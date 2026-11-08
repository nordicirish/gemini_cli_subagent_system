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
)
from python.tool_registry import (
    execution_role_context,
    GLOBAL_OWNER_APPROVAL,
    compute_payload_digest,
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
            repo = FinancialChangelogRepository(conn)
            with execution_role_context("Terminal Orchestrator"):
                entry = repo.append_entry(
                    correlation_id="CORR_123",
                    actor_role="Terminal Orchestrator",
                    operation_name="update_ssot",
                    target_resource="portfolio_positions/PRIMARY/SAP.DE",
                    before_state=None,
                    after_state={"ticker": "SAP.DE", "shares": "100.0000"},
                )
                self.assertIsNotNone(entry.id)
                self.assertEqual(entry.correlation_id, "CORR_123")
                self.assertEqual(entry.after_state["shares"], "100.0000")

                fetched = repo.get_entry(entry.id)
                self.assertIsNotNone(fetched)
                self.assertEqual(fetched.operation_name, "update_ssot")

                entries = repo.list_entries(correlation_id="CORR_123")
                self.assertEqual(len(entries), 1)

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
            with self.assertRaises(ValidationError) as ctx:
                repo.set_cash_balance("PRIMARY", "EUR", 100.50, caller_role="Terminal Orchestrator")
            self.assertIn("Floating-point values strictly forbidden", str(ctx.exception))

    def test_cash_negative_rejected(self):
        with self.core_manager.connection() as conn:
            repo = CashRepository(conn)
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
            for val in malformed:
                with self.subTest(val=val):
                    with self.assertRaises(ValidationError):
                        repo.set_cash_balance("PRIMARY", "EUR", val, caller_role="Terminal Orchestrator")

    def test_cash_nan_inf_rejected(self):
        with self.core_manager.connection() as conn:
            repo = CashRepository(conn)
            with self.assertRaises(ValidationError):
                repo.set_cash_balance("PRIMARY", "EUR", Decimal("NaN"), caller_role="Terminal Orchestrator")
            with self.assertRaises(ValidationError):
                repo.set_cash_balance("PRIMARY", "EUR", Decimal("Infinity"), caller_role="Terminal Orchestrator")

    def test_position_decimal_precision_scale(self):
        with self.core_manager.connection() as conn:
            repo = PositionRepository(conn)
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
            # Both cash and position recorded changelog entries
            entries = changelog_repo.list_entries()
            self.assertEqual(len(entries), 2)

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


if __name__ == "__main__":
    unittest.main()
