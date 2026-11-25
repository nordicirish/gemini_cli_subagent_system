#!/usr/bin/env python3
"""
Dual-Runtime Stop Hook — GEM (gemini_cli_subagent_system).

Automatically runs `scripts/stage_gate.py --fast --no-receipt --skip-pip-check` when a turn
ends with modified workspace files, preventing broken Python/JS syntax, Protected Financial Asset
violations, or agent-manifest drift from leaving the working tree silently broken.

Runtime contracts (auto-detected from stdin JSON):
  - Antigravity (.agents/hooks.json): {"decision": "allow"} / {"decision": "continue", "reason": "..."}
  - Claude Code (.claude/settings.json, payload carries hook_event_name): {} / {"decision": "block", "reason": "..."}
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from stage_gate import find_repo_root, parse_porcelain_paths  # noqa: E402

IGNORED_PATH_PATTERNS = (
    re.compile(r"^\.vscode/"),
    re.compile(r"^\.idea/"),
    re.compile(r"\.DS_Store$"),
    re.compile(r"Thumbs\.db$", re.IGNORECASE),
    re.compile(r"\.(?:tmp|swp|swo|bak)~?$", re.IGNORECASE),
    re.compile(r"^\.agents/(?:state|logs)/"),
    re.compile(r"^\.claude/settings\.local\.json$"),
    re.compile(r"^(?:logs|cache|scratch)/"),
)

ANTIGRAVITY_SKIP_TERMINATION_REASONS = frozenset({
    "USER_CANCELED",
    "ERROR",
    "MAX_INVOCATIONS",
    "MAX_FORCED_INVOCATIONS",
    "MAX_TOKEN_BUDGET_EXCEEDED",
    "TERMINAL_CUSTOM_HOOK",
})


def is_ignored_path(rel_path: str) -> bool:
    """Return True if rel_path is an ignored runtime/editor artifact."""
    norm = rel_path.replace("\\", "/")
    if norm.startswith("./"):
        norm = norm[2:]
    return any(pat.search(norm) for pat in IGNORED_PATH_PATTERNS)


def parse_hook_stdin(raw_stdin: str) -> Dict[str, Any]:
    """Parse optional JSON payload from stdin safely."""
    text = (raw_stdin or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except ValueError:
        return {}


def format_decision(is_claude: bool, allow: bool, reason: str = "", system_message: str = "") -> Dict[str, Any]:
    """Build runtime-specific Stop hook response payload."""
    if is_claude:
        if allow:
            return {"systemMessage": system_message} if system_message else {}
        return {"decision": "block", "reason": reason}
    if allow:
        return {"decision": "allow"}
    return {"decision": "continue", "reason": reason}


def get_relevant_modified_files(repo_root: Path) -> List[str]:
    """Return non-ignored changed/untracked repository files."""
    proc = subprocess.run(
        ["git", "status", "--porcelain", "-uall"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"git status exited with code {proc.returncode}")
    return [p for p in parse_porcelain_paths(proc.stdout) if not is_ignored_path(p)]


def evaluate_stop_hook(raw_stdin: str = "", repo_root: Optional[Path] = None, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Evaluate Stop hook gate and return decision dict."""
    root = (repo_root or find_repo_root()).resolve()
    environ = os.environ if env is None else env
    payload = parse_hook_stdin(raw_stdin)
    is_claude = "hook_event_name" in payload

    if environ.get("GEM_STOP_HOOK_VERIFYING") == "1":
        return format_decision(is_claude, allow=True)

    if is_claude and payload.get("stop_hook_active"):
        return format_decision(
            is_claude=True,
            allow=True,
            system_message="Stop hook loop guard triggered; allowing stop after previous block.",
        )

    if not is_claude and payload.get("continuationActive"):
        return format_decision(is_claude=False, allow=True)

    if not is_claude and payload.get("terminationReason") in ANTIGRAVITY_SKIP_TERMINATION_REASONS:
        return format_decision(is_claude=False, allow=True)

    try:
        modified = get_relevant_modified_files(root)
    except Exception as exc:
        return format_decision(
            is_claude,
            allow=False,
            reason=f"Stop Hook Gate: unable to inspect working tree ({exc}).",
        )

    if not modified:
        return format_decision(is_claude, allow=True)

    child_env = dict(environ)
    child_env["GEM_STOP_HOOK_VERIFYING"] = "1"
    gate_script = root / "scripts" / "stage_gate.py"
    if not gate_script.is_file():
        return format_decision(
            is_claude,
            allow=False,
            reason="Stop Hook Gate: scripts/stage_gate.py not found.",
        )

    try:
        proc = subprocess.run(
            [sys.executable, str(gate_script), "--fast", "--no-receipt", "--skip-pip-check"],
            cwd=str(root),
            env=child_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return format_decision(
            is_claude,
            allow=False,
            reason="Stop Hook Gate: scripts/stage_gate.py --fast timed out after 60s.",
        )

    if proc.returncode == 0:
        return format_decision(is_claude, allow=True)

    details = (proc.stdout + "\n" + proc.stderr).strip()[-1400:]
    return format_decision(
        is_claude,
        allow=False,
        reason=f"Stop Hook Gate failed on modified workspace files:\n{details}",
    )


def main() -> int:
    raw = ""
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
        except Exception:
            raw = ""
    decision = evaluate_stop_hook(raw_stdin=raw)
    print(json.dumps(decision))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
