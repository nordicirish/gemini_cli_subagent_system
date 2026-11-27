# Changelog

All notable engineering, architecture, UI, and governance changes to `gemini_cli_subagent_system` are documented in this file.

> **Governance Note:** Modifications to trading rules (`gem_trading_rules/rules.md`), Council instructions (`engine_instructions/*.md`), and Single Source of Truth financial records (`context/*`) follow **Protocol 06** and occur strictly with explicit Human Repository Owner approval.

---

## [Unreleased] — Colab MCP Handshake Fix & User Documentation Parity Agent

### Fixed & Improved
- **Unified Colab Sector Themes & Top Recommendations Modal (`python/colab_workers/quant_scout_worker.py`, `python/colab_calibration.py`, `python/colab_jobs.py`, `python/web_server.py`, `static/index.html`, `static/styles.css`, `static/app.js`):**
  - Routed **Discovery → Sector Themes** (`POST /api/ai_scout`) through Colab Stock Finder (`quant_scout` with `options.sectors` and `options.max_rsi`), with automatic local fallback when offline.
  - Added canonical sector tagging (`infer_sector_for_ticker`, S&P 500 GICS parsing + parent sector matching), `rsi_14` scoring, and Top-50 default candidate depth; auto-enriches existing cached artifacts on load.
  - Added **📊 Top 20+ Table** (`#scout-rankings-modal-overlay`) showing top-to-bottom Colab rankings, detailed performance/liquidity/RSI/PEAD columns, Colab cluster & notes, sector filter, show-count selector (`10`/`20`/`30`/`50`/`All`), sorting, search, `+ Watch`, and quick chart buttons (`styles.css?v=18.82`, `app.js?v=18.90`).
- **Stock Finder Equity-Only Filter (`python/colab_workers/quant_scout_worker.py`, `python/colab_calibration.py`):**
  - Excluded volatility products (`VIXY`, `^VIX`, `UVXY`, etc.), market indices (`^GSPC`, etc.), macro/bond/currency/commodity ETFs (`UUP`, `IEF`, `TLT`, `GLD`), and broad/sector ETFs (`SPY`, `QQQ`, `XLV`) from Colab Stock Finder (`quant_scout`) screening and persisted artifact loading.
- **Headless Google Colab CLI Mode (`mode="cli"`, `python/colab_mcp_client.py`, `python/colab_jobs.py`, `static/app.js`):**
  - Added `mode="cli"` powered by `google-colab-cli` (`colab run --config <temp> --timeout <sec> [--gpu T4|L4]`), auto-selected as the default when `colab` is installed (`COLAB_CLI_PATH`, `COLAB_CLI_AUTH`). Eliminates browser-tab handshakes and self-terminates each ephemeral VM cleanly; strictly allowlists `CPU`/`T4`/`L4` to prevent silent A100 fallback.
  - Updated `ColabMCPClient._wrap_script()` via `ast.parse` so `GEM_JOB_INPUTS` is injected *after* module docstrings and `from __future__ import annotations` lines.
  - Updated `PersistentColabSession` to tear down and reset if a `stdio` browser handshake times out at 60s, and updated the transport pill to `Colab: Ready (cli)`.
- **Persistent Colab Session — Connect Once per Server Run (`python/colab_mcp_client.py`, `python/colab_jobs.py`, `static/app.js`):**
  - Added `PersistentColabSession` holding one `colab-mcp` stdio session on a dedicated background event loop; pipelines, retries, and single jobs reuse the same linked Colab browser tab (`COLAB_MCP_PERSISTENT_SESSION`, default on). Crashed or timed-out sessions reset and restart lazily.
  - `/api/colab/status` now reports `"session"` (`alive`, `browser_linked`); the MCP pill shows **Colab linked ✓**.
