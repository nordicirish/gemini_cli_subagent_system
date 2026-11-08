# Protocol 05: Automated Testing & Verification Policy

**Status:** Enforced  
**Standard:** Adapted from `tk-site-new` Protocol 05  

---

## 1. Scope & Purpose

This protocol defines testing standards, test discovery conventions, regression coverage, and evidence reporting across all repository components.

## 2. Test Architecture & Framework

1. **Standard Library Testing:**
   - Unit and integration tests must utilize the Python standard library `unittest` framework to avoid introducing external dependencies.
   - Tests are located under the `tests/` directory with `test_*.py` naming conventions, discoverable via `python -m unittest discover tests`.
2. **Test Categories:**
   - **Permission Tests (`test_permissions.py`):** Verify unauthorized tools are rejected, role permissions are enforced, and sensitive operations require approval.
   - **Execution Tests (`test_tool_dispatch.py`):** Verify tool dispatch parity between CLI and Web, multi-turn loop limits, timeout handling, and subagent tool routing.
   - **Persistence Tests (`test_atomic_io.py`):** Verify atomic replacement, concurrency locking, data integrity under simulated write failures, and preservation of existing state.
   - **Audit Tests (`test_audit_logger.py`):** Verify structured event logging, secret redaction, and append-only integrity.
   - **Regression Tests (`test_investment_regression.py`):** Verify zero changes to mathematical formulas, Fibonacci levels, SSoT schema integrity, and trading rule mandates.

## 3. Truth in Reporting

1. **Empirical Evidence Only:** Agents must run tests and report actual stdout, stderr, and return codes. Inferring test outcomes without execution is strictly prohibited.
2. **Baseline Failures Preserved:** Pre-existing baseline failures must be reported transparently and not silently deleted or modified.
