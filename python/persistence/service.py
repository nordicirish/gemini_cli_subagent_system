"""
Shared Application Persistence Service for GEM.
Governed by Stage 2 Architecture and Agent Governance Framework.

Provides a unified application-level persistence boundary for CLI, Web, and Daemon.
Delegates to:
- JsonPersistenceAdapter (default runtime)
- SqlitePersistenceAdapter (controlled integration / test isolation)
"""

import gc
import hashlib
import os
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional, Union
import uuid

try:
    from python.database.manager import DatabaseManager, PRIMARY_FINANCIAL_PROFILE
    from python.database.schema import SchemaMigrator
    from python.database.projections import SSoTProjectionGenerator
    from python.database.migration.importer import LegacyJsonImporter
    from python.database.migration.parity import SSoTParityVerifier
    from python.database.migration.contract import load_strict_json_file, validate_migration_destination
    from python.database.repositories.base import utc_now_iso
except ImportError:
    from database.manager import DatabaseManager, PRIMARY_FINANCIAL_PROFILE
    from database.schema import SchemaMigrator
    from database.projections import SSoTProjectionGenerator
    from database.migration.importer import LegacyJsonImporter
    from database.migration.parity import SSoTParityVerifier
    from database.migration.contract import load_strict_json_file, validate_migration_destination
    from database.repositories.base import utc_now_iso

from .contracts import (
    PersistenceAdapter,
    PersistenceError,
    PersistenceValidationError,
    PersistenceAuthorizationError,
    PersistenceIntegrityError,
)
from .json_adapter import JsonPersistenceAdapter
from .sqlite_adapter import SqlitePersistenceAdapter


