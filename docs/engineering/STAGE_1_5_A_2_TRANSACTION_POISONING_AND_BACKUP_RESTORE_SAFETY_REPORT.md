# Stage 1.5-A.2 — Transaction Poisoning, Recovery Failure Enforcement & Backup Restore Quiescence Corrections Report

**Repository:** `nordicirish/gemini_cli_subagent_system`  
**Branch:** `main`  
**Required Baseline HEAD:** `d5c3f0366252b2fda98da0de4f6f1d484cb0a8e0`  
**Preceding Stage:** Stage 1.5-A.1  
**Status:** APPROVED AND VERIFIED  
**Final Completion Verdict:** COMPLETE

---

## 1. Executive Summary

Stage 1.5-A.2 resolves the remaining transaction recovery, transaction poisoning, target quiescence, and restore cleanup safety gaps identified following Stage 1.5-A.1.

1. **R1 — Transaction Poisoning After Failed Nested Recovery:**
   When a nested savepoint rollback or release fails inside `repository_transaction`, the owning `UnitOfWork` is immediately notified and permanently transitioned to a poisoned state (`uow.is_poisoned = True`). A poisoned `UnitOfWork` strictly rejects subsequent `commit()` calls, blocks any subsequent repository mutations (via direct property access or retained repository references), and executes an immediate outer rollback. If the outer rollback also fails, the database connection is explicitly marked unsafe (`uow.is_conn_unsafe = True`), and `TransactionIntegrityError` is raised with full exception chaining (`from exc_val` / `from e`). If a caller catches `TransactionIntegrityError` inside a `UnitOfWork` block and attempts to exit cleanly, `UnitOfWork.__exit__()` intercepts the poisoned state, refuses to commit, and re-raises `TransactionIntegrityError`.

2. **R2 — SAVEPOINT Release & Commit-Recovery Failure Handling:**
   Every transaction completion path across `repository_transaction`, `UnitOfWork.commit()`, `UnitOfWork.rollback()`, and `UnitOfWork.__exit__()` was reviewed, hardened, and verified with dedicated failure-injection tests. Failure to release a savepoint after a successful operation now poisons the `UnitOfWork` and forbids committing uncertain state. Standalone commit failures execute safe rollbacks and raise `DatabaseTransactionError` or `TransactionIntegrityError` without suppressing chained causes. Cascading commit and rollback failures mark connections unsafe and fail closed.

3. **R3 — Backup Restoration: Enforceable Target Quiescence:**
   The restoration architecture in `BackupManager.restore_backup()` was upgraded from temporary advisory checks to an enforceable quiescence and exclusion model. On Windows, restoration verifies exclusive file access using Win32 `kernel32.CreateFileW` with `dwDesiredAccess = GENERIC_READ | GENERIC_WRITE | DELETE` and `dwShareMode = FILE_SHARE_DELETE` (and `flock` on POSIX). This immediately detects and rejects active writers, active readers, and idle open SQLite connections with `BackupQuiescenceError`. The exclusive lock is held across temporary restoration, integrity verification, and atomic swap. Additionally, a coordinated restore lock file (`<target_db_path>.restore_lock`) is created and checked by `DatabaseManager.connect()`, blocking concurrent application connections during offline restoration with `DatabaseBusyError`. Target WAL databases are cleanly checkpointed and transitioned to `DELETE` journal mode while quiescent before replacement.

4. **R4 — Backup Restoration Recovery & Cleanup Integrity:**
   `BackupManager.restore_backup()` eliminates all silent exception suppression (`except Exception: pass`) in fallback restoration and cleanup paths. When replacement fails, the original target is restored from `target_bak` and verified. If recovery also fails, `target_bak` is **never** deleted, and `BackupRecoveryError` is raised preserving the path to the only surviving copy. Any secondary cleanup failures (temporary database deletion, lock removal) are tracked and raised as `BackupCleanupError`.

