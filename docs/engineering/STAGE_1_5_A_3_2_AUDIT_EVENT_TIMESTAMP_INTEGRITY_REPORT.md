# Stage 1.5-A.3.2 — Audit Event Timestamp Integrity Report

**Repository:** `nordicirish/gemini_cli_subagent_system`  
**Branch:** `main`  
**Baseline HEAD (verified):** `02bdea0f0bfce3f3d12181f6254897a8e457a957` — equals actual HEAD; working tree clean at start; HEAD unchanged at end  
**Commit/Push authority:** Human Repository Owner only. Nothing was committed or pushed.

> [!IMPORTANT]
> This stage fixes one defect (unvalidated `AuditEventRepository.append_event(timestamp_utc=…)`). It **does not establish overall Stage 1.5-A completion.** That requires the separate evidence-based closeout audit (Stage 1.5-A.4), which has not been authorized or started.

---

## 1. Baseline & Inspection

| Check | Result |
|---|---|
| Branch | `main` |
| `git rev-parse HEAD` | `02bdea0f0bfce3f3d12181f6254897a8e457a957` (matches expected) |
| Baseline ancestry | `git merge-base --is-ancestor` exit 0; HEAD *is* the baseline |
| Working tree at start | Clean |
| Callers of `append_event()` | **Only the definition** (`audit.py:53`) plus 11 test call sites. **No production caller exists in `python/`.** |
| Timestamps used by those 11 test callers | All valid `…Z` values (`2026-11-08T22:00:00Z`, `2026-11-09T12:00:00Z`). **None depend on the permissive behaviour.** |
| Schema (`schema.py:211-225`) | `timestamp_utc TEXT NOT NULL` — **no CHECK constraint**; no triggers found for `audit_events`. Only index `idx_audit_timestamp`. |
| `python/database/models.py` | **Does not exist** (the directive listed it). The relevant types live in `repositories/base.py`. |

The schema offers no defence in depth: before this stage, any string (or, via SQLite type affinity, other bindable values) could be stored.

---

## 2. Implementation

**File:** `python/database/repositories/audit.py` — method `AuditEventRepository.append_event()`. 4 small edits, +10/−2 lines. Verified by `git diff`:

1. **Import** `validate_timestamp_utc` from `python.database.repositories.base` (the existing centralized validator; **no second validator created**).
2. **Validate first**, immediately after `self._check_active()` and before every other statement:
   ```python
   self._check_active()
   # Validate before transaction entry, database reads or writes (no state change on failure).
   valid_timestamp = validate_timestamp_utc(timestamp_utc)
   norm_id = validate_event_id(event_id)
   ```
3. **INSERT** parameter `timestamp_utc` → `valid_timestamp`.
4. **Returned `AuditEventRecord`** `timestamp_utc=timestamp_utc` → `valid_timestamp`.
5. Docstring extended to state the contract.

Because `validate_timestamp_utc()` returns the original string unchanged (Stage 1.5-A.3.1), the persisted value is **byte-for-byte as supplied**: no strip, no `Z`→`+00:00`, no precision rewrite, no local-time conversion.

### Validation ordering

```
_check_active()  →  validate_timestamp_utc()  →  validate_event_id/identifiers  →  json.dumps
        →  [repository_transaction entered here: lock, SELECT, INSERT]
```

Invalid timestamps therefore fail **before** transaction entry, lock acquisition, the existence query and the insert. This is proven by test, not just inspection (Section 4).

### Exception type
`ValidationError` (existing; raised by `validate_timestamp_utc`). No new exception type.

### Not changed
Schema, triggers, event IDs, payload hashing, idempotency comparison, actor attribution, transaction ownership/SAVEPOINT behaviour, financial repositories, backup/restore, trading rules/data, `validate_timestamp_utc()` itself.

---

## 3. Contract Applied

**Accepted (returned unchanged):** `…Z`, `…+00:00`, and fractional-second variants of either.  
**Rejected with `ValidationError`:** `-00:00`, `+01:00`, `-05:00`, `+0000`, naive, leading/trailing whitespace, trailing newline, invalid calendar date, empty string, and non-string values (int, bool, bytes, list, dict).  
**`None`:** per the centralized contract it **generates a current `Z` timestamp** — see Behaviour Changes.

---

## 4. Regression Tests (exact names)

