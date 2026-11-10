# Stage 1.5-C — Deterministic SSoT Projection Generator Report

**Repository:** `nordicirish/gemini_cli_subagent_system`  
**Branch:** `main`  
**Starting Baseline HEAD:** `5c35901c55f737b150523c3d318832c67967af4a` (Stage 1.5-B.1)  
**Governance Framework Standard:** Adapted from `tk-site-new` Engineering Governance  
**Authority:** Human Repository Owner. Autonomous commits, pushes, and production deployments are strictly forbidden.  
**Stage:** 1.5-C (Deterministic SSoT Projection Generator)  
**Status:** COMPLETE  

---

## 1. Executive Summary

Stage 1.5-C establishes an isolated, deterministic, read-only SQLite-to-JSON projection capability centered on `SSoTProjectionGenerator` within `python/database/projections.py`. This component provides the authoritative bridge between GEM's persistent SQLite database (`context/gem.db`) and its canonical Single Source of Truth (`ssot.json`) representation, without requiring runtime cutover or legacy file migration.

Key capabilities delivered in Stage 1.5-C:
1. **Isolated Projection Architecture:** Implemented `SSoTProjectionGenerator` with strict decoupling between SQLite snapshot reads, canonical in-memory projection construction, non-tabular section preservation, deterministic JSON serialization, and safe atomic file writing.
2. **Snapshot Read Isolation (WAL MVCC):** Establishes an explicit read transaction (`BEGIN;`) when called without an active transaction, reading all projection tables and `db_revision` at one point-in-time snapshot, and cleanly releasing the transaction (`ROLLBACK;`) without writing or incrementing revision. When called within an existing caller transaction, it operates non-intrusively without committing or rolling back caller work.
3. **Monotonic Revision Provenance:** Binds the database's monotonic `database_metadata.db_revision` directly into `_projection_metadata`, providing consumers with definitive state freshness verification.
4. **Canonical Exact Decimal Serialization:** All monetary amounts, WAC values, and share counts are serialized into exact canonical decimal strings via `decimal_to_canonical_str()`. Binary floating-point values are strictly forbidden in financial projections.
5. **Lossless Non-Tabular Section Preservation:** Externally managed analytical sections (`state_context`, `runtime_flags`, `macro_calendar_shield`, `forensic_intelligence`) present in a supplied `base_document` are preserved exactly via deep copy, while SQLite-owned domains are overwritten with authoritative database state. Malformed inputs fail closed with `ValidationError`.
6. **Fsync-Hardened Atomic File Writer:** Implemented `atomic_write_json()`, ensuring validation before touching disk, tempfile creation on the same filesystem, explicit flush + `fsync`, atomic `os.replace` (Windows-safe handle closure), and fail-safe temporary file cleanup.
7. **Comprehensive Verification:** 35 dedicated unit and failure-injection tests in `tests/test_database_projections.py`, 25-iteration deterministic stress verification, full repository discovery passing (532 discovered: 531 passed, 1 skipped platform POSIX test on Windows), 100% SHA-256 byte-for-byte invariance across all 8 authoritative files, and verified non-existence of production databases.

---

## 2. Baseline Verification

Initial inspection confirmed repository and working tree state prior to any modification:

- **Repository Root:** `c:\github\gemini_cli_subagent_system`
- **Branch:** `main`
- **Starting Baseline Commit:** `5c35901c55f737b150523c3d318832c67967af4a`
- **Working Tree:** Clean (`git status --short` returned 0 entries).
- **Production Database Existence:**
  - `context/gem.db`: `False` (absent)
  - `cache/market_data.db`: `False` (absent)

---

## 3. Projection Architecture & Implementation Summary

The projection capability is implemented in `python/database/projections.py` (with compatibility re-export in `python/database/projection.py` and `python/database/__init__.py`).

### 3.1 Separation of Responsibilities

```
                               ┌────────────────────────────────────────────────┐
                               │           SSoTProjectionGenerator              │
                               └───────────────────────┬────────────────────────┘
                                                       │
         ┌─────────────────────────┬───────────────────┴───────────────┬────────────────────────┐
         │                         │                                   │                        │
         ▼                         ▼                                   ▼                        ▼
┌──────────────────┐    ┌──────────────────────┐            ┌─────────────────────┐   ┌──────────────────┐
│  Snapshot Read   │    │ Canonical Projection │            │    Deterministic    │   │   Atomic File    │
│    Lifecycle     │    │   & Preservation     │            │    Serialization    │   │      Writer      │
├──────────────────┤    ├──────────────────────┤            ├─────────────────────┤   ├──────────────────┤
│ • BEGIN; / MVCC  │    │ • 6 SQLite Domains   │            │ • sort_keys=True    │   │ • Tempfile dir   │
│ • db_revision    │    │ • Non-Tabular DeepCP │            │ • Canonical Decimal │   │ • flush + fsync  │
│ • Non-intrusive  │    │ • Fail-closed check  │            │ • ensure_ascii=False│   │ • os.replace     │
│ • ROLLBACK exit  │    │ • mutable_state wrap │            │ • allow_nan=False   │   │ • Cleanup unlinks│
└──────────────────┘    └──────────────────────┘            └─────────────────────┘   └──────────────────┘
```

