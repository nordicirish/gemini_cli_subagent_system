"""Unit & Integration Tests for Read-Only Council Quantitative Investigations."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import quant_investigations as qi  # noqa: E402
import tools  # noqa: E402
import web_server  # noqa: E402
from tool_registry import GLOBAL_TOOL_REGISTRY  # noqa: E402

NY_TZ = ZoneInfo("America/New_York")


def _build_5m_rth_and_vix(num_days: int = 18):
    rows = []
    idx = []
    vix_rows = []
    dates = pd.bdate_range("2026-10-01", periods=num_days, tz=NY_TZ)
    close_prev = 10.00
    for day_i, d in enumerate(dates):
        # Gap up +1.2% and spike to +3.0% by 10:00 ET, then fade to close ~ +0.5% over prior
        o = round(close_prev * 1.012, 4)
        peak_p = round(o * 1.022, 4)
        end_p = round(o * 0.992, 4)
        bars = [
            (9, 30, o, o * 1.006, o * 0.996, o * 1.004, 40000),
            (9, 45, o * 1.004, peak_p * 0.996, o, peak_p * 0.995, 50000),
            (10, 0, peak_p * 0.995, peak_p, o * 1.005, peak_p * 0.998, 65000),
            (10, 15, peak_p * 0.998, peak_p * 0.999, o * 0.998, o * 1.002, 35000),
            (10, 30, o * 1.002, o * 1.005, o * 0.995, o * 1.000, 25000),
            (11, 0, o * 1.000, o * 1.003, o * 0.993, o * 0.997, 20000),
            (12, 30, o * 0.997, o * 1.001, o * 0.990, o * 0.994, 15000),
            (14, 0, o * 0.994, o * 0.998, o * 0.988, o * 0.993, 18000),
            (15, 30, o * 0.993, o * 0.996, o * 0.989, end_p, 22000),
            (15, 55, end_p, end_p * 1.002, end_p * 0.997, end_p, 30000),
        ]
        for hh, mm, bo, bh, bl, bc, bv in bars:
            idx.append(d.replace(hour=hh, minute=mm, second=0))
            rows.append({"Open": bo, "High": bh, "Low": bl, "Close": bc, "Volume": bv})
        close_prev = end_p
        vix_rows.append({"Open": 18.0, "High": 19.0, "Low": 17.5, "Close": 18.5 if day_i % 2 == 0 else 23.0, "Volume": 0})
    df_5m = pd.DataFrame(rows, index=pd.DatetimeIndex(idx))
    df_vix = pd.DataFrame(vix_rows, index=pd.DatetimeIndex(dates))
    return df_5m, df_vix


def _build_daily_bars(num_days: int = 80):
    dates = pd.bdate_range("2026-07-01", periods=num_days, tz=NY_TZ)
    closes = [10.0 * (1.002 ** i) * (1.02 if i % 5 == 0 else 0.995) for i in range(num_days)]
    df = pd.DataFrame(
        {
            "Open": closes,
            "High": [c * 1.02 for c in closes],
            "Low": [c * 0.98 for c in closes],
            "Close": closes,
            "Volume": [100000] * num_days,
        },
        index=dates,
    )
    vix = pd.DataFrame({"Open": 19.0, "High": 22.0, "Low": 18.0, "Close": [17.0 if i % 2 == 0 else 24.0 for i in range(num_days)]}, index=dates)
    return df, vix


class TestQuantInvestigations(unittest.TestCase):
    def test_overnight_to_morning_peak_metrics_friction_and_regimes(self):
        df_5m, df_vix = _build_5m_rth_and_vix(18)
        tmp = tempfile.TemporaryDirectory()
        try:
            cache_file = os.path.join(tmp.name, "inv.json")
            res = qi.run_investigation(
                study_type="overnight_to_morning_peak",
                ticker="UMAC",
                friction_pct=0.85,
                entry_time_et="15:55",
                exit_cutoff_et="10:30",
                bootstrap_paths=500,
                cache_path=cache_file,
                bars_5m=df_5m,
                vix_daily=df_vix,
            )
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["ticker"], "UMAC")
            self.assertEqual(res["compute_target_used"], "local")
            self.assertEqual(res["window"]["overnight_trades"], 17)
            self.assertIn(res["verdict"], ("SOUND_EDGE", "MARGINAL_AFTER_FRICTION", "UNSOUND_NEGATIVE_EXPECTANCY"))
            self.assertIn("empirical_p50_front_run_or_cutoff", res["strategies"])
            self.assertIn("exit_1000_et", res["strategies"])
            self.assertGreater(
                res["oracle_morning_hod_upper_bound_non_tradable"]["mean_net_pct"],
                res["strategies"]["exit_1030_et"]["mean_net_pct"],
            )
            # Check exact friction deduction: gross - 0.85 == net
            ex10 = res["strategies"]["exit_1000_et"]
            self.assertAlmostEqual(ex10["mean_gross_pct"] - 0.85, ex10["mean_net_pct"], places=2)
            self.assertGreater(res["regime_breakdown"]["vix_lt_20"]["trades"], 0)
            self.assertGreater(res["regime_breakdown"]["vix_gte_20"]["trades"], 0)
            self.assertTrue(os.path.isfile(cache_file))
        finally:
            tmp.cleanup()

    def test_gap_vwap_and_regime_studies(self):
        df_5m, _ = _build_5m_rth_and_vix(14)
        df_daily, vix_daily = _build_daily_bars(80)

        gap_res = qi.run_investigation("gap_behavior", "UMAC", bars_5m=df_5m, cache_path=None)
        self.assertEqual(gap_res["status"], "ok")
        self.assertIn("gap_ups_long_open_to_1030", gap_res)

        vwap_res = qi.run_investigation("vwap_reversion", "UMAC", bars_5m=df_5m, cache_path=None)
        self.assertEqual(vwap_res["status"], "ok")
        self.assertIn("vwap_reclaim_long_to_close", vwap_res)

        reg_res = qi.run_investigation("regime_forward_returns", "UMAC", daily_bars=df_daily, vix_daily=vix_daily, cache_path=None)
        self.assertEqual(reg_res["status"], "ok")
        self.assertGreater(reg_res["horizons_by_regime"]["all"]["5d"]["trades"], 10)

    def test_validation_rejects_bad_inputs(self):
        with self.assertRaises(ValueError):
            qi.run_investigation("arbitrary_exec", "UMAC", cache_path=None)
        with self.assertRaises(ValueError):
            qi.run_investigation("overnight_to_morning_peak", "BAD SYMBOL!;", cache_path=None)
        with self.assertRaises(ValueError):
            qi.run_investigation("overnight_to_morning_peak", "UMAC", compute_target="ssh", cache_path=None)

    def test_colab_offload_zero_secret_inputs_and_offline_fallback(self):
        df_5m, df_vix = _build_5m_rth_and_vix(10)
        captured = {}

        def fake_colab_ok(script, inputs):
            captured["script"] = script
            captured["inputs"] = inputs
            return {"status": "ok", "study_type": inputs["study_type"], "ticker": inputs["ticker"], "verdict": "SOUND_EDGE"}

        res_ok = qi.run_investigation(
            "overnight_to_morning_peak",
            "UMAC",
            compute_target="colab",
            cache_path=None,
            colab_executor=fake_colab_ok,
        )
        self.assertEqual(res_ok["compute_target_used"], "colab")
        self.assertEqual(set(captured["inputs"].keys()), {
            "study_type", "ticker", "lookback_days", "friction_pct",
            "entry_time_et", "exit_cutoff_et", "stop_loss_pct", "bootstrap_paths",
        })
        serialized = json.dumps(captured)
        for forbidden in ("portfolio_snapshot", "wac", "unallocated_cash", "AIza"):
            self.assertNotIn(forbidden, serialized)

        # Fallback to local when Colab raises
        def fake_colab_fail(_script, _inputs):
            raise RuntimeError("Colab transport is offline")

        res_fb = qi.run_investigation(
            "overnight_to_morning_peak",
            "UMAC",
            compute_target="colab",
            cache_path=None,
            bars_5m=df_5m,
            vix_daily=df_vix,
            colab_executor=fake_colab_fail,
        )
        self.assertEqual(res_fb["status"], "ok")
        self.assertEqual(res_fb["compute_target_used"], "local")
        self.assertIn("offline", res_fb["fallback_reason"].lower())

    def test_tool_registry_rbac_and_http_endpoint(self):
        meta = GLOBAL_TOOL_REGISTRY.get("run_quant_investigation")
        self.assertIsNotNone(meta)
        self.assertEqual(meta.classification, "READ")
        self.assertFalse(meta.affects_financial_state)
        self.assertEqual(meta.required_permission, "read_market")

        # Wired to Orchestrator terminal_tools and executable via tools.run_quant_investigation
        self.assertIn(tools.run_quant_investigation, web_server._runtime.terminal_tools)
        with patch("quant_investigations.run_investigation", return_value={"status": "ok", "ticker": "UMAC", "study_type": "overnight_to_morning_peak"}):
            tool_out = json.loads(tools.run_quant_investigation("overnight_to_morning_peak", "UMAC"))
        self.assertEqual(tool_out["status"], "ok")

        client = TestClient(web_server.app)
        with patch("quant_investigations.run_investigation", return_value={"status": "ok", "ticker": "UMAC", "study_type": "overnight_to_morning_peak", "verdict": "MARGINAL_AFTER_FRICTION"}):
            ok_res = client.post("/api/analytics/investigate", json={"study_type": "overnight_to_morning_peak", "ticker": "UMAC"})
        self.assertEqual(ok_res.status_code, 200)
        self.assertEqual(ok_res.json()["verdict"], "MARGINAL_AFTER_FRICTION")

        bad_res = client.post("/api/analytics/investigate", json={"study_type": "bad_study", "ticker": "UMAC"})
        self.assertEqual(bad_res.status_code, 422)


if __name__ == "__main__":
    unittest.main()
