# Stage 1.3.1.3 Completion Report: SQLite Schema Integrity Verification Completion & Independent Assurance

**Repository:** `nordicirish/gemini_cli_subagent_system`  
**Branch:** `main`  
**Baseline HEAD:** `1d3344b925412fb9e2db27d01c28a7ff767b1cee`  
**Predecessor:** Stage 1.3.1.2 (`689a7933f1f098a22e13762bb4d77651d6aac2d1`)  
**Stage:** 1.3.1.3  
**Standard:** Agent Governance Framework (`.agents/AGENTS.md`, Protocols 01–06)  
**Verification Date:** 2026-11-08  
**Scope:** Complete structural verification for financial CHECK constraints, foreign-key definitions, exact column sets/defaults/ordering, and index uniqueness/partial status prior to Stage 1.4.

---

## 1. Verified Baseline HEAD

- **Repository:** `nordicirish/gemini_cli_subagent_system`
- **Branch:** `main`
- **Baseline Commit SHA:** `1d3344b925412fb9e2db27d01c28a7ff767b1cee`
- **Working Tree State at Baseline Inception:** Clean working tree, verified via `git rev-parse HEAD` and `git status --short`.
- **Git Ownership Rule:** The human repository owner holds exclusive authority over all Git commits, pushes, and branch operations. All Stage 1.3.1.3 implementation changes remain uncommitted in the local working tree.

---

## 2. Pre-Implementation Findings and Reproductions

Prior to code modifications, an exhaustive comparison between the canonical DDL statements and the Stage 1.3.1.2 integrity verifiers revealed six specific structural verification gaps. Each gap was independently reproduced using isolated temporary SQLite databases:

### Gap 1: Financial Cash Scale Constraint Removal
- **Defect:** `MANDATORY_CORE_TABLE_CONSTRAINTS["portfolio_cash"]` checked multiple-dot and non-numeric rules, but omitted the scale constraint pattern (`amount NOT LIKE '%.%' OR length(substr(amount, instr(amount, '.') + 1)) <= 2`).
- **Reproduction:** Recreated `portfolio_cash` without the scale check. `SchemaMigrator.verify_core_schema_integrity(conn)` passed with **zero errors**.

### Gap 2: Portfolio Positions Scale and Non-Numeric Check Removal
- **Defect:** `MANDATORY_CORE_TABLE_CONSTRAINTS["portfolio_positions"]` only checked `ticker` trimming and multiple-dot wildcards (`*.*.*`), omitting the scale constraints (`<= 4`) on `shares`, `wac_eur`, and `wac_usd`, as well as `NOT GLOB '*[^0-9.]*'`.
- **Reproduction:** Recreated `portfolio_positions` with scale checks and non-numeric checks removed. Verification passed with **zero errors**.

### Gap 3: Market Data OHLC Scale and Multi-Dot Constraint Removal
- **Defect:** `MANDATORY_CACHE_TABLE_CONSTRAINTS["daily_bars"]` omitted scale checks (`<= 4`) across `open`, `high`, `low`, and `close`, and omitted multi-dot wildcards on `high`, `low`, and `close`.
- **Reproduction:** Recreated `daily_bars` without OHLC scale constraints. `SchemaMigrator.verify_cache_schema_integrity(conn)` passed with **zero errors**.

### Gap 4: Unauthorized Additional Columns (Exact Column Set Bypasses)
- **Defect:** The verifier computed `missing_cols = set(expected_cols.keys()) - set(actual_cols.keys())`, but did not compute `unexpected_cols = set(actual_cols.keys()) - set(expected_cols.keys())`.
- **Reproduction:** Executed `ALTER TABLE portfolio_cash ADD COLUMN unauthorized_column TEXT;`. Verification passed with **zero errors**.

