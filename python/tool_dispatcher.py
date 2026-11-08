"""
Shared Multi-Turn Tool Dispatcher Service
Governed by Stage 1 Section 6.2, Section 3.4, and Protocol 03.
Provides unified tool dispatching with loop bounds, cycle detection,
role authorization, and structured audit logging for CLI, Web, and Subagents.
"""

import time
import json
import hashlib
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass

from tool_registry import ToolRegistry, GLOBAL_TOOL_REGISTRY
from audit_logger import AuditLogger, AuditEvent, GLOBAL_AUDIT_LOGGER

MAX_TOOL_TURNS = 5


@dataclass
class ToolExecutionResult:
    tool_name: str
    args: Dict[str, Any]
    success: bool
    result: Any = None
    error: Optional[str] = None


class ToolDispatcher:
    """Unified service for dispatching tools with security enforcement and multi-turn loops."""

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        audit_logger: Optional[AuditLogger] = None,
        max_turns: int = MAX_TOOL_TURNS,
    ):
        self.registry = registry or GLOBAL_TOOL_REGISTRY
        self.audit_logger = audit_logger or GLOBAL_AUDIT_LOGGER
        self.max_turns = max_turns

    def dispatch_single_tool(
        self,
        tool_name: str,
        args: Dict[str, Any],
        agent_role: str,
        approval_token: Optional[str] = None,
        task_id: str = "default-task",
        agent_id: str = "default-agent",
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> ToolExecutionResult:
        """Validates and executes a single tool call with audit logging."""
        if cancel_check and cancel_check():
            return ToolExecutionResult(
                tool_name=tool_name,
                args=args,
                success=False,
                error="Operation cancelled by user.",
            )

        # 1. Validation & Permission Check
        is_valid, reason = self.registry.validate_call(
            tool_name=tool_name,
            agent_role=agent_role,
            args=args,
            approval_token=approval_token,
        )

        tool_def = self.registry.get(tool_name)
        action_cat = "FINANCIAL_WRITE" if (tool_def and tool_def.affects_financial_state) else "TOOL_CALL"

        if not is_valid:
            # Log denied event
            self.audit_logger.log_event(
                AuditEvent(
                    action_category=action_cat,
                    status="DENIED",
                    agent_role=agent_role,
                    agent_id=agent_id,
                    task_id=task_id,
                    tool_name=tool_name,
                    permission_decision="DENIED",
                    approval_status="UNAPPROVED" if "approval" in (reason or "").lower() else "NOT_REQUIRED",
                    error_classification="PERMISSION_DENIED",
                    details={"args": args, "rejection_reason": reason},
                )
            )
            return ToolExecutionResult(
                tool_name=tool_name,
                args=args,
                success=False,
                error=reason,
            )

        # 2. Execution under audit trail
        self.audit_logger.log_event(
            AuditEvent(
                action_category=action_cat,
                status="ATTEMPTED",
                agent_role=agent_role,
                agent_id=agent_id,
                task_id=task_id,
                tool_name=tool_name,
                permission_decision="GRANTED",
                approval_status="APPROVED" if approval_token else "NOT_REQUIRED",
                details={"args": args},
            )
        )

        try:
            start_time = time.time()
            result = self.registry.execute(
                tool_name=tool_name,
                agent_role=agent_role,
                args=args,
                approval_token=approval_token,
            )
            elapsed = time.time() - start_time

            self.audit_logger.log_event(
                AuditEvent(
                    action_category=action_cat,
                    status="SUCCESS",
                    agent_role=agent_role,
                    agent_id=agent_id,
                    task_id=task_id,
                    tool_name=tool_name,
                    permission_decision="GRANTED",
                    details={"latency_seconds": round(elapsed, 4)},
                )
            )
            return ToolExecutionResult(
                tool_name=tool_name,
                args=args,
                success=True,
                result=result,
            )
        except Exception as e:
            self.audit_logger.log_event(
                AuditEvent(
                    action_category=action_cat,
                    status="FAILED",
                    agent_role=agent_role,
                    agent_id=agent_id,
                    task_id=task_id,
                    tool_name=tool_name,
                    error_classification=type(e).__name__,
                    details={"error_message": str(e)},
                )
            )
            return ToolExecutionResult(
                tool_name=tool_name,
                args=args,
                success=False,
                error=str(e),
            )

    def execute_multi_turn_session(
        self,
        session: Any,
        initial_message: Any,
        part_factory: Any,
        agent_role: str = "Terminal Orchestrator",
        approval_token: Optional[str] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """
        Executes a multi-turn conversation turn with cycle detection,
        bounded loop limits (MAX_TOOL_TURNS), cancellation checks,
        and unified tool dispatching.
        """
        def log(msg: str):
            if log_callback:
                try:
                    log_callback(msg)
                except Exception:
                    pass

        current_message = initial_message
        all_text: List[str] = []
        turn_count = 0
        call_history_hashes: List[str] = []

        while True:
            if cancel_check and cancel_check():
                log("[ToolDispatcher] Operation cancelled by user.")
                return {
                    "status": "cancelled",
                    "response": "[OFFLINE] Session terminated by user interrupt.",
                    "turn_count": turn_count,
                }

            turn_count += 1
            if turn_count > self.max_turns:
                log(f"[ToolDispatcher] Warning: Maximum tool turns reached ({self.max_turns}). Halting loop.")
                break

            log(f"[ToolDispatcher] Starting turn {turn_count}/{self.max_turns}...")
            response = session.send_message(current_message)

            # Capture text content
            if hasattr(response, "text") and response.text:
                txt = response.text.strip()
                if txt:
                    all_text.append(txt)
            elif getattr(response, "candidates", None):
                try:
                    for part in response.candidates[0].content.parts:
                        if getattr(part, "text", None):
                            all_text.append(part.text.strip())
                except Exception:
                    pass

            # Check if function calls were requested
            function_calls = getattr(response, "function_calls", [])
            if not function_calls:
                # No more tools requested; conversation complete
                break

            log(f"[ToolDispatcher] Turn {turn_count} requested {len(function_calls)} tool(s).")
            tool_responses = []

            # Cycle detection fingerprint
            turn_sig = json.dumps(
                [{"name": fc.name, "args": getattr(fc, "args", {})} for fc in function_calls],
                sort_keys=True,
            )
            sig_hash = hashlib.md5(turn_sig.encode("utf-8")).hexdigest()
            if call_history_hashes.count(sig_hash) >= 2:
                log("[ToolDispatcher] Cycle detected: identical tool calls repeated 3 times. Breaking loop.")
                tool_responses.append(
                    part_factory.from_function_response(
                        name="cycle_guard",
                        response={"error": "Tool call cycle detected. Halting further calls."},
                    )
                )
                current_message = tool_responses
                break

            call_history_hashes.append(sig_hash)

            for call in function_calls:
                name = call.name
                args = getattr(call, "args", {}) or {}
                log(f"[ToolDispatcher] Executing tool: '{name}' (Role: {agent_role})")

                exec_result = self.dispatch_single_tool(
                    tool_name=name,
                    args=args,
                    agent_role=agent_role,
                    approval_token=approval_token,
                    cancel_check=cancel_check,
                )

                if exec_result.success:
                    tool_responses.append(
                        part_factory.from_function_response(
                            name=name,
                            response={"result": exec_result.result},
                        )
                    )
                else:
                    tool_responses.append(
                        part_factory.from_function_response(
                            name=name,
                            response={"error": exec_result.error},
                        )
                    )

            current_message = tool_responses

        final_response_text = "\n\n---\n\n".join(all_text)
        return {
            "status": "success",
            "response": final_response_text,
            "turn_count": turn_count,
            "captured_text": all_text,
        }


# Canonical global tool dispatcher instance
GLOBAL_TOOL_DISPATCHER = ToolDispatcher()
