# 💎 GEM Trading Agent System

**A multi-agent AI trading intelligence framework built on Google Gemini Gems.**

Starting with v8.9-Forensic-WebUI-Stability-Sync, the system has fully matured its core orchestration into **machine-executable Markdown instructions** with integrated **Live Web Search** and **Dynamic Model Routing**. While JSON remains the underlying data exchange format for state persistence (`ssot.json`), the system has migrated its institutional memory to a high-density Markdown registry (`trade_lessons.md`) with a synchronized, normalized JSON fallback (`trade_lessons.json`). This transition enhances agent readability, improves complex instruction following, and enables real-time forensic auditing of market narratives.

---

## 📐 Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                   TERMINAL ORCHESTRATOR                     │
│                     (terminal.md)                           │
│          Routes user input to the correct engine            │
└────────────┬────────────────────────────────────────────────┘
             │
     ┌───────┴────────┐
     │  ROUTING LAYER  │
     └───────┬────────┘
             │
     ┌───────┴────────┐
     │  DATA ANALYST   │ (Stage 0: Data Sync)
     │ (data_analyst.md)│ ◄── Baseline Prices, Macro Events
     └───────┬────────┘
             │
  ┌──────────┼──────────────────────────────────┐
  │          │                                  │
  ▼          ▼                                  ▼
┌─────┐  ┌──────────┐  ┌───────┐  ┌──────┐  ┌─────────────┐
│MACRO│  │RESEARCH  │  │ GEX   │  │SENT. │  │ STRUCTURAL  │
│SENT.│  │ENGINE    │  │ENGINE │  │ENGINE│  │ RISK ENGINE │
└──┬──┘  └────┬─────┘  └───┬───┘  └──┬───┘  └──────┬──────┘
   │          │             │         │              │
   └──────────┴──────┬──────┴─────────┴──────────────┘
                     │
          ┌──────────┴──────────┐
          │   CONSENSUS COUNCIL  │
          │  ┌────────────────┐  │
          │  │BULLISH ADVOCATE│  │
          │  │RED TEAM PESSIM.│  │
          │  │NEUTRAL STRUCT. │  │
          │  └────────────────┘  │
          └──────────┬──────────┘
                     │
          ┌──────────┴──────────┐
          │ TECHNICAL VALIDATOR  │
          │    (Synthesis Node)  │
          └──────────┬──────────┘
                     │
          ┌──────────┴──────────┐
          │   CONTEXT ENGINE     │──── SSoT STORAGE
          │   (State Bridge)     │     (Schema)
          └──────────┴──────────┘
                     │
          ┌──────────┴──────────┐
          │  EXECUTION ENGINE    │
          │  (Order Generation)  │
          └─────────────────────┘
