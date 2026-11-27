"""Unit & Integration Tests for Phase 1 Python Analytics Offload Engine."""

from datetime import datetime, timedelta
import os
import sys
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import web_server  # noqa: E402
from analytics_engine import (  # noqa: E402
    AnalyticsEngine,
    classify_regime,
    compute_position_tranches,
)

NY_TZ = ZoneInfo("America/New_York")


def _tick(
    ticker: str,
    price: float,
    *,
    regular_close: float = 10.0,
    vwap: float = 10.0,
    rvol: float = 1.2,
    rsi: float = 50.0,
    ma_20: float = 10.0,
    ma_50: float = 10.0,
    dealer_posture: str = "NEUTRAL",
    net_gex_total: float = 0.0,
    chg_pct: float = 0.0,
    gap_pct: float = 0.0,
    trend: str = "FLAT",
    trend_score: int = 0,
    score: int = 0,
    dist_vwap: float = None,
    hod: float = 12.0,
    validity: str = "VALID",
    freshness: str = "CURRENT",
) -> dict:
    computed_dist = dist_vwap if dist_vwap is not None else (((price - vwap) / vwap) * 100.0 if vwap > 0 else 0.0)
    return {
        "ticker": ticker,
        "price": price,
        "regular_close": regular_close,
        "price_validity": validity,
        "freshness": freshness,
        "vwap": vwap,
        "distance_from_vwap": round(computed_dist, 4),
        "rvol": rvol,
        "rsi": rsi,
        "ma_20": ma_20,
        "ma_50": ma_50,
        "dealer_posture": dealer_posture,
        "net_gex_total": net_gex_total,
        "session_change_pct": chg_pct,
        "gap_percent": gap_pct,
        "trend": trend,
        "trend_score": trend_score,
        "score": score,
        "fib_forecast": {
            "mode": "EXTENSION" if chg_pct >= 0 else "RETRACEMENT",
            "pA": 8.65,
            "pB": hod,
            "next_resistance": 9.07,
            "daily_peak_target": hod,
            "daily_peak_status": "EXPANDING" if chg_pct >= 0 else "PULLBACK",
        },
    }


