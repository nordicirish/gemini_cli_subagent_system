"""
Atomic File I/O and Concurrency Protection Module
Governed by Protocol 02 (Development Standards) and Stage 1 Section 3.3.
"""

import os
import sys
import time
import json
import uuid
from typing import Any, Callable, Optional

# Platform-aware file locking using standard library
if sys.platform == "win32":
    import msvcrt

    def _lock_file(f):
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


class FileLock:
    """Inter-process and inter-thread file lock with timeout and exponential backoff."""

    def __init__(self, lock_path: str, timeout: float = 5.0):
        self.lock_path = lock_path
        self.timeout = timeout
        self._file = None

    def __enter__(self):
        start_time = time.time()
        backoff = 0.01

        # Ensure parent directory exists
        parent_dir = os.path.dirname(self.lock_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        while True:
            try:
                self._file = open(self.lock_path, "a+b")
                self._file.seek(0)
                _lock_file(self._file)
                return self
            except (IOError, OSError):
                if self._file:
                    try:
                        self._file.close()
                    except Exception:
                        pass
                    self._file = None

                if time.time() - start_time >= self.timeout:
                    raise FileLockTimeoutError(
                        f"Could not acquire file lock on '{self.lock_path}' after {self.timeout}s"
                    )

                time.sleep(backoff)
                backoff = min(backoff * 1.5, 0.1)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._file:
            try:
                _unlock_file(self._file)
            finally:
                try:
                    self._file.close()
                except Exception:
                    pass
                self._file = None


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
    4. Writes to a temporary file in the same directory.
    5. Flushes and fsyncs to physical disk.
    6. Replaces the destination file atomically via os.replace.
    """
    # 1. In-memory serialization validation
    try:
        serialized = json.dumps(data, indent=indent)
    except Exception as e:
        raise ValueError(f"Data failed JSON serialization validation: {e}")

    # 2. Domain data validation
    if validate_fn and not validate_fn(data):
        raise ValueError("Data validation function rejected the payload before writing.")

    # 3. Lock and write
    parent_dir = os.path.dirname(file_path)
    if parent_dir and not os.path.exists(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)

    lock_path = file_path + ".lock"
    temp_path = os.path.join(
        parent_dir if parent_dir else ".",
        f".{os.path.basename(file_path)}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp",
    )

    with FileLock(lock_path, timeout=timeout):
        try:
            with open(temp_path, "w", encoding="utf-8") as tf:
                tf.write(serialized)
                tf.flush()
                os.fsync(tf.fileno())

            # Atomic swap
            os.replace(temp_path, file_path)
            return True
        except Exception as e:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            raise IOError(f"Failed to atomically write '{file_path}': {e}")


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
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return default
            return json.loads(content)


def atomic_write_text(
    file_path: str,
    text: str,
    timeout: float = 5.0,
) -> bool:
    """Atomically writes text to file_path with concurrency protection."""
    parent_dir = os.path.dirname(file_path)
    if parent_dir and not os.path.exists(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)

    lock_path = file_path + ".lock"
    temp_path = os.path.join(
        parent_dir if parent_dir else ".",
        f".{os.path.basename(file_path)}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp",
    )

    with FileLock(lock_path, timeout=timeout):
        try:
            with open(temp_path, "w", encoding="utf-8") as tf:
                tf.write(text)
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
            raise IOError(f"Failed to atomically write text to '{file_path}': {e}")