Added to the existing audit-event test class `TestStage15ARepositoryTransactionAuthorizationRevisionHardening` in `tests/test_database_repositories.py`, immediately after `test_r4_audit_event_concurrent_connections_idempotent`, plus one test-local helper `_r41_append`. **9 new tests**:

| # | Test function | Covers |
|---|---|---|
| 1 | `test_r4_1_audit_event_accepts_valid_utc_timestamps_and_preserves_bytes` | 4 valid forms; returned value byte-identical; fresh-connection `get_event()` identical; raw `SELECT timestamp_utc` identical with `typeof = 'text'` |
| 2 | `test_r4_1_audit_event_rejects_invalid_timestamps_without_mutation` | All 15 required invalid cases via `subTest`; per case: `ValidationError`, count unchanged, `get_event(id) is None`, `conn.in_transaction` is False, then a **valid insert of the same event_id succeeds** (transaction usable, no partial row) |
| 3 | `test_r4_1_invalid_timestamp_is_rejected_before_transaction_entry` | Patches `audit.repository_transaction` to raise if entered; `assert_not_called()` for invalid input |
| 4 | `test_r4_1_invalid_timestamp_inside_uow_does_not_alter_outer_transaction` | Nested in `UnitOfWork`: not poisoned, not conn-unsafe, still active and in-transaction, `repository_transaction` not entered, surrounding valid writes commit atomically (2 rows; bad id absent) |
| 5 | `test_r4_1_identical_replay_remains_idempotent_and_returns_original_timestamp` | Idempotency regression |
| 6 | `test_r4_1_replay_with_different_valid_timestamp_does_not_overwrite_original` | Characterizes existing semantics: differing valid timestamp → original returned, stored value never overwritten |
| 7 | `test_r4_1_invalid_timestamp_on_replay_raises_validation_error_not_silent_success` | Documents behaviour change (below) |
| 8 | `test_r4_1_conflicting_payload_still_raises_integrity_conflict_with_valid_timestamps` | Conflicting payload still `IntegrityConflictError` (not `ValidationError`) with same and different timestamp; original preserved |
| 9 | `test_r4_1_none_timestamp_follows_centralized_contract_and_generates_utc_now` | Pins `None` behaviour |

**Existing tests:** none modified, deleted or weakened. The 3 existing R4 audit tests plus `test_audit_event_crud_and_mapping`, `test_audit_event_idempotent_replay` and `test_audit_event_conflicting_replay_raises_integrity_conflict` all still pass unchanged.

### Mutation check (do the new tests actually detect the defect?)
I temporarily restored the **original** `audit.py` (via read-only `git show HEAD:…`, with a hash-verified backup; no Git state changed) and re-ran the class:

- **Result against original code: 22 failures/errors across 5 tests** (`…rejects_invalid_timestamps_without_mutation`, `…rejected_before_transaction_entry`, `…inside_uow…`, `…invalid_timestamp_on_replay…`, `…none_timestamp…`).
- The 4 other new tests (valid preservation, identical replay, differing-valid-timestamp, conflicting payload) pass on **both** old and new code — by design, as they guard behaviour that must *not* change.
- Fixed file restored; SHA-256 before and after identical (`06B8454A…FFC4D0`).

Notably, against the original code the `list` and `dict` cases surfaced as test **ERRORs** (an exception other than `ValidationError`; the exact exception type was not captured), while `int`, `bool` and `bytes` were silently accepted (test **FAILs**). So even "rejection" was inconsistent before this stage.

---

## 5. Behaviour Changes (deliberate, for owner awareness)

| # | Change | Rationale / risk |
|---|---|---|
| B-1 | Invalid timestamps now raise `ValidationError` instead of being stored (or, for list/dict, raising a driver error). | The intended fix. |
| B-2 | A **replay of an existing event carrying an invalid timestamp** now raises `ValidationError`; previously it returned the existing record (idempotent success). | Validation precedes the existence lookup per directive §3.2. The stored event is unchanged (test 7). No production caller exists, so no runtime impact today. |
| B-3 | `timestamp_utc=None` now **generates a current `Z` timestamp**; previously it hit the `NOT NULL` constraint and raised `IntegrityConflictError`. | Direct consequence of reusing the centralized function as-is, which the directive endorsed. The annotation is still `timestamp_utc: str`. Pinned by test 9. **Owner may prefer rejecting `None` for audit events** (an audit timestamp arguably should be supplied by the producer) — that would be a one-line follow-up, deliberately not made here. |

