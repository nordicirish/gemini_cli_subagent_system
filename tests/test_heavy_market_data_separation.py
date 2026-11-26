"""
Stage 2.2.3 — Heavy Market-Data Workload Separation Test Suite.
Validates complete isolation of historical OHLCV/technical acquisition and
options/GEX acquisition onto HEAVY_MARKET_DATA_COORDINATOR, independent of
the monitoring thread and MARKET_DATA_COORDINATOR live quote/VWAP pipelines.
"""

import contextlib
import math
import os
import sys
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "python")))

import background_intelligence as bi
import fetch_stocks as fs


NY_TZ = ZoneInfo("America/New_York")
UTC_TZ = timezone.utc


def _wait_until(predicate, timeout: float = 4.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def _wait_workers_idle(coord: bi.BackgroundIntelligenceCoordinator, timeout: float = 4.0) -> bool:
    return _wait_until(
        lambda: coord.pending_count() == 0 and coord.in_flight_count() == 0,
        timeout=timeout,
    )


def _make_sample_history_df(days: int = 220, base_price: float = 100.0, end_date: datetime | None = None) -> pd.DataFrame:
    end_dt = end_date or datetime(2026, 4, 20, 16, 0, tzinfo=NY_TZ)
    dates = pd.bdate_range(end=end_dt.date(), periods=days)
    closes = [base_price + i * 0.5 for i in range(days)]
    data = {
        "Open": [c - 0.3 for c in closes],
        "High": [c + 1.0 for c in closes],
        "Low": [c - 1.0 for c in closes],
        "Close": closes,
        "Volume": [1_000_000 + i * 1000 for i in range(days)],
    }
    return pd.DataFrame(data, index=dates)


def _make_option_chain_pair(spot: float = 100.0):
    calls = pd.DataFrame(
        [
            {"strike": spot, "openInterest": 500, "volume": 100, "impliedVolatility": 0.30},
            {"strike": spot + 5.0, "openInterest": 800, "volume": 150, "impliedVolatility": 0.28},
        ]
    )
    puts = pd.DataFrame(
        [
            {"strike": spot - 5.0, "openInterest": 400, "volume": 80, "impliedVolatility": 0.32},
            {"strike": spot, "openInterest": 300, "volume": 50, "impliedVolatility": 0.29},
        ]
    )
    return SimpleNamespace(calls=calls, puts=puts)


class _MutateAndRaiseDict(dict):
    """Test double whose first write to `fail_key` raises RuntimeError AFTER external cache mutation."""

    def __init__(self, fail_key: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail_key = fail_key

    def __setitem__(self, key, value):
        if key == self.fail_key:
            raise RuntimeError(f"Injected write failure on {key}")
        super().__setitem__(key, value)


@contextlib.contextmanager
def _isolated_all_coordinators(
    *,
    heavy_workers: int = 2,
    heavy_queue: int = 64,
    heavy_results: int = 128,
    heavy_cooldown=None,
    mkt_workers: int = 2,
):
    """Creates isolated heavy, live-market-data, and intelligence coordinators for hermetic tests."""
    heavy_coord = bi.BackgroundIntelligenceCoordinator(
        max_workers=heavy_workers,
        max_queue_size=heavy_queue,
        max_results_size=heavy_results,
        cooldown_tracker=heavy_cooldown,
        worker_name_prefix="HeavyMarketDataWorker",
    )
    heavy_app = bi.BackgroundResultApplicator(coordinator=heavy_coord)

    mkt_coord = bi.BackgroundIntelligenceCoordinator(
        max_workers=mkt_workers,
        max_queue_size=50,
        max_results_size=100,
        worker_name_prefix="LiveMarketDataWorker",
    )
    mkt_app = bi.BackgroundResultApplicator(coordinator=mkt_coord)

    bg_coord = bi.BackgroundIntelligenceCoordinator(
        max_workers=1,
        max_queue_size=20,
        max_results_size=20,
        worker_name_prefix="BackgroundIntelligenceWorker",
    )
    bg_app = bi.BackgroundResultApplicator(coordinator=bg_coord)

    try:
        with patch.object(fs, "HEAVY_MARKET_DATA_COORDINATOR", heavy_coord), \
             patch.object(fs, "HEAVY_MARKET_DATA_RESULT_APPLICATOR", heavy_app), \
             patch.object(fs, "MARKET_DATA_COORDINATOR", mkt_coord), \
             patch.object(fs, "MARKET_DATA_RESULT_APPLICATOR", mkt_app), \
             patch.object(fs, "BACKGROUND_COORDINATOR", bg_coord), \
             patch.object(fs, "BACKGROUND_RESULT_APPLICATOR", bg_app):
            yield heavy_coord, heavy_app, mkt_coord, mkt_app, bg_coord, bg_app
    finally:
        heavy_coord.shutdown(timeout=2.0)
        mkt_coord.shutdown(timeout=2.0)
        bg_coord.shutdown(timeout=2.0)


class _HeavyMarketDataTestBase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._original_all_tickers = list(fs.ALL_TICKERS)
        self.addCleanup(self._restore_all_tickers)
        fixture_symbols = [
            "AAPL", "MSFT", "AMD", "QQQ", "META", "IWM", "DIA"
        ]
        fs.ALL_TICKERS = list(dict.fromkeys(
            self._original_all_tickers + fixture_symbols
        ))
        fs.cache.clear()
        with fs._MARKET_DATA_FALLBACK_STATE_LOCK:
            fs._MARKET_DATA_FALLBACK_REQUESTS.clear()

    def _restore_all_tickers(self):
        fs.ALL_TICKERS = list(self._original_all_tickers)

    def tearDown(self):
        try:
            fs.ALL_TICKERS = list(self._original_all_tickers)
            fs.cache.clear()
            with fs._MARKET_DATA_FALLBACK_STATE_LOCK:
                fs._MARKET_DATA_FALLBACK_REQUESTS.clear()
        finally:
            super().tearDown()


class TestStage223CoordinatorIsolation(_HeavyMarketDataTestBase):
    def test_01_heavy_coordinator_and_applicator_are_distinct_and_bounded(self):
        heavy = fs.get_heavy_market_data_coordinator()
        heavy_app = fs.get_heavy_market_data_result_applicator()
        mkt = fs.get_market_data_coordinator()
        mkt_app = fs.get_market_data_result_applicator()
        bg = fs.get_background_coordinator()

        self.assertIsNot(heavy, mkt)
        self.assertIsNot(heavy, bg)
        self.assertIsNot(heavy_app, mkt_app)
        self.assertIs(heavy_app._coordinator, heavy)
        self.assertEqual(heavy.max_workers, 2)
        self.assertEqual(heavy.max_queue_size, 64)
        self.assertEqual(heavy.max_results_size, 128)
        self.assertEqual(heavy.worker_name_prefix, "HeavyMarketDataWorker")

    def test_02_work_type_classifications_exist(self):
        self.assertEqual(bi.WorkType.HISTORICAL_DATA.value, "historical_data")
        self.assertEqual(bi.WorkType.OPTIONS_GEX.value, "options_gex")

    def test_03_heavy_worker_per_key_dedup_and_bounded_backpressure(self):
        started = threading.Event()
        release = threading.Event()

        def _slow_raw():
            started.set()
            release.wait(timeout=3.0)
            return bi.ObservedResult(data={"kind": "historical_data"})

        coord = bi.BackgroundIntelligenceCoordinator(
            max_workers=1,
            max_queue_size=1,
            max_results_size=2,
            worker_name_prefix="HeavyMarketDataWorker",
        )
        try:
            acc1, k1 = coord.enqueue(bi.WorkType.HISTORICAL_DATA.value, "AAPL", fn=_slow_raw)
            self.assertTrue(acc1)
            self.assertTrue(started.wait(timeout=2.0))

            # Item 1 is in-flight; enqueue SPY fills max_queue_size=1; duplicate SPY coalesces; NVDA hits queue cap
            acc_spy, item_spy = coord.enqueue(bi.WorkType.HISTORICAL_DATA.value, "SPY", fn=_slow_raw)
            acc_spy_dup, item_spy_dup = coord.enqueue(bi.WorkType.HISTORICAL_DATA.value, "SPY", fn=_slow_raw)
            acc_nvda, k_nvda = coord.enqueue(bi.WorkType.HISTORICAL_DATA.value, "NVDA", fn=_slow_raw)

            self.assertTrue(acc_spy)
            self.assertTrue(acc_spy_dup)
            self.assertIs(item_spy_dup, item_spy)
            self.assertFalse(acc_nvda)
            self.assertIsNone(k_nvda)
            self.assertEqual(k1.key.work_type, bi.WorkType.HISTORICAL_DATA.value)
        finally:
            release.set()
            coord.shutdown(timeout=2.0)

    def test_04_blocked_heavy_workers_do_not_delay_live_market_data_or_monitoring_cycle(self):
        heavy_entered = threading.Event()
        heavy_release = threading.Event()
        heavy_threads = []

        def _blocked_hist(*args, **kwargs):
            heavy_threads.append(threading.current_thread().name)
            heavy_entered.set()
            heavy_release.wait(timeout=5.0)
            raise RuntimeError("heavy test released")

        now_ny = datetime(2026, 4, 20, 11, 0, tzinfo=NY_TZ)
        obs_epoch = int(now_ny.timestamp()) - 10
        obs_iso = datetime.fromtimestamp(obs_epoch, UTC_TZ).isoformat().replace("+00:00", "Z")

        with _isolated_all_coordinators(heavy_workers=2, mkt_workers=2) as (
            heavy_coord, _, mkt_coord, _, _, _
        ):
            fake_ticker = SimpleNamespace(
                ticker="NVDA",
                history=lambda **kw: _blocked_hist(),
                options=["2026-06-19"],
                option_chain=lambda exp: _blocked_hist(),
            )
            fs.cache.prices["NVDA"] = 150.0
            fs.cache.price_validity["NVDA"] = "VALID"
            fs.cache.freshness["NVDA"] = "CURRENT"
            fs.cache.observation_times["NVDA"] = "2026-04-20T11:00:00Z"
            fs.cache.retrieval_times["NVDA"] = "2026-04-20T11:00:01Z"

            try:
                # Saturate both heavy workers with blocked heavy work
                h_res = fs.coordinate_heavy_market_data_cycle(
                    ["NVDA"],
                    tickers_obj={"NVDA": fake_ticker},
                    is_heavy=True,
                    today_str="2026-04-20",
                    now_ny=now_ny,
                )
                self.assertEqual(h_res["historical_enqueued"], 1)
                self.assertEqual(h_res["gex_enqueued"], 1)
                self.assertTrue(heavy_entered.wait(timeout=2.0))

                # Live market-data batch & VWAP continue executing end-to-end with zero blocking
                fake_batch = {
                    "NVDA": {
                        "regularMarketPrice": 152.5,
                        "regularMarketTime": obs_epoch,
                        "regularMarketPreviousClose": 149.0,
                        "regularMarketOpen": 151.0,
                        "regularMarketDayHigh": 153.0,
                        "regularMarketDayLow": 150.5,
                        "regularMarketVolume": 5_000_000,
                    }
                }
                with patch.object(fs, "get_batch_quotes", return_value=fake_batch), \
                     patch.object(
                         fs,
                         "_acquire_true_intraday_vwap_bars",
                         return_value=([(151.75, 151.75, 151.75, 1000)], obs_iso, False),
                     ):
                    t0 = time.monotonic()
                    fs.coordinate_market_data_cycle(["NVDA"], "OPEN", ["NVDA"], now_ny=now_ny)
                    self.assertTrue(_wait_workers_idle(mkt_coord, timeout=2.0))
                    fs.coordinate_market_data_cycle(["NVDA"], "OPEN", ["NVDA"], now_ny=now_ny)
                    elapsed = time.monotonic() - t0

                self.assertLess(elapsed, 1.5)
                self.assertEqual(fs.cache.prices.get("NVDA"), 152.5)
                self.assertEqual(fs.cache.vwaps.get("NVDA"), 151.75)
                self.assertEqual(fs.cache.price_sources.get("NVDA"), "BATCH_QUOTE")
                self.assertEqual(
                    datetime.fromisoformat(fs.cache.observation_times.get("NVDA")).astimezone(UTC_TZ),
                    datetime.fromisoformat(obs_iso.replace("Z", "+00:00")),
                )
                self.assertTrue(all(name.startswith("HeavyMarketDataWorker") for name in heavy_threads))
            finally:
                heavy_release.set()
                _wait_workers_idle(heavy_coord, timeout=2.0)

    def test_05_heavy_publication_does_not_alter_live_quote_provenance_or_timestamps(self):
        now_ny = datetime(2026, 4, 20, 11, 0, tzinfo=NY_TZ)
        sample_df = _make_sample_history_df(days=220, end_date=now_ny)

        fs.cache.prices["AAPL"] = 200.0
        fs.cache.price_sources["AAPL"] = "YAHOO_BATCH_LIVE"
        fs.cache.observation_times["AAPL"] = "2026-04-20T15:00:00Z"
        fs.cache.retrieval_times["AAPL"] = "2026-04-20T15:00:01Z"
        fs.cache.price_validity["AAPL"] = "VALID"
        fs.cache.freshness["AAPL"] = "CURRENT"
        fs.cache.metric_provenance["QUOTE:AAPL"] = bi.MarketDataProvenance(
            kind=bi.ObservationKind.INSTANT,
            source="YAHOO_BATCH_LIVE",
        )

        class _Ticker:
            ticker = "AAPL"
            options = ["2026-06-19"]
            fast_info = SimpleNamespace(regular_market_previous_close=199.5, three_month_average_volume=50_000_000)

            def history(self, **kwargs):
                return sample_df.copy()

            def option_chain(self, exp):
                return _make_option_chain_pair(200.0)

        with _isolated_all_coordinators() as (heavy_coord, _, _, _, _, _):
            fs.coordinate_heavy_market_data_cycle(
                ["AAPL"],
                tickers_obj={"AAPL": _Ticker()},
                is_heavy=True,
                today_str="2026-04-20",
                now_ny=now_ny,
            )
            self.assertTrue(_wait_workers_idle(heavy_coord, timeout=3.0))
            stats = fs.coordinate_heavy_market_data_cycle(
                ["AAPL"],
                tickers_obj={"AAPL": _Ticker()},
                is_heavy=False,
                today_str="2026-04-20",
                now_ny=now_ny,
            )
            self.assertEqual(stats["historical_applied"], 1)
            self.assertEqual(stats["gex_applied"], 1)

        # Live quote provenance/timestamps untouched
        self.assertEqual(fs.cache.prices["AAPL"], 200.0)
        self.assertEqual(fs.cache.price_sources["AAPL"], "YAHOO_BATCH_LIVE")
        self.assertEqual(fs.cache.observation_times["AAPL"], "2026-04-20T15:00:00Z")
        self.assertEqual(fs.cache.retrieval_times["AAPL"], "2026-04-20T15:00:01Z")
        self.assertEqual(fs.cache.price_validity["AAPL"], "VALID")
        self.assertEqual(fs.cache.freshness["AAPL"], "CURRENT")
        self.assertEqual(fs.cache.metric_provenance["QUOTE:AAPL"].source, "YAHOO_BATCH_LIVE")

    def test_06_heavy_coordinator_deterministic_shutdown(self):
        coord = bi.BackgroundIntelligenceCoordinator(
            max_workers=2,
            max_queue_size=8,
            max_results_size=8,
            worker_name_prefix="HeavyMarketDataWorker",
        )
        threads_before = list(coord._workers)
        self.assertTrue(all(t.is_alive() for t in threads_before))
        coord.shutdown(timeout=2.0)
        self.assertTrue(all(not t.is_alive() for t in threads_before))
        accepted, _ = coord.enqueue(bi.WorkType.HISTORICAL_DATA.value, "MSFT", fn=lambda: bi.ObservedResult(data={}))
        self.assertFalse(accepted)


class TestStage223HistoricalAcquisition(_HeavyMarketDataTestBase):
    def test_07_historical_provider_calls_run_off_coordinating_thread_without_mutating_cache(self):
        coord_thread_id = threading.get_ident()
        provider_thread_ids = []
        cache_snapshot_during_worker = {}
        now_ny = datetime(2026, 4, 20, 11, 0, tzinfo=NY_TZ)
        sample_df = _make_sample_history_df(days=220, end_date=now_ny)

        class _Ticker:
            ticker = "MSFT"

            @property
            def fast_info(self):
                provider_thread_ids.append(("fast_info", threading.get_ident()))
                return SimpleNamespace(regular_market_previous_close=410.25)

            def history(self, **kwargs):
                provider_thread_ids.append(("history", threading.get_ident()))
                cache_snapshot_during_worker["history_keys"] = list(fs.cache.history.keys())
                cache_snapshot_during_worker["tech_keys"] = list(fs.cache.technicals.keys())
                cache_snapshot_during_worker["date_keys"] = list(fs.cache.last_history_fetch_date.keys())
                cache_snapshot_during_worker["reg_keys"] = list(fs.cache.regular_close.keys())
                return sample_df.copy()

        with _isolated_all_coordinators() as (heavy_coord, _, _, _, _, _):
            acc, item = fs.enqueue_historical_data("MSFT", t_obj=_Ticker(), today_str="2026-04-20", now_ny=now_ny)
            self.assertTrue(acc)
            self.assertEqual(item.key.work_type, bi.WorkType.HISTORICAL_DATA.value)
            self.assertTrue(_wait_workers_idle(heavy_coord, timeout=3.0))

            # Before coordinating-thread application, shared caches are still empty!
            self.assertEqual(cache_snapshot_during_worker["history_keys"], [])
            self.assertEqual(cache_snapshot_during_worker["tech_keys"], [])
            self.assertEqual(cache_snapshot_during_worker["date_keys"], [])
            self.assertEqual(cache_snapshot_during_worker["reg_keys"], [])
            self.assertNotIn("MSFT", fs.cache.history)
            self.assertNotIn("MSFT", fs.cache.technicals)

            self.assertTrue(all(tid != coord_thread_id for _, tid in provider_thread_ids))

            audit = fs.apply_background_heavy_market_data_results(now_ny=now_ny)
            self.assertEqual(len(audit), 1)
            self.assertTrue(audit[0][1])

        self.assertIn("MSFT", fs.cache.history)
        self.assertEqual(fs.cache.last_history_fetch_date["MSFT"], "2026-04-20")
        self.assertEqual(fs.cache.regular_close["MSFT"], 410.25)
        self.assertIn("RSI", fs.cache.technicals["MSFT"])
        self.assertIn("Trend_Score", fs.cache.technicals["MSFT"])

    def test_08_yahoo_to_polygon_fallback_and_previous_close_fallback_in_worker(self):
        coord_thread_id = threading.get_ident()
        call_threads = []
        now_ny = datetime(2026, 4, 20, 11, 0, tzinfo=NY_TZ)
        sample_df = _make_sample_history_df(days=210, base_price=50.0, end_date=now_ny)

        class _BrokenYahooTicker:
            ticker = "AMD"

            def history(self, **kwargs):
                call_threads.append(("yf_history", threading.get_ident()))
                raise RuntimeError("Yahoo timeout")

            @property
            def fast_info(self):
                call_threads.append(("yf_fast_info", threading.get_ident()))
                raise RuntimeError("fast_info unavailable")

        def _fake_polygon(sym):
            call_threads.append(("polygon_history", threading.get_ident()))
            return sample_df.copy()

        with _isolated_all_coordinators() as (heavy_coord, _, _, _, _, _), \
             patch.object(fs, "get_polygon_history_df", side_effect=_fake_polygon):
            acc, _ = fs.enqueue_historical_data("AMD", t_obj=_BrokenYahooTicker(), today_str="2026-04-20", now_ny=now_ny)
            self.assertTrue(acc)
            self.assertTrue(_wait_workers_idle(heavy_coord, timeout=3.0))
            audit = fs.apply_background_heavy_market_data_results(now_ny=now_ny)

        self.assertEqual(len(audit), 1)
        res, applied, _ = audit[0]
        self.assertTrue(applied)
        self.assertEqual(res.data["source"], "POLYGON_HISTORY")
        expected_prev_close = float(sample_df["Close"].iloc[-2])
        self.assertEqual(fs.cache.regular_close["AMD"], expected_prev_close)
        self.assertEqual(fs.cache.technicals["AMD"]["Last_Reg_Close"], expected_prev_close)
        self.assertTrue(any(tag == "polygon_history" for tag, _ in call_threads))
        self.assertTrue(any(tag == "yf_fast_info" for tag, _ in call_threads))
        self.assertTrue(all(tid != coord_thread_id for _, tid in call_threads))

        # Verify get_previous_close fallback executes off the coordinating thread when close bars/fast_info lack close
        prev_threads = []
        with patch.object(fs, "get_previous_close", side_effect=lambda sym: (prev_threads.append(threading.get_ident()), 123.45)[1]):
            worker_res = []
            t = threading.Thread(
                target=lambda: worker_res.append(
                    fs._extract_last_regular_close_for_history(
                        symbol="AMD",
                        hist=pd.DataFrame({"Close": [0.0]}),
                        close=pd.Series([0.0]),
                        t_obj=None,
                        now_ny=now_ny,
                    )
                )
            )
            t.start()
            t.join(timeout=2.0)
        self.assertEqual(worker_res, [123.45])
        self.assertTrue(all(tid != coord_thread_id for tid in prev_threads))

    def test_09_technical_calculations_and_premarket_regular_close_semantics_preserved(self):
        premarket_ny = datetime(2026, 4, 20, 8, 15, tzinfo=NY_TZ)
        sample_df = _make_sample_history_df(days=220, base_price=100.0, end_date=datetime(2026, 4, 17, 16, 0, tzinfo=NY_TZ))
        expected_last_close = float(sample_df["Close"].iloc[-1])

        class _Ticker:
            ticker = "QQQ"
            fast_info = SimpleNamespace(last_price=999.0, regular_market_previous_close=888.0)

            def history(self, **kwargs):
                return sample_df.copy()

        # Pre-market should prefer last valid bar in Close before fast_info.last_price
        with _isolated_all_coordinators() as (heavy_coord, _, _, _, _, _):
            fs.enqueue_historical_data("QQQ", t_obj=_Ticker(), today_str="2026-04-20", now_ny=premarket_ny)
            self.assertTrue(_wait_workers_idle(heavy_coord, timeout=3.0))
            audit = fs.apply_background_heavy_market_data_results(now_ny=premarket_ny)

        self.assertTrue(audit[0][1])
        tech = fs.cache.technicals["QQQ"]
        for req_key in fs._HISTORICAL_REQUIRED_TECH_KEYS:
            self.assertIn(req_key, tech)
        self.assertAlmostEqual(tech["Last_Reg_Close"], expected_last_close)
        self.assertEqual(fs.cache.regular_close["QQQ"], expected_last_close)
        # Rising series -> Trend_Score == 4, MACD bullish
        self.assertEqual(tech["Trend_Score"], 4)
        self.assertGreater(tech["SMA_20"], tech["SMA_50"])
        self.assertGreater(tech["SMA_50"], tech["SMA_200"])
        self.assertIsNotNone(tech["RSI"])
        self.assertIsNotNone(tech["MFI"])
        self.assertIsNotNone(tech["ATR"])
        self.assertIsNotNone(tech["ATR_Pct"])
        self.assertIsNotNone(tech["BB_Pct"])

    def test_10_daily_historical_refresh_deduplication_and_midnight_rollover(self):
        day1 = datetime(2026, 4, 20, 11, 0, tzinfo=NY_TZ)
        day2 = datetime(2026, 4, 21, 10, 0, tzinfo=NY_TZ)
        df_day1 = _make_sample_history_df(days=210, end_date=day1)
        df_day2 = _make_sample_history_df(days=211, end_date=day2)
        calls = []

        class _Ticker:
            ticker = "META"
            fast_info = SimpleNamespace(regular_market_previous_close=500.0)

            def history(self, **kwargs):
                calls.append(len(calls) + 1)
                return df_day1.copy() if len(calls) == 1 else df_day2.copy()

        with _isolated_all_coordinators() as (heavy_coord, _, _, _, _, _):
            acc1, _ = fs.enqueue_historical_data("META", t_obj=_Ticker(), today_str="2026-04-20", now_ny=day1)
            self.assertTrue(acc1)
            self.assertTrue(_wait_workers_idle(heavy_coord, timeout=3.0))
            fs.apply_background_heavy_market_data_results(now_ny=day1)

            # Same day: skipped!
            acc2, _ = fs.enqueue_historical_data("META", t_obj=_Ticker(), today_str="2026-04-20", now_ny=day1)
            self.assertFalse(acc2)
            self.assertEqual(len(calls), 1)

            # Next trading date: enqueued & refreshed!
            acc3, _ = fs.enqueue_historical_data("META", t_obj=_Ticker(), today_str="2026-04-21", now_ny=day2)
            self.assertTrue(acc3)
            self.assertTrue(_wait_workers_idle(heavy_coord, timeout=3.0))
            fs.apply_background_heavy_market_data_results(now_ny=day2)
            self.assertEqual(fs.cache.last_history_fetch_date["META"], "2026-04-21")
            self.assertEqual(len(calls), 2)

    def test_11_rejected_malformed_or_failed_historical_preserves_existing_cache(self):
        now_ny = datetime(2026, 4, 20, 11, 0, tzinfo=NY_TZ)
        good_df = _make_sample_history_df(days=50, base_price=120.0, end_date=now_ny)
        good_tech = fs._compute_technicals_from_history(good_df, last_reg_close=140.0)
        fs.cache.history["TSLA"] = good_df.copy()
        fs.cache.technicals["TSLA"] = dict(good_tech)
        fs.cache.last_history_fetch_date["TSLA"] = "2026-04-17"
        fs.cache.regular_close["TSLA"] = 140.0

        # Case A: Provider fails in worker (Yahoo + Polygon both fail) -> Failed WorkResult, cache preserved
        class _FailTicker:
            ticker = "TSLA"

            def history(self, **kwargs):
                return pd.DataFrame()

        with _isolated_all_coordinators() as (heavy_coord, heavy_app, _, _, _, _), \
             patch.object(fs, "get_polygon_history_df", return_value=pd.DataFrame()):
            acc, _ = fs.enqueue_historical_data("TSLA", t_obj=_FailTicker(), today_str="2026-04-20", now_ny=now_ny)
            self.assertTrue(acc)
            self.assertTrue(_wait_workers_idle(heavy_coord, timeout=3.0))
            audit = fs.apply_background_heavy_market_data_results(now_ny=now_ny)
            self.assertEqual(len(audit), 1)
            self.assertFalse(audit[0][1])

            # Case B: Malformed candidates injected directly at publication boundary -> rejected
            bad_cases = [
                # Future fetch date
                {
                    "kind": "historical_data",
                    "symbol": "TSLA",
                    "history": good_df.copy(),
                    "technicals": dict(good_tech),
                    "last_history_fetch_date": "2026-04-25",
                    "regular_close": 141.0,
                    "source": "YAHOO_HISTORY",
                },
                # Regressive history (< existing 50 rows / fewer bars)
                {
                    "kind": "historical_data",
                    "symbol": "TSLA",
                    "history": _make_sample_history_df(days=12, base_price=120.0, end_date=now_ny),
                    "technicals": dict(good_tech),
                    "last_history_fetch_date": "2026-04-20",
                    "regular_close": 141.0,
                    "source": "YAHOO_HISTORY",
                },
                # Missing technical keys
                {
                    "kind": "historical_data",
                    "symbol": "TSLA",
                    "history": good_df.copy(),
                    "technicals": {"RSI": 50.0},
                    "last_history_fetch_date": "2026-04-20",
                    "regular_close": 141.0,
                    "source": "YAHOO_HISTORY",
                },
                # UNAVAILABLE technicals
                {
                    "kind": "historical_data",
                    "symbol": "TSLA",
                    "history": good_df.copy(),
                    "technicals": fs._unavailable_technicals_dict(),
                    "last_history_fetch_date": "2026-04-20",
                    "regular_close": 141.0,
                    "source": "YAHOO_HISTORY",
                },
            ]
            for seq, payload in enumerate(bad_cases, start=10):
                wr = bi.WorkResult(
                    key=bi.WorkKey(bi.WorkType.HISTORICAL_DATA.value, "TSLA"),
                    sequence=seq,
                    data=payload,
                )
                heavy_coord._enqueue_result_handoff(wr)
                audit_bad = fs.apply_background_heavy_market_data_results(now_ny=now_ny)
                self.assertEqual(len(audit_bad), 1)
                self.assertFalse(audit_bad[0][1])

        # Prior valid cache completely preserved
        self.assertEqual(len(fs.cache.history["TSLA"]), 50)
        self.assertEqual(fs.cache.last_history_fetch_date["TSLA"], "2026-04-17")
        self.assertEqual(fs.cache.regular_close["TSLA"], 140.0)
        self.assertEqual(fs.cache.technicals["TSLA"]["RSI"], good_tech["RSI"])

    def test_12_detached_existing_history_prevents_in_place_worker_mutation_alias(self):
        now_ny = datetime(2026, 4, 20, 11, 0, tzinfo=NY_TZ)
        orig_df = _make_sample_history_df(days=30, base_price=80.0, end_date=now_ny)
        fs.cache.history["PLTR"] = orig_df
        seen_hist_ids = []

        def _raw_spy(symbol, t_obj=None, existing_hist=None, today_str=None, now_ny=None, cancel_check=None):
            seen_hist_ids.append(id(existing_hist))
            raise ValueError("abort before publish")

        with _isolated_all_coordinators() as (heavy_coord, _, _, _, _, _), \
             patch.object(fs, "_acquire_historical_and_technicals_raw", side_effect=_raw_spy):
            acc, _ = fs.enqueue_historical_data("PLTR", today_str="2026-04-20", now_ny=now_ny)
            self.assertTrue(acc)
            self.assertTrue(_wait_workers_idle(heavy_coord, timeout=3.0))

        self.assertEqual(len(seen_hist_ids), 1)
        self.assertNotEqual(seen_hist_ids[0], id(orig_df))

    def test_13_calculate_score_returns_zero_when_technicals_unavailable(self):
        fs.cache.prices["XYZ"] = 50.0
        fs.cache.price_validity["XYZ"] = "VALID"
        fs.cache.freshness["XYZ"] = "LIVE"
        fs.cache.technicals["XYZ"] = fs._unavailable_technicals_dict()
        self.assertEqual(fs.calculate_score("XYZ"), (0, ""))


class TestStage223OptionsGexAcquisition(_HeavyMarketDataTestBase):
    def test_14_options_gex_provider_calls_run_in_worker_off_coordinating_thread(self):
        coord_thread_id = threading.get_ident()
        call_threads = []
        cache_during_worker = {}

        fs.cache.prices["SPY"] = 500.0
        fs.cache.price_validity["SPY"] = "VALID"
        fs.cache.freshness["SPY"] = "CURRENT"
        fs.cache.observation_times["SPY"] = "2026-04-20T11:00:00Z"
        fs.cache.retrieval_times["SPY"] = "2026-04-20T11:00:01Z"

        class _GexTicker:
            ticker = "SPY"

            @property
            def options(self):
                call_threads.append(("options", threading.get_ident()))
                return ["2026-06-19"]

            def option_chain(self, exp):
                call_threads.append(("option_chain", threading.get_ident()))
                cache_during_worker["gex"] = dict(fs.cache.gex)
                cache_during_worker["last_gex_fetch"] = dict(fs.cache.last_gex_fetch)
                return _make_option_chain_pair(500.0)

            @property
            def fast_info(self):
                call_threads.append(("fast_info", threading.get_ident()))
                return SimpleNamespace(three_month_average_volume=40_000_000)

        with _isolated_all_coordinators() as (heavy_coord, _, _, _, _, _):
            acc, item = fs.enqueue_options_gex("SPY", ticker_obj=_GexTicker(), spot_price=500.0)
            self.assertTrue(acc)
            self.assertEqual(item.key.work_type, bi.WorkType.OPTIONS_GEX.value)
            self.assertTrue(_wait_workers_idle(heavy_coord, timeout=3.0))

            # Cache untouched until coordinating thread applies result
            self.assertEqual(cache_during_worker["gex"], {})
            self.assertEqual(cache_during_worker["last_gex_fetch"], {})
            self.assertNotIn("SPY", fs.cache.gex)
            self.assertNotIn("SPY", fs.cache.last_gex_fetch)
            self.assertTrue(all(tid != coord_thread_id for _, tid in call_threads))
            self.assertEqual({tag for tag, _ in call_threads}, {"options", "option_chain", "fast_info"})

            audit = fs.apply_background_heavy_market_data_results()
            self.assertEqual(len(audit), 1)
            self.assertTrue(audit[0][1])

        for req_key in fs._GEX_REQUIRED_KEYS:
            self.assertIn(req_key, fs.cache.gex["SPY"])
            self.assertTrue(math.isfinite(fs.cache.gex["SPY"][req_key]))
        self.assertEqual(fs.cache.gex["SPY"], fs.cache.gex_cache["SPY"])
        self.assertGreater(fs.cache.last_gex_fetch["SPY"], 0)
        self.assertNotEqual(fs.cache.gex["SPY"]["net_gex"], 0.0)

    def test_15_gex_1800_second_ttl_and_no_premature_timestamp_advance(self):
        chain_calls = 0

        fs.cache.prices["IWM"] = 200.0
        fs.cache.price_validity["IWM"] = "VALID"
        fs.cache.freshness["IWM"] = "CURRENT"
        fs.cache.observation_times["IWM"] = "2026-04-20T11:00:00Z"
        fs.cache.retrieval_times["IWM"] = "2026-04-20T11:00:01Z"

        class _GexTicker:
            ticker = "IWM"
            options = ["2026-06-19"]
            fast_info = SimpleNamespace(three_month_average_volume=30_000_000)

            def option_chain(self, exp):
                nonlocal chain_calls
                chain_calls += 1
                return _make_option_chain_pair(200.0)

        t0 = 1_700_000_000.0
        with _isolated_all_coordinators() as (heavy_coord, _, _, _, _, _):
            with patch.object(fs.t_time, "time", return_value=t0):
                acc1, _ = fs.enqueue_options_gex("IWM", ticker_obj=_GexTicker(), spot_price=200.0, now_ts=t0)
                self.assertTrue(acc1)
                # Timestamp NOT advanced before publication!
                self.assertNotIn("IWM", fs.cache.last_gex_fetch)
                self.assertTrue(_wait_workers_idle(heavy_coord, timeout=3.0))
                audit1 = fs.apply_background_heavy_market_data_results(now_ts=t0)
                self.assertTrue(audit1[0][1])

            # Within 1,800s TTL -> skipped without provider calls
            fs.cache.gex.pop("IWM", None)  # Also verifies gex is restored from gex_cache
            acc2, _ = fs.enqueue_options_gex("IWM", ticker_obj=_GexTicker(), spot_price=200.0, now_ts=t0 + 1799.0)
            self.assertFalse(acc2)
            self.assertEqual(chain_calls, 1)
            self.assertIn("IWM", fs.cache.gex)

            # At 1,800s -> re-enqueued and refreshed
            with patch.object(fs.t_time, "time", return_value=t0 + 1800.0):
                acc3, _ = fs.enqueue_options_gex("IWM", ticker_obj=_GexTicker(), spot_price=200.0, now_ts=t0 + 1800.0)
                self.assertTrue(acc3)
                self.assertTrue(_wait_workers_idle(heavy_coord, timeout=3.0))
                audit3 = fs.apply_background_heavy_market_data_results(now_ts=t0 + 1800.0)
                self.assertTrue(audit3[0][1])
                self.assertEqual(chain_calls, 2)

    def test_16_failed_missing_or_malformed_gex_never_overwrites_valid_cache_or_fabricates_zeros(self):
        prior_profile = {
            "net_gex": 12.5,
            "flip_price": 498.0,
            "inventory_velocity_delta": 100.0,
            "gex_slope": 0.4,
            "flip_proximity_percent": 0.4,
            "strike_oi_magnet": 505.0,
        }
        prior_ts = 1_700_000_000.0
        fs.cache.gex["QQQ"] = dict(prior_profile)
        fs.cache.gex_cache["QQQ"] = dict(prior_profile)
        fs.cache.last_gex_fetch["QQQ"] = prior_ts

        # Missing expirations & empty chains
        no_exp_ticker = SimpleNamespace(ticker="QQQ", options=[])
        empty_chain_ticker = SimpleNamespace(
            ticker="QQQ",
            options=["2026-06-19"],
            option_chain=lambda exp: SimpleNamespace(calls=pd.DataFrame(), puts=pd.DataFrame()),
        )
        exception_ticker = SimpleNamespace(
            ticker="QQQ",
            options=["2026-06-19"],
            option_chain=lambda exp: (_ for _ in ()).throw(RuntimeError("options rate limit")),
        )

        with _isolated_all_coordinators() as (heavy_coord, _, _, _, _, _):
            # Missing / zero spot -> refuses enqueue, preserves cache
            acc_zero_spot, _ = fs.enqueue_options_gex("QQQ", ticker_obj=no_exp_ticker, spot_price=0.0, force=True)
            self.assertFalse(acc_zero_spot)
            self.assertEqual(fs.cache.gex["QQQ"], prior_profile)

            for idx, bad_ticker in enumerate((no_exp_ticker, empty_chain_ticker, exception_ticker)):
                sym_i = f"BAD{idx}"
                fs.cache.prices[sym_i] = 500.0
                fs.cache.price_validity[sym_i] = "VALID"
                fs.cache.freshness[sym_i] = "CURRENT"
                fs.cache.observation_times[sym_i] = "2026-04-20T11:00:00Z"
                fs.cache.retrieval_times[sym_i] = "2026-04-20T11:00:01Z"
                fs.cache.gex[sym_i] = dict(prior_profile)
                fs.cache.gex_cache[sym_i] = dict(prior_profile)
                fs.cache.last_gex_fetch[sym_i] = prior_ts
                bad_ticker_i = SimpleNamespace(
                    ticker=sym_i,
                    options=getattr(bad_ticker, "options", []),
                    option_chain=getattr(bad_ticker, "option_chain", None),
                )
                acc, _ = fs.enqueue_options_gex(sym_i, ticker_obj=bad_ticker_i, spot_price=500.0, force=True)
                self.assertTrue(acc)
                self.assertTrue(_wait_workers_idle(heavy_coord, timeout=3.0))
                audit = fs.apply_background_heavy_market_data_results()
                self.assertEqual(len(audit), 1)
                self.assertFalse(audit[0][1])
                self.assertEqual(fs.cache.gex[sym_i], prior_profile)
                self.assertEqual(fs.cache.gex_cache[sym_i], prior_profile)
                self.assertEqual(fs.cache.last_gex_fetch[sym_i], prior_ts)

            # Malformed publication candidates (NaN, missing key, stale fetched_at, contract_count=0)
            now_ts = time.time()
            fs.cache.prices["QQQ"] = 500.0
            fs.cache.price_validity["QQQ"] = "VALID"
            fs.cache.freshness["QQQ"] = "CURRENT"
            fs.cache.observation_times["QQQ"] = "2026-04-20T11:00:00Z"
            fs.cache.retrieval_times["QQQ"] = "2026-04-20T11:00:01Z"
            qqq_snapshot = {
                "price": 500.0,
                "observation_time": "2026-04-20T11:00:00Z",
                "retrieval_time": "2026-04-20T11:00:01Z",
            }
            bad_payloads = [
                {
                    "kind": "options_gex",
                    "symbol": "QQQ",
                    "spot_price": 500.0,
                    "spot_snapshot": dict(qqq_snapshot),
                    "gex_profile": {**prior_profile, "net_gex": float("nan")},
                    "fetched_at": now_ts,
                    "source": "YAHOO_OPTIONS",
                    "contract_count": 4,
                },
                {
                    "kind": "options_gex",
                    "symbol": "QQQ",
                    "spot_price": 500.0,
                    "spot_snapshot": dict(qqq_snapshot),
                    "gex_profile": {"net_gex": 1.0},
                    "fetched_at": now_ts,
                    "source": "YAHOO_OPTIONS",
                    "contract_count": 4,
                },
                {
                    "kind": "options_gex",
                    "symbol": "QQQ",
                    "spot_price": 500.0,
                    "spot_snapshot": dict(qqq_snapshot),
                    "gex_profile": dict(prior_profile),
                    "fetched_at": now_ts - 2500.0,
                    "source": "YAHOO_OPTIONS",
                    "contract_count": 4,
                },
                {
                    "kind": "options_gex",
                    "symbol": "QQQ",
                    "spot_price": 500.0,
                    "spot_snapshot": dict(qqq_snapshot),
                    "gex_profile": dict(prior_profile),
                    "fetched_at": now_ts,
                    "source": "YAHOO_OPTIONS",
                    "contract_count": 0,
                },
            ]
            for seq, bad in enumerate(bad_payloads, start=20):
                heavy_coord._enqueue_result_handoff(
                    bi.WorkResult(
                        key=bi.WorkKey(bi.WorkType.OPTIONS_GEX.value, "QQQ"),
                        sequence=seq,
                        data=bad,
                    )
                )
                audit_bad = fs.apply_background_heavy_market_data_results(now_ts=now_ts)
                self.assertFalse(audit_bad[0][1])

        self.assertEqual(fs.cache.gex["QQQ"], prior_profile)
        self.assertEqual(fs.cache.gex_cache["QQQ"], prior_profile)
        self.assertEqual(fs.cache.last_gex_fetch["QQQ"], prior_ts)


class TestStage223LifecycleAtomicityAndDaemonIntegration(_HeavyMarketDataTestBase):
    def test_17_stale_sequence_and_failed_publish_do_not_advance_applicator_state(self):
        now_ny = datetime(2026, 4, 20, 11, 0, tzinfo=NY_TZ)
        sample_df = _make_sample_history_df(days=40, end_date=now_ny)
        tech = fs._compute_technicals_from_history(sample_df, last_reg_close=110.0)
        key = bi.WorkKey(bi.WorkType.HISTORICAL_DATA.value, "DIA")

        coord = bi.BackgroundIntelligenceCoordinator(max_workers=1, max_queue_size=8, max_results_size=8)
        app = bi.BackgroundResultApplicator(coordinator=coord)
        try:
            # First publish sequence 5 fails in callback -> last_seq must remain unadvanced
            bad_res = bi.WorkResult(
                key=key,
                sequence=5,
                data={
                    "kind": "historical_data",
                    "symbol": "DIA",
                    "history": pd.DataFrame(),  # invalid empty DF rejected at publication boundary
                    "technicals": tech,
                    "last_history_fetch_date": "2026-04-20",
                    "regular_close": 110.0,
                    "source": "YAHOO_HISTORY",
                },
            )
            audit_fail = app.apply_results(
                results=[bad_res],
                on_publish=lambda r: fs._publish_market_data_candidate(*fs._build_historical_cache_candidate(r, now_ny=now_ny)),
            )
            self.assertFalse(audit_fail[0][1])
            self.assertEqual(app.get_last_applied(key), (-1, ""))

            # Sequence 4 (< 5) still succeeds because sequence 5 failed!
            good_res_4 = bi.WorkResult(
                key=key,
                sequence=4,
                data={
                    "kind": "historical_data",
                    "symbol": "DIA",
                    "history": sample_df.copy(),
                    "technicals": tech,
                    "last_history_fetch_date": "2026-04-20",
                    "regular_close": 110.0,
                    "source": "YAHOO_HISTORY",
                },
            )
            audit_ok = app.apply_results(
                results=[good_res_4],
                on_publish=lambda r: fs._publish_market_data_candidate(*fs._build_historical_cache_candidate(r, now_ny=now_ny)),
            )
            self.assertTrue(audit_ok[0][1])
            self.assertEqual(app.get_last_applied(key)[0], 4)

            # Sequence 3 (< 4) rejected as stale!
            stale_res_3 = bi.WorkResult(key=key, sequence=3, data=dict(good_res_4.data))
            audit_stale = app.apply_results(
                results=[stale_res_3],
                on_publish=lambda r: fs._publish_market_data_candidate(*fs._build_historical_cache_candidate(r, now_ny=now_ny)),
            )
            self.assertFalse(audit_stale[0][1])
            self.assertIn("Stale", audit_stale[0][2])
        finally:
            coord.shutdown(timeout=2.0)

    def test_18_cancelled_queued_heavy_item_does_not_overwrite_newer_valid_result(self):
        entered = threading.Event()
        release = threading.Event()

        def _blocking_job():
            entered.set()
            release.wait(timeout=3.0)
            return bi.ObservedResult(data={"kind": "historical_data", "v": 1})

        coord = bi.BackgroundIntelligenceCoordinator(
            max_workers=1,
            max_queue_size=8,
            max_results_size=8,
            worker_name_prefix="HeavyMarketDataWorker",
        )
        try:
            # Occupy the single worker
            coord.enqueue(bi.WorkType.HISTORICAL_DATA.value, "BUSY", fn=_blocking_job)
            self.assertTrue(entered.wait(timeout=2.0))

            # Queue item for AAPL then cancel it cleanly
            acc1, item_aapl = coord.enqueue(bi.WorkType.HISTORICAL_DATA.value, "AAPL", fn=lambda: bi.ObservedResult(data={"v": "old"}))
            self.assertTrue(acc1)
            self.assertTrue(coord.cancel_work(item_aapl.key))

            # Re-enqueue fresh item for AAPL while old one was cancelled
            acc2, _ = coord.enqueue(bi.WorkType.HISTORICAL_DATA.value, "AAPL", fn=lambda: bi.ObservedResult(data={"v": "new"}))
            self.assertTrue(acc2)
            release.set()
            self.assertTrue(_wait_workers_idle(coord, timeout=3.0))

            results = [r for r in coord.drain_results() if r.key == item_aapl.key]
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].data["v"], "new")
        finally:
            release.set()
            coord.shutdown(timeout=2.0)

    def test_19_atomic_rollback_on_partial_historical_and_gex_publication_failure(self):
        now_ny = datetime(2026, 4, 20, 11, 0, tzinfo=NY_TZ)
        old_df = _make_sample_history_df(days=40, base_price=50.0, end_date=now_ny)
        old_tech = fs._compute_technicals_from_history(old_df, last_reg_close=69.5)
        fs.cache.history["AMZN"] = old_df.copy()
        fs.cache.technicals["AMZN"] = dict(old_tech)
        fs.cache.last_history_fetch_date["AMZN"] = "2026-04-17"
        fs.cache.regular_close["AMZN"] = 69.5

        new_df = _make_sample_history_df(days=45, base_price=150.0, end_date=now_ny)
        new_tech = fs._compute_technicals_from_history(new_df, last_reg_close=172.0)

        # Fail AFTER cache.history["AMZN"] is written, when cache.technicals["AMZN"] is assigned
        with patch.object(fs.cache, "technicals", _MutateAndRaiseDict("AMZN", fs.cache.technicals)):
            wr = bi.WorkResult(
                key=bi.WorkKey(bi.WorkType.HISTORICAL_DATA.value, "AMZN"),
                sequence=1,
                data={
                    "kind": "historical_data",
                    "symbol": "AMZN",
                    "history": new_df,
                    "technicals": new_tech,
                    "last_history_fetch_date": "2026-04-20",
                    "regular_close": 172.0,
                    "source": "YAHOO_HISTORY",
                },
            )
            with self.assertRaises(RuntimeError):
                fs._publish_market_data_candidate(*fs._build_historical_cache_candidate(wr, now_ny=now_ny))

        # Both history and other fields must be rolled back to prior valid values
        self.assertEqual(len(fs.cache.history["AMZN"]), 40)
        self.assertEqual(fs.cache.last_history_fetch_date["AMZN"], "2026-04-17")
        self.assertEqual(fs.cache.regular_close["AMZN"], 69.5)

        # Now test atomic rollback on GEX publication (fail on last_gex_fetch after gex & gex_cache written)
        fs.cache.prices["AMZN"] = 175.0
        fs.cache.price_validity["AMZN"] = "VALID"
        fs.cache.freshness["AMZN"] = "CURRENT"
        fs.cache.observation_times["AMZN"] = "2026-04-20T11:00:00Z"
        fs.cache.retrieval_times["AMZN"] = "2026-04-20T11:00:01Z"

        old_gex = {k: float(idx + 1) for idx, k in enumerate(fs._GEX_REQUIRED_KEYS)}
        fs.cache.gex["AMZN"] = dict(old_gex)
        fs.cache.gex_cache["AMZN"] = dict(old_gex)
        fs.cache.last_gex_fetch["AMZN"] = 1_700_000_000.0

        new_gex = {k: float((idx + 1) * 10) for idx, k in enumerate(fs._GEX_REQUIRED_KEYS)}
        now_ts = time.time()
        with patch.object(fs.cache, "last_gex_fetch", _MutateAndRaiseDict("AMZN", fs.cache.last_gex_fetch)):
            wr_gex = bi.WorkResult(
                key=bi.WorkKey(bi.WorkType.OPTIONS_GEX.value, "AMZN"),
                sequence=1,
                data={
                    "kind": "options_gex",
                    "symbol": "AMZN",
                    "spot_price": 175.0,
                    "spot_snapshot": {
                        "price": 175.0,
                        "observation_time": "2026-04-20T11:00:00Z",
                        "retrieval_time": "2026-04-20T11:00:01Z",
                    },
                    "gex_profile": new_gex,
                    "fetched_at": now_ts,
                    "source": "YAHOO_OPTIONS",
                    "contract_count": 10,
                },
            )
            with self.assertRaises(RuntimeError):
                fs._publish_market_data_candidate(*fs._build_gex_cache_candidate(wr_gex, now_ts=now_ts))

        self.assertEqual(fs.cache.gex["AMZN"], old_gex)
        self.assertEqual(fs.cache.gex_cache["AMZN"], old_gex)
        self.assertEqual(fs.cache.last_gex_fetch["AMZN"], 1_700_000_000.0)

    def test_20_run_daemon_startup_and_loop_never_block_on_heavy_providers_and_save_only_on_apply(self):
        class _StopLoop(BaseException):
            pass

        blocked_provider = threading.Event()
        sleep_calls = []

        def _fake_sleep(secs):
            sleep_calls.append(secs)
            # Abort after first monitoring iteration's final refresh sleep
            if secs == fs.REFRESH_RATE_SECONDS or len(sleep_calls) >= 2:
                blocked_provider.set()
                raise _StopLoop()

        sync_calls = []
        save_calls = []
        heavy_cycle_calls = []

        class _BlockedYfTicker:
            def __init__(self, sym):
                self.ticker = sym
                self.options = ["2026-06-19"]

            def history(self, **kwargs):
                blocked_provider.wait(timeout=4.0)
                return pd.DataFrame()

            def option_chain(self, exp):
                blocked_provider.wait(timeout=4.0)
                raise RuntimeError("blocked")

        orig_coord_heavy = fs.coordinate_heavy_market_data_cycle

        def _spy_coord_heavy(*args, **kwargs):
            heavy_cycle_calls.append(kwargs.get("is_heavy"))
            return orig_coord_heavy(*args, **kwargs)

        with _isolated_all_coordinators() as (heavy_coord, _, mkt_coord, _, bg_coord, _):
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(fs, "ALL_TICKERS", ["AAPL"]))
                stack.enter_context(patch.object(fs, "TICKERS", ["AAPL"]))
                stack.enter_context(patch.object(fs, "MACRO_TICKERS", []))
                stack.enter_context(patch.object(fs, "_load_ssot_tickers", return_value=["AAPL"]))
                stack.enter_context(patch.object(fs, "_load_macro_tickers", return_value=[]))
                stack.enter_context(patch.object(fs.yf, "Ticker", side_effect=_BlockedYfTicker))
                stack.enter_context(patch.object(fs, "get_market_status", return_value="OPEN"))
                stack.enter_context(patch.object(fs, "resolve_held_and_watched", return_value=(set(), None)))
                stack.enter_context(patch.object(fs, "get_macro_telemetry_context", return_value=(18.0, "NORMAL", 0.0)))
                stack.enter_context(patch.object(fs, "enqueue_news_grounding"))
                stack.enter_context(patch.object(fs, "apply_background_intelligence_results"))
                stack.enter_context(patch.object(fs, "coordinate_market_data_cycle", return_value={"applied_count": 0}))
                stack.enter_context(patch.object(fs, "coordinate_heavy_market_data_cycle", side_effect=_spy_coord_heavy))
                stack.enter_context(patch.object(fs, "update_history_and_technicals", side_effect=lambda *a, **k: sync_calls.append("hist")))
                stack.enter_context(patch.object(fs, "get_gex_profile", side_effect=lambda *a, **k: sync_calls.append("gex")))
                stack.enter_context(patch.object(fs, "get_batch_quotes", side_effect=lambda *a, **k: sync_calls.append("batch")))
                stack.enter_context(patch.object(fs.cache, "save_to_disk", side_effect=lambda *a, **k: save_calls.append(threading.get_ident())))
                stack.enter_context(patch.object(fs, "build_daemon_final_output", return_value={"tickers": []}))
                stack.enter_context(patch.object(fs, "update_global_state_snapshot"))
                stack.enter_context(patch.object(fs, "get_global_state_snapshot", return_value={}))
                stack.enter_context(patch.object(fs.t_time, "sleep", side_effect=_fake_sleep))
                t0 = time.monotonic()
                try:
                    fs.run_daemon()
                except _StopLoop:
                    pass
                elapsed = time.monotonic() - t0
            blocked_provider.set()
            _wait_workers_idle(heavy_coord, timeout=2.0)

        self.assertLess(elapsed, 2.0)
        self.assertEqual(sync_calls, [])
        # Startup (is_heavy=True) + first loop iteration (is_heavy=False)
        self.assertEqual(heavy_cycle_calls, [True, False])
        # Zero historical applied yet -> save_to_disk not triggered in worker or un-applied cycle
        self.assertEqual(save_calls, [])
        # No 0.5s or 5.0s or 0.2s heavy blocking sleeps
        self.assertNotIn(5, sleep_calls)
        self.assertNotIn(0.2, sleep_calls)
        self.assertNotIn(0.5, sleep_calls)


