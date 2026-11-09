"""
Unit of Work Coordinator for Atomic Core SQLite Transactions.
Governed by Stage 1.4.1 Architecture and Agent Governance Framework.

Features:
- Single-connection transaction ownership across multiple core repositories.
- BEGIN IMMEDIATE semantics preventing concurrent writer deadlocks.
- Automatic rollback on exception and deterministic connection cleanup.
- R6: Strict lifecycle enforcement:
  - Repositories accessible only while UnitOfWork is active.
  - Retained repository references fail-closed after commit, rollback, or exit.
  - Safe exit policy for auto_commit=False: rolls back uncommitted transaction so no dangling lock remains.
  - Prevents re-entry of completed UnitOfWork instances.
  - Preserves external connection ownership without leaving open transactions.
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
from python.database.repositories.base import (
    RepositoryError,
    TransactionIntegrityError,
    get_db_revision,
)
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
        self._is_active = False
        self._completed = False
        self._committed = False
        self._rolled_back = False
        self._auto_commit = auto_commit
        self._is_poisoned = False
        self._poison_reason: Optional[str] = None
        self._poison_cause: Optional[Exception] = None
        self._conn_unsafe = False

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

    @property
    def is_active(self) -> bool:
        """Returns True if the Unit of Work is actively managing an open transaction."""
        return self._is_active and self._in_transaction and not self._is_poisoned

    @property
    def is_poisoned(self) -> bool:
        """Returns True if the transaction has been poisoned by a recovery or savepoint failure (R1/R2)."""
        return self._is_poisoned

    @property
    def poison_reason(self) -> Optional[str]:
        """Returns the diagnostic reason for transaction poisoning."""
        return self._poison_reason

    @property
    def is_conn_unsafe(self) -> bool:
        """Returns True if the underlying connection is in an unsafe state due to failed rollback."""
        return self._conn_unsafe

    def poison(
        self,
        reason: str,
        cause: Optional[Exception] = None,
        original_error: Optional[Exception] = None,
    ) -> None:
        """
        Marks the UnitOfWork and its active transaction as poisoned (R1/R2).
        Attempts full outer transaction rollback to protect the connection.
        If outer rollback also fails, marks the connection as unsafe.
        """
        self._is_poisoned = True
        self._poison_reason = reason
        self._poison_cause = cause

        rollback_failed = False
        rollback_err = None
        if self._in_transaction and self._conn is not None:
            try:
                self._conn.execute("ROLLBACK;")
                self._in_transaction = False
                self._rolled_back = True
            except Exception as e:
                rollback_failed = True
                rollback_err = e
                self._conn_unsafe = True
                self._in_transaction = False
                self._rolled_back = True

        self._clear_repositories()

        if rollback_failed:
            tie = TransactionIntegrityError(
                f"CRITICAL: Transaction poisoned ({reason}) and full outer rollback also failed: {rollback_err}. "
                f"Database connection is in an unsafe state."
            )
            if cause is not None:
                tie.__cause__ = cause
            raise tie

    def _clear_repositories(self) -> None:
        """Clears repository references on UoW completion (R6)."""
        self._positions = None
        self._cash = None
        self._watchlist = None
        self._scout_categories = None
        self._trade_lessons = None
        self._decision_log = None
        self._financial_changelog = None
        self._owner_approvals = None
        self._audit_events = None

    def __enter__(self) -> "UnitOfWork":
        if self._completed:
            raise RepositoryError("UnitOfWork has already completed and cannot be re-entered.")
        if self._is_active or self._in_transaction:
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
        self._is_active = True
        self._committed = False
        self._rolled_back = False

        # Bind repositories to this transaction's connection and UoW lifecycle (R6)
        self._positions = PositionRepository(self._conn, uow=self)
        self._cash = CashRepository(self._conn, uow=self)
        self._watchlist = WatchlistRepository(self._conn, uow=self)
        self._scout_categories = ScoutCategoryRepository(self._conn, uow=self)
        self._trade_lessons = TradeLessonRepository(self._conn, uow=self)
        self._decision_log = DecisionLogRepository(self._conn, uow=self)
        self._financial_changelog = FinancialChangelogRepository(self._conn, uow=self)
        self._owner_approvals = OwnerApprovalRepository(self._conn, uow=self)
        self._audit_events = AuditEventRepository(self._conn, uow=self)

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if self._is_poisoned:
                # R1: The UnitOfWork must never automatically commit a poisoned transaction during __exit__().
                if self._in_transaction and self._conn is not None:
                    try:
                        self.rollback()
                    except Exception:
                        self._conn_unsafe = True

                # If caller caught/suppressed the exception inside the block, exc_type is None!
                if exc_type is None:
                    raise TransactionIntegrityError(
                        f"UnitOfWork transaction completed with poisoned state: {self._poison_reason}"
                    ) from self._poison_cause
                return False

            if exc_type is not None:
                try:
                    self.rollback()
                except TransactionIntegrityError as tie:
                    # R2: Chaining diagnostic context; ensure rollback failure is not swallowed
                    raise TransactionIntegrityError(
                        f"CRITICAL: Rollback failed during exception handling: {tie}. "
                        f"Connection is in an uncertain state."
                    ) from exc_val
            else:
                if not self._committed and not self._rolled_back:
                    if self._auto_commit:
                        self.commit()
                    else:
                        # R6: Explicit exit policy for auto_commit=False: roll back uncommitted work
                        # so no open transaction remains dangling on an external connection.
                        self.rollback()
        finally:
            self._is_active = False
            self._completed = True
            self._in_transaction = False
            self._clear_repositories()

            if self._owns_conn and self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    def commit(self) -> None:
        """Commits the current transaction (R6/R2/R1)."""
        if self._is_poisoned:
            raise TransactionIntegrityError(
                f"Cannot commit: UnitOfWork transaction has been poisoned: {self._poison_reason}"
            ) from self._poison_cause
        if self._conn_unsafe:
            raise TransactionIntegrityError(
                "Cannot commit: Database connection is in an unsafe state."
            )
        if not self._is_active or not self._in_transaction or self._conn is None:
            raise RepositoryError("No active transaction to commit.")
        if self._rolled_back:
            raise RepositoryError("Cannot commit: transaction has already been rolled back.")
        if self._committed:
            return

        try:
            self._conn.execute("COMMIT;")
            self._committed = True
            self._in_transaction = False
            self._is_active = False
            self._clear_repositories()
        except Exception as e:
            try:
                self.rollback()
            except TransactionIntegrityError as tie:
                self._conn_unsafe = True
                raise TransactionIntegrityError(
                    f"CRITICAL: Commit failed and subsequent rollback also failed: {tie}. "
                    f"Database connection is in an uncertain state."
                ) from e
            raise DatabaseTransactionError(f"Commit failed: {e}") from e

    def rollback(self) -> None:
        """Rolls back the current transaction (R6/R2)."""
        if self._conn is None or self._rolled_back:
            return
        rollback_error = None
        try:
            if self._in_transaction:
                self._conn.execute("ROLLBACK;")
        except Exception as e:
            rollback_error = e
            self._conn_unsafe = True
            self._is_poisoned = True
        finally:
            self._rolled_back = True
            self._in_transaction = False
            self._is_active = False
            self._clear_repositories()

        if rollback_error is not None:
            raise TransactionIntegrityError(
                f"CRITICAL: Failed to roll back transaction: {rollback_error}. "
                f"The database connection is in an uncertain state and must not be committed."
            ) from rollback_error

    def get_db_revision(self) -> int:
        """Returns the current monotonic database revision for the UnitOfWork connection."""
        if self._is_poisoned:
            raise TransactionIntegrityError(
                f"Cannot read revision: UnitOfWork transaction is poisoned: {self._poison_reason}"
            ) from self._poison_cause
        if not self._is_active or self._conn is None:
            raise RepositoryError("UnitOfWork is not active.")
        return get_db_revision(self._conn)

    @property
    def positions(self) -> PositionRepository:
        """Portfolio positions repository bound to this unit of work."""
        if self._is_poisoned:
            raise TransactionIntegrityError(
                f"Cannot access repository: UnitOfWork transaction is poisoned: {self._poison_reason}"
            ) from self._poison_cause
        if not self._is_active or self._positions is None:
            raise RepositoryError("UnitOfWork is not active.")
        return self._positions

    @property
    def cash(self) -> CashRepository:
        """Portfolio cash repository bound to this unit of work."""
        if self._is_poisoned:
            raise TransactionIntegrityError(
                f"Cannot access repository: UnitOfWork transaction is poisoned: {self._poison_reason}"
            ) from self._poison_cause
        if not self._is_active or self._cash is None:
            raise RepositoryError("UnitOfWork is not active.")
        return self._cash

    @property
    def watchlist(self) -> WatchlistRepository:
        """Watchlist repository bound to this unit of work."""
        if self._is_poisoned:
            raise TransactionIntegrityError(
                f"Cannot access repository: UnitOfWork transaction is poisoned: {self._poison_reason}"
            ) from self._poison_cause
        if not self._is_active or self._watchlist is None:
            raise RepositoryError("UnitOfWork is not active.")
        return self._watchlist

    @property
    def scout_categories(self) -> ScoutCategoryRepository:
        """Scout category repository bound to this unit of work."""
        if self._is_poisoned:
            raise TransactionIntegrityError(
                f"Cannot access repository: UnitOfWork transaction is poisoned: {self._poison_reason}"
            ) from self._poison_cause
        if not self._is_active or self._scout_categories is None:
            raise RepositoryError("UnitOfWork is not active.")
        return self._scout_categories

    @property
    def trade_lessons(self) -> TradeLessonRepository:
        """Trade lessons repository bound to this unit of work."""
        if self._is_poisoned:
            raise TransactionIntegrityError(
                f"Cannot access repository: UnitOfWork transaction is poisoned: {self._poison_reason}"
            ) from self._poison_cause
        if not self._is_active or self._trade_lessons is None:
            raise RepositoryError("UnitOfWork is not active.")
        return self._trade_lessons

    @property
    def decision_log(self) -> DecisionLogRepository:
        """Decision log repository bound to this unit of work."""
        if self._is_poisoned:
            raise TransactionIntegrityError(
                f"Cannot access repository: UnitOfWork transaction is poisoned: {self._poison_reason}"
            ) from self._poison_cause
        if not self._is_active or self._decision_log is None:
            raise RepositoryError("UnitOfWork is not active.")
        return self._decision_log

    @property
    def financial_changelog(self) -> FinancialChangelogRepository:
        """Financial changelog repository bound to this unit of work."""
        if self._is_poisoned:
            raise TransactionIntegrityError(
                f"Cannot access repository: UnitOfWork transaction is poisoned: {self._poison_reason}"
            ) from self._poison_cause
        if not self._is_active or self._financial_changelog is None:
            raise RepositoryError("UnitOfWork is not active.")
        return self._financial_changelog

    @property
    def owner_approvals(self) -> OwnerApprovalRepository:
        """Owner approvals repository bound to this unit of work."""
        if self._is_poisoned:
            raise TransactionIntegrityError(
                f"Cannot access repository: UnitOfWork transaction is poisoned: {self._poison_reason}"
            ) from self._poison_cause
        if not self._is_active or self._owner_approvals is None:
            raise RepositoryError("UnitOfWork is not active.")
        return self._owner_approvals

    @property
    def audit_events(self) -> AuditEventRepository:
        """Audit events repository bound to this unit of work."""
        if self._is_poisoned:
            raise TransactionIntegrityError(
                f"Cannot access repository: UnitOfWork transaction is poisoned: {self._poison_reason}"
            ) from self._poison_cause
        if not self._is_active or self._audit_events is None:
            raise RepositoryError("UnitOfWork is not active.")
        return self._audit_events

    @property
    def daily_bars(self):
        """Cross-database boundary protection."""
        raise TypeError(
            "Cross-database boundary violation: DailyBarRepository operates strictly on the "
            "reconstructible market-data cache database. It cannot be part of the core financial UnitOfWork."
        )
