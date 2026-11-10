# Engineering Report: Stage 1.5-D.1.1 — Migration Duplicate Integrity, Complete Bidirectional Parity & Historical Timestamp Provenance Corrections

**Repository:** `nordicirish/gemini_cli_subagent_system`  
**Branch:** `main`  
**Baseline Commit:** `ab6a5aa7c0fafa8ddfe43315a54787459b3296a0`  
**Stage Type:** Narrowly Scoped Corrective Implementation & Regression Verification  
**Status:** COMPLETE (Ready for Human Review, Git Commit, and Push)

---

## 1. Executive Summary

Stage 1.5-D.1.1 resolved the six findings (D1.1 through D1.6) identified during the independent review of Stage 1.5-D.1, establishing strict contract-level duplicate integrity, complete bidirectional semantic parity, fail-closed historical timestamp provenance, and rigorous audit reconciliation of real repository state.

### Key Achievements

1. **Complete Duplicate Integrity (R1 / D1.1):** Codified canonical equality field lists across all six domains. Conflicting duplicate records (e.g. differing timestamps, amounts, or outcomes) are rejected fail-closed with `MigrationConflictError`. Identical records are deduplicated idempotently.
2. **Comprehensive Bidirectional Parity (R2 / D1.2):** Implemented forward and reverse discrepancy detection across all six domains. Unexpected SQLite-owned records in projections are detected alongside missing legacy records. Zero-balance cash (`0`, `0.00`) is preserved and validated explicitly (`is not None`) rather than dropped through truthiness. Multi-account positions are tracked via composite key `(account_id, ticker)` to eliminate cross-account collisions.
3. **Shared Canonical Transformations & 7-Field Parity (R3 / D1.3):** Unified Decision Log Case B and Case C transformations in `contract.py` (`transform_legacy_decision_case_b` and `transform_legacy_decision_case_c`), verifying all seven fields (`session_id`, `action_proposed`, `decision_outcome`, `ticker`, `votes`, `transcript_text`, `timestamp_utc`) during parity verification.
4. **Strict Timestamp Provenance & Timezone Safety (R4 / D1.4):** Implemented `TimestampProvenance` enum. Date-only (`YYYY-MM-DD`) and timezone-naive timestamps are rejected fail-closed unless explicitly authorized via `allow_date_only=True` or `allow_naive_utc=True`. Timezone-aware timestamps with arbitrary offsets (`+02:00`, `-05:00`) are normalized to canonical UTC ending in `Z`.
5. **Trade Lesson Review Timestamp Provenance (R5 / D1.5):** Stopped substituting `ts_created` or caller default timestamps for missing review timestamps. Trade lessons without historical reviews preserve `last_reviewed_at_utc = None`.
6. **Real Legacy-Source Audit Reconciliation (R6 / D1.6):** Corrected audit discrepancies regarding `context/ssot.json` (which contains 0 cash entries, 1 position, 2 watched tickers), `context/trade_lessons.json` (24 items: 22 prefixed with `L-xxx`, 2 missing prefix), and `context/decision_log.json` (Entry 0 date-only, Entry 1 naive timestamp with 5 sub-decisions).
7. **Full Automated Verification:** Discovered and executed 593 tests across 5 test suites. 592 passed, 1 skipped (POSIX advisory locking on Windows `win32`), 0 failures, 0 errors. All 8 authoritative files remain 100% byte-for-byte invariant.

---

## 2. Baseline Ancestry & Repository State

- **Baseline Commit:** `ab6a5aa7c0fafa8ddfe43315a54787459b3296a0`
- **Subject Commit Message:** `Stage 1.5-D.1 — Migration Provenance, Complete Record Integrity & Semantic Parity Hardening`
- **Parent Commit:** `13ef19beade33102d95e0fdc3769fb9185047c19` (Stage 1.5-D implementation)
- **Active Operating System:** Windows (`win32`, Python 3.15.1)
- **Git Authority:** Human repository owner exclusively controls all Git commits and pushes. Strictly zero autonomous Git write operations performed.

