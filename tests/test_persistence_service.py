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
    validate_finite_float,
)
from persistence.contracts import (
    validate_financial_decimal,
    format_financial_decimal,
    serialize_json_with_exact_decimals,
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

    def test_default_service_is_sqlite_when_database_present(self):
        service = get_persistence_service()
        self.assertIsInstance(service, PersistenceService)
        self.assertEqual(service.backend_name, "sqlite")
        self.assertIsInstance(service.adapter, SqlitePersistenceAdapter)

    def test_configure_explicit_json_mode(self):
        service = configure_persistence(backend="json")
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
        self.assertIsInstance(basket["portfolio"][0]["shares"], Decimal)
        self.assertEqual(basket["portfolio"][0]["shares"], Decimal("10.0"))
        self.assertIsInstance(basket["portfolio"][0]["wac"], Decimal)
        self.assertEqual(basket["portfolio"][0]["wac"], Decimal("500.0"))
        self.assertIsInstance(basket["unallocated_cash_eur"], Decimal)
        self.assertEqual(basket["unallocated_cash_eur"], Decimal("2500.0"))
        self.assertIsInstance(basket["unallocated_cash_usd"], Decimal)
        self.assertEqual(basket["unallocated_cash_usd"], Decimal("2700.0"))

    def test_save_portfolio_basket_preserves_external_sections(self):
        with execution_role_context("Terminal Orchestrator"):
            new_portfolio = [
                {"ticker": "NVDA", "shares": Decimal("15"), "wac": Decimal("120.00")},
                {"ticker": "AMD", "shares": Decimal("20"), "wac": Decimal("140.00")},
            ]
            res = self.adapter.save_portfolio_basket(
                portfolio=new_portfolio,
                unallocated_cash_eur=Decimal("3000.00"),
                unallocated_cash_usd=Decimal("3240.00"),
            )
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["unallocated_cash_eur"], Decimal("3000.00"))
            self.assertEqual(res["unallocated_cash_usd"], Decimal("3240.00"))

        # Read back raw SSoT to verify external section preservation and exact canonical string storage
        ssot = self.adapter.get_ssot_data()
        self.assertIn("state_context", ssot)
        self.assertEqual(ssot["state_context"]["regime"], "NORMAL")
        self.assertIn("runtime_flags", ssot)
        self.assertEqual(ssot["runtime_flags"]["dry_run"], False)

        # Check updated basket on disk & via get_portfolio_basket
        self.assertEqual(ssot["unallocated_cash_eur"], "3000.00")
        self.assertEqual(len(ssot["portfolio_snapshot"]), 2)
        self.assertEqual(ssot["portfolio_snapshot"][0]["ticker"], "NVDA")

        basket = self.adapter.get_portfolio_basket()
        self.assertEqual(basket["unallocated_cash_eur"], Decimal("3000.00"))
        self.assertEqual(basket["portfolio"][0]["shares"], Decimal("15"))

    def test_financial_mutation_rejected_for_engineering_roles(self):
        with execution_role_context("Python Engineer"):
            with self.assertRaises(PermissionError):
                self.adapter.save_portfolio_basket(
                    portfolio=[{"ticker": "HACK", "shares": Decimal("100"), "wac": Decimal("10.00")}],
                    unallocated_cash_eur=Decimal("9999.00"),
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
        self.assertEqual(basket["unallocated_cash_eur"], Decimal("0.00"))
        self.assertEqual(basket["unallocated_cash_usd"], Decimal("0.00"))
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
                        portfolio=[{"ticker": "SPY", "shares": nf, "wac": Decimal("500.0")}],
                        unallocated_cash_eur=Decimal("1000.00"),
                    )
                with self.assertRaises(PersistenceValidationError, msg=f"save_portfolio_basket should reject non-finite cash: {nf}"):
                    self.adapter.save_portfolio_basket(
                        portfolio=[{"ticker": "SPY", "shares": Decimal("10.0"), "wac": Decimal("500.0")}],
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
        self.assertEqual(basket["portfolio"][0]["shares"], Decimal("0.0"))
        self.assertEqual(basket["portfolio"][1]["shares"], Decimal("10.5"))
        self.assertEqual(basket["unallocated_cash_eur"], Decimal("0.0"))
        self.assertEqual(basket["unallocated_cash_usd"], Decimal("1500.75"))
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
                {"ticker": "SPY", "shares": Decimal("10"), "wac": Decimal("500.00")},
                {"ticker": "AAPL", "shares": Decimal("50"), "wac": Decimal("220.00")},
            ]
            res = self.adapter.save_portfolio_basket(
                portfolio=portfolio,
                unallocated_cash_eur=Decimal("2500.50"),
                unallocated_cash_usd=Decimal("2700.54"),
            )
            self.assertEqual(res["status"], "success")

            basket = self.adapter.get_portfolio_basket()
            self.assertEqual(basket["unallocated_cash_eur"], Decimal("2500.50"))
            self.assertEqual(basket["unallocated_cash_usd"], Decimal("2700.54"))
            self.assertEqual(len(basket["portfolio"]), 2)
            tickers = {p["ticker"] for p in basket["portfolio"]}
            self.assertEqual(tickers, {"SPY", "AAPL"})

    def test_save_portfolio_basket_reconciles_deletions(self):
        with execution_role_context("Terminal Orchestrator"):
            # Step 1: save SPY and AAPL
            self.adapter.save_portfolio_basket(
                portfolio=[
                    {"ticker": "SPY", "shares": Decimal("10"), "wac": Decimal("500.00")},
                    {"ticker": "AAPL", "shares": Decimal("50"), "wac": Decimal("220.00")},
                ],
                unallocated_cash_eur=Decimal("2000.00"),
            )

            # Step 2: update basket to ONLY contain AAPL (SPY sold)
            self.adapter.save_portfolio_basket(
                portfolio=[{"ticker": "AAPL", "shares": Decimal("50"), "wac": Decimal("220.00")}],
                unallocated_cash_eur=Decimal("7000.00"),
            )

            basket = self.adapter.get_portfolio_basket()
            self.assertEqual(len(basket["portfolio"]), 1)
            self.assertEqual(basket["portfolio"][0]["ticker"], "AAPL")
            self.assertEqual(basket["unallocated_cash_eur"], Decimal("7000.00"))

    def test_sqlite_rollback_on_failure_preserves_initial_state(self):
        with execution_role_context("Terminal Orchestrator"):
            self.adapter.save_portfolio_basket(
                portfolio=[{"ticker": "SPY", "shares": Decimal("10"), "wac": Decimal("500.00")}],
                unallocated_cash_eur=Decimal("2000.00"),
            )

            # Attempt a basket save that fails validation (negative shares)
            with self.assertRaises(Exception):
                self.adapter.save_portfolio_basket(
                    portfolio=[{"ticker": "BAD", "shares": Decimal("-10"), "wac": Decimal("100.00")}],
                    unallocated_cash_eur=Decimal("1000.00"),
                )

            # Confirm original state remains intact
            basket = self.adapter.get_portfolio_basket()
            self.assertEqual(len(basket["portfolio"]), 1)
            self.assertEqual(basket["portfolio"][0]["ticker"], "SPY")
            self.assertEqual(basket["unallocated_cash_eur"], Decimal("2000.00"))

    def test_sqlite_financial_mutation_authorization_denial(self):
        with execution_role_context("Python Engineer"):
            with self.assertRaises(PermissionError):
                self.adapter.save_portfolio_basket(
                    portfolio=[{"ticker": "SPY", "shares": Decimal("10"), "wac": Decimal("500.00")}],
                    unallocated_cash_eur=Decimal("2000.00"),
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
        self.assertEqual(basket["unallocated_cash_eur"], Decimal("0.00"))
        self.assertEqual(basket["unallocated_cash_usd"], Decimal("0.00"))
        self.assertEqual(basket["eurusd_rate"], 1.08)

        ssot = self.adapter.get_ssot_data()
        self.assertIsInstance(ssot, dict)
        self.assertEqual(ssot["portfolio_snapshot"], [])
        self.assertEqual(ssot["unallocated_cash_eur"], "0.00")

    def test_sqlite_rejects_non_finite_financial_values(self):
        """Reject non-finite shares, wac, and cash on SQLite adapter with PersistenceValidationError."""
        non_finite_values = [float("nan"), float("inf"), float("-inf"), Decimal("NaN"), Decimal("Infinity")]

        for nf in non_finite_values:
            with execution_role_context("Terminal Orchestrator"):
                with self.assertRaises(PersistenceValidationError, msg=f"SQLite should reject non-finite shares: {nf}"):
                    self.adapter.save_portfolio_basket(
                        portfolio=[{"ticker": "SPY", "shares": nf, "wac": Decimal("500.00")}],
                        unallocated_cash_eur=Decimal("1000.00"),
                    )
                with self.assertRaises(PersistenceValidationError, msg=f"SQLite should reject non-finite wac: {nf}"):
                    self.adapter.save_portfolio_basket(
                        portfolio=[{"ticker": "SPY", "shares": Decimal("10"), "wac": nf}],
                        unallocated_cash_eur=Decimal("1000.00"),
                    )
                with self.assertRaises(PersistenceValidationError, msg=f"SQLite should reject non-finite cash: {nf}"):
                    self.adapter.save_portfolio_basket(
                        portfolio=[{"ticker": "SPY", "shares": Decimal("10"), "wac": Decimal("500.00")}],
                        unallocated_cash_eur=nf,
                    )

    def test_sqlite_validates_wac_on_zero_shares_parity(self):
        """SQLite save_portfolio_basket validates wac even when shares=0 before omitting."""
        invalid_wac_values = [float("nan"), float("inf"), float("-inf"), "invalid_str", True, False, 10**500]
        for bad_wac in invalid_wac_values:
            with execution_role_context("Terminal Orchestrator"):
                with self.assertRaises(PersistenceValidationError, msg=f"Should reject zero-share position with bad wac {bad_wac}"):
                    self.adapter.save_portfolio_basket(
                        portfolio=[{"ticker": "SPY", "shares": Decimal("0"), "wac": bad_wac}],
                        unallocated_cash_eur=Decimal("1000.00"),
                    )

    def test_sqlite_rejects_zero_wac_for_active_position_with_validation_error(self):
        """SQLite save_portfolio_basket raises PersistenceValidationError (not generic PersistenceError) for shares > 0 with wac == 0."""
        with execution_role_context("Terminal Orchestrator"):
            with self.assertRaises(PersistenceValidationError) as ctx:
                self.adapter.save_portfolio_basket(
                    portfolio=[{"ticker": "NVDA", "shares": Decimal("1"), "wac": Decimal("0")}],
                    unallocated_cash_eur=Decimal("0"),
                )
            self.assertIn("greater than 0", str(ctx.exception))

            # Zero-share zero-WAC positions remain omitted cleanly
            res = self.adapter.save_portfolio_basket(
                portfolio=[{"ticker": "NVDA", "shares": Decimal("0"), "wac": Decimal("0")}],
                unallocated_cash_eur=Decimal("0"),
            )
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["portfolio"], [])

    def test_sqlite_get_portfolio_validates_corrupt_non_finite_records(self):
        """SQLite get_portfolio_basket rejects corrupted non-finite database records fail-closed."""
        with patch("persistence.sqlite_adapter.UnitOfWork") as mock_uow_cls:
            mock_uow = mock_uow_cls.return_value.__enter__.return_value
            mock_pos = type("PosRec", (), {"ticker": "CORRUPT", "shares": Decimal("10"), "wac_eur": float("nan")})()
            mock_uow.positions.list_positions.return_value = [mock_pos]
            mock_uow.cash.get_cash.return_value = None

            with self.assertRaises(PersistenceValidationError):
                self.adapter.get_portfolio_basket()

    def test_sqlite_get_ssot_data_missing_base_file_raises_integrity_error(self):
        """Missing configured base SSoT document raises PersistenceIntegrityError with chained cause."""
        missing_path = os.path.join(self.temp_dir.name, "nonexistent_ssot.json")
        adapter = SqlitePersistenceAdapter(self.mgr, base_ssot_path=missing_path)
        with self.assertRaises(PersistenceIntegrityError) as ctx:
            adapter.get_ssot_data()
        self.assertIn("unavailable or invalid", str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, (OSError, FileNotFoundError))

    def test_sqlite_get_ssot_data_malformed_json_raises_integrity_error(self):
        """Malformed JSON in configured base SSoT document raises PersistenceIntegrityError."""
        bad_json_path = os.path.join(self.temp_dir.name, "bad_ssot.json")
        with open(bad_json_path, "w", encoding="utf-8") as f:
            f.write("{invalid_json: true,")
        adapter = SqlitePersistenceAdapter(self.mgr, base_ssot_path=bad_json_path)
        with self.assertRaises(PersistenceIntegrityError) as ctx:
            adapter.get_ssot_data()
        self.assertIn("unavailable or invalid", str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, json.JSONDecodeError)

    def test_sqlite_get_ssot_data_invalid_utf8_raises_integrity_error(self):
        """Invalid UTF-8 encoding in configured base SSoT document raises PersistenceIntegrityError."""
        invalid_utf8_path = os.path.join(self.temp_dir.name, "invalid_utf8_ssot.json")
        with open(invalid_utf8_path, "wb") as f:
            f.write(b"\x80\x81\xff\xfe\x00\x01not valid utf8")
        adapter = SqlitePersistenceAdapter(self.mgr, base_ssot_path=invalid_utf8_path)
        with self.assertRaises(PersistenceIntegrityError) as ctx:
            adapter.get_ssot_data()
        self.assertIn("unavailable or invalid", str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, UnicodeError)

    def test_sqlite_get_ssot_data_invalid_root_type_raises_integrity_error(self):
        """Non-dictionary root in configured base SSoT document raises PersistenceIntegrityError."""
        list_root_path = os.path.join(self.temp_dir.name, "list_root.json")
        with open(list_root_path, "w", encoding="utf-8") as f:
            json.dump(["not", "a", "dictionary"], f)
        adapter = SqlitePersistenceAdapter(self.mgr, base_ssot_path=list_root_path)
        with self.assertRaises(PersistenceIntegrityError) as ctx:
            adapter.get_ssot_data()
        self.assertIn("unavailable or invalid", str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, PersistenceValidationError)

    def test_sqlite_get_ssot_data_read_failure_raises_integrity_error(self):
        """Read failure / permission denial on base SSoT document raises PersistenceIntegrityError."""
        read_fail_path = os.path.join(self.temp_dir.name, "denied_ssot.json")
        with open(read_fail_path, "w", encoding="utf-8") as f:
            json.dump({"test": "data"}, f)
        adapter = SqlitePersistenceAdapter(self.mgr, base_ssot_path=read_fail_path)
        with patch("builtins.open", side_effect=PermissionError("Access denied")):
            with self.assertRaises(PersistenceIntegrityError) as ctx:
                adapter.get_ssot_data()
            self.assertIn("unavailable or invalid", str(ctx.exception))
            self.assertIsInstance(ctx.exception.__cause__, PermissionError)

    def test_sqlite_get_ssot_data_preserves_external_sections_when_valid(self):
        """Valid configured base SSoT document preserves externally managed non-tabular sections."""
        valid_base_path = os.path.join(self.temp_dir.name, "valid_base.json")
        base_content = {
            "portfolio_notes": "Preserve long-term risk mandate",
            "rebalancing_schedule": {"cadence": "weekly", "day": "Friday"},
            "custom_metadata": {"author": "Investment Council"},
        }
        with open(valid_base_path, "w", encoding="utf-8") as f:
            json.dump(base_content, f)
        adapter = SqlitePersistenceAdapter(self.mgr, base_ssot_path=valid_base_path)
        ssot = adapter.get_ssot_data()
        self.assertEqual(ssot.get("portfolio_notes"), "Preserve long-term risk mandate")
        self.assertEqual(ssot.get("rebalancing_schedule"), {"cadence": "weekly", "day": "Friday"})
        self.assertEqual(ssot.get("custom_metadata"), {"author": "Investment Council"})
        self.assertIn("_projection_metadata", ssot)
        self.assertIn("portfolio_snapshot", ssot)

    def test_sqlite_get_ssot_data_none_base_path_succeeds_without_external_sections(self):
        """Explicitly unconfigured base document (None) succeeds without external sections."""
        adapter = SqlitePersistenceAdapter(self.mgr, base_ssot_path=None)
        ssot = adapter.get_ssot_data()
        self.assertIn("_projection_metadata", ssot)
        self.assertIn("portfolio_snapshot", ssot)
        self.assertIn("unallocated_cash_eur", ssot)


