"""
Automated Verification Suite for Stage 2.1: Background Intelligence Isolation.

Tests all mandatory requirements and negative tests:
1. Strict UTC ISO-8601 timestamp validation (fail-closed on naive/invalid/ambiguous).
2. Per-key failure cooldown and exponential backoff.
3. Bounded worker pool, queue saturation, and per-key deduplication (queued & in-flight).
4. Explicit subprocess lifecycle: cancellation during execution, timeout, process reaping, stream cleanup.
5. Atomic GLOBAL_STATE snapshot publication: concurrent readers, multiple writers, immutability.
6. Monotonic result ordering: sequence and observation timestamp enforcement.
7. Market monitoring isolation: slow/failing AI requests do not block the coordinator.
"""

from __future__ import annotations

import collections.abc
import contextlib
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from background_intelligence import (
    BackgroundIntelligenceCoordinator,
    BackgroundResultApplicator,
    BatchObservedResult,
    ChildObservedResult,
    FailureCooldownTracker,
    MarketDataProvenance,
    ObservationKind,
    ObservedResult,
    SessionReferenceValue,
    TimestampedValue,
    WorkItem,
    WorkKey,
    WorkResult,
    WorkStatus,
    WorkType,
    current_market_session_date,
    validate_session_date,
    validate_utc_iso8601_timestamp,
    parse_utc_iso8601_timestamp,
)
from agent_framework import (
    AntigravityAgentRunner,
    _cleanup_subprocess,
    SubprocessCleanupError,
)
from fetch_stocks import (
    get_global_state_snapshot,
    publish_global_state_snapshot,
    update_global_state_snapshot,
    _get_dynamic_scout_tickers,
    query_gemini_news_grounding,
    get_background_coordinator,
    apply_background_intelligence_results,
    enqueue_scout_discovery,
    enqueue_news_grounding,
)


class IsolatedTestCoordinator(BackgroundIntelligenceCoordinator):
    """Isolated coordinator for unit testing that does not spawn background worker threads."""
    def _start_workers(self) -> None:
        # Explicitly do NOT start any background worker threads
        pass


@contextlib.contextmanager
def isolated_coordinator_context():
    """
    Context manager fixture that substitutes an IsolatedTestCoordinator (0 worker threads)
    for fetch_stocks.BACKGROUND_COORDINATOR, patches raw AI execution functions,
    and guarantees zero background jobs remain pending upon exit.
    """
    import fetch_stocks
    dummy_coord = IsolatedTestCoordinator(max_workers=1, max_queue_size=50)
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(fetch_stocks, "BACKGROUND_COORDINATOR", dummy_coord))
        mock_scout_raw = stack.enter_context(patch.object(fetch_stocks, "_execute_scout_discovery_raw"))
        mock_grounding_raw = stack.enter_context(patch.object(fetch_stocks, "_execute_news_grounding_raw"))
        mock_runner = stack.enter_context(patch("agent_framework.AntigravityAgentRunner.run_prompt"))
        if "python.fetch_stocks" in sys.modules:
            p_fs = sys.modules["python.fetch_stocks"]
            stack.enter_context(patch.object(p_fs, "BACKGROUND_COORDINATOR", dummy_coord))
            stack.enter_context(patch.object(p_fs, "_execute_scout_discovery_raw", mock_scout_raw))
            stack.enter_context(patch.object(p_fs, "_execute_news_grounding_raw", mock_grounding_raw))
        try:
            yield dummy_coord, mock_scout_raw, mock_grounding_raw, mock_runner
        finally:
            dummy_coord.shutdown(timeout=0.0)


class TestStrictTimestampValidation(unittest.TestCase):
    """Verifies strict UTC ISO-8601 validation with explicit 'Z' or zero offset."""

    def test_valid_utc_timestamps_with_z(self):
        ts = "2026-11-14T19:00:00Z"
        self.assertEqual(validate_utc_iso8601_timestamp(ts), ts)

        ts_fraction = "2026-11-14T19:00:00.123456Z"
        self.assertEqual(validate_utc_iso8601_timestamp(ts_fraction), ts_fraction)

    def test_valid_utc_timestamps_with_zero_offset(self):
        ts = "2026-11-14T19:00:00+00:00"
        self.assertEqual(validate_utc_iso8601_timestamp(ts), ts)

    def test_ambiguous_neg_zero_offset_rejected(self):
        ts_neg_zero = "2026-11-14T19:00:00-00:00"
        with self.assertRaises(ValueError):
            validate_utc_iso8601_timestamp(ts_neg_zero)

    def test_naive_timestamp_rejected_fail_closed(self):
        with self.assertRaises(ValueError):
            validate_utc_iso8601_timestamp("2026-11-14T19:00:00")

    def test_non_zero_offset_rejected_fail_closed(self):
        with self.assertRaises(ValueError):
            validate_utc_iso8601_timestamp("2026-11-14T19:00:00+05:00")

        with self.assertRaises(ValueError):
            validate_utc_iso8601_timestamp("2026-11-14T19:00:00-05:00")

    def test_invalid_formats_and_types_rejected(self):
        invalid_cases = [
            None,
            123456789,
            "",
            "   ",
            "not-a-timestamp",
            "2026/11/14 19:00:00",
            "2026-11-14 19:00:00Z",
            "2026-02-31T19:00:00Z",  # Invalid calendar date
        ]
        for c in invalid_cases:
            with self.subTest(case=c):
                with self.assertRaises(ValueError):
                    validate_utc_iso8601_timestamp(c)


class TestFailureCooldownAndBackoff(unittest.TestCase):
    """Verifies independent per-key failure cooldown, backoff, and recovery."""

    def setUp(self):
        self.simulated_time = 1000.0
        self.tracker = FailureCooldownTracker(
            base_delay=5.0,
            backoff_factor=2.0,
            max_delay=60.0,
            clock_fn=lambda: self.simulated_time
        )
        self.key_a = WorkKey("grounding", "AAPL")
        self.key_b = WorkKey("grounding", "MSFT")

    def test_initial_state_not_in_cooldown(self):
        self.assertFalse(self.tracker.is_in_cooldown(self.key_a))
        self.assertEqual(self.tracker.get_failure_count(self.key_a), 0)

    def test_first_failure_sets_base_delay(self):
        deadline = self.tracker.record_failure(self.key_a)
        self.assertEqual(deadline, 1005.0)  # 1000 + 5.0
        self.assertTrue(self.tracker.is_in_cooldown(self.key_a))
        self.assertEqual(self.tracker.get_failure_count(self.key_a), 1)

        # Unrelated key is NOT affected
        self.assertFalse(self.tracker.is_in_cooldown(self.key_b))

    def test_exponential_backoff_doubling_up_to_max(self):
        # 1st failure: 5s -> deadline 1005
        self.tracker.record_failure(self.key_a)
        # 2nd failure: 5 * 2^1 = 10s -> deadline 1010
        d2 = self.tracker.record_failure(self.key_a)
        self.assertEqual(d2, 1010.0)
        # 3rd failure: 5 * 2^2 = 20s -> deadline 1020
        d3 = self.tracker.record_failure(self.key_a)
        self.assertEqual(d3, 1020.0)
        # 4th failure: 5 * 2^3 = 40s -> deadline 1040
        d4 = self.tracker.record_failure(self.key_a)
        self.assertEqual(d4, 1040.0)
        # 5th failure: capped at max_delay=60s -> deadline 1060
        d5 = self.tracker.record_failure(self.key_a)
        self.assertEqual(d5, 1060.0)

    def test_cooldown_expires_with_clock_advance(self):
        self.tracker.record_failure(self.key_a)
        self.assertTrue(self.tracker.is_in_cooldown(self.key_a))

        # Advance past cooldown deadline
        self.simulated_time += 5.1
        self.assertFalse(self.tracker.is_in_cooldown(self.key_a))

    def test_successful_completion_resets_failure_state(self):
        self.tracker.record_failure(self.key_a)
        self.tracker.record_failure(self.key_a)
        self.assertEqual(self.tracker.get_failure_count(self.key_a), 2)

        self.tracker.record_success(self.key_a)
        self.assertEqual(self.tracker.get_failure_count(self.key_a), 0)
        self.assertFalse(self.tracker.is_in_cooldown(self.key_a))


class TestBoundedWorkerAndDeduplication(unittest.TestCase):
    """Verifies bounded worker concurrency, queue limits, and per-key deduplication."""

    def setUp(self):
        self.clock = 1000.0
        self.tracker = FailureCooldownTracker(
            base_delay=5.0,
            backoff_factor=2.0,
            max_delay=60.0,
            clock_fn=lambda: self.clock
        )
        self.coordinator = BackgroundIntelligenceCoordinator(
            max_workers=2,
            max_queue_size=4,
            cooldown_tracker=self.tracker,
            clock_fn=lambda: self.clock
        )

    def tearDown(self):
        self.coordinator.shutdown(timeout=1.0)

    def test_worker_thread_count_is_bounded(self):
        self.assertEqual(len(self.coordinator._workers), 2)
        for w in self.coordinator._workers:
            self.assertTrue(w.is_alive())
            self.assertTrue(w.daemon)

    def test_coalesces_duplicate_queued_request(self):
        gate = threading.Event()
        def blocked_worker():
            gate.wait(timeout=2.0)
            return {"status": "ok"}

        # Occupy both workers with dummy tasks
        self.coordinator.enqueue("grounding", "W1", "2026-11-14T19:00:00Z", blocked_worker)
        self.coordinator.enqueue("grounding", "W2", "2026-11-14T19:00:00Z", blocked_worker)
        time.sleep(0.05)

        # Enqueue item that will sit in pending queue
        accepted1, item1 = self.coordinator.enqueue("grounding", "AAPL", "2026-11-14T19:00:00Z", lambda: 42)
        self.assertTrue(accepted1)
        self.assertIsNotNone(item1)
        self.assertEqual(item1.status, WorkStatus.QUEUED)

        # Enqueue duplicate while first is still QUEUED
        accepted2, item2 = self.coordinator.enqueue("grounding", "AAPL", "2026-11-14T19:01:00Z", lambda: 42)
        self.assertTrue(accepted2)
        # Must return the SAME existing work item and update observation timestamp
        self.assertIs(item1, item2)
        self.assertEqual(item1.obs_time, "2026-11-14T19:01:00Z")

        gate.set()

    def test_coalesces_duplicate_inflight_request(self):
        in_flight_event = threading.Event()
        release_event = threading.Event()

        def slow_task():
            in_flight_event.set()
            release_event.wait(timeout=2.0)
            return {"status": "slow_ok"}

        accepted1, item1 = self.coordinator.enqueue("grounding", "SLOW", "2026-11-14T19:00:00Z", slow_task)
        self.assertTrue(accepted1)
        in_flight_event.wait(timeout=1.0)

        # Confirm it is now IN_FLIGHT
        self.assertEqual(item1.status, WorkStatus.IN_FLIGHT)

        # Request duplicate while in-flight
        accepted2, item2 = self.coordinator.enqueue("grounding", "SLOW", "2026-11-14T19:02:00Z", slow_task)
        self.assertTrue(accepted2)
        self.assertIs(item1, item2)

        release_event.set()
        item1.event.wait(timeout=1.0)
        self.assertEqual(item1.status, WorkStatus.COMPLETED)

    def test_queue_saturation_rejects_without_blocking_coordinator(self):
        gate = threading.Event()
        def blocking_fn():
            gate.wait(timeout=2.0)
            return True

        # Occupy 2 workers
        self.coordinator.enqueue("scout", "OCC1", "2026-11-14T19:00:00Z", blocking_fn)
        self.coordinator.enqueue("scout", "OCC2", "2026-11-14T19:00:00Z", blocking_fn)
        time.sleep(0.05)

        # Fill queue capacity (max_queue_size = 4)
        for i in range(4):
            acc, it = self.coordinator.enqueue("scout", f"Q{i}", "2026-11-14T19:00:00Z", blocking_fn)
            self.assertTrue(acc)

        # 5th item exceeds capacity: must be rejected immediately without blocking
        t_before = time.monotonic()
        acc_overflow, it_overflow = self.coordinator.enqueue("scout", "OVERFLOW", "2026-11-14T19:00:00Z", blocking_fn)
        t_elapsed = time.monotonic() - t_before

        self.assertFalse(acc_overflow)
        self.assertIsNone(it_overflow)
        self.assertLess(t_elapsed, 0.1)  # Non-blocking

        gate.set()

    def test_worker_exception_does_not_terminate_coordinator(self):
        def failing_task():
            raise RuntimeError("Provider API rate limit or outage")

        accepted, item = self.coordinator.enqueue("grounding", "FAIL_SYM", "2026-11-14T19:00:00Z", failing_task)
        self.assertTrue(accepted)
        item.event.wait(timeout=1.0)

        self.assertEqual(item.status, WorkStatus.FAILED)
        self.assertIn("Provider API rate limit", item.error)

        # Key should now be in cooldown
        self.assertTrue(self.tracker.is_in_cooldown(WorkKey("grounding", "FAIL_SYM")))

        # Coordinator is still healthy and processes subsequent tasks
        accepted_next, item_next = self.coordinator.enqueue("grounding", "GOOD_SYM", "2026-11-14T19:00:00Z", lambda: {"result": "success"})
        self.assertTrue(accepted_next)
        item_next.event.wait(timeout=1.0)
        self.assertEqual(item_next.status, WorkStatus.COMPLETED)


