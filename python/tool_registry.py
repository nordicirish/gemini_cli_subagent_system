"""
Centralized Tool Registry, Runtime Permission Enforcement & Local Owner Confirmation
Governed by Protocol 03 (Security Policy), Protocol 06 (Financial Boundaries), and Stage 1.1 R2/R3/R4.
"""

import sys
import json
import hashlib
import inspect
import threading
import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Any, Dict, List, Set, Optional, Tuple

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
    "Post-Trade Review Engine",
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

TRUSTED_SYSTEM_ROLES: Set[str] = {"System", "Human", "Owner"}

ALL_VALID_ROLES: Set[str] = ENGINEERING_ROLES | INVESTMENT_ROLES | TRUSTED_SYSTEM_ROLES

# Read-only permissions shared across analytical and engineering roles
READ_ONLY_PERMISSIONS: Set[str] = {
    "read_portfolio",
    "read_lessons",
    "read_decision_log",
    "read_market",
    "search_web",
}

# Explicit Role -> Allowed Tool Classifications ("READ", "WRITE")
ROLE_CLASSIFICATIONS: Dict[str, Set[str]] = {
    **{role: {"READ"} for role in ENGINEERING_ROLES},
    **{role: {"READ"} for role in INVESTMENT_ROLES},
    "Terminal Orchestrator": {"READ", "WRITE"},
    "State Validation Router": {"READ", "WRITE"},
    "Context Engine": {"READ", "WRITE"},
    "Review Engine": {"READ", "WRITE"},
    "Post-Trade Review Engine": {"READ", "WRITE"},
    "System": {"READ", "WRITE"},
    "Human": {"READ", "WRITE"},
    "Owner": {"READ", "WRITE"},
}

# Explicit Role -> Granted Permissions
ROLE_PERMISSIONS: Dict[str, Set[str]] = {
    **{role: set(READ_ONLY_PERMISSIONS) for role in ENGINEERING_ROLES},
    **{role: set(READ_ONLY_PERMISSIONS) | {"delegate_agent"} for role in INVESTMENT_ROLES},
    "Terminal Orchestrator": set(READ_ONLY_PERMISSIONS) | {
        "write_portfolio",
        "write_lessons",
        "write_decision_log",
        "delegate_agent",
    },
    "State Validation Router": set(READ_ONLY_PERMISSIONS) | {
        "write_portfolio",
        "write_decision_log",
    },
    "Context Engine": set(READ_ONLY_PERMISSIONS) | {
        "write_portfolio",
        "write_lessons",
        "write_decision_log",
        "write_rules",
    },
    "Review Engine": set(READ_ONLY_PERMISSIONS) | {
        "write_lessons",
        "write_decision_log",
    },
    "Post-Trade Review Engine": set(READ_ONLY_PERMISSIONS) | {
        "write_lessons",
        "write_decision_log",
    },
    "System": set(READ_ONLY_PERMISSIONS) | {
        "write_portfolio",
        "write_lessons",
        "write_decision_log",
        "write_rules",
        "delegate_agent",
    },
    "Human": set(READ_ONLY_PERMISSIONS) | {
        "write_portfolio",
        "write_lessons",
        "write_decision_log",
        "write_rules",
        "delegate_agent",
    },
    "Owner": set(READ_ONLY_PERMISSIONS) | {
        "write_portfolio",
        "write_lessons",
        "write_decision_log",
        "write_rules",
        "delegate_agent",
    },
}

# Allowed roles per financial/protected mutation operation
FINANCIAL_MUTATION_ALLOWED_ROLES: Dict[str, Set[str]] = {
    "update_ssot": {"Terminal Orchestrator", "State Validation Router", "Context Engine"} | TRUSTED_SYSTEM_ROLES,
    "update_trade_lessons": {
        "Terminal Orchestrator",
        "State Validation Router",
        "Context Engine",
        "Review Engine",
        "Post-Trade Review Engine",
    } | TRUSTED_SYSTEM_ROLES,
    "update_decision_log": {
        "Terminal Orchestrator",
        "State Validation Router",
        "Context Engine",
        "Review Engine",
        "Post-Trade Review Engine",
    } | TRUSTED_SYSTEM_ROLES,
    "update_rules": {"Context Engine"} | TRUSTED_SYSTEM_ROLES,
}

# Context variable tracking the currently executing caller role across call stacks
_ACTIVE_CALLER_ROLE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_ACTIVE_CALLER_ROLE", default="System"
)


def get_active_caller_role() -> str:
    """Returns the role of the currently executing caller (defaults to 'System' for owner/UI calls)."""
    return _ACTIVE_CALLER_ROLE.get()


