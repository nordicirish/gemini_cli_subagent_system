# 🤖 Gemini Trading Subagent System

**An autonomous, multi-agent AI trading intelligence system powered by the Google Gemini API.**

Each Markdown file (`.md`) is a **system instruction** for a dedicated AI sub-agent. Together, these agents form an institutional-grade trading council that analyses live market data, enforces risk protocols, and produces consensus-driven trade decisions — all accessible via a real-time, glassmorphic Web Dashboard with a built-in AI chat interface.

> **Autonomous & Self-Evolving.** The system is fully autonomous, utilizing **Gemini Context Caching** to ingest a 40k-token rulebook and historical lesson library for near-instant inference. It features a **Self-Optimization Protocol (ENH_61/62)** that allows it to propose and commit its own logic updates to the core rulebook (`rules.md`) after human-in-the-loop validation.

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

The chat interface in the dashboard connects directly to a **Gemini 2.5 Pro** (or best available) model configured as the Terminal Orchestrator. It has access to the following **tool functions**:

| `read_ssot()` | Reads the current SSoT (`ssot.json`) |
| `update_ssot(payload)` | Merges an execution payload into the SSoT |
| `read_trade_lessons()` | Reads all historical trade lessons |
| `update_trade_lessons(lesson)` | Appends a new insight to the lessons library autonomously (ENH_62) |
| `update_rules(rules_md)` | **NEW**: Commits rule promotions to `rules.md` (Human-gated per MANDATE_21) |
| `get_market_data()` | Returns live ticker/macro data (Fixed return logic) |
| `perform_web_forensic_search()` | Performs grounded search for catalysts and filings |
| `ask_<subagent>(query)` | Delegates a query to a specialised sub-agent |
| `ask_council(queries)` | Parallel Dispatcher — runs multiple agents simultaneously (3x speedup) |

Sub-agents marked as **Research**, **Sentiment**, **Bullish Advocate**, **Red Team Pessimist**, **Technical Validator**, and **Macro Sentinel** have built-in **Google Search Grounding** to fetch live 2026 catalysts and override static training data.

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
| `ssot.json` | **Single Source of Truth.** Stores the active portfolio snapshot, mutable trading state, and forensic intelligence. Written to by the Orchestrator via `update_ssot`. |
| `trade_lessons.json` | **Historical trade lessons.** Appended after each post-trade review. Read by all agents to avoid repeating past mistakes. |
| `user_config.json` | **Persisted user preferences.** Stores the active ticker list and macro indices. Auto-created on first save; survives server restarts. |
| `config.json` | API keys and local model overrides. |
| `trade_lessons.json` | **Historical trade lessons.** Appended autonomously via ENH_62. |
| `rules.md` | **Canonical Rules Engine.** Now supports autonomous updates and **MANDATE_23 Triage**. |

---

## ⚡ High-Performance Features

### 🚀 Context Caching (ENH_CACHE_01)
The system aggregates the entire rulebook, trade history, and subagent instructions into a **Gemini Context Cache**. This reduces the per-turn token cost and eliminates "cold start" latency, allowing the Orchestrator to respond in seconds.

### 🧠 Torque-Based Triage (MANDATE_23)
The Orchestrator now intelligently scales its analytical depth based on news magnitude:
- **Minor News (Torque < 5)**: Dispatches the **Neutral Structuralist** only for structural capacity verification.
- **Major News (Torque >= 5)**: Triggers a **Full Council Audit** (Bullish + Red Team + Structuralist) for thesis re-rating and trade execution.

### 🏛️ Autonomous Rule Evolution (ENH_61/62)
The system "Learns" from market events. When a high-conviction pattern is identified, the **Context Engine** proposes a new rule. Once you provide **MANDATE_21** approval, the system autonomously modifies `rules.md`.

### 🕓 Temporal Alignment (Wall Street Time)
The system is anchored to **US/Eastern (New York) Time** regardless of your local timezone. This ensures the Council correctly identifies if the market is **OPEN** and applies the correct intraday vs. after-hours protocols.

### 💎 Modernized Council UI
- **Session Manager**: Start fresh conversations and archive past deliberations into a **Session History** library (last 10 sessions).
- **Global Stop Button**: Instantly interrupt long-running tool executions or model deliberations.
- **Persistent Chat**: Conversations survive page refreshes and modal closures via `localStorage`.
- **Resizable Interface**: User-adjustable chat panels and multi-line input areas.
- **Full-Width Output**: Forensic reports expand for maximum readability.

