"""
Audit & Changelog Repositories: AuditEventRepository and FinancialChangelogRepository.
Governed by Stage 1.4.1 Architecture and Agent Governance Framework.

Enforces:
- R3: Financial changelog repository protection: authorization enforcement and trusted actor attribution.
- R6: Strict Unit of Work lifecycle checking on all methods.
- Strict 32-character lowercase hex event_id validation on audit_events.
- Idempotent event deduplication: replaying identical event returns existing record.
- Conflict detection: replaying duplicate event_id with different payload raises IntegrityConflictError.
- Append-only immutability: update and delete operations strictly raise AppendOnlyViolationError.
- Deterministic JSON serialization for changelogs and audit payloads.
"""

import json
import sqlite3
from typing import Any, Dict, List, Optional

from python.database.repositories.base import (
    BaseRepository,
    AuditEventRecord,
    FinancialChangelogEntry,
    EntityNotFoundError,
    IntegrityConflictError,
    ValidationError,
    AppendOnlyViolationError,
    RepositoryError,
    validate_event_id,
    validate_identifier,
    utc_now_iso,
)
from python.tool_registry import (
    authorize_financial_mutation,
    get_active_caller_role,
    ENGINEERING_ROLES,
)


# ---------------------------------------------------------------------------
# Audit Event Repository (Indexed Audit Mirror)
# ---------------------------------------------------------------------------

