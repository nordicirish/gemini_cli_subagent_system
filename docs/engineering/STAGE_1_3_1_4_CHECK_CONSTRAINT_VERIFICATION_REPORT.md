# Stage 1.3.1.4 Completion Report: SQLite CHECK Constraint Enforcement Verification & Schema Inventory Hardening

**Repository:** `nordicirish/gemini_cli_subagent_system`  
**Branch:** `main`  
**Baseline HEAD:** `65efce1b3b77d11c1bf1410ec649775de1b4437d`  
**Predecessor:** Stage 1.3.1.3 (`1d3344b925412fb9e2db27d01c28a7ff767b1cee`)  
**Stage:** 1.3.1.4  
**Standard:** Agent Governance Framework (`.agents/AGENTS.md`, Protocols 01–06)  
**Verification Date:** 2026-11-08  
**Scope:** Remediate normalized DDL substring matching bypass vulnerability, establish canonical tokenized CHECK constraint verification with anti-bypass scanning, harden user table inventory checks against unauthorized tables, and verify read-only verification invariance before Stage 1.4.

---

## 1. Verified Baseline HEAD

- **Repository:** `nordicirish/gemini_cli_subagent_system`
- **Branch:** `main`
- **Baseline Commit SHA:** `65efce1b3b77d11c1bf1410ec649775de1b4437d`
- **Working Tree State at Baseline Inception:** Clean working tree, verified via `git rev-parse HEAD` and `git status --short`.
- **Git Ownership Rule:** The human repository owner holds exclusive authority over all Git commits, pushes, and branch operations. All Stage 1.3.1.4 implementation changes remain strictly uncommitted in the local working tree.

---

## 2. Pre-Implementation Findings and Reproductions

Prior to code modifications, an adversarial analysis of Stage 1.3.1.3's structural verifier (`SchemaMigrator._verify_schema_structural_integrity()`) revealed two vulnerabilities:

### Finding 1: Unconditional CHECK Constraint Bypass via Substring Matching (High Severity)
- **Defect:** Stage 1.3.1.3 verified mandatory CHECK constraints by verifying that required substring patterns (e.g. `currency IN ('EUR', 'USD')`, `amount NOT LIKE '%.%' OR length(...) <= 2`) existed within the table DDL string. An adversary or corrupted migration could retain all required substring patterns while appending an unconditional bypass such as `OR 1=1`, `OR TRUE`, or `OR 1`.
- **Reproduction:** In a test SQLite database, `portfolio_cash` was recreated with `amount` CHECK constraint modified to append `OR 1=1`. When `verify_core_schema_integrity(conn)` was executed under Stage 1.3.1.3, it **passed with zero errors** because all expected substring patterns remained present in the DDL. Malformed records such as negative numbers or alphabetic strings could then be written directly to `portfolio_cash.amount`.

### Finding 2: Lack of Exact User Table Inventory Verification (Medium Severity)
- **Defect:** The Stage 1.3.1.3 verifier validated that expected tables existed, but did not compute `unexpected_tables = actual_user_tables - expected_user_tables`. As a result, unauthorized or rogue user-created tables could exist in the database without failing verification.
- **Reproduction:** Created an unauthorized user table `unauthorized_user_table` in a core SQLite database. When `verify_core_schema_integrity(conn)` was executed under Stage 1.3.1.3, it **passed with zero errors**.

---

## 3. Architecture & Technical Remediation

