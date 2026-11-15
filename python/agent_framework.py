import os
import re
import json
import time
import hashlib
import requests
import subprocess
import signal
import sys
import threading
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import concurrent.futures
import logging
from logging.handlers import TimedRotatingFileHandler

try:
    from tool_registry import (
        FINANCIAL_MUTATION_ALLOWED_ROLES,
        GLOBAL_TOOL_REGISTRY,
        ROLE_CLASSIFICATIONS,
        TRUSTED_SYSTEM_ROLES,
        execution_role_context,
    )
except ImportError:
    from python.tool_registry import (
        FINANCIAL_MUTATION_ALLOWED_ROLES,
        GLOBAL_TOOL_REGISTRY,
        ROLE_CLASSIFICATIONS,
        TRUSTED_SYSTEM_ROLES,
        execution_role_context,
    )


MAX_PARALLEL_COUNCIL_WORKERS = 8

APPROVED_PARALLEL_COUNCIL_ROLES: Set[str] = frozenset({
    "Macro Sentinel",
    "Research Engine",
    "Macro Narrative Engine",
    "Data Analyst",
    "Rule Enforcer Engine",
    "Execution Engine",
    "GEX Engine",
    "Sentiment Engine",
    "Structural Engine",
    "Bullish Advocate",
    "Red Team Pessimist",
    "Neutral Structuralist",
    "Technical Validator",
    "Regime Engine",
    "Strategy Engine",
})

APPROVED_PARALLEL_COUNCIL_TOOLS: Set[str] = frozenset({
    f"ask_{role.lower().replace(' ', '_')}"
    for role in APPROVED_PARALLEL_COUNCIL_ROLES
})

APPROVED_PARALLEL_TOOL_TO_ROLE: Dict[str, str] = {
    f"ask_{role.lower().replace(' ', '_')}": role
    for role in APPROVED_PARALLEL_COUNCIL_ROLES
}

FORBIDDEN_PARALLEL_MUTATING_ROLES: Set[str] = frozenset({
    "Terminal Orchestrator",
    "State Validation Router",
    "Context Engine",
    "Review Engine",
    "Post-Trade Review Engine",
})

FORBIDDEN_PARALLEL_MUTATING_TOOLS: Set[str] = frozenset({
    "ask_terminal_orchestrator",
    "ask_state_validation_router",
    "ask_context_engine",
    "ask_review_engine",
    "ask_post_trade_review_engine",
    "ask_post-trade_review_engine",
    "ask_post_trade_review",
    "update_ssot",
    "update_trade_lessons",
    "update_rules",
    "update_decision_log",
    "intercept_and_log_decision",
})

MUTATING_TOOL_NAMES: Set[str] = frozenset({
    "update_ssot",
    "update_trade_lessons",
    "update_rules",
    "update_decision_log",
    "intercept_and_log_decision",
})


def _has_mutating_tool_capability(
    agent_role: Optional[str], agent_tools: Optional[List[Any]]
) -> bool:
    """Returns True if the agent role or any assigned tool carries financial write/mutation capabilities."""
    if agent_role:
        if agent_role in FORBIDDEN_PARALLEL_MUTATING_ROLES:
            return True
        if "WRITE" in ROLE_CLASSIFICATIONS.get(agent_role, set()):
            return True
        for allowed in FINANCIAL_MUTATION_ALLOWED_ROLES.values():
            if agent_role in (allowed - TRUSTED_SYSTEM_ROLES):
                return True

    if agent_tools:
        for tool in agent_tools:
            tool_name = tool if isinstance(tool, str) else getattr(tool, "__name__", "")
            if tool_name in MUTATING_TOOL_NAMES:
                return True
            reg_def = GLOBAL_TOOL_REGISTRY.get(tool_name)
            if reg_def is not None and (
                reg_def.classification != "READ" or reg_def.affects_financial_state
            ):
                return True

    return False


def _normalize_parallel_agent_identifier(raw_name: Any) -> Tuple[str, str]:
    """Validates and normalizes a parallel agent key to (short_name, 'ask_' + short_name)."""
    if not isinstance(raw_name, str):
        raise ValueError("Parallel council agent name must be a non-empty string.")
    if not raw_name or raw_name != raw_name.strip():
        raise ValueError(f"Malformed parallel council agent name '{raw_name}'.")
    if not re.fullmatch(r"[a-z0-9_]+", raw_name):
        raise ValueError(f"Malformed parallel council agent name '{raw_name}'.")
    short_name = raw_name[4:] if raw_name.startswith("ask_") else raw_name
    if not short_name:
        raise ValueError(f"Malformed parallel council agent name '{raw_name}'.")
    return short_name, f"ask_{short_name}"


def _iter_wrapped_callables(func: Any) -> List[Any]:
    """Returns func followed by wrapped inner targets via functools.partial .func or __wrapped__."""
    chain: List[Any] = []
    seen_ids: Set[int] = set()
    curr = func
    while curr is not None and id(curr) not in seen_ids and len(chain) < 8:
        seen_ids.add(id(curr))
        chain.append(curr)
        next_target = getattr(curr, "func", None)
        if next_target is None or next_target is curr:
            next_target = getattr(curr, "__wrapped__", None)
        curr = next_target
    return chain


def _resolve_agent_role(func: Any, tool_name: str) -> Optional[str]:
    for target in _iter_wrapped_callables(func):
        role = getattr(target, "_agent_role", None)
        if role:
            return role
    return APPROVED_PARALLEL_TOOL_TO_ROLE.get(tool_name)


def _validate_parallel_agent_callable(short_name: str, tool_name: str, func: Any) -> None:
    """Fails closed if func is not callable, has mutation capabilities, or is not an approved read-only agent."""
    if not callable(func):
        raise ValueError(f"Parallel council target '{short_name}' is not callable.")

    if tool_name in FORBIDDEN_PARALLEL_MUTATING_TOOLS or short_name in FORBIDDEN_PARALLEL_MUTATING_TOOLS:
        raise PermissionError(
            f"Agent '{short_name}' ({tool_name}) has financial mutation or write capabilities "
            "and is forbidden from parallel council execution."
        )

    for target in _iter_wrapped_callables(func):
        role = getattr(target, "_agent_role", None)
        tools_list = getattr(target, "_agent_tools", None)
        is_read_only = getattr(target, "_is_read_only_deliberative", True)
        if (
            role in FORBIDDEN_PARALLEL_MUTATING_ROLES
            or is_read_only is False
            or _has_mutating_tool_capability(role, tools_list)
        ):
            raise PermissionError(
                f"Agent '{short_name}' ({tool_name}) has financial mutation or write capabilities "
                "and is forbidden from parallel council execution."
            )

    if tool_name not in APPROVED_PARALLEL_COUNCIL_TOOLS:
        raise PermissionError(
            f"Agent '{short_name}' ({tool_name}) is not an approved read-only deliberative council agent."
        )


