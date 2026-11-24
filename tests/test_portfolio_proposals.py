"""
Tests for owner-approved portfolio proposals: model-originated EXECUTION_PAYLOADs are staged
with a before/after diff, merged per ticker, and written only on owner approval.
"""

import copy
import os
import sys
import unittest
from decimal import Decimal
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from portfolio_proposals import (
    ProposalConflictError,
    ProposalNotFoundError,
    ProposalStateError,
    ProposalStore,
)
from persistence import PersistenceValidationError
from tool_registry import execution_role_context


class FakeService:
    """Minimal persistence double: replace-semantics portfolio like the real adapters."""

    def __init__(self, positions=None, eur="1000.00", usd="500.00"):
        self.positions = {
            t: {"shares": Decimal(s), "wac": Decimal(w)} for t, (s, w) in (positions or {}).items()
        }
        self.eur = Decimal(eur)
        self.usd = Decimal(usd)
        self.writes = []

    def get_portfolio_basket(self):
        return {
            "portfolio": [
                {"ticker": t, "shares": v["shares"], "wac": v["wac"]} for t, v in self.positions.items()
            ],
            "unallocated_cash_eur": self.eur,
            "unallocated_cash_usd": self.usd,
        }

    def update_ssot_payload(self, payload, caller_role=None):
        self.writes.append((copy.deepcopy(payload), caller_role))
        ms = payload.get("mutable_state", {})
        if "portfolio_snapshot" in ms:
            self.positions = {
                p["ticker"]: {"shares": p["shares"], "wac": p["wac"]}
                for p in ms["portfolio_snapshot"]
                if p["shares"] > 0
            }
        if "unallocated_cash_eur" in ms:
            self.eur = ms["unallocated_cash_eur"]
        if "unallocated_cash_usd" in ms:
            self.usd = ms["unallocated_cash_usd"]
        return {"status": "success"}


def _payload(portfolio=None, **cash):
    ep = {}
    if portfolio is not None:
        ep["portfolio_snapshot"] = portfolio
    ep.update(cash)
    return {"EXECUTION_PAYLOAD": ep}


