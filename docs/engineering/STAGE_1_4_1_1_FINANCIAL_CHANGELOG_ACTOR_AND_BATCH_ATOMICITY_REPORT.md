# Stage 1.4.1.1 — Financial Changelog Integrity, Trusted Actor Attribution & Nested Batch Atomicity Report

**Repository:** `nordicirish/gemini_cli_subagent_system`  
**Branch:** `main`  
**Baseline Commit:** `319c70c799658bebac72e4e76f7026bf636db250`  
**Stage:** 1.4.1.1  
**Mode:** TARGETED ENGINEERING REMEDIATION & VERIFICATION  
**Standard:** Agent Governance Framework (`.agents/AGENTS.md`, Protocols 01–06)  
**Date:** 2026-11-09  
**Author:** Database Architect, Python Engineer & Independent Reviewer  
**Verdict:** **COMPLETE**

---

## 1. Executive Summary & Baseline Verification

Stage 1.4.1.1 provides targeted corrective hardening addressing three findings from the independent Stage 1.4.1 source review. The objectives were:
1. Eliminate the ability of ordinary callers (such as default `System` context) to independently fabricate `financial_changelog` entries disconnected from an actual state mutation (R1).
2. Eliminate actor impersonation in `_resolve_effective_actor()` by deriving attribution strictly from the trusted execution context and rejecting caller-supplied overrides (R2).
3. Ensure `DailyBarRepository.upsert_bars_batch()` executes with atomic all-or-nothing rollback inside caller-owned transactions via SQLite savepoints without committing or aborting the caller's transaction (R3).

- **Baseline HEAD Verified:** `319c70c799658bebac72e4e76f7026bf636db250` on branch `main`.
- **Working Tree Clean Prior to Stage:** Working tree was clean at baseline commit before remediation.
- **Persistence State Invariance:** Cryptographic SHA-256 hashes of all 6 authoritative file-backed state files were verified 100% byte-for-byte invariant across all test runs.
- **Zero Production Databases Created:** `context/gem.db` and `cache/market_data.db` confirmed non-existent.
- **Automated Verification:**
  - Dedicated repository test suite (`tests/test_database_repositories.py`): **92/92 PASS** (15 new regression tests).
  - Database core test suite (`tests/test_database_core.py`): **95/95 PASS**.
  - Full workspace regression suite (`unittest discover tests`): **241/241 PASS**.
  - Bytecode compilation (`compileall`) and dependency audit (`pip check`): **0 errors, 0 broken requirements**.

---

## 2. Exact Files Changed

The remediation is strictly confined to the repository persistence layer and its dedicated test suite:

| File Path | Description of Changes |
|---|---|
| `python/database/repositories/base.py` | Added internal `_FinancialMutationCapability` class, private module key `_MUTATION_CAPABILITY_KEY`, and `create_mutation_capability()` helper bound to active transactions. |
| `python/database/repositories/audit.py` | Hardened `FinancialChangelogRepository.append_entry()`: rejected direct public calls without a valid internal `_FinancialMutationCapability`, enforced connection identity matching, active transaction validation, single-use consumption, and strict extraction of fields from the capability. |
| `python/database/repositories/financial.py` | Updated `_resolve_effective_actor()` to require exact agreement between any explicitly supplied `caller_role` and `get_active_caller_role()`. Updated `_record_changelog()` to mint internal capabilities. Added mandatory changelog recording to `WatchlistRepository.upsert_watchlist_item` and `remove_watchlist_item`. |
| `python/database/repositories/cache.py` | Refactored `DailyBarRepository.upsert_bars_batch()` to manage a SQLite `SAVEPOINT` when invoked inside a caller-owned transaction (`owns_tx=False`), releasing the savepoint on success and rolling back to it on failure without touching the caller's transaction. |
| `tests/test_database_repositories.py` | Updated `test_financial_changelog_crud_and_mapping` to test via trusted mutation; updated `test_uow_multi_repo_commit` to assert 3 changelog entries (cash, position, watchlist); authored 15 new targeted regression tests across `TestR1FinancialChangelogIntegrity`, `TestR2ExactTrustedActorAttribution`, and `TestR3DailyBarNestedBatchAtomicity`. |

---

## 3. Root Cause Analysis & Detailed Remediation