### 3.1 SQL Expression Tokenization & Canonical Normalization
To eliminate the vulnerability of substring matching while avoiding heavy third-party SQL parsing dependencies, Stage 1.3.1.4 introduced an embedded, standard-library SQL tokenizer and canonical normalizer in `SchemaMigrator`:
- **`extract_check_expressions(ddl_sql: str) -> List[str]`**: Strips SQL comments (preserving `--` within string literals), extracts the table body between outermost parentheses, locates standalone `CHECK` keywords, and traverses matching parentheses with quote-escape awareness (`''`) to extract all raw `CHECK (...)` clauses.
- **`tokenize_sql_expression(expr: str) -> List[str]`**: Tokenizes expressions into canonical tokens, preserving string literals verbatim, standardizing standard SQL keywords (`AND`, `OR`, `NOT`, `GLOB`, `LIKE`, `IN`, `IS`, `NULL`, `CHECK`, `UPPER`, `TRIM`, `LENGTH`, `SUBSTR`, `INSTR`, `TRUE`, `FALSE`) to uppercase, normalizing multi-character operators (`<=`, `>=`, `!=`, `<>`), and normalizing identifiers and column names to lowercase.
- **`normalize_check_expression(expr: str) -> str`**: Joins token sequences with single spaces to produce a canonical representation insensitive to indentation, line breaks, or arbitrary whitespace variations.

### 3.2 Canonical CHECK Constraint Inventory & Set Equivalence
Authoritative dictionaries were established in `python/database/schema.py`:
- **`EXPECTED_CORE_CHECK_CONSTRAINTS`**: 17 canonical CHECK constraints across all 10 core tables.
- **`EXPECTED_CACHE_CHECK_CONSTRAINTS`**: 8 canonical CHECK constraints across all 2 cache tables.

The verifier enforces:
1. **Anti-Bypass Scanning:** Every extracted CHECK expression is tokenized and scanned against `UNCONDITIONAL_BYPASS_PATTERNS` (`OR 1 = 1`, `OR TRUE`, `OR 1`, `OR ( 1 = 1 )`, `OR ( TRUE )`, `OR ( 1 )`, `OR 0 = 0`, `OR ( 0 = 0 )`, `OR 'A' = 'A'`, `OR ( 'A' = 'A' )`). Any presence immediately raises `SchemaIntegrityError`.
2. **Exact Count Equivalence:** The number of normalized CHECK constraints on the table must exactly match the authoritative count.
3. **Exact Canonical Set Equivalence:** `set(expected_checks) == set(actual_normalized_checks)`. Any missing or altered CHECK constraint immediately raises `SchemaIntegrityError`.

### 3.3 Exact User Table Inventory Enforcement
- Implemented exact user table set comparison in Step 0 of `_verify_schema_structural_integrity`:
  ```python
  actual_user_tables = set(cls.get_table_names(conn))
  expected_user_tables = set(expected_tables.keys())
  ```
- Any unauthorized user table raises `SchemaIntegrityError` detailing the unexpected table name(s).
- SQLite internal tables (e.g. `sqlite_sequence`, `sqlite_stat1`) are filtered out via `WHERE type='table' AND name NOT LIKE 'sqlite_%'` in `get_table_names()`, ensuring internal SQLite housekeeping objects never trigger false positives.

### 3.4 Read-Only Verification Invariance & Fail-Closed Guarantee
- Verified that `verify_core_schema_integrity()` and `verify_cache_schema_integrity()` perform zero DML or DDL writes.
- Verified that verification operates seamlessly across read-only connections (`file:...?mode=ro`), preserving byte-for-byte SHA-256 hash invariance of database files across both passing and failing verification paths.
- Verified zero auto-repair behavior: failing verifications never alter or mutate the underlying database.

---

## 4. Complete Authoritative Schema Inventory

### 4.1 Canonical CHECK Constraints Summary (25 Total)