### 3.2 Public API Specification

```python
class SSoTProjectionGenerator:
    def __init__(self, db_manager: Optional[Any] = None): ...

    def generate(
        self,
        conn: sqlite3.Connection,
        base_document: Optional[Dict[str, Any]] = None,
        generation_timestamp: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Reads authoritative SQLite state and returns canonical SSoT projection dictionary."""

    def serialize(
        self,
        projection: Dict[str, Any],
        allow_floats: bool = False,
    ) -> str:
        """Normalizes and deterministically serializes projection dictionary to JSON."""

    def write_atomic(
        self,
        projection: Dict[str, Any],
        destination: Union[Path, str],
        allow_floats: bool = False,
    ) -> None:
        """Serializes and atomically writes projection to destination path."""

    def project_to_file(
        self,
        conn: sqlite3.Connection,
        destination: Union[Path, str],
        base_document: Optional[Dict[str, Any]] = None,
        generation_timestamp: Optional[str] = None,
        allow_floats: bool = False,
    ) -> Dict[str, Any]:
        """Convenience method executing generate() and write_atomic() in sequence."""

    def project_trade_lessons(self, conn: sqlite3.Connection) -> Dict[str, Any]:
        """Generates isolated trade_lessons projection matching context/trade_lessons.json shape."""

    def project_decision_log(self, conn: sqlite3.Connection) -> Dict[str, Any]:
        """Generates isolated decision_log projection matching context/decision_log.json shape."""
```

---

## 4. Projection Ownership & Table-to-JSON Mapping

### 4.1 SQLite-Owned Domains & Mappings

The generator queries authoritative state exclusively through domain repositories and schema metadata:

| Domain | SQLite Table | Repositories Used | SQLite Columns | Projected JSON Fields | Output Ordering |
|---|---|---|---|---|---|
| **Provenance** | `database_metadata` | `get_db_revision(conn)` / cursor | `schema_version`, `db_revision`, `last_updated_at_utc` | `_projection_metadata.schema_version`, `_projection_metadata.db_revision`, `_projection_metadata.db_last_updated_at_utc` | Single row (`id=1`) |
| **Cash Balances** | `portfolio_cash` | `CashRepository` | `account_id`, `currency`, `amount`, `last_updated_at_utc` | `unallocated_cash_eur` (`PRIMARY` EUR), `unallocated_cash_usd` (`PRIMARY` USD), `portfolio_cash: [{"account_id", "amount", "currency", "last_updated_at_utc"}]` | `account_id ASC, currency ASC` |
| **Positions** | `portfolio_positions` | `PositionRepository` | `account_id`, `ticker`, `shares`, `wac_eur`, `wac_usd`, `last_updated_at_utc` | `portfolio_snapshot: [{"account_id", "ticker", "shares", "wac", "wac_eur", "wac_usd", "last_updated_at_utc"}]` | `account_id ASC, ticker ASC` |
| **Watchlist** | `watchlist` | `WatchlistRepository` | `ticker`, `status`, `added_at_utc`, `notes` | `watched_tickers: ["TICKER", ...]` (`ACTIVE` status), `watchlist: [{"ticker", "status", "added_at_utc", "notes"}]` | `ticker ASC` |
| **Scout Categories** | `scout_categories` | `ScoutCategoryRepository` | `id`, `category_name`, `description`, `tickers_json`, `updated_at_utc` | `scout_categories: ["NAME", ...]`, `scout_category_records: [{"id", "category_name", "description", "tickers", "updated_at_utc"}]` | `category_name ASC` |
| **Trade Lessons** | `trade_lessons` | `TradeLessonRepository` | `rule_code`, `directive_text`, `category`, `status`, `created_at_utc`, `last_reviewed_at_utc` | `trade_lessons: [{"rule_code", "directive_text", "category", "status", "created_at_utc", "last_reviewed_at_utc"}]` | `rule_code ASC` |
| **Decision Log** | `decision_log` | `DecisionLogRepository` | `id`, `session_id`, `ticker`, `action_proposed`, `decision_outcome`, `votes_json`, `transcript_text`, `timestamp_utc` | `decision_log: [{"id", "session_id", "ticker", "action_proposed", "decision_outcome", "votes", "transcript_text", "timestamp_utc"}]` | `id ASC` (Chronological PK) |

### 4.2 Explicitly Excluded Operational Tables

Operational and cache tables are strictly forbidden from participating in the user-facing SSoT projection:
- `financial_changelog`: Internal audit trail for financial state transitions.
- `owner_approvals`: One-time authorization tokens and digests.
- `audit_events`: Security event ledger.
- `daily_bars` / `cache_metadata`: Reconstructible market data cache (`cache/market_data.db`).

---

## 5. Non-Tabular Section Preservation Policy

GEM's legacy `ssot.json` contains critical analytical context sections that are not modeled as relational tables in SQLite (e.g. `state_context`, `runtime_flags`, `macro_calendar_shield`, and inside `mutable_state`: `forensic_intelligence`, `active_orders`).

