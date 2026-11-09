# Stage 1.4.1.3.1 Engineering Completion Report: Capability Credential Encapsulation & Exact Financial After-State Verification

**Repository:** `nordicirish/gemini_cli_subagent_system`  
**Branch:** `main`  
**Predecessor Baseline Commit:** `ee4c85ff708b27a9a08dbab2ab09643726291032`  
**Stage Baseline (HEAD) Commit:** `a198c42a59a721df2667389a62241e17d9185a50`  
**Date:** 2026-11-09  
**Status:** COMPLETE  
**Governance Framework:** `.agents/AGENTS.md` (Engineering Governance Protocols 01–06)

---

## 1. Executive Summary & Verdict

### Final Verdict: **COMPLETE**

Stage 1.4.1.3.1 resolves both High-severity findings identified in the independent review of Stage 1.4.1.3:
1. **R1 (Capability Credential Encapsulation):** The module-level importable credential `_MUTATION_KEY = object()` was completely removed from the module namespace. Capability credential creation is now encapsulated within a private closure. Credential dispensing is strictly restricted via caller bytecode inspection to the six authorized financial mutation implementations (`CashRepository.set_cash_balance`, `delete_cash`, `PositionRepository.upsert_position`, `delete_position`, `WatchlistRepository.upsert_watchlist_item`, `remove_watchlist_item`), plus an isolated automated test seam exclusively active under `tests.`. Direct capability construction without the encapsulated credential fails closed.
2. **R2 (Exact Financial After-State Field Verification):** In `FinancialChangelogRepository.append_entry()`, after-state verification now strictly enforces exact key sets matching the target schema:
   - Cash: `account_id`, `currency`, `amount`
   - Position: `account_id`, `ticker`, `shares`, `wac_eur`, `wac_usd`
   - Watchlist: `ticker`, `status`, `notes`
   Missing keys, unexpected extra keys, and value mismatches are strictly rejected fail-closed. Nullable fields (`wac_usd`, `notes`) must be explicitly present even when their value is `None`. Mandatory fields no longer use `.get()` lookups.

All 138 repository tests, all 95 core database tests, and all 287 repository-wide tests pass with zero errors, zero failures, and zero regressions.

---

## 2. Baseline & Pre-Implementation Reproduction Evidence

### 2.1 Baseline State
- **Predecessor Baseline:** `ee4c85ff708b27a9a08dbab2ab09643726291032`
- **Active Working Commit (HEAD):** `a198c42a59a721df2667389a62241e17d9185a50`
- **Working Tree:** Clean prior to implementation.
- **Python Runtime:** Python 3.13.3, SQLite 3.49.1 on Windows 11.

### 2.2 Pre-Implementation Reproduction

Both defects were reproduced using focused executable demonstrations prior to modifying source code:

```
=== Pre-Implementation Execution Evidence ===
Defect 1 (R1) reproduced: _MUTATION_KEY successfully imported: <class 'object'>
Defect 1 (R1) execution confirmed: fabricated entry appended:
  FinancialChangelogEntry(id=1, correlation_id='DEFECT1_EXPLOIT', actor_role='System',
    operation_name='set_cash_balance', target_resource='portfolio_cash/PRIMARY/EUR',
    approval_digest=None, before_state=None,
    after_state={'account_id': 'PRIMARY', 'currency': 'EUR', 'amount': '500.00'},
    timestamp_utc='2026-11-09T12:00:00Z')

Defect 2 (R2 Position) reproduced: position accepted missing wac_usd! Entry:
  FinancialChangelogEntry(id=2, correlation_id='DEFECT2_POS', actor_role='System',
    operation_name='upsert_position', target_resource='portfolio_positions/PRIMARY/TEST',
    approval_digest=None, before_state=None,
    after_state={'account_id': 'PRIMARY', 'ticker': 'TEST', 'shares': '10.0000', 'wac_eur': '100.0000'},
    timestamp_utc='2026-11-09T12:00:00Z')

Defect 2 (R2 Watchlist) reproduced: watchlist accepted missing notes! Entry:
  FinancialChangelogEntry(id=3, correlation_id='DEFECT3_WL', actor_role='System',
    operation_name='upsert_watchlist_item', target_resource='watchlist/TEST_WL',
    approval_digest=None, before_state=None,
    after_state={'ticker': 'TEST_WL', 'status': 'ACTIVE'},
    timestamp_utc='2026-11-09T12:00:00Z')
```

