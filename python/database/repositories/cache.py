"""
Market Data Cache Repository: DailyBarRepository.
Governed by Stage 1.4.1 Architecture and Agent Governance Framework.

Enforces:
- R7: Daily bar batch atomicity: explicit transaction management (BEGIN IMMEDIATE / COMMIT / ROLLBACK) for upsert_bars_batch().
- R8: Unified input validation: identical validation for DailyBarRecord and dict inputs; strict calendar date validation; inverted range query rejection.
- R6: Strict Unit of Work lifecycle checking on all methods.
- Strict isolation from core financial database: rejects core database connections.
"""

from decimal import Decimal
import json
import sqlite3
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from python.database.repositories.base import (
    BaseRepository,
    DailyBarRecord,
    EntityNotFoundError,
    IntegrityConflictError,
    ValidationError,
    RepositoryError,
    validate_decimal,
    decimal_to_canonical_str,
    validate_identifier,
    validate_date_str,
    utc_now_iso,
)


def _validate_bar_inputs(
    ticker: Any,
    bar_date: Any,
    open_val: Any,
    high_val: Any,
    low_val: Any,
    close_val: Any,
    volume: Any,
    indicators: Any,
) -> Tuple[str, str, Decimal, Decimal, Decimal, Decimal, int, str]:
    """Unified validation helper for daily bar attributes (R8)."""
    norm_ticker = validate_identifier(str(ticker) if ticker is not None else "", "ticker").upper()
    norm_date = validate_date_str(bar_date)
    dec_open = validate_decimal(open_val, "open", max_scale=4, allow_negative=False, allow_zero=True)
    dec_high = validate_decimal(high_val, "high", max_scale=4, allow_negative=False, allow_zero=True)
    dec_low = validate_decimal(low_val, "low", max_scale=4, allow_negative=False, allow_zero=True)
    dec_close = validate_decimal(close_val, "close", max_scale=4, allow_negative=False, allow_zero=True)

    if not isinstance(volume, int) or isinstance(volume, bool):
        raise ValidationError(f"volume must be an integer, got {type(volume).__name__}")
    if volume < 0:
        raise ValidationError(f"volume must be non-negative, got {volume}")

    if indicators is not None and not isinstance(indicators, dict):
        raise ValidationError(f"indicators must be a dictionary or None, got {type(indicators).__name__}")
    try:
        indicators_json = json.dumps(indicators or {}, sort_keys=True)
    except (TypeError, ValueError) as e:
        raise ValidationError(f"indicators could not be serialized to JSON: {e}") from e

    return (
        norm_ticker,
        norm_date,
        dec_open,
        dec_high,
        dec_low,
        dec_close,
        volume,
        indicators_json,
    )


