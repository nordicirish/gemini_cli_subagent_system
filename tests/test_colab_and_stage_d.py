"""
Unit & Integration Tests for Offline Colab Calibration & Stage D Refactoring:
- colab_calibration export (zero-secret), fail-closed validation, atomic import & status
- peak_predictor integration with Colab priors (fallback + enrichment)
- FastAPI /api/calibration/export, /api/calibration/import (200/422), /api/calibration/status
- Extracted chat_payload_parser and merton_optimizer modules (+ memoization)
- Retired scratch/master_trading_knowledge.md generation no-op
"""

import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import chat_payload_parser
import colab_calibration
import fetch_stocks
import merton_optimizer
import peak_predictor
import web_server


class TestColabCalibrationAndStageD(unittest.TestCase):
    def _sample_valid_calibration(self):
        return {
            "schema_version": "1.0",
            "generated_at_utc": "2026-11-26T17:00:00Z",
            "source": "google_colab",
            "notes": "250d peak & CVaR run",
            "peak_priors": {
                "NVDA": {
                    "sessions_analyzed": 120,
                    "p25_minute_from_open": 15.0,
                    "p50_minute_from_open": 35.0,
                    "p75_minute_from_open": 75.0,
                    "peak_window_start_et": "09:45",
                    "median_peak_time_et": "10:05",
                    "peak_window_end_et": "10:45",
                    "pct_peaking_before_1030": 68.0,
                    "pct_peaking_before_1100": 78.0,
                    "upside_from_open_p50_pct": 2.5,
                    "upside_from_open_p75_pct": 4.0,
                    "upside_from_open_p90_pct": 6.2,
                    "avg_fade_from_peak_pct": -1.9,
                }
            },
            "merton_stress": {
                "monte_carlo_cvar_95_pct": -11.4,
                "recommended_gamma": 3.5,
                "stress_correlation_regime": "ELEVATED",
                "ticker_weights": {"NVDA": 0.65, "UMAC": 0.35},
            },
            "backtest_summary": {
                "sessions_tested": 250,
                "sharpe_ratio": 1.42,
                "max_drawdown_pct": -13.8,
                "hit_rate_pct": 59.5,
            },
        }

    def test_export_colab_bundle_zero_secrets_and_structure(self):
        mock_svc = MagicMock()
        mock_svc.get_portfolio_basket.return_value = {
            "portfolio": [{"ticker": "NVDA", "shares": 10, "wac": 100.0}],
            "unallocated_cash_eur": 9999.99,
            "unallocated_cash_usd": 10999.99,
        }
        mock_svc.get_watchlist.return_value = ["PLTR", "NVDA"]
        mock_svc.get_decision_log.return_value = [
            {"ticker": "NVDA", "action_proposed": "HOLD", "decision_outcome": "HOLD", "timestamp": "2026-11-26T15:00:00Z"}
        ]

        with tempfile.TemporaryDirectory() as td:
            daily_path = Path(td) / "daily_cache.json"
            daily_path.write_text(json.dumps({"history": {"AMD": {"columns": ["Close"], "data": [[150.0]]}}}), encoding="utf-8")
            bundle = colab_calibration.export_colab_bundle(
                service=mock_svc,
                daily_cache_path=str(daily_path),
                peak_cache_path=str(Path(td) / "missing_peaks.json"),
            )

        self.assertEqual(bundle["schema_version"], "1.0")
        self.assertEqual(bundle["symbols"], ["NVDA", "PLTR", "AMD"])
        self.assertEqual(bundle["portfolio_weights_pct"], {"NVDA": 100.0})
        raw_text = json.dumps(bundle)
        self.assertNotIn("9999.99", raw_text)
        self.assertNotIn("FINNHUB_API_KEY", raw_text)
        self.assertIn("calibration_template", bundle)

    def test_validate_colab_calibration_fail_closed(self):
        with self.assertRaises(ValueError):
            colab_calibration.validate_colab_calibration({"schema_version": "2.0"})

        bad_unknown = self._sample_valid_calibration()
        bad_unknown["rogue_field"] = 123
        with self.assertRaises(ValueError):
            colab_calibration.validate_colab_calibration(bad_unknown)

        bad_nan = self._sample_valid_calibration()
        bad_nan["merton_stress"]["monte_carlo_cvar_95_pct"] = float("nan")
        with self.assertRaises(ValueError):
            colab_calibration.validate_colab_calibration(bad_nan)

        bad_bool = self._sample_valid_calibration()
        bad_bool["peak_priors"]["NVDA"]["sessions_analyzed"] = True
        with self.assertRaises(ValueError):
            colab_calibration.validate_colab_calibration(bad_bool)

        bad_order = self._sample_valid_calibration()
        bad_order["peak_priors"]["NVDA"]["upside_from_open_p75_pct"] = 1.0
        with self.assertRaises(ValueError):
            colab_calibration.validate_colab_calibration(bad_order)

    def test_import_and_peak_predictor_fallback_and_enrichment(self):
        with tempfile.TemporaryDirectory() as td:
            cal_path = os.path.join(td, "colab_calibration.json")
            summary = colab_calibration.import_colab_calibration(self._sample_valid_calibration(), calibration_path=cal_path)
            self.assertEqual(summary["status"], "success")
            self.assertEqual(summary["tickers_calibrated"], ["NVDA"])

            loaded = colab_calibration.load_colab_calibration(calibration_path=cal_path)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["merton_stress"]["recommended_gamma"], 3.5)

            # Fallback when live 5m bars are unavailable (bars_df empty)
            fallback_pred = peak_predictor.predict_daily_peak(
                "NVDA",
                live_row={"price": 103.0, "open": 100.0, "rsi": 74.0, "vwap": 99.0},
                bars_df=pd.DataFrame(),
                cache_path=None,
                calibration_path=cal_path,
            )
            self.assertEqual(fallback_pred["status"], "ok")
            self.assertEqual(fallback_pred["profile"]["source"], "colab_calibration")
            self.assertEqual(fallback_pred["p50_peak_usd"], 102.5)
            self.assertIn("colab_prior", fallback_pred)

            # Enrichment when live 5m bars exist
            idx = pd.date_range("2026-11-24 09:30", periods=20, freq="5min", tz="America/New_York")
            live_df = pd.DataFrame(
                {
                    "Open": [100.0] * 20,
                    "High": [101.0 + i * 0.05 for i in range(20)],
                    "Low": [99.5] * 20,
                    "Close": [100.8] * 20,
                    "Volume": [1000] * 20,
                },
                index=idx,
            )
            live_pred = peak_predictor.predict_daily_peak(
                "NVDA",
                live_row={"price": 101.5, "open": 100.0},
                bars_df=live_df,
                cache_path=None,
                calibration_path=cal_path,
            )
            self.assertEqual(live_pred["status"], "ok")
            self.assertNotIn("source", live_pred["profile"])
            self.assertIn("colab_prior", live_pred)

    def test_calibration_http_endpoints(self):
        client = TestClient(web_server.app)
        with tempfile.TemporaryDirectory() as td:
            cal_path = os.path.join(td, "colab_calibration.json")
            orig_import = colab_calibration.import_colab_calibration
            orig_status = colab_calibration.get_calibration_status

            def _scoped_import(payload):
                return orig_import(payload, calibration_path=cal_path)

            def _scoped_status():
                return orig_status(calibration_path=cal_path)

            with patch.object(web_server, "import_colab_calibration", side_effect=_scoped_import), \
                 patch.object(web_server, "get_calibration_status", side_effect=_scoped_status):
                exp_resp = client.get("/api/calibration/export")
                self.assertEqual(exp_resp.status_code, 200)
                self.assertIn("gem_colab_export.json", exp_resp.headers.get("content-disposition", ""))
                self.assertEqual(exp_resp.json().get("schema_version"), "1.0")

                bad_resp = client.post("/api/calibration/import", json={"schema_version": "99.0"})
                self.assertEqual(bad_resp.status_code, 422)

                ok_resp = client.post("/api/calibration/import", json=self._sample_valid_calibration())
                self.assertEqual(ok_resp.status_code, 200)
                self.assertEqual(ok_resp.json()["tickers_calibrated"], ["NVDA"])

                st_resp = client.get("/api/calibration/status")
                self.assertEqual(st_resp.status_code, 200)
                self.assertTrue(st_resp.json()["loaded"])

    def test_extracted_modules_and_retired_scratch_noop(self):
        slim = chat_payload_parser.assemble_slim_tickers([{"ticker": "NVDA", "price": 100.0}])
        self.assertEqual(slim[0]["price_validity"], "UNAVAILABLE")
        self.assertEqual(slim[0]["freshness"], "UNAVAILABLE")
        self.assertIsNotNone(chat_payload_parser.is_halted_execution_payload(json.dumps({"runtime_flags": {"halt_active": True}})))
        self.assertIs(web_server._is_halted_execution_payload, chat_payload_parser.is_halted_execution_payload)

        dates = pd.date_range("2026-10-01", periods=30, freq="B")
        rng = np.random.default_rng(7)
        hist = {
            "AAPL": pd.DataFrame({"Close": 100.0 * np.cumprod(1.0 + rng.normal(0.001, 0.015, 30))}, index=dates),
            "MSFT": pd.DataFrame({"Close": 200.0 * np.cumprod(1.0 + rng.normal(0.001, 0.012, 30))}, index=dates),
        }
        res1 = merton_optimizer.compute_merton_from_history(hist, ["AAPL", "MSFT"], gamma=3.0, r=0.04)
        self.assertEqual(res1["status"], "success")
        self.assertAlmostEqual(sum(res1["normalized_long_only_weights"].values()), 1.0, places=5)
        with patch.object(merton_optimizer.np.linalg, "solve", side_effect=AssertionError("Must hit cache")) as _:
            res2 = merton_optimizer.compute_merton_from_history(hist, ["AAPL", "MSFT"], gamma=3.0, r=0.04)
        self.assertEqual(res1, res2)

        self.assertFalse(os.path.exists("scratch"))
        fetch_stocks.compile_master_document()
        self.assertFalse(os.path.exists(os.path.join("scratch", "master_trading_knowledge.md")))


if __name__ == "__main__":
    unittest.main()
