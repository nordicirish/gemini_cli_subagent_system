"""
Financial Repositories: Cash, Positions, and Watchlist.
Governed by Stage 1.4.1 Architecture and Agent Governance Framework.

Enforces:
- R1: Mandatory financial changelog capture on all financial mutations (no suppression parameter).
- R4: Trusted actor attribution derived from execution context; no Owner/Human spoofing from System.
- R5: Transaction snapshot consistency: write lock (BEGIN IMMEDIATE) acquired before before-state query.
- R6: Strict Unit of Work lifecycle checking: retained repository references fail-closed on completion.
"""

from decimal import Decimal
import json
import sqlite3
import sys
from typing import Any, Dict, List, Optional, Union
import uuid

from python.database.repositories.base import (
    BaseRepository,
    CashBalance,
    PositionRecord,
    WatchlistItem,
    FinancialChangelogEntry,
    EntityNotFoundError,
    DuplicateEntityError,
    IntegrityConflictError,
    ValidationError,
    RepositoryError,
    validate_decimal,
    decimal_to_canonical_str,
    validate_identifier,
    utc_now_iso,
    _resolve_effective_actor,
    validate_timestamp_utc,
    increment_db_revision,
    repository_transaction,
)
from python.tool_registry import (
    authorize_financial_mutation,
    get_active_caller_role,
    compute_payload_digest,
    TRUSTED_SYSTEM_ROLES,
)

__all__ = [
    "CashRepository",
    "PositionRepository",
    "WatchlistRepository",
]

_SUPPORTED_MUTATION_OPERATIONS = frozenset({
    "set_cash_balance",
    "delete_cash",
    "upsert_position",
    "delete_position",
    "upsert_watchlist_item",
    "remove_watchlist_item",
})


def _create_credential_closure():
    _private_token = object()

    def validate_credential(token: object) -> bool:
        return token is _private_token

    def acquire_mutation_credential() -> object:
        """
        Dispenses the private capability credential strictly to the six authorized
        financial mutation implementation methods (R1).
        """
        frame = sys._getframe(1)
        code = frame.f_code
        method_name = code.co_name
        caller_self = frame.f_locals.get("self")

        # Check caller against authorized repository methods
        if method_name in ("set_cash_balance", "delete_cash"):
            if isinstance(caller_self, CashRepository) and getattr(CashRepository, method_name).__code__ is code:
                return _private_token
        elif method_name in ("upsert_position", "delete_position"):
            if isinstance(caller_self, PositionRepository) and getattr(PositionRepository, method_name).__code__ is code:
                return _private_token
        elif method_name in ("upsert_watchlist_item", "remove_watchlist_item"):
            if isinstance(caller_self, WatchlistRepository) and getattr(WatchlistRepository, method_name).__code__ is code:
                return _private_token

        raise PermissionError(
            f"Unauthorized attempt to acquire financial mutation credential from '{method_name}'."
        )

    return validate_credential, acquire_mutation_credential


_validate_credential, _acquire_mutation_credential = _create_credential_closure()


class _FinancialMutationCapability:
    """
    Internal capability token coupling financial changelog entries to trusted mutations (R1).
    Cannot be instantiated directly by callers without the internal encapsulated credential.
    """

    def __init__(
        self,
        credential: Any = None,
        connection: Optional[sqlite3.Connection] = None,
        correlation_id: str = "",
        actor_role: str = "",
        operation_name: str = "",
        target_resource: str = "",
        before_state: Optional[Dict[str, Any]] = None,
        after_state: Optional[Dict[str, Any]] = None,
        approval_digest: Optional[str] = None,
        timestamp_utc: str = "",
        *,
        key: Any = None,
    ):
        cred = credential if credential is not None else key
        if not _validate_credential(cred):
            raise PermissionError("Direct instantiation of _FinancialMutationCapability is prohibited.")
        if connection is None:
            raise ValueError("Connection cannot be None")
        if operation_name not in _SUPPORTED_MUTATION_OPERATIONS:
            raise ValidationError(f"Unsupported mutation operation: '{operation_name}'")
        self.connection = connection
        self.correlation_id = correlation_id
        self.actor_role = actor_role
        self.operation_name = operation_name
        self.target_resource = target_resource
        self.before_state = before_state
        self.after_state = after_state
        self.approval_digest = approval_digest
        self.timestamp_utc = timestamp_utc
        self._consumed = False

    @property
    def is_consumed(self) -> bool:
        return self._consumed

    def mark_consumed(self) -> None:
        if self._consumed:
            raise IntegrityConflictError("Mutation capability has already been consumed.")
        self._consumed = True


