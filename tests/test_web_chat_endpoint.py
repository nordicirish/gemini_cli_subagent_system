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

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import web_server
import fetch_stocks


@contextmanager
def patch_global_state(state):
    """Legitimate external test fixture for temporarily swapping the published snapshot."""
    orig = fetch_stocks.get_global_state_snapshot()
    fetch_stocks.publish_global_state_snapshot(state)
    try:
        yield
    finally:
        fetch_stocks.publish_global_state_snapshot(orig)
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

        with patch_global_state(degraded_state):
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
        with patch_global_state({"local_storage_state": None}):
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

        with patch_global_state(valid_empty_state):
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

        with patch_global_state(valid_populated_state):
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

        with patch_global_state(malformed_state):
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

        with patch_global_state(malformed_state):
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

        with patch_global_state(malformed_state):
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

        with patch_global_state(malformed_state):
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

        with patch_global_state(malformed_state):
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

        with patch_global_state(valid_state):
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

        with patch_global_state(uninitialized_state):
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

        with patch_global_state(uninitialized_state):
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

        with patch_global_state(uninitialized_state):
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
        with patch_global_state(uninitialized_state):
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
        with patch_global_state(uninitialized_state):
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

        with patch_global_state(degraded_state):
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

        with patch_global_state(degraded_state):
            req = web_server.ChatRequest(message="Rebalance")
            res = web_server.chat_endpoint(req)

            self.assertEqual(res["status"], "error")
            self.assertEqual(res["code"], "financial_state_degraded")
            # Existing session preserved invariant
            self.assertIs(web_server.global_chat_session, dummy_session)
            self.assertEqual(mock_dispatch.call_count, 0)

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_endpoint_cancel_event_preserved_when_financial_state_degraded(self, mock_dispatch, mock_charts):
        """When cancel_event is already set and financial state is degraded, event remains set after return."""
        web_server.cancel_event.set()

        degraded_state = {
            "financial_state_valid": False,
            "local_storage_state": None,
            "persistence_error": "Authoritative SSoT retrieval failure",
            "tickers": [],
        }

        with patch_global_state(degraded_state):
            req = web_server.ChatRequest(message="Halt check")
            res = web_server.chat_endpoint(req)

            self.assertEqual(res["status"], "error")
            self.assertEqual(res["code"], "financial_state_degraded")
            # Runtime cancel_event must not be cleared on rejected request
            self.assertTrue(web_server.cancel_event.is_set())
            self.assertEqual(mock_dispatch.call_count, 0)

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_endpoint_reset_turn_usage_not_called_when_financial_state_degraded(self, mock_dispatch, mock_charts):
        """When authoritative financial state is degraded, framework.reset_turn_usage() is not called."""
        with patch.object(web_server.framework, "reset_turn_usage") as mock_reset:
            degraded_state = {
                "financial_state_valid": False,
                "local_storage_state": None,
                "persistence_error": "Authoritative SSoT retrieval failure",
                "tickers": [],
            }

            with patch_global_state(degraded_state):
                req = web_server.ChatRequest(message="Usage check")
                res = web_server.chat_endpoint(req)

                self.assertEqual(res["status"], "error")
                self.assertEqual(res["code"], "financial_state_degraded")
                mock_reset.assert_not_called()
                self.assertEqual(mock_dispatch.call_count, 0)

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_endpoint_runtime_resets_execute_once_on_valid_state(self, mock_dispatch, mock_charts):
        """When authoritative financial state is valid, runtime reset operations execute exactly once before dispatch."""
        web_server.cancel_event.set()

        mock_dispatch.return_value = {
            "status": "completed",
            "response": "Dispatch completed successfully.",
            "turn_count": 1,
        }

        valid_state = {
            "financial_state_valid": True,
            "local_storage_state": {},
            "tickers": [],
        }

        with patch.object(web_server.framework, "reset_turn_usage") as mock_reset:
            with patch_global_state(valid_state):
                req = web_server.ChatRequest(message="Valid dispatch check")
                res = web_server.chat_endpoint(req)

                self.assertEqual(res["status"], "success")
                # cancel_event was cleared before dispatch
                self.assertFalse(web_server.cancel_event.is_set())
                # reset_turn_usage executed exactly once
                mock_reset.assert_called_once()
                self.assertEqual(mock_dispatch.call_count, 1)


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


