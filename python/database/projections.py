"""
SSoT Projection Generator and Deterministic JSON Serialization Infrastructure.
Governed by Stage 1.5-C Architecture and Agent Governance Framework.

This module provides:
- SSoTProjectionGenerator: Deterministic, read-only projection from authoritative
  SQLite persistence layer to GEM's canonical Single Source of Truth (SSoT) JSON document.
- Snapshot Isolation: Coherent read transaction capturing db_revision and all domain records
  at one point-in-time snapshot under SQLite WAL mode without taking ownership of caller transactions.
- Exact Decimal Serialization: Financial quantities serialized via canonical string representation
  without binary floating-point conversion.
- Non-Tabular Preservation: Explicit preservation policy for externally managed and non-tabular
  sections from a supplied base document without silent data loss or recursive dictionary pollution.
- Atomic File Replacement: Safe, fsync-hardened atomic file replacement on explicit destination paths
  with temporary file cleanup and fail-closed error reporting.
"""

import copy
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from python.database.repositories.base import (
    decimal_to_canonical_str,
    get_db_revision,
    validate_timestamp_utc,
    RepositoryError,
    ValidationError,
)
from python.database.repositories.financial import (
    CashRepository,
    PositionRepository,
    WatchlistRepository,
)
from python.database.repositories.governance import (
    ScoutCategoryRepository,
    TradeLessonRepository,
    DecisionLogRepository,
)

__all__ = [
    "PROJECTION_FORMAT_VERSION",
    "SQLITE_OWNED_TOP_LEVEL_KEYS",
    "SQLITE_OWNED_MUTABLE_STATE_KEYS",
    "normalize_projection_value",
    "serialize_projection",
    "atomic_write_json",
    "SSoTProjectionGenerator",
]

PROJECTION_FORMAT_VERSION = "1.0"

# Explicit domain boundaries: Top-level keys owned strictly by SQLite projection
SQLITE_OWNED_TOP_LEVEL_KEYS = frozenset({
    "_projection_metadata",
    "portfolio_snapshot",
    "unallocated_cash_eur",
    "unallocated_cash_usd",
    "portfolio_cash",
    "watched_tickers",
    "watchlist",
    "scout_categories",
    "scout_category_records",
    "trade_lessons",
    "decision_log",
    "mutable_state",
})

# Sub-keys within mutable_state owned strictly by SQLite projection
SQLITE_OWNED_MUTABLE_STATE_KEYS = frozenset({
    "portfolio_snapshot",
    "unallocated_cash_eur",
    "unallocated_cash_usd",
    "watched_tickers",
    "scout_categories",
})


