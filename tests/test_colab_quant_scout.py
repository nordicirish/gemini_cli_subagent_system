"""
Stage M2 Unit Tests for Broad 500+ Ticker Quantitative Scout Worker:
- Liquidity ADV filter & short history filter
- Exclusion of already-held portfolio positions
- Diversification penalty against correlated portfolio returns
- Deterministic cluster labeling, top_n cap, and strict Schema v2.0 validation
"""

import os
import sys
import tempfile
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

    def test_excludes_volatility_indices_macro_and_sector_etfs_end_to_end(self) -> None:
        rng = np.random.default_rng(77)
        n = 140
        strong_rets = np.concatenate([rng.normal(0.003, 0.01, n - 1), [0.06]])
        port_rets = rng.normal(0.001, 0.014, n)
        excluded_syms = ("VIXY", "^VIX", "^GSPC", "UVXY", "UUP", "IEF", "TLT", "GLD", "SPY", "QQQ", "XLV", "SMH")
        histories = {"NVDA": _make_price_df(port_rets), "PLTR": _make_price_df(strong_rets)}
        for sym in excluded_syms:
            histories[sym] = _make_price_df(strong_rets * 1.2)

        payload = quant_scout_worker.run_quant_scout_job(
            {
                "symbols": ["NVDA", *excluded_syms],
                "portfolio_weights_pct": {"NVDA": 100.0},
                "options": {"top_n": 25, "tickers": ["PLTR", *excluded_syms]},
            },
            history_fetcher=lambda u: {k: v for k, v in histories.items() if k in u},
        )
        cand_syms = [c["ticker"] for c in payload["candidates"]]
        self.assertEqual(cand_syms, ["PLTR"])

        # Defense-in-depth: validate_quant_scout_artifact and load_colab_artifact strip excluded tickers too
        dirty_payload = dict(payload)
        dirty_payload["candidates"] = [
            *payload["candidates"],
            {
                "ticker": "VIXY",
                "composite_score": 32.7,
                "rs_1m_pct": 10.0,
                "rs_3m_pct": 40.0,
                "rs_6m_pct": 25.0,
                "corr_to_portfolio": -0.5,
                "vol_regime": "ELEVATED",
                "cluster_label": "LOW_CORR_ALPHA",
                "pead_score": 0.0,
                "adv_usd_m": 150.0,
                "rationale": "Synthetic volatility ETF row",
            },
            {
                "ticker": "UUP",
                "composite_score": 4.0,
                "rs_1m_pct": 1.0,
                "rs_3m_pct": 2.0,
                "rs_6m_pct": 3.0,
                "corr_to_portfolio": -0.2,
                "vol_regime": "LOW_VOL",
                "cluster_label": "LOW_CORR_ALPHA",
                "pead_score": 0.0,
                "adv_usd_m": 100.0,
                "rationale": "Synthetic USD ETF row",
            },
        ]
        cleaned = colab_calibration.validate_quant_scout_artifact(dirty_payload)
        self.assertEqual([c["ticker"] for c in cleaned["candidates"]], ["PLTR"])

        with tempfile.TemporaryDirectory() as td:
            cache_path = colab_calibration.get_artifact_cache_path("quant_scout", cache_dir=td)
            import json
            with open(cache_path, "w", encoding="utf-8") as fh:
                json.dump(dirty_payload, fh)
            loaded = colab_calibration.load_colab_artifact("quant_scout", cache_dir=td)
            self.assertIsNotNone(loaded)
            self.assertEqual([c["ticker"] for c in loaded["candidates"]], ["PLTR"])
            # Legacy cached rows missing `sector` get auto-enriched with inferred sector
            self.assertEqual(loaded["candidates"][0]["sector"], "AI & Data")

    def test_sector_and_max_rsi_filtering(self) -> None:
        rng = np.random.default_rng(99)
        n = 140
        steady_rets = rng.normal(0.0012, 0.011, n)
        # Sharp 15-day rally so RSI-14 is very high (> 80)
        overbought_rets = np.concatenate([rng.normal(0.0, 0.01, n - 16), np.full(16, 0.025)])
        histories = {
            "NVDA": _make_price_df(steady_rets),        # held
            "REGN": _make_price_df(steady_rets * 1.1),  # Biotech (healthy RSI)
            "GILD": _make_price_df(overbought_rets),    # Biotech (overbought RSI)
            "WMT": _make_price_df(steady_rets * 1.2),   # Consumer Staples
            "LMT": _make_price_df(steady_rets * 1.15),  # Aerospace & Defense
        }
        # Filter to Biotech with max_rsi=75 -> keeps REGN only (excludes WMT, LMT, and high-RSI GILD)
        bio_payload = quant_scout_worker.run_quant_scout_job(
            {
                "symbols": ["NVDA"],
                "portfolio_weights_pct": {"NVDA": 100.0},
                "options": {"sectors": ["Biotech"], "max_rsi": 75.0, "top_n": 20},
            },
            history_fetcher=lambda u: {k: v for k, v in histories.items() if k in u},
        )
        validated = colab_calibration.validate_quant_scout_artifact(bio_payload)
        self.assertEqual([c["ticker"] for c in validated["candidates"]], ["REGN"])
        self.assertEqual(validated["candidates"][0]["sector"], "Biotech")
        self.assertLessEqual(validated["candidates"][0]["rsi_14"], 75.0)

        # Parent group matching: "Healthcare" matches "Biotech", "Industrials" matches "Aerospace & Defense"
        parent_payload = quant_scout_worker.run_quant_scout_job(
            {
                "symbols": ["NVDA"],
                "portfolio_weights_pct": {"NVDA": 100.0},
                "options": {"sectors": ["Healthcare", "Industrials"], "top_n": 20},
            },
            history_fetcher=lambda u: {k: v for k, v in histories.items() if k in u},
        )
        parent_syms = {c["ticker"] for c in parent_payload["candidates"]}
        self.assertEqual(parent_syms, {"REGN", "GILD", "LMT"})


if __name__ == "__main__":
    unittest.main()
