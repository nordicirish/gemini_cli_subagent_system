# Stage 1.5-D.1.1.2.1 — Schema Migration Verification, Strict Field Parity & Representation Consolidation Corrective Report

**Repository:** `nordicirish/gemini_cli_subagent_system`  
**Branch:** `main`  
**Baseline HEAD:** `bdf27e8c305aad1c44a7a2f401abf0b43e1a9958`  
**Stage Type:** Narrowly Scoped Corrective Implementation & Regression Verification  
**Governance Standard:** GEM Governance Framework (`.agents/AGENTS.md`)  
**Execution Authority:** Human-supervised; implementation and verification only  
**Git Authority:** Exclusive human repository owner control (zero autonomous git commits/pushes)

---

## 1. Executive Summary

Stage 1.5-D.1.1.2.1 resolves all remaining findings from the independent review of Stage 1.5-D.1.1.2:

1. **F1 (High) — Schema Migration Verification Integrity & Transactional Rollback:**
   - Moved post-migration structural integrity verification (`cls.verify_core_schema_integrity(conn)`) inside `_migrate_core_v1_to_v2()` before `conn.execute("COMMIT;")`. If post-migration verification fails, the transaction rolls back atomically to version 1, leaving original tables, records, constraints, and metadata intact.
   - Secured cache schema version handling in `initialize_cache_schema()` with transactional safety (`BEGIN IMMEDIATE; ... COMMIT;`) and schema integrity verification before commit.
   - Verified that all 5 regression tests in `tests/test_database_core.py` under `TestStage15D112SchemaVersionUpgradeAndCompatibility` are implemented and passing.
2. **F2 (High) — Strict Field-Level Parity Without Truthiness Comparisons:**
   - Eliminated all remaining truthiness comparisons (`or "ACTIVE"`, `or "MANDATE"`, `or {}`, etc.) in `python/database/migration/parity.py`.
   - Introduced `_MISSING` sentinel to explicitly distinguish omitted fields from explicit `None`, explicit empty strings (`""`), or explicit zero values.
   - For decision log votes, distinguished omitted votes (defaults to `{}`) from explicit `None` (detects discrepancy against `{}`).
   - Added focused regression tests in `tests/test_database_migration.py` under `TestStage15D1121CorrectiveRegressions`.
3. **F3 (Medium) — Complementary Representation Consolidation & Order Invariance:**
   - Resolved the defect in `consolidate_scout_representations`, `consolidate_watchlist_representations`, `consolidate_position_representations`, and `consolidate_cash_representations` in `python/database/migration/contract.py` where a redundant second `if not records_equal(...)` check aborted complementary record merges.
   - Codified safe merge semantics using `_UNSPECIFIED` sentinel to distinguish absent fields from explicit `None` / empty values.
   - Enforced order invariance and fail-closed conflict detection raising `MigrationConflictError`.
   - Added comprehensive regression tests in `tests/test_database_migration.py` under `TestStage15D1121CorrectiveRegressions`.
4. **F4 (Medium) — Reconciled Authoritative File Hashes and Report Evidence:**
   - Recomputed SHA-256 hashes for all 8 protected authoritative files on disk. Confirmed 100% byte-for-byte match against expected baselines.
   - Replaced inaccurate tables from previous reports that referenced non-existent files with the complete set of 8 existing protected files.

---

## 2. Starting Git Baseline

- **Repository:** `nordicirish/gemini_cli_subagent_system`
- **Branch:** `main`
- **Baseline Commit SHA:** `bdf27e8c305aad1c44a7a2f401abf0b43e1a9958`
- **Verification:** Verified via `git log -1` on 2026-11-10. Working directory clean prior to Stage 1.5-D.1.1.2.1 modifications.

---

## 3. Finding F1 Resolution: Post-Migration Schema Verification & Rollback