### 5.1 Preservation Invariants
1. **Explicit Key Partitioning:** Keys in `base_document` are partitioned into SQLite-owned keys (`SQLITE_OWNED_TOP_LEVEL_KEYS`) and external keys.
2. **Lossless Deep Copy:** All external top-level keys are deep-copied into the projection before applying SQLite-owned sections.
3. **`mutable_state` Assembly:** Within `mutable_state`, external keys (e.g. `forensic_intelligence`) are preserved via deep copy, while SQLite-owned keys (`portfolio_snapshot`, `unallocated_cash_eur`, `unallocated_cash_usd`, `watched_tickers`, `scout_categories`) are populated with authoritative SQLite state.
4. **Base Document Immutability:** `base_document` is never mutated in memory.
5. **Fail-Closed Validation:** If `base_document` is provided and is not a dictionary, or if `base_document["mutable_state"]` is present and is not a dictionary, the generator raises `ValidationError` immediately.

---

## 6. Snapshot & `db_revision` Consistency Guarantees

### 6.1 Transaction Lifecycle & WAL Isolation
- When `generate()` is called on a connection with no active transaction (`conn.in_transaction == False`), it begins an explicit read transaction via `conn.execute("BEGIN;")`. Under SQLite WAL mode, this establishes an immutable point-in-time read mark across all subsequent `SELECT` statements.
- In the `finally:` block, `conn.execute("ROLLBACK;")` releases the read transaction and drops the read mark, preventing dangling read locks or transaction leakage.
- When called on a connection with an active transaction (`conn.in_transaction == True`), the generator reads within the caller's established snapshot without executing `COMMIT` or `ROLLBACK`, leaving caller transaction ownership intact.

### 6.2 Provenance & Timestamp Handling
- `_projection_metadata.db_revision` captures the exact integer revision from `database_metadata` within the same snapshot.
- To prevent non-deterministic divergence across calls on unchanged database state, the generator distinguishes between `db_last_updated_at_utc` (read deterministically from the database) and `generation_timestamp`. An explicit `generated_at_utc` timestamp is included only if supplied by the caller; otherwise, identical database state produces byte-identical serialized JSON.

---

## 7. Decimal & Serialization Rules

### 7.1 Exact Decimal Serialization
- All numeric values originating from SQLite financial fields (`portfolio_cash.amount`, `portfolio_positions.shares`, `portfolio_positions.wac_eur`, `portfolio_positions.wac_usd`) are validated and held as `Decimal` instances.
- Normalization converts `Decimal` instances via `decimal_to_canonical_str()`, yielding canonical plain strings (e.g. `"2359.80"`, `"10.0000"`).
- Binary floating-point values are strictly rejected by default (`allow_floats=False`) with `TypeError` to enforce financial precision invariants.
- Nullable decimal fields (e.g. `wac_usd`) preserve `None` and serialize to JSON `null`.

### 7.2 Deterministic JSON Serialization
- Canonical JSON output is produced using:
  ```python
  json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
  ```
- All keys are recursively sorted.
- Whitespace is eliminated.
- Non-standard JSON values (`NaN`, `Infinity`) raise `ValueError`.

---

## 8. Atomic Writer Behavior & Safety

The standalone `atomic_write_json(destination, serialized_json)` function ensures filesystem integrity:
1. **Destination Validation:** Validates destination is not an existing directory (`IsADirectoryError`).
2. **Directory Creation:** Ensures destination parent directory exists (`mkdir(parents=True, exist_ok=True)`).
3. **Same-Filesystem Tempfile:** Uses `tempfile.NamedTemporaryFile` in `destination.parent` with prefix `.{filename}.` and suffix `.tmp` to guarantee same-filesystem atomicity.
4. **Flushing & Fsync:** Calls `handle.flush()` and `os.fsync(handle.fileno())` to force write-through to physical disk buffers.
5. **Windows-Safe Replacement:** The context manager closes the file handle before invoking `os.replace(temp_path, destination)`, preventing Windows file-lock errors.
6. **Fail-Safe Cleanup:** If serialization or writing fails before `os.replace`, the temporary file is unlinked in `finally:`, and the original destination file remains completely untouched.

---

## 9. Test Inventory & Execution Evidence

### 9.1 Focused Projection Test Suite (`tests/test_database_projections.py`)
Executed via: `.venv\Scripts\python.exe -m unittest tests/test_database_projections.py -v`

