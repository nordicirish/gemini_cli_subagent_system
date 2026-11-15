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
    "previous_session_volume",
    "previous_session_volume_dates",
    "previous_session_volume_sources",
    "regular_close",
    "regular_close_dates",
    "regular_close_sources",
    "vwaps",
    "metric_provenance",
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
                batch_key = bi.WorkKey(
                    bi.WorkType.MARKET_DATA.value,
                    "BATCH_QUOTE:OPEN:DAY_HIGH,DAY_LOW,DAY_OPEN,QUOTE,SESSION_VOLUME:AAPL,NVDA",
                )
                seq_before = mkt_coord.get_latest_issued_sequence(batch_key)
                self.assertEqual(seq_before, 1)
                stats2 = fs.coordinate_market_data_cycle(["AAPL", "NVDA"], "OPEN", ["AAPL"], now_ny=now_ny)
                self.assertEqual(mkt_coord.get_latest_issued_sequence(batch_key), seq_before)
                self.assertTrue(stats2["batch_enqueued"])

                release_event.set()
                self.assertTrue(_wait_workers_idle(mkt_coord))
                apply_stats = fs.coordinate_market_data_cycle(["AAPL", "NVDA"], "OPEN", now_ny=now_ny)
                self.assertGreaterEqual(apply_stats["applied_count"], 4)
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
            applied_map = {r.key.target: ok for r, ok, _ in audit}
            self.assertTrue(applied_map["QUOTE:AAPL"])
            self.assertTrue(applied_map["QUOTE:NVDA"])
            self.assertTrue(applied_map["SESSION_VOLUME:AAPL"])
            self.assertTrue(applied_map["SESSION_VOLUME:NVDA"])
            self.assertEqual(
                fs.cache.metric_provenance["AAPL"]["QUOTE"]["kind"],
                bi.ObservationKind.INSTANT.value,
            )
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
            inst_prov = bi.MarketDataProvenance(bi.ObservationKind.INSTANT, "BATCH_QUOTE")
            for idx, bad_ts in enumerate(bad_timestamps, start=1):
                res = bi.WorkResult(
                    key=key,
                    sequence=idx,
                    obs_time=bad_ts,
                    data={
                        "kind": "metric",
                        "metric": "QUOTE",
                        "symbol": "AAPL",
                        "status": "OPEN",
                        "value": 200.0,
                    },
                    provenance=inst_prov,
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
                        "kind": "metric",
                        "metric": "QUOTE",
                        "symbol": "AAPL",
                        "status": "OPEN",
                        "value": 200.0 + idx,
                    },
                    provenance=inst_prov,
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
                        _wait_until(lambda: (audit2.extend(fs.apply_background_market_data_results()), len(audit2) >= 10)[1])
                    )
                    applied_map2 = {r.key.target: ok for r, ok, _ in audit2}
                    self.assertTrue(applied_map2["QUOTE:AAPL"])
                    self.assertFalse(applied_map2["QUOTE:MSFT"])
                    self.assertAlmostEqual(fs.cache.prices["AAPL"], 205.0)
                    # Failed QUOTE:MSFT leaves MSFT quote, timestamp, validity, freshness, regular_close, and session_change intact
                    self.assertAlmostEqual(fs.cache.prices["MSFT"], msft_before["prices"])
                    self.assertEqual(fs.cache.price_sources["MSFT"], msft_before["price_sources"])
                    self.assertEqual(fs.cache.observation_times["MSFT"], msft_before["observation_times"])
                    self.assertEqual(fs.cache.retrieval_times["MSFT"], msft_before["retrieval_times"])
                    self.assertEqual(fs.cache.price_validity["MSFT"], msft_before["price_validity"])
                    self.assertEqual(fs.cache.freshness["MSFT"], msft_before["freshness"])
                    self.assertAlmostEqual(fs.cache.regular_close["MSFT"], msft_before["regular_close"])
                    self.assertAlmostEqual(fs.cache.session_change["MSFT"], msft_before["session_change"])
                    # Meanwhile valid sibling metrics with their own valid regularMarketTime publish independently
                    self.assertTrue(applied_map2["DAY_OPEN:MSFT"])
                    self.assertAlmostEqual(fs.cache.day_open["MSFT"], 999.0)
                    self.assertEqual(fs.cache.volumes["MSFT"], 555_555)

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
                        "kind": "metric",
                        "metric": "QUOTE",
                        "symbol": "AAPL",
                        "status": "OPEN",
                        "value": 222.0,
                    },
                    obs_time=now_iso,
                    provenance=bi.MarketDataProvenance(bi.ObservationKind.INSTANT, "BATCH_QUOTE"),
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
            inst_prov = bi.MarketDataProvenance(bi.ObservationKind.INSTANT, "BATCH_QUOTE")
            res_fail = bi.WorkResult(
                key=key,
                sequence=5,
                obs_time=obs_old,
                data={"kind": "metric", "metric": "QUOTE", "symbol": "AAPL", "status": "OPEN", "value": 199.0},
                provenance=inst_prov,
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
                data={"kind": "metric", "metric": "QUOTE", "symbol": "AAPL", "status": "OPEN", "value": 201.0},
                provenance=inst_prov,
            )
            applied_values = []
            audit_ok = mkt_app.apply_results(
                [res_ok],
                grounding_target={},
                scout_cache_target={},
                scout_category_map={},
                latest_issued_provider=lambda _k: 5,
                on_publish=lambda r: applied_values.append(r.data["value"]),
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

        # Precedence: INTRADAY_CHART beats FINNHUB; FINNHUB publishes on quote fallback; FAST_INFO fails closed
        chart_ok = {
            "price": bi.TimestampedValue(175.0, _utc_iso(ts_chart), "INTRADAY_CHART"),
            "session_volume": bi.TimestampedValue(1000, _utc_iso(ts_chart), "INTRADAY_CHART_VOLUME"),
            "premarket_volume": None,
            "after_hours_volume": None,
            "chart_obs_utc_iso": _utc_iso(ts_chart),
            "legacy_tuple": (175.0, 1000, 0, 0, _utc_iso(ts_chart)),
        }
        with patch.object(fs, "_acquire_live_chart_observations_raw", return_value=chart_ok), \
             patch.object(fs, "get_premarket_volume_chart", return_value=None), \
             patch.object(fs, "_acquire_finnhub_quote_raw", return_value=bi.TimestampedValue(176.0, _utc_iso(ts_fh), "FINNHUB")) as fh_mock:
            obs1 = fs._acquire_symbol_fallback_raw(
                "AAPL", "OPEN", need_price=True, fetch_chart=True, fetch_premarket_vol=False, include_fast_info=False
            )
            fh_mock.assert_not_called()
            obs1_map = {item.key.target: item for item in obs1.items}
            self.assertEqual(obs1_map["QUOTE:AAPL"].obs_time, _utc_iso(ts_chart))
            self.assertEqual(obs1_map["QUOTE:AAPL"].provenance.source, "INTRADAY_CHART")

        chart_empty = {
            "price": None,
            "session_volume": None,
            "premarket_volume": None,
            "after_hours_volume": None,
            "chart_obs_utc_iso": None,
            "legacy_tuple": (None, 0, 0, 0, None),
        }
        with _isolated_market_coordinators() as (mkt_coord, _, _, _):
            with patch.object(fs, "_acquire_live_chart_observations_raw", return_value=chart_empty), \
                 patch.object(fs, "get_premarket_volume_chart", return_value=None), \
                 patch.object(fs, "_acquire_finnhub_quote_raw", return_value=bi.TimestampedValue(176.0, _utc_iso(ts_fh), "FINNHUB")), \
                 patch.object(fs, "USE_FINNHUB", True):
                acc, _ = fs.enqueue_market_symbol_fallback("AAPL", "OPEN", need_price=True)
                self.assertTrue(acc)
                self.assertTrue(_wait_workers_idle(mkt_coord))
                audit = fs.apply_background_market_data_results()
                audit_map = {r.key.target: ok for r, ok, _ in audit}
                self.assertTrue(audit_map["QUOTE:AAPL"])
                self.assertEqual(fs.cache.price_sources["AAPL"], "FINNHUB")
                self.assertAlmostEqual(fs.cache.prices["AAPL"], 176.0)

        # fast_info-only fallback lacks verifiable provenance -> fails closed (ignored & never published)
        with _isolated_market_coordinators() as (mkt_coord, _, _, _):
            with patch.object(fs, "_acquire_live_chart_observations_raw", return_value=chart_empty), \
                 patch.object(fs, "get_premarket_volume_chart", return_value=None), \
                 patch.object(fs, "_acquire_finnhub_quote_raw", return_value=None), \
                 patch.object(fs, "_extract_fast_info_snapshot", return_value={"last_price": 177.0}) as fi_mock:
                self.assertTrue(fs.enqueue_market_symbol_fallback("NVDA", "OPEN", need_price=True)[0])
                self.assertTrue(_wait_workers_idle(mkt_coord))
                audit = fs.apply_background_market_data_results()
                fi_mock.assert_not_called()
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
                return {
                    "price": bi.TimestampedValue(140.0, _utc_iso(ts_pre), "INTRADAY_CHART"),
                    "session_volume": bi.TimestampedValue(1000, _utc_iso(ts_pre), "INTRADAY_CHART_VOLUME"),
                    "premarket_volume": bi.TimestampedValue(500, _utc_iso(ts_pre), "INTRADAY_CHART_PREMARKET_VOLUME"),
                    "after_hours_volume": None,
                    "chart_obs_utc_iso": _utc_iso(ts_pre),
                    "legacy_tuple": (140.0, 1000, 500, 0, _utc_iso(ts_pre)),
                }
            return {
                "price": bi.TimestampedValue(155.0, _utc_iso(ts_open), "INTRADAY_CHART"),
                "session_volume": bi.TimestampedValue(2000, _utc_iso(ts_open), "INTRADAY_CHART_VOLUME"),
                "premarket_volume": None,
                "after_hours_volume": None,
                "chart_obs_utc_iso": _utc_iso(ts_open),
                "legacy_tuple": (155.0, 2000, 0, 0, _utc_iso(ts_open)),
            }

        def fake_vwap_bars(sym, status):
            if status == "PRE-MARKET":
                vwap_pre_entered.set()
                vwap_pre_gate.wait(timeout=3.0)
                return [(139.0, 139.0, 139.0, 100.0)], _utc_iso(ts_pre), False
            return [(154.0, 154.0, 154.0, 200.0)], _utc_iso(ts_open), False

        with _isolated_market_coordinators(max_workers=4) as (mkt_coord, _, _, _):
            quote_key = bi.WorkKey(bi.WorkType.MARKET_DATA.value, "QUOTE:AAPL")
            try:
                with patch.object(fs, "_acquire_live_chart_observations_raw", side_effect=fake_chart), \
                     patch.object(fs, "get_premarket_volume_chart", return_value=None), \
                     patch.object(fs, "_acquire_finnhub_quote_raw", return_value=None), \
                     patch.object(fs, "_extract_fast_info_snapshot", return_value={}), \
                     patch.object(fs, "_acquire_true_intraday_vwap_bars", side_effect=fake_vwap_bars):
                    # 1. Symbol fallback transition: PRE-MARKET blocked, OPEN issued
                    ok_fb1, item_fb1 = fs.enqueue_market_symbol_fallback("AAPL", "PRE-MARKET", need_price=True)
                    self.assertTrue(ok_fb1)
                    self.assertTrue(fb_pre_entered.wait(timeout=2.0))
                    ok_fb_dup, item_fb_dup = fs.enqueue_market_symbol_fallback("AAPL", "PRE-MARKET", need_price=True)
                    self.assertTrue(ok_fb_dup)
                    self.assertEqual(item_fb_dup.key, item_fb1.key)
                    self.assertEqual(item_fb_dup.child_sequences[quote_key], item_fb1.child_sequences[quote_key])

                    ok_fb2, item_fb2 = fs.enqueue_market_symbol_fallback("AAPL", "OPEN", need_price=True)
                    self.assertTrue(ok_fb2)
                    self.assertNotEqual(item_fb2.key, item_fb1.key)
                    self.assertGreater(item_fb2.child_sequences[quote_key], item_fb1.child_sequences[quote_key])
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
                    old_map = {r.key.target: ok for r, ok, _ in audit_old}
                    self.assertIn("QUOTE:AAPL", old_map)
                    self.assertFalse(old_map["QUOTE:AAPL"])
                    self.assertTrue(all(not ok for _, ok, _ in audit_old))
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
            inst_prov = bi.MarketDataProvenance(bi.ObservationKind.INSTANT, "BATCH_QUOTE")
            bad_res = bi.WorkResult(
                key=key,
                sequence=1,
                obs_time=_utc_iso(ts_bad_quote),
                data={
                    "kind": "metric",
                    "metric": "QUOTE",
                    "symbol": "AAPL",
                    "status": "PRE-MARKET",
                    "value": 0,  # invalid price
                },
                provenance=inst_prov,
            )

            def publish_fn(res):
                sym, m_name, updates, prov_entry = fs._build_metric_cache_candidate(res, now_ny=now_ny)
                fs._publish_metric_cache_candidate(sym, m_name, updates, prov_entry)

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
                    "kind": "metric",
                    "metric": "QUOTE",
                    "symbol": "AAPL",
                    "status": "OPEN",
                    "value": 212.0,
                },
                provenance=inst_prov,
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
                    q_entries = [entry for entry in audit if entry[0].key.target == f"QUOTE:{sym}"]
                    self.assertEqual(len(q_entries), 1)
                    self.assertEqual(q_entries[0][1], should_apply, f"Case {idx} ({status}) got {q_entries[0][2]}")
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

        # Bare untimestamped supplemental sources (fast_info / bare int premarket_vol) fail closed cleanly
        with patch.object(fs, "get_premarket_volume_chart", return_value=12345), \
             patch.object(fs, "_extract_fast_info_snapshot", return_value={"open": 199.0}) as fi_mock, \
             patch.object(fs, "_acquire_previous_close_raw", return_value=None):
            batch_supp = fs._acquire_symbol_fallback_raw(
                "AAPL",
                "PRE-MARKET",
                need_price=False,
                fetch_chart=False,
                fetch_premarket_vol=True,
                include_fast_info=True,
                include_prev_close=True,
            )
            fi_mock.assert_not_called()
            supp_map = {item.key.target: item for item in batch_supp.items}
            self.assertIsNone(supp_map["PREMARKET_VOLUME:AAPL"].data)
            self.assertIsNone(supp_map["PREMARKET_VOLUME:AAPL"].obs_time)
            self.assertIsNone(supp_map["REGULAR_CLOSE:AAPL"].data)
            self.assertIsNone(supp_map["REGULAR_CLOSE:AAPL"].provenance)

        # Even if chart AND fast_info run together, fast_info never borrows chart ts and never emits DAY_OPEN
        chart_ok = {
            "price": None,
            "session_volume": bi.TimestampedValue(1000, _utc_iso(ts_chart), "INTRADAY_CHART_VOLUME"),
            "premarket_volume": None,
            "after_hours_volume": None,
            "chart_obs_utc_iso": _utc_iso(ts_chart),
            "legacy_tuple": (None, 1000, 0, 0, _utc_iso(ts_chart)),
        }
        with patch.object(fs, "_acquire_live_chart_observations_raw", return_value=chart_ok), \
             patch.object(fs, "get_premarket_volume_chart", return_value=None), \
             patch.object(fs, "_extract_fast_info_snapshot", return_value={"open": 199.0}) as fi_mock:
            obs_mixed = fs._acquire_symbol_fallback_raw(
                "AAPL",
                "OPEN",
                need_price=False,
                fetch_chart=True,
                fetch_premarket_vol=True,
                include_fast_info=True,
                include_chart_open=False,
            )
            fi_mock.assert_not_called()
            mixed_map = {item.key.target: item for item in obs_mixed.items}
            self.assertNotIn("DAY_OPEN:AAPL", mixed_map)
            self.assertEqual(mixed_map["SESSION_VOLUME:AAPL"].obs_time, _utc_iso(ts_chart))

        # Enqueueing supplement fallback uses per-metric child_keys and never advances QUOTE:AAPL
        with _isolated_market_coordinators() as (mkt_coord, _, _, _):
            with patch.object(fs, "_acquire_live_chart_observations_raw", return_value={
                "price": None, "session_volume": None, "premarket_volume": None,
                "after_hours_volume": None, "chart_obs_utc_iso": None, "legacy_tuple": (None, 0, 0, 0, None),
            }), \
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
                self.assertIn(bi.WorkKey(bi.WorkType.MARKET_DATA.value, "PREMARKET_VOLUME:AAPL"), item.child_sequences)
                self.assertEqual(mkt_coord.get_latest_issued_sequence(bi.WorkKey(bi.WorkType.MARKET_DATA.value, "QUOTE:AAPL")), 0)
                self.assertTrue(_wait_workers_idle(mkt_coord))
                audit = fs.apply_background_market_data_results()
                self.assertEqual(len(audit), 1)
                self.assertFalse(audit[0][1])
                self.assertNotIn("AAPL", fs.cache.pre_market_volume)
                self.assertNotIn("AAPL", fs.cache.prices)


