"""
Google Colab MCP Worker #4: Multi-Expiry SVI Volatility Surface & 2nd-Order Greeks.

Can run remotely inside Google Colab (reading `GEM_JOB_INPUTS` and printing JSON
wrapped in `<<<GEM_COLAB_JSON_START>>>` / `<<<GEM_COLAB_JSON_END>>>`) or locally
for unit tests using injected option chain fixtures.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import least_squares
from scipy.stats import norm

JSON_START_MARKER = "<<<GEM_COLAB_JSON_START>>>"
JSON_END_MARKER = "<<<GEM_COLAB_JSON_END>>>"

MAX_EXPIRIES = 6
MAX_DTE = 90
DEFAULT_RISK_FREE_RATE = 0.045


def black_scholes_greeks(
    spot: float,
    strike: float,
    t_years: float,
    iv: float,
    r: float = DEFAULT_RISK_FREE_RATE,
    option_type: str = "call",
) -> Dict[str, float]:
    """
    Compute analytical Black-Scholes Gamma, Vanna (dDelta/dSigma), and Charm (-dDelta/dt).
    Handles near-zero time or vol cleanly without producing NaN/Inf.
    """
    s = float(spot)
    k = float(strike)
    t = max(float(t_years), 1.0 / (365.0 * 24.0))
    sigma = max(float(iv), 1e-4)
    if s <= 0.0 or k <= 0.0 or not math.isfinite(s) or not math.isfinite(k) or not math.isfinite(sigma):
        return {"gamma": 0.0, "vanna": 0.0, "charm": 0.0, "d1": 0.0, "d2": 0.0}

    sqrt_t = math.sqrt(t)
    vol_sqrt_t = sigma * sqrt_t
    d1 = (math.log(s / k) + (r + 0.5 * sigma * sigma) * t) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    pdf_d1 = float(norm.pdf(d1))

    gamma = pdf_d1 / (s * vol_sqrt_t)
    # Vanna = dDelta / dSigma = -phi(d1) * d2 / sigma
    vanna = -(pdf_d1 * d2) / sigma
    # Charm = -dDelta / dt = -phi(d1) * (2*r*t - d2*sigma*sqrt(t)) / (2*t*sigma*sqrt(t))
    charm = -pdf_d1 * ((2.0 * r * t - d2 * vol_sqrt_t) / (2.0 * t * vol_sqrt_t))
    if str(option_type).lower().startswith("p"):
        # Put charm differs only by continuous dividend yield q; with q=0 call/put gamma, vanna, charm match.
        pass

    return {
        "gamma": float(gamma) if math.isfinite(gamma) else 0.0,
        "vanna": float(vanna) if math.isfinite(vanna) else 0.0,
        "charm": float(charm) if math.isfinite(charm) else 0.0,
        "d1": float(d1),
        "d2": float(d2),
    }


def _svi_raw(k: np.ndarray, params: Sequence[float]) -> np.ndarray:
    a, b, rho, m, sigma = params
    dx = k - m
    return a + b * (rho * dx + np.sqrt(dx * dx + sigma * sigma))


def fit_svi_slice(
    log_moneyness: Sequence[float],
    total_variance: Sequence[float],
    t_years: float = 30.0 / 365.0,
) -> Dict[str, float]:
    """
    Fit raw SVI total variance w(k) = a + b*(rho*(k-m) + sqrt((k-m)^2 + sigma^2))
    using bounded non-linear least squares; falls back gracefully on sparse slices.
    """
    k_arr = np.asarray(log_moneyness, dtype=float)
    w_arr = np.asarray(total_variance, dtype=float)
    mask = np.isfinite(k_arr) & np.isfinite(w_arr) & (w_arr > 1e-6) & (np.abs(k_arr) <= 1.2)
    k_arr = k_arr[mask]
    w_arr = w_arr[mask]

    if len(k_arr) == 0:
        return {"a": 0.02, "b": 0.01, "rho": 0.0, "m": 0.0, "sigma": 0.1, "fit_rmse": 0.0, "skew_25d": 0.0}

    w_med = float(np.median(w_arr))
    if len(k_arr) < 5:
        atm_iv = math.sqrt(max(w_med, 1e-6) / max(t_years, 1.0 / 365.0)) * 100.0
        left = w_arr[k_arr < -0.02]
        right = w_arr[k_arr > 0.02]
        skew_pct = 0.0
        if len(left) > 0 and len(right) > 0:
            iv_l = math.sqrt(float(np.mean(left)) / max(t_years, 1.0 / 365.0)) * 100.0
            iv_r = math.sqrt(float(np.mean(right)) / max(t_years, 1.0 / 365.0)) * 100.0
            skew_pct = iv_r - iv_l
        return {
            "a": round(w_med, 6),
            "b": 0.05,
            "rho": 0.0,
            "m": 0.0,
            "sigma": 0.1,
            "fit_rmse": 0.0 if len(k_arr) <= 1 else round(float(np.std(w_arr)), 6),
            "skew_25d": round(max(-200.0, min(200.0, skew_pct)), 4),
            "atm_iv_pct": round(min(1000.0, atm_iv), 4),
        }

    x0 = np.array([max(1e-4, w_med * 0.6), 0.1, -0.3, 0.0, 0.15], dtype=float)
    lb = np.array([-0.5, 0.0, -0.995, -0.5, 0.005], dtype=float)
    ub = np.array([5.0, 2.0, 0.995, 0.5, 2.0], dtype=float)

    def _resid(p: np.ndarray) -> np.ndarray:
        return _svi_raw(k_arr, p) - w_arr

    res = least_squares(_resid, x0=x0, bounds=(lb, ub), max_nfev=300)
    p = res.x
    fitted = _svi_raw(k_arr, p)
    rmse = float(np.sqrt(np.mean((fitted - w_arr) ** 2)))

    t_eff = max(float(t_years), 1.0 / 365.0)
    w_atm = float(max(_svi_raw(np.array([0.0]), p)[0], 1e-6))
    atm_iv = math.sqrt(w_atm / t_eff)
    # Approximate +/-25-delta log-moneyness wing spread (~ +/-0.674 * sigma_atm * sqrt(T))
    dk = min(0.45, max(0.05, 0.674 * atm_iv * math.sqrt(t_eff)))
    w_down = float(max(_svi_raw(np.array([-dk]), p)[0], 1e-6))
    w_up = float(max(_svi_raw(np.array([dk]), p)[0], 1e-6))
    skew_25d = (math.sqrt(w_up / t_eff) - math.sqrt(w_down / t_eff)) * 100.0

    return {
        "a": round(float(p[0]), 6),
        "b": round(float(p[1]), 6),
        "rho": round(float(p[2]), 6),
        "m": round(float(p[3]), 6),
        "sigma": round(float(p[4]), 6),
        "fit_rmse": round(min(100.0, rmse), 6),
        "skew_25d": round(max(-200.0, min(200.0, skew_25d)), 4),
        "atm_iv_pct": round(min(1000.0, atm_iv * 100.0), 4),
    }


def compute_ticker_vol_surface(
    spot: float,
    expiries_chains: Sequence[Dict[str, Any]],
    r: float = DEFAULT_RISK_FREE_RATE,
) -> Optional[Dict[str, Any]]:
    """
    Compute multi-expiry SVI parameters + dealer GEX/Vanna/Charm exposure for one ticker.
    `expiries_chains` items: {"dte": float, "calls": [...], "puts": [...]} where contract dicts contain
    strike, impliedVolatility (decimal or %), openInterest.
    """
    s = float(spot)
    if not math.isfinite(s) or s <= 0.0 or not expiries_chains:
        return None

    selected = sorted(
        (ex for ex in expiries_chains if 0.5 <= float(ex.get("dte") or 0.0) <= MAX_DTE),
        key=lambda x: float(x.get("dte") or 999.0),
    )[:MAX_EXPIRIES]
    if not selected:
        return None

    strike_gex: Dict[float, float] = {}
    total_net_gex = 0.0
    total_vanna = 0.0
    total_charm = 0.0
    slice_stats: List[Tuple[float, float, float, float]] = []  # (dte, atm_iv, skew, rmse)

    for ex in selected:
        dte = float(ex["dte"])
        t_years = max(dte / 365.0, 1.0 / 365.0)
        log_k_list: List[float] = []
        tot_w_list: List[float] = []

        for opt_type, sign in (("calls", 1.0), ("puts", -1.0)):
            for row in ex.get(opt_type) or ():
                try:
                    k = float(row.get("strike") or 0.0)
                    iv_raw = float(row.get("impliedVolatility") or 0.0)
                    oi = float(row.get("openInterest") or 0.0)
                except (TypeError, ValueError):
                    continue
                if k <= 0.0 or iv_raw <= 0.0 or not (math.isfinite(k) and math.isfinite(iv_raw) and math.isfinite(oi)):
                    continue
                iv = iv_raw / 100.0 if iv_raw > 5.0 else iv_raw
                iv = max(0.01, min(5.0, iv))

                # Collect near-the-money implied total variance for SVI fit
                log_m = math.log(k / s)
                if -0.75 <= log_m <= 0.75:
                    log_k_list.append(log_m)
                    tot_w_list.append((iv * iv) * t_years)

                if oi <= 0.0:
                    continue
                greeks = black_scholes_greeks(s, k, t_years, iv, r=r, option_type=opt_type[:-1])
                # Dealer assumed long calls (+1), short puts (-1) convention matching standard GEM GEX sign
                gex_contrib = sign * greeks["gamma"] * oi * 100.0 * (s * s) * 0.01 / 1_000_000.0
                vanna_contrib = sign * greeks["vanna"] * oi * 100.0 * s * 0.01 / 1_000_000.0
                charm_contrib = sign * greeks["charm"] * oi * 100.0 / 1_000_000.0

                total_net_gex += gex_contrib
                total_vanna += vanna_contrib
                total_charm += charm_contrib
                strike_gex[k] = strike_gex.get(k, 0.0) + gex_contrib

        if log_k_list:
            svi = fit_svi_slice(log_k_list, tot_w_list, t_years=t_years)
            slice_stats.append((dte, svi.get("atm_iv_pct", 30.0), svi["skew_25d"], svi["fit_rmse"]))

    if not slice_stats:
        return None

    # Interpolate ~30D ATM IV and term-structure slope across fitted expiries
    dtes = np.array([x[0] for x in slice_stats], dtype=float)
    ivs = np.array([x[1] for x in slice_stats], dtype=float)
    skews = np.array([x[2] for x in slice_stats], dtype=float)
    rmses = np.array([x[3] for x in slice_stats], dtype=float)

    atm_iv_30d = float(np.interp(30.0, dtes, ivs))
    svi_skew_25d = float(np.interp(30.0, dtes, skews))
    term_slope = float((ivs[-1] - ivs[0]) if len(ivs) >= 2 else 0.0)
    fit_rmse = float(np.mean(rmses))

    # Multi-expiry gamma flip strike interpolation
    gamma_flip = s
    sorted_strikes = sorted(strike_gex.keys())
    if len(sorted_strikes) >= 2:
        vals = [strike_gex[k] for k in sorted_strikes]
        # Cumulative or strike-level zero crossing closest to spot
        crossings: List[float] = []
        for idx in range(len(sorted_strikes) - 1):
            v0, v1 = vals[idx], vals[idx + 1]
            k0, k1 = sorted_strikes[idx], sorted_strikes[idx + 1]
            if (v0 <= 0.0 <= v1) or (v1 <= 0.0 <= v0):
                if v1 != v0:
                    k_cross = k0 + (-v0) * (k1 - k0) / (v1 - v0)
                    if 0.5 * s <= k_cross <= 1.5 * s:
                        crossings.append(k_cross)
        if crossings:
            gamma_flip = min(crossings, key=lambda kc: abs(kc - s))

    # Classify dominant dealer flow regime
    if total_net_gex < -1.0:
        regime = "VOL_ACCELERATION"
    elif total_vanna > abs(total_charm) and total_vanna > 0.5:
        regime = "VANNA_TAILWIND"
    elif total_charm < -abs(total_vanna) and abs(total_charm) > 0.5:
        regime = "CHARM_BLEED"
    elif total_net_gex > 1.0:
        regime = "PINNING"
    else:
        regime = "NEUTRAL"

    return {
        "atm_iv_30d": round(max(0.0, min(1000.0, atm_iv_30d)), 4),
        "svi_skew_25d": round(max(-200.0, min(200.0, svi_skew_25d)), 4),
        "term_structure_slope": round(max(-200.0, min(200.0, term_slope)), 4),
        "fit_rmse": round(max(0.0, min(100.0, fit_rmse)), 6),
        "gamma_flip_multi_expiry": round(max(0.0, min(1_000_000.0, gamma_flip)), 4),
        "net_gex_multi_expiry": round(max(-1_000_000.0, min(1_000_000.0, total_net_gex)), 4),
        "vanna_exposure": round(max(-1_000_000.0, min(1_000_000.0, total_vanna)), 4),
        "charm_decay_bias": round(max(-1_000_000.0, min(1_000_000.0, total_charm)), 4),
        "dealer_flow_regime": regime,
        "expiries_analyzed": len(selected),
    }


def _yfinance_chain_fetcher(ticker: str) -> Optional[Tuple[float, List[Dict[str, Any]]]]:
    import yfinance as yf

    tk = yf.Ticker(ticker)
    hist = tk.history(period="5d", auto_adjust=False)
    if hist is None or hist.empty:
        return None
    spot = float(hist["Close"].dropna().iloc[-1])
    now_date = datetime.now(timezone.utc).date()
    expiries = list(getattr(tk, "options", None) or ())
    chains: List[Dict[str, Any]] = []
    for exp_str in expiries:
        try:
            exp_dt = datetime.strptime(exp_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        dte = float((exp_dt - now_date).days)
        if dte < 1.0 or dte > MAX_DTE:
            continue
        try:
            oc = tk.option_chain(exp_str)
            calls = oc.calls[["strike", "impliedVolatility", "openInterest"]].fillna(0.0).to_dict("records")
            puts = oc.puts[["strike", "impliedVolatility", "openInterest"]].fillna(0.0).to_dict("records")
            chains.append({"dte": dte, "calls": calls, "puts": puts})
        except Exception:
            continue
        if len(chains) >= MAX_EXPIRIES:
            break
    return (spot, chains)


def run_vol_surface_job(
    job_inputs: Dict[str, Any],
    *,
    chain_fetcher: Optional[Callable[[str], Optional[Tuple[float, List[Dict[str, Any]]]]]] = None,
) -> Dict[str, Any]:
    """Build a Schema v2.0 `vol_surface` artifact from `job_inputs`."""
    raw_syms = list(job_inputs.get("symbols") or [])
    for idx_sym in ("SPY", "QQQ"):
        if idx_sym not in raw_syms:
            raw_syms.append(idx_sym)

    seen = set()
    symbols: List[str] = []
    for s in raw_syms:
        sym = str(s or "").strip().upper()
        if sym and sym not in seen and not sym.startswith("^"):
            seen.add(sym)
            symbols.append(sym)

    fetcher = chain_fetcher or _yfinance_chain_fetcher
    tickers_out: Dict[str, Dict[str, Any]] = {}
    for sym in symbols[:30]:
        try:
            fetched = fetcher(sym)
            if not fetched:
                continue
            spot, chains = fetched
            metrics = compute_ticker_vol_surface(spot, chains)
            if metrics is not None:
                tickers_out[sym] = metrics
        except Exception:
            continue

    return {
        "schema_version": "2.0",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "google_colab_mcp",
        "notes": f"Multi-expiry SVI & 2nd-order Greeks across {len(tickers_out)} symbol(s).",
        "tickers": tickers_out,
    }


if __name__ == "__main__":
    _inputs = globals().get("GEM_JOB_INPUTS") or {}
    _result = run_vol_surface_job(_inputs)
    print(JSON_START_MARKER + json.dumps(_result, separators=(",", ":")) + JSON_END_MARKER)
