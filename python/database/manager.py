"""
Database Connection Manager and Configuration Infrastructure
Governed by Stage 1.3 Directive and Stage 1.2.1 / 1.3.1 Architecture Corrections.

Provides isolated, explicit connection lifecycle and transaction management
for SQLite databases using Write-Ahead Logging (WAL) and strict PRAGMA configurations.

Durability & Consistency Considerations:
- Transactional Consistency: SQLite transactions in WAL mode guarantee consistency
  (no torn pages or half-written rows) following unexpected application crashes or
  power interruptions.
- SQLite Durability Guarantees: PRIMARY_FINANCIAL_PROFILE configures `PRAGMA synchronous = FULL;`.
  In WAL mode with FULL, SQLite executes an explicit filesystem flush (Win32 FlushFileBuffers on
  Windows, fsync on POSIX) on the WAL file upon every transaction commit before returning control
  to the caller. This ensures that committed transactions survive operating-system crashes and
  sudden power loss on conforming storage hardware.
- Storage & Hardware Assumptions: No database software can provide absolute durability against
  catastrophic storage-controller failures, non-volatile drive wear-out, or drive firmware that
  reorders or falsely reports cache flush completion. Regular point-in-time backups via BackupManager
  provide independent recovery against physical and filesystem-level disasters.
"""

import os
import sys
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Generator, Optional, Tuple, List, Any

from python.database.backup import (
    DatabaseRestoreLock,
    DatabaseRestoreLockToken,
    RestoreLockBusyError,
)


# ---------------------------------------------------------------------------
# Custom Exception Hierarchy
# ---------------------------------------------------------------------------

class DatabaseError(Exception):
    """Base exception for all SQLite database infrastructure errors."""
    pass


class DatabaseConnectionError(DatabaseError):
    """Raised when opening or initializing a database connection fails."""
    pass


class DatabaseConfigurationError(DatabaseError):
    """Raised when PRAGMA settings cannot be applied or fail verification."""
    pass


class DatabaseBusyError(DatabaseError):
    """Raised when the database is locked or busy timeout is exceeded."""
    pass


class DatabaseCorruptionError(DatabaseError):
    """Raised when database corruption or malformed pages are detected."""
    pass


class DatabaseTransactionError(DatabaseError):
    """Raised when transaction begin, commit, or rollback fails, or on nested transaction attempts."""
    pass


# ---------------------------------------------------------------------------
# Managed Connection Lifecycle (Stage 1.5-A.2.2 R1)
# ---------------------------------------------------------------------------