@contextmanager
def execution_role_context(role: str):
    """Binds the active caller role for the duration of an agent or tool execution block."""
    token = _ACTIVE_CALLER_ROLE.set(role)
    try:
        yield
    finally:
        _ACTIVE_CALLER_ROLE.reset(token)


def compute_payload_digest(payload: Any) -> str:
    """Computes a canonical SHA-256 digest of a payload to bind owner approval to exact data."""
    if payload is None:
        raw = ""
    elif isinstance(payload, str):
        raw = payload
    else:
        try:
            raw = json.dumps(payload, sort_keys=True, default=str)
        except Exception:
            raw = str(payload)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class OwnerApprovalManager:
    """
    Local human-confirmation manager for protected operations (Stage 1.1 R2).
    - No hardcoded source-code tokens or agent-supplied approval strings.
    - Binds each approval to (operation_name, target_resource, payload_digest).
    - Consumes one-time approvals immediately upon execution so an approval for one
      operation cannot authorize unrelated operations or subsequent calls.
    - Prevents any non-owner agent role from granting approvals or setting callbacks.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._pending_grants: Set[Tuple[str, str, str]] = set()
        self._confirmation_callback: Optional[Callable[[str, str, str], bool]] = None
        self.interactive_cli_enabled: bool = False

    def _require_trusted_caller(self):
        caller = get_active_caller_role()
        if caller not in TRUSTED_SYSTEM_ROLES:
            raise PermissionError(
                f"Security violation: Agent role '{caller}' cannot grant or configure owner approvals."
            )

    def set_confirmation_callback(
        self, callback: Optional[Callable[[str, str, str], bool]]
    ) -> None:
        """Sets a trusted application-level owner confirmation callback(operation, target, digest) -> bool."""
        self._require_trusted_caller()
        with self._lock:
            self._confirmation_callback = callback

    @staticmethod
    def _normalize_resource(target_resource: str) -> str:
        return (target_resource or "").replace("\\", "/").strip()

    def grant_one_time_approval(
        self, operation_name: str, target_resource: str, payload: Any
    ) -> str:
        """
        Records a one-time owner approval bound to the specific operation, target resource, and payload.
        Can only be called from trusted application/owner context, never from an agent execution context.
        """
        self._require_trusted_caller()
        norm_target = self._normalize_resource(target_resource)
        digest = compute_payload_digest(payload)
        key = (operation_name, norm_target, digest)
        with self._lock:
            self._pending_grants.add(key)
        return digest

    def clear_approvals(self) -> None:
        """Clears all pending one-time approvals."""
        self._require_trusted_caller()
        with self._lock:
            self._pending_grants.clear()

    def reset(self) -> None:
        """Resets pending grants, confirmation callback, and interactive CLI state."""
        self._require_trusted_caller()
        with self._lock:
            self._pending_grants.clear()
            self._confirmation_callback = None
            self.interactive_cli_enabled = False

    def verify_or_prompt(
        self,
        operation_name: str,
        target_resource: str,
        payload: Any,
        consume: bool = True,
    ) -> tuple[bool, Optional[str]]:
        """
        Verifies whether owner confirmation has been granted for (operation_name, target_resource, payload).
        If not pre-granted, invokes the configured owner confirmation callback or interactive CLI prompt.
        When consume=True, the approval is consumed so it cannot be reused.
        """
        norm_target = self._normalize_resource(target_resource)
        digest = compute_payload_digest(payload)
        key = (operation_name, norm_target, digest)

        with self._lock:
            if key in self._pending_grants:
                if consume:
                    self._pending_grants.remove(key)
                return True, None
            callback = self._confirmation_callback

        # Check trusted owner confirmation callback if configured
        if callback is not None:
            try:
                approved = bool(callback(operation_name, norm_target, digest))
            except Exception as e:
                return False, f"Owner confirmation callback failed: {e}"
            if approved:
                if not consume:
                    with self._lock:
                        self._pending_grants.add(key)
                return True, None
            return (
                False,
                f"Owner confirmation declined for operation '{operation_name}' on '{target_resource}'.",
            )

        # Check interactive terminal prompt if explicitly enabled and attached to a TTY
        if self.interactive_cli_enabled and sys.stdin is not None and sys.stdin.isatty():
            try:
                prompt_msg = (
                    f"\n[OWNER CONFIRMATION REQUIRED] Operation '{operation_name}' "
                    f"on '{target_resource}' (sha256: {digest[:12]}...). Approve? [y/N]: "
                )
                ans = input(prompt_msg).strip().lower()
                if ans in ("y", "yes"):
                    if not consume:
                        with self._lock:
                            self._pending_grants.add(key)
                    return True, None
                return (
                    False,
                    f"Owner declined interactive confirmation for '{operation_name}'.",
                )
            except Exception as e:
                return False, f"Interactive owner confirmation failed or cancelled: {e}"

        return (
            False,
            f"Human owner confirmation required for '{operation_name}' on '{target_resource}'. "
            "No valid owner confirmation was provided.",
        )


GLOBAL_OWNER_APPROVAL = OwnerApprovalManager()


def authorize_financial_mutation(
    operation_name: str,
    target_resource: str,
    payload: Any = None,
    caller_role: Optional[str] = None,
    requires_owner_approval: bool = False,
) -> None:
    """
    Central authorization boundary for all financial-state and trading-rule mutations (Stage 1.1 R3).
    Enforced directly inside every mutation function so direct Python calls, API routes,
    subagents, and tool dispatches cannot bypass protection.
    Raises PermissionError if unauthorized.
    """
    active_context_role = get_active_caller_role()
    # If an agent is active in the execution context, it cannot spoof a different caller_role
    if active_context_role not in TRUSTED_SYSTEM_ROLES:
        effective_role = active_context_role
    else:
        effective_role = caller_role or active_context_role

    if effective_role not in ALL_VALID_ROLES:
        raise PermissionError(
            f"Unauthorized financial mutation '{operation_name}': unrecognized role '{effective_role}'."
        )

    if effective_role in ENGINEERING_ROLES:
        raise PermissionError(
            f"Financial boundary violation: Engineering role '{effective_role}' is forbidden from "
            f"executing '{operation_name}' on '{target_resource}'."
        )

    allowed_roles = FINANCIAL_MUTATION_ALLOWED_ROLES.get(operation_name)
    if allowed_roles is None or effective_role not in allowed_roles:
        raise PermissionError(
            f"Role '{effective_role}' is not authorized to perform financial mutation '{operation_name}' "
            f"on '{target_resource}'."
        )

    if requires_owner_approval or operation_name == "update_rules":
        approved, reason = GLOBAL_OWNER_APPROVAL.verify_or_prompt(
            operation_name=operation_name,
            target_resource=target_resource,
            payload=payload,
            consume=True,
        )
        if not approved:
            raise PermissionError(f"Unauthorized '{operation_name}': {reason}")


def _matches_expected_type(value: Any, expected_type: Any) -> bool:
    """Checks whether a value matches the declared expected type (handling bool vs int strictly)."""
    if expected_type is None or expected_type is Any:
        return True
    if expected_type is int and isinstance(value, bool):
        return False
    if expected_type is float and isinstance(value, int) and not isinstance(value, bool):
        return True
    try:
        return isinstance(value, expected_type)
    except TypeError:
        return True


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
    target_resource: str = ""

    def validate_arguments(self, args: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validates incoming arguments against declared parameter types and function signature (Stage 1.1 R4)."""
        if not isinstance(args, dict):
            return False, f"Arguments must be a dict, got {type(args).__name__}"

        sig = inspect.signature(self.func)
        accepts_var_kw = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )

        # 1. Check required parameters from function signature
        for param_name, param in sig.parameters.items():
            if param.default is inspect.Parameter.empty and param.kind not in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                if param_name not in args:
                    return False, f"Missing required parameter: '{param_name}'"
                if args[param_name] is None:
                    return False, f"Required parameter '{param_name}' cannot be None"

        # 2. Check required parameters declared in self.parameters metadata
        for declared_name, declared_spec in self.parameters.items():
            sig_param = sig.parameters.get(declared_name)
            is_optional = (
                sig_param is not None and sig_param.default is not inspect.Parameter.empty
            )
            if not is_optional and declared_name not in args:
                return False, f"Missing required parameter: '{declared_name}'"

        # 3. Reject unknown parameters and validate argument types
        for k, v in args.items():
            if k not in sig.parameters and not accepts_var_kw:
                return False, f"Unknown parameter: '{k}'"

            # Check type against self.parameters metadata if declared
            expected_type = self.parameters.get(k)
            if expected_type is None and k in sig.parameters:
                ann = sig.parameters[k].annotation
                if ann is not inspect.Parameter.empty and isinstance(ann, type):
                    expected_type = ann

            if expected_type is not None:
                sig_param = sig.parameters.get(k)
                allows_none = (
                    sig_param is not None
                    and sig_param.default is None
                    and sig_param.default is not inspect.Parameter.empty
                )
                if v is None and allows_none:
                    continue
                if not _matches_expected_type(v, expected_type):
                    type_name = getattr(expected_type, "__name__", str(expected_type))
                    return (
                        False,
                        f"Invalid type for parameter '{k}': expected {type_name}, got {type(v).__name__}",
                    )

        return True, None


