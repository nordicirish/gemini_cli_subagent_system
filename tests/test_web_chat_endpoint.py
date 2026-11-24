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

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import atomic_io
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
    configure_persistence,
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


class _ObservedRLock:
    """Deterministic RLock wrapper signaling when a second thread blocks acquiring the lock."""

    def __init__(self, inner, on_contended_enter=None):
        self._inner = inner
        self._on_contended_enter = on_contended_enter

    def __enter__(self):
        acquired_immediately = self._inner.acquire(blocking=False)
        if not acquired_immediately:
            if self._on_contended_enter is not None:
                self._on_contended_enter()
            self._inner.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._inner.release()
        return False


class TestStage333ChatLatencyAndUxCleanup(unittest.TestCase):
    def setUp(self):
        self._orig_model = web_server.ORCHESTRATOR_MODEL
        self._orig_session = web_server.global_chat_session
        self._orig_hydrated = web_server._session_hydrated
        self._orig_cancel_check = web_server.framework.cancel_check
        self._orig_free_tier_only = getattr(web_server.framework, "free_tier_only", True)
        self._orig_sub_linked = getattr(web_server.framework, "gemini_subscription_linked", False)
        self._orig_warning = web_server.active_model_warning
        self._reset_turn_registry()

    def tearDown(self):
        web_server.ORCHESTRATOR_MODEL = self._orig_model
        web_server.global_chat_session = self._orig_session
        web_server._session_hydrated = self._orig_hydrated
        web_server.framework.cancel_check = self._orig_cancel_check
        web_server.framework.free_tier_only = self._orig_free_tier_only
        web_server.framework.gemini_subscription_linked = self._orig_sub_linked
        web_server.active_model_warning = self._orig_warning
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
        second_waiting_on_lock = threading.Event()
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
                    self.assertTrue(second_waiting_on_lock.wait(timeout=3.0))
                tokens = 111 if is_first else 222
                with web_server.framework._usage_lock:
                    web_server.framework.turn_usage["actual_prompt_tokens"] = tokens
                    web_server.framework.turn_usage["prompt_tokens"] = tokens
                return {"status": "completed", "response": f"Done {tokens}", "turn_count": 1}
            finally:
                with probe_lock:
                    metrics["inflight"] -= 1

        mock_dispatch.side_effect = simulate_turn
        results = {}

        def invoke(key, msg, tid):
            results[key] = web_server.chat_endpoint(web_server.ChatRequest(message=msg, turn_id=tid))

        observed_lock = _ObservedRLock(
            web_server._chat_execution_lock,
            on_contended_enter=second_waiting_on_lock.set,
        )
        with patch.object(web_server, "_chat_execution_lock", observed_lock), patch_global_state(live_state):
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
            active_evt = web_server._active_turn_event
            self.assertIsNotNone(active_evt)
            entered.set()
            self.assertTrue(active_evt.wait(timeout=3.0))

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

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_set_model_serializes_with_active_chat_and_applies_to_next_chat(self, mock_dispatch, mock_charts):
        """set_model waits for an active chat turn before changing policy/model/session cleanly for the next turn."""
        live_state = self._make_valid_live_state()
        initial_session = MagicMock(name="InitialSession")
        recalibrated_session = MagicMock(name="RecalibratedSession")

        web_server.ORCHESTRATOR_MODEL = "gemini-3.8-flash-high"
        web_server.framework.free_tier_only = True
        web_server.framework.gemini_subscription_linked = False
        web_server.global_chat_session = initial_session
        web_server._session_hydrated = True

        turn1_entered = threading.Event()
        set_model_waiting_on_lock = threading.Event()
        set_model_done = threading.Event()
        turn1_observations = {}
        turn2_observations = {}

        def simulate_turn(**kwargs):
            msg = kwargs["initial_message"]
            if "[USER_QUERY]: First active turn" in msg:
                turn1_entered.set()
                self.assertTrue(set_model_waiting_on_lock.wait(timeout=3.0))
                turn1_observations["model"] = web_server.ORCHESTRATOR_MODEL
                turn1_observations["free_tier_only"] = web_server.framework.free_tier_only
                turn1_observations["sub_linked"] = web_server.framework.gemini_subscription_linked
                turn1_observations["session"] = kwargs["session"]
                turn1_observations["hydrated"] = web_server._session_hydrated
                turn1_observations["set_model_finished_early"] = set_model_done.is_set()
                return {"status": "completed", "response": "Turn 1 ok", "turn_count": 1}

            turn2_observations["model"] = web_server.ORCHESTRATOR_MODEL
            turn2_observations["free_tier_only"] = web_server.framework.free_tier_only
            turn2_observations["sub_linked"] = web_server.framework.gemini_subscription_linked
            turn2_observations["session"] = kwargs["session"]
            turn2_observations["hydrated"] = web_server._session_hydrated
            turn2_observations["initial_message"] = msg
            return {"status": "completed", "response": "Turn 2 ok", "turn_count": 1}

        mock_dispatch.side_effect = simulate_turn

        set_model_out = {}
        turn1_out = {}

        def run_turn1():
            turn1_out["res"] = web_server.chat_endpoint(
                web_server.ChatRequest(message="First active turn", turn_id="turn-model-1")
            )

        def run_set_model():
            set_model_out["res"] = web_server.set_model(
                {
                    "model": "gemini-4-argon-high",
                    "include_paid": True,
                    "gemini_subscription_linked": True,
                }
            )
            set_model_done.set()

        orig_exists = os.path.exists
        observed_lock = _ObservedRLock(
            web_server._chat_execution_lock,
            on_contended_enter=set_model_waiting_on_lock.set,
        )

        with (
            patch.object(web_server, "_chat_execution_lock", observed_lock),
            patch("web_server.create_new_session", return_value=recalibrated_session) as mock_create_session,
            patch(
                "web_server.os.path.exists",
                side_effect=lambda p: False if "config.json" in str(p) else orig_exists(p),
            ),
            patch_global_state(live_state),
        ):
            t_chat = threading.Thread(target=run_turn1)
            t_model = threading.Thread(target=run_set_model)
            t_chat.start()
            self.assertTrue(turn1_entered.wait(timeout=3.0))
            t_model.start()
            t_chat.join(timeout=5.0)
            t_model.join(timeout=5.0)
            self.assertFalse(t_chat.is_alive())
            self.assertFalse(t_model.is_alive())

            self.assertTrue(set_model_done.is_set())
            self.assertEqual(mock_create_session.call_count, 1)

            res_turn2 = web_server.chat_endpoint(
                web_server.ChatRequest(message="Next turn", turn_id="turn-model-2")
            )

        self.assertEqual(turn1_out["res"]["status"], "success")
        self.assertEqual(turn1_observations["model"], "gemini-3.8-flash-high")
        self.assertTrue(turn1_observations["free_tier_only"])
        self.assertFalse(turn1_observations["sub_linked"])
        self.assertIs(turn1_observations["session"], initial_session)
        self.assertTrue(turn1_observations["hydrated"])
        self.assertFalse(turn1_observations["set_model_finished_early"])

        self.assertEqual(set_model_out["res"]["status"], "success")
        self.assertEqual(set_model_out["res"]["current_model"], "gemini-4-argon-high")

        self.assertEqual(res_turn2["status"], "success")
        self.assertEqual(res_turn2["model"], "gemini-4-argon-high")
        self.assertEqual(turn2_observations["model"], "gemini-4-argon-high")
        self.assertFalse(turn2_observations["free_tier_only"])
        self.assertTrue(turn2_observations["sub_linked"])
        self.assertIs(turn2_observations["session"], recalibrated_session)
        self.assertFalse(turn2_observations["hydrated"])
        self.assertIn("[ACTIVE_MODEL]: gemini-4-argon-high", turn2_observations["initial_message"])

    def test_set_model_rolls_back_policy_and_session_when_session_creation_fails(self):
        """Session creation failure inside set_model rolls back model, flags, hydration, warning, and session."""
        initial_session = MagicMock(name="IntactSession")
        web_server.ORCHESTRATOR_MODEL = "gemini-3.8-flash-high"
        web_server.framework.free_tier_only = True
        web_server.framework.gemini_subscription_linked = False
        web_server.global_chat_session = initial_session
        web_server._session_hydrated = True
        web_server.active_model_warning = "existing-warning"

        orig_exists = os.path.exists
        with (
            patch("web_server.create_new_session", side_effect=RuntimeError("Session init failure")),
            patch(
                "web_server.os.path.exists",
                side_effect=lambda p: False if "config.json" in str(p) else orig_exists(p),
            ),
        ):
            with self.assertRaises(RuntimeError):
                web_server.set_model(
                    {
                        "model": "gemini-4-argon-high",
                        "include_paid": True,
                        "gemini_subscription_linked": True,
                    }
                )

        self.assertEqual(web_server.ORCHESTRATOR_MODEL, "gemini-3.8-flash-high")
        self.assertTrue(web_server.framework.free_tier_only)
        self.assertFalse(web_server.framework.gemini_subscription_linked)
        self.assertIs(web_server.global_chat_session, initial_session)
        self.assertTrue(web_server._session_hydrated)
        self.assertEqual(web_server.active_model_warning, "existing-warning")

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_abandoned_precancellation_does_not_retain_event_and_bounds_registry(self, mock_dispatch, mock_charts):
        """Abandoned pre-arrival cancellations never retain events in _turn_events and stay bounded."""
        live_state = self._make_valid_live_state()
        mock_dispatch.return_value = {"status": "completed", "response": "Fresh ok", "turn_count": 1}

        res0 = web_server.cancel_chat({"turn_id": "turn-abandoned-0"})
        self.assertEqual(res0, {"status": "cancelled", "turn_id": "turn-abandoned-0"})
        self.assertEqual(len(web_server._turn_events), 0)
        self.assertIn("turn-abandoned-0", web_server._precancelled_turn_ids)

        for idx in range(1, 300):
            res_i = web_server.cancel_chat({"turn_id": f"turn-abandoned-{idx}"})
            self.assertEqual(res_i["status"], "cancelled")

        self.assertEqual(len(web_server._turn_events), 0)
        self.assertEqual(len(web_server._precancelled_turn_ids), web_server._MAX_TRACKED_TURN_IDS)
        self.assertNotIn("turn-abandoned-0", web_server._precancelled_turn_ids)
        self.assertNotIn("turn-abandoned-43", web_server._precancelled_turn_ids)
        self.assertIn("turn-abandoned-44", web_server._precancelled_turn_ids)
        self.assertIn("turn-abandoned-299", web_server._precancelled_turn_ids)

        # Latest bounded pre-arrival cancellation is still honoured without leaking events
        with patch_global_state(live_state):
            aborted = web_server.chat_endpoint(
                web_server.ChatRequest(message="Should abort", turn_id="turn-abandoned-299")
            )
            fresh = web_server.chat_endpoint(
                web_server.ChatRequest(message="Fresh unaffected turn", turn_id="turn-fresh")
            )

        self.assertTrue(aborted.get("cancelled"))
        self.assertEqual(aborted["turn_id"], "turn-abandoned-299")
        mock_dispatch.assert_called_once()
        self.assertNotIn("cancelled", fresh)
        self.assertEqual(fresh["turn_id"], "turn-fresh")
        self.assertEqual(len(web_server._turn_events), 0)
        self.assertNotIn("turn-abandoned-299", web_server._precancelled_turn_ids)

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_concurrent_registration_and_cancellation_never_loses_cancellation_or_leaks_events(
        self, mock_dispatch, mock_charts
    ):
        """Racing cancel_chat against chat_endpoint registration never drops cancellation or retains events."""
        live_state = self._make_valid_live_state()

        def wait_if_dispatched(**kwargs):
            evt = web_server._active_turn_event
            self.assertIsNotNone(evt)
            self.assertTrue(evt.wait(timeout=3.0))
            self.assertTrue(kwargs["cancel_check"]())
            return {"status": "cancelled", "response": "[OFFLINE] Session terminated by user interrupt."}

        mock_dispatch.side_effect = wait_if_dispatched

        with patch_global_state(live_state):
            for idx in range(10):
                tid = f"turn-race-{idx}"
                barrier = threading.Barrier(2)
                outcomes = {}

                def run_chat():
                    barrier.wait(timeout=3.0)
                    outcomes["chat"] = web_server.chat_endpoint(
                        web_server.ChatRequest(message=f"Race {idx}", turn_id=tid)
                    )

                def run_cancel():
                    barrier.wait(timeout=3.0)
                    outcomes["cancel"] = web_server.cancel_chat({"turn_id": tid})

                t_chat = threading.Thread(target=run_chat)
                t_cancel = threading.Thread(target=run_cancel)
                t_chat.start()
                t_cancel.start()
                t_chat.join(timeout=5.0)
                t_cancel.join(timeout=5.0)
                self.assertFalse(t_chat.is_alive())
                self.assertFalse(t_cancel.is_alive())

                self.assertEqual(outcomes["cancel"], {"status": "cancelled", "turn_id": tid})
                self.assertEqual(outcomes["chat"]["status"], "success")
                self.assertTrue(outcomes["chat"].get("cancelled"))
                self.assertEqual(outcomes["chat"]["turn_id"], tid)
                self.assertEqual(len(web_server._turn_events), 0)
                self.assertNotIn(tid, web_server._precancelled_turn_ids)
                self.assertIn(tid, web_server._completed_turn_ids)
                self.assertIsNone(web_server._active_turn_id)
                self.assertIsNone(web_server._active_turn_event)


