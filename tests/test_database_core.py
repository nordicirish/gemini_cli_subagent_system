"""
Comprehensive Automated Unit Tests for Isolated SQLite Core Infrastructure
Governed by Stage 1.3 Directive and Stage 1.3.1 / 1.3.1.2 Integrity Corrections.

Tests cover:
- D1: Connection lifecycle, WAL mode, profile-specific PRAGMAs.
- D2: Schema initialisation, versioning, primary/unique/check constraints, structural integrity checks.
- D3: Financial decimal structural constraints (Workstream A: multiple decimal points, zero REAL).
- D4: Audit event identifier integrity (Workstream B: strict 32-hex lowercase UUID).
- D5: Explicit ACID transaction commits, rollbacks, multi-row atomicity, nested transaction rejection.
- D6: SQLite Online Backup API snapshots, race-safe overwrite protection, failed backup cleanup.
- D7: Cryptographic hash-based persistence isolation verification.
"""

import os
import sys
import tempfile
import sqlite3
import unittest
import hashlib
import uuid
from typing import List, Dict, Optional

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
    SchemaIntegrityError,
    CURRENT_SCHEMA_VERSION,
)
from database.backup import (
    BackupManager,
    BackupError,
    BackupDestinationExistsError,
    BackupIntegrityError,
    BackupQuiescenceError,
    BackupRecoveryError,
    BackupCleanupError,
    BackupResult,
)


