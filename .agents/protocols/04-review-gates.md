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
   - A dedicated Independent Reviewer agent conducts a read-only audit of the diff.
   - Evaluates permission enforcement, boundary containment, regression risk, and code quality.
   - Emits a formal review artifact in `.agents/reviews/`.
4. **Gate 4 — Human Acceptance:**
   - The human repository owner reviews the completion report and diff.
   - Only the human owner performs git commits, pushes, and releases.

## 3. Fail-Closed Acceptance Criteria

A task is immediately marked **REJECTED** if:
- Any automated test fails.
- Baseline test results are masked, deleted, or silently modified.
- An independent review artifact is missing.
- Any Critical or High finding remains unresolved.
- Investment mandates or financial risk rules are modified without human approval.
- An agent attempts an autonomous git commit or push.
