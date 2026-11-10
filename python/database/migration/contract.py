"""
Explicit Versioned Migration Contract and Validation Infrastructure.
Governed by Stage 1.5-D Architecture and Agent Governance Framework.
"""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

from python.database.manager import DatabaseManager
from python.database.repositories.base import (
    validate_timestamp_utc,
    decimal_to_canonical_str,
    ValidationError,
)
from python.database.migration.models import (
    TimestampProvenance,
    MigrationSafetyError,
    MigrationValidationError,
)

__all__ = [
    "MIGRATION_CONTRACT_VERSION",
    "SQLITE_OWNED_DOMAINS",
    "EXTERNALLY_MANAGED_SECTIONS",
    "CASH_EQUALITY_FIELDS",
    "POSITION_EQUALITY_FIELDS",
    "WATCHLIST_EQUALITY_FIELDS",
    "SCOUT_CATEGORY_EQUALITY_FIELDS",
    "TRADE_LESSON_EQUALITY_FIELDS",
    "DECISION_LOG_EQUALITY_FIELDS",
    "records_equal",
    "is_date_only",
    "is_timezone_aware",
    "normalize_aware_timestamp",
    "parse_financial_decimal",
    "parse_ticker",
    "parse_iso_timestamp",
    "resolve_historical_timestamp",
    "transform_legacy_decision_case_b",
    "transform_legacy_decision_case_c",
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

CASH_EQUALITY_FIELDS = ("account_id", "currency", "amount", "timestamp_utc")
POSITION_EQUALITY_FIELDS = ("account_id", "ticker", "shares", "wac_eur", "wac_usd", "timestamp_utc")
WATCHLIST_EQUALITY_FIELDS = ("ticker", "status", "notes", "timestamp_utc")
SCOUT_CATEGORY_EQUALITY_FIELDS = ("category_name", "description", "tickers", "timestamp_utc")
TRADE_LESSON_EQUALITY_FIELDS = ("rule_code", "directive_text", "category", "status", "created_at_utc", "last_reviewed_at_utc")
DECISION_LOG_EQUALITY_FIELDS = ("session_id", "action_proposed", "decision_outcome", "ticker", "votes", "transcript_text", "timestamp_utc")


def records_equal(
    rec1: Dict[str, Any],
    rec2: Dict[str, Any],
    fields: Sequence[str],
) -> bool:
    """
    Compares two domain records for equality across specified contract fields (D1.1).
    Guarantees:
    - Exact Decimal numerical comparison for financial fields.
    - Set / sorted sequence comparison for collections (e.g. tickers).
    - Recursive dictionary comparison for JSON blobs (e.g. votes).
    - String and None exact comparison.
    """
    for f in fields:
        v1 = rec1.get(f)
        v2 = rec2.get(f)
        if v1 is None and v2 is None:
            continue
        if v1 is None or v2 is None:
            return False

        # Decimal / numerical comparison
        if isinstance(v1, Decimal) or isinstance(v2, Decimal):
            try:
                if Decimal(str(v1)) != Decimal(str(v2)):
                    return False
                continue
            except (InvalidOperation, ValueError):
                return False

        # Collection comparison (e.g. tickers list/tuple/set)
        if isinstance(v1, (list, tuple, set)) and isinstance(v2, (list, tuple, set)):
            if sorted(list(v1)) != sorted(list(v2)):
                return False
            continue

        # Dictionary comparison (e.g. votes)
        if isinstance(v1, dict) and isinstance(v2, dict):
            if v1 != v2:
                return False
            continue

        # Standard equality
        if v1 != v2:
            return False

    return True


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


_DATE_ONLY_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def is_date_only(value: Any) -> bool:
    """Returns True if value is a string in YYYY-MM-DD format."""
    if not isinstance(value, str):
        return False
    return bool(_DATE_ONLY_PATTERN.fullmatch(value.strip()))


def is_timezone_aware(value: Any) -> bool:
    """
    Returns True if value is an ISO-8601 string containing an explicit timezone offset or 'Z'.
    Date-only strings and naive date/time strings return False.
    """
    if not isinstance(value, str) or not value.strip():
        return False
    s = value.strip()
    if is_date_only(s):
        return False
    try:
        norm = s[:-1] + "+00:00" if s.endswith("Z") else s
        dt = datetime.fromisoformat(norm)
        return dt.tzinfo is not None and dt.utcoffset() is not None
    except Exception:
        return False


def normalize_aware_timestamp(value: Any, field_name: str = "timestamp") -> str:
    """
    Validates that value is timezone-aware, converts to UTC, and formats with canonical 'Z' suffix (D1.4, D1.5).
    Rejects date-only strings and timezone-naive timestamps fail-closed.
    """
    if not isinstance(value, str) or not value.strip():
        raise MigrationValidationError(f"{field_name}: timestamp must be non-empty string, got {type(value).__name__}")
    s = value.strip()
    if is_date_only(s):
        raise MigrationValidationError(
            f"{field_name}: date-only string '{s}' cannot be converted to timestamp without explicit authorization"
        )
    try:
        norm = s[:-1] + "+00:00" if s.endswith("Z") else s
        dt = datetime.fromisoformat(norm)
    except Exception as exc:
        raise MigrationValidationError(f"{field_name}: invalid timestamp format '{s}': {exc}") from exc

    if dt.tzinfo is None or dt.utcoffset() is None:
        raise MigrationValidationError(
            f"{field_name}: timezone-naive timestamp '{s}' is forbidden fail-closed; "
            f"timezone cannot be established without explicit caller authorization"
        )

    utc_dt = dt.astimezone(timezone.utc)
    if utc_dt.microsecond > 0:
        canonical = utc_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    else:
        canonical = utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return validate_timestamp_utc(canonical)


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
    allow_date_only: bool = False,
    allow_naive_utc: bool = False,
) -> Tuple[Optional[str], TimestampProvenance]:
    """
    Resolves historical timestamp without implicit current-time fabrication (D-01, D1.4, D1.5).
    - If source_value is present:
        - If date-only: rejected unless allow_date_only=True (provenance: DERIVED).
        - If timezone-naive: rejected unless allow_naive_utc=True (provenance: DERIVED).
        - If timezone-aware: normalized to canonical UTC ending in 'Z' (provenance: SOURCE_PROVIDED).
    - If source_value is missing:
        - If default_timestamp is provided: validated and returned (provenance: CALLER_SUPPLIED_DEFAULT).
        - If nullable=True: returns (None, TimestampProvenance.MISSING).
        - Otherwise raises MigrationValidationError fail-closed.
    """
    if source_value is not None and str(source_value).strip() != "":
        val_str = str(source_value).strip()
        if is_date_only(val_str):
            if allow_date_only:
                canonical = f"{val_str}T00:00:00Z"
                validate_timestamp_utc(canonical)
                return canonical, TimestampProvenance.DERIVED
            raise MigrationValidationError(
                f"{field_name}: date-only value '{val_str}' cannot be converted to timestamp without explicit authorization"
            )

        # Check if timezone naive
        try:
            norm = val_str[:-1] + "+00:00" if val_str.endswith("Z") else val_str
            dt = datetime.fromisoformat(norm)
            is_naive = dt.tzinfo is None or dt.utcoffset() is None
        except Exception as exc:
            raise MigrationValidationError(f"{field_name}: invalid timestamp format '{val_str}': {exc}") from exc

        if is_naive:
            if allow_naive_utc:
                candidate = f"{val_str}Z"
                try:
                    validate_timestamp_utc(candidate)
                    return candidate, TimestampProvenance.DERIVED
                except ValidationError as e:
                    raise MigrationValidationError(f"{field_name}: invalid derived naive UTC timestamp: {e}") from e
            raise MigrationValidationError(
                f"{field_name}: timezone-naive timestamp '{val_str}' is forbidden fail-closed; "
                f"timezone cannot be established without explicit caller authorization"
            )

        # Timezone aware
        canonical = normalize_aware_timestamp(val_str, field_name)
        return canonical, TimestampProvenance.SOURCE_PROVIDED

    if default_timestamp is not None and str(default_timestamp).strip() != "":
        ts = parse_iso_timestamp(default_timestamp, f"{field_name} (default)")
        return ts, TimestampProvenance.CALLER_SUPPLIED_DEFAULT

    if nullable:
        return None, TimestampProvenance.MISSING

    raise MigrationValidationError(
        f"{field_name}: historical timestamp is missing; migration cannot fabricate provenance"
    )


