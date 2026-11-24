"""
Live EUR/USD rate (replaces the hardcoded 1.08): quote validation, freshness, fetching, and
how persistence / proposals derive USD cash, failing closed when no fresh rate exists.
"""

import datetime
import json
import os
import sys
import tempfile
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # sibling test helpers

import fx_rates
from fx_rates import FxRateError

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 11, 24, 15, 0, tzinfo=UTC)


class FxStateMixin:
    """Isolates the process-wide quote state for each test."""

    def setUp(self):
        super().setUp()
        self._saved = (fx_rates._latest, fx_rates._last_attempt)
        fx_rates._latest = None
        fx_rates._last_attempt = 0.0
        clock = patch("fx_rates._utc_now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def tearDown(self):
        fx_rates._latest, fx_rates._last_attempt = self._saved
        super().tearDown()

    def record(self, rate="1.0973", age=datetime.timedelta(minutes=1)):
        return fx_rates.record_eurusd(Decimal(rate), NOW - age, "test")


def _resp(status, body):
    return SimpleNamespace(status_code=status, text=json.dumps(body))


CHART_OK = {"chart": {"result": [{"meta": {"regularMarketPrice": 1.0973334, "regularMarketTime": 1795536105}}]}}
QUOTE_OK = {"quoteResponse": {"result": [{"regularMarketPrice": 1.1012, "regularMarketTime": 1795536105}]}}


class TestQuote(FxStateMixin, unittest.TestCase):
    def test_no_quote_means_unavailable(self):
        self.assertIsNone(fx_rates.get_eurusd())
        self.assertEqual(fx_rates.fx_public(None)["available"], False)

    def test_rate_quantized_to_six_places(self):
        q = self.record("1.09733345")
        self.assertEqual(q.rate, Decimal("1.097333"))
        self.assertEqual(fx_rates.fx_public(q)["rate"], "1.097333")

    def test_rejects_float_and_insane_rates(self):
        for bad in (1.09, Decimal("0"), Decimal("-1.1"), Decimal("5"), Decimal("NaN"), True, None):
            with self.assertRaises(FxRateError, msg=repr(bad)):
                fx_rates.record_eurusd(bad, NOW, "test")

    def test_rejects_naive_timestamp(self):
        with self.assertRaises(FxRateError):
            fx_rates.record_eurusd(Decimal("1.1"), datetime.datetime(2026, 11, 24), "test")

    def test_weekend_quote_still_fresh_but_old_quote_expires(self):
        self.record(age=datetime.timedelta(days=3))
        self.assertIsNotNone(fx_rates.get_eurusd())
        fx_rates._latest = None
        self.record(age=datetime.timedelta(days=5))
        self.assertIsNone(fx_rates.get_eurusd())

    def test_older_quote_does_not_replace_newer(self):
        self.record("1.10", age=datetime.timedelta(minutes=1))
        self.record("1.05", age=datetime.timedelta(hours=2))
        self.assertEqual(fx_rates.get_eurusd().rate, Decimal("1.100000"))

    def test_derive_usd_rounds_half_up_to_cents(self):
        q = self.record("1.0973")
        self.assertEqual(fx_rates.derive_usd_cash(Decimal("1000.05"), q), Decimal("1097.35"))


class TestFetch(FxStateMixin, unittest.TestCase):
    def test_fetch_parses_chart_source_exactly(self):
        with patch("fx_rates._http_get", return_value=_resp(200, CHART_OK)):
            q = fx_rates.fetch_eurusd_quote()
        self.assertEqual((q.rate, q.source), (Decimal("1.097333"), "yahoo_chart"))
        self.assertEqual(q.as_of_utc, "2026-11-24T16:01:45Z")  # epoch 1795536105

    def test_fetch_falls_back_to_quote_source(self):
        with patch("fx_rates._http_get", side_effect=[_resp(503, {}), _resp(200, QUOTE_OK)]):
            q = fx_rates.fetch_eurusd_quote()
        self.assertEqual((q.rate, q.source), (Decimal("1.101200"), "yahoo_quote"))

    def test_fetch_failure_raises_and_refresh_keeps_last_good(self):
        good = self.record("1.0973")
        logs = []
        with patch("fx_rates._http_get", side_effect=ConnectionError("offline")):
            with self.assertRaises(FxRateError):
                fx_rates.fetch_eurusd_quote()
            self.assertEqual(fx_rates.refresh_eurusd(force=True, log=logs.append), good)
        self.assertTrue(any("refresh failed" in m for m in logs))

    def test_refresh_is_rate_limited(self):
        with patch("fx_rates._http_get", return_value=_resp(200, CHART_OK)) as get:
            fx_rates.refresh_eurusd()
            fx_rates.refresh_eurusd()
        self.assertEqual(get.call_count, 1)


class TestSqliteUsdDerivation(FxStateMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        from python.database.manager import DatabaseManager, PRIMARY_FINANCIAL_PROFILE
        from python.database.schema import SchemaMigrator
        from persistence import SqlitePersistenceAdapter
        from tool_registry import execution_role_context

        self.temp_dir = tempfile.TemporaryDirectory()
        self.mgr = DatabaseManager(os.path.join(self.temp_dir.name, "fx.db"), profile=PRIMARY_FINANCIAL_PROFILE)
        with self.mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)
        self.adapter = SqlitePersistenceAdapter(self.mgr)
        self.role = execution_role_context
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(self.adapter.close)

    def save(self, eur, usd=None):
        with self.role("Terminal Orchestrator"):
            return self.adapter.save_portfolio_basket(portfolio=[], unallocated_cash_eur=Decimal(eur), unallocated_cash_usd=usd)

    def test_basket_reports_live_rate(self):
        self.record("1.0973")
        basket = self.adapter.get_portfolio_basket()
        self.assertEqual(basket["eurusd_rate"], Decimal("1.097300"))
        self.assertEqual(basket["eurusd_rate_as_of"], "2026-11-24T14:59:00Z")

    def test_usd_derived_at_live_rate(self):
        self.record("1.0973")
        res = self.save("1000.00")
        self.assertEqual(res["unallocated_cash_usd"], Decimal("1097.30"))
        self.assertEqual(self.adapter.get_portfolio_basket()["unallocated_cash_usd"], Decimal("1097.30"))

    def test_no_rate_leaves_stored_usd_unchanged(self):
        self.save("500.00", usd=Decimal("550.00"))
        res = self.save("1000.00")  # no fresh quote
        self.assertEqual(res["unallocated_cash_usd"], Decimal("550.00"))
        basket = self.adapter.get_portfolio_basket()
        self.assertEqual((basket["unallocated_cash_eur"], basket["unallocated_cash_usd"]), (Decimal("1000.00"), Decimal("550.00")))
        self.assertIsNone(basket["eurusd_rate"])

    def test_explicit_usd_is_kept(self):
        self.record("1.0973")
        self.assertEqual(self.save("1000.00", usd=Decimal("1234.56"))["unallocated_cash_usd"], Decimal("1234.56"))

    def test_zero_eur_means_zero_usd(self):
        self.save("500.00", usd=Decimal("550.00"))
        self.assertEqual(self.save("0.00")["unallocated_cash_usd"], Decimal("0.00"))

    def test_payload_eur_only_derives_or_keeps_usd(self):
        self.save("500.00", usd=Decimal("550.00"))
        with self.role("Terminal Orchestrator"):
            self.adapter.update_ssot_payload({"mutable_state": {"unallocated_cash_eur": Decimal("800.00")}})
        self.assertEqual(self.adapter.get_portfolio_basket()["unallocated_cash_usd"], Decimal("550.00"))
        self.record("1.10")
        with self.role("Terminal Orchestrator"):
            self.adapter.update_ssot_payload({"mutable_state": {"unallocated_cash_eur": Decimal("900.00")}})
        self.assertEqual(self.adapter.get_portfolio_basket()["unallocated_cash_usd"], Decimal("990.00"))


class TestJsonUsdDerivation(FxStateMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        from persistence import JsonPersistenceAdapter
        from tool_registry import execution_role_context

        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        os.makedirs(os.path.join(self.temp_dir.name, "context"))
        self.adapter = JsonPersistenceAdapter(base_dir=self.temp_dir.name)
        self.role = execution_role_context

    def test_parity_with_sqlite_rules(self):
        with self.role("Terminal Orchestrator"):
            self.adapter.save_portfolio_basket([], Decimal("500.00"), Decimal("550.00"))
            self.assertEqual(self.adapter.save_portfolio_basket([], Decimal("1000.00"))["unallocated_cash_usd"], Decimal("550.00"))
            self.record("1.0973")
            self.assertEqual(self.adapter.save_portfolio_basket([], Decimal("1000.00"))["unallocated_cash_usd"], Decimal("1097.30"))
        self.assertEqual(self.adapter.get_portfolio_basket()["eurusd_rate"], Decimal("1.097300"))


class TestProposalUsdRow(FxStateMixin, unittest.TestCase):
    def _store(self):
        from portfolio_proposals import ProposalStore
        from test_portfolio_proposals import FakeService
        self.service = FakeService(positions={}, eur="1000.00", usd="1080.00")
        return ProposalStore(service_factory=lambda: self.service)

    def test_eur_change_adds_derived_usd_row(self):
        self.record("1.0973")
        out = self._store().submit({"EXECUTION_PAYLOAD": {"unallocated_cash_eur": Decimal("2000.00")}})
        usd = [c for c in out["proposal"]["cash"] if c["currency"] == "USD"]
        self.assertEqual(len(usd), 1)
        self.assertEqual((usd[0]["before"], usd[0]["after"]), ("1080.00", "2194.60"))
        self.assertIn("live EUR/USD 1.097300", usd[0]["note"])

    def test_no_rate_no_usd_row(self):
        out = self._store().submit({"EXECUTION_PAYLOAD": {"unallocated_cash_eur": Decimal("2000.00")}})
        self.assertEqual([c["currency"] for c in out["proposal"]["cash"]], ["EUR"])


if __name__ == "__main__":
    unittest.main()
