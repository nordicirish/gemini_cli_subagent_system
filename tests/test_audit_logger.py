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
        import time
        errors = []
        threads = []

        def worker(tid):
            try:
                for i in range(10):
                    evt = AuditEvent(
                        action_category="CONCURRENT_TEST",
                        status="SUCCESS",
                        agent_id=f"worker-{tid}",
                        details={"iteration": i},
                    )
                    self.logger.log_event(evt)
                    time.sleep(0.001)
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

        self.assertEqual(len(lines), 40)
        for line in lines:
            parsed = json.loads(line)
            self.assertEqual(parsed["action_category"], "CONCURRENT_TEST")

    def test_stage37_canonical_audit_log_gitignored_and_untouched(self):
        """R5: Runtime agent-events.jsonl is gitignored, lazy-dir on init, and tests leave canonical log unmodified."""
        import hashlib

        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        gitignore_path = os.path.join(repo_root, ".gitignore")
        with open(gitignore_path, "r", encoding="utf-8") as f:
            gitignore_text = f.read()
        self.assertIn("context/agent_logs/*.jsonl", gitignore_text)

        # Lazy directory creation: instantiating AuditLogger must not create directories/files before log_event()
        nested_dir = os.path.join(self.temp_dir.name, "lazy_audit_subdir")
        lazy_log = os.path.join(nested_dir, "events.jsonl")
        self.assertFalse(os.path.exists(nested_dir))
        logger = AuditLogger(log_path=lazy_log)
        self.assertFalse(os.path.exists(nested_dir))
        self.assertFalse(os.path.exists(lazy_log))

        # Canonical log file remains byte-for-byte invariant (if present) or absent (if absent) when isolated AuditLogger logs events
        canonical_log = os.path.join(repo_root, "context", "agent_logs", "agent-events.jsonl")
        canonical_existed = os.path.isfile(canonical_log)
        before_hash = None
        if canonical_existed:
            with open(canonical_log, "rb") as f:
                before_hash = hashlib.sha256(f.read()).hexdigest()

        logger.log_event(AuditEvent(action_category="ISOLATION_TEST", status="SUCCESS", agent_role="System"))
        self.assertTrue(os.path.exists(lazy_log))

        if canonical_existed:
            self.assertTrue(os.path.isfile(canonical_log))
            with open(canonical_log, "rb") as f:
                after_hash = hashlib.sha256(f.read()).hexdigest()
            self.assertEqual(before_hash, after_hash)
        else:
            self.assertFalse(os.path.exists(canonical_log))

        # Dual-condition verification using isolated fixtures (absent vs present)
        with tempfile.TemporaryDirectory() as fixture_dir:
            # Condition 1: Simulated canonical log is ABSENT before isolated logging
            simulated_canonical_absent = os.path.join(fixture_dir, "canonical_absent", "agent-events.jsonl")
            isolated_log_a = os.path.join(fixture_dir, "isolated_a", "events.jsonl")
            logger_a = AuditLogger(log_path=isolated_log_a)
            self.assertFalse(os.path.exists(simulated_canonical_absent))
            logger_a.log_event(AuditEvent(action_category="FIXTURE_TEST_A", status="SUCCESS", agent_role="System"))
            self.assertTrue(os.path.exists(isolated_log_a))
            self.assertFalse(os.path.exists(simulated_canonical_absent))

            # Condition 2: Simulated canonical log is PRESENT before isolated logging
            canonical_b_dir = os.path.join(fixture_dir, "canonical_present")
            os.makedirs(canonical_b_dir, exist_ok=True)
            simulated_canonical_present = os.path.join(canonical_b_dir, "agent-events.jsonl")
            fixture_content = b'{"action_category":"BASELINE","status":"SUCCESS"}\n'
            with open(simulated_canonical_present, "wb") as f:
                f.write(fixture_content)
            fixture_before_hash = hashlib.sha256(fixture_content).hexdigest()

            isolated_log_b = os.path.join(fixture_dir, "isolated_b", "events.jsonl")
            logger_b = AuditLogger(log_path=isolated_log_b)
            logger_b.log_event(AuditEvent(action_category="FIXTURE_TEST_B", status="SUCCESS", agent_role="System"))
            self.assertTrue(os.path.exists(isolated_log_b))
            with open(simulated_canonical_present, "rb") as f:
                fixture_after_hash = hashlib.sha256(f.read()).hexdigest()
            self.assertEqual(fixture_before_hash, fixture_after_hash)


if __name__ == "__main__":
    unittest.main()
