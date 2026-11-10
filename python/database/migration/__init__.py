"""
Isolated Offline Legacy JSON Migration & Parity Verification Infrastructure.
Governed by Stage 1.5-D Directive and Agent Governance Framework.
"""

from .models import (
    TimestampProvenance,
    MigrationError,
    MigrationSafetyError,
    MigrationValidationError,
    MigrationConflictError,
    DiscrepancyType,
    ParityDiscrepancy,
    ParitySummary,
    ParityResult,
    MigrationSource,
    MigrationPlan,
    MigrationResult,
)

from .contract import (
    MIGRATION_CONTRACT_VERSION,
    SQLITE_OWNED_DOMAINS,
    EXTERNALLY_MANAGED_SECTIONS,
    parse_financial_decimal,
    parse_ticker,
    parse_iso_timestamp,
    validate_migration_destination,
    extract_rule_code_and_directive,
)

from .importer import LegacyJsonImporter
from .parity import SSoTParityVerifier

__all__ = [
    "MIGRATION_CONTRACT_VERSION",
    "SQLITE_OWNED_DOMAINS",
    "EXTERNALLY_MANAGED_SECTIONS",
    "TimestampProvenance",
    "MigrationError",
    "MigrationSafetyError",
    "MigrationValidationError",
    "MigrationConflictError",
    "DiscrepancyType",
    "ParityDiscrepancy",
    "ParitySummary",
    "ParityResult",
    "MigrationSource",
    "MigrationPlan",
    "MigrationResult",
    "parse_financial_decimal",
    "parse_ticker",
    "parse_iso_timestamp",
    "validate_migration_destination",
    "extract_rule_code_and_directive",
    "LegacyJsonImporter",
    "SSoTParityVerifier",
]

