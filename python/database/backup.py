"""
SQLite Online Backup Infrastructure
Governed by Stage 1.3 Directive and Stage 1.2.1 / 1.3.1 Architecture Corrections.

Provides atomic, consistent database snapshots using SQLite's native Online Backup API
(`sqlite3.Connection.backup()`). Guarantees consistent point-in-time snapshots
even when the source database is in active WAL mode with concurrent readers/writers.
"""

import os
import sys
import sqlite3
import datetime
from dataclasses import dataclass
from typing import Optional, Callable, Dict, Any


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
            # Clean up destination connection and temporary file on error
            if dst_conn is not None:
                try:
                    dst_conn.close()
                except Exception:
                    pass
            if os.path.exists(tmp_dest):
                try:
                    os.unlink(tmp_dest)
                except Exception:
                    pass
            if isinstance(e, BackupError):
                raise
            raise BackupError(f"Backup operation failed: {e}") from e

        finally:
            if owns_source_conn:
                try:
                    src_conn.close()
                except Exception:
                    pass

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
        Safely restores a verified SQLite backup snapshot to target_db_path (R7).

        Requirements enforced:
        - Verifies source backup physical integrity before restoration attempt.
        - Rejects corrupted or invalid backups fail-closed.
        - Avoids replacing an existing target database until restored copy is fully verified.
        - Cleans up existing SQLite WAL and SHM sidecars to prevent WAL frame contamination.
        - Restores to an isolated temporary file first, then atomically replaces target.
        - Guarantees zero partial replacement on failure.
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

        # Verify source database physical integrity before attempting restoration
        cls.verify_backup(backup_path)

        target_dir = os.path.dirname(target_db_path)
        if target_dir and not os.path.exists(target_dir):
            try:
                os.makedirs(target_dir, exist_ok=True)
            except OSError as e:
                raise BackupError(f"Failed to create target directory '{target_dir}': {e}") from e

        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%H%M%S%f")
        tmp_restore = f"{target_db_path}.tmp_restore_{os.getpid()}_{now_str}"

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

            dst_conn.close()
            dst_conn = None
            src_conn.close()
            src_conn = None

            # Handle existing WAL / SHM sidecars: remove to prevent stale WAL contamination
            wal_path = f"{target_db_path}-wal"
            shm_path = f"{target_db_path}-shm"
            if os.path.exists(wal_path):
                try:
                    os.unlink(wal_path)
                except OSError as e:
                    raise BackupError(f"Failed to remove stale WAL sidecar '{wal_path}': {e}") from e
            if os.path.exists(shm_path):
                try:
                    os.unlink(shm_path)
                except OSError as e:
                    raise BackupError(f"Failed to remove stale SHM sidecar '{shm_path}': {e}") from e

            # Atomically replace target database file
            os.replace(tmp_restore, target_db_path)

            # Reopen restored database at final target path and perform final verification
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
            if dst_conn is not None:
                try:
                    dst_conn.close()
                except Exception:
                    pass
            if src_conn is not None:
                try:
                    src_conn.close()
                except Exception:
                    pass
            if os.path.exists(tmp_restore):
                try:
                    os.unlink(tmp_restore)
                except Exception:
                    pass
            if isinstance(e, BackupError):
                raise
            raise BackupError(f"Restore operation failed: {e}") from e
