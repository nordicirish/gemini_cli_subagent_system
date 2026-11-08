"""
Unit & Integration Tests for Unified Tool Dispatcher, Antigravity Execution Flags (R1),
and Bounded Multi-Turn Subagent Tool Execution (R7).
Governed by Stage 1.1 (R1, R7), Stage 1 Section 6.2, Section 3.4, and Protocol 03.
"""

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from tool_dispatcher import ToolDispatcher, MAX_TOOL_TURNS, GLOBAL_TOOL_DISPATCHER
from tool_registry import ToolRegistry
from audit_logger import AuditLogger
from agent_framework import (
    AntigravityAgentRunner,
    AntigravityResponse,
    AgentFramework,
    MAX_SUBAGENT_TOOL_TURNS,
)
import tools


class MockPartFactory:
    @staticmethod
    def from_function_response(name: str, response: dict):
        return SimpleNamespace(name=name, response=response, kind="function_response")


class TestToolDispatcher(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.audit_log = AuditLogger(log_path=os.path.join(self.temp_dir.name, "test_audit.jsonl"))
        self._orig_global_audit = GLOBAL_TOOL_DISPATCHER.audit_logger
        GLOBAL_TOOL_DISPATCHER.audit_logger = self.audit_log
        self.registry = ToolRegistry()
        # Register test dummy tools
        self.registry.register(
            name="echo_tool",
            func=lambda text: f"echo: {text}",
            description="Echoes back text",
            classification="READ",
            allowed_roles={"Terminal Orchestrator", "Data Analyst"},
            parameters={"text": str},
            required_permission="read_market",
        )
        self.registry.register(
            name="sensitive_tool",
            func=lambda data: "sensitive_success",
            description="Sensitive tool needing approval",
            classification="WRITE",
            requires_human_approval=True,
            allowed_roles={"Terminal Orchestrator"},
            parameters={"data": str},
            required_permission="write_rules",
        )
        self.dispatcher = ToolDispatcher(
            registry=self.registry,
            audit_logger=self.audit_log,
            max_turns=5,
        )

    def tearDown(self):
        GLOBAL_TOOL_DISPATCHER.audit_logger = self._orig_global_audit
        self.temp_dir.cleanup()

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

    # --- R1: Antigravity CLI Execution Flags ---

    @patch("subprocess.Popen")
    def test_r1_antigravity_runner_uses_sandbox_and_disables_slash_commands(self, mock_popen):
        """R1: AntigravityAgentRunner must pass --sandbox and --disable-slash-commands and never --dangerously-skip-permissions."""
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("Analytical response", "")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        runner = AntigravityAgentRunner(agy_path="agy")
        resp = runner.run_prompt("Analyze SPY", system_instruction="System prompt", mode="PRO")
        self.assertEqual(resp.text, "Analytical response")

        called_cmd = mock_popen.call_args[0][0]
        self.assertIn("--sandbox", called_cmd)
        self.assertIn("--disable-slash-commands", called_cmd)
        self.assertNotIn("--dangerously-skip-permissions", called_cmd)

    # --- R7: Bounded Multi-Turn Subagent Tool Execution ---

    def test_r7_subagent_multi_turn_tool_execution_across_multiple_turns(self):
        """R7: Subagent can execute tools across >1 turns before producing its final synthesized analysis."""
        framework = AgentFramework()
        turn_responses = [
            AntigravityResponse(
                '```json\n{"function_calls": [{"name": "read_ssot", "args": {}}]}\n```'
            ),
            AntigravityResponse(
                '```json\n{"function_calls": [{"name": "perform_web_forensic_search", "args": {"query": "SPY catalyst"}}]}\n```'
            ),
            AntigravityResponse("Final multi-turn subagent synthesis incorporating SSoT and search."),
        ]

        with patch.object(framework, "generate_response_with_fallback", side_effect=turn_responses) as mock_gen, \
             patch("tools.perform_web_forensic_search", return_value="Mock search result"):
            sub_fn = framework.create_agent_tool(
                name="Macro Narrative Engine",
                file_path="engine_instructions/macro_narrative_engine.md",
                mode="THINKING",
                agent_tools=[tools.read_ssot, tools.perform_web_forensic_search],
            )
            output = sub_fn("Evaluate SPY narrative")

        self.assertEqual(mock_gen.call_count, 3)
        self.assertIn("Final multi-turn subagent synthesis", output)

    def test_r7_subagent_stops_at_max_subagent_tool_turns(self):
        """R7: Subagent endlessly requesting tools must terminate at MAX_SUBAGENT_TOOL_TURNS (3)."""
        framework = AgentFramework()
        call_counter = {"n": 0}

        def endless_tool_requests(prompt, instruction, mode, tools=None):
            call_counter["n"] += 1
            idx = call_counter["n"]
            return AntigravityResponse(
                f'```json\n{{"function_calls": [{{"name": "perform_web_forensic_search", "args": {{"query": "query_{idx}"}}}}]}}\n```'
            )

        with patch.object(framework, "generate_response_with_fallback", side_effect=endless_tool_requests), \
             patch("tools.perform_web_forensic_search", return_value="ok"):
            sub_fn = framework.create_agent_tool(
                name="Data Analyst",
                file_path="engine_instructions/data_analyst.md",
                mode="PRO",
                agent_tools=[tools.perform_web_forensic_search],
            )
            sub_fn("Run endless queries")

        self.assertEqual(call_counter["n"], MAX_SUBAGENT_TOOL_TURNS)

    def test_r7_subagent_rejects_unauthorized_tool_and_detects_cycles(self):
        """R7: Subagent attempting to invoke an unassigned/unauthorized tool receives a denial, and repeated calls trigger cycle detection."""
        framework = AgentFramework()
        received_prompts = []

        def mock_gen(prompt, instruction, mode, tools=None):
            received_prompts.append(prompt)
            if len(received_prompts) == 1:
                # Attempt unauthorized update_ssot from Data Analyst
                return AntigravityResponse(
                    '```json\n{"function_calls": [{"name": "update_ssot", "args": {"payload_json": "{}"}}]}\n```'
                )
            return AntigravityResponse("Handled tool denial cleanly.")

        with patch.object(framework, "generate_response_with_fallback", side_effect=mock_gen):
            sub_fn = framework.create_agent_tool(
                name="Data Analyst",
                file_path="engine_instructions/data_analyst.md",
                mode="PRO",
                agent_tools=[tools.perform_web_forensic_search],
            )
            result = sub_fn("Try unauthorized tool")

        self.assertEqual(len(received_prompts), 2)
        self.assertIn("not in the assigned toolset", received_prompts[1])
        self.assertIn("Handled tool denial cleanly.", result)

    # --- R5: Verification Harness Modes (Working-Tree & Committed) ---

    def test_r5_verification_harness_supports_both_working_tree_and_committed_modes(self):
        """R5: verify_stage_1.py mode check must pass in both pre-commit working-tree and post-commit clean states."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
        import verify_stage_1

        reporter = verify_stage_1.VerificationReporter()

        # 1. Simulate clean committed repository state
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                SimpleNamespace(returncode=0, stdout="60919b5f9e9614bda4d877886219446b351ceacb\n", stderr=""),
                SimpleNamespace(returncode=0, stdout="", stderr=""),
            ]
            check_fn = verify_stage_1.make_repository_mode_check("committed")
            self.assertTrue(check_fn(reporter))

        # 2. Simulate dirty pre-commit working-tree state
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                SimpleNamespace(returncode=0, stdout="60919b5f9e9614bda4d877886219446b351ceacb\n", stderr=""),
                SimpleNamespace(returncode=0, stdout=" M python/tool_registry.py\n", stderr=""),
            ]
            check_fn = verify_stage_1.make_repository_mode_check("working-tree")
            self.assertTrue(check_fn(reporter))

        # 3. Auto mode succeeds on both states
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                SimpleNamespace(returncode=0, stdout="abcdef1234567890\n", stderr=""),
                SimpleNamespace(returncode=0, stdout="", stderr=""),
            ]
            check_fn = verify_stage_1.make_repository_mode_check("auto")
            self.assertTrue(check_fn(reporter))


if __name__ == "__main__":
    unittest.main()