class AuditEventRepository(BaseRepository):
    """
    Repository for structured audit events (audit_events).
    Storage: SQLite table indexed by timestamp_utc and action_category.
    Idempotent replay: identical event_id + identical payload succeeds idempotently.
    Conflicting replay: duplicate event_id + conflicting payload raises IntegrityConflictError.
    """

    def append_event(
        self,
        event_id: str,
        timestamp_utc: str,
        action_category: str,
        status: str,
        agent_role: str,
        permission_decision: str,
        approval_status: str,
        raw_event: Dict[str, Any],
        tool_name: Optional[str] = None,
        payload_hash: Optional[str] = None,
    ) -> AuditEventRecord:
        """
        Appends a new audit event, or deterministically returns existing record if replayed identically.
        Raises IntegrityConflictError if event_id already exists with different payload hash or json.
        """
        self._check_active()
        norm_id = validate_event_id(event_id)
        norm_category = validate_identifier(action_category, "action_category")
        norm_status = validate_identifier(status, "status")
        norm_role = validate_identifier(agent_role, "agent_role")
        norm_decision = validate_identifier(permission_decision, "permission_decision")
        norm_approval = validate_identifier(approval_status, "approval_status")
        raw_event_json = json.dumps(raw_event, sort_keys=True)

        cur = self._conn.cursor()

        # Check for existing event to enforce idempotency vs conflict
        cur.execute(
            """
            SELECT event_id, timestamp_utc, action_category, status, agent_role,
                   tool_name, permission_decision, approval_status, payload_hash, raw_event_json
            FROM audit_events
            WHERE event_id = ?
            """,
            (norm_id,),
        )
        existing = cur.fetchone()
        if existing is not None:
            existing_payload_hash = existing[8]
            existing_raw_json = existing[9]

            # Compare payloads to detect conflict
            same_hash = (existing_payload_hash or "") == (payload_hash or "")
            same_json = json.loads(existing_raw_json) == raw_event

            if same_hash and same_json:
                # Idempotent replay: return existing record
                return AuditEventRecord(
                    event_id=existing[0],
                    timestamp_utc=existing[1],
                    action_category=existing[2],
                    status=existing[3],
                    agent_role=existing[4],
                    tool_name=existing[5],
                    permission_decision=existing[6],
                    approval_status=existing[7],
                    payload_hash=existing[8],
                    raw_event=json.loads(existing[9]),
                )
            else:
                raise IntegrityConflictError(
                    f"Audit event with id '{norm_id}' already exists with different payload or hash."
                )

        # Insert new record
        try:
            cur.execute(
                """
                INSERT INTO audit_events (
                    event_id, timestamp_utc, action_category, status, agent_role,
                    tool_name, permission_decision, approval_status, payload_hash, raw_event_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    norm_id,
                    timestamp_utc,
                    norm_category,
                    norm_status,
                    norm_role,
                    tool_name,
                    norm_decision,
                    norm_approval,
                    payload_hash,
                    raw_event_json,
                ),
            )
        except sqlite3.IntegrityError as e:
            raise IntegrityConflictError(f"Integrity violation inserting audit event: {e}") from e

        return AuditEventRecord(
            event_id=norm_id,
            timestamp_utc=timestamp_utc,
            action_category=norm_category,
            status=norm_status,
            agent_role=norm_role,
            tool_name=tool_name,
            permission_decision=norm_decision,
            approval_status=norm_approval,
            payload_hash=payload_hash,
            raw_event=raw_event,
        )

    def get_event(self, event_id: str) -> Optional[AuditEventRecord]:
        """Retrieves an audit event by 32-character hex ID."""
        self._check_active()
        norm_id = validate_event_id(event_id)
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT event_id, timestamp_utc, action_category, status, agent_role,
                   tool_name, permission_decision, approval_status, payload_hash, raw_event_json
            FROM audit_events
            WHERE event_id = ?
            """,
            (norm_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return AuditEventRecord(
            event_id=row[0],
            timestamp_utc=row[1],
            action_category=row[2],
            status=row[3],
            agent_role=row[4],
            tool_name=row[5],
            permission_decision=row[6],
            approval_status=row[7],
            payload_hash=row[8],
            raw_event=json.loads(row[9]),
        )

    def list_events(
        self,
        action_category: Optional[str] = None,
        agent_role: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[AuditEventRecord]:
        """Lists audit events ordered newest first, with optional filters."""
        self._check_active()
        query = (
            "SELECT event_id, timestamp_utc, action_category, status, agent_role, "
            "tool_name, permission_decision, approval_status, payload_hash, raw_event_json "
            "FROM audit_events"
        )
        params: List[Any] = []
        clauses = []
        if action_category:
            clauses.append("action_category = ?")
            params.append(validate_identifier(action_category, "action_category"))
        if agent_role:
            clauses.append("agent_role = ?")
            params.append(validate_identifier(agent_role, "agent_role"))

        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY timestamp_utc DESC"
        if limit is not None:
            if limit <= 0:
                raise ValidationError("Limit must be positive")
            query += " LIMIT ?"
            params.append(limit)

        cur = self._conn.cursor()
        cur.execute(query, tuple(params))
        return [
            AuditEventRecord(
                event_id=r[0],
                timestamp_utc=r[1],
                action_category=r[2],
                status=r[3],
                agent_role=r[4],
                tool_name=r[5],
                permission_decision=r[6],
                approval_status=r[7],
                payload_hash=r[8],
                raw_event=json.loads(r[9]),
            )
            for r in cur.fetchall()
        ]

    def count_events(self) -> int:
        """Returns total count of recorded audit events."""
        self._check_active()
        cur = self._conn.cursor()
        cur.execute("SELECT COUNT(*) FROM audit_events")
        row = cur.fetchone()
        return row[0] if row else 0

    def update(self, *args, **kwargs):
        """Append-only audit record: updates are strictly forbidden."""
        self._check_active()
        raise AppendOnlyViolationError("Audit events are append-only; update operations are prohibited.")

    def delete(self, *args, **kwargs):
        """Append-only audit record: deletions are strictly forbidden."""
        self._check_active()
        raise AppendOnlyViolationError("Audit events are append-only; delete operations are prohibited.")


# ---------------------------------------------------------------------------
# Financial Changelog Repository (Append-Only & Authorization-Protected R3)
# ---------------------------------------------------------------------------

class FinancialChangelogRepository(BaseRepository):
    """
    Repository for transactional financial state mutations (financial_changelog).
    R3: Enforces financial mutation authorization boundary and trusted execution context.
    Append-only: captures before/after states, actor role, approval digest, and correlation ID.
    """

    def append_entry(
        self,
        correlation_id: str,
        actor_role: Optional[str],
        operation_name: str,
        target_resource: str,
        before_state: Optional[Dict[str, Any]],
        after_state: Optional[Dict[str, Any]],
        approval_digest: Optional[str] = None,
        timestamp_utc: Optional[str] = None,
    ) -> FinancialChangelogEntry:
        """
        Appends a new financial changelog entry (R3).
        Enforces authorization boundary: rejects engineering roles and unauthorized callers.
        Derives actor role from trusted execution context; rejects spoofed actor_role.
        """
        self._check_active()
        active_role = get_active_caller_role()

        # R3: Reject engineering roles fail-closed
        if active_role in ENGINEERING_ROLES:
            raise PermissionError(
                f"Security violation: Engineering role '{active_role}' cannot append to financial changelog."
            )

        # R3: Reject spoofed actor_role
        if actor_role is not None and actor_role != active_role:
            raise PermissionError(
                f"Attribution spoofing attempt: caller claimed actor_role '{actor_role}', but active context is '{active_role}'."
            )

        # R3: Enforce financial mutation authorization boundary
        authorize_financial_mutation(
            operation_name=operation_name,
            target_resource=target_resource,
            caller_role=active_role,
        )

        norm_cid = validate_identifier(correlation_id, "correlation_id")
        norm_actor = active_role
        norm_op = validate_identifier(operation_name, "operation_name")
        norm_target = validate_identifier(target_resource, "target_resource")
        ts = timestamp_utc or utc_now_iso()
        before_json = json.dumps(before_state, sort_keys=True) if before_state is not None else "{}"
        after_json = json.dumps(after_state, sort_keys=True) if after_state is not None else "{}"

        cur = self._conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO financial_changelog (
                    correlation_id, actor_role, operation_name, target_resource,
                    approval_digest, before_state_json, after_state_json, timestamp_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    norm_cid,
                    norm_actor,
                    norm_op,
                    norm_target,
                    approval_digest,
                    before_json,
                    after_json,
                    ts,
                ),
            )
            record_id = cur.lastrowid
        except sqlite3.IntegrityError as e:
            raise IntegrityConflictError(f"Integrity violation appending financial changelog: {e}") from e

        return FinancialChangelogEntry(
            id=record_id,
            correlation_id=norm_cid,
            actor_role=norm_actor,
            operation_name=norm_op,
            target_resource=norm_target,
            approval_digest=approval_digest,
            before_state=before_state,
            after_state=after_state,
            timestamp_utc=ts,
        )

    def get_entry(self, entry_id: int) -> Optional[FinancialChangelogEntry]:
        """Retrieves a financial changelog entry by primary key ID."""
        self._check_active()
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT id, correlation_id, actor_role, operation_name, target_resource,
                   approval_digest, before_state_json, after_state_json, timestamp_utc
            FROM financial_changelog
            WHERE id = ?
            """,
            (entry_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return FinancialChangelogEntry(
            id=row[0],
            correlation_id=row[1],
            actor_role=row[2],
            operation_name=row[3],
            target_resource=row[4],
            approval_digest=row[5],
            before_state=json.loads(row[6]) if row[6] != "{}" else None,
            after_state=json.loads(row[7]) if row[7] != "{}" else None,
            timestamp_utc=row[8],
        )

    def list_entries(
        self, correlation_id: Optional[str] = None, limit: Optional[int] = None
    ) -> List[FinancialChangelogEntry]:
        """Lists financial changelog entries ordered by ID ASC, with optional correlation_id filter."""
        self._check_active()
        query = (
            "SELECT id, correlation_id, actor_role, operation_name, target_resource, "
            "approval_digest, before_state_json, after_state_json, timestamp_utc "
            "FROM financial_changelog"
        )
        params: List[Any] = []
        if correlation_id:
            query += " WHERE correlation_id = ?"
            params.append(validate_identifier(correlation_id, "correlation_id"))
        query += " ORDER BY id ASC"
        if limit is not None:
            if limit <= 0:
                raise ValidationError("Limit must be positive")
            query += " LIMIT ?"
            params.append(limit)

        cur = self._conn.cursor()
        cur.execute(query, tuple(params))
        return [
            FinancialChangelogEntry(
                id=r[0],
                correlation_id=r[1],
                actor_role=r[2],
                operation_name=r[3],
                target_resource=r[4],
                approval_digest=r[5],
                before_state=json.loads(r[6]) if r[6] != "{}" else None,
                after_state=json.loads(r[7]) if r[7] != "{}" else None,
                timestamp_utc=r[8],
            )
            for r in cur.fetchall()
        ]

    def update(self, *args, **kwargs):
        """Append-only audit record: updates are strictly forbidden."""
        self._check_active()
        raise AppendOnlyViolationError("Financial changelog is append-only; update operations are prohibited.")

    def delete(self, *args, **kwargs):
        """Append-only audit record: deletions are strictly forbidden."""
        self._check_active()
        raise AppendOnlyViolationError("Financial changelog is append-only; delete operations are prohibited.")
