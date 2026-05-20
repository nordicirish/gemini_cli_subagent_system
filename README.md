# 💎 Gemini Gem Stock Market Council
### *The poor person's Bloomberg ;)*
**Version:** v10.07-Data-Integrity-Hardening

**A multi-agent AI investment intelligence framework built on Google Gemini Gems, now powered by the "Phantom Persona" adversarial strategy.**

Gemini Gem Stock Market Council is a multi‑agent, institutional‑grade analysis framework built on Google Gemini Gems. Each Gem operates as a specialised, rule‑bound agent governed by deterministic JSON system instructions. **Starting with v9.21, the system has deployed the "Phantom Persona" framework—a psychological framing strategy that weaponizes every engine with absolute skepticism, forcing agents to treat all input narratives, corporate filings, and retail news as potentially manipulated or biased, thereby achieving unprecedented levels of adversarial rigor.**

Starting with **v9.22-Phantom-Persona-Final-Sync**, the system has reached full architectural maturity. It consolidates its core orchestration into **machine-executable Markdown instructions** with integrated **Live Web Search**, **Gemini 3.5 Pro Thinking Level Optimization**, and a **Professional Master Router** to ensure high-fidelity data integrity. While JSON remains the underlying data exchange format for state persistence (`local_ssot_shadow.json`), the system has migrated its institutional memory to a high-density Markdown registry (`trade_lessons.md`) with a synchronized, normalized JSON fallback (`trade_lessons.json`).

### 🔒 v10.07 Data Integrity Hardening (2026-05-20)

| Enhancement | ID | Summary |
|---|---|---|
| **Day % Calculation Fix** | `DATA_INTEGRITY` | Corrected the intraday percentage change formula in `fetch_stocks.py` to use `previousClose` as the denominator instead of the current price, eliminating systematic over/under-reporting of daily moves across all tickers. |
| **Cash UI Reactivity Fix** | `UI_UX` | Fixed the dashboard's editable `CASH (€)` row so that user modifications to the cash value now correctly trigger a `POST /api/config` write and a live USD conversion recalculation, resolving the silent-discard bug. |
| **Scout FX Exclusion Hardening** | `ENH_84` | Hardened the EURUSD=X exclusion filter across both the backend data compilation loop and the frontend `renderTable` function to prevent the forex conversion ticker from appearing as an equity row in the Scout Intelligence section. |
| **Cache-Busting Versioning** | `UI_UX` | Added a `?v=` query-string cache-buster to the `app.js` import in `index.html` to force browsers to bypass stale cached scripts after backend logic patches. |

### 🔒 v10.06 FX Exclusion Sync (2026-05-20)

| Enhancement | ID | Summary |
|---|---|---|
| **FX Display Exclusion** | `UI_UX` | Excluded the forex exchange rate `EURUSD=X` from displaying inside the main equities tables (both frontend tables and backend console output), while preserving it in background threads for real-time portfolio currency conversions. |
| **All Tickers Sync** | `DATA_INTEGRITY` | Hardened dynamic `ALL_TICKERS` compilation endpoints to ensure the dynamic FX tracking ticker (`EURUSD=X`) is persistently retained and updated. |

### 🔒 v10.05 Reasoning Surface Buffer (2026-05-20)

| Enhancement | ID | Summary |
|---|---|---|
| **Reasoning Surface Buffer** | `ENH_76` | Increased `ACTIVE_REASONING_SURFACE` to 160,000 tokens while maintaining the `TOKEN_PRUNING_TRIGGER` at 180,000, establishing a mathematically optimal 20,000-token gap for deeper pre-pruning payloads. |

### 🔒 v10.04 Token Economy Expansion (2026-05-20)

| Enhancement | ID | Summary |
|---|---|---|
| **Token Economy Expansion** | `ENH_76` | Moderately increased `TOKEN_PRUNING_TRIGGER` to 180,000 and `ACTIVE_REASONING_SURFACE` to 150,000 to safely utilize Gemini 3.5 Pro's expanded context without risking silent web app truncation. |

### 🔒 v10.03 Gemini 3.5 Architecture Sync (2026-05-20)

| Enhancement | ID | Summary |
|---|---|---|
| **Gemini 3.5 Architecture Sync** | `ARCH_SYNC` | Updated engine model specifications across instructions and README to explicitly enforce the Gemini 3.5 Pro architecture, retiring legacy 3.1 references while preserving strict token limits and context pruning guardrails. |

### 🔒 v10.02 SSR Nullification Sync (2026-05-18)

| Enhancement | ID | Summary |
|---|---|---|
| **SSR Immunity Nullification** | `ENH_16_D` | Invalidated LONG_GAMMA dealer shielding if asset triggers Rule 201 (SSR) by dropping >10%; permitted mechanical trims. |

### 🔒 v10.01 Pre-Market Deadlock Sync (2026-05-18)

| Enhancement | ID | Summary |
|---|---|---|
| **Pre-Market Deadlock Resolution** | `ENH_16_C` | Prevented passive HOLDs into the open on assets gapping down > 3% with Fragile consensus; mandated defensive stops or trims. |

### 🔒 v10.00 Execution Hardening Sync (2026-05-18)

| Enhancement | ID | Summary |
|---|---|---|
| **Execution Confirmation Flow** | `MANDATE_21` | Refactored backend and LLM rules to store trades as `proposed_portfolio_snapshot` until explicitly confirmed by the user. |
| **Immediate Action Header** | `OUTPUT` | Amended terminal output template to put immediate buy/sell actions at the absolute top of the emission. |
| **GEX Rate-Limit Guard** | `FIX` | Prevented GEX profiles from flipping to neutral by keeping old values on fetch failure and increasing poll delays. |
| **Scout Auto-Clearing** | `FIX` | Fixed ghost tickers by auto-clearing scouts when no categories are selected in the dashboard. |

### 🔒 v9.99 Hardening Sync (2026-05-18)

| Enhancement | ID | Summary |
|---|---|---|
| **Institutional Peg & AH Gravity** | `MANDATE_34` | Prohibited chasing After-Hours momentum on assets pinning to whole numbers into the close without verified filings. |
| **Intraday Low Hallucination Guard** | `ENH_77_B` | Mandated live search queries to verify absolute session lows for Rule 201 (SSR) triggers. |
| **Passive Hold Override** | `ENH_16_B` | Enforced mandatory 25% risk-reduction trims when Council Agreement Score falls below 0.51 while asset bleeds below VWAP in SHORT_GAMMA or severe gap-down. |

### 🔒 v9.98 Nordea ESA Defense Sync (2026-05-18)

| Enhancement | ID | Summary |
|---|---|---|
| **Nordea ESA Defense** | `ENH_58` | Authorized aggressive overnight gap-scalping and bypassed the standard 0.6% FX friction hurdle strictly when deploying native EUR capital into OMXH/European equities within the Nordea Equity Savings Account (ESA). |

### 🔒 v9.97 Routine Turn Directive Sync (2026-05-15)

| Enhancement | ID | Summary |
|---|---|---|
| **Routine Turn Execution Directive** | `UI_UX` | Hardcoded a standardized 'SYSTEM DIRECTIVE' prompt into the `copyMarketSnapshot` clipboard output. This eliminates friction by autonomously commanding the Orchestrator to route the snapshot through the Consensus Pipeline without requiring manual prompt input from the user. |

