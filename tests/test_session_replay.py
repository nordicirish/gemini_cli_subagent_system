"""
Tests for AntigravityChatSession conversation replay: the agy CLI is stateless per call, so
tool follow-up turns must carry the original request (with its DATA_PACKET), earlier steps,
and a condensed recap of earlier exchanges, within a byte budget.
"""

import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import agent_framework
from agent_framework import AntigravityChatSession, Part, REPLAY_PROMPT_MAX_BYTES

DATA_PACKET = '\n[SYSTEM AUTO-INJECT: LIVE_DATA_PACKET]\n```json\n{"ssot": {"portfolio_snapshot": [{"ticker": "NVDA"}]}}\n```\n[END_DATA_PACKET]\n'


class RecordingSession:
    def __init__(self, replies, is_subagent=False):
        self.prompts = []
        replies = list(replies)

        def generate(user_input, sys_instruction, mode):
            self.prompts.append(user_input)
            return SimpleNamespace(text=replies.pop(0) if replies else "done")

        self.session = AntigravityChatSession(
            runner=None, sys_instruction="SYS", tools=[], generate_fn=generate, is_subagent=is_subagent
        )


def _tool_result(name, result):
    return [Part.from_function_response(name=name, response={"result": result})]


class TestSessionReplay(unittest.TestCase):
    def test_first_message_is_sent_unchanged(self):
        rec = RecordingSession(["ok"])
        rec.session.send_message("[USER_QUERY]: hello")
        self.assertEqual(rec.prompts[0], "[USER_QUERY]: hello")

    def test_tool_followup_carries_original_request_and_steps(self):
        rec = RecordingSession(['```json\n{"function_calls": [{"name": "ask_x", "args": {}}]}\n```', "step2", "final"])
        request = f"[ACTIVE_MODEL]: m\n{DATA_PACKET}[USER_QUERY]: Should I trim NVDA?"
        rec.session.send_message(request)
        rec.session.send_message(_tool_result("ask_x", "RESULT_ONE"))
        second = rec.prompts[1]
        self.assertIn("[CURRENT REQUEST]", second)
        self.assertIn("Should I trim NVDA?", second)
        self.assertIn('"portfolio_snapshot"', second)  # live DATA_PACKET is replayed in full
        self.assertIn("ASSISTANT:", second)
        self.assertIn("ask_x", second)
        self.assertIn("Tool execution results:", second)
        self.assertIn("RESULT_ONE", second)

        rec.session.send_message(_tool_result("ask_y", "RESULT_TWO"))
        third = rec.prompts[2]
        self.assertIn("TOOL RESULTS:", third)
        self.assertIn("RESULT_ONE", third)
        self.assertIn("RESULT_TWO", third)
        self.assertLess(third.index("RESULT_ONE"), third.index("RESULT_TWO"))

    def test_next_exchange_gets_condensed_recap(self):
        rec = RecordingSession(["Trim 20% of NVDA.", "ok"])
        rec.session.send_message(f"{DATA_PACKET}[USER_QUERY]: Should I trim NVDA?")
        rec.session.send_message("[USER_QUERY]: And AMD?")
        prompt = rec.prompts[1]
        self.assertIn("[EARLIER IN THIS SESSION", prompt)
        self.assertIn("USER: Should I trim NVDA?", prompt)
        self.assertIn("ASSISTANT: Trim 20% of NVDA.", prompt)
        self.assertNotIn('"portfolio_snapshot"', prompt)  # stale DATA_PACKET is not replayed
        self.assertTrue(prompt.rstrip().endswith("[END CURRENT REQUEST]"))
        self.assertIn("And AMD?", prompt)

    def test_prior_exchanges_are_bounded(self):
        rec = RecordingSession([f"reply {i}" for i in range(10)])
        for i in range(8):
            rec.session.send_message(f"[USER_QUERY]: question {i}")
        last = rec.prompts[-1]
        self.assertNotIn("question 0", last)
        self.assertIn("question 6", last)
        self.assertEqual(len(rec.session._exchanges), agent_framework.REPLAY_PRIOR_EXCHANGES + 1)

    def test_prompt_stays_within_budget(self):
        rec = RecordingSession(["call"] * 12)
        rec.session.send_message("[USER_QUERY]: big analysis")
        for i in range(10):
            rec.session.send_message(_tool_result(f"tool_{i}", "X" * 20_000))
        for prompt in rec.prompts[1:]:
            self.assertLessEqual(len(prompt.encode("utf-8")), REPLAY_PROMPT_MAX_BYTES + 200)
        self.assertIn("omitted for length", rec.prompts[-1])
        self.assertIn("big analysis", rec.prompts[-1])

    def test_image_parts_do_not_crash(self):
        rec = RecordingSession(["ok"])
        rec.session.send_message([Part.from_bytes(b"\x89PNG", mime_type="image/png"), "[USER_QUERY]: chart check"])
        self.assertIn("Attachment omitted (image/png)", rec.prompts[0])
        self.assertIn("chart check", rec.prompts[0])

    def test_subagent_followup_keeps_delegated_query(self):
        rec = RecordingSession(["call", "final"], is_subagent=True)
        rec.session.send_message("Evaluate SPY narrative")
        rec.session.send_message(_tool_result("read_ssot", "{}"))
        self.assertIn("Evaluate SPY narrative", rec.prompts[1])
        self.assertIn("Sub-tool execution results:", rec.prompts[1])


if __name__ == "__main__":
    unittest.main()