class TestValidateFiniteFloatHardening(unittest.TestCase):
    """Direct unit tests for validate_finite_float hardening (Scope C)."""

    def test_rejects_boolean_types(self):
        """Booleans must be explicitly rejected and not cast to 1.0 or 0.0."""
        with self.assertRaises(PersistenceValidationError) as ctx_true:
            validate_finite_float(True, "test_field")
        self.assertIn("Boolean values not allowed", str(ctx_true.exception))

        with self.assertRaises(PersistenceValidationError) as ctx_false:
            validate_finite_float(False, "test_field")
        self.assertIn("Boolean values not allowed", str(ctx_false.exception))

    def test_rejects_string_types(self):
        """String values must be rejected fail-closed to prevent accidental conversions."""
        bad_strings = ["100.5", "0.0", "0", "NaN", "Infinity", "invalid", ""]
        for s in bad_strings:
            with self.assertRaises(PersistenceValidationError) as ctx:
                validate_finite_float(s, "test_field")
            self.assertIn("String values not allowed", str(ctx.exception))

    def test_handles_conversion_overflow_safely(self):
        """Massive integers or exponents causing OverflowError must be caught fail-closed."""
        huge_int = 10**500
        with self.assertRaises(PersistenceValidationError) as ctx:
            validate_finite_float(huge_int, "test_field")
        self.assertIn("Invalid numeric value", str(ctx.exception))

    def test_rejects_non_finite_floats_and_decimals(self):
        """NaN, +inf, -inf in float and Decimal types must be rejected."""
        non_finite = [
            float("nan"),
            float("inf"),
            float("-inf"),
            Decimal("NaN"),
            Decimal("Infinity"),
            Decimal("-Infinity"),
        ]
        for nf in non_finite:
            with self.assertRaises(PersistenceValidationError):
                validate_finite_float(nf, "test_field")

    def test_accepts_valid_numerics_and_preserves_legitimate_zeros(self):
        """Valid int, float, and Decimal positive and zero values must be accepted."""
        # Int
        self.assertEqual(validate_finite_float(100, "test_field"), 100.0)
        self.assertEqual(validate_finite_float(0, "test_field"), 0.0)

        # Float
        self.assertEqual(validate_finite_float(123.456, "test_field"), 123.456)
        self.assertEqual(validate_finite_float(0.0, "test_field"), 0.0)

        # Decimal
        self.assertEqual(validate_finite_float(Decimal("789.12"), "test_field"), 789.12)
        self.assertEqual(validate_finite_float(Decimal("0.0"), "test_field"), 0.0)

    def test_enforces_allow_zero_and_non_negative_constraints(self):
        """Zero values are rejected when allow_zero=False; negative values rejected when non_negative=True."""
        with self.assertRaises(PersistenceValidationError):
            validate_finite_float(0.0, "test_field", allow_zero=False)

        with self.assertRaises(PersistenceValidationError):
            validate_finite_float(-5.0, "test_field", non_negative=True)

        # Negative allowed when non_negative=False
        self.assertEqual(validate_finite_float(-5.0, "test_field", non_negative=False), -5.0)

    def test_rejects_custom_object_implementing_float(self):
        """Custom objects that define __float__() must be rejected fail-closed under explicit allowlist."""
        class CustomFloatObj:
            def __float__(self):
                return 42.0

        with self.assertRaises(PersistenceValidationError) as ctx:
            validate_finite_float(CustomFloatObj(), "test_field")
        self.assertIn("Unsupported type CustomFloatObj", str(ctx.exception))

    def test_rejects_other_unsupported_types(self):
        """None, list, dict, set, and tuple must be rejected fail-closed under explicit allowlist."""
        unsupported = [None, [1.0], {"value": 1.0}, {1.0}, (1.0,)]
        for u in unsupported:
            with self.assertRaises(PersistenceValidationError) as ctx:
                validate_finite_float(u, "test_field")
            self.assertIn("Unsupported type", str(ctx.exception))