class TestStage332ContextAndTokenEfficiency(unittest.TestCase):
    def setUp(self):
        self._orig_model = web_server.ORCHESTRATOR_MODEL
        self._orig_session = web_server.global_chat_session
        self._orig_hydrated = web_server._session_hydrated
        web_server.cancel_event.clear()

    def tearDown(self):
        web_server.ORCHESTRATOR_MODEL = self._orig_model
        web_server.global_chat_session = self._orig_session
        web_server._session_hydrated = self._orig_hydrated
        web_server.framework.reset_turn_usage()
        web_server.cancel_event.clear()

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_endpoint_serializes_compact_data_packet_with_exact_semantic_equality(self, mock_dispatch, mock_charts):
        """DATA_PACKET uses compact JSON serialization with exact semantic equality across all fields and SSoT."""
        mock_dispatch.return_value = {
            "status": "completed",
            "response": "Compact payload verified.",
            "turn_count": 1,
        }

        ssot_data = get_persistence_service().get_ssot_data()
        populated_state = {
            "_meta": {"reliability": "High", "cycle_id": "stage332-test"},
            "timestamp": "2026-11-15T12:30:00",
            "status": "OPEN",
            "financial_state_valid": True,
            "local_storage_state": ssot_data,
            "tickers": [
                {
                    "ticker": "SPY",
                    "price": 595.5,
                    "price_validity": "VALID",
                    "freshness": "CURRENT",
                    "price_source": "yfinance",
                    "rsi": 54.2,
                    "net_gex_total": 1.2345,
                }
            ],
        }

        with patch_global_state(populated_state):
            res = web_server.chat_endpoint(web_server.ChatRequest(message="Check compact DATA_PACKET"))

        self.assertEqual(res["status"], "success")
        dispatched_msg = mock_dispatch.call_args.kwargs["initial_message"]
        self.assertIsInstance(dispatched_msg, str)

        start_marker = "```json\n"
        end_marker = "\n```\n[END_DATA_PACKET]"
        start_idx = dispatched_msg.find(start_marker)
        end_idx = dispatched_msg.find(end_marker, start_idx)
        self.assertNotEqual(start_idx, -1)
        self.assertNotEqual(end_idx, -1)
        packet_str = dispatched_msg[start_idx + len(start_marker):end_idx]

        self.assertNotIn("\n", packet_str)
        self.assertNotIn("ImmutableDict(", packet_str)

        compact_parsed = json.loads(packet_str)
        pretty_equivalent = json.loads(json.dumps(compact_parsed, indent=2, default=str))
        self.assertEqual(compact_parsed, pretty_equivalent)
        self.assertEqual(packet_str, json.dumps(compact_parsed, separators=(",", ":"), default=str))

        self.assertEqual(compact_parsed["_meta"], {"reliability": "High", "cycle_id": "stage332-test"})
        self.assertEqual(compact_parsed["timestamp"], "2026-11-15T12:30:00")
        self.assertEqual(compact_parsed["market_status"], "OPEN")
        self.assertEqual(compact_parsed["ssot"]["portfolio_snapshot"], ssot_data["portfolio_snapshot"])
        self.assertEqual(compact_parsed["ssot"]["unallocated_cash_eur"], ssot_data["unallocated_cash_eur"])
        self.assertEqual(compact_parsed["ssot"]["watched_tickers"], ssot_data["watched_tickers"])
        self.assertEqual(compact_parsed["tickers"][0]["ticker"], "SPY")
        self.assertEqual(compact_parsed["tickers"][0]["net_gex_total"], 1.234)
        self.assertIn("trade_lessons", compact_parsed)
        self.assertIn("decision_log", compact_parsed)

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_endpoint_returns_honest_estimated_token_usage(self, mock_dispatch, mock_charts):
        """/api/chat reports heuristic estimate labels and zero cached tokens when runner estimates tokens."""
        from agent_framework import AntigravityResponse

        def simulate_turn(**kwargs):
            with patch.object(
                web_server.framework.antigravity_runner,
                "run_prompt",
                return_value=AntigravityResponse("Council reply", prompt_tokens=321, candidate_tokens=42, is_estimated=True),
            ):
                web_server.framework.generate_response_with_fallback("prompt", "instr", "PRO")
            return {"status": "completed", "response": "Council reply", "turn_count": 1}

        mock_dispatch.side_effect = simulate_turn

        res = web_server.chat_endpoint(web_server.ChatRequest(message="Status check"))
        self.assertEqual(res["status"], "success")
        usage = res["usage"]
        self.assertTrue(usage["is_estimated"])
        self.assertEqual(usage["token_accounting_mode"], "heuristic_estimate")
        self.assertEqual(usage["prompt_tokens"], 321)
        self.assertEqual(usage["candidates_tokens"], 42)
        self.assertEqual(usage["estimated_prompt_tokens"], 321)
        self.assertEqual(usage["estimated_candidates_tokens"], 42)
        self.assertIsNone(usage["actual_prompt_tokens"])
        self.assertIsNone(usage["actual_candidates_tokens"])
        self.assertEqual(usage["cached_tokens"], 0)
        self.assertFalse(usage["caching_active"])

    def test_set_cache_policy_reports_deprecated_without_mutating_config_or_session(self):
        """set_cache_policy does not mutate context/config.json or rebuild the active session."""
        config_path = os.path.join(os.path.dirname(__file__), "..", "context", "config.json")
        with open(config_path, "rb") as f:
            before_hash = hashlib.sha256(f.read()).hexdigest()

        sentinel_session = MagicMock(name="SentinelSession")
        web_server.global_chat_session = sentinel_session
        web_server._session_hydrated = True

        for flag in (False, True):
            res = web_server.set_cache_policy({"disable_cache": flag})
            self.assertEqual(res["status"], "deprecated")
            self.assertFalse(res["caching_supported"])
            self.assertFalse(res["caching_active"])
            self.assertTrue(res["disable_cache"])
            self.assertIn("deprecated", res["message"].lower())

        with open(config_path, "rb") as f:
            after_hash = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(before_hash, after_hash)
        self.assertIs(web_server.global_chat_session, sentinel_session)
        self.assertTrue(web_server._session_hydrated)


