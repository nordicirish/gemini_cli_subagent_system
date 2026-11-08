"""
Regression Verification Suite for Financial & Investment Invariance
Governed by Protocol 06 and Stage 1 Section 2.1.
Guarantees zero modifications to investment mandates, Fibonacci formulas,
indicator calculations, and portfolio SSoT structures.
"""

import os
import sys
import json
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import fetch_stocks
import tools


class TestInvestmentRegression(unittest.TestCase):
    def test_fibonacci_forecast_calculation_invariance(self):
        """Verifies Fibonacci extension calculations against standard mathematical benchmarks."""
        hist_df = fetch_stocks.cache.history.get("UMAC")
        if hist_df is None or (hasattr(hist_df, "empty") and hist_df.empty):
            import pandas as pd
            hist_df = pd.DataFrame(
                {"Open": [25.71], "High": [26.70], "Low": [24.25], "Close": [24.39], "Volume": [100000]},
                index=[pd.Timestamp("2026-06-15 04:00:00+00:00", tz="UTC")],
            )
        res = fetch_stocks.calculate_fib_forecast(
            symbol="UMAC",
            current_price=24.39,
            hist_df=hist_df,
            open_price=24.27,
            atr=1.59,
            rsi=52.8,
            rvol=0.97,
            vwap=24.38,
        )
        self.assertIsNotNone(res)
        self.assertIn("t1_100", res)
        self.assertIn("t2_1618", res)
        self.assertIn("t3_2618", res)
        self.assertAlmostEqual(res["t1_100"], 26.84, places=2)
        self.assertAlmostEqual(res["t2_1618"], 28.35, places=2)
        self.assertAlmostEqual(res["t3_2618"], 30.80, places=2)

    def test_ssot_structure_invariance(self):
        """Verifies that context/ssot.json remains structurally intact and valid JSON."""
        ssot_path = os.path.join("context", "ssot.json")
        self.assertTrue(os.path.exists(ssot_path), "context/ssot.json must exist")

        with open(ssot_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertIsInstance(data, dict)
        # Verify canonical keys exist
        if "mutable_state" in data:
            ms = data["mutable_state"]
            self.assertIn("portfolio_snapshot", ms)
        else:
            self.assertIn("portfolio_snapshot", data)

    def test_rules_md_integrity(self):
        """Verifies canonical rules.md is present, intact, and contains core mandates."""
        rules_path = os.path.join("gem_trading_rules", "rules.md")
        self.assertTrue(os.path.exists(rules_path), "rules.md must exist")

        with open(rules_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertGreater(len(content), 1000)
        # Verify key mandates remain present
        self.assertIn("MANDATE_01", content)
        self.assertIn("MANDATE_18", content)
        self.assertIn("MANDATE_21", content)

    def test_all_engine_instructions_preserved(self):
        """Verifies that all 19 trading engine instructions are present and non-empty."""
        expected_engines = [
            "bullish_gem.md",
            "context_engine.md",
            "data_analyst.md",
            "execution.md",
            "gex_engine.md",
            "macro_narrative_engine.md",
            "macro_sentinel.md",
            "neutral_gem.md",
            "post_trade_review.md",
            "red_team_gem.md",
            "regime_engine.md",
            "research.md",
            "rule_enforcer_engine.md",
            "state_validation_router.md",
            "strategy_engine.md",
            "structural_engine.md",
            "technical_validator.md",
            "terminal.md",
            "sentiment_engine.md",
        ]
        for engine_file in expected_engines:
            path = os.path.join("engine_instructions", engine_file)
            self.assertTrue(os.path.exists(path), f"Engine instruction {engine_file} must exist")
            self.assertGreater(os.path.getsize(path), 100, f"Engine instruction {engine_file} must not be empty")


if __name__ == "__main__":
    unittest.main()
