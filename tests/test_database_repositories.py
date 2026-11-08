"""
Automated Test Suite for SQLite Repository Layer & Transactional Persistence Verification.
Governed by Stage 1.4 Architecture and Agent Governance Framework.

Covers:
1. CRUD and Mapping (all 10 entities).
2. Financial Decimal Integrity (exact scale, floats rejected, invalid strings rejected).
3. Financial Authorization Enforcement (role checks, owner approval, digest binding).
4. Transactional Atomicity & Rollback (multi-repo writes, changelog failure atomicity).
5. Concurrency and Idempotency (replay of duplicate event_ids with identical vs conflicting payloads).
6. Append-Only Immutability (audit events, changelog, owner approvals, decision log).
7. Cache Isolation (core vs cache database separation, cross-connection boundary enforcement).
8. Import and Lifecycle Safety (zero side-effects, state file invariance).
"""

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
            entry = repo.append_entry(
                correlation_id="CORR_123",
                actor_role="Terminal Orchestrator",
                operation_name="upsert_position",
                target_resource="portfolio_positions/PRIMARY/SAP.DE",
                before_state=None,
                after_state={"ticker": "SAP.DE", "shares": "100.0000"},
            )
            self.assertIsNotNone(entry.id)
            self.assertEqual(entry.correlation_id, "CORR_123")
            self.assertEqual(entry.after_state["shares"], "100.0000")

            fetched = repo.get_entry(entry.id)
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched.operation_name, "upsert_position")

            entries = repo.list_entries(correlation_id="CORR_123")
            self.assertEqual(len(entries), 1)

    def test_owner_approvals_crud_and_mapping(self):
        with self.core_manager.connection() as conn:
            repo = OwnerApprovalRepository(conn)
            approval = repo.append_approval(
                approval_id="APP_001",
                operation_name="update_rules",
                target_resource="rules/core",
                payload_digest="a" * 64,
                granted_by="Owner",
            )
            self.assertEqual(approval.approval_id, "APP_001")
            self.assertEqual(approval.status, "PENDING")
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
                repo.set_cash_balance("PRIMARY", "EUR", Decimal("10000.00"), record_changelog=True)

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


if __name__ == "__main__":
    unittest.main()
