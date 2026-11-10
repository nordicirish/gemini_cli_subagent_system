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
    TimestampProvenance,
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
    is_date_only,
    is_timezone_aware,
    normalize_aware_timestamp,
    deep_equal,
    records_equal,
    consolidate_scout_representations,
    consolidate_watchlist_representations,
    consolidate_position_representations,
    consolidate_cash_representations,
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
        self.default_ts = "2026-11-10T12:00:00Z"
        self.verifier = SSoTParityVerifier(self.generator, default_timestamp=self.default_ts)

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
        """Audits actual context/decision_log.json import into disposable database and verifies parity (D-06, D1.2, D1.4)."""
        dl_path = self.importer.repository_root / "context" / "decision_log.json"
        self.assertTrue(dl_path.exists())

        loaded = self.importer.load_source_file(dl_path)
        # 1. Under default strict policies (fail-closed), importing raises MigrationValidationError
        with self.assertRaises(MigrationValidationError):
            self.importer.import_into(loaded, self.db_manager, default_timestamp=self.default_ts)

        # 2. Under authorized legacy compatibility policies, importing succeeds deterministically
        res = self.importer.import_into(
            loaded,
            self.db_manager,
            default_timestamp=self.default_ts,
            allow_date_only=True,
            allow_naive_utc=True,
        )
        self.assertTrue(res.success)
        # 1 date-based record + 5 deliberation sub-decisions = 6 imported
        self.assertEqual(res.imported_counts["decision_log"], 6)

        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn)

        parity = self.verifier.compare(
            loaded,
            proj,
            allow_date_only=True,
            allow_naive_utc=True,
        )
        self.assertTrue(parity.is_parity)
        self.assertEqual(parity.summary.missing_records, 0)
        self.assertEqual(parity.summary.unexpected_records, 0)
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
        # Fail-closed without authorization
        with self.assertRaises(MigrationValidationError):
            self.importer.plan_migration(source, default_timestamp=self.default_ts)

        plan = self.importer.plan_migration(source, default_timestamp=self.default_ts, allow_date_only=True)
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
        # Fail-closed without authorization
        with self.assertRaises(MigrationValidationError):
            self.importer.plan_migration(source, default_timestamp=self.default_ts)

        plan = self.importer.plan_migration(source, default_timestamp=self.default_ts, allow_naive_utc=True)
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


# ===========================================================================
# J. Stage 1.5-D.1.1 Corrective Regressions Tests (R1 through R6)
# ===========================================================================

