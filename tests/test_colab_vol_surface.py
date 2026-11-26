"""
Stage M2 Unit & Integration Tests for Multi-Expiry Vol Surface & 2nd-Order Greeks Worker
and AnalyticsEngine Read-Only Enrichment:
- Black-Scholes Gamma / Vanna / Charm numerical sanity & boundary guards
- Bounded SVI total-variance convergence on synthetic smiles
- Multi-expiry surface & dealer flow regime classification -> Schema v2.0 validation pass
- AnalyticsEngine.evaluate() + format_gex_engine_response() cached vol_surface enrichment & stale TTLs
"""

from datetime import datetime, timedelta, timezone
import math
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import analytics_engine
import colab_calibration
from colab_workers import vol_surface_worker


def _make_synthetic_chain(spot: float, dte: float, base_iv: float = 0.35, skew: float = -0.15):
    t = dte / 365.0
    strikes = np.linspace(spot * 0.75, spot * 1.25, 13)
    calls = []
    puts = []
    for k in strikes:
        km = math.log(k / spot)
        iv = max(0.10, base_iv + skew * km + 0.12 * km * km)
        oi_c = 1500.0 if k >= spot else 400.0
        oi_p = 1200.0 if k <= spot else 300.0
        calls.append({"strike": float(k), "impliedVolatility": float(iv), "openInterest": oi_c})
        puts.append({"strike": float(k), "impliedVolatility": float(iv * 1.03), "openInterest": oi_p})
    return {"dte": dte, "calls": calls, "puts": puts}


class TestVolSurfaceWorker(unittest.TestCase):
    def test_black_scholes_greeks_sanity_and_bounds(self):
        atm = vol_surface_worker.black_scholes_greeks(spot=100.0, strike=100.0, t_years=30.0 / 365.0, iv=0.35)
        self.assertGreater(atm["gamma"], 0.0)
        self.assertTrue(math.isfinite(atm["vanna"]))
        self.assertTrue(math.isfinite(atm["charm"]))

        zero_guard = vol_surface_worker.black_scholes_greeks(spot=0.0, strike=100.0, t_years=0.0, iv=0.0)
        self.assertEqual(zero_guard["gamma"], 0.0)
        self.assertEqual(zero_guard["vanna"], 0.0)

    def test_fit_svi_slice_converges_on_synthetic_smile(self):
        t_years = 30.0 / 365.0
        k = np.linspace(-0.4, 0.4, 15)
        true_w = 0.015 + 0.08 * (-0.35 * k + np.sqrt(k * k + 0.02))
        fit = vol_surface_worker.fit_svi_slice(k, true_w, t_years=t_years)
        self.assertLess(fit["fit_rmse"], 0.02)
        self.assertLess(fit["skew_25d"], 0.0)  # negative put/downside skew produces higher downside IV than upside IV

    def test_run_vol_surface_job_passes_schema_v2(self):
        chains_nvda = [
            _make_synthetic_chain(140.0, 14.0, base_iv=0.45),
            _make_synthetic_chain(140.0, 30.0, base_iv=0.42),
            _make_synthetic_chain(140.0, 60.0, base_iv=0.40),
        ]
        chains_spy = [
            _make_synthetic_chain(580.0, 14.0, base_iv=0.16),
            _make_synthetic_chain(580.0, 35.0, base_iv=0.17),
        ]

        def _mock_fetcher(sym: str):
            if sym == "NVDA":
                return (140.0, chains_nvda)
            if sym == "SPY":
                return (580.0, chains_spy)
            return None

        payload = vol_surface_worker.run_vol_surface_job({"symbols": ["NVDA"]}, chain_fetcher=_mock_fetcher)
        validated = colab_calibration.validate_vol_surface_artifact(payload)
        self.assertEqual(validated["schema_version"], "2.0")
        self.assertIn("NVDA", validated["tickers"])
        self.assertIn("SPY", validated["tickers"])
        self.assertEqual(validated["tickers"]["NVDA"]["expiries_analyzed"], 3)

    def test_analytics_engine_attaches_vol_surface_without_mutating_spot_gex(self):
        with tempfile.TemporaryDirectory() as td:
            gen_utc = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
            sample = {
                "schema_version": "2.0",
                "generated_at_utc": gen_utc,
                "source": "google_colab_mcp",
                "tickers": {
                    "NVDA": {
                        "atm_iv_30d": 44.0,
                        "svi_skew_25d": -3.1,
                        "term_structure_slope": -1.2,
                        "fit_rmse": 0.01,
                        "gamma_flip_multi_expiry": 135.0,
                        "net_gex_multi_expiry": 55.2,
                        "vanna_exposure": 14.8,
                        "charm_decay_bias": -4.2,
                        "dealer_flow_regime": "VANNA_TAILWIND",
                        "expiries_analyzed": 3,
                    }
                },
            }
            colab_calibration.import_colab_artifact("vol_surface", sample, cache_dir=td)

            orig_load = colab_calibration.load_colab_artifact
            orig_status = colab_calibration.get_artifact_status
            live_state = {
                "tickers": [
                    {
                        "ticker": "NVDA",
                        "price": 141.0,
                        "regular_close": 140.0,
                        "price_validity": "VALID",
                        "freshness": "CURRENT",
                        "dealer_posture": "LONG_GAMMA",
                        "net_gex_total": 0.85,
                        "gamma_flip_price": 138.0,
                        "rsi": 62.0,
                        "rvol": 1.4,
                        "vwap": 140.2,
                    }
                ]
            }
            ssot = {
                "mutable_state": {
                    "portfolio_snapshot": [{"ticker": "NVDA", "shares": 10, "wac": 120.0}],
                    "watched_tickers": [],
                    "unallocated_cash_eur": 1000.0,
                }
            }
            eng = analytics_engine.AnalyticsEngine()
            with patch.object(analytics_engine, "load_colab_artifact", side_effect=lambda at: orig_load(at, cache_dir=td)), \
                 patch.object(analytics_engine, "get_artifact_status", side_effect=lambda at: orig_status(at, cache_dir=td)):
                bundle = eng.evaluate(live_state, ssot=ssot)
                gex_json = eng.format_gex_engine_response(live_state, ssot=ssot)

            self.assertTrue(bundle["colab_vol_surface"]["loaded"])
            self.assertTrue(bundle["colab_vol_surface"]["fresh"])
            self.assertEqual(bundle["positions"][0]["dealer_posture"], "LONG_GAMMA")
            self.assertEqual(bundle["positions"][0]["vol_surface"]["dealer_flow_regime"], "VANNA_TAILWIND")
            self.assertFalse(bundle["positions"][0]["vol_surface"]["stale"])
            self.assertIn("vanna_exposure", gex_json)


if __name__ == "__main__":
    unittest.main()