class TestStage333ChatLatencyAndUxCleanup(unittest.TestCase):
    def setUp(self):
        self._orig_model = web_server.ORCHESTRATOR_MODEL
        self._orig_session = web_server.global_chat_session
        self._orig_hydrated = web_server._session_hydrated
        self._orig_cancel_check = web_server.framework.cancel_check
        self._reset_turn_registry()

    def tearDown(self):
        web_server.ORCHESTRATOR_MODEL = self._orig_model
        web_server.global_chat_session = self._orig_session
        web_server._session_hydrated = self._orig_hydrated
        web_server.framework.cancel_check = self._orig_cancel_check
        self._reset_turn_registry()

    def _reset_turn_registry(self):
        web_server.cancel_event.clear()
        with web_server._turn_registry_lock:
            web_server._active_turn_id = None
            web_server._active_turn_event = None
            web_server._turn_events.clear()
            web_server._completed_turn_ids.clear()
            web_server._precancelled_turn_ids.clear()

    def _make_valid_live_state(self):
        ssot_data = get_persistence_service().get_ssot_data()
        return {
            "_meta": {"reliability": "High", "cycle_id": "turn-test"},
            "timestamp": "2026-11-16T00:00:00",
            "status": "OPEN",
            "financial_state_valid": True,
            "local_storage_state": ssot_data,
            "tickers": [],
        }

    @patch("web_server.get_chart_parts", return_value=(["FAKE_CHART_PART"], ["SPY"]))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_endpoint_skips_chart_capture_by_default(self, mock_dispatch, mock_charts):
        """Standard /api/chat requests do not attach chart screenshots unless include_charts=True."""
        mock_dispatch.return_value = {
            "status": "completed",
            "response": "Portfolio posture checked without chart blocking.",
            "turn_count": 1,
        }

        req = web_server.ChatRequest(message="What is our current cash allocation?")
        self.assertFalse(req.include_charts)
        res = web_server.chat_endpoint(req)

        self.assertEqual(res["status"], "success")
        mock_charts.assert_not_called()
        dispatched_msg = mock_dispatch.call_args.kwargs["initial_message"]
        self.assertIsInstance(dispatched_msg, str)

    @patch("web_server.get_chart_parts", return_value=(["FAKE_CHART_PART"], ["SPY"]))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_endpoint_attaches_charts_when_explicitly_requested(self, mock_dispatch, mock_charts):
        """/api/chat invokes get_chart_parts and prepends chart parts when include_charts=True."""
        mock_dispatch.return_value = {
            "status": "completed",
            "response": "Reviewed 1m chart structure.",
            "turn_count": 1,
        }

        req = web_server.ChatRequest(message="Inspect 1m SPY chart", include_charts=True)
        res = web_server.chat_endpoint(req)

        self.assertEqual(res["status"], "success")
        mock_charts.assert_called_once()
        dispatched_msg = mock_dispatch.call_args.kwargs["initial_message"]
        self.assertIsInstance(dispatched_msg, list)
        self.assertEqual(dispatched_msg[0], "FAKE_CHART_PART")
        self.assertIsInstance(dispatched_msg[-1], str)

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_endpoint_cancellation_returns_clean_status_and_preserves_session(self, mock_dispatch, mock_charts):
        """/api/cancel_chat signals cancel_event and preserves session/model on clean cancellation."""
        initial_model = web_server.ORCHESTRATOR_MODEL
        sentinel_session = MagicMock(name="SentinelSession")
        web_server.global_chat_session = sentinel_session

        def simulate_cancel(**kwargs):
            cancel_res = web_server.cancel_chat()
            self.assertEqual(cancel_res["status"], "cancelled")
            self.assertTrue(kwargs["cancel_check"]())
            return {"status": "cancelled", "response": "[OFFLINE] Request cancelled by user."}

        mock_dispatch.side_effect = simulate_cancel

        res = web_server.chat_endpoint(web_server.ChatRequest(message="Long analysis"))
        self.assertEqual(res["status"], "success")
        self.assertIn("cancelled", res["response"].lower())
        self.assertTrue(web_server.cancel_event.is_set())
        self.assertEqual(web_server.ORCHESTRATOR_MODEL, initial_model)
        self.assertIs(web_server.global_chat_session, sentinel_session)

    def test_modern_ui_js_stage333_contracts(self):
        """static/modern_ui.js enforces opt-in chart capture, honest/mixed token accounting, turn-correlated cancellation, and retired cache toggle."""
        ui_path = os.path.join(os.path.dirname(__file__), "..", "static", "modern_ui.js")
        with open(ui_path, "r", encoding="utf-8") as f:
            ui_js = f.read()

        self.assertIn("shouldCaptureCharts", ui_js)
        self.assertIn("include_charts: includeCharts", ui_js)
        self.assertIn("turn_id: turnId", ui_js)
        self.assertIn("_summarizeUsageTokens", ui_js)
        self.assertIn("mixed_estimated_and_measured", ui_js)
        self.assertIn("Mixed Est. Cost", ui_js)
        self.assertIn("AbortController", ui_js)
        self.assertIn("_cleanupPendingTurnUI", ui_js)
        self.assertIn("chars/4 est.", ui_js)
        self.assertIn("Heuristic Est. Cost", ui_js)
        self.assertNotIn("saving up to 75% on token costs", ui_js)
        self.assertNotIn("Context Caching** enabled", ui_js)
        self.assertNotIn("/api/set_cache_policy", ui_js)

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_cancelling_turn_a_cannot_cancel_turn_b(self, mock_dispatch, mock_charts):
        """Cancelling turn A (active or queued) never cancels turn B."""
        live_state = self._make_valid_live_state()

        def simulate_turn(**kwargs):
            msg = kwargs["initial_message"]
            if "[USER_QUERY]: Run B" in msg:
                cancel_b_target_a = web_server.cancel_chat({"turn_id": "turn-A"})
                self.assertEqual(cancel_b_target_a["status"], "cancelled")
                self.assertFalse(kwargs["cancel_check"]())
                self.assertFalse(web_server.cancel_event.is_set())
                return {"status": "completed", "response": "Turn B ok", "turn_count": 1}
            if kwargs["cancel_check"]():
                return {"status": "cancelled", "response": "[OFFLINE] Session terminated by user interrupt."}
            return {"status": "completed", "response": "Unexpected", "turn_count": 1}

        mock_dispatch.side_effect = simulate_turn

        with patch_global_state(live_state):
            res_b = web_server.chat_endpoint(web_server.ChatRequest(message="Run B", turn_id="turn-B"))
            res_a = web_server.chat_endpoint(web_server.ChatRequest(message="Run A", turn_id="turn-A"))

        self.assertEqual(res_b["status"], "success")
        self.assertEqual(res_b["turn_id"], "turn-B")
        self.assertEqual(res_b["response"], "Turn B ok")
        self.assertNotIn("cancelled", res_b)

        self.assertEqual(res_a["status"], "success")
        self.assertTrue(res_a.get("cancelled"))
        self.assertEqual(res_a["turn_id"], "turn-A")

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_late_cancellation_for_turn_a_ignored_after_turn_b_starts(self, mock_dispatch, mock_charts):
        """A stale cancel_chat for completed turn A returns ignored_stale and leaves turn B unaffected."""
        live_state = self._make_valid_live_state()
        b_entered = threading.Event()
        b_release = threading.Event()
        observed_b_cancel = []

        def simulate_turn(**kwargs):
            msg = kwargs["initial_message"]
            if "[USER_QUERY]: Turn A" in msg:
                return {"status": "completed", "response": "Turn A done", "turn_count": 1}
            b_entered.set()
            self.assertTrue(b_release.wait(timeout=3.0))
            observed_b_cancel.append(kwargs["cancel_check"]())
            return {"status": "completed", "response": "Turn B done", "turn_count": 1}

        mock_dispatch.side_effect = simulate_turn

        with patch_global_state(live_state):
            res_a = web_server.chat_endpoint(web_server.ChatRequest(message="Turn A", turn_id="turn-A"))
            self.assertEqual(res_a["turn_id"], "turn-A")

            result_holder = {}

            def run_b():
                result_holder["res_b"] = web_server.chat_endpoint(
                    web_server.ChatRequest(message="Turn B", turn_id="turn-B")
                )

            t_b = threading.Thread(target=run_b)
            t_b.start()
            self.assertTrue(b_entered.wait(timeout=3.0))

            late_cancel = web_server.cancel_chat({"turn_id": "turn-A"})
            self.assertEqual(late_cancel["status"], "ignored_stale")
            self.assertEqual(late_cancel["turn_id"], "turn-A")
            self.assertEqual(late_cancel["active_turn_id"], "turn-B")
            self.assertFalse(web_server.cancel_event.is_set())

            b_release.set()
            t_b.join(timeout=3.0)
            self.assertFalse(t_b.is_alive())

        self.assertEqual(observed_b_cancel, [False])
        res_b = result_holder["res_b"]
        self.assertEqual(res_b["status"], "success")
        self.assertEqual(res_b["turn_id"], "turn-B")
        self.assertEqual(res_b["response"], "Turn B done")
        self.assertNotIn("cancelled", res_b)

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_precancellation_before_registration_prevents_turn_execution(self, mock_dispatch, mock_charts):
        """Cancellation arriving before turn A registers prevents turn A from invoking session/dispatcher."""
        live_state = self._make_valid_live_state()

        pre_cancel = web_server.cancel_chat({"turn_id": "turn-pre-A"})
        self.assertEqual(pre_cancel["status"], "cancelled")
        self.assertEqual(pre_cancel["turn_id"], "turn-pre-A")

        with patch_global_state(live_state):
            res = web_server.chat_endpoint(
                web_server.ChatRequest(message="Should not execute", turn_id="turn-pre-A")
            )

        self.assertEqual(res["status"], "success")
        self.assertTrue(res.get("cancelled"))
        self.assertEqual(res["turn_id"], "turn-pre-A")
        mock_dispatch.assert_not_called()
        mock_charts.assert_not_called()

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_concurrent_chat_requests_serialize_session_and_turn_usage(self, mock_dispatch, mock_charts):
        """Two concurrent /api/chat calls cannot overlap inside session execution or mix turn_usage counters."""
        live_state = self._make_valid_live_state()
        sentinel_session = MagicMock(name="SerializedSession")
        web_server.global_chat_session = sentinel_session

        probe_lock = threading.Lock()
        first_entered = threading.Event()
        metrics = {"inflight": 0, "max_inflight": 0, "sessions": []}

        def simulate_turn(**kwargs):
            with probe_lock:
                metrics["inflight"] += 1
                metrics["max_inflight"] = max(metrics["max_inflight"], metrics["inflight"])
                metrics["sessions"].append(kwargs["session"] is sentinel_session)
            try:
                is_first = "[USER_QUERY]: First request" in kwargs["initial_message"]
                if is_first:
                    first_entered.set()
                    time.sleep(0.08)
                tokens = 111 if is_first else 222
                with web_server.framework._usage_lock:
                    web_server.framework.turn_usage["actual_prompt_tokens"] = tokens
                    web_server.framework.turn_usage["prompt_tokens"] = tokens
                if is_first:
                    time.sleep(0.03)
                return {"status": "completed", "response": f"Done {tokens}", "turn_count": 1}
            finally:
                with probe_lock:
                    metrics["inflight"] -= 1

        mock_dispatch.side_effect = simulate_turn
        results = {}

        def invoke(key, msg, tid):
            results[key] = web_server.chat_endpoint(web_server.ChatRequest(message=msg, turn_id=tid))

        with patch_global_state(live_state):
            t1 = threading.Thread(target=invoke, args=("first", "First request", "turn-1"))
            t2 = threading.Thread(target=invoke, args=("second", "Second request", "turn-2"))
            t1.start()
            self.assertTrue(first_entered.wait(timeout=3.0))
            t2.start()
            t1.join(timeout=5.0)
            t2.join(timeout=5.0)
            self.assertFalse(t1.is_alive())
            self.assertFalse(t2.is_alive())

        self.assertEqual(metrics["max_inflight"], 1)
        self.assertEqual(metrics["sessions"], [True, True])
        self.assertEqual(results["first"]["turn_id"], "turn-1")
        self.assertEqual(results["first"]["response"], "Done 111")
        self.assertEqual(results["first"]["usage"]["prompt_tokens"], 111)
        self.assertEqual(results["second"]["turn_id"], "turn-2")
        self.assertEqual(results["second"]["response"], "Done 222")
        self.assertEqual(results["second"]["usage"]["prompt_tokens"], 222)

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_turn_cancellation_propagates_to_nested_calls_and_cleans_up(self, mock_dispatch, mock_charts):
        """Request-scoped cancellation terminates nested agent/batch checks and restores framework.cancel_check."""
        live_state = self._make_valid_live_state()
        orig_cancel_check = web_server.framework.cancel_check
        entered = threading.Event()
        cancelled_observed = {}

        def simulate_nested(**kwargs):
            turn_check = kwargs["cancel_check"]
            self.assertFalse(turn_check())
            self.assertFalse(web_server.framework.cancel_check())
            entered.set()

            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if web_server.framework.cancel_check() and turn_check():
                    break
                time.sleep(0.01)

            cancelled_observed["framework_cancelled"] = web_server.framework.cancel_check()
            cancelled_observed["dispatch_cancelled"] = turn_check()

            subagent = web_server.framework.create_agent_tool(
                "Bullish Advocate",
                "engine_instructions/bullish_gem.md",
            )
            cancelled_observed["subagent_reply"] = subagent("Check subagent abort")

            batch_ran = []

            def batch_fn():
                batch_ran.append(True)
                return "unexpected"

            batch_fn._agent_role = "Bullish Advocate"
            batch_fn._is_read_only_deliberative = True
            batch_fn._agent_tools = []

            batch_res = web_server.framework.execute_ephemeral_batch(
                {"bullish_advocate": batch_fn}
            )
            cancelled_observed["batch_ran"] = batch_ran
            cancelled_observed["batch_reply"] = batch_res.get("bullish_advocate", "")
            return {"status": "cancelled", "response": "[OFFLINE] Session terminated by user interrupt."}

        mock_dispatch.side_effect = simulate_nested

        out = {}

        def run_turn():
            out["res"] = web_server.chat_endpoint(web_server.ChatRequest(message="Nested run", turn_id="turn-nested"))

        with patch_global_state(live_state):
            t = threading.Thread(target=run_turn)
            t.start()
            self.assertTrue(entered.wait(timeout=3.0))

            cancel_res = web_server.cancel_chat({"turn_id": "turn-nested"})
            self.assertEqual(cancel_res, {"status": "cancelled", "turn_id": "turn-nested"})

            t.join(timeout=3.0)
            self.assertFalse(t.is_alive())

        self.assertTrue(cancelled_observed.get("framework_cancelled"))
        self.assertTrue(cancelled_observed.get("dispatch_cancelled"))
        self.assertIn("[OFFLINE] Session terminated", cancelled_observed.get("subagent_reply", ""))
        self.assertEqual(cancelled_observed.get("batch_ran"), [])
        self.assertIn("[OFFLINE] Session terminated", cancelled_observed.get("batch_reply", ""))
        self.assertEqual(out["res"]["status"], "success")
        self.assertTrue(out["res"].get("cancelled"))
        self.assertEqual(out["res"]["turn_id"], "turn-nested")
        self.assertIs(web_server.framework.cancel_check, orig_cancel_check)
        with web_server._turn_registry_lock:
            self.assertIsNone(web_server._active_turn_id)
            self.assertIsNone(web_server._active_turn_event)
            self.assertIn("turn-nested", web_server._completed_turn_ids)

    def test_modern_ui_js_runtime_mixed_tokens_and_turn_isolation(self):
        """Node runtime test verifies mixed token badges/cost and turn-ID-scoped UI cleanup in modern_ui.js."""
        node_bin = shutil.which("node")
        self.assertIsNotNone(node_bin, "node executable must be available to execute JS runtime tests")
        ui_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static", "modern_ui.js"))

        script = r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const uiCode = fs.readFileSync(process.argv[1], 'utf8');

