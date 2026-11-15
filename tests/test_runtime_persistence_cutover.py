"""
Automated Integration Test Suite for Runtime Persistence Cutover & Consumer Parity.
Governed by Stage 3.2 Architecture and Agent Governance Framework.

Covers:
1. Cutover Promotion Atomicity & Protected Asset Invariance (SHA-256 byte-for-byte invariance).
2. SqlitePersistenceAdapter Canonical Projection Completeness, Non-Tabular Preservation, & Revision Invariance.
3. Consumer Parity across /api/data, /api/chat, and tools.get_market_data().
4. Fail-Closed Degradation on Database Corruption / Disconnection (zero silent fallback).
5. Pre-Cutover JSON Configuration Test Mode.
"""

from contextlib import contextmanager
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from python.database.manager import DatabaseManager, PRIMARY_FINANCIAL_PROFILE
from python.database.schema import SchemaMigrator
from python.database.projections import SSoTProjectionGenerator
from python.database.repositories.base import utc_now_iso
from python.persistence.contracts import (
    PersistenceError,
    PersistenceIntegrityError,
    PersistenceValidationError,
)
from python.persistence.json_adapter import JsonPersistenceAdapter
from python.persistence.sqlite_adapter import SqlitePersistenceAdapter
from python.persistence.service import (
    PersistenceService,
    get_persistence_service,
    configure_persistence,
    reset_persistence_service,
    perform_sqlite_cutover,
)
from python.tool_registry import execution_role_context

try:
    import fetch_stocks
except ImportError:
    import python.fetch_stocks as fetch_stocks

try:
    import web_server
except ImportError:
    import python.web_server as web_server

try:
    import tools
except ImportError:
    import python.tools as tools


@contextmanager
def patch_global_state(state):
    """Temporarily swaps the published snapshot in fetch_stocks."""
    orig = fetch_stocks.get_global_state_snapshot()
    fetch_stocks.publish_global_state_snapshot(state)
    try:
        yield
    finally:
        fetch_stocks.publish_global_state_snapshot(orig)


class TestRuntimePersistenceCutoverAndInvariance(unittest.TestCase):
    """Verifies that the SQLite cutover preserves protected assets and initializes context/gem.db cleanly."""

    def setUp(self):
        reset_persistence_service()
        self.repo_root = Path(__file__).resolve().parent.parent
        self.ssot_path = self.repo_root / "context" / "ssot.json"
        self.lessons_path = self.repo_root / "context" / "trade_lessons.json"
        self.decisions_path = self.repo_root / "context" / "decision_log.json"
        self.db_path = self.repo_root / "context" / "gem.db"

    def tearDown(self):
        reset_persistence_service()

    def test_protected_json_hashes_byte_for_byte_preserved(self):
        """Verify that protected JSON financial assets have exact expected SHA-256 hashes."""
        expected_ssot_hash = "0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da"
        expected_lessons_hash = "0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463"
        expected_decisions_hash = "757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2"

        actual_ssot_hash = hashlib.sha256(self.ssot_path.read_bytes()).hexdigest().lower()
        actual_lessons_hash = hashlib.sha256(self.lessons_path.read_bytes()).hexdigest().lower()
        actual_decisions_hash = hashlib.sha256(self.decisions_path.read_bytes()).hexdigest().lower()

        self.assertEqual(actual_ssot_hash, expected_ssot_hash, "context/ssot.json hash mismatch!")
        self.assertEqual(actual_lessons_hash, expected_lessons_hash, "context/trade_lessons.json hash mismatch!")
        self.assertEqual(actual_decisions_hash, expected_decisions_hash, "context/decision_log.json hash mismatch!")

    def test_active_production_database_exists_and_healthy(self):
        """Verify context/gem.db exists, has valid schema, and passes integrity checks."""
        self.assertTrue(self.db_path.exists(), "context/gem.db must exist after Stage 3.2 cutover")
        mgr = DatabaseManager(str(self.db_path), profile=PRIMARY_FINANCIAL_PROFILE)
        self.assertEqual(mgr.verify_integrity(), ["ok"])

        # Check records imported
        with mgr.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM portfolio_positions;")
            pos_count = cur.fetchone()[0]
            self.assertGreaterEqual(pos_count, 1)

            cur.execute("SELECT count(*) FROM watchlist;")
            wl_count = cur.fetchone()[0]
            self.assertGreaterEqual(wl_count, 2)

            cur.execute("SELECT count(*) FROM trade_lessons;")
            lessons_count = cur.fetchone()[0]
            self.assertGreaterEqual(lessons_count, 20)

            cur.execute("SELECT count(*) FROM decision_log;")
            dec_count = cur.fetchone()[0]
            self.assertGreaterEqual(dec_count, 1)

    def test_cutover_in_isolated_temporary_workspace(self):
        """Tests that perform_sqlite_cutover runs cleanly and atomicity holds in an isolated workspace."""
        with tempfile.TemporaryDirectory() as td:
            temp_root = Path(td)
            temp_context = temp_root / "context"
            temp_context.mkdir(parents=True)

            # Copy current protected sources into temporary context
            (temp_context / "ssot.json").write_bytes(self.ssot_path.read_bytes())
            (temp_context / "trade_lessons.json").write_bytes(self.lessons_path.read_bytes())
            (temp_context / "decision_log.json").write_bytes(self.decisions_path.read_bytes())

            before_hash = hashlib.sha256((temp_context / "ssot.json").read_bytes()).hexdigest()

            cutover_res = perform_sqlite_cutover(repo_root=temp_root)
            self.assertEqual(cutover_res["status"], "success")
            self.assertTrue(cutover_res["protected_hashes_verified"])
            self.assertEqual(cutover_res["parity_summary"]["financial_mismatches"], 0)
            self.assertEqual(cutover_res["parity_summary"]["missing_records"], 0)

            # Assert target exists
            self.assertTrue((temp_context / "gem.db").exists())

            # Assert zero staging files remain
            staging_files = list(temp_context.glob("staging_gem_*"))
            self.assertEqual(staging_files, [])

            # Assert hash invariance
            after_hash = hashlib.sha256((temp_context / "ssot.json").read_bytes()).hexdigest()
            self.assertEqual(before_hash, after_hash)


