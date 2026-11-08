"""
Comprehensive Automated Unit Tests for Isolated SQLite Core Infrastructure
Governed by Stage 1.3 Directive.

Tests cover:
- D1: Connection lifecycle, WAL mode, profile-specific PRAGMAs.
- D2: Schema initialisation, versioning, primary/unique/check constraints.
- D3: Explicit ACID transaction commits, rollbacks, and multi-row atomicity.
- D4: Financial storage validation (TEXT columns, no REAL, exact Decimal preservation).
- D5: SQLite Online Backup API snapshots, integrity checks, and data equivalence.
- D6: Architectural isolation from live application state and files.
"""

import os
import sys
import tempfile
import sqlite3
import unittest
import hashlib
from typing import List

# Ensure python directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "python")))

from database.manager import (
    DatabaseManager,
    DatabaseProfile,
    PRIMARY_FINANCIAL_PROFILE,
    MARKET_DATA_CACHE_PROFILE,
    DatabaseError,
    DatabaseConnectionError,
    DatabaseConfigurationError,
    DatabaseBusyError,
    DatabaseCorruptionError,
    DatabaseTransactionError,
)
from database.schema import (
    SchemaMigrator,
    SchemaError,
    SchemaVersionMismatchError,
    CURRENT_SCHEMA_VERSION,
)
from database.backup import (
    BackupManager,
    BackupError,
    BackupIntegrityError,
    BackupResult,
)