# ---------------------------------------------------------------------------
# Cash Repository
# ---------------------------------------------------------------------------

class CashRepository(BaseRepository):
    """
    Repository for portfolio cash balances (portfolio_cash).
    Storage: exact Decimal TEXT, scale <= 2.
    """

    def get_cash(self, account_id: str = "PRIMARY", currency: str = "EUR") -> Optional[CashBalance]:
        """Retrieves a single cash balance by account and currency."""
        self._check_active()
        norm_account = validate_identifier(account_id, "account_id")
        norm_currency = validate_identifier(currency, "currency").upper()
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT account_id, currency, amount, last_updated_at_utc
            FROM portfolio_cash
            WHERE account_id = ? AND currency = ?
            """,
            (norm_account, norm_currency),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return CashBalance(
            account_id=row[0],
            currency=row[1],
            amount=Decimal(row[2]),
            last_updated_at_utc=row[3],
        )

    def list_cash(self, account_id: Optional[str] = None) -> List[CashBalance]:
        """Lists all cash balances, optionally filtered by account_id."""
        self._check_active()
        cur = self._conn.cursor()
        if account_id:
            norm_account = validate_identifier(account_id, "account_id")
            cur.execute(
                """
                SELECT account_id, currency, amount, last_updated_at_utc
                FROM portfolio_cash
                WHERE account_id = ?
                ORDER BY currency ASC
                """,
                (norm_account,),
            )
        else:
            cur.execute(
                """
                SELECT account_id, currency, amount, last_updated_at_utc
                FROM portfolio_cash
                ORDER BY account_id ASC, currency ASC
                """
            )
        return [
            CashBalance(
                account_id=r[0],
                currency=r[1],
                amount=Decimal(r[2]),
                last_updated_at_utc=r[3],
            )
            for r in cur.fetchall()
        ]

    def set_cash_balance(
        self,
        account_id: str,
        currency: str,
        amount: Union[Decimal, int, str],
        correlation_id: Optional[str] = None,
        caller_role: Optional[str] = None,
        requires_owner_approval: bool = False,
        timestamp_utc: Optional[str] = None,
    ) -> CashBalance:
        """
        Sets the cash balance for an account and currency.
        Enforces Decimal scale <= 2, non-negative, authorization, and mandatory changelog atomicity (R1).
        Acquires write lock before before-state query for snapshot consistency (R5).
        """
        self._check_active()
        norm_account = validate_identifier(account_id, "account_id")
        norm_currency = validate_identifier(currency, "currency").upper()
        if norm_currency not in ("EUR", "USD"):
            raise ValidationError(f"Unsupported cash currency '{norm_currency}'. Must be EUR or USD.")

        dec_amount = validate_decimal(
            amount,
            field_name="amount",
            max_scale=2,
            allow_negative=False,
            allow_zero=True,
            allow_none=False,
        )
        amount_str = decimal_to_canonical_str(dec_amount)
        valid_ts = validate_timestamp_utc(timestamp_utc)
        cid = correlation_id or uuid.uuid4().hex
        effective_actor = _resolve_effective_actor(caller_role)
        target_resource = f"portfolio_cash/{norm_account}/{norm_currency}"

        payload = {
            "account_id": norm_account,
            "currency": norm_currency,
            "amount": amount_str,
        }

        # Enforce fail-closed financial mutation authorization boundary
        authorize_financial_mutation(
            operation_name="update_ssot",
            target_resource=target_resource,
            payload=payload,
            caller_role=effective_actor,
            requires_owner_approval=requires_owner_approval,
        )

        approval_digest = compute_payload_digest(payload) if requires_owner_approval else None

        # R1/R3/R5: Acquire write lock and manage savepoint/transaction lifecycle
        with repository_transaction(self._conn):
            try:
                # Fetch before-state under write lock inside the transaction
                existing = self.get_cash(norm_account, norm_currency)
                before_state = (
                    {
                        "account_id": existing.account_id,
                        "currency": existing.currency,
                        "amount": decimal_to_canonical_str(existing.amount),
                    }
                    if existing
                    else None
                )

                after_state = {
                    "account_id": norm_account,
                    "currency": norm_currency,
                    "amount": amount_str,
                }

                self._conn.execute(
                    """
                    INSERT INTO portfolio_cash (account_id, currency, amount, last_updated_at_utc)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(account_id, currency) DO UPDATE SET
                        amount = excluded.amount,
                        last_updated_at_utc = excluded.last_updated_at_utc
                    """,
                    (norm_account, norm_currency, amount_str, valid_ts),
                )

                # Atomically increment database revision with mutation
                increment_db_revision(self._conn, valid_ts)

                # R1.2: Construct capability directly from validated local state and append changelog
                from python.database.repositories.audit import FinancialChangelogRepository

                cap = _FinancialMutationCapability(
                    credential=_acquire_mutation_credential(),
                    connection=self._conn,
                    correlation_id=cid,
                    actor_role=effective_actor,
                    operation_name="set_cash_balance",
                    target_resource=target_resource,
                    before_state=before_state,
                    after_state=after_state,
                    approval_digest=approval_digest,
                    timestamp_utc=valid_ts,
                )
                self._last_mutation_capability = cap
                FinancialChangelogRepository(self._conn).append_entry(mutation_capability=cap)
            except sqlite3.IntegrityError as e:
                raise IntegrityConflictError(f"Integrity constraint violation setting cash balance: {e}") from e

        return CashBalance(
            account_id=norm_account,
            currency=norm_currency,
            amount=dec_amount,
            last_updated_at_utc=valid_ts,
        )

    def delete_cash(
        self,
        account_id: str,
        currency: str,
        correlation_id: Optional[str] = None,
        caller_role: Optional[str] = None,
        requires_owner_approval: bool = False,
    ) -> bool:
        """Deletes a cash balance record with authorization and mandatory changelog capture (R1)."""
        self._check_active()
        norm_account = validate_identifier(account_id, "account_id")
        norm_currency = validate_identifier(currency, "currency").upper()

        target_resource = f"portfolio_cash/{norm_account}/{norm_currency}"
        payload = {"account_id": norm_account, "currency": norm_currency, "action": "delete"}

        effective_actor = _resolve_effective_actor(caller_role)
        authorize_financial_mutation(
            operation_name="update_ssot",
            target_resource=target_resource,
            payload=payload,
            caller_role=effective_actor,
            requires_owner_approval=requires_owner_approval,
        )

        timestamp_utc = utc_now_iso()
        cid = correlation_id or uuid.uuid4().hex
        approval_digest = compute_payload_digest(payload) if requires_owner_approval else None

        # R1/R3/R5: Acquire write lock and manage savepoint/transaction lifecycle
        with repository_transaction(self._conn):
            existing = self.get_cash(norm_account, norm_currency)
            if existing is None:
                return False

            before_state = {
                "account_id": existing.account_id,
                "currency": existing.currency,
                "amount": decimal_to_canonical_str(existing.amount),
            }

            self._conn.execute(
                "DELETE FROM portfolio_cash WHERE account_id = ? AND currency = ?",
                (norm_account, norm_currency),
            )

            # Atomically increment database revision when row is actually deleted
            increment_db_revision(self._conn, timestamp_utc)

            # R1.2: Construct capability directly from validated local state and append changelog
            from python.database.repositories.audit import FinancialChangelogRepository

            cap = _FinancialMutationCapability(
                credential=_acquire_mutation_credential(),
                connection=self._conn,
                correlation_id=cid,
                actor_role=effective_actor,
                operation_name="delete_cash",
                target_resource=target_resource,
                before_state=before_state,
                after_state=None,
                approval_digest=approval_digest,
                timestamp_utc=timestamp_utc,
            )
            self._last_mutation_capability = cap
            FinancialChangelogRepository(self._conn).append_entry(mutation_capability=cap)
            return True


# ---------------------------------------------------------------------------
# Position Repository
# ---------------------------------------------------------------------------

class PositionRepository(BaseRepository):
    """
    Repository for portfolio positions (portfolio_positions).
    Storage: exact Decimal TEXT (shares scale <= 4, wac_eur scale <= 4, wac_usd scale <= 4).
    """

    def get_position(self, account_id: str = "PRIMARY", ticker: str = "") -> Optional[PositionRecord]:
        """Retrieves a single position by account and ticker."""
        self._check_active()
        norm_account = validate_identifier(account_id, "account_id")
        norm_ticker = validate_identifier(ticker, "ticker").upper()
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT account_id, ticker, shares, wac_eur, wac_usd, last_updated_at_utc
            FROM portfolio_positions
            WHERE account_id = ? AND ticker = ?
            """,
            (norm_account, norm_ticker),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return PositionRecord(
            account_id=row[0],
            ticker=row[1],
            shares=Decimal(row[2]),
            wac_eur=Decimal(row[3]),
            wac_usd=Decimal(row[4]) if row[4] is not None else None,
            last_updated_at_utc=row[5],
        )

    def list_positions(self, account_id: Optional[str] = None) -> List[PositionRecord]:
        """Lists positions, optionally filtered by account_id."""
        self._check_active()
        cur = self._conn.cursor()
        if account_id:
            norm_account = validate_identifier(account_id, "account_id")
            cur.execute(
                """
                SELECT account_id, ticker, shares, wac_eur, wac_usd, last_updated_at_utc
                FROM portfolio_positions
                WHERE account_id = ?
                ORDER BY ticker ASC
                """,
                (norm_account,),
            )
        else:
            cur.execute(
                """
                SELECT account_id, ticker, shares, wac_eur, wac_usd, last_updated_at_utc
                FROM portfolio_positions
                ORDER BY account_id ASC, ticker ASC
                """
            )
        return [
            PositionRecord(
                account_id=r[0],
                ticker=r[1],
                shares=Decimal(r[2]),
                wac_eur=Decimal(r[3]),
                wac_usd=Decimal(r[4]) if r[4] is not None else None,
                last_updated_at_utc=r[5],
            )
            for r in cur.fetchall()
        ]

    def upsert_position(
        self,
        account_id: str,
        ticker: str,
        shares: Union[Decimal, int, str],
        wac_eur: Union[Decimal, int, str],
        wac_usd: Optional[Union[Decimal, int, str]] = None,
        correlation_id: Optional[str] = None,
        caller_role: Optional[str] = None,
        requires_owner_approval: bool = False,
        timestamp_utc: Optional[str] = None,
    ) -> PositionRecord:
        """
        Upserts a portfolio position.
        Enforces positive shares & cost basis, Decimal scale <= 4, authorization, and mandatory changelog atomicity (R1).
        Acquires write lock before reading before-state (R5).
        """
        self._check_active()
        norm_account = validate_identifier(account_id, "account_id")
        norm_ticker = validate_identifier(ticker, "ticker").upper()

        dec_shares = validate_decimal(
            shares, field_name="shares", max_scale=4, allow_negative=False, allow_zero=False
        )
        dec_wac_eur = validate_decimal(
            wac_eur, field_name="wac_eur", max_scale=4, allow_negative=False, allow_zero=False
        )
        dec_wac_usd = (
            validate_decimal(
                wac_usd, field_name="wac_usd", max_scale=4, allow_negative=False, allow_zero=False, allow_none=True
            )
            if wac_usd is not None
            else None
        )

        shares_str = decimal_to_canonical_str(dec_shares)
        wac_eur_str = decimal_to_canonical_str(dec_wac_eur)
        wac_usd_str = decimal_to_canonical_str(dec_wac_usd) if dec_wac_usd is not None else None
        valid_ts = validate_timestamp_utc(timestamp_utc)
        cid = correlation_id or uuid.uuid4().hex
        effective_actor = _resolve_effective_actor(caller_role)
        target_resource = f"portfolio_positions/{norm_account}/{norm_ticker}"

        payload = {
            "account_id": norm_account,
            "ticker": norm_ticker,
            "shares": shares_str,
            "wac_eur": wac_eur_str,
            "wac_usd": wac_usd_str,
        }

        authorize_financial_mutation(
            operation_name="update_ssot",
            target_resource=target_resource,
            payload=payload,
            caller_role=effective_actor,
            requires_owner_approval=requires_owner_approval,
        )

        approval_digest = compute_payload_digest(payload) if requires_owner_approval else None

        # R1/R3/R5: Acquire write lock and manage savepoint/transaction lifecycle
        with repository_transaction(self._conn):
            try:
                # Query before-state under write lock inside transaction
                existing = self.get_position(norm_account, norm_ticker)
                before_state = (
                    {
                        "account_id": existing.account_id,
                        "ticker": existing.ticker,
                        "shares": decimal_to_canonical_str(existing.shares),
                        "wac_eur": decimal_to_canonical_str(existing.wac_eur),
                        "wac_usd": decimal_to_canonical_str(existing.wac_usd) if existing.wac_usd is not None else None,
                    }
                    if existing
                    else None
                )

                after_state = {
                    "account_id": norm_account,
                    "ticker": norm_ticker,
                    "shares": shares_str,
                    "wac_eur": wac_eur_str,
                    "wac_usd": wac_usd_str,
                }

                self._conn.execute(
                    """
                    INSERT INTO portfolio_positions (
                        account_id, ticker, shares, wac_eur, wac_usd, last_updated_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(account_id, ticker) DO UPDATE SET
                        shares = excluded.shares,
                        wac_eur = excluded.wac_eur,
                        wac_usd = excluded.wac_usd,
                        last_updated_at_utc = excluded.last_updated_at_utc
                    """,
                    (norm_account, norm_ticker, shares_str, wac_eur_str, wac_usd_str, valid_ts),
                )

                # Atomically increment database revision with mutation
                increment_db_revision(self._conn, valid_ts)

                # R1.2: Construct capability directly from validated local state and append changelog
                from python.database.repositories.audit import FinancialChangelogRepository

                cap = _FinancialMutationCapability(
                    credential=_acquire_mutation_credential(),
                    connection=self._conn,
                    correlation_id=cid,
                    actor_role=effective_actor,
                    operation_name="upsert_position",
                    target_resource=target_resource,
                    before_state=before_state,
                    after_state=after_state,
                    approval_digest=approval_digest,
                    timestamp_utc=valid_ts,
                )
                self._last_mutation_capability = cap
                FinancialChangelogRepository(self._conn).append_entry(mutation_capability=cap)
            except sqlite3.IntegrityError as e:
                raise IntegrityConflictError(f"Integrity constraint violation upserting position: {e}") from e

        return PositionRecord(
            account_id=norm_account,
            ticker=norm_ticker,
            shares=dec_shares,
            wac_eur=dec_wac_eur,
            wac_usd=dec_wac_usd,
            last_updated_at_utc=valid_ts,
        )

    def delete_position(
        self,
        account_id: str,
        ticker: str,
        correlation_id: Optional[str] = None,
        caller_role: Optional[str] = None,
        requires_owner_approval: bool = False,
    ) -> bool:
        """Deletes a portfolio position with authorization and mandatory changelog capture (R1)."""
        self._check_active()
        norm_account = validate_identifier(account_id, "account_id")
        norm_ticker = validate_identifier(ticker, "ticker").upper()

        target_resource = f"portfolio_positions/{norm_account}/{norm_ticker}"
        payload = {"account_id": norm_account, "ticker": norm_ticker, "action": "delete"}

        effective_actor = _resolve_effective_actor(caller_role)
        authorize_financial_mutation(
            operation_name="update_ssot",
            target_resource=target_resource,
            payload=payload,
            caller_role=effective_actor,
            requires_owner_approval=requires_owner_approval,
        )

        timestamp_utc = utc_now_iso()
        cid = correlation_id or uuid.uuid4().hex
        approval_digest = compute_payload_digest(payload) if requires_owner_approval else None

        # R1/R3/R5: Acquire write lock and manage savepoint/transaction lifecycle
        with repository_transaction(self._conn):
            existing = self.get_position(norm_account, norm_ticker)
            if existing is None:
                return False

            before_state = {
                "account_id": existing.account_id,
                "ticker": existing.ticker,
                "shares": decimal_to_canonical_str(existing.shares),
                "wac_eur": decimal_to_canonical_str(existing.wac_eur),
                "wac_usd": decimal_to_canonical_str(existing.wac_usd) if existing.wac_usd is not None else None,
            }

            self._conn.execute(
                "DELETE FROM portfolio_positions WHERE account_id = ? AND ticker = ?",
                (norm_account, norm_ticker),
            )

            # Atomically increment database revision when row is actually deleted
            increment_db_revision(self._conn, timestamp_utc)

            # R1.2: Construct capability directly from validated local state and append changelog
            from python.database.repositories.audit import FinancialChangelogRepository

            cap = _FinancialMutationCapability(
                credential=_acquire_mutation_credential(),
                connection=self._conn,
                correlation_id=cid,
                actor_role=effective_actor,
                operation_name="delete_position",
                target_resource=target_resource,
                before_state=before_state,
                after_state=None,
                approval_digest=approval_digest,
                timestamp_utc=timestamp_utc,
            )
            self._last_mutation_capability = cap
            FinancialChangelogRepository(self._conn).append_entry(mutation_capability=cap)
            return True