---

## 6. Idempotency & Transaction Verification

- **Replay identity unchanged:** still `payload_hash` + parsed `raw_event` equality. The timestamp is **not** part of identity. So a replay with a different *valid* timestamp is treated as an identical replay and returns the original (test 6) — pre-existing behaviour, left as-is. Related pre-existing observation: `action_category`, `status`, `agent_role`, `permission_decision` and `approval_status` are likewise not compared on replay. Not changed; noted under limitations.
- **Conflicts unchanged:** differing payload/hash → `IntegrityConflictError` (test 8).
- **Transactions:** no transaction/savepoint is entered for invalid input; standalone state is left clean; a caller-owned `UnitOfWork` is neither poisoned nor altered and can still commit (tests 2–4).

---

## 7. Raw-Event Timestamp Investigation

| Question | Finding (source-verified) |
|---|---|
| Does `raw_event` contain its own timestamp? | **Not by the repository's definition.** `raw_event` is a free-form `Dict[str, Any]` serialized with `json.dumps(sort_keys=True)`. `append_event` neither requires nor inspects any key. All test payloads omit one. |
| Is there an upstream producer with its own timestamp field? | **Yes.** `python/audit_logger.py:62` `AuditEvent.timestamp_utc` defaults to `datetime.now(timezone.utc).isoformat()`, and `to_jsonl_line()` serializes it into the JSONL line (so it *would* appear inside a payload built from `asdict(event)`). It is a dataclass default (effectively always present) but is **not validated** anywhere. |
| Is the repository timestamp intended to equal the payload timestamp? | **Undefined.** No code links them. No `AuditEvent` → `audit_events` bridge exists in `python/` (only `tool_dispatcher.py` writes JSONL; nothing else references `AuditEventRepository`). |
| Are the formats compatible? | **Yes.** I verified that `isoformat()` output validates and is returned unchanged both with microseconds (`2026-11-09T22:16:28.253251+00:00`) and without (`2026-11-09T22:16:28+00:00`). A future bridge passing `event.timestamp_utc` through will be accepted. |
| Existing callers with different semantics? | None exist. |

**No equality requirement was imposed; no schema or payload change was made.**

**Follow-up finding F-1 (documented, not implemented):** if a future JSONL→SQLite bridge is built, nothing prevents the column `timestamp_utc` and the payload's embedded `timestamp_utc` from diverging, and the payload timestamp is never validated. Whether to enforce equality or derive one from the other is a design decision for the owner at bridge-design time (Stage 1.5-D/1.6 territory).

---

## 8. Exact Test Execution (independently executed this stage)

Environment: `.venv\Scripts\python.exe`, Python 3.13.3, Windows. All run **after** the final code state. Exit codes captured from the process, not from a pipeline.

| Command | Tests | Result | Skips | Reported run time | Exit |
|---|---|---|---|---|---|
| `python -m unittest tests.test_database_repositories` | **224** (was 215; +9) | OK | 0 | 19.871s | 0 |
| `python -m unittest tests.test_database_core` | 115 | OK | 0 | 6.104s | 0 |
| `python -m unittest tests.test_database_backup_concurrency` | 74 | OK | **1** | 7.909s | 0 |
| `python -m unittest discover -s tests -p "test_*.py"` | **467** (was 458; +9) | `OK (skipped=1)` | **1** | 61.937s | 0 |
| Audit class `TestStage15ARepositoryTransactionAuthorizationRevisionHardening` | 37 | OK | 0 | 3.027s | — |
| `python -m pip check` | — | "No broken requirements found" | — | — | 0 |
| `py_compile` on `audit.py`, `test_database_repositories.py` | — | clean | — | — | 0 |

Failures: **0**. Errors: **0** (in the final state). The single skip is `test_r5_04_posix_unlock_failure_injection` (`@unittest.skipUnless(sys.platform != "win32")`), confirmed by `-v` output.

