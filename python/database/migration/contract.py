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
    MigrationConflictError,
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
    "consolidate_scout_representations",
    "consolidate_watchlist_representations",
    "consolidate_position_representations",
    "consolidate_cash_representations",
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

_UNSPECIFIED = object()
_MISSING = object()


def deep_equal(v1: Any, v2: Any) -> bool:
    """
    Recursively compares two values with Decimal numerical equivalence,
    sorted collection comparison, and recursive dict evaluation (D1.1, D1.1.1).
    """
    if v1 is None and v2 is None:
        return True
    if v1 is None or v2 is None:
        return False

    # Decimal / numerical comparison
    if isinstance(v1, Decimal) or isinstance(v2, Decimal):
        try:
            return Decimal(str(v1)) == Decimal(str(v2))
        except (InvalidOperation, ValueError):
            return False

    # Collection comparison (e.g. tickers list/tuple/set)
    if isinstance(v1, (list, tuple, set)) and isinstance(v2, (list, tuple, set)):
        if len(v1) != len(v2):
            return False
        # For sets or simple hashable collections of scalars (like tickers)
        if isinstance(v1, set) or isinstance(v2, set):
            try:
                s1 = sorted(list(v1))
                s2 = sorted(list(v2))
                return all(deep_equal(x, y) for x, y in zip(s1, s2))
            except TypeError:
                pass
        return all(deep_equal(x, y) for x, y in zip(v1, v2))

    # Dictionary comparison (e.g. votes)
    if isinstance(v1, dict) and isinstance(v2, dict):
        if set(v1.keys()) != set(v2.keys()):
            return False
        return all(deep_equal(v1[k], v2[k]) for k in v1)

    # Standard equality
    return bool(v1 == v2)


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
        if not deep_equal(rec1.get(f), rec2.get(f)):
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
    - If source_value is genuinely missing (_MISSING or _UNSPECIFIED):
        - If default_timestamp is provided: validated and returned (provenance: CALLER_SUPPLIED_DEFAULT).
        - If nullable=True: returns (None, TimestampProvenance.MISSING).
        - Otherwise raises MigrationValidationError fail-closed.
    - If source_value is explicit None:
        - If nullable=True: returns (None, TimestampProvenance.MISSING).
        - If nullable=False: raises MigrationValidationError fail-closed (cannot silently fall back to default).
    - If source_value is explicitly supplied:
        - If empty string or whitespace: raises MigrationValidationError fail-closed.
        - If date-only: rejected unless allow_date_only=True (provenance: DERIVED).
        - If timezone-naive: rejected unless allow_naive_utc=True (provenance: DERIVED).
        - If timezone-aware: normalized to canonical UTC ending in 'Z' (provenance: SOURCE_PROVIDED).
    """
    if source_value is _MISSING or source_value is _UNSPECIFIED:
        if default_timestamp is not None and str(default_timestamp).strip() != "":
            ts = parse_iso_timestamp(default_timestamp, f"{field_name} (default)")
            return ts, TimestampProvenance.CALLER_SUPPLIED_DEFAULT
        if nullable:
            return None, TimestampProvenance.MISSING
        raise MigrationValidationError(
            f"{field_name}: historical timestamp is missing; migration cannot fabricate provenance"
        )

    if source_value is None:
        if nullable:
            return None, TimestampProvenance.MISSING
        raise MigrationValidationError(
            f"{field_name}: non-nullable historical timestamp cannot be null"
        )

    if not isinstance(source_value, str):
        raise MigrationValidationError(
            f"{field_name}: invalid timestamp format: expected string, got {type(source_value).__name__}"
        )

    val_str = source_value.strip()
    if not val_str:
        raise MigrationValidationError(
            f"{field_name}: explicitly supplied timestamp cannot be empty or whitespace"
        )

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

    raw_parent_ts = record["timestamp"] if "timestamp" in record else _UNSPECIFIED
    parent_ts, _ = resolve_historical_timestamp(
        raw_parent_ts,
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
    seen_sub_identities: Set[Tuple[str, Optional[str]]] = set()

    for sub_idx, sub_d in enumerate(record["decisions"]):
        if not isinstance(sub_d, dict):
            raise MigrationValidationError(
                f"decision_log.decisions[{sub_idx}]: sub-decision must be a dict, got {type(sub_d).__name__}"
            )
        if not sub_d:
            raise MigrationValidationError(
                f"decision_log.decisions[{sub_idx}]: sub-decision cannot be empty"
            )

        sub_ticker_raw = sub_d.get("ticker")
        sub_ticker = parse_ticker(sub_ticker_raw) if sub_ticker_raw else None
        raw_action = sub_d.get("trade_state") or sub_d.get("action")
        if not raw_action and not sub_ticker and not sub_d.get("outcome"):
            raise MigrationValidationError(
                f"decision_log.decisions[{sub_idx}]: required fields (action/trade_state/ticker) cannot be derived from sub-decision"
            )
        sub_action = str(raw_action or trigger_ctx).strip()
        sub_outcome = str(sub_d.get("trade_state") or sub_d.get("outcome") or "APPROVED").strip()

        # R3/R5: Detect duplicate or colliding canonical sub-decision identities within the session
        sub_key = (sub_action, sub_ticker)
        if sub_key in seen_sub_identities:
            raise MigrationConflictError(
                f"decision_log.decisions[{sub_idx}]: duplicate/colliding canonical sub-decision identity "
                f"'{sub_key}' within session '{session_id}'"
            )
        seen_sub_identities.add(sub_key)

        sub_votes = sub_d.get("scrutiny_audit") or council_debate or {}
        if "timestamp" in sub_d:
            sub_ts_raw = sub_d["timestamp"]
            ts_field_name = f"decision_log.decisions[{sub_idx}].timestamp"
        elif raw_parent_ts is not _UNSPECIFIED:
            sub_ts_raw = raw_parent_ts
            ts_field_name = "decision_log.timestamp"
        else:
            sub_ts_raw = _UNSPECIFIED
            ts_field_name = f"decision_log.decisions[{sub_idx}].timestamp"

        sub_default = parent_ts if parent_ts is not None else default_timestamp
        sub_ts, _ = resolve_historical_timestamp(
            sub_ts_raw,
            ts_field_name,
            default_timestamp=sub_default,
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


def consolidate_scout_representations(
    data: Dict[str, Any],
    default_timestamp: Optional[str] = None,
    allow_date_only: bool = False,
    allow_naive_utc: bool = False,
    discrepancies: Optional[List[Dict[str, Any]]] = None,
    source_name: str = "source",
    require_timestamp: bool = True,
) -> List[Dict[str, Any]]:
    """
    Consolidates scout category representations (SCOUT_TICKER_MAP, scout_categories, scout_category_records)
    deterministically (Stage 1.5-D.1.1.2 R3).
    Fails closed with MigrationConflictError on conflicting category tickers, descriptions, or timestamps.
    Consolidates identical or compatible representations without double counting, invariant to key ordering.
    """
    records_by_cat: Dict[str, Dict[str, Any]] = {}
    sources_by_cat: Dict[str, List[str]] = {}

    rep_items: List[Tuple[str, str, Dict[str, Any]]] = []

    # 1. SCOUT_TICKER_MAP and scout_candidates
    for map_key in ("SCOUT_TICKER_MAP", "scout_candidates"):
        if map_key in data and isinstance(data[map_key], dict):
            for raw_k, raw_v in data[map_key].items():
                cat = str(raw_k).strip()
                if not cat:
                    continue
                if raw_v is None:
                    parsed_tickers = None
                elif isinstance(raw_v, list):
                    parsed_tickers = sorted(list({parse_ticker(t, f"{map_key}[{cat}].ticker") for t in raw_v if t}))
                else:
                    raise MigrationValidationError(f"{map_key}[{cat}]: tickers must be a list")
                if default_timestamp:
                    ts_val, _ = resolve_historical_timestamp(
                        _UNSPECIFIED,
                        f"{map_key}[{cat}].updated_at_utc",
                        default_timestamp=default_timestamp,
                        allow_date_only=allow_date_only,
                        allow_naive_utc=allow_naive_utc,
                    )
                elif require_timestamp:
                    raise MigrationValidationError(
                        f"{map_key}[{cat}].updated_at_utc: historical timestamp is missing; migration cannot fabricate provenance"
                    )
                else:
                    ts_val = None
                rep_items.append((map_key, cat, {
                    "category_name": cat,
                    "description": _UNSPECIFIED,
                    "tickers": parsed_tickers,
                    "updated_at_utc": ts_val,
                    "timestamp_utc": ts_val,
                }))

    # 2. scout_categories and scout_category_records
    for rep_key in ("scout_categories", "scout_category_records"):
        raw_list = data.get(rep_key)
        if isinstance(raw_list, list):
            for idx, entry in enumerate(raw_list):
                if isinstance(entry, dict):
                    raw_name = entry.get("category_name")
                    if not raw_name:
                        continue
                    cat = str(raw_name).strip()
                    if "tickers" in entry:
                        raw_tickers = entry["tickers"]
                        if raw_tickers is None:
                            parsed_tickers = None
                        elif isinstance(raw_tickers, list):
                            parsed_tickers = sorted(list({parse_ticker(t, f"{rep_key}[{idx}].ticker") for t in raw_tickers if t}))
                        else:
                            raise MigrationValidationError(f"{rep_key}[{idx}].tickers must be a list")
                    else:
                        parsed_tickers = _UNSPECIFIED

                    if "description" in entry:
                        desc = str(entry["description"]).strip() if entry["description"] is not None else None
                    else:
                        desc = _UNSPECIFIED

                    if "updated_at_utc" in entry:
                        raw_ts = entry["updated_at_utc"]
                        ts_field = f"{rep_key}[{idx}].updated_at_utc"
                    elif "timestamp_utc" in entry:
                        raw_ts = entry["timestamp_utc"]
                        ts_field = f"{rep_key}[{idx}].timestamp_utc"
                    else:
                        raw_ts = _UNSPECIFIED
                        ts_field = f"{rep_key}[{idx}].updated_at_utc"

                    if raw_ts is not _UNSPECIFIED:
                        ts_val, _ = resolve_historical_timestamp(
                            raw_ts,
                            ts_field,
                            default_timestamp=default_timestamp,
                            allow_date_only=allow_date_only,
                            allow_naive_utc=allow_naive_utc,
                        )
                    elif default_timestamp:
                        ts_val, _ = resolve_historical_timestamp(
                            _UNSPECIFIED,
                            ts_field,
                            default_timestamp=default_timestamp,
                            allow_date_only=allow_date_only,
                            allow_naive_utc=allow_naive_utc,
                        )
                    elif require_timestamp:
                        raise MigrationValidationError(
                            f"{ts_field}: historical timestamp is missing; migration cannot fabricate provenance"
                        )
                    else:
                        ts_val = None

                    rep_items.append((rep_key, cat, {
                        "category_name": cat,
                        "description": desc,
                        "tickers": parsed_tickers,
                        "updated_at_utc": ts_val,
                        "timestamp_utc": ts_val,
                    }))
                elif isinstance(entry, str) and entry.strip():
                    cat = entry.strip()
                    if default_timestamp:
                        ts_val, _ = resolve_historical_timestamp(
                            _UNSPECIFIED,
                            f"{rep_key}[{idx}].updated_at_utc",
                            default_timestamp=default_timestamp,
                            allow_date_only=allow_date_only,
                            allow_naive_utc=allow_naive_utc,
                        )
                    elif require_timestamp:
                        raise MigrationValidationError(
                            f"{rep_key}[{idx}].updated_at_utc: historical timestamp is missing; migration cannot fabricate provenance"
                        )
                    else:
                        ts_val = None
                    rep_items.append((rep_key, cat, {
                        "category_name": cat,
                        "description": _UNSPECIFIED,
                        "tickers": _UNSPECIFIED,
                        "updated_at_utc": ts_val,
                        "timestamp_utc": ts_val,
                    }))

    for rep_name, cat, item in rep_items:
        if cat not in records_by_cat:
            records_by_cat[cat] = item
            sources_by_cat[cat] = [rep_name]
        else:
            prev = records_by_cat[cat]
            prev_source = sources_by_cat[cat][0]

            if records_equal(prev, item, SCOUT_CATEGORY_EQUALITY_FIELDS):
                if discrepancies is not None:
                    discrepancies.append({
                        "domain": "scout_categories",
                        "category_name": cat,
                        "type": "EXACT_DUPLICATE_DEDUPLICATED",
                        "message": f"Exact duplicate scout category record for '{cat}' deduplicated.",
                    })
                continue

            # Compare tickers
            prev_tickers = prev.get("tickers")
            item_tickers = item.get("tickers")
            if prev_tickers is not _UNSPECIFIED and item_tickers is not _UNSPECIFIED:
                if prev_tickers != item_tickers:
                    raise MigrationConflictError(
                        f"Conflicting duplicate scout category record for {cat} in {source_name}: "
                        f"existing {prev} != incoming {item} (representation '{prev_source}' tickers {prev_tickers} != '{rep_name}' {item_tickers})"
                    )

            # Compare description
            prev_desc = prev.get("description")
            item_desc = item.get("description")
            if prev_desc is not _UNSPECIFIED and item_desc is not _UNSPECIFIED and prev_desc != item_desc:
                raise MigrationConflictError(
                    f"Conflicting duplicate scout category record for {cat} in {source_name}: "
                    f"existing {prev} != incoming {item} (representation '{prev_source}' description '{prev_desc}' != '{rep_name}' '{item_desc}')"
                )

            # Compare timestamp
            prev_ts = prev.get("timestamp_utc")
            item_ts = item.get("timestamp_utc")
            if prev_ts is not None and item_ts is not None and prev_ts != item_ts:
                raise MigrationConflictError(
                    f"Conflicting duplicate scout category record for {cat} in {source_name}: "
                    f"existing {prev} != incoming {item} (representation '{prev_source}' timestamp '{prev_ts}' != '{rep_name}' '{item_ts}')"
                )

            prev["tickers"] = prev_tickers if prev_tickers is not _UNSPECIFIED else item_tickers
            prev["description"] = prev_desc if prev_desc is not _UNSPECIFIED else item_desc
            prev["timestamp_utc"] = prev_ts if prev_ts is not None else item_ts
            prev["updated_at_utc"] = prev["timestamp_utc"]
            sources_by_cat[cat].append(rep_name)

    for rec in records_by_cat.values():
        if rec.get("description") is _UNSPECIFIED:
            rec["description"] = None
        if rec.get("tickers") is _UNSPECIFIED:
            rec["tickers"] = []

    return sorted(records_by_cat.values(), key=lambda x: x["category_name"])


def consolidate_watchlist_representations(
    data: Dict[str, Any],
    default_timestamp: Optional[str] = None,
    allow_date_only: bool = False,
    allow_naive_utc: bool = False,
    discrepancies: Optional[List[Dict[str, Any]]] = None,
    source_name: str = "source",
    require_timestamp: bool = True,
) -> List[Dict[str, Any]]:
    """
    Consolidates watchlist representations (watchlist, watched_tickers, WATCHLIST, mutable_state)
    deterministically (Stage 1.5-D.1.1.2 R3).
    Fails closed with MigrationConflictError on conflicting status, notes, or timestamps.
    Consolidates identical or compatible representations without double counting, invariant to key ordering.
    """
    records_by_tk: Dict[str, Dict[str, Any]] = {}
    sources_by_tk: Dict[str, List[str]] = {}

    rep_items: List[Tuple[str, str, Dict[str, Any]]] = []

    # 1. watchlist structured list
    if "watchlist" in data and isinstance(data["watchlist"], list):
        for idx, entry in enumerate(data["watchlist"]):
            if isinstance(entry, dict):
                tk = parse_ticker(entry.get("ticker"), f"watchlist[{idx}].ticker")
                if "status" in entry:
                    status = str(entry["status"]).strip().upper() if entry["status"] is not None else None
                else:
                    status = _UNSPECIFIED
                if "notes" in entry:
                    notes = str(entry["notes"]).strip() if entry["notes"] is not None else None
                else:
                    notes = _UNSPECIFIED
                if "added_at_utc" in entry:
                    raw_ts = entry["added_at_utc"]
                    ts_field = f"watchlist[{idx}].added_at_utc"
                elif "timestamp_utc" in entry:
                    raw_ts = entry["timestamp_utc"]
                    ts_field = f"watchlist[{idx}].timestamp_utc"
                else:
                    raw_ts = _UNSPECIFIED
                    ts_field = f"watchlist[{idx}].added_at_utc"

                if raw_ts is not _UNSPECIFIED:
                    ts_val, _ = resolve_historical_timestamp(
                        raw_ts,
                        ts_field,
                        default_timestamp=default_timestamp,
                        allow_date_only=allow_date_only,
                        allow_naive_utc=allow_naive_utc,
                    )
                elif default_timestamp:
                    ts_val, _ = resolve_historical_timestamp(
                        _UNSPECIFIED,
                        ts_field,
                        default_timestamp=default_timestamp,
                        allow_date_only=allow_date_only,
                        allow_naive_utc=allow_naive_utc,
                    )
                elif require_timestamp:
                    raise MigrationValidationError(
                        f"{ts_field}: historical timestamp is missing; migration cannot fabricate provenance"
                    )
                else:
                    ts_val = None
                rep_items.append(("watchlist", tk, {
                    "ticker": tk,
                    "status": status,
                    "notes": notes,
                    "added_at_utc": ts_val,
                    "timestamp_utc": ts_val,
                }))
            else:
                raise MigrationValidationError(f"watchlist[{idx}] must be a dict")

    # 2. watched_tickers (top-level and in mutable_state) and WATCHLIST
    candidate_keys = ["watched_tickers", "WATCHLIST"]
    for rep_key in candidate_keys:
        raw_list = data.get(rep_key)
        if raw_list is None and rep_key == "watched_tickers" and isinstance(data.get("mutable_state"), dict):
            raw_list = data["mutable_state"].get(rep_key)
        if isinstance(raw_list, list):
            for idx, entry in enumerate(raw_list):
                if isinstance(entry, str) and entry.strip():
                    tk = parse_ticker(entry, f"{rep_key}[{idx}]")
                    if default_timestamp:
                        ts_val, _ = resolve_historical_timestamp(
                            _UNSPECIFIED,
                            f"{rep_key}[{idx}].added_at_utc",
                            default_timestamp=default_timestamp,
                            allow_date_only=allow_date_only,
                            allow_naive_utc=allow_naive_utc,
                        )
                    elif require_timestamp:
                        raise MigrationValidationError(
                            f"{rep_key}[{idx}].added_at_utc: historical timestamp is missing; migration cannot fabricate provenance"
                        )
                    else:
                        ts_val = None
                    rep_items.append((rep_key, tk, {
                        "ticker": tk,
                        "status": _UNSPECIFIED,
                        "notes": _UNSPECIFIED,
                        "added_at_utc": ts_val,
                        "timestamp_utc": ts_val,
                    }))
                elif isinstance(entry, dict):
                    tk = parse_ticker(entry.get("ticker"), f"{rep_key}[{idx}].ticker")
                    if "status" in entry:
                        status = str(entry["status"]).strip().upper() if entry["status"] is not None else None
                    else:
                        status = _UNSPECIFIED
                    if "notes" in entry:
                        notes = str(entry["notes"]).strip() if entry["notes"] is not None else None
                    else:
                        notes = _UNSPECIFIED
                    if "added_at_utc" in entry:
                        raw_ts = entry["added_at_utc"]
                        ts_field = f"{rep_key}[{idx}].added_at_utc"
                    elif "timestamp_utc" in entry:
                        raw_ts = entry["timestamp_utc"]
                        ts_field = f"{rep_key}[{idx}].timestamp_utc"
                    else:
                        raw_ts = _UNSPECIFIED
                        ts_field = f"{rep_key}[{idx}].added_at_utc"

                    if raw_ts is not _UNSPECIFIED:
                        ts_val, _ = resolve_historical_timestamp(
                            raw_ts,
                            ts_field,
                            default_timestamp=default_timestamp,
                            allow_date_only=allow_date_only,
                            allow_naive_utc=allow_naive_utc,
                        )
                    elif default_timestamp:
                        ts_val, _ = resolve_historical_timestamp(
                            _UNSPECIFIED,
                            ts_field,
                            default_timestamp=default_timestamp,
                            allow_date_only=allow_date_only,
                            allow_naive_utc=allow_naive_utc,
                        )
                    elif require_timestamp:
                        raise MigrationValidationError(
                            f"{ts_field}: historical timestamp is missing; migration cannot fabricate provenance"
                        )
                    else:
                        ts_val = None
                    rep_items.append((rep_key, tk, {
                        "ticker": tk,
                        "status": status,
                        "notes": notes,
                        "added_at_utc": ts_val,
                        "timestamp_utc": ts_val,
                    }))
        elif isinstance(raw_list, dict):
            for tk_raw, details in raw_list.items():
                tk = parse_ticker(tk_raw, f"{rep_key}[{tk_raw}]")
                if isinstance(details, dict):
                    if "status" in details:
                        status = str(details["status"]).strip().upper() if details["status"] is not None else None
                    else:
                        status = _UNSPECIFIED
                    if "notes" in details:
                        notes = str(details["notes"]).strip() if details["notes"] is not None else None
                    else:
                        notes = _UNSPECIFIED
                    if "added_at_utc" in details:
                        raw_ts = details["added_at_utc"]
                        ts_field = f"{rep_key}[{tk_raw}].added_at_utc"
                    elif "timestamp_utc" in details:
                        raw_ts = details["timestamp_utc"]
                        ts_field = f"{rep_key}[{tk_raw}].timestamp_utc"
                    else:
                        raw_ts = _UNSPECIFIED
                        ts_field = f"{rep_key}[{tk_raw}].added_at_utc"

                    if raw_ts is not _UNSPECIFIED:
                        ts_val, _ = resolve_historical_timestamp(
                            raw_ts,
                            ts_field,
                            default_timestamp=default_timestamp,
                            allow_date_only=allow_date_only,
                            allow_naive_utc=allow_naive_utc,
                        )
                    elif default_timestamp:
                        ts_val, _ = resolve_historical_timestamp(
                            _UNSPECIFIED,
                            ts_field,
                            default_timestamp=default_timestamp,
                            allow_date_only=allow_date_only,
                            allow_naive_utc=allow_naive_utc,
                        )
                    elif require_timestamp:
                        raise MigrationValidationError(
                            f"{ts_field}: historical timestamp is missing; migration cannot fabricate provenance"
                        )
                    else:
                        ts_val = None
                else:
                    status = _UNSPECIFIED
                    notes = _UNSPECIFIED
                    if default_timestamp:
                        ts_val, _ = resolve_historical_timestamp(
                            _UNSPECIFIED,
                            f"{rep_key}[{tk_raw}].added_at_utc",
                            default_timestamp=default_timestamp,
                            allow_date_only=allow_date_only,
                            allow_naive_utc=allow_naive_utc,
                        )
                    elif require_timestamp:
                        raise MigrationValidationError(
                            f"{rep_key}[{tk_raw}].added_at_utc: historical timestamp is missing; migration cannot fabricate provenance"
                        )
                    else:
                        ts_val = None
                rep_items.append((rep_key, tk, {
                    "ticker": tk,
                    "status": status,
                    "notes": notes,
                    "added_at_utc": ts_val,
                    "timestamp_utc": ts_val,
                }))

    for rep_name, tk, item in rep_items:
        if tk not in records_by_tk:
            records_by_tk[tk] = item
            sources_by_tk[tk] = [rep_name]
        else:
            prev = records_by_tk[tk]
            prev_source = sources_by_tk[tk][0]

            if records_equal(prev, item, WATCHLIST_EQUALITY_FIELDS):
                if discrepancies is not None:
                    discrepancies.append({
                        "domain": "watchlist",
                        "ticker": tk,
                        "type": "EXACT_DUPLICATE_DEDUPLICATED",
                        "message": f"Exact duplicate watchlist record for {tk} deduplicated.",
                    })
                continue

            # Compare status
            prev_status = prev.get("status")
            item_status = item.get("status")
            if prev_status is not _UNSPECIFIED and item_status is not _UNSPECIFIED and prev_status != item_status:
                raise MigrationConflictError(
                    f"Conflicting duplicate watchlist record for {tk} in {source_name}: "
                    f"existing {prev} != incoming {item} (representation '{prev_source}' status '{prev_status}' != '{rep_name}' '{item_status}')"
                )

            # Compare notes
            prev_notes = prev.get("notes")
            item_notes = item.get("notes")
            if prev_notes is not _UNSPECIFIED and item_notes is not _UNSPECIFIED and prev_notes != item_notes:
                raise MigrationConflictError(
                    f"Conflicting duplicate watchlist record for {tk} in {source_name}: "
                    f"existing {prev} != incoming {item} (representation '{prev_source}' notes '{prev_notes}' != '{rep_name}' '{item_notes}')"
                )

            # Compare timestamp
            prev_ts = prev.get("timestamp_utc")
            item_ts = item.get("timestamp_utc")
            if prev_ts is not None and item_ts is not None and prev_ts != item_ts:
                raise MigrationConflictError(
                    f"Conflicting duplicate watchlist record for {tk} in {source_name}: "
                    f"existing {prev} != incoming {item} (representation '{prev_source}' timestamp '{prev_ts}' != '{rep_name}' '{item_ts}')"
                )

            prev["status"] = prev_status if prev_status is not _UNSPECIFIED else item_status
            prev["notes"] = prev_notes if prev_notes is not _UNSPECIFIED else item_notes
            prev["timestamp_utc"] = prev_ts if prev_ts is not None else item_ts
            prev["added_at_utc"] = prev["timestamp_utc"]
            sources_by_tk[tk].append(rep_name)

    for rec in records_by_tk.values():
        if rec.get("status") is _UNSPECIFIED:
            rec["status"] = "ACTIVE"
        if rec.get("notes") is _UNSPECIFIED:
            rec["notes"] = None

    return sorted(records_by_tk.values(), key=lambda x: x["ticker"])


def consolidate_position_representations(
    data: Dict[str, Any],
    default_account_id: str = "PRIMARY",
    default_timestamp: Optional[str] = None,
    allow_date_only: bool = False,
    allow_naive_utc: bool = False,
    discrepancies: Optional[List[Dict[str, Any]]] = None,
    source_name: str = "source",
    require_timestamp: bool = True,
) -> List[Dict[str, Any]]:
    """
    Consolidates position representations (portfolio_snapshot, portfolio_positions, positions, mutable_state)
    deterministically (Stage 1.5-D.1.1.2 R3).
    Fails closed with MigrationConflictError on conflicting shares, WAC EUR, WAC USD, or timestamps.
    Consolidates identical or compatible representations without double counting, invariant to key ordering.
    """
    records_by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
    sources_by_key: Dict[Tuple[str, str], List[str]] = {}

    rep_items: List[Tuple[str, Tuple[str, str], Dict[str, Any]]] = []

    for rep_key in ("portfolio_snapshot", "portfolio_positions", "positions"):
        raw_list = data.get(rep_key)
        if raw_list is None and isinstance(data.get("mutable_state"), dict):
            raw_list = data["mutable_state"].get(rep_key)

        if isinstance(raw_list, list):
            for idx, entry in enumerate(raw_list):
                if not isinstance(entry, dict):
                    raise MigrationValidationError(f"{rep_key}[{idx}] must be a dict")
                raw_acc = entry.get("account_id", _UNSPECIFIED)
                if raw_acc is _UNSPECIFIED:
                    acc = default_account_id
                else:
                    if not isinstance(raw_acc, str) or not raw_acc.strip():
                        raise MigrationValidationError(
                            f"{rep_key}[{idx}].account_id: explicitly supplied account_id must be a non-empty string"
                        )
                    acc = raw_acc.strip().upper()
                tk = parse_ticker(entry.get("ticker"), f"{rep_key}[{idx}].ticker")
                shares = parse_financial_decimal(entry.get("shares"), f"{rep_key}[{idx}].shares", max_scale=4)
                wac_eur_raw = entry.get("wac_eur") if entry.get("wac_eur") is not None else entry.get("wac")
                if wac_eur_raw is None:
                    raise MigrationValidationError(f"{rep_key}[{idx}] missing required wac/wac_eur")
                wac_eur = parse_financial_decimal(wac_eur_raw, f"{rep_key}[{idx}].wac_eur", max_scale=4)
                raw_wac_usd = entry.get("wac_usd", _UNSPECIFIED)
                if raw_wac_usd is _UNSPECIFIED:
                    wac_usd = _UNSPECIFIED
                elif raw_wac_usd is not None:
                    wac_usd = parse_financial_decimal(raw_wac_usd, f"{rep_key}[{idx}].wac_usd", max_scale=4)
                else:
                    wac_usd = None
                if "last_updated_at_utc" in entry:
                    raw_ts = entry["last_updated_at_utc"]
                    ts_field = f"{rep_key}[{idx}].last_updated_at_utc"
                elif "timestamp_utc" in entry:
                    raw_ts = entry["timestamp_utc"]
                    ts_field = f"{rep_key}[{idx}].timestamp_utc"
                else:
                    raw_ts = _UNSPECIFIED
                    ts_field = f"{rep_key}[{idx}].last_updated_at_utc"

                if raw_ts is not _UNSPECIFIED:
                    ts_val, _ = resolve_historical_timestamp(
                        raw_ts,
                        ts_field,
                        default_timestamp=default_timestamp,
                        allow_date_only=allow_date_only,
                        allow_naive_utc=allow_naive_utc,
                    )
                elif default_timestamp:
                    ts_val, _ = resolve_historical_timestamp(
                        _UNSPECIFIED,
                        ts_field,
                        default_timestamp=default_timestamp,
                        allow_date_only=allow_date_only,
                        allow_naive_utc=allow_naive_utc,
                    )
                elif require_timestamp:
                    raise MigrationValidationError(
                        f"{ts_field}: historical timestamp is missing; migration cannot fabricate provenance"
                    )
                else:
                    ts_val = None

                pos_rec = {
                    "account_id": acc,
                    "ticker": tk,
                    "shares": shares,
                    "wac_eur": wac_eur,
                    "wac_usd": wac_usd,
                    "raw_shares": entry.get("shares"),
                    "raw_wac": wac_eur_raw,
                    "raw_wac_usd": raw_wac_usd if raw_wac_usd is not _UNSPECIFIED else None,
                    "last_updated_at_utc": ts_val,
                    "timestamp_utc": ts_val,
                }
                rep_items.append((rep_key, (acc, tk), pos_rec))

    for rep_name, key, item in rep_items:
        if key not in records_by_key:
            records_by_key[key] = item
            sources_by_key[key] = [rep_name]
        else:
            prev = records_by_key[key]
            prev_source = sources_by_key[key][0]

            if records_equal(prev, item, POSITION_EQUALITY_FIELDS):
                if discrepancies is not None:
                    discrepancies.append({
                        "domain": "portfolio_positions",
                        "key": key,
                        "type": "EXACT_DUPLICATE_DEDUPLICATED",
                        "message": f"Exact duplicate position record for {key[0]}/{key[1]} deduplicated.",
                    })
                continue

            # Compare shares
            if Decimal(str(prev["shares"])) != Decimal(str(item["shares"])):
                raise MigrationConflictError(
                    f"Conflicting duplicate position record for {key[0]}/{key[1]} in {source_name}: "
                    f"existing {prev} != incoming {item} (representation '{prev_source}' shares {prev['shares']} != '{rep_name}' {item['shares']})"
                )

            # Compare WAC EUR
            if Decimal(str(prev["wac_eur"])) != Decimal(str(item["wac_eur"])):
                raise MigrationConflictError(
                    f"Conflicting duplicate position record for {key[0]}/{key[1]} in {source_name}: "
                    f"existing {prev} != incoming {item} (representation '{prev_source}' wac_eur {prev['wac_eur']} != '{rep_name}' {item['wac_eur']})"
                )

            # Compare WAC USD
            prev_usd = prev.get("wac_usd")
            item_usd = item.get("wac_usd")
            if prev_usd is not _UNSPECIFIED and item_usd is not _UNSPECIFIED:
                if (prev_usd is None) != (item_usd is None):
                    raise MigrationConflictError(
                        f"Conflicting duplicate position record for {key[0]}/{key[1]} in {source_name}: "
                        f"existing {prev} != incoming {item} (representation '{prev_source}' wac_usd {prev_usd} != '{rep_name}' {item_usd})"
                    )
                if prev_usd is not None and item_usd is not None and Decimal(str(prev_usd)) != Decimal(str(item_usd)):
                    raise MigrationConflictError(
                        f"Conflicting duplicate position record for {key[0]}/{key[1]} in {source_name}: "
                        f"existing {prev} != incoming {item} (representation '{prev_source}' wac_usd {prev_usd} != '{rep_name}' {item_usd})"
                    )

            # Compare timestamp
            prev_ts = prev.get("timestamp_utc")
            item_ts = item.get("timestamp_utc")
            if prev_ts is not None and item_ts is not None and prev_ts != item_ts:
                raise MigrationConflictError(
                    f"Conflicting duplicate position record for {key[0]}/{key[1]} in {source_name}: "
                    f"existing {prev} != incoming {item} (representation '{prev_source}' timestamp '{prev_ts}' != '{rep_name}' '{item_ts}')"
                )

            prev["wac_usd"] = prev_usd if prev_usd is not _UNSPECIFIED else item_usd
            prev["raw_wac_usd"] = prev.get("raw_wac_usd") if prev_usd is not _UNSPECIFIED else item.get("raw_wac_usd")
            prev["timestamp_utc"] = prev_ts if prev_ts is not None else item_ts
            prev["last_updated_at_utc"] = prev["timestamp_utc"]
            sources_by_key[key].append(rep_name)

    for rec in records_by_key.values():
        if rec.get("wac_usd") is _UNSPECIFIED:
            rec["wac_usd"] = None

    return sorted(records_by_key.values(), key=lambda x: (x["account_id"], x["ticker"]))


def consolidate_cash_representations(
    data: Dict[str, Any],
    default_account_id: str = "PRIMARY",
    default_timestamp: Optional[str] = None,
    allow_date_only: bool = False,
    allow_naive_utc: bool = False,
    discrepancies: Optional[List[Dict[str, Any]]] = None,
    source_name: str = "source",
    require_timestamp: bool = True,
) -> List[Dict[str, Any]]:
    """
    Consolidates portfolio cash representations (structured portfolio_cash and scalar unallocated_cash_eur/usd)
    deterministically (Stage 1.5-D.1.1.2 R3).
    Fails closed with MigrationConflictError on conflicting amounts or timestamps.
    Consolidates identical or compatible representations without double counting, invariant to key ordering.
    """
    records_by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
    sources_by_key: Dict[Tuple[str, str], List[str]] = {}

    rep_items: List[Tuple[str, Tuple[str, str], Dict[str, Any]]] = []

    # 1. Structured portfolio_cash
    raw_list = data.get("portfolio_cash")
    if isinstance(raw_list, list):
        for idx, entry in enumerate(raw_list):
            if not isinstance(entry, dict):
                raise MigrationValidationError(f"portfolio_cash[{idx}] must be a dict")
            raw_acc = entry.get("account_id", _UNSPECIFIED)
            if raw_acc is _UNSPECIFIED:
                acc = default_account_id
            else:
                if not isinstance(raw_acc, str) or not raw_acc.strip():
                    raise MigrationValidationError(
                        f"portfolio_cash[{idx}].account_id: explicitly supplied account_id must be a non-empty string"
                    )
                acc = raw_acc.strip().upper()

            raw_curr = entry.get("currency", _UNSPECIFIED)
            if raw_curr is _UNSPECIFIED:
                curr = "EUR"
            else:
                if not isinstance(raw_curr, str) or not raw_curr.strip():
                    raise MigrationValidationError(
                        f"portfolio_cash[{idx}].currency: explicitly supplied currency must be a non-empty string"
                    )
                curr = raw_curr.strip().upper()

            if curr not in ("EUR", "USD"):
                raise MigrationValidationError(f"portfolio_cash[{idx}]: unsupported currency '{curr}'")
            amt = parse_financial_decimal(entry.get("amount"), f"portfolio_cash[{idx}].amount", max_scale=2)
            if "last_updated_at_utc" in entry:
                raw_ts = entry["last_updated_at_utc"]
                ts_field = f"portfolio_cash[{idx}].last_updated_at_utc"
            elif "timestamp_utc" in entry:
                raw_ts = entry["timestamp_utc"]
                ts_field = f"portfolio_cash[{idx}].timestamp_utc"
            else:
                raw_ts = _UNSPECIFIED
                ts_field = f"portfolio_cash[{idx}].last_updated_at_utc"

            if raw_ts is not _UNSPECIFIED:
                ts_val, _ = resolve_historical_timestamp(
                    raw_ts,
                    ts_field,
                    default_timestamp=default_timestamp,
                    allow_date_only=allow_date_only,
                    allow_naive_utc=allow_naive_utc,
                )
            elif default_timestamp:
                ts_val, _ = resolve_historical_timestamp(
                    _UNSPECIFIED,
                    ts_field,
                    default_timestamp=default_timestamp,
                    allow_date_only=allow_date_only,
                    allow_naive_utc=allow_naive_utc,
                )
            elif require_timestamp:
                raise MigrationValidationError(
                    f"{ts_field}: historical timestamp is missing; migration cannot fabricate provenance"
                )
            else:
                ts_val = None
            rep_items.append(("portfolio_cash", (acc, curr), {
                "account_id": acc,
                "currency": curr,
                "amount": amt,
                "raw_amount": entry.get("amount"),
                "last_updated_at_utc": ts_val,
                "timestamp_utc": ts_val,
            }))

    # 2. Scalar cash (unallocated_cash_eur, unallocated_cash_usd)
    for curr, field_key in [("EUR", "unallocated_cash_eur"), ("USD", "unallocated_cash_usd")]:
        val = data.get(field_key)
        if val is None and isinstance(data.get("mutable_state"), dict):
            val = data["mutable_state"].get(field_key)
        if val is not None:
            amt = parse_financial_decimal(val, field_key, max_scale=2)
            if default_timestamp:
                ts_val, _ = resolve_historical_timestamp(
                    _UNSPECIFIED,
                    field_key,
                    default_timestamp=default_timestamp,
                    allow_date_only=allow_date_only,
                    allow_naive_utc=allow_naive_utc,
                )
            elif require_timestamp:
                raise MigrationValidationError(
                    f"{field_key}: historical timestamp is missing; migration cannot fabricate provenance"
                )
            else:
                ts_val = None
            rep_items.append((field_key, (default_account_id, curr), {
                "account_id": default_account_id,
                "currency": curr,
                "amount": amt,
                "raw_amount": val,
                "last_updated_at_utc": ts_val,
                "timestamp_utc": ts_val,
            }))

    for rep_name, key, item in rep_items:
        if key not in records_by_key:
            records_by_key[key] = item
            sources_by_key[key] = [rep_name]
        else:
            prev = records_by_key[key]
            prev_source = sources_by_key[key][0]

            if records_equal(prev, item, CASH_EQUALITY_FIELDS):
                if discrepancies is not None:
                    discrepancies.append({
                        "domain": "portfolio_cash",
                        "key": key,
                        "type": "EXACT_DUPLICATE_DEDUPLICATED",
                        "message": f"Exact duplicate cash record for {key[0]}/{key[1]} deduplicated.",
                    })
                continue

            # Compare amount
            if Decimal(str(prev["amount"])) != Decimal(str(item["amount"])):
                raise MigrationConflictError(
                    f"Conflicting duplicate cash record for {key[0]}/{key[1]} in {source_name}: "
                    f"existing {prev} != incoming {item} (representation '{prev_source}' amount {prev['amount']} != '{rep_name}' {item['amount']})"
                )

            # Compare timestamp
            prev_ts = prev.get("timestamp_utc")
            item_ts = item.get("timestamp_utc")
            if prev_ts is not None and item_ts is not None and prev_ts != item_ts:
                raise MigrationConflictError(
                    f"Conflicting duplicate cash record for {key[0]}/{key[1]} in {source_name}: "
                    f"existing {prev} != incoming {item} (representation '{prev_source}' timestamp '{prev_ts}' != '{rep_name}' '{item_ts}')"
                )

            prev["timestamp_utc"] = prev_ts if prev_ts is not None else item_ts
            prev["last_updated_at_utc"] = prev["timestamp_utc"]
            sources_by_key[key].append(rep_name)

    return sorted(records_by_key.values(), key=lambda x: (x["account_id"], x["currency"]))