```

**Governance backbone:** The **Rule Enforcer** (`rule_enforcer_engine.md`) actively validates compliance, while `rules.md` serves as the authoritative legislative body containing the system thresholds, mandates, and enhancement protocols.

### 🏛️ The Architectural Divide: Mandates vs. Protocols
To prevent the LLM reasoning engines from hallucinating software structures while analyzing financial data, the system strictly separates **Software Architecture** from **Trading Logic**:

1. **Mandates (`MANDATE_01` to `MANDATE_21`)**: These govern **System Behavior & Constraints**. These are non-negotiable instructions telling the Gemini model *how to act as a piece of software*. 
   - *Example:* `MANDATE_09` (Always output un-truncated JSON), `MANDATE_13` (Weighted Consensus voting mechanics).
2. **Rules / Protocols (`ENH_01` to `ENH_52`)**: These govern **Financial Execution & Domain Knowledge**. These are the fluid, quantitative strategies telling the system *how to trade*. 
   - *Example:* `ENH_45` (Macro Shock Veto), `ENH_50` (Pre-Trade Formulation), `ENH_36` (Post-14:30 Liquidity Gates).

### The Live-Web SSoT Architecture (v8.6+)
Unlike earlier versions that relied on static data, v8.6-Forensic-Zero-Hallucination-Sync implements **Grounding via Native Google Search**. Agents are now mandated to explicitly invoke Google Search to verify catalysts, news, and disconfirming evidence in real-time.
*   **The Processor:** The sandboxed Gemini Web UI acts as a stateless, high-powered reasoning engine with live web access.
*   **The Search Tool:** Agents like the `RED_TEAM_PESSIMIST` and `RESEARCH_ENGINE` use native Google Search to hunt for "Thesis-Killers" and verify catalyst URLs.
*   **The Bridge:** The Python backend (`fetch_stocks.py`) injects your live state (`local_ssot_shadow.json`) and institutional memory (`trade_lessons.md`) directly into the markdown prompt it generates for you. 
*   **The Sync:** Gemini outputs a strict `EXECUTION_PAYLOAD` JSON block or Markdown lessons for manual dashboard ingestion.

---

## 📁 File Reference

### 🧠 Institutional Intelligence (v8.6+)

The system now enforces **adversarial reasoning**, **volatility awareness**, and **forensic data integrity** to prevent hallucinations. 
1. **Normalized Registry Sync**: Ensures zero-drift between the high-density `trade_lessons.md` (institutional memory) and the machine-executable `trade_lessons.json` fallback.
2. **Flash-Tier Scrutiny (ENH_78)**: Hardens the data ingestion pipeline for high-velocity analysis, mandating cross-reference verification between live search and the SSoT.
3. **Bifurcated Delivery**: Optimizes payload delivery by separating lightweight turn data from heavy session initialization context.

### 🧠 Console Architecture Principles (v4.1+)

The system integrates core software engineering principles into its trading logic to enhance autonomous rigor and self-improvement:
1. **Plan Node Default:** System dynamically generates a rigid "Trade Thesis" before execution and triggers re-planning if the thesis is violated.
2.  **Subagent Strategy:** Employs "Dynamic Micro-Gem Routing" for borderline decisions (S_A 0.65–0.75) to spawn focused sub-engines to break ties.
3.  **Self-Improvement Loop:** Conducts root-cause post-mortems on losses and natively appends the `trade_lessons.md` registry to the clipboard payload so the system never repeats mistakes.
4.  **Verification Before Done:** The `TECHNICAL_VALIDATOR` is forced to "prove the setup" against quantitative restrictions seconds before executing a `FORCE_WRITE`.
5.  **Demand Elegance:** Explicitly rejects hacky or borderline setups relying on multiple overriding exceptions. Pushes for A+ convergence setups.
6.  **Autonomous Bug Fixing:** Functions as a zero-prompt risk manager. Pre-formats a TRIM/EXIT JSON output if quantitative guards fail.

### Institutional Intelligence & Volatility Duality
To solve for 15-minute index data delays while preventing algorithm decay from ETN contango roll-costs, the system implements a **Volatility Duality** architecture:
1.  **Absolute Trailing Regime (`^VIX`)**: Used to dictate daily macro regimes and weightings (e.g. `HIGH_VOL` > 20).
2.  **Real-Time Velocity Proxy (`VIXY`)**: The short-term ETF derivative used to calculate real-time intraday Rate-of-Change percentage. The Macro Sentinel aggressively vetoes exposure against surging `VIXY` > +5%.

### Chain-of-Thought Enforcement
The **Neutral Gem** and **Validator** must output a structured logic block:
> **Reasoning Path:** 1. Regime Analysis → 2. Dealer Posture → 3. Liquidity Gap → 4. Verdict.

### 3. Devil's Advocate Protocols
To combat confirmation bias, the Consensus Council must argue against themselves:
- **Red Team:** Must list "Top 3 Bull Case Opportunities" before concluding.

---

## 🛠️ Setup Instructions (Markdown) — One per Gem

> **Note on Mode Selection:** You must manually select the Gemini Mode in the chat interface. **Standard Gemini 3.1 Pro (without Thinking mode enabled)** is explicitly required for the Orchestrator. This provides the highest routing fidelity while maintaining the 1-million-token context window necessary to bypass the 192K timeout crashes associated with Deep Think modes.

| File | Gem Role | Gemini Mode | Purpose |
|------|----------|-------------|---------|
| `terminal.md` | **Orchestrator** | PRO (Standard) | Master router — strictly requires Standard Pro to prevent 192K Deep Think timeouts |
| `rules.md` | **Legislative Body** | N/A | **Static Rules**: The canonical source containing thresholds and ENH_ protocols. MUST be attached via Google Drive. |
| `rule_enforcer_engine.md` | **Rule Enforcer** | PRO | Active Policing Agent — solely responsible for validating logic against `GEM_Rules_Data` |
| `data_analyst.md` | **Data Analyst** | PRO | Tier-1 Data Shield — retrieves and formats raw web data (SEC/Macro) via native Google Search to save reasoning tokens |
| `context_engine.md` | **Context Engine** | PRO | Active SSoT bridge — drift detection, state merging, sync orchestration |
| `bullish_gem.md` | **Bullish Advocate** | THINKING | Alpha & momentum specialist — identifies reasons to approve trades |
| `red_team_gem.md` | **Red Team Pessimist** | THINKING | Adversarial risk specialist — identifies reasons to reject trades |
| `neutral_gem.md` | **Neutral Structuralist** | PRO | Market architecture specialist — GEX, regime detection, liquidity |
| `execution.md` | **Execution Engine** | PRO | ATR-adjusted position sizing, order generation, liquidity gates |
| `gex_engine.md` | **GEX Engine** | PRO | Gamma exposure computation — net GEX, gamma flip, dealer posture |

| `macro_arbiter.md` | **Macro Sentinel** | PRO | Binary risk-on/risk-off veto — CPI, FOMC, FX, geopolitical shocks |
| `sentiment_engine.md` | **Sentiment Engine** | PRO | Sentiment & catalyst extraction — news, social velocity, regulatory |
| `structural_engine.md` | **Structural Risk Engine** | FAST | Forensic dilution detection — shelf offerings, warrant walls, PIPE |
| `technical_validator.md` | **Technical Validator** | PRO | Data integrity, consensus synthesis, health score calculation |
| `research.md` | **Research Engine** | THINKING | Macro narrative, sector rotation, forensic signal attribution |
| `post_trade_review.md` | **Review Engine** | PRO | Post-trade reflection — thesis vs. outcome, misfire detection |

### Python Utilities

| File | Purpose |
|------|---------|
| `fetch_stocks.py` | **FastAPI Backend & Web Server** — Serves a real-time web dashboard at `http://localhost:8000`. Generates JSON prompt payloads and exposes an `/api/paste` route to ingest Gem execution payloads into the local SSoT. |
| `json_to_md_tool.py` | **Architectural Sync Tool** — Automatically converts JSON configuration files into the machine-executable Markdown format used by the Gems. Ensures parity between source data and agent instructions. |
| `compare_json.py` | Diff utility — compares two JSON instruction files to detect missing or added values |
| `format_json.py` | Formatter — pretty-prints a JSON instruction file with consistent indentation |
### 4. Updating Gems (Maintenance)

