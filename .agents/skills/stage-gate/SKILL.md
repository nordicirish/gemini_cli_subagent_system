---
name: stage-gate
description: Execute the mandatory read-only pre-commit verification gate, protected-asset invariance check, artifact hygiene scan, and Protocol 05 scope-proportional unittest suite before issuing a stage verdict or Owner Git commands.
---

# Stage-Gate Verification Skill (`/stage-gate`)

Run this workflow automatically before issuing any stage completion report or Argon acceptance verdict, or whenever invoked via `/stage-gate`.

## Step 1: Read-Only Git & Scope Inspection
1. Run `git branch --show-current` and `git rev-parse HEAD`.
2. Run `git status --short` and `git diff --name-only` to list all modified, untracked, or deleted files in the working tree.
3. Verify that every modified or untracked file falls strictly within the active stage's Authorised File Scope. Flag any out-of-scope file immediately as a blocker.

## Step 2: Protected Financial Asset Invariance Gate (Protocol 06)
1. Run:
   `git diff --exit-code -- engine_instructions/ gem_trading_rules/rules.md context/ssot.json context/config.json`
2. If the exit code is non-zero (unless explicitly authorised by the Human Owner for that specific stage), STOP and report a **CRITICAL PROTOCOL 06 VIOLATION**.

## Step 3: Repository Hygiene & Forbidden Artifact Gate
Inspect `git status --short` and verify:
1. Zero new report files exist under `docs/engineering/STAGE_*.md`.
2. Zero lock files (`*.lock`, `*.restore_lock`) exist in the working tree.
3. If `context/gem.db` or `context/gem.db.restore_lock` appear as tracked in the Git index, include `git rm --cached context/gem.db context/gem.db.restore_lock` in the Owner Git commands.

## Step 4: Scope-Proportional Test Routing (Protocol 05)
Select and execute ONLY the test suites matching the modified paths in `git diff --name-only` (unless final milestone closeout is requested):

1. **Fast Core Gate (Run whenever any `python/` file is modified):**
   `python -m unittest tests.test_investment_regression tests.test_permissions tests.test_tool_dispatch`
2. **Agent, Council & Web Server Gate (Run when `python/agent_framework.py`, `python/web_server.py`, `python/tools.py`, or `static/` is modified):**
   `python -m unittest tests.test_agent_framework tests.test_web_chat_endpoint tests.test_watchlist_endpoints`
3. **Persistence & Database Gate (Run ONLY when `python/database/**` or `python/persistence/**` is modified):**
   `python -m unittest tests.test_database_core tests.test_database_repositories tests.test_persistence_service tests.test_runtime_persistence_cutover tests.test_atomic_io tests.test_audit_logger`
4. **Market Data & Background Daemon Gate (Run ONLY when `python/fetch_stocks.py` or `python/background_intelligence.py` is modified):**
   `python -m unittest tests.test_market_data_integrity tests.test_market_data_acquisition tests.test_heavy_market_data_separation tests.test_market_data_pipeline_integration tests.test_background_intelligence`
5. **Final Milestone Closeout Gate (Run ONLY when closing a major stage):**
   `python -m unittest discover tests`
6. **Compilation & Dependency Gate:**
   `python -m compileall -q python tests`
   `python -m pip check`

## Step 5: Structured Gate Output
Output a concise **Stage-Gate Report** in chat containing:
1. **Branch & HEAD SHA**
2. **Scope & Protected Asset Check:** `PASS` / `FAIL` (with exact files touched)
3. **Artifact Hygiene Check:** `PASS` / `FAIL`
4. **Scoped Tests Executed:** Exact commands, test counts, duration, and exit codes
5. **Argon Acceptance Verdict:** `ACCEPTED`, `ACCEPTED WITH LIMITATIONS`, or `REMEDIATION REQUIRED`
6. **Owner Git Commands:** Exact `git add` and `git commit -m "..."` commands ready for the Human Owner to copy-paste.