function makeElement(tag, id) {
    const el = {
        tagName: tag.toUpperCase(),
        id: id || '',
        className: '',
        innerHTML: '',
        textContent: '',
        value: '',
        disabled: false,
        checked: false,
        style: {},
        options: [],
        children: [],
        attributes: {},
        scrollTop: 0,
        scrollHeight: 100,
        parentNode: null,
        setAttribute(k, v) { this.attributes[k] = String(v); },
        getAttribute(k) { return this.attributes[k]; },
        appendChild(child) {
            child.parentNode = this;
            this.children.push(child);
            if (child.id) elementsById[child.id] = child;
            return child;
        },
        remove() {
            if (this.id && elementsById[this.id] === this) delete elementsById[this.id];
            if (this.parentNode) {
                this.parentNode.children = this.parentNode.children.filter(c => c !== this);
            }
        },
        focus() {},
        addEventListener() {},
        querySelectorAll() { return []; },
        querySelector() { return null; },
        classList: { contains: () => false, add() {}, remove() {} },
    };
    if (id) elementsById[id] = el;
    return el;
}

const elementsById = {};
[
    'chat-window', 'launch-chat-btn', 'chat-close-btn', 'chat-input',
    'chat-messages', 'send-chat-btn', 'stop-chat-btn', 'chat-model-select',
    'clear-chat-btn', 'gemini-subscription-toggle', 'history-chat-btn',
    'chat-status-indicator', 'new-chat-btn', 'model-selector'
].forEach(id => makeElement('div', id));