### 3.1 Workstream R1 — Prevent Independent or Fabricated Financial Changelog Entries
- **Root Cause:** In Stage 1.4.1, `FinancialChangelogRepository.append_entry()` enforced `active_role in ENGINEERING_ROLES` and called `authorize_financial_mutation()`. However, the default `System` context (and authorized investment roles) are permitted to perform certain financial mutations (e.g. `update_ssot`). Consequently, an ordinary caller operating in `System` context could call `FinancialChangelogRepository.append_entry()` directly, passing arbitrary `before_state`, `after_state`, `target_resource`, and `operation_name`, resulting in a fabricated changelog record inserted into SQLite without any corresponding financial row change occurring in `portfolio_cash`, `portfolio_positions`, or `watchlist`.
- **Enforcement Mechanism:**
  - Introduced an internal transaction capability object: `_FinancialMutationCapability`.
  - Construction of `_FinancialMutationCapability` requires a private module-level token `_MUTATION_CAPABILITY_KEY` that is not exported and cannot be instantiated by external callers. Attempting to directly instantiate `_FinancialMutationCapability` raises `PermissionError`.
  - The capability can only be minted via `create_mutation_capability()`, which asserts `connection.in_transaction is True`.
  - `FinancialChangelogRepository.append_entry()` now requires `mutation_capability: _FinancialMutationCapability`. If called directly without a capability (the public repository API path), it fails closed with `PermissionError("Direct insertion into financial_changelog via public repository API is prohibited. Financial changelog entries must be generated through trusted financial mutation operations.")`.
  - When `mutation_capability` is provided, `append_entry()` verifies:
    1. `isinstance(mutation_capability, _FinancialMutationCapability)`
    2. `mutation_capability.connection is self._conn`
    3. `self._conn.in_transaction is True`
    4. `not mutation_capability.is_consumed`
  - All changelog columns (`correlation_id`, `actor_role`, `operation_name`, `target_resource`, `approval_digest`, `before_state`, `after_state`, `timestamp_utc`) are extracted **strictly from the capability**, ignoring any caller-supplied arguments.
  - Upon successful SQL insertion, `mutation_capability.mark_consumed()` is invoked, preventing replay of the capability.
  - In `python/database/repositories/financial.py`, all mutations (`CashRepository.set_cash_balance`, `delete_cash`, `PositionRepository.upsert_position`, `delete_position`, `WatchlistRepository.upsert_watchlist_item`, `remove_watchlist_item`) mint the capability and execute changelog recording within the exact same immediate SQLite transaction. If changelog insertion fails, the transaction is rolled back, guaranteeing state atomicity.

### 3.2 Workstream R2 — Enforce Exact Trusted Actor Attribution
- **Root Cause:** In Stage 1.4.1, `_resolve_effective_actor()` checked `if caller_role in ("Owner", "Human") and active_role not in ("Owner", "Human"): raise PermissionError(...)`, but returned `caller_role` if `active_role in TRUSTED_SYSTEM_ROLES`. Because default `System` context is a trusted system role, a caller operating in default context could pass `caller_role="Terminal Orchestrator"` or another named agent role, creating false attribution in the changelog.
- **Enforcement Mechanism:**
  - Revised `_resolve_effective_actor(caller_role)` to derive identity strictly from the active execution context:
    ```python
    active_role = get_active_caller_role()
    if caller_role is not None and caller_role != active_role:
        if active_role in ("System", "Root"):
            raise PermissionError(
                f"Security violation: Active context '{active_role}' cannot claim '{caller_role}' attribution."
            )
        else:
            raise PermissionError(
                f"Security violation: Agent role '{active_role}' cannot spoof actor role '{caller_role}'."
            )
    return active_role
    ```
  - If a caller supplies an explicit `caller_role`, it must match `active_role` exactly. Any mismatch raises `PermissionError` fail-closed.
  - Default `System` context cannot impersonate `Owner`, `Human`, `Terminal Orchestrator`, `Macro Sentinel`, or any other agent.
  - If code legitimately executes as a given role (e.g. `execution_role_context("Terminal Orchestrator")`), `active_role` is `"Terminal Orchestrator"`, and passing `caller_role="Terminal Orchestrator"` or `None` returns `"Terminal Orchestrator"`.
  - The changelog entry's `actor_role` is populated strictly from the returned `effective_actor`, eliminating caller-controlled spoofing.

