# Stage 1.4.1.2 — Financial Mutation Capability Boundary & Savepoint Rollback Failure Integrity Report

**Repository:** `nordicirish/gemini_cli_subagent_system`  
**Branch:** `main`  
**Baseline Commit:** `2dde5ae645a4fcf00b2533e51c5ad95401f03954`  
**Predecessor Stages:** Stage 1.4, Stage 1.4.1, Stage 1.4.1.1  
**Stage:** 1.4.1.2  
**Mode:** TARGETED ENGINEERING REMEDIATION & VERIFICATION  
**Standard:** Agent Governance Framework (`.agents/AGENTS.md`, Protocols 01–06)  
**Date:** 2026-11-09  
**Author:** Database Architect, Python Engineer & Independent Reviewer  
**Verdict:** **COMPLETE**

---

## 1. Executive Summary & Pre-Implementation Assessment

Stage 1.4.1.2 completes the narrowly scoped corrective remediation addressing two outstanding findings from the independent Stage 1.4.1.1 source review:

1. **R1 (High) — Financial Mutation Capability Minting Bypass:**  
   In Stage 1.4.1.1, `create_mutation_capability()` was placed in `python/database/repositories/base.py` and exported alongside the repository classes. Any caller with an active transaction could invoke this factory to mint a valid `_FinancialMutationCapability` and call `FinancialChangelogRepository.append_entry()`, fabricating arbitrary financial changelog records disconnected from any actual state change in `portfolio_cash`, `portfolio_positions`, or `watchlist`.
2. **R2 (Medium) — Silent Suppression of Savepoint Rollback / Release Failures:**  
   In Stage 1.4.1.1, `DailyBarRepository.upsert_bars_batch()` trapped rollback failures inside caller-owned transactions (`owns_tx=False`) and silently swallowed exceptions occurring during `ROLLBACK TO SAVEPOINT` or `RELEASE SAVEPOINT`. This left the caller-owned transaction in an uncertain, potentially partially modified state without warning the caller.

### Pre-Implementation Verification Baseline
- **Baseline HEAD Verified:** `2dde5ae645a4fcf00b2533e51c5ad95401f03954` on branch `main`.
- **Working Tree Cleanliness:** Verified clean working tree prior to remediation.
- **Persistence State Invariance:** Verified 100% cryptographic SHA-256 invariance across all 6 authoritative file-backed state files.
- **Zero Production Databases Created:** Confirmed `context/gem.db` and `cache/market_data.db` do not exist.
- **Automated Verification:**
  - Dedicated repository test suite (`tests/test_database_repositories.py`): **105/105 PASS** (13 new tests across R1 and R2).
  - Database core test suite (`tests/test_database_core.py`): **95/95 PASS**.
  - Full workspace regression suite (`unittest discover tests`): **254/254 PASS**.
  - Bytecode compilation (`compileall`): **0 errors**.
  - Dependency audit (`pip check`): **0 broken requirements**.

---

## 2. Exact Files Changed

The remediation was strictly confined to repository modules and tests:

| File Path | Description of Changes |
|---|---|
| `python/database/repositories/base.py` | Added `TransactionIntegrityError(RepositoryError)` to the exception hierarchy. Completely removed `_MUTATION_CAPABILITY_KEY`, `_FinancialMutationCapability`, and `create_mutation_capability()` from `base.py` to eliminate public factory exposure. |
| `python/database/repositories/__init__.py` | Exported `TransactionIntegrityError` in `__all__`. |
| `python/database/repositories/financial.py` | Defined private module token `_MUTATION_KEY = object()` and private `_FinancialMutationCapability` class requiring `key is _MUTATION_KEY`. Encapsulated capability creation strictly inside `_record_changelog()` during genuine state mutations. |
| `python/database/repositories/audit.py` | Hardened `FinancialChangelogRepository.append_entry()` to validate `_FinancialMutationCapability`, ensure matching connection, active transaction, and single-use consumption. Added active transactional database state verification querying `portfolio_cash`, `portfolio_positions`, or `watchlist` on `self._conn` to confirm the row was actually modified or deleted before inserting the changelog record. |
| `python/database/repositories/cache.py` | Updated `DailyBarRepository.upsert_bars_batch()`: on savepoint rollback or release failure inside a caller-owned transaction, raises `TransactionIntegrityError` with diagnostic warning that transaction is unsafe to commit, preserving root cause via `__cause__`. Explicitly re-raises `TransactionIntegrityError` to avoid secondary rollback collisions. |
| `tests/test_database_repositories.py` | Authored 13 dedicated regression tests across `TestR1FinancialMutationCapabilityBoundary` (Tests A through G) and `TestR2SavepointRollbackFailureIntegrity` (Tests A through F). Added connection proxy pattern (`FaultyRollbackProxy`, `FaultyReleaseProxy`) for controlled fault injection without mutating read-only C descriptors on `sqlite3.Connection`. |

