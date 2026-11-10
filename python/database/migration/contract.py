"""
Explicit Versioned Migration Contract and Validation Infrastructure.
Governed by Stage 1.5-D Architecture and Agent Governance Framework.
"""

from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from python.database.manager import DatabaseManager
from python.database.repositories.base import (
    validate_timestamp_utc,
    decimal_to_canonical_str,
    ValidationError,
)
from python.database.migration.models import (
    MigrationSafetyError,
    MigrationValidationError,
)

__all__ = [
    "MIGRATION_CONTRACT_VERSION",
    "SQLITE_OWNED_DOMAINS",
    "EXTERNALLY_MANAGED_SECTIONS",
    "parse_financial_decimal",
    "parse_ticker",
    "parse_iso_timestamp",
    "validate_migration_destination",
    "extract_rule_code_and_directive",
]

MIGRATION_CONTRACT_VERSION = "1.0"

SQLITE_OWNED_DOMAINS = frozenset({
    "portfolio_cash",
    "portfolio_positions",
    "watchlist",
    "scout_categories",
    "trade_lessons",
    "decision_log",
})

EXTERNALLY_MANAGED_SECTIONS = frozenset({
    "state_context",
    "runtime_flags",
    "macro_calendar_shield",
    "forensic_intelligence",
    "custom_metadata",
    "execution_payload",
})


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-standard JSON constant is forbidden: {value}")


def parse_financial_decimal(
    value: Any,
    field_name: str,
    max_scale: int = 4,
    allow_zero: bool = True,
    allow_negative: bool = False,
) -> Decimal:
    """
    Parses and validates a financial quantity into an exact Decimal.
    Strictly forbids binary floating-point numbers (float) to prevent precision decay.
    """
    if isinstance(value, bool) or isinstance(value, float):
        raise MigrationValidationError(
            f"{field_name}: binary floating-point input is prohibited; received {type(value).__name__}"
        )

    if not isinstance(value, (str, int, Decimal)):
        raise MigrationValidationError(
            f"{field_name}: unsupported financial value type '{type(value).__name__}' (value: {value!r})"
        )

    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MigrationValidationError(
            f"{field_name}: invalid Decimal representation '{value}': {exc}"
        ) from exc

    if not dec.is_finite():
        raise MigrationValidationError(
            f"{field_name}: non-standard JSON float value forbidden (NaN/Infinity): {value!r}"
        )

    if not allow_negative and dec < 0:
        raise MigrationValidationError(
            f"{field_name}: negative amounts are forbidden, got {dec}"
        )

    if not allow_zero and dec == 0:
        raise MigrationValidationError(
            f"{field_name}: zero amount is forbidden"
        )

    # Check decimal scale
    sign, digits, exponent = dec.as_tuple()
    if isinstance(exponent, int) and exponent < 0:
        scale = -exponent
        if scale > max_scale:
            raise MigrationValidationError(
                f"{field_name}: scale {scale} exceeds maximum permitted scale {max_scale} (value: {dec})"
            )

    return dec


def parse_ticker(value: Any, field_name: str = "ticker") -> str:
    """Validates an authoritative asset ticker symbol."""
    if not isinstance(value, str):
        raise MigrationValidationError(
            f"{field_name}: ticker must be a string, got {type(value).__name__}"
        )
    s = value.strip().upper()
    if not s:
        raise MigrationValidationError(f"{field_name}: ticker cannot be empty")
    if not re.match(r"^[A-Z0-9.\^=-]{1,16}$", s):
        raise MigrationValidationError(f"{field_name}: invalid ticker format '{s}'")
    return s


def parse_iso_timestamp(value: Any, field_name: str = "timestamp") -> str:
    """Validates a canonical UTC ISO-8601 timestamp string ('Z' or '+00:00')."""
    if not isinstance(value, str):
        raise MigrationValidationError(
            f"{field_name}: timestamp must be a string, got {type(value).__name__}"
        )
    try:
        return validate_timestamp_utc(value)
    except ValidationError as e:
        raise MigrationValidationError(f"{field_name}: invalid UTC timestamp: {e}") from e


def validate_migration_destination(
    destination: Union[Path, str, Any],
    repository_root: Optional[Union[Path, str]] = None,
) -> Path:
    """
    Validates that a destination database path is strictly isolated and disposable.
    Fails closed if the path matches or resides within protected production database locations.
    """
    if hasattr(destination, "db_path"):
        # DatabaseManager instance
        dest_path = Path(destination.db_path)
    elif hasattr(destination, "cursor"):
        # sqlite3.Connection or ManagedConnection
        cur = destination.cursor()
        cur.execute("PRAGMA database_list;")
        rows = cur.fetchall()
        main_db = next((r[2] for r in rows if r[1] == "main"), "")
        if not main_db or main_db == ":memory:":
            return Path(":memory:")
        dest_path = Path(main_db)
    else:
        dest_path = Path(destination)

    if str(dest_path) == ":memory:":
        return dest_path

    resolved_dest = dest_path.resolve()

    if repository_root is None:
        # Repository root inferred relative to this file: python/database/migration/contract.py -> 4 levels up
        repo_root = Path(__file__).resolve().parent.parent.parent.parent
    else:
        repo_root = Path(repository_root).resolve()

    protected_targets = {
        (repo_root / "context" / "gem.db").resolve(),
        (repo_root / "cache" / "market_data.db").resolve(),
    }

    if resolved_dest in protected_targets:
        raise MigrationSafetyError(
            f"Operation refused: destination path is a protected production database: {resolved_dest}"
        )

    # Directory containment safety: prevent placing gem.db directly in context/ or market_data.db in cache/
    context_dir = (repo_root / "context").resolve()
    if resolved_dest.parent == context_dir and resolved_dest.name.lower() == "gem.db":
        raise MigrationSafetyError(
            f"Operation refused: destination database cannot be created in production context dir: {resolved_dest}"
        )

    cache_dir = (repo_root / "cache").resolve()
    if resolved_dest.parent == cache_dir and resolved_dest.name.lower() == "market_data.db":
        raise MigrationSafetyError(
            f"Operation refused: destination database cannot be created in production cache dir: {resolved_dest}"
        )

    return resolved_dest


def extract_rule_code_and_directive(rule_text: str) -> Tuple[Optional[str], str]:
    """
    Extracts rule_code (e.g. 'L-219') and directive text from legacy rule strings.
    If no rule code is extractable, returns (None, rule_text).
    """
    match = re.match(r"^(L-\d+):\s*(.*)$", rule_text.strip(), flags=re.DOTALL)
    if match:
        return match.group(1).upper(), match.group(2).strip()
    return None, rule_text.strip()

