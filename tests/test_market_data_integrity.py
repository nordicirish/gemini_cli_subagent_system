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
from datetime import datetime, time
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


if __name__ == "__main__":
    unittest.main()