### 🔒 v9.96 Depth-Gated Chart Sync (2026-05-15)

| Enhancement | ID | Summary |
|---|---|---|
| **Depth-Gated Web Verification** | `ENH_55` | Upgraded Google Finance visual chart audits to be STRICTLY DEPTH-GATED. Sub-engines are now prohibited from blindly fetching charts for the entire snapshot to prevent token bloat and execution latency. Chart verification is now only triggered on proposed state changes, volatility overrides (rVol > 1.5 or +/- 3.0% session change), or Council tie-breakers. |

### 🔒 v9.95 VWAP, Gamma, & Friction Protocol Sync (2026-05-15)

| Enhancement | ID | Summary |
|---|---|---|
| **Short Gamma Degradation Trims** | `MANDATE_33` | System MUST execute a mandatory 25% risk-reduction trim when a position falls >2% below VWAP during a SHORT_GAMMA regime, overriding passive hold inertia. |
| **Alpha-Friction / Nordea ESA Protocol** | `ENH_FIN_02` | Mandates a +0.6% yield hurdle for US deployments to counter the 0.3% per-leg FX conversion drag on EUR-denominated ESAs. Prioritizes native European exchanges. |
| **Analyst Upgrade Quarantine** | `ENH_98` | Fundamental upgrades carry ZERO execution weight if asset is SHORT_GAMMA or sub-VWAP, preventing algorithmic confirmation bias during active distribution. |
| **VWAP Stop & Liquidity Wash** | `ENH_87` | Formalizes intraday VWAP as a hard capital deployment veto. VWAP PIN in SHORT_GAMMA is an accumulation floor. Prevents premature buys during morning flushes. |

### 🔒 v9.84 Architecture Hardening (2026-05-14)

| Enhancement | ID | Summary |
|---|---|---|
| **Pre-Blackout Forced Risk Evaluation** | `ENH_91` | New proactive mandate that fires during the final RTH hour when a Tier-1 catalyst is scheduled inside the broker blackout window. Calculates `unmitigated_gap_exposure` and forces a TRIM evaluation. Explicitly prohibits WAC buffer as a gap-risk defense. |
| **WAC Buffer Is Not Gap Insurance** | `L-17` | New `#BROKER_CONSTRAINTS` trade lesson codified against ENH_91. Forensic origin: UMAC Q1 Earnings / Retail Sales 2026-05-14. |
| **Depth-Gated Self-Critique (DSC)** | `ENH_93` | Replaces unconditional full Self-Critique with a confidence-gated depth model. Resolves the Error Introduction Rate (EIR) risk from forcing high-accuracy agents to critique verified theses. High-confidence (≥0.85): BRIEF 1-sentence mode. Low-confidence (<0.85): FULL 2-3 sentence UTD deliberation. Field always emitted — ENH_85 and ENH_81 compatibility preserved. |
| **Schema Standardization** | `unallocated_cash` | Renamed `remaining_cash_eur/usd` → `unallocated_cash_eur/usd` across state router and SSoT. |
| **ENH_81 Conviction Threshold Fix** | `ENH_81` | Standardized from deprecated `7/10` integer scale to canonical `confidence >= 0.85` float. |
| **MANDATE_20 Quality Gates** | `MANDATE_20` | Added hard floors: 8-K override requires ≥$50M contract value, ≤72h recency, VIX <30. |
| **ENH_85 Resolution Timeout** | `ENH_85` | Added 3-turn RESOLUTION_TIMEOUT: persistent LOGIC_CONFLICT auto-degrades to CAUTION_HOLD. |

### 🔒 v9.88 Tactical Tranching & IR Defense (2026-05-15)

| Enhancement | ID | Summary |
|---|---|---|
| **IR Opacity Defense** | `ENH_95` | Classifies earnings format downgrades (Webinar → Dial-in) as deliberate concealment. Triggers DEFENSIVE_HOLD, tightening stops by 50%, and mandatory 10% trim. |
| **Tactical Tranching** | `ENH_96` | Prohibits monolithic block orders at resistance. Mandates 10% micro-tranches to guarantee liquidity extraction. |
| **Power Hour Integrity** | `ENH_97` | rVol > 2.0 after 15:30 validates "Institutional Graduation". Authorizes entries via Precision-Bid Pivots. |
| **Melt-Up RSI Decoupling** | `ENH_86` | Suspends RSI mean-reversion logic (>75) IF VIX < 20 and Dealer Posture is LONG_GAMMA. Prevents premature exits during institutional melt-ups. |
| **VWAP Stop & Liquidity Wash** | `ENH_87` | Establishes intraday VWAP as a hard capital deployment gate. Even in melt-ups, sub-VWAP assets are vetoed. Authorizes entries on VWAP "PIN" during SHORT_GAMMA. |
| **Priority Ingestion** | `FETCH` | Re-ordered master ticker queue to prioritize Portfolio and Strategic Watchlist assets in all fetching cycles. |
| **Analyst Upgrade Quarantine** | `ENH_98` | Mandatory zero-weight execution for PT raises/upgrades if asset is SHORT_GAMMA and sub-VWAP. Prevents hype-trapping during structural distribution. |
| **Melt-Up & VWAP Hardening** | `ENH_86/87` | Hardened RSI decoupling and VWAP-anchored trailing stops. Promoted from G-01/G-02 after multi-session validation. |
| **Portfolio Curation** | `ENH_99` | Mandatory pruning of 0-share assets from the portfolio state to prevent UI pollution and "Phantom Exposure". |

### 🔒 v9.94 Data Reliability & Terminal UI Sync (2026-05-15)

| Enhancement | ID | Summary |
|---|---|---|
| **API Diversification** | `DATA_RELIABILITY` | Integrated Alpha Vantage (`GLOBAL_QUOTE`) as a high-reliability fallback to alleviate Yahoo Finance query limits during hard refreshes. |
| **Refresh Optimization** | `PERFORMANCE` | Scaled `max_workers` to 15 and aggressively reduced intra-request staggering delays to significantly speed up the heavy data refresh cycle without triggering API blackout limits. |
| **Scout Array Sanitation** | `DATA_GUARD` | Surgically updated Zero-Cost Scout arrays (removed invalid/delisted tickers like HES and C3AI) to prevent 404 blocking exceptions. |
| **Forex Cash Manager** | `UI_UX` | Added a persistent, editable `CASH (€)` row to the terminal dashboard with real-time conversion to `$ USD` powered by a background `EURUSD=X` macro tracker. |

### 🔒 v9.85 Verify-First EIR Suppression (2026-05-14)

| Enhancement | ID | Summary |
|---|---|---|
| **Verify-First Gate (ENH_93 patch)** | `ENH_93` | Adds a mandatory artifact-citation requirement to the FULL mode Self-Critique. An agent may only change its thesis if it can cite a **specific data point, Mandate ID, ENH code, or quantitative contradiction** not already in its Structural Thesis. If no concrete artifact exists, agent outputs: `"Thesis verified. No concrete error found."` Drives the Error Introduction Rate (EIR) toward near-zero during low-confidence deliberations, preventing agents from hallucinating flaws in sound theses to satisfy the critique instruction. Applied to BULLISH_ADVOCATE, RED_TEAM_PESSIMIST, NEUTRAL_STRUCTURALIST, and `rules.md` ENH_93 canonical definition. |