### Gap 5: Missing or Altered Default Values & Column Ordering
- **Defect:** `EXPECTED_CORE_TABLE_DEFS` checked `(declared_type, notnull, pk)`, but never validated `default_value` or physical column ordering (`cid`).
- **Reproduction:** Recreated `portfolio_cash` without `DEFAULT 'PRIMARY'` on `account_id` or reordered `currency` before `account_id`. Verification passed with **zero errors**.

### Gap 6: Index Uniqueness and Partial Index Substitution
- **Defect:** `EXPECTED_CORE_INDEX_DEFS` checked `(target_table, indexed_columns)` using `PRAGMA index_info`, but never queried `PRAGMA index_list` to inspect `unique` or `partial`.
- **Reproduction:** Substituted `idx_decision_session` with a `UNIQUE` index or `idx_audit_timestamp` with a partial index (`WHERE action_category = 'FINANCIAL'`). Verification passed with **zero errors**.

---

## 3. Complete Authoritative Schema Inventory

### 3.1 Table and Column Inventory
The Stage 1.3 schema defines 12 authoritative tables (10 core, 2 cache):

| Profile | Table Name | Columns (Order, Declared Type, NOT NULL, PK Ordinal, Default Value) |
|---|---|---|
| **Core** | `database_metadata` | `id` (INTEGER, NULL, PK 1, None), `schema_version` (INTEGER, NOT NULL, PK 0, None), `db_revision` (INTEGER, NOT NULL, PK 0, '0'), `created_at_utc` (TEXT, NOT NULL, PK 0, None), `last_updated_at_utc` (TEXT, NOT NULL, PK 0, None) |
| **Core** | `portfolio_cash` | `account_id` (TEXT, NOT NULL, PK 1, "'PRIMARY'"), `currency` (TEXT, NOT NULL, PK 2, None), `amount` (TEXT, NOT NULL, PK 0, None), `last_updated_at_utc` (TEXT, NOT NULL, PK 0, None) |
| **Core** | `portfolio_positions` | `account_id` (TEXT, NOT NULL, PK 1, "'PRIMARY'"), `ticker` (TEXT, NOT NULL, PK 2, None), `shares` (TEXT, NOT NULL, PK 0, None), `wac_eur` (TEXT, NOT NULL, PK 0, None), `wac_usd` (TEXT, NULL, PK 0, None), `last_updated_at_utc` (TEXT, NOT NULL, PK 0, None) |
| **Core** | `watchlist` | `ticker` (TEXT, NOT NULL, PK 1, None), `status` (TEXT, NOT NULL, PK 0, "'ACTIVE'"), `added_at_utc` (TEXT, NOT NULL, PK 0, None), `notes` (TEXT, NULL, PK 0, None) |
| **Core** | `scout_categories` | `id` (INTEGER, NULL, PK 1, None), `category_name` (TEXT, NOT NULL, PK 0, None), `description` (TEXT, NULL, PK 0, None), `tickers_json` (TEXT, NOT NULL, PK 0, "'[]'"), `updated_at_utc` (TEXT, NOT NULL, PK 0, None) |
| **Core** | `trade_lessons` | `rule_code` (TEXT, NOT NULL, PK 1, None), `directive_text` (TEXT, NOT NULL, PK 0, None), `category` (TEXT, NOT NULL, PK 0, None), `status` (TEXT, NOT NULL, PK 0, "'ACTIVE'"), `created_at_utc` (TEXT, NOT NULL, PK 0, None), `last_reviewed_at_utc` (TEXT, NOT NULL, PK 0, None) |
| **Core** | `decision_log` | `id` (INTEGER, NULL, PK 1, None), `session_id` (TEXT, NOT NULL, PK 0, None), `ticker` (TEXT, NULL, PK 0, None), `action_proposed` (TEXT, NOT NULL, PK 0, None), `decision_outcome` (TEXT, NOT NULL, PK 0, None), `votes_json` (TEXT, NOT NULL, PK 0, "'{}'"), `transcript_text` (TEXT, NULL, PK 0, None), `timestamp_utc` (TEXT, NOT NULL, PK 0, None) |
| **Core** | `financial_changelog` | `id` (INTEGER, NULL, PK 1, None), `correlation_id` (TEXT, NOT NULL, PK 0, None), `actor_role` (TEXT, NOT NULL, PK 0, None), `operation_name` (TEXT, NOT NULL, PK 0, None), `target_resource` (TEXT, NOT NULL, PK 0, None), `approval_digest` (TEXT, NULL, PK 0, None), `before_state_json` (TEXT, NOT NULL, PK 0, None), `after_state_json` (TEXT, NOT NULL, PK 0, None), `timestamp_utc` (TEXT, NOT NULL, PK 0, None) |
| **Core** | `owner_approvals` | `id` (INTEGER, NULL, PK 1, None), `approval_id` (TEXT, NOT NULL, PK 0, None), `operation_name` (TEXT, NOT NULL, PK 0, None), `target_resource` (TEXT, NOT NULL, PK 0, None), `payload_digest` (TEXT, NOT NULL, PK 0, None), `granted_by` (TEXT, NOT NULL, PK 0, "'Owner'"), `consumed_at_utc` (TEXT, NULL, PK 0, None), `status` (TEXT, NOT NULL, PK 0, None), `created_at_utc` (TEXT, NOT NULL, PK 0, None) |
| **Core** | `audit_events` | `event_id` (TEXT, NOT NULL, PK 1, None), `timestamp_utc` (TEXT, NOT NULL, PK 0, None), `action_category` (TEXT, NOT NULL, PK 0, None), `status` (TEXT, NOT NULL, PK 0, None), `agent_role` (TEXT, NOT NULL, PK 0, None), `tool_name` (TEXT, NULL, PK 0, None), `permission_decision` (TEXT, NOT NULL, PK 0, None), `approval_status` (TEXT, NOT NULL, PK 0, None), `payload_hash` (TEXT, NULL, PK 0, None), `raw_event_json` (TEXT, NOT NULL, PK 0, None) |
| **Cache** | `cache_metadata` | `id` (INTEGER, NULL, PK 1, None), `schema_version` (INTEGER, NOT NULL, PK 0, None), `created_at_utc` (TEXT, NOT NULL, PK 0, None), `last_updated_at_utc` (TEXT, NOT NULL, PK 0, None) |
| **Cache** | `daily_bars` | `ticker` (TEXT, NOT NULL, PK 1, None), `bar_date` (TEXT, NOT NULL, PK 2, None), `open` (TEXT, NOT NULL, PK 0, None), `high` (TEXT, NOT NULL, PK 0, None), `low` (TEXT, NOT NULL, PK 0, None), `close` (TEXT, NOT NULL, PK 0, None), `volume` (INTEGER, NOT NULL, PK 0, None), `indicators_json` (TEXT, NOT NULL, PK 0, "'{}'"), `updated_at_utc` (TEXT, NOT NULL, PK 0, None) |

