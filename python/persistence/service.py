"""
Shared Application Persistence Service for GEM.
Governed by Stage 2 Architecture and Agent Governance Framework.

Provides a unified application-level persistence boundary for CLI, Web, and Daemon.
Delegates to:
- JsonPersistenceAdapter (default runtime)
- SqlitePersistenceAdapter (controlled integration / test isolation)
"""

import threading
from typing import Any, Dict, List, Optional, Union

try:
    from python.database.manager import DatabaseManager
except ImportError:
    from database.manager import DatabaseManager

from .contracts import (
    PersistenceAdapter,
    PersistenceError,
    PersistenceValidationError,
    PersistenceAuthorizationError,
    PersistenceIntegrityError,
)
from .json_adapter import JsonPersistenceAdapter
from .sqlite_adapter import SqlitePersistenceAdapter


class PersistenceService:
    """
    Unified application persistence service coordinating authoritative state operations.
    """

    def __init__(self, adapter: PersistenceAdapter):
        if adapter is None:
            raise ValueError("Persistence adapter cannot be None")
        self._adapter = adapter

    @property
    def backend_name(self) -> str:
        """The active persistence backend name ('json' or 'sqlite')."""
        return self._adapter.backend_name

    @property
    def adapter(self) -> PersistenceAdapter:
        """The underlying adapter instance."""
        return self._adapter

    # -----------------------------------------------------------------------
    # Delegated Contract Methods
    # -----------------------------------------------------------------------

    def get_ssot_data(self) -> Dict[str, Any]:
        return self._adapter.get_ssot_data()

    def get_portfolio_basket(self) -> Dict[str, Any]:
        return self._adapter.get_portfolio_basket()

    def save_portfolio_basket(
        self,
        portfolio: List[Dict[str, Any]],
        unallocated_cash_eur: float,
        unallocated_cash_usd: float = 0.0,
        caller_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._adapter.save_portfolio_basket(
            portfolio=portfolio,
            unallocated_cash_eur=unallocated_cash_eur,
            unallocated_cash_usd=unallocated_cash_usd,
            caller_role=caller_role,
        )

    def update_ssot_payload(
        self,
        payload: Union[str, Dict[str, Any]],
        caller_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._adapter.update_ssot_payload(payload=payload, caller_role=caller_role)

    def get_ssot_tickers(self) -> List[str]:
        return self._adapter.get_ssot_tickers()

    def reconcile_ssot_format(self) -> None:
        return self._adapter.reconcile_ssot_format()

    def get_watchlist(self) -> List[str]:
        return self._adapter.get_watchlist()

    def save_watchlist(
        self,
        watchlist: Union[List[str], Dict[str, Any]],
        caller_role: Optional[str] = None,
    ) -> List[str]:
        return self._adapter.save_watchlist(watchlist=watchlist, caller_role=caller_role)

    def get_scout_categories(self) -> Dict[str, Any]:
        return self._adapter.get_scout_categories()

    def save_scout_categories(
        self,
        categories: Union[Dict[str, Any], List[Any]],
        caller_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._adapter.save_scout_categories(categories=categories, caller_role=caller_role)

    def get_trade_lessons(self) -> List[Dict[str, Any]]:
        return self._adapter.get_trade_lessons()

    def save_trade_lessons(
        self,
        lessons: Union[List[Dict[str, Any]], Dict[str, Any], str],
        caller_role: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return self._adapter.save_trade_lessons(lessons=lessons, caller_role=caller_role)

    def get_decision_log(self) -> List[Dict[str, Any]]:
        return self._adapter.get_decision_log()

    def append_decision_log(
        self,
        entry: Dict[str, Any],
        prepend: bool = False,
        caller_role: Optional[str] = None,
    ) -> None:
        return self._adapter.append_decision_log(entry=entry, prepend=prepend, caller_role=caller_role)

    def append_decision_log_from_payload(
        self,
        payload: Union[str, Dict[str, Any]],
        caller_role: Optional[str] = None,
    ) -> bool:
        return self._adapter.append_decision_log_from_payload(payload=payload, caller_role=caller_role)

    def clear_decision_log(self, caller_role: Optional[str] = None) -> None:
        return self._adapter.clear_decision_log(caller_role=caller_role)

    def close(self) -> None:
        self._adapter.close()


# ---------------------------------------------------------------------------
# Global Singleton Lifecycle Management
# ---------------------------------------------------------------------------

_ACTIVE_PERSISTENCE_SERVICE: Optional[PersistenceService] = None
_PERSISTENCE_LOCK = threading.Lock()


def get_persistence_service() -> PersistenceService:
    """
    Returns the thread-safe active PersistenceService singleton.
    Defaults to JsonPersistenceAdapter on initial construction.
    """
    global _ACTIVE_PERSISTENCE_SERVICE
    if _ACTIVE_PERSISTENCE_SERVICE is None:
        with _PERSISTENCE_LOCK:
            if _ACTIVE_PERSISTENCE_SERVICE is None:
                default_adapter = JsonPersistenceAdapter()
                _ACTIVE_PERSISTENCE_SERVICE = PersistenceService(default_adapter)
    return _ACTIVE_PERSISTENCE_SERVICE


def configure_persistence(
    backend: str = "json",
    db_manager: Optional[DatabaseManager] = None,
    base_dir: Optional[str] = None,
    adapter: Optional[PersistenceAdapter] = None,
) -> PersistenceService:
    """
    Explicitly configures the persistence service backend.
    Used for test isolation or controlled environments.
    """
    global _ACTIVE_PERSISTENCE_SERVICE
    with _PERSISTENCE_LOCK:
        if adapter is not None:
            new_adapter = adapter
        elif backend.lower() == "sqlite":
            if db_manager is None:
                raise ValueError("db_manager is required when configuring SQLite persistence backend.")
            new_adapter = SqlitePersistenceAdapter(db_manager)
        elif backend.lower() == "json":
            new_adapter = JsonPersistenceAdapter(base_dir=base_dir)
        else:
            raise ValueError(f"Unsupported persistence backend: '{backend}'. Must be 'json' or 'sqlite'.")

        # Dispose existing if open
        if _ACTIVE_PERSISTENCE_SERVICE is not None:
            try:
                _ACTIVE_PERSISTENCE_SERVICE.close()
            except Exception:
                pass

        _ACTIVE_PERSISTENCE_SERVICE = PersistenceService(new_adapter)
        return _ACTIVE_PERSISTENCE_SERVICE


def reset_persistence_service() -> None:
    """Disposes and resets the global persistence service singleton to uninitialized."""
    global _ACTIVE_PERSISTENCE_SERVICE
    with _PERSISTENCE_LOCK:
        if _ACTIVE_PERSISTENCE_SERVICE is not None:
            try:
                _ACTIVE_PERSISTENCE_SERVICE.close()
            except Exception:
                pass
            _ACTIVE_PERSISTENCE_SERVICE = None

