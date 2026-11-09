"""
SQLite Online Backup Infrastructure
Governed by Stage 1.3 Directive and Stage 1.2.1 / 1.3.1 Architecture Corrections.

Provides atomic, consistent database snapshots using SQLite's native Online Backup API
(`sqlite3.Connection.backup()`). Guarantees consistent point-in-time snapshots
even when the source database is in active WAL mode with concurrent readers/writers.
"""

import os
import sys
import enum
import ctypes
import sqlite3
import datetime
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional, Callable, Dict, Any, List, Union

if sys.platform == "win32":
    import msvcrt
    from ctypes import wintypes

    class _OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_ulong),
            ("InternalHigh", ctypes.c_ulong),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]
else:
    class _OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_ulong),
            ("InternalHigh", ctypes.c_ulong),
            ("Offset", ctypes.c_ulong),
            ("OffsetHigh", ctypes.c_ulong),
            ("hEvent", ctypes.c_void_p),
        ]


# ---------------------------------------------------------------------------
# Backup Exception Hierarchy
# ---------------------------------------------------------------------------

class BackupError(Exception):
    """Base exception for database backup operations."""
    pass


class BackupDestinationExistsError(BackupError):
    """Raised when the backup destination already exists and overwrite is False."""
    pass


class BackupIntegrityError(BackupError):
    """Raised when an exported backup fails PRAGMA integrity_check."""
    pass


class BackupQuiescenceError(BackupError):
    """Raised when target database quiescence cannot be established (R1, R2, R3)."""
    pass


class RestoreLockBusyError(BackupError):
    """Raised when the database is locked by an active backup restore operation (R3)."""
    pass


class BackupRecoveryError(BackupError):
    """Raised when restoration replacement failed and recovery of original target also failed (R4)."""
    def __init__(self, message: str, target_path: str, fallback_path: str):
        super().__init__(message)
        self.target_path = target_path
        self.fallback_path = fallback_path


class BackupCleanupError(BackupError):
    """Raised when cleanup of temporary files, handles, or preserved fallback backups fails (R4)."""
    def __init__(self, message: str, cleanup_errors: Optional[List[Exception]] = None):
        super().__init__(message)
        self.cleanup_errors = cleanup_errors or []


# ---------------------------------------------------------------------------
# Coordinated Restore Locking Infrastructure (R1, R2, R3, R4, Stage 1.5-A.2.2.2)
# ---------------------------------------------------------------------------

class LockTokenState(str, enum.Enum):
    """
    Truthful lifecycle states for DatabaseRestoreLockToken (Stage 1.5-A.2.2.2 R2).

    States:
    - HELD: The kernel lock is actively held by the open handle.
    - UNLOCK_FAILED: The OS unlock call failed; handle remains open; kernel lock is still held.
    - UNLOCKED_BUT_CLOSE_FAILED: OS unlock succeeded; handle close failed; kernel lock is NOT held.
    - FULLY_RELEASED: Both OS unlock and handle close succeeded cleanly.
    - UNCERTAIN: Release state cannot be determined with certainty; exclusion cannot be proven.
    """
    HELD = "HELD"
    UNLOCK_FAILED = "UNLOCK_FAILED"
    UNLOCKED_BUT_CLOSE_FAILED = "UNLOCKED_BUT_CLOSE_FAILED"
    FULLY_RELEASED = "FULLY_RELEASED"
    UNCERTAIN = "UNCERTAIN"


class DatabaseRestoreLockToken:
    """
    Active lock token representing an acquired exclusive or shared restore lock (R3/R4/Stage 1.5-A.2.2.2 R2).
    Tracks a truthful lifecycle state machine across OS unlock and handle close.
    """

    def __init__(
        self,
        db_path: str,
        lock_path: str,
        f,
        handle,
        is_exclusive: bool,
        fileno: int = -1,
    ):
        self.db_path = db_path
        self.lock_path = lock_path
        self._f = f
        self._handle = handle
        self._fileno = fileno
        self.is_exclusive = is_exclusive
        self._state = LockTokenState.HELD
        self._ov = _OVERLAPPED() if sys.platform == "win32" else None
        self._unlock_error: Optional[Exception] = None
        self._close_error: Optional[Exception] = None

    @property
    def state(self) -> LockTokenState:
        """Returns the current truthful lifecycle state of the token."""
        return self._state

    @property
    def is_held(self) -> bool:
        """
        Returns True if and only if the underlying OS kernel lock is established as held.
        Returns True for HELD and UNLOCK_FAILED (where unlock failed and handle remains open).
        Returns False once OS unlock succeeds (even if file-handle close subsequently fails).
        Returns False for UNCERTAIN (cannot claim exclusion that cannot be proven).
        """
        return self._state in (LockTokenState.HELD, LockTokenState.UNLOCK_FAILED)

    @property
    def _is_held(self) -> bool:
        """Backwards compatibility alias for is_held."""
        return self.is_held

    @property
    def is_released(self) -> bool:
        """Returns True if and only if both OS unlock and handle close have succeeded cleanly."""
        return self._state == LockTokenState.FULLY_RELEASED

    @property
    def is_uncertain(self) -> bool:
        """Returns True if the release state could not be determined with certainty."""
        return self._state == LockTokenState.UNCERTAIN

    @property
    def unlock_error(self) -> Optional[Exception]:
        """Returns the exception from the most recent OS unlock failure, if any."""
        return self._unlock_error

    @property
    def close_error(self) -> Optional[Exception]:
        """Returns the exception from the most recent file handle close failure, if any."""
        return self._close_error

    def release(self, unlink_on_release: bool = False) -> None:
        """
        Releases the held lock according to a truthful state machine (Stage 1.5-A.2.2.2 R2).

        Distinguishes:
        - HELD -> initial state.
        - UNLOCK_FAILED -> OS unlock returned error; handle remains open; lock is still held.
        - UNLOCKED_BUT_CLOSE_FAILED -> OS unlock succeeded; handle close failed; lock is NOT held.
        - FULLY_RELEASED -> Both OS unlock and handle close succeeded cleanly.
        - UNCERTAIN -> Release state cannot be established reliably.

        Preserves open handles on failure to support safe retry and diagnostics.
        Does NOT re-invoke OS unlock if unlock has already succeeded.
        Raises BackupCleanupError if unlock or close fails.
        """
        if self._state == LockTokenState.FULLY_RELEASED:
            return

        cleanup_errors: List[Exception] = []

        # Step 1: OS Unlock (only if not already successfully unlocked)
        if self._state in (LockTokenState.HELD, LockTokenState.UNLOCK_FAILED):
            if sys.platform == "win32":
                try:
                    res = ctypes.windll.kernel32.UnlockFileEx(
                        self._handle, 0, 0xFFFFFFFF, 0xFFFFFFFF, ctypes.byref(self._ov)
                    )
                    if res == 0:
                        err = ctypes.windll.kernel32.GetLastError()
                        unlock_err = OSError(f"UnlockFileEx failed (WinError {err})")
                        self._unlock_error = unlock_err
                        self._state = LockTokenState.UNLOCK_FAILED
                        cleanup_errors.append(unlock_err)
                    else:
                        self._unlock_error = None
                        self._state = LockTokenState.UNLOCKED_BUT_CLOSE_FAILED
                except Exception as e:
                    self._unlock_error = e
                    self._state = LockTokenState.UNCERTAIN
                    cleanup_errors.append(e)
            else:
                import fcntl
                try:
                    fcntl.flock(self._fileno, fcntl.LOCK_UN)
                    self._unlock_error = None
                    self._state = LockTokenState.UNLOCKED_BUT_CLOSE_FAILED
                except Exception as e:
                    self._unlock_error = e
                    self._state = LockTokenState.UNLOCK_FAILED
                    cleanup_errors.append(e)

        # If unlock failed or is uncertain, keep handles open and fail closed immediately.
        # Do not close the file handle if unlock failed or state is uncertain.
        if self._state in (LockTokenState.UNLOCK_FAILED, LockTokenState.UNCERTAIN):
            err_msg = "; ".join(str(e) for e in cleanup_errors)
            exc = BackupCleanupError(
                f"Failed to cleanly release restore lock for '{self.db_path}': OS unlock failed: {err_msg}",
                cleanup_errors=cleanup_errors,
            )
            DatabaseRestoreLockQuarantine.quarantine(self, exc)
            raise exc

        # Step 2: File Handle Close (unlock has succeeded)
        if self._f is not None:
            is_closed = getattr(self._f, "closed", False)
            if not is_closed:
                try:
                    self._f.close()
                    self._close_error = None
                except Exception as e:
                    self._close_error = e
                    self._state = LockTokenState.UNLOCKED_BUT_CLOSE_FAILED
                    cleanup_errors.append(e)
                    err_msg = "; ".join(str(e) for e in cleanup_errors)
                    exc = BackupCleanupError(
                        f"Failed to cleanly release restore lock for '{self.db_path}': OS unlock succeeded, but file close failed: {err_msg}",
                        cleanup_errors=cleanup_errors,
                    )
                    DatabaseRestoreLockQuarantine.quarantine(self, exc)
                    raise exc

        # Both OS unlock and file close succeeded
        self._state = LockTokenState.FULLY_RELEASED
        self._f = None
        self._handle = None
        DatabaseRestoreLockQuarantine.remove(self)


