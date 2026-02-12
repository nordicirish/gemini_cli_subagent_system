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

**Governance backbone:** The **Rules Engine** (`rules_engine.json`) contains 45+ ENH protocols and 20 MANDATEs that every Gem must obey.

---

## 📁 File Reference

### System Instructions (JSON) — One per Gem

| File | Gem Role | Gemini Mode | Purpose |
|------|----------|-------------|---------|
| `terminal.json` | **Orchestrator** | PRO | Master router — classifies user input and dispatches to the correct engine |
| `rules_engine.json` | **Rules Engine** | PRO | Master contract with 45+ ENH protocols & 20 MANDATEs governing all logic |
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
| `quick_check.json` | **QuickCheck Engine** | FAST | Fast feasibility check (≤6 lines) — OST status, VWAP, GEX posture |

### Python Utilities

| File | Purpose |
|------|---------|
| `fetch_stocks.py` | **Financial Dashboard** — Real-time terminal displaying price, VWAP, GEX, RSI, ATR, rVol, and health scores. Output is copied to clipboard for pasting into Gem conversations. |
| `compare_json.py` | Diff utility — compares two JSON instruction files to detect missing or added values |
| `format_json.py` | Formatter — pretty-prints a JSON instruction file with consistent indentation |

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
- **rVol** — relative volume vs. 20-day average
- **Health Score** — composite signal scoring

The formatted output is automatically **copied to your clipboard**.

### 2. Paste Into a Gem Conversation
Open the relevant Gemini Gem (e.g., the Terminal Orchestrator) and paste the dashboard output. The Gem parses the financial data and routes it through the appropriate engine pipeline.

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

### 5. SSoT State Commit
Every turn concludes with a full SSoT JSON dump, maintaining state across the stateless web-based Gem sessions.

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

### 4. Set Up Google Drive SSoT Backup
The Gems are stateless web sessions — create a Google Drive document to persist your SSoT state across sessions:

1. Create a new **Google Doc** named exactly **`GEM-Context-SSOT`**
2. In Google Gemini, go to **Settings → Extensions** and **enable Google Drive integration**
3. This allows the Gems to read the SSoT backup document at session start (`MANDATE_12_BOOT_SYNC`)

> [!IMPORTANT]
> The `GEM-Context-SSOT` document is how your portfolio state survives between Gem sessions. Without it, each new conversation starts with a blank state.

### 5. Create the Gems
For each JSON file, create a new Gem in Google Gemini:
1. Go to [gemini.google.com/gems](https://gemini.google.com/gems)
2. Click **"New Gem"**
3. Paste the contents of the JSON file as the Gem's system instruction
4. Name the Gem to match its role (e.g., *"GEM Terminal"*, *"GEM Rules Engine"*)

### 6. Run the Dashboard
```bash
python fetch_stocks.py
```
The output auto-copies to your clipboard — paste it into the Terminal Orchestrator Gem to begin analysis.

---

## 💬 Essential Prompts

These are the key prompts used to manage state across Gem sessions. Copy-paste them as needed.

### Context Bridge Sync (Periodic Backup)
Periodically save your SSoT to the Google Drive document:

```
Export Context Bridge Sync.
```

### Full State Export (Manual SSoT Dump)
Force a complete, untruncated SSoT JSON output for backup:

```
Execute FULL_STATE_OUTPUT protocol per SSoT_Storage.json. Generate the complete SSoT JSON
in a single, raw markdown code block. Do not use snippets, do not omit unchanged keys, and
do not use placeholders like '// ... rest of code'. Every field defined in the schema—including
state_context, forensic_intelligence, portfolio_snapshot, and mapping_rules—must be present.
Increment the sync_id before outputting.
```

Copy the resulting JSON and paste it into your **`GEM-Context-SSOT`** Google Doc.

### Session Restore (Start of Day)
Paste the saved SSoT JSON into the Gem conversation followed by:

```
Restore this as active GEM context.
```

### End of Day Close
Run this at the end of each trading session to perform a final audit and generate the EOD backup:

```
Session Close initiated. Perform a final forensic audit of today's trading session and
synchronize all end-of-day data for active tickers. Incorporate final trade entries,
including Units, WAC, and Remaining Cash.

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
- **Logic/Data Separation** — `SSoT_Storage.json` holds schema only; `rules_engine.json` holds all execution logic
- **Non-Destructive Merging** — Field-level merge with `PRESERVE_IF_NOT_UPDATED` strategy
- **Forensic Lineage** — Every state change includes timestamped source attribution
- **Alpha-Friction Awareness** — All engines account for the 1% round-trip cost of the Nordea OST platform

---

## 📜 License

This project is provided as-is for personal and educational use.

> **Disclaimer:** This system is a research and educational tool. It does not constitute financial advice. Always do your own due diligence before making investment decisions.