- **Defect 1:** An ordinary caller imported `_MUTATION_KEY` from `python.database.repositories.financial` and passed it to `_FinancialMutationCapability`, successfully inserting a forged entry into `financial_changelog`.
- **Defect 2 (Position):** `upsert_position` changelog verification passed an `after_state` missing the `wac_usd` key because `.get("wac_usd")` returned `None`, matching the database `NULL` row.
- **Defect 2 (Watchlist):** `upsert_watchlist_item` changelog verification passed an `after_state` missing the `notes` key because `.get("notes")` returned `None`, matching the database `NULL` row.

---

## 3. Engineering Implementation Details

### 3.1 R1 — Encapsulate Capability Credentials

#### 3.1.1 Elimination of Module-Level Credential
The module-level variable `_MUTATION_KEY = object()` was completely removed from `python/database/repositories/financial.py`. Attempting to import `_MUTATION_KEY` raises `ImportError`. The module attribute does not exist.

#### 3.1.2 Private Closure & Caller Bytecode Verification
A private lexical closure `_create_credential_closure()` encapsulates `_private_token`. The token is never stored in module globals. Dispensing of this credential is strictly governed by `_acquire_mutation_credential()`:
- Inspects `sys._getframe(1)` to identify the immediate caller.
- Verifies that `caller_self` is an instance of `CashRepository`, `PositionRepository`, or `WatchlistRepository`.
- Verifies that `frame.f_code` matches the compiled bytecode object of the exact repository method (`CashRepository.set_cash_balance.__code__`, etc.), preventing subclassing, monkey-patching, or name-spoofing attacks.
- If called by `_test_seam_mint_capability`, verifies via `sys._getframe(2)` that the invocation originates from an automated test module (`tests.` or `test_`).
- Any unauthorized caller raises `PermissionError("Unauthorized attempt to acquire financial mutation credential...")`.

#### 3.1.3 Restriction to the Six Financial Mutations
Each of the six financial mutation methods:
1. `CashRepository.set_cash_balance()`
2. `CashRepository.delete_cash()`
3. `PositionRepository.upsert_position()`
4. `PositionRepository.delete_position()`
5. `WatchlistRepository.upsert_watchlist_item()`
6. `WatchlistRepository.remove_watchlist_item()`
constructs its capability using `credential=_acquire_mutation_credential()` and locally validated values within the active transaction, saving `self._last_mutation_capability = cap` for verification.

#### 3.1.4 Clean Test Seam
An isolated helper `_test_seam_mint_capability()` provides a narrowly scoped seam strictly for test suites to construct capabilities for fail-closed validation tests. Calls originating outside test modules fail closed with `PermissionError`.

### 3.2 R2 — Exact Financial After-State Field Verification

In `python/database/repositories/audit.py`, `FinancialChangelogRepository.append_entry()` was hardened to enforce exact field sets:

| Target Resource | Required Exact Fields | Nullable Fields (Must Be Explicitly Present) |
|---|---|---|
| **Cash** (`portfolio_cash/<account>/<curr>`) | `account_id`, `currency`, `amount` | None |
| **Position** (`portfolio_positions/<account>/<ticker>`) | `account_id`, `ticker`, `shares`, `wac_eur`, `wac_usd` | `wac_usd` (may be `None`, key must exist) |
| **Watchlist** (`watchlist/<ticker>`) | `ticker`, `status`, `notes` | `notes` (may be `None`, key must exist) |

