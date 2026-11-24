"""Owner-approved self-improvement proposals (lessons & rules.md amendments) and decision-log backfill."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "python"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

import improvement_proposals as ip  # noqa: E402
from tool_registry import execution_role_context  # noqa: E402


class FakeLessonService:
    def __init__(self, lessons=None):
        self.lessons = list(lessons or [])
        self.saves = 0

    def get_trade_lessons(self):
        return list(self.lessons)

    def save_trade_lessons(self, items):
        self.saves += 1
        self.lessons = list(items)


class TestImprovementStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rules_path = os.path.join(self.tmp.name, "rules.md")
        with open(self.rules_path, "w", encoding="utf-8") as f:
            f.write("# Rules\n\n## Mandate Registry\n- MANDATE_52: trailing stop.\n")
        self.svc = FakeLessonService([{"lesson": "Existing"}])
        self.written = []

        def writer(content):
            self.written.append(content)
            with open(self.rules_path, "w", encoding="utf-8") as f:
                f.write(content)

        self.store = ip.ImprovementProposalStore(
            store_path=os.path.join(self.tmp.name, "improvements.json"),
            rules_path=self.rules_path,
            service_factory=lambda: self.svc,
            rules_writer=writer,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_staging_dedupes_pending_and_persists(self):
        a = self.store.stage_lesson("Tighten stops pre-PCE", "umac", source="Terminal Orchestrator")
        b = self.store.stage_lesson("  tighten   stops pre-pce ", "UMAC")
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(a["ticker"], "UMAC")
        reloaded = ip.ImprovementProposalStore(store_path=self.store.store_path)
        self.assertEqual(len(reloaded.list()), 1)

    def test_apply_lesson_uses_edited_text_and_marks_applied(self):
        p = self.store.stage_lesson("Draft lesson", "UMAC")
        applied = self.store.apply(p["id"], edited_text="Final lesson text")
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(self.svc.lessons[-1]["lesson"], "Final lesson text")
        self.assertEqual(self.store.list(), [])
        with self.assertRaises(ip.ImprovementStateError):
            self.store.apply(p["id"])

    def test_apply_rule_appends_numbered_amendments_section(self):
        p1 = self.store.stage_rule("MANDATE_52", "Tighten stop to 1% before Tier-1 data.", action="MODIFY", justification="UMAC -7%")
        r1 = self.store.apply(p1["id"])
        p2 = self.store.stage_rule("NEW_RULE", "Log every per-ticker decision.")
        r2 = self.store.apply(p2["id"])
        self.assertEqual((r1["amendment_id"], r2["amendment_id"]), ("A-1", "A-2"))
        with open(self.rules_path, encoding="utf-8") as f:
            rules = f.read()
        self.assertTrue(rules.startswith("# Rules\n\n## Mandate Registry\n- MANDATE_52: trailing stop.\n"))
        self.assertEqual(rules.count(ip.AMENDMENTS_HEADING), 1)
        self.assertIn("### [AMENDMENT A-1] MANDATE_52 (MODIFY)", rules)
        self.assertIn("Supersedes any conflicting text in MANDATE_52", rules)
        self.assertIn("- **Justification:** UMAC -7%", rules)
        self.assertLess(rules.index("A-1]"), rules.index("A-2]"))

    def test_amendment_inserted_before_following_section(self):
        text = f"# R\n\n{ip.AMENDMENTS_HEADING}\nintro\n\n### [AMENDMENT A-4] X (ADD) — owner-approved 2026-01-01\n- **Rule:** old\n\n## Later Section\nbody\n"
        rec = {"id": "imp-1", "rule_id": "ENH_1", "action": "ADD", "source": "Review Engine"}
        out = ip.build_rules_with_amendment(text, rec, "new rule", "2026-11-25T00:00:00Z")
        self.assertIn("A-5] ENH_1", out)
        self.assertLess(out.index("A-5]"), out.index("## Later Section"))
        self.assertTrue(out.rstrip().endswith("body"))

    def test_agents_cannot_apply_and_discard_writes_nothing(self):
        p = self.store.stage_rule("ENH_9", "Rule text")
        with execution_role_context("Review Engine"):
            with self.assertRaises(PermissionError):
                self.store.apply(p["id"])
        self.assertEqual(self.store.discard(p["id"])["status"], "discarded")
        self.assertEqual(self.written, [])
        with self.assertRaises(ip.ImprovementNotFoundError):
            self.store.discard("imp-missing")

    def test_real_rules_path_goes_through_owner_approval_guard(self):
        # No rules_writer: must grant a one-time approval bound to the exact content, then call update_rules.
        store = ip.ImprovementProposalStore(store_path=os.path.join(self.tmp.name, "i2.json"), rules_path=self.rules_path)
        p = store.stage_rule("ENH_2", "Guarded rule")
        calls = {}
        import tools
        from tool_registry import GLOBAL_OWNER_APPROVAL

        def fake_update_rules(content):
            ok, reason = GLOBAL_OWNER_APPROVAL.verify_or_prompt("update_rules", self.rules_path, content)
            calls["approved"] = ok
            return "ok"

        with patch.object(tools, "update_rules", side_effect=fake_update_rules):
            store.apply(p["id"])
        self.assertTrue(calls["approved"])


class TestRuleProposalParsing(unittest.TestCase):
    def test_parses_id_action_text_and_justification(self):
        line = "- **[RULE_PROPOSAL] MANDATE_52** — MODIFY: Tighten trailing stop to 1.0% before Tier-1 data. Justification: UMAC rows 2026-11-24."
        p = ip.parse_rule_proposal_line(line)
        self.assertEqual(p["rule_id"], "MANDATE_52")
        self.assertEqual(p["action"], "MODIFY")
        self.assertEqual(p["text"], "Tighten trailing stop to 1.0% before Tier-1 data")
        self.assertEqual(p["justification"], "UMAC rows 2026-11-24.")

    def test_defaults_and_extraction(self):
        p = ip.parse_rule_proposal_line("* [RULE_PROPOSAL] Always log per-ticker decisions.")
        self.assertEqual((p["rule_id"], p["action"]), ("NEW_RULE", "ADD"))
        reply = "### 🎯 Actionable Recommendations\n- [LESSON] X — y\n- [RULE_PROPOSAL] ENH_259 — ADD: Rule one.\n- [RULE_PROPOSAL] ENH_260 — ADD: Rule two."
        self.assertEqual([x["rule_id"] for x in ip.extract_rule_proposals(reply)], ["ENH_259", "ENH_260"])
        self.assertIsNone(ip.parse_rule_proposal_line("[RULE_PROPOSAL]"))

    def test_lesson_items_from_payload_shapes(self):
        self.assertEqual(ip.lesson_items_from_payload("plain"), [{"text": "plain", "ticker": "PORTFOLIO"}])
        items = ip.lesson_items_from_payload({"new_trade_lessons": [{"rule": "L-1: a", "ticker": "UMAC"}, {"x": 1}]})
        self.assertEqual(items, [{"text": "L-1: a", "ticker": "UMAC"}])


class TestImprovementEndpoints(unittest.TestCase):
    def setUp(self):
        import web_server
        self.ws = web_server
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = FakeLessonService()
        self.store = ip.ImprovementProposalStore(
            store_path=os.path.join(self.tmp.name, "imp.json"),
            rules_path=os.path.join(self.tmp.name, "rules.md"),
            service_factory=lambda: self.svc,
            rules_writer=lambda c: None,
        )
        self.patch = patch.object(web_server, "IMPROVEMENT_STORE", self.store)
        self.patch.start()
        self.client = TestClient(web_server.app)

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_list_apply_discard_flow(self):
        p = self.store.stage_lesson("Lesson A")
        q = self.store.stage_lesson("Lesson B")
        self.assertEqual(len(self.client.get("/api/improvements").json()["proposals"]), 2)
        r = self.client.post(f"/api/improvements/{p['id']}/apply", json={"text": "Lesson A edited"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.svc.lessons[-1]["lesson"], "Lesson A edited")
        self.assertEqual(self.client.post(f"/api/improvements/{p['id']}/apply").status_code, 409)
        self.assertEqual(self.client.post(f"/api/improvements/{q['id']}/discard").status_code, 200)
        self.assertEqual(self.client.post("/api/improvements/imp-nope/apply").status_code, 404)
        self.assertEqual(self.client.post(f"/api/improvements/{q['id']}/apply", content=b"{bad").status_code, 422)

    def test_review_endpoint_stages_rule_proposals_for_its_turn(self):
        class Svc:
            def get_decision_log(self):
                return []

            def get_trade_lessons(self):
                return []

        reply = "### 🎯 Actionable Recommendations\n- [RULE_PROPOSAL] ENH_259 — ADD: Halve size before PCE. Justification: row 3."
        with patch.object(self.ws, "get_persistence_service", return_value=Svc()), \
                patch.object(self.ws, "review_log_engine", return_value=reply):
            body = self.client.post("/api/review", json={"message": "Review Log", "turn_id": "turn-imp-1"}).json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(len(body["improvement_proposals"]), 1)
        staged = body["improvement_proposals"][0]
        self.assertEqual((staged["kind"], staged["rule_id"], staged["turn_id"]), ("rule", "ENH_259", "turn-imp-1"))
        self.assertIsNone(self.store.current_turn_id)


class TestDecisionLogBackfill(unittest.TestCase):
    def test_build_backfill_collapses_per_day_and_is_idempotent(self):
        import backfill_decision_log as bf

        def payload(directive, trade_state, price, sa):
            return json.dumps({
                "EXECUTION_PAYLOAD": {
                    "state_context": {"directive": directive},
                    "runtime_flags": {"trade_state": trade_state},
                    "risk_metrics": {"agreement_score_sa": sa},
                    "forensic_intelligence": {"trailing_stop_audit": [{"ticker": "UMAC", "current_price": price}]},
                },
                "mutable_state": {"portfolio_snapshot": [{"ticker": "UMAC", "shares": "1860"}]},
            })

        rows = [
            {"session_id": "s1", "ticker": "SESSION", "timestamp": "2026-11-24T15:00:00+00:00", "transcript_text": payload("HOLD", "NO_TRADE", "8.90", "0.93")},
            {"session_id": "s2", "ticker": "SESSION", "timestamp": "2026-11-24T18:00:00+00:00", "transcript_text": payload("REJECT", "NO_TRADE", "8.82", "0.92")},
            {"session_id": "s3", "ticker": "SESSION", "timestamp": "2026-11-24T16:00:00+00:00", "transcript_text": payload("EXECUTE", "TRIM", "8.74", "0.91")},
            {"session_id": "s4", "ticker": "SESSION", "timestamp": "2026-11-25T15:00:00+00:00",
             "transcript_text": "### 🎯 Actionable Recommendations\n* **[TRIM] UMAC** — sell 465 at limit $8.67"},
            {"session_id": "s5", "ticker": "UMAC", "timestamp": "2026-09-22T15:00:00+00:00", "transcript_text": "already scorable"},
        ]
        plan = bf.build_backfill(rows, {"UMAC"})
        got = [(d["day"], d["action"], d["ticker"], d["price"], d["agreement_score_sa"], d["sources"]) for d in plan]
        self.assertEqual(got, [
            ("2026-11-24", "HOLD", "UMAC", 8.82, 0.92, ["s1", "s2"]),
            ("2026-11-24", "TRIM", "UMAC", 8.74, 0.91, ["s3"]),
            ("2026-11-25", "TRIM", "UMAC", None, None, ["s4"]),
        ])
        done = rows + [{"session_id": "backfill-s2", "ticker": "UMAC", "timestamp": "2026-11-24T18:00:00+00:00",
                        "votes": {"source": bf.BACKFILL_SOURCE, "backfilled_from": "s2", "collapsed_sources": ["s1", "s2"]}}]
        self.assertEqual([d["sources"] for d in bf.build_backfill(done, {"UMAC"})], [["s3"], ["s4"]])


if __name__ == "__main__":
    unittest.main()