def _parse_unique_json_object(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    """JSON object_pairs_hook that rejects duplicate keys fail-closed while preserving insertion order."""
    seen: Set[str] = set()
    result: Dict[str, Any] = {}
    for k, v in pairs:
        if k in seen:
            raise ValueError(f"Duplicate agent key '{k}' in parallel council request.")
        seen.add(k)
        result[k] = v
    return result
# Lightweight standalone Part class for payload encapsulation
class Part:
    def __init__(self, text: str = None, function_response=None, inline_data=None):
        self.text = text
        self.function_response = function_response
        self.inline_data = inline_data

    @classmethod
    def from_text(cls, text: str):
        return cls(text=text)

    @classmethod
    def from_function_response(cls, name: str, response: dict):
        fn_resp = SimpleNamespace(name=name, response=response)
        return cls(function_response=fn_resp)

    @classmethod
    def from_bytes(cls, data: bytes, mime_type: str = "image/png"):
        return cls(inline_data=SimpleNamespace(data=data, mime_type=mime_type))

types = SimpleNamespace(Part=Part)

# ---------------------------------------------------------------------------
# Antigravity App Agent Runtime (Local agy CLI integration)
# ---------------------------------------------------------------------------
class AntigravityResponse:
    def __init__(
        self,
        text: str,
        prompt_tokens: int = 0,
        candidate_tokens: int = 0,
        model_name: str = "gemini-3.8-flash",
        is_estimated: bool = True,
        token_accounting_mode: Optional[str] = None,
    ):
        self.text = text
        self.function_calls = []
        part = SimpleNamespace(text=text)
        content = SimpleNamespace(parts=[part])
        candidate = SimpleNamespace(content=content, finish_reason="STOP", safety_ratings=[])
        self.candidates = [candidate]
        self.is_estimated = bool(is_estimated)
        resolved_mode = token_accounting_mode or (
            "heuristic_estimate" if self.is_estimated else "provider_measured"
        )
        self.token_accounting_mode = resolved_mode
        self.usage_metadata = SimpleNamespace(
            prompt_token_count=prompt_tokens,
            candidates_token_count=candidate_tokens,
            cached_content_token_count=0,
            estimated_prompt_tokens=prompt_tokens if self.is_estimated else None,
            estimated_candidate_tokens=candidate_tokens if self.is_estimated else None,
            actual_prompt_tokens=None if self.is_estimated else prompt_tokens,
            actual_candidate_tokens=None if self.is_estimated else candidate_tokens,
            is_estimated=self.is_estimated,
            token_accounting_mode=resolved_mode,
        )
        self.model_name = model_name

    def __str__(self):
        return self.text


class SubprocessCleanupError(RuntimeError):
    """Raised when subprocess tree cleanup or reaping fails."""
    pass


def _get_windows_descendant_pids(parent_pid: int) -> Tuple[List[int], bool]:
    """
    Finds all descendant PIDs for parent_pid on Windows using Toolhelp snapshot.
    Returns (descendant_pids, success).
    Explicitly declares ctypes argtypes and restype for pointer-sized 64-bit/32-bit Windows integrity.
    If snapshot creation or enumeration fails, returns ([], False).
    """
    if sys.platform != "win32" or not parent_pid:
        return [], True
    descendants: List[int] = []
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_char * 260),
            ]

        kernel32 = ctypes.windll.kernel32

        # Explicitly declare ctypes argtypes and restype for pointer-sized HANDLE definitions
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE

        kernel32.Process32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
        kernel32.Process32First.restype = wintypes.BOOL

        kernel32.Process32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
        kernel32.Process32Next.restype = wintypes.BOOL

        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        kernel32.GetLastError.argtypes = []
        kernel32.GetLastError.restype = wintypes.DWORD

        # INVALID_HANDLE_VALUE is pointer-sized ((HANDLE)(LONG_PTR)-1)
        invalid_handle_val = wintypes.HANDLE(-1).value

        # TH32CS_SNAPPROCESS = 0x00000002
        h_snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        h_val = getattr(h_snapshot, "value", h_snapshot)
        if h_val is None or h_val == 0 or h_val == invalid_handle_val or h_snapshot == -1:
            err = kernel32.GetLastError()
            logging.getLogger("agent_framework").error(
                f"[WindowsToolhelp] Failed to create Toolhelp snapshot for PID {parent_pid} (error {err})"
            )
            return [], False

        enumeration_ok = False
        try:
            pe = PROCESSENTRY32()
            pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
            tree: Dict[int, List[int]] = {}

            if not kernel32.Process32First(h_snapshot, ctypes.byref(pe)):
                err = kernel32.GetLastError()
                # ERROR_NO_MORE_FILES = 18 indicates empty snapshot
                if err != 18:
                    logging.getLogger("agent_framework").error(
                        f"[WindowsToolhelp] Process32First failed during descendant enumeration for PID {parent_pid} (error {err})"
                    )
                    return [], False
            else:
                while True:
                    c_pid = int(pe.th32ProcessID)
                    p_pid = int(pe.th32ParentProcessID)
                    if c_pid != 0:
                        tree.setdefault(p_pid, []).append(c_pid)
                    if not kernel32.Process32Next(h_snapshot, ctypes.byref(pe)):
                        err = kernel32.GetLastError()
                        # ERROR_NO_MORE_FILES = 18 indicates normal enumeration exhaustion
                        if err != 18:
                            logging.getLogger("agent_framework").error(
                                f"[WindowsToolhelp] Process32Next aborted unexpectedly with error code {err} for PID {parent_pid}"
                            )
                            return [], False
                        break

            # Safe parent-to-child breadth-first traversal
            queue = list(tree.get(parent_pid, []))
            visited = set()
            while queue:
                curr = queue.pop(0)
                if curr not in visited and curr != parent_pid:
                    visited.add(curr)
                    descendants.append(curr)
                    queue.extend(tree.get(curr, []))

            enumeration_ok = True
        finally:
            close_ok = kernel32.CloseHandle(h_snapshot)
            if not close_ok:
                close_err = kernel32.GetLastError()
                logging.getLogger("agent_framework").error(
                    f"[WindowsToolhelp] CloseHandle failed for snapshot handle on PID {parent_pid} (error {close_err})"
                )
                enumeration_ok = False

        return (descendants, True) if enumeration_ok else ([], False)
    except Exception as ex:
        logging.getLogger("agent_framework").error(
            f"[WindowsToolhelp] Exception during descendant enumeration for PID {parent_pid}: {ex}"
        )
        return [], False


