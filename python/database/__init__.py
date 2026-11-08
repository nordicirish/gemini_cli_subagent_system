"""
Isolated SQLite Core Database Infrastructure
Governed by Stage 1.3 Directive and Architecture Assessments (Stage 1.2 / 1.2.1).

This package provides isolated, testable database components:
- DatabaseManager: Connection lifecycle, WAL configuration, profile management, transactions.
- SchemaMigrator: Versioned DDL deployment, exact decimal constraints, integrity checks.
- BackupManager: SQLite Online Backup API integration, integrity checks, restore verification.

NOTE: This infrastructure is completely decoupled from live application state in Stage 1.3.
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
    CURRENT_SCHEMA_VERSION,
)

from .backup import (
    BackupManager,
    BackupError,
    BackupIntegrityError,
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
    "CURRENT_SCHEMA_VERSION",
    "BackupManager",
    "BackupError",
    "BackupIntegrityError",
]