def transform_legacy_decision_case_b(
    record: Dict[str, Any],
    default_timestamp: Optional[str] = None,
    allow_date_only: bool = False,
) -> Dict[str, Any]:
    """
    Transforms a Case B legacy date-based decision record into canonical decision fields (D-06, D1.3).
    """
    if "date" not in record:
        raise MigrationValidationError("Case B decision record missing 'date' field")
    date_str = str(record["date"]).strip()
    session_id = f"LEGACY-SESSION-{date_str}"
    lesson = record.get("lesson") or record.get("action") or "Legacy session log"
    action = str(lesson).strip()
    outcome = str(record.get("outcome") or "AUTO_LOGGED").strip()
    raw_ticker = record.get("ticker")
    ticker = parse_ticker(raw_ticker) if (raw_ticker and str(raw_ticker).strip().upper() != "SESSION") else None
    transcript = str(lesson) if record.get("lesson") else None
    votes: Dict[str, Any] = {}

    ts, _ = resolve_historical_timestamp(
        date_str,
        "decision_log.date",
        default_timestamp=default_timestamp,
        allow_date_only=allow_date_only,
    )

    return {
        "session_id": session_id,
        "action_proposed": action,
        "decision_outcome": outcome,
        "ticker": ticker,
        "votes": votes,
        "transcript_text": transcript,
        "timestamp_utc": ts,
    }


