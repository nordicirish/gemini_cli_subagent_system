"""Deterministic Decision Log Backtester (Phase 2).

Evaluates historical decision_log entries against cached/provided OHLCV daily
bars to quantify realized 1d/3d/5d forward returns, max favorable/adverse
excursion (MAE), and directional hit rates without burning LLM tokens.
"""

from __future__ import annotations

from datetime import datetime
import math
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional

POSITIVE_ACTIONS = {"BUY", "ADD", "EXECUTE", "HOLD", "ACCUMULATE"}
DEFENSIVE_ACTIONS = {"TRIM", "SELL", "EXIT", "REDUCE", "LIQUIDATE", "REJECT", "AVOID"}
_NON_TICKER_LABELS = {"SESSION", "PORTFOLIO", "SYSTEM", "MARKET", "COUNCIL", "GLOBAL"}

_HIST_CACHE_LOCK = threading.RLock()
_BACKTEST_HIST_CACHE: Dict[str, tuple[float, Any]] = {}
_BACKTEST_HIST_TTL_SEC = 21600  # 6 hours


def _safe_float(val: Any) -> Optional[float]:
    if val is None or isinstance(val, bool):
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _default_history_provider(ticker: str) -> Any:
    sym = str(ticker or "").strip().upper()
    if not sym or sym in _NON_TICKER_LABELS:
        return None
    try:
        from fetch_stocks import cache
        cached_df = cache.history.get(sym)
        if cached_df is not None and not getattr(cached_df, "empty", True):
            return cached_df
    except Exception:
        try:
            from python.fetch_stocks import cache
            cached_df = cache.history.get(sym)
            if cached_df is not None and not getattr(cached_df, "empty", True):
                return cached_df
        except Exception:
            pass

    now = time.time()
    with _HIST_CACHE_LOCK:
        hit = _BACKTEST_HIST_CACHE.get(sym)
        if hit and (now - hit[0]) < _BACKTEST_HIST_TTL_SEC:
            return hit[1]

    try:
        import yfinance as yf
        df = yf.Ticker(sym).history(period="6mo", auto_adjust=False)
        if df is not None and not getattr(df, "empty", True):
            with _HIST_CACHE_LOCK:
                _BACKTEST_HIST_CACHE[sym] = (now, df)
            return df
    except Exception:
        return None
    return None


def _extract_ticker_from_entry(entry: Dict[str, Any]) -> str:
    raw = entry.get("ticker")
    if isinstance(raw, str) and raw.strip():
        sym = raw.strip().upper()
        return "" if sym in _NON_TICKER_LABELS else sym
    text = f"{entry.get('action_proposed', '')} {entry.get('lesson', '')}"
    for m in re.finditer(r"\b([A-Z]{2,5})\b", text):
        cand = m.group(1).upper()
        if cand not in _NON_TICKER_LABELS:
            return cand
    return ""


def _extract_action_from_entry(entry: Dict[str, Any]) -> str:
    for key in ("action", "trade_state", "verdict", "action_proposed", "decision", "outcome"):
        val = str(entry.get(key) or "").strip().upper()
        if not val:
            continue
        for tok in sorted(POSITIVE_ACTIONS | DEFENSIVE_ACTIONS, key=len, reverse=True):
            if tok in val:
                return tok
    return "HOLD"


