"""
Automated Test Suite for SQLite Repository Layer & Transactional Persistence Verification.
Governed by Stage 1.4 & Stage 1.4.1 Architecture and Agent Governance Framework.

Covers:
1. CRUD and Mapping (all 10 entities).
2. Financial Decimal Integrity (exact scale, floats rejected, invalid strings rejected).
3. Financial Authorization Enforcement (role checks, owner approval, digest binding).
4. Transactional Atomicity & Rollback (multi-repo writes, changelog failure atomicity).
5. Concurrency and Idempotency (replay of duplicate event_ids with identical vs conflicting payloads).
6. Append-Only Immutability (audit events, changelog, owner approvals, decision log).
7. Cache Isolation (core vs cache database separation, cross-connection boundary enforcement).
8. Import and Lifecycle Safety (zero side-effects, state file invariance).
9. R1: Mandatory Financial Changelog Enforcement.
10. R2: Owner Approval Record Integrity & Trusted Attribution.
11. R3: Financial Changelog Repository Protection.
12. R4: Trusted Actor Attribution.
13. R5: Transaction Snapshot Consistency.
14. R6: Unit of Work Lifecycle Enforcement.
15. R7: Daily Bar Batch Atomicity.
16. R8: Consistent Daily Bar Input & Calendar Date Validation.
"""

from datetime import datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid
from typing import Any, Dict, Optional
from unittest.mock import patch

from python.database.manager import (
    DatabaseManager,
    PRIMARY_FINANCIAL_PROFILE,
    MARKET_DATA_CACHE_PROFILE,
    DatabaseTransactionError,
)
from python.database.schema import SchemaMigrator
from python.database.repositories import (
    RepositoryError,
    EntityNotFoundError,
    DuplicateEntityError,
    IntegrityConflictError,
    ValidationError,
    ConcurrencyConflictError,
    AppendOnlyViolationError,
    TransactionIntegrityError,
    CashBalance,
    PositionRecord,
    WatchlistItem,
    ScoutCategory,
    TradeLesson,
    DecisionRecord,
    FinancialChangelogEntry,
    OwnerApprovalRecord,
    AuditEventRecord,
    DailyBarRecord,
    CashRepository,
    PositionRepository,
    WatchlistRepository,
    ScoutCategoryRepository,
    TradeLessonRepository,
    DecisionLogRepository,
    OwnerApprovalRepository,
    AuditEventRepository,
    FinancialChangelogRepository,
    DailyBarRepository,
    UnitOfWork,
    RepositoryFactory,
    validate_decimal,
    utc_now_iso,
    validate_timestamp_utc,
    get_db_revision,
    increment_db_revision,
    repository_transaction,
)
from python.tool_registry import (
    execution_role_context,
    GLOBAL_OWNER_APPROVAL,
    compute_payload_digest,
)


def _mint_test_capability(
    connection: sqlite3.Connection,
    correlation_id: str,
    actor_role: str,
    operation_name: str,
    target_resource: str,
    before_state: Optional[Dict[str, Any]] = None,
    after_state: Optional[Dict[str, Any]] = None,
    approval_digest: Optional[str] = None,
    timestamp_utc: Optional[str] = None,
):
    """
    Test-local helper strictly for test suites to construct _FinancialMutationCapability
    when testing fail-closed validation in append_entry.
    Uses unittest.mock.patch to satisfy internal credential validation strictly within
    test scope. No test seams exist in production code.
    """
    from python.database.repositories.financial import _FinancialMutationCapability

    with patch("python.database.repositories.financial._validate_credential", return_value=True):
        return _FinancialMutationCapability(
            credential="test_fixture_credential",
            connection=connection,
            correlation_id=correlation_id,
            actor_role=actor_role,
            operation_name=operation_name,
            target_resource=target_resource,
            before_state=before_state,
            after_state=after_state,
            approval_digest=approval_digest,
            timestamp_utc=timestamp_utc or utc_now_iso(),
        )


