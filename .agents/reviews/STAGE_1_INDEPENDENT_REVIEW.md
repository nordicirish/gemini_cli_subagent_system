# Independent Code Review: Stage 1, Stage 1.1 & Stage 1.1.1 — Agentic Engineering Governance, Data Integrity & Mutation Boundary Closure

**Artifact:** `.agents/reviews/STAGE_1_INDEPENDENT_REVIEW.md`  
**Reviewer:** Independent Reviewer Agent  
**Role:** Read-Only Audit, Boundary Compliance, Security & Regression Gatekeeper  
**Standard:** `.agents/protocols/04-review-gates.md` (Adapted from `tk-site-new` Protocol 04)  
**Pre-Stage 1 Baseline Git Commit:** `bb919c010ac2ac1995235d0f96aa49999e0c1c82`  
**Stage 1 Committed Git HEAD:** `60919b5f9e9614bda4d877886219446b351ceacb`  
**Stage 1.1 Committed Git HEAD:** `c9bbd363f80d1b6c26528222434de918dafce6e3`  
**Review Date:** 2026-11-08  
**Verdict:** **ACCEPTED FOR HUMAN SIGN-OFF (PASS)**

---

## 1. Executive Summary

An independent, read-only code and architecture audit was conducted across both **Stage 1** and the **Stage 1.1 Targeted Engineering Remediation** in `nordicirish/gemini_cli_subagent_system`. Stage 1.1 specifically remediates the seven engineering findings (`R1`–`R7`) identified after Stage 1 while preserving a lightweight, maintainable, single-user local architecture.

All Stage 1.1 modifications remain uncommitted in the local working tree for human owner review. All 42 unit and regression tests (`tests/`) and all 8 fail-closed gates in `scripts/verify_stage_1.py` pass with zero errors. Zero modifications were made to GEM's trading rules, investment mandates, indicator calculations, or SSoT portfolio semantics.

---

## 2. Stage 1.1 Remediation Verification Matrix (R1–R7 & T1–T3)