For each target:
1. `set(after.keys()) != REQUIRED_KEYS` rejects both missing and unexpected extra keys with `IntegrityConflictError`.
2. Every field is compared directly (`row[4] != after["wac_usd"]`, `row[2] != after["notes"]`). No `.get()` is permitted.
3. Deletions verify that `after_state` is `None` and confirm via database query that the specific row is absent.

---

## 4. Test Evidence & Regression Verification

### 4.1 Focused R1 & R2 Test Suite
A dedicated test suite `TestStage14131CapabilityCredentialAndExactStateVerification` (21 tests) was added to `tests/test_database_repositories.py`:

| Test Name | Domain | Verification Description | Result |
|---|---|---|---|
| `test_r1_mutation_key_not_importable` | R1 | Proves `_MUTATION_KEY` cannot be imported and does not exist in module/pkg. | **PASS** |
| `test_r1_record_changelog_absent` | R1 | Proves `_record_changelog` is absent. | **PASS** |
| `test_r1_no_general_purpose_factory_exposed` | R1 | Confirms `__all__` contains only repos; no capability factories exposed. | **PASS** |
| `test_r1_direct_capability_construction_without_credential_fails` | R1 | Direct instantiation with None, fake object, or string raises `PermissionError`. | **PASS** |
| `test_r1_unauthorized_attempt_to_acquire_credential_fails` | R1 | External caller calling `_acquire_mutation_credential()` raises `PermissionError`. | **PASS** |
| `test_r1_merely_having_active_transaction_insufficient_to_mint` | R1 | Direct capability construction inside active transaction raises `PermissionError`. | **PASS** |
| `test_r1_all_six_genuine_mutations_generate_exactly_one_changelog` | R1 | All 6 financial mutations mint valid capability and record 1 changelog entry. | **PASS** |
| `test_r1_capability_cannot_be_replayed_via_genuine_mutation` | R1 | Re-appending consumed `_last_mutation_capability` raises `IntegrityConflictError`. | **PASS** |
| `test_r1_capability_cannot_be_used_on_different_connection` | R1 | Passing capability to repository on connection 2 raises `PermissionError`. | **PASS** |
| `test_r1_changelog_failure_rolls_back_associated_mutation` | R1 | Mutation rolls back cleanly when changelog insert fails. | **PASS** |
| `test_r2_position_missing_wac_usd_rejected` | R2 | Position after-state missing `wac_usd` key is rejected even when DB has `NULL`. | **PASS** |
| `test_r2_position_present_wac_usd_none_accepted` | R2 | Position after-state with explicit `wac_usd=None` is accepted when DB has `NULL`. | **PASS** |
| `test_r2_position_mismatched_wac_usd_rejected` | R2 | Position after-state with mismatched `wac_usd` is rejected fail-closed. | **PASS** |
| `test_r2_watchlist_missing_notes_rejected` | R2 | Watchlist after-state missing `notes` key is rejected even when DB has `NULL`. | **PASS** |
| `test_r2_watchlist_present_notes_none_accepted` | R2 | Watchlist after-state with explicit `notes=None` is accepted when DB has `NULL`. | **PASS** |
| `test_r2_watchlist_mismatched_notes_rejected` | R2 | Watchlist after-state with mismatched `notes` is rejected fail-closed. | **PASS** |
| `test_r2_unexpected_additional_fields_rejected_cash` | R2 | Extra fields in cash after-state raise `IntegrityConflictError`. | **PASS** |
| `test_r2_unexpected_additional_fields_rejected_position` | R2 | Extra fields in position after-state raise `IntegrityConflictError`. | **PASS** |
| `test_r2_unexpected_additional_fields_rejected_watchlist` | R2 | Extra fields in watchlist after-state raise `IntegrityConflictError`. | **PASS** |
| `test_r2_missing_mandatory_fields_rejected_across_all_targets` | R2 | Missing mandatory fields across cash, position, and watchlist are rejected. | **PASS** |
| `test_r2_exact_deletion_verification` | R2 | Delete changelog entry fails closed if entity row was not actually deleted. | **PASS** |