class _FakeHttpResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class TestStage222ProvenanceAwareContracts(_MarketDataTestBase):
    def test_22_different_metric_sets_do_not_coalesce_in_batch_or_fallback(self):
        with _isolated_market_coordinators(max_workers=2) as (mkt_coord, _, _, _):
            with patch.object(fs, "get_batch_quotes", return_value={}):
                ok1, item1 = fs.enqueue_market_batch_quotes(["AAPL"], "OPEN", metrics=("QUOTE",))
                ok2, item2 = fs.enqueue_market_batch_quotes(["AAPL"], "OPEN", metrics=("DAY_OPEN", "SESSION_VOLUME"))
                self.assertTrue(ok1 and ok2)
                self.assertNotEqual(item1.key, item2.key)
                # Identical status + metric set still coalesces while active/queued
                ok1_dup, item1_dup = fs.enqueue_market_batch_quotes(["AAPL"], "OPEN", metrics=("QUOTE",))
                self.assertTrue(ok1_dup)
                self.assertEqual(item1_dup.key, item1.key)
                self.assertTrue(_wait_workers_idle(mkt_coord))
                mkt_coord.drain_results()

            gate = threading.Event()
            entered = threading.Event()

            def slow_chart_open(_sym):
                entered.set()
                gate.wait(timeout=3.0)
                return None

            try:
                with patch.object(fs, "_acquire_chart_open_raw", side_effect=slow_chart_open), \
                     patch.object(fs, "_acquire_previous_close_raw", return_value=None):
                    ok_open, fb_open = fs.enqueue_market_symbol_fallback(
                        "AAPL", "OPEN", need_price=False, need_chart_open=True
                    )
                    self.assertTrue(ok_open)
                    self.assertTrue(entered.wait(timeout=2.0))

                    ok_rc, fb_rc = fs.enqueue_market_symbol_fallback(
                        "AAPL", "OPEN", need_price=False, need_prev_close=True
                    )
                    self.assertTrue(ok_rc)
                    self.assertNotEqual(fb_open.key, fb_rc.key)

                    ok_open_dup, fb_open_dup = fs.enqueue_market_symbol_fallback(
                        "AAPL", "OPEN", need_price=False, need_chart_open=True
                    )
                    self.assertTrue(ok_open_dup)
                    self.assertEqual(fb_open_dup.key, fb_open.key)

                    gate.set()
                    self.assertTrue(_wait_workers_idle(mkt_coord))
                    mkt_coord.drain_results()
            finally:
                gate.set()

    def test_23_independent_metric_ordering_and_session_reference_non_interference(self):
        now_ny = datetime.now(NY_TZ).replace(microsecond=0)
        ts_quote = _epoch_after(now_ny, -30)
        quote_utc = _utc_iso(ts_quote)
        quote_ny = datetime.fromtimestamp(ts_quote, UTC_TZ).astimezone(NY_TZ).isoformat()
        yesterday = (now_ny.date() - timedelta(days=1)).isoformat()

        # Pre-seed QUOTE:AAPL at seq 5 and verify supplemental metrics never perturb QUOTE state
        fs.cache.session["AAPL"] = "OPEN"
        fs.cache.prices["AAPL"] = 210.0
        fs.cache.price_sources["AAPL"] = "BATCH_QUOTE"
        fs.cache.observation_times["AAPL"] = quote_ny
        fs.cache.price_validity["AAPL"] = "VALID"
        fs.cache.freshness["AAPL"] = "CURRENT"

        with _isolated_market_coordinators() as (_, mkt_app, _, _):
            quote_key = bi.WorkKey(bi.WorkType.MARKET_DATA.value, "QUOTE:AAPL")
            rc_key = bi.WorkKey(bi.WorkType.MARKET_DATA.value, "REGULAR_CLOSE:AAPL")
            pv_key = bi.WorkKey(bi.WorkType.MARKET_DATA.value, "PREVIOUS_SESSION_VOLUME:AAPL")

            # Seed applicator with QUOTE seq=2 and an older REGULAR_CLOSE seq=1 pending
            r_quote = bi.WorkResult(
                key=quote_key,
                sequence=2,
                obs_time=quote_utc,
                data={"kind": "metric", "metric": "QUOTE", "symbol": "AAPL", "status": "OPEN", "value": 215.0},
                provenance=bi.MarketDataProvenance(bi.ObservationKind.INSTANT, "BATCH_QUOTE"),
            )
            r_rc = bi.WorkResult(
                key=rc_key,
                sequence=1,
                obs_time=None,
                data={"kind": "metric", "metric": "REGULAR_CLOSE", "symbol": "AAPL", "status": "OPEN", "value": 200.0},
                provenance=bi.MarketDataProvenance(bi.ObservationKind.SESSION_REFERENCE, "YAHOO_DAILY_CLOSE", yesterday),
            )
            r_pv = bi.WorkResult(
                key=pv_key,
                sequence=1,
                obs_time=None,
                data={"kind": "metric", "metric": "PREVIOUS_SESSION_VOLUME", "symbol": "AAPL", "value": 4_500_000},
                provenance=bi.MarketDataProvenance(bi.ObservationKind.SESSION_REFERENCE, "POLYGON_PREV_AGG", yesterday),
            )

            def pub(res):
                sym, m_name, updates, prov_entry = fs._build_metric_cache_candidate(res, now_ny=now_ny)
                fs._publish_metric_cache_candidate(sym, m_name, updates, prov_entry)

            # Publish QUOTE first, then older-numbered SESSION_REFERENCE metrics -> none block one another
            audit = mkt_app.apply_results(
                [r_quote, r_rc, r_pv],
                grounding_target={},
                scout_cache_target={},
                scout_category_map={},
                on_publish=pub,
            )
            self.assertTrue(all(ok for _, ok, _ in audit))
            self.assertAlmostEqual(fs.cache.prices["AAPL"], 215.0)
            self.assertEqual(fs.cache.observation_times["AAPL"], quote_ny)
            self.assertEqual(fs.cache.price_sources["AAPL"], "BATCH_QUOTE")
            self.assertAlmostEqual(fs.cache.regular_close["AAPL"], 200.0)
            self.assertEqual(fs.cache.regular_close_dates["AAPL"], yesterday)
            self.assertEqual(fs.cache.previous_session_volume["AAPL"], 4_500_000)
            self.assertEqual(fs.cache.previous_session_volume_dates["AAPL"], yesterday)
            self.assertNotIn("AAPL", fs.cache.volumes)
            # SESSION_REFERENCE must not advance INSTANT obs_time tracking
            self.assertEqual(mkt_app.get_last_applied(rc_key), (1, ""))
            self.assertEqual(mkt_app.get_last_applied_session_date(rc_key), yesterday)
            self.assertEqual(mkt_app.get_last_applied(quote_key), (2, quote_utc))

            # Stale REGULAR_CLOSE seq=0 rejected without affecting QUOTE:AAPL
            r_rc_stale = bi.WorkResult(
                key=rc_key,
                sequence=0,
                obs_time=None,
                data={"kind": "metric", "metric": "REGULAR_CLOSE", "symbol": "AAPL", "status": "OPEN", "value": 180.0},
                provenance=bi.MarketDataProvenance(bi.ObservationKind.SESSION_REFERENCE, "YAHOO_DAILY_CLOSE", yesterday),
            )
            audit_stale = mkt_app.apply_results(
                [r_rc_stale],
                grounding_target={},
                scout_cache_target={},
                scout_category_map={},
                latest_issued_provider=lambda _k: 2,
                on_publish=pub,
            )
            self.assertFalse(audit_stale[0][1])
            self.assertAlmostEqual(fs.cache.regular_close["AAPL"], 200.0)
            self.assertAlmostEqual(fs.cache.prices["AAPL"], 215.0)

    def test_24_provider_chart_and_premarket_volume_provenance_and_zero_distinction(self):
        today_ny = datetime.now(NY_TZ).date()
        dt_0800 = datetime(today_ny.year, today_ny.month, today_ny.day, 8, 0, tzinfo=NY_TZ)
        dt_0805 = datetime(today_ny.year, today_ny.month, today_ny.day, 8, 5, tzinfo=NY_TZ)
        dt_0935 = datetime(today_ny.year, today_ny.month, today_ny.day, 9, 35, tzinfo=NY_TZ)
        dt_1000 = datetime(today_ny.year, today_ny.month, today_ny.day, 10, 0, tzinfo=NY_TZ)
        dt_1615 = datetime(today_ny.year, today_ny.month, today_ny.day, 16, 15, tzinfo=NY_TZ)

        # Fake "now" as 10:30 NY today so premarket/open bars are in the past/same day
        fake_now_open = datetime(today_ny.year, today_ny.month, today_ny.day, 10, 30, tzinfo=NY_TZ)

        # 1. Premarket chart helper captures latest contributing bar timestamp
        pm_payload = {
            "chart": {
                "result": [{
                    "timestamp": [int(dt_0800.timestamp()), int(dt_0805.timestamp())],
                    "indicators": {"quote": [{"volume": [1500, 2500]}]},
                }]
            }
        }
        with patch.object(fs, "safe_yf_get", return_value=_FakeHttpResponse(pm_payload)), \
             patch("fetch_stocks.datetime") as dt_mock:
            dt_mock.now.return_value = fake_now_open
            dt_mock.fromtimestamp.side_effect = datetime.fromtimestamp
            obs = fs.get_premarket_volume_chart("AAPL")
        self.assertIsInstance(obs, fs.PremarketVolumeObservation)
        self.assertEqual(obs.value, 4000)
        self.assertEqual(obs.obs_time, _utc_iso(int(dt_0805.timestamp())))
        self.assertEqual(obs.source, "PREMARKET_VOLUME_CHART")

        # All-None volumes return None (never masquerade as observed 0)
        pm_all_none = {
            "chart": {
                "result": [{
                    "timestamp": [int(dt_0800.timestamp()), int(dt_0805.timestamp())],
                    "indicators": {"quote": [{"volume": [None, None]}]},
                }]
            }
        }
        with patch.object(fs, "safe_yf_get", return_value=_FakeHttpResponse(pm_all_none)), \
             patch("fetch_stocks.datetime") as dt_mock:
            dt_mock.now.return_value = fake_now_open
            dt_mock.fromtimestamp.side_effect = datetime.fromtimestamp
            self.assertIsNone(fs.get_premarket_volume_chart("AAPL"))

        # Valid 0-volume bar distinguishes observed 0 cleanly
        pm_zero = {
            "chart": {
                "result": [{
                    "timestamp": [int(dt_0800.timestamp())],
                    "indicators": {"quote": [{"volume": [0]}]},
                }]
            }
        }
        with patch.object(fs, "safe_yf_get", return_value=_FakeHttpResponse(pm_zero)), \
             patch("fetch_stocks.datetime") as dt_mock:
            dt_mock.now.return_value = fake_now_open
            dt_mock.fromtimestamp.side_effect = datetime.fromtimestamp
            obs_zero = fs.get_premarket_volume_chart("AAPL")
        self.assertIsInstance(obs_zero, fs.PremarketVolumeObservation)
        self.assertEqual(obs_zero.value, 0)
        self.assertEqual(obs_zero.obs_time, _utc_iso(int(dt_0800.timestamp())))

        # 2. Intraday chart raw observation extracts separate metric timestamps
        chart_payload = {
            "chart": {
                "result": [{
                    "timestamp": [
                        int(dt_0800.timestamp()),
                        int(dt_0935.timestamp()),
                        int(dt_1000.timestamp()),
                        int(dt_1615.timestamp()),
                    ],
                    "indicators": {"quote": [{
                        "close": [101.0, 102.0, 103.5, None],
                        "volume": [400, 1000, 2000, 50],
                    }]},
                }]
            }
        }
        with patch.object(fs, "safe_yf_get", return_value=_FakeHttpResponse(chart_payload)), \
             patch("fetch_stocks.datetime") as dt_mock:
            dt_mock.now.return_value = fake_now_open
            dt_mock.fromtimestamp.side_effect = datetime.fromtimestamp
            chart_obs_open = fs._acquire_live_chart_observations_raw("AAPL", "OPEN")
            chart_obs_ah = fs._acquire_live_chart_observations_raw("AAPL", "AFTER-HOURS")
        self.assertEqual(chart_obs_open["price"].value, 103.5)
        self.assertEqual(chart_obs_open["price"].obs_time, _utc_iso(int(dt_1000.timestamp())))
        self.assertEqual(chart_obs_open["session_volume"].value, 3050)
        self.assertEqual(chart_obs_open["session_volume"].obs_time, _utc_iso(int(dt_1615.timestamp())))
        self.assertEqual(chart_obs_open["premarket_volume"].value, 400)
        self.assertEqual(chart_obs_open["premarket_volume"].obs_time, _utc_iso(int(dt_0800.timestamp())))
        self.assertEqual(chart_obs_ah["after_hours_volume"].value, 50)
        self.assertEqual(chart_obs_ah["after_hours_volume"].obs_time, _utc_iso(int(dt_1615.timestamp())))
        self.assertEqual(chart_obs_ah["session_volume"].value, 50)

    def test_25_provider_chart_open_finnhub_polygon_prev_and_yahoo_prev_close(self):
        today_ny = datetime.now(NY_TZ).date()
        fake_now_open = datetime(today_ny.year, today_ny.month, today_ny.day, 10, 30, tzinfo=NY_TZ)
        prev_day = today_ny - timedelta(days=1)
        prev_daily_dt = datetime(prev_day.year, prev_day.month, prev_day.day, 9, 30, tzinfo=NY_TZ)
        today_open_dt = datetime(today_ny.year, today_ny.month, today_ny.day, 9, 30, tzinfo=NY_TZ)
        future_day = today_ny + timedelta(days=2)
        future_dt = datetime(future_day.year, future_day.month, future_day.day, 9, 30, tzinfo=NY_TZ)

        # 1. Chart open pairs price with opening bar timestamp; rejects missing timestamp
        open_payload = {
            "chart": {
                "result": [{
                    "timestamp": [int(today_open_dt.timestamp())],
                    "indicators": {"quote": [{"open": [188.5]}]},
                }]
            }
        }
        with patch.object(fs, "safe_yf_get", return_value=_FakeHttpResponse(open_payload)), \
             patch("fetch_stocks.datetime") as dt_mock:
            dt_mock.now.return_value = fake_now_open
            dt_mock.fromtimestamp.side_effect = datetime.fromtimestamp
            co = fs._acquire_chart_open_raw("AAPL")
        self.assertEqual(co.value, 188.5)
        self.assertEqual(co.obs_time, _utc_iso(int(today_open_dt.timestamp())))
        self.assertEqual(co.source, "INTRADAY_CHART_OPEN")

        bad_open = {"chart": {"result": [{"timestamp": [None], "indicators": {"quote": [{"open": [188.5]}]}}]}}
        with patch.object(fs, "safe_yf_get", return_value=_FakeHttpResponse(bad_open)), \
             patch("fetch_stocks.datetime") as dt_mock:
            dt_mock.now.return_value = fake_now_open
            dt_mock.fromtimestamp.side_effect = datetime.fromtimestamp
            self.assertIsNone(fs._acquire_chart_open_raw("AAPL"))

        # 2. Finnhub raw extracts c paired with t, never pc
        fh_ts = _epoch_after(today_open_dt, 60)
        with patch.object(fs, "USE_FINNHUB", True), \
             patch.object(fs, "FINNHUB_API_KEY", "test-key"), \
             patch.object(fs.session, "get", return_value=_FakeHttpResponse({"c": 191.25, "pc": 185.0, "t": fh_ts})):
            fh_obs = fs._acquire_finnhub_quote_raw("AAPL")
        self.assertEqual(fh_obs.value, 191.25)
        self.assertEqual(fh_obs.obs_time, _utc_iso(fh_ts))
        self.assertEqual(fh_obs.source, "FINNHUB")

        with patch.object(fs, "USE_FINNHUB", True), \
             patch.object(fs, "FINNHUB_API_KEY", "test-key"), \
             patch.object(fs.session, "get", return_value=_FakeHttpResponse({"c": 0, "pc": 185.0, "t": fh_ts})):
            self.assertIsNone(fs._acquire_finnhub_quote_raw("AAPL"))

        # 3. Polygon previous aggregate -> SESSION_REFERENCE; never overwrites cache.volumes or calculate_rvol
        prev_ms = int(prev_daily_dt.timestamp()) * 1000
        with patch.object(fs, "USE_POLYGON", True), \
             patch.object(fs, "POLYGON_API_KEY", "poly-key"), \
             patch.object(fs.session, "get", return_value=_FakeHttpResponse({"resultsCount": 1, "results": [{"v": 9_876_543, "t": prev_ms}]})):
            poly_obs = fs._acquire_polygon_previous_agg_raw("AAPL")
        self.assertIsInstance(poly_obs, bi.SessionReferenceValue)
        self.assertEqual(poly_obs.value, 9_876_543)
        self.assertEqual(poly_obs.effective_session_date, prev_day.isoformat())
        self.assertEqual(poly_obs.source, "POLYGON_PREV_AGG")

        # Reject future / today Polygon bar date
        future_ms = int(future_dt.timestamp()) * 1000
        with patch.object(fs, "USE_POLYGON", True), \
             patch.object(fs, "POLYGON_API_KEY", "poly-key"), \
             patch.object(fs.session, "get", return_value=_FakeHttpResponse({"resultsCount": 1, "results": [{"v": 100, "t": future_ms}]})):
            self.assertIsNone(fs._acquire_polygon_previous_agg_raw("AAPL"))

        # 4. Yahoo previous close extracts prior daily bar (< today NY) + date; rejects same-day / NaN / future
        yc_payload = {
            "chart": {
                "result": [{
                    "meta": {"previousClose": 180.0},
                    "timestamp": [int(prev_daily_dt.timestamp()), int(today_open_dt.timestamp())],
                    "indicators": {"quote": [{"close": [184.25, 190.0]}]},
                }]
            }
        }
        with patch.object(fs, "safe_yf_get", return_value=_FakeHttpResponse(yc_payload)):
            yc_obs = fs._acquire_previous_close_raw("AAPL")
        self.assertIsInstance(yc_obs, bi.SessionReferenceValue)
        self.assertEqual(yc_obs.value, 184.25)
        self.assertEqual(yc_obs.effective_session_date, prev_day.isoformat())
        self.assertEqual(yc_obs.source, "YAHOO_DAILY_CLOSE")

        yc_same_day_only = {
            "chart": {
                "result": [{
                    "meta": {"previousClose": 180.0},
                    "timestamp": [int(today_open_dt.timestamp())],
                    "indicators": {"quote": [{"close": [190.0]}]},
                }]
            }
        }
        with patch.object(fs, "safe_yf_get", return_value=_FakeHttpResponse(yc_same_day_only)):
            self.assertIsNone(fs._acquire_previous_close_raw("AAPL"))

    def test_26_strict_fail_closed_fast_info_never_published_or_used(self):
        fast_info_all_fields = {
            "last_price": 150.0,
            "pre_market_price": 151.0,
            "post_market_price": 152.0,
            "open": 149.0,
            "day_high": 153.0,
            "day_low": 148.0,
            "last_volume": 100_000,
            "three_month_average_volume": 90_000,
            "regular_market_previous_close": 147.0,
            "previous_close": 147.0,
        }
        now_utc = _utc_iso(int(datetime.now(UTC_TZ).timestamp()) - 10)
        with _isolated_market_coordinators() as (mkt_coord, mkt_app, _, _):
            with patch.object(fs, "_extract_fast_info_snapshot", return_value=fast_info_all_fields) as fi_spy, \
                 patch.object(fs, "_acquire_live_chart_observations_raw", return_value={
                     "price": None, "session_volume": None, "premarket_volume": None,
                     "after_hours_volume": None, "chart_obs_utc_iso": None, "legacy_tuple": (None, 0, 0, 0, None),
                 }), \
                 patch.object(fs, "get_premarket_volume_chart", return_value=None), \
                 patch.object(fs, "_acquire_finnhub_quote_raw", return_value=None), \
                 patch.object(fs, "_acquire_polygon_previous_agg_raw", return_value=None), \
                 patch.object(fs, "_acquire_previous_close_raw", return_value=None), \
                 patch.object(fs, "_acquire_chart_open_raw", return_value=None):
                ok, _ = fs.enqueue_market_symbol_fallback(
                    "TSLA", "OPEN",
                    need_price=True, needs_pre_vol=True, needs_post_vol=True,
                    need_fast_info=True, need_prev_close=True, need_chart_open=True,
                )
                self.assertTrue(ok)
                self.assertTrue(_wait_workers_idle(mkt_coord))
                audit = fs.apply_background_market_data_results()
                fi_spy.assert_not_called()
                self.assertTrue(all(not ok for _, ok, _ in audit))
                self.assertNotIn("TSLA", fs.cache.prices)
                self.assertNotIn("TSLA", fs.cache.day_open)
                self.assertNotIn("TSLA", fs.cache.regular_close)
                self.assertNotIn("TSLA", fs.cache.volumes)

            # Even a manually injected WorkResult claiming FAST_INFO provenance is rejected by applicator
            for source_tag in ("FAST_INFO", "FAST_INFO_PRE"):
                res_fi = bi.WorkResult(
                    key=bi.WorkKey(bi.WorkType.MARKET_DATA.value, "QUOTE:TSLA"),
                    sequence=1,
                    obs_time=now_utc,
                    data={"kind": "metric", "metric": "QUOTE", "symbol": "TSLA", "status": "OPEN", "value": 150.0},
                    provenance=bi.MarketDataProvenance(bi.ObservationKind.INSTANT, source_tag),
                )
                audit_fi = mkt_app.apply_results(
                    [res_fi],
                    grounding_target={},
                    scout_cache_target={},
                    scout_category_map={},
                    latest_issued_provider=lambda _k: 1,
                    on_publish=lambda r: fs._publish_metric_cache_candidate(*fs._build_metric_cache_candidate(r)),
                )
                self.assertFalse(audit_fi[0][1])
                self.assertNotIn("TSLA", fs.cache.prices)

    def test_27_post_batch_supplement_triggers_session_reference_metrics_cleanly(self):
        now_ny = datetime.now(NY_TZ).replace(microsecond=0)
        ts_open = _epoch_after(now_ny, -15)
        prev_day = (now_ny.date() - timedelta(days=1)).isoformat()

        with _isolated_market_coordinators(max_workers=2) as (mkt_coord, _, _, _):
            with patch.object(fs, "USE_POLYGON", True), \
                 patch.object(
                     fs,
                     "get_batch_quotes",
                     return_value={
                         "AAPL": {
                             "regularMarketPrice": 210.0,
                             "regularMarketTime": ts_open,
                             "regularMarketVolume": 1_500_000,
                             "regularMarketOpen": 208.0,
                             "regularMarketDayHigh": 211.0,
                             "regularMarketDayLow": 207.5,
                         }
                     },
                 ), \
                 patch.object(fs, "get_premarket_volume_chart", return_value=None), \
                 patch.object(
                     fs,
                     "_acquire_previous_close_raw",
                     return_value=bi.SessionReferenceValue(205.0, prev_day, "YAHOO_DAILY_CLOSE"),
                 ), \
                 patch.object(
                     fs,
                     "_acquire_polygon_previous_agg_raw",
                     return_value=bi.SessionReferenceValue(8_000_000, prev_day, "POLYGON_PREV_AGG"),
                 ):
                # Cycle 1: enqueue batch
                fs.coordinate_market_data_cycle(["AAPL"], "OPEN", now_ny=now_ny)
                self.assertTrue(_wait_workers_idle(mkt_coord))

                # Cycle 2: apply batch (with existing volumes > 0) and enqueue supplemental prev_close + polygon_prev_vol
                stats2 = fs.coordinate_market_data_cycle(["AAPL"], "OPEN", now_ny=now_ny)
                self.assertGreaterEqual(stats2["applied_count"], 5)
                self.assertEqual(stats2["fallback_enqueued"], 1)
                self.assertTrue(_wait_workers_idle(mkt_coord))

                # Cycle 3: apply supplemental SESSION_REFERENCE results
                stats3 = fs.coordinate_market_data_cycle(["AAPL"], "OPEN", now_ny=now_ny)
                self.assertGreaterEqual(stats3["applied_count"], 2)

                # Verify clean separation between today's volume and previous_session_volume + regular_close
                self.assertEqual(fs.cache.volumes["AAPL"], 1_500_000)
                self.assertEqual(fs.cache.previous_session_volume["AAPL"], 8_000_000)
                self.assertEqual(fs.cache.previous_session_volume_dates["AAPL"], prev_day)
                self.assertAlmostEqual(fs.cache.regular_close["AAPL"], 205.0)
                self.assertEqual(fs.cache.regular_close_dates["AAPL"], prev_day)
                self.assertAlmostEqual(fs.cache.prices["AAPL"], 210.0)

    def test_28_aggregate_volume_observation_timestamps_latest_valid_bar(self):
        """
        Finding 1 Regression:
        Aggregate volume observation timestamps must reflect the latest valid contributing bar,
        including later bars whose volume is zero. Earlier positive-volume bars must not override
        the latest valid timestamp. Applies consistently across pre-market, regular-session,
        and after-hours volume aggregates, preserving the distinction between valid zero and missing bars.
        """
        today_ny = datetime.now(NY_TZ).date()
        fake_now = datetime(today_ny.year, today_ny.month, today_ny.day, 17, 0, tzinfo=NY_TZ)

        # Pre-market bars: 08:00 (vol=500, positive), 08:30 (vol=0, valid zero)
        dt_0800 = datetime(today_ny.year, today_ny.month, today_ny.day, 8, 0, tzinfo=NY_TZ)
        dt_0830 = datetime(today_ny.year, today_ny.month, today_ny.day, 8, 30, tzinfo=NY_TZ)
        # Regular market bars: 09:35 (vol=1000, positive), 10:00 (vol=2000, positive), 10:15 (vol=0, valid zero)
        dt_0935 = datetime(today_ny.year, today_ny.month, today_ny.day, 9, 35, tzinfo=NY_TZ)
        dt_1000 = datetime(today_ny.year, today_ny.month, today_ny.day, 10, 0, tzinfo=NY_TZ)
        dt_1015 = datetime(today_ny.year, today_ny.month, today_ny.day, 10, 15, tzinfo=NY_TZ)
        # After-hours bars: 16:15 (vol=200, positive), 16:30 (vol=0, valid zero)
        dt_1615 = datetime(today_ny.year, today_ny.month, today_ny.day, 16, 15, tzinfo=NY_TZ)
        dt_1630 = datetime(today_ny.year, today_ny.month, today_ny.day, 16, 30, tzinfo=NY_TZ)

        chart_payload = {
            "chart": {
                "result": [{
                    "timestamp": [
                        int(dt_0800.timestamp()),
                        int(dt_0830.timestamp()),
                        int(dt_0935.timestamp()),
                        int(dt_1000.timestamp()),
                        int(dt_1015.timestamp()),
                        int(dt_1615.timestamp()),
                        int(dt_1630.timestamp()),
                    ],
                    "indicators": {"quote": [{
                        "close": [100.0, 100.5, 101.0, 102.0, 102.0, 103.0, 103.0],
                        "volume": [500, 0, 1000, 2000, 0, 200, 0],
                    }]},
                }]
            }
        }

        with patch.object(fs, "safe_yf_get", return_value=_FakeHttpResponse(chart_payload)), \
             patch("fetch_stocks.datetime") as dt_mock:
            dt_mock.now.return_value = fake_now
            dt_mock.fromtimestamp.side_effect = datetime.fromtimestamp

            chart_open = fs._acquire_live_chart_observations_raw("AAPL", "OPEN")
            chart_ah = fs._acquire_live_chart_observations_raw("AAPL", "AFTER-HOURS")

        # Pre-market aggregate: total volume is 500, but latest valid bar is dt_0830 (vol=0)
        self.assertIsNotNone(chart_open["premarket_volume"])
        self.assertEqual(chart_open["premarket_volume"].value, 500)
        self.assertEqual(chart_open["premarket_volume"].obs_time, _utc_iso(int(dt_0830.timestamp())))
        self.assertNotEqual(chart_open["premarket_volume"].obs_time, _utc_iso(int(dt_0800.timestamp())))

        # Regular-session aggregate: in OPEN session, cutoff is 09:30, all bars >= 09:30 contribute
        # Total session volume includes 1000 + 2000 + 0 + 200 + 0 = 3200
        # Latest valid bar timestamp is dt_1630 (vol=0)
        self.assertIsNotNone(chart_open["session_volume"])
        self.assertEqual(chart_open["session_volume"].value, 3200)
        self.assertEqual(chart_open["session_volume"].obs_time, _utc_iso(int(dt_1630.timestamp())))

        # After-hours aggregate: bars between 16:00 and 20:00 (vol=200 at 16:15, vol=0 at 16:30)
        # Total after-hours volume is 200, but latest valid bar timestamp is dt_1630 (vol=0)
        self.assertIsNotNone(chart_ah["after_hours_volume"])
        self.assertEqual(chart_ah["after_hours_volume"].value, 200)
        self.assertEqual(chart_ah["after_hours_volume"].obs_time, _utc_iso(int(dt_1630.timestamp())))
        self.assertNotEqual(chart_ah["after_hours_volume"].obs_time, _utc_iso(int(dt_1615.timestamp())))

        # Valid zero-volume bars (all bars 0) must remain distinguishable from missing data (None)
        zero_payload = {
            "chart": {
                "result": [{
                    "timestamp": [int(dt_0800.timestamp()), int(dt_0830.timestamp())],
                    "indicators": {"quote": [{
                        "close": [100.0, 100.0],
                        "volume": [0, 0],
                    }]},
                }]
            }
        }
        with patch.object(fs, "safe_yf_get", return_value=_FakeHttpResponse(zero_payload)), \
             patch("fetch_stocks.datetime") as dt_mock:
            dt_mock.now.return_value = fake_now
            dt_mock.fromtimestamp.side_effect = datetime.fromtimestamp

            chart_zero = fs._acquire_live_chart_observations_raw("AAPL", "OPEN")

        # Pre-market had bars, all 0 -> valid TimestampedValue with 0 and dt_0830 timestamp
        self.assertIsNotNone(chart_zero["premarket_volume"])
        self.assertEqual(chart_zero["premarket_volume"].value, 0)
        self.assertEqual(chart_zero["premarket_volume"].obs_time, _utc_iso(int(dt_0830.timestamp())))

        # After-hours had no bars -> None (missing, distinguishable from observed 0)
        self.assertIsNone(chart_zero["after_hours_volume"])

    def test_29_chart_and_premarket_ttl_metadata_propagation_and_boundaries(self):
        """
        Finding 2 Regression:
        - Retrieval timestamps are captured immediately after successful acquisition, not before.
        - Delayed acquisition does not consume TTL time while the request is running.
        - Failed, empty or invalid acquisitions do not update TTL metadata.
        - Provider observation timestamps (obs_time) remain independent of retrieval TTL timestamps.
        - Existing 60-second chart and 90-second pre-market TTL expiration boundaries remain correct.
        - Chart cache tuple conforms to the exact 4-tuple structure consumed by existing code
          (price, session_volume, premarket_volume, after_hours_volume).
        - Malformed metadata (booleans, non-finite values, malformed tuples, invalid volumes)
          cannot update TTL state.
        """
        today_ny = datetime.now(NY_TZ).date()
        fake_now = datetime(today_ny.year, today_ny.month, today_ny.day, 10, 0, tzinfo=NY_TZ)
        dt_bar = datetime(today_ny.year, today_ny.month, today_ny.day, 9, 35, tzinfo=NY_TZ)
        obs_iso = _utc_iso(int(dt_bar.timestamp()))

        # Controlled clock timeline:
        # T0 = 1000.0: Fallback acquisition starts
        # T1 = 1015.0: Chart acquisition finishes (15s duration)
        # T2 = 1025.0: Premarket volume acquisition finishes (10s duration)
        t_start = 1000.0
        t_chart_done = 1015.0
        t_pm_done = 1025.0

        chart_legacy_5_tuple = (185.0, 50000, 2000, 0, obs_iso)
        chart_consumed_4_tuple = (185.0, 50000, 2000, 0)
        chart_ok = {
            "price": bi.TimestampedValue(185.0, obs_iso, "INTRADAY_CHART"),
            "session_volume": bi.TimestampedValue(50000, obs_iso, "INTRADAY_CHART_VOLUME"),
            "premarket_volume": bi.TimestampedValue(2000, obs_iso, "INTRADAY_CHART_PREMARKET_VOLUME"),
            "after_hours_volume": None,
            "chart_obs_utc_iso": obs_iso,
            "legacy_tuple": chart_legacy_5_tuple,
        }
        pm_obs = fs.PremarketVolumeObservation(
            value=12500,
            obs_time=obs_iso,
            effective_session_date=today_ny.isoformat(),
            source="PREMARKET_VOLUME_CHART",
        )

        def mock_acquire_chart(_sym, _status):
            return chart_ok

        def mock_acquire_pm(_sym):
            return pm_obs

        # Clock returns t_chart_done when called after chart acquisition,
        # then t_pm_done when called after premarket acquisition.
        with patch.object(fs, "_acquire_live_chart_observations_raw", side_effect=mock_acquire_chart), \
             patch.object(fs, "get_premarket_volume_chart", side_effect=mock_acquire_pm), \
             patch("fetch_stocks.t_time.time", side_effect=[t_chart_done, t_pm_done]):
            batch_res = fs._acquire_symbol_fallback_raw(
                "AAPL", "OPEN", need_price=True, fetch_chart=True, fetch_premarket_vol=True
            )

        items_map = {item.key.target: item for item in batch_res.items}

        # 1. Retrieval timestamp captured after acquisition: matches completion time, not start time
        q_item = items_map["QUOTE:AAPL"]
        self.assertIn("chart_cache_meta", q_item.data)
        chart_meta = q_item.data["chart_cache_meta"]
        self.assertEqual(chart_meta["tuple"], chart_consumed_4_tuple)
        self.assertEqual(chart_meta["ts"], t_chart_done)
        self.assertNotEqual(chart_meta["ts"], t_start)
        # Provider observation timestamp is strictly independent of local retrieval timestamp
        self.assertEqual(q_item.obs_time, obs_iso)
        self.assertNotEqual(q_item.obs_time, t_chart_done)

        pv_item = items_map["PREMARKET_VOLUME:AAPL"]
        self.assertIn("premarket_vol_cache_meta", pv_item.data)
        pm_meta = pv_item.data["premarket_vol_cache_meta"]
        self.assertEqual(pm_meta["value"], 12500)
        self.assertEqual(pm_meta["ts"], t_pm_done)
        self.assertNotEqual(pm_meta["ts"], t_start)
        self.assertEqual(pv_item.obs_time, obs_iso)

        # 2. Delayed acquisition does not consume TTL time while running:
        # At T = 1065.0:
        # Elapsed since acquisition start = 65s (> 60s chart TTL)
        # Elapsed since acquisition completion = 1065.0 - 1015.0 = 50s (< 60s chart TTL!)
        sym_q, m_q, updates_q, prov_q = fs._build_metric_cache_candidate(q_item, now_ny=fake_now)
        self.assertEqual(updates_q["last_chart_fetch"], t_chart_done)
        self.assertEqual(updates_q["chart_cache"], chart_consumed_4_tuple)
        fs._publish_metric_cache_candidate(sym_q, m_q, updates_q, prov_q)

        now_eval = 1065.0
        self.assertLess(now_eval - fs.cache.last_chart_fetch["AAPL"], 60.0)

        sym_pv, m_pv, updates_pv, prov_pv = fs._build_metric_cache_candidate(pv_item, now_ny=fake_now)
        self.assertEqual(updates_pv["last_premarket_vol_fetch"], t_pm_done)
        self.assertEqual(updates_pv["premarket_vol_cache"], 12500)
        fs._publish_metric_cache_candidate(sym_pv, m_pv, updates_pv, prov_pv)

        # Verify chart cache unpacks cleanly into 4 variables as expected by existing code
        c_price, c_vol, c_pre, c_post = fs.cache.chart_cache["AAPL"]
        self.assertEqual(c_price, 185.0)
        self.assertEqual(c_vol, 50000)
        self.assertEqual(c_pre, 2000)
        self.assertEqual(c_post, 0)

        # 3. Malformed metadata validation before staging cache updates:
        # Must reject: booleans, non-finite floats, non-positive timestamps, malformed tuples, invalid volumes
        malformed_cases_chart = [
            {"tuple": "not_a_tuple", "ts": 1000.0},
            {"tuple": (185.0, 50000, 2000), "ts": 1000.0},  # len 3 != 4
            {"tuple": (185.0, 50000, 2000, 0, "extra"), "ts": 1000.0},  # len 5 != 4
            {"tuple": (True, 50000, 2000, 0), "ts": 1000.0},  # boolean price
            {"tuple": (0.0, 50000, 2000, 0), "ts": 1000.0},  # zero price
            {"tuple": (-10.0, 50000, 2000, 0), "ts": 1000.0},  # negative price
            {"tuple": (float("nan"), 50000, 2000, 0), "ts": 1000.0},  # NaN price
            {"tuple": (185.0, True, 2000, 0), "ts": 1000.0},  # boolean volume
            {"tuple": (185.0, -100, 2000, 0), "ts": 1000.0},  # negative volume
            {"tuple": (185.0, 50000, float("inf"), 0), "ts": 1000.0},  # infinite volume
            {"tuple": (185.0, 50000, 2000, 0), "ts": True},  # boolean timestamp
            {"tuple": (185.0, 50000, 2000, 0), "ts": -10.0},  # negative timestamp
            {"tuple": (185.0, 50000, 2000, 0), "ts": 0.0},  # zero timestamp
            {"tuple": (185.0, 50000, 2000, 0), "ts": float("nan")},  # NaN timestamp
            {"tuple": (185.0, 50000, 2000, 0), "ts": float("inf")},  # Inf timestamp
        ]
        for idx, bad_meta in enumerate(malformed_cases_chart):
            res = bi.ChildObservedResult(
                key=bi.WorkKey(bi.WorkType.MARKET_DATA.value, f"QUOTE:BAD_{idx}"),
                data={"kind": "metric", "metric": "QUOTE", "symbol": f"BAD_{idx}", "value": 100.0,
                      "chart_cache_meta": bad_meta},
                obs_time=obs_iso,
                error=None,
                provenance=bi.MarketDataProvenance(bi.ObservationKind.INSTANT, "TEST"),
            )
            _, _, up, _ = fs._build_metric_cache_candidate(res, now_ny=fake_now)
            self.assertNotIn("last_chart_fetch", up, f"Case {idx} staged last_chart_fetch")
            self.assertNotIn("chart_cache", up, f"Case {idx} staged chart_cache")

        malformed_cases_pm = [
            {"value": True, "ts": 1000.0},  # boolean value
            {"value": -50, "ts": 1000.0},  # negative value
            {"value": float("nan"), "ts": 1000.0},  # NaN value
            {"value": float("inf"), "ts": 1000.0},  # Inf value
            {"value": 1000, "ts": True},  # boolean timestamp
            {"value": 1000, "ts": -5.0},  # negative timestamp
            {"value": 1000, "ts": 0.0},  # zero timestamp
            {"value": 1000, "ts": float("nan")},  # NaN timestamp
        ]
        for idx, bad_pm_meta in enumerate(malformed_cases_pm):
            res = bi.ChildObservedResult(
                key=bi.WorkKey(bi.WorkType.MARKET_DATA.value, f"PREMARKET_VOLUME:BAD_{idx}"),
                data={"kind": "metric", "metric": "PREMARKET_VOLUME", "symbol": f"BAD_{idx}", "value": 100,
                      "premarket_vol_cache_meta": bad_pm_meta},
                obs_time=obs_iso,
                error=None,
                provenance=bi.MarketDataProvenance(bi.ObservationKind.INSTANT, "TEST"),
            )
            _, _, up, _ = fs._build_metric_cache_candidate(res, now_ny=fake_now)
            self.assertNotIn("last_premarket_vol_fetch", up, f"Case {idx} staged last_premarket_vol_fetch")
            self.assertNotIn("premarket_vol_cache", up, f"Case {idx} staged premarket_vol_cache")

        # 4. Failed acquisition does NOT refresh TTL metadata
        empty_chart = {
            "price": None, "session_volume": None, "premarket_volume": None,
            "after_hours_volume": None, "chart_obs_utc_iso": None,
            "legacy_tuple": (None, 0, 0, 0, None),
        }
        with patch.object(fs, "_acquire_live_chart_observations_raw", return_value=empty_chart), \
             patch.object(fs, "get_premarket_volume_chart", return_value=None):
            fail_batch = fs._acquire_symbol_fallback_raw(
                "GOOGL", "OPEN", need_price=True, fetch_chart=True, fetch_premarket_vol=True
            )
        for child in fail_batch.items:
            if child.data is not None:
                self.assertNotIn("chart_cache_meta", child.data)
                self.assertNotIn("premarket_vol_cache_meta", child.data)
        self.assertNotIn("GOOGL", fs.cache.last_chart_fetch)
        self.assertNotIn("GOOGL", fs.cache.last_premarket_vol_fetch)

        # 5. Subsequent acquisition respects TTL boundaries (60s chart, 90s pre-market)
        with _isolated_market_coordinators(max_workers=1) as (mkt_coord, _, _, _):
            with patch.object(mkt_coord, "enqueue", wraps=mkt_coord.enqueue) as enq_spy, \
                 patch.object(fs, "_acquire_symbol_fallback_raw", return_value=bi.ObservedResult(data=None, obs_time=None)):
                # Fresh (< 60s, < 90s) -> both False
                fs.cache.last_chart_fetch["AAPL"] = time.time() - 25
                fs.cache.last_premarket_vol_fetch["AAPL"] = time.time() - 40
                fs.enqueue_market_symbol_fallback("AAPL", "PRE-MARKET", need_price=True, needs_pre_vol=True)
                self.assertFalse(enq_spy.call_args.kwargs["kwargs"]["fetch_chart"])
                self.assertFalse(enq_spy.call_args.kwargs["kwargs"]["fetch_premarket_vol"])

                # Expired (> 60s, > 90s) -> both True
                fs.cache.last_chart_fetch["AAPL"] = time.time() - 65
                fs.cache.last_premarket_vol_fetch["AAPL"] = time.time() - 95
                fs.enqueue_market_symbol_fallback("AAPL", "PRE-MARKET", need_price=True, needs_pre_vol=True)
                self.assertTrue(enq_spy.call_args.kwargs["kwargs"]["fetch_chart"])
                self.assertTrue(enq_spy.call_args.kwargs["kwargs"]["fetch_premarket_vol"])

    def test_30_atomic_rollback_of_cache_and_provenance_publication(self):
        """
        Finding 3 Regression:
        Publication of market-data cache candidates and provenance records is strictly atomic.
        If an assignment mutates its target mapping and then raises an exception, or if an assignment
        fails before mutating:
        1. Cache mapping that mutates and then raises: rollback restores exact prior value for existing keys
           or removes newly introduced keys.
        2. Nested provenance mapping that mutates and then raises: rollback restores exact prior provenance
           for existing metrics or removes newly introduced metric entries, preserving the symbol entry.
        3. Newly created provenance symbol mapping whose nested assignment mutates and raises:
           rollback completely removes the newly created symbol entry.
        4. Primary exception is preserved and re-raised with diagnostic notes.
        5. Applicator sequence, observation timestamp and provenance ordering do NOT advance on failure.
        6. Subsequent valid publication succeeds.
        """
        today_ny = datetime.now(NY_TZ).date()
        fake_now = datetime(today_ny.year, today_ny.month, today_ny.day, 10, 0, tzinfo=NY_TZ)
        obs_iso = _utc_iso(int(fake_now.timestamp()))

        class MutateAndRaiseDict(dict):
            """Controlled fixture mapping that sets item and then raises on first attempt."""
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.failed_once = False

            def __setitem__(self, key, value):
                super().__setitem__(key, value)
                if not self.failed_once:
                    self.failed_once = True
                    raise RuntimeError(f"Simulated mutate-then-raise failure for {key!r}")

        # ---------------------------------------------------------------------
        # 1. Cache mapping mutate-then-raise:
        #    1a. Key existed prior to mutation: restored to exact previous value
        #    1b. Key did not exist prior to mutation: removed on rollback
        # ---------------------------------------------------------------------
        fs.cache.prices["AAPL"] = 150.0
        fs.cache.volumes["AAPL"] = 1_000_000
        fs.cache.day_open.pop("AAPL", None)

        failing_gaps = MutateAndRaiseDict({"AAPL": 2.5})
        with patch.object(fs.cache, "gaps", failing_gaps):
            candidate = {
                "prices": 160.0,
                "volumes": 2_000_000,
                "day_open": 155.0,
                "gaps": 5.0,  # Will mutate failing_gaps["AAPL"] = 5.0, then raise
            }
            with self.assertRaises(RuntimeError) as cm:
                fs._publish_quote_cache_candidate("AAPL", candidate)
            self.assertIn("Simulated mutate-then-raise failure for 'AAPL'", str(cm.exception))

        # Gaps was mutated to 5.0 before raising, but rollback restored it to 2.5!
        self.assertEqual(failing_gaps["AAPL"], 2.5)
        # Earlier mutated fields were also rolled back
        self.assertEqual(fs.cache.prices["AAPL"], 150.0)
        self.assertEqual(fs.cache.volumes["AAPL"], 1_000_000)
        self.assertNotIn("AAPL", fs.cache.day_open)

        # 1b. Key did not exist in failing cache mapping before write
        failing_after_hours = MutateAndRaiseDict()
        self.assertNotIn("AAPL", failing_after_hours)
        with patch.object(fs.cache, "after_hours_price", failing_after_hours):
            candidate2 = {
                "prices": 165.0,
                "after_hours_price": 166.0,  # Will mutate then raise
            }
            with self.assertRaises(RuntimeError) as cm2:
                fs._publish_quote_cache_candidate("AAPL", candidate2)
            self.assertIn("Simulated mutate-then-raise failure for 'AAPL'", str(cm2.exception))

        # Rollback removed the newly added key from after_hours_price
        self.assertNotIn("AAPL", failing_after_hours)
        self.assertEqual(fs.cache.prices["AAPL"], 150.0)

        # ---------------------------------------------------------------------
        # 2. Existing nested provenance mapping mutate-then-raise:
        #    2a. Metric already existed: restored to exact previous value
        #    2b. Metric was newly added: removed, existing symbol mapping preserved
        # ---------------------------------------------------------------------
        fs.cache.prices["AAPL"] = 150.0
        orig_day_open_prov = {"kind": "INSTANT", "source": "YAHOO", "timestamp": obs_iso}
        orig_quote_prov = {"kind": "INSTANT", "source": "BATCH_QUOTE", "timestamp": obs_iso}

        # 2a. Update existing metric in nested provenance
        nested_prov_2a = MutateAndRaiseDict({
            "DAY_OPEN": orig_day_open_prov,
            "QUOTE": orig_quote_prov,
        })
        fs.cache.metric_provenance["AAPL"] = nested_prov_2a

        new_quote_prov = {"kind": "INSTANT", "source": "INTRADAY_CHART", "timestamp": obs_iso}
        with self.assertRaises(RuntimeError) as cm3:
            fs._publish_metric_cache_candidate(
                "AAPL",
                "QUOTE",
                {"prices": 175.0},
                new_quote_prov,
            )
        self.assertIn("Simulated mutate-then-raise failure for 'QUOTE'", str(cm3.exception))
        # Verify QUOTE provenance was restored to orig_quote_prov
        self.assertEqual(nested_prov_2a["QUOTE"], orig_quote_prov)
        self.assertEqual(nested_prov_2a["DAY_OPEN"], orig_day_open_prov)
        self.assertEqual(fs.cache.prices["AAPL"], 150.0)

        # 2b. Add new metric to existing symbol provenance
        nested_prov_2b = MutateAndRaiseDict({
            "DAY_OPEN": orig_day_open_prov,
        })
        fs.cache.metric_provenance["AAPL"] = nested_prov_2b
        with self.assertRaises(RuntimeError) as cm4:
            fs._publish_metric_cache_candidate(
                "AAPL",
                "QUOTE",
                {"prices": 180.0},
                new_quote_prov,
            )
        self.assertIn("Simulated mutate-then-raise failure for 'QUOTE'", str(cm4.exception))
        # Verify QUOTE metric was removed, but symbol entry and DAY_OPEN remain intact
        self.assertIn("AAPL", fs.cache.metric_provenance)
        self.assertNotIn("QUOTE", nested_prov_2b)
        self.assertEqual(nested_prov_2b["DAY_OPEN"], orig_day_open_prov)
        self.assertEqual(fs.cache.prices["AAPL"], 150.0)

        # ---------------------------------------------------------------------
        # 3. Newly created provenance symbol mapping whose assignment mutates and raises:
        #    Verify no symbol entry remains
        # ---------------------------------------------------------------------
        fs.cache.metric_provenance.pop("NVDA", None)
        fs.cache.prices.pop("NVDA", None)

        class MutateAndRaiseOuterDict(dict):
            """Fails after setting inner dictionary for a new symbol."""
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.failed_once = False

            def __setitem__(self, key, value):
                super().__setitem__(key, value)
                if not self.failed_once:
                    self.failed_once = True
                    raise RuntimeError(f"Simulated outer mutate-then-raise failure for {key!r}")

        with patch.object(fs.cache, "metric_provenance", MutateAndRaiseOuterDict()):
            with self.assertRaises(RuntimeError) as cm5:
                fs._publish_metric_cache_candidate(
                    "NVDA",
                    "QUOTE",
                    {"prices": 500.0},
                    {"kind": "INSTANT", "source": "INTRADAY_CHART", "timestamp": obs_iso},
                )
            self.assertIn("Simulated outer mutate-then-raise failure for 'NVDA'", str(cm5.exception))
            # Verify no symbol entry remains in metric_provenance
            self.assertNotIn("NVDA", fs.cache.metric_provenance)
            self.assertNotIn("NVDA", fs.cache.prices)

        # ---------------------------------------------------------------------
        # 4. Applicator sequence, obs_time and provenance do NOT advance on failure
        # ---------------------------------------------------------------------
        with _isolated_market_coordinators(max_workers=1) as (mkt_coord, mkt_app, _, _):
            key = bi.WorkKey(bi.WorkType.MARKET_DATA.value, "QUOTE:AAPL")
            work_res = bi.WorkResult(
                key=key,
                data={"kind": "metric", "metric": "QUOTE", "symbol": "AAPL", "value": 180.0},
                obs_time=obs_iso,
                error=None,
                sequence=10,
                provenance=bi.MarketDataProvenance(bi.ObservationKind.INSTANT, "INTRADAY_CHART"),
            )

            def _failing_publish(res):
                raise RuntimeError("Publication failed inside callback")

            audit = mkt_app.apply_results(
                results=[work_res],
                grounding_target={},
                scout_cache_target={},
                scout_category_map={},
                on_publish=_failing_publish,
            )
            self.assertEqual(len(audit), 1)
            self.assertFalse(audit[0][1])  # applied = False
            self.assertIn("Publication failed", audit[0][2])

            seq, obs_t = mkt_app.get_last_applied(key)
            self.assertEqual(seq, -1)
            self.assertEqual(obs_t, "")
            self.assertIsNone(mkt_app.get_last_applied_provenance(key))

            # ---------------------------------------------------------------------
            # 5. Subsequent valid publication succeeds after failed attempt
            # ---------------------------------------------------------------------
            fs.cache.metric_provenance["AAPL"] = {}

            def _working_publish(res):
                fs._publish_metric_cache_candidate(
                    "AAPL",
                    "QUOTE",
                    {"prices": 180.0},
                    {"kind": "INSTANT", "source": "INTRADAY_CHART", "timestamp": obs_iso},
                )

            audit_retry = mkt_app.apply_results(
                results=[work_res],
                grounding_target={},
                scout_cache_target={},
                scout_category_map={},
                on_publish=_working_publish,
            )
            self.assertEqual(len(audit_retry), 1)
            self.assertTrue(audit_retry[0][1])  # applied = True
            seq_ok, obs_t_ok = mkt_app.get_last_applied(key)
            self.assertEqual(seq_ok, 10)
            self.assertEqual(obs_t_ok, obs_iso)
            self.assertEqual(fs.cache.prices["AAPL"], 180.0)
            self.assertEqual(fs.cache.metric_provenance["AAPL"]["QUOTE"]["source"], "INTRADAY_CHART")


if __name__ == "__main__":
    unittest.main()