class TestStage223Finding1GexCalculationIntegrity(_HeavyMarketDataTestBase):
    def test_f1_01_valid_mathematical_zero_remains_valid_zero(self):
        # Balanced option inventory where calls and puts cancel exactly
        inv = [
            {"K": 100.0, "T": 0.1, "sigma": 0.20, "oi": 100.0, "type": "call"},
            {"K": 100.0, "T": 0.1, "sigma": 0.20, "oi": 100.0, "type": "put"},
        ]
        strike_map = {100.0: 200.0}
        profile = fs._compute_gex_profile_from_inventory(
            symbol="SPY",
            spot_price=100.0,
            option_inventory=inv,
            strike_oi_map=strike_map,
            avg_vol=1_000_000,
        )
        self.assertIsInstance(profile["net_gex"], float)
        self.assertEqual(profile["net_gex"], 0.0)
        self.assertTrue(math.isfinite(profile["net_gex"]))
        for k in fs._GEX_REQUIRED_KEYS:
            self.assertIn(k, profile)
            self.assertTrue(math.isfinite(profile[k]))

    def test_f1_02_nan_in_calculated_output_raises_value_error(self):
        inv = [{"K": 100.0, "T": 0.1, "sigma": 0.20, "oi": 100.0, "type": "call"}]
        with patch.object(fs, "calculate_gamma", return_value=float("nan")):
            with self.assertRaises(ValueError) as cm:
                fs._compute_gex_profile_from_inventory(
                    symbol="SPY",
                    spot_price=100.0,
                    option_inventory=inv,
                    strike_oi_map={100.0: 100.0},
                    avg_vol=1_000_000,
                )
            self.assertIn("Non-finite GEX calculation result", str(cm.exception))

    def test_f1_03_positive_or_negative_infinity_raises_value_error(self):
        inv = [{"K": 100.0, "T": 0.1, "sigma": 0.20, "oi": 100.0, "type": "call"}]
        with patch.object(fs, "calculate_gamma", return_value=float("inf")):
            with self.assertRaises(ValueError) as cm_pos:
                fs._compute_gex_profile_from_inventory(
                    symbol="SPY",
                    spot_price=100.0,
                    option_inventory=inv,
                    strike_oi_map={100.0: 100.0},
                    avg_vol=1_000_000,
                )
            self.assertIn("Non-finite GEX calculation result", str(cm_pos.exception))

        with patch.object(fs, "calculate_gamma", return_value=float("-inf")):
            with self.assertRaises(ValueError) as cm_neg:
                fs._compute_gex_profile_from_inventory(
                    symbol="SPY",
                    spot_price=100.0,
                    option_inventory=inv,
                    strike_oi_map={100.0: 100.0},
                    avg_vol=1_000_000,
                )
            self.assertIn("Non-finite GEX calculation result", str(cm_neg.exception))

    def test_f1_04_unconvertible_or_boolean_output_raises_value_error(self):
        inv = [{"K": 100.0, "T": 0.1, "sigma": 0.20, "oi": 100.0, "type": "call"}]
        with self.assertRaises(ValueError) as cm_str:
            fs._compute_gex_profile_from_inventory(
                symbol="SPY",
                spot_price=100.0,
                option_inventory=inv,
                strike_oi_map={"unconvertible_string": 100.0},
                avg_vol=1_000_000,
            )
        self.assertIn("Invalid GEX calculation result", str(cm_str.exception))

        with self.assertRaises(ValueError) as cm_bool:
            fs._compute_gex_profile_from_inventory(
                symbol="SPY",
                spot_price=100.0,
                option_inventory=inv,
                strike_oi_map={True: 100.0},
                avg_vol=1_000_000,
            )
        self.assertIn("Invalid boolean GEX result", str(cm_bool.exception))

    def test_f1_05_failed_gex_calculation_recorded_unsuccessful_by_coordinator_and_preserves_cache(self):
        prior_profile = {k: 55.5 for k in fs._GEX_REQUIRED_KEYS}
        prior_ts = 1_700_000_000.0
        fs.cache.prices["AAPL"] = 150.0
        fs.cache.price_validity["AAPL"] = "VALID"
        fs.cache.freshness["AAPL"] = "CURRENT"
        fs.cache.observation_times["AAPL"] = "2026-04-20T11:00:00Z"
        fs.cache.retrieval_times["AAPL"] = "2026-04-20T11:00:01Z"

        fs.cache.gex["AAPL"] = dict(prior_profile)
        fs.cache.gex_cache["AAPL"] = dict(prior_profile)
        fs.cache.last_gex_fetch["AAPL"] = prior_ts

        class _Ticker:
            ticker = "AAPL"
            options = ["2026-06-19"]
            fast_info = SimpleNamespace(three_month_average_volume=40_000_000)

            def option_chain(self, exp):
                return _make_option_chain_pair(150.0)

        with _isolated_all_coordinators() as (heavy_coord, _, _, _, _, _):
            with patch.object(fs, "_compute_gex_profile_from_inventory", side_effect=ValueError("Non-finite GEX calculation result for net_gex: nan")):
                acc, _ = fs.enqueue_options_gex("AAPL", ticker_obj=_Ticker(), spot_price=150.0, force=True)
                self.assertTrue(acc)
                self.assertTrue(_wait_workers_idle(heavy_coord, timeout=3.0))

                audit = fs.apply_background_heavy_market_data_results()
                self.assertEqual(len(audit), 1)
                self.assertFalse(audit[0][1])
                self.assertIn("Worker error", audit[0][2])

        # Cache remains completely untouched
        self.assertEqual(fs.cache.gex["AAPL"], prior_profile)
        self.assertEqual(fs.cache.gex_cache["AAPL"], prior_profile)
        self.assertEqual(fs.cache.last_gex_fetch["AAPL"], prior_ts)