class TestStage35ConfigPersistenceAndMutationBoundary(unittest.TestCase):
    def setUp(self):
        self._orig_model = web_server.ORCHESTRATOR_MODEL
        self._orig_session = web_server.global_chat_session
        self._orig_hydrated = web_server._session_hydrated
        self._orig_free_tier_only = getattr(web_server.framework, "free_tier_only", True)
        self._orig_sub_linked = getattr(web_server.framework, "gemini_subscription_linked", False)
        self._orig_warning = web_server.active_model_warning

        self._real_config_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "context", "config.json")
        )
        if os.path.exists(self._real_config_path):
            with open(self._real_config_path, "rb") as f:
                self._real_config_sha256 = hashlib.sha256(f.read()).hexdigest()
        else:
            self._real_config_sha256 = None

        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_config_path = os.path.join(self.temp_dir.name, "config.json")

        self._real_exists = os.path.exists
        self._real_atomic_update = atomic_io.atomic_update_json
        self._real_file_lock = atomic_io.FileLock
        self._atomic_update_override = None
        self._file_lock_enter_hook = None

        def routed_exists(path):
            if str(path).replace("\\", "/") == "context/config.json":
                return self._real_exists(self.temp_config_path)
            return self._real_exists(path)

        def routed_atomic_update(path, update_fn, *args, **kwargs):
            if str(path).replace("\\", "/") == "context/config.json":
                if self._atomic_update_override is not None:
                    return self._atomic_update_override(path, update_fn, *args, **kwargs)
                return self._real_atomic_update(self.temp_config_path, update_fn, *args, **kwargs)
            return self._real_atomic_update(path, update_fn, *args, **kwargs)

        def routed_file_lock(lock_path, *args, **kwargs):
            norm = str(lock_path).replace("\\", "/")
            if norm == "context/config.json.lock":
                inner_lock = self._real_file_lock(self.temp_config_path + ".lock", *args, **kwargs)
                hook = self._file_lock_enter_hook
                if hook is None:
                    return inner_lock

                class _HookedLockContext:
                    def __enter__(self_ctx):
                        hook()
                        return inner_lock.__enter__()

                    def __exit__(self_ctx, exc_type, exc_val, exc_tb):
                        return inner_lock.__exit__(exc_type, exc_val, exc_tb)

                return _HookedLockContext()
            return self._real_file_lock(lock_path, *args, **kwargs)

        self._patch_exists = patch("web_server.os.path.exists", side_effect=routed_exists)
        self._patch_atomic_update = patch("web_server.atomic_update_json", side_effect=routed_atomic_update)
        self._patch_file_lock = patch("web_server.FileLock", side_effect=routed_file_lock)
        self._patch_exists.start()
        self._patch_atomic_update.start()
        self._patch_file_lock.start()

    def tearDown(self):
        self._patch_file_lock.stop()
        self._patch_atomic_update.stop()
        self._patch_exists.stop()

        web_server.ORCHESTRATOR_MODEL = self._orig_model
        web_server.global_chat_session = self._orig_session
        web_server._session_hydrated = self._orig_hydrated
        web_server.framework.free_tier_only = self._orig_free_tier_only
        web_server.framework.gemini_subscription_linked = self._orig_sub_linked
        web_server.active_model_warning = self._orig_warning

        if self._real_config_sha256 is not None:
            self.assertTrue(os.path.exists(self._real_config_path))
            with open(self._real_config_path, "rb") as f:
                after_sha256 = hashlib.sha256(f.read()).hexdigest()
            self.assertEqual(self._real_config_sha256, after_sha256)
        else:
            self.assertFalse(os.path.exists(self._real_config_path))

        self.temp_dir.cleanup()

    def _write_temp_config_bytes(self, raw_bytes: bytes) -> None:
        with open(self.temp_config_path, "wb") as f:
            f.write(raw_bytes)

    def _read_temp_config_bytes(self) -> bytes:
        with open(self.temp_config_path, "rb") as f:
            return f.read()

    def test_set_model_atomic_persistence_updates_subscription_policy_and_preserves_unrelated_keys(self):
        """set_model updates GEMINI_SUBSCRIPTION_LINKED via atomic_update_json without mutating input dict or unrelated keys."""
        initial_config = {
            "GEMINI_SUBSCRIPTION_LINKED": False,
            "ANTIGRAVITY_MODEL_PRO": "gemini-3.8-flash-high",
            "WATCHLIST": ["SPY", "NVDA"],
            "SCOUT_TICKER_MAP": {"AI": ["NVDA", "AMD"]},
            "NESTED_SETTINGS": {"retry": 3, "flags": ["alpha", "beta"]},
        }
        self._write_temp_config_bytes(json.dumps(initial_config, indent=2).encode("utf-8"))

        initial_session = MagicMock(name="InitialSession")
        recalibrated_session = MagicMock(name="RecalibratedSession")
        web_server.ORCHESTRATOR_MODEL = "gemini-3.8-flash-high"
        web_server.framework.free_tier_only = True
        web_server.framework.gemini_subscription_linked = False
        web_server.global_chat_session = initial_session
        web_server._session_hydrated = True
        web_server.active_model_warning = "prior-warning"

        observed_calls = []

        def spy_atomic_update(path, update_fn, *args, **kwargs):
            def non_mutating_check(cfg):
                snapshot = copy.deepcopy(cfg) if isinstance(cfg, dict) else cfg
                result = update_fn(cfg)
                if isinstance(cfg, dict):
                    self.assertEqual(cfg, snapshot)
                    self.assertIsNot(result, cfg)
                return result

            observed_calls.append(path)
            return self._real_atomic_update(self.temp_config_path, non_mutating_check, *args, **kwargs)

        self._atomic_update_override = spy_atomic_update

        with (
            patch("web_server.create_new_session", return_value=recalibrated_session),
            patch("web_server.check_model_validity", return_value=None),
        ):
            res = web_server.set_model(
                {
                    "model": "gemini-4-argon-high",
                    "include_paid": True,
                    "gemini_subscription_linked": True,
                }
            )

        self.assertEqual(observed_calls, ["context/config.json"])
        self.assertEqual(
            res,
            {"status": "success", "current_model": "gemini-4-argon-high", "warning": None},
        )
        self.assertEqual(web_server.ORCHESTRATOR_MODEL, "gemini-4-argon-high")
        self.assertFalse(web_server.framework.free_tier_only)
        self.assertTrue(web_server.framework.gemini_subscription_linked)
        self.assertIs(web_server.global_chat_session, recalibrated_session)
        self.assertFalse(web_server._session_hydrated)
        self.assertIsNone(web_server.active_model_warning)

        persisted = json.loads(self._read_temp_config_bytes().decode("utf-8"))
        self.assertTrue(persisted["GEMINI_SUBSCRIPTION_LINKED"])
        self.assertEqual(persisted["ANTIGRAVITY_MODEL_PRO"], "gemini-3.8-flash-high")
        self.assertEqual(persisted["WATCHLIST"], ["SPY", "NVDA"])
        self.assertEqual(persisted["SCOUT_TICKER_MAP"], {"AI": ["NVDA", "AMD"]})
        self.assertEqual(persisted["NESTED_SETTINGS"], {"retry": 3, "flags": ["alpha", "beta"]})

        leftover_tmp = [name for name in os.listdir(self.temp_dir.name) if name.endswith(".tmp")]
        self.assertEqual(leftover_tmp, [])

    def test_set_model_persistence_failure_rolls_back_all_in_memory_state_and_reraises(self):
        """Persistence failure after in-memory transition propagates exception and restores all 6 state fields."""
        initial_config = {"GEMINI_SUBSCRIPTION_LINKED": False, "ANTIGRAVITY_MODEL_PRO": "gemini-3.8-flash-high"}
        original_bytes = json.dumps(initial_config, indent=2).encode("utf-8")
        self._write_temp_config_bytes(original_bytes)

        initial_session = MagicMock(name="InitialSession")
        recalibrated_session = MagicMock(name="RecalibratedSession")
        web_server.ORCHESTRATOR_MODEL = "gemini-3.8-flash-high"
        web_server.framework.free_tier_only = True
        web_server.framework.gemini_subscription_linked = False
        web_server.global_chat_session = initial_session
        web_server._session_hydrated = True
        web_server.active_model_warning = "existing-warning"

        injected_error = IOError("Simulated atomic replacement failure")
        transition_observed = {}

        def failing_atomic_update(path, update_fn, *args, **kwargs):
            transition_observed["model"] = web_server.ORCHESTRATOR_MODEL
            transition_observed["free_tier_only"] = web_server.framework.free_tier_only
            transition_observed["sub_linked"] = web_server.framework.gemini_subscription_linked
            transition_observed["session"] = web_server.global_chat_session
            transition_observed["hydrated"] = web_server._session_hydrated
            transition_observed["warning"] = web_server.active_model_warning
            raise injected_error

        self._atomic_update_override = failing_atomic_update

        with (
            patch("web_server.create_new_session", return_value=recalibrated_session),
            patch("web_server.check_model_validity", return_value="new-warning"),
        ):
            with self.assertRaises(IOError) as ctx:
                web_server.set_model(
                    {
                        "model": "gemini-4-argon-high",
                        "include_paid": True,
                        "gemini_subscription_linked": True,
                    }
                )

        self.assertIs(ctx.exception, injected_error)
        self.assertEqual(transition_observed["model"], "gemini-4-argon-high")
        self.assertFalse(transition_observed["free_tier_only"])
        self.assertTrue(transition_observed["sub_linked"])
        self.assertIs(transition_observed["session"], recalibrated_session)
        self.assertFalse(transition_observed["hydrated"])
        self.assertEqual(transition_observed["warning"], "new-warning")

        self.assertEqual(web_server.ORCHESTRATOR_MODEL, "gemini-3.8-flash-high")
        self.assertTrue(web_server.framework.free_tier_only)
        self.assertFalse(web_server.framework.gemini_subscription_linked)
        self.assertIs(web_server.global_chat_session, initial_session)
        self.assertTrue(web_server._session_hydrated)
        self.assertEqual(web_server.active_model_warning, "existing-warning")
        self.assertEqual(self._read_temp_config_bytes(), original_bytes)

    def test_set_model_malformed_json_fails_closed_preserves_file_and_rolls_back_state(self):
        """Malformed JSON in config fails closed, preserves original file bytes, and rolls back all in-memory state."""
        malformed_bytes = b'{"GEMINI_SUBSCRIPTION_LINKED": false, "broken": '
        self._write_temp_config_bytes(malformed_bytes)

        initial_session = MagicMock(name="InitialSession")
        recalibrated_session = MagicMock(name="RecalibratedSession")
        web_server.ORCHESTRATOR_MODEL = "gemini-3.8-flash-high"
        web_server.framework.free_tier_only = True
        web_server.framework.gemini_subscription_linked = False
        web_server.global_chat_session = initial_session
        web_server._session_hydrated = True
        web_server.active_model_warning = "prior-warning"

        with (
            patch("web_server.create_new_session", return_value=recalibrated_session),
            patch("web_server.check_model_validity", return_value=None),
        ):
            with self.assertRaises(ValueError) as ctx:
                web_server.set_model(
                    {
                        "model": "gemini-4-argon-high",
                        "include_paid": True,
                        "gemini_subscription_linked": True,
                    }
                )

        self.assertIn("Failed to parse existing JSON file", str(ctx.exception))
        self.assertEqual(web_server.ORCHESTRATOR_MODEL, "gemini-3.8-flash-high")
        self.assertTrue(web_server.framework.free_tier_only)
        self.assertFalse(web_server.framework.gemini_subscription_linked)
        self.assertIs(web_server.global_chat_session, initial_session)
        self.assertTrue(web_server._session_hydrated)
        self.assertEqual(web_server.active_model_warning, "prior-warning")
        self.assertEqual(self._read_temp_config_bytes(), malformed_bytes)

    def test_set_model_invalid_top_level_config_structure_fails_closed_preserves_file_and_rolls_back_state(self):
        """Valid non-object JSON top-level values fail closed, preserve file bytes, and roll back all state."""
        for raw_payload in (b'["not", "a", "dict"]', b'"not_an_object"'):
            with self.subTest(raw_payload=raw_payload):
                self._write_temp_config_bytes(raw_payload)

                initial_session = MagicMock(name="InitialSession")
                recalibrated_session = MagicMock(name="RecalibratedSession")
                web_server.ORCHESTRATOR_MODEL = "gemini-3.8-flash-high"
                web_server.framework.free_tier_only = True
                web_server.framework.gemini_subscription_linked = False
                web_server.global_chat_session = initial_session
                web_server._session_hydrated = True
                web_server.active_model_warning = "prior-warning"

                with (
                    patch("web_server.create_new_session", return_value=recalibrated_session),
                    patch("web_server.check_model_validity", return_value=None),
                ):
                    with self.assertRaises(ValueError) as ctx:
                        web_server.set_model(
                            {
                                "model": "gemini-4-argon-high",
                                "include_paid": True,
                                "gemini_subscription_linked": True,
                            }
                        )

                self.assertIn("Configuration must be a JSON object", str(ctx.exception))
                self.assertEqual(web_server.ORCHESTRATOR_MODEL, "gemini-3.8-flash-high")
                self.assertTrue(web_server.framework.free_tier_only)
                self.assertFalse(web_server.framework.gemini_subscription_linked)
                self.assertIs(web_server.global_chat_session, initial_session)
                self.assertTrue(web_server._session_hydrated)
                self.assertEqual(web_server.active_model_warning, "prior-warning")
                self.assertEqual(self._read_temp_config_bytes(), raw_payload)

    def test_set_model_missing_config_file_preserves_no_create_behaviour(self):
        """When config.json does not exist, set_model succeeds in memory and does not create config.json or lock files."""
        if os.path.exists(self.temp_config_path):
            os.remove(self.temp_config_path)
        lock_path = self.temp_config_path + ".lock"
        if os.path.exists(lock_path):
            os.remove(lock_path)

        initial_session = MagicMock(name="InitialSession")
        recalibrated_session = MagicMock(name="RecalibratedSession")
        web_server.ORCHESTRATOR_MODEL = "gemini-3.8-flash-high"
        web_server.framework.free_tier_only = True
        web_server.framework.gemini_subscription_linked = False
        web_server.global_chat_session = initial_session
        web_server._session_hydrated = True
        web_server.active_model_warning = "prior-warning"

        with (
            patch("web_server.create_new_session", return_value=recalibrated_session),
            patch("web_server.check_model_validity", return_value=None),
        ):
            res = web_server.set_model(
                {
                    "model": "gemini-4-argon-high",
                    "include_paid": True,
                    "gemini_subscription_linked": True,
                }
            )

        self.assertEqual(
            res,
            {"status": "success", "current_model": "gemini-4-argon-high", "warning": None},
        )
        self.assertEqual(web_server.ORCHESTRATOR_MODEL, "gemini-4-argon-high")
        self.assertFalse(web_server.framework.free_tier_only)
        self.assertTrue(web_server.framework.gemini_subscription_linked)
        self.assertIs(web_server.global_chat_session, recalibrated_session)
        self.assertFalse(web_server._session_hydrated)
        self.assertIsNone(web_server.active_model_warning)
        self.assertFalse(self._real_exists(self.temp_config_path))
        self.assertFalse(self._real_exists(lock_path))

    def test_set_model_concurrent_configuration_modification_preserves_unrelated_updates(self):
        """Concurrent atomic_update_json and set_model updates serialize under FileLock without losing unrelated fields."""
        initial_config = {
            "GEMINI_SUBSCRIPTION_LINKED": False,
            "ANTIGRAVITY_MODEL_PRO": "gemini-3.8-flash-high",
            "CONCURRENT_KEY": "initial",
        }
        self._write_temp_config_bytes(json.dumps(initial_config, indent=2).encode("utf-8"))

        web_server.ORCHESTRATOR_MODEL = "gemini-3.8-flash-high"
        web_server.framework.free_tier_only = True
        web_server.framework.gemini_subscription_linked = False
        web_server.global_chat_session = MagicMock(name="InitialSession")
        web_server._session_hydrated = True

        worker_in_lock = threading.Event()
        allow_worker_finish = threading.Event()
        set_model_done = threading.Event()
        errors = []
        results = {}

        def worker_update():
            try:
                def update_unrelated(cfg):
                    worker_in_lock.set()
                    if not allow_worker_finish.wait(timeout=5.0):
                        raise TimeoutError("Timed out waiting to finish worker update")
                    updated = dict(cfg)
                    updated["CONCURRENT_KEY"] = "updated_by_worker"
                    return updated

                self._real_atomic_update(self.temp_config_path, update_unrelated)
            except Exception as exc:
                errors.append(exc)

        def run_set_model():
            try:
                results["set_model"] = web_server.set_model(
                    {
                        "model": "gemini-4-argon-high",
                        "include_paid": True,
                        "gemini_subscription_linked": True,
                    }
                )
            except Exception as exc:
                errors.append(exc)
            finally:
                set_model_done.set()

        with (
            patch("web_server.create_new_session", return_value=MagicMock(name="RecalibratedSession")),
            patch("web_server.check_model_validity", return_value=None),
        ):
            t_worker = threading.Thread(target=worker_update)
            t_model = threading.Thread(target=run_set_model)
            t_worker.start()
            self.assertTrue(worker_in_lock.wait(timeout=3.0))
            t_model.start()

            # set_model must block on FileLock while worker holds it
            self.assertFalse(set_model_done.wait(timeout=0.15))
            allow_worker_finish.set()

            t_worker.join(timeout=5.0)
            t_model.join(timeout=5.0)
            self.assertFalse(t_worker.is_alive())
            self.assertFalse(t_model.is_alive())

        self.assertEqual(errors, [])
        self.assertEqual(results["set_model"]["status"], "success")
        persisted = json.loads(self._read_temp_config_bytes().decode("utf-8"))
        self.assertTrue(persisted["GEMINI_SUBSCRIPTION_LINKED"])
        self.assertEqual(persisted["CONCURRENT_KEY"], "updated_by_worker")
        self.assertEqual(persisted["ANTIGRAVITY_MODEL_PRO"], "gemini-3.8-flash-high")

        # Multi-threaded concurrent stress: distinct unrelated keys + set_model updates
        stress_errors = []

        def key_writer(worker_idx):
            try:
                for step in range(5):
                    def _set_key(cfg, idx=worker_idx, s=step):
                        nxt = dict(cfg)
                        nxt[f"WORKER_{idx}"] = s
                        return nxt

                    self._real_atomic_update(self.temp_config_path, _set_key)
            except Exception as exc:
                stress_errors.append(exc)

        def policy_toggler(toggle_idx):
            try:
                for step in range(5):
                    linked_val = bool((toggle_idx + step) % 2)
                    res = web_server.set_model(
                        {
                            "model": "gemini-4-argon-high",
                            "include_paid": True,
                            "gemini_subscription_linked": linked_val,
                        }
                    )
                    self.assertEqual(res["status"], "success")
            except Exception as exc:
                stress_errors.append(exc)

        with (
            patch("web_server.create_new_session", return_value=MagicMock(name="StressSession")),
            patch("web_server.check_model_validity", return_value=None),
        ):
            stress_threads = [
                threading.Thread(target=key_writer, args=(0,)),
                threading.Thread(target=key_writer, args=(1,)),
                threading.Thread(target=policy_toggler, args=(0,)),
                threading.Thread(target=policy_toggler, args=(1,)),
            ]
            for t in stress_threads:
                t.start()
            for t in stress_threads:
                t.join(timeout=10.0)
                self.assertFalse(t.is_alive())

        self.assertEqual(stress_errors, [])
        final_persisted = json.loads(self._read_temp_config_bytes().decode("utf-8"))
        self.assertEqual(final_persisted["WORKER_0"], 4)
        self.assertEqual(final_persisted["WORKER_1"], 4)
        self.assertEqual(final_persisted["CONCURRENT_KEY"], "updated_by_worker")
        self.assertEqual(final_persisted["ANTIGRAVITY_MODEL_PRO"], "gemini-3.8-flash-high")
        self.assertIsInstance(final_persisted["GEMINI_SUBSCRIPTION_LINKED"], bool)

    def test_set_model_does_not_recreate_config_deleted_before_lock_acquisition(self):
        """If config.json is deleted while set_model waits for FileLock, recheck skips update without recreating it."""
        initial_config = {
            "GEMINI_SUBSCRIPTION_LINKED": False,
            "ANTIGRAVITY_MODEL_PRO": "gemini-3.8-flash-high",
        }
        self._write_temp_config_bytes(json.dumps(initial_config, indent=2).encode("utf-8"))

        initial_session = MagicMock(name="InitialSession")
        recalibrated_session = MagicMock(name="RecalibratedSession")
        web_server.ORCHESTRATOR_MODEL = "gemini-3.8-flash-high"
        web_server.framework.free_tier_only = True
        web_server.framework.gemini_subscription_linked = False
        web_server.global_chat_session = initial_session
        web_server._session_hydrated = True
        web_server.active_model_warning = "prior-warning"

        waiting_for_lock = threading.Event()
        set_model_done = threading.Event()
        self._file_lock_enter_hook = waiting_for_lock.set

        outcomes = {}

        def run_set_model():
            try:
                outcomes["result"] = web_server.set_model(
                    {
                        "model": "gemini-4-argon-high",
                        "include_paid": True,
                        "gemini_subscription_linked": True,
                    }
                )
            except Exception as exc:
                outcomes["error"] = exc
            finally:
                set_model_done.set()

        with (
            patch("web_server.create_new_session", return_value=recalibrated_session),
            patch("web_server.check_model_validity", return_value=None),
        ):
            with atomic_io.FileLock(self.temp_config_path + ".lock"):
                t_model = threading.Thread(target=run_set_model)
                t_model.start()

                self.assertTrue(waiting_for_lock.wait(timeout=3.0))
                self.assertFalse(set_model_done.wait(timeout=0.15))

                os.remove(self.temp_config_path)
                self.assertFalse(self._real_exists(self.temp_config_path))

            t_model.join(timeout=5.0)
            self.assertFalse(t_model.is_alive())

        self.assertNotIn("error", outcomes)
        self.assertEqual(
            outcomes.get("result"),
            {"status": "success", "current_model": "gemini-4-argon-high", "warning": None},
        )
        self.assertFalse(self._real_exists(self.temp_config_path))
        config_files = [
            name for name in os.listdir(self.temp_dir.name)
            if name == "config.json" or name.startswith(".config.json.")
        ]
        self.assertEqual(config_files, [])
        self.assertEqual(web_server.ORCHESTRATOR_MODEL, "gemini-4-argon-high")
        self.assertFalse(web_server.framework.free_tier_only)
        self.assertTrue(web_server.framework.gemini_subscription_linked)
        self.assertIs(web_server.global_chat_session, recalibrated_session)
        self.assertFalse(web_server._session_hydrated)
        self.assertIsNone(web_server.active_model_warning)


