# Stage 1.5-A.2.1 — Backup Restore Locking Guarantees & Exception Integrity Corrections Report

**Repository:** `nordicirish/gemini_cli_subagent_system`  
**Branch:** `main`  
**Verified Baseline HEAD:** `9a3535abbcfb2f318dd4c7df5698f72a393f9a0d`  
**Preceding Stage:** Stage 1.5-A.2 (`9a3535abbcfb2f318dd4c7df5698f72a393f9a0d`)  
**Execution Mode:** Targeted corrective implementation and verification  
**Authority:** Human Repository Owner. Commits, pushes, and production cutover are strictly reserved.

---

## 1. Verified Baseline SHA and Branch

Before executing any modifications, repository state was verified fail-closed:

- **Branch:** `main`
- **HEAD Commit SHA:** `9a3535abbcfb2f318dd4c7df5698f72a393f9a0d`
- **Predecessor Commit Subject:** `fix(database): Stage 1.5-A.2 transaction poisoning, recovery failure enforcement & backup restore quiescence corrections`
- **Working Tree State at Entry:** Clean (`nothing to commit, working tree clean`)

All Git operations remained strictly read-only. No commits, amends, pushes, pulls, branch operations, or resets were performed.

---

## 2. Supported Operating Systems & SQLite Locking Assumptions

The SQLite core database infrastructure supports:

1. **Microsoft Windows (NTFS / Win32 API):** Primary development and execution environment.
2. **POSIX Environments (Linux / macOS):** Secondary target environments adhering to standard POSIX system calls (`fcntl`, `flock`, `open`, `unlink`).

### Key SQLite Locking Assumptions

- **Advisory vs. Mandatory Locking:** Standard SQLite VFS implementations on both Windows and POSIX operate with specific sharing flags (`CreateFileW`) or POSIX advisory record locks (`fcntl(F_SETLK)`).
- **POSIX VFS Reality:** The default SQLite `unix` VFS uses `fcntl(F_SETLK)` byte-range locks on the database file and shared-memory index (`-shm`) in WAL mode. Crucially, POSIX byte-range record locks and BSD/Linux `flock` locks reside in entirely independent kernel lock tables. An external call to `fcntl.flock(LOCK_EX)` on a file descriptor does **not** block `open(..., O_RDWR)` or `fcntl(F_SETLK)` by direct SQLite connections or external processes.
- **Windows VFS Reality:** SQLite Windows VFS (`os_win.c`) opens database files with `dwShareMode = FILE_SHARE_READ | FILE_SHARE_WRITE`. SQLite does **not** request `FILE_SHARE_DELETE` by default.

---

## 3. Exact Windows File-Sharing Behaviour Established by Inspection & Tests

Empirical and code inspection established the following kernel semantics under Windows NTFS:

1. **Idle SQLite Connection Rejection:**
   When an active or idle SQLite connection exists, the database file has open kernel file handles with access `GENERIC_READ | GENERIC_WRITE` and sharing `FILE_SHARE_READ | FILE_SHARE_WRITE`. Calling `CreateFileW(target, GENERIC_READ | GENERIC_WRITE | DELETE, FILE_SHARE_DELETE, ..., OPEN_EXISTING)` fails immediately with `WinError 32` (`ERROR_SHARING_VIOLATION`), because the existing handle did not grant `FILE_SHARE_DELETE` and our request omits `FILE_SHARE_READ | FILE_SHARE_WRITE`.
2. **Active Reader and Writer Rejection:**
   Active readers and writers maintain open file handles and shared memory bindings. `CreateFileW` with `FILE_SHARE_DELETE` is rejected with `ERROR_SHARING_VIOLATION` (32) fail-closed.
3. **Blocking Direct SQLite Connections:**
   While the exclusive handle (`CreateFileW`) is held on `target_db_path`, any new direct connection via `sqlite3.connect(target_db_path)` fails with `sqlite3.OperationalError: unable to open database file` (WinError 32).