class TestSqlitePersistenceAdapterCanonicalProjection(unittest.TestCase):
    """Verifies that SqlitePersistenceAdapter generates canonical SSoT projections."""

    def setUp(self):
        reset_persistence_service()
        self.service = get_persistence_service()

    def tearDown(self):
        reset_persistence_service()

    def test_active_backend_is_sqlite(self):
        """Active runtime backend must be sqlite."""
        self.assertEqual(self.service.backend_name, "sqlite")
        self.assertIsInstance(self.service.adapter, SqlitePersistenceAdapter)

    def test_canonical_projection_structure_and_metadata(self):
        """get_ssot_data() must return the canonical projection with _projection_metadata and mutable_state."""
        ssot = self.service.get_ssot_data()
        self.assertIsInstance(ssot, dict)

        # Check metadata
        meta = ssot.get("_projection_metadata")
        self.assertIsInstance(meta, dict)
        self.assertEqual(meta.get("authority"), "PROJECTED_READ_ONLY")
        self.assertEqual(meta.get("status"), "HEALTHY")
        self.assertEqual(meta.get("projection_format_version"), "1.0")
        self.assertIsInstance(meta.get("db_revision"), int)
        self.assertGreaterEqual(meta.get("db_revision"), 1)

        # Check domain keys
        self.assertIn("portfolio_snapshot", ssot)
        self.assertIn("unallocated_cash_eur", ssot)
        self.assertIn("unallocated_cash_usd", ssot)
        self.assertIn("portfolio_cash", ssot)
        self.assertIn("watched_tickers", ssot)
        self.assertIn("watchlist", ssot)
        self.assertIn("scout_categories", ssot)
        self.assertIn("trade_lessons", ssot)
        self.assertIn("decision_log", ssot)
        self.assertIn("mutable_state", ssot)

        # Check mutable_state alignment
        ms = ssot["mutable_state"]
        self.assertEqual(ms["portfolio_snapshot"], ssot["portfolio_snapshot"])
        self.assertEqual(ms["unallocated_cash_eur"], ssot["unallocated_cash_eur"])
        self.assertEqual(ms["watched_tickers"], ssot["watched_tickers"])

    def test_non_tabular_preservation(self):
        """Non-tabular / externally managed sections from base document are preserved."""
        with tempfile.TemporaryDirectory() as td:
            temp_db = os.path.join(td, "test_ext.db")
            mgr = DatabaseManager(temp_db, profile=PRIMARY_FINANCIAL_PROFILE)
            with mgr.connection() as conn:
                SchemaMigrator.initialize_core_schema(conn)

            base_file = os.path.join(td, "base_ssot.json")
            base_content = {
                "arbitration_weights": {"bullish": 0.35, "red_team": 0.45, "neutral": 0.20},
                "risk_parameters": {"max_drawdown_pct": 5.0},
                "state_context": {"market_regime": "NORMAL"},
                "mutable_state": {
                    "preserved_custom_flag": True,
                },
            }
            with open(base_file, "w", encoding="utf-8") as f:
                json.dump(base_content, f)

            adapter = SqlitePersistenceAdapter(mgr, base_ssot_path=base_file)
            proj = adapter.get_ssot_data()

            self.assertEqual(proj["arbitration_weights"], base_content["arbitration_weights"])
            self.assertEqual(proj["risk_parameters"], base_content["risk_parameters"])
            self.assertEqual(proj["state_context"], base_content["state_context"])
            self.assertEqual(proj["mutable_state"]["preserved_custom_flag"], True)

    def test_mutation_advances_db_revision_monotonically(self):
        """Mutations via UnitOfWork advance db_revision monotonically in projections."""
        with tempfile.TemporaryDirectory() as td:
            temp_db = os.path.join(td, "test_rev.db")
            mgr = DatabaseManager(temp_db, profile=PRIMARY_FINANCIAL_PROFILE)
            with mgr.connection() as conn:
                SchemaMigrator.initialize_core_schema(conn)

            adapter = SqlitePersistenceAdapter(mgr)
            proj1 = adapter.get_ssot_data()
            rev1 = proj1["_projection_metadata"]["db_revision"]

            with execution_role_context("Terminal Orchestrator"):
                adapter.save_portfolio_basket(
                    portfolio=[{"ticker": "AAPL", "shares": 10.0, "wac": 150.0}],
                    unallocated_cash_eur=1000.0,
                )

            proj2 = adapter.get_ssot_data()
            rev2 = proj2["_projection_metadata"]["db_revision"]
            self.assertGreater(rev2, rev1)

            positions = proj2["portfolio_snapshot"]
            self.assertEqual(len(positions), 1)
            self.assertEqual(positions[0]["ticker"], "AAPL")