- **Startup Auto-Run Now Opt-In (`COLAB_AUTO_RUN_ON_STARTUP`, default `0`):** No surprise browser tab or 60s countdown at boot; run via **▶ Refresh All Cloud Insights** or set `1`.
- **Google Colab MCP Handshake & Root-Cause Diagnostics (`python/colab_mcp_client.py`, `python/colab_jobs.py`):**
  - Implemented `open_colab_browser_connection` handshake inside `ColabMCPClient._run_on_session()` so `googlecolab/colab-mcp` automatically opens the browser bridge tab and unlocks execution tools cleanly.
  - Fixed `mcp==2.3.0` `Tool.input_schema` attribute compatibility via `_tool_input_schema()`.
  - Added recursive `unwrap_exception_message()` so `ExceptionGroup` / `TaskGroup` errors surface concrete root-cause guidance instead of opaque TaskGroup strings.
  - Added `execute_scripts_batch_sync()` so multi-job pipelines share one Colab browser connection session instead of spawning four tabs, plus an in-process `python/colab_hook/sitecustomize.py` `webbrowser` hook (loaded via `PYTHONPATH`) capturing ephemeral `colab.research.google.com` proxy URLs instantly — avoiding nested `BROWSER` subprocesses that stall inside the Windows MCP stdio Job Object.
- **Cloud Research Connect, Error & Retry Banner (`static/index.html`, `static/styles.css`, `static/app.js`):**
  - Added allowlisted **🔗 Open Google Colab to Connect** banner link (`#colab-connect-link`), red/amber `.is-failed` / `.is-partial` visual states, inline `#colab-pipeline-error-detail` messages, `↻ Retry` (`#colab-banner-retry-btn`), and `×` dismiss (`#colab-banner-dismiss-btn`).
- **User Documentation Parity Agent & Gate (`docs/agents/documentation-agent.md`, `docs/agents/roles.json`, `docs/USER_GUIDE.md`, `scripts/stage_gate.py`):**
  - Expanded `documentation-subagent` to own plain-English user docs (`README.md`, `CHANGELOG.md`, `docs/USER_GUIDE.md`).
  - Added `docs/USER_GUIDE.md` trader handbook and automated `check_user_docs_parity()` in `scripts/stage_gate.py`.

---

## [v19.0] — Startup Colab Auto-Pipeline & Plain-English "Inform & Suggest" UX (`1e8d0a7`)

### Added
- **Startup Auto-Pipeline (`python/colab_jobs.py`, `python/web_server.py`):** Added `ColabJobManager.run_pipeline()`, `maybe_start_startup_pipeline()`, `COLAB_AUTO_RUN_ON_STARTUP`, `POST /api/colab/jobs/run_all`, and live `"pipeline"` progress in `/api/colab/status`.
- **Today's Brief & Suggested Next Steps (`#action-brief-card`):** Added prioritized 1-click guidance card summarizing Risk Alerts, Macro Shields, SEC Filing Risks, Stretched Peaks, and Stock Finder ideas.
- **Simple / Pro (14) Table Toggle & Plain-English Labels:** Added `.view-simple` toggle, `.signal-plain-pill` verdicts, inline `uv` setup box, and `.inline-feedback.active` visibility fix.

---

## [v18.9] — UI Streamlining & Standalone Changelog (`ef67235`)

### Added
- **`CHANGELOG.md`:** Extracted project version history out of `README.md` into a standalone chronological changelog.
- **Unified Sidebar Navigation (`static/index.html`, `static/styles.css`, `static/app.js`):**
  - **Watchlist (`#watchlist-content`):** Relocated Watchlist Rescan (`#scan-watchlist-btn`, `#scan-watchlist-status`) directly beneath the watchlist ticker manager inside `.watchlist-rescan-row`.
  - **`🔭 Discovery` (`#scout-content`):** Consolidated quantitative screening and sector scouting into a single card with a 2-tab segmented switcher: **Quant Screen (500+)** (default, `#colab-run-quant-scout-btn`, `#colab-scout-job-status`, `#colab-quant-scout-panel`) and **Sector AI** (`#scout-categories-container`, `#run-ai-scout-btn`).
  - **`📊 Quant Lab` (`#analytics-quant-content`):** Consolidated live quantitative analytics and Colab MCP GPU workflows into a single card with a 2-tab segmented switcher: **Live Quant** (default, `#analytics-quant-panel`, `#refresh-analytics-btn`) and **Colab MCP Lab** (Vol Surface, SEC NLP + optional L4 7B toggle, Walk-Forward Backtest, Protocol 06 Read-Only Evidence Pack, and collapsible `<details>` Offline Colab Calibration JSON controls).
  - **`⚙️ Admin` (`#admin-content`):** Slimmed down strictly to destructive Decision Log maintenance (`#clear-log-btn`).
