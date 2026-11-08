"""
Unit & Integration Tests for Structured Audit Logging
Governed by Protocol 03 and Stage 1 Section 7.
"""

import os
import sys
import json
import unittest
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from audit_logger import AuditLogger, AuditEvent, redact_secrets


class TestAuditLogger(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_file = os.path.join(self.temp_dir.name, "test_events.jsonl")
        self.logger = AuditLogger(log_path=self.log_file)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_log_event_appends_valid_jsonl(self):
        event = AuditEvent(
            action_category="TOOL_CALL",
            status="SUCCESS",
            agent_role="Terminal Orchestrator",
            tool_name="read_ssot",
            permission_decision="GRANTED",
        )
        self.logger.log_event(event)

        self.assertTrue(os.path.exists(self.log_file))
        with open(self.log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        self.assertEqual(len(lines), 1)
        parsed = json.loads(lines[0])
        self.assertEqual(parsed["action_category"], "TOOL_CALL")
        self.assertEqual(parsed["status"], "SUCCESS")
        self.assertEqual(parsed["tool_name"], "read_ssot")
        self.assertEqual(parsed["agent_role"], "Terminal Orchestrator")
        self.assertIn("timestamp_utc", parsed)
        self.assertIn("event_id", parsed)

    def test_secret_redaction_in_audit_records(self):
        raw_details = {
            "api_key": "AIzaSyD-fake_gemini_api_key_1234567890",
            "token": "ghp_1234567890abcdefghijklmnopqrstuvwxyz",
            "safe_data": "AAPL, MSFT",
        }
        event = AuditEvent(
            action_category="TOOL_CALL",
            status="ATTEMPTED",
            agent_role="Data Analyst",
            tool_name="perform_web_forensic_search",
            details=raw_details,
        )
        self.logger.log_event(event)

        with open(self.log_file, "r", encoding="utf-8") as f:
            logged_text = f.read()

        # The raw secrets must NOT appear in the log file
        self.assertNotIn("AIzaSyD-fake_gemini_api_key_1234567890", logged_text)
        self.assertNotIn("ghp_1234567890abcdefghijklmnopqrstuvwxyz", logged_text)
        # Redaction marker must be present
        self.assertIn("[REDACTED_SECRET]", logged_text)
        self.assertIn("AAPL, MSFT", logged_text)

    def test_denied_event_logged(self):
        event = AuditEvent(
            action_category="TOOL_CALL",
            status="DENIED",
            agent_role="Python Engineer",
            tool_name="update_ssot",
            permission_decision="DENIED",
            error_classification="PERMISSION_DENIED",
            details={"reason": "Financial boundary violation"},
        )
        self.logger.log_event(event)

        with open(self.log_file, "r", encoding="utf-8") as f:
            line = f.readline()

        parsed = json.loads(line)
        self.assertEqual(parsed["status"], "DENIED")
        self.assertEqual(parsed["permission_decision"], "DENIED")
        self.assertEqual(parsed["error_classification"], "PERMISSION_DENIED")

    def test_concurrent_audit_writes(self):
        errors = []
        threads = []

        def worker(tid):
            try:
                for i in range(15):
                    evt = AuditEvent(
                        action_category="CONCURRENT_TEST",
                        status="SUCCESS",
                        agent_id=f"worker-{tid}",
                        details={"iteration": i},
                    )
                    self.logger.log_event(evt)
            except Exception as e:
                errors.append(e)

        for tid in range(4):
            t = threading.Thread(target=worker, args=(tid,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Concurrent logging errors: {errors}")
        with open(self.log_file, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]

        self.assertEqual(len(lines), 60)
        for line in lines:
            parsed = json.loads(line)
            self.assertEqual(parsed["action_category"], "CONCURRENT_TEST")


if __name__ == "__main__":
    unittest.main()
