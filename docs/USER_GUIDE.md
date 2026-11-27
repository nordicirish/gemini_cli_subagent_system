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

Inside **🧰 TOOLS → 🔭 Discovery**, both tabs are powered by the same **Google Colab Stock Finder** engine:

1. **Stock Finder (500+):**
   - Screens 500+ individual stocks (indices, VIX products, and macro ETFs are filtered out) for strong momentum and low correlation to what you already hold.
   - Click **📊 Top 20+ Table** to open the full ranked modal: inspect Top 10 / 20 / 30 / 50 / All picks from top to bottom with 1M/3M/6M returns, RSI, portfolio correlation, PEAD, liquidity, Colab cluster tags, and notes — filter by sector or search, and click **+ Watch** on any row.
2. **Sector Themes:**
   - Pick market sectors (e.g., Semiconductors, AI & Data, Biotech), set max RSI and max picks, and click **🔭 Scout New Sectors**.
   - Runs a targeted Google Colab scan for those sectors and automatically opens the ranked recommendations modal (falling back to local scanning only when Colab is offline).

---

## 4. Portfolio Health & Google Colab Cloud Research (**📊 Quant Lab**)

Inside **🧰 TOOLS → 📊 Quant Lab**:

- **Live Health:** Shows which trading strategies currently fit the market mood, any active risk alerts, watchlist setup readiness, and historical decision backtest hit rate.
- **Cloud Research (Google Colab MCP):**
  - Click **▶ Refresh All Cloud Insights** to run cloud research with a live progress bar across **4 steps** (it no longer starts by itself when the server starts — set `COLAB_AUTO_RUN_ON_STARTUP=1` if you want that back):
    1. **⚡ Options Flow** (Multi-expiry volatility surface & dealer hedging)
    2. **📑 Filing Risk** (SEC filing & dilution language scan)
    3. **🔭 Stock Finder (500+)** (Broad quantitative stock screen)
    4. **📊 Strategy Check** (Out-of-sample walk-forward rule validation — **Suggested Rule Tweaks** are strictly read-only until you explicitly approve them)
  - **Headless Colab CLI Connection (no browser tab needed):** When the Google Colab CLI is installed and signed in once in your terminal (`colab sessions`), cloud research runs directly from the dashboard with **no browser pop-up or 60-second timer**, and each cloud machine turns itself off automatically when its step finishes (look for **Colab: Ready (cli)** in the Cloud Research header). If you run in browser-bridge mode (`stdio`) instead, click the green **🔗 Open Google Colab to Connect** button in the banner when prompted.
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
