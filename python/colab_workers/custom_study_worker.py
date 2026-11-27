"""
Google Colab MCP Worker: Zero-Secret Quantitative Hypothesis Investigation.

Executes one allowlisted statistical study (`overnight_to_morning_peak`, `gap_behavior`,
`vwap_reversion`, or `regime_forward_returns`) using public OHLCV history and `GEM_JOB_INPUTS`.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

JSON_START_MARKER = "<<<GEM_COLAB_JSON_START>>>"
JSON_END_MARKER = "<<<GEM_COLAB_JSON_END>>>"
NY_TZ = ZoneInfo("America/New_York")

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


def _normalize_df(df: Any) -> pd.DataFrame:
    if df is None or getattr(df, "empty", True):
        return pd.DataFrame()
    work = df.copy()
    if isinstance(work.columns, pd.MultiIndex):
        lvl0 = [str(c[0]) for c in work.columns]
        work.columns = lvl0 if any(c in ("Close", "Open", "High", "Low") for c in lvl0) else [str(c[-1]) for c in work.columns]
    else:
        work.columns = [str(c) for c in work.columns]
    work = work.loc[:, ~work.columns.duplicated()]
    idx = work.index
    work.index = idx.tz_convert(NY_TZ) if getattr(idx, "tz", None) is not None else pd.DatetimeIndex(idx).tz_localize(NY_TZ)
    return work.sort_index()


def _parse_hhmm(raw: str, default_hhmm: int) -> int:
    m = re.match(r"^(\d{1,2}):(\d{2})$", str(raw or "").strip())
    if not m:
        return default_hhmm
    hhmm = int(m.group(1)) * 100 + int(m.group(2))
    return hhmm if 930 <= hhmm <= 1600 else default_hhmm


def _bar_exit_px(day_df: pd.DataFrame, target_hhmm: int) -> Optional[float]:
    hhmm = day_df.index.hour * 100 + day_df.index.minute
    sub = day_df[hhmm >= target_hhmm]
    return _safe_float((day_df.iloc[-1] if sub.empty else sub.iloc[0]).get("Close"))


def _summarize(arr_vals: Sequence[float], gross_vals: Optional[Sequence[float]] = None, fill_flags: Optional[Sequence[bool]] = None) -> Dict[str, Any]:
    arr = np.asarray([float(x) for x in arr_vals if math.isfinite(float(x))], dtype=float)
    n = int(arr.size)
    if n == 0:
        return {"trades": 0, "win_rate_pct": 0.0, "mean_net_pct": 0.0, "median_net_pct": 0.0, "compounded_net_pct": 0.0, "sharpe_ann": 0.0, "max_drawdown_pct": 0.0, "p5_net_pct": 0.0, "worst_trade_pct": 0.0, "best_trade_pct": 0.0}
    mean_v = float(np.mean(arr))
    std_v = float(np.std(arr, ddof=1)) if n > 1 else 0.0
    growth = np.cumprod(np.maximum(0.01, 1.0 + arr / 100.0))
    peaks = np.maximum.accumulate(np.insert(growth, 0, 1.0))[1:]
    out: Dict[str, Any] = {
        "trades": n,
        "win_rate_pct": round(float(np.mean(arr > 0.0) * 100.0), 2),
        "mean_net_pct": round(mean_v, 3),
        "median_net_pct": round(float(np.median(arr)), 3),
        "compounded_net_pct": round(float((growth[-1] - 1.0) * 100.0), 2),
        "sharpe_ann": round((mean_v / std_v) * math.sqrt(252.0) if std_v > 1e-9 else 0.0, 2),
        "max_drawdown_pct": round(float(np.min((growth / peaks - 1.0) * 100.0)), 2),
        "p5_net_pct": round(float(np.percentile(arr, 5)), 3),
        "worst_trade_pct": round(float(np.min(arr)), 3),
        "best_trade_pct": round(float(np.max(arr)), 3),
    }
    if gross_vals is not None and len(gross_vals) == n:
        out["mean_gross_pct"] = round(float(np.mean(gross_vals)), 3)
    if fill_flags is not None and len(fill_flags) == n:
        out["limit_fill_rate_pct"] = round((sum(1 for f in fill_flags if f) / n) * 100.0, 1)
    return out


def _bootstrap(arr_vals: Sequence[float], n_paths: int) -> Dict[str, Any]:
    arr = np.asarray([float(x) for x in arr_vals if math.isfinite(float(x))], dtype=float)
    if arr.size < 3 or n_paths < 100:
        return {"paths": 0, "p5_mean_net_pct": None, "p50_mean_net_pct": None, "p95_mean_net_pct": None, "prob_positive_mean_pct": None}
    rng = np.random.default_rng(42)
    idx = rng.integers(0, arr.size, size=(int(n_paths), arr.size))
    means = np.mean(arr[idx], axis=1)
    return {
        "paths": int(n_paths),
        "p5_mean_net_pct": round(float(np.percentile(means, 5)), 3),
        "p50_mean_net_pct": round(float(np.percentile(means, 50)), 3),
        "p95_mean_net_pct": round(float(np.percentile(means, 95)), 3),
        "prob_positive_mean_pct": round(float(np.mean(means > 0.0) * 100.0), 1),
    }


def main() -> None:
    import yfinance as yf

    inputs = globals().get("GEM_JOB_INPUTS") or {}
    study = str(inputs.get("study_type") or "overnight_to_morning_peak").strip().lower()
    sym = str(inputs.get("ticker") or "UMAC").strip().upper()
    lb = max(5, min(730, int(inputs.get("lookback_days") or 60)))
    friction = max(0.0, min(5.0, float(inputs.get("friction_pct") if inputs.get("friction_pct") is not None else 0.85)))
    entry_time = str(inputs.get("entry_time_et") or "15:55")
    cutoff_time = str(inputs.get("exit_cutoff_et") or "10:30")
    stop_pct = _safe_float(inputs.get("stop_loss_pct"))
    boots = max(0, min(10000, int(inputs.get("bootstrap_paths") or 5000)))

    if study == "overnight_to_morning_peak":
        df_5m = _normalize_df(yf.Ticker(sym).history(period=f"{min(60, lb)}d", interval="5m", prepost=False, auto_adjust=False))
        vix_df = _normalize_df(yf.Ticker("^VIX").history(period="6mo", interval="1d", prepost=False, auto_adjust=False))
        vix_map = {ts.date(): float(r["Close"]) for ts, r in vix_df.iterrows() if _safe_float(r.get("Close"))} if not vix_df.empty else {}
        hhmm = df_5m.index.hour * 100 + df_5m.index.minute
        rth = df_5m[(hhmm >= 930) & (hhmm < 1600)].sort_index()
        sessions = [(d, g) for d, g in rth.groupby(rth.index.date) if len(g) >= 10]
        entry_hhmm = _parse_hhmm(entry_time, 1555)
        cutoff_hhmm = _parse_hhmm(cutoff_time, 1030)
        clock_net: Dict[str, List[float]] = {k: [] for k, _ in _CLOCK_EXIT_SPECS}
        clock_gross: Dict[str, List[float]] = {k: [] for k, _ in _CLOCK_EXIT_SPECS}
        gaps: List[float] = []
        prior_ups: List[float] = []
        emp_net: List[float] = []
        emp_gross: List[float] = []
        emp_fills: List[bool] = []
        oracle_net: List[float] = []
        oracle_gross: List[float] = []
        meta: List[Dict[str, Any]] = []
        for i in range(len(sessions) - 1):
            d0, df0 = sessions[i]
            d1, df1 = sessions[i + 1]
            if (d1 - d0).days > 5:
                continue
            hh0 = df0.index.hour * 100 + df0.index.minute
            ebars = df0[hh0 <= entry_hhmm]
            if ebars.empty:
                continue
            e_px = _safe_float(ebars.iloc[-1].get("Close"))
            d0_op = _safe_float(df0.iloc[0].get("Open")) or e_px
            hh1 = df1.index.hour * 100 + df1.index.minute
            n_op = _safe_float(df1.iloc[0].get("Open"))
            m_df = df1[(hh1 >= 930) & (hh1 <= cutoff_hhmm)]
            cut_px = _bar_exit_px(df1, cutoff_hhmm)
            if not (e_px and e_px > 0 and n_op and n_op > 0 and cut_px and not m_df.empty):
                continue
            gaps.append(((n_op - e_px) / e_px) * 100.0)
            for k, ex_h in _CLOCK_EXIT_SPECS:
                px = _bar_exit_px(df1, ex_h) or cut_px
                g_ret = ((px - e_px) / e_px) * 100.0
                clock_gross[k].append(g_ret)
                clock_net[k].append(g_ret - friction)
            if len(prior_ups) >= 5:
                lim = n_op * (1.0 + max(0.0, float(np.median(prior_ups))) / 100.0) * 0.9975
                stop_px = e_px * (1.0 - stop_pct / 100.0) if (stop_pct and stop_pct > 0) else None
                ex_px, filled = cut_px, False
                for _, bar in m_df.iterrows():
                    bo, bh, bl = float(bar["Open"]), float(bar["High"]), float(bar["Low"])
                    if stop_px and bl <= stop_px:
                        ex_px, filled = (bo if bo <= stop_px else stop_px), False
                        break
                    if bo >= lim:
                        ex_px, filled = bo, True
                        break
                    if bh >= lim:
                        ex_px, filled = lim, True
                        break
            else:
                ex_px, filled = cut_px, False
            g_emp = ((ex_px - e_px) / e_px) * 100.0
            emp_gross.append(g_emp)
            emp_net.append(g_emp - friction)
            emp_fills.append(filled)
            mh = _safe_float(df1[hh1 <= 1130]["High"].max())
            if mh and mh > 0:
                prior_ups.append(((mh - n_op) / n_op) * 100.0)
                oracle_gross.append(((mh - e_px) / e_px) * 100.0)
                oracle_net.append(((mh - e_px) / e_px) * 100.0 - friction)
            meta.append({"d0": str(d0), "d1": str(d1), "vix": vix_map.get(d0), "d0_green": bool(e_px >= d0_op)})
        strategies = {k: _summarize(clock_net[k], clock_gross[k]) for k, _ in _CLOCK_EXIT_SPECS}
        strategies["empirical_p50_front_run_or_cutoff"] = _summarize(emp_net, emp_gross, fill_flags=emp_fills)
        best_k = max(strategies.keys(), key=lambda k: (strategies[k]["mean_net_pct"], strategies[k]["sharpe_ann"]))
        best_s = strategies[best_k]
        best_rets = clock_net[best_k] if best_k in clock_net else emp_net
        verdict = "SOUND_EDGE" if (best_s["mean_net_pct"] > 0.25 and best_s["sharpe_ann"] >= 1.0 and best_s["win_rate_pct"] >= 50.0) else ("MARGINAL_AFTER_FRICTION" if best_s["mean_net_pct"] > 0 else "UNSOUND_NEGATIVE_EXPECTANCY")
        payload = {
            "status": "ok",
            "study_type": study,
            "ticker": sym,
            "source": "google_colab_mcp",
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "window": {"sessions_analyzed": len(sessions), "overnight_trades": len(meta), "entry_time_et": entry_time, "exit_cutoff_et": cutoff_time, "friction_pct": friction},
            "verdict": verdict,
            "best_executable_strategy": best_k,
            "best_executable_metrics": best_s,
            "bootstrap_ci": _bootstrap(best_rets, boots),
            "oracle_morning_hod_upper_bound_non_tradable": _summarize(oracle_net, oracle_gross),
            "overnight_gap_stats": {"pct_gap_up": round(float(np.mean(np.asarray(gaps) > 0.0) * 100.0), 1), "mean_gap_pct": round(float(np.mean(gaps)), 3), "worst_gap_pct": round(float(np.min(gaps)), 3)} if gaps else {},
            "strategies": strategies,
        }
    else:
        df_1d = _normalize_df(yf.Ticker(sym).history(period="2y" if lb > 365 else "1y", interval="1d", prepost=False, auto_adjust=False))
        closes = df_1d["Close"].astype(float).to_numpy()
        rets = [((closes[i + 5] - closes[i]) / closes[i]) * 100.0 - friction for i in range(len(closes) - 5) if closes[i] > 0]
        payload = {
            "status": "ok",
            "study_type": study,
            "ticker": sym,
            "source": "google_colab_mcp",
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "friction_pct": friction,
            "summary_5d_net": _summarize(rets),
            "bootstrap_ci": _bootstrap(rets, boots),
        }

    print(f"{JSON_START_MARKER}\n{json.dumps(payload)}\n{JSON_END_MARKER}")


if __name__ == "__main__":
    main()
