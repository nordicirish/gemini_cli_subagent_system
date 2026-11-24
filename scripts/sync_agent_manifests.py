#!/usr/bin/env python3
"""
Agent Manifest Synchronizer — GEM (gemini_cli_subagent_system).

Generates Antigravity manifests (.agents/agents/<role>/agent.md) and Claude Code
subagents (.claude/agents/<role>.md) from the single role registry (docs/agents/roles.json),
and validates cross-runtime parity, tool restrictions, and directive links.

Usage:
    python scripts/sync_agent_manifests.py           # regenerate manifests
    python scripts/sync_agent_manifests.py --check   # exit 1 on drift or policy errors
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REGISTRY_REL = "docs/agents/roles.json"
ANTIGRAVITY_DIR = ".agents/agents"
CLAUDE_DIR = ".claude/agents"
CORE_READS = [
    ".agents/AGENTS.md",
    "INSTRUCTIONS.md",
    "docs/governance/project-state.md",
]

AUTHORING_TOOLS = {
    "antigravity": {"write_file", "edit_file", "notebook_edit"},
    "claude": {"Edit", "Write", "NotebookEdit"},
}

ON_DEMAND_RE = re.compile(r"ON-DEMAND", re.IGNORECASE)
ROLE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*-subagent$")
VALID_CLAUDE_MODELS = {"haiku", "sonnet", "opus", "inherit"}


def find_repo_root(start_dir: Optional[Path] = None) -> Path:
    """Resolve repository root containing .agents/AGENTS.md or package.json."""
    current = (start_dir or Path(__file__).resolve().parent).resolve()
    while True:
        if (current / ".agents" / "AGENTS.md").exists() or (current / "package.json").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return Path(__file__).resolve().parent.parent


def load_registry(repo_root: Path) -> Dict[str, Any]:
    """Load and validate basic structure of docs/agents/roles.json."""
    reg_path = repo_root / REGISTRY_REL
    data = json.loads(reg_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("roles"), list) or not data["roles"]:
        raise ValueError(f"{REGISTRY_REL} must contain a non-empty 'roles' list")
    return data


def _human_list(items: List[str]) -> str:
    quoted = [f"`{item}`" for item in items]
    if len(quoted) <= 1:
        return "".join(quoted)
    if len(quoted) == 2:
        return f"{quoted[0]} and {quoted[1]}"
    return f"{', '.join(quoted[:-1])}, and {quoted[-1]}"


def doc_path(role: Dict[str, Any]) -> str:
    """Return workspace-relative canonical directive path for a role."""
    return f"docs/agents/{role['doc']}"


def render_antigravity(role: Dict[str, Any]) -> str:
    """Render Antigravity subagent manifest content."""
    a = role["antigravity"]
    fm_lines = [
        "---",
        f"name: {role['name']}",
        f"description: {a['description']}",
        "tools:",
        *[f"    - {tool}" for tool in a["tools"]],
    ]
    if a.get("hidden"):
        fm_lines.append("hidden: true")
    fm_lines.append("---")

    reads = [*(role.get("extraReads") or []), *CORE_READS]
    body_lines = [
        "",
        "# Agent System Instructions",
        "",
        f"You are the {role['title']} for `gemini_cli_subagent_system`.",
        "",
        (
            f"**Canonical directive:** `{doc_path(role)}`. Read it, then {_human_list(reads)}, "
            "before acting. Those files are the single source of truth; this manifest only declares "
            "runtime identity and tool permissions — never hand-edit it."
        ),
    ]
    if role.get("onDemand"):
        trigger = a.get("trigger") or "RUN ONLY ON-DEMAND WHEN EXPLICITLY REQUESTED BY THE USER."
        body_lines.extend(["", f"Execution Trigger: {trigger}"])
    body_lines.extend(["", f"Boundary summary: {a['boundary']}", ""])
    return "\n".join(fm_lines) + "\n" + "\n".join(body_lines)


def render_claude(role: Dict[str, Any]) -> str:
    """Render Claude Code subagent manifest content."""
    c = role["claude"]
    fm_lines = [
        "---",
        f"name: {role['name']}",
        f"description: {c['description']}",
        f"tools: {', '.join(c['tools'])}",
    ]
    if c.get("model"):
        fm_lines.append(f"model: {c['model']}")
    fm_lines.append("---")

    reads = [f"`{doc_path(role)}` (your role)", *[f"`{r}`" for r in (role.get("extraReads") or [])], *[f"`{r}`" for r in CORE_READS]]
    numbered_reads = "\n".join(f"{idx}. {item}" for idx, item in enumerate(reads, start=1))
    dispatch = (
        "dispatched only on explicit user request"
        if role.get("onDemand")
        else "dispatched for bounded stage work"
    )
    sections = [
        f"You are the {role['title']} for `gemini_cli_subagent_system`, {dispatch}.",
        f"Before acting, read these as your single source of truth:\n{numbered_reads}",
        *(c.get("notes") or []),
    ]
    return "\n".join(fm_lines) + "\n\n" + "\n\n".join(sections) + "\n"


def validate_policy(registry: Dict[str, Any], repo_root: Path) -> List[str]:
    """Validate role definitions, links, tool least-privilege, and absence of orphans."""
    problems: List[str] = []
    agents_md_path = repo_root / ".agents" / "AGENTS.md"
    agents_md = agents_md_path.read_text(encoding="utf-8") if agents_md_path.exists() else ""
    seen_names: set[str] = set()

    for role in registry["roles"]:
        role_id = role.get("name") or "(unnamed role)"
        if not ROLE_NAME_RE.match(role.get("name") or ""):
            problems.append(f"{role_id}: name must match ^[a-z][a-z0-9-]*-subagent$")
        if role.get("name") in seen_names:
            problems.append(f"{role_id}: duplicate role name")
        if role.get("name"):
            seen_names.add(role["name"])

        for req_key in ("title", "doc", "antigravity", "claude"):
            if not role.get(req_key):
                problems.append(f"{role_id}: missing '{req_key}'")
        if not role.get("antigravity") or not role.get("claude"):
            continue

        model = role["claude"].get("model")
        if model is not None and model not in VALID_CLAUDE_MODELS:
            problems.append(
                f"{role_id}: claude.model must be one of {sorted(VALID_CLAUDE_MODELS)} (or omitted)"
            )

        canonical_rel = doc_path(role)
        if not (repo_root / canonical_rel).is_file():
            problems.append(f"{role_id}: canonical doc {canonical_rel} does not exist")
        if canonical_rel not in agents_md:
            problems.append(f"{role_id}: .agents/AGENTS.md does not reference {canonical_rel}")

        for extra in role.get("extraReads") or []:
            if not (repo_root / extra).exists():
                problems.append(f"{role_id}: extraReads entry {extra} does not exist")

        if role.get("noAuthoring"):
            for runtime in ("antigravity", "claude"):
                tools = role[runtime].get("tools") or []
                leaked = [t for t in tools if t in AUTHORING_TOOLS[runtime]]
                if leaked:
                    problems.append(
                        f"{role_id}: noAuthoring role exposes authoring tools in {runtime}: {', '.join(leaked)}"
                    )

        for runtime in ("antigravity", "claude"):
            desc = role[runtime].get("description") or ""
            has_marker = bool(ON_DEMAND_RE.search(desc))
            if role.get("onDemand") and not has_marker:
                problems.append(f"{role_id}: onDemand role's {runtime} description must include ON-DEMAND")
            if not role.get("onDemand") and has_marker:
                problems.append(f"{role_id}: {runtime} description includes ON-DEMAND but onDemand is false")

    ag_dir = repo_root / ANTIGRAVITY_DIR
    if ag_dir.is_dir():
        for entry in sorted(ag_dir.iterdir()):
            if entry.is_dir() and entry.name not in seen_names:
                problems.append(f"orphan Antigravity manifest {ANTIGRAVITY_DIR}/{entry.name}/ has no entry in {REGISTRY_REL}")
            elif entry.is_file():
                problems.append(f"legacy/orphan file {ANTIGRAVITY_DIR}/{entry.name} should not exist alongside generated manifests")

    cc_dir = repo_root / CLAUDE_DIR
    if cc_dir.is_dir():
        for entry in sorted(cc_dir.iterdir()):
            if entry.is_file() and entry.suffix == ".md":
                stem = entry.stem
                if stem not in seen_names:
                    problems.append(f"orphan Claude Code subagent {CLAUDE_DIR}/{entry.name} has no entry in {REGISTRY_REL}")

    return problems


def _normalize_eol(text: str) -> str:
    return text.replace("\r\n", "\n")


def sync_agent_manifests(repo_root: Optional[Path] = None, write: bool = False) -> Dict[str, Any]:
    """Validate registry and compare or regenerate dual-runtime manifests."""
    root = (repo_root or find_repo_root()).resolve()
    registry = load_registry(root)
    problems = validate_policy(registry, root)
    drift: List[str] = []
    written: List[str] = []

    for role in registry["roles"]:
        if not role.get("antigravity") or not role.get("claude"):
            continue
        targets = (
            (f"{ANTIGRAVITY_DIR}/{role['name']}/agent.md", render_antigravity(role)),
            (f"{CLAUDE_DIR}/{role['name']}.md", render_claude(role)),
        )
        for rel_path, expected in targets:
            abs_path = root / rel_path
            actual = _normalize_eol(abs_path.read_text(encoding="utf-8")) if abs_path.is_file() else None
            if actual == expected:
                continue
            if write:
                abs_path.parent.mkdir(parents=True, exist_ok=True)
                abs_path.write_text(expected, encoding="utf-8", newline="\n")
                written.append(rel_path)
            else:
                reason = "is missing" if actual is None else "differs from rendered registry form"
                drift.append(f"{rel_path} {reason}")

    return {
        "ok": len(problems) == 0 and len(drift) == 0,
        "problems": problems,
        "drift": drift,
        "written": written,
    }


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    check_only = "--check" in args
    res = sync_agent_manifests(write=not check_only)

    for rel_path in res["written"]:
        print(f"OK regenerated {rel_path}")
    if not check_only and not res["written"]:
        print("OK Agent manifests already in sync.")

    if not res["ok"]:
        mode_label = "check" if check_only else "sync"
        print(f"FAIL Agent manifest {mode_label} found problems:", file=sys.stderr)
        for item in [*res["problems"], *res["drift"]]:
            print(f"  - {item}", file=sys.stderr)
        if res["drift"]:
            print(
                f"\nRegenerate with: python scripts/sync_agent_manifests.py  "
                f"(edit {REGISTRY_REL}, never generated manifests)",
                file=sys.stderr,
            )
        return 1

    if check_only:
        print("OK Antigravity and Claude Code agent manifests match docs/agents/roles.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