### Root Cause
In Stage 1.5-D.1.1.2, `_migrate_core_v1_to_v2()` committed table recreation and metadata changes via `conn.execute("COMMIT;")` before executing `cls.verify_core_schema_integrity(conn)`. Consequently, if structural verification failed after table recreation, the upgraded table and metadata advance were already permanently committed, preventing automated rollback to version 1.

### Implementation
1. In [`python/database/schema.py`](file:///C:/github/gemini_cli_subagent_system/python/database/schema.py):
   - Moved `cls.verify_core_schema_integrity(conn)` directly into `_migrate_core_v1_to_v2(conn)` *before* `conn.execute("COMMIT;")`.
   - If verification raises `SchemaIntegrityError` or any other exception, the `except Exception:` block executes `conn.execute("ROLLBACK;")`, atomically restoring table `trade_lessons` to v1 with all original constraints and records.
   - Refactored `initialize_core_schema()` so fresh initialization verifies schema before `COMMIT;`, and the migration branch returns the upgraded version without redundant post-commit verification.
   - Secured `initialize_cache_schema()` by wrapping schema metadata advance inside an immediate transaction (`BEGIN IMMEDIATE; ... COMMIT;`) with `verify_cache_schema_integrity(conn)` verified prior to commit.
2. In [`tests/test_database_core.py`](file:///C:/github/gemini_cli_subagent_system/tests/test_database_core.py):
   - Confirmed implementation of `TestStage15D112SchemaVersionUpgradeAndCompatibility` covering:
     - `test_fresh_db_initializes_at_version_2`: Fresh database creates v2 schema with nullable `last_reviewed_at_utc`.
     - `test_v1_to_v2_upgrade_preserves_records_and_relaxes_constraint`: Upgrades v1 with existing records and relaxes constraint.
     - `test_v1_to_v2_upgrade_atomic_rollback_on_failure`: Injected verification failure rolls back atomically to v1, preserving records and constraints, and leaves no temporary migration tables.
     - `test_idempotent_reinitialization_of_v2_db`: Re-initialization of v2 database is completely idempotent.
     - `test_unsupported_versions_fail_closed`: Unsupported versions raise `SchemaVersionMismatchError`.

---

## 4. Finding F2 Resolution: Elimination of Truthiness Parity Comparisons

### Root Cause
In `python/database/migration/parity.py`, expressions such as `item.get("category") or "MANDATE"`, `leg_item.get("status") or "ACTIVE"`, `exp.get("votes") or {}`, and `p.get("account_id") or "PRIMARY"` evaluated truthiness. When legacy records explicitly specified empty strings (`""`), `None`, or empty collections, these expressions collapsed them into defaults, masking discrepancies between legacy and projected state.

### Implementation
1. Defined `_MISSING = object()` in [`python/database/migration/contract.py`](file:///C:/github/gemini_cli_subagent_system/python/database/migration/contract.py) and imported it into [`python/database/migration/parity.py`](file:///C:/github/gemini_cli_subagent_system/python/database/migration/parity.py).
2. Refactored field comparisons across all domains:
   - **Trade Lessons (`category` and `status`):**
     ```python
     raw_leg_cat = item.get("category", _MISSING)
     leg_cat = "MANDATE" if raw_leg_cat is _MISSING else item.get("category")
     raw_proj_cat = proj_l.get("category", _MISSING)
     proj_cat = "MANDATE" if raw_proj_cat is _MISSING else proj_l.get("category")
     if leg_cat != proj_cat: ...
     ```
     Explicit `""` and explicit `None` are preserved and detected as discrepancies if projected contains `"MANDATE"` or `"ACTIVE"`.
   - **Watchlist (`status`):**
     ```python
     raw_leg_status = leg_item.get("status", _MISSING)
     leg_status = "ACTIVE" if raw_leg_status is _MISSING else leg_item.get("status")
     raw_proj_status = proj_item.get("status", _MISSING)
     proj_status = "ACTIVE" if raw_proj_status is _MISSING else proj_item.get("status")
     if leg_status != proj_status: ...
     ```
     Explicit `""` or `None` status is preserved and detected as a discrepancy against `"ACTIVE"`.
   - **Decision Log (`votes`):**
     ```python
     raw_leg_votes = exp.get("votes", _MISSING)
     leg_votes = {} if raw_leg_votes is _MISSING else exp.get("votes")
     raw_proj_votes = matching.get("votes", _MISSING)
     proj_votes = {} if raw_proj_votes is _MISSING else matching.get("votes")
     if not deep_equal(leg_votes, proj_votes): ...
     ```
     Omitted votes field defaults to `{}`, while explicit `None` is preserved and correctly flags a discrepancy against `{}`.
   - **Positions & Cash (`account_id`, `timestamp`):**
     Replaced truthiness fallbacks with explicit `_MISSING` checks for `account_id` and explicit `is not None` checks for timestamp alternatives.

---

## 5. Finding F3 Resolution: Complementary Representation Consolidation & Order Invariance

### Root Cause
In `python/database/migration/contract.py`, the consolidation functions (`consolidate_scout_representations`, `consolidate_watchlist_representations`, `consolidate_position_representations`, `consolidate_cash_representations`) initially compared each field. However, right before the merge logic, they invoked `if not records_equal(prev, item, ...): raise MigrationConflictError(...)`.
Because complementary records naturally have differing unspecified fields (e.g. `description` present in `scout_categories` but absent from `SCOUT_TICKER_MAP`), `records_equal()` returned `False`. This raised `MigrationConflictError` and prevented complementary records from ever merging, rendering the merge logic dead code.

### Implementation
1. Defined `_UNSPECIFIED = object()` sentinel in [`python/database/migration/contract.py`](file:///C:/github/gemini_cli_subagent_system/python/database/migration/contract.py).
2. Updated representation parsers:
   - When a representation format does not provide a field (or omits it), the field is assigned `_UNSPECIFIED` rather than `None`.
   - For example:
     - `SCOUT_TICKER_MAP` assigns `description = _UNSPECIFIED`.
     - `scout_categories` assigns `description = str(entry["description"]).strip()` if `"description"` in entry else `_UNSPECIFIED`.
     - `watched_tickers` string lists assign `status = _UNSPECIFIED` and `notes = _UNSPECIFIED`.
     - `positions` without `wac_usd` assign `wac_usd = _UNSPECIFIED`.
3. Updated consolidation merge loops:
   - Specific field comparison:
     - If both `prev` and `incoming` specify a field (neither is `_UNSPECIFIED`) and their values differ: raise `MigrationConflictError` fail-closed.
     - If one specifies a value and the other is `_UNSPECIFIED`: merge the specified value into `prev`.
     - If both are `_UNSPECIFIED`: remain `_UNSPECIFIED`.
   - Removed the redundant `if not records_equal(...)` check that was aborting valid complementary merges.
   - After consolidating all entries for a domain, normalized any remaining `_UNSPECIFIED` values to canonical defaults (`None` for descriptions/notes/`wac_usd`, `"ACTIVE"` for watchlist status).
4. Order Invariance:
   - Because merge logic treats `_UNSPECIFIED` symmetrically, processing representation A before B produces the exact same consolidated output as processing B before A.

---

## 6. Finding F4 Resolution: Authoritative File Hash Re-Verification

### Root Cause
Previous corrective report documentation evaluated non-existent file paths (`context/state.json`, `context/system_instructions.md`, `context/macro_environment.json`, `context/audit_log.jsonl`) marked as "Absent", rather than auditing the complete set of 8 authoritative source files that actually exist in the repository.

### Recomputed SHA-256 Hashes
All 8 authoritative files exist on disk and were recomputed via SHA-256. All 8 hashes match the expected baseline commit exactly:

| Index | File Path | Status | SHA-256 Hash | Byte-for-Byte Match |
|---|---|---|---|---|
| 1 | `context/ssot.json` | Present | `0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da` | True |
| 2 | `context/trade_lessons.json` | Present | `0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463` | True |
| 3 | `context/decision_log.json` | Present | `757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2` | True |
| 4 | `context/config.json` | Present | `30ea2b67dd0ecac4b16e3e726c9644948119729023d382c385301f9db72099d5` | True |
| 5 | `context/user_config.json` | Present | `88e00f3ef6609eba02066cdfb3750152bfa2b97667aceed5cfba30b5db607183` | True |
| 6 | `context/agent_logs/agent-events.jsonl` | Present | `58a830fe4a59a02bb16d33df10c1a09b3e76a2b6250377e26ee37a8d73d8bc3d` | True |
| 7 | `config.json` | Present | `10c005efa15940d9c503aae62bbcb9a195ddbe249387c8335a4cb0f836064ee3` | True |
| 8 | `gem_trading_rules/rules.md` | Present | `04ea36c0762de30c841bb25ac35bdffeb6845cccfba19a9b94d343743c075056` | True |

---

## 7. Regression Test Suite Details

### `tests/test_database_core.py` (`TestStage15D112SchemaVersionUpgradeAndCompatibility`)
- `test_fresh_db_initializes_at_version_2`: Confirms fresh initialization sets schema version 2 and relaxes constraint.
- `test_v1_to_v2_upgrade_preserves_records_and_relaxes_constraint`: Verifies table rebuild from v1 to v2 preserves all data.
- `test_v1_to_v2_upgrade_atomic_rollback_on_failure`: Verifies that verification failure rolls back to v1 atomically.
- `test_idempotent_reinitialization_of_v2_db`: Confirms idempotent initialization of existing v2 database.
- `test_unsupported_versions_fail_closed`: Verifies unsupported versions fail closed.

### `tests/test_database_migration.py` (`TestStage15D1121CorrectiveRegressions`)
- `test_f2_trade_lessons_truthiness_elimination_parity`: Verifies `category` and `status` parity distinguishes `None` and `""` from missing fields.
- `test_f2_watchlist_status_truthiness_elimination_parity`: Verifies watchlist `status` parity distinguishes `None` and `""` from missing fields.
- `test_f2_decision_log_votes_truthiness_elimination_parity`: Verifies decision log `votes` parity distinguishes omitted votes (`{}`) from explicit `None`.
- `test_f3_scout_complementary_consolidation_and_order_invariance`: Verifies complementary scout consolidation merges tickers and descriptions, ensures order invariance, and detects conflicting descriptions.
- `test_f3_watchlist_complementary_consolidation_and_order_invariance`: Verifies complementary watchlist consolidation merges tickers, notes, and status, ensures order invariance, and detects conflicting status.
- `test_f3_position_complementary_consolidation_and_order_invariance`: Verifies complementary position consolidation merges `wac_usd`, ensures order invariance, and detects conflicting WAC amounts.
- `test_f3_cash_complementary_consolidation_and_order_invariance`: Verifies scalar cash (without timestamp) and structured cash (with timestamp) consolidate cleanly and order-invariantly, and detects conflicting cash amounts.

---

## 8. Verification & Test Suite Execution Results

All test suites were executed sequentially using `python -m unittest`:

| Test Suite | Tests Run | Passed | Failed | Skipped | Errors |
|---|---|---|---|---|---|
| `tests.test_database_core` | 120 | 120 | 0 | 0 | 0 |
| `tests.test_database_migration` | 130 | 130 | 0 | 0 | 0 |
| `tests.test_database_repositories` | 254 | 254 | 0 | 0 | 0 |
| `tests.test_database_projections` | 54 | 54 | 0 | 0 | 0 |
| `tests.test_database_backup_concurrency` | 74 | 73 | 0 | 1 | 0 |
| `test_atomic_io` | 15 | 15 | 0 | 0 | 0 |
| `test_audit_logger` | 4 | 4 | 0 | 0 | 0 |
| **Subtotal Database Subsystem** | **651** | **650** | **0** | **1** | **0** |
| Full Discovery (`unittest discover -s tests`) | 655 | 650 | 0 | 1 | 4 |

### Notes on Test Arithmetic:
- **1 Skipped:** `test_r1_posix_existing_target_restoration_strictly_refused` in `tests.test_database_backup_concurrency` (expected platform skip on Windows OS).
- **4 Discovery Errors:** Four legacy third-party network integration tests outside the database subsystem (`test_investment_regression`, `test_permissions`, `test_tool_dispatch`, `test_watchlist_endpoints`) fail import due to uninstalled optional network packages `requests` and `yfinance`. Verified unchanged from baseline.
- **Zero Failures or Errors across all 651 database and migration tests.**

---

## 9. Dependency & Compilation Verification

1. `python -m pip check`:
   ```
   No broken requirements found.
   ```
2. `python -m py_compile`:
   Executed across all modified files:
   - `python/database/schema.py`
   - `python/database/migration/contract.py`
   - `python/database/migration/parity.py`
   - `tests/test_database_core.py`
   - `tests/test_database_migration.py`
   Exit code 0 (all compiled cleanly with zero errors).

---

## 10. Modified Files Summary

| File | Change Description |
|---|---|
| [`python/database/schema.py`](file:///C:/github/gemini_cli_subagent_system/python/database/schema.py) | Moved `verify_core_schema_integrity(conn)` inside `_migrate_core_v1_to_v2()` before `COMMIT;` ensuring atomic rollback on failure; secured cache schema initialization in `BEGIN IMMEDIATE; ... COMMIT;`. |
| [`python/database/migration/contract.py`](file:///C:/github/gemini_cli_subagent_system/python/database/migration/contract.py) | Defined `_UNSPECIFIED` and `_MISSING` sentinels; refactored 4 consolidation functions to preserve complementary fields, removed redundant `records_equal` check, preserved fail-closed conflict detection, and ensured order invariance. |
| [`python/database/migration/parity.py`](file:///C:/github/gemini_cli_subagent_system/python/database/migration/parity.py) | Eliminated truthiness comparisons across watchlist status, trade lesson category/status, decision log votes, and position account IDs; preserved explicit empty strings and `None`. |
| [`tests/test_database_core.py`](file:///C:/github/gemini_cli_subagent_system/tests/test_database_core.py) | Confirmed implementation and passing status of all 5 schema upgrade and compatibility regression tests in `TestStage15D112SchemaVersionUpgradeAndCompatibility`. |
| [`tests/test_database_migration.py`](file:///C:/github/gemini_cli_subagent_system/tests/test_database_migration.py) | Added `TestStage15D1121CorrectiveRegressions` with 7 comprehensive regression tests covering F2 truthiness elimination and F3 complementary consolidation. |
| [`docs/engineering/STAGE_1_5_D_1_1_2_1_CORRECTIVE_REPORT.md`](file:///C:/github/gemini_cli_subagent_system/docs/engineering/STAGE_1_5_D_1_1_2_1_CORRECTIVE_REPORT.md) | Authored comprehensive corrective report documenting findings, root cause analysis, implementation details, file hash audit, and verified test results. |

---

## 11. Governance and Safety Statement

- **Production Isolation:** No writes were performed to production databases; `context/gem.db` and `cache/market_data.db` remain absent.
- **Trading Integrity:** No trading rules, sizing, portfolio allocations, or risk parameters were modified.
- **Git Authority:** No commits, pushes, or branch operations were executed. Git authority remains exclusively with the human repository owner.
- **Closeout:** Stage 1.5-D.1.1.2.1 is complete, verified, and ready for human repository owner review and sign-off.

