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

    def __enter__(self):
        start_time = time.time()
        backoff = 0.01

        depths = _get_held_depths()
        current_depth = depths.get(self.norm_path, 0)

        # If current thread already holds this lock, re-enter without re-locking OS handle
        if current_depth > 0:
            self._rlock.acquire()
            self._acquired_rlock = True
            depths[self.norm_path] = current_depth + 1
            self._is_outermost = False
            return self

        # Acquire intra-process thread RLock within timeout
        while True:
            if self._rlock.acquire(blocking=False):
                self._acquired_rlock = True
                break
            if time.time() - start_time >= self.timeout:
                raise FileLockTimeoutError(
                    f"Could not acquire thread lock on '{self.lock_path}' after {self.timeout}s"
                )
            time.sleep(backoff)
            backoff = min(backoff * 1.5, 0.05)

        # Ensure parent directory exists
        parent_dir = os.path.dirname(self.norm_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        # Acquire OS-level file lock for cross-process exclusion
        backoff = 0.01
        while True:
            try:
                self._file = open(self.norm_path, "a+b")
                self._file.seek(0)
                _lock_file(self._file)
                depths[self.norm_path] = 1
                self._is_outermost = True
                return self
            except (IOError, OSError):
                if self._file:
                    try:
                        self._file.close()
                    except Exception:
                        pass
                    self._file = None

                if time.time() - start_time >= self.timeout:
                    if self._acquired_rlock:
                        self._rlock.release()
                        self._acquired_rlock = False
                    raise FileLockTimeoutError(
                        f"Could not acquire file lock on '{self.lock_path}' after {self.timeout}s"
                    )

                time.sleep(backoff)
                backoff = min(backoff * 1.5, 0.1)

    def __exit__(self, exc_type, exc_val, exc_tb):
        depths = _get_held_depths()
        current_depth = depths.get(self.norm_path, 0)

        try:
            if self._is_outermost and self._file:
                try:
                    _unlock_file(self._file)
                finally:
                    try:
                        self._file.close()
                    except Exception:
                        pass
                    self._file = None
        finally:
            if current_depth <= 1:
                depths.pop(self.norm_path, None)
            else:
                depths[self.norm_path] = current_depth - 1

            if self._acquired_rlock:
                self._rlock.release()
                self._acquired_rlock = False


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
    1. Acquires FileLock(file_path + '.lock').
    2. Reads existing JSON content (or initializes via default_factory() if missing/corrupt/empty).
    3. Invokes updated_data = update_fn(current_data).
    4. Validates JSON serialization and optional validate_fn(updated_data).
    5. Atomically writes updated_data via temp file + fsync + os.replace before releasing lock.
    Returns updated_data.
    """
    lock_path = file_path + ".lock"
    with FileLock(lock_path, timeout=timeout):
        current_data = None
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        current_data = json.loads(content)
            except Exception:
                current_data = None

        if current_data is None:
            current_data = default_factory() if callable(default_factory) else default_factory

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
