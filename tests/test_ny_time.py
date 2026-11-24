"""
New York time stamping: DST-aware (EST UTC-5 / EDT UTC-4) and computed per call, not frozen
at server start.
"""

import datetime
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import agent_framework
from agent_framework import format_ny_timestamp, ny_now

UTC = datetime.timezone.utc


class TestNewYorkTime(unittest.TestCase):
    def test_winter_is_est_utc_minus_5(self):
        self.assertEqual(format_ny_timestamp(datetime.datetime(2026, 11, 24, 15, 0, tzinfo=UTC)), "2026-11-24T10:00:00")

    def test_summer_is_edt_utc_minus_4(self):
        self.assertEqual(format_ny_timestamp(datetime.datetime(2026, 7, 1, 15, 0, tzinfo=UTC)), "2026-07-01T11:00:00")

    def test_dst_transition_days(self):
        # 2026-03-08 07:00Z is 03:00 EDT (clocks sprang forward); 2026-11-01 06:00Z is 01:00 EST.
        self.assertEqual(format_ny_timestamp(datetime.datetime(2026, 3, 8, 7, 0, tzinfo=UTC)), "2026-03-08T03:00:00")
        self.assertEqual(format_ny_timestamp(datetime.datetime(2026, 11, 1, 6, 0, tzinfo=UTC)), "2026-11-01T01:00:00")

    def test_ny_now_is_new_york_zone(self):
        self.assertEqual(str(ny_now().tzinfo), "America/New_York")

    def test_subagent_time_is_stamped_per_call(self):
        import web_server
        fw = web_server.framework
        tool = fw.create_agent_tool(name="Research Engine", file_path="engine_instructions/research.md", mode="FAST")
        captured = []

        def fake_generate(prompt, instruction, mode, tools=None):
            captured.append((prompt, instruction))
            return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text="ok")]))])

        first = datetime.datetime(2026, 11, 24, 14, 30, tzinfo=UTC)
        second = datetime.datetime(2026, 11, 24, 20, 45, tzinfo=UTC)
        with patch.object(fw, "generate_response_with_fallback", side_effect=fake_generate):
            with patch("agent_framework.ny_now", return_value=first.astimezone(agent_framework.NEW_YORK_TZ)):
                tool("query one")
            with patch("agent_framework.ny_now", return_value=second.astimezone(agent_framework.NEW_YORK_TZ)):
                tool("query two")

        self.assertIn("2026-11-24T09:30:00", captured[0][0])
        self.assertIn("2026-11-24T09:30:00", captured[0][1])
        self.assertIn("2026-11-24T15:45:00", captured[1][0])
        self.assertIn("2026-11-24T15:45:00", captured[1][1])


if __name__ == "__main__":
    unittest.main()
