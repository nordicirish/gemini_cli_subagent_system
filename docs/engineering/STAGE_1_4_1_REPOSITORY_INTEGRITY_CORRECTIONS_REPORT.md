# Stage 1.4.1 — SQLite Repository Security, Financial Changelog & Transaction Lifecycle Corrections Report

**Repository:** `nordicirish/gemini_cli_subagent_system`  
**Branch:** `main`  
**Baseline HEAD Commit:** `423a48a9b0ef458c214c4eaedc372d435b5b3fc9`  
**Stage:** 1.4.1  
**Mode:** TARGETED ENGINEERING REMEDIATION & VERIFICATION  
**Standard:** Agent Governance Framework (`.agents/AGENTS.md`, Protocols 01–06)  
**Date:** 2026-11-09  
**Author:** Database Architect, Python Engineer & Independent Reviewer  
**Verdict:** **COMPLETE**

---

## 1. Executive Summary & Baseline Verification

Stage 1.4.1 provides targeted corrective remediation and regression verification for the repository layer introduced in Stage 1.4. An independent architectural and security review identified 8 specific integrity, attribution, and transaction lifecycle defects (R1–R8). All 8 defects have been resolved without altering the verified Stage 1.3/1.3.1 SQLite core infrastructure and without modifying live application state.

- **Baseline HEAD Verified:** `423a48a9b0ef458c214c4eaedc372d435b5b3fc9` on `main`.
- **Working Tree Integrity:** Confined strictly to `python/database/repositories/` and `tests/test_database_repositories.py`.
- **Zero Production Databases Created:** `context/gem.db` and `cache/market_data.db` confirmed non-existent.
- **Persistence State Invariance:** Cryptographic SHA-256 hashes of all 6 authoritative file-backed state files (`context/ssot.json`, `context/trade_lessons.json`, `context/decision_log.json`, `context/config.json`, `context/agent_logs/agent-events.jsonl`, `config.json`) remain 100% byte-for-byte invariant.
- **Automated Verification:**
  - Dedicated repository test suite (`tests/test_database_repositories.py`): **77/77 PASS** (including 35 new regression tests).
  - Database core test suite (`tests/test_database_core.py`): **95/95 PASS**.
  - Full workspace regression suite (`unittest discover tests`): **226/226 PASS**.
  - Static compilation (`compileall`) and dependency audit (`pip check`): **0 errors, 0 broken requirements**.

---

## 2. Finding-to-Test Matrix

Every reported finding was mapped to a concrete failure scenario and verified with dedicated regression tests before and after remediation:

| ID | Defect / Vulnerability Description | Severity | Affected Component | Test Class / Test Cases | Status |
|---|---|---|---|---|---|
| **R1** | Optional `record_changelog=False` permitted callers to bypass financial audit logging during cash, position, and watchlist mutations. | **Critical** | `python/database/repositories/financial.py` | `TestR1MandatoryFinancialChangelogEnforcement` (5 tests):<br>• `test_r1_cash_mutation_suppress_changelog_impossible`<br>• `test_r1_position_mutation_always_records_changelog`<br>• `test_r1_watchlist_mutation_always_records_changelog`<br>• `test_r1_deletion_creates_exactly_one_changelog`<br>• `test_r1_parameter_removed_from_public_interface` | **RESOLVED** |
| **R2** | `OwnerApprovalRepository` accepted approval records without verifying active caller role (`Owner`/`Human`), and allowed post-creation tampering of sign-off metadata. | **Critical** | `python/database/repositories/governance.py` | `TestR2OwnerApprovalRecordIntegrity` (7 tests):<br>• `test_r2_non_owner_role_cannot_append_approval`<br>• `test_r2_system_context_cannot_append_approval`<br>• `test_r2_investment_role_cannot_append_approval`<br>• `test_r2_authorized_owner_append_succeeds`<br>• `test_r2_granted_by_forced_to_active_caller_role`<br>• `test_r2_original_approval_fields_immutable_on_consumption`<br>• `test_r2_consumed_record_cannot_be_reconsumed` | **RESOLVED** |
| **R3** | Direct caller insertion via `FinancialChangelogRepository.append_entry()` bypassed role checks and allowed unauthenticated audit record fabrication. | **High** | `python/database/repositories/audit.py` | `TestR3FinancialChangelogRepositoryProtection` (4 tests):<br>• `test_r3_engineering_role_cannot_append_changelog`<br>• `test_r3_unauthorized_role_cannot_append_changelog`<br>• `test_r3_authorized_owner_append_succeeds`<br>• `test_r3_spoofed_actor_role_rejected` | **RESOLVED** |
| **R4** | `_resolve_effective_actor()` allowed default `System` execution context to spoof `Owner` or `Human` actor attribution via `caller_role` argument. | **High** | `python/database/repositories/financial.py` | `TestR4TrustedActorAttribution` (3 tests):<br>• `test_r4_system_context_cannot_claim_owner_role`<br>• `test_r4_system_context_cannot_claim_human_role`<br>• `test_r4_active_owner_context_honors_matching_caller_role` | **RESOLVED** |
| **R5** | Read operations querying `before_state` occurred outside the write transaction, creating time-of-check to time-of-use (TOCTOU) race conditions under concurrency. | **Medium** | `python/database/repositories/financial.py` | `TestR5TransactionSnapshotConsistency` (2 tests):<br>• `test_r5_write_lock_acquired_before_before_state_query`<br>• `test_r5_concurrent_modification_blocked_during_mutation` | **RESOLVED** |
| **R6** | UnitOfWork lifecycle lacked explicit state tracking; permitted re-entering completed UoW, allowed retained repository references after exit, and left dangling transactions on `auto_commit=False`. | **High** | `python/database/repositories/unit_of_work.py`<br>`python/database/repositories/base.py` | `TestR6UnitOfWorkLifecycleEnforcement` (8 tests):<br>• `test_r6_repository_access_invalid_before_uow_enter`<br>• `test_r6_repository_access_during_uow_succeeds`<br>• `test_r6_reentering_completed_uow_fails`<br>• `test_r6_retained_repository_reference_fails_after_commit`<br>• `test_r6_retained_repository_reference_fails_after_rollback`<br>• `test_r6_retained_repository_reference_fails_after_context_exit`<br>• `test_r6_auto_commit_false_rolls_back_on_uncommitted_exit`<br>• `test_r6_auto_commit_false_leaves_no_open_transaction_on_external_conn` | **RESOLVED** |
| **R7** | `DailyBarRepository.upsert_bars_batch()` executed individual autocommit statements, resulting in non-atomic partial writes on mid-batch validation failures. | **High** | `python/database/repositories/cache.py` | `TestR7DailyBarBatchAtomicity` (3 tests):<br>• `test_r7_batch_failure_rolls_back_entire_batch`<br>• `test_r7_batch_success_commits_all_or_nothing`<br>• `test_r7_empty_batch_is_noop` | **RESOLVED** |
| **R8** | Date format validator used regex without calendar verification (permitting invalid dates like `2026-02-30`), and dict-based batch upsert bypassed dataclass validation rules. | **Medium** | `python/database/repositories/base.py`<br>`python/database/repositories/cache.py` | `TestR8DailyBarInputValidation` (3 tests):<br>• `test_r8_calendar_invalid_dates_rejected`<br>• `test_r8_inverted_date_range_rejected`<br>• `test_r8_record_and_dict_validation_equivalence` | **RESOLVED** |

---

## 3. Root Cause Analysis & Detailed Remediation

### 3.1 Workstream R1 — Mandatory Financial Changelog Atomicity
- **Root Cause:** In Stage 1.4, repository methods `set_cash_balance`, `delete_cash`, `upsert_position`, `delete_position`, `upsert_watchlist_item`, and `remove_watchlist_item` exposed an optional `record_changelog: bool = True` parameter. If invoked with `record_changelog=False`, financial state could mutate in SQLite without appending an audit trail record in `financial_changelog`, violating Protocol 02 and enabling undetectable state drifting.
- **Remediation:**
  - Removed `record_changelog` parameter entirely from all public method signatures in `CashRepository`, `PositionRepository`, and `WatchlistRepository`.
  - Every financial mutation unconditionally and atomically generates and inserts a corresponding `financial_changelog` entry within the same immediate transaction.
  - Deletions record a changelog entry with `after_state=None`.

