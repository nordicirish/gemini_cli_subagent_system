"""
Unit & Integration Tests for Unified Tool Dispatcher
Governed by Stage 1 Section 6.2, Section 3.4, and Protocol 03.
"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from tool_dispatcher import ToolDispatcher, MAX_TOOL_TURNS
from tool_registry import ToolRegistry, HUMAN_APPROVAL_TOKEN_RULES


class MockPartFactory:
    @staticmethod
    def from_function_response(name: str, response: dict):
        return SimpleNamespace(name=name, response=response, kind="function_response")


class TestToolDispatcher(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry()
        # Register test dummy tools
        self.registry.register(
            name="echo_tool",
            func=lambda text: f"echo: {text}",
            description="Echoes back text",
            allowed_roles={"Terminal Orchestrator", "Data Analyst"},
            parameters={"text": str},
        )
        self.registry.register(
            name="sensitive_tool",
            func=lambda data: "sensitive_success",
            description="Sensitive tool needing approval",
            requires_human_approval=True,
            allowed_roles={"Terminal Orchestrator"},
            parameters={"data": str},
        )
        self.dispatcher = ToolDispatcher(registry=self.registry, max_turns=5)

    def test_dispatch_single_tool_success(self):
        result = self.dispatcher.dispatch_single_tool(
            tool_name="echo_tool",
            args={"text": "hello world"},
            agent_role="Terminal Orchestrator",
        )
        self.assertTrue(result.success)
        self.assertEqual(result.result, "echo: hello world")
        self.assertIsNone(result.error)

    def test_dispatch_single_tool_unauthorized_role(self):
        result = self.dispatcher.dispatch_single_tool(
            tool_name="echo_tool",
            args={"text": "hello"},
            agent_role="Python Engineer",
        )
        self.assertFalse(result.success)
        self.assertIn("not authorized", result.error)

    def test_dispatch_single_tool_cancellation(self):
        result = self.dispatcher.dispatch_single_tool(
            tool_name="echo_tool",
            args={"text": "hello"},
            agent_role="Terminal Orchestrator",
            cancel_check=lambda: True,
        )
        self.assertFalse(result.success)
        self.assertIn("cancelled", result.error.lower())

    def test_multi_turn_session_bounds_loop_at_max_turns(self):
        """A session endlessly returning function_calls must halt at max_turns."""
        mock_session = MagicMock()
        # Always request echo_tool with a varying counter
        counter = {"val": 0}

        def mock_send(msg):
            counter["val"] += 1
            fc = SimpleNamespace(name="echo_tool", args={"text": f"turn_{counter['val']}"})
            return SimpleNamespace(
                text=f"Turn response {counter['val']}",
                function_calls=[fc],
            )

        mock_session.send_message.side_effect = mock_send

        res = self.dispatcher.execute_multi_turn_session(
            session=mock_session,
            initial_message="start",
            part_factory=MockPartFactory,
            agent_role="Terminal Orchestrator",
        )

        # The loop must have terminated at max_turns (5) without hanging
        self.assertLessEqual(res["turn_count"], self.dispatcher.max_turns + 1)
        self.assertEqual(counter["val"], 5)

    def test_cycle_detection_breaks_repeated_calls(self):
        """Repeated identical calls with identical arguments must trigger cycle guard."""
        mock_session = MagicMock()

        def mock_send(msg):
            # Identical call every time
            fc = SimpleNamespace(name="echo_tool", args={"text": "static_argument"})
            return SimpleNamespace(
                text="Repeating turn",
                function_calls=[fc],
            )

        mock_session.send_message.side_effect = mock_send

        res = self.dispatcher.execute_multi_turn_session(
            session=mock_session,
            initial_message="start",
            part_factory=MockPartFactory,
            agent_role="Terminal Orchestrator",
        )

        # Cycle guard must break before max_turns
        self.assertLess(res["turn_count"], self.dispatcher.max_turns)


if __name__ == "__main__":
    unittest.main()
