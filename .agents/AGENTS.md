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
│            (.agents/)           │             │      (engine_instructions/)     │
├─────────────────────────────────┤             ├─────────────────────────────────┤
│ • Architecture Agent            │             │ • Terminal Orchestrator         │
│ • Python Engineer               │             │ • Macro Sentinel / Data Analyst │
│ • Security Agent                │             │ • Bullish / Red Team / Neutral  │
│ • Testing Agent                 │             │ • GEX, Structural, Execution    │
│ • Documentation Agent           │             │ • Technical Validator           │
│ • Independent Reviewer          │             │ • Context & Review Engines      │
└────────────────┬────────────────┘             └────────────────┬────────────────┘
                 │                                               │
                 │     STRICT SEPARATION: ZERO LEAKAGE           │
                 └───────────────────X───────────────────────────┘
```

1. **Human Authority:** The human repository owner holds exclusive authority over task/stage scope authorisation, Protected Financial Assets (`Protocol 06`), financial mandates, capital risk parameters, trading execution, git commits, git pushes, and releases.
2. **Instruction Precedence (Fail-Closed):**
   - Security Policies & Financial Boundary Rules (`.agents/protocols/03-security-policy.md`, `.agents/protocols/06-financial-boundaries.md`).
   - `.agents/AGENTS.md` and remaining engineering protocols (`.agents/protocols/01-agent-orchestration.md`, `02`, `04`, `05`).
   - Domain-specific role definitions (`.agents/agents/` for engineering, `engine_instructions/` for trading).
   - [`INSTRUCTIONS.md`](../INSTRUCTIONS.md) (subordinate engineering execution guide; zero trading or financial authority) and approved session task prompts.
3. **Operating Model (Native Orchestrator):**
   - **Gemini 4 Argon (Lead Architect, Planner & Independent Reviewer):** Performs read-only baseline inspection (`git rev-parse HEAD`, `git status --short`, `git diff`), authors compact delta-only stage prompts referencing `@INSTRUCTIONS.md`, independently audits Hydrogen's `git diff` and test results, and issues formal acceptance verdicts under the **Two-Pass Acceptance Cap** (maximum one independent Argon audit and at most one `.1` remediation pass per stage/sub-stage for concrete runtime, security, or data-integrity defects).
   - **Gemini 4 Hydrogen (Bounded Implementation & Testing Worker):** Modifies ONLY explicitly authorised files, executes scope-proportional `unittest` suites (`Protocol 05`), delivers completion reports in chat only (zero report bloat in `docs/engineering/`), never expands scope, never self-approves work, and never executes Git write commands.
   - **Human Owner:** Holds exclusive authority over stage authorisations, Protected Financial Assets (`Protocol 06`), and all Git write operations.

---

## 2. Separation of Concerns: Engineering vs. Investment

A fundamental architectural boundary is enforced between **Engineering Agents** and **Investment Council Agents**:

| Domain | Allowed Agents | Authority Scope | Forbidden Actions |
|---|---|---|---|
| **Engineering & Maintenance** | Architecture Agent, Python Engineer, Security Agent, Testing Agent, Documentation Agent, Independent Reviewer | Code quality, tests, security boundaries, documentation, bug fixes, refactoring | **CANNOT** alter trading rules, portfolio allocations, position sizing, risk thresholds, or SSoT financial records. |
| **Investment Council** | Terminal Orchestrator, Bullish Advocate, Red Team Pessimist, Macro Sentinel, etc. | Market analysis, regime detection, catalyst scrutiny, portfolio rebalancing proposals | **CANNOT** execute host shell commands, alter application code, or bypass runtime security controls. |

---

## 3. Specialist Engineering Agent Directory

All engineering agent definitions are codified under `.agents/agents/`:

1. **Architecture Agent (`architecture-agent.md`):** System boundaries, dependency integrity, architectural assessment, refactoring plans.
2. **Python Engineer (`python-engineer.md`):** Python implementation, code quality, error handling, maintainability, type compliance.
3. **Security Agent (`security-agent.md`):** Runtime permission enforcement, subprocess isolation, input sanitization, financial state protection.
4. **Testing Agent (`testing-agent.md`):** Automated test authoring, test execution, baseline verification, regression prevention.
5. **Documentation Agent (`documentation-agent.md`):** Technical documentation, architectural synchronisation, changelogs, audit reports.
6. **Independent Reviewer (`independent-reviewer.md`):** Independent code review, boundary compliance verification, rejection of unauthorized changes.

---

## 4. Development & Review Lifecycle (Two-Pass Acceptance Cap)

Every code change must traverse the fail-closed lifecycle:

```
[Approved Delta-Only Stage Scope]
          │
          ▼
   [Hydrogen Implementation] ◄─── Restricted to approved scope under @INSTRUCTIONS.md; tests written concurrently
          │
          ▼
 [Scope-Proportional Verification] ◄─── Satisfies Protocol 05 (targeted gates, compileall, pip check; full discover at closeout)
          │
          ▼
 [Argon Independent Review] ◄─── Read-only diff & evidence audit (Protocol 04); max 1 remediation pass (.1) for concrete defects
          │
          ▼
   [Human Sign-Off] ◄─── Reports delivered in chat only; human performs git commit & push and authorises next stage
```

**Fail-Closed Rule:** If required automated tests fail, if unresolved relevant defects remain, if review evidence is incomplete, if unapproved files or Protected Financial Assets (`Protocol 06`) are touched, if permitted blockers or deferrals lack explicit documentation and justification under Protocol 05, if stage reports are written to `docs/engineering/` without explicit authorisation, or if an agent attempts to self-approve or auto-advance stages, the task is marked `REJECTED` and execution terminates immediately.

- **Pre-Verdict Verification Skill (`/stage-gate`):** `.agents/skills/stage-gate/SKILL.md` is the standard pre-verdict verification skill executing read-only scope inspection, Protected Financial Asset invariance checks, artifact hygiene scans, and Protocol 05 scope-proportional test routing.
