"""
Database Schema Definitions and Versioned Migration Infrastructure
Governed by Stage 1.3 Directive and Stage 1.2.1 / 1.3.1 / 1.3.1.2 / 1.3.1.3 Architecture Corrections.

Provides canonical DDL definitions, table constraints, index definitions,
and versioned schema initialisation for GEM's embedded SQLite persistence layer.
"""

import datetime
import sqlite3
from typing import Optional, Tuple, List, Dict, Any, Set


CURRENT_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Schema Exception Hierarchy
# ---------------------------------------------------------------------------

class SchemaError(Exception):
    """Base exception for schema management errors."""
    pass


class SchemaVersionMismatchError(SchemaError):
    """Raised when the database schema version is unsupported or newer than code."""
    pass


class SchemaIntegrityError(SchemaError):
    """Raised when an existing database is missing required tables, columns, indexes, or constraints."""
    pass


# ---------------------------------------------------------------------------
# Canonical Core Schema DDL Statements
# ---------------------------------------------------------------------------

CORE_SCHEMA_STATEMENTS = [
    # 1. Database Metadata & Monotonic Revision Counter
    """
    CREATE TABLE IF NOT EXISTS database_metadata (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        schema_version INTEGER NOT NULL,
        db_revision INTEGER NOT NULL DEFAULT 0,
        created_at_utc TEXT NOT NULL,
        last_updated_at_utc TEXT NOT NULL
    );
    """,

    # 2. Portfolio Cash Balances (Exact Decimal TEXT Storage; NO REAL)
    # Scale: at most 2 decimal places. Canonical decimal string:
    # Exactly one period permitted (NOT GLOB '*.*.*' and NOT LIKE '%.%.%'),
    # no leading/trailing dot, no non-canonical leading zeros,
    # strictly non-negative, non-empty, no exponents or whitespace.
    """
    CREATE TABLE IF NOT EXISTS portfolio_cash (
        account_id TEXT NOT NULL DEFAULT 'PRIMARY',
        currency TEXT NOT NULL CHECK (currency IN ('EUR', 'USD')),
        amount TEXT NOT NULL CHECK (
            amount NOT GLOB '*[^0-9.]*'
            AND amount NOT GLOB '*.*.*'
            AND amount NOT LIKE '%.%.%'
            AND amount NOT GLOB '.*'
            AND amount NOT GLOB '*.'
            AND amount NOT GLOB '0[0-9]*'
            AND length(amount) > 0
            AND (
                amount NOT LIKE '%.%'
                OR length(substr(amount, instr(amount, '.') + 1)) <= 2
            )
        ),
        last_updated_at_utc TEXT NOT NULL,
        PRIMARY KEY (account_id, currency)
    );
    """,

    # 3. Portfolio Positions (Exact Decimal TEXT Storage; NO REAL)
    # Shares: scale <= 4. WAC EUR: scale <= 4. WAC USD: nullable, scale <= 4.
    """
    CREATE TABLE IF NOT EXISTS portfolio_positions (
        account_id TEXT NOT NULL DEFAULT 'PRIMARY',
        ticker TEXT NOT NULL CHECK (ticker = UPPER(TRIM(ticker)) AND length(ticker) > 0),
        shares TEXT NOT NULL CHECK (
            shares NOT GLOB '*[^0-9.]*'
            AND shares NOT GLOB '*.*.*'
            AND shares NOT LIKE '%.%.%'
            AND shares NOT GLOB '.*'
            AND shares NOT GLOB '*.'
            AND shares NOT GLOB '0[0-9]*'
            AND length(shares) > 0
            AND (
                shares NOT LIKE '%.%'
                OR length(substr(shares, instr(shares, '.') + 1)) <= 4
            )
        ),
        wac_eur TEXT NOT NULL CHECK (
            wac_eur NOT GLOB '*[^0-9.]*'
            AND wac_eur NOT GLOB '*.*.*'
            AND wac_eur NOT LIKE '%.%.%'
            AND wac_eur NOT GLOB '.*'
            AND wac_eur NOT GLOB '*.'
            AND wac_eur NOT GLOB '0[0-9]*'
            AND length(wac_eur) > 0
            AND (
                wac_eur NOT LIKE '%.%'
                OR length(substr(wac_eur, instr(wac_eur, '.') + 1)) <= 4
            )
        ),
        wac_usd TEXT CHECK (
            wac_usd IS NULL OR (
                wac_usd NOT GLOB '*[^0-9.]*'
                AND wac_usd NOT GLOB '*.*.*'
                AND wac_usd NOT LIKE '%.%.%'
                AND wac_usd NOT GLOB '.*'
                AND wac_usd NOT GLOB '*.'
                AND wac_usd NOT GLOB '0[0-9]*'
                AND length(wac_usd) > 0
                AND (
                    wac_usd NOT LIKE '%.%'
                    OR length(substr(wac_usd, instr(wac_usd, '.') + 1)) <= 4
                )
            )
        ),
        last_updated_at_utc TEXT NOT NULL,
        PRIMARY KEY (account_id, ticker)
    );
    """,

    # 4. Watched Tickers
    """
    CREATE TABLE IF NOT EXISTS watchlist (
        ticker TEXT PRIMARY KEY NOT NULL CHECK (ticker = UPPER(TRIM(ticker)) AND length(ticker) > 0),
        status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'PAUSED', 'REMOVED')),
        added_at_utc TEXT NOT NULL,
        notes TEXT
    );
    """,

    # 5. Scout Categories
    """
    CREATE TABLE IF NOT EXISTS scout_categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category_name TEXT NOT NULL UNIQUE CHECK (length(trim(category_name)) > 0),
        description TEXT,
        tickers_json TEXT NOT NULL DEFAULT '[]',
        updated_at_utc TEXT NOT NULL
    );
    """,

    # 6. Codified Trade Lessons & Directives
    """
    CREATE TABLE IF NOT EXISTS trade_lessons (
        rule_code TEXT PRIMARY KEY NOT NULL CHECK (rule_code = UPPER(TRIM(rule_code)) AND length(rule_code) > 0),
        directive_text TEXT NOT NULL CHECK (length(trim(directive_text)) > 0),
        category TEXT NOT NULL CHECK (category IN ('MANDATE', 'EXECUTION', 'STRUCTURAL', 'RISK', 'HEURISTIC')),
        status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'DEPRECATED', 'SUSPENDED')),
        created_at_utc TEXT NOT NULL,
        last_reviewed_at_utc TEXT NOT NULL
    );
    """,

    # 7. Investment Council Decision Log
    """
    CREATE TABLE IF NOT EXISTS decision_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        ticker TEXT CHECK (ticker IS NULL OR (ticker = UPPER(TRIM(ticker)) AND length(ticker) > 0)),
        action_proposed TEXT NOT NULL,
        decision_outcome TEXT NOT NULL,
        votes_json TEXT NOT NULL DEFAULT '{}',
        transcript_text TEXT,
        timestamp_utc TEXT NOT NULL
    );
    """,

    # 8. Transactional Financial State Changelog
    """
    CREATE TABLE IF NOT EXISTS financial_changelog (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        correlation_id TEXT NOT NULL,
        actor_role TEXT NOT NULL,
        operation_name TEXT NOT NULL,
        target_resource TEXT NOT NULL,
        approval_digest TEXT,
        before_state_json TEXT NOT NULL,
        after_state_json TEXT NOT NULL,
        timestamp_utc TEXT NOT NULL
    );
    """,

    # 9. Owner Approvals Record
    """
    CREATE TABLE IF NOT EXISTS owner_approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        approval_id TEXT UNIQUE NOT NULL,
        operation_name TEXT NOT NULL,
        target_resource TEXT NOT NULL,
        payload_digest TEXT NOT NULL,
        granted_by TEXT NOT NULL DEFAULT 'Owner',
        consumed_at_utc TEXT,
        status TEXT NOT NULL CHECK (status IN ('PENDING', 'CONSUMED', 'EXPIRED', 'REVOKED')),
        created_at_utc TEXT NOT NULL
    );
    """,

    # 10. Indexed Audit Events Mirror
    # Requires exactly 32-character lowercase hexadecimal event_id matching uuid.uuid4().hex
    """
    CREATE TABLE IF NOT EXISTS audit_events (
        event_id TEXT PRIMARY KEY NOT NULL CHECK (
            length(event_id) = 32
            AND event_id NOT GLOB '*[^0-9a-f]*'
        ),
        timestamp_utc TEXT NOT NULL,
        action_category TEXT NOT NULL,
        status TEXT NOT NULL,
        agent_role TEXT NOT NULL,
        tool_name TEXT,
        permission_decision TEXT NOT NULL,
        approval_status TEXT NOT NULL,
        payload_hash TEXT,
        raw_event_json TEXT NOT NULL
    );
    """,

    # Core Indexes
    "CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_events(timestamp_utc);",
    "CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_events(action_category);",
    "CREATE INDEX IF NOT EXISTS idx_changelog_corr ON financial_changelog(correlation_id);",
    "CREATE INDEX IF NOT EXISTS idx_decision_session ON decision_log(session_id);",
    "CREATE INDEX IF NOT EXISTS idx_decision_ticker ON decision_log(ticker);",
]


