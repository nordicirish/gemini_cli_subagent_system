"""Unit & Integration Tests for Phase 3 Python Analytics Offload."""

import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import web_server  # noqa: E402
from analytics_engine import AnalyticsEngine, compute_agreement_score  # noqa: E402


class TestAgreementScore(unittest.TestCase):
    def test_agreement_score_aligned_and_fragile(self):
        unanimous = compute_agreement_score({
            "bullish_advocate": "HOLD",
            "red_team_pessimist": {"stance": "HOLD", "weight": 1.2},
            "neutral_structuralist": "NEUTRAL",
        })
        self.assertEqual(unanimous["status"], "ALIGNED")
        self.assertFalse(unanimous["fragile"])
        self.assertEqual(unanimous["agreement_score"], 1.0)
        self.assertEqual(unanimous["dominant_stance"], "HOLD")

        split = compute_agreement_score({
            "bull": {"stance": "BUY", "weight": 1.0},
            "bear": {"stance": "TRIM", "weight": 1.0},
            "neut": {"stance": "HOLD", "weight": 1.0},
        })
        self.assertEqual(split["status"], "FRAGILE")
        self.assertTrue(split["fragile"])
        self.assertLess(split["agreement_score"], 0.51)
        self.assertEqual(split["dominant_stance"], "SPLIT")

        weighted = compute_agreement_score({
            "bull": {"stance": "ADD", "weight": 2.0},
            "bear": {"stance": "SELL", "weight": 1.0},
        })
        self.assertEqual(weighted["status"], "ALIGNED")
        self.assertEqual(weighted["dominant_stance"], "BULLISH")
        self.assertAlmostEqual(weighted["agreement_score"], 0.6667, places=4)

        empty = compute_agreement_score({})
        self.assertEqual(empty["status"], "FRAGILE")
        self.assertEqual(empty["agreement_score"], 0.0)


