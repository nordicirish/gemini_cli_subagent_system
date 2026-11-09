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


if __name__ == "__main__":
    unittest.main()
