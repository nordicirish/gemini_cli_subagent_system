# Stage 1.5-A.2.2.3 — Restore Lock Ownership Retention, Failed-Release Quarantine & Lifecycle Integrity Report

**Repository:** `nordicirish/gemini_cli_subagent_system`  
**Branch:** `main`  
**Baseline HEAD:** `f83d327a91b91a27f70a1dd243ebc1c61a75a556`  
**Stage:** Stage 1.5-A.2.2.3 (Targeted corrective implementation and verification)  
**Governance Standard:** Agent Governance Framework (`.agents/AGENTS.md`)  
**Commit/Push Authority:** Human Repository Owner Only

---

## 1. Executive Summary

During the independent architectural review of Stage 1.5-A.2.2.2, a critical lifecycle defect was discovered (**Finding A22.2.2-01**):
While `DatabaseRestoreLockToken` correctly implemented a truthful state machine that preserves handles when OS unlock fails, `BackupManager.restore_backup()` previously discarded its local reference to `restore_lock_token` following a failed release. Because the token was an unreferenced local variable, subsequent Python garbage collection cycles (`gc.collect()`) destroyed the token, triggering the underlying Python file object's `__del__` method. Closing the file descriptor dropped the active kernel-level byte-range lock (`LockFileEx`) on Windows and advisory lock (`flock`) on POSIX, silently destroying the restore exclusion that the token state machine was designed to maintain.

**Required Invariant Enforced in Stage 1.5-A.2.2.3:**
> *A failed restore-lock release must never result in loss of the last strong reference to the token or its underlying OS handle while the process remains alive.*

Stage 1.5-A.2.2.3 fully resolves Finding A22.2.2-01 across the system:
1. **Unconditional Token Ownership Preservation:** In `BackupManager.restore_backup()`, `restore_lock_token = None` is strictly guarded by confirmed successful release (`if token.is_released:`). On release failure, the token reference is never discarded.
2. **Explicit Failed-Token Quarantine Registry (`DatabaseRestoreLockQuarantine`):** Implemented a process-level, thread-safe quarantine registry holding strong references to failed tokens and their open OS handles. Tokens are keyed by object identity (`id(token)`), ensuring multiple failed operations never overwrite or drop earlier quarantined locks.
3. **Automated Auto-Quarantine Guarantee:** `DatabaseRestoreLockToken.release()` automatically registers `self` in `DatabaseRestoreLockQuarantine` upon any cleanup failure, and removes `self` upon successful release. All callers—`restore_backup()`, `shared_gate()`, `DatabaseManager.connect()`, and `ManagedConnection.close()`—are permanently protected against handle loss.
4. **Safe, Non-Concurrent Retry API:** Quarantined tokens can be retried via `DatabaseRestoreLockQuarantine.retry_release()` or `retry_all()`. Each entry guards against concurrent retry calls using a dedicated `threading.Lock`. Retries preserve existing state and never issue duplicate OS unlocks for tokens in `UNLOCKED_BUT_CLOSE_FAILED`.
5. **Garbage Collection Immunity Verification:** Failure-injection tests verify that after a failed restore and explicit `gc.collect()`, the token, its file object, and its Win32 handle remain open in quarantine, and concurrent processes/handles attempting exclusive lock acquisition remain strictly blocked fail-closed by the Windows kernel.
6. **Zero Authoritative Drift:** All 385 database-layer tests pass (with 1 platform-specific skip). 100% SHA-256 hash invariance is maintained across all 8 authoritative financial and rule files, with zero production database files created.

---

## 2. Baseline & Environment Verification

Prior to implementation, the repository state and environment were strictly verified:
- **Repository Branch:** `main`
- **Baseline HEAD:** `f83d327a91b91a27f70a1dd243ebc1c61a75a556`
- **Working Tree State:** Clean working tree prior to modification.
- **Python Runtime:** Python 3.11.9 (`.venv\Scripts\python.exe`) on Windows 11.
- **Production Database Non-Existence:** Confirmed absent (`context/gem.db`: False, `cache/market_data.db`: False).
- **Authoritative Hash Baseline:** 100% byte-for-byte match across all 8 governed files.

---

## 3. Root Cause Analysis: Finding A22.2.2-01

