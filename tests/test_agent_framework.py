"""
Unit & Security Tests for Parallel Council Execution, Read-Only Role Allowlisting,
Request Validation, Bounded Concurrency, Worker Isolation, Cancellation, and Ordering.
Governed by Stage 3.3.1, Protocol 03 (Security), Protocol 05 (Testing), and Protocol 06 (Financial Boundaries).
"""

import functools
import inspect
import json
import os
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import agent_framework
from agent_framework import (
    APPROVED_PARALLEL_COUNCIL_ROLES,
    APPROVED_PARALLEL_COUNCIL_TOOLS,
    AgentFramework,
    AntigravityResponse,
    MAX_PARALLEL_COUNCIL_WORKERS,
)
from tool_registry import GLOBAL_TOOL_REGISTRY, get_active_caller_role
import tools
import web_server


def _approved_role_to_key(role_name: str) -> str:
    return role_name.lower().replace(" ", "_")


class TestParallelCouncilOrchestration(unittest.TestCase):
    def setUp(self):
        self._saved_registry_tools = dict(GLOBAL_TOOL_REGISTRY._tools)
        self.framework = AgentFramework()
        self.executed_log = []
        self._log_lock = threading.Lock()

    def tearDown(self):
        GLOBAL_TOOL_REGISTRY._tools.clear()
        GLOBAL_TOOL_REGISTRY._tools.update(self._saved_registry_tools)

    def _make_readonly_agents_dict(self):
        agents = {}
        for role in sorted(APPROVED_PARALLEL_COUNCIL_ROLES):
            short_key = _approved_role_to_key(role)
            tool_key = f"ask_{short_key}"

            def make_fn(r=role, k=short_key):
                def _agent_fn(query: str) -> str:
                    caller_role = get_active_caller_role()
                    with self._log_lock:
                        self.executed_log.append((k, caller_role, query))
                    return f"{r} [{caller_role}]: {query}"

                _agent_fn.__name__ = f"ask_{k}"
                _agent_fn._agent_role = r
                _agent_fn._agent_tools = [tools.perform_web_forensic_search]
                _agent_fn._is_read_only_deliberative = True
                return _agent_fn

            agents[tool_key] = make_fn()
        return agents

    def test_valid_parallel_readonly_dispatch_and_role_context(self):
        """Approved read-only deliberative agents execute in parallel under their bound roles."""
        agents_dict = self._make_readonly_agents_dict()
        ask_council = self.framework.create_parallel_council_tool(agents_dict)

        payload = json.dumps({
            "bullish_advocate": "Evaluate NVDA breakout",
            "ask_red_team_pessimist": "Identify NVDA downside risks",
            "gex_engine": "Summarize gamma posture",
        })
        raw_out = ask_council(payload)
        parsed = json.loads(raw_out)

        self.assertEqual(
            list(parsed.keys()),
            ["bullish_advocate", "ask_red_team_pessimist", "gex_engine"],
        )
        self.assertIn("Bullish Advocate [Bullish Advocate]: Evaluate NVDA breakout", parsed["bullish_advocate"])
        self.assertIn(
            "Red Team Pessimist [Red Team Pessimist]: Identify NVDA downside risks",
            parsed["ask_red_team_pessimist"],
        )
        self.assertIn("GEX Engine [GEX Engine]: Summarize gamma posture", parsed["gex_engine"])

    def test_fenced_json_request_accepted(self):
        """Outer ```json markdown fences in ask_council input are parsed strictly without regex escaping hacks."""
        agents_dict = self._make_readonly_agents_dict()
        ask_council = self.framework.create_parallel_council_tool(agents_dict)

        fenced = '```json\n{"macro_sentinel": "Check VIX regime"}\n```'
        parsed = json.loads(ask_council(fenced))
        self.assertIn("Macro Sentinel [Macro Sentinel]: Check VIX regime", parsed["macro_sentinel"])

    def test_shared_ssot_base_removed_from_parallel_dispatch(self):
        """execute_ephemeral_batch no longer accepts shared_ssot_base and ask_council never builds _get_sys_instruction."""
        sig = inspect.signature(AgentFramework.execute_ephemeral_batch)
        self.assertNotIn("shared_ssot_base", sig.parameters)
        self.assertEqual(list(sig.parameters.keys()), ["self", "parallel_tasks"])

        agents_dict = self._make_readonly_agents_dict()
        ask_council = self.framework.create_parallel_council_tool(agents_dict)

        with patch.object(
            self.framework,
            "_get_sys_instruction",
            side_effect=AssertionError("_get_sys_instruction must not be called in parallel dispatch"),
        ):
            res = json.loads(ask_council(json.dumps({"neutral_structuralist": "Check VWAP structure"})))

        self.assertIn("Neutral Structuralist", res["neutral_structuralist"])

    def test_rejects_mutating_agents_in_ask_council_without_executing_siblings(self):
        """Including any mutating/state-routing agent fails the entire ask_council batch before execution."""
        agents_dict = self._make_readonly_agents_dict()
        # Even if caller attempts to register mutating agents into create_parallel_council_tool,
        # they must be filtered out and excluded.
        mutating_targets = {}
        for mut_name, role in [
            ("ask_state_validation_router", "State Validation Router"),
            ("ask_context_engine", "Context Engine"),
            ("ask_post_trade_review", "Post-Trade Review Engine"),
            ("ask_post_trade_review_engine", "Post-Trade Review Engine"),
            ("ask_review_engine", "Review Engine"),
        ]:
            fn = lambda q, r=role: f"MUTATED {r}"
            fn._agent_role = role
            mutating_targets[mut_name] = fn

        agents_dict.update(mutating_targets)
        ask_council = self.framework.create_parallel_council_tool(agents_dict)

        for bad_name in [
            "state_validation_router",
            "ask_state_validation_router",
            "context_engine",
            "ask_context_engine",
            "post_trade_review",
            "post_trade_review_engine",
            "review_engine",
            "terminal_orchestrator",
            "update_ssot",
        ]:
            self.executed_log.clear()
            req = json.dumps({
                "bullish_advocate": "Assess SPY",
                bad_name: "Mutate or write state",
            })
            parsed = json.loads(ask_council(req))
            self.assertIn("error", parsed, f"Expected fail-closed error for forbidden agent '{bad_name}'")
            self.assertEqual(
                self.executed_log,
                [],
                f"No sibling tasks should execute when '{bad_name}' is rejected",
            )

    def test_rejects_mutating_tools_or_roles_in_execute_ephemeral_batch(self):
        """execute_ephemeral_batch rejects forbidden roles or approved role names armed with mutating tools."""
        # 1. Direct forbidden agent key
        with self.assertRaises(PermissionError):
            self.framework.execute_ephemeral_batch({
                "state_validation_router": lambda: "should not run",
            })

        # 2. Approved agent name manually constructed with a mutating tool (update_ssot / update_rules)
        with patch.object(self.framework, "load_system_instruction", return_value="Dummy instruction"):
            compromised_agent = self.framework.create_agent_tool(
                name="Bullish Advocate",
                file_path="engine_instructions/bullish_gem.md",
                mode="THINKING",
                agent_tools=[tools.read_ssot, tools.update_ssot],
            )

        executed = {"ran": False}

        def safe_sibling():
            executed["ran"] = True
            return "ok"

        with patch.object(
            self.framework,
            "generate_response_with_fallback",
            side_effect=AssertionError("Compromised agent must never execute"),
        ):
            with self.assertRaises(PermissionError):
                self.framework.execute_ephemeral_batch({
                    "macro_sentinel": safe_sibling,
                    "bullish_advocate": functools.partial(compromised_agent, "Mutate state"),
                })
        self.assertFalse(executed["ran"])

        # Same check via create_parallel_council_tool: compromised agent must be excluded even under approved key
        ask_council = self.framework.create_parallel_council_tool({
            "ask_macro_sentinel": lambda q: "macro ok",
            "ask_bullish_advocate": compromised_agent,
        })
        res = json.loads(ask_council(json.dumps({"bullish_advocate": "Attempt run"})))
        self.assertIn("error", res)

    def test_rejects_unknown_agents_fail_closed(self):
        """Unknown or unapproved agent names reject the batch fail-closed with zero task execution."""
        agents_dict = self._make_readonly_agents_dict()
        ask_council = self.framework.create_parallel_council_tool(agents_dict)

        parsed = json.loads(ask_council(json.dumps({
            "bullish_advocate": "Assess SPY",
            "nonexistent_agent": "Hello",
        })))
        self.assertIn("error", parsed)
        self.assertIn("nonexistent_agent", parsed["error"])
        self.assertEqual(self.executed_log, [])

        with self.assertRaises(PermissionError):
            self.framework.execute_ephemeral_batch({"unknown_agent": lambda: "x"})

    def test_rejects_malformed_requests_and_duplicate_keys(self):
        """Malformed JSON, wrong containers, bad queries, bad identifiers, and duplicate keys fail closed."""
        agents_dict = self._make_readonly_agents_dict()
        ask_council = self.framework.create_parallel_council_tool(agents_dict)

        malformed_payloads = [
            "",
            "   ",
            "{not valid json",
            "[]",
            '["bullish_advocate"]',
            "{}",
            "123",
            '"bullish_advocate"',
            "null",
            json.dumps({"bullish_advocate": ""}),
            json.dumps({"bullish_advocate": "   "}),
            json.dumps({"bullish_advocate": 42}),
            json.dumps({"bullish_advocate": {"nested": "dict"}}),
            json.dumps({"Bullish_Advocate": "Uppercase key"}),
            json.dumps({"bullish advocate": "Space in key"}),
            json.dumps({"bullish-advocate": "Hyphen in key"}),
            json.dumps({"ask_": "Empty suffix"}),
            # Duplicate JSON key (exact)
            '{"bullish_advocate": "q1", "bullish_advocate": "q2"}',
            # Duplicate normalized key (bare + ask_ prefix)
            json.dumps({"bullish_advocate": "q1", "ask_bullish_advocate": "q2"}),
        ]

        for payload in malformed_payloads:
            self.executed_log.clear()
            parsed = json.loads(ask_council(payload))
            self.assertIn("error", parsed, f"Payload should have been rejected: {payload!r}")
            self.assertEqual(self.executed_log, [], f"Zero tasks must run for invalid payload: {payload!r}")

        # Also validate direct execute_ephemeral_batch container & key validation
        with self.assertRaises(ValueError):
            self.framework.execute_ephemeral_batch({})
        with self.assertRaises(ValueError):
            self.framework.execute_ephemeral_batch(["bullish_advocate"])  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            self.framework.execute_ephemeral_batch({
                "bullish_advocate": lambda: "1",
                "ask_bullish_advocate": lambda: "2",
            })

    def test_web_server_council_allowlist_excludes_mutating_agents(self):
        """web_server.council_members exposes strictly the 15 read-only deliberative agents."""
        self.assertEqual(set(web_server.council_members.keys()), APPROVED_PARALLEL_COUNCIL_TOOLS)
        self.assertEqual(len(web_server.council_members), 15)

        for forbidden_tool in (
            "ask_state_validation_router",
            "ask_context_engine",
            "ask_post_trade_review",
            "ask_post_trade_review_engine",
            "ask_review_engine",
        ):
            self.assertNotIn(forbidden_tool, web_server.council_members)

        # Sequential specialist tools remain intact on terminal_tools
        terminal_tool_names = {getattr(t, "__name__", str(t)) for t in web_server.terminal_tools}
        self.assertIn("ask_state_validation_router", terminal_tool_names)
        self.assertIn("ask_context_engine", terminal_tool_names)
        self.assertIn("ask_post-trade_review_engine", terminal_tool_names)
        self.assertIn("ask_council", terminal_tool_names)

    def test_bounded_concurrency_respects_max_workers(self):
        """Executing > MAX_PARALLEL_COUNCIL_WORKERS tasks never exceeds the 8-worker concurrency cap."""
        active_lock = threading.Lock()
        state = {"active": 0, "peak": 0}

        tasks = {}
        for role in sorted(APPROVED_PARALLEL_COUNCIL_ROLES):  # 15 approved roles (> 8)
            short_key = _approved_role_to_key(role)

            def make_worker(k=short_key):
                def _work():
                    with active_lock:
                        state["active"] += 1
                        if state["active"] > state["peak"]:
                            state["peak"] = state["active"]
                    try:
                        time.sleep(0.03)
                        return f"done:{k}"
                    finally:
                        with active_lock:
                            state["active"] -= 1

                return _work

            tasks[short_key] = make_worker()

        res = self.framework.execute_ephemeral_batch(tasks)
        self.assertEqual(len(res), 15)
        self.assertLessEqual(state["peak"], MAX_PARALLEL_COUNCIL_WORKERS)
        self.assertGreaterEqual(state["peak"], 2)

    def test_worker_failure_isolation_preserves_siblings(self):
        """An exception raised in one worker returns an isolated error string without aborting siblings."""
        agents_dict = self._make_readonly_agents_dict()

        def failing_macro(query: str) -> str:
            raise RuntimeError("Simulated provider timeout")

        failing_macro._agent_role = "Macro Sentinel"
        agents_dict["ask_macro_sentinel"] = failing_macro

        ask_council = self.framework.create_parallel_council_tool(agents_dict)
        parsed = json.loads(ask_council(json.dumps({
            "bullish_advocate": "Analyze AAPL",
            "macro_sentinel": "Check macro",
            "red_team_pessimist": "Challenge AAPL",
        })))

        self.assertIn("Bullish Advocate", parsed["bullish_advocate"])
        self.assertEqual(parsed["macro_sentinel"], "Error: Simulated provider timeout")
        self.assertIn("Red Team Pessimist", parsed["red_team_pessimist"])

    def test_cancellation_precancelled_and_midbatch_cleanup(self):
        """Pre-cancelled and mid-batch cancellation halt queued tasks and return interrupt markers cleanly."""
        # 1. Pre-cancelled
        ran = {"count": 0}
        self.framework.cancel_check = lambda: True
        tasks = {
            "bullish_advocate": lambda: ran.__setitem__("count", ran["count"] + 1) or "ok",
            "red_team_pessimist": lambda: ran.__setitem__("count", ran["count"] + 1) or "ok",
        }
        pre_res = self.framework.execute_ephemeral_batch(tasks)
        self.assertEqual(ran["count"], 0)
        self.assertEqual(
            pre_res,
            {
                "bullish_advocate": "[OFFLINE] Session terminated by user interrupt.",
                "red_team_pessimist": "[OFFLINE] Session terminated by user interrupt.",
            },
        )

        # 2. Mid-batch cancellation with > MAX_PARALLEL_COUNCIL_WORKERS (15) queued tasks
        cancel_flag = {"cancelled": False}
        self.framework.cancel_check = lambda: cancel_flag["cancelled"]
        started_lock = threading.Lock()
        executed_keys = []
        gate_event = threading.Event()

        mid_tasks = {}
        for idx, role in enumerate(sorted(APPROVED_PARALLEL_COUNCIL_ROLES)):
            short_key = _approved_role_to_key(role)

            def make_cancel_worker(i=idx, k=short_key):
                def _work():
                    with started_lock:
                        executed_keys.append(k)
                        if len(executed_keys) >= 2:
                            cancel_flag["cancelled"] = True
                            gate_event.set()
                    if i < MAX_PARALLEL_COUNCIL_WORKERS:
                        gate_event.wait(timeout=1.0)
                        time.sleep(0.02)
                    return f"ok:{k}"

                return _work

            mid_tasks[short_key] = make_cancel_worker()

        mid_res = self.framework.execute_ephemeral_batch(mid_tasks)
        self.assertEqual(len(mid_res), 15)
        self.assertLess(len(executed_keys), 15)
        self.assertIn("[OFFLINE] Session terminated by user interrupt.", mid_res.values())

    def test_deterministic_result_key_ordering(self):
        """Returned dict and JSON output preserve exact caller input key order regardless of completion order."""
        first_done_gate = threading.Event()
        ordered_keys = ["bullish_advocate", "red_team_pessimist", "neutral_structuralist"]

        def slow_first():
            # Wait until later tasks complete first to force out-of-order completion
            first_done_gate.wait(timeout=1.0)
            time.sleep(0.02)
            return "first-finished-last"

        def fast_second():
            return "second-finished-first"

        def fast_third():
            first_done_gate.set()
            return "third-finished-first"

        res = self.framework.execute_ephemeral_batch({
            ordered_keys[0]: slow_first,
            ordered_keys[1]: fast_second,
            ordered_keys[2]: fast_third,
        })
        self.assertEqual(list(res.keys()), ordered_keys)
        self.assertEqual(res[ordered_keys[0]], "first-finished-last")
        self.assertEqual(res[ordered_keys[1]], "second-finished-first")
        self.assertEqual(res[ordered_keys[2]], "third-finished-first")

    def test_create_agent_tool_binds_execution_role_context_when_no_subtools(self):
        """Sequential create_agent_tool wraps calls without subtools in execution_role_context(name)."""
        captured_roles = []

        def mock_gen(prompt, instruction, mode, tools=None):
            captured_roles.append(get_active_caller_role())
            return AntigravityResponse("Thesis complete")

        with patch.object(self.framework, "generate_response_with_fallback", side_effect=mock_gen):
            bull_tool = self.framework.create_agent_tool(
                name="Bullish Advocate",
                file_path="engine_instructions/bullish_gem.md",
                mode="THINKING",
                agent_tools=[],
            )
            out = bull_tool("Evaluate SPY")

        self.assertEqual(captured_roles, ["Bullish Advocate"])
        self.assertEqual(out, "Thesis complete")


if __name__ == "__main__":
    unittest.main()