> *"SYSTEM UPDATE: I have patched your instructions to v8.9-Forensic-WebUI-Stability-Sync. Please simulate the following logic update: [Paste critical logic change]"*
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
- **Copy/Paste Integrations**: Two-tier copy system — **Copy Turn Data** (lightweight per-turn payload with slim tickers + compact mutable state) and **Copy Session Init** (full SSOT + tickers + trade lessons for new sessions). A **Paste Execution Payload** button ingests Gemini outputs back to automatically mutate `local_ssot_shadow.json`.

### 2. Paste Into a Gem Conversation
Open the relevant Gemini Gem (e.g., the Terminal Orchestrator). **IMPORTANT: Ensure you have manually selected your preferred model mode (e.g., "Gemini 3.1 Pro" or "Thinking") in the chat interface. Thinking mode is recommended.** Paste the dashboard payload. The Gem parses the financial data and routes it through the appropriate engine pipeline.

### 3. The Council Deliberates
For trade decisions, the system triggers the **Two-Stage Consensus Pipeline**:

1. **Data Analyst (Tier-1 Shield)** — Proactively gathers, verifies, and formats raw market data (Baseline Sync, SEC filings, macro prints) via native Google Search. This engine shields the "Thinking" models from wasting tokens on basic retrieval tasks (ENH_31 / ENH_77).
2. **Macro Sentinel** — checks for exogenous shocks (CPI, FOMC, geopolitical) using live Google Search.
3. **Neutral Structuralist** — evaluates GEX posture, market regime, and structural capacity to establish the baseline.
4. **Stage 1 (Initial Theses):** **Bullish Advocate** identifies alpha catalysts, momentum signals, and entry thesis. **Red Team Pessimist** stress-tests for dilution, liquidity voids, and "Thesis-Killers" via live forensic search.
5. **Stage 2 (Rebuttal):** The **Red Team Pessimist** is fed the Bullish thesis and mandated to provide a direct counter-argument.

### 4. Synthesis & Decision
The **Technical Validator** computes the weighted **Agreement Score (S_A)** based on the victor of the Stage 2 rebuttal:

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
3. The React/JS frontend routes the payload to the Python orchestrator, which natively parses the clipboard, validates the JSON/Markdown, and seamlessly writes the state to `local_ssot_shadow.json` and the `trade_lessons.md` registry.

---

## 🛡️ Key Risk Protocols