class DailyBarRepository(BaseRepository):
    """
    Repository for reconstructible historical daily market data bars (daily_bars).
    Operates strictly on the market-data cache database profile.
    """

    def __init__(self, connection: sqlite3.Connection, uow: Any = None):
        super().__init__(connection, uow=uow)
        self._verify_cache_isolation()

    def _verify_cache_isolation(self) -> None:
        """Verifies that this repository is not connected to the primary core financial database."""
        cur = self._conn.cursor()
        cur.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='portfolio_cash'"
        )
        if cur.fetchone()[0] > 0:
            raise ValidationError(
                "Cross-database boundary violation: DailyBarRepository cannot be bound to "
                "the primary core financial database. Market-data cache persistence must remain "
                "strictly isolated from authoritative financial state."
            )

    def get_bar(self, ticker: str, bar_date: str) -> Optional[DailyBarRecord]:
        """Retrieves a single daily bar by ticker and date."""
        self._check_active()
        norm_ticker = validate_identifier(ticker, "ticker").upper()
        norm_date = validate_date_str(bar_date)
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT ticker, bar_date, open, high, low, close, volume, indicators_json, updated_at_utc
            FROM daily_bars
            WHERE ticker = ? AND bar_date = ?
            """,
            (norm_ticker, norm_date),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return DailyBarRecord(
            ticker=row[0],
            bar_date=row[1],
            open=Decimal(row[2]),
            high=Decimal(row[3]),
            low=Decimal(row[4]),
            close=Decimal(row[5]),
            volume=row[6],
            indicators=json.loads(row[7]),
            updated_at_utc=row[8],
        )

    def list_bars(
        self,
        ticker: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[DailyBarRecord]:
        """Lists daily bars for a ticker within an optional date range, ordered by bar_date ASC."""
        self._check_active()
        norm_ticker = validate_identifier(ticker, "ticker").upper()
        query = (
            "SELECT ticker, bar_date, open, high, low, close, volume, indicators_json, updated_at_utc "
            "FROM daily_bars WHERE ticker = ?"
        )
        params: List[Any] = [norm_ticker]
        if start_date:
            norm_start = validate_date_str(start_date)
            query += " AND bar_date >= ?"
            params.append(norm_start)
        if end_date:
            norm_end = validate_date_str(end_date)
            query += " AND bar_date <= ?"
            params.append(norm_end)

        # R8: Reject inverted date ranges fail-closed
        if start_date and end_date and start_date > end_date:
            raise ValidationError(
                f"Inverted date range: start_date '{start_date}' cannot be after end_date '{end_date}'"
            )

        query += " ORDER BY bar_date ASC"

        cur = self._conn.cursor()
        cur.execute(query, tuple(params))
        return [
            DailyBarRecord(
                ticker=r[0],
                bar_date=r[1],
                open=Decimal(r[2]),
                high=Decimal(r[3]),
                low=Decimal(r[4]),
                close=Decimal(r[5]),
                volume=r[6],
                indicators=json.loads(r[7]),
                updated_at_utc=r[8],
            )
            for r in cur.fetchall()
        ]

    def upsert_bar(
        self,
        ticker: str,
        bar_date: str,
        open: Union[Decimal, int, str],
        high: Union[Decimal, int, str],
        low: Union[Decimal, int, str],
        close: Union[Decimal, int, str],
        volume: int,
        indicators: Optional[Dict[str, Any]] = None,
    ) -> DailyBarRecord:
        """Upserts a single historical daily bar with strict unified validation (R8)."""
        self._check_active()
        (
            norm_ticker,
            norm_date,
            dec_open,
            dec_high,
            dec_low,
            dec_close,
            vol,
            indicators_json,
        ) = _validate_bar_inputs(ticker, bar_date, open, high, low, close, volume, indicators)

        open_str = decimal_to_canonical_str(dec_open)
        high_str = decimal_to_canonical_str(dec_high)
        low_str = decimal_to_canonical_str(dec_low)
        close_str = decimal_to_canonical_str(dec_close)
        timestamp_utc = utc_now_iso()

        owns_tx = not self._conn.in_transaction
        if owns_tx:
            self._conn.execute("BEGIN IMMEDIATE;")

        cur = self._conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO daily_bars (
                    ticker, bar_date, open, high, low, close, volume, indicators_json, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker, bar_date) DO UPDATE SET
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    volume = excluded.volume,
                    indicators_json = excluded.indicators_json,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (
                    norm_ticker,
                    norm_date,
                    open_str,
                    high_str,
                    low_str,
                    close_str,
                    vol,
                    indicators_json,
                    timestamp_utc,
                ),
            )
            if owns_tx:
                self._conn.execute("COMMIT;")
        except sqlite3.IntegrityError as e:
            if owns_tx:
                try:
                    self._conn.execute("ROLLBACK;")
                except Exception:
                    pass
            raise IntegrityConflictError(f"Integrity violation upserting daily bar: {e}") from e
        except Exception:
            if owns_tx:
                try:
                    self._conn.execute("ROLLBACK;")
                except Exception:
                    pass
            raise

        return DailyBarRecord(
            ticker=norm_ticker,
            bar_date=norm_date,
            open=dec_open,
            high=dec_high,
            low=dec_low,
            close=dec_close,
            volume=vol,
            indicators=indicators or {},
            updated_at_utc=timestamp_utc,
        )

    def upsert_bars_batch(
        self, bars: Sequence[Union[DailyBarRecord, Dict[str, Any]]]
    ) -> int:
        """
        Performs an atomic batch upsert of multiple daily bars (R7).
        Enforces identical upfront validation on DailyBarRecord and dict items (R8).
        Rolls back the entire batch if any record or write fails.
        """
        self._check_active()
        if not bars:
            return 0

        timestamp_utc = utc_now_iso()
        rows_to_insert = []

        # R8: Validate ALL records upfront before any database writes
        for b in bars:
            if isinstance(b, DailyBarRecord):
                (
                    norm_ticker,
                    norm_date,
                    dec_open,
                    dec_high,
                    dec_low,
                    dec_close,
                    vol,
                    ind_json,
                ) = _validate_bar_inputs(
                    ticker=b.ticker,
                    bar_date=b.bar_date,
                    open_val=b.open,
                    high_val=b.high,
                    low_val=b.low,
                    close_val=b.close,
                    volume=b.volume,
                    indicators=b.indicators,
                )
                rows_to_insert.append((
                    norm_ticker,
                    norm_date,
                    decimal_to_canonical_str(dec_open),
                    decimal_to_canonical_str(dec_high),
                    decimal_to_canonical_str(dec_low),
                    decimal_to_canonical_str(dec_close),
                    vol,
                    ind_json,
                    timestamp_utc,
                ))
            elif isinstance(b, dict):
                (
                    norm_ticker,
                    norm_date,
                    dec_open,
                    dec_high,
                    dec_low,
                    dec_close,
                    vol,
                    ind_json,
                ) = _validate_bar_inputs(
                    ticker=b.get("ticker"),
                    bar_date=b.get("bar_date"),
                    open_val=b.get("open"),
                    high_val=b.get("high"),
                    low_val=b.get("low"),
                    close_val=b.get("close"),
                    volume=b.get("volume"),
                    indicators=b.get("indicators"),
                )
                rows_to_insert.append((
                    norm_ticker,
                    norm_date,
                    decimal_to_canonical_str(dec_open),
                    decimal_to_canonical_str(dec_high),
                    decimal_to_canonical_str(dec_low),
                    decimal_to_canonical_str(dec_close),
                    vol,
                    ind_json,
                    timestamp_utc,
                ))
            else:
                raise ValidationError(f"Unsupported bar item type: {type(b).__name__}")

        # R7: Atomic transaction management across the batch
        owns_tx = not self._conn.in_transaction
        if owns_tx:
            self._conn.execute("BEGIN IMMEDIATE;")

        cur = self._conn.cursor()
        try:
            cur.executemany(
                """
                INSERT INTO daily_bars (
                    ticker, bar_date, open, high, low, close, volume, indicators_json, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker, bar_date) DO UPDATE SET
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    volume = excluded.volume,
                    indicators_json = excluded.indicators_json,
                    updated_at_utc = excluded.updated_at_utc
                """,
                rows_to_insert,
            )
            if owns_tx:
                self._conn.execute("COMMIT;")
        except sqlite3.IntegrityError as e:
            if owns_tx:
                try:
                    self._conn.execute("ROLLBACK;")
                except Exception:
                    pass
            raise IntegrityConflictError(f"Integrity violation in batch daily bar upsert: {e}") from e
        except Exception:
            if owns_tx:
                try:
                    self._conn.execute("ROLLBACK;")
                except Exception:
                    pass
            raise

        return len(rows_to_insert)

    def delete_bars(self, ticker: str) -> int:
        """Deletes all daily bars for a ticker. Returns count of deleted rows."""
        self._check_active()
        norm_ticker = validate_identifier(ticker, "ticker").upper()
        cur = self._conn.cursor()
        cur.execute("DELETE FROM daily_bars WHERE ticker = ?", (norm_ticker,))
        return cur.rowcount