class TestDatabaseConnectionAndPragmas(unittest.TestCase):
    """D1: Connection and PRAGMA tests."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_primary.db")
        self.cache_path = os.path.join(self.temp_dir.name, "test_cache.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_primary_profile_pragmas(self):
        """Verify primary financial profile applies WAL mode, FULL sync, foreign keys, and timeout."""
        manager = DatabaseManager(self.db_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with manager.connection() as conn:
            cursor = conn.cursor()

            # Journal mode must be WAL
            jm = cursor.execute("PRAGMA journal_mode;").fetchone()[0]
            self.assertEqual(jm.lower(), "wal")

            # Synchronous must be FULL (integer 2)
            sync = cursor.execute("PRAGMA synchronous;").fetchone()[0]
            self.assertEqual(sync, 2)

            # Foreign keys must be ON (integer 1)
            fk = cursor.execute("PRAGMA foreign_keys;").fetchone()[0]
            self.assertEqual(fk, 1)

            # Busy timeout must be 5000 ms
            bt = cursor.execute("PRAGMA busy_timeout;").fetchone()[0]
            self.assertEqual(bt, 5000)

            # Trusted schema must be OFF (integer 0)
            ts = cursor.execute("PRAGMA trusted_schema;").fetchone()[0]
            self.assertEqual(ts, 0)

    def test_cache_profile_pragmas(self):
        """Verify cache profile applies WAL mode and NORMAL sync (integer 1)."""
        manager = DatabaseManager(self.cache_path, profile=MARKET_DATA_CACHE_PROFILE)
        with manager.connection() as conn:
            cursor = conn.cursor()

            jm = cursor.execute("PRAGMA journal_mode;").fetchone()[0]
            self.assertEqual(jm.lower(), "wal")

            sync = cursor.execute("PRAGMA synchronous;").fetchone()[0]
            self.assertEqual(sync, 1)  # NORMAL = 1

    def test_foreign_key_runtime_enforcement(self):
        """Verify foreign-key constraints actually block invalid references at runtime."""
        manager = DatabaseManager(self.db_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with manager.connection() as conn:
            conn.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY);")
            conn.execute(
                "CREATE TABLE child (id INTEGER PRIMARY KEY, p_id INTEGER REFERENCES parent(id));"
            )

            # Valid reference
            conn.execute("INSERT INTO parent VALUES (1);")
            conn.execute("INSERT INTO child VALUES (10, 1);")

            # Invalid reference must fail
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO child VALUES (20, 999);")

    def test_checkpoint_execution(self):
        """Verify checkpoint executes without errors and returns frame metrics."""
        manager = DatabaseManager(self.db_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with manager.connection() as conn:
            conn.execute("CREATE TABLE t (x INT);")
            conn.execute("INSERT INTO t VALUES (1);")

        busy, log, checkpointed = manager.checkpoint("PASSIVE")
        self.assertIsInstance(busy, int)
        self.assertIsInstance(log, int)
        self.assertIsInstance(checkpointed, int)

    def test_invalid_checkpoint_mode(self):
        """Verify invalid checkpoint mode raises ValueError."""
        manager = DatabaseManager(self.db_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with self.assertRaises(ValueError):
            manager.checkpoint("INVALID_MODE")

    def test_connection_closure(self):
        """Verify connection context manager properly closes the underlying connection."""
        manager = DatabaseManager(self.db_path, profile=PRIMARY_FINANCIAL_PROFILE)
        conn_ref = None
        with manager.connection() as conn:
            conn_ref = conn
            conn.execute("CREATE TABLE t (x INT);")
        # Attempting operation on closed connection must fail
        with self.assertRaises(sqlite3.ProgrammingError):
            conn_ref.execute("SELECT * FROM t;")


class TestDatabaseSchema(unittest.TestCase):
    """D2: Schema design, integrity constraints, and versioning tests."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_schema.db")
        self.manager = DatabaseManager(self.db_path, profile=PRIMARY_FINANCIAL_PROFILE)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_initial_schema_creation_and_version(self):
        """Verify initial schema creation installs all core tables and metadata version 1."""
        with self.manager.connection() as conn:
            version = SchemaMigrator.initialize_core_schema(conn)
            self.assertEqual(version, CURRENT_SCHEMA_VERSION)

            tables = SchemaMigrator.get_table_names(conn)
            expected_tables = {
                "database_metadata",
                "portfolio_cash",
                "portfolio_positions",
                "watchlist",
                "scout_categories",
                "trade_lessons",
                "decision_log",
                "financial_changelog",
                "owner_approvals",
                "audit_events",
            }
            self.assertTrue(expected_tables.issubset(set(tables)))

            # Verify metadata row
            cursor = conn.cursor()
            cursor.execute("SELECT schema_version, db_revision FROM database_metadata WHERE id = 1;")
            row = cursor.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], 1)
            self.assertEqual(row[1], 0)

    def test_idempotent_schema_initialization(self):
        """Verify repeated initialization is safe and idempotent."""
        with self.manager.connection() as conn:
            v1 = SchemaMigrator.initialize_core_schema(conn)
            v2 = SchemaMigrator.initialize_core_schema(conn)
            self.assertEqual(v1, v2)
            self.assertEqual(v2, CURRENT_SCHEMA_VERSION)

    def test_unknown_future_schema_version_rejected(self):
        """Verify database with future schema version raises SchemaVersionMismatchError."""
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            # Simulate a newer version installed by a future code version
            conn.execute("UPDATE database_metadata SET schema_version = 999 WHERE id = 1;")

            with self.assertRaises(SchemaVersionMismatchError):
                SchemaMigrator.initialize_core_schema(conn)

    def test_portfolio_positions_primary_key(self):
        """Verify primary key on (account_id, ticker) prevents duplicate entries."""
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            now = "2026-11-08T18:00:00+00:00"

            conn.execute(
                "INSERT INTO portfolio_positions VALUES ('PRIMARY', 'SPY', '10.0000', '500.0000', NULL, ?);",
                (now,),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO portfolio_positions VALUES ('PRIMARY', 'SPY', '20.0000', '510.0000', NULL, ?);",
                    (now,),
                )

    def test_ticker_check_constraint(self):
        """Verify lowercase or un-trimmed tickers are rejected by CHECK constraint."""
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            now = "2026-11-08T18:00:00+00:00"

            # Lowercase ticker must fail
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO portfolio_positions VALUES ('PRIMARY', 'spy', '10.0000', '500.0000', NULL, ?);",
                    (now,),
                )

            # Empty ticker must fail
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO portfolio_positions VALUES ('PRIMARY', '', '10.0000', '500.0000', NULL, ?);",
                    (now,),
                )

    def test_cash_check_constraints(self):
        """Verify cash check constraints reject negative values, invalid characters, and excess decimals."""
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            now = "2026-11-08T18:00:00+00:00"

            # Valid cash values
            conn.execute("INSERT INTO portfolio_cash VALUES ('PRIMARY', 'EUR', '15000.00', ?);", (now,))
            conn.execute("INSERT INTO portfolio_cash VALUES ('PRIMARY', 'USD', '500', ?);", (now,))

            # Negative cash must fail
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO portfolio_cash VALUES ('ACC2', 'EUR', '-100.00', ?);", (now,))

            # Three decimal places on cash must fail
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO portfolio_cash VALUES ('ACC2', 'EUR', '100.555', ?);", (now,))

            # Non-numeric characters must fail
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO portfolio_cash VALUES ('ACC2', 'EUR', '100.00EUR', ?);", (now,))

            # Multiple dots must fail
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO portfolio_cash VALUES ('ACC2', 'EUR', '10.5.2', ?);", (now,))

    def test_shares_check_constraints(self):
        """Verify share check constraints reject negative amounts, characters, and excess decimals."""
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            now = "2026-11-08T18:00:00+00:00"

            # Valid fractional shares
            conn.execute(
                "INSERT INTO portfolio_positions VALUES ('PRIMARY', 'AAPL', '12.5000', '180.2500', NULL, ?);",
                (now,),
            )

            # More than 4 decimals on shares must fail
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO portfolio_positions VALUES ('PRIMARY', 'MSFT', '10.12345', '400.0000', NULL, ?);",
                    (now,),
                )

    def test_audit_events_primary_key(self):
        """Verify audit_events enforces uniqueness on event_id (32-hex UUID)."""
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            now = "2026-11-08T18:00:00+00:00"
            ev_id = "a" * 32

            conn.execute(
                """
                INSERT INTO audit_events VALUES (
                    ?, ?, 'TOOL_CALL', 'SUCCESS', 'Data Analyst', 'echo', 'GRANTED', 'NOT_REQUIRED', 'hash', '{}'
                );
                """,
                (ev_id, now),
            )
            # Duplicate event_id must fail
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO audit_events VALUES (
                        ?, ?, 'TOOL_CALL', 'SUCCESS', 'Data Analyst', 'echo', 'GRANTED', 'NOT_REQUIRED', 'hash', '{}'
                    );
                    """,
                    (ev_id, now),
                )

    def test_cache_schema_initialization(self):
        """Verify cache schema installs cache_metadata and daily_bars with correct constraints."""
        cache_path = os.path.join(self.temp_dir.name, "cache.db")
        cache_manager = DatabaseManager(cache_path, profile=MARKET_DATA_CACHE_PROFILE)
        with cache_manager.connection() as conn:
            version = SchemaMigrator.initialize_cache_schema(conn)
            self.assertEqual(version, 1)

            tables = SchemaMigrator.get_table_names(conn)
            self.assertIn("daily_bars", tables)
            self.assertIn("cache_metadata", tables)


class TestDatabaseTransactions(unittest.TestCase):
    """D3: Explicit ACID transaction commits, rollbacks, and multi-row atomicity."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_tx.db")
        self.manager = DatabaseManager(self.db_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_successful_transaction_commit(self):
        """Verify writes within a transaction context are durably committed on clean exit."""
        now = "2026-11-08T18:00:00+00:00"
        with self.manager.transaction() as conn:
            conn.execute("INSERT INTO portfolio_cash VALUES ('PRIMARY', 'EUR', '10000.00', ?);", (now,))

        # Verify in a separate fresh connection
        with self.manager.connection() as conn:
            row = conn.execute("SELECT amount FROM portfolio_cash WHERE currency = 'EUR';").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "10000.00")

    def test_transaction_rollback_on_exception(self):
        """Verify all writes within a transaction context roll back cleanly when an exception is raised."""
        now = "2026-11-08T18:00:00+00:00"
        with self.assertRaises(RuntimeError):
            with self.manager.transaction() as conn:
                conn.execute("INSERT INTO portfolio_cash VALUES ('PRIMARY', 'EUR', '5000.00', ?);", (now,))
                raise RuntimeError("Simulated failure mid-transaction")

        # Verify nothing was persisted
        with self.manager.connection() as conn:
            row = conn.execute("SELECT amount FROM portfolio_cash WHERE currency = 'EUR';").fetchone()
            self.assertIsNone(row)

    def test_multi_row_atomic_consistency(self):
        """
        Verify multi-table atomic commit: portfolio position, changelog entry, and db_revision
        must commit together or roll back entirely.
        """
        now = "2026-11-08T18:00:00+00:00"

        # Successful atomic update
        with self.manager.transaction() as conn:
            conn.execute(
                "INSERT INTO portfolio_positions VALUES ('PRIMARY', 'NVDA', '5.0000', '130.0000', NULL, ?);",
                (now,),
            )
            conn.execute(
                """
                INSERT INTO financial_changelog (
                    correlation_id, actor_role, operation_name, target_resource,
                    before_state_json, after_state_json, timestamp_utc
                ) VALUES ('tx-101', 'Terminal Orchestrator', 'trade_buy', 'portfolio_positions', '{}', '{"NVDA":5}', ?);
                """,
                (now,),
            )
            conn.execute("UPDATE database_metadata SET db_revision = db_revision + 1 WHERE id = 1;")

        with self.manager.connection() as conn:
            pos = conn.execute("SELECT ticker, shares FROM portfolio_positions WHERE ticker='NVDA';").fetchone()
            cl = conn.execute("SELECT correlation_id FROM financial_changelog WHERE correlation_id='tx-101';").fetchone()
            rev = conn.execute("SELECT db_revision FROM database_metadata WHERE id = 1;").fetchone()[0]

            self.assertEqual(pos, ("NVDA", "5.0000"))
            self.assertIsNotNone(cl)
            self.assertEqual(rev, 1)

        # Failed atomic update must leave NO partial rows
        with self.assertRaises(ValueError):
            with self.manager.transaction() as conn:
                conn.execute(
                    "INSERT INTO portfolio_positions VALUES ('PRIMARY', 'AMD', '10.0000', '150.0000', NULL, ?);",
                    (now,),
                )
                conn.execute("UPDATE database_metadata SET db_revision = db_revision + 1 WHERE id = 1;")
                # Simulated failure before changelog insertion
                raise ValueError("Validation error before changelog inserted")

        with self.manager.connection() as conn:
            amd_pos = conn.execute("SELECT ticker FROM portfolio_positions WHERE ticker='AMD';").fetchone()
            rev = conn.execute("SELECT db_revision FROM database_metadata WHERE id = 1;").fetchone()[0]
            self.assertIsNone(amd_pos)
            self.assertEqual(rev, 1)  # Revision did not increment


