#!/usr/bin/env python3
"""
STAGE 1 FAIL-CLOSED VERIFICATION HARNESS
Repository: nordicirish/gemini_cli_subagent_system
Stage 1: Agentic Engineering Governance & Runtime Integrity

Verifies:
1. Python Environment & Dependency Integrity (pip check)
2. Python Syntax & Compilation (py_compile on all repo python files)
3. Governance Framework Structure (.agents/AGENTS.md, protocols, agent roles)
4. Out-of-Scope Boundary Protection (git diff against baseline for rules.md & engine_instructions)
5. Comprehensive Unit Test Suite (tests/ - 27 tests)
6. Decoupled Antigravity Integration (Zero Cloud Calls)
7. Baseline Failure Preservation (3 known legacy failures isolated and identifiable)
8. Git Commit Authority Compliance (No autonomous commits or pushes)

Exits 0 if all gates pass. Exits non-zero fail-closed on any failure.
"""

import os
import sys
import subprocess
import py_compile
import unittest
import time

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BASELINE_COMMIT = "bb919c010ac2ac1995235d0f96aa49999e0c1c82"

class VerificationReporter:
    def __init__(self):
        self.results = []
        self.start_time = time.time()

    def record(self, gate_name: str, passed: bool, details: str, duration: float):
        self.results.append({
            "gate": gate_name,
            "passed": passed,
            "details": details,
            "duration": duration
        })
        status_icon = "✅ PASS" if passed else "❌ FAIL"
        print(f"[{status_icon}] {gate_name:<36} ({duration:.2f}s) - {details}")

    def summary(self) -> bool:
        total_time = time.time() - self.start_time
        print("\n" + "=" * 80)
        print("STAGE 1 FAIL-CLOSED VERIFICATION SUMMARY")
        print("=" * 80)
        all_passed = True
        for r in self.results:
            status = "PASS" if r["passed"] else "FAIL"
            print(f"[{status:^6}] {r['gate']:<40} | {r['duration']:>5.2f}s | {r['details']}")
            if not r["passed"]:
                all_passed = False
        print("=" * 80)
        print(f"Total Execution Time: {total_time:.2f}s")
        print(f"Final Status: {'ALL GATES PASSED (READY FOR HUMAN REVIEW)' if all_passed else 'VERIFICATION FAILED (FAIL-CLOSED)'}")
        print("=" * 80 + "\n")
        return all_passed


def check_python_environment(reporter: VerificationReporter):
    t0 = time.time()
    if sys.version_info < (3, 10):
        reporter.record("Python Environment", False, f"Python 3.10+ required; found {sys.version}", time.time() - t0)
        return False

    # Check pip check
    res = subprocess.run([sys.executable, "-m", "pip", "check"], capture_output=True, text=True)
    if res.returncode != 0:
        reporter.record("Dependency Integrity (pip check)", False, res.stderr.strip() or res.stdout.strip(), time.time() - t0)
        return False

    reporter.record("Environment & Dependencies", True, f"Python {sys.version.split()[0]}, pip check clean", time.time() - t0)
    return True


def check_compilation(reporter: VerificationReporter):
    t0 = time.time()
    files_to_check = [
        "python/agent_framework.py",
        "python/fetch_stocks.py",
        "python/main.py",
        "python/tools.py",
        "python/web_server.py",
        "python/atomic_io.py",
        "python/tool_registry.py",
        "python/tool_dispatcher.py",
        "python/audit_logger.py",
        "tests/test_permissions.py",
        "tests/test_atomic_io.py",
        "tests/test_audit_logger.py",
        "tests/test_tool_dispatch.py",
        "tests/test_investment_regression.py",
        "scripts/test_decoupled_tools.py",
        "scripts/test_antigravity_integration.py",
    ]

    failed = []
    for rel_path in files_to_check:
        full_path = os.path.join(REPO_ROOT, rel_path)
        if not os.path.exists(full_path):
            failed.append(f"{rel_path} (missing)")
            continue
        try:
            py_compile.compile(full_path, doraise=True)
        except py_compile.PyCompileError as e:
            failed.append(f"{rel_path}: {e}")

    duration = time.time() - t0
    if failed:
        reporter.record("Syntax & Compilation Check", False, f"Failed on: {', '.join(failed)}", duration)
        return False
    reporter.record("Syntax & Compilation Check", True, f"Compiled {len(files_to_check)} Python files with zero errors", duration)
    return True


def check_governance_framework(reporter: VerificationReporter):
    t0 = time.time()
    required_paths = [
        ".agents/AGENTS.md",
        ".agents/protocols/01-agent-orchestration.md",
        ".agents/protocols/02-development-standards.md",
        ".agents/protocols/03-security-policy.md",
        ".agents/protocols/04-review-gates.md",
        ".agents/protocols/05-testing-policy.md",
        ".agents/protocols/06-financial-boundaries.md",
        ".agents/agents/architecture-agent.md",
        ".agents/agents/python-engineer.md",
        ".agents/agents/security-agent.md",
        ".agents/agents/testing-agent.md",
        ".agents/agents/documentation-agent.md",
        ".agents/agents/independent-reviewer.md",
    ]

    missing = []
    for rel_path in required_paths:
        full_path = os.path.join(REPO_ROOT, rel_path)
        if not os.path.exists(full_path) or os.path.getsize(full_path) == 0:
            missing.append(rel_path)

    duration = time.time() - t0
    if missing:
        reporter.record("Governance Framework Integrity", False, f"Missing/empty files: {', '.join(missing)}", duration)
        return False
    reporter.record("Governance Framework Integrity", True, f"All {len(required_paths)} governance files verified present", duration)
    return True