### 3.2 Workstream R2 — Owner Approval Record Integrity
- **Root Cause:** `OwnerApprovalRepository.append_approval` recorded approvals based on caller-supplied strings without verifying that the caller executing in the Python thread was an authentic `Owner` or `Human` role. Furthermore, consumed approvals could theoretically have their core terms modified during consumption.
- **Remediation:**
  - In `OwnerApprovalRepository.append_approval()`, inspected `get_active_caller_role()`. Strictly required `active_role in ("Owner", "Human")`. Any invocation under an engineering role, investment agent role, or default `System` context fails closed with `PermissionError`.
  - Overrode `record.granted_by` with the authenticated `active_role`.
  - In `consume_approval()`, enforced that consumption only updates `consumed_at` and `consumed_by_agent_run_id`, leaving `approval_id`, `requested_action`, `scope`, `parameters`, `granted_by`, and `granted_at` immutable. Attempting to reconsume an already consumed approval raises `OwnerApprovalAlreadyConsumedError`.

### 3.3 Workstream R3 — Protection Against Fabricated Financial Changelogs
- **Root Cause:** `FinancialChangelogRepository.append_entry()` lacked caller authorization checks. An unprivileged caller or rogue subagent could directly instantiate `FinancialChangelogRepository` and inject false audit history records without performing an authorized financial action.
- **Remediation:**
  - In `FinancialChangelogRepository.append_entry()`, added mandatory active caller role validation:
    ```python
    active_role = get_active_caller_role()
    if active_role in ENGINEERING_ROLES:
        raise PermissionError(f"Engineering role '{active_role}' is strictly forbidden from writing financial changelog")
    authorize_financial_mutation(active_role, "append_entry", "financial_changelog")
    if record.actor_role and active_role != "System" and record.actor_role != active_role:
        raise PermissionError(f"Spoofed actor_role '{record.actor_role}' does not match active role '{active_role}'")
    ```
  - Direct changelog appending without financial authorization fails closed immediately.

### 3.4 Workstream R4 — Trusted Actor Attribution
- **Root Cause:** `_resolve_effective_actor()` in `financial.py` accepted an optional `caller_role` argument and used it even when `get_active_caller_role()` returned default `"System"`. This allowed unauthenticated code running without an active execution context to claim `caller_role="Owner"` or `caller_role="Human"`.
- **Remediation:**
  - Modified `_resolve_effective_actor()` to strictly inspect `get_active_caller_role()`.
  - If `active_role == "System"` (the default unauthenticated context), any caller attempt to supply `caller_role in ("Owner", "Human")` is explicitly rejected with `PermissionError("Default System execution context cannot claim Owner/Human actor attribution")`.
  - Caller roles are only accepted if they match the active context or conform to permitted delegation.

### 3.5 Workstream R5 — Transaction Snapshot Consistency (TOCTOU Prevention)
- **Root Cause:** In Stage 1.4, repository mutations queried existing state (`SELECT ... FROM ... WHERE ...`) *before* issuing `BEGIN IMMEDIATE;`. Under concurrent database access, another transaction could modify or delete the record between the read and the lock acquisition, resulting in a stale `before_state` in the changelog.
- **Remediation:**
  - Restructured `set_cash_balance`, `delete_cash`, `upsert_position`, `delete_position`, `upsert_watchlist_item`, and `remove_watchlist_item` to acquire the transaction write lock (`BEGIN IMMEDIATE;` or within the active `UnitOfWork`) **before** issuing the `SELECT` query for `existing`.
  - Guarantees serialized read-modify-write atomicity and zero stale snapshots.

### 3.6 Workstream R6 — Strict Unit of Work Lifecycle & Repository Invalidation
- **Root Cause:** `UnitOfWork` did not explicitly prevent re-entry of an already committed or rolled-back instance. Callers could retain references to repository instances (`uow.positions`) after the UoW context had exited, and invoke methods that unexpectedly used a closed connection or caused dangling transactions. When `auto_commit=False` and no explicit commit occurred, the connection was not rolled back upon exit, leaving write locks active on external connections.
- **Remediation:**
  - Added `_is_active: bool = False` lifecycle state flag to `UnitOfWork`.
  - Bound `uow: Any = self` to all repositories created by `UnitOfWork`.
  - In `BaseRepository`, implemented `_check_active()` which validates `self._uow.is_active` before executing any operation.
  - Upon `commit()`, `rollback()`, or `__exit__()`, `UnitOfWork` marks `_is_active = False` and decouples repository attributes (`_cash = None`, etc.).
  - Calling any method on a retained repository reference after UoW completion raises `RepositoryLifecycleError("Repository is inactive: owning UnitOfWork has completed or closed")`.
  - If `auto_commit=False` and uncommitted changes exist upon `__exit__`, `UnitOfWork` executes an automatic `rollback()` to ensure external connections never hold dangling write locks.
  - Re-entering a closed `UnitOfWork` raises `UnitOfWorkError("UnitOfWork instance cannot be reused")`.