class TestStage223Finding2GexSpotFreshnessAndPublicationBoundary(_HeavyMarketDataTestBase):
    def _seed_valid_spot_cache(
        self,
        symbol="SPY",
        price=500.0,
        obs_time="2026-04-20T11:00:00Z",
        retrieval_time="2026-04-20T11:00:01Z",
        validity="VALID",
        freshness="CURRENT",
    ):
        fs.cache.prices[symbol] = price
        fs.cache.price_validity[symbol] = validity
        fs.cache.freshness[symbol] = freshness
        fs.cache.observation_times[symbol] = obs_time
        fs.cache.retrieval_times[symbol] = retrieval_time

    def _make_valid_work_result(
        self,
        symbol="SPY",
        spot_price=500.0,
        spot_snapshot=None,
        sequence=1,
    ):
        if spot_snapshot is None:
            spot_snapshot = {
                "price": spot_price,
                "observation_time": "2026-04-20T11:00:00Z",
                "retrieval_time": "2026-04-20T11:00:01Z",
            }
        return bi.WorkResult(
            key=bi.WorkKey(bi.WorkType.OPTIONS_GEX.value, symbol),
            sequence=sequence,
            data={
                "kind": "options_gex",
                "symbol": symbol,
                "spot_price": spot_price,
                "spot_snapshot": spot_snapshot,
                "gex_profile": {k: 10.0 for k in fs._GEX_REQUIRED_KEYS},
                "fetched_at": time.time(),
                "source": "YAHOO_OPTIONS",
                "contract_count": 10,
            },
        )

    def test_f2_01_valid_complete_snapshot_publishes_successfully(self):
        self._seed_valid_spot_cache()

        class _Ticker:
            ticker = "SPY"
            options = ["2026-06-19"]
            fast_info = SimpleNamespace(three_month_average_volume=50_000_000)

            def option_chain(self, exp):
                return _make_option_chain_pair(500.0)

        with _isolated_all_coordinators() as (heavy_coord, _, _, _, _, _):
            acc, _ = fs.enqueue_options_gex("SPY", ticker_obj=_Ticker(), spot_price=500.0)
            self.assertTrue(acc)
            self.assertTrue(_wait_workers_idle(heavy_coord, timeout=3.0))

            audit = fs.apply_background_heavy_market_data_results()
            self.assertEqual(len(audit), 1)
            self.assertTrue(audit[0][1])

        self.assertIn("SPY", fs.cache.gex)
        self.assertIn("SPY", fs.cache.gex_cache)
        self.assertGreater(fs.cache.last_gex_fetch["SPY"], 0)

    def test_f2_02_missing_spot_snapshot_is_rejected(self):
        self._seed_valid_spot_cache()
        res = self._make_valid_work_result()
        res.data.pop("spot_snapshot")

        with self.assertRaises(ValueError) as cm:
            fs._build_gex_cache_candidate(res)
        self.assertIn("missing canonical spot snapshot", str(cm.exception).lower())

        app = bi.BackgroundResultApplicator()
        audit = app.apply_results(
            [res],
            on_publish=lambda r: fs._publish_market_data_candidate(*fs._build_gex_cache_candidate(r)),
        )
        self.assertFalse(audit[0][1])
        self.assertNotIn("SPY", fs.cache.gex)

    def test_f2_03_missing_observation_time_is_rejected(self):
        self._seed_valid_spot_cache()
        snapshot = {
            "price": 500.0,
            "retrieval_time": "2026-04-20T11:00:01Z",
        }
        res = self._make_valid_work_result(spot_snapshot=snapshot)

        with self.assertRaises(ValueError) as cm:
            fs._build_gex_cache_candidate(res)
        self.assertIn("spot observation_time", str(cm.exception))

        app = bi.BackgroundResultApplicator()
        audit = app.apply_results(
            [res],
            on_publish=lambda r: fs._publish_market_data_candidate(*fs._build_gex_cache_candidate(r)),
        )
        self.assertFalse(audit[0][1])
        self.assertNotIn("SPY", fs.cache.gex)

    def test_f2_04_missing_retrieval_time_is_rejected(self):
        self._seed_valid_spot_cache()
        snapshot = {
            "price": 500.0,
            "observation_time": "2026-04-20T11:00:00Z",
        }
        res = self._make_valid_work_result(spot_snapshot=snapshot)

        with self.assertRaises(ValueError) as cm:
            fs._build_gex_cache_candidate(res)
        self.assertIn("spot retrieval_time", str(cm.exception))

        app = bi.BackgroundResultApplicator()
        audit = app.apply_results(
            [res],
            on_publish=lambda r: fs._publish_market_data_candidate(*fs._build_gex_cache_candidate(r)),
        )
        self.assertFalse(audit[0][1])
        self.assertNotIn("SPY", fs.cache.gex)

    def test_f2_05_invalid_observation_timestamp_is_rejected(self):
        self._seed_valid_spot_cache()
        snapshot = {
            "price": 500.0,
            "observation_time": "2026-99-99T99:99:99Z",
            "retrieval_time": "2026-04-20T11:00:01Z",
        }
        res = self._make_valid_work_result(spot_snapshot=snapshot)

        with self.assertRaises(ValueError) as cm:
            fs._build_gex_cache_candidate(res)
        self.assertIn("spot observation_time", str(cm.exception))

        app = bi.BackgroundResultApplicator()
        audit = app.apply_results(
            [res],
            on_publish=lambda r: fs._publish_market_data_candidate(*fs._build_gex_cache_candidate(r)),
        )
        self.assertFalse(audit[0][1])
        self.assertNotIn("SPY", fs.cache.gex)

    def test_f2_06_invalid_retrieval_timestamp_is_rejected(self):
        self._seed_valid_spot_cache()
        snapshot = {
            "price": 500.0,
            "observation_time": "2026-04-20T11:00:00Z",
            "retrieval_time": "not-a-valid-retrieval-time",
        }
        res = self._make_valid_work_result(spot_snapshot=snapshot)

        with self.assertRaises(ValueError) as cm:
            fs._build_gex_cache_candidate(res)
        self.assertIn("spot retrieval_time", str(cm.exception))

        app = bi.BackgroundResultApplicator()
        audit = app.apply_results(
            [res],
            on_publish=lambda r: fs._publish_market_data_candidate(*fs._build_gex_cache_candidate(r)),
        )
        self.assertFalse(audit[0][1])
        self.assertNotIn("SPY", fs.cache.gex)

    def test_f2_07_naive_timestamp_is_rejected(self):
        self._seed_valid_spot_cache()
        snapshot = {
            "price": 500.0,
            "observation_time": "2026-04-20T11:00:00",
            "retrieval_time": "2026-04-20T11:00:01Z",
        }
        res = self._make_valid_work_result(spot_snapshot=snapshot)

        with self.assertRaises(ValueError) as cm:
            fs._build_gex_cache_candidate(res)
        self.assertIn("spot observation_time", str(cm.exception))

        app = bi.BackgroundResultApplicator()
        audit = app.apply_results(
            [res],
            on_publish=lambda r: fs._publish_market_data_candidate(*fs._build_gex_cache_candidate(r)),
        )
        self.assertFalse(audit[0][1])
        self.assertNotIn("SPY", fs.cache.gex)

    def test_f2_08_non_zero_offset_timestamp_is_rejected(self):
        self._seed_valid_spot_cache()
        snapshot = {
            "price": 500.0,
            "observation_time": "2026-04-20T11:00:00+05:00",
            "retrieval_time": "2026-04-20T11:00:01Z",
        }
        res = self._make_valid_work_result(spot_snapshot=snapshot)

        with self.assertRaises(ValueError) as cm:
            fs._build_gex_cache_candidate(res)
        self.assertIn("spot observation_time", str(cm.exception))

        app = bi.BackgroundResultApplicator()
        audit = app.apply_results(
            [res],
            on_publish=lambda r: fs._publish_market_data_candidate(*fs._build_gex_cache_candidate(r)),
        )
        self.assertFalse(audit[0][1])
        self.assertNotIn("SPY", fs.cache.gex)

    def test_f2_09_changed_current_observation_timestamp_rejects_publication(self):
        self._seed_valid_spot_cache(obs_time="2026-04-20T11:00:00Z")
        res = self._make_valid_work_result()

        # Cache observation time updated before publication
        fs.cache.observation_times["SPY"] = "2026-04-20T11:00:15Z"

        with self.assertRaises(ValueError) as cm:
            fs._build_gex_cache_candidate(res)
        self.assertIn("superseded", str(cm.exception).lower())

        app = bi.BackgroundResultApplicator()
        audit = app.apply_results(
            [res],
            on_publish=lambda r: fs._publish_market_data_candidate(*fs._build_gex_cache_candidate(r)),
        )
        self.assertFalse(audit[0][1])
        self.assertNotIn("SPY", fs.cache.gex)

    def test_f2_10_changed_current_retrieval_timestamp_rejects_publication(self):
        self._seed_valid_spot_cache(
            price=500.0,
            obs_time="2026-04-20T11:00:00Z",
            retrieval_time="2026-04-20T11:00:01Z",
        )
        res = self._make_valid_work_result()

        # Price and observation_time remain identical, but retrieval_time changed
        fs.cache.retrieval_times["SPY"] = "2026-04-20T11:00:25Z"

        with self.assertRaises(ValueError) as cm:
            fs._build_gex_cache_candidate(res)
        self.assertIn("retrieval identity has changed", str(cm.exception).lower())

        app = bi.BackgroundResultApplicator()
        audit = app.apply_results(
            [res],
            on_publish=lambda r: fs._publish_market_data_candidate(*fs._build_gex_cache_candidate(r)),
        )
        self.assertFalse(audit[0][1])
        self.assertNotIn("SPY", fs.cache.gex)

    def test_f2_11_changed_current_spot_price_rejects_publication(self):
        self._seed_valid_spot_cache(price=500.0)
        res = self._make_valid_work_result(spot_price=500.0)

        # Price moves before publication
        fs.cache.prices["SPY"] = 502.50

        with self.assertRaises(ValueError) as cm:
            fs._build_gex_cache_candidate(res)
        self.assertIn("spot price has changed", str(cm.exception).lower())

        app = bi.BackgroundResultApplicator()
        audit = app.apply_results(
            [res],
            on_publish=lambda r: fs._publish_market_data_candidate(*fs._build_gex_cache_candidate(r)),
        )
        self.assertFalse(audit[0][1])
        self.assertNotIn("SPY", fs.cache.gex)

    def test_f2_12_non_actionable_current_price_state_rejects_publication(self):
        non_actionable_states = [
            ("VALID", "STALE"),
            ("INVALID", "CURRENT"),
            ("UNAVAILABLE", "UNAVAILABLE"),
            ("STALE", "STALE"),
        ]
        res = self._make_valid_work_result()

        for validity, freshness in non_actionable_states:
            self._seed_valid_spot_cache()
            fs.cache.price_validity["SPY"] = validity
            fs.cache.freshness["SPY"] = freshness

            with self.assertRaises(ValueError) as cm:
                fs._build_gex_cache_candidate(res)
            self.assertIn("no longer actionable", str(cm.exception).lower())

            app = bi.BackgroundResultApplicator()
            audit = app.apply_results(
                [res],
                on_publish=lambda r: fs._publish_market_data_candidate(*fs._build_gex_cache_candidate(r)),
            )
            self.assertFalse(audit[0][1])
            self.assertNotIn("SPY", fs.cache.gex)

    def test_f2_13_missing_canonical_timestamp_prevents_enqueue(self):
        # Case A: missing observation_time
        self._seed_valid_spot_cache()
        fs.cache.observation_times.pop("SPY", None)
        acc_obs, key_obs = fs.enqueue_options_gex("SPY")
        self.assertFalse(acc_obs)
        self.assertIsNone(key_obs)

        # Case B: missing retrieval_time
        self._seed_valid_spot_cache()
        fs.cache.retrieval_times.pop("SPY", None)
        acc_ret, key_ret = fs.enqueue_options_gex("SPY")
        self.assertFalse(acc_ret)
        self.assertIsNone(key_ret)

    def test_f2_14_invalid_canonical_timestamp_prevents_enqueue(self):
        # Case A: naive timestamp in cache
        self._seed_valid_spot_cache()
        fs.cache.observation_times["SPY"] = "2026-04-20T11:00:00"
        acc_naive, key_naive = fs.enqueue_options_gex("SPY")
        self.assertFalse(acc_naive)
        self.assertIsNone(key_naive)

        # Case B: non-zero offset timestamp in cache
        self._seed_valid_spot_cache()
        fs.cache.retrieval_times["SPY"] = "2026-04-20T11:00:01+03:00"
        acc_offset, key_offset = fs.enqueue_options_gex("SPY")
        self.assertFalse(acc_offset)
        self.assertIsNone(key_offset)

        # Case C: malformed timestamp in cache
        self._seed_valid_spot_cache()
        fs.cache.observation_times["SPY"] = "invalid-iso-timestamp"
        acc_malformed, key_malformed = fs.enqueue_options_gex("SPY")
        self.assertFalse(acc_malformed)
        self.assertIsNone(key_malformed)

    def test_f2_15_rejected_results_preserve_both_gex_caches_and_last_gex_fetch(self):
        self._seed_valid_spot_cache()
        prior_profile = {k: 77.7 for k in fs._GEX_REQUIRED_KEYS}
        prior_fetch = 1_700_000_000.0
        fs.cache.gex["SPY"] = dict(prior_profile)
        fs.cache.gex_cache["SPY"] = dict(prior_profile)
        fs.cache.last_gex_fetch["SPY"] = prior_fetch

        # Create result with mismatched price
        res = self._make_valid_work_result(spot_price=480.0)

        app = bi.BackgroundResultApplicator()
        audit = app.apply_results(
            [res],
            on_publish=lambda r: fs._publish_market_data_candidate(*fs._build_gex_cache_candidate(r)),
        )
        self.assertEqual(len(audit), 1)
        self.assertFalse(audit[0][1])

        # Both caches and last_gex_fetch remain strictly unchanged
        self.assertEqual(fs.cache.gex["SPY"], prior_profile)
        self.assertEqual(fs.cache.gex_cache["SPY"], prior_profile)
        self.assertEqual(fs.cache.last_gex_fetch["SPY"], prior_fetch)

    def test_f2_16_rejected_results_do_not_advance_applied_sequence(self):
        self._seed_valid_spot_cache()
        key = bi.WorkKey(bi.WorkType.OPTIONS_GEX.value, "SPY")
        app = bi.BackgroundResultApplicator()

        self.assertEqual(app.get_last_applied(key), (-1, ""))

        # Invalid result sequence 1 (mismatched price)
        res1 = self._make_valid_work_result(spot_price=490.0, sequence=1)
        audit1 = app.apply_results(
            [res1],
            on_publish=lambda r: fs._publish_market_data_candidate(*fs._build_gex_cache_candidate(r)),
        )
        self.assertFalse(audit1[0][1])
        # Sequence not advanced
        self.assertEqual(app.get_last_applied(key), (-1, ""))

    def test_f2_17_subsequent_valid_snapshot_can_still_publish_successfully(self):
        self._seed_valid_spot_cache()
        key = bi.WorkKey(bi.WorkType.OPTIONS_GEX.value, "SPY")
        app = bi.BackgroundResultApplicator()

        # Reject sequence 1
        res1 = self._make_valid_work_result(spot_price=490.0, sequence=1)
        audit1 = app.apply_results(
            [res1],
            on_publish=lambda r: fs._publish_market_data_candidate(*fs._build_gex_cache_candidate(r)),
        )
        self.assertFalse(audit1[0][1])
        self.assertEqual(app.get_last_applied(key), (-1, ""))

        # Sequence 2 is valid and matches cache
        res2 = self._make_valid_work_result(spot_price=500.0, sequence=2)
        audit2 = app.apply_results(
            [res2],
            on_publish=lambda r: fs._publish_market_data_candidate(*fs._build_gex_cache_candidate(r)),
        )
        self.assertTrue(audit2[0][1])
        self.assertEqual(app.get_last_applied(key)[0], 2)
        self.assertIn("SPY", fs.cache.gex)
        self.assertIn("SPY", fs.cache.gex_cache)
        self.assertEqual(fs.cache.gex["SPY"]["net_gex"], 10.0)


