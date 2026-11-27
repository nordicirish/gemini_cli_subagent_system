"""Read-Only Parameterized Quantitative Investigation Engine (Local + Colab Offload).

Enables the AI Council (`Terminal Orchestrator`, `Data Analyst`, `Research Engine`)
and `/api/analytics/investigate` to run audited, zero-lookahead historical backtests
and regime studies on demand without arbitrary code execution or SSoT/rule mutations.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
import re
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

NY_TZ = ZoneInfo("America/New_York")
DEFAULT_INVESTIGATION_CACHE_PATH = os.path.join("cache", "last_quant_investigation.json")
_WORKERS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "colab_workers")

VALID_STUDY_TYPES: Tuple[str, ...] = (
    "overnight_to_morning_peak",
    "gap_behavior",
    "vwap_reversion",
    "regime_forward_returns",
)
VALID_COMPUTE_TARGETS: Tuple[str, ...] = ("local", "colab")

_CLOCK_EXIT_SPECS: Tuple[Tuple[str, int], ...] = (
    ("exit_0935_et", 935),
    ("exit_0945_et", 945),
    ("exit_1000_et", 1000),
    ("exit_1015_et", 1015),
    ("exit_1030_et", 1030),
    ("exit_1100_et", 1100),
)


def _safe_float(val: Any) -> Optional[float]:
    if val is None or isinstance(val, bool):
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def validate_ticker(ticker: Any) -> str:
    sym = str(ticker or "").strip().upper()
    if not re.match(r"^[A-Z0-9.\-\^]{1,12}$", sym):
        raise ValueError(f"Invalid ticker symbol {ticker!r}")
    return sym


def _parse_hhmm(raw: str, *, default_hhmm: int) -> int:
    s = str(raw or "").strip()
    if not s:
        return default_hhmm
    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if not m:
        raise ValueError(f"Expected HH:MM time in ET, got {raw!r}")
    hh, mm = int(m.group(1)), int(m.group(2))
    if hh < 0 or hh > 23 or mm < 0 or mm > 59:
        raise ValueError(f"Invalid clock time {raw!r}")
    hhmm = hh * 100 + mm
    if hhmm < 930 or hhmm > 1600:
        raise ValueError(f"RTH time must be between 09:30 and 16:00 ET, got {raw!r}")
    return hhmm


def _normalize_ohlcv_df(df: Any) -> pd.DataFrame:
    """Flattens MultiIndex yfinance columns and aligns index to America/New_York."""
    if df is None or getattr(df, "empty", True):
        return pd.DataFrame()
    work = df.copy()
    if isinstance(work.columns, pd.MultiIndex):
        lvl0 = [str(c[0]) for c in work.columns]
        if any(c in ("Close", "Open", "High", "Low") for c in lvl0):
            work.columns = lvl0
        else:
            work.columns = [str(c[-1]) for c in work.columns]
    else:
        work.columns = [str(c) for c in work.columns]
    # Deduplicate columns if any repeated names appeared after flattening
    work = work.loc[:, ~work.columns.duplicated()]
    for req in ("Open", "High", "Low", "Close"):
        if req not in work.columns:
            return pd.DataFrame()
    idx = work.index
    if getattr(idx, "tz", None) is not None:
        work.index = idx.tz_convert(NY_TZ)
    else:
        work.index = pd.DatetimeIndex(idx).tz_localize(NY_TZ)
    return work.sort_index()


def _fetch_yfinance_bars(sym: str, *, period: str, interval: str) -> pd.DataFrame:
    import yfinance as yf

    df = yf.Ticker(sym).history(period=period, interval=interval, prepost=False, auto_adjust=False)
    return _normalize_ohlcv_df(df)


def _summarize_net_returns(
    net_pcts: Sequence[float],
    gross_pcts: Optional[Sequence[float]] = None,
    *,
    fill_flags: Optional[Sequence[bool]] = None,
) -> Dict[str, Any]:
    arr = np.asarray([float(x) for x in net_pcts if math.isfinite(float(x))], dtype=float)
    n = int(arr.size)
    if n == 0:
        return {
            "trades": 0,
            "win_rate_pct": 0.0,
            "mean_net_pct": 0.0,
            "median_net_pct": 0.0,
            "compounded_net_pct": 0.0,
            "sharpe_ann": 0.0,
            "max_drawdown_pct": 0.0,
            "p5_net_pct": 0.0,
            "worst_trade_pct": 0.0,
            "best_trade_pct": 0.0,
        }

    wins = int(np.sum(arr > 0.0))
    mean_v = float(np.mean(arr))
    med_v = float(np.median(arr))
    std_v = float(np.std(arr, ddof=1)) if n > 1 else 0.0
    sharpe = (mean_v / std_v) * math.sqrt(252.0) if std_v > 1e-9 else 0.0

    growth = np.cumprod(np.maximum(0.01, 1.0 + arr / 100.0))
    peaks = np.maximum.accumulate(np.insert(growth, 0, 1.0))[1:]
    dd = (growth / peaks - 1.0) * 100.0
    max_dd = float(np.min(dd)) if dd.size else 0.0
    comp_pct = float((growth[-1] - 1.0) * 100.0)

    out: Dict[str, Any] = {
        "trades": n,
        "win_rate_pct": round((wins / n) * 100.0, 2),
        "mean_net_pct": round(mean_v, 3),
        "median_net_pct": round(med_v, 3),
        "compounded_net_pct": round(comp_pct, 2),
        "sharpe_ann": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "p5_net_pct": round(float(np.percentile(arr, 5)), 3),
        "worst_trade_pct": round(float(np.min(arr)), 3),
        "best_trade_pct": round(float(np.max(arr)), 3),
    }
    if gross_pcts is not None and len(gross_pcts) == n:
        out["mean_gross_pct"] = round(float(np.mean(gross_pcts)), 3)
    if fill_flags is not None and len(fill_flags) == n:
        out["limit_fill_rate_pct"] = round((sum(1 for f in fill_flags if f) / n) * 100.0, 1)
    return out


def _bootstrap_mean_ci(net_pcts: Sequence[float], n_paths: int = 2000, seed: int = 42) -> Dict[str, Any]:
    arr = np.asarray([float(x) for x in net_pcts if math.isfinite(float(x))], dtype=float)
    if arr.size < 3 or n_paths < 100:
        return {"paths": 0, "p5_mean_net_pct": None, "p50_mean_net_pct": None, "p95_mean_net_pct": None, "prob_positive_mean_pct": None}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, arr.size, size=(int(n_paths), arr.size))
    means = np.mean(arr[idx], axis=1)
    return {
        "paths": int(n_paths),
        "p5_mean_net_pct": round(float(np.percentile(means, 5)), 3),
        "p50_mean_net_pct": round(float(np.percentile(means, 50)), 3),
        "p95_mean_net_pct": round(float(np.percentile(means, 95)), 3),
        "prob_positive_mean_pct": round(float(np.mean(means > 0.0) * 100.0), 1),
    }


def _build_vix_lookup(vix_daily: Any) -> Dict[Any, float]:
    norm = _normalize_ohlcv_df(vix_daily)
    if norm.empty:
        return {}
    out: Dict[Any, float] = {}
    for ts, row in norm.iterrows():
        c = _safe_float(row.get("Close"))
        if c is not None and c > 0:
            out[ts.date()] = c
    return out


def _bar_exit_price_at_hhmm(day_df: pd.DataFrame, target_hhmm: int) -> Optional[float]:
    hhmm = day_df.index.hour * 100 + day_df.index.minute
    sub = day_df[hhmm >= target_hhmm]
    if sub.empty:
        return _safe_float(day_df.iloc[-1].get("Close"))
    return _safe_float(sub.iloc[0].get("Close"))


def _simulate_morning_limit_trade(
    morning_df: pd.DataFrame,
    *,
    entry_px: float,
    limit_px: float,
    cutoff_px: float,
    stop_loss_pct: Optional[float] = None,
) -> Tuple[float, bool]:
    """Causal per-bar morning execution simulation (limit order + cutoff, optional stop).

    Returns (exit_px, limit_filled). If a stop and limit occur inside the same 5m bar,
    conservatively assumes stop triggered first.
    """
    stop_px = entry_px * (1.0 - float(stop_loss_pct) / 100.0) if (stop_loss_pct and stop_loss_pct > 0) else None
    for _, bar in morning_df.iterrows():
        b_open = _safe_float(bar.get("Open"))
        b_high = _safe_float(bar.get("High"))
        b_low = _safe_float(bar.get("Low"))
        if b_open is None or b_high is None or b_low is None:
            continue
        if stop_px is not None:
            if b_open <= stop_px:
                return b_open, False
            if b_low <= stop_px:
                return stop_px, False
        if b_open >= limit_px:
            return b_open, True
        if b_high >= limit_px:
            return limit_px, True
    return cutoff_px, False


def evaluate_overnight_to_morning_peak(
    ticker: str,
    *,
    bars_5m: Any = None,
    vix_daily: Any = None,
    lookback_days: int = 60,
    friction_pct: float = 0.85,
    entry_time_et: str = "15:55",
    exit_cutoff_et: str = "10:30",
    stop_loss_pct: Optional[float] = None,
    bootstrap_paths: int = 2000,
) -> Dict[str, Any]:
    """Tests buying near close and exiting into the next morning window net of friction."""
    sym = validate_ticker(ticker)
    entry_hhmm = _parse_hhmm(entry_time_et, default_hhmm=1555)
    cutoff_hhmm = _parse_hhmm(exit_cutoff_et, default_hhmm=1030)
    if cutoff_hhmm <= 930:
        cutoff_hhmm = 1030
    friction = max(0.0, min(5.0, float(friction_pct)))

    raw_5m = _normalize_ohlcv_df(bars_5m) if bars_5m is not None else _fetch_yfinance_bars(
        sym, period=f"{max(10, min(60, int(lookback_days or 60)))}d", interval="5m"
    )
    if raw_5m.empty:
        return {"status": "unavailable", "study_type": "overnight_to_morning_peak", "ticker": sym, "error": "No 5m OHLCV data returned"}

    if vix_daily is None and bars_5m is None:
        try:
            vix_daily = _fetch_yfinance_bars("^VIX", period="6mo", interval="1d")
        except Exception:
            vix_daily = None
    vix_map = _build_vix_lookup(vix_daily)

    hhmm = raw_5m.index.hour * 100 + raw_5m.index.minute
    rth = raw_5m[(hhmm >= 930) & (hhmm < 1600)].sort_index()
    sessions: List[Tuple[Any, pd.DataFrame]] = [
        (d, g) for d, g in rth.groupby(rth.index.date) if len(g) >= 10
    ]
    if len(sessions) < 3:
        return {"status": "unavailable", "study_type": "overnight_to_morning_peak", "ticker": sym, "error": "Fewer than 3 RTH sessions available"}

    clock_gross: Dict[str, List[float]] = {k: [] for k, _ in _CLOCK_EXIT_SPECS}
    clock_net: Dict[str, List[float]] = {k: [] for k, _ in _CLOCK_EXIT_SPECS}

    target_specs = [("limit_+1.5pct_or_cutoff", 1.5), ("limit_+2.5pct_or_cutoff", 2.5), ("limit_+3.5pct_or_cutoff", 3.5)]
    limit_gross: Dict[str, List[float]] = {k: [] for k, _ in target_specs}
    limit_net: Dict[str, List[float]] = {k: [] for k, _ in target_specs}
    limit_fills: Dict[str, List[bool]] = {k: [] for k, _ in target_specs}

    emp_gross: List[float] = []
    emp_net: List[float] = []
    emp_fills: List[bool] = []
    prior_morning_upsides: List[float] = []

    oracle_gross: List[float] = []
    oracle_net: List[float] = []
    overnight_gaps: List[float] = []
    pair_meta: List[Dict[str, Any]] = []

    for i in range(len(sessions) - 1):
        d0, df0 = sessions[i]
        d1, df1 = sessions[i + 1]
        if (d1 - d0).days > 5:
            continue

        hh0 = df0.index.hour * 100 + df0.index.minute
        entry_bars = df0[hh0 <= entry_hhmm]
        if entry_bars.empty:
            continue
        entry_px = _safe_float(entry_bars.iloc[-1].get("Close"))
        d0_open = _safe_float(df0.iloc[0].get("Open")) or _safe_float(df0.iloc[0].get("Close"))

        hh1 = df1.index.hour * 100 + df1.index.minute
        next_open = _safe_float(df1.iloc[0].get("Open")) or _safe_float(df1.iloc[0].get("Close"))
        morning_df = df1[(hh1 >= 930) & (hh1 <= cutoff_hhmm)]
        if morning_df.empty or not entry_px or entry_px <= 0 or not next_open or next_open <= 0:
            continue

        cutoff_px = _bar_exit_price_at_hhmm(df1, cutoff_hhmm)
        if not cutoff_px or cutoff_px <= 0:
            continue

        overnight_gaps.append(((next_open - entry_px) / entry_px) * 100.0)
        vix_val = vix_map.get(d0)
        d0_green = bool(d0_open and d0_open > 0 and entry_px >= d0_open)

        for key, exit_hhmm in _CLOCK_EXIT_SPECS:
            ex_px = _bar_exit_price_at_hhmm(df1, exit_hhmm) or cutoff_px
            g_ret = ((ex_px - entry_px) / entry_px) * 100.0
            clock_gross[key].append(g_ret)
            clock_net[key].append(g_ret - friction)

        for key, pct in target_specs:
            lim_px = entry_px * (1.0 + pct / 100.0)
            ex_px, filled = _simulate_morning_limit_trade(
                morning_df, entry_px=entry_px, limit_px=lim_px, cutoff_px=cutoff_px, stop_loss_pct=stop_loss_pct
            )
            g_ret = ((ex_px - entry_px) / entry_px) * 100.0
            limit_gross[key].append(g_ret)
            limit_net[key].append(g_ret - friction)
            limit_fills[key].append(filled)

        # Causal expanding prior p50 front-run limit (-0.25% buffer) computed strictly from sessions < d1
        if len(prior_morning_upsides) >= 5:
            prior_p50_up = float(np.median(prior_morning_upsides))
            emp_lim = next_open * (1.0 + max(0.0, prior_p50_up) / 100.0) * 0.9975
            ex_px, filled = _simulate_morning_limit_trade(
                morning_df, entry_px=entry_px, limit_px=emp_lim, cutoff_px=cutoff_px, stop_loss_pct=stop_loss_pct
            )
        else:
            ex_px, filled = cutoff_px, False
        g_emp = ((ex_px - entry_px) / entry_px) * 100.0
        emp_gross.append(g_emp)
        emp_net.append(g_emp - friction)
        emp_fills.append(filled)

        m_high_series = df1[hh1 <= 1130].get("High")
        m_high = _safe_float(m_high_series.max()) if m_high_series is not None and not m_high_series.empty else None
        if m_high and m_high > 0:
            prior_morning_upsides.append(((m_high - next_open) / next_open) * 100.0)
            g_or = ((m_high - entry_px) / entry_px) * 100.0
            oracle_gross.append(g_or)
            oracle_net.append(g_or - friction)

        pair_meta.append({"d0": str(d0), "d1": str(d1), "vix": vix_val, "d0_green": d0_green})

    if not pair_meta:
        return {"status": "unavailable", "study_type": "overnight_to_morning_peak", "ticker": sym, "error": "No valid overnight trade pairs constructed"}

    strategies: Dict[str, Any] = {}
    for key, _ in _CLOCK_EXIT_SPECS:
        strategies[key] = _summarize_net_returns(clock_net[key], clock_gross[key])
    for key, _ in target_specs:
        strategies[key] = _summarize_net_returns(limit_net[key], limit_gross[key], fill_flags=limit_fills[key])
    strategies["empirical_p50_front_run_or_cutoff"] = _summarize_net_returns(emp_net, emp_gross, fill_flags=emp_fills)

    best_key = max(strategies.keys(), key=lambda k: (strategies[k]["mean_net_pct"], strategies[k]["sharpe_ann"]))
    best_stats = strategies[best_key]
    best_series = (
        clock_net[best_key]
        if best_key in clock_net
        else (limit_net[best_key] if best_key in limit_net else emp_net)
    )

    first_entry = _safe_float(sessions[0][1].iloc[-1].get("Close"))
    last_close = _safe_float(sessions[-1][1].iloc[-1].get("Close"))
    bh_pct = round(((last_close - first_entry) / first_entry) * 100.0, 2) if (first_entry and last_close and first_entry > 0) else None

    vix_low_rets = [r for r, m in zip(best_series, pair_meta) if m["vix"] is not None and m["vix"] < 20.0]
    vix_high_rets = [r for r, m in zip(best_series, pair_meta) if m["vix"] is not None and m["vix"] >= 20.0]
    green_prev_rets = [r for r, m in zip(best_series, pair_meta) if m["d0_green"]]
    red_prev_rets = [r for r, m in zip(best_series, pair_meta) if not m["d0_green"]]

    if best_stats["mean_net_pct"] > 0.25 and best_stats["sharpe_ann"] >= 1.0 and best_stats["win_rate_pct"] >= 50.0:
        verdict = "SOUND_EDGE"
    elif best_stats["mean_net_pct"] > 0.0:
        verdict = "MARGINAL_AFTER_FRICTION"
    else:
        verdict = "UNSOUND_NEGATIVE_EXPECTANCY"

    gap_arr = np.asarray(overnight_gaps, dtype=float)
    return {
        "status": "ok",
        "study_type": "overnight_to_morning_peak",
        "ticker": sym,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window": {
            "sessions_analyzed": len(sessions),
            "overnight_trades": len(pair_meta),
            "start_date": pair_meta[0]["d0"],
            "end_date": pair_meta[-1]["d1"],
            "entry_time_et": entry_time_et,
            "exit_cutoff_et": exit_cutoff_et,
            "friction_pct": round(friction, 3),
            "stop_loss_pct": stop_loss_pct,
        },
        "verdict": verdict,
        "best_executable_strategy": best_key,
        "best_executable_metrics": best_stats,
        "bootstrap_ci": _bootstrap_mean_ci(best_series, n_paths=bootstrap_paths),
        "buy_and_hold_pct": bh_pct,
        "oracle_morning_hod_upper_bound_non_tradable": _summarize_net_returns(oracle_net, oracle_gross),
        "overnight_gap_stats": {
            "pct_gap_up": round(float(np.mean(gap_arr > 0.0) * 100.0), 1),
            "mean_gap_pct": round(float(np.mean(gap_arr)), 3),
            "median_gap_pct": round(float(np.median(gap_arr)), 3),
            "p5_gap_pct": round(float(np.percentile(gap_arr, 5)), 3),
            "worst_gap_pct": round(float(np.min(gap_arr)), 3),
        },
        "regime_breakdown": {
            "vix_lt_20": _summarize_net_returns(vix_low_rets),
            "vix_gte_20": _summarize_net_returns(vix_high_rets),
            "after_green_day": _summarize_net_returns(green_prev_rets),
            "after_red_day": _summarize_net_returns(red_prev_rets),
        },
        "strategies": strategies,
    }


def evaluate_gap_behavior(
    ticker: str,
    *,
    bars_5m: Any = None,
    lookback_days: int = 60,
    friction_pct: float = 0.85,
    gap_threshold_pct: float = 2.0,
) -> Dict[str, Any]:
    """Analyzes post-open continuation vs fade behavior on >=2% gap-ups and <=-2% gap-downs."""
    sym = validate_ticker(ticker)
    friction = max(0.0, min(5.0, float(friction_pct)))
    raw_5m = _normalize_ohlcv_df(bars_5m) if bars_5m is not None else _fetch_yfinance_bars(
        sym, period=f"{max(10, min(60, int(lookback_days or 60)))}d", interval="5m"
    )
    if raw_5m.empty:
        return {"status": "unavailable", "study_type": "gap_behavior", "ticker": sym, "error": "No 5m OHLCV data returned"}

    hhmm = raw_5m.index.hour * 100 + raw_5m.index.minute
    rth = raw_5m[(hhmm >= 930) & (hhmm < 1600)].sort_index()
    sessions = [(d, g) for d, g in rth.groupby(rth.index.date) if len(g) >= 10]

    gap_up_to_1030: List[float] = []
    gap_up_to_close: List[float] = []
    gap_down_to_1030: List[float] = []
    gap_down_to_close: List[float] = []
    prev_close: Optional[float] = None
    for _, day_df in sessions:
        d_open = _safe_float(day_df.iloc[0].get("Open"))
        d_close = _safe_float(day_df.iloc[-1].get("Close"))
        px_1030 = _bar_exit_price_at_hhmm(day_df, 1030)
        if prev_close and prev_close > 0 and d_open and d_close and px_1030:
            gap_pct = ((d_open - prev_close) / prev_close) * 100.0
            r_1030 = ((px_1030 - d_open) / d_open) * 100.0 - friction
            r_close = ((d_close - d_open) / d_open) * 100.0 - friction
            if gap_pct >= float(gap_threshold_pct):
                gap_up_to_1030.append(r_1030)
                gap_up_to_close.append(r_close)
            elif gap_pct <= -float(gap_threshold_pct):
                gap_down_to_1030.append(r_1030)
                gap_down_to_close.append(r_close)
        if d_close and d_close > 0:
            prev_close = d_close

    return {
        "status": "ok",
        "study_type": "gap_behavior",
        "ticker": sym,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sessions_analyzed": len(sessions),
        "gap_threshold_pct": float(gap_threshold_pct),
        "friction_pct": round(friction, 3),
        "gap_ups_long_open_to_1030": _summarize_net_returns(gap_up_to_1030),
        "gap_ups_long_open_to_close": _summarize_net_returns(gap_up_to_close),
        "gap_downs_long_open_to_1030": _summarize_net_returns(gap_down_to_1030),
        "gap_downs_long_open_to_close": _summarize_net_returns(gap_down_to_close),
    }


def evaluate_vwap_reversion(
    ticker: str,
    *,
    bars_5m: Any = None,
    lookback_days: int = 60,
    friction_pct: float = 0.85,
) -> Dict[str, Any]:
    """Tests intraday VWAP reclaim longs and +3% VWAP extension exits net of friction."""
    sym = validate_ticker(ticker)
    friction = max(0.0, min(5.0, float(friction_pct)))
    raw_5m = _normalize_ohlcv_df(bars_5m) if bars_5m is not None else _fetch_yfinance_bars(
        sym, period=f"{max(10, min(60, int(lookback_days or 60)))}d", interval="5m"
    )
    if raw_5m.empty:
        return {"status": "unavailable", "study_type": "vwap_reversion", "ticker": sym, "error": "No 5m OHLCV data returned"}

    hhmm = raw_5m.index.hour * 100 + raw_5m.index.minute
    rth = raw_5m[(hhmm >= 930) & (hhmm < 1600)].sort_index()
    sessions = [(d, g) for d, g in rth.groupby(rth.index.date) if len(g) >= 10]

    reclaim_rets: List[float] = []
    ext_fade_rets: List[float] = []
    for _, day_df in sessions:
        tp = (day_df["High"] + day_df["Low"] + day_df["Close"]) / 3.0
        vol = day_df["Volume"].clip(lower=0.0) if "Volume" in day_df.columns else pd.Series(1.0, index=day_df.index)
        cum_vol = vol.cumsum()
        vwap_s = (tp * vol).cumsum() / cum_vol.replace(0.0, np.nan)
        closes = day_df["Close"].to_numpy(dtype=float)
        vwaps = vwap_s.to_numpy(dtype=float)
        eod = float(closes[-1])
        reclaim_done = False
        ext_done = False
        for k in range(2, len(closes) - 2):
            if not math.isfinite(vwaps[k]) or vwaps[k] <= 0:
                continue
            if not reclaim_done and closes[k - 1] < vwaps[k - 1] and closes[k] >= vwaps[k]:
                reclaim_done = True
                reclaim_rets.append(((eod - closes[k]) / closes[k]) * 100.0 - friction)
            if not ext_done and ((closes[k] - vwaps[k]) / vwaps[k]) * 100.0 >= 3.0:
                ext_done = True
                ext_fade_rets.append(((closes[k] - eod) / closes[k]) * 100.0 - friction)

    return {
        "status": "ok",
        "study_type": "vwap_reversion",
        "ticker": sym,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sessions_analyzed": len(sessions),
        "friction_pct": round(friction, 3),
        "vwap_reclaim_long_to_close": _summarize_net_returns(reclaim_rets),
        "vwap_ext_plus3pct_short_fade_to_close": _summarize_net_returns(ext_fade_rets),
    }


def evaluate_regime_forward_returns(
    ticker: str,
    *,
    daily_bars: Any = None,
    vix_daily: Any = None,
    lookback_days: int = 365,
    friction_pct: float = 0.85,
) -> Dict[str, Any]:
    """Conditioned 1d/3d/5d/10d forward returns by RSI and VIX regime over 1-2y daily history."""
    sym = validate_ticker(ticker)
    friction = max(0.0, min(5.0, float(friction_pct)))
    lb = max(60, min(730, int(lookback_days or 365)))
    period = "2y" if lb > 365 else "1y"

    df = _normalize_ohlcv_df(daily_bars) if daily_bars is not None else _fetch_yfinance_bars(sym, period=period, interval="1d")
    if df.empty or len(df) < 30:
        return {"status": "unavailable", "study_type": "regime_forward_returns", "ticker": sym, "error": "Insufficient daily OHLCV history"}
    if vix_daily is None and daily_bars is None:
        try:
            vix_daily = _fetch_yfinance_bars("^VIX", period=period, interval="1d")
        except Exception:
            vix_daily = None
    vix_map = _build_vix_lookup(vix_daily)

    closes = df["Close"].astype(float)
    delta = closes.diff()
    gain = delta.clip(lower=0.0).rolling(14).mean()
    loss = (-delta.clip(upper=0.0)).rolling(14).mean()
    rs = gain / loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))

    buckets: Dict[str, Dict[int, List[float]]] = {
        "all": {1: [], 3: [], 5: [], 10: []},
        "rsi_lt_35": {1: [], 3: [], 5: [], 10: []},
        "rsi_35_70": {1: [], 3: [], 5: [], 10: []},
        "rsi_gte_70": {1: [], 3: [], 5: [], 10: []},
        "vix_lt_20": {1: [], 3: [], 5: [], 10: []},
        "vix_gte_20": {1: [], 3: [], 5: [], 10: []},
    }
    dates = [ts.date() for ts in df.index]
    c_vals = closes.to_numpy(dtype=float)
    rsi_vals = rsi.to_numpy(dtype=float)
    for i in range(15, len(c_vals) - 10):
        c0 = c_vals[i]
        r0 = rsi_vals[i]
        if c0 <= 0 or not math.isfinite(r0):
            continue
        vix_val = vix_map.get(dates[i])
        for h in (1, 3, 5, 10):
            ret = ((c_vals[i + h] - c0) / c0) * 100.0 - friction
            buckets["all"][h].append(ret)
            if r0 < 35.0:
                buckets["rsi_lt_35"][h].append(ret)
            elif r0 >= 70.0:
                buckets["rsi_gte_70"][h].append(ret)
            else:
                buckets["rsi_35_70"][h].append(ret)
            if vix_val is not None:
                buckets["vix_lt_20" if vix_val < 20.0 else "vix_gte_20"][h].append(ret)

    summary_out = {
        bname: {f"{h}d": _summarize_net_returns(vals) for h, vals in horizons.items()}
        for bname, horizons in buckets.items()
    }
    return {
        "status": "ok",
        "study_type": "regime_forward_returns",
        "ticker": sym,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "daily_bars_analyzed": len(df),
        "friction_pct": round(friction, 3),
        "horizons_by_regime": summary_out,
    }


def _persist_latest_investigation(payload: Dict[str, Any], cache_path: Optional[str]) -> None:
    if not cache_path:
        return
    try:
        parent = os.path.dirname(os.path.abspath(cache_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = f"{cache_path}.tmp.{os.getpid()}.{threading.get_ident()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, cache_path)
    except Exception:
        pass


def run_investigation(
    study_type: str,
    ticker: str,
    *,
    lookback_days: int = 60,
    friction_pct: float = 0.85,
    entry_time_et: str = "15:55",
    exit_cutoff_et: str = "10:30",
    stop_loss_pct: Optional[float] = None,
    compute_target: str = "local",
    bootstrap_paths: int = 2000,
    cache_path: Optional[str] = DEFAULT_INVESTIGATION_CACHE_PATH,
    bars_5m: Any = None,
    daily_bars: Any = None,
    vix_daily: Any = None,
    colab_executor: Optional[Any] = None,
) -> Dict[str, Any]:
    """Executes an allowlisted quantitative study locally (<2s) or on Google Colab (zero-secret)."""
    study = str(study_type or "").strip().lower()
    if study not in VALID_STUDY_TYPES:
        raise ValueError(f"Unknown study_type {study_type!r}; allowed: {list(VALID_STUDY_TYPES)}")
    sym = validate_ticker(ticker)
    target = str(compute_target or "local").strip().lower()
    if target not in VALID_COMPUTE_TARGETS:
        raise ValueError(f"Unknown compute_target {compute_target!r}; allowed: {list(VALID_COMPUTE_TARGETS)}")
    lb = max(5, min(730, int(lookback_days or 60)))
    friction = max(0.0, min(5.0, float(friction_pct if friction_pct is not None else 0.85)))
    stop_clean = _safe_float(stop_loss_pct)
    if stop_clean is not None and (stop_clean <= 0 or stop_clean > 50):
        raise ValueError("stop_loss_pct must be between 0 and 50")
    boots = max(0, min(10000, int(bootstrap_paths or 2000)))

    fallback_reason: Optional[str] = None
    if target == "colab":
        inputs = {
            "study_type": study,
            "ticker": sym,
            "lookback_days": lb,
            "friction_pct": friction,
            "entry_time_et": str(entry_time_et or "15:55"),
            "exit_cutoff_et": str(exit_cutoff_et or "10:30"),
            "stop_loss_pct": stop_clean,
            "bootstrap_paths": boots,
        }
        try:
            worker_path = os.path.join(_WORKERS_DIR, "custom_study_worker.py")
            with open(worker_path, "r", encoding="utf-8") as f:
                script = f.read()
            if colab_executor is not None:
                res = colab_executor(script, inputs)
            else:
                try:
                    from colab_mcp_client import ColabMCPClient, ColabMCPConfig
                except ImportError:
                    from python.colab_mcp_client import ColabMCPClient, ColabMCPConfig
                cfg = ColabMCPConfig.from_env()
                if cfg.mode == "offline":
                    raise RuntimeError("Colab transport is offline")
                client = ColabMCPClient(config=cfg)
                exec_res = client.execute_script_sync(script, inputs=inputs, accelerator="CPU", timeout_seconds=120)
                if not exec_res.ok or not isinstance(exec_res.parsed_json, dict):
                    raise RuntimeError(exec_res.error or "Colab investigation returned invalid JSON")
                res = exec_res.parsed_json
            if isinstance(res, dict) and res.get("status") == "ok":
                res["compute_target_used"] = "colab"
                _persist_latest_investigation(res, cache_path)
                return res
            fallback_reason = str((res or {}).get("error") or "Colab investigation returned non-ok status")
        except Exception as exc:
            fallback_reason = str(exc)[:200]

    if study == "overnight_to_morning_peak":
        result = evaluate_overnight_to_morning_peak(
            sym,
            bars_5m=bars_5m,
            vix_daily=vix_daily,
            lookback_days=min(60, lb),
            friction_pct=friction,
            entry_time_et=entry_time_et,
            exit_cutoff_et=exit_cutoff_et,
            stop_loss_pct=stop_clean,
            bootstrap_paths=boots,
        )
    elif study == "gap_behavior":
        result = evaluate_gap_behavior(sym, bars_5m=bars_5m, lookback_days=min(60, lb), friction_pct=friction)
    elif study == "vwap_reversion":
        result = evaluate_vwap_reversion(sym, bars_5m=bars_5m, lookback_days=min(60, lb), friction_pct=friction)
    else:
        result = evaluate_regime_forward_returns(
            sym, daily_bars=daily_bars, vix_daily=vix_daily, lookback_days=lb, friction_pct=friction
        )

    result["compute_target_used"] = "local"
    if fallback_reason:
        result["compute_target_requested"] = "colab"
        result["fallback_reason"] = fallback_reason
    if result.get("status") == "ok":
        _persist_latest_investigation(result, cache_path)
    return result