### 3.7 Workstream R7 — Daily Bar Batch Transactional Atomicity
- **Root Cause:** `DailyBarRepository.upsert_bars_batch()` iterated through input records and executed individual queries under SQLite autocommit. If an invalid record was encountered halfway through a batch of 1,000 bars, the first 500 remained committed while the remainder failed, corrupting market-data cache consistency.
- **Remediation:**
  - Refactored `upsert_bars_batch()` to:
    1. Pre-validate all records upfront before touching the database.
    2. Execute within an explicit `BEGIN IMMEDIATE;` transaction block.
    3. Roll back completely on any unexpected error or constraint violation.
    4. Treat empty batches as a clean no-op.

### 3.8 Workstream R8 — Unified Market-Data Validation & Gregorian Calendar Check
- **Root Cause:** `validate_date_str()` used regex `r"^\d{4}-\d{2}-\d{2}$"`, which permitted impossible dates such as `2026-02-30`, `2026-13-40`, or leap-year errors. Furthermore, `upsert_bars_batch()` accepted both `DailyBarRecord` instances and `dict` inputs, but the dict branch used ad-hoc conversions that bypassed certain bounds checks present in `DailyBarRecord`.
- **Remediation:**
  - Updated `validate_date_str()` in `base.py` to parse via `datetime.strptime(date_str, "%Y-%m-%d").date()`, enforcing true Gregorian calendar validity (rejecting invalid month/day counts and leap-year errors).
  - Implemented unified `_validate_bar_inputs()` in `cache.py` used uniformly by both single upserts and batch operations regardless of input type (`DailyBarRecord` or `dict`).
  - Enforced `start_date <= end_date` validation in `list_bars()`, raising `EntityValidationError` fail-closed on inverted ranges.

---

## 4. Persistence Isolation & Security Boundaries

Strict persistence isolation was preserved throughout this corrective stage:

### 4.1 Authoritative State File Cryptographic Invariance (SHA-256)
All 6 authoritative application state files remain 100% byte-for-byte identical to baseline:

| File Path | Baseline SHA-256 | Post-Test SHA-256 | Verification Status |
|---|---|---|---|
| `context/ssot.json` | `0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da` | `0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da` | **INVARIANT (MATCH)** |
| `context/trade_lessons.json` | `0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463` | `0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463` | **INVARIANT (MATCH)** |
| `context/decision_log.json` | `757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2` | `757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2` | **INVARIANT (MATCH)** |
| `context/config.json` | `30ea2b67dd0ecac4b16e3e726c9644948119729023d382c385301f9db72099d5` | `30ea2b67dd0ecac4b16e3e726c9644948119729023d382c385301f9db72099d5` | **INVARIANT (MATCH)** |
| `context/agent_logs/agent-events.jsonl` | `58a830fe4a59a02bb16d33df10c1a09b3e76a2b6250377e26ee37a8d73d8bc3d` | `58a830fe4a59a02bb16d33df10c1a09b3e76a2b6250377e26ee37a8d73d8bc3d` | **INVARIANT (MATCH)** |
| `config.json` | `10c005efa15940d9c503aae62bbcb9a195ddbe249387c8335a4cb0f836064ee3` | `10c005efa15940d9c503aae62bbcb9a195ddbe249387c8335a4cb0f836064ee3` | **INVARIANT (MATCH)** |

### 4.2 Production Database Absence Verification
- `os.path.exists("context/gem.db")`: **False**
- `os.path.exists("cache/market_data.db")`: **False**

No live application databases were created. All tests operate against temporary in-memory or throwaway directory databases (`tempfile.TemporaryDirectory`).

---

## 5. Test Execution Evidence & Regression Verification

### 5.1 Repository Test Suite (`tests/test_database_repositories.py`)
- **Total Tests:** 77
- **Passed:** 77 (100%)
- **Failed:** 0
- **Errors:** 0
- **Execution Time:** ~5.7s
- **Coverage:** Includes 35 newly authored regression tests for R1–R8 and all existing repository CRUD, mapping, decimal, and concurrency tests.

