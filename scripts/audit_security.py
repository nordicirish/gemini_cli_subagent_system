#!/usr/bin/env python3
"""
Read-Only Security & Boundary Auditor — GEM (gemini_cli_subagent_system).

Static checks (SAST & boundary hygiene):
  1. High-confidence credential / secret leakage across repository files.
  2. Tracked sensitive or forbidden runtime artifacts in Git index (`config.json`, `*.db`, `*.lock`).
  3. CORS wildcard / credentialed cross-origin / 0.0.0.0 network bind regression checks in `python/`.
  4. Unsafe subprocess patterns (`--dangerously-skip-permissions`, `shell=True`) in `python/`.
  5. `static/index.html` CDN pinning (`marked`, `dompurify`) & load order before `modern_ui.js`.

Optional passive dev check (`--dev` or `--url <base>`):
  Sends a read-only GET to `/api/data` with an untrusted `Origin` header to verify CORS isolation.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from sync_agent_manifests import find_repo_root  # noqa: E402

SECRET_PATTERNS = (
    ("Google API Key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("GitHub Token", re.compile(r"\bgh[pousr]_[0-9A-Za-z]{36,}\b")),
    ("AWS Access Key ID", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Private Key Block", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")),
    (
        "Hardcoded Non-Empty API Key",
        re.compile(
            r"""(?:GEMINI_API_KEY|GEMINI_FREE_TIER_API_KEY|FINNHUB_API_KEY)\s*=\s*["'](?!your_|placeholder|example|changeme|xxx)[A-Za-z0-9_-]{20,}["']""",
            re.IGNORECASE,
        ),
    ),
)

AUDITABLE_EXTENSIONS = {".py", ".js", ".html", ".css", ".md", ".yml", ".yaml", ".toml", ".json", ".txt", ".ps1"}
EXCLUDED_DIRS = {".git", ".venv", "venv", "node_modules", "cache", "logs", "scratch", "__pycache__", ".agents"}
EXCLUDED_FILES = {"context/config.json", "scripts/audit_security.py", "package-lock.json"}

WILDCARD_CORS_RE = re.compile(r"""allow_origins\s*=\s*\[\s*["']\*["']\s*\]""")
CREDENTIALED_CORS_RE = re.compile(r"""allow_credentials\s*=\s*True""")
NON_LOOPBACK_BIND_RE = re.compile(
    r"""(?:host\s*=\s*["']0\.0\.0\.0["']|uvicorn\.run\([^)]*0\.0\.0\.0|\.bind\(\s*\(\s*["']0\.0\.0\.0["'])"""
)
DANGEROUS_FLAG_RE = re.compile(r"--dangerously-skip-permissions")
SHELL_TRUE_RE = re.compile(r"\bshell\s*=\s*True\b")

MARKED_CDN_RE = re.compile(r'src="https://cdn\.jsdelivr\.net/npm/marked@([0-9][0-9.]*)/marked\.min\.js"')
DOMPURIFY_CDN_RE = re.compile(r'src="https://cdnjs\.cloudflare\.com/ajax/libs/dompurify/([0-9][0-9.]*)/purify\.min\.js"')


def _iter_auditable_files(repo_root: Path) -> List[Path]:
    files: List[Path] = []
    for root, dirs, filenames in os.walk(repo_root):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS)
        for fname in sorted(filenames):
            fpath = Path(root) / fname
            rel = fpath.relative_to(repo_root).as_posix()
            if rel.startswith("tests/") or rel in EXCLUDED_FILES:
                continue
            if fpath.suffix.lower() in AUDITABLE_EXTENSIONS:
                files.append(fpath)
    return files


def scan_secrets(repo_root: Path) -> List[Dict[str, Any]]:
    """Scan non-test workspace files for leaked tokens or private keys."""
    findings: List[Dict[str, Any]] = []
    for fpath in _iter_auditable_files(repo_root):
        rel = fpath.relative_to(repo_root).as_posix()
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for label, pat in SECRET_PATTERNS:
                if pat.search(line):
                    findings.append(
                        {
                            "severity": "CRITICAL",
                            "rule": "SECRET_LEAKAGE",
                            "file": rel,
                            "line": lineno,
                            "detail": f"Potential {label} detected (pattern match; raw value redacted).",
                        }
                    )
    return findings