const fetchCalls = [];
let pendingChatResolvers = [];

class MockEventSource {
    constructor() {}
    close() {}
}

const sandbox = {
    console,
    Date,
    Math,
    JSON,
    Number,
    Boolean,
    Object,
    Array,
    String,
    Promise,
    AbortController,
    EventSource: MockEventSource,
    setTimeout,
    clearTimeout,
    window: { addEventListener() {} },
    document: {
        body: makeElement('body'),
        getElementById: (id) => elementsById[id] || null,
        createElement: (tag) => makeElement(tag),
        querySelector: () => null,
        querySelectorAll: () => [],
        addEventListener() {},
    },
    localStorage: {
        _store: {},
        getItem(k) { return this._store[k] || null; },
        setItem(k, v) { this._store[k] = String(v); },
        removeItem(k) { delete this._store[k]; },
    },
    marked: { parse: (s) => String(s) },
    fetch: (url, opts) => {
        fetchCalls.push({ url, opts });
        if (url === '/api/chat') {
            return new Promise((resolve, reject) => {
                if (opts && opts.signal) {
                    opts.signal.addEventListener('abort', () => {
                        const err = new Error('Aborted');
                        err.name = 'AbortError';
                        reject(err);
                    });
                }
                pendingChatResolvers.push(resolve);
            });
        }
        if (url === '/api/list_models') {
            return Promise.resolve({ json: async () => ({ status: 'success', models: [], current_model: 'gemini-3.8-flash-high' }) });
        }
        if (url === '/api/watchlist' || url === '/api/tickers') {
            return Promise.resolve({ json: async () => ([]) });
        }
        if (url === '/api/data') {
            return Promise.resolve({ json: async () => ({ tickers: [], local_storage_state: {} }) });
        }
        return Promise.resolve({ json: async () => ({ status: 'cancelled' }) });
    },
};

