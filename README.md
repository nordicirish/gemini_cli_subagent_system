# 🤖 Gemini Trading Subagent System

**An autonomous, multi-agent AI trading intelligence system powered by the Google Gemini API.**

Each JSON file is a **system instruction** for a dedicated AI sub-agent. Together, these agents form an institutional-grade trading council that analyses live market data, enforces risk protocols, and produces consensus-driven trade decisions — all accessible via a real-time, glassmorphic Web Dashboard with a built-in AI chat interface.

> **No manual intervention required.** The system is fully autonomous. The AI Orchestrator reads live market data, writes to the local SSoT, appends trade lessons, and coordinates multiple council agents in parallel via automated tool calls.

---

## 🚀 Quick Start

### Prerequisites

- Python 3.10+
- A [Google AI Studio API Key](https://aistudio.google.com/app/apikey) with the Gemini API enabled
- A `config.json` file in the root with your `FINNHUB_API_KEY` (optional, for GEX data)

### Installation

```powershell
# 1. Clone the repo
git clone https://github.com/your-org/gemini_cli_subagent_system
cd gemini_cli_subagent_system

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set your Gemini API key
$env:GEMINI_API_KEY="your_api_key_here"

# 4. Launch the system
python web_server.py
```

The dashboard will be available at **http://localhost:8000**

---

## 🏗️ Architecture

The system runs as a single Python process with two concurrent components:

```
┌─────────────────────────────────────────────────────────────────────┐
│                        web_server.py (Entry Point)                  │
│                                                                     │
│  ┌──────────────────────────┐   ┌──────────────────────────────┐   │
│  │   FastAPI Web Server      │   │   Background Data Daemon     │   │
│  │   (http://localhost:8000) │   │   (fetch_stocks.py thread)   │   │
│  │                          │   │                              │   │
│  │  GET  /api/data          │   │  Polls Yahoo Finance / yfinance│  │
│  │  POST /api/chat  ──────────────► Terminal Orchestrator       │   │
│  │  POST /api/tickers       │   │  Updates GLOBAL_STATE every  │   │
│  │  POST /api/macro         │   │  30 seconds                  │   │
│  └──────────────────────────┘   └──────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### Terminal Orchestrator (Chat AI)

The chat interface in the dashboard connects directly to a **Gemini 3.1 Pro** (or best available) model configured as the Terminal Orchestrator. It has access to the following **tool functions**:

| `read_ssot()` | Reads the current SSoT (`local_ssot_shadow.json`) |
| `update_ssot(payload)` | Merges an execution payload into the SSoT |
| `read_trade_lessons()` | Reads all historical trade lessons |
| `update_trade_lessons(lesson)` | **NEW**: Appends a new insight to the lessons library autonomously |
| `get_market_data()` | Returns the latest live ticker and macro data |
| `ask_<subagent>(query)` | Delegates a query to a specialised sub-agent |
| `ask_council(queries)` | **NEW**: Parallel Dispatcher — runs multiple agents simultaneously (3x speedup) |

Sub-agents marked as **Research**, **Sentiment**, and **Context** engines additionally have access to **Google Search** for live web data.

---

## 📁 File Reference

### Entry Points

| File | Purpose |
|------|---------|
| `web_server.py` | **Primary entry point.** Starts the FastAPI server, initialises all sub-agents, and exposes the `/api/chat` endpoint. |
| `main.py` | Alternative **CLI-only** orchestrator. Runs the same agent stack in a terminal chat loop. |

### Core Python Modules

| File | Purpose |
|------|---------|
| `agent_framework.py` | Initialises the Gemini API client, defines the model fallback hierarchy (`PRO → FAST`), and creates sub-agent tool functions. |
| `tools.py` | Defines the tool functions available to the Terminal Orchestrator: `read_ssot`, `update_ssot`, `read_trade_lessons`, `get_market_data`. Also starts the background data daemon. |
| `fetch_stocks.py` | **FastAPI app + background data daemon.** Fetches live ticker data (price, RSI, ATR, VWAP, GEX, dealer posture, etc.) via `yfinance` and Finnhub. Exposes all `/api/*` routes. |

### Persistent Data Files

| File | Purpose |
|------|---------|
| `local_ssot_shadow.json` | **Single Source of Truth.** Stores the active portfolio snapshot, mutable trading state, and forensic intelligence. Written to by the Orchestrator via `update_ssot`. |
| `trade_lessons.json` | **Historical trade lessons.** Appended after each post-trade review. Read by all agents to avoid repeating past mistakes. |
| `user_config.json` | **Persisted user preferences.** Stores the active ticker list and macro indices. Auto-created on first save; survives server restarts. |
| `config.json` | API keys for Finnhub and Polygon (optional). |

### Sub-Agent JSON Instructions

Each file below is loaded as a system instruction for a dedicated AI sub-agent via `agent_framework.py`:

| File | Agent Name | Mode | Role |
|------|-----------|------|------|
| `terminal.json` | **Terminal Orchestrator** | PRO | Routes user queries, delegates to sub-agents, synthesises final decisions |
| `macro_arbiter.json` | **Macro Sentinel** | PRO | Macro regime detection, Calendar Shield, exogenous shock monitoring |
| `bullish_gem.json` | **Bullish Advocate** | THINKING | Constructs the strongest bull case with self-critique |
| `red_team_gem.json` | **Red Team Pessimist** | THINKING | Constructs the strongest bear case with self-critique |
| `neutral_gem.json` | **Neutral Structuralist** | PRO | Unbiased structural analysis; breaks ties with quantitative evidence |
| `execution.json` | **Execution Engine** | PRO | Generates `EXECUTION_PAYLOAD` JSON; manages sizing and order routing |
| `structural_engine.json` | **Structural Engine** | FAST | GEX regime, dark pool posture, VWAP structure analysis |
| `technical_validator.json` | **Technical Validator** | PRO | Final gate — validates thesis against quantitative restrictions pre-execution |
| `research.json` | **Research Engine** | THINKING | Live web search for macro narrative, filings, sector rotation signals |
| `sentiment_engine.json` | **Sentiment Engine** | PRO | Social sentiment, news velocity, dark pool order flow |
| `context_engine.json` | **Context Engine** | PRO | SSoT state bridge — maintains session continuity and trade thesis integrity |
| `gex_engine.json` | **GEX Engine** | PRO | Gamma Exposure modelling, dealer hedging flow, pin risk analysis |
| `post_trade_review.json` | **Review Engine** | PRO | Post-trade reflection — thesis vs. outcome, misfire detection, lesson authoring |
| `rule_enforcer_engine.json` | **Rule Enforcer** | PRO | Compliance guardian — validates all decisions against `rules.json` mandates |

### Model Hierarchy

The system uses a **tiered fallback model** strategy defined in `agent_framework.py`:

| Mode | Model Priority | Used By |
|------|---------------|---------|
| `PRO` | `gemini-3.1-pro-preview` → `gemini-2.5-pro` | Orchestrator, complex engines |
| `THINKING` | `gemini-3.1-pro-preview` → `gemini-2.5-pro` | Council advocates, Research |
| `FAST` | `gemini-2.5-flash` → `gemini-2.0-flash` | Utility engines (Sentiment, Review, etc.) |
| `LOCAL` | `Gemma 4 e4b` (via Ollama) | Context, Execution, Rule Enforcement |

If a model returns a `429 Resource Exhausted` error, the system automatically waits 32 seconds and retries before falling back to the next model.

---

## 🖥️ Web Dashboard

The dashboard is served at **http://localhost:8000** and includes:

- **Real-time market table** — Ticker, Price, Gap %, Volume, ATR %, RSI, VWAP, Trend, Dealer Posture, Score
- **Macro HUD** — Live cards for tracked macro indices (VIX, SPY, IEF, UUP, GDX, etc.)
- **Alerts panel** — Surfaced signal alerts from the data daemon
- **GEM Orchestrator Chat** — Full AI chat interface. Supports **Markdown rendering**, parallel "Council Debate" synthesis, and dynamic "Thinking..." indicators.
- **Manage Tickers modal** — Add/remove tracked equity tickers (saved to `user_config.json`)
- **Indices modal** — Add/remove tracked macro indices (saved to `user_config.json`)

### Keyboard Shortcuts
- `Enter` — Send chat message
- `Shift + Enter` — New line in chat input

---

## ⚙️ Configuration

### Changing Tracked Tickers

Click **📊 Manage Tickers** in the dashboard sidebar. Changes are saved immediately to `user_config.json` and persist across server restarts.

### Changing the API Key

```powershell
$env:GEMINI_API_KEY="your_new_key_here"
python web_server.py
```

### Editing Sub-Agent Instructions

Each JSON file in the root is human-readable and can be edited directly. The changes take effect on the **next server restart**. No Gem synchronisation is required.

---

## 🗄️ Data Architecture

### SSoT (Single Source of Truth)

The `local_ssot_shadow.json` file is the system's persistent memory. The AI Orchestrator reads from it at the start of each session and writes back execution payloads automatically using the `update_ssot` tool — no manual copy-paste required.

### Trade Lessons

The `trade_lessons.json` file is a growing library of codified trade post-mortems. The system now **automatically appends new lessons** via the `update_trade_lessons` tool after the Orchestrator identifies long-term insights during a council session.

### User Config

`user_config.json` is auto-created the first time you save a ticker or index list from the dashboard:

```json
{
  "tickers": ["ONDS", "UMAC", "RCAT", "DFTX"],
  "macro": ["^VIX", "VIXY", "IEF", "UUP", "SPY", "GDX"]
}
```

---

## 🧠 Governance Backbone

### Mandates vs. Protocols

The `rules.json` file separates two distinct classes of directives:

1. **Mandates (`MANDATE_*`)** — Non-negotiable system behaviour constraints (e.g., always emit untruncated JSON, weighted consensus mechanics).
2. **Rules / Protocols (`ENH_*`)** — Financial execution strategies and domain knowledge (e.g., macro shock veto, pre-trade formulation, post-14:30 liquidity gates).

The **Rule Enforcer Engine** validates all decisions against these mandates before any execution payload is written.

### Adversarial Council Design

The consensus council uses a structured adversarial debate:
1. **Bullish Advocate** — Best bull case + self-critique
2. **Red Team Pessimist** — Best bear case + self-critique
3. **Neutral Structuralist** — Unbiased quantitative verdict

The Terminal Orchestrator synthesises all three positions into a final `HOLD / BUY / SELL / TRIM` decision with a source index.

---

## 📄 Licence

Private — internal trading research use only.