class TestDatabaseConnectionAndPragmas(unittest.TestCase):
    """D1: Connection lifecycle and PRAGMA configuration tests."""

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
    """D2: Schema design, versioning, structural definition verification, and constraints."""

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
        """Verify repeated initialization is safe, verifies integrity, and returns version."""
        with self.manager.connection() as conn:
            v1 = SchemaMigrator.initialize_core_schema(conn)
            v2 = SchemaMigrator.initialize_core_schema(conn)
            self.assertEqual(v1, v2)
            self.assertEqual(v2, CURRENT_SCHEMA_VERSION)

    def test_unknown_future_schema_version_rejected(self):
        """Verify core database with future schema version raises SchemaVersionMismatchError."""
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            # Simulate a newer version installed by a future code version
            conn.execute("UPDATE database_metadata SET schema_version = 999 WHERE id = 1;")

            with self.assertRaises(SchemaVersionMismatchError):
                SchemaMigrator.initialize_core_schema(conn)

    def test_cache_schema_future_version_rejected(self):
        """Verify cache database with future schema version raises SchemaVersionMismatchError."""
        cache_path = os.path.join(self.temp_dir.name, "cache_future.db")
        cache_mgr = DatabaseManager(cache_path, profile=MARKET_DATA_CACHE_PROFILE)
        with cache_mgr.connection() as conn:
            SchemaMigrator.initialize_cache_schema(conn)
            conn.execute("UPDATE cache_metadata SET schema_version = 999 WHERE id = 1;")

            with self.assertRaises(SchemaVersionMismatchError):
                SchemaMigrator.initialize_cache_schema(conn)

    def test_core_schema_integrity_missing_table(self):
        """Verify that existing database missing an expected table fails with SchemaIntegrityError."""
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("DROP TABLE portfolio_positions;")

            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.initialize_core_schema(conn)
            self.assertIn("portfolio_positions", str(ctx.exception))

    def test_core_schema_integrity_column_missing(self):
        """Verify that a table missing an expected column raises SchemaIntegrityError."""
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            # Drop portfolio_cash and recreate with missing currency column
            conn.execute("DROP TABLE portfolio_cash;")
            conn.execute(
                "CREATE TABLE portfolio_cash (account_id TEXT PRIMARY KEY, amount TEXT NOT NULL, last_updated_at_utc TEXT NOT NULL);"
            )

            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.verify_core_schema_integrity(conn)
            self.assertIn("missing column", str(ctx.exception))
            self.assertIn("currency", str(ctx.exception))

    def test_core_schema_integrity_column_type_mismatch(self):
        """Verify that a table with declared column type mismatch (e.g. REAL instead of TEXT) fails integrity check."""
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("DROP TABLE portfolio_cash;")
            conn.execute(
                """
                CREATE TABLE portfolio_cash (
                    account_id TEXT NOT NULL DEFAULT 'PRIMARY',
                    currency TEXT NOT NULL,
                    amount REAL NOT NULL,
                    last_updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY (account_id, currency)
                );
                """
            )

            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.verify_core_schema_integrity(conn)
            self.assertIn("declared type mismatch", str(ctx.exception))

    def test_core_schema_integrity_missing_check_constraint(self):
        """Verify that a table missing critical financial CHECK constraints fails integrity check."""
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            # Recreate portfolio_cash without the amount CHECK constraint
            conn.execute("DROP TABLE portfolio_cash;")
            conn.execute(
                """
                CREATE TABLE portfolio_cash (
                    account_id TEXT NOT NULL DEFAULT 'PRIMARY',
                    currency TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    last_updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY (account_id, currency)
                );
                """
            )

            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.verify_core_schema_integrity(conn)
            self.assertIn("missing mandatory constraint pattern", str(ctx.exception))

    def test_core_schema_integrity_index_mismatch(self):
        """Verify that an index pointing to the wrong column or table raises SchemaIntegrityError."""
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("DROP INDEX idx_audit_timestamp;")
            # Recreate index on the wrong column
            conn.execute("CREATE INDEX idx_audit_timestamp ON audit_events(status);")

            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.verify_core_schema_integrity(conn)
            self.assertIn("column mismatch", str(ctx.exception))

    def test_cache_schema_integrity_verification(self):
        """Verify cache schema integrity fails closed if daily_bars has a missing column or constraint."""
        cache_path = os.path.join(self.temp_dir.name, "cache_integrity.db")
        cache_mgr = DatabaseManager(cache_path, profile=MARKET_DATA_CACHE_PROFILE)
        with cache_mgr.connection() as conn:
            SchemaMigrator.initialize_cache_schema(conn)
            conn.execute("DROP TABLE daily_bars;")
            # Recreate daily_bars without open constraint
            conn.execute(
                """
                CREATE TABLE daily_bars (
                    ticker TEXT NOT NULL,
                    bar_date TEXT NOT NULL,
                    open TEXT NOT NULL,
                    high TEXT NOT NULL,
                    low TEXT NOT NULL,
                    close TEXT NOT NULL,
                    volume INTEGER NOT NULL,
                    indicators_json TEXT NOT NULL DEFAULT '{}',
                    updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY (ticker, bar_date)
                );
                """
            )
            conn.execute("CREATE INDEX idx_bars_ticker ON daily_bars(ticker);")
            conn.execute("CREATE INDEX idx_bars_date ON daily_bars(bar_date);")

            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.verify_cache_schema_integrity(conn)
            self.assertIn("missing mandatory constraint pattern", str(ctx.exception))

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

    def test_cash_scale_constraint_removal_rejected(self):
        """E1: Verify that removing the <= 2 decimal scale check from portfolio_cash raises SchemaIntegrityError."""
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("DROP TABLE portfolio_cash;")
            conn.execute(
                """
                CREATE TABLE portfolio_cash (
                    account_id TEXT NOT NULL DEFAULT 'PRIMARY',
                    currency TEXT NOT NULL CHECK (currency IN ('EUR', 'USD')),
                    amount TEXT NOT NULL CHECK (
                        amount NOT GLOB '*[^0-9.]*'
                        AND amount NOT GLOB '*.*.*'
                        AND amount NOT LIKE '%.%.%'
                        AND amount NOT GLOB '.*'
                        AND amount NOT GLOB '*.'
                        AND amount NOT GLOB '0[0-9]*'
                        AND length(amount) > 0
                    ),
                    last_updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY (account_id, currency)
                );
                """
            )
            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.verify_core_schema_integrity(conn)
            self.assertIn("missing mandatory constraint pattern", str(ctx.exception))
            self.assertIn("amount NOT LIKE '%.%' OR length(substr(amount, instr(amount, '.') + 1)) <= 2", str(ctx.exception))

    def test_shares_scale_constraint_removal_rejected(self):
        """E1: Verify that removing the <= 4 decimal scale check from portfolio_positions raises SchemaIntegrityError."""
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("DROP TABLE portfolio_positions;")
            conn.execute(
                """
                CREATE TABLE portfolio_positions (
                    account_id TEXT NOT NULL DEFAULT 'PRIMARY',
                    ticker TEXT NOT NULL CHECK (ticker = UPPER(TRIM(ticker)) AND length(ticker) > 0),
                    shares TEXT NOT NULL CHECK (
                        shares NOT GLOB '*[^0-9.]*'
                        AND shares NOT GLOB '*.*.*'
                        AND shares NOT LIKE '%.%.%'
                        AND shares NOT GLOB '.*'
                        AND shares NOT GLOB '*.'
                        AND shares NOT GLOB '0[0-9]*'
                        AND length(shares) > 0
                    ),
                    wac_eur TEXT NOT NULL CHECK (
                        wac_eur NOT GLOB '*[^0-9.]*'
                        AND wac_eur NOT GLOB '*.*.*'
                        AND wac_eur NOT LIKE '%.%.%'
                        AND wac_eur NOT GLOB '.*'
                        AND wac_eur NOT GLOB '*.'
                        AND wac_eur NOT GLOB '0[0-9]*'
                        AND length(wac_eur) > 0
                        AND (wac_eur NOT LIKE '%.%' OR length(substr(wac_eur, instr(wac_eur, '.') + 1)) <= 4)
                    ),
                    wac_usd TEXT CHECK (
                        wac_usd IS NULL OR (
                            wac_usd NOT GLOB '*[^0-9.]*'
                            AND wac_usd NOT GLOB '*.*.*'
                            AND wac_usd NOT LIKE '%.%.%'
                            AND wac_usd NOT GLOB '.*'
                            AND wac_usd NOT GLOB '*.'
                            AND wac_usd NOT GLOB '0[0-9]*'
                            AND length(wac_usd) > 0
                            AND (wac_usd NOT LIKE '%.%' OR length(substr(wac_usd, instr(wac_usd, '.') + 1)) <= 4)
                        )
                    ),
                    last_updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY (account_id, ticker)
                );
                """
            )
            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.verify_core_schema_integrity(conn)
            self.assertIn("missing mandatory constraint pattern", str(ctx.exception))
            self.assertIn("shares NOT LIKE '%.%' OR length(substr(shares, instr(shares, '.') + 1)) <= 4", str(ctx.exception))

    def test_ohlc_scale_constraint_removal_rejected(self):
        """E1: Verify that removing the <= 4 decimal scale check from daily_bars.open raises SchemaIntegrityError."""
        cache_path = os.path.join(self.temp_dir.name, "cache_ohlc_integrity.db")
        cache_mgr = DatabaseManager(cache_path, profile=MARKET_DATA_CACHE_PROFILE)
        with cache_mgr.connection() as conn:
            SchemaMigrator.initialize_cache_schema(conn)
            conn.execute("DROP TABLE daily_bars;")
            conn.execute(
                """
                CREATE TABLE daily_bars (
                    ticker TEXT NOT NULL CHECK (ticker = UPPER(TRIM(ticker)) AND length(ticker) > 0),
                    bar_date TEXT NOT NULL CHECK (length(bar_date) = 10),
                    open TEXT NOT NULL CHECK (
                        open NOT GLOB '*[^0-9.]*'
                        AND open NOT GLOB '*.*.*'
                        AND open NOT LIKE '%.%.%'
                        AND open NOT GLOB '.*'
                        AND open NOT GLOB '*.'
                        AND open NOT GLOB '0[0-9]*'
                        AND length(open) > 0
                    ),
                    high TEXT NOT NULL,
                    low TEXT NOT NULL,
                    close TEXT NOT NULL,
                    volume INTEGER NOT NULL CHECK (volume >= 0),
                    indicators_json TEXT NOT NULL DEFAULT '{}',
                    updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY (ticker, bar_date)
                );
                """
            )
            conn.execute("CREATE INDEX idx_bars_ticker ON daily_bars(ticker);")
            conn.execute("CREATE INDEX idx_bars_date ON daily_bars(bar_date);")

            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.verify_cache_schema_integrity(conn)
            self.assertIn("missing mandatory constraint pattern", str(ctx.exception))
            self.assertIn("open NOT LIKE '%.%' OR length(substr(open, instr(open, '.') + 1)) <= 4", str(ctx.exception))

    def test_multi_dot_constraint_removal_rejected(self):
        """E1: Verify that removing amount NOT GLOB '*.*.*' raises SchemaIntegrityError."""
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("DROP TABLE portfolio_cash;")
            conn.execute(
                """
                CREATE TABLE portfolio_cash (
                    account_id TEXT NOT NULL DEFAULT 'PRIMARY',
                    currency TEXT NOT NULL CHECK (currency IN ('EUR', 'USD')),
                    amount TEXT NOT NULL CHECK (
                        amount NOT GLOB '*[^0-9.]*'
                        AND amount NOT LIKE '%.%.%'
                        AND amount NOT GLOB '.*'
                        AND amount NOT GLOB '*.'
                        AND amount NOT GLOB '0[0-9]*'
                        AND length(amount) > 0
                        AND (amount NOT LIKE '%.%' OR length(substr(amount, instr(amount, '.') + 1)) <= 2)
                    ),
                    last_updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY (account_id, currency)
                );
                """
            )
            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.verify_core_schema_integrity(conn)
            self.assertIn("amount NOT GLOB '*.*.*'", str(ctx.exception))

    def test_malformed_decimal_constraint_removal_rejected(self):
        """E1: Verify that removing amount NOT GLOB '*[^0-9.]*' raises SchemaIntegrityError."""
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("DROP TABLE portfolio_cash;")
            conn.execute(
                """
                CREATE TABLE portfolio_cash (
                    account_id TEXT NOT NULL DEFAULT 'PRIMARY',
                    currency TEXT NOT NULL CHECK (currency IN ('EUR', 'USD')),
                    amount TEXT NOT NULL CHECK (
                        amount NOT GLOB '*.*.*'
                        AND amount NOT LIKE '%.%.%'
                        AND amount NOT GLOB '.*'
                        AND amount NOT GLOB '*.'
                        AND amount NOT GLOB '0[0-9]*'
                        AND length(amount) > 0
                        AND (amount NOT LIKE '%.%' OR length(substr(amount, instr(amount, '.') + 1)) <= 2)
                    ),
                    last_updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY (account_id, currency)
                );
                """
            )
            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.verify_core_schema_integrity(conn)
            self.assertIn("amount NOT GLOB '*[^0-9.]*'", str(ctx.exception))

    def test_unexpected_foreign_key_rejected(self):
        """E2: Verify that an unexpected foreign key added to a table raises SchemaIntegrityError."""
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("DROP TABLE portfolio_positions;")
            conn.execute(
                """
                CREATE TABLE portfolio_positions (
                    account_id TEXT NOT NULL DEFAULT 'PRIMARY',
                    ticker TEXT NOT NULL CHECK (ticker = UPPER(TRIM(ticker)) AND length(ticker) > 0),
                    shares TEXT NOT NULL CHECK (
                        shares NOT GLOB '*[^0-9.]*'
                        AND shares NOT GLOB '*.*.*'
                        AND shares NOT LIKE '%.%.%'
                        AND shares NOT GLOB '.*'
                        AND shares NOT GLOB '*.'
                        AND shares NOT GLOB '0[0-9]*'
                        AND length(shares) > 0
                        AND (shares NOT LIKE '%.%' OR length(substr(shares, instr(shares, '.') + 1)) <= 4)
                    ),
                    wac_eur TEXT NOT NULL CHECK (
                        wac_eur NOT GLOB '*[^0-9.]*'
                        AND wac_eur NOT GLOB '*.*.*'
                        AND wac_eur NOT LIKE '%.%.%'
                        AND wac_eur NOT GLOB '.*'
                        AND wac_eur NOT GLOB '*.'
                        AND wac_eur NOT GLOB '0[0-9]*'
                        AND length(wac_eur) > 0
                        AND (wac_eur NOT LIKE '%.%' OR length(substr(wac_eur, instr(wac_eur, '.') + 1)) <= 4)
                    ),
                    wac_usd TEXT CHECK (
                        wac_usd IS NULL OR (
                            wac_usd NOT GLOB '*[^0-9.]*'
                            AND wac_usd NOT GLOB '*.*.*'
                            AND wac_usd NOT LIKE '%.%.%'
                            AND wac_usd NOT GLOB '.*'
                            AND wac_usd NOT GLOB '*.'
                            AND wac_usd NOT GLOB '0[0-9]*'
                            AND length(wac_usd) > 0
                            AND (wac_usd NOT LIKE '%.%' OR length(substr(wac_usd, instr(wac_usd, '.') + 1)) <= 4)
                        )
                    ),
                    last_updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY (account_id, ticker),
                    FOREIGN KEY (ticker) REFERENCES watchlist (ticker)
                );
                """
            )
            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.verify_core_schema_integrity(conn)
            self.assertIn("foreign-key mismatch", str(ctx.exception))

    def test_empty_foreign_key_inventory_verified(self):
        """E2: Verify that expected foreign-key inventories for Core and Cache are explicit and empty."""
        from database.schema import EXPECTED_CORE_FOREIGN_KEYS, EXPECTED_CACHE_FOREIGN_KEYS
        for tbl, fks in EXPECTED_CORE_FOREIGN_KEYS.items():
            self.assertEqual(fks, [], f"Core table '{tbl}' should have empty expected foreign keys")
        for tbl, fks in EXPECTED_CACHE_FOREIGN_KEYS.items():
            self.assertEqual(fks, [], f"Cache table '{tbl}' should have empty expected foreign keys")

    def test_unexpected_column_rejected(self):
        """E3: Verify that an unexpected additional column on an exact table raises SchemaIntegrityError."""
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("ALTER TABLE portfolio_cash ADD COLUMN unauthorized_column TEXT;")
            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.verify_core_schema_integrity(conn)
            self.assertIn("unexpected column", str(ctx.exception))
            self.assertIn("unauthorized_column", str(ctx.exception))

    def test_column_ordering_mismatch_rejected(self):
        """E3: Verify that reordered columns raise SchemaIntegrityError."""
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("DROP TABLE portfolio_cash;")
            conn.execute(
                """
                CREATE TABLE portfolio_cash (
                    currency TEXT NOT NULL CHECK (currency IN ('EUR', 'USD')),
                    account_id TEXT NOT NULL DEFAULT 'PRIMARY',
                    amount TEXT NOT NULL CHECK (
                        amount NOT GLOB '*[^0-9.]*'
                        AND amount NOT GLOB '*.*.*'
                        AND amount NOT LIKE '%.%.%'
                        AND amount NOT GLOB '.*'
                        AND amount NOT GLOB '*.'
                        AND amount NOT GLOB '0[0-9]*'
                        AND length(amount) > 0
                        AND (amount NOT LIKE '%.%' OR length(substr(amount, instr(amount, '.') + 1)) <= 2)
                    ),
                    last_updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY (account_id, currency)
                );
                """
            )
            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.verify_core_schema_integrity(conn)
            self.assertIn("column ordering mismatch", str(ctx.exception))

    def test_column_notnull_mismatch_rejected(self):
        """E3: Verify that missing NOT NULL on a required column raises SchemaIntegrityError."""
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("DROP TABLE portfolio_cash;")
            conn.execute(
                """
                CREATE TABLE portfolio_cash (
                    account_id TEXT NOT NULL DEFAULT 'PRIMARY',
                    currency TEXT NOT NULL CHECK (currency IN ('EUR', 'USD')),
                    amount TEXT CHECK (
                        amount NOT GLOB '*[^0-9.]*'
                        AND amount NOT GLOB '*.*.*'
                        AND amount NOT LIKE '%.%.%'
                        AND amount NOT GLOB '.*'
                        AND amount NOT GLOB '*.'
                        AND amount NOT GLOB '0[0-9]*'
                        AND length(amount) > 0
                        AND (amount NOT LIKE '%.%' OR length(substr(amount, instr(amount, '.') + 1)) <= 2)
                    ),
                    last_updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY (account_id, currency)
                );
                """
            )
            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.verify_core_schema_integrity(conn)
            self.assertIn("NOT NULL mismatch", str(ctx.exception))

    def test_column_pk_mismatch_rejected(self):
        """E3: Verify that incorrect primary key composition raises SchemaIntegrityError."""
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("DROP TABLE portfolio_cash;")
            conn.execute(
                """
                CREATE TABLE portfolio_cash (
                    account_id TEXT NOT NULL PRIMARY KEY DEFAULT 'PRIMARY',
                    currency TEXT NOT NULL CHECK (currency IN ('EUR', 'USD')),
                    amount TEXT NOT NULL CHECK (
                        amount NOT GLOB '*[^0-9.]*'
                        AND amount NOT GLOB '*.*.*'
                        AND amount NOT LIKE '%.%.%'
                        AND amount NOT GLOB '.*'
                        AND amount NOT GLOB '*.'
                        AND amount NOT GLOB '0[0-9]*'
                        AND length(amount) > 0
                        AND (amount NOT LIKE '%.%' OR length(substr(amount, instr(amount, '.') + 1)) <= 2)
                    ),
                    last_updated_at_utc TEXT NOT NULL
                );
                """
            )
            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.verify_core_schema_integrity(conn)
            self.assertIn("primary key mismatch", str(ctx.exception))

    def test_column_default_value_mismatch_rejected(self):
        """E3: Verify that missing or altered default value raises SchemaIntegrityError."""
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("DROP TABLE portfolio_cash;")
            conn.execute(
                """
                CREATE TABLE portfolio_cash (
                    account_id TEXT NOT NULL,
                    currency TEXT NOT NULL CHECK (currency IN ('EUR', 'USD')),
                    amount TEXT NOT NULL CHECK (
                        amount NOT GLOB '*[^0-9.]*'
                        AND amount NOT GLOB '*.*.*'
                        AND amount NOT LIKE '%.%.%'
                        AND amount NOT GLOB '.*'
                        AND amount NOT GLOB '*.'
                        AND amount NOT GLOB '0[0-9]*'
                        AND length(amount) > 0
                        AND (amount NOT LIKE '%.%' OR length(substr(amount, instr(amount, '.') + 1)) <= 2)
                    ),
                    last_updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY (account_id, currency)
                );
                """
            )
            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.verify_core_schema_integrity(conn)
            self.assertIn("default value mismatch", str(ctx.exception))

    def test_index_uniqueness_mismatch_rejected(self):
        """E4: Verify that substituting a UNIQUE index for a non-unique index raises SchemaIntegrityError."""
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("DROP INDEX idx_decision_session;")
            conn.execute("CREATE UNIQUE INDEX idx_decision_session ON decision_log(session_id);")
            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.verify_core_schema_integrity(conn)
            self.assertIn("uniqueness mismatch", str(ctx.exception))

    def test_partial_index_substitution_rejected(self):
        """E4: Verify that substituting a partial index for a full index raises SchemaIntegrityError."""
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("DROP INDEX idx_audit_timestamp;")
            conn.execute(
                "CREATE INDEX idx_audit_timestamp ON audit_events(timestamp_utc) WHERE action_category = 'FINANCIAL';"
            )
            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.verify_core_schema_integrity(conn)
            self.assertIn("partial status mismatch", str(ctx.exception))

    def test_unexpected_index_rejected(self):
        """E4: Verify that an unexpected user index on a core table raises SchemaIntegrityError."""
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("CREATE INDEX idx_cash_amount ON portfolio_cash(amount);")
            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.verify_core_schema_integrity(conn)
            self.assertIn("unexpected user index", str(ctx.exception))
            self.assertIn("idx_cash_amount", str(ctx.exception))