| Category | Test Function | Purpose / Validation | Exit Status | Runtime |
|---|---|---|---|---|
| **A. Completeness** | `test_all_approved_sqlite_domains_projected` | All 6 approved domains populated and structured | PASS | 0.04s |
| | `test_unintended_operational_tables_excluded` | Operational/cache tables excluded from SSoT | PASS | 0.03s |
| | `test_nullable_fields_preserved_as_null` | `wac_usd`, `notes`, `description`, `ticker` nulls | PASS | 0.04s |
| | `test_empty_tables_produce_empty_representation` | Empty DB produces `[]` and `"0.00"` | PASS | 0.02s |
| **B. Determinism** | `test_identical_output_for_unchanged_database` | Consecutive calls yield byte-identical output | PASS | 0.04s |
| | `test_insertion_order_invariance` | Reverse insertion produces identical sorted output | PASS | 0.03s |
| | `test_no_implicit_timestamps_cause_drift` | Unchanged DB produces identical metadata | PASS | 0.03s |
| | `test_explicit_generation_timestamp_supported` | Caller-supplied timestamp recorded | PASS | 0.03s |
| | `test_repeated_deterministic_projection_stress_loop` | 25 consecutive generations yield zero drift | PASS | 0.08s |
| **C. Decimal Integrity** | `test_exact_decimal_string_canonicalization` | Scale 4, zeros, large numbers canonicalized | PASS | 0.03s |
| | `test_float_rejected_by_default` | Floats in financial state raise `TypeError` | PASS | 0.01s |
| | `test_non_standard_floats_rejected` | `NaN` and `Infinity` raise `ValueError` | PASS | 0.01s |
| **D. Revision Consistency** | `test_projection_records_correct_db_revision` | `db_revision` provenance matches DB | PASS | 0.03s |
| | `test_successful_mutation_advances_projected_revision` | Mutation advances revision in projection | PASS | 0.03s |
| | `test_failed_or_rolled_back_mutation_does_not_advance_revision` | Rolled-back work does not advance revision | PASS | 0.03s |
| | `test_projection_itself_never_increments_revision` | `generate()` is strictly read-only on revision | PASS | 0.03s |
| **E. Transaction Ownership** | `test_generator_called_with_no_active_transaction` | `in_transaction` false before and after | PASS | 0.02s |
| | `test_generator_called_with_active_caller_transaction` | Caller transaction preserved and active | PASS | 0.03s |
| | `test_generator_does_not_commit_or_rollback_caller_work` | Uncommitted work visible, caller rolls back | PASS | 0.03s |
| | `test_closed_connection_fails_cleanly` | Closed connection raises error cleanly | PASS | 0.01s |
| | `test_none_connection_fails_cleanly` | `None` connection raises `ValidationError` | PASS | 0.01s |
| **F. Non-Tabular Preservation** | `test_preserves_external_top_level_sections` | External root keys preserved via deep copy | PASS | 0.04s |
| | `test_preserves_external_mutable_state_sections` | External mutable_state keys preserved | PASS | 0.04s |
| | `test_overwrites_sqlite_owned_sections` | Stale base doc sections overwritten | PASS | 0.03s |
| | `test_malformed_base_document_rejected` | Non-dict inputs raise `ValidationError` | PASS | 0.02s |
| | `test_base_document_not_mutated` | Base document memory identity invariant | PASS | 0.03s |
| **G. Atomic Writer** | `test_successful_write_produces_utf8_json` | Writes valid UTF-8 JSON to temp destination | PASS | 0.04s |
| | `test_existing_destination_replaced_atomically` | Existing file atomically overwritten | PASS | 0.04s |
| | `test_serialization_failure_leaves_destination_unchanged` | Error before write leaves destination intact | PASS | 0.01s |
| | `test_write_failure_preserves_destination_and_cleans_temp` | Injected write failure cleans up tempfile | PASS | 0.01s |
| | `test_replace_failure_preserves_destination_and_cleans_temp` | Injected replace failure cleans up tempfile | PASS | 0.01s |
| | `test_nested_destination_creates_directories` | Creates non-existent parent directories | PASS | 0.01s |
| | `test_directory_destination_raises_error` | Directory path raises `IsADirectoryError` | PASS | 0.01s |
| **H. Read-Only Invariants** | `test_projection_does_not_modify_any_database_table` | All table row counts before == after | PASS | 0.05s |
| | `test_isolated_domain_projection_methods` | `project_trade_lessons` and `decision_log` | PASS | 0.03s |

**Focused Suite Result:** `Ran 35 tests in 2.049s — OK`

---

### 9.2 Regression & Full Discovery Test Execution Summary

| Test Suite | Command | Discovered | Passed | Skipped | Failed | Errors | Runtime | Exit Code |
|---|---|---|---|---|---|---|---|---|
| **Focused Projections** | `.venv\Scripts\python.exe -m unittest tests/test_database_projections.py` | 35 | 35 | 0 | 0 | 0 | 2.05s | 0 |
| **Repository Suite** | `.venv\Scripts\python.exe -m unittest tests/test_database_repositories.py` | 254 | 254 | 0 | 0 | 0 | 17.04s | 0 |
| **Core Database** | `.venv\Scripts\python.exe -m unittest tests/test_database_core.py` | 115 | 115 | 0 | 0 | 0 | 5.00s | 0 |
| **Backup Concurrency** | `.venv\Scripts\python.exe -m unittest tests/test_database_backup_concurrency.py` | 74 | 73 | 1* | 0 | 0 | 7.12s | 0 |
| **Full Repository Discovery** | `.venv\Scripts\python.exe -m unittest discover -s tests` | 532 | 531 | 1* | 0 | 0 | 62.58s | 0 |

*\*Note: 1 test skipped in backup concurrency suite is the platform-gated POSIX `fcntl` locking test on Windows (`test_r5_04_posix_unlock_failure_injection`), accurately identified as skipped on win32.*

### 9.3 Package Integrity & Compilation

