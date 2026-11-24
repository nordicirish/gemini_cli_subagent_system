"""
Unit & Integration Tests for Watchlist Read-Only GET Endpoints & Authorized Writes
Governed by Stage 1.1.1 (Workstream F2), Protocol 03 (Security), and Protocol 06 (Financial Boundaries).
"""

import os
import sys
import json
import unittest
import tempfile
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import fetch_stocks
import web_server
from tool_registry import execution_role_context
from database.manager import DatabaseManager, PRIMARY_FINANCIAL_PROFILE
from database.schema import SchemaMigrator
from persistence.service import (
    configure_persistence,
    get_persistence_service,
    reset_persistence_service,
)


class MockRequest:
    """Mock FastAPI Request object for testing POST endpoints."""
    def __init__(self, json_data):
        self._json_data = json_data

    async def json(self):
        return self._json_data


class TestWatchlistEndpoints(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_config_path = os.path.join(self.temp_dir.name, "config.json")
        self.temp_ssot_path = os.path.join(self.temp_dir.name, "ssot.json")

        self.initial_config = {"WATCHLIST": ["AAPL", "MSFT"]}
        self.initial_ssot = {
            "mutable_state": {
                "portfolio_snapshot": [{"ticker": "SPY", "shares": 10, "wac": 500.0}],
                "watched_tickers": ["OLD_SYM1", "OLD_SYM2"],
            }
        }

        with open(self.temp_config_path, "w", encoding="utf-8") as f:
            json.dump(self.initial_config, f, indent=2)

        with open(self.temp_ssot_path, "w", encoding="utf-8") as f:
            json.dump(self.initial_ssot, f, indent=2)

        # Configure isolated SQLite persistence backend
        self.temp_db_path = os.path.join(self.temp_dir.name, "test_gem.db")
        self.db_manager = DatabaseManager(self.temp_db_path, profile=PRIMARY_FINANCIAL_PROFILE)
        with self.db_manager.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
        configure_persistence(backend="sqlite", db_manager=self.db_manager)
        self.service = get_persistence_service()
        with execution_role_context("Terminal Orchestrator"):
            self.service.save_watchlist(["AAPL", "MSFT"], caller_role="Terminal Orchestrator")

        # Intercept path lookups for context/config.json and context/ssot.json
        import builtins
        import atomic_io

        self._orig_exists = os.path.exists
        self._orig_open = builtins.open
        self._orig_update = atomic_io.atomic_update_json
        self._orig_read = atomic_io.atomic_read_json

        def mocked_exists(path):
            norm = str(path).replace("\\", "/")
            if norm == "context/config.json":
                return os.path.exists(self.temp_config_path)
            if norm == "context/ssot.json":
                return os.path.exists(self.temp_ssot_path)
            return self._orig_exists(path)

        def mocked_open(path, *args, **kwargs):
            norm = str(path).replace("\\", "/")
            if norm == "context/config.json":
                return self._orig_open(self.temp_config_path, *args, **kwargs)
            if norm == "context/ssot.json":
                return self._orig_open(self.temp_ssot_path, *args, **kwargs)
            return self._orig_open(path, *args, **kwargs)

        def mocked_update(path, *args, **kwargs):
            norm = str(path).replace("\\", "/")
            if norm == "context/config.json":
                return self._orig_update(self.temp_config_path, *args, **kwargs)
            if norm == "context/ssot.json":
                return self._orig_update(self.temp_ssot_path, *args, **kwargs)
            return self._orig_update(path, *args, **kwargs)

        def mocked_read(path, *args, **kwargs):
            norm = str(path).replace("\\", "/")
            if norm == "context/config.json":
                return self._orig_read(self.temp_config_path, *args, **kwargs)
            if norm == "context/ssot.json":
                return self._orig_read(self.temp_ssot_path, *args, **kwargs)
            return self._orig_read(path, *args, **kwargs)

        self.patch_exists = patch("os.path.exists", side_effect=mocked_exists)
        self.patch_open = patch("builtins.open", side_effect=mocked_open)
        self.patch_update_io = patch("atomic_io.atomic_update_json", side_effect=mocked_update)
        self.patch_update_fs = patch("fetch_stocks.atomic_update_json", side_effect=mocked_update)
        self.patch_read_io = patch("atomic_io.atomic_read_json", side_effect=mocked_read)

        self.patch_exists.start()
        self.patch_open.start()
        self.patch_update_io.start()
        self.patch_update_fs.start()
        self.patch_read_io.start()

    def tearDown(self):
        self.patch_read_io.stop()
        self.patch_update_fs.stop()
        self.patch_update_io.stop()
        self.patch_open.stop()
        self.patch_exists.stop()
        reset_persistence_service()
        self.db_manager = None
        self.service = None
        import gc
        gc.collect()
        try:
            self.temp_dir.cleanup()
        except PermissionError:
            pass

    def _get_file_bytes(self, path):
        with self._orig_open(path, "rb") as f:
            return f.read()

    def test_f2_web_server_get_watchlist_is_strictly_read_only(self):
        """F2.2: GET /api/watchlist in web_server.py must not modify ssot.json or config.json."""
        ssot_before = self._get_file_bytes(self.temp_ssot_path)
        config_before = self._get_file_bytes(self.temp_config_path)

        watchlist = web_server.get_watch_list()

        # Verify response content (FastAPI list serialization)
        self.assertEqual(watchlist, ["AAPL", "MSFT"])

        # Verify persistence service state is unchanged
        self.assertEqual(self.service.get_watchlist(), ["AAPL", "MSFT"])

        # Verify zero disk mutations on authoritative financial files
        self.assertEqual(self._get_file_bytes(self.temp_ssot_path), ssot_before)
        self.assertEqual(self._get_file_bytes(self.temp_config_path), config_before)

    def test_f2_watchlist_fallback_to_ssot_when_config_empty(self):
        """F2: If config.json has no watchlist, fall back to ssot.json watched_tickers without mutating either file."""
        configure_persistence(backend="json")
        try:
            with self._orig_open(self.temp_config_path, "w", encoding="utf-8") as f:
                json.dump({"WATCHLIST": []}, f)

            ssot_before = self._get_file_bytes(self.temp_ssot_path)
            config_before = self._get_file_bytes(self.temp_config_path)

            # Test web_server GET
            ws_resp = web_server.get_watch_list()
            self.assertEqual(ws_resp, ["OLD_SYM1", "OLD_SYM2"])

            # Neither file was altered
            self.assertEqual(self._get_file_bytes(self.temp_ssot_path), ssot_before)
            self.assertEqual(self._get_file_bytes(self.temp_config_path), config_before)
        finally:
            configure_persistence(backend="sqlite", db_manager=self.db_manager)
            self.service = get_persistence_service()

    def test_f2_symbol_filtering_and_normalization(self):
        """F2: Watchlist GET endpoints normalize symbols (uppercase, stripped) and ignore invalid items."""
        import asyncio
        req = MockRequest({"watchlist": ["  tsla ", "NvDa", "", 123, None, "  amd  "]})
        with (
            execution_role_context("Terminal Orchestrator"),
            patch("web_server.check_invalid_tickers", return_value=[]),  # no network in tests
        ):
            res = asyncio.run(web_server.save_watch_list(req))
        self.assertEqual(res.get("status"), "success")

        # web_server GET
        ws_resp = web_server.get_watch_list()
        self.assertEqual(ws_resp, ["AMD", "NVDA", "TSLA"])

        # Persistence service state
        self.assertEqual(self.service.get_watchlist(), ["AMD", "NVDA", "TSLA"])

    def test_f2_authorized_watchlist_writes_succeed(self):
        """F2.3: Authorized POST requests to save_watch_list successfully update the active persistence backend."""
        import asyncio

        # web_server.save_watch_list (POST /api/watchlist) is the only watchlist write route
        req2 = MockRequest({"watchlist": ["META", "NFLX"]})
        with execution_role_context("Context Engine"):
            res2 = asyncio.run(web_server.save_watch_list(req2))
        self.assertEqual(res2.get("status"), "success")

        self.assertEqual(self.service.get_watchlist(), ["META", "NFLX"])

    def test_f2_unauthorized_watchlist_writes_blocked(self):
        """F2.4: Unauthorized roles attempting POST mutations to watchlist endpoints must be blocked fail-closed."""
        import asyncio

        watchlist_before = self.service.get_watchlist()
        ssot_before = self._get_file_bytes(self.temp_ssot_path)
        config_before = self._get_file_bytes(self.temp_config_path)

        unauthorized_roles = ["Python Engineer", "Architecture Agent", "Bullish Advocate"]
        for role in unauthorized_roles:
            with execution_role_context(role):
                # web_server POST
                req2 = MockRequest({"watchlist": ["HACK2"]})
                with self.assertRaises(PermissionError, msg=f"web_server should block {role}"):
                    asyncio.run(web_server.save_watch_list(req2))

        # Persistence state and files must remain unchanged
        self.assertEqual(self.service.get_watchlist(), watchlist_before)
        self.assertEqual(self._get_file_bytes(self.temp_ssot_path), ssot_before)
        self.assertEqual(self._get_file_bytes(self.temp_config_path), config_before)


if __name__ == "__main__":
    unittest.main()
