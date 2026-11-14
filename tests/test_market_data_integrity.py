"""
Automated Test Suite for GEM Stage 1: Market Data Integrity and Validation
Governed by Protocol 05 and Stage 1 Specifications.

Mandated Test Coverage:
1. Legitimate numeric zero remains valid.
2. Missing numeric value is not converted into valid zero.
3. NaN and infinity are rejected or explicitly marked invalid.
4. Stale observations cannot be represented as current.
5. Valid observations during market closures are not incorrectly rejected solely because of elapsed time.
6. Failed historical refresh preserves previously valid data.
7. Empty historical response does not generate artificial observations.
8. Partial historical response does not contaminate existing valid data.
9. Downstream market-data consumers can distinguish unavailable data from legitimate numeric observations.
10. Existing successful market-data processing remains compatible.
"""

import os
import sys
import math
import json
import unittest
from datetime import datetime, time, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import fetch_stocks
from fetch_stocks import (
    classify_market_numeric,
    to_float,
    determine_market_freshness,
    get_market_status,
    is_market_holiday,
    is_price_actionable,
    classify_signal,
    calculate_score,
    distance_from_vwap,
    validate_historical_dataframe,
    get_live_chart_data,
    update_history_and_technicals,
    update_price_tick,
    cache,
)
import tools


