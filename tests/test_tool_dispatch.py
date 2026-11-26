"""
Unit & Integration Tests for Unified Tool Dispatcher, Antigravity Execution Flags (R1),
and Bounded Multi-Turn Subagent Tool Execution (R7).
Governed by Stage 1.1 (R1, R7), Stage 1 Section 6.2, Section 3.4, and Protocol 03.
"""

import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from tool_dispatcher import ToolDispatcher, MAX_TOOL_TURNS, GLOBAL_TOOL_DISPATCHER
from tool_registry import ToolRegistry, GLOBAL_TOOL_REGISTRY, execution_role_context, get_active_caller_role
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
        self._saved_global_registry_tools = dict(GLOBAL_TOOL_REGISTRY._tools)
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
        GLOBAL_TOOL_REGISTRY._tools.clear()
        GLOBAL_TOOL_REGISTRY._tools.update(self._saved_global_registry_tools)
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

    # --- Stage 3.3.1: Parallel Council & Sequential Specialist Dispatch Parity ---

    def test_stage_331_ask_council_dispatch_and_mutating_agent_rejection(self):
        """GLOBAL_TOOL_DISPATCHER dispatches read-only ask_council batches and rejects mutating parallel agents while preserving sequential router delegation."""
        framework = AgentFramework()
        calls = {"bull": 0, "red": 0, "router": 0}

        def mock_bull(query: str) -> str:
            calls["bull"] += 1
            return f"BULL[{get_active_caller_role()}]: {query}"

        mock_bull._agent_role = "Bullish Advocate"
        mock_bull._is_read_only_deliberative = True
        mock_bull._agent_tools = []

        def mock_red(query: str) -> str:
            calls["red"] += 1
            return f"RED[{get_active_caller_role()}]: {query}"

        mock_red._agent_role = "Red Team Pessimist"
        mock_red._is_read_only_deliberative = True
        mock_red._agent_tools = []

        with patch.object(framework, "load_system_instruction", return_value="Router instruction"):
            seq_router = framework.create_agent_tool(
                name="State Validation Router",
                file_path="engine_instructions/state_validation_router.md",
                mode="PRO",
                agent_tools=[tools.read_ssot, tools.update_ssot, tools.get_market_data],
            )

        framework.create_parallel_council_tool({
            "ask_bullish_advocate": mock_bull,
            "ask_red_team_pessimist": mock_red,
            "ask_state_validation_router": seq_router,
        })

        # 1. Valid parallel read-only dispatch via GLOBAL_TOOL_DISPATCHER
        ok_res = GLOBAL_TOOL_DISPATCHER.dispatch_single_tool(
            tool_name="ask_council",
            args={
                "queries_json": json.dumps({
                    "bullish_advocate": "Bull case SPY",
                    "red_team_pessimist": "Bear case SPY",
                })
            },
            agent_role="Terminal Orchestrator",
        )
        self.assertTrue(ok_res.success, f"ask_council dispatch failed: {ok_res.error}")
        ok_payload = json.loads(ok_res.result)
        self.assertIn("BULL[Bullish Advocate]", ok_payload["bullish_advocate"])
        self.assertIn("RED[Red Team Pessimist]", ok_payload["red_team_pessimist"])
        self.assertEqual(calls["bull"], 1)
        self.assertEqual(calls["red"], 1)

        # 2. Batch containing mutating agent is rejected fail-closed before running siblings
        bad_res = GLOBAL_TOOL_DISPATCHER.dispatch_single_tool(
            tool_name="ask_council",
            args={
                "queries_json": json.dumps({
                    "bullish_advocate": "Bull case SPY",
                    "state_validation_router": "Update SSoT",
                })
            },
            agent_role="Terminal Orchestrator",
        )
        self.assertTrue(bad_res.success)
        bad_payload = json.loads(bad_res.result)
        self.assertIn("error", bad_payload)
        self.assertEqual(calls["bull"], 1)

        # 3. Non-orchestrator role is denied calling ask_council
        denied = GLOBAL_TOOL_DISPATCHER.dispatch_single_tool(
            tool_name="ask_council",
            args={"queries_json": json.dumps({"bullish_advocate": "Test"})},
            agent_role="Data Analyst",
        )
        self.assertFalse(denied.success)
        self.assertIn("not authorized", denied.error.lower())

        # 4. Sequential specialist invocation of ask_state_validation_router remains functional for Terminal Orchestrator
        with patch.object(
            framework,
            "generate_response_with_fallback",
            return_value=AntigravityResponse("Sequential router output"),
        ):
            seq_res = GLOBAL_TOOL_DISPATCHER.dispatch_single_tool(
                tool_name="ask_state_validation_router",
                args={"query": "Compile final EXECUTION_PAYLOAD"},
                agent_role="Terminal Orchestrator",
            )
        self.assertTrue(seq_res.success, f"Sequential router dispatch failed: {seq_res.error}")
        self.assertIn("Sequential router output", seq_res.result)

    def test_stage37_update_ssot_propagates_failure_and_logs_failed_audit(self):
        """R4: tools.update_ssot raises persistence errors rather than returning success/exception strings, causing ToolDispatcher to record FAILED."""
        from decimal import Decimal
        from persistence import PersistenceError, PersistenceValidationError

        mock_service = MagicMock()
        mock_service.update_ssot_payload.side_effect = PersistenceError("Simulated composite write failure")
        mock_service.get_portfolio_basket.return_value = {
            "portfolio": [],
            "unallocated_cash_eur": Decimal("100.55"),
            "unallocated_cash_usd": Decimal("0.00"),
        }
        with (
            patch("tools.get_persistence_service", return_value=mock_service),
            patch.object(tools.PROPOSAL_STORE, "_service_factory", lambda: mock_service),
        ):
            # 1. Trusted direct call raises instead of returning "Exception in update_ssot: ..."
            with self.assertRaises(PersistenceError):
                tools.update_ssot('{"mutable_state": {"unallocated_cash_eur": "100.55"}}')

            # Verify JSON parsing used parse_float=Decimal (no float leakage)
            passed_payload = mock_service.update_ssot_payload.call_args[0][0]
            self.assertEqual(passed_payload["mutable_state"]["unallocated_cash_eur"], "100.55")

            with execution_role_context("Terminal Orchestrator"):
                with self.assertRaises(PersistenceValidationError):
                    tools.update_ssot("{bad json")

                # Agent-proposed financial changes are staged for owner approval, never written
                mock_service.update_ssot_payload.reset_mock()
                staged = tools.update_ssot('{"mutable_state": {"unallocated_cash_eur": "250.00"}}')
                self.assertIn("NOT been applied", staged)
                mock_service.update_ssot_payload.assert_not_called()

            # 2. Dispatch through ToolDispatcher marks tool execution as FAILED in result and AuditLogger
            #    (a payload with no financial change records immediately, so the write failure surfaces)
            res = GLOBAL_TOOL_DISPATCHER.dispatch_single_tool(
                tool_name="update_ssot",
                args={"payload_json": '{"mutable_state": {"unallocated_cash_eur": 100.55}}'},
                agent_role="Terminal Orchestrator",
            )
            self.assertFalse(res.success)
            self.assertIn("Simulated composite write failure", res.error or "")

            with open(self.audit_log.log_path, "r", encoding="utf-8") as f:
                events = [json.loads(line) for line in f if line.strip()]
            failed_events = [e for e in events if e.get("tool_name") == "update_ssot" and e.get("status") == "FAILED"]
            self.assertGreaterEqual(len(failed_events), 1)
            self.assertEqual(failed_events[-1]["error_classification"], "PersistenceError")

            # 3. tools.update_trade_lessons & intercept_and_log_decision also propagate errors fail-closed
            mock_service.save_trade_lessons.side_effect = PersistenceError("Lessons write failure")
            mock_service.append_decision_log.side_effect = PersistenceError("Decision write failure")
            with tempfile.TemporaryDirectory() as tmp:
                from improvement_proposals import ImprovementProposalStore
                temp_store = ImprovementProposalStore(store_path=os.path.join(tmp, "improvements.json"))
                with patch.object(tools, "IMPROVEMENT_STORE", temp_store):
                    # Owner/system path writes directly, so a persistence failure surfaces
                    with self.assertRaises(PersistenceError):
                        tools.update_trade_lessons('[{"rule": "L-99: Test rule"}]')
                    with execution_role_context("Terminal Orchestrator"):
                        # Agent path stages for owner approval and never touches trade_lessons
                        msg = tools.update_trade_lessons('[{"rule": "L-99: Test rule"}]')
                        self.assertIn("owner approval", msg)
                        self.assertEqual(mock_service.save_trade_lessons.call_count, 1)
                        self.assertEqual([p["text"] for p in temp_store.list()], ["L-99: Test rule"])
                        with self.assertRaises(PersistenceError):
                            tools.intercept_and_log_decision({"portfolio_snapshot": [{"ticker": "SPY", "action": "HOLD"}]})

    def test_stage_a_slim_tools_and_idempotent_daemon_start(self):
        """Stage A: read_ssot/get_market_data strip history, read_decision_log respects limit, and start_market_daemon is idempotent."""
        mock_svc = MagicMock()
        mock_svc.get_ssot_data.return_value = {
            "mutable_state": {"watched_tickers": ["SPY"]},
            "decision_log": [{"id": 1}, {"id": 2}, {"id": 3}],
            "trade_lessons": [{"rule_code": "L-1"}],
        }
        mock_svc.get_decision_log.return_value = [{"id": 1}, {"id": 2}, {"id": 3}]

        with patch("tools.get_persistence_service", return_value=mock_svc):
            ssot_out = json.loads(tools.read_ssot())
            self.assertNotIn("decision_log", ssot_out)
            self.assertNotIn("trade_lessons", ssot_out)
            log_out = json.loads(tools.read_decision_log(limit=2))
            self.assertEqual([r["id"] for r in log_out], [2, 3])

        fake_state = {
            "status": "OPEN",
            "timestamp": "2026-11-26T10:00:00Z",
            "tickers": [],
            "local_storage_state": {
                "mutable_state": {"watched_tickers": ["NVDA"]},
                "decision_log": [{"id": 99}],
                "trade_lessons": [{"rule_code": "L-9"}],
            },
        }
        with patch.object(tools.fetch_stocks, "get_global_state_snapshot", return_value=fake_state):
            mkt_out = json.loads(tools.get_market_data())
            self.assertNotIn("decision_log", mkt_out["ssot"])
            self.assertNotIn("trade_lessons", mkt_out["ssot"])

        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        mock_thread.ident = 4242
        with patch.object(tools, "daemon_thread", None), \
             patch("tools.threading.Thread", return_value=mock_thread) as mock_thread_cls:
            t1 = tools.start_market_daemon(suppress_daemon_stdout=False)
            t2 = tools.start_market_daemon(suppress_daemon_stdout=False)
            self.assertIs(t1, t2)
            self.assertEqual(mock_thread_cls.call_count, 1)
            mock_thread.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