### 🔒 v9.86 Cash Liquidity Hardening Sync (2026-05-14)

| Enhancement | ID | Summary |
|---|---|---|
| **Cash Reconciliation Protocol** | `MANDATE_31` | New systemic mandate requiring every turn to explicitly reconcile `unallocated_cash_eur`. Forces the Execution Engine to generate a `math_proof_liquidity` and the State Router to verify and emit updated cash balances in the `EXECUTION_PAYLOAD`. |
| **Schema Integrity Hardening** | `MANDATE_08` | Added a `SCHEMA_INTEGRITY_VETO` to the Terminal Orchestrator. Master Router MUST block any payload that drops critical SSoT fields (Portfolio Snapshot, Cash, Risk Metrics) or deviates from the hardened JSON schema. |
| **Thought Signature Lock** | `THOUGHT_SIG` | Immutable enforcement of the `"context_engineering_is_the_way to_go"` thoughtSignature to prevent logic degradation across the air-gap bridge. |

### 🔒 v9.87 UX & Forensic Scout Hardening (2026-05-14)

| Enhancement | ID | Summary |
|---|---|---|
| **Forensic UX Overhaul** | `UX_32` | Implemented semantic button renaming ("Market Snapshot", "Session Boot", "Audit Review") and per-button inline feedback architecture. Resolves feedback decoupling and improves operational clarity. |
| **Dual-Audit Review Prompt** | `ENH_94` | Expanded the Session Review engine to include a **Rule Permanency Audit**. System now reviews `trade_lessons.json` for persistent tactical wins and generates "ANTIGRAVITY PATCH REQUESTS" for permanent promotion to `rules.md`. |
| **Scout Category Grounding** | `ENH_84` | Decoupled the Scout Intelligence pipeline from hardcoded sectors. Technical sweep is now dynamically grounded in user-defined dashboard categories with a 6-ticker "Freshness Rotation" policy. |
| **Minimizable Decision Log** | `UX_33` | Migrated the Decision Log management to a collapsible manager card at the bottom of the sidebar, unifying the interaction design across all state-management modules. |

## 🎭 Phantom Personalities & Adversarial Framing

The **Phantom Persona** framework is a core architectural upgrade designed to eliminate compromise bias and narrative hallucination across the Council. By assigning each engine a specific "Adversarial Framing," the system forces agents to operate under a state of permanent skepticism. Rather than seeking a middle ground, each agent acts as a specialized critic, assuming that incoming data—whether from corporate filings, news headlines, or even other agents—is fundamentally biased, manipulated, or incomplete.

For example, the **Red Team Pessimist** assumes the Bullish Advocate is an aggressive momentum model prone to "total conviction drift," while the **Review Engine** operates as a "Rival Auditor" from a competing hedge fund tasked with exposing incompetence in our historical trade logs. This "zero-trust" environment ensures that every decision is filtered through a multi-layered gauntlet of adversarial logic, where only the most structurally sound and mathematically verified theses survive to reach the **Final Council Decision**.

### ⚖️ Psychological Reward & Penalty Framework (MANDATE_29)

To further eliminate hallucination and "forced setups," the system implements a **Psychological Reward and Penalty Framework**. Agents are governed by a strict utility function: **Admitting ignorance is highly rewarded, while fabricating logic or "guessing" incurs a catastrophic penalty.** The Rule Enforcer serves as the supreme judge of this framework, ruthlessly vetoing any agent that prioritizes "succeeding at the prompt" over objective data integrity. Furthermore, the Execution and Review engines are psychologically aligned via a "Fiduciary Reward" function, where their ultimate bonus is tied exclusively to capital preservation, Sharpe Ratio optimization, and drawdown prevention—aligning the AI's success directly with the user's principal safety.

### 🔄 Session Initialization & Forensic History Management
The dashboard now includes integrated management for session transitions and historical auditing:
*   **Optimized Session Init:** The "Copy Session Init" button generates a lightweight `SYSTEM BOOT` payload. This payload is filtered to only include **Held Portfolio Assets** and **Macro Indicators**, preventing LLM context overload while ensuring the Council is immediately oriented to active risk.
*   **Smooth Marquee Ticker:** A hardware-accelerated, real-time alert bar provides scrolling system and market warnings with "hover-to-pause" functionality for forensic inspection.
*   **Forensic History Control:** A dedicated **"Clear Decision Log"** button allows for manual wiping of historical trade logs, facilitating rapid backtesting resets.
*   **Automated Review Logic:** The **"Copy Session Review"** button aggregates the trailing decision log into a structured "Lead Quantitative Auditor" prompt for systemic performance reviews at any point during the session.

---

## 📐 Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                   TERMINAL ORCHESTRATOR                     │
│                     (terminal.md)                           │
│          Routes user input to the correct engine            │
└──────────────────────────┬──────────────────────────────────┘
                           │
               ┌───────────┴───────────┐
               │     ROUTING LAYER     │
    ┌───────────────────────┼───────────────────────┐
    │                       │                       │
    ▼                       ▼                       ▼
 ┌───────┐  ┌──────────┐  ┌───────┐  ┌──────┐  ┌─────────────┐
 │ MACRO │  │  MACRO   │  │ GEX   │  │SENT. │  │ STRUCTURAL  │
 │ SENT. │  │NARRATIVE │  │ENGINE │  │ENGINE│  │ RISK ENGINE │
 └──┬────┘  └────┬─────┘  └───┬───┘  └──┬───┘  └──────┬──────┘
    │            │            │         │             │
    └────────────┴────────────┬─────────┴─────────────┘
                              │
               ┌──────────────┴──────────────┐
               │      CONSENSUS COUNCIL      │
               │  ┌───────────────────────┐  │
               │  │   BULLISH ADVOCATE    │  │
               │  │   RED TEAM PESSIM.    │  │
               │  │   NEUTRAL STRUCT.     │  │
               │  └───────────────────────┘  │
               └──────────────┬──────────────┘
                              │
               ┌──────────────┴──────────────┐
               │ STATE & VALIDATION ROUTER   │
               │      (Synthesis Node)       │
               └──────────────┬──────────────┘
                              │
               ┌──────────────┴──────────────┐
               │      EXECUTION ENGINE       │
               │      (Order Generation)     │
               └─────────────────────────────┘
