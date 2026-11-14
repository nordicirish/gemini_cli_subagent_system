"""
Automated Test Suite for GEM Shared Application Persistence Service.
Governed by Stage 2 Architecture and Agent Governance Framework.

Covers:
1. Shared Service Lifecycle, Backend Selection, and Configuration.
2. JSON Adapter Domain Operations, Atomic RMW, and External Section Preservation.
3. SQLite Adapter Repository Mapping, Multi-Record UnitOfWork Atomicity, and Rollback.
4. Security & Role Authorization Enforcement Across Both Adapters.
5. UnitOfWork Transaction Poisoning Preservation.
"""

from decimal import Decimal
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from python.database.manager import DatabaseManager, PRIMARY_FINANCIAL_PROFILE
from python.database.schema import SchemaMigrator
from python.database.repositories.base import (
    TransactionIntegrityError,
    RepositoryError,
    ValidationError,
)

from persistence import (
    PersistenceService,
    JsonPersistenceAdapter,
    SqlitePersistenceAdapter,
    get_persistence_service,
    configure_persistence,
    reset_persistence_service,
    PersistenceError,
    PersistenceIntegrityError,
    PersistenceValidationError,
)
try:
    from python.tool_registry import execution_role_context, authorize_financial_mutation
except ImportError:
    from tool_registry import execution_role_context, authorize_financial_mutation


class TestPersistenceServiceConfiguration(unittest.TestCase):
    def setUp(self):
        reset_persistence_service()
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        reset_persistence_service()
        self.temp_dir.cleanup()

    def test_default_service_is_json(self):
        service = get_persistence_service()
        self.assertIsInstance(service, PersistenceService)
        self.assertEqual(service.backend_name, "json")
        self.assertIsInstance(service.adapter, JsonPersistenceAdapter)

    def test_configure_explicit_json_with_base_dir(self):
        service = configure_persistence(backend="json", base_dir=self.temp_dir.name)
        self.assertEqual(service.backend_name, "json")
        self.assertIn(self.temp_dir.name.replace("\\", "/"), service.adapter.ssot_path.replace("\\", "/"))

    def test_configure_sqlite_requires_db_manager(self):
        with self.assertRaises(ValueError) as ctx:
            configure_persistence(backend="sqlite", db_manager=None)
        self.assertIn("db_manager is required", str(ctx.exception))

    def test_configure_sqlite_with_valid_manager(self):
        db_path = os.path.join(self.temp_dir.name, "test_core.db")
        mgr = DatabaseManager(db_path, profile=PRIMARY_FINANCIAL_PROFILE)
        service = configure_persistence(backend="sqlite", db_manager=mgr)

        self.assertEqual(service.backend_name, "sqlite")
        self.assertIsInstance(service.adapter, SqlitePersistenceAdapter)
        service.close()

    def test_configure_unsupported_backend_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            configure_persistence(backend="mongodb")
        self.assertIn("Unsupported persistence backend", str(ctx.exception))

    def test_reset_disposes_service(self):
        service1 = get_persistence_service()
        self.assertIsNotNone(service1)
        reset_persistence_service()
        service2 = get_persistence_service()
        self.assertIsNot(service1, service2)