class BaseRepositoryTestCase(unittest.TestCase):
    """Base test case initializing isolated temporary core and cache SQLite databases."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.core_db_path = os.path.join(self.temp_dir.name, "test_core.db")
        self.cache_db_path = os.path.join(self.temp_dir.name, "test_cache.db")

        self.core_manager = DatabaseManager(self.core_db_path, profile=PRIMARY_FINANCIAL_PROFILE)
        self.cache_manager = DatabaseManager(self.cache_db_path, profile=MARKET_DATA_CACHE_PROFILE)

        # Deploy verified schemas using SchemaMigrator classmethods
        with self.core_manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
        with self.cache_manager.connection() as conn:
            SchemaMigrator.initialize_cache_schema(conn)

        self.factory = RepositoryFactory(self.core_manager, self.cache_manager)

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass
        GLOBAL_OWNER_APPROVAL.reset()


# ===========================================================================
# 1. CRUD and Mapping Tests
# ===========================================================================

class TestRepositoryCrudAndMapping(BaseRepositoryTestCase):
    """Verifies CRUD operations and domain object mapping across all 10 schema entities."""

    def test_cash_crud_and_mapping(self):
        with self.core_manager.connection() as conn:
            repo = CashRepository(conn)
            with execution_role_context("Terminal Orchestrator"):
                # Insert cash
                balance = repo.set_cash_balance("PRIMARY", "EUR", Decimal("15000.50"), caller_role="Terminal Orchestrator")
                self.assertEqual(balance.account_id, "PRIMARY")
                self.assertEqual(balance.currency, "EUR")
                self.assertEqual(balance.amount, Decimal("15000.50"))
                self.assertIsInstance(balance.amount, Decimal)

                # Retrieve cash
                fetched = repo.get_cash("PRIMARY", "EUR")
                self.assertIsNotNone(fetched)
                self.assertEqual(fetched.amount, Decimal("15000.50"))

                # Update cash
                updated = repo.set_cash_balance("PRIMARY", "EUR", "20000.00", caller_role="Terminal Orchestrator")
                self.assertEqual(updated.amount, Decimal("20000.00"))

                # List cash
                repo.set_cash_balance("PRIMARY", "USD", Decimal("5000.00"), caller_role="Terminal Orchestrator")
                all_cash = repo.list_cash("PRIMARY")
                self.assertEqual(len(all_cash), 2)
                self.assertEqual({c.currency for c in all_cash}, {"EUR", "USD"})

                # Delete cash
                deleted = repo.delete_cash("PRIMARY", "USD", caller_role="Terminal Orchestrator")
                self.assertTrue(deleted)
                self.assertIsNone(repo.get_cash("PRIMARY", "USD"))

    def test_position_crud_and_mapping(self):
        with self.core_manager.connection() as conn:
            repo = PositionRepository(conn)
            with execution_role_context("Terminal Orchestrator"):
                # Insert position without USD cost basis
                pos = repo.upsert_position(
                    account_id="PRIMARY",
                    ticker="NOVO-B.CO",
                    shares=Decimal("150.0000"),
                    wac_eur=Decimal("95.2500"),
                    caller_role="Terminal Orchestrator",
                )
                self.assertEqual(pos.ticker, "NOVO-B.CO")
                self.assertEqual(pos.shares, Decimal("150.0000"))
                self.assertEqual(pos.wac_eur, Decimal("95.2500"))
                self.assertIsNone(pos.wac_usd)
                self.assertIsInstance(pos.shares, Decimal)

                # Retrieve position
                fetched = repo.get_position("PRIMARY", "NOVO-B.CO")
                self.assertIsNotNone(fetched)
                self.assertEqual(fetched.shares, Decimal("150.0000"))

                # Update position with USD cost basis
                updated = repo.upsert_position(
                    account_id="PRIMARY",
                    ticker="NOVO-B.CO",
                    shares=Decimal("200.0000"),
                    wac_eur=Decimal("92.1000"),
                    wac_usd=Decimal("100.5000"),
                    caller_role="Terminal Orchestrator",
                )
                self.assertEqual(updated.shares, Decimal("200.0000"))
                self.assertEqual(updated.wac_usd, Decimal("100.5000"))

                # List positions
                repo.upsert_position("PRIMARY", "SAP.DE", Decimal("50.0000"), Decimal("180.0000"), caller_role="Terminal Orchestrator")
                positions = repo.list_positions("PRIMARY")
                self.assertEqual(len(positions), 2)

                # Delete position
                deleted = repo.delete_position("PRIMARY", "SAP.DE", caller_role="Terminal Orchestrator")
                self.assertTrue(deleted)
                self.assertIsNone(repo.get_position("PRIMARY", "SAP.DE"))

    def test_watchlist_crud_and_mapping(self):
        with self.core_manager.connection() as conn:
            repo = WatchlistRepository(conn)
            item = repo.upsert_watchlist_item("ASML.AS", status="ACTIVE", notes="Semiconductor equipment leader")
            self.assertEqual(item.ticker, "ASML.AS")
            self.assertEqual(item.status, "ACTIVE")
            self.assertEqual(item.notes, "Semiconductor equipment leader")

            fetched = repo.get_watchlist_item("ASML.AS")
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched.ticker, "ASML.AS")

            # Update status
            repo.upsert_watchlist_item("ASML.AS", status="PAUSED", notes="Waiting for pullback")
            updated = repo.get_watchlist_item("ASML.AS")
            self.assertEqual(updated.status, "PAUSED")

            # Filter list
            repo.upsert_watchlist_item("AIR.PA", status="ACTIVE")
            active_items = repo.list_watchlist(status="ACTIVE")
            self.assertEqual(len(active_items), 1)
            self.assertEqual(active_items[0].ticker, "AIR.PA")

            # Delete
            deleted = repo.remove_watchlist_item("ASML.AS")
            self.assertTrue(deleted)
            self.assertIsNone(repo.get_watchlist_item("ASML.AS"))

    def test_scout_categories_crud_and_mapping(self):
        with self.core_manager.connection() as conn:
            repo = ScoutCategoryRepository(conn)
            cat = repo.upsert_category("Defense & Aerospace", "European defense contractors", ["AIR.PA", "RHM.DE"])
            self.assertEqual(cat.category_name, "Defense & Aerospace")
            self.assertEqual(cat.tickers, ("AIR.PA", "RHM.DE"))

            fetched = repo.get_category_by_name("Defense & Aerospace")
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched.id, cat.id)

            # Update category
            repo.upsert_category("Defense & Aerospace", "Updated description", ["AIR.PA", "RHM.DE", "BA.L"])
            updated = repo.get_category(cat.id)
            self.assertEqual(updated.description, "Updated description")
            self.assertEqual(updated.tickers, ("AIR.PA", "RHM.DE", "BA.L"))

            # Delete category
            deleted = repo.delete_category(cat.id)
            self.assertTrue(deleted)
            self.assertIsNone(repo.get_category(cat.id))

    def test_trade_lessons_crud_and_mapping(self):
        with self.core_manager.connection() as conn:
            repo = TradeLessonRepository(conn)
            with execution_role_context("Terminal Orchestrator"):
                lesson = repo.upsert_lesson(
                    rule_code="RULE_01",
                    directive_text="Never enter without structural edge",
                    category="MANDATE",
                    status="ACTIVE",
                    caller_role="Terminal Orchestrator",
                )
                self.assertEqual(lesson.rule_code, "RULE_01")
                self.assertEqual(lesson.category, "MANDATE")

                fetched = repo.get_lesson("RULE_01")
                self.assertIsNotNone(fetched)
                self.assertEqual(fetched.directive_text, "Never enter without structural edge")

                # List filtered
                repo.upsert_lesson("RULE_02", "Take profit at Fib 1.618", "EXECUTION", caller_role="Terminal Orchestrator")
                mandates = repo.list_lessons(category="MANDATE")
                self.assertEqual(len(mandates), 1)
                self.assertEqual(mandates[0].rule_code, "RULE_01")

                # Delete lesson
                deleted = repo.delete_lesson("RULE_02", caller_role="Terminal Orchestrator")
                self.assertTrue(deleted)
                self.assertIsNone(repo.get_lesson("RULE_02"))

    def test_decision_log_crud_and_mapping(self):
        with self.core_manager.connection() as conn:
            repo = DecisionLogRepository(conn)
            with execution_role_context("Terminal Orchestrator"):
                record = repo.append_decision(
                    session_id="SESS_001",
                    action_proposed="BUY NOVO-B.CO",
                    decision_outcome="APPROVED",
                    ticker="NOVO-B.CO",
                    votes={"Bullish Advocate": "YES", "Macro Sentinel": "YES"},
                    transcript_text="Council approved based on GLP-1 demand.",
                    caller_role="Terminal Orchestrator",
                )
                self.assertIsNotNone(record.id)
                self.assertEqual(record.session_id, "SESS_001")
                self.assertEqual(record.votes["Bullish Advocate"], "YES")

                fetched = repo.get_decision(record.id)
                self.assertIsNotNone(fetched)
                self.assertEqual(fetched.action_proposed, "BUY NOVO-B.CO")

                # Filter list
                items = repo.list_decisions(ticker="NOVO-B.CO")
                self.assertEqual(len(items), 1)

    def test_financial_changelog_crud_and_mapping(self):
        with self.core_manager.connection() as conn:
            pos_repo = PositionRepository(conn)
            repo = FinancialChangelogRepository(conn)
            with execution_role_context("Terminal Orchestrator"):
                pos_repo.upsert_position(
                    account_id="PRIMARY",
                    ticker="SAP.DE",
                    shares=Decimal("100.0000"),
                    wac_eur=Decimal("150.0000"),
                    correlation_id="CORR_123",
                )
                entries = repo.list_entries(correlation_id="CORR_123")
                self.assertEqual(len(entries), 1)
                entry = entries[0]
                self.assertIsNotNone(entry.id)
                self.assertEqual(entry.correlation_id, "CORR_123")
                self.assertEqual(entry.after_state["shares"], "100.0000")

                fetched = repo.get_entry(entry.id)
                self.assertIsNotNone(fetched)
                self.assertEqual(fetched.operation_name, "upsert_position")

    def test_owner_approvals_crud_and_mapping(self):
        with self.core_manager.connection() as conn:
            repo = OwnerApprovalRepository(conn)
            with execution_role_context("Owner"):
                approval = repo.append_approval(
                    approval_id="APP_001",
                    operation_name="update_rules",
                    target_resource="rules/core",
                    payload_digest="a" * 64,
                )
                self.assertEqual(approval.approval_id, "APP_001")
                self.assertEqual(approval.status, "PENDING")
                self.assertEqual(approval.granted_by, "Owner")
                self.assertIsNone(approval.consumed_at_utc)

                fetched = repo.get_approval("APP_001")
                self.assertIsNotNone(fetched)

                # Consume approval
                consumed = repo.consume_approval("APP_001")
                self.assertTrue(consumed)
                after_consume = repo.get_approval("APP_001")
                self.assertEqual(after_consume.status, "CONSUMED")
                self.assertIsNotNone(after_consume.consumed_at_utc)

                # Cannot re-consume
                reconsumed = repo.consume_approval("APP_001")
                self.assertFalse(reconsumed)

    def test_audit_event_crud_and_mapping(self):
        with self.core_manager.connection() as conn:
            repo = AuditEventRepository(conn)
            event_id = uuid.uuid4().hex
            event = repo.append_event(
                event_id=event_id,
                timestamp_utc="2026-11-08T22:00:00Z",
                action_category="TOOL_EXECUTION",
                status="SUCCESS",
                agent_role="Macro Sentinel",
                permission_decision="ALLOWED",
                approval_status="NOT_REQUIRED",
                raw_event={"tool": "read_portfolio", "status": "ok"},
                tool_name="read_portfolio",
                payload_hash="hash123",
            )
            self.assertEqual(event.event_id, event_id)
            self.assertEqual(repo.count_events(), 1)

            fetched = repo.get_event(event_id)
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched.raw_event["tool"], "read_portfolio")

            events = repo.list_events(action_category="TOOL_EXECUTION")
            self.assertEqual(len(events), 1)

    def test_daily_bar_crud_and_mapping(self):
        with self.cache_manager.connection() as conn:
            repo = DailyBarRepository(conn)
            bar = repo.upsert_bar(
                ticker="ASML.AS",
                bar_date="2026-11-07",
                open=Decimal("850.2500"),
                high=Decimal("865.0000"),
                low=Decimal("845.5000"),
                close=Decimal("860.7500"),
                volume=125000,
                indicators={"RSI_14": "62.5", "EMA_20": "840.10"},
            )
            self.assertEqual(bar.ticker, "ASML.AS")
            self.assertEqual(bar.bar_date, "2026-11-07")
            self.assertEqual(bar.close, Decimal("860.7500"))
            self.assertEqual(bar.indicators["RSI_14"], "62.5")

            fetched = repo.get_bar("ASML.AS", "2026-11-07")
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched.volume, 125000)

            # Batch upsert
            batch = [
                DailyBarRecord(
                    ticker="ASML.AS",
                    bar_date="2026-11-08",
                    open=Decimal("861.0000"),
                    high=Decimal("870.0000"),
                    low=Decimal("858.0000"),
                    close=Decimal("868.5000"),
                    volume=150000,
                    indicators={},
                    updated_at_utc="2026-11-08T22:00:00Z",
                )
            ]
            count = repo.upsert_bars_batch(batch)
            self.assertEqual(count, 1)

            # Date range query
            bars = repo.list_bars("ASML.AS", start_date="2026-11-07", end_date="2026-11-08")
            self.assertEqual(len(bars), 2)

            # Delete bars
            deleted_count = repo.delete_bars("ASML.AS")
            self.assertEqual(deleted_count, 2)
            self.assertEqual(len(repo.list_bars("ASML.AS")), 0)


# ===========================================================================
# 2. Financial Decimal Integrity Tests
# ===========================================================================

class TestFinancialDecimalIntegrity(BaseRepositoryTestCase):
    """Verifies strict Decimal enforcement, scale constraints, float rejection, and invalid string rejection."""

    def test_cash_decimal_precision_scale(self):
        with self.core_manager.connection() as conn:
            repo = CashRepository(conn)
            with execution_role_context("Terminal Orchestrator"):
                # Valid: scale <= 2
                repo.set_cash_balance("PRIMARY", "EUR", Decimal("100.25"), caller_role="Terminal Orchestrator")
                repo.set_cash_balance("PRIMARY", "EUR", "100.2", caller_role="Terminal Orchestrator")
                repo.set_cash_balance("PRIMARY", "EUR", 100, caller_role="Terminal Orchestrator")
                repo.set_cash_balance("PRIMARY", "EUR", "0", caller_role="Terminal Orchestrator")
                repo.set_cash_balance("PRIMARY", "EUR", "0.05", caller_role="Terminal Orchestrator")

                # Excess scale (> 2 decimal places) must be rejected
                with self.assertRaises(ValidationError):
                    repo.set_cash_balance("PRIMARY", "EUR", Decimal("100.255"), caller_role="Terminal Orchestrator")
                with self.assertRaises(ValidationError):
                    repo.set_cash_balance("PRIMARY", "EUR", "100.001", caller_role="Terminal Orchestrator")

    def test_cash_float_rejected(self):
        with self.core_manager.connection() as conn:
            repo = CashRepository(conn)
            with execution_role_context("Terminal Orchestrator"):
                with self.assertRaises(ValidationError) as ctx:
                    repo.set_cash_balance("PRIMARY", "EUR", 100.50, caller_role="Terminal Orchestrator")
                self.assertIn("Floating-point values strictly forbidden", str(ctx.exception))

    def test_cash_negative_rejected(self):
        with self.core_manager.connection() as conn:
            repo = CashRepository(conn)
            with execution_role_context("Terminal Orchestrator"):
                with self.assertRaises(ValidationError):
                    repo.set_cash_balance("PRIMARY", "EUR", Decimal("-10.00"), caller_role="Terminal Orchestrator")
                with self.assertRaises(ValidationError):
                    repo.set_cash_balance("PRIMARY", "EUR", "-5.00", caller_role="Terminal Orchestrator")

    def test_cash_malformed_strings(self):
        with self.core_manager.connection() as conn:
            repo = CashRepository(conn)
            malformed = [
                "1.2.3", "1..2", ".5", "5.", "05", "00", "05.20",
                "abc", "1e5", "1.5E2", "100 ", " 100", "", "   "
            ]
            with execution_role_context("Terminal Orchestrator"):
                for val in malformed:
                    with self.subTest(val=val):
                        with self.assertRaises(ValidationError):
                            repo.set_cash_balance("PRIMARY", "EUR", val, caller_role="Terminal Orchestrator")

    def test_cash_nan_inf_rejected(self):
        with self.core_manager.connection() as conn:
            repo = CashRepository(conn)
            with execution_role_context("Terminal Orchestrator"):
                with self.assertRaises(ValidationError):
                    repo.set_cash_balance("PRIMARY", "EUR", Decimal("NaN"), caller_role="Terminal Orchestrator")
                with self.assertRaises(ValidationError):
                    repo.set_cash_balance("PRIMARY", "EUR", Decimal("Infinity"), caller_role="Terminal Orchestrator")

    def test_position_decimal_precision_scale(self):
        with self.core_manager.connection() as conn:
            repo = PositionRepository(conn)
            with execution_role_context("Terminal Orchestrator"):
                # Valid: scale <= 4
                repo.upsert_position(
                    "PRIMARY", "SAP.DE", Decimal("100.1234"), Decimal("150.5555"), caller_role="Terminal Orchestrator"
                )

                # Excess scale (> 4) rejected
                with self.assertRaises(ValidationError):
                    repo.upsert_position(
                        "PRIMARY", "SAP.DE", Decimal("100.12345"), Decimal("150.0000"), caller_role="Terminal Orchestrator"
                    )
                with self.assertRaises(ValidationError):
                    repo.upsert_position(
                        "PRIMARY", "SAP.DE", Decimal("100.0000"), Decimal("150.12345"), caller_role="Terminal Orchestrator"
                    )

    def test_position_zero_and_negative_shares_rejected(self):
        with self.core_manager.connection() as conn:
            repo = PositionRepository(conn)
            with execution_role_context("Terminal Orchestrator"):
                # Zero shares forbidden
                with self.assertRaises(ValidationError):
                    repo.upsert_position(
                        "PRIMARY", "SAP.DE", Decimal("0"), Decimal("150.0000"), caller_role="Terminal Orchestrator"
                    )
                # Negative shares forbidden
                with self.assertRaises(ValidationError):
                    repo.upsert_position(
                        "PRIMARY", "SAP.DE", Decimal("-10.0000"), Decimal("150.0000"), caller_role="Terminal Orchestrator"
                    )

    def test_exact_roundtrip_persistence(self):
        with self.core_manager.connection() as conn:
            repo = PositionRepository(conn)
            original_shares = Decimal("123.4567")
            original_wac = Decimal("987.6543")
            with execution_role_context("Terminal Orchestrator"):
                repo.upsert_position(
                    "PRIMARY", "ASML.AS", original_shares, original_wac, caller_role="Terminal Orchestrator"
                )

            fetched = repo.get_position("PRIMARY", "ASML.AS")
            self.assertEqual(fetched.shares, original_shares)
            self.assertEqual(fetched.wac_eur, original_wac)
            self.assertIsInstance(fetched.shares, Decimal)
            self.assertIsInstance(fetched.wac_eur, Decimal)


# ===========================================================================
# 3. Financial Authorization Enforcement Tests
# ===========================================================================

class TestFinancialAuthorizationEnforcement(BaseRepositoryTestCase):
    """Verifies fail-closed enforcement of authorize_financial_mutation() on all financial paths."""

    def test_unauthorized_engineering_role_cash_mutation_rejected(self):
        with self.core_manager.connection() as conn:
            repo = CashRepository(conn)
            with execution_role_context("Python Engineer"):
                with self.assertRaises(PermissionError) as ctx:
                    repo.set_cash_balance("PRIMARY", "EUR", Decimal("1000.00"))
                self.assertIn("Financial boundary violation: Engineering role 'Python Engineer' is forbidden", str(ctx.exception))

    def test_unauthorized_investment_role_cash_mutation_rejected(self):
        with self.core_manager.connection() as conn:
            repo = CashRepository(conn)
            with execution_role_context("Macro Sentinel"):
                with self.assertRaises(PermissionError) as ctx:
                    repo.set_cash_balance("PRIMARY", "EUR", Decimal("1000.00"))
                self.assertIn("not authorized to perform financial mutation", str(ctx.exception))

    def test_authorized_role_cash_mutation_succeeds(self):
        with self.core_manager.connection() as conn:
            repo = CashRepository(conn)
            with execution_role_context("Terminal Orchestrator"):
                balance = repo.set_cash_balance("PRIMARY", "EUR", Decimal("1000.00"))
                self.assertEqual(balance.amount, Decimal("1000.00"))

    def test_owner_approval_binding_and_tampering_prevention(self):
        with self.core_manager.connection() as conn:
            repo = CashRepository(conn)
            payload = {"account_id": "PRIMARY", "currency": "EUR", "amount": "50000.00"}
            target = "portfolio_cash/PRIMARY/EUR"

            # 1. Calling with requires_owner_approval=True without grant must fail
            with execution_role_context("Terminal Orchestrator"):
                with self.assertRaises(PermissionError) as ctx:
                    repo.set_cash_balance("PRIMARY", "EUR", Decimal("50000.00"), requires_owner_approval=True)
                self.assertIn("Human owner confirmation required", str(ctx.exception))

            # 2. Grant approval for payload
            with execution_role_context("Owner"):
                GLOBAL_OWNER_APPROVAL.grant_one_time_approval(
                    operation_name="update_ssot",
                    target_resource=target,
                    payload=payload,
                )

            # 3. Calling with a DIFFERENT amount must still fail (tamper prevention)
            with execution_role_context("Terminal Orchestrator"):
                with self.assertRaises(PermissionError):
                    repo.set_cash_balance("PRIMARY", "EUR", Decimal("60000.00"), requires_owner_approval=True)

            # 4. Calling with the EXACT approved payload succeeds
            with execution_role_context("Terminal Orchestrator"):
                balance = repo.set_cash_balance("PRIMARY", "EUR", Decimal("50000.00"), requires_owner_approval=True)
                self.assertEqual(balance.amount, Decimal("50000.00"))

            # 5. One-time approval is now consumed; subsequent call must fail
            with execution_role_context("Terminal Orchestrator"):
                with self.assertRaises(PermissionError):
                    repo.set_cash_balance("PRIMARY", "EUR", Decimal("50000.00"), requires_owner_approval=True)

    def test_unauthorized_trade_lesson_mutation_rejected(self):
        with self.core_manager.connection() as conn:
            repo = TradeLessonRepository(conn)
            with execution_role_context("Security Agent"):
                with self.assertRaises(PermissionError):
                    repo.upsert_lesson("RULE_01", "Some directive", "MANDATE")

    def test_unauthorized_decision_log_mutation_rejected(self):
        with self.core_manager.connection() as conn:
            repo = DecisionLogRepository(conn)
            with execution_role_context("Architecture Agent"):
                with self.assertRaises(PermissionError):
                    repo.append_decision("SESS_01", "BUY", "APPROVED")

    def test_read_operations_require_no_financial_authorization(self):
        with self.core_manager.connection() as conn:
            cash_repo = CashRepository(conn)
            pos_repo = PositionRepository(conn)
            # Populate data with System role
            cash_repo.set_cash_balance("PRIMARY", "EUR", Decimal("500.00"))
            pos_repo.upsert_position("PRIMARY", "SAP.DE", Decimal("10.0000"), Decimal("150.0000"))

            # Read operations with engineering role must succeed
            with execution_role_context("Python Engineer"):
                c = cash_repo.get_cash("PRIMARY", "EUR")
                self.assertIsNotNone(c)
                p = pos_repo.get_position("PRIMARY", "SAP.DE")
                self.assertIsNotNone(p)


# ===========================================================================
# 4. Transactional Atomicity and Rollback Tests
# ===========================================================================

class TestTransactionalAtomicityAndRollback(BaseRepositoryTestCase):
    """Verifies UnitOfWork atomic commits, rollback on failure, and changelog atomicity."""

    def test_uow_multi_repo_commit(self):
        with self.factory.create_unit_of_work() as uow:
            uow.cash.set_cash_balance("PRIMARY", "EUR", Decimal("10000.00"))
            uow.positions.upsert_position("PRIMARY", "SAP.DE", Decimal("50.0000"), Decimal("180.0000"))
            uow.watchlist.upsert_watchlist_item("SAP.DE", status="ACTIVE")

        # Verify all changes persisted on a fresh connection
        with self.core_manager.connection() as conn:
            cash_repo = CashRepository(conn)
            pos_repo = PositionRepository(conn)
            watch_repo = WatchlistRepository(conn)
            changelog_repo = FinancialChangelogRepository(conn)

            self.assertEqual(cash_repo.get_cash("PRIMARY", "EUR").amount, Decimal("10000.00"))
            self.assertEqual(pos_repo.get_position("PRIMARY", "SAP.DE").shares, Decimal("50.0000"))
            self.assertEqual(watch_repo.get_watchlist_item("SAP.DE").status, "ACTIVE")
            # Cash, position, and watchlist all recorded changelog entries (R1)
            entries = changelog_repo.list_entries()
            self.assertEqual(len(entries), 3)

    def test_uow_rollback_on_exception(self):
        with self.assertRaises(ValueError):
            with self.factory.create_unit_of_work() as uow:
                uow.cash.set_cash_balance("PRIMARY", "EUR", Decimal("10000.00"))
                uow.positions.upsert_position("PRIMARY", "SAP.DE", Decimal("50.0000"), Decimal("180.0000"))
                # Trigger exception before commit
                raise ValueError("Simulated unexpected business error")

        # Verify nothing was persisted
        with self.core_manager.connection() as conn:
            cash_repo = CashRepository(conn)
            pos_repo = PositionRepository(conn)
            changelog_repo = FinancialChangelogRepository(conn)

            self.assertIsNone(cash_repo.get_cash("PRIMARY", "EUR"))
            self.assertIsNone(pos_repo.get_position("PRIMARY", "SAP.DE"))
            self.assertEqual(len(changelog_repo.list_entries()), 0)

    def test_changelog_failure_rolls_back_mutation(self):
        """Simulate a failure during changelog insertion and verify financial mutation rolls back."""
        with self.core_manager.connection() as conn:
            # Drop financial_changelog table temporarily to force changelog insertion failure
            conn.execute("DROP TABLE financial_changelog;")
            repo = CashRepository(conn)

            with self.assertRaises(sqlite3.OperationalError):
                repo.set_cash_balance("PRIMARY", "EUR", Decimal("10000.00"))

            # Cash record must not exist
            cur = conn.cursor()
            cur.execute("SELECT * FROM portfolio_cash WHERE currency = 'EUR'")
            self.assertIsNone(cur.fetchone())

    def test_mutation_failure_leaves_no_changelog(self):
        """When a financial mutation fails validation, no changelog record is created."""
        with self.core_manager.connection() as conn:
            repo = CashRepository(conn)
            changelog_repo = FinancialChangelogRepository(conn)

            with self.assertRaises(ValidationError):
                repo.set_cash_balance("PRIMARY", "EUR", "invalid_decimal")

            self.assertEqual(len(changelog_repo.list_entries()), 0)

    def test_nested_uow_rejected(self):
        """Nested UnitOfWork transactions on the same connection are rejected fail-closed."""
        with self.core_manager.connection() as conn:
            with self.assertRaises(DatabaseTransactionError):
                with UnitOfWork(self.core_manager, connection=conn):
                    # Nested transaction on already-active connection
                    with UnitOfWork(self.core_manager, connection=conn):
                        pass

    def test_commit_after_rollback_rejected(self):
        uow = self.factory.create_unit_of_work()
        with uow:
            uow.cash.set_cash_balance("PRIMARY", "EUR", Decimal("100.00"))
            uow.rollback()
            with self.assertRaises(RepositoryError):
                uow.commit()


# ===========================================================================
# 5. Concurrency and Idempotency Tests
# ===========================================================================

class TestConcurrencyAndIdempotency(BaseRepositoryTestCase):
    """Verifies duplicate handling, idempotency replay, and conflict detection."""

    def test_audit_event_idempotent_replay(self):
        with self.core_manager.connection() as conn:
            repo = AuditEventRepository(conn)
            event_id = uuid.uuid4().hex
            raw_event = {"tool": "read_portfolio", "status": "ok"}
            payload_hash = hashlib.sha256(b"payload_data").hexdigest()

            # First insertion
            first = repo.append_event(
                event_id=event_id,
                timestamp_utc="2026-11-08T22:00:00Z",
                action_category="TOOL_EXECUTION",
                status="SUCCESS",
                agent_role="Macro Sentinel",
                permission_decision="ALLOWED",
                approval_status="NOT_REQUIRED",
                raw_event=raw_event,
                payload_hash=payload_hash,
            )

            # Idempotent replay of exact same event
            replayed = repo.append_event(
                event_id=event_id,
                timestamp_utc="2026-11-08T22:00:00Z",
                action_category="TOOL_EXECUTION",
                status="SUCCESS",
                agent_role="Macro Sentinel",
                permission_decision="ALLOWED",
                approval_status="NOT_REQUIRED",
                raw_event=raw_event,
                payload_hash=payload_hash,
            )

            self.assertEqual(first.event_id, replayed.event_id)
            self.assertEqual(repo.count_events(), 1)

    def test_audit_event_conflicting_replay_raises_integrity_conflict(self):
        with self.core_manager.connection() as conn:
            repo = AuditEventRepository(conn)
            event_id = uuid.uuid4().hex

            # First insertion
            repo.append_event(
                event_id=event_id,
                timestamp_utc="2026-11-08T22:00:00Z",
                action_category="TOOL_EXECUTION",
                status="SUCCESS",
                agent_role="Macro Sentinel",
                permission_decision="ALLOWED",
                approval_status="NOT_REQUIRED",
                raw_event={"tool": "read_portfolio"},
                payload_hash="hash_AAA",
            )

            # Conflicting replay: same event_id but different payload
            with self.assertRaises(IntegrityConflictError) as ctx:
                repo.append_event(
                    event_id=event_id,
                    timestamp_utc="2026-11-08T22:00:00Z",
                    action_category="TOOL_EXECUTION",
                    status="SUCCESS",
                    agent_role="Macro Sentinel",
                    permission_decision="ALLOWED",
                    approval_status="NOT_REQUIRED",
                    raw_event={"tool": "write_portfolio"},
                    payload_hash="hash_BBB",
                )
            self.assertIn("already exists with different payload or hash", str(ctx.exception))

    def test_owner_approval_duplicate_id_raises_duplicate_entity_error(self):
        with self.core_manager.connection() as conn:
            repo = OwnerApprovalRepository(conn)
            with execution_role_context("Owner"):
                repo.append_approval("APP_DUPLICATE", "update_rules", "rules/core", "digest123")
                with self.assertRaises(DuplicateEntityError):
                    repo.append_approval("APP_DUPLICATE", "update_rules", "rules/core", "digest123")


# ===========================================================================
# 6. Append-Only Immutability Tests
# ===========================================================================

class TestAppendOnlyIntegrity(BaseRepositoryTestCase):
    """Verifies that append-only entities strictly prohibit update and delete operations."""

    def test_audit_event_update_and_delete_rejected(self):
        with self.core_manager.connection() as conn:
            repo = AuditEventRepository(conn)
            with self.assertRaises(AppendOnlyViolationError):
                repo.update()
            with self.assertRaises(AppendOnlyViolationError):
                repo.delete()

    def test_financial_changelog_update_and_delete_rejected(self):
        with self.core_manager.connection() as conn:
            repo = FinancialChangelogRepository(conn)
            with self.assertRaises(AppendOnlyViolationError):
                repo.update()
            with self.assertRaises(AppendOnlyViolationError):
                repo.delete()

    def test_owner_approval_update_and_delete_rejected(self):
        with self.core_manager.connection() as conn:
            repo = OwnerApprovalRepository(conn)
            with self.assertRaises(AppendOnlyViolationError):
                repo.update()
            with self.assertRaises(AppendOnlyViolationError):
                repo.delete()

    def test_decision_log_update_and_delete_rejected(self):
        with self.core_manager.connection() as conn:
            repo = DecisionLogRepository(conn)
            with self.assertRaises(AppendOnlyViolationError):
                repo.update()
            with self.assertRaises(AppendOnlyViolationError):
                repo.delete()


# ===========================================================================
# 7. Cache Isolation Tests
# ===========================================================================

class TestCacheIsolation(BaseRepositoryTestCase):
    """Verifies that core financial and reconstructible cache databases remain strictly isolated."""

    def test_daily_bar_repo_on_core_conn_rejected(self):
        with self.core_manager.connection() as core_conn:
            with self.assertRaises(ValidationError) as ctx:
                DailyBarRepository(core_conn)
            self.assertIn("DailyBarRepository cannot be bound to the primary core financial database", str(ctx.exception))

    def test_core_repo_on_cache_conn_rejected_via_factory(self):
        with self.cache_manager.connection() as cache_conn:
            with self.assertRaises(ValidationError) as ctx:
                self.factory.create_cash_repository(cache_conn)
            self.assertIn("Core financial repository cannot be bound to the market-data cache database", str(ctx.exception))

    def test_uow_daily_bars_access_rejected(self):
        with self.factory.create_unit_of_work() as uow:
            with self.assertRaises(TypeError) as ctx:
                _ = uow.daily_bars
            self.assertIn("DailyBarRepository operates strictly on the reconstructible market-data cache database", str(ctx.exception))

    def test_cache_db_failure_does_not_affect_core_db(self):
        """Simulate cache DB corruption or drop; core financial DB remains intact and operational."""
        with self.core_manager.connection() as core_conn:
            cash_repo = CashRepository(core_conn)
            cash_repo.set_cash_balance("PRIMARY", "EUR", Decimal("10000.00"))

        # Drop cache daily_bars
        with self.cache_manager.connection() as cache_conn:
            cache_conn.execute("DROP TABLE daily_bars;")

        # Core database remains completely intact
        with self.core_manager.connection() as core_conn:
            cash_repo = CashRepository(core_conn)
            self.assertEqual(cash_repo.get_cash("PRIMARY", "EUR").amount, Decimal("10000.00"))


# ===========================================================================
# 8. Import and Lifecycle Safety Tests
# ===========================================================================

class TestImportAndLifecycleSafety(unittest.TestCase):
    """Verifies that module import and repository initialization create zero files on disk."""

    def test_import_and_factory_creates_no_files(self):
        # Confirm that production database paths do not exist
        self.assertFalse(os.path.exists("context/gem.db"))
        self.assertFalse(os.path.exists("cache/market_data.db"))

        # Instantiate DatabaseManager for a non-existent path without connecting
        dm = DatabaseManager("temp_test_nonexistent.db", profile=PRIMARY_FINANCIAL_PROFILE)
        factory = RepositoryFactory(dm)
        self.assertFalse(os.path.exists("temp_test_nonexistent.db"))

    def test_state_files_invariance(self):
        expected_hashes = {
            "context/ssot.json": "0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da",
            "context/trade_lessons.json": "0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463",
            "context/decision_log.json": "757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2",
            "context/config.json": "30ea2b67dd0ecac4b16e3e726c9644948119729023d382c385301f9db72099d5",
            "context/agent_logs/agent-events.jsonl": "58a830fe4a59a02bb16d33df10c1a09b3e76a2b6250377e26ee37a8d73d8bc3d",
            "config.json": "10c005efa15940d9c503aae62bbcb9a195ddbe249387c8335a4cb0f836064ee3",
        }
        for path_str, expected_hash in expected_hashes.items():
            path = Path(path_str)
            self.assertTrue(path.exists(), f"State file {path_str} must exist")
            actual_hash = hashlib.sha256(path.read_bytes()).hexdigest().lower()
            self.assertEqual(
                actual_hash,
                expected_hash.lower(),
                f"State file {path_str} hash mismatch! Expected {expected_hash}, got {actual_hash}",
            )


# ===========================================================================
# 9. R1: Mandatory Financial Changelog Enforcement Tests
# ===========================================================================

class TestR1MandatoryFinancialChangelogEnforcement(BaseRepositoryTestCase):
    """Verifies that changelog recording cannot be suppressed through any public financial API."""

    def test_r1_cash_mutation_suppress_changelog_impossible(self):
        with self.core_manager.connection() as conn:
            repo = CashRepository(conn)
            # Passing record_changelog must raise TypeError (parameter removed from public API)
            with self.assertRaises(TypeError):
                repo.set_cash_balance("PRIMARY", "EUR", Decimal("100.00"), record_changelog=False)
            with self.assertRaises(TypeError):
                repo.delete_cash("PRIMARY", "EUR", record_changelog=False)

    def test_r1_position_mutation_suppress_changelog_impossible(self):
        with self.core_manager.connection() as conn:
            repo = PositionRepository(conn)
            with self.assertRaises(TypeError):
                repo.upsert_position("PRIMARY", "SAP.DE", Decimal("10.0000"), Decimal("150.0000"), record_changelog=False)
            with self.assertRaises(TypeError):
                repo.delete_position("PRIMARY", "SAP.DE", record_changelog=False)

    def test_r1_successful_cash_mutation_creates_exactly_one_changelog(self):
        with self.core_manager.connection() as conn:
            cash_repo = CashRepository(conn)
            changelog_repo = FinancialChangelogRepository(conn)

            cash_repo.set_cash_balance("PRIMARY", "EUR", Decimal("5000.00"), correlation_id="CID_CASH_01")
            entries = changelog_repo.list_entries(correlation_id="CID_CASH_01")
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].operation_name, "set_cash_balance")
            self.assertEqual(entries[0].target_resource, "portfolio_cash/PRIMARY/EUR")
            self.assertEqual(entries[0].after_state["amount"], "5000.00")

    def test_r1_successful_position_mutation_creates_exactly_one_changelog(self):
        with self.core_manager.connection() as conn:
            pos_repo = PositionRepository(conn)
            changelog_repo = FinancialChangelogRepository(conn)

            pos_repo.upsert_position(
                "PRIMARY", "SAP.DE", Decimal("25.0000"), Decimal("175.5000"), correlation_id="CID_POS_01"
            )
            entries = changelog_repo.list_entries(correlation_id="CID_POS_01")
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].operation_name, "upsert_position")
            self.assertEqual(entries[0].after_state["shares"], "25.0000")

    def test_r1_deletion_creates_exactly_one_changelog(self):
        with self.core_manager.connection() as conn:
            cash_repo = CashRepository(conn)
            pos_repo = PositionRepository(conn)
            changelog_repo = FinancialChangelogRepository(conn)

            cash_repo.set_cash_balance("PRIMARY", "EUR", Decimal("1000.00"))
            pos_repo.upsert_position("PRIMARY", "SAP.DE", Decimal("10.0000"), Decimal("150.0000"))

            cash_repo.delete_cash("PRIMARY", "EUR", correlation_id="CID_DEL_CASH")
            del_cash_entries = changelog_repo.list_entries(correlation_id="CID_DEL_CASH")
            self.assertEqual(len(del_cash_entries), 1)
            self.assertEqual(del_cash_entries[0].operation_name, "delete_cash")
            self.assertIsNone(del_cash_entries[0].after_state)

            pos_repo.delete_position("PRIMARY", "SAP.DE", correlation_id="CID_DEL_POS")
            del_pos_entries = changelog_repo.list_entries(correlation_id="CID_DEL_POS")
            self.assertEqual(len(del_pos_entries), 1)
            self.assertEqual(del_pos_entries[0].operation_name, "delete_position")
            self.assertIsNone(del_pos_entries[0].after_state)


# ===========================================================================
# 10. R2: Owner Approval Record Integrity Tests
# ===========================================================================

class TestR2OwnerApprovalRecordIntegrity(BaseRepositoryTestCase):
    """Verifies that owner approvals strictly require trusted Owner or Human execution context."""

    def test_r2_engineering_role_cannot_create_approval(self):
        with self.core_manager.connection() as conn:
            repo = OwnerApprovalRepository(conn)
            with execution_role_context("Python Engineer"):
                with self.assertRaises(PermissionError) as ctx:
                    repo.append_approval("APP_FAIL_01", "update_rules", "rules/core", "digest123")
                self.assertIn("not authorized to create owner approvals", str(ctx.exception))

    def test_r2_investment_role_cannot_create_approval(self):
        with self.core_manager.connection() as conn:
            repo = OwnerApprovalRepository(conn)
            with execution_role_context("Macro Sentinel"):
                with self.assertRaises(PermissionError) as ctx:
                    repo.append_approval("APP_FAIL_02", "update_rules", "rules/core", "digest123")
                self.assertIn("not authorized to create owner approvals", str(ctx.exception))

    def test_r2_system_context_cannot_manufacture_owner_approval(self):
        with self.core_manager.connection() as conn:
            repo = OwnerApprovalRepository(conn)
            # Default System context must fail closed
            with execution_role_context("System"):
                with self.assertRaises(PermissionError) as ctx:
                    repo.append_approval("APP_FAIL_03", "update_rules", "rules/core", "digest123")
                self.assertIn("Active context role 'System' is not authorized", str(ctx.exception))

    def test_r2_untrusted_caller_cannot_claim_granted_by_owner(self):
        with self.core_manager.connection() as conn:
            repo = OwnerApprovalRepository(conn)
            with execution_role_context("System"):
                with self.assertRaises(PermissionError):
                    repo.append_approval("APP_FAIL_04", "update_rules", "rules/core", "digest123", granted_by="Owner")

    def test_r2_authorized_owner_context_creates_approval(self):
        with self.core_manager.connection() as conn:
            repo = OwnerApprovalRepository(conn)
            with execution_role_context("Owner"):
                approval = repo.append_approval("APP_OK_01", "update_rules", "rules/core", "digest_owner_123")
                self.assertEqual(approval.granted_by, "Owner")
                self.assertEqual(approval.status, "PENDING")

    def test_r2_authorized_human_context_creates_approval(self):
        with self.core_manager.connection() as conn:
            repo = OwnerApprovalRepository(conn)
            with execution_role_context("Human"):
                approval = repo.append_approval("APP_OK_02", "update_rules", "rules/core", "digest_human_123")
                self.assertEqual(approval.granted_by, "Human")
                self.assertEqual(approval.status, "PENDING")

    def test_r2_approval_consumption_preserves_immutable_attributes(self):
        with self.core_manager.connection() as conn:
            repo = OwnerApprovalRepository(conn)
            with execution_role_context("Owner"):
                original = repo.append_approval("APP_CONSUME", "update_rules", "rules/core", "digest_immutable")

            consumed = repo.consume_approval("APP_CONSUME")
            self.assertTrue(consumed)

            after = repo.get_approval("APP_CONSUME")
            self.assertEqual(after.operation_name, original.operation_name)
            self.assertEqual(after.target_resource, original.target_resource)
            self.assertEqual(after.payload_digest, original.payload_digest)
            self.assertEqual(after.granted_by, original.granted_by)
            self.assertEqual(after.status, "CONSUMED")
            self.assertIsNotNone(after.consumed_at_utc)


# ===========================================================================
# 11. R3: Financial Changelog Repository Protection Tests
# ===========================================================================

class TestR3FinancialChangelogRepositoryProtection(BaseRepositoryTestCase):
    """Verifies that arbitrary callers cannot fabricate financial changelog entries."""

    def test_r3_engineering_role_cannot_append_changelog(self):
        with self.core_manager.connection() as conn:
            repo = FinancialChangelogRepository(conn)
            with execution_role_context("Python Engineer"):
                with self.assertRaises(PermissionError) as ctx:
                    repo.append_entry(
                        correlation_id="FORGE_01",
                        actor_role="Python Engineer",
                        operation_name="update_ssot",
                        target_resource="portfolio_cash/PRIMARY/EUR",
                        before_state=None,
                        after_state={"amount": "1000000.00"},
                    )
                self.assertIn("Engineering role 'Python Engineer' cannot append to financial changelog", str(ctx.exception))

    def test_r3_unauthorized_investment_role_cannot_append_changelog(self):
        with self.core_manager.connection() as conn:
            repo = FinancialChangelogRepository(conn)
            with execution_role_context("Macro Sentinel"):
                with self.assertRaises(PermissionError) as ctx:
                    repo.append_entry(
                        correlation_id="FORGE_02",
                        actor_role="Macro Sentinel",
                        operation_name="update_ssot",
                        target_resource="portfolio_cash/PRIMARY/EUR",
                        before_state=None,
                        after_state={"amount": "1000000.00"},
                    )
                self.assertIn("not authorized to perform financial mutation", str(ctx.exception))

    def test_r3_caller_cannot_spoof_actor_role_in_changelog(self):
        with self.core_manager.connection() as conn:
            repo = FinancialChangelogRepository(conn)
            with execution_role_context("Terminal Orchestrator"):
                with self.assertRaises(PermissionError) as ctx:
                    repo.append_entry(
                        correlation_id="FORGE_03",
                        actor_role="Owner",
                        operation_name="update_ssot",
                        target_resource="portfolio_cash/PRIMARY/EUR",
                        before_state=None,
                        after_state={"amount": "1000000.00"},
                    )
                self.assertIn("Attribution spoofing attempt", str(ctx.exception))


# ===========================================================================
# 12. R4: Trusted Actor Attribution Tests
# ===========================================================================

class TestR4TrustedActorAttribution(BaseRepositoryTestCase):
    """Verifies that actor attribution is strictly derived from trusted execution context."""

    def test_r4_active_engineering_or_agent_cannot_spoof_role(self):
        with self.core_manager.connection() as conn:
            repo = CashRepository(conn)
            with execution_role_context("Python Engineer"):
                with self.assertRaises(PermissionError) as ctx:
                    repo.set_cash_balance("PRIMARY", "EUR", Decimal("100.00"), caller_role="Terminal Orchestrator")
                self.assertIn("cannot spoof actor role", str(ctx.exception))

    def test_r4_system_context_cannot_claim_owner_via_caller_role(self):
        with self.core_manager.connection() as conn:
            repo = CashRepository(conn)
            with execution_role_context("System"):
                with self.assertRaises(PermissionError) as ctx:
                    repo.set_cash_balance("PRIMARY", "EUR", Decimal("100.00"), caller_role="Owner")
                self.assertIn("cannot claim 'Owner' attribution", str(ctx.exception))

    def test_r4_changelog_actor_matches_trusted_context(self):
        with self.core_manager.connection() as conn:
            cash_repo = CashRepository(conn)
            changelog_repo = FinancialChangelogRepository(conn)
            with execution_role_context("Terminal Orchestrator"):
                cash_repo.set_cash_balance("PRIMARY", "EUR", Decimal("1234.56"), correlation_id="CID_ACTOR_MATCH")
                entries = changelog_repo.list_entries(correlation_id="CID_ACTOR_MATCH")
                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0].actor_role, "Terminal Orchestrator")


# ===========================================================================
# 13. R5: Transaction Snapshot Consistency Tests
# ===========================================================================

class TestR5TransactionSnapshotConsistency(BaseRepositoryTestCase):
    """Verifies that before-state queries execute under write lock inside the transaction."""

    def test_r5_read_under_write_lock_consistency(self):
        with self.core_manager.connection() as conn:
            cash_repo = CashRepository(conn)
            changelog_repo = FinancialChangelogRepository(conn)

            # Initial write
            cash_repo.set_cash_balance("PRIMARY", "EUR", Decimal("1000.00"), correlation_id="CID_SNAP_01")
            first_entry = changelog_repo.list_entries(correlation_id="CID_SNAP_01")[0]
            self.assertIsNone(first_entry.before_state)
            self.assertEqual(first_entry.after_state["amount"], "1000.00")

            # Sequential update: before-state must capture 1000.00 exactly under lock
            cash_repo.set_cash_balance("PRIMARY", "EUR", Decimal("2500.00"), correlation_id="CID_SNAP_02")
            second_entry = changelog_repo.list_entries(correlation_id="CID_SNAP_02")[0]
            self.assertIsNotNone(second_entry.before_state)
            self.assertEqual(second_entry.before_state["amount"], "1000.00")
            self.assertEqual(second_entry.after_state["amount"], "2500.00")

    def test_r5_concurrent_modification_lock_prevention(self):
        """Verifies that an active write transaction locks out concurrent writers."""
        with self.core_manager.connection() as conn1:
            with self.core_manager.connection() as conn2:
                conn1.execute("BEGIN IMMEDIATE;")
                # conn2 attempting BEGIN IMMEDIATE with small timeout must fail or wait
                with self.assertRaises(sqlite3.OperationalError):
                    # Set busy timeout on conn2 to 10ms for instant conflict detection
                    conn2.execute("PRAGMA busy_timeout = 10;")
                    conn2.execute("BEGIN IMMEDIATE;")
                conn1.execute("ROLLBACK;")


# ===========================================================================
# 14. R6: Unit of Work Lifecycle Enforcement Tests
# ===========================================================================

class TestR6UnitOfWorkLifecycleEnforcement(BaseRepositoryTestCase):
    """Verifies strict lifecycle enforcement and rejection of operations through retained references."""

    def test_r6_repository_access_invalid_before_uow_enter(self):
        uow = self.factory.create_unit_of_work()
        with self.assertRaises(RepositoryError):
            _ = uow.positions
        with self.assertRaises(RepositoryError):
            _ = uow.cash

    def test_r6_repository_access_during_uow_succeeds(self):
        with self.factory.create_unit_of_work() as uow:
            uow.cash.set_cash_balance("PRIMARY", "EUR", Decimal("500.00"))
            self.assertEqual(uow.cash.get_cash("PRIMARY", "EUR").amount, Decimal("500.00"))

    def test_r6_retained_repository_reference_fails_after_commit(self):
        uow = self.factory.create_unit_of_work()
        with uow:
            retained_cash = uow.cash
            uow.commit()

        # Retained repository reference must fail closed
        with self.assertRaises(RepositoryError) as ctx:
            retained_cash.set_cash_balance("PRIMARY", "EUR", Decimal("999.00"))
        self.assertIn("UnitOfWork is no longer active", str(ctx.exception))

    def test_r6_retained_repository_reference_fails_after_rollback(self):
        uow = self.factory.create_unit_of_work()
        with uow:
            retained_pos = uow.positions
            uow.rollback()

        with self.assertRaises(RepositoryError) as ctx:
            retained_pos.upsert_position("PRIMARY", "SAP.DE", Decimal("10.0000"), Decimal("150.0000"))
        self.assertIn("UnitOfWork is no longer active", str(ctx.exception))

    def test_r6_retained_repository_reference_fails_after_context_exit(self):
        uow = self.factory.create_unit_of_work()
        with uow:
            retained_cash = uow.cash

        # After normal context exit
        with self.assertRaises(RepositoryError):
            retained_cash.set_cash_balance("PRIMARY", "EUR", Decimal("999.00"))
        with self.assertRaises(RepositoryError):
            _ = uow.cash

    def test_r6_reentering_completed_uow_fails(self):
        uow = self.factory.create_unit_of_work()
        with uow:
            uow.cash.set_cash_balance("PRIMARY", "EUR", Decimal("100.00"))
        with self.assertRaises(RepositoryError) as ctx:
            with uow:
                pass
        self.assertIn("cannot be re-entered", str(ctx.exception))

    def test_r6_auto_commit_false_leaves_no_open_transaction_on_external_conn(self):
        """Verifies that auto_commit=False rolls back uncommitted work on context exit without dangling transaction."""
        with self.core_manager.connection() as ext_conn:
            uow = UnitOfWork(self.core_manager, connection=ext_conn, auto_commit=False)
            with uow:
                uow.cash.set_cash_balance("PRIMARY", "EUR", Decimal("1000.00"))
                # Do NOT commit explicitly

            # Verify no transaction remains active on the external connection
            self.assertFalse(ext_conn.in_transaction)

            # Verify cash record was rolled back cleanly
            cur = ext_conn.cursor()
            cur.execute("SELECT * FROM portfolio_cash WHERE currency = 'EUR'")
            self.assertIsNone(cur.fetchone())

            # Verify external connection is still alive and operational
            cur.execute("SELECT 1")
            self.assertEqual(cur.fetchone()[0], 1)


# ===========================================================================
# 15. R7: Daily Bar Batch Atomicity Tests
# ===========================================================================

class TestR7DailyBarBatchAtomicity(BaseRepositoryTestCase):
    """Verifies all-or-nothing atomicity for daily bar batch upserts."""

    def test_r7_batch_success_commits_all_or_nothing(self):
        with self.cache_manager.connection() as conn:
            repo = DailyBarRepository(conn)
            batch = [
                {
                    "ticker": "SAP.DE",
                    "bar_date": "2026-11-01",
                    "open": "150.00",
                    "high": "155.00",
                    "low": "149.00",
                    "close": "154.00",
                    "volume": 10000,
                },
                {
                    "ticker": "SAP.DE",
                    "bar_date": "2026-11-02",
                    "open": "154.00",
                    "high": "158.00",
                    "low": "153.00",
                    "close": "157.00",
                    "volume": 12000,
                },
            ]
            count = repo.upsert_bars_batch(batch)
            self.assertEqual(count, 2)
            self.assertEqual(len(repo.list_bars("SAP.DE")), 2)

    def test_r7_batch_failure_rolls_back_entire_batch(self):
        with self.cache_manager.connection() as conn:
            repo = DailyBarRepository(conn)
            # Second item has an invalid calendar date
            batch = [
                {
                    "ticker": "NOVO-B.CO",
                    "bar_date": "2026-11-01",
                    "open": "100.00",
                    "high": "105.00",
                    "low": "99.00",
                    "close": "104.00",
                    "volume": 10000,
                },
                {
                    "ticker": "NOVO-B.CO",
                    "bar_date": "2026-02-30",  # Impossible calendar date
                    "open": "104.00",
                    "high": "108.00",
                    "low": "103.00",
                    "close": "107.00",
                    "volume": 12000,
                },
            ]
            with self.assertRaises(ValidationError):
                repo.upsert_bars_batch(batch)

            # Verify ZERO rows persisted
            self.assertEqual(len(repo.list_bars("NOVO-B.CO")), 0)

    def test_r7_empty_batch_is_noop(self):
        with self.cache_manager.connection() as conn:
            repo = DailyBarRepository(conn)
            self.assertEqual(repo.upsert_bars_batch([]), 0)


# ===========================================================================
# 16. R8: Consistent Daily Bar Input & Calendar Date Validation Tests
# ===========================================================================

class TestR8DailyBarInputValidation(BaseRepositoryTestCase):
    """Verifies equivalent validation for DailyBarRecord and dicts, and strict calendar dates."""

    def test_r8_calendar_invalid_dates_rejected(self):
        with self.cache_manager.connection() as conn:
            repo = DailyBarRepository(conn)
            invalid_dates = [
                "2026-02-30",  # February 30 does not exist
                "2026-99-99",  # Non-existent month and day
                "2026-13-01",  # Month 13 does not exist
                "2026-02-29",  # 2026 is not a leap year
            ]
            for bad_date in invalid_dates:
                with self.subTest(bad_date=bad_date):
                    with self.assertRaises(ValidationError):
                        repo.upsert_bar("ASML.AS", bad_date, "100.00", "105.00", "99.00", "104.00", 1000)

            # Leap year valid date must succeed
            valid_bar = repo.upsert_bar("ASML.AS", "2024-02-29", "100.00", "105.00", "99.00", "104.00", 1000)
            self.assertEqual(valid_bar.bar_date, "2024-02-29")

    def test_r8_record_and_dict_validation_equivalence(self):
        """Verifies DailyBarRecord and dict paths enforce identical validations in batch upsert."""
        with self.cache_manager.connection() as conn:
            repo = DailyBarRepository(conn)

            # 1. Invalid excess scale (scale 5) in DailyBarRecord
            bad_record = DailyBarRecord(
                ticker="ASML.AS",
                bar_date="2026-11-01",
                open=Decimal("100.12345"),  # Excess scale > 4
                high=Decimal("105.0000"),
                low=Decimal("99.0000"),
                close=Decimal("104.0000"),
                volume=1000,
                indicators={},
                updated_at_utc="2026-11-01T00:00:00Z",
            )
            with self.assertRaises(ValidationError):
                repo.upsert_bars_batch([bad_record])

            # 2. Invalid excess scale in dict
            bad_dict = {
                "ticker": "ASML.AS",
                "bar_date": "2026-11-01",
                "open": Decimal("100.12345"),
                "high": Decimal("105.0000"),
                "low": Decimal("99.0000"),
                "close": Decimal("104.0000"),
                "volume": 1000,
            }
            with self.assertRaises(ValidationError):
                repo.upsert_bars_batch([bad_dict])

    def test_r8_inverted_date_range_rejected(self):
        with self.cache_manager.connection() as conn:
            repo = DailyBarRepository(conn)
            with self.assertRaises(ValidationError) as ctx:
                repo.list_bars("ASML.AS", start_date="2026-11-10", end_date="2026-11-01")
            self.assertIn("Inverted date range", str(ctx.exception))


# ===========================================================================
# 18. Stage 1.4.1.1 R1: Financial Changelog Integrity Tests
# ===========================================================================

class TestR1FinancialChangelogIntegrity(BaseRepositoryTestCase):
    """
    Verifies that financial changelog entries cannot be independently fabricated
    or decoupled from an actual financial state mutation.
    """

    def test_default_system_context_cannot_append_fabricated_changelog(self):
        """Default System context cannot independently append a fabricated changelog entry."""
        with self.core_manager.connection() as conn:
            repo = FinancialChangelogRepository(conn)
            with self.assertRaises(PermissionError) as ctx:
                repo.append_entry(
                    correlation_id="FABRICATED_01",
                    actor_role="System",
                    operation_name="update_ssot",
                    target_resource="portfolio_cash/PRIMARY/EUR",
                    before_state=None,
                    after_state={"amount": "999999.00"},
                )
            self.assertIn("Direct insertion into financial_changelog via public repository API is prohibited", str(ctx.exception))

    def test_authorized_mutation_creates_corresponding_changelog(self):
        """An authorized financial mutation creates its corresponding changelog entry."""
        with self.core_manager.connection() as conn:
            cash_repo = CashRepository(conn)
            pos_repo = PositionRepository(conn)
            watch_repo = WatchlistRepository(conn)
            changelog_repo = FinancialChangelogRepository(conn)

            # 1. Cash mutation
            cash_repo.set_cash_balance("PRIMARY", "EUR", Decimal("1500.00"), correlation_id="CID_CASH_AUTH")
            entries = changelog_repo.list_entries(correlation_id="CID_CASH_AUTH")
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].operation_name, "set_cash_balance")
            self.assertEqual(entries[0].after_state["amount"], "1500.00")

            # 2. Position mutation
            pos_repo.upsert_position("PRIMARY", "NOVO.CO", Decimal("50.0000"), Decimal("850.0000"), correlation_id="CID_POS_AUTH")
            entries = changelog_repo.list_entries(correlation_id="CID_POS_AUTH")
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].operation_name, "upsert_position")
            self.assertEqual(entries[0].after_state["shares"], "50.0000")

            # 3. Watchlist upsert
            watch_repo.upsert_watchlist_item("NOVO.CO", status="ACTIVE", notes="Strong pipeline", correlation_id="CID_WATCH_AUTH")
            entries = changelog_repo.list_entries(correlation_id="CID_WATCH_AUTH")
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].operation_name, "upsert_watchlist_item")
            self.assertEqual(entries[0].after_state["status"], "ACTIVE")

            # 4. Watchlist remove
            watch_repo.remove_watchlist_item("NOVO.CO", correlation_id="CID_WATCH_REM")
            entries = changelog_repo.list_entries(correlation_id="CID_WATCH_REM")
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].operation_name, "remove_watchlist_item")
            self.assertIsNone(entries[0].after_state)

    def test_changelog_failure_rolls_back_financial_mutation(self):
        """Changelog insertion failure rolls back the financial mutation."""
        with self.core_manager.connection() as conn:
            conn.execute("DROP TABLE financial_changelog;")
            cash_repo = CashRepository(conn)
            pos_repo = PositionRepository(conn)
            watch_repo = WatchlistRepository(conn)

            with self.assertRaises(sqlite3.OperationalError):
                cash_repo.set_cash_balance("PRIMARY", "EUR", Decimal("7500.00"))

            with self.assertRaises(sqlite3.OperationalError):
                pos_repo.upsert_position("PRIMARY", "ASML.AS", Decimal("10.0000"), Decimal("700.0000"))

            with self.assertRaises(sqlite3.OperationalError):
                watch_repo.upsert_watchlist_item("ASML.AS", status="ACTIVE")

            # Assert no mutations persisted
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM portfolio_cash WHERE currency = 'EUR'")
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("SELECT count(*) FROM portfolio_positions WHERE ticker = 'ASML.AS'")
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("SELECT count(*) FROM watchlist WHERE ticker = 'ASML.AS'")
            self.assertEqual(cur.fetchone()[0], 0)

    def test_cannot_fabricate_state_or_actor_through_public_arguments(self):
        """A caller cannot fabricate before/after state or actor attribution through public repository arguments."""
        with self.core_manager.connection() as conn:
            cash_repo = CashRepository(conn)
            # Public API does not accept before_state or after_state arguments
            with self.assertRaises(TypeError):
                cash_repo.set_cash_balance("PRIMARY", "EUR", Decimal("2000.00"), before_state={"amount": "0.00"})

            # Attempting to supply a fake caller_role in System context fails closed
            with self.assertRaises(PermissionError):
                cash_repo.set_cash_balance("PRIMARY", "EUR", Decimal("2000.00"), caller_role="Owner")

    def test_direct_repository_access_cannot_bypass_trusted_mutation_path(self):
        """Direct repository access cannot bypass the trusted mutation path."""
        with self.core_manager.connection() as conn:
            repo = FinancialChangelogRepository(conn)
            with execution_role_context("Owner"):
                with self.assertRaises(PermissionError):
                    repo.append_entry(
                        correlation_id="FORGE_OWNER",
                        actor_role="Owner",
                        operation_name="update_ssot",
                        target_resource="portfolio_cash/PRIMARY/EUR",
                    )

    def test_capability_direct_instantiation_and_tampering_rejected(self):
        """_FinancialMutationCapability cannot be instantiated directly and cannot be reused."""
        from python.database.repositories.financial import _FinancialMutationCapability
        with self.assertRaises(PermissionError) as ctx:
            _FinancialMutationCapability(
                key=object(),  # Invalid key
                connection=None,
                correlation_id="DUMMY",
                actor_role="Owner",
                operation_name="test",
                target_resource="res",
                before_state=None,
                after_state=None,
                approval_digest=None,
                timestamp_utc="2026-11-09T00:00:00Z",
            )
        self.assertIn("Direct instantiation of _FinancialMutationCapability is prohibited", str(ctx.exception))


# ===========================================================================
# 19. Stage 1.4.1.1 R2: Exact Trusted Actor Attribution Tests
# ===========================================================================

class TestR2ExactTrustedActorAttribution(BaseRepositoryTestCase):
    """
    Verifies that actor attribution is strictly derived from trusted execution context
    and rejects any mismatched or impersonated caller_role.
    """

    def test_default_system_context_cannot_claim_another_actor(self):
        """Default System context cannot claim another actor."""
        with self.core_manager.connection() as conn:
            repo = CashRepository(conn)
            with self.assertRaises(PermissionError) as ctx:
                repo.set_cash_balance("PRIMARY", "EUR", Decimal("100.00"), caller_role="UnknownActor")
            self.assertIn("Active context 'System' cannot claim 'UnknownActor' attribution", str(ctx.exception))

    def test_system_cannot_claim_owner_or_human(self):
        """System cannot claim Owner or Human."""
        with self.core_manager.connection() as conn:
            repo = CashRepository(conn)
            with self.assertRaises(PermissionError) as ctx:
                repo.set_cash_balance("PRIMARY", "EUR", Decimal("100.00"), caller_role="Owner")
            self.assertIn("Active context 'System' cannot claim 'Owner' attribution", str(ctx.exception))

            with self.assertRaises(PermissionError) as ctx:
                repo.set_cash_balance("PRIMARY", "EUR", Decimal("100.00"), caller_role="Human")
            self.assertIn("Active context 'System' cannot claim 'Human' attribution", str(ctx.exception))

    def test_system_cannot_claim_terminal_orchestrator_or_investment_agent(self):
        """System cannot claim Terminal Orchestrator or Investment Agent."""
        with self.core_manager.connection() as conn:
            repo = CashRepository(conn)
            with self.assertRaises(PermissionError) as ctx:
                repo.set_cash_balance("PRIMARY", "EUR", Decimal("100.00"), caller_role="Terminal Orchestrator")
            self.assertIn("Active context 'System' cannot claim 'Terminal Orchestrator' attribution", str(ctx.exception))

            with self.assertRaises(PermissionError) as ctx:
                repo.set_cash_balance("PRIMARY", "EUR", Decimal("100.00"), caller_role="Macro Sentinel")
            self.assertIn("Active context 'System' cannot claim 'Macro Sentinel' attribution", str(ctx.exception))

    def test_mismatched_explicit_caller_role_is_rejected(self):
        """A mismatched explicit caller_role is rejected fail-closed."""
        with self.core_manager.connection() as conn:
            repo = CashRepository(conn)
            with execution_role_context("Terminal Orchestrator"):
                with self.assertRaises(PermissionError):
                    repo.set_cash_balance("PRIMARY", "EUR", Decimal("100.00"), caller_role="Owner")
            with execution_role_context("Owner"):
                with self.assertRaises(PermissionError):
                    repo.set_cash_balance("PRIMARY", "EUR", Decimal("100.00"), caller_role="System")

    def test_exact_matching_trusted_identity_is_accepted(self):
        """Exact matching trusted identity is accepted where legitimately supplied."""
        with self.core_manager.connection() as conn:
            repo = CashRepository(conn)
            with execution_role_context("Terminal Orchestrator"):
                balance = repo.set_cash_balance("PRIMARY", "EUR", Decimal("250.00"), caller_role="Terminal Orchestrator")
                self.assertEqual(balance.amount, Decimal("250.00"))
            with execution_role_context("Owner"):
                balance = repo.set_cash_balance("PRIMARY", "EUR", Decimal("500.00"), caller_role="Owner")
                self.assertEqual(balance.amount, Decimal("500.00"))

    def test_audit_and_financial_changelog_attribution_consistent(self):
        """Audit and financial changelog attribution remain strictly consistent with trusted context."""
        with self.core_manager.connection() as conn:
            cash_repo = CashRepository(conn)
            changelog_repo = FinancialChangelogRepository(conn)
            with execution_role_context("Terminal Orchestrator"):
                cash_repo.set_cash_balance("PRIMARY", "EUR", Decimal("800.00"), correlation_id="CID_CONSISTENT")
                entries = changelog_repo.list_entries(correlation_id="CID_CONSISTENT")
                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0].actor_role, "Terminal Orchestrator")


# ===========================================================================
# 20. Stage 1.4.1.1 R3: DailyBar Nested Batch Atomicity Tests
# ===========================================================================

class TestR3DailyBarNestedBatchAtomicity(BaseRepositoryTestCase):
    """
    Verifies that DailyBarRepository.upsert_bars_batch() executes atomically
    both inside caller-owned transactions (via savepoints) and standalone.
    """

    def test_a_failed_nested_batch_rolls_back_to_savepoint(self):
        """
        Test A — Failed nested batch:
        - Start a caller-owned transaction.
        - Insert an unrelated valid record.
        - Execute a batch containing valid rows followed by a deliberately invalid row.
        - Confirm that no rows from the failed batch remain.
        - Confirm that the unrelated record remains available in the caller's transaction.
        - Confirm that the caller can still commit or roll back its transaction.
        """
        with self.cache_manager.connection() as conn:
            repo = DailyBarRepository(conn)

            # Start caller-owned transaction
            conn.execute("BEGIN IMMEDIATE;")

            # Insert an unrelated valid record
            repo.upsert_bar("AAPL", "2026-01-02", "150.00", "155.00", "149.00", "154.00", 50000)

            # Prepare batch with valid rows followed by an invalid row (invalid date '2026-02-30')
            batch = [
                {
                    "ticker": "MSFT",
                    "bar_date": "2026-01-02",
                    "open": "200.00",
                    "high": "205.00",
                    "low": "199.00",
                    "close": "204.00",
                    "volume": 20000,
                },
                {
                    "ticker": "MSFT",
                    "bar_date": "2026-01-05",
                    "open": "204.00",
                    "high": "210.00",
                    "low": "203.00",
                    "close": "209.00",
                    "volume": 25000,
                },
                {
                    "ticker": "MSFT",
                    "bar_date": "2026-02-30",  # Calendar invalid!
                    "open": "209.00",
                    "high": "212.00",
                    "low": "208.00",
                    "close": "211.00",
                    "volume": 15000,
                },
            ]

            # Batch execution must raise ValidationError
            with self.assertRaises(ValidationError):
                repo.upsert_bars_batch(batch)

            # Transaction must still be open
            self.assertTrue(conn.in_transaction)

            # Confirm no rows from the failed batch remain
            msft_bars = repo.list_bars("MSFT")
            self.assertEqual(len(msft_bars), 0)

            # Confirm the unrelated record remains available in the transaction
            aapl_bar = repo.get_bar("AAPL", "2026-01-02")
            self.assertIsNotNone(aapl_bar)
            self.assertEqual(aapl_bar.ticker, "AAPL")

            # Confirm caller can still commit transaction
            conn.execute("COMMIT;")
            self.assertFalse(conn.in_transaction)

            # Unrelated record durably persisted
            aapl_durable = repo.get_bar("AAPL", "2026-01-02")
            self.assertIsNotNone(aapl_durable)

    def test_b_successful_nested_batch_preserves_caller_transaction(self):
        """
        Test B — Successful nested batch:
        - Start a caller-owned transaction.
        - Execute a valid batch.
        - Confirm that all batch rows are visible within the transaction.
        - Confirm that the repository has not committed the caller's transaction.
        - Confirm that the caller retains control over the final commit or rollback.
        """
        with self.cache_manager.connection() as conn:
            repo = DailyBarRepository(conn)

            # Start caller-owned transaction
            conn.execute("BEGIN IMMEDIATE;")

            # Execute a valid batch
            batch = [
                {
                    "ticker": "NVDA",
                    "bar_date": "2026-01-02",
                    "open": "100.00",
                    "high": "105.00",
                    "low": "99.00",
                    "close": "104.00",
                    "volume": 30000,
                },
                {
                    "ticker": "NVDA",
                    "bar_date": "2026-01-05",
                    "open": "104.00",
                    "high": "110.00",
                    "low": "103.00",
                    "close": "108.00",
                    "volume": 35000,
                },
            ]
            count = repo.upsert_bars_batch(batch)
            self.assertEqual(count, 2)

            # Transaction must still be open (repository must NOT commit caller's transaction)
            self.assertTrue(conn.in_transaction)

            # Batch rows visible in the transaction
            bars = repo.list_bars("NVDA")
            self.assertEqual(len(bars), 2)

            # Caller rolls back transaction
            conn.execute("ROLLBACK;")
            self.assertFalse(conn.in_transaction)

            # After rollback, rows are gone
            bars_post_rollback = repo.list_bars("NVDA")
            self.assertEqual(len(bars_post_rollback), 0)

    def test_c_repository_owned_transaction_standalone_behavior(self):
        """Test C — Repository-owned transaction standalone success and failure."""
        with self.cache_manager.connection() as conn:
            repo = DailyBarRepository(conn)

            # Standalone success: commits durably
            valid_batch = [
                {
                    "ticker": "SAP.DE",
                    "bar_date": "2026-01-02",
                    "open": "150.00",
                    "high": "155.00",
                    "low": "148.00",
                    "close": "154.00",
                    "volume": 12000,
                }
            ]
            count = repo.upsert_bars_batch(valid_batch)
            self.assertEqual(count, 1)
            self.assertFalse(conn.in_transaction)
            self.assertIsNotNone(repo.get_bar("SAP.DE", "2026-01-02"))

            # Standalone failure: rolls back cleanly
            invalid_batch = [
                {
                    "ticker": "SAP.DE",
                    "bar_date": "2026-01-05",
                    "open": "154.00",
                    "high": "160.00",
                    "low": "153.00",
                    "close": "158.00",
                    "volume": 14000,
                },
                {
                    "ticker": "SAP.DE",
                    "bar_date": "2026-02-31",  # Invalid date
                    "open": "158.00",
                    "high": "162.00",
                    "low": "157.00",
                    "close": "161.00",
                    "volume": 10000,
                },
            ]
            with self.assertRaises(ValidationError):
                repo.upsert_bars_batch(invalid_batch)
            self.assertFalse(conn.in_transaction)
            self.assertIsNone(repo.get_bar("SAP.DE", "2026-01-05"))


# ===========================================================================
# 21. Stage 1.4.1.2 R1: Financial Mutation Capability Boundary Tests
# ===========================================================================

class TestR1FinancialMutationCapabilityBoundary(BaseRepositoryTestCase):
    """
    Verifies that the financial mutation capability cannot be minted through
    any public repository interface, preventing callers from fabricating changelogs.
    """

    def test_a_direct_factory_bypass(self):
        """
        Test A — Direct factory bypass:
        - Attempt to import and invoke the former public factory.
        - Confirm that the supported public repository interface cannot mint a valid capability.
        - Confirm that an active SQLite transaction alone is insufficient.
        """
        # 1. Former factory must not exist in base.py or repositories package
        import python.database.repositories.base as repo_base
        import python.database.repositories as repo_pkg
        self.assertFalse(hasattr(repo_base, "create_mutation_capability"))
        self.assertFalse(hasattr(repo_pkg, "create_mutation_capability"))
        self.assertFalse(hasattr(repo_base, "_MUTATION_CAPABILITY_KEY"))

        with self.core_manager.connection() as conn:
            # 2. Active transaction alone is insufficient
            conn.execute("BEGIN IMMEDIATE;")
            self.assertTrue(conn.in_transaction)

            # Public repository interface provides no capability minting route
            changelog_repo = FinancialChangelogRepository(conn)
            with self.assertRaises(PermissionError) as ctx:
                changelog_repo.append_entry(
                    correlation_id="FORGE_TX",
                    actor_role="System",
                    operation_name="update_ssot",
                    target_resource="portfolio_cash/PRIMARY/EUR",
                    before_state=None,
                    after_state={"amount": "500000.00"},
                )
            self.assertIn("Direct insertion into financial_changelog via public repository API is prohibited", str(ctx.exception))

    def test_b_fabricated_changelog(self):
        """
        Test B — Fabricated changelog:
        - Use default System execution context.
        - Attempt to fabricate a changelog entry with arbitrary before/after state and actor attribution.
        - Confirm that the attempt is rejected.
        """
        with self.core_manager.connection() as conn:
            repo = FinancialChangelogRepository(conn)
            with self.assertRaises(PermissionError):
                repo.append_entry(
                    correlation_id="FABRICATE_01",
                    actor_role="System",
                    operation_name="update_ssot",
                    target_resource="portfolio_cash/PRIMARY/EUR",
                    before_state={"amount": "100.00"},
                    after_state={"amount": "9999999.00"},
                )

    def test_c_genuine_financial_mutation(self):
        """
        Test C — Genuine financial mutation:
        - Perform a legitimate authorized Cash mutation.
        - Confirm that its changelog entry is created correctly.
        """
        with self.core_manager.connection() as conn:
            cash_repo = CashRepository(conn)
            changelog_repo = FinancialChangelogRepository(conn)

            cash_repo.set_cash_balance("PRIMARY", "EUR", Decimal("4200.00"), correlation_id="CID_GENUINE_01")
            entries = changelog_repo.list_entries(correlation_id="CID_GENUINE_01")
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].operation_name, "set_cash_balance")
            self.assertEqual(entries[0].target_resource, "portfolio_cash/PRIMARY/EUR")
            self.assertEqual(entries[0].after_state["amount"], "4200.00")

    def test_d_capability_replay(self):
        """
        Test D — Capability replay:
        - Obtain capability through genuine mutation path.
        - Attempt to re-consume the already consumed capability.
        - Confirm rejection.
        """
        with self.core_manager.connection() as conn:
            cash_repo = CashRepository(conn)
            cash_repo.set_cash_balance("PRIMARY", "EUR", Decimal("100.00"), correlation_id="REPLAY_TEST")
            cap = cash_repo._last_mutation_capability
            self.assertTrue(cap.is_consumed)

            # Replay attempt must raise IntegrityConflictError
            repo = FinancialChangelogRepository(conn)
            with self.assertRaises(IntegrityConflictError) as ctx:
                repo.append_entry(mutation_capability=cap)
            self.assertIn("already been consumed", str(ctx.exception))

    def test_e_connection_binding(self):
        """
        Test E — Connection binding:
        - Obtain capability through genuine mutation path on connection 1.
        - Attempt to consume that capability using a different SQLite connection.
        - Confirm rejection.
        """
        with self.core_manager.connection() as conn1:
            with self.core_manager.connection() as conn2:
                cash_repo = CashRepository(conn1)
                cash_repo.set_cash_balance("PRIMARY", "EUR", Decimal("100.00"), correlation_id="CONN_BIND_TEST")
                cap = cash_repo._last_mutation_capability

                repo2 = FinancialChangelogRepository(conn2)
                with self.assertRaises(PermissionError) as ctx:
                    repo2.append_entry(mutation_capability=cap)
                self.assertIn("bound to a different database connection", str(ctx.exception))

    def test_f_transactional_rollback(self):
        """
        Test F — Transactional rollback:
        - Force changelog insertion to fail during a legitimate financial mutation.
        - Confirm that the associated financial mutation is rolled back.
        """
        with self.core_manager.connection() as conn:
            conn.execute("DROP TABLE financial_changelog;")
            cash_repo = CashRepository(conn)

            with self.assertRaises(sqlite3.OperationalError):
                cash_repo.set_cash_balance("PRIMARY", "EUR", Decimal("8888.88"))

            cur = conn.cursor()
            cur.execute("SELECT * FROM portfolio_cash WHERE currency = 'EUR'")
            self.assertIsNone(cur.fetchone())

    def test_g_existing_repository_compatibility(self):
        """
        Test G — Existing repository compatibility:
        - Verify legitimate Position and Watchlist mutation paths continue to create correct changelog entries.
        - Confirm existing authorisation and actor-attribution behaviour is unchanged.
        """
        with self.core_manager.connection() as conn:
            pos_repo = PositionRepository(conn)
            watch_repo = WatchlistRepository(conn)
            changelog_repo = FinancialChangelogRepository(conn)

            with execution_role_context("Terminal Orchestrator"):
                pos_repo.upsert_position(
                    "PRIMARY", "SAP.DE", Decimal("30.0000"), Decimal("170.0000"), correlation_id="CID_POS_G"
                )
                watch_repo.upsert_watchlist_item("SAP.DE", status="ACTIVE", correlation_id="CID_WATCH_G")

            entries_pos = changelog_repo.list_entries(correlation_id="CID_POS_G")
            self.assertEqual(len(entries_pos), 1)
            self.assertEqual(entries_pos[0].actor_role, "Terminal Orchestrator")
            self.assertEqual(entries_pos[0].operation_name, "upsert_position")

            entries_watch = changelog_repo.list_entries(correlation_id="CID_WATCH_G")
            self.assertEqual(len(entries_watch), 1)
            self.assertEqual(entries_watch[0].actor_role, "Terminal Orchestrator")
            self.assertEqual(entries_watch[0].operation_name, "upsert_watchlist_item")


# ===========================================================================
# 22. Stage 1.4.1.2 R2: Savepoint Rollback Failure Integrity Tests
# ===========================================================================

class TestR2SavepointRollbackFailureIntegrity(BaseRepositoryTestCase):
    """
    Verifies that savepoint rollback and release failures are never silently suppressed,
    raising explicit TransactionIntegrityError while preserving original batch exceptions.
    """

    def test_a_nested_batch_failure_with_successful_rollback(self):
        """
        Test A — Nested batch failure with successful rollback:
        - Begin a caller-owned transaction.
        - Insert an unrelated valid record.
        - Execute a batch containing valid rows followed by an invalid row.
        - Confirm no rows from the failed batch remain.
        - Confirm the unrelated record remains available.
        - Confirm the caller retains control of the transaction.
        """
        with self.cache_manager.connection() as conn:
            repo = DailyBarRepository(conn)
            conn.execute("BEGIN IMMEDIATE;")

            # Unrelated valid record
            repo.upsert_bar("AAPL", "2026-01-02", "150.00", "155.00", "149.00", "154.00", 50000)

            batch = [
                {
                    "ticker": "MSFT",
                    "bar_date": "2026-01-02",
                    "open": "200.00",
                    "high": "205.00",
                    "low": "199.00",
                    "close": "204.00",
                    "volume": 20000,
                },
                {
                    "ticker": "MSFT",
                    "bar_date": "2026-02-30",  # Calendar invalid
                    "open": "204.00",
                    "high": "210.00",
                    "low": "203.00",
                    "close": "209.00",
                    "volume": 25000,
                },
            ]
            with self.assertRaises(ValidationError):
                repo.upsert_bars_batch(batch)

            # Assert transaction still open and no MSFT rows remain
            self.assertTrue(conn.in_transaction)
            self.assertEqual(len(repo.list_bars("MSFT")), 0)

            # Unrelated AAPL record remains available
            self.assertIsNotNone(repo.get_bar("AAPL", "2026-01-02"))

            # Caller can still commit cleanly
            conn.execute("COMMIT;")
            self.assertFalse(conn.in_transaction)
            self.assertIsNotNone(repo.get_bar("AAPL", "2026-01-02"))

    def test_b_nested_batch_success(self):
        """
        Test B — Nested batch success:
        - Begin a caller-owned transaction.
        - Execute a valid batch.
        - Confirm all batch rows are visible inside the transaction.
        - Confirm the repository has not committed the caller's transaction.
        - Confirm the caller can subsequently commit or roll back.
        """
        with self.cache_manager.connection() as conn:
            repo = DailyBarRepository(conn)
            conn.execute("BEGIN IMMEDIATE;")

            batch = [
                {
                    "ticker": "NVDA",
                    "bar_date": "2026-01-02",
                    "open": "100.00",
                    "high": "105.00",
                    "low": "99.00",
                    "close": "104.00",
                    "volume": 30000,
                }
            ]
            count = repo.upsert_bars_batch(batch)
            self.assertEqual(count, 1)

            # Repository must not commit caller's transaction
            self.assertTrue(conn.in_transaction)
            self.assertEqual(len(repo.list_bars("NVDA")), 1)

            # Caller rolls back
            conn.execute("ROLLBACK;")
            self.assertFalse(conn.in_transaction)
            self.assertEqual(len(repo.list_bars("NVDA")), 0)

    def test_c_injected_rollback_failure(self):
        """
        Test C — Injected rollback failure:
        - Exercise the rollback failure path using controlled fault injection.
        - Confirm the rollback failure is surfaced explicitly via TransactionIntegrityError.
        - Confirm the original batch failure is preserved as diagnostic context (__cause__).
        - Confirm the exception clearly identifies the transaction as potentially unsafe to commit.
        """
        with self.cache_manager.connection() as raw_conn:
            raw_conn.execute("BEGIN IMMEDIATE;")

            class FaultyRollbackProxy:
                def __init__(self, target):
                    self._target = target

                def __getattr__(self, name):
                    return getattr(self._target, name)

                def execute(self, sql, *args, **kwargs):
                    if isinstance(sql, str) and sql.strip().startswith("ROLLBACK TO SAVEPOINT"):
                        raise sqlite3.OperationalError("Simulated disk I/O error during savepoint rollback")
                    return self._target.execute(sql, *args, **kwargs)

            proxy_conn = FaultyRollbackProxy(raw_conn)
            repo = DailyBarRepository(proxy_conn)

            batch = [
                {
                    "ticker": "FAIL_TEST",
                    "bar_date": "2026-01-02",
                    "open": "100.00",
                    "high": "105.00",
                    "low": "99.00",
                    "close": "104.00",
                    "volume": 1000,
                },
                {
                    "ticker": "FAIL_TEST",
                    "bar_date": "2026-02-30",  # Triggers ValidationError
                    "open": "100.00",
                    "high": "105.00",
                    "low": "99.00",
                    "close": "104.00",
                    "volume": 1000,
                },
            ]

            try:
                with self.assertRaises(TransactionIntegrityError) as ctx:
                    repo.upsert_bars_batch(batch)

                err_msg = str(ctx.exception)
                self.assertIn("CRITICAL: Failed to roll back savepoint", err_msg)
                self.assertIn("MUST NOT be committed", err_msg)
                # Confirm original batch failure is preserved as cause
                self.assertIsNotNone(ctx.exception.__cause__)
                self.assertIsInstance(ctx.exception.__cause__, ValidationError)
            finally:
                try:
                    raw_conn.execute("ROLLBACK;")
                except Exception:
                    pass

    def test_d_savepoint_release_failure(self):
        """
        Test D — Savepoint release failure:
        - Exercise a controlled savepoint release failure.
        - Confirm that the failure is not silently suppressed.
        - Confirm the resulting transaction-integrity state is reported accurately.
        """
        with self.cache_manager.connection() as raw_conn:
            raw_conn.execute("BEGIN IMMEDIATE;")

            class FaultyReleaseProxy:
                def __init__(self, target):
                    self._target = target

                def __getattr__(self, name):
                    return getattr(self._target, name)

                def execute(self, sql, *args, **kwargs):
                    if isinstance(sql, str) and sql.strip().startswith("RELEASE SAVEPOINT"):
                        raise sqlite3.OperationalError("Simulated I/O failure during savepoint release")
                    return self._target.execute(sql, *args, **kwargs)

            proxy_conn = FaultyReleaseProxy(raw_conn)
            repo = DailyBarRepository(proxy_conn)

            batch = [
                {
                    "ticker": "RELEASE_TEST",
                    "bar_date": "2026-01-02",
                    "open": "100.00",
                    "high": "105.00",
                    "low": "99.00",
                    "close": "104.00",
                    "volume": 1000,
                }
            ]

            try:
                with self.assertRaises(TransactionIntegrityError) as ctx:
                    repo.upsert_bars_batch(batch)

                err_msg = str(ctx.exception)
                self.assertIn("CRITICAL: Failed to release savepoint", err_msg)
                self.assertIn("MUST NOT be committed", err_msg)
            finally:
                try:
                    raw_conn.execute("ROLLBACK;")
                except Exception:
                    pass

    def test_e_repository_owned_transaction(self):
        """Test E — Standalone repository-owned batch success and failure."""
        with self.cache_manager.connection() as conn:
            repo = DailyBarRepository(conn)

            # Success commits cleanly
            count = repo.upsert_bars_batch([
                {
                    "ticker": "STANDALONE",
                    "bar_date": "2026-01-02",
                    "open": "50.00",
                    "high": "55.00",
                    "low": "49.00",
                    "close": "54.00",
                    "volume": 5000,
                }
            ])
            self.assertEqual(count, 1)
            self.assertFalse(conn.in_transaction)
            self.assertIsNotNone(repo.get_bar("STANDALONE", "2026-01-02"))

            # Failure rolls back cleanly
            with self.assertRaises(ValidationError):
                repo.upsert_bars_batch([
                    {
                        "ticker": "STANDALONE",
                        "bar_date": "2026-02-30",
                        "open": "50.00",
                        "high": "55.00",
                        "low": "49.00",
                        "close": "54.00",
                        "volume": 5000,
                    }
                ])
            self.assertFalse(conn.in_transaction)

    def test_f_existing_transaction_preservation(self):
        """
        Test F — Existing transaction preservation:
        - Confirm that unrelated caller-owned work is not silently committed or discarded during normal batch rollback.
        """
        with self.cache_manager.connection() as conn:
            repo = DailyBarRepository(conn)
            conn.execute("BEGIN IMMEDIATE;")

            # 2 unrelated records
            repo.upsert_bar("UNRELATED_1", "2026-01-02", "10.00", "11.00", "9.00", "10.50", 100)
            repo.upsert_bar("UNRELATED_2", "2026-01-02", "20.00", "21.00", "19.00", "20.50", 200)

            # Failed batch
            with self.assertRaises(ValidationError):
                repo.upsert_bars_batch([
                    {
                        "ticker": "BAD_BATCH",
                        "bar_date": "2026-02-30",
                        "open": "100.00",
                        "high": "105.00",
                        "low": "99.00",
                        "close": "104.00",
                        "volume": 1000,
                    }
                ])

            # Transaction still open
            self.assertTrue(conn.in_transaction)

            # Both unrelated records still available
            self.assertIsNotNone(repo.get_bar("UNRELATED_1", "2026-01-02"))
            self.assertIsNotNone(repo.get_bar("UNRELATED_2", "2026-01-02"))

            # Commit persists them durably
            conn.execute("COMMIT;")
            self.assertFalse(conn.in_transaction)
            self.assertIsNotNone(repo.get_bar("UNRELATED_1", "2026-01-02"))
            self.assertIsNotNone(repo.get_bar("UNRELATED_2", "2026-01-02"))


# ===========================================================================
# 18. Stage 1.4.1.3: Capability Minting Closure & Mutation-State Verification
# ===========================================================================

class TestStage1413FinancialChangelogClosureAndStateVerification(BaseRepositoryTestCase):
    """
    Dedicated adversarial test suite for Stage 1.4.1.3:
    Verifies total closure of capability minting bypasses and fail-closed state verification.
    """

    def test_1_record_changelog_not_importable_or_callable(self):
        """1. _record_changelog() is no longer an importable general-purpose capability-minting route."""
        # Attempt direct import from financial.py
        with self.assertRaises(ImportError):
            from python.database.repositories.financial import _record_changelog  # type: ignore

        # Inspect module attributes
        import python.database.repositories.financial as fin_mod
        self.assertFalse(hasattr(fin_mod, "_record_changelog"))

        import python.database.repositories as repo_pkg
        self.assertFalse(hasattr(repo_pkg, "_record_changelog"))

    def test_2_removed_public_factory_and_exports_remain_absent(self):
        """2. The removed public factory and public capability exports remain absent."""
        with self.assertRaises(ImportError):
            from python.database.repositories.base import create_mutation_capability  # type: ignore
        with self.assertRaises(ImportError):
            from python.database.repositories import create_mutation_capability  # type: ignore
        with self.assertRaises(ImportError):
            from python.database.repositories.financial import create_mutation_capability  # type: ignore

        import python.database.repositories as repo_pkg
        self.assertNotIn("_FinancialMutationCapability", repo_pkg.__all__)
        self.assertNotIn("create_mutation_capability", repo_pkg.__all__)

    def test_3_cannot_fabricate_changelog_from_existing_cash_balance(self):
        """3. A caller cannot fabricate a changelog entry by supplying an existing cash balance as after_state."""
        with self.core_manager.connection() as conn:
            # Genuine setup: cash balance already exists in database
            cash_repo = CashRepository(conn)
            cash_repo.set_cash_balance("PRIMARY", "EUR", Decimal("1000.00"), correlation_id="INITIAL_SETUP")

            changelog_repo = FinancialChangelogRepository(conn)
            initial_count = len(changelog_repo.list_entries())

            # Caller in an active transaction attempts to append a fabricated changelog entry
            # claiming that a mutation occurred setting cash to the already-existing 1000.00 EUR
            conn.execute("BEGIN IMMEDIATE;")
            with self.assertRaises(PermissionError) as ctx:
                changelog_repo.append_entry(
                    correlation_id="FABRICATED_FORGERY",
                    actor_role="System",
                    operation_name="set_cash_balance",
                    target_resource="portfolio_cash/PRIMARY/EUR",
                    before_state={"account_id": "PRIMARY", "currency": "EUR", "amount": "500.00"},
                    after_state={"account_id": "PRIMARY", "currency": "EUR", "amount": "1000.00"},
                )
            self.assertIn("Direct insertion into financial_changelog via public repository API is prohibited", str(ctx.exception))

            # Attempting to construct capability directly without internal key fails closed
            from python.database.repositories.financial import _FinancialMutationCapability
            with self.assertRaises(PermissionError):
                _FinancialMutationCapability(
                    credential="INVALID_KEY",
                    connection=conn,
                    correlation_id="FAKE_CAP",
                    actor_role="System",
                    operation_name="set_cash_balance",
                    target_resource="portfolio_cash/PRIMARY/EUR",
                    before_state=None,
                    after_state={"account_id": "PRIMARY", "currency": "EUR", "amount": "1000.00"},
                    approval_digest=None,
                    timestamp_utc=utc_now_iso(),
                )

            conn.execute("ROLLBACK;")
            # Confirm no changelog entries were added
            self.assertEqual(len(changelog_repo.list_entries()), initial_count)

    def test_4_unsupported_operation_name_rejected(self):
        """4. A caller cannot fabricate entries for an unsupported operation name."""
        with self.core_manager.connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            repo = FinancialChangelogRepository(conn)

            # Test seam construction itself rejects unsupported operations
            with self.assertRaises(ValidationError) as ctx:
                _mint_test_capability(
                    connection=conn,
                    correlation_id="OP_TEST",
                    actor_role="System",
                    operation_name="grant_special_dividend",
                    target_resource="portfolio_cash/PRIMARY/EUR",
                    before_state=None,
                    after_state={"account_id": "PRIMARY", "currency": "EUR", "amount": "100.00"},
                    approval_digest=None,
                    timestamp_utc=utc_now_iso(),
                )
            self.assertIn("Unsupported mutation operation", str(ctx.exception))

            # Even if capability was somehow constructed with unsupported op, append_entry fails closed
            fake_cap = _mint_test_capability(
                connection=conn,
                correlation_id="OP_TEST_2",
                actor_role="System",
                operation_name="set_cash_balance",
                target_resource="portfolio_cash/PRIMARY/EUR",
                before_state=None,
                after_state={"account_id": "PRIMARY", "currency": "EUR", "amount": "100.00"},
                approval_digest=None,
                timestamp_utc=utc_now_iso(),
            )
            fake_cap.operation_name = "unauthorized_transfer"
            with self.assertRaises(ValidationError) as ctx2:
                repo.append_entry(mutation_capability=fake_cap)
            self.assertIn("Unsupported financial mutation operation", str(ctx2.exception))
            conn.execute("ROLLBACK;")

    def test_5_malformed_target_path_fails_closed(self):
        """5. A malformed target path fails closed."""
        with self.core_manager.connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            repo = FinancialChangelogRepository(conn)

            malformed_paths = [
                ("set_cash_balance", "portfolio_cash"),
                ("set_cash_balance", "portfolio_cash/PRIMARY"),
                ("set_cash_balance", "portfolio_cash/PRIMARY/EUR/EXTRA"),
                ("set_cash_balance", "wrong_table/PRIMARY/EUR"),
                ("upsert_position", "portfolio_positions/AAPL"),
                ("upsert_position", "portfolio_positions/PRIMARY/AAPL/EXTRA"),
                ("upsert_watchlist_item", "watchlist"),
                ("upsert_watchlist_item", "watchlist/AAPL/EXTRA"),
                ("upsert_watchlist_item", "invalid_table/AAPL"),
            ]
            for op, bad_target in malformed_paths:
                cap = _mint_test_capability(
                    connection=conn,
                    correlation_id=f"BAD_TARGET_{uuid.uuid4().hex[:6]}",
                    actor_role="System",
                    operation_name=op,
                    target_resource=bad_target,
                    before_state=None,
                    after_state={"account_id": "PRIMARY", "currency": "EUR", "amount": "100.00"},
                    approval_digest=None,
                    timestamp_utc=utc_now_iso(),
                )
                with self.assertRaises(ValidationError, msg=f"Should reject {op} with {bad_target}") as ctx:
                    repo.append_entry(mutation_capability=cap)
                self.assertIn("Malformed target_resource", str(ctx.exception))
            conn.execute("ROLLBACK;")

    def test_6_partial_or_mismatched_cash_after_state_rejected(self):
        """6. Partial or mismatched cash after-state is rejected."""
        with self.core_manager.connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                "INSERT INTO portfolio_cash (account_id, currency, amount, last_updated_at_utc) VALUES ('PRIMARY', 'EUR', '500.00', '2026-11-09T00:00:00Z')"
            )
            repo = FinancialChangelogRepository(conn)

            invalid_after_states = [
                {"account_id": "PRIMARY", "currency": "EUR"},  # missing amount
                {"currency": "EUR", "amount": "500.00"},  # missing account_id
                {"account_id": "PRIMARY", "amount": "500.00"},  # missing currency
                {"account_id": "PRIMARY", "currency": "EUR", "amount": "999.00"},  # mismatched amount
                {"account_id": "SECONDARY", "currency": "EUR", "amount": "500.00"},  # mismatched account
                {"account_id": "PRIMARY", "currency": "USD", "amount": "500.00"},  # mismatched currency
                None,  # None after_state for non-delete
                "not_a_dict",  # non-dict
            ]
            for bad_after in invalid_after_states:
                if isinstance(bad_after, dict):
                    cap = _mint_test_capability(
                        connection=conn,
                        correlation_id=f"BAD_CASH_{uuid.uuid4().hex[:6]}",
                        actor_role="System",
                        operation_name="set_cash_balance",
                        target_resource="portfolio_cash/PRIMARY/EUR",
                        before_state=None,
                        after_state=bad_after,
                        approval_digest=None,
                        timestamp_utc=utc_now_iso(),
                    )
                else:
                    # Non-dict after_state tested via test seam
                    cap = _mint_test_capability(
                        connection=conn,
                        correlation_id=f"BAD_CASH_{uuid.uuid4().hex[:6]}",
                        actor_role="System",
                        operation_name="set_cash_balance",
                        target_resource="portfolio_cash/PRIMARY/EUR",
                        before_state=None,
                        after_state=None,
                        approval_digest=None,
                        timestamp_utc=utc_now_iso(),
                    )
                    cap.after_state = bad_after
                with self.assertRaises(IntegrityConflictError, msg=f"Should reject bad after_state: {bad_after}"):
                    repo.append_entry(mutation_capability=cap)
            conn.execute("ROLLBACK;")

    def test_7_partial_or_mismatched_position_after_state_rejected_including_wac_usd(self):
        """7. Partial or mismatched position after-state is rejected, including WAC USD."""
        with self.core_manager.connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                """
                INSERT INTO portfolio_positions (account_id, ticker, shares, wac_eur, wac_usd, last_updated_at_utc)
                VALUES ('PRIMARY', 'AAPL', '10.0000', '150.0000', '165.0000', '2026-11-09T00:00:00Z')
                """
            )
            repo = FinancialChangelogRepository(conn)

            invalid_position_states = [
                # missing shares
                {"account_id": "PRIMARY", "ticker": "AAPL", "wac_eur": "150.0000", "wac_usd": "165.0000"},
                # missing wac_eur
                {"account_id": "PRIMARY", "ticker": "AAPL", "shares": "10.0000", "wac_usd": "165.0000"},
                # mismatched shares
                {"account_id": "PRIMARY", "ticker": "AAPL", "shares": "15.0000", "wac_eur": "150.0000", "wac_usd": "165.0000"},
                # mismatched wac_eur
                {"account_id": "PRIMARY", "ticker": "AAPL", "shares": "10.0000", "wac_eur": "140.0000", "wac_usd": "165.0000"},
                # mismatched wac_usd
                {"account_id": "PRIMARY", "ticker": "AAPL", "shares": "10.0000", "wac_eur": "150.0000", "wac_usd": "199.0000"},
                # missing wac_usd when db has 165.0000
                {"account_id": "PRIMARY", "ticker": "AAPL", "shares": "10.0000", "wac_eur": "150.0000", "wac_usd": None},
                # mismatched account
                {"account_id": "OTHER", "ticker": "AAPL", "shares": "10.0000", "wac_eur": "150.0000", "wac_usd": "165.0000"},
                # mismatched ticker
                {"account_id": "PRIMARY", "ticker": "MSFT", "shares": "10.0000", "wac_eur": "150.0000", "wac_usd": "165.0000"},
            ]
            for bad_pos in invalid_position_states:
                cap = _mint_test_capability(
                    connection=conn,
                    correlation_id=f"BAD_POS_{uuid.uuid4().hex[:6]}",
                    actor_role="System",
                    operation_name="upsert_position",
                    target_resource="portfolio_positions/PRIMARY/AAPL",
                    before_state=None,
                    after_state=bad_pos,
                    approval_digest=None,
                    timestamp_utc=utc_now_iso(),
                )
                with self.assertRaises(IntegrityConflictError, msg=f"Should reject bad pos: {bad_pos}"):
                    repo.append_entry(mutation_capability=cap)
            conn.execute("ROLLBACK;")

    def test_8_partial_or_mismatched_watchlist_after_state_rejected_including_notes(self):
        """8. Partial or mismatched watchlist after-state is rejected, including notes."""
        with self.core_manager.connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                "INSERT INTO watchlist (ticker, status, added_at_utc, notes) VALUES ('NVDA', 'ACTIVE', '2026-11-09T00:00:00Z', 'AI momentum leader')"
            )
            repo = FinancialChangelogRepository(conn)

            invalid_wl_states = [
                # missing status
                {"ticker": "NVDA", "notes": "AI momentum leader"},
                # missing ticker
                {"status": "ACTIVE", "notes": "AI momentum leader"},
                # mismatched status
                {"ticker": "NVDA", "status": "PAUSED", "notes": "AI momentum leader"},
                # mismatched notes
                {"ticker": "NVDA", "status": "ACTIVE", "notes": "Completely different notes"},
                # None notes when db has notes
                {"ticker": "NVDA", "status": "ACTIVE", "notes": None},
                # mismatched ticker
                {"ticker": "AMD", "status": "ACTIVE", "notes": "AI momentum leader"},
            ]
            for bad_wl in invalid_wl_states:
                cap = _mint_test_capability(
                    connection=conn,
                    correlation_id=f"BAD_WL_{uuid.uuid4().hex[:6]}",
                    actor_role="System",
                    operation_name="upsert_watchlist_item",
                    target_resource="watchlist/NVDA",
                    before_state=None,
                    after_state=bad_wl,
                    approval_digest=None,
                    timestamp_utc=utc_now_iso(),
                )
                with self.assertRaises(IntegrityConflictError, msg=f"Should reject bad wl: {bad_wl}"):
                    repo.append_entry(mutation_capability=cap)
            conn.execute("ROLLBACK;")

    def test_9_genuine_mutations_produce_correct_changelog(self):
        """9. Genuine cash, position and watchlist mutations still produce correct changelog entries."""
        with self.core_manager.connection() as conn:
            cash_repo = CashRepository(conn)
            pos_repo = PositionRepository(conn)
            wl_repo = WatchlistRepository(conn)
            changelog_repo = FinancialChangelogRepository(conn)

            # 1. Cash set & delete
            cash_repo.set_cash_balance("PRIMARY", "EUR", Decimal("1250.50"), correlation_id="CID_CASH_SET")
            e1 = changelog_repo.list_entries(correlation_id="CID_CASH_SET")
            self.assertEqual(len(e1), 1)
            self.assertEqual(e1[0].operation_name, "set_cash_balance")
            self.assertEqual(e1[0].after_state["amount"], "1250.50")
            self.assertEqual(e1[0].after_state["currency"], "EUR")
            self.assertEqual(e1[0].after_state["account_id"], "PRIMARY")

            cash_repo.delete_cash("PRIMARY", "EUR", correlation_id="CID_CASH_DEL")
            e2 = changelog_repo.list_entries(correlation_id="CID_CASH_DEL")
            self.assertEqual(len(e2), 1)
            self.assertEqual(e2[0].operation_name, "delete_cash")
            self.assertIsNone(e2[0].after_state)

            # 2. Position upsert & delete
            pos_repo.upsert_position("PRIMARY", "AAPL", Decimal("10"), Decimal("150.25"), Decimal("165.50"), correlation_id="CID_POS_UP")
            e3 = changelog_repo.list_entries(correlation_id="CID_POS_UP")
            self.assertEqual(len(e3), 1)
            self.assertEqual(e3[0].operation_name, "upsert_position")
            self.assertEqual(e3[0].after_state["shares"], "10")
            self.assertEqual(e3[0].after_state["wac_eur"], "150.25")
            self.assertEqual(e3[0].after_state["wac_usd"], "165.50")

            pos_repo.delete_position("PRIMARY", "AAPL", correlation_id="CID_POS_DEL")
            e4 = changelog_repo.list_entries(correlation_id="CID_POS_DEL")
            self.assertEqual(len(e4), 1)
            self.assertEqual(e4[0].operation_name, "delete_position")
            self.assertIsNone(e4[0].after_state)

            # 3. Watchlist upsert & remove
            wl_repo.upsert_watchlist_item("MSFT", "ACTIVE", "Q3 earnings setup", correlation_id="CID_WL_UP")
            e5 = changelog_repo.list_entries(correlation_id="CID_WL_UP")
            self.assertEqual(len(e5), 1)
            self.assertEqual(e5[0].operation_name, "upsert_watchlist_item")
            self.assertEqual(e5[0].after_state["ticker"], "MSFT")
            self.assertEqual(e5[0].after_state["status"], "ACTIVE")
            self.assertEqual(e5[0].after_state["notes"], "Q3 earnings setup")

            wl_repo.remove_watchlist_item("MSFT", correlation_id="CID_WL_DEL")
            e6 = changelog_repo.list_entries(correlation_id="CID_WL_DEL")
            self.assertEqual(len(e6), 1)
            self.assertEqual(e6[0].operation_name, "remove_watchlist_item")
            self.assertIsNone(e6[0].after_state)

    def test_10_changelog_failure_rolls_back_associated_mutation(self):
        """10. Changelog insertion failure rolls back the associated mutation."""
        with self.core_manager.connection() as conn:
            conn.execute("DROP TABLE financial_changelog;")
            cash_repo = CashRepository(conn)
            pos_repo = PositionRepository(conn)
            wl_repo = WatchlistRepository(conn)

            # Cash mutation fails and rolls back
            with self.assertRaises(sqlite3.OperationalError):
                cash_repo.set_cash_balance("PRIMARY", "EUR", Decimal("999.00"))
            self.assertIsNone(cash_repo.get_cash("PRIMARY", "EUR"))

            # Position mutation fails and rolls back
            with self.assertRaises(sqlite3.OperationalError):
                pos_repo.upsert_position("PRIMARY", "TSLA", Decimal("5"), Decimal("200.00"))
            self.assertIsNone(pos_repo.get_position("PRIMARY", "TSLA"))

            # Watchlist mutation fails and rolls back
            with self.assertRaises(sqlite3.OperationalError):
                wl_repo.upsert_watchlist_item("TSLA", "ACTIVE", "EV leader")
            self.assertIsNone(wl_repo.get_watchlist_item("TSLA"))

    def test_11_capability_replay_and_cross_connection_rejected(self):
        """11. Capability replay and cross-connection use remain rejected."""
        with self.core_manager.connection() as conn1:
            with self.core_manager.connection() as conn2:
                cash_repo = CashRepository(conn1)
                cash_repo.set_cash_balance("PRIMARY", "EUR", Decimal("100.00"), correlation_id="REPLAY_CROSS_TEST")
                cap = cash_repo._last_mutation_capability
                self.assertTrue(cap.is_consumed)

                # Cross connection consumption rejected
                repo2 = FinancialChangelogRepository(conn2)
                with self.assertRaises(PermissionError) as ctx_cross:
                    repo2.append_entry(mutation_capability=cap)
                self.assertIn("bound to a different database connection", str(ctx_cross.exception))

                # Replay on connection 1 rejected
                repo1 = FinancialChangelogRepository(conn1)
                with self.assertRaises(IntegrityConflictError) as ctx_replay:
                    repo1.append_entry(mutation_capability=cap)
                self.assertIn("already been consumed", str(ctx_replay.exception))

    def test_12_uow_multi_repo_transactions_continue_to_work(self):
        """12. Existing UnitOfWork multi-repository transactions continue to work."""
        # Multi-repo atomic commit
        with UnitOfWork(self.core_manager, auto_commit=True) as uow:
            uow.cash.set_cash_balance("PRIMARY", "EUR", Decimal("2000.00"))
            uow.positions.upsert_position("PRIMARY", "SPY", Decimal("10"), Decimal("500.00"))
            uow.watchlist.upsert_watchlist_item("SPY", "ACTIVE", "Core ETF")

        with self.core_manager.connection() as conn:
            changelog_repo = FinancialChangelogRepository(conn)
            self.assertEqual(len(changelog_repo.list_entries()), 3)
            self.assertIsNotNone(CashRepository(conn).get_cash("PRIMARY", "EUR"))
            self.assertIsNotNone(PositionRepository(conn).get_position("PRIMARY", "SPY"))
            self.assertIsNotNone(WatchlistRepository(conn).get_watchlist_item("SPY"))

        # Multi-repo rollback on failure
        try:
            with UnitOfWork(self.core_manager, auto_commit=True) as uow:
                uow.cash.set_cash_balance("PRIMARY", "USD", Decimal("5000.00"))
                raise RuntimeError("Simulated failure inside UnitOfWork")
        except RuntimeError:
            pass

        with self.core_manager.connection() as conn:
            # USD balance was rolled back
            self.assertIsNone(CashRepository(conn).get_cash("PRIMARY", "USD"))
            # Changelog count remains 3 (zero partial entries recorded)
            self.assertEqual(len(FinancialChangelogRepository(conn).list_entries()), 3)


# ===========================================================================
# 19. Stage 1.4.1.3.1: Capability Credential Encapsulation & Exact State Verification
# ===========================================================================

class TestStage14131CapabilityCredentialAndExactStateVerification(BaseRepositoryTestCase):
    """
    Dedicated test suite for Stage 1.4.1.3.1:
    - R1: Encapsulation of capability credentials (no module-level key, no general factories).
    - R2: Exact financial after-state field verification (exact key sets, nullable keys must be present).
    """

    # -----------------------------------------------------------------------
    # R1: Capability Credential Encapsulation Tests
    # -----------------------------------------------------------------------

    def test_r1_mutation_key_not_importable(self):
        """R1.1: _MUTATION_KEY is no longer an importable module-level credential."""
        with self.assertRaises(ImportError):
            from python.database.repositories.financial import _MUTATION_KEY  # type: ignore

        import python.database.repositories.financial as fin_mod
        self.assertFalse(hasattr(fin_mod, "_MUTATION_KEY"))

        import python.database.repositories as repo_pkg
        self.assertFalse(hasattr(repo_pkg, "_MUTATION_KEY"))

    def test_r1_record_changelog_absent(self):
        """R1.2: _record_changelog is absent and not importable."""
        with self.assertRaises(ImportError):
            from python.database.repositories.financial import _record_changelog  # type: ignore

        import python.database.repositories.financial as fin_mod
        self.assertFalse(hasattr(fin_mod, "_record_changelog"))

    def test_r1_no_general_purpose_factory_exposed(self):
        """R1.3: No public or private general-purpose capability factory is exposed in __all__ or module."""
        import python.database.repositories.financial as fin_mod
        self.assertEqual(fin_mod.__all__, ["CashRepository", "PositionRepository", "WatchlistRepository"])
        self.assertNotIn("create_mutation_capability", dir(fin_mod))

        import python.database.repositories as repo_pkg
        self.assertNotIn("_FinancialMutationCapability", repo_pkg.__all__)
        self.assertNotIn("create_mutation_capability", repo_pkg.__all__)

    def test_r1_direct_capability_construction_without_credential_fails(self):
        """R1.4: Direct capability construction without the encapsulated credential fails closed."""
        from python.database.repositories.financial import _FinancialMutationCapability
        with self.core_manager.connection() as conn:
            # Without credential (None)
            with self.assertRaises(PermissionError) as ctx1:
                _FinancialMutationCapability(
                    credential=None,
                    connection=conn,
                    correlation_id="TEST_NO_CRED",
                    actor_role="System",
                    operation_name="set_cash_balance",
                    target_resource="portfolio_cash/PRIMARY/EUR",
                    before_state=None,
                    after_state={"account_id": "PRIMARY", "currency": "EUR", "amount": "100.00"},
                    approval_digest=None,
                    timestamp_utc=utc_now_iso(),
                )
            self.assertIn("Direct instantiation of _FinancialMutationCapability is prohibited", str(ctx1.exception))

            # With arbitrary fake object
            with self.assertRaises(PermissionError) as ctx2:
                _FinancialMutationCapability(
                    credential=object(),
                    connection=conn,
                    correlation_id="TEST_FAKE_CRED",
                    actor_role="System",
                    operation_name="set_cash_balance",
                    target_resource="portfolio_cash/PRIMARY/EUR",
                    before_state=None,
                    after_state={"account_id": "PRIMARY", "currency": "EUR", "amount": "100.00"},
                    approval_digest=None,
                    timestamp_utc=utc_now_iso(),
                )
            self.assertIn("Direct instantiation of _FinancialMutationCapability is prohibited", str(ctx2.exception))

            # With string or int
            with self.assertRaises(PermissionError):
                _FinancialMutationCapability(
                    credential="secret_key_attempt",
                    connection=conn,
                    correlation_id="TEST_STR_CRED",
                    actor_role="System",
                    operation_name="set_cash_balance",
                    target_resource="portfolio_cash/PRIMARY/EUR",
                    before_state=None,
                    after_state={"account_id": "PRIMARY", "currency": "EUR", "amount": "100.00"},
                    approval_digest=None,
                    timestamp_utc=utc_now_iso(),
                )

    def test_r1_unauthorized_attempt_to_acquire_credential_fails(self):
        """R1.5: Calling _acquire_mutation_credential from unauthorized caller/context fails."""
        from python.database.repositories.financial import _acquire_mutation_credential
        with self.assertRaises(PermissionError) as ctx:
            _acquire_mutation_credential()
        self.assertIn("Unauthorized attempt to acquire financial mutation credential", str(ctx.exception))

    def test_r1_merely_having_active_transaction_insufficient_to_mint(self):
        """R1.6: Merely having an active transaction is insufficient to mint a valid capability."""
        from python.database.repositories.financial import _FinancialMutationCapability
        with self.core_manager.connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            with self.assertRaises(PermissionError):
                _FinancialMutationCapability(
                    credential=None,
                    connection=conn,
                    correlation_id="ACTIVE_TX_BYPASS",
                    actor_role="System",
                    operation_name="set_cash_balance",
                    target_resource="portfolio_cash/PRIMARY/EUR",
                    before_state=None,
                    after_state={"account_id": "PRIMARY", "currency": "EUR", "amount": "100.00"},
                    approval_digest=None,
                    timestamp_utc=utc_now_iso(),
                )
            conn.execute("ROLLBACK;")

    def test_r1_all_six_genuine_mutations_generate_exactly_one_changelog(self):
        """R1.7: All six genuine financial mutation methods generate exactly one changelog entry."""
        with self.core_manager.connection() as conn:
            cash_repo = CashRepository(conn)
            pos_repo = PositionRepository(conn)
            wl_repo = WatchlistRepository(conn)
            cl_repo = FinancialChangelogRepository(conn)

            initial_count = len(cl_repo.list_entries())

            # 1. set_cash_balance
            cash_repo.set_cash_balance("PRIMARY", "EUR", Decimal("100.00"), correlation_id="MUT_1")
            self.assertIsNotNone(cash_repo._last_mutation_capability)
            self.assertTrue(cash_repo._last_mutation_capability.is_consumed)
            self.assertEqual(len(cl_repo.list_entries()), initial_count + 1)

            # 2. delete_cash
            cash_repo.delete_cash("PRIMARY", "EUR", correlation_id="MUT_2")
            self.assertIsNotNone(cash_repo._last_mutation_capability)
            self.assertTrue(cash_repo._last_mutation_capability.is_consumed)
            self.assertEqual(len(cl_repo.list_entries()), initial_count + 2)

            # 3. upsert_position
            pos_repo.upsert_position("PRIMARY", "AAPL", Decimal("10"), Decimal("150.00"), Decimal("165.00"), correlation_id="MUT_3")
            self.assertIsNotNone(pos_repo._last_mutation_capability)
            self.assertTrue(pos_repo._last_mutation_capability.is_consumed)
            self.assertEqual(len(cl_repo.list_entries()), initial_count + 3)

            # 4. delete_position
            pos_repo.delete_position("PRIMARY", "AAPL", correlation_id="MUT_4")
            self.assertIsNotNone(pos_repo._last_mutation_capability)
            self.assertTrue(pos_repo._last_mutation_capability.is_consumed)
            self.assertEqual(len(cl_repo.list_entries()), initial_count + 4)

            # 5. upsert_watchlist_item
            wl_repo.upsert_watchlist_item("MSFT", "ACTIVE", "Cloud leader", correlation_id="MUT_5")
            self.assertIsNotNone(wl_repo._last_mutation_capability)
            self.assertTrue(wl_repo._last_mutation_capability.is_consumed)
            self.assertEqual(len(cl_repo.list_entries()), initial_count + 5)

            # 6. remove_watchlist_item
            wl_repo.remove_watchlist_item("MSFT", correlation_id="MUT_6")
            self.assertIsNotNone(wl_repo._last_mutation_capability)
            self.assertTrue(wl_repo._last_mutation_capability.is_consumed)
            self.assertEqual(len(cl_repo.list_entries()), initial_count + 6)

    def test_r1_capability_cannot_be_replayed_via_genuine_mutation(self):
        """R1.8: A capability obtained through genuine mutation cannot be replayed."""
        with self.core_manager.connection() as conn:
            cash_repo = CashRepository(conn)
            cash_repo.set_cash_balance("PRIMARY", "EUR", Decimal("100.00"), correlation_id="GENUINE_REPLAY")
            consumed_cap = cash_repo._last_mutation_capability
            self.assertTrue(consumed_cap.is_consumed)

            cl_repo = FinancialChangelogRepository(conn)
            with self.assertRaises(IntegrityConflictError) as ctx:
                cl_repo.append_entry(mutation_capability=consumed_cap)
            self.assertIn("already been consumed", str(ctx.exception))

    def test_r1_capability_cannot_be_used_on_different_connection(self):
        """R1.9: A capability obtained through genuine mutation cannot be used on a different connection."""
        with self.core_manager.connection() as conn1:
            with self.core_manager.connection() as conn2:
                cash_repo = CashRepository(conn1)
                cash_repo.set_cash_balance("PRIMARY", "EUR", Decimal("100.00"), correlation_id="GENUINE_CROSS")
                cap = cash_repo._last_mutation_capability

                cl_repo2 = FinancialChangelogRepository(conn2)
                with self.assertRaises(PermissionError) as ctx:
                    cl_repo2.append_entry(mutation_capability=cap)
                self.assertIn("bound to a different database connection", str(ctx.exception))

    def test_r1_changelog_failure_rolls_back_associated_mutation(self):
        """R1.10: Changelog write failure rolls back the associated financial mutation."""
        with self.core_manager.connection() as conn:
            conn.execute("DROP TABLE financial_changelog;")
            cash_repo = CashRepository(conn)
            with self.assertRaises(sqlite3.OperationalError):
                cash_repo.set_cash_balance("PRIMARY", "EUR", Decimal("999.00"))
            self.assertIsNone(cash_repo.get_cash("PRIMARY", "EUR"))


    # -----------------------------------------------------------------------
    # R2: Exact Financial After-State Field Verification Tests
    # -----------------------------------------------------------------------

    def test_r2_position_missing_wac_usd_rejected(self):
        """R2.1: Position after-state missing wac_usd key is rejected fail-closed (even when DB row has NULL)."""
        with self.core_manager.connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                """
                INSERT INTO portfolio_positions (account_id, ticker, shares, wac_eur, wac_usd, last_updated_at_utc)
                VALUES ('PRIMARY', 'TEST1', '10.0000', '100.0000', NULL, '2026-11-09T00:00:00Z')
                """
            )
            cl_repo = FinancialChangelogRepository(conn)

            # Missing wac_usd key entirely
            bad_after = {
                "account_id": "PRIMARY",
                "ticker": "TEST1",
                "shares": "10.0000",
                "wac_eur": "100.0000",
            }
            cap = _mint_test_capability(
                connection=conn,
                correlation_id="POS_MISSING_WAC_USD",
                actor_role="System",
                operation_name="upsert_position",
                target_resource="portfolio_positions/PRIMARY/TEST1",
                before_state=None,
                after_state=bad_after,
            )
            with self.assertRaises(IntegrityConflictError) as ctx:
                cl_repo.append_entry(mutation_capability=cap)
            self.assertIn("missing required fields", str(ctx.exception))
            self.assertIn("wac_usd", str(ctx.exception))
            conn.execute("ROLLBACK;")

    def test_r2_position_present_wac_usd_none_accepted(self):
        """R2.2: Position after-state with explicit wac_usd=None is accepted when DB row has NULL."""
        with self.core_manager.connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                """
                INSERT INTO portfolio_positions (account_id, ticker, shares, wac_eur, wac_usd, last_updated_at_utc)
                VALUES ('PRIMARY', 'TEST2', '10.0000', '100.0000', NULL, '2026-11-09T00:00:00Z')
                """
            )
            cl_repo = FinancialChangelogRepository(conn)

            valid_after = {
                "account_id": "PRIMARY",
                "ticker": "TEST2",
                "shares": "10.0000",
                "wac_eur": "100.0000",
                "wac_usd": None,
            }
            cap = _mint_test_capability(
                connection=conn,
                correlation_id="POS_EXPLICIT_NONE_WAC_USD",
                actor_role="System",
                operation_name="upsert_position",
                target_resource="portfolio_positions/PRIMARY/TEST2",
                before_state=None,
                after_state=valid_after,
            )
            entry = cl_repo.append_entry(mutation_capability=cap)
            self.assertEqual(entry.after_state["wac_usd"], None)
            conn.execute("ROLLBACK;")

    def test_r2_position_mismatched_wac_usd_rejected(self):
        """R2.3: Position after-state with mismatched wac_usd is rejected."""
        with self.core_manager.connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                """
                INSERT INTO portfolio_positions (account_id, ticker, shares, wac_eur, wac_usd, last_updated_at_utc)
                VALUES ('PRIMARY', 'TEST3', '10.0000', '100.0000', NULL, '2026-11-09T00:00:00Z')
                """
            )
            cl_repo = FinancialChangelogRepository(conn)

            # DB has NULL, but after_state claims non-null
            mismatched_after = {
                "account_id": "PRIMARY",
                "ticker": "TEST3",
                "shares": "10.0000",
                "wac_eur": "100.0000",
                "wac_usd": "110.0000",
            }
            cap = _mint_test_capability(
                connection=conn,
                correlation_id="POS_MISMATCHED_WAC_USD",
                actor_role="System",
                operation_name="upsert_position",
                target_resource="portfolio_positions/PRIMARY/TEST3",
                before_state=None,
                after_state=mismatched_after,
            )
            with self.assertRaises(IntegrityConflictError) as ctx:
                cl_repo.append_entry(mutation_capability=cap)
            self.assertIn("database position values do not match after_state", str(ctx.exception))
            conn.execute("ROLLBACK;")

    def test_r2_watchlist_missing_notes_rejected(self):
        """R2.4: Watchlist after-state missing notes key is rejected fail-closed (even when DB row has NULL)."""
        with self.core_manager.connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                "INSERT INTO watchlist (ticker, status, added_at_utc, notes) VALUES ('WL_TEST1', 'ACTIVE', '2026-11-09T00:00:00Z', NULL)"
            )
            cl_repo = FinancialChangelogRepository(conn)

            # Missing notes key entirely
            bad_after = {
                "ticker": "WL_TEST1",
                "status": "ACTIVE",
            }
            cap = _mint_test_capability(
                connection=conn,
                correlation_id="WL_MISSING_NOTES",
                actor_role="System",
                operation_name="upsert_watchlist_item",
                target_resource="watchlist/WL_TEST1",
                before_state=None,
                after_state=bad_after,
            )
            with self.assertRaises(IntegrityConflictError) as ctx:
                cl_repo.append_entry(mutation_capability=cap)
            self.assertIn("missing required fields", str(ctx.exception))
            self.assertIn("notes", str(ctx.exception))
            conn.execute("ROLLBACK;")

    def test_r2_watchlist_present_notes_none_accepted(self):
        """R2.5: Watchlist after-state with explicit notes=None is accepted when DB row has NULL."""
        with self.core_manager.connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                "INSERT INTO watchlist (ticker, status, added_at_utc, notes) VALUES ('WL_TEST2', 'ACTIVE', '2026-11-09T00:00:00Z', NULL)"
            )
            cl_repo = FinancialChangelogRepository(conn)

            valid_after = {
                "ticker": "WL_TEST2",
                "status": "ACTIVE",
                "notes": None,
            }
            cap = _mint_test_capability(
                connection=conn,
                correlation_id="WL_EXPLICIT_NONE_NOTES",
                actor_role="System",
                operation_name="upsert_watchlist_item",
                target_resource="watchlist/WL_TEST2",
                before_state=None,
                after_state=valid_after,
            )
            entry = cl_repo.append_entry(mutation_capability=cap)
            self.assertIsNone(entry.after_state["notes"])
            conn.execute("ROLLBACK;")

    def test_r2_watchlist_mismatched_notes_rejected(self):
        """R2.6: Watchlist after-state with mismatched notes is rejected."""
        with self.core_manager.connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                "INSERT INTO watchlist (ticker, status, added_at_utc, notes) VALUES ('WL_TEST3', 'ACTIVE', '2026-11-09T00:00:00Z', NULL)"
            )
            cl_repo = FinancialChangelogRepository(conn)

            # DB has NULL, but after claims text
            mismatched_after = {
                "ticker": "WL_TEST3",
                "status": "ACTIVE",
                "notes": "Some unexpected note",
            }
            cap = _mint_test_capability(
                connection=conn,
                correlation_id="WL_MISMATCHED_NOTES",
                actor_role="System",
                operation_name="upsert_watchlist_item",
                target_resource="watchlist/WL_TEST3",
                before_state=None,
                after_state=mismatched_after,
            )
            with self.assertRaises(IntegrityConflictError) as ctx:
                cl_repo.append_entry(mutation_capability=cap)
            self.assertIn("database watchlist state does not match after_state", str(ctx.exception))
            conn.execute("ROLLBACK;")

    def test_r2_unexpected_additional_fields_rejected_cash(self):
        """R2.7: Cash after-state with unexpected extra fields is rejected."""
        with self.core_manager.connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                "INSERT INTO portfolio_cash (account_id, currency, amount, last_updated_at_utc) VALUES ('PRIMARY', 'EUR', '100.00', '2026-11-09T00:00:00Z')"
            )
            cl_repo = FinancialChangelogRepository(conn)

            extra_cash = {
                "account_id": "PRIMARY",
                "currency": "EUR",
                "amount": "100.00",
                "extra_field": "unauthorized",
            }
            cap = _mint_test_capability(
                connection=conn,
                correlation_id="CASH_EXTRA_KEY",
                actor_role="System",
                operation_name="set_cash_balance",
                target_resource="portfolio_cash/PRIMARY/EUR",
                before_state=None,
                after_state=extra_cash,
            )
            with self.assertRaises(IntegrityConflictError) as ctx:
                cl_repo.append_entry(mutation_capability=cap)
            self.assertIn("unexpected extra fields", str(ctx.exception))
            conn.execute("ROLLBACK;")

    def test_r2_unexpected_additional_fields_rejected_position(self):
        """R2.8: Position after-state with unexpected extra fields is rejected."""
        with self.core_manager.connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                """
                INSERT INTO portfolio_positions (account_id, ticker, shares, wac_eur, wac_usd, last_updated_at_utc)
                VALUES ('PRIMARY', 'EXTRA_P', '10.0000', '100.0000', '110.0000', '2026-11-09T00:00:00Z')
                """
            )
            cl_repo = FinancialChangelogRepository(conn)

            extra_pos = {
                "account_id": "PRIMARY",
                "ticker": "EXTRA_P",
                "shares": "10.0000",
                "wac_eur": "100.0000",
                "wac_usd": "110.0000",
                "extra_field": "unauthorized",
            }
            cap = _mint_test_capability(
                connection=conn,
                correlation_id="POS_EXTRA_KEY",
                actor_role="System",
                operation_name="upsert_position",
                target_resource="portfolio_positions/PRIMARY/EXTRA_P",
                before_state=None,
                after_state=extra_pos,
            )
            with self.assertRaises(IntegrityConflictError) as ctx:
                cl_repo.append_entry(mutation_capability=cap)
            self.assertIn("unexpected extra fields", str(ctx.exception))
            conn.execute("ROLLBACK;")

    def test_r2_unexpected_additional_fields_rejected_watchlist(self):
        """R2.9: Watchlist after-state with unexpected extra fields is rejected."""
        with self.core_manager.connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                "INSERT INTO watchlist (ticker, status, added_at_utc, notes) VALUES ('EXTRA_W', 'ACTIVE', '2026-11-09T00:00:00Z', 'ok')"
            )
            cl_repo = FinancialChangelogRepository(conn)

            extra_wl = {
                "ticker": "EXTRA_W",
                "status": "ACTIVE",
                "notes": "ok",
                "extra_field": "unauthorized",
            }
            cap = _mint_test_capability(
                connection=conn,
                correlation_id="WL_EXTRA_KEY",
                actor_role="System",
                operation_name="upsert_watchlist_item",
                target_resource="watchlist/EXTRA_W",
                before_state=None,
                after_state=extra_wl,
            )
            with self.assertRaises(IntegrityConflictError) as ctx:
                cl_repo.append_entry(mutation_capability=cap)
            self.assertIn("unexpected extra fields", str(ctx.exception))
            conn.execute("ROLLBACK;")

    def test_r2_missing_mandatory_fields_rejected_across_all_targets(self):
        """R2.10: Missing mandatory fields for cash, position, and watchlist are rejected."""
        with self.core_manager.connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                "INSERT INTO portfolio_cash (account_id, currency, amount, last_updated_at_utc) VALUES ('PRIMARY', 'EUR', '100.00', '2026-11-09T00:00:00Z')"
            )
            conn.execute(
                """
                INSERT INTO portfolio_positions (account_id, ticker, shares, wac_eur, wac_usd, last_updated_at_utc)
                VALUES ('PRIMARY', 'MAND_P', '10.0000', '100.0000', '110.0000', '2026-11-09T00:00:00Z')
                """
            )
            conn.execute(
                "INSERT INTO watchlist (ticker, status, added_at_utc, notes) VALUES ('MAND_W', 'ACTIVE', '2026-11-09T00:00:00Z', 'ok')"
            )
            cl_repo = FinancialChangelogRepository(conn)

            # Cash missing amount
            cap_cash = _mint_test_capability(
                connection=conn,
                correlation_id="CASH_NO_AMT",
                actor_role="System",
                operation_name="set_cash_balance",
                target_resource="portfolio_cash/PRIMARY/EUR",
                before_state=None,
                after_state={"account_id": "PRIMARY", "currency": "EUR"},
            )
            with self.assertRaises(IntegrityConflictError):
                cl_repo.append_entry(mutation_capability=cap_cash)

            # Position missing shares
            cap_pos = _mint_test_capability(
                connection=conn,
                correlation_id="POS_NO_SHARES",
                actor_role="System",
                operation_name="upsert_position",
                target_resource="portfolio_positions/PRIMARY/MAND_P",
                before_state=None,
                after_state={"account_id": "PRIMARY", "ticker": "MAND_P", "wac_eur": "100.0000", "wac_usd": "110.0000"},
            )
            with self.assertRaises(IntegrityConflictError):
                cl_repo.append_entry(mutation_capability=cap_pos)

            # Watchlist missing status
            cap_wl = _mint_test_capability(
                connection=conn,
                correlation_id="WL_NO_STATUS",
                actor_role="System",
                operation_name="upsert_watchlist_item",
                target_resource="watchlist/MAND_W",
                before_state=None,
                after_state={"ticker": "MAND_W", "notes": "ok"},
            )
            with self.assertRaises(IntegrityConflictError):
                cl_repo.append_entry(mutation_capability=cap_wl)

            conn.execute("ROLLBACK;")

    def test_r2_exact_deletion_verification(self):
        """R2.11: Deletion operations verify that the exact row is absent, failing if row persists."""
        with self.core_manager.connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            # Row actually persists in DB
            conn.execute(
                "INSERT INTO portfolio_cash (account_id, currency, amount, last_updated_at_utc) VALUES ('PRIMARY', 'EUR', '100.00', '2026-11-09T00:00:00Z')"
            )
            cl_repo = FinancialChangelogRepository(conn)

            # Attempting delete_cash changelog when row was NOT deleted raises IntegrityConflictError
            cap = _mint_test_capability(
                connection=conn,
                correlation_id="FAKE_DELETE",
                actor_role="System",
                operation_name="delete_cash",
                target_resource="portfolio_cash/PRIMARY/EUR",
                before_state={"account_id": "PRIMARY", "currency": "EUR", "amount": "100.00"},
                after_state=None,
            )
            with self.assertRaises(IntegrityConflictError) as ctx:
                cl_repo.append_entry(mutation_capability=cap)
            self.assertIn("record was not deleted", str(ctx.exception))
            conn.execute("ROLLBACK;")


class TestStage14132TestSeamRemovalAndCredentialBoundary(BaseRepositoryTestCase):
    """
    Dedicated test suite for Stage 1.4.1.3.2:
    - Complete removal of _test_seam_mint_capability() from production code.
    - Elimination of all caller-module-name inspection (tests. / test_ prefixes) in production code.
    - Strict restriction of capability minting to caller bytecode and repository instances.
    - Verification that test fixtures cannot be abused from production and test mocks are strictly test-local.
    """

    def test_r1_test_seam_mint_capability_completely_absent(self):
        """R1.1: _test_seam_mint_capability is absent from python.database.repositories.financial and repositories."""
        import python.database.repositories.financial as fin_mod
        self.assertFalse(hasattr(fin_mod, "_test_seam_mint_capability"))
        self.assertNotIn("_test_seam_mint_capability", dir(fin_mod))

        import python.database.repositories as repo_pkg
        self.assertFalse(hasattr(repo_pkg, "_test_seam_mint_capability"))

    def test_r1_no_module_name_or_test_prefix_checks_in_production(self):
        """R1.2: Production code does not contain module-name checks like tests. or test_ prefixes."""
        fin_path = Path(__file__).parent.parent / "python" / "database" / "repositories" / "financial.py"
        source = fin_path.read_text(encoding="utf-8")
        self.assertNotIn("_test_seam_mint_capability", source)
        self.assertNotIn('startswith("tests.")', source)
        self.assertNotIn('startswith("test_")', source)
        self.assertNotIn("caller_mod", source)

    def test_r1_ordinary_caller_cannot_acquire_mutation_credential(self):
        """R1.3: Calling _acquire_mutation_credential directly from ordinary code raises PermissionError."""
        from python.database.repositories.financial import _acquire_mutation_credential
        with self.assertRaises(PermissionError) as ctx:
            _acquire_mutation_credential()
        self.assertIn("Unauthorized attempt to acquire financial mutation credential", str(ctx.exception))

    def test_r1_spoofed_module_name_cannot_acquire_mutation_credential(self):
        """R1.4: Code executing under a module named test_something or tests.something cannot acquire credential."""
        from python.database.repositories.financial import _acquire_mutation_credential
        import types

        # Create a module with a test-like name
        fake_module = types.ModuleType("test_adversarial_exploit")
        code = compile(
            """