| Protocol | ID | Enforcement |
|----------|----|-------------|
| **Alpha-Friction Guard** | `ENH_FIN_02` | Blocks trades with < GLOBAL_ALPHA_FRICTION_HURDLE expected move |
| **Macro Veto** | `MANDATE_20` | Sentinel vetoes entries against surging VIXY velocity (>+5.0%) or high absolute VIX (> 20) |
| **Drift Control** | `MANDATE_04` | Forensic handshake validation prevents behavioral/data drift |
| **ATR Position Sizing** | `ENH_29` / `ENH_41` | Volatility-adjusted position sizing — deterministic formula |
| **Forensic Structural Filter** | `ENH_30` | 50% sizing reduction on dilution/warrant forensic flags |
| **Post-ATR Execution Gate** | `ENH_36` | Blocks entries after 14:30 ET in low-volatility conditions |
| **Correlation Guard** | `ENH_43` | Warns on >80% pairwise correlation or >35% sector exposure |
| **Web Verification Protocol** | `ENH_55` | Forces sub-agents to visually verify multi-timeframe mathematical trends (1D, 5D, 6M, YTD) using the Google Finance extension |
| **Live Web Grounding** | `MANDATE_15` | Mandates explicit native Google Search tool usage for disconfirming evidence and catalyst verification |
| **Flash-Tier Scrutiny** | `ENH_78` | Hardens data integrity during high-velocity analysis via cross-reference grounding |
| **State Emission** | `MANDATE_09` | Optimized non-destructive merge; emits delta payloads while preserving untouched portfolio tickers |

---

## ⚙️ Setup & Configuration