---

## 3. Root Cause Analysis & Detailed Remediation

### 3.1 Workstream R1 — Capability Minting Boundary & State Decoupling Prevention

#### Root Cause
In Stage 1.4.1.1, `create_mutation_capability` in `base.py` accepted any active `connection` and returned a valid capability. Callers could pass arbitrary `before_state`, `after_state`, `operation_name`, and `target_resource`. While `FinancialChangelogRepository.append_entry()` verified capability presence, it trusted the capability's fields without verifying that an underlying mutation had occurred in the database tables.

#### Remediation Architecture
1. **Elimination of Public Factory:**  
   `create_mutation_capability`, `_MUTATION_CAPABILITY_KEY`, and `_FinancialMutationCapability` were completely purged from `base.py`. They are not part of any public module interface.
2. **Strict Internal Encapsulation:**  
   `_MUTATION_KEY = object()` is defined privately within `python/database/repositories/financial.py`. `_FinancialMutationCapability.__init__` requires `key is _MUTATION_KEY`. Direct instantiation outside `financial.py` raises `PermissionError("Direct instantiation of _FinancialMutationCapability is prohibited.")`.
3. **Capability Minting Bound to Trusted Mutations:**  
   Capabilities are minted exclusively within `_record_changelog()` in `financial.py` during `set_cash_balance`, `delete_cash`, `upsert_position`, `delete_position`, `upsert_watchlist_item`, and `remove_watchlist_item`.
4. **Active Database State Verification in Audit Repository:**  
   When `FinancialChangelogRepository.append_entry()` receives a capability, before inserting the changelog row, it queries `self._conn` to verify that the claimed mutation actually exists in the database within the current uncommitted transaction:
   - For `portfolio_cash`: verifies the row exists with exact `amount` and `currency` (or was deleted for `DELETE`).
   - For `portfolio_positions`: verifies the row exists with exact `shares` and `wac_eur` (or was deleted for `DELETE`).
   - For `watchlist`: verifies the row exists with exact `status` and `notes` (or was deleted for `DELETE`).
   If the state in the database does not match `after_state`, `append_entry()` raises `IntegrityConflictError`, preventing decoupling of changelog records from physical state changes.
5. **Single-Use Consumption:**  
   Upon successful insert, `mutation_capability.mark_consumed()` is called. Re-using a consumed capability raises `PermissionError`.

---

### 3.2 Workstream R2 — Savepoint Rollback & Release Failure Integrity

#### Root Cause
In Stage 1.4.1.1, `DailyBarRepository.upsert_bars_batch()` wrapped `ROLLBACK TO SAVEPOINT` in `try...except Exception: pass`. If SQLite encountered an I/O error, lock failure, or resource constraint during rollback, the error was silently ignored. The caller's transaction was left in an unknown state with partially inserted batch rows, and the caller might unknowingly commit corrupt or incomplete data.

#### Remediation Architecture
1. **Explicit `TransactionIntegrityError` Hierarchy:**  
   Added `TransactionIntegrityError(RepositoryError)` to `base.py` and exported it via `__init__.py`.
