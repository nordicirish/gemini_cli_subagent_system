# Stage 1.3.1.5 Completion Report: SQLite CHECK Extractor SQL Lexical Integrity & Quoted-Identifier Spoofing Prevention

**Repository:** `nordicirish/gemini_cli_subagent_system`  
**Branch:** `main`  
**Baseline HEAD:** `98f5c03f3398f690e2384fc544b23b4d10bff5dc`  
**Predecessor:** Stage 1.3.1.4  
**Stage:** 1.3.1.5  
**Standard:** Agent Governance Framework (`.agents/AGENTS.md`, Protocols 01–06)  
**Verification Date:** 2026-11-08  
**Scope:** Elimination of quoted-identifier spoofing vulnerability in `SchemaMigrator.extract_check_expressions()` via fail-closed SQL lexical scanning, verification of SQLite automatic index interaction, and adversarial regression testing prior to Stage 1.4.

---

## 1. Verified Baseline HEAD

- **Repository:** `nordicirish/gemini_cli_subagent_system`
- **Branch:** `main`
- **Baseline Commit SHA:** `98f5c03f3398f690e2384fc544b23b4d10bff5dc`
- **Working Tree State at Baseline Inception:** Clean working tree, verified via `git rev-parse HEAD` and `git status --short`.
- **Git Ownership Rule:** The human repository owner holds exclusive authority over all Git commits, pushes, and branch operations. All Stage 1.3.1.5 implementation changes remain strictly uncommitted in the local working tree.

---

## 2. Pre-Implementation Spoofing Reproduction & Root Cause Analysis

### 2.1 Adversarial Reproduction
Prior to code modifications, an adversarial reproduction script was executed against the Stage 1.3.1.4 implementation. The test reconstructed the core `database_metadata` table without its mandatory singleton CHECK constraint (`CHECK (id = 1)`), substituting a UNIQUE constraint whose quoted name mimicked the expected CHECK clause:

```sql
CREATE TABLE database_metadata (
    id INTEGER PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    db_revision INTEGER NOT NULL DEFAULT 0,
    created_at_utc TEXT NOT NULL,
    last_updated_at_utc TEXT NOT NULL,
    CONSTRAINT "CHECK (id = 1)" UNIQUE(schema_version)
);
```

### 2.2 Pre-Implementation Result
- `SchemaMigrator.extract_check_expressions(ddl)` extracted: `['id = 1']`.
- `SchemaMigrator.verify_core_schema_integrity(conn)` **PASSED SILENTLY**.
- **Impact:** The table retained zero CHECK constraints at runtime. Non-singleton rows (e.g. `id = 2`) could be written directly to `database_metadata`, bypassing core financial schema integrity.

### 2.3 Root Cause and Severity
- **Severity:** High (integrity bypass via lexical confusion).
- **Root Cause:** The Stage 1.3.1.4 extractor searched the table body for the substring `"CHECK"`, tracked only single-quoted string literals (`'...'`), and balanced parentheses. It did not recognize double quotes (`"..."`), backticks (`` `...` ``), bracket quotes (`[...]`), or C-style block comments (`/* ... */`). Consequently, any text resembling `CHECK (...)` within an identifier or comment was erroneously extracted as a genuine CHECK expression.

---

## 3. SQL Lexer & Parser Architecture

To resolve this vulnerability without introducing external third-party SQL parser dependencies, Stage 1.3.1.5 implemented a dedicated standard-library lexical scanner in `SchemaMigrator` with strict fail-closed validation:

### 3.1 Lexical Recognition Boundary (`extract_check_expressions`)
1. **Outermost Table Body Extraction (Phase 1):**
   - Traverses the DDL from start to end, skipping whitespace, single-line comments (`-- ...`), block comments (`/* ... */`), single-quoted strings (`'...'`), double-quoted identifiers (`"..."`), backtick identifiers (`` `...` ``), and bracket identifiers (`[...]`).
   - Identifies the first unquoted opening parenthesis `(` as the start of the table body.
   - Accurately balances unquoted parentheses until the outer table body closes at depth 0.
   - Inspects trailing DDL for unclosed quotes, unclosed comments, or unexpected parentheses.
