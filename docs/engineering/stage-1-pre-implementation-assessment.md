# STAGE 1 — PRE-IMPLEMENTATION ASSESSMENT
**Repository:** `nordicirish/gemini_cli_subagent_system`  
**Baseline Commit:** `bb919c010ac2ac1995235d0f96aa49999e0c1c82`  
**Execution Environment:** Antigravity CLI (`C:\Users\roger\.gemini\bin\agy.exe`) / Windows PowerShell  
**Assessment Date:** 2026-11-08  
**Governance Standard:** Adapted from `tk-site-new` Engineering Governance Framework  

---

## 1. Existing Architecture Summary

The `gemini_cli_subagent_system` repository is an autonomous algorithmic investment management and market surveillance system designed to monitor equity baskets, calculate technical indicators (RSI, MACD, VWAP, Bollinger Bands, Net GEX, ATR, Fibonacci expansions), arbitrate decisions among specialized investment personas, and maintain a persistent Single Source of Truth (SSoT) portfolio state.

### Key Architectural Components
1. **Primary Entry Point — Web Dashboard & API (`python/web_server.py`):**
   - FastAPI server mounting static assets (`static/`), providing REST APIs (`/api/data`, `/api/chat`, `/api/basket`, `/api/watchlist`, `/api/list_models`), and handling streaming investment council debates.
   - Configured with an Antigravity Sidecar UI reverse-proxy specification (`~/.gemini/config/sidecars/gem-dashboard/sidecar.json`).
2. **CLI Entry Point (`python/main.py`):**
   - Interactive terminal REPL initializing sub-agents, loading rules, and executing council prompts directly from the command line.
3. **Core Agent Framework (`python/agent_framework.py`):**
   - Implements `AgentFramework`, `AntigravityAgentRunner`, and `AntigravityChatSession`.
   - Decoupled from Google GenAI cloud SDKs; executes locally by spawning the Antigravity CLI executable (`agy.exe`) via subprocess pipes.
4. **Market Telemetry Daemon (`python/fetch_stocks.py`):**
   - Background thread running `run_daemon()` that continuously polls Yahoo Finance and Finnhub APIs, calculates indicators, and updates `GLOBAL_STATE`.
5. **Execution Tools (`python/tools.py`):**
   - Python functions exposing portfolio SSoT reads/writes, trade lesson logging, rules updates, live market data inspection, and web forensic search.
6. **State & Rules Storage (`context/`, `gem_trading_rules/`):**
   - `context/ssot.json`: Persistent portfolio state, allocations, cash, and active holdings.
   - `context/decision_log.json`: Append-only historical log of council decisions.
   - `context/trade_lessons.json`: Persistent ledger of post-trade lessons and risk adjustments.
   - `gem_trading_rules/rules.md`: Canonical ruleset and investment mandates.

---

## 2. Runtime Execution Flow

### Subprocess Invocation Flow
The current agent execution flow in `python/agent_framework.py` operates as follows:

```
[AgentFramework.generate_response_with_fallback / AntigravityChatSession]
                        │
                        ▼
           [AntigravityAgentRunner.run_prompt]
                        │
                        ├─► 1. Check self.cancel_check()
                        ├─► 2. Map mode ("PRO"/"THINKING" -> "gemini-3.8-flash-high", else "gemini-3.8-flash-low")
                        ├─► 3. Construct cmd: [agy_path, "--model", model, "--dangerously-skip-permissions"]
                        ├─► 4. subprocess.Popen(cmd, stdin=PIPE, stdout=PIPE, stderr=PIPE, text=True)
                        ├─► 5. proc.communicate(input=full_prompt, timeout=120)
                        ├─► 6. Check proc.returncode != 0
                        └─► 7. Return AntigravityResponse(output_text, tokens, model)
```

