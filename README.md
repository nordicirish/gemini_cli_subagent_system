# 🤖 GEM Trading Agent Framework (v18.0)

**An autonomous, multi-agent AI trading intelligence system powered by Google Gemini & Gemma.**

Each Markdown file (`.md`) is a **system instruction** for a dedicated AI sub-agent. Together, these agents form an institutional-grade trading council that analyses live market data, enforces risk protocols, and produces consensus-driven trade decisions — all accessible via a real-time, glassmorphic Web Dashboard with a built-in AI chat interface.

> **Cost-Optimized & Local-First.** The system has been hardened to eliminate all external Google Drive/Research dependencies, relying exclusively on a local **Single Source of Truth (`ssot.json`)**. It utilizes a hybrid model architecture, routing high-precision logic to **Gemma 4 31B** to maximize performance while remaining within free-tier API quotas.

---

## 🚀 Quick Start

### Prerequisites

- Python 3.10+
- A [Google AI Studio API Key](https://aistudio.google.com/app/apikey)
- A `config.json` file for API keys and local model overrides.

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

The system runs as a high-performance Python framework with a dynamic model router:

```
┌─────────────────────────────────────────────────────────────────────┐
│                        web_server.py (Entry Point)                  │
│                                                                     │
│  ┌──────────────────────────┐   ┌──────────────────────────────┐   │
│  │   FastAPI Web Server      │   │   Background Data Daemon     │   │
│  │   (http://localhost:8000) │   │   (fetch_stocks.py thread)   │   │
│  │                          │   │                              │   │
│  │  GET  /api/data          │   │  Polls Yahoo Finance / SSoT  │   │
│  │  POST /api/chat  ──────────────► Model Router (Logic Tier)   │   │
│  │  POST /api/save_basket   │   │  Updates GLOBAL_STATE every  │   │
│  │  POST /api/save_watch    │   │  30 seconds                  │   │
│  └──────────────────────────┘   └──────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
          │                                  │
    ┌─────▼────────────────┐           ┌─────▼────────────────┐
    │  GEMINI 2.5 PRO/FLASH│           │     GEMMA 4 31B      │
    │  (Reasoning & Search)│           │   (Precision Logic)  │
    └──────────────────────┘           └──────────────────────┘
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
| `ssot.json` | **Master Data Store.** The Single Source of Truth for portfolio holdings, cost basis (WAC), and the dynamic watch list. Synchronized in real-time with the Web Dashboard. |
| `trade_lessons.json` | **Historical trade lessons.** Appended autonomously via ENH_62 to prevent repeating past mistakes. |
| `rules.md` | **Canonical Rules Engine.** Local rules document containing all mandates, thresholds, and autonomous logic updates. |
| `config.json` | **API Configuration.** Keys for Gemini, Finnhub, and local model routing overrides. |

---

## ⚡ High-Performance Features

### 🧠 Hybrid Model Routing (v18.0)
To optimize for both **cost** and **precision**, the system now routes sub-agents to specific model tiers:
- **GEMMA Tier (Gemma 4 31B)**: Handles deterministic, JSON-heavy, and structural analysis (Context, Structural, Technical Validator).
- **FLASH Tier (Gemini 2.5 Flash)**: Handles high-speed research, social sentiment, and news velocity monitoring.
- **PRO Tier (Gemini 2.5 Pro)**: Reserved for the Terminal Orchestrator and complex Macro Arbitration.

### 💼 Dynamic Ticker Management
The dashboard now features an **Inline Ticker Manager**:
- **Basket Management**: Add/Delete tickers directly in the portfolio view. Update shares and cost basis (UAC) with instant SSoT synchronization.
- **Watch List**: Maintain a separate list of monitored symbols. The data daemon automatically begins polling any ticker added to the watch list.
- **SSoT Sync**: All UI updates trigger a `POST` to the framework, ensuring the AI Council always analyzes the most current state of your universe.

### 🚀 Context Caching (ENH_CACHE_01)
The system aggregates the entire rulebook, trade history, and subagent instructions into a **Gemini Context Cache**. This reduces the per-turn token cost and eliminates "cold start" latency, allowing the Orchestrator to respond in seconds.

### 🏛️ Autonomous Rule Evolution (ENH_61/62)
The system "Learns" from market events. When a high-conviction pattern is identified, the **Context Engine** proposes a new rule. Once you provide **MANDATE_21** approval, the system autonomously modifies `rules.md`.

### Sub-Agent Markdown Instructions

Each file below is loaded as a system instruction for a dedicated AI sub-agent. High-precision nodes have been migrated to the **GEMMA** tier for improved instruction-following and cost efficiency:

| File | Agent Name | Mode | Role |
|------|-----------|------|------|
| `terminal.md` | **Terminal Orchestrator** | PRO | Routes user queries, delegates to sub-agents, synthesises final decisions |
| `macro_arbiter.md` | **Macro Sentinel** | PRO | Macro regime detection, Calendar Shield monitoring (Search Enabled) |
| `bullish_gem.md` | **Bullish Advocate** | FLASH | Constructs the strongest bull case + self-critique (Search Enabled) |
| `red_team_gem.md` | **Red Team Pessimist** | FLASH | Constructs the strongest bear case + self-critique (Search Enabled) |
| `neutral_gem.md` | **Neutral Structuralist**| GEMMA | Unbiased structural analysis; breaks ties with quantitative evidence |
| `execution.md` | **Execution Engine** | PRO | Generates `EXECUTION_PAYLOAD` JSON; manages sizing and order routing |
| `structural_engine.md` | **Structural Engine** | GEMMA | GEX regime, dark pool posture, VWAP structure analysis |
| `technical_validator.md` | **Technical Validator** | GEMMA | Final gate — validates thesis against quantitative restrictions |
| `research.md` | **Research Engine** | FLASH | Live web search for macro narrative, filings, sector rotation signals |
| `sentiment_engine.md` | **Sentiment Engine** | FLASH | Social sentiment, news velocity, dark pool order flow (Search Enabled) |
| `context_engine.md` | **Context Engine** | GEMMA | SSoT state bridge — maintains session continuity and trade thesis integrity |
| `gex_engine.md` | **GEX Engine** | GEMMA | Gamma Exposure modelling, dealer hedging flow, pin risk analysis |
| `post_trade_review.md` | **Review Engine** | PRO | Post-trade reflection — thesis vs. outcome, misfire detection, lesson authoring |

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

The glassmorphic dashboard is served at **http://localhost:8000** and includes:

- **Real-time market table** — Ticker, Price, Gap %, Volume, ATR %, RSI, VWAP, Trend, Dealer Posture, Score.
- **Macro HUD** — Live cards for tracked macro indices (VIX, SPY, IEF, UUP, GDX, etc.).
- **Dynamic Portfolio Basket** — Inline management of your holdings. Add/Delete tickers and update cost basis (`UAC ($)`) with real-time **SSoT Sync**.
- **Interactive Watch List** — Monitor new setups by injecting symbols directly into the background data daemon.
- **GEMINI AI COUNCIL Chat** — Full AI chat interface. Supports **Markdown rendering**, parallel "Council Debate" synthesis, and dynamic "Thinking..." indicators.

### Keyboard Shortcuts
- `Enter` — Send chat message
- `Shift + Enter` — New line in chat input

---

## ⚙️ Configuration

### Managing the Ticker Universe

The system has moved away from hard-coded configurations. All tickers are managed via the **Portfolio Basket** and **Watch List** on the dashboard. 
1.  **Add Ticker**: Type the symbol into the `SYM` field and click `+`.
2.  **Edit Basis**: Update your shares or cost basis directly in the table.
3.  **Sync**: Click **SYNC** to commit your changes to the local `ssot.json` data store. 
    *   *Note: Changes are instantly reflected in the background data poller.*

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
