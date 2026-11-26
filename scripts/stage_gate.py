#!/usr/bin/env python3
"""
Executable Stage-Gate Verification Orchestrator — GEM (gemini_cli_subagent_system).

Automates `.agents/skills/stage-gate/SKILL.md`:
  1. Read-only Git branch / HEAD / changed-file inspection.
  2. Protocol 06 Protected Financial Asset invariance verification.
  3. Artifact hygiene check (tracked DB/locks/secrets & unauthorized stage reports).
  4. Role-registry manifest parity + directive size-ceiling verification (< 23,500 bytes, < 150 lines).
  5. Protocol 05 scope-proportional unittest & JS test routing.
  6. Python bytecode compilation (`compileall`) and `pip check`.
  7. Atomic receipt emission to `.agents/state/last-verification.json`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from sync_agent_manifests import find_repo_root, sync_agent_manifests  # noqa: E402

DIRECTIVE_MAX_BYTES = 23_500
PROJECT_STATE_MAX_LINES = 150

REQUIRED_USER_DOCS = (
    "README.md",
    "CHANGELOG.md",
    "docs/USER_GUIDE.md",
)

BANNED_USER_DOC_PATTERNS = (
    "save_chart_screenshots",
    "verify_stage_1.py",
    "scratch/",
    "gemini-3.8",
)

PROTECTED_FINANCIAL_ASSETS = (
    "engine_instructions/",
    "gem_trading_rules/rules.md",
    "context/ssot.json",
    "context/trade_lessons.json",
    "context/decision_log.json",
    "context/config.json",
)

TRACKED_FORBIDDEN_PATTERNS = (
    "context/*.db",
    "cache/*.db",
    "*.lock",
    "**/*.lock",
    "*.restore_lock",
    "**/*.restore_lock",
    "*.cutover_lock",
    "**/*.cutover_lock",
    "config.json",
    "credentials.json",
    "token.json",
)

UNTRACKED_FORBIDDEN_RE = re.compile(
    r"^(?:context/[^/]+\.db(?:-shm|-wal)?|cache/[^/]+\.db(?:-shm|-wal)?|.*\.(?:lock|restore_lock|cutover_lock))$",
    re.IGNORECASE,
)
UNAUTHORIZED_STAGE_REPORT_RE = re.compile(r"^docs/engineering/STAGE_.*\.md$", re.IGNORECASE)


def _run(cmd: Sequence[str], cwd: Path, env: Optional[Dict[str, str]] = None, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(cmd),
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def parse_porcelain_paths(output: str) -> List[str]:
    """Parse `git status --porcelain -uall` output into normalized forward-slash relative paths."""
    paths: List[str] = []
    for raw_line in (output or "").splitlines():
        line = raw_line.rstrip("\r\n")
        if len(line) < 4:
            continue
        target = line[3:].strip()
        if " -> " in target:
            target = target.split(" -> ", 1)[1].strip()
        if target.startswith('"') and target.endswith('"'):
            try:
                target = json.loads(target)
            except ValueError:
                target = target.strip('"')
        norm = target.replace("\\", "/")
        if norm.startswith("./"):
            norm = norm[2:]
        if norm:
            paths.append(norm)
    return paths


def get_git_state(repo_root: Path) -> Dict[str, Any]:
    """Inspect branch, HEAD SHA, and changed/untracked paths read-only."""
    branch_proc = _run(["git", "branch", "--show-current"], repo_root, timeout=15)
    head_proc = _run(["git", "rev-parse", "HEAD"], repo_root, timeout=15)
    status_proc = _run(["git", "status", "--porcelain", "-uall"], repo_root, timeout=15)
    return {
        "branch": branch_proc.stdout.strip() if branch_proc.returncode == 0 else "UNKNOWN",
        "head": head_proc.stdout.strip() if head_proc.returncode == 0 else "UNKNOWN",
        "changed_files": parse_porcelain_paths(status_proc.stdout) if status_proc.returncode == 0 else [],
        "git_ok": status_proc.returncode == 0,
    }


def check_protected_assets(repo_root: Path, changed_files: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Verify Protocol 06 Protected Financial Assets remain unmodified."""
    proc = _run(["git", "diff", "--exit-code", "--", *PROTECTED_FINANCIAL_ASSETS], repo_root, timeout=15)
    touched: List[str] = []
    for rel in changed_files or ():
        if rel in PROTECTED_FINANCIAL_ASSETS or rel.startswith("engine_instructions/"):
            touched.append(rel)
    ok = proc.returncode == 0 and not touched
    return {
        "ok": ok,
        "exit_code": proc.returncode,
        "touched": sorted(set(touched)),
        "diff_summary": (proc.stdout or proc.stderr).strip()[:1000],
    }