def _flatten_decision_entries(decision_log: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    flattened: List[Dict[str, Any]] = []
    for item in decision_log:
        if not isinstance(item, dict):
            continue
        nested = item.get("decisions")
        if isinstance(nested, list) and nested:
            parent_date = item.get("date") or item.get("timestamp") or item.get("timestamp_utc")
            for sub in nested:
                if not isinstance(sub, dict):
                    continue
                merged = dict(sub)
                if parent_date and not (merged.get("date") or merged.get("timestamp") or merged.get("timestamp_utc")):
                    merged["timestamp"] = parent_date
                flattened.append(merged)
            continue
        if str(item.get("ticker") or "").strip().upper() in _NON_TICKER_LABELS:
            continue
        flattened.append(item)
    return flattened


def _parse_entry_date(raw_date: Any) -> Optional[datetime]:
    if not raw_date:
        return None
    s = str(raw_date).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(s[:len(fmt) + 2].rstrip("Z"), fmt.rstrip("Z"))
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def backtest_decision_log(
    decision_log: List[Dict[str, Any]],
    history_provider: Optional[Callable[[str], Any]] = None,
    max_entries: int = 25,
) -> Dict[str, Any]:
    """Evaluates decision_log entries against forward historical OHLCV bars."""
    raw_list = list(decision_log) if isinstance(decision_log, list) else []
    entries_list = _flatten_decision_entries(raw_list)
    provider = history_provider or _default_history_provider

    evaluated: List[Dict[str, Any]] = []
    by_action: Dict[str, Dict[str, Any]] = {}

    recent = entries_list[-max(1, int(max_entries or 25)):]
    for item in recent:
        if not isinstance(item, dict):
            continue
        ticker = _extract_ticker_from_entry(item)
        if not ticker:
            continue
        hist_df = provider(ticker)
        if hist_df is None or getattr(hist_df, "empty", True):
            continue
        if "Close" not in hist_df.columns:
            continue

        target_dt = _parse_entry_date(item.get("date") or item.get("timestamp") or item.get("timestamp_utc"))
        try:
            df = hist_df.sort_index()
            idx_dates = [ts.tz_convert(None) if getattr(ts, "tzinfo", None) is not None else ts for ts in df.index]
        except Exception:
            continue

        start_pos: Optional[int] = None
        if target_dt is not None:
            t_date = target_dt.date()
            for idx_i, idx_ts in enumerate(idx_dates):
                try:
                    bar_date = idx_ts.date()
                except Exception:
                    continue
                if bar_date >= t_date:
                    start_pos = idx_i
                    break
        elif len(df) >= 2:
            start_pos = 0

        if start_pos is None or start_pos >= len(df):
            continue

        logged_price = _safe_float(item.get("price") or item.get("price_at_eval") or item.get("entry_price"))
        entry_close = _safe_float(df.iloc[start_pos]["Close"])
        entry_price = logged_price if (logged_price and logged_price > 0) else entry_close
        if not entry_price or entry_price <= 0:
            continue

        fwd = df.iloc[start_pos + 1 : start_pos + 6]
        if fwd.empty:
            continue

        closes = [_safe_float(x) for x in fwd["Close"].tolist()]
        closes = [c for c in closes if c is not None]
        if not closes:
            continue

        highs = [_safe_float(x) for x in fwd["High"].tolist()] if "High" in fwd.columns else closes
        lows = [_safe_float(x) for x in fwd["Low"].tolist()] if "Low" in fwd.columns else closes
        valid_highs = [h for h in highs if h is not None] or closes
        valid_lows = [lo for lo in lows if lo is not None] or closes

        ret_1d = round(((closes[0] - entry_price) / entry_price) * 100.0, 2)
        ret_3d = round(((closes[min(2, len(closes) - 1)] - entry_price) / entry_price) * 100.0, 2)
        ret_5d = round(((closes[-1] - entry_price) / entry_price) * 100.0, 2)
        mfe = round(((max(valid_highs) - entry_price) / entry_price) * 100.0, 2)
        mae = round(((min(valid_lows) - entry_price) / entry_price) * 100.0, 2)

        action = _extract_action_from_entry(item)
        if action in DEFENSIVE_ACTIONS:
            hit = ret_5d < 0.0
        else:
            hit = ret_5d > 0.0

        audit = item.get("scrutiny_audit") if isinstance(item.get("scrutiny_audit"), dict) else {}
        sa_score = _safe_float(audit.get("agreement_score_sa") or item.get("agreement_score_sa"))
        row_res = {
            "date": str(item.get("date") or (target_dt.strftime("%Y-%m-%d") if target_dt else "")),
            "ticker": ticker,
            "action": action,
            "entry_price": round(entry_price, 4),
            "agreement_score_sa": round(sa_score, 4) if sa_score is not None else None,
            "bars_evaluated": len(closes),
            "ret_1d_pct": ret_1d,
            "ret_3d_pct": ret_3d,
            "ret_5d_pct": ret_5d,
            "max_favorable_pct": mfe,
            "max_adverse_pct": mae,
            "hit": hit,
        }
        evaluated.append(row_res)

        bucket = by_action.setdefault(action, {"count": 0, "hits": 0, "sum_5d_pct": 0.0, "sum_mae_pct": 0.0})
        bucket["count"] += 1
        if hit:
            bucket["hits"] += 1
        bucket["sum_5d_pct"] += ret_5d
        bucket["sum_mae_pct"] += mae

    n = len(evaluated)
    by_action_summary = {
        act: {
            "count": b["count"],
            "hit_rate_pct": round((b["hits"] / b["count"]) * 100.0, 1),
            "avg_ret_5d_pct": round(b["sum_5d_pct"] / b["count"], 2),
            "avg_mae_pct": round(b["sum_mae_pct"] / b["count"], 2),
        }
        for act, b in by_action.items()
    }

    return {
        "total_logged": len(entries_list),
        "evaluated_count": n,
        "hit_rate_pct": round((sum(1 for e in evaluated if e["hit"]) / n) * 100.0, 1) if n else None,
        "avg_ret_1d_pct": round(sum(e["ret_1d_pct"] for e in evaluated) / n, 2) if n else None,
        "avg_ret_5d_pct": round(sum(e["ret_5d_pct"] for e in evaluated) / n, 2) if n else None,
        "avg_mae_pct": round(sum(e["max_adverse_pct"] for e in evaluated) / n, 2) if n else None,
        "by_action": by_action_summary,
        "entries": evaluated,
    }
