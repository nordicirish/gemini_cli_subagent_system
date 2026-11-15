# Protocol 05: Automated Testing & Verification Policy

**Status:** Enforced  
**Standard:** Adapted from `tk-site-new` Protocol 05  

---

## 1. Scope & Purpose

This protocol establishes the repository's authoritative testing standards, verification categories, risk-proportionate execution requirements, external dependency handling, baseline failure investigations, and evidence reporting across all engineering stages.

The objective is to maintain rigorous software correctness, financial integrity, security boundaries, and acceptance standards while avoiding unnecessary development delays caused by external-provider outages, market-hour constraints, hardware scheduling jitter, or deployment-stage dependencies.

## 2. Test Architecture & Framework

1. **Standard Library Testing:**
   - Unit and integration tests must utilize the Python standard library `unittest` framework to avoid introducing external test dependencies.
   - Tests are located under the `tests/` directory with `test_*.py` naming conventions, discoverable via:
     ```bash
     python -m unittest discover tests
     ```
   - Do not mandate or install `pytest` or introduce external test runners.
2. **Core Test Suites:**
   - **Permission Tests (`test_permissions.py`):** Verify unauthorized tools are rejected, role permissions are enforced, and sensitive operations require approval.
   - **Execution Tests (`test_tool_dispatch.py`):** Verify tool dispatch parity between CLI and Web, multi-turn loop limits, timeout handling, and subagent tool routing.
   - **Persistence Tests (`test_atomic_io.py`):** Verify atomic replacement, concurrency locking, data integrity under simulated write failures, and preservation of existing state.
   - **Audit Tests (`test_audit_logger.py`):** Verify structured event logging, secret redaction, and append-only integrity.
   - **Regression Tests (`test_investment_regression.py`):** Verify zero changes to mathematical formulas, Fibonacci levels, SSoT schema integrity, and trading rule mandates.
   - **Market Data Integrity & Concurrency (`test_market_data_integrity.py`, `test_market_data_acquisition.py`, `test_background_intelligence.py`):** Verify provider data provenance, non-blocking asynchronous acquisition, cache publication atomicity, and session freshness rules.

## 3. Testing Categories

All verification must be classified into one of four distinct categories:

### 1. Deterministic Automated Tests
- **Scope:** Unit tests, boundary validation, input sanitization, mathematical and financial calculations, state machine transitions, provenance and freshness validation, failure paths, and regression assertions.
- **Requirements:**
  - Must be fully repeatable and deterministic.
  - Must use controlled in-memory fixtures, mock data, or synthetic objects.
  - Must not depend on live network services, provider API keys, or external market hours.
  - Must pass completely for relevant stage acceptance.
  - Must never be weakened, suppressed, or silently bypassed.

### 2. Controlled Integration Tests
- **Scope:** Database interactions, disk persistence, atomic file replacement, concurrency and locking, queues, background worker coordinators, cache publication, and multi-component interfaces.
- **Requirements:**
  - Must utilize controlled fixtures, temporary directories, isolated coordinators, and mock provider endpoints wherever practicable.
  - Required whenever an implementation touches shared interfaces, state boundaries, persistence, or concurrency.
  - Concurrency or lock timeouts must be investigated as potential race conditions or contention defects; they must not be dismissed as external provider issues.

### 3. External Dependency & Live-Provider Tests
- **Scope:** Live integrations with external market data providers (e.g., Yahoo Finance, Finnhub, Polygon) or network services.
- **Requirements:**
  - Routine automated tests and baseline gates must prefer deterministic provider fixtures and mocked responses over live network calls.
  - Ordinary deterministic acceptance must never depend on live third-party service uptime or off-hours provider semantics.
  - Live smoke tests should be scheduled when market hours, credentials, and network connectivity make them meaningful.
  - Provider outages or network unavailability must be reported separately from application code defects.
  - Live provider failures must never be silently masked by substituting a mock and claiming live integration passed.
  - Passing mocked tests does not establish live provider compliance where live integration is the explicit subject of the stage.