### Forensic Analysis of Runtime Execution (Questions 1–10)
1. **Subprocess Creation:** Antigravity subprocesses are created via `subprocess.Popen` with standard pipes for `stdin`, `stdout`, and `stderr`, piping prompt payloads over UTF-8 text stdin.
2. **Executable & Arguments:**
   - Executable: `C:\Users\roger\.gemini\bin\agy.exe` (resolved via explicit candidates path resolution).
   - Command Arguments: `[self.agy_path, "--model", model, "--dangerously-skip-permissions"]`.
   - Note: The `--print` flag is omitted, and `--sandbox` is not currently passed in production runtime calls.
3. **Permission Granting:** Permissions are completely bypassed by passing `--dangerously-skip-permissions` to the CLI binary.
4. **`--dangerously-skip-permissions` Usage:** Confirmed hardcoded at line 86 of `python/agent_framework.py`.
5. **Filesystem Access:** Because `--dangerously-skip-permissions` is passed, `agy.exe` runs with the full filesystem read/write privileges of the host user account, with zero path confinement.
6. **Command Execution:** Because `--dangerously-skip-permissions` is passed, `agy.exe` has auto-approved access to its built-in shell execution tools (`run_command`), enabling arbitrary command execution on the host machine if instructed by input prompts.
7. **Permission Enforcement:** Tool permissions are described strictly in text docstrings (e.g., `update_rules` docstring claims human approval is required per MANDATE_21). The runtime does NOT enforce permission boundaries, role validations, or human approvals before dispatching tools.
8. **Cancellation & Timeout:**
   - Timeout handling: 120s timeout in `proc.communicate(timeout=timeout)` kills the process via `proc.kill()` on `TimeoutExpired`.
   - Cancellation handling: `self.cancel_check()` is checked before subprocess launch and between turns, but a running turn cannot be aborted until `proc.communicate` returns or times out.
9. **Output & Failure Classification:** Non-zero exit codes raise a `RuntimeError`. Handshakes are logged to `logs/gem_handshakes.log`. However, application-level error structures returned within tool execution payloads are treated as standard string results without structured failure handling.
10. **Model Selection Integrity:** Mode mapping routes `PRO`/`THINKING` to `gemini-3.8-flash-high` and `FAST`/`FLASH`/`GEMMA` to `gemini-3.8-flash-low`. While both models exist in `agy.exe models`, setting non-Gemini models via `/api/set_model` (e.g. `claude-opus-5-5-high`) is ignored because `AntigravityChatSession.send_message` hardcodes `mode="PRO"`.

---

## 3. Tool Execution Flow

### Tool Dispatch Comparison: CLI vs. Web Dashboard
A severe structural divergence exists between the CLI (`python/main.py`) and the Web Dashboard (`python/web_server.py`):

| Capability | Web Dashboard (`python/web_server.py`) | CLI (`python/main.py`) | Divergence Assessment |
|---|---|---|---|
| **Multi-Turn Dispatch Loop** | Implemented via `while True` loop inspecting `response.function_calls` | **MISSING:** Single-call `chat.send_message()` only | **CRITICAL:** CLI cannot execute tools across multi-turn reasoning |
| **Tool Execution Handler** | Iterates through `response.function_calls`, executes `tool_map[name](**args)`, wraps in `Part.from_function_response` | None. If model returns `function_calls`, CLI simply prints the unexecuted JSON | **CRITICAL:** Tools requested in CLI are never dispatched |
| **Subagent Tool Execution** | Subagent tool definition drops `agent_tools` | Subagent tool definition drops `agent_tools` | **HIGH:** Subagents advertised with sub-tools (e.g. `Data Analyst` search) cannot invoke them |
| **Argument Validation** | Raw `**args` unpacking with no schema validation or type checks | N/A (no dispatch) | **HIGH:** Vulnerable to `TypeError` crashes on unexpected LLM arguments |
| **Recursion / Loop Guards** | None (unbounded turn loop) | Single turn | **MEDIUM:** Web interface susceptible to infinite tool-call loops |