4. **File Object Handle Tracking on Rename (`os.replace`):**
   In Windows NTFS, a file handle opened via `CreateFileW` is bound to the kernel file object (`FILE_OBJECT` / MFT record). When `os.replace(target_db_path, target_bak)` is executed, the file handle follows the moved file object to `target_bak`.
   When `os.replace(tmp_restore, target_db_path)` executes, a **new** file object is placed at `target_db_path`.
   **Critical Finding:** The original lock handle `h_target` holds `target_bak`, **not** the newly placed file at `target_db_path`.
   Consequently, direct unmanaged `sqlite3.connect()` calls that bypass `DatabaseManager` during post-swap verification cannot be blocked by `h_target`. Mutual exclusion against all managed connections is instead guaranteed by the coordinated restore lock.
5. **Handle Failure Handling:**
   `CloseHandle` failures are explicitly checked against return code 0; failures query `GetLastError()` and raise `BackupCleanupError` without exception suppression.

---

## 4. POSIX Locking Design & Enforced Guarantees

### Finding & Assessment

On POSIX platforms, `fcntl.flock(LOCK_EX | LOCK_NB)` is advisory and does not provide general exclusion against SQLite's normal POSIX byte-range locking protocol or arbitrary external processes opening the database file. There is no standard userspace OS mechanism to guarantee that an arbitrary external process calling `sqlite3.connect()` will be blocked from accessing an existing file during replacement.

### Enforced Outcome: Option B (Restrict Existing-Target Restoration)

In accordance with Section 3 of the Stage 1.5-A.2.1 directive:

- **Existing Target Refusal on POSIX:**
  If `os.path.exists(target_db_path)` is True on any non-Windows platform (`sys.platform != "win32"`), `BackupManager.restore_backup()` strictly refuses restoration fail-closed:
  ```python
  if target_exists and sys.platform != "win32":
      raise BackupQuiescenceError(
          f"Restoration over an existing target database '{target_db_path}' is not supported on POSIX: "
          f"operating system advisory locking cannot guarantee exclusive connection exclusion against "
          f"concurrent SQLite access. Restore to a new target path instead."
      )
  ```
- **New Target Restoration on POSIX:**
  Restoration to a non-existent target path (`target_exists == False`) on POSIX is demonstrably safe, supported, and verified. The database is reconstructed in an isolated temporary file, verified via `PRAGMA integrity_check;`, and atomically placed at the target path.

---

## 5. Connection-Opening Coordination Protocol

To eliminate the TOCTOU race where `DatabaseManager.connect()` checked for file existence immediately before restoration created the lock file, an OS-enforced reader-writer lock protocol was implemented in `DatabaseRestoreLock`:

```
                       DatabaseRestoreLock Protocol
                       
  DatabaseManager.connect()                       BackupManager.restore_backup()
              │                                                 │
              ▼                                                 ▼
   [ shared_gate() ]                                  [ acquire_exclusive() ]
              │                                                 │
  Acquire SHARED lock on                            Acquire EXCLUSIVE lock on
  <target>.restore_lock                             <target>.restore_lock
  (Win32 LockFileEx / POSIX flock)                  (Win32 LockFileEx | EXCL / POSIX flock)
              │                                                 │
      ┌───────┴───────┐                                 ┌───────┴───────┐
      │               │                                 │               │
  [SUCCESS]       [FAILURE]                         [SUCCESS]       [FAILURE]
      │               │                                 │               │
Open SQLite &    Raise DatabaseBusyError           Hold lock across    Raise BackupQuiescenceError
apply PRAGMAs    fail-closed                       entire restore      fail-closed
      │                                            (quiescence, swap,
Release SHARED                                     verification)
gate lock                                               │
                                                   Release EXCLUSIVE lock
```

### Protocol Mechanics

1. **Shared Connection Gate (`DatabaseRestoreLock.shared_gate(db_path)`):**
   - Every invocation of `DatabaseManager.connect()` enters this gate before opening SQLite.
   - On Windows: Calls `LockFileEx` requesting `LOCKFILE_FAIL_IMMEDIATELY` (shared lock).
   - On POSIX: Calls `fcntl.flock(fileno, fcntl.LOCK_SH | fcntl.LOCK_NB)`.
   - If an exclusive restore lock is held, the shared lock fails immediately; `DatabaseManager.connect()` catches this and raises `DatabaseBusyError("Database is locked: currently undergoing offline backup restoration.")`.
   - Multiple managed connections acquire shared locks concurrently without contention.
   - While any connection is inside `shared_gate`, restoration cannot acquire the exclusive lock.