### 4. Environment & Deployment Tests
- **Scope:** Cloud infrastructure, cloud credentials/networking, deployment scripts, operational monitoring, production hardware performance, and production-scale workload testing.
- **Requirements:**
  - May be formally deferred until the corresponding deployment, infrastructure, or operational stage only with explicit Human Owner authorization.
  - A deferral cannot automatically be treated as an accepted limitation.
  - Any proposed deferral must be explicitly recorded as deferred verification documenting its stated rationale, diagnostic evidence, remaining risk, affected verification, and designated future verification stage.
  - A deferral must never waive mandatory financial-integrity, security, authorization, or deterministic correctness requirements.
  - All deferred verification items relevant to a release must be validated prior to the associated production release.

## 4. Test Suite Tiers & Risk-Proportionate Test Execution

Verification is partitioned into two distinct execution tiers:

1. **Standard Test Tier (Default):**
   - Deterministic, relevant unit and integration tests executed during ordinary development, targeted verification, and standard full-suite acceptance (`python -m unittest discover tests` with `RUN_EXTENDED_TESTS` unset).
   - All tests in this tier must pass cleanly for relevant stage acceptance.
2. **Extended Test Tier (Opt-In via `RUN_EXTENDED_TESTS=1`):**
   - High-overhead stress, real-timeout concurrency, timing-contention, and offline migration suites:
     - `tests/test_database_backup_concurrency.py`
     - `tests/test_database_migration.py`
     - `tests/test_database_projections.py`
     - `tests/test_background_intelligence.py` (`TestSubprocessLifecycleAndHardening`)
   - Skipped by default in standard discovery using `@unittest.skipUnless(os.getenv("RUN_EXTENDED_TESTS") == "1", "Extended stress/timing suite (opt-in)")`.
   - Executed only when backup concurrency, offline migration, database projections, or subprocess lifecycle code is explicitly in the authorised implementation scope.

### Scope-Proportional Verification Execution

Every implementation task must follow a test execution plan proportionate to its scope and risk tier:

1. **Baseline Gate (Pre-Edit):**
   - Verify `git rev-parse HEAD` and `git status --short`, and run only the targeted `unittest` module(s) relevant to files in the authorised scope before editing.
   - Do not mandate running `python -m unittest discover tests` as a routine pre-edit baseline.
   - Do not run repository-wide discovery twice per stage.
   - Do not execute unrelated extended suites as a routine baseline.
   - Record exact commands, test counts, durations, and outcomes in the chat report.
   - If a targeted baseline reveals a relevant failure, capture diagnostic evidence, classify the failure, and halt immediately if safe progress is blocked.
2. **Post-Edit Gate (Scope-Proportional Selection):**
   - Run modified-module test suites plus the fast core gate: `python -m unittest tests.test_investment_regression tests.test_permissions tests.test_tool_dispatch`.
   - Run `tests.test_database_*` only when `python/database/**` is in the authorised implementation scope.
   - Run `tests.test_market_data_*` and `tests.test_heavy_market_data_separation` only when `python/fetch_stocks.py` or `python/background_intelligence.py` is in the authorised implementation scope.
   - Heavy stress/timing suites (`test_database_backup_concurrency`, `test_database_migration`, `test_database_projections`, and `TestSubprocessLifecycleAndHardening`) remain opt-in via `RUN_EXTENDED_TESTS=1`, executed only when backup concurrency, offline migration, database projections, or subprocess lifecycle code is explicitly modified.
   - Preserve the owner-authorised intermittent classification for `tests.test_background_intelligence.TestSubprocessLifecycleAndHardening.test_run_prompt_timeout_triggers_cleanup_and_preserves_exception` (*Intermittent Contention / Timing*) without weakening or deleting the test.
   - Compilation checks (`python -m compileall -q python tests`) and dependency integrity checks (`python -m pip check`) remain mandatory post-edit gates.
   - Never treat an omitted test as passed. Always report why it was not run.
3. **Final Stage Closeout & Full-Suite Testing:**
   - Standard full-suite execution (`python -m unittest discover tests` with `RUN_EXTENDED_TESTS` unset, plus `python -m compileall -q python tests` and `python -m pip check`) is reserved for the final acceptance gate of a major stage closeout (invoking `RUN_EXTENDED_TESTS=1` only if extended domains were modified).
   - Incremental sub-stages rely on Scope-Proportional Testing (Items 1 and 2) to eliminate redundant repository-wide test runs.
