"""
Isolated SQLite Core Database Infrastructure
Governed by Stage 1.3 Directive and Architecture Assessments (Stage 1.2 / 1.2.1 / 1.3.1).

This package provides isolated, testable database components:
- DatabaseManager: Connection lifecycle, WAL configuration, profile management, transactions.
- SchemaMigrator: Versioned DDL deployment, exact decimal constraints, integrity checks.
- BackupManager: SQLite Online Backup API integration, integrity checks, restore verification.

NOTE: This infrastructure is completely decoupled from live application state in Stage 1.3.1.
No production database files are created, and no live state files are migrated.
"""

from .manager import (
    DatabaseManager,
    DatabaseProfile,
    PRIMARY_FINANCIAL_PROFILE,
    MARKET_DATA_CACHE_PROFILE,
    DatabaseError,
    DatabaseConnectionError,
    DatabaseConfigurationError,
    DatabaseBusyError,
    DatabaseCorruptionError,
    DatabaseTransactionError,
)

from .schema import (
    SchemaMigrator,
    SchemaError,
    SchemaVersionMismatchError,
    SchemaIntegrityError,
    CURRENT_SCHEMA_VERSION,
)

from .backup import (
    BackupManager,
    BackupError,
    BackupDestinationExistsError,
    BackupIntegrityError,
    BackupQuiescenceError,
    BackupRecoveryError,
    BackupCleanupError,
    RestoreLockBusyError,
    DatabaseRestoreLock,
    BackupResult,
)

from .repositories import (
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
    CashRepository,
    PositionRepository,
    WatchlistRepository,
    ScoutCategoryRepository,
    TradeLessonRepository,
    DecisionLogRepository,
    OwnerApprovalRepository,
    AuditEventRepository,
    FinancialChangelogRepository,
    DailyBarRepository,
    UnitOfWork,
    RepositoryFactory,
)

__all__ = [
    "DatabaseManager",
    "DatabaseProfile",
    "PRIMARY_FINANCIAL_PROFILE",
    "MARKET_DATA_CACHE_PROFILE",
    "DatabaseError",
    "DatabaseConnectionError",
    "DatabaseConfigurationError",
    "DatabaseBusyError",
    "DatabaseCorruptionError",
    "DatabaseTransactionError",
    "SchemaMigrator",
    "SchemaError",
    "SchemaVersionMismatchError",
    "SchemaIntegrityError",
    "CURRENT_SCHEMA_VERSION",
    "BackupManager",
    "BackupError",
    "BackupDestinationExistsError",
    "BackupIntegrityError",
    "BackupQuiescenceError",
    "BackupRecoveryError",
    "BackupCleanupError",
    "RestoreLockBusyError",
    "DatabaseRestoreLock",
    "BackupResult",
    "RepositoryError",
    "EntityNotFoundError",
    "DuplicateEntityError",
    "IntegrityConflictError",
    "ValidationError",
    "ConcurrencyConflictError",
    "AppendOnlyViolationError",
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
    "UnitOfWork",
    "RepositoryFactory",
]
