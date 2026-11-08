"""
Unit of Work Coordinator for Atomic Core SQLite Transactions.
Governed by Stage 1.4 Architecture and Agent Governance Framework.

Features:
- Single-connection transaction ownership across multiple core repositories.
- BEGIN IMMEDIATE semantics preventing concurrent writer deadlocks.
- Automatic rollback on exception and deterministic connection cleanup.
- Strict rejection of nested transactions on the same connection.
- Strict isolation preventing market-data cache repositories from participating in core UoW.
"""

import sqlite3
from typing import Optional

from python.database.manager import (
    DatabaseManager,
    DatabaseTransactionError,
    DatabaseBusyError,
    DatabaseCorruptionError,
)
from python.database.repositories.base import RepositoryError
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


class UnitOfWork:
    """
    Unit of Work coordinator for atomic multi-repository operations on the core financial database.
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        connection: Optional[sqlite3.Connection] = None,
        auto_commit: bool = True,
    ):
        self._db_manager = db_manager
        self._external_conn = connection
        self._owns_conn = connection is None
        self._conn: Optional[sqlite3.Connection] = None
        self._in_transaction = False
        self._committed = False
        self._rolled_back = False
        self._auto_commit = auto_commit

        # Repositories (instantiated upon __enter__)
        self._positions: Optional[PositionRepository] = None
        self._cash: Optional[CashRepository] = None
        self._watchlist: Optional[WatchlistRepository] = None
        self._scout_categories: Optional[ScoutCategoryRepository] = None
        self._trade_lessons: Optional[TradeLessonRepository] = None
        self._decision_log: Optional[DecisionLogRepository] = None
        self._financial_changelog: Optional[FinancialChangelogRepository] = None
        self._owner_approvals: Optional[OwnerApprovalRepository] = None
        self._audit_events: Optional[AuditEventRepository] = None

    def __enter__(self) -> "UnitOfWork":
        if self._in_transaction:
            raise RepositoryError("UnitOfWork already active; nested transactions are prohibited.")

        if self._external_conn is not None:
            self._conn = self._external_conn
        else:
            self._conn = self._db_manager.connect()

        if self._conn.in_transaction:
            if self._owns_conn:
                self._conn.close()
                self._conn = None
            raise DatabaseTransactionError(
                "Nested transactions are not supported; transaction already active on connection."
            )

        try:
            self._conn.execute("BEGIN IMMEDIATE;")
        except sqlite3.OperationalError as e:
            err_msg = str(e).lower()
            if self._owns_conn:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
            if "locked" in err_msg or "busy" in err_msg:
                raise DatabaseBusyError(f"Database locked attempting BEGIN IMMEDIATE: {e}") from e
            if "malformed" in err_msg or "corrupt" in err_msg:
                raise DatabaseCorruptionError(f"Database corrupted attempting transaction: {e}") from e
            raise DatabaseTransactionError(f"Failed to begin immediate transaction: {e}") from e

        self._in_transaction = True
        self._committed = False
        self._rolled_back = False

        # Bind repositories to this transaction's connection
        self._positions = PositionRepository(self._conn)
        self._cash = CashRepository(self._conn)
        self._watchlist = WatchlistRepository(self._conn)
        self._scout_categories = ScoutCategoryRepository(self._conn)
        self._trade_lessons = TradeLessonRepository(self._conn)
        self._decision_log = DecisionLogRepository(self._conn)
        self._financial_changelog = FinancialChangelogRepository(self._conn)
        self._owner_approvals = OwnerApprovalRepository(self._conn)
        self._audit_events = AuditEventRepository(self._conn)

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is not None:
                self.rollback()
            else:
                if not self._committed and not self._rolled_back and self._auto_commit:
                    self.commit()
        finally:
            if self._owns_conn and self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
            self._in_transaction = False

    def commit(self) -> None:
        """Commits the current transaction."""
        if not self._in_transaction or self._conn is None:
            raise RepositoryError("No active transaction to commit.")
        if self._rolled_back:
            raise RepositoryError("Cannot commit: transaction has already been rolled back.")
        if self._committed:
            return

        try:
            self._conn.execute("COMMIT;")
            self._committed = True
            self._in_transaction = False
        except Exception as e:
            self.rollback()
            raise DatabaseTransactionError(f"Commit failed: {e}") from e

    def rollback(self) -> None:
        """Rolls back the current transaction."""
        if self._conn is None or self._rolled_back:
            return
        try:
            if self._in_transaction:
                self._conn.execute("ROLLBACK;")
        except Exception:
            pass
        finally:
            self._rolled_back = True
            self._in_transaction = False

    @property
    def positions(self) -> PositionRepository:
        """Portfolio positions repository bound to this unit of work."""
        if self._positions is None:
            raise RepositoryError("UnitOfWork is not active.")
        return self._positions

    @property
    def cash(self) -> CashRepository:
        """Portfolio cash repository bound to this unit of work."""
        if self._cash is None:
            raise RepositoryError("UnitOfWork is not active.")
        return self._cash

    @property
    def watchlist(self) -> WatchlistRepository:
        """Watchlist repository bound to this unit of work."""
        if self._watchlist is None:
            raise RepositoryError("UnitOfWork is not active.")
        return self._watchlist

    @property
    def scout_categories(self) -> ScoutCategoryRepository:
        """Scout category repository bound to this unit of work."""
        if self._scout_categories is None:
            raise RepositoryError("UnitOfWork is not active.")
        return self._scout_categories

    @property
    def trade_lessons(self) -> TradeLessonRepository:
        """Trade lessons repository bound to this unit of work."""
        if self._trade_lessons is None:
            raise RepositoryError("UnitOfWork is not active.")
        return self._trade_lessons

    @property
    def decision_log(self) -> DecisionLogRepository:
        """Decision log repository bound to this unit of work."""
        if self._decision_log is None:
            raise RepositoryError("UnitOfWork is not active.")
        return self._decision_log

    @property
    def financial_changelog(self) -> FinancialChangelogRepository:
        """Financial changelog repository bound to this unit of work."""
        if self._financial_changelog is None:
            raise RepositoryError("UnitOfWork is not active.")
        return self._financial_changelog

    @property
    def owner_approvals(self) -> OwnerApprovalRepository:
        """Owner approvals repository bound to this unit of work."""
        if self._owner_approvals is None:
            raise RepositoryError("UnitOfWork is not active.")
        return self._owner_approvals

    @property
    def audit_events(self) -> AuditEventRepository:
        """Audit events repository bound to this unit of work."""
        if self._audit_events is None:
            raise RepositoryError("UnitOfWork is not active.")
        return self._audit_events

    @property
    def daily_bars(self):
        """Cross-database boundary protection."""
        raise TypeError(
            "Cross-database boundary violation: DailyBarRepository operates strictly on the "
            "reconstructible market-data cache database. It cannot be part of the core financial UnitOfWork."
        )