- **Tests (`tests/js/dashboard_load.test.js`):** Added JSDOM checks covering Watchlist Rescan placement, Discovery & Quant Lab tab switching (`aria-selected` and `.hidden` state), and slim Admin card boundaries.

### Changed
- **Documentation & Governance Parity:** Streamlined `README.md` (~300 lines), refreshed `docs/governance/project-state.md` to match current `main` baseline, and recorded Colab MCP zero-secret egress / security scanner pitfalls in `.agents/AGENTS.md`.
- **Static Cache Busting (`static/index.html`):** Bumped `styles.css?v=18.78` and `app.js?v=18.84`.

---

## [v18.4] — Colab MCP Walk-Forward Backtester, Read-Only Evidence & Council Signals (Stage M4 — `a99a257`)

### Added
- **Worker #2 (`python/colab_workers/walk_forward_backtester.py`):** Implemented GPU/vectorized out-of-sample walk-forward parameter evaluation emitting Schema v2.0 `backtest_evidence` artifacts with OOS Sharpe, drawdown, stability scores, and proposed parameter deltas.
- **Read-Only Council Tool (`python/tools.py`, `python/agent_factory.py`):** Added strictly read-only `read_colab_quant_signals()` tool returning freshest Vol Surface, Quant Scout, SEC NLP, and Backtest Evidence summaries (with explicit staleness annotations) to Council prompts.
- **Protocol 06 Evidence Pack UI (`static/index.html`, `static/app.js`, `static/styles.css`):** Added read-only Protocol 06 evidence summary card with `📋 Copy Proposal` clipboard export; strictly never mutates `gem_trading_rules/rules.md`.

---

## [v18.3] — Zero-Token GPU SEC/Earnings NLP Worker & Dashboard Badges (Stage M3 — `3f2b41f`)

### Added
- **Worker #3 (`python/colab_workers/sec_nlp_analyzer.py`):** Added Colab worker scoring SEC filings and dilution risk (`dilution_risk_score`, `catalyst_sentiment`, `offering_overhang_flag`, `going_concern_flag`) with FinBERT and opt-in L4 7B 4-bit refinement (`use_llm`).
- **SEC & Analytics Enrichment (`python/sec_edgar.py`, `python/analytics_engine.py`):** Integrated persisted `colab_sec_nlp` artifacts cleanly into ticker/analytics payloads without blocking loopback polling.
- **UI Badges (`static/app.js`, `static/styles.css`):** Added escaped `.sec-nlp-badge` indicators with stale/risk styling and `Use L4 7B LLM for SEC NLP` toggle.

---

## [v18.2] — Multi-Expiry Vol Surface & 500+ Ticker Quant Scout Workers (Stage M2 — `fb9e048`)

### Added
- **Worker #4 (`python/colab_workers/vol_surface_greeks.py`):** Computes multi-expiry implied volatility surfaces, 2nd-order Greeks (Vanna/Charm), and dealer flow regime tags (`VANNA_TAILWIND`, `CHARM_PIN`).
- **Worker #5 (`python/colab_workers/quant_scout_screener.py`):** Screens a 500+ ticker quantitative universe on Colab compute and returns ranked breakout/momentum candidates (`quant_scout`).
- **UI & Analytics Wiring:** Enriched `python/analytics_engine.py` with Vol Surface regime badges (`V+`/`V-`) and added XSS-safe Quant Scout candidate rows with one-click `+ Watch` watchlist promotion.

---

## [v18.1] — Dual-Mode Google Colab MCP Client & Async Job Runner (Stage M1 — `895e253`)

