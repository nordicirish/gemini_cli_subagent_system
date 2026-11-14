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
   - Full test suite execution with zero regressions.
   - Python syntax check: `python -m py_compile` passes cleanly.
   - Dependency check: `pip check` reports no broken requirements.
3. **Gate 3 — Independent Code Review:**
   - A dedicated read-only Independent Reviewer (separate from the implementer, or the owner-designated external reviewer inspecting the repository) audits the diff.
   - Evaluates permission enforcement, boundary containment, regression risk, and code quality.
   - Issues a formal acceptance recommendation (`.agents/reviews/` when recorded locally).
4. **Gate 4 — Human Acceptance:**
   - The human repository owner reviews the completion report, test evidence, and independent review verdict.
   - Only the human owner performs git commits, pushes, and releases, and authorises any subsequent stage.

## 3. Fail-Closed Acceptance Criteria

A task is immediately marked **REJECTED** if:
- Any automated test fails.
- Baseline test results are masked, deleted, or silently modified.
- Independent review evidence is incomplete or an agent attempts to review/accept its own changes.
- Any Critical or High finding remains unresolved.
- Investment mandates, `engine_instructions/`, or financial risk rules are modified outside explicit Human Owner authorisation.
- An agent attempts an autonomous git commit or push, or proceeds to another stage without authorisation.
