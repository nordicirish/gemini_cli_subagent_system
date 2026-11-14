"""
Automated Test Suite for GEM Web Chat Endpoint and Daemon Persistence Integration.
Governed by Stage 2 Architecture, Agent Governance Framework, and Follow-up Remediation.

Covers:
1. Web Chat Primary Model Dispatch, Session Preservation, and Fallback Invariant (Scope A).
2. Web Chat Error Propagation and Zero Dynamic Model Substitution (Scope A).
3. Daemon Authoritative State Boundary and Missing SSoT First-Run Behaviour (Scope B).
4. Daemon Degradation and Output Suppression on Corrupt/Unreadable SSoT (Scope B).
5. Scout Classification Suppression under Authoritative Persistence Failure (Scope B).
6. Optional Non-Authoritative Lesson Retrieval Failure Handling (Scope B).
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import web_server
import fetch_stocks
from persistence import (
    get_persistence_service,
    reset_persistence_service,
    PersistenceIntegrityError,
    PersistenceValidationError,
    PersistenceError,
)


class TestWebChatEndpointModelAndSessionIntegrity(unittest.TestCase):
    def setUp(self):
        self._orig_model = web_server.ORCHESTRATOR_MODEL
        self._orig_session = web_server.global_chat_session
        web_server.cancel_event.clear()

    def tearDown(self):
        web_server.ORCHESTRATOR_MODEL = self._orig_model
        web_server.global_chat_session = self._orig_session
        web_server.cancel_event.clear()

    def test_create_new_session_initializes_with_orchestrator_model(self):
        """create_new_session initializes session using authoritative ORCHESTRATOR_MODEL without dynamic overrides."""
        with patch.object(web_server.framework, "create_chat_session") as mock_create:
            mock_create.return_value = MagicMock()
            sess = web_server.create_new_session()
            mock_create.assert_called_once_with(
                web_server.framework._get_sys_instruction(web_server.terminal_instruction),
                web_server.terminal_tools,
                web_server.ORCHESTRATOR_MODEL,
            )

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_endpoint_primary_success_reports_fallback_false(self, mock_dispatch, mock_charts):
        """When primary dispatch succeeds, response contains fallback_occurred: False and model is unchanged."""
        web_server.ORCHESTRATOR_MODEL = "gemini-3.8-flash-high"
        mock_dispatch.return_value = {
            "status": "completed",
            "response": "Market regime is currently NEUTRAL with low dealer gamma.",
            "turn_count": 1,
        }

        req = web_server.ChatRequest(message="What is the current regime?")
        res = web_server.chat_endpoint(req)

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["response"], "Market regime is currently NEUTRAL with low dealer gamma.")
        self.assertFalse(res["fallback_occurred"])
        self.assertEqual(res["model"], "gemini-3.8-flash-high")
        self.assertEqual(res["active_model"], "gemini-3.8-flash-high")
        self.assertEqual(mock_dispatch.call_count, 1)

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_endpoint_dispatch_failure_preserves_model_and_session(self, mock_dispatch, mock_charts):
        """When dispatch fails, error is returned, model is NOT dynamically mutated, and session is NOT replaced."""
        initial_model = "gemini-3.8-flash-high"
        web_server.ORCHESTRATOR_MODEL = initial_model
        initial_session = web_server.global_chat_session

        mock_dispatch.side_effect = RuntimeError("Service connection timeout (503)")

        req = web_server.ChatRequest(message="What is the current regime?")
        res = web_server.chat_endpoint(req)

        # 1. Error path executed cleanly
        self.assertEqual(res["status"], "error")
        self.assertIn("503", res.get("message", ""))

        # 2. No second invocation / retry policy attempted
        self.assertEqual(mock_dispatch.call_count, 1)

        # 3. Model preserved invariant
        self.assertEqual(web_server.ORCHESTRATOR_MODEL, initial_model)

        # 4. Session preserved invariant
        self.assertIs(web_server.global_chat_session, initial_session)

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_endpoint_halts_dispatch_when_financial_state_is_degraded(self, mock_dispatch, mock_charts):
        """When daemon financial state is degraded, chat endpoint aborts dispatch with controlled error."""
        initial_model = "gemini-3.8-flash-high"
        web_server.ORCHESTRATOR_MODEL = initial_model
        initial_session = web_server.global_chat_session

        degraded_state = {
            "financial_state_valid": False,
            "local_storage_state": None,
            "persistence_error": "Authoritative SSoT retrieval failure: Malformed JSON",
            "tickers": [],
        }

        with patch.dict(fetch_stocks.GLOBAL_STATE, degraded_state, clear=True):
            req = web_server.ChatRequest(message="Execute rebalance")
            res = web_server.chat_endpoint(req)

            # 1. Controlled error response returned without leaking raw internal exception
            self.assertEqual(res["status"], "error")
            self.assertEqual(res["code"], "financial_state_degraded")
            self.assertIn("Authoritative financial state is currently unavailable or corrupted", res["message"])
            self.assertNotIn("Traceback", res["message"])

            # 2. Dispatch is halted — zero tool or model execution
            self.assertEqual(mock_dispatch.call_count, 0)

            # 3. Model invariant preserved
            self.assertEqual(web_server.ORCHESTRATOR_MODEL, initial_model)

            # 4. Session invariant preserved
            self.assertIs(web_server.global_chat_session, initial_session)

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_endpoint_halts_dispatch_when_local_storage_state_is_none(self, mock_dispatch, mock_charts):
        """When local_storage_state is explicitly None, chat endpoint halts dispatch."""
        with patch.dict(fetch_stocks.GLOBAL_STATE, {"local_storage_state": None}, clear=True):
            req = web_server.ChatRequest(message="What is the current regime?")
            res = web_server.chat_endpoint(req)

            self.assertEqual(res["status"], "error")
            self.assertEqual(res["code"], "financial_state_degraded")
            self.assertEqual(mock_dispatch.call_count, 0)

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_endpoint_allows_dispatch_on_valid_empty_state(self, mock_dispatch, mock_charts):
        """When financial state is valid and empty, chat endpoint proceeds normally to dispatch."""
        mock_dispatch.return_value = {
            "status": "completed",
            "response": "Portfolio is currently empty. Ready for cash allocation.",
            "turn_count": 1,
        }

        valid_empty_state = {
            "financial_state_valid": True,
            "local_storage_state": {},
            "tickers": [],
        }

        with patch.dict(fetch_stocks.GLOBAL_STATE, valid_empty_state, clear=True):
            req = web_server.ChatRequest(message="What is the portfolio status?")
            res = web_server.chat_endpoint(req)

            self.assertEqual(res["status"], "success")
            self.assertEqual(mock_dispatch.call_count, 1)

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_endpoint_allows_dispatch_on_valid_populated_ssot(self, mock_dispatch, mock_charts):
        """Valid populated SSoT permits normal dispatch with active financial context."""
        mock_dispatch.return_value = {
            "status": "completed",
            "response": "Holding AAPL 10 shares at $150.0 WAC.",
            "turn_count": 1,
        }

        valid_populated_state = {
            "financial_state_valid": True,
            "local_storage_state": {
                "mutable_state": {
                    "portfolio_snapshot": [{"ticker": "AAPL", "shares": 10.0, "wac": 150.0, "status": "ACTIVE"}],
                    "state_context": {"regime": "BULLISH"},
                }
            },
            "tickers": [],
        }

        with patch.dict(fetch_stocks.GLOBAL_STATE, valid_populated_state, clear=True):
            req = web_server.ChatRequest(message="What is our AAPL position?")
            res = web_server.chat_endpoint(req)

            self.assertEqual(res["status"], "success")
            self.assertEqual(mock_dispatch.call_count, 1)

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_endpoint_halts_when_mutable_state_is_none(self, mock_dispatch, mock_charts):
        """When authoritative SSoT contains mutable_state=None, chat dispatch halts fail-closed."""
        initial_model = web_server.ORCHESTRATOR_MODEL
        initial_session = web_server.global_chat_session

        malformed_state = {
            "financial_state_valid": True,
            "local_storage_state": {"mutable_state": None},
            "tickers": [],
        }

        with patch.dict(fetch_stocks.GLOBAL_STATE, malformed_state, clear=True):
            req = web_server.ChatRequest(message="Execute order")
            res = web_server.chat_endpoint(req)

            self.assertEqual(res["status"], "error")
            self.assertEqual(res["code"], "financial_state_degraded")
            self.assertEqual(mock_dispatch.call_count, 0)
            self.assertEqual(web_server.ORCHESTRATOR_MODEL, initial_model)
            self.assertIs(web_server.global_chat_session, initial_session)

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_endpoint_halts_when_mutable_state_is_invalid_type(self, mock_dispatch, mock_charts):
        """When mutable_state is a non-dict type, chat dispatch halts fail-closed."""
        malformed_state = {
            "financial_state_valid": True,
            "local_storage_state": {"mutable_state": "corrupted_non_dict_string"},
            "tickers": [],
        }

        with patch.dict(fetch_stocks.GLOBAL_STATE, malformed_state, clear=True):
            req = web_server.ChatRequest(message="Execute order")
            res = web_server.chat_endpoint(req)

            self.assertEqual(res["status"], "error")
            self.assertEqual(res["code"], "financial_state_degraded")
            self.assertEqual(mock_dispatch.call_count, 0)

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_endpoint_halts_when_portfolio_structure_is_invalid(self, mock_dispatch, mock_charts):
        """When portfolio_snapshot is not a list, chat dispatch halts fail-closed."""
        malformed_state = {
            "financial_state_valid": True,
            "local_storage_state": {
                "mutable_state": {
                    "portfolio_snapshot": "invalid_not_a_list"
                }
            },
            "tickers": [],
        }

        with patch.dict(fetch_stocks.GLOBAL_STATE, malformed_state, clear=True):
            req = web_server.ChatRequest(message="Portfolio overview")
            res = web_server.chat_endpoint(req)

            self.assertEqual(res["status"], "error")
            self.assertEqual(res["code"], "financial_state_degraded")
            self.assertEqual(mock_dispatch.call_count, 0)

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_endpoint_halts_when_portfolio_entry_is_invalid(self, mock_dispatch, mock_charts):
        """When an individual portfolio entry is not a dict, chat dispatch halts fail-closed."""
        malformed_state = {
            "financial_state_valid": True,
            "local_storage_state": {
                "mutable_state": {
                    "portfolio_snapshot": [123, "invalid_entry_type"]
                }
            },
            "tickers": [],
        }

        with patch.dict(fetch_stocks.GLOBAL_STATE, malformed_state, clear=True):
            req = web_server.ChatRequest(message="Portfolio overview")
            res = web_server.chat_endpoint(req)

            self.assertEqual(res["status"], "error")
            self.assertEqual(res["code"], "financial_state_degraded")
            self.assertEqual(mock_dispatch.call_count, 0)

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_endpoint_halts_on_exception_during_authoritative_ssot_processing(self, mock_dispatch, mock_charts):
        """An unexpected exception during authoritative SSoT processing aborts dispatch without broad catch suppression."""
        initial_model = web_server.ORCHESTRATOR_MODEL
        initial_session = web_server.global_chat_session

        mock_bad_ssot = MagicMock()
        mock_bad_ssot.get.side_effect = RuntimeError("Memory corruption during authoritative SSoT retrieval")

        malformed_state = {
            "financial_state_valid": True,
            "local_storage_state": mock_bad_ssot,
            "tickers": [],
        }

        with patch.dict(fetch_stocks.GLOBAL_STATE, malformed_state, clear=True):
            req = web_server.ChatRequest(message="Status")
            res = web_server.chat_endpoint(req)

            self.assertEqual(res["status"], "error")
            self.assertEqual(res["code"], "financial_state_degraded")
            self.assertEqual(mock_dispatch.call_count, 0)
            self.assertEqual(web_server.ORCHESTRATOR_MODEL, initial_model)
            self.assertIs(web_server.global_chat_session, initial_session)

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_endpoint_optional_enrichment_failure_remains_non_blocking(self, mock_dispatch, mock_charts):
        """When authoritative SSoT is valid, non-authoritative enrichment failure remains non-blocking."""
        mock_dispatch.return_value = {
            "status": "completed",
            "response": "Dispatched despite optional telemetry enrichment failure.",
            "turn_count": 1,
        }

        valid_state = {
            "financial_state_valid": True,
            "local_storage_state": {
                "mutable_state": {
                    "portfolio_snapshot": [{"ticker": "SPY", "shares": 10.0, "wac": 500.0}],
                }
            },
            "tickers": [],
        }

        with patch.dict(fetch_stocks.GLOBAL_STATE, valid_state, clear=True):
            # Simulate failure during prompt template formatting of optional telemetry
            with patch("web_server.load_data_packet_injection_prompt", side_effect=RuntimeError("Template load failure")):
                req = web_server.ChatRequest(message="What is the current position?")
                res = web_server.chat_endpoint(req)

                self.assertEqual(res["status"], "success")
                self.assertEqual(mock_dispatch.call_count, 1)

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_endpoint_startup_unreadable_ssot_halts_dispatch(self, mock_dispatch, mock_charts):
        """On startup before daemon runs, unreadable/corrupt SSoT halts chat dispatch."""
        mock_service = MagicMock()
        mock_service.get_portfolio_basket.side_effect = PersistenceIntegrityError("Corrupt SSoT JSON")
        mock_service.get_ssot_data.side_effect = PersistenceIntegrityError("Corrupt SSoT JSON")

        uninitialized_state = {
            "status": "INITIALIZING - LOADING GEX PROFILES...",
            "tickers": [],
        }

        with patch.dict(fetch_stocks.GLOBAL_STATE, uninitialized_state, clear=True):
            with patch("web_server.get_persistence_service", return_value=mock_service):
                req = web_server.ChatRequest(message="Hello")
                res = web_server.chat_endpoint(req)

                self.assertEqual(res["status"], "error")
                self.assertEqual(res["code"], "financial_state_degraded")
                self.assertEqual(mock_dispatch.call_count, 0)

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_endpoint_startup_clean_empty_ssot_allows_dispatch(self, mock_dispatch, mock_charts):
        """On startup before daemon runs, clean first-run empty SSoT allows dispatch."""
        mock_dispatch.return_value = {
            "status": "completed",
            "response": "First run initialized cleanly.",
            "turn_count": 1,
        }

        mock_service = MagicMock()
        mock_service.get_portfolio_basket.return_value = {
            "portfolio": [],
            "unallocated_cash_eur": 0.0,
            "unallocated_cash_usd": 0.0,
            "eurusd_rate": 1.08,
        }
        mock_service.get_ssot_data.return_value = {}

        uninitialized_state = {
            "status": "INITIALIZING - LOADING GEX PROFILES...",
            "tickers": [],
        }

        with patch.dict(fetch_stocks.GLOBAL_STATE, uninitialized_state, clear=True):
            with patch("web_server.get_persistence_service", return_value=mock_service):
                req = web_server.ChatRequest(message="Hello")
                res = web_server.chat_endpoint(req)

                self.assertEqual(res["status"], "success")
                self.assertEqual(mock_dispatch.call_count, 1)

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_endpoint_startup_invalid_portfolio_financial_values_halts_dispatch(self, mock_dispatch, mock_charts):
        """Startup SSoT containing non-finite or invalid portfolio financial values halts dispatch."""
        mock_service = MagicMock()
        mock_service.get_portfolio_basket.side_effect = PersistenceValidationError(
            "Non-finite numeric value not allowed for shares for position AAPL: nan"
        )

        uninitialized_state = {
            "status": "INITIALIZING - LOADING GEX PROFILES...",
            "tickers": [],
        }

        with patch.dict(fetch_stocks.GLOBAL_STATE, uninitialized_state, clear=True):
            with patch("web_server.get_persistence_service", return_value=mock_service):
                req = web_server.ChatRequest(message="Execute buy")
                res = web_server.chat_endpoint(req)

                self.assertEqual(res["status"], "error")
                self.assertEqual(res["code"], "financial_state_degraded")
                self.assertEqual(mock_dispatch.call_count, 0)

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_endpoint_startup_invalid_cash_or_rate_halts_dispatch(self, mock_dispatch, mock_charts):
        """Startup SSoT containing invalid cash or exchange rate values halts dispatch."""
        uninitialized_state = {
            "status": "INITIALIZING - LOADING GEX PROFILES...",
            "tickers": [],
        }

        # 1. Invalid Cash EUR
        mock_service_cash = MagicMock()
        mock_service_cash.get_portfolio_basket.side_effect = PersistenceValidationError(
            "Non-finite numeric value not allowed for unallocated_cash_eur: inf"
        )
        with patch.dict(fetch_stocks.GLOBAL_STATE, uninitialized_state, clear=True):
            with patch("web_server.get_persistence_service", return_value=mock_service_cash):
                res = web_server.chat_endpoint(web_server.ChatRequest(message="Status"))
                self.assertEqual(res["status"], "error")
                self.assertEqual(res["code"], "financial_state_degraded")
                self.assertEqual(mock_dispatch.call_count, 0)

        # 2. Invalid Exchange Rate
        mock_service_rate = MagicMock()
        mock_service_rate.get_portfolio_basket.side_effect = PersistenceValidationError(
            "Zero value not allowed for exchange_rate"
        )
        with patch.dict(fetch_stocks.GLOBAL_STATE, uninitialized_state, clear=True):
            with patch("web_server.get_persistence_service", return_value=mock_service_rate):
                res = web_server.chat_endpoint(web_server.ChatRequest(message="Status"))
                self.assertEqual(res["status"], "error")
                self.assertEqual(res["code"], "financial_state_degraded")
                self.assertEqual(mock_dispatch.call_count, 0)

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_endpoint_deferred_session_creation_remains_none_on_degraded_state(self, mock_dispatch, mock_charts):
        """When global_chat_session is initially None, degraded financial state leaves it None."""
        web_server.global_chat_session = None

        degraded_state = {
            "financial_state_valid": False,
            "local_storage_state": None,
            "persistence_error": "Authoritative SSoT retrieval failure",
            "tickers": [],
        }

        with patch.dict(fetch_stocks.GLOBAL_STATE, degraded_state, clear=True):
            req = web_server.ChatRequest(message="Rebalance")
            res = web_server.chat_endpoint(req)

            self.assertEqual(res["status"], "error")
            self.assertEqual(res["code"], "financial_state_degraded")
            # Session must remain uninstantiated (None)
            self.assertIsNone(web_server.global_chat_session)
            self.assertEqual(mock_dispatch.call_count, 0)

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_endpoint_existing_session_preserved_on_degraded_state(self, mock_dispatch, mock_charts):
        """Existing global_chat_session instance is preserved unchanged on validation failure."""
        dummy_session = MagicMock(name="PreExistingSession")
        web_server.global_chat_session = dummy_session

        degraded_state = {
            "financial_state_valid": False,
            "local_storage_state": None,
            "persistence_error": "Authoritative SSoT retrieval failure",
            "tickers": [],
        }

        with patch.dict(fetch_stocks.GLOBAL_STATE, degraded_state, clear=True):
            req = web_server.ChatRequest(message="Rebalance")
            res = web_server.chat_endpoint(req)

            self.assertEqual(res["status"], "error")
            self.assertEqual(res["code"], "financial_state_degraded")
            # Existing session preserved invariant
            self.assertIs(web_server.global_chat_session, dummy_session)
            self.assertEqual(mock_dispatch.call_count, 0)


class TestDaemonExecutionPathPersistenceIntegrity(unittest.TestCase):
    def test_daemon_missing_ssot_first_run_behaviour(self):
        """A genuinely missing SSoT returns clean first-run empty state with financial_state_valid=True."""
        mock_service = MagicMock()
        mock_service.get_portfolio_basket.return_value = {
            "portfolio": [],
            "unallocated_cash_eur": 0.0,
            "unallocated_cash_usd": 0.0,
            "eurusd_rate": 1.08,
        }
        mock_service.get_watchlist.return_value = []
        mock_service.get_ssot_data.return_value = {}
        mock_service.get_trade_lessons.return_value = []

        held_and_watched, err = fetch_stocks.resolve_held_and_watched(service=mock_service)
        self.assertIsNone(err)
        self.assertEqual(held_and_watched, set())

        out = fetch_stocks.build_daemon_final_output(
            data=[{"ticker": "AAPL", "price": 220.0, "_isScout": False}],
            status="OPEN",
            authoritative_state_error=err,
            service=mock_service,
        )

        self.assertTrue(out["financial_state_valid"])
        self.assertEqual(out["status"], "OPEN")
        self.assertEqual(out["_meta"]["reliability"], "High")
        self.assertEqual(out["local_storage_state"], {})
        self.assertEqual(len(out["tickers"]), 1)

    def test_daemon_valid_empty_portfolio_handled_correctly(self):
        """A valid empty portfolio produces normal publication without degraded flags."""
        mock_service = MagicMock()
        mock_service.get_portfolio_basket.return_value = {
            "portfolio": [],
            "unallocated_cash_eur": 5000.0,
            "unallocated_cash_usd": 5400.0,
            "eurusd_rate": 1.08,
        }
        mock_service.get_watchlist.return_value = ["MSFT"]
        mock_service.get_ssot_data.return_value = {
            "portfolio_snapshot": [],
            "unallocated_cash_eur": 5000.0,
            "watched_tickers": ["MSFT"],
        }
        mock_service.get_trade_lessons.return_value = []

        held_and_watched, err = fetch_stocks.resolve_held_and_watched(service=mock_service)
        self.assertIsNone(err)
        self.assertEqual(held_and_watched, {"MSFT"})

        out = fetch_stocks.build_daemon_final_output(
            data=[{"ticker": "MSFT", "price": 400.0, "_isScout": False}],
            status="OPEN",
            authoritative_state_error=err,
            service=mock_service,
        )

        self.assertTrue(out["financial_state_valid"])
        self.assertEqual(out["local_storage_state"]["unallocated_cash_eur"], 5000.0)

    def test_daemon_malformed_ssot_fails_closed_and_marks_degraded(self):
        """Malformed or unreadable SSoT prevents normal financial publication and surfaces observable degraded state."""
        mock_service = MagicMock()
        mock_service.get_portfolio_basket.side_effect = PersistenceIntegrityError("SSoT file contains malformed JSON")
        mock_service.get_watchlist.side_effect = PersistenceIntegrityError("SSoT file contains malformed JSON")
        mock_service.get_ssot_data.side_effect = PersistenceIntegrityError("SSoT file contains malformed JSON")
        mock_service.get_trade_lessons.return_value = []

        held_and_watched, err = fetch_stocks.resolve_held_and_watched(service=mock_service)
        self.assertIsNotNone(err)
        self.assertIn("malformed JSON", err)

        out = fetch_stocks.build_daemon_final_output(
            data=[{"ticker": "AAPL", "price": 220.0, "_isScout": False}],
            status="OPEN",
            authoritative_state_error=err,
            service=mock_service,
        )

        # 1. Financial state explicitly marked invalid
        self.assertFalse(out["financial_state_valid"])

        # 2. Status surfaces explicit degraded failure
        self.assertIn("DEGRADED - PERSISTENCE FAILURE", out["status"])

        # 3. Reliability marked DEGRADED
        self.assertEqual(out["_meta"]["reliability"], "DEGRADED")

        # 4. Persistence error preserves underlying exception info
        self.assertIn("malformed JSON", out["persistence_error"])

        # 5. local_storage_state is None (NOT empty dict {}!)
        self.assertIsNone(out["local_storage_state"])

    def test_daemon_portfolio_failure_prevents_scout_classification(self):
        """Authoritative portfolio retrieval failure halts scout classification and discards scout tickers."""
        # When authoritative error is present:
        err = "Authoritative portfolio/watchlist retrieval failure: Disk I/O error"

        # Provide a candidate item marked as a scout
        candidate_data = [
            {"ticker": "SPY", "price": 500.0, "_isScout": False},
            {"ticker": "XYZ", "price": 25.0, "_isScout": True, "score": 85.0, "rsi": 40.0},
        ]

        mock_service = MagicMock()
        mock_service.get_ssot_data.side_effect = PersistenceError("Disk I/O error")
        mock_service.get_trade_lessons.return_value = []

        out = fetch_stocks.build_daemon_final_output(
            data=candidate_data,
            status="OPEN",
            authoritative_state_error=err,
            service=mock_service,
        )

        # Scout tickers must be completely excluded from final publication
        self.assertEqual(len(out["tickers"]), 1)
        self.assertEqual(out["tickers"][0]["ticker"], "SPY")
        self.assertEqual(out["scout_categories_loaded"], [])
        self.assertFalse(out["financial_state_valid"])

    def test_daemon_optional_trade_lessons_failure_does_not_mask_authoritative_state(self):
        """Failure of optional trade lessons logs a warning without marking authoritative financial state degraded."""
        mock_service = MagicMock()
        mock_service.get_portfolio_basket.return_value = {
            "portfolio": [{"ticker": "SPY", "shares": 10.0, "wac": 500.0}],
            "unallocated_cash_eur": 2000.0,
            "unallocated_cash_usd": 2160.0,
            "eurusd_rate": 1.08,
        }
        mock_service.get_watchlist.return_value = ["AAPL"]
        mock_service.get_ssot_data.return_value = {
            "portfolio_snapshot": [{"ticker": "SPY", "shares": 10.0, "wac": 500.0}],
            "unallocated_cash_eur": 2000.0,
        }
        # Optional data source fails
        mock_service.get_trade_lessons.side_effect = PersistenceError("trade_lessons.json read error")

        held_and_watched, err = fetch_stocks.resolve_held_and_watched(service=mock_service)
        self.assertIsNone(err)

        out = fetch_stocks.build_daemon_final_output(
            data=[{"ticker": "SPY", "price": 505.0, "_isScout": False}],
            status="OPEN",
            authoritative_state_error=err,
            service=mock_service,
        )

        # Financial state remains valid
        self.assertTrue(out["financial_state_valid"])
        self.assertEqual(out["status"], "OPEN")
        self.assertEqual(out["_meta"]["reliability"], "High")
        # Lessons fallback to empty list
        self.assertEqual(out["trade_lessons"], [])
        self.assertIsNotNone(out["local_storage_state"])


if __name__ == "__main__":
    unittest.main()
