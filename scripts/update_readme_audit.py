"""
scripts/update_readme_audit.py
Antigravity Custodian Utility: Updates README.md with comprehensive architectural audit,
pure Google Gemini stack documentation, Council personality specifications,
feature suite documentation, and strict tone compliance per MANDATE_29.
"""

import os
import re

def build_readme_content():
    doc_sections = r'''# 💎 GEM Investment Portfolio Agent Framework

**An autonomous, API-driven multi-agent investment portfolio intelligence platform powered exclusively by Google Gemini.**

Each Markdown file (`.md`) in `engine_instructions/` defines the system instruction, behavioral bounds, and output schemas for a dedicated AI sub-agent. Together, these engines form a deliberative multi-agent council that analyzes live market data, enforces quantitative risk protocols, and compiles consensus-driven portfolio execution directives — accessible via an asynchronous Web Dashboard (`python/web_server.py`) or interactive CLI (`python/main.py`).

> **Pure Google Gemini Architecture.** Powered exclusively by Google Gemini. All structural, quantitative, and deliberative nodes operate natively on Google Gemini model tiers: **PRO / THINKING** (`gemini-3.7-flash`, `gemini-2.5-pro`, `gemini-3.1-pro-preview`) and **FAST / UTILITY** (`gemini-3.7-flash`, `gemini-3.1-flash-lite`, `gemini-2.5-flash`), with high-speed utility and background scout workflows isolated to the free-tier API client.

---

## 🖥️ Platform Showcase

<p align="center">
  <img src="static/dashboard_mockup.png" alt="GEM Live Web Dashboard Mockup" width="48%">
  <img src="static/chat_overlay_mockup.png" alt="GEM AI Council Chat Overlay UI Mockup" width="48%">
</p>

---

## 🚀 Quick Start

### Prerequisites

- Python 3.10+
- Google AI Studio API Key (`GEMINI_API_KEY`)
- Optional Free-Tier API Key (`GEMINI_FREE_TIER_API_KEY`) for isolated utility and scout routing
- Optional market data API keys (`FINNHUB_API_KEY`, `POLYGON_API_KEY`, `ALPHA_ADVANTAGE_API_KEY`)

### Installation

```powershell
# 1. Clone the repository
git clone https://github.com/nordicirish/gemini_cli_subagent_system
cd gemini_cli_subagent_system

# 2. Install dependencies
# Automated installer:
powershell -ExecutionPolicy Bypass -File install.ps1

# Or manual installation:
pip install -r requirements.txt

# 3. Configure API Keys
# Option A: Environment variables
$env:GEMINI_API_KEY="your_primary_api_key_here"
$env:GEMINI_FREE_TIER_API_KEY="your_free_tier_api_key_here"

# Option B: Persist in config.json
# Populate keys in config.json (see Configuration section)

# 4. Launch the platform
# Primary Entry Point (FastAPI Web Server + Background Daemon + Council Chat Overlay):
python python/web_server.py

# Alternative Entry Point (CLI Terminal Chat Loop):
python python/main.py
```

The web dashboard is served at **http://localhost:8000**.

---

## 🏗️ Architecture & Entry Point Clarity

`gemini_cli_subagent_system` is an **API-driven multi-agent platform** engineered for automated market monitoring, quantitative risk auditing, and council deliberation:

- **Primary Entry Point (`python/web_server.py`):** Runs the FastAPI application, mounts static frontend assets (`static/`), starts the background market data daemon (`fetch_stocks.py` thread updating `GLOBAL_STATE` every 30 seconds), serves REST endpoints, and exposes the interactive `/api/chat` streaming council endpoint.
- **CLI Entry Point (`python/main.py`):** Executes the same agent framework, tool bindings, and prompt pipelines directly inside an interactive terminal loop.
- **Persistent SSoT Data Store (`context/ssot.json`):** Serves as the Single Source of Truth for portfolio allocations, shares, weighted average cost basis (`wac`), cash reserves (`unallocated_cash_eur`, `unallocated_cash_usd`), and monitored watchlists. Automatically synchronized with execution payloads via `tools.update_ssot`.
- **Architectural Decoupling from `gem_trading_agent_system`:** While its sister repository (`gem_trading_agent_system`) operates as a clipboard-bridged workspace requiring manual prompt exports and response imports through external Gemini Web UI turns, `gemini_cli_subagent_system` features **direct programmatic tool-calling** via the Google GenAI SDK (`agent_framework.py`), native in-dashboard chat streaming (`/api/chat`), and automated database state ingestion (`tools.update_ssot`), operating with complete independence from manual clipboard cycles.

### System Architecture Flow

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                   python/web_server.py (Entry Point)                             │
│                                                                                                  │
│  ┌──────────────────────────────────────────────┐   ┌─────────────────────────────────────────┐  │
│  │             FastAPI Web Server               │   │         Background Data Daemon          │  │
│  │            (http://localhost:8000)           │   │        (fetch_stocks.py Thread)         │  │
│  │                                              │   │                                         │  │
│  │  GET  /api/data            (Market State)    │   │  Polls Yahoo Finance / Polygon API      │  │
│  │  POST /api/chat            (Council Stream)  │   │  Computes RSI, MACD, VWAP, BB, GEX      │  │
│  │  POST /api/basket          (SSoT Portfolio)  │   │  Updates GLOBAL_STATE every 30 seconds  │  │
│  │  POST /api/watchlist       (SSoT Watchlist)  │   │  Executes Dynamic AI Scout Scanner      │  │
│  │  POST /api/save_chart_screenshots (Vision)   │   │  Caches Daily History to JSON           │  │
│  └──────────────────────┬───────────────────────┘   └────────────────────┬────────────────────┘  │
│                         │                                                │                       │
│                         ▼                                                ▼                       │
│               AgentFramework Client                                 GLOBAL_STATE                 │
│               (agent_framework.py)                                (Shared Memory)                │
└─────────────────────────┬────────────────────────────────────────────────┬───────────────────────┘
                          │                                                │
          ┌───────────────┴──────────────────────────┐                     │
          │ Dual-Key Isolation & Model Routing        │                     │
          ▼                                          ▼                     ▼
┌────────────────────────────────────┐    ┌───────────────────────────────────┐  ┌─────────────────┐
│ Primary Client (GEMINI_API_KEY)    │    │ Free Client                       │  │ SSoT Data Store │
│ Paid / Standard Quota              │    │ (GEMINI_FREE_TIER_API_KEY)        │  │ context/ssot.json│
│                                    │    │ Free-Tier Isolated                │  └────────┬────────┘
│ • PRO Tier: gemini-3.7-flash       │    │                                   │           ▲
│   (or gemini-2.5-pro / 3.1-pro)    │    │ • FAST / UTILITY Tier:            │           │ tools.
│ • THINKING Tier: gemini-3.7-flash  │    │   gemini-3.7-flash                │           │ update_ssot
│ • Primary Orchestrator             │    │   (gemini-3.1-flash-lite fallback)│           │
│ • Ephemeral JIT Context Caching    │    │ • AI Scout Scanner                │           │
└─────────────────┬──────────────────┘    └─────────────────┬─────────────────┘           │
                  │                                         │                             │
                  │              429 Failover               │                             │
                  └─────────────────────────────────────────┘                             │
                                        │                                                 │
                                        ▼                                                 │
                       ┌─────────────────────────────────┐                                │
                       │    Unified Execution Payload    │────────────────────────────────┘
                       │    (EXECUTION_PAYLOAD JSON)     │
                       └─────────────────────────────────┘
```

---

## 🛡️ Autonomous Key Routing (Dual-Key Architecture)

To minimize operating expenses and protect primary API rate limits during high-frequency scans and council deliberations, `agent_framework.py` implements a **Dual-Key Isolation** architecture:

1. **Paid/Primary Client (`GEMINI_API_KEY`):**
   - Designated for the Primary Orchestrator (`terminal.md`), deep deliberative debate chains (`bullish_gem.md`, `red_team_gem.md`), complex research syntheses (`research.md`, `macro_narrative_engine.md`), and ephemeral JIT context caching (`client.caches.create`).
   - Defaults to `gemini-3.7-flash` (or `gemini-2.5-pro` / `gemini-3.1-pro-preview` for deep reasoning).

2. **Free-Tier Client (`GEMINI_FREE_TIER_API_KEY`):**
   - Configured in `config.json` or through environment variables.
   - Dedicated exclusively to high-speed utility routes (`gemini-3.1-flash-lite`, `gemini-2.5-flash`), routine quantitative validations (`technical_validator.md`, `gex_engine.md`, `rule_enforcer_engine.md`), and the background **AI Scout** breakout scanner (`fetch_stocks._get_dynamic_scout_tickers`).
   - Shields paid quotas from routine polling and high-frequency token consumption.

3. **Automated Rate-Limit Failover Cascade:**
   - If the free-tier client encounters a `429 Resource Exhausted` or daily quota error, `AgentFramework.generate_response_with_fallback()` intercepts the exception, logs a warning to `logs/gem_handshakes.log`, and immediately falls back to the primary key client to fulfill the request without dropping the session.
   - For temporary rate limits, the client dynamically extracts the `retry-after` header and pauses before retrying.

4. **Structured Handshake & Audit Telemetry:**
   - Every outbound request, inbound response, latency measurement, token usage metric, and error trace is recorded to `logs/gem_handshakes.log` via a 5-day rolling `TimedRotatingFileHandler`.

---

## 🏛️ Council Personalities & Sub-Agent Roster

The system organizes analytical responsibilities across specialized engines and four core Council personalities:

### 1. Bullish Advocate (`bullish_gem.md`) — "Contrarian Alpha Hunter"
- **Role:** Momentum and alpha specialist operating under an aggressive contrarian thesis.
- **Persona & Behavioral Mandate:** Rejects consensus retail/algorithmic herd pessimism. Hunts asymmetric upside, hidden structural catalysts, and breakout setups that panic-driven models overlook.
- **Key Protocols & Filters:**
  - *Institutional Handshake (ENH_37):* Audits Form 13-F and Form 144 institutional block accumulations.
  - *Dark Pool / Shadow Tape:* Identifies bid-ask midpoint prints (Trade Reporting Facility / TRF prints) as block accumulation evidence.
  - *Clinical Sentinels:* Tracks enrollment velocity and completion date stability on ClinicalTrials.gov for biotech setups.
  - *Technical Momentum:* Validates Price > VWAP, relative volume $rVol > \text{RVOL\_CONFIRMATION}$, MA50 > MA200 Golden Cross confirmations, and Alpha Friction Gate (upside exceeding `GLOBAL_ALPHA_FRICTION_HURDLE` 0.85%).
  - *Dilution Override (ENH_30 / L-228):* Holds permitted during secondary offering/shelf dilution announcements if paired with a Torque 10 binary catalyst (FDA approval, Tier-1 contract, Phase 3 success) above VWAP with $rVol > 3.0$.
  - *Double-Top Resistance Trim (ENH_258):* Advocates for an immediate 10% to 15% tactical alpha-harvest trim at morning peaks (Chart Level B) when rVol contracts, rather than maintaining 100% passive holds.
  - *Pairwise Alpha Rotation (MANDATE_53):* Proactively identifies momentum leaders for capital rotation when portfolio cash is zero (€0.00 EUR).
  - *Depth-Gated Bias Self-Critique (ENH_93):* Rigid schema with Brief Mode (confidence $\ge 0.85$) or Full Mode Verify-First Gate (confidence $< 0.85$). Enforces mandatory Top 3 Bear Cases list.

### 2. Red Team Pessimist (`red_team_gem.md`) — "Forensic Risk Auditor"
- **Role:** Adversarial risk and structural failure specialist.
- **Persona & Behavioral Mandate:** Evaluates all arguments through forensic skepticism; assumes bullish theses are vulnerable to retail hype, narrative bias, and institutional traps.
- **Key Protocols & Filters:**
  - *Independent Live Google Search:* Executes independent Google Search queries to uncover thesis-killers, SEC enforcement actions, warrant overhangs, or counter-narratives missing from internal context.
  - *Black Swan Zero-Success Simulation (ENH_68-B):* Mandated whenever council agreement score $S_A > 0.85$ to stress-test catastrophic failure of core catalysts (e.g., complete CRL rejection).
  - *5-Day Event-Risk Veto:* Hard veto (Fatal Flaw Score $\ge 8.0$) on mean-reversion and pullback setups within 5 trading days of scheduled earnings, FDA decisions, or Tier-1 macro events.
  - *Forensic Dilution Overhang Audit (ENH_30 / ENH_117):* Audits warrant exercise floors, ATM shelf registrations, and dilution resistance walls.
  - *Volatility Duality Mandate:* Cross-references trailing benchmark regime (^VIX > 20) with real-time intraday velocity (VIXY Rate-of-Change $> +5.0\%$ triggers Fatal Flaw Score $> 8.0$).
  - *Technical Fatal Flaws:* Death Cross (MA50 crossing below MA200), sustained sub-MA200 trading, and over-extension.
  - *Depth-Gated Bias Self-Critique (ENH_93):* Brief Mode vs. Full Mode Verify-First Gate. Enforces mandatory Top 3 Bull Cases list.

### 3. Neutral Structuralist (`neutral_gem.md`) — "Market Architecture & Liquidity Specialist"
- **Role:** Market architecture, options market-maker positioning, and liquidity specialist.
- **Persona & Behavioral Mandate:** Emotionless quantitative arbiter grounding decisions in dealer gamma exposure and order-book mechanics.
- **Key Protocols & Filters:**
  - *Dealer Positioning & GEX Modeling (ENH_17):* Tracks Net GEX, gamma slope, strike magnets, and zero-gamma flip thresholds to predict transition between vol-dampening (LONG_GAMMA) and vol-acceleration (SHORT_GAMMA).
  - *Gamma Whiplash Lock (ENH_17_B):* Places an asset on a mandatory 15-minute capital allocation freeze if dealer posture flips LONG $\rightarrow$ SHORT $\rightarrow$ LONG within a 30-minute window.
  - *SEC Rule 201 SSR Shield Invalidation (MANDATE_34 / ENH_16_E):* Strictly invalidates LONG_GAMMA hold shields if session change drops $< -10\%$ and triggers SEC Rule 201 SSR, forcing immediate classification as `STRUCTURAL_FAILURE`.
  - *SSR Proximity Liquidation (MANDATE_54):* Prohibits using the absence of an SSR trigger to justify holding deteriorating assets; mandates immediate 50% defensive trim if drawdown exceeds -8.0% from previous close without triggering the -10.0% SSR threshold.
  - *Pairwise Opportunity Cost Alpha Rotation (MANDATE_53):* Prohibits passive stasis at zero cash; audits laggards for 25-50% rotation into verified momentum leaders.
  - *Liquidity Void Sentinel:* Detects bid/ask order book depth voids during volatility compression.
  - *Depth-Gated Bias Self-Critique (ENH_93):* Brief Mode vs. Full Mode Verify-First Gate.

### 4. Terminal Orchestrator (`terminal.md`) — "Master Router & Absolute Arbiter"
- **Role:** Master router, hub-and-spoke consolidator, and final execution arbiter.
- **Persona & Behavioral Mandate:** Deterministic system router. Strictly suppresses persona drift (strictly prohibits tutor, assistant, or educational conversational filler).
- **Key Protocols & Execution Engine:**
  - *Hub-and-Spoke Consolidation (MANDATE_51 / MANDATE_22):* Sole authorized Hub emitting consolidated user-facing markdown and machine-executable JSON payloads. Intermediate sub-agent reasoning is suppressed internally.
  - *Forensic Math Proofs (MANDATE_06):* Math proof strings on all price, P&L, and sizing claims: `Proof: (Price [P] - PrevClose [C]) / [C] = Result%` and FX conversions.
  - *Google Finance AI Overview Emulation:* Structures executive summaries with `### 💡 AI Overview` (Why it's moving, 3 Key Drivers, Valuation/Momentum Context).
  - *Dynamic Trailing Stop & Fibonacci Telemetry (MANDATE_36 / ENH_104 / ENH_111 / ENH_255):* Persistently formats `### 📊 Active Telemetry & Suggested Sell Quantities` detailing downside risk anchors (VWAP stops) and upside Fibonacci daily peak scale-outs ($T_1, T_2, T_3$, ATR confluence, -0.25% front-run limit orders).
  - *Thought Signature Bypass:* Injects `"thoughtSignature": "context_engineering_is_the_way to_go"` on outgoing payloads to prevent API validation errors.
  - *Final Machine-Executable Emission:* Outputs unified `EXECUTION_PAYLOAD` with `portfolio_snapshot`, cash fields, and `council_debate` object for `decision_log.json` persistence.

---

### Sub-Agent Markdown Instructions & Model Mapping

Each sub-agent instruction file (`engine_instructions/*.md`) is loaded dynamically by `agent_framework.py`:

| File | Agent Name | Active Gemini Tier | Primary Technical Role |
|------|-----------|--------------------|------------------------|
| `terminal.md` | **Terminal Orchestrator** | PRO (`gemini-3.7-flash`) | Request routing, council debate consolidation, math verification, `EXECUTION_PAYLOAD` emission |
| `macro_sentinel.md` | **Macro Sentinel** | PRO (`gemini-3.7-flash`) | Macro benchmark regime monitoring, calendar shield audit, geopolitical energy scans (`BZ=F`, `CL=F`) |
| `data_analyst.md` | **Data Analyst** | PRO (`gemini-3.7-flash`) | Stage 0 live web grounding, price baseline verification, MTFA trend & ATR calculation |
| `state_validation_router.md` | **State & Validation Router** | PRO (`gemini-3.7-flash`) | State synthesis, schema compliance audit, SSoT drift detection |
| `research.md` | **Research Engine** | THINKING (`gemini-3.7-flash`) | Grounded web search for SEC filings (8-K, 10-Q, 424B), clinical registries, sector rotation |
| `macro_narrative_engine.md` | **Macro-Narrative Engine** | THINKING (`gemini-3.7-flash`) | Thematic macro backdrop synthesis, torque scoring (1–10), narrative catalyst attribution |
| `bullish_gem.md` | **Bullish Advocate** | THINKING (`gemini-3.7-flash`) | Contrarian momentum thesis, institutional 13-F block accumulation, double-top trims, self-critique |
| `red_team_gem.md` | **Red Team Pessimist** | THINKING (`gemini-3.7-flash`) | Forensic risk audit, independent web search for thesis-killers, Black Swan simulations, 5-day event veto |
| `neutral_gem.md` | **Neutral Structuralist** | FAST / UTILITY (`gemini-3.7-flash`) | Options dealer GEX modeling, gamma flip proximity, SSR shield invalidation, alpha rotation |
| `post_trade_review.md` | **Review Engine** | FAST / UTILITY (`gemini-3.7-flash`) | Post-trade reflection, thesis vs. outcome variance, trade lesson authoring |
| `regime_engine.md` | **Regime Engine** | FAST / UTILITY (`gemini-3.7-flash`) | Macro volatility regime classification (ADX, SPY EMAs, VIX matrix) |
| `strategy_engine.md` | **Strategy Engine** | FAST / UTILITY (`gemini-3.7-flash`) | High-beta setup classification (Momentum Breakout, High Tight Flag, Pullback, Mean-Reversion, Episodic Pivot) |
| `sentiment_engine.md` | **Sentiment Engine** | FAST / UTILITY (`gemini-3.7-flash`) | Social velocity, news sentiment divergence, retail crowding indicators |
| `structural_engine.md` | **Structural Engine** | FAST / UTILITY (`gemini-3.7-flash`) | Multi-timeframe VWAP structure, moving average ribbons, dark pool posture |
| `rule_enforcer_engine.md` | **Rule Enforcer Engine** | FAST / UTILITY (`gemini-3.7-flash`) | Legislative compliance verification, circuit breaker enforcement, mandate vetoes |
| `context_engine.md` | **Context Engine** | FAST / UTILITY (`gemini-3.7-flash`) | SSoT state bridging, session continuity, rule promotion proposal synthesis |
| `execution.md` | **Execution Engine** | FAST / UTILITY (`gemini-3.7-flash`) | Sizing calculation (ATR-based), limit order pricing, stop-loss telemetry handoff |
| `technical_validator.md` | **Technical Validator** | FAST / UTILITY (`gemini-3.7-flash`) | Quantitative schema validation, math proof verification, strict JSON formatting |
| `gex_engine.md` | **GEX Engine** | FAST / UTILITY (`gemini-3.7-flash`) | Gamma Exposure modeling, dealer hedging flow, strike magnet identification |

---

### Model Hierarchy & Fallback Matrix

Configured in `agent_framework.py`:

| Mode Tier | Primary Model | Fallback Candidates | Client Routing |
|-----------|---------------|---------------------|----------------|
| `PRO` | `gemini-3.7-flash` | `gemini-3.1-pro-preview`, `gemini-2.5-pro`, `gemini-3.5-flash` | Primary Client (`GEMINI_API_KEY`) |
| `THINKING` | `gemini-3.7-flash` | `gemini-3.1-pro-preview`, `gemini-3.5-flash` | Primary Client (`GEMINI_API_KEY`) |
| `FAST` / `UTILITY` | `gemini-3.7-flash` | `gemini-3.1-flash-lite`, `gemini-2.5-flash` | Free Client (`GEMINI_FREE_TIER_API_KEY`) with Primary Fallback |

---

### Programmatic Tool-Calling Suite

The Terminal Orchestrator and specialized sub-agents interact with system state and live market data through native tool bindings (`python/tools.py` and `python/agent_framework.py`):

| Tool Function | Signature / Target | Operational Purpose |
|---------------|-------------------|---------------------|
| `read_ssot()` | `() -> str` | Reads persistent Single Source of Truth (`context/ssot.json`) |
| `update_ssot(payload_json)` | `(str) -> str` | Merges incoming `EXECUTION_PAYLOAD` JSON into `context/ssot.json` |
| `read_trade_lessons()` | `() -> str` | Reads historical codified trade lessons (`context/trade_lessons.json`) |
| `update_trade_lessons(lessons_json)`| `(str) -> str` | Appends or updates trade lesson insights autonomously |
| `update_rules(rules_md_content)` | `(str) -> str` | Commits legislative rule promotions to `gem_trading_rules/rules.md` (MANDATE_21 gated) |
| `get_market_data()` | `() -> str` | Returns current market quote telemetry from background daemon `GLOBAL_STATE` |
| `read_decision_log()` | `() -> str` | Reads continuous time-series ledger of council decisions (`context/decision_log.json`) |
| `perform_web_forensic_search(query)`| `(str) -> str` | Executes live Google Search with grounding for corporate filings, catalysts, and events |
| `ask_<subagent>(query)` | `(str) -> str` | Dispatches single sub-agent queries with role-based payload slicing |
| `ask_council(queries_json)` | `(str) -> str` | Parallel Council Dispatcher executing multi-agent deliberations simultaneously via thread pools |

---

## ⚡ Core Technical Platform Features

### 1. Multimodal Vision & TradingView Lightweight Charts
- **Interactive 1m Candlestick Modal:** Clicking any ticker row opens an interactive modal powered by TradingView Lightweight Charts (v4.2.0, Apache 2.0).
- **24-Hour Continuous Market Data:** Charts render 1-minute OHLCV bars across Pre-market (04:00–09:30), Regular (09:30–16:00), and Post-market (16:00–20:00 ET) trading sessions.
- **Multi-EMA Ribbons & VWAP Envelopes:** Renders continuous exponential EMA 9 (Red), EMA 30 (Blue), and EMA 200 (White) alongside a continuous center VWAP baseline (Orange) and standard deviation envelope bands (+/- 1.25 stdev dashed green).
- **Bar-1 Accurate EMA 200 Warmup:** The backend `GET /api/intraday/{symbol}` partitions 5 days of 1-minute history into prior sessions' warmup closes and today's session bars, ensuring EMA 200 is seeded from the opening bar.
- **Automated Offscreen Chart Capture:** Triggering council chat boot or quick prompts renders and captures 1-minute chart screenshots offscreen for portfolio holdings, SPY benchmark, and active watchlist symbols.
- **Direct Multimodal Gemini Vision Ingestion:** Captured charts are streamed to `POST /api/save_chart_screenshots` and stored in `context/charts/chart_<TICKER>_1m.png`. During chat turns, active PNG charts are converted into `types.Part.from_bytes(data, mime_type="image/png")` objects and prepended to Gemini API requests (`[*chart_parts, prompt]`), enabling visual trend auditing alongside quantitative SSoT JSON payloads.

### 2. Trend-Based Fibonacci Forecasting (ENH_254 / ENH_255)
- **Quantitative Swing Anchors:** Identifies swing anchor points ($P_A$ swing low, $P_B$ swing high, $P_C$ retracement floor) across multi-timeframe structures.
- **Fibonacci Expansion Tranches:** Calculates expansion targets:
  - $T_1$ (1.000 expansion): Conservative initial take-profit level (20–25% trim).
  - $T_{1.272}$ (1.272 expansion): Structural extension level.
  - $T_2$ (1.618 Golden Ratio): Primary institutional peak target (50% cumulative trim).
  - $T_3$ (2.618 Blow-Off): Parabolic momentum runner liquidation.
- **Daily Peak ATR Volatility Confluence:** Computes daily volatility ceiling:
  $$\text{daily\_peak\_atr} = \text{open\_price} + (1.25 \times \text{ATR})$$
  Evaluates $\pm 1.8\%$ ATR confluence against Fibonacci levels to confirm institutional target alignment.
- **Front-Running Limit Orders:** Establishes limit order pricing at -0.25% offsets below target levels (`t1_limit`, `t2_limit`, `t3_limit`) to ensure fill execution before wholesale institutional liquidity resistance.
- **Exhaustion State Classification:** Classifies real-time price status into `EXPANDING`, `PEAK_APPROACH`, `AT_PEAK_RESISTANCE`, `PEAK_EXHAUSTED`, or `PARABOLIC_BLOW_OFF`.

### 3. Technical Oscillators & Indicator Suite
- **Wilder RSI (9-day and 14-day):** Computes both a sensitive 9-day RSI for intraday overbought/oversold detection and a standard 14-day RSI for swing evaluation.
- **MACD (12/26/9):** Evaluates MACD line, signal line, histogram, status tag (`BULLISH`, `BEARISH`, `NEUTRAL`), and 4-state histogram slope (`EXPANDING_POSITIVE`, `CONTRACTING_POSITIVE`, `EXPANDING_NEGATIVE`, `CONTRACTING_NEGATIVE`).
- **Bollinger Bands:** 20-period moving average with 2.0 standard deviation bands and %B oscillator.
- **Relative Volume (rVol):** Ratio of current intraday volume against 20-period historical average volume.
- **Options Dealer Posture GEX:** Calculates Net GEX, gamma slope, strike magnets, and directional delta chevrons (indicating Net GEX expansion or contraction between consecutive polling cycles).

### 4. Macro HUD & Ticker Management
- **Macro Benchmark Telemetry Cards:** Real-time HUD tracking key indices:
  - `^VIX`: CBOE Volatility Index
  - `VIXY`: Short-Term VIX Futures ETF
  - `SPY`: S&P 500 Index ETF
  - `IEF`: iShares 7-10 Year Treasury Bond ETF
  - `UUP`: Invesco DB US Dollar Index Bullish Fund
  - `GDX`: VanEck Gold Miners ETF
- **Inline Portfolio Basket Manager:** Add, edit, or delete portfolio holdings directly in the dashboard. Adjust shares and cost basis (`wac`) with immediate persistence to `context/ssot.json` via `/api/basket`.
- **Monitored Watchlist Manager:** Inject symbols into the background data daemon with automatic polling registration via `/api/watchlist`.
- **Scout Controls:** Toggle market sectors with optimistic UI state updates (`/api/scout_categories`, `/api/scout_sectors`).

### 5. AI Scout Scanner
- **Background Sector Breakout Discovery:** Daemon invokes `_get_dynamic_scout_tickers` via `GEMINI_FREE_TIER_API_KEY` using Google Search Grounding to identify sector leaders and emerging momentum candidates.
- **Quantitative Gating Filters:**
  - Price > SMA50 (intermediate trend confirmation)
  - Relative Volume ($rVol > 1.2$)
  - RSI Cap (`SCOUT_MAX_RSI`, default 75)
  - Total Scout Cap (`SCOUT_LIMIT`, default 3)
- **Metadata Tagging:** Injects `institutional_status: "Unverified Institutional Status"` into scout candidate metadata, triggering Stage 0E deep web grounding by the Council.

### 6. Ephemeral JIT Context Caching & Real-Time Cost Tracking
- **Ephemeral Just-In-Time (JIT) Caching:** For parallel council dispatches where the shared SSoT and legislative rules base exceeds 32,768 tokens, `AgentFramework.execute_ephemeral_batch()` dynamically provisions a temporary cache via `client.caches.create` with a 15-minute TTL, running all parallel sub-agent queries against it before deleting the cache in a strict `finally` block to prevent persistent storage fees.
- **Real-Time Cost Diagnostics:** Computes exact token usage (prompt tokens, candidates tokens, cached content tokens) on every call, tracking turn costs and cumulative session costs in both CLI and web server diagnostic outputs.
- **Payload Asymmetry & Context Slicing:** Dynamically slices incoming market context based on agent role (e.g., GEX data for GEX Engine, news/sentiment for Macro Sentinel, schemas for Technical Validator) to minimize context consumption.
- **Payload Minification (`_minify_payload`):** Strips comments, collapses blank lines, and trims whitespace from rules and trade lessons before generation.

### 7. Institutional Governance Backbone
- **Master Legislative SSoT (`gem_trading_rules/rules.md`):** Separates non-negotiable system invariants (`MANDATE_*`) from technical domain protocols (`ENH_*`).
- **Fail-Fast Invariants:** Rejects speculative fallbacks; unverified prices, corrupt schemas, or missing math proofs trigger hard stops.
- **Sequential Circuit Breakers:** Risk filters (Macro Sentinel, Red Team Pessimist, Rule Enforcer Engine) exercise independent veto authority.
- **State Machine Idempotency (ENH_31-S / ENH_31-P):** Directives in `EXECUTION_PAYLOAD` are promoted atomically to `mutable_state` in `ssot.json` without shadow state drift.
- **Trade Lesson Garbage Collection (ENH_53-GC):** When dynamic trade lessons from `context/trade_lessons.json` are promoted to codified mandates in `rules.md`, they are automatically purged from the lesson store to prevent context bloat.

---

## 🖥️ Web Dashboard & REST API Reference

The web dashboard is served at `http://localhost:8000` via FastAPI (`python/web_server.py`):

| Endpoint | Method | Payload / Params | Functionality |
|----------|--------|------------------|---------------|
| `/api/data` | GET | None | Returns active ticker quotes, technical oscillators, macro benchmarks, and system state |
| `/api/chat` | POST | `{"message": str, "model": str}` | SSE streaming chat endpoint for Council deliberation, auto-ingesting `EXECUTION_PAYLOAD` |
| `/api/cancel_chat` | POST | None | Signals immediate cancellation event to running council threads |
| `/api/reset_chat` | POST | None | Clears active conversational history |
| `/api/list_models` | GET | None | Discovers available Gemini models supporting function calling |
| `/api/set_model` | POST | `{"model": str}` | Overrides active primary orchestrator model |
| `/api/set_cache_policy` | POST | `{"policy": str}` | Configures context caching policy |
| `/api/system_logs` | GET | None | SSE stream of real-time backend operational logs |
| `/api/tickers` | GET | None | Returns list of currently tracked portfolio and watchlist symbols |
| `/api/basket` | GET / POST | `BasketSaveRequest` | Retrieves or saves portfolio holdings and cash balances to `context/ssot.json` |
| `/api/watchlist` | GET / POST | `List[str]` | Retrieves or saves monitored watchlist symbols to `context/ssot.json` |
| `/api/ai_scout` | POST | `{"mode": "sectors"}` | Triggers background AI Scout breakout scan |
| `/api/scout_categories` | GET / POST | `List[str]` | Retrieves or saves active scout sector categories |
| `/api/scout_sectors` | GET | None | Returns available sector categories from `config.json` |
| `/api/scout_config` | GET / POST | `{"limit": int, "max_rsi": int}` | Reads or updates scout limit and RSI filter configuration |
| `/api/intraday/{symbol}` | GET | URL path param | Returns 1-minute OHLCV bars and warmup closes for TradingView charts |
| `/api/save_chart_screenshots` | POST | `{"screenshots": dict, "replace_all": bool}` | Saves base64 PNG chart captures for Gemini multimodal vision ingestion |
| `/api/save_decision_log` | POST | `{"data": list}` | Appends council turn records to `context/decision_log.json` |
| `/api/clear_decision_log` | POST | None | Clears `context/decision_log.json` |

### Keyboard Shortcuts
- `Enter` — Send message to AI Council
- `Shift + Enter` — Insert newline in chat input

---

## ⚙️ Configuration & Environment Variables

### Shell Environment Variables
```powershell
# Set primary Gemini API key (Paid / Standard tier)
$env:GEMINI_API_KEY="your_primary_api_key_here"

# Set free-tier Gemini API key (Utility & Scout isolation)
$env:GEMINI_FREE_TIER_API_KEY="your_free_tier_api_key_here"

# Optional market data keys
$env:FINNHUB_API_KEY="your_finnhub_key_here"
$env:POLYGON_API_KEY="your_polygon_key_here"
$env:ALPHA_ADVANTAGE_API_KEY="your_alpha_vantage_key_here"
```

### Local Configuration File (`config.json`)

Located in the project root:

```json
{
  "GEMINI_API_KEY": "",
  "GEMINI_FREE_TIER_API_KEY": "",
  "FINNHUB_API_KEY": "",
  "POLYGON_API_KEY": "",
  "ALPHA_ADVANTAGE_API_KEY": "",
  "MODEL_PRO_1": "gemini-3.7-flash",
  "MODEL_FLASH": "gemini-3.7-flash",
  "MODEL_THINKING": "gemini-3.7-flash",
  "DISABLE_CACHE": false,
  "DEFAULT_TICKERS": ["ONDS", "UMAC", "RCAT", "DFTX", "GLD"],
  "DEFAULT_MACRO_TICKERS": ["^VIX", "VIXY", "IEF", "UUP", "SPY", "GDX"],
  "SCOUT_LIMIT": 3,
  "SCOUT_MAX_RSI": 75,
  "REFRESH_RATE_SECONDS": 30
}
```

| Configuration Key | Purpose |
|-------------------|---------|
| `GEMINI_API_KEY` | Primary API key for PRO / THINKING reasoning routes and JIT caching |
| `GEMINI_FREE_TIER_API_KEY` | Isolated free-tier API key for FAST / UTILITY queries and AI Scout scanner |
| `FINNHUB_API_KEY` | Optional market data quotes provider |
| `POLYGON_API_KEY` | Optional aggregates and equity pricing metadata provider |
| `ALPHA_ADVANTAGE_API_KEY` | Optional macroeconomic and indicator data provider |
| `SCOUT_LIMIT` | Maximum number of scouted tickers displayed simultaneously |
| `SCOUT_MAX_RSI` | Upper RSI ceiling for filtering dynamic breakout candidates |
| `REFRESH_RATE_SECONDS` | Market data polling interval for background daemon (default: 30) |

---

## 🗄️ Data Architecture

- **`context/ssot.json` (Single Source of Truth):** Persistent JSON repository tracking portfolio holdings, share counts, cost basis, unallocated cash, and active watchlists.
- **`context/trade_lessons.json`:** Structured repository of historical post-trade audits. New lessons are appended autonomously via `tools.update_trade_lessons`.
- **`context/decision_log.json`:** Append-only continuous time-series ledger capturing all council debates, trigger contexts, and execution directives.
- **`context/user_config.json`:** Local persistent cache for tracked macro benchmarks and active ticker selections.
- **`context/charts/`:** Offscreen chart screenshot store (`chart_<SYM>_1m.png`) ingested directly by Gemini multimodal vision API requests.

---

'''

    return doc_sections

