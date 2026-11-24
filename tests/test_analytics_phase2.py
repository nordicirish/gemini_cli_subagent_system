"""Unit & Integration Tests for Phase 2 Python Analytics Offload."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import web_server  # noqa: E402
from alert_monitor import AlertMonitor  # noqa: E402
from analytics_engine import AnalyticsEngine  # noqa: E402
from decision_backtester import backtest_decision_log  # noqa: E402
from sec_edgar import fetch_recent_sec_filings  # noqa: E402


class TestDeterministicSubEngines(unittest.TestCase):
    def test_deterministic_engines_skip_llm_and_work_in_ask_council(self):
        self.assertTrue(getattr(web_server.regime_engine, "_is_deterministic_python", False))
        self.assertTrue(getattr(web_server.gex_engine, "_is_deterministic_python", False))
        self.assertTrue(getattr(web_server.technical_validator, "_is_deterministic_python", False))
        self.assertTrue(getattr(web_server.regime_engine, "_is_read_only_deliberative", False))
        self.assertTrue(getattr(web_server.gex_engine, "_is_read_only_deliberative", False))
        self.assertTrue(getattr(web_server.technical_validator, "_is_read_only_deliberative", False))

        sample_state = {
            "status": "OPEN",
            "tickers": [
                {"ticker": "^VIX", "price": 23.0, "price_validity": "VALID", "freshness": "CURRENT"},
                {"ticker": "SPY", "price": 718.0, "ma_50": 730.0, "price_validity": "VALID", "freshness": "CURRENT", "dealer_posture": "SHORT_GAMMA", "net_gex_total": -0.02},
                {"ticker": "UMAC", "price": 8.80, "regular_close": 9.30, "vwap": 9.00, "distance_from_vwap": -2.22, "price_validity": "VALID", "freshness": "CURRENT", "dealer_posture": "SHORT_GAMMA", "net_gex_total": -0.005, "gamma_flip_price": 9.20},
            ],
            "local_storage_state": {
                "mutable_state": {
                    "portfolio_snapshot": [{"ticker": "UMAC", "shares": "1860", "wac": "9.12"}],
                    "watched_tickers": [],
                }
            },
        }
        iso_engine = AnalyticsEngine()
        with (
            patch.object(web_server, "ANALYTICS_ENGINE", iso_engine),
            patch("fetch_stocks.get_global_state_snapshot", return_value=sample_state),
            patch.object(web_server.framework, "generate_response_with_fallback", side_effect=AssertionError("LLM must not be called")),
        ):
            raw = web_server.ask_council(json.dumps({
                "regime_engine": "Evaluate regime",
                "gex_engine": "Evaluate GEX",
                "technical_validator": "Validate UMAC",
            }))

        parsed = json.loads(raw)
        reg = json.loads(parsed["regime_engine"])
        gex = json.loads(parsed["gex_engine"])
        tech = json.loads(parsed["technical_validator"])
        self.assertEqual(reg["source"], "python_deterministic")
        self.assertEqual(reg["Volatility_State"], "BEAR_VOLATILE")
        self.assertTrue(gex["spy_short_gamma"])
        self.assertEqual(tech["positions"][0]["tranches"]["25pct"]["sell_shares"], 465)


class TestMandateAutoAlerting(unittest.TestCase):
    def test_mandate_alerts_emit_with_exact_sizing_and_respect_cooldown(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            monitor = AlertMonitor(state_path=os.path.join(tmp.name, "triggers.json"))
            positions = [
                {
                    "ticker": "UMAC",
                    "price": 8.70,
                    "valid_telemetry": True,
                    "tranches": {"25pct": {"sell_shares": 465, "sweep_limit_usd": 8.66, "front_run_limit_usd": 8.68}},
                    "triggered_mandates": [
                        {"rule_id": "MANDATE_52", "action": "MANDATORY_25PCT_SWEEP_TRIM", "trim_pct": 25, "severity": "critical", "detail": "SPY SHORT_GAMMA & -2.5% VWAP"},
                    ],
                },
                {
                    "ticker": "STALE",
                    "price": 5.0,
                    "valid_telemetry": False,
                    "tranches": {},
                    "triggered_mandates": [{"rule_id": "MANDATE_54", "trim_pct": 50, "severity": "critical", "detail": "Should be ignored"}],
                },
            ]
            ev1 = monitor.record_mandate_alerts(positions, market_status="OPEN", cooldown_sec=900, now_ts=1000.0)
            self.assertEqual(len(ev1), 1)
            self.assertEqual(ev1[0]["source"], "analytics")
            self.assertIn("sell 465 sh (25%) limit ~$8.66", ev1[0]["message"])

            # Suppressed during 900s cooldown
            self.assertEqual(monitor.record_mandate_alerts(positions, market_status="OPEN", cooldown_sec=900, now_ts=1200.0), [])
            # Emits again once cooldown expires
            ev2 = monitor.record_mandate_alerts(positions, market_status="OPEN", cooldown_sec=900, now_ts=1950.0)
            self.assertEqual(len(ev2), 1)
        finally:
            tmp.cleanup()


class TestBacktesterAndSecEdgar(unittest.TestCase):
    def test_decision_backtester_computes_forward_returns_and_hit_rate(self):
        idx = pd.to_datetime(["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09"], utc=True)
        hist = pd.DataFrame(
            {
                "Open": [10.0, 10.5, 11.0, 10.8, 11.5, 12.0],
                "High": [10.2, 10.7, 11.2, 11.1, 11.8, 12.2],
                "Low": [9.8, 10.1, 10.6, 10.4, 11.0, 11.5],
                "Close": [10.0, 10.5, 11.0, 10.8, 11.5, 12.0],
            },
            index=idx,
        )
        log = [
            {"date": "2026-11-02", "ticker": "UMAC", "action": "BUY", "price": 10.0},
            {"date": "2026-11-02", "ticker": "UMAC", "action": "TRIM", "price": 10.0},
        ]
        res = backtest_decision_log(log, history_provider=lambda s: hist if s == "UMAC" else None)
        self.assertEqual(res["evaluated_count"], 2)
        self.assertAlmostEqual(res["hit_rate_pct"], 50.0, places=1)
        self.assertAlmostEqual(res["entries"][0]["ret_1d_pct"], 5.0, places=2)
        self.assertAlmostEqual(res["entries"][0]["ret_5d_pct"], 20.0, places=2)
        self.assertAlmostEqual(res["avg_mae_pct"], 1.0, places=2)
        self.assertIn("BUY", res["by_action"])
        self.assertIn("TRIM", res["by_action"])

        nested_log = [
            {"date": "2026-11-02", "ticker": "SESSION", "lesson": "Narrative summary only"},
            {
                "timestamp": "2026-11-02T15:00:00",
                "decisions": [
                    {
                        "ticker": "UMAC",
                        "price_at_eval": 10.0,
                        "trade_state": "BUY",
                        "scrutiny_audit": {"agreement_score_sa": 0.91},
                    }
                ],
            },
        ]
        nested_res = backtest_decision_log(nested_log, history_provider=lambda s: hist if s == "UMAC" else None)
        self.assertEqual(nested_res["total_logged"], 1)
        self.assertEqual(nested_res["evaluated_count"], 1)
        self.assertAlmostEqual(nested_res["entries"][0]["agreement_score_sa"], 0.91, places=2)

    def test_add_trade_lesson_endpoint_persists_owner_approved_lesson(self):
        saved = {}

        class FakeSvc:
            def get_trade_lessons(self):
                return [{"date": "2026-11-01", "ticker": "UMAC", "lesson": "Existing lesson"}]

            def save_trade_lessons(self, items):
                saved["items"] = items

        client = TestClient(web_server.app)
        with patch.object(web_server, "get_persistence_service", return_value=FakeSvc()):
            res = client.post("/api/trade_lessons/add", json={"ticker": "UMAC", "lesson": "Tighten stop pre-PCE"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(saved["items"]), 2)
        self.assertEqual(saved["items"][1]["ticker"], "UMAC")
        self.assertEqual(saved["items"][1]["lesson"], "Tighten stop pre-PCE")

    def test_sec_edgar_dilution_warning_caching_and_urls(self):
        tmp = tempfile.TemporaryDirectory()
        calls = []

        def fake_http(url: str):
            calls.append(url)
            if "company_tickers.json" in url:
                return {"0": {"cik_str": 1712762, "ticker": "UMAC", "title": "Unusual Machines"}}
            return {
                "name": "Unusual Machines, Inc.",
                "filings": {
                    "recent": {
                        "form": ["424B5", "8-K", "10-Q"],
                        "filingDate": ["2026-11-23", "2026-11-10", "2026-11-01"],
                        "accessionNumber": ["0001712762-26-000101", "0001712762-26-000090", "0001712762-26-000050"],
                        "primaryDocument": ["prospectus.htm", "form8k.htm", "q3.htm"],
                        "primaryDocDescription": ["Prospectus Supplement", "Current Report", "Quarterly Report"],
                    }
                },
            }

        try:
            cache_path = os.path.join(tmp.name, "sec_cache.json")
            now_ts = 1795500000.0  # 2026-11-24 UTC
            res = fetch_recent_sec_filings("umac", cache_path=cache_path, http_get=fake_http, now_ts=now_ts)
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["cik"], "0001712762")
            self.assertTrue(res["dilution_warning_72h"])
            self.assertEqual(
                res["filings"][0]["url"],
                "https://www.sec.gov/Archives/edgar/data/1712762/000171276226000101/prospectus.htm",
            )
            self.assertEqual(len(calls), 2)

            # Second call within 6h must hit local cache (0 extra HTTP calls)
            res_cached = fetch_recent_sec_filings("UMAC", cache_path=cache_path, http_get=fake_http, now_ts=now_ts + 60)
            self.assertTrue(res_cached["cached"])
            self.assertEqual(len(calls), 2)
        finally:
            tmp.cleanup()

    def test_phase2_analytics_http_routes(self):
        client = TestClient(web_server.app)
        with (
            patch.object(web_server, "backtest_decision_log", return_value={"evaluated_count": 3, "hit_rate_pct": 66.7, "entries": []}),
            patch.object(web_server, "fetch_recent_sec_filings", return_value={"status": "ok", "ticker": "UMAC", "dilution_warning_72h": False, "filings": []}),
        ):
            r_bt = client.get("/api/analytics/backtest")
            r_sec = client.get("/api/analytics/sec/UMAC")
        self.assertEqual(r_bt.status_code, 200)
        self.assertEqual(r_bt.json()["evaluated_count"], 3)
        self.assertEqual(r_sec.status_code, 200)
        self.assertEqual(r_sec.json()["ticker"], "UMAC")


if __name__ == "__main__":
    unittest.main()
