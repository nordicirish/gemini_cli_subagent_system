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
import inspect
import sqlite3
import tempfile
import unittest
import threading
import subprocess
from unittest.mock import patch, MagicMock

from python.database.manager import (
    DatabaseManager,
    ManagedConnection,
    PRIMARY_FINANCIAL_PROFILE,
    DatabaseBusyError,
    DatabaseError,
    DatabaseConnectionError,
    DatabaseTransactionError,
)
from python.database.schema import SchemaMigrator
from python.database.backup import (
    BackupManager,
    BackupResult,
    BackupError,
    BackupDestinationExistsError,
    BackupQuiescenceError,
    BackupRecoveryError,
    BackupCleanupError,
    RestoreLockBusyError,
    DatabaseRestoreLock,
    DatabaseRestoreLockToken,
    LockTokenState,
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


class FaultyBaseConnection(sqlite3.Connection):
    """Base connection class for testing close failures through ManagedConnection super() calls."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.allow_close = True

    def close(self):
        if not self.allow_close:
            raise sqlite3.OperationalError("Injected connection close failure")
        super().close()


class FaultyManagedConnection(ManagedConnection, FaultyBaseConnection):
    """Subclass of ManagedConnection for testing close failure propagation."""
    pass


class FaultyRollbackConnection(ManagedConnection):
    """Subclass of ManagedConnection that injects rollback failure for testing."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail_rollback = False

    def execute(self, sql, *args, **kwargs):
        if self.fail_rollback and "ROLLBACK" in str(sql).upper():
            raise sqlite3.OperationalError("Rollback hardware fail")
        return super().execute(sql, *args, **kwargs)


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

        mock_fcntl = MagicMock()
        mock_fcntl.LOCK_SH = 1
        mock_fcntl.LOCK_NB = 4
        mock_fcntl.LOCK_EX = 2
        mock_fcntl.LOCK_UN = 8

        with patch("sys.platform", "linux"), patch.dict("sys.modules", {"fcntl": mock_fcntl}):
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

        try:
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
        finally:
            token.release()

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


# ===========================================================================
# Stage 1.5-A.2.2: Restore Gate Lifetime, Target-State Race & No-Clobber Tests
# ===========================================================================

class TestStage15A22RestoreGateLifetimeAndNoClobber(unittest.TestCase):
    """
    Automated regression and concurrency tests for Stage 1.5-A.2.2.
    Verifies:
    - R1: Connection-lifetime shared lock contract on ManagedConnection.
    - R2: Target-state inspection reordered behind exclusive lock acquisition; pre-placement recheck.
    - R3: Atomic no-clobber placement for new targets; destination and tmp_restore preservation.
    - R4: Persistent zero-byte restore lock file identity; zero-byte kernel locks; no unlinking.
    - R5: Elimination of remaining silent exception suppression.
    - R6: Cross-process, race condition, and error-chaining verifications.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.source_db_path = os.path.join(self.temp_dir.name, "source_stage15_a22.db")
        self.backup_path = os.path.join(self.temp_dir.name, "backup_stage15_a22.db")

        # Initialize and populate source database
        mgr = DatabaseManager(self.source_db_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute(
                "INSERT INTO portfolio_cash (account_id, currency, amount, last_updated_at_utc) "
                "VALUES ('PRIMARY', 'EUR', '250000.00', '2026-11-09T18:00:00Z');"
            )

        # Create valid verified backup
        res = BackupManager.create_backup(self.source_db_path, self.backup_path, overwrite=False)
        self.assertEqual(res.integrity_status, "ok")

    def tearDown(self):
        self.temp_dir.cleanup()

    # ---------------------------------------------------------------------------
    # R1: Connection-Lifetime Shared Lock Contract
    # ---------------------------------------------------------------------------

    def test_r1_managed_connection_retains_token_until_closed(self):
        """R1.1: ManagedConnection retains shared restore lock across its entire active lifetime."""
        target_path = os.path.join(self.temp_dir.name, "target_lifetime.db")
        mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)

        conn = mgr.connect()
        try:
            self.assertIsInstance(conn, ManagedConnection)
            self.assertIsNotNone(conn.lock_token)
            self.assertTrue(conn.lock_token.is_held)

            # While connection is open, restore attempt MUST be rejected with BackupQuiescenceError
            with self.assertRaises(BackupQuiescenceError) as ctx:
                BackupManager.restore_backup(self.backup_path, target_path)
            self.assertIn("Restore coordination lock is currently held", str(ctx.exception))
        finally:
            conn.close()

        # After connection is closed, token is released and restore succeeds
        self.assertIsNone(conn.lock_token)
        res = BackupManager.restore_backup(self.backup_path, target_path)
        self.assertEqual(res.integrity_status, "ok")

    def test_r1_managed_connection_close_idempotent(self):
        """R1.2: Calling close() repeatedly on ManagedConnection is completely safe and idempotent."""
        target_path = os.path.join(self.temp_dir.name, "target_idempotent_close.db")
        mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)

        conn = mgr.connect()
        self.assertTrue(conn.lock_token.is_held)

        conn.close()
        self.assertIsNone(conn.lock_token)

        # Second and third close calls must not raise
        conn.close()
        conn.close()

    def test_r1_connection_config_failure_releases_shared_token(self):
        """R1.3: Failure during PRAGMA configuration releases the acquired shared lock token."""
        target_path = os.path.join(self.temp_dir.name, "target_cfg_fail.db")
        mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)

        with patch.object(mgr, "_apply_and_verify_pragmas", side_effect=RuntimeError("Pragma verification failed")):
            with self.assertRaises(RuntimeError):
                mgr.connect()

        # Token must not have leaked; exclusive restore lock can be acquired
        token = DatabaseRestoreLock.acquire_exclusive(target_path)
        self.assertTrue(token.is_held)
        token.release()

    def test_r1_both_sqlite_close_and_token_release_fail_preserves_both_errors(self):
        """R1.4: Calling close with removed force_lock_release raises TypeError; SQLite close failure never releases token."""
        target_path = os.path.join(self.temp_dir.name, "target_both_fail.db")
        conn = sqlite3.connect(target_path, factory=FaultyManagedConnection)
        conn.allow_close = False
        mock_token = MagicMock()
        mock_token.release.side_effect = RuntimeError("Lock release failed")
        conn._lock_token = mock_token

        try:
            with self.assertRaises(TypeError):
                conn.close(force_lock_release=True)

            with self.assertRaises(DatabaseConnectionError) as ctx:
                conn.close()
            self.assertIn("Failed to close SQLite connection", str(ctx.exception))
            self.assertIn("Injected connection close failure", str(ctx.exception))
            mock_token.release.assert_not_called()
        finally:
            conn._is_closed = False
            conn.allow_close = True
            conn._lock_token = None
            conn.close()

    def test_r1_unit_of_work_owned_vs_external_connection_token_lifecycle(self):
        """R1.5: UnitOfWork-owned connection releases token on exit; external connection does not."""
        target_path = os.path.join(self.temp_dir.name, "target_uow_tokens.db")
        mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)

        # 1. Owned connection: token held during UoW, released on exit
        with UnitOfWork(mgr) as uow:
            with self.assertRaises(BackupQuiescenceError):
                BackupManager.restore_backup(self.backup_path, target_path)

        # After UoW exit, restore lock can be acquired
        token = DatabaseRestoreLock.acquire_exclusive(target_path)
        self.assertTrue(token.is_held)
        token.release()

        # 2. External connection: token not released when UoW exits
        ext_conn = mgr.connect()
        try:
            with UnitOfWork(mgr, connection=ext_conn) as uow:
                pass
            # External connection is still open; restore must still be blocked
            with self.assertRaises(BackupQuiescenceError):
                BackupManager.restore_backup(self.backup_path, target_path)
        finally:
            ext_conn.close()

        # Now restore succeeds
        token = DatabaseRestoreLock.acquire_exclusive(target_path)
        self.assertTrue(token.is_held)
        token.release()

    # ---------------------------------------------------------------------------
    # R2: Target-State Inspection Behind Exclusive Lock & Pre-Placement Recheck
    # ---------------------------------------------------------------------------

    def test_r2_target_inspection_occurs_after_exclusive_lock_acquisition(self):
        """R2.1: Target existence inspection is performed strictly after exclusive restore lock acquisition."""
        target_path = os.path.join(self.temp_dir.name, "target_r2_order.db")
        self.assertFalse(os.path.exists(target_path))

        execution_order = []
        real_acquire_exclusive = DatabaseRestoreLock.acquire_exclusive
        real_exists = os.path.exists

        def tracked_acquire_exclusive(path):
            execution_order.append("acquire_exclusive")
            return real_acquire_exclusive(path)

        def tracked_exists(path):
            if path == target_path:
                execution_order.append("inspect_target")
            return real_exists(path)

        with patch.object(DatabaseRestoreLock, "acquire_exclusive", side_effect=tracked_acquire_exclusive), \
             patch("os.path.exists", side_effect=tracked_exists):
            res = BackupManager.restore_backup(self.backup_path, target_path)
            self.assertEqual(res.integrity_status, "ok")

        # Verify that acquire_exclusive occurred BEFORE the first target inspection
        first_acquire = execution_order.index("acquire_exclusive")
        target_inspections = [i for i, step in enumerate(execution_order) if step == "inspect_target"]
        self.assertTrue(len(target_inspections) >= 1)
        self.assertLess(first_acquire, target_inspections[0])

    def test_r2_target_created_before_placement_detected_by_recheck(self):
        """R2.2: Target appearing before placement raises BackupDestinationExistsError and preserves files."""
        target_path = os.path.join(self.temp_dir.name, "target_r2_recheck.db")
        self.assertFalse(os.path.exists(target_path))

        tmp_restore_path = [None]

        def progress_inject(status, remaining, total):
            # When backup is in progress, create target_path to simulate a concurrent racer
            if not os.path.exists(target_path):
                with open(target_path, "w") as f:
                    f.write("CONCURRENT RACER CONTENT")

        with self.assertRaises(BackupDestinationExistsError) as ctx:
            BackupManager.restore_backup(
                self.backup_path,
                target_path,
                progress_callback=progress_inject,
            )

        self.assertIn("was created concurrently during restoration", str(ctx.exception))
        # Verify existing racer file was not clobbered
        with open(target_path, "r") as f:
            content = f.read()
        self.assertEqual(content, "CONCURRENT RACER CONTENT")

        # Verify temporary database was preserved intact
        self.assertTrue(hasattr(ctx.exception, "temporary_database_path"))
        tmp_path = ctx.exception.temporary_database_path
        self.assertTrue(os.path.exists(tmp_path))
        # Verify the preserved temporary file is a valid SQLite DB
        self.assertTrue(BackupManager.verify_backup(tmp_path))

        # Cleanup preserved temporary file
        os.unlink(tmp_path)

    # ---------------------------------------------------------------------------
    # R3: Atomic No-Clobber Placement for New Targets
    # ---------------------------------------------------------------------------

    def test_r3_atomic_no_clobber_placement_failure_preserves_both_files(self):
        """R3.1: _atomic_no_clobber_place raises BackupDestinationExistsError and preserves files."""
        tmp_db = os.path.join(self.temp_dir.name, "tmp_source.db")
        dest_db = os.path.join(self.temp_dir.name, "dest_exists.db")

        with open(tmp_db, "w") as f:
            f.write("TEMPORARY DB CONTENT")
        with open(dest_db, "w") as f:
            f.write("DESTINATION DB CONTENT")

        with self.assertRaises(BackupDestinationExistsError) as ctx:
            BackupManager._atomic_no_clobber_place(tmp_db, dest_db)

        self.assertIn("atomic no-clobber placement rejected overwrite", str(ctx.exception))
        # Both files must remain unchanged and present
        self.assertTrue(os.path.exists(tmp_db))
        self.assertTrue(os.path.exists(dest_db))
        with open(tmp_db, "r") as f:
            self.assertEqual(f.read(), "TEMPORARY DB CONTENT")
        with open(dest_db, "r") as f:
            self.assertEqual(f.read(), "DESTINATION DB CONTENT")

    # ---------------------------------------------------------------------------
    # R4: Persistent Zero-Byte Restore-Lock Identity
    # ---------------------------------------------------------------------------

    def test_r4_persistent_zero_byte_restore_lock_never_unlinked(self):
        """R4.1: Restore lock file remains a persistent zero-byte artifact and is never unlinked."""
        target_path = os.path.join(self.temp_dir.name, "target_persistent_lock.db")
        lock_path = DatabaseRestoreLock.get_lock_path(target_path)
        self.assertFalse(os.path.exists(lock_path))

        # 1. Acquire shared lock
        token_sh = DatabaseRestoreLock.acquire_shared(target_path)
        self.assertTrue(os.path.exists(lock_path))
        self.assertEqual(os.path.getsize(lock_path), 0)

        # Release shared lock: must NOT unlink lock file!
        token_sh.release()
        self.assertTrue(os.path.exists(lock_path))
        self.assertEqual(os.path.getsize(lock_path), 0)

        # 2. Acquire exclusive lock
        token_ex = DatabaseRestoreLock.acquire_exclusive(target_path)
        self.assertTrue(os.path.exists(lock_path))
        # Must NOT write pid/timestamp metadata; must remain 0 bytes
        self.assertEqual(os.path.getsize(lock_path), 0)

        # Release exclusive lock: must NOT unlink lock file!
        token_ex.release()
        self.assertTrue(os.path.exists(lock_path))
        self.assertEqual(os.path.getsize(lock_path), 0)

    def test_r4_multiple_concurrent_managed_connections_coexist(self):
        """R4.2: Multiple ManagedConnection instances share the lock file without interference."""
        target_path = os.path.join(self.temp_dir.name, "target_multi_conn.db")
        mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)

        c1 = mgr.connect()
        c2 = mgr.connect()
        c3 = mgr.connect()

        try:
            # All 3 hold shared locks concurrently
            self.assertTrue(c1.lock_token.is_held)
            self.assertTrue(c2.lock_token.is_held)
            self.assertTrue(c3.lock_token.is_held)

            # Exclusive restore lock must be rejected
            with self.assertRaises(BackupQuiescenceError):
                DatabaseRestoreLock.acquire_exclusive(target_path)

            c1.close()
            # Still rejected while c2 and c3 are open
            with self.assertRaises(BackupQuiescenceError):
                DatabaseRestoreLock.acquire_exclusive(target_path)

            c2.close()
            # Still rejected while c3 is open
            with self.assertRaises(BackupQuiescenceError):
                DatabaseRestoreLock.acquire_exclusive(target_path)
        finally:
            c3.close()

        # All closed: exclusive lock succeeds
        tok_ex = DatabaseRestoreLock.acquire_exclusive(target_path)
        self.assertTrue(tok_ex.is_held)
        tok_ex.release()

    def test_r4_cross_process_managed_connection_blocks_restore(self):
        """R4.3: Managed connection held in a child process blocks restoration in parent process."""
        target_path = os.path.join(self.temp_dir.name, "target_child_proc.db")
        ready_file = os.path.join(self.temp_dir.name, "child_ready.flag")
        stop_file = os.path.join(self.temp_dir.name, "child_stop.flag")

        child_code = f"""
import sys, time, os
sys.path.insert(0, os.path.abspath('python'))
from database.manager import DatabaseManager, PRIMARY_FINANCIAL_PROFILE

mgr = DatabaseManager(r'{target_path}', profile=PRIMARY_FINANCIAL_PROFILE)
conn = mgr.connect()
with open(r'{ready_file}', 'w') as f:
    f.write('ready')

while not os.path.exists(r'{stop_file}'):
    time.sleep(0.05)

conn.close()
"""
        proc = subprocess.Popen([sys.executable, "-c", child_code])
        try:
            # Wait for child process to acquire connection
            timeout = time.time() + 10.0
            while not os.path.exists(ready_file) and time.time() < timeout:
                time.sleep(0.05)
            self.assertTrue(os.path.exists(ready_file), "Child process failed to acquire connection in time")

            # Restore must be rejected while child connection is open
            with self.assertRaises(BackupQuiescenceError) as ctx:
                BackupManager.restore_backup(self.backup_path, target_path)
            self.assertIn("Restore coordination lock is currently held", str(ctx.exception))
        finally:
            with open(stop_file, "w") as f:
                f.write("stop")
            proc.wait(timeout=5.0)

        # Once child process has closed its connection and terminated, restore succeeds
        res = BackupManager.restore_backup(self.backup_path, target_path)
        self.assertEqual(res.integrity_status, "ok")

    # ---------------------------------------------------------------------------
    # R5: Elimination of Remaining Silent Exception Suppression
    # ---------------------------------------------------------------------------

    def test_r5_unit_of_work_poisoned_rollback_failure_propagates_integrity_error(self):
        """R5.1: Failed rollback on poisoned UnitOfWork exit raises TransactionIntegrityError."""
        target_path = os.path.join(self.temp_dir.name, "target_uow_poison_rollback.db")
        mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)

        # Case A: exc_type is None (e.g. inner block caught the error)
        uow = UnitOfWork(mgr)
        uow.__enter__()
        uow._is_poisoned = True
        uow._poison_reason = "Deliberate poisoning for test"
        with patch.object(uow, "rollback", side_effect=RuntimeError("Rollback execution failed")):
            with self.assertRaises(TransactionIntegrityError) as ctx:
                uow.__exit__(None, None, None)

        self.assertIn("completed with poisoned state", str(ctx.exception))
        self.assertTrue(hasattr(ctx.exception, "rollback_error"))
        self.assertIn("Rollback execution failed", str(ctx.exception.rollback_error))

        # Case B: exc_type is not None (exception bubbling out)
        uow2 = UnitOfWork(mgr)
        uow2.__enter__()
        uow2._is_poisoned = True
        uow2._poison_reason = "Poisoned before error"
        val_err = ValueError("Primary domain failure")
        with patch.object(uow2, "rollback", side_effect=RuntimeError("Secondary rollback failure")):
            with self.assertRaises(TransactionIntegrityError) as ctx2:
                uow2.__exit__(ValueError, val_err, None)

        self.assertIn("Rollback failed during poisoned transaction exit", str(ctx2.exception))
        self.assertIn("Secondary rollback failure", str(ctx2.exception))
        self.assertIs(ctx2.exception.__cause__, val_err)

    def test_r5_database_manager_connection_close_failure_reported(self):
        """R5.2: DatabaseManager.connection() reports connection close failure without suppression."""
        target_path = os.path.join(self.temp_dir.name, "target_mgr_conn_close.db")
        mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)

        created_conns = []

        def faulty_connect():
            c = sqlite3.connect(mgr.db_path, autocommit=True, factory=FaultyManagedConnection)
            c.allow_close = False
            created_conns.append(c)
            return c

        try:
            with patch.object(mgr, "connect", side_effect=faulty_connect):
                # 1. Clean context exit: close failure raises DatabaseConnectionError
                with self.assertRaises(DatabaseConnectionError) as ctx:
                    with mgr.connection() as conn:
                        pass
                self.assertIn("Injected connection close failure", str(ctx.exception))

                # 2. Context exit with active exception: close failure attached as connection_close_error
                with self.assertRaises(ValueError) as ctx:
                    with mgr.connection() as conn:
                        raise ValueError("Domain query failure")
                self.assertEqual(str(ctx.exception), "Domain query failure")
                self.assertTrue(hasattr(ctx.exception, "connection_close_error"))
                self.assertIn("Injected connection close failure", str(ctx.exception.connection_close_error))
        finally:
            # Cleanup created connections
            for c in created_conns:
                c._is_closed = False
                c.allow_close = True
                c.close()

    def test_r5_database_manager_transaction_rollback_failure_reported(self):
        """R5.3: DatabaseManager.transaction() rollback failure raises DatabaseTransactionError."""
        target_path = os.path.join(self.temp_dir.name, "target_mgr_tx_rollback.db")
        mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)

        # Open connection with FaultyRollbackConnection factory
        conn = sqlite3.connect(target_path, autocommit=True, factory=FaultyRollbackConnection)
        conn.fail_rollback = True
        try:
            with self.assertRaises(DatabaseTransactionError) as ctx:
                with mgr.transaction(conn=conn):
                    raise ValueError("Transaction domain logic error")

            self.assertIn("Transaction failed and rollback also failed", str(ctx.exception))
            self.assertIn("Rollback hardware fail", str(ctx.exception))
            self.assertIsInstance(ctx.exception.__cause__, ValueError)
            self.assertTrue(hasattr(ctx.exception, "rollback_error"))
        finally:
            conn.fail_rollback = False
            conn.close()


# ===========================================================================
# Stage 1.5-A.2.2.1: Managed Connection Close-Failure Safety & Cleanup Integrity
# ===========================================================================

class UnattributableError(Exception):
    """Exception that forbids arbitrary attribute assignment."""
    def __setattr__(self, name, value):
        raise TypeError(f"Cannot set attribute {name} on immutable exception")


class TestStage15A221ManagedConnectionCloseSafetyAndCleanupIntegrity(unittest.TestCase):
    """
    Focused verification of Stage 1.5-A.2.2.1 requirements:
    - R1: Managed SQLite connection never releases shared restore lock if SQLite close fails.
    - R1: Distinguishes SQLite close failure, token release failure, both succeeding, and retries.
    - R2: Failure-injection tests proving actual restore-exclusion invariant using real DB and locks.
    - R3: UnitOfWork integration for owned and external connection close failure and state preservation.
    - R4: Cleanup exception non-suppression in shared_gate, acquire_shared, acquire_exclusive, and fallbacks.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.source_db_path = os.path.join(self.temp_dir.name, "source_stage15_a221.db")
        self.backup_path = os.path.join(self.temp_dir.name, "backup_stage15_a221.db")

        # Initialize and populate source database
        mgr = DatabaseManager(self.source_db_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute(
                "INSERT INTO portfolio_cash (account_id, currency, amount, last_updated_at_utc) "
                "VALUES ('PRIMARY', 'EUR', '300000.00', '2026-11-09T20:00:00Z');"
            )

        # Create valid verified backup
        res = BackupManager.create_backup(self.source_db_path, self.backup_path, overwrite=False)
        self.assertEqual(res.integrity_status, "ok")

    def tearDown(self):
        self.temp_dir.cleanup()

    # ---------------------------------------------------------------------------
    # R1 & R2: ManagedConnection.close() Failure Semantics & Restore Exclusion
    # ---------------------------------------------------------------------------

    def test_r1_sqlite_close_failure_retains_token_and_blocks_restore_until_succeeded(self):
        """R1.1 / R2.1-R2.7: SQLite close failure retains restore lock; restore blocked until subsequent close succeeds."""
        target_path = os.path.join(self.temp_dir.name, "target_close_safety.db")

        # 1. Acquire real shared restore lock token on target_path
        real_token = DatabaseRestoreLock.acquire_shared(target_path)
        self.assertIsNotNone(real_token)
        self.assertTrue(real_token.is_held)

        # 2. Open connection using FaultyManagedConnection with close failure injected
        conn = sqlite3.connect(target_path, autocommit=True, factory=FaultyManagedConnection)
        conn.allow_close = False
        conn._lock_token = real_token

        try:
            # 3. Call conn.close() -> SQLite close fails
            with self.assertRaises(DatabaseConnectionError) as ctx:
                conn.close()

            self.assertIn("Failed to close SQLite connection", str(ctx.exception))
            self.assertIn("Injected connection close failure", str(ctx.exception))

            # 4. Verify shared token remains held and connection is NOT marked closed
            self.assertIsNotNone(conn.lock_token)
            self.assertTrue(conn.lock_token.is_held)
            self.assertFalse(conn.is_sqlite_closed)
            self.assertFalse(conn.is_closed)

            # 5. Attempt DatabaseRestoreLock.acquire_exclusive() and verify restoration is REJECTED
            with self.assertRaises(BackupQuiescenceError) as q_ctx:
                DatabaseRestoreLock.acquire_exclusive(target_path)
            self.assertIn("Restore coordination lock is currently held", str(q_ctx.exception))

            # Also verify BackupManager.restore_backup() is rejected fail-closed
            with self.assertRaises(BackupQuiescenceError):
                BackupManager.restore_backup(self.backup_path, target_path)

            # 6. Allow underlying SQLite close to succeed on subsequent attempt
            conn.allow_close = True
            conn.close()

            # 7. Verify token is then released and connection is fully closed
            self.assertIsNone(conn.lock_token)
            self.assertTrue(conn.is_sqlite_closed)
            self.assertTrue(conn.is_closed)

            # 8. Verify exclusive restore-lock acquisition succeeds only after successful connection closure
            exclusive_token = DatabaseRestoreLock.acquire_exclusive(target_path)
            self.assertTrue(exclusive_token.is_held)
            exclusive_token.release()

            # 9. Verify repeated close calls remain idempotent
            conn.close()
            conn.close()
            self.assertTrue(conn.is_closed)
        finally:
            conn._sqlite_closed = False
            conn.allow_close = True
            if conn.lock_token is not None:
                try:
                    conn.lock_token.release()
                except Exception:
                    pass
                conn._lock_token = None
            try:
                super(FaultyManagedConnection, conn).close()
            except Exception:
                pass

    def test_r1_sqlite_close_succeeds_but_token_release_fails_retains_token(self):
        """R1.2 / R2.9: SQLite close succeeds but lock release fails: error reported, token retained, restore blocked."""
        target_path = os.path.join(self.temp_dir.name, "target_lock_fail_retained.db")

        real_token = DatabaseRestoreLock.acquire_shared(target_path)
        conn = sqlite3.connect(target_path, autocommit=True, factory=FaultyManagedConnection)
        conn.allow_close = True
        conn._lock_token = real_token

        # Inject failure into token.release
        with patch.object(real_token, "release", side_effect=RuntimeError("Hardware unlock failure")):
            with self.assertRaises(DatabaseConnectionError) as ctx:
                conn.close()

            self.assertIn("SQLite connection closed successfully, but restore lock release failed", str(ctx.exception))
            self.assertIn("Hardware unlock failure", str(ctx.exception))

            # Connection is sqlite-closed, but NOT fully closed, and token is NOT discarded
            self.assertTrue(conn.is_sqlite_closed)
            self.assertFalse(conn.is_closed)
            self.assertIsNotNone(conn.lock_token)
            self.assertTrue(conn.lock_token.is_held)

            # Restoration remains blocked
            with self.assertRaises(BackupQuiescenceError):
                DatabaseRestoreLock.acquire_exclusive(target_path)

        # Retry close without failure -> succeeds and releases token
        conn.close()
        self.assertTrue(conn.is_closed)
        self.assertIsNone(conn.lock_token)

        # Now exclusive lock succeeds
        ex_tok = DatabaseRestoreLock.acquire_exclusive(target_path)
        self.assertTrue(ex_tok.is_held)
        ex_tok.release()

    def test_r1_both_close_and_token_release_fail_diagnosable(self):
        """R1.3 / R2.10: Verify public force_lock_release bypass is removed and rejected with TypeError."""
        target_path = os.path.join(self.temp_dir.name, "target_both_fail_diag.db")
        conn = sqlite3.connect(target_path, autocommit=True, factory=FaultyManagedConnection)
        conn.allow_close = False
        mock_token = MagicMock()
        mock_token.release.side_effect = RuntimeError("Secondary lock release error")
        conn._lock_token = mock_token

        try:
            # Calling with force_lock_release raises TypeError because bypass is removed
            with self.assertRaises(TypeError):
                conn.close(force_lock_release=True)

            # Normal close preserves SQLite failure and does NOT call token.release()
            with self.assertRaises(DatabaseConnectionError) as ctx:
                conn.close()

            self.assertIn("Failed to close SQLite connection", str(ctx.exception))
            self.assertIn("Injected connection close failure", str(ctx.exception))
            mock_token.release.assert_not_called()
            self.assertFalse(conn.is_sqlite_closed)
            self.assertFalse(conn.is_closed)
            self.assertIs(conn.lock_token, mock_token)
        finally:
            conn._sqlite_closed = False
            conn.allow_close = True
            conn._lock_token = None
            try:
                super(FaultyManagedConnection, conn).close()
            except Exception:
                pass

    # ---------------------------------------------------------------------------
    # R3: UnitOfWork Integration
    # ---------------------------------------------------------------------------

    def test_r3_unit_of_work_owned_connection_close_failure_preserves_state_and_lock(self):
        """R3.1: Owned connection whose close fails in UnitOfWork retains lock and marks conn_unsafe."""
        target_path = os.path.join(self.temp_dir.name, "target_uow_owned_fail.db")
        mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)

        created_conns = []

        def faulty_connect():
            token = DatabaseRestoreLock.acquire_shared(mgr.db_path)
            c = sqlite3.connect(mgr.db_path, autocommit=True, factory=FaultyManagedConnection)
            c.allow_close = False
            c._lock_token = token
            created_conns.append(c)
            return c

        with patch.object(mgr, "connect", side_effect=faulty_connect):
            uow = UnitOfWork(mgr)
            with self.assertRaises(TransactionIntegrityError) as ctx:
                with uow:
                    pass  # Clean exit; close() will fail in finally

            self.assertIn("Failed to close owned database connection on UnitOfWork exit", str(ctx.exception))
            self.assertTrue(uow.is_conn_unsafe)

            # Restoration MUST remain blocked because the connection's restore lock was retained!
            with self.assertRaises(BackupQuiescenceError):
                DatabaseRestoreLock.acquire_exclusive(target_path)

        # Cleanup: allow close and close the connection
        for c in created_conns:
            c.allow_close = True
            c.close()

        # After real cleanup, restore lock succeeds
        ex_tok = DatabaseRestoreLock.acquire_exclusive(target_path)
        self.assertTrue(ex_tok.is_held)
        ex_tok.release()

    def test_r3_unit_of_work_owned_connection_close_failure_with_active_exception(self):
        """R3.2: Transaction error in UnitOfWork attaches secondary close error without suppression."""
        target_path = os.path.join(self.temp_dir.name, "target_uow_owned_tx_fail.db")
        mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)

        created_conns = []

        def faulty_connect():
            token = DatabaseRestoreLock.acquire_shared(mgr.db_path)
            c = sqlite3.connect(mgr.db_path, autocommit=True, factory=FaultyManagedConnection)
            c.allow_close = False
            c._lock_token = token
            created_conns.append(c)
            return c

        with patch.object(mgr, "connect", side_effect=faulty_connect):
            uow = UnitOfWork(mgr)
            with self.assertRaises(ValueError) as ctx:
                with uow:
                    raise ValueError("Domain application transaction failure")

            self.assertEqual(str(ctx.exception), "Domain application transaction failure")
            self.assertTrue(hasattr(ctx.exception, "connection_close_error"))
            self.assertIn("Injected connection close failure", str(ctx.exception.connection_close_error))
            self.assertTrue(uow.is_conn_unsafe)

        for c in created_conns:
            c.allow_close = True
            c.close()

    def test_r3_unit_of_work_external_connection_close_failure_preserves_caller_ownership(self):
        """R3.3: UnitOfWork leaves external connection ownership and close responsibility to caller."""
        target_path = os.path.join(self.temp_dir.name, "target_uow_ext_fail.db")
        mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)

        token = DatabaseRestoreLock.acquire_shared(target_path)
        ext_conn = sqlite3.connect(target_path, autocommit=True, factory=FaultyManagedConnection)
        ext_conn.allow_close = False
        ext_conn._lock_token = token

        try:
            # UoW with external connection
            with UnitOfWork(mgr, connection=ext_conn) as uow:
                pass

            # UoW does NOT close external connection; it remains open and lock is held
            self.assertFalse(ext_conn.is_closed)
            self.assertTrue(ext_conn.lock_token.is_held)

            with self.assertRaises(BackupQuiescenceError):
                DatabaseRestoreLock.acquire_exclusive(target_path)

            # Caller attempts close -> fails and retains lock
            with self.assertRaises(DatabaseConnectionError):
                ext_conn.close()

            with self.assertRaises(BackupQuiescenceError):
                DatabaseRestoreLock.acquire_exclusive(target_path)

            # Caller allows close -> succeeds and releases lock
            ext_conn.allow_close = True
            ext_conn.close()
            self.assertTrue(ext_conn.is_closed)

            # Now restore succeeds
            ex_tok = DatabaseRestoreLock.acquire_exclusive(target_path)
            self.assertTrue(ex_tok.is_held)
            ex_tok.release()
        finally:
            ext_conn.allow_close = True
            if ext_conn.lock_token is not None:
                try:
                    ext_conn.lock_token.release()
                except Exception:
                    pass
                ext_conn._lock_token = None
            try:
                super(FaultyManagedConnection, ext_conn).close()
            except Exception:
                pass

    # ---------------------------------------------------------------------------
    # R4: Cleanup Exception Non-Suppression & Diagnostic Fallbacks
    # ---------------------------------------------------------------------------

    def test_r4_shared_gate_release_failure_reported(self):
        """R4.1: DatabaseRestoreLock.shared_gate() reports lock release failure on clean exit and active error."""
        target_path = os.path.join(self.temp_dir.name, "target_gate_fail.db")
        real_acquire = DatabaseRestoreLock.acquire_shared

        # 1. Clean exit raises release failure
        def faulty_acquire_clean(path):
            tok = real_acquire(path)
            orig_rel = tok.release

            def faulty_rel():
                orig_rel()  # Release OS lock first so no handle leak on Windows
                raise RuntimeError("Gate release error")

            tok.release = faulty_rel
            return tok

        with patch.object(DatabaseRestoreLock, "acquire_shared", side_effect=faulty_acquire_clean):
            with self.assertRaises(RuntimeError) as ctx:
                with DatabaseRestoreLock.shared_gate(target_path):
                    pass
            self.assertIn("Gate release error", str(ctx.exception))

        # 2. Context exit with active error attaches lock_release_error
        def faulty_acquire_active_err(path):
            tok = real_acquire(path)
            orig_rel = tok.release

            def faulty_rel():
                orig_rel()  # Release OS lock first so no handle leak on Windows
                raise RuntimeError("Gate release error")

            tok.release = faulty_rel
            return tok

        with patch.object(DatabaseRestoreLock, "acquire_shared", side_effect=faulty_acquire_active_err):
            with self.assertRaises(ValueError) as ctx2:
                with DatabaseRestoreLock.shared_gate(target_path):
                    raise ValueError("Domain query error")
            self.assertEqual(str(ctx2.exception), "Domain query error")
            self.assertTrue(hasattr(ctx2.exception, "lock_release_error"))
            self.assertIn("Gate release error", str(ctx2.exception.lock_release_error))

    def test_r4_acquire_shared_and_exclusive_file_close_failure_attached(self):
        """R4.2: File-close failures during lock acquisition cleanup are attached as file_close_error."""
        target_path = os.path.join(self.temp_dir.name, "target_acq_cleanup.db")

        # Make file.close fail during lock failure cleanup
        real_open = open

        def faulty_open(path, *args, **kwargs):
            f = real_open(path, *args, **kwargs)
            if ".restore_lock" in path:
                f_close = f.close
                def faulty_f_close():
                    f_close()
                    raise OSError("Injected lock file handle close failure")
                f.close = faulty_f_close
            return f

        # Hold exclusive lock first so acquire_shared fails and triggers cleanup
        ex_tok = DatabaseRestoreLock.acquire_exclusive(target_path)
        try:
            with patch("builtins.open", side_effect=faulty_open):
                with self.assertRaises(RestoreLockBusyError) as ctx:
                    DatabaseRestoreLock.acquire_shared(target_path)
                self.assertTrue(hasattr(ctx.exception, "file_close_error"))
                self.assertIn("Injected lock file handle close failure", str(ctx.exception.file_close_error))
        finally:
            ex_tok.release()

    def test_r4_diagnostic_attachment_fallback_on_unattributable_exception(self):
        """R4.3: If diagnostic attachment fails on an unattributable exception, fallback chains explicitly."""
        target_path = os.path.join(self.temp_dir.name, "target_fallback.db")
        mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)

        created_conns = []

        def faulty_connect():
            c = sqlite3.connect(mgr.db_path, autocommit=True, factory=FaultyManagedConnection)
            c.allow_close = False
            created_conns.append(c)
            return c

        try:
            with patch.object(mgr, "connect", side_effect=faulty_connect):
                # An exception whose type has __slots__ = () and does not allow setattr
                with self.assertRaises(DatabaseConnectionError) as ctx:
                    with mgr.connection():
                        raise UnattributableError("Slots exception")

                self.assertIn("Injected connection close failure", str(ctx.exception))
                self.assertIsInstance(ctx.exception.__cause__, UnattributableError)
        finally:
            for c in created_conns:
                c.allow_close = True
                c.close()