2. **Fail-Closed Savepoint Rollback Semantics:**  
   When a batch insert fails inside a caller-owned transaction (`owns_tx=False`):
   ```python
   try:
       self._conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name};")
       self._conn.execute(f"RELEASE SAVEPOINT {sp_name};")
   except Exception as rollback_err:
       raise TransactionIntegrityError(
           f"CRITICAL: Failed to roll back savepoint '{sp_name}' during batch failure: {rollback_err}. "
           f"The caller-owned transaction is in an uncertain state and MUST NOT be committed. "
           f"Full transaction rollback or connection termination is required."
       ) from e
   ```
   The original batch error (`e`) is preserved as `__cause__`, providing complete diagnostic traceability.
3. **Fail-Closed Savepoint Release Semantics:**  
   If the batch succeeds but `RELEASE SAVEPOINT` fails:
   ```python
   try:
       self._conn.execute(f"RELEASE SAVEPOINT {sp_name};")
   except Exception as release_err:
       raise TransactionIntegrityError(
           f"CRITICAL: Failed to release savepoint '{sp_name}' after successful batch: {release_err}. "
           f"The caller-owned transaction is in an uncertain state and MUST NOT be committed."
       ) from release_err
   ```
4. **Clean Exception Propagation:**  
   `upsert_bars_batch()` catches `TransactionIntegrityError` first and immediately re-raises it, preventing secondary rollback collision loops.

---

## 4. Comprehensive Regression Verification Matrix

13 new dedicated regression tests were authored across 2 test classes in `tests/test_database_repositories.py`:

| Finding | Test Class | Test Method | Scenario Tested | Outcome |
|---|---|---|---|---|
| **R1** | `TestR1FinancialMutationCapabilityBoundary` | `test_a_direct_factory_bypass` | Confirms `create_mutation_capability` cannot be imported from `base.py` or `repositories`, and `_FinancialMutationCapability` rejects direct instantiation. | **PASS** |
| **R1** | `TestR1FinancialMutationCapabilityBoundary` | `test_b_fabricated_changelog` | Confirms calling `append_entry()` directly without a valid capability raises `PermissionError`. | **PASS** |
| **R1** | `TestR1FinancialMutationCapabilityBoundary` | `test_c_genuine_financial_mutation` | Confirms authorized mutations across cash, positions, and watchlist mint capabilities and record changelogs. | **PASS** |
| **R1** | `TestR1FinancialMutationCapabilityBoundary` | `test_d_capability_replay` | Confirms a capability cannot be reused for a second `append_entry()` call. | **PASS** |
| **R1** | `TestR1FinancialMutationCapabilityBoundary` | `test_e_connection_binding` | Confirms a capability minted on connection 1 cannot be submitted on connection 2. | **PASS** |
| **R1** | `TestR1FinancialMutationCapabilityBoundary` | `test_f_transactional_rollback` | Confirms that rolling back a mutation transaction discards both the financial state change and the changelog. | **PASS** |
| **R1** | `TestR1FinancialMutationCapabilityBoundary` | `test_g_existing_repository_compatibility` | Confirms UnitOfWork multi-repository transactions produce exact, atomic changelog entries. | **PASS** |
| **R2** | `TestR2SavepointRollbackFailureIntegrity` | `test_a_nested_batch_failure_with_successful_rollback` | Confirms failed batch rolls back cleanly to savepoint, preserves caller transaction, and caller can commit unaffected work. | **PASS** |
| **R2** | `TestR2SavepointRollbackFailureIntegrity` | `test_b_nested_batch_success` | Confirms successful batch leaves rows uncommitted in caller transaction, and caller can roll back entire transaction. | **PASS** |
| **R2** | `TestR2SavepointRollbackFailureIntegrity` | `test_c_injected_rollback_failure` | Injects savepoint rollback failure; asserts `TransactionIntegrityError` is raised with `__cause__` and warning that transaction must not be committed. | **PASS** |
| **R2** | `TestR2SavepointRollbackFailureIntegrity` | `test_d_savepoint_release_failure` | Injects savepoint release failure; asserts `TransactionIntegrityError` is raised and transaction is flagged unsafe. | **PASS** |
| **R2** | `TestR2SavepointRollbackFailureIntegrity` | `test_e_repository_owned_transaction` | Confirms standalone repository-owned batch commits cleanly on success and rolls back cleanly on failure. | **PASS** |
| **R2** | `TestR2SavepointRollbackFailureIntegrity` | `test_f_existing_transaction_preservation` | Confirms caller-owned transaction remains active (`in_transaction=True`) after successful nested batch execution. | **PASS** |

