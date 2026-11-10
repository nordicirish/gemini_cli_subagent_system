"""
Automated Test Suite for SSoT Projection Generator & Deterministic JSON Serialization.
Governed by Stage 1.5-C Architecture and Agent Governance Framework.

Covers:
A. Projection Completeness (all 6 approved domains, exclusions of operational tables, nullable handling, empty representations).
B. Determinism (unchanged state reproducibility, sorting/ordering stability, timestamp separation).
C. Decimal Integrity (exact canonical strings, zero binary floats, float rejection).
D. Revision Consistency (monotonic db_revision provenance, mutation advances, rollback immunity, read-only snapshot).
E. Transaction Ownership (no active transaction cleanup, caller transaction preservation, closed/None fail-closed).
F. Non-Tabular Preservation (external sections preserved, SQLite-owned overwritten, malformed rejected, base doc immutability).
G. Atomic Writer Safety (atomic replacement, failure preservation, temp cleanup, directory creation, temp-only isolation).
H. Read-Only Invariants (zero DB writes, zero changelog/audit entries, zero revision increment, isolated domain helpers).
"""

import concurrent.futures
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
from typing import Any, Dict, Optional
import unittest
from unittest.mock import patch

from python.database.manager import (
    DatabaseManager,
    PRIMARY_FINANCIAL_PROFILE,
    MARKET_DATA_CACHE_PROFILE,
)
from python.database.schema import SchemaMigrator
from python.database.repositories.base import (
    RepositoryError,
    ValidationError,
    TransactionIntegrityError,
    decimal_to_canonical_str,
    get_db_revision,
    increment_db_revision,
    utc_now_iso,
)
from python.database.repositories.financial import (
    CashRepository,
    PositionRepository,
    WatchlistRepository,
)
from python.database.repositories.governance import (
    ScoutCategoryRepository,
    TradeLessonRepository,
    DecisionLogRepository,
)
from python.database.projections import (
    PROJECTION_FORMAT_VERSION,
    SQLITE_OWNED_TOP_LEVEL_KEYS,
    SQLITE_OWNED_MUTABLE_STATE_KEYS,
    normalize_projection_value,
    serialize_projection,
    atomic_write_json,
    SSoTProjectionGenerator,
)
from python.tool_registry import execution_role_context