### Subagent Tool Execution Gap
In `python/agent_framework.py`, `create_agent_tool(name, file_path, mode, agent_tools=None)` receives `agent_tools` (e.g., `tools.perform_web_forensic_search`, `tools.get_market_data`). However:
1. `generate_response_with_fallback(..., tools=agent_tools)` accepts `tools`, but `AntigravityAgentRunner.run_prompt(...)` does not pass tool definitions to `agy.exe`.
2. As a consequence, subagents cannot trigger nested tool calls. All subagent queries operate in single-pass text generation.

---

## 4. Security Boundary Analysis

### Current Security Posture
1. **Unsafe Subprocess Privileges:**
   - Spawning `agy.exe` with `--dangerously-skip-permissions` grants the subagent process unrestricted access to local tools including `run_command`, `write_file`, and `edit_file`.
   - If market data, RSS feeds, or news search queries contain prompt injections, malicious directives could execute shell commands or overwrite local files.
2. **Unprotected File Write Tools:**
   - `tools.update_rules(rules_md_content: str)` writes directly to `gem_trading_rules/rules.md`.
   - `tools.update_ssot(payload_json: str)` directly overwrites `context/ssot.json`.
   - `tools.update_trade_lessons(lessons_json: str)` writes directly to `context/trade_lessons.json`.
   - None of these functions verify the identity of the calling agent, validate write permissions, or request human approval.
3. **Missing Network & Boundary Sandboxing:**
   - The Antigravity CLI binary supports `--sandbox` ("Run in a sandbox with terminal restrictions enabled"), but it is not currently utilized by `AgentFramework`.

---

## 5. Agent Permission Analysis

### Role vs. Capability Matrix (As Configured in Code)

| Agent Role | Declared Responsibilities | Advertised Tools | Actual Runtime Enforcement |
|---|---|---|---|
| **Terminal Orchestrator** | Master arbitration, synthesis | All terminal tools | Prompt docstrings only |
| **Data Analyst** | Forensic data analysis, filings search | `perform_web_forensic_search`, `get_market_data` | **Tools dropped** by runner |
| **Macro Sentinel** | Macro regime identification | `perform_web_forensic_search` | **Tools dropped** by runner |
| **Context Engine** | SSoT, lessons, rules maintenance | `read_ssot`, `update_ssot`, `read_trade_lessons`, `update_trade_lessons`, `get_market_data`, `perform_web_forensic_search`, `update_rules` | **Tools dropped** by runner; if called directly, has **unrestricted write access** to rules and SSoT |
| **State Validation Router** | SSoT verification | `read_ssot`, `update_ssot`, `get_market_data` | **Tools dropped** by runner |
| **Development / Engineering Agents** | Maintenance, tests, architecture | None defined in codebase | **No governance boundaries exist** |

**Finding:** Engineering agents do not exist as distinct entities from investment council personas in the current codebase. There is no separation between engineering authority and financial decision authority.

---

## 6. State Persistence Analysis

### State Stores & Ownership

| State Store | Format | Owning Components | Concurrency | Atomicity | Corruption Risk |
|---|---|---|---|---|---|
| `context/ssot.json` | JSON | `fetch_stocks.handle_paste`, `fetch_stocks.save_basket`, `tools.update_ssot` | Multiple threads (daemon, API, subagents) | **Non-atomic** (`open('w')`) | **HIGH:** Truncation on crash; race conditions |
| `context/trade_lessons.json` | JSON | `tools.update_trade_lessons`, `fetch_stocks.handle_paste` | Multiple callers | **Non-atomic** (`open('w')`) | **HIGH:** File truncation on concurrent write |
| `context/decision_log.json` | JSON | `tools.intercept_and_log_decision` | Background & chat threads | **Non-atomic** (`open('w')`) | **MEDIUM:** Duplicate or lost entries |
| `gem_trading_rules/rules.md` | Markdown | `tools.update_rules` | Manual + `Context Engine` | **Non-atomic** (`open('w')`) | **CRITICAL:** Accidental wipe of investment mandates |
| `context/config.json` | JSON | `web_server.py` (`/api/set_model`, `/api/set_cache_policy`) | API threads | **Non-atomic** (`open('w')`) | **MEDIUM:** Partial write on crash |