- **`pip check`:** `No broken requirements found.` (Exit code: 0)
- **`compileall`:**
  ```
  .venv\Scripts\python.exe -m compileall python/database/projections.py python/database/projection.py python/database/__init__.py tests/test_database_projections.py
  ```
  Completed with exit code 0 and zero compilation errors.

---

## 10. Authoritative File Hash Comparison

Verification script executed:
```python
import hashlib, os
baseline_hashes = {
    'context/ssot.json': '0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da',
    'context/trade_lessons.json': '0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463',
    'context/decision_log.json': '757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2',
    'context/config.json': '30ea2b67dd0ecac4b16e3e726c9644948119729023d382c385301f9db72099d5',
    'context/user_config.json': '88e00f3ef6609eba02066cdfb3750152bfa2b97667aceed5cfba30b5db607183',
    'context/agent_logs/agent-events.jsonl': '58a830fe4a59a02bb16d33df10c1a09b3e76a2b6250377e26ee37a8d73d8bc3d',
    'config.json': '10c005efa15940d9c503aae62bbcb9a195ddbe249387c8335a4cb0f836064ee3',
    'gem_trading_rules/rules.md': '04ea36c0762de30c841bb25ac35bdffeb6845cccfba19a9b94d343743c075056'
}
```

### Hash Verification Results

| File Path | Starting Baseline SHA-256 | Current Working Copy SHA-256 | Invariant Status |
|---|---|---|---|
| `context/ssot.json` | `0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da` | `0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da` | **MATCH (100% Invariant)** |
| `context/trade_lessons.json` | `0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463` | `0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463` | **MATCH (100% Invariant)** |
| `context/decision_log.json` | `757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2` | `757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2` | **MATCH (100% Invariant)** |
| `context/config.json` | `30ea2b67dd0ecac4b16e3e726c9644948119729023d382c385301f9db72099d5` | `30ea2b67dd0ecac4b16e3e726c9644948119729023d382c385301f9db72099d5` | **MATCH (100% Invariant)** |
| `context/user_config.json` | `88e00f3ef6609eba02066cdfb3750152bfa2b97667aceed5cfba30b5db607183` | `88e00f3ef6609eba02066cdfb3750152bfa2b97667aceed5cfba30b5db607183` | **MATCH (100% Invariant)** |
| `context/agent_logs/agent-events.jsonl` | `58a830fe4a59a02bb16d33df10c1a09b3e76a2b6250377e26ee37a8d73d8bc3d` | `58a830fe4a59a02bb16d33df10c1a09b3e76a2b6250377e26ee37a8d73d8bc3d` | **MATCH (100% Invariant)** |
| `config.json` | `10c005efa15940d9c503aae62bbcb9a195ddbe249387c8335a4cb0f836064ee3` | `10c005efa15940d9c503aae62bbcb9a195ddbe249387c8335a4cb0f836064ee3` | **MATCH (100% Invariant)** |
| `gem_trading_rules/rules.md` | `04ea36c0762de30c841bb25ac35bdffeb6845cccfba19a9b94d343743c075056` | `04ea36c0762de30c841bb25ac35bdffeb6845cccfba19a9b94d343743c075056` | **MATCH (100% Invariant)** |

**All 8 authoritative files remain byte-for-byte unchanged.**

---

## 11. Production Database Safety Evidence

Active inspection confirmed:
- `os.path.exists("context/gem.db")` -> `False`
- `os.path.exists("cache/market_data.db")` -> `False`

All generator and writer tests executed strictly against temporary directories (`tempfile.TemporaryDirectory()`). Zero production databases were created, connected to, or altered.

---

## 12. Known Limitations & Scope Boundaries

1. **Standalone Read Projection Only:** `SSoTProjectionGenerator` produces forward SQLite-to-JSON projections. It does not perform legacy JSON-to-SQLite migration, reverse reconciliation, or parity comparison against existing legacy files.
2. **Offline Decoupled Operation:** The generator is not wired into live runtime tools (`python/tools.py`), background daemons (`python/fetch_stocks.py`), or web server endpoints (`python/web_server.py`). Live runtime execution continues using the legacy JSON files.
3. **Legacy Schema Heterogeneity (F-15-04):** Heterogeneous entry shapes in `context/trade_lessons.json` (such as rules without `L-xxx:` prefixes like items 16 and 17) and diverse deliberation structures in `context/decision_log.json` remain un-migrated. Handling these legacy shapes belongs strictly to Stage 1.5-D (`DataMigrationService`).
4. **Subsequent Stage Progression Boundary:** Stages 1.5-D (Offline Migration & Parity Dry-Run) and 1.6 (Production Cutover & Legacy Tool Routing) have not been started and await explicit human repository owner authorization.

---

## 13. Changed File Inventory

| File Path | Action | Description |
|---|---|---|
| `python/database/projections.py` | Created | Canonical `SSoTProjectionGenerator`, snapshot read lifecycle, exact Decimal normalizer, deterministic serializer, and fsync atomic file writer |
| `python/database/projection.py` | Created | Compatibility shim re-exporting `SSoTProjectionGenerator` and helpers from `python.database.projections` |
| `python/database/__init__.py` | Modified | Exported `SSoTProjectionGenerator`, `serialize_projection`, `normalize_projection_value`, and `atomic_write_json` |
| `tests/test_database_projections.py` | Created | 35-test unit, regression, failure-injection, and determinism test suite |
| `docs/engineering/STAGE_1_5_C_DETERMINISTIC_SSOT_PROJECTION_REPORT.md` | Created | This engineering closeout report |