def update_readme():
    with open("README.md", "r", encoding="utf-8") as f:
        content = f.read()

    changelog_marker = "## 📋 Changelog"
    if changelog_marker not in content:
        raise ValueError("Changelog marker not found in README.md")

    changelog_idx = content.find(changelog_marker)
    existing_changelog = content[changelog_idx:]

    # Clean up any hyperbolic words in the changelog
    replacements = [
        ("premium animated settings popover", "animated settings popover"),
        ("premium-styled \"📰 News Scan\" button", "styled \"📰 News Scan\" button"),
        ("Surgically re-indexed", "Programmatically re-indexed"),
        ("standard, premium Google AI Studio", "standard Google AI Studio"),
        ("Surgically restructured the CSS layout", "Restructured the CSS layout"),
        ("wider, premium `16px` gap", "wider `16px` gap"),
        ("wider, premium 16px gap", "wider 16px gap"),
        ("for perfect visual horizontal alignment", "for visual horizontal alignment"),
        ("Surgically upgraded the fallback validation gate", "Upgraded the fallback validation gate"),
        ("Surgically aligned the main data loading panel visually", "Aligned the main data loading panel visually"),
        ("Surgical Immediate Stop & Cancel propagation", "Immediate Stop & Cancel propagation"),
        ("for premium visual clarity", "for visual clarity"),
        ("Surgically revised sibling element iteration", "Revised sibling element iteration"),
        ("Surgically updated the Master SSoT rules", "Updated the Master SSoT rules"),
        ("Surgically corrected the manual tool response formatting", "Corrected the manual tool response formatting"),
        ("This conforms perfectly with the Google GenAI SDK", "This conforms with the Google GenAI SDK"),
        ("glassmorphic warning card", "warning card"),
        ("premium glassmorphic warning card", "warning card"),
        ("premium warning card", "warning card"),
        ("seamless backend calibration hot-swaps", "automatic backend calibration hot-swaps"),
        ("seamless, hot-reloadable model routing", "hot-reloadable model routing"),
        ("seamlessly support both flat and nested", "support both flat and nested"),
        ("free-tier LLMs (such as `FLASH` and `GEMMA` tiers)", "free-tier LLMs (such as `FLASH` and `FAST` tiers)"),
        ("seamlessly falling back to the primary key", "automatically falling back to the primary key"),
        ("GEMMA routing tier is bypassed", "legacy local model routing tier is bypassed"),
        ("GEMMA tier fallback to Flash", "legacy local model tier fallback to Flash"),
        ("your-org/gemini_cli_subagent_system", "nordicirish/gemini_cli_subagent_system"),
    ]

    clean_changelog = existing_changelog
    for old_val, new_val in replacements:
        clean_changelog = clean_changelog.replace(old_val, new_val)

    # Insert documentation audit update under v11.53-SSR-Proximity-Energy-Sentry-Double-Top-Sync
    target_version_header = "### v11.53-SSR-Proximity-Energy-Sentry-Double-Top-Sync *(2026-09-30)*"
    audit_entry = """- **Architectural Audit & Documentation Overhaul (`README.md`):**
  - *Complete Deprecation of Legacy Local Runtimes:* Removed all legacy references to former local/lightweight runtimes (`gemma-4-31b-it`, local runtimes, fallback tiers) across system title, tagline, architecture diagrams, and sub-agent registries. Formally transitioned all structural, quantitative, and deterministic engines to the active Google Gemini stack (PRO/THINKING and FAST/UTILITY tiers).
  - *API-Driven Multi-Agent Architecture Clarity:* Clarified primary entry point (`python/web_server.py`), CLI orchestrator (`python/main.py`), and Single Source of Truth (`context/ssot.json`). Formally documented architectural decoupling from `gem_trading_agent_system` (direct programmatic tool-calling via Google GenAI SDK, native `/api/chat` streaming, and automated `tools.update_ssot` state ingestion vs. clipboard-bridged Gemini Web UI turns). Updated repository clone URL to `https://github.com/nordicirish/gemini_cli_subagent_system`.
  - *Autonomous Dual-Key Isolation:* Fully documented the dual-key routing mechanism in `agent_framework.py`, partitioning paid/primary keys (`GEMINI_API_KEY`) for Pro/Thinking reasoning and caching from free-tier keys (`GEMINI_FREE_TIER_API_KEY`) for Flash/Flash-lite utility tasks and background AI Scout scanning, backed by automated 429 rate-limit failovers.
  - *Council Personalities & Engine Roster:* Documented comprehensive architectural breakdowns, behavioral mandates, logic filters, and schemas for Bullish Advocate (`bullish_gem.md` — Contrarian Alpha Hunter), Red Team Pessimist (`red_team_gem.md` — Forensic Risk Auditor), Neutral Structuralist (`neutral_gem.md` — Market Architecture & Liquidity Specialist), and Terminal Orchestrator (`terminal.md` — Master Router & Absolute Arbiter). Mapped all 19 sub-agent engines to their active Gemini tiers and concrete system functions.
  - *Platform Feature Suite Documentation:* Fully documented Multimodal Vision & TradingView Lightweight Charts (1m intraday 24h extended sessions, EMA ribbons, VWAP bands, automated offscreen capture `POST /api/save_chart_screenshots`), Trend-Based Fibonacci Forecasting (anchors $P_A, P_B, P_C$, targets $T_1, T_2, T_3$, ATR peak confluence, -0.25% front-running limit orders), Technical Indicator Suite (Wilder RSI-9/14, MACD slope/telemetry, Bollinger Bands, rVol, GEX chevrons), Macro HUD, AI Scout Scanner, Ephemeral JIT Context Caching, and Institutional Governance (`rules.md` Mandates vs. Protocols, Fail-Fast Invariants, Sequential Circuit Breakers, State Machine Idempotency, and Trade Lesson Garbage Collection ENH_53-GC).
  - *Style & Tone Compliance (MANDATE_29):* Systematically purged all hyperbolic and promotional vocabulary across the entire documentation surface, anchoring all descriptions to concrete schemas, endpoints, and data models.
"""

    if target_version_header in clean_changelog:
        header_pos = clean_changelog.find(target_version_header) + len(target_version_header)
        # Check if already inserted
        if "Architectural Audit & Documentation Overhaul" not in clean_changelog:
            clean_changelog = clean_changelog[:header_pos] + "\n" + audit_entry + clean_changelog[header_pos:]

    doc_content = build_readme_content()
    final_readme = doc_content + clean_changelog

    with open("README.md", "w", encoding="utf-8") as f:
        f.write(final_readme)

    print("Successfully updated README.md with comprehensive audit and documentation overhaul.")

if __name__ == "__main__":
    update_readme()