class TestRegimeAndSizing(unittest.TestCase):
    def test_classify_regime_permissibility_matrix(self):
        bear = classify_regime(
            [
                _tick("^VIX", 24.5, volatility_regime_unused=None) if False else {**_tick("^VIX", 24.5), "volatility_regime": "HIGH_VOL"},
                _tick("SPY", 718.0, ma_50=730.0, dealer_posture="SHORT_GAMMA", net_gex_total=-0.015),
                _tick("QQQ", 638.0, ma_50=650.0),
            ],
            market_status="OPEN",
        )
        self.assertEqual(bear["volatility_state"], "BEAR_VOLATILE")
        self.assertFalse(bear["strategy_permissibility"]["Momentum_Breakouts"])
        self.assertFalse(bear["strategy_permissibility"]["Pullback_Continuations"])
        self.assertTrue(bear["strategy_permissibility"]["Mean_Reversion"])
        self.assertTrue(bear["strategy_permissibility"]["Parabolic_Shorts"])

        bull = classify_regime(
            [
                {**_tick("^VIX", 14.2), "volatility_regime": "NORMAL"},
                _tick("SPY", 740.0, ma_20=735.0, ma_50=720.0, dealer_posture="LONG_GAMMA", net_gex_total=0.02),
                _tick("QQQ", 660.0, ma_50=640.0),
            ],
            market_status="OPEN",
        )
        self.assertEqual(bull["volatility_state"], "BULL_QUIET")
        self.assertTrue(bull["strategy_permissibility"]["Momentum_Breakouts"])
        self.assertFalse(bull["strategy_permissibility"]["Mean_Reversion"])

        neutral = classify_regime(
            [
                _tick("^VIX", 18.8),
                _tick("SPY", 725.0, ma_20=722.0, ma_50=726.0),
                _tick("QQQ", 650.0, ma_50=645.0),
            ],
            market_status="OPEN",
        )
        self.assertEqual(neutral["volatility_state"], "NEUTRAL")
        self.assertFalse(neutral["strategy_permissibility"]["Momentum_Breakouts"])
        self.assertTrue(neutral["strategy_permissibility"]["Episodic_Pivots"])

    def test_exact_decimal_trim_sizing_and_proofs(self):
        res = compute_position_tranches(
            ticker="UMAC",
            shares_raw="1860",
            wac_raw="9.12",
            price_raw="8.80",
            prev_close_raw="9.50",
            eurusd_rate_raw="1.10",
        )
        self.assertEqual(res["shares"], 1860)
        self.assertEqual(res["tranches"]["25pct"]["sell_shares"], 465)
        self.assertEqual(res["tranches"]["25pct"]["remaining_shares"], 1395)
        self.assertEqual(res["tranches"]["15pct"]["sell_shares"], 279)
        self.assertEqual(res["tranches"]["15pct"]["remaining_shares"], 1581)
        self.assertEqual(res["tranches"]["50pct"]["sell_shares"], 930)
        self.assertEqual(res["tranches"]["50pct"]["remaining_shares"], 930)
        self.assertAlmostEqual(res["tranches"]["25pct"]["sweep_limit_usd"], 8.76, places=2)
        self.assertAlmostEqual(res["tranches"]["25pct"]["front_run_limit_usd"], 8.78, places=2)
        self.assertAlmostEqual(res["tranches"]["25pct"]["est_proceeds_usd"], 4092.00, places=2)
        self.assertAlmostEqual(res["tranches"]["25pct"]["est_proceeds_eur"], 3720.00, places=2)
        self.assertIn("Proof:", res["math_proof"])
        self.assertIn("Proof:", res["fx_proof"])

    def test_live_immutable_snapshot_prices_held_positions(self):
        # The server publishes GLOBAL_STATE as fetch_stocks.ImmutableDict / ImmutableList
        # (Mapping / Sequence, not dict / list). Rows must not be dropped by dict-only checks.
        import fetch_stocks
        from analytics_engine import AnalyticsEngine

        plain = {
            "status": "OPEN",
            "tickers": [_tick("UMAC", 8.25), _tick("^VIX", 31.0), _tick("SPY", 704.0)],
            "local_storage_state": {"portfolio_snapshot": [{"ticker": "UMAC", "shares": "1860", "wac": "12.60"}]},
        }
        frozen = fetch_stocks.freeze_snapshot(plain)  # exactly how publish_global_state_snapshot freezes it
        self.assertNotIsInstance(frozen, dict)
        self.assertNotIsInstance(frozen["tickers"], list)

        engine = AnalyticsEngine()
        res = engine.evaluate(frozen, ssot=frozen["local_storage_state"], fx_info={"available": True, "eurusd_rate": 1.097})
        pos = res["positions"][0]
        self.assertTrue(pos["valid_telemetry"])
        self.assertAlmostEqual(pos["price"], 8.25, places=2)
        self.assertAlmostEqual(pos["unrealized_pnl_pct"], -40.31, places=2)

        plan = engine.build_execution_plan(frozen, ssot=frozen["local_storage_state"], fx_info={"available": True, "eurusd_rate": 1.097})
        self.assertAlmostEqual(plan["positions"][0]["price"], 8.25, places=2)
        self.assertEqual(
            classify_regime(frozen["tickers"], market_status="OPEN"),
            classify_regime(plain["tickers"], market_status="OPEN"),
        )
        import json as _json
        gex_out = _json.loads(engine.format_gex_engine_response(frozen, ssot=frozen["local_storage_state"]))
        strat_out = _json.loads(engine.format_strategy_engine_response(frozen, ssot=frozen["local_storage_state"]))
        self.assertGreaterEqual(len(gex_out["tickers"]), 3)
        self.assertEqual(strat_out["setups"][0]["Ticker"], "UMAC")

    def test_engine_reads_rate_from_real_fx_public_snapshot(self):
        # Production passes fx_rates.fx_public(...), which carries the rate under "rate" (a string).
        from decimal import Decimal
        import fx_rates
        from analytics_engine import AnalyticsEngine

        quote = fx_rates.FxQuote(rate=Decimal("1.097"), as_of_utc="2026-11-25T13:00:00Z", source="test")
        fx_snapshot = fx_rates.fx_public(quote)
        self.assertNotIn("eurusd_rate", fx_snapshot)
        state = {"status": "OPEN", "tickers": [_tick("UMAC", 8.25)]}
        ssot = {"portfolio_snapshot": [{"ticker": "UMAC", "shares": "1860", "wac": "12.60"}]}
        pos = AnalyticsEngine().evaluate(state, ssot=ssot, fx_info=fx_snapshot)["positions"][0]
        self.assertAlmostEqual(pos["unrealized_pnl_pct"], -40.31, places=2)
        self.assertIsNotNone(pos["market_value_eur"])
        unavailable = AnalyticsEngine().evaluate(state, ssot=ssot, fx_info=fx_rates.fx_public(None))["positions"][0]
        self.assertIsNone(unavailable["unrealized_pnl_pct"])

    def test_pnl_converts_eur_wac_to_usd_cost_basis(self):
        # WAC €12.60 at 1.097 USD/EUR = $13.8222/share; spot $8.25 (USD).
        res = compute_position_tranches(
            ticker="UMAC", shares_raw="1860", wac_raw="12.60", price_raw="8.25", eurusd_rate_raw="1.097",
        )
        self.assertAlmostEqual(res["wac_usd"], 13.8222, places=4)
        self.assertAlmostEqual(res["cost_basis_usd"], 25709.29, places=2)   # 1860 × 12.60 × 1.097
        self.assertAlmostEqual(res["market_value_usd"], 15345.00, places=2)
        self.assertAlmostEqual(res["unrealized_pnl_usd"], -10364.29, places=2)
        self.assertAlmostEqual(res["unrealized_pnl_pct"], -40.31, places=2)  # not -34.52 (EUR WAC read as USD)

        # No fresh FX quote: P&L is unknown rather than computed at a guessed rate.
        no_fx = compute_position_tranches(ticker="UMAC", shares_raw="1860", wac_raw="12.60", price_raw="8.25")
        self.assertIsNone(no_fx["unrealized_pnl_usd"])
        self.assertIsNone(no_fx["unrealized_pnl_pct"])
        self.assertIsNone(no_fx["cost_basis_usd"])
        self.assertAlmostEqual(no_fx["market_value_usd"], 15345.00, places=2)


