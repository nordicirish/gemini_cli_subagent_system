# Stage 2 — Shared Runtime Persistence Service and Controlled SQLite Integration Report

**Repository:** `nordicirish/gemini_cli_subagent_system`  
**Branch:** `main`  
**Baseline HEAD Commit:** `d88ca529f33d37c26e323d075175ace0ef8274d2`  
**Stage Type:** Controlled Implementation (Stage 2 Only)  
**Governance Standard:** GEM Agent Governance Framework (`.agents/AGENTS.md`)  
**Git Authority:** Human repository owner exclusively controls all git commits and pushes (no autonomous commits, pushes, or branch changes were performed)

---

## 1. Executive Summary & Mission Scope

Stage 2 establishes a single, shared application-level persistence boundary for GEM so that CLI execution, the Flask web server, and the background stock telemetry/paste handler no longer duplicate direct file-parsing and persistence logic.

**Implemented Architecture:**

```
Application Entry Points (CLI tools, Web API, Background Daemon / Paste Handler)
   └─▶ Shared Application Persistence Boundary (`PersistenceService` in `python/persistence/service.py`)
         └─▶ Persistence Contracts & Exceptions (`PersistenceAdapter` in `python/persistence/contracts.py`)
               ├─▶ `JsonPersistenceAdapter` (DEFAULT active runtime; atomic JSON I/O)
               └─▶ `SqlitePersistenceAdapter` (explicitly selected / test-isolated; reuses `DatabaseManager` & `UnitOfWork`)
```