CACHE_SCHEMA_STATEMENTS = [
    # 1. Market Data Cache Metadata
    """
    CREATE TABLE IF NOT EXISTS cache_metadata (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        schema_version INTEGER NOT NULL,
        created_at_utc TEXT NOT NULL,
        last_updated_at_utc TEXT NOT NULL
    );
    """,

    # 2. Historical Daily Bars (OHLCV & Indicators)
    """
    CREATE TABLE IF NOT EXISTS daily_bars (
        ticker TEXT NOT NULL CHECK (ticker = UPPER(TRIM(ticker)) AND length(ticker) > 0),
        bar_date TEXT NOT NULL CHECK (length(bar_date) = 10), -- YYYY-MM-DD
        open TEXT NOT NULL CHECK (
            open NOT GLOB '*[^0-9.]*'
            AND open NOT GLOB '*.*.*'
            AND open NOT LIKE '%.%.%'
            AND open NOT GLOB '.*'
            AND open NOT GLOB '*.'
            AND open NOT GLOB '0[0-9]*'
            AND length(open) > 0
            AND (
                open NOT LIKE '%.%'
                OR length(substr(open, instr(open, '.') + 1)) <= 4
            )
        ),
        high TEXT NOT NULL CHECK (
            high NOT GLOB '*[^0-9.]*'
            AND high NOT GLOB '*.*.*'
            AND high NOT LIKE '%.%.%'
            AND high NOT GLOB '.*'
            AND high NOT GLOB '*.'
            AND high NOT GLOB '0[0-9]*'
            AND length(high) > 0
            AND (
                high NOT LIKE '%.%'
                OR length(substr(high, instr(high, '.') + 1)) <= 4
            )
        ),
        low TEXT NOT NULL CHECK (
            low NOT GLOB '*[^0-9.]*'
            AND low NOT GLOB '*.*.*'
            AND low NOT LIKE '%.%.%'
            AND low NOT GLOB '.*'
            AND low NOT GLOB '*.'
            AND low NOT GLOB '0[0-9]*'
            AND length(low) > 0
            AND (
                low NOT LIKE '%.%'
                OR length(substr(low, instr(low, '.') + 1)) <= 4
            )
        ),
        close TEXT NOT NULL CHECK (
            close NOT GLOB '*[^0-9.]*'
            AND close NOT GLOB '*.*.*'
            AND close NOT LIKE '%.%.%'
            AND close NOT GLOB '.*'
            AND close NOT GLOB '*.'
            AND close NOT GLOB '0[0-9]*'
            AND length(close) > 0
            AND (
                close NOT LIKE '%.%'
                OR length(substr(close, instr(close, '.') + 1)) <= 4
            )
        ),
        volume INTEGER NOT NULL CHECK (volume >= 0),
        indicators_json TEXT NOT NULL DEFAULT '{}',
        updated_at_utc TEXT NOT NULL,
        PRIMARY KEY (ticker, bar_date)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_bars_ticker ON daily_bars(ticker);",
    "CREATE INDEX IF NOT EXISTS idx_bars_date ON daily_bars(bar_date);",
]


# ---------------------------------------------------------------------------
# Expected Structural Definitions for Integrity Verification
# ---------------------------------------------------------------------------

# Expected columns: {table_name: {column_name: (declared_type, notnull_bool, pk_order, default_value)}}
EXPECTED_CORE_TABLE_DEFS: Dict[str, Dict[str, Tuple[str, bool, int, Optional[str]]]] = {
    "database_metadata": {
        "id": ("INTEGER", False, 1, None),
        "schema_version": ("INTEGER", True, 0, None),
        "db_revision": ("INTEGER", True, 0, "0"),
        "created_at_utc": ("TEXT", True, 0, None),
        "last_updated_at_utc": ("TEXT", True, 0, None),
    },
    "portfolio_cash": {
        "account_id": ("TEXT", True, 1, "'PRIMARY'"),
        "currency": ("TEXT", True, 2, None),
        "amount": ("TEXT", True, 0, None),
        "last_updated_at_utc": ("TEXT", True, 0, None),
    },
    "portfolio_positions": {
        "account_id": ("TEXT", True, 1, "'PRIMARY'"),
        "ticker": ("TEXT", True, 2, None),
        "shares": ("TEXT", True, 0, None),
        "wac_eur": ("TEXT", True, 0, None),
        "wac_usd": ("TEXT", False, 0, None),
        "last_updated_at_utc": ("TEXT", True, 0, None),
    },
    "watchlist": {
        "ticker": ("TEXT", True, 1, None),
        "status": ("TEXT", True, 0, "'ACTIVE'"),
        "added_at_utc": ("TEXT", True, 0, None),
        "notes": ("TEXT", False, 0, None),
    },
    "scout_categories": {
        "id": ("INTEGER", False, 1, None),
        "category_name": ("TEXT", True, 0, None),
        "description": ("TEXT", False, 0, None),
        "tickers_json": ("TEXT", True, 0, "'[]'"),
        "updated_at_utc": ("TEXT", True, 0, None),
    },
    "trade_lessons": {
        "rule_code": ("TEXT", True, 1, None),
        "directive_text": ("TEXT", True, 0, None),
        "category": ("TEXT", True, 0, None),
        "status": ("TEXT", True, 0, "'ACTIVE'"),
        "created_at_utc": ("TEXT", True, 0, None),
        "last_reviewed_at_utc": ("TEXT", True, 0, None),
    },
    "decision_log": {
        "id": ("INTEGER", False, 1, None),
        "session_id": ("TEXT", True, 0, None),
        "ticker": ("TEXT", False, 0, None),
        "action_proposed": ("TEXT", True, 0, None),
        "decision_outcome": ("TEXT", True, 0, None),
        "votes_json": ("TEXT", True, 0, "'{}'"),
        "transcript_text": ("TEXT", False, 0, None),
        "timestamp_utc": ("TEXT", True, 0, None),
    },
    "financial_changelog": {
        "id": ("INTEGER", False, 1, None),
        "correlation_id": ("TEXT", True, 0, None),
        "actor_role": ("TEXT", True, 0, None),
        "operation_name": ("TEXT", True, 0, None),
        "target_resource": ("TEXT", True, 0, None),
        "approval_digest": ("TEXT", False, 0, None),
        "before_state_json": ("TEXT", True, 0, None),
        "after_state_json": ("TEXT", True, 0, None),
        "timestamp_utc": ("TEXT", True, 0, None),
    },
    "owner_approvals": {
        "id": ("INTEGER", False, 1, None),
        "approval_id": ("TEXT", True, 0, None),
        "operation_name": ("TEXT", True, 0, None),
        "target_resource": ("TEXT", True, 0, None),
        "payload_digest": ("TEXT", True, 0, None),
        "granted_by": ("TEXT", True, 0, "'Owner'"),
        "consumed_at_utc": ("TEXT", False, 0, None),
        "status": ("TEXT", True, 0, None),
        "created_at_utc": ("TEXT", True, 0, None),
    },
    "audit_events": {
        "event_id": ("TEXT", True, 1, None),
        "timestamp_utc": ("TEXT", True, 0, None),
        "action_category": ("TEXT", True, 0, None),
        "status": ("TEXT", True, 0, None),
        "agent_role": ("TEXT", True, 0, None),
        "tool_name": ("TEXT", False, 0, None),
        "permission_decision": ("TEXT", True, 0, None),
        "approval_status": ("TEXT", True, 0, None),
        "payload_hash": ("TEXT", False, 0, None),
        "raw_event_json": ("TEXT", True, 0, None),
    },
}

# Expected foreign keys: {table_name: List[Dict[str, Any]]}
# Core schema intentionally contains zero foreign keys in Stage 1.3
EXPECTED_CORE_FOREIGN_KEYS: Dict[str, List[Dict[str, Any]]] = {
    tbl: [] for tbl in EXPECTED_CORE_TABLE_DEFS
}

# Expected indexes: {index_name: (target_table, indexed_columns, is_unique, is_partial)}
EXPECTED_CORE_INDEX_DEFS: Dict[str, Tuple[str, List[str], bool, bool]] = {
    "idx_audit_timestamp": ("audit_events", ["timestamp_utc"], False, False),
    "idx_audit_action": ("audit_events", ["action_category"], False, False),
    "idx_changelog_corr": ("financial_changelog", ["correlation_id"], False, False),
    "idx_decision_session": ("decision_log", ["session_id"], False, False),
    "idx_decision_ticker": ("decision_log", ["ticker"], False, False),
}

EXPECTED_CACHE_TABLE_DEFS: Dict[str, Dict[str, Tuple[str, bool, int, Optional[str]]]] = {
    "cache_metadata": {
        "id": ("INTEGER", False, 1, None),
        "schema_version": ("INTEGER", True, 0, None),
        "created_at_utc": ("TEXT", True, 0, None),
        "last_updated_at_utc": ("TEXT", True, 0, None),
    },
    "daily_bars": {
        "ticker": ("TEXT", True, 1, None),
        "bar_date": ("TEXT", True, 2, None),
        "open": ("TEXT", True, 0, None),
        "high": ("TEXT", True, 0, None),
        "low": ("TEXT", True, 0, None),
        "close": ("TEXT", True, 0, None),
        "volume": ("INTEGER", True, 0, None),
        "indicators_json": ("TEXT", True, 0, "'{}'"),
        "updated_at_utc": ("TEXT", True, 0, None),
    },
}

# Cache schema intentionally contains zero foreign keys in Stage 1.3
EXPECTED_CACHE_FOREIGN_KEYS: Dict[str, List[Dict[str, Any]]] = {
    tbl: [] for tbl in EXPECTED_CACHE_TABLE_DEFS
}

EXPECTED_CACHE_INDEX_DEFS: Dict[str, Tuple[str, List[str], bool, bool]] = {
    "idx_bars_ticker": ("daily_bars", ["ticker"], False, False),
    "idx_bars_date": ("daily_bars", ["bar_date"], False, False),
}

# Complete mandatory constraint patterns that must be present in table DDL
MANDATORY_CORE_TABLE_CONSTRAINTS: Dict[str, List[str]] = {
    "database_metadata": [
        "id = 1",
    ],
    "portfolio_cash": [
        "currency IN ('EUR', 'USD')",
        "amount NOT GLOB '*[^0-9.]*'",
        "amount NOT GLOB '*.*.*'",
        "amount NOT LIKE '%.%.%'",
        "amount NOT GLOB '.*'",
        "amount NOT GLOB '*.'",
        "amount NOT GLOB '0[0-9]*'",
        "length(amount) > 0",
        "amount NOT LIKE '%.%' OR length(substr(amount, instr(amount, '.') + 1)) <= 2",
    ],
    "portfolio_positions": [
        "ticker = UPPER(TRIM(ticker))",
        "length(ticker) > 0",
        "shares NOT GLOB '*[^0-9.]*'",
        "shares NOT GLOB '*.*.*'",
        "shares NOT LIKE '%.%.%'",
        "shares NOT GLOB '.*'",
        "shares NOT GLOB '*.'",
        "shares NOT GLOB '0[0-9]*'",
        "length(shares) > 0",
        "shares NOT LIKE '%.%' OR length(substr(shares, instr(shares, '.') + 1)) <= 4",
        "wac_eur NOT GLOB '*[^0-9.]*'",
        "wac_eur NOT GLOB '*.*.*'",
        "wac_eur NOT LIKE '%.%.%'",
        "wac_eur NOT GLOB '.*'",
        "wac_eur NOT GLOB '*.'",
        "wac_eur NOT GLOB '0[0-9]*'",
        "length(wac_eur) > 0",
        "wac_eur NOT LIKE '%.%' OR length(substr(wac_eur, instr(wac_eur, '.') + 1)) <= 4",
        "wac_usd IS NULL OR",
        "wac_usd NOT GLOB '*[^0-9.]*'",
        "wac_usd NOT GLOB '*.*.*'",
        "wac_usd NOT LIKE '%.%.%'",
        "wac_usd NOT GLOB '.*'",
        "wac_usd NOT GLOB '*.'",
        "wac_usd NOT GLOB '0[0-9]*'",
        "length(wac_usd) > 0",
        "wac_usd NOT LIKE '%.%' OR length(substr(wac_usd, instr(wac_usd, '.') + 1)) <= 4",
    ],
    "watchlist": [
        "ticker = UPPER(TRIM(ticker))",
        "length(ticker) > 0",
        "status IN ('ACTIVE', 'PAUSED', 'REMOVED')",
    ],
    "scout_categories": [
        "length(trim(category_name)) > 0",
    ],
    "trade_lessons": [
        "rule_code = UPPER(TRIM(rule_code))",
        "length(rule_code) > 0",
        "length(trim(directive_text)) > 0",
        "category IN ('MANDATE', 'EXECUTION', 'STRUCTURAL', 'RISK', 'HEURISTIC')",
        "status IN ('ACTIVE', 'DEPRECATED', 'SUSPENDED')",
    ],
    "decision_log": [
        "ticker IS NULL OR (ticker = UPPER(TRIM(ticker)) AND length(ticker) > 0)",
    ],
    "owner_approvals": [
        "status IN ('PENDING', 'CONSUMED', 'EXPIRED', 'REVOKED')",
    ],
    "audit_events": [
        "length(event_id) = 32",
        "event_id NOT GLOB '*[^0-9a-f]*'",
    ],
}

MANDATORY_CACHE_TABLE_CONSTRAINTS: Dict[str, List[str]] = {
    "cache_metadata": [
        "id = 1",
    ],
    "daily_bars": [
        "ticker = UPPER(TRIM(ticker))",
        "length(ticker) > 0",
        "length(bar_date) = 10",
        "open NOT GLOB '*[^0-9.]*'",
        "open NOT GLOB '*.*.*'",
        "open NOT LIKE '%.%.%'",
        "open NOT GLOB '.*'",
        "open NOT GLOB '*.'",
        "open NOT GLOB '0[0-9]*'",
        "length(open) > 0",
        "open NOT LIKE '%.%' OR length(substr(open, instr(open, '.') + 1)) <= 4",
        "high NOT GLOB '*[^0-9.]*'",
        "high NOT GLOB '*.*.*'",
        "high NOT LIKE '%.%.%'",
        "high NOT GLOB '.*'",
        "high NOT GLOB '*.'",
        "high NOT GLOB '0[0-9]*'",
        "length(high) > 0",
        "high NOT LIKE '%.%' OR length(substr(high, instr(high, '.') + 1)) <= 4",
        "low NOT GLOB '*[^0-9.]*'",
        "low NOT GLOB '*.*.*'",
        "low NOT LIKE '%.%.%'",
        "low NOT GLOB '.*'",
        "low NOT GLOB '*.'",
        "low NOT GLOB '0[0-9]*'",
        "length(low) > 0",
        "low NOT LIKE '%.%' OR length(substr(low, instr(low, '.') + 1)) <= 4",
        "close NOT GLOB '*[^0-9.]*'",
        "close NOT GLOB '*.*.*'",
        "close NOT LIKE '%.%.%'",
        "close NOT GLOB '.*'",
        "close NOT GLOB '*.'",
        "close NOT GLOB '0[0-9]*'",
        "length(close) > 0",
        "close NOT LIKE '%.%' OR length(substr(close, instr(close, '.') + 1)) <= 4",
        "volume >= 0",
    ],
}


# ---------------------------------------------------------------------------
# SchemaMigrator
# ---------------------------------------------------------------------------

class SchemaMigrator:
    """
    Installs, validates, and reports on versioned database schemas.
    All schema initialisations execute transactionally and are strictly idempotent.
    """

    @staticmethod
    def get_current_version(conn: sqlite3.Connection) -> Optional[int]:
        """
        Returns the recorded schema_version from database_metadata,
        or None if database_metadata does not exist.
        """
        cursor = conn.cursor()
        cursor.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='database_metadata';"
        )
        if cursor.fetchone()[0] == 0:
            return None

        cursor.execute("SELECT schema_version FROM database_metadata WHERE id = 1;")
        row = cursor.fetchone()
        return row[0] if row else None

    @classmethod
    def _verify_schema_structural_integrity(
        cls,
        conn: sqlite3.Connection,
        profile: str,
        expected_tables: Dict[str, Dict[str, Tuple[str, bool, int, Optional[str]]]],
        expected_indexes: Dict[str, Tuple[str, List[str], bool, bool]],
        mandatory_constraints: Dict[str, List[str]],
        expected_fks: Dict[str, List[Dict[str, Any]]],
    ) -> bool:
        """
        Shared fail-closed structural schema verifier for core and cache database profiles.
        Performs exhaustive structural verification across:
        1. Exact table existence, column sets, column ordering, declared types,
           nullability, primary key ordinals, and default values.
        2. Authoritative foreign-key definitions, referenced tables/columns, and actions.
        3. Mandatory financial and market-data CHECK constraint DDL patterns.
        4. Complete index specifications (target table, indexed columns, ordering,
           uniqueness, partial status, and absence of unexpected user indexes).
        """
        cursor = conn.cursor()

        # 1. Verify exact tables, columns, ordering, types, nullability, PKs, and defaults
        for table_name, expected_cols in expected_tables.items():
            actual_cols_info = cls.get_column_info(conn, table_name)
            if not actual_cols_info:
                raise SchemaIntegrityError(
                    f"{profile} schema integrity failure: missing required table '{table_name}'."
                )

            actual_col_names = [col["name"] for col in actual_cols_info]
            expected_col_names = list(expected_cols.keys())

            # Check for missing columns
            missing_cols = set(expected_col_names) - set(actual_col_names)
            if missing_cols:
                raise SchemaIntegrityError(
                    f"{profile} schema integrity failure: table '{table_name}' is missing column(s): {sorted(missing_cols)}."
                )

            # Check for unexpected additional columns (exact column set enforcement)
            unexpected_cols = set(actual_col_names) - set(expected_col_names)
            if unexpected_cols:
                raise SchemaIntegrityError(
                    f"{profile} schema integrity failure: table '{table_name}' has unexpected column(s): {sorted(unexpected_cols)}."
                )

            # Check exact column ordering
            if actual_col_names != expected_col_names:
                raise SchemaIntegrityError(
                    f"{profile} schema integrity failure: table '{table_name}' column ordering mismatch: "
                    f"expected {expected_col_names}, found {actual_col_names}."
                )

            # Check declared types first across all columns
            for col in actual_cols_info:
                col_name = col["name"]
                exp_type = expected_cols[col_name][0]
                if col["type"] != exp_type:
                    raise SchemaIntegrityError(
                        f"{profile} schema integrity failure: column '{table_name}.{col_name}' declared type mismatch: "
                        f"expected '{exp_type}', found '{col['type']}'."
                    )

            # Check NOT NULL flags across all columns
            for col in actual_cols_info:
                col_name = col["name"]
                exp_notnull = expected_cols[col_name][1]
                if col["notnull"] != exp_notnull:
                    raise SchemaIntegrityError(
                        f"{profile} schema integrity failure: column '{table_name}.{col_name}' NOT NULL mismatch: "
                        f"expected {exp_notnull}, found {col['notnull']}."
                    )

            # Check primary key ordinals across all columns
            for col in actual_cols_info:
                col_name = col["name"]
                exp_pk = expected_cols[col_name][2]
                if col["pk"] != exp_pk:
                    raise SchemaIntegrityError(
                        f"{profile} schema integrity failure: column '{table_name}.{col_name}' primary key mismatch: "
                        f"expected pk={exp_pk}, found pk={col['pk']}."
                    )

            # Check default values across all columns
            for col in actual_cols_info:
                col_name = col["name"]
                exp_default = expected_cols[col_name][3]
                if col["default_value"] != exp_default:
                    raise SchemaIntegrityError(
                        f"{profile} schema integrity failure: column '{table_name}.{col_name}' default value mismatch: "
                        f"expected {exp_default}, found {col['default_value']}."
                    )

        # 2. Verify foreign keys via PRAGMA foreign_key_list
        for table_name, expected_fk_list in expected_fks.items():
            cursor.execute(f"PRAGMA foreign_key_list({table_name});")
            actual_fks = []
            for r in cursor.fetchall():
                # PRAGMA foreign_key_list returns: (id, seq, table, from, to, on_update, on_delete, match)
                actual_fks.append({
                    "from": r[3],
                    "table": r[2],
                    "to": r[4],
                    "on_update": r[5].upper() if r[5] else "NO ACTION",
                    "on_delete": r[6].upper() if r[6] else "NO ACTION",
                })
            act_sorted = sorted(actual_fks, key=lambda x: (x["from"], x["table"], str(x["to"])))
            exp_sorted = sorted(expected_fk_list, key=lambda x: (x["from"], x["table"], str(x["to"])))
            if act_sorted != exp_sorted:
                raise SchemaIntegrityError(
                    f"{profile} schema integrity failure: table '{table_name}' foreign-key mismatch: "
                    f"expected {exp_sorted}, found {act_sorted}."
                )

        # 3. Verify mandatory CHECK constraints in table DDL
        for table_name, required_patterns in mandatory_constraints.items():
            cursor.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?;",
                (table_name,),
            )
            row = cursor.fetchone()
            if not row or not row[0]:
                raise SchemaIntegrityError(
                    f"{profile} schema integrity failure: cannot read DDL for table '{table_name}'."
                )
            ddl_sql = " ".join(row[0].split())
            for pattern in required_patterns:
                if pattern not in ddl_sql:
                    raise SchemaIntegrityError(
                        f"{profile} schema integrity failure: table '{table_name}' is missing mandatory constraint pattern: '{pattern}'."
                    )

        # 4. Verify indexes: existence, target table, columns, ordering, uniqueness, and partial status
        for index_name, (expected_tbl, expected_cols, exp_unique, exp_partial) in expected_indexes.items():
            cursor.execute(
                "SELECT tbl_name FROM sqlite_master WHERE type='index' AND name=?;",
                (index_name,),
            )
            row = cursor.fetchone()
            if not row:
                raise SchemaIntegrityError(
                    f"{profile} schema integrity failure: missing required index '{index_name}'."
                )
            act_tbl = row[0]
            if act_tbl != expected_tbl:
                raise SchemaIntegrityError(
                    f"{profile} schema integrity failure: index '{index_name}' target table mismatch: "
                    f"expected '{expected_tbl}', found '{act_tbl}'."
                )

            cursor.execute(f"PRAGMA index_list({expected_tbl});")
            idx_rows = cursor.fetchall()
            matching = [r for r in idx_rows if r[1] == index_name]
            if not matching:
                raise SchemaIntegrityError(
                    f"{profile} schema integrity failure: index '{index_name}' not listed in PRAGMA index_list for '{expected_tbl}'."
                )
            entry = matching[0]
            # entry: (seq, name, unique, origin, partial)
            act_unique = bool(entry[2])
            act_partial = bool(entry[4])
            if act_unique != exp_unique:
                raise SchemaIntegrityError(
                    f"{profile} schema integrity failure: index '{index_name}' uniqueness mismatch: "
                    f"expected unique={exp_unique}, found unique={act_unique}."
                )
            if act_partial != exp_partial:
                raise SchemaIntegrityError(
                    f"{profile} schema integrity failure: index '{index_name}' partial status mismatch: "
                    f"expected partial={exp_partial}, found partial={act_partial}."
                )

            cursor.execute(f"PRAGMA index_info({index_name});")
            act_cols = [r[2] for r in cursor.fetchall()]
            if act_cols != expected_cols:
                raise SchemaIntegrityError(
                    f"{profile} schema integrity failure: index '{index_name}' column mismatch: "
                    f"expected {expected_cols}, found {act_cols}."
                )

        # 5. Verify no unexpected user-created indexes exist on expected tables
        for table_name in expected_tables:
            cursor.execute(f"PRAGMA index_list({table_name});")
            for r in cursor.fetchall():
                idx_name = r[1]
                origin = r[3]
                if origin == "c" and idx_name not in expected_indexes:
                    raise SchemaIntegrityError(
                        f"{profile} schema integrity failure: table '{table_name}' has unexpected user index '{idx_name}'."
                    )

        return True

    @classmethod
    def verify_core_schema_integrity(cls, conn: sqlite3.Connection) -> bool:
        """
        Verifies that all required core tables, exact column structures, default values,
        ordering, foreign-key inventories, indexes (uniqueness and partial status),
        and critical financial validation constraints exist and match expected definitions.
        Fails closed with SchemaIntegrityError if any discrepancy is found.
        """
        return cls._verify_schema_structural_integrity(
            conn=conn,
            profile="Core",
            expected_tables=EXPECTED_CORE_TABLE_DEFS,
            expected_indexes=EXPECTED_CORE_INDEX_DEFS,
            mandatory_constraints=MANDATORY_CORE_TABLE_CONSTRAINTS,
            expected_fks=EXPECTED_CORE_FOREIGN_KEYS,
        )

    @classmethod
    def verify_cache_schema_integrity(cls, conn: sqlite3.Connection) -> bool:
        """
        Verifies that all required cache tables, exact column structures, default values,
        ordering, foreign-key inventories, indexes (uniqueness and partial status),
        and critical market-data validation constraints exist and match expected definitions.
        Fails closed with SchemaIntegrityError if any discrepancy is found.
        """
        return cls._verify_schema_structural_integrity(
            conn=conn,
            profile="Cache",
            expected_tables=EXPECTED_CACHE_TABLE_DEFS,
            expected_indexes=EXPECTED_CACHE_INDEX_DEFS,
            mandatory_constraints=MANDATORY_CACHE_TABLE_CONSTRAINTS,
            expected_fks=EXPECTED_CACHE_FOREIGN_KEYS,
        )

    @classmethod
    def initialize_core_schema(cls, conn: sqlite3.Connection) -> int:
        """
        Installs the core financial and governance schema transactionally.
        If already installed at CURRENT_SCHEMA_VERSION, verifies existing schema integrity
        before returning current version idempotently.
        If the database version is newer than CURRENT_SCHEMA_VERSION, raises SchemaVersionMismatchError.
        """
        existing_version = cls.get_current_version(conn)
        if existing_version is not None:
            if existing_version > CURRENT_SCHEMA_VERSION:
                raise SchemaVersionMismatchError(
                    f"Database schema version {existing_version} is newer than supported code version {CURRENT_SCHEMA_VERSION}."
                )
            if existing_version == CURRENT_SCHEMA_VERSION:
                cls.verify_core_schema_integrity(conn)
                return existing_version
            # Future migrations for existing_version < CURRENT_SCHEMA_VERSION will be handled here

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Execute DDL statements transactionally
        try:
            conn.execute("BEGIN IMMEDIATE;")
            for statement in CORE_SCHEMA_STATEMENTS:
                conn.execute(statement)

            conn.execute(
                """
                INSERT INTO database_metadata (
                    id, schema_version, db_revision, created_at_utc, last_updated_at_utc
                ) VALUES (1, ?, 0, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    schema_version = excluded.schema_version,
                    last_updated_at_utc = excluded.last_updated_at_utc;
                """,
                (CURRENT_SCHEMA_VERSION, now_iso, now_iso),
            )
            conn.execute("COMMIT;")
        except Exception as e:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            raise SchemaError(f"Failed to initialize core database schema: {e}") from e

        # Verify integrity of newly initialized schema
        cls.verify_core_schema_integrity(conn)
        return CURRENT_SCHEMA_VERSION

    @classmethod
    def initialize_cache_schema(cls, conn: sqlite3.Connection) -> int:
        """
        Installs the market-data cache schema transactionally.
        Rejects future schema versions and verifies integrity of existing schemas.
        """
        cursor = conn.cursor()
        cursor.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='cache_metadata';"
        )
        if cursor.fetchone()[0] > 0:
            cursor.execute("SELECT schema_version FROM cache_metadata WHERE id = 1;")
            row = cursor.fetchone()
            if row:
                existing_version = row[0]
                if existing_version > CURRENT_SCHEMA_VERSION:
                    raise SchemaVersionMismatchError(
                        f"Cache database schema version {existing_version} is newer than supported code version {CURRENT_SCHEMA_VERSION}."
                    )
                if existing_version == CURRENT_SCHEMA_VERSION:
                    cls.verify_cache_schema_integrity(conn)
                    return existing_version

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            for statement in CACHE_SCHEMA_STATEMENTS:
                conn.execute(statement)

            conn.execute(
                """
                INSERT INTO cache_metadata (id, schema_version, created_at_utc, last_updated_at_utc)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    schema_version = excluded.schema_version,
                    last_updated_at_utc = excluded.last_updated_at_utc;
                """,
                (CURRENT_SCHEMA_VERSION, now_iso, now_iso),
            )
            conn.execute("COMMIT;")
        except Exception as e:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            raise SchemaError(f"Failed to initialize cache database schema: {e}") from e

        cls.verify_cache_schema_integrity(conn)
        return CURRENT_SCHEMA_VERSION

    @staticmethod
    def get_table_names(conn: sqlite3.Connection) -> List[str]:
        """Returns sorted list of user table names in the database."""
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;"
        )
        return [row[0] for row in cursor.fetchall()]

    @staticmethod
    def get_index_names(conn: sqlite3.Connection) -> List[str]:
        """Returns sorted list of user index names in the database."""
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%' ORDER BY name;"
        )
        return [row[0] for row in cursor.fetchall()]

    @staticmethod
    def get_column_info(conn: sqlite3.Connection, table_name: str) -> List[Dict[str, Any]]:
        """
        Returns column metadata for table_name via PRAGMA table_info.
        Each entry contains cid, name, type, notnull, dflt_value, pk.
        """
        cursor = conn.cursor()
        cursor.execute(f"PRAGMA table_info({table_name});")
        columns = []
        for row in cursor.fetchall():
            columns.append({
                "cid": row[0],
                "name": row[1],
                "type": row[2].upper(),
                "notnull": bool(row[3]),
                "default_value": row[4],
                "pk": row[5],
            })
        return columns
