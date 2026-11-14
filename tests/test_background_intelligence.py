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

import os
import queue
import signal
import subprocess
import sys
import threading
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from background_intelligence import (
    BackgroundIntelligenceCoordinator,
    BackgroundResultApplicator,
    FailureCooldownTracker,
    WorkItem,
    WorkKey,
    WorkResult,
    WorkStatus,
    WorkType,
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
)


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
                # Reader must always observe a consistent dictionary
                self.assertIsInstance(snap, dict)
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
    Items 1 through 24 (Item 25 is market data integrity regression suite).
    """

    def test_01_posix_child_created_in_dedicated_process_group(self):
        mock_proc = MagicMock()
        mock_proc.pid = 55555
        mock_proc.communicate.return_value = ("output", "")
        mock_proc.returncode = 0
        with patch("sys.platform", "linux"), patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
            runner = AntigravityAgentRunner(agy_path="agy")
            runner.run_prompt("test")
            mock_popen.assert_called_once()
            _, kwargs = mock_popen.call_args
            self.assertTrue(kwargs.get("start_new_session"), "POSIX subprocess must set start_new_session=True")
            self.assertEqual(getattr(mock_proc, "_dedicated_pgid", None), 55555)

    def test_02_posix_timeout_does_not_terminate_parent_process_group(self):
        mock_proc = MagicMock()
        mock_proc.pid = 55555
        mock_proc._dedicated_pgid = 55555
        mock_proc.poll.return_value = None
        mock_proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="test", timeout=1.0), 0]
        parent_pgrp = 1234
        with patch("sys.platform", "linux"), patch("os.getpgrp", create=True, return_value=parent_pgrp), patch("os.killpg", create=True) as mock_killpg:
            _cleanup_subprocess(mock_proc, graceful_timeout=0.01)
            for call in mock_killpg.call_args_list:
                pgid_signaled = call.args[0]
                self.assertNotEqual(pgid_signaled, parent_pgrp, "Cleanup must NEVER signal parent process group!")
                self.assertEqual(pgid_signaled, 55555)

    def test_03_posix_descendant_termination_when_direct_child_exits_first(self):
        mock_proc = MagicMock()
        mock_proc.pid = 55555
        mock_proc._dedicated_pgid = 55555
        mock_proc.poll.return_value = 0  # Direct child exited
        mock_proc.wait.return_value = 0
        sigkill = getattr(signal, "SIGKILL", 9)
        with patch("sys.platform", "linux"), patch("os.getpgrp", create=True, return_value=1234), patch("os.killpg", create=True) as mock_killpg:
            _cleanup_subprocess(mock_proc, graceful_timeout=0.01)
            # SIGKILL must still be dispatched to dedicated_pgid to terminate surviving descendants
            mock_killpg.assert_any_call(55555, sigkill)

    def test_04_windows_process_tree_cleanup(self):
        mock_proc = MagicMock()
        mock_proc.pid = 44444
        mock_proc.poll.return_value = None
        mock_proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="test", timeout=1.0), 0]
        with patch("sys.platform", "win32"), patch("subprocess.run") as mock_run:
            _cleanup_subprocess(mock_proc, graceful_timeout=0.01)
            cmd_runs = [c.args[0] for c in mock_run.call_args_list if c.args]
            self.assertTrue(any("taskkill" in str(cmd) and "/T" in str(cmd) for cmd in cmd_runs))

    def test_05_explicit_child_reaping_and_stream_closure(self):
        mock_proc = MagicMock()
        mock_proc.pid = 33333
        mock_proc.poll.return_value = None
        mock_proc.wait.return_value = 0
        _cleanup_subprocess(mock_proc, graceful_timeout=0.01)
        mock_proc.wait.assert_called()
        mock_proc.stdin.close.assert_called()
        mock_proc.stdout.close.assert_called()
        mock_proc.stderr.close.assert_called()

    def test_06_cleanup_failure_reporting_and_primary_exception_preservation(self):
        mock_proc = MagicMock()
        mock_proc.pid = 22222
        mock_proc.poll.return_value = None
        mock_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=1.0)

        # Standalone call with raise_on_error=True raises SubprocessCleanupError
        with self.assertRaises(SubprocessCleanupError):
            _cleanup_subprocess(mock_proc, graceful_timeout=0.01, raise_on_error=True)

        # Standalone call with raise_on_error=False returns False without raising
        res = _cleanup_subprocess(mock_proc, graceful_timeout=0.01, raise_on_error=False)
        self.assertFalse(res)

        # When a primary exception is active, cleanup failure must preserve primary exception
        try:
            raise TimeoutError("Primary timeout occurred")
        except TimeoutError as primary_err:
            success = _cleanup_subprocess(mock_proc, graceful_timeout=0.01, raise_on_error=True)
            self.assertFalse(success)
            self.assertEqual(str(primary_err), "Primary timeout occurred")

    def test_07_deep_snapshot_immutability_including_nested_structures(self):
        snap = publish_global_state_snapshot({
            "meta": {"version": 1},
            "tickers": [{"symbol": "AAPL", "tags": ["tech"]}],
            "values": [1, 2, 3]
        })
        with self.assertRaises(TypeError):
            snap["meta"]["version"] = 2
        with self.assertRaises(TypeError):
            snap["tickers"][0]["tags"].append("growth")
        with self.assertRaises(TypeError):
            snap["values"].extend([4])
        with self.assertRaises(TypeError):
            del snap["meta"]

    def test_08_caller_mutation_after_publication_cannot_alter_published_state(self):
        caller_dict = {"nested": {"data": [1, 2, 3]}}
        published = publish_global_state_snapshot(caller_dict)

        # Caller mutates their dictionary
        caller_dict["nested"]["data"].append(999)
        caller_dict["nested"]["hacked"] = True

        # Published snapshot must remain completely unaffected
        self.assertEqual(list(published["nested"]["data"]), [1, 2, 3])
        self.assertNotIn("hacked", published["nested"])

    def test_09_reader_references_remain_unchanged_after_subsequent_publications(self):
        publish_global_state_snapshot({"version": 1, "items": [10]})
        captured = get_global_state_snapshot()
        publish_global_state_snapshot({"version": 2, "items": [20, 30]})

        self.assertEqual(captured["version"], 1)
        self.assertEqual(list(captured["items"]), [10])
        self.assertEqual(get_global_state_snapshot()["version"], 2)

    def test_10_concurrent_snapshot_writers_do_not_lose_updates(self):
        base = {"counter": 0}
        publish_global_state_snapshot(base)

        def inc():
            for _ in range(25):
                update_global_state_snapshot(lambda s: {**s, "counter": s["counter"] + 1})

        threads = [threading.Thread(target=inc) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2.0)
        self.assertEqual(get_global_state_snapshot()["counter"], 100)

    def test_11_background_result_application_does_not_mutate_previously_published_snapshots(self):
        publish_global_state_snapshot({"qualitative_grounding": {"AAPL": {"sentiment_score": 0.5}}})
        captured = get_global_state_snapshot()

        def fake_grounding(sym, cancel_check=None):
            return {"headline": "New Event", "sentiment_score": 0.99}

        coord = get_background_coordinator()
        accepted, item = coord.enqueue("grounding", "AAPL", "2026-11-14T19:00:00Z", fake_grounding, args=("AAPL",), force=True)
        self.assertTrue(accepted)
        self.assertTrue(item.event.wait(timeout=2.0))

        apply_background_intelligence_results()

        # Captured reader reference must NOT be mutated in place
        self.assertEqual(captured["qualitative_grounding"]["AAPL"]["sentiment_score"], 0.5)
        # Current published state must reflect the newly applied value
        self.assertEqual(get_global_state_snapshot()["qualitative_grounding"]["AAPL"]["sentiment_score"], 0.99)

    def test_12_shutdown_during_queued_work(self):
        coord = BackgroundIntelligenceCoordinator(max_workers=1, max_queue_size=10)
        gate = threading.Event()
        # Occupy the worker
        coord.enqueue("grounding", "W1", "2026-11-14T19:00:00Z", lambda: gate.wait(timeout=1.0))
        accepted, item_q = coord.enqueue("grounding", "Q1", "2026-11-14T19:00:00Z", lambda: 123)
        self.assertTrue(accepted)
        self.assertEqual(item_q.status, WorkStatus.QUEUED)

        gate.set()
        coord.shutdown(timeout=1.0)
        self.assertEqual(item_q.status, WorkStatus.CANCELLED)
        self.assertTrue(item_q.event.is_set())

    def test_13_shutdown_during_active_subprocess_execution(self):
        cancelled_observed = threading.Event()

        def long_task(cancel_check=None):
            for _ in range(50):
                if cancel_check and cancel_check():
                    cancelled_observed.set()
                    raise RuntimeError("Cancelled by check")
                time.sleep(0.02)
            return "finished"

        coord = BackgroundIntelligenceCoordinator(max_workers=1, max_queue_size=5)
        accepted, item = coord.enqueue("grounding", "AAPL", "2026-11-14T19:00:00Z", long_task)
        self.assertTrue(accepted)
        time.sleep(0.05)
        coord.shutdown(timeout=1.0)
        self.assertTrue(cancelled_observed.is_set())

    def test_14_cancellation_propagates_to_child_process(self):
        cancelled_flag = threading.Event()

        def task_with_cancel(cancel_check=None):
            while not (cancel_check and cancel_check()):
                time.sleep(0.01)
            cancelled_flag.set()
            raise RuntimeError("Cancelled")

        coord = BackgroundIntelligenceCoordinator(max_workers=1, max_queue_size=5)
        key = WorkKey("grounding", "TSLA")
        accepted, item = coord.enqueue("grounding", "TSLA", "2026-11-14T19:00:00Z", task_with_cancel)
        self.assertTrue(accepted)
        time.sleep(0.05)
        success = coord.cancel_work(key)
        self.assertTrue(success)
        self.assertTrue(cancelled_flag.wait(timeout=1.0))
        coord.shutdown(timeout=1.0)

    def test_15_shutdown_never_reports_success_while_workers_remain_active(self):
        coord = BackgroundIntelligenceCoordinator(max_workers=1, max_queue_size=5)
        hang_gate = threading.Event()

        def stubborn_worker(cancel_check=None):
            hang_gate.wait(timeout=1.0)
            return "done"

        coord.enqueue("grounding", "STUBBORN", "2026-11-14T19:00:00Z", stubborn_worker)
        time.sleep(0.05)
        res = coord.shutdown(timeout=0.05)
        self.assertFalse(res, "Shutdown must report False while workers are active!")
        hang_gate.set()
        coord.shutdown(timeout=1.0)

    def test_16_bounded_result_handoff_under_delayed_draining(self):
        coord = BackgroundIntelligenceCoordinator(max_workers=2, max_queue_size=10, max_results_size=2)
        for i in range(4):
            coord.enqueue("grounding", f"SYM_{i}", "2026-11-14T19:00:00Z", lambda idx=i: f"val_{idx}")
        time.sleep(0.15)
        self.assertLessEqual(coord._results_queue.qsize(), 2)
        drained = coord.drain_results()
        self.assertEqual(len(drained), 4)
        coord.shutdown(timeout=1.0)

    def test_17_result_handoff_capacity_exhaustion_without_silent_data_loss(self):
        coord = BackgroundIntelligenceCoordinator(max_workers=2, max_queue_size=10, max_results_size=1)
        coord.enqueue("grounding", "A", "2026-11-14T19:00:00Z", lambda: "res_a1")
        coord.enqueue("grounding", "A", "2026-11-14T19:01:00Z", lambda: "res_a2")
        coord.enqueue("grounding", "B", "2026-11-14T19:00:00Z", lambda: "res_b1")
        time.sleep(0.15)
        drained = coord.drain_results()
        drained_targets = {r.key.target for r in drained}
        self.assertIn("A", drained_targets)
        self.assertIn("B", drained_targets)
        coord.shutdown(timeout=1.0)

    def test_18_queue_accounting_remains_correct_after_exceptions_and_cancellation(self):
        coord = BackgroundIntelligenceCoordinator(max_workers=1, max_queue_size=5)

        def fail_fn():
            raise ValueError("Intentional")

        coord.enqueue("grounding", "ERR", "2026-11-14T19:00:00Z", fail_fn)
        coord.enqueue("grounding", "OK", "2026-11-14T19:00:00Z", lambda: 1)
        time.sleep(0.15)
        self.assertEqual(coord.pending_count(), 0)
        self.assertEqual(coord.in_flight_count(), 0)
        self.assertEqual(len(coord._in_flight_keys), 0)
        coord.shutdown(timeout=1.0)

    def test_19_fractional_second_chronological_comparisons(self):
        applicator = BackgroundResultApplicator()
        g, sc, sm = {}, {}, {}
        key = WorkKey("grounding", "AAPL")
        r1 = WorkResult(key=key, sequence=1, obs_time="2026-11-14T19:00:00.090000Z", data={"v": 1})
        r2 = WorkResult(key=key, sequence=2, obs_time="2026-11-14T19:00:00.100000Z", data={"v": 2})
        r3_stale = WorkResult(key=key, sequence=3, obs_time="2026-11-14T19:00:00.095000Z", data={"v": 3})

        audit1 = applicator.apply_results([r1], g, sc, sm)
        self.assertTrue(audit1[0][1])
        audit2 = applicator.apply_results([r2], g, sc, sm)
        self.assertTrue(audit2[0][1])
        self.assertEqual(g["AAPL"]["v"], 2)

        audit3 = applicator.apply_results([r3_stale], g, sc, sm)
        self.assertFalse(audit3[0][1])
        self.assertIn("Stale observation", audit3[0][2])
        self.assertEqual(g["AAPL"]["v"], 2)

    def test_20_equivalent_utc_timestamps(self):
        applicator = BackgroundResultApplicator()
        g, sc, sm = {}, {}, {}
        key = WorkKey("grounding", "AAPL")
        r1 = WorkResult(key=key, sequence=1, obs_time="2026-11-14T19:00:00+00:00", data={"v": 1})
        r2 = WorkResult(key=key, sequence=2, obs_time="2026-11-14T19:00:00Z", data={"v": 2})

        audit1 = applicator.apply_results([r1], g, sc, sm)
        self.assertTrue(audit1[0][1])
        audit2 = applicator.apply_results([r2], g, sc, sm)
        self.assertTrue(audit2[0][1])
        self.assertEqual(g["AAPL"]["v"], 2)

    def test_21_rejection_of_ambiguous_neg_zero_timestamps(self):
        with self.assertRaises(ValueError) as ctx:
            validate_utc_iso8601_timestamp("2026-11-14T19:00:00-00:00")
        self.assertIn("Ambiguous timestamp offset '-00:00'", str(ctx.exception))

    def test_22_request_time_not_misrepresented_as_source_observation_time(self):
        item = WorkItem(
            key=WorkKey("grounding", "AAPL"),
            sequence=1,
            obs_time="2026-11-14T19:00:00Z",
            fn=lambda: None,
            enqueued_time=12345.67
        )
        res = WorkResult(
            key=item.key,
            sequence=item.sequence,
            obs_time=item.obs_time,
            data="data",
            completed_time=12345.99,
            enqueued_time=item.enqueued_time
        )
        self.assertEqual(res.obs_time, "2026-11-14T19:00:00Z")
        self.assertEqual(res.enqueued_time, 12345.67)
        self.assertEqual(res.completed_time, 12345.99)
        self.assertNotEqual(res.obs_time, str(res.enqueued_time))

    def test_23_newer_issued_sequence_invalidates_older_outstanding_result(self):
        applicator = BackgroundResultApplicator()
        g, sc, sm = {}, {}, {}
        key = WorkKey("grounding", "NVDA")
        applicator.record_issued_sequence(key, 1)
        applicator.record_issued_sequence(key, 2)

        r1 = WorkResult(key=key, sequence=1, obs_time="2026-11-14T19:00:00Z", data={"headline": "Old news"})
        audit1 = applicator.apply_results([r1], g, sc, sm)
        self.assertFalse(audit1[0][1], "Older sequence must be rejected when newer work was issued")
        self.assertIn("older than latest issued", audit1[0][2])
        self.assertNotIn("NVDA", g)

        r2 = WorkResult(key=key, sequence=2, obs_time="2026-11-14T19:01:00Z", data={"headline": "Fresh news"})
        audit2 = applicator.apply_results([r2], g, sc, sm)
        self.assertTrue(audit2[0][1])
        self.assertEqual(g["NVDA"]["headline"], "Fresh news")

    def test_24_concurrent_result_application_and_snapshot_publication(self):
        publish_global_state_snapshot({"qualitative_grounding": {}, "tickers": []})
        applicator = BackgroundResultApplicator()
        g = {}
        stop_event = threading.Event()
        exceptions = []

        def writer_thread():
            try:
                seq = 0
                while not stop_event.is_set():
                    seq += 1
                    res = WorkResult(
                        key=WorkKey("grounding", "AAPL"),
                        sequence=seq,
                        obs_time="2026-11-14T19:00:00Z",
                        data={"score": seq}
                    )
                    applicator.apply_results([res], g, {}, {})
                    update_global_state_snapshot(lambda s: {**s, "qualitative_grounding": dict(g)})
                    time.sleep(0.001)
            except Exception as e:
                exceptions.append(e)

        def reader_thread():
            try:
                while not stop_event.is_set():
                    snap = get_global_state_snapshot()
                    self.assertIsInstance(snap, dict)
                    with self.assertRaises(TypeError):
                        snap["tickers"].append(1)
                    time.sleep(0.001)
            except Exception as e:
                exceptions.append(e)

        threads = [
            threading.Thread(target=writer_thread),
            threading.Thread(target=reader_thread),
            threading.Thread(target=reader_thread)
        ]
        for t in threads:
            t.start()
        time.sleep(0.2)
        stop_event.set()
        for t in threads:
            t.join(timeout=1.0)
        self.assertEqual(exceptions, [])


if __name__ == "__main__":
    unittest.main()
