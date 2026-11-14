"""
GEM Shared Application Persistence Package.
Governed by Stage 2 Architecture and Agent Governance Framework.
"""

import sys

# Ensure module unification between root-relative and package-relative imports
if "python.tool_registry" in sys.modules and "tool_registry" not in sys.modules:
    sys.modules["tool_registry"] = sys.modules["python.tool_registry"]
elif "tool_registry" in sys.modules and "python.tool_registry" not in sys.modules:
    sys.modules["python.tool_registry"] = sys.modules["tool_registry"]

if __name__ == "python.persistence" and "persistence" not in sys.modules:
    sys.modules["persistence"] = sys.modules["python.persistence"]
elif __name__ == "persistence" and "python.persistence" not in sys.modules:
    sys.modules["python.persistence"] = sys.modules["persistence"]

from .contracts import (
    PersistenceAdapter,
    PersistenceError,
    PersistenceValidationError,
    PersistenceAuthorizationError,
    PersistenceIntegrityError,
    PersistenceNotFoundError,
    CANONICAL_SSOT_KEYS,
    validate_finite_float,
)
from .json_adapter import JsonPersistenceAdapter
from .sqlite_adapter import SqlitePersistenceAdapter
from .service import (
    PersistenceService,
    get_persistence_service,
    configure_persistence,
    reset_persistence_service,
)

__all__ = [
    "PersistenceAdapter",
    "PersistenceError",
    "PersistenceValidationError",
    "PersistenceAuthorizationError",
    "PersistenceIntegrityError",
    "PersistenceNotFoundError",
    "CANONICAL_SSOT_KEYS",
    "validate_finite_float",
    "JsonPersistenceAdapter",
    "SqlitePersistenceAdapter",
    "PersistenceService",
    "get_persistence_service",
    "configure_persistence",
    "reset_persistence_service",
]
