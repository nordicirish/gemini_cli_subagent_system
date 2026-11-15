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
2. **Gate 2 — Automated Verification:**
   - Execution of automated verification satisfying the risk-proportionate test plan defined in Protocol 05:
     - Relevant targeted and regression tests for every implementation change.
     - Relevant integration tests where changes affect component interfaces, state boundaries, concurrency, persistence, or queues.
     - Standard full test suite execution (`python -m unittest discover tests` with `RUN_EXTENDED_TESTS` unset) for cross-cutting architectural refactoring, financial-integrity, security-sensitive, background coordinator, and final stage remediation changes, invoking the extended tier (`RUN_EXTENDED_TESTS=1`) only when backup concurrency, offline migration, database projections, or subprocess lifecycle code is in scope, adhering to Protocol 05.
   - Zero regressions on required tests; explicit diagnostic documentation for any permitted blocker, intermittent failure, or deferred item.
   - Python syntax check: `python -m py_compile` (or `python -m compileall -q python tests`) passes cleanly with zero errors.
   - Dependency check: `pip check` reports zero broken requirements.
3. **Gate 3 — Independent Code Review:**
   - A dedicated read-only Independent Reviewer (separate from the implementer, or the owner-designated external reviewer inspecting the repository) audits the diff.
   - Evaluates permission enforcement, boundary containment, regression risk, test adequacy under Protocol 05, and code quality.
   - Issues a formal acceptance recommendation (`.agents/reviews/` when recorded locally).
4. **Gate 4 — Human Acceptance:**
   - The human repository owner reviews the completion report, test evidence, and independent review verdict.
   - Only the human owner performs git commits, pushes, and releases, and authorises any subsequent stage.

## 3. Fail-Closed Acceptance Criteria

A task is immediately marked **REJECTED** if:
- Any required automated test fails, or an unresolved defect/regression remains.
- Baseline test results or failure evidence are masked, deleted, or silently modified.
- An agent attempts to bypass, suppress, or excuse relevant software defects using environmental explanations.
- Permitted blockers, intermittent issues, or deferred verifications lack explicit documentation and Human Owner authorization under Protocol 05.
- Independent review evidence is incomplete or an agent attempts to review/accept its own changes.
- Any Critical or High finding remains unresolved.
- Investment mandates, `engine_instructions/`, or financial risk rules are modified outside explicit Human Owner authorisation.
- An agent attempts an autonomous git commit or push, or proceeds to another stage without authorisation.
