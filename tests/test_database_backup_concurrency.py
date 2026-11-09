"""
Stage 1.5-A.2.1 Concurrency, Locking Guarantees and Exception Integrity Tests
Governed by Stage 1.5-A.2.1 Specifications (R1, R2, R3, R4, R5).

Verifies:
- OS-level locking semantics (Win32 LockFileEx / CreateFileW, POSIX flock / restriction)
- Target quiescence against idle connections, active readers, and active writers
- Cross-process reader-writer coordination between DatabaseManager.connect() and restore_backup()
- Mutual exclusion of multiple simultaneous restoration attempts
- Direct sqlite3.connect() bypassing DatabaseManager
- Process crash / termination automatic lock recovery (stale lock freedom)
- Non-silent resource cleanup and failure propagation
- New target restoration safety
"""

import os
import sys
import time
import ctypes
import sqlite3
import tempfile
import unittest
import threading
import subprocess
from unittest.mock import patch, MagicMock

from python.database.manager import (
    DatabaseManager,
    PRIMARY_FINANCIAL_PROFILE,
    DatabaseBusyError,
    DatabaseError,
)
from python.database.schema import SchemaMigrator
from python.database.backup import (
    BackupManager,
    BackupResult,
    BackupError,
    BackupQuiescenceError,
    BackupRecoveryError,
    BackupCleanupError,
    RestoreLockBusyError,
    DatabaseRestoreLock,
    _acquire_exclusive_target_lock,
    _close_exclusive_target_lock,
)
from python.database.repositories.unit_of_work import UnitOfWork
from python.database.repositories.base import TransactionIntegrityError