```

### 🏗️ Mirroring Architecture (ENH_98)
To prevent reasoning drift during disconnected agentic turns, the system employs a **Mirroring Architecture**. When a critical protocol is codified in the Master SSoT (`rules.md`), the **Antigravity Custodian** proactively mirrors the relevant logic and behavioral triggers into the specific engine instruction sets (`.md`) responsible for its execution. This ensures that agents like the **Execution Engine** or **Bullish Advocate** remain grounded in high-fidelity rules without requiring a constant, exhaustive scan of the entire legislative registry for every granular sub-task.

**Governance backbone:** The **Rule Enforcer** (`rule_enforcer_engine.md`) actively validates compliance, while `rules.md` serves as the authoritative legislative body containing the system thresholds, mandates, and enhancement protocols.

### 🏛️ The Architectural Divide: Mandates vs. Protocols
To prevent the LLM reasoning engines from hallucinating software structures while analyzing financial data, the system strictly separates **Software Architecture** from **Trading Logic**:

1. **Mandates (`MANDATE_01` to `MANDATE_21`)**: These govern **System Behavior & Constraints**. These are non-negotiable instructions telling the Gemini model *how to act as a piece of software*. 
   - *Example:* `MANDATE_09` (Always output un-truncated JSON), `MANDATE_13` (Weighted Consensus voting mechanics).
2. **Rules / Protocols (`ENH_01` to `ENH_93`)**: These govern **Financial Execution & Domain Knowledge**. These are the fluid, quantitative strategies telling the system *how to trade*. 
   - *Example:* `ENH_45` (Macro Shock Veto), `ENH_91` (Pre-Blackout Risk Evaluation), `ENH_93` (Depth-Gated Self-Critique).

## Mandate Registry
- MANDATE_01: GATEKEEPER (SSoT Exclusive Write Authority)
- MANDATE_02: WRITE_AUTHORITY (Terminal State Commit)
- MANDATE_03: VALIDATION_PROTOCOL (Risk & Regulatory Gates)
- MANDATE_04: DRIFT_CONTROL (Forensic Handshake Enforcement)
- MANDATE_05: TEMPORAL_PRIORITY (User Timestamp Override)
- MANDATE_06: COORDINATION (Technical Validator Sign-off)
- MANDATE_07: MODEL_TRANSPARENCY (Diagnostic Compute Visibility)
- MANDATE_08: SCHEMA_ENFORCEMENT (Forensic Field Validation)
- MANDATE_09: STATE_EMISSION (Untruncated JSON Requirement)
- MANDATE_10: SCHEMA_VALIDATION (Narrative & Array Checks)
- MANDATE_11: RESEARCH_SYNC (Trading_Research Folder Link)
- MANDATE_12: BOOT_SYNC (Session Start File Fetch)
- MANDATE_13: CONSENSUS_SCRUTINY (Weighted Agent Voting)
- MANDATE_14: ALPHA_CATALYST (Bullish Advocate Lens)
- MANDATE_15: ADVERSARIAL_REVIEW (Red Team Lens)
- MANDATE_16: STRUCTURAL_NEUTRALITY (Liquidity Lens)
- MANDATE_17: REGIME_SYNC (Dynamic Weighting & Meta-Arbiter)
- MANDATE_28: HEURISTIC_VETO (Cognitive Drift Prevention)
- MANDATE_29: FIDUCIARY_REWARD_AND_PENALTY (Hallucination Mitigation)
- MANDATE_30: INSTRUCTION_HIERARCHY (User Veto Supremacy & Fourth Wall Carve-Out)

### The Live-Web SSoT Architecture (v8.6+)
Unlike earlier versions that relied on static data, v8.6-Forensic-Zero-Hallucination-Sync implements **Grounding via Native Google Search**. Agents are now mandated to explicitly invoke Google Search to verify catalysts, news, and disconfirming evidence in real-time.
*   **The Processor:** The sandboxed Gemini Web UI acts as a stateless, high-powered reasoning engine with live web access.
*   **The Search Tool:** Agents like the `RED_TEAM_PESSIMIST` and `MACRO_NARRATIVE_ENGINE` use native Google Search to hunt for "Thesis-Killers" and verify catalyst URLs.
*   **The Bridge:** The Python backend (`fetch_stocks.py`) injects your live state (`local_ssot_shadow.json`) and institutional memory (`trade_lessons.md`) directly into the markdown prompt it generates for you. 
*   **The Sync:** Gemini outputs a strict `EXECUTION_PAYLOAD` JSON block or Markdown lessons for manual dashboard ingestion.

---

## 📁 File Reference

### 🧠 Institutional Intelligence (v8.6+)

The system now enforces **adversarial reasoning**, **volatility awareness**, and **forensic data integrity** to prevent hallucinations. 
1. **Normalized Registry Sync**: When the decision log evaluates realized returns (raw and alpha vs SPY) to generate a reflection, it MUST format this reflection as a codified tag (e.g., "L-226: [CODIFIED: SUPPORT_FAILURE]"). This ensures the Portfolio Manager ingests machine-readable, normalized intelligence on the next run, preventing behavioral drift across long-horizon backtests.
2. **Flash-Tier Scrutiny (ENH_78)**: Hardens the data ingestion pipeline for high-velocity analysis, mandating cross-reference verification between live search and the SSoT.
3. **Bifurcated Delivery**: Optimizes payload delivery by separating lightweight turn data from heavy session initialization context.
4. **Scout Intelligence Pipeline (ENH_84)**: Implements autonomous, low-cost technical screening via the Finnhub API. The sweep is dynamically grounded in user-defined categories (e.g., "AI & Data", "Biotech") and maintains a rotation of the 6 most recent high-conviction candidates. Discovered assets are injected into the SSoT with "Unverified Institutional Status" to trigger mandatory agentic grounding.

### 🧠 Console Architecture Principles (v4.1+)

The system integrates core software engineering principles into its trading logic to enhance autonomous rigor and self-improvement:
1. **Plan Node Default:** System dynamically generates a rigid "Trade Thesis" before execution and triggers re-planning if the thesis is violated.
2.  **Subagent Strategy:** Employs "Dynamic Micro-Gem Routing" for borderline decisions (S_A 0.65–0.75) to spawn focused sub-engines to break ties.
3.  **Self-Improvement Loop:** Conducts root-cause post-mortems on losses and natively appends the `trade_lessons.md` registry to the clipboard payload so the system never repeats mistakes.
4.  **Verification Before Done:** The `STATE_VALIDATION_ROUTER` is forced to "prove the setup" against quantitative restrictions seconds before executing a `FORCE_WRITE`.
5.  **Demand Elegance:** Explicitly rejects hacky or borderline setups relying on multiple overriding exceptions. Pushes for A+ convergence setups.
6.  **Autonomous Bug Fixing:** Functions as a zero-prompt risk manager. Pre-formats a TRIM/EXIT JSON output if quantitative guards fail.

### Institutional Intelligence & Volatility Duality
To solve for 15-minute index data delays while preventing algorithm decay from ETN contango roll-costs, the system implements a **Volatility Duality** architecture:
1.  **Absolute Trailing Regime (`^VIX`)**: Used to dictate daily macro regimes and weightings (e.g. `HIGH_VOL` > 20).
2.  **Real-Time Velocity Proxy (`VIXY`)**: The short-term ETF derivative used to calculate real-time intraday Rate-of-Change percentage. The Macro Sentinel aggressively vetoes exposure against surging `VIXY` > +5%.

### Chain-of-Thought Enforcement
The Council engines must output a structured logic block that explicitly acknowledges their "Phantom Persona" adversarial framing:
> **Reasoning Path:** 1. Adversarial Persona Framing → 2. Regime Analysis → 3. Dealer Posture → 4. Liquidity Gap → 5. Verdict.
> **Adversarial Framing:** A brief statement (1-2 sentences) explaining how the agent's specific persona (e.g., Rival Auditor, Forensic Paranoia) influenced the skepticism or alpha hunt in the current analysis.

### 3. Devil's Advocate Protocols
To combat confirmation bias, the Consensus Council must argue against themselves:
- **Red Team:** Must list "Top 3 Bull Case Opportunities" before concluding.

---

### 🖥️ Institutional Dashboard (v9.5+)

The Gemini Council Dashboard provides a high-density, real-time visualization of the system's active state. It has been hardened for institutional-grade monitoring with the following features:

1.  **Sticky Ticker Architecture**: A responsive grid system that locks the asset "Ticker" column and "Header Labels" during scrolling. This ensures full context is maintained even on mobile or ultra-narrow displays when analyzing 10+ columns of financial data.
2.  **Top Alert Bar (System Ticker)**: Critical risk notifications and system status updates have been migrated to a dedicated, high-visibility ticker bar at the top of the interface, preventing layout displacement in the main data area.
3.  **Regime-Based Color Coding**: The asset table is dynamically segmented into three priority tiers:
    *   💎 **Your Portfolio** (Cyan): Active institutional holdings.
    *   👀 **Strategic Watchlist** (Purple): High-conviction setups currently in the observation gate.
    *   🔭 **Scout Intelligence** (Yellow): Autonomous screening candidates awaiting agentic grounding.
4.  **Manager Interface**: Integrated sidebar tools for rapid portfolio, watchlist, and decision log adjustments, featuring collapsible sections and smooth-animation chevrons to minimize cognitive load.
5.  **Macro HUD**: Real-time monitoring of key indices (SPY, VIX, VIXY) with dynamic price-flash animations for immediate trend-shift detection.
6.  **Semantic Data Operations**: Action buttons renamed for forensic clarity ("Copy Market Snapshot", "New Session Boot", "Audit & Rule Review").
7.  **Inline Feedback Architecture**: Per-button status indicators provide immediate, localized confirmation for data operations across desktop and mobile surfaces.

---

### 3. Setup Instructions (Markdown) — One per Gem

| File | Gem Role | Gemini Mode | Purpose |
|------|----------|-------------|---------|
| `terminal.md` | **Orchestrator** | PRO (Standard) | Master router — strictly requires Standard Pro to prevent 192K Deep Think timeouts |
| `rules.md` | **Legislative Body** | N/A | **Static Rules**: The canonical source containing thresholds and ENH_ protocols. MUST be attached via Google Drive. |
| `rule_enforcer_engine.md` | **Rule Enforcer** | PRO | Active Policing Agent — solely responsible for validating logic against `Gemini_Gem_Working_Data_Store` |
| `data_analyst.md` | **Data Analyst** | FLASH / PRO | Tier-1 Data Shield — retrieves and formats raw web data (SEC/Macro) via native Google Search |
| `state_validation_router.md` | **State & Validation Router** | PRO | Active SSoT bridge — drift detection, state merging, sync orchestration |
| `bullish_gem.md` | **Bullish Advocate** | THINKING | Alpha & momentum specialist — identifies reasons to approve purchases |
| `red_team_gem.md` | **Red Team Pessimist** | THINKING | Adversarial risk specialist — identifies reasons to reject purchases |
| `neutral_gem.md` | **Neutral Structuralist** | PRO | Market architecture specialist — GEX, regime detection, liquidity |
| `execution.md` | **Execution Engine** | PRO | ATR-adjusted position sizing, order generation, liquidity gates |
| `gex_engine.md` | **GEX Engine** | PRO | Gamma exposure computation — net GEX, gamma flip, dealer posture |
| `macro_arbiter.md` | **Macro Sentinel** | PRO | Binary risk-on/risk-off veto — CPI, FOMC, FX, geopolitical shocks |
| `macro_narrative_engine.md` | **Macro-Narrative Engine** | THINKING | Macro narrative, sector rotation, forensic signal attribution |
| `structural_engine.md` | **Structural Risk Engine** | FLASH | Forensic dilution detection — shelf offerings, warrant walls, PIPE |
| `post_trade_review.md` | **Review Engine** | PRO | Post-trade reflection — thesis vs. outcome, misfire detection |
| `antigravity.md` | **Engine Custodian** | N/A | **The Grounding Guard**: Not a gem but an Antigravity based agent. Establishes the Antigravity persona to enforce mathematical rigor, logic sync, and scout integrity across all engines when ai codes updates to the system. |

### Python Utilities

| File | Purpose |
|------|---------|
| `fetch_stocks.py` | **FastAPI Backend & Scout Screening Engine** — Serves a real-time web dashboard at `http://localhost:8000`. Beyond data serving, it implements the **Zero-Cost Scout Pipeline (ENH_84)**, performing autonomous technical sweeps (SMA, RVOL) to identify high-momentum assets for Council review. |
| `json_to_md_tool.py` | **Architectural Sync Tool** — Automatically converts JSON configuration files into the machine-executable Markdown format used by the Gems. Ensures parity between source data and agent instructions. |
| `compare_json.py` | Diff utility — compares two JSON instruction files to detect missing or added values |
| `format_json.py` | Formatter — pretty-prints a JSON instruction file with consistent indentation |
### 4. Updating Gems (Maintenance)