In Stage 1.5-A.2.2.2, `DatabaseRestoreLockToken.release()` was enhanced to retain `self._f` and `self._handle` when OS unlock failed (`UNLOCK_FAILED`). However:
1. In `BackupManager.restore_backup()`, `restore_lock_token` was held only as a local function variable.
2. At line 1057 (and line 1137 of `finally:`), `restore_lock_token = None` was called unconditionally, or the function terminated and the variable went out of scope.
3. Once the local reference dropped to 0, Python's memory manager scheduled the token for destruction.
4. During garbage collection, the file object's internal C-level finalizer (`closefd`) invoked `CloseHandle` / `close()`.
5. The operating system kernel automatically released the byte-range lock upon handle closure.
6. Consequently, a token that reported `is_held=True` was stripped of its kernel lock by garbage collection.

**Architectural Fix:**
A token whose release fails is immediately placed in `DatabaseRestoreLockQuarantine`, which stores a persistent strong reference in a process-level registry dictionary `_registry[id(token)] = entry`. Even if local variables are deleted and `gc.collect()` is forced, the token and its file handle remain alive and open in the registry, preventing garbage collection from closing the handle.

---

## 4. Architecture of `DatabaseRestoreLockQuarantine`

### 4.1 Data Structure & Components

```
                               ┌─────────────────────────────────────────┐
                               │     DatabaseRestoreLockQuarantine       │
                               │  _registry: Dict[int, _QuarantineEntry] │
                               │  _lock: threading.Lock                  │
                               └────────────────────┬────────────────────┘
                                                    │
                      ┌─────────────────────────────┴─────────────────────────────┐
                      │                                                           │
                      ▼                                                           ▼
       ┌──────────────────────────────┐                            ┌──────────────────────────────┐
       │   _QuarantineEntry (ID 1)    │                            │   _QuarantineEntry (ID 2)    │
       ├──────────────────────────────┤                            ├──────────────────────────────┤
       │ token: DatabaseRestoreLockTok│                            │ token: DatabaseRestoreLockTok│
       │   (STRONG REFERENCE)         │                            │   (STRONG REFERENCE)         │
       │ db_path: "target.db"         │                            │ db_path: "target.db"         │
       │ retry_lock: threading.Lock() │                            │ retry_lock: threading.Lock() │
       │ retry_count: 0               │                            │ retry_count: 1               │
       │ error: BackupCleanupError    │                            │ error: BackupCleanupError    │
       └──────────────────────────────┘                            └──────────────────────────────┘
```

### 4.2 State Table & Treatment

| Token State | Quarantine Treatment | OS Lock State | Exclusion Guarantee |
|---|---|---|---|
| **`HELD`** | Active ownership; not in quarantine | OS byte-range lock held | Active exclusion enforced by OS |
| **`UNLOCK_FAILED`** | **Quarantined**: strong reference held | OS byte-range lock held | Active exclusion enforced by OS kernel |
| **`UNLOCKED_BUT_CLOSE_FAILED`** | **Quarantined**: handle preserved for retry | OS lock dropped; handle open | Exclusion not claimed (`is_held=False`) |
| **`UNCERTAIN`** | **Quarantined**: handle preserved | Indeterminate | Exclusion unproven (`is_held=False`, `is_uncertain=True`) |
| **`FULLY_RELEASED`** | **Removed from Quarantine** | Cleanly unlocked & closed | None (Target available) |

### 4.3 Safe Non-Concurrent Retry Semantics

1. `DatabaseRestoreLockQuarantine.retry_release(token)`:
   - Locates `_QuarantineEntry` by `id(token)`.
   - Acquires `entry.retry_lock` with `blocking=False`. If another thread is actively retrying, immediately raises `BackupCleanupError("Retry release already in progress...")`.
   - Increments `entry.retry_count`.
   - Invokes `entry.token.release()`.
   - If `token.is_released` is `True`: pops entry from `_registry` and returns `True`.
   - If release fails: updates `entry.error = e`, retains entry in `_registry`, and re-raises the failure.
2. `retry_all(db_path=None)`:
   - Safely iterates snapshot of token IDs, invoking `retry_release()` on each. Returns mapping of token ID to success status.

---