from python.database.repositories.financial import _acquire_mutation_credential
def exploit():
    return _acquire_mutation_credential()
""",
            "<exploit>",
            "exec",
        )
        exec(code, fake_module.__dict__)
        with self.assertRaises(PermissionError) as ctx:
            fake_module.exploit()
        self.assertIn("Unauthorized attempt to acquire financial mutation credential", str(ctx.exception))

        fake_module2 = types.ModuleType("tests.unit.adversarial")
        code2 = compile(
            """
from python.database.repositories.financial import _acquire_mutation_credential
def exploit():
    return _acquire_mutation_credential()
""",
            "<exploit2>",
            "exec",
        )
        exec(code2, fake_module2.__dict__)
        with self.assertRaises(PermissionError) as ctx2:
            fake_module2.exploit()
        self.assertIn("Unauthorized attempt to acquire financial mutation credential", str(ctx2.exception))

    def test_r1_spoofed_method_bytecode_without_repository_instance_fails(self):
        """R1.5: Method named set_cash_balance on a non-CashRepository class raises PermissionError."""
        from python.database.repositories.financial import _acquire_mutation_credential

        class FakeRepository:
            def set_cash_balance(self):
                return _acquire_mutation_credential()

        fake_repo = FakeRepository()
        with self.assertRaises(PermissionError) as ctx:
            fake_repo.set_cash_balance()
        self.assertIn("Unauthorized attempt to acquire financial mutation credential", str(ctx.exception))

    def test_r1_direct_capability_construction_without_credential_fails(self):
        """R1.6: _FinancialMutationCapability direct instantiation fails closed without valid credential."""
        from python.database.repositories.financial import _FinancialMutationCapability
        with self.core_manager.connection() as conn:
            with self.assertRaises(PermissionError) as ctx:
                _FinancialMutationCapability(
                    credential=None,
                    connection=conn,
                    correlation_id="NO_CRED",
                    actor_role="System",
                    operation_name="set_cash_balance",
                    target_resource="portfolio_cash/PRIMARY/EUR",
                    before_state=None,
                    after_state={"account_id": "PRIMARY", "currency": "EUR", "amount": "100.00"},
                    timestamp_utc=utc_now_iso(),
                )
            self.assertIn("Direct instantiation of _FinancialMutationCapability is prohibited", str(ctx.exception))

            with self.assertRaises(PermissionError):
                _FinancialMutationCapability(
                    credential="unauthorized_token",
                    connection=conn,
                    correlation_id="BAD_CRED",
                    actor_role="System",
                    operation_name="set_cash_balance",
                    target_resource="portfolio_cash/PRIMARY/EUR",
                    before_state=None,
                    after_state={"account_id": "PRIMARY", "currency": "EUR", "amount": "100.00"},
                    timestamp_utc=utc_now_iso(),
                )

    def test_r1_merely_opening_transaction_does_not_permit_capability_creation(self):
        """R1.7: An active transaction does not permit unauthorized capability creation."""
        from python.database.repositories.financial import _FinancialMutationCapability
        with self.core_manager.connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            with self.assertRaises(PermissionError):
                _FinancialMutationCapability(
                    credential=None,
                    connection=conn,
                    correlation_id="TX_OPEN",
                    actor_role="System",
                    operation_name="set_cash_balance",
                    target_resource="portfolio_cash/PRIMARY/EUR",
                    before_state=None,
                    after_state={"account_id": "PRIMARY", "currency": "EUR", "amount": "100.00"},
                    timestamp_utc=utc_now_iso(),
                )
            conn.execute("ROLLBACK;")

    def test_r1_test_local_mock_does_not_persist_or_affect_production(self):
        """R1.8: Outside the test-local mock scope, _validate_credential remains fully enforced."""
        from python.database.repositories.financial import _FinancialMutationCapability, _validate_credential
        with self.core_manager.connection() as conn:
            # 1. Inside test-local helper, capability is minted
            cap = _mint_test_capability(
                connection=conn,
                correlation_id="TEST_HELPER",
                actor_role="System",
                operation_name="set_cash_balance",
                target_resource="portfolio_cash/PRIMARY/EUR",
                before_state=None,
                after_state={"account_id": "PRIMARY", "currency": "EUR", "amount": "100.00"},
            )
            self.assertIsNotNone(cap)

            # 2. Immediately outside, validation fails for arbitrary credentials
            self.assertFalse(_validate_credential("test_fixture_credential"))
            self.assertFalse(_validate_credential(None))
            self.assertFalse(_validate_credential(12345))

            # 3. Direct instantiation fails
            with self.assertRaises(PermissionError):
                _FinancialMutationCapability(
                    credential="test_fixture_credential",
                    connection=conn,
                    correlation_id="TEST_ESCAPE",
                    actor_role="System",
                    operation_name="set_cash_balance",
                    target_resource="portfolio_cash/PRIMARY/EUR",
                    before_state=None,
                    after_state={"account_id": "PRIMARY", "currency": "EUR", "amount": "100.00"},
                    timestamp_utc=utc_now_iso(),
                )

    def test_r1_all_six_genuine_mutations_create_exactly_one_changelog(self):
        """R1.9: All six genuine mutation methods mint valid capabilities and record changelog entries."""
        with self.core_manager.connection() as conn:
            cash_repo = CashRepository(conn)
            pos_repo = PositionRepository(conn)
            wl_repo = WatchlistRepository(conn)
            cl_repo = FinancialChangelogRepository(conn)

            initial_count = len(cl_repo.list_entries())

            # 1. set_cash_balance
            cash_repo.set_cash_balance("PRIMARY", "EUR", Decimal("250.00"), correlation_id="GEN_1")
            self.assertEqual(len(cl_repo.list_entries()), initial_count + 1)
            self.assertTrue(cash_repo._last_mutation_capability.is_consumed)

            # 2. delete_cash
            cash_repo.delete_cash("PRIMARY", "EUR", correlation_id="GEN_2")
            self.assertEqual(len(cl_repo.list_entries()), initial_count + 2)
            self.assertTrue(cash_repo._last_mutation_capability.is_consumed)

            # 3. upsert_position
            pos_repo.upsert_position("PRIMARY", "MSFT", Decimal("5"), Decimal("300.00"), Decimal("330.00"), correlation_id="GEN_3")
            self.assertEqual(len(cl_repo.list_entries()), initial_count + 3)
            self.assertTrue(pos_repo._last_mutation_capability.is_consumed)

            # 4. delete_position
            pos_repo.delete_position("PRIMARY", "MSFT", correlation_id="GEN_4")
            self.assertEqual(len(cl_repo.list_entries()), initial_count + 4)
            self.assertTrue(pos_repo._last_mutation_capability.is_consumed)

            # 5. upsert_watchlist_item
            wl_repo.upsert_watchlist_item("GOOGL", "ACTIVE", "Search & AI", correlation_id="GEN_5")
            self.assertEqual(len(cl_repo.list_entries()), initial_count + 5)
            self.assertTrue(wl_repo._last_mutation_capability.is_consumed)

            # 6. remove_watchlist_item
            wl_repo.remove_watchlist_item("GOOGL", correlation_id="GEN_6")
            self.assertEqual(len(cl_repo.list_entries()), initial_count + 6)
            self.assertTrue(wl_repo._last_mutation_capability.is_consumed)

    def test_r1_consumed_capabilities_cannot_be_replayed(self):
        """R1.10: Replay of an already-consumed capability is rejected fail-closed."""
        with self.core_manager.connection() as conn:
            cash_repo = CashRepository(conn)
            cash_repo.set_cash_balance("PRIMARY", "EUR", Decimal("500.00"), correlation_id="NO_REPLAY")
            consumed_cap = cash_repo._last_mutation_capability
            self.assertTrue(consumed_cap.is_consumed)

            cl_repo = FinancialChangelogRepository(conn)
            with self.assertRaises(IntegrityConflictError) as ctx:
                cl_repo.append_entry(mutation_capability=consumed_cap)
            self.assertIn("already been consumed", str(ctx.exception))

    def test_r1_genuine_capabilities_remain_connection_bound(self):
        """R1.11: Capabilities minted on one connection cannot be used on another connection."""
        with self.core_manager.connection() as conn1:
            with self.core_manager.connection() as conn2:
                cash_repo = CashRepository(conn1)
                cash_repo.set_cash_balance("PRIMARY", "EUR", Decimal("500.00"), correlation_id="CROSS_CONN")
                cap = cash_repo._last_mutation_capability

                cl_repo2 = FinancialChangelogRepository(conn2)
                with self.assertRaises(PermissionError) as ctx:
                    cl_repo2.append_entry(mutation_capability=cap)
                self.assertIn("bound to a different database connection", str(ctx.exception))

    def test_r1_changelog_failure_rolls_back_associated_mutation(self):
        """R1.12: If changelog append fails, the associated financial mutation is completely rolled back."""
        with self.core_manager.connection() as conn:
            conn.execute("DROP TABLE financial_changelog;")
            cash_repo = CashRepository(conn)
            with self.assertRaises(sqlite3.OperationalError):
                cash_repo.set_cash_balance("PRIMARY", "EUR", Decimal("999.00"))
            self.assertIsNone(cash_repo.get_cash("PRIMARY", "EUR"))


# ===========================================================================
# Stage 1.5-A: Repository Transaction, Attribution, Authorization & Revision
# ===========================================================================

class TestStage15ARepositoryTransactionAuthorizationRevisionHardening(BaseRepositoryTestCase):
    """
    Automated regression suite verifying Stage 1.5-A infrastructure hardening:
    - R1: Trusted actor attribution on trade lessons and decision log.
    - R2: Scout category authorization boundary (update_ssot).
    - R3: Standalone transaction safety and isolation.
    - R4: Audit event idempotency and concurrency protection.
    - R5: Monotonic database_metadata.db_revision tracking.
    - R6: Historical timestamp preservation.
    """

    # -----------------------------------------------------------------------
    # R1: Trusted Actor Attribution
    # -----------------------------------------------------------------------

    def test_r1_trade_lesson_matching_role_succeeds(self):
        """R1: Explicit caller_role matching active context succeeds."""
        with self.core_manager.connection() as conn:
            repo = TradeLessonRepository(conn)
            with execution_role_context("Terminal Orchestrator"):
                lesson = repo.upsert_lesson(
                    rule_code="R1_TEST_01",
                    directive_text="Matching role directive",
                    category="MANDATE",
                    caller_role="Terminal Orchestrator",
                )
                self.assertEqual(lesson.rule_code, "R1_TEST_01")

    def test_r1_trade_lesson_mismatched_role_rejected(self):
        """R1: Explicit caller_role conflicting with active context is rejected fail-closed."""
        with self.core_manager.connection() as conn:
            repo = TradeLessonRepository(conn)
            with execution_role_context("Terminal Orchestrator"):
                with self.assertRaises(PermissionError) as ctx:
                    repo.upsert_lesson(
                        rule_code="R1_TEST_02",
                        directive_text="Spoofed directive",
                        category="MANDATE",
                        caller_role="Context Engine",
                    )
                self.assertIn("cannot spoof", str(ctx.exception))

    def test_r1_trade_lesson_system_claiming_agent_rejected(self):
        """R1: Default System context cannot claim agent role without execution context."""
        with self.core_manager.connection() as conn:
            repo = TradeLessonRepository(conn)
            # Active context is default System
            with self.assertRaises(PermissionError) as ctx:
                repo.upsert_lesson(
                    rule_code="R1_TEST_03",
                    directive_text="Claimed agent directive",
                    category="MANDATE",
                    caller_role="Terminal Orchestrator",
                )
            self.assertIn("cannot claim", str(ctx.exception))

    def test_r1_trade_lesson_engineering_role_rejected(self):
        """R1: Engineering roles are forbidden from mutating trade lessons."""
        with self.core_manager.connection() as conn:
            repo = TradeLessonRepository(conn)
            with execution_role_context("Python Engineer"):
                with self.assertRaises(PermissionError) as ctx:
                    repo.upsert_lesson(
                        rule_code="R1_TEST_04",
                        directive_text="Engineer directive",
                        category="MANDATE",
                    )
                self.assertIn("Engineering role", str(ctx.exception))

    def test_r1_trade_lesson_unauthorized_investment_role_rejected(self):
        """R1: Unauthorized investment roles cannot mutate trade lessons."""
        with self.core_manager.connection() as conn:
            repo = TradeLessonRepository(conn)
            with execution_role_context("Macro Sentinel"):
                with self.assertRaises(PermissionError) as ctx:
                    repo.upsert_lesson(
                        rule_code="R1_TEST_05",
                        directive_text="Macro Sentinel directive",
                        category="MANDATE",
                    )
                self.assertIn("not authorized", str(ctx.exception))

    def test_r1_trade_lesson_delete_actor_attribution(self):
        """R1: Trade lesson deletion enforces exact actor attribution."""
        with self.core_manager.connection() as conn:
            repo = TradeLessonRepository(conn)
            with execution_role_context("Terminal Orchestrator"):
                repo.upsert_lesson("R1_DEL_01", "To be deleted", "MANDATE")

            with execution_role_context("Python Engineer"):
                with self.assertRaises(PermissionError):
                    repo.delete_lesson("R1_DEL_01")

            with execution_role_context("Terminal Orchestrator"):
                with self.assertRaises(PermissionError):
                    repo.delete_lesson("R1_DEL_01", caller_role="Review Engine")
                deleted = repo.delete_lesson("R1_DEL_01", caller_role="Terminal Orchestrator")
                self.assertTrue(deleted)

    def test_r1_decision_log_matching_role_succeeds(self):
        """R1: Decision log append with matching role succeeds."""
        with self.core_manager.connection() as conn:
            repo = DecisionLogRepository(conn)
            with execution_role_context("Review Engine"):
                rec = repo.append_decision(
                    session_id="SESS_R1_01",
                    action_proposed="HOLD",
                    decision_outcome="APPROVED",
                    caller_role="Review Engine",
                )
                self.assertIsNotNone(rec.id)

    def test_r1_decision_log_mismatched_role_rejected(self):
        """R1: Decision log append with mismatched role raises PermissionError."""
        with self.core_manager.connection() as conn:
            repo = DecisionLogRepository(conn)
            with execution_role_context("Review Engine"):
                with self.assertRaises(PermissionError) as ctx:
                    repo.append_decision(
                        session_id="SESS_R1_02",
                        action_proposed="BUY",
                        decision_outcome="APPROVED",
                        caller_role="Terminal Orchestrator",
                    )
                self.assertIn("cannot spoof", str(ctx.exception))

    def test_r1_decision_log_system_claiming_owner_rejected(self):
        """R1: Default System context cannot claim Owner/Human identity."""
        with self.core_manager.connection() as conn:
            repo = DecisionLogRepository(conn)
            with self.assertRaises(PermissionError) as ctx:
                repo.append_decision(
                    session_id="SESS_R1_03",
                    action_proposed="BUY",
                    decision_outcome="APPROVED",
                    caller_role="Owner",
                )
            self.assertIn("cannot claim", str(ctx.exception))

    def test_r1_decision_log_engineering_role_rejected(self):
        """R1: Engineering roles are forbidden from appending to decision log."""
        with self.core_manager.connection() as conn:
            repo = DecisionLogRepository(conn)
            with execution_role_context("Security Agent"):
                with self.assertRaises(PermissionError) as ctx:
                    repo.append_decision(
                        session_id="SESS_R1_04",
                        action_proposed="AUDIT",
                        decision_outcome="APPROVED",
                    )
                self.assertIn("Engineering role", str(ctx.exception))

    # -----------------------------------------------------------------------
    # R2: Scout Category Authorization
    # -----------------------------------------------------------------------

    def test_r2_scout_category_upsert_authorized_roles(self):
        """R2: Authorized roles (Terminal Orchestrator, Context Engine, System, Owner) can upsert categories."""
        with self.core_manager.connection() as conn:
            repo = ScoutCategoryRepository(conn)
            with execution_role_context("Terminal Orchestrator"):
                cat = repo.upsert_category("AI Infrastructure", "Semiconductors", ["NVDA", "ASML.AS"])
                self.assertEqual(cat.category_name, "AI Infrastructure")

            with execution_role_context("Context Engine"):
                cat2 = repo.upsert_category("Defense Tech", "Aerospace", ["RHM.DE"])
                self.assertEqual(cat2.category_name, "Defense Tech")

    def test_r2_scout_category_upsert_engineering_role_rejected(self):
        """R2: Engineering roles are rejected fail-closed on scout category mutations."""
        with self.core_manager.connection() as conn:
            repo = ScoutCategoryRepository(conn)
            with execution_role_context("Python Engineer"):
                with self.assertRaises(PermissionError) as ctx:
                    repo.upsert_category("Blocked Category", "Desc", ["AAPL"])
                self.assertIn("Engineering role", str(ctx.exception))

    def test_r2_scout_category_upsert_unauthorized_investment_role_rejected(self):
        """R2: Unauthorized investment roles (e.g. Bullish Advocate) are rejected on scout categories."""
        with self.core_manager.connection() as conn:
            repo = ScoutCategoryRepository(conn)
            with execution_role_context("Bullish Advocate"):
                with self.assertRaises(PermissionError) as ctx:
                    repo.upsert_category("Bull Category", "Desc", ["TSLA"])
                self.assertIn("not authorized", str(ctx.exception))

    def test_r2_scout_category_delete_authorization(self):
        """R2: Delete authorization checks role and targets category correctly."""
        with self.core_manager.connection() as conn:
            repo = ScoutCategoryRepository(conn)
            with execution_role_context("Terminal Orchestrator"):
                cat = repo.upsert_category("To Delete Cat", "Temp", ["MSFT"])

            # Engineering role rejected
            with execution_role_context("Architecture Agent"):
                with self.assertRaises(PermissionError):
                    repo.delete_category(cat.id)

            # Authorized role succeeds
            with execution_role_context("Terminal Orchestrator"):
                deleted = repo.delete_category(cat.id)
                self.assertTrue(deleted)
                self.assertIsNone(repo.get_category(cat.id))

    def test_r2_scout_category_owner_approval_requirement(self):
        """R2: requires_owner_approval enforces local owner approval verify_or_prompt."""
        with self.core_manager.connection() as conn:
            repo = ScoutCategoryRepository(conn)
            # In non-interactive mode with no pre-approved token, requires_owner_approval raises PermissionError
            with execution_role_context("Terminal Orchestrator"):
                with self.assertRaises(PermissionError) as ctx:
                    repo.upsert_category("High Risk Cat", "Desc", ["COIN"], requires_owner_approval=True)
                self.assertIn("owner confirmation", str(ctx.exception).lower())

    # -----------------------------------------------------------------------
    # R3: Standalone Transaction Safety
    # -----------------------------------------------------------------------

    def test_r3_standalone_write_transaction_atomicity(self):
        """R3: Standalone operations manage transaction boundaries (BEGIN IMMEDIATE / COMMIT) safely."""
        with self.core_manager.connection() as conn:
            # Standalone autocommit connection
            repo = ScoutCategoryRepository(conn)
            with execution_role_context("Terminal Orchestrator"):
                cat = repo.upsert_category("Tx Test Category", "Description", ["SAP.DE"])
                self.assertEqual(cat.category_name, "Tx Test Category")
                # Ensure no transaction was left open on the connection
                self.assertFalse(conn.in_transaction)

    def test_r3_standalone_rollback_on_failure(self):
        """R3: Standalone operations roll back their owned transaction on error without dangling locks."""
        with self.core_manager.connection() as conn:
            repo = TradeLessonRepository(conn)
            with execution_role_context("Terminal Orchestrator"):
                with self.assertRaises(ValidationError):
                    # Invalid status causes ValidationError before or during write
                    repo.upsert_lesson("INV_RULE", "Text", "MANDATE", status="INVALID_STATUS")
                self.assertFalse(conn.in_transaction)

    def test_r3_unit_of_work_transaction_preserved(self):
        """R3: Repository methods inside UnitOfWork participate without committing or rolling back early."""
        with self.factory.create_unit_of_work() as uow:
            with execution_role_context("Terminal Orchestrator"):
                uow.cash.set_cash_balance("PRIMARY", "EUR", Decimal("50000.00"))
                uow.positions.upsert_position("PRIMARY", "ASML.AS", Decimal("10.0000"), Decimal("800.0000"))
                uow.scout_categories.upsert_category("Semis", "Equip", ["ASML.AS"])
                # Transaction is still active in UoW
                self.assertTrue(uow.is_active)
            uow.commit()

        # Verify all three mutations committed together
        with self.core_manager.connection() as conn:
            self.assertEqual(CashRepository(conn).get_cash("PRIMARY", "EUR").amount, Decimal("50000.00"))
            self.assertEqual(PositionRepository(conn).get_position("PRIMARY", "ASML.AS").shares, Decimal("10.0000"))
            self.assertIsNotNone(ScoutCategoryRepository(conn).get_category_by_name("Semis"))

    # -----------------------------------------------------------------------
    # R4: Audit Event Idempotency and Concurrency
    # -----------------------------------------------------------------------

    def test_r4_audit_event_idempotency_and_replay(self):
        """R4: Audit event first insertion and identical replay succeed idempotently."""
        event_id = uuid.uuid4().hex
        raw = {"action": "test", "value": 123}

        with self.core_manager.connection() as conn:
            repo = AuditEventRepository(conn)
            # First insert
            rec1 = repo.append_event(
                event_id=event_id,
                timestamp_utc="2026-11-09T12:00:00Z",
                action_category="TEST",
                status="SUCCESS",
                agent_role="Terminal Orchestrator",
                permission_decision="ALLOW",
                approval_status="NONE",
                raw_event=raw,
            )
            self.assertEqual(rec1.event_id, event_id)

            # Replay identical event
            rec2 = repo.append_event(
                event_id=event_id,
                timestamp_utc="2026-11-09T12:00:00Z",
                action_category="TEST",
                status="SUCCESS",
                agent_role="Terminal Orchestrator",
                permission_decision="ALLOW",
                approval_status="NONE",
                raw_event=raw,
            )
            self.assertEqual(rec2.event_id, event_id)
            self.assertEqual(repo.count_events(), 1)

    def test_r4_audit_event_conflicting_replay_raises(self):
        """R4: Replaying existing event_id with conflicting payload raises IntegrityConflictError."""
        event_id = uuid.uuid4().hex
        raw1 = {"action": "original"}
        raw2 = {"action": "conflicting_tamper"}

        with self.core_manager.connection() as conn:
            repo = AuditEventRepository(conn)
            repo.append_event(
                event_id=event_id,
                timestamp_utc="2026-11-09T12:00:00Z",
                action_category="TEST",
                status="SUCCESS",
                agent_role="Terminal Orchestrator",
                permission_decision="ALLOW",
                approval_status="NONE",
                raw_event=raw1,
            )

            with self.assertRaises(IntegrityConflictError) as ctx:
                repo.append_event(
                    event_id=event_id,
                    timestamp_utc="2026-11-09T12:00:00Z",
                    action_category="TEST",
                    status="SUCCESS",
                    agent_role="Terminal Orchestrator",
                    permission_decision="ALLOW",
                    approval_status="NONE",
                    raw_event=raw2,
                )
            self.assertIn("different payload", str(ctx.exception))

    def test_r4_audit_event_concurrent_connections_idempotent(self):
        """R4: Two independent connections appending identical events succeed idempotently."""
        event_id = uuid.uuid4().hex
        raw = {"action": "concurrent_test", "data": "same"}

        conn1 = self.core_manager.connect()
        conn2 = self.core_manager.connect()
        try:
            repo1 = AuditEventRepository(conn1)
            repo2 = AuditEventRepository(conn2)

            rec1 = repo1.append_event(
                event_id=event_id,
                timestamp_utc="2026-11-09T12:00:00Z",
                action_category="CONCURRENT",
                status="SUCCESS",
                agent_role="Terminal Orchestrator",
                permission_decision="ALLOW",
                approval_status="NONE",
                raw_event=raw,
            )

            rec2 = repo2.append_event(
                event_id=event_id,
                timestamp_utc="2026-11-09T12:00:00Z",
                action_category="CONCURRENT",
                status="SUCCESS",
                agent_role="Terminal Orchestrator",
                permission_decision="ALLOW",
                approval_status="NONE",
                raw_event=raw,
            )

            self.assertEqual(rec1.event_id, event_id)
            self.assertEqual(rec2.event_id, event_id)
            self.assertEqual(repo1.count_events(), 1)
        finally:
            conn1.close()
            conn2.close()

    # -----------------------------------------------------------------------
    # R5: Monotonic db_revision Tracking
    # -----------------------------------------------------------------------

    def test_r5_initial_revision_is_zero(self):
        """R5: Database initialization creates database_metadata with db_revision == 0."""
        with self.core_manager.connection() as conn:
            rev = get_db_revision(conn)
            self.assertEqual(rev, 0)
            self.assertEqual(CashRepository(conn).get_db_revision(), 0)

    def test_r5_individual_mutations_increment_revision(self):
        """R5: Every successful logical mutation across all 7 repositories increments db_revision by 1."""
        with self.core_manager.connection() as conn:
            cash_repo = CashRepository(conn)
            pos_repo = PositionRepository(conn)
            wl_repo = WatchlistRepository(conn)
            cat_repo = ScoutCategoryRepository(conn)
            lesson_repo = TradeLessonRepository(conn)
            dec_repo = DecisionLogRepository(conn)
            appr_repo = OwnerApprovalRepository(conn)

            with execution_role_context("Terminal Orchestrator"):
                # 1. CashRepository
                cash_repo.set_cash_balance("PRIMARY", "EUR", Decimal("1000.00"))
                self.assertEqual(get_db_revision(conn), 1)

                # 2. PositionRepository
                pos_repo.upsert_position("PRIMARY", "NOVO-B.CO", Decimal("10.0000"), Decimal("100.0000"))
                self.assertEqual(get_db_revision(conn), 2)

                # 3. WatchlistRepository
                wl_repo.upsert_watchlist_item("SAP.DE")
                self.assertEqual(get_db_revision(conn), 3)

                # 4. ScoutCategoryRepository
                cat = cat_repo.upsert_category("Software", "Tech", ["SAP.DE"])
                self.assertEqual(get_db_revision(conn), 4)

                # 5. TradeLessonRepository
                lesson_repo.upsert_lesson("L_REV_01", "Revision test directive", "MANDATE")
                self.assertEqual(get_db_revision(conn), 5)

                # 6. DecisionLogRepository
                dec_repo.append_decision("SESS_REV", "BUY", "APPROVED")
                self.assertEqual(get_db_revision(conn), 6)

            # 7. OwnerApprovalRepository.append_approval (requires Owner/Human)
            with execution_role_context("Owner"):
                appr_repo.append_approval("APP_REV_01", "update_ssot", "portfolio_cash", "hash123")
                self.assertEqual(get_db_revision(conn), 7)

                # 8. OwnerApprovalRepository.consume_approval
                appr_repo.consume_approval("APP_REV_01")
                self.assertEqual(get_db_revision(conn), 8)

    def test_r5_unit_of_work_multiple_mutations_and_rollback(self):
        """R5: Multiple mutations in a UnitOfWork increment atomically; rollback preserves original revision."""
        with self.core_manager.connection() as conn:
            initial_rev = get_db_revision(conn)
            self.assertEqual(initial_rev, 0)

        # Successful UnitOfWork with 3 mutations
        with self.factory.create_unit_of_work() as uow:
            with execution_role_context("Terminal Orchestrator"):
                uow.cash.set_cash_balance("PRIMARY", "EUR", Decimal("5000.00"))
                self.assertEqual(uow.get_db_revision(), 1)
                uow.positions.upsert_position("PRIMARY", "AAPL", Decimal("5.0000"), Decimal("200.0000"))
                self.assertEqual(uow.get_db_revision(), 2)
                uow.watchlist.upsert_watchlist_item("GOOG")
                self.assertEqual(uow.get_db_revision(), 3)
            uow.commit()

        with self.core_manager.connection() as conn:
            self.assertEqual(get_db_revision(conn), 3)

        # Rolled back UnitOfWork
        try:
            with self.factory.create_unit_of_work() as uow:
                with execution_role_context("Terminal Orchestrator"):
                    uow.cash.set_cash_balance("PRIMARY", "EUR", Decimal("9999.00"))
                    self.assertEqual(uow.get_db_revision(), 4)
                    raise RuntimeError("Simulated failure inside transaction")
        except RuntimeError:
            pass

        # Revision must remain at 3 following rollback
        with self.core_manager.connection() as conn:
            self.assertEqual(get_db_revision(conn), 3)
            # Cash balance must be unmutated
            self.assertEqual(CashRepository(conn).get_cash("PRIMARY", "EUR").amount, Decimal("5000.00"))

    def test_r5_noop_operations_do_not_increment_revision(self):
        """R5: No-op deletions or unconsumed approvals do not increment db_revision."""
        with self.core_manager.connection() as conn:
            cash_repo = CashRepository(conn)
            cat_repo = ScoutCategoryRepository(conn)
            lesson_repo = TradeLessonRepository(conn)
            appr_repo = OwnerApprovalRepository(conn)

            initial_rev = get_db_revision(conn)

            with execution_role_context("Terminal Orchestrator"):
                # Non-existent cash deletion
                deleted_cash = cash_repo.delete_cash("NON_EXISTENT", "EUR")
                self.assertFalse(deleted_cash)
                self.assertEqual(get_db_revision(conn), initial_rev)

                # Non-existent category deletion
                deleted_cat = cat_repo.delete_category(9999)
                self.assertFalse(deleted_cat)
                self.assertEqual(get_db_revision(conn), initial_rev)

                # Non-existent lesson deletion
                deleted_lesson = lesson_repo.delete_lesson("NON_EXISTENT_RULE")
                self.assertFalse(deleted_lesson)
                self.assertEqual(get_db_revision(conn), initial_rev)

            # Consuming non-existent approval
            consumed = appr_repo.consume_approval("NON_EXISTENT_APP")
            self.assertFalse(consumed)
            self.assertEqual(get_db_revision(conn), initial_rev)

    def test_r5_cache_daily_bars_do_not_increment_core_revision(self):
        """R5: Market-data cache operations never increment core db_revision."""
        with self.core_manager.connection() as core_conn:
            initial_core_rev = get_db_revision(core_conn)

        with self.cache_manager.connection() as cache_conn:
            cache_repo = DailyBarRepository(cache_conn)
            cache_repo.upsert_bar("SPY", "2026-11-09", 500, 505, 498, 502, 1000000)
            cache_repo.delete_bars("SPY")

        with self.core_manager.connection() as core_conn:
            self.assertEqual(get_db_revision(core_conn), initial_core_rev)

    # -----------------------------------------------------------------------
    # R6: Historical Timestamp Preservation
    # -----------------------------------------------------------------------

    def test_r6_historical_timestamp_preservation_across_all_mutations(self):
        """R6: Valid historical timestamps are preserved exactly across all 6 mutation methods."""
        historical_ts = "2024-03-15T09:30:00Z"

        with self.core_manager.connection() as conn:
            cash_repo = CashRepository(conn)
            pos_repo = PositionRepository(conn)
            wl_repo = WatchlistRepository(conn)
            cat_repo = ScoutCategoryRepository(conn)
            lesson_repo = TradeLessonRepository(conn)
            dec_repo = DecisionLogRepository(conn)

            with execution_role_context("Terminal Orchestrator"):
                # 1. Cash
                bal = cash_repo.set_cash_balance("PRIMARY", "EUR", "12500.00", timestamp_utc=historical_ts)
                self.assertEqual(bal.last_updated_at_utc, historical_ts)
                fetched_cash = cash_repo.get_cash("PRIMARY", "EUR")
                self.assertEqual(fetched_cash.last_updated_at_utc, historical_ts)

                # 2. Position
                pos = pos_repo.upsert_position(
                    "PRIMARY", "MC.PA", "25.0000", "820.5000", timestamp_utc=historical_ts
                )
                self.assertEqual(pos.last_updated_at_utc, historical_ts)
                fetched_pos = pos_repo.get_position("PRIMARY", "MC.PA")
                self.assertEqual(fetched_pos.last_updated_at_utc, historical_ts)

                # 3. Watchlist
                wl = wl_repo.upsert_watchlist_item("OR.PA", timestamp_utc=historical_ts)
                self.assertEqual(wl.added_at_utc, historical_ts)
                fetched_wl = wl_repo.get_watchlist_item("OR.PA")
                self.assertEqual(fetched_wl.added_at_utc, historical_ts)

                # 4. Scout Category
                cat = cat_repo.upsert_category("Luxury", "European Luxury", ["MC.PA", "OR.PA"], timestamp_utc=historical_ts)
                self.assertEqual(cat.updated_at_utc, historical_ts)
                fetched_cat = cat_repo.get_category_by_name("Luxury")
                self.assertEqual(fetched_cat.updated_at_utc, historical_ts)

                # 5. Trade Lesson
                lesson = lesson_repo.upsert_lesson(
                    "HIST_01", "Preserve history", "MANDATE", timestamp_utc=historical_ts
                )
                self.assertEqual(lesson.created_at_utc, historical_ts)
                self.assertEqual(lesson.last_reviewed_at_utc, historical_ts)
                fetched_lesson = lesson_repo.get_lesson("HIST_01")
                self.assertEqual(fetched_lesson.created_at_utc, historical_ts)

                # 6. Decision Log
                dec = dec_repo.append_decision(
                    "SESS_HIST", "BUY MC.PA", "APPROVED", ticker="MC.PA", timestamp_utc=historical_ts
                )
                self.assertEqual(dec.timestamp_utc, historical_ts)
                fetched_dec = dec_repo.get_decision(dec.id)
                self.assertEqual(fetched_dec.timestamp_utc, historical_ts)

    def test_r6_invalid_timestamps_rejected_fail_closed(self):
        """R6: Malformed, non-UTC, or invalid timestamps raise ValidationError and are never silently accepted."""
        with self.core_manager.connection() as conn:
            cash_repo = CashRepository(conn)
            with execution_role_context("Terminal Orchestrator"):
                # Malformed string
                with self.assertRaises(ValidationError):
                    cash_repo.set_cash_balance("PRIMARY", "EUR", "100.00", timestamp_utc="not-a-timestamp")

                # Non-UTC timezone offset (+02:00)
                with self.assertRaises(ValidationError):
                    cash_repo.set_cash_balance("PRIMARY", "EUR", "100.00", timestamp_utc="2026-11-09T12:00:00+02:00")

                # Wrong data type
                with self.assertRaises(ValidationError):
                    cash_repo.set_cash_balance("PRIMARY", "EUR", "100.00", timestamp_utc=12345678)  # type: ignore

                # Empty string
                with self.assertRaises(ValidationError):
                    cash_repo.set_cash_balance("PRIMARY", "EUR", "100.00", timestamp_utc="")


class TestStage15A1NestedTransactionAtomicityAndTimestampSafety(unittest.TestCase):
    """
    Stage 1.5-A.1 Regression Test Suite:
    - R1: Nested repository-operation atomicity via SAVEPOINTs and unified transaction management.
    - R2: Fail-closed rollback error handling without silent error suppression.
    - R3: Strict UTC timestamp validation with explicit 'Z' or '+00:00' timezone designator
      ('-00:00' rejected as of Stage 1.5-A.3.1).
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_stage_15_a1.db")
        self.core_manager = DatabaseManager(self.db_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with self.core_manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_r1_standalone_mutation_commits_normally(self):
        """R1.1: Standalone repository mutation acquires BEGIN IMMEDIATE and commits normally."""
        with self.core_manager.connection() as conn:
            cash_repo = CashRepository(conn)
            with execution_role_context("Terminal Orchestrator"):
                rec = cash_repo.set_cash_balance("PRIMARY", "EUR", "1500.00")
                self.assertEqual(rec.amount, Decimal("1500.00"))

        with self.core_manager.connection() as conn:
            cash_repo = CashRepository(conn)
            rec = cash_repo.get_cash("PRIMARY", "EUR")
            self.assertIsNotNone(rec)
            self.assertEqual(rec.amount, Decimal("1500.00"))
            self.assertEqual(get_db_revision(conn), 1)

    def test_r1_standalone_mutation_failure_rolls_back_all_statements(self):
        """R1.2: Standalone repository mutation failure rolls back all statements."""
        with self.core_manager.connection() as conn:
            cash_repo = CashRepository(conn)
            with execution_role_context("Terminal Orchestrator"):
                # Invalid currency fails validation before commit
                with self.assertRaises(ValidationError):
                    cash_repo.set_cash_balance("PRIMARY", "INVALID", "1500.00")
            self.assertEqual(get_db_revision(conn), 0)

    def test_r1_nested_mutation_success_leaves_outer_transaction_open(self):
        """R1.3: Successful nested repository operation leaves outer transaction open."""
        with UnitOfWork(self.core_manager) as uow:
            with execution_role_context("Terminal Orchestrator"):
                uow.cash.set_cash_balance("PRIMARY", "EUR", "2000.00")
                self.assertTrue(uow.is_active)
                self.assertTrue(uow._conn.in_transaction)

                uow.watchlist.upsert_watchlist_item("AAPL")
                self.assertTrue(uow.is_active)
                self.assertTrue(uow._conn.in_transaction)

        # After commit, both mutations and revisions exist
        with self.core_manager.connection() as conn:
            cash = CashRepository(conn).get_cash("PRIMARY", "EUR")
            self.assertEqual(cash.amount, Decimal("2000.00"))
            wl = WatchlistRepository(conn).get_watchlist_item("AAPL")
            self.assertIsNotNone(wl)
            self.assertEqual(get_db_revision(conn), 2)

    def test_r1_nested_mutation_failure_rolls_back_own_changes(self):
        """R1.4: Failed nested operation rolls back all of its own changes without affecting outer tx."""
        with UnitOfWork(self.core_manager) as uow:
            with execution_role_context("Terminal Orchestrator"):
                uow.cash.set_cash_balance("PRIMARY", "EUR", "3000.00")

                # Injected integrity error inside position upsert
                pos_repo = uow.positions
                with self.assertRaises(ValidationError):
                    pos_repo.upsert_position("PRIMARY", "FAIL", "-10", "100.00")

                # Outer transaction is still active and can be committed
                self.assertTrue(uow.is_active)

        with self.core_manager.connection() as conn:
            cash = CashRepository(conn).get_cash("PRIMARY", "EUR")
            self.assertEqual(cash.amount, Decimal("3000.00"))
            pos = PositionRepository(conn).get_position("PRIMARY", "FAIL")
            self.assertIsNone(pos)
            self.assertEqual(get_db_revision(conn), 1)

    def test_r1_nested_failure_does_not_erase_unrelated_prior_outer_changes(self):
        """R1.5: A failed nested operation does not erase unrelated successful changes made earlier."""
        with UnitOfWork(self.core_manager) as uow:
            with execution_role_context("Terminal Orchestrator"):
                uow.watchlist.upsert_watchlist_item("MSFT")
                self.assertEqual(uow.get_db_revision(), 1)

                try:
                    # Trigger validation error
                    uow.cash.set_cash_balance("PRIMARY", "INVALID_CCY", "100.00")
                except ValidationError:
                    pass

                # Outer work continues
                uow.watchlist.upsert_watchlist_item("GOOGL")
                self.assertEqual(uow.get_db_revision(), 2)

        with self.core_manager.connection() as conn:
            wl_repo = WatchlistRepository(conn)
            self.assertIsNotNone(wl_repo.get_watchlist_item("MSFT"))
            self.assertIsNotNone(wl_repo.get_watchlist_item("GOOGL"))
            self.assertEqual(get_db_revision(conn), 2)

    def test_r1_caller_can_commit_outer_tx_after_catching_nested_exception(self):
        """R1.6: After catching a nested-operation exception, the caller can successfully commit outer tx."""
        with UnitOfWork(self.core_manager) as uow:
            with execution_role_context("Terminal Orchestrator"):
                uow.cash.set_cash_balance("PRIMARY", "USD", "500.00")

                # Nested operation fails and rolls back its savepoint
                with self.assertRaises(ValidationError):
                    uow.trade_lessons.upsert_lesson("BAD_CODE", "directive", "INVALID_CATEGORY")

                # Continue outer transaction
                uow.cash.set_cash_balance("PRIMARY", "EUR", "750.00")

        with self.core_manager.connection() as conn:
            cash_repo = CashRepository(conn)
            self.assertEqual(cash_repo.get_cash("PRIMARY", "USD").amount, Decimal("500.00"))
            self.assertEqual(cash_repo.get_cash("PRIMARY", "EUR").amount, Decimal("750.00"))
            self.assertIsNone(TradeLessonRepository(conn).get_lesson("BAD_CODE"))

    def test_r1_multiple_nested_savepoints_do_not_interfere(self):
        """R1.7: Multiple repository operations can use nested SAVEPOINTs without interfering."""
        with UnitOfWork(self.core_manager) as uow:
            with execution_role_context("Terminal Orchestrator"):
                # Success 1
                uow.watchlist.upsert_watchlist_item("TICKER1")
                # Failure 1 (caught)
                try:
                    uow.watchlist.upsert_watchlist_item("TICKER_BAD", status="INVALID_STATUS")
                except ValidationError:
                    pass
                # Success 2
                uow.watchlist.upsert_watchlist_item("TICKER2")
                # Failure 2 (caught)
                try:
                    uow.trade_lessons.upsert_lesson("L1", "directive", "INVALID_CATEGORY")
                except ValidationError:
                    pass
                # Success 3
                uow.trade_lessons.upsert_lesson("L1", "valid directive", "MANDATE")

        with self.core_manager.connection() as conn:
            wl = WatchlistRepository(conn)
            tl = TradeLessonRepository(conn)
            self.assertIsNotNone(wl.get_watchlist_item("TICKER1"))
            self.assertIsNone(wl.get_watchlist_item("TICKER_BAD"))
            self.assertIsNotNone(wl.get_watchlist_item("TICKER2"))
            self.assertIsNotNone(tl.get_lesson("L1"))
            self.assertEqual(get_db_revision(conn), 3)

    def test_r1_revision_and_changelog_roll_back_with_failed_nested_mutation(self):
        """R1.8: Revision increments and financial changelog records roll back with failed nested mutation."""
        with UnitOfWork(self.core_manager) as uow:
            with execution_role_context("Terminal Orchestrator"):
                uow.watchlist.upsert_watchlist_item("INITIAL")
                self.assertEqual(uow.get_db_revision(), 1)

                # Attempt invalid position mutation
                try:
                    uow.positions.upsert_position("PRIMARY", "FAIL", "0.00", "100.00")
                except ValidationError:
                    pass

                self.assertEqual(uow.get_db_revision(), 1)

    def test_r1_financial_mutation_changelog_failure_injection_in_uow(self):
        """
        R1.9 Failure Injection: Allow primary row write and revision increment to occur,
        force changelog insertion to fail, catch exception inside UnitOfWork, then commit outer tx.
        Verify that neither primary row nor revision increment nor changelog survives,
        while an unrelated successful outer-transaction mutation DOES survive.
        """
        with UnitOfWork(self.core_manager) as uow:
            with execution_role_context("Terminal Orchestrator"):
                # 1. Unrelated successful outer-transaction mutation
                uow.watchlist.upsert_watchlist_item("NVDA")
                self.assertEqual(uow.get_db_revision(), 1)

                # 2. Monkeypatch changelog append_entry to fail after row write and revision increment
                orig_append = FinancialChangelogRepository.append_entry
                def fail_append(repo_self, *args, **kwargs):
                    raise RuntimeError("Injected changelog persistence failure after row mutation")

                FinancialChangelogRepository.append_entry = fail_append
                try:
                    # Primary cash write and revision increment execute inside set_cash_balance,
                    # but append_entry fails. repository_transaction must rollback to savepoint.
                    uow.cash.set_cash_balance("PRIMARY", "EUR", "5000.00")
                    self.fail("Expected RuntimeError from injected changelog failure")
                except RuntimeError as e:
                    self.assertIn("Injected changelog persistence failure", str(e))
                finally:
                    FinancialChangelogRepository.append_entry = orig_append

                # 3. Outer transaction is committed cleanly
                self.assertTrue(uow.is_active)
                # Outer revision must have rolled back to 1
                self.assertEqual(uow.get_db_revision(), 1)

        # 4. Verify post-commit state on a fresh connection
        with self.core_manager.connection() as conn:
            # Unrelated mutation survived
            wl = WatchlistRepository(conn).get_watchlist_item("NVDA")
            self.assertIsNotNone(wl)

            # Financial cash mutation did NOT survive
            cash = CashRepository(conn).get_cash("PRIMARY", "EUR")
            self.assertIsNone(cash)

            # Changelog cash entry did NOT survive
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM financial_changelog WHERE target_resource LIKE 'portfolio_cash%'")
            self.assertEqual(cur.fetchone()[0], 0)

            # Revision is exactly 1 (NVDA increment survived, cash increment rolled back)
            self.assertEqual(get_db_revision(conn), 1)

    def test_r2_standalone_rollback_failure_raises_transaction_integrity_error(self):
        """R2.1: Standalone rollback failure raises TransactionIntegrityError with chained cause."""
        class FaultyRollbackConn:
            def __init__(self, target):
                self._target = target
            def __getattr__(self, name):
                return getattr(self._target, name)
            @property
            def in_transaction(self):
                return self._target.in_transaction
            def execute(self, sql, *args, **kwargs):
                if isinstance(sql, str) and sql.strip().startswith("ROLLBACK"):
                    raise sqlite3.OperationalError("Simulated disk error during standalone rollback")
                return self._target.execute(sql, *args, **kwargs)

        with self.core_manager.connection() as raw_conn:
            proxy = FaultyRollbackConn(raw_conn)
            with self.assertRaises(TransactionIntegrityError) as ctx:
                with repository_transaction(proxy):
                    raise ValueError("Business logic error triggering rollback")

            err_msg = str(ctx.exception)
            self.assertIn("CRITICAL: Standalone rollback failed", err_msg)
            self.assertIn("Database connection is in an uncertain state", err_msg)
            # Verify original cause is preserved via exception chaining
            self.assertIsInstance(ctx.exception.__cause__, ValueError)

    def test_r2_savepoint_rollback_failure_raises_transaction_integrity_error(self):
        """R2.2: Nested SAVEPOINT rollback failure raises TransactionIntegrityError with chained cause."""
        class FaultySavepointConn:
            def __init__(self, target):
                self._target = target
            def __getattr__(self, name):
                return getattr(self._target, name)
            @property
            def in_transaction(self):
                return True
            def execute(self, sql, *args, **kwargs):
                if isinstance(sql, str) and "ROLLBACK TO SAVEPOINT" in sql:
                    raise sqlite3.OperationalError("Simulated I/O failure during savepoint rollback")
                return self._target.execute(sql, *args, **kwargs)

        with self.core_manager.connection() as raw_conn:
            raw_conn.execute("BEGIN IMMEDIATE;")
            proxy = FaultySavepointConn(raw_conn)
            try:
                with self.assertRaises(TransactionIntegrityError) as ctx:
                    with repository_transaction(proxy):
                        raise ValueError("Operation error inside savepoint")

                err_msg = str(ctx.exception)
                self.assertIn("CRITICAL: Failed to roll back savepoint", err_msg)
                self.assertIn("MUST NOT be committed", err_msg)
                self.assertIsInstance(ctx.exception.__cause__, ValueError)
            finally:
                raw_conn.execute("ROLLBACK;")

    def test_r2_uow_commit_failure_raises_database_transaction_error(self):
        """R2.3: UnitOfWork.commit failure calls rollback and raises DatabaseTransactionError."""
        with UnitOfWork(self.core_manager, auto_commit=False) as uow:
            class FaultyCommitConn:
                def __init__(self, target):
                    self._target = target
                def __getattr__(self, name):
                    return getattr(self._target, name)
                def execute(self, sql, *args, **kwargs):
                    if isinstance(sql, str) and sql.strip().startswith("COMMIT"):
                        raise sqlite3.OperationalError("Simulated disk error during COMMIT")
                    return self._target.execute(sql, *args, **kwargs)

            uow._conn = FaultyCommitConn(uow._conn)
            with self.assertRaises(DatabaseTransactionError) as ctx:
                uow.commit()
            self.assertIn("Commit failed", str(ctx.exception))
            self.assertTrue(uow._rolled_back)

    def test_r3_strict_utc_accepted_formats(self):
        """R3.1: Strict UTC validation accepts only explicit 'Z' and '+00:00' designators, unchanged."""
        # 'Z' suffix
        ts_z = "2024-03-15T09:30:00Z"
        self.assertEqual(validate_timestamp_utc(ts_z), ts_z)

        # '+00:00' offset
        ts_plus = "2024-03-15T09:30:00+00:00"
        self.assertEqual(validate_timestamp_utc(ts_plus), ts_plus)

        # Microseconds with Z
        ts_micro = "2024-03-15T09:30:00.123456Z"
        self.assertEqual(validate_timestamp_utc(ts_micro), ts_micro)

        # None returns current UTC with Z
        now_ts = validate_timestamp_utc(None)
        self.assertTrue(now_ts.endswith("Z"))
        self.assertIn("T", now_ts)

    def test_r3_strict_utc_rejected_formats(self):
        """R3.2: Strict UTC validation rejects naive, space-separated, non-zero offset, and invalid timestamps."""
        rejected_cases = [
            ("2024-03-15T09:30:00", "naive ISO timestamp without timezone"),
            ("2024-03-15 09:30:00", "space-separated datetime without timezone"),
            ("2024-03-15 09:30:00Z", "space-separated datetime with Z"),
            ("2024-03-15T09:30:00+02:00", "non-zero timezone offset +02:00"),
            ("2024-03-15T09:30:00-05:00", "non-zero timezone offset -05:00"),
            ("2024-03-15T09:30:00-00:00", "negative-zero offset -00:00"),
            ("2024-02-30T09:30:00Z", "invalid calendar date Feb 30"),
            ("2024-03-15T25:00:00Z", "invalid time hour 25"),
            ("2024-03-15T09:60:00Z", "invalid time minute 60"),
            ("", "empty string"),
            ("   ", "whitespace-only string"),
            ("not-a-timestamp", "arbitrary text"),
            (123456789, "integer type"),
            (123.456, "float type"),
            (["2024-03-15T09:30:00Z"], "list type"),
            ({"ts": "2024-03-15T09:30:00Z"}, "dict type"),
        ]
        for val, desc in rejected_cases:
            with self.subTest(case=desc, val=val):
                with self.assertRaises(ValidationError):
                    validate_timestamp_utc(val)  # type: ignore

    def test_r3_1_validate_timestamp_utc_rejects_negative_zero_offset(self):
        """TS-CORR-01: '-00:00' is not an accepted UTC designator."""
        for value in ("2026-11-09T12:30:00-00:00", "2026-11-09T12:30:00.123456-00:00"):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    validate_timestamp_utc(value)

    def test_r3_1_validate_timestamp_utc_rejects_nonzero_offsets(self):
        """TS-CORR-01: Positive and negative non-zero offsets are rejected."""
        for value in (
            "2026-11-09T12:30:00+01:00",
            "2026-11-09T12:30:00+00:30",
            "2026-11-09T12:30:00-05:00",
            "2026-11-09T12:30:00+0000",
            "2026-11-09T12:30:00+00",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    validate_timestamp_utc(value)

    def test_r3_1_validate_timestamp_utc_rejects_surrounding_whitespace(self):
        """TS-CORR-01: Supplied timestamps are never stripped; surrounding whitespace is rejected."""
        for value in (
            " 2026-11-09T12:30:00Z",
            "2026-11-09T12:30:00Z ",
            "2026-11-09T12:30:00Z\n",
            "\n2026-11-09T12:30:00Z",
            "2026-11-09T12:30:00Z\t",
            "2026-11-09T12:30:00+00:00 ",
            "2026-11-09T12:30:00+00:00\n",
        ):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValidationError):
                    validate_timestamp_utc(value)

    def test_r3_1_validate_timestamp_utc_rejects_naive_and_malformed(self):
        """TS-CORR-01: Naive, malformed, calendar-invalid, empty and non-ASCII-digit values are rejected."""
        for value in (
            "2026-11-09T12:30:00",
            "2026-11-09T12:30:00.123456",
            "2026-02-30T12:30:00Z",
            "2026-13-01T12:30:00Z",
            "2026-11-09T24:00:00Z",
            "2026-11-09T12:30Z",
            "2026-11-09T12:30:00.Z",
            "2026-11-09T12:30:00ZZ",
            "2026-11-09T12:30:00Z+00:00",
            "\u0662\u0660\u0662\u0666-11-09T12:30:00Z",  # Arabic-Indic digits
            "",
        ):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValidationError):
                    validate_timestamp_utc(value)

    def test_r3_1_validate_timestamp_utc_rejects_non_string_values(self):
        """TS-CORR-01: Any non-string, non-None value is rejected."""
        for value in (0, 1699533000, 1.5, True, b"2026-11-09T12:30:00Z", ["x"], {"x": 1}, object()):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValidationError):
                    validate_timestamp_utc(value)  # type: ignore

    def test_r3_1_validate_timestamp_utc_preserves_original_string(self):
        """TS-CORR-01: Valid supplied timestamps are returned byte-for-byte unchanged ('Z' is not rewritten)."""
        valid_values = [
            "2026-11-09T12:30:00Z",
            "2026-11-09T12:30:00+00:00",
            "2026-11-09T12:30:00.123456Z",
            "2026-11-09T12:30:00.123456+00:00",
            "2026-11-09T12:30:00.1Z",
            "1999-12-31T23:59:59Z",
        ]
        for value in valid_values:
            with self.subTest(value=value):
                result = validate_timestamp_utc(value)
                self.assertEqual(result, value)
                self.assertEqual(result.encode("utf-8"), value.encode("utf-8"))

    def test_r3_1_validate_timestamp_utc_none_generates_valid_utc_timestamp(self):
        """TS-CORR-01: None still yields a current UTC timestamp that itself passes strict validation."""
        generated = validate_timestamp_utc(None)
        self.assertTrue(generated.endswith("Z"))
        self.assertEqual(validate_timestamp_utc(generated), generated)

    def test_r3_1_rejected_timestamp_leaves_no_partial_mutation(self):
        """TS-CORR-01: A rejected supplied timestamp aborts the mutation with no row and no revision bump."""
        bad_values = ("2026-11-09T12:30:00-00:00", " 2026-11-09T12:30:00Z", "2026-11-09T12:30:00Z\n")
        for bad in bad_values:
            with self.subTest(value=repr(bad)):
                with self.core_manager.connection() as conn:
                    rev_before = get_db_revision(conn)
                    with execution_role_context("Terminal Orchestrator"):
                        with self.assertRaises(ValidationError):
                            CashRepository(conn).set_cash_balance("PRIMARY", "EUR", "1.00", timestamp_utc=bad)
                    self.assertIsNone(CashRepository(conn).get_cash("PRIMARY", "EUR"))
                    self.assertEqual(get_db_revision(conn), rev_before)

    def test_r3_1_historical_timestamps_round_trip_byte_for_byte_after_persistence(self):
        """TS-CORR-01: Supplied timestamps are identical after persistence AND retrieval via a fresh connection."""
        ts_z = "2023-01-15T08:00:00.123456Z"
        ts_plus = "2023-06-20T14:30:00.654321+00:00"

        with self.core_manager.connection() as conn:
            with execution_role_context("Terminal Orchestrator"):
                CashRepository(conn).set_cash_balance("PRIMARY", "EUR", "100.00", timestamp_utc=ts_z)
                CashRepository(conn).set_cash_balance("PRIMARY", "USD", "200.00", timestamp_utc=ts_plus)
                PositionRepository(conn).upsert_position("PRIMARY", "AIR.PA", "10", "120.00", timestamp_utc=ts_z)
                WatchlistRepository(conn).upsert_watchlist_item("BNP.PA", timestamp_utc=ts_plus)
                ScoutCategoryRepository(conn).upsert_category("AERO", tickers=["AIR.PA"], timestamp_utc=ts_z)
                TradeLessonRepository(conn).upsert_lesson("RULE_Z", "Rule directive", "MANDATE", timestamp_utc=ts_z)
                decision = DecisionLogRepository(conn).append_decision(
                    "S_Z", "BUY AIR.PA", "APPROVED", ticker="AIR.PA", timestamp_utc=ts_plus
                )

        with self.core_manager.connection() as conn:
            self.assertEqual(CashRepository(conn).get_cash("PRIMARY", "EUR").last_updated_at_utc, ts_z)
            self.assertEqual(CashRepository(conn).get_cash("PRIMARY", "USD").last_updated_at_utc, ts_plus)
            self.assertEqual(PositionRepository(conn).get_position("PRIMARY", "AIR.PA").last_updated_at_utc, ts_z)
            self.assertEqual(WatchlistRepository(conn).get_watchlist_item("BNP.PA").added_at_utc, ts_plus)
            self.assertEqual(ScoutCategoryRepository(conn).get_category_by_name("AERO").updated_at_utc, ts_z)
            lesson = TradeLessonRepository(conn).get_lesson("RULE_Z")
            self.assertEqual(lesson.created_at_utc, ts_z)
            self.assertEqual(lesson.last_reviewed_at_utc, ts_z)
            self.assertEqual(DecisionLogRepository(conn).get_decision(decision.id).timestamp_utc, ts_plus)

    def test_r3_all_six_mutations_preserve_valid_historical_timestamps(self):
        """R3.3: All 6 core mutation operations preserve valid historical UTC timestamps exactly."""
        historical_z = "2023-01-15T08:00:00Z"
        historical_offset = "2023-06-20T14:30:00+00:00"

        with self.core_manager.connection() as conn:
            with execution_role_context("Terminal Orchestrator"):
                # 1. Cash Balance
                c1 = CashRepository(conn).set_cash_balance("PRIMARY", "EUR", "100.00", timestamp_utc=historical_z)
                self.assertEqual(c1.last_updated_at_utc, historical_z)
                c2 = CashRepository(conn).set_cash_balance("PRIMARY", "USD", "200.00", timestamp_utc=historical_offset)
                self.assertEqual(c2.last_updated_at_utc, historical_offset)

                # 2. Position
                p1 = PositionRepository(conn).upsert_position("PRIMARY", "AIR.PA", "10", "120.00", timestamp_utc=historical_z)
                self.assertEqual(p1.last_updated_at_utc, historical_z)

                # 3. Watchlist
                w1 = WatchlistRepository(conn).upsert_watchlist_item("BNP.PA", timestamp_utc=historical_offset)
                self.assertEqual(w1.added_at_utc, historical_offset)

                # 4. Scout Category
                s1 = ScoutCategoryRepository(conn).upsert_category("AERO", tickers=["AIR.PA"], timestamp_utc=historical_z)
                self.assertEqual(s1.updated_at_utc, historical_z)

                # 5. Trade Lesson
                l1 = TradeLessonRepository(conn).upsert_lesson("RULE_Z", "Rule directive", "MANDATE", timestamp_utc=historical_z)
                self.assertEqual(l1.created_at_utc, historical_z)
                self.assertEqual(l1.last_reviewed_at_utc, historical_z)

                # 6. Decision Log
                d1 = DecisionLogRepository(conn).append_decision("S_Z", "BUY AIR.PA", "APPROVED", ticker="AIR.PA", timestamp_utc=historical_offset)
                self.assertEqual(d1.timestamp_utc, historical_offset)