class TestJsonAndSqliteValidationParity(unittest.TestCase):
    """Verifies that JsonPersistenceAdapter and SqlitePersistenceAdapter exhibit identical validation behavior."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.context_dir = os.path.join(self.temp_dir.name, "context")
        os.makedirs(self.context_dir, exist_ok=True)
        self.ssot_path = os.path.join(self.context_dir, "ssot.json")

        self.json_adapter = JsonPersistenceAdapter(base_dir=self.temp_dir.name)

        db_path = os.path.join(self.temp_dir.name, "parity_test.db")
        self.db_manager = DatabaseManager(db_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with self.db_manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
        self.sqlite_adapter = SqlitePersistenceAdapter(self.db_manager)

    def tearDown(self):
        self.sqlite_adapter.close()
        self.temp_dir.cleanup()

    def test_parity_on_boolean_rejection(self):
        """Both adapters reject boolean shares and wac."""
        for adapter in (self.json_adapter, self.sqlite_adapter):
            with execution_role_context("Terminal Orchestrator"):
                with self.assertRaises(PersistenceValidationError):
                    adapter.save_portfolio_basket(
                        portfolio=[{"ticker": "SPY", "shares": True, "wac": Decimal("500.00")}],
                        unallocated_cash_eur=Decimal("1000.00"),
                    )
                with self.assertRaises(PersistenceValidationError):
                    adapter.save_portfolio_basket(
                        portfolio=[{"ticker": "SPY", "shares": Decimal("10"), "wac": False}],
                        unallocated_cash_eur=Decimal("1000.00"),
                    )

    def test_parity_on_raw_float_and_malformed_string_rejection(self):
        """Both adapters reject raw binary floats and malformed/whitespace/scientific strings."""
        for adapter in (self.json_adapter, self.sqlite_adapter):
            with execution_role_context("Terminal Orchestrator"):
                with self.assertRaises(PersistenceValidationError):
                    adapter.save_portfolio_basket(
                        portfolio=[{"ticker": "SPY", "shares": 10.5, "wac": Decimal("500.00")}],
                        unallocated_cash_eur=Decimal("1000.00"),
                    )
                with self.assertRaises(PersistenceValidationError):
                    adapter.save_portfolio_basket(
                        portfolio=[{"ticker": "SPY", "shares": Decimal("10"), "wac": 500.25}],
                        unallocated_cash_eur=Decimal("1000.00"),
                    )
                with self.assertRaises(PersistenceValidationError):
                    adapter.save_portfolio_basket(
                        portfolio=[{"ticker": "SPY", "shares": Decimal("10"), "wac": Decimal("500.00")}],
                        unallocated_cash_eur=1000.5,
                    )
                with self.assertRaises(PersistenceValidationError):
                    adapter.save_portfolio_basket(
                        portfolio=[{"ticker": "SPY", "shares": " 10 ", "wac": Decimal("500.00")}],
                        unallocated_cash_eur=Decimal("1000.00"),
                    )
                with self.assertRaises(PersistenceValidationError):
                    adapter.save_portfolio_basket(
                        portfolio=[{"ticker": "SPY", "shares": "1e2", "wac": Decimal("500.00")}],
                        unallocated_cash_eur=Decimal("1000.00"),
                    )

    def test_parity_on_zero_share_wac_validation(self):
        """Both adapters validate wac on zero-share positions before omitting."""
        for adapter in (self.json_adapter, self.sqlite_adapter):
            with execution_role_context("Terminal Orchestrator"):
                with self.assertRaises(PersistenceValidationError):
                    adapter.save_portfolio_basket(
                        portfolio=[{"ticker": "SPY", "shares": Decimal("0"), "wac": float("nan")}],
                        unallocated_cash_eur=Decimal("1000.00"),
                    )
                with self.assertRaises(PersistenceValidationError):
                    adapter.save_portfolio_basket(
                        portfolio=[{"ticker": "SPY", "shares": Decimal("0"), "wac": "corrupt"}],
                        unallocated_cash_eur=Decimal("1000.00"),
                    )

    def test_parity_on_custom_object_rejection(self):
        """Both adapters reject custom objects implementing __float__()."""
        class CustomFloat:
            def __float__(self):
                return 150.0

        for adapter in (self.json_adapter, self.sqlite_adapter):
            with execution_role_context("Terminal Orchestrator"):
                with self.assertRaises(PersistenceValidationError):
                    adapter.save_portfolio_basket(
                        portfolio=[{"ticker": "SPY", "shares": CustomFloat(), "wac": Decimal("500.00")}],
                        unallocated_cash_eur=Decimal("1000.00"),
                    )
                with self.assertRaises(PersistenceValidationError):
                    adapter.save_portfolio_basket(
                        portfolio=[{"ticker": "SPY", "shares": Decimal("10"), "wac": CustomFloat()}],
                        unallocated_cash_eur=Decimal("1000.00"),
                    )
                with self.assertRaises(PersistenceValidationError):
                    adapter.save_portfolio_basket(
                        portfolio=[{"ticker": "SPY", "shares": Decimal("10"), "wac": Decimal("500.00")}],
                        unallocated_cash_eur=CustomFloat(),
                    )


class TestStage37FinancialPrecisionAndCompositeAtomicity(unittest.TestCase):
    """Regression suite covering Stage 3.7 R2 (Decimal boundaries) and R4 (composite atomicity)."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.temp_dir.name, "context"), exist_ok=True)
        self.json_adapter = JsonPersistenceAdapter(base_dir=self.temp_dir.name)

        self.db_path = os.path.join(self.temp_dir.name, "stage37.db")
        self.db_manager = DatabaseManager(self.db_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with self.db_manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
        self.sqlite_adapter = SqlitePersistenceAdapter(self.db_manager)

    def tearDown(self):
        self.sqlite_adapter.close()
        self.temp_dir.cleanup()

    def test_validate_financial_decimal_exactness_and_rejections(self):
        """validate_financial_decimal enforces Decimal/int/canonical-str and rejects float, bool, scientific, scale overflow, and non-finite inputs."""
        self.assertEqual(validate_financial_decimal(Decimal("12.3456"), "shares", max_scale=4), Decimal("12.3456"))
        self.assertEqual(validate_financial_decimal("12.3456", "shares", max_scale=4), Decimal("12.3456"))
        self.assertEqual(validate_financial_decimal(42, "shares", max_scale=4), Decimal("42"))
        self.assertEqual(validate_financial_decimal(Decimal("-0.00"), "shares", max_scale=4), Decimal("0.00"))

        invalid_inputs = [
            (True, 4),
            (False, 4),
            (12.34, 4),
            (float("nan"), 4),
            (float("inf"), 4),
            (Decimal("NaN"), 4),
            (Decimal("Infinity"), 4),
            (Decimal("-Infinity"), 4),
            (Decimal("-0.01"), 4),
            (Decimal("0.12345"), 4),
            (Decimal("10.999"), 2),
            ("0.12345", 4),
            (" 10.5", 4),
            ("10.5 ", 4),
            ("1e-3", 4),
            ("007.5", 4),
            (".5", 4),
            ("5.", 4),
            ("NaN", 4),
            ("", 4),
            (None, 4),
            (Decimal("1" * 29), 4),
            (Decimal("1E+29"), 4),
            (Decimal("1E+100"), 4),
            (Decimal("1e9999"), 4),
            (Decimal("1E+28"), 2),
            (Decimal("1" + "0" * 28), 2),
        ]
        for bad_val, scale in invalid_inputs:
            with self.assertRaises(PersistenceValidationError, msg=f"Should reject {bad_val!r} at scale={scale}"):
                validate_financial_decimal(bad_val, "test_field", max_scale=scale)

        # JSON numeric parsing with parse_float=Decimal must also fail-closed on oversized positive exponents
        oversized_json_tokens = [
            '{"amount": 1e100}',
            '{"amount": 1E+29}',
            '{"amount": 1e9999}',
            '{"amount": 100000000000000000000000000000}',  # 30-digit integer
        ]
        for token_json in oversized_json_tokens:
            parsed_token = json.loads(token_json, parse_float=Decimal)
            with self.assertRaises(PersistenceValidationError, msg=f"Should reject JSON token {token_json}"):
                validate_financial_decimal(parsed_token["amount"], "amount", max_scale=4)

        with self.assertRaises(PersistenceValidationError):
            validate_financial_decimal("0", "shares", max_scale=4, allow_zero=False)

        raw_json = serialize_json_with_exact_decimals({
            "shares": Decimal("12.3456"),
            "wac": Decimal("123.4567"),
            "unallocated_cash_eur": Decimal("1000.55"),
        })
        parsed = json.loads(raw_json, parse_float=Decimal)
        self.assertEqual(parsed["shares"], Decimal("12.3456"))
        self.assertEqual(parsed["wac"], Decimal("123.4567"))
        self.assertEqual(parsed["unallocated_cash_eur"], Decimal("1000.55"))

    def test_stage371_decimal_exponent_bounds_and_magnitude_enforcement(self):
        """F37-01: Positive Decimal exponents expanding magnitude beyond 28 digits are rejected fail-closed."""
        # 1. Non-zero positive exponents that exceed 28 digits must raise PersistenceValidationError
        for oversized in (
            Decimal("1E+28"),  # 1 followed by 28 zeros = 29 digits
            Decimal("1E+29"),  # 30 digits
            Decimal("1E+100"), # 101 digits
            Decimal("1e9999"), # 10000 digits
            Decimal("9.9E+28"),# 99 followed by 27 zeros = 29 digits
            Decimal("1.0001E+28"), # 10001 followed by 24 zeros = 29 digits
        ):
            with self.assertRaises(PersistenceValidationError, msg=f"Expected rejection for {oversized}"):
                validate_financial_decimal(oversized, "test_field", max_scale=4)

        # 2. Defensive rejection in format_financial_decimal
        for bad_decimal in (
            Decimal("1E+28"),
            Decimal("1E+29"),
            Decimal("1E+100"),
            Decimal("1e9999"),
            Decimal("9.9E+28"),
            Decimal("9" * 29),
        ):
            with self.assertRaises(PersistenceValidationError, msg=f"Expected format rejection for {bad_decimal}"):
                format_financial_decimal(bad_decimal, min_scale=2)

    def test_stage371_maximum_valid_28_digit_boundary_and_formatting(self):
        """F37-01: Valid 28-digit maximum financial values are accepted and format cleanly with min_scale=2 without InvalidOperation."""
        # Exact 28-digit integer cash amount
        max_28_int = Decimal("9" * 28)
        self.assertEqual(validate_financial_decimal(max_28_int, "cash", max_scale=2), max_28_int)

        # format_financial_decimal with min_scale=2 requires prec >= 30, handled cleanly via localcontext
        formatted_28 = format_financial_decimal(max_28_int, min_scale=2)
        self.assertEqual(formatted_28, ("9" * 28) + ".00")

        # format_financial_decimal with min_scale=0
        formatted_28_raw = format_financial_decimal(max_28_int, min_scale=0)
        self.assertEqual(formatted_28_raw, "9" * 28)

        # Exact 28-digit value expressed with positive exponent (1 followed by 27 zeros = 28 digits)
        max_28_exp = Decimal("1E+27")
        self.assertEqual(validate_financial_decimal(max_28_exp, "cash", max_scale=2), max_28_exp)
        formatted_exp = format_financial_decimal(max_28_exp, min_scale=2)
        self.assertEqual(formatted_exp, ("1" + "0" * 27) + ".00")

        # Exact 28-digit value with non-unit coefficient (99 followed by 26 zeros = 28 digits)
        max_28_coeff = Decimal("9.9E+27")
        self.assertEqual(validate_financial_decimal(max_28_coeff, "cash", max_scale=2), max_28_coeff)
        formatted_coeff = format_financial_decimal(max_28_coeff, min_scale=2)
        self.assertEqual(formatted_coeff, ("99" + "0" * 26) + ".00")

        # Zero with min_scale=2 formats cleanly
        self.assertEqual(format_financial_decimal(Decimal("0"), min_scale=2), "0.00")
        self.assertEqual(format_financial_decimal(Decimal("0.0"), min_scale=2), "0.00")

    def test_stage371_adapter_positive_exponent_and_boundary_parity(self):
        """F37-01 parity: Both adapters reject positive-exponent overflows and accept valid 28-digit cash amounts."""
        for adapter in (self.json_adapter, self.sqlite_adapter):
            with execution_role_context("Terminal Orchestrator"):
                # Reject positive-exponent cash overflow
                with self.assertRaises(PersistenceValidationError):
                    adapter.save_portfolio_basket(
                        portfolio=[],
                        unallocated_cash_eur=Decimal("1E+29"),
                    )

                # Reject positive-exponent shares overflow
                with self.assertRaises(PersistenceValidationError):
                    adapter.save_portfolio_basket(
                        portfolio=[{"ticker": "SPY", "shares": Decimal("1E+29"), "wac": Decimal("500.00")}],
                        unallocated_cash_eur=Decimal("1000.00"),
                    )

                # Accept valid 28-digit maximum cash amount with explicit EUR and USD amounts
                adapter.save_portfolio_basket(
                    portfolio=[{"ticker": "SPY", "shares": Decimal("10.0000"), "wac": Decimal("500.00")}],
                    unallocated_cash_eur=Decimal("9" * 28),
                    unallocated_cash_usd=Decimal("9" * 28),
                )
                basket = adapter.get_portfolio_basket()
                self.assertEqual(basket["unallocated_cash_eur"], Decimal("9" * 28))
                self.assertEqual(basket["unallocated_cash_usd"], Decimal("9" * 28))

    def test_exact_decimal_round_trip_both_adapters(self):
        """Both adapters preserve exact 4-decimal share/WAC and 2-decimal cash values without float drift."""
        for adapter in (self.json_adapter, self.sqlite_adapter):
            with execution_role_context("Terminal Orchestrator"):
                adapter.save_portfolio_basket(
                    portfolio=[{"ticker": "NVDA", "shares": Decimal("12.3456"), "wac": Decimal("123.4567")}],
                    unallocated_cash_eur=Decimal("1000.55"),
                    unallocated_cash_usd=Decimal("1080.59"),
                )
                basket = adapter.get_portfolio_basket()
                self.assertEqual(basket["portfolio"][0]["shares"], Decimal("12.3456"))
                self.assertEqual(basket["portfolio"][0]["wac"], Decimal("123.4567"))
                self.assertEqual(basket["unallocated_cash_eur"], Decimal("1000.55"))
                self.assertEqual(basket["unallocated_cash_usd"], Decimal("1080.59"))

    def test_sqlite_composite_ssot_update_single_uow_rolls_back_all_on_substep_failure(self):
        """Failure in decision_log or trade_lessons during SQLite update_ssot_payload rolls back portfolio/cash and earlier writes."""
        with execution_role_context("Terminal Orchestrator"):
            self.sqlite_adapter.save_portfolio_basket(
                portfolio=[{"ticker": "SPY", "shares": Decimal("5"), "wac": Decimal("500.00")}],
                unallocated_cash_eur=Decimal("2000.00"),
                unallocated_cash_usd=Decimal("2160.00"),
            )

            composite_payload = {
                "mutable_state": {
                    "portfolio_snapshot": [
                        {"ticker": "NVDA", "shares": "12.3456", "wac": "123.4567", "trade_state": "BUY"}
                    ],
                    "unallocated_cash_eur": "500.25",
                    "unallocated_cash_usd": "540.27",
                },
                "decision_log_entry": {
                    "session_id": "sess_composite_fail",
                    "ticker": "NVDA",
                    "action_proposed": "BUY",
                    "decision_outcome": "APPROVED",
                    "timestamp_utc": "2026-11-16T12:00:00Z",
                },
                "trade_lessons": [{"rule": "L-777: Never split composite transactions"}],
            }

            # 1. Decision log failure rolls back portfolio/cash update
            with patch(
                "python.database.repositories.governance.DecisionLogRepository.append_decision",
                side_effect=RuntimeError("Simulated decision_log failure"),
            ):
                with self.assertRaises(PersistenceError):
                    self.sqlite_adapter.update_ssot_payload(composite_payload)

            basket_after_dl_fail = self.sqlite_adapter.get_portfolio_basket()
            self.assertEqual(basket_after_dl_fail["portfolio"][0]["ticker"], "SPY")
            self.assertEqual(basket_after_dl_fail["unallocated_cash_eur"], Decimal("2000.00"))
            self.assertEqual(self.sqlite_adapter.get_decision_log(), [])
            self.assertEqual(self.sqlite_adapter.get_trade_lessons(), [])

            # 2. Trade lessons failure rolls back BOTH portfolio/cash AND decision_log insert
            with patch(
                "python.database.repositories.governance.TradeLessonRepository.upsert_lesson",
                side_effect=RuntimeError("Simulated trade_lesson failure"),
            ):
                with self.assertRaises(PersistenceError):
                    self.sqlite_adapter.update_ssot_payload(composite_payload)

            basket_after_tl_fail = self.sqlite_adapter.get_portfolio_basket()
            self.assertEqual(basket_after_tl_fail["portfolio"][0]["ticker"], "SPY")
            self.assertEqual(basket_after_tl_fail["unallocated_cash_eur"], Decimal("2000.00"))
            self.assertEqual(self.sqlite_adapter.get_decision_log(), [])
            self.assertEqual(self.sqlite_adapter.get_trade_lessons(), [])

            # 3. Valid composite update commits portfolio/cash, decision_log, and trade_lessons atomically
            res = self.sqlite_adapter.update_ssot_payload(composite_payload)
            self.assertEqual(res["status"], "success")
            basket_ok = self.sqlite_adapter.get_portfolio_basket()
            self.assertEqual(basket_ok["portfolio"][0]["ticker"], "NVDA")
            self.assertEqual(basket_ok["portfolio"][0]["shares"], Decimal("12.3456"))
            self.assertEqual(basket_ok["unallocated_cash_eur"], Decimal("500.25"))
            self.assertEqual(len(self.sqlite_adapter.get_decision_log()), 1)
            self.assertEqual(self.sqlite_adapter.get_decision_log()[0]["ticker"], "NVDA")
            self.assertEqual(len(self.sqlite_adapter.get_trade_lessons()), 1)
            self.assertEqual(self.sqlite_adapter.get_trade_lessons()[0]["rule_code"], "L-777")

    def test_json_composite_ssot_update_rolls_back_earlier_files_on_failure(self):
        """Failure in decision_log or trade_lessons during JSON update_ssot_payload restores all prior file bytes."""
        initial_ssot = {
            "mutable_state": {
                "portfolio_snapshot": [{"ticker": "SPY", "shares": "5", "wac": "500.00"}],
                "unallocated_cash_eur": "2000.00",
                "unallocated_cash_usd": "2160.00",
            }
        }
        initial_decisions = [{"session_id": "orig", "ticker": "SPY"}]
        initial_lessons = [{"id": 1, "rule": "L-001: Baseline rule"}]
        with open(self.json_adapter.ssot_path, "w", encoding="utf-8") as f:
            json.dump(initial_ssot, f)
        with open(self.json_adapter.decision_log_path, "w", encoding="utf-8") as f:
            json.dump(initial_decisions, f)
        with open(self.json_adapter.trade_lessons_path, "w", encoding="utf-8") as f:
            json.dump(initial_lessons, f)

        with open(self.json_adapter.ssot_path, "rb") as f:
            ssot_bytes_before = f.read()
        with open(self.json_adapter.decision_log_path, "rb") as f:
            dl_bytes_before = f.read()
        with open(self.json_adapter.trade_lessons_path, "rb") as f:
            tl_bytes_before = f.read()

        composite_payload = {
            "mutable_state": {
                "portfolio_snapshot": [
                    {"ticker": "NVDA", "shares": "12.3456", "wac": "123.4567", "trade_state": "BUY"}
                ],
                "unallocated_cash_eur": "500.25",
                "unallocated_cash_usd": "540.27",
            },
            "trade_lessons": [{"rule": "L-888: Atomic rollback works"}],
        }

        with execution_role_context("Terminal Orchestrator"):
            # 1. Inject failure in decision log after ssot.json was updated -> ssot.json must be restored byte-for-byte
            with patch.object(
                self.json_adapter,
                "append_decision_log",
                side_effect=PersistenceError("Simulated JSON decision log failure"),
            ):
                with self.assertRaises(PersistenceError):
                    self.json_adapter.update_ssot_payload(composite_payload)

            with open(self.json_adapter.ssot_path, "rb") as f:
                self.assertEqual(f.read(), ssot_bytes_before)
            with open(self.json_adapter.decision_log_path, "rb") as f:
                self.assertEqual(f.read(), dl_bytes_before)
            with open(self.json_adapter.trade_lessons_path, "rb") as f:
                self.assertEqual(f.read(), tl_bytes_before)

            # 2. Inject failure in trade_lessons after BOTH ssot.json and decision_log.json were updated -> BOTH must roll back
            with patch.object(
                self.json_adapter,
                "save_trade_lessons",
                side_effect=PersistenceError("Simulated JSON trade lessons failure"),
            ):
                with self.assertRaises(PersistenceError):
                    self.json_adapter.update_ssot_payload(composite_payload)

            with open(self.json_adapter.ssot_path, "rb") as f:
                self.assertEqual(f.read(), ssot_bytes_before)
            with open(self.json_adapter.decision_log_path, "rb") as f:
                self.assertEqual(f.read(), dl_bytes_before)
            with open(self.json_adapter.trade_lessons_path, "rb") as f:
                self.assertEqual(f.read(), tl_bytes_before)

            # 3. Valid composite payload updates all three files cleanly
            res = self.json_adapter.update_ssot_payload(composite_payload)
            self.assertEqual(res["status"], "success")
            basket = self.json_adapter.get_portfolio_basket()
            self.assertEqual(basket["portfolio"][0]["ticker"], "NVDA")
            self.assertEqual(basket["portfolio"][0]["shares"], Decimal("12.3456"))
            self.assertEqual(len(self.json_adapter.get_decision_log()), 2)
            self.assertEqual(len(self.json_adapter.get_trade_lessons()), 2)


if __name__ == "__main__":
    unittest.main()
