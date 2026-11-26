"""
Stage M4 Unit & Integration Tests for Walk-Forward Strategy Backtester (#2),
Read-Only `read_colab_quant_signals` Council Tool, and Protocol 06 Evidence Pack:
- Causal simulation zero-lookahead invariant (mutating future bar t+1 never alters signal at bar t)
- Round-trip friction hurdle invariant (higher friction strictly <= return)
- Chronological walk-forward sweep -> strict Schema v2.0 `backtest_evidence` (`protocol_06_read_only=True`)
- `tools.read_colab_quant_signals` strictly READ classification, role permissions & ticker filter
"""

import json
import os
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import colab_calibration
from colab_workers import walk_forward_worker
import tool_registry
import tools


def _make_trend_ohlcv(n_bars: int = 320, seed: int = 99) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-02", periods=n_bars, freq="B")
    rets = rng.normal(0.0012, 0.014, n_bars)
    closes = 100.0 * np.cumprod(1.0 + rets)
    vols = rng.integers(800_000, 2_500_000, size=n_bars).astype(float)
    # Inject periodic high-rVol breakout days so signals fire realistically
    for idx in range(30, n_bars, 25):
        vols[idx] *= 2.2
        closes[idx:] *= 1.01
    return pd.DataFrame({"Close": closes, "Volume": vols}, index=dates)


class TestWalkForwardWorkerAndTool(unittest.TestCase):
    def test_zero_lookahead_and_friction_hurdle_invariants(self):
        df = _make_trend_ohlcv(120, seed=11)
        params = walk_forward_worker.BASELINE_RULE_PARAMS

        sig_orig = walk_forward_worker.compute_signal_series(df, params)
        # Mutate future closing prices at and after cutoff=60; signals strictly before 60 must be identical
        df_mut = df.copy()
        df_mut.iloc[60:, df_mut.columns.get_loc("Close")] *= 0.5
        df_mut.iloc[60:, df_mut.columns.get_loc("Volume")] *= 3.0
        sig_mut = walk_forward_worker.compute_signal_series(df_mut, params)
        self.assertTrue(np.array_equal(sig_orig[:60], sig_mut[:60]), "Future bar mutation altered past signal!")

        # Friction hurdle monotonicity: 2.0% hurdle <= 0.25% hurdle total compounded return
        r_low, _ = walk_forward_worker.simulate_strategy_returns(df, params, friction_hurdle_pct=0.25)
        r_high, _ = walk_forward_worker.simulate_strategy_returns(df, params, friction_hurdle_pct=2.00)
        eq_low = float(np.prod(1.0 + r_low))
        eq_high = float(np.prod(1.0 + r_high))
        self.assertLessEqual(eq_high, eq_low + 1e-12)

    def test_run_walk_forward_job_produces_valid_protocol06_evidence_pack(self):
        histories = {
            "NVDA": _make_trend_ohlcv(300, seed=1),
            "MSFT": _make_trend_ohlcv(300, seed=2),
            "SPY": _make_trend_ohlcv(300, seed=3),
        }
        payload = walk_forward_worker.run_walk_forward_job(
            {"symbols": ["NVDA", "MSFT"], "options": {"lookback_years": 3}},
            history_fetcher=lambda _syms, _yrs: histories,
        )
        validated = colab_calibration.validate_backtest_evidence_artifact(payload)
        self.assertEqual(validated["schema_version"], "2.0")
        self.assertTrue(validated["protocol_06_read_only"])
        self.assertGreaterEqual(validated["folds"], 2)
        self.assertEqual(validated["universe_count"], 3)
        self.assertEqual(validated["friction_hurdle_pct"], 0.85)
        self.assertGreaterEqual(validated["stability_score"], 0.0)
        self.assertLessEqual(validated["stability_score"], 1.0)

    def test_read_colab_quant_signals_tool_is_strictly_read_only_and_filters(self):
        tool_spec = tool_registry.GLOBAL_TOOL_REGISTRY.get("read_colab_quant_signals")
        self.assertIsNotNone(tool_spec)
        self.assertEqual(tool_spec.classification, "READ")
        self.assertFalse(tool_spec.affects_financial_state)
        self.assertIn("Bullish Advocate", tool_spec.allowed_roles)
        self.assertIn("Architecture Agent", tool_spec.allowed_roles)

        with tempfile.TemporaryDirectory() as td:
            colab_calibration.import_colab_artifact(
                "vol_surface",
                {
                    "schema_version": "2.0",
                    "generated_at_utc": "2026-11-26T20:00:00Z",
                    "tickers": {
                        "NVDA": {"vanna_exposure": 12.0, "dealer_flow_regime": "VANNA_TAILWIND"},
                        "PLTR": {"vanna_exposure": -4.0, "dealer_flow_regime": "CHARM_BLEED"},
                    },
                },
                cache_dir=td,
            )
            raw = tools.read_colab_quant_signals(artifact_type="vol_surface", ticker="NVDA", cache_dir=td)
            parsed = json.loads(raw)
            self.assertEqual(parsed["source"], "colab_cache_read_only")
            self.assertEqual(parsed["ticker_filter"], "NVDA")
            self.assertTrue(parsed["artifacts"]["vol_surface"]["loaded"])
            self.assertEqual(list(parsed["artifacts"]["vol_surface"]["data"]["tickers"].keys()), ["NVDA"])


if __name__ == "__main__":
    unittest.main()