---

## 3. Duplicate Equality Field Specifications (R1)

To prevent silent overwrite or data corruption, duplicate comparisons check every contract-owned field using exact Decimal comparison and recursive collection/dictionary comparison (`deep_equal`):

| Domain | Canonical Equality Fields | Behavior on Mismatch |
|---|---|---|
| **Portfolio Cash** | `account_id`, `currency`, `amount`, `last_updated_at_utc` | `MigrationConflictError` |
| **Portfolio Positions** | `account_id`, `ticker`, `shares`, `wac_eur`, `wac_usd`, `last_updated_at_utc` | `MigrationConflictError` |
| **Watchlist** | `ticker`, `status`, `notes`, `added_at_utc` | `MigrationConflictError` |
| **Scout Categories** | `category_name`, `tickers`, `description`, `updated_at_utc` | `MigrationConflictError` |
| **Trade Lessons** | `rule_code`, `directive_text`, `category`, `status`, `created_at_utc`, `last_reviewed_at_utc` | `MigrationConflictError` |
| **Decision Log** | `session_id`, `action_proposed`, `decision_outcome`, `ticker`, `votes`, `transcript_text`, `timestamp_utc` | `MigrationConflictError` |

Identical duplicates (where all equality fields match) are deduplicated idempotently with an audit log entry in `plan.discrepancies`.

---

## 4. Comprehensive Bidirectional Parity Verification (R2)

`SSoTParityVerifier` performs complete bidirectional verification across all six domains:

### 4.1 Portfolio Cash
- **Forward check:** Every legacy EUR/USD scalar and structured cash record is compared against projection.
- **Reverse check:** Any projected cash entry (scalar `Decimal != 0` or structured record in `portfolio_cash`) that has no corresponding legacy entry is flagged as `UNEXPECTED_RECORD`.
- **Zero-balance preservation:** Scalar cash balances `0` and `0.00` are checked explicitly (`is not None`), preserving exact numeric equivalence without false omissions.

### 4.2 Portfolio Positions
- **Composite Identity:** Keyed by composite tuple `(account_id, normalized_ticker)`. Multi-account portfolios (e.g. `PRIMARY` and `TAXABLE` both holding `SPY`) do not collide.
- **Forward check:** Shares, cost bases (`wac_eur`, `wac_usd`), and timestamps are verified.
- **Reverse check:** Any projected position not present in legacy source is flagged as `UNEXPECTED_RECORD`.

### 4.3 Watchlist
- **Forward check:** Watched ticker status, notes, and added timestamp are verified.
- **Reverse check:** Any projected watched ticker or structured watchlist item not in legacy source is flagged as `UNEXPECTED_RECORD`.

### 4.4 Scout Categories
- **Forward check:** Category names, ticker sets, descriptions, and update timestamps are verified.
- **Reverse check:** Any projected scout category not in legacy source is flagged as `UNEXPECTED_RECORD`.

### 4.5 Trade Lessons
- **Forward check:** Rule code, directive text, category, status, and created/reviewed timestamps are verified.
- **Reverse check:** Any projected trade lesson not in legacy source is flagged as `UNEXPECTED_RECORD`.

### 4.6 Decision Log
- **Forward check:** All 7 fields (`session_id`, `action_proposed`, `decision_outcome`, `ticker`, `votes`, `transcript_text`, `timestamp_utc`) are compared.
- **Reverse check:** Any projected decision log record `(session_id, action_proposed, ticker)` not in legacy source is flagged as `UNEXPECTED_RECORD`.

---

## 5. Zero-Balance Cash Preservation Evidence (R2)

Previous implementations used truthiness checks (`val or ...`), treating zero balances (`0` or `0.00`) as missing.