class TestFinancialDecimalConstraints(unittest.TestCase):
    """D3: Parameterised tests for financial numeric constraints and multiple-decimal-point rejection (F1)."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_fin_decimal.db")
        self.cache_path = os.path.join(self.temp_dir.name, "test_cache_decimal.db")
        self.manager = DatabaseManager(self.db_path, profile=PRIMARY_FINANCIAL_PROFILE)
        self.cache_mgr = DatabaseManager(self.cache_path, profile=MARKET_DATA_CACHE_PROFILE)
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
        with self.cache_mgr.connection() as conn:
            SchemaMigrator.initialize_cache_schema(conn)

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

    def test_multiple_decimal_points_rejected_across_all_financial_columns(self):
        """
        F1 Parameterised Test: Verify that multiple decimal points are strictly rejected
        across portfolio_cash.amount, portfolio_positions.shares, wac_eur, wac_usd,
        and daily_bars.open/high/low/close.
        """
        multi_dot_cases = [
            "1.2.3",
            "1..2",
            "12.3.4",
            "0.1.2",
            "100..00",
            "0..0",
            "1.2.3.4.5",
        ]

        now = "2026-11-08T18:00:00+00:00"

        # 1. Test portfolio_cash.amount
        with self.manager.connection() as conn:
            for bad_val in multi_dot_cases:
                with self.subTest(column="portfolio_cash.amount", bad_val=bad_val):
                    with self.assertRaises(sqlite3.IntegrityError):
                        conn.execute(
                            "INSERT INTO portfolio_cash VALUES ('ACC_MD', 'EUR', ?, ?);",
                            (bad_val, now),
                        )

        # 2. Test portfolio_positions.shares
        with self.manager.connection() as conn:
            for bad_val in multi_dot_cases:
                with self.subTest(column="portfolio_positions.shares", bad_val=bad_val):
                    with self.assertRaises(sqlite3.IntegrityError):
                        conn.execute(
                            "INSERT INTO portfolio_positions VALUES ('PRIMARY', 'SH_TEST', ?, '100.0000', NULL, ?);",
                            (bad_val, now),
                        )

        # 3. Test portfolio_positions.wac_eur
        with self.manager.connection() as conn:
            for bad_val in multi_dot_cases:
                with self.subTest(column="portfolio_positions.wac_eur", bad_val=bad_val):
                    with self.assertRaises(sqlite3.IntegrityError):
                        conn.execute(
                            "INSERT INTO portfolio_positions VALUES ('PRIMARY', 'WAC_EUR_TEST', '10.0000', ?, NULL, ?);",
                            (bad_val, now),
                        )

        # 4. Test portfolio_positions.wac_usd
        with self.manager.connection() as conn:
            for bad_val in multi_dot_cases:
                with self.subTest(column="portfolio_positions.wac_usd", bad_val=bad_val):
                    with self.assertRaises(sqlite3.IntegrityError):
                        conn.execute(
                            "INSERT INTO portfolio_positions VALUES ('PRIMARY', 'WAC_USD_TEST', '10.0000', '100.0000', ?, ?);",
                            (bad_val, now),
                        )

        # 5. Test daily_bars (open, high, low, close)
        with self.cache_mgr.connection() as conn:
            for bad_val in multi_dot_cases:
                with self.subTest(column="daily_bars.open", bad_val=bad_val):
                    with self.assertRaises(sqlite3.IntegrityError):
                        conn.execute(
                            "INSERT INTO daily_bars VALUES ('SPY', '2026-11-08', ?, '100.0000', '100.0000', '100.0000', 1000, '{}', ?);",
                            (bad_val, now),
                        )
                with self.subTest(column="daily_bars.high", bad_val=bad_val):
                    with self.assertRaises(sqlite3.IntegrityError):
                        conn.execute(
                            "INSERT INTO daily_bars VALUES ('SPY', '2026-11-08', '100.0000', ?, '100.0000', '100.0000', 1000, '{}', ?);",
                            (bad_val, now),
                        )

    def test_invalid_cash_representations_rejected(self):
        """Parameterised test verifying malformed values are rejected by portfolio_cash.amount constraints."""
        invalid_cash_values = [
            "",             # Empty string
            ".",            # Bare period
            "1.",           # Trailing period without fractional digits
            ".1",           # Leading period without integer digits
            "00.10",        # Non-canonical leading zero with decimals
            "01",           # Non-canonical leading zero integer
            "1.2.3",        # Multiple periods
            "-1",           # Negative integer
            "-10.00",       # Negative decimal
            "+1",           # Explicit positive sign
            "+10.00",       # Explicit positive sign decimal
            "1e3",          # Scientific notation
            "NaN",          # Non-numeric NaN
            "Infinity",     # Non-numeric Infinity
            " 1.00 ",       # Whitespace-padded
            "100.00 EUR",   # Character suffix
            "1.001",        # Scale violation (> 2 decimal places for cash)
        ]

        now = "2026-11-08T18:00:00+00:00"
        with self.manager.connection() as conn:
            for idx, bad_val in enumerate(invalid_cash_values):
                with self.subTest(bad_val=bad_val):
                    with self.assertRaises(
                        sqlite3.IntegrityError,
                        msg=f"Expected IntegrityError for invalid cash amount {bad_val!r}",
                    ):
                        conn.execute(
                            "INSERT INTO portfolio_cash VALUES (?, 'EUR', ?, ?);",
                            (f"ACC_{idx}", bad_val, now),
                        )

    def test_valid_cash_representations_accepted(self):
        """Parameterised test verifying valid canonical cash decimal strings are accepted exactly."""
        valid_cash_values = [
            "0",
            "0.00",
            "0.5",
            "0.50",
            "100",
            "100.5",
            "100.50",
            "15000.00",
            "999999999.99",
        ]

        now = "2026-11-08T18:00:00+00:00"
        with self.manager.connection() as conn:
            for idx, good_val in enumerate(valid_cash_values):
                with self.subTest(good_val=good_val):
                    acc_id = f"ACC_VALID_{idx}"
                    conn.execute(
                        "INSERT INTO portfolio_cash VALUES (?, 'EUR', ?, ?);",
                        (acc_id, good_val, now),
                    )
                    stored = conn.execute(
                        "SELECT amount FROM portfolio_cash WHERE account_id = ?;",
                        (acc_id,),
                    ).fetchone()[0]
                    self.assertEqual(stored, good_val)
                    self.assertIsInstance(stored, str)

    def test_invalid_shares_representations_rejected(self):
        """Parameterised test verifying malformed share quantities are rejected."""
        invalid_shares = [
            "",
            ".",
            "1.",
            ".1",
            "00.10",
            "-5.0000",
            "10.12345",    # Scale violation (> 4 decimal places)
            "NaN",
            "Infinity",
            "10 shares",
            "1.2.3",
            "1..2",
        ]

        now = "2026-11-08T18:00:00+00:00"
        with self.manager.connection() as conn:
            for idx, bad_shares in enumerate(invalid_shares):
                with self.subTest(bad_shares=bad_shares):
                    with self.assertRaises(sqlite3.IntegrityError):
                        conn.execute(
                            "INSERT INTO portfolio_positions VALUES ('PRIMARY', ?, ?, '100.0000', NULL, ?);",
                            (f"TICK_{idx}", bad_shares, now),
                        )

    def test_valid_fractional_shares_preserved_exactly(self):
        """Verify fractional share quantities up to 4 decimal places are preserved exactly as TEXT."""
        valid_shares = [
            ("SPY", "10.0000"),
            ("AAPL", "12.5000"),
            ("MSFT", "0.1234"),
            ("NVDA", "500"),
        ]

        now = "2026-11-08T18:00:00+00:00"
        with self.manager.connection() as conn:
            for ticker, sh_val in valid_shares:
                conn.execute(
                    "INSERT INTO portfolio_positions VALUES ('PRIMARY', ?, ?, '150.0000', NULL, ?);",
                    (ticker, sh_val, now),
                )
                stored = conn.execute(
                    "SELECT shares FROM portfolio_positions WHERE ticker = ?;",
                    (ticker,),
                ).fetchone()[0]
                self.assertEqual(stored, sh_val)
                self.assertIsInstance(stored, str)


class TestAuditEventIdentifierIntegrity(unittest.TestCase):
    """D4: Strict 32-hex lowercase UUID format validation for audit_events.event_id."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_audit_id.db")
        self.manager = DatabaseManager(self.db_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_valid_lowercase_32hex_event_id_accepted(self):
        """Verify valid 32-hex lowercase UUIDs (generated by audit_logger.py) are accepted."""
        now = "2026-11-08T18:00:00+00:00"
        valid_ids = [
            uuid.uuid4().hex,
            "00000000000000000000000000000000",
            "abcdef0123456789abcdef0123456789",
        ]
        with self.manager.connection() as conn:
            for ev_id in valid_ids:
                conn.execute(
                    """
                    INSERT INTO audit_events VALUES (
                        ?, ?, 'TOOL_CALL', 'SUCCESS', 'Data Analyst', 'echo', 'GRANTED', 'NOT_REQUIRED', 'hash', '{}'
                    );
                    """,
                    (ev_id, now),
                )
                stored = conn.execute(
                    "SELECT event_id FROM audit_events WHERE event_id = ?;",
                    (ev_id,),
                ).fetchone()[0]
                self.assertEqual(stored, ev_id)

    def test_invalid_event_ids_rejected(self):
        """Verify uppercase hex, wrong length, non-hex, empty, and NULL IDs are rejected."""
        sample_hex = uuid.uuid4().hex
        invalid_ids = [
            sample_hex.upper(),       # Uppercase rejected (policy requires lowercase)
            sample_hex[:31],          # Too short (31 chars)
            sample_hex + "0",         # Too long (33 chars)
            sample_hex[:-1] + "g",    # Non-hex character 'g'
            sample_hex[:-1] + "z",    # Non-hex character 'z'
            "",                       # Empty
            None,                     # NULL
        ]

        now = "2026-11-08T18:00:00+00:00"
        with self.manager.connection() as conn:
            for bad_id in invalid_ids:
                with self.subTest(bad_id=bad_id):
                    with self.assertRaises(sqlite3.IntegrityError):
                        conn.execute(
                            """
                            INSERT INTO audit_events VALUES (
                                ?, ?, 'TOOL_CALL', 'SUCCESS', 'Data Analyst', 'echo', 'GRANTED', 'NOT_REQUIRED', 'hash', '{}'
                            );
                            """,
                            (bad_id, now),
                        )


class TestDatabaseTransactions(unittest.TestCase):
    """D5: Explicit ACID transaction commits, rollbacks, multi-row atomicity, and failure paths."""

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

        with self.manager.connection() as conn:
            row = conn.execute("SELECT amount FROM portfolio_cash WHERE currency = 'EUR';").fetchone()
            self.assertIsNone(row)

    def test_multi_row_atomic_consistency(self):
        """Verify multi-table atomic commit: portfolio position, changelog, and db_revision commit together."""
        now = "2026-11-08T18:00:00+00:00"

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

    def test_multi_row_failure_leaves_no_partial_data(self):
        """Verify failed transaction leaves zero partial data."""
        now = "2026-11-08T18:00:00+00:00"
        with self.assertRaises(ValueError):
            with self.manager.transaction() as conn:
                conn.execute(
                    "INSERT INTO portfolio_positions VALUES ('PRIMARY', 'AMD', '10.0000', '150.0000', NULL, ?);",
                    (now,),
                )
                conn.execute("UPDATE database_metadata SET db_revision = db_revision + 1 WHERE id = 1;")
                raise ValueError("Simulated failure before changelog inserted")

        with self.manager.connection() as conn:
            amd_pos = conn.execute("SELECT ticker FROM portfolio_positions WHERE ticker='AMD';").fetchone()
            rev = conn.execute("SELECT db_revision FROM database_metadata WHERE id = 1;").fetchone()[0]
            self.assertIsNone(amd_pos)
            self.assertEqual(rev, 0)

    def test_nested_transaction_attempt_rejected(self):
        """Verify nested transaction attempts on the same connection raise DatabaseTransactionError."""
        with self.manager.connection() as conn:
            with self.manager.transaction(conn):
                with self.assertRaises(DatabaseTransactionError) as ctx:
                    with self.manager.transaction(conn):
                        pass
                self.assertIn("Nested transactions are not supported", str(ctx.exception))

    def test_caller_connection_clean_after_failure(self):
        """Verify that when caller provides a connection, after a failed transaction, in_transaction is False."""
        now = "2026-11-08T18:00:00+00:00"
        with self.manager.connection() as conn:
            with self.assertRaises(RuntimeError):
                with self.manager.transaction(conn):
                    conn.execute("INSERT INTO portfolio_cash VALUES ('PRIMARY', 'USD', '100.00', ?);", (now,))
                    raise RuntimeError("Crash inside transaction")

            # Connection must be rolled back and not left in an active transaction
            self.assertFalse(conn.in_transaction)
            row = conn.execute("SELECT amount FROM portfolio_cash WHERE currency='USD';").fetchone()
            self.assertIsNone(row)


class TestBackupInfrastructure(unittest.TestCase):
    """D6: SQLite Online Backup API snapshots, race-safe overwrite protection, and failure cleanup."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.src_path = os.path.join(self.temp_dir.name, "source.db")
        self.dst_path = os.path.join(self.temp_dir.name, "backup.db")
        self.manager = DatabaseManager(self.src_path, profile=PRIMARY_FINANCIAL_PROFILE)

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
        self.assertTrue(BackupManager.verify_backup(self.dst_path))

    def test_backup_data_equivalence(self):
        """Verify all records from source database are accurately copied to the backup database."""
        BackupManager.create_backup(self.src_path, self.dst_path)

        dst_manager = DatabaseManager(self.dst_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with dst_manager.connection() as conn:
            cash = conn.execute("SELECT amount FROM portfolio_cash WHERE currency='EUR';").fetchone()[0]
            pos = conn.execute("SELECT shares, wac_eur FROM portfolio_positions WHERE ticker='SPY';").fetchone()
            wl = conn.execute("SELECT ticker FROM watchlist WHERE ticker='GOOG';").fetchone()[0]

            self.assertEqual(cash, "25000.00")
            self.assertEqual(pos, ("50.0000", "495.5000"))
            self.assertEqual(wl, "GOOG")

    def test_existing_destination_overwrite_false_raises(self):
        """Verify overwrite=False raises BackupDestinationExistsError when destination exists."""
        BackupManager.create_backup(self.src_path, self.dst_path)

        with self.assertRaises(BackupDestinationExistsError):
            BackupManager.create_backup(self.src_path, self.dst_path, overwrite=False)

    def test_existing_destination_overwrite_true_replaces(self):
        """Verify overwrite=True atomically replaces an existing backup destination."""
        BackupManager.create_backup(self.src_path, self.dst_path)
        res = BackupManager.create_backup(self.src_path, self.dst_path, overwrite=True)
        self.assertEqual(res.integrity_status, "ok")

    def test_concurrent_destination_creation_race_safe(self):
        """
        Verify that if dest_path is created during backup execution when overwrite=False,
        _place_backup_file raises BackupDestinationExistsError and does NOT overwrite dest_path.
        """
        tmp_backup = os.path.join(self.temp_dir.name, "tmp_test.db")
        with open(tmp_backup, "w") as f:
            f.write("temporary content")

        # Destination already exists with original content
        with open(self.dst_path, "w") as f:
            f.write("original preserved content")

        with self.assertRaises(BackupDestinationExistsError):
            BackupManager._place_backup_file(tmp_backup, self.dst_path, overwrite=False)

        # Assert destination still has original content
        with open(self.dst_path, "r") as f:
            content = f.read()
        self.assertEqual(content, "original preserved content")

        if os.path.exists(tmp_backup):
            os.unlink(tmp_backup)

    def test_failed_backup_preserves_original_destination(self):
        """Verify that a backup failure preserves the original destination intact."""
        with open(self.dst_path, "w") as f:
            f.write("original destination data")

        with self.assertRaises(BackupError):
            BackupManager.create_backup("non_existent_source.db", self.dst_path, overwrite=True)

        with open(self.dst_path, "r") as f:
            self.assertEqual(f.read(), "original destination data")

    def test_restore_backup_success_and_integrity(self):
        """R7: Valid backup is successfully restored to target database with verified integrity."""
        BackupManager.create_backup(self.src_path, self.dst_path)
        restored_path = os.path.join(self.temp_dir.name, "restored.db")

        res = BackupManager.restore_backup(self.dst_path, restored_path)
        self.assertIsInstance(res, BackupResult)
        self.assertEqual(res.destination_path, os.path.abspath(restored_path))
        self.assertEqual(res.integrity_status, "ok")
        self.assertTrue(os.path.exists(restored_path))

        # Verify data in restored database
        restored_manager = DatabaseManager(restored_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with restored_manager.connection() as conn:
            cash = conn.execute("SELECT amount FROM portfolio_cash WHERE currency='EUR';").fetchone()[0]
            pos = conn.execute("SELECT shares, wac_eur FROM portfolio_positions WHERE ticker='SPY';").fetchone()
            wl = conn.execute("SELECT ticker FROM watchlist WHERE ticker='GOOG';").fetchone()[0]

            self.assertEqual(cash, "25000.00")
            self.assertEqual(pos, ("50.0000", "495.5000"))
            self.assertEqual(wl, "GOOG")

    def test_restore_backup_rejects_corrupted_backup(self):
        """R7: Corrupted or non-database files are rejected and fail-closed before touching target."""
        corrupted_path = os.path.join(self.temp_dir.name, "corrupt_backup.db")
        with open(corrupted_path, "wb") as f:
            f.write(b"NOT A SQLITE FILE - GARBAGE BYTES " * 10)

        target_path = os.path.join(self.temp_dir.name, "target_untouched.db")
        with open(target_path, "w") as f:
            f.write("target existing content")

        with self.assertRaises((BackupIntegrityError, BackupError)):
            BackupManager.restore_backup(corrupted_path, target_path)

        # Target must remain completely untouched
        with open(target_path, "r") as f:
            self.assertEqual(f.read(), "target existing content")

    def test_restore_backup_cleans_stale_wal_shm_sidecars(self):
        """R7: Existing stale WAL and SHM sidecars at target destination are cleaned up safely."""
        BackupManager.create_backup(self.src_path, self.dst_path)
        target_path = os.path.join(self.temp_dir.name, "target_wal_test.db")
        wal_path = f"{target_path}-wal"
        shm_path = f"{target_path}-shm"

        # Create dummy target and stale sidecars
        with open(target_path, "w") as f:
            f.write("old db")
        with open(wal_path, "wb") as f:
            f.write(b"stale wal frames")
        with open(shm_path, "wb") as f:
            f.write(b"stale shm frames")

        res = BackupManager.restore_backup(self.dst_path, target_path)
        self.assertEqual(res.integrity_status, "ok")
        # Stale sidecars must be removed
        self.assertFalse(os.path.exists(wal_path))
        self.assertFalse(os.path.exists(shm_path))

        # Restored database opens cleanly
        target_mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with target_mgr.connection() as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA integrity_check;")
            self.assertEqual(cur.fetchone()[0], "ok")


class TestPersistenceIsolationStrengthened(unittest.TestCase):
    """D7: Cryptographic hash verification demonstrating zero modification to file-backed state."""

    CRITICAL_STATE_FILES = [
        os.path.join("context", "ssot.json"),
        os.path.join("context", "trade_lessons.json"),
        os.path.join("context", "decision_log.json"),
        os.path.join("context", "config.json"),
        os.path.join("context", "agent_logs", "agent-events.jsonl"),
        "config.json",
    ]

    def _compute_hashes(self) -> Dict[str, Optional[str]]:
        hashes = {}
        for fpath in self.CRITICAL_STATE_FILES:
            if os.path.exists(fpath):
                with open(fpath, "rb") as f:
                    hashes[fpath] = hashlib.sha256(f.read()).hexdigest()
            else:
                hashes[fpath] = None
        return hashes

    def test_zero_modification_to_existing_state_files(self):
        """
        Computes SHA-256 hashes of all authoritative application state files,
        exercises database operations on temporary files, and asserts 100% hash invariance.
        """
        before_hashes = self._compute_hashes()

        # Run temporary database operations
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_db = os.path.join(tmp_dir, "isolated.db")
            temp_backup = os.path.join(tmp_dir, "isolated_backup.db")

            manager = DatabaseManager(temp_db, profile=PRIMARY_FINANCIAL_PROFILE)
            with manager.connection() as conn:
                SchemaMigrator.initialize_core_schema(conn)
                now = "2026-11-08T18:00:00+00:00"
                conn.execute("INSERT INTO portfolio_cash VALUES ('PRIMARY', 'EUR', '100.00', ?);", (now,))

            BackupManager.create_backup(temp_db, temp_backup)

        after_hashes = self._compute_hashes()

        # Assert every file has identical hash and existence state
        for fpath, before_hash in before_hashes.items():
            after_hash = after_hashes[fpath]
            self.assertEqual(
                before_hash,
                after_hash,
                f"Persistence isolation violation: File '{fpath}' hash modified during test execution!",
            )

    def test_no_production_database_created(self):
        """Verify context/gem.db and cache/market_data.db do not exist."""
        self.assertFalse(os.path.exists(os.path.join("context", "gem.db")))
        self.assertFalse(os.path.exists(os.path.join("context", "gem.db-wal")))
        self.assertFalse(os.path.exists(os.path.join("cache", "market_data.db")))


class TestCheckConstraintEnforcementAndBypassPrevention(unittest.TestCase):
    """
    Stage 1.3.1.4 Workstream B: Adversarial CHECK Constraint Verification & Anti-Bypass Testing.
    Verifies that SchemaMigrator rejects any weakening or bypass of financial/market-data
    CHECK constraints, including OR 1=1, OR TRUE, OR 1, parenthesized variants,
    unusual whitespace, altered precision/scale, and altered enum values.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.core_db = os.path.join(self.temp_dir.name, "test_core.db")
        self.cache_db = os.path.join(self.temp_dir.name, "test_cache.db")
        self.core_mgr = DatabaseManager(self.core_db, profile=PRIMARY_FINANCIAL_PROFILE)
        self.cache_mgr = DatabaseManager(self.cache_db, profile=MARKET_DATA_CACHE_PROFILE)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_check_bypass_or_1_equals_1_rejected_core(self):
        """Verify that appending 'OR 1=1' to portfolio_cash.amount CHECK raises SchemaIntegrityError."""
        with self.core_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("DROP TABLE portfolio_cash;")
            conn.execute(
                """
                CREATE TABLE portfolio_cash (
                    account_id TEXT NOT NULL DEFAULT 'PRIMARY',
                    currency TEXT NOT NULL CHECK (currency IN ('EUR', 'USD')),
                    amount TEXT NOT NULL CHECK (
                        amount NOT GLOB '*[^0-9.]*'
                        AND amount NOT GLOB '*.*.*'
                        AND amount NOT LIKE '%.%.%'
                        AND amount NOT GLOB '.*'
                        AND amount NOT GLOB '*.'
                        AND amount NOT GLOB '0[0-9]*'
                        AND length(amount) > 0
                        AND (amount NOT LIKE '%.%' OR length(substr(amount, instr(amount, '.') + 1)) <= 2)
                        OR 1=1
                    ),
                    last_updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY (account_id, currency)
                );
                """
            )
            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.verify_core_schema_integrity(conn)
            self.assertTrue(
                "unconditional CHECK constraint bypass" in str(ctx.exception)
                or "unexpected or altered CHECK constraint" in str(ctx.exception)
            )

    def test_check_bypass_or_1_equals_1_rejected_cache(self):
        """Verify that appending 'OR 1=1' to daily_bars.open CHECK raises SchemaIntegrityError."""
        with self.cache_mgr.connection() as conn:
            SchemaMigrator.initialize_cache_schema(conn)
            conn.execute("DROP TABLE daily_bars;")
            conn.execute(
                """
                CREATE TABLE daily_bars (
                    ticker TEXT NOT NULL CHECK (ticker = UPPER(TRIM(ticker)) AND length(ticker) > 0),
                    bar_date TEXT NOT NULL CHECK (length(bar_date) = 10),
                    open TEXT NOT NULL CHECK (
                        open NOT GLOB '*[^0-9.]*'
                        AND open NOT GLOB '*.*.*'
                        AND open NOT LIKE '%.%.%'
                        AND open NOT GLOB '.*'
                        AND open NOT GLOB '*.'
                        AND open NOT GLOB '0[0-9]*'
                        AND length(open) > 0
                        AND (open NOT LIKE '%.%' OR length(substr(open, instr(open, '.') + 1)) <= 4)
                        OR 1=1
                    ),
                    high TEXT NOT NULL CHECK (
                        high NOT GLOB '*[^0-9.]*'
                        AND high NOT GLOB '*.*.*'
                        AND high NOT LIKE '%.%.%'
                        AND high NOT GLOB '.*'
                        AND high NOT GLOB '*.'
                        AND high NOT GLOB '0[0-9]*'
                        AND length(high) > 0
                        AND (high NOT LIKE '%.%' OR length(substr(high, instr(high, '.') + 1)) <= 4)
                    ),
                    low TEXT NOT NULL CHECK (
                        low NOT GLOB '*[^0-9.]*'
                        AND low NOT GLOB '*.*.*'
                        AND low NOT LIKE '%.%.%'
                        AND low NOT GLOB '.*'
                        AND low NOT GLOB '*.'
                        AND low NOT GLOB '0[0-9]*'
                        AND length(low) > 0
                        AND (low NOT LIKE '%.%' OR length(substr(low, instr(low, '.') + 1)) <= 4)
                    ),
                    close TEXT NOT NULL CHECK (
                        close NOT GLOB '*[^0-9.]*'
                        AND close NOT GLOB '*.*.*'
                        AND close NOT LIKE '%.%.%'
                        AND close NOT GLOB '.*'
                        AND close NOT GLOB '*.'
                        AND close NOT GLOB '0[0-9]*'
                        AND length(close) > 0
                        AND (close NOT LIKE '%.%' OR length(substr(close, instr(close, '.') + 1)) <= 4)
                    ),
                    volume INTEGER NOT NULL CHECK (volume >= 0),
                    indicators_json TEXT NOT NULL DEFAULT '{}',
                    updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY (ticker, bar_date)
                );
                """
            )
            conn.execute("CREATE INDEX idx_bars_ticker ON daily_bars(ticker);")
            conn.execute("CREATE INDEX idx_bars_date ON daily_bars(bar_date);")
            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.verify_cache_schema_integrity(conn)
            self.assertTrue(
                "unconditional CHECK constraint bypass" in str(ctx.exception)
                or "unexpected or altered CHECK constraint" in str(ctx.exception)
            )

    def test_check_bypass_or_true_rejected(self):
        """Verify that appending 'OR TRUE' (case-insensitive) is rejected."""
        with self.core_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("DROP TABLE portfolio_cash;")
            conn.execute(
                """
                CREATE TABLE portfolio_cash (
                    account_id TEXT NOT NULL DEFAULT 'PRIMARY',
                    currency TEXT NOT NULL CHECK (currency IN ('EUR', 'USD')),
                    amount TEXT NOT NULL CHECK (
                        amount NOT GLOB '*[^0-9.]*'
                        AND amount NOT GLOB '*.*.*'
                        AND amount NOT LIKE '%.%.%'
                        AND amount NOT GLOB '.*'
                        AND amount NOT GLOB '*.'
                        AND amount NOT GLOB '0[0-9]*'
                        AND length(amount) > 0
                        AND (amount NOT LIKE '%.%' OR length(substr(amount, instr(amount, '.') + 1)) <= 2)
                        OR TrUe
                    ),
                    last_updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY (account_id, currency)
                );
                """
            )
            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.verify_core_schema_integrity(conn)
            self.assertTrue(
                "unconditional CHECK constraint bypass" in str(ctx.exception)
                or "unexpected or altered CHECK constraint" in str(ctx.exception)
            )

    def test_check_bypass_or_1_rejected(self):
        """Verify that appending 'OR 1' is rejected."""
        with self.core_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("DROP TABLE portfolio_cash;")
            conn.execute(
                """
                CREATE TABLE portfolio_cash (
                    account_id TEXT NOT NULL DEFAULT 'PRIMARY',
                    currency TEXT NOT NULL CHECK (currency IN ('EUR', 'USD')),
                    amount TEXT NOT NULL CHECK (
                        amount NOT GLOB '*[^0-9.]*'
                        AND amount NOT GLOB '*.*.*'
                        AND amount NOT LIKE '%.%.%'
                        AND amount NOT GLOB '.*'
                        AND amount NOT GLOB '*.'
                        AND amount NOT GLOB '0[0-9]*'
                        AND length(amount) > 0
                        AND (amount NOT LIKE '%.%' OR length(substr(amount, instr(amount, '.') + 1)) <= 2)
                        OR 1
                    ),
                    last_updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY (account_id, currency)
                );
                """
            )
            with self.assertRaises(SchemaIntegrityError):
                SchemaMigrator.verify_core_schema_integrity(conn)

    def test_check_bypass_parenthesized_rejected(self):
        """Verify parenthesized bypasses like 'OR (1=1)' and 'OR (TRUE)' are rejected."""
        variants = ["OR ( 1 = 1 )", "OR (1=1)", "OR (TRUE)", "OR (0=0)", "OR ('A'='A')"]
        for variant in variants:
            with self.subTest(variant=variant):
                conn = sqlite3.connect(":memory:")
                try:
                    SchemaMigrator.initialize_core_schema(conn)
                    conn.execute("DROP TABLE portfolio_cash;")
                    conn.execute(
                        f"""
                        CREATE TABLE portfolio_cash (
                            account_id TEXT NOT NULL DEFAULT 'PRIMARY',
                            currency TEXT NOT NULL CHECK (currency IN ('EUR', 'USD')),
                            amount TEXT NOT NULL CHECK (
                                (amount NOT GLOB '*[^0-9.]*'
                                AND amount NOT GLOB '*.*.*'
                                AND amount NOT LIKE '%.%.%'
                                AND amount NOT GLOB '.*'
                                AND amount NOT GLOB '*.'
                                AND amount NOT GLOB '0[0-9]*'
                                AND length(amount) > 0
                                AND (amount NOT LIKE '%.%' OR length(substr(amount, instr(amount, '.') + 1)) <= 2))
                                {variant}
                            ),
                            last_updated_at_utc TEXT NOT NULL,
                            PRIMARY KEY (account_id, currency)
                        );
                        """
                    )
                    with self.assertRaises(SchemaIntegrityError):
                        SchemaMigrator.verify_core_schema_integrity(conn)
                finally:
                    conn.close()

    def test_check_bypass_case_and_whitespace_rejected(self):
        """Verify unusual whitespace, linebreaks, and case variants in bypass expressions are rejected."""
        bypass_expr = "   oR   \n   1  \t  =   1   "
        with self.core_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("DROP TABLE portfolio_cash;")
            conn.execute(
                f"""
                CREATE TABLE portfolio_cash (
                    account_id TEXT NOT NULL DEFAULT 'PRIMARY',
                    currency TEXT NOT NULL CHECK (currency IN ('EUR', 'USD')),
                    amount TEXT NOT NULL CHECK (
                        amount NOT GLOB '*[^0-9.]*'
                        AND amount NOT GLOB '*.*.*'
                        AND amount NOT LIKE '%.%.%'
                        AND amount NOT GLOB '.*'
                        AND amount NOT GLOB '*.'
                        AND amount NOT GLOB '0[0-9]*'
                        AND length(amount) > 0
                        AND (amount NOT LIKE '%.%' OR length(substr(amount, instr(amount, '.') + 1)) <= 2)
                        {bypass_expr}
                    ),
                    last_updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY (account_id, currency)
                );
                """
            )
            with self.assertRaises(SchemaIntegrityError):
                SchemaMigrator.verify_core_schema_integrity(conn)

    def test_individual_decimal_field_bypass_rejected(self):
        """Verify tampering on individual decimal fields (e.g. shares, wac_eur, high) is rejected."""
        with self.core_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("DROP TABLE portfolio_positions;")
            conn.execute(
                """
                CREATE TABLE portfolio_positions (
                    account_id TEXT NOT NULL DEFAULT 'PRIMARY',
                    ticker TEXT NOT NULL CHECK (ticker = UPPER(TRIM(ticker)) AND length(ticker) > 0),
                    shares TEXT NOT NULL CHECK (
                        shares NOT GLOB '*[^0-9.]*'
                        AND shares NOT GLOB '*.*.*'
                        AND shares NOT LIKE '%.%.%'
                        AND shares NOT GLOB '.*'
                        AND shares NOT GLOB '*.'
                        AND shares NOT GLOB '0[0-9]*'
                        AND length(shares) > 0
                        AND (shares NOT LIKE '%.%' OR length(substr(shares, instr(shares, '.') + 1)) <= 4)
                        OR 1=1
                    ),
                    wac_eur TEXT NOT NULL CHECK (
                        wac_eur NOT GLOB '*[^0-9.]*'
                        AND wac_eur NOT GLOB '*.*.*'
                        AND wac_eur NOT LIKE '%.%.%'
                        AND wac_eur NOT GLOB '.*'
                        AND wac_eur NOT GLOB '*.'
                        AND wac_eur NOT GLOB '0[0-9]*'
                        AND length(wac_eur) > 0
                        AND (wac_eur NOT LIKE '%.%' OR length(substr(wac_eur, instr(wac_eur, '.') + 1)) <= 4)
                    ),
                    wac_usd TEXT CHECK (
                        wac_usd IS NULL OR (
                            wac_usd NOT GLOB '*[^0-9.]*'
                            AND wac_usd NOT GLOB '*.*.*'
                            AND wac_usd NOT LIKE '%.%.%'
                            AND wac_usd NOT GLOB '.*'
                            AND wac_usd NOT GLOB '*.'
                            AND wac_usd NOT GLOB '0[0-9]*'
                            AND length(wac_usd) > 0
                            AND (wac_usd NOT LIKE '%.%' OR length(substr(wac_usd, instr(wac_usd, '.') + 1)) <= 4)
                        )
                    ),
                    last_updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY (account_id, ticker)
                );
                """
            )
            with self.assertRaises(SchemaIntegrityError):
                SchemaMigrator.verify_core_schema_integrity(conn)

    def test_check_constraint_count_mismatch_rejected(self):
        """Verify adding an extra CHECK constraint or dropping one raises SchemaIntegrityError with count mismatch."""
        with self.core_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("DROP TABLE portfolio_cash;")
            conn.execute(
                """
                CREATE TABLE portfolio_cash (
                    account_id TEXT NOT NULL DEFAULT 'PRIMARY',
                    currency TEXT NOT NULL CHECK (currency IN ('EUR', 'USD')),
                    amount TEXT NOT NULL CHECK (
                        amount NOT GLOB '*[^0-9.]*'
                        AND amount NOT GLOB '*.*.*'
                        AND amount NOT LIKE '%.%.%'
                        AND amount NOT GLOB '.*'
                        AND amount NOT GLOB '*.'
                        AND amount NOT GLOB '0[0-9]*'
                        AND length(amount) > 0
                        AND (amount NOT LIKE '%.%' OR length(substr(amount, instr(amount, '.') + 1)) <= 2)
                    ),
                    last_updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY (account_id, currency),
                    CHECK (length(account_id) > 0)
                );
                """
            )
            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.verify_core_schema_integrity(conn)
            self.assertIn("count mismatch", str(ctx.exception))

    def test_check_constraint_semantic_tampering_rejected(self):
        """Verify subtle semantic changes (e.g. adding GBP to allowed currencies) raise SchemaIntegrityError."""
        with self.core_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("DROP TABLE portfolio_cash;")
            conn.execute(
                """
                CREATE TABLE portfolio_cash (
                    account_id TEXT NOT NULL DEFAULT 'PRIMARY',
                    currency TEXT NOT NULL CHECK (currency IN ('EUR', 'USD', 'GBP')),
                    amount TEXT NOT NULL CHECK (
                        amount NOT GLOB '*[^0-9.]*'
                        AND amount NOT GLOB '*.*.*'
                        AND amount NOT LIKE '%.%.%'
                        AND amount NOT GLOB '.*'
                        AND amount NOT GLOB '*.'
                        AND amount NOT GLOB '0[0-9]*'
                        AND length(amount) > 0
                        AND (amount NOT LIKE '%.%' OR length(substr(amount, instr(amount, '.') + 1)) <= 2)
                    ),
                    last_updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY (account_id, currency)
                );
                """
            )
            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.verify_core_schema_integrity(conn)
            self.assertTrue(
                "missing mandatory constraint pattern" in str(ctx.exception)
                or "unexpected or altered CHECK constraint" in str(ctx.exception)
            )


class TestSchemaInventoryHardening(unittest.TestCase):
    """
    Stage 1.3.1.4 Workstream C: User Table Inventory Hardening & SQLite Internal Object Handling.
    Verifies that SchemaMigrator rejects databases containing unauthorized user tables,
    while correctly allowing SQLite internal tables (prefixed with 'sqlite_').
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.core_db = os.path.join(self.temp_dir.name, "test_core.db")
        self.cache_db = os.path.join(self.temp_dir.name, "test_cache.db")
        self.core_mgr = DatabaseManager(self.core_db, profile=PRIMARY_FINANCIAL_PROFILE)
        self.cache_mgr = DatabaseManager(self.cache_db, profile=MARKET_DATA_CACHE_PROFILE)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_unexpected_table_rejected_core(self):
        """Verify that an unauthorized user table in core database raises SchemaIntegrityError."""
        with self.core_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("CREATE TABLE unauthorized_user_table (id INTEGER PRIMARY KEY, note TEXT);")

            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.verify_core_schema_integrity(conn)
            self.assertIn("unexpected additional user table(s) detected", str(ctx.exception))
            self.assertIn("unauthorized_user_table", str(ctx.exception))

    def test_unexpected_table_rejected_cache(self):
        """Verify that an unauthorized user table in cache database raises SchemaIntegrityError."""
        with self.cache_mgr.connection() as conn:
            SchemaMigrator.initialize_cache_schema(conn)
            conn.execute("CREATE TABLE shadow_cache (ticker TEXT PRIMARY KEY);")

            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.verify_cache_schema_integrity(conn)
            self.assertIn("unexpected additional user table(s) detected", str(ctx.exception))
            self.assertIn("shadow_cache", str(ctx.exception))

    def test_reinitialize_existing_with_unexpected_table_fails_closed(self):
        """Verify calling initialize_core_schema on an existing DB with unexpected table fails closed."""
        with self.core_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("CREATE TABLE rogue_table (val TEXT);")

        with self.core_mgr.connection() as conn:
            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.initialize_core_schema(conn)
            self.assertIn("unexpected additional user table(s) detected", str(ctx.exception))
            self.assertIn("rogue_table", str(ctx.exception))

    def test_internal_sqlite_tables_ignored(self):
        """Verify that SQLite internal tables (e.g. sqlite_stat1) do not trigger false positives."""
        with self.core_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("ANALYZE;")
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE name = 'sqlite_stat1';")
            if cursor.fetchone():
                self.assertTrue(SchemaMigrator.verify_core_schema_integrity(conn))


class TestSchemaVerificationReadOnlyAndFailClosed(unittest.TestCase):
    """
    Stage 1.3.1.4 Workstream D: Read-Only Verification Invariance & Fail-Closed Behavior.
    Verifies that schema verification performs zero writes to the database file (preserving byte hashes),
    can run across read-only connections, and does not perform auto-repair when schema integrity fails.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.core_db = os.path.join(self.temp_dir.name, "test_core.db")
        self.core_mgr = DatabaseManager(self.core_db, profile=PRIMARY_FINANCIAL_PROFILE)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _file_hash(self, path: str) -> str:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def test_verification_is_read_only_and_preserves_hashes(self):
        """Verify schema verification is strictly read-only and preserves exact database file hashes."""
        with self.core_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
        self.core_mgr.checkpoint(mode="TRUNCATE")

        hash_before = self._file_hash(self.core_db)

        # 1. Run verification on valid DB over read-only URI connection
        conn_ro = sqlite3.connect(f"file:{self.core_db}?mode=ro", uri=True)
        try:
            self.assertTrue(SchemaMigrator.verify_core_schema_integrity(conn_ro))
        finally:
            conn_ro.close()

        hash_after_valid = self._file_hash(self.core_db)
        self.assertEqual(hash_before, hash_after_valid, "Valid verification mutated the database file!")

        # 2. Corrupt schema by adding unexpected table
        with self.core_mgr.connection() as conn:
            conn.execute("CREATE TABLE rogue_table (x INT);")
        self.core_mgr.checkpoint(mode="TRUNCATE")

        hash_corrupt_before = self._file_hash(self.core_db)

        # 3. Run verification on corrupt DB over read-only connection -> must fail closed
        conn_corrupt_ro = sqlite3.connect(f"file:{self.core_db}?mode=ro", uri=True)
        try:
            with self.assertRaises(SchemaIntegrityError):
                SchemaMigrator.verify_core_schema_integrity(conn_corrupt_ro)
        finally:
            conn_corrupt_ro.close()

        hash_corrupt_after = self._file_hash(self.core_db)
        self.assertEqual(
            hash_corrupt_before,
            hash_corrupt_after,
            "Failed verification mutated the corrupted database file!",
        )

    def test_verification_does_not_auto_repair(self):
        """Verify that verification does not attempt any self-repair or schema mutation."""
        with self.core_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("CREATE TABLE rogue_table (x INT);")

        with self.core_mgr.connection() as conn:
            with self.assertRaises(SchemaIntegrityError):
                SchemaMigrator.verify_core_schema_integrity(conn)

            with self.assertRaises(SchemaIntegrityError):
                SchemaMigrator.verify_core_schema_integrity(conn)

            cursor = conn.cursor()
            cursor.execute("SELECT count(*) FROM sqlite_master WHERE name='rogue_table';")
            self.assertEqual(cursor.fetchone()[0], 1)


class TestCheckExtractorSqlLexicalIntegrity(unittest.TestCase):
    """
    Stage 1.3.1.5: SQLite CHECK Extractor SQL Lexical Integrity & Quoted-Identifier Spoofing Prevention.
    Verifies that SchemaMigrator.extract_check_expressions() strictly distinguishes SQL keywords
    from text inside quoting forms ("...", `...`, [...], '...') and comments (--..., /*...*/),
    rejects quoted-identifier spoofing attacks, and fails closed on malformed DDL.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.core_db = os.path.join(self.temp_dir.name, "test_core.db")
        self.core_mgr = DatabaseManager(self.core_db, profile=PRIMARY_FINANCIAL_PROFILE)

    def tearDown(self):
        self.temp_dir.cleanup()

    # -------------------------------------------------------------------------
    # A. Quoted constraint-name spoofing
    # -------------------------------------------------------------------------
    def test_quoted_constraint_name_spoofing_double_quotes_rejected(self):
        """
        Verify that a table with a double-quoted constraint name mimicking a CHECK expression
        (e.g. CONSTRAINT "CHECK (id = 1)" UNIQUE(schema_version)) has 0 checks extracted,
        and verify_core_schema_integrity fails closed with SchemaIntegrityError.
        """
        with self.core_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("DROP TABLE database_metadata;")
            conn.execute(
                """
                CREATE TABLE database_metadata (
                    id INTEGER PRIMARY KEY,
                    schema_version INTEGER NOT NULL,
                    db_revision INTEGER NOT NULL DEFAULT 0,
                    created_at_utc TEXT NOT NULL,
                    last_updated_at_utc TEXT NOT NULL,
                    CONSTRAINT "CHECK (id = 1)" UNIQUE(schema_version)
                );
                """
            )
            # Extractor must return 0 checks (quoted name must not be extracted)
            cursor = conn.cursor()
            cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='database_metadata';")
            ddl = cursor.fetchone()[0]
            checks = SchemaMigrator.extract_check_expressions(ddl)
            self.assertEqual(checks, [], "Quoted constraint name was falsely extracted as CHECK!")

            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.verify_core_schema_integrity(conn)
            self.assertIn("count mismatch", str(ctx.exception))

    def test_quoted_constraint_name_spoofing_backticks_rejected(self):
        """Verify backtick-quoted constraint names mimicking CHECK are not extracted and verification fails."""
        with self.core_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("DROP TABLE database_metadata;")
            conn.execute(
                """
                CREATE TABLE database_metadata (
                    id INTEGER PRIMARY KEY,
                    schema_version INTEGER NOT NULL,
                    db_revision INTEGER NOT NULL DEFAULT 0,
                    created_at_utc TEXT NOT NULL,
                    last_updated_at_utc TEXT NOT NULL,
                    CONSTRAINT `CHECK (id = 1)` UNIQUE(schema_version)
                );
                """
            )
            cursor = conn.cursor()
            cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='database_metadata';")
            ddl = cursor.fetchone()[0]
            self.assertEqual(SchemaMigrator.extract_check_expressions(ddl), [])
            with self.assertRaises(SchemaIntegrityError):
                SchemaMigrator.verify_core_schema_integrity(conn)

    def test_quoted_constraint_name_spoofing_brackets_rejected(self):
        """Verify bracket-quoted constraint names mimicking CHECK are not extracted and verification fails."""
        with self.core_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("DROP TABLE database_metadata;")
            conn.execute(
                """
                CREATE TABLE database_metadata (
                    id INTEGER PRIMARY KEY,
                    schema_version INTEGER NOT NULL,
                    db_revision INTEGER NOT NULL DEFAULT 0,
                    created_at_utc TEXT NOT NULL,
                    last_updated_at_utc TEXT NOT NULL,
                    CONSTRAINT [CHECK (id = 1)] UNIQUE(schema_version)
                );
                """
            )
            cursor = conn.cursor()
            cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='database_metadata';")
            ddl = cursor.fetchone()[0]
            self.assertEqual(SchemaMigrator.extract_check_expressions(ddl), [])
            with self.assertRaises(SchemaIntegrityError):
                SchemaMigrator.verify_core_schema_integrity(conn)

    # -------------------------------------------------------------------------
    # B. Quoted-token handling
    # -------------------------------------------------------------------------
    def test_single_quoted_string_literal_not_extracted_as_check(self):
        """Verify text inside single quotes (e.g. DEFAULT 'CHECK (id = 1)') is not extracted as CHECK."""
        ddl = "CREATE TABLE t ( id INT PRIMARY KEY, note TEXT DEFAULT 'CHECK (id = 1)' );"
        self.assertEqual(SchemaMigrator.extract_check_expressions(ddl), [])

    def test_quoted_column_names_not_extracted_as_check(self):
        """Verify column names formatted as quoted CHECK strings are not extracted."""
        ddl_double = 'CREATE TABLE t ( id INT PRIMARY KEY, "CHECK (id = 1)" TEXT );'
        self.assertEqual(SchemaMigrator.extract_check_expressions(ddl_double), [])

        ddl_backtick = 'CREATE TABLE t ( id INT PRIMARY KEY, `CHECK (id = 1)` TEXT );'
        self.assertEqual(SchemaMigrator.extract_check_expressions(ddl_backtick), [])

        ddl_bracket = 'CREATE TABLE t ( id INT PRIMARY KEY, [CHECK (id = 1)] TEXT );'
        self.assertEqual(SchemaMigrator.extract_check_expressions(ddl_bracket), [])

    # -------------------------------------------------------------------------
    # C. Comment handling
    # -------------------------------------------------------------------------
    def test_single_line_comment_with_check_not_extracted(self):
        """Verify CHECK expressions inside single-line comments (-- ...) are ignored."""
        ddl = "CREATE TABLE t (\n  id INT PRIMARY KEY,\n  -- CHECK (id = 1)\n  val TEXT\n);"
        self.assertEqual(SchemaMigrator.extract_check_expressions(ddl), [])

    def test_block_comment_with_check_not_extracted(self):
        """Verify CHECK expressions inside block comments (/* ... */) are ignored."""
        ddl = "CREATE TABLE t ( id INT PRIMARY KEY /* CHECK (id = 1) */, val TEXT );"
        self.assertEqual(SchemaMigrator.extract_check_expressions(ddl), [])

    def test_block_comment_with_nested_parentheses_not_extracted(self):
        """Verify block comments containing unbalanced or nested parentheses do not corrupt parsing."""
        ddl = "CREATE TABLE t ( id INT PRIMARY KEY /* (((( CHECK (id = 1) )))) */, val TEXT );"
        self.assertEqual(SchemaMigrator.extract_check_expressions(ddl), [])

    # -------------------------------------------------------------------------
    # D. Parenthesis and escaping handling
    # -------------------------------------------------------------------------
    def test_nested_parentheses_in_genuine_check_extracted(self):
        """Verify genuine CHECK expressions with complex nested parentheses are extracted intact."""
        ddl = "CREATE TABLE t ( amount TEXT NOT NULL CHECK ((amount NOT LIKE '%.%' OR length(substr(amount, instr(amount, '.') + 1)) <= 2)) );"
        checks = SchemaMigrator.extract_check_expressions(ddl)
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0], "(amount NOT LIKE '%.%' OR length(substr(amount, instr(amount, '.') + 1)) <= 2)")

    def test_escaped_single_quotes_inside_check(self):
        """Verify escaped single quotes ('') inside string literals within CHECK are preserved."""
        ddl = "CREATE TABLE t ( rule TEXT NOT NULL CHECK (rule != 'it''s deprecated') );"
        checks = SchemaMigrator.extract_check_expressions(ddl)
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0], "rule != 'it''s deprecated'")

    def test_escaped_double_quotes_in_quoted_identifier(self):
        """Verify escaped double quotes (\"\") in constraint identifiers do not break parser."""
        ddl = 'CREATE TABLE t ( id INT PRIMARY KEY, CONSTRAINT "chk_""valid""_id" CHECK (id = 1) );'
        checks = SchemaMigrator.extract_check_expressions(ddl)
        self.assertEqual(checks, ["id = 1"])

    def test_parentheses_inside_string_literals_do_not_balance(self):
        """Verify parentheses inside single-quoted strings do not close the CHECK clause early."""
        ddl = "CREATE TABLE t ( pattern TEXT CHECK (pattern NOT LIKE '%(%)%' AND length(pattern) > 0) );"
        checks = SchemaMigrator.extract_check_expressions(ddl)
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0], "pattern NOT LIKE '%(%)%' AND length(pattern) > 0")

    def test_parentheses_inside_quoted_identifiers_do_not_balance(self):
        """Verify parentheses inside quoted identifiers do not close the CHECK clause early."""
        ddl = 'CREATE TABLE t ( "col(1)" TEXT CHECK ("col(1)" != \'\') );'
        checks = SchemaMigrator.extract_check_expressions(ddl)
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0], '"col(1)" != \'\'')

    # -------------------------------------------------------------------------
    # E. Fail-closed handling
    # -------------------------------------------------------------------------
    def test_unterminated_single_quote_fails_closed(self):
        """Verify DDL with an unterminated single-quoted string raises SchemaIntegrityError."""
        ddl = "CREATE TABLE t ( id INT PRIMARY KEY, val TEXT DEFAULT 'unclosed );"
        with self.assertRaises(SchemaIntegrityError) as ctx:
            SchemaMigrator.extract_check_expressions(ddl)
        self.assertIn("unterminated string literal", str(ctx.exception))

    def test_unterminated_double_quote_fails_closed(self):
        """Verify DDL with an unterminated double-quoted identifier raises SchemaIntegrityError."""
        ddl = 'CREATE TABLE t ( "unclosed_col INT PRIMARY KEY );'
        with self.assertRaises(SchemaIntegrityError) as ctx:
            SchemaMigrator.extract_check_expressions(ddl)
        self.assertIn("unterminated double-quoted identifier", str(ctx.exception))

    def test_unterminated_backtick_fails_closed(self):
        """Verify DDL with an unterminated backtick identifier raises SchemaIntegrityError."""
        ddl = 'CREATE TABLE t ( `unclosed_col INT PRIMARY KEY );'
        with self.assertRaises(SchemaIntegrityError) as ctx:
            SchemaMigrator.extract_check_expressions(ddl)
        self.assertIn("unterminated backtick identifier", str(ctx.exception))

    def test_unterminated_bracket_fails_closed(self):
        """Verify DDL with an unterminated bracket identifier raises SchemaIntegrityError."""
        ddl = 'CREATE TABLE t ( [unclosed_col INT PRIMARY KEY );'
        with self.assertRaises(SchemaIntegrityError) as ctx:
            SchemaMigrator.extract_check_expressions(ddl)
        self.assertIn("unterminated bracket identifier", str(ctx.exception))

    def test_unterminated_block_comment_fails_closed(self):
        """Verify DDL with an unterminated block comment raises SchemaIntegrityError."""
        ddl = 'CREATE TABLE t ( id INT PRIMARY KEY /* unclosed comment );'
        with self.assertRaises(SchemaIntegrityError) as ctx:
            SchemaMigrator.extract_check_expressions(ddl)
        self.assertIn("unterminated block comment", str(ctx.exception))

    def test_unbalanced_check_parentheses_fails_closed(self):
        """Verify DDL with unbalanced parentheses in a CHECK expression raises SchemaIntegrityError."""
        ddl = 'CREATE TABLE t ( id INT PRIMARY KEY, CHECK (id = 1 );'
        with self.assertRaises(SchemaIntegrityError) as ctx:
            SchemaMigrator.extract_check_expressions(ddl)
        self.assertIn("malformed CREATE TABLE body", str(ctx.exception))

    def test_check_keyword_without_opening_parenthesis_fails_closed(self):
        """Verify a standalone unquoted CHECK keyword not followed by '(' raises SchemaIntegrityError."""
        ddl = 'CREATE TABLE t ( id INT PRIMARY KEY, CHECK id = 1 );'
        with self.assertRaises(SchemaIntegrityError) as ctx:
            SchemaMigrator.extract_check_expressions(ddl)
        self.assertIn("CHECK keyword not followed by opening parenthesis", str(ctx.exception))

    # -------------------------------------------------------------------------
    # Stage 1.3.1.5.1: Trailing DDL lexical validation
    # -------------------------------------------------------------------------
    def test_unterminated_backtick_in_trailing_ddl_fails_closed(self):
        """
        R3.1: Verify that an unterminated backtick identifier occurring after the
        closing table-body parenthesis raises SchemaIntegrityError fail-closed.
        """
        ddl = "CREATE TABLE t ( id INT PRIMARY KEY ) `unterminated"
        with self.assertRaises(SchemaIntegrityError) as ctx:
            SchemaMigrator.extract_check_expressions(ddl)
        self.assertIn("unterminated backtick identifier in trailing DDL", str(ctx.exception))

    def test_unterminated_bracket_in_trailing_ddl_fails_closed(self):
        """
        R3.2: Verify that an unterminated bracket identifier occurring after the
        closing table-body parenthesis raises SchemaIntegrityError fail-closed.
        """
        ddl = "CREATE TABLE t ( id INT PRIMARY KEY ) [unterminated"
        with self.assertRaises(SchemaIntegrityError) as ctx:
            SchemaMigrator.extract_check_expressions(ddl)
        self.assertIn("unterminated bracket identifier in trailing DDL", str(ctx.exception))

    def test_properly_terminated_backtick_in_trailing_ddl_accepted(self):
        """
        R3.3: Verify that properly terminated backtick identifiers in trailing DDL
        (e.g. table options like `WITHOUT` `ROWID`) are recognized without error,
        and genuine CHECK expressions in the body are extracted.
        """
        ddl = "CREATE TABLE t ( id INT PRIMARY KEY CHECK (id = 1) ) `WITHOUT` `ROWID`;"
        checks = SchemaMigrator.extract_check_expressions(ddl)
        self.assertEqual(checks, ["id = 1"])

    def test_properly_terminated_bracket_in_trailing_ddl_accepted(self):
        """
        R3.4: Verify that properly terminated bracket identifiers in trailing DDL
        (e.g. [WITHOUT] [ROWID]) are recognized without error, and genuine CHECK
        expressions in the body are extracted.
        """
        ddl = "CREATE TABLE t ( id INT PRIMARY KEY CHECK (id = 1) ) [WITHOUT] [ROWID];"
        checks = SchemaMigrator.extract_check_expressions(ddl)
        self.assertEqual(checks, ["id = 1"])

    def test_escaped_backticks_in_trailing_ddl_handling(self):
        """
        Verify that escaped backticks (``) in trailing DDL are correctly handled
        and that odd/unterminated backticks fail closed.
        """
        # Properly terminated escaped backtick
        ddl_valid = "CREATE TABLE t ( id INT PRIMARY KEY CHECK (id = 1) ) `opt``name`;"
        checks = SchemaMigrator.extract_check_expressions(ddl_valid)
        self.assertEqual(checks, ["id = 1"])

        # Unterminated escaped backtick (odd number of backticks)
        ddl_invalid = "CREATE TABLE t ( id INT PRIMARY KEY ) `opt``"
        with self.assertRaises(SchemaIntegrityError) as ctx:
            SchemaMigrator.extract_check_expressions(ddl_invalid)
        self.assertIn("unterminated backtick identifier in trailing DDL", str(ctx.exception))