class ToolRegistry:
    """Central registry enforcing runtime permissions, classifications, owner confirmation, and parameter validation."""

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
        target_resource: str = "",
    ) -> ToolDefinition:
        if allowed_roles is None:
            allowed_roles = set(ALL_VALID_ROLES)

        tool_def = ToolDefinition(
            name=name,
            description=description or getattr(func, "__doc__", "") or "",
            func=func,
            parameters=parameters or {},
            classification=classification.upper(),
            required_permission=required_permission,
            requires_human_approval=requires_human_approval,
            allowed_roles=allowed_roles,
            is_reversible=is_reversible,
            affects_financial_state=affects_financial_state,
            target_resource=target_resource or name,
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
        consume_approval: bool = False,
    ) -> tuple[bool, Optional[str]]:
        """
        Validates whether a tool call is authorized, classification-permitted, permission-valid,
        argument-valid, and owner-approved (when required).
        Enforces Protocol 03, Protocol 06, and Stage 1.1 R2/R3/R4.
        """
        tool = self.get(tool_name)
        if not tool:
            return False, f"Tool '{tool_name}' is not registered."

        # 1. Recognized role check
        if agent_role not in ALL_VALID_ROLES:
            return False, f"Role '{agent_role}' is not a recognized agent role."

        # 2. Financial state boundary check (Protocol 06)
        if tool.affects_financial_state and agent_role in ENGINEERING_ROLES:
            return (
                False,
                f"Financial boundary violation: Engineering role '{agent_role}' cannot execute "
                f"'{tool_name}' which modifies financial state.",
            )

        # 3. Tool classification enforcement ("READ" vs "WRITE")
        allowed_classes = ROLE_CLASSIFICATIONS.get(agent_role, set())
        if tool.classification not in allowed_classes:
            return (
                False,
                f"Classification violation: Role '{agent_role}' is not permitted to execute "
                f"'{tool.classification}' tool '{tool_name}'.",
            )

        # 4. Required permission enforcement
        granted_perms = ROLE_PERMISSIONS.get(agent_role, set())
        if tool.required_permission not in granted_perms:
            return (
                False,
                f"Permission denied: Role '{agent_role}' lacks required permission "
                f"'{tool.required_permission}' for tool '{tool_name}'.",
            )

        # 5. Tool-specific allowed_roles check
        if agent_role not in tool.allowed_roles:
            return False, f"Role '{agent_role}' is not authorized to execute tool '{tool_name}'."

        # 6. Parameter schema and type validation
        valid_args, err = tool.validate_arguments(args)
        if not valid_args:
            return False, f"Invalid arguments for tool '{tool_name}': {err}"

        # 7. Owner confirmation check for sensitive operations (R2)
        if tool.requires_human_approval:
            # Extract primary payload for digest binding (e.g., rules_md_content if single arg, else args dict)
            payload_for_hash = (
                args.get("rules_md_content")
                if "rules_md_content" in args and len(args) == 1
                else args
            )
            approved, reason = GLOBAL_OWNER_APPROVAL.verify_or_prompt(
                operation_name=tool_name,
                target_resource=tool.target_resource,
                payload=payload_for_hash,
                consume=consume_approval,
            )
            if not approved:
                return False, f"Human approval required: {reason}"

        return True, None

    def execute(
        self,
        tool_name: str,
        agent_role: str,
        args: Dict[str, Any],
    ) -> Any:
        """
        Executes the tool inside execution_role_context(agent_role) after validating permissions fail-closed.
        Note: For tools that also call authorize_financial_mutation internally (like update_rules),
        we validate without consuming the approval here so the internal boundary consumes it once.
        """
        tool = self.get(tool_name)
        # Consume approval here only if the tool does not delegate consumption to authorize_financial_mutation
        consume_in_registry = bool(
            tool and tool.requires_human_approval and not tool.affects_financial_state
        )
        is_valid, reason = self.validate_call(
            tool_name, agent_role, args, consume_approval=consume_in_registry
        )
        if not is_valid:
            raise PermissionError(reason)

        with execution_role_context(agent_role):
            return self._tools[tool_name].func(**args)


# Global canonical tool registry instance
GLOBAL_TOOL_REGISTRY = ToolRegistry()
