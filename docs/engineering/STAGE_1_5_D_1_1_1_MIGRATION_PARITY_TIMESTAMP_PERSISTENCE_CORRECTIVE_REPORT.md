# Engineering Report: Stage 1.5-D.1.1.1 — Migration Parity Completeness, Review Timestamp Persistence & Fail-Closed Legacy Record Handling

**Repository:** `nordicirish/gemini_cli_subagent_system`  
**Branch:** `main`  
**Baseline Commit:** `ee4b97571cd2e9bf3cdc5d0456f80095d341468d`  
**Implementation Stage:** Stage 1.5-D.1.1.1 (Corrective Closeout)  
**Execution Authority:** User-supervised; implementation and verification only  
**Git Authority:** Human repository owner exclusively controls commits and pushes  
**Execution Date:** 2026-11-10  

---

## 1. Executive Summary

Stage 1.5-D.1.1.1 completes the narrowly scoped corrective remediation addressing findings identified in the independent review of Stage 1.5-D.1.1 within `nordicirish/gemini_cli_subagent_system`.

All six migration domains now enforce complete, collision-proof, bidirectional semantic parity between legacy source state and generated SQLite SSoT projections. Missing or null historical timestamps can never create false-positive parity, non-null trade-lesson review timestamps are preserved and persisted faithfully, unreviewed trade lessons retain genuine `NULL` values in SQLite, Case C malformed or colliding sub-decisions fail closed, and all accepted structured legacy input representations are normalized consistently without double-counting.

Zero production databases were created, zero authoritative legacy JSON files were modified, and all eight protected files remain 100% byte-for-byte invariant.

---

## 2. Baseline Ancestry & Repository State

- **Baseline Commit:** `ee4b97571cd2e9bf3cdc5d0456f80095d341468d`
- **Current HEAD:** `ee4b97571cd2e9bf3cdc5d0456f80095d341468d`
- **Environment:** Windows 10 Pro / Python 3.15.1 (`win32`)
- **Git State:** Read-only inspection; zero autonomous commits, pushes, or branch modifications performed.

---

## 3. Exact Files Modified & Technical Justification

Seven files were modified to resolve findings R1 through R5:

| Modified File | Primary Purpose / Reason |
|---|---|
| [`python/database/schema.py`](file:///C:/github/gemini_cli_subagent_system/python/database/schema.py) | **R2:** Relaxed `trade_lessons.last_reviewed_at_utc` column constraint from `TEXT NOT NULL` to nullable `TEXT`, and updated `EXPECTED_CORE_TABLE_DEFS` `notnull = False` to permit unreviewed trade lessons to store genuine `NULL` in SQLite. |
| [`python/database/repositories/base.py`](file:///C:/github/gemini_cli_subagent_system/python/database/repositories/base.py) | **R2:** Updated `TradeLesson.last_reviewed_at_utc` dataclass field type annotation from `str` to `Optional[str] = None` to support nullable review history. |
| [`python/database/repositories/governance.py`](file:///C:/github/gemini_cli_subagent_system/python/database/repositories/governance.py) | **R2:** Extended `TradeLessonRepository.upsert_lesson()` with `last_reviewed_at_utc: Any = _UNSET`. When omitted (`_UNSET`), defaults to mutation timestamp for backward compatibility with active trading mutations; when explicitly `None`, persists SQL `NULL`; when provided as a valid timestamp string, validates and persists it. |
| [`python/database/migration/contract.py`](file:///C:/github/gemini_cli_subagent_system/python/database/migration/contract.py) | **R3, R5:** Codified recursive `deep_equal()` supporting Decimal equivalence across nested dicts, lists, tuples, and sets. Enforced fail-closed validation on Case C sub-decisions: non-dict items raise `MigrationValidationError`, null items raise `MigrationValidationError`, empty/incomplete items raise `MigrationValidationError` (all with entry index), and colliding canonical sub-decision identities `(sub_action, sub_ticker)` raise `MigrationConflictError`. |
| [`python/database/migration/importer.py`](file:///C:/github/gemini_cli_subagent_system/python/database/migration/importer.py) | **R2:** Configured `last_reviewed_at_utc` with `nullable=True, default_timestamp=None` in Case A trade lessons and `last_reviewed_at_utc: None` in Case B lessons. Passed `last_reviewed_at_utc=tl["last_reviewed_at_utc"]` to `uow.trade_lessons.upsert_lesson()` during migration import. |
| [`python/database/migration/parity.py`](file:///C:/github/gemini_cli_subagent_system/python/database/migration/parity.py) | **R1, R4, R5:** Added `default_timestamp` support to `SSoTParityVerifier`. Implemented `_verify_timestamp_field()` to distinguish source-provided timestamps, authorized caller defaults, nullable timestamps (`nullable=True`), and required timestamps, rejecting unexplained projected timestamps. Normalized structured inputs across watchlist, scout categories, and portfolio positions without double-counting. Used `matched_projected_ids` for collision-proof reverse decision log checks and `deep_equal()` for votes comparison. |
| [`tests/test_database_migration.py`](file:///C:/github/gemini_cli_subagent_system/tests/test_database_migration.py) | **R7:** Configured `self.default_ts` in `BaseMigrationTestCase.setUp()`. Added dedicated test suite `TestStage15D111CorrectiveRegressions` with 15 comprehensive regression tests covering R1–R5. |

---

## 4. Verification of Findings R1–R5

### 4.1 R1 — Explicit & Complete Nullable Timestamp Parity

**Defect:** The parity verifier conditionally verified timestamps only when the legacy value was truthy (`if legacy_val:`), allowing projected timestamps to pass parity even when the legacy source contained no timestamp or `null`.

**Resolution:**
- Implemented `SSoTParityVerifier._verify_timestamp_field()`:
  1. *Source value present:* Validates and compares expected canonical UTC timestamp with projected timestamp (`TIMESTAMP_MISMATCH` if differing).
  2. *Source value missing/null:*
     - If `nullable=True` (e.g. `last_reviewed_at_utc` for unreviewed lessons): projected value **MUST** be `None`. If non-null, flags `TIMESTAMP_MISMATCH`.
     - If `nullable=False`:
       - If caller supplied an authorized `default_timestamp`: projected value must equal the canonical default timestamp (`equivalent_values` incremented).
       - If caller did **not** supply a default timestamp (`default_timestamp=None`): flags `TIMESTAMP_MISMATCH` with message `"Unexplained projected timestamp for <path>: source is missing/null and no default authorized, but got '<projected_val>'"`!
- Applied across all six domains: portfolio cash (`last_updated_at_utc`), portfolio positions (`last_updated_at_utc`), watchlist (`added_at_utc`), scout categories (`updated_at_utc`), trade lessons (`created_at_utc` and `last_reviewed_at_utc`), and decision log (`timestamp_utc`).

### 4.2 R2 — Preserve & Persist Non-Null Trade-Lesson Review Timestamps

**Defect:** While idempotency comparisons checked `last_reviewed_at_utc`, new record insertion in `importer.py` omitted `last_reviewed_at_utc` in calls to `uow.trade_lessons.upsert_lesson()`. Furthermore, SQLite schema enforced `last_reviewed_at_utc TEXT NOT NULL`, forcing review timestamps to be fabricated even when legacy rules were unreviewed.

**Resolution:**
- In `python/database/schema.py`, updated table definition to `last_reviewed_at_utc TEXT` (nullable) and `EXPECTED_CORE_TABLE_DEFS` `notnull = False`.
- In `python/database/repositories/base.py`, updated `TradeLesson.last_reviewed_at_utc` to `Optional[str] = None`.
- In `python/database/repositories/governance.py`, updated `TradeLessonRepository.upsert_lesson()` with parameter `last_reviewed_at_utc: Any = _UNSET`:
  - When omitted (`_UNSET`), defaults to mutation timestamp `valid_ts` (preserving backward compatibility for active trading mutations).
  - When explicitly passed as `None`, writes SQL `NULL`.
  - When explicitly passed as a timestamp string, validates format and persists it.
- In `python/database/migration/importer.py`, passed `last_reviewed_at_utc=tl["last_reviewed_at_utc"]` on upsert.

### 4.3 R3 — Fail-Closed Handling of Malformed Case C Sub-Decisions

**Defect:** Case C deliberation council debate records with malformed sub-decisions (non-dictionary elements, null elements, or empty dictionaries) could cause unhandled exceptions or corrupt database records. Colliding sub-decisions within the same session were not caught fail-closed.

**Resolution:**
- In `python/database/migration/contract.py` (`transform_legacy_decision_case_c`):
  - Validates `isinstance(sub_d, dict)`: raises `MigrationValidationError(f"decision_log.decisions[{sub_idx}]: sub-decision must be a dict, got {type(sub_d).__name__}")`.
  - Validates non-null / non-empty: raises `MigrationValidationError(f"decision_log.decisions[{sub_idx}]: sub-decision cannot be empty")`.
  - Tracks canonical sub-decision identities `sub_key = (sub_action, sub_ticker)` within the session. If duplicate detected, raises `MigrationConflictError(f"decision_log.decisions[{sub_idx}]: duplicate/colliding canonical sub-decision identity '{sub_key}' within session '{session_id}'")`.
  - Imports all valid sub-decisions into SQLite `decision_log` table.

### 4.4 R4 — Canonical Normalization of Structured Legacy Inputs

**Defect:** Importer accepted alternative structured input keys (`portfolio_positions`, `watchlist`, `scout_categories`), but parity verification previously looked only for legacy aliases (`portfolio_snapshot`, `watched_tickers`, `scout_candidates`), leading to false discrepancies or double-counting.

**Resolution:**
- In `python/database/migration/parity.py`:
  - `_verify_watchlist_domain()`: Checks both `watchlist` and `watched_tickers`, normalizing records into a deduplicated map keyed by uppercase ticker.
  - `_verify_scout_categories_domain()`: Checks `scout_categories`, `scout_category_records`, `SCOUT_TICKER_MAP`, and `scout_candidates`, normalizing them into a deduplicated category map.
  - `_verify_positions_domain()`: Checks both `portfolio_positions` and `portfolio_snapshot`, normalizing composite keys `(account_id, ticker)`.
  - `_verify_cash_domain()`: Recognizes default `"0.00"` scalar cash when no cash records are present in legacy or projection, preventing false unexpected record discrepancies.

### 4.5 R5 — Complete Bidirectional Parity & Collision-Proof Checks

**Defect:** Decision log parity verification previously relied on matching by index without tracking matched projected record IDs, which allowed duplicate projected records mapping to a single legacy record to escape detection.

**Resolution:**
- In `python/database/migration/parity.py` (`_verify_decision_log_domain`):
  - Maintained `matched_projected_ids: Set[int] = set()`.
  - When matching legacy decisions against projected decisions, each matched projection index is recorded.
  - In the reverse pass, any projected decision whose index is not in `matched_projected_ids` is flagged as `UNEXPECTED_RECORD`.
  - Recursive `deep_equal()` is used to compare nested `votes` structures containing Decimals and floats.

---

## 5. Dedicated Regression Test Suite (`TestStage15D111CorrectiveRegressions`)

The dedicated regression test class was added to `tests/test_database_migration.py` containing 15 test cases:

```python
class TestStage15D111CorrectiveRegressions(BaseMigrationTestCase):
    test_parity_rejects_unexplained_projected_timestamp_when_source_missing
    test_parity_accepts_explicit_caller_default_timestamp
    test_nullable_review_timestamp_must_remain_null
    test_trade_lesson_review_timestamp_persisted_on_insert
    test_trade_lesson_review_timestamp_conflict_detected
    test_case_c_rejects_non_dict_subdecision
    test_case_c_rejects_null_subdecision
    test_case_c_rejects_empty_invalid_subdecision
    test_case_c_imports_every_valid_subdecision
    test_structured_only_watchlist_parity
    test_structured_only_scout_categories_parity
    test_mixed_legacy_and_structured_watchlist_no_double_count
    test_mixed_legacy_and_structured_scout_no_double_count
    test_decision_projection_duplicate_identity_detected
    test_case_c_duplicate_subdecision_conflict_rejected
```

---

## 6. Test Suite Execution & Arithmetic Breakdown

All test suites were executed sequentially. Timings and exact test counts are reported below:

| Test Command | Discovered | Passed | Skipped | Failed | Errors | Duration |
|---|---|---|---|---|---|---|
| `python -m unittest tests.test_database_migration -v` | 111 | 111 | 0 | 0 | 0 | 7.880s |
| `python -m unittest tests.test_database_core -v` | 115 | 115 | 0 | 0 | 0 | 5.401s |
| `python -m unittest tests.test_database_repositories -v` | 254 | 254 | 0 | 0 | 0 | 18.868s |
| `python -m unittest tests.test_database_projections -v` | 54 | 54 | 0 | 0 | 0 | 3.812s |
| `python -m unittest tests.test_database_backup_concurrency -v` | 74 | 73 | 1* | 0 | 0 | 7.703s |
| `python -m unittest discover -s tests -v` | 631 | 626 | 1* | 0 | 4** | 46.085s |
| `python -m pip check` | N/A | Pass | 0 | 0 | 0 | 1.850s |
| `python -m py_compile` (7 modified files) | 7 | 7 | 0 | 0 | 0 | 0.810s |

### Test Arithmetic & Skip Documentation

- **Total Database Subsystem Tests:** 111 + 115 + 254 + 54 + 73 = **607 tests executed and passed**.
- **Documented Platform Skip (*):**
  `tests.test_database_backup_concurrency.TestStage15A21BackupLockingAndConcurrency.test_r1_posix_existing_target_restoration_strictly_refused` is gated with `@unittest.skipUnless(sys.platform != 'win32', "POSIX-only test")`. On Windows, this test is safely skipped.
- **Documented Non-Database Environment Errors (**):**
  Four tests in full test discovery failed with `ModuleNotFoundError` during module import because optional web/scraping packages are not installed in this clean offline environment:
  1. `test_investment_regression` (`yfinance`)
  2. `test_permissions` (`yfinance`)
  3. `test_watchlist_endpoints` (`yfinance`)
  4. `test_tool_dispatch` (`requests`)  
  These tests reside outside the database subsystem and have no relation to SQLite SSoT or migration functionality.

---

## 7. Real Legacy-Source Audit Reconciliation (R6)

The real local legacy files were inspected in the execution environment. Their presence, byte sizes, calculated SHA-256 hashes, and record counts are:

| File Path | Status | Size (Bytes) | SHA-256 Hash | Record Inventory |
|---|---|---|---|---|
| `context/ssot.json` | **VERIFIED** | 219 | `0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da` | 1 position (`SPY`), 2 watched tickers (`GOOG`, `AMZN`), cash: `None` |
| `context/trade_lessons.json` | **VERIFIED** | 11,738 | `0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463` | 24 trade lessons (`RULE_01` to `RULE_24`, all unreviewed) |
| `context/decision_log.json` | **VERIFIED** | 9,691 | `757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2` | 2 entries (Entry 0: Case B session `SPY`; Entry 1: Case C deliberation with 5 sub-decisions: `UMAC`, `RCAT`, `PLTR`, `TSLA`, `MSFT`) |
| `config.json` | **VERIFIED** | 2,117 | `10c005efa15940d9c503aae62bbcb9a195ddbe249387c8335a4cb0f836064ee3` | 5 scout categories in `SCOUT_TICKER_MAP` (25 tickers total) |

> [!NOTE]
> **GitHub Web View Reconciliation:**
> The legacy files `context/ssot.json`, `context/trade_lessons.json`, and `context/decision_log.json` are tracked in `.gitignore` on branch `main` to protect local operational state from accidental remote repository exposure. They exist physically on disk in the local workspace, and their hashes and contents match the audit values exactly.

---

## 8. Protected-File Invariance Verification (8 Files)

All eight authoritative application state files were verified using direct binary SHA-256 calculation. All match expected hashes:

| File Path | Verified SHA-256 Hash | Invariance Status |
|---|---|---|
| `context/ssot.json` | `0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da` | **INVARIANT** |
| `context/trade_lessons.json` | `0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463` | **INVARIANT** |
| `context/decision_log.json` | `757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2` | **INVARIANT** |
| `context/config.json` | `30ea2b67dd0ecac4b16e3e726c9644948119729023d382c385301f9db72099d5` | **INVARIANT** |
| `context/user_config.json` | `88e00f3ef6609eba02066cdfb3750152bfa2b97667aceed5cfba30b5db607183` | **INVARIANT** |
| `context/agent_logs/agent-events.jsonl` | `58a830fe4a59a02bb16d33df10c1a09b3e76a2b6250377e26ee37a8d73d8bc3d` | **INVARIANT** |
| `config.json` | `10c005efa15940d9c503aae62bbcb9a195ddbe249387c8335a4cb0f836064ee3` | **INVARIANT** |
| `gem_trading_rules/rules.md` | `04ea36c0762de30c841bb25ac35bdffeb6845cccfba19a9b94d343743c075056` | **INVARIANT** |

---

## 9. Production Safety & Database Absence Confirmation

Direct filesystem inspection confirms that production databases remain absent:

```powershell
python -c "import os; print('context/gem.db exists:', os.path.exists('context/gem.db')); print('cache/market_data.db exists:', os.path.exists('cache/market_data.db'))"
# context/gem.db exists: False
# cache/market_data.db exists: False
```

- Zero production migration, initialization, or runtime cutover occurred.
- All migration imports and parity verifications were conducted strictly in isolated, disposable, temporary SQLite databases.
- No trading rules, financial formulas, portfolio logic, or execution policies were modified.

---

## 10. Remaining Limitations & Boundaries

1. **Production Cutover Deferred:** Stage 1.5-D and its corrective iterations establish offline migration and parity verification capability only. Live production cutover and runtime dual-write or read-through remain deferred to future stages.
2. **Offline Dependency Boundary:** Tests requiring `yfinance` or `requests` remain gated behind network/package requirements and were not altered.
3. **No Stage 1.5-E or 1.6 Activity:** **Stage 1.5-E and Stage 1.6 have NOT been started.** Execution terminates immediately upon delivery of this report, awaiting explicit human authorization.

---

## 11. Completion Sign-Off & Suggested Git Commit

In accordance with the Agent Governance Framework, zero autonomous Git write operations (`git commit`, `git push`, branch switching) were executed.

### Suggested Commit Message for Human Repository Owner:

```text
fix(database): Stage 1.5-D.1.1.1 migration parity completeness & review timestamp persistence

- Enforce complete and explicit nullable/default timestamp parity across all 6 domains
- Relax trade_lessons.last_reviewed_at_utc column constraint to nullable in schema
- Extend TradeLessonRepository.upsert_lesson to preserve genuine None for unreviewed lessons
- Fail closed on malformed Case C sub-decisions with entry index and identity tracking
- Normalize structured legacy input representations in parity verifier without double-counting
- Implement collision-proof reverse decision projection checks using matched ID set
- Add 15 dedicated regression tests in TestStage15D111CorrectiveRegressions (111 migration tests passing)
```

