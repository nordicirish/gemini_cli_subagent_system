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

1. **Zero Engineering Modification:**
   - Engineering agents (Architecture, Python Engineer, Security, Testing, Documentation, Independent Reviewer) are strictly forbidden from modifying any Protected Financial Asset.
   - Any tool call or script executed by an engineering agent that attempts to write to these files without explicit human authorization must be rejected fail-closed.
2. **Invariance of Investment Algorithms:**
   - No engineering task may alter technical indicator calculations: RSI, MACD, VWAP, Bollinger Bands, Net GEX, ATR, or Fibonacci extensions.
   - Position sizing algorithms, cash friction hurdles, and arbitration weights are immutable during engineering stages.
3. **Runtime Protection on `tools.update_rules()`:**
   - `tools.update_rules()` must verify caller identity and require an explicit human authorization token before writing to `gem_trading_rules/rules.md`.
