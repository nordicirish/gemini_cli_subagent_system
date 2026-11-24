"""
Route-table regression tests: every (method, path) is registered exactly once on the shared
FastAPI app (fetch_stocks.py + web_server.py), legacy duplicate routes stay removed, and the
canonical watchlist write fails with a real HTTP error status.
"""

import collections
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import web_server
from fastapi.testclient import TestClient

ORIGIN = {"Origin": "http://127.0.0.1:8000"}
REMOVED_LEGACY = [
    ("GET", "/api/get_basket"),
    ("POST", "/api/save_basket"),
    ("GET", "/api/get_watch_list"),
    ("POST", "/api/save_watch_list"),
]


def _registrations():
    seen = collections.defaultdict(list)
    for route in web_server.app.routes:
        endpoint = getattr(route, "endpoint", None)
        for method in getattr(route, "methods", None) or ():
            if method != "HEAD" and endpoint is not None:
                seen[(method, route.path)].append(f"{endpoint.__module__}.{endpoint.__name__}")
    return seen


class TestRouteTable(unittest.TestCase):
    def test_no_method_path_registered_twice(self):
        dups = {k: v for k, v in _registrations().items() if len(v) > 1}
        self.assertEqual(dups, {}, f"Duplicate route registrations: {dups}")

    def test_legacy_duplicate_routes_removed(self):
        regs = _registrations()
        for key in REMOVED_LEGACY:
            self.assertNotIn(key, regs, f"{key} should stay removed (use /api/basket or /api/watchlist)")

    def test_canonical_routes_present(self):
        regs = _registrations()
        for key in [("GET", "/api/basket"), ("POST", "/api/basket"), ("GET", "/api/watchlist"),
                    ("POST", "/api/watchlist"), ("GET", "/api/tickers"), ("GET", "/api/get_settings")]:
            self.assertIn(key, regs)
        self.assertEqual(regs[("GET", "/api/tickers")], ["fetch_stocks.get_tickers"])


class TestWatchlistWriteErrors(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(web_server.app)

    def test_persistence_failure_returns_503_not_200(self):
        service = MagicMock()
        service.save_watchlist.side_effect = RuntimeError("disk full api_key=AIzaSyD-secret12345")
        with (
            patch("web_server.get_persistence_service", return_value=service),
            patch("web_server.check_invalid_tickers", return_value=[]),
        ):
            res = self.client.post("/api/watchlist", json=["NVDA"], headers=ORIGIN)
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json(), {"status": "error", "detail": "Failed to persist watchlist"})

    def test_invalid_ticker_returns_400(self):
        with patch("web_server.check_invalid_tickers", return_value=["ZZZZQ"]):
            res = self.client.post("/api/watchlist", json=["ZZZZQ"], headers=ORIGIN)
        self.assertEqual(res.status_code, 400)
        self.assertIn("ZZZZQ", res.json()["detail"])


if __name__ == "__main__":
    unittest.main()
