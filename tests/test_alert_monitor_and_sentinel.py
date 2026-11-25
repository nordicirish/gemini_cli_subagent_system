"""Unit & Integration Tests for AlertMonitor, Auto-Arming, and Free-Tier Sentinel Fallback."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import alert_monitor  # noqa: E402
import alert_sentinel  # noqa: E402
import web_server  # noqa: E402
from alert_monitor import AlertMonitor, extract_hold_tickers_from_chat, extract_triggers_from_chat  # noqa: E402
from alert_sentinel import AlertSentinel, build_public_technicals_payload  # noqa: E402


def _valid_tick(ticker: str, price: float, *, vwap: float = 10.0, rvol: float = 1.6, rsi: float = 55.0, hod: float = 12.0, fib_next: float = 11.5) -> dict:
    return {
        "ticker": ticker,
        "price": price,
        "price_validity": "VALID",
        "freshness": "CURRENT",
        "vwap": vwap,
        "rvol": rvol,
        "rsi": rsi,
        "session_change_pct": 1.2,
        "trend": "UP",
        "fib_forecast": {
            "mode": "EXTENSION",
            "pA": 9.5,
            "pB": hod,
            "next_resistance": fib_next,
            "daily_peak_target": hod,
            "daily_peak_status": "EXPANDING",
        },
    }


class TestAlertMonitor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = os.path.join(self.tmp.name, "triggers.json")
        self.monitor = AlertMonitor(state_path=self.state_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_threshold_crossing_dynamic_ref_confirm_and_fail_closed_gating(self):
        cross_rule = self.monitor.add_rule(
            {
                "ticker": "UMAC",
                "metric": "price",
                "op": "crosses_above",
                "target_ref": "fib_next",
                "confirm": {"rvol_gte": 1.5, "above_vwap": True},
                "label": "0.50 Reclaim",
                "severity": "warning",
                "one_shot": True,
            },
            source="council",
        )
        # 1. Stale/invalid tick must NOT seed or trigger
        stale = _valid_tick("UMAC", 12.00, vwap=10.5, fib_next=11.00)
        stale["price_validity"] = "INVALID"
        self.assertEqual(self.monitor.evaluate_snapshot([stale], now_ts=100.0), [])

        # 2. First valid observation below target seeds last_value without firing
        self.assertEqual(
            self.monitor.evaluate_snapshot([_valid_tick("UMAC", 10.80, vwap=10.5, fib_next=11.00, rvol=1.8)], now_ts=110.0),
            [],
        )

        # 3. Crosses above 11.00, but rVol confirm fails (0.8 < 1.5) -> must not fire
        self.assertEqual(
            self.monitor.evaluate_snapshot([_valid_tick("UMAC", 11.20, vwap=10.5, fib_next=11.00, rvol=0.8)], now_ts=120.0),
            [],
        )

        # 4. Re-seed below 11.00, then cross above with rVol >= 1.5 and above VWAP -> fires and disarms
        self.assertEqual(
            self.monitor.evaluate_snapshot([_valid_tick("UMAC", 10.90, vwap=10.5, fib_next=11.00, rvol=1.6)], now_ts=130.0),
            [],
        )
        events = self.monitor.evaluate_snapshot(
            [_valid_tick("UMAC", 11.15, vwap=10.5, fib_next=11.00, rvol=1.7)],
            market_status="OPEN",
            now_ts=140.0,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["rule_id"], cross_rule["rule_id"])
        self.assertEqual(events[0]["severity"], "warning")
        # One-shot rule must now be disabled
        dash = self.monitor.get_dashboard_payload()
        self.assertEqual(dash["active_rules_count"], 0)
        self.assertEqual(dash["unacknowledged_count"], 1)
        self.assertTrue(self.monitor.acknowledge_event(events[0]["event_id"]))
        self.assertEqual(self.monitor.get_dashboard_payload()["unacknowledged_count"], 0)

    def test_repeating_cooldown_and_replace_source_preserves_manual_rules(self):
        self.monitor.add_rule(
            {"ticker": "NVDA", "metric": "price", "op": ">=", "target": 120.0, "label": "Manual Hold", "one_shot": True},
            source="manual",
        )
        self.monitor.upsert_rules(
            [{"ticker": "GOOG", "metric": "price", "op": "<=", "target": 270.0, "label": "Old Sentinel", "one_shot": False, "cooldown_sec": 60}],
            source="sentinel",
            replace_source=True,
        )
        # First fire at t=1000
        ev1 = self.monitor.evaluate_snapshot([_valid_tick("GOOG", 269.0)], now_ts=1000.0)
        self.assertEqual(len(ev1), 1)
        # Suppressed within 60s cooldown at t=1030
        self.assertEqual(self.monitor.evaluate_snapshot([_valid_tick("GOOG", 268.0)], now_ts=1030.0), [])
        # Fires again once cooldown expires at t=1065
        ev2 = self.monitor.evaluate_snapshot([_valid_tick("GOOG", 268.0)], now_ts=1065.0)
        self.assertEqual(len(ev2), 1)

        # Replacing sentinel rules keeps manual rule intact and removes old sentinel rule
        self.monitor.upsert_rules(
            [{"ticker": "AMZN", "metric": "rsi", "op": ">=", "target": 70.0, "label": "AMZN RSI"}],
            source="sentinel",
            replace_source=True,
        )
        rules = self.monitor.get_dashboard_payload()["rules"]
        tickers_by_source = {(r["source"], r["ticker"]) for r in rules}
        self.assertEqual(tickers_by_source, {("manual", "NVDA"), ("sentinel", "AMZN")})

    def test_chat_extraction_supports_structured_json_and_prose_fallback(self):
        raw_payload = json.dumps({
            "EXECUTION_PAYLOAD": {
                "timestamp_utc": "2026-11-24T20:00:00Z",
                "alert_triggers": [
                    {"ticker": "UMAC", "metric": "price", "op": "crosses_above", "target": 9.07, "label": "0.50 Reclaim"},
                    {"ticker": "UNKNOWN", "metric": "price", "op": ">=", "target": 50.0},
                ],
            }
        })
        structured = extract_triggers_from_chat("Analysis text", raw_payload_json=raw_payload, known_tickers={"UMAC", "AMZN"})
        self.assertEqual(len(structured), 1)
        self.assertEqual(structured[0]["ticker"], "UMAC")

        prose = """
        ### Actionable Recommendations
        - Watch UMAC breakout above $9.48 on volume.
        - Hold AMZN unless stop below $250.50 hits; keep $1000 cash buffer.
        - Rule L-01 and P/L stop below $8.75 or SL below $8.75 should never arm bogus symbols.
        """
        inferred = extract_triggers_from_chat(prose, known_tickers={"UMAC", "AMZN"})
        self.assertEqual({(t["ticker"], t["op"], t["target"]) for t in inferred}, {("UMAC", ">=", 9.48), ("AMZN", "<=", 250.5)})

        inferred_no_whitelist = extract_triggers_from_chat(
            "Rule L-01 and P/L stop below $8.75 or SL below $8.75",
            known_tickers=None,
        )
        self.assertEqual(inferred_no_whitelist, [])

    def test_dedupes_unacknowledged_alerts_one_per_ticker_and_acknowledges_group(self):
        self.monitor.add_rule(
            {"ticker": "UMAC", "metric": "price", "op": ">=", "target": 9.0, "label": "UMAC Info", "severity": "info", "one_shot": True},
            source="manual",
        )
        self.monitor.add_rule(
            {"ticker": "UMAC", "metric": "price", "op": "<=", "target": 9.5, "label": "UMAC Critical Stop", "severity": "critical", "one_shot": True},
            source="sentinel",
        )
        self.monitor.add_rule(
            {"ticker": "UMAC", "metric": "rsi", "op": "<=", "target": 60.0, "label": "UMAC Warning RSI", "severity": "warning", "one_shot": True},
            source="council",
        )
        self.monitor.add_rule(
            {"ticker": "NVDA", "metric": "price", "op": ">=", "target": 120.0, "label": "NVDA Info", "severity": "info", "one_shot": True},
            source="manual",
        )

        # Fire all four rules; UMAC has 3 conflicting alerts while NVDA has 1.
        self.monitor.evaluate_snapshot(
            [_valid_tick("UMAC", 9.10, rsi=45.0), _valid_tick("NVDA", 121.0)],
            market_status="OPEN",
            now_ts=1000.0,
        )
        # Fire a newer same-severity warning for NVDA to verify newest wins on severity ties.
        self.monitor.add_rule(
            {"ticker": "NVDA", "metric": "price", "op": ">=", "target": 120.5, "label": "NVDA Warning Old", "severity": "warning", "one_shot": True},
            source="manual",
        )
        self.monitor.evaluate_snapshot([_valid_tick("NVDA", 122.0)], market_status="OPEN", now_ts=1010.0)
        self.monitor.add_rule(
            {"ticker": "NVDA", "metric": "price", "op": ">=", "target": 121.5, "label": "NVDA Warning New", "severity": "warning", "one_shot": True},
            source="manual",
        )
        self.monitor.evaluate_snapshot([_valid_tick("NVDA", 123.0)], market_status="OPEN", now_ts=1020.0)

        payload = self.monitor.get_dashboard_payload()
        unacked = payload["unacknowledged_events"]
        self.assertEqual(len(payload["events"]), 6)
        self.assertEqual(payload["unacknowledged_count"], 2)
        self.assertEqual(len(unacked), 2)

        by_ticker = {ev["ticker"]: ev for ev in unacked}
        self.assertEqual(set(by_ticker.keys()), {"UMAC", "NVDA"})
        self.assertEqual(by_ticker["UMAC"]["label"], "UMAC Critical Stop")
        self.assertEqual(by_ticker["UMAC"]["severity"], "critical")
        self.assertEqual(by_ticker["NVDA"]["label"], "NVDA Warning New")

        # Dismissing UMAC's visible alert clears ALL unacknowledged UMAC events (no stale resurface)
        self.assertTrue(self.monitor.acknowledge_event(by_ticker["UMAC"]["event_id"]))
        after_ack = self.monitor.get_dashboard_payload()
        self.assertEqual(after_ack["unacknowledged_count"], 1)
        self.assertEqual([ev["ticker"] for ev in after_ack["unacknowledged_events"]], ["NVDA"])

    def test_enh_16_e_recovered_non_long_gamma_skips_and_auto_clears_stale_event(self):
        # Emit an active ENH_16_E while UMAC is red / LONG_GAMMA
        red_pos = [{
            "ticker": "UMAC",
            "valid_telemetry": True,
            "price": 7.95,
            "vwap": 8.34,
            "session_change_pct": -10.27,
            "dealer_posture": "LONG_GAMMA",
            "tranches": {},
            "triggered_mandates": [{
                "rule_id": "ENH_16_E",
                "action": "LONG_GAMMA_SHIELD_INVALIDATED",
                "severity": "critical",
                "detail": "SEC Rule 201 SSR active / <= -10% drop; LONG_GAMMA shield overridden.",
            }],
        }]
        emitted = self.monitor.record_mandate_alerts(red_pos, market_status="OPEN", now_ts=1795620000.0)
        self.assertEqual(len(emitted), 1)
        self.assertEqual(self.monitor.get_dashboard_payload()["unacknowledged_count"], 1)

        # Once UMAC recovers green above VWAP under SHORT_GAMMA, ENH_16_E is auto-cleared & suppressed
        recovered_pos = [{
            "ticker": "UMAC",
            "valid_telemetry": True,
            "price": 8.98,
            "vwap": 8.34,
            "session_change_pct": 1.30,
            "dealer_posture": "SHORT_GAMMA",
            "tranches": {},
            "triggered_mandates": red_pos[0]["triggered_mandates"],
        }]
        self.assertEqual(
            self.monitor.record_mandate_alerts(recovered_pos, market_status="OPEN", cooldown_sec=900, now_ts=1795622000.0),
            [],
        )
        self.assertEqual(self.monitor.get_dashboard_payload()["unacknowledged_count"], 0)

        # Still fires if dealer_posture is LONG_GAMMA or session_change_pct is negative
        fresh_monitor = AlertMonitor(state_path=os.path.join(self.tmp.name, "triggers_lg.json"))
        lg_green = [dict(recovered_pos[0], dealer_posture="LONG_GAMMA")]
        self.assertEqual(len(fresh_monitor.record_mandate_alerts(lg_green, market_status="OPEN", now_ts=1795622000.0)), 1)
        sg_red_monitor = AlertMonitor(state_path=os.path.join(self.tmp.name, "triggers_sg_red.json"))
        sg_red = [dict(recovered_pos[0], session_change_pct=-2.0)]
        self.assertEqual(len(sg_red_monitor.record_mandate_alerts(sg_red, market_status="OPEN", now_ts=1795622000.0)), 1)

    def test_manual_ack_suppresses_session_mandates_and_rearms_when_cleared(self):
        t0 = 1795620000.0  # 2026-11-25 NY session
        enh_pos = [{
            "ticker": "UMAC",
            "valid_telemetry": True,
            "price": 8.40,
            "vwap": 8.50,
            "session_change_pct": -5.0,
            "dealer_posture": "SHORT_GAMMA",
            "tranches": {},
            "triggered_mandates": [
                {"rule_id": "ENH_16_E", "action": "LONG_GAMMA_SHIELD_INVALIDATED", "severity": "critical", "detail": "SSR active"},
                {"rule_id": "MANDATE_52", "action": "MANDATORY_25PCT_SWEEP_TRIM", "trim_pct": 25, "severity": "critical", "detail": "Sub-VWAP"},
            ],
        }]
        ev1 = self.monitor.record_mandate_alerts(enh_pos, market_status="OPEN", cooldown_sec=900, now_ts=t0)
        self.assertEqual(len(ev1), 2)
        self.assertTrue(self.monitor.acknowledge_event(ev1[0]["event_id"], now_ts=t0 + 5))

        # Past 900s cooldown, both dismissed mandates stay suppressed (even after reload from disk)
        reloaded = AlertMonitor(state_path=self.state_path)
        self.assertEqual(
            reloaded.record_mandate_alerts(enh_pos, market_status="OPEN", cooldown_sec=900, now_ts=t0 + 1200),
            [],
        )

        # Non-sticky MANDATE_52 clears on next tick -> re-arms; ENH_16_E stays session-sticky
        cleared_pos = [dict(enh_pos[0], triggered_mandates=[enh_pos[0]["triggered_mandates"][0]])]
        self.assertEqual(
            reloaded.record_mandate_alerts(cleared_pos, market_status="OPEN", cooldown_sec=900, now_ts=t0 + 1300),
            [],
        )
        ev_rearmed = reloaded.record_mandate_alerts(enh_pos, market_status="OPEN", cooldown_sec=900, now_ts=t0 + 1400)
        self.assertEqual([e["op"] for e in ev_rearmed], ["MANDATE_52"])

        # Next NY session day resets session suppression so ENH_16_E can fire again
        next_day_ts = t0 + 86400.0
        ev_next_day = reloaded.record_mandate_alerts(enh_pos, market_status="OPEN", cooldown_sec=900, now_ts=next_day_ts)
        self.assertEqual(len(ev_next_day), 2)

    def test_extract_hold_tickers_and_reconcile_council_hold(self):
        payload = json.dumps({
            "EXECUTION_PAYLOAD": {
                "state_context": {"directive": "HOLD"},
                "portfolio_snapshot": [{"ticker": "UMAC", "shares": "1860", "wac": "12.6"}],
                "forensic_intelligence": {
                    "trailing_stop_audit": [
                        {"ticker": "UMAC", "recommended_action": "HOLD_MONITOR_STOP", "trim_shares": 0}
                    ]
                },
            }
        })
        self.assertEqual(
            extract_hold_tickers_from_chat("", raw_payload_json=payload, known_tickers={"UMAC", "AMZN"}),
            ["UMAC"],
        )

        prose_hold = (
            "### 🎯 Actionable Recommendations\n"
            "1. **Maintain Defensive HOLD on UMAC (1,860 Shares):** Retain current position intact. Do not initiate any sell orders while above VWAP.\n"
            "- **[HOLD] AMZN** — Keep current allocation.\n"
        )
        self.assertEqual(
            extract_hold_tickers_from_chat(prose_hold, known_tickers={"UMAC", "AMZN"}),
            ["AMZN", "UMAC"],
        )

        # Fail closed when a defensive TRIM is present for UMAC
        conflict_prose = (
            "### 🎯 Actionable Recommendations\n"
            "- **[TRIM] UMAC** — Sell 465 shares at $8.67.\n"
        )
        self.assertEqual(
            extract_hold_tickers_from_chat(conflict_prose, raw_payload_json=payload, known_tickers={"UMAC"}),
            [],
        )

        # Reconcile HOLD acknowledges UMAC ENH_16_E, suppresses it past cooldown, preserves NVDA, allows new MANDATE_54
        t0 = 1795620000.0
        umac_pos = {
            "ticker": "UMAC",
            "valid_telemetry": True,
            "price": 8.40,
            "vwap": 8.50,
            "session_change_pct": -5.0,
            "dealer_posture": "SHORT_GAMMA",
            "tranches": {},
            "triggered_mandates": [
                {"rule_id": "ENH_16_E", "action": "LONG_GAMMA_SHIELD_INVALIDATED", "severity": "critical", "detail": "SSR active"}
            ],
        }
        nvda_pos = {
            "ticker": "NVDA",
            "valid_telemetry": True,
            "price": 120.0,
            "vwap": 124.0,
            "session_change_pct": -4.0,
            "dealer_posture": "SHORT_GAMMA",
            "tranches": {},
            "triggered_mandates": [
                {"rule_id": "MANDATE_52", "action": "MANDATORY_25PCT_SWEEP_TRIM", "trim_pct": 25, "severity": "critical", "detail": "Sub-VWAP"}
            ],
        }
        self.monitor.record_mandate_alerts([umac_pos, nvda_pos], market_status="OPEN", cooldown_sec=900, now_ts=t0)
        res = self.monitor.reconcile_council_hold(["UMAC"], active_mandates_by_ticker={"UMAC": ["ENH_16_E"]}, now_ts=t0 + 10)
        self.assertEqual(res["acknowledged"], 1)
        self.assertEqual(res["suppressed"], ["UMAC|ENH_16_E"])

        dash = self.monitor.get_dashboard_payload()
        self.assertEqual([e["ticker"] for e in dash["unacknowledged_events"]], ["NVDA"])

        # Past cooldown, UMAC ENH_16_E stays suppressed, but a brand-new MANDATE_54 still fires
        umac_worse = dict(
            umac_pos,
            triggered_mandates=[
                umac_pos["triggered_mandates"][0],
                {"rule_id": "MANDATE_54", "action": "MANDATORY_50PCT_TRIM", "trim_pct": 50, "severity": "critical", "detail": "<= -8%"},
            ],
        )
        ev_after = self.monitor.record_mandate_alerts([umac_worse], market_status="OPEN", cooldown_sec=900, now_ts=t0 + 1200)
        self.assertEqual([e["op"] for e in ev_after], ["MANDATE_54"])


class TestAlertSentinelFallbackAndPrivacy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.monitor = AlertMonitor(state_path=os.path.join(self.tmp.name, "triggers.json"))
        self.cfg_path = os.path.join(self.tmp.name, "sentinel_cfg.json")
        self.sample_snapshot = {
            "status": "OPEN",
            "local_storage_state": {
                "mutable_state": {
                    "portfolio_snapshot": [{"ticker": "UMAC", "shares": "250", "wac": "9.12"}],
                    "unallocated_cash_eur": "4200.50",
                }
            },
            "tickers": [_valid_tick("UMAC", 9.02, vwap=8.95, hod=9.48, fib_next=9.07)],
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_privacy_sanitization_strips_portfolio_and_cash(self):
        public_payload = build_public_technicals_payload(self.sample_snapshot)
        serialized = json.dumps(public_payload)
        self.assertIn("UMAC", serialized)
        for forbidden in ("portfolio_snapshot", "shares", "wac", "4200.50", "unallocated_cash"):
            self.assertNotIn(forbidden, serialized)

    def test_model_fallback_on_404_and_429_with_cooldowns(self):
        calls = []

        def fake_gemini(model_name: str, prompt: str, api_key: str) -> str:
            calls.append(model_name)
            if model_name == "gemini-2.5-flash-lite":
                raise RuntimeError("404 NOT_FOUND: model gemini-2.5-flash-lite is not found")
            if model_name == "gemini-2.5-flash":
                raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")
            return json.dumps({
                "triggers": [
                    {"ticker": "UMAC", "metric": "price", "op": "crosses_above", "target": 9.48, "label": "HOD Break"}
                ]
            })

        sentinel = AlertSentinel(
            alert_monitor=self.monitor,
            config_path=self.cfg_path,
            gemini_invoker=fake_gemini,
        )
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-dummy-key"}):
            res = sentinel.run_once(snapshot=self.sample_snapshot, force=True, now_ts=5000.0)

        self.assertEqual(res["status"], "OK")
        self.assertEqual(res["provider_used"], "gemini_free")
        self.assertEqual(res["model_used"], "gemini-2.0-flash-lite")
        self.assertEqual(res["fallback_count"], 2)
        self.assertEqual(calls, ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.0-flash-lite"])

        # Subsequent non-forced call respects 404/429 cooldowns and starts directly on gemini-2.0-flash-lite
        calls.clear()
        moved_snap = json.loads(json.dumps(self.sample_snapshot))
        moved_snap["tickers"][0]["price"] = 9.20  # >= 0.5% move triggers event gate
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-dummy-key"}):
            res2 = sentinel.run_once(snapshot=moved_snap, force=False, now_ts=5060.0)
        self.assertEqual(res2["status"], "OK")
        self.assertEqual(calls, ["gemini-2.0-flash-lite"])

        # Same price on next check skips LLM call via event gate
        calls.clear()
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-dummy-key"}):
            res3 = sentinel.run_once(snapshot=moved_snap, force=False, now_ts=5120.0)
        self.assertEqual(res3["status"], "SKIPPED")
        self.assertEqual(res3["skipped_reason"], "no_structural_change")
        self.assertEqual(calls, [])

    def test_auto_provider_falls_back_to_antigravity_and_never_leaks_key(self):
        ag_calls = []

        def fail_gemini(model_name: str, prompt: str, api_key: str) -> str:
            raise RuntimeError("401 UNAUTHENTICATED: api key not valid")

        def ok_ag(model_name: str, prompt: str) -> str:
            ag_calls.append(model_name)
            return json.dumps({
                "triggers": [{"ticker": "UMAC", "metric": "price", "op": ">=", "target": 9.10, "label": "AG Trigger"}]
            })

        sentinel = AlertSentinel(
            alert_monitor=self.monitor,
            config_path=self.cfg_path,
            gemini_invoker=fail_gemini,
            antigravity_invoker=ok_ag,
        )
        sentinel.update_config({"provider": "auto"}, persist=False)
        with patch.dict(os.environ, {"GEMINI_API_KEY": "super-secret-value-123"}):
            res = sentinel.run_once(snapshot=self.sample_snapshot, force=True, now_ts=6000.0)
            status = sentinel.get_status()

        self.assertEqual(res["status"], "OK")
        self.assertEqual(res["provider_used"], "antigravity")
        self.assertEqual(ag_calls, ["gemini-3.8-flash-low"])
        self.assertTrue(status["api_key_configured"])
        self.assertNotIn("super-secret-value-123", json.dumps(status))


class TestAlertApiRoutes(unittest.TestCase):
    def test_alert_and_sentinel_http_routes(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            iso_monitor = AlertMonitor(state_path=os.path.join(tmp.name, "triggers.json"))
            iso_sentinel = AlertSentinel(
                alert_monitor=iso_monitor,
                config_path=os.path.join(tmp.name, "cfg.json"),
                gemini_invoker=lambda m, p, k: json.dumps({"triggers": []}),
            )
            client = TestClient(web_server.app)
            with patch.object(web_server, "ALERT_MONITOR", iso_monitor), patch.object(web_server, "ALERT_SENTINEL", iso_sentinel):
                create_res = client.post("/api/alerts", json={"ticker": "NVDA", "metric": "price", "op": ">=", "target": 125.0})
                self.assertEqual(create_res.status_code, 200)
                rule_id = create_res.json()["rule"]["rule_id"]

                tog_res = client.post(f"/api/alerts/{rule_id}/toggle")
                self.assertEqual(tog_res.status_code, 200)
                self.assertFalse(tog_res.json()["rule"]["enabled"])

                cfg_res = client.post("/api/alert_sentinel/config", json={"enabled": True, "provider": "auto", "interval_sec": 300})
                self.assertEqual(cfg_res.status_code, 200)
                self.assertTrue(cfg_res.json()["sentinel"]["enabled"])
                self.assertEqual(cfg_res.json()["sentinel"]["interval_sec"], 300)

                del_res = client.delete(f"/api/alerts/{rule_id}")
                self.assertEqual(del_res.status_code, 200)
                self.assertEqual(del_res.json()["alerts"]["total_rules_count"], 0)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