class TestDeterministicMandatesAndTrackers(unittest.TestCase):
    def setUp(self):
        self.engine = AnalyticsEngine()
        self.ssot = {
            "mutable_state": {
                "portfolio_snapshot": [{"ticker": "UMAC", "shares": "1860", "wac": "9.12"}],
                "watched_tickers": ["AMZN"],
            }
        }
        self.fx = {"available": True, "eurusd_rate": 1.10}

    def test_global_vetoes_mandate_52_and_mandate_54(self):
        t_noon = datetime(2026, 11, 24, 12, 15, tzinfo=NY_TZ)
        live_state = {
            "status": "OPEN",
            "tickers": [
                _tick("^VIX", 25.0),
                _tick("^VVIX", 112.0),
                _tick("IEF", 91.20, chg_pct=-0.25),
                _tick("SPY", 718.0, ma_50=730.0, dealer_posture="SHORT_GAMMA", net_gex_total=-0.02),
                _tick("UMAC", 8.65, vwap=8.95, dist_vwap=-3.35, chg_pct=-8.5, rvol=0.70, trend="DOWN"),
                _tick("AMZN", 252.0, vwap=244.0, dist_vwap=3.28, rvol=1.60, trend="UP"),
            ],
        }
        out = self.engine.evaluate(live_state, ssot=self.ssot, fx_info=self.fx, ny_dt=t_noon)
        veto_ids = {v["rule_id"] for v in out["global_vetoes"]}
        self.assertEqual(veto_ids, {"ENH_72", "MANDATE_48", "IEF_YIELD_ALERT"})

        umac_mandates = {m["rule_id"] for m in out["positions"][0]["triggered_mandates"]}
        self.assertIn("MANDATE_52", umac_mandates)
        self.assertIn("MANDATE_54", umac_mandates)
        self.assertIn("MANDATE_50", umac_mandates)

        amzn_sig = out["watchlist_signals"][0]
        self.assertTrue(amzn_sig["rvol_confirmed"])
        self.assertTrue(amzn_sig["vwap_execution_blocked"])

    def test_mandate_47_whipsaw_shield_and_volume_override(self):
        t_open = datetime(2026, 11, 24, 9, 45, tzinfo=NY_TZ)
        low_vol = {
            "status": "OPEN",
            "tickers": [
                _tick("SPY", 720.0, dealer_posture="SHORT_GAMMA", net_gex_total=-0.01),
                _tick("UMAC", 8.80, vwap=9.00, rvol=1.2, trend="DOWN"),
            ],
        }
        out1 = self.engine.evaluate(low_vol, ssot=self.ssot, fx_info=self.fx, ny_dt=t_open)
        ids1 = {m["rule_id"] for m in out1["positions"][0]["triggered_mandates"]}
        self.assertIn("MANDATE_47", ids1)
        self.assertNotIn("MANDATE_43", ids1)

        high_vol = {
            "status": "OPEN",
            "tickers": [
                _tick("SPY", 720.0, dealer_posture="SHORT_GAMMA", net_gex_total=-0.01),
                _tick("UMAC", 8.80, vwap=9.00, rvol=3.4, trend="DOWN"),
            ],
        }
        out2 = self.engine.evaluate(high_vol, ssot=self.ssot, fx_info=self.fx, ny_dt=t_open)
        ids2 = {m["rule_id"] for m in out2["positions"][0]["triggered_mandates"]}
        self.assertIn("MANDATE_43", ids2)

    def test_mandate_38_timer_enh_17_b_whiplash_and_fail_closed_stale_guard(self):
        t0 = datetime(2026, 11, 24, 10, 0, tzinfo=NY_TZ)
        self.engine.record_tick([_tick("UMAC", 10.0, rsi=78.0, dealer_posture="LONG_GAMMA")], "OPEN", ny_dt=t0)
        self.engine.record_tick([_tick("UMAC", 10.2, rsi=79.0, dealer_posture="SHORT_GAMMA")], "OPEN", ny_dt=t0 + timedelta(minutes=60))
        self.engine.record_tick([_tick("UMAC", 10.4, rsi=80.0, dealer_posture="LONG_GAMMA")], "OPEN", ny_dt=t0 + timedelta(minutes=245))

        live_state = {
            "status": "OPEN",
            "tickers": [_tick("UMAC", 10.4, rsi=80.0, dealer_posture="LONG_GAMMA")],
        }
        out = self.engine.evaluate(live_state, ssot=self.ssot, fx_info=self.fx, ny_dt=t0 + timedelta(minutes=245))
        ids = {m["rule_id"] for m in out["positions"][0]["triggered_mandates"]}
        self.assertIn("MANDATE_38", ids)
        self.assertIn("ENH_17_B", ids)
        self.assertIn("ENH_111", ids)

        # Fail-closed guard: stale/invalid tick yields zero price-triggered mandates and no sizing
        stale_state = {
            "status": "OPEN",
            "tickers": [_tick("UMAC", 5.00, chg_pct=-40.0, validity="INVALID")],
        }
        stale_out = self.engine.evaluate(stale_state, ssot=self.ssot, fx_info=self.fx, ny_dt=t0 + timedelta(minutes=250))
        self.assertFalse(stale_out["positions"][0]["valid_telemetry"])
        self.assertEqual(stale_out["positions"][0]["triggered_mandates"], [])
        self.assertEqual(stale_out["positions"][0]["tranches"], {})


