# Stage 1.5-A.2.2 Engineering Report: Restore Gate Lifetime, Target-State Race & Atomic No-Clobber Corrections

**Repository:** `nordicirish/gemini_cli_subagent_system`  
**Branch:** `main`  
**Verified Baseline HEAD:** `ea7ed90b3f62f51c9114e2a433388a6c5f8b8d55`  
**Execution Date:** 2026-11-09  
**Status:** Verification Complete — Ready for Human Repository Owner Sign-Off and Commit  
**Authority:** Human Repository Owner Only (Zero Autonomous Commits/Pushes)

---

## 1. Executive Summary & Objectives

Stage 1.5-A.2.2 implements targeted corrective engineering to eliminate remaining concurrency vulnerabilities, TOCTOU races, and exception suppression paths identified during the independent review of Stage 1.5-A.2.1. 

Specifically, this stage addresses and resolves:
1. **R1 — Shared-Lock Lifetime Contract:** Transformed the shared restore gate from an opening-only check to a connection-lifetime guarantee via `ManagedConnection(sqlite3.Connection)`. Active database connections now retain the shared restore lock token until explicitly closed, preventing restoration from proceeding while connections are active.
2. **R2 — Target-State Inspection Behind Exclusive Lock:** Reordered the restoration protocol so that target path inspection occurs strictly *after* acquiring the exclusive restore coordination lock. Stale target existence checks can no longer bypass POSIX existing-target refusal or Windows target quiescence checks. Added an immediate pre-placement recheck.
3. **R3 — Atomic No-Clobber Placement for New Targets:** Implemented `BackupManager._atomic_no_clobber_place()` using cross-platform OS atomic primitives (`os.rename()` on Windows NTFS raising `FileExistsError`, and `os.link()` + `os.unlink()` on POSIX). If a destination file is created concurrently during restoration, restoration fails closed with `BackupDestinationExistsError`, preserving the destination file intact and preserving the verified temporary restored database without unlinking.
4. **R4 — Stabilised Restore-Lock File Identity:** Eliminated repeated truncation, textual metadata writing (`pid=...`), size checks (`getsize > 0`), and shared-lock unlinking. Lock files are treated as persistent zero-byte coordination artifacts governed purely by kernel-managed byte-range/advisory locks (`LockFileEx` on Windows, `fcntl.flock` on POSIX), eliminating split-brain inode and delete-pending access conflicts.
5. **R5 — Elimination of Silent Exception Suppression:** Removed all remaining `except Exception: pass` blocks in `DatabaseManager.connection()`, `DatabaseManager.transaction()`, and `UnitOfWork.__exit__()`. All rollback failures and connection close failures now propagate with full exception chaining and diagnostic attributes.
6. **R6 — Concurrency, Race Condition, and Exception Integrity Test Suite:** Added 14 comprehensive test cases in `TestStage15A22RestoreGateLifetimeAndNoClobber` within `tests/test_database_backup_concurrency.py`, expanding the test suite to 406 passing tests with zero regressions.

---

## 2. Governance Baseline Verification

Before any code modification, the working tree and baseline HEAD were strictly verified:
- **Branch:** `main`
- **HEAD Commit:** `ea7ed90b3f62f51c9114e2a433388a6c5f8b8d55`
- **Initial Working Tree:** Clean (zero untracked or modified files)
- **Authoritative Hash Baseline:** 100% verified across all 8 governed files.
- **Production Databases:** Verified absent (`context/gem.db`: False, `cache/market_data.db`: False).

---

## 3. R1 — Connection-Lifetime Shared Lock Contract

### Finding in Stage 1.5-A.2.1
`DatabaseManager.connect()` previously acquired `DatabaseRestoreLock.shared_gate()` inside a context manager that exited before returning the connection to the caller. The lock protected connection opening and PRAGMA configuration, but not the active lifetime of the connection. An offline restoration could start while managed connections were actively performing queries.