class TestStage37ConsolidatedTrustPrecisionAndPersistence(unittest.TestCase):
    """Comprehensive tests for Stage 3.7 R1 (origin boundary), R2 (Decimal API), R3 (fail-closed basket & UI), R4 (chat/scout error propagation)."""

    def setUp(self):
        reset_persistence_service()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_context = os.path.join(self.temp_dir.name, "context")
        os.makedirs(self.temp_context, exist_ok=True)
        self._orig_tickers = list(fetch_stocks.TICKERS)
        self._orig_macro = list(fetch_stocks.MACRO_TICKERS)
        self._orig_all_tickers = list(fetch_stocks.ALL_TICKERS)
        self._orig_force_refresh = fetch_stocks.FORCE_REFRESH
        self._orig_state = fetch_stocks.get_global_state_snapshot()
        self._orig_model = web_server.ORCHESTRATOR_MODEL
        self._orig_session = web_server.global_chat_session

    def tearDown(self):
        reset_persistence_service()
        fetch_stocks.TICKERS = self._orig_tickers
        fetch_stocks.MACRO_TICKERS = self._orig_macro
        fetch_stocks.ALL_TICKERS = self._orig_all_tickers
        fetch_stocks.FORCE_REFRESH = self._orig_force_refresh
        fetch_stocks.publish_global_state_snapshot(self._orig_state)
        web_server.ORCHESTRATOR_MODEL = self._orig_model
        web_server.global_chat_session = self._orig_session
        self.temp_dir.cleanup()

    def test_r1_cors_allowlist_origin_guard_and_loopback_binding(self):
        """R1: Wildcard CORS absent, credentials disabled, trusted local origins accepted, untrusted origins rejected 403, and loopback 127.0.0.1 preserved."""
        from fastapi.testclient import TestClient

        cors_middlewares = [
            mw for mw in web_server.app.user_middleware
            if getattr(mw.cls, "__name__", "") == "CORSMiddleware"
        ]
        self.assertEqual(len(cors_middlewares), 1)
        cors_opts = cors_middlewares[0].kwargs
        self.assertNotIn("*", cors_opts["allow_origins"])
        self.assertFalse(cors_opts["allow_credentials"])
        self.assertEqual(
            set(cors_opts["allow_origins"]),
            set(fetch_stocks.TRUSTED_LOCAL_ORIGINS),
        )

        # Verify dynamic sidecar port support in get_trusted_local_origins()
        with patch.dict(os.environ, {"ANTIGRAVITY_SIDECAR_WEB_PORT": "8123"}):
            dynamic_origins = fetch_stocks.get_trusted_local_origins()
            self.assertIn("http://127.0.0.1:8123", dynamic_origins)
            self.assertIn("http://localhost:8123", dynamic_origins)
            self.assertIn("http://127.0.0.1:8000", dynamic_origins)
            self.assertIn("http://localhost:8000", dynamic_origins)

        client = TestClient(web_server.app)

        # Trusted local origin accepted cleanly
        for trusted in ("http://127.0.0.1:8000", "http://localhost:8000"):
            res = client.get("/api/tickers", headers={"Origin": trusted})
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.headers.get("access-control-allow-origin"), trusted)
            self.assertNotIn("access-control-allow-credentials", res.headers)

        # Untrusted cross-origin GET, POST, and OPTIONS preflight rejected fail-closed with 403
        for evil in (
            "http://evil.example.com",
            "https://attacker.local",
            "http://127.0.0.1.evil.com:8000",
            "http://localhost:9999",
        ):
            r_get = client.get("/api/tickers", headers={"Origin": evil})
            self.assertEqual(r_get.status_code, 403)
            self.assertIsNone(r_get.headers.get("access-control-allow-origin"))

            r_post = client.post("/api/basket", json={"portfolio": [], "unallocated_cash_eur": 0}, headers={"Origin": evil})
            self.assertEqual(r_post.status_code, 403)
            self.assertIsNone(r_post.headers.get("access-control-allow-origin"))

            r_opt = client.options(
                "/api/basket",
                headers={"Origin": evil, "Access-Control-Request-Method": "POST"},
            )
            self.assertEqual(r_opt.status_code, 403)
            self.assertIsNone(r_opt.headers.get("access-control-allow-origin"))

        # Loopback binding preserved in source entrypoints
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        for fname in ("web_server.py", "fetch_stocks.py"):
            with open(os.path.join(repo_root, "python", fname), "r", encoding="utf-8") as f:
                src = f.read()
            self.assertIn('uvicorn.run(app, host="127.0.0.1"', src)
            self.assertNotIn('host="0.0.0.0"', src)
            self.assertNotIn("host='0.0.0.0'", src)

    def test_r2_pydantic_models_and_basket_endpoints_preserve_decimal_and_reject_invalid(self):
        """R2: BasketItem/BasketSaveRequest and /api/basket round-trip exact Decimals and reject float, bool, NaN, Inf, negative, and excess scale."""
        from decimal import Decimal
        from fastapi.testclient import TestClient
        from pydantic import ValidationError

        # Pydantic models reject raw Python floats, booleans, non-finite, negative, and excessive scale
        with self.assertRaises((ValidationError, PersistenceValidationError)):
            web_server.BasketItem(ticker="NVDA", shares=12.345, wac="100.00")
        with self.assertRaises((ValidationError, PersistenceValidationError)):
            web_server.BasketItem(ticker="NVDA", shares=True, wac="100.00")
        with self.assertRaises((ValidationError, PersistenceValidationError)):
            web_server.BasketItem(ticker="NVDA", shares="12.34567", wac="100.00")
        with self.assertRaises((ValidationError, PersistenceValidationError)):
            web_server.BasketItem(ticker="NVDA", shares=1, wac=0)
        with self.assertRaises((ValidationError, PersistenceValidationError)):
            web_server.BasketSaveRequest(portfolio=[], unallocated_cash_eur=100.5)
        with self.assertRaises((ValidationError, PersistenceValidationError)):
            web_server.BasketSaveRequest(portfolio=[], unallocated_cash_eur="100.555")

        valid_req = web_server.BasketSaveRequest(
            portfolio=[web_server.BasketItem(ticker="NVDA", shares=Decimal("12.3456"), wac="123.4567")],
            unallocated_cash_eur=Decimal("1000.55"),
        )
        self.assertEqual(valid_req.portfolio[0].shares, Decimal("12.3456"))
        self.assertEqual(valid_req.unallocated_cash_eur, Decimal("1000.55"))

        configure_persistence(backend="json", base_dir=self.temp_dir.name)
        with open(os.path.join(self.temp_context, "ssot.json"), "w", encoding="utf-8") as f:
            json.dump({"mutable_state": {}}, f)

        client = TestClient(web_server.app)
        with (
            patch("web_server.check_invalid_tickers", return_value=[]),
            patch("fetch_stocks._load_ssot_tickers", return_value=["NVDA"]),
            patch("fetch_stocks._load_macro_tickers", return_value=[]),
        ):
            # Exact Decimal round-trip via POST /api/basket -> GET /api/basket
            res_post = client.post(
                "/api/basket",
                content='{"portfolio":[{"ticker":"NVDA","shares":12.3456,"wac":19.9999}],"unallocated_cash_eur":1000.55,"unallocated_cash_usd":1080.59}',
                headers={"Content-Type": "application/json", "Origin": "http://127.0.0.1:8000"},
            )
            self.assertEqual(res_post.status_code, 200)
            raw_post_body = res_post.text
            self.assertIn("12.3456", raw_post_body)
            self.assertIn("19.9999", raw_post_body)
            self.assertIn("1000.55", raw_post_body)

            res_get = client.get("/api/basket", headers={"Origin": "http://127.0.0.1:8000"})
            self.assertEqual(res_get.status_code, 200)
            parsed_get = json.loads(res_get.text, parse_float=Decimal)
            self.assertEqual(parsed_get["portfolio"][0]["shares"], Decimal("12.3456"))
            self.assertEqual(parsed_get["portfolio"][0]["wac"], Decimal("19.9999"))
            self.assertEqual(parsed_get["unallocated_cash_eur"], Decimal("1000.55"))

            # Reject NaN, Infinity, excessive scale, booleans, negative cash over HTTP
            for bad_body in (
                '{"portfolio":[{"ticker":"NVDA","shares":NaN,"wac":100}],"unallocated_cash_eur":100}',
                '{"portfolio":[{"ticker":"NVDA","shares":Infinity,"wac":100}],"unallocated_cash_eur":100}',
                '{"portfolio":[{"ticker":"NVDA","shares":1.12345,"wac":100}],"unallocated_cash_eur":100}',
                '{"portfolio":[{"ticker":"NVDA","shares":true,"wac":100}],"unallocated_cash_eur":100}',
                '{"portfolio":[],"unallocated_cash_eur":-1.00}',
                '{"portfolio":[],"unallocated_cash_eur":10.999}',
            ):
                bad_res = client.post(
                    "/api/basket",
                    content=bad_body,
                    headers={"Content-Type": "application/json", "Origin": "http://127.0.0.1:8000"},
                )
                self.assertEqual(bad_res.status_code, 422, f"Expected 422 for {bad_body}, got {bad_res.status_code}")

            # Active position with shares > 0 and wac == 0 must return 422 (never 503) with clear WAC message
            zero_wac_res = client.post(
                "/api/basket",
                content='{"portfolio":[{"ticker":"NVDA","shares":1,"wac":0}],"unallocated_cash_eur":0}',
                headers={"Content-Type": "application/json", "Origin": "http://127.0.0.1:8000"},
            )
            self.assertEqual(zero_wac_res.status_code, 422)
            zero_wac_detail = zero_wac_res.json().get("detail", "")
            self.assertIn("WAC", zero_wac_detail)
            self.assertIn("greater than 0", zero_wac_detail)

            # Zero-share position with wac == 0 remains allowed and omitted cleanly
            zero_share_res = client.post(
                "/api/basket",
                content='{"portfolio":[{"ticker":"NVDA","shares":0,"wac":0}],"unallocated_cash_eur":0}',
                headers={"Content-Type": "application/json", "Origin": "http://127.0.0.1:8000"},
            )
            self.assertEqual(zero_share_res.status_code, 200)

    def test_r3_basket_endpoints_fail_closed_with_503_and_redact_secrets(self):
        """R3: /api/basket and /api/get_basket return HTTP 503 error (never empty/zero fallback) and redact secrets from error logs."""
        from fastapi.testclient import TestClient

        mock_service = MagicMock()
        mock_service.get_portfolio_basket.side_effect = PersistenceIntegrityError(
            "Corrupt SSoT api_key=AIzaSyD-secret12345"
        )
        logged_messages = []

        client = TestClient(web_server.app)
        with (
            patch("web_server.get_persistence_service", return_value=mock_service),
            patch("fetch_stocks.get_persistence_service", return_value=mock_service),
            patch.object(web_server.framework, "log", side_effect=logged_messages.append),
        ):
            r_web = client.get("/api/basket", headers={"Origin": "http://127.0.0.1:8000"})
            self.assertEqual(r_web.status_code, 503)
            body_web = r_web.json()
            self.assertEqual(body_web["status"], "error")
            self.assertEqual(body_web["detail"], "Authoritative portfolio persistence unavailable")
            self.assertNotIn("portfolio", body_web)
            self.assertNotIn("unallocated_cash_eur", body_web)
            self.assertNotIn("AIzaSyD-secret12345", json.dumps(body_web))

            r_fs = client.get("/api/get_basket", headers={"Origin": "http://127.0.0.1:8000"})
            self.assertEqual(r_fs.status_code, 503)
            body_fs = r_fs.json()
            self.assertEqual(body_fs["status"], "error")
            self.assertEqual(body_fs["detail"], "Authoritative portfolio persistence unavailable")

        self.assertTrue(any("[REDACTED_SECRET]" in m for m in logged_messages))
        self.assertFalse(any("AIzaSyD-secret12345" in m for m in logged_messages))

    def test_r3_modern_ui_preserves_stale_portfolio_and_renders_degraded_state(self):
        """R3: modern_ui.js fetchPortfolio/renderPortfolio preserve lastValidPortfolioBasket and surface degraded/stale state on 503 errors."""
        node_bin = shutil.which("node")
        self.assertIsNotNone(node_bin, "node executable must be available")
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
        style: {},
        children: [],
        attributes: {},
        firstChild: null,
        parentNode: null,
        setAttribute(k, v) { this.attributes[k] = String(v); },
        getAttribute(k) { return this.attributes[k]; },
        appendChild(child) {
            child.parentNode = this;
            this.children.push(child);
            this.firstChild = this.children[0] || null;
            if (child.id) elementsById[child.id] = child;
            return child;
        },
        insertBefore(newChild, refChild) {
            newChild.parentNode = this;
            const idx = this.children.indexOf(refChild);
            if (idx === -1) this.children.push(newChild);
            else this.children.splice(idx, 0, newChild);
            this.firstChild = this.children[0] || null;
            if (newChild.id) elementsById[newChild.id] = newChild;
            return newChild;
        },
        remove() {
            if (this.id && elementsById[this.id] === this) delete elementsById[this.id];
            if (this.parentNode) {
                this.parentNode.children = this.parentNode.children.filter(c => c !== this);
                this.parentNode.firstChild = this.parentNode.children[0] || null;
            }
        },
        focus() {},
        querySelectorAll() { return []; },
        querySelector() { return null; },
        addEventListener() {},
        classList: { contains: () => false, add() {}, remove() {} },
    };
    if (id) elementsById[id] = el;
    return el;
}