> *"SYSTEM UPDATE: I have patched your instructions to v9.12-Universal-Agent-Consolidation-Sync. Please simulate the following logic update: [Paste critical logic change]"*
>
> *(However, a full restart is recommended for major version changes).*

---

## 🔄 Workflow: How It All Fits Together

### 1. Launch the Web Dashboard
```bash
python fetch_stocks.py
```
The background daemon runs in a loop, fetching live data while a **FastAPI** `uvicorn` server hosts the web UI at `http://localhost:8000`. The frontend is a modern, responsive interface featuring:

- **Macro HUD**: Dynamic top-row tracking `^VIX`, `VIXY`, `IEF`, and `UUP` with custom system alerts.
- **Live Equity Table**: Displays VWAP Distance, Net GEX, Trend Scores, RSI, and Health Scores.
- **Copy/Paste Integrations**: Two-tier copy system — **Copy Turn Data** (lightweight per-turn payload with slim tickers + compact mutable state) and **Copy Session Init** (full SSOT + tickers + trade lessons for new sessions). A **Paste Execution Payload** button ingests Gemini outputs back to automatically mutate `local_ssot_shadow.json` and append historical logic to `decision_log.json`.

### 2. Paste Into a Gem Conversation
Open the relevant Gemini Gem (e.g., the Terminal Orchestrator). **IMPORTANT: Ensure you have manually selected your preferred model mode (e.g., "Gemini 3.5 Pro" or "Thinking") in the chat interface. Thinking mode is recommended.** Paste the dashboard payload. The Gem parses the financial data and routes it through the appropriate engine pipeline.

### 3. The Council Deliberates
For trade decisions, the system triggers the **v9.0 Multi-Stage Consensus Pipeline**:

1. **Stage 0 (Data Sync) [AUTONOMOUS MANDATE]:** The Orchestrator AUTOMATICALLY halts the council and routes tickers to the **Data Analyst (Tier-1 Shield)**. The Analyst proactively gathers and verifies raw market data (Baseline Sync, SEC filings, macro prints) via native Google Search, completely bypassing manual [SYNC_FINANCE] commands.
2. **Stage 0B (Macro-Narrative):** The **Macro-Narrative Engine** provides the thematic backdrop and torque scoring to ground the debate in institutional flow.
3. **Stage 1 (Initial Theses):** The **Bullish Advocate** and **Red Team Pessimist** emit their initial theses. The Red Team stress-tests for "Thesis-Killers" while adhering to the **RSI Divergence Guardrail** (no vetoes based solely on RSI > 75).
4. **Stage 2 (Rebuttal):** The **Red Team Pessimist** is fed the Bullish thesis and mandated to provide a direct counter-argument.