### Corrective Implementation (Option A)
Implemented `ManagedConnection(sqlite3.Connection)` in `python/database/manager.py`:
- Inherits directly from `sqlite3.Connection` and is instantiated via the SQLite connection factory (`sqlite3.connect(..., factory=ManagedConnection)`).
- Retains an acquired `DatabaseRestoreLockToken` in `self._lock_token`.
- Overrides `close()` to be completely idempotent (`if self._is_closed: return; self._is_closed = True`).
- Deterministically closes the SQLite connection via `super().close()` and releases the held restore lock token via `self._lock_token.release()`.
- Error preservation: If both SQLite close and lock release fail, both exceptions are preserved and chained into `DatabaseConnectionError`.
- PRAGMA configuration rollback: If connection configuration fails in `DatabaseManager.connect()`, the connection is closed, releasing the shared token immediately and preventing dangling locks.
- `UnitOfWork` integration:
  - Owned connections (`self._owns_conn = True`): `UnitOfWork` closes the connection in `__exit__()`, releasing the shared lock token on all exit paths (commit, rollback, exception).
  - External connections (`self._owns_conn = False`): `UnitOfWork` leaves the connection and its token ownership with the external caller.

---

## 4. R2 — Target-State Inspection Behind Exclusive Lock Acquisition

### Finding in Stage 1.5-A.2.1
`BackupManager.restore_backup()` previously evaluated `target_exists = os.path.exists(target_db_path)` *before* acquiring the exclusive restore coordination lock. If a concurrent connection created the database between the existence check and lock acquisition, the stale `False` bypassed POSIX existing-target refusal and Windows quiescence validation, followed by an unconditional clobber via `os.replace()`.

### Corrective Implementation
Reordered the restoration protocol in `python/database/backup.py`:
1. Source backup physical integrity is verified via `cls.verify_backup(backup_path)`.
2. Target directory is ensured.
3. **Exclusive Lock First:** `DatabaseRestoreLock.acquire_exclusive(target_db_path)` is acquired before any target state assessment.
4. **Target Inspection Behind Lock:** `target_exists = os.path.exists(target_db_path)` is evaluated strictly *after* acquiring the exclusive lock.
5. **Platform Existing-Target Policy:**
   - **POSIX:** If `target_exists`, strictly refused fail-closed with `BackupQuiescenceError`.
   - **Windows:** If `target_exists`, verifies filesystem quiescence via `_acquire_exclusive_target_lock()`, executes `PRAGMA wal_checkpoint(TRUNCATE)` and transitions to `PRAGMA journal_mode=DELETE`, verifies removal of sidecars, and acquires/holds a persistent exclusive Windows file handle `h_target`.
6. Restores to isolated temporary database file (`tmp_restore`) via the Online Backup API and executes `PRAGMA integrity_check;`.
7. Closes temporary connections.
8. **Pre-Placement Recheck:** Evaluates target state immediately prior to placement. If target appeared during restoration, raises `BackupDestinationExistsError`.
9. **Final Placement:** Executes atomic replacement with fallback preservation (for existing Windows targets) or atomic no-clobber placement (for new targets).
10. Releases exclusive coordination lock.

---

## 5. R3 — Atomic No-Clobber Placement for New Targets

### Finding in Stage 1.5-A.2.1
When restoring to a new target path, the implementation previously invoked `os.replace(tmp_restore, target_db_path)`. If another process created the target database after the initial check, `os.replace()` unconditionally clobbered that file.

### Corrective Implementation
Implemented `BackupManager._atomic_no_clobber_place(tmp_path: str, dest_path: str)`:
- **Windows:** Calls `os.rename(tmp_path, dest_path)` (internally `MoveFileExW` without `MOVEFILE_REPLACE_EXISTING`), which raises `FileExistsError` if `dest_path` already exists.
- **POSIX:** Calls `os.link(tmp_path, dest_path)` followed by `os.unlink(tmp_path)`. `link()` atomically fails with `FileExistsError` if the destination exists without touching the target.
- **Fail-Closed Preservation:** When `FileExistsError` is caught:
  - Raises `BackupDestinationExistsError`.
  - Preserves the existing destination file intact without overwriting or truncating it.
  - Preserves the verified temporary database file (`tmp_restore`) intact (bypassing deletion in the exception handler) and attaches `e.temporary_database_path = tmp_restore` for diagnostic inspection and recovery.