const elementsById = {};
[
    'portfolio-content', 'portfolio-manager-body',
    'chat-window', 'launch-chat-btn', 'chat-close-btn', 'chat-input',
    'chat-messages', 'send-chat-btn', 'stop-chat-btn', 'chat-model-select',
    'new-chat-btn', 'history-chat-btn', 'model-selector'
].forEach(id => makeElement('div', id));

const basketResponses = [
    // 1. Initial failure before any valid load -> unavailable, not stale, body untouched
    { ok: false, status: 503, json: async () => ({ status: 'error', detail: 'Authoritative portfolio persistence unavailable' }) },
    // 2. Valid authoritative load -> renders NVDA and 1000.55 EUR
    { ok: true, status: 200, json: async () => ({ portfolio: [{ ticker: 'NVDA', shares: 12.3456, wac: 123.4567 }], unallocated_cash_eur: 1000.55, unallocated_cash_usd: 1080.59, eurusd_rate: 1.08 }) },
    // 3. Subsequent persistence outage -> preserves NVDA & 1000.55 EUR in DOM, marks degraded + stale
    { ok: false, status: 503, json: async () => ({ status: 'error', detail: 'Authoritative portfolio persistence unavailable' }) },
    // 4. Recovery authoritative load -> clears degraded banner and updates DOM
    { ok: true, status: 200, json: async () => ({ portfolio: [{ ticker: 'SPY', shares: 5, wac: 500.0 }], unallocated_cash_eur: 2500.00, unallocated_cash_usd: 2700.00, eurusd_rate: 1.08 }) },
];