def normalize_projection_value(value: Any, allow_floats: bool = False) -> Any:
    """
    Recursively normalizes Python objects into deterministic JSON-serializable structures.
    - Exact Decimal values are converted to canonical SQLite strings (decimal_to_canonical_str).
    - Dictionaries have their keys stringified and values recursively normalized.
    - Lists and tuples are recursively normalized into lists.
    - Primitives (None, str, int, bool) are preserved unchanged.
    - Floats are strictly prohibited by default (allow_floats=False) to enforce financial Decimal integrity.
      When allow_floats=True is passed, finite floats are accepted; non-standard NaN/Infinity are rejected.
    - Unsupported types raise TypeError fail-closed.
    """
    if isinstance(value, Decimal):
        return decimal_to_canonical_str(value)

    if isinstance(value, dict):
        return {
            str(key): normalize_projection_value(item, allow_floats=allow_floats)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [
            normalize_projection_value(item, allow_floats=allow_floats)
            for item in value
        ]

    # Explicit handling for scalar primitives
    if value is None or isinstance(value, (str, int, bool)):
        return value

    if isinstance(value, float):
        if not allow_floats:
            raise TypeError(
                f"Floating-point values strictly forbidden in projection: {value!r}. "
                f"Use Decimal or exact canonical string."
            )
        if not math.isfinite(value):
            raise ValueError(f"Non-standard JSON float value forbidden (NaN/Infinity): {value!r}")
        return value

    raise TypeError(
        f"Unsupported projection value type: {type(value).__name__} (value: {value!r})"
    )


def serialize_projection(projection: Dict[str, Any], allow_floats: bool = False) -> str:
    """
    Serializes a projection dictionary to a deterministic, canonical UTF-8 JSON string.
    Enforces:
    - ensure_ascii=False for native UTF-8 representations.
    - sort_keys=True for deterministic key ordering.
    - separators=(',', ':') for canonical whitespace-free output.
    - allow_nan=False to strictly reject non-standard JSON numeric tokens.
    """
    if not isinstance(projection, dict):
        raise ValidationError(f"Projection must be a dict, got {type(projection).__name__}")

    normalized = normalize_projection_value(projection, allow_floats=allow_floats)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def atomic_write_json(destination: Union[Path, str], serialized_json: str) -> None:
    """
    Atomically writes serialized JSON string to an explicitly supplied destination path.
    1. Validates destination path and pre-serialized content.
    2. Ensures parent directory exists.
    3. Writes to a hidden temporary file in the destination directory (ensuring same filesystem).
    4. Explicitly flushes and fsyncs the temporary file handle.
    5. Closes file handle prior to replacement (Windows-safe).
    6. Replaces the destination file atomically via os.replace.
    7. Cleans up temporary file in finally block upon any write failure.
    """
    if not isinstance(serialized_json, str):
        raise ValidationError(f"serialized_json must be a str, got {type(serialized_json).__name__}")

    dest = Path(destination)
    if dest.exists() and dest.is_dir():
        raise IsADirectoryError(f"Destination path is an existing directory: {dest}")

    parent_dir = dest.parent
    if not parent_dir.exists():
        parent_dir.mkdir(parents=True, exist_ok=True)

    temp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=parent_dir,
            prefix=f".{dest.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(serialized_json)
            handle.flush()
            os.fsync(handle.fileno())

        # Atomic replacement across filesystem boundary
        os.replace(temp_path, dest)
        temp_path = None
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


class SSoTProjectionGenerator:
    """
    Deterministic, read-only SQLite-to-JSON projection generator for GEM's canonical SSoT document.
    Governed by Stage 1.5-C Architecture.
    """

    def __init__(self, db_manager: Optional[Any] = None):
        self._db_manager = db_manager

    def generate(
        self,
        conn: sqlite3.Connection,
        base_document: Optional[Dict[str, Any]] = None,
        generation_timestamp: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Produces a canonical JSON-compatible representation of the authoritative SQLite state.

        Guarantees:
        - Consistent Snapshot: Read transaction (BEGIN;) used when connection has no active transaction;
          rolled back on exit so no persistent locks or transaction leaks occur.
        - Non-Intrusive: If connection is already within an active transaction, reads within that transaction
          without committing or rolling back caller work.
        - Read-Only: Never executes INSERT, UPDATE, DELETE, or PRAGMA statements.
        - Deterministic: Output for unchanged database state and base_document is byte-for-byte identical.
        - Provenance: Includes db_revision captured from database_metadata within the same snapshot.
        - Non-Tabular Preservation: Preserves externally managed sections from base_document exactly.
        """
        if conn is None:
            raise ValidationError("Database connection cannot be None")
        if not hasattr(conn, "cursor"):
            raise ValidationError(f"Invalid database connection object: {type(conn).__name__}")

        # Validate base_document structure fail-closed
        if base_document is not None:
            if not isinstance(base_document, dict):
                raise ValidationError(
                    f"base_document must be a dict if provided, got {type(base_document).__name__}"
                )
            if "mutable_state" in base_document and not isinstance(base_document["mutable_state"], dict):
                raise ValidationError(
                    f"base_document['mutable_state'] must be a dict if provided, "
                    f"got {type(base_document['mutable_state']).__name__}"
                )

        if generation_timestamp is not None:
            generation_timestamp = validate_timestamp_utc(generation_timestamp)

        # Inspect connection transaction state
        try:
            caller_in_tx = bool(conn.in_transaction)
        except Exception as e:
            raise RepositoryError(f"Cannot inspect connection transaction state: {e}") from e

        we_started_tx = False
        if not caller_in_tx:
            try:
                conn.execute("BEGIN;")
                we_started_tx = True
            except (sqlite3.OperationalError, sqlite3.ProgrammingError) as e:
                raise RepositoryError(f"Failed to begin read snapshot transaction: {e}") from e

        try:
            # 1. Snapshot Provenance & Metadata
            cur = conn.cursor()
            cur.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='database_metadata';"
            )
            has_meta = cur.fetchone()[0] > 0
            schema_version = 1
            db_revision = 0
            db_last_updated = None
            if has_meta:
                cur.execute(
                    "SELECT schema_version, db_revision, last_updated_at_utc FROM database_metadata WHERE id = 1;"
                )
                meta_row = cur.fetchone()
                if meta_row:
                    schema_version = int(meta_row[0])
                    db_revision = int(meta_row[1])
                    db_last_updated = meta_row[2]

            # 2. Portfolio Cash Domain
            cash_repo = CashRepository(conn)
            cash_balances = cash_repo.list_cash()
            primary_cash_eur = "0.00"
            primary_cash_usd = "0.00"
            for c in cash_balances:
                if c.account_id == "PRIMARY":
                    if c.currency == "EUR":
                        primary_cash_eur = decimal_to_canonical_str(c.amount)
                    elif c.currency == "USD":
                        primary_cash_usd = decimal_to_canonical_str(c.amount)

            portfolio_cash_records = [
                {
                    "account_id": c.account_id,
                    "amount": decimal_to_canonical_str(c.amount),
                    "currency": c.currency,
                    "last_updated_at_utc": c.last_updated_at_utc,
                }
                for c in sorted(cash_balances, key=lambda x: (x.account_id, x.currency))
            ]

            # 3. Portfolio Positions Domain
            pos_repo = PositionRepository(conn)
            positions = pos_repo.list_positions()
            portfolio_snapshot = [
                {
                    "account_id": p.account_id,
                    "last_updated_at_utc": p.last_updated_at_utc,
                    "shares": decimal_to_canonical_str(p.shares),
                    "ticker": p.ticker,
                    "wac": decimal_to_canonical_str(p.wac_eur),
                    "wac_eur": decimal_to_canonical_str(p.wac_eur),
                    "wac_usd": decimal_to_canonical_str(p.wac_usd) if p.wac_usd is not None else None,
                }
                for p in sorted(positions, key=lambda x: (x.account_id, x.ticker))
            ]

            # 4. Watchlist Domain
            wl_repo = WatchlistRepository(conn)
            wl_items = wl_repo.list_watchlist()
            watched_tickers = sorted(
                [item.ticker for item in wl_items if item.status == "ACTIVE"]
            )
            watchlist_records = [
                {
                    "added_at_utc": item.added_at_utc,
                    "notes": item.notes,
                    "status": item.status,
                    "ticker": item.ticker,
                }
                for item in sorted(wl_items, key=lambda x: x.ticker)
            ]

            # 5. Scout Categories Domain
            scout_repo = ScoutCategoryRepository(conn)
            scout_cats = scout_repo.list_categories()
            scout_category_names = sorted([c.category_name for c in scout_cats])
            scout_category_records = [
                {
                    "category_name": c.category_name,
                    "description": c.description,
                    "id": c.id,
                    "tickers": sorted(list(c.tickers)),
                    "updated_at_utc": c.updated_at_utc,
                }
                for c in sorted(scout_cats, key=lambda x: x.category_name)
            ]

            # 6. Trade Lessons Domain
            lesson_repo = TradeLessonRepository(conn)
            lessons = lesson_repo.list_lessons()
            trade_lessons_records = [
                {
                    "category": l.category,
                    "created_at_utc": l.created_at_utc,
                    "directive_text": l.directive_text,
                    "last_reviewed_at_utc": l.last_reviewed_at_utc,
                    "rule_code": l.rule_code,
                    "status": l.status,
                }
                for l in sorted(lessons, key=lambda x: x.rule_code)
            ]

            # 7. Decision Log Domain (Chronological primary-key order)
            decision_repo = DecisionLogRepository(conn)
            decisions = decision_repo.list_decisions()
            decision_log_records = [
                {
                    "action_proposed": d.action_proposed,
                    "decision_outcome": d.decision_outcome,
                    "id": d.id,
                    "session_id": d.session_id,
                    "ticker": d.ticker,
                    "timestamp_utc": d.timestamp_utc,
                    "transcript_text": d.transcript_text,
                    "votes": d.votes,
                }
                for d in sorted(decisions, key=lambda x: x.id)
            ]

        finally:
            if we_started_tx:
                try:
                    conn.execute("ROLLBACK;")
                except Exception:
                    pass

        # -------------------------------------------------------------------
        # Build Canonical In-Memory Projection with Non-Tabular Preservation
        # -------------------------------------------------------------------
        projection: Dict[str, Any] = {}

        # 1. Preserve top-level non-tabular / externally managed sections from base_document
        if base_document is not None:
            for key, value in base_document.items():
                if key not in SQLITE_OWNED_TOP_LEVEL_KEYS:
                    projection[key] = copy.deepcopy(value)

        # 2. Add SQLite-owned top-level sections
        projection_meta: Dict[str, Any] = {
            "authority": "PROJECTED_READ_ONLY",
            "db_last_updated_at_utc": db_last_updated,
            "db_revision": db_revision,
            "projection_format_version": PROJECTION_FORMAT_VERSION,
            "schema_version": schema_version,
            "source_database": "context/gem.db",
            "status": "HEALTHY",
        }
        if generation_timestamp is not None:
            projection_meta["generated_at_utc"] = generation_timestamp
        projection["_projection_metadata"] = projection_meta

        projection["portfolio_snapshot"] = portfolio_snapshot
        projection["unallocated_cash_eur"] = primary_cash_eur
        projection["unallocated_cash_usd"] = primary_cash_usd
        projection["portfolio_cash"] = portfolio_cash_records
        projection["watched_tickers"] = watched_tickers
        projection["watchlist"] = watchlist_records
        projection["scout_categories"] = scout_category_names
        projection["scout_category_records"] = scout_category_records
        projection["trade_lessons"] = trade_lessons_records
        projection["decision_log"] = decision_log_records

        # 3. Assemble mutable_state
        mutable_state: Dict[str, Any] = {}
        if base_document is not None and "mutable_state" in base_document and isinstance(base_document["mutable_state"], dict):
            for mk, mv in base_document["mutable_state"].items():
                if mk not in SQLITE_OWNED_MUTABLE_STATE_KEYS:
                    mutable_state[mk] = copy.deepcopy(mv)

        mutable_state["portfolio_snapshot"] = portfolio_snapshot
        mutable_state["unallocated_cash_eur"] = primary_cash_eur
        mutable_state["unallocated_cash_usd"] = primary_cash_usd
        mutable_state["watched_tickers"] = watched_tickers
        mutable_state["scout_categories"] = scout_category_names

        projection["mutable_state"] = mutable_state
        return projection

    def serialize(self, projection: Dict[str, Any], allow_floats: bool = False) -> str:
        """Serializes a projection dictionary to deterministic canonical JSON."""
        return serialize_projection(projection, allow_floats=allow_floats)

    def write_atomic(
        self,
        projection: Dict[str, Any],
        destination: Union[Path, str],
        allow_floats: bool = False,
    ) -> None:
        """Serializes and atomically writes projection to destination path."""
        serialized = self.serialize(projection, allow_floats=allow_floats)
        atomic_write_json(destination, serialized)

    def project_to_file(
        self,
        conn: sqlite3.Connection,
        destination: Union[Path, str],
        base_document: Optional[Dict[str, Any]] = None,
        generation_timestamp: Optional[str] = None,
        allow_floats: bool = False,
    ) -> Dict[str, Any]:
        """Generates projection from connection and writes it atomically to destination file."""
        projection = self.generate(
            conn,
            base_document=base_document,
            generation_timestamp=generation_timestamp,
        )
        self.write_atomic(projection, destination, allow_floats=allow_floats)
        return projection

    def project_trade_lessons(self, conn: sqlite3.Connection) -> Dict[str, Any]:
        """Generates an isolated trade_lessons projection matching context/trade_lessons.json shape."""
        repo = TradeLessonRepository(conn)
        lessons = repo.list_lessons()
        return {
            "trade_lessons": [
                {
                    "category": l.category,
                    "created_at_utc": l.created_at_utc,
                    "directive_text": l.directive_text,
                    "last_reviewed_at_utc": l.last_reviewed_at_utc,
                    "rule_code": l.rule_code,
                    "status": l.status,
                }
                for l in sorted(lessons, key=lambda x: x.rule_code)
            ]
        }

    def project_decision_log(self, conn: sqlite3.Connection) -> Dict[str, Any]:
        """Generates an isolated decision_log projection matching context/decision_log.json shape."""
        repo = DecisionLogRepository(conn)
        decisions = repo.list_decisions()
        return {
            "decision_log": [
                {
                    "action_proposed": d.action_proposed,
                    "decision_outcome": d.decision_outcome,
                    "id": d.id,
                    "session_id": d.session_id,
                    "ticker": d.ticker,
                    "timestamp_utc": d.timestamp_utc,
                    "transcript_text": d.transcript_text,
                    "votes": d.votes,
                }
                for d in sorted(decisions, key=lambda x: x.id)
            ]
        }

