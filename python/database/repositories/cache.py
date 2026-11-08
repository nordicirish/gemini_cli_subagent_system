"""
Market Data Cache Repository: DailyBarRepository.
Governed by Stage 1.4 Architecture and Agent Governance Framework.

Enforces:
- Strict Decimal validation on OHLC prices (scale <= 4, non-negative, no floats).
- Integer validation on volume (>= 0).
- YYYY-MM-DD date format validation.
- Idempotent upsert behavior for single bars and bulk batches.
- Strict isolation from core financial database: rejects core database connections.
"""

from decimal import Decimal
import json
import sqlite3
from typing import Any, Dict, List, Optional, Sequence, Union

from python.database.repositories.base import (
    BaseRepository,
    DailyBarRecord,
    EntityNotFoundError,
    IntegrityConflictError,
    ValidationError,
    validate_decimal,
    decimal_to_canonical_str,
    validate_identifier,
    validate_date_str,
    utc_now_iso,
)


class DailyBarRepository(BaseRepository):
    """
    Repository for reconstructible historical daily market data bars (daily_bars).
    Operates strictly on the market-data cache database profile.
    """

    def __init__(self, connection: sqlite3.Connection):
        super().__init__(connection)
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
        norm_ticker = validate_identifier(ticker, "ticker").upper()
        query = (
            "SELECT ticker, bar_date, open, high, low, close, volume, indicators_json, updated_at_utc "
            "FROM daily_bars WHERE ticker = ?"
        )
        params: List[Any] = [norm_ticker]
        if start_date:
            query += " AND bar_date >= ?"
            params.append(validate_date_str(start_date))
        if end_date:
            query += " AND bar_date <= ?"
            params.append(validate_date_str(end_date))
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
        """Upserts a single historical daily bar."""
        norm_ticker = validate_identifier(ticker, "ticker").upper()
        norm_date = validate_date_str(bar_date)

        dec_open = validate_decimal(open, "open", max_scale=4, allow_negative=False, allow_zero=True)
        dec_high = validate_decimal(high, "high", max_scale=4, allow_negative=False, allow_zero=True)
        dec_low = validate_decimal(low, "low", max_scale=4, allow_negative=False, allow_zero=True)
        dec_close = validate_decimal(close, "close", max_scale=4, allow_negative=False, allow_zero=True)

        if not isinstance(volume, int) or isinstance(volume, bool):
            raise ValidationError(f"volume must be an integer, got {type(volume).__name__}")
        if volume < 0:
            raise ValidationError(f"volume must be non-negative, got {volume}")

        open_str = decimal_to_canonical_str(dec_open)
        high_str = decimal_to_canonical_str(dec_high)
        low_str = decimal_to_canonical_str(dec_low)
        close_str = decimal_to_canonical_str(dec_close)
        indicators_json = json.dumps(indicators or {}, sort_keys=True)
        timestamp_utc = utc_now_iso()

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
                    volume,
                    indicators_json,
                    timestamp_utc,
                ),
            )
        except sqlite3.IntegrityError as e:
            raise IntegrityConflictError(f"Integrity violation upserting daily bar: {e}") from e

        return DailyBarRecord(
            ticker=norm_ticker,
            bar_date=norm_date,
            open=dec_open,
            high=dec_high,
            low=dec_low,
            close=dec_close,
            volume=volume,
            indicators=indicators or {},
            updated_at_utc=timestamp_utc,
        )

    def upsert_bars_batch(
        self, bars: Sequence[Union[DailyBarRecord, Dict[str, Any]]]
    ) -> int:
        """
        Performs an atomic batch upsert of multiple daily bars.
        Returns the count of upserted bars.
        """
        if not bars:
            return 0

        timestamp_utc = utc_now_iso()
        rows_to_insert = []
        for b in bars:
            if isinstance(b, DailyBarRecord):
                rows_to_insert.append((
                    b.ticker,
                    b.bar_date,
                    decimal_to_canonical_str(b.open),
                    decimal_to_canonical_str(b.high),
                    decimal_to_canonical_str(b.low),
                    decimal_to_canonical_str(b.close),
                    b.volume,
                    json.dumps(b.indicators, sort_keys=True),
                    timestamp_utc,
                ))
            elif isinstance(b, dict):
                norm_ticker = validate_identifier(b["ticker"], "ticker").upper()
                norm_date = validate_date_str(b["bar_date"])
                d_open = validate_decimal(b["open"], "open", max_scale=4, allow_negative=False, allow_zero=True)
                d_high = validate_decimal(b["high"], "high", max_scale=4, allow_negative=False, allow_zero=True)
                d_low = validate_decimal(b["low"], "low", max_scale=4, allow_negative=False, allow_zero=True)
                d_close = validate_decimal(b["close"], "close", max_scale=4, allow_negative=False, allow_zero=True)
                vol = b["volume"]
                if not isinstance(vol, int) or isinstance(vol, bool) or vol < 0:
                    raise ValidationError(f"Invalid volume: {vol}")
                ind = json.dumps(b.get("indicators") or {}, sort_keys=True)
                rows_to_insert.append((
                    norm_ticker,
                    norm_date,
                    decimal_to_canonical_str(d_open),
                    decimal_to_canonical_str(d_high),
                    decimal_to_canonical_str(d_low),
                    decimal_to_canonical_str(d_close),
                    vol,
                    ind,
                    timestamp_utc,
                ))
            else:
                raise ValidationError(f"Unsupported bar item type: {type(b).__name__}")

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
        except sqlite3.IntegrityError as e:
            raise IntegrityConflictError(f"Integrity violation in batch daily bar upsert: {e}") from e

        return len(rows_to_insert)

    def delete_bars(self, ticker: str) -> int:
        """Deletes all daily bars for a ticker. Returns count of deleted rows."""
        norm_ticker = validate_identifier(ticker, "ticker").upper()
        cur = self._conn.cursor()
        cur.execute("DELETE FROM daily_bars WHERE ticker = ?", (norm_ticker,))
        return cur.rowcount

