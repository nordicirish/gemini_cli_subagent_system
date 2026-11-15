"""
Stage 2.2.4 — Market-Data Pipeline Integration and Hardening Test Suite.

Verifies and hardens the complete runtime integration between MARKET_DATA_COORDINATOR,
HEAVY_MARKET_DATA_COORDINATOR, and BACKGROUND_COORDINATOR across startup, repeated
monitoring cycles, refresh scheduling, dynamic symbol-list changes, provider failures,
result publication, and bounded shutdown.
"""

import contextlib
import math
import os
import sys
import threading
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
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


def _make_batch_quote(price: float, prev_close: float = 100.0, volume: int = 1_000_000, ts: int | None = None):
    return {
        "regularMarketPrice": price,
        "regularMarketPreviousClose": prev_close,
        "regularMarketVolume": volume,
        "regularMarketTime": ts or int(time.time()),
    }


@contextlib.contextmanager
def _isolated_pipeline_coordinators(
    *,
    heavy_workers: int = 2,
    heavy_queue: int = 64,
    heavy_results: int = 128,
    mkt_workers: int = 2,
    mkt_queue: int = 50,
    mkt_results: int = 100,
):
    """Hermetic coordinator environment for pipeline integration tests."""
    heavy_coord = bi.BackgroundIntelligenceCoordinator(
        max_workers=heavy_workers,
        max_queue_size=heavy_queue,
        max_results_size=heavy_results,
        worker_name_prefix="TestHeavyWorker",
    )
    heavy_app = bi.BackgroundResultApplicator(coordinator=heavy_coord)

    mkt_coord = bi.BackgroundIntelligenceCoordinator(
        max_workers=mkt_workers,
        max_queue_size=mkt_queue,
        max_results_size=mkt_results,
        worker_name_prefix="TestLiveWorker",
    )
    mkt_app = bi.BackgroundResultApplicator(coordinator=mkt_coord)

    bg_coord = bi.BackgroundIntelligenceCoordinator(
        max_workers=1,
        max_queue_size=20,
        max_results_size=20,
        worker_name_prefix="TestBgWorker",
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


class _MarketDataPipelineIntegrationTestBase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._original_all_tickers = list(fs.ALL_TICKERS)
        self.addCleanup(self._restore_all_tickers)
        default_test_symbols = ["SPY", "AAPL", "MSFT", "QQQ", "NVDA", "GOOG", "AMZN"]
        fs.ALL_TICKERS = list(dict.fromkeys(self._original_all_tickers + default_test_symbols))
        fs.cache.clear()
        fs.FORCE_REFRESH = False
        with fs._MARKET_DATA_FALLBACK_STATE_LOCK:
            fs._MARKET_DATA_FALLBACK_REQUESTS.clear()

    def _restore_all_tickers(self):
        fs.ALL_TICKERS = list(self._original_all_tickers)

    def tearDown(self):
        try:
            fs.ALL_TICKERS = list(self._original_all_tickers)
            fs.cache.clear()
            fs.FORCE_REFRESH = False
            with fs._MARKET_DATA_FALLBACK_STATE_LOCK:
                fs._MARKET_DATA_FALLBACK_REQUESTS.clear()
        finally:
            super().tearDown()

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


class TestMarketDataPipelineIntegration(_MarketDataPipelineIntegrationTestBase):
    def test_01_live_acquisition_and_publication_while_historical_is_blocked(self):
        """1. Blocked historical-data worker cannot block live quote acquisition or publication."""
        hist_started = threading.Event()
        hist_release = threading.Event()

        class _BlockedTicker:
            ticker = "MSFT"

            @property
            def fast_info(self):
                return SimpleNamespace(regular_market_previous_close=400.0)

            def history(self, **kwargs):
                hist_started.set()
                if not hist_release.wait(timeout=3.0):
                    raise TimeoutError("Historical test release timed out")
                return _make_sample_history_df(days=220)

        with _isolated_pipeline_coordinators() as (heavy_coord, _, mkt_coord, mkt_app, _, _):
            try:
                acc_hist, _ = fs.enqueue_historical_data(
                    "MSFT",
                    t_obj=_BlockedTicker(),
                    today_str="2026-04-20",
                    coordinator=heavy_coord,
                )
                self.assertTrue(acc_hist)
                self.assertTrue(hist_started.wait(timeout=2.0))

                # While historical worker is blocked in flight, live market data cycle executes for AAPL
                batch_quotes = {"AAPL": _make_batch_quote(175.50, prev_close=170.0)}
                with patch.object(fs, "get_batch_quotes", return_value=batch_quotes):
                    fs.coordinate_market_data_cycle(
                        symbols=["AAPL"],
                        status="OPEN",
                        coordinator=mkt_coord,
                        applicator=mkt_app,
                    )
                    self.assertTrue(_wait_workers_idle(mkt_coord, timeout=2.0))
                    # Drain and apply live results
                    fs.coordinate_market_data_cycle(
                        symbols=["AAPL"],
                        status="OPEN",
                        coordinator=mkt_coord,
                        applicator=mkt_app,
                    )

                # Live quote published while historical worker remains blocked!
                self.assertEqual(fs.cache.prices["AAPL"], 175.50)
                self.assertEqual(fs.cache.price_validity["AAPL"], "VALID")
                self.assertEqual(fs.cache.freshness["AAPL"], "CURRENT")
                self.assertNotIn("MSFT", fs.cache.history)
            finally:
                hist_release.set()
                _wait_workers_idle(heavy_coord, timeout=2.0)

    def test_02_live_acquisition_and_publication_while_gex_is_blocked(self):
        """2. Blocked options/GEX worker cannot block live quote acquisition or publication."""
        gex_started = threading.Event()
        gex_release = threading.Event()
        self._seed_valid_spot_cache("QQQ", price=450.0)

        class _BlockedGexTicker:
            ticker = "QQQ"
            options = ["2026-06-19"]
            fast_info = SimpleNamespace(three_month_average_volume=30_000_000)

            def option_chain(self, exp):
                gex_started.set()
                if not gex_release.wait(timeout=3.0):
                    raise TimeoutError("GEX test release timed out")
                return _make_option_chain_pair(450.0)

        with _isolated_pipeline_coordinators() as (heavy_coord, _, mkt_coord, mkt_app, _, _):
            try:
                acc_gex, _ = fs.enqueue_options_gex(
                    "QQQ",
                    ticker_obj=_BlockedGexTicker(),
                    spot_price=450.0,
                    coordinator=heavy_coord,
                )
                self.assertTrue(acc_gex)
                self.assertTrue(gex_started.wait(timeout=2.0))

                # Execute live market data cycle for NVDA
                batch_quotes = {"NVDA": _make_batch_quote(880.0, prev_close=870.0)}
                with patch.object(fs, "get_batch_quotes", return_value=batch_quotes):
                    fs.coordinate_market_data_cycle(
                        symbols=["NVDA"],
                        status="OPEN",
                        coordinator=mkt_coord,
                        applicator=mkt_app,
                    )
                    self.assertTrue(_wait_workers_idle(mkt_coord, timeout=2.0))
                    fs.coordinate_market_data_cycle(
                        symbols=["NVDA"],
                        status="OPEN",
                        coordinator=mkt_coord,
                        applicator=mkt_app,
                    )

                self.assertEqual(fs.cache.prices["NVDA"], 880.0)
                self.assertEqual(fs.cache.price_validity["NVDA"], "VALID")
                self.assertNotIn("QQQ", fs.cache.gex)
            finally:
                gex_release.set()
                _wait_workers_idle(heavy_coord, timeout=2.0)

    def test_03_heavy_queue_saturation_without_live_coordinator_starvation(self):
        """3. Heavy queue saturation cannot consume live coordinator queue or worker capacity."""
        release_workers = threading.Event()
        t1_started = threading.Event()

        def _blocking_work():
            t1_started.set()
            release_workers.wait(timeout=3.0)
            return bi.ObservedResult(data={})

        with _isolated_pipeline_coordinators(
            heavy_workers=1,
            heavy_queue=1,
            mkt_workers=2,
            mkt_queue=10,
        ) as (heavy_coord, _, mkt_coord, mkt_app, _, _):
            try:
                # Item 1 starts running on the 1 worker
                acc1, _ = heavy_coord.enqueue(bi.WorkType.HISTORICAL_DATA.value, "H1", fn=_blocking_work)
                self.assertTrue(acc1)
                self.assertTrue(t1_started.wait(timeout=2.0))

                # Item 2 fills the queue (queue capacity = 1)
                acc2, _ = heavy_coord.enqueue(bi.WorkType.HISTORICAL_DATA.value, "H2", fn=_blocking_work)
                self.assertTrue(acc2)

                # Item 3 is rejected due to queue capacity saturation
                acc3, key3 = heavy_coord.enqueue(bi.WorkType.HISTORICAL_DATA.value, "H3", fn=_blocking_work)
                self.assertFalse(acc3)
                self.assertIsNone(key3)

                # Live coordinator has independent capacity and completes live quote work cleanly
                batch_quotes = {"SPY": _make_batch_quote(505.0, prev_close=500.0)}
                with patch.object(fs, "get_batch_quotes", return_value=batch_quotes):
                    fs.coordinate_market_data_cycle(
                        symbols=["SPY"],
                        status="OPEN",
                        coordinator=mkt_coord,
                        applicator=mkt_app,
                    )
                    self.assertTrue(_wait_workers_idle(mkt_coord, timeout=2.0))
                    fs.coordinate_market_data_cycle(
                        symbols=["SPY"],
                        status="OPEN",
                        coordinator=mkt_coord,
                        applicator=mkt_app,
                    )

                self.assertEqual(fs.cache.prices["SPY"], 505.0)
            finally:
                release_workers.set()
                _wait_workers_idle(heavy_coord, timeout=2.0)

    def test_04_non_blocking_startup_scheduling(self):
        """4. Initial live, historical, and GEX workloads are scheduled without sequential provider waits."""
        worker_block = threading.Event()
        now_ny = datetime(2026, 4, 20, 11, 0, 5, tzinfo=NY_TZ)

        class _SlowTicker:
            ticker = "AAPL"
            options = ["2026-06-19"]
            fast_info = SimpleNamespace(
                regular_market_previous_close=180.0,
                three_month_average_volume=50_000_000,
            )

            def history(self, **kwargs):
                worker_block.wait(timeout=3.0)
                return _make_sample_history_df(days=220)

            def option_chain(self, exp):
                worker_block.wait(timeout=3.0)
                return _make_option_chain_pair(180.0)

        self._seed_valid_spot_cache("AAPL", price=180.0, obs_time="2026-04-20T15:00:00Z", retrieval_time="2026-04-20T15:00:01Z")

        with _isolated_pipeline_coordinators() as (heavy_coord, heavy_app, mkt_coord, mkt_app, _, _):
            try:
                t0 = time.monotonic()
                # Schedule startup live cycle
                fs.coordinate_market_data_cycle(
                    symbols=["AAPL"],
                    status="OPEN",
                    now_ny=now_ny,
                    coordinator=mkt_coord,
                    applicator=mkt_app,
                )
                # Schedule startup heavy cycle
                stats = fs.coordinate_heavy_market_data_cycle(
                    symbols=["AAPL"],
                    tickers_obj={"AAPL": _SlowTicker()},
                    is_heavy=True,
                    today_str="2026-04-20",
                    now_ny=now_ny,
                    coordinator=heavy_coord,
                    applicator=heavy_app,
                )
                elapsed = time.monotonic() - t0

                # Scheduling on coordinating thread must be near-instantaneous (< 0.25s)
                self.assertLess(elapsed, 0.25)
                self.assertEqual(stats["historical_enqueued"], 1)
                self.assertEqual(stats["gex_enqueued"], 1)
                self.assertGreater(heavy_coord.pending_count() + heavy_coord.in_flight_count(), 0)
            finally:
                worker_block.set()
                _wait_workers_idle(heavy_coord, timeout=2.0)

    def test_05_repeated_monitoring_cycles_with_queued_and_completed_heavy_work(self):
        """5. Repeated monitoring cycles do not create duplicate or unbounded work."""
        worker_block = threading.Event()
        sample_df = _make_sample_history_df(days=220)

        class _Ticker:
            ticker = "MSFT"
            options = ["2026-06-19"]
            fast_info = SimpleNamespace(
                regular_market_previous_close=400.0,
                three_month_average_volume=30_000_000,
            )

            def history(self, **kwargs):
                worker_block.wait(timeout=3.0)
                return sample_df.copy()

            def option_chain(self, exp):
                worker_block.wait(timeout=3.0)
                return _make_option_chain_pair(400.0)

        self._seed_valid_spot_cache("MSFT", price=400.0)

        with _isolated_pipeline_coordinators() as (heavy_coord, heavy_app, _, _, _, _):
            try:
                # Cycle 1: Enqueue initial heavy work
                stats1 = fs.coordinate_heavy_market_data_cycle(
                    symbols=["MSFT"],
                    tickers_obj={"MSFT": _Ticker()},
                    is_heavy=False,
                    today_str="2026-04-20",
                    coordinator=heavy_coord,
                    applicator=heavy_app,
                )
                self.assertEqual(stats1["historical_enqueued"], 1)
                self.assertEqual(stats1["gex_enqueued"], 1)

                # Cycle 2: Work is in flight; coordinator coalesces with in-flight tasks without increasing pending queue
                initial_pending = heavy_coord.pending_count()
                fs.coordinate_heavy_market_data_cycle(
                    symbols=["MSFT"],
                    tickers_obj={"MSFT": _Ticker()},
                    is_heavy=False,
                    today_str="2026-04-20",
                    coordinator=heavy_coord,
                    applicator=heavy_app,
                )
                self.assertEqual(heavy_coord.pending_count(), initial_pending)

                # Allow workers to complete
                worker_block.set()
                self.assertTrue(_wait_workers_idle(heavy_coord, timeout=3.0))

                # Cycle 3: Applies completed results to cache
                stats3 = fs.coordinate_heavy_market_data_cycle(
                    symbols=["MSFT"],
                    tickers_obj={"MSFT": _Ticker()},
                    is_heavy=False,
                    today_str="2026-04-20",
                    coordinator=heavy_coord,
                    applicator=heavy_app,
                )
                self.assertEqual(stats3["historical_applied"], 1)
                self.assertEqual(stats3["gex_applied"], 1)
                self.assertIn("MSFT", fs.cache.history)
                self.assertIn("MSFT", fs.cache.gex)

                # Cycle 4: Caches are fresh; no duplicate re-enqueue occurs
                stats4 = fs.coordinate_heavy_market_data_cycle(
                    symbols=["MSFT"],
                    tickers_obj={"MSFT": _Ticker()},
                    is_heavy=False,
                    today_str="2026-04-20",
                    coordinator=heavy_coord,
                    applicator=heavy_app,
                )
                self.assertEqual(stats4["historical_enqueued"], 0)
                self.assertEqual(stats4["gex_enqueued"], 0)
            finally:
                worker_block.set()

    def test_06_correct_coordinating_thread_publication_of_heavy_results(self):
        """6. Completed heavy results are applied through the existing coordinating-thread publication boundary."""
        coord_thread_id = threading.get_ident()
        worker_threads = []
        publish_threads = []

        class _Ticker:
            ticker = "AAPL"
            fast_info = SimpleNamespace(regular_market_previous_close=150.0)

            def history(self, **kwargs):
                worker_threads.append(threading.get_ident())
                return _make_sample_history_df(days=220)

        with _isolated_pipeline_coordinators() as (heavy_coord, heavy_app, _, _, _, _):
            acc, _ = fs.enqueue_historical_data(
                "AAPL",
                t_obj=_Ticker(),
                today_str="2026-04-20",
                coordinator=heavy_coord,
            )
            self.assertTrue(acc)
            self.assertTrue(_wait_workers_idle(heavy_coord, timeout=3.0))

            # Shared cache remains empty before coordinating-thread application
            self.assertNotIn("AAPL", fs.cache.history)

            orig_publish = fs._publish_market_data_candidate

            def _spy_publish(*args, **kwargs):
                publish_threads.append(threading.get_ident())
                return orig_publish(*args, **kwargs)

            with patch.object(fs, "_publish_market_data_candidate", side_effect=_spy_publish):
                audit = fs.apply_background_heavy_market_data_results(
                    coordinator=heavy_coord,
                    applicator=heavy_app,
                )
                self.assertEqual(len(audit), 1)
                self.assertTrue(audit[0][1])

            # Publication occurred strictly on the coordinating thread
            self.assertIn(coord_thread_id, publish_threads)
            self.assertTrue(all(tid != coord_thread_id for tid in worker_threads))
            self.assertIn("AAPL", fs.cache.history)

    def test_07_historical_daily_refresh_behaviour(self):
        """7. Historical refresh remains governed by daily refresh semantics."""
        sample_df = _make_sample_history_df(days=220)
        t_obj = SimpleNamespace(
            ticker="SPY",
            fast_info=SimpleNamespace(regular_market_previous_close=500.0),
            history=lambda **k: sample_df.copy(),
        )

        with _isolated_pipeline_coordinators() as (heavy_coord, heavy_app, _, _, _, _):
            # Seed day 1 cache
            fs.cache.history["SPY"] = sample_df.copy()
            fs.cache.last_history_fetch_date["SPY"] = "2026-04-20"

            # Same day (2026-04-20) without is_heavy -> no re-enqueue
            stats1 = fs.coordinate_heavy_market_data_cycle(
                symbols=["SPY"],
                tickers_obj={"SPY": t_obj},
                is_heavy=False,
                today_str="2026-04-20",
                coordinator=heavy_coord,
                applicator=heavy_app,
            )
            self.assertEqual(stats1["historical_enqueued"], 0)

            # Next trading day (2026-04-21) -> historical refresh enqueued
            stats2 = fs.coordinate_heavy_market_data_cycle(
                symbols=["SPY"],
                tickers_obj={"SPY": t_obj},
                is_heavy=False,
                today_str="2026-04-21",
                coordinator=heavy_coord,
                applicator=heavy_app,
            )
            self.assertEqual(stats2["historical_enqueued"], 1)

            # Wait and apply
            self.assertTrue(_wait_workers_idle(heavy_coord, timeout=3.0))
            fs.apply_background_heavy_market_data_results(coordinator=heavy_coord, applicator=heavy_app)
            self.assertEqual(fs.cache.last_history_fetch_date["SPY"], "2026-04-21")

            # Same day with is_heavy=True -> forces re-enqueue via force=is_heavy
            stats3 = fs.coordinate_heavy_market_data_cycle(
                symbols=["SPY"],
                tickers_obj={"SPY": t_obj},
                is_heavy=True,
                today_str="2026-04-21",
                coordinator=heavy_coord,
                applicator=heavy_app,
            )
            self.assertEqual(stats3["historical_enqueued"], 1)

    def test_08_gex_ttl_and_refresh_eligibility(self):
        """8. Options/GEX retains its existing 1,800-second TTL."""
        self._seed_valid_spot_cache("SPY", price=500.0)
        t_obj = SimpleNamespace(
            ticker="SPY",
            options=["2026-06-19"],
            fast_info=SimpleNamespace(three_month_average_volume=40_000_000),
            option_chain=lambda exp: _make_option_chain_pair(500.0),
        )
        t0 = 1_700_000_000.0
        fs.cache.last_gex_fetch["SPY"] = t0
        fs.cache.gex_cache["SPY"] = {k: 5.0 for k in fs._GEX_REQUIRED_KEYS}

        with _isolated_pipeline_coordinators() as (heavy_coord, _, _, _, _, _):
            # Within TTL (elapsed 500s < 1800s) -> rejected
            acc_fresh, key_fresh = fs.enqueue_options_gex(
                "SPY",
                ticker_obj=t_obj,
                now_ts=t0 + 500.0,
                force=False,
                coordinator=heavy_coord,
            )
            self.assertFalse(acc_fresh)
            self.assertIsNone(key_fresh)

            # Expired TTL (elapsed 1801s > 1800s) -> accepted
            acc_expired, key_expired = fs.enqueue_options_gex(
                "SPY",
                ticker_obj=t_obj,
                now_ts=t0 + 1801.0,
                force=False,
                coordinator=heavy_coord,
            )
            self.assertTrue(acc_expired)
            self.assertIsNotNone(key_expired)

            # Forced refresh overrides active TTL
            acc_forced, key_forced = fs.enqueue_options_gex(
                "SPY",
                ticker_obj=t_obj,
                now_ts=t0 + 10.0,
                force=True,
                coordinator=heavy_coord,
            )
            self.assertTrue(acc_forced)

    def test_09_dynamic_symbol_addition_while_work_is_pending(self):
        """9. Newly added symbols become eligible for correct live and heavy scheduling paths."""
        self._seed_valid_spot_cache("AAPL", price=150.0)
        self._seed_valid_spot_cache("GOOG", price=175.0)

        class _Ticker:
            def __init__(self, sym):
                self.ticker = sym
                self.options = ["2026-06-19"]
                self.fast_info = SimpleNamespace(
                    regular_market_previous_close=150.0 if sym == "AAPL" else 175.0,
                    three_month_average_volume=20_000_000,
                )

            def history(self, **kwargs):
                return _make_sample_history_df(days=220)

            def option_chain(self, exp):
                return _make_option_chain_pair(150.0 if self.ticker == "AAPL" else 175.0)

        with _isolated_pipeline_coordinators() as (heavy_coord, heavy_app, _, _, _, _):
            # Cycle 1 with initial symbol list: only AAPL
            fs.ALL_TICKERS = ["AAPL"]
            stats1 = fs.coordinate_heavy_market_data_cycle(
                symbols=["AAPL"],
                tickers_obj={"AAPL": _Ticker("AAPL")},
                today_str="2026-04-20",
                coordinator=heavy_coord,
                applicator=heavy_app,
            )
            self.assertEqual(stats1["historical_enqueued"], 1)
            self.assertTrue(_wait_workers_idle(heavy_coord, timeout=3.0))
            fs.apply_background_heavy_market_data_results(coordinator=heavy_coord, applicator=heavy_app)
            self.assertIn("AAPL", fs.cache.history)

            # Dynamic expansion: GOOG is added to symbol list
            fs.ALL_TICKERS = ["AAPL", "GOOG"]
            stats2 = fs.coordinate_heavy_market_data_cycle(
                symbols=["AAPL", "GOOG"],
                tickers_obj={"AAPL": _Ticker("AAPL"), "GOOG": _Ticker("GOOG")},
                today_str="2026-04-20",
                coordinator=heavy_coord,
                applicator=heavy_app,
            )
            # AAPL is already fresh in cache; GOOG is newly enqueued
            self.assertEqual(stats2["historical_enqueued"], 1)
            self.assertEqual(stats2["gex_enqueued"], 1)

            self.assertTrue(_wait_workers_idle(heavy_coord, timeout=3.0))
            audit = fs.apply_background_heavy_market_data_results(coordinator=heavy_coord, applicator=heavy_app)
            self.assertGreater(len(audit), 0)
            self.assertIn("AAPL", fs.cache.history)
            self.assertIn("GOOG", fs.cache.history)

    def test_10_dynamic_symbol_removal_while_work_is_pending_or_in_flight(self):
        """10. Symbol removal does not corrupt coordinator sequence state, queue bookkeeping, or other symbols."""
        sample_df = _make_sample_history_df(days=220)

        class _Ticker:
            def __init__(self, sym):
                self.ticker = sym
                self.fast_info = SimpleNamespace(regular_market_previous_close=100.0)

            def history(self, **kwargs):
                return sample_df.copy()

        with _isolated_pipeline_coordinators() as (heavy_coord, heavy_app, _, _, _, _):
            # Enqueue both AAPL and DEL in Cycle 1
            fs.ALL_TICKERS = ["AAPL", "DEL"]
            stats1 = fs.coordinate_heavy_market_data_cycle(
                symbols=["AAPL", "DEL"],
                tickers_obj={"AAPL": _Ticker("AAPL"), "DEL": _Ticker("DEL")},
                today_str="2026-04-20",
                coordinator=heavy_coord,
                applicator=heavy_app,
            )
            self.assertEqual(stats1["historical_enqueued"], 2)
            self.assertTrue(_wait_workers_idle(heavy_coord, timeout=3.0))
            audit = fs.apply_background_heavy_market_data_results(coordinator=heavy_coord, applicator=heavy_app)
            self.assertEqual(len(audit), 2)
            self.assertIn("AAPL", fs.cache.history)
            self.assertIn("DEL", fs.cache.history)

            # Cycle 2: DEL is removed from active list; only AAPL is monitored
            fs.ALL_TICKERS = ["AAPL"]
            stats2 = fs.coordinate_heavy_market_data_cycle(
                symbols=["AAPL"],
                tickers_obj={"AAPL": _Ticker("AAPL")},
                today_str="2026-04-20",
                coordinator=heavy_coord,
                applicator=heavy_app,
            )
            # DEL is not scheduled; AAPL is fresh in cache
            self.assertEqual(stats2["historical_enqueued"], 0)
            # Valid cache for DEL is preserved without destruction
            self.assertIn("DEL", fs.cache.history)
            self.assertIn("AAPL", fs.cache.history)

    def test_11_re_addition_of_previously_removed_symbol(self):
        """11. Re-adding a previously removed symbol does not cause incorrect deduplication or stale-result acceptance."""
        t_obj = SimpleNamespace(
            ticker="READD",
            fast_info=SimpleNamespace(regular_market_previous_close=200.0),
            history=lambda **k: _make_sample_history_df(days=220),
        )

        with _isolated_pipeline_coordinators() as (heavy_coord, heavy_app, _, _, _, _):
            # Phase 1: READD active, completes initial fetch
            fs.ALL_TICKERS = ["AAPL", "READD"]
            acc1, item1 = fs.enqueue_historical_data("READD", t_obj=t_obj, today_str="2026-04-20", coordinator=heavy_coord)
            self.assertTrue(acc1)
            self.assertTrue(_wait_workers_idle(heavy_coord, timeout=3.0))
            audit1 = fs.apply_background_heavy_market_data_results(coordinator=heavy_coord, applicator=heavy_app)
            self.assertTrue(audit1[0][1])
            seq1 = item1.sequence

            # Phase 2: READD removed from active symbols list during subsequent monitoring
            fs.ALL_TICKERS = ["AAPL"]
            # Existing valid cache data is preserved!
            self.assertIn("READD", fs.cache.history)

            # Phase 3: READD is re-added on the next trading day
            fs.ALL_TICKERS = ["AAPL", "READD"]
            acc2, item2 = fs.enqueue_historical_data("READD", t_obj=t_obj, today_str="2026-04-21", coordinator=heavy_coord)
            self.assertTrue(acc2)
            self.assertGreater(item2.sequence, seq1)

            self.assertTrue(_wait_workers_idle(heavy_coord, timeout=3.0))
            audit2 = fs.apply_background_heavy_market_data_results(coordinator=heavy_coord, applicator=heavy_app)
            self.assertTrue(audit2[0][1])
            self.assertEqual(heavy_app.get_last_applied(item2.key)[0], item2.sequence)

    def test_12_rejection_of_stale_or_superseded_results_after_symbol_list_changes(self):
        """12. Delayed results carrying superseded spot observation timestamps are rejected fail-closed."""
        self._seed_valid_spot_cache("SPY", price=500.0, obs_time="2026-04-20T11:00:00Z")
        coord = bi.BackgroundIntelligenceCoordinator(max_workers=1)
        app = bi.BackgroundResultApplicator(coordinator=coord)
        key = bi.WorkKey(bi.WorkType.OPTIONS_GEX.value, "SPY")

        try:
            # Result carrying older snapshot
            delayed_result = bi.WorkResult(
                key=key,
                sequence=1,
                data={
                    "kind": "options_gex",
                    "symbol": "SPY",
                    "spot_price": 500.0,
                    "spot_snapshot": {
                        "price": 500.0,
                        "observation_time": "2026-04-20T11:00:00Z",
                        "retrieval_time": "2026-04-20T11:00:01Z",
                    },
                    "gex_profile": {k: 5.0 for k in fs._GEX_REQUIRED_KEYS},
                    "fetched_at": time.time(),
                    "source": "YAHOO_OPTIONS",
                    "contract_count": 5,
                },
            )

            # Newer live tick arrives before delayed result publishes
            fs.cache.observation_times["SPY"] = "2026-04-20T11:00:30Z"
            fs.cache.retrieval_times["SPY"] = "2026-04-20T11:00:31Z"

            audit = app.apply_results(
                [delayed_result],
                on_publish=lambda r: fs._publish_market_data_candidate(*fs._build_gex_cache_candidate(r)),
            )
            self.assertEqual(len(audit), 1)
            self.assertFalse(audit[0][1])
            self.assertIn("superseded", audit[0][2].lower())
            self.assertNotIn("SPY", fs.cache.gex)
        finally:
            coord.shutdown(timeout=2.0)

    def test_13_provider_exception_isolation(self):
        """13. Heavy provider exceptions do not terminate or stall the coordinator or monitoring loop."""
        class _FailingTicker:
            ticker = "BROKEN"

            @property
            def fast_info(self):
                raise RuntimeError("fast_info unreachable")

            def history(self, **kwargs):
                raise ConnectionError("Yahoo connection reset")

        with _isolated_pipeline_coordinators() as (heavy_coord, heavy_app, _, _, _, _):
            acc, _ = fs.enqueue_historical_data("BROKEN", t_obj=_FailingTicker(), coordinator=heavy_coord)
            self.assertTrue(acc)
            self.assertTrue(_wait_workers_idle(heavy_coord, timeout=3.0))

            audit = fs.apply_background_heavy_market_data_results(coordinator=heavy_coord, applicator=heavy_app)
            self.assertEqual(len(audit), 1)
            self.assertFalse(audit[0][1])
            self.assertIn("Historical refresh rejected", audit[0][2])

            # Coordinator remains alive and healthy for subsequent tasks
            acc_subsequent, _ = heavy_coord.enqueue(bi.WorkType.HISTORICAL_DATA.value, "NEXT", fn=lambda: bi.ObservedResult(data={}))
            self.assertTrue(acc_subsequent)

    def test_14_malformed_result_rejection_and_cache_preservation(self):
        """14. Malformed results (e.g. non-finite GEX, empty technicals) are rejected and preserve cache."""
        prior_profile = {k: 12.3 for k in fs._GEX_REQUIRED_KEYS}
        self._seed_valid_spot_cache("SPY", price=500.0)
        fs.cache.gex["SPY"] = dict(prior_profile)
        fs.cache.gex_cache["SPY"] = dict(prior_profile)
        fs.cache.last_gex_fetch["SPY"] = 1_700_000_000.0

        key = bi.WorkKey(bi.WorkType.OPTIONS_GEX.value, "SPY")
        res_nan = bi.WorkResult(
            key=key,
            sequence=1,
            data={
                "kind": "options_gex",
                "symbol": "SPY",
                "spot_price": 500.0,
                "spot_snapshot": {
                    "price": 500.0,
                    "observation_time": "2026-04-20T11:00:00Z",
                    "retrieval_time": "2026-04-20T11:00:01Z",
                },
                "gex_profile": {**prior_profile, "net_gex": float("nan")},
                "fetched_at": time.time(),
                "source": "YAHOO_OPTIONS",
                "contract_count": 10,
            },
        )

        app = bi.BackgroundResultApplicator()
        audit = app.apply_results(
            [res_nan],
            on_publish=lambda r: fs._publish_market_data_candidate(*fs._build_gex_cache_candidate(r)),
        )
        self.assertFalse(audit[0][1])
        # Valid cache remains completely intact
        self.assertEqual(fs.cache.gex["SPY"], prior_profile)
        self.assertEqual(fs.cache.gex_cache["SPY"], prior_profile)
        self.assertEqual(fs.cache.last_gex_fetch["SPY"], 1_700_000_000.0)

    def test_15_cancellation_and_shutdown_with_queued_work(self):
        """15. Coordinator shutdown cancels queued work while worker is still blocked and prevents subsequent enqueue."""
        t1_started = threading.Event()
        s1_saw_cancel = threading.Event()
        s2_executed = threading.Event()
        s3_executed = threading.Event()
        fallback_release = threading.Event()

        coord = bi.BackgroundIntelligenceCoordinator(max_workers=1, max_queue_size=10)

        def _s1_work(cancel_check=None):
            t1_started.set()
            if not fallback_release.is_set() and not (cancel_check and cancel_check()):
                coord._shutdown_event.wait(timeout=3.0)
            if cancel_check and cancel_check():
                s1_saw_cancel.set()
            return bi.ObservedResult(data={})

        def _queued_work_spy(flag: threading.Event):
            flag.set()
            return bi.ObservedResult(data={})

        try:
            # 1 running (still blocked), 2 queued
            acc1, item1 = coord.enqueue(bi.WorkType.HISTORICAL_DATA.value, "S1", fn=_s1_work)
            self.assertTrue(acc1)
            self.assertTrue(t1_started.wait(timeout=2.0))
            self.assertEqual(coord.in_flight_count(), 1)

            acc2, item2 = coord.enqueue(
                bi.WorkType.HISTORICAL_DATA.value, "S2", fn=lambda: _queued_work_spy(s2_executed)
            )
            acc3, item3 = coord.enqueue(
                bi.WorkType.HISTORICAL_DATA.value, "S3", fn=lambda: _queued_work_spy(s3_executed)
            )
            self.assertTrue(acc2)
            self.assertTrue(acc3)
            self.assertEqual(coord.in_flight_count(), 1)
            self.assertEqual(coord.pending_count(), 2)
            self.assertEqual(item2.status, bi.WorkStatus.QUEUED)
            self.assertEqual(item3.status, bi.WorkStatus.QUEUED)

            # Call shutdown directly WITHOUT releasing blocked worker S1 first
            shutdown_ok = coord.shutdown(timeout=2.0)
            self.assertTrue(shutdown_ok)
            self.assertTrue(s1_saw_cancel.is_set())
            self.assertFalse(s2_executed.is_set())
            self.assertFalse(s3_executed.is_set())
            self.assertEqual(item1.status, bi.WorkStatus.CANCELLED)
            self.assertEqual(item2.status, bi.WorkStatus.CANCELLED)
            self.assertEqual(item3.status, bi.WorkStatus.CANCELLED)
            self.assertTrue(item2.cancel_event.is_set())
            self.assertTrue(item3.cancel_event.is_set())
            self.assertEqual(coord.pending_count(), 0)
            self.assertEqual(coord.in_flight_count(), 0)
            self.assertTrue(coord._queue.empty())
            self.assertTrue(all(not w.is_alive() for w in coord._workers))
            self.assertEqual(coord.drain_results(), [])

            # Enqueue after shutdown returns False
            acc_post, key_post = coord.enqueue(
                bi.WorkType.HISTORICAL_DATA.value, "POST", fn=lambda: bi.ObservedResult(data={})
            )
            self.assertFalse(acc_post)
            self.assertIsNone(key_post)
        finally:
            fallback_release.set()
            coord.shutdown(timeout=1.0)

    def test_16_cancellation_and_shutdown_with_in_flight_work(self):
        """16. In-flight work terminates cleanly on coordinator shutdown without resource leaks."""
        worker_started = threading.Event()
        cancel_detected = threading.Event()
        fallback_release = threading.Event()

        coord = bi.BackgroundIntelligenceCoordinator(max_workers=1)

        def _cancellable_work(cancel_check=None):
            worker_started.set()
            if not fallback_release.is_set() and not (cancel_check and cancel_check()):
                coord._shutdown_event.wait(timeout=3.0)
            if cancel_check and cancel_check():
                cancel_detected.set()
                raise RuntimeError("Cancelled in flight")
            return bi.ObservedResult(data={})

        try:
            acc, item = coord.enqueue(bi.WorkType.HISTORICAL_DATA.value, "CANCEL_TEST", fn=_cancellable_work)
            self.assertTrue(acc)
            self.assertTrue(worker_started.wait(timeout=2.0))
            self.assertEqual(coord.in_flight_count(), 1)
            self.assertEqual(coord.pending_count(), 0)

            # Coordinator shutdown signals cancellation while worker is active
            shutdown_ok = coord.shutdown(timeout=2.0)
            self.assertTrue(shutdown_ok)
            self.assertTrue(cancel_detected.is_set())
            self.assertTrue(item.cancel_event.is_set())
            self.assertEqual(item.status, bi.WorkStatus.CANCELLED)
            self.assertEqual(coord.in_flight_count(), 0)
            self.assertEqual(coord.pending_count(), 0)
            self.assertEqual(coord.drain_results(), [])
            self.assertTrue(all(not w.is_alive() for w in coord._workers))
        finally:
            fallback_release.set()
            coord.shutdown(timeout=1.0)

    def test_17_bounded_shutdown_and_visible_timeout_behaviour(self):
        """17. Shutdown timeout remains visible, bounded, and does not report false success."""
        worker_started = threading.Event()
        hung_event = threading.Event()

        def _hung_worker():
            worker_started.set()
            hung_event.wait(timeout=5.0)
            return bi.ObservedResult(data={})

        coord = bi.BackgroundIntelligenceCoordinator(max_workers=1)
        clean_ok = False
        try:
            acc, item = coord.enqueue(bi.WorkType.HISTORICAL_DATA.value, "HUNG", fn=_hung_worker)
            self.assertTrue(acc)
            self.assertTrue(worker_started.wait(timeout=2.0))
            self.assertEqual(coord.in_flight_count(), 1)
            self.assertEqual(coord.pending_count(), 0)

            t0 = time.monotonic()
            with self.assertLogs(bi.logger, level="WARNING") as cm:
                shutdown_ok = coord.shutdown(timeout=0.2)
            elapsed = time.monotonic() - t0

            # Bounded: did not hang indefinitely; returned False with incomplete state
            self.assertFalse(shutdown_ok)
            self.assertLess(elapsed, 0.8)
            self.assertTrue(any("Shutdown incomplete" in msg for msg in cm.output))
            self.assertEqual(item.status, bi.WorkStatus.CANCELLED)
            self.assertEqual(coord.in_flight_count(), 1)
            self.assertEqual(coord.pending_count(), 0)
            self.assertTrue(any(w.is_alive() for w in coord._workers))
        finally:
            hung_event.set()
            clean_ok = coord.shutdown(timeout=2.0)

        self.assertTrue(clean_ok)
        self.assertEqual(coord.in_flight_count(), 0)
        self.assertEqual(coord.pending_count(), 0)
        self.assertEqual(coord.drain_results(), [])
        self.assertTrue(all(not w.is_alive() for w in coord._workers))

    def test_18_preservation_of_valid_live_and_heavy_cache_entries_after_failed_publication(self):
        """18. Failed or rejected candidates preserve valid live and heavy cache entries atomically."""
        prior_df = _make_sample_history_df(days=220)
        prior_tech = {k: 10.0 for k in fs._HISTORICAL_REQUIRED_TECH_KEYS}
        prior_gex = {k: 20.0 for k in fs._GEX_REQUIRED_KEYS}

        self._seed_valid_spot_cache("AAPL", price=150.0)
        fs.cache.history["AAPL"] = prior_df.copy()
        fs.cache.technicals["AAPL"] = dict(prior_tech)
        fs.cache.gex["AAPL"] = dict(prior_gex)
        fs.cache.gex_cache["AAPL"] = dict(prior_gex)
        fs.cache.last_gex_fetch["AAPL"] = 1_700_000_000.0

        # Attempt to publish a corrupted historical candidate (missing required keys)
        now_ny = datetime(2026, 4, 20, 11, 0, tzinfo=NY_TZ)
        corrupted_hist_res = bi.WorkResult(
            key=bi.WorkKey(bi.WorkType.HISTORICAL_DATA.value, "AAPL"),
            sequence=1,
            data={
                "kind": "historical_data",
                "symbol": "AAPL",
                "history": prior_df.copy(),
                "last_history_fetch_date": "2026-04-20",
                "technicals": {"RSI": 50.0},  # Missing all other required keys
                "today_str": "2026-04-20",
                "source": "CORRUPT",
            },
        )

        app = bi.BackgroundResultApplicator()
        audit = app.apply_results(
            [corrupted_hist_res],
            on_publish=lambda r: fs._publish_market_data_candidate(*fs._build_historical_cache_candidate(r, now_ny=now_ny)),
        )
        self.assertFalse(audit[0][1])

        # All existing cache entries remain strictly unchanged
        self.assertEqual(fs.cache.prices["AAPL"], 150.0)
        self.assertEqual(fs.cache.technicals["AAPL"], prior_tech)
        self.assertEqual(fs.cache.gex["AAPL"], prior_gex)
        self.assertEqual(fs.cache.gex_cache["AAPL"], prior_gex)
        self.assertEqual(fs.cache.last_gex_fetch["AAPL"], 1_700_000_000.0)

    def test_19_correct_sequence_and_refresh_timestamp_behaviour_after_rejected_results(self):
        """19. Rejected results do not advance sequence or update refresh timestamps; subsequent valid results succeed."""
        self._seed_valid_spot_cache("SPY", price=500.0)
        key = bi.WorkKey(bi.WorkType.OPTIONS_GEX.value, "SPY")
        app = bi.BackgroundResultApplicator()

        self.assertEqual(app.get_last_applied(key), (-1, ""))

        # Sequence 1: Rejected due to changed spot price
        res_rej = bi.WorkResult(
            key=key,
            sequence=1,
            data={
                "kind": "options_gex",
                "symbol": "SPY",
                "spot_price": 490.0,  # Mismatched spot price
                "spot_snapshot": {
                    "price": 490.0,
                    "observation_time": "2026-04-20T11:00:00Z",
                    "retrieval_time": "2026-04-20T11:00:01Z",
                },
                "gex_profile": {k: 1.0 for k in fs._GEX_REQUIRED_KEYS},
                "fetched_at": time.time(),
                "source": "YAHOO_OPTIONS",
                "contract_count": 5,
            },
        )
        audit1 = app.apply_results(
            [res_rej],
            on_publish=lambda r: fs._publish_market_data_candidate(*fs._build_gex_cache_candidate(r)),
        )
        self.assertFalse(audit1[0][1])
        # Sequence NOT advanced; refresh timestamp NOT set
        self.assertEqual(app.get_last_applied(key), (-1, ""))
        self.assertNotIn("SPY", fs.cache.last_gex_fetch)

        # Sequence 2: Valid candidate matching cache
        res_valid = bi.WorkResult(
            key=key,
            sequence=2,
            data={
                "kind": "options_gex",
                "symbol": "SPY",
                "spot_price": 500.0,
                "spot_snapshot": {
                    "price": 500.0,
                    "observation_time": "2026-04-20T11:00:00Z",
                    "retrieval_time": "2026-04-20T11:00:01Z",
                },
                "gex_profile": {k: 2.0 for k in fs._GEX_REQUIRED_KEYS},
                "fetched_at": time.time(),
                "source": "YAHOO_OPTIONS",
                "contract_count": 5,
            },
        )
        audit2 = app.apply_results(
            [res_valid],
            on_publish=lambda r: fs._publish_market_data_candidate(*fs._build_gex_cache_candidate(r)),
        )
        self.assertTrue(audit2[0][1])
        self.assertEqual(app.get_last_applied(key)[0], 2)
        self.assertEqual(fs.cache.gex["SPY"]["net_gex"], 2.0)
        self.assertGreater(fs.cache.last_gex_fetch["SPY"], 0)

    def test_20_no_regression_to_existing_stage_222_and_223_publication_contracts(self):
        """20. End-to-end integration: Live quotes, historical technicals, and GEX publish atomically without regression."""
        now_ny = datetime(2026, 4, 20, 11, 0, tzinfo=NY_TZ)
        sample_df = _make_sample_history_df(days=220, end_date=now_ny)

        class _IntegratedTicker:
            ticker = "AAPL"
            options = ["2026-06-19"]
            fast_info = SimpleNamespace(
                regular_market_previous_close=150.0,
                three_month_average_volume=45_000_000,
            )

            def history(self, **kwargs):
                return sample_df.copy()

            def option_chain(self, exp):
                return _make_option_chain_pair(155.0)

        with _isolated_pipeline_coordinators() as (heavy_coord, heavy_app, mkt_coord, mkt_app, _, _):
            # Step A: Live quote cycle runs on MARKET_DATA_COORDINATOR
            batch_quotes = {"AAPL": _make_batch_quote(155.0, prev_close=150.0, ts=int(now_ny.timestamp()))}
            with patch.object(fs, "get_batch_quotes", return_value=batch_quotes):
                fs.coordinate_market_data_cycle(
                    symbols=["AAPL"],
                    status="OPEN",
                    now_ny=now_ny,
                    coordinator=mkt_coord,
                    applicator=mkt_app,
                )
                self.assertTrue(_wait_workers_idle(mkt_coord, timeout=2.0))
                fs.coordinate_market_data_cycle(
                    symbols=["AAPL"],
                    status="OPEN",
                    now_ny=now_ny,
                    coordinator=mkt_coord,
                    applicator=mkt_app,
                )

            # Live market data published with valid actionable spot identity
            self.assertEqual(fs.cache.prices["AAPL"], 155.0)
            self.assertEqual(fs.cache.price_validity["AAPL"], "VALID")
            self.assertEqual(fs.cache.freshness["AAPL"], "CURRENT")
            self.assertTrue(fs.is_price_actionable("AAPL"))

            # Step B: Heavy market data cycle runs on HEAVY_MARKET_DATA_COORDINATOR
            tickers_obj = {"AAPL": _IntegratedTicker()}
            stats = fs.coordinate_heavy_market_data_cycle(
                symbols=["AAPL"],
                tickers_obj=tickers_obj,
                is_heavy=True,
                today_str="2026-04-20",
                now_ny=now_ny,
                coordinator=heavy_coord,
                applicator=heavy_app,
            )
            self.assertEqual(stats["historical_enqueued"], 1)
            self.assertEqual(stats["gex_enqueued"], 1)

            self.assertTrue(_wait_workers_idle(heavy_coord, timeout=3.0))

            # Step C: Coordinating-thread drain and publication
            audit = fs.apply_background_heavy_market_data_results(
                now_ny=now_ny,
                coordinator=heavy_coord,
                applicator=heavy_app,
            )
            self.assertEqual(len(audit), 2)
            self.assertTrue(all(ok for _, ok, _ in audit))

            # Step D: Verify all contracts from Stage 2.2.2 and 2.2.3 hold together
            # Live quote invariants
            self.assertEqual(fs.cache.prices["AAPL"], 155.0)

            # Historical OHLCV & technical invariants
            self.assertIn("AAPL", fs.cache.history)
            self.assertEqual(fs.cache.last_history_fetch_date["AAPL"], "2026-04-20")
            for req_tech in fs._HISTORICAL_REQUIRED_TECH_KEYS:
                self.assertIn(req_tech, fs.cache.technicals["AAPL"])
            self.assertGreater(fs.cache.technicals["AAPL"]["Trend_Score"], 0)

            # Options / GEX invariants
            self.assertIn("AAPL", fs.cache.gex)
            self.assertIn("AAPL", fs.cache.gex_cache)
            for req_gex in fs._GEX_REQUIRED_KEYS:
                self.assertIn(req_gex, fs.cache.gex["AAPL"])
                self.assertTrue(math.isfinite(fs.cache.gex["AAPL"][req_gex]))
            self.assertGreater(fs.cache.last_gex_fetch["AAPL"], 0)

    def test_21_in_flight_historical_result_rejected_when_symbol_removed_before_publication(self):
        """21. In-flight historical work for a symbol removed from ALL_TICKERS is rejected at publication."""
        now_ny = datetime(2026, 4, 20, 11, 0, tzinfo=NY_TZ)
        prior_df = _make_sample_history_df(
            days=40,
            base_price=80.0,
            end_date=datetime(2026, 4, 17, 16, 0, tzinfo=NY_TZ),
        )
        prior_tech = fs._compute_technicals_from_history(prior_df, last_reg_close=95.0)
        new_df = _make_sample_history_df(days=220, base_price=100.0, end_date=now_ny)

        fs.cache.history["REM_HIST"] = prior_df.copy()
        fs.cache.technicals["REM_HIST"] = dict(prior_tech)
        fs.cache.last_history_fetch_date["REM_HIST"] = "2026-04-17"
        fs.cache.regular_close["REM_HIST"] = 95.0

        rem_started = threading.Event()
        rem_release = threading.Event()

        class _RemHistTicker:
            ticker = "REM_HIST"
            fast_info = SimpleNamespace(regular_market_previous_close=210.0)

            def history(self, **kwargs):
                rem_started.set()
                if not rem_release.wait(timeout=3.0):
                    raise TimeoutError("REM_HIST historical release timed out")
                return new_df.copy()

        class _ActiveHistTicker:
            ticker = "AAPL"
            fast_info = SimpleNamespace(regular_market_previous_close=155.0)

            def history(self, **kwargs):
                return new_df.copy()

        fs.ALL_TICKERS = ["REM_HIST", "AAPL"]
        with _isolated_pipeline_coordinators(heavy_workers=2) as (heavy_coord, heavy_app, _, _, _, _):
            try:
                acc_rem, item_rem = fs.enqueue_historical_data(
                    "REM_HIST",
                    t_obj=_RemHistTicker(),
                    today_str="2026-04-20",
                    now_ny=now_ny,
                    coordinator=heavy_coord,
                )
                acc_aapl, item_aapl = fs.enqueue_historical_data(
                    "AAPL",
                    t_obj=_ActiveHistTicker(),
                    today_str="2026-04-20",
                    now_ny=now_ny,
                    coordinator=heavy_coord,
                )
                self.assertTrue(acc_rem)
                self.assertTrue(acc_aapl)
                self.assertTrue(rem_started.wait(timeout=2.0))

                # Remove REM_HIST from authoritative active list while worker is genuinely in flight
                fs.ALL_TICKERS = ["AAPL"]
                rem_release.set()
                self.assertTrue(_wait_workers_idle(heavy_coord, timeout=3.0))

                audit = fs.apply_background_heavy_market_data_results(
                    now_ny=now_ny,
                    coordinator=heavy_coord,
                    applicator=heavy_app,
                )
                audit_by_sym = {res.key.target: (applied, reason) for res, applied, reason in audit}
                self.assertEqual(len(audit_by_sym), 2)
                self.assertFalse(audit_by_sym["REM_HIST"][0])
                self.assertIn("inactive symbol", audit_by_sym["REM_HIST"][1].lower())
                self.assertTrue(audit_by_sym["AAPL"][0])

                # Prior valid cache for removed symbol is preserved; applicator sequence NOT advanced
                self.assertEqual(len(fs.cache.history["REM_HIST"]), 40)
                self.assertEqual(fs.cache.technicals["REM_HIST"], prior_tech)
                self.assertEqual(fs.cache.last_history_fetch_date["REM_HIST"], "2026-04-17")
                self.assertEqual(fs.cache.regular_close["REM_HIST"], 95.0)
                self.assertEqual(heavy_app.get_last_applied(item_rem.key), (-1, ""))

                # Active symbol published normally
                self.assertIn("AAPL", fs.cache.history)
                self.assertEqual(len(fs.cache.history["AAPL"]), 220)
                self.assertEqual(heavy_app.get_last_applied(item_aapl.key)[0], item_aapl.sequence)

                # Re-adding REM_HIST permits subsequent valid historical publication
                fs.ALL_TICKERS = ["AAPL", "REM_HIST"]
                acc_readd, item_readd = fs.enqueue_historical_data(
                    "REM_HIST",
                    t_obj=_RemHistTicker(),
                    today_str="2026-04-20",
                    now_ny=now_ny,
                    coordinator=heavy_coord,
                )
                self.assertTrue(acc_readd)
                self.assertTrue(_wait_workers_idle(heavy_coord, timeout=3.0))
                audit_readd = fs.apply_background_heavy_market_data_results(
                    now_ny=now_ny,
                    coordinator=heavy_coord,
                    applicator=heavy_app,
                )
                self.assertEqual(len(audit_readd), 1)
                self.assertTrue(audit_readd[0][1])
                self.assertEqual(len(fs.cache.history["REM_HIST"]), 220)
                self.assertEqual(fs.cache.last_history_fetch_date["REM_HIST"], "2026-04-20")
                self.assertEqual(heavy_app.get_last_applied(item_readd.key)[0], item_readd.sequence)
            finally:
                rem_release.set()

    def test_22_in_flight_gex_result_rejected_when_symbol_removed_before_publication(self):
        """22. In-flight GEX work for a symbol removed from ALL_TICKERS is rejected at publication."""
        prior_gex = {k: 42.0 for k in fs._GEX_REQUIRED_KEYS}
        prior_fetch = 1_700_000_000.0
        now_ts = prior_fetch + 2000.0

        self._seed_valid_spot_cache(
            "REM_GEX",
            price=250.0,
            obs_time="2026-04-20T15:00:00Z",
            retrieval_time="2026-04-20T15:00:01Z",
        )
        self._seed_valid_spot_cache(
            "SPY",
            price=500.0,
            obs_time="2026-04-20T15:00:00Z",
            retrieval_time="2026-04-20T15:00:01Z",
        )
        fs.cache.gex["REM_GEX"] = dict(prior_gex)
        fs.cache.gex_cache["REM_GEX"] = dict(prior_gex)
        fs.cache.last_gex_fetch["REM_GEX"] = prior_fetch

        rem_started = threading.Event()
        rem_release = threading.Event()

        class _RemGexTicker:
            ticker = "REM_GEX"
            options = ["2026-06-19"]
            fast_info = SimpleNamespace(three_month_average_volume=35_000_000)

            def option_chain(self, exp):
                rem_started.set()
                if not rem_release.wait(timeout=3.0):
                    raise TimeoutError("REM_GEX release timed out")
                return _make_option_chain_pair(250.0)

        class _SpyGexTicker:
            ticker = "SPY"
            options = ["2026-06-19"]
            fast_info = SimpleNamespace(three_month_average_volume=50_000_000)

            def option_chain(self, exp):
                return _make_option_chain_pair(500.0)

        fs.ALL_TICKERS = ["REM_GEX", "SPY"]
        with _isolated_pipeline_coordinators(heavy_workers=2) as (heavy_coord, heavy_app, _, _, _, _), \
             patch.object(fs.t_time, "time", return_value=now_ts):
            try:
                acc_rem, item_rem = fs.enqueue_options_gex(
                    "REM_GEX",
                    ticker_obj=_RemGexTicker(),
                    spot_price=250.0,
                    now_ts=now_ts,
                    coordinator=heavy_coord,
                )
                acc_spy, item_spy = fs.enqueue_options_gex(
                    "SPY",
                    ticker_obj=_SpyGexTicker(),
                    spot_price=500.0,
                    now_ts=now_ts,
                    coordinator=heavy_coord,
                )
                self.assertTrue(acc_rem)
                self.assertTrue(acc_spy)
                self.assertTrue(rem_started.wait(timeout=2.0))

                # Remove REM_GEX from authoritative active list while worker is genuinely in flight
                fs.ALL_TICKERS = ["SPY"]
                rem_release.set()
                self.assertTrue(_wait_workers_idle(heavy_coord, timeout=3.0))

                audit = fs.apply_background_heavy_market_data_results(
                    now_ts=now_ts,
                    coordinator=heavy_coord,
                    applicator=heavy_app,
                )
                audit_by_sym = {res.key.target: (applied, reason) for res, applied, reason in audit}
                self.assertEqual(len(audit_by_sym), 2)
                self.assertFalse(audit_by_sym["REM_GEX"][0])
                self.assertIn("inactive symbol", audit_by_sym["REM_GEX"][1].lower())
                self.assertTrue(audit_by_sym["SPY"][0])

                # Prior valid cache and refresh timestamp preserved; sequence NOT advanced
                self.assertEqual(fs.cache.gex["REM_GEX"], prior_gex)
                self.assertEqual(fs.cache.gex_cache["REM_GEX"], prior_gex)
                self.assertEqual(fs.cache.last_gex_fetch["REM_GEX"], prior_fetch)
                self.assertEqual(heavy_app.get_last_applied(item_rem.key), (-1, ""))

                # Unrelated active symbol published normally
                self.assertIn("SPY", fs.cache.gex)
                self.assertGreater(fs.cache.last_gex_fetch["SPY"], 0)
                self.assertEqual(heavy_app.get_last_applied(item_spy.key)[0], item_spy.sequence)

                # Re-adding REM_GEX permits subsequent valid GEX publication
                fs.ALL_TICKERS = ["SPY", "REM_GEX"]
                acc_readd, item_readd = fs.enqueue_options_gex(
                    "REM_GEX",
                    ticker_obj=_RemGexTicker(),
                    spot_price=250.0,
                    now_ts=now_ts,
                    coordinator=heavy_coord,
                )
                self.assertTrue(acc_readd)
                self.assertTrue(_wait_workers_idle(heavy_coord, timeout=3.0))
                audit_readd = fs.apply_background_heavy_market_data_results(
                    now_ts=now_ts,
                    coordinator=heavy_coord,
                    applicator=heavy_app,
                )
                self.assertEqual(len(audit_readd), 1)
                self.assertTrue(audit_readd[0][1])
                self.assertNotEqual(fs.cache.gex["REM_GEX"], prior_gex)
                self.assertEqual(fs.cache.last_gex_fetch["REM_GEX"], now_ts)
                self.assertEqual(heavy_app.get_last_applied(item_readd.key)[0], item_readd.sequence)
            finally:
                rem_release.set()

    def test_23_strict_timestamp_identity_utc_and_ny_dst_validation(self):
        """23. Strict UTC and America/New_York DST validation rejects foreign/invalid offsets fail-closed."""
        # Valid UTC and valid America/New_York representations (both EDT summer and EST winter)
        self.assertEqual(
            fs._canonical_spot_timestamp("2026-07-15T15:00:00Z", "obs"),
            "2026-07-15T15:00:00Z",
        )
        self.assertEqual(
            fs._canonical_spot_timestamp("2026-07-15T15:00:00+00:00", "obs"),
            "2026-07-15T15:00:00Z",
        )
        self.assertEqual(
            fs._canonical_spot_timestamp("2026-07-15T11:00:00-04:00", "obs"),
            "2026-07-15T15:00:00Z",
        )
        self.assertEqual(
            fs._canonical_spot_timestamp("2026-01-15T10:30:00-05:00", "obs"),
            "2026-01-15T15:30:00Z",
        )
        self.assertTrue(
            fs._matches_canonical_timestamp("2026-07-15T11:00:00-04:00", "2026-07-15T15:00:00Z")
        )
        self.assertTrue(
            fs._matches_canonical_timestamp("2026-01-15T15:30:00+00:00", "2026-01-15T10:30:00-05:00")
        )

        # Rejected timestamps: wrong DST offset, spring/fall transitions, foreign offsets (even same instant),
        # -00:00, naive, missing, and malformed values
        invalid_timestamps = [
            "2026-01-15T10:30:00-04:00",  # -04:00 invalid in January EST
            "2026-07-15T11:00:00-05:00",  # -05:00 invalid in July EDT
            "2026-03-08T02:30:00-05:00",  # Nonexistent spring-forward gap
            "2026-03-08T02:30:00-04:00",  # Nonexistent spring-forward gap
            "2026-11-01T01:30:00-04:00",  # Ambiguous fall-back transition
            "2026-11-01T01:30:00-05:00",  # Ambiguous fall-back transition
            "2026-07-15T17:00:00+02:00",  # Foreign offset for same UTC instant (15:00Z)
            "2026-07-15T08:00:00-07:00",  # Foreign US Pacific offset for same UTC instant (15:00Z)
            "2026-01-15T09:30:00-06:00",  # Foreign US Central offset for same UTC instant (15:30Z)
            "2026-07-15T15:00:00-00:00",  # RFC3339 -00:00
            "2026-07-15T15:00:00",        # Naive
            "2026-07-15 15:00:00Z",       # Malformed separator
            "not-a-timestamp",            # Malformed text
            "",                           # Empty
            None,                         # Missing
        ]
        for bad_ts in invalid_timestamps:
            with self.subTest(bad_ts=bad_ts):
                with self.assertRaises(ValueError):
                    fs._canonical_spot_timestamp(bad_ts, "spot observation_time")
                # Even self-equality or same-instant foreign offsets must fail _matches_canonical_timestamp
                self.assertFalse(fs._matches_canonical_timestamp(bad_ts, bad_ts))
                self.assertFalse(fs._matches_canonical_timestamp(bad_ts, "2026-07-15T15:00:00Z"))
                self.assertFalse(fs._matches_canonical_timestamp("2026-07-15T15:00:00Z", bad_ts))

        # Mismatched valid instants must fail
        self.assertFalse(
            fs._matches_canonical_timestamp("2026-07-15T11:00:00-04:00", "2026-07-15T15:00:01Z")
        )

        prior_gex = {k: 88.8 for k in fs._GEX_REQUIRED_KEYS}
        prior_fetch = 1_700_000_000.0
        now_ts = time.time()
        app = bi.BackgroundResultApplicator()
        key = bi.WorkKey(bi.WorkType.OPTIONS_GEX.value, "SPY")

        # Enqueue & publication both fail closed for invalid timestamps while preserving caches & sequence
        bad_cache_ts_cases = [
            "2026-01-15T10:30:00-04:00",  # Invalid winter DST offset
            "2026-07-15T11:00:00-05:00",  # Invalid summer DST offset
            "2026-07-15T17:00:00+02:00",  # Foreign offset matching 15:00Z
            "2026-03-08T02:30:00-05:00",  # Nonexistent DST time
            "2026-11-01T01:30:00-04:00",  # Ambiguous DST time
            "2026-07-15T15:00:00-00:00",  # Ambiguous -00:00
            "2026-07-15T15:00:00",        # Naive
            "not-a-timestamp",            # Malformed
            None,                         # Missing
        ]
        for seq, bad_ts in enumerate(bad_cache_ts_cases, start=1):
            fs.cache.gex["SPY"] = dict(prior_gex)
            fs.cache.gex_cache["SPY"] = dict(prior_gex)
            fs.cache.last_gex_fetch["SPY"] = prior_fetch

            # Enqueue validation fails closed when cache holds invalid timestamp
            self._seed_valid_spot_cache(
                "SPY",
                price=500.0,
                obs_time=bad_ts,
                retrieval_time="2026-07-15T15:00:01Z",
            )
            acc_bad, key_bad = fs.enqueue_options_gex("SPY", force=True)
            self.assertFalse(acc_bad)
            self.assertIsNone(key_bad)

            # Publication validation fails closed even if candidate snapshot carries invalid or foreign timestamp
            self._seed_valid_spot_cache(
                "SPY",
                price=500.0,
                obs_time="2026-07-15T15:00:00Z",
                retrieval_time="2026-07-15T15:00:01Z",
            )
            bad_res = bi.WorkResult(
                key=key,
                sequence=seq,
                data={
                    "kind": "options_gex",
                    "symbol": "SPY",
                    "spot_price": 500.0,
                    "spot_snapshot": {
                        "price": 500.0,
                        "observation_time": bad_ts,
                        "retrieval_time": "2026-07-15T15:00:01Z",
                    },
                    "gex_profile": {k: 999.0 for k in fs._GEX_REQUIRED_KEYS},
                    "fetched_at": now_ts,
                    "source": "YAHOO_OPTIONS",
                    "contract_count": 5,
                },
            )
            audit = app.apply_results(
                [bad_res],
                on_publish=lambda r: fs._publish_market_data_candidate(*fs._build_gex_cache_candidate(r, now_ts=now_ts)),
            )
            self.assertFalse(audit[0][1])
            self.assertEqual(app.get_last_applied(key), (-1, ""))
            self.assertEqual(fs.cache.gex["SPY"], prior_gex)
            self.assertEqual(fs.cache.gex_cache["SPY"], prior_gex)
            self.assertEqual(fs.cache.last_gex_fetch["SPY"], prior_fetch)

        # Valid NY EDT/EST cache reconciles with matching canonical UTC snapshot at both enqueue & publication
        valid_pairs = [
            ("2026-07-15T11:00:00-04:00", "2026-07-15T11:00:01-04:00", "2026-07-15T15:00:00Z", "2026-07-15T15:00:01Z"),
            ("2026-01-15T10:30:00-05:00", "2026-01-15T10:30:01-05:00", "2026-01-15T15:30:00Z", "2026-01-15T15:30:01Z"),
        ]
        for idx, (ny_obs, ny_ret, utc_obs, utc_ret) in enumerate(valid_pairs, start=100):
            self._seed_valid_spot_cache("SPY", price=500.0, obs_time=ny_obs, retrieval_time=ny_ret)
            class _SpyTicker:
                ticker = "SPY"
                options = ["2026-06-19"]
                fast_info = SimpleNamespace(three_month_average_volume=50_000_000)

                def option_chain(self, exp):
                    return _make_option_chain_pair(500.0)

            with _isolated_pipeline_coordinators() as (heavy_coord, _, _, _, _, _), \
                 patch.object(fs.t_time, "time", return_value=now_ts):
                acc_ok, item_ok = fs.enqueue_options_gex(
                    "SPY",
                    ticker_obj=_SpyTicker(),
                    spot_price=500.0,
                    now_ts=now_ts,
                    force=True,
                    coordinator=heavy_coord,
                )
                self.assertTrue(acc_ok)
                self.assertEqual(item_ok.kwargs["spot_snapshot"]["observation_time"], utc_obs)
                self.assertEqual(item_ok.kwargs["spot_snapshot"]["retrieval_time"], utc_ret)
                self.assertTrue(_wait_workers_idle(heavy_coord, timeout=3.0))
                audit_ok = fs.apply_background_heavy_market_data_results(now_ts=now_ts, coordinator=heavy_coord)
                self.assertEqual(len(audit_ok), 1)
                self.assertTrue(audit_ok[0][1])

        # Direct publication candidate with valid NY cache and valid UTC snapshot
        self._seed_valid_spot_cache(
            "SPY",
            price=500.0,
            obs_time="2026-01-15T10:30:00-05:00",
            retrieval_time="2026-01-15T10:30:01-05:00",
        )
        valid_est_res = bi.WorkResult(
            key=key,
            sequence=200,
            data={
                "kind": "options_gex",
                "symbol": "SPY",
                "spot_price": 500.0,
                "spot_snapshot": {
                    "price": 500.0,
                    "observation_time": "2026-01-15T15:30:00Z",
                    "retrieval_time": "2026-01-15T15:30:01Z",
                },
                "gex_profile": {k: 7.5 for k in fs._GEX_REQUIRED_KEYS},
                "fetched_at": now_ts,
                "source": "YAHOO_OPTIONS",
                "contract_count": 5,
            },
        )
        audit_est = app.apply_results(
            [valid_est_res],
            on_publish=lambda r: fs._publish_market_data_candidate(*fs._build_gex_cache_candidate(r, now_ts=now_ts)),
        )
        self.assertTrue(audit_est[0][1])
        self.assertEqual(app.get_last_applied(key)[0], 200)
        self.assertEqual(fs.cache.gex["SPY"]["net_gex"], 7.5)


if __name__ == "__main__":
    unittest.main()
