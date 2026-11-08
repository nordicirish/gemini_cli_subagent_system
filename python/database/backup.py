"""
SQLite Online Backup Infrastructure
Governed by Stage 1.3 Directive and Stage 1.2.1 Architecture Corrections.

Provides atomic, consistent database snapshots using SQLite's native Online Backup API
(`sqlite3.Connection.backup()`). Guarantees consistent point-in-time snapshots
even when the source database is in active WAL mode with concurrent readers/writers.
"""

import os
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

    @staticmethod
    def create_backup(
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
            overwrite: If False, raises BackupError if dest_path already exists.
            pages_per_step: Number of pages to copy per step (-1 copies all pages in one step).
            progress_callback: Optional callback(status, remaining, total).

        Returns:
            BackupResult with file metadata and verified integrity status.
        """
        dest_path = os.path.abspath(dest_path)

        if os.path.exists(dest_path) and not overwrite:
            raise BackupError(
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
        tmp_dest = f"{dest_path}.tmp_{os.getpid()}_{datetime.datetime.now(datetime.timezone.utc).strftime('%H%M%S%f')}"
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

            # Atomically replace or place target destination file
            if os.path.exists(dest_path) and overwrite:
                os.replace(tmp_dest, dest_path)
            else:
                os.rename(tmp_dest, dest_path)

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