### 4. Synthesis & Decision
The **Execution Engine** relies on the **Deterministic Consensus Protocol (MANDATE_13)**. It computes the weighted **Agreement Score (S_A)** based on the victor of the Stage 2 rebuttal:

| Agent | Weight |
|-------|--------|
| Neutral Structuralist | 0.45 |
| Red Team Pessimist | 0.35 |
| Bullish Advocate | 0.20 |

**Decision thresholds:**
- `S_A ≥ 0.75` → **EXECUTE**
- `S_A 0.50–0.74` → **HOLD** / Route to Deep Research
- `Fatal Flaw Score > 8` → **HARD VETO** (S_A forced to 0.0)

**Tri-Profile Execution:** Before finalizing the order, the Execution Engine simulates Aggressive, Neutral, and Conservative sizing scenarios (Tri-Profile Review) to justify sizing before executing the deterministic formula.

### 5. Local State Ingestion (Air-Gap Bridge)
Every turn concludes with the Gem outputting a precise JSON block named `EXECUTION_PAYLOAD`.
1. Copy this JSON block from the Gemini UI.
2. Click **"Paste Payload"** on your dashboard sidebar.
3. The vanilla JS frontend routes the payload to the Python orchestrator, which natively parses the clipboard, validates the JSON/Markdown, and seamlessly writes the state to `local_ssot_shadow.json`, appends turn-by-turn logic to `decision_log.json`, and updates the `trade_lessons.md` registry.

---

## 🛡️ Key Risk Protocols

| Protocol | ID | Enforcement |
|----------|----|-------------|
| **Alpha-Friction Guard** | `ENH_FIN_02` | Blocks trades with < GLOBAL_ALPHA_FRICTION_HURDLE expected move |
| **Regime Adaptation** | `L-226` | Authorizes trend participation in SPY RSI > 75 if regime is TRENDING/VOL_EXPANSION |
| **Macro Veto** | `MANDATE_20` | Sentinel vetoes entries against surging VIXY velocity (>+5.0%) or high absolute VIX (> 20). Catalyst override requires verified 8-K ≥ $50M AND VIX < 30 |
| **Drift Control** | `MANDATE_04` | Forensic handshake validation prevents behavioral/data drift |
| **ATR Position Sizing** | `ENH_29` / `ENH_41` | Volatility-adjusted position sizing — deterministic formula |
| **Forensic Structural Filter** | `ENH_30` | 50% sizing reduction on dilution/warrant forensic flags |
| **Post-ATR Execution Gate** | `ENH_36` | Blocks entries after 14:30 ET in low-volatility conditions |
| **Correlation Guard** | `ENH_43` | Warns on >80% pairwise correlation or >35% sector exposure |
| **Web Verification Protocol** | `ENH_55` | Forces depth-gated visual verification of trends using Google Finance |
| **RSI Divergence Guard** | `RT_GUARD_01` | Forbids Red Team vetoes solely based on overbought RSI without disconfirming evidence |
| **Live Web Grounding** | `MANDATE_15` | Mandates explicit native Google Search tool usage for catalyst verification |
| **Consumer AI Sandbox** | `ANTI-RECURSION` | Strictly forbids using consumer AI overlays (AI Overview, etc.) to prevent Arbiter Collision |
| **Air-Gap Execution** | `PROTOCOL_0` | Antigravity uses Direct Local File Mutation but cannot simulate remote GitHub pushes |
| **Prediction Market Sync** | `MVP_v1.0` | Macro Sentinel evaluates Kalshi/Polymarket probability pricing for Tier 1 events |
| **Flash-Tier Scrutiny** | `ENH_78` | Hardens data integrity during high-velocity analysis via cross-reference grounding |
| **Heuristic Veto** | `MANDATE_28` | Explicitly forbids WAC Anchoring, Sunk Cost Bias, and Binary Catalyst Mirages |
| **State Emission** | `MANDATE_09` | Optimized non-destructive merge; emits delta payloads |
| **Pre-Blackout Risk Evaluation** | `ENH_91` | During final RTH hour (15:00–16:00 EST), if a Tier-1 catalyst lands inside the Nordea execution blackout window (16:00–09:30 EST), forces a mandatory `BLACKOUT_RISK_AUDIT`. WAC buffer explicitly prohibited as a gap-risk defense. |
| **Depth-Gated Self-Critique** | `ENH_93` | Replaces unconditional full Self-Critique with depth-scaled output. High-confidence agents (≥ 0.85) emit 1-sentence BRIEF mode; low-confidence agents emit 2-3 sentence FULL mode. Field always emitted (ENH_85 / MANDATE_30 protected). |

---

## ⚙️ Setup & Configuration