def transform_legacy_decision_case_c(
    record: Dict[str, Any],
    default_timestamp: Optional[str] = None,
    allow_naive_utc: bool = False,
) -> List[Dict[str, Any]]:
    """
    Transforms a Case C nested council debate record into canonical decision records (D-06, D1.3).
    """
    if "decisions" not in record or not isinstance(record["decisions"], list):
        raise MigrationValidationError("Case C decision record missing 'decisions' list")

    ts_raw = record.get("timestamp")
    parent_ts, _ = resolve_historical_timestamp(
        ts_raw,
        "decision_log.timestamp",
        default_timestamp=default_timestamp,
        allow_naive_utc=allow_naive_utc,
    )
    date_part = parent_ts[:10] if parent_ts else "UNKNOWN"
    trigger_ctx = str(record.get("trigger_context") or "DELIBERATION").strip()
    sanitized_trigger = re.sub(r'[^A-Za-z0-9_]+', '_', trigger_ctx).strip('_')
    session_id = f"SESSION-{date_part}-{sanitized_trigger}"
    council_debate = record.get("council_debate") or {}
    transcript = json.dumps(council_debate, sort_keys=True) if isinstance(council_debate, dict) else str(council_debate)

    sub_records: List[Dict[str, Any]] = []
    for sub_idx, sub_d in enumerate(record["decisions"]):
        if not isinstance(sub_d, dict):
            continue
        sub_ticker_raw = sub_d.get("ticker")
        sub_ticker = parse_ticker(sub_ticker_raw) if sub_ticker_raw else None
        sub_action = str(sub_d.get("trade_state") or sub_d.get("action") or trigger_ctx).strip()
        sub_outcome = str(sub_d.get("trade_state") or sub_d.get("outcome") or "APPROVED").strip()
        sub_votes = sub_d.get("scrutiny_audit") or council_debate or {}
        sub_ts_raw = sub_d.get("timestamp") or ts_raw
        sub_ts, _ = resolve_historical_timestamp(
            sub_ts_raw,
            f"decision_log.decisions[{sub_idx}].timestamp",
            default_timestamp=parent_ts or default_timestamp,
            allow_naive_utc=allow_naive_utc,
        )
        sub_records.append({
            "session_id": session_id,
            "action_proposed": sub_action,
            "decision_outcome": sub_outcome,
            "ticker": sub_ticker,
            "votes": sub_votes,
            "transcript_text": transcript,
            "timestamp_utc": sub_ts,
        })
    return sub_records

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