# ---------------------------------------------------------------------------
# Watchlist Repository
# ---------------------------------------------------------------------------

class WatchlistRepository(BaseRepository):
    """
    Repository for tracked watchlist tickers (watchlist).
    """

    def get_watchlist_item(self, ticker: str) -> Optional[WatchlistItem]:
        """Retrieves a single watched ticker."""
        self._check_active()
        norm_ticker = validate_identifier(ticker, "ticker").upper()
        cur = self._conn.cursor()
        cur.execute(
            "SELECT ticker, status, added_at_utc, notes FROM watchlist WHERE ticker = ?",
            (norm_ticker,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return WatchlistItem(
            ticker=row[0],
            status=row[1],
            added_at_utc=row[2],
            notes=row[3],
        )

    def list_watchlist(self, status: Optional[str] = None) -> List[WatchlistItem]:
        """Lists watched tickers, optionally filtered by status."""
        self._check_active()
        cur = self._conn.cursor()
        if status:
            norm_status = validate_identifier(status, "status").upper()
            cur.execute(
                "SELECT ticker, status, added_at_utc, notes FROM watchlist WHERE status = ? ORDER BY ticker ASC",
                (norm_status,),
            )
        else:
            cur.execute(
                "SELECT ticker, status, added_at_utc, notes FROM watchlist ORDER BY ticker ASC"
            )
        return [
            WatchlistItem(
                ticker=r[0],
                status=r[1],
                added_at_utc=r[2],
                notes=r[3],
            )
            for r in cur.fetchall()
        ]

    def upsert_watchlist_item(
        self,
        ticker: str,
        status: str = "ACTIVE",
        notes: Optional[str] = None,
        correlation_id: Optional[str] = None,
        caller_role: Optional[str] = None,
        requires_owner_approval: bool = False,
        timestamp_utc: Optional[str] = None,
    ) -> WatchlistItem:
        """Upserts a watchlist item."""
        self._check_active()
        norm_ticker = validate_identifier(ticker, "ticker").upper()
        norm_status = validate_identifier(status, "status").upper()
        if norm_status not in ("ACTIVE", "PAUSED", "REMOVED"):
            raise ValidationError(f"Invalid watchlist status '{norm_status}'. Must be ACTIVE, PAUSED, or REMOVED.")

        valid_ts = validate_timestamp_utc(timestamp_utc)
        target_resource = f"watchlist/{norm_ticker}"
        payload = {"ticker": norm_ticker, "status": norm_status, "notes": notes}

        effective_actor = _resolve_effective_actor(caller_role)
        authorize_financial_mutation(
            operation_name="update_ssot",
            target_resource=target_resource,
            payload=payload,
            caller_role=effective_actor,
            requires_owner_approval=requires_owner_approval,
        )

        # R1/R3/R5: Acquire write lock and manage savepoint/transaction lifecycle
        with repository_transaction(self._conn):
            try:
                existing = self.get_watchlist_item(norm_ticker)
                added_at = existing.added_at_utc if existing else valid_ts

                before_state = (
                    {"ticker": existing.ticker, "status": existing.status, "notes": existing.notes}
                    if existing
                    else None
                )
                after_state = {"ticker": norm_ticker, "status": norm_status, "notes": notes}

                self._conn.execute(
                    """
                    INSERT INTO watchlist (ticker, status, added_at_utc, notes)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(ticker) DO UPDATE SET
                        status = excluded.status,
                        notes = excluded.notes
                    """,
                    (norm_ticker, norm_status, added_at, notes),
                )

                # Atomically increment database revision with mutation
                increment_db_revision(self._conn, valid_ts)

                cid = correlation_id or uuid.uuid4().hex
                approval_digest = compute_payload_digest(payload) if requires_owner_approval else None

                # R1.2: Construct capability directly from validated local state and append changelog
                from python.database.repositories.audit import FinancialChangelogRepository

                cap = _FinancialMutationCapability(
                    credential=_acquire_mutation_credential(),
                    connection=self._conn,
                    correlation_id=cid,
                    actor_role=effective_actor,
                    operation_name="upsert_watchlist_item",
                    target_resource=target_resource,
                    before_state=before_state,
                    after_state=after_state,
                    approval_digest=approval_digest,
                    timestamp_utc=valid_ts,
                )
                self._last_mutation_capability = cap
                FinancialChangelogRepository(self._conn).append_entry(mutation_capability=cap)
            except sqlite3.IntegrityError as e:
                raise IntegrityConflictError(f"Integrity constraint violation upserting watchlist: {e}") from e

        return WatchlistItem(
            ticker=norm_ticker,
            status=norm_status,
            added_at_utc=added_at,
            notes=notes,
        )

    def remove_watchlist_item(
        self,
        ticker: str,
        correlation_id: Optional[str] = None,
        caller_role: Optional[str] = None,
        requires_owner_approval: bool = False,
    ) -> bool:
        """Deletes a watchlist item from the table with mandatory changelog capture (R1)."""
        self._check_active()
        norm_ticker = validate_identifier(ticker, "ticker").upper()

        target_resource = f"watchlist/{norm_ticker}"
        payload = {"ticker": norm_ticker, "action": "delete"}

        effective_actor = _resolve_effective_actor(caller_role)
        authorize_financial_mutation(
            operation_name="update_ssot",
            target_resource=target_resource,
            payload=payload,
            caller_role=effective_actor,
            requires_owner_approval=requires_owner_approval,
        )

        timestamp_utc = utc_now_iso()
        # R1/R3/R5: Acquire write lock and manage savepoint/transaction lifecycle
        with repository_transaction(self._conn):
            existing = self.get_watchlist_item(norm_ticker)
            if existing is None:
                return False

            before_state = {"ticker": existing.ticker, "status": existing.status, "notes": existing.notes}

            self._conn.execute("DELETE FROM watchlist WHERE ticker = ?", (norm_ticker,))

            # Atomically increment database revision when row is actually deleted
            increment_db_revision(self._conn, timestamp_utc)

            cid = correlation_id or uuid.uuid4().hex
            approval_digest = compute_payload_digest(payload) if requires_owner_approval else None

            # R1.2: Construct capability directly from validated local state and append changelog
            from python.database.repositories.audit import FinancialChangelogRepository

            cap = _FinancialMutationCapability(
                credential=_acquire_mutation_credential(),
                connection=self._conn,
                correlation_id=cid,
                actor_role=effective_actor,
                operation_name="remove_watchlist_item",
                target_resource=target_resource,
                approval_digest=approval_digest,
                before_state=before_state,
                after_state=None,
                timestamp_utc=timestamp_utc,
            )
            self._last_mutation_capability = cap
            FinancialChangelogRepository(self._conn).append_entry(mutation_capability=cap)
            return True