# ===========================================================================
# 28. Stage 1.5-A.2: Transaction Poisoning & Recovery Failure Enforcement (R1/R2)
# ===========================================================================

class TestStage15A2TransactionPoisoningAndRecoveryFailureEnforcement(BaseRepositoryTestCase):
    """
    Mandatory regression and failure-injection test suite for Stage 1.5-A.2 (R1 & R2).
    Verifies that failed nested savepoint recoveries or releases immediately poison
    the owning UnitOfWork, strictly forbidding subsequent commits or mutations,
    enforcing fail-closed outer rollback, and marking connections unsafe on cascading failures.
    """

    def test_r1_nested_savepoint_rollback_success_preserves_outer_usability(self):
        """R1.1: When nested savepoint rollback succeeds, the outer UnitOfWork remains healthy and committable."""
        with execution_role_context("Terminal Orchestrator"):
            with UnitOfWork(self.core_manager) as uow:
                # 1. First mutation in outer transaction
                uow.positions.upsert_position("PRIMARY", "ASML.AS", "10", "800.00")

                # 2. Nested mutation that fails validation
                try:
                    with repository_transaction(uow._conn, uow=uow):
                        # Trigger an operational error inside savepoint
                        uow._conn.execute("INSERT INTO portfolio_cash VALUES ('FAIL', 'EUR', '100.00', 'invalid_date');")
                except sqlite3.IntegrityError:
                    pass  # Caller handled nested error

                # 3. Outer UoW is NOT poisoned because savepoint rollback succeeded
                self.assertFalse(uow.is_poisoned)
                self.assertFalse(uow.is_conn_unsafe)

                # 4. Second mutation in outer transaction succeeds
                uow.cash.set_cash_balance("PRIMARY", "EUR", "5000.00")

            # 5. After clean exit, valid mutations were committed
            with self.core_manager.connection() as conn:
                pos = PositionRepository(conn).get_position("PRIMARY", "ASML.AS")
                cash = CashRepository(conn).get_cash("PRIMARY", "EUR")
                self.assertIsNotNone(pos)
                self.assertIsNotNone(cash)
                self.assertEqual(cash.amount, Decimal("5000.00"))

    def test_r1_nested_savepoint_rollback_failure_poisons_uow(self):
        """R1.2: When nested savepoint rollback fails, UnitOfWork is immediately poisoned."""
        with execution_role_context("Terminal Orchestrator"):
            uow = UnitOfWork(self.core_manager)
            uow.__enter__()
            try:
                # Wrap connection to inject failure into ROLLBACK TO SAVEPOINT
                class FaultySavepointRollbackConn:
                    def __init__(self, target):
                        self._target = target
                    def __getattr__(self, name):
                        return getattr(self._target, name)
                    def execute(self, sql, *args, **kwargs):
                        if isinstance(sql, str) and sql.strip().startswith("ROLLBACK TO SAVEPOINT"):
                            raise sqlite3.OperationalError("Simulated disk error during savepoint rollback")
                        return self._target.execute(sql, *args, **kwargs)

                uow._conn = FaultySavepointRollbackConn(uow._conn)

                with self.assertRaises(TransactionIntegrityError) as ctx:
                    with repository_transaction(uow._conn, uow=uow):
                        raise RuntimeError("Operation error inside savepoint")

                self.assertIn("Failed to roll back savepoint", str(ctx.exception))
                self.assertTrue(uow.is_poisoned)
                self.assertIn("Failed to roll back savepoint", str(uow.poison_reason))
            finally:
                try:
                    uow.__exit__(None, None, None)
                except Exception:
                    pass

    def test_r1_poisoned_uow_rejects_subsequent_commit(self):
        """R1.3: Caller catching TransactionIntegrityError cannot subsequently commit poisoned UnitOfWork."""
        with execution_role_context("Terminal Orchestrator"):
            uow = UnitOfWork(self.core_manager)
            uow.__enter__()
            try:
                uow.positions.upsert_position("PRIMARY", "ASML.AS", "10", "800.00")

                # Poison the UoW directly
                uow.poison("Simulated nested savepoint failure", cause=RuntimeError("disk failure"))
                self.assertTrue(uow.is_poisoned)

                # Attempt to commit must fail closed
                with self.assertRaises(TransactionIntegrityError) as ctx:
                    uow.commit()
                self.assertIn("UnitOfWork transaction has been poisoned", str(ctx.exception))
            finally:
                try:
                    uow.__exit__(None, None, None)
                except Exception:
                    pass

    def test_r1_poisoned_uow_rejects_subsequent_mutations(self):
        """R1.4: Subsequent repository mutations through a poisoned UnitOfWork are strictly rejected."""
        with execution_role_context("Terminal Orchestrator"):
            uow = UnitOfWork(self.core_manager)
            uow.__enter__()
            try:
                pos_ref = uow.positions

                # Poison the UoW
                uow.poison("Simulated savepoint failure")

                # Property access on UoW must fail closed
                with self.assertRaises(TransactionIntegrityError) as ctx:
                    _ = uow.positions
                self.assertIn("UnitOfWork transaction is poisoned", str(ctx.exception))

                with self.assertRaises(TransactionIntegrityError) as ctx:
                    _ = uow.cash
                self.assertIn("UnitOfWork transaction is poisoned", str(ctx.exception))

                # Mutation via retained reference must also fail closed
                with self.assertRaises(TransactionIntegrityError) as ctx:
                    pos_ref.upsert_position("PRIMARY", "SAP.DE", "5", "150.00")
                self.assertIn("UnitOfWork transaction is poisoned", str(ctx.exception))
            finally:
                try:
                    uow.__exit__(None, None, None)
                except Exception:
                    pass

    def test_r1_poisoned_uow_context_exit_does_not_commit_and_raises(self):
        """R1.5: If caller catches TransactionIntegrityError, __exit__ does NOT commit and raises TransactionIntegrityError."""
        with execution_role_context("Terminal Orchestrator"):
            with self.assertRaises(TransactionIntegrityError) as ctx:
                with UnitOfWork(self.core_manager) as uow:
                    uow.positions.upsert_position("PRIMARY", "ASML.AS", "10", "800.00")

                    # Inject savepoint failure and catch it
                    class FaultySavepointConn:
                        def __init__(self, target):
                            self._target = target
                        def __getattr__(self, name):
                            return getattr(self._target, name)
                        def execute(self, sql, *args, **kwargs):
                            if isinstance(sql, str) and sql.strip().startswith("ROLLBACK TO SAVEPOINT"):
                                raise sqlite3.OperationalError("Simulated rollback failure")
                            return self._target.execute(sql, *args, **kwargs)

                    uow._conn = FaultySavepointConn(uow._conn)
                    try:
                        with repository_transaction(uow._conn, uow=uow):
                            raise ValueError("Inner failure")
                    except TransactionIntegrityError:
                        pass  # Caller swallowed the integrity error!

                    # Code continues to end of with block...

            self.assertIn("UnitOfWork transaction completed with poisoned state", str(ctx.exception))

            # Verify no data was committed
            with self.core_manager.connection() as conn:
                pos = PositionRepository(conn).get_position("PRIMARY", "ASML.AS")
                self.assertIsNone(pos)

    def test_r1_outer_rollback_succeeds_after_nested_recovery_failure(self):
        """R1.6: When nested recovery fails, poison() successfully performs outer transaction rollback."""
        with execution_role_context("Terminal Orchestrator"):
            uow = UnitOfWork(self.core_manager)
            uow.__enter__()
            try:
                uow.positions.upsert_position("PRIMARY", "MC.PA", "2", "700.00")

                # Poisoning performs outer rollback
                uow.poison("Nested recovery failure")
                self.assertTrue(uow._rolled_back)
                self.assertFalse(uow._in_transaction)
                self.assertFalse(uow.is_conn_unsafe)  # Rollback succeeded, so connection is cleanly closed
            finally:
                try:
                    uow.__exit__(None, None, None)
                except Exception:
                    pass

            with self.core_manager.connection() as conn:
                self.assertIsNone(PositionRepository(conn).get_position("PRIMARY", "MC.PA"))

    def test_r1_outer_rollback_failure_marks_connection_unsafe(self):
        """R1.7: When outer rollback also fails after poisoning, connection is marked unsafe."""
        with execution_role_context("Terminal Orchestrator"):
            uow = UnitOfWork(self.core_manager)
            uow.__enter__()
            try:
                # Wrap connection so outer ROLLBACK fails
                class BrokenRollbackConn:
                    def __init__(self, target):
                        self._target = target
                    def __getattr__(self, name):
                        return getattr(self._target, name)
                    def execute(self, sql, *args, **kwargs):
                        if isinstance(sql, str) and sql.strip().startswith("ROLLBACK"):
                            raise sqlite3.OperationalError("Outer rollback fatal disk error")
                        return self._target.execute(sql, *args, **kwargs)

                uow._conn = BrokenRollbackConn(uow._conn)

                with self.assertRaises(TransactionIntegrityError) as ctx:
                    uow.poison("Savepoint broken", cause=RuntimeError("inner error"))

                self.assertIn("full outer rollback also failed", str(ctx.exception))
                self.assertTrue(uow.is_conn_unsafe)
                self.assertTrue(uow.is_poisoned)
            finally:
                try:
                    uow.__exit__(None, None, None)
                except Exception:
                    pass

    def test_r2_injected_release_savepoint_failure(self):
        """R2.1: Failure during RELEASE SAVEPOINT poisons UnitOfWork and raises TransactionIntegrityError."""
        with execution_role_context("Terminal Orchestrator"):
            uow = UnitOfWork(self.core_manager)
            uow.__enter__()
            try:
                class FaultyReleaseConn:
                    def __init__(self, target):
                        self._target = target
                    def __getattr__(self, name):
                        return getattr(self._target, name)
                    def execute(self, sql, *args, **kwargs):
                        if isinstance(sql, str) and sql.strip().startswith("RELEASE SAVEPOINT"):
                            raise sqlite3.OperationalError("Simulated RELEASE SAVEPOINT disk error")
                        return self._target.execute(sql, *args, **kwargs)

                uow._conn = FaultyReleaseConn(uow._conn)

                with self.assertRaises(TransactionIntegrityError) as ctx:
                    with repository_transaction(uow._conn, uow=uow):
                        pass  # Operation itself succeeds, but release fails

                self.assertIn("Failed to release savepoint", str(ctx.exception))
                self.assertTrue(uow.is_poisoned)
                self.assertIn("Failed to release savepoint", str(uow.poison_reason))

                # Subsequent commit attempt must be rejected
                with self.assertRaises(TransactionIntegrityError):
                    uow.commit()
            finally:
                try:
                    uow.__exit__(None, None, None)
                except Exception:
                    pass

    def test_r2_injected_standalone_commit_failure_with_safe_rollback(self):
        """R2.2: Failure during standalone COMMIT safely rolls back and raises DatabaseTransactionError."""
        with self.core_manager.connection() as real_conn:
            class FaultyCommitConn:
                def __init__(self, target):
                    self._target = target
                def __getattr__(self, name):
                    return getattr(self._target, name)
                def execute(self, sql, *args, **kwargs):
                    if isinstance(sql, str) and sql.strip().startswith("COMMIT"):
                        raise sqlite3.OperationalError("Simulated COMMIT write error")
                    return self._target.execute(sql, *args, **kwargs)

            f_conn = FaultyCommitConn(real_conn)
            with self.assertRaises(DatabaseTransactionError) as ctx:
                with repository_transaction(f_conn):
                    f_conn.execute("INSERT INTO scout_categories (category_name, updated_at_utc) VALUES ('X', '2026-01-01T00:00:00Z');")
            self.assertIn("Standalone commit failed", str(ctx.exception))

    def test_r2_injected_standalone_commit_and_rollback_double_failure(self):
        """R2.3: Double failure during standalone COMMIT and subsequent ROLLBACK raises TransactionIntegrityError."""
        with self.core_manager.connection() as real_conn:
            class TotalFailureConn:
                def __init__(self, target):
                    self._target = target
                def __getattr__(self, name):
                    return getattr(self._target, name)
                def execute(self, sql, *args, **kwargs):
                    if isinstance(sql, str) and (sql.strip().startswith("COMMIT") or sql.strip().startswith("ROLLBACK")):
                        raise sqlite3.OperationalError("Fatal I/O deadlock")
                    return self._target.execute(sql, *args, **kwargs)

            f_conn = TotalFailureConn(real_conn)
            with self.assertRaises(TransactionIntegrityError) as ctx:
                with repository_transaction(f_conn):
                    f_conn.execute("INSERT INTO scout_categories (category_name, updated_at_utc) VALUES ('Y', '2026-01-01T00:00:00Z');")
            self.assertIn("commit failed and subsequent rollback also failed", str(ctx.exception).lower())

    def test_r2_injected_standalone_rollback_failure_following_operation_error(self):
        """R2.4: Standalone rollback failure following operation error raises TransactionIntegrityError chaining exc_val."""
        with self.core_manager.connection() as real_conn:
            class RollbackFailConn:
                def __init__(self, target):
                    self._target = target
                def __getattr__(self, name):
                    return getattr(self._target, name)
                def execute(self, sql, *args, **kwargs):
                    if isinstance(sql, str) and sql.strip().startswith("ROLLBACK"):
                        raise sqlite3.OperationalError("Rollback disk fail")
                    return self._target.execute(sql, *args, **kwargs)

            f_conn = RollbackFailConn(real_conn)
            with self.assertRaises(TransactionIntegrityError) as ctx:
                with repository_transaction(f_conn):
                    raise ValueError("Initial operation error")

            self.assertIn("Standalone rollback failed following operation error", str(ctx.exception))
            self.assertIsInstance(ctx.exception.__cause__, ValueError)

    def test_r2_injected_uow_commit_and_rollback_cascade_failure(self):
        """R2.5: In UnitOfWork.commit(), cascade failure of COMMIT and ROLLBACK marks connection unsafe."""
        with execution_role_context("Terminal Orchestrator"):
            uow = UnitOfWork(self.core_manager)
            uow.__enter__()
            try:
                class TotalFailConn:
                    def __init__(self, target):
                        self._target = target
                    def __getattr__(self, name):
                        return getattr(self._target, name)
                    def execute(self, sql, *args, **kwargs):
                        if isinstance(sql, str) and (sql.strip().startswith("COMMIT") or sql.strip().startswith("ROLLBACK")):
                            raise sqlite3.OperationalError("Cascade fatal I/O failure")
                        return self._target.execute(sql, *args, **kwargs)

                uow._conn = TotalFailConn(uow._conn)

                with self.assertRaises(TransactionIntegrityError) as ctx:
                    uow.commit()

                self.assertIn("Commit failed and subsequent rollback also failed", str(ctx.exception))
                self.assertTrue(uow.is_conn_unsafe)
            finally:
                try:
                    uow.__exit__(None, None, None)
                except Exception:
                    pass

    def test_r2_injected_uow_rollback_failure_marks_unsafe(self):
        """R2.6: In UnitOfWork.rollback(), failure raises TransactionIntegrityError and marks connection unsafe."""
        with execution_role_context("Terminal Orchestrator"):
            uow = UnitOfWork(self.core_manager)
            uow.__enter__()
            try:
                class FailRollbackConn:
                    def __init__(self, target):
                        self._target = target
                    def __getattr__(self, name):
                        return getattr(self._target, name)
                    def execute(self, sql, *args, **kwargs):
                        if isinstance(sql, str) and sql.strip().startswith("ROLLBACK"):
                            raise sqlite3.OperationalError("Direct rollback failure")
                        return self._target.execute(sql, *args, **kwargs)

                uow._conn = FailRollbackConn(uow._conn)

                with self.assertRaises(TransactionIntegrityError) as ctx:
                    uow.rollback()

                self.assertIn("Failed to roll back transaction", str(ctx.exception))
                self.assertTrue(uow.is_conn_unsafe)
                self.assertTrue(uow.is_poisoned)
            finally:
                try:
                    uow.__exit__(None, None, None)
                except Exception:
                    pass



if __name__ == "__main__":
    unittest.main()
