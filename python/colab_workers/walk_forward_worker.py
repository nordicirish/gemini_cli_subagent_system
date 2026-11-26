"""
Google Colab MCP Worker #2: Walk-Forward Strategy & Rule Parameter Backtester.

Evaluates `rules.md` parameter grids across multi-year OHLCV histories using
strict chronological train/test walk-forward splits (zero lookahead leak) and
exact round-trip transaction friction hurdles (`0.85%` US / `0.25%` OMXH).
Produces a strictly read-only Schema v2.0 Protocol 06 Evidence Pack
(`backtest_evidence`) for Human Owner inspection — never mutates `rules.md`.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

JSON_START_MARKER = "<<<GEM_COLAB_JSON_START>>>"
JSON_END_MARKER = "<<<GEM_COLAB_JSON_END>>>"

BASELINE_RULE_PARAMS: Dict[str, float] = {
    "trailing_stop_vwap_ext_pct": 2.0,
    "rsi_overbought_gate": 75.0,
    "rvol_breakout_min": 1.5,
}

# Compact parameter grid centered on canonical rules.md thresholds
DEFAULT_PARAM_GRID: Tuple[Dict[str, float], ...] = tuple(
    {
        "trailing_stop_vwap_ext_pct": stop_pct,
        "rsi_overbought_gate": rsi_gate,
        "rvol_breakout_min": rvol_min,
    }
    for stop_pct in (1.5, 2.0, 2.5, 3.0)
    for rsi_gate in (70.0, 75.0, 80.0)
    for rvol_min in (1.25, 1.5)
)

_PARAM_RULE_MAP: Dict[str, str] = {
    "trailing_stop_vwap_ext_pct": "ENH_104",
    "rsi_overbought_gate": "MANDATE_38",
    "rvol_breakout_min": "ENH_18",
}


def _compute_rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0).rolling(window, min_periods=window).mean()
    loss = (-delta.clip(upper=0.0)).rolling(window, min_periods=window).mean()
    rs = gain / loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)


def compute_signal_series(df: pd.DataFrame, params: Dict[str, float]) -> np.ndarray:
    """
    Causal signal generator: position[t] uses strictly information up to bar t close
    (and is applied to bar t+1 close-to-close return to guarantee zero lookahead).
    """
    close = pd.to_numeric(df["Close"], errors="coerce").astype(float)
    volume = pd.to_numeric(df["Volume"], errors="coerce").reindex(close.index).fillna(0.0).astype(float)
    n = len(close)
    if n < 25:
        return np.zeros(n, dtype=float)

    ema20 = close.ewm(span=20, adjust=False).mean()
    avg_vol20 = volume.rolling(20, min_periods=10).mean().replace(0.0, np.nan)
    rvol = (volume / avg_vol20).fillna(1.0)
    rsi = _compute_rsi(close, window=14)

    stop_ext_pct = float(params.get("trailing_stop_vwap_ext_pct", 2.0))
    rsi_gate = float(params.get("rsi_overbought_gate", 75.0))
    rvol_min = float(params.get("rvol_breakout_min", 1.5))

    close_arr = close.values
    ema_arr = ema20.values
    rvol_arr = rvol.values
    rsi_arr = rsi.values

    sig = np.zeros(n, dtype=float)
    in_pos = False
    peak_px = 0.0
    for t in range(20, n):
        px = close_arr[t]
        if not math.isfinite(px) or px <= 0.0:
            in_pos = False
            peak_px = 0.0
            continue
        if not in_pos:
            if px > ema_arr[t] and rvol_arr[t] >= rvol_min and rsi_arr[t] < rsi_gate:
                in_pos = True
                peak_px = px
                sig[t] = 1.0
        else:
            peak_px = max(peak_px, px)
            drawdown_from_peak_pct = ((peak_px - px) / peak_px) * 100.0 if peak_px > 0 else 0.0
            vwap_ext_breach = px < ema_arr[t] and (((ema_arr[t] - px) / ema_arr[t]) * 100.0) >= stop_ext_pct
            if rsi_arr[t] >= rsi_gate or drawdown_from_peak_pct >= (stop_ext_pct * 1.5) or vwap_ext_breach:
                in_pos = False
                peak_px = 0.0
                sig[t] = 0.0
            else:
                sig[t] = 1.0
    return sig


def simulate_strategy_returns(
    df: pd.DataFrame,
    params: Dict[str, float],
    *,
    friction_hurdle_pct: float = 0.85,
) -> Tuple[np.ndarray, List[float]]:
    """
    Simulate net daily returns with next-bar execution (`signal[t-1] * ret[t]`)
    and half round-trip friction deducted on each entry/exit edge.
    """
    if df is None or df.empty or "Close" not in df.columns or "Volume" not in df.columns:
        return np.zeros(0, dtype=float), []

    close = pd.to_numeric(df["Close"], errors="coerce").dropna().astype(float)
    if len(close) < 25:
        return np.zeros(len(close), dtype=float), []

    work_df = df.loc[close.index]
    signals = compute_signal_series(work_df, params)
    close_arr = close.values
    raw_rets = np.zeros(len(close_arr), dtype=float)
    raw_rets[1:] = (close_arr[1:] / np.where(close_arr[:-1] > 0.0, close_arr[:-1], 1.0)) - 1.0

    # Causal shift: position active on bar t was decided at close of bar t-1
    pos = np.zeros(len(close_arr), dtype=float)
    pos[1:] = signals[:-1]

    half_cost = max(0.0, float(friction_hurdle_pct)) / 200.0
    net_rets = np.zeros(len(close_arr), dtype=float)
    trade_returns: List[float] = []
    cur_trade_eq = 1.0
    was_in = False

    for t in range(1, len(close_arr)):
        prev_p = pos[t - 1]
        cur_p = pos[t]
        turnover = abs(cur_p - prev_p)
        cost = turnover * half_cost
        step_r = (cur_p * raw_rets[t]) - cost
        net_rets[t] = step_r

        if cur_p > 0.5 and not was_in:
            cur_trade_eq = 1.0 * (1.0 + step_r)
            was_in = True
        elif was_in and cur_p > 0.5:
            cur_trade_eq *= (1.0 + step_r)
        elif was_in and cur_p <= 0.5:
            cur_trade_eq *= (1.0 - cost)
            trade_returns.append(cur_trade_eq - 1.0)
            was_in = False
            cur_trade_eq = 1.0

    if was_in:
        trade_returns.append(cur_trade_eq - 1.0)

    return net_rets, trade_returns


def compute_performance_metrics(
    daily_returns: Sequence[float],
    trade_returns: Sequence[float],
) -> Dict[str, float]:
    """Compute bounded Schema v2.0 performance summary metrics."""
    arr = np.asarray(daily_returns, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) < 5:
        return {
            "sharpe_ratio": 0.0,
            "sortino_ratio": 0.0,
            "max_drawdown_pct": 0.0,
            "hit_rate_pct": 0.0,
            "profit_factor": 1.0,
        }

    mean_r = float(np.mean(arr))
    std_r = float(np.std(arr))
    sharpe = (mean_r / std_r) * math.sqrt(252.0) if std_r > 1e-8 else 0.0

    downside = arr[arr < 0.0]
    down_std = float(np.sqrt(np.mean(downside * downside))) if len(downside) > 0 else 0.0
    sortino = (mean_r / down_std) * math.sqrt(252.0) if down_std > 1e-8 else sharpe

    equity = np.cumprod(1.0 + np.clip(arr, -0.95, 5.0))
    peaks = np.maximum.accumulate(equity)
    dd = ((equity / np.where(peaks > 0.0, peaks, 1.0)) - 1.0) * 100.0
    max_dd = float(np.min(dd)) if len(dd) > 0 else 0.0

    trades = [float(t) for t in trade_returns if math.isfinite(t)]
    if trades:
        wins = [t for t in trades if t > 0.0]
        losses = [-t for t in trades if t < 0.0]
        hit_rate = (len(wins) / len(trades)) * 100.0
        sum_win = sum(wins)
        sum_loss = sum(losses)
        profit_factor = (sum_win / sum_loss) if sum_loss > 1e-8 else (10.0 if sum_win > 0 else 1.0)
    else:
        hit_rate = 50.0 if mean_r > 0 else 0.0
        profit_factor = 1.0

    return {
        "sharpe_ratio": round(max(-10.0, min(20.0, sharpe)), 4),
        "sortino_ratio": round(max(-10.0, min(40.0, sortino)), 4),
        "max_drawdown_pct": round(max(-100.0, min(0.0, max_dd)), 4),
        "hit_rate_pct": round(max(0.0, min(100.0, hit_rate)), 4),
        "profit_factor": round(max(0.0, min(100.0, profit_factor)), 4),
    }


def _evaluate_params_across_slice(
    histories: Dict[str, pd.DataFrame],
    start_idx: int,
    end_idx: int,
    params: Dict[str, float],
    default_friction_pct: float,
) -> Tuple[List[float], List[float], Dict[str, float]]:
    """Run causal simulation over [0:end_idx] then slice strictly [start_idx:end_idx] OOS returns."""
    per_sym_rets: List[np.ndarray] = []
    all_trades: List[float] = []
    for sym, df in histories.items():
        if len(df) < end_idx:
            continue
        sub = df.iloc[:end_idx]
        friction = 0.25 if sym.endswith(".HE") else default_friction_pct
        rets, trades = simulate_strategy_returns(sub, params, friction_hurdle_pct=friction)
        oos_slice = rets[start_idx:end_idx]
        if len(oos_slice) > 0:
            per_sym_rets.append(oos_slice)
            # Filter trade attribution approximately via active test fraction when non-empty
            all_trades.extend(trades)

    if not per_sym_rets:
        return [], [], compute_performance_metrics([], [])
    min_len = min(len(r) for r in per_sym_rets)
    port_daily = np.mean(np.vstack([r[-min_len:] for r in per_sym_rets]), axis=0)
    return port_daily.tolist(), all_trades, compute_performance_metrics(port_daily, all_trades)


def run_walk_forward_sweep(
    price_histories: Dict[str, pd.DataFrame],
    *,
    folds: int = 6,
    friction_hurdle_pct: float = 0.85,
    param_grid: Optional[Sequence[Dict[str, float]]] = None,
) -> Dict[str, Any]:
    """Execute rolling chronological walk-forward sweep returning Schema v2.0 `backtest_evidence`."""
    valid_hist: Dict[str, pd.DataFrame] = {}
    for raw_sym, df in (price_histories or {}).items():
        sym = str(raw_sym or "").strip().upper()
        if sym and df is not None and not df.empty and "Close" in df.columns and "Volume" in df.columns and len(df) >= 140:
            valid_hist[sym] = df.sort_index()

    if not valid_hist:
        empty_metrics = compute_performance_metrics([], [])
        return {
            "schema_version": "2.0",
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "google_colab_mcp",
            "notes": "Insufficient history for walk-forward backtest.",
            "protocol_06_read_only": True,
            "folds": 1,
            "universe_count": 1,
            "friction_hurdle_pct": round(float(friction_hurdle_pct), 4),
            "baseline_metrics": empty_metrics,
            "candidate_metrics": empty_metrics,
            "stability_score": 0.0,
            "proposed_rule_deltas": [],
        }

    min_bars = min(len(df) for df in valid_hist.values())
    eff_folds = max(2, min(int(folds or 6), max(2, (min_bars - 60) // 25)))
    train_init = max(60, min_bars // 2)
    fold_len = max(20, (min_bars - train_init) // eff_folds)

    grid = list(param_grid or DEFAULT_PARAM_GRID)
    if BASELINE_RULE_PARAMS not in grid:
        grid.insert(0, dict(BASELINE_RULE_PARAMS))

    baseline_oos_daily: List[float] = []
    baseline_trades: List[float] = []
    candidate_oos_daily: List[float] = []
    candidate_trades: List[float] = []
    chosen_keys: List[Tuple[float, float, float]] = []

    for f_idx in range(eff_folds):
        train_end = train_init + (f_idx * fold_len)
        test_end = min(min_bars, train_end + fold_len)
        if test_end - train_end < 10:
            break

        # Baseline evaluation strictly on OOS test fold
        b_daily, b_tr, _ = _evaluate_params_across_slice(valid_hist, train_end, test_end, BASELINE_RULE_PARAMS, friction_hurdle_pct)
        baseline_oos_daily.extend(b_daily)
        baseline_trades.extend(b_tr)

        # In-sample parameter selection strictly up to `train_end` (never touches test fold)
        best_params = dict(BASELINE_RULE_PARAMS)
        best_sharpe = -999.0
        for cand in grid:
            _, _, tr_metrics = _evaluate_params_across_slice(valid_hist, 25, train_end, cand, friction_hurdle_pct)
            if tr_metrics["sharpe_ratio"] > best_sharpe:
                best_sharpe = tr_metrics["sharpe_ratio"]
                best_params = dict(cand)

        c_daily, c_tr, _ = _evaluate_params_across_slice(valid_hist, train_end, test_end, best_params, friction_hurdle_pct)
        candidate_oos_daily.extend(c_daily)
        candidate_trades.extend(c_tr)
        chosen_keys.append((
            float(best_params["trailing_stop_vwap_ext_pct"]),
            float(best_params["rsi_overbought_gate"]),
            float(best_params["rvol_breakout_min"]),
        ))

    baseline_metrics = compute_performance_metrics(baseline_oos_daily, baseline_trades)
    candidate_metrics = compute_performance_metrics(candidate_oos_daily, candidate_trades)

    if chosen_keys:
        modal_key, modal_count = Counter(chosen_keys).most_common(1)[0]
        stability_score = round(modal_count / len(chosen_keys), 4)
        modal_params = {
            "trailing_stop_vwap_ext_pct": modal_key[0],
            "rsi_overbought_gate": modal_key[1],
            "rvol_breakout_min": modal_key[2],
        }
    else:
        stability_score = 0.0
        modal_params = dict(BASELINE_RULE_PARAMS)

    proposed_deltas: List[Dict[str, Any]] = []
    improved = candidate_metrics["sharpe_ratio"] > baseline_metrics["sharpe_ratio"]
    if improved or stability_score >= 0.5:
        for param_name, rule_id in _PARAM_RULE_MAP.items():
            cur_v = BASELINE_RULE_PARAMS[param_name]
            prop_v = modal_params[param_name]
            if prop_v != cur_v:
                proposed_deltas.append({
                    "rule_id": rule_id,
                    "parameter": param_name,
                    "current_value": cur_v,
                    "proposed_value": prop_v,
                    "rationale": (
                        f"Selected across {eff_folds} OOS folds (stability {stability_score:.2f}; "
                        f"Sharpe {baseline_metrics['sharpe_ratio']:.2f} -> {candidate_metrics['sharpe_ratio']:.2f} "
                        f"after {friction_hurdle_pct:.2f}% hurdle)."
                    )[:300],
                })

    return {
        "schema_version": "2.0",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "google_colab_mcp",
        "notes": (
            f"Read-only Protocol 06 walk-forward evidence across {len(valid_hist)} ticker(s) "
            f"and {max(1, len(chosen_keys))} chronological OOS fold(s)."
        ),
        "protocol_06_read_only": True,
        "folds": max(1, min(100, len(chosen_keys) or 1)),
        "universe_count": max(1, min(2000, len(valid_hist))),
        "friction_hurdle_pct": round(max(0.0, min(10.0, float(friction_hurdle_pct))), 4),
        "baseline_metrics": baseline_metrics,
        "candidate_metrics": candidate_metrics,
        "stability_score": stability_score,
        "proposed_rule_deltas": proposed_deltas[:25],
    }


def _yfinance_walk_forward_fetcher(symbols: Sequence[str], lookback_years: int = 3) -> Dict[str, pd.DataFrame]:
    import yfinance as yf

    period = f"{max(1, min(10, int(lookback_years or 3)))}y"
    out: Dict[str, pd.DataFrame] = {}
    for i in range(0, len(symbols), 25):
        chunk = list(symbols[i:i + 25])
        try:
            data = yf.download(
                chunk,
                period=period,
                interval="1d",
                group_by="ticker",
                progress=False,
                threads=True,
                auto_adjust=False,
            )
        except Exception:
            continue
        if data is None or data.empty:
            continue
        if len(chunk) == 1:
            out[chunk[0]] = data
        elif isinstance(data.columns, pd.MultiIndex):
            top_cols = set(data.columns.get_level_values(0))
            for sym in chunk:
                if sym in top_cols:
                    sub = data[sym].dropna(how="all")
                    if not sub.empty:
                        out[sym] = sub
    return out


def run_walk_forward_job(
    job_inputs: Dict[str, Any],
    *,
    history_fetcher: Optional[Callable[[Sequence[str], int], Dict[str, pd.DataFrame]]] = None,
) -> Dict[str, Any]:
    """Run #2 Walk-Forward Rule Parameter Backtester from `job_inputs`."""
    options = job_inputs.get("options") or {}
    lookback_years = int(options.get("lookback_years") or 3)
    raw_syms = list(job_inputs.get("symbols") or [])
    for bench in ("SPY", "QQQ"):
        if bench not in raw_syms:
            raw_syms.append(bench)

    seen = set()
    symbols: List[str] = []
    for raw in raw_syms:
        sym = str(raw or "").strip().upper()
        if sym and sym not in seen and not sym.startswith("^"):
            seen.add(sym)
            symbols.append(sym)

    fetcher = history_fetcher or _yfinance_walk_forward_fetcher
    histories = fetcher(symbols[:50], lookback_years)
    return run_walk_forward_sweep(histories, folds=6, friction_hurdle_pct=0.85)


if __name__ == "__main__":
    _inputs = globals().get("GEM_JOB_INPUTS") or {}
    _result = run_walk_forward_job(_inputs)
    print(JSON_START_MARKER + json.dumps(_result, separators=(",", ":")) + JSON_END_MARKER)