**Key Deficiencies:**
- Zero file locking (`fcntl` / `msvcrt` / lockfiles).
- Zero atomic write pattern (no write to temporary file followed by atomic `os.replace`).
- Zero isolation between financial investment state and engineering/maintenance state.

---

## 7. Existing Test Coverage & Baseline Results

### Baseline Execution Audit (2026-11-08)

| Test Script | Execution Command | Result | Findings / Notes |
|---|---|---|---|
| `scripts/test_antigravity_runner.py` | `python scripts/test_antigravity_runner.py` | **PASSED** (1/1) | Verifies `agy.exe` prompt execution and token counts |
| `scripts/test_antigravity_integration.py` | `python scripts/test_antigravity_integration.py` | **PASSED** (5/5) | Verifies backend initialization, subagent tool, and chat adapter |
| `scripts/test_basket_save.py` | `python scripts/test_basket_save.py` | **PASSED** (1/1) | Basket save succeeds; emitted warning: `module 'fetch_stocks' has no attribute '_load_watchlist'` |
| `scripts/test_cascading_fallback.py` | `python scripts/test_cascading_fallback.py` | **PASSED** (1/1) | Verifies fallback candidate list ordering |
| `scripts/test_daemon_init.py` | `python scripts/test_daemon_init.py` | **PASSED** (1/1) | Verifies `ALL_TICKERS` initialization |
| `scripts/test_decoupled_tools.py` | `python scripts/test_decoupled_tools.py` | **PASSED** (2/2) | Forensic web search and scout scanner execute via Antigravity |
| `scripts/test_fib_daily_peak.py` | `python scripts/test_fib_daily_peak.py` | **FAILED** (0/1) | `AssertionError: assert res2["daily_peak_status"] == "PEAK_EXHAUSTED"` (actual: `"EXPANDING"`) |
| `scripts/test_full_chart_flow.py` | `python scripts/test_full_chart_flow.py` | **PASSED** (1/1) | Multimodal chart parts loading verified |
| `scripts/test_intraday_and_charts.py` | `python scripts/test_intraday_and_charts.py` | **PASSED** (1/1) | 1m intraday data and chart screenshot decode verified |
| `scripts/test_live_call.py` | `python scripts/test_live_call.py` | **FAILED** (0/1) | `AttributeError: 'AgentFramework' object has no attribute 'client'` (legacy cloud reference) |
| `scripts/test_portfolio_sync.py` | `python scripts/test_portfolio_sync.py` | **PASSED** (1/1) | SSOT ticker extraction verified |
| `scripts/test_umac_cache.py` | `python scripts/test_umac_cache.py` | **PASSED** (1/1) | Daily history cache inspection verified |
| `scripts/test_umac_chart_flow.py` | `python scripts/test_umac_chart_flow.py` | **FAILED** (0/1) | Step 4 fails: `AttributeError: 'AgentFramework' object has no attribute 'client'` (legacy cloud reference) |
| `scripts/verify_audit_amendments.py` | `python scripts/verify_audit_amendments.py` | **PASSED** (6/6) | Verifies RSI, primary keys, session gates, and gamma-flip bounds |
| `scripts/verify_trading_system_fib.py` | `python scripts/verify_trading_system_fib.py` | **PASSED** (1/1) | Validates Fibonacci calculation and measured move tranches |

**Baseline Summary:**
- Discovered 15 test & verification scripts in `scripts/`.
- **Passed:** 12 scripts.
- **Failed:** 3 scripts (`test_fib_daily_peak.py`, `test_live_call.py`, `test_umac_chart_flow.py`).
- **Test Framework:** No automated test runner (such as `pytest`) configured in `requirements.txt`. Execution relies on standalone scripts using `assert` statements. Python standard library `unittest` is available.

---

## 8. Findings Classified by Severity