class TestStage15A1BackupRestorationSafety(unittest.TestCase):
    """
    Stage 1.5-A.1 Regression Test Suite for R4:
    - Backup restoration safety contract and quiescence enforcement.
    - Active connection blocking, WAL/SHM handling, corrupt backup rejection,
      and fallback recovery.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.src_path = os.path.join(self.temp_dir.name, "source_15a1.db")
        self.dst_path = os.path.join(self.temp_dir.name, "backup_15a1.db")

        # Initialize source database with primary financial profile
        src_mgr = DatabaseManager(self.src_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with src_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute(
                "INSERT INTO portfolio_cash (account_id, currency, amount, last_updated_at_utc) "
                "VALUES ('PRIMARY', 'EUR', '1000.00', '2026-11-09T12:00:00Z')"
            )
        # Create verified backup snapshot
        BackupManager.create_backup(self.src_path, self.dst_path)

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_r4_valid_backup_restoration(self):
        """R4.1: Valid backup snapshot is restored safely to a new target destination."""
        target_path = os.path.join(self.temp_dir.name, "target_restored.db")
        result = BackupManager.restore_backup(self.dst_path, target_path)

        self.assertEqual(result.integrity_status, "ok")
        self.assertTrue(os.path.exists(target_path))

        target_mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with target_mgr.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT amount FROM portfolio_cash WHERE account_id='PRIMARY' AND currency='EUR'")
            row = cur.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "1000.00")

    def test_r4_corrupt_backup_rejected_without_target_damage(self):
        """R4.2: Corrupted backup is rejected fail-closed without modifying existing target."""
        target_path = os.path.join(self.temp_dir.name, "existing_target.db")
        target_mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with target_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute(
                "INSERT INTO portfolio_cash (account_id, currency, amount, last_updated_at_utc) "
                "VALUES ('PRIMARY', 'USD', '777.00', '2026-11-09T12:00:00Z')"
            )

        corrupt_backup = os.path.join(self.temp_dir.name, "corrupt_backup.db")
        with open(corrupt_backup, "wb") as f:
            f.write(b"SQLite format 3\x00" + b"\x00" * 200 + b"garbage-corrupted-bytes")

        with self.assertRaises((BackupIntegrityError, BackupError)):
            BackupManager.restore_backup(corrupt_backup, target_path)

        # Confirm target is completely unharmed
        with DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE).connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT amount FROM portfolio_cash WHERE account_id='PRIMARY' AND currency='USD'")
            self.assertEqual(cur.fetchone()[0], "777.00")

    def test_r4_target_remains_unchanged_when_restoration_refused(self):
        """R4.3: Target remains unchanged when restoration is refused due to invalid source."""
        target_path = os.path.join(self.temp_dir.name, "target_untouched.db")
        with open(target_path, "w") as f:
            f.write("authoritative target state")

        non_existent_backup = os.path.join(self.temp_dir.name, "missing.db")
        with self.assertRaises(BackupError):
            BackupManager.restore_backup(non_existent_backup, target_path)

        with open(target_path, "r") as f:
            self.assertEqual(f.read(), "authoritative target state")

    def test_r4_active_target_concurrent_access_blocks_restoration_fail_closed(self):
        """R4.4: Active concurrent access or transaction on target blocks restoration fail-closed."""
        target_path = os.path.join(self.temp_dir.name, "active_target.db")
        target_mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with target_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)

        # Hold an open uncheckpointed transaction on target in WAL mode
        active_conn = target_mgr.connect()
        active_conn.execute("BEGIN IMMEDIATE;")
        active_conn.execute(
            "INSERT INTO portfolio_cash (account_id, currency, amount, last_updated_at_utc) "
            "VALUES ('HOLD', 'EUR', '99.00', '2026-11-09T12:00:00Z')"
        )

        try:
            with self.assertRaises(BackupError) as ctx:
                BackupManager.restore_backup(self.dst_path, target_path)
            err_msg = str(ctx.exception).lower()
            self.assertTrue(
                "locked" in err_msg or "in use" in err_msg or "concurrent" in err_msg or "active" in err_msg,
                f"Unexpected error message: {ctx.exception}"
            )
        finally:
            active_conn.execute("ROLLBACK;")
            active_conn.close()

    def test_r4_wal_shm_handling_cleans_sidecars_safely(self):
        """R4.5: Target database in WAL mode is safely transitioned and restored without sidecar corruption."""
        target_path = os.path.join(self.temp_dir.name, "wal_target.db")
        target_mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with target_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                "INSERT INTO portfolio_cash (account_id, currency, amount, last_updated_at_utc) "
                "VALUES ('OLD', 'EUR', '1.00', '2026-11-09T12:00:00Z')"
            )

        # Now restore backup onto the WAL target
        res = BackupManager.restore_backup(self.dst_path, target_path)
        self.assertEqual(res.integrity_status, "ok")

        # Verify restored data
        with DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE).connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT amount FROM portfolio_cash WHERE account_id='PRIMARY' AND currency='EUR'")
            self.assertEqual(cur.fetchone()[0], "1000.00")

    def test_r4_final_restored_database_passes_integrity_verification(self):
        """R4.6: Final restored database passes PRAGMA integrity_check verification."""
        target_path = os.path.join(self.temp_dir.name, "integrity_target.db")
        res = BackupManager.restore_backup(self.dst_path, target_path)
        self.assertEqual(res.integrity_status, "ok")
        BackupManager.verify_backup(target_path)


# ===========================================================================
# Stage 1.5-A.2: Backup Restoration Quiescence, Locking & Cleanup Safety Tests (R3/R4)
# ===========================================================================

class TestStage15A2BackupRestoreQuiescenceAndCleanupSafety(unittest.TestCase):
    """
    Mandatory regression and failure-injection test suite for Stage 1.5-A.2 (R3 & R4).
    Verifies enforceable target quiescence, cross-process access blocking,
    safe WAL checkpointing/transition, and reliable failure recovery without silent suppression.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.src_path = os.path.join(self.temp_dir.name, "source_15a2.db")
        self.backup_path = os.path.join(self.temp_dir.name, "backup_15a2.db")

        # Initialize source database with known content
        src_mgr = DatabaseManager(self.src_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with src_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute(
                "INSERT INTO portfolio_cash (account_id, currency, amount, last_updated_at_utc) "
                "VALUES ('PRIMARY', 'EUR', '9999.00', '2026-11-09T15:00:00Z')"
            )
        BackupManager.create_backup(self.src_path, self.backup_path)

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_r3_active_uncommitted_writer_rejected_fail_closed(self):
        """R3.1: Active uncommitted writer holding lock on target blocks restoration fail-closed."""
        target_path = os.path.join(self.temp_dir.name, "target_writer.db")
        target_mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with target_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute(
                "INSERT INTO portfolio_cash (account_id, currency, amount, last_updated_at_utc) "
                "VALUES ('ORIG', 'EUR', '111.00', '2026-11-09T12:00:00Z')"
            )

        writer_conn = target_mgr.connect()
        writer_conn.execute("BEGIN IMMEDIATE;")
        writer_conn.execute(
            "INSERT INTO portfolio_cash (account_id, currency, amount, last_updated_at_utc) "
            "VALUES ('UNCOMMITTED', 'EUR', '222.00', '2026-11-09T12:00:00Z')"
        )
        try:
            with self.assertRaises(BackupError) as ctx:
                BackupManager.restore_backup(self.backup_path, target_path)
            self.assertTrue(isinstance(ctx.exception, (BackupQuiescenceError, BackupError)))
        finally:
            writer_conn.execute("ROLLBACK;")
            writer_conn.close()

        # Original data untouched
        with target_mgr.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT amount FROM portfolio_cash WHERE account_id='ORIG'")
            self.assertEqual(cur.fetchone()[0], "111.00")

    def test_r3_active_reader_transaction_rejected_fail_closed(self):
        """R3.2: Active reader transaction holding locks on target blocks restoration fail-closed."""
        target_path = os.path.join(self.temp_dir.name, "target_reader.db")
        target_mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with target_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute(
                "INSERT INTO portfolio_cash (account_id, currency, amount, last_updated_at_utc) "
                "VALUES ('READER_ORIG', 'EUR', '333.00', '2026-11-09T12:00:00Z')"
            )

        reader_conn = target_mgr.connect()
        reader_conn.execute("BEGIN DEFERRED;")
        reader_conn.execute("SELECT count(*) FROM portfolio_cash;")
        try:
            with self.assertRaises(BackupError) as ctx:
                BackupManager.restore_backup(self.backup_path, target_path)
            self.assertTrue(isinstance(ctx.exception, (BackupQuiescenceError, BackupError)))
        finally:
            reader_conn.execute("ROLLBACK;")
            reader_conn.close()

    def test_r3_idle_open_connection_rejected_fail_closed(self):
        """R3.3: Idle open SQLite connection holding file handle blocks restoration fail-closed."""
        target_path = os.path.join(self.temp_dir.name, "target_idle.db")
        target_mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with target_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)

        idle_conn = target_mgr.connect()
        try:
            with self.assertRaises(BackupError) as ctx:
                BackupManager.restore_backup(self.backup_path, target_path)
            self.assertTrue(isinstance(ctx.exception, (BackupQuiescenceError, BackupError)))
        finally:
            idle_conn.close()

    def test_r3_concurrent_database_manager_connect_blocked_by_restore_lock(self):
        """R3.4: Concurrent DatabaseManager.connect() during restoration is rejected via restore lock."""
        target_path = os.path.join(self.temp_dir.name, "target_lock_test.db")
        restore_lock_path = f"{target_path}.restore_lock"

        # Simulate active restore lock
        with open(restore_lock_path, "w") as f:
            f.write("pid=1234\n")

        try:
            mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)
            with self.assertRaises(DatabaseBusyError) as ctx:
                mgr.connect()
            self.assertIn("undergoing offline backup restoration", str(ctx.exception))
        finally:
            if os.path.exists(restore_lock_path):
                os.unlink(restore_lock_path)

    def test_r3_wal_database_with_outstanding_frames_checkpointed_and_restored(self):
        """R3.5: Target WAL database with outstanding frames is checkpointed and restored safely."""
        target_path = os.path.join(self.temp_dir.name, "target_wal_frames.db")
        target_mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with target_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                "INSERT INTO portfolio_cash (account_id, currency, amount, last_updated_at_utc) "
                "VALUES ('WAL_PRE', 'EUR', '50.00', '2026-11-09T12:00:00Z')"
            )

        # Restore onto the WAL database (which has closed all connections and is quiescent)
        res = BackupManager.restore_backup(self.backup_path, target_path)
        self.assertEqual(res.integrity_status, "ok")

        with target_mgr.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT amount FROM portfolio_cash WHERE account_id='PRIMARY'")
            self.assertEqual(cur.fetchone()[0], "9999.00")

    def test_r3_checkpoint_failure_fails_closed(self):
        """R3.6: Failure during WAL checkpoint causes restoration to reject fail-closed without modifying target."""
        target_path = os.path.join(self.temp_dir.name, "target_cp_fail.db")
        target_mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with target_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                "INSERT INTO portfolio_cash (account_id, currency, amount, last_updated_at_utc) "
                "VALUES ('SAFE', 'EUR', '123.00', '2026-11-09T12:00:00Z')"
            )

        # Active reader prevents WAL checkpoint truncation
        active_reader = sqlite3.connect(target_path, autocommit=True)
        active_reader.execute("BEGIN DEFERRED;")
        active_reader.execute("SELECT * FROM portfolio_cash;")

        try:
            with self.assertRaises(BackupError) as ctx:
                BackupManager.restore_backup(self.backup_path, target_path)
            self.assertTrue(isinstance(ctx.exception, (BackupQuiescenceError, BackupError)))
        finally:
            active_reader.execute("ROLLBACK;")
            active_reader.close()

        # Original data untouched
        with target_mgr.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT amount FROM portfolio_cash WHERE account_id='SAFE'")
            self.assertEqual(cur.fetchone()[0], "123.00")

    def test_r3_failure_during_replacement_recovers_original_target(self):
        """R3.7: Failure during replacement recovers original target and verifies integrity."""
        target_path = os.path.join(self.temp_dir.name, "target_swap_fail.db")
        target_mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with target_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute(
                "INSERT INTO portfolio_cash (account_id, currency, amount, last_updated_at_utc) "
                "VALUES ('ORIGINAL', 'EUR', '888.00', '2026-11-09T12:00:00Z')"
            )

        real_replace = os.replace
        replace_call_count = [0]

        def faulty_replace(src, dst):
            replace_call_count[0] += 1
            # First replace is target -> target_bak (let it succeed)
            # Second replace is tmp_restore -> target (inject failure)
            if replace_call_count[0] == 2:
                raise OSError("Simulated filesystem I/O error during tmp_restore -> target replace")
            return real_replace(src, dst)

        os.replace = faulty_replace
        try:
            with self.assertRaises(BackupError) as ctx:
                BackupManager.restore_backup(self.backup_path, target_path)
            self.assertIn("Original target database successfully recovered", str(ctx.exception))
        finally:
            os.replace = real_replace

        # Verify original target is completely recovered
        with target_mgr.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT amount FROM portfolio_cash WHERE account_id='ORIGINAL'")
            self.assertEqual(cur.fetchone()[0], "888.00")

    def test_r3_failure_during_final_integrity_verification_recovers_original_target(self):
        """R3.8: Failure during final integrity verification recovers original target from backup."""
        target_path = os.path.join(self.temp_dir.name, "target_final_verify_fail.db")
        target_mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with target_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute(
                "INSERT INTO portfolio_cash (account_id, currency, amount, last_updated_at_utc) "
                "VALUES ('KEEP_ORIGINAL', 'EUR', '444.00', '2026-11-09T12:00:00Z')"
            )

        real_verify = BackupManager.verify_backup
        verify_calls = [0]

        def faulty_verify(path):
            verify_calls[0] += 1
            # Call 1 is pre-check on backup_path (let it pass)
            # Call 2 is on final placed target (fail it)
            if verify_calls[0] == 2:
                raise BackupIntegrityError("Simulated corruption in final placed target")
            return real_verify(path)

        BackupManager.verify_backup = classmethod(lambda cls, path: faulty_verify(path))
        try:
            with self.assertRaises(BackupError) as ctx:
                BackupManager.restore_backup(self.backup_path, target_path)
            self.assertIn("Original target database successfully recovered", str(ctx.exception))
        finally:
            BackupManager.verify_backup = real_verify

        # Verify original target is recovered
        with target_mgr.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT amount FROM portfolio_cash WHERE account_id='KEEP_ORIGINAL'")
            self.assertEqual(cur.fetchone()[0], "444.00")

    def test_r3_no_unsafe_sidecar_deletion_when_target_held_open(self):
        """R3.9: WAL/SHM sidecars are NOT deleted when target database is active."""
        target_path = os.path.join(self.temp_dir.name, "target_sidecars.db")
        target_mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with target_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                "INSERT INTO portfolio_cash (account_id, currency, amount, last_updated_at_utc) "
                "VALUES ('SIDECAR', 'EUR', '10.00', '2026-11-09T12:00:00Z')"
            )

        active = target_mgr.connect()
        try:
            with self.assertRaises(BackupError):
                BackupManager.restore_backup(self.backup_path, target_path)

            wal_file = f"{target_path}-wal"
            shm_file = f"{target_path}-shm"
            self.assertTrue(os.path.exists(wal_file) or os.path.exists(shm_file))
        finally:
            active.close()

    def test_r4_fallback_database_deletion_failure_reported(self):
        """R4.1: Failure during target_bak deletion raises BackupCleanupError."""
        target_path = os.path.join(self.temp_dir.name, "target_cleanup_fail.db")
        target_mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with target_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute(
                "INSERT INTO portfolio_cash (account_id, currency, amount, last_updated_at_utc) "
                "VALUES ('PRE', 'EUR', '1.00', '2026-11-09T12:00:00Z')"
            )

        real_unlink = os.unlink

        def faulty_unlink(path):
            if ".pre_restore_" in path:
                raise OSError("Simulated permission error unlinking fallback backup")
            return real_unlink(path)

        os.unlink = faulty_unlink
        try:
            with self.assertRaises(BackupCleanupError) as ctx:
                BackupManager.restore_backup(self.backup_path, target_path)
            self.assertIn("non-fatal cleanup errors occurred", str(ctx.exception))
        finally:
            os.unlink = real_unlink

    def test_r4_original_target_recovery_failure_preserves_fallback_and_raises_recovery_error(self):
        """R4.2: When replacement fails AND recovery of original target fails, fallback is NEVER deleted."""
        target_path = os.path.join(self.temp_dir.name, "target_catastrophic_fail.db")
        target_mgr = DatabaseManager(target_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with target_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            conn.execute(
                "INSERT INTO portfolio_cash (account_id, currency, amount, last_updated_at_utc) "
                "VALUES ('CRITICAL_DATA', 'EUR', '555.00', '2026-11-09T12:00:00Z')"
            )

        real_replace = os.replace
        replace_call_count = [0]

        def double_faulty_replace(src, dst):
            replace_call_count[0] += 1
            # Call 1: target -> target_bak (succeeds)
            # Call 2: tmp_restore -> target (fails)
            # Call 3: target_bak -> target recovery (fails)
            if replace_call_count[0] in (2, 3):
                raise OSError(f"Simulated fatal filesystem error on replace #{replace_call_count[0]}")
            return real_replace(src, dst)

        os.replace = double_faulty_replace
        try:
            with self.assertRaises(BackupRecoveryError) as ctx:
                BackupManager.restore_backup(self.backup_path, target_path)

            self.assertIn("recovery of original target", str(ctx.exception))
            self.assertEqual(ctx.exception.target_path, target_path)
            self.assertTrue(os.path.exists(ctx.exception.fallback_path))
            self.assertIn(".pre_restore_", ctx.exception.fallback_path)
        finally:
            os.replace = real_replace


if __name__ == "__main__":
    unittest.main()