2. **Table Body Lexical Scanning (Phase 2):**
   - Inside the table body, all quoting contexts and comments are tracked and skipped with escape handling:
     - Single-quoted string literals (`'...'`) with `''` escape.
     - Double-quoted identifiers (`"..."`) with `""` escape.
     - Backtick-quoted identifiers (`` `...` ``) with ```` `` ```` escape.
     - Bracket-quoted identifiers (`[...]`).
     - Single-line comments (`-- ...`).
     - Block comments (`/* ... */`).
   - Only standalone, unquoted words matching `CHECK` (case-insensitive) are recognized.
   - The unquoted `CHECK` keyword must be followed by optional whitespace/comments and an opening parenthesis `(`. Any deviation raises `SchemaIntegrityError`.
   - The CHECK expression is extracted by scanning forward until the matching closing parenthesis at depth 0. During this scan, quotes and comments are fully tracked so parentheses inside strings or identifiers never alter depth.
3. **Fail-Closed Guarantee:**
   - Any unterminated string literal, double-quoted identifier, backtick identifier, bracket identifier, block comment, or unbalanced parenthesis immediately raises `SchemaIntegrityError`.

### 3.2 Hardened Expression Tokenizer (`tokenize_sql_expression`)
- Enhanced to recognize and strip comments (`--` and `/* ... */`).
- Normalizes double-quoted, backtick-quoted, and bracket-quoted identifiers to canonical lowercase tokens.
- Preserves single-quoted string literals verbatim.
- Raises `SchemaIntegrityError` on unterminated quotes, unterminated block comments, or unbalanced parentheses.

---

## 4. Assessment of SQLite Automatic Indexes

Section 2 and Section 5 mandated assessing the interaction between SQLite automatic indexes and schema integrity verification:

1. **Automatic Index Mechanism:** When a table defines a `UNIQUE` or `PRIMARY KEY` constraint, SQLite automatically creates an internal B-tree index (e.g. `sqlite_autoindex_table_1`) reported in `PRAGMA index_list` with `origin == 'u'` (for UNIQUE) or `origin == 'pk'` (for PRIMARY KEY).
2. **Existing Index Verifier Policy:** Step 6 of `_verify_schema_structural_integrity()` deliberately inspects only `origin == 'c'` (indexes created via explicit `CREATE INDEX` statements). This allows SQLite's internal automatic indexes for primary keys and legitimate UNIQUE constraints (e.g. on `scout_categories.category_name` and `owner_approvals.approval_id`) to function without false rejections.
3. **Integrity Guarantee Assessment:** In the spoofing scenario `CONSTRAINT "CHECK (id = 1)" UNIQUE(schema_version)`, SQLite generates an automatic index with `origin == 'u'`. While Step 6 ignores `origin == 'u'`, the hardened lexical CHECK extractor in Stage 1.3.1.5 recognizes `"CHECK (id = 1)"` as an identifier token and extracts **zero CHECK expressions**. Step 4 immediately fails closed due to count mismatch (`expected 1, found 0`).
4. **Conclusion:** SQLite automatic indexes cannot conceal an unintended schema change or CHECK constraint omission. The existing index verification policy is documented and safe.

---

## 5. Adversarial Testing & Verification Evidence