### Critical Severity (C)
- **C-01: Wholesale Permission Bypass in Subprocess Execution:** `agent_framework.py` executes `agy.exe` with `--dangerously-skip-permissions`, granting the LLM process unrestricted permission to execute shell commands and modify arbitrary files on the developer's workstation.
- **C-02: Missing Multi-Turn Tool Dispatch in CLI:** `python/main.py` lacks a multi-turn tool execution loop. Sub-agent tool calls returned by the model are never dispatched, breaking CLI functionality when tools are requested.
- **C-03: Direct Unvalidated Overwrite of Canonical Rules:** `tools.update_rules()` directly overwrites `gem_trading_rules/rules.md` without permission checks, schema validation, or human approval verification.
- **C-04: Non-Atomic State Persistence with Truncation Risk:** `context/ssot.json`, `context/trade_lessons.json`, and `context/decision_log.json` are written via raw non-atomic file writes without concurrency locks.

### High Severity (H)
- **H-01: Dropped Subagent Tools:** `create_agent_tool` accepts `agent_tools`, but `AntigravityAgentRunner.run_prompt` completely ignores the `tools` parameter, preventing subagents from invoking delegated tools.
- **H-02: Missing Tool Permission & Role Enforcement:** Tool dispatching in `web_server.py` executes any tool requested by name via `**args` without verifying calling role, argument types, or read/write classifications.
- **H-03: Absence of Engineering Governance Structure:** No formal agent governance hierarchy, specialist agent definitions, review gates, or permission boundaries exist in the repository.
- **H-04: Broken Baseline Tests from Cloud Decoupling:** `scripts/test_live_call.py` and `scripts/test_umac_chart_flow.py` contain obsolete references to `framework.client`.

### Medium Severity (M)
- **M-01: Unbounded Tool Loop Risk:** `web_server.py` tool dispatch while loop lacks a maximum turn boundary or loop detection, risking infinite execution cycles.
- **M-02: Unaligned Model Selection in Dashboard:** User-selected non-Gemini models via `/api/set_model` are bypassed during chat turns because `send_message` defaults mode to `PRO` (`gemini-3.8-flash-high`).
- **M-03: Incomplete Baseline Test in Fibonacci Module:** `scripts/test_fib_daily_peak.py` fails on peak exhaustion status assertion.
- **M-04: Non-Standardized Test Infrastructure:** Lack of a unified test discovery and execution framework complicates automated regression gating.

### Low Severity (L)
- **L-01: Daemon Hot-Reload Warning:** `save_basket` logs a warning regarding missing `_load_watchlist` in `fetch_stocks`.
- **L-02: Deprecated Pandas Arguments:** Repetitive `inplace=True` deprecation warnings on `DataFrame.sort_index` across tests.

---

## 9. Gap-Analysis Matrix

| ID | Finding | Severity | Evidence | Proposed Remediation | Regression Test |
|---|---|---|---|---|---|
| **GAP-01** | Wholesale permission bypass via `--dangerously-skip-permissions` | **Critical** | `agent_framework.py:86` | Enforce `--sandbox` mode; route tools through application-level permission registry | Test subprocess invocation with and without sandbox; verify command execution blocked |
| **GAP-02** | CLI cannot execute tools (missing tool dispatch loop) | **Critical** | `main.py:227-234` | Unify tool dispatching into a shared `ToolDispatcher` service used by both `main.py` and `web_server.py` | Execute representative tool-calling prompt via CLI test harness; verify tool dispatch |
| **GAP-03** | `update_rules` allows unauthorized overwrite of canonical rules | **Critical** | `tools.py:290-302` | Enforce read-only restriction for automated tools; require explicit cryptographic/human token for rule changes | Attempt `update_rules` from subagent; verify write rejection |
| **GAP-04** | Non-atomic writes to `ssot.json` and state files | **Critical** | `fetch_stocks.py:1221`, `tools.py:139,283` | Implement atomic write helper (`write_atomic_json`) with temporary file and `os.replace` plus file locking | Concurrent write stress test; verify zero data truncation or state amnesia |
| **GAP-05** | Subagents cannot execute assigned tools (`agent_tools` dropped) | **High** | `agent_framework.py:69,425` | Support prompt-based or runner-dispatched tool delegation for subagents | Invoke subagent with tool requirement; verify tool execution and synthesis |
| **GAP-06** | Tool arguments unpacked with raw `**args` without validation | **High** | `web_server.py:739` | Implement `ToolRegistry` with strict schema validation and parameter type guards | Invoke tool with invalid/extra parameters; verify structured error returned |
| **GAP-07** | Absence of structured agent governance and role boundaries | **High** | Empty `.agents` directory | Implement `.agents/` structure adapted from `tk-site-new` with explicit specialist roles and policies | Verify governance policy loading and role isolation tests |
| **GAP-08** | Broken legacy tests (`test_live_call.py`, `test_umac_chart_flow.py`) | **High** | `scripts/test_live_call.py:15`, `scripts/test_umac_chart_flow.py:56` | Update tests to use `AgentFramework` decoupled Antigravity runner | Execute test scripts; verify clean pass |
| **GAP-09** | Unbounded tool-dispatch loop in web server | **Medium** | `web_server.py:656-755` | Enforce `MAX_TOOL_TURNS` (default: 5) with cycle detection | Trigger repeated tool calls; verify termination at threshold |
| **GAP-10** | Absence of structured append-only audit log | **Medium** | Only `logs/gem_handshakes.log` exists (unstructured text) | Implement `context/agent_logs/agent-events.jsonl` with structured audit schema | Execute tool operations; verify audit event appended with redactions |

