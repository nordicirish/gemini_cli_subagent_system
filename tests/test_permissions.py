"""
Unit & Integration Tests for Runtime Permission Enforcement, Local Owner Confirmation,
Direct Financial Mutation Boundaries, and Argument Type Validation.
Governed by Stage 1.1 (R2, R3, R4), Protocol 03 (Security), and Protocol 06 (Financial Boundaries).
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from tool_registry import (
    ToolRegistry,
    GLOBAL_TOOL_REGISTRY,
    GLOBAL_OWNER_APPROVAL,
    execution_role_context,
    authorize_financial_mutation,
)
import tools
import fetch_stocks


class TestPermissionEnforcement(unittest.TestCase):
    def setUp(self):
        self.registry = GLOBAL_TOOL_REGISTRY
        GLOBAL_OWNER_APPROVAL.reset()

    def tearDown(self):
        GLOBAL_OWNER_APPROVAL.reset()

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

    # --- R4: Classification, Required Permission & Argument Type Enforcement ---

    def test_r4_classification_write_blocked_for_read_only_role(self):
        """R4: A READ-only role must be blocked from invoking any WRITE-classified tool even if listed in allowed_roles."""
        custom_reg = ToolRegistry()
        custom_reg.register(
            name="write_op",
            func=lambda x: x,
            description="Write tool",
            classification="WRITE",
            allowed_roles={"Data Analyst"},  # Data Analyst only has {"READ"} classification
            parameters={"x": str},
            required_permission="market_data_read",
        )
        is_valid, reason = custom_reg.validate_call(
            tool_name="write_op",
            agent_role="Data Analyst",
            args={"x": "hello"},
        )
        self.assertFalse(is_valid)
        self.assertIn("classification", reason.lower())

    def test_r4_required_permission_enforced(self):
        """R4: A role lacking the tool's required_permission in ROLE_PERMISSIONS must be rejected."""
        custom_reg = ToolRegistry()
        custom_reg.register(
            name="special_perm_tool",
            func=lambda x: x,
            description="Tool requiring rules_write permission",
            classification="READ",
            allowed_roles={"Data Analyst"},
            parameters={"x": str},
            required_permission="write_rules",  # Data Analyst does not have write_rules
        )
        is_valid, reason = custom_reg.validate_call(
            tool_name="special_perm_tool",
            agent_role="Data Analyst",
            args={"x": "hello"},
        )
        self.assertFalse(is_valid)
        self.assertIn("required permission", reason.lower())

    def test_r4_invalid_parameters_and_wrong_types_rejected(self):
        """R4: Passing unexpected parameters, missing required parameters, or wrong runtime types must fail."""
        # 1. Unknown parameter
        is_valid, reason = self.registry.validate_call(
            tool_name="perform_web_forensic_search",
            agent_role="Data Analyst",
            args={"query": "test query", "unexpected_exploit_param": "payload"},
        )
        self.assertFalse(is_valid)
        self.assertIn("Unknown parameter", reason)

        # 2. Missing required parameter
        is_valid, reason = self.registry.validate_call(
            tool_name="perform_web_forensic_search",
            agent_role="Data Analyst",
            args={},
        )
        self.assertFalse(is_valid)
        self.assertIn("Missing required parameter", reason)

        # 3. Wrong argument type (int instead of str, bool instead of int, etc.)
        is_valid, reason = self.registry.validate_call(
            tool_name="perform_web_forensic_search",
            agent_role="Data Analyst",
            args={"query": 12345},
        )
        self.assertFalse(is_valid)
        self.assertIn("invalid type", reason.lower())

        is_valid, reason = self.registry.validate_call(
            tool_name="update_ssot",
            agent_role="Terminal Orchestrator",
            args={"payload_json": ["LIST_INSTEAD_OF_JSON_STR"]},
        )
        self.assertFalse(is_valid)
        self.assertIn("invalid type", reason.lower())

    # --- R2: Local Owner Confirmation & Self-Approval Prevention ---

    def test_r2_no_hardcoded_approval_token_in_update_rules_schema(self):
        """R2: update_rules must not accept an approval_token parameter that an LLM could pass."""
        tool_def = self.registry.get("update_rules")
        self.assertIsNotNone(tool_def)
        self.assertNotIn("approval_token", tool_def.parameters)

        # Attempting to pass approval_token in args is rejected as an unknown parameter
        is_valid, reason = self.registry.validate_call(
            tool_name="update_rules",
            agent_role="Context Engine",
            args={"rules_md_content": "# Rules", "approval_token": "ANY_TOKEN"},
        )
        self.assertFalse(is_valid)
        self.assertIn("Unknown parameter: 'approval_token'", reason)

    def test_r2_update_rules_requires_local_owner_confirmation(self):
        """R2: Updating canonical rules requires explicit local owner confirmation bound to operation & payload."""
        rules_payload = "# New Canonical Rules"

        # 1. Without owner confirmation -> Rejected
        is_valid, reason = self.registry.validate_call(
            tool_name="update_rules",
            agent_role="Context Engine",
            args={"rules_md_content": rules_payload},
        )
        self.assertFalse(is_valid)
        self.assertIn("owner confirmation required", reason.lower())

        # 2. Grant one-time owner approval for a DIFFERENT payload -> Rejected for rules_payload
        GLOBAL_OWNER_APPROVAL.grant_one_time_approval(
            operation_name="update_rules",
            target_resource="gem_trading_rules/rules.md",
            payload="# Different Payload",
        )
        is_valid, reason = self.registry.validate_call(
            tool_name="update_rules",
            agent_role="Context Engine",
            args={"rules_md_content": rules_payload},
        )
        self.assertFalse(is_valid)

        # 3. Grant one-time owner approval for exact payload -> Allowed on validate_call, consumed on execute
        GLOBAL_OWNER_APPROVAL.grant_one_time_approval(
            operation_name="update_rules",
            target_resource="gem_trading_rules/rules.md",
            payload=rules_payload,
        )
        is_valid, reason = self.registry.validate_call(
            tool_name="update_rules",
            agent_role="Context Engine",
            args={"rules_md_content": rules_payload},
        )
        self.assertTrue(is_valid, f"Expected valid when owner approval granted: {reason}")

        # Consume the one-time approval via verify_or_prompt(consume=True)
        consumed, _ = GLOBAL_OWNER_APPROVAL.verify_or_prompt(
            operation_name="update_rules",
            target_resource="gem_trading_rules/rules.md",
            payload=rules_payload,
            consume=True,
        )
        self.assertTrue(consumed)

        # 4. Subsequent attempt with identical payload must fail because approval is one-time
        replay_ok, _ = GLOBAL_OWNER_APPROVAL.verify_or_prompt(
            operation_name="update_rules",
            target_resource="gem_trading_rules/rules.md",
            payload=rules_payload,
            consume=True,
        )
        self.assertFalse(replay_ok, "One-time owner approval must not be replayable")

    def test_r2_agent_cannot_self_approve(self):
        """R2: An executing agent role cannot call grant_one_time_approval or set_confirmation_callback."""
        with execution_role_context("Context Engine"):
            with self.assertRaises(PermissionError):
                GLOBAL_OWNER_APPROVAL.grant_one_time_approval(
                    operation_name="update_rules",
                    target_resource="gem_trading_rules/rules.md",
                    payload="# Self Approved Rules",
                )
            with self.assertRaises(PermissionError):
                GLOBAL_OWNER_APPROVAL.set_confirmation_callback(lambda op, tgt, d: True)

    def test_r2_owner_confirmation_callback_approve_and_decline(self):
        """R2: Owner confirmation callback can dynamically approve or decline sensitive mutations."""
        GLOBAL_OWNER_APPROVAL.set_confirmation_callback(lambda op, tgt, d: False)
        is_valid, _ = self.registry.validate_call(
            tool_name="update_rules",
            agent_role="Context Engine",
            args={"rules_md_content": "# Test"},
        )
        self.assertFalse(is_valid)

        GLOBAL_OWNER_APPROVAL.set_confirmation_callback(
            lambda op, tgt, d: op == "update_rules" and tgt == "gem_trading_rules/rules.md"
        )
        is_valid, reason = self.registry.validate_call(
            tool_name="update_rules",
            agent_role="Context Engine",
            args={"rules_md_content": "# Test"},
        )
        self.assertTrue(is_valid, f"Expected callback approval to succeed: {reason}")

    # --- R3: Direct Financial Mutation Protection Across All Entry Points ---

    def test_r3_direct_python_calls_blocked_for_unauthorized_roles(self):
        """R3: Direct Python calls to financial mutation functions must fail-closed for unauthorized roles."""
        import asyncio
        unauthorized_roles = ["Python Engineer", "Architecture Agent", "Data Analyst", "Bullish Advocate"]
        for role in unauthorized_roles:
            with execution_role_context(role):
                with self.assertRaises(PermissionError, msg=f"update_ssot should block {role}"):
                    tools.update_ssot('{"cash": 999999}')
                with self.assertRaises(PermissionError, msg=f"update_trade_lessons should block {role}"):
                    tools.update_trade_lessons("Unauthorized lesson")
                with self.assertRaises(PermissionError, msg=f"intercept_and_log_decision should block {role}"):
                    tools.intercept_and_log_decision('```json\n{"EXECUTION_PAYLOAD": {"new_decisions": [{"ticker": "SPY"}]}}\n```')
                with self.assertRaises(PermissionError, msg=f"update_rules should block {role}"):
                    tools.update_rules("# Unauthorized rules")
                with self.assertRaises(PermissionError, msg=f"handle_paste should block {role}"):
                    asyncio.run(fetch_stocks.handle_paste('{"cash": 999999}'))

    def test_r3_direct_update_rules_blocked_even_for_context_engine_without_owner_confirmation(self):
        """R3 + R2: Direct call to tools.update_rules by Context Engine without owner confirmation raises PermissionError."""
        with execution_role_context("Context Engine"):
            with self.assertRaises(PermissionError):
                tools.update_rules("# Unconfirmed Rules Mutation")

    def test_r3_role_spoofing_blocked_in_authorize_financial_mutation(self):
        """R3: An active agent context cannot spoof 'System' or another role when calling authorize_financial_mutation."""
        with execution_role_context("Python Engineer"):
            with self.assertRaises(PermissionError):
                authorize_financial_mutation(
                    operation_name="update_ssot",
                    target_resource="context/ssot.json",
                    caller_role="Terminal Orchestrator",
                )

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