class TestProposalSemantics(unittest.TestCase):
    def setUp(self):
        self.service = FakeService(positions={"NVDA": ("10", "100.00"), "AMD": ("5", "150.00")})
        self.store = ProposalStore(service_factory=lambda: self.service)

    def test_submit_never_writes_financial_changes(self):
        out = self.store.submit(_payload([{"ticker": "NVDA", "shares": 12, "wac": "105.00"}]))
        self.assertEqual(out["status"], "pending")
        self.assertEqual(self.service.writes, [])
        self.assertEqual(self.service.positions["NVDA"]["shares"], Decimal("10"))

    def test_omitted_ticker_is_kept_on_apply(self):
        out = self.store.submit(_payload([{"ticker": "NVDA", "shares": 12, "wac": "105.00"}]))
        self.store.apply(out["proposal"]["proposal_id"])
        self.assertIn("AMD", self.service.positions)
        self.assertEqual(self.service.positions["AMD"]["shares"], Decimal("5"))
        self.assertEqual(self.service.positions["NVDA"]["shares"], Decimal("12"))

    def test_empty_snapshot_deletes_nothing(self):
        out = self.store.submit(_payload([]))
        self.assertEqual(out["status"], "no_changes")
        self.assertEqual(set(self.service.positions), {"NVDA", "AMD"})

    def test_explicit_zero_shares_closes_position(self):
        out = self.store.submit(_payload([{"ticker": "amd", "shares": 0}]))
        changes = out["proposal"]["positions"]
        self.assertEqual(changes, [{
            "ticker": "AMD", "kind": "close",
            "shares_before": "5", "shares_after": "0",
            "wac_before": "150.00", "wac_after": "150.00",
        }])
        self.store.apply(out["proposal"]["proposal_id"])
        self.assertNotIn("AMD", self.service.positions)
        self.assertIn("NVDA", self.service.positions)

    def test_missing_fields_keep_current_values(self):
        out = self.store.submit(_payload([{"ticker": "NVDA", "shares": 20}]))
        change = out["proposal"]["positions"][0]
        self.assertEqual((change["kind"], change["wac_after"]), ("modify", "100.00"))

    def test_open_requires_positive_wac(self):
        with self.assertRaises(PersistenceValidationError):
            self.store.submit(_payload([{"ticker": "TSLA", "shares": 3}]))

    def test_duplicate_ticker_rejected(self):
        with self.assertRaises(PersistenceValidationError):
            self.store.submit(_payload([
                {"ticker": "NVDA", "shares": 1, "wac": "1.00"},
                {"ticker": "nvda", "shares": 2, "wac": "1.00"},
            ]))

    @patch("portfolio_proposals.get_eurusd", return_value=None)
    def test_eur_only_change_keeps_usd_explicitly_without_live_rate(self, _no_rate):
        out = self.store.submit(_payload(unallocated_cash_eur=Decimal("2000.00")))
        self.assertEqual(out["proposal"]["cash"], [{"currency": "EUR", "before": "1000.00", "after": "2000.00", "note": None}])
        self.store.apply(out["proposal"]["proposal_id"])
        written, _ = self.service.writes[-1]
        self.assertEqual(written["mutable_state"]["unallocated_cash_usd"], Decimal("500.00"))
        self.assertEqual(self.service.usd, Decimal("500.00"))

    def test_identical_snapshot_is_no_change(self):
        out = self.store.submit(_payload([{"ticker": "NVDA", "shares": 10, "wac": "100.00"}]))
        self.assertEqual(out["status"], "no_changes")
        self.assertEqual(self.store.list(), [])

    def test_apply_rejects_stale_state(self):
        out = self.store.submit(_payload([{"ticker": "NVDA", "shares": 12, "wac": "105.00"}]))
        self.service.positions["NVDA"]["shares"] = Decimal("11")  # owner edited the basket meanwhile
        with self.assertRaises(ProposalConflictError):
            self.store.apply(out["proposal"]["proposal_id"])
        self.assertEqual(self.service.writes, [])

    def test_apply_uses_apply_time_state_for_untouched_positions(self):
        out = self.store.submit(_payload([{"ticker": "NVDA", "shares": 12, "wac": "105.00"}]))
        self.service.positions["MSFT"] = {"shares": Decimal("2"), "wac": Decimal("400.00")}
        self.store.apply(out["proposal"]["proposal_id"])
        self.assertIn("MSFT", self.service.positions)

    def test_agent_role_cannot_approve(self):
        out = self.store.submit(_payload([{"ticker": "NVDA", "shares": 12, "wac": "105.00"}]))
        with execution_role_context("Terminal Orchestrator"):
            with self.assertRaises(PermissionError):
                self.store.apply(out["proposal"]["proposal_id"])
        self.assertEqual(self.service.writes, [])

    def test_new_proposal_supersedes_pending(self):
        first = self.store.submit(_payload([{"ticker": "NVDA", "shares": 12, "wac": "105.00"}]))
        second = self.store.submit(_payload([{"ticker": "NVDA", "shares": 14, "wac": "105.00"}]))
        pending_ids = [p["proposal_id"] for p in self.store.list()]
        self.assertEqual(pending_ids, [second["proposal"]["proposal_id"]])
        with self.assertRaises(ProposalStateError):
            self.store.apply(first["proposal"]["proposal_id"])

    def test_discard_writes_nothing(self):
        out = self.store.submit(_payload([{"ticker": "NVDA", "shares": 12, "wac": "105.00"}]))
        self.store.discard(out["proposal"]["proposal_id"])
        self.assertEqual(self.service.writes, [])
        with self.assertRaises(ProposalStateError):
            self.store.apply(out["proposal"]["proposal_id"])

    def test_unknown_proposal(self):
        with self.assertRaises(ProposalNotFoundError):
            self.store.apply("prop-missing")

    def test_decision_metadata_preserved_on_apply(self):
        out = self.store.submit(_payload([
            {"ticker": "NVDA", "shares": 12, "wac": "105.00", "action": "ADD"},
        ]))
        self.store.apply(out["proposal"]["proposal_id"])
        written, role = self.service.writes[-1]
        entry = written["mutable_state"]["portfolio_snapshot"][0]
        self.assertEqual((entry["ticker"], entry["action"]), ("NVDA", "ADD"))
        self.assertEqual(role, "Owner")
        self.assertNotIn("portfolio_snapshot", written["EXECUTION_PAYLOAD"])