### 3.2 Inventory of All Eight Authoritative Decimal Columns
All 8 decimal columns store exact numerical strings as `TEXT` (no `REAL`/`FLOAT`):

| Table | Column | Permitted Scale | Mandatory CHECK Constraints |
|---|---|---:|---|
| `portfolio_cash` | `amount` | 2 | `NOT GLOB '*[^0-9.]*'`, `NOT GLOB '*.*.*'`, `NOT LIKE '%.%.%'`, `NOT GLOB '.*'`, `NOT GLOB '*.'`, `NOT GLOB '0[0-9]*'`, `length(amount) > 0`, `amount NOT LIKE '%.%' OR length(substr(amount, instr(amount, '.') + 1)) <= 2` |
| `portfolio_positions` | `shares` | 4 | `NOT GLOB '*[^0-9.]*'`, `NOT GLOB '*.*.*'`, `NOT LIKE '%.%.%'`, `NOT GLOB '.*'`, `NOT GLOB '*.'`, `NOT GLOB '0[0-9]*'`, `length(shares) > 0`, `shares NOT LIKE '%.%' OR length(substr(shares, instr(shares, '.') + 1)) <= 4` |
| `portfolio_positions` | `wac_eur` | 4 | Same lexical and scale constraints as `shares` |
| `portfolio_positions` | `wac_usd` | 4 | `wac_usd IS NULL OR` (same lexical and scale constraints as `shares`) |
| `daily_bars` | `open` | 4 | `NOT GLOB '*[^0-9.]*'`, `NOT GLOB '*.*.*'`, `NOT LIKE '%.%.%'`, `NOT GLOB '.*'`, `NOT GLOB '*.'`, `NOT GLOB '0[0-9]*'`, `length(open) > 0`, `open NOT LIKE '%.%' OR length(substr(open, instr(open, '.') + 1)) <= 4` |
| `daily_bars` | `high` | 4 | Same lexical and scale constraints as `open` |
| `daily_bars` | `low` | 4 | Same lexical and scale constraints as `open` |
| `daily_bars` | `close` | 4 | Same lexical and scale constraints as `open` |

