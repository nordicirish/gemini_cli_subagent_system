"""
Repository Factory for Dependency Injection and Isolation Enforcement.
Governed by Stage 1.4 Architecture and Agent Governance Framework.

Provides:
- Controlled, explicit instantiation of repository instances.
- Prevention of mixing core financial and reconstructible market-data cache components.
- Seamless creation of UnitOfWork instances.
- Zero side-effects or file creation upon module import or factory initialization.
"""

import sqlite3
from typing import Optional

from python.database.manager import DatabaseManager
from python.database.repositories.base import RepositoryError, ValidationError
from python.database.repositories.financial import (
    CashRepository,
    PositionRepository,
    WatchlistRepository,
)
from python.database.repositories.governance import (
    ScoutCategoryRepository,
    TradeLessonRepository,
    DecisionLogRepository,
    OwnerApprovalRepository,
)
from python.database.repositories.audit import (
    AuditEventRepository,
    FinancialChangelogRepository,
)
from python.database.repositories.cache import DailyBarRepository
from python.database.repositories.unit_of_work import UnitOfWork


class RepositoryFactory:
    """
    Factory for instantiating repositories with explicit connection or manager bindings.
    """

    def __init__(
        self,
        core_manager: DatabaseManager,
        cache_manager: Optional[DatabaseManager] = None,
    ):
        if core_manager is None:
            raise ValueError("core_manager cannot be None")
        self._core_manager = core_manager
        self._cache_manager = cache_manager

    @property
    def core_manager(self) -> DatabaseManager:
        """The primary core financial database manager."""
        return self._core_manager

    @property
    def cache_manager(self) -> Optional[DatabaseManager]:
        """The market-data cache database manager, if configured."""
        return self._cache_manager

    def create_unit_of_work(
        self,
        connection: Optional[sqlite3.Connection] = None,
        auto_commit: bool = True,
    ) -> UnitOfWork:
        """Creates a UnitOfWork bound to the core financial database."""
        return UnitOfWork(
            db_manager=self._core_manager,
            connection=connection,
            auto_commit=auto_commit,
        )

    def create_cash_repository(self, conn: sqlite3.Connection) -> CashRepository:
        """Creates a CashRepository on the supplied core database connection."""
        self._verify_not_cache_connection(conn)
        return CashRepository(conn)

    def create_position_repository(self, conn: sqlite3.Connection) -> PositionRepository:
        """Creates a PositionRepository on the supplied core database connection."""
        self._verify_not_cache_connection(conn)
        return PositionRepository(conn)

    def create_watchlist_repository(self, conn: sqlite3.Connection) -> WatchlistRepository:
        """Creates a WatchlistRepository on the supplied core database connection."""
        self._verify_not_cache_connection(conn)
        return WatchlistRepository(conn)

    def create_scout_category_repository(self, conn: sqlite3.Connection) -> ScoutCategoryRepository:
        """Creates a ScoutCategoryRepository on the supplied core database connection."""
        self._verify_not_cache_connection(conn)
        return ScoutCategoryRepository(conn)

    def create_trade_lesson_repository(self, conn: sqlite3.Connection) -> TradeLessonRepository:
        """Creates a TradeLessonRepository on the supplied core database connection."""
        self._verify_not_cache_connection(conn)
        return TradeLessonRepository(conn)

    def create_decision_log_repository(self, conn: sqlite3.Connection) -> DecisionLogRepository:
        """Creates a DecisionLogRepository on the supplied core database connection."""
        self._verify_not_cache_connection(conn)
        return DecisionLogRepository(conn)

    def create_financial_changelog_repository(
        self, conn: sqlite3.Connection
    ) -> FinancialChangelogRepository:
        """Creates a FinancialChangelogRepository on the supplied core database connection."""
        self._verify_not_cache_connection(conn)
        return FinancialChangelogRepository(conn)

    def create_owner_approval_repository(self, conn: sqlite3.Connection) -> OwnerApprovalRepository:
        """Creates an OwnerApprovalRepository on the supplied core database connection."""
        self._verify_not_cache_connection(conn)
        return OwnerApprovalRepository(conn)

    def create_audit_event_repository(self, conn: sqlite3.Connection) -> AuditEventRepository:
        """Creates an AuditEventRepository on the supplied core database connection."""
        self._verify_not_cache_connection(conn)
        return AuditEventRepository(conn)

    def create_daily_bar_repository(self, conn: sqlite3.Connection) -> DailyBarRepository:
        """Creates a DailyBarRepository on the supplied cache database connection."""
        return DailyBarRepository(conn)

    @staticmethod
    def _verify_not_cache_connection(conn: sqlite3.Connection) -> None:
        """Verifies that a core repository is not accidentally bound to a cache-only database."""
        cur = conn.cursor()
        cur.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='cache_metadata'"
        )
        is_cache_db = cur.fetchone()[0] > 0
        cur.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='portfolio_cash'"
        )
        has_core_tables = cur.fetchone()[0] > 0
        if is_cache_db and not has_core_tables:
            raise ValidationError(
                "Cross-database boundary violation: Core financial repository cannot be bound to "
                "the market-data cache database."
            )

