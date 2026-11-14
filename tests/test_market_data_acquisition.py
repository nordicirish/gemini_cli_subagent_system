import contextlib
import inspect
import os
import sys
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "python")))

import background_intelligence as bi
import fetch_stocks as fs


NY_TZ = ZoneInfo("America/New_York")
UTC_TZ = timezone.utc


def _epoch_after(base_dt: datetime, offset_seconds: int = 0) -> int:
    return int((base_dt + timedelta(seconds=offset_seconds)).timestamp())


def _utc_iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC_TZ).isoformat().replace("+00:00", "Z")


def _wait_until(predicate, timeout: float = 3.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def _wait_workers_idle(coord: bi.BackgroundIntelligenceCoordinator, timeout: float = 3.0) -> bool:
    return _wait_until(
        lambda: coord.pending_count() == 0 and coord.in_flight_count() == 0,
        timeout=timeout,
    )


_SYMBOL_CACHE_ATTRS = (
    "session",
    "session_liquidity",
    "prices",
    "price_sources",
    "observation_times",
    "retrieval_times",
    "price_validity",
    "freshness",
    "day_open",
    "day_high",
    "day_low",
    "volumes",
    "pre_market_volume",
    "after_hours_volume",
    "regular_close",
    "gaps",
    "session_change",
    "pre_market_price",
    "pre_market_change",
    "after_hours_price",
    "after_hours_change",
    "overnight_return",
    "ssr_active",
    "last_chart_fetch",
    "chart_cache",
    "chart_obs_time",
    "last_premarket_vol_fetch",
    "premarket_vol_cache",
)


_MISSING = object()


def _snapshot_symbol_cache(symbol: str) -> dict:
    snap = {}
    for attr in _SYMBOL_CACHE_ATTRS:
        d = getattr(fs.cache, attr)
        snap[attr] = d.get(symbol, _MISSING)
    return snap


@contextlib.contextmanager
def _isolated_market_coordinators(
    *,
    max_workers: int = 2,
    max_queue_size: int = 50,
    max_results_size: int = 100,
    cooldown_tracker=None,
    bg_workers: int = 1,
):
    """Creates isolated market-data and intelligence coordinators for hermetic tests."""
    mkt_coord = bi.BackgroundIntelligenceCoordinator(
        max_workers=max_workers,
        max_queue_size=max_queue_size,
        max_results_size=max_results_size,
        cooldown_tracker=cooldown_tracker,
    )
    mkt_app = bi.BackgroundResultApplicator(coordinator=mkt_coord)
    bg_coord = bi.BackgroundIntelligenceCoordinator(
        max_workers=bg_workers,
        max_queue_size=20,
        max_results_size=20,
    )
    bg_app = bi.BackgroundResultApplicator(coordinator=bg_coord)
    try:
        with patch.object(fs, "MARKET_DATA_COORDINATOR", mkt_coord), \
             patch.object(fs, "MARKET_DATA_RESULT_APPLICATOR", mkt_app), \
             patch.object(fs, "BACKGROUND_COORDINATOR", bg_coord), \
             patch.object(fs, "BACKGROUND_RESULT_APPLICATOR", bg_app):
            yield mkt_coord, mkt_app, bg_coord, bg_app
    finally:
        mkt_coord.shutdown(timeout=2.0)
        bg_coord.shutdown(timeout=2.0)


class _MarketDataTestBase(unittest.TestCase):
    def setUp(self):
        fs.cache.clear()
        with fs._MARKET_DATA_FALLBACK_STATE_LOCK:
            fs._MARKET_DATA_FALLBACK_REQUESTS.clear()

    def tearDown(self):
        fs.cache.clear()
        with fs._MARKET_DATA_FALLBACK_STATE_LOCK:
            fs._MARKET_DATA_FALLBACK_REQUESTS.clear()


class TestStage222WorkerSeparationAndIsolation(_MarketDataTestBase):
    def test_01_coordinators_and_getters_are_distinct(self):
        self.assertIsNot(fs.get_market_data_coordinator(), fs.get_background_coordinator())
        self.assertIsNot(fs.get_market_data_result_applicator(), fs.BACKGROUND_RESULT_APPLICATOR)
        self.assertIs(
            fs.get_market_data_result_applicator()._coordinator,
            fs.get_market_data_coordinator(),
        )

    def test_02_non_blocking_cycle_when_provider_blocks_and_runs_on_worker_thread(self):
        now_ny = datetime.now(NY_TZ)
        main_tid = threading.get_ident()
        provider_tids = []
        release_event = threading.Event()
        started_event = threading.Event()

        def slow_batch_quotes(tickers):
            provider_tids.append(threading.get_ident())
            started_event.set()
            release_event.wait(timeout=3.0)
            ts = int((now_ny - timedelta(seconds=10)).timestamp())
            return {
                sym: {
                    "regularMarketPrice": 150.25,
                    "regularMarketPreviousClose": 148.00,
                    "regularMarketVolume": 1_200_000,
                    "regularMarketTime": ts,
                    "regularMarketOpen": 149.00,
                    "regularMarketDayHigh": 151.00,
                    "regularMarketDayLow": 148.50,
                }
                for sym in tickers
            }

        with _isolated_market_coordinators() as (mkt_coord, _, _, _):
            with patch.object(fs, "get_batch_quotes", side_effect=slow_batch_quotes):
                t0 = time.monotonic()
                stats = fs.coordinate_market_data_cycle(["AAPL", "NVDA"], "OPEN", ["AAPL"], now_ny=now_ny)
                elapsed = time.monotonic() - t0

                self.assertLess(elapsed, 0.25)
                self.assertTrue(stats["batch_enqueued"])
                self.assertTrue(started_event.wait(timeout=2.0))
                self.assertNotIn("AAPL", fs.cache.prices)

                # While blocked, duplicate cycle for same symbols and session coalesces (no new sequence, single provider call)
                batch_key = bi.WorkKey(bi.WorkType.MARKET_DATA.value, "BATCH_QUOTE:OPEN:AAPL,NVDA")
                seq_before = mkt_coord.get_latest_issued_sequence(batch_key)
                stats2 = fs.coordinate_market_data_cycle(["AAPL", "NVDA"], "OPEN", ["AAPL"], now_ny=now_ny)
                self.assertEqual(mkt_coord.get_latest_issued_sequence(batch_key), seq_before)
                self.assertTrue(stats2["batch_enqueued"])

                release_event.set()
                self.assertTrue(_wait_workers_idle(mkt_coord))
                apply_stats = fs.coordinate_market_data_cycle(["AAPL", "NVDA"], "OPEN", now_ny=now_ny)
                self.assertGreaterEqual(apply_stats["applied_count"], 2)
                self.assertEqual(len(provider_tids), 1)
                self.assertAlmostEqual(fs.cache.prices["AAPL"], 150.25)
                self.assertAlmostEqual(fs.cache.prices["NVDA"], 150.25)
                self.assertTrue(all(tid != main_tid for tid in provider_tids))

    def test_03_blocked_and_saturated_market_data_does_not_block_intelligence(self):
        block_mkt = threading.Event()
        mkt_entered = threading.Event()
        now_iso = datetime.now(UTC_TZ).isoformat()

        def stuck_batch(tickers):
            mkt_entered.set()
            block_mkt.wait(timeout=4.0)
            return {}

        def fast_grounding(sym):
            return bi.ObservedResult(
                data={"symbol": sym, "sentiment": "BULLISH", "catalyst": "Launch"},
                obs_time=now_iso,
            )

        with _isolated_market_coordinators(max_workers=1, max_queue_size=1) as (mkt_coord, _, bg_coord, _):
            try:
                with patch.object(fs, "get_batch_quotes", side_effect=stuck_batch):
                    acc1, _ = fs.enqueue_market_batch_quotes(["AAPL"], "OPEN")
                    self.assertTrue(acc1)
                    self.assertTrue(mkt_entered.wait(timeout=2.0))
                    # Fill single market-data queue slot and verify saturation does not block intelligence
                    acc2, _ = fs.enqueue_market_batch_quotes(["NVDA"], "OPEN")
                    self.assertTrue(acc2)
                    acc3, _ = fs.enqueue_market_batch_quotes(["MSFT"], "OPEN")
                    self.assertFalse(acc3)

                    bg_acc, _ = bg_coord.enqueue(
                        work_type=bi.WorkType.GROUNDING.value,
                        target="AAPL",
                        fn=fast_grounding,
                        args=("AAPL",),
                    )
                    self.assertTrue(bg_acc)
                    self.assertTrue(_wait_workers_idle(bg_coord))
                    bg_results = bg_coord.drain_results()
                    self.assertEqual(len(bg_results), 1)
                    self.assertIsNone(bg_results[0].error)
                    self.assertEqual(bg_results[0].obs_time, now_iso)
                    self.assertEqual(bg_coord.total_work_count(), 0)
                    self.assertEqual(mkt_coord.pending_count() + mkt_coord.in_flight_count(), 2)
            finally:
                block_mkt.set()


class TestStage222TimestampsBatchAndOrdering(_MarketDataTestBase):
    def test_04_batch_efficiency_and_independent_per_symbol_source_timestamps(self):
        now_ny = datetime.now(NY_TZ).replace(microsecond=0)
        ts_aapl = _epoch_after(now_ny, -30)
        ts_nvda = _epoch_after(now_ny, -120)
        calls = []

        def fake_batch(tickers):
            calls.append(tuple(tickers))
            return {
                "AAPL": {
                    "regularMarketPrice": 210.50,
                    "regularMarketPreviousClose": 205.00,
                    "regularMarketVolume": 500_000,
                    "regularMarketTime": ts_aapl,
                },
                "NVDA": {
                    "regularMarketPrice": 125.00,
                    "regularMarketPreviousClose": 120.00,
                    "regularMarketVolume": 900_000,
                    "regularMarketTime": ts_nvda,
                },
            }

        with _isolated_market_coordinators() as (mkt_coord, _, _, _):
            with patch.object(fs, "get_batch_quotes", side_effect=fake_batch):
                acc, _ = fs.enqueue_market_batch_quotes(["AAPL", "NVDA"], "OPEN")
                self.assertTrue(acc)
                self.assertTrue(_wait_workers_idle(mkt_coord))
                audit = fs.apply_background_market_data_results()

            self.assertEqual(len(calls), 1)
            self.assertEqual(set(calls[0]), {"AAPL", "NVDA"})
            self.assertEqual(sum(1 for _, ok, _ in audit if ok), 2)
            self.assertEqual(
                fs.cache.observation_times["AAPL"],
                datetime.fromtimestamp(ts_aapl, UTC_TZ).astimezone(NY_TZ).isoformat(),
            )
            self.assertEqual(
                fs.cache.observation_times["NVDA"],
                datetime.fromtimestamp(ts_nvda, UTC_TZ).astimezone(NY_TZ).isoformat(),
            )
            self.assertNotEqual(fs.cache.observation_times["AAPL"], fs.cache.observation_times["NVDA"])
            self.assertNotEqual(fs.cache.observation_times["AAPL"], fs.cache.retrieval_times["AAPL"])

    def test_05_rejects_missing_naive_malformed_and_non_utc_timestamps(self):
        bad_timestamps = [
            None,
            "",
            "   ",
            "not-a-timestamp",
            "2026-11-14T15:30:00",          # naive
            "2026-11-14T15:30:00-00:00",    # ambiguous -00:00
            "2026-11-14T15:30:00-05:00",    # non-UTC NY offset
            "2026-11-14T15:30:00+02:00",    # non-UTC offset
        ]
        with _isolated_market_coordinators() as (mkt_coord, mkt_app, _, _):
            key = bi.WorkKey(bi.WorkType.MARKET_DATA.value, "QUOTE:AAPL")
            for idx, bad_ts in enumerate(bad_timestamps, start=1):
                res = bi.WorkResult(
                    key=key,
                    sequence=idx,
                    obs_time=bad_ts,
                    data={
                        "kind": "batch_quote",
                        "symbol": "AAPL",
                        "status": "OPEN",
                        "quote_data": {"regularMarketPrice": 200.0},
                    },
                )
                published = []
                audit = mkt_app.apply_results(
                    [res],
                    grounding_target={},
                    scout_cache_target={},
                    scout_category_map={},
                    latest_issued_provider=lambda _k: idx,
                    on_publish=lambda r: published.append(r),
                )
                self.assertEqual(len(audit), 1)
                self.assertFalse(audit[0][1], f"Timestamp {bad_ts!r} must be rejected")
                self.assertEqual(published, [])
                self.assertNotIn("AAPL", fs.cache.prices)

            # Valid +00:00 and Z timestamps accepted
            for idx, good_ts in enumerate(["2026-11-14T15:30:00Z", "2026-11-14T15:31:00+00:00"], start=100):
                res = bi.WorkResult(
                    key=key,
                    sequence=idx,
                    obs_time=good_ts,
                    data={
                        "kind": "batch_quote",
                        "symbol": "AAPL",
                        "status": "OPEN",
                        "quote_data": {"regularMarketPrice": 200.0 + idx},
                    },
                )
                published = []
                audit = mkt_app.apply_results(
                    [res],
                    grounding_target={},
                    scout_cache_target={},
                    scout_category_map={},
                    latest_issued_provider=lambda _k: idx,
                    on_publish=lambda r: published.append(r),
                )
                self.assertTrue(audit[0][1], f"UTC timestamp {good_ts!r} must be accepted")
                self.assertEqual(len(published), 1)

    def test_06_overlapping_batches_per_symbol_ordering_and_partial_batch_isolation(self):
        now_ny = datetime.now(NY_TZ).replace(microsecond=0)
        ts_1 = _epoch_after(now_ny, -120)
        ts_2 = _epoch_after(now_ny, -30)
        batch1_gate = threading.Event()
        batch2_done = threading.Event()

        def fake_batch(tickers):
            tset = set(tickers)
            if tset == {"AAPL", "NVDA"}:
                batch1_gate.wait(timeout=3.0)
                return {
                    "AAPL": {
                        "regularMarketPrice": 100.0,
                        "regularMarketTime": ts_1,
                    },
                    "NVDA": {
                        "regularMarketPrice": 400.0,
                        "regularMarketTime": ts_1,
                    },
                }
            if tset == {"AAPL", "MSFT"}:
                try:
                    return {
                        "AAPL": {
                            "regularMarketPrice": 205.0,
                            "regularMarketTime": ts_2,
                        },
                        # MSFT missing/invalid price -> partial batch invalid symbol with non-price fields
                        "MSFT": {
                            "regularMarketPrice": 0,
                            "regularMarketTime": ts_2,
                            "regularMarketOpen": 999.0,
                            "regularMarketDayHigh": 1005.0,
                            "regularMarketDayLow": 990.0,
                            "regularMarketVolume": 555_555,
                            "regularMarketPreviousClose": 995.0,
                        },
                    }
                finally:
                    batch2_done.set()
            return {}

        # Pre-seed complete MSFT accepted state so partial batch failure must leave all relevant fields intact
        msft_obs_iso = datetime.fromtimestamp(_epoch_after(now_ny, -60), UTC_TZ).astimezone(NY_TZ).isoformat()
        msft_ret_iso = datetime.fromtimestamp(_epoch_after(now_ny, -55), UTC_TZ).astimezone(NY_TZ).isoformat()
        fs.cache.session["MSFT"] = "OPEN"
        fs.cache.session_liquidity["MSFT"] = "HIGH"
        fs.cache.prices["MSFT"] = 333.33
        fs.cache.price_sources["MSFT"] = "BATCH_QUOTE"
        fs.cache.observation_times["MSFT"] = msft_obs_iso
        fs.cache.retrieval_times["MSFT"] = msft_ret_iso
        fs.cache.price_validity["MSFT"] = "VALID"
        fs.cache.freshness["MSFT"] = "CURRENT"
        fs.cache.volumes["MSFT"] = 777
        fs.cache.day_open["MSFT"] = 330.0
        fs.cache.day_high["MSFT"] = 335.0
        fs.cache.day_low["MSFT"] = 329.0
        fs.cache.regular_close["MSFT"] = 325.0
        fs.cache.gaps["MSFT"] = 1.54
        fs.cache.session_change["MSFT"] = 2.56
        msft_before = _snapshot_symbol_cache("MSFT")

        with _isolated_market_coordinators(max_workers=2) as (mkt_coord, _, _, _):
            try:
                with patch.object(fs, "get_batch_quotes", side_effect=fake_batch):
                    # Batch 1 issued first (seq 1 for AAPL & NVDA), blocked in worker
                    self.assertTrue(fs.enqueue_market_batch_quotes(["AAPL", "NVDA"], "OPEN")[0])
                    # Batch 2 issued second (seq 2 for AAPL, seq 1 for MSFT), finishes first
                    self.assertTrue(fs.enqueue_market_batch_quotes(["AAPL", "MSFT"], "OPEN")[0])
                    self.assertTrue(batch2_done.wait(timeout=2.0))

                    audit2 = []
                    self.assertTrue(
                        _wait_until(lambda: (audit2.extend(fs.apply_background_market_data_results()), len(audit2) >= 2)[1])
                    )
                    applied_map2 = {r.key.target: ok for r, ok, _ in audit2}
                    self.assertTrue(applied_map2["QUOTE:AAPL"])
                    self.assertFalse(applied_map2["QUOTE:MSFT"])
                    self.assertAlmostEqual(fs.cache.prices["AAPL"], 205.0)
                    # Partial batch failure did not corrupt any MSFT cache field
                    self.assertEqual(_snapshot_symbol_cache("MSFT"), msft_before)

                    # Release Batch 1: AAPL (seq 1) must be rejected as stale, NVDA (seq 1) must apply cleanly
                    batch1_gate.set()
                    self.assertTrue(_wait_workers_idle(mkt_coord))
                    audit1 = fs.apply_background_market_data_results()
                    applied_map1 = {r.key.target: ok for r, ok, _ in audit1}
                    self.assertFalse(applied_map1["QUOTE:AAPL"])
                    self.assertTrue(applied_map1["QUOTE:NVDA"])
                    self.assertAlmostEqual(fs.cache.prices["AAPL"], 205.0)
                    self.assertAlmostEqual(fs.cache.prices["NVDA"], 400.0)
            finally:
                batch1_gate.set()

    def test_07_fallback_and_batch_share_quote_ordering_and_vwap_is_independent(self):
        now_ny = datetime.now(NY_TZ).replace(microsecond=0)
        ts_fb = _epoch_after(now_ny, -90)
        ts_batch = _epoch_after(now_ny, -20)
        ts_vwap = _epoch_after(now_ny, -30)
        fb_gate = threading.Event()

        def slow_chart(sym, status):
            fb_gate.wait(timeout=3.0)
            return 180.0, 50_000, 0, 0, _utc_iso(ts_fb)

        def fast_batch(tickers):
            return {
                sym: {"regularMarketPrice": 195.0, "regularMarketTime": ts_batch}
                for sym in tickers
            }

        def fast_vwap_bars(sym, status):
            return [(194.0, 194.0, 194.0, 1000.0)], _utc_iso(ts_vwap), False

        with _isolated_market_coordinators(max_workers=3) as (mkt_coord, _, _, _):
            try:
                with patch.object(fs, "_acquire_live_chart_data_raw", side_effect=slow_chart), \
                     patch.object(fs, "get_premarket_volume_chart", return_value=0), \
                     patch.object(fs, "get_finnhub_quote", return_value=(0, 0, 0)), \
                     patch.object(fs, "_extract_fast_info_snapshot", return_value={}), \
                     patch.object(fs, "get_batch_quotes", side_effect=fast_batch), \
                     patch.object(fs, "_acquire_true_intraday_vwap_bars", side_effect=fast_vwap_bars):
                    # 1. Enqueue fallback first (QUOTE:AAPL seq 1)
                    self.assertTrue(fs.enqueue_market_symbol_fallback("AAPL", "OPEN")[0])
                    # 2. Enqueue batch quote second (QUOTE:AAPL seq 2)
                    self.assertTrue(fs.enqueue_market_batch_quotes(["AAPL"], "OPEN")[0])
                    # 3. Enqueue VWAP (VWAP:AAPL seq 1)
                    self.assertTrue(fs.enqueue_market_vwap("AAPL", "OPEN")[0])

                    # Wait until batch + vwap finish while fallback stays blocked
                    self.assertTrue(
                        _wait_until(
                            lambda: (
                                fs.apply_background_market_data_results(),
                                "AAPL" in fs.cache.prices and "AAPL" in fs.cache.vwaps,
                            )[1]
                        )
                    )
                    self.assertAlmostEqual(fs.cache.prices["AAPL"], 195.0)
                    self.assertAlmostEqual(fs.cache.vwaps["AAPL"], 194.0)

                    # Release older fallback: must NOT overwrite newer batch quote
                    fb_gate.set()
                    self.assertTrue(_wait_workers_idle(mkt_coord))
                    audit = fs.apply_background_market_data_results()
                    self.assertTrue(any(r.key.target == "QUOTE:AAPL" and not ok for r, ok, _ in audit))
                    self.assertAlmostEqual(fs.cache.prices["AAPL"], 195.0)
            finally:
                fb_gate.set()

    def test_08_publication_callback_runs_on_coordinating_thread_under_key_lock(self):
        now_iso = datetime.now(UTC_TZ).isoformat()
        coord_tid = threading.get_ident()
        observed_calls = []

        with _isolated_market_coordinators() as (mkt_coord, mkt_app, _, _):
            key = bi.WorkKey(bi.WorkType.MARKET_DATA.value, "QUOTE:AAPL")

            def worker_task():
                return bi.ObservedResult(
                    data={
                        "kind": "batch_quote",
                        "symbol": "AAPL",
                        "status": "OPEN",
                        "quote_data": {"regularMarketPrice": 222.0},
                    },
                    obs_time=now_iso,
                )

            acc, item = mkt_coord.enqueue(
                work_type=bi.WorkType.MARKET_DATA.value,
                target="QUOTE:AAPL",
                fn=worker_task,
            )
            self.assertTrue(acc)
            self.assertTrue(_wait_workers_idle(mkt_coord))
            drained = mkt_coord.drain_results()

            def inspect_publish(res):
                lock = mkt_app.get_key_lock(res.key)
                observed_calls.append(
                    (
                        threading.get_ident(),
                        lock._is_owned(),
                    )
                )

            audit = mkt_app.apply_results(
                drained,
                grounding_target={},
                scout_cache_target={},
                scout_category_map={},
                on_publish=inspect_publish,
            )
            self.assertEqual(len(audit), 1)
            self.assertTrue(audit[0][1])
            self.assertEqual(observed_calls, [(coord_tid, True)])
            # Lock released cleanly on exit
            self.assertTrue(mkt_app.get_key_lock(key).acquire(blocking=False))
            mkt_app.get_key_lock(key).release()


class TestStage222FailureRecoveryAndLoopPurity(_MarketDataTestBase):
    def test_09_failed_cancelled_and_publish_failure_do_not_advance_state_and_recover(self):
        fs.cache.prices["AAPL"] = 188.0
        obs_old = _utc_iso(int(datetime(2026, 11, 14, 15, 0, tzinfo=UTC_TZ).timestamp()))
        obs_new = _utc_iso(int(datetime(2026, 11, 14, 15, 5, tzinfo=UTC_TZ).timestamp()))
        fs.cache.observation_times["AAPL"] = obs_old

        with _isolated_market_coordinators() as (mkt_coord, mkt_app, _, _):
            key = bi.WorkKey(bi.WorkType.MARKET_DATA.value, "QUOTE:AAPL")

            # Worker failure does not mutate cache
            with patch.object(fs, "get_batch_quotes", side_effect=RuntimeError("network down")):
                self.assertTrue(fs.enqueue_market_batch_quotes(["AAPL"], "OPEN")[0])
                self.assertTrue(_wait_workers_idle(mkt_coord))
                audit_fail = fs.apply_background_market_data_results()
                self.assertTrue(all(not ok for _, ok, _ in audit_fail))
                self.assertAlmostEqual(fs.cache.prices["AAPL"], 188.0)

            # Publication exception rollback: must NOT advance applied sequence or last_obs
            res_fail = bi.WorkResult(
                key=key,
                sequence=5,
                obs_time=obs_old,
                data={"kind": "batch_quote", "symbol": "AAPL", "status": "OPEN", "quote_data": {"regularMarketPrice": 199.0}},
            )

            def exploding_publish(_res):
                raise RuntimeError("Transient publish failure")

            audit_err = mkt_app.apply_results(
                [res_fail],
                grounding_target={},
                scout_cache_target={},
                scout_category_map={},
                latest_issued_provider=lambda _k: 5,
                on_publish=exploding_publish,
            )
            self.assertFalse(audit_err[0][1])
            self.assertEqual(mkt_app.get_last_applied(key), (-1, ""))

            # Later valid result with same/newer sequence applies cleanly
            res_ok = bi.WorkResult(
                key=key,
                sequence=5,
                obs_time=obs_new,
                data={"kind": "batch_quote", "symbol": "AAPL", "status": "OPEN", "quote_data": {"regularMarketPrice": 201.0}},
            )
            applied_values = []
            audit_ok = mkt_app.apply_results(
                [res_ok],
                grounding_target={},
                scout_cache_target={},
                scout_category_map={},
                latest_issued_provider=lambda _k: 5,
                on_publish=lambda r: applied_values.append(r.data["quote_data"]["regularMarketPrice"]),
            )
            self.assertTrue(audit_ok[0][1])
            self.assertEqual(applied_values, [201.0])
            self.assertEqual(mkt_app.get_last_applied(key), (5, obs_new))

    def test_10_cancelled_work_does_not_publish(self):
        gate = threading.Event()
        entered = threading.Event()

        def cancellable_batch(tickers, cancel_check=None):
            entered.set()
            gate.wait(timeout=3.0)
            return fs._acquire_batch_quotes_raw(tickers, "OPEN", cancel_check=cancel_check)

        with _isolated_market_coordinators(max_workers=1) as (mkt_coord, _, _, _):
            key = bi.WorkKey(bi.WorkType.MARKET_DATA.value, "BATCH_QUOTE:AAPL")
            with patch.object(
                fs,
                "get_batch_quotes",
                return_value={"AAPL": {"regularMarketPrice": 200.0, "regularMarketTime": 1763132400}},
            ):
                acc, _ = mkt_coord.enqueue(
                    work_type=bi.WorkType.MARKET_DATA.value,
                    target="BATCH_QUOTE:AAPL",
                    fn=cancellable_batch,
                    args=(("AAPL",),),
                    child_keys=(bi.WorkKey(bi.WorkType.MARKET_DATA.value, "QUOTE:AAPL"),),
                )
                self.assertTrue(acc)
                self.assertTrue(entered.wait(timeout=2.0))
                self.assertTrue(mkt_coord.cancel_work(key))
                gate.set()
                self.assertTrue(_wait_workers_idle(mkt_coord))
                audit = fs.apply_background_market_data_results()
                self.assertTrue(all(not ok for _, ok, _ in audit))
                self.assertNotIn("AAPL", fs.cache.prices)

    def test_11_monitoring_loop_has_zero_synchronous_live_provider_calls(self):
        daemon_src = inspect.getsource(fs.run_daemon)
        loop_marker = "while True:"
        self.assertIn(loop_marker, daemon_src)
        loop_body = daemon_src.split(loop_marker, 1)[1]

        self.assertIn("coordinate_market_data_cycle(", loop_body)
        for forbidden_sync_call in (
            "update_price_tick(",
            "get_batch_quotes(",
            "get_true_intraday_vwap(",
            "fallback_vwap(",
            "get_live_chart_data(",
            "get_finnhub_quote(",
        ):
            self.assertNotIn(
                forbidden_sync_call,
                loop_body,
                f"Synchronous call {forbidden_sync_call} must not exist inside monitoring loop",
            )

        # Runtime proof: calling coordinate_market_data_cycle executes zero provider I/O on caller thread
        caller_tid = threading.get_ident()
        provider_caller_tids = []

        def track(fn_ret):
            def _wrapped(*args, **kwargs):
                provider_caller_tids.append(threading.get_ident())
                return fn_ret
            return _wrapped

        with _isolated_market_coordinators() as (mkt_coord, _, _, _):
            with patch.object(fs, "get_batch_quotes", side_effect=track({})), \
                 patch.object(fs, "get_live_chart_data", side_effect=track((0, 0, 0, 0))), \
                 patch.object(fs, "get_finnhub_quote", side_effect=track((0, 0, 0))), \
                 patch.object(fs, "_acquire_true_intraday_vwap_bars", side_effect=track(([], None, False))), \
                 patch.object(fs, "_acquire_fallback_vwap_payload", side_effect=track((None, None))):
                fs.coordinate_market_data_cycle(["AAPL"], "OPEN", ["AAPL"])
                self.assertTrue(_wait_workers_idle(mkt_coord))
                fs.apply_background_market_data_results()

        self.assertNotIn(caller_tid, provider_caller_tids)


class TestStage222SemanticsTTLsSessionsAndShutdown(_MarketDataTestBase):
    def test_12_cache_ttls_and_fallback_precedence_preserved(self):
        now_ny = datetime.now(NY_TZ).replace(microsecond=0)
        ts_chart = _epoch_after(now_ny, -45)
        ts_fh = _epoch_after(now_ny, -15)

        # Verify TTL skip behavior on enqueue_market_symbol_fallback
        with _isolated_market_coordinators(max_workers=1) as (mkt_coord, _, _, _):
            # Capture kwargs passed when chart & premarket vol caches are fresh vs expired
            with patch.object(mkt_coord, "enqueue", wraps=mkt_coord.enqueue) as enq_spy, \
                 patch.object(fs, "_acquire_symbol_fallback_raw", return_value=bi.ObservedResult(data=None, obs_time=None)):
                fs.cache.last_chart_fetch["AAPL"] = time.time() - 10
                fs.cache.last_premarket_vol_fetch["AAPL"] = time.time() - 20
                fs.enqueue_market_symbol_fallback("AAPL", "PRE-MARKET", need_price=True, needs_pre_vol=True)
                self.assertFalse(enq_spy.call_args.kwargs["kwargs"]["fetch_chart"])
                self.assertFalse(enq_spy.call_args.kwargs["kwargs"]["fetch_premarket_vol"])

                self.assertTrue(_wait_workers_idle(mkt_coord))
                mkt_coord.drain_results()

                fs.cache.last_chart_fetch["AAPL"] = time.time() - 65
                fs.cache.last_premarket_vol_fetch["AAPL"] = time.time() - 95
                fs.enqueue_market_symbol_fallback("AAPL", "PRE-MARKET", need_price=True, needs_pre_vol=True)
                self.assertTrue(enq_spy.call_args.kwargs["kwargs"]["fetch_chart"])
                self.assertTrue(enq_spy.call_args.kwargs["kwargs"]["fetch_premarket_vol"])

        # Precedence: INTRADAY_CHART beats FINNHUB; FINNHUB beats FAST_INFO; FAST_INFO has no obs_time & fails closed
        with patch.object(fs, "_acquire_live_chart_data_raw", return_value=(175.0, 1000, 0, 0, _utc_iso(ts_chart))), \
             patch.object(fs, "get_premarket_volume_chart", return_value=0), \
             patch.object(fs, "get_finnhub_quote", return_value=(176.0, 170.0, ts_fh)) as fh_mock:
            obs1 = fs._acquire_symbol_fallback_raw(
                "AAPL", "OPEN", need_price=True, fetch_chart=True, fetch_premarket_vol=False, include_fast_info=False
            )
            fh_mock.assert_not_called()
            self.assertEqual(obs1.obs_time, _utc_iso(ts_chart))

        with patch.object(fs, "_acquire_live_chart_data_raw", return_value=(None, 0, 0, 0, None)), \
             patch.object(fs, "get_premarket_volume_chart", return_value=0), \
             patch.object(fs, "get_finnhub_quote", return_value=(176.0, 170.0, ts_fh)), \
             patch.object(fs, "USE_FINNHUB", True):
            obs2 = fs._acquire_symbol_fallback_raw(
                "AAPL", "OPEN", need_price=True, fetch_chart=True, fetch_premarket_vol=False, include_fast_info=False
            )
            self.assertEqual(obs2.obs_time, _utc_iso(ts_fh))
            fs._apply_quote_and_fallback_to_cache(
                symbol="AAPL",
                status="OPEN",
                finnhub_data=obs2.data["finnhub_data"],
                chart_data=obs2.data["chart_data"],
                chart_fetched=True,
                allow_cache_previous=False,
                now_ny=now_ny,
            )
            self.assertEqual(fs.cache.price_sources["AAPL"], "FINNHUB")
            self.assertAlmostEqual(fs.cache.prices["AAPL"], 176.0)

        # fast_info-only fallback lacks source timestamp -> rejected fail-closed in MARKET_DATA applicator
        with _isolated_market_coordinators() as (mkt_coord, _, _, _):
            with patch.object(fs, "_acquire_live_chart_data_raw", return_value=(None, 0, 0, 0, None)), \
                 patch.object(fs, "get_premarket_volume_chart", return_value=0), \
                 patch.object(fs, "get_finnhub_quote", return_value=(0, 0, 0)), \
                 patch.object(fs, "_extract_fast_info_snapshot", return_value={"last_price": 177.0}):
                self.assertTrue(fs.enqueue_market_symbol_fallback("NVDA", "OPEN", need_price=True)[0])
                self.assertTrue(_wait_workers_idle(mkt_coord))
                audit = fs.apply_background_market_data_results()
                self.assertTrue(all(not ok for _, ok, _ in audit))
                self.assertNotIn("NVDA", fs.cache.prices)

    def test_13_session_selection_and_vwap_fallback_precedence(self):
        now_ny = datetime.now(NY_TZ).replace(microsecond=0)
        ts_reg = _epoch_after(now_ny, -300)
        ts_pre = _epoch_after(now_ny, -60)
        ts_post = _epoch_after(now_ny, -30)
        q = {
            "regularMarketPrice": 100.0,
            "regularMarketTime": ts_reg,
            "preMarketPrice": 102.5,
            "preMarketTime": ts_pre,
            "postMarketPrice": 101.25,
            "postMarketTime": ts_post,
            "regularMarketPreviousClose": 99.0,
        }

        with patch.object(fs, "get_batch_quotes", return_value={"AAPL": q}):
            pre_batch = fs._acquire_batch_quotes_raw(("AAPL",), "PRE-MARKET")
            self.assertEqual(pre_batch.items[0].obs_time, _utc_iso(ts_pre))
            post_batch = fs._acquire_batch_quotes_raw(("AAPL",), "AFTER-HOURS")
            self.assertEqual(post_batch.items[0].obs_time, _utc_iso(ts_post))

        with patch.object(fs, "_acquire_true_intraday_vwap_bars", return_value=([], None, False)), \
             patch.object(
                 fs,
                 "_acquire_fallback_vwap_payload",
                 return_value=(
                     {"kind": "vwap", "symbol": "AAPL", "source": "FALLBACK_VWAP", "bars": [(101.0, 101.0, 101.0, 500.0)]},
                     _utc_iso(ts_post),
                 ),
             ):
            vwap_res = fs._acquire_vwap_raw("AAPL", "OPEN")
            self.assertEqual(vwap_res.obs_time, _utc_iso(ts_post))
            self.assertAlmostEqual(fs._compute_vwap_from_payload(vwap_res.data), 101.0)

    def test_14_freshness_decays_without_network_updates_never_disguised_as_fresh(self):
        t0 = datetime(2026, 11, 14, 15, 0, tzinfo=NY_TZ)
        fs.cache.prices["AAPL"] = 210.0
        fs.cache.price_sources["AAPL"] = "BATCH_QUOTE"
        fs.cache.observation_times["AAPL"] = t0.isoformat()
        fs.cache.retrieval_times["AAPL"] = t0.isoformat()

        with _isolated_market_coordinators():
            with patch.object(fs, "enqueue_market_batch_quotes", return_value=(False, None)):
                fs.coordinate_market_data_cycle(["AAPL"], "OPEN", now_ny=t0 + timedelta(seconds=60))
                self.assertEqual(fs.cache.freshness["AAPL"], "CURRENT")
                self.assertEqual(fs.cache.price_validity["AAPL"], "VALID")

                fs.coordinate_market_data_cycle(["AAPL"], "OPEN", now_ny=t0 + timedelta(seconds=1000))
                self.assertEqual(fs.cache.freshness["AAPL"], "DELAYED")
                self.assertEqual(fs.cache.observation_times["AAPL"], t0.isoformat())

                fs.coordinate_market_data_cycle(["AAPL"], "OPEN", now_ny=t0 + timedelta(seconds=1300))
                self.assertEqual(fs.cache.freshness["AAPL"], "STALE")
                self.assertEqual(fs.cache.observation_times["AAPL"], t0.isoformat())
                self.assertEqual(fs.cache.retrieval_times["AAPL"], t0.isoformat())

    def test_15_shutdown_cleanup_leaves_no_threads_work_or_held_locks(self):
        coord = bi.BackgroundIntelligenceCoordinator(max_workers=2, max_queue_size=20, max_results_size=20)
        gate = threading.Event()
        entered = threading.Event()

        def blocking_task(cancel_check=None):
            entered.set()
            gate.wait(timeout=1.0)
            return None

        key_a = bi.WorkKey(bi.WorkType.MARKET_DATA.value, "QUOTE:AAPL")
        key_b = bi.WorkKey(bi.WorkType.MARKET_DATA.value, "QUOTE:NVDA")
        acc, _ = coord.enqueue(
            work_type=bi.WorkType.MARKET_DATA.value,
            target="BATCH_QUOTE:AAPL,NVDA",
            fn=blocking_task,
            child_keys=(key_a, key_b),
        )
        self.assertTrue(acc)
        self.assertTrue(entered.wait(timeout=2.0))
        workers_before = list(coord._workers)
        gate.set()
        self.assertTrue(coord.shutdown(timeout=2.0))

        self.assertFalse(any(t.is_alive() for t in workers_before))
        self.assertEqual(coord.pending_count(), 0)
        self.assertEqual(coord.total_work_count(), 0)
        for k in (key_a, key_b):
            lock = coord.get_key_lock(k)
            self.assertTrue(lock.acquire(blocking=False))
            lock.release()


class TestStage222RemediationRequirements(_MarketDataTestBase):
    def test_16_session_transition_batch_quote_does_not_coalesce_and_rejects_old_session(self):
        now_ny = datetime.now(NY_TZ).replace(microsecond=0)
        ts_pre = _epoch_after(now_ny, -60)
        ts_open = _epoch_after(now_ny, -15)
        pre_entered = threading.Event()
        pre_gate = threading.Event()
        calls = []

        def fake_batch(tickers):
            idx = len(calls)
            calls.append(tuple(tickers))
            if idx == 0:
                pre_entered.set()
                pre_gate.wait(timeout=3.0)
                return {
                    s: {"preMarketPrice": 180.0, "preMarketTime": ts_pre, "regularMarketPrice": 179.0, "regularMarketTime": ts_pre}
                    for s in tickers
                }
            return {
                s: {"regularMarketPrice": 192.5, "regularMarketTime": ts_open}
                for s in tickers
            }

        with _isolated_market_coordinators(max_workers=2) as (mkt_coord, _, _, _):
            try:
                with patch.object(fs, "get_batch_quotes", side_effect=fake_batch):
                    ok_pre, item_pre = fs.enqueue_market_batch_quotes(["AAPL", "NVDA"], "PRE-MARKET")
                    self.assertTrue(ok_pre)
                    self.assertTrue(pre_entered.wait(timeout=2.0))

                    # Different session must NOT coalesce; issues newer QUOTE:AAPL & QUOTE:NVDA sequences
                    ok_open, item_open = fs.enqueue_market_batch_quotes(["AAPL", "NVDA"], "OPEN")
                    self.assertTrue(ok_open)
                    self.assertNotEqual(item_open.key, item_pre.key)
                    self.assertTrue(item_open.event.wait(timeout=2.0))

                    # Same-session duplicate remains bounded & coalesces onto in-flight/queued or fresh item cleanly
                    ok_dup, item_dup = fs.enqueue_market_batch_quotes(["AAPL", "NVDA"], "PRE-MARKET")
                    self.assertTrue(ok_dup)
                    self.assertEqual(item_dup.key, item_pre.key)
                    self.assertEqual(
                        item_dup.child_sequences[bi.WorkKey(bi.WorkType.MARKET_DATA.value, "QUOTE:AAPL")],
                        item_pre.child_sequences[bi.WorkKey(bi.WorkType.MARKET_DATA.value, "QUOTE:AAPL")],
                    )

                    # Release old PRE-MARKET worker and apply all completed results
                    pre_gate.set()
                    self.assertTrue(_wait_workers_idle(mkt_coord))
                    audit = fs.apply_background_market_data_results()
                    res_map = {(r.key.target, r.sequence): ok for r, ok, _ in audit}

                    self.assertFalse(res_map[("QUOTE:AAPL", 1)])
                    self.assertFalse(res_map[("QUOTE:NVDA", 1)])
                    self.assertTrue(res_map[("QUOTE:AAPL", 2)])
                    self.assertTrue(res_map[("QUOTE:NVDA", 2)])
                    self.assertAlmostEqual(fs.cache.prices["AAPL"], 192.5)
                    self.assertEqual(fs.cache.session["AAPL"], "OPEN")
                    self.assertEqual(
                        fs.cache.observation_times["AAPL"],
                        datetime.fromtimestamp(ts_open, UTC_TZ).astimezone(NY_TZ).isoformat(),
                    )
            finally:
                pre_gate.set()

    def test_17_session_transition_symbol_fallback_and_vwap_ordering_and_same_session_dedup(self):
        now_ny = datetime.now(NY_TZ).replace(microsecond=0)
        ts_pre = _epoch_after(now_ny, -70)
        ts_open = _epoch_after(now_ny, -10)
        fb_pre_entered = threading.Event()
        fb_pre_gate = threading.Event()
        vwap_pre_entered = threading.Event()
        vwap_pre_gate = threading.Event()

        def fake_chart(sym, status):
            if status == "PRE-MARKET":
                fb_pre_entered.set()
                fb_pre_gate.wait(timeout=3.0)
                return 140.0, 1000, 500, 0, _utc_iso(ts_pre)
            return 155.0, 2000, 0, 0, _utc_iso(ts_open)

        def fake_vwap_bars(sym, status):
            if status == "PRE-MARKET":
                vwap_pre_entered.set()
                vwap_pre_gate.wait(timeout=3.0)
                return [(139.0, 139.0, 139.0, 100.0)], _utc_iso(ts_pre), False
            return [(154.0, 154.0, 154.0, 200.0)], _utc_iso(ts_open), False

        with _isolated_market_coordinators(max_workers=4) as (mkt_coord, _, _, _):
            try:
                with patch.object(fs, "_acquire_live_chart_data_raw", side_effect=fake_chart), \
                     patch.object(fs, "get_premarket_volume_chart", return_value=0), \
                     patch.object(fs, "get_finnhub_quote", return_value=(0, 0, 0)), \
                     patch.object(fs, "_extract_fast_info_snapshot", return_value={}), \
                     patch.object(fs, "_acquire_true_intraday_vwap_bars", side_effect=fake_vwap_bars):
                    # 1. Symbol fallback transition: PRE-MARKET blocked, OPEN issued
                    ok_fb1, item_fb1 = fs.enqueue_market_symbol_fallback("AAPL", "PRE-MARKET", need_price=True)
                    self.assertTrue(ok_fb1)
                    self.assertTrue(fb_pre_entered.wait(timeout=2.0))
                    ok_fb_dup, item_fb_dup = fs.enqueue_market_symbol_fallback("AAPL", "PRE-MARKET", need_price=True)
                    self.assertTrue(ok_fb_dup)
                    self.assertEqual(item_fb_dup.key, item_fb1.key)
                    self.assertEqual(item_fb_dup.sequence, item_fb1.sequence)

                    ok_fb2, item_fb2 = fs.enqueue_market_symbol_fallback("AAPL", "OPEN", need_price=True)
                    self.assertTrue(ok_fb2)
                    self.assertNotEqual(item_fb2.key, item_fb1.key)
                    self.assertEqual(item_fb2.result_key, item_fb1.result_key)
                    self.assertTrue(item_fb2.event.wait(timeout=2.0))

                    # 2. VWAP transition: PRE-MARKET blocked, OPEN issued; shares VWAP:AAPL independent of QUOTE:AAPL
                    ok_v1, item_v1 = fs.enqueue_market_vwap("AAPL", "PRE-MARKET")
                    self.assertTrue(ok_v1)
                    self.assertTrue(vwap_pre_entered.wait(timeout=2.0))
                    ok_v_dup, item_v_dup = fs.enqueue_market_vwap("AAPL", "PRE-MARKET")
                    self.assertTrue(ok_v_dup)
                    self.assertEqual(item_v_dup.key, item_v1.key)
                    self.assertEqual(item_v_dup.sequence, item_v1.sequence)

                    ok_v2, item_v2 = fs.enqueue_market_vwap("AAPL", "OPEN")
                    self.assertTrue(ok_v2)
                    self.assertNotEqual(item_v2.key, item_v1.key)
                    self.assertEqual(item_v2.result_key, bi.WorkKey(bi.WorkType.MARKET_DATA.value, "VWAP:AAPL"))
                    self.assertTrue(item_v2.event.wait(timeout=2.0))

                    fb_pre_gate.set()
                    vwap_pre_gate.set()
                    self.assertTrue(_wait_workers_idle(mkt_coord))
                    audit = fs.apply_background_market_data_results()
                    res_map = {(r.key.target, r.sequence): ok for r, ok, _ in audit}

                    self.assertFalse(res_map[("QUOTE:AAPL", 1)])
                    self.assertTrue(res_map[("QUOTE:AAPL", 2)])
                    self.assertFalse(res_map[("VWAP:AAPL", 1)])
                    self.assertTrue(res_map[("VWAP:AAPL", 2)])
                    self.assertAlmostEqual(fs.cache.prices["AAPL"], 155.0)
                    self.assertAlmostEqual(fs.cache.vwaps["AAPL"], 154.0)
            finally:
                fb_pre_gate.set()
                vwap_pre_gate.set()

    def test_18_session_oscillation_premarket_open_premarket_never_accepts_oldest(self):
        now_ny = datetime.now(NY_TZ).replace(microsecond=0)
        ts_1 = _epoch_after(now_ny, -120)
        ts_2 = _epoch_after(now_ny, -60)
        pre1_entered = threading.Event()
        pre1_gate = threading.Event()

        def fake_batch(tickers):
            if not pre1_entered.is_set():
                pre1_entered.set()
                pre1_gate.wait(timeout=3.0)
                return {s: {"preMarketPrice": 111.0, "preMarketTime": ts_1} for s in tickers}
            return {s: {"regularMarketPrice": 222.0, "regularMarketTime": ts_2} for s in tickers}

        with _isolated_market_coordinators(max_workers=2) as (mkt_coord, _, _, _):
            try:
                with patch.object(fs, "get_batch_quotes", side_effect=fake_batch):
                    # 1. PRE-MARKET request stuck in flight (QUOTE:AAPL seq=1)
                    ok1, item1 = fs.enqueue_market_batch_quotes(["AAPL"], "PRE-MARKET")
                    self.assertTrue(ok1)
                    self.assertTrue(pre1_entered.wait(timeout=2.0))

                    # 2. Transition to OPEN -> finishes and publishes (QUOTE:AAPL seq=2)
                    ok2, item2 = fs.enqueue_market_batch_quotes(["AAPL"], "OPEN")
                    self.assertTrue(ok2)
                    self.assertTrue(item2.event.wait(timeout=2.0))
                    audit_open = fs.apply_background_market_data_results()
                    self.assertTrue(audit_open[0][1])
                    self.assertAlmostEqual(fs.cache.prices["AAPL"], 222.0)

                    # 3. Transition back PRE-MARKET while Req 1 still in flight -> Req 1 must NEVER become valid
                    ok3, item3 = fs.enqueue_market_batch_quotes(["AAPL"], "PRE-MARKET")
                    self.assertTrue(ok3)
                    self.assertEqual(item3.child_sequences[bi.WorkKey(bi.WorkType.MARKET_DATA.value, "QUOTE:AAPL")], 1)

                    pre1_gate.set()
                    self.assertTrue(_wait_workers_idle(mkt_coord))
                    audit_old = fs.apply_background_market_data_results()
                    self.assertEqual(len(audit_old), 1)
                    self.assertFalse(audit_old[0][1])
                    self.assertAlmostEqual(fs.cache.prices["AAPL"], 222.0)
            finally:
                pre1_gate.set()

    def test_19_atomic_cache_publication_rejects_invalid_quote_without_partial_mutation(self):
        now_ny = datetime.now(NY_TZ).replace(microsecond=0)
        ts_prev = _epoch_after(now_ny, -60)
        ts_bad_quote = _epoch_after(now_ny, -20)
        ts_valid_later = _epoch_after(now_ny, -5)
        obs_prev_iso = _utc_iso(ts_prev)
        obs_prev_ny_iso = datetime.fromtimestamp(ts_prev, UTC_TZ).astimezone(NY_TZ).isoformat()

        # Pre-seed complete symbol cache across all relevant fields
        fs.cache.session["AAPL"] = "OPEN"
        fs.cache.session_liquidity["AAPL"] = "HIGH"
        fs.cache.prices["AAPL"] = 200.0
        fs.cache.price_sources["AAPL"] = "BATCH_QUOTE"
        fs.cache.observation_times["AAPL"] = obs_prev_ny_iso
        fs.cache.retrieval_times["AAPL"] = obs_prev_ny_iso
        fs.cache.price_validity["AAPL"] = "VALID"
        fs.cache.freshness["AAPL"] = "CURRENT"
        fs.cache.day_open["AAPL"] = 198.0
        fs.cache.day_high["AAPL"] = 202.0
        fs.cache.day_low["AAPL"] = 197.0
        fs.cache.volumes["AAPL"] = 100_000
        fs.cache.pre_market_volume["AAPL"] = 5_000
        fs.cache.after_hours_volume["AAPL"] = 1_000
        fs.cache.regular_close["AAPL"] = 195.0
        fs.cache.gaps["AAPL"] = 1.5
        fs.cache.session_change["AAPL"] = 2.56
        fs.cache.pre_market_price["AAPL"] = 197.5
        fs.cache.pre_market_change["AAPL"] = 1.28
        fs.cache.after_hours_price["AAPL"] = 199.0
        fs.cache.after_hours_change["AAPL"] = 2.05
        fs.cache.overnight_return["AAPL"] = -0.75
        fs.cache.ssr_active["AAPL"] = False
        before_snap = _snapshot_symbol_cache("AAPL")

        with _isolated_market_coordinators() as (_, mkt_app, _, _):
            key = bi.WorkKey(bi.WorkType.MARKET_DATA.value, "QUOTE:AAPL")
            bad_res = bi.WorkResult(
                key=key,
                sequence=1,
                obs_time=_utc_iso(ts_bad_quote),
                data={
                    "kind": "batch_quote",
                    "symbol": "AAPL",
                    "status": "PRE-MARKET",
                    "quote_data": {
                        "regularMarketPrice": 0,  # invalid price
                        "regularMarketOpen": 999.0,
                        "regularMarketDayHigh": 1005.0,
                        "regularMarketDayLow": 990.0,
                        "regularMarketVolume": 777_777,
                        "preMarketVolume": 88_888,
                        "postMarketVolume": 99_999,
                        "regularMarketPreviousClose": 980.0,
                    },
                },
            )

            def publish_fn(res):
                obs_ny = bi.parse_utc_iso8601_timestamp(res.obs_time).astimezone(NY_TZ)
                fs._apply_quote_and_fallback_to_cache(
                    symbol="AAPL",
                    status=res.data["status"],
                    quote_data=res.data["quote_data"],
                    obs_time_override=obs_ny,
                    allow_cache_previous=False,
                    now_ny=now_ny,
                )

            audit_bad = mkt_app.apply_results(
                [bad_res],
                grounding_target={},
                scout_cache_target={},
                scout_category_map={},
                latest_issued_provider=lambda _k: 1,
                on_publish=publish_fn,
            )
            self.assertFalse(audit_bad[0][1])
            self.assertEqual(_snapshot_symbol_cache("AAPL"), before_snap)
            self.assertEqual(mkt_app.get_last_applied(key), (-1, ""))
            lock = mkt_app.get_key_lock(key)
            self.assertTrue(lock.acquire(blocking=False))
            lock.release()

            # Valid subsequent result publishes cleanly and advances sequence & obs_time
            good_iso = _utc_iso(ts_valid_later)
            good_res = bi.WorkResult(
                key=key,
                sequence=2,
                obs_time=good_iso,
                data={
                    "kind": "batch_quote",
                    "symbol": "AAPL",
                    "status": "OPEN",
                    "quote_data": {
                        "regularMarketPrice": 212.0,
                        "regularMarketOpen": 210.0,
                        "regularMarketDayHigh": 213.0,
                        "regularMarketDayLow": 209.0,
                        "regularMarketVolume": 200_000,
                        "regularMarketPreviousClose": 205.0,
                        "regularMarketTime": ts_valid_later,
                    },
                },
            )
            audit_good = mkt_app.apply_results(
                [good_res],
                grounding_target={},
                scout_cache_target={},
                scout_category_map={},
                latest_issued_provider=lambda _k: 2,
                on_publish=publish_fn,
            )
            self.assertTrue(audit_good[0][1])
            self.assertEqual(mkt_app.get_last_applied(key), (2, good_iso))
            self.assertAlmostEqual(fs.cache.prices["AAPL"], 212.0)
            self.assertAlmostEqual(fs.cache.day_open["AAPL"], 210.0)

    def test_20_quote_field_and_source_timestamp_strict_coherence(self):
        now_ny = datetime.now(NY_TZ).replace(microsecond=0)
        ts_reg = _epoch_after(now_ny, -300)
        ts_pre = _epoch_after(now_ny, -45)
        ts_post = _epoch_after(now_ny, -20)
        ny_reg = datetime.fromtimestamp(ts_reg, UTC_TZ).astimezone(NY_TZ).isoformat()
        ny_pre = datetime.fromtimestamp(ts_pre, UTC_TZ).astimezone(NY_TZ).isoformat()
        ny_post = datetime.fromtimestamp(ts_post, UTC_TZ).astimezone(NY_TZ).isoformat()

        cases = [
            # 1. PRE-MARKET with valid premarket price & time
            ("PRE-MARKET", {"preMarketPrice": 152.0, "preMarketTime": ts_pre, "regularMarketPrice": 150.0, "regularMarketTime": ts_reg}, True, 152.0, ny_pre),
            # 2. PRE-MARKET without premarket price -> falls back to regular price & regular time (NOT preMarketTime)
            ("PRE-MARKET", {"preMarketPrice": None, "preMarketTime": ts_pre, "regularMarketPrice": 150.0, "regularMarketTime": ts_reg}, True, 150.0, ny_reg),
            # 3. AFTER-HOURS with valid postmarket price & time
            ("AFTER-HOURS", {"postMarketPrice": 154.0, "postMarketTime": ts_post, "regularMarketPrice": 150.0, "regularMarketTime": ts_reg}, True, 154.0, ny_post),
            # 4. AFTER-HOURS without postmarket price -> falls back to regular price & regular time (NOT postMarketTime)
            ("AFTER-HOURS", {"postMarketPrice": None, "postMarketTime": ts_post, "regularMarketPrice": 150.0, "regularMarketTime": ts_reg}, True, 150.0, ny_reg),
            # 5. Selected field missing timestamp -> fail closed (never borrow regularMarketTime)
            ("PRE-MARKET", {"preMarketPrice": 153.0, "preMarketTime": None, "regularMarketPrice": 150.0, "regularMarketTime": ts_reg}, False, None, None),
            # 6. Selected field invalid / ambiguous timestamp -> fail closed
            ("AFTER-HOURS", {"postMarketPrice": 155.0, "postMarketTime": "2026-11-14T21:00:00-00:00", "regularMarketPrice": 150.0, "regularMarketTime": ts_reg}, False, None, None),
            ("OPEN", {"regularMarketPrice": 151.0, "regularMarketTime": "2026-11-14T15:30:00"}, False, None, None),
        ]

        for idx, (status, q_payload, should_apply, exp_price, exp_ny_iso) in enumerate(cases):
            sym = f"T{idx}"
            fs.cache.clear()
            with _isolated_market_coordinators() as (mkt_coord, _, _, _):
                with patch.object(fs, "get_batch_quotes", return_value={sym: q_payload}):
                    acc, _ = fs.enqueue_market_batch_quotes([sym], status)
                    self.assertTrue(acc)
                    self.assertTrue(_wait_workers_idle(mkt_coord))
                    audit = fs.apply_background_market_data_results()
                    self.assertEqual(len(audit), 1)
                    self.assertEqual(audit[0][1], should_apply, f"Case {idx} ({status}) got {audit[0][2]}")
                    if should_apply:
                        self.assertAlmostEqual(fs.cache.prices[sym], exp_price)
                        self.assertEqual(fs.cache.price_sources[sym], "BATCH_QUOTE")
                        self.assertEqual(fs.cache.observation_times[sym], exp_ny_iso)
                    else:
                        self.assertNotIn(sym, fs.cache.prices)
                        self.assertNotIn(sym, fs.cache.observation_times)

    def test_21_supplemental_fallback_never_borrows_prior_batch_or_invents_timestamps(self):
        now_ny = datetime.now(NY_TZ).replace(microsecond=0)
        ts_chart = _epoch_after(now_ny, -40)

        # Untimestamped supplemental sources (fast_info / premarket_vol / prev_close) yield obs_time=None
        with patch.object(fs, "get_premarket_volume_chart", return_value=12345), \
             patch.object(fs, "_extract_fast_info_snapshot", return_value={"open": 199.0}), \
             patch.object(fs, "get_previous_close", return_value=195.0):
            obs_supp = fs._acquire_symbol_fallback_raw(
                "AAPL",
                "PRE-MARKET",
                need_price=False,
                fetch_chart=False,
                fetch_premarket_vol=True,
                include_fast_info=True,
                include_prev_close=True,
            )
            self.assertIsNone(obs_supp.obs_time)

        # Even if chart AND fast_info run in supplement mode, untimestamped fast_info must not borrow chart ts
        with patch.object(fs, "_acquire_live_chart_data_raw", return_value=(None, 1000, 500, 0, _utc_iso(ts_chart))), \
             patch.object(fs, "get_premarket_volume_chart", return_value=500), \
             patch.object(fs, "_extract_fast_info_snapshot", return_value={"open": 199.0}):
            obs_mixed = fs._acquire_symbol_fallback_raw(
                "AAPL",
                "OPEN",
                need_price=False,
                fetch_chart=True,
                fetch_premarket_vol=True,
                include_fast_info=True,
            )
            self.assertIsNone(obs_mixed.obs_time)
            self.assertEqual(obs_mixed.data["source_timestamps"]["chart"], _utc_iso(ts_chart))

        # Enqueueing supplement fallback uses separate QUOTE_SUPPLEMENT key and does not advance QUOTE:AAPL
        with _isolated_market_coordinators() as (mkt_coord, _, _, _):
            with patch.object(fs, "_acquire_live_chart_data_raw", return_value=(None, 0, 0, 0, None)), \
                 patch.object(fs, "get_premarket_volume_chart", return_value=12345), \
                 patch.object(fs, "_extract_fast_info_snapshot", return_value={"open": 199.0}):
                ok, item = fs.enqueue_market_symbol_fallback(
                    "AAPL",
                    "PRE-MARKET",
                    need_price=False,
                    needs_pre_vol=True,
                    need_fast_info=True,
                )
                self.assertTrue(ok)
                self.assertEqual(item.result_key, bi.WorkKey(bi.WorkType.MARKET_DATA.value, "QUOTE_SUPPLEMENT:AAPL"))
                self.assertEqual(mkt_coord.get_latest_issued_sequence(bi.WorkKey(bi.WorkType.MARKET_DATA.value, "QUOTE:AAPL")), 0)
                self.assertTrue(_wait_workers_idle(mkt_coord))
                audit = fs.apply_background_market_data_results()
                self.assertEqual(len(audit), 1)
                self.assertFalse(audit[0][1])
                self.assertIn("Missing required source observation timestamp", audit[0][2])


if __name__ == "__main__":
    unittest.main()