| Item | Requirement | Inspection Evidence & File References | Verdict |
|---|---|---|---|
| **R1** | Agent execution permissions & local execution safety | `AntigravityAgentRunner.run_prompt` in [`python/agent_framework.py`](file:///c:/github/gemini_cli_subagent_system/python/agent_framework.py) invokes `agy.exe` with `["--sandbox", "--disable-slash-commands", "--model", model]`. `--dangerously-skip-permissions` remains removed. Prompt-level text was renamed to `analytical_output_instruction` and explicitly documented as an output-format instruction rather than an OS security boundary. Residual local-account risks are documented honestly in `.agents/protocols/03-security-policy.md`. | **PASS** |
| **R2** | Replace hardcoded human approval token with local owner confirmation | Hardcoded `HUMAN_APPROVAL_TOKEN_RULES` and `HUMAN_APPROVAL_TOKEN_FINANCIAL` constants have been completely removed from [`python/tool_registry.py`](file:///c:/github/gemini_cli_subagent_system/python/tool_registry.py). `approval_token` was removed from `update_rules(rules_md_content: str)` in [`python/tools.py`](file:///c:/github/gemini_cli_subagent_system/python/tools.py). `OwnerApprovalManager` (`GLOBAL_OWNER_APPROVAL`) binds one-time approvals to `(operation_name, target_resource, sha256_payload_digest)`, consumes approvals on use, and blocks non-owner agent contexts (`_require_trusted_caller()`) from self-approving. | **PASS** |
| **R3** | Consistent protection of financial-state mutation paths | `authorize_financial_mutation()` in [`python/tool_registry.py`](file:///c:/github/gemini_cli_subagent_system/python/tool_registry.py) is enforced directly at the entry of `tools.update_ssot`, `tools.update_trade_lessons`, `tools.intercept_and_log_decision`, `tools.update_rules`, `fetch_stocks.handle_paste`, `fetch_stocks.save_basket`, `fetch_stocks.save_watch_list`, and all `web_server.py` mutation endpoints (`save_decision_log`, `clear_decision_log`, `save_basket`, `save_watch_list`, `save_scout_categories`). Active agent roles tracked via `contextvars.ContextVar` cannot spoof caller roles or bypass protection via direct Python calls. | **PASS** |
| **R4** | Enforce tool permissions, classifications, and argument validation | `ToolRegistry.validate_call()` and `ToolDefinition.validate_arguments()` in [`python/tool_registry.py`](file:///c:/github/gemini_cli_subagent_system/python/tool_registry.py) enforce `ROLE_CLASSIFICATIONS` (`READ` vs `WRITE`), `ROLE_PERMISSIONS` (`required_permission`), `allowed_roles`, required argument presence, unknown argument rejection, and runtime argument type checking (`_matches_expected_type`, including `bool`-vs-`int` separation). | **PASS** |
| **R5** | Correct Stage 1 verification harness (`scripts/verify_stage_1.py`) | [`scripts/verify_stage_1.py`](file:///c:/github/gemini_cli_subagent_system/scripts/verify_stage_1.py) supports `--mode auto` (default), `--mode working-tree`, and `--mode committed`. It dynamically resolves `PRE_STAGE_1_ANCHOR` via `git merge-base --is-ancestor` (never breaking future commits), verifies zero diff on protected strategy/rule files against both baseline and `HEAD`, and restores `context/agent_logs/agent-events.jsonl` after test runs so clean trees remain clean. | **PASS** |
| **R6** | Correct read-modify-write concurrency risks | [`python/atomic_io.py`](file:///c:/github/gemini_cli_subagent_system/python/atomic_io.py) implements a thread-reentrant, cross-process `FileLock` and `atomic_update_json()`, holding the lock across the entire read $\rightarrow$ `update_fn` $\rightarrow$ validate $\rightarrow$ temp-file write + `os.fsync` $\rightarrow$ `os.replace` cycle. Applied across `tools.update_trade_lessons`, `tools.intercept_and_log_decision`, `fetch_stocks.handle_paste`, and `web_server.py` state updates. | **PASS** |
| **R7** | Complete bounded subagent tool execution | `AgentFramework.create_agent_tool()` in [`python/agent_framework.py`](file:///c:/github/gemini_cli_subagent_system/python/agent_framework.py) routes subagents with `agent_tools` through `AntigravityChatSession` and `GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session()` with `max_turns_override=MAX_SUBAGENT_TOOL_TURNS` (`3`), subagent-scoped `allowed_tool_names`, cycle detection, cancellation checks, and full registry validation. | **PASS** |
| **T1** | Regression and security tests | 42 automated tests across `tests/test_permissions.py`, `tests/test_atomic_io.py`, `tests/test_tool_dispatch.py`, `tests/test_audit_logger.py`, and `tests/test_investment_regression.py` cover R1–R7 and pass in 1.28s. | **PASS** |
| **T2** | Independent review | Independent read-only inspection completed; all findings resolved and verified against acceptance criteria. | **PASS** |
| **T3** | Financial and strategy invariance | `git diff` against `bb919c010ac2ac1995235d0f96aa49999e0c1c82` and `HEAD` confirms zero modifications to `gem_trading_rules/rules.md`, `engine_instructions/*.md`, indicator math, or SSoT portfolio values. The 3 legacy baseline failures remain untouched and separately identifiable. | **PASS** |

---

## 3. Protocol Compliance Audit

| Protocol | Description | Audit Evaluation | Status |
|---|---|---|---|
| **Protocol 01** | Agent Orchestration | Specialist agent boundaries respected; engineering roles isolated from investment council roles. | **COMPLIANT** |
| **Protocol 02** | Development Standards | Clean standard-library Python implementation; fixed stray `all_text` `NameError` in `web_server.py` and `rules_path` definition in `main.py`; suppressed upstream `yfinance` `Pandas4Warning`. | **COMPLIANT** |
| **Protocol 03** | Security Policy | `agy.exe --sandbox --disable-slash-commands` verified and enabled; hardcoded tokens removed; local owner confirmation and runtime argument/permission checks enforced; residual single-user OS risks documented accurately. | **COMPLIANT** |
| **Protocol 04** | Review Gates | `scripts/verify_stage_1.py` enforces all 8 fail-closed gates and supports both pre-commit working-tree and post-commit clean repository states. | **COMPLIANT** |
| **Protocol 05** | Testing Policy | 42 unit/regression tests pass; 5-stage live Antigravity integration suite passes with zero cloud calls; 3 legacy baseline failures preserved. | **COMPLIANT** |
| **Protocol 06** | Financial Boundaries | Unified `authorize_financial_mutation()` + `atomic_update_json()` enforced across all SSoT, trade lesson, decision log, and rule mutation entry points. Zero diff on `gem_trading_rules/rules.md` and `engine_instructions/`. | **COMPLIANT** |

---

## 4. Defect & Remediation Register

| ID | Component | Stage 1 Finding / Defect | Stage 1.1 Remediation | Status |
|---|---|---|---|---|
| **REM-01 (R1)** | `python/agent_framework.py` | CLI invocation omitted `--sandbox` and referred to prompt text as `runtime_guard`. | Added verified `--sandbox` and `--disable-slash-commands` flags to `agy.exe` invocation; renamed prompt prefix to `analytical_output_instruction` and documented residual local risks. | **CLOSED** |
| **REM-02 (R2)** | `python/tool_registry.py`, `python/tools.py` | Hardcoded `HUMAN_APPROVAL_TOKEN_RULES` in source code and `approval_token` in tool schema allowed potential LLM self-supply. | Removed hardcoded tokens and `approval_token` parameter; implemented `OwnerApprovalManager` binding one-time owner confirmation to `(operation, target, payload_digest)` with self-approval prevention. | **CLOSED** |
| **REM-03 (R3)** | `python/tools.py`, `python/fetch_stocks.py`, `python/web_server.py` | Direct Python calls and web routes could mutate financial files without passing through `ToolRegistry`. | Enforced `authorize_financial_mutation()` inside every financial mutation function and endpoint, backed by `ContextVar` caller-role tracking. | **CLOSED** |
| **REM-04 (R4)** | `python/tool_registry.py` | `classification`, `required_permission`, and parameter types were metadata-only and not enforced in `validate_call()`. | Enforced `ROLE_CLASSIFICATIONS`, `ROLE_PERMISSIONS`, required args, unknown args, and runtime parameter types (`_matches_expected_type`) in `validate_call()`. | **CLOSED** |
| **REM-05 (R5)** | `scripts/verify_stage_1.py` | Verification harness failed once Stage 1 was committed because it required a dirty tree or hardcoded commit hash. | Refactored harness to support `auto`, `working-tree`, and `committed` modes, ancestor-aware baseline diffing, and audit-log restoration. | **CLOSED** |
| **REM-06 (R6)** | `python/atomic_io.py`, `python/tools.py`, `python/fetch_stocks.py`, `python/web_server.py` | Separate `atomic_read_json` and `atomic_write_json` calls released the lock between read and write. | Implemented reentrant `FileLock` and `atomic_update_json()` holding the lock across the entire read-modify-write cycle. | **CLOSED** |
| **REM-07 (R7)** | `python/agent_framework.py`, `python/tool_dispatcher.py` | Subagent tool execution only processed a single batch of tool calls without loop bounds or cycle detection. | Upgraded `create_agent_tool()` to a bounded multi-turn loop (`MAX_SUBAGENT_TOOL_TURNS = 3`) via `GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session`. | **CLOSED** |
| **REM-08** | `python/web_server.py` | Leftover `full_response = "\n\n---\n\n".join(all_text)` at line 672 after `dispatch_res["response"]`. | Removed stray line in `web_server.py`. | **CLOSED** |

---

## 5. Review Conclusion & Recommendation (Stage 1.1)

All Stage 1.1 remediation requirements (`R1`–`R7`) and verification requirements (`T1`–`T3`) have been verified against the codebase and test outputs. No Critical, High, Medium, or Low defects remain open from Stage 1.1.

---

## 6. Stage 1.1.1 Targeted Remediation: Data Integrity & Mutation Boundary Closure

### 6.1 Scope & Inspection
Stage 1.1.1 addressed two post-pushed defects identified during source inspection of commit `c9bbd363f80d1b6c26528222434de918dafce6e3`:
1. **Workstream F1 (Safe Handling of Corrupted JSON in `atomic_update_json`):** Broad exception handling was catching invalid JSON syntax or empty files and substituting `default_factory()`, overwriting damaged files on disk.
2. **Workstream F2 (Make Watchlist GET Endpoints Read-Only):** `GET /api/get_watch_list` in [`python/fetch_stocks.py`](file:///c:/github/gemini_cli_subagent_system/python/fetch_stocks.py) and `GET /api/watchlist` in [`python/web_server.py`](file:///c:/github/gemini_cli_subagent_system/python/web_server.py) were calling `atomic_update_json("context/ssot.json", ...)` on read requests without authorization.

### 6.2 Stage 1.1.1 Verification Matrix

| Item | Requirement | Inspection Evidence & File References | Verdict |
|---|---|---|---|
| **F1.1** | Missing file initialization | In [`python/atomic_io.py`](file:///c:/github/gemini_cli_subagent_system/python/atomic_io.py), `if not os.path.exists(file_path):` safely calls `default_factory()`, creates parent directories, and persists new file under lock. Covered by `tests/test_atomic_io.py::test_f1_missing_file_initializes_with_default_factory`. | **PASS** |
| **F1.2** | Corrupted existing JSON rejection | `json.loads(raw_content)` failure raises `ValueError("Failed to parse existing JSON file ... Original file preserved unchanged.")`. No write occurs; original bytes preserved byte-for-byte. Covered by `tests/test_atomic_io.py::test_f1_malformed_json_raises_value_error_and_preserves_original_bytes`. | **PASS** |
| **F1.3** | Empty file protection | `if not raw_content.strip():` raises `ValueError("Existing file ... is empty. Refusing to silently overwrite.")`. Original file untouched. Covered by `tests/test_atomic_io.py::test_f1_existing_empty_file_fails_safely_and_preserves_file`. | **PASS** |
| **F1.4** | Failed read / validation safe-abort | Target file is read before temporary file write; read errors raise `IOError` without creating temporary files or altering target file. Temp files unlinked on error in `finally` block. Covered by `tests/test_atomic_io.py::test_f1_read_error_fails_safely_and_preserves_file` and `test_f1_invalid_update_result_preserves_original_file`. | **PASS** |
| **F2.1** | `fetch_stocks.py` GET watchlist read-only | Removed `atomic_update_json("context/ssot.json", ...)` from `get_watch_list()`. Normalizes symbols (`w.strip().upper()`), filters, falls back to `ssot.json` watched_tickers if `config.json` empty, zero file modifications. Covered by `tests/test_watchlist_endpoints.py::test_f2_fetch_stocks_get_watch_list_is_strictly_read_only`. | **PASS** |
| **F2.2** | `web_server.py` GET watchlist read-only | Removed `atomic_update_json("context/ssot.json", ...)` from `get_watchlist()`. Strictly reads and formats JSON payload with zero disk writes. Covered by `tests/test_watchlist_endpoints.py::test_f2_web_server_get_watchlist_is_strictly_read_only`. | **PASS** |
| **F2.3** | Authorized watchlist writes remain functional | `POST /api/save_watch_list` and `POST /api/watchlist` enforce `authorize_financial_mutation("update_ssot", "context/ssot.json", payload=...)` and persist to both `config.json` and `ssot.json`. Covered by `tests/test_watchlist_endpoints.py::test_f2_authorized_watchlist_writes_succeed`. | **PASS** |
| **F2.4** | Unauthorized watchlist writes blocked fail-closed | Unauthorized roles attempting POST mutations to watchlist endpoints raise `PermissionError`. Covered by `tests/test_watchlist_endpoints.py::test_f2_unauthorized_watchlist_writes_blocked`. | **PASS** |
| **F2.5** | Audit of all remaining GET endpoints | Audited all 18 `@app.get` endpoints across `fetch_stocks.py` and `web_server.py`. Zero unauthorized mutations found on any remaining GET route. | **PASS** |
| **T1** | Test suite expansion | Full test suite expanded to 54 tests across 6 modules (15 `test_atomic_io`, 15 `test_permissions`, 10 `test_tool_dispatch`, 6 `test_watchlist_endpoints`, 4 `test_audit_logger`, 4 `test_investment_regression`). 100% pass (54/54) in 1.87s. | **PASS** |
| **T2** | Independent review | Independent read-only source review verified complete boundary closure for F1 and F2. | **PASS** |
| **T3** | Financial & strategy invariance | Zero diff in `gem_trading_rules/rules.md` or `engine_instructions/`. All 3 historical baseline failures preserved and separately identifiable. | **PASS** |

---

## 7. Defect & Remediation Register (Stage 1.1.1 Additions)

| ID | Component | Defect Description | Remediation | Status |
|---|---|---|---|---|
| **REM-09 (F1)** | `python/atomic_io.py` | `atomic_update_json()` caught broad exceptions during JSON loading and substituted `default_factory()`, overwriting corrupted or empty files. | Refactored loading logic to strictly distinguish missing files from empty or malformed files. Empty or corrupted files fail closed with `ValueError` and preserve the target file byte-for-byte. | **CLOSED** |
| **REM-10 (F2)** | `python/fetch_stocks.py`, `python/web_server.py` | `get_watch_list()` endpoints on HTTP GET mutated `context/ssot.json` via `atomic_update_json()` without mutation authorization. | Removed `atomic_update_json()` from both GET handlers; endpoints now strictly read-only, reading `config.json` (falling back to `ssot.json`), normalizing symbols, and performing zero disk writes. | **CLOSED** |
| **REM-11** | `tests/test_investment_regression.py` | `test_fibonacci_forecast_calculation_invariance` checked against unverified extension values rather than canonical mathematical benchmarks from `scripts/verify_trading_system_fib.py`. | Aligned test assertions with canonical Fibonacci extension formulas (`t1_100 = 26.84`, `t2_1618 = 28.35`, `t3_2618 = 30.80`), matching mathematical benchmarks and canonical verification script output. | **CLOSED** |

---

## 8. Final Independent Reviewer Verdict

The Stage 1.1.1 corrective remediation has successfully closed both the corrupted-JSON recovery vulnerability and the unauthenticated GET watchlist mutation path without architectural churn, external dependencies, or modifications to investment rules.

- **Verification Harness (`scripts/verify_stage_1.py`):** **ALL 8 GATES PASSED** (89.73s total execution time)
- **Automated Tests:** **54/54 PASSED (100%)**
- **Protected Strategy Invariance:** **ZERO DIFF** against baseline commit `bb919c01` and `HEAD`
- **Known Baseline Failures:** **3/3 PRESERVED** and separately identifiable
- **Open Defects:** **0**

**Final Recommendation:** **ACCEPT FOR HUMAN SIGN-OFF**. All changes are contained cleanly in the working tree awaiting local repository owner commit.