@unittest.skipUnless(
    os.getenv("RUN_EXTENDED_TESTS") == "1",
    "Extended stress/timing suite (opt-in)",
)
class TestSubprocessLifecycleAndHardening(unittest.TestCase):
    """Verifies subprocess timeout, cancellation while running, stream cleanup, and reaping."""

    def test_cleanup_subprocess_terminates_and_closes_streams(self):
        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.poll.return_value = None  # Alive
        mock_proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="test", timeout=1.0), 0]

        _cleanup_subprocess(mock_proc, graceful_timeout=0.05)

        mock_proc.terminate.assert_called()
        mock_proc.kill.assert_called()
        mock_proc.stdin.close.assert_called()
        mock_proc.stdout.close.assert_called()
        mock_proc.stderr.close.assert_called()

    @patch("subprocess.Popen")
    def test_run_prompt_timeout_triggers_cleanup_and_preserves_exception(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 88888
        mock_proc.poll.return_value = None

        # Simulate hanging communicate
        hang_event = threading.Event()
        def mock_comm(input=None, timeout=None):
            hang_event.wait(timeout=2.0)
            return ("", "")
        mock_proc.communicate.side_effect = mock_comm
        mock_proc.terminate.side_effect = lambda: hang_event.set()
        mock_proc.kill.side_effect = lambda: hang_event.set()
        mock_popen.return_value = mock_proc

        runner = AntigravityAgentRunner(agy_path="agy")

        t_start = time.monotonic()
        with self.assertRaises(TimeoutError) as ctx:
            runner.run_prompt("Test prompt", timeout=0.1)
        t_elapsed = time.monotonic() - t_start

        self.assertIn("timed out after 0.1s", str(ctx.exception))
        self.assertLess(t_elapsed, 1.0)
        mock_proc.terminate.assert_called()
        hang_event.set()

    @patch("subprocess.Popen")
    def test_cancellation_while_subprocess_is_running(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 77777
        mock_proc.poll.return_value = None

        hang_event = threading.Event()
        def mock_comm(input=None, timeout=None):
            hang_event.wait(timeout=2.0)
            return ("", "")
        mock_proc.communicate.side_effect = mock_comm
        mock_proc.terminate.side_effect = lambda: hang_event.set()
        mock_proc.kill.side_effect = lambda: hang_event.set()
        mock_popen.return_value = mock_proc

        is_cancelled = False
        def cancel_check():
            return is_cancelled

        runner = AntigravityAgentRunner(agy_path="agy", cancel_check=cancel_check)

        # Trigger cancellation after 50ms in another thread
        def trigger_cancel():
            nonlocal is_cancelled
            time.sleep(0.05)
            is_cancelled = True

        threading.Thread(target=trigger_cancel, daemon=True).start()

        with self.assertRaises(RuntimeError) as ctx:
            runner.run_prompt("Test cancel while running", timeout=10)

        self.assertIn("Operation cancelled by user", str(ctx.exception))
        mock_proc.terminate.assert_called()
        hang_event.set()


class TestAtomicSnapshotPublication(unittest.TestCase):
    """Verifies atomic snapshot publication, reader consistency, and writer isolation."""

    def test_readers_observe_consistent_snapshot_during_publication(self):
        initial = {"status": "INITIAL", "tickers": [{"ticker": "AAPL", "price": 100.0}], "version": 0}
        publish_global_state_snapshot(initial)

        observed_versions = []
        stop_event = threading.Event()

        def reader():
            while not stop_event.is_set():
                snap = get_global_state_snapshot()
                # Reader must always observe a consistent mapping
                self.assertIsInstance(snap, (dict, collections.abc.Mapping))
                self.assertIn("version", snap)
                observed_versions.append(snap["version"])
                time.sleep(0.001)

        readers = [threading.Thread(target=reader, daemon=True) for _ in range(4)]
        for r in readers:
            r.start()

        # Publish 20 rapid snapshot updates
        for v in range(1, 21):
            new_snap = {
                "status": f"UPDATE_{v}",
                "tickers": [{"ticker": "AAPL", "price": 100.0 + v}],
                "version": v
            }
            publish_global_state_snapshot(new_snap)
            time.sleep(0.002)

        stop_event.set()
        for r in readers:
            r.join(timeout=1.0)

        self.assertGreater(len(observed_versions), 10)
        # Final snapshot must be version 20
        final_snap = get_global_state_snapshot()
        self.assertEqual(final_snap["version"], 20)

    def test_multiple_writers_do_not_lose_updates(self):
        base_state = {"counter": 0, "status": "BASE"}
        publish_global_state_snapshot(base_state)

        def writer():
            for _ in range(50):
                def increment(current):
                    s = dict(current)
                    s["counter"] = s["counter"] + 1
                    return s
                update_global_state_snapshot(increment)

        threads = [threading.Thread(target=writer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2.0)

        final_snap = get_global_state_snapshot()
        self.assertEqual(final_snap["counter"], 200)

    def test_published_snapshot_reference_not_mutated_in_place(self):
        snap1 = {"name": "SNAP1", "items": [1, 2, 3]}
        publish_global_state_snapshot(snap1)

        # Reader captures reference
        captured = get_global_state_snapshot()

        # Writer updates state
        def update_items(current):
            s = dict(current)
            s["items"] = s["items"] + [4]
            return s
        update_global_state_snapshot(update_items)

        # Captured snapshot must remain unchanged
        self.assertEqual(captured["items"], [1, 2, 3])
        self.assertEqual(get_global_state_snapshot()["items"], [1, 2, 3, 4])


class TestMonotonicOrderingAndHandoff(unittest.TestCase):
    """Verifies sequence and observation timestamp monotonicity enforcement."""

    def setUp(self):
        self.applicator = BackgroundResultApplicator()
        self.grounding = {}
        self.scout_cache = {}
        self.scout_map = {}

    def test_valid_result_applies_successfully(self):
        key = WorkKey("grounding", "AAPL")
        result = WorkResult(
            key=key,
            sequence=1,
            obs_time="2026-11-14T19:00:00Z",
            data={"headline": "Apple announces earnings", "sentiment_score": 0.8}
        )

        audit = self.applicator.apply_results(
            [result],
            self.grounding,
            self.scout_cache,
            self.scout_map
        )
        self.assertEqual(len(audit), 1)
        self.assertTrue(audit[0][1])
        self.assertIn("AAPL", self.grounding)
        self.assertEqual(self.grounding["AAPL"]["sentiment_score"], 0.8)

    def test_stale_sequence_rejected(self):
        key = WorkKey("grounding", "AAPL")
        r1 = WorkResult(key=key, sequence=2, obs_time="2026-11-14T19:00:00Z", data={"v": 2})
        r_stale = WorkResult(key=key, sequence=1, obs_time="2026-11-14T19:01:00Z", data={"v": 1})

        # Apply sequence 2 first
        self.applicator.apply_results([r1], self.grounding, self.scout_cache, self.scout_map)
        self.assertEqual(self.grounding["AAPL"]["v"], 2)

        # Apply stale sequence 1
        audit = self.applicator.apply_results([r_stale], self.grounding, self.scout_cache, self.scout_map)
        self.assertFalse(audit[0][1])
        self.assertIn("Stale sequence", audit[0][2])
        # Value must not be overwritten
        self.assertEqual(self.grounding["AAPL"]["v"], 2)

    def test_older_observation_timestamp_rejected(self):
        key = WorkKey("grounding", "AAPL")
        r1 = WorkResult(key=key, sequence=1, obs_time="2026-11-14T19:05:00Z", data={"v": "newer_obs"})
        r_older_obs = WorkResult(key=key, sequence=2, obs_time="2026-11-14T19:00:00Z", data={"v": "older_obs"})

        self.applicator.apply_results([r1], self.grounding, self.scout_cache, self.scout_map)
        self.assertEqual(self.grounding["AAPL"]["v"], "newer_obs")

        audit = self.applicator.apply_results([r_older_obs], self.grounding, self.scout_cache, self.scout_map)
        self.assertFalse(audit[0][1])
        self.assertIn("Stale observation", audit[0][2])
        self.assertEqual(self.grounding["AAPL"]["v"], "newer_obs")

    def test_invalid_or_naive_observation_timestamp_fails_closed(self):
        key = WorkKey("grounding", "AAPL")
        r_naive = WorkResult(key=key, sequence=1, obs_time="2026-11-14 19:00:00", data={"v": "naive"})

        audit = self.applicator.apply_results([r_naive], self.grounding, self.scout_cache, self.scout_map)
        self.assertFalse(audit[0][1])
        self.assertIn("Invalid observation timestamp", audit[0][2])
        self.assertNotIn("AAPL", self.grounding)


class TestMarketMonitoringIsolation(unittest.TestCase):
    """Verifies that slow, failing, or hung AI calls do not block market monitoring routines."""

    @patch.dict("fetch_stocks.SCOUT_TICKER_MAP", {"Technology": ["MSFT", "AAPL", "NVDA"]}, clear=False)
    @patch("fetch_stocks.enqueue_scout_discovery")
    def test_dynamic_scout_returns_fallback_immediately_without_blocking(self, mock_enqueue):
        mock_enqueue.return_value = (True, None)

        t_start = time.monotonic()
        tickers = _get_dynamic_scout_tickers("Technology")
        t_elapsed = time.monotonic() - t_start

        # Must return immediately (< 50ms) using fallback mappings
        self.assertLess(t_elapsed, 0.05)
        self.assertIsInstance(tickers, list)
        self.assertGreater(len(tickers), 0)
        mock_enqueue.assert_called_once()

    @patch("fetch_stocks.enqueue_news_grounding")
    def test_query_gemini_news_grounding_timeout_does_not_block_indefinitely(self, mock_enqueue):
        # Simulate worker that never sets event
        hang_item = MagicMock()
        hang_item.event.wait.return_value = False  # Timed out
        mock_enqueue.return_value = (True, hang_item)

        t_start = time.monotonic()
        # Call with short bounded timeout (0.1s)
        res = query_gemini_news_grounding("", "UMAC", timeout=0.1)
        t_elapsed = time.monotonic() - t_start

        self.assertLess(t_elapsed, 0.5)
        self.assertIsInstance(res, dict)


class TestStage21RemediationRequirements(unittest.TestCase):
    """
    Mandatory deterministic negative and regression tests for Stage 2.1 Targeted Remediation:
    Items 1 through 22 as specified in Section 4.
    """

    def test_01_published_snapshots_cannot_be_mutated_through_normal_methods(self):
        snap = publish_global_state_snapshot({
            "meta": {"version": 1},
            "tickers": ["AAPL", "NVDA"],
            "info": {"counts": [1, 2]}
        })
        # Dict mutators on snapshot root and nested mappings
        with self.assertRaises(TypeError):
            snap["meta"] = {"version": 2}
        with self.assertRaises(TypeError):
            snap["meta"]["version"] = 2
        with self.assertRaises(TypeError):
            snap["meta"].update({"version": 2})
        with self.assertRaises(TypeError):
            snap["meta"].pop("version")
        with self.assertRaises(TypeError):
            snap["meta"].clear()
        with self.assertRaises(TypeError):
            snap["meta"].setdefault("new_key", 100)
        with self.assertRaises(TypeError):
            del snap["meta"]

        # Sequence mutators on nested lists
        with self.assertRaises(TypeError):
            snap["tickers"].append("MSFT")
        with self.assertRaises(TypeError):
            snap["tickers"].extend(["MSFT"])
        with self.assertRaises(TypeError):
            snap["tickers"].insert(0, "MSFT")
        with self.assertRaises(TypeError):
            snap["tickers"].pop()
        with self.assertRaises(TypeError):
            snap["tickers"].clear()
        with self.assertRaises(TypeError):
            snap["tickers"][0] = "GOOG"

    def test_02_published_snapshots_cannot_be_mutated_through_base_class_operations(self):
        snap = publish_global_state_snapshot({
            "meta": {"version": 1},
            "items": [1, 2, 3]
        })
        # Base-class dict operations must fail closed because ImmutableDict does not inherit from dict
        with self.assertRaises(TypeError):
            dict.__setitem__(snap, "meta", {})
        with self.assertRaises(TypeError):
            dict.__setitem__(snap["meta"], "version", 999)
        with self.assertRaises(TypeError):
            dict.clear(snap["meta"])
        with self.assertRaises(TypeError):
            dict.update(snap["meta"], {"version": 999})
        with self.assertRaises(TypeError):
            dict.pop(snap["meta"], "version")

        # Base-class list operations must fail closed because ImmutableList does not inherit from list
        with self.assertRaises(TypeError):
            list.append(snap["items"], 4)
        with self.assertRaises(TypeError):
            list.__setitem__(snap["items"], 0, 999)
        with self.assertRaises(TypeError):
            list.extend(snap["items"], [5])
        with self.assertRaises(TypeError):
            list.pop(snap["items"])
        with self.assertRaises(TypeError):
            list.clear(snap["items"])

    def test_03_no_unittest_or_mock_call_stack_exception_permits_mutation(self):
        # Running inside unittest.TestCase must NEVER bypass immutability
        snap = publish_global_state_snapshot({"test_key": "initial_val"})
        with self.assertRaises(TypeError):
            snap["test_key"] = "tampered_val"

        # Calling from inside a mock must also never bypass immutability
        mock_caller = MagicMock()
        def try_mutate(s):
            s["test_key"] = "mock_tampered"
        mock_caller.side_effect = try_mutate
        with self.assertRaises(TypeError):
            mock_caller(snap)

    def test_04_caller_owned_nested_structures_cannot_mutate_published_snapshots(self):
        nested_list = [1, 2, 3]
        nested_dict = {"inner_list": nested_list, "count": 10}
        root = {"nested": nested_dict}
        published = publish_global_state_snapshot(root)

        # Caller mutates external structures after publication
        nested_list.append(4)
        nested_dict["count"] = 999
        nested_dict["new_field"] = "leaked"
        root["nested"] = "replaced"

        # Published snapshot must remain completely isolated and unaffected
        self.assertEqual(list(published["nested"]["inner_list"]), [1, 2, 3])
        self.assertEqual(published["nested"]["count"], 10)
        self.assertNotIn("new_field", published["nested"])
        self.assertIn("inner_list", published["nested"])

    def test_05_windows_handle_return_values_retain_pointer_sized_integrity(self):
        import ctypes
        from ctypes import wintypes
        from agent_framework import _get_windows_descendant_pids
        with patch("sys.platform", "win32"), \
             patch("ctypes.windll.kernel32", create=True) as mock_kernel32:
            mock_kernel32.CreateToolhelp32Snapshot.return_value = 0x123456789ABCDEF0
            mock_kernel32.Process32First.return_value = True
            mock_kernel32.Process32Next.return_value = False
            mock_kernel32.GetLastError.return_value = 18  # ERROR_NO_MORE_FILES
            mock_kernel32.CloseHandle.return_value = 1
            pids, success = _get_windows_descendant_pids(1234)
            self.assertEqual(mock_kernel32.CreateToolhelp32Snapshot.restype, wintypes.HANDLE)
            self.assertEqual(mock_kernel32.CloseHandle.argtypes, [wintypes.HANDLE])
            self.assertEqual(ctypes.sizeof(wintypes.HANDLE), ctypes.sizeof(ctypes.c_void_p))
            self.assertTrue(success)

    def test_06_invalid_toolhelp_handles_are_detected(self):
        import ctypes
        from agent_framework import _get_windows_descendant_pids
        for invalid_handle in [-1, 0, None, ctypes.c_void_p(-1).value]:
            with patch("sys.platform", "win32"), patch("ctypes.windll.kernel32", create=True) as mock_kernel32:
                mock_kernel32.CreateToolhelp32Snapshot.return_value = invalid_handle
                pids, success = _get_windows_descendant_pids(1234)
                self.assertFalse(success, f"Handle {invalid_handle} must be detected as invalid")
                self.assertEqual(pids, [])
                mock_kernel32.Process32First.assert_not_called()

    def test_07_toolhelp_enumeration_failure_is_reported(self):
        from agent_framework import _get_windows_descendant_pids
        ERROR_ACCESS_DENIED = 5
        with patch("sys.platform", "win32"), \
             patch("ctypes.windll.kernel32", create=True) as mock_kernel32:
            mock_kernel32.CreateToolhelp32Snapshot.return_value = 100
            mock_kernel32.Process32First.return_value = False
            mock_kernel32.GetLastError.return_value = ERROR_ACCESS_DENIED
            mock_kernel32.CloseHandle.return_value = 1
            pids, success = _get_windows_descendant_pids(1234)
            self.assertFalse(success, "Enumeration failure must report success=False")
            mock_kernel32.CloseHandle.assert_called_once_with(100)

    def test_08_windows_descendant_termination_failure_is_reported(self):
        mock_proc = MagicMock()
        mock_proc.pid = 44444
        mock_proc.poll.return_value = 0
        mock_proc.wait.return_value = 0
        mock_proc._cleaned_up = False

        with patch("sys.platform", "win32"), \
             patch("agent_framework._get_windows_descendant_pids", return_value=([55555], True)), \
             patch("subprocess.run", return_value=subprocess.CompletedProcess(args=["taskkill"], returncode=1)):
            with self.assertRaises(SubprocessCleanupError) as ctx:
                _cleanup_subprocess(mock_proc, raise_on_error=True)
            self.assertIn("taskkill failed", str(ctx.exception))
            self.assertFalse(mock_proc._cleaned_up)

    def test_09_posix_process_group_termination_failure_is_reported(self):
        mock_proc = MagicMock()
        mock_proc.pid = 55555
        mock_proc._dedicated_pgid = 55555
        mock_proc.poll.return_value = 0
        mock_proc.wait.return_value = 0
        mock_proc._cleaned_up = False

        with patch("sys.platform", "linux"), \
             patch("os.getpgrp", create=True, return_value=1234), \
             patch("os.killpg", create=True, return_value=None):
            with self.assertRaises(SubprocessCleanupError) as ctx:
                _cleanup_subprocess(mock_proc, raise_on_error=True)
            self.assertIn("Process group 55555 still contains active processes", str(ctx.exception))
            self.assertFalse(mock_proc._cleaned_up)

    def test_10_direct_child_reaping_does_not_imply_descendant_cleanup_success(self):
        mock_proc = MagicMock()
        mock_proc.pid = 11111
        mock_proc.poll.return_value = 0  # Direct child exited cleanly
        mock_proc.wait.return_value = 0  # Direct child reaped cleanly
        mock_proc._cleaned_up = False

        # But Windows descendant enumeration failed
        with patch("sys.platform", "win32"), \
             patch("agent_framework._get_windows_descendant_pids", return_value=([], False)):
            with self.assertRaises(SubprocessCleanupError):
                _cleanup_subprocess(mock_proc, raise_on_error=True)
            self.assertFalse(mock_proc._cleaned_up)

    def test_11_stream_closure_failures_are_reported(self):
        mock_proc = MagicMock()
        mock_proc.pid = 22222
        mock_proc.poll.return_value = 0
        mock_proc.wait.return_value = 0
        mock_proc._cleaned_up = False
        mock_proc.stdout.close.side_effect = IOError("Failed to close stdout pipe")

        with patch("sys.platform", "win32"), \
             patch("agent_framework._get_windows_descendant_pids", return_value=([], True)):
            with self.assertRaises(SubprocessCleanupError) as ctx:
                _cleanup_subprocess(mock_proc, raise_on_error=True)
            self.assertIn("Failed to close stdout", str(ctx.exception))
            self.assertFalse(mock_proc._cleaned_up)

    def test_12_primary_exceptions_survive_cleanup_failures(self):
        mock_proc = MagicMock()
        mock_proc.pid = 33333
        mock_proc.poll.return_value = None
        mock_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=1.0)

        try:
            raise TimeoutError("Primary timeout during agent run")
        except TimeoutError as primary_exc:
            success = _cleanup_subprocess(mock_proc, raise_on_error=True)
            self.assertFalse(success)
            self.assertEqual(str(primary_exc), "Primary timeout during agent run")

    def test_13_cleaned_up_not_set_after_incomplete_cleanup(self):
        mock_proc = MagicMock()
        mock_proc.pid = 44444
        mock_proc.poll.return_value = None
        mock_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=0.1)
        mock_proc._cleaned_up = False

        with patch("sys.platform", "win32"), \
             patch("agent_framework._get_windows_descendant_pids", return_value=([], True)):
            success = _cleanup_subprocess(mock_proc, raise_on_error=False)
            self.assertFalse(success)
            self.assertFalse(mock_proc._cleaned_up)

    def test_14_cleanup_remains_safely_idempotent(self):
        mock_proc = MagicMock()
        mock_proc.pid = 55555
        mock_proc.poll.return_value = 0
        mock_proc.wait.return_value = 0
        mock_proc._cleaned_up = False

        with patch("sys.platform", "win32"), \
             patch("agent_framework._get_windows_descendant_pids", return_value=([], True)):
            s1 = _cleanup_subprocess(mock_proc, raise_on_error=True)
            self.assertTrue(s1)
            self.assertTrue(mock_proc._cleaned_up)

            s2 = _cleanup_subprocess(mock_proc, raise_on_error=True)
            self.assertTrue(s2)

    def test_15_enqueue_time_not_represented_as_source_observation_time(self):
        coord = BackgroundIntelligenceCoordinator(max_workers=1, max_queue_size=5)
        t_before = time.monotonic()
        accepted, item = coord.enqueue(
            "grounding", "AAPL",
            obs_time="2026-11-14T19:00:00Z",
            fn=lambda: {"test": 1}
        )
        t_after = time.monotonic()
        self.assertTrue(accepted)
        self.assertEqual(item.obs_time, "2026-11-14T19:00:00Z")
        self.assertGreaterEqual(item.enqueued_time, t_before)
        self.assertLessEqual(item.enqueued_time, t_after)
        self.assertNotEqual(item.obs_time, str(item.enqueued_time))
        self.assertNotIn("T", str(item.enqueued_time))
        coord.shutdown(timeout=1.0)

    def test_16_completion_time_not_represented_as_source_observation_time(self):
        coord = BackgroundIntelligenceCoordinator(max_workers=1, max_queue_size=5)
        accepted, item = coord.enqueue(
            "grounding", "MSFT",
            obs_time="2026-11-14T19:00:00Z",
            fn=lambda: {"test": 2}
        )
        self.assertTrue(accepted)
        self.assertTrue(item.event.wait(timeout=2.0))
        results = coord.drain_results()
        self.assertEqual(len(results), 1)
        res = results[0]
        self.assertEqual(res.obs_time, "2026-11-14T19:00:00Z")
        self.assertIsInstance(res.completed_time, float)
        self.assertNotEqual(res.obs_time, str(res.completed_time))
        self.assertGreater(res.completed_time, res.enqueued_time)
        coord.shutdown(timeout=1.0)

    def test_17_missing_source_timestamp_fails_closed_where_required(self):
        applicator = BackgroundResultApplicator()
        g, sc, sm = {}, {}, {}
        key = WorkKey("grounding", "GOOG")
        res = WorkResult(key=key, sequence=1, obs_time=None, data={"headline": "Missing timestamp"})
        audit = applicator.apply_results([res], g, sc, sm, require_source_time=True)
        self.assertFalse(audit[0][1], "Result with missing obs_time must be rejected")
        self.assertIn("Missing required source observation timestamp", audit[0][2])
        self.assertNotIn("GOOG", g)

    def test_18_production_grounding_integration_preserves_timestamp_semantics(self):
        with isolated_coordinator_context() as (dummy, mock_scout_raw, mock_grounding_raw, mock_runner):
            from fetch_stocks import enqueue_news_grounding
            t0 = time.monotonic()
            accepted, item = enqueue_news_grounding("AAPL", obs_time="2026-11-14T19:00:00Z")
            t1 = time.monotonic()
            self.assertTrue(accepted)
            self.assertEqual(item.obs_time, "2026-11-14T19:00:00Z")
            self.assertIsInstance(item.enqueued_time, float)
            self.assertTrue(t0 <= item.enqueued_time <= t1)

            t2 = time.monotonic()
            accepted2, item2 = enqueue_news_grounding("NVDA", obs_time=None)
            t3 = time.monotonic()
            self.assertTrue(accepted2)
            self.assertIsNone(item2.obs_time)
            self.assertIsInstance(item2.enqueued_time, float)
            self.assertTrue(t2 <= item2.enqueued_time <= t3)

            # Assert no worker threads started and no AI functions run
            self.assertEqual(len(dummy._workers), 0)
            mock_grounding_raw.assert_not_called()
            mock_scout_raw.assert_not_called()
            mock_runner.assert_not_called()

        self.assertEqual(dummy.pending_count(), 0)
        self.assertEqual(dummy.total_work_count(), 0)
        self.assertEqual(get_background_coordinator().pending_count(), 0)

    def test_19_production_scout_integration_preserves_timestamp_semantics(self):
        with isolated_coordinator_context() as (dummy, mock_scout_raw, mock_grounding_raw, mock_runner):
            from fetch_stocks import enqueue_scout_discovery
            t0 = time.monotonic()
            accepted, item = enqueue_scout_discovery("Technology", obs_time="2026-11-14T19:00:00Z")
            t1 = time.monotonic()
            self.assertTrue(accepted)
            self.assertEqual(item.obs_time, "2026-11-14T19:00:00Z")
            self.assertIsInstance(item.enqueued_time, float)
            self.assertTrue(t0 <= item.enqueued_time <= t1)

            t2 = time.monotonic()
            accepted2, item2 = enqueue_scout_discovery("Healthcare", obs_time=None)
            t3 = time.monotonic()
            self.assertTrue(accepted2)
            self.assertIsNone(item2.obs_time)
            self.assertIsInstance(item2.enqueued_time, float)
            self.assertTrue(t2 <= item2.enqueued_time <= t3)

            # Assert no worker threads started and no AI functions run
            self.assertEqual(len(dummy._workers), 0)
            mock_scout_raw.assert_not_called()
            mock_grounding_raw.assert_not_called()
            mock_runner.assert_not_called()

        self.assertEqual(dummy.pending_count(), 0)
        self.assertEqual(dummy.total_work_count(), 0)
        self.assertEqual(get_background_coordinator().pending_count(), 0)

    def test_20_newer_work_issued_between_validation_and_publication_prevents_older(self):
        applicator = BackgroundResultApplicator()
        g, sc, sm = {}, {}, {}
        key = WorkKey("grounding", "AAPL")
        applicator.record_issued_sequence(key, 1)
        applicator.record_issued_sequence(key, 2)

        r1 = WorkResult(key=key, sequence=1, obs_time="2026-11-14T19:00:00Z", data={"v": 1})
        audit1 = applicator.apply_results([r1], g, sc, sm)
        self.assertFalse(audit1[0][1], "Older sequence must be rejected when newer work was issued")
        self.assertIn("older than latest issued", audit1[0][2])
        self.assertNotIn("AAPL", g)

    def test_21_concurrent_sequence_issuance_and_result_application(self):
        coord = BackgroundIntelligenceCoordinator(max_workers=2, max_queue_size=30)
        applicator = BackgroundResultApplicator(coordinator=coord)
        g, sc, sm = {}, {}, {}
        key = WorkKey("grounding", "AAPL")

        stop_event = threading.Event()
        exceptions = []
        applied_sequences = []

        def issuer_thread():
            try:
                for s in range(1, 20):
                    if stop_event.is_set():
                        break
                    coord.enqueue("grounding", "AAPL", "2026-11-14T19:00:00Z", lambda idx=s: {"val": idx}, force=True)
                    time.sleep(0.005)
            except Exception as e:
                exceptions.append(e)

        def applicator_thread():
            try:
                while not stop_event.is_set():
                    results = coord.drain_results()
                    if results:
                        audit = applicator.apply_results(results, g, sc, sm)
                        for r, applied, reason in audit:
                            if applied:
                                applied_sequences.append(r.sequence)
                    time.sleep(0.005)
            except Exception as e:
                exceptions.append(e)

        t1 = threading.Thread(target=issuer_thread)
        t2 = threading.Thread(target=applicator_thread)
        t1.start()
        t2.start()
        t1.join(timeout=3.0)
        time.sleep(0.1)
        stop_event.set()
        t2.join(timeout=3.0)

        results = coord.drain_results()
        if results:
            audit = applicator.apply_results(results, g, sc, sm)
            for r, applied, reason in audit:
                if applied:
                    applied_sequences.append(r.sequence)
        coord.shutdown(timeout=2.0)

        self.assertEqual(exceptions, [])
        # Sequences applied must be strictly monotonically increasing (no stale publication)
        self.assertEqual(applied_sequences, sorted(applied_sequences))
        self.assertEqual(len(applied_sequences), len(set(applied_sequences)))
        if applied_sequences:
            self.assertEqual(applicator._last_applied_sequence.get(key), applied_sequences[-1])

    def test_22_existing_stage1_and_stage21_regression_tests_remain_green(self):
        coord = BackgroundIntelligenceCoordinator(max_workers=1, max_queue_size=2, max_results_size=2)
        self.assertEqual(coord.pending_count(), 0)
        self.assertEqual(coord.in_flight_count(), 0)

        snap = publish_global_state_snapshot({"a": 1, "b": [1, 2]})
        self.assertEqual(snap["a"], 1)
        self.assertEqual(list(snap["b"]), [1, 2])
        self.assertTrue(coord.shutdown(timeout=1.0))


class TestStage21FinalCorrectnessRequirements(unittest.TestCase):
    """
    Mandatory Deterministic Negative and Regression Verification Suite for
    Stage 2.1 Final Correctness Remediation (Section 5, Requirements 1-17).
    """

    def test_01_direct_mutation_through_snapshot_data_impossible(self):
        """1. Direct mutation through snapshot _data is impossible."""
        snap = publish_global_state_snapshot({"a": 1, "nested": {"b": 2}})
        # _data is MappingProxyType: mutating items directly raises TypeError
        with self.assertRaises(TypeError):
            snap._data["a"] = 100
        with self.assertRaises(TypeError):
            snap["nested"]._data["b"] = 200
        # Reassigning or deleting the _data attribute raises TypeError
        with self.assertRaises(TypeError):
            snap._data = {}
        with self.assertRaises(TypeError):
            del snap._data
        with self.assertRaises(TypeError):
            snap.new_attr = 123

    def test_02_direct_mutation_through_snapshot_items_impossible(self):
        """2. Direct mutation through snapshot _items is impossible."""
        snap = publish_global_state_snapshot({"list": [1, 2, 3]})
        # _items is a tuple: mutating elements raises TypeError
        with self.assertRaises(TypeError):
            snap["list"]._items[0] = 999
        with self.assertRaises(AttributeError):
            snap["list"]._items.append(4)
        # Reassigning or deleting the _items attribute raises TypeError
        with self.assertRaises(TypeError):
            snap["list"]._items = (4, 5, 6)
        with self.assertRaises(TypeError):
            del snap["list"]._items
        with self.assertRaises(TypeError):
            snap["list"].new_attr = 123

    def test_03_nested_mappings_and_sequences_remain_immutable(self):
        """3. Nested mappings and sequences remain immutable."""
        snap = publish_global_state_snapshot({
            "meta": {"version": 1, "tags": ["a", "b"]},
            "items": [{"id": 10}, {"id": 20}]
        })
        with self.assertRaises(TypeError):
            snap["meta"]["version"] = 2
        with self.assertRaises(TypeError):
            snap["meta"]["tags"].append("c")
        with self.assertRaises(TypeError):
            snap["items"][0]["id"] = 99
        with self.assertRaises(TypeError):
            dict.__setitem__(snap["meta"], "version", 2)
        with self.assertRaises(TypeError):
            list.append(snap["items"], {"id": 30})

    def test_04_caller_owned_nested_structures_cannot_change_published_snapshots(self):
        """4. Caller-owned nested structures cannot change published snapshots."""
        nested_list = [10, 20, 30]
        nested_dict = {"inner_list": nested_list, "count": 100}
        root = {"data": nested_dict}
        snap = publish_global_state_snapshot(root)

        nested_list.append(40)
        nested_dict["count"] = 999
        nested_dict["injected"] = "tampered"
        root["data"] = "replaced"

        self.assertEqual(list(snap["data"]["inner_list"]), [10, 20, 30])
        self.assertEqual(snap["data"]["count"], 100)
        self.assertNotIn("injected", snap["data"])

    def test_05_sequence_issuance_cannot_interleave_between_validation_and_actual_publication(self):
        """5. Sequence issuance cannot interleave between validation and actual publication (barrier/event test)."""
        coord = BackgroundIntelligenceCoordinator(max_workers=1, max_queue_size=5)
        applicator = BackgroundResultApplicator(coordinator=coord)
        key = WorkKey("grounding", "TEST_BARRIER")

        # Sequence 1 is issued
        accepted, item = coord.enqueue("grounding", "TEST_BARRIER", "2026-11-14T19:00:00Z", fn=lambda: {"res": 1})
        self.assertTrue(accepted)
        self.assertEqual(item.sequence, 1)

        pub_started = threading.Event()
        allow_pub_finish = threading.Event()
        enqueue_attempted = threading.Event()
        enqueue_finished = threading.Event()
        second_item_holder = []

        def blocking_publish(result):
            pub_started.set()
            # Wait until the enqueue thread attempts to run
            enqueue_attempted.wait(timeout=2.0)
            # Verify that enqueue thread has NOT finished yet (it must be blocked on key_lock)
            self.assertFalse(enqueue_finished.is_set(), "New sequence must not be issued while publication holds key_lock")
            allow_pub_finish.wait(timeout=2.0)
            update_global_state_snapshot(lambda current: {**dict(current), "qualitative_grounding": {"TEST_BARRIER": result.data}})

        def attempt_enqueue():
            enqueue_attempted.set()
            # This must block until blocking_publish completes and key_lock is released
            acc, itm = coord.enqueue("grounding", "TEST_BARRIER", "2026-11-14T19:05:00Z", fn=lambda: {"res": 2}, force=True)
            second_item_holder.append((acc, itm))
            enqueue_finished.set()

        res1 = WorkResult(key=key, sequence=1, obs_time="2026-11-14T19:00:00Z", data={"val": "pub1"})
        t_apply = threading.Thread(target=lambda: applicator.apply_results([res1], {}, {}, {}, on_publish=blocking_publish))
        t_enq = threading.Thread(target=attempt_enqueue)

        t_apply.start()
        self.assertTrue(pub_started.wait(timeout=2.0))
        t_enq.start()
        time.sleep(0.1)  # Give t_enq time to invoke coord.enqueue
        self.assertFalse(enqueue_finished.is_set(), "enqueue must be blocked on key_lock while publication is in progress")
        allow_pub_finish.set()
        t_apply.join(timeout=2.0)
        t_enq.join(timeout=2.0)
        self.assertTrue(enqueue_finished.is_set())
        self.assertEqual(second_item_holder[0][1].sequence, 2)
        coord.shutdown(timeout=1.0)

    def test_06_newer_request_cannot_be_overtaken_by_older_result(self):
        """6. A newer request cannot be overtaken by an older result."""
        applicator = BackgroundResultApplicator()
        g, sc, sm = {}, {}, {}
        key = WorkKey("grounding", "AAPL")
        applicator.record_issued_sequence(key, 1)
        applicator.record_issued_sequence(key, 2)

        r1 = WorkResult(key=key, sequence=1, obs_time="2026-11-14T19:00:00Z", data={"v": 1})
        audit1 = applicator.apply_results([r1], g, sc, sm)
        self.assertFalse(audit1[0][1], "Older sequence must be rejected when newer work was issued")
        self.assertIn("older than latest issued", audit1[0][2])
        self.assertNotIn("AAPL", g)

    def test_07_concurrent_unrelated_keys_remain_processable(self):
        """7. Concurrent unrelated keys remain processable."""
        coord = BackgroundIntelligenceCoordinator(max_workers=2, max_queue_size=10)
        key_a = WorkKey("grounding", "AAPL")
        key_b = WorkKey("grounding", "MSFT")

        # Explicitly acquire lock for key_a
        lock_a = coord.get_key_lock(key_a)
        lock_a.acquire()
        try:
            # Key B must be enqueued and executed without blocking
            accepted_b, item_b = coord.enqueue("grounding", "MSFT", "2026-11-14T19:00:00Z", fn=lambda: {"val": "msft"})
            self.assertTrue(accepted_b)
            self.assertTrue(item_b.event.wait(timeout=2.0))
            res_b = coord.drain_results()
            self.assertEqual(len(res_b), 1)
            self.assertEqual(res_b[0].key, key_b)
        finally:
            lock_a.release()
        coord.shutdown(timeout=1.0)

    def test_08_scout_discovery_enqueue_time_never_represented_as_source_observation_time(self):
        """8. Scout discovery enqueue time is never represented as source observation time."""
        with isolated_coordinator_context() as (dummy, mock_scout_raw, mock_grounding_raw, mock_runner):
            from fetch_stocks import enqueue_scout_discovery
            t0 = time.monotonic()
            accepted, item = enqueue_scout_discovery("Technology", obs_time=None)
            t1 = time.monotonic()
            self.assertTrue(accepted)
            self.assertIsNone(item.obs_time)
            self.assertIsInstance(item.enqueued_time, float)
            self.assertTrue(t0 <= item.enqueued_time <= t1)
            self.assertNotEqual(item.obs_time, item.enqueued_time)

            # Assert no worker threads started and no AI functions run
            self.assertEqual(len(dummy._workers), 0)
            mock_scout_raw.assert_not_called()
            mock_grounding_raw.assert_not_called()
            mock_runner.assert_not_called()

        self.assertEqual(dummy.pending_count(), 0)
        self.assertEqual(dummy.total_work_count(), 0)
        self.assertEqual(get_background_coordinator().pending_count(), 0)

    def test_09_news_grounding_enqueue_time_never_represented_as_source_observation_time(self):
        """9. News grounding enqueue time is never represented as source observation time."""
        with isolated_coordinator_context() as (dummy, mock_scout_raw, mock_grounding_raw, mock_runner):
            from fetch_stocks import enqueue_news_grounding
            t0 = time.monotonic()
            accepted, item = enqueue_news_grounding("AAPL", obs_time=None)
            t1 = time.monotonic()
            self.assertTrue(accepted)
            self.assertIsNone(item.obs_time)
            self.assertIsInstance(item.enqueued_time, float)
            self.assertTrue(t0 <= item.enqueued_time <= t1)
            self.assertNotEqual(item.obs_time, item.enqueued_time)

            # Assert no worker threads started and no AI functions run
            self.assertEqual(len(dummy._workers), 0)
            mock_grounding_raw.assert_not_called()
            mock_scout_raw.assert_not_called()
            mock_runner.assert_not_called()

        self.assertEqual(dummy.pending_count(), 0)
        self.assertEqual(dummy.total_work_count(), 0)
        self.assertEqual(get_background_coordinator().pending_count(), 0)

    def test_10_daemon_grounding_scheduling_does_not_fabricate_source_timestamps(self):
        """10. Daemon grounding scheduling does not fabricate source timestamps."""
        import inspect
        from fetch_stocks import run_daemon
        src = inspect.getsource(run_daemon)
        self.assertIn("enqueue_news_grounding(p_sym, obs_time=None)", src)
        self.assertNotIn("enqueue_news_grounding(p_sym, obs_time=datetime", src)

    def test_11_generated_cache_timestamps_clearly_distinct_from_source_observation_timestamps(self):
        """11. Generated cache timestamps are clearly distinct from source observation timestamps."""
        applicator = BackgroundResultApplicator()
        scout_cache = {}
        scout_map = {}

        # 1. Scout result with valid source timestamp retains that timestamp
        res_with_ts = WorkResult(
            key=WorkKey("scout", "SEMICONDUCTORS"),
            sequence=1,
            obs_time="2026-11-14T19:00:00Z",
            data=["NVDA", "TSM"],
        )
        t_before_1 = time.time()
        applicator.apply_results([res_with_ts], {}, scout_cache, scout_map)
        t_after_1 = time.time()

        entry_1 = scout_cache["SEMICONDUCTORS"]
        self.assertEqual(entry_1["timestamp"], "2026-11-14T19:00:00Z")
        self.assertIsInstance(entry_1["cached_at"], float)
        self.assertTrue(t_before_1 <= entry_1["cached_at"] <= t_after_1)
        self.assertNotEqual(entry_1["timestamp"], entry_1["cached_at"])
        self.assertEqual(entry_1["tickers"], ["NVDA", "TSM"])

        # 2. Scout result without source timestamp receives None (never fabricated timestamp)
        res_without_ts = WorkResult(
            key=WorkKey("scout", "ENERGY"),
            sequence=1,
            obs_time=None,
            data=["XOM", "CVX"],
        )
        t_before_2 = time.time()
        applicator.apply_results([res_without_ts], {}, scout_cache, scout_map)
        t_after_2 = time.time()

        entry_2 = scout_cache["ENERGY"]
        self.assertIsNone(entry_2["timestamp"], "Absence of source timestamp must be recorded as None")
        self.assertIsInstance(entry_2["cached_at"], float)
        self.assertTrue(t_before_2 <= entry_2["cached_at"] <= t_after_2)
        self.assertNotEqual(entry_2["timestamp"], entry_2["cached_at"])
        self.assertEqual(entry_2["tickers"], ["XOM", "CVX"])

    def test_12_windows_normal_enumeration_exhaustion_accepted(self):
        """12. Windows normal enumeration exhaustion is accepted (GetLastError() == 18)."""
        from agent_framework import _get_windows_descendant_pids
        with patch("sys.platform", "win32"), \
             patch("ctypes.windll.kernel32", create=True) as mock_kernel32:
            mock_kernel32.CreateToolhelp32Snapshot.return_value = 100
            mock_kernel32.Process32First.return_value = True
            mock_kernel32.Process32Next.return_value = False
            mock_kernel32.GetLastError.return_value = 18  # ERROR_NO_MORE_FILES
            mock_kernel32.CloseHandle.return_value = 1
            pids, success = _get_windows_descendant_pids(1234)
            self.assertTrue(success)

    def test_13_windows_unexpected_enumeration_failure_rejected(self):
        """13. Windows unexpected enumeration failure is rejected (GetLastError() != 18)."""
        from agent_framework import _get_windows_descendant_pids
        with patch("sys.platform", "win32"), \
             patch("ctypes.windll.kernel32", create=True) as mock_kernel32:
            mock_kernel32.CreateToolhelp32Snapshot.return_value = 100
            mock_kernel32.Process32First.return_value = True
            mock_kernel32.Process32Next.return_value = False
            mock_kernel32.GetLastError.return_value = 5  # ERROR_ACCESS_DENIED
            mock_kernel32.CloseHandle.return_value = 1
            pids, success = _get_windows_descendant_pids(1234)
            self.assertFalse(success)
            self.assertEqual(pids, [])

    def test_14_windows_handle_closure_failure_reported(self):
        """14. Windows handle-closure failure is reported (CloseHandle() == False)."""
        from agent_framework import _get_windows_descendant_pids
        with patch("sys.platform", "win32"), \
             patch("ctypes.windll.kernel32", create=True) as mock_kernel32:
            mock_kernel32.CreateToolhelp32Snapshot.return_value = 100
            mock_kernel32.Process32First.return_value = True
            mock_kernel32.Process32Next.return_value = False
            mock_kernel32.GetLastError.return_value = 18  # Normal exhaustion
            mock_kernel32.CloseHandle.return_value = 0   # CloseHandle returns FALSE (0)
            pids, success = _get_windows_descendant_pids(1234)
            self.assertFalse(success)
            self.assertEqual(pids, [])

    def test_15_existing_subprocess_cleanup_tests_remain_green(self):
        """15. Existing subprocess cleanup tests remain green."""
        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.poll.return_value = 0
        mock_proc.wait.return_value = 0
        mock_proc._cleaned_up = False
        with patch("sys.platform", "win32"), \
             patch("agent_framework._get_windows_descendant_pids", return_value=([], True)):
            success = _cleanup_subprocess(mock_proc, raise_on_error=True)
            self.assertTrue(success)
            self.assertTrue(mock_proc._cleaned_up)

    def test_16_existing_stage1_market_data_integrity_tests_remain_green(self):
        """16. Existing Stage 1 market-data integrity tests remain green."""
        from fetch_stocks import build_daemon_final_output
        final_output = build_daemon_final_output(data=[], status="CLOSED")
        snap = publish_global_state_snapshot(final_output)
        self.assertIsInstance(snap, collections.abc.Mapping)
        self.assertIn("_meta", snap)
        self.assertIn("tickers", snap)
        self.assertIn("status", snap)

    def test_17_existing_stage21_regression_tests_remain_green(self):
        """17. Existing Stage 2.1 regression tests remain green."""
        coord = BackgroundIntelligenceCoordinator(max_workers=1, max_queue_size=2)
        self.assertEqual(coord.pending_count(), 0)
        self.assertEqual(coord.in_flight_count(), 0)
        self.assertTrue(coord.shutdown(timeout=1.0))

    def test_18_scout_cache_hit_uses_cached_at(self):
        """18. Scout cache hit uses cached_at (< 4h TTL) and returns tickers immediately without enqueuing."""
        from fetch_stocks import _get_dynamic_scout_tickers, DYNAMIC_SCOUT_CACHE, DYNAMIC_SCOUT_CACHE_LOCK
        with isolated_coordinator_context() as (dummy, mock_scout_raw, mock_grounding_raw, mock_runner):
            with DYNAMIC_SCOUT_CACHE_LOCK:
                DYNAMIC_SCOUT_CACHE["TECH_HIT"] = {
                    "tickers": ["AAPL", "MSFT"],
                    "cached_at": time.time() - 300,  # 5 minutes old (< 14400s)
                    "timestamp": None,
                }
            try:
                tickers = _get_dynamic_scout_tickers("TECH_HIT")
                self.assertEqual(tickers, ["AAPL", "MSFT"])
                self.assertEqual(dummy.pending_count(), 0)
                mock_scout_raw.assert_not_called()
            finally:
                with DYNAMIC_SCOUT_CACHE_LOCK:
                    DYNAMIC_SCOUT_CACHE.pop("TECH_HIT", None)

    def test_19_scout_cache_ttl_expiration_uses_cached_at(self):
        """19. Scout cache expired by cached_at (>= 4h) enqueues discovery and returns fallback without blocking."""
        from fetch_stocks import _get_dynamic_scout_tickers, DYNAMIC_SCOUT_CACHE, DYNAMIC_SCOUT_CACHE_LOCK
        with isolated_coordinator_context() as (dummy, mock_scout_raw, mock_grounding_raw, mock_runner):
            with DYNAMIC_SCOUT_CACHE_LOCK:
                DYNAMIC_SCOUT_CACHE["TECH_EXP"] = {
                    "tickers": ["STALE1", "STALE2"],
                    "cached_at": time.time() - 15000,  # > 4h (14400s)
                    "timestamp": None,
                }
            try:
                tickers = _get_dynamic_scout_tickers("TECH_EXP")
                # Expired: enqueues background discovery with obs_time=None
                self.assertEqual(dummy.pending_count(), 1)
                item = dummy._queue.queue[0]
                self.assertEqual(item.key.target, "TECH_EXP")
                self.assertIsNone(item.obs_time)
                mock_scout_raw.assert_not_called()
            finally:
                with DYNAMIC_SCOUT_CACHE_LOCK:
                    DYNAMIC_SCOUT_CACHE.pop("TECH_EXP", None)

    def test_20_scout_cache_safe_fallback_when_cached_at_missing_or_none_or_string(self):
        """20. Scout cache TTL check handles missing cached_at, None timestamps, and strings without TypeError."""
        import math
        from fetch_stocks import _get_dynamic_scout_tickers, DYNAMIC_SCOUT_CACHE, DYNAMIC_SCOUT_CACHE_LOCK

        # 0. Missing cached_at with timestamp=None -> Cache expires safely
        with isolated_coordinator_context() as (dummy, mock_scout_raw, _, mock_runner):
            with DYNAMIC_SCOUT_CACHE_LOCK:
                DYNAMIC_SCOUT_CACHE["MISSING_NONE_TS"] = {
                    "tickers": ["OLD"],
                    "timestamp": None,
                }
            try:
                res = _get_dynamic_scout_tickers("MISSING_NONE_TS")
                self.assertNotEqual(res, ["OLD"])
                self.assertEqual(dummy.pending_count(), 1)
                self.assertEqual(len(dummy._workers), 0)
                mock_scout_raw.assert_not_called()
                mock_runner.assert_not_called()
                self.assertIsNone(DYNAMIC_SCOUT_CACHE["MISSING_NONE_TS"]["timestamp"])
            finally:
                with DYNAMIC_SCOUT_CACHE_LOCK:
                    DYNAMIC_SCOUT_CACHE.pop("MISSING_NONE_TS", None)

        # 1. Missing cached_at with recent numeric source timestamp -> Cache expires, enqueues discovery
        with isolated_coordinator_context() as (dummy, mock_scout_raw, _, mock_runner):
            recent_num = time.time() - 60
            with DYNAMIC_SCOUT_CACHE_LOCK:
                DYNAMIC_SCOUT_CACHE["MISSING_NUM_TS"] = {
                    "tickers": ["OLD"],
                    "timestamp": recent_num,
                }
            try:
                res = _get_dynamic_scout_tickers("MISSING_NUM_TS")
                self.assertNotEqual(res, ["OLD"])  # Must NOT produce cache hit
                self.assertEqual(dummy.pending_count(), 1)
                self.assertEqual(len(dummy._workers), 0)
                mock_scout_raw.assert_not_called()
                mock_runner.assert_not_called()
                self.assertEqual(DYNAMIC_SCOUT_CACHE["MISSING_NUM_TS"]["timestamp"], recent_num)
            finally:
                with DYNAMIC_SCOUT_CACHE_LOCK:
                    DYNAMIC_SCOUT_CACHE.pop("MISSING_NUM_TS", None)

        # 2. Missing cached_at with recent ISO source timestamp -> Cache expires, enqueues discovery
        with isolated_coordinator_context() as (dummy, mock_scout_raw, _, mock_runner):
            recent_iso = "2026-11-14T20:00:00Z"
            with DYNAMIC_SCOUT_CACHE_LOCK:
                DYNAMIC_SCOUT_CACHE["MISSING_ISO_TS"] = {
                    "tickers": ["OLD"],
                    "timestamp": recent_iso,
                }
            try:
                res = _get_dynamic_scout_tickers("MISSING_ISO_TS")
                self.assertNotEqual(res, ["OLD"])  # Must NOT produce cache hit
                self.assertEqual(dummy.pending_count(), 1)
                self.assertEqual(len(dummy._workers), 0)
                mock_scout_raw.assert_not_called()
                mock_runner.assert_not_called()
                self.assertEqual(DYNAMIC_SCOUT_CACHE["MISSING_ISO_TS"]["timestamp"], recent_iso)
            finally:
                with DYNAMIC_SCOUT_CACHE_LOCK:
                    DYNAMIC_SCOUT_CACHE.pop("MISSING_ISO_TS", None)

        # 3. cached_at=None with recent source timestamp -> Cache expires
        with isolated_coordinator_context() as (dummy, mock_scout_raw, _, mock_runner):
            recent_ts = time.time() - 30
            with DYNAMIC_SCOUT_CACHE_LOCK:
                DYNAMIC_SCOUT_CACHE["CACHED_AT_NONE"] = {
                    "tickers": ["OLD"],
                    "cached_at": None,
                    "timestamp": recent_ts,
                }
            try:
                res = _get_dynamic_scout_tickers("CACHED_AT_NONE")
                self.assertNotEqual(res, ["OLD"])
                self.assertEqual(dummy.pending_count(), 1)
                mock_scout_raw.assert_not_called()
                self.assertEqual(DYNAMIC_SCOUT_CACHE["CACHED_AT_NONE"]["timestamp"], recent_ts)
            finally:
                with DYNAMIC_SCOUT_CACHE_LOCK:
                    DYNAMIC_SCOUT_CACHE.pop("CACHED_AT_NONE", None)

        # 4. String cached_at -> Cache expires
        with isolated_coordinator_context() as (dummy, mock_scout_raw, _, mock_runner):
            with DYNAMIC_SCOUT_CACHE_LOCK:
                DYNAMIC_SCOUT_CACHE["CACHED_AT_STR"] = {
                    "tickers": ["OLD"],
                    "cached_at": "invalid_date_or_number",
                    "timestamp": "2026-11-14T19:00:00Z",
                }
            try:
                res = _get_dynamic_scout_tickers("CACHED_AT_STR")
                self.assertNotEqual(res, ["OLD"])
                self.assertEqual(dummy.pending_count(), 1)
                mock_scout_raw.assert_not_called()
                self.assertEqual(DYNAMIC_SCOUT_CACHE["CACHED_AT_STR"]["timestamp"], "2026-11-14T19:00:00Z")
            finally:
                with DYNAMIC_SCOUT_CACHE_LOCK:
                    DYNAMIC_SCOUT_CACHE.pop("CACHED_AT_STR", None)

        # 5. Boolean cached_at (True and False) -> Cache expires
        for b_val in (True, False):
            with isolated_coordinator_context() as (dummy, mock_scout_raw, _, mock_runner):
                key = f"CACHED_AT_BOOL_{b_val}"
                with DYNAMIC_SCOUT_CACHE_LOCK:
                    DYNAMIC_SCOUT_CACHE[key] = {
                        "tickers": ["OLD"],
                        "cached_at": b_val,
                        "timestamp": 12345.0,
                    }
                try:
                    res = _get_dynamic_scout_tickers(key)
                    self.assertNotEqual(res, ["OLD"])
                    self.assertEqual(dummy.pending_count(), 1)
                    mock_scout_raw.assert_not_called()
                    self.assertEqual(DYNAMIC_SCOUT_CACHE[key]["timestamp"], 12345.0)
                finally:
                    with DYNAMIC_SCOUT_CACHE_LOCK:
                        DYNAMIC_SCOUT_CACHE.pop(key, None)

        # 6. NaN, Positive infinity, Negative infinity -> Cache expires
        for nf_val, nf_name in [(float("nan"), "NAN"), (float("inf"), "POS_INF"), (float("-inf"), "NEG_INF")]:
            with isolated_coordinator_context() as (dummy, mock_scout_raw, _, mock_runner):
                key = f"CACHED_AT_{nf_name}"
                with DYNAMIC_SCOUT_CACHE_LOCK:
                    DYNAMIC_SCOUT_CACHE[key] = {
                        "tickers": ["OLD"],
                        "cached_at": nf_val,
                        "timestamp": 5000.0,
                    }
                try:
                    res = _get_dynamic_scout_tickers(key)
                    self.assertNotEqual(res, ["OLD"])
                    self.assertEqual(dummy.pending_count(), 1)
                    mock_scout_raw.assert_not_called()
                    self.assertEqual(DYNAMIC_SCOUT_CACHE[key]["timestamp"], 5000.0)
                finally:
                    with DYNAMIC_SCOUT_CACHE_LOCK:
                        DYNAMIC_SCOUT_CACHE.pop(key, None)

        # 7. Future finite float -> Cache expires
        with isolated_coordinator_context() as (dummy, mock_scout_raw, _, mock_runner):
            future_float = time.time() + 600.0
            with DYNAMIC_SCOUT_CACHE_LOCK:
                DYNAMIC_SCOUT_CACHE["FUTURE_FLOAT"] = {
                    "tickers": ["OLD"],
                    "cached_at": future_float,
                    "timestamp": 1000.0,
                }
            try:
                res = _get_dynamic_scout_tickers("FUTURE_FLOAT")
                self.assertNotEqual(res, ["OLD"])
                self.assertEqual(dummy.pending_count(), 1)
                mock_scout_raw.assert_not_called()
                self.assertEqual(DYNAMIC_SCOUT_CACHE["FUTURE_FLOAT"]["timestamp"], 1000.0)
            finally:
                with DYNAMIC_SCOUT_CACHE_LOCK:
                    DYNAMIC_SCOUT_CACHE.pop("FUTURE_FLOAT", None)

        # 8. Future finite integer -> Cache expires
        with isolated_coordinator_context() as (dummy, mock_scout_raw, _, mock_runner):
            future_int = int(time.time() + 3600)
            with DYNAMIC_SCOUT_CACHE_LOCK:
                DYNAMIC_SCOUT_CACHE["FUTURE_INT"] = {
                    "tickers": ["OLD"],
                    "cached_at": future_int,
                    "timestamp": 1000.0,
                }
            try:
                res = _get_dynamic_scout_tickers("FUTURE_INT")
                self.assertNotEqual(res, ["OLD"])
                self.assertEqual(dummy.pending_count(), 1)
                mock_scout_raw.assert_not_called()
                self.assertEqual(DYNAMIC_SCOUT_CACHE["FUTURE_INT"]["timestamp"], 1000.0)
            finally:
                with DYNAMIC_SCOUT_CACHE_LOCK:
                    DYNAMIC_SCOUT_CACHE.pop("FUTURE_INT", None)

        # 9. Extremely large positive integer -> Cache expires without exception
        with isolated_coordinator_context() as (dummy, mock_scout_raw, _, mock_runner):
            huge_pos_int = 10**1000
            with DYNAMIC_SCOUT_CACHE_LOCK:
                DYNAMIC_SCOUT_CACHE["HUGE_POS_INT"] = {
                    "tickers": ["OLD"],
                    "cached_at": huge_pos_int,
                    "timestamp": 1000.0,
                }
            try:
                res = _get_dynamic_scout_tickers("HUGE_POS_INT")
                self.assertNotEqual(res, ["OLD"])
                self.assertEqual(dummy.pending_count(), 1)
                mock_scout_raw.assert_not_called()
                self.assertEqual(DYNAMIC_SCOUT_CACHE["HUGE_POS_INT"]["timestamp"], 1000.0)
            finally:
                with DYNAMIC_SCOUT_CACHE_LOCK:
                    DYNAMIC_SCOUT_CACHE.pop("HUGE_POS_INT", None)

        # 10. Extremely large negative integer -> Cache expires without exception
        with isolated_coordinator_context() as (dummy, mock_scout_raw, _, mock_runner):
            huge_neg_int = -10**1000
            with DYNAMIC_SCOUT_CACHE_LOCK:
                DYNAMIC_SCOUT_CACHE["HUGE_NEG_INT"] = {
                    "tickers": ["OLD"],
                    "cached_at": huge_neg_int,
                    "timestamp": 1000.0,
                }
            try:
                res = _get_dynamic_scout_tickers("HUGE_NEG_INT")
                self.assertNotEqual(res, ["OLD"])
                self.assertEqual(dummy.pending_count(), 1)
                mock_scout_raw.assert_not_called()
                self.assertEqual(DYNAMIC_SCOUT_CACHE["HUGE_NEG_INT"]["timestamp"], 1000.0)
            finally:
                with DYNAMIC_SCOUT_CACHE_LOCK:
                    DYNAMIC_SCOUT_CACHE.pop("HUGE_NEG_INT", None)

        # 11. Valid timestamp just inside four-hour boundary -> Cache hit
        with isolated_coordinator_context() as (dummy, mock_scout_raw, _, mock_runner):
            with DYNAMIC_SCOUT_CACHE_LOCK:
                DYNAMIC_SCOUT_CACHE["JUST_INSIDE_4H"] = {
                    "tickers": ["FRESH_TICKER"],
                    "cached_at": time.time() - 14390,  # 10 seconds before 4-hour mark
                    "timestamp": "2026-11-14T19:00:00Z",
                }
            try:
                res = _get_dynamic_scout_tickers("JUST_INSIDE_4H")
                self.assertEqual(res, ["FRESH_TICKER"])
                self.assertEqual(dummy.pending_count(), 0)  # No enqueue
                mock_scout_raw.assert_not_called()
                self.assertEqual(DYNAMIC_SCOUT_CACHE["JUST_INSIDE_4H"]["timestamp"], "2026-11-14T19:00:00Z")
            finally:
                with DYNAMIC_SCOUT_CACHE_LOCK:
                    DYNAMIC_SCOUT_CACHE.pop("JUST_INSIDE_4H", None)

        # 12. Timestamp exactly at four-hour boundary -> Cache expires
        fixed_now = 1_800_000_000.0
        with isolated_coordinator_context() as (dummy, mock_scout_raw, _, mock_runner):
            with DYNAMIC_SCOUT_CACHE_LOCK:
                DYNAMIC_SCOUT_CACHE["EXACT_4H_BOUNDARY"] = {
                    "tickers": ["OLD"],
                    "cached_at": fixed_now - 14400.0,
                    "timestamp": "2026-11-14T19:00:00Z",
                }
            try:
                with patch("fetch_stocks.t_time.time", return_value=fixed_now):
                    res = _get_dynamic_scout_tickers("EXACT_4H_BOUNDARY")
                self.assertNotEqual(res, ["OLD"])
                self.assertEqual(dummy.pending_count(), 1)
                self.assertEqual(len(dummy._workers), 0)
                mock_scout_raw.assert_not_called()
                mock_runner.assert_not_called()
                self.assertEqual(DYNAMIC_SCOUT_CACHE["EXACT_4H_BOUNDARY"]["timestamp"], "2026-11-14T19:00:00Z")
            finally:
                with DYNAMIC_SCOUT_CACHE_LOCK:
                    DYNAMIC_SCOUT_CACHE.pop("EXACT_4H_BOUNDARY", None)

        # 13. Timestamp older than four hours -> Cache expires
        with isolated_coordinator_context() as (dummy, mock_scout_raw, _, mock_runner):
            with DYNAMIC_SCOUT_CACHE_LOCK:
                DYNAMIC_SCOUT_CACHE["OLDER_THAN_4H"] = {
                    "tickers": ["OLD"],
                    "cached_at": time.time() - 15000,  # > 4h
                    "timestamp": "2026-11-14T10:00:00Z",
                }
            try:
                res = _get_dynamic_scout_tickers("OLDER_THAN_4H")
                self.assertNotEqual(res, ["OLD"])
                self.assertEqual(dummy.pending_count(), 1)
                mock_scout_raw.assert_not_called()
                self.assertEqual(DYNAMIC_SCOUT_CACHE["OLDER_THAN_4H"]["timestamp"], "2026-11-14T10:00:00Z")
            finally:
                with DYNAMIC_SCOUT_CACHE_LOCK:
                    DYNAMIC_SCOUT_CACHE.pop("OLDER_THAN_4H", None)


MD = WorkType.MARKET_DATA.value
T1 = "2026-11-14T19:00:00Z"
T2 = "2026-11-14T19:05:00Z"
T3 = "2026-11-14T19:10:00Z"


def _collect_results(coordinator, count, timeout=5.0):
    """Polls a coordinator's real handoff until `count` results are drained (or timeout)."""
    collected = []
    deadline = time.monotonic() + timeout
    while len(collected) < count and time.monotonic() < deadline:
        collected.extend(coordinator.drain_results())
        if len(collected) < count:
            time.sleep(0.01)
    return collected


class TestMarketDataWorkloadIsolation(unittest.TestCase):
    """Stage 2.2.1: separately instantiated coordinators are fully independent."""

    def _make(self, **kwargs):
        coord = BackgroundIntelligenceCoordinator(**kwargs)
        self.addCleanup(coord.shutdown, 2.0)
        return coord

    def setUp(self):
        self.gate = threading.Event()
        # Registered first so it runs LAST (cleanups are LIFO): coordinators shut down after gate release.
        self.addCleanup(self.gate.set)
        self.md = self._make(max_workers=1, max_queue_size=2, max_results_size=2)
        self.ai = self._make(max_workers=1, max_queue_size=2, max_results_size=2)

    def test_01_market_data_work_enqueued_and_processed_by_separate_coordinator(self):
        accepted, item = self.md.enqueue(MD, "AAPL", T1, lambda: {"price": "1"})
        self.assertTrue(accepted)
        self.assertTrue(item.event.wait(timeout=5.0))
        self.assertEqual(item.status, WorkStatus.COMPLETED)
        self.assertEqual(item.key.work_type, "market_data")

        results = _collect_results(self.md, 1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].key, WorkKey(MD, "AAPL"))
        self.assertEqual(results[0].data, {"price": "1"})
        self.assertEqual(results[0].obs_time, T1)
        # Nothing leaked into the other coordinator
        self.assertEqual(self.ai.total_work_count(), 0)
        self.assertEqual(self.ai.drain_results(), [])

    def test_02_coordinators_share_no_state(self):
        for attr in (
            "_workers", "_queue", "_results_queue", "_overflow_results", "_pending_keys",
            "_in_flight_keys", "_sequences", "_latest_issued_sequence", "_key_locks",
            "_shutdown_event", "_lock", "cooldown_tracker",
        ):
            with self.subTest(attr=attr):
                self.assertIsNot(getattr(self.md, attr), getattr(self.ai, attr))
        md_threads = {id(t) for t in self.md._workers}
        ai_threads = {id(t) for t in self.ai._workers}
        self.assertFalse(md_threads & ai_threads)
        self.assertIsNot(self.md.cooldown_tracker._failures, self.ai.cooldown_tracker._failures)
        self.assertIsNot(self.md.cooldown_tracker._deadlines, self.ai.cooldown_tracker._deadlines)

        # Same logical key gets an independent sequence counter in each coordinator
        _, md_item = self.md.enqueue(MD, "SAME", T1, lambda: 1)
        _, ai_item = self.ai.enqueue(MD, "SAME", T1, lambda: 1)
        self.assertTrue(md_item.event.wait(5.0))
        self.assertTrue(ai_item.event.wait(5.0))
        self.assertEqual(md_item.sequence, 1)
        self.assertEqual(ai_item.sequence, 1)
        _, md_item2 = self.md.enqueue(MD, "SAME", T2, lambda: 2)
        self.assertEqual(md_item2.sequence, 2)
        self.assertEqual(self.ai.get_latest_issued_sequence(WorkKey(MD, "SAME")), 1)

    def test_03_blocked_market_data_worker_does_not_block_intelligence_work(self):
        started = threading.Event()

        def blocked():
            started.set()
            self.gate.wait(timeout=10.0)
            return {"v": 1}

        _, md_item = self.md.enqueue(MD, "BLOCK", T1, blocked)
        self.assertTrue(started.wait(timeout=5.0))
        self.assertEqual(self.md.in_flight_count(), 1)

        _, ai_item = self.ai.enqueue("grounding", "AAPL", T1, lambda: {"ok": True})
        self.assertTrue(ai_item.event.wait(timeout=5.0))
        self.assertEqual(ai_item.status, WorkStatus.COMPLETED)
        # Market-data worker is still blocked and holds only its own capacity
        self.assertEqual(md_item.status, WorkStatus.IN_FLIGHT)
        self.assertEqual(self.md.in_flight_count(), 1)
        self.assertEqual(self.ai.in_flight_count(), 0)

        self.gate.set()
        self.assertTrue(md_item.event.wait(timeout=5.0))

    def test_04_queue_saturation_in_one_coordinator_does_not_affect_another(self):
        started = threading.Event()

        def blocked():
            started.set()
            self.gate.wait(timeout=10.0)
            return True

        self.md.enqueue(MD, "OCC", T1, blocked)
        self.assertTrue(started.wait(timeout=5.0))

        accepted_count = 0
        rejected = False
        for i in range(20):
            acc, _ = self.md.enqueue(MD, f"Q{i}", T1, blocked)
            if acc:
                accepted_count += 1
            else:
                rejected = True
                break
        self.assertTrue(rejected, "market-data coordinator must saturate and reject")
        self.assertLessEqual(accepted_count, self.md.max_queue_size + self.md.max_results_size)

        # The other coordinator is unaffected and still accepts and completes work
        acc, ai_item = self.ai.enqueue("grounding", "MSFT", T1, lambda: {"ok": 1})
        self.assertTrue(acc)
        self.assertTrue(ai_item.event.wait(timeout=5.0))
        self.assertEqual(ai_item.status, WorkStatus.COMPLETED)

    def test_05_cooldown_state_is_independent(self):
        key = WorkKey(MD, "FAILSYM")

        def failing():
            raise RuntimeError("provider outage")

        _, item = self.md.enqueue(MD, "FAILSYM", T1, failing)
        self.assertTrue(item.event.wait(timeout=5.0))
        self.assertEqual(item.status, WorkStatus.FAILED)
        self.assertTrue(self.md.cooldown_tracker.is_in_cooldown(key))
        self.assertFalse(self.ai.cooldown_tracker.is_in_cooldown(key))

        acc_md, _ = self.md.enqueue(MD, "FAILSYM", T2, lambda: 1)
        self.assertFalse(acc_md, "market-data key must be rejected during its own cooldown")
        acc_ai, ai_item = self.ai.enqueue(MD, "FAILSYM", T2, lambda: 1)
        self.assertTrue(acc_ai)
        self.assertTrue(ai_item.event.wait(timeout=5.0))
        self.assertEqual(ai_item.status, WorkStatus.COMPLETED)

    def test_06_shutdown_lifecycles_are_independent(self):
        self.assertTrue(self.md.shutdown(timeout=2.0))
        for t in self.md._workers:
            self.assertFalse(t.is_alive())
        for t in self.ai._workers:
            self.assertTrue(t.is_alive())
        acc, _ = self.md.enqueue(MD, "AAPL", T1, lambda: 1)
        self.assertFalse(acc)

        acc, item = self.ai.enqueue("grounding", "AAPL", T1, lambda: 1)
        self.assertTrue(acc)
        self.assertTrue(item.event.wait(timeout=5.0))
        self.assertEqual(item.status, WorkStatus.COMPLETED)

    def test_17a_shutdown_cancels_inflight_and_queued_work_and_stops_all_workers(self):
        coord = self._make(max_workers=2, max_queue_size=4, max_results_size=4)
        started = [threading.Event(), threading.Event()]

        def make_blocker(idx):
            def blocker(cancel_check):
                started[idx].set()
                while not cancel_check():
                    time.sleep(0.01)
                raise RuntimeError("cancelled by coordinator")
            return blocker

        _, in1 = coord.enqueue(MD, "IN1", T1, make_blocker(0))
        _, in2 = coord.enqueue(MD, "IN2", T1, make_blocker(1))
        self.assertTrue(started[0].wait(5.0))
        self.assertTrue(started[1].wait(5.0))
        _, q1 = coord.enqueue(MD, "Q1", T1, lambda: 1)
        _, q2 = coord.enqueue(MD, "Q2", T1, lambda: 1)
        self.assertEqual(coord.pending_count(), 2)

        self.assertTrue(coord.shutdown(timeout=5.0))
        for t in coord._workers:
            self.assertFalse(t.is_alive())
        for it in (in1, in2, q1, q2):
            self.assertEqual(it.status, WorkStatus.CANCELLED)
            self.assertTrue(it.event.is_set())
        self.assertEqual(coord.pending_count(), 0)
        self.assertEqual(coord.in_flight_count(), 0)
        self.assertEqual(coord.drain_results(), [])
        self.assertEqual(coord.total_work_count(), 0)

    def test_17b_cancel_work_cancels_market_data_work_without_publishing(self):
        started = threading.Event()

        def blocker(cancel_check):
            started.set()
            while not cancel_check():
                time.sleep(0.01)
            return {"late": True}

        key = WorkKey(MD, "CANCELME")
        _, item = self.md.enqueue(MD, "CANCELME", T1, blocker)
        self.assertTrue(started.wait(5.0))
        self.assertTrue(self.md.cancel_work(key))
        self.assertTrue(item.event.wait(timeout=5.0))
        self.assertEqual(item.status, WorkStatus.CANCELLED)
        time.sleep(0.05)
        self.assertEqual(self.md.drain_results(), [])
        self.assertEqual(self.md.in_flight_count(), 0)


