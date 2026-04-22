# 💎 GEM Trading Agent System

**A multi-agent AI trading intelligence framework built on Google Gemini Gems.**

Each JSON file in this repository is a **system instruction** designed to be loaded into a separate [Google Gemini Gem](https://gemini.google.com/gems). Together, the Gems form an institutional-grade trading council that analyzes financial data, enforces risk protocols, and produces consensus-driven trade decisions. The Python backend (`fetch_stocks.py`) drives a real-time, glassmorphic Web Dashboard UI, allowing quick JSON payload extraction and SSoT paste-syncing directly from the browser.

---

## 📐 Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                   TERMINAL ORCHESTRATOR                     │
│                     (terminal.json)                         │
│          Routes user input to the correct engine            │
└────────────┬────────────────────────────────────────────────┘
             │
     ┌───────┴────────┐
     │  ROUTING LAYER  │
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
          └──────────┬──────────┘
                     │
          ┌──────────┴──────────┐
          │  EXECUTION ENGINE    │
          │  (Order Generation)  │
          └─────────────────────┘
```

**Governance backbone:** The **Rule Enforcer** (`rule_enforcer_engine.json`) actively validates compliance, while `rules.json` serves as the static legislative body containing the system thresholds, along with two distinct classes of architectural directives:

### 🏛️ The Architectural Divide: Mandates vs. Protocols
To prevent the LLM reasoning engines from hallucinating software structures while analyzing financial data, the system strictly separates **Software Architecture** from **Trading Logic**:

1. **Mandates (`MANDATE_01` to `MANDATE_21`)**: These govern **System Behavior & Constraints**. These are non-negotiable instructions telling the Gemini model *how to act as a piece of software*. 
   - *Example:* `MANDATE_09` (Always output un-truncated JSON), `MANDATE_13` (Weighted Consensus voting mechanics).
2. **Rules / Protocols (`ENH_01` to `ENH_52`)**: These govern **Financial Execution & Domain Knowledge**. These are the fluid, quantitative strategies telling the system *how to trade*. 
   - *Example:* `ENH_45` (Macro Shock Veto), `ENH_50` (Pre-Trade Formulation), `ENH_36` (Post-14:30 Liquidity Gates).

### The Air-Gapped SSoT Architecture (v4.9x+)
Due to Google Gemini's web sandboxing preventing direct external API calls, the system employs an **Air-Gapped Single Source of Truth via Prompt Payload Injection**.
*   **The Processor:** The sandboxed Gemini Web UI acts as a stateless, high-powered reasoning engine. It does not require the SSoT file or trade lessons to be attached as Google Docs.
*   **The Bridge:** The Python backend (`fetch_stocks.py`) injects your live state (`local_ssot_shadow.json`) and history (`trade_lessons.json`) directly into the markdown prompt it generates for you. 
*   **The Sync:** Gemini outputs a strict `EXECUTION_PAYLOAD` JSON block. You copy this block and paste it into the Web Dashboard (`/api/paste`), seamlessly syncing the LLM's decisions back to your local files.

---

## 📁 File Reference

### 🧠 Institutional Intelligence (v4.0+)

The system now enforces **adversarial reasoning** and **volatility awareness** to prevent hallucinations.

### 🧠 Console Architecture Principles (v4.1+)

The system integrates core software engineering principles into its trading logic to enhance autonomous rigor and self-improvement:
1. **Plan Node Default:** System dynamically generates a rigid "Trade Thesis" before execution and triggers re-planning if the thesis is violated.
1.  **Plan Node Default:** System dynamically generates a rigid "Trade Thesis" before execution and triggers re-planning if the thesis is violated.
2.  **Subagent Strategy:** Employs "Dynamic Micro-Gem Routing" for borderline decisions (S_A 0.65–0.75) to spawn focused sub-engines to break ties.
3.  **Self-Improvement Loop:** Conducts root-cause post-mortems on losses and natively appends `trade_lessons.json` to the clipboard payload so the system never repeats mistakes.
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

## 🛠️ Setup Instructions (JSON) — One per Gem

| File | Gem Role | Gemini Mode | Purpose |
|------|----------|-------------|---------|
| `terminal.json` | **Orchestrator** | PRO | Master router — classifies user input and dispatches to the correct engine |
| `rules.json` | **Legislative Body** | N/A | **Static Rules**: The canonical source containing thresholds and ENH_ protocols. MUST be attached via Google Drive. |
| `rule_enforcer_engine.json` | **Rule Enforcer** | PRO | Active Policing Agent — solely responsible for validating logic against `GEM_Rules_Data` |
| `SSoT_Storage.json` | **SSoT Controller** | PRO | Passive data schema logic. (State ops owned by Context Engine, execution logic deferred to Rule Enforcer) |
| `context_engine.json` | **Context Engine** | PRO | Active SSoT bridge — drift detection, state merging, sync orchestration |
| `bullish_gem.json` | **Bullish Advocate** | THINKING | Alpha & momentum specialist — identifies reasons to approve trades |
| `red_team_gem.json` | **Red Team Pessimist** | THINKING | Adversarial risk specialist — identifies reasons to reject trades |
| `neutral_gem.json` | **Neutral Structuralist** | PRO | Market architecture specialist — GEX, regime detection, liquidity |
| `execution.json` | **Execution Engine** | PRO | ATR-adjusted position sizing, order generation, liquidity gates |
| `gex_engine.json` | **GEX Engine** | PRO | Gamma exposure computation — net GEX, gamma flip, dealer posture |

| `macro_arbiter.json` | **Macro Sentinel** | PRO | Binary risk-on/risk-off veto — CPI, FOMC, FX, geopolitical shocks |
| `sentiment_engine.json` | **Sentiment Engine** | PRO | Sentiment & catalyst extraction — news, social velocity, regulatory |
| `structural_risk.json` | **Structural Risk Engine** | FAST | Forensic dilution detection — shelf offerings, warrant walls, PIPE |
| `technical_validator.json` | **Technical Validator** | PRO | Data integrity, consensus synthesis, health score calculation |
| `research.json` | **Research Engine** | THINKING | Macro narrative, sector rotation, forensic signal attribution |
| `post_trade_review.json` | **Review Engine** | PRO | Post-trade reflection — thesis vs. outcome, misfire detection |

### Python Utilities

| File | Purpose |
|------|---------|
| `fetch_stocks.py` | **FastAPI Backend & Web Server** — Serves a real-time web dashboard at `http://localhost:8000`. Generates JSON prompt payloads and exposes an `/api/paste` route to ingest Gem execution payloads into the local SSoT. |
| `compare_json.py` | Diff utility — compares two JSON instruction files to detect missing or added values |
| `format_json.py` | Formatter — pretty-prints a JSON instruction file with consistent indentation |
### 4. Updating Gems (Maintenance)

When you update the JSON instructions in this repository (e.g., version increment to v4.02), you must **manually update** the live Gem:

1.  **Open the Gem** in [Gemini Advanced](https://gemini.google.com/gems).
2.  Click the **Pencil Icon** (Edit) > **Instructions**.
3.  **Copy** the content of the updated JSON file (e.g., `technical_validator.json`).
4.  **Paste** it into the Instructions box, replacing the old content.
5.  Click **Update**.

**Pro Tip:** If you are mid-session and don't want to restart, you can type:
> *"SYSTEM UPDATE: I have patched your instructions to v4.02. Please simulate the following logic update: [Paste critical logic change]"*
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
Open the relevant Gemini Gem (e.g., the Terminal Orchestrator) and paste the dashboard payload. The Gem parses the financial data and routes it through the appropriate engine pipeline.

### 3. The Council Deliberates
For trade decisions, the system triggers the **Consensus Pipeline**:

1. **Macro Sentinel** — checks for exogenous shocks (CPI, FOMC, geopolitical)
2. **Bullish Advocate** — identifies alpha catalysts, momentum signals, and entry thesis
3. **Red Team Pessimist** — stress-tests for dilution, liquidity voids, structural traps
4. **Neutral Structuralist** — evaluates GEX posture, market regime, and structural capacity

### 4. Synthesis & Decision
The **Technical Validator** computes the weighted **Agreement Score (S_A)**:

| Agent | Weight |
|-------|--------|
| Neutral Structuralist | 0.45 |
| Red Team Pessimist | 0.35 |
| Bullish Advocate | 0.20 |

**Decision thresholds:**
- `S_A ≥ 0.75` → **EXECUTE**
- `S_A 0.50–0.74` → **HOLD** / Route to Deep Research
- `Fatal Flaw Score > 8` → **HARD VETO** (S_A forced to 0.0)

### 5. Local State Ingestion (Air-Gap Bridge)
Every turn concludes with the Gem outputting a precise JSON block named `EXECUTION_PAYLOAD`.
1. Copy this JSON block from the Gemini UI.
2. Click **"Paste Payload"** on your dashboard sidebar.
3. The React/JS frontend routes the payload to the Python orchestrator, which natively parses the clipboard, validates the JSON, and seamlessly writes the state to `local_ssot_shadow.json` and `trade_lessons.json`.

---

## 🛡️ Key Risk Protocols

| Protocol | ID | Enforcement |
|----------|----|-------------|
| **Alpha-Friction Guard** | `ENH_FIN_02` | Blocks trades with <2.5% expected move (covers 1% round-trip fees - adjust as neededc) |
| **Macro Veto** | `MANDATE_20` | Sentinel vetoes entries against surging VIXY velocity (>+5.0%) or high absolute VIX (> 20) |
| **Drift Control** | `MANDATE_04` | Forensic handshake validation prevents behavioral/data drift |
| **ATR Position Sizing** | `ENH_29` / `ENH_41` | Volatility-adjusted position sizing — deterministic formula |
| **Forensic Structural Filter** | `ENH_30` | 50% sizing reduction on dilution/warrant forensic flags |
| **Post-ATR Execution Gate** | `ENH_36` | Blocks entries after 14:30 ET in low-volatility conditions |
| **Correlation Guard** | `ENH_43` | Warns on >80% pairwise correlation or >35% sector exposure |
| **Web Verification Protocol** | `ENH_55` | Forces sub-agents to visually verify multi-timeframe mathematical trends (1D, 5D, 6M, YTD) using the Google Finance extension |
| **State Emission** | `MANDATE_09` | Every turn must output complete, untruncated SSoT JSON |

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

> [!IMPORTANT]
> **When changing tickers**, you must also update the centralized constants in `rules.json`. All ticker-specific data is consolidated in one file — see the reference table below.

#### Ticker-Dependent Constants (`rules.json`)

These sections **must** be updated whenever you add, remove, or change portfolio tickers:

| Section | Path in `rules.json` | What to change |
|---------|---------------------------|----------------|
| **Basket Definition** | `basket_definition > defense_tech > tickers` | Add/remove tickers per theme |
| | `basket_definition > health_tech > tickers` | Add/remove tickers per theme |
| | `basket_definition > all_watched` | Must contain the union of all theme tickers |
| **Sector Taxonomy** | `sector_taxonomy > categories` | Add new sector names if your basket expands beyond current themes |
| **Temporal Events** | `temporal_events > DHS_CR_EXPIRY` | Update when government CR deadline changes |
| | `temporal_events > RCAT_INNOVATION_DAY` | Update when event date is announced (set `null` if unknown) |

These sections are **ticker-independent** but may need tuning for a different broker or strategy:

| Section | Path | Default | When to change |
|---------|------|---------|----------------|
| **Round-Trip Cost** | `system_thresholds > ROUND_TRIP_COST_BASIS` | `0.01` (1%) | Different broker fee structure |
| **Alpha-Friction Minimum** | `system_thresholds > ALPHA_FRICTION_MINIMUM` | `0.025` (2.5%) | Adjusting minimum viable move |
| **Slippage Penalty** | `system_thresholds > SLIPPAGE_PENALTY` | `0.5` | Different execution environment |
| **OST Lockout Time** | `system_thresholds > OST_LOCKOUT_TIME` | `14:30 ET` | Different market/time zone |

All 22+ named constants live in `rules.json > system_thresholds`. Sub-engine files reference these by name — **never hardcode values in sub-engines**.

### 4. Create the Gems
For each JSON file, create a new Gem in Google Gemini:
1. Go to [gemini.google.com/gems](https://gemini.google.com/gems)
2. Click **"New Gem"**
3. Paste the contents of the JSON file as the Gem's system instruction
4. Name the Gem to match its role (e.g., *"GEM Terminal"*, *"GEM Rule Enforcer"*)
5. **Knowledge Injection (Web UI Sandbox Bridge):**
   - **Required Attachments**: You MUST attach the **`rules`** Google Doc (from `GEM_Trading_Rules/`) to the Gem's knowledge base using the Drive extension. If using the Research Engine, attach the **Trading_Research** folder.
   - **Payload Injection (No longer attached)**: The `GEM-Context-SSOT` and `trade_lessons` files are **no longer required as attached files**. The Python dashboard now injects `local_storage_state` and `trade_lessons` directly into the text prompt payload it generates for you. 

### 5. Initialize Air-Gap State Files
The system uses local JSON files to maintain your "State of the World" portfolio and history across sessions without encountering web latency. Before your first run, create these two empty files in the root directory:
- `local_ssot_shadow.json` (Initialize with an empty object `{}`)
- `trade_lessons.json` (Initialize with an empty array `[]`)

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
Please cite the exact value for "ALPHA_FRICTION_MINIMUM" located in "system_thresholds".
Also, confirm which "ENH_Protocol" governs the "Macro Calendar Shield".
```

**Expected Result:**
- `ALPHA_FRICTION_MINIMUM`: **0.025** (default)
- `Macro Calendar Shield`: **ENH_47**

### Test 2: The Mutation Verification (Definitive)
Confirm the Gem is reading the *live* Google Doc, not a stale cache.

1.  **Modify:** Open `GEM_Trading_Rules/rules` in Google Drive. Change `"ALPHA_FRICTION_MINIMUM": 0.025` to **`0.099`**.
2.  **Ask:** In the Gem, ask:
    ```text
    Reload Rules from GoogleDrive://GEM_Trading_Rules/rules.
    What is the current value of ALPHA_FRICTION_MINIMUM?
    ```
3.  **Verify:** The Gem should report **0.099**.
4.  **Reset:** Change the value back to **0.025** in the Google Doc.

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
2. ACKNOWLEDGE SCHEMA: The system now uses the v4.9x Layer Model. The `local_storage_state` payload block will contain all data wrapped in `"immutable_background"` and `"mutable_state"`. You must merge delta updates into `"mutable_state"`.
3. ACTION (ZERO-TOUCH SYNC): Use your Google Drive extension to read the attached `rules` document (GEM_Trading_Rules/rules).
4. VERIFICATION: Do NOT fabricate data. If you cannot read the file, STOP and output "TOOL FAILURE". 
   - Cite the exact `version` string verbatim from the top of the Google Doc.
   - Confirm which "ENH_" protocol governs the "Web Verification Protocol".
5. Confirm Status: "System initialized: State is bound to v4.9x Payload Architecture and SSoT Rules are synced via Drive."
```

### 🟧 Local Pipeline Setup (The Clipboard Bridge)
Since we're using the Air-Gapped Sandboxed architecture, your state is generated by Gemini but saved locally via your Web Dashboard.

The dashboard provides **two copy modes** to optimize token usage:

| Button | Payload | When to Use |
|--------|---------|-------------|
| **📋 Copy Turn Data** | Slim tickers (`price`, `vwap`, `gap_percent`, `rsi`, `atr_percent`, `net_gex_total`, `dealer_posture`, `score`, `trend`, `signal`) + compact `mutable_state` (`unallocated_cash_eur`, `total_liquidity_eur`, `risk_regime`, slim `portfolio_snapshot`) | Every turn during an active LLM session — lightweight, token-efficient |
| **📋 Copy Session Init** | Full SSOT (`local_ssot_shadow.json`), full ticker data, and `trade_lessons.json` history | Session initialization, start of day, or when trade lessons have changed |

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
Click **"Copy Session Init"** on the Web Dashboard to package the full SSoT, live market prices, and trade lessons history. Paste the block into the Gem and immediately prompt:

```
Restore this as active GEM context.
```

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
- **Logic/Data Separation** — `SSoT_Storage.json` holds state schema; `rules.json` holds static laws; `rule_enforcer_engine.json` exclusively handles execution logic.
- **Non-Destructive Merging** — Field-level merge with `PRESERVE_IF_NOT_UPDATED` strategy
- **Forensic Lineage** — Every state change includes timestamped source attribution
- **Alpha-Friction Awareness** — All engines account for the 1% round-trip cost of the Nordea OST platform

---

## 📜 License

This project is provided as-is for personal and educational use.

> **Disclaimer:** This system is a research and educational tool. It does not constitute financial advice. Always do your own due diligence before making investment decisions.