class TestConsumerParity(unittest.TestCase):
    """Verifies that /api/data, /api/chat, and tools.get_market_data() exhibit consumer parity."""

    def setUp(self):
        reset_persistence_service()
        self.service = get_persistence_service()

    def tearDown(self):
        reset_persistence_service()

    def test_consumer_parity_holdings_cash_watchlist(self):
        """Authoritative holdings, cash, and watchlist are strictly identical across consumers."""
        # 1. Direct persistence service
        canonical_ssot = self.service.get_ssot_data()
        canonical_basket = self.service.get_portfolio_basket()
        canonical_watchlist = self.service.get_watchlist()

        # 2. /api/data via fetch_stocks.build_daemon_final_output
        mock_feed = [
            {"ticker": "SPY", "price": 500.0, "status": "ACTIVE", "price_validity": "VALID", "freshness": "CURRENT"},
        ]
        daemon_output = fetch_stocks.build_daemon_final_output(mock_feed, status="HEALTHY", service=self.service)
        self.assertTrue(daemon_output["financial_state_valid"])
        api_data_ssot = daemon_output["local_storage_state"]

        # Assert /api/data reflects canonical SSoT projection
        self.assertEqual(api_data_ssot["portfolio_snapshot"], canonical_ssot["portfolio_snapshot"])
        self.assertEqual(api_data_ssot["unallocated_cash_eur"], canonical_ssot["unallocated_cash_eur"])
        self.assertEqual(api_data_ssot["watched_tickers"], canonical_ssot["watched_tickers"])
        self.assertEqual(
            api_data_ssot["_projection_metadata"]["db_revision"],
            canonical_ssot["_projection_metadata"]["db_revision"],
        )

        # 3. tools.get_market_data()
        with patch_global_state(daemon_output):
            raw_tools_data = tools.get_market_data()
            tools_payload = json.loads(raw_tools_data)

            self.assertTrue(tools_payload.get("financial_state_valid"))
            tools_ssot = tools_payload.get("ssot", {})
            self.assertEqual(tools_ssot["portfolio_snapshot"], canonical_ssot["portfolio_snapshot"])
            self.assertEqual(tools_ssot["unallocated_cash_eur"], canonical_ssot["unallocated_cash_eur"])
            self.assertEqual(tools_ssot["watched_tickers"], canonical_ssot["watched_tickers"])

        # 4. /api/chat context validation
        with patch_global_state(daemon_output):
            chat_ssot = web_server.get_persistence_service().get_ssot_data()
            self.assertEqual(chat_ssot["portfolio_snapshot"], canonical_ssot["portfolio_snapshot"])
            self.assertEqual(chat_ssot["unallocated_cash_eur"], canonical_ssot["unallocated_cash_eur"])


