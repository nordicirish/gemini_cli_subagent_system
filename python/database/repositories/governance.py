"""
Governance Repositories: Scout Categories, Trade Lessons, Decision Log, and Owner Approvals.
Governed by Stage 1.4.1 Architecture and Agent Governance Framework.

Enforces:
- R2: Strict Owner approval record integrity: grantor derived strictly from trusted Owner/Human context.
- R6: Strict Unit of Work lifecycle checking on all methods.
- Authorization on trade lessons ('update_trade_lessons') and decision log ('update_decision_log').
- Append-only immutability on decision log and owner approvals.
- Controlled state transition on owner approvals (PENDING -> CONSUMED).
"""

import json
import sqlite3
from typing import Any, Dict, List, Optional, Sequence, Tuple

from python.database.repositories.base import (
    BaseRepository,
    ScoutCategory,
    TradeLesson,
    DecisionRecord,
    OwnerApprovalRecord,
    EntityNotFoundError,
    DuplicateEntityError,
    IntegrityConflictError,
    ValidationError,
    AppendOnlyViolationError,
    RepositoryError,
    validate_identifier,
    decimal_to_canonical_str,
    utc_now_iso,
    _resolve_effective_actor,
    validate_timestamp_utc,
    increment_db_revision,
    get_db_revision,
    repository_transaction,
)
from python.tool_registry import authorize_financial_mutation, get_active_caller_role


# ---------------------------------------------------------------------------
# Scout Category Repository
# ---------------------------------------------------------------------------

