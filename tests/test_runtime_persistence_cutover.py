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
import threading
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from python.atomic_io import FileLock
from python.database.manager import DatabaseManager, PRIMARY_FINANCIAL_PROFILE
from python.database.schema import SchemaMigrator
from python.database.projections import SSoTProjectionGenerator
from python.database.migration.models import ParityResult, ParitySummary
from python.database.migration.parity import SSoTParityVerifier
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

try:
    import tool_dispatcher
except ImportError:
    import python.tool_dispatcher as tool_dispatcher


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
        """Verify context/gem.db exists, has valid schema, and passes integrity checks without mutating it."""
        self.assertTrue(self.db_path.exists(), "context/gem.db must exist after Stage 3.2 cutover")
        p = self.db_path.resolve().as_posix()
        with sqlite3.connect(f"file:{p}?mode=ro", uri=True) as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA integrity_check;")
            self.assertEqual(cur.fetchall(), [("ok",)])

            # Check records imported
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

    def _prepare_clean_temp_workspace(self, td: str):
        temp_root = Path(td)
        temp_context = temp_root / "context"
        temp_context.mkdir(parents=True)
        (temp_context / "ssot.json").write_bytes(self.ssot_path.read_bytes())
        (temp_context / "decision_log.json").write_bytes(self.decisions_path.read_bytes())

        # For temporary workspace testing of cutover operations that should reach promotion:
        # Codify the 2 legacy lessons that lack 'L-xxx' prefix (ids 16 and 17)
        lessons_data = json.loads(self.lessons_path.read_text(encoding="utf-8"))
        for item in lessons_data.get("trade_lessons", []):
            if item.get("id") == 16 and not str(item.get("rule", "")).startswith("L-"):
                item["rule"] = "L-116: " + item["rule"]
            elif item.get("id") == 17 and not str(item.get("rule", "")).startswith("L-"):
                item["rule"] = "L-117: " + item["rule"]
        (temp_context / "trade_lessons.json").write_text(json.dumps(lessons_data, indent=2), encoding="utf-8")
        return temp_root, temp_context

    def test_cutover_refuses_when_destination_already_exists(self):
        """perform_sqlite_cutover refuses execution when target context/gem.db already exists."""
        with tempfile.TemporaryDirectory() as td:
            temp_root, temp_context = self._prepare_clean_temp_workspace(td)
            target_db = temp_context / "gem.db"
            target_db.write_text("PRE_EXISTING_DB", encoding="utf-8")

            with self.assertRaises(PersistenceIntegrityError) as ctx:
                perform_sqlite_cutover(repo_root=temp_root)
            self.assertIn("destination already exists", str(ctx.exception))

    def test_destination_content_and_sha256_invariant_after_rejection(self):
        """Pre-existing destination content and SHA-256 hash remain invariant after cutover rejection."""
        with tempfile.TemporaryDirectory() as td:
            temp_root, temp_context = self._prepare_clean_temp_workspace(td)
            target_db = temp_context / "gem.db"
            target_content = b"EXISTING_DATABASE_STATE_12345"
            target_db.write_bytes(target_content)
            expected_hash = hashlib.sha256(target_content).hexdigest()

            with self.assertRaises(PersistenceIntegrityError):
                perform_sqlite_cutover(repo_root=temp_root)

            self.assertEqual(target_db.read_bytes(), target_content)
            self.assertEqual(hashlib.sha256(target_db.read_bytes()).hexdigest(), expected_hash)

    def test_concurrent_cutover_attempts_blocked_by_exclusive_guard(self):
        """Concurrent cutover attempt fails with PersistenceIntegrityError when exclusive lock held."""
        with tempfile.TemporaryDirectory() as td:
            temp_root, temp_context = self._prepare_clean_temp_workspace(td)
            lock_path = str(temp_context / "gem.db.cutover_lock")
            held_event = threading.Event()
            stop_event = threading.Event()

            def hold_cutover_lock():
                with FileLock(lock_path, timeout=1.0):
                    held_event.set()
                    stop_event.wait(timeout=5.0)

            t = threading.Thread(target=hold_cutover_lock)
            t.start()
            try:
                held_event.wait(timeout=2.0)
                with self.assertRaises(PersistenceIntegrityError) as ctx:
                    perform_sqlite_cutover(repo_root=temp_root)
                self.assertIn("exclusive destination cutover lock could not be acquired", str(ctx.exception))
            finally:
                stop_event.set()
                t.join()

    def test_promotion_failure_cleans_staging_and_leaves_no_partial_destination(self):
        """Failure during atomic promotion cleans staging files and leaves no destination database."""
        with tempfile.TemporaryDirectory() as td:
            temp_root, temp_context = self._prepare_clean_temp_workspace(td)
            from python.persistence import service as svc_mod
            with patch.object(svc_mod, "_atomic_no_overwrite_promote", side_effect=OSError("Simulated promotion failure")):
                with self.assertRaises(OSError):
                    perform_sqlite_cutover(repo_root=temp_root)

            self.assertFalse((temp_context / "gem.db").exists())
            staging_files = list(temp_context.glob("staging_gem_*"))
            self.assertEqual(staging_files, [])

    def test_post_promotion_verification_failure_cleans_target_database(self):
        """Failed post-promotion verification cleans target database preventing partial production state."""
        with tempfile.TemporaryDirectory() as td:
            temp_root, temp_context = self._prepare_clean_temp_workspace(td)
            with patch.object(DatabaseManager, "verify_integrity", return_value=["database disk image is malformed"]):
                with self.assertRaises(PersistenceIntegrityError) as ctx:
                    perform_sqlite_cutover(repo_root=temp_root)
                self.assertIn("integrity check failed after promotion", str(ctx.exception))

            # Database must NOT be left behind as an apparently authoritative database
            self.assertFalse((temp_context / "gem.db").exists())
            staging_files = list(temp_context.glob("staging_gem_*"))
            self.assertEqual(staging_files, [])

    def test_cutover_in_isolated_temporary_workspace(self):
        """Tests that perform_sqlite_cutover runs cleanly and atomicity holds in an isolated workspace."""
        with tempfile.TemporaryDirectory() as td:
            temp_root, temp_context = self._prepare_clean_temp_workspace(td)
            before_hash = hashlib.sha256((temp_context / "ssot.json").read_bytes()).hexdigest()

            cutover_res = perform_sqlite_cutover(repo_root=temp_root)
            self.assertEqual(cutover_res["status"], "success")
            self.assertTrue(cutover_res["protected_hashes_verified"])
            self.assertEqual(cutover_res["parity_summary"]["financial_mismatches"], 0)
            self.assertEqual(cutover_res["parity_summary"]["missing_records"], 0)
            self.assertEqual(cutover_res["parity_summary"]["unresolved"], 0)

            # Assert target exists
            self.assertTrue((temp_context / "gem.db").exists())

            # Assert zero staging files remain
            staging_files = list(temp_context.glob("staging_gem_*"))
            self.assertEqual(staging_files, [])

            # Assert hash invariance
            after_hash = hashlib.sha256((temp_context / "ssot.json").read_bytes()).hexdigest()
            self.assertEqual(before_hash, after_hash)