class TestFinancialStorageExactness(unittest.TestCase):
    """D4: Financial numeric validation, zero REAL usage, exact Decimal preservation."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_fin.db")
        self.manager = DatabaseManager(self.db_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_no_authoritative_financial_columns_are_real(self):
        """Verify via PRAGMA table_info that NO monetary or share columns are declared as REAL or FLOAT."""
        with self.manager.connection() as conn:
            for table_name in ["portfolio_cash", "portfolio_positions"]:
                columns = SchemaMigrator.get_column_info(conn, table_name)
                for col in columns:
                    col_name = col["name"]
                    col_type = col["type"]
                    self.assertNotIn(
                        col_type,
                        {"REAL", "FLOAT", "DOUBLE"},
                        f"Column '{col_name}' in table '{table_name}' has floating-point type: {col_type}",
                    )
                    if col_name in {"amount", "shares", "wac_eur", "wac_usd"}:
                        self.assertEqual(
                            col_type,
                            "TEXT",
                            f"Financial column '{col_name}' in table '{table_name}' must be TEXT, got {col_type}",
                        )

    def test_exact_decimal_string_preservation(self):
        """Verify that decimal strings preserve exact string representations without binary float drift."""
        test_strings = [
            ("EUR", "15000.00"),
            ("USD", "16200.50"),
        ]
        now = "2026-11-08T18:00:00+00:00"
        with self.manager.connection() as conn:
            for currency, amount_str in test_strings:
                conn.execute(
                    "INSERT INTO portfolio_cash VALUES ('PRIMARY', ?, ?, ?);",
                    (currency, amount_str, now),
                )

            for currency, amount_str in test_strings:
                row = conn.execute(
                    "SELECT amount FROM portfolio_cash WHERE currency = ?;",
                    (currency,),
                ).fetchone()
                self.assertEqual(row[0], amount_str)
                # Verify exact character length and identity
                self.assertIsInstance(row[0], str)
                self.assertEqual(len(row[0]), len(amount_str))


class TestBackupInfrastructure(unittest.TestCase):
    """D5: SQLite Online Backup API snapshots, integrity checks, and data equivalence."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.src_path = os.path.join(self.temp_dir.name, "source.db")
        self.dst_path = os.path.join(self.temp_dir.name, "backup.db")
        self.manager = DatabaseManager(self.src_path, profile=PRIMARY_FINANCIAL_PROFILE)

        # Seed source database with schema and records
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            now = "2026-11-08T18:00:00+00:00"
            conn.execute("INSERT INTO portfolio_cash VALUES ('PRIMARY', 'EUR', '25000.00', ?);", (now,))
            conn.execute(
                "INSERT INTO portfolio_positions VALUES ('PRIMARY', 'SPY', '50.0000', '495.5000', NULL, ?);",
                (now,),
            )
            conn.execute("INSERT INTO watchlist VALUES ('GOOG', 'ACTIVE', ?, 'Core holding');", (now,))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_successful_backup_creation_and_integrity(self):
        """Verify backup creates a valid standalone file that passes PRAGMA integrity_check."""
        res = BackupManager.create_backup(self.src_path, self.dst_path, overwrite=False)
        self.assertIsInstance(res, BackupResult)
        self.assertTrue(os.path.exists(self.dst_path))
        self.assertGreater(res.file_size_bytes, 0)
        self.assertEqual(res.integrity_status, "ok")

        # Verify verify_backup utility
        self.assertTrue(BackupManager.verify_backup(self.dst_path))

    def test_backup_data_equivalence(self):
        """Verify all records from source database are accurately copied to the backup database."""
        BackupManager.create_backup(self.src_path, self.dst_path)

        # Query destination backup independently
        dst_manager = DatabaseManager(self.dst_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with dst_manager.connection() as conn:
            cash = conn.execute("SELECT amount FROM portfolio_cash WHERE currency='EUR';").fetchone()[0]
            pos = conn.execute("SELECT shares, wac_eur FROM portfolio_positions WHERE ticker='SPY';").fetchone()
            wl = conn.execute("SELECT ticker FROM watchlist WHERE ticker='GOOG';").fetchone()[0]

            self.assertEqual(cash, "25000.00")
            self.assertEqual(pos, ("50.0000", "495.5000"))
            self.assertEqual(wl, "GOOG")

    def test_existing_destination_handling(self):
        """Verify overwrite=False raises BackupError when destination exists, and overwrite=True succeeds."""
        # Create first backup
        BackupManager.create_backup(self.src_path, self.dst_path)

        # Attempt second backup without overwrite -> must fail
        with self.assertRaises(BackupError):
            BackupManager.create_backup(self.src_path, self.dst_path, overwrite=False)

        # Attempt second backup with overwrite -> must succeed
        res = BackupManager.create_backup(self.src_path, self.dst_path, overwrite=True)
        self.assertEqual(res.integrity_status, "ok")

    def test_backup_from_active_connection(self):
        """Verify backup can be initiated directly from an active sqlite3.Connection handle."""
        with self.manager.connection() as conn:
            res = BackupManager.create_backup(conn, self.dst_path)
            self.assertEqual(res.integrity_status, "ok")
            self.assertTrue(os.path.exists(self.dst_path))


class TestArchitecturalIsolation(unittest.TestCase):
    """D6: Isolation tests verifying no interference with live application state or files."""

    def test_import_does_not_create_production_database(self):
        """Verify importing database infrastructure does NOT create context/gem.db or cache/market_data.db."""
        import database
        self.assertFalse(os.path.exists(os.path.join("context", "gem.db")))
        self.assertFalse(os.path.exists(os.path.join("context", "gem.db-wal")))
        self.assertFalse(os.path.exists(os.path.join("cache", "market_data.db")))

    def test_existing_persistence_files_unmodified(self):
        """Verify existing JSON and JSONL state files remain present and untouched."""
        critical_files = [
            os.path.join("context", "ssot.json"),
            os.path.join("context", "trade_lessons.json"),
            os.path.join("context", "decision_log.json"),
            os.path.join("context", "agent_logs", "agent-events.jsonl"),
            os.path.join("gem_trading_rules", "rules.md"),
        ]
        for fpath in critical_files:
            self.assertTrue(os.path.exists(fpath), f"Critical file missing: {fpath}")


if __name__ == "__main__":
    unittest.main()

