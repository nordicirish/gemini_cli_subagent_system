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
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import agent_framework
from agent_framework import (
    APPROVED_PARALLEL_COUNCIL_ROLES,
    APPROVED_PARALLEL_COUNCIL_TOOLS,
    AgentFramework,
    AntigravityAgentRunner,
    AntigravityResponse,
    MAX_PARALLEL_COUNCIL_WORKERS,
)
from tool_registry import GLOBAL_TOOL_REGISTRY, get_active_caller_role
import tools
import web_server


def _approved_role_to_key(role_name: str) -> str:
    return role_name.lower().replace(" ", "_")


def _annotate_readonly_callable(fn, role_name: str, agent_tools=None):
    fn._agent_role = role_name
    fn._is_read_only_deliberative = True
    fn._agent_tools = list(agent_tools or [])
    return fn


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

        _annotate_readonly_callable(safe_sibling, "Macro Sentinel", [tools.perform_web_forensic_search])

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
            "ask_macro_sentinel": _annotate_readonly_callable(lambda q: "macro ok", "Macro Sentinel"),
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
        bull_1 = _annotate_readonly_callable(lambda: "1", "Bullish Advocate")
        bull_2 = _annotate_readonly_callable(lambda: "2", "Bullish Advocate")
        with self.assertRaises(ValueError):
            self.framework.execute_ephemeral_batch({})
        with self.assertRaises(ValueError):
            self.framework.execute_ephemeral_batch(["bullish_advocate"])  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            self.framework.execute_ephemeral_batch({
                "bullish_advocate": bull_1,
                "ask_bullish_advocate": bull_2,
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

            def make_worker(k=short_key, r=role):
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

                return _annotate_readonly_callable(_work, r)

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

        _annotate_readonly_callable(failing_macro, "Macro Sentinel", [tools.perform_web_forensic_search])
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
            "bullish_advocate": _annotate_readonly_callable(
                lambda: ran.__setitem__("count", ran["count"] + 1) or "ok", "Bullish Advocate"
            ),
            "red_team_pessimist": _annotate_readonly_callable(
                lambda: ran.__setitem__("count", ran["count"] + 1) or "ok", "Red Team Pessimist"
            ),
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

            def make_cancel_worker(i=idx, k=short_key, r=role):
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

                return _annotate_readonly_callable(_work, r)

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

        _annotate_readonly_callable(slow_first, "Bullish Advocate")
        _annotate_readonly_callable(fast_second, "Red Team Pessimist")
        _annotate_readonly_callable(fast_third, "Neutral Structuralist")

        res = self.framework.execute_ephemeral_batch({
            ordered_keys[0]: slow_first,
            ordered_keys[1]: fast_second,
            ordered_keys[2]: fast_third,
        })
        self.assertEqual(list(res.keys()), ordered_keys)
        self.assertEqual(res[ordered_keys[0]], "first-finished-last")
        self.assertEqual(res[ordered_keys[1]], "second-finished-first")
        self.assertEqual(res[ordered_keys[2]], "third-finished-first")

    def test_fail_closed_capability_validation_rejects_unannotated_mismatched_and_wrapped_mutating(self):
        """Fail-closed capability validation rejects unannotated, mismatched, incomplete, and wrapped mutating callables without starting siblings."""
        sibling_ran = {"count": 0}

        def valid_sibling():
            sibling_ran["count"] += 1
            return "sibling ok"

        _annotate_readonly_callable(valid_sibling, "Macro Sentinel", [tools.perform_web_forensic_search])

        # 1. Unannotated lambda/callable is rejected before any sibling runs
        with self.assertRaises(PermissionError):
            self.framework.execute_ephemeral_batch({
                "macro_sentinel": valid_sibling,
                "bullish_advocate": lambda: "unannotated",
            })
        self.assertEqual(sibling_ran["count"], 0)

        # 2. Mismatched role metadata is rejected without running sibling
        def wrong_role():
            return "wrong role"

        _annotate_readonly_callable(wrong_role, "Macro Sentinel", [])
        with self.assertRaises(PermissionError):
            self.framework.execute_ephemeral_batch({
                "macro_sentinel": valid_sibling,
                "bullish_advocate": wrong_role,
            })
        self.assertEqual(sibling_ran["count"], 0)

        # 3. Missing read-only flag or invalid/missing _agent_tools is rejected without running sibling
        def only_role():
            return "missing flags"

        only_role._agent_role = "Bullish Advocate"
        with self.assertRaises(PermissionError):
            self.framework.execute_ephemeral_batch({
                "macro_sentinel": valid_sibling,
                "bullish_advocate": only_role,
            })

        def nonbool_flag():
            return "truthy int"

        nonbool_flag._agent_role = "Bullish Advocate"
        nonbool_flag._is_read_only_deliberative = 1
        nonbool_flag._agent_tools = []
        with self.assertRaises(PermissionError):
            self.framework.execute_ephemeral_batch({
                "macro_sentinel": valid_sibling,
                "bullish_advocate": nonbool_flag,
            })

        def missing_tools_decl():
            return "missing tools"

        missing_tools_decl._agent_role = "Bullish Advocate"
        missing_tools_decl._is_read_only_deliberative = True
        with self.assertRaises(PermissionError):
            self.framework.execute_ephemeral_batch({
                "macro_sentinel": valid_sibling,
                "bullish_advocate": missing_tools_decl,
            })

        def none_tools_decl():
            return "none tools"

        none_tools_decl._agent_role = "Bullish Advocate"
        none_tools_decl._is_read_only_deliberative = True
        none_tools_decl._agent_tools = None
        with self.assertRaises(PermissionError):
            self.framework.execute_ephemeral_batch({
                "macro_sentinel": valid_sibling,
                "bullish_advocate": none_tools_decl,
            })
        self.assertEqual(sibling_ran["count"], 0)

        # 4. Mutating tool declared directly, via functools.partial, or via __wrapped__ alias is rejected
        def harmless_alias(*args, **kwargs):
            return "alias"

        harmless_alias.__wrapped__ = tools.update_ssot

        for bad_tools in (
            [tools.update_ssot],
            ["update_decision_log"],
            [harmless_alias],
            [functools.partial(tools.update_ssot)],
        ):
            def bad_agent():
                return "bad"

            _annotate_readonly_callable(bad_agent, "Bullish Advocate", bad_tools)
            with self.assertRaises(PermissionError):
                self.framework.execute_ephemeral_batch({
                    "macro_sentinel": valid_sibling,
                    "bullish_advocate": bad_agent,
                })

            # Also wrap in valid-looking outer wrapper with __wrapped__ and partial
            def outer_wrapper(*args, **kwargs):
                return "wrapped"

            _annotate_readonly_callable(outer_wrapper, "Bullish Advocate", [tools.perform_web_forensic_search])
            outer_wrapper.__wrapped__ = bad_agent
            with self.assertRaises(PermissionError):
                self.framework.execute_ephemeral_batch({
                    "macro_sentinel": valid_sibling,
                    "bullish_advocate": outer_wrapper,
                })
            with self.assertRaises(PermissionError):
                self.framework.execute_ephemeral_batch({
                    "macro_sentinel": valid_sibling,
                    "bullish_advocate": functools.partial(outer_wrapper),
                })

        self.assertEqual(sibling_ran["count"], 0)

        # 5. Unannotated or mismatched callables passed to create_parallel_council_tool are excluded fail-closed
        ask_council = self.framework.create_parallel_council_tool({
            "ask_macro_sentinel": lambda q: "unannotated",
            "ask_red_team_pessimist": wrong_role,
            "ask_bullish_advocate": _annotate_readonly_callable(lambda q: "bull ok", "Bullish Advocate"),
        })
        excluded_unannotated = json.loads(ask_council(json.dumps({"macro_sentinel": "check"})))
        self.assertIn("error", excluded_unannotated)
        excluded_mismatched = json.loads(ask_council(json.dumps({"red_team_pessimist": "check"})))
        self.assertIn("error", excluded_mismatched)
        valid_only = json.loads(ask_council(json.dumps({"bullish_advocate": "check"})))
        self.assertEqual(valid_only["bullish_advocate"], "bull ok")

        # 6. Valid generated read-only agents from create_agent_tool execute cleanly in parallel
        with patch.object(self.framework, "load_system_instruction", return_value="Dummy instruction"), \
             patch.object(self.framework, "generate_response_with_fallback", return_value=AntigravityResponse("Generated OK")):
            gen_bull = self.framework.create_agent_tool(
                name="Bullish Advocate",
                file_path="engine_instructions/bullish_gem.md",
                mode="THINKING",
                agent_tools=[tools.read_ssot, tools.perform_web_forensic_search],
            )
            gen_red = self.framework.create_agent_tool(
                name="Red Team Pessimist",
                file_path="engine_instructions/red_team_gem.md",
                mode="THINKING",
                agent_tools=[tools.read_ssot],
            )
            gen_council = self.framework.create_parallel_council_tool({
                "ask_bullish_advocate": gen_bull,
                "ask_red_team_pessimist": gen_red,
            })
            gen_out = json.loads(gen_council(json.dumps({
                "bullish_advocate": "Evaluate SPY",
                "red_team_pessimist": "Challenge SPY",
            })))
            self.assertIn("Generated OK", gen_out["bullish_advocate"])
            self.assertIn("Generated OK", gen_out["red_team_pessimist"])

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


class TestContextAndTokenEfficiency(unittest.TestCase):
    def setUp(self):
        self.framework = AgentFramework()

    def test_get_sys_instruction_preserves_rules_and_excludes_redundant_trade_lessons(self):
        """_get_sys_instruction keeps base instruction and rules.md while omitting duplicated trade_lessons.json."""
        sys_inst = self.framework._get_sys_instruction("BASE_INSTRUCTION_MARKER")

        self.assertTrue(sys_inst.startswith("BASE_INSTRUCTION_MARKER"))
        self.assertIn("--- ATTACHED KNOWLEDGE BASE (GEM_Rules_Data) ---", sys_inst)
        self.assertNotIn("--- TRADE LESSONS REPOSITORY ---", sys_inst)

    def test_antigravity_response_and_runner_honest_token_accounting(self):
        """AntigravityResponse and run_prompt label heuristic estimates honestly and report zero cached tokens."""
        est_resp = AntigravityResponse("hello world", prompt_tokens=20, candidate_tokens=3)
        self.assertTrue(est_resp.is_estimated)
        self.assertEqual(est_resp.token_accounting_mode, "heuristic_estimate")
        self.assertTrue(est_resp.usage_metadata.is_estimated)
        self.assertEqual(est_resp.usage_metadata.token_accounting_mode, "heuristic_estimate")
        self.assertEqual(est_resp.usage_metadata.prompt_token_count, 20)
        self.assertEqual(est_resp.usage_metadata.candidates_token_count, 3)
        self.assertEqual(est_resp.usage_metadata.estimated_prompt_tokens, 20)
        self.assertEqual(est_resp.usage_metadata.estimated_candidate_tokens, 3)
        self.assertIsNone(est_resp.usage_metadata.actual_prompt_tokens)
        self.assertIsNone(est_resp.usage_metadata.actual_candidate_tokens)
        self.assertEqual(est_resp.usage_metadata.cached_content_token_count, 0)

        meas_resp = AntigravityResponse("measured", prompt_tokens=150, candidate_tokens=25, is_estimated=False)
        self.assertFalse(meas_resp.is_estimated)
        self.assertEqual(meas_resp.token_accounting_mode, "provider_measured")
        self.assertFalse(meas_resp.usage_metadata.is_estimated)
        self.assertEqual(meas_resp.usage_metadata.actual_prompt_tokens, 150)
        self.assertEqual(meas_resp.usage_metadata.actual_candidate_tokens, 25)
        self.assertIsNone(meas_resp.usage_metadata.estimated_prompt_tokens)
        self.assertIsNone(meas_resp.usage_metadata.estimated_candidate_tokens)
        self.assertEqual(meas_resp.usage_metadata.cached_content_token_count, 0)

        captured_prompt = {}
        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.returncode = 0
        mock_proc.poll.return_value = 0

        def fake_communicate(input=None):
            captured_prompt["text"] = input
            return ("Agent reply output", "")

        mock_proc.communicate.side_effect = fake_communicate

        runner = AntigravityAgentRunner(agy_path="mock_agy.exe")
        with patch("agent_framework.subprocess.Popen", return_value=mock_proc), \
             patch("agent_framework._cleanup_subprocess"):
            run_resp = runner.run_prompt("Analyze SPY", system_instruction="SYS", mode="FAST")

        expected_prompt_tokens = len(captured_prompt["text"]) // 4
        expected_candidate_tokens = len("Agent reply output") // 4
        self.assertTrue(run_resp.is_estimated)
        self.assertEqual(run_resp.token_accounting_mode, "heuristic_estimate")
        self.assertEqual(run_resp.usage_metadata.prompt_token_count, expected_prompt_tokens)
        self.assertEqual(run_resp.usage_metadata.candidates_token_count, expected_candidate_tokens)
        self.assertEqual(run_resp.usage_metadata.estimated_prompt_tokens, expected_prompt_tokens)
        self.assertEqual(run_resp.usage_metadata.estimated_candidate_tokens, expected_candidate_tokens)
        self.assertIsNone(run_resp.usage_metadata.actual_prompt_tokens)
        self.assertIsNone(run_resp.usage_metadata.actual_candidate_tokens)
        self.assertEqual(run_resp.usage_metadata.cached_content_token_count, 0)

    def test_turn_usage_accumulates_estimated_vs_actual_tokens_without_fabricated_cache(self):
        """AgentFramework.turn_usage distinguishes heuristic estimates from measured usage and never fabricates cache."""
        self.framework.reset_turn_usage()
        self.assertEqual(
            self.framework.turn_usage,
            {
                "prompt_tokens": 0,
                "candidates_tokens": 0,
                "cached_tokens": 0,
                "estimated_prompt_tokens": 0,
                "estimated_candidates_tokens": 0,
                "actual_prompt_tokens": None,
                "actual_candidates_tokens": None,
                "is_estimated": True,
                "token_accounting_mode": "heuristic_estimate",
                "caching_active": False,
                "estimated_cost": 0.0,
            },
        )

        # 1. Heuristic-estimated calls
        with patch.object(
            self.framework.antigravity_runner,
            "run_prompt",
            return_value=AntigravityResponse("est out", prompt_tokens=120, candidate_tokens=30, is_estimated=True),
        ):
            self.framework.generate_response_with_fallback("p1", "i1", "FAST")

        self.assertEqual(self.framework.turn_usage["prompt_tokens"], 120)
        self.assertEqual(self.framework.turn_usage["candidates_tokens"], 30)
        self.assertEqual(self.framework.turn_usage["estimated_prompt_tokens"], 120)
        self.assertEqual(self.framework.turn_usage["estimated_candidates_tokens"], 30)
        self.assertIsNone(self.framework.turn_usage["actual_prompt_tokens"])
        self.assertIsNone(self.framework.turn_usage["actual_candidates_tokens"])
        self.assertTrue(self.framework.turn_usage["is_estimated"])
        self.assertEqual(self.framework.turn_usage["token_accounting_mode"], "heuristic_estimate")
        self.assertEqual(self.framework.turn_usage["cached_tokens"], 0)
        self.assertFalse(self.framework.turn_usage["caching_active"])

        # 2. Pure provider-measured turn
        self.framework.reset_turn_usage()
        with patch.object(
            self.framework.antigravity_runner,
            "run_prompt",
            return_value=AntigravityResponse("meas out", prompt_tokens=200, candidate_tokens=45, is_estimated=False),
        ):
            self.framework.generate_response_with_fallback("p2", "i2", "PRO")

        self.assertFalse(self.framework.turn_usage["is_estimated"])
        self.assertEqual(self.framework.turn_usage["token_accounting_mode"], "provider_measured")
        self.assertEqual(self.framework.turn_usage["actual_prompt_tokens"], 200)
        self.assertEqual(self.framework.turn_usage["actual_candidates_tokens"], 45)
        self.assertEqual(self.framework.turn_usage["estimated_prompt_tokens"], 0)
        self.assertEqual(self.framework.turn_usage["estimated_candidates_tokens"], 0)
        self.assertEqual(self.framework.turn_usage["cached_tokens"], 0)
        self.assertFalse(self.framework.turn_usage["caching_active"])

        # 3. Mixed measured + estimated in the same turn
        with patch.object(
            self.framework.antigravity_runner,
            "run_prompt",
            return_value=AntigravityResponse("mix out", prompt_tokens=40, candidate_tokens=10, is_estimated=True),
        ):
            self.framework.generate_response_with_fallback("p3", "i3", "FAST")

        self.assertTrue(self.framework.turn_usage["is_estimated"])
        self.assertEqual(self.framework.turn_usage["token_accounting_mode"], "mixed_estimated_and_measured")
        self.assertEqual(self.framework.turn_usage["prompt_tokens"], 240)
        self.assertEqual(self.framework.turn_usage["candidates_tokens"], 55)
        self.assertEqual(self.framework.turn_usage["actual_prompt_tokens"], 200)
        self.assertEqual(self.framework.turn_usage["actual_candidates_tokens"], 45)
        self.assertEqual(self.framework.turn_usage["estimated_prompt_tokens"], 40)
        self.assertEqual(self.framework.turn_usage["estimated_candidates_tokens"], 10)
        self.assertEqual(self.framework.turn_usage["cached_tokens"], 0)
        self.assertFalse(self.framework.turn_usage["caching_active"])

    def test_bounded_stdin_prompt_preserves_live_data_and_tools(self):
        """_build_bounded_prompt compacts oversized static rules.md to <= MAX_AGY_STDIN_BYTES while preserving DATA_PACKET, local_storage_state, tools, and query."""
        with open(os.path.join("engine_instructions", "terminal.md"), "r", encoding="utf-8") as f:
            terminal_inst = f.read()
        full_sys = self.framework._get_sys_instruction(terminal_inst)
        full_sys_with_tools = (
            f"{full_sys}\n\n"
            "Available Tools:\n"
            "- read_ssot: Read current SSoT state\n\n"
            "If you need to query subagents or execute tools, output ONLY a JSON block specifying function calls:\n"
            '```json\n{"function_calls": [{"name": "tool_name", "args": {"query": "..."}}]}\n```\n'
            "Otherwise, output the complete single-pass Markdown analysis followed by the single valid ```json EXECUTION_PAYLOAD ``` block."
        )
        prompt = (
            "--- LIVE MARKET DATA_PACKET ---\n"
            'local_storage_state = {"portfolio_snapshot": [{"ticker": "NVDA", "shares": "10", "wac": "120.00"}], "unallocated_cash_eur": "1500.00"}\n'
            "[USER_QUERY]: Evaluate NVDA position and portfolio allocation."
        )
        bounded = agent_framework._build_bounded_prompt(
            "ANALYTICAL OUTPUT INSTRUCTION: Text only.",
            full_sys_with_tools,
            prompt,
        )
        self.assertLessEqual(
            len(bounded.encode("utf-8")),
            agent_framework.MAX_AGY_STDIN_BYTES,
        )
        self.assertIn("DATA_PACKET", bounded)
        self.assertIn("local_storage_state", bounded)
        self.assertIn("[USER_QUERY]: Evaluate NVDA position and portfolio allocation.", bounded)
        self.assertIn("Available Tools:\n- read_ssot:", bounded)

    def test_create_chat_session_forwards_selected_model_to_runner(self):
        """AgentFramework.create_chat_session forwards model_name through AntigravityChatSession to subprocess --model."""
        captured_cmd = {}
        mock_proc = MagicMock()
        mock_proc.pid = 88888
        mock_proc.returncode = 0
        mock_proc.poll.return_value = 0
        mock_proc.communicate.return_value = ("Neon model response", "")

        def fake_popen(cmd, **kwargs):
            captured_cmd["cmd"] = list(cmd)
            return mock_proc

        session = self.framework.create_chat_session("SYS", [], model_name="gemini-4-neon-high")
        with patch("agent_framework.subprocess.Popen", side_effect=fake_popen), \
             patch("agent_framework._cleanup_subprocess"):
            resp = session.send_message("Check SPY")

        self.assertEqual(resp.text, "Neon model response")
        cmd = captured_cmd["cmd"]
        model_idx = cmd.index("--model")
        self.assertEqual(cmd[model_idx + 1], "gemini-4-neon-high")

    def test_list_available_models_parses_cli_and_falls_back(self):
        """AntigravityAgentRunner.list_available_models parses `agy models` output and falls back to DEFAULT_ANTIGRAVITY_MODELS."""
        runner = AntigravityAgentRunner(agy_path="mock_agy.exe")
        mock_completed = MagicMock()
        mock_completed.returncode = 0
        mock_completed.stdout = (
            "Fetching available models...\n"
            "gemini-4-neon-high\tGemini 4 Neon (High)\n"
            "gemini-4-hydrogen-high\tGemini 4 Hydrogen (High)\n"
        )
        with patch("agent_framework.subprocess.run", return_value=mock_completed):
            models = runner.list_available_models(force_refresh=True)

        names = [m["name"] for m in models]
        self.assertIn("gemini-4-neon-high", names)
        self.assertIn("gemini-4-hydrogen-high", names)
        self.assertTrue(all(m.get("provider") == "antigravity" for m in models))

        # Failure falls back to DEFAULT_ANTIGRAVITY_MODELS including Hydrogen & Neon
        runner._models_cache = None
        with patch("agent_framework.subprocess.run", side_effect=OSError("agy missing")):
            fallback = runner.list_available_models(force_refresh=True)
        fb_names = [m["name"] for m in fallback]
        self.assertIn("gemini-4-neon-high", fb_names)
        self.assertIn("gemini-4-hydrogen-high", fb_names)

    def test_chat_session_forwards_turn_timeout_for_orchestrator_and_subagent(self):
        """AntigravityChatSession uses 300s timeout for Orchestrator turns and 180s for Subagent turns."""
        mock_runner = MagicMock()
        mock_runner.run_prompt.return_value = AntigravityResponse("Done")

        orch_session = self.framework.create_chat_session("SYS", [], model_name="gemini-4-neon-high")
        orch_session.runner = mock_runner
        orch_session.send_message("Run news scan")
        self.assertEqual(
            mock_runner.run_prompt.call_args.kwargs.get("timeout"),
            agent_framework.ORCHESTRATOR_TURN_TIMEOUT_SECONDS,
        )

        mock_runner.reset_mock()
        sub_session = agent_framework.AntigravityChatSession(
            mock_runner, "SYS", [], is_subagent=True, model_name="gemini-4-hydrogen-high"
        )
        sub_session.send_message("Subagent query")
        self.assertEqual(
            mock_runner.run_prompt.call_args.kwargs.get("timeout"),
            agent_framework.SUBAGENT_TURN_TIMEOUT_SECONDS,
        )

    def test_rs1_rs3_rs4_compacted_rules_preserve_core_thresholds_and_modern_tiers(self):
        """Compacted rules.md retains System Thresholds & core gates and removes stale model references."""
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        rules_text = (repo_root / "gem_trading_rules" / "rules.md").read_text(encoding="utf-8")
        compacted = agent_framework._compact_rules_for_budget(rules_text, 60_000)

        for required in (
            "## System Thresholds",
            "## Global Logic Gates",
            "## Anti Hallucination Core",
            "## Tool Supremacy Hierarchy",
            "## Score Protocol",
            "GLOBAL_ALPHA_FRICTION_HURDLE",
            "VOLATILITY_REGIME_TELEMETRY",
        ):
            self.assertIn(required, compacted, f"Compacted rules missing {required}")

        for rel_path in (
            "gem_trading_rules/rules.md",
            "engine_instructions/terminal.md",
            "engine_instructions/context_engine.md",
        ):
            txt = (repo_root / rel_path).read_text(encoding="utf-8")
            self.assertNotIn("GEMMA (Gemma4 via Cloud)", txt, f"Stale GEMMA ref in {rel_path}")
            self.assertNotIn("Gemini 3.8 Flash Extended", txt, f"Stale Gemini 3.8 ref in {rel_path}")


if __name__ == "__main__":
    unittest.main()
