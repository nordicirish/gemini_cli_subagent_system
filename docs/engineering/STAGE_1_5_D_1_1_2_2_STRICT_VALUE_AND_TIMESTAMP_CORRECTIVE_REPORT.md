# Stage 1.5-D.1.1.2.2 — Strict Missing-Value, Empty-Value & Timestamp Provenance Corrective Report

**Repository:** `nordicirish/gemini_cli_subagent_system`  
**Branch:** `main`  
**Stage Type:** Targeted Corrective Remediation & Provenance Hardening  
**Baseline HEAD Commit:** `5a4586552e6d91f3c1d4510cb96d761759e017e9`  
**Governance Standard:** GEM Agent Governance Framework (`.agents/AGENTS.md`)  
**Execution Authority:** Human-supervised implementation and automated verification only  
**Git Authority:** Human repository owner exclusively controls all git commits and pushes  

---

## 1. Executive Summary & Mission Scope

Stage 1.5-D.1.1.2.2 was executed as a narrowly scoped corrective stage addressing three findings identified in the independent review of Stage 1.5-D.1.1.2.1:

1. **Finding F1 (Medium): Explicit empty collections incorrectly treated as unspecified during representation consolidation.**  
   Truthiness checks (`prev.get("tickers") or []` and `prev["tickers"] = prev_tickers if prev_tickers else item_tickers`) caused explicitly empty collections (`[]`) to be erased when merged with populated collections (`["AAPL"]`), silently fabricating ticker memberships instead of raising `MigrationConflictError`.
2. **Finding F2 (Medium): Explicit empty account and currency identifiers silently replaced with defaults.**  
   Expressions such as `entry.get("account_id") or default_account_id` and `entry.get("currency") or "EUR"` replaced explicit empty strings (`""`), whitespace (`"   "`), `None`, or invalid types with `"PRIMARY"` and `"EUR"`, violating deterministic provenance and fail-closed validation.
3. **Finding F3 (Medium): Timestamp fallback relied on truthiness-based expressions.**  
   Constructs like `raw_ts = entry.get("added_at_utc") or entry.get("timestamp_utc")` allowed explicitly supplied empty strings (`""`), whitespace, or invalid timestamps to silently fall back to secondary timestamps or caller defaults, bypassing historical provenance enforcement. In `resolve_historical_timestamp()`, explicit empty strings were treated as missing rather than invalid.

All three findings have been resolved. The offline migration architecture and deterministic projection guarantees remain intact. No production databases (`context/gem.db`, `cache/market_data.db`) were accessed or created. Zero autonomous git commits or pushes were executed.

---

## 2. Baseline Verification & Operating Context

- **Baseline Commit:** `5a4586552e6d91f3c1d4510cb96d761759e017e9`
- **Operating System:** Windows (`win32`)
- **Python Version:** 3.15.1
- **Working Tree:** Clean prior to stage commencement.
- **Production Safety:** Verified that `context/gem.db` and `cache/market_data.db` do not exist.

---

## 3. Finding F1 Resolution: Strict Collection Preservation & Sentinel Consolidation