4. **Isolated Low-Risk Changes:** Isolated documentation, UI formatting, or leaf-node bug fixes do not require full-suite execution unless specified by repository instructions or the authorized task prompt.
5. **Transparency:** Never silently skip a required test. Always report exact test commands, tests executed, tests omitted, and the reasons.
6. **Limits of Testing:** A passing test suite is necessary but never sufficient; it does not replace independent source-code review or governance compliance verification.

## 5. Baseline Failure & Environmental Instability Protocol

When running baseline or verification suites, any failure must be captured and evaluated systematically:

1. **Mandatory Failure Record:**
   - Full test identifier (`test_module.ClassName.test_name`).
   - Exact execution command line.
   - Standard output and standard error.
   - Process exit code from the execution environment (e.g., `$LASTEXITCODE` in PowerShell, `%ERRORLEVEL%` in Command Prompt, or `$?` in POSIX shells).
   - Execution environment details (OS, Python version, system load, time of day/market session).
   - Whether the failure was reproduced upon controlled inspection.
   - Source code analysis of the failure mechanism.
2. **Failure Classification:**
   - **Reproducible Defect:** A consistent failure indicating broken logic, invalid assertions, or regression. Must be resolved before acceptance.
   - **Intermittent Contention / Timing:** A failure occurring under resource contention or concurrency jitter that does not reproduce in isolation. Must remain documented and investigated; a passing rerun does not erase the failure.
   - **External Dependency Failure:** A failure caused by third-party service downtime, rate limiting, or off-hours data semantics.
   - **Environmental Failure:** A failure directly attributable to host system conditions (e.g., disk full, OS permission lock, hardware interruption).
3. **Investigation & Anti-Gaming Safeguards:**
   - **No Repeated Reruns:** Repeatedly running a failing test in a loop hoping for a transient pass is strictly forbidden.
   - **No Silent Modification:** Never alter test assertions, skip decorators, or production code to conceal baseline failures.
   - **No Automatic Acceptance of Blockers or Deferrals:** A blocker, intermittent failure, or deferral cannot automatically be treated as an accepted limitation. Any proposed blocker or deferral must document its stated rationale, diagnostic evidence, remaining risk, affected verification, and (for deferred items) the designated future verification stage. Explicit Human Owner authorization is mandatory before any blocker or deferral can be accepted as a permitted limitation.
   - **Demonstrably Unrelated Baseline Failures:** If a pre-existing baseline failure is demonstrably unrelated to the authorized scope and cannot compromise the safety or correctness of the change, work may proceed only if explicitly authorized by the Human Owner with the failure documented as an unresolved baseline limitation.
   - **Unresolved Relevant Defects Remain Fail-Closed:** If an unresolved failure or defect intersects the modified code or its dependencies, execution must halt immediately (fail-closed). Deferrals or environmental explanations cannot excuse unresolved relevant defects.

## 6. Truth in Reporting & Acceptance Categories

All verification reporting in Gemini completion handovers must classify test outcomes into the five standard states:

- **Passed:** Required verification completed with zero errors and exit code 0.
- **Failed:** A reproducible failure or confirmed defect.
- **Intermittent:** A failure observed during execution that did not consistently reproduce, recorded with its full diagnostic evidence.
- **Blocked:** Verification could not proceed due to an external service dependency or environmental limitation.
- **Deferred:** Verification formally postponed to a designated subsequent deployment/infrastructure stage.

**Reporting Rules:**
- Never describe blocked, intermittent, or deferred tests as "Passed".
- Explicit Human Owner authorization is required before any blocked, intermittent, or deferred state can be accepted as a permitted limitation.
- Any blocked or deferred report must document its reason, diagnostic evidence, remaining risk, affected verification, and (for deferred items) the designated future verification stage.
- A deferral cannot waive deterministic correctness, security, financial integrity, or authorization requirements. All deferred verifications relevant to a release must be completed before the associated production release.
- Unresolved relevant defects remain fail-closed.
- Report actual numbers for tests run, failures, errors, and skips directly from the runner output. Inferring test outcomes without execution is strictly prohibited.