| Category | Items |
|---|---|
| **Independently executed** | Everything in the table above, plus the mutation-check run against the original `audit.py` (Section 4) |
| **Inspected only, not executed** | POSIX-only code paths (1 skip); `scripts/verify_stage_1.py` |
| **Not executed** | `scripts/verify_stage_1.py` (invokes external `agy.exe`; Gate 6 failed in the earlier session, so Gates 7–8 had never run) |
| **Previously reported (not re-derived here)** | Stage 1.5-A.3.1 figures (458 total). The Stage 1.5-A.3 findings matrix was **not** relied on. |

---

## 9. Authoritative File Hashes (SHA-256, verified after the full test run)

Identical to the Stage 1.5-A.3 / 1.5-A.3.1 baseline.

| File | SHA-256 |
|---|---|
| `context/ssot.json` | `0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da` |
| `context/trade_lessons.json` | `0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463` |
| `context/decision_log.json` | `757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2` |
| `context/config.json` | `30ea2b67dd0ecac4b16e3e726c9644948119729023d382c385301f9db72099d5` |
| `context/user_config.json` | `88e00f3ef6609eba02066cdfb3750152bfa2b97667aceed5cfba30b5db607183` |
| `context/agent_logs/agent-events.jsonl` | `58a830fe4a59a02bb16d33df10c1a09b3e76a2b6250377e26ee37a8d73d8bc3d` |
| `config.json` | `10c005efa15940d9c503aae62bbcb9a195ddbe249387c8335a4cb0f836064ee3` |
| `gem_trading_rules/rules.md` | `04ea36c0762de30c841bb25ac35bdffeb6845cccfba19a9b94d343743c075056` |

Caveat: the baseline is the set of hashes established in the earlier stages and carried forward; it is not an independent pre-history baseline.

## 10. Production Database Absence

`context/gem.db` → **absent** (`False`). `cache/market_data.db` → **absent** (`False`). No production database was created or initialised.

---

## 11. File-Change Inventory

`git status --short` / `git diff --stat` at end (HEAD still `02bdea0`):

| File | Status | Change |
|---|---|---|
| `python/database/repositories/audit.py` | Modified | +10 / −2 |
| `tests/test_database_repositories.py` | Modified | +213 / 0 |
| `docs/engineering/STAGE_1_5_A_3_2_AUDIT_EVENT_TIMESTAMP_INTEGRITY_REPORT.md` | New (untracked) | This report |

No schema, trading rule, context file, backup/manager or other file was touched (a `git diff --name-only` over those paths is empty). Scratch artifacts live outside the repo under the conversation `scratch/` directory. Git prints an LF→CRLF notice for modified files; this is the existing line-ending setting, not a content change.

---

## 12. Remaining Limitations

- **L-1 (B-3):** `None` is now accepted and generates a timestamp for audit events. Owner decision whether to reject `None` here.
- **L-2:** Replay identity ignores timestamp and non-payload columns (pre-existing; deliberately unchanged per directive).
- **L-3 (F-1):** Payload-embedded timestamp is unvalidated and has no relationship to the column timestamp; relevant only once a JSONL→SQLite bridge exists.
- **L-4:** `audit_events.timestamp_utc` has **no schema-level CHECK**. Validation is enforced only in the repository layer; direct SQL writes can still store arbitrary text. Adding a CHECK would be a schema change (out of scope here).
- **L-5:** POSIX paths unexecuted (1 skip); `verify_stage_1.py` not run.
- **L-6:** Other repository `timestamp_utc`-accepting APIs were not re-audited in this stage beyond the six covered in 1.5-A.3.1. No further validation was added elsewhere, per directive.

## 13. Scope Statement

This stage does **not** establish overall Stage 1.5-A completion, and the inaccurate Stage 1.5-A.3 findings matrix remains uncorrected. Stage 1.5-A.4, 1.5-B, 1.5-C, 1.5-D, 1.5-E and 1.6 were not begun.

---

## 14. Completion Verdict

The targeted defect is fixed, proven by tests that fail against the original code and pass against the fix, with no regression (467/467 pass, 1 platform skip) and all invariants intact. Because three deliberate behaviour changes (B-1…B-3, notably `None`) and the L-4 schema-level gap warrant an owner decision, the verdict is:

```
================================================================================
STAGE 1.5-A.3.2 VERDICT: COMPLETE WITH LIMITATIONS — HUMAN REVIEW REQUIRED
================================================================================
```

Execution stopped. Awaiting human review and sign-off.