class FaultyCloseConnection(sqlite3.Connection):
    """Subclass of sqlite3.Connection that can inject close failure for testing."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.allow_close = False

    def close(self):
        if not self.allow_close:
            raise sqlite3.OperationalError("Injected connection close failure")
        super().close()


class FaultyBackupConnection(sqlite3.Connection):
    """Subclass of sqlite3.Connection that injects failure into backup()."""

    def backup(self, *args, **kwargs):
        raise sqlite3.OperationalError("Disk I/O error during backup")


class TestStage15A21BackupLockingAndConcurrency(unittest.TestCase):
    """Rigorous verification of Stage 1.5-A.2.1 locking guarantees and exception integrity."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.src_path = os.path.join(self.temp_dir.name, "source.db")
        self.backup_path = os.path.join(self.temp_dir.name, "source.bak")

        # Initialize source database with valid financial schema and data
        src_mgr = DatabaseManager(self.src_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with src_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute(
                "INSERT INTO portfolio_cash (account_id, currency, amount, last_updated_at_utc) "
                "VALUES ('PRIMARY', 'EUR', '125000.50', '2026-11-09T12:00:00Z');"
            )
            conn.execute(
                "INSERT INTO portfolio_positions (account_id, ticker, shares, wac_eur, last_updated_at_utc) "
                "VALUES ('PRIMARY', 'AAPL', '100', '150.00', '2026-11-09T12:00:00Z');"
            )

        # Create verified source backup snapshot
        BackupManager.create_backup(self.src_path, self.backup_path)

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    # ---------------------------------------------------------------------------
    # R1: POSIX Database Exclusion & Existing Target Restriction
    # ---------------------------------------------------------------------------

    def test_r1_posix_existing_target_restoration_strictly_refused(self):
        """R1: Restoration over an existing target on POSIX is refused fail-closed (Option B)."""
        target_path = os.path.join(self.temp_dir.name, "target_posix.db")
        with open(target_path, "w") as f:
            f.write("existing db content")

        with patch("sys.platform", "linux"):
            with self.assertRaises(BackupQuiescenceError) as ctx:
                BackupManager.restore_backup(self.backup_path, target_path)
            self.assertIn("not supported on POSIX", str(ctx.exception))
            self.assertIn("operating system advisory locking cannot guarantee", str(ctx.exception))

    def test_r1_posix_new_target_restoration_supported(self):
        """R1: Restoration to a non-existent target path on POSIX is safe and supported."""
        target_path = os.path.join(self.temp_dir.name, "target_posix_new.db")
        self.assertFalse(os.path.exists(target_path))

        mock_fcntl = MagicMock()
        mock_fcntl.LOCK_SH = 1
        mock_fcntl.LOCK_NB = 4
        mock_fcntl.LOCK_EX = 2
        mock_fcntl.LOCK_UN = 8

        with patch("sys.platform", "linux"), patch.dict("sys.modules", {"fcntl": mock_fcntl}):
            res = BackupManager.restore_backup(self.backup_path, target_path)
            self.assertEqual(res.integrity_status, "ok")
            self.assertTrue(os.path.exists(target_path))

    # ---------------------------------------------------------------------------
    # R2: Windows Exclusive-Handle Semantics & Quiescence Checks
    # ---------------------------------------------------------------------------

    @unittest.skipUnless(sys.platform == "win32", "Windows-specific VFS handle semantics")
    def test_r2_windows_rejects_existing_idle_sqlite_connection(self):
        """R2.1: Target held open by idle SQLite connection is rejected fail-closed with WinError 32."""
        target_path = os.path.join(self.temp_dir.name, "target_idle.db")
        target_mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with target_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)

        # Open connection and leave it idle (no active query or transaction)
        idle_conn = sqlite3.connect(target_path, autocommit=True)
        try:
            with self.assertRaises(BackupQuiescenceError) as ctx:
                BackupManager.restore_backup(self.backup_path, target_path)
            self.assertIn("Held open by active or idle connection", str(ctx.exception))
            self.assertIn("32", str(ctx.exception))
        finally:
            idle_conn.close()

        # After closing, restoration succeeds
        res = BackupManager.restore_backup(self.backup_path, target_path)
        self.assertEqual(res.integrity_status, "ok")

    @unittest.skipUnless(sys.platform == "win32", "Windows-specific VFS handle semantics")
    def test_r2_windows_rejects_active_sqlite_reader(self):
        """R2.2: Target held open by active SQLite reader transaction is rejected fail-closed."""
        target_path = os.path.join(self.temp_dir.name, "target_reader.db")
        target_mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with target_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)

        reader_conn = sqlite3.connect(target_path, autocommit=True)
        try:
            cur = reader_conn.cursor()
            cur.execute("BEGIN;")
            cur.execute("SELECT * FROM portfolio_cash;")

            with self.assertRaises(BackupQuiescenceError) as ctx:
                BackupManager.restore_backup(self.backup_path, target_path)
            self.assertTrue(
                "Held open by active or idle connection" in str(ctx.exception)
                or "locked" in str(ctx.exception)
            )
        finally:
            reader_conn.execute("ROLLBACK;")
            reader_conn.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows-specific VFS handle semantics")
    def test_r2_windows_rejects_active_sqlite_writer(self):
        """R2.3: Target held open by active SQLite writer transaction is rejected fail-closed."""
        target_path = os.path.join(self.temp_dir.name, "target_writer.db")
        target_mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with target_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)

        writer_conn = sqlite3.connect(target_path, autocommit=True)
        try:
            writer_conn.execute("BEGIN IMMEDIATE;")
            with self.assertRaises(BackupQuiescenceError) as ctx:
                BackupManager.restore_backup(self.backup_path, target_path)
            self.assertTrue(
                "Held open by active or idle connection" in str(ctx.exception)
                or "locked" in str(ctx.exception)
            )
        finally:
            writer_conn.execute("ROLLBACK;")
            writer_conn.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows-specific VFS handle semantics")
    def test_r2_direct_sqlite_connection_blocked_while_exclusive_target_lock_held(self):
        """R2.4: Direct sqlite3.connect() bypassing DatabaseManager is blocked while target lock is held."""
        target_path = os.path.join(self.temp_dir.name, "target_direct.db")
        target_mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with target_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)

        # Acquire exclusive target lock
        handle = _acquire_exclusive_target_lock(target_path)
        self.assertIsNotNone(handle)
        try:
            # Attempt direct sqlite3.connect() bypassing DatabaseManager
            with self.assertRaises(sqlite3.OperationalError) as ctx:
                direct_conn = sqlite3.connect(target_path, timeout=0.1)
                direct_conn.execute("SELECT * FROM portfolio_cash;")
            self.assertIn("unable to open database file", str(ctx.exception).lower())
        finally:
            _close_exclusive_target_lock(handle)

        # After releasing handle, direct connect succeeds
        direct_conn = sqlite3.connect(target_path)
        cur = direct_conn.cursor()
        cur.execute("SELECT COUNT(*) FROM portfolio_cash;")
        self.assertEqual(cur.fetchone()[0], 0)
        direct_conn.close()

    # ---------------------------------------------------------------------------
    # R3: Eliminate Restore-Lock Race (Coordinated Acquisition Protocol)
    # ---------------------------------------------------------------------------

    def test_r3_coordinated_reader_writer_mutual_exclusion(self):
        """R3.1: DatabaseRestoreLock coordinates shared connection gates and exclusive restore locks."""
        target_path = os.path.join(self.temp_dir.name, "target_rw_coord.db")

        # 1. Acquire shared gate 1 (simulate active DatabaseManager.connect())
        with DatabaseRestoreLock.shared_gate(target_path):
            # 2. Acquire shared gate 2 concurrently (second connection allowed)
            with DatabaseRestoreLock.shared_gate(target_path):
                # 3. Restoration attempt must fail immediately because shared locks are held
                with self.assertRaises(BackupQuiescenceError) as ctx:
                    DatabaseRestoreLock.acquire_exclusive(target_path)
                self.assertIn("Restore coordination lock is currently held", str(ctx.exception))

        # 4. Once shared locks released, exclusive restore lock succeeds
        ex_token = DatabaseRestoreLock.acquire_exclusive(target_path)
        self.assertTrue(ex_token.is_held)
        try:
            # 5. Connection attempt during restore is rejected fail-closed
            with self.assertRaises(RestoreLockBusyError):
                with DatabaseRestoreLock.shared_gate(target_path):
                    pass

            # Also verify via DatabaseManager.connect() -> raises DatabaseBusyError
            mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)
            with self.assertRaises(DatabaseBusyError) as ctx:
                mgr.connect()
            self.assertIn("currently undergoing offline backup restoration", str(ctx.exception))
        finally:
            ex_token.release(unlink_on_release=True)

        # 6. After exclusive release, shared gate succeeds again
        with DatabaseRestoreLock.shared_gate(target_path):
            pass

    def test_r3_multiple_simultaneous_restorations_rejected(self):
        """R3.2: Simultaneous restoration attempts are rejected fail-closed."""
        target_path = os.path.join(self.temp_dir.name, "target_multi_restore.db")

        # First restore acquires exclusive lock
        token1 = DatabaseRestoreLock.acquire_exclusive(target_path)
        self.assertTrue(token1.is_held)
        try:
            # Second restore attempt fails
            with self.assertRaises(BackupQuiescenceError) as ctx:
                BackupManager.restore_backup(self.backup_path, target_path)
            self.assertIn("Restore coordination lock is currently held", str(ctx.exception))
        finally:
            token1.release(unlink_on_release=True)

    def test_r3_race_connection_opening_and_restoration_concurrency(self):
        """R3.3: High-concurrency race between connection opening and restore lock acquisition."""
        target_path = os.path.join(self.temp_dir.name, "target_race.db")
        target_mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)

        # Pre-initialize target database schema so any connection reads consistent tables
        with target_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute(
                "INSERT INTO portfolio_cash (account_id, currency, amount, last_updated_at_utc) "
                "VALUES ('PRIMARY', 'EUR', '100.00', '2026-11-09T12:00:00Z');"
            )

        stop_event = threading.Event()
        connection_attempts = [0]
        blocked_by_restore = [0]
        successful_connections = [0]
        errors = []

        def connection_worker():
            while not stop_event.is_set():
                connection_attempts[0] += 1
                try:
                    conn = target_mgr.connect()
                    successful_connections[0] += 1
                    cur = conn.cursor()
                    cur.execute("SELECT amount FROM portfolio_cash WHERE account_id='PRIMARY';")
                    cur.fetchone()
                    time.sleep(0.001)
                    conn.close()
                except DatabaseBusyError:
                    blocked_by_restore[0] += 1
                except Exception as ex:
                    errors.append(ex)

        # Start concurrent connection threads
        workers = [threading.Thread(target=connection_worker) for _ in range(3)]
        for w in workers:
            w.start()

        # Perform restoration while workers are racing
        time.sleep(0.01)
        restores_completed = 0
        restores_busy = 0
        for _ in range(3):
            try:
                res = BackupManager.restore_backup(self.backup_path, target_path)
                if res.integrity_status == "ok":
                    restores_completed += 1
            except BackupQuiescenceError:
                restores_busy += 1
            time.sleep(0.005)

        stop_event.set()
        for w in workers:
            w.join()

        self.assertEqual(errors, [], f"Unexpected connection errors: {errors}")
        self.assertGreater(connection_attempts[0], 0)
        self.assertGreater(restores_completed + restores_busy, 0)

        # Final database must have verified integrity
        with target_mgr.connection() as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA integrity_check;")
            self.assertEqual(cur.fetchone()[0], "ok")
            cur.execute("SELECT amount FROM portfolio_cash WHERE account_id='PRIMARY';")
            val = str(cur.fetchone()[0])
            self.assertIn(val, ("100.00", "125000.50"))

    def test_r3_process_termination_automatic_lock_recovery(self):
        """R3.4: Operating system kernel automatically releases restore lock if process crashes."""
        target_path = os.path.join(self.temp_dir.name, "target_crash_recovery.db")

        # Launch child process that acquires exclusive lock and abruptly terminates
        child_code = f"""
import os, sys, time
from python.database.backup import DatabaseRestoreLock
token = DatabaseRestoreLock.acquire_exclusive(r"{target_path}")
print("LOCK_ACQUIRED", flush=True)
time.sleep(0.5)
# Exit abruptly without releasing token
sys.exit(0)
"""
        proc = subprocess.Popen(
            [sys.executable, "-c", child_code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            # Wait until child reports lock acquisition
            first_line = proc.stdout.readline()
            self.assertIn("LOCK_ACQUIRED", first_line)

            # While child is running, connection creation must be blocked
            mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)
            with self.assertRaises(DatabaseBusyError):
                mgr.connect()

            # Wait for child process to terminate
            proc.wait(timeout=5.0)
        finally:
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()

        # As soon as child terminates, OS kernel has released the lock
        # Next connection creation or restore must succeed without stale lock errors
        res = BackupManager.restore_backup(self.backup_path, target_path)
        self.assertEqual(res.integrity_status, "ok")

        with mgr.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT amount FROM portfolio_cash WHERE account_id='PRIMARY';")
            self.assertEqual(str(cur.fetchone()[0]), "125000.50")

    # ---------------------------------------------------------------------------
    # R4: Exception Integrity & Resource-Release Non-Suppression
    # ---------------------------------------------------------------------------

    def test_r4_restore_lock_release_failure_raises_cleanup_error(self):
        """R4.1: Failure during restore lock release raises explicit BackupCleanupError."""
        target_path = os.path.join(self.temp_dir.name, "target_lock_fail.db")
        token = DatabaseRestoreLock.acquire_exclusive(target_path)

        if sys.platform == "win32":
            with patch("ctypes.windll.kernel32.UnlockFileEx", return_value=0):
                with self.assertRaises(BackupCleanupError) as ctx:
                    token.release()
                self.assertIn("Failed to cleanly release restore lock", str(ctx.exception))
                self.assertIn("UnlockFileEx failed", str(ctx.exception))
        else:
            with patch("fcntl.flock", side_effect=OSError("Injected POSIX unlock failure")):
                with self.assertRaises(BackupCleanupError) as ctx:
                    token.release()
                self.assertIn("flock(LOCK_UN) failed", str(ctx.exception))

    @unittest.skipUnless(sys.platform == "win32", "Win32 CloseHandle verification")
    def test_r4_target_lock_closehandle_failure_raises_cleanup_error(self):
        """R4.2: CloseHandle failure in _close_exclusive_target_lock raises BackupCleanupError."""
        target_path = os.path.join(self.temp_dir.name, "target_ch_fail.db")
        with open(target_path, "w") as f:
            f.write("target db")

        h = _acquire_exclusive_target_lock(target_path)
        self.assertIsNotNone(h)

        with patch("ctypes.windll.kernel32.CloseHandle", return_value=0):
            with self.assertRaises(BackupCleanupError) as ctx:
                _close_exclusive_target_lock(h)
            self.assertIn("Failed to close exclusive target lock handle", str(ctx.exception))

        # Real close to free handle
        _close_exclusive_target_lock(h)

    def test_r4_create_backup_preserves_primary_error_and_records_cleanup_errors(self):
        """R4.3: Primary backup error is preserved without being obscured by cleanup errors."""
        dest_path = os.path.join(self.temp_dir.name, "dest_fail.db")

        # Open real connection using FaultyBackupConnection factory
        faulty_src = sqlite3.connect(self.src_path, factory=FaultyBackupConnection, autocommit=True)
        try:
            with self.assertRaises(BackupError) as ctx:
                BackupManager.create_backup(faulty_src, dest_path)
            self.assertIn("Disk I/O error during backup", str(ctx.exception))
        finally:
            faulty_src.close()

    def test_r4_unit_of_work_connection_close_failure_on_clean_exit(self):
        """R4.4: Failure to close owned connection on successful UoW exit raises TransactionIntegrityError."""
        mgr = DatabaseManager(self.src_path, profile=PRIMARY_FINANCIAL_PROFILE)
        faulty_conn = sqlite3.connect(self.src_path, factory=FaultyCloseConnection, autocommit=True)
        faulty_conn.allow_close = False

        with patch.object(mgr, "connect", return_value=faulty_conn):
            uow = UnitOfWork(db_manager=mgr)
            try:
                with self.assertRaises(TransactionIntegrityError) as ctx:
                    with uow:
                        pass
                self.assertIn("Failed to close owned database connection on UnitOfWork exit", str(ctx.exception))
                self.assertTrue(uow.is_conn_unsafe)
            finally:
                faulty_conn.allow_close = True
                faulty_conn.close()

    def test_r4_unit_of_work_connection_close_failure_during_primary_exception(self):
        """R4.5: Failure to close owned connection during primary exception does NOT mask primary exception."""
        mgr = DatabaseManager(self.src_path, profile=PRIMARY_FINANCIAL_PROFILE)
        faulty_conn = sqlite3.connect(self.src_path, factory=FaultyCloseConnection, autocommit=True)
        faulty_conn.allow_close = False

        with patch.object(mgr, "connect", return_value=faulty_conn):
            uow = UnitOfWork(db_manager=mgr)
            try:
                with self.assertRaises(ValueError) as ctx:
                    with uow:
                        raise ValueError("Application domain validation error")

                self.assertEqual(str(ctx.exception), "Application domain validation error")
                self.assertTrue(hasattr(ctx.exception, "connection_close_error"))
                self.assertIn("Injected connection close failure", str(ctx.exception.connection_close_error))
                self.assertTrue(uow.is_conn_unsafe)
            finally:
                faulty_conn.allow_close = True
                faulty_conn.close()

    def test_r4_restore_backup_cleanup_error_reported_when_restore_succeeds(self):
        """R4.6: If restoration succeeds but fallback unlink fails, BackupCleanupError is raised."""
        target_path = os.path.join(self.temp_dir.name, "target_cleanup_test.db")
        res = BackupManager.restore_backup(self.backup_path, target_path)
        self.assertEqual(res.integrity_status, "ok")

        real_unlink = os.unlink

        def faulty_unlink(path):
            if ".pre_restore_" in path:
                raise OSError("Permission denied unlinking fallback backup")
            return real_unlink(path)

        with patch("os.unlink", side_effect=faulty_unlink):
            if sys.platform == "win32":
                with self.assertRaises(BackupCleanupError) as ctx:
                    BackupManager.restore_backup(self.backup_path, target_path)
                self.assertIn("non-fatal cleanup errors occurred", str(ctx.exception))
                self.assertIn("Permission denied", str(ctx.exception))

    # ---------------------------------------------------------------------------
    # R5: End-to-End Safety Guarantees Verification
    # ---------------------------------------------------------------------------

    def test_r5_new_target_restoration_safe_and_verified(self):
        """R5.1: New-target restoration succeeds and verifies database integrity on all platforms."""
        new_target = os.path.join(self.temp_dir.name, "brand_new_restored.db")
        self.assertFalse(os.path.exists(new_target))

        res = BackupManager.restore_backup(self.backup_path, new_target)
        self.assertIsInstance(res, BackupResult)
        self.assertEqual(res.integrity_status, "ok")
        self.assertTrue(os.path.exists(new_target))

        # Verify data content
        target_mgr = DatabaseManager(new_target, profile=PRIMARY_FINANCIAL_PROFILE)
        with target_mgr.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT amount FROM portfolio_cash WHERE account_id='PRIMARY';")
            self.assertEqual(str(cur.fetchone()[0]), "125000.50")
            cur.execute("SELECT shares, wac_eur FROM portfolio_positions WHERE ticker='AAPL';")
            row = cur.fetchone()
            self.assertEqual(str(row[0]), "100")
            self.assertEqual(str(row[1]), "150.00")


if __name__ == "__main__":
    unittest.main()