---

## 6. R4 — Stabilisation of Restore-Lock File Identity

### Finding in Stage 1.5-A.2.1
The restore lock file was repeatedly created, truncated, written with text (`pid=...`), and unlinked on lock release. This introduced inode-race conditions on POSIX, delete-pending access conflicts on Windows NTFS, and relied on non-atomic file-size checks (`getsize > 0`).

### Corrective Implementation
1. **Persistent Zero-Byte File:** The coordination lock file `<db_path>.restore_lock` is treated as a persistent zero-byte artifact.
2. **Pure Kernel Locks:**
   - **Windows:** `LockFileEx` with byte range `(0, MAXDWORD, MAXDWORD)`.
     - Shared lock: `LOCKFILE_FAIL_IMMEDIATELY (0x01)`.
     - Exclusive lock: `LOCKFILE_FAIL_IMMEDIATELY | LOCKFILE_EXCLUSIVE_LOCK (0x03)`.
     - Releasing: `UnlockFileEx`.
   - **POSIX:** `fcntl.flock` on file descriptor.
     - Shared lock: `fcntl.LOCK_SH | fcntl.LOCK_NB`.
     - Exclusive lock: `fcntl.LOCK_EX | fcntl.LOCK_NB`.
     - Releasing: `fcntl.flock(fileno, fcntl.LOCK_UN)`.
3. **No Unlinking on Release:** Neither shared nor exclusive lock release unlinks the lock file. The zero-byte file remains permanently on disk, ensuring all concurrent openers coordinate on the exact same filesystem identity and eliminating split-brain locking.
4. **Zero-Byte Invariant:** No text or PID metadata is written to the lock file. All lock-state decisions are made by the operating system kernel.

---

## 7. R5 — Complete Elimination of Silent Exception Suppression

### Corrective Implementation
Removed all instances of `except Exception: pass`:
1. **`DatabaseManager.connection()`:**
   - Captures `conn.close()` exceptions.
   - If an active domain exception is propagating, attaches the error as `connection_close_error`.
   - If context exited cleanly, raises `DatabaseConnectionError` chaining the close error.
2. **`DatabaseManager.transaction()`:**
   - In `except Exception as exc:`, if `conn.execute("ROLLBACK;")` fails, captures `rollback_err` and raises `DatabaseTransactionError` chaining both `exc` and `rollback_err`.
   - In `finally:`, if `owns_connection`, handles close errors without swallowing.
3. **`UnitOfWork.__exit__()`:**
   - In poisoned exit (`self._is_poisoned`):
     - If `self.rollback()` fails, captures `rollback_err` and marks `self._conn_unsafe = True`.
     - If `exc_type is None`, raises `TransactionIntegrityError` with `rollback_error` attached.
     - If `exc_type is not None`, raises `TransactionIntegrityError` chaining `exc_val` with `rollback_error` attached.
   - If owned connection close fails on exit, raises `TransactionIntegrityError` (or attaches to in-flight exception).

---

## 8. R6 — Concurrency, Race Condition, and Exception Integrity Test Suite

Added test class `TestStage15A22RestoreGateLifetimeAndNoClobber` with 14 automated tests in `tests/test_database_backup_concurrency.py`:

| Test Name | Focus Area | Verification Details |
|---|---|---|
| `test_r1_managed_connection_retains_token_until_closed` | Shared lock lifetime | Verifies connection holds token until `close()`; restore blocked during connection lifetime. |
| `test_r1_managed_connection_close_idempotent` | Connection close safety | Verifies multiple `close()` calls on `ManagedConnection` do not raise or double-release. |
| `test_r1_connection_config_failure_releases_shared_token` | Leak prevention | Verifies failed PRAGMA setup closes connection and releases token. |
| `test_r1_both_sqlite_close_and_token_release_fail_preserves_both_errors` | Error chaining | Verifies `DatabaseConnectionError` chains both SQLite close and token release failures. |
| `test_r1_unit_of_work_owned_vs_external_connection_token_lifecycle` | UoW token ownership | Verifies owned connection token released on exit; external connection token preserved. |
| `test_r2_target_inspection_occurs_after_exclusive_lock_acquisition` | TOCTOU elimination | Validates that `acquire_exclusive` executes strictly before target existence checks. |
| `test_r2_target_created_before_placement_detected_by_recheck` | Pre-placement recheck | Verifies concurrent target creation before placement raises `BackupDestinationExistsError` and preserves files. |
| `test_r3_atomic_no_clobber_placement_failure_preserves_both_files` | Atomic no-clobber | Verifies `_atomic_no_clobber_place` refuses overwrite, preserving destination and temporary database. |
| `test_r4_persistent_zero_byte_restore_lock_never_unlinked` | Lock identity | Verifies lock file remains 0 bytes and is never unlinked on shared or exclusive release. |
| `test_r4_multiple_concurrent_managed_connections_coexist` | Reader concurrency | Verifies multiple `ManagedConnection` instances hold shared locks simultaneously. |
| `test_r4_cross_process_managed_connection_blocks_restore` | Cross-process exclusion | Subprocess child holding managed connection blocks parent restoration fail-closed. |
| `test_r5_unit_of_work_poisoned_rollback_failure_propagates_integrity_error` | Exception integrity | Verifies poisoned UoW rollback failure raises `TransactionIntegrityError` (both `exc_type is None` and not None). |
| `test_r5_database_manager_connection_close_failure_reported` | Non-suppression | Verifies `DatabaseManager.connection()` reports close failure on clean exit and attaches on active error. |
| `test_r5_database_manager_transaction_rollback_failure_reported` | Non-suppression | Verifies `DatabaseManager.transaction()` rollback failure raises `DatabaseTransactionError`. |

---

## 9. Verification Results Matrix & Test Execution Evidence

### Test Execution Summary
- **Targeted Backup Concurrency Tests:** `tests/test_database_backup_concurrency.py` — **31 tests passed** (17 from Stage 1.5-A.2.1 + 14 from Stage 1.5-A.2.2).
- **Core Database Tests:** `tests/test_database_core.py` — **115 tests passed**.
- **Repository Unit of Work Tests:** `tests/test_database_repositories.py` — **206 tests passed**.
- **Full Workspace Discovery:** `.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"` — **406 tests passed in 52.8s (100% OK, 0 failures, 0 errors)**.
- **Dependency Integrity (`pip check`):** No broken requirements found.
- **Bytecode Compilation (`py_compile`):** All modified modules compiled cleanly with zero syntax or compilation errors.

---

## 10. Authoritative File Hash Invariance Table

All 8 authoritative financial, configuration, and rule files remain 100% byte-for-byte invariant against their canonical baseline SHA-256 hashes:

| Authoritative File | Expected SHA-256 Hash | Observed SHA-256 Hash | Verification Status |
|---|---|---|:---:|
| `context/ssot.json` | `0A2BA4D8C125D213C5772B36164637DF74094CA95E28E2CA0AFC2DA5E55414DA` | `0A2BA4D8C125D213C5772B36164637DF74094CA95E28E2CA0AFC2DA5E55414DA` | **MATCH (PASS)** |
| `context/trade_lessons.json` | `0C4AC816009CB348F4BC923D94124F41F5C6D1EC8722A487BBA9115F66029463` | `0C4AC816009CB348F4BC923D94124F41F5C6D1EC8722A487BBA9115F66029463` | **MATCH (PASS)** |
| `context/decision_log.json` | `757E0B4855458CE7075EC16697798B3047CCC72A6214CDED6D7BBB1346700AF2` | `757E0B4855458CE7075EC16697798B3047CCC72A6214CDED6D7BBB1346700AF2` | **MATCH (PASS)** |
| `context/config.json` | `30EA2B67DD0ECAC4B16E3E726C9644948119729023D382C385301F9DB72099D5` | `30EA2B67DD0ECAC4B16E3E726C9644948119729023D382C385301F9DB72099D5` | **MATCH (PASS)** |
| `context/user_config.json` | `88E00F3EF6609EBA02066CDFB3750152BFA2B97667ACEED5CFBA30B5DB607183` | `88E00F3EF6609EBA02066CDFB3750152BFA2B97667ACEED5CFBA30B5DB607183` | **MATCH (PASS)** |
| `context/agent_logs/agent-events.jsonl` | `58A830FE4A59A02BB16D33DF10C1A09B3E76A2B6250377E26EE37A8D73D8BC3D` | `58A830FE4A59A02BB16D33DF10C1A09B3E76A2B6250377E26EE37A8D73D8BC3D` | **MATCH (PASS)** |
| `config.json` | `10C005EFA15940D9C503AAE62BBCB9A195DDBE249387C8335A4CB0F836064EE3` | `10C005EFA15940D9C503AAE62BBCB9A195DDBE249387C8335A4CB0F836064EE3` | **MATCH (PASS)** |
| `gem_trading_rules/rules.md` | `04EA36C0762DE30C841BB25AC35BDFFEB6845CCCFBA19A9B94D343743C075056` | `04EA36C0762DE30C841BB25AC35BDFFEB6845CCCFBA19A9B94D343743C075056` | **MATCH (PASS)** |

---

## 11. Production Database Non-Existence Verification

Verified strictly:
- `context/gem.db`: **Does not exist (False)**
- `cache/market_data.db`: **Does not exist (False)**

Zero production database initialization or runtime cutover was performed.

---

## 12. Modified File Summary

The working tree contains only the approved implementation files and documentation:
1. `python/database/backup.py` — Persistent zero-byte restore locking, reordered target inspection behind exclusive lock, atomic no-clobber placement `_atomic_no_clobber_place()`, and file preservation on conflict.
2. `python/database/manager.py` — `ManagedConnection` with connection-lifetime shared restore lock token, non-suppression of rollback/close failures in `connection()` and `transaction()`.
3. `python/database/__init__.py` — Exported `ManagedConnection`.
4. `python/database/repositories/unit_of_work.py` — Non-suppression of rollback failure during poisoned transaction exit.
5. `tests/test_database_core.py` — Updated restore lock test to acquire kernel-level exclusive lock.
6. `tests/test_database_backup_concurrency.py` — Added 14 comprehensive tests in `TestStage15A22RestoreGateLifetimeAndNoClobber`.
7. `docs/engineering/STAGE_1_5_A_2_2_RESTORE_GATE_LIFETIME_AND_NO_CLOBBER_REPORT.md` — Authoritative engineering report.

---

## 13. Proposed Git Commit Message

```text
fix(database): enforce restore gate lifetime, target race elimination and atomic no-clobber placement (Stage 1.5-A.2.2)

- Implement ManagedConnection retaining shared DatabaseRestoreLockToken across active connection lifecycle
- Reorder restore_backup to inspect target existence strictly after acquiring exclusive restore lock
- Add pre-placement target existence recheck and atomic no-clobber placement via _atomic_no_clobber_place
- Preserve verified temporary database and untouched destination when destination exists on new target restore
- Stabilise restore coordination lock as persistent zero-byte artifact governed purely by kernel locks (LockFileEx / flock)
- Eliminate silent exception suppression in UnitOfWork.__exit__ poisoned rollback and DatabaseManager transaction/connection cleanup
- Add 14 comprehensive concurrency and race tests in TestStage15A22RestoreGateLifetimeAndNoClobber (406 total suite tests pass)
- Verify 100% SHA-256 hash invariance across all 8 authoritative files with zero production database files created
```

---

## 14. Conclusion & Directive

All requirements of **Stage 1.5-A.2.2** are satisfied, fully verified, and backed by automated regression tests. The working tree remains uncommitted in strict adherence to the Agent Governance Framework.

**STOPPED.** Awaiting Human Repository Owner review, sign-off, and commit.