def _cleanup_subprocess(
    proc: subprocess.Popen,
    graceful_timeout: float = 0.2,
    raise_on_error: bool = False
) -> bool:
    """
    Guarantees deterministic termination, reaping, and stream cleanup for proc and its descendants.
    1. Request termination safely:
       - Never use os.killpg unless child was created in a dedicated process group and ownership is established.
       - Never signal the GEM application's own process group.
       - Terminate descendants even when direct child exits before them.
    2. Wait bounded graceful termination period.
    3. Force-kill if necessary, including process tree descendants.
    4. Explicitly wait/reap the direct child.
    5. Close or release associated pipes and streams.
    6. Preserve primary in-flight exception while reporting cleanup failures.
    Returns True if process was cleanly reaped and streams closed, False otherwise.
    """
    if proc is None or getattr(proc, "_cleaned_up", False) is True:
        return True

    caller_exc = sys.exc_info()[1]
    pid = getattr(proc, "pid", None)
    dedicated_pgid = getattr(proc, "_dedicated_pgid", None)
    reaped = False
    descendants_cleaned = True
    cleanup_success = True
    cleanup_errors: List[str] = []

    try:
        poll_fn = getattr(proc, "poll", None)
        is_alive = True
        if callable(poll_fn):
            try:
                is_alive = (proc.poll() is None)
            except Exception:
                is_alive = True

        # Safety check: Verify we never signal our own process group on POSIX
        safe_to_killpg = False
        if dedicated_pgid is not None and hasattr(os, "killpg"):
            try:
                current_pgrp = os.getpgrp() if hasattr(os, "getpgrp") else os.getpgid(0)
                if dedicated_pgid > 1 and dedicated_pgid != current_pgrp:
                    safe_to_killpg = True
            except Exception:
                safe_to_killpg = False

        sig_kill = getattr(signal, "SIGKILL", 9)
        sig_term = getattr(signal, "SIGTERM", 15)

        if is_alive:
            # 1. Graceful termination request
            if safe_to_killpg:
                try:
                    os.killpg(dedicated_pgid, sig_term)
                except (ProcessLookupError, PermissionError):
                    pass
                except Exception as ex:
                    cleanup_errors.append(f"killpg SIGTERM failed: {ex}")

            try:
                proc.terminate()
            except (ProcessLookupError, PermissionError):
                pass
            except Exception as ex:
                cleanup_errors.append(f"proc.terminate failed: {ex}")

            # 2. Wait bounded graceful period
            try:
                proc.wait(timeout=graceful_timeout)
                reaped = True
            except (subprocess.TimeoutExpired, Exception):
                reaped = False

        # If direct child has exited, poll() is not None, but descendants might still survive
        # 3. Force-kill descendants and process if not reaped or if descendants may survive
        if not reaped and is_alive:
            if pid and sys.platform == "win32":
                try:
                    res = subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(pid)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5.0
                    )
                    if res.returncode not in (0, 128):
                        cleanup_success = False
                        cleanup_errors.append(f"taskkill /T failed on PID {pid} (exit {res.returncode})")
                except Exception as ex:
                    cleanup_success = False
                    cleanup_errors.append(f"taskkill exception on PID {pid}: {ex}")
            elif safe_to_killpg:
                try:
                    os.killpg(dedicated_pgid, sig_kill)
                except (ProcessLookupError, PermissionError):
                    pass
                except Exception as ex:
                    cleanup_success = False
                    cleanup_errors.append(f"killpg SIGKILL failed: {ex}")

            try:
                proc.kill()
            except (ProcessLookupError, PermissionError):
                pass
            except Exception as ex:
                cleanup_errors.append(f"proc.kill failed: {ex}")

        # 3b. Ensure descendants are terminated even when the direct child exits before them:
        if safe_to_killpg:
            try:
                os.killpg(dedicated_pgid, sig_kill)
            except (ProcessLookupError, PermissionError):
                pass
            except Exception as ex:
                descendants_cleaned = False
                cleanup_success = False
                cleanup_errors.append(f"killpg SIGKILL to descendants failed: {ex}")

            # Verify process group is confirmed absent
            try:
                os.killpg(dedicated_pgid, 0)
                # If call succeeds, processes still exist in the group!
                descendants_cleaned = False
                cleanup_success = False
                cleanup_errors.append(f"Process group {dedicated_pgid} still contains active processes")
                logging.getLogger("agent_framework").error(
                    f"[SubprocessCleanup] POSIX process group {dedicated_pgid} still active after termination"
                )
            except ProcessLookupError:
                # Confirmed absent
                pass
            except Exception as ex:
                descendants_cleaned = False
                cleanup_success = False
                cleanup_errors.append(f"os.killpg(pgid, 0) check failed: {ex}")

        elif pid and sys.platform == "win32":
            descendants, enum_ok = _get_windows_descendant_pids(pid)
            if not enum_ok:
                descendants_cleaned = False
                cleanup_success = False
                cleanup_errors.append(f"Descendant check failed for PID {pid}")
            for d_pid in descendants:
                try:
                    res = subprocess.run(
                        ["taskkill", "/F", "/PID", str(d_pid)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=2.0
                    )
                    if res.returncode not in (0, 128):
                        descendants_cleaned = False
                        cleanup_success = False
                        cleanup_errors.append(f"taskkill failed on PID {d_pid} (exit {res.returncode})")
                except Exception as ex:
                    descendants_cleaned = False
                    cleanup_success = False
                    cleanup_errors.append(f"taskkill exception on PID {d_pid}: {ex}")

        # 4. Explicitly wait/reap the direct child
        if not reaped:
            try:
                proc.wait(timeout=0.2)
                reaped = True
            except Exception:
                reaped = (proc.poll() is not None) if callable(getattr(proc, "poll", None)) else False

        if not reaped:
            cleanup_success = False
            cleanup_errors.append(f"Direct child PID {pid} could not be reaped")

    except Exception as ex:
        cleanup_success = False
        cleanup_errors.append(f"Subprocess cleanup exception: {ex}")
    finally:
        # 5. Close/release associated pipes and streams
        stream_names = [("stdin", getattr(proc, "stdin", None)),
                        ("stdout", getattr(proc, "stdout", None)),
                        ("stderr", getattr(proc, "stderr", None))]
        for name, stream in stream_names:
            if stream is not None:
                try:
                    stream.close()
                except Exception as ex:
                    cleanup_success = False
                    cleanup_errors.append(f"Failed to close {name}: {ex}")
                    logging.getLogger("agent_framework").error(f"[SubprocessCleanup] Stream closure failed: {ex}")

    # _cleaned_up may ONLY be set after all required cleanup conditions have been established
    if cleanup_success and reaped and descendants_cleaned:
        try:
            proc._cleaned_up = True
        except Exception:
            pass
    else:
        cleanup_success = False
        err_detail = f": {'; '.join(cleanup_errors)}" if cleanup_errors else ""
        if caller_exc is not None:
            logging.getLogger("agent_framework").error(
                f"[SubprocessCleanup] Process PID {pid} could not be cleanly terminated/reaped{err_detail}; primary exception preserved."
            )
        elif raise_on_error:
            raise SubprocessCleanupError(f"Subprocess PID {pid} could not be cleanly terminated or reaped{err_detail}")

    return cleanup_success

    return cleanup_success


class AntigravityAgentRunner:
    def __init__(self, agy_path=None, cancel_check=None):
        if agy_path and os.path.exists(agy_path):
            self.agy_path = agy_path
        else:
            candidates = [
                os.path.expanduser(r"~\.gemini\bin\agy.exe"),
                r"C:\Users\roger\.gemini\bin\agy.exe",
            ]
            self.agy_path = next((c for c in candidates if os.path.exists(c)), "agy")
        self.cancel_check = cancel_check
        self.is_available = os.path.exists(self.agy_path) or bool(os.environ.get("AGY_PATH"))

    def run_prompt(
        self,
        prompt: str,
        system_instruction: str = "",
        mode: str = "FAST",
        tools=None,
        timeout: int = 120,
        cancel_check: Optional[Callable[[], bool]] = None
    ) -> AntigravityResponse:
        effective_cancel_check = cancel_check if cancel_check is not None else self.cancel_check
        if effective_cancel_check and effective_cancel_check():
            raise RuntimeError("Operation cancelled by user.")

        # Map execution mode to Antigravity model tiers
        if mode in ("PRO", "THINKING"):
            model = "gemini-3.8-flash-high"
        else:
            model = "gemini-3.8-flash-low"

        # Output-formatting instruction (NOT a security control).
        # Actual execution restrictions are enforced by CLI flags (--sandbox,
        # --disable-slash-commands), headless permission auto-denial, and Python
        # ToolRegistry / authorize_financial_mutation boundaries.
        analytical_output_instruction = (
            "ANALYTICAL OUTPUT INSTRUCTION: You are an analytical financial intelligence engine. "
            "Do NOT attempt to execute host shell commands, local workspace edits, or terminal tasks. "
            "Output all analyses, reasoning, and JSON payloads directly as text."
        )

        full_prompt = f"{analytical_output_instruction}\n\nTask / User Prompt:\n{prompt}\n"
        if system_instruction:
            full_prompt = f"{analytical_output_instruction}\n\nSystem Instructions:\n{system_instruction}\n\nTask / User Prompt:\n{prompt}\n"

        cmd = [
            self.agy_path,
            "--sandbox",
            "--disable-slash-commands",
            "--model", model,
        ]

        popen_kwargs = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
        }
        if sys.platform != "win32":
            popen_kwargs["start_new_session"] = True

        proc = subprocess.Popen(
            cmd,
            **popen_kwargs
        )
        if sys.platform != "win32":
            proc._dedicated_pgid = proc.pid

        out = ""
        err = ""
        timed_out = False
        cancelled = False

        try:
            out_holder: List[str] = []
            err_holder: List[str] = []
            exc_holder: List[Exception] = []

            def comm_worker():
                try:
                    o, e = proc.communicate(input=full_prompt)
                    out_holder.append(o)
                    err_holder.append(e)
                except Exception as ex:
                    exc_holder.append(ex)

            comm_thread = threading.Thread(target=comm_worker, daemon=True)
            comm_thread.start()

            start_time = time.monotonic()
            poll_interval = 0.05

            while comm_thread.is_alive():
                # Check cancellation while running
                if effective_cancel_check and effective_cancel_check():
                    cancelled = True
                    break

                elapsed = time.monotonic() - start_time
                if timeout is not None and elapsed > timeout:
                    timed_out = True
                    break

                comm_thread.join(timeout=poll_interval)

            if cancelled:
                _cleanup_subprocess(proc)
                comm_thread.join(timeout=0.1)
                raise RuntimeError("Operation cancelled by user.")

            if timed_out:
                _cleanup_subprocess(proc)
                comm_thread.join(timeout=0.1)
                raise TimeoutError(f"Antigravity Agent timed out after {timeout}s.")

            if exc_holder:
                raise exc_holder[0]

            out = out_holder[0] if out_holder else ""
            err = err_holder[0] if err_holder else ""

        finally:
            _cleanup_subprocess(proc)

        if proc.returncode != 0:
            raise RuntimeError(f"Antigravity Agent failed (code {proc.returncode}): {err or out}")

        if "auto-denied" in (err or "").lower() and not out.strip():
            raise PermissionError(f"Antigravity Security Boundary: Subprocess attempted unauthorized host tool: {err.strip()}")

        output_text = out.strip()
        p_tokens = len(full_prompt) // 4
        c_tokens = len(output_text) // 4
        return AntigravityResponse(
            output_text,
            prompt_tokens=p_tokens,
            candidate_tokens=c_tokens,
            model_name=model,
            is_estimated=True,
            token_accounting_mode="heuristic_estimate",
        )