class ScoutCategoryRepository(BaseRepository):
    """Repository for scout category definitions (scout_categories)."""

    def get_category(self, category_id: int) -> Optional[ScoutCategory]:
        """Retrieves a scout category by primary key ID."""
        self._check_active()
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT id, category_name, description, tickers_json, updated_at_utc
            FROM scout_categories
            WHERE id = ?
            """,
            (category_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        tickers = tuple(json.loads(row[3]))
        return ScoutCategory(
            id=row[0],
            category_name=row[1],
            description=row[2],
            tickers=tickers,
            updated_at_utc=row[4],
        )

    def get_category_by_name(self, category_name: str) -> Optional[ScoutCategory]:
        """Retrieves a scout category by unique name."""
        self._check_active()
        norm_name = validate_identifier(category_name, "category_name")
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT id, category_name, description, tickers_json, updated_at_utc
            FROM scout_categories
            WHERE category_name = ?
            """,
            (norm_name,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        tickers = tuple(json.loads(row[3]))
        return ScoutCategory(
            id=row[0],
            category_name=row[1],
            description=row[2],
            tickers=tickers,
            updated_at_utc=row[4],
        )

    def list_categories(self) -> List[ScoutCategory]:
        """Lists all scout categories ordered by name."""
        self._check_active()
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT id, category_name, description, tickers_json, updated_at_utc
            FROM scout_categories
            ORDER BY category_name ASC
            """
        )
        results = []
        for r in cur.fetchall():
            tickers = tuple(json.loads(r[3]))
            results.append(
                ScoutCategory(
                    id=r[0],
                    category_name=r[1],
                    description=r[2],
                    tickers=tickers,
                    updated_at_utc=r[4],
                )
            )
        return results

    def upsert_category(
        self,
        category_name: str,
        description: Optional[str] = None,
        tickers: Optional[Sequence[str]] = None,
        caller_role: Optional[str] = None,
        requires_owner_approval: bool = False,
        timestamp_utc: Optional[str] = None,
    ) -> ScoutCategory:
        """Upserts a scout category definition with authorization and revision tracking (R2/R3/R5/R6)."""
        self._check_active()
        norm_name = validate_identifier(category_name, "category_name")
        tickers_list = [validate_identifier(t, "ticker").upper() for t in (tickers or [])]
        tickers_json = json.dumps(tickers_list, sort_keys=True)
        valid_ts = validate_timestamp_utc(timestamp_utc)

        target_resource = f"scout_categories/{norm_name}"
        payload = {
            "category_name": norm_name,
            "description": description,
            "tickers": tickers_list,
        }

        effective_actor = _resolve_effective_actor(caller_role)
        authorize_financial_mutation(
            operation_name="update_ssot",
            target_resource=target_resource,
            payload=payload,
            caller_role=effective_actor,
            requires_owner_approval=requires_owner_approval,
        )

        # R1/R3/R5: Acquire write lock and manage savepoint/transaction lifecycle
        with repository_transaction(self._conn, uow=self._uow):
            try:
                cur = self._conn.cursor()
                cur.execute(
                    """
                    INSERT INTO scout_categories (category_name, description, tickers_json, updated_at_utc)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(category_name) DO UPDATE SET
                        description = excluded.description,
                        tickers_json = excluded.tickers_json,
                        updated_at_utc = excluded.updated_at_utc
                    """,
                    (norm_name, description, tickers_json, valid_ts),
                )

                # Increment db_revision atomically within the same transaction
                increment_db_revision(self._conn, valid_ts)
            except sqlite3.IntegrityError as e:
                raise IntegrityConflictError(f"Integrity constraint violation in scout category: {e}") from e

        cat = self.get_category_by_name(norm_name)
        if cat is None:
            raise EntityNotFoundError(f"Failed to retrieve newly upserted category '{norm_name}'")
        return cat

    def delete_category(
        self,
        category_id: int,
        caller_role: Optional[str] = None,
        requires_owner_approval: bool = False,
    ) -> bool:
        """Deletes a scout category by ID with authorization and revision tracking (R2/R3/R5)."""
        self._check_active()
        if not isinstance(category_id, int) or category_id <= 0:
            raise ValidationError(f"category_id must be a positive integer, got {category_id}")

        existing = self.get_category(category_id)
        target_resource = (
            f"scout_categories/{existing.category_name}"
            if existing
            else f"scout_categories/id:{category_id}"
        )
        payload = {
            "category_id": category_id,
            "action": "delete",
            "category_name": existing.category_name if existing else None,
        }

        effective_actor = _resolve_effective_actor(caller_role)
        authorize_financial_mutation(
            operation_name="update_ssot",
            target_resource=target_resource,
            payload=payload,
            caller_role=effective_actor,
            requires_owner_approval=requires_owner_approval,
        )

        # R1/R3/R5: Acquire write lock and manage savepoint/transaction lifecycle
        with repository_transaction(self._conn, uow=self._uow):
            cur = self._conn.cursor()
            cur.execute("DELETE FROM scout_categories WHERE id = ?", (category_id,))
            deleted = cur.rowcount > 0

            if deleted:
                increment_db_revision(self._conn)

            return deleted


_UNSET = object()


# ---------------------------------------------------------------------------
# Trade Lesson Repository
# ---------------------------------------------------------------------------

class TradeLessonRepository(BaseRepository):
    """
    Repository for codified investment trade lessons (trade_lessons).
    Mutations strictly governed by 'update_trade_lessons' authorization.
    """

    VALID_CATEGORIES = frozenset({"MANDATE", "EXECUTION", "STRUCTURAL", "RISK", "HEURISTIC"})
    VALID_STATUSES = frozenset({"ACTIVE", "DEPRECATED", "SUSPENDED"})

    def get_lesson(self, rule_code: str) -> Optional[TradeLesson]:
        """Retrieves a trade lesson by rule code."""
        self._check_active()
        norm_code = validate_identifier(rule_code, "rule_code").upper()
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT rule_code, directive_text, category, status, created_at_utc, last_reviewed_at_utc
            FROM trade_lessons
            WHERE rule_code = ?
            """,
            (norm_code,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return TradeLesson(
            rule_code=row[0],
            directive_text=row[1],
            category=row[2],
            status=row[3],
            created_at_utc=row[4],
            last_reviewed_at_utc=row[5],
        )

    def list_lessons(
        self, category: Optional[str] = None, status: Optional[str] = None
    ) -> List[TradeLesson]:
        """Lists trade lessons, optionally filtered by category and/or status."""
        self._check_active()
        query = "SELECT rule_code, directive_text, category, status, created_at_utc, last_reviewed_at_utc FROM trade_lessons"
        params: List[Any] = []
        clauses = []
        if category:
            norm_cat = validate_identifier(category, "category").upper()
            clauses.append("category = ?")
            params.append(norm_cat)
        if status:
            norm_status = validate_identifier(status, "status").upper()
            clauses.append("status = ?")
            params.append(norm_status)

        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY rule_code ASC"

        cur = self._conn.cursor()
        cur.execute(query, tuple(params))
        return [
            TradeLesson(
                rule_code=r[0],
                directive_text=r[1],
                category=r[2],
                status=r[3],
                created_at_utc=r[4],
                last_reviewed_at_utc=r[5],
            )
            for r in cur.fetchall()
        ]

    def upsert_lesson(
        self,
        rule_code: str,
        directive_text: str,
        category: str,
        status: str = "ACTIVE",
        caller_role: Optional[str] = None,
        timestamp_utc: Optional[str] = None,
        last_reviewed_at_utc: Any = _UNSET,
    ) -> TradeLesson:
        """Upserts a trade lesson with authorization check, transaction safety, and revision tracking (R1/R3/R5/R6)."""
        self._check_active()
        norm_code = validate_identifier(rule_code, "rule_code").upper()
        norm_directive = validate_identifier(directive_text, "directive_text")
        norm_cat = validate_identifier(category, "category").upper()
        norm_status = validate_identifier(status, "status").upper()

        if norm_cat not in self.VALID_CATEGORIES:
            raise ValidationError(f"Invalid category '{norm_cat}'. Must be one of {sorted(self.VALID_CATEGORIES)}")
        if norm_status not in self.VALID_STATUSES:
            raise ValidationError(f"Invalid status '{norm_status}'. Must be one of {sorted(self.VALID_STATUSES)}")

        valid_ts = validate_timestamp_utc(timestamp_utc)
        if last_reviewed_at_utc is _UNSET:
            valid_rev_ts = valid_ts
        elif last_reviewed_at_utc is None:
            valid_rev_ts = None
        else:
            valid_rev_ts = validate_timestamp_utc(last_reviewed_at_utc)
        target_resource = f"trade_lessons/{norm_code}"
        payload = {
            "rule_code": norm_code,
            "directive_text": norm_directive,
            "category": norm_cat,
            "status": norm_status,
        }

        effective_actor = _resolve_effective_actor(caller_role)

        authorize_financial_mutation(
            operation_name="update_trade_lessons",
            target_resource=target_resource,
            payload=payload,
            caller_role=effective_actor,
        )

        # R1/R3/R5: Acquire write lock and manage savepoint/transaction lifecycle
        with repository_transaction(self._conn, uow=self._uow):
            try:
                existing = self.get_lesson(norm_code)
                created_at = existing.created_at_utc if existing else valid_ts

                self._conn.execute(
                    """
                    INSERT INTO trade_lessons (
                        rule_code, directive_text, category, status, created_at_utc, last_reviewed_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(rule_code) DO UPDATE SET
                        directive_text = excluded.directive_text,
                        category = excluded.category,
                        status = excluded.status,
                        last_reviewed_at_utc = excluded.last_reviewed_at_utc
                    """,
                    (norm_code, norm_directive, norm_cat, norm_status, created_at, valid_rev_ts),
                )

                # Atomically increment database revision with mutation
                increment_db_revision(self._conn, valid_ts)
            except sqlite3.IntegrityError as e:
                raise IntegrityConflictError(f"Integrity constraint violation upserting lesson: {e}") from e

        return TradeLesson(
            rule_code=norm_code,
            directive_text=norm_directive,
            category=norm_cat,
            status=norm_status,
            created_at_utc=created_at,
            last_reviewed_at_utc=valid_rev_ts,
        )

    def delete_lesson(self, rule_code: str, caller_role: Optional[str] = None) -> bool:
        """Deletes a trade lesson with authorization, transaction safety, and revision tracking (R1/R3/R5)."""
        self._check_active()
        norm_code = validate_identifier(rule_code, "rule_code").upper()
        target_resource = f"trade_lessons/{norm_code}"

        effective_actor = _resolve_effective_actor(caller_role)

        authorize_financial_mutation(
            operation_name="update_trade_lessons",
            target_resource=target_resource,
            payload={"rule_code": norm_code, "action": "delete"},
            caller_role=effective_actor,
        )

        # R1/R3/R5: Acquire write lock and manage savepoint/transaction lifecycle
        with repository_transaction(self._conn, uow=self._uow):
            cur = self._conn.cursor()
            cur.execute("DELETE FROM trade_lessons WHERE rule_code = ?", (norm_code,))
            deleted = cur.rowcount > 0

            if deleted:
                increment_db_revision(self._conn)

            return deleted


# ---------------------------------------------------------------------------
# Decision Log Repository (Append-Only)
# ---------------------------------------------------------------------------

class DecisionLogRepository(BaseRepository):
    """
    Repository for Investment Council decision log (decision_log).
    Append-only: update and delete operations are strictly prohibited.
    """

    def append_decision(
        self,
        session_id: str,
        action_proposed: str,
        decision_outcome: str,
        ticker: Optional[str] = None,
        votes: Optional[Dict[str, Any]] = None,
        transcript_text: Optional[str] = None,
        caller_role: Optional[str] = None,
        timestamp_utc: Optional[str] = None,
    ) -> DecisionRecord:
        """Appends a new council decision record with authorization, transaction safety, and revision tracking (R1/R3/R5/R6)."""
        self._check_active()
        norm_session = validate_identifier(session_id, "session_id")
        norm_action = validate_identifier(action_proposed, "action_proposed")
        norm_outcome = validate_identifier(decision_outcome, "decision_outcome")
        norm_ticker = validate_identifier(ticker, "ticker").upper() if ticker else None
        votes_json = json.dumps(votes or {}, sort_keys=True, default=decimal_to_canonical_str)
        valid_ts = validate_timestamp_utc(timestamp_utc)

        payload = {
            "session_id": norm_session,
            "action_proposed": norm_action,
            "decision_outcome": norm_outcome,
            "ticker": norm_ticker,
        }

        effective_actor = _resolve_effective_actor(caller_role)

        authorize_financial_mutation(
            operation_name="update_decision_log",
            target_resource="decision_log",
            payload=payload,
            caller_role=effective_actor,
        )

        # R1/R3/R5: Acquire write lock and manage savepoint/transaction lifecycle
        with repository_transaction(self._conn, uow=self._uow):
            cur = self._conn.cursor()
            try:
                cur.execute(
                    """
                    INSERT INTO decision_log (
                        session_id, ticker, action_proposed, decision_outcome,
                        votes_json, transcript_text, timestamp_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        norm_session,
                        norm_ticker,
                        norm_action,
                        norm_outcome,
                        votes_json,
                        transcript_text,
                        valid_ts,
                    ),
                )
                record_id = cur.lastrowid

                # Atomically increment database revision with mutation
                increment_db_revision(self._conn, valid_ts)
            except sqlite3.IntegrityError as e:
                raise IntegrityConflictError(f"Integrity violation appending decision log: {e}") from e

        return DecisionRecord(
            id=record_id,
            session_id=norm_session,
            ticker=norm_ticker,
            action_proposed=norm_action,
            decision_outcome=norm_outcome,
            votes=votes or {},
            transcript_text=transcript_text,
            timestamp_utc=valid_ts,
        )

    def get_decision(self, decision_id: int) -> Optional[DecisionRecord]:
        """Retrieves a decision record by primary key."""
        self._check_active()
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT id, session_id, ticker, action_proposed, decision_outcome,
                   votes_json, transcript_text, timestamp_utc
            FROM decision_log
            WHERE id = ?
            """,
            (decision_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return DecisionRecord(
            id=row[0],
            session_id=row[1],
            ticker=row[2],
            action_proposed=row[3],
            decision_outcome=row[4],
            votes=json.loads(row[5]),
            transcript_text=row[6],
            timestamp_utc=row[7],
        )

    def list_decisions(
        self,
        session_id: Optional[str] = None,
        ticker: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[DecisionRecord]:
        """Lists decision records ordered newest first, with optional filters."""
        self._check_active()
        query = (
            "SELECT id, session_id, ticker, action_proposed, decision_outcome, "
            "votes_json, transcript_text, timestamp_utc FROM decision_log"
        )
        params: List[Any] = []
        clauses = []
        if session_id:
            clauses.append("session_id = ?")
            params.append(validate_identifier(session_id, "session_id"))
        if ticker:
            clauses.append("ticker = ?")
            params.append(validate_identifier(ticker, "ticker").upper())

        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id DESC"
        if limit is not None:
            if limit <= 0:
                raise ValidationError("Limit must be positive")
            query += " LIMIT ?"
            params.append(limit)

        cur = self._conn.cursor()
        cur.execute(query, tuple(params))
        return [
            DecisionRecord(
                id=r[0],
                session_id=r[1],
                ticker=r[2],
                action_proposed=r[3],
                decision_outcome=r[4],
                votes=json.loads(r[5]),
                transcript_text=r[6],
                timestamp_utc=r[7],
            )
            for r in cur.fetchall()
        ]

    def update(self, *args, **kwargs):
        """Append-only entity: updates are strictly forbidden."""
        self._check_active()
        raise AppendOnlyViolationError("Decision log is append-only; update operations are prohibited.")

    def delete(self, *args, **kwargs):
        """Append-only entity: deletions are strictly forbidden."""
        self._check_active()
        raise AppendOnlyViolationError("Decision log is append-only; delete operations are prohibited.")


# ---------------------------------------------------------------------------
# Owner Approval Repository (Controlled Immutability & Trusted Attribution R2)
# ---------------------------------------------------------------------------

class OwnerApprovalRepository(BaseRepository):
    """
    Repository for owner approval records (owner_approvals).
    R2: Grantor identity derived strictly from trusted Owner/Human context.
    Records are append-only; only controlled status transition to CONSUMED is permitted.
    """

    VALID_STATUSES = frozenset({"PENDING", "CONSUMED", "EXPIRED", "REVOKED"})

    def append_approval(
        self,
        approval_id: str,
        operation_name: str,
        target_resource: str,
        payload_digest: str,
        granted_by: Optional[str] = None,
    ) -> OwnerApprovalRecord:
        """
        Appends a new owner approval with PENDING status (R2).
        Strictly requires active execution context in ('Owner', 'Human').
        Rejects engineering, investment agent, and default System contexts fail-closed.
        """
        self._check_active()
        active_role = get_active_caller_role()

        # R2: Strictly require Owner or Human execution context
        if active_role not in ("Owner", "Human"):
            raise PermissionError(
                f"Security violation: Active context role '{active_role}' is not authorized to create owner approvals. "
                f"Owner approvals require explicit Owner or Human execution context."
            )

        # R2: Do not permit caller to spoof granted_by
        if granted_by is not None and granted_by != active_role:
            raise PermissionError(
                f"Attribution violation: caller passed granted_by='{granted_by}', but active context is '{active_role}'."
            )

        norm_id = validate_identifier(approval_id, "approval_id")
        norm_op = validate_identifier(operation_name, "operation_name")
        norm_target = validate_identifier(target_resource, "target_resource")
        norm_digest = validate_identifier(payload_digest, "payload_digest")
        norm_grantor = active_role
        timestamp_utc = utc_now_iso()

        # R1/R3/R5: Acquire write lock and manage savepoint/transaction lifecycle
        with repository_transaction(self._conn, uow=self._uow):
            cur = self._conn.cursor()
            try:
                cur.execute(
                    """
                    INSERT INTO owner_approvals (
                        approval_id, operation_name, target_resource, payload_digest,
                        granted_by, status, created_at_utc
                    ) VALUES (?, ?, ?, ?, ?, 'PENDING', ?)
                    """,
                    (norm_id, norm_op, norm_target, norm_digest, norm_grantor, timestamp_utc),
                )
                record_id = cur.lastrowid

                # Atomically increment database revision with mutation
                increment_db_revision(self._conn, timestamp_utc)
            except sqlite3.IntegrityError as e:
                if "UNIQUE constraint failed" in str(e):
                    raise DuplicateEntityError(f"Owner approval with id '{norm_id}' already exists") from e
                raise IntegrityConflictError(f"Integrity violation creating owner approval: {e}") from e

        return OwnerApprovalRecord(
            id=record_id,
            approval_id=norm_id,
            operation_name=norm_op,
            target_resource=norm_target,
            payload_digest=norm_digest,
            granted_by=norm_grantor,
            consumed_at_utc=None,
            status="PENDING",
            created_at_utc=timestamp_utc,
        )

    def get_approval(self, approval_id: str) -> Optional[OwnerApprovalRecord]:
        """Retrieves an approval record by its unique approval_id."""
        self._check_active()
        norm_id = validate_identifier(approval_id, "approval_id")
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT id, approval_id, operation_name, target_resource, payload_digest,
                   granted_by, consumed_at_utc, status, created_at_utc
            FROM owner_approvals
            WHERE approval_id = ?
            """,
            (norm_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return OwnerApprovalRecord(
            id=row[0],
            approval_id=row[1],
            operation_name=row[2],
            target_resource=row[3],
            payload_digest=row[4],
            granted_by=row[5],
            consumed_at_utc=row[6],
            status=row[7],
            created_at_utc=row[8],
        )

    def get_active_approval(
        self, operation_name: str, target_resource: str, payload_digest: str
    ) -> Optional[OwnerApprovalRecord]:
        """Retrieves the oldest PENDING approval matching operation, target, and payload digest."""
        self._check_active()
        norm_op = validate_identifier(operation_name, "operation_name")
        norm_target = validate_identifier(target_resource, "target_resource")
        norm_digest = validate_identifier(payload_digest, "payload_digest")
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT id, approval_id, operation_name, target_resource, payload_digest,
                   granted_by, consumed_at_utc, status, created_at_utc
            FROM owner_approvals
            WHERE operation_name = ? AND target_resource = ? AND payload_digest = ? AND status = 'PENDING'
            ORDER BY id ASC
            LIMIT 1
            """,
            (norm_op, norm_target, norm_digest),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return OwnerApprovalRecord(
            id=row[0],
            approval_id=row[1],
            operation_name=row[2],
            target_resource=row[3],
            payload_digest=row[4],
            granted_by=row[5],
            consumed_at_utc=row[6],
            status=row[7],
            created_at_utc=row[8],
        )

    def consume_approval(self, approval_id: str) -> bool:
        """
        Atomically transitions an approval from PENDING to CONSUMED with timestamp.
        Preserves original operation, target, payload digest, and grantor immutability.
        """
        self._check_active()
        norm_id = validate_identifier(approval_id, "approval_id")
        timestamp_utc = utc_now_iso()

        # R1/R3/R5: Acquire write lock and manage savepoint/transaction lifecycle
        with repository_transaction(self._conn, uow=self._uow):
            cur = self._conn.cursor()
            cur.execute(
                """
                UPDATE owner_approvals
                SET status = 'CONSUMED', consumed_at_utc = ?
                WHERE approval_id = ? AND status = 'PENDING'
                """,
                (timestamp_utc, norm_id),
            )
            consumed = cur.rowcount > 0

            if consumed:
                increment_db_revision(self._conn, timestamp_utc)

            return consumed

    def list_approvals(self, status: Optional[str] = None) -> List[OwnerApprovalRecord]:
        """Lists owner approvals, optionally filtered by status."""
        self._check_active()
        query = (
            "SELECT id, approval_id, operation_name, target_resource, payload_digest, "
            "granted_by, consumed_at_utc, status, created_at_utc FROM owner_approvals"
        )
        params: List[Any] = []
        if status:
            norm_status = validate_identifier(status, "status").upper()
            query += " WHERE status = ?"
            params.append(norm_status)
        query += " ORDER BY id DESC"

        cur = self._conn.cursor()
        cur.execute(query, tuple(params))
        return [
            OwnerApprovalRecord(
                id=r[0],
                approval_id=r[1],
                operation_name=r[2],
                target_resource=r[3],
                payload_digest=r[4],
                granted_by=r[5],
                consumed_at_utc=r[6],
                status=r[7],
                created_at_utc=r[8],
            )
            for r in cur.fetchall()
        ]

    def update(self, *args, **kwargs):
        """Append-only audit record: arbitrary updates are strictly prohibited."""
        self._check_active()
        raise AppendOnlyViolationError("Owner approvals are immutable audit records; arbitrary updates are prohibited.")

    def delete(self, *args, **kwargs):
        """Append-only audit record: deletions are strictly prohibited."""
        self._check_active()
        raise AppendOnlyViolationError("Owner approvals are immutable audit records; deletions are prohibited.")
