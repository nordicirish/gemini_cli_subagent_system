"""
Agentic Governance, Manifest Parity, Directive Size-Ceiling, Stage-Gate, Stop Hook & Security Audit Tests.

Verifies code-enforced governance adapted from tk-site-new:
  1. Single-source role registry parity (.agents/agents/<role>/agent.md & .claude/agents/<role>.md).
  2. Strict directive size ceiling (< 23,500 bytes) and Project State Brief (< 150 lines).
  3. Stage-gate orchestrator Protocol 05 routing, Protocol 06 enforcement, and hygiene detection.
  4. Dual-runtime Stop hook contract & infinite-loop guard.
  5. Security auditor clean baseline & negative detection on injected secret/CORS/bind/subprocess flaws.
  6. Headless dashboard UI verifier CLI & metric evaluator contracts.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import audit_security  # noqa: E402
import hook_stop_verify  # noqa: E402
import stage_gate  # noqa: E402
import sync_agent_manifests  # noqa: E402


class TestAgenticManifestsAndCeilings(unittest.TestCase):
    def test_role_registry_and_manifests_have_zero_drift_or_policy_errors(self):
        res = sync_agent_manifests.sync_agent_manifests(repo_root=ROOT, write=False)
        self.assertEqual(res["problems"], [], f"Role policy problems: {res['problems']}")
        self.assertEqual(res["drift"], [], f"Manifest drift detected: {res['drift']}")
        self.assertTrue(res["ok"])

    def test_no_authoring_roles_expose_zero_write_tools_in_both_runtimes(self):
        registry = sync_agent_manifests.load_registry(ROOT)
        by_name = {r["name"]: r for r in registry["roles"]}
        for ro_role in (
            "architecture-subagent",
            "security-subagent",
            "independent-reviewer-subagent",
            "browser-subagent",
            "rules-steward-subagent",
        ):
            self.assertIn(ro_role, by_name)
            self.assertTrue(by_name[ro_role]["noAuthoring"])
            for rt, forbidden in sync_agent_manifests.AUTHORING_TOOLS.items():
                tools = set(by_name[ro_role][rt]["tools"])
                self.assertEqual(tools & forbidden, set(), f"{ro_role} leaked write tools in {rt}")

    def test_design_subagent_is_strictly_on_demand_in_both_runtimes(self):
        registry = sync_agent_manifests.load_registry(ROOT)
        by_name = {r["name"]: r for r in registry["roles"]}
        design = by_name["design-subagent"]
        self.assertTrue(design["onDemand"])
        self.assertIn("ON-DEMAND", design["antigravity"]["description"])
        self.assertIn("ON-DEMAND", design["claude"]["description"])

    def test_rules_steward_subagent_is_read_only_and_on_demand(self):
        registry = sync_agent_manifests.load_registry(ROOT)
        by_name = {r["name"]: r for r in registry["roles"]}
        steward = by_name["rules-steward-subagent"]
        self.assertTrue(steward["noAuthoring"])
        self.assertTrue(steward["onDemand"])
        self.assertIn("ON-DEMAND", steward["antigravity"]["description"])
        self.assertIn("ON-DEMAND", steward["claude"]["description"])
        ag_manifest = (ROOT / ".agents" / "agents" / "rules-steward-subagent" / "agent.md").read_text(encoding="utf-8")
        cl_manifest = (ROOT / ".claude" / "agents" / "rules-steward-subagent.md").read_text(encoding="utf-8")
        for forbidden in ("write_file", "edit_file", "notebook_edit", "Edit", "Write", "NotebookEdit"):
            self.assertNotIn(f"- {forbidden}", ag_manifest)
        self.assertNotIn("Edit", cl_manifest.split("---")[1])
        self.assertNotIn("Write", cl_manifest.split("---")[1])

    def test_directive_byte_ceilings_and_project_state_line_limit(self):
        files = stage_gate.directive_files_to_check(ROOT)
        self.assertGreaterEqual(len(files), 15)
        for fpath in files:
            size = fpath.stat().st_size
            self.assertLess(
                size,
                stage_gate.DIRECTIVE_MAX_BYTES,
                f"{fpath.relative_to(ROOT).as_posix()} is {size} bytes (limit < {stage_gate.DIRECTIVE_MAX_BYTES})",
            )
        state_path = ROOT / "docs" / "governance" / "project-state.md"
        lines = len(state_path.read_text(encoding="utf-8").splitlines())
        self.assertLess(
            lines,
            stage_gate.PROJECT_STATE_MAX_LINES,
            f"project-state.md has {lines} lines (limit < {stage_gate.PROJECT_STATE_MAX_LINES})",
        )


class TestStageGateAndStopHook(unittest.TestCase):
    def test_protocol_05_test_routing(self):
        mods_py, js_py = stage_gate.select_test_targets(["python/tools.py"], mode="scoped")
        self.assertIn("tests.test_investment_regression", mods_py)
        self.assertIn("tests.test_permissions", mods_py)
        self.assertIn("tests.test_tool_dispatch", mods_py)
        self.assertIn("tests.test_agent_framework", mods_py)
        self.assertEqual(js_py, [])

        mods_db, _ = stage_gate.select_test_targets(["python/persistence/contracts.py"], mode="scoped")
        self.assertIn("tests.test_persistence_service", mods_db)
        self.assertNotIn("tests.test_heavy_market_data_separation", mods_db)

        mods_ui, js_ui = stage_gate.select_test_targets(["static/app.js"], mode="scoped")
        self.assertIn("tests.test_dashboard_scripts", mods_ui)
        self.assertIn("tests.test_chat_sanitizer", mods_ui)
        self.assertEqual(js_ui, ["tests/js/dashboard_load.test.js", "tests/js/sanitize_chat_html.test.js"])

        mods_gov, _ = stage_gate.select_test_targets([".agents/AGENTS.md", "scripts/stage_gate.py"], mode="scoped")
        self.assertIn("tests.test_agentic_governance", mods_gov)

    def test_hygiene_flags_unauthorized_stage_report_and_unignored_db(self):
        hygiene = stage_gate.check_artifact_hygiene(
            ROOT,
            ["docs/engineering/STAGE_99_UNSOLICITED_REPORT.md", "context/rogue.db", "scripts/stage_gate.py"],
        )
        self.assertFalse(hygiene["ok"])
        self.assertEqual(hygiene["unauthorized_stage_reports"], ["docs/engineering/STAGE_99_UNSOLICITED_REPORT.md"])
        self.assertEqual(hygiene["untracked_forbidden"], ["context/rogue.db"])

    def test_protected_asset_check_passes_on_live_repo_and_flags_touched_paths(self):
        clean = stage_gate.check_protected_assets(ROOT, ["scripts/stage_gate.py"])
        self.assertTrue(clean["ok"])
        dirty = stage_gate.check_protected_assets(ROOT, ["engine_instructions/orchestrator.md"])
        self.assertFalse(dirty["ok"])
        self.assertEqual(dirty["touched"], ["engine_instructions/orchestrator.md"])

    def test_stop_hook_contracts_and_loop_guards(self):
        self.assertEqual(
            hook_stop_verify.format_decision(is_claude=False, allow=True),
            {"decision": "allow"},
        )
        self.assertEqual(
            hook_stop_verify.format_decision(is_claude=False, allow=False, reason="failed"),
            {"decision": "continue", "reason": "failed"},
        )
        self.assertEqual(
            hook_stop_verify.format_decision(is_claude=True, allow=True),
            {},
        )
        self.assertEqual(
            hook_stop_verify.format_decision(is_claude=True, allow=False, reason="failed"),
            {"decision": "block", "reason": "failed"},
        )

        # Env recursion guard
        res_env = hook_stop_verify.evaluate_stop_hook(
            raw_stdin=json.dumps({"hook_event_name": "Stop"}),
            repo_root=ROOT,
            env={"GEM_STOP_HOOK_VERIFYING": "1"},
        )
        self.assertEqual(res_env, {})

        # Claude stop_hook_active infinite-loop guard
        res_claude_loop = hook_stop_verify.evaluate_stop_hook(
            raw_stdin=json.dumps({"hook_event_name": "Stop", "stop_hook_active": True}),
            repo_root=ROOT,
            env={},
        )
        self.assertIn("systemMessage", res_claude_loop)

        # Ignored artifact path filtering
        self.assertTrue(hook_stop_verify.is_ignored_path(".agents/state/last-verification.json"))
        self.assertTrue(hook_stop_verify.is_ignored_path(".agents/logs/agent-events.jsonl"))
        self.assertFalse(hook_stop_verify.is_ignored_path("python/web_server.py"))


class TestSecurityAuditor(unittest.TestCase):
    def test_live_repo_security_audit_has_zero_blocking_findings(self):
        report = audit_security.run_security_audit(repo_root=ROOT)
        blocking = [f for f in report["findings"] if f["severity"] in ("CRITICAL", "HIGH")]
        self.assertEqual(blocking, [], f"Unexpected security findings: {blocking}")
        self.assertTrue(report["ok"])

    def test_security_auditor_catches_injected_secret_cors_bind_and_subprocess_flaws(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            (tmp_root / "python").mkdir()
            (tmp_root / "static").mkdir()
            (tmp_root / "python" / "bad.py").write_text(
                "\n".join(
                    [
                        "import subprocess",
                        'SECRET = "AIzaSyD12345678901234567890123456789012"',
                        'allow_origins=["*"]',
                        'uvicorn.run(app, host="0.0.0.0", port=8000)',
                        'subprocess.run(["agy", "--dangerously-skip-permissions"], shell=True)',
                    ]
                ),
                encoding="utf-8",
            )
            (tmp_root / "static" / "index.html").write_text(
                '<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>',
                encoding="utf-8",
            )
            (tmp_root / "package.json").write_text('{"devDependencies":{"marked":"15.0.12","dompurify":"3.2.6"}}', encoding="utf-8")

            report = audit_security.run_security_audit(repo_root=tmp_root)
            self.assertFalse(report["ok"])
            rules = {f["rule"] for f in report["findings"]}
            self.assertTrue(
                {
                    "SECRET_LEAKAGE",
                    "WILDCARD_CORS",
                    "NON_LOOPBACK_BIND",
                    "UNSAFE_SUBPROCESS_FLAG",
                    "SUBPROCESS_SHELL_TRUE",
                    "UNPINNED_CDN_SCRIPT",
                }.issubset(rules),
                f"Missing expected rules in {rules}",
            )


class TestDashboardUiVerifier(unittest.TestCase):
    def test_verify_dashboard_ui_syntax_and_pure_helpers(self):
        node_bin = shutil.which("node")
        if node_bin is None:
            self.skipTest("node not found on PATH")

        syntax = subprocess.run(
            [node_bin, "--check", str(ROOT / "scripts" / "verify_dashboard_ui.js")],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)

        js_probe = (
            "const v = require('./scripts/verify_dashboard_ui.js');"
            "const opts = v.parseArgs(['--url', 'http://127.0.0.1:8000', '--widths', '1280', '--allow-fallback']);"
            "if (opts.widths[0] !== 1280 || !opts.allowFallback) process.exit(2);"
            "const pass = v.evaluatePageMetrics({httpStatus:200, scrollWidth:1280, innerWidth:1280, brokenImages:0, consoleErrors:[], failedSameOriginRequests:[]});"
            "const fail = v.evaluatePageMetrics({httpStatus:500, scrollWidth:1400, innerWidth:1280, brokenImages:1, consoleErrors:['boom'], failedSameOriginRequests:['500 /api/data']});"
            "if (!pass.ok || fail.ok || fail.issues.length !== 5) process.exit(3);"
            "console.log('UI_HELPER_OK');"
        )
        proc = subprocess.run(
            [node_bin, "-e", js_probe],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)
        self.assertIn("UI_HELPER_OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