### Sub-Agent Markdown Instructions

Each file below is loaded as a system instruction for a dedicated AI sub-agent via `agent_framework.py`. The system has been migrated to **Markdown (.md)** for improved token efficiency and stricter behavioural adherence:

| File | Agent Name | Mode | Role |
|------|-----------|------|------|
| `terminal.md` | **Terminal Orchestrator** | PRO | Routes user queries, delegates to sub-agents, synthesises final decisions |
| `macro_arbiter.md` | **Macro Sentinel** | PRO | Macro regime detection, Calendar Shield, exogenous shock monitoring (Search Enabled) |
| `bullish_gem.md` | **Bullish Advocate** | PRO | Constructs the strongest bull case + self-critique (Search Enabled) |
| `red_team_gem.md` | **Red Team Pessimist** | PRO | Constructs the strongest bear case + self-critique (Search Enabled) |
| `neutral_gem.md` | **Neutral Structuralist** | PRO | Unbiased structural analysis; breaks ties with quantitative evidence |
| `execution.md` | **Execution Engine** | PRO | Generates `EXECUTION_PAYLOAD` JSON; manages sizing and order routing |
| `structural_engine.md` | **Structural Engine** | FAST | GEX regime, dark pool posture, VWAP structure analysis |
| `technical_validator.md` | **Technical Validator** | PRO | Final gate — validates thesis against quantitative restrictions (Search Enabled) |
| `research.md` | **Research Engine** | PRO | Live web search for macro narrative, filings, sector rotation signals |
| `sentiment_engine.md` | **Sentiment Engine** | PRO | Social sentiment, news velocity, dark pool order flow (Search Enabled) |
| `context_engine.md` | **Context Engine** | PRO | SSoT state bridge — maintains session continuity and trade thesis integrity |
| `gex_engine.md` | **GEX Engine** | PRO | Gamma Exposure modelling, dealer hedging flow, pin risk analysis |
| `post_trade_review.md` | **Review Engine** | PRO | Post-trade reflection — thesis vs. outcome, misfire detection, lesson authoring |
| `rule_enforcer_engine.md` | **Rule Enforcer** | PRO | Compliance guardian — validates all decisions against `rules.md` mandates |

### Model Hierarchy

The system uses a **tiered fallback model** strategy defined in `agent_framework.py`:

| Mode | Model Priority | Used By |
|------|---------------|---------|
| `PRO` | `gemini-2.5-pro` | Orchestrator, complex engines |
| `THINKING` | `gemini-2.5-pro` | Council advocates, Research |
| `GEMMA` | `gemma-4-31b-it` → `gemini-2.5-flash` | Context, Execution, Structural, Rule Enforcement |
| `FAST` | `gemini-2.5-flash` → `gemini-2.0-flash` | Utility engines (Sentiment, Review, etc.) |

If a model returns a `429 Resource Exhausted` error, the system automatically parses the specific `retry-after` wait time from Google's API header and executes a dynamic backoff before retrying.

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

Each Markdown file (.md) in the root or `GEM_Trading_Rules/` is human-readable and can be edited directly. The changes take effect on the **next server restart**. No manual synchronisation is required.

---

## 🗄️ Data Architecture

### SSoT (Single Source of Truth)

The `ssot.json` file is the system's persistent memory. The AI Orchestrator reads from it at the start of each session and writes back execution payloads automatically using the `update_ssot` tool — no manual copy-paste required.

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

The `rules.md` file separates two distinct classes of directives:

1. **Mandates (`MANDATE_*`)** — Non-negotiable system behaviour constraints (e.g., always emit untruncated JSON, weighted consensus mechanics).
2. **Rules / Protocols (`ENH_*`)** — Financial execution strategies and domain knowledge (e.g., macro shock veto, pre-trade formulation, post-14:30 liquidity gates).

The **Rule Enforcer Engine** validates all decisions against these mandates before any execution payload is written. The migration to Markdown ensures the AI pays higher attention to these mandates by stripping syntax noise.

### Adversarial Council Design

The consensus council uses a structured adversarial debate:
1. **Bullish Advocate** — Best bull case + self-critique
2. **Red Team Pessimist** — Best bear case + self-critique
3. **Neutral Structuralist** — Unbiased quantitative verdict

The Terminal Orchestrator synthesises all three positions into a final `HOLD / BUY / SELL / TRIM` decision with a source index.

---

## 📄 Licence

Private — internal trading research use only.