### 5.2 Core Database Test Suite (`tests/test_database_core.py`)
- **Total Tests:** 95
- **Passed:** 95 (100%)
- **Failed:** 0
- **Errors:** 0
- **Execution Time:** ~3.1s
- **Coverage:** Validates that Stage 1.3/1.3.1 core schema migrator, check extractor, backup engine, and transaction primitives remain 100% intact.

### 5.3 Full Workspace Regression Suite (`unittest discover tests`)
- **Total Tests:** 226
- **Passed:** 226 (100%)
- **Failed:** 0
- **Errors:** 0
- **Execution Time:** ~40.2s
- **Regressions:** Zero regressions across trading engine, orchestrator, subagent dispatcher, watchlist endpoints, and atomic I/O.

### 5.4 Static Compilation & Dependency Audit
- `python -m compileall -q python tests`: **Clean exit (code 0)**.
- `pip check`: **No broken requirements found**.

---

## 6. Acceptance Criteria Verification Checklist

| Criterion | Requirement Description | Verification Evidence | Status |
|---|---|---|---|
| **AC-1** | Financial changelog cannot be suppressed on any financial mutation | `test_r1_cash_mutation_suppress_changelog_impossible`<br>`test_r1_position_mutation_always_records_changelog` | **VERIFIED** |
| **AC-2** | Owner approval creation requires active `Owner`/`Human` caller role | `test_r2_non_owner_role_cannot_append_approval`<br>`test_r2_system_context_cannot_append_approval` | **VERIFIED** |
| **AC-3** | Owner approval fields immutable upon consumption; reconsumption rejected | `test_r2_original_approval_fields_immutable_on_consumption`<br>`test_r2_consumed_record_cannot_be_reconsumed` | **VERIFIED** |
| **AC-4** | Direct changelog insertion requires active financial mutation authorization | `test_r3_engineering_role_cannot_append_changelog`<br>`test_r3_spoofed_actor_role_rejected` | **VERIFIED** |
| **AC-5** | Default System context cannot claim Owner/Human actor attribution | `test_r4_system_context_cannot_claim_owner_role`<br>`test_r4_system_context_cannot_claim_human_role` | **VERIFIED** |
| **AC-6** | Transaction write lock acquired before `before_state` is queried | `test_r5_write_lock_acquired_before_before_state_query` | **VERIFIED** |
| **AC-7** | UnitOfWork invalidates repositories on commit, rollback, and exit | `test_r6_retained_repository_reference_fails_after_commit`<br>`test_r6_retained_repository_reference_fails_after_context_exit` | **VERIFIED** |
| **AC-8** | Completed UnitOfWork cannot be re-entered | `test_r6_reentering_completed_uow_fails` | **VERIFIED** |
| **AC-9** | `auto_commit=False` rolls back uncommitted transaction on exit | `test_r6_auto_commit_false_rolls_back_on_uncommitted_exit`<br>`test_r6_auto_commit_false_leaves_no_open_transaction_on_external_conn` | **VERIFIED** |
| **AC-10** | Daily bar batch upsert commits all-or-nothing | `test_r7_batch_failure_rolls_back_entire_batch`<br>`test_r7_batch_success_commits_all_or_nothing` | **VERIFIED** |
| **AC-11** | Calendar-invalid dates rejected by date validator | `test_r8_calendar_invalid_dates_rejected` | **VERIFIED** |
| **AC-12** | Dict and DailyBarRecord paths enforce identical validation rules | `test_r8_record_and_dict_validation_equivalence` | **VERIFIED** |
| **AC-13** | Cryptographic SHA-256 state file invariance verified | Section 4.1; all 6 files 100% identical | **VERIFIED** |
| **AC-14** | Production database files do not exist | Section 4.2; `context/gem.db` & `cache/market_data.db` non-existent | **VERIFIED** |
| **AC-15** | Zero dual-write / zero live application rewiring | All repository code decoupled from live production execution | **VERIFIED** |
| **AC-16** | Git working tree left uncommitted for human repository owner review | `git status` shows uncommitted working tree modifications | **VERIFIED** |

---

## 7. Stage 1.5 Readiness & Conclusion

Stage 1.4.1 has systematically eliminated all 8 identified integrity, security, and transaction lifecycle defects in the repository layer. The persistence abstraction is hardened, fully covered by automated regression tests, and provably isolated from existing application data.

Stage 1.5 (Migration, Projection & Cutover) may safely proceed upon human repository owner approval.

### Final Verdict

**STAGE 1.4.1 COMPLETE**

