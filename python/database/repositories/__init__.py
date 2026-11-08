"""
SQLite Repository Layer Package.
Governed by Stage 1.4 Architecture and Agent Governance Framework.

Exports:
- Base exceptions and domain dataclasses.
- Concrete repositories for all 10 schema entities.
- UnitOfWork transactional coordinator.
- RepositoryFactory for dependency injection.
"""

from python.database.repositories.base import (
    RepositoryError,
    EntityNotFoundError,
    DuplicateEntityError,
    IntegrityConflictError,
    ValidationError,
    ConcurrencyConflictError,
    AppendOnlyViolationError,
    CashBalance,
    PositionRecord,
    WatchlistItem,
    ScoutCategory,
    TradeLesson,
    DecisionRecord,
    FinancialChangelogEntry,
    OwnerApprovalRecord,
    AuditEventRecord,
    DailyBarRecord,
    validate_decimal,
    decimal_to_canonical_str,
    validate_identifier,
    validate_date_str,
    validate_event_id,
    utc_now_iso,
    BaseRepository,
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

from python.database.repositories.cache import (
    DailyBarRepository,
)

from python.database.repositories.unit_of_work import (
    UnitOfWork,
)

from python.database.repositories.factory import (
    RepositoryFactory,
)

__all__ = [
    # Exceptions
    "RepositoryError",
    "EntityNotFoundError",
    "DuplicateEntityError",
    "IntegrityConflictError",
    "ValidationError",
    "ConcurrencyConflictError",
    "AppendOnlyViolationError",
    # Domain Models
    "CashBalance",
    "PositionRecord",
    "WatchlistItem",
    "ScoutCategory",
    "TradeLesson",
    "DecisionRecord",
    "FinancialChangelogEntry",
    "OwnerApprovalRecord",
    "AuditEventRecord",
    "DailyBarRecord",
    # Base and Helpers
    "validate_decimal",
    "decimal_to_canonical_str",
    "validate_identifier",
    "validate_date_str",
    "validate_event_id",
    "utc_now_iso",
    "BaseRepository",
    # Repositories
    "CashRepository",
    "PositionRepository",
    "WatchlistRepository",
    "ScoutCategoryRepository",
    "TradeLessonRepository",
    "DecisionLogRepository",
    "OwnerApprovalRepository",
    "AuditEventRepository",
    "FinancialChangelogRepository",
    "DailyBarRepository",
    # Coordinator & Factory
    "UnitOfWork",
    "RepositoryFactory",
]