class PersistenceService:
    """
    Unified application persistence service coordinating authoritative state operations.
    """

    def __init__(self, adapter: PersistenceAdapter):
        if adapter is None:
            raise ValueError("Persistence adapter cannot be None")
        self._adapter = adapter

    @property
    def backend_name(self) -> str:
        """The active persistence backend name ('json' or 'sqlite')."""
        return self._adapter.backend_name

    @property
    def adapter(self) -> PersistenceAdapter:
        """The underlying adapter instance."""
        return self._adapter

    # -----------------------------------------------------------------------
    # Delegated Contract Methods
    # -----------------------------------------------------------------------

    def get_ssot_data(self) -> Dict[str, Any]:
        return self._adapter.get_ssot_data()

    def get_portfolio_basket(self) -> Dict[str, Any]:
        return self._adapter.get_portfolio_basket()

    def save_portfolio_basket(
        self,
        portfolio: List[Dict[str, Any]],
        unallocated_cash_eur: float,
        unallocated_cash_usd: float = 0.0,
        caller_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._adapter.save_portfolio_basket(
            portfolio=portfolio,
            unallocated_cash_eur=unallocated_cash_eur,
            unallocated_cash_usd=unallocated_cash_usd,
            caller_role=caller_role,
        )

    def update_ssot_payload(
        self,
        payload: Union[str, Dict[str, Any]],
        caller_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._adapter.update_ssot_payload(payload=payload, caller_role=caller_role)

    def get_ssot_tickers(self) -> List[str]:
        return self._adapter.get_ssot_tickers()

    def reconcile_ssot_format(self) -> None:
        return self._adapter.reconcile_ssot_format()

    def get_watchlist(self) -> List[str]:
        return self._adapter.get_watchlist()

    def save_watchlist(
        self,
        watchlist: Union[List[str], Dict[str, Any]],
        caller_role: Optional[str] = None,
    ) -> List[str]:
        return self._adapter.save_watchlist(watchlist=watchlist, caller_role=caller_role)

    def get_scout_categories(self) -> Dict[str, Any]:
        return self._adapter.get_scout_categories()

    def save_scout_categories(
        self,
        categories: Union[Dict[str, Any], List[Any]],
        caller_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._adapter.save_scout_categories(categories=categories, caller_role=caller_role)

    def get_trade_lessons(self) -> List[Dict[str, Any]]:
        return self._adapter.get_trade_lessons()

    def save_trade_lessons(
        self,
        lessons: Union[List[Dict[str, Any]], Dict[str, Any], str],
        caller_role: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return self._adapter.save_trade_lessons(lessons=lessons, caller_role=caller_role)

    def get_decision_log(self) -> List[Dict[str, Any]]:
        return self._adapter.get_decision_log()

    def append_decision_log(
        self,
        entry: Dict[str, Any],
        prepend: bool = False,
        caller_role: Optional[str] = None,
    ) -> None:
        return self._adapter.append_decision_log(entry=entry, prepend=prepend, caller_role=caller_role)

    def append_decision_log_from_payload(
        self,
        payload: Union[str, Dict[str, Any]],
        caller_role: Optional[str] = None,
    ) -> bool:
        return self._adapter.append_decision_log_from_payload(payload=payload, caller_role=caller_role)

    def clear_decision_log(self, caller_role: Optional[str] = None) -> None:
        return self._adapter.clear_decision_log(caller_role=caller_role)

    def close(self) -> None:
        self._adapter.close()


# ---------------------------------------------------------------------------
# Global Singleton Lifecycle Management
# ---------------------------------------------------------------------------

_ACTIVE_PERSISTENCE_SERVICE: Optional[PersistenceService] = None
_PERSISTENCE_LOCK = threading.Lock()


def get_persistence_service(repo_root: Optional[Union[str, Path]] = None) -> PersistenceService:
    """
    Returns the thread-safe active PersistenceService singleton.
    In Stage 3.2:
    - If context/gem.db exists, activates SqlitePersistenceAdapter with PRIMARY_FINANCIAL_PROFILE.
    - If SQLite fails (corrupt, unavailable), fails closed with PersistenceIntegrityError/PersistenceError.
      Silent fallback to JSON or default empty portfolios is strictly prohibited.
    - In pre-cutover test environments where context/gem.db is absent, raises PersistenceError
      unless explicitly configured via configure_persistence(backend='json').
    """
    global _ACTIVE_PERSISTENCE_SERVICE
    if _ACTIVE_PERSISTENCE_SERVICE is None:
        with _PERSISTENCE_LOCK:
            if _ACTIVE_PERSISTENCE_SERVICE is None:
                root_path = Path(repo_root).resolve() if repo_root else Path(__file__).resolve().parent.parent.parent
                db_path = root_path / "context" / "gem.db"
                if db_path.exists():
                    try:
                        mgr = DatabaseManager(str(db_path), profile=PRIMARY_FINANCIAL_PROFILE)
                        with mgr.connection() as conn:
                            cur = conn.cursor()
                            cur.execute("PRAGMA integrity_check;")
                            rows = cur.fetchall()
                            if rows != [("ok",)]:
                                raise PersistenceIntegrityError(f"Database integrity check failed for '{db_path}': {rows}")
                        adapter = SqlitePersistenceAdapter(mgr, base_ssot_path=str(root_path / "context" / "ssot.json"))
                        _ACTIVE_PERSISTENCE_SERVICE = PersistenceService(adapter)
                    except (PersistenceIntegrityError, PersistenceError):
                        raise
                    except Exception as e:
                        raise PersistenceIntegrityError(
                            f"Failed to initialize active SQLite persistence backend from '{db_path}': {e}. "
                            "Silent fallback to JSON is strictly prohibited."
                        ) from e
                else:
                    raise PersistenceError(
                        f"Authoritative SQLite persistence database '{db_path}' not found. "
                        "Silent JSON fallback is prohibited. "
                        "For isolated pre-cutover test environments, explicitly call configure_persistence(backend='json')."
                    )
    return _ACTIVE_PERSISTENCE_SERVICE


def configure_persistence(
    backend: str = "json",
    db_manager: Optional[DatabaseManager] = None,
    base_dir: Optional[str] = None,
    adapter: Optional[PersistenceAdapter] = None,
    base_ssot_path: Optional[str] = None,
) -> PersistenceService:
    """
    Explicitly configures the persistence service backend.
    Used for test isolation or controlled environments.
    """
    global _ACTIVE_PERSISTENCE_SERVICE
    with _PERSISTENCE_LOCK:
        if adapter is not None:
            new_adapter = adapter
        elif backend.lower() == "sqlite":
            if db_manager is None:
                raise ValueError("db_manager is required when configuring SQLite persistence backend.")
            new_adapter = SqlitePersistenceAdapter(db_manager, base_ssot_path=base_ssot_path)
        elif backend.lower() == "json":
            new_adapter = JsonPersistenceAdapter(base_dir=base_dir)
        else:
            raise ValueError(f"Unsupported persistence backend: '{backend}'. Must be 'json' or 'sqlite'.")

        # Dispose existing if open
        if _ACTIVE_PERSISTENCE_SERVICE is not None:
            try:
                _ACTIVE_PERSISTENCE_SERVICE.close()
            except Exception:
                pass

        _ACTIVE_PERSISTENCE_SERVICE = PersistenceService(new_adapter)
        return _ACTIVE_PERSISTENCE_SERVICE


def reset_persistence_service() -> None:
    """Disposes and resets the global persistence service singleton to uninitialized."""
    global _ACTIVE_PERSISTENCE_SERVICE
    with _PERSISTENCE_LOCK:
        if _ACTIVE_PERSISTENCE_SERVICE is not None:
            try:
                _ACTIVE_PERSISTENCE_SERVICE.close()
            except Exception:
                pass
            _ACTIVE_PERSISTENCE_SERVICE = None


def perform_sqlite_cutover(
    repo_root: Optional[Union[str, Path]] = None,
    default_timestamp: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Performs atomic initialisation and cutover to SQLite persistence at context/gem.db.
    Guarantees:
    - Disposable staging database on the same filesystem (context/staging_gem_<uuid>.db).
    - Validation of destination isolation rules.
    - Full schema initialisation using PRIMARY_FINANCIAL_PROFILE.
    - Ingestion of legacy JSON sources (context/ssot.json, trade_lessons.json, decision_log.json).
    - Zero floating-point conversions and full SSoT canonical projection parity validation.
    - PRAGMA integrity_check and foreign_key_check on staging database.
    - Zero modification to protected JSON assets (SHA-256 byte-for-byte invariance verified).
    - Hardened WAL truncation and atomic file replacement via os.replace.
    - Post-promotion verification on context/gem.db.
    """
    root_dir = Path(repo_root).resolve() if repo_root else Path(__file__).resolve().parent.parent.parent
    context_dir = root_dir / "context"
    target_db = context_dir / "gem.db"

    ssot_path = context_dir / "ssot.json"
    lessons_path = context_dir / "trade_lessons.json"
    decisions_path = context_dir / "decision_log.json"

    sources_to_check = [ssot_path, lessons_path, decisions_path]
    for p in sources_to_check:
        if not p.exists():
            raise PersistenceIntegrityError(f"Required cutover source file missing: {p}")

    initial_hashes = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sources_to_check
    }

    staging_name = f"staging_gem_{uuid.uuid4().hex}.db"
    staging_path = context_dir / staging_name
    staging_mgr: Optional[DatabaseManager] = None

    try:
        # Validate destination safety
        validate_migration_destination(staging_path, repository_root=root_dir)

        # 1. Initialize schema
        staging_mgr = DatabaseManager(str(staging_path), profile=PRIMARY_FINANCIAL_PROFILE)
        with staging_mgr.connection() as conn:
            SchemaMigrator.initialize_core_schema(conn)

        # 2. Import legacy JSON financial state
        cutover_ts = default_timestamp or utc_now_iso()
        importer = LegacyJsonImporter(repository_root=root_dir)
        import_result = importer.import_into(
            sources=[ssot_path, lessons_path, decisions_path],
            destination=staging_mgr,
            default_timestamp=cutover_ts,
            allow_date_only=True,
            allow_naive_utc=True,
            initialize_schema=False,
        )

        # 3. Canonical projection and parity verification
        generator = SSoTProjectionGenerator(staging_mgr)
        base_doc, _, _ = load_strict_json_file(ssot_path, repository_root=root_dir)
        with staging_mgr.connection() as conn:
            projection = generator.generate(conn, base_document=base_doc)

        verifier = SSoTParityVerifier(
            generator=generator,
            default_timestamp=cutover_ts,
            allow_date_only=True,
            allow_naive_utc=True,
        )
        parity_res = verifier.compare(
            legacy_sources=[ssot_path, lessons_path, decisions_path],
            projected_document=projection,
            base_document=base_doc,
        )

        if parity_res.summary.financial_mismatches > 0:
            raise PersistenceIntegrityError(
                f"Parity verification detected financial mismatches: {parity_res.summary.financial_mismatches}"
            )
        if parity_res.summary.missing_records > 0:
            raise PersistenceIntegrityError(
                f"Parity verification detected missing records: {parity_res.summary.missing_records}"
            )

        # 4. PRAGMA integrity and FK checks
        with staging_mgr.connection() as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA integrity_check;")
            rows = cur.fetchall()
            if rows != [("ok",)]:
                raise PersistenceIntegrityError(f"Staging database integrity_check failed: {rows}")

            cur.execute("PRAGMA foreign_key_check;")
            fk_rows = cur.fetchall()
            if fk_rows:
                raise PersistenceIntegrityError(f"Staging database foreign_key_check failed: {fk_rows}")
            cur.close()

        # 5. Pre-promotion hash invariance verification
        for p_str, expected_hash in initial_hashes.items():
            actual_hash = hashlib.sha256(Path(p_str).read_bytes()).hexdigest()
            if actual_hash != expected_hash:
                raise PersistenceIntegrityError(
                    f"Protected asset '{p_str}' altered during migration staging! "
                    f"Expected: {expected_hash}, Actual: {actual_hash}"
                )

        # 6. Checkpoint staging database and release all connection handles before atomic promotion
        staging_mgr.checkpoint(mode="TRUNCATE")
        del staging_mgr, conn, cur, generator, verifier, projection
        staging_mgr = None
        gc.collect()

        # Clean staging sidecars
        for ext in ("-wal", "-shm", ".lock", ".restore_lock"):
            sidecar = Path(str(staging_path) + ext)
            if sidecar.exists():
                try:
                    sidecar.unlink()
                except OSError:
                    pass

        # 7. Atomic promotion via os.replace
        os.replace(staging_path, target_db)

        # 8. Post-promotion validation on target database
        verify_mgr = DatabaseManager(str(target_db), profile=PRIMARY_FINANCIAL_PROFILE)
        if verify_mgr.verify_integrity() != ["ok"]:
            raise PersistenceIntegrityError("Target database integrity check failed after promotion.")
        with verify_mgr.connection() as conn:
            # Verify projection generation succeeds on promoted database
            gen = SSoTProjectionGenerator(verify_mgr)
            promoted_proj = gen.generate(conn, base_document=base_doc)
            db_revision = promoted_proj.get("_projection_metadata", {}).get("db_revision", 0)

        # 9. Post-promotion hash invariance verification
        for p_str, expected_hash in initial_hashes.items():
            actual_hash = hashlib.sha256(Path(p_str).read_bytes()).hexdigest()
            if actual_hash != expected_hash:
                raise PersistenceIntegrityError(
                    f"Protected asset '{p_str}' altered during atomic promotion! "
                    f"Expected: {expected_hash}, Actual: {actual_hash}"
                )

        # 10. Reset active persistence singleton so next access opens promoted database
        reset_persistence_service()

        return {
            "status": "success",
            "target_database": str(target_db),
            "db_revision": db_revision,
            "imported_counts": import_result.imported_counts,
            "skipped_counts": import_result.skipped_counts,
            "parity_summary": {
                "exact_matches": parity_res.summary.exact_matches,
                "equivalent_values": parity_res.summary.equivalent_values,
                "expected_external_sections": parity_res.summary.expected_external_sections,
                "financial_mismatches": parity_res.summary.financial_mismatches,
                "missing_records": parity_res.summary.missing_records,
                "unexpected_records": parity_res.summary.unexpected_records,
                "unresolved": parity_res.summary.unresolved,
            },
            "protected_hashes_verified": True,
        }

    finally:
        if staging_path.exists():
            try:
                staging_path.unlink()
            except OSError:
                pass
        for ext in ("-wal", "-shm", ".lock", ".restore_lock"):
            sidecar = Path(str(staging_path) + ext)
            if sidecar.exists():
                try:
                    sidecar.unlink()
                except OSError:
                    pass