MAX_SUBAGENT_TOOL_TURNS = 3


class AntigravityChatSession:
    """Session adapter allowing the Orchestrator and Subagents to run natively on Antigravity with bounded multi-turn tool dispatch."""
    def __init__(
        self,
        runner: AntigravityAgentRunner,
        sys_instruction: str,
        tools: list,
        mode: str = "PRO",
        generate_fn=None,
        is_subagent: bool = False,
    ):
        self.runner = runner
        self.sys_instruction = sys_instruction
        self.tools = tools or []
        self.tool_map = {getattr(t, "__name__", str(t)): t for t in self.tools}
        self.mode = mode
        self.generate_fn = generate_fn
        self.is_subagent = is_subagent
        self.history = []

    def get_history(self):
        return self.history

    def send_message(self, message):
        # Format available tools
        tool_desc = []
        for name, fn in self.tool_map.items():
            doc = getattr(fn, "__doc__", "") or f"Tool {name}"
            tool_desc.append(f"- {name}: {doc.strip()}")
        tools_block = "Available Tools:\n" + "\n".join(tool_desc) if tool_desc else ""

        # Format incoming user prompt or function returns
        if isinstance(message, list):
            res_parts = []
            for item in message:
                if hasattr(item, "function_response"):
                    res_parts.append(f"Tool Result [{item.function_response.name}]: {item.function_response.response}")
                elif hasattr(item, "text"):
                    res_parts.append(item.text)
                else:
                    res_parts.append(str(item))
            if self.is_subagent:
                user_input = (
                    "Sub-tool execution results:\n"
                    + "\n\n".join(res_parts)
                    + "\n\nPlease synthesize these results into your final analysis, or request additional tools if required."
                )
            else:
                user_input = (
                    "Tool execution results:\n"
                    + "\n\n".join(res_parts)
                    + "\n\nPlease synthesize these results into the final Markdown report and EXECUTION_PAYLOAD, or request additional tools."
                )
        else:
            user_input = str(message)

        closing_instruction = (
            "Otherwise, output your synthesized text analysis directly."
            if self.is_subagent
            else "Otherwise, output the complete single-pass Markdown analysis followed by the single valid ```json EXECUTION_PAYLOAD ``` block."
        )

        full_sys = (
            f"{self.sys_instruction}\n\n"
            f"{tools_block}\n\n"
            "If you need to query subagents or execute tools, output ONLY a JSON block specifying function calls:\n"
            "```json\n"
            "{\n"
            '  "function_calls": [\n'
            '    {"name": "tool_name", "args": {"query": "..."}}\n'
            "  ]\n"
            "}\n"
            "```\n"
            f"{closing_instruction}"
        )

        if self.generate_fn is not None:
            resp = self.generate_fn(user_input, full_sys, self.mode)
        else:
            resp = self.runner.run_prompt(user_input, system_instruction=full_sys, mode=self.mode)

        text = ""
        if hasattr(resp, "text") and resp.text:
            text = resp.text
        elif getattr(resp, "candidates", None):
            try:
                for part in resp.candidates[0].content.parts:
                    if getattr(part, "text", None):
                        text += part.text
            except Exception:
                pass
        if not hasattr(resp, "text") or not resp.text:
            resp.text = text
        if not hasattr(resp, "function_calls"):
            resp.function_calls = []

        # Detect tool call requests
        fc_match = re.search(r"```json\s*(\{[\s\S]*?\"function_calls\"[\s\S]*?\})\s*```", text, re.IGNORECASE)
        if fc_match:
            try:
                parsed = json.loads(fc_match.group(1))
                calls = []
                for fc in parsed.get("function_calls", []):
                    name = fc.get("name")
                    args = fc.get("args", {})
                    calls.append(SimpleNamespace(name=name, args=args))
                resp.function_calls = calls
            except Exception:
                pass

        self.history.append(SimpleNamespace(parts=[SimpleNamespace(text=text)]))
        return resp

