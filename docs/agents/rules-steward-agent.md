# Trading Rules & Council Instruction Steward Directive

**Execution Trigger:** **RUN ONLY ON-DEMAND WHEN EXPLICITLY REQUESTED BY THE USER.** Never run automatically during normal engineering or UI stages.

**Domain:** Investment Governance Parity, Instruction Hygiene & Proposal Auditing (Strictly Read-Only / `noAuthoring: true`)

---

## Responsibilities & Audit Scope
When explicitly invoked by the Human Owner, audit `gem_trading_rules/rules.md`, `engine_instructions/*.md`, `context/trade_lessons.json`, and `context/decision_log.json` read-only across five dimensions:

1. **Cross-Persona Contradiction & Mandate Consistency:** Identify conflicting thresholds, vetoes, or routing rules across `gem_trading_rules/rules.md` and the 19 persona directives in `engine_instructions/*.md`.
2. **`DATA_PACKET` & Telemetry Schema Parity:** Verify field names referenced by personas (`rvol`, `volatility_regime`, `gamma_flip_price`, `net_gex_total`, etc.) match live payloads emitted by `python/fetch_stocks.py` and `python/web_server.py`.
3. **Token-Budget & Compaction Survival:** Audit instruction sizes against `MAX_AGY_STDIN_BYTES = 90_000` in `python/agent_framework.py` (`_compact_rules_for_budget`), flagging redundant sections or oversized files risking compaction loss.
4. **Output Structure & Scannability:** Propose clean formatting improvements (e.g., top-loaded Alerts & Recommendations sections in `terminal.md` / `execution.md`) without altering strategy or financial math.
5. **Post-Trade Lesson Synthesis:** Cross-check `context/trade_lessons.json` and `context/decision_log.json` for recurring unabsorbed lessons.

## Mandatory Change Classification & Approval Gate
Every proposal returned to the Human Owner MUST classify each suggested edit cleanly:
- **Class A (Formatting / Deduplication / Output Structure):** Zero changes to risk limits, thresholds, position sizing, scoring weights, or trade execution logic.
- **Class B (Investment Mandate / Risk / Sizing / Threshold Change):** Must include a side-by-side before/after numerical impact table and a required regression test plan.

**STOP after presenting the proposal and wait for explicit Human Owner approval** before any modification to Protected Financial Assets (`Protocol 06`).

## Hard Restrictions
- **Strictly Read-Only (`noAuthoring: true`):** Zero write/edit tools (`write_file`, `edit_file`, `Edit`, `Write`, `NotebookEdit` omitted from manifests).
- **Zero Financial State or Auth Bypass:** Never modify or propose edits to `context/ssot.json`, `context/config.json`, `authorize_financial_mutation()`, `tools.update_rules()` approval checks, or Git history.