---

## 14. Governance Recommendation

All completion criteria for Stage 1.5-C have been achieved. The implementation is isolated, test-verified, and does not alter production databases or authoritative legacy state files.

The human repository owner may review the uncommitted changes and proceed to Git commit and push at their discretion. Progression to **Stage 1.5-D** is recommended once human sign-off is granted.

---

## 15. Stage 1.5-C.1 Corrective Addendum: Projection Writer Validation, Transaction Cleanup Integrity & Genuine Snapshot Concurrency Verification

**Date:** 2026-11-10  
**Baseline HEAD:** `c7e1a4b85a4d27d114326e49911ef71094939b00`  
**Stage:** 1.5-C.1 (Corrective Closeout)  
**Status:** COMPLETE  

### 15.1 Findings and Resolutions Summary

| Finding | Defect Description | Resolution & Architecture Applied | Affected Files & Functions |
|---|---|---|---|
| **C-01** | `atomic_write_json()` checked only `isinstance(serialized_json, str)` without JSON validation, allowing malformed JSON or non-standard tokens (`NaN`, `Infinity`) to create directories or temporary files. | Added `_reject_json_constant()` and `_validate_serialized_json()` called at the entry point of `atomic_write_json()`. Strictly parses JSON with `parse_constant` rejecting `NaN`/`Infinity`/`-Infinity`, rejecting malformed syntax and empty input before any filesystem modification. | `python/database/projections.py` (`_reject_json_constant`, `_validate_serialized_json`, `atomic_write_json`) |
| **C-02** | In `SSoTProjectionGenerator.generate()`, the `finally` block suppressed every exception from `conn.execute("ROLLBACK;")`, concealing failed releases of internally owned transactions and reporting successful projection on an uncertain connection state. | Refactored transaction cleanup using explicit `try...except BaseException...else:` semantics. Rolled back ONLY if `we_started_tx` is true. Caller-owned transactions are never touched. Rollback failure raises `TransactionIntegrityError` (preserving `.rollback_error` attribute). If projection read fails and rollback also fails, `TransactionIntegrityError` chains the original error, preserves rollback error, and attaches diagnostic note. | `python/database/projections.py` (`SSoTProjectionGenerator.generate`), `python/database/projection.py` (re-export `TransactionIntegrityError`) |
| **C-03** | Stage 1.5-C test suite verified sequential revision updates and caller-owned transactions, but did not verify genuine concurrent WAL-mode snapshot consistency where a separate writer commits during an active projection read. | Added genuine WAL-mode concurrency test coordinating independent reader and writer connections via `threading.Event`. Paused reader after metadata read pins the WAL snapshot; committed writer mutations to cash, positions, and monotonic revision; resumed reader; proved reader returned 100% pre-write state without mixed data, while subsequent projection returned 100% post-write state. Executed across 10 iterations. | `tests/test_database_projections.py` (`TestConcurrentSnapshotConsistency`) |
| **C-04** | Stage 1.5-C report Section 9.2 documented the Windows platform-skipped test as `test_posix_flock_semantics`. | Corrected documentation note in Section 9.2 to reflect the actual test name in `tests/test_database_backup_concurrency.py`: `test_r5_04_posix_unlock_failure_injection`. | `docs/engineering/STAGE_1_5_C_DETERMINISTIC_SSOT_PROJECTION_REPORT.md` (Section 9.2 note corrected) |
| **C-05** | `normalize_projection_value()` converted dictionary keys via `str(key)`, which silently collapses colliding keys (e.g., `{1: "a", "1": "b"}`). | Enforced strict string key validation (`not isinstance(key, str)`) before recursively normalizing values, raising `TypeError` fail-closed on integer, boolean, float, or nested non-string dictionary keys. | `python/database/projections.py` (`normalize_projection_value`) |

### 15.2 Implementation Inventory

1. **`python/database/projections.py`:**
   - Imported `TransactionIntegrityError` from `python.database.repositories.base`.
   - Exported `TransactionIntegrityError` in `__all__`.
   - `normalize_projection_value(value, allow_floats=False)`: Added fail-closed string validation on dictionary keys (`isinstance(key, str)` check; raises `TypeError` with received type name).
   - Added `_reject_json_constant(value)`: Raises `ValueError` for non-standard JSON tokens.
   - Added `_validate_serialized_json(serialized_json)`: Validates strict JSON via `json.loads(..., parse_constant=_reject_json_constant)` before filesystem access, raising `ValidationError`.
   - `atomic_write_json(destination, serialized_json)`: Calls `_validate_serialized_json()` at entry before parent directory check/creation, tempfile generation, or `os.replace()`.
   - `SSoTProjectionGenerator.generate(conn, ...)`: Replaced silent rollback suppression in `finally:` with fail-closed transaction lifecycle in `except BaseException as operation_error:` and `else:` blocks, raising `TransactionIntegrityError` if rollback fails.