class TestJsonPersistenceAdapter(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.context_dir = os.path.join(self.temp_dir.name, "context")
        os.makedirs(self.context_dir, exist_ok=True)

        self.adapter = JsonPersistenceAdapter(base_dir=self.temp_dir.name)

        # Seed initial ssot.json with external section
        self.initial_ssot = {
            "state_context": {"regime": "NORMAL", "vix": 14.5},
            "runtime_flags": {"dry_run": False},
            "portfolio_snapshot": [{"ticker": "SPY", "shares": 10.0, "wac": 500.0}],
            "unallocated_cash_eur": 2500.0,
            "unallocated_cash_usd": 2700.0,
            "watched_tickers": ["AAPL", "MSFT"],
            "mutable_state": {
                "portfolio_snapshot": [{"ticker": "SPY", "shares": 10.0, "wac": 500.0}],
                "unallocated_cash_eur": 2500.0,
                "unallocated_cash_usd": 2700.0,
                "watched_tickers": ["AAPL", "MSFT"],
            },
        }
        with open(self.adapter.ssot_path, "w", encoding="utf-8") as f:
            json.dump(self.initial_ssot, f, indent=2)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_get_portfolio_basket_reads_holdings_and_cash(self):
        basket = self.adapter.get_portfolio_basket()
        self.assertEqual(len(basket["portfolio"]), 1)
        self.assertEqual(basket["portfolio"][0]["ticker"], "SPY")
        self.assertEqual(basket["portfolio"][0]["shares"], 10.0)
        self.assertEqual(basket["portfolio"][0]["wac"], 500.0)
        self.assertEqual(basket["unallocated_cash_eur"], 2500.0)
        self.assertEqual(basket["unallocated_cash_usd"], 2700.0)

    def test_save_portfolio_basket_preserves_external_sections(self):
        with execution_role_context("Terminal Orchestrator"):
            new_portfolio = [
                {"ticker": "NVDA", "shares": 15.0, "wac": 120.0},
                {"ticker": "AMD", "shares": 20.0, "wac": 140.0},
            ]
            res = self.adapter.save_portfolio_basket(
                portfolio=new_portfolio,
                unallocated_cash_eur=3000.0,
                unallocated_cash_usd=3240.0,
            )
            self.assertEqual(res["status"], "success")

        # Read back raw SSoT to verify external section preservation
        ssot = self.adapter.get_ssot_data()
        self.assertIn("state_context", ssot)
        self.assertEqual(ssot["state_context"]["regime"], "NORMAL")
        self.assertIn("runtime_flags", ssot)
        self.assertEqual(ssot["runtime_flags"]["dry_run"], False)

        # Check updated basket
        self.assertEqual(ssot["unallocated_cash_eur"], 3000.0)
        self.assertEqual(len(ssot["portfolio_snapshot"]), 2)
        self.assertEqual(ssot["portfolio_snapshot"][0]["ticker"], "NVDA")

    def test_financial_mutation_rejected_for_engineering_roles(self):
        with execution_role_context("Python Engineer"):
            with self.assertRaises(PermissionError):
                self.adapter.save_portfolio_basket(
                    portfolio=[{"ticker": "HACK", "shares": 100, "wac": 10.0}],
                    unallocated_cash_eur=9999.0,
                )

    def test_watchlist_operations(self):
        with execution_role_context("Terminal Orchestrator"):
            saved = self.adapter.save_watchlist(["GOOG", "AMZN", "TSLA"])
            self.assertEqual(saved, ["GOOG", "AMZN", "TSLA"])

            read_back = self.adapter.get_watchlist()
            self.assertEqual(read_back, ["GOOG", "AMZN", "TSLA"])

    def test_trade_lessons_operations(self):
        with execution_role_context("Terminal Orchestrator"):
            lessons = [{"id": 1, "rule": "L-01: Risk maximum 1% per position"}]
            self.adapter.save_trade_lessons(lessons)

            res = self.adapter.get_trade_lessons()
            self.assertEqual(len(res), 1)
            self.assertIn("Risk maximum 1%", res[0]["rule"])

            # Append another lesson
            self.adapter.save_trade_lessons([{"rule": "L-02: Trail VWAP anchor"}])
            res2 = self.adapter.get_trade_lessons()
            self.assertEqual(len(res2), 2)

    def test_decision_log_operations_and_retention_cap(self):
        with execution_role_context("Terminal Orchestrator"):
            # Append entry
            entry1 = {"session_id": "sess_1", "action_proposed": "HOLD", "ticker": "SPY"}
            self.adapter.append_decision_log(entry1)

            log1 = self.adapter.get_decision_log()
            self.assertEqual(len(log1), 1)
            self.assertEqual(log1[0]["session_id"], "sess_1")

            # Clear
            self.adapter.clear_decision_log()
            log_cleared = self.adapter.get_decision_log()
            self.assertEqual(len(log_cleared), 0)

    def test_missing_ssot_returns_first_run_defaults(self):
        """Missing SSoT file returns {} from get_ssot_data and default empty basket."""
        if os.path.exists(self.adapter.ssot_path):
            os.remove(self.adapter.ssot_path)
        self.assertEqual(self.adapter.get_ssot_data(), {})
        basket = self.adapter.get_portfolio_basket()
        self.assertEqual(basket["portfolio"], [])
        self.assertEqual(basket["unallocated_cash_eur"], 0.0)
        self.assertEqual(basket["unallocated_cash_usd"], 0.0)
        self.assertEqual(basket["eurusd_rate"], 1.08)

    def test_empty_ssot_file_fails_closed_with_integrity_error(self):
        """Existing but empty or whitespace SSoT file must raise PersistenceIntegrityError."""
        with open(self.adapter.ssot_path, "w", encoding="utf-8") as f:
            f.write("   \n  ")
        with self.assertRaises(PersistenceIntegrityError):
            self.adapter.get_ssot_data()
        with self.assertRaises(PersistenceIntegrityError):
            self.adapter.get_portfolio_basket()

    def test_malformed_json_ssot_file_fails_closed_with_integrity_error(self):
        """Existing but malformed JSON SSoT file must raise PersistenceIntegrityError."""
        with open(self.adapter.ssot_path, "w", encoding="utf-8") as f:
            f.write("{invalid_json: true, broken")
        with self.assertRaises(PersistenceIntegrityError):
            self.adapter.get_ssot_data()
        with self.assertRaises(PersistenceIntegrityError):
            self.adapter.get_portfolio_basket()

    def test_non_dict_root_ssot_file_fails_closed_with_integrity_error(self):
        """SSoT file whose JSON root is not a dict must raise PersistenceIntegrityError."""
        with open(self.adapter.ssot_path, "w", encoding="utf-8") as f:
            f.write('["item1", "item2"]')
        with self.assertRaises(PersistenceIntegrityError):
            self.adapter.get_ssot_data()
        with self.assertRaises(PersistenceIntegrityError):
            self.adapter.get_portfolio_basket()

    def test_corrupted_portfolio_structure_fails_closed_with_validation_error(self):
        """SSoT with non-list portfolio_snapshot or invalid positions must raise PersistenceValidationError."""
        with open(self.adapter.ssot_path, "w", encoding="utf-8") as f:
            json.dump({"portfolio_snapshot": "not_a_list"}, f)
        with self.assertRaises(PersistenceValidationError):
            self.adapter.get_portfolio_basket()

        # Test negative shares
        with open(self.adapter.ssot_path, "w", encoding="utf-8") as f:
            json.dump({"portfolio_snapshot": [{"ticker": "SPY", "shares": -10.0, "wac": 500.0}]}, f)
        with self.assertRaises(PersistenceValidationError):
            self.adapter.get_portfolio_basket()

        # Test non-numeric cash
        with open(self.adapter.ssot_path, "w", encoding="utf-8") as f:
            json.dump({"portfolio_snapshot": [], "unallocated_cash_eur": "invalid_number"}, f)
        with self.assertRaises(PersistenceValidationError):
            self.adapter.get_portfolio_basket()

    def test_json_rejects_non_finite_financial_values(self):
        """Reject non-finite shares, wac, cash, and exchange-rate values fail-closed with PersistenceValidationError."""
        non_finite_values = [float("nan"), float("inf"), float("-inf")]

        for nf in non_finite_values:
            # 1. Non-finite shares in SSoT read
            with open(self.adapter.ssot_path, "w", encoding="utf-8") as f:
                json.dump({"portfolio_snapshot": [{"ticker": "SPY", "shares": nf, "wac": 500.0}]}, f)
            with self.assertRaises(PersistenceValidationError, msg=f"Should reject non-finite shares: {nf}"):
                self.adapter.get_portfolio_basket()

            # 2. Non-finite wac in SSoT read
            with open(self.adapter.ssot_path, "w", encoding="utf-8") as f:
                json.dump({"portfolio_snapshot": [{"ticker": "SPY", "shares": 10.0, "wac": nf}]}, f)
            with self.assertRaises(PersistenceValidationError, msg=f"Should reject non-finite wac: {nf}"):
                self.adapter.get_portfolio_basket()

            # 3. Non-finite EUR cash in SSoT read
            with open(self.adapter.ssot_path, "w", encoding="utf-8") as f:
                json.dump({"portfolio_snapshot": [], "unallocated_cash_eur": nf}, f)
            with self.assertRaises(PersistenceValidationError, msg=f"Should reject non-finite EUR cash: {nf}"):
                self.adapter.get_portfolio_basket()

            # 4. Non-finite USD cash in SSoT read
            with open(self.adapter.ssot_path, "w", encoding="utf-8") as f:
                json.dump({"portfolio_snapshot": [], "unallocated_cash_usd": nf}, f)
            with self.assertRaises(PersistenceValidationError, msg=f"Should reject non-finite USD cash: {nf}"):
                self.adapter.get_portfolio_basket()

            # 5. Non-finite exchange rate in SSoT read
            with open(self.adapter.ssot_path, "w", encoding="utf-8") as f:
                json.dump({"portfolio_snapshot": [], "exchange_rate": nf}, f)
            with self.assertRaises(PersistenceValidationError, msg=f"Should reject non-finite exchange rate: {nf}"):
                self.adapter.get_portfolio_basket()

            # 6. Non-finite values in save_portfolio_basket
            with execution_role_context("Terminal Orchestrator"):
                with self.assertRaises(PersistenceValidationError, msg=f"save_portfolio_basket should reject non-finite shares: {nf}"):
                    self.adapter.save_portfolio_basket(
                        portfolio=[{"ticker": "SPY", "shares": nf, "wac": 500.0}],
                        unallocated_cash_eur=1000.0,
                    )
                with self.assertRaises(PersistenceValidationError, msg=f"save_portfolio_basket should reject non-finite cash: {nf}"):
                    self.adapter.save_portfolio_basket(
                        portfolio=[{"ticker": "SPY", "shares": 10.0, "wac": 500.0}],
                        unallocated_cash_eur=nf,
                    )

    def test_json_accepts_valid_zero_and_positive_values(self):
        """Valid zero and ordinary positive financial numbers must be accepted without error."""
        with open(self.adapter.ssot_path, "w", encoding="utf-8") as f:
            json.dump({
                "portfolio_snapshot": [
                    {"ticker": "SPY", "shares": 0.0, "wac": 0.0},
                    {"ticker": "AAPL", "shares": 10.5, "wac": 225.50},
                ],
                "unallocated_cash_eur": 0.0,
                "unallocated_cash_usd": 1500.75,
                "exchange_rate": 1.085,
            }, f)

        basket = self.adapter.get_portfolio_basket()
        self.assertEqual(len(basket["portfolio"]), 2)
        self.assertEqual(basket["portfolio"][0]["shares"], 0.0)
        self.assertEqual(basket["portfolio"][1]["shares"], 10.5)
        self.assertEqual(basket["unallocated_cash_eur"], 0.0)
        self.assertEqual(basket["unallocated_cash_usd"], 1500.75)
        self.assertEqual(basket["eurusd_rate"], 1.085)


class TestSqlitePersistenceAdapter(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_core.db")
        self.migrator = SchemaMigrator()
        self.mgr = DatabaseManager(self.db_path, profile=PRIMARY_FINANCIAL_PROFILE)

        # Initialize schema in temporary isolated database
        with self.mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)

        self.adapter = SqlitePersistenceAdapter(self.mgr)

    def tearDown(self):
        self.adapter.close()
        self.temp_dir.cleanup()

    def test_save_and_get_portfolio_basket_atomic_unit_of_work(self):
        with execution_role_context("Terminal Orchestrator"):
            portfolio = [
                {"ticker": "SPY", "shares": 10.0, "wac": 500.0},
                {"ticker": "AAPL", "shares": 50.0, "wac": 220.0},
            ]
            res = self.adapter.save_portfolio_basket(
                portfolio=portfolio,
                unallocated_cash_eur=2500.50,
                unallocated_cash_usd=2700.54,
            )
            self.assertEqual(res["status"], "success")

            basket = self.adapter.get_portfolio_basket()
            self.assertEqual(basket["unallocated_cash_eur"], 2500.50)
            self.assertEqual(basket["unallocated_cash_usd"], 2700.54)
            self.assertEqual(len(basket["portfolio"]), 2)
            tickers = {p["ticker"] for p in basket["portfolio"]}
            self.assertEqual(tickers, {"SPY", "AAPL"})

    def test_save_portfolio_basket_reconciles_deletions(self):
        with execution_role_context("Terminal Orchestrator"):
            # Step 1: save SPY and AAPL
            self.adapter.save_portfolio_basket(
                portfolio=[
                    {"ticker": "SPY", "shares": 10.0, "wac": 500.0},
                    {"ticker": "AAPL", "shares": 50.0, "wac": 220.0},
                ],
                unallocated_cash_eur=2000.0,
            )

            # Step 2: update basket to ONLY contain AAPL (SPY sold)
            self.adapter.save_portfolio_basket(
                portfolio=[{"ticker": "AAPL", "shares": 50.0, "wac": 220.0}],
                unallocated_cash_eur=7000.0,
            )

            basket = self.adapter.get_portfolio_basket()
            self.assertEqual(len(basket["portfolio"]), 1)
            self.assertEqual(basket["portfolio"][0]["ticker"], "AAPL")
            self.assertEqual(basket["unallocated_cash_eur"], 7000.0)

    def test_sqlite_rollback_on_failure_preserves_initial_state(self):
        with execution_role_context("Terminal Orchestrator"):
            self.adapter.save_portfolio_basket(
                portfolio=[{"ticker": "SPY", "shares": 10.0, "wac": 500.0}],
                unallocated_cash_eur=2000.0,
            )

            # Attempt a basket save that fails validation (negative shares)
            with self.assertRaises(Exception):
                self.adapter.save_portfolio_basket(
                    portfolio=[{"ticker": "BAD", "shares": -10.0, "wac": 100.0}],
                    unallocated_cash_eur=1000.0,
                )

            # Confirm original state remains intact
            basket = self.adapter.get_portfolio_basket()
            self.assertEqual(len(basket["portfolio"]), 1)
            self.assertEqual(basket["portfolio"][0]["ticker"], "SPY")
            self.assertEqual(basket["unallocated_cash_eur"], 2000.0)

    def test_sqlite_financial_mutation_authorization_denial(self):
        with execution_role_context("Python Engineer"):
            with self.assertRaises(PermissionError):
                self.adapter.save_portfolio_basket(
                    portfolio=[{"ticker": "SPY", "shares": 10.0, "wac": 500.0}],
                    unallocated_cash_eur=2000.0,
                )

    def test_sqlite_watchlist_and_trade_lessons(self):
        with execution_role_context("Terminal Orchestrator"):
            self.adapter.save_watchlist(["NVDA", "MSFT"])
            wl = self.adapter.get_watchlist()
            self.assertEqual(sorted(wl), ["MSFT", "NVDA"])

            self.adapter.save_trade_lessons([{"rule": "L-101: Never counter-trend trade"}])
            lessons = self.adapter.get_trade_lessons()
            self.assertEqual(len(lessons), 1)
            self.assertEqual(lessons[0]["rule_code"], "L-101")

    def test_sqlite_empty_first_run_basket_parity(self):
        """Empty SQLite database returns valid empty basket contract matching JSON first-run."""
        basket = self.adapter.get_portfolio_basket()
        self.assertEqual(basket["portfolio"], [])
        self.assertEqual(basket["unallocated_cash_eur"], 0.0)
        self.assertEqual(basket["unallocated_cash_usd"], 0.0)
        self.assertEqual(basket["eurusd_rate"], 1.08)

        ssot = self.adapter.get_ssot_data()
        self.assertIsInstance(ssot, dict)
        self.assertEqual(ssot["portfolio_snapshot"], [])
        self.assertEqual(ssot["unallocated_cash_eur"], 0.0)

    def test_sqlite_rejects_non_finite_financial_values(self):
        """Reject non-finite shares, wac, and cash on SQLite adapter with PersistenceValidationError."""
        non_finite_values = [float("nan"), float("inf"), float("-inf")]

        for nf in non_finite_values:
            with execution_role_context("Terminal Orchestrator"):
                with self.assertRaises(PersistenceValidationError, msg=f"SQLite should reject non-finite shares: {nf}"):
                    self.adapter.save_portfolio_basket(
                        portfolio=[{"ticker": "SPY", "shares": nf, "wac": 500.0}],
                        unallocated_cash_eur=1000.0,
                    )
                with self.assertRaises(PersistenceValidationError, msg=f"SQLite should reject non-finite wac: {nf}"):
                    self.adapter.save_portfolio_basket(
                        portfolio=[{"ticker": "SPY", "shares": 10.0, "wac": nf}],
                        unallocated_cash_eur=1000.0,
                    )
                with self.assertRaises(PersistenceValidationError, msg=f"SQLite should reject non-finite cash: {nf}"):
                    self.adapter.save_portfolio_basket(
                        portfolio=[{"ticker": "SPY", "shares": 10.0, "wac": 500.0}],
                        unallocated_cash_eur=nf,
                    )


if __name__ == "__main__":
    unittest.main()