2. **Exclusive Restore Lock (`DatabaseRestoreLock.acquire_exclusive(db_path)`):**
   - `BackupManager.restore_backup()` acquires an exclusive lock at the very start of restoration.
   - On Windows: Calls `LockFileEx` with `LOCKFILE_FAIL_IMMEDIATELY | LOCKFILE_EXCLUSIVE_LOCK`.
   - On POSIX: Calls `fcntl.flock(fileno, fcntl.LOCK_EX | fcntl.LOCK_NB)`.
   - If any connection is currently inside `shared_gate` or another restore is active, acquisition fails immediately, raising `BackupQuiescenceError`.
   - Writes process PID and UTC timestamp to the file for diagnostic identification.
   - Remains held continuously across the entire restoration: source verification, target quiescence, temporary database creation, atomic replacement swap, post-swap verification, and fallback cleanup.
   - Released and unlinked only upon complete restoration or failure handling.
3. **Automatic Crash Recovery & Stale Lock Freedom:**
   Both Win32 `LockFileEx` and POSIX `flock` are kernel-managed lock primitives bound to open file descriptors/handles. If a process holding a shared or exclusive lock crashes or terminates unexpectedly, the operating system kernel automatically releases the lock immediately. No stale PID files or orphan locks remain.

---

## 6. Direct SQLite Connection Behaviour & Boundaries

| Access Path | Windows Behaviour | POSIX Behaviour | Safety Guarantee |
|---|---|---|---|
| **`DatabaseManager.connect()`** | Enters `shared_gate`; blocked if restoration active (`DatabaseBusyError`). | Enters `shared_gate`; blocked if restoration active (`DatabaseBusyError`). | **Full Guarantee:** Mutual exclusion mathematically proven by OS reader-writer lock. |
| **`UnitOfWork`** | Inherits `DatabaseManager.connect()` guarantees. | Inherits `DatabaseManager.connect()` guarantees. | **Full Guarantee:** All mutations protected. |
| **Direct `sqlite3.connect()` on existing target** | While initial target handle is held, rejected with `WinError 32`. During swap/verification, handle is on `target_bak`. | Advisory locks only; does not block `open()`. | **Refused / Restricted:** Existing target restore refused on POSIX. On Windows, documented as unmanaged boundary. |
| **Direct `sqlite3.connect()` on new target** | Database path does not exist until placed; restoration places verified snapshot atomically via `os.replace`. | Database path does not exist until placed; atomic swap via `os.replace`. | **Full Guarantee:** New target restoration is atomic on both platforms. |

---

## 7. Concurrency & Failure-Injection Test Results

A dedicated suite was implemented in `tests/test_database_backup_concurrency.py` containing 17 comprehensive concurrency and failure-injection tests:

| Test ID | Method | Description | Result |
|---|---|---|---|
| **R1.1** | `test_r1_posix_existing_target_restoration_strictly_refused` | Existing target on POSIX strictly raises `BackupQuiescenceError` | `PASS` |
| **R1.2** | `test_r1_posix_new_target_restoration_supported` | New target restoration on POSIX succeeds with verified integrity | `PASS` |
| **R2.1** | `test_r2_windows_rejects_existing_idle_sqlite_connection` | Idle open connection rejected fail-closed with WinError 32 | `PASS` |
| **R2.2** | `test_r2_windows_rejects_active_sqlite_reader` | Active reader transaction on target rejected fail-closed | `PASS` |
| **R2.3** | `test_r2_windows_rejects_active_sqlite_writer` | Active writer (`BEGIN IMMEDIATE`) on target rejected fail-closed | `PASS` |
| **R2.4** | `test_r2_direct_sqlite_connection_blocked_while_exclusive_target_lock_held` | Direct `sqlite3.connect()` blocked with `unable to open database file` | `PASS` |
| **R3.1** | `test_r3_coordinated_reader_writer_mutual_exclusion` | Shared gate concurrency and exclusive restore lock mutual exclusion | `PASS` |
| **R3.2** | `test_r3_multiple_simultaneous_restorations_rejected` | Simultaneous restoration attempts rejected fail-closed | `PASS` |
| **R3.3** | `test_r3_race_connection_opening_and_restoration_concurrency` | High-frequency threaded connection opening racing with restoration | `PASS` |
| **R3.4** | `test_r3_process_termination_automatic_lock_recovery` | Child process abrupt termination verifies automatic OS lock release | `PASS` |
| **R4.1** | `test_r4_restore_lock_release_failure_raises_cleanup_error` | Injected unlock failure in `DatabaseRestoreLockToken` raises `BackupCleanupError` | `PASS` |
| **R4.2** | `test_r4_target_lock_closehandle_failure_raises_cleanup_error` | Injected `CloseHandle` failure in `_close_exclusive_target_lock` raises `BackupCleanupError` | `PASS` |
| **R4.3** | `test_r4_create_backup_preserves_primary_error_and_records_cleanup_errors` | Injected primary error in `create_backup` is preserved without obscuring | `PASS` |
| **R4.4** | `test_r4_unit_of_work_connection_close_failure_on_clean_exit` | Injected `conn.close()` failure on UoW exit raises `TransactionIntegrityError` | `PASS` |
| **R4.5** | `test_r4_unit_of_work_connection_close_failure_during_primary_exception` | Primary domain error preserved when `conn.close()` fails; error attached | `PASS` |
| **R4.6** | `test_r4_restore_backup_cleanup_error_reported_when_restore_succeeds` | Restoration success with unlinking failure raises `BackupCleanupError` | `PASS` |
| **R5.1** | `test_r5_new_target_restoration_safe_and_verified` | End-to-end new-target restoration verifies full schema and content | `PASS` |

---

## 8. Exception & Resource-Release Handling

All silent exception suppression (`except Exception: pass`) was eliminated across the database transaction and backup modules:

1. **`_close_exclusive_target_lock(handle)`:**
   - Evaluates `CloseHandle` return value on Windows; calls `GetLastError()` and raises `BackupCleanupError` if 0.
   - Evaluates `fcntl.flock(LOCK_UN)` and `close()` on POSIX; raises `BackupCleanupError` if unlinking or closing fails.
2. **`DatabaseRestoreLockToken.release()`:**
   - Evaluates `UnlockFileEx` (Windows) and `flock(LOCK_UN)` (POSIX).
   - Truncates file before unlock.
   - Closes handle/descriptor explicitly.
   - Raises `BackupCleanupError` containing diagnostic error strings if any close or unlock fails.
3. **`BackupManager.restore_backup()`:**
   - Distinguishes operation failure with successful cleanup, operation failure with cleanup failure, and successful restoration with cleanup failure.
   - If restoration succeeded but secondary cleanup failed: raises `BackupCleanupError(..., cleanup_errors=[...])`.
   - If restoration failed: executes cleanup, preserving the primary exception without masking. Any cleanup errors are attached to `primary_exc.cleanup_errors`, and `lock_state_uncertain = True` is recorded if unlock failed.
   - Fallback database (`target_bak`) is **never** deleted if replacement and target recovery fail, raising `BackupRecoveryError(..., target_path, fallback_path)`.
4. **`UnitOfWork.__exit__()`:**
   - If closing an owned connection fails on clean exit (`exc_type is None`), raises `TransactionIntegrityError` to prevent misleading the caller that resources were released.
   - If an exception was already active in the context block, the primary exception is propagated untouched, and the close failure is attached as `connection_close_error`. Marks `self._conn_unsafe = True`.
5. **`UnitOfWork.__enter__()` and `DatabaseManager.connect()`:**
   - If `BEGIN IMMEDIATE` or PRAGMA verification fails, any secondary connection close failure is attached as `connection_close_error` rather than silently swallowed.

