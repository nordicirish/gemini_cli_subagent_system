# Protocol 06: Financial State & Investment Boundaries

**Status:** Enforced  
**Standard:** Adapted from `tk-site-new` Protocol 06  

---

## 1. Scope & Purpose

This protocol establishes strict, impenetrable runtime and governance boundaries protecting financial state, portfolio records, risk mandates, and trading algorithms.

## 2. Protected Financial Assets

The following files and directories are designated **PROTECTED FINANCIAL ASSETS**:

- `context/ssot.json` (Single Source of Truth: Portfolio allocations, shares, cash, WAC)
- `context/trade_lessons.json` (Post-trade review ledger and historical risk lessons)
- `context/decision_log.json` (Arbitration audit trail of investment council turns)
- `gem_trading_rules/rules.md` (Canonical investment rules and trading mandates)
- `engine_instructions/*.md` (Investment persona instructions)

## 3. Strict Boundary Rules

1. **Zero Engineering Modification & Unified Mutation Boundary:**
   - Engineering agents (Architecture, Python Engineer, Security, Testing, Documentation, Independent Reviewer) and unauthorized council roles are strictly forbidden from modifying any Protected Financial Asset.
   - Every financial-state mutation function (`tools.update_ssot`, `tools.update_trade_lessons`, `tools.intercept_and_log_decision`, `tools.update_rules`, `fetch_stocks.handle_paste`, and `web_server.py` mutation endpoints) enforces `authorize_financial_mutation()` directly at the function entry point so direct Python calls, subagents, web routes, and tool dispatches cannot bypass authorization.
   - All multi-step read-modify-write mutations use `atomic_update_json()` to hold a reentrant cross-process file lock across the entire read $\rightarrow$ transform $\rightarrow$ validate $\rightarrow$ atomic temp-file replacement sequence.
2. **Invariance of Investment Algorithms:**
   - No engineering task may alter technical indicator calculations: RSI, MACD, VWAP, Bollinger Bands, Net GEX, ATR, or Fibonacci extensions.
   - Position sizing algorithms, cash friction hurdles, and arbitration weights are immutable during engineering stages.
3. **Runtime Protection on `tools.update_rules()`:**
   - `tools.update_rules()` enforces caller role authorization (`Context Engine`, `Rule Enforcer Engine`, or `System`) and requires explicit, one-time local owner confirmation (`GLOBAL_OWNER_APPROVAL`) bound to the operation, target resource (`gem_trading_rules/rules.md`), and SHA-256 payload digest before writing. Hardcoded approval strings in source code or tool schemas are strictly prohibited.