vm.runInNewContext(uiCode, sandbox);
const chatUI = sandbox.window.chatUI;
chatUI.init();

// 1. Mixed measured + estimated token accounting preserves both components
const mixedUsage = {
    actual_prompt_tokens: 200,
    estimated_prompt_tokens: 40,
    prompt_tokens: 240,
    actual_candidates_tokens: 100,
    estimated_candidates_tokens: 20,
    candidates_tokens: 120,
    cached_tokens: 0,
    caching_active: false,
    is_estimated: true,
    token_accounting_mode: 'mixed_estimated_and_measured',
};
const mixedCost = chatUI.calculateCost(mixedUsage, 'gemini-3.8-flash-high');
const expectedMixedCost = (240 * 0.075 + 120 * 0.30) / 1000000;
assert(Math.abs(mixedCost - expectedMixedCost) < 1e-12, `Expected mixed cost ${expectedMixedCost}, got ${mixedCost}`);

chatUI.appendMessage('ai', 'Mixed reply', false, mixedCost, mixedUsage, 'gemini-3.8-flash-high');
const lastMsg = elementsById['chat-messages'].children.slice(-1)[0];
const mixedBadgeHtml = lastMsg.children.find(c => c.className === 'cost-estimator').innerHTML;
assert(mixedBadgeHtml.includes('Mixed Est. Cost:'), mixedBadgeHtml);
assert(mixedBadgeHtml.includes('240 in [200 meas. + ~40 est.]'), mixedBadgeHtml);
assert(mixedBadgeHtml.includes('120 out [100 meas. + ~20 est.]'), mixedBadgeHtml);
assert(mixedBadgeHtml.includes('mixed'), mixedBadgeHtml);

