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
        self.temp_dir.cleanup()

    def _get_file_bytes(self, path):
        with self._orig_open(path, "rb") as f:
            return f.read()

    def test_f2_fetch_stocks_get_watch_list_is_strictly_read_only(self):
        """F2.1: GET /api/get_watch_list in fetch_stocks.py must not modify ssot.json or config.json."""
        ssot_before = self._get_file_bytes(self.temp_ssot_path)
        config_before = self._get_file_bytes(self.temp_config_path)

        response = fetch_stocks.get_watch_list()
        self.assertEqual(response.status_code, 200)

        # Verify response structure and content
        body = json.loads(response.body.decode("utf-8"))
        self.assertEqual(body, ["AAPL", "MSFT"])

        # Verify zero disk mutations on authoritative financial files
        self.assertEqual(self._get_file_bytes(self.temp_ssot_path), ssot_before)
        self.assertEqual(self._get_file_bytes(self.temp_config_path), config_before)

    def test_f2_web_server_get_watchlist_is_strictly_read_only(self):
        """F2.2: GET /api/watchlist in web_server.py must not modify ssot.json or config.json."""
        ssot_before = self._get_file_bytes(self.temp_ssot_path)
        config_before = self._get_file_bytes(self.temp_config_path)

        watchlist = web_server.get_watch_list()

        # Verify response content (FastAPI list serialization)
        self.assertEqual(watchlist, ["AAPL", "MSFT"])

        # Verify zero disk mutations on authoritative financial files
        self.assertEqual(self._get_file_bytes(self.temp_ssot_path), ssot_before)
        self.assertEqual(self._get_file_bytes(self.temp_config_path), config_before)

    def test_f2_watchlist_fallback_to_ssot_when_config_empty(self):
        """F2: If config.json has no watchlist, fall back to ssot.json watched_tickers without mutating either file."""
        with self._orig_open(self.temp_config_path, "w", encoding="utf-8") as f:
            json.dump({"WATCHLIST": []}, f)

        ssot_before = self._get_file_bytes(self.temp_ssot_path)
        config_before = self._get_file_bytes(self.temp_config_path)

        # Test fetch_stocks GET
        fs_resp = fetch_stocks.get_watch_list()
        self.assertEqual(json.loads(fs_resp.body.decode("utf-8")), ["OLD_SYM1", "OLD_SYM2"])

        # Test web_server GET
        ws_resp = web_server.get_watch_list()
        self.assertEqual(ws_resp, ["OLD_SYM1", "OLD_SYM2"])

        # Neither file was altered
        self.assertEqual(self._get_file_bytes(self.temp_ssot_path), ssot_before)
        self.assertEqual(self._get_file_bytes(self.temp_config_path), config_before)

    def test_f2_symbol_filtering_and_normalization(self):
        """F2: Watchlist GET endpoints normalize symbols (uppercase, stripped) and ignore invalid items."""
        with self._orig_open(self.temp_config_path, "w", encoding="utf-8") as f:
            json.dump({"WATCHLIST": ["  tsla ", "NvDa", "", 123, None, "  amd  "]}, f)

        # fetch_stocks GET
        fs_resp = fetch_stocks.get_watch_list()
        self.assertEqual(json.loads(fs_resp.body.decode("utf-8")), ["TSLA", "NVDA", "AMD"])

        # web_server GET
        ws_resp = web_server.get_watch_list()
        self.assertEqual(ws_resp, ["TSLA", "NVDA", "AMD"])

    def test_f2_authorized_watchlist_writes_succeed(self):
        """F2.3: Authorized POST requests to save_watch_list successfully update both config.json and ssot.json."""
        import asyncio

        # 1. Test fetch_stocks.save_watch_list (POST /api/save_watch_list)
        req1 = MockRequest({"watchlist": ["GOOG", "AMZN"]})
        with execution_role_context("Terminal Orchestrator"):
            res1 = asyncio.run(fetch_stocks.save_watch_list(req1))
        self.assertEqual(res1.status_code, 200)

        with self._orig_open(self.temp_config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            self.assertEqual(cfg.get("WATCHLIST"), ["GOOG", "AMZN"])

        with self._orig_open(self.temp_ssot_path, "r", encoding="utf-8") as f:
            ssot = json.load(f)
            self.assertEqual(ssot["mutable_state"].get("watched_tickers"), ["GOOG", "AMZN"])

        # 2. Test web_server.save_watch_list (POST /api/watchlist)
        req2 = MockRequest({"watchlist": ["META", "NFLX"]})
        with execution_role_context("Context Engine"):
            res2 = asyncio.run(web_server.save_watch_list(req2))
        self.assertEqual(res2.get("status"), "success")

        with self._orig_open(self.temp_config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            self.assertEqual(cfg.get("WATCHLIST"), ["META", "NFLX"])

        with self._orig_open(self.temp_ssot_path, "r", encoding="utf-8") as f:
            ssot = json.load(f)
            self.assertEqual(ssot["mutable_state"].get("watched_tickers"), ["META", "NFLX"])

    def test_f2_unauthorized_watchlist_writes_blocked(self):
        """F2.4: Unauthorized roles attempting POST mutations to watchlist endpoints must be blocked fail-closed."""
        import asyncio

        ssot_before = self._get_file_bytes(self.temp_ssot_path)
        config_before = self._get_file_bytes(self.temp_config_path)

        unauthorized_roles = ["Python Engineer", "Architecture Agent", "Bullish Advocate"]
        for role in unauthorized_roles:
            with execution_role_context(role):
                # fetch_stocks POST
                req1 = MockRequest({"watchlist": ["HACK1"]})
                with self.assertRaises(PermissionError, msg=f"fetch_stocks should block {role}"):
                    asyncio.run(fetch_stocks.save_watch_list(req1))

                # web_server POST
                req2 = MockRequest({"watchlist": ["HACK2"]})
                with self.assertRaises(PermissionError, msg=f"web_server should block {role}"):
                    asyncio.run(web_server.save_watch_list(req2))

        # Files must remain unchanged
        self.assertEqual(self._get_file_bytes(self.temp_ssot_path), ssot_before)
        self.assertEqual(self._get_file_bytes(self.temp_config_path), config_before)


if __name__ == "__main__":
    unittest.main()