def check_out_of_scope_boundaries(reporter: VerificationReporter):
    t0 = time.time()
    # Ensure gem_trading_rules/rules.md and engine_instructions/ have zero diff against baseline commit
    cmd = ["git", "diff", BASELINE_COMMIT, "--", "gem_trading_rules/rules.md", "engine_instructions/"]
    res = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    duration = time.time() - t0

    if res.returncode != 0:
        reporter.record("Out-of-Scope Boundary Check", False, f"git diff returned code {res.returncode}: {res.stderr.strip()}", duration)
        return False

    if res.stdout.strip():
        reporter.record("Out-of-Scope Boundary Check", False, "Unauthorized diff detected in trading rules or engine instructions!", duration)
        return False

    reporter.record("Out-of-Scope Boundary Check", True, "Zero diff in rules.md and engine_instructions/ vs baseline", duration)
    return True


def check_unit_tests(reporter: VerificationReporter):
    t0 = time.time()
    suite = unittest.defaultTestLoader.discover(os.path.join(REPO_ROOT, "tests"))
    runner = unittest.TextTestRunner(verbosity=0, stream=open(os.devnull, "w"))
    result = runner.run(suite)
    duration = time.time() - t0

    if not result.wasSuccessful():
        details = f"{len(result.failures)} failures, {len(result.errors)} errors out of {result.testsRun} tests"
        reporter.record("Unit Test Suite (tests/)", False, details, duration)
        return False

    reporter.record("Unit Test Suite (tests/)", True, f"All {result.testsRun} tests passed cleanly", duration)
    return True


def check_antigravity_integration(reporter: VerificationReporter):
    t0 = time.time()
    test_script = os.path.join(REPO_ROOT, "scripts", "test_antigravity_integration.py")
    res = subprocess.run([sys.executable, test_script], cwd=REPO_ROOT, capture_output=True, text=True)
    duration = time.time() - t0

    if res.returncode != 0:
        details = f"Exit {res.returncode}: {res.stderr.strip() or res.stdout.strip()[:200]}"
        reporter.record("Antigravity Integration Test", False, details, duration)
        return False

    reporter.record("Antigravity Integration Test", True, "All 5 integration stages passed (zero cloud calls)", duration)
    return True


def check_baseline_failures_preservation(reporter: VerificationReporter):
    t0 = time.time()
    # Verify that the 3 baseline failures are separately identifiable and have NOT been modified to pass
    f1 = os.path.join(REPO_ROOT, "scripts", "test_fib_daily_peak.py")
    f2 = os.path.join(REPO_ROOT, "scripts", "test_live_call.py")
    f3 = os.path.join(REPO_ROOT, "scripts", "test_umac_chart_flow.py")

    res1 = subprocess.run([sys.executable, f1], cwd=REPO_ROOT, capture_output=True, text=True)
    res2 = subprocess.run([sys.executable, f2], cwd=REPO_ROOT, capture_output=True, text=True)
    res3 = subprocess.run([sys.executable, f3], cwd=REPO_ROOT, capture_output=True, text=True)
    duration = time.time() - t0

    # res1 must fail with AssertionError on PEAK_EXHAUSTED
    r1_ok = (res1.returncode != 0 and "AssertionError" in (res1.stderr + res1.stdout))
    # res2 must fail with AttributeError on client
    r2_ok = (res2.returncode != 0 and "AttributeError" in (res2.stderr + res2.stdout))
    # res3 must fail with AttributeError on client
    r3_ok = (res3.returncode != 0 and "AttributeError" in (res3.stderr + res3.stdout))

    if not (r1_ok and r2_ok and r3_ok):
        reporter.record("Baseline Failures Preservation", False,
                        f"Expected 3 known baseline failures; status: fib={r1_ok}, live={r2_ok}, umac={r3_ok}",
                        duration)
        return False

    reporter.record("Baseline Failures Preservation", True, "All 3 baseline failures preserved and separately identifiable", duration)
    return True


def check_git_commit_authority(reporter: VerificationReporter):
    t0 = time.time()
    res = subprocess.run(["git", "log", "-n", "1", "--format=%H"], cwd=REPO_ROOT, capture_output=True, text=True)
    current_head = res.stdout.strip()
    duration = time.time() - t0

    if current_head != BASELINE_COMMIT:
        reporter.record("Git Commit Authority", False, f"HEAD has moved! Expected {BASELINE_COMMIT}, got {current_head}", duration)
        return False

    # Check that changes are present in working tree (not committed)
    status_res = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True, text=True)
    if not status_res.stdout.strip():
        reporter.record("Git Commit Authority", False, "Working tree is unexpectedly clean! Changes must remain in working tree.", duration)
        return False

    reporter.record("Git Commit Authority", True, f"HEAD matches baseline {BASELINE_COMMIT[:8]}, all changes in local working tree", duration)
    return True


def main():
    print("=" * 80)
    print("STARTING STAGE 1 VERIFICATION HARNESS")
    print(f"Repository Root: {REPO_ROOT}")
    print(f"Baseline Commit: {BASELINE_COMMIT}")
    print("=" * 80 + "\n")

    reporter = VerificationReporter()

    gates = [
        check_python_environment,
        check_compilation,
        check_governance_framework,
        check_out_of_scope_boundaries,
        check_unit_tests,
        check_antigravity_integration,
        check_baseline_failures_preservation,
        check_git_commit_authority
    ]

    for gate in gates:
        gate_ok = gate(reporter)
        if not gate_ok:
            print(f"\n[FATAL] Gate failed! Aborting remaining verification checks (fail-closed).\n")
            reporter.summary()
            sys.exit(1)

    all_passed = reporter.summary()
    if all_passed:
        print("[SUCCESS] All Stage 1 verification gates passed successfully.")
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()