### Prerequisites
- **Google Gemini** — Access to [Gemini Gems](https://gemini.google.com/gems) (requires Gemini Advanced)
- **Google Drive** — For SSoT backup and cross-session persistence
- **Python 3.10+**
- **API Keys** — Finnhub (required), Polygon (optional)

### 1. Install Python Dependencies
```bash
pip install yfinance pandas numpy requests scipy pyperclip
```

### 2. Create `config.json`
```json
{
  "FINNHUB_API_KEY": "your_finnhub_api_key_here",
  "POLYGON_API_KEY": "your_polygon_api_key_here"
}
```

### 3. Configure Tickers
Edit `fetch_stocks.py` to set your portfolio and macro benchmark tickers separately:
```python
# Portfolio / trade tickers
TICKERS = ['ONDS', 'UMAC', 'RCAT', 'DFTX', 'RKLB', 'PLTR']

# Macro benchmarks & risk indicators
MACRO_TICKERS = ['IEF', '^VIX', 'VIXY', 'UUP']

# Combined list used by the dashboard
ALL_TICKERS = TICKERS + MACRO_TICKERS
```
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
For each JSON file, create a new Gem in Google Gemini:
1. Go to [gemini.google.com/gems](https://gemini.google.com/gems)
2. Click **"New Gem"**
3. Paste the contents of the JSON file as the Gem's system instruction
4. Name the Gem to match its role (e.g., *"GEM Terminal"*, *"GEM Rule Enforcer"*)
5. **Knowledge Injection (Web UI Sandbox Bridge):**
   - **Required Attachments**: You MUST attach the **`rules`** Google Doc (from `GEM_Trading_Rules/`) to the Gem's knowledge base using the Drive extension. If using the Research Engine, attach the **Trading_Research** folder.
   - **Payload Injection (Live File Attachment):** To prevent 192K token timeout crashes during 'Thinking' mode, the full SSOT and trade_lessons files should NOT be pasted into the chat box or attached permanently to the Gem's Knowledge Base. Instead, you must attach the live 'local_ssot_shadow.json' and 'trade_lessons.md' files directly to your first chat message as live attachments.

### 5. Initialize Air-Gap State Files
The system uses local JSON files to maintain your "State of the World" portfolio and history across sessions without encountering web latency. Before your first run, create these two empty files in the root directory:
- `local_ssot_shadow.json` (Initialize with an empty object `{}`)
- `trade_lessons.md` (Initialize with an empty Markdown registry)

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
System Check: Access the rules at GoogleDrive://GEM_Trading_Rules/rules.
Please cite the exact value for "GLOBAL_ALPHA_FRICTION_HURDLE" located in "system_thresholds".
Also, confirm which "ENH_Protocol" governs the "Macro Calendar Shield".
```

**Expected Result:**
- `GLOBAL_ALPHA_FRICTION_HURDLE`: **0.0117** (default)
- `Macro Calendar Shield`: **ENH_47**

### Test 2: The Mutation Verification (Definitive)
Confirm the Gem is reading the *live* Google Doc, not a stale cache.

1.  **Modify:** Open `GEM_Trading_Rules/rules` in Google Drive. Change `"GLOBAL_ALPHA_FRICTION_HURDLE": 0.0117` to **`0.099`**.
2.  **Ask:** In the Gem, ask:
    ```text
    Reload Rules from GoogleDrive://GEM_Trading_Rules/rules.
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
2. ACKNOWLEDGE SCHEMA: The system now uses the v8.6-Forensic-Zero-Hallucination-Sync Layer Model. The `local_storage_state` payload block will contain all data wrapped in `"immutable_background"` and `"mutable_state"`. You must merge delta updates into `"mutable_state"`.
3. ACTION (ZERO-TOUCH SYNC): Use your Google Drive extension to read the attached `rules.md` markdown document in the (GEM_Trading_Rules) folder.
4. VERIFICATION: Do NOT fabricate data. If you cannot read the file, STOP and output "TOOL FAILURE". 
   - Cite the exact `version` string verbatim from the top of the rules.md file.
   - Confirm which "ENH_" protocol governs the "Web Verification Protocol".
5. Confirm Status: "System initialized: State is bound to v8.6-Forensic-Zero-Hallucination-Sync Payload Architecture and SSoT Rules are synced via Drive."
```

### 🟧 Local Pipeline Setup (The Clipboard Bridge)
Since we're using the Air-Gapped Sandboxed architecture, your state is generated by Gemini but saved locally via your Web Dashboard.

The dashboard provides **two copy modes** to optimize token usage:

| Button | Payload | When to Use |
|--------|---------|-------------|
| **📋 Copy Turn Data** | Slim tickers (`price`, `vwap`, `gap_percent`, `rsi`, `atr_percent`, `net_gex_total`, `dealer_posture`, `score`, `trend`, `signal`) + compact `mutable_state` (`unallocated_cash_eur`, `total_liquidity_eur`, `risk_regime`, slim `portfolio_snapshot`) | Every turn during an active LLM session — lightweight, token-efficient |
| **📋 Copy Session Init** | Full SSOT (`local_ssot_shadow.json`), full ticker data, and `trade_lessons.md` registry | Session initialization, start of day, or when trade lessons have changed |

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

### End of Day Close
Run this at the end of each trading session to perform a final audit and generate the EOD backup:

```
Session Close initiated. Perform a final forensic audit of today's trading session and
synchronize all end-of-day data for active tickers. Incorporate final trade entries,
including Units, WAC, and Remaining Cash.

Once the audit is complete, execute the FULL_STATE_OUTPUT protocol per SSoT_Storage
Generate the absolute, complete SSoT JSON in a single markdown code block with zero omissions
and no placeholders (e.g., do not use "// ..."). Every key from both immutable_background and
mutable_state must be fully populated. Increment the sync_id for this final EOD synchronization.
```

---

## 🛸 Antigravity Sync Protocol (The Engine Custodian)
To ensure absolute mathematical and logical synchronization across the GEM Trading Terminal, use the **Antigravity** persona.

**How to use:**
Upload the `antigravity.md` file along with your other project instructions to your Gemini Gem.

**Trigger:** Whenever you need an update or refactor, start your prompt with:
> "Antigravity, execute a Sync Protocol on [File Name] using the antigravity.md guardrails."

**Result:** Antigravity will automatically hunt for hardcoded numbers, update forensic math proofs (MANDATE_06), and ensure engine versioning is consistent without you having to explain the 1.17% friction or FX logic every time.

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

---

## 🏗️ Design Principles

- **Canonical Centralization** — Individual engines do not contain hardcoded logic or parameters (e.g. lists of exogenous shocks). All logic points natively to canonical protocols in `GEM_Rules_Data`.
- **Logic/Data Separation** — `SSoT_Storage.json` holds state schema; `rules.md` holds static laws; `trade_lessons.md` holds historical institutional memory; `rule_enforcer_engine.md` exclusively handles execution logic.
- **Non-Destructive Merging** — Field-level merge with `PRESERVE_IF_NOT_UPDATED` strategy
- **Forensic Lineage** — Every state change includes timestamped source attribution
- **Alpha-Friction Awareness** — All engines account for the 1% round-trip cost of the Nordea OST platform

---

## 📜 License

This project is provided as-is for personal and educational use.

> **Disclaimer:** This system is a research and educational tool. It does not constitute financial advice. Always do your own due diligence before making investment decisions.