def check_artifact_hygiene(repo_root: Path, changed_files: Sequence[str]) -> Dict[str, Any]:
    """Verify zero tracked forbidden runtime/secret files and zero unauthorized stage reports."""
    ls_proc = _run(["git", "ls-files", "--", *TRACKED_FORBIDDEN_PATTERNS], repo_root, timeout=15)
    tracked_forbidden = [line.strip().replace("\\", "/") for line in ls_proc.stdout.splitlines() if line.strip()]
    untracked_forbidden = [p for p in changed_files if UNTRACKED_FORBIDDEN_RE.match(p)]
    stage_reports = [p for p in changed_files if UNAUTHORIZED_STAGE_REPORT_RE.match(p)]
    ok = ls_proc.returncode == 0 and not tracked_forbidden and not untracked_forbidden and not stage_reports
    return {
        "ok": ok,
        "tracked_forbidden": tracked_forbidden,
        "untracked_forbidden": untracked_forbidden,
        "unauthorized_stage_reports": stage_reports,
    }


def directive_files_to_check(repo_root: Path) -> List[Path]:
    """Collect all active governance and agent directive files subject to the 23,500-byte ceiling."""
    candidates: List[Path] = [
        repo_root / ".agents" / "AGENTS.md",
        repo_root / "INSTRUCTIONS.md",
        repo_root / "docs" / "governance" / "AI_DEVELOPMENT_MANAGEMENT.md",
        repo_root / "docs" / "governance" / "project-state.md",
        repo_root / ".agents" / "skills" / "stage-gate" / "SKILL.md",
    ]
    for folder in (repo_root / "docs" / "agents", repo_root / ".claude" / "agents"):
        if folder.is_dir():
            candidates.extend(sorted(p for p in folder.iterdir() if p.is_file() and p.suffix == ".md"))
    ag_dir = repo_root / ".agents" / "agents"
    if ag_dir.is_dir():
        for entry in sorted(ag_dir.iterdir()):
            if entry.is_dir() and (entry / "agent.md").is_file():
                candidates.append(entry / "agent.md")
    return [p for p in candidates if p.is_file()]


def check_governance_and_ceilings(repo_root: Path) -> Dict[str, Any]:
    """Validate role registry parity and directive byte/line ceilings."""
    manifest_res = sync_agent_manifests(repo_root=repo_root, write=False)
    oversized: List[Dict[str, Any]] = []
    for file_path in directive_files_to_check(repo_root):
        size_bytes = file_path.stat().st_size
        if size_bytes >= DIRECTIVE_MAX_BYTES:
            oversized.append(
                {
                    "file": file_path.relative_to(repo_root).as_posix(),
                    "bytes": size_bytes,
                    "limit": DIRECTIVE_MAX_BYTES,
                }
            )

    state_path = repo_root / "docs" / "governance" / "project-state.md"
    state_lines = len(state_path.read_text(encoding="utf-8").splitlines()) if state_path.is_file() else 0
    state_over_limit = state_lines >= PROJECT_STATE_MAX_LINES

    ok = bool(manifest_res["ok"]) and not oversized and not state_over_limit
    return {
        "ok": ok,
        "manifest_problems": manifest_res["problems"],
        "manifest_drift": manifest_res["drift"],
        "oversized_directives": oversized,
        "project_state_lines": state_lines,
        "project_state_limit": PROJECT_STATE_MAX_LINES,
        "project_state_over_limit": state_over_limit,
    }