### Root Cause
In [`python/database/migration/contract.py`](file:///C:/github/gemini_cli_subagent_system/python/database/migration/contract.py), `consolidate_scout_representations` used `prev.get("tickers") or []` and `prev["tickers"] = prev_tickers if prev_tickers else item_tickers`. In Python, an empty list `[]` evaluates to `False`. When an explicit empty list `[]` was compared against a populated list `["AAPL"]`, `prev_tickers and item_tickers` evaluated to `False`, skipping conflict detection. Then, `prev_tickers if prev_tickers else item_tickers` selected `item_tickers` (`["AAPL"]`), silently discarding the explicit empty collection.

### Corrective Implementation
1. **Sentinel Disambiguation:**
   - When a category record omits the `tickers` field, it is assigned the sentinel `_UNSPECIFIED`.
   - When a category record explicitly contains `"tickers": []`, the empty list `[]` is preserved.
   - When a category record contains `"tickers": None`, `None` is preserved.
   - String entries in `scout_categories` and `SCOUT_TICKER_MAP` set `description = _UNSPECIFIED` and `tickers = _UNSPECIFIED` respectively where unstated.
2. **Strict Comparison Logic:**
   - Both `prev_tickers` and `item_tickers` are compared if and only if neither is `_UNSPECIFIED`:
     ```python
     prev_tickers = prev.get("tickers")
     item_tickers = item.get("tickers")
     if prev_tickers is not _UNSPECIFIED and item_tickers is not _UNSPECIFIED:
         if prev_tickers != item_tickers:
             raise MigrationConflictError(...)
     ```
   - If one is `_UNSPECIFIED` and the other is specified (`[]` or `["AAPL"]`), the specified value is retained:
     ```python
     prev["tickers"] = prev_tickers if prev_tickers is not _UNSPECIFIED else item_tickers
     ```
3. **Consolidation Truth Table:**
   | Left Representation | Right Representation | Consolidation Outcome | Invariance Verified |
   |---|---|---|---|
   | Absent (`_UNSPECIFIED`) | `["AAPL"]` | `["AAPL"]` | Yes (AB == BA) |
   | `["AAPL"]` | Absent (`_UNSPECIFIED`) | `["AAPL"]` | Yes (AB == BA) |
   | `[]` (explicit empty) | `[]` (explicit empty) | `[]` (explicit empty) | Yes (AB == BA) |
   | `[]` (explicit empty) | `["AAPL"]` | `MigrationConflictError` | Yes (fails both orders) |
   | `["AAPL"]` | `[]` (explicit empty) | `MigrationConflictError` | Yes (fails both orders) |
   | `["AAPL"]` | `["MSFT"]` | `MigrationConflictError` | Yes (fails both orders) |
   | `None` (explicit null) | `["AAPL"]` | `MigrationConflictError` | Yes (fails both orders) |

4. **Post-Loop Normalization:**
   After all representations are merged, remaining `_UNSPECIFIED` descriptions normalize to `None`, and remaining `_UNSPECIFIED` ticker collections normalize to `[]`.

---

## 4. Finding F2 Resolution: Rejection of Empty Account & Currency Identifiers

### Root Cause
In [`python/database/migration/contract.py`](file:///C:/github/gemini_cli_subagent_system/python/database/migration/contract.py):
- `consolidate_position_representations` evaluated `str(entry.get("account_id") or default_account_id).strip().upper()`.
- `consolidate_cash_representations` evaluated `str(entry.get("account_id") or default_account_id).strip().upper()` and `str(entry.get("currency") or "EUR").strip().upper()`.

If `account_id` or `currency` was explicitly supplied as `""`, `"   "`, `None`, or an integer `123`, the truthiness operator `or` silently replaced it with `"PRIMARY"` or `"EUR"`, accepting corrupt or uncodified records.

### Corrective Implementation
1. **Explicit Account Identifier Validation:**
   In both `consolidate_position_representations` and `consolidate_cash_representations`:
   ```python
   raw_acc = entry.get("account_id", _UNSPECIFIED)
   if raw_acc is _UNSPECIFIED:
       acc = default_account_id
   else:
       if not isinstance(raw_acc, str) or not raw_acc.strip():
           raise MigrationValidationError(
               f"{rep_key}[{idx}].account_id: explicitly supplied account_id must be a non-empty string"
           )
       acc = raw_acc.strip().upper()
   ```
2. **Explicit Currency Validation:**
   In `consolidate_cash_representations`:
   ```python
   raw_curr = entry.get("currency", _UNSPECIFIED)
   if raw_curr is _UNSPECIFIED:
       curr = "EUR"
   else:
       if not isinstance(raw_curr, str) or not raw_curr.strip():
           raise MigrationValidationError(
               f"portfolio_cash[{idx}].currency: explicitly supplied currency must be a non-empty string"
           )
       curr = raw_curr.strip().upper()

   if curr not in ("EUR", "USD"):
       raise MigrationValidationError(f"portfolio_cash[{idx}]: unsupported currency '{curr}'")
   ```
3. **Fail-Closed Behavior:**
   - Missing fields default safely (`"PRIMARY"`, `"EUR"`).
   - Valid explicit strings normalize cleanly (`" brokerage_1 "` -> `"BROKERAGE_1"`, `" usd "` -> `"USD"`).
   - Explicit `""`, `"   "`, `None`, non-string types (`123`, `[]`), or unapproved currencies (`"GBP"`, `"JPY"`) immediately raise `MigrationValidationError`.

---

## 5. Finding F3 Resolution: Strict Timestamp Fallback Elimination & Historical Provenance

### Root Cause
Across `contract.py`, `importer.py`, and `parity.py`:
- Multiple domains used `raw_ts = entry.get("primary_ts") or entry.get("secondary_ts")`.
- In `resolve_historical_timestamp()`, `source_value is not None and str(source_value).strip() != ""` treated explicit empty strings `""` or whitespace as missing, triggering fallback to `default_timestamp`.
- In `parity.py`, `_verify_timestamp_field` similarly treated empty strings as missing and verified them against caller default timestamps rather than flagging them fail-closed.
- In `importer.py`, trade lesson categories, statuses, and timestamps used truthiness defaults. Case C nested sub-decisions used `sub_d.get("timestamp") or ts_raw`, masking sub-decisions with explicit empty strings.

### Corrective Implementation
1. **Key-Presence Timestamp Selection:**
   Eliminated all `.get(...) or ...` timestamp expressions across:
   - `scout_categories`: Checks `"updated_at_utc" in entry`, elif `"timestamp_utc" in entry`, else `_UNSPECIFIED`.
   - `watchlist`: Checks `"added_at_utc" in entry`, elif `"timestamp_utc" in entry`, else `_UNSPECIFIED` across all 3 representation paths.
   - `portfolio_positions`: Checks `"last_updated_at_utc" in entry`, elif `"timestamp_utc" in entry`, else `_UNSPECIFIED`.
   - `portfolio_cash`: Checks `"last_updated_at_utc" in entry`, elif `"timestamp_utc" in entry`, else `_UNSPECIFIED`.
   - `decision_log` (Case C sub-decisions): Checks `"timestamp" in sub_d`, elif parent `raw_parent_ts is not _UNSPECIFIED`, else `_UNSPECIFIED`.
2. **Strict `resolve_historical_timestamp()` Rules:**
   [`python/database/migration/contract.py`](file:///C:/github/gemini_cli_subagent_system/python/database/migration/contract.py):
   - **Genuinely missing (`_MISSING` or `_UNSPECIFIED`):**
     - If `default_timestamp` is provided: parses and returns canonical timestamp with `CALLER_SUPPLIED_DEFAULT` provenance.
     - If `nullable=True`: returns `(None, MISSING)`.
     - Else: raises `MigrationValidationError` fail-closed.
   - **Explicit `None`:**
     - If `nullable=True`: returns `(None, MISSING)` (never falls back to default).
     - If `nullable=False`: raises `MigrationValidationError` fail-closed (never falls back to default).
   - **Explicit string value:**
     - Must be a non-empty string; `""` or `"   "` raises `MigrationValidationError` fail-closed (never falls back to secondary or default).
     - Non-string types raise `MigrationValidationError`.
     - Date-only strings require `allow_date_only=True` (returns `DERIVED`).
     - Timezone-naive strings require `allow_naive_utc=True` (returns `DERIVED`).
     - Timezone-aware strings normalize to canonical UTC ending in `'Z'` (returns `SOURCE_PROVIDED`).
3. **Parity Verifier Timestamp Field Verification:**
   [`python/database/migration/parity.py`](file:///C:/github/gemini_cli_subagent_system/python/database/migration/parity.py):
   - `_verify_timestamp_field`: Updated to inspect explicitly supplied values first. Explicit empty strings or invalid timestamps raise `MigrationValidationError`, which is captured as `UNRESOLVED` or `TIMESTAMP_MISMATCH` fail-closed rather than silently falling back to `default_timestamp`.
   - All legacy domain extractors in `parity.py` (`portfolio_cash`, `positions`, `watchlist`, `scout_categories`, `trade_lessons`, `decision_log`) now use explicit key-presence checks (`in dict`) rather than truthiness or `.get(...) is not None`.
4. **Importer Domain Hardening:**
   [`python/database/migration/importer.py`](file:///C:/github/gemini_cli_subagent_system/python/database/migration/importer.py):
   - `trade_lessons`: Explicit `category` or `status` must be a non-empty string. Explicit `created_at_utc` and `last_reviewed_at_utc` preserve `_UNSPECIFIED` when omitted, preventing explicit `None` or `""` from adopting defaults.
   - `decision_log` (Case A): Explicit `timestamp_utc` uses key presence (`_UNSPECIFIED` when absent).

---

## 6. Authoritative File Integrity & Hashes

All 8 protected authoritative files were audited and verified directly from the working filesystem. SHA-256 hashes and file byte sizes match the baseline exactly:

| Index | File Path | Status | Size (Bytes) | SHA-256 Digest | Match |
|---|---|---|---|---|---|
| 1 | `context/ssot.json` | Present | 219 | `0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da` | Exact |
| 2 | `context/trade_lessons.json` | Present | 11,738 | `0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463` | Exact |
| 3 | `context/decision_log.json` | Present | 9,691 | `757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2` | Exact |
| 4 | `context/config.json` | Present | 52 | `30ea2b67dd0ecac4b16e3e726c9644948119729023d382c385301f9db72099d5` | Exact |
| 5 | `context/user_config.json` | Present | 196 | `88e00f3ef6609eba02066cdfb3750152bfa2b97667aceed5cfba30b5db607183` | Exact |
| 6 | `context/agent_logs/agent-events.jsonl` | Present | 37,485 | `58a830fe4a59a02bb16d33df10c1a09b3e76a2b6250377e26ee37a8d73d8bc3d` | Exact |
| 7 | `config.json` | Present | 2,117 | `10c005efa15940d9c503aae62bbcb9a195ddbe249387c8335a4cb0f836064ee3` | Exact |
| 8 | `gem_trading_rules/rules.md` | Present | 215,204 | `04ea36c0762de30c841bb25ac35bdffeb6845cccfba19a9b94d343743c075056` | Exact |

---

## 7. Regression Test Suite Details

In [`tests/test_database_migration.py`](file:///C:/github/gemini_cli_subagent_system/tests/test_database_migration.py), class `TestStage15D1122StrictValueAndTimestampCorrectiveRegressions` was added with 20 focused regression tests covering all aspects of F1, F2, and F3:

### Finding F1 Regression Tests
- `test_f1_scout_missing_tickers_merges_with_populated_tickers`: Verifies absent + `["AAPL"]` merges to `["AAPL"]` in both orders.
- `test_f1_scout_two_explicit_empty_collections_consolidate_to_empty`: Verifies `[]` + `[]` consolidates to `[]` in both orders.
- `test_f1_scout_explicit_empty_vs_populated_raises_conflict`: Verifies `[]` vs `["AAPL"]` raises `MigrationConflictError` in both orders.
- `test_f1_scout_conflicting_populated_tickers_raises_conflict`: Verifies `["AAPL"]` vs `["MSFT"]` raises `MigrationConflictError`.
- `test_f1_scout_explicit_none_vs_populated_raises_conflict`: Verifies `None` vs `["AAPL"]` raises `MigrationConflictError` in both orders.

### Finding F2 Regression Tests
- `test_f2_positions_missing_account_id_defaults_to_primary`: Verifies omitted `account_id` defaults to `"PRIMARY"`.
- `test_f2_positions_explicit_valid_account_id_normalizes`: Verifies explicit `" brokerage_1 "` normalizes to `"BROKERAGE_1"`.
- `test_f2_positions_explicit_empty_or_invalid_account_id_rejected`: Verifies explicit `""`, `"   "`, `None`, `123`, and `[]` raise `MigrationValidationError`.
- `test_f2_cash_missing_account_id_defaults_to_primary`: Verifies omitted cash `account_id` defaults to `"PRIMARY"`.
- `test_f2_cash_explicit_empty_or_invalid_account_id_rejected`: Verifies cash explicit empty or invalid `account_id` raises `MigrationValidationError`.
- `test_f2_cash_missing_currency_defaults_to_eur`: Verifies omitted cash `currency` defaults to `"EUR"`.
- `test_f2_cash_explicit_valid_currency_normalizes`: Verifies explicit `" usd "` normalizes to `"USD"`.
- `test_f2_cash_explicit_empty_or_invalid_currency_rejected`: Verifies explicit `""`, `"   "`, `None`, `123`, `[]`, and unsupported currencies (`"GBP"`, `"JPY"`, `"CHF"`) raise `MigrationValidationError`.

### Finding F3 Regression Tests
- `test_f3_resolve_historical_timestamp_strict_missing_and_empty`: Verifies `resolve_historical_timestamp` fail-closed behavior for explicit `""`, `"   "`, `None` (non-nullable vs nullable), `_MISSING`, and `_UNSPECIFIED`.
- `test_f3_watchlist_explicit_empty_timestamp_fails_closed_without_fallback`: Verifies explicit empty primary timestamp raises `MigrationValidationError` even if secondary timestamp is valid.
- `test_f3_positions_explicit_empty_timestamp_fails_closed_without_fallback`: Verifies position with explicit empty `last_updated_at_utc` fails closed.
- `test_f3_cash_explicit_empty_timestamp_fails_closed_without_fallback`: Verifies cash with explicit empty `last_updated_at_utc` fails closed.
- `test_f3_scout_explicit_empty_timestamp_fails_closed_without_fallback`: Verifies scout category with explicit empty `updated_at_utc` fails closed.
- `test_f3_trade_lessons_validation_and_timestamp_strictness`: Verifies trade lessons reject empty category, empty status, empty created timestamp, and null created timestamp, while permitting explicit null review timestamp.
- `test_f3_case_c_decision_sub_decision_empty_timestamp_fails_closed`: Verifies sub-decision with explicit empty timestamp fails closed rather than inheriting parent decision timestamp.

---

## 8. Verification & Test Execution Results

All automated test suites were executed sequentially. Zero regressions were introduced.

| Test Suite | Command | Discovered | Passed | Skipped | Failures | Errors | Execution Time |
|---|---|---|---|---|---|---|---|
| Migration Suite | `python -m unittest tests.test_database_migration -v` | 150 | 150 | 0 | 0 | 0 | 8.33s |
| Database Core | `python -m unittest tests.test_database_core -v` | 120 | 120 | 0 | 0 | 0 | 5.12s |
| Repositories | `python -m unittest tests.test_database_repositories -v` | 254 | 254 | 0 | 0 | 0 | 23.56s |
| Projections | `python -m unittest tests.test_database_projections -v` | 54 | 54 | 0 | 0 | 0 | 3.23s |
| Backup Concurrency | `python -m unittest tests.test_database_backup_concurrency -v` | 74 | 73 | 1* | 0 | 0 | 7.26s |
| Full Discovery | `python -m unittest discover -s tests -v` | 675 | 670 | 1* | 0 | 4** | 57.85s |

*\* Skipped test: `test_r1_posix_existing_target_restoration_strictly_refused` in `tests/test_database_backup_concurrency.py` (explicitly annotated to run on POSIX platforms only; skipped on Windows).*  
*\*\* Discovery errors: Exactly 4 tests in non-database legacy modules (`test_investment_regression`, `test_permissions`, `test_tool_dispatch`, `test_watchlist_endpoints`) fail at module import due to optional third-party packages (`yfinance`, `requests`) not installed in the Windows environment. These 4 import errors are pre-existing and identical across all previous stages.*

### Exact Test Arithmetic Verification
$$\text{Discovered (675)} = \text{Passed (670)} + \text{Skipped (1)} + \text{Failures (0)} + \text{Errors (4)}$$
$$675 = 670 + 1 + 0 + 4 = 675 \quad \checkmark$$

### Static Checks & Byte Compilation
- **`python -m pip check`:** `No broken requirements found.`
- **`python -m py_compile`:** All modified files (`contract.py`, `importer.py`, `parity.py`, `test_database_migration.py`) compiled with exit code 0.

---

## 9. Code Modifications Summary

| File Path | Description of Changes |
|---|---|
| [`python/database/migration/contract.py`](file:///C:/github/gemini_cli_subagent_system/python/database/migration/contract.py) | Preserved explicit empty collections (`[]`) and `None` vs `_UNSPECIFIED` in scout representations; validated explicit account IDs and currencies in positions and cash; eliminated truthiness `.get(...) or ...` timestamp fallbacks across all domains; updated `resolve_historical_timestamp` to reject empty strings fail-closed. |
| [`python/database/migration/importer.py`](file:///C:/github/gemini_cli_subagent_system/python/database/migration/importer.py) | Validated explicit trade lesson categories and statuses; used key-presence checks (`_UNSPECIFIED` when omitted) for trade lesson timestamps and Case A decision record timestamps. |
| [`python/database/migration/parity.py`](file:///C:/github/gemini_cli_subagent_system/python/database/migration/parity.py) | Updated `_verify_timestamp_field` to inspect explicitly supplied timestamps first, failing closed on empty strings; replaced truthiness timestamp selection across all domain verifiers with explicit key-presence checks. |
| [`tests/test_database_migration.py`](file:///C:/github/gemini_cli_subagent_system/tests/test_database_migration.py) | Added `TestStage15D1122StrictValueAndTimestampCorrectiveRegressions` containing 20 automated regression tests verifying F1, F2, and F3. |

---

## 10. Boundary Compliance & Next Steps

1. **Governance & Boundaries:**
   - No production database cutover attempted; `context/gem.db` and `cache/market_data.db` remain absent.
   - All 8 authoritative legacy files remain 100% byte-for-byte identical to their baseline hashes.
   - No changes were made to trading strategies, risk controls, financial boundaries, or external APIs.
   - Zero autonomous git commits, amends, pushes, or branch operations were executed.
2. **Next Steps:**
   - Present this engineering report and verification evidence to the human repository owner.
   - Await human owner review and explicit git commit/push instructions. Do not proceed to Stage 1.5-E autonomously.