# ---------------------------------------------------------------------------
# Failed Restore Lock Quarantine Registry (Stage 1.5-A.2.2.3 R2, R3, R4)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class QuarantinedTokenInfo:
    """Read-only diagnostic snapshot of a quarantined lock token (Stage 1.5-A.2.2.3 R2)."""
    db_path: str
    lock_path: str
    token_state: LockTokenState
    is_exclusive: bool
    is_held: bool
    is_uncertain: bool
    quarantined_at_utc: str
    error_message: str
    retry_count: int


class _QuarantineEntry:
    """Internal registry record holding a strong reference and retry lock for a quarantined token."""
    def __init__(self, token: DatabaseRestoreLockToken, error: Exception):
        self.token = token
        self.db_path = token.db_path
        self.lock_path = token.lock_path
        self.error = error
        self.quarantined_at_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.retry_count = 0
        self.retry_lock = threading.Lock()


class DatabaseRestoreLockQuarantine:
    """
    Process-level quarantine registry for DatabaseRestoreLockTokens whose release failed (Stage 1.5-A.2.2.3).

    Maintains strong references to failed tokens and their underlying file handles to prevent
    garbage collection from silently closing the OS handle and dropping kernel locks.

    Provides thread-safe registration, explicit retry mechanisms, and read-only diagnostics.
    """
    _registry: Dict[int, _QuarantineEntry] = {}
    _lock: threading.Lock = threading.Lock()

    @classmethod
    def quarantine(cls, token: DatabaseRestoreLockToken, error: Exception) -> None:
        """
        Registers a token whose release failed into quarantine (R2, R4).
        Maintains a strong reference to the token and its OS handle.
        Thread-safe; prevents duplicate registration of the same token instance.
        """
        if token.is_released:
            return

        with cls._lock:
            token_id = id(token)
            if token_id not in cls._registry:
                cls._registry[token_id] = _QuarantineEntry(token, error)
            else:
                entry = cls._registry[token_id]
                entry.error = error

    @classmethod
    def remove(cls, token: DatabaseRestoreLockToken) -> bool:
        """
        Removes a token from quarantine if and only if it is demonstrably FULLY_RELEASED.
        Returns True if removed, False otherwise.
        """
        if not token.is_released or token.state != LockTokenState.FULLY_RELEASED:
            return False

        with cls._lock:
            token_id = id(token)
            entry = cls._registry.get(token_id)
            if (
                entry is not None
                and entry.token.is_released
                and entry.token.state == LockTokenState.FULLY_RELEASED
            ):
                del cls._registry[token_id]
                return True
            return False

    @classmethod
    def is_quarantined(cls, token: DatabaseRestoreLockToken) -> bool:
        """Returns True if the specified token instance is currently quarantined."""
        with cls._lock:
            return id(token) in cls._registry

    @classmethod
    def has_quarantined_tokens(cls, db_path: Optional[str] = None) -> bool:
        """Returns True if any tokens are currently quarantined (optionally filtered by db_path)."""
        with cls._lock:
            if db_path is None:
                return len(cls._registry) > 0
            norm_target = os.path.abspath(db_path)
            return any(os.path.abspath(entry.db_path) == norm_target for entry in cls._registry.values())

    @classmethod
    def count(cls, db_path: Optional[str] = None) -> int:
        """Returns the number of quarantined tokens (optionally filtered by db_path)."""
        with cls._lock:
            if db_path is None:
                return len(cls._registry)
            norm_target = os.path.abspath(db_path)
            return sum(1 for entry in cls._registry.values() if os.path.abspath(entry.db_path) == norm_target)

    @classmethod
    def get_quarantined_tokens(cls, db_path: Optional[str] = None) -> List[QuarantinedTokenInfo]:
        """
        Returns a read-only list of diagnostic snapshots of quarantined tokens.
        Does not expose mutable token references directly.
        """
        with cls._lock:
            results: List[QuarantinedTokenInfo] = []
            for entry in cls._registry.values():
                if db_path is not None and os.path.abspath(entry.db_path) != os.path.abspath(db_path):
                    continue
                results.append(
                    QuarantinedTokenInfo(
                        db_path=entry.db_path,
                        lock_path=entry.lock_path,
                        token_state=entry.token.state,
                        is_exclusive=entry.token.is_exclusive,
                        is_held=entry.token.is_held,
                        is_uncertain=entry.token.is_uncertain,
                        quarantined_at_utc=entry.quarantined_at_utc,
                        error_message=str(entry.error),
                        retry_count=entry.retry_count,
                    )
                )
            return results

    @classmethod
    def get_token(cls, token_id: int) -> Optional[DatabaseRestoreLockToken]:
        """Returns the token instance for a given token_id, if quarantined."""
        with cls._lock:
            entry = cls._registry.get(token_id)
            return entry.token if entry else None

    @classmethod
    def retry_release(cls, token: DatabaseRestoreLockToken) -> bool:
        """
        Retries releasing a quarantined token (R3).
        Operates on the existing token without creating replacement locks.
        Guards against concurrent retries on the same token.
        Removes token from quarantine only upon confirmed is_released == True.
        Raises BackupCleanupError if retry fails, keeping token quarantined.
        """
        token_id = id(token)
        entry: Optional[_QuarantineEntry] = None
        with cls._lock:
            entry = cls._registry.get(token_id)

        if entry is None:
            # Token is not quarantined; if already released return True, else release directly
            if token.is_released:
                return True
            token.release()
            return token.is_released

        # Prevent concurrent retries on the same token
        if not entry.retry_lock.acquire(blocking=False):
            raise BackupCleanupError(
                f"Retry release already in progress for quarantined token on '{entry.db_path}'."
            )

        try:
            entry.retry_count += 1
            try:
                entry.token.release()
            except Exception as e:
                entry.error = e
                raise

            if entry.token.is_released and entry.token.state == LockTokenState.FULLY_RELEASED:
                cls.remove(entry.token)
                return True
            return False
        finally:
            entry.retry_lock.release()

    @classmethod
    def retry_all(cls, db_path: Optional[str] = None) -> Dict[int, bool]:
        """
        Retries release for all quarantined tokens (optionally filtered by db_path).
        Returns a dict mapping token_id to release success (True/False).
        """
        with cls._lock:
            token_ids = [
                tid for tid, entry in cls._registry.items()
                if db_path is None or os.path.abspath(entry.db_path) == os.path.abspath(db_path)
            ]

        results: Dict[int, bool] = {}
        for tid in token_ids:
            with cls._lock:
                entry = cls._registry.get(tid)
            if entry is None:
                continue
            try:
                success = cls.retry_release(entry.token)
                results[tid] = success
            except Exception:
                results[tid] = False

        return results

    @classmethod
    def clear_for_test(cls) -> None:
        """
        Remove only fully released tokens from the quarantine registry.

        Unreleased, uncertain, or partially released tokens must remain strongly
        referenced. Raises RuntimeError with diagnostics if any unreleased or
        uncertain token entries remain.
        """
        with cls._lock:
            released_ids = []
            unreleased: Dict[int, _QuarantineEntry] = {}

            for token_id, entry in list(cls._registry.items()):
                token = entry.token
                if token.is_released and token.state == LockTokenState.FULLY_RELEASED:
                    released_ids.append(token_id)
                else:
                    unreleased[token_id] = entry

            for token_id in released_ids:
                current_entry = cls._registry.get(token_id)
                if (
                    current_entry is not None
                    and current_entry.token.is_released
                    and current_entry.token.state == LockTokenState.FULLY_RELEASED
                ):
                    del cls._registry[token_id]

            if unreleased:
                diagnostics = []
                for token_id, entry in unreleased.items():
                    token = entry.token
                    state_obj = getattr(token, "state", "unknown")
                    state_val = state_obj.value if isinstance(state_obj, enum.Enum) else str(state_obj)
                    db_path = getattr(token, "db_path", getattr(entry, "db_path", "unknown"))
                    diagnostics.append(
                        f"token_id={token_id}, state={state_val}, db_path={db_path}"
                    )

                raise RuntimeError(
                    "Cannot clear restore-lock quarantine: "
                    f"{len(unreleased)} unreleased or uncertain token(s) remain. "
                    + "; ".join(diagnostics)
                )