class TestStage223Finding3DaemonCoordinatorShutdown(_HeavyMarketDataTestBase):
    class _StopLoop(BaseException):
        pass

    def _setup_daemon_mocks(self, stack, mock_heavy):
        stack.enter_context(patch.object(fs, "HEAVY_MARKET_DATA_COORDINATOR", mock_heavy))
        stack.enter_context(patch.object(fs, "ALL_TICKERS", ["AAPL"]))
        stack.enter_context(patch.object(fs, "TICKERS", ["AAPL"]))
        stack.enter_context(patch.object(fs, "MACRO_TICKERS", []))
        stack.enter_context(patch.object(fs, "_load_ssot_tickers", return_value=["AAPL"]))
        stack.enter_context(patch.object(fs, "_load_macro_tickers", return_value=[]))
        stack.enter_context(patch.object(fs.yf, "Ticker", return_value=SimpleNamespace(ticker="AAPL", options=[])))
        stack.enter_context(patch.object(fs, "get_market_status", return_value="OPEN"))
        stack.enter_context(patch.object(fs, "resolve_held_and_watched", return_value=(set(), None)))
        stack.enter_context(patch.object(fs, "get_macro_telemetry_context", return_value=(18.0, "NORMAL", 0.0)))
        stack.enter_context(patch.object(fs, "coordinate_market_data_cycle", return_value={"applied_count": 0}))
        stack.enter_context(patch.object(fs, "coordinate_heavy_market_data_cycle", return_value={"applied_count": 0}))
        stack.enter_context(patch.object(fs, "apply_background_intelligence_results"))
        stack.enter_context(patch.object(fs, "build_daemon_final_output", return_value={"tickers": []}))
        stack.enter_context(patch.object(fs, "update_global_state_snapshot"))
        stack.enter_context(patch.object(fs, "publish_global_state_snapshot"))
        stack.enter_context(patch.object(fs, "get_global_state_snapshot", return_value={}))
        stack.enter_context(patch.object(fs.cache, "save_to_disk"))

    def test_f3_01_exiting_run_daemon_invokes_shutdown_with_5s_timeout(self):
        mock_heavy = MagicMock()
        mock_heavy.shutdown.return_value = True

        with contextlib.ExitStack() as stack:
            self._setup_daemon_mocks(stack, mock_heavy)
            stack.enter_context(patch.object(fs.t_time, "sleep", side_effect=self._StopLoop()))
            try:
                fs.run_daemon()
            except self._StopLoop:
                pass

        mock_heavy.shutdown.assert_called_once_with(timeout=5.0)

    def test_f3_02_keyboard_interrupt_triggers_shutdown_cleanly(self):
        mock_heavy = MagicMock()
        mock_heavy.shutdown.return_value = True

        with contextlib.ExitStack() as stack:
            self._setup_daemon_mocks(stack, mock_heavy)
            stack.enter_context(patch.object(fs.t_time, "sleep", side_effect=KeyboardInterrupt()))
            # Must exit cleanly without re-raising KeyboardInterrupt
            fs.run_daemon()

        mock_heavy.shutdown.assert_called_once_with(timeout=5.0)

    def test_f3_03_unhandled_exception_triggers_shutdown_and_preserves_exception(self):
        mock_heavy = MagicMock()
        mock_heavy.shutdown.return_value = True

        with contextlib.ExitStack() as stack:
            self._setup_daemon_mocks(stack, mock_heavy)
            stack.enter_context(patch.object(fs, "coordinate_heavy_market_data_cycle", side_effect=ZeroDivisionError("simulated crash")))
            with self.assertRaises(ZeroDivisionError) as cm:
                fs.run_daemon()
            self.assertEqual(str(cm.exception), "simulated crash")

        mock_heavy.shutdown.assert_called_once_with(timeout=5.0)

    def test_f3_04_shutdown_returning_false_logs_warning_without_raising(self):
        mock_heavy = MagicMock()
        mock_heavy.shutdown.return_value = False

        with contextlib.ExitStack() as stack:
            self._setup_daemon_mocks(stack, mock_heavy)
            stack.enter_context(patch.object(fs.t_time, "sleep", side_effect=self._StopLoop()))
            with self.assertLogs(fs.logger, level="WARNING") as cm:
                try:
                    fs.run_daemon()
                except self._StopLoop:
                    pass

        mock_heavy.shutdown.assert_called_once_with(timeout=5.0)
        self.assertTrue(any("Shutdown timed out after 5.0s during daemon exit" in msg for msg in cm.output))

    def test_f3_05_shutdown_raising_exception_logs_error_and_preserves_primary_exception(self):
        mock_heavy = MagicMock()
        mock_heavy.shutdown.side_effect = RuntimeError("shutdown failure")

        with contextlib.ExitStack() as stack:
            self._setup_daemon_mocks(stack, mock_heavy)
            stack.enter_context(patch.object(fs.t_time, "sleep", side_effect=ValueError("primary loop error")))
            with self.assertLogs(fs.logger, level="ERROR") as cm:
                with self.assertRaises(ValueError) as err_cm:
                    fs.run_daemon()
            self.assertEqual(str(err_cm.exception), "primary loop error")

        mock_heavy.shutdown.assert_called_once_with(timeout=5.0)
        self.assertTrue(any("Error shutting down coordinator during daemon exit: shutdown failure" in msg for msg in cm.output))


