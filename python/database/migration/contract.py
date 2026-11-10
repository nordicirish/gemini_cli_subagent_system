"""
Explicit Versioned Migration Contract and Validation Infrastructure.
Governed by Stage 1.5-D Architecture and Agent Governance Framework.
"""

from decimal import Decimal, InvalidOperation
import hashlib
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
    "resolve_historical_timestamp",
    "load_strict_json",
    "load_strict_json_file",
    "validate_migration_destination",
    "extract_rule_code_and_directive",
    "_validate_json_dict_keys",
    "_reject_json_constant",
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


def _validate_json_dict_keys(data: Any, path: str = "root") -> None:
    """Recursively verifies that all dictionary keys in data are strings."""
    if isinstance(data, dict):
        for k, v in data.items():
            if not isinstance(k, str):
                raise MigrationValidationError(
                    f"{path}: Dictionary keys must be strings; received {type(k).__name__}"
                )
            _validate_json_dict_keys(v, f"{path}.{k}")
    elif isinstance(data, (list, tuple)):
        for idx, item in enumerate(data):
            _validate_json_dict_keys(item, f"{path}[{idx}]")


def load_strict_json(raw_text: str, source_name: str = "json_document") -> Any:
    """
    Strictly parses a JSON string:
    - Rejects non-standard constants (NaN, Infinity, -Infinity).
    - Decodes numbers as exact Decimal to prevent binary float precision loss.
    - Validates that dictionary keys are strings recursively.
    """
    if not isinstance(raw_text, str):
        raise MigrationValidationError(f"{source_name} must be a string, got {type(raw_text).__name__}")
    if not raw_text.strip():
        raise MigrationValidationError(f"{source_name} cannot be empty")

    try:
        data = json.loads(
            raw_text,
            parse_float=Decimal,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise MigrationValidationError(f"{source_name} is not valid strict JSON: {exc}") from exc

    _validate_json_dict_keys(data, path=source_name)
    return data


def load_strict_json_file(
    file_path: Union[Path, str],
    repository_root: Optional[Union[Path, str]] = None,
) -> Tuple[Any, str, Path]:
    """
    Reads and strictly parses a JSON file from disk into native Python/Decimal objects (D-05).
    Returns (parsed_data, sha256_hex, resolved_path).
    """
    p = Path(file_path)
    if not p.is_absolute() and repository_root is not None:
        p = (Path(repository_root) / p).resolve()
    else:
        p = p.resolve()

    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"Legacy source file does not exist: {p}")

    raw_bytes = p.read_bytes()
    sha256 = hashlib.sha256(raw_bytes).hexdigest()

    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MigrationValidationError(f"Source file {p.name} is not valid UTF-8: {exc}") from exc

    data = load_strict_json(raw_text, source_name=p.name)

    if not isinstance(data, dict):
        raise MigrationValidationError(
            f"Source file {p.name} root must be a JSON object (dict), got {type(data).__name__}"
        )

    return data, sha256, p


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


def resolve_historical_timestamp(
    source_value: Any,
    field_name: str,
    *,
    nullable: bool = False,
    default_timestamp: Optional[str] = None,
) -> Optional[str]:
    """
    Resolves historical timestamp without implicit current-time fabrication (D-01).
    - If source_value is present, parses and validates as ISO-8601 UTC.
    - If missing: uses explicit default_timestamp if provided.
    - If missing and nullable=True, returns None.
    - Otherwise raises MigrationValidationError fail-closed.
    """
    if source_value is not None and str(source_value).strip() != "":
        return parse_iso_timestamp(source_value, field_name)

    if default_timestamp is not None and str(default_timestamp).strip() != "":
        return parse_iso_timestamp(default_timestamp, f"{field_name} (default)")

    if nullable:
        return None

    raise MigrationValidationError(
        f"{field_name}: historical timestamp is missing; migration cannot fabricate provenance"
    )


def validate_migration_destination(
    destination: Union[Path, str, Any],
    repository_root: Optional[Union[Path, str]] = None,
) -> Path:
    """
    Validates that a destination database path is strictly isolated and disposable (D-07).
    Fails closed if the path matches or resolves into protected production database locations
    via relative paths, absolute paths, traversal (..), symlinks, or case variations.
    """
    if destination is None:
        raise MigrationSafetyError("Destination database cannot be None")

    if hasattr(destination, "db_path"):
        dest_str = str(destination.db_path)
    elif hasattr(destination, "cursor"):
        cur = destination.cursor()
        cur.execute("PRAGMA database_list;")
        rows = cur.fetchall()
        main_db = next((r[2] for r in rows if r[1] == "main"), "")
        if not main_db or main_db == ":memory:":
            return Path(":memory:")
        dest_str = str(main_db)
    elif isinstance(destination, (str, Path)):
        dest_str = str(destination)
    else:
        raise MigrationSafetyError(f"Unsupported destination type: {type(destination).__name__}")

    if dest_str == ":memory:":
        return Path(":memory:")

    dest_path = Path(dest_str)

    if repository_root is None:
        repo_root = Path(__file__).resolve().parent.parent.parent.parent
    else:
        repo_root = Path(repository_root).resolve()

    # Canonicalize paths using both realpath (resolving symlinks and ..) and normcase (handling case-insensitivity)
    dest_real = Path(os.path.realpath(dest_path))
    dest_norm = os.path.normcase(str(dest_real))

    # Also resolve destination relative to repo_root if it's relative
    if not dest_path.is_absolute():
        dest_rel_to_repo = (repo_root / dest_path).resolve()
        dest_rel_norm = os.path.normcase(os.path.realpath(dest_rel_to_repo))
    else:
        dest_rel_norm = dest_norm

    prod_gem_real = os.path.normcase(os.path.realpath(repo_root / "context" / "gem.db"))
    prod_market_real = os.path.normcase(os.path.realpath(repo_root / "cache" / "market_data.db"))

    context_dir_real = os.path.normcase(os.path.realpath(repo_root / "context"))
    cache_dir_real = os.path.normcase(os.path.realpath(repo_root / "cache"))

    # Direct resolution check against protected targets
    if dest_norm in (prod_gem_real, prod_market_real) or dest_rel_norm in (prod_gem_real, prod_market_real):
        raise MigrationSafetyError(
            f"Operation refused: destination path resolves to protected production database: {dest_path}"
        )

    # Containment check: prevent creating gem.db inside context dir or market_data.db inside cache dir
    for candidate in (dest_norm, dest_rel_norm):
        parent_dir = os.path.dirname(candidate)
        base_name = os.path.basename(candidate)
        if parent_dir == context_dir_real and base_name == "gem.db":
            raise MigrationSafetyError(
                f"Operation refused: destination database cannot be created in production context dir: {dest_path}"
            )
        if parent_dir == cache_dir_real and base_name == "market_data.db":
            raise MigrationSafetyError(
                f"Operation refused: destination database cannot be created in production cache dir: {dest_path}"
            )

    return dest_real


def extract_rule_code_and_directive(rule_text: str) -> Tuple[Optional[str], str]:
    """
    Extracts rule_code (e.g. 'L-219') and directive text from legacy rule strings.
    If no rule code is extractable, returns (None, rule_text).
    """
    match = re.match(r"^(L-\d+):\s*(.*)$", rule_text.strip(), flags=re.DOTALL)
    if match:
        return match.group(1).upper(), match.group(2).strip()
    return None, rule_text.strip()