def check_user_docs_parity(repo_root: Path, changed_files: Sequence[str]) -> Dict[str, Any]:
    """Ensure required user documentation exists without stale patterns and flag doc-drift reminders."""
    missing: List[str] = []
    stale_hits: List[Dict[str, str]] = []
    for rel in REQUIRED_USER_DOCS:
        p = repo_root / rel
        if not p.is_file():
            missing.append(rel)
            continue
        if rel in ("README.md", "docs/USER_GUIDE.md"):
            text = p.read_text(encoding="utf-8", errors="replace")
            for pat in BANNED_USER_DOC_PATTERNS:
                if pat in text:
                    stale_hits.append({"file": rel, "pattern": pat})

    normalized = [
        (p.replace("\\", "/")[2:] if p.replace("\\", "/").startswith("./") else p.replace("\\", "/"))
        for p in changed_files
    ]
    ui_or_api_touched = any(
        p in ("static/index.html", "static/app.js", "python/web_server.py") for p in normalized
    )
    docs_touched = any(
        p in ("README.md", "CHANGELOG.md", "docs/USER_GUIDE.md") for p in normalized
    )
    reminders: List[str] = []
    if ui_or_api_touched and not docs_touched:
        reminders.append(
            "UI/API files changed without touching README.md, CHANGELOG.md, or docs/USER_GUIDE.md — "
            "run documentation-subagent to keep user docs synchronized."
        )

    return {
        "ok": not missing and not stale_hits,
        "missing": missing,
        "stale_hits": stale_hits,
        "reminders": reminders,
    }


def select_test_targets(changed_files: Sequence[str], mode: str = "scoped") -> Tuple[List[str], List[str]]:
    """
    Map modified repository files to Protocol 05 unittest modules and JS test script paths.
    Returns `(unittest_modules, js_script_relpaths)`.
    """
    if mode == "all":
        return (["discover"], ["tests/js/dashboard_load.test.js", "tests/js/sanitize_chat_html.test.js"])

    normalized = [
        (p.replace("\\", "/")[2:] if p.replace("\\", "/").startswith("./") else p.replace("\\", "/"))
        for p in changed_files
    ]
    modules: List[str] = []
    js_scripts: List[str] = []

    def add_mod(name: str) -> None:
        if name not in modules:
            modules.append(name)

    def add_js(rel: str) -> None:
        if rel not in js_scripts:
            js_scripts.append(rel)

    any_python = any(p.startswith("python/") and p.endswith(".py") for p in normalized)
    any_governance = any(
        p.startswith((".agents/", ".claude/", "docs/agents/", "docs/governance/", "scripts/"))
        or p in ("INSTRUCTIONS.md", ".gitignore")
        for p in normalized
    )
    any_ui = any(p.startswith(("static/", "tests/js/")) or p == "package.json" for p in normalized)
    any_db = any(p.startswith(("python/database/", "python/persistence/")) for p in normalized)
    any_market = any(p in ("python/fetch_stocks.py", "python/background_intelligence.py") for p in normalized)
    any_web_or_agent = any(
        p in ("python/agent_framework.py", "python/web_server.py", "python/tools.py", "python/main.py")
        for p in normalized
    )

    if any_governance:
        add_mod("tests.test_agentic_governance")

    if mode == "fast":
        if any_python:
            add_mod("tests.test_permissions")
            add_mod("tests.test_tool_dispatch")
        if any_ui:
            add_mod("tests.test_dashboard_scripts")
            add_mod("tests.test_chat_sanitizer")
            add_js("tests/js/dashboard_load.test.js")
            add_js("tests/js/sanitize_chat_html.test.js")
        return (modules, js_scripts)

    # Full Protocol 05 --scoped routing
    if any_python:
        for mod in ("tests.test_investment_regression", "tests.test_permissions", "tests.test_tool_dispatch"):
            add_mod(mod)
    if any_db:
        for mod in (
            "tests.test_database_core",
            "tests.test_database_repositories",
            "tests.test_persistence_service",
            "tests.test_runtime_persistence_cutover",
            "tests.test_atomic_io",
            "tests.test_audit_logger",
        ):
            add_mod(mod)
    if any_market:
        for mod in (
            "tests.test_market_data_integrity",
            "tests.test_market_data_acquisition",
            "tests.test_heavy_market_data_separation",
            "tests.test_market_data_pipeline_integration",
            "tests.test_background_intelligence",
        ):
            add_mod(mod)
    if any_web_or_agent:
        for mod in (
            "tests.test_agent_framework",
            "tests.test_web_chat_endpoint",
            "tests.test_watchlist_endpoints",
            "tests.test_api_routes",
            "tests.test_entrypoints",
        ):
            add_mod(mod)
    if any_ui:
        for mod in ("tests.test_dashboard_scripts", "tests.test_chat_sanitizer"):
            add_mod(mod)
        add_js("tests/js/dashboard_load.test.js")
        add_js("tests/js/sanitize_chat_html.test.js")

    for p in normalized:
        if p.startswith("tests/test_") and p.endswith(".py"):
            add_mod(f"tests.{Path(p).stem}")

    return (modules, js_scripts)


