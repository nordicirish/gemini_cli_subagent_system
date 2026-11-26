"""
Google Colab MCP Worker #5: Broad 500+ Ticker Quantitative Scout.

Screens a broad liquid public equity universe (~500+ tickers when online via
S&P 500 + Nasdaq-100 + high-beta thematic ETFs/equities), ranks candidates by
multi-horizon momentum, realized volatility regime, PEAD earnings-drift proxy,
and diversification (low correlation to active GEM portfolio), and outputs a
compact Schema v2.0 `quant_scout` artifact.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

JSON_START_MARKER = "<<<GEM_COLAB_JSON_START>>>"
JSON_END_MARKER = "<<<GEM_COLAB_JSON_END>>>"

# Curated fallback basket of liquid equities across sectors (expanded via public constituent lists in Colab)
_FALLBACK_UNIVERSE: Tuple[str, ...] = (
    "SPY", "QQQ", "IWM", "SMH", "XBI", "ITA", "XLE", "XLF", "XLK", "XLV",
    "AAPL", "MSFT", "NVDA", "AVGO", "AMD", "ARM", "TSM", "ASML", "MU", "MRVL",
    "AMZN", "GOOGL", "META", "TSLA", "NFLX", "PLTR", "CRWD", "PANW", "NET", "SNOW",
    "NOW", "ADBE", "CRM", "ORCL", "INTC", "QCOM", "AMAT", "LRCX", "KLAC", "ANET",
    "LMT", "RTX", "NOC", "GD", "BA", "KTOS", "AVAV", "RKLB", "LUNR", "ASTS",
    "LLY", "NVO", "UNH", "JNJ", "ISRG", "AMGN", "GILD", "VRTX", "REGN", "CRSP",
    "JPM", "GS", "MS", "V", "MA", "COIN", "HOOD", "SOFI", "XYZ", "PYPL",
    "XOM", "CVX", "COP", "SLB", "OXY", "CEG", "VST", "CCJ", "SMR", "OKLO",
    "CAT", "DE", "GE", "ETN", "PWR", "URI", "PH", "EMR", "HON", "MMM",
    "COST", "WMT", "HD", "LOW", "MCD", "SBUX", "NKE", "TGT", "BKNG", "ABNB",
)


def fetch_public_universe(max_tickers: int = 550) -> List[str]:
    """Fetch public S&P 500 + Nasdaq-100 constituents using pandas read_html with offline fallback."""
    collected: List[str] = list(_FALLBACK_UNIVERSE)
    urls = (
        ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", 0, ("Symbol", "Ticker")),
        ("https://en.wikipedia.org/wiki/Nasdaq-100", 4, ("Ticker", "Symbol")),
    )
    for url, table_idx, col_names in urls:
        try:
            tables = pd.read_html(url)
            if len(tables) > table_idx:
                df = tables[table_idx]
                for col in col_names:
                    if col in df.columns:
                        for raw in df[col].dropna().astype(str).tolist():
                            sym = raw.strip().upper().replace(".", "-")
                            if sym and len(sym) <= 10:
                                collected.append(sym)
                        break
        except Exception:
            continue

    seen = set()
    unique: List[str] = []
    for sym in collected:
        if sym not in seen:
            seen.add(sym)
            unique.append(sym)
            if len(unique) >= max_tickers:
                break
    return unique


def _classify_vol_regime(ann_vol_pct: float) -> str:
    if ann_vol_pct < 22.0:
        return "LOW_VOL"
    if ann_vol_pct < 38.0:
        return "NORMAL"
    if ann_vol_pct < 60.0:
        return "ELEVATED"
    return "HIGH_VOL"


def _assign_clusters(features: np.ndarray, rs_3m: Sequence[float], corr_vals: Sequence[float]) -> List[str]:
    """Deterministic lightweight k-means clustering across standardized candidate features."""
    n = features.shape[0]
    if n == 0:
        return []
    if n < 3:
        return ["HIGH_RS_DIVERSIFIER" if c < 0.45 else "CORE_MOMENTUM" for c in corr_vals]

    mean = np.mean(features, axis=0, keepdims=True)
    std = np.std(features, axis=0, keepdims=True)
    std = np.where(std < 1e-6, 1.0, std)
    z = (features - mean) / std

    # Deterministic centroid initialization at quantiles of composite order
    k = min(3, n)
    idx_sorted = np.argsort(z[:, 0] - z[:, 2])
    centroids = z[idx_sorted[np.linspace(0, n - 1, k, dtype=int)]].copy()
    labels = np.zeros(n, dtype=int)
    for _ in range(10):
        dists = np.linalg.norm(z[:, None, :] - centroids[None, :, :], axis=2)
        new_labels = np.argmin(dists, axis=1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for cid in range(k):
            pts = z[labels == cid]
            if len(pts) > 0:
                centroids[cid] = np.mean(pts, axis=0)

    # Human-readable stable labels per cluster based on mean RS and portfolio correlation
    cluster_names: Dict[int, str] = {}
    for cid in range(k):
        idxs = [i for i in range(n) if labels[i] == cid]
        if not idxs:
            cluster_names[cid] = f"CLUSTER_{cid}"
            continue
        avg_rs = float(np.mean([rs_3m[i] for i in idxs]))
        avg_corr = float(np.mean([corr_vals[i] for i in idxs]))
        if avg_corr < 0.35 and avg_rs > 0.0:
            cluster_names[cid] = "LOW_CORR_ALPHA"
        elif avg_rs >= 10.0:
            cluster_names[cid] = "HIGH_MOMENTUM_LEADER"
        else:
            cluster_names[cid] = "MEAN_REVERT_BASE"
    return [cluster_names[int(lb)] for lb in labels]


def score_universe_histories(
    price_histories: Dict[str, pd.DataFrame],
    portfolio_weights_pct: Optional[Dict[str, float]] = None,
    *,
    top_n: int = 25,
    min_adv_usd_m: float = 20.0,
) -> Dict[str, Any]:
    """Score & rank universe OHLCV histories into a Schema v2.0 `quant_scout` payload."""
    weights = {str(k).strip().upper(): float(v) for k, v in (portfolio_weights_pct or {}).items() if v}
    held_set = set(weights.keys())

    # Build synthetic daily return series of current portfolio for diversification correlation scoring
    port_ret_series: Optional[pd.Series] = None
    ret_cols: Dict[str, pd.Series] = {}
    ret_weights: List[Tuple[str, float]] = []
    for sym, w_pct in weights.items():
        df = price_histories.get(sym)
        if df is not None and not df.empty and "Close" in df.columns:
            closes = pd.to_numeric(df["Close"], errors="coerce").dropna()
            if len(closes) >= 25:
                ret_cols[sym] = closes.pct_change().dropna()
                ret_weights.append((sym, max(0.0, float(w_pct))))
    if ret_weights:
        total_w = sum(w for _, w in ret_weights)
        if total_w > 0:
            joined = pd.DataFrame(ret_cols).dropna()
            if not joined.empty:
                vec = np.array([w / total_w for _, w in ret_weights], dtype=float)
                port_ret_series = pd.Series(joined.values @ vec, index=joined.index)

    scored_rows: List[Dict[str, Any]] = []
    for raw_sym, df in price_histories.items():
        sym = str(raw_sym or "").strip().upper()
        if not sym or sym in held_set or df is None or df.empty:
            continue
        if "Close" not in df.columns or "Volume" not in df.columns:
            continue

        close = pd.to_numeric(df["Close"], errors="coerce").dropna()
        vol = pd.to_numeric(df["Volume"], errors="coerce").reindex(close.index).fillna(0.0)
        if len(close) < 65:
            continue

        adv_usd_m = float((close.tail(20) * vol.tail(20)).mean() / 1_000_000.0)
        if not math.isfinite(adv_usd_m) or adv_usd_m < min_adv_usd_m:
            continue

        last_px = float(close.iloc[-1])
        rs_1m = ((last_px / float(close.iloc[-22])) - 1.0) * 100.0 if len(close) >= 22 and close.iloc[-22] > 0 else 0.0
        rs_3m = ((last_px / float(close.iloc[-64])) - 1.0) * 100.0 if len(close) >= 64 and close.iloc[-64] > 0 else 0.0
        rs_6m = ((last_px / float(close.iloc[-127])) - 1.0) * 100.0 if len(close) >= 127 and close.iloc[-127] > 0 else rs_3m

        rets = close.pct_change().dropna()
        if len(rets) < 20:
            continue
        ann_vol = float(rets.tail(20).std() * math.sqrt(252.0) * 100.0)
        vol_regime = _classify_vol_regime(ann_vol)

        # PEAD proxy: largest single-day gap/surge over last 40 sessions with >2.5x volume
        tail_r = rets.tail(40)
        tail_v = vol.tail(40)
        avg_v = float(vol.tail(60).mean()) or 1.0
        pead_mask = tail_v >= (2.2 * avg_v)
        pead_score = float(tail_r[pead_mask].max() * 400.0) if bool(pead_mask.any()) else 0.0
        pead_score = max(-100.0, min(100.0, pead_score))

        # Correlation to portfolio
        corr = 0.0
        if port_ret_series is not None:
            aligned = pd.concat([rets.rename("cand"), port_ret_series.rename("port")], axis=1).dropna().tail(63)
            if len(aligned) >= 20 and aligned["cand"].std() > 1e-8 and aligned["port"].std() > 1e-8:
                cval = float(aligned["cand"].corr(aligned["port"]))
                if math.isfinite(cval):
                    corr = max(-1.0, min(1.0, cval))

        # Composite score rewarding 3m/1m momentum & PEAD, penalizing high correlation and extreme vol
        raw_comp = (0.55 * rs_3m) + (0.35 * rs_1m) + (0.20 * pead_score) - (18.0 * max(0.0, corr)) - (0.15 * max(0.0, ann_vol - 40.0))
        composite_score = round(max(-100.0, min(100.0, float(raw_comp))), 4)

        scored_rows.append({
            "ticker": sym,
            "composite_score": composite_score,
            "rs_1m_pct": round(max(-100.0, min(1000.0, float(rs_1m))), 4),
            "rs_3m_pct": round(max(-100.0, min(2000.0, float(rs_3m))), 4),
            "rs_6m_pct": round(max(-100.0, min(5000.0, float(rs_6m))), 4),
            "corr_to_portfolio": round(corr, 4),
            "vol_regime": vol_regime,
            "pead_score": round(pead_score, 4),
            "adv_usd_m": round(min(1_000_000.0, adv_usd_m), 4),
        })

    scored_rows.sort(key=lambda r: r["composite_score"], reverse=True)
    capped_n = max(1, min(100, int(top_n or 25)))
    top_rows = scored_rows[:capped_n]

    if top_rows:
        feat = np.array([[r["rs_3m_pct"], r["rs_1m_pct"], r["corr_to_portfolio"]] for r in top_rows], dtype=float)
        clusters = _assign_clusters(
            feat,
            [r["rs_3m_pct"] for r in top_rows],
            [r["corr_to_portfolio"] for r in top_rows],
        )
        for row, cl in zip(top_rows, clusters):
            row["cluster_label"] = cl
            row["rationale"] = (
                f"3m RS {row['rs_3m_pct']:+.1f}%, corr {row['corr_to_portfolio']:+.2f}, "
                f"ADV ${row['adv_usd_m']:.0f}M ({row['vol_regime']})"
            )[:300]

    return {
        "schema_version": "2.0",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "google_colab_mcp",
        "notes": f"Screened {len(price_histories)} universe ticker(s); ADV >= ${min_adv_usd_m:.0f}M.",
        "universe_size": max(1, min(5000, len(price_histories))),
        "candidates": top_rows,
    }


def _yfinance_bulk_history_fetcher(universe: Sequence[str]) -> Dict[str, pd.DataFrame]:
    import yfinance as yf

    out: Dict[str, pd.DataFrame] = {}
    chunk_size = 50
    for i in range(0, len(universe), chunk_size):
        chunk = list(universe[i:i + chunk_size])
        try:
            data = yf.download(
                chunk,
                period="1y",
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


def run_quant_scout_job(
    job_inputs: Dict[str, Any],
    *,
    history_fetcher: Optional[Callable[[Sequence[str]], Dict[str, pd.DataFrame]]] = None,
) -> Dict[str, Any]:
    """Run broad universe quant screening from `job_inputs`."""
    options = job_inputs.get("options") or {}
    top_n = int(options.get("top_n") or 25)
    weights = job_inputs.get("portfolio_weights_pct") or {}

    custom_tickers = options.get("tickers")
    if isinstance(custom_tickers, list) and custom_tickers:
        universe = [str(t).strip().upper() for t in custom_tickers if str(t).strip()]
    else:
        universe = fetch_public_universe(max_tickers=550)

    for sym in list(weights.keys()) + list(job_inputs.get("symbols") or []):
        s = str(sym or "").strip().upper()
        if s and s not in universe:
            universe.append(s)

    fetcher = history_fetcher or _yfinance_bulk_history_fetcher
    histories = fetcher(universe)
    return score_universe_histories(histories, weights, top_n=top_n)


if __name__ == "__main__":
    _inputs = globals().get("GEM_JOB_INPUTS") or {}
    _result = run_quant_scout_job(_inputs)
    print(JSON_START_MARKER + json.dumps(_result, separators=(",", ":")) + JSON_END_MARKER)
