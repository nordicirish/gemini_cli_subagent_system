"""Unit & Integration Tests for Phase 4 Python Analytics Offload."""

from datetime import datetime
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import web_server  # noqa: E402
from analytics_engine import AnalyticsEngine  # noqa: E402
from macro_calendar import get_macro_calendar_status  # noqa: E402

NY_TZ = ZoneInfo("America/New_York")


class TestMacroCalendarShield(unittest.TestCase):
    def test_shield_windows_and_custom_calendar_override(self):
        # 18h before 2026-12-09 14:00 FOMC Rate Decision -> shield active, 0.50 stop & 0.50 sizing
        dt_18h = datetime(2026, 12, 8, 20, 0, tzinfo=NY_TZ)
        cal_18 = get_macro_calendar_status(now_ny=dt_18h, calendar_path="__nonexistent__.json")
        self.assertTrue(cal_18["shield_active"])
        self.assertEqual(cal_18["stop_tighten_factor"], 0.5)
        self.assertEqual(cal_18["sizing_dampener"], 0.5)
        self.assertIn("ENH_259", cal_18["active_rules"])
        self.assertEqual(cal_18["next_event"]["name"], "FOMC Rate Decision")

        # 36h before 2026-12-04 08:30 NFP -> shield active, 0.50 stop & 0.75 sizing
        dt_36h = datetime(2026, 12, 2, 20, 30, tzinfo=NY_TZ)
        cal_36 = get_macro_calendar_status(now_ny=dt_36h, calendar_path="__nonexistent__.json")
        self.assertTrue(cal_36["shield_active"])
        self.assertEqual(cal_36["stop_tighten_factor"], 0.5)
        self.assertEqual(cal_36["sizing_dampener"], 0.75)

        # >48h before any default event (e.g. 2026-11-28 vs 2026-12-04 NFP) -> clear
        dt_far = datetime(2026, 11, 28, 12, 0, tzinfo=NY_TZ)
        cal_far = get_macro_calendar_status(now_ny=dt_far, calendar_path="__nonexistent__.json")
        self.assertFalse(cal_far["shield_active"])
        self.assertEqual(cal_far["stop_tighten_factor"], 1.0)
        self.assertEqual(cal_far["sizing_dampener"], 1.0)

        # Custom calendar override via cache/macro_calendar.json
        tmp = tempfile.TemporaryDirectory()
        try:
            custom_path = os.path.join(tmp.name, "macro_calendar.json")
            with open(custom_path, "w", encoding="utf-8") as f:
                json.dump([{"name": "Custom CPI", "date": "2026-11-30", "time_et": "08:30", "tier": "TIER_1"}], f)
            cal_custom = get_macro_calendar_status(now_ny=datetime(2026, 11, 29, 10, 0, tzinfo=NY_TZ), calendar_path=custom_path)
            self.assertTrue(cal_custom["shield_active"])
            self.assertEqual(cal_custom["next_event"]["name"], "Custom CPI")
        finally:
            tmp.cleanup()

    def test_post_release_grace_window_and_expiration(self):
        # 15m after 2026-11-25 08:30 ET Core PCE -> still inside 30-min grace window
        cal_live = get_macro_calendar_status(
            now_ny=datetime(2026, 11, 25, 8, 45, tzinfo=NY_TZ),
            calendar_path="__nonexistent__.json",
        )
        self.assertTrue(cal_live["shield_active"])
        self.assertEqual(cal_live["next_event"]["name"], "Core PCE")
        self.assertTrue(cal_live["next_event"]["in_release_window"])

        # 35m after release (09:05 ET) and 2h+ after (10:39 ET) -> expired, advances to NFP
        for check_dt in (
            datetime(2026, 11, 25, 9, 5, tzinfo=NY_TZ),
            datetime(2026, 11, 25, 10, 39, tzinfo=NY_TZ),
        ):
            cal_expired = get_macro_calendar_status(now_ny=check_dt, calendar_path="__nonexistent__.json")
            self.assertFalse(cal_expired["shield_active"])
            self.assertEqual(cal_expired["stop_tighten_factor"], 1.0)
            self.assertEqual(cal_expired["sizing_dampener"], 1.0)
            self.assertEqual(cal_expired["next_event"]["name"], "Non-Farm Payrolls")


