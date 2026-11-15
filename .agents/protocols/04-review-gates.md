# Protocol 04: Independent Review & Verification Gates

**Status:** Enforced  
**Standard:** Adapted from `tk-site-new` Protocol 04  

---

## 1. Scope & Purpose

This protocol mandates independent verification and fail-closed quality gates before any implementation task is accepted.

## 2. Gate Definitions & Verification Hierarchy

```
┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
│   Gate 1:       │ ───► │   Gate 2:       │ ───► │   Gate 3:       │ ───► │   Gate 4:       │
│ Implementation  │      │ Automated Tests │      │ Independent Rev │      │ Human Acceptance│
└─────────────────┘      └─────────────────┘      └─────────────────┘      └─────────────────┘
```

1. **Gate 1 — Implementation Completeness:**
   - Code implemented strictly within the approved task scope.
   - Zero modifications to out-of-scope files.
2. **Gate 2 — Automated Verification (Scope-Proportional Testing):**
   - Execution of automated verification satisfying the scope-proportional test plan defined in Protocol 05 and `@INSTRUCTIONS.md`:
     - Targeted baseline tests covering authorised files prior to edits.
     - Modified-module test suites plus the fast core gate (`python -m unittest tests.test_investment_regression tests.test_permissions tests.test_tool_dispatch`) post-edit.
     - Domain-scoped suites (`tests.test_database_*` only when `python/database/**` is modified; `tests.test_market_data_*` and `tests.test_heavy_market_data_separation` only when `python/fetch_stocks.py` or `python/background_intelligence.py` is modified).
     - Heavy stress/timing suites (`test_database_backup_concurrency`, `test_database_migration`, `test_database_projections`, and `TestSubprocessLifecycleAndHardening`) remain opt-in via `RUN_EXTENDED_TESTS=1`.
     - Standard full test suite execution (`python -m unittest discover tests` with `RUN_EXTENDED_TESTS` unset) is reserved for final stage closeout.
   - Zero regressions on required tests; explicit diagnostic documentation for any permitted blocker, intermittent failure, or deferred item.
   - Python syntax check: `python -m compileall -q python tests` passes cleanly with zero errors.
   - Dependency check: `python -m pip check` reports zero broken requirements.
3. **Gate 3 — Independent Code Review (Gemini 4 Argon & Two-Pass Acceptance Cap):**
   - **Gemini 4 Argon** acts as the dedicated read-only Independent Reviewer (separate from the Hydrogen implementation worker) and audits Hydrogen's `git diff` and test results.
   - Evaluates permission enforcement, boundary containment, regression risk, test adequacy under Protocol 05, and code quality.
   - Enforces the **Two-Pass Acceptance Cap**: maximum one independent Argon audit per stage/sub-stage, and at most one `.1` remediation pass solely for concrete runtime, security, or data-integrity defects.
   - Delivers formal acceptance verdicts (`ACCEPTED`, `ACCEPTED WITH LIMITATIONS`, `REMEDIATION REQUIRED`, `INCOMPLETE`, `BLOCKED`) in chat only (zero report bloat in `docs/engineering/`).
4. **Gate 4 — Human Acceptance:**
   - The human repository owner reviews the chat completion report, test evidence, and Argon independent review verdict.
   - Only the human owner performs git commits, pushes, and releases, and authorises transitions between sub-stages.

## 3. Fail-Closed Acceptance Criteria

A task is immediately marked **REJECTED** if:
- Any required automated test fails, or an unresolved defect/regression remains.
- Baseline test results or failure evidence are masked, deleted, or silently modified.
- An agent attempts to bypass, suppress, or excuse relevant software defects using environmental explanations.
- Permitted blockers, intermittent issues, or deferred verifications lack explicit documentation and Human Owner authorization under Protocol 05.
- Independent review evidence is incomplete, an agent attempts to review/accept its own changes, or the Two-Pass Acceptance Cap is bypassed.
- Any Critical or High finding remains unresolved.
- Investment mandates, `engine_instructions/`, or financial risk rules are modified outside explicit Human Owner authorisation.
- An agent attempts an autonomous git commit or push, or proceeds to another stage without authorisation.