class TestVwapRotationAndPreTickGexDrain(unittest.TestCase):
    """Regression coverage for VWAP modular starvation fix and pre-tick GEX drain ordering."""

    def test_select_next_vwap_batch_visits_all_indices_and_prioritizes_missing_vwap(self):
        tickers = ["UMAC", "AMZN", "GOOG", "^VIX", "VIXY", "^VVIX", "IEF", "UUP", "SPY", "QQQ"]
        # Simulate prior even-length modular starvation starting at odd pointer 1
        visited = set()
        ptr = 1
        vwaps = {sym: 100.0 for sym in tickers if sym not in ("UMAC", "^VIX", "^VVIX")}
        vwaps["UMAC"] = 0.0  # Held ticker with missing VWAP

        first_batch, ptr = fs._select_next_vwap_batch(
            tickers, ptr, batch_size=2, current_vwaps=vwaps, max_catchup=2
        )
        # Missing VWAP catch-up immediately picks up UMAC on the very first cycle
        self.assertIn("UMAC", first_batch)
        visited.update(first_batch)

        # Walking pointer around full cycle with no missing catch-up visits index 0 cleanly too
        ptr = 1
        cycle_visited = set()
        for _ in range(6):
            b, ptr = fs._select_next_vwap_batch(tickers, ptr, batch_size=2, current_vwaps=None)
            cycle_visited.update(b)
        self.assertEqual(cycle_visited, set(tickers))

    def test_draining_heavy_gex_before_spot_update_publishes_spy_and_qqq(self):
        with _isolated_all_coordinators() as (_, _, _, _, heavy_coord, heavy_app):
            t_obs = "2026-11-24T14:30:00Z"
            t_ret = "2026-11-24T14:30:01Z"
            for sym, spot in (("SPY", 719.50), ("QQQ", 641.50)):
                fs.cache.prices[sym] = spot
                fs.cache.price_validity[sym] = "VALID"
                fs.cache.freshness[sym] = "CURRENT"
                fs.cache.observation_times[sym] = t_obs
                fs.cache.retrieval_times[sym] = t_ret

            class _MockTicker:
                def __init__(self, sym, spot):
                    self.ticker = sym
                    self._spot = spot
                    self.options = ["2026-12-18"]
                    self.fast_info = SimpleNamespace(three_month_average_volume=50_000_000)

                def option_chain(self, exp):
                    return _make_option_chain_pair(self._spot)

            acc_spy, _ = fs.enqueue_options_gex(
                "SPY", ticker_obj=_MockTicker("SPY", 719.50), spot_price=719.50, coordinator=heavy_coord
            )
            acc_qqq, _ = fs.enqueue_options_gex(
                "QQQ", ticker_obj=_MockTicker("QQQ", 641.50), spot_price=641.50, coordinator=heavy_coord
            )
            self.assertTrue(acc_spy and acc_qqq)
            self.assertTrue(_wait_workers_idle(heavy_coord, timeout=5.0))

            # Drain BEFORE next tick mutates spot price/timestamps -> both SPY & QQQ publish
            audit = fs.apply_background_heavy_market_data_results(
                coordinator=heavy_coord, applicator=heavy_app
            )
            self.assertEqual(len(audit), 2)
            self.assertTrue(all(ok for _, ok, _ in audit), f"Unexpected rejection: {audit}")
            self.assertIn("SPY", fs.cache.gex)
            self.assertIn("QQQ", fs.cache.gex)


