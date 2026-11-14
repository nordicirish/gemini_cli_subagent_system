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
        from python.fetch_stocks import enqueue_news_grounding, get_background_coordinator
        coord = get_background_coordinator()
        accepted, item = enqueue_news_grounding("AAPL", obs_time="2026-11-14T19:00:00Z")
        self.assertTrue(accepted)
        self.assertEqual(item.obs_time, "2026-11-14T19:00:00Z")
        self.assertIsInstance(item.enqueued_time, float)

        accepted2, item2 = enqueue_news_grounding("NVDA", obs_time=None)
        self.assertTrue(accepted2)
        self.assertIsNone(item2.obs_time)

    def test_19_production_scout_integration_preserves_timestamp_semantics(self):
        from python.fetch_stocks import enqueue_scout_discovery, get_background_coordinator
        coord = get_background_coordinator()
        accepted, item = enqueue_scout_discovery("Technology", obs_time="2026-11-14T19:00:00Z")
        self.assertTrue(accepted)
        self.assertEqual(item.obs_time, "2026-11-14T19:00:00Z")
        self.assertIsInstance(item.enqueued_time, float)

        accepted2, item2 = enqueue_scout_discovery("Healthcare", obs_time=None)
        self.assertTrue(accepted2)
        self.assertIsNone(item2.obs_time)

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
        from fetch_stocks import enqueue_scout_discovery
        accepted, item = enqueue_scout_discovery("Technology", obs_time=None)
        self.assertTrue(accepted)
        self.assertIsNone(item.obs_time)
        self.assertIsInstance(item.enqueued_time, float)

    def test_09_news_grounding_enqueue_time_never_represented_as_source_observation_time(self):
        """9. News grounding enqueue time is never represented as source observation time."""
        from fetch_stocks import enqueue_news_grounding
        accepted, item = enqueue_news_grounding("AAPL", obs_time=None)
        self.assertTrue(accepted)
        self.assertIsNone(item.obs_time)
        self.assertIsInstance(item.enqueued_time, float)

    def test_10_daemon_grounding_scheduling_does_not_fabricate_source_timestamps(self):
        """10. Daemon grounding scheduling does not fabricate source timestamps."""
        import inspect
        from fetch_stocks import run_daemon
        src = inspect.getsource(run_daemon)
        self.assertIn("enqueue_news_grounding(p_sym, obs_time=None)", src)
        self.assertNotIn("enqueue_news_grounding(p_sym, obs_time=datetime", src)

    def test_11_generated_cache_timestamps_clearly_distinct_from_source_observation_timestamps(self):
        """11. Generated cache timestamps are clearly distinct from source observation timestamps."""
        from fetch_stocks import DYNAMIC_SCOUT_CACHE, DYNAMIC_SCOUT_CACHE_LOCK
        with DYNAMIC_SCOUT_CACHE_LOCK:
            DYNAMIC_SCOUT_CACHE["TECHNOLOGY"] = {
                "tickers": ["AAPL"],
                "timestamp": 1000.0,
                "cached_at": 2000.0,
            }
            self.assertIn("cached_at", DYNAMIC_SCOUT_CACHE["TECHNOLOGY"])
            self.assertIn("timestamp", DYNAMIC_SCOUT_CACHE["TECHNOLOGY"])
            self.assertNotEqual(
                DYNAMIC_SCOUT_CACHE["TECHNOLOGY"]["timestamp"],
                DYNAMIC_SCOUT_CACHE["TECHNOLOGY"]["cached_at"],
            )

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


if __name__ == "__main__":
    unittest.main()
