"""
Live EUR/USD exchange rate.

Replaces the former hardcoded 1.08. The rate is fetched from Yahoo Finance (EURUSD=X) by a
background refresher and kept in memory with its observation time. Readers get a quote only
while it is fresh; otherwise they get None and must not invent a rate (fail closed):
derived USD figures are then left unchanged or shown as unavailable.

Note: agent position sizing uses BASE_CURRENCY_EXCHANGE_RATE from gem_trading_rules (a
protected asset) and is not affected by this module; the live quote is additionally exposed
to agents in the DATA_PACKET.
"""

import datetime
import json
import threading
import time
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Callable, Dict, Optional

EURUSD_SYMBOL = "EURUSD=X"
REFRESH_INTERVAL_SECONDS = 300
# FX markets close over weekends (and holidays): a Friday close must stay usable until Monday.
MAX_QUOTE_AGE = datetime.timedelta(days=4)
_RATE_SCALE = Decimal("0.000001")
_SANE_MIN = Decimal("0.5")
_SANE_MAX = Decimal("2.0")

_SOURCES = (
    ("yahoo_chart", "https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X?interval=1d&range=1d", ("chart", "result", 0, "meta")),
    ("yahoo_quote", "https://query1.finance.yahoo.com/v7/finance/quote?symbols=EURUSD=X", ("quoteResponse", "result", 0)),
)


class FxRateError(RuntimeError):
    """Raised when a live quote cannot be obtained or fails validation."""


@dataclass(frozen=True)
class FxQuote:
    rate: Decimal          # USD per 1 EUR
    as_of_utc: str         # observation time, ISO-8601 with Z
    source: str


_lock = threading.Lock()
_latest: Optional[FxQuote] = None
_last_attempt: float = 0.0
_refresher: Optional[threading.Thread] = None


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _iso_z(dt: datetime.datetime) -> str:
    return dt.astimezone(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso_z(value: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _validate_rate(raw: Any) -> Decimal:
    if isinstance(raw, bool) or isinstance(raw, float) or not isinstance(raw, (Decimal, int, str)):
        raise FxRateError(f"EUR/USD rate must be an exact Decimal, got {type(raw).__name__}")
    try:
        rate = Decimal(raw) if not isinstance(raw, Decimal) else raw
    except InvalidOperation as e:
        raise FxRateError(f"Invalid EUR/USD rate {raw!r}") from e
    if not rate.is_finite() or not (_SANE_MIN <= rate <= _SANE_MAX):
        raise FxRateError(f"EUR/USD rate {raw!r} outside sane range {_SANE_MIN}-{_SANE_MAX}")
    return rate.quantize(_RATE_SCALE, rounding=ROUND_HALF_UP)


def record_eurusd(rate: Any, as_of: datetime.datetime, source: str) -> FxQuote:
    """Validates and stores a quote. `as_of` must be timezone-aware."""
    global _latest
    if not isinstance(as_of, datetime.datetime) or as_of.tzinfo is None:
        raise FxRateError("EUR/USD quote time must be a timezone-aware datetime")
    quote = FxQuote(rate=_validate_rate(rate), as_of_utc=_iso_z(as_of), source=source)
    with _lock:
        if _latest is None or _parse_iso_z(quote.as_of_utc) >= _parse_iso_z(_latest.as_of_utc):
            _latest = quote
        return _latest


def get_eurusd() -> Optional[FxQuote]:
    """The latest quote if it is fresh enough to use, else None (never a default rate)."""
    with _lock:
        quote = _latest
    if quote is None:
        return None
    if _utc_now() - _parse_iso_z(quote.as_of_utc) > MAX_QUOTE_AGE:
        return None
    return quote


def _http_get(url: str, timeout: float):
    try:
        from fetch_stocks import safe_yf_get  # shared Yahoo session / headers
        return safe_yf_get(url, timeout=timeout)
    except ImportError:
        import requests
        return requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})


def fetch_eurusd_quote(timeout: float = 5.0) -> FxQuote:
    """Fetches and records a live quote, trying each source in turn."""
    errors = []
    for source, url, path in _SOURCES:
        try:
            resp = _http_get(url, timeout)
            if getattr(resp, "status_code", None) != 200:
                raise FxRateError(f"HTTP {getattr(resp, 'status_code', '?')}")
            node: Any = json.loads(resp.text, parse_float=Decimal)
            for key in path:
                node = node[key]
            price = node.get("regularMarketPrice")
            epoch = node.get("regularMarketTime")
            if not isinstance(epoch, int) or isinstance(epoch, bool):
                raise FxRateError("missing regularMarketTime")
            as_of = datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc)
            return record_eurusd(price, as_of, source)
        except Exception as e:
            errors.append(f"{source}: {e}")
    raise FxRateError("No live EUR/USD quote available (" + "; ".join(errors) + ")")


def refresh_eurusd(force: bool = False, log: Optional[Callable[[str], None]] = None) -> Optional[FxQuote]:
    """Refetches at most every REFRESH_INTERVAL_SECONDS; failures keep the last good quote."""
    global _last_attempt
    with _lock:
        if not force and time.monotonic() - _last_attempt < REFRESH_INTERVAL_SECONDS:
            due = False
        else:
            _last_attempt = time.monotonic()
            due = True
    if due:
        try:
            fetch_eurusd_quote()
        except Exception as e:
            if log:
                log(f"[FX Warning] EUR/USD refresh failed: {e}")
    return get_eurusd()


def start_background_refresher(log: Optional[Callable[[str], None]] = None) -> threading.Thread:
    """Starts the refresher thread once per process (idempotent)."""
    global _refresher

    def _loop():
        while True:
            refresh_eurusd(force=True, log=log)
            time.sleep(REFRESH_INTERVAL_SECONDS)

    with _lock:
        if _refresher is None or not _refresher.is_alive():
            _refresher = threading.Thread(target=_loop, name="eurusd-refresher", daemon=True)
            _refresher.start()
        return _refresher


def derive_usd_cash(eur_cash: Decimal, quote: FxQuote) -> Decimal:
    """USD equivalent of EUR cash at the quoted rate, rounded to cents (ROUND_HALF_UP)."""
    return (eur_cash * quote.rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fx_public(quote: Optional[FxQuote]) -> Dict[str, Any]:
    """JSON-safe description for APIs and the agent DATA_PACKET (rate as an exact string)."""
    if quote is None:
        return {"pair": "EURUSD", "available": False, "rate": None, "as_of_utc": None, "source": None}
    return {"pair": "EURUSD", "available": True, "rate": str(quote.rate), "as_of_utc": quote.as_of_utc, "source": quote.source}
