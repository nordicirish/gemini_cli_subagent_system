"""
Shared Persistence Contracts and Abstract Adapter Interface for GEM.
Governed by Stage 2 Architecture and Agent Governance Framework.

Defines the narrow, application-level persistence contracts needed by:
- CLI (python/main.py, python/tools.py)
- Web Server (python/web_server.py)
- Background Telemetry & Workers (python/fetch_stocks.py)

Confines backend-specific implementation details (JSON file I/O vs. SQLite
repositories and UnitOfWork) behind a uniform, inspectable interface.
"""

import json
import math
import re
from abc import ABC, abstractmethod
from decimal import Decimal, localcontext
from typing import Any, Dict, List, Optional, Union


_CANONICAL_DECIMAL_RE = re.compile(r"^[+-]?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_MAX_FINANCIAL_DIGITS = 28


# ---------------------------------------------------------------------------
# Numeric Validation Utilities
# ---------------------------------------------------------------------------

def validate_financial_decimal(
    val: Any,
    field_name: str,
    max_scale: int = 4,
    allow_zero: bool = True,
    non_negative: bool = True,
) -> Decimal:
    """
    Validates that a financial value is an exact finite Decimal.

    Rejects booleans, raw binary floats, None, custom objects (such as __float__
    wrappers), non-canonical/whitespace/scientific strings, non-finite values
    (NaN, +Inf, -Inf), excessive precision (>28 digits), excessive fractional
    scale (>max_scale), and disallowed negative or zero values fail-closed with
    PersistenceValidationError.
    """
    if isinstance(val, bool):
        raise PersistenceValidationError(
            f"Boolean values not allowed for financial field {field_name}"
        )
    if isinstance(val, float):
        raise PersistenceValidationError(
            f"Binary float not allowed at financial boundary for {field_name}: {val!r}"
        )
    if val is None or not isinstance(val, (Decimal, int, str)):
        raise PersistenceValidationError(
            f"Unsupported type {type(val).__name__} for financial field {field_name}; "
            "expected Decimal, int, or canonical numeric string"
        )

    if isinstance(val, str):
        if not val or val != val.strip() or not _CANONICAL_DECIMAL_RE.match(val):
            raise PersistenceValidationError(
                f"Invalid canonical numeric string for financial field {field_name}: {val!r}"
            )
        dec = Decimal(val)
    elif isinstance(val, int):
        dec = Decimal(val)
    else:
        dec = val

    if not dec.is_finite():
        raise PersistenceValidationError(
            f"Non-finite Decimal value not allowed for financial field {field_name}: {val!r}"
        )

    _sign, digits, exponent = dec.as_tuple()
    if dec != Decimal("0"):
        effective_integer_digits = max(0, len(digits) + exponent)
        if effective_integer_digits > _MAX_FINANCIAL_DIGITS:
            raise PersistenceValidationError(
                f"Precision exceeds {_MAX_FINANCIAL_DIGITS} digits for financial field {field_name}: {val!r}"
            )

    scale = -exponent if isinstance(exponent, int) and exponent < 0 else 0
    if scale > max_scale:
        raise PersistenceValidationError(
            f"Scale {scale} exceeds maximum allowed scale {max_scale} for financial field {field_name}: {val!r}"
        )

    if non_negative and dec < Decimal("0"):
        raise PersistenceValidationError(
            f"Negative value not allowed for financial field {field_name}: {dec}"
        )

    if not allow_zero and dec == Decimal("0"):
        raise PersistenceValidationError(
            f"Zero value not allowed for financial field {field_name}"
        )

    if dec == Decimal("0"):
        if scale > 0:
            return Decimal("0").quantize(Decimal(10) ** -scale)
        return Decimal("0")

    return dec


def format_financial_decimal(val: Decimal, min_scale: int = 0) -> str:
    """Formats a finite Decimal as a canonical plain decimal string without scientific notation."""
    if not isinstance(val, Decimal) or not val.is_finite():
        raise PersistenceValidationError(f"Expected finite Decimal to format, got {val!r}")
    normalized = Decimal("0") if val == Decimal("0") else val
    _sign, digits, exponent = normalized.as_tuple()
    if normalized != Decimal("0"):
        effective_integer_digits = max(0, len(digits) + exponent)
        if effective_integer_digits > _MAX_FINANCIAL_DIGITS:
            raise PersistenceValidationError(
                f"Precision exceeds {_MAX_FINANCIAL_DIGITS} digits for financial value: {val!r}"
            )

    current_scale = -exponent if isinstance(exponent, int) and exponent < 0 else 0
    if min_scale > current_scale:
        required_precision = max(
            _MAX_FINANCIAL_DIGITS,
            len(digits) + max(exponent, 0) + min_scale + 1,
        )
        with localcontext() as ctx:
            ctx.prec = required_precision
            normalized = normalized.quantize(Decimal(10) ** -min_scale)
    elif normalized == Decimal("0") and current_scale > 0:
        normalized = Decimal("0").quantize(Decimal(10) ** -current_scale)
    return f"{normalized:f}"


def _serialize_json_node(node: Any) -> str:
    if isinstance(node, Decimal):
        return format_financial_decimal(node)
    if isinstance(node, dict):
        parts = []
        for k, v in node.items():
            key_str = k if isinstance(k, str) else str(k)
            parts.append(f"{json.dumps(key_str)}:{_serialize_json_node(v)}")
        return "{" + ",".join(parts) + "}"
    if isinstance(node, (list, tuple)):
        return "[" + ",".join(_serialize_json_node(item) for item in node) + "]"
    return json.dumps(node, allow_nan=False)


def serialize_json_with_exact_decimals(data: Any) -> str:
    """
    Serializes structured API payloads to JSON while emitting Decimal values as
    exact JSON numeric literals without binary float conversion.
    """
    return _serialize_json_node(data)


def validate_finite_float(
    val: Any,
    field_name: str,
    allow_zero: bool = True,
    non_negative: bool = True,
) -> float:
    """
    Validates that a financial numeric value is a finite float.
    Explicitly enforces an accepted numeric type allowlist (int, float, Decimal).
    Rejects booleans, strings, custom objects implementing __float__(),
    non-numeric types, non-finite values (NaN, +inf, -inf),
    and conversion overflow fail-closed with PersistenceValidationError.
    Preserves legitimate zero values (when allow_zero=True) and finite positive values.
    """
    if isinstance(val, bool):
        raise PersistenceValidationError(f"Boolean values not allowed for financial field {field_name}")
    if isinstance(val, str):
        raise PersistenceValidationError(f"String values not allowed for financial field {field_name}")
    if not isinstance(val, (int, float, Decimal)):
        raise PersistenceValidationError(
            f"Unsupported type {type(val).__name__} for financial field {field_name}; expected int, float, or Decimal"
        )

    try:
        num = float(val)
    except (ValueError, TypeError, OverflowError) as e:
        raise PersistenceValidationError(f"Invalid numeric value for {field_name}: {val}") from e

    if not math.isfinite(num):
        raise PersistenceValidationError(f"Non-finite numeric value not allowed for {field_name}: {num}")

    if non_negative and num < 0.0:
        raise PersistenceValidationError(f"Negative value not allowed for {field_name}: {num}")

    if not allow_zero and num == 0.0:
        raise PersistenceValidationError(f"Zero value not allowed for {field_name}")

    return num


# ---------------------------------------------------------------------------
# Persistence Exception Hierarchy
# ---------------------------------------------------------------------------

class PersistenceError(Exception):
    """Base exception for application persistence errors."""
    pass


class PersistenceValidationError(PersistenceError):
    """Raised when incoming payload or record fails schema/domain validation."""
    pass


class PersistenceAuthorizationError(PersistenceError):
    """Raised when an operation violates financial or role mutation boundaries."""
    pass


class PersistenceNotFoundError(PersistenceError):
    """Raised when an expected persistence entity or file does not exist."""
    pass


class PersistenceIntegrityError(PersistenceError):
    """Raised when persistence state integrity is compromised or poisoned."""
    pass


# ---------------------------------------------------------------------------
# Canonical Top-Level SSoT Keys
# ---------------------------------------------------------------------------

CANONICAL_SSOT_KEYS = frozenset({
    "state_context",
    "portfolio_snapshot",
    "proposed_portfolio_snapshot",
    "forensic_intelligence",
    "runtime_flags",
    "macro_calendar_shield",
    "active_orders",
    "fin_account_gate",
    "registry_pointers",
    "overnight_posture",
    "strategy_timing",
    "lesson_integration",
    "immutable_background",
    "mutable_state",
    "unallocated_cash_eur",
    "unallocated_cash_usd",
    "portfolio_total_value_usd",
    "portfolio_total_value_eur",
    "base_currency",
    "exchange_rate",
    "scouted_assets_tracked",
    "scout_categories",
    "watched_tickers",
    "watchlist",
    "risk_metrics",
    "directive",
    "EXECUTION_PAYLOAD",
    "_meta",
    "_projection_metadata",
})


# ---------------------------------------------------------------------------
# Abstract Persistence Adapter Interface
# ---------------------------------------------------------------------------

class PersistenceAdapter(ABC):
    """
    Abstract interface defining authoritative application state persistence operations.
    Implemented by:
    - JsonPersistenceAdapter (default runtime)
    - SqlitePersistenceAdapter (controlled integration / test-isolated)
    """

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Returns the canonical backend name ('json' or 'sqlite')."""
        pass

    # --- Portfolio State & SSoT Operations ---

    @abstractmethod
    def get_ssot_data(self) -> Dict[str, Any]:
        """Returns the authoritative SSoT state dictionary."""
        pass

    @abstractmethod
    def get_portfolio_basket(self) -> Dict[str, Any]:
        """
        Returns structured portfolio state with exact Decimal financial fields:
        {
            "portfolio": [{"ticker": str, "shares": Decimal, "wac": Decimal}, ...],
            "unallocated_cash_eur": Decimal,
            "unallocated_cash_usd": Decimal,
            "eurusd_rate": float
        }
        """
        pass

    @abstractmethod
    def save_portfolio_basket(
        self,
        portfolio: List[Dict[str, Any]],
        unallocated_cash_eur: Union[Decimal, int, str],
        unallocated_cash_usd: Union[Decimal, int, str] = Decimal("0.00"),
        caller_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Atomically saves the portfolio holdings and cash balances.
        Returns the updated basket dictionary.
        """
        pass

    @abstractmethod
    def update_ssot_payload(
        self,
        payload: Union[str, Dict[str, Any]],
        caller_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Merges an EXECUTION_PAYLOAD or dictionary into authoritative SSoT state,
        preserving externally managed sections and routing embedded trade lessons.
        """
        pass

    @abstractmethod
    def get_ssot_tickers(self) -> List[str]:
        """Returns uppercase tickers actively monitored in SSoT (portfolio + watchlist + scouts)."""
        pass

    @abstractmethod
    def reconcile_ssot_format(self) -> None:
        """Ensures internal layer consistency between root keys and mutable_state."""
        pass

    # --- Watchlist & Scout Operations ---

    @abstractmethod
    def get_watchlist(self) -> List[str]:
        """Returns active watched ticker symbols."""
        pass

    @abstractmethod
    def save_watchlist(
        self,
        watchlist: Union[List[str], Dict[str, Any]],
        caller_role: Optional[str] = None,
    ) -> List[str]:
        """Atomically updates the active watched ticker list."""
        pass

    @abstractmethod
    def get_scout_categories(self) -> Dict[str, Any]:
        """Returns configured scout categories and their associated tickers."""
        pass

    @abstractmethod
    def save_scout_categories(
        self,
        categories: Union[Dict[str, Any], List[Any]],
        caller_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Atomically updates scout categories."""
        pass

    # --- Trade Lessons Operations ---

    @abstractmethod
    def get_trade_lessons(self) -> List[Dict[str, Any]]:
        """Returns all persisted trade lessons."""
        pass

    @abstractmethod
    def save_trade_lessons(
        self,
        lessons: Union[List[Dict[str, Any]], Dict[str, Any], str],
        caller_role: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Appends or updates trade lessons."""
        pass

    # --- Decision Log Operations ---

    @abstractmethod
    def get_decision_log(self) -> List[Dict[str, Any]]:
        """Returns the chronological decision history records."""
        pass

    @abstractmethod
    def append_decision_log(
        self,
        entry: Dict[str, Any],
        prepend: bool = False,
        caller_role: Optional[str] = None,
    ) -> None:
        """Appends (or prepends) a council decision record."""
        pass

    @abstractmethod
    def append_decision_log_from_payload(
        self,
        payload: Union[str, Dict[str, Any]],
        caller_role: Optional[str] = None,
    ) -> bool:
        """Extracts and logs individual decisions from an incoming payload turn."""
        pass

    @abstractmethod
    def clear_decision_log(self, caller_role: Optional[str] = None) -> None:
        """Wipes the decision log history."""
        pass

    # --- Lifecycle Operations ---

    @abstractmethod
    def close(self) -> None:
        """Disposes resources and connections cleanly."""
        pass