class TestStage15D11CorrectiveRegressions(BaseMigrationTestCase):
    """
    Exhaustive regression test suite verifying fixes for Stage 1.5-D.1.1:
    - R1: Complete duplicate detection across all 6 domains.
    - R2: Bidirectional parity verification and reverse check across all 6 domains.
    - R2: Composite position identities (account_id, ticker) across accounts.
    - R2: Zero-balance cash (0, 0.00) preserved and verified explicitly.
    - R3: Shared canonical transformations and 7-field parity for Case B and Case C.
    - R4: Strict timestamp provenance, timezone safety, and awareness normalization.
    - R5: Trade lesson review timestamp not fabricated from created timestamp.
    - R6: Real legacy file audit reconciliation (context/ssot.json, context/trade_lessons.json, context/decision_log.json).
    """

    # -----------------------------------------------------------------------
    # R1: Duplicate Detection Across All 6 Domains
    # -----------------------------------------------------------------------

    def test_duplicate_cash_conflicting_timestamp_rejected(self):
        """Verifies duplicate cash with conflicting timestamp is rejected fail-closed (R1)."""
        source = {
            "portfolio_cash": [
                {"currency": "EUR", "amount": "1000.00", "last_updated_at_utc": "2026-11-09T10:00:00Z"},
                {"currency": "EUR", "amount": "1000.00", "last_updated_at_utc": "2026-11-09T11:00:00Z"},
            ]
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            self.importer.plan_migration(source, default_timestamp=self.default_ts)
        self.assertIn("Conflicting duplicate cash record", str(ctx.exception))

    def test_duplicate_cash_conflicting_amount_rejected(self):
        """Verifies duplicate cash with conflicting amount is rejected fail-closed (R1)."""
        source = {
            "portfolio_cash": [
                {"currency": "EUR", "amount": "1000.00", "last_updated_at_utc": self.default_ts},
                {"currency": "EUR", "amount": "1500.00", "last_updated_at_utc": self.default_ts},
            ]
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            self.importer.plan_migration(source, default_timestamp=self.default_ts)
        self.assertIn("Conflicting duplicate cash record", str(ctx.exception))

    def test_duplicate_position_conflicting_timestamp_rejected(self):
        """Verifies duplicate position with conflicting timestamp is rejected fail-closed (R1)."""
        source = {
            "portfolio_snapshot": [
                {"account_id": "PRIMARY", "ticker": "SPY", "shares": "10", "wac": "500", "last_updated_at_utc": "2026-11-09T10:00:00Z"},
                {"account_id": "PRIMARY", "ticker": "SPY", "shares": "10", "wac": "500", "last_updated_at_utc": "2026-11-09T11:00:00Z"},
            ]
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            self.importer.plan_migration(source, default_timestamp=self.default_ts)
        self.assertIn("Conflicting duplicate position record", str(ctx.exception))

    def test_duplicate_position_conflicting_shares_or_cost_rejected(self):
        """Verifies duplicate position with conflicting shares is rejected fail-closed (R1)."""
        source = {
            "portfolio_snapshot": [
                {"account_id": "PRIMARY", "ticker": "SPY", "shares": "10", "wac": "500", "last_updated_at_utc": self.default_ts},
                {"account_id": "PRIMARY", "ticker": "SPY", "shares": "20", "wac": "500", "last_updated_at_utc": self.default_ts},
            ]
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            self.importer.plan_migration(source, default_timestamp=self.default_ts)
        self.assertIn("Conflicting duplicate position record", str(ctx.exception))

    def test_duplicate_watchlist_conflicting_notes_or_timestamp_rejected(self):
        """Verifies duplicate watchlist item with conflicting notes/timestamp is rejected fail-closed (R1)."""
        source = {
            "watchlist": [
                {"ticker": "GOOG", "notes": "Initial analysis", "added_at_utc": "2026-11-09T10:00:00Z"},
                {"ticker": "GOOG", "notes": "Different notes", "added_at_utc": "2026-11-09T10:00:00Z"},
            ]
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            self.importer.plan_migration(source, default_timestamp=self.default_ts)
        self.assertIn("Conflicting duplicate watchlist record", str(ctx.exception))

    def test_duplicate_scout_category_conflicting_tickers_or_timestamp_rejected(self):
        """Verifies duplicate scout category with conflicting tickers is rejected fail-closed (R1)."""
        source = {
            "scout_categories": [
                {"category_name": "TECH", "tickers": ["AAPL"], "updated_at_utc": "2026-11-09T10:00:00Z"},
                {"category_name": "TECH", "tickers": ["MSFT"], "updated_at_utc": "2026-11-09T10:00:00Z"},
            ]
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            self.importer.plan_migration(source, default_timestamp=self.default_ts)
        self.assertIn("Conflicting duplicate scout category record", str(ctx.exception))

    def test_duplicate_trade_lesson_conflicting_directive_or_category_rejected(self):
        """Verifies duplicate trade lesson with conflicting directive is rejected fail-closed (R1)."""
        source = {
            "trade_lessons": [
                {"rule_code": "L-101", "directive_text": "Rule statement A", "category": "MANDATE", "created_at_utc": self.default_ts},
                {"rule_code": "L-101", "directive_text": "Rule statement B", "category": "MANDATE", "created_at_utc": self.default_ts},
            ]
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            self.importer.plan_migration(source, default_timestamp=self.default_ts)
        self.assertIn("Conflicting duplicate trade lesson record", str(ctx.exception))

    def test_duplicate_decision_log_conflicting_outcome_or_votes_rejected(self):
        """Verifies duplicate decision log with conflicting outcome is rejected fail-closed (R1)."""
        source = {
            "decision_log": [
                {"session_id": "S1", "action_proposed": "HOLD", "decision_outcome": "APPROVED", "ticker": "SPY", "timestamp_utc": self.default_ts},
                {"session_id": "S1", "action_proposed": "HOLD", "decision_outcome": "REJECTED", "ticker": "SPY", "timestamp_utc": self.default_ts},
            ]
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            self.importer.plan_migration(source, default_timestamp=self.default_ts)
        self.assertIn("Conflicting duplicate decision log record", str(ctx.exception))

    def test_identical_duplicate_records_accepted_idempotently_all_domains(self):
        """Verifies identical duplicates across all 6 domains are deduplicated idempotently (R1)."""
        source = {
            "portfolio_cash": [
                {"currency": "EUR", "amount": "1000.00", "last_updated_at_utc": self.default_ts},
                {"currency": "EUR", "amount": "1000.00", "last_updated_at_utc": self.default_ts},
            ],
            "portfolio_snapshot": [
                {"account_id": "PRIMARY", "ticker": "SPY", "shares": "10", "wac": "500", "last_updated_at_utc": self.default_ts},
                {"account_id": "PRIMARY", "ticker": "SPY", "shares": "10.0000", "wac": "500.0000", "last_updated_at_utc": self.default_ts},
            ],
            "watchlist": [
                {"ticker": "GOOG", "notes": "Solid", "status": "ACTIVE", "added_at_utc": self.default_ts},
                {"ticker": "GOOG", "notes": "Solid", "status": "ACTIVE", "added_at_utc": self.default_ts},
            ],
            "scout_categories": [
                {"category_name": "TECH", "tickers": ["AAPL", "MSFT"], "description": "Tech", "updated_at_utc": self.default_ts},
                {"category_name": "TECH", "tickers": ["MSFT", "AAPL"], "description": "Tech", "updated_at_utc": self.default_ts},
            ],
            "trade_lessons": [
                {"rule_code": "L-101", "directive_text": "Rule", "category": "MANDATE", "created_at_utc": self.default_ts},
                {"rule_code": "L-101", "directive_text": "Rule", "category": "MANDATE", "created_at_utc": self.default_ts},
            ],
            "decision_log": [
                {"session_id": "S1", "action_proposed": "HOLD", "decision_outcome": "APPROVED", "ticker": "SPY", "timestamp_utc": self.default_ts, "votes": {"score": "0.9"}},
                {"session_id": "S1", "action_proposed": "HOLD", "decision_outcome": "APPROVED", "ticker": "SPY", "timestamp_utc": self.default_ts, "votes": {"score": Decimal("0.9")}},
            ],
        }
        plan = self.importer.plan_migration(source, default_timestamp=self.default_ts)
        self.assertEqual(len(plan.cash_records), 1)
        self.assertEqual(len(plan.position_records), 1)
        self.assertEqual(len(plan.watchlist_records), 1)
        self.assertEqual(len(plan.scout_categories), 1)
        self.assertEqual(len(plan.trade_lessons), 1)
        self.assertEqual(len(plan.decision_log_records), 1)

    # -----------------------------------------------------------------------
    # R2: Bidirectional Parity Verification and Reverse Checking
    # -----------------------------------------------------------------------

    def test_zero_balance_cash_preserved_and_verified_explicitly(self):
        """Verifies zero-balance cash ('0', '0.00') is preserved and compared explicitly (R2)."""
        source = {
            "unallocated_cash_eur": "0.00",
            "portfolio_cash": [{"currency": "EUR", "amount": "0.00", "last_updated_at_utc": self.default_ts}],
        }
        res = self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)
        self.assertTrue(res.success)

        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn)

        parity = self.verifier.compare(source, proj)
        self.assertTrue(parity.is_parity)
        self.assertEqual(parity.summary.financial_mismatches, 0)
        self.assertEqual(parity.summary.missing_records, 0)
        self.assertEqual(parity.summary.unexpected_records, 0)

    def test_reverse_cash_unexpected_projected_detected(self):
        """Verifies reverse cash check detects unexpected projected cash (R2)."""
        source = {"unallocated_cash_eur": "1000.00"}
        self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)

        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn)

        legacy_empty = {}
        res = self.verifier.compare(legacy_empty, proj)
        self.assertFalse(res.is_parity)
        self.assertGreaterEqual(res.summary.unexpected_records, 1)
        cash_unexp = [d for d in res.discrepancies if d.domain == "portfolio_cash" and d.discrepancy_type == DiscrepancyType.UNEXPECTED_RECORD.value]
        self.assertTrue(len(cash_unexp) > 0)

    def test_reverse_position_unexpected_projected_detected(self):
        """Verifies reverse position check detects unexpected projected positions (R2)."""
        source = {
            "portfolio_snapshot": [
                {"account_id": "PRIMARY", "ticker": "SPY", "shares": "10", "wac": "500"},
                {"account_id": "PRIMARY", "ticker": "QQQ", "shares": "20", "wac": "300"},
            ]
        }
        self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)

        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn)

        legacy_spy = {
            "portfolio_snapshot": [{"account_id": "PRIMARY", "ticker": "SPY", "shares": "10", "wac": "500"}]
        }
        res = self.verifier.compare(legacy_spy, proj)
        self.assertFalse(res.is_parity)
        self.assertEqual(res.summary.unexpected_records, 1)
        self.assertIn("QQQ", res.discrepancies[0].path)

    def test_composite_position_identity_multi_account_supported(self):
        """Verifies multi-account positions with composite identity (account_id, ticker) do not collide (R2)."""
        source = {
            "portfolio_snapshot": [
                {"account_id": "PRIMARY", "ticker": "SPY", "shares": "10.0", "wac": "500.0", "last_updated_at_utc": self.default_ts},
                {"account_id": "TAXABLE", "ticker": "SPY", "shares": "25.0", "wac": "480.0", "last_updated_at_utc": self.default_ts},
            ]
        }
        res = self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)
        self.assertTrue(res.success)
        self.assertEqual(res.imported_counts["portfolio_positions"], 2)

        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn)

        parity = self.verifier.compare(source, proj)
        self.assertTrue(parity.is_parity)
        self.assertEqual(parity.summary.missing_records, 0)
        self.assertEqual(parity.summary.unexpected_records, 0)
        self.assertGreaterEqual(parity.summary.exact_matches, 2)

    def test_reverse_watchlist_unexpected_projected_detected(self):
        """Verifies reverse watchlist check detects unexpected projected watched tickers (R2)."""
        source = {"watched_tickers": ["GOOG", "AMZN"]}
        self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)

        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn)

        legacy = {"watched_tickers": ["GOOG"]}
        res = self.verifier.compare(legacy, proj)
        self.assertFalse(res.is_parity)
        self.assertEqual(res.summary.unexpected_records, 1)
        self.assertIn("AMZN", res.discrepancies[0].path)

    def test_reverse_scout_category_unexpected_projected_detected(self):
        """Verifies reverse scout category check detects unexpected projected categories (R2)."""
        source = {
            "scout_categories": [
                {"category_name": "TECH", "tickers": ["AAPL"]},
                {"category_name": "ENERGY", "tickers": ["XOM"]},
            ]
        }
        self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)

        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn)

        legacy = {"scout_categories": [{"category_name": "TECH", "tickers": ["AAPL"]}]}
        res = self.verifier.compare(legacy, proj)
        self.assertFalse(res.is_parity)
        self.assertEqual(res.summary.unexpected_records, 1)
        self.assertIn("ENERGY", res.discrepancies[0].path)

    def test_reverse_trade_lesson_unexpected_projected_detected(self):
        """Verifies reverse trade lesson check detects unexpected projected trade lessons (R2)."""
        source = {
            "trade_lessons": [
                {"rule_code": "L-101", "directive_text": "First rule", "category": "MANDATE"},
                {"rule_code": "L-102", "directive_text": "Second rule", "category": "RISK"},
            ]
        }
        self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)

        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn)

        legacy = {"trade_lessons": [{"rule_code": "L-101", "directive_text": "First rule", "category": "MANDATE"}]}
        res = self.verifier.compare(legacy, proj)
        self.assertFalse(res.is_parity)
        self.assertEqual(res.summary.unexpected_records, 1)
        self.assertIn("L-102", res.discrepancies[0].path)

    def test_reverse_decision_log_unexpected_projected_detected(self):
        """Verifies reverse decision log check detects unexpected projected decisions (R2)."""
        source = {
            "decision_log": [
                {"session_id": "S1", "action_proposed": "HOLD", "decision_outcome": "APPROVED", "ticker": "SPY", "timestamp_utc": self.default_ts},
                {"session_id": "S2", "action_proposed": "BUY", "decision_outcome": "REJECTED", "ticker": "QQQ", "timestamp_utc": self.default_ts},
            ]
        }
        self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)

        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn)

        legacy = {
            "decision_log": [
                {"session_id": "S1", "action_proposed": "HOLD", "decision_outcome": "APPROVED", "ticker": "SPY", "timestamp_utc": self.default_ts}
            ]
        }
        res = self.verifier.compare(legacy, proj)
        self.assertFalse(res.is_parity)
        self.assertEqual(res.summary.unexpected_records, 1)
        self.assertIn("S2", res.discrepancies[0].path)

    # -----------------------------------------------------------------------
    # R3: Shared Canonical Transformations & Complete 7-Field Parity
    # -----------------------------------------------------------------------

    def test_decision_case_b_7_fields_parity_verified(self):
        """Verifies Case B date-based decision record is verified across all 7 fields in parity (R3)."""
        source = {
            "decision_log": [
                {
                    "date": "2026-09-22",
                    "lesson": "Stage 0 Council boot sequence",
                    "outcome": "AUTO_LOGGED",
                    "ticker": "SESSION",
                }
            ]
        }
        res = self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts, allow_date_only=True)
        self.assertTrue(res.success)

        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn)

        parity = self.verifier.compare(source, proj, allow_date_only=True)
        self.assertTrue(parity.is_parity)
        self.assertEqual(parity.summary.missing_records, 0)
        self.assertEqual(parity.summary.unexpected_records, 0)
        self.assertEqual(parity.summary.exact_matches, 1)

    def test_decision_case_c_7_fields_parity_verified(self):
        """Verifies Case C deliberation debate record with sub-decisions verifies all 7 fields in parity (R3)."""
        source = {
            "decision_log": [
                {
                    "timestamp": "2026-09-22T15:01:03",
                    "trigger_context": "STAGE_0_BOOT_SYNC",
                    "council_debate": {"bullish": "Positive gamma", "neutral": "Normal"},
                    "decisions": [
                        {
                            "ticker": "UMAC",
                            "trade_state": "HOLD",
                            "scrutiny_audit": {
                                "agreement_score_sa": 0.91,
                                "fatal_flaw_score": 1,
                                "final_posture": "STABLE",
                                "agent_votes": [
                                    {"agent": "BULLISH_ADVOCATE", "verdict": "HOLD", "confidence": 0.92, "self_critique": "Intact"}
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        res = self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts, allow_naive_utc=True)
        self.assertTrue(res.success)

        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn)

        parity = self.verifier.compare(source, proj, allow_naive_utc=True)
        self.assertTrue(parity.is_parity)
        self.assertEqual(parity.summary.missing_records, 0)
        self.assertEqual(parity.summary.unexpected_records, 0)
        self.assertEqual(parity.summary.exact_matches, 1)

    # -----------------------------------------------------------------------
    # R4: Strict Timestamp Provenance & Timezone Safety
    # -----------------------------------------------------------------------

    def test_date_only_rejected_without_explicit_authorization(self):
        """Verifies date-only timestamp is rejected fail-closed without allow_date_only=True (R4)."""
        with self.assertRaises(MigrationValidationError) as ctx:
            resolve_historical_timestamp("2026-09-22", field_name="test_date", allow_date_only=False)
        self.assertIn("date-only value", str(ctx.exception))

        ts, prov = resolve_historical_timestamp("2026-09-22", field_name="test_date", allow_date_only=True)
        self.assertEqual(ts, "2026-09-22T00:00:00Z")
        self.assertEqual(prov, TimestampProvenance.DERIVED)

    def test_timezone_naive_rejected_without_explicit_authorization(self):
        """Verifies naive timestamp is rejected fail-closed without allow_naive_utc=True (R4)."""
        with self.assertRaises(MigrationValidationError) as ctx:
            resolve_historical_timestamp("2026-09-22T15:01:03", field_name="test_ts", allow_naive_utc=False)
        self.assertIn("timezone-naive timestamp", str(ctx.exception))

        ts, prov = resolve_historical_timestamp("2026-09-22T15:01:03", field_name="test_ts", allow_naive_utc=True)
        self.assertEqual(ts, "2026-09-22T15:01:03Z")
        self.assertEqual(prov, TimestampProvenance.DERIVED)

    def test_timezone_aware_normalized_to_canonical_utc_z(self):
        """Verifies aware timestamps with arbitrary offsets are converted to UTC ending in Z (R4)."""
        norm1 = normalize_aware_timestamp("2026-09-22T17:00:00+02:00")
        self.assertEqual(norm1, "2026-09-22T15:00:00Z")

        norm2 = normalize_aware_timestamp("2026-09-22T10:00:00-05:00")
        self.assertEqual(norm2, "2026-09-22T15:00:00Z")

        ts, prov = resolve_historical_timestamp("2026-09-22T17:00:00+02:00", field_name="test_ts")
        self.assertEqual(ts, "2026-09-22T15:00:00Z")
        self.assertEqual(prov, TimestampProvenance.SOURCE_PROVIDED)

    # -----------------------------------------------------------------------
    # R5: Trade Lesson Review Timestamp Provenance
    # -----------------------------------------------------------------------

    def test_trade_lesson_review_timestamp_not_fabricated_from_created(self):
        """Verifies trade lesson review timestamp remains None if omitted from source (R5)."""
        source = {
            "trade_lessons": [
                {
                    "rule_code": "L-201",
                    "directive_text": "Do not trade against trend",
                    "category": "MANDATE",
                    "status": "ACTIVE",
                    "created_at_utc": "2026-10-01T12:00:00Z",
                }
            ]
        }
        plan = self.importer.plan_migration(source, default_timestamp=self.default_ts)
        self.assertEqual(len(plan.trade_lessons), 1)
        rec = plan.trade_lessons[0]
        self.assertEqual(rec["created_at_utc"], "2026-10-01T12:00:00Z")
        self.assertIsNone(rec["last_reviewed_at_utc"])

    # -----------------------------------------------------------------------
    # R6: Real Legacy Files Audit Reconciliation
    # -----------------------------------------------------------------------

    def test_real_legacy_files_hash_and_record_counts_reconciled(self):
        """Verifies SHA-256 hashes and record counts of real legacy files match audited values (R6)."""
        repo_root = self.importer.repository_root

        # 1. context/ssot.json
        ssot_path = repo_root / "context" / "ssot.json"
        self.assertTrue(ssot_path.exists())
        with open(ssot_path, "rb") as f:
            ssot_sha = hashlib.sha256(f.read()).hexdigest()
        ssot_data, loaded_sha, _ = load_strict_json_file(ssot_path)
        self.assertEqual(ssot_sha, loaded_sha)
        self.assertEqual(len(ssot_data.get("mutable_state", {}).get("portfolio_snapshot", [])), 1)
        self.assertEqual(len(ssot_data.get("mutable_state", {}).get("watched_tickers", [])), 2)
        self.assertIsNone(ssot_data.get("unallocated_cash_eur"))

        # 2. context/trade_lessons.json
        tl_path = repo_root / "context" / "trade_lessons.json"
        self.assertTrue(tl_path.exists())
        with open(tl_path, "rb") as f:
            tl_sha = hashlib.sha256(f.read()).hexdigest()
        tl_data, loaded_tl_sha, _ = load_strict_json_file(tl_path)
        self.assertEqual(tl_sha, loaded_tl_sha)
        self.assertEqual(len(tl_data.get("trade_lessons", [])), 24)

        # 3. context/decision_log.json
        dl_path = repo_root / "context" / "decision_log.json"
        self.assertTrue(dl_path.exists())
        with open(dl_path, "rb") as f:
            dl_sha = hashlib.sha256(f.read()).hexdigest()
        dl_data, loaded_dl_sha, _ = load_strict_json_file(dl_path)
        self.assertEqual(dl_sha, loaded_dl_sha)
        self.assertEqual(len(dl_data.get("decision_log", [])), 2)
        self.assertEqual(len(dl_data.get("decision_log", [])[1].get("decisions", [])), 5)


# ===========================================================================
# Stage 1.5-D.1.1.1 Corrective Regression Tests (R1 - R5)
# ===========================================================================

class TestStage15D111CorrectiveRegressions(BaseMigrationTestCase):
    """
    Focused regression test suite for Stage 1.5-D.1.1.1:
    - R1: Parity rejects unexplained projected timestamp when source missing.
    - R1: Parity accepts explicit caller default timestamp.
    - R2: Nullable trade lesson review timestamp remains null in SQLite & parity.
    - R2: Trade lesson review timestamp persisted on insert.
    - R2: Trade lesson review timestamp conflict detected.
    - R3: Fail-closed on Case C malformed sub-decisions (non-dict, null, empty).
    - R3: Case C imports every valid sub-decision.
    - R4: Canonical normalization of structured-only watchlist & scout categories.
    - R4: Mixed legacy & structured inputs without double-counting.
    - R5: Complete bidirectional parity & collision-proof decision projection checks.
    """

    def test_parity_rejects_unexplained_projected_timestamp_when_source_missing(self):
        """Verifies parity rejects an unexplained projected timestamp when source timestamp is missing (R1)."""
        source = {
            "watchlist": [
                {"ticker": "NVDA", "notes": "No timestamp in source", "status": "ACTIVE"}
            ]
        }
        res = self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)
        self.assertTrue(res.success)

        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn)

        # Verifier without default_timestamp cannot explain the projected default timestamp
        verifier_no_default = SSoTParityVerifier(self.generator, default_timestamp=None)
        parity = verifier_no_default.compare(source, proj)
        self.assertFalse(parity.is_parity)
        self.assertGreaterEqual(len(parity.discrepancies), 1)
        ts_mismatches = [
            d for d in parity.discrepancies
            if "added_at_utc" in d.path and d.discrepancy_type == DiscrepancyType.TIMESTAMP_MISMATCH.value
        ]
        self.assertEqual(len(ts_mismatches), 1)

    def test_parity_accepts_explicit_caller_default_timestamp(self):
        """Verifies parity accepts projected timestamp when caller explicitly authorizes matching default (R1)."""
        source = {
            "watchlist": [
                {"ticker": "NVDA", "notes": "Authorized default timestamp", "status": "ACTIVE"}
            ]
        }
        res = self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)
        self.assertTrue(res.success)

        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn)

        verifier_with_default = SSoTParityVerifier(self.generator, default_timestamp=self.default_ts)
        parity = verifier_with_default.compare(source, proj)
        self.assertTrue(parity.is_parity)
        self.assertEqual(len(parity.discrepancies), 0)
        self.assertEqual(parity.summary.exact_matches, 1)

    def test_nullable_review_timestamp_must_remain_null(self):
        """Verifies trade lesson without review timestamp persists genuine NULL in SQLite and verifies as None (R2)."""
        source = {
            "trade_lessons": [
                {
                    "rule_code": "L-999",
                    "directive_text": "Preserve genuine null for unreviewed lessons",
                    "category": "MANDATE",
                    "status": "ACTIVE",
                    "created_at_utc": "2026-11-01T10:00:00Z",
                }
            ]
        }
        res = self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)
        self.assertTrue(res.success)

        with self.db_manager.connection() as conn:
            row = conn.execute(
                "SELECT created_at_utc, last_reviewed_at_utc FROM trade_lessons WHERE rule_code = 'L-999'"
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "2026-11-01T10:00:00Z")
            self.assertIsNone(row[1])

            proj = self.generator.generate(conn)

        # Check projected lesson has None
        proj_lessons = proj.get("trade_lessons", [])
        self.assertEqual(len(proj_lessons), 1)
        self.assertIsNone(proj_lessons[0].get("last_reviewed_at_utc"))

        # Verify parity passes and matches None to None
        parity = self.verifier.compare(source, proj)
        self.assertTrue(parity.is_parity)
        self.assertEqual(parity.summary.exact_matches, 1)

    def test_trade_lesson_review_timestamp_persisted_on_insert(self):
        """Verifies trade lesson with valid review timestamp persists to SQLite and projects faithfully (R2)."""
        source = {
            "trade_lessons": [
                {
                    "rule_code": "L-888",
                    "directive_text": "Explicit review history persisted faithfully",
                    "category": "MANDATE",
                    "status": "ACTIVE",
                    "created_at_utc": "2026-10-01T10:00:00Z",
                    "last_reviewed_at_utc": "2026-10-15T14:30:00Z",
                }
            ]
        }
        res = self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)
        self.assertTrue(res.success)

        with self.db_manager.connection() as conn:
            row = conn.execute(
                "SELECT created_at_utc, last_reviewed_at_utc FROM trade_lessons WHERE rule_code = 'L-888'"
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "2026-10-01T10:00:00Z")
            self.assertEqual(row[1], "2026-10-15T14:30:00Z")

            proj = self.generator.generate(conn)

        proj_lessons = proj.get("trade_lessons", [])
        self.assertEqual(len(proj_lessons), 1)
        self.assertEqual(proj_lessons[0].get("last_reviewed_at_utc"), "2026-10-15T14:30:00Z")

        parity = self.verifier.compare(source, proj)
        self.assertTrue(parity.is_parity)
        self.assertEqual(parity.summary.exact_matches, 1)

    def test_trade_lesson_review_timestamp_conflict_detected(self):
        """Verifies duplicate trade lesson with conflicting review timestamps is rejected (R2)."""
        source = {
            "trade_lessons": [
                {
                    "rule_code": "L-777",
                    "directive_text": "Same directive",
                    "category": "MANDATE",
                    "status": "ACTIVE",
                    "created_at_utc": "2026-10-01T10:00:00Z",
                    "last_reviewed_at_utc": "2026-10-15T14:30:00Z",
                },
                {
                    "rule_code": "L-777",
                    "directive_text": "Same directive",
                    "category": "MANDATE",
                    "status": "ACTIVE",
                    "created_at_utc": "2026-10-01T10:00:00Z",
                    "last_reviewed_at_utc": "2026-10-20T16:00:00Z",
                },
            ]
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            self.importer.plan_migration(source, default_timestamp=self.default_ts)
        self.assertIn("Conflicting duplicate trade lesson record", str(ctx.exception))

    def test_case_c_rejects_non_dict_subdecision(self):
        """Verifies Case C decision rejects non-dictionary sub-decision fail-closed (R3)."""
        source = {
            "decision_log": [
                {
                    "timestamp": "2026-09-22T15:01:03Z",
                    "trigger_context": "STAGE_0_BOOT_SYNC",
                    "council_debate": {"bullish": "Positive"},
                    "decisions": ["NOT_A_DICT_ENTRY"],
                }
            ]
        }
        with self.assertRaises(MigrationValidationError) as ctx:
            self.importer.plan_migration(source, default_timestamp=self.default_ts)
        self.assertIn("sub-decision must be a dict", str(ctx.exception))
        self.assertIn("decisions[0]", str(ctx.exception))

    def test_case_c_rejects_null_subdecision(self):
        """Verifies Case C decision rejects null sub-decision fail-closed (R3)."""
        source = {
            "decision_log": [
                {
                    "timestamp": "2026-09-22T15:01:03Z",
                    "trigger_context": "STAGE_0_BOOT_SYNC",
                    "council_debate": {"bullish": "Positive"},
                    "decisions": [None],
                }
            ]
        }
        with self.assertRaises(MigrationValidationError) as ctx:
            self.importer.plan_migration(source, default_timestamp=self.default_ts)
        self.assertIn("sub-decision must be a dict, got NoneType", str(ctx.exception))
        self.assertIn("decisions[0]", str(ctx.exception))

    def test_case_c_rejects_empty_invalid_subdecision(self):
        """Verifies Case C decision rejects empty sub-decision missing required fields fail-closed (R3)."""
        source = {
            "decision_log": [
                {
                    "timestamp": "2026-09-22T15:01:03Z",
                    "trigger_context": "STAGE_0_BOOT_SYNC",
                    "council_debate": {"bullish": "Positive"},
                    "decisions": [{}],
                }
            ]
        }
        with self.assertRaises(MigrationValidationError) as ctx:
            self.importer.plan_migration(source, default_timestamp=self.default_ts)
        self.assertIn("sub-decision cannot be empty", str(ctx.exception))
        self.assertIn("decisions[0]", str(ctx.exception))

    def test_case_c_imports_every_valid_subdecision(self):
        """Verifies Case C deliberation with multiple sub-decisions imports every entry (R3)."""
        source = {
            "decision_log": [
                {
                    "timestamp": "2026-09-22T15:01:03Z",
                    "trigger_context": "STAGE_0_BOOT_SYNC",
                    "council_debate": {"bullish": "Positive", "neutral": "Neutral"},
                    "decisions": [
                        {"ticker": "UMAC", "trade_state": "HOLD", "scrutiny_audit": {"final_posture": "STABLE"}},
                        {"ticker": "SPY", "trade_state": "BUY", "scrutiny_audit": {"final_posture": "ACCUMULATE"}},
                        {"ticker": "QQQ", "trade_state": "NO_TRADE", "scrutiny_audit": {"final_posture": "CAUTION"}},
                    ],
                }
            ]
        }
        res = self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)
        self.assertTrue(res.success)
        self.assertEqual(res.imported_counts["decision_log"], 3)

        with self.db_manager.connection() as conn:
            rows = conn.execute("SELECT ticker, action_proposed FROM decision_log ORDER BY ticker").fetchall()
            self.assertEqual(len(rows), 3)
            self.assertEqual([r[0] for r in rows], ["QQQ", "SPY", "UMAC"])
            proj = self.generator.generate(conn)

        parity = self.verifier.compare(source, proj)
        self.assertTrue(parity.is_parity)
        self.assertEqual(parity.summary.exact_matches, 3)

    def test_structured_only_watchlist_parity(self):
        """Verifies structured-only watchlist input format is normalized and verified in parity (R4)."""
        source = {
            "watchlist": [
                {"ticker": "NVDA", "notes": "AI accelerator", "status": "ACTIVE", "added_at_utc": self.default_ts}
            ]
        }
        res = self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)
        self.assertTrue(res.success)
        self.assertEqual(res.imported_counts["watchlist"], 1)

        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn)

        parity = self.verifier.compare(source, proj)
        self.assertTrue(parity.is_parity)
        self.assertEqual(parity.summary.missing_records, 0)
        self.assertEqual(parity.summary.unexpected_records, 0)
        self.assertEqual(parity.summary.exact_matches, 1)

    def test_structured_only_scout_categories_parity(self):
        """Verifies structured-only scout categories format is normalized and verified in parity (R4)."""
        source = {
            "scout_categories": [
                {"category_name": "CORE_RADAR", "tickers": ["MSFT"], "description": "Core radar", "updated_at_utc": self.default_ts}
            ]
        }
        res = self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)
        self.assertTrue(res.success)
        self.assertEqual(res.imported_counts["scout_categories"], 1)

        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn)

        parity = self.verifier.compare(source, proj)
        self.assertTrue(parity.is_parity)
        self.assertEqual(parity.summary.missing_records, 0)
        self.assertEqual(parity.summary.unexpected_records, 0)
        self.assertEqual(parity.summary.exact_matches, 1)

    def test_mixed_legacy_and_structured_watchlist_no_double_count(self):
        """Verifies mixed legacy and structured watchlist inputs are deduplicated without double-counting (R4)."""
        source = {
            "watched_tickers": ["AAPL"],
            "watchlist": [
                {"ticker": "AAPL", "status": "ACTIVE", "added_at_utc": self.default_ts}
            ],
        }
        res = self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)
        self.assertTrue(res.success)
        self.assertEqual(res.imported_counts["watchlist"], 1)

        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn)

        parity = self.verifier.compare(source, proj)
        self.assertTrue(parity.is_parity)
        self.assertEqual(parity.summary.missing_records, 0)
        self.assertEqual(parity.summary.unexpected_records, 0)
        self.assertEqual(parity.summary.exact_matches, 1)

    def test_mixed_legacy_and_structured_scout_no_double_count(self):
        """Verifies mixed legacy and structured scout category inputs are deduplicated without double-counting (R4)."""
        source = {
            "scout_candidates": {"CORE_RADAR": ["TSLA"]},
            "scout_categories": [
                {"category_name": "CORE_RADAR", "tickers": ["TSLA"], "updated_at_utc": self.default_ts}
            ],
        }
        res = self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)
        self.assertTrue(res.success)
        self.assertEqual(res.imported_counts["scout_categories"], 1)

        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn)

        parity = self.verifier.compare(source, proj)
        self.assertTrue(parity.is_parity)
        self.assertEqual(parity.summary.missing_records, 0)
        self.assertEqual(parity.summary.unexpected_records, 0)
        self.assertEqual(parity.summary.exact_matches, 1)

    def test_decision_projection_duplicate_identity_detected(self):
        """Verifies collision-proof reverse checks detect duplicate projected decisions mapped to one source (R5)."""
        legacy = {
            "decision_log": [
                {
                    "session_id": "S1",
                    "ticker": "SPY",
                    "action_proposed": "HOLD",
                    "decision_outcome": "APPROVED",
                    "timestamp_utc": self.default_ts,
                }
            ]
        }
        # Simulated projected state with two decisions matching the same legacy record
        projected = {
            "decision_log": [
                {
                    "session_id": "S1",
                    "ticker": "SPY",
                    "action_proposed": "HOLD",
                    "decision_outcome": "APPROVED",
                    "timestamp_utc": self.default_ts,
                },
                {
                    "session_id": "S1",
                    "ticker": "SPY",
                    "action_proposed": "HOLD",
                    "decision_outcome": "APPROVED",
                    "timestamp_utc": self.default_ts,
                },
            ]
        }
        res = self.verifier.compare(legacy, projected)
        self.assertFalse(res.is_parity)
        self.assertGreaterEqual(res.summary.unexpected_records, 1)
        unexpected_decisions = [
            d for d in res.discrepancies
            if d.domain == "decision_log" and d.discrepancy_type == DiscrepancyType.UNEXPECTED_RECORD.value
        ]
        self.assertEqual(len(unexpected_decisions), 1)

    def test_case_c_duplicate_subdecision_conflict_rejected(self):
        """Verifies Case C decision with colliding sub-decision identities raises MigrationConflictError (R3)."""
        source = {
            "decision_log": [
                {
                    "timestamp": "2026-09-22T15:01:03Z",
                    "trigger_context": "STAGE_0_BOOT_SYNC",
                    "council_debate": {"bullish": "Positive"},
                    "decisions": [
                        {"ticker": "UMAC", "trade_state": "HOLD", "scrutiny_audit": {"final_posture": "STABLE"}},
                        {"ticker": "UMAC", "trade_state": "HOLD", "scrutiny_audit": {"final_posture": "CONFLICT"}},
                    ],
                }
            ]
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            self.importer.plan_migration(source, default_timestamp=self.default_ts)
        self.assertIn("duplicate/colliding canonical sub-decision identity", str(ctx.exception))


class TestStage15D112ParityAndConflictingRepresentations(BaseMigrationTestCase):
    """
    Stage 1.5-D.1.1.2 Regression Suite for Findings F2 and F3:
    - F2: Complete field-level semantic parity without truthiness checks across all 6 domains.
    - F3: Rejection of conflicting structured source representations and deterministic consolidation.
    """

    def test_cash_domain_explicit_parity_and_zero_preservation(self):
        """F2: Validates zero cash amount preservation and explicit field comparison in cash parity."""
        # 1. Zero cash amount preserved and matches
        source = {
            "portfolio_cash": [
                {"account_id": "PRIMARY", "currency": "EUR", "amount": "0.00", "last_updated_at_utc": self.default_ts}
            ]
        }
        res = self.importer.import_into(source, self.db_manager, default_timestamp=self.default_ts)
        self.assertTrue(res.success)
        with self.db_manager.connection() as conn:
            proj = self.generator.generate(conn)
        parity = self.verifier.compare(source, proj)
        self.assertTrue(parity.is_parity)
        self.assertGreaterEqual(parity.summary.exact_matches, 1)

        # 2. Financial mismatch detected
        mismatch_proj = {
            "portfolio_cash": [
                {"account_id": "PRIMARY", "currency": "EUR", "amount": "100.00", "last_updated_at_utc": self.default_ts}
            ],
            "unallocated_cash_eur": "100.00",
        }
        p_res = self.verifier.compare(source, mismatch_proj)
        self.assertFalse(p_res.is_parity)
        self.assertGreaterEqual(p_res.summary.financial_mismatches, 1)

    def test_positions_wac_presence_and_explicit_field_parity(self):
        """F2: Validates explicit presence and value equality for WAC EUR, WAC USD, and shares."""
        # 1. Legacy has WAC USD, projected has None -> FINANCIAL_MISMATCH
        source = {
            "positions": [
                {
                    "account_id": "PRIMARY",
                    "ticker": "AAPL",
                    "shares": "10.0",
                    "wac": "150.00",
                    "wac_usd": "165.00",
                    "last_updated_at_utc": self.default_ts,
                }
            ]
        }
        proj_no_usd = {
            "portfolio_snapshot": [
                {
                    "account_id": "PRIMARY",
                    "ticker": "AAPL",
                    "shares": "10.0",
                    "wac_eur": "150.00",
                    "wac_usd": None,
                    "last_updated_at_utc": self.default_ts,
                }
            ]
        }
        p1 = self.verifier.compare(source, proj_no_usd)
        self.assertFalse(p1.is_parity)
        self.assertTrue(any("WAC USD presence mismatch" in d.message for d in p1.discrepancies))

        # 2. Legacy has None WAC USD, projected has WAC USD -> FINANCIAL_MISMATCH
        source_no_usd = {
            "positions": [
                {
                    "account_id": "PRIMARY",
                    "ticker": "AAPL",
                    "shares": "10.0",
                    "wac": "150.00",
                    "wac_usd": None,
                    "last_updated_at_utc": self.default_ts,
                }
            ]
        }
        proj_with_usd = {
            "portfolio_snapshot": [
                {
                    "account_id": "PRIMARY",
                    "ticker": "AAPL",
                    "shares": "10.0",
                    "wac_eur": "150.00",
                    "wac_usd": "165.00",
                    "last_updated_at_utc": self.default_ts,
                }
            ]
        }
        p2 = self.verifier.compare(source_no_usd, proj_with_usd)
        self.assertFalse(p2.is_parity)
        self.assertTrue(any("WAC USD presence mismatch" in d.message for d in p2.discrepancies))

        # 3. WAC EUR presence mismatch
        proj_no_eur = {
            "portfolio_snapshot": [
                {
                    "account_id": "PRIMARY",
                    "ticker": "AAPL",
                    "shares": "10.0",
                    "wac_eur": None,
                    "wac_usd": None,
                    "last_updated_at_utc": self.default_ts,
                }
            ]
        }
        p3 = self.verifier.compare(source_no_usd, proj_no_eur)
        self.assertFalse(p3.is_parity)
        self.assertTrue(any("WAC EUR presence mismatch" in d.message for d in p3.discrepancies))

        # 4. Shares mismatch
        proj_diff_shares = {
            "portfolio_snapshot": [
                {
                    "account_id": "PRIMARY",
                    "ticker": "AAPL",
                    "shares": "12.0",
                    "wac_eur": "150.00",
                    "wac_usd": None,
                    "last_updated_at_utc": self.default_ts,
                }
            ]
        }
        p4 = self.verifier.compare(source_no_usd, proj_diff_shares)
        self.assertFalse(p4.is_parity)
        self.assertEqual(p4.summary.financial_mismatches, 1)

    def test_watchlist_notes_none_vs_empty_vs_string_parity(self):
        """F2: Validates explicit comparison of watchlist notes (None vs empty vs string) and status."""
        # 1. notes=None in legacy, but string in projected -> FIELD_MISMATCH
        legacy_none = {
            "watchlist": [
                {"ticker": "NVDA", "status": "ACTIVE", "notes": None, "added_at_utc": self.default_ts}
            ]
        }
        proj_str = {
            "watched_tickers": ["NVDA"],
            "watchlist": [
                {"ticker": "NVDA", "status": "ACTIVE", "notes": "Leader", "added_at_utc": self.default_ts}
            ]
        }
        p1 = self.verifier.compare(legacy_none, proj_str)
        self.assertFalse(p1.is_parity)
        self.assertTrue(any("Notes mismatch" in d.message for d in p1.discrepancies))

        # 2. notes="" in legacy, but string in projected -> FIELD_MISMATCH
        legacy_empty = {
            "watchlist": [
                {"ticker": "NVDA", "status": "ACTIVE", "notes": "", "added_at_utc": self.default_ts}
            ]
        }
        p2 = self.verifier.compare(legacy_empty, proj_str)
        self.assertFalse(p2.is_parity)
        self.assertTrue(any("Notes mismatch" in d.message for d in p2.discrepancies))

        # 3. notes=None in legacy vs notes="" in projected (null vs empty) -> FIELD_MISMATCH
        proj_empty = {
            "watched_tickers": ["NVDA"],
            "watchlist": [
                {"ticker": "NVDA", "status": "ACTIVE", "notes": "", "added_at_utc": self.default_ts}
            ]
        }
        p3 = self.verifier.compare(legacy_none, proj_empty)
        self.assertFalse(p3.is_parity)
        self.assertTrue(any("Notes mismatch" in d.message for d in p3.discrepancies))

        # 4. Status mismatch
        proj_status = {
            "watched_tickers": ["NVDA"],
            "watchlist": [
                {"ticker": "NVDA", "status": "PAUSED", "notes": None, "added_at_utc": self.default_ts}
            ]
        }
        p4 = self.verifier.compare(legacy_none, proj_status)
        self.assertFalse(p4.is_parity)
        self.assertTrue(any("Status mismatch" in d.message for d in p4.discrepancies))

    def test_scout_categories_description_and_tickers_explicit_parity(self):
        """F2: Validates explicit comparison of scout category description and ticker collections."""
        # 1. description=None in legacy vs string in projected -> FIELD_MISMATCH
        legacy_none = {
            "scout_categories": [
                {"category_name": "TECH", "description": None, "tickers": ["AAPL"], "updated_at_utc": self.default_ts}
            ]
        }
        proj_str = {
            "scout_category_records": [
                {"category_name": "TECH", "description": "Tech stocks", "tickers": ["AAPL"], "updated_at_utc": self.default_ts}
            ]
        }
        p1 = self.verifier.compare(legacy_none, proj_str)
        self.assertFalse(p1.is_parity)
        self.assertTrue(any("Description mismatch" in d.message for d in p1.discrepancies))

        # 2. description="" in legacy vs None in projected -> FIELD_MISMATCH
        legacy_empty = {
            "scout_categories": [
                {"category_name": "TECH", "description": "", "tickers": ["AAPL"], "updated_at_utc": self.default_ts}
            ]
        }
        proj_none = {
            "scout_category_records": [
                {"category_name": "TECH", "description": None, "tickers": ["AAPL"], "updated_at_utc": self.default_ts}
            ]
        }
        p2 = self.verifier.compare(legacy_empty, proj_none)
        self.assertFalse(p2.is_parity)
        self.assertTrue(any("Description mismatch" in d.message for d in p2.discrepancies))

        # 3. Tickers collection mismatch
        proj_diff_tickers = {
            "scout_category_records": [
                {"category_name": "TECH", "description": None, "tickers": ["AAPL", "NVDA"], "updated_at_utc": self.default_ts}
            ]
        }
        p3 = self.verifier.compare(legacy_none, proj_diff_tickers)
        self.assertFalse(p3.is_parity)
        self.assertTrue(any("Tickers mismatch" in d.message for d in p3.discrepancies))

    def test_trade_lessons_directive_and_review_ts_explicit_parity(self):
        """F2: Validates explicit comparison of trade lesson directive, category, status, and nullable review timestamp."""
        # 1. last_reviewed_at_utc None in legacy vs non-null in projected -> TIMESTAMP_MISMATCH
        legacy_none = {
            "trade_lessons": [
                {
                    "rule_code": "L-101",
                    "directive_text": "Do not chase",
                    "category": "MANDATE",
                    "status": "ACTIVE",
                    "created_at_utc": self.default_ts,
                    "last_reviewed_at_utc": None,
                }
            ]
        }
        proj_reviewed = {
            "trade_lessons": [
                {
                    "rule_code": "L-101",
                    "directive_text": "Do not chase",
                    "category": "MANDATE",
                    "status": "ACTIVE",
                    "created_at_utc": self.default_ts,
                    "last_reviewed_at_utc": "2026-11-09T00:00:00Z",
                }
            ]
        }
        p1 = self.verifier.compare(legacy_none, proj_reviewed)
        self.assertFalse(p1.is_parity)
        self.assertTrue(any("last_reviewed_at_utc" in d.path and d.discrepancy_type == DiscrepancyType.TIMESTAMP_MISMATCH.value for d in p1.discrepancies))

        # 2. last_reviewed_at_utc non-null in legacy vs None in projected -> TIMESTAMP_MISMATCH
        p2 = self.verifier.compare(proj_reviewed, legacy_none)
        self.assertFalse(p2.is_parity)
        self.assertTrue(any("last_reviewed_at_utc" in d.path and d.discrepancy_type == DiscrepancyType.TIMESTAMP_MISMATCH.value for d in p2.discrepancies))

        # 3. Directive text mismatch
        proj_diff_dir = {
            "trade_lessons": [
                {
                    "rule_code": "L-101",
                    "directive_text": "Always chase",
                    "category": "MANDATE",
                    "status": "ACTIVE",
                    "created_at_utc": self.default_ts,
                    "last_reviewed_at_utc": None,
                }
            ]
        }
        p3 = self.verifier.compare(legacy_none, proj_diff_dir)
        self.assertFalse(p3.is_parity)
        self.assertTrue(any("Directive text mismatch" in d.message for d in p3.discrepancies))

        # 4. Category mismatch
        proj_diff_cat = {
            "trade_lessons": [
                {
                    "rule_code": "L-101",
                    "directive_text": "Do not chase",
                    "category": "RISK",
                    "status": "ACTIVE",
                    "created_at_utc": self.default_ts,
                    "last_reviewed_at_utc": None,
                }
            ]
        }
        p4 = self.verifier.compare(legacy_none, proj_diff_cat)
        self.assertFalse(p4.is_parity)
        self.assertTrue(any("Category mismatch" in d.message for d in p4.discrepancies))

    def test_decision_log_transcript_and_votes_explicit_parity(self):
        """F2: Validates explicit comparison of decision log transcript_text, votes, action, and outcome."""
        # 1. transcript_text None in legacy vs string in projected -> FIELD_MISMATCH
        legacy_none = {
            "decision_log": [
                {
                    "session_id": "S1",
                    "ticker": "SPY",
                    "action_proposed": "HOLD",
                    "decision_outcome": "APPROVED",
                    "votes": {"bullish": "YES"},
                    "transcript_text": None,
                    "timestamp_utc": self.default_ts,
                }
            ]
        }
        proj_str = {
            "decision_log": [
                {
                    "session_id": "S1",
                    "ticker": "SPY",
                    "action_proposed": "HOLD",
                    "decision_outcome": "APPROVED",
                    "votes": {"bullish": "YES"},
                    "transcript_text": "Full debate transcript",
                    "timestamp_utc": self.default_ts,
                }
            ]
        }
        p1 = self.verifier.compare(legacy_none, proj_str)
        self.assertFalse(p1.is_parity)
        self.assertTrue(any("Transcript text mismatch" in d.message for d in p1.discrepancies))

        # 2. transcript_text "" in legacy vs None in projected -> FIELD_MISMATCH
        legacy_empty = {
            "decision_log": [
                {
                    "session_id": "S1",
                    "ticker": "SPY",
                    "action_proposed": "HOLD",
                    "decision_outcome": "APPROVED",
                    "votes": {"bullish": "YES"},
                    "transcript_text": "",
                    "timestamp_utc": self.default_ts,
                }
            ]
        }
        p2 = self.verifier.compare(legacy_empty, legacy_none)
        self.assertFalse(p2.is_parity)
        self.assertTrue(any("Transcript text mismatch" in d.message for d in p2.discrepancies))

        # 3. votes mismatch
        proj_diff_votes = {
            "decision_log": [
                {
                    "session_id": "S1",
                    "ticker": "SPY",
                    "action_proposed": "HOLD",
                    "decision_outcome": "APPROVED",
                    "votes": {"bullish": "NO"},
                    "transcript_text": None,
                    "timestamp_utc": self.default_ts,
                }
            ]
        }
        p3 = self.verifier.compare(legacy_none, proj_diff_votes)
        self.assertFalse(p3.is_parity)
        self.assertTrue(any("Votes mismatch" in d.message for d in p3.discrepancies))

    def test_bidirectional_parity_all_six_domains(self):
        """F2: Validates that unexpected projected records in ALL 6 domains are detected fail-closed."""
        empty_legacy = {}
        all_projected = {
            "portfolio_cash": [
                {"account_id": "PRIMARY", "currency": "EUR", "amount": "100.00", "last_updated_at_utc": self.default_ts}
            ],
            "portfolio_snapshot": [
                {"account_id": "PRIMARY", "ticker": "SPY", "shares": "10.0", "wac_eur": "400.00", "last_updated_at_utc": self.default_ts}
            ],
            "watched_tickers": ["NVDA"],
            "scout_category_records": [
                {"category_name": "GROWTH", "description": "High beta", "tickers": ["NVDA"], "updated_at_utc": self.default_ts}
            ],
            "trade_lessons": [
                {"rule_code": "L-100", "directive_text": "Risk check", "category": "RISK", "status": "ACTIVE", "created_at_utc": self.default_ts}
            ],
            "decision_log": [
                {"session_id": "S99", "ticker": "SPY", "action_proposed": "HOLD", "decision_outcome": "APPROVED", "timestamp_utc": self.default_ts}
            ],
        }

        res = self.verifier.compare(empty_legacy, all_projected)
        self.assertFalse(res.is_parity)
        self.assertEqual(res.summary.unexpected_records, 6)

        domains_detected = {d.domain for d in res.discrepancies if d.discrepancy_type == DiscrepancyType.UNEXPECTED_RECORD.value}
        self.assertEqual(domains_detected, {
            "portfolio_cash",
            "portfolio_positions",
            "watchlist",
            "scout_categories",
            "trade_lessons",
            "decision_log",
        })

    def test_scout_representations_consolidation_and_conflict_rejection(self):
        """F3: Validates consolidate_scout_representations handles identical sources and rejects conflicts."""
        # 1. Identical representations consolidate cleanly
        identical = {
            "scout_categories": [
                {"category_name": "TECH", "tickers": ["AAPL", "NVDA"], "updated_at_utc": self.default_ts}
            ],
            "scout_candidates": {"TECH": ["AAPL", "NVDA"]},
        }
        res = consolidate_scout_representations(identical, default_timestamp=self.default_ts)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["category_name"], "TECH")
        self.assertEqual(res[0]["tickers"], ["AAPL", "NVDA"])

        # 2. Conflicting tickers raise MigrationConflictError
        conflict_tickers = {
            "scout_categories": [
                {"category_name": "TECH", "tickers": ["AAPL"], "updated_at_utc": self.default_ts}
            ],
            "scout_candidates": {"TECH": ["NVDA"]},
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            consolidate_scout_representations(conflict_tickers, default_timestamp=self.default_ts)
        self.assertIn("Conflicting duplicate scout category record for TECH", str(ctx.exception))

        # 3. Conflicting descriptions raise MigrationConflictError
        conflict_desc = {
            "scout_categories": [
                {"category_name": "TECH", "description": "Desc A", "tickers": ["AAPL"], "updated_at_utc": self.default_ts},
                {"category_name": "TECH", "description": "Desc B", "tickers": ["AAPL"], "updated_at_utc": self.default_ts},
            ]
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            consolidate_scout_representations(conflict_desc, default_timestamp=self.default_ts)
        self.assertIn("Conflicting duplicate scout category record for TECH", str(ctx.exception))

        # 4. Conflicting timestamps raise MigrationConflictError
        conflict_ts = {
            "scout_categories": [
                {"category_name": "TECH", "tickers": ["AAPL"], "updated_at_utc": "2026-01-01T00:00:00Z"},
                {"category_name": "TECH", "tickers": ["AAPL"], "updated_at_utc": "2026-01-02T00:00:00Z"},
            ]
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            consolidate_scout_representations(conflict_ts, default_timestamp=self.default_ts)
        self.assertIn("Conflicting duplicate scout category record for TECH", str(ctx.exception))

    def test_watchlist_representations_consolidation_and_conflict_rejection(self):
        """F3: Validates consolidate_watchlist_representations handles identical sources and rejects conflicts."""
        # 1. Identical representations consolidate cleanly
        identical = {
            "watchlist": [
                {"ticker": "AAPL", "status": "ACTIVE", "notes": "Tech", "added_at_utc": self.default_ts}
            ],
            "watched_tickers": {
                "AAPL": {"status": "ACTIVE", "notes": "Tech", "added_at_utc": self.default_ts}
            },
        }
        res = consolidate_watchlist_representations(identical, default_timestamp=self.default_ts)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["ticker"], "AAPL")

        # 2. Conflicting status raises MigrationConflictError
        conflict_status = {
            "watchlist": [
                {"ticker": "AAPL", "status": "ACTIVE", "added_at_utc": self.default_ts}
            ],
            "watched_tickers": {
                "AAPL": {"status": "PAUSED", "added_at_utc": self.default_ts}
            },
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            consolidate_watchlist_representations(conflict_status, default_timestamp=self.default_ts)
        self.assertIn("Conflicting duplicate watchlist record for AAPL", str(ctx.exception))

        # 3. Conflicting notes raise MigrationConflictError
        conflict_notes = {
            "watchlist": [
                {"ticker": "AAPL", "notes": "Notes A", "added_at_utc": self.default_ts}
            ],
            "watched_tickers": {
                "AAPL": {"notes": "Notes B", "added_at_utc": self.default_ts}
            },
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            consolidate_watchlist_representations(conflict_notes, default_timestamp=self.default_ts)
        self.assertIn("Conflicting duplicate watchlist record for AAPL", str(ctx.exception))

        # 4. Conflicting timestamps raise MigrationConflictError
        conflict_ts = {
            "watchlist": [
                {"ticker": "AAPL", "added_at_utc": "2026-01-01T00:00:00Z"}
            ],
            "watched_tickers": {
                "AAPL": {"added_at_utc": "2026-01-02T00:00:00Z"}
            },
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            consolidate_watchlist_representations(conflict_ts, default_timestamp=self.default_ts)
        self.assertIn("Conflicting duplicate watchlist record for AAPL", str(ctx.exception))

    def test_position_representations_consolidation_and_conflict_rejection(self):
        """F3: Validates consolidate_position_representations handles identical sources and rejects conflicts."""
        # 1. Identical representations consolidate cleanly
        identical = {
            "positions": [
                {"account_id": "PRIMARY", "ticker": "AAPL", "shares": "10.0", "wac": "150.00", "last_updated_at_utc": self.default_ts}
            ],
            "mutable_state": {
                "portfolio_snapshot": [
                    {"account_id": "PRIMARY", "ticker": "AAPL", "shares": "10.0", "wac": "150.00", "last_updated_at_utc": self.default_ts}
                ]
            }
        }
        res = consolidate_position_representations(identical, default_account_id="PRIMARY", default_timestamp=self.default_ts)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["ticker"], "AAPL")
        self.assertEqual(res[0]["shares"], Decimal("10.0"))

        # 2. Conflicting shares raise MigrationConflictError
        conflict_shares = {
            "positions": [
                {"account_id": "PRIMARY", "ticker": "AAPL", "shares": "10.0", "wac": "150.00", "last_updated_at_utc": self.default_ts}
            ],
            "mutable_state": {
                "portfolio_snapshot": [
                    {"account_id": "PRIMARY", "ticker": "AAPL", "shares": "15.0", "wac": "150.00", "last_updated_at_utc": self.default_ts}
                ]
            }
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            consolidate_position_representations(conflict_shares, default_account_id="PRIMARY", default_timestamp=self.default_ts)
        self.assertIn("Conflicting duplicate position record for PRIMARY/AAPL", str(ctx.exception))

        # 3. Conflicting WAC EUR raises MigrationConflictError
        conflict_wac = {
            "positions": [
                {"account_id": "PRIMARY", "ticker": "AAPL", "shares": "10.0", "wac": "150.00", "last_updated_at_utc": self.default_ts}
            ],
            "mutable_state": {
                "portfolio_snapshot": [
                    {"account_id": "PRIMARY", "ticker": "AAPL", "shares": "10.0", "wac": "160.00", "last_updated_at_utc": self.default_ts}
                ]
            }
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            consolidate_position_representations(conflict_wac, default_account_id="PRIMARY", default_timestamp=self.default_ts)
        self.assertIn("Conflicting duplicate position record for PRIMARY/AAPL", str(ctx.exception))

        # 4. Conflicting timestamps raise MigrationConflictError
        conflict_ts = {
            "positions": [
                {"account_id": "PRIMARY", "ticker": "AAPL", "shares": "10.0", "wac": "150.00", "last_updated_at_utc": "2026-01-01T00:00:00Z"}
            ],
            "mutable_state": {
                "portfolio_snapshot": [
                    {"account_id": "PRIMARY", "ticker": "AAPL", "shares": "10.0", "wac": "150.00", "last_updated_at_utc": "2026-01-02T00:00:00Z"}
                ]
            }
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            consolidate_position_representations(conflict_ts, default_account_id="PRIMARY", default_timestamp=self.default_ts)
        self.assertIn("Conflicting duplicate position record for PRIMARY/AAPL", str(ctx.exception))

    def test_cash_representations_consolidation_and_conflict_rejection(self):
        """F3: Validates consolidate_cash_representations handles identical sources and rejects conflicts."""
        # 1. Identical scalar and structured cash consolidate cleanly
        identical = {
            "unallocated_cash_eur": "5000.00",
            "portfolio_cash": [
                {"account_id": "PRIMARY", "currency": "EUR", "amount": "5000.00", "last_updated_at_utc": self.default_ts}
            ],
        }
        res = consolidate_cash_representations(identical, default_account_id="PRIMARY", default_timestamp=self.default_ts)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["account_id"], "PRIMARY")
        self.assertEqual(res[0]["currency"], "EUR")
        self.assertEqual(res[0]["amount"], Decimal("5000.00"))

        # 2. Conflicting amount raises MigrationConflictError
        conflict_amt = {
            "unallocated_cash_eur": "5000.00",
            "portfolio_cash": [
                {"account_id": "PRIMARY", "currency": "EUR", "amount": "6000.00", "last_updated_at_utc": self.default_ts}
            ],
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            consolidate_cash_representations(conflict_amt, default_account_id="PRIMARY", default_timestamp=self.default_ts)
        self.assertIn("Conflicting duplicate cash record for PRIMARY/EUR", str(ctx.exception))

        # 3. Conflicting timestamps raise MigrationConflictError
        conflict_ts = {
            "portfolio_cash": [
                {"account_id": "PRIMARY", "currency": "EUR", "amount": "5000.00", "last_updated_at_utc": "2026-01-01T00:00:00Z"},
                {"account_id": "PRIMARY", "currency": "EUR", "amount": "5000.00", "last_updated_at_utc": "2026-01-02T00:00:00Z"},
            ]
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            consolidate_cash_representations(conflict_ts, default_account_id="PRIMARY", default_timestamp=self.default_ts)
        self.assertIn("Conflicting duplicate cash record for PRIMARY/EUR", str(ctx.exception))

    def test_consolidation_invariance_to_representation_order(self):
        """F3: Validates consolidation produces deterministic output invariant to key/representation order."""
        source_order_a = {
            "scout_categories": [
                {"category_name": "A", "tickers": ["T1"], "updated_at_utc": self.default_ts},
                {"category_name": "B", "tickers": ["T2"], "updated_at_utc": self.default_ts},
            ],
            "scout_candidates": {
                "A": ["T1"],
                "B": ["T2"],
            },
        }
        source_order_b = {
            "scout_candidates": {
                "B": ["T2"],
                "A": ["T1"],
            },
            "scout_categories": [
                {"category_name": "B", "tickers": ["T2"], "updated_at_utc": self.default_ts},
                {"category_name": "A", "tickers": ["T1"], "updated_at_utc": self.default_ts},
            ],
        }

        res_a = consolidate_scout_representations(source_order_a, default_timestamp=self.default_ts)
        res_b = consolidate_scout_representations(source_order_b, default_timestamp=self.default_ts)

        self.assertEqual(len(res_a), len(res_b))
        for item_a, item_b in zip(res_a, res_b):
            self.assertEqual(item_a["category_name"], item_b["category_name"])
            self.assertEqual(item_a["tickers"], item_b["tickers"])
            self.assertEqual(item_a["timestamp_utc"], item_b["timestamp_utc"])


class TestStage15D1121CorrectiveRegressions(BaseMigrationTestCase):
    """
    Stage 1.5-D.1.1.2.1 Regression Suite for Findings F2 and F3:
    - F2: Elimination of remaining truthiness-based parity comparisons.
    - F3: Complementary representation consolidation, order invariance, and conflict detection.
    """

    def test_f2_trade_lessons_truthiness_elimination_parity(self):
        """F2: Verifies trade lessons category and status parity distinguishes explicit empty/None from missing."""
        base_proj = {
            "trade_lessons": [
                {
                    "rule_code": "L-901",
                    "directive_text": "Always verify bounds",
                    "category": "MANDATE",
                    "status": "ACTIVE",
                    "created_at_utc": self.default_ts,
                    "last_reviewed_at_utc": None,
                }
            ]
        }

        # 1. Missing category in legacy defaults to "MANDATE" and matches projected
        legacy_missing_cat = {
            "trade_lessons": [
                {
                    "rule_code": "L-901",
                    "directive_text": "Always verify bounds",
                    "created_at_utc": self.default_ts,
                }
            ]
        }
        res1 = self.verifier.compare(legacy_missing_cat, base_proj)
        self.assertTrue(res1.is_parity)

        # 2. Explicit empty string category does NOT default to "MANDATE" and flags discrepancy
        legacy_empty_cat = {
            "trade_lessons": [
                {
                    "rule_code": "L-901",
                    "directive_text": "Always verify bounds",
                    "category": "",
                    "status": "ACTIVE",
                    "created_at_utc": self.default_ts,
                }
            ]
        }
        res2 = self.verifier.compare(legacy_empty_cat, base_proj)
        self.assertFalse(res2.is_parity)
        cat_discs = [d for d in res2.discrepancies if d.path == "trade_lessons[L-901].category"]
        self.assertEqual(len(cat_discs), 1)
        self.assertIn("Category mismatch", cat_discs[0].message)

        # 3. Explicit None category flags discrepancy
        legacy_none_cat = {
            "trade_lessons": [
                {
                    "rule_code": "L-901",
                    "directive_text": "Always verify bounds",
                    "category": None,
                    "status": "ACTIVE",
                    "created_at_utc": self.default_ts,
                }
            ]
        }
        res3 = self.verifier.compare(legacy_none_cat, base_proj)
        self.assertFalse(res3.is_parity)
        self.assertTrue(any(d.path == "trade_lessons[L-901].category" for d in res3.discrepancies))

        # 4. Explicit empty string status does NOT default to "ACTIVE" and flags discrepancy
        legacy_empty_status = {
            "trade_lessons": [
                {
                    "rule_code": "L-901",
                    "directive_text": "Always verify bounds",
                    "category": "MANDATE",
                    "status": "",
                    "created_at_utc": self.default_ts,
                }
            ]
        }
        res4 = self.verifier.compare(legacy_empty_status, base_proj)
        self.assertFalse(res4.is_parity)
        status_discs = [d for d in res4.discrepancies if d.path == "trade_lessons[L-901].status"]
        self.assertEqual(len(status_discs), 1)
        self.assertIn("Status mismatch", status_discs[0].message)

        # 5. Explicit None status flags discrepancy
        legacy_none_status = {
            "trade_lessons": [
                {
                    "rule_code": "L-901",
                    "directive_text": "Always verify bounds",
                    "category": "MANDATE",
                    "status": None,
                    "created_at_utc": self.default_ts,
                }
            ]
        }
        res5 = self.verifier.compare(legacy_none_status, base_proj)
        self.assertFalse(res5.is_parity)
        self.assertTrue(any(d.path == "trade_lessons[L-901].status" for d in res5.discrepancies))

    def test_f2_watchlist_status_truthiness_elimination_parity(self):
        """F2: Verifies watchlist status parity distinguishes explicit empty/None from missing."""
        base_proj = {
            "watched_tickers": ["AAPL"],
            "watchlist": [
                {
                    "ticker": "AAPL",
                    "status": "ACTIVE",
                    "notes": None,
                    "added_at_utc": self.default_ts,
                }
            ],
        }

        # 1. Missing status in legacy defaults to "ACTIVE" and matches projected
        legacy_missing = {
            "watchlist": [
                {
                    "ticker": "AAPL",
                    "added_at_utc": self.default_ts,
                }
            ]
        }
        res1 = self.verifier.compare(legacy_missing, base_proj)
        self.assertTrue(res1.is_parity)

        # 2. Explicit empty string status does NOT default to "ACTIVE" and flags discrepancy
        legacy_empty = {
            "watchlist": [
                {
                    "ticker": "AAPL",
                    "status": "",
                    "added_at_utc": self.default_ts,
                }
            ]
        }
        res2 = self.verifier.compare(legacy_empty, base_proj)
        self.assertFalse(res2.is_parity)
        status_discs = [d for d in res2.discrepancies if d.path == "watchlist[AAPL].status"]
        self.assertEqual(len(status_discs), 1)
        self.assertIn("Status mismatch", status_discs[0].message)

        # 3. Explicit None status flags discrepancy
        legacy_none = {
            "watchlist": [
                {
                    "ticker": "AAPL",
                    "status": None,
                    "added_at_utc": self.default_ts,
                }
            ]
        }
        res3 = self.verifier.compare(legacy_none, base_proj)
        self.assertFalse(res3.is_parity)
        self.assertTrue(any(d.path == "watchlist[AAPL].status" for d in res3.discrepancies))

    def test_f2_decision_log_votes_truthiness_elimination_parity(self):
        """F2: Verifies decision log votes parity distinguishes missing from explicit None/dict."""
        base_proj = {
            "decision_log": [
                {
                    "session_id": "S-100",
                    "action_proposed": "BUY",
                    "decision_outcome": "APPROVED",
                    "ticker": "AAPL",
                    "votes": {},
                    "transcript_text": "Debate transcript",
                    "timestamp_utc": self.default_ts,
                }
            ]
        }

        # 1. Missing votes in legacy defaults to {} and matches projected {}
        legacy_missing = {
            "decision_log": [
                {
                    "session_id": "S-100",
                    "action_proposed": "BUY",
                    "decision_outcome": "APPROVED",
                    "ticker": "AAPL",
                    "transcript_text": "Debate transcript",
                    "timestamp_utc": self.default_ts,
                }
            ]
        }
        res1 = self.verifier.compare(legacy_missing, base_proj)
        self.assertTrue(res1.is_parity)

        # 2. Explicit None votes does NOT collapse to {} and flags discrepancy
        legacy_none = {
            "decision_log": [
                {
                    "session_id": "S-100",
                    "action_proposed": "BUY",
                    "decision_outcome": "APPROVED",
                    "ticker": "AAPL",
                    "votes": None,
                    "transcript_text": "Debate transcript",
                    "timestamp_utc": self.default_ts,
                }
            ]
        }
        res2 = self.verifier.compare(legacy_none, base_proj)
        self.assertFalse(res2.is_parity)
        vote_discs = [d for d in res2.discrepancies if d.path == "decision_log[S-100/BUY/AAPL].votes"]
        self.assertEqual(len(vote_discs), 1)

        # 3. Differing votes dict flags discrepancy
        legacy_diff = {
            "decision_log": [
                {
                    "session_id": "S-100",
                    "action_proposed": "BUY",
                    "decision_outcome": "APPROVED",
                    "ticker": "AAPL",
                    "votes": {"bull": "YES"},
                    "transcript_text": "Debate transcript",
                    "timestamp_utc": self.default_ts,
                }
            ]
        }
        res3 = self.verifier.compare(legacy_diff, base_proj)
        self.assertFalse(res3.is_parity)
        self.assertTrue(any(d.path == "decision_log[S-100/BUY/AAPL].votes" for d in res3.discrepancies))

    def test_f3_scout_complementary_consolidation_and_order_invariance(self):
        """F3: Verifies scout representations consolidate complementary data order-invariantly and reject conflicts."""
        # Representation A: SCOUT_TICKER_MAP provides tickers, no description
        # Representation B: scout_categories provides description and matching tickers
        source_order_ab = {
            "SCOUT_TICKER_MAP": {"TECH": ["AAPL", "NVDA"]},
            "scout_categories": [
                {"category_name": "TECH", "description": "Technology sector", "tickers": ["AAPL", "NVDA"], "updated_at_utc": self.default_ts}
            ],
        }
        source_order_ba = {
            "scout_categories": [
                {"category_name": "TECH", "description": "Technology sector", "tickers": ["AAPL", "NVDA"], "updated_at_utc": self.default_ts}
            ],
            "SCOUT_TICKER_MAP": {"TECH": ["AAPL", "NVDA"]},
        }

        res_ab = consolidate_scout_representations(source_order_ab, default_timestamp=self.default_ts)
        res_ba = consolidate_scout_representations(source_order_ba, default_timestamp=self.default_ts)

        self.assertEqual(len(res_ab), 1)
        self.assertEqual(len(res_ba), 1)
        self.assertEqual(res_ab[0]["category_name"], "TECH")
        self.assertEqual(res_ab[0]["description"], "Technology sector")
        self.assertEqual(res_ab[0]["tickers"], ["AAPL", "NVDA"])
        self.assertEqual(res_ab, res_ba)

        # Conflicting descriptions between representations raise MigrationConflictError
        conflict_desc = {
            "scout_categories": [
                {"category_name": "TECH", "description": "Tech Leaders", "tickers": ["AAPL"], "updated_at_utc": self.default_ts},
                {"category_name": "TECH", "description": "Tech Infrastructure", "tickers": ["AAPL"], "updated_at_utc": self.default_ts},
            ]
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            consolidate_scout_representations(conflict_desc, default_timestamp=self.default_ts)
        self.assertIn("Conflicting duplicate scout category record for TECH", str(ctx.exception))

    def test_f3_watchlist_complementary_consolidation_and_order_invariance(self):
        """F3: Verifies watchlist representations consolidate complementary data order-invariantly and reject conflicts."""
        # Representation A: watched_tickers plain string list (status and notes unspecified)
        # Representation B: watchlist structured dict providing notes and active status
        source_order_ab = {
            "watched_tickers": ["AAPL"],
            "watchlist": [
                {"ticker": "AAPL", "status": "ACTIVE", "notes": "Core tech holding", "added_at_utc": self.default_ts}
            ],
        }
        source_order_ba = {
            "watchlist": [
                {"ticker": "AAPL", "status": "ACTIVE", "notes": "Core tech holding", "added_at_utc": self.default_ts}
            ],
            "watched_tickers": ["AAPL"],
        }

        res_ab = consolidate_watchlist_representations(source_order_ab, default_timestamp=self.default_ts)
        res_ba = consolidate_watchlist_representations(source_order_ba, default_timestamp=self.default_ts)

        self.assertEqual(len(res_ab), 1)
        self.assertEqual(len(res_ba), 1)
        self.assertEqual(res_ab[0]["ticker"], "AAPL")
        self.assertEqual(res_ab[0]["status"], "ACTIVE")
        self.assertEqual(res_ab[0]["notes"], "Core tech holding")
        self.assertEqual(res_ab, res_ba)

        # Conflicting status between representations raises MigrationConflictError
        conflict_status = {
            "watchlist": [
                {"ticker": "AAPL", "status": "ACTIVE", "added_at_utc": self.default_ts}
            ],
            "watched_tickers": {
                "AAPL": {"status": "PAUSED", "added_at_utc": self.default_ts}
            },
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            consolidate_watchlist_representations(conflict_status, default_timestamp=self.default_ts)
        self.assertIn("Conflicting duplicate watchlist record for AAPL", str(ctx.exception))

    def test_f3_position_complementary_consolidation_and_order_invariance(self):
        """F3: Verifies position representations consolidate complementary wac_usd order-invariantly and reject conflicts."""
        # Representation A: positions without wac_usd
        # Representation B: portfolio_positions with wac_usd
        source_order_ab = {
            "positions": [
                {"account_id": "PRIMARY", "ticker": "MSFT", "shares": "50", "wac_eur": "300.00", "last_updated_at_utc": self.default_ts}
            ],
            "portfolio_positions": [
                {"account_id": "PRIMARY", "ticker": "MSFT", "shares": "50", "wac_eur": "300.00", "wac_usd": "330.00", "last_updated_at_utc": self.default_ts}
            ],
        }
        source_order_ba = {
            "portfolio_positions": [
                {"account_id": "PRIMARY", "ticker": "MSFT", "shares": "50", "wac_eur": "300.00", "wac_usd": "330.00", "last_updated_at_utc": self.default_ts}
            ],
            "positions": [
                {"account_id": "PRIMARY", "ticker": "MSFT", "shares": "50", "wac_eur": "300.00", "last_updated_at_utc": self.default_ts}
            ],
        }

        res_ab = consolidate_position_representations(source_order_ab, default_timestamp=self.default_ts)
        res_ba = consolidate_position_representations(source_order_ba, default_timestamp=self.default_ts)

        self.assertEqual(len(res_ab), 1)
        self.assertEqual(len(res_ba), 1)
        self.assertEqual(res_ab[0]["ticker"], "MSFT")
        self.assertEqual(res_ab[0]["shares"], Decimal("50"))
        self.assertEqual(res_ab[0]["wac_eur"], Decimal("300.00"))
        self.assertEqual(res_ab[0]["wac_usd"], Decimal("330.00"))
        self.assertEqual(res_ab, res_ba)

        # Conflicting wac_usd raises MigrationConflictError
        conflict_wac = {
            "portfolio_positions": [
                {"account_id": "PRIMARY", "ticker": "MSFT", "shares": "50", "wac_eur": "300.00", "wac_usd": "330.00", "last_updated_at_utc": self.default_ts},
                {"account_id": "PRIMARY", "ticker": "MSFT", "shares": "50", "wac_eur": "300.00", "wac_usd": "340.00", "last_updated_at_utc": self.default_ts},
            ]
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            consolidate_position_representations(conflict_wac, default_timestamp=self.default_ts)
        self.assertIn("Conflicting duplicate position record for PRIMARY/MSFT", str(ctx.exception))

    def test_f3_cash_complementary_consolidation_and_order_invariance(self):
        """F3: Verifies scalar cash (no ts) and structured cash (with ts) consolidate cleanly and order-invariantly."""
        source_order_ab = {
            "unallocated_cash_eur": "7500.00",
            "portfolio_cash": [
                {"account_id": "PRIMARY", "currency": "EUR", "amount": "7500.00", "last_updated_at_utc": self.default_ts}
            ],
        }
        source_order_ba = {
            "portfolio_cash": [
                {"account_id": "PRIMARY", "currency": "EUR", "amount": "7500.00", "last_updated_at_utc": self.default_ts}
            ],
            "unallocated_cash_eur": "7500.00",
        }

        res_ab = consolidate_cash_representations(source_order_ab, default_account_id="PRIMARY", default_timestamp=self.default_ts)
        res_ba = consolidate_cash_representations(source_order_ba, default_account_id="PRIMARY", default_timestamp=self.default_ts)

        self.assertEqual(len(res_ab), 1)
        self.assertEqual(len(res_ba), 1)
        self.assertEqual(res_ab[0]["amount"], Decimal("7500.00"))
        self.assertEqual(res_ab[0]["timestamp_utc"], self.default_ts)
        self.assertEqual(res_ab, res_ba)

        # Conflicting amount raises MigrationConflictError
        conflict_amt = {
            "unallocated_cash_eur": "7500.00",
            "portfolio_cash": [
                {"account_id": "PRIMARY", "currency": "EUR", "amount": "8000.00", "last_updated_at_utc": self.default_ts}
            ],
        }
        with self.assertRaises(MigrationConflictError) as ctx:
            consolidate_cash_representations(conflict_amt, default_account_id="PRIMARY", default_timestamp=self.default_ts)
        self.assertIn("Conflicting duplicate cash record for PRIMARY/EUR", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