### Added
- **Dual-Mode MCP Client (`python/colab_mcp_client.py`):** Added `mcp==2.3.0` client supporting local stdio (`uvx googlecolab/colab-mcp`), hosted HTTP (`https://colaboratory.googleapis.com/mcp` with `COLAB_MCP_BEARER_TOKEN`), and strict stdout JSON sentinel parsing (`<<<GEM_COLAB_JSON_START>>>` .. `<<<GEM_COLAB_JSON_END>>>`).
- **Single-Flight Job Runner (`python/colab_jobs.py`):** Added background async job orchestration enforcing zero-secret outbound bundle generation (`export_colab_bundle`), single-flight concurrency locks, and fail-closed schema validation (`python/colab_calibration.py`).
- **FastAPI Endpoints (`python/web_server.py`):** Added `/api/colab/status`, `/api/colab/jobs/{job_type}`, `/api/colab/jobs/{job_id}`, `/api/colab/artifacts/{artifact_type}`, and `/api/colab/artifacts/{artifact_type}/import`.

---

## [v17.5] — Protocol 06 Consolidation & Architecture Overhaul Stages A–D (`a048e6f` – `1f7d48c`)

### Changed
- **Protocol 06 Class A/B Consolidation (`1f7d48c`):** Audited and consolidated overlapping Council directives across `gem_trading_rules/rules.md` and `engine_instructions/*.md` under explicit Human Owner sign-off, improving prompt budget compliance under the 90 KB `agy.exe` stdin ceiling.
- **Stage D + Offline Colab Calibration (`46027a6`):** Added schema-validated offline Colab JSON calibration export/import (`/api/calibration/export`, `/api/calibration/import`, `/api/calibration/status`) and split monolithic helpers cleanly into focused modules.
- **Stage C (`e184d96`):** Unified CSS design tokens across `static/styles.css` and `static/modern_ui.css`, removed inline `<style>`/`<script>` blocks from `static/index.html`, and added table live ticker filter (`/`), per-section sorting, compact density mode, and keyboard shortcuts (`t`, `c`, `Esc`).
- **Stage B (`8f409bb`):** Modernized `python/agent_factory.py` and `python/tool_dispatcher.py` for parallel read-tool dispatch, bounded market data caching (`python/fetch_stocks.py`), and atomic I/O (`python/atomic_io.py`).
- **Stage A (`a048e6f`):** Resolved P0 prompt token bloat, duplicate daemon/TTL bugs, Command Strip EUR cash hydration (`#cash-input-eur`), FX-missing portfolio draft guards, and watchlist save feedback regressions.

---

## Historical Summary (Pre-v17)

- **v11.55–v16.x — Deterministic Quant Engines, Alerts, Review & Governance Hardening:**
  - Transitioned runtime execution to the local **Antigravity App Agent (`agy.exe`)** runtime (`python/agent_framework.py`), decoupling external paid cloud fallbacks while retaining optional free-tier Sentinel polling (`python/alert_sentinel.py`).
  - Added SQLite + `PersistenceService` (`python/persistence/`, `python/database/`) with strict `Decimal` (28-digit) accounting contracts, monotonic schema revisions, deterministic SSoT projection into `context/ssot.json`, and HTTP 422 validation guards (`shares > 0, wac == 0`).
  - Implemented deterministic local engines: `python/analytics_engine.py`, `python/peak_predictor.py` (60-day intraday peak timing & exhaustion zones), `python/sec_edgar.py` (72h dilution/shelf filing radar), `python/macro_calendar.py` (`ENH_259` pre-event shield), `python/merton_optimizer.py`, `python/decision_backtester.py`, and `python/alert_monitor.py`.
  - Added owner-gated proposal workflows (`python/portfolio_proposals.py`, `python/improvement_proposals.py`) and read-only `/api/review` log auditing (`scripts/backfill_decision_log.py`).
  - Established `.agents/` governance framework (Protocols 01–06), role manifests (`docs/agents/roles.json` + `scripts/sync_agent_manifests.py`), security scanner (`scripts/audit_security.py`), executable gate (`scripts/stage_gate.py`), and Stop hook verification (`scripts/hook_stop_verify.py`).
- **v11.0–v11.54 — Foundation & TradingView Charting Suite:**
  - Built FastAPI dashboard (`python/web_server.py`) and dual-coordinator market daemon (`python/fetch_stocks.py`) with 1-minute TradingView Lightweight Charts (24h extended hours, EMA 9/30/200 warmup, VWAP envelope bands), Fibonacci expansion & ATR peak targets, Wilder RSI-9/14, MACD, Bollinger %B, rVol, GEX modeling, and legislative rule registries (`MANDATE_*` / `ENH_*`).