Twenty new unit tests were added to [`tests/test_database_core.py`](file:///c:/github/gemini_cli_subagent_system/tests/test_database_core.py) in `TestCheckExtractorSqlLexicalIntegrity`, directly addressing Section 4 requirements:

### 5.1 Quoted Constraint-Name Spoofing Tests (Section 4.A)
1. `test_quoted_constraint_name_spoofing_double_quotes_rejected`: Recreated `database_metadata` with `CONSTRAINT "CHECK (id = 1)" UNIQUE(schema_version)` and actual check removed. Extractor returned `[]`, and `verify_core_schema_integrity` raised `SchemaIntegrityError` (count mismatch).
2. `test_quoted_constraint_name_spoofing_backticks_rejected`: Backtick-quoted constraint names mimicking CHECK returned `[]`, failing verification closed.
3. `test_quoted_constraint_name_spoofing_brackets_rejected`: Bracket-quoted constraint names mimicking CHECK returned `[]`, failing verification closed.

### 5.2 Quoted-Token Handling Tests (Section 4.B)
4. `test_single_quoted_string_literal_not_extracted_as_check`: `DEFAULT 'CHECK (id = 1)'` returned `[]`.
5. `test_quoted_column_names_not_extracted_as_check`: Column names formatted as `"CHECK (id = 1)"`, `` `CHECK (id = 1)` ``, and `[CHECK (id = 1)]` returned `[]`.

### 5.3 Comment Handling Tests (Section 4.C)
6. `test_single_line_comment_with_check_not_extracted`: `-- CHECK (id = 1)` returned `[]`.
7. `test_block_comment_with_check_not_extracted`: `/* CHECK (id = 1) */` returned `[]`.
8. `test_block_comment_with_nested_parentheses_not_extracted`: `/* ((( CHECK (id = 1) ))) */` returned `[]`.

### 5.4 Parenthesis & Escaping Handling Tests (Section 4.D)
9. `test_nested_parentheses_in_genuine_check_extracted`: Verified nested expressions e.g. `((amount NOT LIKE ...))` extracted completely.
10. `test_escaped_single_quotes_inside_check`: Verified `''` inside string literals preserved (`name != 'it''s bad'`).
11. `test_escaped_double_quotes_in_quoted_identifier`: Verified `""` in constraint names parsed without corruption.
12. `test_parentheses_inside_string_literals_do_not_balance`: Verified `%(%)%` inside single quotes does not close CHECK early.
13. `test_parentheses_inside_quoted_identifiers_do_not_balance`: Verified `"col(1)"` does not close CHECK early.

### 5.5 Fail-Closed Handling Tests (Section 4.E)
14. `test_unterminated_single_quote_fails_closed`: Unterminated `'` raised `SchemaIntegrityError`.
15. `test_unterminated_double_quote_fails_closed`: Unterminated `"` raised `SchemaIntegrityError`.
16. `test_unterminated_backtick_fails_closed`: Unterminated `` ` `` raised `SchemaIntegrityError`.
17. `test_unterminated_bracket_fails_closed`: Unterminated `[` raised `SchemaIntegrityError`.
18. `test_unterminated_block_comment_fails_closed`: Unterminated `/*` raised `SchemaIntegrityError`.
19. `test_unbalanced_check_parentheses_fails_closed`: Unbalanced `(` in table body raised `SchemaIntegrityError`.
20. `test_check_keyword_without_opening_parenthesis_fails_closed`: Standalone `CHECK` keyword without `(` raised `SchemaIntegrityError`.

---

## 6. Test Suite Execution Results

All commands executed using virtual environment interpreter (`.\.venv\Scripts\python.exe`):

1. **Database Core Test Suite:**
   ```bash
   .\.venv\Scripts\python.exe -m unittest tests.test_database_core -v
   ```
   **Result:** `Ran 90 tests in 3.108s — OK (90/90 PASS, 0 FAIL, 0 ERROR)`
   * All 70 existing tests (including all 15 Stage 1.3.1.4 anti-bypass tests) passed.
   * All 20 new Stage 1.3.1.5 lexical integrity tests passed.

2. **Full Repository Regression Suite:**
   ```bash
   .\.venv\Scripts\python.exe -m unittest discover tests -v
   ```
   **Result:** `Ran 144 tests in 29.842s — OK (144/144 PASS, 0 FAIL, 0 ERROR)`
   * `test_atomic_io` (15/15 PASS)
   * `test_audit_logger` (4/4 PASS)
   * `test_database_core` (90/90 PASS)
   * `test_investment_regression` (7/7 PASS)
   * `test_permissions` (14/14 PASS)
   * `test_tool_dispatch` (8/8 PASS)
   * `test_watchlist_endpoints` (6/6 PASS)

3. **Compilation & Packaging Integrity:**
   ```bash
   .\.venv\Scripts\python.exe -m compileall -q python tests   # 0 syntax errors
   .\.venv\Scripts\pip.exe check                             # No broken requirements found.
   ```

---

## 7. Cryptographic Persistence Isolation Results (SHA-256)

All 6 authoritative application state files remain 100% byte-for-byte identical to their pre-implementation baseline:

| File Path | Baseline SHA-256 | Post-Test SHA-256 | Match Status |
|---|---|---|---|
| `context/ssot.json` | `0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da` | `0a2ba4d8c125d213c5772b36164637df74094ca95e28e2ca0afc2da5e55414da` | **MATCH (INVARIANT)** |
| `context/trade_lessons.json` | `0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463` | `0c4ac816009cb348f4bc923d94124f41f5c6d1ec8722a487bba9115f66029463` | **MATCH (INVARIANT)** |
| `context/decision_log.json` | `757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2` | `757e0b4855458ce7075ec16697798b3047ccc72a6214cded6d7bbb1346700af2` | **MATCH (INVARIANT)** |
| `context/config.json` | `30ea2b67dd0ecac4b16e3e726c9644948119729023d382c385301f9db72099d5` | `30ea2b67dd0ecac4b16e3e726c9644948119729023d382c385301f9db72099d5` | **MATCH (INVARIANT)** |
| `context/agent_logs/agent-events.jsonl` | `58a830fe4a59a02bb16d33df10c1a09b3e76a2b6250377e26ee37a8d73d8bc3d` | `58a830fe4a59a02bb16d33df10c1a09b3e76a2b6250377e26ee37a8d73d8bc3d` | **MATCH (INVARIANT)** |
| `config.json` | `10c005efa15940d9c503aae62bbcb9a195ddbe249387c8335a4cb0f836064ee3` | `10c005efa15940d9c503aae62bbcb9a195ddbe249387c8335a4cb0f836064ee3` | **MATCH (INVARIANT)** |

Filesystem check confirms `context/gem.db` and `cache/market_data.db` do not exist.

---

## 8. Changed-File Inventory

| File | Status | Lines Changed | Description |
|---|---|---:|---|
| `python/database/schema.py` | Modified | +531 / -76 | Replaced naive substring extraction in `extract_check_expressions()` with fail-closed SQL lexical scanner supporting single-quotes, double-quotes, backticks, brackets, line comments, block comments, and parenthesis depth tracking. Hardened `tokenize_sql_expression()` with comment stripping and quoted-identifier normalization. Updated governance docstrings. |
| `tests/test_database_core.py` | Modified | +226 / -0 | Added `TestCheckExtractorSqlLexicalIntegrity` with 20 unit tests covering quoted constraint-name spoofing, quoted-token handling, comment handling, parenthesis/quote escaping, and fail-closed error paths. Total database tests expanded from 70 to 90. |

Zero modifications made to trading rules, investment parameters, or financial calculations.

---

## 9. Remaining Limitations

1. **SQLite Embedded Lexer Scope:** The lexical scanner is deliberately tailored to SQLite table DDL syntax (`CREATE TABLE`) and CHECK expressions. It does not attempt to be a general-purpose SQL parser for complex statements like `SELECT` with CTEs or window functions, which are outside the scope of DDL schema validation.
2. **Schema Version Invariance:** The schema version remains `1`. No migrations were necessary.

---

## 10. Assessment of Stage 1.4 Readiness

With Stage 1.3.1.5 complete:
1. **Quoted-identifier spoofing is fully eliminated:** Quoted constraint names, column names, default strings, and comments cannot spoof CHECK constraints.
2. **Fail-closed guarantees are exhaustive:** All malformed, ambiguous, or unterminated constructs raise `SchemaIntegrityError`.
3. **Persisted state isolation is intact:** Zero production databases created, zero state files touched.
4. **Test coverage is comprehensive:** 90 database core unit tests and 144 full regression tests pass.

**Recommendation:** The repository is fully prepared and certified for Stage 1.4 (Repository Layer Implementation).

---

## 11. Final Verdict

### **STAGE 1.3.1.5 COMPLETE**

The SQL lexical integrity vulnerability in CHECK constraint extraction has been eliminated with verified, fail-closed standard-library lexing and 20 adversarial unit tests. All regression and isolation gates pass.