| Profile | Table Name | Constraint Count | Canonical Expressions |
|---|---|---:|---|
| **Core** | `database_metadata` | 1 | `id = 1` |
| **Core** | `portfolio_cash` | 2 | `currency IN ( 'EUR' , 'USD' )`<br>`amount NOT GLOB '*[^0-9.]*' AND amount NOT GLOB '*.*.*' AND amount NOT LIKE '%.%.%' AND amount NOT GLOB '.*' AND amount NOT GLOB '*.' AND amount NOT GLOB '0[0-9]*' AND LENGTH ( amount ) > 0 AND ( amount NOT LIKE '%.%' OR LENGTH ( SUBSTR ( amount , INSTR ( amount , '.' ) + 1 ) ) <= 2 )` |
| **Core** | `portfolio_positions` | 4 | `ticker = UPPER ( TRIM ( ticker ) ) AND LENGTH ( ticker ) > 0`<br>`shares` (exact 4-decimal constraint)<br>`wac_eur` (exact 4-decimal constraint)<br>`wac_usd` (`IS NULL OR` exact 4-decimal constraint) |
| **Core** | `watchlist` | 2 | `ticker = UPPER ( TRIM ( ticker ) ) AND LENGTH ( ticker ) > 0`<br>`status IN ( 'ACTIVE' , 'PAUSED' , 'REMOVED' )` |
| **Core** | `scout_categories` | 1 | `LENGTH ( TRIM ( category_name ) ) > 0` |
| **Core** | `trade_lessons` | 4 | `rule_code = UPPER ( TRIM ( rule_code ) ) AND LENGTH ( rule_code ) > 0`<br>`LENGTH ( TRIM ( directive_text ) ) > 0`<br>`category IN ( 'MANDATE' , 'EXECUTION' , 'STRUCTURAL' , 'RISK' , 'HEURISTIC' )`<br>`status IN ( 'ACTIVE' , 'DEPRECATED' , 'SUSPENDED' )` |
| **Core** | `decision_log` | 1 | `ticker IS NULL OR ( ticker = UPPER ( TRIM ( ticker ) ) AND LENGTH ( ticker ) > 0 )` |
| **Core** | `financial_changelog` | 0 | (No explicit CHECK constraints) |
| **Core** | `owner_approvals` | 1 | `status IN ( 'PENDING' , 'CONSUMED' , 'EXPIRED' , 'REVOKED' )` |
| **Core** | `audit_events` | 1 | `LENGTH ( event_id ) = 32 AND event_id NOT GLOB '*[^0-9a-f]*'` |
| **Cache** | `cache_metadata` | 1 | `id = 1` |
| **Cache** | `daily_bars` | 7 | `ticker = UPPER ( TRIM ( ticker ) ) AND LENGTH ( ticker ) > 0`<br>`LENGTH ( bar_date ) = 10`<br>`open` (exact 4-decimal constraint)<br>`high` (exact 4-decimal constraint)<br>`low` (exact 4-decimal constraint)<br>`close` (exact 4-decimal constraint)<br>`volume >= 0` |

---

## 5. Adversarial Testing & Verification Evidence

