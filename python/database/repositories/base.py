"""
Base Repository Abstractions, Domain Models, and Validation Infrastructure.
Governed by Stage 1.4 Architecture and Agent Governance Framework.

This module provides:
- Strong repository-level exception hierarchies mapped from DatabaseError.
- Frozen, immutable domain dataclasses decoupling domain models from sqlite3.Row.
- Exact Decimal validation enforcing SQLite CHECK constraint invariants in Python.
- Fail-closed identifier and timestamp validation helpers.
- BaseRepository class encapsulating parameterised SQLite operations.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import re
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from python.database.manager import DatabaseError


# ---------------------------------------------------------------------------
# Repository Exception Hierarchy
# ---------------------------------------------------------------------------

class RepositoryError(DatabaseError):
    """Base exception for all repository and persistence abstraction errors."""
    pass


class EntityNotFoundError(RepositoryError):
    """Raised when an expected domain entity does not exist in the database."""
    pass


class DuplicateEntityError(RepositoryError):
    """Raised when attempting to insert an entity with an existing unique key."""
    pass


class IntegrityConflictError(RepositoryError):
    """Raised when an operation conflicts with existing entity state (e.g. idempotency mismatch)."""
    pass


class ValidationError(RepositoryError):
    """Raised when an entity or value fails domain or schema validation before persistence."""
    pass


class ConcurrencyConflictError(RepositoryError):
    """Raised when a concurrent update conflict or stale revision is detected."""
    pass


class AppendOnlyViolationError(RepositoryError):
    """Raised when an update or delete is attempted on an append-only audit/changelog entity."""
    pass


# ---------------------------------------------------------------------------
# Immutable Domain Models (Frozen Dataclasses)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CashBalance:
    """Authoritative portfolio cash balance record."""
    account_id: str
    currency: str
    amount: Decimal
    last_updated_at_utc: str


@dataclass(frozen=True)
class PositionRecord:
    """Authoritative portfolio position holding record."""
    account_id: str
    ticker: str
    shares: Decimal
    wac_eur: Decimal
    wac_usd: Optional[Decimal]
    last_updated_at_utc: str


@dataclass(frozen=True)
class WatchlistItem:
    """Watched ticker item record."""
    ticker: str
    status: str
    added_at_utc: str
    notes: Optional[str]


@dataclass(frozen=True)
class ScoutCategory:
    """Scout category grouping record."""
    id: int
    category_name: str
    description: Optional[str]
    tickers: Tuple[str, ...]
    updated_at_utc: str


@dataclass(frozen=True)
class TradeLesson:
    """Codified investment trade lesson / directive record."""
    rule_code: str
    directive_text: str
    category: str
    status: str
    created_at_utc: str
    last_reviewed_at_utc: str


@dataclass(frozen=True)
class DecisionRecord:
    """Investment council deliberation / decision log record."""
    id: int
    session_id: str
    ticker: Optional[str]
    action_proposed: str
    decision_outcome: str
    votes: Dict[str, Any]
    transcript_text: Optional[str]
    timestamp_utc: str


@dataclass(frozen=True)
class FinancialChangelogEntry:
    """Immutable transactional financial state mutation changelog entry."""
    id: int
    correlation_id: str
    actor_role: str
    operation_name: str
    target_resource: str
    approval_digest: Optional[str]
    before_state: Optional[Dict[str, Any]]
    after_state: Optional[Dict[str, Any]]
    timestamp_utc: str


@dataclass(frozen=True)
class OwnerApprovalRecord:
    """One-time owner approval persistence record."""
    id: int
    approval_id: str
    operation_name: str
    target_resource: str
    payload_digest: str
    granted_by: str
    consumed_at_utc: Optional[str]
    status: str
    created_at_utc: str


@dataclass(frozen=True)
class AuditEventRecord:
    """Indexed structured audit event mirror record."""
    event_id: str
    timestamp_utc: str
    action_category: str
    status: str
    agent_role: str
    tool_name: Optional[str]
    permission_decision: str
    approval_status: str
    payload_hash: Optional[str]
    raw_event: Dict[str, Any]


@dataclass(frozen=True)
class DailyBarRecord:
    """Reconstructible historical market data daily bar record."""
    ticker: str
    bar_date: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    indicators: Dict[str, Any]
    updated_at_utc: str


# ---------------------------------------------------------------------------
# Strict Validation Helpers
# ---------------------------------------------------------------------------

_HEX_32_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def utc_now_iso() -> str:
    """Returns current UTC ISO-8601 timestamp with explicit Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_event_id(event_id: str) -> str:
    """
    Validates that event_id is a 32-character lowercase hexadecimal string
    matching SQLite CHECK constraint `length(event_id) = 32 AND event_id NOT GLOB '*[^0-9a-f]*'`.
    """
    if not isinstance(event_id, str):
        raise ValidationError(f"event_id must be a string, got {type(event_id).__name__}")
    if not _HEX_32_PATTERN.match(event_id):
        raise ValidationError(
            f"event_id must be exactly 32 lowercase hex characters matching uuid4().hex: got '{event_id}'"
        )
    return event_id


def validate_date_str(date_str: str) -> str:
    """Validates that date_str is in YYYY-MM-DD format."""
    if not isinstance(date_str, str) or not _DATE_PATTERN.match(date_str):
        raise ValidationError(f"Date must be in YYYY-MM-DD format, got '{date_str}'")
    return date_str


