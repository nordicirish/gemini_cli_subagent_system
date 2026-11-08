"""
Comprehensive Automated Unit Tests for Isolated SQLite Core Infrastructure
Governed by Stage 1.3 Directive and Stage 1.3.1 Integrity Corrections.

Tests cover:
- D1: Connection lifecycle, WAL mode, profile-specific PRAGMAs.
- D2: Schema initialisation, versioning, primary/unique/check constraints, schema integrity checks.
- D3: Financial decimal structural constraints (Workstream A & B: zero REAL, no malformed values).
- D4: Audit event identifier integrity (Workstream B: strict 32-hex lowercase UUID).
- D5: Explicit ACID transaction commits, rollbacks, multi-row atomicity, nested transaction rejection.
- D6: SQLite Online Backup API snapshots, race-safe overwrite protection, failed backup cleanup.
- D7: Cryptographic hash-based persistence isolation verification (Workstream F).
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
    """D2: Schema design, versioning, integrity verification, and constraints."""

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

    def test_core_schema_integrity_verification_missing_table(self):
        """Verify that existing database missing an expected table fails with SchemaIntegrityError."""
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            # Drop a required table
            conn.execute("DROP TABLE portfolio_positions;")

            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.initialize_core_schema(conn)
            self.assertIn("portfolio_positions", str(ctx.exception))

    def test_core_schema_integrity_verification_missing_index(self):
        """Verify that existing database missing an expected index fails with SchemaIntegrityError."""
        with self.manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            # Drop a required index
            conn.execute("DROP INDEX idx_audit_timestamp;")

            with self.assertRaises(SchemaIntegrityError) as ctx:
                SchemaMigrator.initialize_core_schema(conn)
            self.assertIn("idx_audit_timestamp", str(ctx.exception))

    def test_cache_schema_integrity_verification(self):
        """Verify cache schema integrity fails closed if daily_bars table is dropped."""
        cache_path = os.path.join(self.temp_dir.name, "cache_integrity.db")
        cache_mgr = DatabaseManager(cache_path, profile=MARKET_DATA_CACHE_PROFILE)
        with cache_mgr.connection() as conn:
            SchemaMigrator.initialize_cache_schema(conn)
            conn.execute("DROP TABLE daily_bars;")

            with self.assertRaises(SchemaIntegrityError):
                SchemaMigrator.initialize_cache_schema(conn)

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


class TestFinancialDecimalConstraints(unittest.TestCase):
    """D3: Parameterised tests for financial numeric constraints and canonical decimal contract."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_fin_decimal.db")
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
        # Create an initial valid file
        with open(self.dst_path, "w") as f:
            f.write("original destination data")

        # Attempt backup with invalid source connection to trigger failure
        with self.assertRaises(BackupError):
            BackupManager.create_backup("non_existent_source.db", self.dst_path, overwrite=True)

        # Original destination must remain completely untouched
        with open(self.dst_path, "r") as f:
            self.assertEqual(f.read(), "original destination data")


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


if __name__ == "__main__":
    unittest.main()
