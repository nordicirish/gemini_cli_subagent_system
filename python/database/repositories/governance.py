"""
Governance Repositories: Scout Categories, Trade Lessons, Decision Log, and Owner Approvals.
Governed by Stage 1.4 Architecture and Agent Governance Framework.

Enforces:
- Authorization on trade lessons (operation 'update_trade_lessons') and decision log ('update_decision_log').
- Append-only immutability on decision log and owner approvals.
- Controlled state transition on owner approvals (PENDING -> CONSUMED).
- Fail-closed validation on all entity fields.
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
    validate_identifier,
    utc_now_iso,
)
from python.tool_registry import authorize_financial_mutation


# ---------------------------------------------------------------------------
# Scout Category Repository
# ---------------------------------------------------------------------------

class ScoutCategoryRepository(BaseRepository):
    """Repository for scout category definitions (scout_categories)."""

    def get_category(self, category_id: int) -> Optional[ScoutCategory]:
        """Retrieves a scout category by primary key ID."""
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
    ) -> ScoutCategory:
        """Upserts a scout category definition."""
        norm_name = validate_identifier(category_name, "category_name")
        tickers_list = [validate_identifier(t, "ticker").upper() for t in (tickers or [])]
        tickers_json = json.dumps(tickers_list, sort_keys=True)
        timestamp_utc = utc_now_iso()

        cur = self._conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO scout_categories (category_name, description, tickers_json, updated_at_utc)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(category_name) DO UPDATE SET
                    description = excluded.description,
                    tickers_json = excluded.tickers_json,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (norm_name, description, tickers_json, timestamp_utc),
            )
        except sqlite3.IntegrityError as e:
            raise IntegrityConflictError(f"Integrity constraint violation in scout category: {e}") from e

        cat = self.get_category_by_name(norm_name)
        if cat is None:
            raise EntityNotFoundError(f"Failed to retrieve newly upserted category '{norm_name}'")
        return cat

    def delete_category(self, category_id: int) -> bool:
        """Deletes a scout category by ID."""
        cur = self._conn.cursor()
        cur.execute("DELETE FROM scout_categories WHERE id = ?", (category_id,))
        return cur.rowcount > 0


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
    ) -> TradeLesson:
        """Upserts a trade lesson with authorization check."""
        norm_code = validate_identifier(rule_code, "rule_code").upper()
        norm_directive = validate_identifier(directive_text, "directive_text")
        norm_cat = validate_identifier(category, "category").upper()
        norm_status = validate_identifier(status, "status").upper()

        if norm_cat not in self.VALID_CATEGORIES:
            raise ValidationError(f"Invalid category '{norm_cat}'. Must be one of {sorted(self.VALID_CATEGORIES)}")
        if norm_status not in self.VALID_STATUSES:
            raise ValidationError(f"Invalid status '{norm_status}'. Must be one of {sorted(self.VALID_STATUSES)}")

        target_resource = f"trade_lessons/{norm_code}"
        payload = {
            "rule_code": norm_code,
            "directive_text": norm_directive,
            "category": norm_cat,
            "status": norm_status,
        }

        authorize_financial_mutation(
            operation_name="update_trade_lessons",
            target_resource=target_resource,
            payload=payload,
            caller_role=caller_role,
        )

        timestamp_utc = utc_now_iso()
        existing = self.get_lesson(norm_code)
        created_at = existing.created_at_utc if existing else timestamp_utc

        try:
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
                (norm_code, norm_directive, norm_cat, norm_status, created_at, timestamp_utc),
            )
        except sqlite3.IntegrityError as e:
            raise IntegrityConflictError(f"Integrity constraint violation upserting lesson: {e}") from e

        return TradeLesson(
            rule_code=norm_code,
            directive_text=norm_directive,
            category=norm_cat,
            status=norm_status,
            created_at_utc=created_at,
            last_reviewed_at_utc=timestamp_utc,
        )

    def delete_lesson(self, rule_code: str, caller_role: Optional[str] = None) -> bool:
        """Deletes a trade lesson with authorization."""
        norm_code = validate_identifier(rule_code, "rule_code").upper()
        target_resource = f"trade_lessons/{norm_code}"

        authorize_financial_mutation(
            operation_name="update_trade_lessons",
            target_resource=target_resource,
            payload={"rule_code": norm_code, "action": "delete"},
            caller_role=caller_role,
        )

        cur = self._conn.cursor()
        cur.execute("DELETE FROM trade_lessons WHERE rule_code = ?", (norm_code,))
        return cur.rowcount > 0


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
    ) -> DecisionRecord:
        """Appends a new council decision record."""
        norm_session = validate_identifier(session_id, "session_id")
        norm_action = validate_identifier(action_proposed, "action_proposed")
        norm_outcome = validate_identifier(decision_outcome, "decision_outcome")
        norm_ticker = validate_identifier(ticker, "ticker").upper() if ticker else None
        votes_json = json.dumps(votes or {}, sort_keys=True)
        timestamp_utc = utc_now_iso()

        payload = {
            "session_id": norm_session,
            "action_proposed": norm_action,
            "decision_outcome": norm_outcome,
            "ticker": norm_ticker,
        }

        authorize_financial_mutation(
            operation_name="update_decision_log",
            target_resource="decision_log",
            payload=payload,
            caller_role=caller_role,
        )

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
                    timestamp_utc,
                ),
            )
            record_id = cur.lastrowid
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
            timestamp_utc=timestamp_utc,
        )

    def get_decision(self, decision_id: int) -> Optional[DecisionRecord]:
        """Retrieves a decision record by primary key."""
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
        raise AppendOnlyViolationError("Decision log is append-only; update operations are prohibited.")

    def delete(self, *args, **kwargs):
        """Append-only entity: deletions are strictly forbidden."""
        raise AppendOnlyViolationError("Decision log is append-only; delete operations are prohibited.")


# ---------------------------------------------------------------------------
# Owner Approval Repository (Controlled Immutability)
# ---------------------------------------------------------------------------

class OwnerApprovalRepository(BaseRepository):
    """
    Repository for owner approval records (owner_approvals).
    Records are append-only; only controlled status transition to CONSUMED is permitted.
    """

    VALID_STATUSES = frozenset({"PENDING", "CONSUMED", "EXPIRED", "REVOKED"})

    def append_approval(
        self,
        approval_id: str,
        operation_name: str,
        target_resource: str,
        payload_digest: str,
        granted_by: str = "Owner",
    ) -> OwnerApprovalRecord:
        """Appends a new owner approval with PENDING status."""
        norm_id = validate_identifier(approval_id, "approval_id")
        norm_op = validate_identifier(operation_name, "operation_name")
        norm_target = validate_identifier(target_resource, "target_resource")
        norm_digest = validate_identifier(payload_digest, "payload_digest")
        norm_grantor = validate_identifier(granted_by, "granted_by")
        timestamp_utc = utc_now_iso()

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
        """Atomically transitions an approval from PENDING to CONSUMED with timestamp."""
        norm_id = validate_identifier(approval_id, "approval_id")
        timestamp_utc = utc_now_iso()
        cur = self._conn.cursor()
        cur.execute(
            """
            UPDATE owner_approvals
            SET status = 'CONSUMED', consumed_at_utc = ?
            WHERE approval_id = ? AND status = 'PENDING'
            """,
            (timestamp_utc, norm_id),
        )
        return cur.rowcount > 0

    def list_approvals(self, status: Optional[str] = None) -> List[OwnerApprovalRecord]:
        """Lists owner approvals, optionally filtered by status."""
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
        raise AppendOnlyViolationError("Owner approvals are immutable audit records; arbitrary updates are prohibited.")

    def delete(self, *args, **kwargs):
        """Append-only audit record: deletions are strictly prohibited."""
        raise AppendOnlyViolationError("Owner approvals are immutable audit records; deletions are prohibited.")