class TestMigrationParityAndDiscrepancyGates(unittest.TestCase):
    """Verifies that perform_sqlite_cutover enforces complete migration parity and classifies discrepancies."""

    def setUp(self):
        reset_persistence_service()
        self.repo_root = Path(__file__).resolve().parent.parent
        self.ssot_path = self.repo_root / "context" / "ssot.json"
        self.lessons_path = self.repo_root / "context" / "trade_lessons.json"
        self.decisions_path = self.repo_root / "context" / "decision_log.json"

    def tearDown(self):
        reset_persistence_service()

    def _prepare_clean_temp_workspace(self, td: str):
        temp_root = Path(td)
        temp_context = temp_root / "context"
        temp_context.mkdir(parents=True)
        (temp_context / "ssot.json").write_bytes(self.ssot_path.read_bytes())
        (temp_context / "decision_log.json").write_bytes(self.decisions_path.read_bytes())

        lessons_data = json.loads(self.lessons_path.read_text(encoding="utf-8"))
        for item in lessons_data.get("trade_lessons", []):
            if item.get("id") == 16 and not str(item.get("rule", "")).startswith("L-"):
                item["rule"] = "L-116: " + item["rule"]
            elif item.get("id") == 17 and not str(item.get("rule", "")).startswith("L-"):
                item["rule"] = "L-117: " + item["rule"]
        (temp_context / "trade_lessons.json").write_text(json.dumps(lessons_data, indent=2), encoding="utf-8")
        return temp_root, temp_context

    def test_cutover_blocked_on_unexpected_records(self):
        """Parity summary with unexpected_records > 0 blocks cutover with PersistenceIntegrityError."""
        with tempfile.TemporaryDirectory() as td:
            temp_root, temp_context = self._prepare_clean_temp_workspace(td)
            mock_summary = ParitySummary(unexpected_records=1)
            mock_res = ParityResult(is_parity=False, summary=mock_summary)

            with patch.object(SSoTParityVerifier, "compare", return_value=mock_res):
                with self.assertRaises(PersistenceIntegrityError) as ctx:
                    perform_sqlite_cutover(repo_root=temp_root)
                self.assertIn("unexpected_records=1", str(ctx.exception))
            self.assertFalse((temp_context / "gem.db").exists())

    def test_cutover_blocked_on_timestamp_mismatches(self):
        """Parity summary with timestamp_mismatches > 0 blocks cutover with PersistenceIntegrityError."""
        with tempfile.TemporaryDirectory() as td:
            temp_root, temp_context = self._prepare_clean_temp_workspace(td)
            mock_summary = ParitySummary(timestamp_mismatches=1)
            mock_res = ParityResult(is_parity=False, summary=mock_summary)

            with patch.object(SSoTParityVerifier, "compare", return_value=mock_res):
                with self.assertRaises(PersistenceIntegrityError) as ctx:
                    perform_sqlite_cutover(repo_root=temp_root)
                self.assertIn("timestamp_mismatches=1", str(ctx.exception))
            self.assertFalse((temp_context / "gem.db").exists())

    def test_cutover_blocked_on_identity_mismatches(self):
        """Parity summary with identity_mismatches > 0 blocks cutover with PersistenceIntegrityError."""
        with tempfile.TemporaryDirectory() as td:
            temp_root, temp_context = self._prepare_clean_temp_workspace(td)
            mock_summary = ParitySummary(identity_mismatches=1)
            mock_res = ParityResult(is_parity=False, summary=mock_summary)

            with patch.object(SSoTParityVerifier, "compare", return_value=mock_res):
                with self.assertRaises(PersistenceIntegrityError) as ctx:
                    perform_sqlite_cutover(repo_root=temp_root)
                self.assertIn("identity_mismatches=1", str(ctx.exception))
            self.assertFalse((temp_context / "gem.db").exists())

    def test_cutover_blocked_on_unresolved_discrepancies(self):
        """Parity summary with unresolved > 0 blocks cutover with PersistenceIntegrityError."""
        with tempfile.TemporaryDirectory() as td:
            temp_root, temp_context = self._prepare_clean_temp_workspace(td)
            mock_summary = ParitySummary(unresolved=1)
            mock_res = ParityResult(is_parity=False, summary=mock_summary)

            with patch.object(SSoTParityVerifier, "compare", return_value=mock_res):
                with self.assertRaises(PersistenceIntegrityError) as ctx:
                    perform_sqlite_cutover(repo_root=temp_root)
                self.assertIn("unresolved=1", str(ctx.exception))
            self.assertFalse((temp_context / "gem.db").exists())

    def test_cutover_blocked_on_skipped_uncodified_trade_lessons(self):
        """Uncodified trade lessons without 'L-xxx' prefix block cutover with PersistenceIntegrityError."""
        with tempfile.TemporaryDirectory() as td:
            temp_root, temp_context = self._prepare_clean_temp_workspace(td)
            lessons = json.loads((temp_context / "trade_lessons.json").read_text(encoding="utf-8"))
            lessons.setdefault("trade_lessons", []).append({"id": 9999, "rule": "Mandate without code prefix"})
            (temp_context / "trade_lessons.json").write_text(json.dumps(lessons, indent=2), encoding="utf-8")

            with self.assertRaises(PersistenceIntegrityError) as ctx:
                perform_sqlite_cutover(repo_root=temp_root)
            self.assertIn("uncodified trade lesson without 'L-xxx' prefix", str(ctx.exception))
            self.assertFalse((temp_context / "gem.db").exists())

    def test_cutover_blocked_on_skipped_unsupported_decision_log_entries(self):
        """Unsupported decision log entries block cutover with PersistenceIntegrityError."""
        with tempfile.TemporaryDirectory() as td:
            temp_root, temp_context = self._prepare_clean_temp_workspace(td)
            decisions = json.loads((temp_context / "decision_log.json").read_text(encoding="utf-8"))
            decisions.setdefault("decision_log", []).append({"arbitrary_unsupported_structure": "non_tabular_entry"})
            (temp_context / "decision_log.json").write_text(json.dumps(decisions, indent=2), encoding="utf-8")

            with self.assertRaises(PersistenceIntegrityError) as ctx:
                perform_sqlite_cutover(repo_root=temp_root)
            self.assertIn("unsupported decision-log entry detected", str(ctx.exception))
            self.assertFalse((temp_context / "gem.db").exists())

    def test_cutover_reports_discrepancy_classifications_and_counts(self):
        """Cutover returns structured discrepancy classifications and counts without exposing financial contents."""
        with tempfile.TemporaryDirectory() as td:
            temp_root, temp_context = self._prepare_clean_temp_workspace(td)
            # Add an exact duplicate record to exercise non-lossy deduplication classification
            lessons = json.loads((temp_context / "trade_lessons.json").read_text(encoding="utf-8"))
            dup_lesson = dict(lessons["trade_lessons"][0])
            lessons["trade_lessons"].append(dup_lesson)
            (temp_context / "trade_lessons.json").write_text(json.dumps(lessons, indent=2), encoding="utf-8")

            res = perform_sqlite_cutover(repo_root=temp_root)
            self.assertEqual(res["status"], "success")
            self.assertIn("discrepancy_classifications", res)
            self.assertIn("discrepancies_total", res)
            self.assertIsInstance(res["discrepancy_classifications"], dict)
            self.assertEqual(res["discrepancy_classifications"].get("EXACT_DUPLICATE_DEDUPLICATED"), 1)


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

    def test_consumer_parity_across_actual_endpoints(self):
        """Authoritative holdings, cash, and watchlist are strictly identical across /api/data, /api/chat, and tools."""
        # 1. Direct persistence service
        canonical_ssot = self.service.get_ssot_data()

        # Build daemon output
        mock_feed = [
            {"ticker": "SPY", "price": 500.0, "status": "ACTIVE", "price_validity": "VALID", "freshness": "CURRENT"},
        ]
        daemon_output = fetch_stocks.build_daemon_final_output(mock_feed, status="HEALTHY", service=self.service)
        self.assertTrue(daemon_output["financial_state_valid"])

        with patch_global_state(daemon_output):
            # 2. Exercise actual /api/data endpoint: fetch_stocks.get_data()
            api_data_response = fetch_stocks.get_data()
            self.assertEqual(api_data_response.status_code, 200)
            api_data_payload = json.loads(api_data_response.body)
            self.assertTrue(api_data_payload.get("financial_state_valid"))
            api_ssot = api_data_payload.get("local_storage_state", {})

            # Verify holdings, cash, watchlist, scout categories and projection revision
            self.assertEqual(api_ssot["portfolio_snapshot"], canonical_ssot["portfolio_snapshot"])
            self.assertEqual(api_ssot["unallocated_cash_eur"], canonical_ssot["unallocated_cash_eur"])
            self.assertEqual(api_ssot["unallocated_cash_usd"], canonical_ssot["unallocated_cash_usd"])
            self.assertEqual(api_ssot["watched_tickers"], canonical_ssot["watched_tickers"])
            self.assertEqual(api_ssot["scout_categories"], canonical_ssot["scout_categories"])
            self.assertEqual(
                api_ssot["_projection_metadata"]["db_revision"],
                canonical_ssot["_projection_metadata"]["db_revision"],
            )

            # 3. Exercise actual tools.get_market_data()
            raw_tools_data = tools.get_market_data()
            tools_payload = json.loads(raw_tools_data)
            self.assertTrue(tools_payload.get("financial_state_valid"))
            tools_ssot = tools_payload.get("ssot", {})

            # Verify agreement with /api/data and canonical SSoT
            self.assertEqual(tools_ssot["portfolio_snapshot"], canonical_ssot["portfolio_snapshot"])
            self.assertEqual(tools_ssot["unallocated_cash_eur"], canonical_ssot["unallocated_cash_eur"])
            self.assertEqual(tools_ssot["watched_tickers"], canonical_ssot["watched_tickers"])
            self.assertEqual(tools_ssot["scout_categories"], canonical_ssot["scout_categories"])

            # 4. Exercise actual /api/chat endpoint: web_server.chat_endpoint()
            with patch.object(tool_dispatcher.GLOBAL_TOOL_DISPATCHER, "execute_multi_turn_session") as mock_dispatch:
                mock_dispatch.return_value = {"status": "success", "response": "Market analysis complete."}
                chat_req = web_server.ChatRequest(message="Confirm current portfolio cash balance and holdings.")
                chat_res = web_server.chat_endpoint(chat_req)

                self.assertEqual(chat_res.get("status"), "success")
                mock_dispatch.assert_called_once()
                dispatched_message = mock_dispatch.call_args.kwargs.get("initial_message")
                text_content = dispatched_message[-1] if isinstance(dispatched_message, list) else dispatched_message
                self.assertIsInstance(text_content, str)

                # Verify actual endpoint hydrates and validates the same canonical authoritative financial state
                self.assertIn("portfolio_snapshot", text_content)
                self.assertIn("unallocated_cash_eur", text_content)
                self.assertIn(str(canonical_ssot["unallocated_cash_eur"]), text_content)

    def test_actual_api_chat_endpoint_halts_on_degraded_persistence(self):
        """When persistence is degraded, /api/chat halts before any model or tool dispatch."""
        degraded_state = {
            "status": "DEGRADED - PERSISTENCE FAILURE: Corrupt SQLite state",
            "financial_state_valid": False,
            "persistence_error": "Corrupt SQLite state",
            "local_storage_state": None,
            "tickers": [],
        }

        with patch_global_state(degraded_state):
            with patch.object(tool_dispatcher.GLOBAL_TOOL_DISPATCHER, "execute_multi_turn_session") as mock_dispatch:
                chat_req = web_server.ChatRequest(message="Execute position rebalance")
                res = web_server.chat_endpoint(chat_req)

                self.assertEqual(res["status"], "error")
                self.assertEqual(res["code"], "financial_state_degraded")
                self.assertIn("Authoritative financial state is currently unavailable", res["message"])
                mock_dispatch.assert_not_called()

    def test_tools_get_market_data_surfaces_degradation_without_substitution(self):
        """tools.get_market_data surfaces degraded state without substituting stale or empty portfolio."""
        degraded_state = {
            "status": "DEGRADED - PERSISTENCE FAILURE: Disk I/O error",
            "financial_state_valid": False,
            "persistence_error": "Disk I/O error",
            "local_storage_state": None,
            "tickers": [],
        }

        with patch_global_state(degraded_state):
            raw = tools.get_market_data()
            data = json.loads(raw)

            self.assertFalse(data.get("financial_state_valid"))
            self.assertIn("DEGRADED", data.get("status"))
            self.assertEqual(data.get("persistence_error"), "Disk I/O error")
            self.assertNotIn("ssot", data)


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