class DatabaseRestoreLock:
    """
    Coordinated cross-process restore lock for SQLite databases (R1, R2, R3, R4).

    Eliminates TOCTOU connection-restoration races by establishing an unambiguous,
    OS-enforced reader-writer lock between DatabaseManager connection creation (shared)
    and BackupManager offline restoration (exclusive).

    The coordination lock file (<db_path>.restore_lock) is treated as a persistent
    zero-byte artifact (R4). Locking is governed strictly by kernel-level byte-range/advisory
    locks (LockFileEx on Windows, fcntl.flock on POSIX), without reliance on file size or text content.
    """

    @classmethod
    def get_lock_path(cls, db_path: str) -> str:
        return f"{os.path.abspath(db_path)}.restore_lock"

    @classmethod
    def acquire_shared(cls, db_path: str) -> Optional[DatabaseRestoreLockToken]:
        """
        Shared acquisition gate for database connection creation (R1, R3, R4).
        Guarantees that new connection creation cannot race with active restoration.
        The token is held across the entire lifetime of the returned ManagedConnection.
        Raises RestoreLockBusyError if restoration is currently active or lock cannot be acquired.
        """
        if db_path == ":memory:" or str(db_path).startswith("file:"):
            return None

        lock_path = cls.get_lock_path(db_path)
        lock_dir = os.path.dirname(lock_path)
        if lock_dir and not os.path.exists(lock_dir):
            try:
                os.makedirs(lock_dir, exist_ok=True)
            except OSError as e:
                raise BackupError(
                    f"Failed to create directory for restore lock '{lock_dir}': {e}"
                ) from e

        if sys.platform == "win32":
            import msvcrt
            f = None
            try:
                f = open(lock_path, "a+b")
                h = msvcrt.get_osfhandle(f.fileno())
                ov = _OVERLAPPED()
                # SHARED lock: LOCKFILE_FAIL_IMMEDIATELY without LOCKFILE_EXCLUSIVE_LOCK
                ok = ctypes.windll.kernel32.LockFileEx(
                    h, 0x00000001, 0, 0xFFFFFFFF, 0xFFFFFFFF, ctypes.byref(ov)
                )
                if not ok:
                    err = ctypes.windll.kernel32.GetLastError()
                    close_ce = None
                    try:
                        f.close()
                    except Exception as ce:
                        close_ce = ce
                    busy_err = RestoreLockBusyError(
                        f"Database '{db_path}' is locked: currently undergoing offline backup restoration (WinError {err})."
                    )
                    if close_ce is not None:
                        setattr(busy_err, "file_close_error", close_ce)
                    raise busy_err
                return DatabaseRestoreLockToken(db_path, lock_path, f, h, is_exclusive=False)
            except (OSError, RestoreLockBusyError) as e:
                close_ce = None
                if f is not None and not f.closed:
                    try:
                        f.close()
                    except Exception as ce:
                        close_ce = ce
                if close_ce is not None:
                    setattr(e, "file_close_error", close_ce)
                if isinstance(e, RestoreLockBusyError):
                    raise
                busy_err = RestoreLockBusyError(
                    f"Database '{db_path}' is locked: currently undergoing offline backup restoration: {e}"
                )
                if close_ce is not None:
                    setattr(busy_err, "file_close_error", close_ce)
                raise busy_err from e
        else:
            import fcntl
            f = None
            try:
                f = open(lock_path, "a+b")
                fileno = f.fileno()
                fcntl.flock(fileno, fcntl.LOCK_SH | fcntl.LOCK_NB)
                return DatabaseRestoreLockToken(db_path, lock_path, f, None, is_exclusive=False, fileno=fileno)
            except (IOError, OSError) as e:
                close_ce = None
                if f is not None and not f.closed:
                    try:
                        f.close()
                    except Exception as ce:
                        close_ce = ce
                busy_err = RestoreLockBusyError(
                    f"Database '{db_path}' is locked: currently undergoing offline backup restoration: {e}"
                )
                if close_ce is not None:
                    setattr(busy_err, "file_close_error", close_ce)
                raise busy_err from e

    @classmethod
    @contextmanager
    def shared_gate(cls, db_path: str):
        """
        Shared acquisition gate context manager for database connection creation (R3, R4).
        Maintained for backwards-compatibility.
        Reports lock release failures without silent suppression (Stage 1.5-A.2.2.1 R4).
        """
        token = cls.acquire_shared(db_path)
        try:
            yield token
        finally:
            exc_type, exc_val, _ = sys.exc_info()
            if token is not None and not token.is_released:
                try:
                    token.release()
                except Exception as re:
                    DatabaseRestoreLockQuarantine.quarantine(token, re)
                    if exc_type is not None:
                        try:
                            setattr(exc_val, "lock_release_error", re)
                        except Exception:
                            raise re from exc_val
                    else:
                        raise

    @classmethod
    def acquire_exclusive(cls, db_path: str) -> DatabaseRestoreLockToken:
        """
        Acquires an exclusive restore lock across processes and threads (R1, R2, R3, R4).
        Raises BackupQuiescenceError if target database is currently in use or undergoing restoration.
        """
        lock_path = cls.get_lock_path(db_path)
        lock_dir = os.path.dirname(lock_path)
        if lock_dir and not os.path.exists(lock_dir):
            try:
                os.makedirs(lock_dir, exist_ok=True)
            except OSError as e:
                raise BackupError(f"Failed to create directory for restore lock '{lock_dir}': {e}") from e

        if sys.platform == "win32":
            import msvcrt
            f = None
            try:
                f = open(lock_path, "a+b")
                h = msvcrt.get_osfhandle(f.fileno())
                ov = _OVERLAPPED()
                # EXCLUSIVE lock: LOCKFILE_FAIL_IMMEDIATELY | LOCKFILE_EXCLUSIVE_LOCK
                ok = ctypes.windll.kernel32.LockFileEx(
                    h, 0x00000001 | 0x00000002, 0, 0xFFFFFFFF, 0xFFFFFFFF, ctypes.byref(ov)
                )
                if not ok:
                    err = ctypes.windll.kernel32.GetLastError()
                    close_ce = None
                    try:
                        f.close()
                    except Exception as ce:
                        close_ce = ce
                    quiescence_err = BackupQuiescenceError(
                        f"Database '{db_path}' cannot be safely restored: "
                        f"Restore coordination lock is currently held by an active connection or restore process (WinError {err})."
                    )
                    if close_ce is not None:
                        setattr(quiescence_err, "file_close_error", close_ce)
                    raise quiescence_err
                return DatabaseRestoreLockToken(db_path, lock_path, f, h, is_exclusive=True)
            except (OSError, BackupQuiescenceError) as e:
                close_ce = None
                if f is not None and not f.closed:
                    try:
                        f.close()
                    except Exception as ce:
                        close_ce = ce
                if close_ce is not None:
                    setattr(e, "file_close_error", close_ce)
                if isinstance(e, BackupQuiescenceError):
                    raise
                quiescence_err = BackupQuiescenceError(
                    f"Database '{db_path}' cannot be safely restored: Failed to acquire exclusive restore lock: {e}"
                )
                if close_ce is not None:
                    setattr(quiescence_err, "file_close_error", close_ce)
                raise quiescence_err from e
        else:
            import fcntl
            f = None
            try:
                f = open(lock_path, "a+b")
                fileno = f.fileno()
                fcntl.flock(fileno, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return DatabaseRestoreLockToken(db_path, lock_path, f, None, is_exclusive=True, fileno=fileno)
            except (IOError, OSError) as e:
                close_ce = None
                if f is not None and not f.closed:
                    try:
                        f.close()
                    except Exception as ce:
                        close_ce = ce
                quiescence_err = BackupQuiescenceError(
                    f"Database '{db_path}' cannot be safely restored: "
                    f"Restore coordination lock is held by another process: {e}"
                )
                if close_ce is not None:
                    setattr(quiescence_err, "file_close_error", close_ce)
                raise quiescence_err from e


# ---------------------------------------------------------------------------
# Exclusive Target Locking Helpers (R1, R2, R4)
# ---------------------------------------------------------------------------

def _acquire_exclusive_target_lock(file_path: str):
    """
    Acquires an exclusive OS-level file handle to verify quiescence and block concurrent access (R2).
    On Windows: uses kernel32.CreateFileW with FILE_SHARE_DELETE (blocking read/write while allowing rename).
    On POSIX: uses fcntl.flock(LOCK_EX | LOCK_NB). Note: advisory on POSIX (R1).
    Raises BackupQuiescenceError if the file is in use by any active/idle process.
    Returns opaque handle or file object that must be closed to release.
    """
    if not os.path.exists(file_path):
        return None

    abs_path = os.path.abspath(file_path)
    if sys.platform == "win32":
        GENERIC_READ = 0x80000000
        GENERIC_WRITE = 0x40000000
        DELETE = 0x00010000
        FILE_SHARE_DELETE = 4
        OPEN_EXISTING = 3

        handle = ctypes.windll.kernel32.CreateFileW(
            abs_path,
            GENERIC_READ | GENERIC_WRITE | DELETE,
            FILE_SHARE_DELETE,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
        if handle in (-1, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFF):
            err = ctypes.windll.kernel32.GetLastError()
            raise BackupQuiescenceError(
                f"Target database '{file_path}' cannot be safely restored: "
                f"Held open by active or idle connection (WinError {err})."
            )
        return handle
    else:
        try:
            import fcntl
            f = open(abs_path, "r+b")
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except (IOError, OSError) as e:
            raise BackupQuiescenceError(
                f"Target database '{file_path}' cannot be safely restored: "
                f"Held open by active or idle connection: {e}"
            ) from e


def _close_exclusive_target_lock(handle) -> None:
    """
    Closes an acquired exclusive OS file handle without suppressing errors (R4).
    Raises BackupCleanupError if handle release fails.
    """
    if handle is None:
        return
    if sys.platform == "win32":
        res = ctypes.windll.kernel32.CloseHandle(handle)
        if res == 0:
            err = ctypes.windll.kernel32.GetLastError()
            raise BackupCleanupError(f"Failed to close exclusive target lock handle: WinError {err}")
    else:
        import fcntl
        errs: List[str] = []
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except Exception as e:
            errs.append(f"flock(LOCK_UN) failed: {e}")
        try:
            handle.close()
        except Exception as e:
            errs.append(f"close failed: {e}")
        if errs:
            raise BackupCleanupError(f"Failed to release exclusive target lock on POSIX: {'; '.join(errs)}")


# ---------------------------------------------------------------------------
# Backup Result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BackupResult:
    """Metadata describing a completed database backup."""
    source_path: str
    destination_path: str
    file_size_bytes: int
    timestamp_utc: str
    integrity_status: str


# ---------------------------------------------------------------------------
# BackupManager
# ---------------------------------------------------------------------------

class BackupManager:
    """
    Coordinates atomic point-in-time backups using SQLite's native Online Backup API.

    Unlike naive filesystem file copies (which can capture torn pages or fail to copy
    uncheckpointed WAL frames in active WAL mode), the Online Backup API locks read
    pages safely, writes complete consistent pages to the destination, and produces
    a fully self-contained, standalone database file.
    """

    @classmethod
    def _atomic_no_clobber_place(cls, tmp_path: str, dest_path: str) -> None:
        """
        Moves verified temporary database to dest_path atomically and race-safely (R3).
        Guarantees that dest_path cannot overwrite an existing destination file.
        On Windows: os.rename raises FileExistsError if dest_path exists.
        On POSIX: os.link + os.unlink guarantees atomic placement without overwrite.
        Raises BackupDestinationExistsError if dest_path already exists.
        """
        try:
            if sys.platform == "win32":
                os.rename(tmp_path, dest_path)
            else:
                os.link(tmp_path, dest_path)
                os.unlink(tmp_path)
        except FileExistsError as e:
            raise BackupDestinationExistsError(
                f"Target database destination '{dest_path}' was created concurrently during restoration: "
                f"atomic no-clobber placement rejected overwrite. Restored temporary database preserved at '{tmp_path}'."
            ) from e

    @classmethod
    def _place_backup_file(cls, tmp_path: str, dest_path: str, overwrite: bool) -> None:
        """
        Moves the verified temporary backup to dest_path atomically and race-safely.
        If overwrite is False, guarantees that dest_path cannot be replaced even if
        created concurrently between backup start and completion.
        """
        if overwrite:
            # os.replace is an atomic replacement on both Windows NTFS and POSIX
            os.replace(tmp_path, dest_path)
        else:
            # On Windows, os.rename raises FileExistsError if dest_path already exists
            # On POSIX, os.link + os.unlink guarantees atomic placement without overwrite
            try:
                if sys.platform == "win32":
                    os.rename(tmp_path, dest_path)
                else:
                    os.link(tmp_path, dest_path)
                    os.unlink(tmp_path)
            except FileExistsError as e:
                raise BackupDestinationExistsError(
                    f"Backup destination '{dest_path}' already exists and overwrite is False."
                ) from e

    @classmethod
    def create_backup(
        cls,
        source: sqlite3.Connection | str,
        dest_path: str,
        overwrite: bool = False,
        pages_per_step: int = -1,
        progress_callback: Optional[Callable[[int, int, int], None]] = None,
    ) -> BackupResult:
        """
        Creates a consistent point-in-time backup from source to dest_path.

        Parameters:
            source: An active sqlite3.Connection instance or a path to a source database file.
            dest_path: Target filesystem path for the backup database file.
            overwrite: If False, raises BackupDestinationExistsError if dest_path already exists.
            pages_per_step: Number of pages to copy per step (-1 copies all pages in one step).
            progress_callback: Optional callback(status, remaining, total).

        Returns:
            BackupResult with file metadata and verified integrity status.
        """
        dest_path = os.path.abspath(dest_path)

        if os.path.exists(dest_path) and not overwrite:
            raise BackupDestinationExistsError(
                f"Backup destination '{dest_path}' already exists and overwrite is False."
            )

        parent_dir = os.path.dirname(dest_path)
        if parent_dir and not os.path.exists(parent_dir):
            try:
                os.makedirs(parent_dir, exist_ok=True)
            except OSError as e:
                raise BackupError(f"Failed to create backup destination directory '{parent_dir}': {e}") from e

        # Determine source connection
        owns_source_conn = False
        if isinstance(source, str):
            if not os.path.exists(source) and source != ":memory:":
                raise BackupError(f"Source database file does not exist: '{source}'")
            try:
                src_conn = sqlite3.connect(source, autocommit=True)
                owns_source_conn = True
                source_desc = source
            except Exception as e:
                raise BackupError(f"Failed to open source database for backup '{source}': {e}") from e
        elif isinstance(source, sqlite3.Connection):
            src_conn = source
            source_desc = "<active_connection>"
        else:
            raise TypeError(f"Expected sqlite3.Connection or str path, got {type(source).__name__}")

        # Temporary backup target path to ensure atomic destination replacement
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%H%M%S%f")
        tmp_dest = f"{dest_path}.tmp_{os.getpid()}_{now_str}"
        dst_conn = None

        try:
            dst_conn = sqlite3.connect(tmp_dest, autocommit=True)

            # Execute native SQLite Online Backup
            # This locks pages incrementally and handles WAL replay into the destination
            src_conn.backup(dst_conn, pages=pages_per_step, progress=progress_callback)

            # Immediately verify physical integrity of the completed backup
            cursor = dst_conn.cursor()
            cursor.execute("PRAGMA integrity_check;")
            rows = cursor.fetchall()
            integrity_results = [r[0] for r in rows if r]

            if not integrity_results or integrity_results[0] != "ok":
                raise BackupIntegrityError(
                    f"Backup integrity check failed for '{dest_path}': {integrity_results}"
                )

            # Close destination before renaming to avoid Windows file-locking collisions
            dst_conn.close()
            dst_conn = None

            # Atomically place or replace target destination file
            cls._place_backup_file(tmp_dest, dest_path, overwrite=overwrite)

            file_size = os.path.getsize(dest_path)
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

            return BackupResult(
                source_path=source_desc,
                destination_path=dest_path,
                file_size_bytes=file_size,
                timestamp_utc=now_iso,
                integrity_status="ok",
            )

        except Exception as e:
            cleanup_errors = []
            # Clean up destination connection and temporary file on error
            if dst_conn is not None:
                try:
                    dst_conn.close()
                except Exception as ce:
                    cleanup_errors.append(ce)
            if os.path.exists(tmp_dest):
                try:
                    os.unlink(tmp_dest)
                except Exception as ce:
                    cleanup_errors.append(ce)
            if cleanup_errors:
                setattr(e, "cleanup_errors", cleanup_errors)
            if isinstance(e, BackupError):
                raise
            raise BackupError(f"Backup operation failed: {e}") from e

        finally:
            if owns_source_conn and src_conn is not None:
                try:
                    src_conn.close()
                except Exception as ce:
                    if 'e' not in locals():
                        raise BackupCleanupError(
                            f"Backup succeeded but source connection cleanup failed: {ce}"
                        ) from ce

    @staticmethod
    def verify_backup(backup_path: str) -> bool:
        """
        Reopens a completed backup file and executes PRAGMA integrity_check.
        Returns True if healthy; raises BackupIntegrityError or returns False otherwise.
        """
        if not os.path.exists(backup_path):
            raise BackupError(f"Backup file does not exist: '{backup_path}'")

        try:
            conn = sqlite3.connect(backup_path, autocommit=True)
            try:
                cursor = conn.cursor()
                cursor.execute("PRAGMA integrity_check;")
                rows = cursor.fetchall()
                if rows and rows[0][0] == "ok":
                    return True
                raise BackupIntegrityError(f"Integrity verification failed for '{backup_path}': {rows}")
            finally:
                conn.close()
        except Exception as e:
            if isinstance(e, BackupIntegrityError):
                raise
            raise BackupError(f"Failed to verify backup '{backup_path}': {e}") from e

    @classmethod
    def restore_backup(
        cls,
        backup_path: str,
        target_db_path: str,
        pages_per_step: int = -1,
        progress_callback: Optional[Callable[[int, int, int], None]] = None,
    ) -> BackupResult:
        """
        Safely restores a verified SQLite backup snapshot to target_db_path (R3/R4).

        Enforces strict safety contract:
        - Verifies source backup physical integrity before touching the target.
        - Establishes target quiescence before modifying or replacing any target files:
          - Uses OS-level exclusive file access checks to reject restoration fail-closed
            if target is in active or idle use by any connection or process.
          - If target is in WAL mode, checkpoints and transitions safely via PRAGMA journal_mode=DELETE,
            ensuring SQLite engine itself coordinates WAL flush and cleans up -wal and -shm sidecars.
          - Holds persistent exclusive OS lock on target across entire temporary restoration and swap.
          - Creates and holds coordinated restore lock file to notify DatabaseManager.connect().
        - Restores to an isolated temporary database file first and verifies its integrity.
        - Preserves existing target file as rollback fallback during atomic swap.
        - If replacement fails, recovers original target; if recovery fails, NEVER deletes fallback.
        - Verifies the final restored database with PRAGMA integrity_check.
        - Guaranteed accurate reporting of all recovery or cleanup failures.
        """
        backup_path = os.path.abspath(backup_path)
        target_db_path = os.path.abspath(target_db_path)

        if not os.path.exists(backup_path):
            raise BackupError(f"Backup source file does not exist: '{backup_path}'")

        if os.path.isdir(backup_path):
            raise BackupError(f"Backup source path is a directory, not a file: '{backup_path}'")

        file_size = os.path.getsize(backup_path)
        if file_size < 100:
            raise BackupIntegrityError(
                f"Backup file is too small to be a valid SQLite database ({file_size} bytes): '{backup_path}'"
            )

        # Verify SQLite format 3 magic header
        try:
            with open(backup_path, "rb") as f:
                header = f.read(16)
            if not header.startswith(b"SQLite format 3\x00"):
                raise BackupIntegrityError(f"Backup file does not have a valid SQLite header: '{backup_path}'")
        except OSError as e:
            raise BackupError(f"Failed to read backup file header: {e}") from e

        # 1. Verify source database physical integrity before touching target
        cls.verify_backup(backup_path)

        target_dir = os.path.dirname(target_db_path)
        if target_dir and not os.path.exists(target_dir):
            try:
                os.makedirs(target_dir, exist_ok=True)
            except OSError as e:
                raise BackupError(f"Failed to create target directory '{target_dir}': {e}") from e

        # Acquire coordinated exclusive restore lock across processes and threads (R1, R2, R3, R4)
        # Note: Must be acquired BEFORE inspecting target existence to eliminate TOCTOU races (R2)
        restore_lock_token = DatabaseRestoreLock.acquire_exclusive(target_db_path)

        h_target = None
        cleanup_errors: List[Exception] = []
        lock_state_uncertain = False
        wal_path = f"{target_db_path}-wal"
        shm_path = f"{target_db_path}-shm"

        try:
            # 2. Inspect target path AFTER acquiring exclusive restore coordination lock (R2)
            target_exists = os.path.exists(target_db_path)

            # R1 (Option B): Restoration over an existing target on POSIX is strictly refused
            if target_exists and sys.platform != "win32":
                raise BackupQuiescenceError(
                    f"Restoration over an existing target database '{target_db_path}' is not supported on POSIX: "
                    f"operating system advisory locking cannot guarantee exclusive connection exclusion against "
                    f"concurrent SQLite access. Restore to a new target path instead."
                )

            # 3. If target exists, establish and verify target quiescence fail-closed (Windows only)
            if target_exists:
                if os.path.isdir(target_db_path):
                    raise BackupError(f"Target database path is a directory, not a file: '{target_db_path}'")

                # Verify exclusive OS filesystem access to target file and sidecars
                test_h = _acquire_exclusive_target_lock(target_db_path)
                _close_exclusive_target_lock(test_h)

                for s_path in (wal_path, shm_path):
                    if os.path.exists(s_path):
                        test_sh = _acquire_exclusive_target_lock(s_path)
                        _close_exclusive_target_lock(test_sh)

                # Check if target is a valid SQLite database
                is_sqlite = False
                if os.path.getsize(target_db_path) >= 100:
                    try:
                        with open(target_db_path, "rb") as f:
                            hdr = f.read(16)
                        is_sqlite = hdr.startswith(b"SQLite format 3\x00")
                    except OSError:
                        is_sqlite = False

                if is_sqlite:
                    # Check exclusive connection lock and checkpoint WAL if applicable
                    try:
                        target_check = sqlite3.connect(target_db_path, timeout=0.0, autocommit=True)
                    except sqlite3.OperationalError as e:
                        raise BackupQuiescenceError(
                            f"Target database '{target_db_path}' cannot be safely restored: "
                            f"Target database is currently in use or locked: {e}"
                        ) from e

                    try:
                        cur = target_check.cursor()
                        cur.execute("PRAGMA journal_mode;")
                        row = cur.fetchone()
                        jmode = str(row[0]).lower() if row else ""

                        if jmode == "wal":
                            # Checkpoint and truncate WAL
                            cur.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                            cp_row = cur.fetchone()
                            if cp_row and cp_row[0] != 0:
                                raise BackupQuiescenceError(
                                    f"Target database '{target_db_path}' cannot be safely restored: "
                                    f"Active concurrent readers or uncheckpointed writes in WAL mode."
                                )
                            # Transition away from WAL mode so SQLite flushes and unlinks sidecars
                            try:
                                cur.execute("PRAGMA journal_mode=DELETE;")
                                new_jmode = str(cur.fetchone()[0]).lower()
                                if new_jmode != "delete":
                                    raise BackupQuiescenceError(
                                        f"Target database '{target_db_path}' cannot be safely restored: "
                                        f"Failed to transition from WAL mode (remained '{new_jmode}')."
                                    )
                            except sqlite3.OperationalError as e:
                                raise BackupQuiescenceError(
                                    f"Target database '{target_db_path}' cannot be safely restored: "
                                    f"Concurrent connection active preventing WAL transition: {e}"
                                ) from e
                        else:
                            # Rollback journal mode: verify exclusive locking
                            try:
                                cur.execute("PRAGMA locking_mode=EXCLUSIVE;")
                                cur.execute("BEGIN EXCLUSIVE;")
                                cur.execute("COMMIT;")
                            except sqlite3.OperationalError as e:
                                raise BackupQuiescenceError(
                                    f"Target database '{target_db_path}' cannot be safely restored: "
                                    f"Database is locked by another connection: {e}"
                                ) from e
                    finally:
                        target_check.close()
                else:
                    # If target is non-SQLite (e.g. dummy/corrupted file), clean up any stale sidecars safely
                    for s_path in (wal_path, shm_path):
                        if os.path.exists(s_path):
                            try:
                                os.unlink(s_path)
                            except (PermissionError, OSError) as e:
                                raise BackupQuiescenceError(
                                    f"Target database '{target_db_path}' cannot be safely restored: "
                                    f"Sidecar '{s_path}' is held open by another process: {e}"
                                ) from e

                # Verify no sidecar files remain active
                if os.path.exists(wal_path) or os.path.exists(shm_path):
                    raise BackupQuiescenceError(
                        f"Target database '{target_db_path}' cannot be safely restored: "
                        f"WAL/SHM sidecars still present after quiescence."
                    )

                # Acquire and HOLD persistent exclusive lock on target database
                h_target = _acquire_exclusive_target_lock(target_db_path)

            now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%H%M%S%f")
            tmp_restore = f"{target_db_path}.tmp_restore_{os.getpid()}_{now_str}"
            target_bak = f"{target_db_path}.pre_restore_{os.getpid()}_{now_str}" if target_exists else None

            src_conn = None
            dst_conn = None

            try:
                src_conn = sqlite3.connect(backup_path, autocommit=True)
                dst_conn = sqlite3.connect(tmp_restore, autocommit=True)

                # Reconstruct restored database via Online Backup API
                src_conn.backup(dst_conn, pages=pages_per_step, progress=progress_callback)

                # Verify integrity of newly restored temporary database
                cursor = dst_conn.cursor()
                cursor.execute("PRAGMA integrity_check;")
                rows = cursor.fetchall()
                integrity_results = [r[0] for r in rows if r]

                if not integrity_results or integrity_results[0] != "ok":
                    raise BackupIntegrityError(
                        f"Integrity check failed for restored temporary database '{tmp_restore}': {integrity_results}"
                    )

                try:
                    dst_conn.close()
                except Exception as ce:
                    cleanup_errors.append(ce)
                dst_conn = None

                try:
                    src_conn.close()
                except Exception as ce:
                    cleanup_errors.append(ce)
                src_conn = None

                # Recheck target state immediately before final placement (R2, R3)
                current_target_exists = os.path.exists(target_db_path)
                if not target_exists and current_target_exists:
                    raise BackupDestinationExistsError(
                        f"Target database '{target_db_path}' was created concurrently during restoration: "
                        f"atomic no-clobber placement rejected overwrite. Restored temporary database preserved at '{tmp_restore}'."
                    )

                # Atomically replace or place target database
                if target_exists:
                    try:
                        os.replace(target_db_path, target_bak)
                        os.replace(tmp_restore, target_db_path)
                        # Verify final placed database
                        cls.verify_backup(target_db_path)
                        # Verified! Safely release lock and unlink backup
                        try:
                            _close_exclusive_target_lock(h_target)
                        except Exception as ce:
                            cleanup_errors.append(ce)
                            lock_state_uncertain = True
                        h_target = None
                        try:
                            os.unlink(target_bak)
                            target_bak = None
                        except OSError as unlink_err:
                            cleanup_errors.append(unlink_err)
                    except Exception as replace_err:
                        # Release lock if still held before recovery
                        if h_target is not None:
                            try:
                                _close_exclusive_target_lock(h_target)
                            except Exception as ce:
                                cleanup_errors.append(ce)
                                lock_state_uncertain = True
                            h_target = None

                        # Rollback swap: recover original target from target_bak
                        if target_bak is not None and os.path.exists(target_bak):
                            try:
                                os.replace(target_bak, target_db_path)
                                cls.verify_backup(target_db_path)
                                target_bak = None
                            except Exception as rec_err:
                                # Recovery failed! Do NOT unlink target_bak!
                                rec_exc = BackupRecoveryError(
                                    f"CRITICAL: Replacement failed ({replace_err}) and recovery of original target "
                                    f"from '{target_bak}' also failed ({rec_err}). Original target is preserved at '{target_bak}'.",
                                    target_path=target_db_path,
                                    fallback_path=target_bak,
                                )
                                rec_exc.cleanup_errors = cleanup_errors
                                rec_exc.lock_state_uncertain = lock_state_uncertain
                                raise rec_exc from rec_err
                            recovery_exc = BackupError(
                                f"Restore operation failed: {replace_err}. Original target database successfully recovered."
                            )
                            if cleanup_errors:
                                recovery_exc.cleanup_errors = cleanup_errors
                            if lock_state_uncertain:
                                recovery_exc.lock_state_uncertain = True
                            raise recovery_exc from replace_err
                        raise
                else:
                    # Atomic no-clobber placement for new targets (R3)
                    cls._atomic_no_clobber_place(tmp_restore, target_db_path)
                    cls.verify_backup(target_db_path)

                restored_size = os.path.getsize(target_db_path)
                now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

                return BackupResult(
                    source_path=backup_path,
                    destination_path=target_db_path,
                    file_size_bytes=restored_size,
                    timestamp_utc=now_iso,
                    integrity_status="ok",
                )

            except Exception as e:
                if h_target is not None:
                    try:
                        _close_exclusive_target_lock(h_target)
                    except Exception as ce:
                        cleanup_errors.append(ce)
                        lock_state_uncertain = True
                    h_target = None

                if dst_conn is not None:
                    try:
                        dst_conn.close()
                    except Exception as ce:
                        cleanup_errors.append(ce)
                if src_conn is not None:
                    try:
                        src_conn.close()
                    except Exception as ce:
                        cleanup_errors.append(ce)

                # R3: If BackupDestinationExistsError, preserve tmp_restore intact!
                if isinstance(e, BackupDestinationExistsError):
                    setattr(e, "temporary_database_path", tmp_restore)
                elif os.path.exists(tmp_restore):
                    try:
                        os.unlink(tmp_restore)
                    except Exception as ce:
                        cleanup_errors.append(ce)

                if isinstance(e, BackupRecoveryError):
                    # NEVER touch or delete target_bak if recovery failed!
                    raise

                if target_bak is not None and os.path.exists(target_bak):
                    try:
                        os.unlink(target_bak)
                        target_bak = None
                    except Exception as ce:
                        cleanup_errors.append(ce)

                if isinstance(e, BackupError):
                    raise
                err = BackupError(f"Restore operation failed: {e}")
                raise err from e

        finally:
            outer_exc_type, outer_exc_val, outer_exc_tb = sys.exc_info()
            lock_release_error: Optional[Exception] = None

            if h_target is not None:
                try:
                    _close_exclusive_target_lock(h_target)
                except Exception as ce:
                    cleanup_errors.append(ce)
                    lock_state_uncertain = True
                h_target = None

            if restore_lock_token is not None and not restore_lock_token.is_released:
                try:
                    restore_lock_token.release()
                    if restore_lock_token.is_released:
                        restore_lock_token = None
                except Exception as ce:
                    lock_release_error = ce
                    cleanup_errors.append(ce)
                    lock_state_uncertain = True
                    DatabaseRestoreLockQuarantine.quarantine(restore_lock_token, ce)

            if outer_exc_val is None:
                if cleanup_errors:
                    final_token_state = (
                        restore_lock_token.state
                        if restore_lock_token is not None
                        else LockTokenState.FULLY_RELEASED
                    )
                    is_token_quarantined = (
                        DatabaseRestoreLockQuarantine.is_quarantined(restore_lock_token)
                        if restore_lock_token is not None
                        else False
                    )
                    cleanup_err = BackupCleanupError(
                        f"Restoration succeeded but non-fatal cleanup errors occurred: {[str(ce) for ce in cleanup_errors]}",
                        cleanup_errors=list(cleanup_errors),
                    )
                    cleanup_err.lock_release_error = lock_release_error
                    cleanup_err.token_quarantined = is_token_quarantined
                    cleanup_err.token_state = final_token_state
                    if restore_lock_token is not None and not restore_lock_token.is_released:
                        cleanup_err.quarantined_token = restore_lock_token
                    if lock_state_uncertain:
                        cleanup_err.lock_state_uncertain = True
                    if lock_release_error is not None:
                        raise cleanup_err from lock_release_error
                    raise cleanup_err from cleanup_errors[0]
            elif cleanup_errors or lock_state_uncertain:
                final_token_state = (
                    restore_lock_token.state
                    if restore_lock_token is not None
                    else LockTokenState.FULLY_RELEASED
                )
                is_token_quarantined = (
                    DatabaseRestoreLockQuarantine.is_quarantined(restore_lock_token)
                    if restore_lock_token is not None
                    else False
                )
                cleanup_summary = "; ".join(str(ce) for ce in cleanup_errors)
                cleanup_exc = BackupCleanupError(
                    f"Restore cleanup encountered {len(cleanup_errors)} error(s) "
                    f"(token_quarantined={is_token_quarantined}, token_state={final_token_state.value}): {cleanup_summary}",
                    cleanup_errors=list(cleanup_errors),
                )
                cleanup_exc.lock_release_error = lock_release_error
                cleanup_exc.token_quarantined = is_token_quarantined
                cleanup_exc.token_state = final_token_state
                cleanup_exc.lock_state_uncertain = lock_state_uncertain
                if restore_lock_token is not None and not restore_lock_token.is_released:
                    cleanup_exc.quarantined_token = restore_lock_token
                if lock_release_error is not None:
                    cleanup_exc.__cause__ = lock_release_error
                elif cleanup_errors:
                    cleanup_exc.__cause__ = cleanup_errors[0]

                try:
                    setattr(outer_exc_val, "cleanup_errors", list(cleanup_errors))
                    setattr(outer_exc_val, "cleanup_exception", cleanup_exc)
                    setattr(outer_exc_val, "lock_release_error", lock_release_error)
                    setattr(outer_exc_val, "token_quarantined", is_token_quarantined)
                    setattr(outer_exc_val, "token_state", final_token_state)
                    if lock_state_uncertain:
                        setattr(outer_exc_val, "lock_state_uncertain", True)
                    if restore_lock_token is not None and not restore_lock_token.is_released:
                        setattr(outer_exc_val, "quarantined_token", restore_lock_token)
                    if hasattr(outer_exc_val, "add_note"):
                        outer_exc_val.add_note(f"Restore cleanup failure diagnostics: {cleanup_exc}")
                except Exception as attr_err:
                    wrapper_exc = BackupError(
                        f"Restore failed ({outer_exc_val}) and attaching cleanup diagnostics failed ({attr_err}); "
                        f"cleanup_exc={cleanup_exc}"
                    )
                    wrapper_exc.primary_restore_error = outer_exc_val
                    wrapper_exc.cleanup_errors = list(cleanup_errors)
                    wrapper_exc.cleanup_exception = cleanup_exc
                    wrapper_exc.lock_release_error = lock_release_error
                    wrapper_exc.token_quarantined = is_token_quarantined
                    wrapper_exc.token_state = final_token_state
                    wrapper_exc.attachment_error = attr_err
                    if lock_state_uncertain:
                        wrapper_exc.lock_state_uncertain = True
                    if restore_lock_token is not None and not restore_lock_token.is_released:
                        wrapper_exc.quarantined_token = restore_lock_token
                    raise wrapper_exc from outer_exc_val

                if getattr(outer_exc_val, "__cause__", None) is None:
                    raise outer_exc_val.with_traceback(outer_exc_tb) from cleanup_exc
                else:
                    tail = outer_exc_val.__cause__
                    seen = {id(outer_exc_val), id(tail)}
                    while getattr(tail, "__cause__", None) is not None and id(tail.__cause__) not in seen:
                        tail = tail.__cause__
                        seen.add(id(tail))
                    if getattr(tail, "__cause__", None) is None and tail is not cleanup_exc:
                        try:
                            tail.__cause__ = cleanup_exc
                        except Exception as chain_err:
                            if hasattr(outer_exc_val, "add_note"):
                                outer_exc_val.add_note(f"Chaining cleanup_exc to cause tail failed: {chain_err}")
                            else:
                                raise cleanup_exc from outer_exc_val
