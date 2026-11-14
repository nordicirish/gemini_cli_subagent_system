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

import math
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union


# ---------------------------------------------------------------------------
# Numeric Validation Utilities
# ---------------------------------------------------------------------------

def validate_finite_float(
    val: Any,
    field_name: str,
    allow_zero: bool = True,
    non_negative: bool = True,
) -> float:
    """
    Validates that a financial numeric value is a finite float.
    Rejects NaN, +Infinity, -Infinity, negative values (when non_negative=True),
    and non-numeric types fail-closed with PersistenceValidationError.
    Preserves legitimate zero values (when allow_zero=True) and finite positive values.
    """
    try:
        num = float(val)
    except (ValueError, TypeError) as e:
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
        Returns structured portfolio state:
        {
            "portfolio": [{"ticker": str, "shares": float, "wac": float}, ...],
            "unallocated_cash_eur": float,
            "unallocated_cash_usd": float,
            "eurusd_rate": float
        }
        """
        pass

    @abstractmethod
    def save_portfolio_basket(
        self,
        portfolio: List[Dict[str, Any]],
        unallocated_cash_eur: float,
        unallocated_cash_usd: float = 0.0,
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