2. **`python/database/projection.py`:**
   - Added `TransactionIntegrityError` import and re-export in `__all__` for complete compatibility.

3. **`tests/test_database_projections.py`:**
   - Added 19 dedicated test cases across 4 new test classes.

### 15.3 New Test Inventory & Evidence

19 new unit and integration tests added to `tests/test_database_projections.py`:

| Class | Test Function | Purpose / Validation | Exit Status |
|---|---|---|---|
| `TestAtomicWriterValidation` | `test_malformed_json_rejected_before_filesystem_changes` | Proves malformed JSON raises `ValidationError` without creating destination file | PASS |
| `TestAtomicWriterValidation` | `test_empty_input_rejected_before_filesystem_changes` | Proves empty string and whitespace-only input raise `ValidationError` | PASS |
| `TestAtomicWriterValidation` | `test_trailing_non_whitespace_content_rejected` | Proves extra non-whitespace tokens after valid JSON raise `ValidationError` | PASS |
| `TestAtomicWriterValidation` | `test_nan_and_infinity_rejected` | Proves `NaN`, `Infinity`, and `-Infinity` tokens raise `ValidationError` | PASS |
| `TestAtomicWriterValidation` | `test_existing_destination_byte_for_byte_unchanged_on_validation_failure` | Proves pre-existing destination is untouched and byte-for-byte identical after invalid write attempt | PASS |
| `TestAtomicWriterValidation` | `test_nonexistent_parent_directory_not_created_on_validation_failure` | Proves non-existent parent directory is NOT created when JSON validation fails | PASS |
| `TestAtomicWriterValidation` | `test_no_temporary_file_created_on_validation_failure` | Proves zero temporary files (`*.tmp` or `.*.tmp`) remain or are created on validation failure | PASS |
| `TestAtomicWriterValidation` | `test_valid_utf8_json_writes_correctly` | Proves valid UTF-8 JSON payloads write and replace atomically | PASS |
| `TestTransactionCleanupIntegrity` | `test_successful_projection_rollback_failure_raises_transaction_integrity_error` | Proves rollback failure following successful read raises `TransactionIntegrityError` without returning projection | PASS |
| `TestTransactionCleanupIntegrity` | `test_read_failure_with_successful_rollback_propagates_original_error_and_releases_tx` | Proves read failure propagates original exception directly and releases owned transaction | PASS |
| `TestTransactionCleanupIntegrity` | `test_read_failure_with_rollback_failure_preserves_both_errors` | Proves double failure preserves both query error (`__cause__`) and rollback error (`rollback_error` attribute and note) | PASS |
| `TestTransactionCleanupIntegrity` | `test_caller_owned_transaction_not_rolled_back_or_committed` | Proves caller-owned transaction is never rolled back or committed by generator | PASS |
| `TestTransactionCleanupIntegrity` | `test_normal_successful_projection_releases_owned_transaction` | Proves normal projection cleanly releases generator-owned transaction (`conn.in_transaction == False`) | PASS |
| `TestConcurrentSnapshotConsistency` | `test_genuine_concurrent_writer_during_read_snapshot_maintains_snapshot_isolation` | Proves genuine WAL-mode snapshot consistency: reader paused after metadata read never sees concurrent writer mutations (revision, cash, positions); subsequent projection sees complete post-write state. (10 iterations) | PASS |
| `TestProjectionDictionaryKeyValidation` | `test_rejects_integer_key` | Proves integer dictionary keys raise `TypeError` fail-closed | PASS |
| `TestProjectionDictionaryKeyValidation` | `test_rejects_boolean_key` | Proves boolean dictionary keys raise `TypeError` fail-closed | PASS |
| `TestProjectionDictionaryKeyValidation` | `test_rejects_mixed_integer_string_collision` | Proves colliding integer and string keys raise `TypeError` without data loss | PASS |
| `TestProjectionDictionaryKeyValidation` | `test_rejects_nested_invalid_key` | Proves nested non-string keys raise `TypeError` fail-closed | PASS |
| `TestProjectionDictionaryKeyValidation` | `test_valid_string_key_dictionaries_remain_deterministic` | Proves valid string-key dictionaries serialize deterministically with sorted keys | PASS |

### 15.4 Concurrency Verification Details (C-03)

- **Coordination Mechanism:** Deterministic thread signaling using two `threading.Event` synchronization primitives (`snapshot_established` and `writer_committed`) with zero production delays or arbitrary sleeps.
- **Seam:** Reader connection interception on `PositionRepository.list_positions` after `database_metadata` and `CashRepository` queries established the WAL read snapshot.
- **Writer Action:** Separate `writer_conn` on independent lifecycle updated `portfolio_cash` (EUR 1000.00 -> EUR 2500.00), upserted `portfolio_positions` (AAPL 10.0000 -> 50.0000 shares), and advanced `db_revision` within its own committed transaction.
- **Verification Result:**
  - Reader projection returned: `db_revision=initial`, `unallocated_cash_eur="1000.00"`, `shares="10.0000"`.
  - Zero mixed state: never combined old revision with new positions or new cash with old revision.
  - Subsequent post-write projection returned: `db_revision=writer_revision`, `unallocated_cash_eur="2500.00"`, `shares="50.0000"`.
  - 10 repeated test executions (100 total iterations) completed with 100% pass rate.

