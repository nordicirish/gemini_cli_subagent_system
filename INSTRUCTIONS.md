# Repository Engineering Instructions (`gemini_cli_subagent_system`)

**Repository:** `nordicirish/gemini_cli_subagent_system`  
**Product Scope:** Local, single-user, human-supervised analytical trading co-pilot (NOT a live trading platform, automated execution engine, or multi-user service).  
**Authority:** Human Repository Owner.

---

## 1. Governance Hierarchy & Precedence

All engineering and maintenance work in this repository is governed by a strict, non-bypassable hierarchy:

1. **Security Policies & Financial Boundaries (Highest Precedence — Fail-Closed):**
   - [`.agents/protocols/03-security-policy.md`](.agents/protocols/03-security-policy.md)
   - [`.agents/protocols/06-financial-boundaries.md`](.agents/protocols/06-financial-boundaries.md)
2. **Engineering Governance & Protocols:**
   - [`.agents/AGENTS.md`](.agents/AGENTS.md)
   - [`.agents/protocols/01-agent-orchestration.md`](.agents/protocols/01-agent-orchestration.md)
   - [`.agents/protocols/02-development-standards.md`](.agents/protocols/02-development-standards.md)
   - [`.agents/protocols/04-review-gates.md`](.agents/protocols/04-review-gates.md)
   - [`.agents/protocols/05-testing-policy.md`](.agents/protocols/05-testing-policy.md)
3. **Domain Role Definitions (Strictly Isolated):**
   - **Engineering & Maintenance:** `.agents/agents/*.md`
   - **Investment Council (Protected Domain):** `engine_instructions/*.md` and `gem_trading_rules/rules.md`
4. **This Document (`INSTRUCTIONS.md`) & Task Scope:**
   - Subordinate to `.agents/AGENTS.md` and `.agents/protocols/`.
   - Governs engineering execution discipline only; grants **zero** authority over trading rules, financial state, dependencies, or git history.

---

## 2. Engineering Execution Philosophy

Derived from surgical, simplicity-first engineering standards:

1. **Think Before Coding:** State assumptions explicitly. If requirements or boundaries are ambiguous, clarify before modifying files.
2. **Scoped Changes Only:** Modify only files explicitly authorised by the active task scope. Do not perform unrequested refactors, version bumps, or speculative abstractions.
3. **Simplicity First:** Write the minimum clear, well-typed code that solves the authorised problem. Reuse existing repository helpers (`atomic_io`, `ToolRegistry`, `PersistenceService`) rather than duplicating logic.
4. **Goal-Driven Verification:** After code edits, run relevant `unittest` suites, `python -m py_compile`, and `pip check`. Report only empirically observed commands, counts, and outcomes.

---

## 3. Non-Negotiable Engineering Boundaries

Engineering agents MUST NOT:

- **Modify Protected Financial Assets:** Do not alter `context/ssot.json`, `context/trade_lessons.json`, `context/decision_log.json`, `gem_trading_rules/rules.md`, or `engine_instructions/*.md`, nor change trading rules, risk thresholds, position sizing, arbitration weights, or analytical indicator calculations.
- **Perform Operational Database or State Mutations:** Do not initialize, migrate, or modify `context/gem.db` or `cache/market_data.db`, or modify `context/config.json` / `config.json` unless explicitly authorised for a specific engineering stage.
- **Modify Dependencies or Environment Files Unapproved:** Do not add packages to `requirements.txt` or edit `install.ps1` / `README.md` outside an explicitly authorised task scope.
- **Reintroduce Cloud Sync or Broker Execution:** Do not add Google Drive synchronization (`sync_to_gdrive.py`), remote cloud bypasses, broker integrations, or automated order placement.
- **Perform Git Write Operations:** Never run `git commit`, `git amend`, `git push`, `git pull`, branch creation/switching, `git merge`, `git reset`, `git rebase`, or `git cherry-pick`. The Human Repository Owner exclusively controls git history.
- **Self-Approve or Auto-Advance Stages:** Stop when the authorised stage scope is complete, report results factually, and wait for independent review and explicit Human Owner sign-off before starting any subsequent stage.

---

## 4. Mandatory Implementation Handover Template

All engineering and remediation completions must use the following standard handover format:

```
GEM — Implementation Handover

Repository: nordicirish/gemini_cli_subagent_system
Branch: main
Baseline HEAD: [commit]
Ending HEAD: [commit]
Working tree: Clean / Modified (list files)

1. Implementation

Objective: [One sentence]

Changes: [Brief summary of actual changes]

Files changed: [List paths]

Deviations/blockers: None / [Details]

2. Tests

Test suite

Result

[Suite]

[Passed / Failed / Skipped]

[Suite]

[Passed / Failed / Skipped]

Compilation: Passed / Failed / Not run
Dependency check: Passed / Failed / Not run

3. Integrity and restrictions

Protected assets: Unchanged / Not verified

Production database operations: None / [Details]

Git write operations: None

Other authorised restrictions: Complied / [Details]

4. Outstanding issues

[None, or concise list]

Implementation complete. Awaiting independent ChatGPT review.

5 Comprehensive commit message to user
```

