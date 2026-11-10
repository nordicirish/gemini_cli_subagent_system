"""
Automated Test Suite for Offline Legacy JSON Migration & Parity Verification.
Governed by Stage 1.5-D Architecture and Agent Governance Framework.

Covers:
A. Source validation (strict JSON parsing, binary float rejection, schema validation, non-string key rejection).
B. Migration mapping (cash, positions, watchlist, scout categories, trade lessons, decision logs, Decimal precision).
C. Atomicity and rollback (successful atomic commit, mid-import failure rollback, constraint failure rollback, integrity error).
D. Idempotency and conflicts (repeat identical import skips duplicates, conflicting record raises MigrationConflictError).
E. Parity verification (exact parity, canonical Decimal equivalence, mismatch detection, missing/unexpected record detection).
F. Revision and snapshot integrity (db_revision provenance, no projection revision drift, rollback revision immunity).
G. Production safety (production path rejection, source file immutability, zero production DB creation).
H. Real legacy files audit and dry-run (context/ssot.json, context/trade_lessons.json, config.json).
"""

from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from python.database.manager import (
    DatabaseManager,
    PRIMARY_FINANCIAL_PROFILE,
)
from python.database.schema import SchemaMigrator
from python.database.repositories.base import (
    get_db_revision,
    decimal_to_canonical_str,
    ValidationError,
    TransactionIntegrityError,
)
from python.database.repositories.unit_of_work import UnitOfWork
from python.database.projections import SSoTProjectionGenerator
from python.database.migration.models import (
    MigrationError,
    MigrationSafetyError,
    MigrationValidationError,
    MigrationConflictError,
    DiscrepancyType,
    ParityDiscrepancy,
    ParityResult,
    MigrationSource,
    MigrationPlan,
    MigrationResult,
)
from python.database.migration.contract import (
    MIGRATION_CONTRACT_VERSION,
    parse_financial_decimal,
    parse_ticker,
    parse_iso_timestamp,
    resolve_historical_timestamp,
    load_strict_json,
    load_strict_json_file,
    validate_migration_destination,
    extract_rule_code_and_directive,
)
from python.database.migration.importer import LegacyJsonImporter
from python.database.migration.parity import SSoTParityVerifier
from python.tool_registry import execution_role_context