### 4.2 Automated Test Suites Execution
```
----------------------------------------------------------------------
tests/test_database_repositories.py: 138 tests passed in 10.48s (OK)
tests/test_database_core.py:          95 tests passed in  3.21s (OK)
Full test suite discovery:           287 tests passed in 46.75s (OK)
----------------------------------------------------------------------
Total Test Failures: 0
Total Regressions:   0
```

### 4.3 Static Code Analysis & Environment
- **Bytecode Compilation:** `python -m compileall -q python tests` exited with code 0 (clean).
- **Dependency Integrity:** `pip check` reports `No broken requirements found.` (clean).

### 4.4 Authoritative State File SHA-256 Invariance
All six application state files were verified against their baseline SHA-256 hashes:

| Authoritative File Path | Expected SHA-256 Hash | Actual SHA-256 Hash | Status |
|---|---|---|---|
| `context/ssot.json` | `0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da` | `0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da` | **INVARIANT** |
| `context/trade_lessons.json` | `0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463` | `0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463` | **INVARIANT** |
| `context/decision_log.json` | `757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2` | `757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2` | **INVARIANT** |
| `context/config.json` | `30ea2b67dd0ecac4b16e3e726c9644948119729023d382c385301f9db72099d5` | `30ea2b67dd0ecac4b16e3e726c9644948119729023d382c385301f9db72099d5` | **INVARIANT** |
| `context/agent_logs/agent-events.jsonl` | `58a830fe4a59a02bb16d33df10c1a09b3e76a2b6250377e26ee37a8d73d8bc3d` | `58a830fe4a59a02bb16d33df10c1a09b3e76a2b6250377e26ee37a8d73d8bc3d` | **INVARIANT** |
| `config.json` | `10c005efa15940d9c503aae62bbcb9a195ddbe249387c8335a4cb0f836064ee3` | `10c005efa15940d9c503aae62bbcb9a195ddbe249387c8335a4cb0f836064ee3` | **INVARIANT** |

### 4.5 Production Database Absence
- `context/gem.db` exists: **False**
- `cache/market_data.db` exists: **False**

---

## 5. Governance & Protocol Compliance

- **Protocol 01 (Non-Bypassable Financial Boundaries):** Capability credential creation strictly encapsulated within a private lexical closure. No module-level key is accessible.
- **Protocol 02 (Subprocess & Execution Isolation):** Only authorized read-only git queries executed; no subprocess escapes.
- **Protocol 03 (Zero Live System Rewiring):** No production migration; JSON files remain the application SSoT. Stage 1.5 has not been initiated.
- **Protocol 04 (Fail-Closed Architecture):** Incomplete after-states, unexpected extra fields, and missing keys fail closed immediately.
- **Protocol 05 (Git Commit Authority):** Zero autonomous git commits or pushes. All modifications remain in the working tree for human repository owner review and commit.

---

## 6. Modified Files Manifest

1. `python/database/repositories/financial.py` — Removed `_MUTATION_KEY = object()`. Introduced `_create_credential_closure()` with caller bytecode verification. Bound capability instantiation to the six actual financial mutation methods. Added `_test_seam_mint_capability()`. Updated `after_state` dictionaries to exact schema fields.
2. `python/database/repositories/audit.py` — Enforced exact field sets on `after_state` in `FinancialChangelogRepository.append_entry()`, rejecting missing and extra keys. Eliminated `.get()` lookups for mandatory/nullable fields. Checked `mutation_capability.is_consumed` before active transaction checks.
3. `tests/test_database_repositories.py` — Eliminated all imports of `_MUTATION_KEY`. Updated replay and connection-binding tests to use genuine mutation capabilities via `_last_mutation_capability`. Added `TestStage14131CapabilityCredentialAndExactStateVerification` (21 tests). Total tests increased to 138.
4. `docs/engineering/STAGE_1_4_1_3_1_CAPABILITY_CREDENTIAL_AND_EXACT_STATE_REPORT.md` — Engineering completion report (this document).

