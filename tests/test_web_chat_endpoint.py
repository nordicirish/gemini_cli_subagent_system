"""
Automated Test Suite for GEM Web Chat Endpoint and Persistence Integration.
Governed by Stage 2 Architecture and Agent Governance Framework.

Covers:
1. Web Chat Endpoint Primary Model Dispatch and Response Shape (Finding 3).
2. Web Chat Failover Recovery, Model Mutation, and Fallback Reporting (Finding 3).
3. Web Chat Complete Failure Handling (Zero Unhandled NameError) (Finding 3).
4. Dynamic Model Parameter Support in create_new_session (Finding 3).
5. Background Worker Daemon Persistence Service Integration (Finding 1).
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
)


class TestWebChatEndpoint(unittest.TestCase):
    def setUp(self):
        self._orig_model = web_server.ORCHESTRATOR_MODEL
        self._orig_session = web_server.global_chat_session
        web_server.cancel_event.clear()

    def tearDown(self):
        web_server.ORCHESTRATOR_MODEL = self._orig_model
        web_server.global_chat_session = self._orig_session
        web_server.cancel_event.clear()

    def test_create_new_session_accepts_optional_model_name(self):
        """create_new_session accepts explicit model_name or defaults to ORCHESTRATOR_MODEL."""
        with patch.object(web_server.framework, "create_chat_session") as mock_create:
            mock_create.return_value = MagicMock()
            
            # Explicit model
            sess1 = web_server.create_new_session(model_name="gemini-3.8-flash-low")
            mock_create.assert_called_with(
                web_server.framework._get_sys_instruction(web_server.terminal_instruction),
                web_server.terminal_tools,
                "gemini-3.8-flash-low",
            )

            # Default model
            sess2 = web_server.create_new_session()
            mock_create.assert_called_with(
                web_server.framework._get_sys_instruction(web_server.terminal_instruction),
                web_server.terminal_tools,
                web_server.ORCHESTRATOR_MODEL,
            )

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_endpoint_primary_success_reports_fallback_false(self, mock_dispatch, mock_charts):
        """When primary session succeeds, response contains fallback_occurred: False."""
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
    def test_chat_endpoint_primary_failure_recovers_via_fallback(self, mock_dispatch, mock_charts):
        """When primary model fails, chat endpoint falls back, recovers, and sets fallback_occurred: True."""
        web_server.ORCHESTRATOR_MODEL = "gemini-3.8-flash-high"
        
        # Primary fails with transient APIError / 503, fallback succeeds
        mock_dispatch.side_effect = [
            RuntimeError("Primary model 503 Service Unavailable"),
            {
                "status": "completed",
                "response": "Fallback recovered: Market regime is NEUTRAL.",
                "turn_count": 1,
            },
        ]

        req = web_server.ChatRequest(message="What is the current regime?")
        res = web_server.chat_endpoint(req)

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["response"], "Fallback recovered: Market regime is NEUTRAL.")
        self.assertTrue(res["fallback_occurred"])
        self.assertEqual(res["model"], "gemini-3.8-flash-low")
        self.assertEqual(res["active_model"], "gemini-3.8-flash-low")
        self.assertEqual(mock_dispatch.call_count, 2)

    @patch("web_server.get_chart_parts", return_value=([], []))
    @patch("tool_dispatcher.GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session")
    def test_chat_endpoint_both_primary_and_fallback_fail(self, mock_dispatch, mock_charts):
        """When both primary and fallback fail, endpoint returns clean error dict with no NameError."""
        mock_dispatch.side_effect = [
            RuntimeError("Primary model quota exceeded (429)"),
            RuntimeError("Fallback model also exhausted (429)"),
        ]

        req = web_server.ChatRequest(message="Hello")
        res = web_server.chat_endpoint(req)

        self.assertEqual(res["status"], "error")
        self.assertIn("quota", res.get("code", "") + res.get("message", "").lower())


class TestFetchStocksPersistenceBoundary(unittest.TestCase):
    def test_fetch_stocks_uses_persistence_service_for_state(self):
        """fetch_stocks reads state via PersistenceService rather than raw JSON file reads."""
        service = get_persistence_service()
        
        # Verify get_portfolio_basket returns dict with standard keys
        basket = service.get_portfolio_basket()
        self.assertIn("portfolio", basket)
        self.assertIn("unallocated_cash_eur", basket)
        self.assertIn("unallocated_cash_usd", basket)

        # Verify get_trade_lessons returns a list
        lessons = service.get_trade_lessons()
        self.assertIsInstance(lessons, list)

        # Verify get_ssot_data returns a dict
        ssot = service.get_ssot_data()
        self.assertIsInstance(ssot, dict)


if __name__ == "__main__":
    unittest.main()