def scan_tracked_sensitive_artifacts(repo_root: Path) -> List[Dict[str, Any]]:
    """Verify secrets and local DB/lock files are not tracked in Git."""
    patterns = (
        "config.json",
        "credentials.json",
        "token.json",
        "context/*.db",
        "cache/*.db",
        "*.lock",
        "**/*.lock",
        "*.restore_lock",
        "**/*.restore_lock",
        "*.cutover_lock",
        "**/*.cutover_lock",
    )
    proc = subprocess.run(
        ["git", "ls-files", "--", *patterns],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    if proc.returncode != 0:
        return []
    findings: List[Dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        rel = line.strip()
        if rel:
            findings.append(
                {
                    "severity": "HIGH",
                    "rule": "TRACKED_SENSITIVE_OR_RUNTIME_FILE",
                    "file": rel,
                    "line": 1,
                    "detail": f"Sensitive or runtime artifact '{rel}' is tracked in Git index.",
                }
            )
    return findings


def scan_python_boundaries(repo_root: Path) -> List[Dict[str, Any]]:
    """Inspect python/ modules for CORS wildcards, 0.0.0.0 binds, and unsafe subprocess calls."""
    findings: List[Dict[str, Any]] = []
    py_root = repo_root / "python"
    if not py_root.is_dir():
        return findings

    for fpath in sorted(py_root.rglob("*.py")):
        rel = fpath.relative_to(repo_root).as_posix()
        text = fpath.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            code_part = line.split("#", 1)[0]
            if WILDCARD_CORS_RE.search(code_part):
                findings.append(
                    {
                        "severity": "HIGH",
                        "rule": "WILDCARD_CORS",
                        "file": rel,
                        "line": lineno,
                        "detail": "Wildcard CORS allow_origins=['*'] detected.",
                    }
                )
            if CREDENTIALED_CORS_RE.search(code_part):
                findings.append(
                    {
                        "severity": "MEDIUM",
                        "rule": "CORS_ALLOW_CREDENTIALS",
                        "file": rel,
                        "line": lineno,
                        "detail": "allow_credentials=True detected; verify local requirement.",
                    }
                )
            if NON_LOOPBACK_BIND_RE.search(code_part):
                findings.append(
                    {
                        "severity": "CRITICAL",
                        "rule": "NON_LOOPBACK_BIND",
                        "file": rel,
                        "line": lineno,
                        "detail": "Server/socket bind to 0.0.0.0 violates loopback-only constraint.",
                    }
                )
            if DANGEROUS_FLAG_RE.search(code_part):
                findings.append(
                    {
                        "severity": "CRITICAL",
                        "rule": "UNSAFE_SUBPROCESS_FLAG",
                        "file": rel,
                        "line": lineno,
                        "detail": "Forbidden '--dangerously-skip-permissions' CLI flag detected.",
                    }
                )
            if SHELL_TRUE_RE.search(code_part):
                findings.append(
                    {
                        "severity": "HIGH",
                        "rule": "SUBPROCESS_SHELL_TRUE",
                        "file": rel,
                        "line": lineno,
                        "detail": "subprocess call with shell=True detected.",
                    }
                )
    return findings


def scan_frontend_csp_and_cdn_pins(repo_root: Path) -> List[Dict[str, Any]]:
    """Verify static/index.html pins marked & DOMPurify versions matching package.json in safe order."""
    findings: List[Dict[str, Any]] = []
    index_path = repo_root / "static" / "index.html"
    pkg_path = repo_root / "package.json"
    if not index_path.is_file() or not pkg_path.is_file():
        return findings

    html = index_path.read_text(encoding="utf-8", errors="replace")
    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    dev_deps = pkg.get("devDependencies") or {}

    marked_match = MARKED_CDN_RE.search(html)
    purify_match = DOMPURIFY_CDN_RE.search(html)
    if not marked_match or not purify_match:
        findings.append(
            {
                "severity": "HIGH",
                "rule": "UNPINNED_CDN_SCRIPT",
                "file": "static/index.html",
                "line": 1,
                "detail": "marked or DOMPurify CDN script is missing exact version pin.",
            }
        )
    else:
        if marked_match.group(1) != dev_deps.get("marked") or purify_match.group(1) != dev_deps.get("dompurify"):
            findings.append(
                {
                    "severity": "HIGH",
                    "rule": "CDN_TEST_VERSION_DRIFT",
                    "file": "static/index.html",
                    "line": 1,
                    "detail": "index.html CDN versions differ from package.json devDependencies.",
                }
            )
    if "purify.min.js" in html and "modern_ui.js" in html and html.index("purify.min.js") > html.index("modern_ui.js"):
        findings.append(
            {
                "severity": "HIGH",
                "rule": "SANITIZER_LOAD_ORDER",
                "file": "static/index.html",
                "line": 1,
                "detail": "DOMPurify must load before modern_ui.js.",
            }
        )
    return findings


def probe_dev_cors(base_url: str = "http://127.0.0.1:8000") -> Dict[str, Any]:
    """Send a passive read-only GET /api/data with an untrusted Origin header."""
    target = base_url.rstrip("/") + "/api/data"
    untrusted_origin = "https://untrusted-origin.example"
    req = urllib.request.Request(target, headers={"Origin": untrusted_origin}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            acao = resp.headers.get("Access-Control-Allow-Origin", "")
            status = resp.status
            ok = acao not in ("*", untrusted_origin) and status in (403, 404, 200)
            finding = None
            if acao in ("*", untrusted_origin):
                finding = {
                    "severity": "CRITICAL",
                    "rule": "LIVE_CORS_UNTRUSTED_ORIGIN_ALLOWED",
                    "file": target,
                    "line": 0,
                    "detail": f"Untrusted Origin '{untrusted_origin}' received ACAO '{acao}'.",
                }
            return {"reachable": True, "status": status, "acao": acao, "finding": finding}
    except urllib.error.HTTPError as err:
        return {"reachable": True, "status": err.code, "acao": err.headers.get("Access-Control-Allow-Origin", ""), "finding": None}
    except Exception as exc:
        return {"reachable": False, "error": str(exc), "finding": None}


def run_security_audit(repo_root: Optional[Path] = None, probe_url: Optional[str] = None) -> Dict[str, Any]:
    """Run all static checks plus optional passive loopback probe."""
    root = (repo_root or find_repo_root()).resolve()
    findings: List[Dict[str, Any]] = []
    findings.extend(scan_secrets(root))
    findings.extend(scan_tracked_sensitive_artifacts(root))
    findings.extend(scan_python_boundaries(root))
    findings.extend(scan_frontend_csp_and_cdn_pins(root))

    probe_info = None
    if probe_url:
        probe_info = probe_dev_cors(probe_url)
        if probe_info.get("finding"):
            findings.append(probe_info["finding"])

    blocking = [f for f in findings if f["severity"] in ("CRITICAL", "HIGH")]
    return {
        "ok": len(blocking) == 0,
        "findings": findings,
        "blocking_count": len(blocking),
        "probe": probe_info,
    }


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    probe_url: Optional[str] = None
    if "--dev" in args:
        probe_url = "http://127.0.0.1:8000"
    elif "--url" in args:
        idx = args.index("--url")
        if idx + 1 >= len(args):
            print("--url requires a base URL", file=sys.stderr)
            return 2
        probe_url = args[idx + 1]

    report = run_security_audit(probe_url=probe_url)
    if "--json" in args:
        print(json.dumps(report, indent=2))
    else:
        print(f"GEM Security Audit: {'PASS' if report['ok'] else 'FAIL'} ({len(report['findings'])} finding(s), {report['blocking_count']} blocking)")
        if report["probe"]:
            print(f"Passive Dev Probe: {report['probe']}")
        for f in report["findings"]:
            print(f"  [{f['severity']}] {f['rule']} ({f['file']}:{f['line']}): {f['detail']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