In `python/database/migration/parity.py`, cash amount presence is verified via `is not None`:
```python
leg_eur = legacy.get("unallocated_cash_eur")
if leg_eur is not None and proj_eur is not None:
    dec_leg = Decimal(str(leg_eur))
    dec_proj = Decimal(str(proj_eur))
    if dec_leg == dec_proj:
        ...
```
Regression test `test_zero_balance_cash_preserved_and_verified_explicitly` confirms that `unallocated_cash_eur: "0.00"` and `portfolio_cash: [{"amount": "0.00"}]` achieve 100% parity with zero discrepancies.

---

## 6. Composite Position Identity Design (R2)

Position mapping and parity verification index positions by composite key:
$$\text{Position Identity} = (\text{account\_id}, \text{ticker})$$

- `account_id` defaults to `"PRIMARY"` if omitted in single-account legacy records.
- Path identifiers in discrepancy reports format as `portfolio_snapshot[PRIMARY/SPY]` or `portfolio_snapshot[SPY]`.
- Multi-account holdings in `PRIMARY` and `TAXABLE` accounts with the same ticker are imported and verified without collision. Regression test `test_composite_position_identity_multi_account_supported` validates this capability.

---

## 7. Decision Log Shared Transformations & Complete 7-Field Parity (R3)

Shared transformation functions in `python/database/migration/contract.py` guarantee identical parsing and mapping between migration planning and parity verification:

### 7.1 Case B: Date-Based Sessions
- Function: `transform_legacy_decision_case_b(record, default_timestamp, allow_date_only)`
- Maps:
  - `session_id`: `LEGACY-SESSION-{date}`
  - `action_proposed`: Extracted from lesson or outcome
  - `decision_outcome`: `outcome` or `"AUTO_LOGGED"`
  - `ticker`: `ticker` or `"SESSION"`
  - `votes`: `{}`
  - `transcript_text`: `lesson`
  - `timestamp_utc`: `{date}T00:00:00Z` (requires `allow_date_only=True`)

### 7.2 Case C: Deliberation Council Debates with Sub-Decisions
- Function: `transform_legacy_decision_case_c(record, default_timestamp, allow_naive_utc)`
- Maps:
  - `session_id`: `SESSION-{date}-{trigger_context}`
  - For each sub-decision in `decisions`:
    - `ticker`: Sub-decision ticker
    - `action_proposed`: `trade_state` or `action` or `trigger_context`
    - `decision_outcome`: `trade_state` or `outcome` or `"APPROVED"`
    - `votes`: Nested `scrutiny_audit` dictionary
    - `transcript_text`: Formatted markdown transcript including council debate stances
    - `timestamp_utc`: Parent timestamp (requires `allow_naive_utc=True` if naive)

All 7 fields are verified during parity checking with semantic Decimal awareness via `deep_equal`.

---

## 8. Historical Timestamp Provenance Policy Table (R4)

`TimestampProvenance` Enum codifies historical provenance:

| Provenance Enum | Description | Permitted Context |
|---|---|---|
| `SOURCE_PROVIDED` | Explicitly present in legacy source document with timezone offset | Primary historical record |
| `CALLER_SUPPLIED_DEFAULT` | Supplied by caller via `default_timestamp` argument | Controlled offline dry-run |
| `DERIVED` | Normalized from authorized date-only (midnight UTC) or naive UTC (Z suffix) | Explicitly authorized legacy compatibility |
| `MISSING` | Field is null or omitted in source and allowed to be null | Optional fields (e.g. `last_reviewed_at_utc`) |
| `AMBIGUOUS` | Ambiguous format or unsupported offset | Fail-closed rejection |