### Prerequisites
- **Google Gemini** — Access to [Gemini Gems](https://gemini.google.com/gems) (requires Gemini Advanced)
- **Google Drive** — For SSoT backup and cross-session persistence
- **Python 3.10+**
- **API Keys** — Finnhub (required), Polygon (optional)

### 1. Install Python Dependencies
```bash
pip install yfinance pandas numpy requests scipy pyperclip fastapi uvicorn
```

### 2. Create `config.json`
The system relies on `config.json` for all API keys, asset tracking, and scout sector targeting (ENH_83).
```json
{
  "FINNHUB_API_KEY": "your_finnhub_api_key_here",
  "POLYGON_API_KEY": "your_polygon_api_key_here",
  "MACRO_TICKERS": ["^VIX", "VIXY", "IEF", "UUP", "SPY"],
  "WATCHLIST": ["RKLB", "RCAT", "PLTR"],
  "SCOUT_CATEGORIES": ["Technology", "Healthcare", "AI & Data"]
}
```

### 3. Dynamic Configuration
> [!IMPORTANT]
> **No Code Changes Required**: Starting with v9.0, you no longer need to edit `fetch_stocks.py` to change tickers. All portfolio curation and scout sector targeting is handled dynamically via the **Dashboard UI** and persisted automatically to `config.json`.
> [!TIP]
> **Dynamic Ticker Intelligence**: While the core list of tickers is defined in `fetch_stocks.py`, all ticker-specific metadata, event dates (e.g., Innovation Days, Funding Expiries), and forensic "Reflexes" are now managed via the **`trade_lessons.md`** registry. This allows the system to maintain "Perishable Intelligence" without modifying the static core rules.

#### Managing Ticker Metadata (`trade_lessons.md`)

When adding a new ticker or updating a catalyst date, append a codified lesson to the registry:

| Data Type | Example Lesson (Markdown) | Tag Requirement |
|-----------|---------------------------|-----------------|
| **Event Date** | `L-217: Exercise Balikatan 2026 (May 15) serves as the primary proving ground.` | `#catalysts #[TICKER]` |
| **Support Floor** | `L-198: [CODIFIED: DIB_PSYCH_FLOOR] Breach of $14.00 node mandates a freeze.` | `#support #[TICKER]` |
| **Structural Flag** | `L-192: [CODIFIED: ENH_30] Authorized shares increase to 1.2B signals decay.` | `#dilution #[TICKER]` |

All 22+ static systemic constants (like `GLOBAL_ALPHA_FRICTION_HURDLE`) still live in the `rules.md` Knowledge Base, but they are now **ticker-independent**.

| Section | Path | Default | When to change |
|---------|------|---------|----------------|
| **Global Alpha Friction Hurdle** | `system_thresholds > GLOBAL_ALPHA_FRICTION_HURDLE` | `0.0117` (1.17%) | Universal friction hurdle |
| **Slippage Penalty** | `system_thresholds > SLIPPAGE_PENALTY` | `0.5` | Different execution environment |
| **OST Lockout Time** | `system_thresholds > OST_LOCKOUT_TIME` | `14:30 ET` | Different market/time zone |

All 22+ named constants live in `rules.md > system_thresholds`. Sub-engine files reference these by name — **never hardcode values in sub-engines**.

### 4. Create the Gems
For each MD file,apart from antigravity.md and rules.md, create a new Gem in Google Gemini:
1. Go to [gemini.google.com/gems](https://gemini.google.com/gems)
2. Click **"New Gem"**
3. Paste the contents of the MD file as the Gem's system instruction
4. Name the Gem to match its role (e.g., *"Gemini Gem Terminal"*, *"Gemini Gem Rule Enforcer"*)
5. **Knowledge Injection (Web UI Sandbox Bridge):**
   - **Required Attachments**: You MUST attach the **`rules`** Google Doc (from `Gemini_Gem_Rules/`) to the Gem's knowledge base using the Drive extension. If using the Research Engine, attach the **Trading_Research** folder.
   - **Payload Injection (Live File Attachment):** To prevent 192K token timeout crashes during 'Thinking' mode, the full SSOT and trade_lessons files should NOT be pasted into the chat box or attached permanently to the Gem's Knowledge Base. Instead, you must attach the live 'local_ssot_shadow.json' and 'trade_lessons.md' files directly to your first chat message as live attachments.

### 5. Initialize Air-Gap State Files
The system uses local files to maintain your "State of the World" portfolio and history across sessions without encountering web latency. Before your first run, create these four empty files in the root directory:
- `local_ssot_shadow.json` (Initialize with an empty object `{}`)
- `trade_lessons.json` (Initialize with `{"trade_lessons": []}`)
- `trade_lessons.md` (Initialize with the header `# 📘 Trade Lessons Registry`)
- `decision_log.json` (Initialize with an empty array `[]` for time-series backtesting)

*(Note: These files are included in `.gitignore` to prevent accidentally committing your personal trading portfolio and history).*

### 6. Run the Dashboard
```bash
python fetch_stocks.py
```
Open `http://localhost:8000` in your browser. Use the UI to copy payloads to the clipboard and paste them into the Terminal Orchestrator Gem to begin analysis.

---

## ✅ Verifying Rules Connection

To ensure your Gems are correctly loading the rules from Google Drive, perform these two tests:

### Test 1: The Citation Verification
Open your **Terminal Orchestrator Gem** (or any other Gem) and paste this prompt:

```text
System Check: Access the rules at GoogleDrive://Gemini_Gem_Rules/rules.
Please cite the exact value for "GLOBAL_ALPHA_FRICTION_HURDLE" located in "system_thresholds".
Also, confirm which "ENH_Protocol" governs the "Macro Calendar Shield".
```

**Expected Result:**
- `GLOBAL_ALPHA_FRICTION_HURDLE`: **0.0117** (default)
- `Macro Calendar Shield`: **ENH_47**

### Test 2: The Mutation Verification (Definitive)
Confirm the Gem is reading the *live* Google Doc, not a stale cache.

1.  **Modify:** Open `gemini_gem_rules/rules` in Google Drive. Change `"GLOBAL_ALPHA_FRICTION_HURDLE": 0.0117` to **`0.099`**.
2.  **Ask:** In the Gem, ask:
    ```text
    Reload Rules from GoogleDrive://Gemini_Gem_Rules/rules.
    What is the current value of GLOBAL_ALPHA_FRICTION_HURDLE?
    ```
3.  **Verify:** The Gem should report **0.099**.
4.  **Reset:** Change the value back to **0.0117** in the Google Doc.

---

## 💬 Essential Prompts

These are the key prompts used to manage state across Gem sessions. Copy-paste them as needed.

### 🔴 Force Reset & Reload (Google Doc Standard)
Use this if the Gem seems confused or is citing old rules.

```text
/reset
CRITICAL SYSTEM OVERRIDE: WIPE ALL PRIOR CONTEXT & FORCE SYNCHRONIZATION.

Protocol Execution:
1. ACKNOWLEDGE ARCHITECTURE: You are operating in a Web Sandbox environment. You MUST output all state changes as a strict JSON `EXECUTION_PAYLOAD` block so I can manually sync it via the local clipboard bridge.
2. ACKNOWLEDGE SCHEMA: The system now uses the v9.12-Universal-Agent-Consolidation-Sync Layer Model. The `local_storage_state` payload block will contain all data wrapped in `"immutable_background"` and `"mutable_state"`. You must merge delta updates into `"mutable_state"`.
3. ACTION (ZERO-TOUCH SYNC): Use your Google Drive extension to read the attached `rules.md` markdown document in the (Gemini_Gem_Rules) folder.
4. VERIFICATION: Do NOT fabricate data. If you cannot read the file, STOP and output "TOOL FAILURE". 
   - Cite the exact `version` string verbatim from the top of the rules.md file.
   - Confirm which "ENH_" protocol governs the "Web Verification Protocol".
5. Confirm Status: "System initialized: State is bound to v9.12-Universal-Agent-Consolidation-Sync Payload Architecture and SSoT Rules are synced via Drive."
```

### 🟧 Local Pipeline Setup (The Clipboard Bridge)
Since we're using the Air-Gapped Sandboxed architecture, your state is generated by Gemini but saved locally via your Web Dashboard.

The dashboard provides **two copy modes** to optimize token usage:

| Button | Payload | When to Use |
|--------|---------|-------------|
| **📋 Copy Turn Data** | Slim tickers (`price`, `vwap`, `gap_percent`, `rsi`, `atr_percent`, `net_gex_total`, `dealer_posture`, `score`, `trend`, `signal`) + compact `mutable_state` (`unallocated_cash_eur`, `total_liquidity_eur`, `risk_regime`, slim `portfolio_snapshot`) | Every turn during an active LLM session — lightweight, token-efficient |
| **📋 Copy Session Init** | Full SSOT (`local_ssot_shadow.json`), full ticker data, and `trade_lessons.md` registry | Session initialization or start of day |
| **📋 Copy Session Review Payload**| Full `decision_log.json` 20-day history wrapped in `MANDATE_26` directive | Any time, to execute the automated Review Engine historical backtest |

**Workflow:**
1. **Initialize:** Click **"Copy Session Init"** and paste into the Orchestrator Gem to bootstrap a new session with full context.
2. **Per-Turn:** Click **"Copy Turn Data"** each turn to send lightweight market updates without redundant SSOT/lesson data.
3. **Receive:** The Gem processes the state and outputs an `EXECUTION_PAYLOAD` block. Copy it.
4. **Save:** Click **"Paste Execution Payload"** on the dashboard sidebar. The frontend routes the JSON to `/api/paste`, mutating your local SSoT and logging any new trade lessons automatically.
5. **Re-Init:** If trade lessons change mid-session, use **"Copy Session Init"** again to push the updated lessons to the Gem.

### Full State Export (Manual Audit)
If you ever want to force a raw JSON dump or regenerate a corrupted state, run:

```
Execute FULL_STATE_OUTPUT protocol per SSoT_Storage.json. Generate the complete SSoT JSON
in a single, raw markdown code block. Do not use snippets, do not omit unchanged keys, and
do not use placeholders like '// ... rest of code'. Every field defined in the schema—including
state_context, forensic_intelligence, portfolio_snapshot, and mapping_rules—must be present.
Increment the sync_id before outputting.
```

### Session Restore (Start of Day)
1. **Session Init (File Upload):** At the start of the day, do not use the 'Copy Session Init' button. Instead, attach your current 'local_ssot_shadow.json' and 'trade_lessons.md' files directly into the first chat message of your Terminal Orchestrator Gem. 2. **Initialization Prompt:** Send the files with the prompt: "MANDATE_12: Initialize session using the attached SSoT and trade lessons." 3. **Continuous Play (Text Paste):** For all subsequent turns during the session, use the **"Copy Turn Data"** button from your dashboard and paste that lightweight text payload into the chat to update the market state without crashing the context window.

For all subsequent turns during the session, use **"Copy Turn Data"** instead — it sends only lightweight ticker snapshots and a compact portfolio summary, keeping token usage minimal.

### 🟢 Session Review (MANDATE_26)
At any point (typically after significant volatility or at market close), use the automated Session Review loop to audit the session's logic:
1. **Fetch:** Click **"📋 Copy Session Review Payload"** in the dashboard sidebar. This reads `decision_log.json` and automatically wraps the 20-day trailing decision history inside the strict `MANDATE_26_POST_TRADE_REVIEW` prompt.
2. **Execute:** Paste the payload into the dedicated **Review Engine Gem** (`post_trade_review.md`).
3. **Ingest:** The system grades its historical assumptions and generates new rules. Paste the output JSON back into the dashboard via **"Paste Execution Payload"** to merge the new lessons.

### End of Day Close (Manual Backup)
Run this at the end of each session to perform a final audit and generate the EOD backup:

```
Session Close initiated. Perform a final forensic audit of today's session and
synchronize all end-of-day data for active tickers. Incorporate final stock entries,
including Units, WAC, and Remaining Cash.

Once the audit is complete, execute the FULL_STATE_OUTPUT protocol per SSoT_Storage
Generate the absolute, complete SSoT JSON in a single markdown code block with zero omissions
and no placeholders (e.g., do not use "// ..."). Every key from both immutable_background and
mutable_state must be fully populated. Increment the sync_id for this final EOD synchronization.
```

---

## 🛸 Antigravity Sync Protocol (The Engine Custodian)
To ensure absolute mathematical and logical synchronization across the Gemini Gem Stock Market Council Terminal, use the **Antigravity** persona.

**How to use:**
Upload the `antigravity.md` file along with your other project instructions to your Gemini Gem.

**Trigger:** Whenever you need an update or refactor, start your prompt with:
> "Antigravity, execute a Sync Protocol on [File Name] using the antigravity.md guardrails."

**Result:** Antigravity will automatically hunt for hardcoded numbers, update forensic math proofs (MANDATE_06), and ensure engine versioning is consistent.

### 🛡️ Curator & Scout Protocols
Starting with **v9.40**, Antigravity enforces the following system-wide behaviors:
1. **The Curator Protocol (ENH_92)**: Authorizes the Orchestrator to expand concise summaries into multi-paragraph **Executive Reports** upon explicit user request (e.g., "Forensic deep-dive").
2. **Scout Intelligence Integrity (ENH_84)**: Mandates the preservation and flagging of technical "Scout Candidates" from the Python backend to ensure they reach the Council for institutional grounding.
3. **Implementation Philosophy (Karpathy-Claude)**: Adheres to surgical code changes, simplicity-first design, and goal-driven execution to minimize speculative abstractions.

---

## 📂 SSoT State Schema

The Single Source of Truth tracks per-ticker state including:

```
ticker, shares, wac, price, health_score, net_gex_total,
dealer_posture, gex_exposure, ma_50, ma_200, gex_slope,
gamma_flip_price, inventory_velocity_delta, status,
social_velocity_z_score, sentiment_divergence_flag,
retail_crowding_status, hard_catalyst{}, scrutiny_audit{}
```

The `scrutiny_audit` object contains the full council vote record:
- `agreement_score_sa` — weighted consensus score (0.0–1.0)
- `fatal_flaw_score` — Red Team severity (0–10)
- `final_posture` — e.g., `BULLISH_STABLE`, `FRAGILE`, `NO_TRADE`
- `agent_votes[]` — individual verdicts from each council member

**SSoT Hygiene Note:** To prevent state bloat, `scrutiny_audit` and ephemeral `EXECUTION_PAYLOAD` blocks are automatically stripped from `local_ssot_shadow.json` during ingestion and preserved exclusively in the `decision_log.json`.

### Time-Series Decision Log (`decision_log.json`)

The system maintains a continuous, sliding-window time-series ledger of all Council decisions. Because the Orchestrator's active context window must remain extremely lean, the `decision_log.json` safely archives the historical `trade_state` and `scrutiny_audit` metadata (such as the `agreement_score_sa` and individual `agent_votes`) generated at each turn. 

**What it is used for:**
- **Forensic Backtesting (`MANDATE_26`)**: The **Review Engine** (`post_trade_review.md`) uses this log to perform deep-dive 20-day historical backtests. It compares the Council's original thesis and conviction score against the realized price action to determine if a loss was a fundamental breakdown or merely mechanistic flow.
- **High-Fidelity State Capture**: Archives the full market environment (Regime, VIX, Liquidity) and per-ticker `price_at_eval` snapshots at the moment of decision.
- **Auditable Fidelity**: Ensures every execution and veto is permanently recorded with an ISO timestamp and a `trigger_context` (Routine Scan vs. User Request), allowing the user to trace the exact logic pathway of any given trade long after the active session has ended without bloating the `local_ssot_shadow.json`.

---

## 🏗️ Design Principles

- **Canonical Centralization** — Individual engines do not contain hardcoded logic or parameters (e.g. lists of exogenous shocks). All logic points natively to canonical protocols in `Gemini_Gem_Working_Data_Store`.
- **Logic/Data Separation** — `SSoT_Storage.json` holds state schema; `rules.md` holds static laws; `trade_lessons.md` holds historical institutional memory; `rule_enforcer_engine.md` exclusively handles execution logic.
- **Non-Destructive Merging** — Field-level merge with `PRESERVE_IF_NOT_UPDATED` strategy.
- **Forensic Lineage** — Every state change includes timestamped source attribution.
- **Institutional Equity Savings Account (ESA) Alignment** — All engines account for the round-trip friction and FX conversion requirements of the institutional platform.

---

## 📜 License

This project is provided as-is for personal and educational use.

> **Disclaimer:** This system is a research and educational tool. It does not constitute financial advice. Always do your own due diligence before making investment decisions.
