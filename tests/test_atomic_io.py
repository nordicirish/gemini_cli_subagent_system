"""
Unit & Integration Tests for Atomic Persistence and Concurrency Protection
Governed by Stage 1.1 (R6), Protocol 02, and Stage 1 Section 3.3.
"""

import os
import sys
import unittest
import threading
import tempfile
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from atomic_io import (
    atomic_write_json,
    atomic_read_json,
    atomic_update_json,
    atomic_write_text,
    FileLock,
    FileLockTimeoutError,
)


class TestAtomicIO(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_json_path = os.path.join(self.temp_dir.name, "test_state.json")
        self.test_text_path = os.path.join(self.temp_dir.name, "test_rules.md")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_atomic_write_and_read_json(self):
        data = {"portfolio": [{"ticker": "SPY", "shares": 50}], "cash": 10000.0}
        success = atomic_write_json(self.test_json_path, data)
        self.assertTrue(success)
        self.assertTrue(os.path.exists(self.test_json_path))

        read_data = atomic_read_json(self.test_json_path)
        self.assertEqual(read_data, data)

    def test_read_non_existent_file_returns_default(self):
        non_existent = os.path.join(self.temp_dir.name, "does_not_exist.json")
        res = atomic_read_json(non_existent, default={"default": True})
        self.assertEqual(res, {"default": True})

    def test_unserializable_data_aborts_without_touching_disk(self):
        # Create an existing valid file
        initial_data = {"intact": True}
        atomic_write_json(self.test_json_path, initial_data)

        # Attempt to write an object that cannot be serialized to JSON
        class Unserializable:
            pass

        bad_data = {"bad": Unserializable()}
        with self.assertRaises(ValueError):
            atomic_write_json(self.test_json_path, bad_data)

        # Verify existing file is preserved intact
        current_data = atomic_read_json(self.test_json_path)
        self.assertEqual(current_data, initial_data)

    def test_validation_function_rejection_preserves_existing_data(self):
        initial_data = {"version": 1, "status": "VALID"}
        atomic_write_json(self.test_json_path, initial_data)

        def validator(d):
            return d.get("status") == "VALID"

        # Attempt to write invalid data
        invalid_data = {"version": 2, "status": "INVALID_CORRUPTED"}
        with self.assertRaises(ValueError):
            atomic_write_json(self.test_json_path, invalid_data, validate_fn=validator)

        # Existing file remains untouched
        current_data = atomic_read_json(self.test_json_path)
        self.assertEqual(current_data, initial_data)

    def test_atomic_write_text(self):
        content = "# Markdown Rules\n- Rule 1: Always preserve capital."
        success = atomic_write_text(self.test_text_path, content)
        self.assertTrue(success)

        with open(self.test_text_path, "r", encoding="utf-8") as f:
            read_text = f.read()
        self.assertEqual(read_text, content)

    def test_concurrent_atomic_writes(self):
        """Simulate concurrent threads writing to the same file."""
        errors = []
        threads = []

        def worker(thread_id):
            try:
                for i in range(10):
                    data = {"thread_id": thread_id, "iteration": i}
                    atomic_write_json(self.test_json_path, data)
            except Exception as e:
                errors.append(e)

        for tid in range(4):
            t = threading.Thread(target=worker, args=(tid,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Concurrent write errors encountered: {errors}")
        # Verify file is not corrupt and can be cleanly parsed
        final_data = atomic_read_json(self.test_json_path)
        self.assertIn("thread_id", final_data)
        self.assertIn("iteration", final_data)

    def test_r6_concurrent_read_modify_write_zero_lost_updates(self):
        """R6: Concurrent read-modify-write via atomic_update_json must preserve 100% of appended entries."""
        rmw_path = os.path.join(self.temp_dir.name, "concurrent_lessons.json")
        atomic_write_json(rmw_path, [])

        num_threads = 5
        updates_per_thread = 10
        errors = []
        threads = []

        def rmw_worker(tid):
            try:
                for idx in range(updates_per_thread):
                    entry_id = f"t{tid}-i{idx}"

                    def _append_entry(current_list):
                        if not isinstance(current_list, list):
                            current_list = []
                        return current_list + [{"id": entry_id}]

                    atomic_update_json(rmw_path, _append_entry, default_factory=list)
            except Exception as e:
                errors.append(e)

        for tid in range(num_threads):
            t = threading.Thread(target=rmw_worker, args=(tid,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Concurrent RMW errors: {errors}")
        final_list = atomic_read_json(rmw_path)
        self.assertIsInstance(final_list, list)
        self.assertEqual(
            len(final_list),
            num_threads * updates_per_thread,
            f"Expected {num_threads * updates_per_thread} entries with zero lost updates, got {len(final_list)}",
        )

    def test_r6_same_thread_reentrant_lock_no_deadlock(self):
        """R6: Acquiring FileLock or calling atomic_read_json/atomic_write_json inside an active FileLock on the same thread must not deadlock."""
        reentrant_path = os.path.join(self.temp_dir.name, "reentrant.json")
        lock_path = reentrant_path + ".lock"
        with FileLock(lock_path, timeout=1.0):
            with FileLock(lock_path, timeout=1.0):
                atomic_write_json(reentrant_path, {"step": 1}, timeout=1.0)
                read_back = atomic_read_json(reentrant_path, timeout=1.0)
                self.assertEqual(read_back, {"step": 1})

    def test_r6_cross_thread_lock_timeout(self):
        """R6: A second thread attempting to acquire a held FileLock must time out cleanly."""
        lock_target = os.path.join(self.temp_dir.name, "contended.json")
        lock_acquired_event = threading.Event()
        release_event = threading.Event()

        def holder():
            with FileLock(lock_target, timeout=2.0):
                lock_acquired_event.set()
                release_event.wait(timeout=2.0)

        t = threading.Thread(target=holder)
        t.start()
        lock_acquired_event.wait(timeout=1.0)

        try:
            with self.assertRaises(FileLockTimeoutError):
                with FileLock(lock_target, timeout=0.15):
                    pass
        finally:
            release_event.set()
            t.join()


if __name__ == "__main__":
    unittest.main()
