# Agent Governance Framework

**Repository:** `nordicirish/gemini_cli_subagent_system`  
**Governance Framework Standard:** Adapted from `tk-site-new` Engineering Governance  
**Authority:** Human Repository Owner. Autonomous commits, pushes, and production deployments are strictly forbidden.

---

## 1. Hierarchy of Authority and Instructions

The governance hierarchy is structured into three strictly isolated tiers:

```
                  ┌─────────────────────────────────────┐
                  │        Human Repository Owner       │
                  │   (Final Architecture, Risk, Git)   │
                  └──────────────────┬──────────────────┘
                                     │
         ┌───────────────────────────┴───────────────────────────┐
         │                                                       │
         ▼                                                       ▼
┌─────────────────────────────────┐             ┌─────────────────────────────────┐
│     Engineering Governance      │             │   Investment Decision Council   │
│   (.agents/ + docs/agents/)     │             │      (engine_instructions/)     │
├─────────────────────────────────┤             ├─────────────────────────────────┤
│ • Architecture Agent            │             │ • Terminal Orchestrator         │
│ • Python & Dashboard Engineer   │             │ • Macro Sentinel / Data Analyst │
│ • Security & Integrity Auditor  │             │ • Bullish / Red Team / Neutral  │
│ • Testing & Verification Eng    │             │ • GEX, Structural, Execution    │
│ • Documentation Parity Agent    │             │ • Technical Validator           │
│ • Independent Reviewer          │             │ • Context & Review Engines      │
│ • Browser Verification Auditor  │             └────────────────┬────────────────┘
│ • Design / UI Proposer (Demand) │                              │
└────────────────┬────────────────┘                              │
                 │     STRICT SEPARATION: ZERO LEAKAGE           │
                 └───────────────────X───────────────────────────┘
```

1. **Human Authority:** The human repository owner holds exclusive authority over task/stage scope authorisation, Protected Financial Assets (`Protocol 06`), financial mandates, capital risk parameters, trading execution, git commits, git pushes, and releases.
2. **Instruction Precedence (Fail-Closed):**
   - Security Policies & Financial Boundary Rules (`.agents/protocols/03-security-policy.md`, `.agents/protocols/06-financial-boundaries.md`).
   - `.agents/AGENTS.md` and remaining engineering protocols (`.agents/protocols/01-agent-orchestration.md`, `02`, `04`, `05`).
   - Canonical role directives (`docs/agents/*.md`, registered in `docs/agents/roles.json`) for engineering, and `engine_instructions/` for trading.
   - [`INSTRUCTIONS.md`](../INSTRUCTIONS.md) (subordinate engineering execution guide; zero trading or financial authority), [`docs/governance/project-state.md`](../docs/governance/project-state.md) (curated Project State Brief), and approved session task prompts.
3. **Operating Model (Native Orchestrator):**
   - **Gemini 4 Argon (Lead Architect, Planner & Independent Reviewer):** Performs read-only baseline inspection (`git rev-parse HEAD`, `git status --short`, `git diff`), authors compact delta-only stage prompts referencing `@INSTRUCTIONS.md`, independently audits Hydrogen's `git diff` and test results, and issues formal acceptance verdicts under the **Two-Pass Acceptance Cap** (maximum one independent Argon audit and at most one `.1` remediation pass per stage/sub-stage for concrete runtime, security, or data-integrity defects).
   - **Gemini 4 Hydrogen (Bounded Implementation & Testing Worker):** Modifies ONLY explicitly authorised files, executes scope-proportional `unittest` suites (`Protocol 05`), delivers completion reports in chat only (zero report bloat in `docs/engineering/`), never expands scope, never self-approves work, and never executes Git write commands.
   - **Human Owner:** Holds exclusive authority over stage authorisations, Protected Financial Assets (`Protocol 06`), and all Git write operations.

---

## 2. Separation of Concerns: Engineering vs. Investment

A fundamental architectural boundary is enforced between **Engineering Agents** and **Investment Council Agents**:

| Domain | Allowed Agents | Authority Scope | Forbidden Actions |
|---|---|---|---|
| **Engineering & Maintenance** | Architecture Agent, Python Engineer, Security Agent, Testing Agent, Documentation Agent, Independent Reviewer, Browser Auditor, Design/UI Proposer | Code quality, tests, security boundaries, live UI verification, benchmarking mockups, documentation, bug fixes | **CANNOT** alter trading rules, portfolio allocations, position sizing, risk thresholds, or SSoT financial records. |
| **Investment Council** | Terminal Orchestrator, Bullish Advocate, Red Team Pessimist, Macro Sentinel, etc. | Market analysis, regime detection, catalyst scrutiny, portfolio rebalancing proposals | **CANNOT** execute host shell commands, alter application code, or bypass runtime security controls. |

---

## 3. Specialist Engineering Agent Directory & Manifest Parity

Canonical role rules live in `docs/agents/*.md`, and runtime metadata lives in [`docs/agents/roles.json`](../docs/agents/roles.json). Run `.\.venv\Scripts\python.exe scripts/sync_agent_manifests.py` to generate `.agents/agents/<role>/agent.md` and `.claude/agents/<role>.md` — **never hand-edit generated manifests**.

