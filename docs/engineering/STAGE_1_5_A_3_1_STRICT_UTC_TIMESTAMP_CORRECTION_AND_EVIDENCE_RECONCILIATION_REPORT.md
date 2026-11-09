# Stage 1.5-A.3.1 — Strict UTC Timestamp Validation Correction & Closeout Evidence Reconciliation Report

**Repository:** `nordicirish/gemini_cli_subagent_system`  
**Branch:** `main`  
**Current HEAD at start of stage:** `f5710c50f57c3eba43320ff23bd94302e1fc9196`  
**Implementation baseline:** `a37a1d01c35fad9354689509a20ffc6bb650a0ac`  
**Corrects:** `docs/engineering/STAGE_1_5_A_3_FINAL_REPOSITORY_TRANSACTION_AND_AUTHORIZATION_CLOSEOUT_ASSESSMENT.md`  
**Commit/Push authority:** Human Repository Owner only. Nothing in this stage was committed.

> [!IMPORTANT]
> The Stage 1.5-A.3 assessment was **not fully correct**. Beyond the timestamp finding, this stage found that its test-evidence column and several of its source claims do not match the repository (Section 5). Its closeout verdict is therefore **not adequately supported by that report** (Section 9).

---

## 1. Verified Git State

| Item | Value | How verified |
|---|---|---|
| Implementation baseline / source HEAD assessed | `a37a1d01c35fad9354689509a20ffc6bb650a0ac` | `git log` |
| Assessment document commit | `f5710c50f57c3eba43320ff23bd94302e1fc9196` (`docs(engineering): add Stage 1.5-A.3 …`) | `git show --stat` — adds exactly 1 file, 491 lines |
| Ancestry | `a37a1d0` **is an ancestor of** `f5710c5` (`git merge-base --is-ancestor` exit 0); `f5710c5` is a direct child | `git log --oneline` |
| Working tree at start | Clean | `git status --short` empty |

The original report stated `a37a1d01…` as its "Actual Current HEAD". That was true while it was being written (uncommitted), but the document itself landed in `f5710c5`. The distinction is now: **source assessed = `a37a1d0`; document = `f5710c5`**. No source files changed between the two commits.

---

## 2. Finding TS-CORR-01 — Confirmed

**Location (corrected):** `python/database/repositories/base.py`, `validate_timestamp_utc()`. The original report said `models.py`; that was wrong.

### 2.1 Defects in the original implementation (reproduced)

Reproduced with the original logic in `scratch/old_ts_behaviour.py`:

| Input | Original behaviour |
|---|---|
| `2026-11-09T12:30:00-00:00` | **Accepted**, returned unchanged |
| ` 2026-11-09T12:30:00Z` | **Accepted**, returned **stripped** |
| `2026-11-09T12:30:00Z ` | **Accepted**, returned **stripped** |
| `2026-11-09T12:30:00Z\n` | **Accepted**, returned **stripped** |
| `2026-11-09T12:30:00+0000` | **Accepted**, returned unchanged *(not in the original finding)* |

Root causes in `base.py`:
1. `s = ts.strip()` then `return s` — silent normalization of a supplied historical timestamp.
2. Offset check only tested `total_seconds() == 0`, so `-00:00` passed.
3. Regex `[+-]\d{2}:?\d{2}` admitted `-00:00` **and the colon-less `+0000`**.
4. `^…$` with `.match()`: `$` matches before a trailing `\n`, a second whitespace path independent of `strip()`.
5. `\d` matches non-ASCII Unicode digits.
6. The docstring and test `test_r3_strict_utc_accepted_formats` explicitly **codified** `-00:00` acceptance, as did the test-class docstring.

### 2.2 Other timestamp paths (investigated, per mandatory step 5)

| Path | Result |
|---|---|
| 6 mutation methods that accept `timestamp_utc` (`set_cash_balance`, `upsert_position`, `upsert_watchlist_item`, `ScoutCategoryRepository.upsert_category`, `upsert_lesson`, `append_decision`) | All call `validate_timestamp_utc` and persist/return the validated `valid_ts`. Fixing the single function fixes all six. |
| Delete paths (`financial.py` ~354/626/834) | Use `utc_now_iso()`; no supplied timestamp. No issue. |
| `increment_db_revision(conn, ts)` | Receives `valid_ts` from validated paths, or `utc_now_iso()`. No normalization. |
| `cache.py` | Always `utc_now_iso()`. No supplied timestamp. |
| **`AuditEventRepository.append_event(timestamp_utc=…)`** (`audit.py:56,132`) | **Does not validate and does not normalize.** Value is stored verbatim. See Limitation L-1. |