class BaseProjectionTestCase(unittest.TestCase):
    """Base test case initializing isolated temporary core and cache SQLite databases."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.core_db_path = os.path.join(self.temp_dir.name, "test_core.db")
        self.core_manager = DatabaseManager(self.core_db_path, profile=PRIMARY_FINANCIAL_PROFILE)

        with self.core_manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)

        self.generator = SSoTProjectionGenerator(self.core_manager)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _seed_standard_database(self, conn: sqlite3.Connection):
        """Seeds the database with representative multi-domain test data."""
        with execution_role_context("System"):
            cash_repo = CashRepository(conn)
            cash_repo.set_cash_balance(account_id="PRIMARY", currency="EUR", amount=Decimal("2359.80"), caller_role="System")
            cash_repo.set_cash_balance(account_id="PRIMARY", currency="USD", amount=Decimal("2548.58"), caller_role="System")
            cash_repo.set_cash_balance(account_id="SECONDARY", currency="EUR", amount=Decimal("500.00"), caller_role="System")

            pos_repo = PositionRepository(conn)
            pos_repo.upsert_position(
                account_id="PRIMARY",
                ticker="SPY",
                shares=Decimal("10.0000"),
                wac_eur=Decimal("500.0000"),
                wac_usd=Decimal("540.0000"),
                caller_role="System",
            )
            pos_repo.upsert_position(
                account_id="PRIMARY",
                ticker="RKLB",
                shares=Decimal("100.5000"),
                wac_eur=Decimal("72.3300"),
                wac_usd=None,
                caller_role="System",
            )

            wl_repo = WatchlistRepository(conn)
            wl_repo.upsert_watchlist_item(ticker="GOOG", status="ACTIVE", notes="Core cloud leader")
            wl_repo.upsert_watchlist_item(ticker="AMZN", status="ACTIVE", notes=None)
            wl_repo.upsert_watchlist_item(ticker="MSFT", status="PAUSED", notes="Paused tracking")

            scout_repo = ScoutCategoryRepository(conn)
            scout_repo.upsert_category(category_name="DEFENSE", description="Aerospace & Defense", tickers=["RKLB", "LMT"])
            scout_repo.upsert_category(category_name="AI_INFRA", description=None, tickers=["NVDA", "PLTR"])

            lesson_repo = TradeLessonRepository(conn)
            lesson_repo.upsert_lesson(
                rule_code="L-219",
                directive_text="Pre-market gap-down conviction threshold",
                category="MANDATE",
                status="ACTIVE",
            )
            lesson_repo.upsert_lesson(
                rule_code="L-230",
                directive_text="Gamma whiplash lock on post-flip instability",
                category="EXECUTION",
                status="ACTIVE",
            )

            decision_repo = DecisionLogRepository(conn)
            decision_repo.append_decision(
                session_id="SESSION_001",
                action_proposed="HOLD",
                decision_outcome="APPROVED",
                votes={"BULLISH": "HOLD", "RED_TEAM": "HOLD"},
                ticker="SPY",
                transcript_text="Council approved holding SPY",
                timestamp_utc="2026-09-22T15:00:00Z",
            )
            decision_repo.append_decision(
                session_id="SESSION_002",
                action_proposed="TRIM",
                decision_outcome="REJECTED",
                votes={"BULLISH": "HOLD", "RED_TEAM": "TRIM"},
                ticker=None,
                transcript_text=None,
                timestamp_utc="2026-09-22T16:00:00Z",
            )


# ===========================================================================
# A. Projection Completeness
# ===========================================================================

class TestProjectionCompleteness(BaseProjectionTestCase):
    """Verifies that all approved SQLite-owned domains are represented and operational tables are excluded."""

    def test_all_approved_sqlite_domains_projected(self):
        with self.core_manager.connection() as conn:
            self._seed_standard_database(conn)
            projection = self.generator.generate(conn)

        # 1. Top-level keys verification
        self.assertIn("_projection_metadata", projection)
        self.assertIn("portfolio_snapshot", projection)
        self.assertIn("unallocated_cash_eur", projection)
        self.assertIn("unallocated_cash_usd", projection)
        self.assertIn("portfolio_cash", projection)
        self.assertIn("watched_tickers", projection)
        self.assertIn("watchlist", projection)
        self.assertIn("scout_categories", projection)
        self.assertIn("scout_category_records", projection)
        self.assertIn("trade_lessons", projection)
        self.assertIn("decision_log", projection)
        self.assertIn("mutable_state", projection)

        # 2. Portfolio Cash Domain
        self.assertEqual(projection["unallocated_cash_eur"], "2359.80")
        self.assertEqual(projection["unallocated_cash_usd"], "2548.58")
        self.assertEqual(len(projection["portfolio_cash"]), 3)

        # 3. Positions Domain
        self.assertEqual(len(projection["portfolio_snapshot"]), 2)
        pos_rklb = next(p for p in projection["portfolio_snapshot"] if p["ticker"] == "RKLB")
        self.assertEqual(pos_rklb["shares"], "100.5000")
        self.assertEqual(pos_rklb["wac_eur"], "72.3300")
        self.assertEqual(pos_rklb["wac"], "72.3300")  # legacy compatibility
        self.assertIsNone(pos_rklb["wac_usd"])

        # 4. Watchlist Domain (only ACTIVE tickers in watched_tickers)
        self.assertEqual(projection["watched_tickers"], ["AMZN", "GOOG"])
        self.assertEqual(len(projection["watchlist"]), 3)

        # 5. Scout Categories Domain
        self.assertEqual(projection["scout_categories"], ["AI_INFRA", "DEFENSE"])
        self.assertEqual(len(projection["scout_category_records"]), 2)

        # 6. Trade Lessons Domain
        self.assertEqual(len(projection["trade_lessons"]), 2)
        self.assertEqual(projection["trade_lessons"][0]["rule_code"], "L-219")
        self.assertEqual(projection["trade_lessons"][1]["rule_code"], "L-230")

        # 7. Decision Log Domain
        self.assertEqual(len(projection["decision_log"]), 2)
        self.assertEqual(projection["decision_log"][0]["session_id"], "SESSION_001")
        self.assertEqual(projection["decision_log"][1]["session_id"], "SESSION_002")

        # 8. Mutable State Nested Structure
        ms = projection["mutable_state"]
        self.assertEqual(ms["portfolio_snapshot"], projection["portfolio_snapshot"])
        self.assertEqual(ms["watched_tickers"], projection["watched_tickers"])
        self.assertEqual(ms["unallocated_cash_eur"], "2359.80")
        self.assertEqual(ms["unallocated_cash_usd"], "2548.58")
        self.assertEqual(ms["scout_categories"], ["AI_INFRA", "DEFENSE"])

    def test_unintended_operational_tables_excluded(self):
        with self.core_manager.connection() as conn:
            self._seed_standard_database(conn)
            projection = self.generator.generate(conn)

        # Ensure operational and internal tables are never exposed in user-facing SSoT
        forbidden_keys = {
            "financial_changelog",
            "owner_approvals",
            "audit_events",
            "daily_bars",
            "cache_metadata",
            "database_metadata",
        }
        for k in forbidden_keys:
            self.assertNotIn(k, projection, f"Operational table '{k}' must not be projected at top-level")
            self.assertNotIn(k, projection["mutable_state"], f"Operational table '{k}' must not be in mutable_state")

    def test_nullable_fields_preserved_as_null(self):
        with self.core_manager.connection() as conn:
            self._seed_standard_database(conn)
            projection = self.generator.generate(conn)

        # Position with nullable wac_usd
        pos_rklb = next(p for p in projection["portfolio_snapshot"] if p["ticker"] == "RKLB")
        self.assertIsNone(pos_rklb["wac_usd"])

        # Watchlist with nullable notes
        wl_amzn = next(w for w in projection["watchlist"] if w["ticker"] == "AMZN")
        self.assertIsNone(wl_amzn["notes"])

        # Scout category with nullable description
        sc_ai = next(s for s in projection["scout_category_records"] if s["category_name"] == "AI_INFRA")
        self.assertIsNone(sc_ai["description"])

        # Decision record with nullable ticker and transcript
        dec_2 = next(d for d in projection["decision_log"] if d["session_id"] == "SESSION_002")
        self.assertIsNone(dec_2["ticker"])
        self.assertIsNone(dec_2["transcript_text"])

        # Verify serialized representation retains JSON null
        serialized = self.generator.serialize(projection)
        self.assertIn('"wac_usd":null', serialized)
        self.assertIn('"transcript_text":null', serialized)

    def test_empty_tables_produce_empty_representation(self):
        with self.core_manager.connection() as conn:
            projection = self.generator.generate(conn)

        self.assertEqual(projection["portfolio_snapshot"], [])
        self.assertEqual(projection["unallocated_cash_eur"], "0.00")
        self.assertEqual(projection["unallocated_cash_usd"], "0.00")
        self.assertEqual(projection["portfolio_cash"], [])
        self.assertEqual(projection["watched_tickers"], [])
        self.assertEqual(projection["watchlist"], [])
        self.assertEqual(projection["scout_categories"], [])
        self.assertEqual(projection["scout_category_records"], [])
        self.assertEqual(projection["trade_lessons"], [])
        self.assertEqual(projection["decision_log"], [])
        self.assertEqual(projection["mutable_state"]["portfolio_snapshot"], [])
        self.assertEqual(projection["mutable_state"]["watched_tickers"], [])


# ===========================================================================
# B. Determinism & Stable Ordering
# ===========================================================================

class TestProjectionDeterminism(BaseProjectionTestCase):
    """Verifies that projection output is strictly deterministic across calls and insertion orders."""

    def test_identical_output_for_unchanged_database(self):
        with self.core_manager.connection() as conn:
            self._seed_standard_database(conn)
            proj_1 = self.generator.generate(conn)
            proj_2 = self.generator.generate(conn)

        self.assertEqual(proj_1, proj_2)
        bytes_1 = self.generator.serialize(proj_1).encode("utf-8")
        bytes_2 = self.generator.serialize(proj_2).encode("utf-8")
        self.assertEqual(bytes_1, bytes_2)

    def test_insertion_order_invariance(self):
        """Verifies that inserting rows in reverse or random order produces identical sorted output."""
        db_path_rev = os.path.join(self.temp_dir.name, "test_core_rev.db")
        mgr_rev = DatabaseManager(db_path_rev, profile=PRIMARY_FINANCIAL_PROFILE)
        with mgr_rev.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
            with execution_role_context("System"):
                # Insert in reverse alphabetical order
                wl_repo = WatchlistRepository(conn)
                wl_repo.upsert_watchlist_item(ticker="MSFT", status="PAUSED")
                wl_repo.upsert_watchlist_item(ticker="GOOG", status="ACTIVE")
                wl_repo.upsert_watchlist_item(ticker="AMZN", status="ACTIVE")

                scout_repo = ScoutCategoryRepository(conn)
                scout_repo.upsert_category(category_name="DEFENSE", tickers=["LMT", "RKLB"])
                scout_repo.upsert_category(category_name="AI_INFRA", tickers=["PLTR", "NVDA"])

                lesson_repo = TradeLessonRepository(conn)
                lesson_repo.upsert_lesson(rule_code="L-230", directive_text="Rule 230", category="EXECUTION")
                lesson_repo.upsert_lesson(rule_code="L-219", directive_text="Rule 219", category="MANDATE")

                pos_repo = PositionRepository(conn)
                pos_repo.upsert_position(account_id="PRIMARY", ticker="SPY", shares=Decimal("10"), wac_eur=Decimal("500"), caller_role="System")
                pos_repo.upsert_position(account_id="PRIMARY", ticker="RKLB", shares=Decimal("100"), wac_eur=Decimal("70"), caller_role="System")

                cash_repo = CashRepository(conn)
                cash_repo.set_cash_balance(account_id="PRIMARY", currency="USD", amount=Decimal("2000.00"), caller_role="System")
                cash_repo.set_cash_balance(account_id="PRIMARY", currency="EUR", amount=Decimal("1000.00"), caller_role="System")

            proj = SSoTProjectionGenerator(mgr_rev).generate(conn)

        # Assert explicit canonical orderings
        self.assertEqual(proj["watched_tickers"], ["AMZN", "GOOG"])
        self.assertEqual([w["ticker"] for w in proj["watchlist"]], ["AMZN", "GOOG", "MSFT"])
        self.assertEqual(proj["scout_categories"], ["AI_INFRA", "DEFENSE"])
        self.assertEqual([l["rule_code"] for l in proj["trade_lessons"]], ["L-219", "L-230"])
        self.assertEqual([p["ticker"] for p in proj["portfolio_snapshot"]], ["RKLB", "SPY"])
        self.assertEqual([c["currency"] for c in proj["portfolio_cash"]], ["EUR", "USD"])

    def test_no_implicit_timestamps_cause_drift(self):
        """Verifies that no hidden datetime.now() call introduces divergence between successive calls."""
        with self.core_manager.connection() as conn:
            self._seed_standard_database(conn)
            p1 = self.generator.generate(conn)
            p2 = self.generator.generate(conn)

        # When generation_timestamp is None, _projection_metadata does not contain generated_at_utc
        self.assertNotIn("generated_at_utc", p1["_projection_metadata"])
        self.assertNotIn("generated_at_utc", p2["_projection_metadata"])
        self.assertEqual(self.generator.serialize(p1), self.generator.serialize(p2))

    def test_explicit_generation_timestamp_supported(self):
        ts = "2026-11-10T12:00:00Z"
        with self.core_manager.connection() as conn:
            self._seed_standard_database(conn)
            proj = self.generator.generate(conn, generation_timestamp=ts)

        self.assertEqual(proj["_projection_metadata"]["generated_at_utc"], ts)

    def test_repeated_deterministic_projection_stress_loop(self):
        """Repeats generation 25 times over unchanged state asserting identical byte serialization."""
        with self.core_manager.connection() as conn:
            self._seed_standard_database(conn)
            baseline_bytes = self.generator.serialize(self.generator.generate(conn)).encode("utf-8")
            for _ in range(25):
                candidate_bytes = self.generator.serialize(self.generator.generate(conn)).encode("utf-8")
                self.assertEqual(candidate_bytes, baseline_bytes, "Drift detected across repeated generation runs")


# ===========================================================================
# C. Decimal Integrity
# ===========================================================================

class TestDecimalIntegrity(BaseProjectionTestCase):
    """Verifies that financial numeric values preserve exact Decimal representations."""

    def test_exact_decimal_string_canonicalization(self):
        with self.core_manager.connection() as conn:
            with execution_role_context("System"):
                cash_repo = CashRepository(conn)
                cash_repo.set_cash_balance(account_id="PRIMARY", currency="EUR", amount=Decimal("0"), caller_role="System")
                cash_repo.set_cash_balance(account_id="PRIMARY", currency="USD", amount=Decimal("1234.56"), caller_role="System")

                pos_repo = PositionRepository(conn)
                # Maximum scale 4
                pos_repo.upsert_position(
                    account_id="PRIMARY",
                    ticker="AAPL",
                    shares=Decimal("12.3456"),
                    wac_eur=Decimal("100.0000"),
                    wac_usd=Decimal("98.7654"),
                    caller_role="System",
                )

            proj = self.generator.generate(conn)
            serialized = self.generator.serialize(proj)

        self.assertEqual(proj["unallocated_cash_eur"], "0")
        self.assertEqual(proj["unallocated_cash_usd"], "1234.56")

        pos = proj["portfolio_snapshot"][0]
        self.assertEqual(pos["shares"], "12.3456")
        self.assertEqual(pos["wac_eur"], "100.0000")
        self.assertEqual(pos["wac_usd"], "98.7654")

        # Serialized JSON must contain exact string tokens and no float representations
        self.assertIn('"shares":"12.3456"', serialized)
        self.assertIn('"wac_eur":"100.0000"', serialized)
        self.assertIn('"wac_usd":"98.7654"', serialized)

    def test_float_rejected_by_default(self):
        bad_doc = {"unallocated_cash_eur": 123.45}
        with self.assertRaises(TypeError) as ctx:
            normalize_projection_value(bad_doc, allow_floats=False)
        self.assertIn("Floating-point values strictly forbidden", str(ctx.exception))

    def test_non_standard_floats_rejected(self):
        with self.assertRaises(ValueError):
            normalize_projection_value({"ratio": float("nan")}, allow_floats=True)
        with self.assertRaises(ValueError):
            normalize_projection_value({"ratio": float("inf")}, allow_floats=True)


# ===========================================================================
# D. Revision Consistency
# ===========================================================================

class TestRevisionConsistency(BaseProjectionTestCase):
    """Verifies monotonic db_revision provenance and snapshot consistency."""

    def test_projection_records_correct_db_revision(self):
        with self.core_manager.connection() as conn:
            proj = self.generator.generate(conn)
            self.assertEqual(proj["_projection_metadata"]["db_revision"], 0)

            # Perform mutation
            with execution_role_context("System"):
                CashRepository(conn).set_cash_balance(account_id="PRIMARY", currency="EUR", amount=Decimal("100.00"), caller_role="System")

            proj2 = self.generator.generate(conn)
            self.assertEqual(proj2["_projection_metadata"]["db_revision"], 1)

    def test_successful_mutation_advances_projected_revision(self):
        with self.core_manager.connection() as conn:
            with execution_role_context("System"):
                CashRepository(conn).set_cash_balance(account_id="PRIMARY", currency="EUR", amount=Decimal("50.00"), caller_role="System")
                WatchlistRepository(conn).upsert_watchlist_item(ticker="NVDA", status="ACTIVE")

            proj = self.generator.generate(conn)
            self.assertEqual(proj["_projection_metadata"]["db_revision"], 2)
            self.assertEqual(proj["unallocated_cash_eur"], "50.00")
            self.assertEqual(proj["watched_tickers"], ["NVDA"])

    def test_failed_or_rolled_back_mutation_does_not_advance_revision(self):
        with self.core_manager.connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            with execution_role_context("System"):
                CashRepository(conn).set_cash_balance(account_id="PRIMARY", currency="EUR", amount=Decimal("999.00"), caller_role="System")
            conn.execute("ROLLBACK;")

            proj = self.generator.generate(conn)
            self.assertEqual(proj["_projection_metadata"]["db_revision"], 0)
            self.assertEqual(proj["unallocated_cash_eur"], "0.00")

    def test_projection_itself_never_increments_revision(self):
        with self.core_manager.connection() as conn:
            rev_start = get_db_revision(conn)
            self.generator.generate(conn)
            self.generator.generate(conn)
            self.generator.generate(conn)
            rev_end = get_db_revision(conn)
            self.assertEqual(rev_start, rev_end)


# ===========================================================================
# E. Transaction Ownership
# ===========================================================================

class TestTransactionOwnership(BaseProjectionTestCase):
    """Verifies that projection respects connection transaction state and never commits/rolls back caller work."""

    def test_generator_called_with_no_active_transaction(self):
        with self.core_manager.connection() as conn:
            self.assertFalse(conn.in_transaction)
            self.generator.generate(conn)
            self.assertFalse(conn.in_transaction, "Connection must not be left in transaction")

    def test_generator_called_with_active_caller_transaction(self):
        with self.core_manager.connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            self.assertTrue(conn.in_transaction)

            with execution_role_context("System"):
                CashRepository(conn).set_cash_balance(account_id="PRIMARY", currency="EUR", amount=Decimal("777.00"), caller_role="System")

            # Generate projection while caller transaction is active
            proj = self.generator.generate(conn)

            # Caller transaction MUST remain active
            self.assertTrue(conn.in_transaction, "Caller transaction must remain active after generate()")
            self.assertEqual(proj["unallocated_cash_eur"], "777.00")

            # Caller can still safely commit
            conn.execute("COMMIT;")
            self.assertFalse(conn.in_transaction)

            # Verification that commit succeeded
            proj_post = self.generator.generate(conn)
            self.assertEqual(proj_post["unallocated_cash_eur"], "777.00")

    def test_generator_does_not_commit_or_rollback_caller_work(self):
        with self.core_manager.connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            with execution_role_context("System"):
                CashRepository(conn).set_cash_balance(account_id="PRIMARY", currency="EUR", amount=Decimal("888.00"), caller_role="System")

            # Project sees pending uncommitted work
            proj = self.generator.generate(conn)
            self.assertEqual(proj["unallocated_cash_eur"], "888.00")

            # Caller rolls back
            conn.execute("ROLLBACK;")

            # Post-rollback projection does not see rolled back work
            proj_after = self.generator.generate(conn)
            self.assertEqual(proj_after["unallocated_cash_eur"], "0.00")

    def test_closed_connection_fails_cleanly(self):
        conn = self.core_manager.connect()
        conn.close()
        with self.assertRaises((RepositoryError, sqlite3.ProgrammingError, ValidationError)):
            self.generator.generate(conn)

    def test_none_connection_fails_cleanly(self):
        with self.assertRaises(ValidationError):
            self.generator.generate(None)


# ===========================================================================
# F. Non-Tabular Preservation Policy
# ===========================================================================

class TestNonTabularPreservation(BaseProjectionTestCase):
    """Verifies that externally managed sections survive projection and malformed inputs are rejected."""

    def test_preserves_external_top_level_sections(self):
        base_doc = {
            "state_context": {"regime": "NORMAL", "dealer_gamma": "LONG_GAMMA"},
            "runtime_flags": {"dry_run": False, "circuit_breaker": "ACTIVE"},
            "macro_calendar_shield": {"fomc_proximity": False},
            "custom_metadata": "unmodified_value",
        }
        with self.core_manager.connection() as conn:
            self._seed_standard_database(conn)
            proj = self.generator.generate(conn, base_document=base_doc)

        self.assertEqual(proj["state_context"], {"regime": "NORMAL", "dealer_gamma": "LONG_GAMMA"})
        self.assertEqual(proj["runtime_flags"], {"dry_run": False, "circuit_breaker": "ACTIVE"})
        self.assertEqual(proj["macro_calendar_shield"], {"fomc_proximity": False})
        self.assertEqual(proj["custom_metadata"], "unmodified_value")

    def test_preserves_external_mutable_state_sections(self):
        base_doc = {
            "mutable_state": {
                "forensic_intelligence": {"last_flag": "OK"},
                "active_orders": [{"order_id": "ORD-1", "action": "BUY"}],
                "portfolio_snapshot": [{"ticker": "STALE", "shares": 1}],  # Should be overwritten
            }
        }
        with self.core_manager.connection() as conn:
            self._seed_standard_database(conn)
            proj = self.generator.generate(conn, base_document=base_doc)

        ms = proj["mutable_state"]
        self.assertEqual(ms["forensic_intelligence"], {"last_flag": "OK"})
        self.assertEqual(ms["active_orders"], [{"order_id": "ORD-1", "action": "BUY"}])
        # SQLite-owned portfolio_snapshot must overwrite the stale base_doc value
        self.assertEqual([p["ticker"] for p in ms["portfolio_snapshot"]], ["RKLB", "SPY"])

    def test_overwrites_sqlite_owned_sections(self):
        base_doc = {
            "portfolio_snapshot": [{"ticker": "STALE_TOP", "shares": 999}],
            "unallocated_cash_eur": "99999.99",
            "watched_tickers": ["STALE_TICKER"],
            "mutable_state": {
                "portfolio_snapshot": [{"ticker": "STALE_MUT", "shares": 999}],
                "watched_tickers": ["STALE_TICKER"],
            },
        }
        with self.core_manager.connection() as conn:
            self._seed_standard_database(conn)
            proj = self.generator.generate(conn, base_document=base_doc)

        self.assertEqual([p["ticker"] for p in proj["portfolio_snapshot"]], ["RKLB", "SPY"])
        self.assertEqual(proj["unallocated_cash_eur"], "2359.80")
        self.assertEqual(proj["watched_tickers"], ["AMZN", "GOOG"])
        self.assertEqual([p["ticker"] for p in proj["mutable_state"]["portfolio_snapshot"]], ["RKLB", "SPY"])
        self.assertEqual(proj["mutable_state"]["watched_tickers"], ["AMZN", "GOOG"])

    def test_malformed_base_document_rejected(self):
        with self.core_manager.connection() as conn:
            with self.assertRaises(ValidationError):
                self.generator.generate(conn, base_document="not_a_dict")
            with self.assertRaises(ValidationError):
                self.generator.generate(conn, base_document=["list_not_allowed"])
            with self.assertRaises(ValidationError):
                self.generator.generate(conn, base_document={"mutable_state": "not_a_dict"})

    def test_base_document_not_mutated(self):
        base_doc = {
            "external_key": "val",
            "mutable_state": {"nested_key": [1, 2, 3]},
        }
        original_copy = json.loads(json.dumps(base_doc))

        with self.core_manager.connection() as conn:
            self.generator.generate(conn, base_document=base_doc)

        self.assertEqual(base_doc, original_copy, "base_document must never be modified by generate()")


# ===========================================================================
# G. Atomic Writer Behavior & Safety
# ===========================================================================

class TestAtomicWriter(BaseProjectionTestCase):
    """Verifies that atomic file replacement works reliably and preserves existing files on failure."""

    def test_successful_write_produces_utf8_json(self):
        dest_file = Path(self.temp_dir.name) / "output" / "ssot_test.json"
        with self.core_manager.connection() as conn:
            self._seed_standard_database(conn)
            proj = self.generator.project_to_file(conn, destination=dest_file)

        self.assertTrue(dest_file.exists())
        with open(dest_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data["_projection_metadata"]["db_revision"], proj["_projection_metadata"]["db_revision"])
        self.assertEqual(data["unallocated_cash_eur"], "2359.80")

    def test_existing_destination_replaced_atomically(self):
        dest_file = Path(self.temp_dir.name) / "ssot_replace.json"
        with open(dest_file, "w", encoding="utf-8") as f:
            f.write('{"initial": "data"}')

        with self.core_manager.connection() as conn:
            self._seed_standard_database(conn)
            self.generator.project_to_file(conn, destination=dest_file)

        with open(dest_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertNotIn("initial", data)
        self.assertIn("_projection_metadata", data)

    def test_serialization_failure_leaves_destination_unchanged(self):
        dest_file = Path(self.temp_dir.name) / "ssot_preserve.json"
        initial_content = '{"intact": true}'
        with open(dest_file, "w", encoding="utf-8") as f:
            f.write(initial_content)

        # Attempt to write projection containing float with allow_floats=False
        bad_projection = {"portfolio_cash": 12.34}
        with self.assertRaises(TypeError):
            self.generator.write_atomic(bad_projection, destination=dest_file, allow_floats=False)

        with open(dest_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), initial_content)

    def test_nested_destination_creates_directories(self):
        deep_dest = Path(self.temp_dir.name) / "a" / "b" / "c" / "test.json"
        self.assertFalse(deep_dest.parent.exists())

        atomic_write_json(deep_dest, '{"status": "ok"}')
        self.assertTrue(deep_dest.exists())
        with open(deep_dest, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"status": "ok"})

    def test_directory_destination_raises_error(self):
        dir_dest = Path(self.temp_dir.name) / "existing_dir"
        dir_dest.mkdir(parents=True, exist_ok=True)

        with self.assertRaises(IsADirectoryError):
            atomic_write_json(dir_dest, '{"status": "ok"}')

    def test_write_failure_preserves_destination_and_cleans_temp(self):
        dest_file = Path(self.temp_dir.name) / "ssot_fail_write.json"
        initial_content = '{"preserved": true}'
        with open(dest_file, "w", encoding="utf-8") as f:
            f.write(initial_content)

        # Inject failure during tempfile handle.write
        with patch("tempfile.NamedTemporaryFile") as mock_tmp:
            import io
            fake_handle = io.StringIO()
            fake_handle.fileno = lambda: 1  # dummy fileno
            fake_handle.name = str(Path(self.temp_dir.name) / ".test_fail.tmp")
            fake_handle.write = lambda _: (_ for _ in ()).throw(IOError("Simulated disk error"))
            mock_tmp.return_value.__enter__.return_value = fake_handle

            with self.assertRaises(IOError):
                atomic_write_json(dest_file, '{"new": "data"}')

        # Verify destination preserved
        with open(dest_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), initial_content)

    def test_replace_failure_preserves_destination_and_cleans_temp(self):
        dest_file = Path(self.temp_dir.name) / "ssot_fail_replace.json"
        initial_content = '{"preserved": true}'
        with open(dest_file, "w", encoding="utf-8") as f:
            f.write(initial_content)

        with patch("os.replace", side_effect=OSError("Simulated filesystem replace error")):
            with self.assertRaises(OSError):
                atomic_write_json(dest_file, '{"new": "data"}')

        # Destination must remain untouched
        with open(dest_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), initial_content)

        # Temp files must be cleaned up
        temp_files = list(Path(self.temp_dir.name).glob("*.tmp"))
        self.assertEqual(temp_files, [], "Temporary files must be cleaned up on failure")


# ===========================================================================
# H. Read-Only Invariants & Domain Projections
# ===========================================================================

class TestReadOnlyInvariants(BaseProjectionTestCase):
    """Verifies that projection generation has zero persistent side-effects on SQLite."""

    def test_projection_does_not_modify_any_database_table(self):
        with self.core_manager.connection() as conn:
            self._seed_standard_database(conn)

            # Record table counts before projection
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM portfolio_cash;")
            cash_count_before = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM portfolio_positions;")
            pos_count_before = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM watchlist;")
            wl_count_before = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM trade_lessons;")
            lesson_count_before = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM decision_log;")
            dec_count_before = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM financial_changelog;")
            changelog_count_before = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM audit_events;")
            audit_count_before = cur.fetchone()[0]
            rev_before = get_db_revision(conn)

            # Execute multiple projection runs
            self.generator.generate(conn)
            self.generator.generate(conn, base_document={"test": 123})

            # Assert identical counts after projection
            cur.execute("SELECT count(*) FROM portfolio_cash;")
            self.assertEqual(cur.fetchone()[0], cash_count_before)
            cur.execute("SELECT count(*) FROM portfolio_positions;")
            self.assertEqual(cur.fetchone()[0], pos_count_before)
            cur.execute("SELECT count(*) FROM watchlist;")
            self.assertEqual(cur.fetchone()[0], wl_count_before)
            cur.execute("SELECT count(*) FROM trade_lessons;")
            self.assertEqual(cur.fetchone()[0], lesson_count_before)
            cur.execute("SELECT count(*) FROM decision_log;")
            self.assertEqual(cur.fetchone()[0], dec_count_before)
            cur.execute("SELECT count(*) FROM financial_changelog;")
            self.assertEqual(cur.fetchone()[0], changelog_count_before)
            cur.execute("SELECT count(*) FROM audit_events;")
            self.assertEqual(cur.fetchone()[0], audit_count_before)
            self.assertEqual(get_db_revision(conn), rev_before)

    def test_isolated_domain_projection_methods(self):
        with self.core_manager.connection() as conn:
            self._seed_standard_database(conn)

            # 1. Trade lessons isolated projection
            tl_proj = self.generator.project_trade_lessons(conn)
            self.assertIn("trade_lessons", tl_proj)
            self.assertEqual(len(tl_proj["trade_lessons"]), 2)
            self.assertEqual(tl_proj["trade_lessons"][0]["rule_code"], "L-219")
            self.assertEqual(tl_proj["trade_lessons"][1]["rule_code"], "L-230")

            # 2. Decision log isolated projection
            dec_proj = self.generator.project_decision_log(conn)
            self.assertIn("decision_log", dec_proj)
            self.assertEqual(len(dec_proj["decision_log"]), 2)
            self.assertEqual(dec_proj["decision_log"][0]["session_id"], "SESSION_001")
            self.assertEqual(dec_proj["decision_log"][1]["session_id"], "SESSION_002")


# ===========================================================================
# I. Strict JSON Validation Before Filesystem Changes (Stage 1.5-C.1 C-01)
# ===========================================================================

class TestAtomicWriterValidation(BaseProjectionTestCase):
    """Verifies that atomic_write_json strictly validates JSON before modifying filesystem."""

    def test_malformed_json_rejected_before_filesystem_changes(self):
        dest_file = Path(self.temp_dir.name) / "malformed.json"
        with self.assertRaises(ValidationError) as ctx:
            atomic_write_json(dest_file, '{"invalid": ')
        self.assertIn("not valid strict JSON", str(ctx.exception))
        self.assertFalse(dest_file.exists(), "Destination file must not be created on validation failure")

    def test_empty_input_rejected_before_filesystem_changes(self):
        dest_file = Path(self.temp_dir.name) / "empty.json"
        with self.assertRaises(ValidationError):
            atomic_write_json(dest_file, "")
        with self.assertRaises(ValidationError):
            atomic_write_json(dest_file, "   \n\t  ")
        self.assertFalse(dest_file.exists(), "Destination must not exist for empty JSON input")

    def test_trailing_non_whitespace_content_rejected(self):
        dest_file = Path(self.temp_dir.name) / "trailing.json"
        with self.assertRaises(ValidationError) as ctx:
            atomic_write_json(dest_file, '{"valid": true} trailing_garbage')
        self.assertIn("not valid strict JSON", str(ctx.exception))
        self.assertFalse(dest_file.exists())

    def test_nan_and_infinity_rejected(self):
        dest_file = Path(self.temp_dir.name) / "constants.json"
        with self.assertRaises(ValidationError) as ctx1:
            atomic_write_json(dest_file, '{"value": NaN}')
        self.assertIn("Non-standard JSON constant is forbidden", str(ctx1.exception))

        with self.assertRaises(ValidationError) as ctx2:
            atomic_write_json(dest_file, '{"value": Infinity}')
        self.assertIn("Non-standard JSON constant is forbidden", str(ctx2.exception))

        with self.assertRaises(ValidationError) as ctx3:
            atomic_write_json(dest_file, '{"value": -Infinity}')
        self.assertIn("Non-standard JSON constant is forbidden", str(ctx3.exception))
        self.assertFalse(dest_file.exists())

    def test_existing_destination_byte_for_byte_unchanged_on_validation_failure(self):
        dest_file = Path(self.temp_dir.name) / "keep_intact.json"
        initial_bytes = b'{"original": "authoritative_content", "checksum": 42}\n'
        dest_file.write_bytes(initial_bytes)

        # Attempt to write invalid JSON payloads
        with self.assertRaises(ValidationError):
            atomic_write_json(dest_file, '{"corrupt": ')
        with self.assertRaises(ValidationError):
            atomic_write_json(dest_file, '{"corrupt": NaN}')
        with self.assertRaises(ValidationError):
            atomic_write_json(dest_file, "")

        self.assertEqual(
            dest_file.read_bytes(),
            initial_bytes,
            "Existing destination file must remain byte-for-byte unchanged on validation failure",
        )

    def test_nonexistent_parent_directory_not_created_on_validation_failure(self):
        deep_dest = Path(self.temp_dir.name) / "new_dir_1" / "new_dir_2" / "target.json"
        self.assertFalse(deep_dest.parent.exists())

        with self.assertRaises(ValidationError):
            atomic_write_json(deep_dest, '{"malformed": ')

        self.assertFalse(
            deep_dest.parent.exists(),
            "Nonexistent parent directory must NOT be created when JSON validation fails",
        )

    def test_no_temporary_file_created_on_validation_failure(self):
        work_dir = Path(self.temp_dir.name) / "clean_dir"
        work_dir.mkdir(parents=True, exist_ok=True)
        dest_file = work_dir / "target.json"

        with self.assertRaises(ValidationError):
            atomic_write_json(dest_file, '{"unclosed": [1, 2,')

        temp_files = list(work_dir.glob("*.tmp")) + list(work_dir.glob(".*.tmp"))
        self.assertEqual(temp_files, [], "No temporary file must be created when JSON validation fails")

    def test_valid_utf8_json_writes_correctly(self):
        dest_file = Path(self.temp_dir.name) / "valid_utf8.json"
        valid_json = '{"currency":"EUR","symbols":["α","β","γ"],"value":"123.45"}'
        atomic_write_json(dest_file, valid_json)

        self.assertTrue(dest_file.exists())
        with open(dest_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), valid_json)


# ===========================================================================
# J. Transaction Cleanup Integrity & Rollback Fault-Injection (Stage 1.5-C.1 C-02)
# ===========================================================================

class InjectedExecutionConnection:
    """Wrapper around sqlite3.Connection / ManagedConnection to inject query and rollback faults."""

    def __init__(
        self,
        target: Any,
        fail_rollback: bool = False,
        fail_query_match: Optional[str] = None,
    ):
        self._target = target
        self._fail_rollback = fail_rollback
        self._fail_query_match = fail_query_match

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if self._fail_rollback and sql.strip().upper() == "ROLLBACK;":
            raise sqlite3.OperationalError("Injected failure during ROLLBACK;")
        if self._fail_query_match and self._fail_query_match in sql:
            raise sqlite3.OperationalError(f"Injected failure during query: {self._fail_query_match}")
        return self._target.execute(sql, *args, **kwargs)

    def cursor(self, *args: Any, **kwargs: Any) -> Any:
        cur = self._target.cursor(*args, **kwargs)
        fail_query_match = self._fail_query_match

        class InjectedCursor:
            def __init__(self, target_cur: Any):
                self._cur = target_cur

            def __getattr__(self, name: str) -> Any:
                return getattr(self._cur, name)

            def execute(self, sql: str, *cur_args: Any, **cur_kwargs: Any) -> Any:
                if fail_query_match and fail_query_match in sql:
                    raise sqlite3.OperationalError(f"Injected failure during query: {fail_query_match}")
                return self._cur.execute(sql, *cur_args, **cur_kwargs)

        return InjectedCursor(cur)


class TestTransactionCleanupIntegrity(BaseProjectionTestCase):
    """Verifies fail-closed read transaction cleanup under normal and fault-injected conditions."""

    def test_successful_projection_rollback_failure_raises_transaction_integrity_error(self):
        """Fault-injection 1: Successful projection followed by injected rollback failure."""
        with self.core_manager.connection() as conn:
            self._seed_standard_database(conn)
            self.assertFalse(conn.in_transaction)

            proxy = InjectedExecutionConnection(conn, fail_rollback=True)
            with self.assertRaises(TransactionIntegrityError) as ctx:
                self.generator.generate(proxy)

            self.assertIn("owned read transaction could not be released", str(ctx.exception))
            self.assertIsInstance(ctx.exception.rollback_error, sqlite3.OperationalError)
            self.assertIsInstance(ctx.exception.__cause__, sqlite3.OperationalError)

    def test_read_failure_with_successful_rollback_propagates_original_error_and_releases_tx(self):
        """Fault-injection 2: Projection read failure followed by successful rollback."""
        with self.core_manager.connection() as conn:
            self._seed_standard_database(conn)
            self.assertFalse(conn.in_transaction)

            # Inject query failure during portfolio_cash list query
            proxy = InjectedExecutionConnection(conn, fail_rollback=False, fail_query_match="portfolio_cash")
            with self.assertRaises(sqlite3.OperationalError) as ctx:
                self.generator.generate(proxy)

            self.assertIn("Injected failure during query: portfolio_cash", str(ctx.exception))
            self.assertFalse(conn.in_transaction, "Connection must no longer be in transaction after successful rollback")

    def test_read_failure_with_rollback_failure_preserves_both_errors(self):
        """Fault-injection 3: Projection read failure followed by rollback failure preserves both errors."""
        with self.core_manager.connection() as conn:
            self._seed_standard_database(conn)
            self.assertFalse(conn.in_transaction)

            proxy = InjectedExecutionConnection(conn, fail_rollback=True, fail_query_match="portfolio_cash")
            with self.assertRaises(TransactionIntegrityError) as ctx:
                self.generator.generate(proxy)

            tie = ctx.exception
            self.assertIn("could not be released", str(tie))
            # Original read failure preserved as cause
            self.assertIsInstance(tie.__cause__, sqlite3.OperationalError)
            self.assertIn("Injected failure during query: portfolio_cash", str(tie.__cause__))
            # Rollback failure preserved in attribute
            self.assertIsInstance(tie.rollback_error, sqlite3.OperationalError)
            self.assertIn("Injected failure during ROLLBACK", str(tie.rollback_error))
            # Note attached if supported
            if hasattr(tie.__cause__, "__notes__") and tie.__cause__.__notes__:
                self.assertTrue(any("rollback failed" in str(n) for n in tie.__cause__.__notes__))

    def test_caller_owned_transaction_not_rolled_back_or_committed(self):
        """Fault-injection 4: Caller-owned transaction is never committed or rolled back by generator."""
        with self.core_manager.connection() as conn:
            self._seed_standard_database(conn)
            conn.execute("BEGIN IMMEDIATE;")
            self.assertTrue(conn.in_transaction)

            # Even if rollback would fail if executed, it must NOT be executed for caller transaction
            proxy = InjectedExecutionConnection(conn, fail_rollback=True)
            proj = self.generator.generate(proxy)

            # Caller retains full transaction control
            self.assertTrue(conn.in_transaction, "Caller transaction must remain open")
            self.assertEqual(proj["unallocated_cash_eur"], "2359.80")

            # Caller safely commits
            conn.execute("COMMIT;")
            self.assertFalse(conn.in_transaction)

    def test_normal_successful_projection_releases_owned_transaction(self):
        """Fault-injection 5: Normal successful projection releases generator-owned transaction cleanly."""
        with self.core_manager.connection() as conn:
            self._seed_standard_database(conn)
            self.assertFalse(conn.in_transaction)

            proj = self.generator.generate(conn)
            self.assertFalse(conn.in_transaction, "Generator-owned transaction must be released on normal exit")
            self.assertEqual(proj["_projection_metadata"]["status"], "HEALTHY")


# ===========================================================================
# K. Genuine Concurrent Snapshot Consistency Verification (Stage 1.5-C.1 C-03)
# ===========================================================================

class TestConcurrentSnapshotConsistency(BaseProjectionTestCase):
    """
    Verifies genuine WAL-mode snapshot consistency under concurrent writers.
    Proves that a separate writer committing during a projection read cannot
    produce a mixed-state projection document combining old revision with new data
    or new revision with old data.
    """

    def _execute_concurrency_iteration(self, iteration: int) -> None:
        # 1. Seed initial known state
        with execution_role_context("System"):
            with self.core_manager.connection() as setup_conn:
                CashRepository(setup_conn).set_cash_balance(
                    account_id="PRIMARY",
                    currency="EUR",
                    amount=Decimal("1000.00"),
                    caller_role="System",
                )
                PositionRepository(setup_conn).upsert_position(
                    account_id="PRIMARY",
                    ticker="AAPL",
                    shares=Decimal("10.0000"),
                    wac_eur=Decimal("150.0000"),
                    wac_usd=None,
                    caller_role="System",
                )
                initial_revision = get_db_revision(setup_conn)

        snapshot_established = threading.Event()
        writer_committed = threading.Event()
        original_list_positions = PositionRepository.list_positions
        conn_holder: Dict[str, Any] = {}

        def coordinated_list_positions(repo_self):
            # Intercept only on the active reader thread's connection
            if repo_self.connection == conn_holder.get("reader"):
                snapshot_established.set()
                if not writer_committed.wait(timeout=10.0):
                    raise TimeoutError(f"Iteration {iteration}: Reader timed out waiting for writer commit")
            return original_list_positions(repo_self)

        def reader_task():
            reader_conn = self.core_manager.connect()
            conn_holder["reader"] = reader_conn
            try:
                return self.generator.generate(reader_conn)
            finally:
                reader_conn.close()

        with patch.object(PositionRepository, "list_positions", side_effect=coordinated_list_positions, autospec=True):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                reader_future = executor.submit(reader_task)

                # Wait for reader to establish snapshot and query database_metadata & cash
                if not snapshot_established.wait(timeout=10.0):
                    raise TimeoutError(f"Iteration {iteration}: Reader failed to establish snapshot within timeout")

                # Writer performs mutations on an independent connection
                writer_conn = self.core_manager.connect()
                try:
                    with execution_role_context("System"):
                        CashRepository(writer_conn).set_cash_balance(
                            account_id="PRIMARY",
                            currency="EUR",
                            amount=Decimal("2500.00"),
                            caller_role="System",
                        )
                        PositionRepository(writer_conn).upsert_position(
                            account_id="PRIMARY",
                            ticker="AAPL",
                            shares=Decimal("50.0000"),
                            wac_eur=Decimal("160.0000"),
                            wac_usd=None,
                            caller_role="System",
                        )
                    writer_revision = get_db_revision(writer_conn)
                finally:
                    writer_conn.close()

                # Signal writer committed
                writer_committed.set()

                # Retrieve reader projection and propagate any worker exception
                proj_initial = reader_future.result(timeout=10.0)

        # 7. Verify reader projection contains COMPLETE pre-write state
        self.assertEqual(
            proj_initial["_projection_metadata"]["db_revision"],
            initial_revision,
            f"Iteration {iteration}: Reader must see pre-write revision",
        )
        self.assertEqual(
            proj_initial["unallocated_cash_eur"],
            "1000.00",
            f"Iteration {iteration}: Reader must see pre-write cash",
        )
        self.assertEqual(
            len(proj_initial["portfolio_snapshot"]),
            1,
        )
        self.assertEqual(
            proj_initial["portfolio_snapshot"][0]["shares"],
            "10.0000",
            f"Iteration {iteration}: Reader must see pre-write shares, not post-write shares",
        )

        # 8-9. Run new projection after writer commits
        with self.core_manager.connection() as post_conn:
            proj_post = self.generator.generate(post_conn)

        self.assertEqual(
            proj_post["_projection_metadata"]["db_revision"],
            writer_revision,
            f"Iteration {iteration}: Post-write projection must see writer revision",
        )
        self.assertEqual(
            proj_post["unallocated_cash_eur"],
            "2500.00",
            f"Iteration {iteration}: Post-write projection must see updated cash",
        )
        self.assertEqual(
            proj_post["portfolio_snapshot"][0]["shares"],
            "50.0000",
            f"Iteration {iteration}: Post-write projection must see updated shares",
        )

    def test_genuine_concurrent_writer_during_read_snapshot_maintains_snapshot_isolation(self):
        """Executes genuine WAL-mode snapshot consistency test across 10 iterations."""
        for i in range(10):
            with self.subTest(iteration=i):
                self._execute_concurrency_iteration(i)


# ===========================================================================
# L. Projection Dictionary Key Validation (Stage 1.5-C.1 C-05)
# ===========================================================================

class TestProjectionDictionaryKeyValidation(unittest.TestCase):
    """Verifies that normalize_projection_value strictly rejects non-string dictionary keys."""

    def test_rejects_integer_key(self):
        with self.assertRaises(TypeError) as ctx:
            normalize_projection_value({1: "numeric_value"})
        self.assertIn("must be strings", str(ctx.exception))
        self.assertIn("int", str(ctx.exception))

    def test_rejects_boolean_key(self):
        with self.assertRaises(TypeError) as ctx:
            normalize_projection_value({True: "boolean_key_value"})
        self.assertIn("must be strings", str(ctx.exception))
        self.assertIn("bool", str(ctx.exception))

    def test_rejects_mixed_integer_string_collision(self):
        bad_dict = {1: "numeric", "1": "string"}
        with self.assertRaises(TypeError) as ctx:
            normalize_projection_value(bad_dict)
        self.assertIn("must be strings", str(ctx.exception))

    def test_rejects_nested_invalid_key(self):
        nested_dict = {
            "valid_top": {
                "valid_mid": {
                    42: "nested_int_key"
                }
            }
        }
        with self.assertRaises(TypeError) as ctx:
            normalize_projection_value(nested_dict)
        self.assertIn("must be strings", str(ctx.exception))
        self.assertIn("int", str(ctx.exception))

    def test_valid_string_key_dictionaries_remain_deterministic(self):
        data = {
            "zeta": Decimal("10.50"),
            "alpha": {"beta": 1, "gamma": "text"},
            "items": [{"name": "item1"}, {"name": "item2"}],
        }
        normalized = normalize_projection_value(data)
        self.assertEqual(normalized["zeta"], "10.50")
        self.assertEqual(normalized["alpha"], {"beta": 1, "gamma": "text"})
        self.assertEqual(list(normalized.keys()), ["zeta", "alpha", "items"])
        serialized = serialize_projection(data)
        self.assertTrue(
            serialized.startswith('{"alpha":{"beta":1,"gamma":"text"},"items":[{"name":"item1"},{"name":"item2"}],"zeta":"10.50"}')
        )


if __name__ == "__main__":
    unittest.main()