### Policy Enforcement Rules
1. **Date-Only Timestamps (`YYYY-MM-DD`):** Rejected fail-closed with `MigrationValidationError` unless `allow_date_only=True`. When authorized, converted to `YYYY-MM-DDT00:00:00Z` with provenance `DERIVED`.
2. **Timezone-Naive Timestamps (`YYYY-MM-DDTHH:MM:SS`):** Rejected fail-closed with `MigrationValidationError` unless `allow_naive_utc=True`. When authorized, converted to canonical UTC `Z` with provenance `DERIVED`.
3. **Timezone-Aware Timestamps:** Converted to canonical UTC ending in `Z` (e.g. `17:00:00+02:00` $\to$ `15:00:00Z`) with provenance `SOURCE_PROVIDED`.

---

## 9. Trade Lesson Review Timestamp Policy (R5)

Historical trade lessons codified from manual rules often lack review records. Fabricating review timestamps corrupts governance compliance.

- In `importer.py`, `last_reviewed_at_utc` is resolved with `nullable=True` and `default_timestamp=None`.
- If `last_reviewed_at_utc` is omitted from the source record, it remains `None`.
- Neither `ts_created` nor `default_timestamp` is used as a fallback for missing review timestamps.
- Regression test `test_trade_lesson_review_timestamp_not_fabricated_from_created` validates that `last_reviewed_at_utc` remains `None`.

---

## 10. Actual Legacy-Source Audit Reconciliation (R6)

Inspection of actual repository files establishes true baseline values:

| File Path | SHA-256 Hash | Real Record Counts | Audit Notes |
|---|---|---|---|
| `context/ssot.json` | `0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da` | Positions: 1 (`SPY`)<br>Watchlist: 2 (`GOOG`, `AMZN`)<br>Cash: 0 | Cash is completely absent. Zero unallocated EUR/USD cash exists in this legacy file. |
| `context/trade_lessons.json` | `0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463` | Total: 24<br>Prefixed (`L-xxx`): 22<br>Unprefixed: 2 (items 16, 17) | Unprefixed items fail-closed; 22 items require caller `default_timestamp` to import. `last_reviewed_at_utc` is omitted and preserved as `None`. |
| `context/decision_log.json` | `757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2` | Total: 2 top-level entries<br>Entry 0: 1 date-only session<br>Entry 1: 5 deliberation sub-decisions | Under default policies (`allow_date_only=False, allow_naive_utc=False`), this file fails validation fail-closed. Under authorized compatibility policies (`allow_date_only=True, allow_naive_utc=True`), all 6 records import deterministically with 100% parity. |

---

## 11. Test Execution Results & Arithmetic Breakdown

The complete test suite was executed across all database test modules. Exact results:

- **Total Discovered:** 593
- **Passed:** 592
- **Skipped:** 1
- **Failed:** 0
- **Errors:** 0

### Suite Breakdown
1. `tests/test_database_core.py`: 115 tests, 115 passed.
2. `tests/test_database_repositories.py`: 254 tests, 254 passed.
3. `tests/test_database_projections.py`: 54 tests, 54 passed.
4. `tests/test_database_backup_concurrency.py`: 74 tests, 73 passed, 1 skipped.
5. `tests/test_database_migration.py`: 96 tests, 96 passed.

$$\text{Total} = 115 + 254 + 54 + 74 + 96 = 593$$
$$\text{Passed} = 115 + 254 + 54 + 73 + 96 = 592$$

### Skipped Test Documentation
- **Test Name:** `test_r5_2_posix_advisory_lock_blocks_conflicting_holder` in `tests/test_database_backup_concurrency.py`
- **Condition:** `@unittest.skipIf(sys.platform == "win32", "POSIX fcntl advisory locking is not supported on Windows")`
- **Reason:** The active execution host runs Windows (`win32`), where POSIX `fcntl` file locking is not available. This skip is expected and documented.

---

## 12. Byte-for-Byte Invariance Verification (8 Authoritative Files)

All 8 authoritative files are 100% byte-for-byte identical to baseline `ab6a5aa7c0fafa8ddfe43315a54787459b3296a0`:

| File Path | Verified SHA-256 Hash | Status |
|---|---|---|
| `context/ssot.json` | `0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da` | Invariant |
| `context/trade_lessons.json` | `0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463` | Invariant |
| `context/decision_log.json` | `757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2` | Invariant |
| `config.json` | `10c005efa15940d9c503aae62bbcb9a195ddbe249387c8335a4cb0f836064ee3` | Invariant |
| `python/database/schema.py` | `11c5beb3882f77c273307cb30b6f6990d485c4a38e1ad9426011944a973ffda6` | Invariant |
| `python/database/manager.py` | `c6768ad35020288de599695e046bb7b6bbeee30fee8830d44440d8be18756b07` | Invariant |
| `python/database/projections.py` | `0e557dd71a132a8af37bb4db2a9d3d65fe278372115d3a4d1f8e6c99231881ef` | Invariant |
| `python/database/projection.py` | `5cc5941389d9334d48bcca6b4a394c49e155aac5e0642c57b5fd3522080699a1` | Invariant |

---

## 13. Production Database Absence Confirmation

- `context/gem.db` exists: `False` (absent)
- `cache/market_data.db` exists: `False` (absent)

All tests operate exclusively against isolated disposable SQLite databases in temporary directories.

---

## 14. Autonomous Git Operations Confirmation

Strictly zero autonomous Git write operations (`git commit`, `git push`, `git checkout -b`, etc.) were executed. All changes remain unstaged in the local working directory awaiting human inspection and sign-off.

---

## 15. Summary of Modified Code Files

1. `python/database/repositories/base.py`:
   - Constrained `_ISO_TIMESTAMP_PATTERN` hours to `(?:[01][0-9]|2[0-3])` and minutes/seconds to `[0-5][0-9]` to guarantee fail-closed rejection of `24:00:00` under Python 3.15 `fromisoformat`.
2. `python/database/migration/models.py`:
   - Codified `TimestampProvenance` enum (`SOURCE_PROVIDED`, `CALLER_SUPPLIED_DEFAULT`, `DERIVED`, `MISSING`, `AMBIGUOUS`).
3. `python/database/migration/__init__.py`:
   - Exported `TimestampProvenance` in public API.
4. `python/database/migration/contract.py`:
   - Codified equality field lists for all six domains.
   - Implemented `deep_equal` and `records_equal` supporting exact Decimal arithmetic and recursive collections.
   - Implemented `is_date_only`, `is_timezone_aware`, and `normalize_aware_timestamp`.
   - Updated `resolve_historical_timestamp` to return `(timestamp, TimestampProvenance)` tuple and accept `allow_date_only`, `allow_naive_utc`.
   - Implemented shared `transform_legacy_decision_case_b` and `transform_legacy_decision_case_c`.
5. `python/database/migration/importer.py`:
   - Wired strict equality lists and `records_equal` across all six domains.
   - Refactored `last_reviewed_at_utc` resolution to `nullable=True` and `default_timestamp=None` (no fallback to created timestamp).
   - Wired shared decision transformations in `plan_migration`.
6. `python/database/migration/parity.py`:
   - Added bidirectional parity verification detecting unexpected projected records across all six domains.
   - Preserved zero-balance cash (`0`, `0.00`) via explicit `is not None` comparison.
   - Switched position matching to composite key `(account_id, normalized_ticker)`.
   - Implemented full 7-field semantic verification for Case A, Case B, and Case C decision records using `deep_equal`.
7. `tests/test_database_migration.py`:
   - Added `TestStage15D11CorrectiveRegressions` containing 24 regression tests covering R1 through R6. Total migration test count increased from 72 to 96.

---

## 16. Closeout Statement

Stage 1.5-D.1.1 implementation and automated verification are complete. The codebase strictly adheres to the Agent Governance Framework.

**Execution Authority Handover:** Execution terminates here. The repository owner may inspect the diffs and perform Git commit and push manually. Stage 1.5-E must not begin until authorized.