class TestFailClosedDegradation(unittest.TestCase):
    """Verifies fail-closed behavior on database corruption/disconnection without silent JSON fallback."""

    def setUp(self):
        reset_persistence_service()

    def tearDown(self):
        reset_persistence_service()

    def test_missing_database_fails_closed_no_silent_json_fallback(self):
        """When gem.db does not exist, get_persistence_service() raises PersistenceError without falling back to JSON."""
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(PersistenceError) as ctx:
                get_persistence_service(repo_root=td)
            self.assertIn("Silent JSON fallback is prohibited", str(ctx.exception))

    def test_corrupt_database_fails_closed(self):
        """Corrupt database file raises PersistenceIntegrityError upon access."""
        with tempfile.TemporaryDirectory() as td:
            temp_context = Path(td) / "context"
            temp_context.mkdir(parents=True)
            corrupt_db = temp_context / "gem.db"
            corrupt_db.write_text("CORRUPTED_NON_SQLITE_HEADER", encoding="utf-8")

            with self.assertRaises(PersistenceIntegrityError) as ctx:
                get_persistence_service(repo_root=td)
            self.assertIn("Silent fallback to JSON is strictly prohibited", str(ctx.exception))

    def test_api_data_degradation_on_persistence_failure(self):
        """When persistence raises an error, /api/data sets financial_state_valid=False and local_storage_state=None."""
        mock_service = MagicMock()
        mock_service.get_trade_lessons.return_value = []
        mock_service.get_ssot_data.side_effect = PersistenceIntegrityError("Physical disk corruption")

        output = fetch_stocks.build_daemon_final_output([], status="HEALTHY", service=mock_service)
        self.assertFalse(output["financial_state_valid"])
        self.assertIsNone(output["local_storage_state"])
        self.assertIn("DEGRADED - PERSISTENCE FAILURE", output["status"])
        self.assertIn("Physical disk corruption", output["persistence_error"])

    def test_api_chat_halts_on_degraded_financial_state(self):
        """When financial state is degraded, /api/chat halts before model dispatch."""
        degraded_state = {
            "status": "DEGRADED - PERSISTENCE FAILURE: Disk error",
            "financial_state_valid": False,
            "persistence_error": "Disk error",
            "local_storage_state": None,
            "tickers": [],
        }

        with patch_global_state(degraded_state):
            req = web_server.ChatRequest(message="Execute portfolio rebalance")
            res = web_server.chat_endpoint(req)

            self.assertEqual(res["status"], "error")
            self.assertEqual(res["code"], "financial_state_degraded")
            self.assertIn("Authoritative financial state is currently unavailable", res["message"])

    def test_tools_get_market_data_surfaces_degradation(self):
        """When persistence degrades, tools.get_market_data() returns financial_state_valid=False."""
        degraded_state = {
            "status": "DEGRADED - PERSISTENCE FAILURE: Corrupt page",
            "financial_state_valid": False,
            "persistence_error": "Corrupt page",
            "local_storage_state": None,
            "tickers": [],
        }

        with patch_global_state(degraded_state):
            raw = tools.get_market_data()
            data = json.loads(raw)

            self.assertFalse(data.get("financial_state_valid"))
            self.assertIn("DEGRADED", data.get("status"))
            self.assertEqual(data.get("persistence_error"), "Corrupt page")
            self.assertNotIn("ssot", data)


class TestPreCutoverJsonConfiguration(unittest.TestCase):
    """Verifies that pre-cutover JSON backend can be explicitly configured for isolated testing."""

    def setUp(self):
        reset_persistence_service()

    def tearDown(self):
        reset_persistence_service()

    def test_explicit_json_configuration(self):
        """Explicitly calling configure_persistence(backend='json') activates JSON adapter."""
        service = configure_persistence(backend="json")
        self.assertEqual(service.backend_name, "json")
        self.assertIsInstance(service.adapter, JsonPersistenceAdapter)

        # Reset restores SQLite backend when context/gem.db is present
        reset_persistence_service()
        restored = get_persistence_service()
        self.assertEqual(restored.backend_name, "sqlite")
        self.assertIsInstance(restored.adapter, SqlitePersistenceAdapter)


if __name__ == "__main__":
    unittest.main()