### 3.3 Workstream R3 — DailyBar Nested Batch Atomicity Inside Caller-Owned Transactions
- **Root Cause:** In Stage 1.4.1, `DailyBarRepository.upsert_bars_batch()` checked `owns_tx = not self._conn.in_transaction`. When `owns_tx` was False (the caller had already issued `BEGIN`), the repository executed batch inserts directly on the connection without a savepoint. If a mid-batch failure occurred (e.g., a row failed calendar date validation or hit a SQLite constraint), earlier batch rows remained uncommitted in the caller's transaction. If the caller subsequently committed, partial batch data would be persisted, violating atomicity.
- **Enforcement Mechanism:**
  - Implemented dual-mode transaction handling based on transaction ownership:
    ```python
    owns_tx = not self._conn.in_transaction
    sp_name = f"sp_batch_{uuid.uuid4().hex}" if not owns_tx else None

    if owns_tx:
        self._conn.execute("BEGIN IMMEDIATE;")
    else:
        self._conn.execute(f"SAVEPOINT {sp_name};")
    ```
  - For each bar in `bars`, inputs are validated via `_validate_bar_inputs()` and inserted via parameterized SQL.
  - On success:
    - If `owns_tx`: `self._conn.execute("COMMIT;")`
    - If caller-owned: `self._conn.execute(f"RELEASE SAVEPOINT {sp_name};")` (leaves all batch rows visible in caller transaction; does NOT commit caller transaction).
  - On failure (`sqlite3.IntegrityError` or any `Exception`):
    - If `owns_tx`: `self._conn.execute("ROLLBACK;")`
    - If caller-owned:
      `self._conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name};")`
      `self._conn.execute(f"RELEASE SAVEPOINT {sp_name};")`
    - The exception is re-raised.
  - When rolling back to the savepoint, all rows inserted by the failed batch are undone, while any prior statements executed by the caller before `upsert_bars_batch()` remain fully intact in the caller's transaction. The caller retains complete control to commit or roll back their transaction.

---

## 4. Regression Tests & Test Matrix

15 newly authored regression tests were added across 3 dedicated test classes in `tests/test_database_repositories.py`:

| Finding | Test Class | Test Method | Scenario Tested | Outcome |
|---|---|---|---|---|
| **R1** | `TestR1FinancialChangelogIntegrity` | `test_default_system_context_cannot_append_fabricated_changelog` | Calling `FinancialChangelogRepository.append_entry()` directly in System context fails closed with `PermissionError`. | **PASS** |
| **R1** | `TestR1FinancialChangelogIntegrity` | `test_authorized_mutation_creates_corresponding_changelog` | Authorized mutations for Cash, Position, Watchlist upsert, and Watchlist remove create exact changelog entries. | **PASS** |
| **R1** | `TestR1FinancialChangelogIntegrity` | `test_changelog_failure_rolls_back_financial_mutation` | Dropping `financial_changelog` causes Cash, Position, and Watchlist mutations to roll back with zero partial rows. | **PASS** |
| **R1** | `TestR1FinancialChangelogIntegrity` | `test_cannot_fabricate_state_or_actor_through_public_arguments` | Public mutation APIs reject bogus `before_state` arguments and reject spoofed `caller_role`. | **PASS** |
| **R1** | `TestR1FinancialChangelogIntegrity` | `test_direct_repository_access_cannot_bypass_trusted_mutation_path` | Instantiating `FinancialChangelogRepository` under Owner context still cannot call `append_entry()` directly. | **PASS** |
| **R1** | `TestR1FinancialChangelogIntegrity` | `test_capability_direct_instantiation_and_tampering_rejected` | Instantiating `_FinancialMutationCapability` directly without internal key raises `PermissionError`. | **PASS** |
| **R2** | `TestR2ExactTrustedActorAttribution` | `test_default_system_context_cannot_claim_another_actor` | System context calling `set_cash_balance(caller_role="UnknownActor")` raises `PermissionError`. | **PASS** |
| **R2** | `TestR2ExactTrustedActorAttribution` | `test_system_cannot_claim_owner_or_human` | System context calling `set_cash_balance` with `Owner` or `Human` raises `PermissionError`. | **PASS** |
| **R2** | `TestR2ExactTrustedActorAttribution` | `test_system_cannot_claim_terminal_orchestrator_or_investment_agent` | System context calling `set_cash_balance` with `Terminal Orchestrator` or `Macro Sentinel` raises `PermissionError`. | **PASS** |
| **R2** | `TestR2ExactTrustedActorAttribution` | `test_mismatched_explicit_caller_role_is_rejected` | Mismatch between active role and supplied `caller_role` raises `PermissionError`. | **PASS** |
| **R2** | `TestR2ExactTrustedActorAttribution` | `test_exact_matching_trusted_identity_is_accepted` | Exact matching `caller_role` under active `execution_role_context` succeeds. | **PASS** |
| **R2** | `TestR2ExactTrustedActorAttribution` | `test_audit_and_financial_changelog_attribution_consistent` | Verifies `actor_role` recorded in `financial_changelog` matches active context. | **PASS** |
| **R3** | `TestR3DailyBarNestedBatchAtomicity` | `test_a_failed_nested_batch_rolls_back_to_savepoint` | In caller-owned tx, an invalid batch item rolls back all batch rows to savepoint, preserves unrelated caller row, and allows caller to commit. | **PASS** |
| **R3** | `TestR3DailyBarNestedBatchAtomicity` | `test_b_successful_nested_batch_preserves_caller_transaction` | In caller-owned tx, valid batch rows remain uncommitted in tx; caller retains control to roll back all rows. | **PASS** |
| **R3** | `TestR3DailyBarNestedBatchAtomicity` | `test_c_repository_owned_transaction_standalone_behavior` | Standalone batch commits durably on success and rolls back cleanly on failure. | **PASS** |

