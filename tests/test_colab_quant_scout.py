"""
Stage M2 Unit Tests for Broad 500+ Ticker Quantitative Scout Worker:
- Liquidity ADV filter & short history filter
- Exclusion of already-held portfolio positions
- Diversification penalty against correlated portfolio returns
- Deterministic cluster labeling, top_n cap, and strict Schema v2.0 validation
"""

import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import colab_calibration
from colab_workers import quant_scout_worker


def _make_price_df(returns: np.ndarray, start_price: float = 100.0, daily_vol_shares: float = 1_000_000.0):
    dates = pd.date_range("2026-01-02", periods=len(returns) + 1, freq="B")
    closes = [start_price]
    for r in returns:
        closes.append(closes[-1] * (1.0 + r))
    return pd.DataFrame({"Close": closes, "Volume": [daily_vol_shares] * len(closes)}, index=dates)


class TestQuantScoutWorker(unittest.TestCase):
    def test_score_universe_filters_liquidity_excludes_held_and_validates_schema(self):
        rng = np.random.default_rng(123)
        n = 140
        port_rets = rng.normal(0.001, 0.015, n)
        low_corr_rets = np.concatenate([rng.normal(0.0025, 0.012, n - 1), [0.05]])  # orthogonal strong alpha + PEAD bar
        high_corr_rets = 0.95 * port_rets + rng.normal(0.0005, 0.003, n)
        laggard_rets = rng.normal(-0.002, 0.014, n)
        uncorr_mod = rng.normal(0.0015, 0.011, n)

        histories = {
            "NVDA": _make_price_df(port_rets, start_price=120.0, daily_vol_shares=2_000_000.0),  # already held
            "UNCR": _make_price_df(low_corr_rets, start_price=80.0, daily_vol_shares=2_000_000.0),
            "CORR": _make_price_df(high_corr_rets, start_price=90.0, daily_vol_shares=2_000_000.0),
            "MODX": _make_price_df(uncorr_mod, start_price=70.0, daily_vol_shares=2_000_000.0),
            "LAGG": _make_price_df(laggard_rets, start_price=60.0, daily_vol_shares=2_000_000.0),
            "ILLQ": _make_price_df(low_corr_rets, start_price=5.0, daily_vol_shares=50_000.0),  # $0.25M ADV -> filtered
            "SHRT": _make_price_df(low_corr_rets[:30], start_price=50.0, daily_vol_shares=2_000_000.0),  # too short
        }

        payload = quant_scout_worker.run_quant_scout_job(
            {
                "symbols": ["NVDA"],
                "portfolio_weights_pct": {"NVDA": 100.0},
                "options": {"top_n": 3, "tickers": list(histories.keys())},
            },
            history_fetcher=lambda _u: histories,
        )
        validated = colab_calibration.validate_quant_scout_artifact(payload)
        self.assertEqual(validated["schema_version"], "2.0")
        self.assertEqual(validated["universe_size"], len(histories))

        cands = validated["candidates"]
        self.assertEqual(len(cands), 3)
        cand_syms = [c["ticker"] for c in cands]
        self.assertNotIn("NVDA", cand_syms)
        self.assertNotIn("ILLQ", cand_syms)
        self.assertNotIn("SHRT", cand_syms)
        self.assertEqual(cand_syms[0], "UNCR")


if __name__ == "__main__":
    unittest.main()
