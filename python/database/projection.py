"""
Compatibility shim re-exporting from python.database.projections.
Governed by Stage 1.5-C Architecture.
"""

from python.database.projections import (
    PROJECTION_FORMAT_VERSION,
    SQLITE_OWNED_TOP_LEVEL_KEYS,
    SQLITE_OWNED_MUTABLE_STATE_KEYS,
    normalize_projection_value,
    serialize_projection,
    atomic_write_json,
    SSoTProjectionGenerator,
    TransactionIntegrityError,
)

__all__ = [
    "PROJECTION_FORMAT_VERSION",
    "SQLITE_OWNED_TOP_LEVEL_KEYS",
    "SQLITE_OWNED_MUTABLE_STATE_KEYS",
    "normalize_projection_value",
    "serialize_projection",
    "atomic_write_json",
    "SSoTProjectionGenerator",
    "TransactionIntegrityError",
]