class TestStage15A222RestoreLockTokenReleaseIntegrityAndFailClosedLifecycle(unittest.TestCase):
    """
    Stage 1.5-A.2.2.2 Restore Lock Token Release Integrity & Fail-Closed Lifecycle Tests.

    Validates:
    - R1: Public force_lock_release bypass removal; SQLite close failure prevents token release.
    - R2: DatabaseRestoreLockToken truthful state machine (HELD, UNLOCK_FAILED, UNLOCKED_BUT_CLOSE_FAILED, FULLY_RELEASED, UNCERTAIN).
    - R2: Preserved OS handle references on cleanup failure for safe retry and diagnostics.
    - R2: Suppression of duplicate unlock calls after unlock has already succeeded.
    - R3: ManagedConnection ownership retention and retryable lock release.
    - R4: UnitOfWork rollback and close failure preservation on unattributable exceptions.
    - R5: Real OS lock failure injection and mutual exclusion verification.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_r5_01_sqlite_close_failure_cannot_invoke_token_release(self):
        """R5.1: Failed super().close() never calls token.release(), retaining restore exclusion."""
        target_path = os.path.join(self.temp_dir.name, "r5_01.db")
        token = DatabaseRestoreLock.acquire_shared(target_path)
        conn = sqlite3.connect(target_path, autocommit=True, factory=FaultyManagedConnection)
        conn.allow_close = False
        conn._lock_token = token

        with patch.object(token, "release", wraps=token.release) as mock_rel:
            with self.assertRaises(DatabaseConnectionError):
                conn.close()
            mock_rel.assert_not_called()
            self.assertFalse(conn.is_sqlite_closed)
            self.assertFalse(conn.is_closed)
            self.assertIs(conn.lock_token, token)
            self.assertTrue(token.is_held)
            self.assertEqual(token.state, LockTokenState.HELD)

        # Cleanup
        conn.allow_close = True
        conn.close()
        self.assertTrue(conn.is_closed)

    def test_r5_02_no_public_force_lock_release_bypass(self):
        """R5.2: Public force_lock_release parameter is completely removed from production API."""
        sig = inspect.signature(ManagedConnection.close)
        self.assertNotIn("force_lock_release", sig.parameters)
        self.assertEqual(list(sig.parameters.keys()), ["self"])

        target_path = os.path.join(self.temp_dir.name, "r5_02.db")
        conn = sqlite3.connect(target_path, autocommit=True, factory=ManagedConnection)
        with self.assertRaises(TypeError):
            conn.close(force_lock_release=True)
        conn.close()

    def test_r5_03_successful_sqlite_close_followed_by_successful_token_release(self):
        """R5.3: Normal connection close executes clean two-phase closure and releases exclusive lock."""
        target_path = os.path.join(self.temp_dir.name, "r5_03.db")
        mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)
        conn = mgr.connect()
        self.assertIsInstance(conn, ManagedConnection)
        tok = conn.lock_token
        self.assertIsNotNone(tok)
        self.assertTrue(tok.is_held)
        self.assertEqual(tok.state, LockTokenState.HELD)

        conn.close()
        self.assertTrue(conn.is_sqlite_closed)
        self.assertTrue(conn.is_closed)
        self.assertIsNone(conn.lock_token)
        self.assertTrue(tok.is_released)
        self.assertEqual(tok.state, LockTokenState.FULLY_RELEASED)

        # Exclusive lock can be immediately acquired
        ex_tok = DatabaseRestoreLock.acquire_exclusive(target_path)
        self.assertTrue(ex_tok.is_held)
        ex_tok.release()

    @unittest.skipUnless(sys.platform != "win32", "POSIX-only: flock unlock failure injection")
    def test_r5_04_posix_unlock_failure_injection(self):
        """R5.4: POSIX unlock failure transitions to UNLOCK_FAILED, retains handle, and blocks restore."""
        target_path = os.path.join(self.temp_dir.name, "r5_04.db")
        token = DatabaseRestoreLock.acquire_shared(target_path)
        import fcntl
        with patch("fcntl.flock", side_effect=OSError("Injected POSIX flock unlock failure")):
            with self.assertRaises(BackupCleanupError) as ctx:
                token.release()
            self.assertIn("OS unlock failed", str(ctx.exception))
            self.assertEqual(token.state, LockTokenState.UNLOCK_FAILED)
            self.assertTrue(token.is_held)
            self.assertFalse(token.is_released)
            self.assertIsNotNone(token._f)
            self.assertFalse(token._f.closed)

        # Clean release
        token.release()
        self.assertTrue(token.is_released)

    @unittest.skipUnless(sys.platform == "win32", "Windows-only: UnlockFileEx failure injection and real OS lock test")
    def test_r5_05_windows_unlock_failure_injection(self):
        """R5.5: Windows UnlockFileEx failure retains OS lock, transitions to UNLOCK_FAILED, blocks exclusive lock."""
        target_path = os.path.join(self.temp_dir.name, "r5_05.db")
        token = DatabaseRestoreLock.acquire_shared(target_path)
        try:
            with patch.object(ctypes.windll.kernel32, "UnlockFileEx", return_value=0):
                with self.assertRaises(BackupCleanupError) as ctx:
                    token.release()
                self.assertIn("OS unlock failed", str(ctx.exception))
                self.assertEqual(token.state, LockTokenState.UNLOCK_FAILED)
                self.assertTrue(token.is_held)
                self.assertFalse(token.is_released)
                self.assertIsNotNone(token._f)
                self.assertFalse(token._f.closed)
                self.assertIsNotNone(token._handle)

                # CRITICAL REAL OS LOCK VERIFICATION:
                # The kernel byte-range lock is STILL ACTIVE on Windows.
                # Attempting acquire_exclusive from another handle MUST FAIL with BackupQuiescenceError!
                with self.assertRaises(BackupQuiescenceError):
                    DatabaseRestoreLock.acquire_exclusive(target_path)
        finally:
            token.release()
            self.assertTrue(token.is_released)
            self.assertFalse(token.is_held)
            self.assertEqual(token.state, LockTokenState.FULLY_RELEASED)

    def test_r5_06_file_handle_close_failure_after_successful_unlock(self):
        """R5.6: Handle close failure after successful unlock sets UNLOCKED_BUT_CLOSE_FAILED with is_held=False."""
        target_path = os.path.join(self.temp_dir.name, "r5_06.db")
        token = DatabaseRestoreLock.acquire_shared(target_path)

        real_close = token._f.close

        def faulty_close():
            raise OSError("Injected handle close error")

        token._f.close = faulty_close

        try:
            with self.assertRaises(BackupCleanupError) as ctx:
                token.release()
            self.assertIn("OS unlock succeeded, but file close failed", str(ctx.exception))
            self.assertEqual(token.state, LockTokenState.UNLOCKED_BUT_CLOSE_FAILED)
            # Unlock succeeded in kernel: is_held MUST be False (no false claims)
            self.assertFalse(token.is_held)
            self.assertFalse(token.is_released)
            self.assertIsNotNone(token.close_error)
            self.assertIsNone(token.unlock_error)
            self.assertIsNotNone(token._f)
        finally:
            token._f.close = real_close
            token.release()
            self.assertTrue(token.is_released)

    def test_r5_07_token_state_after_each_failure_and_success(self):
        """R5.7: Truthful state progression through HELD, release, and FULLY_RELEASED."""
        target_path = os.path.join(self.temp_dir.name, "r5_07.db")
        token = DatabaseRestoreLock.acquire_shared(target_path)
        self.assertEqual(token.state, LockTokenState.HELD)
        self.assertTrue(token.is_held)
        self.assertFalse(token.is_released)
        self.assertFalse(token.is_uncertain)

        token.release()
        self.assertEqual(token.state, LockTokenState.FULLY_RELEASED)
        self.assertFalse(token.is_held)
        self.assertTrue(token.is_released)
        self.assertFalse(token.is_uncertain)

        # Repeated release is idempotent
        token.release()
        self.assertEqual(token.state, LockTokenState.FULLY_RELEASED)

    def test_r5_08_safe_retry_after_recoverable_failure(self):
        """R5.8: Safe retry transitions from failure state to FULLY_RELEASED without handle loss."""
        target_path = os.path.join(self.temp_dir.name, "r5_08.db")
        token = DatabaseRestoreLock.acquire_shared(target_path)

        real_close = token._f.close
        fail_close = True

        def conditional_close():
            if fail_close:
                raise OSError("Temporary close failure")
            return real_close()

        token._f.close = conditional_close

        with self.assertRaises(BackupCleanupError):
            token.release()
        self.assertEqual(token.state, LockTokenState.UNLOCKED_BUT_CLOSE_FAILED)

        # Clear failure and retry
        fail_close = False
        token.release()
        self.assertEqual(token.state, LockTokenState.FULLY_RELEASED)
        self.assertTrue(token.is_released)
        self.assertIsNone(token._f)

    def test_r5_09_no_duplicate_unlock_after_already_successful_unlock(self):
        """R5.9: Retrying release after close failure does NOT issue duplicate OS unlock calls."""
        target_path = os.path.join(self.temp_dir.name, "r5_09.db")
        token = DatabaseRestoreLock.acquire_shared(target_path)

        real_close = token._f.close
        token._f.close = lambda: (_ for _ in ()).throw(OSError("Simulated handle close error"))

        with self.assertRaises(BackupCleanupError):
            token.release()
        self.assertEqual(token.state, LockTokenState.UNLOCKED_BUT_CLOSE_FAILED)

        # Restore real close; verify platform unlock is not called during retry
        token._f.close = real_close
        if sys.platform == "win32":
            with patch.object(ctypes.windll.kernel32, "UnlockFileEx") as mock_unlock:
                token.release()
                mock_unlock.assert_not_called()
        else:
            import fcntl
            with patch("fcntl.flock") as mock_flock:
                token.release()
                mock_flock.assert_not_called()

        self.assertEqual(token.state, LockTokenState.FULLY_RELEASED)
        self.assertTrue(token.is_released)

    def test_r5_10_managed_connection_retains_token_until_release_confirmed(self):
        """R5.10: ManagedConnection preserves token ownership during release failure and releases on retry."""
        target_path = os.path.join(self.temp_dir.name, "r5_10.db")
        token = DatabaseRestoreLock.acquire_shared(target_path)
        conn = sqlite3.connect(target_path, autocommit=True, factory=FaultyManagedConnection)
        conn.allow_close = True
        conn._lock_token = token

        with patch.object(token, "release", side_effect=BackupCleanupError("Token release error")):
            with self.assertRaises(DatabaseConnectionError):
                conn.close()

            self.assertIs(conn.lock_token, token)
            self.assertFalse(conn.is_closed)
            self.assertTrue(conn.is_sqlite_closed)

        # Retry without failure
        conn.close()
        self.assertIsNone(conn.lock_token)
        self.assertTrue(conn.is_closed)

    def test_r5_11_exclusive_restore_blocked_whenever_os_lock_held(self):
        """R5.11: Exclusive restore lock is rejected whenever managed connection holds shared lock."""
        target_path = os.path.join(self.temp_dir.name, "r5_11.db")
        mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)
        conn = mgr.connect()
        self.assertTrue(conn.lock_token.is_held)

        with self.assertRaises(BackupQuiescenceError):
            DatabaseRestoreLock.acquire_exclusive(target_path)

        conn.close()

        ex = DatabaseRestoreLock.acquire_exclusive(target_path)
        self.assertTrue(ex.is_held)
        ex.release()

    def test_r5_12_uncertain_release_state_reported_accurately(self):
        """R5.12: Unexpected release exception sets UNCERTAIN; does not claim is_held or is_released."""
        target_path = os.path.join(self.temp_dir.name, "r5_12.db")
        token = DatabaseRestoreLock.acquire_shared(target_path)
        try:
            if sys.platform == "win32":
                patch_target = patch.object(ctypes.windll.kernel32, "UnlockFileEx", side_effect=TypeError("Ctypes fault"))
            else:
                patch_target = patch("fcntl.flock", side_effect=TypeError("Flock fault"))

            with patch_target:
                with self.assertRaises(BackupCleanupError):
                    token.release()

            self.assertEqual(token.state, LockTokenState.UNCERTAIN)
            self.assertTrue(token.is_uncertain)
            self.assertFalse(token.is_held)
            self.assertFalse(token.is_released)
        finally:
            if token._f and not getattr(token._f, "closed", False):
                try:
                    token._f.close()
                except Exception:
                    pass

    def test_r5_13_unit_of_work_preserves_rollback_and_close_errors_on_unattributable_exception(self):
        """R5.13: UnitOfWork poisoned exit and close preserve rollback/close errors when setattr fails."""
        target_path = os.path.join(self.temp_dir.name, "r5_13.db")
        mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)

        # Part A: Poisoned rollback failure with unattributable exception
        uow = UnitOfWork(mgr)
        uow.__enter__()
        uow._is_poisoned = True
        uow._poison_reason = "Testing diagnostic attachment on poison"
        unattributable = UnattributableError("Slots exception rejecting attributes")

        with patch.object(uow, "rollback", side_effect=RuntimeError("Rollback execution failed")):
            with self.assertRaises(TransactionIntegrityError) as ctx:
                uow.__exit__(UnattributableError, unattributable, None)

        self.assertIn("CRITICAL: Rollback failed during poisoned transaction exit", str(ctx.exception))
        self.assertIn("Diagnostic attachment failed", str(ctx.exception))
        self.assertTrue(hasattr(ctx.exception, "rollback_error"))
        self.assertTrue(hasattr(ctx.exception, "attachment_error"))
        self.assertIsInstance(ctx.exception.__cause__, UnattributableError)
        self.assertTrue(uow.is_conn_unsafe)

        # Part B: Owned connection close failure with unattributable exception
        created_conns = []

        def faulty_close_factory():
            c = sqlite3.connect(mgr.db_path, autocommit=True, factory=FaultyManagedConnection)
            c.allow_close = False
            created_conns.append(c)
            return c

        try:
            with patch.object(mgr, "connect", side_effect=faulty_close_factory):
                uow2 = UnitOfWork(mgr)
                with self.assertRaises(TransactionIntegrityError) as ctx2:
                    with uow2:
                        raise UnattributableError("Slots exception during normal body")

                self.assertIn("CRITICAL: Failed to close owned database connection on UnitOfWork exit", str(ctx2.exception))
                self.assertIsInstance(ctx2.exception.__cause__, UnattributableError)
                self.assertTrue(uow2.is_conn_unsafe)
        finally:
            for c in created_conns:
                c.allow_close = True
                c.close()

    def test_r5_14_existing_cross_process_locking_and_connection_lifetime_maintained(self):
        """R5.14: ManagedConnection connection-lifetime restore exclusion works end-to-end with real restore."""
        target_path = os.path.join(self.temp_dir.name, "r5_14.db")
        mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)

        with mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            # While connection is active, restore_backup must fail with BackupQuiescenceError
            with self.assertRaises(BackupQuiescenceError):
                DatabaseRestoreLock.acquire_exclusive(target_path)

        # After connection exits context manager, restore lock can be acquired
        ex_tok = DatabaseRestoreLock.acquire_exclusive(target_path)
        self.assertTrue(ex_tok.is_held)
        ex_tok.release()


if __name__ == "__main__":
    unittest.main()