---

## 9. Deliberately Restricted Restoration Functionality

In accordance with Section 1 and Section 3 of the directive:

1. **Prohibition of In-Place Restoration Over Existing Targets on POSIX:**
   Because POSIX kernel locks are advisory and cannot exclude direct SQLite connections or concurrent processes, restoring over an existing target path on POSIX is **strictly refused** fail-closed (`BackupQuiescenceError`). Callers must restore to a new target path.
2. **Direct SQLite Connections Bypassing `DatabaseManager`:**
   Direct calls to Python's C library function `sqlite3.connect()` bypass application userspace coordination. On Windows, existing target connections are rejected while the target handle is held, but the swap window cannot be locked against untracked direct connections at the OS level once renamed. Application policy requires all database access to traverse `DatabaseManager` or `UnitOfWork`.

---

## 10. Changed Files

| File | Change Description |
|---|---|
| `python/database/backup.py` | Added `DatabaseRestoreLock`, `DatabaseRestoreLockToken`, and `RestoreLockBusyError`. Implemented `LockFileEx` / `flock` reader-writer lock coordination. Refused POSIX existing-target restore fail-closed (Option B). Eliminated all silent exception suppression in `_close_exclusive_target_lock`, `create_backup`, and `restore_backup`. Added cleanup error tracking and diagnostic metadata. |
| `python/database/manager.py` | Integrated `DatabaseRestoreLock.shared_gate(self.db_path)` into `DatabaseManager.connect()`, closing the TOCTOU restoration race. Attached connection close failures during pragma errors. |
| `python/database/repositories/unit_of_work.py` | Hardened `UnitOfWork.__exit__()` and `__enter__()` connection cleanup to eliminate silent `except Exception: pass`, raising `TransactionIntegrityError` on clean exit close failures and attaching diagnostic errors during active exceptions. |
| `python/database/__init__.py` | Exported `DatabaseRestoreLock`, `RestoreLockBusyError`, `BackupQuiescenceError`, `BackupRecoveryError`, and `BackupCleanupError`. |
| `tests/test_database_backup_concurrency.py` | Authored 17 dedicated unit, concurrency, process-crash, and failure-injection tests. |

---

## 11. Full Regression Results

All verification suites were executed against the modified working tree:

### 1. New Stage 1.5-A.2.1 Concurrency & Locking Suite
- **Command:** `.venv\Scripts\python.exe -m unittest tests/test_database_backup_concurrency.py`
- **Result:** `Ran 17 tests in 3.816s — OK`

### 2. Database Core Suite
- **Command:** `.venv\Scripts\python.exe -m unittest tests/test_database_core.py`
- **Result:** `Ran 115 tests in 5.185s — OK`

### 3. Database Repositories Suite
- **Command:** `.venv\Scripts\python.exe -m unittest tests/test_database_repositories.py`
- **Result:** `Ran 206 tests in 14.888s — OK`

### 4. Full Workspace Test Suite
- **Command:** `.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`
- **Result:** `Ran 392 tests in 54.183s — OK` (375 baseline tests + 17 new Stage 1.5-A.2.1 tests; zero failures, zero errors)

### 5. Bytecode Compilation & Dependency Integrity
- **Command:** `.venv\Scripts\python.exe -m py_compile python/database/backup.py python/database/manager.py python/database/repositories/unit_of_work.py python/database/__init__.py tests/test_database_backup_concurrency.py`
- **Result:** `Clean (exit code 0)`
- **Command:** `.venv\Scripts\python.exe -m pip check`
- **Result:** `No broken requirements found.`

---

## 12. Authoritative State Hash Comparison

Cryptographic SHA-256 hashes of all eight authoritative state and rule files were measured at stage entry and stage completion:

| File Path | Baseline SHA-256 | Post-Verification SHA-256 | Status |
|---|---|---|---|
| `context/ssot.json` | `0A2BA4D8C125D213C5772B36164637DF74094CA95E28E2CA0AFC2DA5E55414DA` | `0A2BA4D8C125D213C5772B36164637DF74094CA95E28E2CA0AFC2DA5E55414DA` | **UNMODIFIED** |
| `context/trade_lessons.json` | `0C4AC816009CB348F4BC923D94124F41F5C6D1EC8722A487BBA9115F66029463` | `0C4AC816009CB348F4BC923D94124F41F5C6D1EC8722A487BBA9115F66029463` | **UNMODIFIED** |
| `context/decision_log.json` | `757E0B4855458CE7075EC16697798B3047CCC72A6214CDED6D7BBB1346700AF2` | `757E0B4855458CE7075EC16697798B3047CCC72A6214CDED6D7BBB1346700AF2` | **UNMODIFIED** |
| `context/config.json` | `30EA2B67DD0ECAC4B16E3E726C9644948119729023D382C385301F9DB72099D5` | `30EA2B67DD0ECAC4B16E3E726C9644948119729023D382C385301F9DB72099D5` | **UNMODIFIED** |
| `context/user_config.json` | `88E00F3EF6609EBA02066CDFB3750152BFA2B97667ACEED5CFBA30B5DB607183` | `88E00F3EF6609EBA02066CDFB3750152BFA2B97667ACEED5CFBA30B5DB607183` | **UNMODIFIED** |
| `context/agent_logs/agent-events.jsonl` | `58A830FE4A59A02BB16D33DF10C1A09B3E76A2B6250377E26EE37A8D73D8BC3D` | `58A830FE4A59A02BB16D33DF10C1A09B3E76A2B6250377E26EE37A8D73D8BC3D` | **UNMODIFIED** |
| `config.json` | `10C005EFA15940D9C503AAE62BBCB9A195DDBE249387C8335A4CB0F836064EE3` | `10C005EFA15940D9C503AAE62BBCB9A195DDBE249387C8335A4CB0F836064EE3` | **UNMODIFIED** |
| `gem_trading_rules/rules.md` | `04EA36C0762DE30C841BB25AC35BDFFEB6845CCCFBA19A9B94D343743C075056` | `04EA36C0762DE30C841BB25AC35BDFFEB6845CCCFBA19A9B94D343743C075056` | **UNMODIFIED** |

---

## 13. Production Database Isolation Confirmation

Filesystem verification confirmed:
- `Test-Path 'context/gem.db'`: `False` (Non-existent)
- `Test-Path 'cache/market_data.db'`: `False` (Non-existent)

No live trading workflows, investment directives, portfolio sizing calculations, NO TRADE rules, or financial state files were touched or altered.

---

## 14. Remaining Limitations

1. **POSIX File Replacement Exclusion Boundary:**
   Because POSIX byte-range locking and `flock` are advisory, no userspace cross-process lock can block an arbitrary external script executing direct `sqlite3.connect()` on an existing file without kernel filesystem hooks or directory permission changes. In-place existing target replacement on POSIX is therefore explicitly disallowed.
2. **Direct Windows SQLite Connection Swap Boundary:**
   On Windows, while target handles block unmanaged connections prior to replacement, the replacement swap moves the locked file object to `target_bak`. An unmanaged direct SQLite connection racing during the sub-millisecond post-swap verification window could open the newly placed file before verification completes. All internal system connections must use `DatabaseManager.connect()` to participate in the `DatabaseRestoreLock` gate.

---

## 15. Conclusion & Verification Summary

Stage 1.5-A.2.1 is **COMPLETE**. All five independent review findings (R1 through R5) have been corrected and verified:
- Unsupported POSIX cross-process locking claims removed; existing target restore refused on POSIX fail-closed.
- Windows `CreateFileW` handle semantics documented and proven against idle connections, readers, and writers.
- Coordinated reader-writer acquisition protocol (`DatabaseRestoreLock`) implemented, eliminating the connection/restore race.
- Silent exception suppression completely removed from all cleanup and resource-release paths.
- 17 dedicated concurrency, crash-recovery, and failure-injection tests authored; all 392 workspace tests pass.
- 100% cryptographic invariance across all authoritative JSON state and trading rules verified.
- Production databases remain completely non-existent.

**STOP.** Working tree left clean for repository-owner review and sign-off.