# ---------------------------------------------------------------------------
# Rolling LLM Handshake Logger (5 Days Rolling)
# ---------------------------------------------------------------------------
os.makedirs("logs", exist_ok=True)
handshake_log_file = os.path.join("logs", "gem_handshakes.log")

# Configure a file handler rotating daily, keeping 5 backups
handshake_handler = TimedRotatingFileHandler(
    handshake_log_file,
    when="D",
    interval=1,
    backupCount=5,
    encoding="utf-8"
)
handshake_handler.setLevel(logging.INFO)
handshake_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handshake_handler.setFormatter(handshake_formatter)

llm_logger = logging.getLogger("gem_handshakes")
llm_logger.setLevel(logging.INFO)
# Clear existing handlers to prevent duplicate logs on reload
if llm_logger.hasHandlers():
    llm_logger.handlers.clear()
llm_logger.addHandler(handshake_handler)
# Prevent propagation to parent loggers to avoid flooding console
llm_logger.propagate = False

# ---------------------------------------------------------------------------
# Model definitions — Antigravity Native Engine
# ---------------------------------------------------------------------------
DEFAULT_MODEL_PRO = "gemini-3.8-flash-high"
DEFAULT_MODEL_FLASH = "gemini-3.8-flash-low"
DEFAULT_MODEL_GEMMA = "gemini-3.8-flash-low"
DEFAULT_MODEL_THINKING = "gemini-3.8-flash-high"

MODEL_MAPPING = {
    "PRO":      ["gemini-3.8-flash-high", "gemini-4-argon-high"],
    "FLASH":    ["gemini-3.8-flash-low"],
    "GEMMA":    ["gemini-3.8-flash-low"],
    "THINKING": ["gemini-3.8-flash-high"],
    "FAST":     ["gemini-3.8-flash-low"],
}

CACHE_VERSION = "GEM_CACHE_v11.56-Pure-Antigravity-Sync"