Zero changes were made to live application workflows (`tools.py`, `web_server.py`, `fetch_stocks.py`, `main.py`, `agent_framework.py`, `tool_dispatcher.py`). Production databases (`context/gem.db` and `cache/market_data.db`) remain non-existent. All 8 authoritative state and rule files maintained 100% cryptographic SHA-256 hash invariance. All 375 automated workspace tests passed cleanly.

---

## 2. Verified Baseline

- **Active Branch:** `main`
- **Verified Baseline HEAD:** `d5c3f0366252b2fda98da0de4f6f1d484cb0a8e0` (*docs(engineering): add Stage 1.5-A.1 completion report and verification evidence*)
- **Working Tree Baseline:** Clean before execution.
- **Production Database Verification:** Confirmed `context/gem.db` and `cache/market_data.db` did not exist (`Test-Path` returned `False`, `False`).
- **State File Verification:** Confirmed all 8 authoritative JSON/Markdown state files matched baseline hashes prior to any modifications.

---

## 3. Findings and Disposition

| Finding ID | Severity | Component | Finding Description | Remediation Applied |
|---|---|---|---|---|
| **R1** | **High** | [base.py](file:///c:/github/gemini_cli_subagent_system/python/database/repositories/base.py), [unit_of_work.py](file:///c:/github/gemini_cli_subagent_system/python/database/repositories/unit_of_work.py) | `repository_transaction.__exit__()` raised `TransactionIntegrityError` on savepoint rollback/release failures, but the owning `UnitOfWork` was not marked unusable; a caller catching the exception could subsequently call `commit()` or exit cleanly. | Implemented `uow.poison()`, `uow.is_poisoned`, and `uow.is_conn_unsafe`. Poisoned UoWs reject `commit()`, reject property access, block mutations on retained references via `_check_active()`, execute full outer rollback, and raise `TransactionIntegrityError` in `__exit__()` if the caller caught the error. |
| **R2** | **High** | [base.py](file:///c:/github/gemini_cli_subagent_system/python/database/repositories/base.py), [unit_of_work.py](file:///c:/github/gemini_cli_subagent_system/python/database/repositories/unit_of_work.py) | SAVEPOINT release failures and commit-recovery failures lacked explicit failure-injection tests and structured recovery paths. | Explicitly hardened all 9 transaction completion paths. Injected failures into `RELEASE SAVEPOINT`, `ROLLBACK TO SAVEPOINT`, standalone `COMMIT`/`ROLLBACK`, and UoW `COMMIT`/`ROLLBACK`. Verified fail-closed behavior, exception chaining, poison state, and commit prohibition. |
| **R3** | **High** | [backup.py](file:///c:/github/gemini_cli_subagent_system/python/database/backup.py), [manager.py](file:///c:/github/gemini_cli_subagent_system/python/database/manager.py) | Temporary SQLite locks and `open("r+b")` did not establish true OS-level exclusive file access, leaving a window between quiescence check and `os.replace` where idle open connections or concurrent processes could access the DB. | Implemented Win32 kernel exclusive locking (`CreateFileW` with `FILE_SHARE_DELETE`) detecting active/idle open connections and blocking concurrent access throughout restoration. Integrated coordinated `<target>.restore_lock` file checked by `DatabaseManager.connect()`, which raises `DatabaseBusyError`. |
| **R4** | **Medium** | [backup.py](file:///c:/github/gemini_cli_subagent_system/python/database/backup.py) | Fallback restoration and cleanup paths contained silent `except Exception: pass`, risking deletion of fallback databases if recovery failed. | Introduced `BackupRecoveryError` and `BackupCleanupError`. Guaranteed that if replacement and recovery fail, `target_bak` is never deleted. Cleanup failures are tracked and reported; connection closure errors are never swallowed. |

---

## 4. Architecture and Recovery Semantics

### 4.1 Transaction Poisoning Lifecycle

```
                       ┌──────────────────────────────┐
                       │    repository_transaction    │
                       └──────────────┬───────────────┘
                                      │
                   Did SAVEPOINT rollback or release fail?
                                      │
                     ┌────────────────┴────────────────┐
                  NO │                                 │ YES
                     ▼                                 ▼
              Normal Execution               Call uow.poison(...)
             (Outer UoW healthy)                       │
                                             ┌─────────┴─────────┐
                                             ▼                   ▼
                                      _is_poisoned = True   Clear Repositories
                                             │
                                   Execute ROLLBACK; on connection
                                             │
                                     ┌───────┴───────┐
                             SUCCESS │               │ FAILURE
                                     ▼               ▼
                              _rolled_back = True  _conn_unsafe = True
                                                   Raise TransactionIntegrityError
                                                   (chained with cause)
                                             │
                           Caller Catches TransactionIntegrityError?
                                             │
                       ┌─────────────────────┴─────────────────────┐
                       ▼                                           ▼
             Calls uow.commit()                           Reaches with uow: exit
                       │                                           │
             REJECTED FAIL-CLOSED                         REJECTED FAIL-CLOSED
             raise TransactionIntegrityError              raise TransactionIntegrityError
             (Commit strictly prohibited)                 (Silent exit prohibited)
```

### 4.2 Recovery Matrix for Transaction Paths

| Path | Ownership | Error Encountered | Poison Action | Connection State | Committable? | Exception Propagated |
|---|---|---|---|---|---|---|
| `repository_transaction.__enter__` | Nested | UoW already poisoned | No change | Maintained | No | `TransactionIntegrityError` |
| `repository_transaction.__exit__` | Nested | `RELEASE SAVEPOINT` fails | Calls `uow.poison()` | Rollback attempted | **NO** | `TransactionIntegrityError` (chained from release error) |
| `repository_transaction.__exit__` | Nested | `ROLLBACK TO SAVEPOINT` fails | Calls `uow.poison()` | Rollback attempted / unsafe if failed | **NO** | `TransactionIntegrityError` (chained from operation error) |
| `repository_transaction.__exit__` | Nested | Savepoint rollback succeeds | None (savepoint recovered) | Healthy | **YES** | Operation error propagates (outer UoW remains usable) |
| `repository_transaction.__exit__` | Standalone | `COMMIT` fails, rollback succeeds | N/A | Cleanly rolled back | No | `DatabaseTransactionError` (chained from commit error) |
| `repository_transaction.__exit__` | Standalone | `COMMIT` fails, rollback fails | N/A | Unsafe | No | `TransactionIntegrityError` (chained from commit error) |
| `repository_transaction.__exit__` | Standalone | Operation fails, rollback fails | N/A | Unsafe | No | `TransactionIntegrityError` (chained from operation error) |
| `UnitOfWork.commit()` | Caller | UoW is poisoned | Already poisoned | Rolled back / unsafe | **NO** | `TransactionIntegrityError` |
| `UnitOfWork.commit()` | Caller | `COMMIT` fails, rollback succeeds | Sets rolled back | Cleanly rolled back | No | `DatabaseTransactionError` |
| `UnitOfWork.commit()` | Caller | `COMMIT` fails, rollback fails | Sets poisoned & unsafe | Unsafe | No | `TransactionIntegrityError` |
| `UnitOfWork.rollback()` | Caller | `ROLLBACK` fails | Sets poisoned & unsafe | Unsafe | No | `TransactionIntegrityError` |
| `UnitOfWork.__exit__()` | Caller | UoW poisoned, caller suppressed error | Already poisoned | Rolled back / unsafe | **NO** | `TransactionIntegrityError` |

---

## 5. Backup Quiescence Design & Guarantees

### 5.1 Enforceable Coordinated Locking (R3)

To ensure that no window exists where another connection or process can touch `target_db_path` during restoration, the system combines two interlocking layers of protection:

1. **Kernel-Level OS Exclusive File Access (`CreateFileW` with `FILE_SHARE_DELETE` on Windows / `flock` on POSIX):**
   - Win32 kernel file-sharing modes are enforced by the operating system I/O manager.
   - When `_acquire_exclusive_target_lock()` opens `target_db_path` with access `GENERIC_READ | GENERIC_WRITE | DELETE` and share mode `FILE_SHARE_DELETE`:
     - If ANY process currently has the file open with standard SQLite flags (`FILE_SHARE_READ | FILE_SHARE_WRITE`), the kernel rejects `CreateFileW` with `ERROR_SHARING_VIOLATION` (32).
     - If NO process has the file open, the kernel grants the handle.
     - While held, ANY other attempt by ANY process to call `sqlite3.connect()` or open the file for reading/writing is rejected by the Windows kernel with `sqlite3.OperationalError: unable to open database file`.
     - Because `FILE_SHARE_DELETE` is granted on the handle, `os.replace(target_db_path, target_bak)` succeeds while the lock is held.
   - The handle is held across temporary restoration, integrity verification, and replacement.
2. **Coordinated Restore Lock (`<target_db_path>.restore_lock`):**
   - At the inception of `restore_backup()`, `<target_db_path>.restore_lock` is created on disk.
   - `DatabaseManager.connect()` explicitly tests for the presence of this file before connecting. If present, it immediately raises `DatabaseBusyError("Database is locked: currently undergoing offline backup restoration.")`.
   - The lock file is safely unlinked in the `finally` block of `restore_backup()`.

### 5.2 Failure Distinctions and Recovery Guarantees (R4)

The restoration procedure strictly distinguishes four terminal execution outcomes:
1. **Successful Restoration:** Source backup verified, target quiesced and locked, temporary database restored and verified, atomic swap completed and verified, lock released, fallback copy unlinked. Returns `BackupResult(..., integrity_status="ok")`.
2. **Restoration Refused Safely:** Source corrupt, or target currently in use by an active/idle connection, or WAL checkpoint busy. Target database was **never** touched or modified. Raises `BackupQuiescenceError` or `BackupIntegrityError`.
3. **Restoration Failed but Original Target Recovered:** An error occurred during replacement (e.g. filesystem error during `tmp_restore -> target` or final verification failure). The original target was recovered from `target_bak` via `os.replace` and verified. Raises `BackupError("Restore operation failed: ... Original target database successfully recovered.")`.
4. **Restoration Failed and Recovery is Uncertain:** An error occurred during replacement AND the attempt to restore `target_bak` back to `target_db_path` also failed. **`target_bak` is NEVER deleted.** Raises `BackupRecoveryError(..., target_path=..., fallback_path=...)` chaining the recovery error, ensuring the surviving copy remains on disk for manual operator inspection.

---

## 6. Changed Files Summary

1. [`python/database/repositories/base.py`](file:///c:/github/gemini_cli_subagent_system/python/database/repositories/base.py)
   - Updated `BaseRepository._check_active()` to check `if getattr(self._uow, "is_poisoned", False): raise TransactionIntegrityError(...)`.
   - Updated `repository_transaction.__init__(self, connection, uow=None)` to accept managing `UnitOfWork`.
   - Updated `repository_transaction.__enter__()` to reject entry if `self._uow` is poisoned.
   - Updated `repository_transaction.__exit__()` to notify/poison `uow` on `RELEASE SAVEPOINT` or `ROLLBACK TO SAVEPOINT` failure, and to wrap standalone commit errors in `DatabaseTransactionError`.
2. [`python/database/repositories/unit_of_work.py`](file:///c:/github/gemini_cli_subagent_system/python/database/repositories/unit_of_work.py)
   - Added `_is_poisoned`, `_poison_reason`, `_poison_cause`, `_conn_unsafe` state flags.
   - Added properties `is_poisoned`, `poison_reason`, `is_conn_unsafe`.
   - Implemented `poison(reason, cause=None, original_error=None)` to mark UoW poisoned, clear repositories, attempt full outer rollback, and mark connection unsafe if rollback fails.
   - Updated `commit()` to reject execution if poisoned or if connection is unsafe.
   - Updated `rollback()` to mark connection unsafe and UoW poisoned if rollback fails.
   - Updated `__exit__()` to intercept poisoned state, prevent commit, and raise `TransactionIntegrityError` if caller caught inner exception.
   - Guarded all 9 repository properties (`positions`, `cash`, `watchlist`, etc.) and `get_db_revision()` with `if self._is_poisoned: raise TransactionIntegrityError(...)`.
3. [`python/database/repositories/financial.py`](file:///c:/github/gemini_cli_subagent_system/python/database/repositories/financial.py)
   - Updated all 6 mutation methods to pass `uow=self._uow` to `repository_transaction(self._conn, uow=self._uow)`.
4. [`python/database/repositories/governance.py`](file:///c:/github/gemini_cli_subagent_system/python/database/repositories/governance.py)
   - Updated all 7 mutation methods to pass `uow=self._uow` to `repository_transaction(self._conn, uow=self._uow)`.
5. [`python/database/repositories/audit.py`](file:///c:/github/gemini_cli_subagent_system/python/database/repositories/audit.py)
   - Updated `append_event()` to pass `uow=self._uow` to `repository_transaction(self._conn, uow=self._uow)`.
6. [`python/database/repositories/cache.py`](file:///c:/github/gemini_cli_subagent_system/python/database/repositories/cache.py)
   - Updated `DailyBarRepository` mutation methods to pass `uow=self._uow`.
7. [`python/database/manager.py`](file:///c:/github/gemini_cli_subagent_system/python/database/manager.py)
   - Added coordinated restore lock check: if `<self.db_path>.restore_lock` exists, `connect()` raises `DatabaseBusyError`.
   - Mapped `"unable to open database file"` in `OperationalError` to `DatabaseBusyError`.
8. [`python/database/backup.py`](file:///c:/github/gemini_cli_subagent_system/python/database/backup.py)
   - Added exception classes `BackupQuiescenceError`, `BackupRecoveryError`, `BackupCleanupError`.
   - Added `_acquire_exclusive_target_lock()` and `_close_exclusive_target_lock()` helpers for kernel-enforced OS locking.
   - Redesigned `restore_backup()` with pre-restoration checks, target quiescence verification, persistent exclusive locking, coordinated restore lock file, isolated temp restoration, safe replacement with fallback preservation, guaranteed non-deletion of fallback on recovery failure, and accurate cleanup error propagation.
9. [`tests/test_database_repositories.py`](file:///c:/github/gemini_cli_subagent_system/tests/test_database_repositories.py)
   - Authored `TestStage15A2TransactionPoisoningAndRecoveryFailureEnforcement` containing 13 dedicated tests covering R1.1–R1.7 and R2.1–R2.6.
10. [`tests/test_database_core.py`](file:///c:/github/gemini_cli_subagent_system/tests/test_database_core.py)
    - Authored `TestStage15A2BackupRestoreQuiescenceAndCleanupSafety` containing 11 dedicated tests covering R3.1–R3.9 and R4.1–R4.2.

---

## 7. Verification & Test Matrix

### 7.1 Database Infrastructure Test Suite
Command: `.\.venv\Scripts\python.exe -m unittest tests/test_database_repositories.py tests/test_database_core.py`
- **Result:** Ran **321 tests** in 19.601s. **OK (0 failures, 0 errors)**.
- Includes 24 new tests specifically authored for Stage 1.5-A.2.

### 7.2 Full Workspace Test Suite
Command: `.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`
- **Result:** Ran **375 tests** in 51.394s. **OK (0 failures, 0 errors)**.
- Zero regressions across the entire workspace.

### 7.3 Compilation and Dependencies
- `py_compile`: Passed across all 10 modified modules with exit code 0.
- `pip check`: Clean pass (`No broken requirements found.`).

---

## 8. Authoritative State & Rule Invariance

Cryptographic SHA-256 hashes of all 8 authoritative state and rule files were computed before and after implementation:

| Authoritative File | Pre-Execution Baseline SHA-256 | Post-Execution SHA-256 | Status |
|---|---|---|---|
| `context/ssot.json` | `0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da` | `0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da` | **INVARIANT** |
| `context/trade_lessons.json` | `0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463` | `0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463` | **INVARIANT** |
| `context/decision_log.json` | `757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2` | `757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2` | **INVARIANT** |
| `context/config.json` | `30ea2b67dd0ecac4b16e3e726c9644948119729023d382c385301f9db72099d5` | `30ea2b67dd0ecac4b16e3e726c9644948119729023d382c385301f9db72099d5` | **INVARIANT** |
| `context/user_config.json` | `88e00f3ef6609eba02066cdfb3750152bfa2b97667aceed5cfba30b5db607183` | `88e00f3ef6609eba02066cdfb3750152bfa2b97667aceed5cfba30b5db607183` | **INVARIANT** |
| `context/agent_logs/agent-events.jsonl` | `58a830fe4a59a02bb16d33df10c1a09b3e76a2b6250377e26ee37a8d73d8bc3d` | `58a830fe4a59a02bb16d33df10c1a09b3e76a2b6250377e26ee37a8d73d8bc3d` | **INVARIANT** |
| `config.json` | `10c005efa15940d9c503aae62bbcb9a195ddbe249387c8335a4cb0f836064ee3` | `10c005efa15940d9c503aae62bbcb9a195ddbe249387c8335a4cb0f836064ee3` | **INVARIANT** |
| `gem_trading_rules/rules.md` | `04ea36c0762de30c841bb25ac35bdffeb6845cccfba19a9b94d343743c075056` | `04ea36c0762de30c841bb25ac35bdffeb6845cccfba19a9b94d343743c075056` | **INVARIANT** |

---

## 9. Production Database Non-Existence

- Verification command: `Test-Path context/gem.db, cache/market_data.db`
- Verification output: `False`, `False`
- Confirmation: Zero production database instances have been initialized or modified during Stage 1.5-A.2.

---

## 10. Outstanding Limitations and Risks

1. **Offline Restoration Contract:** Backup restoration is strictly an offline / disaster-recovery administrative operation. While the locking protocol enforces target quiescence and blocks concurrent application connections via `DatabaseManager.connect()` and Windows kernel file locks, hot online restoration during active multi-agent execution is not supported and will safely fail closed with `BackupQuiescenceError`.
2. **Platform Dependency of Kernel Locking:** On Windows, `CreateFileW` with `FILE_SHARE_DELETE` provides airtight kernel-level exclusion. On POSIX environments, `flock(LOCK_EX | LOCK_NB)` provides advisory exclusion across conforming processes.

---

## 11. Sign-off Recommendations & Next Stages

### 11.1 Stage 1.5-A.2 Sign-Off
All completion criteria for Stage 1.5-A.2 have been achieved:
- A failed nested recovery cannot be followed by a successful UnitOfWork commit.
- Savepoint release and transaction recovery failures are covered by comprehensive failure-injection tests.
- Backup restoration enforces target quiescence and safely refuses operations when unquiesced.
- Recovery and cleanup failures are never silently suppressed.
- All 375 tests in the workspace test suite pass cleanly.
- Authoritative state files and production database boundaries are 100% invariant.

### 11.2 Next Stage Guidance
The SQLite persistence layer hardening (Workstream 1.5-A, Stage 1.5-A.1, and Stage 1.5-A.2) is complete and verified. The repository is in an ideal posture to proceed with **Workstream 1.5-B: JSON SSoT Migration Script & One-Way Backfill** upon repository owner review and sign-off.

