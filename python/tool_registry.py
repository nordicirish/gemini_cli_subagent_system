"""
Centralized Tool Registry & Runtime Permission Enforcement
Governed by Protocol 03 (Security Policy) and Protocol 06 (Financial Boundaries).
"""

import inspect
import json
from dataclasses import dataclass, field
from typing import Callable, Any, Dict, List, Set, Optional

# Canonical role categories
ENGINEERING_ROLES: Set[str] = {
    "Architecture Agent",
    "Python Engineer",
    "Security Agent",
    "Testing Agent",
    "Documentation Agent",
    "Independent Reviewer",
}

INVESTMENT_ROLES: Set[str] = {
    "Terminal Orchestrator",
    "Bullish Advocate",
    "Red Team Pessimist",
    "Neutral Structuralist",
    "Macro Sentinel",
    "Data Analyst",
    "Macro Narrative Engine",
    "Research Engine",
    "Review Engine",
    "Sentiment Engine",
    "Structural Engine",
    "Rule Enforcer Engine",
    "Context Engine",
    "Execution Engine",
    "Technical Validator",
    "GEX Engine",
    "Regime Engine",
    "Strategy Engine",
    "State Validation Router",
}

ALL_VALID_ROLES: Set[str] = ENGINEERING_ROLES | INVESTMENT_ROLES | {"System", "Human"}

HUMAN_APPROVAL_TOKEN_RULES = "HUMAN_APPROVED_RULE_MUTATION"
HUMAN_APPROVAL_TOKEN_FINANCIAL = "HUMAN_APPROVED_FINANCIAL_OVERRIDE"


@dataclass
class ToolDefinition:
    name: str
    description: str
    func: Callable
    parameters: Dict[str, Any] = field(default_factory=dict)
    classification: str = "READ"  # "READ" or "WRITE"
    required_permission: str = "read_market"
    requires_human_approval: bool = False
    allowed_roles: Set[str] = field(default_factory=set)
    is_reversible: bool = True
    affects_financial_state: bool = False

    def validate_arguments(self, args: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validates incoming arguments against declared parameters and inspect signature."""
        sig = inspect.signature(self.func)
        bound_args = {}

        for param_name, param in sig.parameters.items():
            if param.default is inspect.Parameter.empty and param.kind not in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                if param_name not in args:
                    return False, f"Missing required parameter: '{param_name}'"

        for k in args:
            if k not in sig.parameters and not any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            ):
                return False, f"Unknown parameter: '{k}'"

        return True, None


class ToolRegistry:
    """Central registry enforcing runtime permissions and parameter validation."""

    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}

    def register(
        self,
        name: str,
        func: Callable,
        description: str = "",
        classification: str = "READ",
        required_permission: str = "read_market",
        requires_human_approval: bool = False,
        allowed_roles: Optional[Set[str]] = None,
        is_reversible: bool = True,
        affects_financial_state: bool = False,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> ToolDefinition:
        if allowed_roles is None:
            allowed_roles = set(ALL_VALID_ROLES)

        tool_def = ToolDefinition(
            name=name,
            description=description or getattr(func, "__doc__", "") or "",
            func=func,
            parameters=parameters or {},
            classification=classification,
            required_permission=required_permission,
            requires_human_approval=requires_human_approval,
            allowed_roles=allowed_roles,
            is_reversible=is_reversible,
            affects_financial_state=affects_financial_state,
        )
        self._tools[name] = tool_def
        return tool_def

    def get(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def list_tools(self) -> List[ToolDefinition]:
        return list(self._tools.values())

    def validate_call(
        self,
        tool_name: str,
        agent_role: str,
        args: Dict[str, Any],
        approval_token: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        """
        Validates whether a tool call is authorized, parameter-valid, and permitted.
        Enforces Protocol 03 and Protocol 06.
        """
        tool = self.get(tool_name)
        if not tool:
            return False, f"Tool '{tool_name}' is not registered."

        # 1. Financial state protection (Protocol 06)
        if tool.affects_financial_state and agent_role in ENGINEERING_ROLES:
            if approval_token != HUMAN_APPROVAL_TOKEN_FINANCIAL:
                return (
                    False,
                    f"Financial boundary violation: Engineering role '{agent_role}' cannot execute "
                    f"'{tool_name}' which modifies financial state without human authorization.",
                )

        # 2. Role validation
        if agent_role not in ALL_VALID_ROLES:
            return False, f"Role '{agent_role}' is not a recognized agent role."

        if agent_role not in tool.allowed_roles:
            return False, f"Role '{agent_role}' is not authorized to execute tool '{tool_name}'."

        # 3. Sensitive action human approval gate
        if tool.requires_human_approval:
            if tool_name == "update_rules":
                if approval_token != HUMAN_APPROVAL_TOKEN_RULES:
                    return (
                        False,
                        "Human approval required: 'update_rules' requires a verified human approval token.",
                    )
            elif not approval_token:
                return (
                    False,
                    f"Human approval required: Tool '{tool_name}' is sensitive and requires explicit human approval.",
                )

        # 4. Parameter schema validation
        valid_args, err = tool.validate_arguments(args)
        if not valid_args:
            return False, f"Invalid arguments for tool '{tool_name}': {err}"

        return True, None

    def execute(
        self,
        tool_name: str,
        agent_role: str,
        args: Dict[str, Any],
        approval_token: Optional[str] = None,
    ) -> Any:
        """Executes the tool after validating permissions fail-closed."""
        is_valid, reason = self.validate_call(tool_name, agent_role, args, approval_token)
        if not is_valid:
            raise PermissionError(reason)

        return self._tools[tool_name].func(**args)


# Global canonical tool registry instance
GLOBAL_TOOL_REGISTRY = ToolRegistry()