class BaseMigrationTestCase(unittest.TestCase):
    """Base test case initializing isolated temporary directories and database managers."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.db_path = self.temp_path / "test_migration.db"
        self.db_manager = DatabaseManager(str(self.db_path), profile=PRIMARY_FINANCIAL_PROFILE)

        with self.db_manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)

        self.importer = LegacyJsonImporter()
        self.generator = SSoTProjectionGenerator(self.db_manager)
        self.verifier = SSoTParityVerifier(self.generator)
        self.default_ts = "2026-11-10T12:00:00Z"

    def tearDown(self):
        self.temp_dir.cleanup()


# ===========================================================================
# A. Source Validation Tests
# ===========================================================================

class TestSourceValidation(BaseMigrationTestCase):
    """Verifies that legacy sources are strictly validated before database access."""

    def test_valid_legacy_json_accepted(self):
        source_data = {
            "mutable_state": {
                "portfolio_snapshot": [{"ticker": "SPY", "shares": "10.0", "wac": "500.0"}],
                "watched_tickers": ["GOOG", "AMZN"],
            }
        }
        sources = self.importer.validate_sources(source_data)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].data, source_data)

    def test_malformed_json_rejected(self):
        bad_json_file = self.temp_path / "bad.json"
        bad_json_file.write_text('{"unclosed": [1, 2,', encoding="utf-8")
        with self.assertRaises(MigrationValidationError) as ctx:
            self.importer.load_source_file(bad_json_file)
        self.assertIn("not valid strict JSON", str(ctx.exception))

    def test_empty_or_unexpected_structures_rejected(self):
        list_file = self.temp_path / "list.json"
        list_file.write_text('["not", "a", "dict"]', encoding="utf-8")
        with self.assertRaises(MigrationValidationError) as ctx:
            self.importer.load_source_file(list_file)
        self.assertIn("root must be a JSON object", str(ctx.exception))

    def test_missing_required_fields_reported(self):
        bad_pos_data = {
            "portfolio_snapshot": [{"shares": "10.0"}]  # Missing ticker
        }
        with self.assertRaises(MigrationValidationError) as ctx:
            self.importer.plan_migration(bad_pos_data, default_timestamp=self.default_ts)
        self.assertIn("ticker", str(ctx.exception))

    def test_unknown_fields_handled_according_to_contract(self):
        data_with_external = {
            "state_context": {"regime": "NORMAL"},
            "runtime_flags": {"dry_run": False},
            "unrelated_meta": "custom_value",
            "portfolio_snapshot": [{"ticker": "AAPL", "shares": "5.0", "wac": "150.0"}],
        }
        plan = self.importer.plan_migration(data_with_external, default_timestamp=self.default_ts)
        self.assertIn("state_context", plan.external_sections)
        self.assertIn("runtime_flags", plan.external_sections)
        self.assertEqual(len(plan.position_records), 1)

    def test_duplicate_identifiers_detected(self):
        # Conflicting duplicate position records must raise MigrationConflictError (D-04)
        dup_data = {
            "portfolio_snapshot": [
                {"ticker": "SPY", "shares": "10.0", "wac": "500.0"},
                {"ticker": "SPY", "shares": "20.0", "wac": "510.0"},
            ]
        }
        with self.assertRaises(MigrationConflictError):
            self.importer.plan_migration(dup_data, default_timestamp=self.default_ts)

    def test_exact_duplicate_records_deduplicated(self):
        # Exact duplicate position records must be deduplicated safely (D-04)
        exact_dup_data = {
            "portfolio_snapshot": [
                {"ticker": "SPY", "shares": "10.0", "wac": "500.0"},
                {"ticker": "SPY", "shares": "10.0", "wac": "500.0"},
            ]
        }
        plan = self.importer.plan_migration(exact_dup_data, default_timestamp=self.default_ts)
        self.assertEqual(len(plan.position_records), 1)
        self.assertEqual(len(plan.discrepancies), 1)
        self.assertEqual(plan.discrepancies[0]["type"], "EXACT_DUPLICATE_DEDUPLICATED")

    def test_invalid_decimal_values_rejected(self):
        with self.assertRaises(MigrationValidationError):
            parse_financial_decimal("not-a-number", "test_field")
        with self.assertRaises(MigrationValidationError):
            parse_financial_decimal("12.34567", "test_field", max_scale=4)

    def test_binary_floats_rejected_for_financial_fields(self):
        with self.assertRaises(MigrationValidationError) as ctx:
            parse_financial_decimal(12.34, "test_float")
        self.assertIn("binary floating-point input is prohibited", str(ctx.exception))

    def test_invalid_timestamps_rejected(self):
        with self.assertRaises(MigrationValidationError):
            parse_iso_timestamp("invalid-date", "test_ts")
        with self.assertRaises(MigrationValidationError):
            parse_iso_timestamp("2026-09-22 15:00:00", "test_ts")  # Missing T and Z

    def test_non_string_dictionary_keys_rejected(self):
        bad_keys_data = {
            1: "int_key",
            "valid": "value",
        }
        with self.assertRaises(MigrationValidationError) as ctx:
            self.importer.validate_sources(bad_keys_data)
        self.assertIn("Dictionary keys must be strings", str(ctx.exception))


# ===========================================================================
# B. Migration Mapping Tests
# ===========================================================================

class TestMigrationMapping(BaseMigrationTestCase):
    """Verifies that legacy data maps accurately into SQLite core tables."""

    def test_cash_records_imported_correctly(self):
        source = {
            "portfolio_cash": [
                {"account_id": "PRIMARY", "currency": "EUR", "amount": "2359.80", "last_updated_at_utc": self.default_ts},
                {"account_id": "PRIMARY", "currency": "USD", "amount": "2548.58", "last_updated_at_utc": self.default_ts},
            ]
        }
        res = self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)
        self.assertTrue(res.success)
        self.assertEqual(res.imported_counts["portfolio_cash"], 2)

        with self.db_manager.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT currency, amount FROM portfolio_cash ORDER BY currency;")
            rows = cur.fetchall()
            self.assertEqual(rows, [("EUR", "2359.80"), ("USD", "2548.58")])

    def test_position_records_imported_correctly(self):
        source = {
            "mutable_state": {
                "portfolio_snapshot": [
                    {"ticker": "SPY", "shares": "10.0000", "wac": "500.0000", "wac_usd": "540.0000"}
                ]
            }
        }
        res = self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)
        self.assertTrue(res.success)
        self.assertEqual(res.imported_counts["portfolio_positions"], 1)

        with self.db_manager.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT ticker, shares, wac_eur, wac_usd FROM portfolio_positions;")
            row = cur.fetchone()
            self.assertEqual(row, ("SPY", "10.0000", "500.0000", "540.0000"))

    def test_watchlist_records_imported_correctly(self):
        source = {
            "WATCHLIST": ["GOOG", "AMZN", "MSFT"]
        }
        res = self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)
        self.assertTrue(res.success)
        self.assertEqual(res.imported_counts["watchlist"], 3)

        with self.db_manager.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT ticker, status FROM watchlist ORDER BY ticker;")
            rows = cur.fetchall()
            self.assertEqual(rows, [("AMZN", "ACTIVE"), ("GOOG", "ACTIVE"), ("MSFT", "ACTIVE")])

    def test_scout_categories_imported_correctly(self):
        source = {
            "SCOUT_TICKER_MAP": {
                "Aerospace": ["RKLB", "LMT"],
                "AI": ["NVDA", "PLTR"],
            }
        }
        res = self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)
        self.assertTrue(res.success)
        self.assertEqual(res.imported_counts["scout_categories"], 2)

        with self.db_manager.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT category_name, tickers_json FROM scout_categories ORDER BY category_name;")
            rows = cur.fetchall()
            self.assertEqual(rows[0][0], "AI")
            self.assertEqual(json.loads(rows[0][1]), ["NVDA", "PLTR"])
            self.assertEqual(rows[1][0], "Aerospace")
            self.assertEqual(json.loads(rows[1][1]), ["LMT", "RKLB"])

    def test_trade_lessons_imported_correctly(self):
        source = {
            "trade_lessons": [
                {
                    "rule_code": "L-219",
                    "directive_text": "Pre-market gap-down conviction threshold",
                    "category": "MANDATE",
                    "status": "ACTIVE",
                    "created_at_utc": self.default_ts,
                    "last_reviewed_at_utc": self.default_ts,
                }
            ]
        }
        res = self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)
        self.assertTrue(res.success)
        self.assertEqual(res.imported_counts["trade_lessons"], 1)

        with self.db_manager.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT rule_code, category FROM trade_lessons;")
            self.assertEqual(cur.fetchone(), ("L-219", "MANDATE"))

    def test_decision_log_records_imported_correctly(self):
        source = {
            "decision_log": [
                {
                    "session_id": "SESSION_001",
                    "action_proposed": "HOLD",
                    "decision_outcome": "APPROVED",
                    "ticker": "SPY",
                    "votes": {"BULLISH": "HOLD"},
                    "transcript_text": "Hold decision unanimous",
                    "timestamp_utc": self.default_ts,
                }
            ]
        }
        res = self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)
        self.assertTrue(res.success)
        self.assertEqual(res.imported_counts["decision_log"], 1)

        with self.db_manager.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT session_id, action_proposed, decision_outcome FROM decision_log;")
            self.assertEqual(cur.fetchone(), ("SESSION_001", "HOLD", "APPROVED"))

    def test_unsupported_or_externally_owned_fields_not_imported(self):
        source = {
            "state_context": {"regime": "NORMAL"},
            "runtime_flags": {"circuit_breaker": "ACTIVE"},
            "portfolio_snapshot": [{"ticker": "SPY", "shares": "10.0", "wac": "500.0"}],
        }
        res = self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)
        self.assertTrue(res.success)

        # Confirm external fields are not tables in SQLite
        with self.db_manager.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('state_context', 'runtime_flags');")
            self.assertEqual(cur.fetchall(), [])

    def test_identifiers_and_timestamps_preserved(self):
        source = {
            "portfolio_cash": [
                {"account_id": "SECONDARY", "currency": "USD", "amount": "123.45", "last_updated_at_utc": "2026-08-15T09:30:00Z"}
            ]
        }
        res = self.importer.import_into(source, self.db_manager)
        self.assertTrue(res.success)

        with self.db_manager.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT account_id, last_updated_at_utc FROM portfolio_cash;")
            row = cur.fetchone()
            self.assertEqual(row, ("SECONDARY", "2026-08-15T09:30:00Z"))

    def test_exact_decimal_values_survive_import(self):
        source = {
            "portfolio_positions": [
                {"account_id": "PRIMARY", "ticker": "SPY", "shares": "123.4567", "wac_eur": "987.6543", "last_updated_at_utc": self.default_ts}
            ]
        }
        res = self.importer.import_into(source, self.db_manager)
        self.assertTrue(res.success)

        with self.db_manager.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT shares, wac_eur FROM portfolio_positions;")
            self.assertEqual(cur.fetchone(), ("123.4567", "987.6543"))


# ===========================================================================
# C. Atomicity and Rollback Tests
# ===========================================================================

class TestAtomicityAndRollback(BaseMigrationTestCase):
    """Verifies that migration import operations are atomic fail-closed."""

    def test_successful_import_commits_all_expected_records(self):
        source = {
            "unallocated_cash_eur": "1000.00",
            "portfolio_snapshot": [{"ticker": "SPY", "shares": "10.0", "wac": "500.0"}],
            "WATCHLIST": ["GOOG"],
        }
        res = self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)
        self.assertTrue(res.success)

        with self.db_manager.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM portfolio_cash;")
            self.assertEqual(cur.fetchone()[0], 1)
            cur.execute("SELECT count(*) FROM portfolio_positions;")
            self.assertEqual(cur.fetchone()[0], 1)
            cur.execute("SELECT count(*) FROM watchlist;")
            self.assertEqual(cur.fetchone()[0], 1)

    def test_failure_midway_rolls_back_all_imported_records(self):
        # Position 2 has invalid scale (5 decimal places), causing ValidationError midway
        source = {
            "unallocated_cash_eur": "1000.00",
            "portfolio_snapshot": [
                {"ticker": "SPY", "shares": "10.0", "wac": "500.0"},
                {"ticker": "AAPL", "shares": "10.12345", "wac": "150.0"},  # Invalid scale > 4
            ],
        }
        with self.assertRaises(MigrationValidationError):
            self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)

        # Database must have zero cash and zero positions
        with self.db_manager.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM portfolio_cash;")
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("SELECT count(*) FROM portfolio_positions;")
            self.assertEqual(cur.fetchone()[0], 0)

    def test_constraint_violations_roll_back_entire_operation(self):
        # Trade lesson with invalid category violating CHECK constraint
        source = {
            "unallocated_cash_eur": "500.00",
            "trade_lessons": [
                {
                    "rule_code": "L-999",
                    "directive_text": "Invalid category test",
                    "category": "NON_EXISTENT_CATEGORY",  # Fails CHECK constraint
                    "status": "ACTIVE",
                    "created_at_utc": self.default_ts,
                    "last_reviewed_at_utc": self.default_ts,
                }
            ],
        }
        with self.assertRaises(ValidationError):
            self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)

        with self.db_manager.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM portfolio_cash;")
            self.assertEqual(cur.fetchone()[0], 0, "Cash must be rolled back on trade lesson failure")

    def test_cleanup_failure_surfaced(self):
        source = {
            "unallocated_cash_eur": "1000.00",
        }
        # Injected failure during mutation and injected failure in rollback
        with patch("python.database.repositories.financial.CashRepository.set_cash_balance", side_effect=RuntimeError("Mutation failed")):
            with patch.object(UnitOfWork, "rollback", side_effect=TransactionIntegrityError("Injected rollback failure")):
                with self.assertRaises(TransactionIntegrityError):
                    self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)

    def test_original_exceptions_not_silently_lost(self):
        source = {
            "portfolio_snapshot": [{"ticker": "SPY", "shares": "not_a_number", "wac": "500.0"}],
        }
        with self.assertRaises(MigrationValidationError) as ctx:
            self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)
        self.assertIn("invalid Decimal representation", str(ctx.exception))


# ===========================================================================
# D. Idempotency and Conflicts Tests
# ===========================================================================

class TestIdempotencyAndConflicts(BaseMigrationTestCase):
    """Verifies safe repeatability and conflict detection."""

    def test_repeated_identical_import_does_not_duplicate_records(self):
        source = {
            "unallocated_cash_eur": "1000.00",
            "portfolio_snapshot": [{"ticker": "SPY", "shares": "10.0", "wac": "500.0"}],
            "WATCHLIST": ["GOOG"],
        }
        # First import
        res1 = self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)
        self.assertTrue(res1.success)
        self.assertEqual(res1.imported_counts["portfolio_cash"], 1)
        self.assertEqual(res1.imported_counts["portfolio_positions"], 1)

        # Second identical import
        res2 = self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)
        self.assertTrue(res2.success)
        self.assertEqual(res2.imported_counts["portfolio_cash"], 0)
        self.assertEqual(res2.skipped_counts["portfolio_cash"], 1)
        self.assertEqual(res2.imported_counts["portfolio_positions"], 0)
        self.assertEqual(res2.skipped_counts["portfolio_positions"], 1)

        with self.db_manager.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM portfolio_cash;")
            self.assertEqual(cur.fetchone()[0], 1)
            cur.execute("SELECT count(*) FROM portfolio_positions;")
            self.assertEqual(cur.fetchone()[0], 1)

    def test_conflicting_existing_records_detected(self):
        source1 = {
            "unallocated_cash_eur": "1000.00",
        }
        self.importer.import_into(source1, self.db_manager, default_timestamp=self.default_ts)

        # Conflicting cash amount for PRIMARY/EUR
        source2 = {
            "unallocated_cash_eur": "2000.00",
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            self.importer.import_into(source2, self.db_manager, default_timestamp=self.default_ts)
        self.assertIn("Cash balance conflict", str(ctx.exception))

    def test_distinct_records_not_incorrectly_merged(self):
        source1 = {
            "portfolio_snapshot": [{"ticker": "SPY", "shares": "10.0", "wac": "500.0"}]
        }
        self.importer.import_into(source1, self.db_manager, default_timestamp=self.default_ts)

        source2 = {
            "portfolio_snapshot": [{"ticker": "AAPL", "shares": "50.0", "wac": "150.0"}]
        }
        self.importer.import_into(source2, self.db_manager, default_timestamp=self.default_ts)

        with self.db_manager.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT ticker FROM portfolio_positions ORDER BY ticker;")
            rows = [r[0] for r in cur.fetchall()]
            self.assertEqual(rows, ["AAPL", "SPY"])

    def test_no_destructive_replacement_occurs(self):
        source1 = {
            "portfolio_snapshot": [{"ticker": "SPY", "shares": "10.0", "wac": "500.0"}]
        }
        self.importer.import_into(source1, self.db_manager, default_timestamp=self.default_ts)

        # Attempt to import conflicting position
        source2 = {
            "portfolio_snapshot": [{"ticker": "SPY", "shares": "99.0", "wac": "600.0"}]
        }
        with self.assertRaises(MigrationConflictError):
            self.importer.import_into(source2, self.db_manager, default_timestamp=self.default_ts)

        # Verify original record remained intact (no overwrite)
        with self.db_manager.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT shares, wac_eur FROM portfolio_positions WHERE ticker='SPY';")
            self.assertEqual(cur.fetchone(), ("10.0", "500.0"))


# ===========================================================================
# E. Parity Verification Tests
# ===========================================================================

class TestParityVerification(BaseMigrationTestCase):
    """Verifies semantic parity comparisons using SSoTProjectionGenerator."""

    def test_exact_parity_produces_zero_discrepancies(self):
        source = {
            "portfolio_cash": [{"account_id": "PRIMARY", "currency": "EUR", "amount": "1000.00", "last_updated_at_utc": self.default_ts}],
            "unallocated_cash_eur": "1000.00",
            "portfolio_snapshot": [{"account_id": "PRIMARY", "ticker": "SPY", "shares": "10.0000", "wac": "500.0000", "last_updated_at_utc": self.default_ts}],
            "watched_tickers": ["GOOG"],
        }
        self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)

        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn)

        res = self.verifier.compare(source, proj)
        self.assertTrue(res.is_parity)
        self.assertEqual(res.summary.missing_records, 0)
        self.assertEqual(res.summary.financial_mismatches, 0)
        self.assertEqual(res.discrepancies, [])

    def test_json_formatting_differences_do_not_create_false_discrepancies(self):
        source = {
            "unallocated_cash_eur": "1000.00",
            "watched_tickers": ["GOOG", "AMZN"],  # un-sorted in source
        }
        self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)

        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn)

        res = self.verifier.compare(source, proj)
        self.assertTrue(res.is_parity)

    def test_canonical_decimal_equivalence_recognized(self):
        source = {
            "unallocated_cash_eur": "1000.0",
            "portfolio_snapshot": [{"ticker": "SPY", "shares": "10", "wac": "500.0"}],
        }
        self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)

        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn)

        differing_representation = {
            "unallocated_cash_eur": "1000.00",
            "portfolio_snapshot": [{"ticker": "SPY", "shares": "10.0000", "wac": "500.0000"}],
        }
        res = self.verifier.compare(differing_representation, proj)
        # Should count as equivalent_values without failing parity
        self.assertTrue(res.is_parity)
        self.assertGreater(res.summary.equivalent_values, 0)
        self.assertEqual(res.summary.financial_mismatches, 0)

    def test_financial_precision_mismatch_detected(self):
        source = {
            "unallocated_cash_eur": "1000.00",
        }
        self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)

        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn)

        # Mutate legacy comparison to different amount
        differing_legacy = {"unallocated_cash_eur": "1005.00"}
        res = self.verifier.compare(differing_legacy, proj)
        self.assertFalse(res.is_parity)
        self.assertEqual(res.summary.financial_mismatches, 1)
        self.assertEqual(res.discrepancies[0].discrepancy_type, DiscrepancyType.FINANCIAL_MISMATCH.value)

    def test_missing_sqlite_owned_records_detected(self):
        source = {
            "portfolio_snapshot": [{"ticker": "SPY", "shares": "10.0", "wac": "500.0"}],
        }
        self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)

        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn)

        # Compare against legacy source that expected an additional position (AAPL)
        legacy_with_extra = {
            "portfolio_snapshot": [
                {"ticker": "SPY", "shares": "10.0", "wac": "500.0"},
                {"ticker": "AAPL", "shares": "50.0", "wac": "150.0"},
            ]
        }
        res = self.verifier.compare(legacy_with_extra, proj)
        self.assertFalse(res.is_parity)
        self.assertEqual(res.summary.missing_records, 1)
        self.assertIn("AAPL", res.discrepancies[0].path)

    def test_unexpected_projected_records_detected(self):
        source = {
            "portfolio_snapshot": [
                {"ticker": "SPY", "shares": "10.0", "wac": "500.0"},
                {"ticker": "AAPL", "shares": "50.0", "wac": "150.0"},
            ]
        }
        self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)

        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn)

        # Compare against legacy that only knew about SPY
        legacy_only_spy = {
            "portfolio_snapshot": [{"ticker": "SPY", "shares": "10.0", "wac": "500.0"}]
        }
        res = self.verifier.compare(legacy_only_spy, proj)
        self.assertFalse(res.is_parity)
        self.assertEqual(res.summary.unexpected_records, 1)
        self.assertEqual(res.discrepancies[0].discrepancy_type, DiscrepancyType.UNEXPECTED_RECORD.value)

    def test_externally_managed_sections_preserved(self):
        base_doc = {
            "state_context": {"regime": "NORMAL"},
            "runtime_flags": {"circuit_breaker": "PASSIVE"},
        }
        source = {
            "unallocated_cash_eur": "1000.00",
        }
        self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)

        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn, base_document=base_doc)

        res = self.verifier.compare(source, proj, base_document=base_doc)
        self.assertTrue(res.is_parity)
        self.assertGreater(res.summary.expected_external_sections, 0)
        self.assertEqual(proj["state_context"], {"regime": "NORMAL"})

    def test_results_are_deterministic_across_repeated_runs(self):
        source = {
            "unallocated_cash_eur": "1000.00",
            "portfolio_snapshot": [
                {"ticker": "SPY", "shares": "10.0", "wac": "500.0"},
                {"ticker": "AAPL", "shares": "50.0", "wac": "150.0"},
            ],
            "WATCHLIST": ["GOOG", "AMZN"],
        }
        self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)

        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn)

        res1 = self.verifier.compare(source, proj)
        res2 = self.verifier.compare(source, proj)

        self.assertEqual(res1.to_dict(), res2.to_dict())


# ===========================================================================
# F. Revision and Snapshot Integrity Tests
# ===========================================================================

class TestRevisionAndSnapshotIntegrity(BaseMigrationTestCase):
    """Verifies monotonic revision tracking and snapshot read invariants."""

    def test_projection_revision_matches_imported_database_state(self):
        source = {
            "unallocated_cash_eur": "1000.00",
            "portfolio_snapshot": [{"ticker": "SPY", "shares": "10.0", "wac": "500.0"}],
        }
        res = self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)

        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn)

        self.assertEqual(proj["_projection_metadata"]["db_revision"], res.database_revision)
        self.assertGreater(res.database_revision, 0)

    def test_projection_does_not_mutate_revision(self):
        source = {
            "unallocated_cash_eur": "500.00",
        }
        self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)

        with self.db_manager.connection() as conn:
            rev_start = get_db_revision(conn)
            self.generator.generate(conn)
            self.generator.generate(conn)
            rev_end = get_db_revision(conn)
            self.assertEqual(rev_start, rev_end)

    def test_failed_import_does_not_leave_revision_increments(self):
        with self.db_manager.connection() as conn:
            rev_initial = get_db_revision(conn)

        bad_source = {
            "unallocated_cash_eur": "100.00",
            "portfolio_snapshot": [{"ticker": "SPY", "shares": "invalid", "wac": "1.0"}],
        }
        with self.assertRaises(MigrationValidationError):
            self.importer.import_into(bad_source, self.db_manager, default_timestamp=self.default_ts)

        with self.db_manager.connection() as conn:
            rev_after = get_db_revision(conn)
            self.assertEqual(rev_initial, rev_after, "Failed import must not increment revision")


# ===========================================================================
# G. Production Safety Tests
# ===========================================================================

class TestProductionSafety(BaseMigrationTestCase):
    """Verifies that protected production paths are strictly rejected."""

    def test_production_paths_rejected_as_destinations(self):
        repo_root = self.importer.repository_root
        prod_gem = repo_root / "context" / "gem.db"

        with self.assertRaises(MigrationSafetyError) as ctx:
            validate_migration_destination(prod_gem, repo_root)
        self.assertIn("protected production database", str(ctx.exception))

        with self.assertRaises(MigrationSafetyError):
            self.importer.import_into({"unallocated_cash_eur": "1.00"}, destination=prod_gem)

    def test_cache_market_data_db_rejected_as_destination(self):
        repo_root = self.importer.repository_root
        cache_db = repo_root / "cache" / "market_data.db"

        with self.assertRaises(MigrationSafetyError) as ctx:
            validate_migration_destination(cache_db, repo_root)
        self.assertIn("protected production database", str(ctx.exception))

    def test_source_files_remain_byte_for_byte_unchanged(self):
        # Create dummy source file, record hash, run import, verify hash invariant
        test_file = self.temp_path / "source.json"
        content = b'{\n  "mutable_state": {\n    "watched_tickers": ["AAPL"]\n  }\n}'
        test_file.write_bytes(content)
        hash_before = hashlib.sha256(content).hexdigest()

        self.importer.import_into(test_file, self.db_manager, default_timestamp=self.default_ts)

        hash_after = hashlib.sha256(test_file.read_bytes()).hexdigest()
        self.assertEqual(hash_before, hash_after, "Source file must remain byte-for-byte unchanged")

    def test_production_databases_remain_absent(self):
        repo_root = self.importer.repository_root
        self.assertFalse((repo_root / "context" / "gem.db").exists())
        self.assertFalse((repo_root / "cache" / "market_data.db").exists())


# ===========================================================================
# H. Real Legacy Files Audit and Dry-Run Tests
# ===========================================================================

class TestRealLegacyFilesAuditAndDryRun(BaseMigrationTestCase):
    """Executes offline dry-run imports and audits using actual repository legacy files."""

    def test_actual_context_ssot_json_import_and_parity(self):
        """Audits actual context/ssot.json file import into disposable database and verifies parity."""
        ssot_path = self.importer.repository_root / "context" / "ssot.json"
        self.assertTrue(ssot_path.exists(), "context/ssot.json must exist in repo")

        initial_bytes = ssot_path.read_bytes()
        initial_hash = hashlib.sha256(initial_bytes).hexdigest()

        # Execute dry-run import
        res = self.importer.import_into(ssot_path, self.db_manager, default_timestamp=self.default_ts)
        self.assertTrue(res.success)
        self.assertEqual(res.imported_counts["portfolio_positions"], 1)
        self.assertEqual(res.imported_counts["watchlist"], 2)

        # Confirm source file was untouched
        self.assertEqual(hashlib.sha256(ssot_path.read_bytes()).hexdigest(), initial_hash)

        # Generate projection from imported database
        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn)

        # Verify parity against ssot.json
        source_loaded = self.importer.load_source_file(ssot_path)
        parity = self.verifier.compare(source_loaded, proj)
        self.assertTrue(parity.is_parity)
        self.assertEqual(parity.summary.missing_records, 0)
        self.assertEqual(parity.summary.financial_mismatches, 0)
        # SPY shares: 10 in ssot.json vs "10.0000" in projection -> equivalent_values
        self.assertGreater(parity.summary.equivalent_values, 0)

    def test_actual_context_trade_lessons_analysis(self):
        """Audits actual context/trade_lessons.json legacy structure and extracts codified rules."""
        tl_path = self.importer.repository_root / "context" / "trade_lessons.json"
        self.assertTrue(tl_path.exists())

        loaded = self.importer.load_source_file(tl_path)
        plan = self.importer.plan_migration(loaded, default_timestamp=self.default_ts)

        # 22 lessons start with L-xxx; 2 lessons (items 16 and 17) lack L- prefix and are flagged as discrepancies
        self.assertEqual(len(plan.trade_lessons), 22)
        self.assertEqual(len(plan.discrepancies), 2)
        uncodified_reasons = [d["reason"] for d in plan.discrepancies]
        self.assertEqual(uncodified_reasons, ["UNCODIFIED_RULE_NO_L_PREFIX", "UNCODIFIED_RULE_NO_L_PREFIX"])

    def test_actual_config_json_scout_categories_import(self):
        """Audits actual config.json SCOUT_TICKER_MAP import into disposable database."""
        cfg_path = self.importer.repository_root / "config.json"
        self.assertTrue(cfg_path.exists())

        loaded = self.importer.load_source_file(cfg_path)
        res = self.importer.import_into(loaded, self.db_manager, default_timestamp=self.default_ts)
        self.assertTrue(res.success)
        # 15 scout categories in config.json
        self.assertEqual(res.imported_counts["scout_categories"], 15)

        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn)

        parity = self.verifier.compare(loaded, proj)
        self.assertEqual(parity.summary.missing_records, 0)

    def test_actual_context_decision_log_import_and_parity(self):
        """Audits actual context/decision_log.json import into disposable database and verifies parity (D-06)."""
        dl_path = self.importer.repository_root / "context" / "decision_log.json"
        self.assertTrue(dl_path.exists())

        loaded = self.importer.load_source_file(dl_path)
        res = self.importer.import_into(loaded, self.db_manager, default_timestamp=self.default_ts)
        self.assertTrue(res.success)
        # 1 date-based record + 5 deliberation sub-decisions = 6 imported
        self.assertEqual(res.imported_counts["decision_log"], 6)

        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn)

        parity = self.verifier.compare(loaded, proj)
        self.assertTrue(parity.is_parity)
        self.assertEqual(parity.summary.missing_records, 0)
        self.assertEqual(parity.summary.financial_mismatches, 0)
        self.assertEqual(parity.summary.exact_matches, 6)


# ===========================================================================
# I. Stage 1.5-D.1 Corrective Regressions Tests (D-01 through D-07)
# ===========================================================================

class TestStage15D1CorrectiveRegressions(BaseMigrationTestCase):
    """
    Exhaustive regression test suite verifying fixes for independent review findings D-01 to D-07.
    """

    # -----------------------------------------------------------------------
    # D-01: Timestamp Provenance
    # -----------------------------------------------------------------------

    def test_missing_historical_timestamp_is_not_fabricated(self):
        """Verifies that missing timestamps are not implicitly fabricated with current time (D-01)."""
        source = {
            "portfolio_cash": [
                {"account_id": "PRIMARY", "currency": "EUR", "amount": "100.00"}
            ]
        }
        with self.assertRaises(MigrationValidationError) as ctx:
            self.importer.import_into(source, self.db_manager, default_timestamp=None)
        self.assertIn("historical timestamp is missing", str(ctx.exception))

    def test_scalar_cash_missing_timestamp_fails_closed(self):
        """Verifies scalar cash without caller default timestamp fails closed (D-01)."""
        source = {"unallocated_cash_eur": "500.00"}
        with self.assertRaises(MigrationValidationError) as ctx:
            self.importer.import_into(source, self.db_manager, default_timestamp=None)
        self.assertIn("historical timestamp is missing", str(ctx.exception))

    def test_caller_supplied_default_timestamp_is_strictly_honored(self):
        """Verifies explicit caller supplied default timestamp is used when source lacks one (D-01)."""
        source = {
            "portfolio_cash": [
                {"account_id": "PRIMARY", "currency": "EUR", "amount": "100.00"}
            ]
        }
        res = self.importer.import_into(source, self.db_manager, default_timestamp="2026-01-01T00:00:00Z")
        self.assertTrue(res.success)
        with self.db_manager.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT last_updated_at_utc FROM portfolio_cash WHERE currency = 'EUR';")
            self.assertEqual(cur.fetchone()[0], "2026-01-01T00:00:00Z")

    # -----------------------------------------------------------------------
    # D-02: Complete Field Parity Verification
    # -----------------------------------------------------------------------

    def test_trade_lesson_directive_mutation_detected_by_parity(self):
        """Verifies that mutating trade lesson directive in DB breaks parity (D-02)."""
        source = {
            "trade_lessons": [
                {
                    "rule_code": "L-101",
                    "directive_text": "Original directive",
                    "category": "MANDATE",
                    "status": "ACTIVE",
                    "created_at_utc": self.default_ts,
                    "last_reviewed_at_utc": self.default_ts,
                }
            ]
        }
        self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)

        with self.db_manager.connection() as conn:
            conn.execute("UPDATE trade_lessons SET directive_text = 'Mutated directive' WHERE rule_code = 'L-101';")
            conn.commit()
            proj = self.generator.generate(conn)

        parity = self.verifier.compare(source, proj)
        self.assertFalse(parity.is_parity)
        self.assertEqual(parity.summary.unresolved, 1)
        self.assertTrue(any(d.path == "trade_lessons[L-101].directive_text" for d in parity.discrepancies))

    def test_trade_lesson_category_mutation_detected_by_parity(self):
        """Verifies that mutating trade lesson category in DB breaks parity (D-02)."""
        source = {
            "trade_lessons": [
                {
                    "rule_code": "L-102",
                    "directive_text": "Test directive",
                    "category": "MANDATE",
                    "status": "ACTIVE",
                    "created_at_utc": self.default_ts,
                    "last_reviewed_at_utc": self.default_ts,
                }
            ]
        }
        self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)

        with self.db_manager.connection() as conn:
            conn.execute("UPDATE trade_lessons SET category = 'EXECUTION' WHERE rule_code = 'L-102';")
            conn.commit()
            proj = self.generator.generate(conn)

        parity = self.verifier.compare(source, proj)
        self.assertFalse(parity.is_parity)
        self.assertEqual(parity.summary.unresolved, 1)
        self.assertTrue(any(d.path == "trade_lessons[L-102].category" for d in parity.discrepancies))

    def test_position_wac_usd_mutation_detected_by_parity(self):
        """Verifies that mutating position wac_usd in DB breaks parity with FINANCIAL_MISMATCH (D-02)."""
        source = {
            "portfolio_positions": [
                {
                    "account_id": "PRIMARY",
                    "ticker": "SPY",
                    "shares": "10.0000",
                    "wac_eur": "500.0000",
                    "wac_usd": "540.0000",
                    "last_updated_at_utc": self.default_ts,
                }
            ]
        }
        self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)

        with self.db_manager.connection() as conn:
            conn.execute("UPDATE portfolio_positions SET wac_usd = '555.0000' WHERE ticker = 'SPY';")
            conn.commit()
            proj = self.generator.generate(conn)

        parity = self.verifier.compare(source, proj)
        self.assertFalse(parity.is_parity)
        self.assertEqual(parity.summary.financial_mismatches, 1)
        self.assertTrue(any(d.path == "portfolio_snapshot[SPY].wac_usd" for d in parity.discrepancies))

    def test_decision_log_votes_mutation_detected_by_parity(self):
        """Verifies that mutating decision log votes in DB breaks parity (D-02)."""
        source = {
            "decision_log": [
                {
                    "session_id": "SESSION-TEST-01",
                    "action_proposed": "BUY",
                    "decision_outcome": "APPROVED",
                    "ticker": "AAPL",
                    "votes": {"bullish": "YES", "red_team": "NO"},
                    "transcript_text": "Debate transcript",
                    "timestamp_utc": self.default_ts,
                }
            ]
        }
        self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)

        with self.db_manager.connection() as conn:
            conn.execute("UPDATE decision_log SET votes_json = '{\"bullish\": \"MUTATED\"}' WHERE session_id = 'SESSION-TEST-01';")
            conn.commit()
            proj = self.generator.generate(conn)

        parity = self.verifier.compare(source, proj)
        self.assertFalse(parity.is_parity)
        self.assertEqual(parity.summary.unresolved, 1)
        self.assertTrue(any("votes" in d.path for d in parity.discrepancies))

    def test_decision_log_transcript_mutation_detected_by_parity(self):
        """Verifies that mutating decision log transcript in DB breaks parity (D-02)."""
        source = {
            "decision_log": [
                {
                    "session_id": "SESSION-TEST-02",
                    "action_proposed": "HOLD",
                    "decision_outcome": "APPROVED",
                    "ticker": "MSFT",
                    "votes": {},
                    "transcript_text": "Original transcript",
                    "timestamp_utc": self.default_ts,
                }
            ]
        }
        self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)

        with self.db_manager.connection() as conn:
            conn.execute("UPDATE decision_log SET transcript_text = 'Tampered text' WHERE session_id = 'SESSION-TEST-02';")
            conn.commit()
            proj = self.generator.generate(conn)

        parity = self.verifier.compare(source, proj)
        self.assertFalse(parity.is_parity)
        self.assertEqual(parity.summary.unresolved, 1)
        self.assertTrue(any("transcript_text" in d.path for d in parity.discrepancies))

    # -----------------------------------------------------------------------
    # D-03: Field-Aware Idempotency Verification
    # -----------------------------------------------------------------------

    def test_existing_cash_with_different_timestamp_raises_conflict(self):
        """Verifies cash with conflicting timestamp raises MigrationConflictError (D-03)."""
        source1 = {
            "portfolio_cash": [
                {"account_id": "PRIMARY", "currency": "EUR", "amount": "100.00", "last_updated_at_utc": "2026-01-01T00:00:00Z"}
            ]
        }
        self.importer.import_into(source1, self.db_manager)

        source2 = {
            "portfolio_cash": [
                {"account_id": "PRIMARY", "currency": "EUR", "amount": "100.00", "last_updated_at_utc": "2026-02-01T00:00:00Z"}
            ]
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            self.importer.import_into(source2, self.db_manager)
        self.assertIn("Cash balance conflict", str(ctx.exception))

    def test_existing_position_with_different_wac_usd_raises_conflict(self):
        """Verifies position with conflicting wac_usd raises MigrationConflictError (D-03)."""
        source1 = {
            "portfolio_positions": [
                {"account_id": "PRIMARY", "ticker": "SPY", "shares": "10.0", "wac_eur": "500.0", "wac_usd": "540.0", "last_updated_at_utc": self.default_ts}
            ]
        }
        self.importer.import_into(source1, self.db_manager)

        source2 = {
            "portfolio_positions": [
                {"account_id": "PRIMARY", "ticker": "SPY", "shares": "10.0", "wac_eur": "500.0", "wac_usd": "550.0", "last_updated_at_utc": self.default_ts}
            ]
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            self.importer.import_into(source2, self.db_manager)
        self.assertIn("Position conflict", str(ctx.exception))

    def test_existing_watchlist_with_different_notes_raises_conflict(self):
        """Verifies watchlist with conflicting notes raises MigrationConflictError (D-03)."""
        source1 = {
            "watchlist": [
                {"ticker": "NVDA", "status": "ACTIVE", "notes": "Initial note", "added_at_utc": self.default_ts}
            ]
        }
        self.importer.import_into(source1, self.db_manager)

        source2 = {
            "watchlist": [
                {"ticker": "NVDA", "status": "ACTIVE", "notes": "Altered note", "added_at_utc": self.default_ts}
            ]
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            self.importer.import_into(source2, self.db_manager)
        self.assertIn("Watchlist conflict", str(ctx.exception))

    def test_existing_trade_lesson_with_different_directive_raises_conflict(self):
        """Verifies trade lesson with conflicting directive raises MigrationConflictError (D-03)."""
        source1 = {
            "trade_lessons": [
                {"rule_code": "L-201", "directive_text": "Original text", "category": "MANDATE", "status": "ACTIVE", "created_at_utc": self.default_ts, "last_reviewed_at_utc": self.default_ts}
            ]
        }
        self.importer.import_into(source1, self.db_manager)

        source2 = {
            "trade_lessons": [
                {"rule_code": "L-201", "directive_text": "Conflicting text", "category": "MANDATE", "status": "ACTIVE", "created_at_utc": self.default_ts, "last_reviewed_at_utc": self.default_ts}
            ]
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            self.importer.import_into(source2, self.db_manager)
        self.assertIn("Trade lesson conflict", str(ctx.exception))

    # -----------------------------------------------------------------------
    # D-04: Conflicting Duplicate Source Records
    # -----------------------------------------------------------------------

    def test_conflicting_duplicate_cash_in_source_raises_conflict(self):
        """Verifies conflicting duplicate cash in source raises MigrationConflictError (D-04)."""
        source = {
            "portfolio_cash": [
                {"account_id": "PRIMARY", "currency": "EUR", "amount": "100.00", "last_updated_at_utc": self.default_ts},
                {"account_id": "PRIMARY", "currency": "EUR", "amount": "200.00", "last_updated_at_utc": self.default_ts},
            ]
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            self.importer.plan_migration(source)
        self.assertIn("Conflicting duplicate cash record", str(ctx.exception))

    def test_conflicting_duplicate_watchlist_in_source_raises_conflict(self):
        """Verifies conflicting duplicate watchlist in source raises MigrationConflictError (D-04)."""
        source = {
            "watchlist": [
                {"ticker": "AMD", "status": "ACTIVE", "notes": "A", "added_at_utc": self.default_ts},
                {"ticker": "AMD", "status": "PAUSED", "notes": "B", "added_at_utc": self.default_ts},
            ]
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            self.importer.plan_migration(source)
        self.assertIn("Conflicting duplicate watchlist record", str(ctx.exception))

    def test_conflicting_duplicate_trade_lessons_in_source_raises_conflict(self):
        """Verifies conflicting duplicate trade lessons in source raise MigrationConflictError (D-04)."""
        source = {
            "trade_lessons": [
                {"rule_code": "L-301", "directive_text": "Dir A", "category": "MANDATE", "status": "ACTIVE", "created_at_utc": self.default_ts, "last_reviewed_at_utc": self.default_ts},
                {"rule_code": "L-301", "directive_text": "Dir B", "category": "MANDATE", "status": "ACTIVE", "created_at_utc": self.default_ts, "last_reviewed_at_utc": self.default_ts},
            ]
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            self.importer.plan_migration(source)
        self.assertIn("Conflicting duplicate trade lesson record", str(ctx.exception))

    def test_exact_duplicates_across_all_domains_are_safely_deduplicated(self):
        """Verifies exact duplicate records across domains are deduplicated cleanly (D-04)."""
        source = {
            "portfolio_cash": [
                {"account_id": "PRIMARY", "currency": "EUR", "amount": "100.00", "last_updated_at_utc": self.default_ts},
                {"account_id": "PRIMARY", "currency": "EUR", "amount": "100.00", "last_updated_at_utc": self.default_ts},
            ],
            "portfolio_positions": [
                {"account_id": "PRIMARY", "ticker": "SPY", "shares": "10.0", "wac_eur": "500.0", "wac_usd": None, "last_updated_at_utc": self.default_ts},
                {"account_id": "PRIMARY", "ticker": "SPY", "shares": "10.0", "wac_eur": "500.0", "wac_usd": None, "last_updated_at_utc": self.default_ts},
            ],
            "watchlist": [
                {"ticker": "QQQ", "status": "ACTIVE", "notes": "ETF", "added_at_utc": self.default_ts},
                {"ticker": "QQQ", "status": "ACTIVE", "notes": "ETF", "added_at_utc": self.default_ts},
            ],
        }
        plan = self.importer.plan_migration(source)
        self.assertEqual(len(plan.cash_records), 1)
        self.assertEqual(len(plan.position_records), 1)
        self.assertEqual(len(plan.watchlist_records), 1)
        self.assertEqual(len(plan.discrepancies), 3)
        self.assertTrue(all(d["type"] == "EXACT_DUPLICATE_DEDUPLICATED" for d in plan.discrepancies))

    # -----------------------------------------------------------------------
    # D-05: Strict JSON Loading
    # -----------------------------------------------------------------------

    def test_parity_uses_strict_json_rejects_nan_and_infinity(self):
        """Verifies parity verifier rejects NaN and Infinity using strict JSON loader (D-05)."""
        nan_file = self.temp_path / "nan_source.json"
        nan_file.write_text('{"portfolio_positions": [{"ticker": "SPY", "shares": NaN}]}', encoding="utf-8")

        with self.assertRaises(MigrationValidationError):
            self.verifier.compare(nan_file, {})

        inf_file = self.temp_path / "inf_source.json"
        inf_file.write_text('{"portfolio_positions": [{"ticker": "SPY", "shares": Infinity}]}', encoding="utf-8")

        with self.assertRaises(MigrationValidationError):
            self.verifier.compare(inf_file, {})

    def test_importer_uses_strict_json_rejects_nan_and_infinity(self):
        """Verifies importer rejects NaN and Infinity using strict JSON loader (D-05)."""
        nan_file = self.temp_path / "nan_import.json"
        nan_file.write_text('{"unallocated_cash_eur": NaN}', encoding="utf-8")

        with self.assertRaises(MigrationValidationError):
            self.importer.load_source_file(nan_file)

    # -----------------------------------------------------------------------
    # D-06: Legacy Decision Log Mapping
    # -----------------------------------------------------------------------

    def test_decision_log_legacy_date_mapping_is_deterministic(self):
        """Verifies date-based legacy records map deterministically to LEGACY-SESSION-{date} (D-06)."""
        source = {
            "decision_log": [
                {
                    "date": "2026-09-22",
                    "lesson": "Stage 0 Council boot sequence",
                    "outcome": "AUTO_LOGGED",
                }
            ]
        }
        plan = self.importer.plan_migration(source, default_timestamp=self.default_ts)
        self.assertEqual(len(plan.decision_log_records), 1)
        rec = plan.decision_log_records[0]
        self.assertEqual(rec["session_id"], "LEGACY-SESSION-2026-09-22")
        self.assertEqual(rec["decision_outcome"], "AUTO_LOGGED")
        self.assertEqual(rec["timestamp_utc"], "2026-09-22T00:00:00Z")

    def test_decision_log_legacy_deliberation_mapping_is_deterministic(self):
        """Verifies nested council debate records map deterministically with sub-decisions (D-06)."""
        source = {
            "decision_log": [
                {
                    "timestamp": "2026-09-22T15:01:03",
                    "trigger_context": "STAGE_0_BOOT_SYNC",
                    "council_debate": {"bullish": "Suppressed volatility", "neutral": "Normal"},
                    "decisions": [
                        {"ticker": "UMAC", "trade_state": "HOLD"},
                        {"ticker": "DFTX", "trade_state": "HOLD"},
                    ],
                }
            ]
        }
        plan = self.importer.plan_migration(source, default_timestamp=self.default_ts)
        self.assertEqual(len(plan.decision_log_records), 2)
        r1, r2 = plan.decision_log_records
        self.assertEqual(r1["session_id"], "SESSION-2026-09-22-STAGE_0_BOOT_SYNC")
        self.assertEqual(r1["ticker"], "UMAC")
        self.assertEqual(r1["decision_outcome"], "HOLD")
        self.assertEqual(r2["session_id"], "SESSION-2026-09-22-STAGE_0_BOOT_SYNC")
        self.assertEqual(r2["ticker"], "DFTX")

    # -----------------------------------------------------------------------
    # D-07: Destination Safety Claims
    # -----------------------------------------------------------------------

    def test_destination_guard_rejects_traversal_aliases(self):
        """Verifies path traversal aliases to production database are rejected fail-closed (D-07)."""
        traversal_path = self.importer.repository_root / "tests" / ".." / "context" / "gem.db"
        with self.assertRaises(MigrationSafetyError) as ctx:
            validate_migration_destination(traversal_path, self.importer.repository_root)
        self.assertIn("resolves to protected production database", str(ctx.exception))

    def test_destination_guard_rejects_case_variations(self):
        """Verifies case variations on Windows are rejected fail-closed (D-07)."""
        case_variations = [
            self.importer.repository_root / "CONTEXT" / "GEM.DB",
            self.importer.repository_root / "Context" / "Gem.db",
            self.importer.repository_root / "CACHE" / "MARKET_DATA.DB",
        ]
        for p in case_variations:
            with self.assertRaises(MigrationSafetyError) as ctx:
                validate_migration_destination(p, self.importer.repository_root)
            self.assertIn("resolves to protected production database", str(ctx.exception))

    def test_destination_guard_rejects_relative_aliases(self):
        """Verifies relative aliases are rejected fail-closed (D-07)."""
        with self.assertRaises(MigrationSafetyError):
            validate_migration_destination("context/gem.db", self.importer.repository_root)
        with self.assertRaises(MigrationSafetyError):
            validate_migration_destination("./context/gem.db", self.importer.repository_root)
        with self.assertRaises(MigrationSafetyError):
            validate_migration_destination("cache/market_data.db", self.importer.repository_root)

    def test_destination_guard_permits_safe_disposable_paths(self):
        """Verifies valid disposable paths outside production locations are accepted (D-07)."""
        safe_path = self.temp_path / "disposable_test.db"
        validated = validate_migration_destination(safe_path, self.importer.repository_root)
        self.assertEqual(validated.resolve(), safe_path.resolve())


if __name__ == "__main__":
    unittest.main()
