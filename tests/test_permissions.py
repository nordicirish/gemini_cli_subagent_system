"""
Unit & Integration Tests for Runtime Permission Enforcement
Governed by Protocol 03 (Security) and Protocol 06 (Financial Boundaries).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from tool_registry import (
    ToolRegistry,
    HUMAN_APPROVAL_TOKEN_RULES,
    HUMAN_APPROVAL_TOKEN_FINANCIAL,
    GLOBAL_TOOL_REGISTRY,
)
import tools


class TestPermissionEnforcement(unittest.TestCase):
    def setUp(self):
        self.registry = GLOBAL_TOOL_REGISTRY

    def test_unknown_tool_rejected(self):
        is_valid, reason = self.registry.validate_call(
            tool_name="non_existent_tool_xyz",
            agent_role="Terminal Orchestrator",
            args={},
        )
        self.assertFalse(is_valid)
        self.assertIn("not registered", reason)

    def test_unrecognized_role_rejected(self):
        is_valid, reason = self.registry.validate_call(
            tool_name="read_ssot",
            agent_role="AttackerRole",
            args={},
        )
        self.assertFalse(is_valid)
        self.assertIn("not a recognized agent role", reason)

    def test_engineering_role_forbidden_from_financial_writes(self):
        """Engineering roles must be blocked from executing update_ssot."""
        engineering_roles = [
            "Architecture Agent",
            "Python Engineer",
            "Security Agent",
            "Testing Agent",
            "Documentation Agent",
            "Independent Reviewer",
        ]
        for role in engineering_roles:
            is_valid, reason = self.registry.validate_call(
                tool_name="update_ssot",
                agent_role=role,
                args={"payload_json": "{}"},
            )
            self.assertFalse(is_valid, f"Role {role} should be blocked from update_ssot")
            self.assertIn("Financial boundary violation", reason)

    def test_investment_role_allowed_for_authorized_tools(self):
        """Investment roles (Terminal Orchestrator, Context Engine) can execute portfolio tools."""
        is_valid, reason = self.registry.validate_call(
            tool_name="update_ssot",
            agent_role="Terminal Orchestrator",
            args={"payload_json": "{}"},
        )
        self.assertTrue(is_valid, f"Terminal Orchestrator should be allowed: {reason}")

    def test_invalid_parameters_rejected(self):
        """Passing unexpected parameters must fail schema validation."""
        is_valid, reason = self.registry.validate_call(
            tool_name="perform_web_forensic_search",
            agent_role="Data Analyst",
            args={"query": "test query", "unexpected_exploit_param": "payload"},
        )
        self.assertFalse(is_valid)
        self.assertIn("Unknown parameter", reason)

    def test_missing_required_parameters_rejected(self):
        """Omitting required parameters must fail validation."""
        is_valid, reason = self.registry.validate_call(
            tool_name="perform_web_forensic_search",
            agent_role="Data Analyst",
            args={},
        )
        self.assertFalse(is_valid)
        self.assertIn("Missing required parameter", reason)

    def test_update_rules_requires_human_approval_token(self):
        """Updating canonical rules requires explicit human approval token."""
        # 1. Without token -> Rejected
        is_valid, reason = self.registry.validate_call(
            tool_name="update_rules",
            agent_role="Context Engine",
            args={"rules_md_content": "# New Rules", "approval_token": None},
            approval_token=None,
        )
        self.assertFalse(is_valid)
        self.assertIn("Human approval required", reason)

        # 2. With invalid token -> Rejected
        is_valid, reason = self.registry.validate_call(
            tool_name="update_rules",
            agent_role="Context Engine",
            args={"rules_md_content": "# New Rules", "approval_token": "FORGED_TOKEN"},
            approval_token="FORGED_TOKEN",
        )
        self.assertFalse(is_valid)
        self.assertIn("Human approval required", reason)

        # 3. With valid verified token -> Allowed
        is_valid, reason = self.registry.validate_call(
            tool_name="update_rules",
            agent_role="Context Engine",
            args={"rules_md_content": "# New Rules", "approval_token": HUMAN_APPROVAL_TOKEN_RULES},
            approval_token=HUMAN_APPROVAL_TOKEN_RULES,
        )
        self.assertTrue(is_valid, f"Expected allowed with token: {reason}")

    def test_execute_raises_permission_error_on_unauthorized(self):
        """Direct execute() call raises PermissionError fail-closed."""
        with self.assertRaises(PermissionError):
            self.registry.execute(
                tool_name="update_ssot",
                agent_role="Python Engineer",
                args={"payload_json": "{}"},
            )


if __name__ == "__main__":
    unittest.main()