No other path silently normalizes a supplied timestamp.

### 2.3 Correction applied

In `base.py` (see inventory, Section 7):
- Regex tightened to ASCII digits and only `Z` / `\+00:00`, applied with `fullmatch()`.
- `strip()` removed; any leading/trailing whitespace is rejected via `ts != ts.strip()`.
- Explicit `endswith("Z") or endswith("+00:00")` pre-check with clear message.
- `Z → +00:00` rewrite is used **only** to feed `fromisoformat()`; the **original string** is returned.
- Docstring corrected (no longer mentions `-00:00`).

This follows the suggested patch, with two deliberate hardening differences: `[0-9]` instead of `\d`, and `fullmatch` (the suggested patch's `fullmatch` was kept; the `+0000` and Unicode-digit gaps were closed by the regex change).

---

## 3. Regression Tests

### 3.1 Placement deviation (explained)

The directive asked for tests in "the existing timestamp validation test class in `tests/test_database_core.py`". **No such class exists there** — `test_database_core.py` does not import or reference `validate_timestamp_utc`. The existing timestamp tests live in `tests/test_database_repositories.py`, class `TestStage15A1NestedTransactionAtomicityAndTimestampSafety`. The new tests were added **there**, alongside the tests they supersede, rather than duplicating infrastructure into `test_database_core.py`.

### 3.2 Existing tests changed (necessarily)

- `test_r3_strict_utc_accepted_formats`: removed the assertion that `-00:00` is accepted (it asserted the defect).
- `test_r3_strict_utc_rejected_formats`: added `-00:00` to the rejected list.
- Class docstring updated.

No other existing test was altered. These changes tighten, not weaken, coverage.

### 3.3 Tests added (9)

| Test | Directive item(s) |
|---|---|
| `test_r3_1_validate_timestamp_utc_rejects_negative_zero_offset` | 4 |
| `test_r3_1_validate_timestamp_utc_rejects_nonzero_offsets` (+01:00, +00:30, -05:00, `+0000`, `+00`) | 5, 6 |
| `test_r3_1_validate_timestamp_utc_rejects_surrounding_whitespace` (leading, trailing space/`\n`/`\t`) | 7, 8, 9 |
| `test_r3_1_validate_timestamp_utc_rejects_naive_and_malformed` (naive, Feb 30, month 13, hour 24, truncated, Unicode digits, empty) | 10, 11, 12 |
| `test_r3_1_validate_timestamp_utc_rejects_non_string_values` (int, float, bool, bytes, list, dict, object) | 13 |
| `test_r3_1_validate_timestamp_utc_preserves_original_string` (Z, +00:00, fractional; byte-level compare) | 1, 2, 3 |
| `test_r3_1_validate_timestamp_utc_none_generates_valid_utc_timestamp` | 14 |
| `test_r3_1_rejected_timestamp_leaves_no_partial_mutation` (no row, no `db_revision` bump) | extra |
| `test_r3_1_historical_timestamps_round_trip_byte_for_byte_after_persistence` — all 6 mutation methods, **re-read through a fresh connection**, fractional seconds, both designators | 15 |

Item 15 is stronger than the pre-existing `test_r3_all_six_mutations_preserve_valid_historical_timestamps`, which only compared the *returned record* and never re-read from the database.

> [!NOTE]
> A test-authoring error (`DecisionRecord.decision_id` does not exist; the field is `id`) was caught on the first run and fixed in the test. It was not a validator defect.

---

## 4. Verification Results (independently executed in this stage)

Environment: `.venv\Scripts\python.exe`, Python 3.13.3, Windows. Run after the changes.

| Command | Tests | Result | Skips | Time |
|---|---|---|---|---|
| `python -m unittest tests.test_database_core` | 115 | OK, exit 0 | 0 | 5.929s |
| `python -m unittest tests.test_database_repositories` | **215** (was 206; +9) | OK, exit 0 | 0 | 17.301s |
| `python -m unittest tests.test_database_backup_concurrency` | 74 | OK, exit 0 | **1** | 8.860s |
| `python -m unittest discover -s tests -p "test_*.py"` | **458** (was 449; +9) | `OK (skipped=1)`, exit 0 | **1** | 60.925s |
| Focused: `TestStage15A1NestedTransactionAtomicityAndTimestampSafety` | 24 | OK | 0 | 1.195s |
| `py_compile` on `base.py`, `test_database_repositories.py` | — | exit 0 | — | — |
| `pip check` | — | "No broken requirements found", exit 0 | — | — |

- **The one skip** is `test_r5_04_posix_unlock_failure_injection` (`tests/test_database_backup_concurrency.py:1522`, `@unittest.skipUnless(sys.platform != "win32")`). The original report misnamed it `test_posix_flock_semantics`, which does not exist.
- **Not independently re-executed this stage:** `scripts/verify_stage_1.py` (see Section 6). No POSIX run was possible; POSIX-only behaviour is **unexecuted** on this machine.
- The mutation-detection check: the new tests exercise exactly the inputs shown in Section 2.1, which the original implementation demonstrably accepted. The original code was **not** re-run against the new test file (no `git stash`/checkout was used, per Git restrictions); the evidence is the standalone reproduction in `scratch/old_ts_behaviour.py` plus the changed assertions.

---

## 5. Closeout Report Reconciliation (Section 5.B/C of directive)

### 5.1 Timestamp claims (B)

| Original claim | Actual |
|---|---|
| `validate_timestamp_utc` in `models.py` | In `repositories/base.py` |
| "accepts only 'Z' or '+00:00'" | **False** at `a37a1d0` (accepted `-00:00`, `+0000`, whitespace-padded input). **True after this stage.** |
| "historical timestamps preserved byte-for-byte" (TS-02) | **Not established** at `a37a1d0` (value was stripped). Established only now, by the new round-trip test. |

### 5.2 Test evidence (C) — material discrepancy

An automated check of every `test_*` identifier cited in the original report against `def` names in `tests/*.py` found **29 of 30 cited test-function names do not exist** in the repository (the other 4 identifiers are module names). Examples: `test_audit_event_concurrency_lock`, `test_nested_savepoint_release`, `test_clear_for_test_fail_closed`, `test_validate_timestamp_utc`, `test_posix_flock_semantics`.

Consequence: the **"Test evidence" column of the original findings matrix is unreliable and must not be treated as evidence.** Real test classes exist covering many of these areas (e.g. `TestR2SavepointRollbackFailureIntegrity`, `TestStage15A2TransactionPoisoningAndRecoveryFailureEnforcement`, `TestStage15ARepositoryTransactionAuthorizationRevisionHardening`), but the matrix does not map to them.

### 5.3 Source claims also found inaccurate (observed incidentally; not an exhaustive re-audit)

- `repository_transaction` was presented as a `@contextmanager` generator with quoted code (including a silent `except Exception: pass`). In the source it is a **class** (`base.py:499`). The quoted snippet was illustrative, not verbatim, and should not be read as source.
- `DecisionLogRepository.append_entry()` is actually `append_decision()`.
- References to `models.py` / `exceptions.py` as holding specific symbols were not verified.

I did **not** re-audit the remaining source claims in this stage; the directive scoped this stage to the timestamp finding plus reconciliation. They should be treated as **unverified**, not as false.

### 5.4 Test counts and Gate 6 (C)

| Statement | Reconciliation |
|---|---|
| "449 tests, 448 passed, 1 skipped" | Count was real for `unittest discover` (449). It is **458** now. The "448 passed" figure is derived (449 − 1 skip). |
| Overall harness | `scripts/verify_stage_1.py` **FAILED (fail-closed)** at **Gate 6** (Antigravity Integration Test: `agy.exe` headless permission denial + `gem_handshakes.log` rollover `PermissionError` on Windows). Gates 1–5 passed (Gate 5 = the 449-test unit suite). **Gates 7 (baseline-failure preservation) and 8 (repository state) were never executed** because the harness aborts on first failure. |

The original report described Gate 6 as a non-database artifact, which is plausible, but then presented a favourable overall verdict without stating that **the harness did not pass and two gates did not run**. The harness must **not** be described as passing. This run was observed in the previous session and was **not** re-executed here (it invokes the external `agy.exe`).

---

## 6. Invariants

| Check | Result |
|---|---|
| 8 authoritative files SHA-256 | **Identical** to the Stage 1.5-A.3 baseline (`ssot.json 0a2ba4d8…14da`, `trade_lessons.json 0c4ac816…9463`, `decision_log.json 757e0b48…0af2`, `context/config.json 30ea2b67…99d5`, `user_config.json 88e00f3e…7183`, `agent-events.jsonl 58a830fe…bc3d`, `config.json 10c005ef…4ee3`, `rules.md 04ea36c0…5056`). Verified **after** the full test run. |
| `context/gem.db`, `cache/market_data.db` | Absent (`False`, `False`) |
| Trading rules, financial calculations, schema, strategy | Not touched |
| Stage 1.5-B…1.6 work | None |
| Git | No commit/push/pull/branch/reset operations performed; read-only inspection only |

> [!NOTE]
> The test run prints `[System] Watch list updated in ssot.json and config.json.` Hashes were identical afterwards, so the tests restore or isolate those files, but this was verified only **post-run**, not during it.

---

## 7. File-Change Inventory

`git status --short` / `git diff --stat` (only these, plus new untracked report files):

| File | Change |
|---|---|
| `python/database/repositories/base.py` | `_ISO_TIMESTAMP_PATTERN` tightened; `validate_timestamp_utc()` rewritten (no strip, only `Z`/`+00:00`, original string returned, docstring corrected). +~28 / −~19 lines |
| `tests/test_database_repositories.py` | 2 existing tests corrected, class docstring updated, 9 tests added. +134 / −~9 lines |
| `docs/engineering/STAGE_1_5_A_3_1_STRICT_UTC_TIMESTAMP_CORRECTION_AND_EVIDENCE_RECONCILIATION_REPORT.md` | **New** (this report) |
| `docs/engineering/STAGE_1_5_A_3_FINAL_REPOSITORY_TRANSACTION_AND_AUTHORIZATION_CLOSEOUT_ASSESSMENT.md` | Correction banner prepended; body left intact as a historical record |

Scratch (outside the repo): `scratch/old_ts_behaviour.py`, `scratch/full_suite.txt`, `scratch/prompt.txt`. Git warns that LF will be converted to CRLF on next touch for the two modified files; this is an existing line-ending setting, not a content change.

---

## 8. Remaining Limitations

- **L-1 — `AuditEventRepository.append_event()` does not validate `timestamp_utc`.** It preserves byte-for-byte but accepts any string (e.g. naive or `-00:00`). It has no non-test callers in `python/` today. Not changed: validating it alters a different API contract, is outside the specified finding, and may affect existing tests. **Recommended** as a small follow-up decision for the owner.
- **L-2 — POSIX paths unexecuted** on this Windows machine (1 skip).
- **L-3 — Harness Gate 6 failing; Gates 7–8 unexecuted** (Section 5.4). Unchanged by this stage.
- **L-4 — The original findings matrix needs rebuilding** with real test references and verbatim source citations (Section 5.2/5.3).

---

## 9. Final Assessment

**Timestamp finding TS-CORR-01: resolved and verified.** The validator now rejects `-00:00`, `+0000`, every whitespace form, naive/malformed/non-ASCII-digit input and non-strings; returns valid input byte-for-byte; and persistence round-trips are proven for all six mutation methods. 458/458 tests pass (1 platform skip) and invariants hold.

**Stage 1.5-A.3 closeout status: re-opened for evidence.** The original verdict (`COMPLETE WITH LIMITATIONS`) rested on a matrix whose test references are almost entirely non-existent and which contained non-verbatim source. This stage did not re-audit the rest of that matrix, so the closeout is **not established** until L-4 is addressed. This is an evidence defect in the *report*; no new defect in the transaction/authorization/backup *code* was found, but none was re-proven either.

```
================================================================================
STAGE 1.5-A.3.1 VERDICT: COMPLETE WITH LIMITATIONS — HUMAN REVIEW REQUIRED
================================================================================
```

Execution stopped. Awaiting human review; no subsequent stage authorized.

