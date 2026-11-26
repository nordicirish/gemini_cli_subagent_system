# GEM Dashboard — Plain-English User Guide

Welcome! This guide explains how to use the dashboard without needing options-desk jargon or developer setup details. Every number and suggestion below comes from local deterministic analytics and optional Google Colab research, and **nothing ever changes your trading rules or portfolio without your click**.

---

## 1. Quick Daily Workflow (1 Minute)

1. Open **`http://127.0.0.1:8000`**.
2. Glance at **Header Status & Market Mood** (`Calm Uptrend`, `Cautious`, or `High Risk`) and the **Macro** pill (warns before major events like CPI, PCE, or FOMC).
3. Read **🧭 Today's Brief & Suggested Next Steps** directly above the table:
   - Shows up to **4 top-priority items** in plain English (active risk alerts, upcoming macro protection windows, SEC filing dilution notices, overheated holdings near intraday peaks, or top Stock Finder ideas).
   - Every card has a one-click button (**Open Exit Plan**, **Ask AI Council**, **Inspect Ideas**, or **Show Setup**).
4. Review your holdings and watchlist in **Simple** view (6 clean columns with plain-English **Signal** badges like `▲ Strong Setup`, `🔥 Overheated`, or `— Steady`). Switch to **Pro (14)** anytime to inspect RSI, VWAP, MACD, rVol, and Options Flow.

---

## 2. Portfolio, Watchlist & Exit Plans

- Click **🧰 TOOLS** (or press `T`) to open the right sidebar:
  - **💼 Portfolio:** Add stock symbols, enter share counts (`Shares`) and average cost in EUR (`Avg Cost`), then click **SYNC**. Unsaved draft rows are protected while editing.
  - **👀 Watchlist:** Track stocks you are considering buying and click **🎯 Scan Watchlist** anytime to force a fresh signal check.
- **One-Click Plans & Charts:**
  - Click any ticker row to open its **1-minute interactive chart** (pre-market, regular hours, post-market, VWAP, and EMAs).
  - Click the **⚡ Plan** pill in the Command Strip (or `⚡` on a held row) to open the **Execution Ticket**: see exact defensive trailing stop levels, profit-taking limit ladder (`T1`, `T2`, `T3`), predicted intraday peak window, and one-click **🔔 Arm** alert buttons.

---

## 3. Finding New Stocks (**🔭 Discovery**)

Inside **🧰 TOOLS → 🔭 Discovery**, there are two ways to look for ideas:

1. **Stock Finder (500+) *(runs via Google Colab Cloud Research)*:**
   - Screens 500+ tickers for strong momentum and low correlation to what you already hold.
   - Click **+ Watch** next to any idea to add it straight to your Watchlist.
2. **Sector Themes *(runs locally)*:**
   - Pick market sectors (e.g., Semiconductors, AI & Data, Biotech), set max RSI and max picks, and click **🔭 Scout New Sectors**.

---

## 4. Portfolio Health & Google Colab Cloud Research (**📊 Quant Lab**)

Inside **🧰 TOOLS → 📊 Quant Lab**:

- **Live Health:** Shows which trading strategies currently fit the market mood, any active risk alerts, watchlist setup readiness, and historical decision backtest hit rate.
- **Cloud Research (Google Colab MCP):**
  - On startup (when `uv` is installed), missing or expired cloud scans run automatically with a live progress bar across **4 steps**:
    1. **⚡ Options Flow** (Multi-expiry volatility surface & dealer hedging)
    2. **📑 Filing Risk** (SEC filing & dilution language scan)
    3. **🔭 Stock Finder (500+)** (Broad quantitative stock screen)
    4. **📊 Strategy Check** (Out-of-sample walk-forward rule validation — **Suggested Rule Tweaks** are strictly read-only until you explicitly approve them)
  - **First-Time Google Colab Browser Connection:** When live scans run via `uvx`, Google Colab opens a browser tab (`colab.research.google.com`) asking to connect the MCP bridge (one tab shared across all 4 steps). Sign in / approve the tab within 60 seconds; if it times out, the banner explains why and offers a **↻ Retry** button.
  - **Offline Fallback:** Open **Offline Colab Calibration (JSON)** inside Cloud Research to export a zero-secret bundle and import results manually without live MCP.

---

## 5. Consulting the AI Council (**💬 AI Council**)

- Click **💬 AI COUNCIL** (or press `C`) to open the Council drawer.
- Use quick-prompt chips (**Update Analysis**, **Audit Portfolio**, **Risk Regime**, **Review Log**) or ask questions in plain English.
- If the Council suggests a portfolio update or rule refinement, it renders a **Proposal Card** that you can inspect, edit, **Apply**, or **Discard** — nothing mutates your portfolio or rules automatically.

---

## 6. Keyboard Shortcuts

- `/` — Focus the table ticker filter
- `T` — Toggle the Tools sidebar
- `C` — Toggle the AI Council chat drawer
- `Esc` — Close open charts, modals, chat drawer, or overlay sidebar
- `Enter` / `Shift + Enter` — Send chat message / insert newline