class TestUpdateSsotToolStagesForAgents(unittest.TestCase):
    def setUp(self):
        import tools
        self.tools = tools
        self.service = FakeService(positions={"NVDA": ("10", "100.00")})
        self.store_patch = patch.object(
            tools.PROPOSAL_STORE, "_service_factory", lambda: self.service
        )
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)

    def test_agent_call_is_staged_not_written(self):
        with execution_role_context("Terminal Orchestrator"):
            msg = self.tools.update_ssot(_payload([{"ticker": "NVDA", "shares": 0}]))
        self.assertIn("NOT been applied", msg)
        self.assertEqual(self.service.writes, [])
        self.assertIn("NVDA", self.service.positions)
        pending = self.tools.PROPOSAL_STORE.list()
        self.assertTrue(any(p["source"] == "Terminal Orchestrator" for p in pending))


class TestProposalEndpoints(unittest.TestCase):
    def setUp(self):
        import web_server
        from fastapi.testclient import TestClient
        self.web_server = web_server
        self.service = FakeService(positions={"NVDA": ("10", "100.00")})
        p = patch.object(web_server.PROPOSAL_STORE, "_service_factory", lambda: self.service)
        p.start()
        self.addCleanup(p.stop)
        r = patch("web_server._refresh_live_portfolio_state")
        r.start()
        self.addCleanup(r.stop)
        self.client = TestClient(web_server.app)

    def test_apply_and_discard_flow(self):
        store = self.web_server.PROPOSAL_STORE
        pid = store.submit(_payload([{"ticker": "NVDA", "shares": 15, "wac": "100.00"}]))["proposal"]["proposal_id"]
        listed = self.client.get("/api/proposals").json()["proposals"]
        self.assertIn(pid, [p["proposal_id"] for p in listed])

        res = self.client.post(f"/api/proposals/{pid}/apply")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.service.positions["NVDA"]["shares"], Decimal("15"))

        again = self.client.post(f"/api/proposals/{pid}/apply")
        self.assertEqual(again.status_code, 409)

        self.assertEqual(self.client.post("/api/proposals/prop-nope/apply").status_code, 404)

        pid2 = store.submit(_payload([{"ticker": "NVDA", "shares": 1}]))["proposal"]["proposal_id"]
        self.assertEqual(self.client.post(f"/api/proposals/{pid2}/discard").status_code, 200)
        self.assertEqual(self.service.positions["NVDA"]["shares"], Decimal("15"))

    def test_stale_apply_returns_409(self):
        store = self.web_server.PROPOSAL_STORE
        pid = store.submit(_payload([{"ticker": "NVDA", "shares": 15, "wac": "100.00"}]))["proposal"]["proposal_id"]
        self.service.positions["NVDA"]["shares"] = Decimal("9")
        res = self.client.post(f"/api/proposals/{pid}/apply")
        self.assertEqual(res.status_code, 409)
        self.assertIn("changed since", res.json()["detail"])


class TestChatEndpointStagesPayload(unittest.TestCase):
    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_payload_becomes_pending_proposal(self, mock_dispatch, _charts):
        import fetch_stocks
        import web_server
        service = FakeService(positions={"NVDA": ("10", "100.00"), "AMD": ("5", "150.00")})
        orig = fetch_stocks.get_global_state_snapshot()
        fetch_stocks.publish_global_state_snapshot({"financial_state_valid": True, "local_storage_state": {}, "tickers": []})
        try:
            with patch.object(web_server.PROPOSAL_STORE, "_service_factory", lambda: service):
                mock_dispatch.return_value = {
                    "status": "completed",
                    "response": 'Trim AMD.\n```json\n{"EXECUTION_PAYLOAD": {"portfolio_snapshot": [{"ticker": "AMD", "shares": 2}]}}\n```',
                    "turn_count": 2,
                }
                res = web_server.chat_endpoint(web_server.ChatRequest(message="Review AMD", turn_id="turn-prop-1"))
        finally:
            fetch_stocks.publish_global_state_snapshot(orig)

        self.assertEqual(res["status"], "success")
        self.assertIn("Pending Your Approval", res["response"])
        self.assertIn("Nothing has been written yet", res["response"])
        self.assertNotIn("synchronized successfully", res["response"])
        self.assertEqual(service.writes, [])
        self.assertEqual(len(res["proposals"]), 1)
        self.assertEqual(res["proposals"][0]["positions"][0]["ticker"], "AMD")
        self.assertIsNone(web_server.PROPOSAL_STORE.current_turn_id)


if __name__ == "__main__":
    unittest.main()
