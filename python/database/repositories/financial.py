"""
Financial Repositories: Cash, Positions, and Watchlist.
Governed by Stage 1.4 Architecture and Agent Governance Framework.

Enforces:
- Exact Decimal validation on all financial quantities (shares, wac_eur, wac_usd, amount).
- Mandatory invocation of authorize_financial_mutation() on all mutations.
- Actor identity extraction from trusted context (get_active_caller_role()).
- Transactional atomicity: financial mutation + financial_changelog on the same connection.
- Autocommit protection: automatically manages atomic transaction if invoked outside an active transaction.
"""

from decimal import Decimal
import json
import sqlite3
from typing import Any, Dict, List, Optional, Union
import uuid

from python.database.repositories.base import (
    BaseRepository,
    CashBalance,
    PositionRecord,
    WatchlistItem,
    EntityNotFoundError,
    DuplicateEntityError,
    IntegrityConflictError,
    ValidationError,
    validate_decimal,
    decimal_to_canonical_str,
    validate_identifier,
    utc_now_iso,
)
from python.tool_registry import (
    authorize_financial_mutation,
    get_active_caller_role,
    compute_payload_digest,
    TRUSTED_SYSTEM_ROLES,
)


def _record_changelog(
    conn: sqlite3.Connection,
    correlation_id: str,
    actor_role: str,
    operation_name: str,
    target_resource: str,
    approval_digest: Optional[str],
    before_state: Optional[Dict[str, Any]],
    after_state: Optional[Dict[str, Any]],
    timestamp_utc: str,
) -> None:
    """Inserts an immutable audit entry into financial_changelog on the same connection."""
    before_json = json.dumps(before_state, sort_keys=True) if before_state is not None else "{}"
    after_json = json.dumps(after_state, sort_keys=True) if after_state is not None else "{}"
    conn.execute(
        """
        INSERT INTO financial_changelog (
            correlation_id, actor_role, operation_name, target_resource,
            approval_digest, before_state_json, after_state_json, timestamp_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            correlation_id,
            actor_role,
            operation_name,
            target_resource,
            approval_digest,
            before_json,
            after_json,
            timestamp_utc,
        ),
    )


def _resolve_effective_actor(caller_role: Optional[str] = None) -> str:
    """Determines trusted actor role without allowing agent spoofing."""
    active_role = get_active_caller_role()
    if active_role not in TRUSTED_SYSTEM_ROLES:
        return active_role
    return caller_role or active_role


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
        record_changelog: bool = True,
    ) -> CashBalance:
        """
        Sets the cash balance for an account and currency.
        Enforces Decimal scale <= 2, non-negative, authorization, and changelog atomicity.
        """
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
        timestamp_utc = utc_now_iso()
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
            caller_role=caller_role,
            requires_owner_approval=requires_owner_approval,
        )

        approval_digest = compute_payload_digest(payload) if requires_owner_approval else None

        # Fetch before-state for changelog
        existing = self.get_cash(norm_account, norm_currency)
        before_state = (
            {
                "account_id": existing.account_id,
                "currency": existing.currency,
                "amount": decimal_to_canonical_str(existing.amount),
                "last_updated_at_utc": existing.last_updated_at_utc,
            }
            if existing
            else None
        )

        after_state = {
            "account_id": norm_account,
            "currency": norm_currency,
            "amount": amount_str,
            "last_updated_at_utc": timestamp_utc,
        }

        # Ensure atomic transaction if not already in one
        owns_tx = not self._conn.in_transaction
        if owns_tx:
            self._conn.execute("BEGIN IMMEDIATE;")

        try:
            self._conn.execute(
                """
                INSERT INTO portfolio_cash (account_id, currency, amount, last_updated_at_utc)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(account_id, currency) DO UPDATE SET
                    amount = excluded.amount,
                    last_updated_at_utc = excluded.last_updated_at_utc
                """,
                (norm_account, norm_currency, amount_str, timestamp_utc),
            )
            if record_changelog:
                _record_changelog(
                    conn=self._conn,
                    correlation_id=cid,
                    actor_role=effective_actor,
                    operation_name="set_cash_balance",
                    target_resource=target_resource,
                    approval_digest=approval_digest,
                    before_state=before_state,
                    after_state=after_state,
                    timestamp_utc=timestamp_utc,
                )
            if owns_tx:
                self._conn.execute("COMMIT;")
        except sqlite3.IntegrityError as e:
            if owns_tx:
                try:
                    self._conn.execute("ROLLBACK;")
                except Exception:
                    pass
            raise IntegrityConflictError(f"Integrity constraint violation setting cash balance: {e}") from e
        except Exception:
            if owns_tx:
                try:
                    self._conn.execute("ROLLBACK;")
                except Exception:
                    pass
            raise

        return CashBalance(
            account_id=norm_account,
            currency=norm_currency,
            amount=dec_amount,
            last_updated_at_utc=timestamp_utc,
        )

    def delete_cash(
        self,
        account_id: str,
        currency: str,
        correlation_id: Optional[str] = None,
        caller_role: Optional[str] = None,
        requires_owner_approval: bool = False,
        record_changelog: bool = True,
    ) -> bool:
        """Deletes a cash balance record with authorization and changelog capture."""
        norm_account = validate_identifier(account_id, "account_id")
        norm_currency = validate_identifier(currency, "currency").upper()
        existing = self.get_cash(norm_account, norm_currency)
        if existing is None:
            return False

        target_resource = f"portfolio_cash/{norm_account}/{norm_currency}"
        payload = {"account_id": norm_account, "currency": norm_currency, "action": "delete"}

        authorize_financial_mutation(
            operation_name="update_ssot",
            target_resource=target_resource,
            payload=payload,
            caller_role=caller_role,
            requires_owner_approval=requires_owner_approval,
        )

        timestamp_utc = utc_now_iso()
        cid = correlation_id or uuid.uuid4().hex
        effective_actor = _resolve_effective_actor(caller_role)
        approval_digest = compute_payload_digest(payload) if requires_owner_approval else None

        before_state = {
            "account_id": existing.account_id,
            "currency": existing.currency,
            "amount": decimal_to_canonical_str(existing.amount),
            "last_updated_at_utc": existing.last_updated_at_utc,
        }

        owns_tx = not self._conn.in_transaction
        if owns_tx:
            self._conn.execute("BEGIN IMMEDIATE;")

        try:
            self._conn.execute(
                "DELETE FROM portfolio_cash WHERE account_id = ? AND currency = ?",
                (norm_account, norm_currency),
            )
            if record_changelog:
                _record_changelog(
                    conn=self._conn,
                    correlation_id=cid,
                    actor_role=effective_actor,
                    operation_name="delete_cash",
                    target_resource=target_resource,
                    approval_digest=approval_digest,
                    before_state=before_state,
                    after_state=None,
                    timestamp_utc=timestamp_utc,
                )
            if owns_tx:
                self._conn.execute("COMMIT;")
        except Exception:
            if owns_tx:
                try:
                    self._conn.execute("ROLLBACK;")
                except Exception:
                    pass
            raise
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
        record_changelog: bool = True,
    ) -> PositionRecord:
        """
        Upserts a portfolio position.
        Enforces positive shares & cost basis, Decimal scale <= 4, authorization, and changelog.
        """
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
        timestamp_utc = utc_now_iso()
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
            caller_role=caller_role,
            requires_owner_approval=requires_owner_approval,
        )

        approval_digest = compute_payload_digest(payload) if requires_owner_approval else None

        existing = self.get_position(norm_account, norm_ticker)
        before_state = (
            {
                "account_id": existing.account_id,
                "ticker": existing.ticker,
                "shares": decimal_to_canonical_str(existing.shares),
                "wac_eur": decimal_to_canonical_str(existing.wac_eur),
                "wac_usd": decimal_to_canonical_str(existing.wac_usd) if existing.wac_usd else None,
                "last_updated_at_utc": existing.last_updated_at_utc,
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
            "last_updated_at_utc": timestamp_utc,
        }

        owns_tx = not self._conn.in_transaction
        if owns_tx:
            self._conn.execute("BEGIN IMMEDIATE;")

        try:
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
                (norm_account, norm_ticker, shares_str, wac_eur_str, wac_usd_str, timestamp_utc),
            )
            if record_changelog:
                _record_changelog(
                    conn=self._conn,
                    correlation_id=cid,
                    actor_role=effective_actor,
                    operation_name="upsert_position",
                    target_resource=target_resource,
                    approval_digest=approval_digest,
                    before_state=before_state,
                    after_state=after_state,
                    timestamp_utc=timestamp_utc,
                )
            if owns_tx:
                self._conn.execute("COMMIT;")
        except sqlite3.IntegrityError as e:
            if owns_tx:
                try:
                    self._conn.execute("ROLLBACK;")
                except Exception:
                    pass
            raise IntegrityConflictError(f"Integrity constraint violation upserting position: {e}") from e
        except Exception:
            if owns_tx:
                try:
                    self._conn.execute("ROLLBACK;")
                except Exception:
                    pass
            raise

        return PositionRecord(
            account_id=norm_account,
            ticker=norm_ticker,
            shares=dec_shares,
            wac_eur=dec_wac_eur,
            wac_usd=dec_wac_usd,
            last_updated_at_utc=timestamp_utc,
        )

    def delete_position(
        self,
        account_id: str,
        ticker: str,
        correlation_id: Optional[str] = None,
        caller_role: Optional[str] = None,
        requires_owner_approval: bool = False,
        record_changelog: bool = True,
    ) -> bool:
        """Deletes a portfolio position with authorization and changelog capture."""
        norm_account = validate_identifier(account_id, "account_id")
        norm_ticker = validate_identifier(ticker, "ticker").upper()
        existing = self.get_position(norm_account, norm_ticker)
        if existing is None:
            return False

        target_resource = f"portfolio_positions/{norm_account}/{norm_ticker}"
        payload = {"account_id": norm_account, "ticker": norm_ticker, "action": "delete"}

        authorize_financial_mutation(
            operation_name="update_ssot",
            target_resource=target_resource,
            payload=payload,
            caller_role=caller_role,
            requires_owner_approval=requires_owner_approval,
        )

        timestamp_utc = utc_now_iso()
        cid = correlation_id or uuid.uuid4().hex
        effective_actor = _resolve_effective_actor(caller_role)
        approval_digest = compute_payload_digest(payload) if requires_owner_approval else None

        before_state = {
            "account_id": existing.account_id,
            "ticker": existing.ticker,
            "shares": decimal_to_canonical_str(existing.shares),
            "wac_eur": decimal_to_canonical_str(existing.wac_eur),
            "wac_usd": decimal_to_canonical_str(existing.wac_usd) if existing.wac_usd else None,
            "last_updated_at_utc": existing.last_updated_at_utc,
        }

        owns_tx = not self._conn.in_transaction
        if owns_tx:
            self._conn.execute("BEGIN IMMEDIATE;")

        try:
            self._conn.execute(
                "DELETE FROM portfolio_positions WHERE account_id = ? AND ticker = ?",
                (norm_account, norm_ticker),
            )
            if record_changelog:
                _record_changelog(
                    conn=self._conn,
                    correlation_id=cid,
                    actor_role=effective_actor,
                    operation_name="delete_position",
                    target_resource=target_resource,
                    approval_digest=approval_digest,
                    before_state=before_state,
                    after_state=None,
                    timestamp_utc=timestamp_utc,
                )
            if owns_tx:
                self._conn.execute("COMMIT;")
        except Exception:
            if owns_tx:
                try:
                    self._conn.execute("ROLLBACK;")
                except Exception:
                    pass
            raise
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
        record_changelog: bool = False,
    ) -> WatchlistItem:
        """Upserts a watchlist item."""
        norm_ticker = validate_identifier(ticker, "ticker").upper()
        norm_status = validate_identifier(status, "status").upper()
        if norm_status not in ("ACTIVE", "PAUSED", "REMOVED"):
            raise ValidationError(f"Invalid watchlist status '{norm_status}'. Must be ACTIVE, PAUSED, or REMOVED.")

        timestamp_utc = utc_now_iso()
        target_resource = f"watchlist/{norm_ticker}"
        payload = {"ticker": norm_ticker, "status": norm_status, "notes": notes}

        authorize_financial_mutation(
            operation_name="update_ssot",
            target_resource=target_resource,
            payload=payload,
            caller_role=caller_role,
            requires_owner_approval=requires_owner_approval,
        )

        existing = self.get_watchlist_item(norm_ticker)
        added_at = existing.added_at_utc if existing else timestamp_utc

        owns_tx = not self._conn.in_transaction
        if owns_tx:
            self._conn.execute("BEGIN IMMEDIATE;")

        try:
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
            if record_changelog:
                cid = correlation_id or uuid.uuid4().hex
                effective_actor = _resolve_effective_actor(caller_role)
                approval_digest = compute_payload_digest(payload) if requires_owner_approval else None
                before_state = (
                    {"ticker": existing.ticker, "status": existing.status, "notes": existing.notes}
                    if existing
                    else None
                )
                after_state = {"ticker": norm_ticker, "status": norm_status, "notes": notes}
                _record_changelog(
                    conn=self._conn,
                    correlation_id=cid,
                    actor_role=effective_actor,
                    operation_name="upsert_watchlist_item",
                    target_resource=target_resource,
                    approval_digest=approval_digest,
                    before_state=before_state,
                    after_state=after_state,
                    timestamp_utc=timestamp_utc,
                )
            if owns_tx:
                self._conn.execute("COMMIT;")
        except sqlite3.IntegrityError as e:
            if owns_tx:
                try:
                    self._conn.execute("ROLLBACK;")
                except Exception:
                    pass
            raise IntegrityConflictError(f"Integrity constraint violation upserting watchlist: {e}") from e
        except Exception:
            if owns_tx:
                try:
                    self._conn.execute("ROLLBACK;")
                except Exception:
                    pass
            raise

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
        record_changelog: bool = False,
    ) -> bool:
        """Deletes a watchlist item from the table."""
        norm_ticker = validate_identifier(ticker, "ticker").upper()
        existing = self.get_watchlist_item(norm_ticker)
        if existing is None:
            return False

        target_resource = f"watchlist/{norm_ticker}"
        payload = {"ticker": norm_ticker, "action": "delete"}

        authorize_financial_mutation(
            operation_name="update_ssot",
            target_resource=target_resource,
            payload=payload,
            caller_role=caller_role,
            requires_owner_approval=requires_owner_approval,
        )

        owns_tx = not self._conn.in_transaction
        if owns_tx:
            self._conn.execute("BEGIN IMMEDIATE;")

        try:
            self._conn.execute("DELETE FROM watchlist WHERE ticker = ?", (norm_ticker,))
            if record_changelog:
                timestamp_utc = utc_now_iso()
                cid = correlation_id or uuid.uuid4().hex
                effective_actor = _resolve_effective_actor(caller_role)
                approval_digest = compute_payload_digest(payload) if requires_owner_approval else None
                before_state = {"ticker": existing.ticker, "status": existing.status, "notes": existing.notes}
                _record_changelog(
                    conn=self._conn,
                    correlation_id=cid,
                    actor_role=effective_actor,
                    operation_name="remove_watchlist_item",
                    target_resource=target_resource,
                    approval_digest=approval_digest,
                    before_state=before_state,
                    after_state=None,
                    timestamp_utc=timestamp_utc,
                )
            if owns_tx:
                self._conn.execute("COMMIT;")
        except Exception:
            if owns_tx:
                try:
                    self._conn.execute("ROLLBACK;")
                except Exception:
                    pass
            raise
        return True
