# 💎 GEM Trading Agent System

**A multi-agent AI trading intelligence framework built on Google Gemini Gems.**

Each JSON file in this repository is a **system instruction** designed to be loaded into a separate [Google Gemini Gem](https://gemini.google.com/gems). Together, the Gems form an institutional-grade trading council that analyzes financial data, enforces risk protocols, and produces consensus-driven trade decisions. The Python scripts generate a real-time financial dashboard whose output is pasted into the Gem terminal conversation for analysis.

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

### The Air-Gapped SSoT Architecture (v4.0+)
Due to Google Gemini's web sandboxing (preventing direct external API calls) and Google Keep's synchronization latency, the system employs an **Air-Gapped Single Source of Truth**.
*   **The Processor:** The sandboxed Gemini UI acts as a stateless, high-powered reasoning engine.
*   **The Database:** Your local Python environment (`fetch_stocks.py`) maintains the persistent, live state (`local_ssot_shadow.json`).
*   **The Bridge (You):** You serve as the high-speed bus connecting the two via your OS clipboard (`c` to copy from Python, `v` to ingest Gem payloads back into Python).

---

## 📁 File Reference

### 🧠 Institutional Intelligence (v4.0+)

The system now enforces **adversarial reasoning** and **volatility awareness** to prevent hallucinations.

### 🧠 Console Architecture Principles (v4.1+)

The system integrates core software engineering principles into its trading logic to enhance autonomous rigor and self-improvement:
1. **Plan Node Default:** System dynamically generates a rigid "Trade Thesis" before execution and triggers re-planning if the thesis is violated.
2. **Subagent Strategy:** Employs "Dynamic Micro-Gem Routing" for borderline decisions (S_A 0.65–0.75) to spawn focused sub-engines to break ties.
3. **Self-Improvement Loop:** Conducts root-cause post-mortems on losses and natively appends `trade_lessons.json` to the clipboard payload so the system never repeats mistakes.
4. **Verification Before Done:** The `TECHNICAL_VALIDATOR` is forced to "prove the setup" against quantitative restrictions seconds before executing a `FORCE_WRITE`.
5. **Demand Elegance:** Explicitly rejects hacky or borderline setups relying on multiple overriding exceptions. Pushes for A+ convergence setups.
6. **Autonomous Bug Fixing:** Functions as a zero-prompt risk manager. Pre-formats a TRIM/EXIT JSON output if quantitative guards fail.

### 1. Volatility Regime Awareness
The Python feed actively monitors the **VIX** and tags the environment:
- **LOW_VOL (<12)**: System prioritizes Mean Reversion and structural liquidity.
- **HIGH_VOL (>20)**: System prioritizes Momentum and tighter risk stops.

### 2. Chain-of-Thought Enforcement
The **Neutral Gem** and **Validator** must output a structured logic block:
> **Reasoning Path:** 1. Regime Analysis → 2. Dealer Posture → 3. Liquidity Gap → 4. Verdict.

### 3. Devil's Advocate Protocols
To combat confirmation bias, the Consensus Council must argue against themselves:
- **Bullish Gem:** Must list "Top 3 Bear Case Risks" before concluding.
- **Red Team:** Must list "Top 3 Bull Case Opportunities" before concluding.

---

## 🛠️ Setup Instructions (JSON) — One per Gem

| File | Gem Role | Gemini Mode | Purpose |
|------|----------|-------------|---------|
| `terminal.json` | **Orchestrator** | PRO | Master router — classifies user input and dispatches to the correct engine |
| `rules.json` | **Legislative Body** | N/A | **SSoT**: Stored centrally at `GoogleDrive://GEM_Trading_Rules/rules.json`. DO NOT upload as static knowledge. |
| `rule_enforcer_engine.json` | **Rule Enforcer** | PRO | Active Enforcer — Policing Agent that validates logic against `rules.json` |
| `SSoT_Storage.json` | **SSoT Controller** | PRO | Passive data schema & state container (Single Source of Truth) |
| `context_engine.json` | **Context Engine** | PRO | Active SSoT bridge — drift detection, state merging, sync orchestration |
| `bullish_gem.json` | **Bullish Advocate** | THINKING | Alpha & momentum specialist — identifies reasons to approve trades |
| `red_team_gem.json` | **Red Team Pessimist** | THINKING | Adversarial risk specialist — identifies reasons to reject trades |
| `neutral_gem.json` | **Neutral Structuralist** | PRO | Market architecture specialist — GEX, regime detection, liquidity |
| `execution.json` | **Execution Engine** | PRO | ATR-adjusted position sizing, order generation, liquidity gates |
| `gex_engine.json` | **GEX Engine** | PRO | Gamma exposure computation — net GEX, gamma flip, dealer posture |
| `institutional.json` | **Institutional Engine** | FAST | Structural viability — dilution, warrants, capital structure, governance |
| `macro_arbiter.json` | **Macro Sentinel** | PRO | Binary risk-on/risk-off veto — CPI, FOMC, FX, geopolitical shocks |
| `sentiment_engine.json` | **Sentiment Engine** | PRO | Sentiment & catalyst extraction — news, social velocity, regulatory |
| `structural_risk.json` | **Structural Risk Engine** | FAST | Forensic dilution detection — shelf offerings, warrant walls, PIPE |
| `technical_validator.json` | **Technical Validator** | PRO | Data integrity, consensus synthesis, health score calculation |
| `research.json` | **Research Engine** | THINKING | Macro narrative, sector rotation, forensic signal attribution |
| `post_trade_review.json` | **Review Engine** | PRO | Post-trade reflection — thesis vs. outcome, misfire detection |

### Python Utilities

| File | Purpose |
|------|---------|
| `fetch_stocks.py` | **Financial Dashboard** — Real-time terminal. Generates JSON prompt payloads (press `c`) and ingests Gem execution payloads from the clipboard (press `v`) to maintain the local SSoT. |
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

### 1. Generate Financial Data
```bash
python fetch_stocks.py
```
The dashboard runs in a loop (30-second refresh), fetching live data from Yahoo Finance and Finnhub for your configured tickers. It displays a color-coded terminal table with:

- **Price & Change %** — session-aware (pre-market, regular, after-hours)
- **VWAP & Distance** — volume-weighted average price positioning
- **GEX Profile** — net gamma exposure, gamma flip price, dealer posture
- **Technicals** — RSI, ATR%, SMA 20/50/200, trend score
- **Health Score** — composite signal scoring

The formatted JSON "State of the World" payload is **copied to your clipboard** when you press `c` in the terminal.

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
2. In your running `fetch_stocks.py` terminal, press **`v`**.
3. The Python orchestrator natively parses the clipboard, validates the JSON, and writes the state to `local_ssot_shadow.json`.

---

## 🛡️ Key Risk Protocols

| Protocol | ID | Enforcement |
|----------|----|-------------|
| **Alpha-Friction Guard** | `ENH_FIN_02` | Blocks trades with <2.5% expected move (covers 1% round-trip fees - adjust as neededc) |
| **Macro Veto** | `MANDATE_20` | Macro Sentinel can override all council decisions (shock > 8.0) |
| **Drift Control** | `MANDATE_04` | Forensic handshake validation prevents behavioral/data drift |
| **ATR Position Sizing** | `ENH_29` / `ENH_41` | Volatility-adjusted position sizing — deterministic formula |
| **Forensic Structural Filter** | `ENH_30` | 50% sizing reduction on dilution/warrant forensic flags |
| **Post-ATR Execution Gate** | `ENH_36` | Blocks entries after 14:30 ET in low-volatility conditions |
| **Correlation Guard** | `ENH_43` | Warns on >80% pairwise correlation or >35% sector exposure |
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
MACRO_TICKERS = ['SPY', 'VXX', 'IEF', 'UUP']

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
5. **Knowledge Injection (CRITICAL - ZERO TOUCH SYNC):**
   - **ALL Gems**: You MUST attach the **`rules`** Google Doc (from `GEM_Trading_Rules/`) to the Gem's knowledge base using the Drive extension.
   - **Automatic Sync**: The Gems are programmed to *automatically* check this attached document for updates before every response. You do not need to manually reload it; just edit the Google Doc, and the Gem will see it instantly.
   - **Do NOT upload static files**. Internal JSON instructions are now "Knowledge Bound" to these live Drive files.

### 5. Initialize Air-Gap State Files
The system uses local JSON files to maintain your "State of the World" portfolio and history across sessions without encountering web latency. Before your first run, create these two empty files in the root directory:
- `local_ssot_shadow.json` (Initialize with an empty object `{}`)
- `trade_lessons.json` (Initialize with an empty array `[]`)

*(Note: These files are included in `.gitignore` to prevent accidentally committing your personal trading portfolio and history).*

### 6. Run the Dashboard
```bash
python fetch_stocks.py
```
The output copies to your clipboard when you press 'c' — paste it into the Terminal Orchestrator Gem to begin analysis.

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
CRITICAL INSTRUCTION: WIPE MEMORY & FORCE TOOL USE.
Protocol:
0.  **ACKNOWLEDGE**: You have access to the 'Google Drive' extension. It is ACTIVE.
1.  **ACTION**: Search for Google Doc named "rules" in folder "GEM_Trading_Rules".
2.  **CONSTRAINT**: Do NOT fabricate data. If you cannot read the file, STOP and say "TOOL FAILURE".
3.  **VERIFICATION**:
    -   Cite `version` string verbatim from the Google Doc.
    -   Cite `system_thresholds > ALPHA_FRICTION_MINIMUM`.
4.  **Confirm Status**: "Synced with Google Doc 'rules' (SSoT)".
```

### 🟧 Local Pipeline Setup (The Clipboard Bridge)
Since we're using the Air-Gapped Sandboxed architecture, your state is generated by Gemini but saved locally by Python.

1. **Copy:** Press `c` in the terminal to package live prices, technicals, your local `local_ssot_shadow.json` portfolio state, and your `trade_lessons.json` history.
2. **Send:** Paste this block into the Orcehstrator Gem.
3. **Receive:** The Gem processes the state and outputs an `EXECUTION_PAYLOAD` block. Copy it.
4. **Save:** Press `v` in the terminal. Python instantly updates your local SSoT shadow and logs any new trade lessons.

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
You no longer need to manually paste your entire state JSON into the chat to start your day. Just press `c` while `fetch_stocks.py` is running, and the active local SSoT and lessons history will be packaged right alongside the live market prices. Paste the block into the Gem and immediately prompt:

```
Restore this as active GEM context.
```

### End of Day Close
Run this at the end of each trading session to perform a final audit and generate the EOD backup:

```
Session Close initiated. Perform a final forensic audit of today's trading session and
synchronize all end-of-day data for active tickers. Incorporate final trade entries,
including Units, WAC, and Remaining Cash.

Once the audit is complete, emit an EXECUTION_PAYLOAD so I can sync it locally.

Once the audit is complete, execute the FULL_STATE_OUTPUT protocol per SSoT_Storage.json.
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

- **No Persona Contamination** — Every Gem identifies its engine role to prevent cross-role hallucination
- **Logic/Data Separation** — `SSoT_Storage.json` holds state schema; `rules.json` holds static laws; `rule_enforcer_engine.json` holds enforcement logic
- **Non-Destructive Merging** — Field-level merge with `PRESERVE_IF_NOT_UPDATED` strategy
- **Forensic Lineage** — Every state change includes timestamped source attribution
- **Alpha-Friction Awareness** — All engines account for the 1% round-trip cost of the Nordea OST platform

---

## 📜 License

This project is provided as-is for personal and educational use.

> **Disclaimer:** This system is a research and educational tool. It does not constitute financial advice. Always do your own due diligence before making investment decisions.