class TestPhase3RuleEnforcerAndStrategy(unittest.TestCase):
    def test_rule_enforcer_sec_72h_dilution_and_fail_open(self):
        engine = AnalyticsEngine()
        state = {
            "status": "OPEN",
            "tickers": [
                {"ticker": "^VIX", "price": 17.0, "price_validity": "VALID", "freshness": "CURRENT"},
                {"ticker": "SPY", "price": 600.0, "ma_50": 580.0, "price_validity": "VALID", "freshness": "CURRENT", "dealer_posture": "LONG_GAMMA", "net_gex_total": 0.03},
                {"ticker": "UMAC", "price": 9.20, "regular_close": 9.10, "vwap": 9.00, "distance_from_vwap": 2.2, "price_validity": "VALID", "freshness": "CURRENT"},
            ],
            "local_storage_state": {
                "mutable_state": {
                    "portfolio_snapshot": [{"ticker": "UMAC", "shares": "100", "wac": "9.00"}],
                    "watched_tickers": [],
                }
            },
        }

        def fake_sec(sym):
            return {
                "status": "ok",
                "ticker": sym,
                "dilution_warning_72h": sym == "UMAC",
                "filings": [
                    {
                        "form": "424B3",
                        "filing_date": "2026-11-23",
                        "is_dilution_form": True,
                        "url": "https://www.sec.gov/Archives/edgar/data/1956955/000168316826009073/umac_424b3.htm",
                    }
                ],
            }

        raw = engine.format_rule_enforcer_response(state, ssot=state["local_storage_state"], sec_fetcher=fake_sec)
        data = json.loads(raw)
        self.assertEqual(data["source"], "python_deterministic")
        self.assertEqual(data["verdict"], "VETO")
        self.assertTrue(data["has_sec_dilution_vetoes"])
        self.assertEqual(data["sec_dilution_vetoes"][0]["rule_id"], "ENH_117")
        self.assertEqual(data["sec_dilution_vetoes"][0]["form"], "424B3")

        # Offline SEC exception fails open cleanly
        raw_offline = engine.format_rule_enforcer_response(
            state,
            ssot=state["local_storage_state"],
            sec_fetcher=lambda _s: (_ for _ in ()).throw(RuntimeError("offline")),
        )
        offline_data = json.loads(raw_offline)
        self.assertEqual(offline_data["verdict"], "CLEAR")
        self.assertEqual(offline_data["sec_dilution_vetoes"], [])

    def test_strategy_engine_classifications_and_regime_blocking(self):
        engine = AnalyticsEngine()
        bear_volatile_state = {
            "status": "OPEN",
            "tickers": [
                {"ticker": "^VIX", "price": 23.0, "price_validity": "VALID", "freshness": "CURRENT"},
                {"ticker": "SPY", "price": 590.0, "ma_50": 580.0, "price_validity": "VALID", "freshness": "CURRENT", "dealer_posture": "SHORT_GAMMA", "net_gex_total": -0.02},
                {"ticker": "NVDA", "price": 135.0, "vwap": 132.0, "distance_from_vwap": 2.2, "rvol": 2.1, "trend": "UP", "rsi": 62.0, "gap_percent": 1.2, "price_validity": "VALID", "freshness": "CURRENT", "fib_forecast": {"mode": "EXTENSION"}},
                {"ticker": "EPST", "price": 25.0, "vwap": 24.0, "distance_from_vwap": 4.1, "rvol": 4.0, "trend": "UP", "rsi": 70.0, "gap_percent": 12.5, "price_validity": "VALID", "freshness": "CURRENT"},
            ],
            "local_storage_state": {
                "mutable_state": {
                    "portfolio_snapshot": [{"ticker": "NVDA", "shares": "10", "wac": "120"}],
                    "watched_tickers": ["EPST"],
                }
            },
        }
        res_bv = json.loads(engine.format_strategy_engine_response(bear_volatile_state, ssot=bear_volatile_state["local_storage_state"]))
        by_sym = {s["Ticker"]: s for s in res_bv["setups"]}
        self.assertEqual(res_bv["Volatility_State"], "BEAR_VOLATILE")
        self.assertEqual(by_sym["NVDA"]["Target_Setup"], "MOMENTUM_BREAKOUT")
        self.assertEqual(by_sym["NVDA"]["Strategy_Status"], "BLOCKED_BY_REGIME")
        self.assertEqual(by_sym["EPST"]["Target_Setup"], "EPISODIC_PIVOT")
        self.assertEqual(by_sym["EPST"]["Strategy_Status"], "PERMITTED")

        bull_quiet_state = {
            "status": "OPEN",
            "tickers": [
                {"ticker": "^VIX", "price": 15.0, "price_validity": "VALID", "freshness": "CURRENT"},
                {"ticker": "SPY", "price": 605.0, "ma_50": 580.0, "price_validity": "VALID", "freshness": "CURRENT", "dealer_posture": "LONG_GAMMA", "net_gex_total": 0.04},
                {"ticker": "AMD", "price": 160.0, "vwap": 159.0, "distance_from_vwap": 0.63, "rvol": 1.1, "trend": "UP", "rsi": 54.0, "gap_percent": 0.4, "price_validity": "VALID", "freshness": "CURRENT", "fib_forecast": {"mode": "RETRACEMENT"}},
            ],
            "local_storage_state": {
                "mutable_state": {
                    "portfolio_snapshot": [],
                    "watched_tickers": ["AMD"],
                }
            },
        }
        res_bq = json.loads(engine.format_strategy_engine_response(bull_quiet_state, ssot=bull_quiet_state["local_storage_state"]))
        self.assertEqual(res_bq["Volatility_State"], "BULL_QUIET")
        self.assertEqual(res_bq["setups"][0]["Target_Setup"], "PULLBACK_CONTINUATION")
        self.assertEqual(res_bq["setups"][0]["Strategy_Status"], "PERMITTED")

    def test_web_server_rule_enforcer_and_strategy_deterministic_in_council(self):
        self.assertTrue(getattr(web_server.rule_enforcer_engine, "_is_deterministic_python", False))
        self.assertTrue(getattr(web_server.strategy_engine, "_is_deterministic_python", False))
        self.assertTrue(getattr(web_server.rule_enforcer_engine, "_is_read_only_deliberative", False))
        self.assertTrue(getattr(web_server.strategy_engine, "_is_read_only_deliberative", False))

        sample_state = {
            "status": "OPEN",
            "tickers": [
                {"ticker": "^VIX", "price": 22.0, "price_validity": "VALID", "freshness": "CURRENT"},
                {"ticker": "SPY", "price": 710.0, "ma_50": 725.0, "price_validity": "VALID", "freshness": "CURRENT", "dealer_posture": "SHORT_GAMMA", "net_gex_total": -0.02},
                {"ticker": "UMAC", "price": 8.70, "regular_close": 9.60, "session_change_pct": -9.37, "vwap": 9.10, "distance_from_vwap": -4.40, "rvol": 1.8, "trend": "DOWN", "rsi": 35.0, "price_validity": "VALID", "freshness": "CURRENT"},
            ],
            "local_storage_state": {
                "mutable_state": {
                    "portfolio_snapshot": [{"ticker": "UMAC", "shares": "1860", "wac": "9.12"}],
                    "watched_tickers": [],
                }
            },
        }
        iso_engine = AnalyticsEngine()
        mock_sec = {
            "status": "ok",
            "ticker": "UMAC",
            "dilution_warning_72h": True,
            "filings": [{"form": "424B3", "filing_date": "2026-11-23", "is_dilution_form": True, "url": "https://www.sec.gov/Archives/edgar/data/1956955/000168316826009073/umac_424b3.htm"}],
        }
        with (
            patch.object(web_server, "ANALYTICS_ENGINE", iso_engine),
            patch("fetch_stocks.get_global_state_snapshot", return_value=sample_state),
            patch.object(web_server, "fetch_recent_sec_filings", return_value=mock_sec),
            patch.object(web_server.framework, "generate_response_with_fallback", side_effect=AssertionError("LLM must not be called")),
        ):
            raw = web_server.ask_council(json.dumps({
                "rule_enforcer_engine": "Audit UMAC mandates",
                "strategy_engine": "Classify setups",
            }))

        parsed = json.loads(raw)
        rule_out = json.loads(parsed["rule_enforcer_engine"])
        strat_out = json.loads(parsed["strategy_engine"])
        self.assertEqual(rule_out["source"], "python_deterministic")
        self.assertEqual(rule_out["verdict"], "TRIM_REQUIRED")
        self.assertTrue(rule_out["has_sec_dilution_vetoes"])
        self.assertEqual(strat_out["source"], "python_deterministic")
        self.assertEqual(strat_out["Volatility_State"], "BEAR_VOLATILE")


if __name__ == "__main__":
    unittest.main()