class ManagedConnection(sqlite3.Connection):
    """
    Managed SQLite connection retaining an active DatabaseRestoreLock shared token
    throughout its lifecycle (Stage 1.5-A.2.2 R1, Stage 1.5-A.2.2.1 R1).

    Safety Invariant:
    A managed SQLite connection must NEVER release its shared restore lock while
    the underlying SQLite connection may still be open.

    State Model:
    1. Active:
       - _sqlite_closed = False, _lock_token is not None, _is_closed = False.
       - Restore coordination lock is held (exclusive restore is blocked).
    2. SQLite Close Failed:
       - _sqlite_closed = False, _lock_token is not None, _is_closed = False.
       - The restore lock is strictly retained (restore remains blocked).
       - Connection remains in a state where close() can be retried.
       - SQLite close exception is raised without attempting lock release.
    3. SQLite Closed, Lock Release Failed:
       - _sqlite_closed = True, _lock_token is not None, _is_closed = False.
       - SQLite connection is closed and cannot be used.
       - Token release failed; token is retained (not discarded) so error is reported
         and subsequent close() call can retry releasing the lock token.
       - Restore remains blocked until the lock token is released.
    4. Fully Closed:
       - _sqlite_closed = True, _lock_token is None, _is_closed = True.
       - Both SQLite connection and restore lock token are cleanly closed.
       - Subsequent close() calls are no-ops (idempotent).
       - Restore coordination lock can now be acquired exclusively.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lock_token: Optional[DatabaseRestoreLockToken] = None
        self._sqlite_closed: bool = False
        self._is_closed: bool = False
        self._last_sqlite_close_error: Optional[Exception] = None
        self._last_lock_release_error: Optional[Exception] = None

    @property
    def lock_token(self) -> Optional[DatabaseRestoreLockToken]:
        """Returns the held DatabaseRestoreLockToken, if any."""
        return self._lock_token

    @property
    def is_closed(self) -> bool:
        """Returns True only when both SQLite close and lock release have succeeded."""
        return self._is_closed

    @property
    def is_sqlite_closed(self) -> bool:
        """Returns True if the underlying SQLite connection has been closed."""
        return self._sqlite_closed

    def close(self, force_lock_release: bool = False) -> None:
        """
        Closes the SQLite connection and releases the restore lock token (Stage 1.5-A.2.2.1 R1).

        Parameters:
            force_lock_release: If True, attempts to release the lock token even if SQLite close
                                failed (used only for diagnostic composite failure verification).
                                Default is False: never releases lock if SQLite close failed.
        """
        # 1. Idempotent exit if already fully closed
        if self._is_closed:
            return

        sqlite_err: Optional[Exception] = None
        # 2. Close underlying SQLite connection first
        if not self._sqlite_closed:
            try:
                super().close()
                self._sqlite_closed = True
                self._last_sqlite_close_error = None
            except Exception as e:
                sqlite_err = e
                self._last_sqlite_close_error = e
                # Fail-closed invariant: do NOT release lock token unless forced
                if not force_lock_release:
                    if isinstance(e, DatabaseError):
                        raise e
                    raise DatabaseConnectionError(f"Failed to close SQLite connection: {e}") from e

        # 3. Only after SQLite close succeeds (or forced), attempt to release restore lock token
        lock_err: Optional[Exception] = None
        if self._lock_token is not None:
            try:
                self._lock_token.release()
                self._lock_token = None
                self._last_lock_release_error = None
            except Exception as e:
                lock_err = e
                self._last_lock_release_error = e
                # Do NOT discard the token; retain reference so error is diagnosable and can be retried

        # 4. Error evaluation and reporting
        if sqlite_err is not None and lock_err is not None:
            err = DatabaseConnectionError(
                f"Both SQLite connection close and restore lock release failed: "
                f"sqlite_err={sqlite_err}; lock_err={lock_err}"
            )
            err.__cause__ = sqlite_err
            err.lock_release_error = lock_err
            raise err
        elif sqlite_err is not None:
            if isinstance(sqlite_err, DatabaseError):
                raise sqlite_err
            raise DatabaseConnectionError(f"Failed to close SQLite connection: {sqlite_err}") from sqlite_err
        elif lock_err is not None:
            if isinstance(lock_err, DatabaseError):
                raise lock_err
            raise DatabaseConnectionError(
                f"SQLite connection closed successfully, but restore lock release failed: {lock_err}"
            ) from lock_err

        # 5. Successfully closed both SQLite and restore lock token
        self._is_closed = True


# ---------------------------------------------------------------------------
# Configuration Profiles
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DatabaseProfile:
    """Immutable database configuration profile."""
    name: str
    default_path: str
    journal_mode: str = "WAL"
    synchronous: str = "FULL"  # "FULL" (2) or "NORMAL" (1)
    foreign_keys: bool = True
    busy_timeout_ms: int = 5000
    trusted_schema: bool = False
    page_size: int = 4096
    auto_vacuum: str = "INCREMENTAL"


# Authoritative financial database profile: durability prioritized (FULL synchronous)
PRIMARY_FINANCIAL_PROFILE = DatabaseProfile(
    name="primary_financial",
    default_path=os.path.join("context", "gem.db"),
    journal_mode="WAL",
    synchronous="FULL",
    foreign_keys=True,
    busy_timeout_ms=5000,
    trusted_schema=False,
    page_size=4096,
    auto_vacuum="INCREMENTAL",
)

# Reconstructible market-data cache profile: throughput prioritized (NORMAL synchronous)
MARKET_DATA_CACHE_PROFILE = DatabaseProfile(
    name="market_data_cache",
    default_path=os.path.join("cache", "market_data.db"),
    journal_mode="WAL",
    synchronous="NORMAL",
    foreign_keys=True,
    busy_timeout_ms=5000,
    trusted_schema=False,
    page_size=4096,
    auto_vacuum="INCREMENTAL",
)


# Synchronous mode mapping for PRAGMA verification
_SYNCHRONOUS_MAP = {
    "OFF": 0,
    "NORMAL": 1,
    "FULL": 2,
    "EXTRA": 3,
}


# ---------------------------------------------------------------------------
# DatabaseManager
# ---------------------------------------------------------------------------

class DatabaseManager:
    """
    Manages explicit SQLite database connection lifecycles, configuration PRAGMAs,
    and transactions for a specified database path and configuration profile.

    Does NOT maintain a global persistent connection; connections are created on demand,
    configured strictly, and closed explicitly or via context managers.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        profile: DatabaseProfile = PRIMARY_FINANCIAL_PROFILE,
    ):
        self.profile = profile
        self.db_path = db_path or profile.default_path

    def connect(self) -> sqlite3.Connection:
        """
        Creates and configures a new SQLite connection according to the active profile.
        Verifies that all required PRAGMAs are effectively applied before returning.
        Acquires and attaches a connection-lifetime shared restore lock token (R1).
        """
        # Ensure parent directory exists for file-backed databases
        if self.db_path != ":memory:" and not self.db_path.startswith("file:"):
            parent_dir = os.path.dirname(os.path.abspath(self.db_path))
            if parent_dir and not os.path.exists(parent_dir):
                try:
                    os.makedirs(parent_dir, exist_ok=True)
                except OSError as e:
                    raise DatabaseConnectionError(
                        f"Failed to create directory for database '{self.db_path}': {e}"
                    ) from e

        token: Optional[DatabaseRestoreLockToken] = None
        try:
            token = DatabaseRestoreLock.acquire_shared(self.db_path)
        except RestoreLockBusyError as e:
            raise DatabaseBusyError(
                f"Database '{self.db_path}' is locked: currently undergoing offline backup restoration."
            ) from e

        conn: Optional[sqlite3.Connection] = None
        try:
            timeout_sec = max(1.0, self.profile.busy_timeout_ms / 1000.0)
            conn = sqlite3.connect(
                self.db_path,
                timeout=timeout_sec,
                autocommit=True,
                factory=ManagedConnection,
            )
            # Attach lock token to managed connection lifecycle (R1)
            conn._lock_token = token
        except sqlite3.OperationalError as e:
            if token is not None:
                try:
                    token.release()
                except Exception as ce:
                    setattr(e, "lock_release_error", ce)
            err_msg = str(e).lower()
            if "locked" in err_msg or "busy" in err_msg or "unable to open database file" in err_msg:
                raise DatabaseBusyError(f"Database locked or busy '{self.db_path}': {e}") from e
            if "malformed" in err_msg or "corrupt" in err_msg or "not a database" in err_msg:
                raise DatabaseCorruptionError(f"Database corrupted '{self.db_path}': {e}") from e
            raise DatabaseConnectionError(f"Failed to connect to database '{self.db_path}': {e}") from e
        except Exception as e:
            if token is not None:
                try:
                    token.release()
                except Exception as ce:
                    setattr(e, "lock_release_error", ce)
            raise DatabaseConnectionError(f"Unexpected connection error for '{self.db_path}': {e}") from e

        # Configure connection PRAGMAs and verify their effectiveness
        try:
            self._apply_and_verify_pragmas(conn)
        except Exception as e:
            try:
                conn.close()
            except Exception as ce:
                setattr(e, "connection_close_error", ce)
            raise

        return conn

    def _apply_and_verify_pragmas(self, conn: sqlite3.Connection) -> None:
        """Applies profile PRAGMAs and verifies their effective values."""
        cursor = conn.cursor()

        # Database-level settings (applied outside transactions)
        # Note: page_size and auto_vacuum only take effect on empty/newly created databases
        try:
            cursor.execute(f"PRAGMA page_size = {self.profile.page_size};")
            cursor.execute(f"PRAGMA auto_vacuum = {self.profile.auto_vacuum};")

            # In-memory databases do not support WAL mode; only configure WAL for file-backed DBs
            if self.db_path != ":memory:":
                cursor.execute(f"PRAGMA journal_mode = {self.profile.journal_mode};")
                jm_res = cursor.fetchone()
                if jm_res and jm_res[0].upper() != self.profile.journal_mode.upper():
                    raise DatabaseConfigurationError(
                        f"Failed to set journal_mode={self.profile.journal_mode}. Effective: {jm_res[0]}"
                    )
        except sqlite3.OperationalError as e:
            err_msg = str(e).lower()
            if "locked" in err_msg or "busy" in err_msg:
                raise DatabaseBusyError(f"Database busy during journal_mode configuration: {e}") from e
            raise DatabaseConfigurationError(f"Database-level PRAGMA configuration failed: {e}") from e

        # Per-connection settings (mandatory on EVERY connection)
        try:
            # 1. Foreign Key Enforcement
            cursor.execute(f"PRAGMA foreign_keys = {'ON' if self.profile.foreign_keys else 'OFF'};")
            fk_res = cursor.execute("PRAGMA foreign_keys;").fetchone()
            expected_fk = 1 if self.profile.foreign_keys else 0
            if fk_res and fk_res[0] != expected_fk:
                raise DatabaseConfigurationError(
                    f"Failed to set foreign_keys={expected_fk}. Effective: {fk_res[0]}"
                )

            # 2. Busy Timeout
            cursor.execute(f"PRAGMA busy_timeout = {self.profile.busy_timeout_ms};")
            bt_res = cursor.execute("PRAGMA busy_timeout;").fetchone()
            if bt_res and bt_res[0] != self.profile.busy_timeout_ms:
                raise DatabaseConfigurationError(
                    f"Failed to set busy_timeout={self.profile.busy_timeout_ms}. Effective: {bt_res[0]}"
                )

            # 3. Trusted Schema (Security Hardening)
            cursor.execute(f"PRAGMA trusted_schema = {'ON' if self.profile.trusted_schema else 'OFF'};")
            ts_res = cursor.execute("PRAGMA trusted_schema;").fetchone()
            expected_ts = 1 if self.profile.trusted_schema else 0
            if ts_res and ts_res[0] != expected_ts:
                raise DatabaseConfigurationError(
                    f"Failed to set trusted_schema={expected_ts}. Effective: {ts_res[0]}"
                )

            # 4. Synchronous Mode (FULL vs NORMAL)
            cursor.execute(f"PRAGMA synchronous = {self.profile.synchronous};")
            sync_res = cursor.execute("PRAGMA synchronous;").fetchone()
            expected_sync = _SYNCHRONOUS_MAP.get(self.profile.synchronous.upper(), 2)
            if sync_res and sync_res[0] != expected_sync:
                raise DatabaseConfigurationError(
                    f"Failed to set synchronous={self.profile.synchronous} (expected {expected_sync}). "
                    f"Effective: {sync_res[0]}"
                )

        except sqlite3.OperationalError as e:
            err_msg = str(e).lower()
            if "locked" in err_msg or "busy" in err_msg:
                raise DatabaseBusyError(f"Database busy during connection PRAGMA configuration: {e}") from e
            if "malformed" in err_msg or "corrupt" in err_msg:
                raise DatabaseCorruptionError(f"Database corrupted during PRAGMA setup: {e}") from e
            raise DatabaseConfigurationError(f"Connection PRAGMA configuration failed: {e}") from e

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        """
        Context manager for acquiring a managed connection.
        Closes the connection safely upon context exit.
        Reports connection close errors without silent suppression (R5).
        """
        conn = self.connect()
        close_err = None
        try:
            yield conn
        finally:
            try:
                conn.close()
            except Exception as ce:
                close_err = ce
            if close_err is not None:
                exc_type, exc_val, _ = sys.exc_info()
                if exc_type is not None:
                    try:
                        setattr(exc_val, "connection_close_error", close_err)
                    except Exception:
                        raise close_err from exc_val
                else:
                    if isinstance(close_err, DatabaseError):
                        raise close_err
                    raise DatabaseConnectionError(f"Failed to close connection: {close_err}") from close_err

    @contextmanager
    def transaction(self, conn: Optional[sqlite3.Connection] = None) -> Generator[sqlite3.Connection, None, None]:
        """
        Explicit ACID transaction context manager using BEGIN IMMEDIATE.
        Acquires write lock immediately to prevent deadlock in concurrent write flows.
        Commits on clean exit, rolls back on exception, and cleans up resources.
        Reports rollback and close errors without silent suppression (R5).

        Nested transactions on the same connection are strictly rejected to prevent
        ambiguous commit boundaries.
        """
        owns_connection = False
        if conn is None:
            conn = self.connect()
            owns_connection = True
        elif conn.in_transaction:
            raise DatabaseTransactionError(
                "Nested transactions are not supported; transaction already active on connection."
            )

        try:
            conn.execute("BEGIN IMMEDIATE;")
        except sqlite3.OperationalError as e:
            err_msg = str(e).lower()
            if owns_connection:
                try:
                    conn.close()
                except Exception as ce:
                    setattr(e, "connection_close_error", ce)
            if "locked" in err_msg or "busy" in err_msg:
                raise DatabaseBusyError(f"Database locked attempting BEGIN IMMEDIATE: {e}") from e
            if "malformed" in err_msg or "corrupt" in err_msg:
                raise DatabaseCorruptionError(f"Database corrupted attempting transaction: {e}") from e
            raise DatabaseTransactionError(f"Failed to begin immediate transaction: {e}") from e

        rollback_err = None
        try:
            yield conn
            conn.execute("COMMIT;")
        except Exception as exc:
            try:
                conn.execute("ROLLBACK;")
            except Exception as re:
                rollback_err = re
                setattr(exc, "rollback_error", rollback_err)

            if rollback_err is not None:
                tie = DatabaseTransactionError(
                    f"Transaction failed and rollback also failed: rollback_err={rollback_err}; primary_err={exc}"
                )
                tie.__cause__ = exc
                tie.rollback_error = rollback_err
                raise tie from exc

            if isinstance(exc, DatabaseError):
                raise
            if isinstance(exc, sqlite3.OperationalError):
                err_msg = str(exc).lower()
                if "locked" in err_msg or "busy" in err_msg:
                    raise DatabaseBusyError(f"Database busy during transaction: {exc}") from exc
                if "malformed" in err_msg or "corrupt" in err_msg:
                    raise DatabaseCorruptionError(f"Database corrupted during transaction: {exc}") from exc
                raise DatabaseTransactionError(f"Transaction failed with operational error: {exc}") from exc
            raise
        finally:
            if owns_connection:
                close_err = None
                try:
                    conn.close()
                except Exception as ce:
                    close_err = ce
                if close_err is not None:
                    exc_type, exc_val, _ = sys.exc_info()
                    if exc_type is not None:
                        try:
                            setattr(exc_val, "connection_close_error", close_err)
                        except Exception:
                            raise close_err from exc_val
                    else:
                        raise DatabaseConnectionError(
                            f"Failed to close owned transaction connection: {close_err}"
                        ) from close_err

    def checkpoint(self, mode: str = "PASSIVE") -> Tuple[int, int, int]:
        """
        Executes a WAL checkpoint.
        Valid modes: PASSIVE, FULL, RESTART, TRUNCATE.
        Returns a tuple: (busy_flag, log_frames, checkpointed_frames).
        """
        valid_modes = {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}
        if mode.upper() not in valid_modes:
            raise ValueError(f"Invalid checkpoint mode '{mode}'. Expected one of {valid_modes}")

        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"PRAGMA wal_checkpoint({mode.upper()});")
            row = cursor.fetchone()
            if row is None:
                return (0, 0, 0)
            return (int(row[0]), int(row[1]), int(row[2]))

    def verify_integrity(self) -> List[str]:
        """
        Executes PRAGMA integrity_check to verify physical database page integrity.
        Returns ['ok'] if healthy, or a list of diagnostic corruption strings.
        """
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA integrity_check;")
            rows = cursor.fetchall()
            return [r[0] for r in rows if r]