Key Stage 2 outcomes:
1. **Default Runtime Preserved:** `PersistenceService` defaults to `JsonPersistenceAdapter`, keeping `context/ssot.json`, `context/config.json`, `context/trade_lessons.json`, and `context/decision_log.json` as the active runtime stores with existing atomic JSON I/O guarantees.
2. **Unified Call Sites:** Direct persistence read/write paths in [`python/tools.py`](file:///c:/github/gemini_cli_subagent_system/python/tools.py), [`python/web_server.py`](file:///c:/github/gemini_cli_subagent_system/python/web_server.py), and [`python/fetch_stocks.py`](file:///c:/github/gemini_cli_subagent_system/python/fetch_stocks.py) now route through `get_persistence_service()`. [`python/main.py`](file:///c:/github/gemini_cli_subagent_system/python/main.py) continues to route SSoT and trade-lesson operations through `tools.*`.
3. **Controlled SQLite Integration:** [`SqlitePersistenceAdapter`](file:///c:/github/gemini_cli_subagent_system/python/persistence/sqlite_adapter.py) adapts the shared `PersistenceAdapter` interface onto the existing `DatabaseManager`, `UnitOfWork`, and Stage 1.4/1.5/1.6 repositories without introducing a second repository or transaction framework.
4. **Zero Financial / Operational Side Effects:** No investment rules, portfolio math, risk limits, sizing, or NO TRADE logic were altered. Operational SQLite databases (`context/gem.db`, `cache/market_data.db`) do not exist and were not initialized, migrated, or cut over.

---

## 2. Files Added & Modified

| Action | File Path | Purpose |
|---|---|---|
| Created | [`python/persistence/__init__.py`](file:///c:/github/gemini_cli_subagent_system/python/persistence/__init__.py) | Public persistence API exports and `sys.modules` aliasing (`persistence` <-> `python.persistence`, `tool_registry` <-> `python.tool_registry`). |
| Created | [`python/persistence/contracts.py`](file:///c:/github/gemini_cli_subagent_system/python/persistence/contracts.py) | `PersistenceAdapter` ABC, exception hierarchy, and `CANONICAL_SSOT_KEYS`. |
| Created | [`python/persistence/json_adapter.py`](file:///c:/github/gemini_cli_subagent_system/python/persistence/json_adapter.py) | Default JSON adapter using `atomic_io` helpers with patch-friendly dynamic lookups and format reconciliation. |
| Created | [`python/persistence/sqlite_adapter.py`](file:///c:/github/gemini_cli_subagent_system/python/persistence/sqlite_adapter.py) | Controlled SQLite adapter delegating to `DatabaseManager`, `UnitOfWork`, and `authorize_financial_mutation()`. |
| Created | [`python/persistence/service.py`](file:///c:/github/gemini_cli_subagent_system/python/persistence/service.py) | `PersistenceService` facade, `get_persistence_service()`, `configure_persistence()`, and `reset_persistence_service()`. |
| Modified | [`python/tools.py`](file:///c:/github/gemini_cli_subagent_system/python/tools.py) | Routed `read_ssot`, `update_ssot`, `read_trade_lessons`, `update_trade_lessons`, `read_decision_log`, and `intercept_and_log_decision` through `get_persistence_service()`. |
| Modified | [`python/web_server.py`](file:///c:/github/gemini_cli_subagent_system/python/web_server.py) | Routed chat context reads (`trade_lessons`, `decision_log`), `/api/save_decision_log`, `/api/clear_decision_log`, `/api/basket`, `/api/watchlist`, `trigger_scout`, and `/api/scout_categories` through `get_persistence_service()`. |
| Modified | [`python/fetch_stocks.py`](file:///c:/github/gemini_cli_subagent_system/python/fetch_stocks.py) | Routed `_load_ssot_tickers`, startup scout category reset, `/api/get_basket`, `/api/save_basket`, `/api/get_watch_list`, `/api/save_watch_list`, and `/api/paste` through `get_persistence_service()`, and upgraded `MarketDataCache` disk read/write to `atomic_read_json`/`atomic_write_json`. |
| Created | [`tests/test_persistence_service.py`](file:///c:/github/gemini_cli_subagent_system/tests/test_persistence_service.py) | 17 unit tests covering JSON & SQLite adapters, service configuration/switching, validation, fail-closed financial authorization, and decision/watchlist/scout parity. |
| Created | [`docs/engineering/STAGE_2_SHARED_RUNTIME_PERSISTENCE_SERVICE_REPORT.md`](file:///c:/github/gemini_cli_subagent_system/docs/engineering/STAGE_2_SHARED_RUNTIME_PERSISTENCE_SERVICE_REPORT.md) | This Stage 2 implementation and verification report. |

---

## 3. Caller Replacement Map

| Entry Point / Caller | Domain / Store | Previous Persistence Mechanism | Stage 2 Shared-Service Call Site |
|---|---|---|---|
| `tools.read_ssot` (`python/tools.py:76`) | SSoT JSON | Direct `atomic_read_json("context/ssot.json")` | `get_persistence_service().get_ssot_data()` |
| `tools.update_ssot` (`python/tools.py:110`) | SSoT JSON + embedded lessons | Inline `atomic_update_json` merge + lesson extract | `get_persistence_service().update_ssot_payload(...)` |
| `tools.read_trade_lessons` (`python/tools.py:127`) | Trade lessons | Direct `atomic_read_json("context/trade_lessons.json")` | `get_persistence_service().get_trade_lessons()` |
| `tools.update_trade_lessons` (`python/tools.py:146`) | Trade lessons | Inline `atomic_update_json` | `get_persistence_service().save_trade_lessons(...)` |
| `tools.read_decision_log` (`python/tools.py:158`) | Decision log | Direct `atomic_read_json("context/decision_log.json")` | `get_persistence_service().get_decision_log()` |
| `tools.intercept_and_log_decision` (`python/tools.py:304`) | Decision log | Inline `atomic_update_json` extraction | `get_persistence_service().append_decision_log_from_payload(...)` |
| `web_server` chat context (`python/web_server.py:603,614`) | Trade lessons & decision log | Direct JSON reads | `get_persistence_service().get_trade_lessons()` / `.get_decision_log()` |
| `web_server` `/api/save_decision_log` (`python/web_server.py:874`) | Decision log | Direct `atomic_update_json` | `get_persistence_service().append_decision_log(...)` |
| `web_server` `/api/clear_decision_log` (`python/web_server.py:894`) | Decision log | Direct `atomic_write_json` | `get_persistence_service().clear_decision_log()` |
| `web_server` `/api/basket` (`python/web_server.py:1008,1037`) | Portfolio cash & positions | Inline read/update of `ssot.json` + `config.json` | `get_persistence_service().get_portfolio_basket()` / `.save_portfolio_basket(...)` |
| `web_server` `/api/watchlist` (`python/web_server.py:1079,1117`) | Watchlist | Inline `atomic_read_json` / `atomic_update_json` | `get_persistence_service().get_watchlist()` / `.save_watchlist(...)` |
| `web_server` `trigger_scout` (`python/web_server.py:1166,1186`) | Watchlist & scout categories | Inline JSON file reads | `get_persistence_service().get_watchlist()` / `.get_scout_categories()` |
| `web_server` `/api/scout_categories` (`python/web_server.py:1211,1244`) | Scout categories | Inline JSON file read/write | `get_persistence_service().get_scout_categories()` / `.save_scout_categories(...)` |
| `fetch_stocks._load_ssot_tickers` (`python/fetch_stocks.py:437`) | Active monitored tickers | Direct parsing of `ssot.json` & `config.json` | `get_persistence_service().get_ssot_tickers()` |
| `fetch_stocks` startup scout cleanup (`python/fetch_stocks.py:482`) | Scout categories | Direct `ssot.json` edit | `get_persistence_service().save_scout_categories({})` |
| `fetch_stocks` `/api/get_basket`, `/api/save_basket` (`python/fetch_stocks.py:603,626`) | Portfolio basket | Duplicate `ssot.json` / `config.json` logic | `get_persistence_service().get_portfolio_basket()` / `.save_portfolio_basket(...)` |
| `fetch_stocks` `/api/get_watch_list`, `/api/save_watch_list` (`python/fetch_stocks.py:645,669`) | Watchlist | Duplicate `ssot.json` / `config.json` logic | `get_persistence_service().get_watchlist()` / `.save_watchlist(...)` |
| `fetch_stocks.handle_paste` (`python/fetch_stocks.py:913`) | Watchlist / SCOUT / Portfolio paste updates | Custom multi-file JSON merge | `get_persistence_service().update_ssot_payload(...)` & `.save_watchlist(...)` / `.save_portfolio_basket(...)` / `.save_scout_categories(...)` |
| `fetch_stocks.MarketDataCache` (`python/fetch_stocks.py:1071,1081`) | Ticker historical disk cache | Raw `json.dump` / `json.load` | `atomic_write_json` / `atomic_read_json` (non-SSoT price cache) |

---

## 4. Architecture & Invariant Highlights

### 4.1 Contracts & Module Unification
- [`contracts.py`](file:///c:/github/gemini_cli_subagent_system/python/persistence/contracts.py) defines a narrow `PersistenceAdapter` interface (`get_ssot_data`, `get_portfolio_basket`, `save_portfolio_basket`, `update_ssot_payload`, `get_ssot_tickers`, `reconcile_ssot_format`, `get_watchlist`, `save_watchlist`, `get_scout_categories`, `save_scout_categories`, `get_trade_lessons`, `save_trade_lessons`, `get_decision_log`, `append_decision_log`, `append_decision_log_from_payload`, `clear_decision_log`, `close`) plus structured domain exceptions (`PersistenceError`, `PersistenceValidationError`, `PersistenceAuthorizationError`, `PersistenceNotFoundError`, `PersistenceIntegrityError`).
- When tests or entry points run with both repo root and `python/` on `sys.path`, importing `tool_registry` vs `python.tool_registry` (or `persistence` vs `python.persistence`) would otherwise create separate module objects with separate `_ACTIVE_CALLER_ROLE` ContextVars or service singletons. [`python/persistence/__init__.py`](file:///c:/github/gemini_cli_subagent_system/python/persistence/__init__.py) unifies `python.persistence` <-> `persistence` and `python.tool_registry` <-> `tool_registry` in `sys.modules`, and internal persistence modules use relative imports (`.contracts`, etc.) with `try: from python... except ImportError:` fallbacks.

### 4.2 JSON Adapter Preservation
- [`JsonPersistenceAdapter`](file:///c:/github/gemini_cli_subagent_system/python/persistence/json_adapter.py) retains full backward compatibility with `context/ssot.json`, `context/config.json`, `context/trade_lessons.json`, and `context/decision_log.json`.
- Atomic I/O calls are resolved through thin wrapper functions reading attributes on the imported `atomic_io` module so `unittest.mock.patch("atomic_io.atomic_update_json", ...)` and `patch("atomic_io.atomic_read_json", ...)` remain effective in existing endpoint tests (`tests/test_watchlist_endpoints.py`).
- Basket/watchlist updates preserve `config.json` key parity where expected (`watched_tickers` synchronized between `ssot.json` and `config.json`) without perturbing externally managed SSoT sections.

### 4.3 Controlled SQLite Adapter
- [`SqlitePersistenceAdapter`](file:///c:/github/gemini_cli_subagent_system/python/persistence/sqlite_adapter.py) takes an explicit SQLite DB path or existing `DatabaseManager` and translates shared service operations onto `UnitOfWork` repository methods (`list_positions`, `list_watchlist`, `list_categories`, `list_lessons`, `list_decisions`, `upsert_*`, etc.) inside explicit transactions (`with UnitOfWork(self._db_manager) as uow: ... uow.commit()`).
- Financial and role-gated write methods call `authorize_financial_mutation(...)` up front before opening mutations, mapping `PermissionError` to `PersistenceAuthorizationError`.
- `save_portfolio_basket` explicitly validates non-dict position entries and rejects negative `shares` or negative `wac` with `PersistenceValidationError` before touching the database.
- Runtime backend selection remains opt-in (`GEM_PERSISTENCE_BACKEND=sqlite` or explicit `configure_persistence(...)`); default startup and normal tool execution strictly use `json`.

---

## 5. Verification Performed (Observed Post-Final-Edit Results)

All checks below were executed using `.\.venv\Scripts\python.exe` (Python `3.13.3`) **after the final code edit**.

### 5.1 Static, Package-Import & Environment Checks
| Check | Command | Observed Result |
|---|---|---|
| Bytecode compilation | `.\.venv\Scripts\python.exe -m py_compile python/persistence/__init__.py python/persistence/contracts.py python/persistence/json_adapter.py python/persistence/sqlite_adapter.py python/persistence/service.py python/tools.py python/web_server.py python/fetch_stocks.py tests/test_persistence_service.py` | Exit `0` |
| Dependency consistency | `.\.venv\Scripts\python.exe -m pip check` | `No broken requirements found.` (Exit `0`) |
| Import identity & default backend | `.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'python'); import python.persistence as pp; import persistence as p; assert pp is p; print('backend:', p.get_persistence_service().backend_name)"` | `backend: json` (Exit `0`) |

### 5.2 Automated Test Suites (All 12 Modules in `tests/`)
| Test Command | Tests Run | Passed | Skipped | Failed / Errors | Elapsed |
|---|---|---|---|---|---|
| `.\.venv\Scripts\python.exe -m unittest tests/test_persistence_service.py tests/test_watchlist_endpoints.py tests/test_permissions.py tests/test_tool_dispatch.py tests/test_atomic_io.py tests/test_audit_logger.py tests/test_investment_regression.py` | 71 | 71 | 0 | 0 | 3.273s |
| `.\.venv\Scripts\python.exe -m unittest tests/test_database_core.py` | 120 | 120 | 0 | 0 | 5.429s |
| `.\.venv\Scripts\python.exe -m unittest tests/test_database_repositories.py` | 265 | 265 | 0 | 0 | 18.488s |
| `.\.venv\Scripts\python.exe -m unittest tests/test_database_projections.py` | 54 | 54 | 0 | 0 | 3.066s |
| `.\.venv\Scripts\python.exe -m unittest tests/test_database_migration.py` | 158 | 158 | 0 | 0 | 9.123s |
| `.\.venv\Scripts\python.exe -m unittest tests/test_database_backup_concurrency.py` | 74 | 73 | 1 | 0 | 7.537s |
| **Total Observed Across `tests/`** | **742** | **741** | **1** | **0** | — |

Note: The single skip in `tests/test_database_backup_concurrency.py` reflects an existing platform-conditional test skip on Windows. `tests/test_database_repositories.py` includes `test_state_files_invariance`, confirming state files matched expected hashes throughout test execution.

### 5.3 Operational State & Database Safety Verification
Verified after all test runs completed:
- `context/ssot.json` SHA-256: `0A2BA4D8C125D213C5772B36164637DF74094CA95E28E2CA0AFC2DA5E55414DA` (matches repository baseline)
- `context/config.json` SHA-256: `30EA2B67DD0ECAC4B16E3E726C9644948119729023D382C385301F9DB72099D5` (matches repository baseline)
- `Test-Path context/gem.db`: `False`
- `Test-Path cache/market_data.db`: `False`

---

## 6. Known Boundaries & Handoff

1. **No Operational SQLite Cutover:** In accordance with Stage 2 authorization, no operational database (`context/gem.db` or `cache/market_data.db`) was initialized, migrated, or switched to production active authority.
2. **Non-SSoT Market Cache:** `MarketDataCache` in [`python/fetch_stocks.py`](file:///c:/github/gemini_cli_subagent_system/python/fetch_stocks.py) remains a JSON disk cache updated atomically (`atomic_write_json` / `atomic_read_json`) rather than authoritative financial/application state.
3. **Human Sign-Off:** All Stage 2 changes remain in the working tree on `main` pending independent review and explicit human sign-off before any git commit or push.

