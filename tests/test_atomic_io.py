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

    # --- F1: Safe Handling of Corrupted JSON & Safe Recovery ---

    def test_f1_missing_file_initializes_with_default_factory(self):
        """F1.1: A file that does not exist must be initialized using default_factory and updated normally."""
        missing_file = os.path.join(self.temp_dir.name, "brand_new_state.json")
        self.assertFalse(os.path.exists(missing_file))

        res = atomic_update_json(
            missing_file,
            lambda d: {"count": d.get("count", 0) + 1, "initialized": True},
            default_factory=dict,
        )
        self.assertTrue(os.path.exists(missing_file))
        self.assertEqual(res, {"count": 1, "initialized": True})

        on_disk = atomic_read_json(missing_file)
        self.assertEqual(on_disk, {"count": 1, "initialized": True})

    def test_f1_valid_existing_json_updates_correctly(self):
        """F1: Valid existing JSON is read and updated under lock without issue."""
        valid_file = os.path.join(self.temp_dir.name, "valid_existing.json")
        atomic_write_json(valid_file, {"users": ["alice"]})

        res = atomic_update_json(
            valid_file,
            lambda d: {"users": d["users"] + ["bob"]},
            default_factory=dict,
        )
        self.assertEqual(res, {"users": ["alice", "bob"]})

        on_disk = atomic_read_json(valid_file)
        self.assertEqual(on_disk, {"users": ["alice", "bob"]})

    def test_f1_malformed_json_raises_value_error_and_preserves_original_bytes(self):
        """F1.2 & F1.4: Malformed existing JSON must raise ValueError and preserve original file bytes unchanged."""
        corrupt_file = os.path.join(self.temp_dir.name, "corrupt_data.json")
        raw_bad_json = b'{"portfolio": [{"ticker": "SPY", "shares": 10}], "broken_syntax": '
        with open(corrupt_file, "wb") as f:
            f.write(raw_bad_json)

        # Attempt to update the corrupted file
        with self.assertRaises(ValueError) as ctx:
            atomic_update_json(
                corrupt_file,
                lambda d: {"portfolio": [], "reset": True},
                default_factory=dict,
            )

        self.assertIn("Failed to parse existing JSON file", str(ctx.exception))
        self.assertIn("Original file preserved", str(ctx.exception))

        # Original file must remain byte-for-byte identical
        with open(corrupt_file, "rb") as f:
            current_bytes = f.read()
        self.assertEqual(current_bytes, raw_bad_json)

    def test_f1_existing_empty_file_fails_safely_and_preserves_file(self):
        """F1.3 & F1.4: An existing empty file (0 bytes) must fail safely and not be silently replaced."""
        empty_file = os.path.join(self.temp_dir.name, "empty_zero_byte.json")
        with open(empty_file, "wb") as f:
            f.write(b"")

        with self.assertRaises(ValueError) as ctx:
            atomic_update_json(
                empty_file,
                lambda d: {"portfolio": [{"ticker": "AAPL"}]},
                default_factory=dict,
            )

        self.assertIn("empty", str(ctx.exception).lower())
        self.assertIn("Refusing to silently overwrite", str(ctx.exception))

        # Empty file must not be modified or replaced
        self.assertEqual(os.path.getsize(empty_file), 0)

    def test_f1_invalid_update_result_preserves_original_file(self):
        """F1.4: An invalid update result (unserializable or failing validate_fn) preserves the original file."""
        test_file = os.path.join(self.temp_dir.name, "test_preserve.json")
        initial_data = {"key": "original_safe_value"}
        atomic_write_json(test_file, initial_data)
        with open(test_file, "rb") as f:
            original_bytes = f.read()

        # 1. Unserializable object from update_fn
        class UnserializableType:
            pass

        with self.assertRaises(ValueError):
            atomic_update_json(
                test_file,
                lambda d: {"corrupted": UnserializableType()},
            )

        with open(test_file, "rb") as f:
            self.assertEqual(f.read(), original_bytes)

        # 2. validate_fn rejection
        with self.assertRaises(ValueError):
            atomic_update_json(
                test_file,
                lambda d: {"key": "invalid_value"},
                validate_fn=lambda d: d.get("key") == "original_safe_value",
            )

        with open(test_file, "rb") as f:
            self.assertEqual(f.read(), original_bytes)

    def test_f1_read_error_fails_safely_and_preserves_file(self):
        """F1: Permission/read errors raise IOError and preserve the original file."""
        from unittest.mock import patch
        test_file = os.path.join(self.temp_dir.name, "unreadable.json")
        atomic_write_json(test_file, {"data": 123})
        with open(test_file, "rb") as f:
            original_bytes = f.read()

        import builtins
        orig_open = builtins.open

        def selective_open(path, *args, **kwargs):
            # Only fail on the target data file read, allowing .lock and temp files to open
            if str(path) == os.path.abspath(test_file) and (not args or "r" in args[0] or kwargs.get("mode", "r") == "r"):
                raise PermissionError("Mocked read access denied")
            return orig_open(path, *args, **kwargs)

        with patch("builtins.open", side_effect=selective_open):
            with self.assertRaises(IOError) as ctx:
                atomic_update_json(
                    test_file,
                    lambda d: {"data": 456},
                )
            self.assertIn("Failed to read existing file", str(ctx.exception))

        with open(test_file, "rb") as f:
            self.assertEqual(f.read(), original_bytes)

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
