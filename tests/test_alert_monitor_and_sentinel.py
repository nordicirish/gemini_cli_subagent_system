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
from alert_monitor import AlertMonitor, extract_triggers_from_chat  # noqa: E402
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