// 2. Dual non-zero actual + estimated fields without explicit mode still aggregate cleanly
const implicitMixed = {
    actual_prompt_tokens: 100,
    estimated_prompt_tokens: 50,
    actual_candidates_tokens: 60,
    estimated_candidates_tokens: 10,
};
const implicitCost = chatUI.calculateCost(implicitMixed, 'gemini-3.8-flash-high');
const expectedImplicitCost = (150 * 0.075 + 70 * 0.30) / 1000000;
assert(Math.abs(implicitCost - expectedImplicitCost) < 1e-12);

// 3. Heuristic-only usage still displays heuristic label cleanly
const estOnlyUsage = {
    actual_prompt_tokens: 0,
    estimated_prompt_tokens: 120,
    prompt_tokens: 120,
    actual_candidates_tokens: 0,
    estimated_candidates_tokens: 40,
    candidates_tokens: 40,
    cached_tokens: 0,
    caching_active: false,
    is_estimated: true,
    token_accounting_mode: 'heuristic_estimate',
};
chatUI.appendMessage('ai', 'Est reply', false, 0.0001, estOnlyUsage, 'gemini-3.8-flash-high');
const estMsg = elementsById['chat-messages'].children.slice(-1)[0];
const estBadgeHtml = estMsg.children.find(c => c.className === 'cost-estimator').innerHTML;
assert(estBadgeHtml.includes('Heuristic Est. Cost:'), estBadgeHtml);
assert(estBadgeHtml.includes('~120 est. in / ~40 est. out'), estBadgeHtml);