class TestPhase4ExecutionAndRouter(unittest.TestCase):
    def _sample_state(self):
        return {
            "status": "OPEN",
            "tickers": [
                {"ticker": "^VIX", "price": 17.5, "price_validity": "VALID", "freshness": "CURRENT"},
                {"ticker": "SPY", "price": 602.0, "ma_50": 580.0, "price_validity": "VALID", "freshness": "CURRENT", "dealer_posture": "LONG_GAMMA", "net_gex_total": 0.03},
                {
                    "ticker": "UMAC",
                    "price": 10.00,
                    "regular_close": 11.00,
                    "session_change_pct": -9.09,
                    "vwap": 10.20,
                    "rsi": 72.5,
                    "price_validity": "VALID",
                    "freshness": "CURRENT",
                    "fib_forecast": {"t1_100": 11.00, "t2_1618": 11.62, "t3_2618": 12.62},
                },
            ],
            "local_storage_state": {
                "mutable_state": {
                    "unallocated_cash_eur": 1250.50,
                    "portfolio_snapshot": [
                        {"ticker": "UMAC", "shares": "100", "wac": "9.00"},
                        {"ticker": "ZERO", "shares": "0", "wac": "5.00"},
                    ],
                    "watched_tickers": [],
                }
            },
        }

    def test_execution_plan_trailing_stops_fib_ladder_and_macro_tightening(self):
        engine = AnalyticsEngine()
        state = self._sample_state()
        fx = {"available": True, "eurusd_rate": 1.10}

        # Non-shield window -> 2.0% stop & MANDATE_54 50% sweep trim triggered (-9.09% drop)
        far_dt = datetime(2026, 11, 28, 12, 0, tzinfo=NY_TZ)
        plan_clear = json.loads(engine.format_execution_engine_response(state, ssot=state["local_storage_state"], fx_info=fx, ny_dt=far_dt))
        self.assertEqual(plan_clear["source"], "python_deterministic")
        self.assertIn("1250.50 EUR Unallocated", plan_clear["cash_reconciliation"]["proof"])
        pos = plan_clear["positions"][0]
        self.assertEqual(pos["trailing_stop"]["effective_stop_pct"], 2.0)
        # 10.00 < anchor 10.20 => base_ref=10.00, 10.00 * 0.98 = 9.80
        self.assertEqual(pos["trailing_stop"]["effective_stop_price"], 9.80)
        # ENH_96 T1 front-run: 11.00 * 0.9975 = 10.97
        self.assertEqual(pos["fib_limit_ladder"][0]["tier"], "T1")
        self.assertEqual(pos["fib_limit_ladder"][0]["limit_usd"], 10.97)
        self.assertEqual(pos["fib_limit_ladder"][0]["sell_shares"], 25)
        self.assertEqual(plan_clear["order_directives"][0]["rule_id"], "MANDATE_54")

        # Inside 48h macro shield window (2026-12-08 vs 2026-12-09 FOMC) -> 50% tightened stop (1.0%)
        shield_dt = datetime(2026, 12, 8, 20, 0, tzinfo=NY_TZ)
        plan_shield = json.loads(engine.format_execution_engine_response(state, ssot=state["local_storage_state"], fx_info=fx, ny_dt=shield_dt))
        self.assertTrue(plan_shield["macro_calendar"]["shield_active"])
        self.assertEqual(plan_shield["positions"][0]["trailing_stop"]["effective_stop_pct"], 1.0)
        self.assertEqual(plan_shield["positions"][0]["trailing_stop"]["effective_stop_price"], 9.90)

    def test_state_validation_router_enh99_pruning_and_security_isolation(self):
        engine = AnalyticsEngine()
        state = self._sample_state()
        raw = engine.format_state_validation_router_response(
            state,
            ssot=state["local_storage_state"],
            fx_info={"available": True, "eurusd_rate": 1.10},
            ny_dt=datetime(2026, 12, 8, 20, 0, tzinfo=NY_TZ),
        )
        res = json.loads(raw)
        self.assertEqual(res["source"], "python_deterministic")
        self.assertEqual(res["Consensus_Status"], "VERIFIED")
        ep = res["EXECUTION_PAYLOAD"]
        syms = [p["ticker"] for p in ep["portfolio_snapshot"]]
        self.assertEqual(syms, ["UMAC"])  # ENH_99 zero-share ticker pruned
        self.assertEqual(ep["unallocated_cash_eur"], 1250.5)
        self.assertEqual(len(ep["forensic_intelligence"]["trailing_stop_audit"]), 1)

        # Verify web_server wiring & security boundaries
        self.assertTrue(getattr(web_server.execution, "_is_deterministic_python", False))
        self.assertTrue(getattr(web_server.execution, "_is_read_only_deliberative", False))
        self.assertTrue(getattr(web_server.state_validation_router, "_is_deterministic_python", False))
        self.assertFalse(getattr(web_server.state_validation_router, "_is_read_only_deliberative", True))
        self.assertNotIn("ask_state_validation_router", web_server.council_members)
        self.assertIn("ask_execution_engine", web_server.council_members)

        with (
            patch.object(web_server, "ANALYTICS_ENGINE", engine),
            patch("fetch_stocks.get_global_state_snapshot", return_value=state),
            patch.object(web_server.framework, "generate_response_with_fallback", side_effect=AssertionError("LLM must not be called")),
        ):
            council_out = json.loads(web_server.ask_council(json.dumps({"execution_engine": "Generate tickets"})))
            exec_data = json.loads(council_out["execution_engine"])
            self.assertEqual(exec_data["source"], "python_deterministic")
            router_out = json.loads(web_server.state_validation_router("Validate SSoT"))
            self.assertEqual(router_out["Consensus_Status"], "VERIFIED")

    def test_phase4_http_routes(self):
        client = TestClient(web_server.app)
        with (
            patch("fetch_stocks.get_global_state_snapshot", return_value=self._sample_state()),
            patch.object(web_server.ANALYTICS_ENGINE, "build_execution_plan", return_value={"source": "python_deterministic", "positions": []}),
        ):
            r_exec = client.get("/api/analytics/execution")
        r_cal = client.get("/api/analytics/calendar")
        self.assertEqual(r_exec.status_code, 200)
        self.assertEqual(r_exec.json()["source"], "python_deterministic")
        self.assertEqual(r_cal.status_code, 200)
        self.assertIn("shield_active", r_cal.json())


if __name__ == "__main__":
    unittest.main()