## 5. Token Ownership Lifecycle Across the Repository

### 5.1 `BackupManager.restore_backup()`
- Lock acquired: `restore_lock_token = DatabaseRestoreLock.acquire_exclusive(target_db_path)`.
- Placement release path:
  ```python
  try:
      restore_lock_token.release()
      restore_lock_token = None
  except Exception as ce:
      cleanup_errors.append(ce)
      lock_state_uncertain = True
      DatabaseRestoreLockQuarantine.quarantine(restore_lock_token, ce)
  ```
- If cleanup failed, raises `BackupCleanupError` with `quarantined_token` attached.
- `finally:` cleanup path:
  - If `restore_lock_token` is still held or unreleased, attempts release.
  - If release fails, quarantines token and attaches `quarantined_token` to any in-flight exception via `outer_exc_val`.

### 5.2 `DatabaseRestoreLock.shared_gate()`
- Shared token acquired: `token = cls.acquire_shared(db_path)`.
- In `finally:`, if `token.release()` fails, immediately registers `DatabaseRestoreLockQuarantine.quarantine(token, re)` before propagating error.

### 5.3 `DatabaseManager.connect()`
- If connection configuration or PRAGMA setup fails after `acquire_shared()`, cleanup attempts `token.release()`.
- If release fails, `DatabaseRestoreLockQuarantine.quarantine(token, ce)` preserves the token before raising `DatabaseConnectionError`.

### 5.4 `ManagedConnection.close()`
- Closes SQLite connection first.
- Attempts `_lock_token.release()`.
- If release fails: retains `self._lock_token` reference AND calls `DatabaseRestoreLockQuarantine.quarantine(self._lock_token, e)`.
- If release succeeds: calls `DatabaseRestoreLockQuarantine.remove(self._lock_token)` and clears `self._lock_token = None`.

---

## 6. Failure-Injection Regression Test Architecture (R5)

The test suite in `tests/test_database_backup_concurrency.py` was extended with `TestStage15A223RestoreLockQuarantineAndLifecycleIntegrity` covering all 14 required cases:

| Test Method | Target Requirement | OS Lock Realism | Test Verification Outcome |
|---|---|:---:|---|
| `test_r5_01_through_04_restore_backup_unlock_failed_retains_token_survives_gc_blocks_restore` | R1, R2, R5.1–R5.4 | **REAL WIN32 LOCK** | Unlock failure injected in `restore_backup()`. Verified: (1) token quarantined, (2) local refs deleted and `gc.collect()` executed, (3) handle remains open, (4) **Windows kernel blocks second exclusive lock request fail-closed (`BackupQuiescenceError`)**. |
| `test_r5_05_and_06_retry_quarantined_token_clears_failure_and_removes_from_quarantine` | R3, R5.5, R5.6 | Real OS Lock | Injected failure cleared; `retry_release()` called. Verified token transitions to `FULLY_RELEASED` and is removed from `DatabaseRestoreLockQuarantine`. |
| `test_r5_07_failed_retry_leaves_token_quarantined` | R3, R5.7 | Real OS Lock | Retry called while unlock still fails. Verified exception is raised, retry count incremented, token remains quarantined. |
| `test_r5_08_unlocked_but_close_failed_retries_close_without_second_unlock` | R3, R5.8 | Real OS Lock | Handle close failure injected (`UNLOCKED_BUT_CLOSE_FAILED`). Retry verified that `UnlockFileEx` was **not** called again, and close succeeded. |
| `test_r5_09_uncertain_tokens_remain_quarantined_not_falsely_reported` | R2, R5.9 | Real OS Lock | Ctypes error injected into unlock. Verified `UNCERTAIN` token quarantined, `is_held==False`, `is_uncertain==True`. |
| `test_r5_10_normal_successful_restore_does_not_quarantine` | R1, R5.10 | Real DB & Lock | Clean restore completed; verified quarantine registry count is 0. |
| `test_r5_11_restore_finally_path_retains_tokens_after_destination_error` | R1, R5.11 | Real DB & Lock | Swap failure injected and unlock failure injected in `finally:`. Verified token quarantined and attached to outer `BackupError`. |
| `test_r5_12_multiple_failed_restores_do_not_overwrite_tokens` | R2, R5.12 | Real DB & Lock | Two distinct tokens on the same target both failed unlock. Verified registry holds count=2 without overwriting. |
| `test_r5_13_concurrent_retry_attempts_cannot_release_same_token_twice` | R3, R5.13 | Threaded Concurrency | Simulated concurrent retry via held lock. Verified second thread raises `BackupCleanupError` ("already in progress"). |
| `test_r5_14_managed_connection_and_shared_gate_quarantine_integration` | R4, R5.14 | Real DB & Lock | Verified `shared_gate()` and `ManagedConnection.close()` both quarantine failed tokens and clean up upon successful retry. |