### 3.3 Foreign-Key Inventory
- **Authoritative Inventory:** In the Stage 1.3 architecture, all 10 core tables and 2 cache tables intentionally define **zero foreign keys** (`REFERENCES`).
- **Enforcement:** `EXPECTED_CORE_FOREIGN_KEYS` and `EXPECTED_CACHE_FOREIGN_KEYS` explicitly map every table to an empty list `[]`. The verifier queries `PRAGMA foreign_key_list(table_name)`. Any foreign key present on any table is flagged as an unauthorized foreign-key mismatch, raising `SchemaIntegrityError`.

### 3.4 Index Inventory
All user-defined indexes are non-unique, full (non-partial) indexes:

| Index Name | Target Table | Indexed Columns | Unique? | Partial? |
|---|---|---|---|---|
| `idx_audit_timestamp` | `audit_events` | `['timestamp_utc']` | False | False |
| `idx_audit_action` | `audit_events` | `['action_category']` | False | False |
| `idx_changelog_corr` | `financial_changelog` | `['correlation_id']` | False | False |
| `idx_decision_session` | `decision_log` | `['session_id']` | False | False |
| `idx_decision_ticker` | `decision_log` | `['ticker']` | False | False |
| `idx_bars_ticker` | `daily_bars` | `['ticker']` | False | False |
| `idx_bars_date` | `daily_bars` | `['bar_date']` | False | False |

---

## 4. Work Performed Under Workstreams A–H

### Workstream A: Complete Financial CHECK Constraint Verification
- Expanded `MANDATORY_CORE_TABLE_CONSTRAINTS` and `MANDATORY_CACHE_TABLE_CONSTRAINTS` to include every mandatory rule:
  - Cash scale limit (`<= 2`).
  - Shares and WAC scale limits (`<= 4`).
  - Market-data OHLC scale limits (`<= 4`).
  - Lexical rules: `NOT GLOB '*[^0-9.]*'`, `NOT GLOB '*.*.*'`, `NOT LIKE '%.%.%'`, `NOT GLOB '.*'`, `NOT GLOB '*.'`, `NOT GLOB '0[0-9]*'`, `length() > 0`.
  - Non-numeric fields: `bar_date` 10-char format, `volume >= 0`, uppercase ticker formatting.
- DDL normalization compresses whitespace (`" ".join(row[0].split())`) prior to pattern matching, ensuring robustness against harmless whitespace/formatting differences while fail-closing if any constraint expression is altered or removed.

### Workstream B: Foreign-Key Structural Verification
- Implemented `PRAGMA foreign_key_list(table_name)` inspection inside `_verify_schema_structural_integrity`.
- Extracts `from`, `table`, `to`, `on_update`, and `on_delete`, and compares the sorted list against the authoritative foreign-key inventory.
- Preserves the runtime `PRAGMA foreign_keys = ON` profile configuration.