### 15.5 Test Suite Totals & Reconciliation

| Test Suite | Command | Discovered | Passed | Skipped | Failed | Errors | Runtime | Exit Code |
|---|---|---|---|---|---|---|---|---|
| **Focused Projections** | `.venv\Scripts\python.exe -m unittest tests/test_database_projections.py` | 54 (+19) | 54 | 0 | 0 | 0 | 4.95s | 0 |
| **Concurrency Stress (10 runs)** | Custom 10-run harness (`TestConcurrentSnapshotConsistency`) | 10 (100 iter) | 10 | 0 | 0 | 0 | 7.12s | 0 |
| **Repository Suite** | `.venv\Scripts\python.exe -m unittest tests/test_database_repositories.py` | 254 | 254 | 0 | 0 | 0 | 19.33s | 0 |
| **Core Database** | `.venv\Scripts\python.exe -m unittest tests/test_database_core.py` | 115 | 115 | 0 | 0 | 0 | 5.90s | 0 |
| **Backup Concurrency** | `.venv\Scripts\python.exe -m unittest tests/test_database_backup_concurrency.py` | 74 | 73 | 1* | 0 | 0 | 8.22s | 0 |
| **Full Repository Discovery** | `.venv\Scripts\python.exe -m unittest discover -s tests` | **551** (+19) | **550** | **1\*** | **0** | **0** | 70.31s | **0** |

*\*Note: 1 test skipped in backup concurrency suite is the platform-gated POSIX `fcntl` locking test on Windows (`test_r5_04_posix_unlock_failure_injection`), accurately identified as skipped on win32.*

**Reconciliation:**
- Stage 1.5-C Baseline: 532 tests (531 passed, 1 skipped).
- Tests Added in Stage 1.5-C.1: 19 tests in `tests/test_database_projections.py`.
- Stage 1.5-C.1 Total: 551 discovered (550 passed, 1 skipped).

### 15.6 Package Integrity & Compilation

- **`pip check`:** `No broken requirements found.` (Exit code: 0)
- **`compileall`:**
  ```
  .venv\Scripts\python.exe -m compileall python/database/projections.py python/database/projection.py tests/test_database_projections.py
  ```
  Completed with exit code 0 and zero compilation errors.

### 15.7 Authoritative File Invariance & Database Non-Existence Evidence

All 8 authoritative files remain byte-for-byte invariant against established baseline hashes:

| Authoritative File | Expected Baseline SHA-256 | Current Working Copy SHA-256 | Status |
|---|---|---|---|
| `context/ssot.json` | `0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da` | `0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da` | **MATCH (100% Invariant)** |
| `context/trade_lessons.json` | `0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463` | `0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463` | **MATCH (100% Invariant)** |
| `context/decision_log.json` | `757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2` | `757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2` | **MATCH (100% Invariant)** |
| `context/config.json` | `30ea2b67dd0ecac4b16e3e726c9644948119729023d382c385301f9db72099d5` | `30ea2b67dd0ecac4b16e3e726c9644948119729023d382c385301f9db72099d5` | **MATCH (100% Invariant)** |
| `context/user_config.json` | `88e00f3ef6609eba02066cdfb3750152bfa2b97667aceed5cfba30b5db607183` | `88e00f3ef6609eba02066cdfb3750152bfa2b97667aceed5cfba30b5db607183` | **MATCH (100% Invariant)** |
| `context/agent_logs/agent-events.jsonl` | `58a830fe4a59a02bb16d33df10c1a09b3e76a2b6250377e26ee37a8d73d8bc3d` | `58a830fe4a59a02bb16d33df10c1a09b3e76a2b6250377e26ee37a8d73d8bc3d` | **MATCH (100% Invariant)** |
| `config.json` | `10c005efa15940d9c503aae62bbcb9a195ddbe249387c8335a4cb0f836064ee3` | `10c005efa15940d9c503aae62bbcb9a195ddbe249387c8335a4cb0f836064ee3` | **MATCH (100% Invariant)** |
| `gem_trading_rules/rules.md` | `04ea36c0762de30c841bb25ac35bdffeb6845cccfba19a9b94d343743c075056` | `04ea36c0762de30c841bb25ac35bdffeb6845cccfba19a9b94d343743c075056` | **MATCH (100% Invariant)** |

**Production Database Non-Existence Confirmation:**
- `context/gem.db` exists: `False` (absent)
- `cache/market_data.db` exists: `False` (absent)

### 15.8 Remaining Limitations & Stage 1.5-D Boundary

1. **Standalone Corrective Closeout:** Stage 1.5-C.1 hardens writer validation, transaction cleanup, concurrency proofs, and dictionary normalization without altering database schemas or runtime wiring.
2. **Offline Decoupled Operation:** Projections remain strictly offline and decoupled from live daemons and runtime tools.
3. **No Migration / Parity Reconciliation:** Migration of legacy JSON documents and parity reconciliation against legacy shapes remains the explicit mission of Stage 1.5-D (`DataMigrationService`).
4. **Autonomous Git Prohibition:** Git commit and push operations remain strictly under human repository owner control.

