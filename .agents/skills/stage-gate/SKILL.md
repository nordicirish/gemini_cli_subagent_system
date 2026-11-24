---
name: stage-gate
description: Execute the mandatory read-only pre-commit verification gate, protected-asset invariance check, artifact hygiene scan, and Protocol 05 scope-proportional unittest suite before issuing a stage verdict or Owner Git commands.
---

# Stage-Gate Verification Skill (`/stage-gate`)

Run this workflow automatically before issuing any stage completion report or Argon acceptance verdict, or whenever invoked via `/stage-gate`.

## Automated Runner (Preferred)
Execute the machine-enforced gate first:
```powershell
.\.venv\Scripts\python.exe scripts/stage_gate.py --scoped
```
(Add `--all` for final milestone closeout, or `--json` for machine-readable output. Also run `.\.venv\Scripts\python.exe scripts/audit_security.py` when modifying security-sensitive files in `python/` or `static/index.html`.)

`scripts/stage_gate.py` automates all four manual gates below:
1. **Read-Only Git & Scope Inspection:** `git branch --show-current`, `git rev-parse HEAD`, `git status --porcelain -uall`.
2. **Protected Financial Asset Invariance Gate (`Protocol 06`):** `git diff --exit-code` against `engine_instructions/`, `gem_trading_rules/rules.md`, `context/ssot.json`, `context/trade_lessons.json`, `context/decision_log.json`, `context/config.json`.
3. **Artifact Hygiene, Manifest Parity & Directive Size Gate:** Rejects tracked `.db`/`.lock`/secret files, rejects unauthorized `docs/engineering/STAGE_*.md` files, validates `docs/agents/roles.json` parity via `scripts/sync_agent_manifests.py`, and verifies `< 23,500`-byte directive ceilings and `< 150`-line `docs/governance/project-state.md`.
4. **Scope-Proportional Test Routing (`Protocol 05` + JS Tests):**
   - `python/` edits -> fast core gate (`test_investment_regression`, `test_permissions`, `test_tool_dispatch`) + module-matched suites (`test_database_*` only for `python/database/`, `test_market_data_*` / `test_heavy_market_data_separation` only for `fetch_stocks.py` / `background_intelligence.py`, `test_agent_framework` / `test_web_chat_endpoint` / `test_watchlist_endpoints` / `test_api_routes` for web/agent edits).
   - `static/` or `tests/js/` edits -> `test_dashboard_scripts`, `test_chat_sanitizer`, plus `node tests/js/dashboard_load.test.js` and `node tests/js/sanitize_chat_html.test.js` (when `node` + `node_modules` are present); run `node scripts/verify_dashboard_ui.js` when a local server is active or visual layout is changed.
   - `.agents/`, `.claude/`, `docs/agents/`, `docs/governance/`, or `scripts/` edits -> `tests.test_agentic_governance`.
   - Compilation (`python -m compileall -q python tests scripts`) and dependency check (`python -m pip check`).

## Step 5: Structured Gate Output
Output a concise **Stage-Gate Report** in chat containing:
1. **Branch & HEAD SHA**
2. **Scope & Protected Asset Check:** `PASS` / `FAIL` (with exact files touched)
3. **Artifact Hygiene & Manifest Parity Check:** `PASS` / `FAIL`
4. **Scoped Tests Executed:** Exact commands, test counts, duration, and exit codes
5. **Argon Acceptance Verdict:** `ACCEPTED`, `ACCEPTED WITH LIMITATIONS`, or `REMEDIATION REQUIRED`
6. **Owner Git Commands:** Exact `git add` and `git commit -m "..."` commands ready for the Human Owner to copy-paste.