# ---------------------------------------------------------------------------
# Main framework
# ---------------------------------------------------------------------------
class AgentFramework:
    def __init__(self, log_callback=None):
        self._local_config = self._load_local_config()
        self.log_callback = log_callback
        self.agents = {}
        
        # Telemetry
        self.session_cost = 0.0
        self.reset_turn_usage()

        # Pure Antigravity App Agent Runtime (Zero Cloud Dependency)
        self.backend = "antigravity"
        self.antigravity_runner = AntigravityAgentRunner(
            cancel_check=lambda: getattr(self, "cancel_check", lambda: False)()
        )
        if not self.antigravity_runner.is_available:
            raise RuntimeError(
                f"Antigravity CLI binary was not found at {self.antigravity_runner.agy_path}. "
                "Antigravity desktop environment is required."
            )

        self.log(f"[Framework] Pure Antigravity App Agent Engine active: {self.antigravity_runner.agy_path} (Cloud Gemini completely decoupled)")

    def create_chat_session(self, sys_instruction: str, tools: list, model_name: str = None):
        """Creates an AntigravityChatSession for the Master Orchestrator."""
        return AntigravityChatSession(self.antigravity_runner, sys_instruction, tools)

    def load_chart_parts(self) -> list:
        """Load latest 1-minute chart screenshots from context/charts/ as multimodal Part instances."""
        import glob
        chart_parts = []
        chart_dir = "context/charts"
        if os.path.exists(chart_dir):
            for filepath in sorted(glob.glob(os.path.join(chart_dir, "chart_*_1m.png"))):
                try:
                    with open(filepath, "rb") as f:
                        data = f.read()
                        if data:
                            chart_parts.append(
                                types.Part.from_bytes(data=data, mime_type="image/png")
                            )
                except Exception:
                    pass
        return chart_parts

    def _resolve_orchestrator(self) -> str:
        """Returns the primary Antigravity Orchestrator model."""
        return self._local_config.get("ANTIGRAVITY_MODEL_PRO", "gemini-3.8-flash-high")

    def _minify_payload(self, text: str) -> str:
        # Strip HTML/Markdown comments
        text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
        # Strip trailing spaces/tabs on multiline
        text = re.sub(r'[ \t]+$', '', text, flags=re.MULTILINE)
        # Collapse excessive blank lines (3 or more down to exactly 2)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text

    def _get_sys_instruction(self, base_instruction: str) -> str:
        """Appends the full rules documentation to the system instruction without duplicating DATA_PACKET lessons."""
        parts = [base_instruction]
        rules_path = os.path.join("gem_trading_rules", "rules.md")
        if os.path.exists(rules_path):
            try:
                with open(rules_path, "r", encoding="utf-8") as f:
                    raw_rules = f.read()
                    parts.append(f"\n\n--- ATTACHED KNOWLEDGE BASE (GEM_Rules_Data) ---\n{self._minify_payload(raw_rules)}")
            except Exception as e:
                self.log(f"[Warning] Failed to read rules.md: {e}")
        return "".join(parts)

    def _log_handshake(self, direction, model_name, client_label, prompt, config, response=None, error=None, latency=None):
        """Record structured LLM interactions to gem_handshakes.log"""
        try:
            p_len = len(str(prompt))
            instruct_len = 0
            if config:
                if getattr(config, "system_instruction", None):
                    instruct_len = len(str(config.system_instruction))

            latency_str = f", Latency: {latency:.2f}s" if latency is not None else ""
            
            if direction == "REQUEST":
                msg = (
                    f"[{direction}] Model: {model_name} ({client_label}) | "
                    f"Prompt Len: {p_len}, Instruct Len: {instruct_len}"
                )
                llm_logger.info(msg)
            elif direction == "RESPONSE" and response:
                t_p, t_c = 0, 0
                is_estimated = getattr(response, "is_estimated", True)
                accounting_mode = getattr(
                    response,
                    "token_accounting_mode",
                    "heuristic_estimate" if is_estimated else "provider_measured",
                )
                if hasattr(response, 'usage_metadata') and response.usage_metadata:
                    t_p = response.usage_metadata.prompt_token_count or 0
                    t_c = response.usage_metadata.candidates_token_count or 0
                    is_estimated = getattr(response.usage_metadata, "is_estimated", is_estimated)
                    accounting_mode = getattr(response.usage_metadata, "token_accounting_mode", accounting_mode)

                token_label = "Estimated Tokens (chars//4)" if is_estimated else "Measured Tokens"
                text_preview = "No text output"
                try:
                    if hasattr(response, "text") and response.text:
                        text_preview = response.text[:300].replace("\n", " ") + "..."
                    elif response.candidates and response.candidates[0].content.parts:
                        parts_text = "".join(part.text for part in response.candidates[0].content.parts if part.text)
                        if parts_text:
                            text_preview = parts_text[:300].replace("\n", " ") + "..."
                except Exception:
                    pass

                msg = (
                    f"[{direction}] Model: {model_name} ({client_label}) | Status: SUCCESS{latency_str} | "
                    f"{token_label} [{accounting_mode}]: [Prompt: {t_p}, Candidate: {t_c}, Cached: 0] | "
                    f"Preview: {text_preview}"
                )
                llm_logger.info(msg)
            elif direction == "WARNING":
                msg = f"[{direction}] Model: {model_name} ({client_label}) | Msg: {error}"
                llm_logger.warning(msg)
            elif direction == "ERROR":
                msg = f"[{direction}] Model: {model_name} ({client_label}) | Status: FAILED{latency_str} | Error: {error}"
                llm_logger.error(msg)
        except Exception as e:
            self.log(f"[Logging Error] Failed to write to handshake log: {e}")

    def reset_turn_usage(self):
        """Clear token counters for a new chat session turn."""
        self._has_estimated_usage = False
        self._has_measured_usage = False
        self.turn_usage = {
            'prompt_tokens': 0,
            'candidates_tokens': 0,
            'cached_tokens': 0,
            'estimated_prompt_tokens': 0,
            'estimated_candidates_tokens': 0,
            'actual_prompt_tokens': None,
            'actual_candidates_tokens': None,
            'is_estimated': True,
            'token_accounting_mode': 'heuristic_estimate',
            'caching_active': False,
            'estimated_cost': 0.0,
        }

    def _calculate_call_cost(self, model_name, client_label, input_tokens, output_tokens, cached_tokens=0) -> float:
        norm_model = model_name.replace("models/", "")
        if "antigravity" in norm_model.lower():
            return 0.0
        if norm_model == "gemini-3.5-flash":
            return (input_tokens / 1_000_000.0) * 1.50 + (cached_tokens / 1_000_000.0) * 0.375 + (output_tokens / 1_000_000.0) * 9.00
        if norm_model == "gemini-3.1-flash-lite":
            if "free" in client_label.lower():
                return 0.0
            return (input_tokens / 1_000_000.0) * 0.25 + (cached_tokens / 1_000_000.0) * 0.0625 + (output_tokens / 1_000_000.0) * 1.50
        return 0.0

    def log(self, message: str):
        """Helper to print to console and send to callback if exists."""
        print(message)
        if self.log_callback:
            try:
                self.log_callback(message)
            except Exception:
                pass

    def _load_local_config(self) -> dict:
        """Read config.json for settings (fails silently with defaults)."""
        try:
            with open("context/config.json", "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def load_system_instruction(self, file_path: str) -> str:
        """Loads a file and formats it as a string for the system prompt.
        Prefers .md directly; falls back .json → .md if needed."""
        # Prefer .md over .json (canonical format per v10.02)
        if not os.path.exists(file_path):
            # Try .md sibling
            alt_md = file_path.replace(".json", ".md")
            alt_json = file_path.replace(".md", ".json")
            if os.path.exists(alt_md):
                file_path = alt_md
            elif os.path.exists(alt_json):
                file_path = alt_json
            else:
                raise FileNotFoundError(f"Missing instruction file: {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        if file_path.endswith(".json"):
            try:
                return json.dumps(json.loads(content), indent=2)
            except json.JSONDecodeError:
                return content
        else:
            return content



    # -----------------------------------------------------------------------
    # Core generation method — Pure Antigravity App Agent Engine
    # -----------------------------------------------------------------------
    def generate_response_with_fallback(self, prompt, instruction, mode, tools=None):
        """Generate a response using the local Antigravity App Agent runtime."""
        if getattr(self, "cancel_check", None) and self.cancel_check():
            self.log("[Framework] Cancel signal active before starting call.")
            raise RuntimeError("Operation cancelled by user.")

        target_model = "gemini-3.8-flash-high" if mode in ("PRO", "THINKING") else "gemini-3.8-flash-low"
        self.log(f"[Antigravity App Agent] Delegating turn with model: {target_model} (mode: {mode})...")
        start_time = time.time()
        try:
            self._log_handshake("REQUEST", target_model, "Antigravity Local", prompt, None)
            resp = self.antigravity_runner.run_prompt(prompt, system_instruction=instruction, mode=mode, tools=tools)
            elapsed = time.time() - start_time
            self._log_handshake("RESPONSE", target_model, "Antigravity Local", prompt, None, response=resp, latency=elapsed)

            # Zero marginal cost for local Antigravity sessions; distinguish heuristic estimates from measured usage
            usage_meta = getattr(resp, "usage_metadata", None)
            p_count = (getattr(usage_meta, "prompt_token_count", 0) or 0) if usage_meta else 0
            c_count = (getattr(usage_meta, "candidates_token_count", 0) or 0) if usage_meta else 0
            resp_is_estimated = getattr(
                usage_meta,
                "is_estimated",
                getattr(resp, "is_estimated", True),
            )
            resp_mode = getattr(
                usage_meta,
                "token_accounting_mode",
                getattr(
                    resp,
                    "token_accounting_mode",
                    "heuristic_estimate" if resp_is_estimated else "provider_measured",
                ),
            )

            lock = getattr(self, "_usage_lock", None)
            if lock is None:
                lock = threading.Lock()
                self._usage_lock = lock
            with lock:
                self.turn_usage['prompt_tokens'] += p_count
                self.turn_usage['candidates_tokens'] += c_count
                self.turn_usage['cached_tokens'] = 0
                self.turn_usage['caching_active'] = False
                if resp_is_estimated:
                    self._has_estimated_usage = True
                    self.turn_usage['estimated_prompt_tokens'] = (self.turn_usage.get('estimated_prompt_tokens') or 0) + p_count
                    self.turn_usage['estimated_candidates_tokens'] = (self.turn_usage.get('estimated_candidates_tokens') or 0) + c_count
                else:
                    self._has_measured_usage = True
                    self.turn_usage['actual_prompt_tokens'] = (self.turn_usage.get('actual_prompt_tokens') or 0) + p_count
                    self.turn_usage['actual_candidates_tokens'] = (self.turn_usage.get('actual_candidates_tokens') or 0) + c_count

                has_est = getattr(self, "_has_estimated_usage", False)
                has_meas = getattr(self, "_has_measured_usage", False)
                if has_est and has_meas:
                    self.turn_usage['is_estimated'] = True
                    self.turn_usage['token_accounting_mode'] = 'mixed_estimated_and_measured'
                elif has_meas:
                    self.turn_usage['is_estimated'] = False
                    self.turn_usage['token_accounting_mode'] = resp_mode or 'provider_measured'
                else:
                    self.turn_usage['is_estimated'] = True
                    self.turn_usage['token_accounting_mode'] = resp_mode or 'heuristic_estimate'
            return resp
        except Exception as e:
            elapsed = time.time() - start_time
            self._log_handshake("ERROR", target_model, "Antigravity Local", prompt, None, error=str(e), latency=elapsed)
            self.log(f"[Antigravity Error] Agent call failed: {e}")
            raise e

    def load_temporal_anchor_prompt(self, ny_iso: str, current_year: int) -> str:
        """Loads the temporal anchor prompt from prompts/temporal_anchor_prompt.txt and templates the parameters."""
        prompt_path = "prompts/temporal_anchor_prompt.txt"
        template = ""
        if os.path.exists(prompt_path):
            try:
                with open(prompt_path, "r", encoding="utf-8") as f:
                    template = f.read().strip()
            except Exception: pass
        if not template:
            template = (
                "[SYSTEM_TIME (NEW YORK / ET): {ny_iso}]\n"
                "[PRODUCTION_ERA: {current_year}]\n"
                "[MANDATE]: The current year provided in the timestamp is the LIVE PRODUCTION ERA. "
                "Treat all data from this era as grounded reality, not a simulation."
            )
        return "\n\n" + template.format(ny_iso=ny_iso, current_year=current_year)

    def create_agent_tool(self, name, file_path, mode="PRO", agent_tools=None):
        instruction = self.load_system_instruction(file_path)

        import datetime
        # Force US/Eastern (New York) Time — UTC-4 (EDT)
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-4)))
        ny_iso = now.strftime("%Y-%m-%dT%H:%M:%S")
        current_year = now.year

        temporal_anchor = self.load_temporal_anchor_prompt(ny_iso, current_year)
        final_instruction = instruction + temporal_anchor

        def call_subagent(query: str) -> str:
            with execution_role_context(name):
                self.log(f"\n[Orchestrator -> {name}] Delegating: {query[:100]}...")

                # Programmatic payload slicing based on agent role
                sliced_query = query
                try:
                    json_blocks = re.findall(r"```json\s*(.*?)\s*```", query, re.DOTALL | re.IGNORECASE)
                    if not json_blocks:
                        json_blocks = re.findall(r"({[\s\S]*?})", query)

                    for block in json_blocks:
                        try:
                            data = json.loads(block.strip())
                            if isinstance(data, dict) and ("tickers" in data or "ssot" in data):
                                sliced_data = {}
                                if "ssot" in data:
                                    sliced_data["ssot"] = data["ssot"]

                                if name == "GEX Engine":
                                    if "tickers" in data:
                                        sliced_data["tickers"] = [
                                            {
                                                "ticker": t.get("ticker"),
                                                "price": t.get("price"),
                                                "dealer_posture": t.get("dealer_posture"),
                                                "net_gex_total": t.get("net_gex_total"),
                                                "gamma_flip_price": t.get("gamma_flip_price"),
                                                "atr_percent": t.get("atr_percent"),
                                            }
                                            for t in data["tickers"]
                                        ]
                                elif name in ["Macro Sentinel", "Sentiment Engine"]:
                                    if "tickers" in data:
                                        sliced_data["tickers"] = [
                                            {
                                                "ticker": t.get("ticker"),
                                                "price": t.get("price"),
                                                "rsi": t.get("rsi"),
                                                "trend": t.get("trend"),
                                                "signal": t.get("signal"),
                                                "note": t.get("note"),
                                            }
                                            for t in data["tickers"]
                                        ]
                                elif name == "Technical Validator":
                                    sliced_data["rules"] = "Strict JSON schema formatting rules: Output MUST be a single valid JSON block complying with the schema. No markdown formatting outside of JSON."
                                    if "tickers" in data:
                                        sliced_data["tickers"] = [
                                            {
                                                "ticker": t.get("ticker"),
                                                "price": t.get("price"),
                                                "vwap": t.get("vwap"),
                                            }
                                            for t in data["tickers"]
                                        ]
                                else:
                                    continue

                                sliced_query = sliced_query.replace(block, json.dumps(sliced_data, indent=2))
                        except Exception:
                            pass
                except Exception as e:
                    self.log(f"[Warning] Slicing failure: {e}")

                if agent_tools:
                    from tool_dispatcher import GLOBAL_TOOL_DISPATCHER
                    allowed_tool_names = {getattr(t, "__name__", str(t)) for t in agent_tools}
                    sub_session = AntigravityChatSession(
                        runner=self.antigravity_runner,
                        sys_instruction=final_instruction,
                        tools=agent_tools,
                        mode=mode,
                        generate_fn=lambda p, sys_inst, m: self.generate_response_with_fallback(
                            prompt=p,
                            instruction=sys_inst,
                            mode=m,
                            tools=agent_tools,
                        ),
                        is_subagent=True,
                    )
                    dispatch_res = GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session(
                        session=sub_session,
                        initial_message=f"[SYSTEM_TIME (NEW YORK / ET): {ny_iso}] {sliced_query}",
                        part_factory=types.Part,
                        agent_role=name,
                        cancel_check=lambda: getattr(self, "cancel_check", lambda: False)(),
                        log_callback=self.log,
                        allowed_tool_names=allowed_tool_names,
                        max_turns_override=MAX_SUBAGENT_TOOL_TURNS,
                    )
                    if dispatch_res.get("status") == "cancelled":
                        return dispatch_res.get("response", "[OFFLINE] Session terminated by user interrupt.")

                    captured_text = dispatch_res.get("captured_text", [])
                    non_fc_texts = []
                    for txt in captured_text:
                        stripped = re.sub(
                            r"```json\s*(\{[\s\S]*?\"function_calls\"[\s\S]*?\})\s*```",
                            "",
                            txt,
                            flags=re.IGNORECASE,
                        ).strip()
                        if stripped:
                            non_fc_texts.append(stripped)

                    if non_fc_texts:
                        return "\n\n---\n\n".join(non_fc_texts)
                    return dispatch_res.get("response", "")

                response = self.generate_response_with_fallback(
                    prompt=f"[SYSTEM_TIME (NEW YORK / ET): {ny_iso}] {sliced_query}",
                    instruction=final_instruction,
                    mode=mode,
                    tools=agent_tools,
                )
                res_text = ""
                try:
                    for part in response.candidates[0].content.parts:
                        if part.text:
                            res_text += part.text
                except Exception:
                    if hasattr(response, "text"):
                        res_text = response.text

                return res_text or str(response)

        tool_name = f"ask_{name.lower().replace(' ', '_')}"
        call_subagent.__name__ = tool_name
        call_subagent.__doc__ = f"Ask the {name} sub-agent."
        call_subagent._agent_role = name
        call_subagent._agent_tools = list(agent_tools or [])
        call_subagent._is_read_only_deliberative = (
            name in APPROVED_PARALLEL_COUNCIL_ROLES
            and not _has_mutating_tool_capability(name, agent_tools)
        )
        GLOBAL_TOOL_REGISTRY.register(
            name=tool_name,
            func=call_subagent,
            description=call_subagent.__doc__,
            classification="READ",
            required_permission="delegate_agent",
            requires_human_approval=False,
            allowed_roles={"Terminal Orchestrator"} | TRUSTED_SYSTEM_ROLES,
            affects_financial_state=False,
            parameters={"query": str},
            target_resource=f"subagent:{name}",
        )
        return call_subagent

    def execute_ephemeral_batch(self, parallel_tasks: dict) -> dict:
        """
        Executes a validated batch of strictly read-only deliberative sub-agent tasks concurrently
        via bounded ThreadPoolExecutor while preserving caller key ordering and worker failure isolation.
        """
        if not isinstance(parallel_tasks, dict) or not parallel_tasks:
            raise ValueError("parallel_tasks must be a non-empty dictionary.")

        seen_canonical: Set[str] = set()
        validated_entries: List[Tuple[str, str, Callable]] = []
        for agent_key, task_fn in parallel_tasks.items():
            short_name, tool_name = _normalize_parallel_agent_identifier(agent_key)
            if short_name in seen_canonical:
                raise ValueError(f"Duplicate normalized parallel agent '{agent_key}'.")
            seen_canonical.add(short_name)
            _validate_parallel_agent_callable(short_name, tool_name, task_fn)
            validated_entries.append((agent_key, tool_name, task_fn))

        cancel_fn = getattr(self, "cancel_check", None)
        if cancel_fn and cancel_fn():
            return {
                agent_key: "[OFFLINE] Session terminated by user interrupt."
                for agent_key in parallel_tasks.keys()
            }

        self.log(
            f"[Antigravity Parallel Batch] Executing {len(validated_entries)} tasks concurrently via Antigravity Agents..."
        )
        raw_results: Dict[str, Any] = {}
        max_workers = min(len(validated_entries), MAX_PARALLEL_COUNCIL_WORKERS)
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        try:
            def _run_isolated(tool_name: str, fn: Callable) -> Any:
                if cancel_fn and cancel_fn():
                    return "[OFFLINE] Session terminated by user interrupt."
                role = _resolve_agent_role(fn, tool_name)
                if role:
                    with execution_role_context(role):
                        return fn()
                return fn()

            future_to_agent = {
                executor.submit(_run_isolated, tool_name, fn): agent_key
                for agent_key, tool_name, fn in validated_entries
            }
            pending = set(future_to_agent.keys())
            cancelled_mid_batch = False

            while pending:
                if cancel_fn and cancel_fn():
                    cancelled_mid_batch = True
                    for f in pending:
                        f.cancel()
                    break
                done, pending = concurrent.futures.wait(
                    pending,
                    timeout=0.05,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for future in done:
                    agent_key = future_to_agent[future]
                    try:
                        raw_results[agent_key] = future.result()
                    except concurrent.futures.CancelledError:
                        raw_results[agent_key] = "[OFFLINE] Session terminated by user interrupt."
                    except Exception as exc:
                        raw_results[agent_key] = f"Error: {exc}"

            if cancelled_mid_batch and pending:
                for future in list(pending):
                    agent_key = future_to_agent[future]
                    if future.cancelled():
                        raw_results[agent_key] = "[OFFLINE] Session terminated by user interrupt."
                    else:
                        try:
                            raw_results[agent_key] = future.result(timeout=0.5)
                        except Exception:
                            raw_results[agent_key] = "[OFFLINE] Session terminated by user interrupt."
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

        return {
            agent_key: raw_results.get(agent_key, "[OFFLINE] Session terminated by user interrupt.")
            for agent_key in parallel_tasks.keys()
        }

    def create_parallel_council_tool(self, agents_dict: dict):
        """
        Builds and registers the ask_council parallel dispatcher restricted strictly to
        explicitly approved read-only deliberative council agents.
        """
        if not isinstance(agents_dict, dict) or not agents_dict:
            raise ValueError("agents_dict must be a non-empty dictionary.")

        approved_agents: Dict[str, Callable] = {}
        excluded_agent_tools: Set[str] = set()
        seen_init_tools: Set[str] = set()

        for raw_key, target_fn in agents_dict.items():
            short_name, tool_name = _normalize_parallel_agent_identifier(raw_key)
            if tool_name in seen_init_tools:
                raise ValueError(f"Duplicate agent definition for '{tool_name}' in agents_dict.")
            seen_init_tools.add(tool_name)
            try:
                _validate_parallel_agent_callable(short_name, tool_name, target_fn)
                approved_agents[tool_name] = target_fn
            except PermissionError:
                excluded_agent_tools.add(tool_name)
                self.log(
                    f"[Security] Excluded non-read-only or unapproved agent '{raw_key}' from parallel council tool."
                )

        def ask_council(queries_json: str) -> str:
            """Dispatch approved read-only deliberative council agents in parallel.
            queries_json: A JSON object mapping approved agent names (e.g. 'bullish_advocate', 'red_team_pessimist') to non-empty query strings.
            """
            if not isinstance(queries_json, str) or not queries_json.strip():
                return json.dumps({"error": "Invalid queries_json: expected a non-empty JSON object string."})

            cleaned = queries_json.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            elif cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

            try:
                queries = json.loads(cleaned, object_pairs_hook=_parse_unique_json_object)
            except Exception as e:
                self.log(f"[Parallel Dispatcher Error] Failed to parse queries_json: {e}")
                return json.dumps({"error": f"Failed to parse queries_json: {e}"})

            if not isinstance(queries, dict) or not queries:
                return json.dumps({"error": "Invalid queries_json: expected a non-empty JSON object mapping agent names to query strings."})

            seen_canonical: Set[str] = set()
            parallel_tasks: Dict[str, Callable] = {}
            for raw_agent_name, query in queries.items():
                try:
                    short_name, tool_name = _normalize_parallel_agent_identifier(raw_agent_name)
                except ValueError as ve:
                    return json.dumps({"error": str(ve)})

                if short_name in seen_canonical:
                    return json.dumps({"error": f"Duplicate agent '{raw_agent_name}' in parallel council request."})
                seen_canonical.add(short_name)

                if not isinstance(query, str) or not query.strip():
                    return json.dumps(
                        {"error": f"Invalid query for agent '{raw_agent_name}': query must be a non-empty string."}
                    )

                if tool_name in FORBIDDEN_PARALLEL_MUTATING_TOOLS or tool_name in excluded_agent_tools:
                    return json.dumps(
                        {
                            "error": (
                                f"Unauthorized parallel agent '{raw_agent_name}': "
                                "mutating or non-deliberative agents cannot be executed in parallel."
                            )
                        }
                    )

                if tool_name not in approved_agents:
                    return json.dumps({"error": f"Unknown or unapproved parallel council agent '{raw_agent_name}'."})

                target_func = approved_agents[tool_name]

                def make_task(tf=target_func, q=query):
                    return tf(q)

                make_task._agent_role = getattr(
                    target_func, "_agent_role", APPROVED_PARALLEL_TOOL_TO_ROLE.get(tool_name)
                )
                make_task._agent_tools = getattr(target_func, "_agent_tools", None)
                make_task._is_read_only_deliberative = getattr(
                    target_func, "_is_read_only_deliberative", True
                )
                parallel_tasks[raw_agent_name] = make_task

            self.log(f"\n[Parallel Dispatcher] Initializing parallel session for {list(parallel_tasks.keys())}...")
            try:
                batch_results = self.execute_ephemeral_batch(parallel_tasks)
            except (PermissionError, ValueError) as val_err:
                return json.dumps({"error": str(val_err)})

            self.log("[Parallel Dispatcher] All agents responded.")
            return json.dumps(batch_results, indent=2)

        ask_council.__name__ = "ask_council"
        GLOBAL_TOOL_REGISTRY.register(
            name="ask_council",
            func=ask_council,
            description=ask_council.__doc__,
            classification="READ",
            required_permission="delegate_agent",
            requires_human_approval=False,
            allowed_roles={"Terminal Orchestrator"} | TRUSTED_SYSTEM_ROLES,
            affects_financial_state=False,
            parameters={"queries_json": str},
            target_resource="council_deliberation",
        )
        return ask_council
