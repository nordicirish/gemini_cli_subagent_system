"""
Migration Domain Models, Exception Hierarchy, and Parity Data Structures.
Governed by Stage 1.5-D Architecture and Agent Governance Framework.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from python.database.manager import DatabaseError
from python.database.repositories.base import RepositoryError, ValidationError


# ---------------------------------------------------------------------------
# Migration Exception Hierarchy
# ---------------------------------------------------------------------------

class MigrationError(RepositoryError):
    """Base exception for all offline migration and parity verification errors."""
    pass


class MigrationSafetyError(MigrationError):
    """Raised when an operation targets a protected production database path or violates safety boundaries."""
    pass


class MigrationValidationError(MigrationError):
    """Raised when a legacy JSON document fails contract validation, type conversion, or domain invariants."""
    pass


class MigrationConflictError(MigrationError):
    """Raised when an incoming migration record conflicts with an existing persistent record."""
    pass


# ---------------------------------------------------------------------------
# Parity Discrepancy Classification
# ---------------------------------------------------------------------------

class DiscrepancyType(str, Enum):
    """Semantic classifications for parity comparison results."""
    EXACT_MATCH = "EXACT_MATCH"
    EQUIVALENT_VALUE = "EQUIVALENT_VALUE"
    EXPECTED_EXTERNAL_SECTION = "EXPECTED_EXTERNAL_SECTION"
    MISSING_RECORD = "MISSING_RECORD"
    UNEXPECTED_RECORD = "UNEXPECTED_RECORD"
    FINANCIAL_MISMATCH = "FINANCIAL_MISMATCH"
    TIMESTAMP_MISMATCH = "TIMESTAMP_MISMATCH"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    ORDERING_MISMATCH = "ORDERING_MISMATCH"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class ParityDiscrepancy:
    """Detailed record of a single semantic or representation difference."""
    domain: str
    path: str
    discrepancy_type: str
    legacy_value: Any
    projected_value: Any
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain": self.domain,
            "path": self.path,
            "discrepancy_type": self.discrepancy_type,
            "legacy_value": self._sanitize_value(self.legacy_value),
            "projected_value": self._sanitize_value(self.projected_value),
            "message": self.message,
        }

    @staticmethod
    def _sanitize_value(val: Any) -> Any:
        if isinstance(val, Decimal):
            return str(val)
        return val


@dataclass
class ParitySummary:
    """Aggregate statistics for a parity comparison run."""
    exact_matches: int = 0
    equivalent_values: int = 0
    expected_external_sections: int = 0
    missing_records: int = 0
    unexpected_records: int = 0
    financial_mismatches: int = 0
    timestamp_mismatches: int = 0
    identity_mismatches: int = 0
    unresolved: int = 0

    def to_dict(self) -> Dict[str, int]:
        return {
            "exact_matches": self.exact_matches,
            "equivalent_values": self.equivalent_values,
            "expected_external_sections": self.expected_external_sections,
            "missing_records": self.missing_records,
            "unexpected_records": self.unexpected_records,
            "financial_mismatches": self.financial_mismatches,
            "timestamp_mismatches": self.timestamp_mismatches,
            "identity_mismatches": self.identity_mismatches,
            "unresolved": self.unresolved,
        }


@dataclass
class ParityResult:
    """Complete structured result of an SSoT parity verification execution."""
    schema_version: str = "1.0"
    is_parity: bool = False
    database_revision: int = 0
    source_files: List[str] = field(default_factory=list)
    summary: ParitySummary = field(default_factory=ParitySummary)
    discrepancies: List[ParityDiscrepancy] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "is_parity": self.is_parity,
            "database_revision": self.database_revision,
            "source_files": sorted(self.source_files),
            "summary": self.summary.to_dict(),
            "discrepancies": [d.to_dict() for d in self.discrepancies],
        }


# ---------------------------------------------------------------------------
# Migration Source and Result Models
# ---------------------------------------------------------------------------

@dataclass
class MigrationSource:
    """Represents a discovered and loaded legacy source document."""
    name: str
    data: Dict[str, Any]
    source_path: Optional[Path] = None
    file_sha256: Optional[str] = None


@dataclass
class MigrationPlan:
    """In-memory representation of records planned for import across domains."""
    cash_records: List[Dict[str, Any]] = field(default_factory=list)
    position_records: List[Dict[str, Any]] = field(default_factory=list)
    watchlist_records: List[Dict[str, Any]] = field(default_factory=list)
    scout_categories: List[Dict[str, Any]] = field(default_factory=list)
    trade_lessons: List[Dict[str, Any]] = field(default_factory=list)
    decision_log_records: List[Dict[str, Any]] = field(default_factory=list)
    external_sections: Dict[str, Any] = field(default_factory=dict)
    discrepancies: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def total_records(self) -> int:
        return (
            len(self.cash_records)
            + len(self.position_records)
            + len(self.watchlist_records)
            + len(self.scout_categories)
            + len(self.trade_lessons)
            + len(self.decision_log_records)
        )


@dataclass
class MigrationResult:
    """Outcome of an offline migration execution."""
    success: bool
    database_revision: int
    imported_counts: Dict[str, int]
    skipped_counts: Dict[str, int]
    discrepancies: List[Dict[str, Any]]
    migration_timestamp_utc: str
    destination_path: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "database_revision": self.database_revision,
            "imported_counts": self.imported_counts,
            "skipped_counts": self.skipped_counts,
            "discrepancies": self.discrepancies,
            "migration_timestamp_utc": self.migration_timestamp_utc,
            "destination_path": self.destination_path,
        }

