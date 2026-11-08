"""
Database Schema Definitions and Versioned Migration Infrastructure
Governed by Stage 1.3 Directive and Stage 1.2.1 / 1.3.1 Architecture Corrections.

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
    """Raised when an existing database is missing required tables, indexes, or constraints."""
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
    # Digits and at most one period, no leading/trailing dot, no non-canonical leading zeros,
    # strictly non-negative, non-empty, no exponents or whitespace.
    """
    CREATE TABLE IF NOT EXISTS portfolio_cash (
        account_id TEXT NOT NULL DEFAULT 'PRIMARY',
        currency TEXT NOT NULL CHECK (currency IN ('EUR', 'USD')),
        amount TEXT NOT NULL CHECK (
            amount NOT GLOB '*[^0-9.]*'
            AND amount NOT LIKE '%.%.*'
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
            AND shares NOT LIKE '%.%.*'
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
            AND wac_eur NOT LIKE '%.%.*'
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
                AND wac_usd NOT LIKE '%.%.*'
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
            AND open NOT LIKE '%.%.*'
            AND open NOT GLOB '.*'
            AND open NOT GLOB '*.'
            AND open NOT GLOB '0[0-9]*'
            AND length(open) > 0
        ),
        high TEXT NOT NULL CHECK (
            high NOT GLOB '*[^0-9.]*'
            AND high NOT LIKE '%.%.*'
            AND high NOT GLOB '.*'
            AND high NOT GLOB '*.'
            AND high NOT GLOB '0[0-9]*'
            AND length(high) > 0
        ),
        low TEXT NOT NULL CHECK (
            low NOT GLOB '*[^0-9.]*'
            AND low NOT LIKE '%.%.*'
            AND low NOT GLOB '.*'
            AND low NOT GLOB '*.'
            AND low NOT GLOB '0[0-9]*'
            AND length(low) > 0
        ),
        close TEXT NOT NULL CHECK (
            close NOT GLOB '*[^0-9.]*'
            AND close NOT LIKE '%.%.*'
            AND close NOT GLOB '.*'
            AND close NOT GLOB '*.'
            AND close NOT GLOB '0[0-9]*'
            AND length(close) > 0
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


# Expected schema structures for integrity verification
EXPECTED_CORE_TABLES: Set[str] = {
    "database_metadata",
    "portfolio_cash",
    "portfolio_positions",
    "watchlist",
    "scout_categories",
    "trade_lessons",
    "decision_log",
    "financial_changelog",
    "owner_approvals",
    "audit_events",
}

EXPECTED_CORE_INDEXES: Set[str] = {
    "idx_audit_timestamp",
    "idx_audit_action",
    "idx_changelog_corr",
    "idx_decision_session",
    "idx_decision_ticker",
}

EXPECTED_CACHE_TABLES: Set[str] = {
    "cache_metadata",
    "daily_bars",
}

EXPECTED_CACHE_INDEXES: Set[str] = {
    "idx_bars_ticker",
    "idx_bars_date",
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
    def verify_core_schema_integrity(cls, conn: sqlite3.Connection) -> bool:
        """
        Verifies that all required core tables and indexes exist.
        Fails closed with SchemaIntegrityError if any expected object is missing.
        """
        existing_tables = set(cls.get_table_names(conn))
        missing_tables = EXPECTED_CORE_TABLES - existing_tables
        if missing_tables:
            raise SchemaIntegrityError(
                f"Core schema integrity failure: missing required table(s): {sorted(missing_tables)}"
            )

        existing_indexes = set(cls.get_index_names(conn))
        missing_indexes = EXPECTED_CORE_INDEXES - existing_indexes
        if missing_indexes:
            raise SchemaIntegrityError(
                f"Core schema integrity failure: missing required index(es): {sorted(missing_indexes)}"
            )

        return True

    @classmethod
    def verify_cache_schema_integrity(cls, conn: sqlite3.Connection) -> bool:
        """
        Verifies that all required cache tables and indexes exist.
        Fails closed with SchemaIntegrityError if any expected object is missing.
        """
        existing_tables = set(cls.get_table_names(conn))
        missing_tables = EXPECTED_CACHE_TABLES - existing_tables
        if missing_tables:
            raise SchemaIntegrityError(
                f"Cache schema integrity failure: missing required table(s): {sorted(missing_tables)}"
            )

        existing_indexes = set(cls.get_index_names(conn))
        missing_indexes = EXPECTED_CACHE_INDEXES - existing_indexes
        if missing_indexes:
            raise SchemaIntegrityError(
                f"Cache schema integrity failure: missing required index(es): {sorted(missing_indexes)}"
            )

        return True

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