// 4. Turn-ID-scoped UI cleanup & stopMessage correlation
(async () => {
    elementsById['chat-input'].value = 'Turn A prompt';
    const pA = chatUI.sendMessage(false, { turnId: 'turn-ui-A' });
    await Promise.resolve();
    const chatCallA = fetchCalls.find(c => c.url === '/api/chat' && JSON.parse(c.opts.body).turn_id === 'turn-ui-A');
    assert(chatCallA, 'Expected /api/chat payload with turn_id=turn-ui-A');
    assert(elementsById['msg-turn-ui-A'], 'Expected thinking bubble msg-turn-ui-A');

    // Start newer Turn B; older Turn A cleanup must NOT clear Turn B UI state
    elementsById['chat-input'].value = 'Turn B prompt';
    const pB = chatUI.sendMessage(false, { turnId: 'turn-ui-B' });
    await Promise.resolve();
    assert.strictEqual(chatUI.activeTurnId, 'turn-ui-B');
    assert.strictEqual(chatUI.currentThinkingId, 'msg-turn-ui-B');

    chatUI._cleanupPendingTurnUI('msg-turn-ui-A', 'turn-ui-A');
    assert(!elementsById['msg-turn-ui-A'], 'Older thinking bubble removed');
    assert(elementsById['msg-turn-ui-B'], 'Active thinking bubble B preserved');
    assert.strictEqual(chatUI.activeTurnId, 'turn-ui-B');
    assert.strictEqual(elementsById['chat-input'].disabled, true);
    assert.strictEqual(elementsById['stop-chat-btn'].style.display, 'flex');

    await chatUI.stopMessage();
    const cancelCall = fetchCalls.find(c => c.url === '/api/cancel_chat');
    assert(cancelCall, 'Expected POST /api/cancel_chat');
    assert.deepStrictEqual(JSON.parse(cancelCall.opts.body), { turn_id: 'turn-ui-B' });
    assert.strictEqual(chatUI.activeTurnId, null);

    pendingChatResolvers.forEach(r => r({ json: async () => ({ status: 'success', response: 'ignored' }) }));
    await Promise.all([pA, pB]);
})().catch((err) => {
    console.error(err);
    process.exit(1);
});
"""
        proc = subprocess.run(
            [node_bin, "-e", script, ui_path],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"Node UI runtime check failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
