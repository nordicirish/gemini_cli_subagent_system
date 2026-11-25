"""Unit & Integration Tests for 60d Peak Predictor & Sentinel 1m/Peak Modes."""

from datetime import datetime
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import web_server  # noqa: E402
from alert_monitor import AlertMonitor  # noqa: E402
from alert_sentinel import AlertSentinel  # noqa: E402
from peak_predictor import compute_historical_peak_profile, predict_daily_peak  # noqa: E402

NY_TZ = ZoneInfo("America/New_York")


def _build_synthetic_5m_rth(num_days: int = 20) -> pd.DataFrame:
    """Builds 20 trading days where price opens at 10.00, peaks at 10:00 ET (+5%), and fades to 10.12."""
    rows = []
    idx = []
    dates = pd.bdate_range("2026-10-01", periods=num_days, tz=NY_TZ)
    for d in dates:
        bar_times = [
            (9, 30, 10.00, 10.15, 9.95, 10.10, 50000),
            (9, 35, 10.10, 10.22, 10.05, 10.20, 45000),
            (9, 45, 10.20, 10.35, 10.15, 10.30, 40000),
            (10, 0, 10.30, 10.50, 10.25, 10.42, 60000),  # Peak at 10:00 ET (+5.0% over open)
            (10, 15, 10.42, 10.44, 10.20, 10.25, 30000),
            (10, 30, 10.25, 10.28, 10.15, 10.18, 20000),
            (11, 0, 10.18, 10.20, 10.10, 10.15, 15000),
            (12, 0, 10.15, 10.18, 10.08, 10.14, 10000),
            (13, 30, 10.14, 10.16, 10.05, 10.10, 12000),
            (15, 55, 10.10, 10.15, 10.06, 10.12, 25000),
        ]
        for hh, mm, o, h, lo, c, v in bar_times:
            idx.append(d.replace(hour=hh, minute=mm, second=0))
            rows.append({"Open": o, "High": h, "Low": lo, "Close": c, "Volume": v})
    return pd.DataFrame(rows, index=pd.DatetimeIndex(idx))


class TestPeakPredictor(unittest.TestCase):
    def test_historical_profile_and_exhaustion_states(self):
        df = _build_synthetic_5m_rth(20)
        prof = compute_historical_peak_profile("UMAC", bars_df=df, cache_path=None)
        self.assertEqual(prof["status"], "ok")
        self.assertEqual(prof["sessions_analyzed"], 20)
        self.assertEqual(prof["median_peak_time_et"], "10:00")
        self.assertEqual(prof["pct_peaking_before_1030"], 100.0)
        self.assertAlmostEqual(prof["upside_from_open_p50_pct"], 5.0, places=2)
        self.assertLess(prof["avg_fade_from_peak_pct"], -3.0)

        # Hot 10:05 ET sell zone near p50 ($10.50) + VWAP +3.5% + RSI 74 + rVol decel
        sell_row = {
            "ticker": "UMAC",
            "open": 10.00,
            "price": 10.48,
            "vwap": 10.12,
            "distance_from_vwap": 3.56,
            "rsi": 74.0,
            "rvol": 0.85,
            "dealer_posture": "LONG_GAMMA",
            "fib_forecast": {"pB": 10.50, "t1_100": 10.55, "daily_peak_target": 10.55},
        }
        pred_hot = predict_daily_peak(
            "UMAC",
            live_row=sell_row,
            tracker={"rvol_decelerating_3x": True},
            bars_df=df,
            ny_dt=datetime(2026, 11, 25, 10, 5, tzinfo=NY_TZ),
            cache_path=None,
        )
        self.assertEqual(pred_hot["exhaustion_state"], "SELL_ZONE")
        self.assertGreaterEqual(pred_hot["exhaustion_score"], 70)
        self.assertEqual(pred_hot["p50_peak_usd"], 10.50)
        self.assertEqual(pred_hot["front_run_sell_limit_usd"], round(10.50 * 0.9975, 2))

        # Afternoon pullback well below open & low RSI -> BUILDING
        cool_row = {"ticker": "UMAC", "open": 10.00, "price": 9.85, "vwap": 10.10, "rsi": 42.0, "rvol": 1.2}
        pred_cool = predict_daily_peak(
            "UMAC",
            live_row=cool_row,
            bars_df=df,
            ny_dt=datetime(2026, 11, 25, 14, 15, tzinfo=NY_TZ),
            cache_path=None,
        )
        self.assertEqual(pred_cool["exhaustion_state"], "BUILDING")
        self.assertLess(pred_cool["exhaustion_score"], 45)


class TestSentinel1mAndPeakMode(unittest.TestCase):
    def test_sentinel_1m_interval_peak_mode_and_privacy(self):
        tmp = tempfile.TemporaryDirectory()
        captured_prompts = []
        try:
            monitor = AlertMonitor(state_path=os.path.join(tmp.name, "triggers.json"))

            def fake_gemini(model_name, prompt, api_key):
                captured_prompts.append(prompt)
                return json.dumps({
                    "triggers": [
                        {"ticker": "UMAC", "metric": "price", "op": ">=", "target": 10.47, "severity": "warning", "label": "Predicted Daily Peak Sell", "one_shot": True}
                    ]
                })

            sentinel = AlertSentinel(
                alert_monitor=monitor,
                config_path=os.path.join(tmp.name, "sentinel.json"),
                env_path=os.path.join(tmp.name, ".env"),
                gemini_invoker=fake_gemini,
            )
            cfg = sentinel.update_config({"enabled": True, "interval_sec": 60, "peak_predictor_enabled": True, "provider": "gemini_free"})
            self.assertEqual(cfg["interval_sec"], 60)
            self.assertTrue(cfg["peak_predictor_enabled"])

            snap = {
                "status": "OPEN",
                "tickers": [{"ticker": "UMAC", "price": 10.40, "open": 10.00, "vwap": 10.10, "rsi": 72.0, "rvol": 1.4, "price_validity": "VALID", "freshness": "CURRENT"}],
                "local_storage_state": {"mutable_state": {"unallocated_cash_eur": 9999.0, "portfolio_snapshot": [{"ticker": "UMAC", "shares": "1860", "wac": "9.12"}]}},
            }
            fake_peak = {
                "status": "ok",
                "front_run_sell_limit_usd": 10.47,
                "exhaustion_score": 78,
                "exhaustion_state": "SELL_ZONE",
                "profile": {"median_peak_time_et": "10:00"},
            }
            with (
                patch("alert_sentinel._load_gemini_api_key", return_value="dummy-key"),
                patch("peak_predictor.predict_daily_peak", return_value=fake_peak),
            ):
                outcome = sentinel.run_once(snapshot=snap, force=True, now_ts=1795600000.0)

            self.assertEqual(outcome["status"], "OK")
            self.assertEqual(len(captured_prompts), 1)
            self.assertIn("peak_zone", captured_prompts[0])
            self.assertIn("10.47", captured_prompts[0])
            self.assertNotIn("9999", captured_prompts[0])
            self.assertNotIn("1860", captured_prompts[0])
        finally:
            tmp.cleanup()

    def test_peak_http_endpoint(self):
        client = TestClient(web_server.app)
        with patch.object(web_server, "predict_daily_peak", return_value={"status": "ok", "ticker": "UMAC", "exhaustion_score": 75, "exhaustion_state": "SELL_ZONE"}):
            res = client.get("/api/analytics/peak/UMAC")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["ticker"], "UMAC")
        self.assertEqual(res.json()["exhaustion_state"], "SELL_ZONE")


if __name__ == "__main__":
    unittest.main()