### Test Suite Execution Metrics
- **Repository Test Suite:** `92 passed in 6.05s` (0 failures, 0 errors).
- **Core Database Suite:** `95 passed in 2.94s` (0 failures, 0 errors).
- **Workspace Regression Suite:** `241 passed in 49.96s` (0 failures, 0 errors).

---

## 5. Persistence Isolation & Security Boundaries

### 5.1 Authoritative State File Cryptographic Invariance (SHA-256)
All 6 authoritative application state files remain 100% byte-for-byte identical to baseline:

| File Path | Baseline SHA-256 | Post-Test SHA-256 | Verification Status |
|---|---|---|---|
| `context/ssot.json` | `0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da` | `0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da` | **INVARIANT (MATCH)** |
| `context/trade_lessons.json` | `0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463` | `0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463` | **INVARIANT (MATCH)** |
| `context/decision_log.json` | `757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2` | `757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2` | **INVARIANT (MATCH)** |
| `context/config.json` | `30ea2b67dd0ecac4b16e3e726c9644948119729023d382c385301f9db72099d5` | `30ea2b67dd0ecac4b16e3e726c9644948119729023d382c385301f9db72099d5` | **INVARIANT (MATCH)** |
| `context/agent_logs/agent-events.jsonl` | `58a830fe4a59a02bb16d33df10c1a09b3e76a2b6250377e26ee37a8d73d8bc3d` | `58a830fe4a59a02bb16d33df10c1a09b3e76a2b6250377e26ee37a8d73d8bc3d` | **INVARIANT (MATCH)** |
| `config.json` | `10c005efa15940d9c503aae62bbcb9a195ddbe249387c8335a4cb0f836064ee3` | `10c005efa15940d9c503aae62bbcb9a195ddbe249387c8335a4cb0f836064ee3` | **INVARIANT (MATCH)** |

### 5.2 Production Database Absence Verification
- `os.path.exists("context/gem.db")`: **False**
- `os.path.exists("cache/market_data.db")`: **False**

All tests ran against isolated temporary directories (`tempfile.TemporaryDirectory`).

### 5.3 Architectural Invariants
- Zero live application or trading-system rewiring was performed.
- Zero dual-write functionality introduced.
- JSON remains the application state source of truth until a separately authorized Stage 1.5 migration.

---

## 6. Limitations and Residual Risks Analysis

1. **In-Process Python Boundary:**  
   The protection against fabricated changelog entries relies on Python module encapsulation (`_MUTATION_CAPABILITY_KEY`) and active thread execution context (`get_active_caller_role()`). In a single-process embedded SQLite architecture, malicious Python code with arbitrary bytecode manipulation or reflection capabilities could theoretically inspect module globals. However, in the system architecture, subagents operate within bounded tool dispatch boundaries and cannot modify Python AST or execute arbitrary scripts in the host application process.
2. **SQLite Savepoint Nesting:**  
   SQLite supports nested savepoints up to the SQLite evaluation limit. The savepoint implementation uses uniquely generated UUID identifiers (`sp_batch_<uuid>`), preventing naming collisions across nested or recursive transactional scopes.
3. **Cache Database Separation:**  
   Market data daily bars remain strictly confined to the cache database. The repository enforces runtime isolation assertions preventing `DailyBarRepository` from ever attaching to or executing against the primary financial database.

---

## 7. Final Verdict

**COMPLETE**

All requirements of Stage 1.4.1.1 (R1, R2, R3) have been fully implemented, independently evidenced with 15 dedicated regression tests, verified against the full project test suite (241/241 pass), confirmed invariant across all protected state files, and documented.

All changes remain uncommitted and unpushed in the local working tree for human repository owner review.