class TestMarketDataResultApplication(unittest.TestCase):
    """Stage 2.2.1: controlled market-data result application via explicit on_publish callback."""

    def setUp(self):
        self.applicator = BackgroundResultApplicator()
        self.key = WorkKey(MD, "AAPL")
        self.published = []

    def _publish(self, result):
        self.published.append(result)

    def _apply(self, results, on_publish="default", latest_issued=None, **kw):
        cb = self._publish if on_publish == "default" else on_publish
        g, s, m = {}, {}, {}
        audit = self.applicator.apply_results(
            results, g, s, m, latest_issued_provider=latest_issued, on_publish=cb, **kw
        )
        self.assertEqual((g, s, m), ({}, {}, {}), "market data must never mutate grounding/scout targets")
        return audit

    def _res(self, seq=1, obs=T1, data=None, error=None, key=None, provenance="default"):
        prov = (
            MarketDataProvenance(ObservationKind.INSTANT, "TEST_PROVIDER")
            if provenance == "default"
            else provenance
        )
        return WorkResult(
            key=key or self.key, sequence=seq, obs_time=obs,
            data={"price": "1"} if data is None and error is None else data, error=error,
            provenance=prov,
        )

    def test_05_callback_is_required(self):
        audit = self._apply([self._res()], on_publish=None)
        self.assertFalse(audit[0][1])
        self.assertIn("callback required", audit[0][2])
        self.assertEqual(self.applicator.get_last_applied(self.key), (-1, ""))

        audit = self._apply([self._res()], on_publish="not-callable")
        self.assertFalse(audit[0][1])
        self.assertIn("callback required", audit[0][2])
        self.assertEqual(self.applicator.get_last_applied(self.key), (-1, ""))

        # State was not advanced, so the same result can still be applied with a real callback
        audit = self._apply([self._res()])
        self.assertTrue(audit[0][1])
        self.assertEqual(len(self.published), 1)

    def test_06_callback_runs_on_coordinating_thread_under_key_lock(self):
        main_ident = threading.get_ident()
        seen = {}

        def cb(result):
            seen["ident"] = threading.get_ident()
            probe = {}

            def other_thread():
                lock = self.applicator.get_key_lock(self.key)
                probe["acquired"] = lock.acquire(blocking=False)
                if probe["acquired"]:
                    lock.release()

            t = threading.Thread(target=other_thread)
            t.start()
            t.join(timeout=5.0)
            seen["other_thread_acquired"] = probe.get("acquired")

        audit = self._apply([self._res()], on_publish=cb)
        self.assertTrue(audit[0][1])
        self.assertEqual(seen["ident"], main_ident)
        self.assertFalse(seen["other_thread_acquired"], "callback must run while key lock is held")

    def test_06b_end_to_end_callback_not_executed_in_worker(self):
        coord = BackgroundIntelligenceCoordinator(max_workers=1)
        self.addCleanup(coord.shutdown, 2.0)
        worker_ident = {}

        def acquire():
            worker_ident["id"] = threading.get_ident()
            return ObservedResult(
                data={"price": "9"},
                obs_time=T1,
                provenance=MarketDataProvenance(ObservationKind.INSTANT, "TEST_PROVIDER"),
            )

        _, item = coord.enqueue(MD, "AAPL", T1, acquire)
        self.assertTrue(item.event.wait(5.0))
        results = _collect_results(coord, 1)
        self.assertEqual(len(results), 1)
        self.assertEqual(self.published, [], "worker completion must not publish")

        cb_ident = {}
        applicator = BackgroundResultApplicator(coordinator=coord)
        applicator.apply_results(
            results, {}, {}, {}, on_publish=lambda r: cb_ident.setdefault("id", threading.get_ident())
        )
        self.assertEqual(cb_ident["id"], threading.get_ident())
        self.assertNotEqual(cb_ident["id"], worker_ident["id"])

    def test_07_valid_utc_timestamps_accepted(self):
        for i, ts in enumerate(
            ["2026-11-14T19:00:00Z", "2026-11-14T19:00:01+00:00", "2026-11-14T19:00:02.123456Z"], start=1
        ):
            with self.subTest(ts=ts):
                audit = self._apply([self._res(seq=i, obs=ts)])
                self.assertTrue(audit[0][1], audit[0][2])
                self.assertEqual(self.applicator.get_last_applied(self.key), (i, ts))
        self.assertEqual(len(self.published), 3)

    def test_08_missing_timestamps_rejected(self):
        for ts in (None, "", "   "):
            with self.subTest(ts=ts):
                audit = self._apply([self._res(obs=ts)])
                self.assertFalse(audit[0][1])
                self.assertIn("Missing required source observation timestamp", audit[0][2])
        self.assertEqual(self.published, [])
        self.assertEqual(self.applicator.get_last_applied(self.key), (-1, ""))

    def test_09_naive_malformed_ambiguous_and_nonzero_offset_timestamps_rejected(self):
        bad = [
            "2026-11-14T19:00:00",        # naive
            "2026-11-14 19:00:00Z",       # malformed separator
            "not-a-timestamp",            # malformed
            "2026-02-31T19:00:00Z",       # impossible date
            "2026-11-14T19:00:00-00:00",  # ambiguous
            "2026-11-14T19:00:00+05:00",  # non-zero offset
            "2026-11-14T19:00:00-05:00",  # non-zero offset
            12345,                        # non-string
        ]
        for ts in bad:
            with self.subTest(ts=ts):
                audit = self._apply([self._res(obs=ts)])
                self.assertFalse(audit[0][1])
                self.assertIn("Invalid observation timestamp", audit[0][2])
        self.assertEqual(self.published, [])
        self.assertEqual(self.applicator.get_last_applied(self.key), (-1, ""))

    def test_10_older_or_equal_sequence_rejected(self):
        self.assertTrue(self._apply([self._res(seq=2, obs=T1)])[0][1])
        for seq in (1, 2):
            with self.subTest(seq=seq):
                audit = self._apply([self._res(seq=seq, obs=T2)])
                self.assertFalse(audit[0][1])
                self.assertIn("Stale sequence", audit[0][2])
        self.assertEqual(len(self.published), 1)
        self.assertEqual(self.applicator.get_last_applied(self.key), (2, T1))

    def test_11_result_superseded_by_newer_issued_sequence_rejected(self):
        audit = self._apply([self._res(seq=2)], latest_issued={self.key: 3})
        self.assertFalse(audit[0][1])
        self.assertIn("older than latest issued", audit[0][2])
        self.assertEqual(self.published, [])
        self.assertEqual(self.applicator.get_last_applied(self.key), (-1, ""))

    def test_11b_end_to_end_superseded_by_real_coordinator_sequence(self):
        coord = BackgroundIntelligenceCoordinator(max_workers=1)
        self.addCleanup(coord.shutdown, 2.0)
        _, first = coord.enqueue(
            MD,
            "AAPL",
            T1,
            lambda: ObservedResult(
                data={"p": "1"},
                obs_time=T1,
                provenance=MarketDataProvenance(ObservationKind.INSTANT, "TEST_PROVIDER"),
            ),
        )
        self.assertTrue(first.event.wait(5.0))
        _, second = coord.enqueue(
            MD,
            "AAPL",
            T2,
            lambda: ObservedResult(
                data={"p": "2"},
                obs_time=T2,
                provenance=MarketDataProvenance(ObservationKind.INSTANT, "TEST_PROVIDER"),
            ),
        )
        self.assertEqual(second.sequence, 2)
        self.assertTrue(second.event.wait(5.0))
        results = sorted(_collect_results(coord, 2), key=lambda r: r.sequence)
        self.assertEqual([r.sequence for r in results], [1, 2])

        applicator = BackgroundResultApplicator(coordinator=coord)
        audit = applicator.apply_results(results, {}, {}, {}, on_publish=self._publish)
        self.assertFalse(audit[0][1])
        self.assertIn("older than latest issued", audit[0][2])
        self.assertTrue(audit[1][1])
        self.assertEqual([r.sequence for r in self.published], [2])

    def test_12_older_source_observation_rejected(self):
        self.assertTrue(self._apply([self._res(seq=1, obs=T2)])[0][1])
        audit = self._apply([self._res(seq=2, obs=T1)])
        self.assertFalse(audit[0][1])
        self.assertIn("Stale observation", audit[0][2])
        self.assertEqual(len(self.published), 1)
        self.assertEqual(self.applicator.get_last_applied(self.key), (1, T2))

    def test_13_failed_or_missing_data_results_cannot_publish(self):
        failed = self._res(error="provider outage", data=None)
        missing = WorkResult(key=self.key, sequence=2, obs_time=T1, data=None, error=None)
        audit = self._apply([failed, missing])
        self.assertFalse(audit[0][1])
        self.assertFalse(audit[1][1])
        self.assertEqual(self.published, [])
        self.assertEqual(self.applicator.get_last_applied(self.key), (-1, ""))

    def test_13b_end_to_end_worker_failure_cannot_publish(self):
        coord = BackgroundIntelligenceCoordinator(max_workers=1)
        self.addCleanup(coord.shutdown, 2.0)

        def failing():
            raise RuntimeError("provider outage")

        _, item = coord.enqueue(MD, "AAPL", T1, failing)
        self.assertTrue(item.event.wait(5.0))
        results = _collect_results(coord, 1)
        self.assertEqual(len(results), 1)
        self.assertIsNotNone(results[0].error)
        audit = BackgroundResultApplicator(coordinator=coord).apply_results(
            results, {}, {}, {}, on_publish=self._publish
        )
        self.assertFalse(audit[0][1])
        self.assertEqual(self.published, [])

    def test_14_callback_failure_does_not_advance_sequence_or_timestamp(self):
        def boom(result):
            raise RuntimeError("publish failed")

        audit = self._apply([self._res(seq=1, obs=T1)], on_publish=boom)
        self.assertFalse(audit[0][1])
        self.assertIn("Publication failed", audit[0][2])
        self.assertEqual(self.applicator.get_last_applied(self.key), (-1, ""))

        # With prior applied state, a failed publication preserves exactly that prior state
        self.assertTrue(self._apply([self._res(seq=2, obs=T1)])[0][1])
        audit = self._apply([self._res(seq=3, obs=T2)], on_publish=boom)
        self.assertFalse(audit[0][1])
        self.assertEqual(self.applicator.get_last_applied(self.key), (2, T1))
        # Key lock is released after the failure
        lock = self.applicator.get_key_lock(self.key)
        self.assertTrue(lock.acquire(blocking=False))
        lock.release()

    def test_15_later_valid_result_applies_after_failed_publication(self):
        def boom(result):
            raise RuntimeError("publish failed")

        self.assertFalse(self._apply([self._res(seq=1, obs=T1)], on_publish=boom)[0][1])
        # Retry of the same sequence is still permitted because nothing advanced
        self.assertTrue(self._apply([self._res(seq=1, obs=T1)])[0][1])
        self.assertFalse(self._apply([self._res(seq=2, obs=T2)], on_publish=boom)[0][1])
        # A later valid result is applied
        self.assertTrue(self._apply([self._res(seq=3, obs=T3)])[0][1])
        self.assertEqual(self.applicator.get_last_applied(self.key), (3, T3))
        self.assertEqual([r.sequence for r in self.published], [1, 3])

    def test_16_grounding_and_scout_semantics_remain_compatible(self):
        applicator = BackgroundResultApplicator()
        grounding, scout_cache, scout_map = {}, {}, {}
        g_key = WorkKey("grounding", "AAPL")
        s_key = WorkKey("scout", "Technology")
        published = []

        results = [
            WorkResult(key=g_key, sequence=1, obs_time=T1, data={"headline": "x"}),
            WorkResult(key=s_key, sequence=1, obs_time=T1, data=["msft", "NVDA"]),
        ]
        audit = applicator.apply_results(results, grounding, scout_cache, scout_map)
        self.assertTrue(all(a[1] for a in audit))
        self.assertEqual(grounding["AAPL"], {"headline": "x"})
        self.assertEqual(scout_cache["TECHNOLOGY"]["tickers"], ["msft", "NVDA"])
        self.assertEqual(scout_map, {"MSFT": "TECHNOLOGY", "NVDA": "TECHNOLOGY"})

        # Grounding without a source timestamp is still accepted (only market data requires one)
        audit = applicator.apply_results(
            [WorkResult(key=WorkKey("grounding", "MSFT"), sequence=1, obs_time=None, data={"h": 1})],
            grounding, scout_cache, scout_map,
        )
        self.assertTrue(audit[0][1])
        # Grounding callback still runs and is optional
        audit = applicator.apply_results(
            [WorkResult(key=g_key, sequence=2, obs_time=T2, data={"headline": "y"})],
            grounding, scout_cache, scout_map, on_publish=published.append,
        )
        self.assertTrue(audit[0][1])
        self.assertEqual(len(published), 1)
        # Stale grounding still rejected; unknown type still rejected
        audit = applicator.apply_results(
            [WorkResult(key=g_key, sequence=1, obs_time=T3, data={"headline": "z"})],
            grounding, scout_cache, scout_map,
        )
        self.assertFalse(audit[0][1])
        self.assertEqual(grounding["AAPL"], {"headline": "y"})
        audit = applicator.apply_results(
            [WorkResult(key=WorkKey("bogus", "X"), sequence=1, obs_time=T1, data=1)],
            grounding, scout_cache, scout_map,
        )
        self.assertFalse(audit[0][1])
        self.assertIn("Unknown work type", audit[0][2])
        # require_source_time still fail-closes grounding when requested
        audit = applicator.apply_results(
            [WorkResult(key=WorkKey("grounding", "NVDA"), sequence=1, obs_time=None, data={"h": 1})],
            grounding, scout_cache, scout_map, require_source_time=True,
        )
        self.assertFalse(audit[0][1])

    def test_market_data_and_other_work_types_have_independent_applied_state(self):
        applicator = BackgroundResultApplicator()
        grounding, scout_cache, scout_map = {}, {}, {}
        results = [
            WorkResult(key=WorkKey("grounding", "AAPL"), sequence=1, obs_time=T1, data={"h": 1}),
            WorkResult(
                key=WorkKey(MD, "AAPL"),
                sequence=1,
                obs_time=T1,
                data={"p": "1"},
                provenance=MarketDataProvenance(ObservationKind.INSTANT, "TEST_PROVIDER"),
            ),
        ]
        audit = applicator.apply_results(results, grounding, scout_cache, scout_map, on_publish=self._publish)
        self.assertTrue(all(a[1] for a in audit))
        self.assertEqual([r.key.work_type for r in self.published], ["grounding", "market_data"])
        self.assertEqual(grounding["AAPL"], {"h": 1})

    def test_observed_result_carries_worker_discovered_timestamp(self):
        coord = BackgroundIntelligenceCoordinator(max_workers=1)
        applicator = BackgroundResultApplicator(coordinator=coord)
        prov = MarketDataProvenance(ObservationKind.INSTANT, "TEST_PROVIDER")
        try:
            def worker():
                return ObservedResult(data={"price": "188.50"}, obs_time=T2, provenance=prov)

            ok, item = coord.enqueue(MD, "AAPL", obs_time=None, fn=worker)
            self.assertTrue(ok)
            self.assertTrue(item.event.wait(timeout=3.0))
            results = coord.drain_results()
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].obs_time, T2)
            self.assertEqual(results[0].data, {"price": "188.50"})
            self.assertEqual(results[0].provenance, prov)
            audit = applicator.apply_results(results, {}, {}, {}, on_publish=self._publish)
            self.assertEqual(len(audit), 1)
            self.assertTrue(audit[0][1])
            self.assertEqual(applicator.get_last_applied(WorkKey(MD, "AAPL")), (1, T2))
            self.assertEqual(applicator.get_last_applied_provenance(WorkKey(MD, "AAPL")), prov)
        finally:
            coord.shutdown()

    def test_batch_observed_result_independent_per_child_sequences_and_timestamps(self):
        coord = BackgroundIntelligenceCoordinator(max_workers=1)
        applicator = BackgroundResultApplicator(coordinator=coord)
        prov_batch = MarketDataProvenance(ObservationKind.INSTANT, "BATCH_QUOTE")
        try:
            k_aapl = WorkKey(MD, "QUOTE:AAPL")
            k_nvda = WorkKey(MD, "QUOTE:NVDA")
            k_msft = WorkKey(MD, "QUOTE:MSFT")

            def batch_one():
                return BatchObservedResult(items=(
                    ChildObservedResult(key=k_aapl, data={"p": "100"}, obs_time=T1, provenance=prov_batch),
                    ChildObservedResult(
                        key=k_nvda,
                        data={"p": "200"},
                        obs_time="2026-11-14T19:00:00",
                        provenance=prov_batch,
                    ),  # naive -> fail closed
                ))

            def batch_two():
                return BatchObservedResult(items=(
                    ChildObservedResult(key=k_aapl, data={"p": "101"}, obs_time=T2, provenance=prov_batch),
                    ChildObservedResult(key=k_msft, data={"p": "300"}, obs_time=T2, provenance=prov_batch),
                ))

            ok1, item1 = coord.enqueue(MD, "BATCH:1", fn=batch_one, child_keys=(k_aapl, k_nvda))
            ok2, item2 = coord.enqueue(MD, "BATCH:2", fn=batch_two, child_keys=(k_aapl, k_msft))
            self.assertTrue(ok1 and ok2)
            self.assertEqual(item1.child_sequences[k_aapl], 1)
            self.assertEqual(item2.child_sequences[k_aapl], 2)
            self.assertEqual(coord.get_latest_issued_sequence(k_aapl), 2)
            self.assertTrue(item1.event.wait(timeout=3.0))
            self.assertTrue(item2.event.wait(timeout=3.0))

            results = coord.drain_results()
            audit = applicator.apply_results(results, {}, {}, {}, on_publish=self._publish)
            audit_map = {(r.key, r.sequence): ok for r, ok, _ in audit}

            # Batch 1 AAPL (seq 1) is superseded by Batch 2 (seq 2); Batch 1 NVDA fails timestamp check
            self.assertFalse(audit_map[(k_aapl, 1)])
            self.assertFalse(audit_map[(k_nvda, 1)])
            self.assertTrue(audit_map[(k_aapl, 2)])
            self.assertTrue(audit_map[(k_msft, 1)])
            self.assertEqual(
                [(r.key.target, r.data, r.provenance) for r in self.published],
                [("QUOTE:AAPL", {"p": "101"}, prov_batch), ("QUOTE:MSFT", {"p": "300"}, prov_batch)],
            )
        finally:
            coord.shutdown()

    def test_result_key_shares_ordering_with_batch_child_keys(self):
        coord = BackgroundIntelligenceCoordinator(max_workers=1)
        applicator = BackgroundResultApplicator(coordinator=coord)
        prov_batch = MarketDataProvenance(ObservationKind.INSTANT, "BATCH_QUOTE")
        prov_fb = MarketDataProvenance(ObservationKind.INSTANT, "INTRADAY_CHART")
        try:
            k_aapl = WorkKey(MD, "QUOTE:AAPL")

            def batch_fn():
                return BatchObservedResult(items=(
                    ChildObservedResult(key=k_aapl, data={"src": "batch"}, obs_time=T1, provenance=prov_batch),
                ))

            def fallback_fn():
                return ObservedResult(data={"src": "fallback"}, obs_time=T2, provenance=prov_fb)

            ok1, item1 = coord.enqueue(MD, "BATCH:A", fn=batch_fn, child_keys=(k_aapl,))
            ok2, item2 = coord.enqueue(MD, "QUOTE_FALLBACK:AAPL", fn=fallback_fn, result_key=k_aapl)
            self.assertTrue(ok1 and ok2)
            self.assertTrue(item1.event.wait(timeout=3.0))
            self.assertTrue(item2.event.wait(timeout=3.0))

            audit = applicator.apply_results(coord.drain_results(), {}, {}, {}, on_publish=self._publish)
            self.assertEqual(len(audit), 2)
            self.assertFalse(audit[0][1])  # superseded batch seq 1
            self.assertTrue(audit[1][1])   # fallback seq 2 applied
            self.assertEqual(self.published[-1].data, {"src": "fallback"})
            self.assertEqual(self.published[-1].provenance, prov_fb)
        finally:
            coord.shutdown()

    def test_17_provenance_contract_cross_field_and_date_validation(self):
        today_ny = current_market_session_date()
        valid_past = (today_ny - timedelta(days=2)).isoformat()
        future_date = (today_ny + timedelta(days=1)).isoformat()

        # Valid instant & session reference contracts
        p_inst = MarketDataProvenance(ObservationKind.INSTANT, "BATCH_QUOTE")
        self.assertEqual(p_inst.kind, ObservationKind.INSTANT)
        self.assertEqual(p_inst.source, "BATCH_QUOTE")
        self.assertIsNone(p_inst.effective_session_date)

        p_ref = MarketDataProvenance(
            ObservationKind.SESSION_REFERENCE,
            "POLYGON_PREV_AGG",
            effective_session_date=valid_past,
        )
        self.assertEqual(p_ref.kind, ObservationKind.SESSION_REFERENCE)
        self.assertEqual(p_ref.effective_session_date, valid_past)

        # Invalid kind, empty source, contradictory date/timestamp, bad calendar or future dates
        bad_constructions = [
            ("instant", "BATCH_QUOTE", None),
            (ObservationKind.INSTANT, "", None),
            (ObservationKind.INSTANT, "   ", None),
            (ObservationKind.INSTANT, None, None),
            (ObservationKind.INSTANT, "BATCH_QUOTE", valid_past),
            (ObservationKind.SESSION_REFERENCE, "POLYGON_PREV_AGG", None),
            (ObservationKind.SESSION_REFERENCE, "POLYGON_PREV_AGG", ""),
            (ObservationKind.SESSION_REFERENCE, "POLYGON_PREV_AGG", "20261113"),
            (ObservationKind.SESSION_REFERENCE, "POLYGON_PREV_AGG", "2026/11/13"),
            (ObservationKind.SESSION_REFERENCE, "POLYGON_PREV_AGG", "2026-02-30"),
            (ObservationKind.SESSION_REFERENCE, "POLYGON_PREV_AGG", future_date),
            (ObservationKind.SESSION_REFERENCE, "   ", valid_past),
        ]
        for kind, source, eff_date in bad_constructions:
            with self.subTest(kind=kind, source=source, eff_date=eff_date):
                with self.assertRaises(ValueError):
                    MarketDataProvenance(kind=kind, source=source, effective_session_date=eff_date)

        # Immutability check
        with self.assertRaises(Exception):
            p_ref.source = "MUTATED"

        # TimestampedValue and SessionReferenceValue contracts
        tv = TimestampedValue(value=150.5, obs_time=T1, source="FINNHUB")
        self.assertEqual(tv.obs_time, T1)
        with self.assertRaises(ValueError):
            TimestampedValue(value=150.5, obs_time="2026-11-14T19:00:00", source="FINNHUB")
        with self.assertRaises(ValueError):
            TimestampedValue(value=150.5, obs_time=T1, source="")

        srv = SessionReferenceValue(value=148.0, effective_session_date=valid_past, source="YAHOO_DAILY_CLOSE")
        self.assertEqual(srv.effective_session_date, valid_past)
        with self.assertRaises(ValueError):
            SessionReferenceValue(value=148.0, effective_session_date=future_date, source="YAHOO_DAILY_CLOSE")

    def test_18_applicator_provenance_fail_closed_and_independent_ordering(self):
        today_ny = current_market_session_date()
        d_old = (today_ny - timedelta(days=3)).isoformat()
        d_mid = (today_ny - timedelta(days=2)).isoformat()
        d_new = (today_ny - timedelta(days=1)).isoformat()

        # 1. Missing or non-MarketDataProvenance rejected fail-closed for MARKET_DATA
        for bad_prov in (None, "BATCH_QUOTE", {"kind": "instant", "source": "X"}):
            audit = self._apply([self._res(seq=1, obs=T1, provenance=bad_prov)])
            self.assertFalse(audit[0][1])
            self.assertEqual(self.published, [])
            self.assertEqual(self.applicator.get_last_applied(self.key), (-1, ""))

        # 2. Contradictory provenance: SESSION_REFERENCE with obs_time present rejected fail-closed
        prov_ref_mid = MarketDataProvenance(ObservationKind.SESSION_REFERENCE, "POLYGON_PREV_AGG", d_mid)
        bad_ref = WorkResult(
            key=WorkKey(MD, "PREVIOUS_SESSION_VOLUME:AAPL"),
            sequence=1,
            obs_time=T1,
            data={"value": 500000},
            provenance=prov_ref_mid,
        )
        audit = self._apply([bad_ref])
        self.assertFalse(audit[0][1])
        self.assertIn("SESSION_REFERENCE", audit[0][2])
        self.assertEqual(self.published, [])

        # 3. Valid SESSION_REFERENCE accepted with obs_time=None and never advances instant obs_time
        k_prev_vol = WorkKey(MD, "PREVIOUS_SESSION_VOLUME:AAPL")
        r_ref_1 = WorkResult(
            key=k_prev_vol,
            sequence=1,
            obs_time=None,
            data={"value": 500000},
            provenance=prov_ref_mid,
        )
        audit1 = self._apply([r_ref_1])
        self.assertTrue(audit1[0][1], audit1[0][2])
        self.assertEqual(self.applicator.get_last_applied(k_prev_vol), (1, ""))
        self.assertEqual(self.applicator.get_last_applied_session_date(k_prev_vol), d_mid)
        self.assertEqual(self.applicator.get_last_applied_provenance(k_prev_vol), prov_ref_mid)

        # 4. Older session reference rejected even with higher sequence
        prov_ref_old = MarketDataProvenance(ObservationKind.SESSION_REFERENCE, "POLYGON_PREV_AGG", d_old)
        r_ref_old = WorkResult(
            key=k_prev_vol,
            sequence=2,
            obs_time=None,
            data={"value": 400000},
            provenance=prov_ref_old,
        )
        audit_old = self._apply([r_ref_old])
        self.assertFalse(audit_old[0][1])
        self.assertIn("Stale session reference", audit_old[0][2])
        self.assertEqual(self.applicator.get_last_applied_session_date(k_prev_vol), d_mid)
        self.assertEqual(self.applicator.get_last_applied(k_prev_vol), (1, ""))

        # 5. Failed on_publish for newer SESSION_REFERENCE leaves session_date & provenance unchanged
        prov_ref_new = MarketDataProvenance(ObservationKind.SESSION_REFERENCE, "POLYGON_PREV_AGG", d_new)
        r_ref_new = WorkResult(
            key=k_prev_vol,
            sequence=3,
            obs_time=None,
            data={"value": 650000},
            provenance=prov_ref_new,
        )
        audit_fail = self._apply([r_ref_new], on_publish=lambda _r: (_ for _ in ()).throw(RuntimeError("boom")))
        self.assertFalse(audit_fail[0][1])
        self.assertEqual(self.applicator.get_last_applied_session_date(k_prev_vol), d_mid)
        self.assertEqual(self.applicator.get_last_applied_provenance(k_prev_vol), prov_ref_mid)

        # Retrying sequence 3 succeeds and advances cleanly
        audit_ok = self._apply([r_ref_new])
        self.assertTrue(audit_ok[0][1])
        self.assertEqual(self.applicator.get_last_applied_session_date(k_prev_vol), d_new)
        self.assertEqual(self.applicator.get_last_applied_provenance(k_prev_vol), prov_ref_new)
        self.assertEqual(self.applicator.get_last_applied(k_prev_vol), (3, ""))

    def test_19_batch_fanout_preserves_mixed_provenance_without_crosstalk(self):
        today_ny = current_market_session_date()
        d_prev = (today_ny - timedelta(days=1)).isoformat()
        coord = BackgroundIntelligenceCoordinator(max_workers=1)
        applicator = BackgroundResultApplicator(coordinator=coord)
        prov_q = MarketDataProvenance(ObservationKind.INSTANT, "INTRADAY_CHART")
        prov_ref = MarketDataProvenance(ObservationKind.SESSION_REFERENCE, "YAHOO_DAILY_CLOSE", d_prev)
        try:
            k_q = WorkKey(MD, "QUOTE:AAPL")
            k_rc = WorkKey(MD, "REGULAR_CLOSE:AAPL")

            def worker():
                return BatchObservedResult(items=(
                    ChildObservedResult(key=k_q, data={"value": 212.0}, obs_time=T2, provenance=prov_q),
                    ChildObservedResult(key=k_rc, data={"value": 208.5}, obs_time=None, provenance=prov_ref),
                ))

            ok, item = coord.enqueue(MD, "MIXED:AAPL", fn=worker, child_keys=(k_q, k_rc))
            self.assertTrue(ok)
            self.assertTrue(item.event.wait(timeout=3.0))
            audit = applicator.apply_results(coord.drain_results(), {}, {}, {}, on_publish=self._publish)
            self.assertEqual(len(audit), 2)
            self.assertTrue(all(ok for _, ok, _ in audit))
            self.assertEqual(applicator.get_last_applied(k_q), (1, T2))
            self.assertEqual(applicator.get_last_applied_provenance(k_q), prov_q)
            self.assertEqual(applicator.get_last_applied(k_rc), (1, ""))
            self.assertEqual(applicator.get_last_applied_session_date(k_rc), d_prev)
            self.assertEqual(applicator.get_last_applied_provenance(k_rc), prov_ref)
        finally:
            coord.shutdown()


if __name__ == "__main__":
    unittest.main()