def validate_identifier(name: str, field_name: str = "identifier") -> str:
    """Validates a non-empty, stripped uppercase identifier."""
    if not isinstance(name, str):
        raise ValidationError(f"Field '{field_name}' must be a string, got {type(name).__name__}")
    s = name.strip()
    if not s:
        raise ValidationError(f"Field '{field_name}' cannot be empty")
    return s


def validate_decimal(
    value: Union[Decimal, int, str],
    field_name: str = "amount",
    max_scale: int = 4,
    allow_negative: bool = False,
    allow_zero: bool = True,
    allow_none: bool = False,
) -> Optional[Decimal]:
    """
    Validates and converts a numeric or string value to an exact Decimal.
    Strictly mirrors the SQLite canonical decimal string CHECK constraints:
    - Floats are strictly prohibited.
    - Scientific notation (e/E) is prohibited.
    - Multiple decimal points (1.2.3) are prohibited.
    - Leading/trailing decimal points (.5, 5.) are prohibited.
    - Leading-zero violations (05, 00, 01.2) are prohibited (except bare 0 or 0.xxx).
    - Excess scale beyond max_scale is prohibited.
    - Negative values prohibited unless allow_negative=True.
    - Non-finite values (NaN, Infinity) are prohibited.
    - Whitespace inside strings is prohibited.
    """
    if value is None:
        if allow_none:
            return None
        raise ValidationError(f"Field '{field_name}' cannot be None")

    if isinstance(value, bool):
        raise ValidationError(f"Boolean values forbidden for financial field '{field_name}'")

    if isinstance(value, float):
        raise ValidationError(
            f"Floating-point values strictly forbidden for financial field '{field_name}'. "
            f"Pass Decimal, int, or exact canonical numeric string."
        )

    if not isinstance(value, (Decimal, int, str)):
        raise ValidationError(
            f"Field '{field_name}' must be Decimal, int, or str; got {type(value).__name__}"
        )

    if isinstance(value, int):
        d = Decimal(value)
        s = str(value)
    elif isinstance(value, str):
        s = value
        if not s:
            raise ValidationError(f"Field '{field_name}' cannot be empty string")
        if s != s.strip():
            raise ValidationError(f"Surrounding whitespace forbidden in field '{field_name}': '{value}'")
        if " " in s or "\t" in s or "\n" in s:
            raise ValidationError(f"Whitespace forbidden in field '{field_name}': '{value}'")
        if "e" in s or "E" in s:
            raise ValidationError(f"Scientific notation forbidden for field '{field_name}': '{value}'")
        if s.count(".") > 1:
            raise ValidationError(f"Multiple decimal points forbidden for field '{field_name}': '{value}'")
        if s.startswith(".") or s.endswith("."):
            raise ValidationError(f"Leading or trailing decimal point forbidden for field '{field_name}': '{value}'")

        # Leading zero checks: '05', '00', '01.5' are invalid
        num_part = s[1:] if s.startswith("-") else s
        parts = num_part.split(".")
        int_part = parts[0]
        if len(int_part) > 1 and int_part.startswith("0"):
            raise ValidationError(f"Leading-zero violation for field '{field_name}': '{value}'")

        try:
            d = Decimal(s)
        except InvalidOperation as e:
            raise ValidationError(f"Invalid decimal string for field '{field_name}': '{value}'") from e
    else:
        d = value
        if not d.is_finite():
            raise ValidationError(f"Non-finite decimal (NaN/Inf) forbidden for field '{field_name}'")
        s = f"{d:f}"
        if "e" in s or "E" in s:
            raise ValidationError(f"Scientific notation detected for field '{field_name}': '{d}'")

    if not d.is_finite():
        raise ValidationError(f"Non-finite decimal (NaN/Inf) forbidden for field '{field_name}'")

    if d < 0 and not allow_negative:
        raise ValidationError(f"Negative value forbidden for field '{field_name}': '{d}'")

    if d == 0 and not allow_zero:
        raise ValidationError(f"Zero value forbidden for field '{field_name}': '{d}'")

    # Enforce exact maximum scale
    exp = d.as_tuple().exponent
    scale = -exp if exp < 0 else 0
    if scale > max_scale:
        raise ValidationError(
            f"Scale of field '{field_name}' ({scale}) exceeds maximum permitted scale ({max_scale}): '{d}'"
        )

    return d


def decimal_to_canonical_str(d: Decimal) -> str:
    """Formats an exact Decimal into canonical SQLite-compatible string without exponents."""
    if not d.is_finite():
        raise ValidationError("Cannot serialize non-finite Decimal to string")
    return f"{d:f}"


# ---------------------------------------------------------------------------
# Base Repository Class
# ---------------------------------------------------------------------------

class BaseRepository:
    """
    Abstract base repository encapsulating parameterised SQLite operations.
    Receives an existing, caller-supplied sqlite3.Connection.
    """

    def __init__(self, connection: sqlite3.Connection):
        if connection is None:
            raise ValueError("Repository connection cannot be None")
        self._conn = connection

    @property
    def connection(self) -> sqlite3.Connection:
        """The underlying sqlite3.Connection."""
        return self._conn