class TestDashboardRvolColumn(unittest.TestCase):
    """Verify dashboard table header, row rendering, cache busting, and section colspan include rVol."""

    def test_dashboard_table_includes_rvol_column_and_aligned_colspan(self):
        import re
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        html = (repo_root / "static" / "index.html").read_text(encoding="utf-8")
        app_js = (repo_root / "static" / "app.js").read_text(encoding="utf-8")

        table_match = re.search(
            r'<table id="data-table">.*?<thead>\s*<tr>(.*?)</tr>\s*</thead>',
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(table_match)
        headers = re.findall(r"<th\b[^>]*>(.*?)</th>", table_match.group(1), re.DOTALL)
        self.assertEqual(len(headers), 14)
        self.assertEqual(headers[4].strip(), "Volume")
        self.assertEqual(headers[5].strip(), "rVol")
        # Cache-bust must be at least the rVol release (18.56); later bumps are fine.
        app_ver = re.search(r'app\.js\?v=(\d+)\.(\d+)', html)
        self.assertIsNotNone(app_ver)
        self.assertGreaterEqual((int(app_ver.group(1)), int(app_ver.group(2))), (18, 56))

        self.assertIn("rvol: 0,", app_js)
        self.assertIn("<td class=\"num-col\">${formatVol(row.volume)}</td>\n                <td class=\"num-col ${rvolClass}\"", app_js.replace("\r\n", "\n"))
        self.assertIn("Number(row.volume) > 0 && Number.isFinite(rvolNum) && rvolNum > 0", app_js)
        self.assertIn("<td colspan=\"14\">${label}</td>", app_js)
        self.assertIn("<td colspan=\"14\">Scout Intelligence Suggestions</td>", app_js)
        self.assertNotIn("colspan=\"13\"", app_js)

    def test_routine_is_heavy_respects_daily_history_and_gex_ttls(self):
        """Routine is_heavy=True cycles (force=False) must honor daily history and 1800s GEX TTLs; force=True overrides."""
        now_ny = datetime(2026, 4, 20, 11, 0, tzinfo=NY_TZ)
        sample_df = _make_sample_history_df(days=220, end_date=now_ny)

        fs.cache.prices["AAPL"] = 200.0
        fs.cache.price_validity["AAPL"] = "VALID"
        fs.cache.freshness["AAPL"] = "CURRENT"
        fs.cache.observation_times["AAPL"] = "2026-04-20T15:00:00Z"
        fs.cache.retrieval_times["AAPL"] = "2026-04-20T15:00:01Z"
        fs.cache.history["AAPL"] = sample_df
        fs.cache.last_history_fetch_date["AAPL"] = "2026-04-20"
        fs.cache.gex_cache["AAPL"] = {"net_gex_total": 0.1}
        fs.cache.last_gex_fetch["AAPL"] = 1000.0

        with _isolated_all_coordinators() as (heavy_coord, _, _, _, _, _):
            stats_routine = fs.coordinate_heavy_market_data_cycle(
                ["AAPL"],
                tickers_obj={"AAPL": SimpleNamespace()},
                is_heavy=True,
                force=False,
                today_str="2026-04-20",
                now_ny=now_ny,
                now_ts=1300.0,  # within 1800s TTL
                coordinator=heavy_coord,
            )
            self.assertEqual(stats_routine["historical_enqueued"], 0)
            self.assertEqual(stats_routine["gex_enqueued"], 0)

            stats_forced = fs.coordinate_heavy_market_data_cycle(
                ["AAPL"],
                tickers_obj={"AAPL": SimpleNamespace()},
                is_heavy=True,
                force=True,
                today_str="2026-04-20",
                now_ny=now_ny,
                now_ts=1300.0,
                coordinator=heavy_coord,
            )
            self.assertEqual(stats_forced["historical_enqueued"], 1)
            self.assertEqual(stats_forced["gex_enqueued"], 1)

            stats_default = fs.coordinate_heavy_market_data_cycle(
                ["AAPL"],
                tickers_obj={"AAPL": SimpleNamespace()},
                is_heavy=True,
                today_str="2026-04-20",
                now_ny=now_ny,
                now_ts=1300.0,
                coordinator=heavy_coord,
            )
            self.assertEqual(stats_default["historical_enqueued"], 1)
            self.assertEqual(stats_default["gex_enqueued"], 1)


if __name__ == "__main__":
    unittest.main()