def write_verification_receipt(repo_root: Path, payload: Dict[str, Any]) -> Optional[Path]:
    """Atomically write `.agents/state/last-verification.json` without leaving temp files behind."""
    state_dir = repo_root / ".agents" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = state_dir / "last-verification.json"
    fd, tmp_name = tempfile.mkstemp(prefix=".last-verification-", suffix=".tmp", dir=str(state_dir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(tmp_name, receipt_path)
        return receipt_path
    finally:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def run_stage_gate(
    repo_root: Optional[Path] = None,
    mode: str = "scoped",
    changed_override: Optional[List[str]] = None,
    write_receipt: bool = True,
    run_pip_check: Optional[bool] = None,
) -> Dict[str, Any]:
    """Execute the full stage gate and return a structured result dictionary."""
    root = (repo_root or find_repo_root()).resolve()
    git_state = get_git_state(root)
    changed_files = list(changed_override) if changed_override is not None else git_state["changed_files"]

    protected = check_protected_assets(root, changed_files)
    hygiene = check_artifact_hygiene(root, changed_files)
    governance = check_governance_and_ceilings(root)
    user_docs = check_user_docs_parity(root, changed_files)

    modules, js_scripts = select_test_targets(changed_files, mode=mode)
    command_results: List[Dict[str, Any]] = []
    tests_ok = True

    if modules:
        cmd = (
            [sys.executable, "-m", "unittest", "discover", "tests"]
            if modules == ["discover"]
            else [sys.executable, "-m", "unittest", *modules, "-v"]
        )
        proc = _run(cmd, root, timeout=600)
        command_results.append(
            {
                "name": "unittest",
                "command": " ".join(cmd),
                "exit_code": proc.returncode,
                "output_tail": (proc.stdout + "\n" + proc.stderr).strip()[-1500:],
            }
        )
        if proc.returncode != 0:
            tests_ok = False

    node_bin = shutil.which("node")
    has_jsdom = (root / "node_modules" / "jsdom").is_dir()
    if js_scripts and node_bin and has_jsdom:
        for script_rel in js_scripts:
            script_path = root / script_rel
            if not script_path.is_file():
                continue
            cmd = [node_bin, str(script_path)]
            if script_rel.endswith("sanitize_chat_html.test.js"):
                cmd.append(str(root / "static" / "modern_ui.js"))
            proc = _run(cmd, root, timeout=120)
            command_results.append(
                {
                    "name": Path(script_rel).name,
                    "command": " ".join(cmd),
                    "exit_code": proc.returncode,
                    "output_tail": (proc.stdout + "\n" + proc.stderr).strip()[-1000:],
                }
            )
            if proc.returncode != 0:
                tests_ok = False

    compile_cmd = [sys.executable, "-m", "compileall", "-q", "python", "tests", "scripts"]
    compile_proc = _run(compile_cmd, root, timeout=120)
    command_results.append(
        {
            "name": "compileall",
            "command": " ".join(compile_cmd),
            "exit_code": compile_proc.returncode,
            "output_tail": (compile_proc.stdout + "\n" + compile_proc.stderr).strip()[-800:],
        }
    )

    should_pip = (mode != "fast") if run_pip_check is None else run_pip_check
    pip_ok = True
    if should_pip:
        pip_cmd = [sys.executable, "-m", "pip", "check"]
        pip_proc = _run(pip_cmd, root, timeout=120)
        pip_ok = pip_proc.returncode == 0
        command_results.append(
            {
                "name": "pip_check",
                "command": " ".join(pip_cmd),
                "exit_code": pip_proc.returncode,
                "output_tail": (pip_proc.stdout + "\n" + pip_proc.stderr).strip()[-500:],
            }
        )

    overall_ok = (
        protected["ok"]
        and hygiene["ok"]
        and governance["ok"]
        and user_docs["ok"]
        and tests_ok
        and compile_proc.returncode == 0
        and pip_ok
    )

    report = {
        "schemaVersion": 1,
        "verificationRunId": f"gate-{uuid.uuid4().hex[:10]}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "ok": overall_ok,
        "git": git_state,
        "changed_files": changed_files,
        "protected_assets": protected,
        "artifact_hygiene": hygiene,
        "governance": governance,
        "user_docs": user_docs,
        "selected_tests": {"unittest": modules, "js": js_scripts},
        "commands": command_results,
    }

    if write_receipt:
        receipt_path = write_verification_receipt(root, report)
        if receipt_path is not None:
            report["receipt"] = receipt_path.relative_to(root).as_posix()

    return report


def _format_human_summary(report: Dict[str, Any]) -> str:
    lines = [
        f"Stage-Gate ({report['mode']}): {'PASS' if report['ok'] else 'FAIL'}",
        f"Branch: {report['git']['branch']} | HEAD: {report['git']['head']}",
        f"Changed files ({len(report['changed_files'])}): {', '.join(report['changed_files']) or 'None'}",
        f"Protocol 06 Protected Assets: {'PASS' if report['protected_assets']['ok'] else 'FAIL'}",
        f"Artifact Hygiene: {'PASS' if report['artifact_hygiene']['ok'] else 'FAIL'}",
        f"Governance & Size Ceilings: {'PASS' if report['governance']['ok'] else 'FAIL'} "
        f"(project-state.md: {report['governance']['project_state_lines']}/{report['governance']['project_state_limit']} lines)",
    ]
    if not report["protected_assets"]["ok"]:
        lines.append(f"  [BLOCKER] Protected assets touched: {report['protected_assets']['touched']}")
    if not report["artifact_hygiene"]["ok"]:
        lines.append(f"  [BLOCKER] Hygiene failure: {report['artifact_hygiene']}")
    if not report["governance"]["ok"]:
        lines.append(f"  [BLOCKER] Governance failure: {report['governance']}")
    user_docs = report.get("user_docs") or {"ok": True, "reminders": []}
    lines.append(f"User Documentation Parity: {'PASS' if user_docs.get('ok', True) else 'FAIL'}")
    if not user_docs.get("ok", True):
        lines.append(f"  [BLOCKER] User docs failure: {user_docs}")
    for rem in user_docs.get("reminders") or ():
        lines.append(f"  [REMINDER] {rem}")
    for cmd in report["commands"]:
        status = "PASS" if cmd["exit_code"] == 0 else f"FAIL (exit {cmd['exit_code']})"
        lines.append(f"  - {cmd['name']}: {status}")
        if cmd["exit_code"] != 0 and cmd["output_tail"]:
            lines.append(cmd["output_tail"])
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    mode = "scoped"
    if "--all" in args:
        mode = "all"
    elif "--fast" in args:
        mode = "fast"

    changed_override: Optional[List[str]] = None
    if "--changed" in args:
        idx = args.index("--changed")
        if idx + 1 >= len(args):
            print("--changed requires a comma-separated path list", file=sys.stderr)
            return 2
        changed_override = [p.strip() for p in args[idx + 1].split(",") if p.strip()]

    report = run_stage_gate(
        mode=mode,
        changed_override=changed_override,
        write_receipt="--no-receipt" not in args,
        run_pip_check=False if "--skip-pip-check" in args else None,
    )

    if "--json" in args:
        print(json.dumps(report, indent=2))
    else:
        print(_format_human_summary(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