const sandbox = {
    console, Date, Math, JSON, Number, Boolean, Object, Array, String, Promise, AbortController,
    EventSource: class MockEventSource { close() {} },
    setTimeout, clearTimeout,
    window: { addEventListener() {} },
    document: {
        body: makeElement('body'),
        activeElement: null,
        getElementById: (id) => elementsById[id] || null,
        createElement: (tag) => makeElement(tag),
        querySelector: () => null,
        querySelectorAll: () => [],
        addEventListener() {},
    },
    localStorage: { _s: {}, getItem: () => null, setItem() {}, removeItem() {} },
    marked: { parse: (s) => String(s) },
    fetch: async (url) => {
        if (url === '/api/basket') return basketResponses.shift();
        return { ok: true, status: 200, json: async () => ({}) };
    },
};

vm.runInNewContext(uiCode, sandbox);
const chatUI = sandbox.window.chatUI;
chatUI.init();

(async () => {
    const tbody = elementsById['portfolio-manager-body'];
    const section = elementsById['portfolio-content'];

    // 1. Initial failure: does not fabricate 0 cash/empty rows
    await sandbox.window.fetchPortfolio(true);
    assert.strictEqual(chatUI.portfolioDegraded, true);
    assert.strictEqual(chatUI.lastValidPortfolioBasket, null);
    assert.strictEqual(tbody.getAttribute('data-portfolio-state'), 'degraded');
    assert.strictEqual(tbody.getAttribute('data-portfolio-stale'), 'false');
    assert.strictEqual(tbody.innerHTML, '');
    assert(elementsById['portfolio-degraded-banner'].textContent.includes('PORTFOLIO UNAVAILABLE'));

    // 2. Authoritative load succeeds
    await sandbox.window.fetchPortfolio(true);
    assert.strictEqual(chatUI.portfolioDegraded, false);
    assert.strictEqual(tbody.getAttribute('data-portfolio-state'), 'authoritative');
    assert(tbody.innerHTML.includes('NVDA'));
    assert(tbody.innerHTML.includes('12.3456'));
    assert(tbody.innerHTML.includes('1000.55'));
    assert.strictEqual(elementsById['portfolio-degraded-banner'].style.display, 'none');

    const snapshotHtmlBeforeOutage = tbody.innerHTML;

    // 3. Subsequent 503 preserves lastValidPortfolioBasket & NVDA/1000.55 DOM rows
    await sandbox.window.fetchPortfolio(true);
    assert.strictEqual(chatUI.portfolioDegraded, true);
    assert.strictEqual(tbody.getAttribute('data-portfolio-state'), 'degraded');
    assert.strictEqual(tbody.getAttribute('data-portfolio-stale'), 'true');
    assert.strictEqual(section.getAttribute('data-portfolio-stale'), 'true');
    assert.strictEqual(tbody.innerHTML, snapshotHtmlBeforeOutage);
    assert(elementsById['portfolio-degraded-banner'].textContent.includes('STALE PORTFOLIO DATA'));

    // 4. Recovery restores authoritative state
    await sandbox.window.fetchPortfolio(true);
    assert.strictEqual(chatUI.portfolioDegraded, false);
    assert.strictEqual(tbody.getAttribute('data-portfolio-state'), 'authoritative');
    assert.strictEqual(tbody.getAttribute('data-portfolio-stale'), 'false');
    assert(tbody.innerHTML.includes('SPY'));
    assert.strictEqual(elementsById['portfolio-degraded-banner'].style.display, 'none');
})().catch((err) => {
    console.error(err);
    process.exit(1);
});
"""
        proc = subprocess.run([node_bin, "-e", script, ui_path], capture_output=True, text=True, timeout=15)
        self.assertEqual(proc.returncode, 0, f"Node test failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")

    def test_modern_ui_preserves_unsaved_draft_row_during_polling(self):
        """modern_ui.js fetchPortfolio(false) skips polling overwrite when a draft row is present, while fetchPortfolio(true) refreshes."""
        node_bin = shutil.which("node")
        self.assertIsNotNone(node_bin, "node executable must be available")
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
        innerHTML: '',
        textContent: '',
        style: {},
        children: [],
        attributes: {},
        firstChild: null,
        parentNode: null,
        setAttribute(k, v) { this.attributes[k] = String(v); },
        getAttribute(k) { return this.attributes[k]; },
        appendChild(child) {
            child.parentNode = this;
            this.children.push(child);
            this.firstChild = this.children[0] || null;
            if (child.id) elementsById[child.id] = child;
            return child;
        },
        insertBefore(newChild, refChild) { return this.appendChild(newChild); },
        remove() {},
        focus() {},
        querySelector(sel) {
            if (sel === 'tr.portfolio-item-row[data-draft="true"]' && this.innerHTML.includes('data-draft="true"')) {
                return makeElement('tr');
            }
            return null;
        },
        querySelectorAll() { return []; },
        addEventListener() {},
        classList: { contains: () => false, add() {}, remove() {} },
    };
    if (id) elementsById[id] = el;
    return el;
}

const elementsById = {};
[
    'portfolio-content', 'portfolio-manager-body',
    'chat-window', 'launch-chat-btn', 'chat-close-btn', 'chat-input',
    'chat-messages', 'send-chat-btn', 'stop-chat-btn', 'chat-model-select',
    'new-chat-btn', 'history-chat-btn', 'model-selector'
].forEach(id => makeElement('div', id));

let fetchCalls = 0;
const sandbox = {
    console, Date, Math, JSON, Number, Boolean, Object, Array, String, Promise, AbortController,
    EventSource: class MockEventSource { close() {} },
    setTimeout, clearTimeout,
    window: { addEventListener() {} },
    document: {
        body: makeElement('body'),
        activeElement: null,
        getElementById: (id) => elementsById[id] || null,
        createElement: (tag) => makeElement(tag),
        querySelector: () => null,
        querySelectorAll: () => [],
        addEventListener() {},
    },
    localStorage: { _s: {}, getItem: () => null, setItem() {}, removeItem() {} },
    marked: { parse: (s) => String(s) },
    fetch: async (url) => {
        if (url === '/api/basket') {
            fetchCalls += 1;
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    portfolio: [{ ticker: 'SPY', shares: 5, wac: 500.0 }],
                    unallocated_cash_eur: 1000.00,
                    unallocated_cash_usd: 1080.00,
                    eurusd_rate: 1.08,
                }),
            };
        }
        return { ok: true, status: 200, json: async () => ({}) };
    },
};

vm.runInNewContext(uiCode, sandbox);
const chatUI = sandbox.window.chatUI;
chatUI.init();

(async () => {
    const tbody = elementsById['portfolio-manager-body'];

    // 1. Authoritative initial load sets lastValidPortfolioBasket
    await sandbox.window.fetchPortfolio(true);
    assert.strictEqual(fetchCalls, 1);
    assert.strictEqual(chatUI.lastValidPortfolioBasket.portfolio.length, 1);
    assert.strictEqual(chatUI.lastValidPortfolioBasket.portfolio[0].ticker, 'SPY');

    // 2. Render unsaved draft row (NVDA with wac=0)
    const rendered = sandbox.window.renderPortfolio({
        portfolio: [
            { ticker: 'SPY', shares: 5, wac: 500.0 },
            { ticker: 'NVDA', shares: 1, wac: 0, _draft: true },
        ],
        unallocated_cash_eur: 1000.00,
        eurusd_rate: 1.08,
        _hasDraft: true,
    });
    assert.strictEqual(rendered, true);
    assert(tbody.innerHTML.includes('data-draft="true"'));
    assert(tbody.innerHTML.includes('placeholder="WAC > 0"'));
    // lastValidPortfolioBasket must NOT be polluted by the unsaved draft
    assert.strictEqual(chatUI.lastValidPortfolioBasket.portfolio.length, 1);

    // 3. Background polling fetchPortfolio(false) must NOT clobber draft row
    await sandbox.window.fetchPortfolio(false);
    assert.strictEqual(fetchCalls, 1);
    assert(tbody.innerHTML.includes('NVDA'));
    assert(tbody.innerHTML.includes('data-draft="true"'));

    // 4. Explicit fetchPortfolio(true) DOES refresh authoritative basket
    await sandbox.window.fetchPortfolio(true);
    assert.strictEqual(fetchCalls, 2);
    assert(!tbody.innerHTML.includes('data-draft="true"'));
    assert(!tbody.innerHTML.includes('NVDA'));
})().catch((err) => {
    console.error(err);
    process.exit(1);
});
"""
        proc = subprocess.run([node_bin, "-e", script, ui_path], capture_output=True, text=True, timeout=15)
        self.assertEqual(proc.returncode, 0, f"Node test failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_r4_chat_endpoint_reports_honest_failure_on_slice_and_brace_payload_sync_errors(self, mock_dispatch, mock_charts):
        """R4: Both slice-extracted and brace-extracted SSoT sync failures report failure without claiming SSoT Shadow State synchronized successfully."""
        valid_state = {"financial_state_valid": True, "local_storage_state": {}, "tickers": []}

        with (
            patch_global_state(valid_state),
            patch("tools.update_ssot", side_effect=PersistenceError("Disk rollback triggered")),
        ):
            # 1. Slice-extracted fenced JSON payload
            mock_dispatch.return_value = {
                "status": "completed",
                "response": 'Summary text.\n```json\n{"EXECUTION_PAYLOAD": {"portfolio_snapshot": []}}\n```',
                "turn_count": 1,
            }
            res_slice = web_server.chat_endpoint(web_server.ChatRequest(message="Update via fenced payload"))
            self.assertEqual(res_slice["status"], "success")
            self.assertNotIn("synchronized successfully", res_slice["response"])
            self.assertIn("SSoT Synchronization Failed", res_slice["response"])
            self.assertIn("synchronization failed. Authoritative state was not updated", res_slice["response"])

            # 2. Brace-extracted unfenced payload
            mock_dispatch.return_value = {
                "status": "completed",
                "response": 'Summary text. {"EXECUTION_PAYLOAD": {"portfolio_snapshot": []}}',
                "turn_count": 1,
            }
            res_brace = web_server.chat_endpoint(web_server.ChatRequest(message="Update via brace payload"))
            self.assertEqual(res_brace["status"], "success")
            self.assertNotIn("synchronized successfully", res_brace["response"])
            self.assertIn("SSoT Synchronization Failed", res_brace["response"])
            self.assertIn("synchronization failed. Authoritative state was not updated", res_brace["response"])

    def test_r4_scout_categories_fails_honestly_with_503_and_skips_background_reload(self):
        """R4: /api/scout_categories returns HTTP 503 error and does not trigger ticker reload when persistence fails."""
        from fastapi.testclient import TestClient

        mock_service = MagicMock()
        mock_service.save_scout_categories.side_effect = PersistenceError("Simulated scout categories write error")

        client = TestClient(web_server.app)
        with (
            patch("web_server.get_persistence_service", return_value=mock_service),
            patch("web_server.reload_scout_tickers_task") as mock_reload,
        ):
            res = client.post(
                "/api/scout_categories",
                json=["AI_INFRA", "DEFENSE"],
                headers={"Origin": "http://127.0.0.1:8000"},
            )
            self.assertEqual(res.status_code, 503)
            body = res.json()
            self.assertEqual(body["status"], "error")
            mock_reload.assert_not_called()

    def test_list_models_returns_dynamic_hydrogen_and_neon_models(self):
        """GET /api/list_models exposes Hydrogen and Neon models with provider='antigravity'."""
        mock_models = [
            {"name": "gemini-4-neon-high", "label": "Gemini 4 Neon (High)", "provider": "antigravity"},
            {"name": "gemini-4-hydrogen-high", "label": "Gemini 4 Hydrogen (High)", "provider": "antigravity"},
        ]
        with patch.object(web_server.framework.antigravity_runner, "list_available_models", return_value=mock_models):
            res = web_server.list_models_endpoint()

        self.assertEqual(res["status"], "success")
        names = [m["name"] for m in res["models"]]
        self.assertIn("gemini-4-neon-high", names)
        self.assertIn("gemini-4-hydrogen-high", names)
        self.assertTrue(all(m.get("provider") == "antigravity" for m in res["models"]))

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_endpoint_skips_ssot_update_on_halted_veto_payload(self, mock_dispatch, mock_charts):
        """CRITICAL_SYNC_ALERT / halt_active / ENH_12_STATE_ABSENCE_VETO payloads must NOT invoke tools.update_ssot."""
        valid_state = {"financial_state_valid": True, "local_storage_state": {}, "tickers": []}
        halted_payload = json.dumps({
            "EXECUTION_PAYLOAD": {
                "directive": "HOLD",
                "unallocated_cash_eur": 0.0,
                "unallocated_cash_usd": 0.0,
                "state_context": {
                    "status": "CRITICAL_SYNC_ALERT",
                    "error": "ENH_12_STATE_ABSENCE_VETO",
                },
                "portfolio_snapshot": [],
                "runtime_flags": {"data_gap": True, "halt_active": True},
                "forensic_intelligence": {
                    "active_flags": ["CRITICAL_SYNC_ALERT", "ENH_12_STATE_ABSENCE_VETO"]
                },
            }
        })

        with (
            patch_global_state(valid_state),
            patch("tools.update_ssot") as mock_update,
        ):
            # 1. Slice-extracted halted payload
            mock_dispatch.return_value = {
                "status": "completed",
                "response": f"REJECT\n```json\n{halted_payload}\n```",
                "turn_count": 1,
            }
            res_slice = web_server.chat_endpoint(web_server.ChatRequest(message="Analyze SPY"))
            self.assertEqual(res_slice["status"], "success")
            mock_update.assert_not_called()
            self.assertNotIn("synchronized successfully", res_slice["response"])
            self.assertIn("SSoT update skipped", res_slice["response"])

            # 2. Brace-extracted halted payload
            mock_dispatch.return_value = {
                "status": "completed",
                "response": f"REJECT {halted_payload}",
                "turn_count": 1,
            }
            res_brace = web_server.chat_endpoint(web_server.ChatRequest(message="Analyze SPY"))
            self.assertEqual(res_brace["status"], "success")
            mock_update.assert_not_called()
            self.assertNotIn("synchronized successfully", res_brace["response"])
            self.assertIn("SSoT update skipped", res_brace["response"])


if __name__ == "__main__":
    unittest.main()