---

## 10. Proposed Implementation Changes

### Phase B: Engineering Governance Foundation
1. **`.agents/` Governance Architecture:**
   - Establish `.agents/AGENTS.md` master governance document.
   - Establish `.agents/protocols/` (orchestration, development standards, security, review gates, testing policy, financial boundaries).
   - Establish `.agents/agents/` (Architecture Agent, Python Engineer, Security Agent, Testing Agent, Documentation Agent, Independent Reviewer).
   - Establish `.agents/reviews/` for independent review sign-offs.

### Phase C: Runtime Permission & Execution Integrity
1. **Application-Level Security Boundary:**
   - Enhance `AntigravityAgentRunner` to pass `--sandbox` by default.
   - Create `python/security.py` / `python/tool_registry.py` defining central tool schemas, read/write classification, role permissions, and validation.
   - Add financial state protection: forbid engineering agents from modifying `context/ssot.json`, `context/trade_lessons.json`, `context/decision_log.json`, and `gem_trading_rules/rules.md`.
2. **Atomic State Persistence:**
   - Create `python/atomic_io.py` providing `atomic_write_json()` with file locking and atomic swap (`os.replace`).

### Phase D: Tool Execution Consistency (CLI & Web Parity)
1. **Unified Tool Dispatcher Service:**
   - Extract tool dispatch logic from `web_server.py` into `python/tool_dispatcher.py`.
   - Wire `python/main.py` and `python/web_server.py` to use the same underlying dispatch service.
   - Enforce `MAX_TOOL_TURNS = 5` to prevent infinite tool loops.

### Phase E: Structured Audit Logging
1. **Append-Only Engineering Event Log:**
   - Implement `python/audit_logger.py` logging structured JSONL events to `context/agent_logs/agent-events.jsonl`.
   - Log tool calls, permission decisions, file modifications, test summaries, and errors with secret redaction.

### Phase F: Review & Verification Gates
1. **Fail-Closed Verification Script:**
   - Implement `scripts/verify_stage_1.py` checking test results, permission enforcement, review evidence, and git clean state.

### Phase G: Automated Testing Suite
1. **Comprehensive Unit & Integration Test Suite (`tests/`):**
   - Permission tests (unauthorized tools rejected, invalid args rejected, financial protection enforced).
   - Execution tests (tool dispatch parity between CLI and Web, timeout handling, cycle prevention).
   - Audit tests (structured events appended, secrets redacted).
   - Regression tests (preserve all investment calculations, Fib levels, and SSoT semantics).

---

## 11. Files Expected to Change