### Test Suite Execution Summary
- **Repository Test Suite:** `105 passed in 7.93s` (0 failures, 0 errors).
- **Core Database Suite:** `95 passed in 3.45s` (0 failures, 0 errors).
- **Full Workspace Regression Suite:** `254 passed in 40.94s` (0 failures, 0 errors).
- **Static Compilation:** `compileall` clean (0 syntax errors).
- **Dependency Audit:** `pip check` clean (0 broken dependencies).

---

## 5. Persistence Isolation & Security Boundaries

### 5.1 Authoritative State File Cryptographic Invariance (SHA-256)
All 6 authoritative application state files remain 100% byte-for-byte identical to baseline:

| File Path | Baseline SHA-256 | Post-Verification SHA-256 | Status |
|---|---|---|---|
| `context/ssot.json` | `0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da` | `0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da` | **INVARIANT** |
| `context/trade_lessons.json` | `0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463` | `0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463` | **INVARIANT** |
| `context/decision_log.json` | `757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2` | `757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2` | **INVARIANT** |
| `context/config.json` | `30ea2b67dd0ecac4b16e3e726c9644948119729023d382c385301f9db72099d5` | `30ea2b67dd0ecac4b16e3e726c9644948119729023d382c385301f9db72099d5` | **INVARIANT** |
| `context/agent_logs/agent-events.jsonl` | `58a830fe4a59a02bb16d33df10c1a09b3e76a2b6250377e26ee37a8d73d8bc3d` | `58a830fe4a59a02bb16d33df10c1a09b3e76a2b6250377e26ee37a8d73d8bc3d` | **INVARIANT** |
| `config.json` | `10c005efa15940d9c503aae62bbcb9a195ddbe249387c8335a4cb0f836064ee3` | `10c005efa15940d9c503aae62bbcb9a195ddbe249387c8335a4cb0f836064ee3` | **INVARIANT** |

### 5.2 Production Database Absence Verification
- `context/gem.db`: **DOES NOT EXIST**
- `cache/market_data.db`: **DOES NOT EXIST**

All tests ran exclusively in isolated temporary directories (`tempfile.TemporaryDirectory`).

---

## 6. Limitations and Residual Risks Analysis

1. **In-Process Python Boundary:**  
   The protection against fabricated changelog entries relies on module encapsulation (`_MUTATION_KEY`) and active thread execution context (`get_active_caller_role()`). In an in-process Python runtime, code with direct access to private module variables could theoretically access internal objects. However, within the agent governance system, subagents interact strictly via tool dispatch interfaces and cannot execute arbitrary shell or python code inside the application runtime.
2. **Active Database State Verification Scope:**  
   `FinancialChangelogRepository.append_entry()` verifies that the mutated row actually exists in `portfolio_cash`, `portfolio_positions`, or `watchlist` with values matching `after_state` before recording the changelog. This guarantees that changelog records cannot be decoupled from physical row changes.
3. **Savepoint Failure Fail-Closed Policy:**  
   When a savepoint rollback fails, SQLite transactions may be partially tainted. Raising `TransactionIntegrityError` explicitly informs the caller that their transaction cannot be safely committed, preventing silent data corruption.

---

## 7. Final Verdict

**COMPLETE**

Both findings (R1 and R2) from Stage 1.4.1.1 source review have been eliminated. The persistence layer now guarantees that financial changelog entries cannot be minted or fabricated outside trusted state mutations, and savepoint rollback failures fail closed with explicit diagnostic exceptions.

All changes remain uncommitted in the local working tree for human repository owner review.

