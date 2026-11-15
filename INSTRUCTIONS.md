# ANTIGRAVITY WORKSPACE — STANDING OPERATING INSTRUCTIONS

## 1. OPERATING PRINCIPLE (NATIVE ORCHESTRATOR)
- **Gemini 4 Argon (Lead Architect, Planner & Independent Reviewer):** Performs read-only repository inspection, defines bounded stage file scopes and invariants, and independently audits Hydrogen's `git diff` and test results before issuing formal verdicts (`ACCEPTED`, `ACCEPTED WITH LIMITATIONS`, `REMEDIATION REQUIRED`, `INCOMPLETE`, `BLOCKED`).
- **Gemini 4 Hydrogen (Bounded Implementation & Testing Worker):** Implements explicitly authorised file scopes only, runs scope-proportional tests, never expands scope, and never self-approves work.
- **Human Owner:** Retains final authority over stage authorisations, scope changes, protected assets, and all Git write operations.
- **Two-Pass Acceptance Cap:** Cap review depth to one independent Argon audit per stage; only concrete runtime, security, or data-integrity defects trigger a `.1` remediation pass.
- **Delta-Only Stage Prompts:** Standing operating rules live in `@INSTRUCTIONS.md`; stage prompts specify only stage-specific deltas (baseline HEAD, objective, authorised file scope, technical requirements, and verification commands).

## 2. STRICT GIT WRITE PROHIBITION
- **PROHIBITED:** `git commit`, `amend`, `push`, `pull`, `fetch`, `branch`, `checkout`, `switch`, `merge`, `reset`, `rebase`, `cherry-pick`, `rm`, or any history mutation.
- **PERMITTED:** Read-only Git inspection (`git status --short`, `git rev-parse HEAD`, `git branch --show-current`, `git diff`).

## 3. PROTECTED FINANCIAL ASSETS & DOMAIN BOUNDARIES
- **Strictly Read-Only Protected Assets:**
  - `engine_instructions/*.md`
  - `gem_trading_rules/rules.md`
  - `context/ssot.json`
  - `context/config.json`
- Engineering workers must NEVER modify protected assets, trading mandates, risk thresholds, indicator formulas, or position-sizing logic, and must NEVER run global version-string replacements across protected files (`antigravity.md` Mandate 13 is superseded).
- Production database files (`context/*.db`, `cache/*.db`) and `.restore_lock` / `.lock` files must never be tracked in Git or mutated outside authorised tests.

## 4. STANDING NEGATIVE INVARIANTS
1. **Failure & Cleanup Atomicity:** Roll back incomplete transactions and release all locks, connections, cursors, files, subprocesses, and tokens in `try...finally` blocks on every exit path without concealing primary exceptions.
2. **Strict Type, Timestamp & Capability Validation:**
   - Use `Decimal` for all financial values; reject `float` at financial boundaries and never silently coerce malformed values or substitute zero/defaults.
   - Enforce strict UTC ISO-8601 (`Z` or `+00:00`) or validated `America/New_York` session timestamps; reject naive, ambiguous, or `-00:00` timestamps.
   - Explicitly verify capability, permission, and actor provenance (`authorize_financial_mutation()`) before any financial state mutation. Fail closed if uncertain.
3. **Zero Production Test Seams:** Never add test-only flags, mock hooks, environment switches, or bypass parameters to production code.
4. **Chat-Only Reports:** Deliver all stage plans, completion reports, and Argon review verdicts in chat only. Never create `docs/engineering/STAGE_*.md` files unless explicitly authorised by the Human Owner.

## 5. SCOPE-PROPORTIONAL TESTING POLICY (PROTOCOL 05)
- **Baseline Gate:** Verify `git rev-parse HEAD` and `git status --short`, and run ONLY the targeted `unittest` module(s) covering the files in Authorised Scope before editing.
- **Post-Edit Scoped Gate:**
  - Run modified-module test suites plus the fast core gate: `python -m unittest tests.test_investment_regression tests.test_permissions tests.test_tool_dispatch`.
  - Run `tests.test_database_*` ONLY when `python/database/**` is modified.
  - Run `tests.test_market_data_*` and `tests.test_heavy_market_data_separation` ONLY when `python/fetch_stocks.py` or `python/background_intelligence.py` is modified.
  - Heavy stress/timing suites (`test_database_backup_concurrency`, `test_database_migration`, `test_database_projections`) remain opt-in via `RUN_EXTENDED_TESTS=1`.
  - Preserve the owner-authorised intermittent classification for `tests.test_background_intelligence.TestSubprocessLifecycleAndHardening.test_run_prompt_timeout_triggers_cleanup_and_preserves_exception` (*Intermittent Contention / Timing*) without weakening or deleting the test.
- **Final Stage Closeout:** Execute `python -m unittest discover tests`, `python -m compileall -q python tests`, and `python -m pip check` at the final acceptance gate of a major stage.