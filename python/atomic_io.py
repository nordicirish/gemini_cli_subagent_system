"""
Atomic File I/O and Concurrency Protection Module
Governed by Protocol 02 (Development Standards), Stage 1 Section 3.3, and Stage 1.1 R6.
Provides atomic replacement and full read-modify-write locking across threads and processes.
"""

import os
import sys
import time
import json
import uuid
import errno
import threading
from typing import Any, Callable, Dict, Optional

# Platform-aware file locking using standard library
if sys.platform == "win32":
    import msvcrt

    def _lock_file(f):
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock_file(f):
        try:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except Exception:
            pass
else:
    import fcntl

    def _lock_file(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_file(f):
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass


class FileLockTimeoutError(TimeoutError):
    """Raised when a file lock cannot be acquired within the timeout window."""
    pass


# Process-local registry for thread-reentrant lock coordination alongside OS file locks
_REGISTRY_GUARD = threading.Lock()
_THREAD_LOCKS: Dict[str, threading.RLock] = {}
_LOCAL_STATE = threading.local()


def _get_thread_rlock(norm_path: str) -> threading.RLock:
    with _REGISTRY_GUARD:
        if norm_path not in _THREAD_LOCKS:
            _THREAD_LOCKS[norm_path] = threading.RLock()
        return _THREAD_LOCKS[norm_path]


def _get_held_depths() -> Dict[str, int]:
    if not hasattr(_LOCAL_STATE, "depths"):
        _LOCAL_STATE.depths = {}
    return _LOCAL_STATE.depths


_CONTENTION_ERRNOS = frozenset(
    code
    for code in (
        errno.EACCES,
        errno.EAGAIN,
        getattr(errno, "EWOULDBLOCK", errno.EAGAIN),
        getattr(errno, "EDEADLK", 36),
        getattr(errno, "EDEADLOCK", 36),
    )
    if code is not None
)


def _is_lock_contention_error(err: BaseException) -> bool:
    """Return True when an OSError represents transient lock contention rather than a hard FS failure."""
    if isinstance(err, BlockingIOError):
        return True
    if isinstance(err, OSError):
        return getattr(err, "errno", None) in _CONTENTION_ERRNOS
    return False


class FileLock:
    """
    Inter-process and inter-thread reentrant file lock with timeout and exponential backoff.
    Prevents both cross-process/cross-thread race conditions and same-thread nested lock deadlocks.
    """

    def __init__(self, lock_path: str, timeout: float = 5.0):
        self.lock_path = lock_path
        self.norm_path = os.path.abspath(lock_path)
        self.timeout = timeout
        self._file = None
        self._rlock = _get_thread_rlock(self.norm_path)
        self._acquired_rlock = False
        self._is_outermost = False
        self._reentrant_depth = 0

    def _acquire_local_lock(self, start_time: float, depths: Dict[str, int]) -> bool:
        """Acquire the process-local RLock; return True if already held reentrantly on this thread."""
        current_depth = depths.get(self.norm_path, 0)
        if current_depth > 0:
            self._rlock.acquire()
            self._reentrant_depth += 1
            self._acquired_rlock = True
            depths[self.norm_path] = current_depth + 1
            if self._file is None:
                self._is_outermost = False
            return True

        backoff = 0.01
        while True:
            if self._rlock.acquire(blocking=False):
                self._reentrant_depth += 1
                self._acquired_rlock = True
                return False
            if time.time() - start_time >= self.timeout:
                raise FileLockTimeoutError(
                    f"Could not acquire thread lock on '{self.lock_path}' after {self.timeout}s"
                )
            time.sleep(backoff)
            backoff = min(backoff * 1.5, 0.05)

    def _ensure_parent_directory(self) -> None:
        parent_dir = os.path.dirname(self.norm_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

    def _open_lock_file(self):
        return open(self.norm_path, "a+b")

    def _acquire_os_lock(self, handle, start_time: float) -> None:
        backoff = 0.01
        while True:
            try:
                handle.seek(0)
                _lock_file(handle)
                return
            except (IOError, OSError) as err:
                if not _is_lock_contention_error(err):
                    raise
                if time.time() - start_time >= self.timeout:
                    raise FileLockTimeoutError(
                        f"Could not acquire file lock on '{self.lock_path}' after {self.timeout}s"
                    ) from err
                time.sleep(backoff)
                backoff = min(backoff * 1.5, 0.1)

    def _release_local_lock_after_failed_acquisition(self) -> None:
        if self._reentrant_depth > 0 or self._acquired_rlock:
            self._reentrant_depth = max(0, self._reentrant_depth - 1)
            self._acquired_rlock = self._reentrant_depth > 0
            self._rlock.release()

    def __enter__(self):
        start_time = time.time()
        depths = _get_held_depths()
        acquired_local = False
        handle = None
        os_locked = False

        try:
            is_reentrant = self._acquire_local_lock(start_time, depths)
            if is_reentrant:
                return self
            acquired_local = True

            self._ensure_parent_directory()
            handle = self._open_lock_file()
            self._acquire_os_lock(handle, start_time)
            os_locked = True

            self._file = handle
            handle = None  # Ownership transferred to instance.
            depths[self.norm_path] = 1
            self._is_outermost = True
            return self

        except BaseException:
            if handle is not None:
                if os_locked:
                    _unlock_file(handle)
                try:
                    handle.close()
                except Exception:
                    pass

            if acquired_local:
                self._release_local_lock_after_failed_acquisition()

            raise

    def __exit__(self, exc_type, exc_val, exc_tb):
        depths = _get_held_depths()
        current_depth = depths.get(self.norm_path, 0)

        try:
            if current_depth <= 1 and self._file is not None:
                try:
                    _unlock_file(self._file)
                finally:
                    try:
                        self._file.close()
                    except Exception:
                        pass
                    self._file = None
                    self._is_outermost = False
        finally:
            if current_depth <= 1:
                depths.pop(self.norm_path, None)
            else:
                depths[self.norm_path] = current_depth - 1

            if self._reentrant_depth > 0 or self._acquired_rlock:
                self._reentrant_depth = max(0, self._reentrant_depth - 1)
                self._acquired_rlock = self._reentrant_depth > 0
                self._rlock.release()


def _write_serialized_unlocked(file_path: str, serialized: str) -> bool:
    """Internal helper that writes pre-serialized content via temp file + fsync + os.replace. Caller must hold FileLock."""
    parent_dir = os.path.dirname(file_path)
    if parent_dir and not os.path.exists(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)

    temp_path = os.path.join(
        parent_dir if parent_dir else ".",
        f".{os.path.basename(file_path)}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp",
    )
    try:
        with open(temp_path, "w", encoding="utf-8") as tf:
            tf.write(serialized)
            tf.flush()
            os.fsync(tf.fileno())

        os.replace(temp_path, file_path)
        return True
    except Exception as e:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        raise IOError(f"Failed to atomically write '{file_path}': {e}")


def atomic_write_json(
    file_path: str,
    data: Any,
    validate_fn: Optional[Callable[[Any], bool]] = None,
    indent: int = 2,
    timeout: float = 5.0,
) -> bool:
    """
    Atomically writes data as JSON to file_path with concurrency protection.
    1. Validates in-memory JSON serializability before touching disk.
    2. Runs optional validate_fn on the data; raises ValueError if invalid.
    3. Acquires file lock.
    4. Writes to a temporary file in the same directory, flushes and fsyncs.
    5. Replaces the destination file atomically via os.replace.
    """
    try:
        serialized = json.dumps(data, indent=indent)
    except Exception as e:
        raise ValueError(f"Data failed JSON serialization validation: {e}")

    if validate_fn and not validate_fn(data):
        raise ValueError("Data validation function rejected the payload before writing.")

    lock_path = file_path + ".lock"
    with FileLock(lock_path, timeout=timeout):
        return _write_serialized_unlocked(file_path, serialized)


def atomic_read_json(
    file_path: str,
    default: Any = None,
    timeout: float = 5.0,
) -> Any:
    """Reads JSON from file_path under file lock protection."""
    if not os.path.exists(file_path):
        return default

    lock_path = file_path + ".lock"
    with FileLock(lock_path, timeout=timeout):
        if not os.path.exists(file_path):
            return default
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return default
            return json.loads(content)


def atomic_update_json(
    file_path: str,
    update_fn: Callable[[Any], Any],
    default_factory: Callable[[], Any] = dict,
    validate_fn: Optional[Callable[[Any], bool]] = None,
    indent: int = 2,
    timeout: float = 5.0,
) -> Any:
    """
    Executes a complete read-modify-write cycle on a JSON file under a single unbroken FileLock.
    Prevents lost updates when multiple threads or processes concurrently update the same file.

    Safety guarantees (Stage 1.1.1 F1):
    1. If file does not exist: initialises using default_factory().
    2. If file exists and contains valid JSON: loads and updates normally.
    3. If file exists but is empty: raises ValueError and preserves original file.
    4. If file exists but has invalid JSON syntax: raises ValueError and preserves original file.
    5. If file cannot be read (I/O/permission error): raises IOError and preserves original file.
    6. If update_fn result is invalid or fails validate_fn: raises ValueError and preserves original file.
    7. Retains thread-reentrant and cross-process FileLock across entire read-modify-write sequence.
    """
    lock_path = file_path + ".lock"
    with FileLock(lock_path, timeout=timeout):
        if not os.path.exists(file_path):
            current_data = default_factory() if callable(default_factory) else default_factory
        else:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    raw_content = f.read()
            except Exception as read_err:
                raise IOError(
                    f"Failed to read existing file '{file_path}': {read_err}. "
                    "Original file preserved to prevent data loss."
                ) from read_err

            if not raw_content.strip():
                raise ValueError(
                    f"Existing file '{file_path}' is empty. Refusing to silently overwrite. "
                    "Original file preserved to prevent data loss."
                )

            try:
                current_data = json.loads(raw_content)
            except json.JSONDecodeError as json_err:
                raise ValueError(
                    f"Failed to parse existing JSON file '{file_path}': {json_err}. "
                    "Original file preserved to prevent data loss."
                ) from json_err

        updated_data = update_fn(current_data)

        try:
            serialized = json.dumps(updated_data, indent=indent)
        except Exception as e:
            raise ValueError(f"Updated data failed JSON serialization validation: {e}")

        if validate_fn and not validate_fn(updated_data):
            raise ValueError("Data validation function rejected the updated payload before writing.")

        _write_serialized_unlocked(file_path, serialized)
        return updated_data


def atomic_write_text(
    file_path: str,
    text: str,
    timeout: float = 5.0,
) -> bool:
    """Atomically writes text to file_path with concurrency protection."""
    if not isinstance(text, str):
        raise ValueError(f"Expected string content for atomic_write_text, got {type(text).__name__}")

    lock_path = file_path + ".lock"
    with FileLock(lock_path, timeout=timeout):
        return _write_serialized_unlocked(file_path, text)