---

## 7. Verification Results Matrix & Test Evidence

### 7.1 Test Execution Summary
- **Targeted Concurrency & Quarantine Suite:** `tests/test_database_backup_concurrency.py` — **64 tests ran (63 passed, 1 skipped)** in 7.07s.
  - 1 platform-specific skip: `test_r5_04_posix_unlock_failure_injection` (POSIX-only guard).
- **Core Database Infrastructure:** `tests/test_database_core.py` — **115 tests passed** in 5.99s (100% OK).
- **Repository Layer Suite:** `tests/test_database_repositories.py` — **206 tests passed** in 16.42s (100% OK).
- **Combined Database Layer Suite:** Ran **385 tests in 28.87s (384 passed, 1 skipped, 0 failures, 0 errors)**.
- **Dependency Integrity (`pip check`):** No broken requirements found.
- **Bytecode Compilation (`py_compile`):** All modified modules compiled cleanly with zero syntax or compilation errors.

---

## 8. Authoritative File Hash Invariance Table

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

## 9. Production Database Non-Existence Verification

Verified strictly:
- `context/gem.db`: **Does not exist (False)**
- `cache/market_data.db`: **Does not exist (False)**

Zero production database initialization or runtime cutover was performed.

---

## 10. Modified File Summary

The working tree contains strictly the approved implementation files and documentation:
1. `python/database/backup.py` — Implemented `DatabaseRestoreLockQuarantine` and `QuarantinedTokenInfo`; updated `DatabaseRestoreLockToken.release()`, `shared_gate()`, and `restore_backup()` with fail-closed quarantine and strong reference preservation.
2. `python/database/manager.py` — Integrated quarantine in `ManagedConnection.close()` and `DatabaseManager.connect()`.
3. `python/database/__init__.py` — Exported `DatabaseRestoreLockQuarantine` and `QuarantinedTokenInfo`.
4. `tests/test_database_backup_concurrency.py` — Added 10 comprehensive tests in `TestStage15A223RestoreLockQuarantineAndLifecycleIntegrity` (64 total suite tests).
5. `docs/engineering/STAGE_1_5_A_2_2_3_RESTORE_LOCK_QUARANTINE_AND_LIFECYCLE_INTEGRITY_REPORT.md` — Authoritative engineering report.

---

## 11. Proposed Git Commit Message

```text
fix(database): enforce restore lock ownership retention and failed-release quarantine (Stage 1.5-A.2.2.3)

- Implement DatabaseRestoreLockQuarantine registry preserving strong references to failed tokens
- Prevent garbage collection from closing OS handles and releasing kernel locks on failed release
- Eliminate unconditional restore_lock_token = None in BackupManager.restore_backup
- Auto-quarantine failed tokens in DatabaseRestoreLockToken.release, shared_gate, and ManagedConnection.close
- Provide thread-safe non-concurrent retry API (retry_release, retry_all) operating on existing tokens
- Preserve multi-token quarantine indexed by token identity, preventing overwrite on repeated failures
- Add 10 regression tests in TestStage15A223RestoreLockQuarantineAndLifecycleIntegrity (385 total DB tests pass)
- Verify 100% SHA-256 hash invariance across all 8 authoritative files with zero production database files created
```

---

## 12. Conclusion & Stop Directive

All objectives and requirements of **Stage 1.5-A.2.2.3** have been completely satisfied, verified against real operating-system kernel locks across garbage collection cycles, and fully documented. The working tree remains uncommitted in strict compliance with the Agent Governance Framework.

**STOPPED.** Awaiting Human Repository Owner review, sign-off, and commit.