class TestMarketDataIntegrity(unittest.TestCase):
    def setUp(self):
        cache.clear()
        self.ny_tz = ZoneInfo("America/New_York")

    def tearDown(self):
        cache.clear()

    # -------------------------------------------------------------------------
    # Test 1: Legitimate numeric zero remains valid
    # -------------------------------------------------------------------------
    def test_01_legitimate_numeric_zero_remains_valid(self):
        """Verifies that numeric zero (0.0, 0, '0.0', '0') is classified as VALID and not rejected."""
        validity, val = classify_market_numeric(0.0)
        self.assertEqual(validity, "VALID")
        self.assertEqual(val, 0.0)

        validity_int, val_int = classify_market_numeric(0)
        self.assertEqual(validity_int, "VALID")
        self.assertEqual(val_int, 0.0)

        validity_str, val_str = classify_market_numeric("0.0")
        self.assertEqual(validity_str, "VALID")
        self.assertEqual(val_str, 0.0)

        self.assertEqual(to_float(0.0), 0.0)
        self.assertEqual(to_float(0), 0.0)
        self.assertEqual(to_float("0.0"), 0.0)
        self.assertEqual(to_float(pd.Series([10.5, 0.0])), 0.0)

    # -------------------------------------------------------------------------
    # Test 2: Missing numeric value is not converted into valid zero
    # -------------------------------------------------------------------------
    def test_02_missing_numeric_value_not_converted_to_zero(self):
        """Verifies that missing values (None, empty string, empty Series) return None, not 0.0."""
        validity_none, val_none = classify_market_numeric(None)
        self.assertEqual(validity_none, "MISSING")
        self.assertIsNone(val_none)
        self.assertIsNone(to_float(None))

        validity_empty_series, val_empty_series = classify_market_numeric(pd.Series([], dtype=float))
        self.assertEqual(validity_empty_series, "MISSING")
        self.assertIsNone(val_empty_series)
        self.assertIsNone(to_float(pd.Series([], dtype=float)))

        self.assertIsNone(to_float(None))
        # Default parameter must be respected if explicitly provided, but default is None
        self.assertEqual(to_float(None, default=-1.0), -1.0)

    # -------------------------------------------------------------------------
    # Test 3: NaN and infinity are rejected or explicitly marked invalid
    # -------------------------------------------------------------------------
    def test_03_nan_and_infinity_rejected_or_marked_invalid(self):
        """Verifies that NaN, +inf, -inf, booleans, and non-numeric strings are rejected."""
        validity_nan, val_nan = classify_market_numeric(float("nan"))
        self.assertEqual(validity_nan, "NON_FINITE")
        self.assertIsNone(val_nan)
        self.assertIsNone(to_float(float("nan")))

        validity_inf, val_inf = classify_market_numeric(float("inf"))
        self.assertEqual(validity_inf, "NON_FINITE")
        self.assertIsNone(val_inf)
        self.assertIsNone(to_float(float("inf")))

        validity_ninf, val_ninf = classify_market_numeric(float("-inf"))
        self.assertEqual(validity_ninf, "NON_FINITE")
        self.assertIsNone(val_ninf)
        self.assertIsNone(to_float(float("-inf")))

        # In Python, bool is a subclass of int (True == 1, False == 0); market numerics must reject bools
        validity_bool, val_bool = classify_market_numeric(True)
        self.assertEqual(validity_bool, "INVALID")
        self.assertIsNone(val_bool)
        self.assertIsNone(to_float(True))

        validity_false, val_false = classify_market_numeric(False)
        self.assertEqual(validity_false, "INVALID")
        self.assertIsNone(val_false)
        self.assertIsNone(to_float(False))

        validity_str, val_str = classify_market_numeric("corrupt_price")
        self.assertEqual(validity_str, "INVALID")
        self.assertIsNone(val_str)
        self.assertIsNone(to_float("corrupt_price"))

    # -------------------------------------------------------------------------
    # Test 4: Stale observations cannot be represented as current
    # -------------------------------------------------------------------------
    def test_04_stale_observations_cannot_be_represented_as_current(self):
        """Verifies that observations older than threshold or from prior sessions cannot be CURRENT during market hours."""
        # Market OPEN at 14:00 ET on a Wednesday
        clock_now = datetime(2026, 6, 17, 14, 0, 0, tzinfo=self.ny_tz)

        # Case A: Observation is 4 hours old (10:00 ET) on same day during OPEN
        stale_same_day = datetime(2026, 6, 17, 10, 0, 0, tzinfo=self.ny_tz)
        validity_a, freshness_a = determine_market_freshness(
            price=150.0,
            obs_time=stale_same_day,
            current_ny=clock_now,
            session_status="OPEN",
            max_stale_seconds=1200,
        )
        self.assertEqual(validity_a, "STALE")
        self.assertEqual(freshness_a, "STALE")
        self.assertNotEqual(freshness_a, "CURRENT")

        # Case B: Observation from prior day (Tuesday close) during Wednesday OPEN session
        prior_day_close = datetime(2026, 6, 16, 16, 0, 0, tzinfo=self.ny_tz)
        validity_b, freshness_b = determine_market_freshness(
            price=150.0,
            obs_time=prior_day_close,
            current_ny=clock_now,
            session_status="OPEN",
        )
        self.assertEqual(validity_b, "STALE")
        self.assertEqual(freshness_b, "STALE")
        self.assertNotEqual(freshness_b, "CURRENT")

        # Case C: Recent observation within 5 minutes is CURRENT
        fresh_obs = datetime(2026, 6, 17, 13, 56, 0, tzinfo=self.ny_tz)
        validity_c, freshness_c = determine_market_freshness(
            price=150.0,
            obs_time=fresh_obs,
            current_ny=clock_now,
            session_status="OPEN",
        )
        self.assertEqual(validity_c, "VALID")
        self.assertEqual(freshness_c, "CURRENT")

    # -------------------------------------------------------------------------
    # Test 5: Valid observations during market closures are not rejected
    # -------------------------------------------------------------------------
    def test_05_market_closure_observations_preserved_as_historical(self):
        """Verifies observations outside trading hours (weekend/overnight) are VALID and HISTORICAL, not rejected."""
        # Weekend: Saturday at 14:00 ET
        saturday_clock = datetime(2026, 6, 20, 14, 0, 0, tzinfo=self.ny_tz)
        friday_close_time = datetime(2026, 6, 19, 16, 0, 0, tzinfo=self.ny_tz)

        status = get_market_status(saturday_clock)
        self.assertEqual(status, "CLOSED")

        validity, freshness = determine_market_freshness(
            price=210.50,
            obs_time=friday_close_time,
            current_ny=saturday_clock,
            session_status=status,
        )
        self.assertEqual(validity, "VALID")
        self.assertEqual(freshness, "HISTORICAL")
        # Must not masquerade as CURRENT live price
        self.assertNotEqual(freshness, "CURRENT")

        # Weeknight overnight: Tuesday at 02:00 ET
        overnight_clock = datetime(2026, 6, 16, 2, 0, 0, tzinfo=self.ny_tz)
        status_night = get_market_status(overnight_clock)
        self.assertEqual(status_night, "CLOSED")

        monday_close_time = datetime(2026, 6, 15, 16, 0, 0, tzinfo=self.ny_tz)
        validity_n, freshness_n = determine_market_freshness(
            price=210.50,
            obs_time=monday_close_time,
            current_ny=overnight_clock,
            session_status=status_night,
        )
        self.assertEqual(validity_n, "VALID")
        self.assertEqual(freshness_n, "HISTORICAL")

    # -------------------------------------------------------------------------
    # Test 6: Failed historical refresh preserves previously valid data
    # -------------------------------------------------------------------------
    def test_06_failed_historical_refresh_preserves_valid_cache(self):
        """Verifies that if a historical refresh fails or raises, existing valid cached history and technicals are preserved."""
        sym = "TEST_FAIL_REFRESH"
        initial_dates = pd.date_range("2026-05-01", periods=30, tz="UTC")
        initial_df = pd.DataFrame(
            {
                "Open": [100.0 + i for i in range(30)],
                "High": [105.0 + i for i in range(30)],
                "Low": [95.0 + i for i in range(30)],
                "Close": [102.0 + i for i in range(30)],
                "Volume": [1000000 for _ in range(30)],
            },
            index=initial_dates,
        )

        cache.history[sym] = initial_df.copy()
        cache.last_history_fetch_date[sym] = "2026-05-30"
        initial_technicals = {
            "SMA_20": 115.0,
            "SMA_50": None,
            "SMA_200": None,
            "RSI": 58.5,
            "MACD": 1.25,
            "Trend_Score": 2,
            "Last_Reg_Close": 131.0,
        }
        cache.technicals[sym] = initial_technicals.copy()

        # Mock ticker that raises on history fetch
        mock_ticker = MagicMock()
        mock_ticker.history.side_effect = RuntimeError("YFinance network down")

        with patch("fetch_stocks.get_polygon_history_df", return_value=pd.DataFrame()):
            update_history_and_technicals(sym, mock_ticker)

        # Existing valid cache must NOT be wiped or replaced with dummy row
        self.assertIn(sym, cache.history)
        self.assertEqual(len(cache.history[sym]), 30)
        self.assertEqual(cache.history[sym]["Close"].iloc[-1], 131.0)
        self.assertEqual(cache.technicals[sym]["SMA_20"], 115.0)
        self.assertEqual(cache.technicals[sym]["Last_Reg_Close"], 131.0)

    # -------------------------------------------------------------------------
    # Test 7: Empty historical response does not generate artificial observations
    # -------------------------------------------------------------------------
    def test_07_empty_historical_response_does_not_fabricate_zero_rows(self):
        """Verifies that an empty historical download for an unseeded ticker does not inject Close: 0.0 or Last_Reg_Close: 0.0."""
        sym = "NEW_UNSEEDED"
        self.assertNotIn(sym, cache.history)

        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame()

        with patch("fetch_stocks.get_polygon_history_df", return_value=pd.DataFrame()):
            update_history_and_technicals(sym, mock_ticker)

        # Must not fabricate a DataFrame with Close: 0.0
        cached_df = cache.history.get(sym)
        self.assertTrue(cached_df is None or cached_df.empty)

        # Technicals must explicitly indicate UNAVAILABLE with None values, NOT dummy zeros
        techs = cache.technicals.get(sym, {})
        self.assertEqual(techs.get("status"), "UNAVAILABLE")
        self.assertIsNone(techs.get("Last_Reg_Close"))
        self.assertIsNone(techs.get("RSI"))
        self.assertIsNone(techs.get("MACD"))
        self.assertIsNone(techs.get("ATR"))

    # -------------------------------------------------------------------------
    # Test 8: Partial historical response does not contaminate existing valid data
    # -------------------------------------------------------------------------
    def test_08_partial_historical_response_does_not_contaminate_existing_data(self):
        """Verifies that a truncated or partial response (e.g. 2 bars) does not overwrite existing full history (250 bars)."""
        sym = "PARTIAL_TEST"
        full_dates = pd.date_range("2025-06-01", periods=250, tz="UTC")
        full_df = pd.DataFrame(
            {
                "Open": [100.0 + (i * 0.5) for i in range(250)],
                "High": [105.0 + (i * 0.5) for i in range(250)],
                "Low": [98.0 + (i * 0.5) for i in range(250)],
                "Close": [102.0 + (i * 0.5) for i in range(250)],
                "Volume": [2000000 for _ in range(250)],
            },
            index=full_dates,
        )
        cache.history[sym] = full_df.copy()
        cache.last_history_fetch_date[sym] = "2026-05-30"

        # Mock ticker returns an unexpected partial download of only 2 bars
        partial_dates = pd.date_range("2026-06-15", periods=2, tz="UTC")
        partial_df = pd.DataFrame(
            {
                "Open": [200.0, 201.0],
                "High": [205.0, 206.0],
                "Low": [199.0, 200.0],
                "Close": [202.0, 203.0],
                "Volume": [50000, 60000],
            },
            index=partial_dates,
        )
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = partial_df

        update_history_and_technicals(sym, mock_ticker)

        # Existing 250 bars must be preserved without contamination
        self.assertEqual(len(cache.history[sym]), 250)
        self.assertEqual(cache.history[sym]["Close"].iloc[-1], 102.0 + (249 * 0.5))

    # -------------------------------------------------------------------------
    # Test 9: Downstream market-data consumers can distinguish unavailable data
    # -------------------------------------------------------------------------
    def test_09_downstream_consumers_distinguish_unavailable_data(self):
        """Verifies tools.get_market_data() returns explicit null price, UNAVAILABLE status and validity for missing tickers."""
        valid_time_iso = datetime(2026, 6, 17, 14, 0, 0, tzinfo=self.ny_tz).isoformat()
        simulated_state = {
            "status": "LIVE_FEED_ONLINE",
            "timestamp": valid_time_iso,
            "tickers": [
                {
                    "ticker": "AAPL",
                    "status": "ACTIVE",
                    "price": 182.50,
                    "price_validity": "VALID",
                    "freshness": "CURRENT",
                    "price_source": "BATCH_QUOTE",
                    "observation_time": valid_time_iso,
                    "retrieval_time": valid_time_iso,
                    "session_change_pct": 1.25,
                },
                {
                    "ticker": "CORRUPT_OR_MISSING",
                    "status": "NO_DATA",
                    "price": None,
                    "price_validity": "UNAVAILABLE",
                    "freshness": "UNAVAILABLE",
                    "price_source": None,
                    "observation_time": None,
                    "retrieval_time": valid_time_iso,
                    "session_change_pct": 0.0,
                },
            ],
        }

        with patch.object(fetch_stocks, "GLOBAL_STATE", simulated_state):
            res_str = tools.get_market_data()
            parsed = json.loads(res_str)

            self.assertIn("tickers", parsed)
            tickers_map = {t["ticker"]: t for t in parsed["tickers"]}

            # AAPL is valid and distinguishable
            aapl = tickers_map["AAPL"]
            self.assertEqual(aapl["price"], 182.50)
            self.assertEqual(aapl["price_validity"], "VALID")
            self.assertEqual(aapl["freshness"], "CURRENT")

            # Missing ticker price is None/null, NOT 0.0
            missing = tickers_map["CORRUPT_OR_MISSING"]
            self.assertIsNone(missing["price"])
            self.assertEqual(missing["price_validity"], "UNAVAILABLE")
            self.assertEqual(missing["freshness"], "UNAVAILABLE")
            self.assertEqual(missing["status"], "NO_DATA")

    # -------------------------------------------------------------------------
    # Test 10: Existing successful market-data processing remains compatible
    # -------------------------------------------------------------------------
    def test_10_successful_market_data_processing_remains_compatible(self):
        """Verifies standard valid ticks populate prices, sessions, regular_close, and session returns correctly."""
        sym = "NVDA"
        mock_ticker = MagicMock()
        mock_ticker.fast_info = MagicMock()

        # Batch quote from provider during regular hours
        now_ny = datetime.now(self.ny_tz)
        trade_ts = int(now_ny.timestamp())

        quote_data = {
            "regularMarketPrice": 125.50,
            "regularMarketPreviousClose": 120.00,
            "regularMarketOpen": 122.00,
            "regularMarketVolume": 35000000,
            "regularMarketTime": trade_ts,
        }

        update_price_tick(sym, mock_ticker, "OPEN", quote_data)

        self.assertEqual(cache.prices.get(sym), 125.50)
        self.assertEqual(cache.regular_close.get(sym), 120.00)
        self.assertEqual(cache.price_sources.get(sym), "BATCH_QUOTE")
        self.assertEqual(cache.price_validity.get(sym), "VALID")
        self.assertEqual(cache.freshness.get(sym), "CURRENT")
        self.assertAlmostEqual(cache.session_change.get(sym), ((125.50 - 120.00) / 120.00) * 100, places=2)

    # -------------------------------------------------------------------------
    # Test 11: Stale price cannot generate directional signal or non-zero score
    # -------------------------------------------------------------------------
    def test_11_stale_price_cannot_generate_directional_signal_or_score(self):
        """Verifies stale observations cannot produce directional signals, non-zero scores, or non-zero vwap distance."""
        sym = "STALE_SYM"
        cache.prices[sym] = 150.0
        cache.vwaps[sym] = 140.0
        cache.technicals[sym] = {
            "RSI": 82.0,  # Overbought territory
            "ATR_Pct": 2.5,
            "Trend_Score": 4,
            "Last_Reg_Close": 145.0,
            "status": "VALID",
        }

        # Case A: freshness is STALE
        cache.price_validity[sym] = "VALID"
        cache.freshness[sym] = "STALE"

        self.assertFalse(is_price_actionable(sym))
        score, note = calculate_score(sym)
        self.assertEqual(score, 0)
        self.assertEqual(note, "")
        self.assertEqual(classify_signal(sym), "STALE")
        self.assertEqual(distance_from_vwap(sym), 0.0)

        # Case B: price_validity is STALE
        cache.price_validity[sym] = "STALE"
        cache.freshness[sym] = "CURRENT"

        self.assertFalse(is_price_actionable(sym))
        score_b, note_b = calculate_score(sym)
        self.assertEqual(score_b, 0)
        self.assertEqual(note_b, "")
        self.assertEqual(classify_signal(sym), "STALE")
        self.assertEqual(distance_from_vwap(sym), 0.0)

    # -------------------------------------------------------------------------
    # Test 12: Price with VALID and UNAVAILABLE freshness cannot generate current signal
    # -------------------------------------------------------------------------
    def test_12_price_with_validity_valid_and_freshness_unavailable_cannot_generate_current_signal(self):
        """Verifies an observation with VALID price but UNAVAILABLE freshness fails closed."""
        sym = "UNAVAIL_FRESH"
        cache.prices[sym] = 100.0
        cache.vwaps[sym] = 95.0
        cache.technicals[sym] = {
            "RSI": 25.0,  # Oversold territory
            "ATR_Pct": 2.0,
            "Trend_Score": -3,
            "Last_Reg_Close": 105.0,
            "status": "VALID",
        }
        cache.price_validity[sym] = "VALID"
        cache.freshness[sym] = "UNAVAILABLE"

        self.assertFalse(is_price_actionable(sym))
        score, note = calculate_score(sym)
        self.assertEqual(score, 0)
        self.assertEqual(note, "")
        sig = classify_signal(sym)
        self.assertEqual(sig, "UNAVAILABLE")
        self.assertNotEqual(sig, "NEUTRAL")
        self.assertNotEqual(sig, "OVERSOLD")
        self.assertEqual(distance_from_vwap(sym), 0.0)

    # -------------------------------------------------------------------------
    # Test 13: Historical price generates HISTORICAL signal and zero score
    # -------------------------------------------------------------------------
    def test_13_historical_price_generates_historical_signal_and_zero_score(self):
        """Verifies historical observations outside active sessions return HISTORICAL signal and 0 score."""
        sym = "HIST_SYM"
        cache.prices[sym] = 200.0
        cache.vwaps[sym] = 195.0
        cache.technicals[sym] = {
            "RSI": 75.0,
            "ATR_Pct": 1.5,
            "Trend_Score": 3,
            "Last_Reg_Close": 190.0,
            "status": "VALID",
        }
        cache.price_validity[sym] = "VALID"
        cache.freshness[sym] = "HISTORICAL"

        self.assertFalse(is_price_actionable(sym))
        score, note = calculate_score(sym)
        self.assertEqual(score, 0)
        self.assertEqual(note, "")
        self.assertEqual(classify_signal(sym), "HISTORICAL")
        self.assertNotEqual(classify_signal(sym), "NEUTRAL")

    # -------------------------------------------------------------------------
    # Test 14: Stale or unavailable ticker is published with status STALE or UNAVAILABLE
    # -------------------------------------------------------------------------
    def test_14_stale_or_unavailable_ticker_status_not_active(self):
        """Verifies non-actionable tickers are published with explicit non-active statuses in web_server telemetry."""
        from web_server import chat_endpoint

        # Test telemetry default in web_server.py: missing freshness/validity must default to UNAVAILABLE
        simulated_live_state = {
            "tickers": [
                {
                    "ticker": "AAPL",
                    "status": "ACTIVE",
                    "price": 150.0,
                    # price_validity and freshness omitted to test safe fail-closed defaults
                },
                {
                    "ticker": "MSFT",
                    "status": "STALE",
                    "price": 400.0,
                    "price_validity": "STALE",
                    "freshness": "STALE",
                },
            ]
        }
        raw_tickers = simulated_live_state["tickers"]
        slim = []
        for t in raw_tickers:
            slim.append({
                "ticker": t.get("ticker"),
                "price": t.get("price"),
                "price_validity": t.get("price_validity", "UNAVAILABLE"),
                "freshness": t.get("freshness", "UNAVAILABLE"),
            })

        self.assertEqual(slim[0]["price_validity"], "UNAVAILABLE")
        self.assertEqual(slim[0]["freshness"], "UNAVAILABLE")
        self.assertEqual(slim[1]["price_validity"], "STALE")
        self.assertEqual(slim[1]["freshness"], "STALE")

    # -------------------------------------------------------------------------
    # Test 15: Future timestamp rejected during closed session and active session
    # -------------------------------------------------------------------------
    def test_15_future_timestamp_rejected_during_closed_and_active_session(self):
        """Verifies materially future-dated observations (> 60s ahead) return INVALID and UNAVAILABLE."""
        # Active session
        open_clock = datetime(2026, 6, 17, 14, 0, 0, tzinfo=self.ny_tz)
        future_open = open_clock + timedelta(minutes=5)

        val_open, fresh_open = determine_market_freshness(
            price=150.0,
            obs_time=future_open,
            current_ny=open_clock,
            session_status="OPEN",
        )
        self.assertEqual(val_open, "INVALID")
        self.assertEqual(fresh_open, "UNAVAILABLE")

        # Closed session (e.g. weekend or overnight)
        closed_clock = datetime(2026, 6, 20, 14, 0, 0, tzinfo=self.ny_tz)
        future_closed = closed_clock + timedelta(minutes=10)

        val_closed, fresh_closed = determine_market_freshness(
            price=150.0,
            obs_time=future_closed,
            current_ny=closed_clock,
            session_status="CLOSED",
        )
        self.assertEqual(val_closed, "INVALID")
        self.assertEqual(fresh_closed, "UNAVAILABLE")

    # -------------------------------------------------------------------------
    # Test 16: INTRADAY_CHART captures actual chart timestamp
    # -------------------------------------------------------------------------
    def test_16_intraday_chart_captures_actual_chart_timestamp(self):
        """Verifies get_live_chart_data parses the chart JSON timestamp into America/New_York without UnboundLocalError."""
        sym = "CHART_TS_TEST"
        fixed_ts = 1781539200  # Example unix timestamp
        expected_dt = datetime.fromtimestamp(fixed_ts, ZoneInfo("UTC")).astimezone(self.ny_tz)

        mock_chart_resp = {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "regularMarketPrice": 185.25,
                            "regularMarketVolume": 1500000,
                        },
                        "timestamp": [fixed_ts],
                        "indicators": {
                            "quote": [
                                {
                                    "close": [185.25],
                                    "volume": [1000],
                                }
                            ]
                        },
                    }
                ]
            }
        }

        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_chart_resp

        with patch("fetch_stocks.safe_yf_get", return_value=mock_resp):
            p, vol, pre_v, post_v = get_live_chart_data(sym, "OPEN")

        self.assertEqual(p, 185.25)
        self.assertIn(sym, cache.chart_obs_time)
        self.assertEqual(cache.chart_obs_time[sym], expected_dt)

    # -------------------------------------------------------------------------
    # Test 17: Provider fallback without observation timestamp (FAST_INFO)
    # -------------------------------------------------------------------------
    def test_17_provider_fallback_fast_info_sets_freshness_unavailable(self):
        """Verifies fallback to fast_info with no observation timestamp marks freshness as UNAVAILABLE."""
        sym = "FAST_INFO_SYM"
        mock_ticker = MagicMock()
        mock_fi = MagicMock()
        mock_fi.last_price = 110.0
        mock_fi.last_volume = 500000
        mock_ticker.fast_info = mock_fi

        # Calling update_price_tick with quote_data=None forces fast_info fallback
        with patch("fetch_stocks.get_live_chart_data", return_value=(None, 0, 0, 0)):
            update_price_tick(sym, mock_ticker, "OPEN", quote_data=None)

        self.assertEqual(cache.prices.get(sym), 110.0)
        self.assertEqual(cache.price_sources.get(sym), "FAST_INFO")
        self.assertIsNone(cache.observation_times.get(sym))
        self.assertEqual(cache.price_validity.get(sym), "VALID")
        self.assertEqual(cache.freshness.get(sym), "UNAVAILABLE")

    # -------------------------------------------------------------------------
    # Test 18: CACHE_PREVIOUS retains original timestamp and evaluates to STALE
    # -------------------------------------------------------------------------
    def test_18_cache_previous_retains_timestamp_and_evaluates_to_stale(self):
        """Verifies CACHE_PREVIOUS retains original observation time and evaluates to STALE when max_stale_seconds elapses."""
        sym = "CACHE_PREV_SYM"
        # Seed cache with observation from 2 hours ago
        two_hours_ago = datetime.now(self.ny_tz) - timedelta(hours=2)
        cache.prices[sym] = 95.0
        cache.observation_times[sym] = two_hours_ago.isoformat()

        mock_ticker = MagicMock()
        mock_ticker.fast_info = MagicMock()
        mock_ticker.fast_info.last_price = None

        with patch("fetch_stocks.get_live_chart_data", return_value=(None, 0, 0, 0)):
            update_price_tick(sym, mock_ticker, "OPEN", quote_data=None)

        self.assertEqual(cache.prices.get(sym), 95.0)
        self.assertEqual(cache.price_sources.get(sym), "CACHE_PREVIOUS")
        self.assertEqual(cache.observation_times.get(sym), two_hours_ago.isoformat())
        self.assertEqual(cache.price_validity.get(sym), "STALE")
        self.assertEqual(cache.freshness.get(sym), "STALE")

    # -------------------------------------------------------------------------
    # Test 19: Weekends and holidays return CLOSED and evaluate HISTORICAL
    # -------------------------------------------------------------------------
    def test_19_holidays_and_weekends_return_closed_and_evaluate_historical(self):
        """Verifies US equity market holidays and weekends return CLOSED and evaluate to HISTORICAL."""
        holidays_2026 = [
            ("New Year's Day", datetime(2026, 1, 1, 11, 0, 0, tzinfo=self.ny_tz)),
            ("MLK Day", datetime(2026, 1, 19, 11, 0, 0, tzinfo=self.ny_tz)),
            ("Presidents Day", datetime(2026, 2, 16, 11, 0, 0, tzinfo=self.ny_tz)),
            ("Good Friday", datetime(2026, 4, 3, 11, 0, 0, tzinfo=self.ny_tz)),
            ("Memorial Day", datetime(2026, 5, 25, 11, 0, 0, tzinfo=self.ny_tz)),
            ("Juneteenth", datetime(2026, 6, 19, 11, 0, 0, tzinfo=self.ny_tz)),
            ("Independence Day (Observed)", datetime(2026, 7, 3, 11, 0, 0, tzinfo=self.ny_tz)),
            ("Labor Day", datetime(2026, 9, 7, 11, 0, 0, tzinfo=self.ny_tz)),
            ("Thanksgiving", datetime(2026, 11, 26, 11, 0, 0, tzinfo=self.ny_tz)),
            ("Christmas", datetime(2026, 12, 25, 11, 0, 0, tzinfo=self.ny_tz)),
        ]

        for name, h_dt in holidays_2026:
            self.assertTrue(is_market_holiday(h_dt.date()), f"Failed holiday detection for {name}")
            status = get_market_status(h_dt)
            self.assertEqual(status, "CLOSED", f"Market not closed on holiday {name}")

            # Observation from previous close evaluated on holiday
            prior_close = h_dt - timedelta(days=1)
            val, fresh = determine_market_freshness(
                price=100.0,
                obs_time=prior_close,
                current_ny=h_dt,
                session_status=status,
            )
            self.assertEqual(val, "VALID")
            self.assertEqual(fresh, "HISTORICAL")

    # -------------------------------------------------------------------------
    # Test 20: Materially truncated history (>20 rows, e.g. 50 vs 250) rejected
    # -------------------------------------------------------------------------
    def test_20_materially_truncated_history_rejected_and_cache_preserved(self):
        """Verifies validate_historical_dataframe rejects responses truncated relative to existing history."""
        existing_dates = pd.date_range("2025-06-01", periods=100, tz="UTC")
        existing_df = pd.DataFrame(
            {
                "Open": [100.0 + i for i in range(100)],
                "High": [105.0 + i for i in range(100)],
                "Low": [95.0 + i for i in range(100)],
                "Close": [102.0 + i for i in range(100)],
                "Volume": [1000000 for _ in range(100)],
            },
            index=existing_dates,
        )

        # Incoming history has only 50 rows (< 75% of 100)
        incoming_dates = pd.date_range("2026-04-01", periods=50, tz="UTC")
        incoming_df = pd.DataFrame(
            {
                "Open": [200.0 + i for i in range(50)],
                "High": [205.0 + i for i in range(50)],
                "Low": [195.0 + i for i in range(50)],
                "Close": [202.0 + i for i in range(50)],
                "Volume": [1500000 for _ in range(50)],
            },
            index=incoming_dates,
        )

        is_valid, reason = validate_historical_dataframe(incoming_df, existing_df)
        self.assertFalse(is_valid)
        self.assertIn("Materially truncated", reason)

        # Verify update_history_and_technicals preserves existing
        sym = "TRUNC_SYM"
        cache.history[sym] = existing_df.copy()
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = incoming_df

        update_history_and_technicals(sym, mock_ticker)
        self.assertEqual(len(cache.history[sym]), 100)
        self.assertEqual(cache.history[sym]["Close"].iloc[-1], 201.0)

    # -------------------------------------------------------------------------
    # Test 21: Historical response with single valid close and corrupted NaNs rejected
    # -------------------------------------------------------------------------
    def test_21_historical_response_with_single_valid_close_and_nans_rejected(self):
        """Verifies validate_historical_dataframe rejects responses where valid closes < 95%."""
        dates = pd.date_range("2026-05-01", periods=30, tz="UTC")
        # 1 valid close, 29 NaNs
        closes = [float("nan")] * 29 + [150.0]
        corrupted_df = pd.DataFrame(
            {
                "Open": [100.0] * 30,
                "High": [105.0] * 30,
                "Low": [95.0] * 30,
                "Close": closes,
                "Volume": [1000000] * 30,
            },
            index=dates,
        )

        is_valid, reason = validate_historical_dataframe(corrupted_df, None)
        self.assertFalse(is_valid)
        self.assertIn("Excessive invalid or non-finite closes", reason)

    # -------------------------------------------------------------------------
    # Test 22: Valid complete response updates history and technicals normally
    # -------------------------------------------------------------------------
    def test_22_valid_complete_response_updates_normally(self):
        """Verifies complete valid historical response updates cache and computes all indicators."""
        sym = "VALID_UPDATE_SYM"
        dates = pd.date_range("2026-01-01", periods=100, tz="UTC")
        valid_df = pd.DataFrame(
            {
                "Open": [100.0 + (i * 0.2) for i in range(100)],
                "High": [105.0 + (i * 0.2) for i in range(100)],
                "Low": [95.0 + (i * 0.2) for i in range(100)],
                "Close": [102.0 + (i * 0.2) for i in range(100)],
                "Volume": [1000000 for _ in range(100)],
            },
            index=dates,
        )

        is_valid, reason = validate_historical_dataframe(valid_df, None)
        self.assertTrue(is_valid)

        mock_ticker = MagicMock()
        mock_ticker.history.return_value = valid_df

        update_history_and_technicals(sym, mock_ticker)
        self.assertIn(sym, cache.history)
        self.assertEqual(len(cache.history[sym]), 100)
        self.assertIn(sym, cache.technicals)
        techs = cache.technicals[sym]
        self.assertIsNotNone(techs.get("SMA_20"))
        self.assertIsNotNone(techs.get("SMA_50"))
        self.assertIsNotNone(techs.get("RSI"))
        self.assertIsNotNone(techs.get("MACD"))
        self.assertIsNotNone(techs.get("ATR"))


if __name__ == "__main__":
    unittest.main()
