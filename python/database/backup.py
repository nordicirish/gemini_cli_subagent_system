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
        Safely restores a verified SQLite backup snapshot to target_db_path (R4).

        Enforces strict safety contract:
        - Verifies source backup physical integrity before touching the target.
        - Establishes target quiescence before modifying or replacing any target files:
          - Rejects restoration fail-closed if target is in active use by any connection or process.
          - If target is in WAL mode, checkpoints and transitions safely via PRAGMA journal_mode=DELETE,
            ensuring SQLite engine itself coordinates WAL flush and cleans up -wal and -shm sidecars.
          - Verifies exclusive filesystem-level access on target.
        - Restores to an isolated temporary database file first and verifies its integrity.
        - Preserves existing target file as rollback fallback during atomic swap.
        - Verifies the final restored database with PRAGMA integrity_check.
        - Guaranteed clean cleanup of temporary files in all scenarios.
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

        target_exists = os.path.exists(target_db_path)
        wal_path = f"{target_db_path}-wal"
        shm_path = f"{target_db_path}-shm"

        # 2. If target exists, establish and verify target quiescence fail-closed
        if target_exists:
            if os.path.isdir(target_db_path):
                raise BackupError(f"Target database path is a directory, not a file: '{target_db_path}'")

            # Verify exclusive filesystem access to target file
            try:
                with open(target_db_path, "r+b"):
                    pass
            except (PermissionError, OSError) as e:
                raise BackupError(
                    f"Target database '{target_db_path}' cannot be safely restored: "
                    f"Database file is held open by another active process: {e}"
                ) from e

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
                # Check exclusive connection lock
                try:
                    target_check = sqlite3.connect(target_db_path, timeout=0.0, autocommit=True)
                except sqlite3.OperationalError as e:
                    raise BackupError(
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
                            raise BackupError(
                                f"Target database '{target_db_path}' cannot be safely restored: "
                                f"Active concurrent readers or uncheckpointed writes in WAL mode."
                            )
                        # Transition away from WAL mode so SQLite flushes and unlinks sidecars
                        try:
                            cur.execute("PRAGMA journal_mode=DELETE;")
                            new_jmode = str(cur.fetchone()[0]).lower()
                            if new_jmode != "delete":
                                raise BackupError(
                                    f"Target database '{target_db_path}' cannot be safely restored: "
                                    f"Failed to transition from WAL mode (remained '{new_jmode}')."
                                )
                        except sqlite3.OperationalError as e:
                            raise BackupError(
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
                            raise BackupError(
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
                            with open(s_path, "r+b"):
                                pass
                            os.unlink(s_path)
                        except (PermissionError, OSError) as e:
                            raise BackupError(
                                f"Target database '{target_db_path}' cannot be safely restored: "
                                f"Sidecar '{s_path}' is held open by another process: {e}"
                            ) from e

            # Verify no sidecar files remain active
            if os.path.exists(wal_path) or os.path.exists(shm_path):
                raise BackupError(
                    f"Target database '{target_db_path}' cannot be safely restored: "
                    f"WAL/SHM sidecars still present after quiescence."
                )

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

            dst_conn.close()
            dst_conn = None
            src_conn.close()
            src_conn = None

            # Atomically replace target database with fallback preservation
            if target_exists:
                try:
                    os.replace(target_db_path, target_bak)
                    os.replace(tmp_restore, target_db_path)
                    # Verify final placed database
                    cls.verify_backup(target_db_path)
                    # Verified! Safely unlink backup
                    try:
                        os.unlink(target_bak)
                        target_bak = None
                    except OSError:
                        pass
                except Exception as replace_err:
                    # Rollback swap if possible
                    if target_bak is not None and os.path.exists(target_bak) and not os.path.exists(target_db_path):
                        try:
                            os.replace(target_bak, target_db_path)
                        except OSError:
                            pass
                    raise
            else:
                os.replace(tmp_restore, target_db_path)
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
            if target_bak is not None and os.path.exists(target_bak):
                if not os.path.exists(target_db_path):
                    try:
                        os.replace(target_bak, target_db_path)
                    except Exception:
                        pass
                else:
                    try:
                        os.unlink(target_bak)
                    except Exception:
                        pass
            if isinstance(e, BackupError):
                raise
            raise BackupError(f"Restore operation failed: {e}") from e