### New Files to Create
- `docs/engineering/stage-1-pre-implementation-assessment.md` (this assessment)
- `.agents/AGENTS.md`
- `.agents/protocols/01-agent-orchestration.md`
- `.agents/protocols/02-development-standards.md`
- `.agents/protocols/03-security-policy.md`
- `.agents/protocols/04-review-gates.md`
- `.agents/protocols/05-testing-policy.md`
- `.agents/protocols/06-financial-boundaries.md`
- `.agents/agents/architecture-agent.md`
- `.agents/agents/python-engineer.md`
- `.agents/agents/security-agent.md`
- `.agents/agents/testing-agent.md`
- `.agents/agents/documentation-agent.md`
- `.agents/agents/independent-reviewer.md`
- `.agents/reviews/STAGE_1_INDEPENDENT_REVIEW.md`
- `python/atomic_io.py`
- `python/tool_registry.py`
- `python/tool_dispatcher.py`
- `python/audit_logger.py`
- `tests/test_permissions.py`
- `tests/test_tool_dispatch.py`
- `tests/test_audit_logger.py`
- `tests/test_atomic_io.py`
- `tests/test_investment_regression.py`
- `scripts/verify_stage_1.py`
- `docs/engineering/STAGE_1_COMPLETION_REPORT.md`

### Files to Modify
- `python/agent_framework.py` (integrate `--sandbox`, tool validation hooks, and runner timeouts)
- `python/tools.py` (integrate `atomic_io`, tool registry decorators, and financial state guards)
- `python/main.py` (integrate unified `tool_dispatcher` for CLI tool execution parity)
- `python/web_server.py` (delegate tool execution loop to unified `tool_dispatcher`)
- `python/fetch_stocks.py` (use `atomic_write_json` for `ssot.json` persistence)
- `scripts/test_live_call.py` (update decoupled Antigravity runner invocation)
- `scripts/test_umac_chart_flow.py` (update decoupled Antigravity runner invocation)
- `README.md` (document agent governance and runtime integrity architecture)

### Files Deliberately Left Unchanged
- `gem_trading_rules/rules.md` (investment rules and mandates are strictly protected)
- `engine_instructions/*.md` (all 19 trading engine instructions preserved without modification)
- `context/ssot.json` (portfolio allocations and financial data preserved)
- `context/trade_lessons.json` (trade lessons preserved)
- `context/decision_log.json` (decision log preserved)
- `static/*` (frontend UI templates and dashboard assets preserved)

---

## 12. Risks and Compatibility Considerations

1. **Subprocess Concurrency:** Running parallel subagent tasks via `ThreadPoolExecutor` spawns multiple `agy.exe` instances. The system must ensure CPU and memory usage remain bounded.
2. **Backward Compatibility:** All existing dashboard REST endpoints (`/api/data`, `/api/chat`, `/api/basket`) and response payloads must remain 100% backward compatible.
3. **Trading Rule Invariance:** No modifications to indicator algorithms (RSI, MACD, GEX, VWAP, Fibs), trading thresholds, or arbitration logic.
4. **Sandboxed Subprocess Execution:** Passing `--sandbox` must not block prompt-based text generation or Antigravity's internal functioning.

---

## 13. Explicit Out-of-Scope Items

As mandated by Stage 1 requirements:
- Autonomous live trading or order execution.
- Modifications to investment mandates, portfolio allocation rules, or trading strategies.
- Alterations to position sizing, risk models, or technical indicator formulas.
- Council debate personality modifications.
- Autonomous Git commits or pushes.
- Replacing the Antigravity CLI execution engine.

---

## 14. Stop Condition Assessment

**Evaluation of Stop Conditions:**
- Does any proposed change modify trading behavior? **NO.**
- Does any proposed change weaken an existing security control? **NO** (all changes strengthen security).
- Does any proposed change corrupt investment state? **NO** (atomic writes prevent state corruption).
- Does any proposed change require an architectural redesign? **NO** (incremental adaptation).

**Conclusion:** Assessment complete. The proposed plan satisfies all Stage 1 constraints and establishes the required governance foundation without impacting financial or trading functionality.