class TestAnalyticsApiRoute(unittest.TestCase):
    def test_analytics_http_route_returns_expected_contract(self):
        iso_engine = AnalyticsEngine()
        client = TestClient(web_server.app)
        sample_state = {
            "status": "OPEN",
            "tickers": [
                _tick("^VIX", 23.0),
                _tick("SPY", 718.0, ma_50=730.0, dealer_posture="SHORT_GAMMA", net_gex_total=-0.01),
                _tick("UMAC", 8.80, vwap=8.95, rvol=0.65, chg_pct=-5.0, trend="DOWN"),
            ],
            "local_storage_state": {
                "mutable_state": {
                    "portfolio_snapshot": [{"ticker": "UMAC", "shares": "1860", "wac": "9.12"}],
                    "watched_tickers": [],
                }
            },
        }
        with (
            patch.object(web_server, "ANALYTICS_ENGINE", iso_engine),
            patch("fetch_stocks.get_global_state_snapshot", return_value=sample_state),
        ):
            res = client.get("/api/analytics")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["regime"]["volatility_state"], "BEAR_VOLATILE")
        self.assertGreaterEqual(len(body["global_vetoes"]), 2)
        self.assertEqual(body["positions"][0]["tranches"]["25pct"]["sell_shares"], 465)


if __name__ == "__main__":
    unittest.main()