Fifteen new adversarial unit tests were added to [`tests/test_database_core.py`](file:///c:/github/gemini_cli_subagent_system/tests/test_database_core.py) across three dedicated test cases:

### 5.1 Adversarial CHECK Bypass Tests (`TestCheckConstraintEnforcementAndBypassPrevention`)
1. `test_check_bypass_or_1_equals_1_rejected_core`: Appending `OR 1=1` to `portfolio_cash.amount` fails closed with `SchemaIntegrityError`.
2. `test_check_bypass_or_1_equals_1_rejected_cache`: Appending `OR 1=1` to `daily_bars.open` fails closed with `SchemaIntegrityError`.
3. `test_check_bypass_or_true_rejected`: Appending `OR TRUE` (case-insensitive) fails closed with `SchemaIntegrityError`.
4. `test_check_bypass_or_1_rejected`: Appending `OR 1` fails closed with `SchemaIntegrityError`.
5. `test_check_bypass_parenthesized_rejected`: Parameterised subtests verifying `OR ( 1 = 1 )`, `OR (1=1)`, `OR (TRUE)`, `OR (0=0)`, `OR ('A'='A')` all fail closed with `SchemaIntegrityError`.
6. `test_check_bypass_case_and_whitespace_rejected`: Unusual whitespace, tabs, and linebreaks (e.g. `\noR \t 1 = 1`) fail closed with `SchemaIntegrityError`.
7. `test_individual_decimal_field_bypass_rejected`: Tampering with individual decimal fields (`shares`, `wac_eur`, `wac_usd`, `open`, `high`, `low`, `close`) fails closed with `SchemaIntegrityError`.
8. `test_check_constraint_count_mismatch_rejected`: Adding or dropping a CHECK constraint fails closed with `SchemaIntegrityError` (count mismatch).
9. `test_check_constraint_semantic_tampering_rejected`: Modifying enum values (e.g. adding `GBP` to `portfolio_cash.currency`) fails closed with `SchemaIntegrityError`.

### 5.2 Schema Inventory Hardening Tests (`TestSchemaInventoryHardening`)
10. `test_unexpected_table_rejected_core`: Creating an unauthorized user table in core database raises `SchemaIntegrityError` detecting unexpected user table(s).
11. `test_unexpected_table_rejected_cache`: Creating an unauthorized user table in cache database raises `SchemaIntegrityError` detecting unexpected user table(s).
12. `test_reinitialize_existing_with_unexpected_table_fails_closed`: Calling `initialize_core_schema` on an existing database containing an unauthorized table fails closed.
13. `test_internal_sqlite_tables_ignored`: Verifies that internal SQLite tables (`sqlite_stat1` after `ANALYZE`) do not trigger false positives.

### 5.3 Read-Only & Fail-Closed Tests (`TestSchemaVerificationReadOnlyAndFailClosed`)
14. `test_verification_is_read_only_and_preserves_hashes`: Computes SHA-256 hash before and after verification over read-only connections (`?mode=ro`) on valid and corrupted databases, proving 100% byte invariance.
15. `test_verification_does_not_auto_repair`: Verifies that failed verification never auto-repairs, deletes, or mutates database objects.

---

## 6. Automated Test Suite Execution Results

All commands executed using virtual environment interpreter (`.\.venv\Scripts\python.exe`):

1. **Database Core Test Suite:**
   ```bash
   .\.venv\Scripts\python.exe -m unittest tests.test_database_core -v
   ```
   **Result:** `Ran 70 tests in 2.796s — OK (70/70 PASS, 0 FAIL, 0 ERROR)`
   * 15 new Stage 1.3.1.4 tests passed seamlessly.
   * Total database tests expanded from 55 to 70.

2. **Full Repository Regression Suite:**
   ```bash
   .\.venv\Scripts\python.exe -m unittest discover tests -v
   ```
   **Result:** `Ran 124 tests in 28.864s — OK (124/124 PASS, 0 FAIL, 0 ERROR)`
   * `test_atomic_io` (15/15 PASS)
   * `test_audit_logger` (4/4 PASS)
   * `test_database_core` (70/70 PASS)
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

| File Path | Baseline SHA-256 | Post-Test SHA-256 | Match Status |
|---|---|---|---|
| `context/ssot.json` | `0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da` | `0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da` | **MATCH (INVARIANT)** |
| `context/trade_lessons.json` | `0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463` | `0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463` | **MATCH (INVARIANT)** |
| `context/decision_log.json` | `757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2` | `757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2` | **MATCH (INVARIANT)** |
| `context/config.json` | `30ea2b67dd0ecac4b16e3e726c9644948119729023d382c385301f9db72099d5` | `30ea2b67dd0ecac4b16e3e726c9644948119729023d382c385301f9db72099d5` | **MATCH (INVARIANT)** |
| `context/agent_logs/agent-events.jsonl` | `58a830fe4a59a02bb16d33df10c1a09b3e76a2b6250377e26ee37a8d73d8bc3d` | `58a830fe4a59a02bb16d33df10c1a09b3e76a2b6250377e26ee37a8d73d8bc3d` | **MATCH (INVARIANT)** |
| `config.json` | `10c005efa15940d9c503aae62bbcb9a195ddbe249387c8335a4cb0f836064ee3` | `10c005efa15940d9c503aae62bbcb9a195ddbe249387c8335a4cb0f836064ee3` | **MATCH (INVARIANT)** |

---

## 8. Confirmation of No Production Databases Created

Filesystem inspection confirms:
- `context/gem.db` exists: **False**
- `context/gem.db-wal` exists: **False**
- `context/gem.db-shm` exists: **False**
- `cache/market_data.db` exists: **False**
- `cache/market_data.db-wal` exists: **False**

All operations were conducted exclusively in memory (`:memory:`) or within isolated temporary directories.

---

## 9. Changed-File Inventory

| File | Status | Lines Changed | Description |
|---|---|---:|---|
| `python/database/schema.py` | Modified | +308 / -4 | Added `EXPECTED_CORE_CHECK_CONSTRAINTS` (17 checks) and `EXPECTED_CACHE_CHECK_CONSTRAINTS` (8 checks); added `UNCONDITIONAL_BYPASS_PATTERNS`; implemented `extract_check_expressions()`, `tokenize_sql_expression()`, and `normalize_check_expression()`; enhanced `_verify_schema_structural_integrity()` to enforce exact user table inventory, anti-bypass scanning, and exact canonical CHECK set equivalence. |
| `tests/test_database_core.py` | Modified | +498 / -0 | Added `TestCheckConstraintEnforcementAndBypassPrevention` (9 tests), `TestSchemaInventoryHardening` (4 tests), and `TestSchemaVerificationReadOnlyAndFailClosed` (2 tests). Total tests expanded from 55 to 70. |

Zero changes made to financial rules, trading logic, Fibonacci calculations, or application configuration files.

---

## 10. Remaining Limitations or Accepted Exceptions

1. **Deterministic Canonical Token Matching:** Rather than pulling in an external third-party SQL parser, the verifier tokenizes SQL expressions and compares canonical token streams. This guarantees that any logical tampering (e.g. `OR 1=1`, modified constants, altered scales, modified identifiers) changes the token stream and fails verification, while remaining impervious to formatting, indentation, and case variations.
2. **Schema Version Invariance:** The schema version remains `1`. No schema migration or version bump was required.

---

## 11. Explicit Independent Verification Results

- Verified that all 15 adversarial bypass and tampering scenarios fail closed with descriptive `SchemaIntegrityError` exceptions.
- Verified that existing valid core and cache schemas initialize and verify cleanly with 100% equivalence.
- Verified that schema verification operates on read-only database connections and never mutates database files or attempts self-repair.

---

## 12. Recommendation on Stage 1.4 Readiness

With the completion of Stage 1.3.1.4, the SQLite core database infrastructure possesses:
1. **Unconditional bypass immunity:** `OR 1=1`, `OR TRUE`, `OR 1`, parenthesized expressions, and scale alterations are detected and rejected fail-closed.
2. **Hardened table inventory:** Unauthorized user tables are rejected; SQLite internal tables are safely handled.
3. **Read-only verification invariance:** Schema checks can run over read-only connections without modifying a single byte on disk.
4. **Exhaustive automated verification:** 70 database unit tests and 124 full regression tests pass cleanly.

**Recommendation:** The repository is fully prepared and certified for Stage 1.4 (Repository Layer Implementation).

---

## 13. Mandatory Acceptance Matrix (Gates G1–G28)

| Gate | Requirement | Status | Verification Evidence |
|---|---|---|---|
| **G1** | Baseline HEAD verified | **PASS** | Verified baseline HEAD `65efce1b3b77d11c1bf1410ec649775de1b4437d` on `main`. |
| **G2** | Existing Stage 1.3.1.3 schema inventory completed | **PASS** | Complete inventory of 12 tables, columns, indexes, and 25 CHECK constraints documented. |
| **G3** | All eight financial columns have complete mandatory constraint verification | **PASS** | All 8 decimal columns verified against canonical tokenized definitions and anti-bypass scanning. |
| **G4** | Cash scale constraint removal or weakening detected | **PASS** | `test_cash_scale_constraint_removal_rejected` and `test_individual_decimal_field_bypass_rejected` pass. |
| **G5** | Share/WAC scale constraint removal or weakening detected | **PASS** | `test_shares_scale_constraint_removal_rejected` and `test_individual_decimal_field_bypass_rejected` pass. |
| **G6** | OHLC scale constraint removal or weakening detected | **PASS** | `test_ohlc_scale_constraint_removal_rejected` and `test_check_bypass_or_1_equals_1_rejected_cache` pass. |
| **G7** | Multiple-decimal-point constraint removal detected | **PASS** | `test_multi_dot_constraint_removal_rejected` passes. |
| **G8** | Malformed decimal constraint removal detected | **PASS** | `test_malformed_decimal_constraint_removal_rejected` passes. |
| **G9** | Foreign-key inventory completed | **PASS** | Explicit inventory confirms 0 foreign keys across all core and cache tables. |
| **G10** | Foreign-key definitions verified where applicable | **PASS** | `PRAGMA foreign_key_list` verified; unauthorized FKs fail closed (`test_unexpected_foreign_key_rejected`). |
| **G11** | Exact expected column sets verified | **PASS** | Missing and unexpected columns fail closed (`test_unexpected_column_rejected`). |
| **G12** | Declared types, nullability, PKs and mandatory defaults verified | **PASS** | All column attributes, types, nullability, defaults, and ordering verified fail closed. |
| **G13** | Index column definitions and ordering verified | **PASS** | `PRAGMA index_info` verified across all 7 user indexes. |
| **G14** | Index uniqueness verified | **PASS** | `test_index_uniqueness_mismatch_rejected` passes. |
| **G15** | Partial-index status verified | **PASS** | `test_partial_index_substitution_rejected` passes. |
| **G16** | Damaged schemas fail closed | **PASS** | All adversarial corruption and bypass scenarios raise `SchemaIntegrityError`. |
| **G17** | Valid Stage 1.3 schema passes | **PASS** | Standard canonical schema passes verification without warnings or errors. |
| **G18** | Future schema rejection preserved | **PASS** | `test_unknown_future_schema_version_rejected` and `test_cache_schema_future_version_rejected` pass. |
| **G19** | No automatic schema repair or migration | **PASS** | Verified in `test_verification_does_not_auto_repair`. |
| **G20** | Database tests pass | **PASS** | 70/70 database core unit tests pass in 2.796s. |
| **G21** | Full repository tests pass | **PASS** | 124/124 full repository tests pass in 28.864s. |
| **G22** | Compilation passes | **PASS** | `compileall -q python tests` exited with code 0. |
| **G23** | `pip check` passes | **PASS** | Confirmed `No broken requirements found.` |
| **G24** | Six authoritative state-file hashes unchanged | **PASS** | 100% byte-for-byte SHA-256 match across all 6 files. |
| **G25** | No production databases created | **PASS** | `context/gem.db` and `cache/market_data.db` verified absent. |
| **G26** | Independent verification documented | **PASS** | Documented in Section 11 of this report. |
| **G27** | No investment strategy or financial logic changed | **PASS** | Pure database verification infrastructure; zero financial code altered. |
| **G28** | No Git history or publishing operations performed | **PASS** | Changes left uncommitted in working tree for owner review. |

---

## 14. Final Verdict

### **STAGE 1.3.1.4 COMPLETE**

All 28 verification gates (G1 through G28) have been satisfied with verified, reproducible evidence. The SQLite schema integrity verification is hardened against unconditional CHECK constraint bypasses and unauthorized user tables. The system is certified ready for Stage 1.4.

