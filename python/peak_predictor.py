"""Empirical 60-Day Intraday Peak Window & Exhaustion Predictor.

Analyzes 60 trading days of 5-minute Regular Trading Hours (09:30-16:00 ET) bars
to quantify when a high-beta ticker statistically prints its daily high, its empirical
upside excursion percentiles from open (p50/p75/p90) and post-peak fade, and combines
those with live VWAP, RSI, rVol deceleration, Fib/ATR confluence, and dealer posture
into a deterministic 0-100 Exhaustion Score and front-run sell limit zone.
"""

from __future__ import annotations

from datetime import datetime
import json
import math
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")
DEFAULT_PEAK_CACHE_PATH = os.path.join("cache", "peak_profiles_cache.json")
PEAK_PROFILE_TTL_SEC = 21600  # 6 hours

_CACHE_LOCK = threading.RLock()


def _safe_float(val: Any) -> Optional[float]:
    if val is None or isinstance(val, bool):
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _percentile(sorted_vals: List[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    pos = (max(0.0, min(100.0, float(pct))) / 100.0) * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(sorted_vals[lo])
    frac = pos - lo
    return float(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac)


def _minutes_to_et_str(minutes_from_open: float) -> str:
    total = int(round(9 * 60 + 30 + max(0.0, min(390.0, float(minutes_from_open)))))
    hh, mm = divmod(total, 60)
    return f"{hh:02d}:{mm:02d}"


def _read_cache(cache_path: Optional[str]) -> Dict[str, Any]:
    if not cache_path or not os.path.exists(cache_path):
        return {"tickers": {}}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("tickers"), dict):
            return data
    except Exception:
        pass
    return {"tickers": {}}


def _write_cache(cache_path: Optional[str], payload: Dict[str, Any]) -> None:
    if not cache_path:
        return
    parent = os.path.dirname(os.path.abspath(cache_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = f"{cache_path}.tmp.{os.getpid()}.{threading.get_ident()}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp_path, cache_path)


def compute_historical_peak_profile(
    ticker: str,
    bars_df: Any = None,
    cache_path: Optional[str] = DEFAULT_PEAK_CACHE_PATH,
    now_ts: Optional[float] = None,
) -> Dict[str, Any]:
    """Computes 60d 5m RTH empirical peak timing, upside percentiles, and fade stats."""
    sym = str(ticker or "").strip().upper()
    if not re.match(r"^[A-Z0-9.\-\^]{1,12}$", sym):
        return {"status": "unavailable", "ticker": sym, "sessions_analyzed": 0, "error": "Invalid ticker"}

    ts = time.time() if now_ts is None else float(now_ts)
    if bars_df is None and cache_path:
        with _CACHE_LOCK:
            cdata = _read_cache(cache_path)
            hit = cdata["tickers"].get(sym)
            if isinstance(hit, dict) and (ts - float(hit.get("cached_at", 0.0))) < PEAK_PROFILE_TTL_SEC:
                out = dict(hit["profile"])
                out["cached"] = True
                return out

    used_yfinance = False
    df = bars_df
    if df is None:
        try:
            import yfinance as yf
            df = yf.Ticker(sym).history(period="60d", interval="5m", prepost=False, auto_adjust=False)
            used_yfinance = True
        except Exception as exc:
            return {"status": "unavailable", "ticker": sym, "sessions_analyzed": 0, "error": str(exc)}

    if df is None or getattr(df, "empty", True):
        return {"status": "unavailable", "ticker": sym, "sessions_analyzed": 0, "error": "No 5m history returned"}

    try:
        idx = df.index
        ny_idx = idx.tz_convert(NY_TZ) if getattr(idx, "tz", None) is not None else idx.tz_localize(NY_TZ)
        work = df.copy()
        work.index = ny_idx
    except Exception as exc:
        return {"status": "unavailable", "ticker": sym, "sessions_analyzed": 0, "error": f"Index error: {exc}"}

    # Filter strictly to Regular Trading Hours [09:30, 16:00)
    hhmm = work.index.hour * 100 + work.index.minute
    rth = work[(hhmm >= 930) & (hhmm < 1600)].sort_index()
    if rth.empty:
        return {"status": "unavailable", "ticker": sym, "sessions_analyzed": 0, "error": "No RTH bars found"}

    peak_minutes: List[float] = []
    upsides_pct: List[float] = []
    fades_pct: List[float] = []
    morning_fades_pct: List[float] = []
    gap_up_upsides: List[float] = []
    gap_up_minutes: List[float] = []
    prev_close: Optional[float] = None

    for _day, day_df in rth.groupby(rth.index.date):
        if len(day_df) < 10:
            continue
        open_px = _safe_float(day_df.iloc[0].get("Open")) or _safe_float(day_df.iloc[0].get("Close"))
        close_px = _safe_float(day_df.iloc[-1].get("Close"))
        high_series = day_df.get("High")
        if high_series is None or open_px is None or open_px <= 0 or close_px is None or close_px <= 0:
            prev_close = close_px if (close_px and close_px > 0) else prev_close
            continue

        high_val = _safe_float(high_series.max())
        if high_val is None or high_val <= 0:
            prev_close = close_px
            continue

        peak_ts = high_series.idxmax()
        mins_from_open = float((peak_ts.hour * 60 + peak_ts.minute) - (9 * 60 + 30))
        mins_from_open = max(0.0, min(390.0, mins_from_open))

        up_pct = ((high_val - open_px) / open_px) * 100.0
        fade_pct = ((close_px - high_val) / high_val) * 100.0

        peak_minutes.append(mins_from_open)
        upsides_pct.append(up_pct)
        fades_pct.append(fade_pct)
        if mins_from_open <= 60.0:
            morning_fades_pct.append(fade_pct)
        if prev_close is not None and prev_close > 0 and open_px > prev_close * 1.005:
            gap_up_upsides.append(up_pct)
            gap_up_minutes.append(mins_from_open)
        prev_close = close_px

    n = len(peak_minutes)
    if n == 0:
        return {"status": "unavailable", "ticker": sym, "sessions_analyzed": 0, "error": "Insufficient RTH sessions"}

    mins_sorted = sorted(peak_minutes)
    up_sorted = sorted(upsides_pct)
    gap_up_sorted = sorted(gap_up_upsides)

    p25_min = _percentile(mins_sorted, 25)
    p50_min = _percentile(mins_sorted, 50)
    p75_min = _percentile(mins_sorted, 75)

    pct_before_1030 = round((sum(1 for m in peak_minutes if m <= 60.0) / n) * 100.0, 1)
    pct_before_1100 = round((sum(1 for m in peak_minutes if m <= 90.0) / n) * 100.0, 1)

    gap_n = len(gap_up_minutes)
    profile = {
        "status": "ok",
        "ticker": sym,
        "sessions_analyzed": n,
        "gap_up_sessions": gap_n,
        "p25_minute_from_open": round(p25_min, 1),
        "p50_minute_from_open": round(p50_min, 1),
        "p75_minute_from_open": round(p75_min, 1),
        "peak_window_start_et": _minutes_to_et_str(p25_min),
        "median_peak_time_et": _minutes_to_et_str(p50_min),
        "peak_window_end_et": _minutes_to_et_str(p75_min),
        "pct_peaking_before_1030": pct_before_1030,
        "pct_peaking_before_1100": pct_before_1100,
        "upside_from_open_p50_pct": round(_percentile(up_sorted, 50), 2),
        "upside_from_open_p75_pct": round(_percentile(up_sorted, 75), 2),
        "upside_from_open_p90_pct": round(_percentile(up_sorted, 90), 2),
        "gap_up_upside_p50_pct": round(_percentile(gap_up_sorted, 50), 2) if gap_n > 0 else None,
        "gap_up_pct_before_1030": round((sum(1 for m in gap_up_minutes if m <= 60.0) / gap_n) * 100.0, 1) if gap_n > 0 else None,
        "avg_fade_from_peak_pct": round(sum(fades_pct) / n, 2),
        "avg_morning_fade_pct": round(sum(morning_fades_pct) / len(morning_fades_pct), 2) if morning_fades_pct else None,
        "cached": False,
    }

    if used_yfinance and cache_path:
        with _CACHE_LOCK:
            cdata = _read_cache(cache_path)
            cdata["tickers"][sym] = {"cached_at": ts, "profile": profile}
            _write_cache(cache_path, cdata)

    return profile


def predict_daily_peak(
    ticker: str,
    live_row: Optional[Dict[str, Any]] = None,
    tracker: Optional[Dict[str, Any]] = None,
    bars_df: Any = None,
    ny_dt: Optional[datetime] = None,
    cache_path: Optional[str] = DEFAULT_PEAK_CACHE_PATH,
    calibration_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Combines 60d empirical RTH profile + live telemetry (and optional Colab priors) into peak price & exhaustion score."""
    sym = str(ticker or "").strip().upper()
    now_ny = ny_dt or datetime.now(NY_TZ)
    if now_ny.tzinfo is None:
        now_ny = now_ny.replace(tzinfo=NY_TZ)
    else:
        now_ny = now_ny.astimezone(NY_TZ)

    row = live_row if isinstance(live_row, dict) else {}
    trk = tracker if isinstance(tracker, dict) else {}
    profile = compute_historical_peak_profile(sym, bars_df=bars_df, cache_path=cache_path, now_ts=now_ny.timestamp())

    colab_prior = None
    try:
        try:
            from colab_calibration import DEFAULT_CALIBRATION_PATH, get_ticker_peak_prior
        except ImportError:
            from python.colab_calibration import DEFAULT_CALIBRATION_PATH, get_ticker_peak_prior
        eff_cal_path = DEFAULT_CALIBRATION_PATH if calibration_path is None else calibration_path
        colab_prior = get_ticker_peak_prior(sym, calibration_path=eff_cal_path)
    except Exception:
        colab_prior = None

    if profile.get("status") != "ok" and isinstance(colab_prior, dict) and colab_prior:
        profile = {
            "status": "ok",
            "ticker": sym,
            "source": "colab_calibration",
            **colab_prior,
            "cached": False,
        }

    price = _safe_float(row.get("price"))
    open_px = _safe_float(row.get("open"))
    prev_close = _safe_float(row.get("regular_close"))
    gap_pct = _safe_float(row.get("gap_percent")) or 0.0
    vwap = _safe_float(row.get("vwap"))
    dist_vwap = _safe_float(row.get("distance_from_vwap"))
    if dist_vwap is None and price and vwap and vwap > 0:
        dist_vwap = ((price - vwap) / vwap) * 100.0
    rsi = _safe_float(row.get("rsi")) or 50.0
    rvol = _safe_float(row.get("rvol"))
    posture = str(row.get("dealer_posture") or "NEUTRAL").strip().upper()
    fib = row.get("fib_forecast") if isinstance(row.get("fib_forecast"), dict) else {}

    anchor = None
    if open_px and open_px > 0:
        anchor = open_px
    elif prev_close and prev_close > 0:
        anchor = prev_close * (1.0 + gap_pct / 100.0) if gap_pct != 0.0 else prev_close
    elif price and price > 0:
        anchor = price

    p50_peak = None
    p75_peak = None
    peak_low = None
    peak_high = None
    front_run_sell = None

    if profile.get("status") == "ok" and anchor and anchor > 0:
        use_gap = gap_pct > 0.5 and (profile.get("gap_up_sessions") or 0) >= 8 and profile.get("gap_up_upside_p50_pct") is not None
        base_up = float(profile["gap_up_upside_p50_pct"] if use_gap else profile["upside_from_open_p50_pct"])
        stretch_up = max(base_up, float(profile["upside_from_open_p75_pct"]))
        p50_peak = round(anchor * (1.0 + base_up / 100.0), 2)
        p75_peak = round(anchor * (1.0 + stretch_up / 100.0), 2)

        fib_cap = _safe_float(fib.get("daily_peak_target"))
        atr_cap = _safe_float(fib.get("daily_peak_atr"))
        low_cands = [p50_peak]
        if fib_cap and fib_cap >= anchor * 0.99:
            low_cands.append(fib_cap)
        peak_low = round(min(low_cands), 2)

        high_cands = [p75_peak]
        if atr_cap and atr_cap >= peak_low:
            high_cands.append(atr_cap)
        peak_high = round(max(peak_low, min(high_cands)), 2)
        front_run_sell = round(peak_low * 0.9975, 2)

    # Exhaustion Score (0-100)
    score = 0
    signals: List[str] = []
    cur_min = float((now_ny.hour * 60 + now_ny.minute) - (9 * 60 + 30))

    if profile.get("status") == "ok" and 0.0 <= cur_min <= 390.0:
        win_start = float(profile.get("p25_minute_from_open", 15.0))
        win_end = float(profile.get("p75_minute_from_open", 90.0))
        in_morning_spike = (5.0 <= cur_min <= 75.0 and float(profile.get("pct_peaking_before_1030", 0.0)) >= 35.0)
        in_emp_window = (win_start <= cur_min <= win_end)
        if in_morning_spike or in_emp_window:
            score += 20
            signals.append(f"TIME_WINDOW ({now_ny.strftime('%H:%M')} ET inside peak corridor)")

    if price and p50_peak and p50_peak > 0:
        if price >= p50_peak * 0.985:
            score += 25
            signals.append(f"RANGE_REACHED (spot >= p50 ${p50_peak:.2f})")
        elif price >= p50_peak * 0.96:
            score += 15
            signals.append(f"APPROACHING_P50 (${p50_peak:.2f})")

    if dist_vwap is not None:
        if dist_vwap >= 3.0:
            score += 15
            signals.append(f"VWAP_EXTENDED (+{dist_vwap:.2f}%)")
        elif dist_vwap >= 2.0:
            score += 10
            signals.append(f"ABOVE_VWAP (+{dist_vwap:.2f}%)")

    if rsi >= 72.0:
        score += 15
        signals.append(f"RSI_OVERBOUGHT ({rsi:.1f})")
    elif rsi >= 68.0:
        score += 10
        signals.append(f"RSI_STRETCHED ({rsi:.1f})")

    hod = _safe_float(fib.get("pB"))
    if price and hod and hod > 0 and price >= hod * 0.995:
        if (rvol is not None and rvol > 0 and rvol < 1.0) or trk.get("rvol_decelerating_3x"):
            score += 15
            signals.append("HOD_RVOL_DECEL (fade risk near HOD)")

    t1 = _safe_float(fib.get("t1_100"))
    t2 = _safe_float(fib.get("t2_1618"))
    fib_cap = _safe_float(fib.get("daily_peak_target"))
    near_res = any(
        lvl and lvl > 0 and price and abs(price - lvl) / lvl <= 0.012
        for lvl in (fib_cap, t1, t2)
    )
    if near_res or (posture == "LONG_GAMMA" and dist_vwap is not None and dist_vwap > 1.0):
        score += 10
        signals.append("RESISTANCE_GEX_CONFLUENCE")

    score = max(0, min(100, score))
    if score >= 70:
        ex_state = "SELL_ZONE"
    elif score >= 45:
        ex_state = "APPROACHING_PEAK"
    else:
        ex_state = "BUILDING"

    result = {
        "status": profile.get("status", "unavailable"),
        "ticker": sym,
        "as_of_ny": now_ny.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "anchor_open_usd": round(anchor, 2) if anchor else None,
        "current_price_usd": round(price, 2) if price else None,
        "p50_peak_usd": p50_peak,
        "p75_peak_usd": p75_peak,
        "predicted_peak_low_usd": peak_low,
        "predicted_peak_high_usd": peak_high,
        "front_run_sell_limit_usd": front_run_sell,
        "exhaustion_score": score,
        "exhaustion_state": ex_state,
        "signals": signals,
        "profile": profile,
    }
    if colab_prior is not None:
        result["colab_prior"] = colab_prior
    return result