### Workstream C: Exact Column Definition Verification
- Enforced exact column set equality:
  - `missing_cols = set(expected_col_names) - set(actual_col_names)` (Fails if any expected column is missing).
  - `unexpected_cols = set(actual_col_names) - set(expected_col_names)` (Fails if any unexpected column is added).
  - `actual_col_names != expected_col_names` (Fails if column ordering does not match).
- Inspects `PRAGMA table_info` for:
  - Declared type equality (`TEXT`, `INTEGER`).
  - NOT NULL flag equality.
  - Primary key ordinal equality.
  - Default value equality (`default_value == exp_default`).

### Workstream D: Complete Index Definition Verification
- Queries `sqlite_master` to ensure index exists and targets the expected table.
- Queries `PRAGMA index_list(expected_tbl)` to verify:
  - Uniqueness (`bool(entry[2]) == exp_unique`).
  - Partial status (`bool(entry[4]) == exp_partial`).
- Queries `PRAGMA index_info(index_name)` to verify exact indexed columns and order.
- Queries all tables to ensure **zero unexpected user indexes** exist (`origin == 'c'`).

### Workstream E: Adversarial Schema Corruption Tests
- Added 15 comprehensive unit tests to [`tests/test_database_core.py`](file:///c:/github/gemini_cli_subagent_system/tests/test_database_core.py) verifying that every corruption scenario triggers `SchemaIntegrityError`.

### Workstream F: Fail-Closed Behaviour
- In every failure path, `_verify_schema_structural_integrity` raises `SchemaIntegrityError` detailing the database profile, affected table, and property mismatch.
- Zero automatic repair, zero schema migration, zero silent fallback.

### Workstream G: Regression and Persistence Isolation
- Full test suite passed without regression. Authoritative state file hashes verified 100% invariant.

### Workstream H: Independent Verification
- Verified that all 15 corruption scenarios fail closed, while the valid Stage 1.3.1.2 schema passes without warnings or errors.

---

## 5. Before-and-After Adversarial Corruption Test Evidence

| Corruption Scenario | Target Property | Stage 1.3.1.2 Behavior | Stage 1.3.1.3 Behavior | Evidence / Exception Raised |
|---|---|---|---|---|
| Cash scale check removed | `portfolio_cash.amount` scale <= 2 | **Passed silently (Gap 1)** | **Fails closed** | `SchemaIntegrityError: Core schema integrity failure: table 'portfolio_cash' is missing mandatory constraint pattern: 'amount NOT LIKE '%.%' OR length(substr(amount, instr(amount, '.') + 1)) <= 2'` |
| Shares scale check removed | `portfolio_positions.shares` scale <= 4 | **Passed silently (Gap 2)** | **Fails closed** | `SchemaIntegrityError: Core schema integrity failure: table 'portfolio_positions' is missing mandatory constraint pattern: 'shares NOT LIKE '%.%' OR length(substr(shares, instr(shares, '.') + 1)) <= 4'` |
| OHLC scale check removed | `daily_bars.open` scale <= 4 | **Passed silently (Gap 3)** | **Fails closed** | `SchemaIntegrityError: Cache schema integrity failure: table 'daily_bars' is missing mandatory constraint pattern: 'open NOT LIKE '%.%' OR length(substr(open, instr(open, '.') + 1)) <= 4'` |
| Multi-dot check removed | `amount NOT GLOB '*.*.*'` | **Passed silently** | **Fails closed** | `SchemaIntegrityError: Core schema integrity failure: table 'portfolio_cash' is missing mandatory constraint pattern: 'amount NOT GLOB '*.*.*''` |
| Non-numeric check removed | `amount NOT GLOB '*[^0-9.]*'` | **Passed silently** | **Fails closed** | `SchemaIntegrityError: Core schema integrity failure: table 'portfolio_cash' is missing mandatory constraint pattern: 'amount NOT GLOB '*[^0-9.]*''` |
| Unexpected FK added | `portfolio_positions` references `watchlist` | **Passed silently (Gap 6)** | **Fails closed** | `SchemaIntegrityError: Core schema integrity failure: table 'portfolio_positions' foreign-key mismatch: expected [], found [{'from': 'ticker', 'table': 'watchlist', ...}]` |
| Unexpected column added | Extra column on `portfolio_cash` | **Passed silently (Gap 4)** | **Fails closed** | `SchemaIntegrityError: Core schema integrity failure: table 'portfolio_cash' has unexpected column(s): ['unauthorized_column']` |
| Column order reversed | `currency` before `account_id` | **Passed silently (Gap 5)** | **Fails closed** | `SchemaIntegrityError: Core schema integrity failure: table 'portfolio_cash' column ordering mismatch: expected ['account_id', 'currency', ...], found ['currency', 'account_id', ...]` |
| Default value removed | `account_id` lacks `DEFAULT 'PRIMARY'` | **Passed silently (Gap 5)** | **Fails closed** | `SchemaIntegrityError: Core schema integrity failure: column 'portfolio_cash.account_id' default value mismatch: expected 'PRIMARY', found None` |
| Unique index substitution | `idx_decision_session` made `UNIQUE` | **Passed silently (Gap 6)** | **Fails closed** | `SchemaIntegrityError: Core schema integrity failure: index 'idx_decision_session' uniqueness mismatch: expected unique=False, found unique=True` |
| Partial index substitution | `idx_audit_timestamp` with `WHERE` | **Passed silently (Gap 6)** | **Fails closed** | `SchemaIntegrityError: Core schema integrity failure: index 'idx_audit_timestamp' partial status mismatch: expected partial=False, found partial=True` |
| Unexpected user index added | `idx_cash_amount` created | **Passed silently** | **Fails closed** | `SchemaIntegrityError: Core schema integrity failure: table 'portfolio_cash' has unexpected user index 'idx_cash_amount'` |

---

## 6. Test Execution Results

All commands executed using the virtual environment interpreter (`.\.venv\Scripts\python.exe`):

1. **Database Core Unit Tests:**
   ```bash
   .\.venv\Scripts\python.exe -m unittest tests.test_database_core -v
   ```
   **Result:** `Ran 55 tests in 1.995s — OK (55/55 PASS, 0 FAIL, 0 ERROR)`
   * 15 new adversarial verification tests added, all passing.

2. **Full Repository Regression Suite:**
   ```bash
   .\.venv\Scripts\python.exe -m unittest discover tests -v
   ```
   **Result:** `Ran 109 tests in 18.451s — OK (109/109 PASS, 0 FAIL, 0 ERROR)`
   * `test_atomic_io` (15/15 PASS)
   * `test_audit_logger` (4/4 PASS)
   * `test_database_core` (55/55 PASS)
   * `test_investment_regression` (7/7 PASS)
   * `test_permissions` (14/14 PASS)
   * `test_tool_dispatch` (8/8 PASS)
   * `test_watchlist_endpoints` (6/6 PASS)

3. **Compilation & Packaging Integrity:**
   ```bash
   .\.venv\Scripts\python.exe -m compileall -q python tests   # 0 syntax errors
   .\.venv\Scripts\pip.exe check                             # No broken requirements found.
   ```

---

## 7. Cryptographic Persistence Isolation Results (SHA-256)

All 6 authoritative application state files remain 100% byte-for-byte identical to their pre-implementation baseline:

| File Path | Baseline SHA-256 (Stage 1.3.1.2) | Post-Test SHA-256 (Stage 1.3.1.3) | Match Status |
|---|---|---|---|
| `context/ssot.json` | `0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da` | `0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da` | **MATCH (INVARIANT)** |
| `context/trade_lessons.json` | `0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463` | `0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463` | **MATCH (INVARIANT)** |
| `context/decision_log.json` | `757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2` | `757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2` | **MATCH (INVARIANT)** |
| `context/config.json` | `30ea2b67dd0ecac4b16e3e726c9644948119729023d382c385301f9db72099d5` | `30ea2b67dd0ecac4b16e3e726c9644948119729023d382c385301f9db72099d5` | **MATCH (INVARIANT)** |
| `context/agent_logs/agent-events.jsonl` | `58a830fe4a59a02bb16d33df10c1a09b3e76a2b6250377e26ee37a8d73d8bc3d` | `58a830fe4a59a02bb16d33df10c1a09b3e76a2b6250377e26ee37a8d73d8bc3d` | **MATCH (INVARIANT)** |
| `config.json` | `10c005efa15940d9c503aae62bbcb9a195ddbe249387c8335a4cb0f836064ee3` | `10c005efa15940d9c503aae62bbcb9a195ddbe249387c8335a4cb0f836064ee3` | **MATCH (INVARIANT)** |

---

## 8. Confirmation of No Production Databases Created

Filesystem check confirms:
- `context/gem.db` exists: **False**
- `cache/market_data.db` exists: **False**

All test routines executed against in-memory databases (`:memory:`) or transient files in `tempfile.TemporaryDirectory()`.

---

## 9. Changed-File Inventory

| File | Status | Description |
|---|---|---|
| `python/database/schema.py` | Modified | Added default values and ordering to `EXPECTED_CORE_TABLE_DEFS` and `EXPECTED_CACHE_TABLE_DEFS`; defined `EXPECTED_CORE_FOREIGN_KEYS` and `EXPECTED_CACHE_FOREIGN_KEYS`; updated `EXPECTED_CORE_INDEX_DEFS` and `EXPECTED_CACHE_INDEX_DEFS` with uniqueness and partial flags; populated complete mandatory CHECK constraint patterns across all tables; implemented `_verify_schema_structural_integrity` shared verifier. |
| `tests/test_database_core.py` | Modified | Added 15 new adversarial unit tests verifying fail-closed detection of scale removal, multi-dot removal, foreign-key tampering, unexpected columns, reordered columns, missing defaults, unique/partial index substitutions, and unexpected indexes. |

Zero changes made to application logic, trading rules, portfolio algorithms, or configuration files.

---

## 10. Remaining Limitations or Accepted Exceptions

1. **Normalized DDL Substring Matching:** SQLite does not expose parsed ASTs for table-level `CHECK` constraints via PRAGMA. The verifier inspects whitespace-normalized DDL strings from `sqlite_master.sql`. While robust against formatting and indentation variations, it verifies exact constraint expressions rather than semantic mathematical equivalence.
2. **Empty Foreign-Key Inventory:** Because the Stage 1.3 schema intentionally omits `REFERENCES` clauses, the foreign-key verifier actively enforces that no foreign keys exist on any table. If future stages introduce foreign keys, their definitions must be codified in `EXPECTED_CORE_FOREIGN_KEYS`.

---

## 11. Explicit Independent Verification Results

- Verified that all 15 corruption scenarios fail closed with descriptive `SchemaIntegrityError` exceptions.
- Verified that the valid Stage 1.3.1.2 schema initializes and passes verification without requiring any database migrations or version bumps (`CURRENT_SCHEMA_VERSION = 1`).
- Verified that schema initialisation remains strictly idempotent and transactional.

---

## 12. Recommendation on Stage 1.4 Readiness

With the completion of Stage 1.3.1.3, all structural integrity verification gaps identified during independent review have been resolved. The embedded SQLite persistence infrastructure is verified to be:
1. **Durably isolated:** No live state files touched; zero production databases created.
2. **Structurally guarded:** All tables, columns, ordering, types, defaults, foreign keys, indexes, and CHECK constraints verified fail-closed.
3. **Numerically verified:** All 8 decimal columns protected against multi-dot bypasses and scale violations.

**Recommendation:** The repository is fully prepared and recommended for Stage 1.4 (Repository Layer Implementation).

---

## 13. Mandatory Acceptance Matrix (Gates G1–G28)

| Gate | Requirement | Status | Verification Evidence |
|---|---|---|---|
| **G1** | Baseline HEAD verified | **PASS** | Verified HEAD `1d3344b925412fb9e2db27d01c28a7ff767b1cee` on `main`. |
| **G2** | Existing Stage 1.3.1.2 schema inventory completed | **PASS** | Full inventory of 12 tables, columns, indexes, and constraints documented. |
| **G3** | All eight financial columns have complete mandatory constraint verification | **PASS** | All 8 decimal columns have exhaustive DDL constraint checks. |
| **G4** | Cash scale constraint removal detected | **PASS** | `test_cash_scale_constraint_removal_rejected` passes. |
| **G5** | Share/WAC scale constraint removal detected | **PASS** | `test_shares_scale_constraint_removal_rejected` passes. |
| **G6** | OHLC scale constraint removal detected | **PASS** | `test_ohlc_scale_constraint_removal_rejected` passes. |
| **G7** | Multiple-decimal-point constraint removal detected | **PASS** | `test_multi_dot_constraint_removal_rejected` passes. |
| **G8** | Malformed decimal constraint removal detected | **PASS** | `test_malformed_decimal_constraint_removal_rejected` passes. |
| **G9** | Foreign-key inventory completed | **PASS** | Explicit inventory confirms 0 foreign keys across all core and cache tables. |
| **G10** | Foreign-key definitions verified where applicable | **PASS** | `PRAGMA foreign_key_list` verified; unexpected FKs fail closed. |
| **G11** | Exact expected column sets verified | **PASS** | Missing and unexpected columns fail closed (`test_unexpected_column_rejected`). |
| **G12** | Declared types, nullability, PKs and mandatory defaults verified | **PASS** | All column attributes and default values verified fail closed. |
| **G13** | Index column definitions and ordering verified | **PASS** | `PRAGMA index_info` verified across all 7 indexes. |
| **G14** | Index uniqueness verified | **PASS** | `test_index_uniqueness_mismatch_rejected` passes. |
| **G15** | Partial-index status verified | **PASS** | `test_partial_index_substitution_rejected` passes. |
| **G16** | Damaged schemas fail closed | **PASS** | All 15 corruption scenarios raise `SchemaIntegrityError`. |
| **G17** | Valid Stage 1.3.1.2 schema passes | **PASS** | Standard schema passes without errors. |
| **G18** | Future schema rejection preserved | **PASS** | `test_unknown_future_schema_version_rejected` passes. |
| **G19** | No automatic schema repair or migration | **PASS** | Fail-closed exceptions raised without table drops or repairs. |
| **G20** | Database tests pass | **PASS** | 55/55 database core unit tests pass in 1.995s. |
| **G21** | Full repository tests pass | **PASS** | 109/109 full repository tests pass in 18.451s. |
| **G22** | Compilation passes | **PASS** | `compileall -q python tests` exited with code 0. |
| **G23** | `pip check` passes | **PASS** | Confirmed `No broken requirements found.` |
| **G24** | Six authoritative state-file hashes unchanged | **PASS** | 100% byte-for-byte SHA-256 match across all 6 files. |
| **G25** | No production databases created | **PASS** | `context/gem.db` and `cache/market_data.db` verified absent. |
| **G26** | Independent verification documented | **PASS** | Documented in Section 11 of this report. |
| **G27** | No investment strategy or financial logic changed | **PASS** | Pure persistence verification; zero financial code altered. |
| **G28** | No Git history or publishing operations performed | **PASS** | Changes left uncommitted in working tree for owner review. |

---

## 14. Final Verdict

### **STAGE 1.3.1.3 COMPLETE**

All 28 verification gates (G1 through G28) have been satisfied with verified, reproducible evidence. The SQLite schema integrity verification is complete, robust, and fail-closed. Stage 1.4 is recommended.