1. **Architecture Agent (`docs/agents/architecture-agent.md`):** Read-only system boundaries, dependency integrity, architectural assessment, delta-only stage planning.
2. **Python & Dashboard Engineer (`docs/agents/python-engineer.md`):** Python and vanilla JS/CSS implementation, `Decimal` precision, atomic persistence, UI alignment.
3. **Security Agent (`docs/agents/security-agent.md`):** Read-only capability/sandbox/CORS/secret auditor running `scripts/audit_security.py`.
4. **Testing Agent (`docs/agents/testing-agent.md`):** `unittest` + `tests/js/` authoring and `Protocol 05` execution via `scripts/stage_gate.py`.
5. **Documentation Agent (`docs/agents/documentation-agent.md`):** Documentation parity, `project-state.md` hygiene, directive size ceilings, chat-only handover formatting.
6. **Independent Reviewer (`docs/agents/independent-reviewer.md`):** Read-only diff audit, `Protocol 01-06` compliance, Two-Pass Acceptance Cap verdicts.
7. **Browser & Visual Verification Auditor (`docs/agents/browser-agent.md`):** Read-only live/headless verification of `http://127.0.0.1:8000` (`Live Browser Mode`, `Screenshot Mode` via `scripts/verify_dashboard_ui.js`, or `Static Fallback Mode`).
8. **Design / Trading UI Researcher (`docs/agents/design-agent.md`, ON-DEMAND ONLY):** Researches TradingView, Yahoo Finance, Finviz, Koyfin, and Linear UX patterns, producing sanitized vanilla HTML/CSS mockups for mandatory Human Owner approval.

---

## 4. Development & Review Lifecycle & Automated Gates

Every code change must traverse the fail-closed lifecycle:

```
[Approved Delta-Only Stage Scope]
          │
          ▼
   [Hydrogen Implementation] ◄─── Restricted to approved scope under @INSTRUCTIONS.md; tests written concurrently
          │
          ▼
  [scripts/stage_gate.py]    ◄─── Enforces Protocol 06 assets, hygiene, manifest parity, Protocol 05 tests & Stop hook
          │
          ▼
 [Argon Independent Review] ◄─── Read-only diff & evidence audit (Protocol 04); max 1 remediation pass (.1) for concrete defects
          │
          ▼
   [Human Sign-Off] ◄─── Reports delivered in chat only; human performs git commit & push and authorises next stage
```

**Fail-Closed Rule:** If required automated tests fail, if unresolved relevant defects remain, if review evidence is incomplete, if unapproved files or Protected Financial Assets (`Protocol 06`) are touched, if permitted blockers or deferrals lack explicit documentation and justification under Protocol 05, if stage reports are written to `docs/engineering/` without explicit authorisation, or if an agent attempts to self-approve or auto-advance stages, the task is marked `REJECTED` and execution terminates immediately.

- **Executable Stage-Gate & Stop Hook:** `scripts/stage_gate.py` enforces Protected Financial Asset invariance, artifact hygiene, manifest parity, directive size ceilings, and `Protocol 05` test routing (including `tests/js/*.test.js`). `.agents/hooks.json` and `.claude/settings.json` invoke `scripts/hook_stop_verify.py` on turn end when workspace files are modified. `.agents/skills/stage-gate/SKILL.md` wraps the full pre-verdict check.

---

## 5. Known Pitfalls (Living Lessons)

Non-obvious traps verified in this repository (keep concise; remove items that stop being true):
- **Windows PowerShell 5.1 has no `&&`:** Chain shell commands with `; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE };`.
- **Script-mode entrypoints need `_REPO_ROOT`:** `python/web_server.py`, `python/main.py`, and `python/fetch_stocks.py` must insert `_REPO_ROOT` at `sys.path.insert(1, _REPO_ROOT)` before importing `persistence` -> `python.database.*` (`tests/test_entrypoints.py`).
- **UI cache-busting & table colspan:** Any edit to `static/app.js`, `static/modern_ui.js`, `static/portfolio.js`, `static/styles.css`, or `static/modern_ui.css` must bump its `?v=` query parameter in `static/index.html`; `#data-table` column edits must update `<thead>`, `renderRow()`, fallback placeholders, and all `colspan` values together.
- **Zero-volume `rvol` placeholders:** `calculate_rvol()` emits `1.0` for NO DATA placeholder rows (`volume == 0`); gate `rVol` display on `volume > 0`.
- **Portfolio `wac == 0` validation:** `shares > 0, wac == 0` raises `PersistenceValidationError` -> HTTP 422 (not 503); client draft rows (`data-draft="true"`) must be protected from `/api/basket` background polling overwrites.
- **Bounded `agy.exe` CLI stdin:** Keep `MAX_AGY_STDIN_BYTES = 90_000` compaction in `python/agent_framework.py`; halted payloads (`CRITICAL_SYNC_ALERT` / `ENH_12_STATE_ABSENCE_VETO`) must never mutate SSoT.
- **CDN pinning parity:** `marked` and `dompurify` CDN versions in `static/index.html` must match `package.json` `devDependencies` (`tests/test_chat_sanitizer.py`), with `purify.min.js` loaded before `modern_ui.js`.
- **Extended & intermittent tests:** Run `test_database_backup_concurrency`, `test_database_migration`, and `test_database_projections` only under `RUN_EXTENDED_TESTS=1`; preserve `test_run_prompt_timeout_triggers_cleanup_and_preserves_exception` as *Intermittent Contention / Timing*.
- **No blind HTTP User-Agent false positives in security scans:** `python/fetch_stocks.py` contains `Chrome/120.0.0.0` in HTTP headers; bind checks look specifically for socket/uvicorn `0.0.0.0` bindings.

---

## 6. Strict Directive Size Ceiling

Every governance and directive file (`.agents/AGENTS.md`, `INSTRUCTIONS.md`, `docs/governance/AI_DEVELOPMENT_MANAGEMENT.md`, `docs/governance/project-state.md`, `.agents/skills/stage-gate/SKILL.md`, `docs/agents/*.md`, `.agents/agents/*/agent.md`, `.claude/agents/*.md`) must stay strictly below **23,500 bytes** (< 24 KB) to prevent prompt truncation; `docs/governance/project-state.md` must stay strictly below **150 lines** (enforced by `tests/test_agentic_governance.py`).
