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

EXCLUDED_NON_STOCK_TICKERS: frozenset[str] = frozenset({
    # Volatility & VIX ETPs/indices
    "VIX", "VVIX", "VIXY", "VIXM", "UVXY", "SVXY", "VXX", "UVIX", "SVIX",
    # Broad, equal-weight & leveraged/inverse index ETFs
    "SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "VT", "RSP", "MDY",
    "SQQQ", "TQQQ", "SPXU", "SPXS", "UPRO", "SH", "SDS", "PSQ", "QID",
    "TZA", "TNA", "SDOW", "UDOW", "SOXL", "SOXS",
    # Treasury, bond, currency & commodity ETFs
    "IEF", "TLT", "SHY", "IEI", "BND", "AGG", "HYG", "LQD", "TIP",
    "SGOV", "BIL", "TMF", "TMV", "TBT", "UUP", "UDN", "FXE", "FXY",
    "GLD", "SLV", "IAU", "GDX", "GDXJ", "USO", "UNG", "DBC", "PDBC", "DBA",
    # Sector & thematic index ETFs
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLY", "XLU", "XLB", "XLRE",
    "XLC", "SMH", "SOXX", "XBI", "IBB", "ITA", "ARKK", "KRE", "KBE", "XRT",
    "XHB", "XME", "XOP", "OIH",
})


def is_excluded_non_stock_ticker(symbol: str) -> bool:
    """Return True for volatility products, market indices, macro/commodity/sector ETFs, and futures."""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return True
    if sym.startswith("^") or "=" in sym or sym == "DX-Y.NYB":
        return True
    return sym in EXCLUDED_NON_STOCK_TICKERS


# Curated fallback basket of liquid individual equities across sectors (expanded via public constituent lists in Colab)
_FALLBACK_SECTOR_MAP: Dict[str, str] = {
    # Technology / AI & Data / Semiconductors
    "AAPL": "Technology", "MSFT": "Technology", "NOW": "Technology", "ADBE": "Technology",
    "CRM": "Technology", "ORCL": "Technology", "ANET": "Technology", "IBM": "Technology",
    "PLTR": "AI & Data", "CRWD": "AI & Data", "PANW": "AI & Data", "NET": "AI & Data",
    "SNOW": "AI & Data", "DDOG": "AI & Data", "MDB": "AI & Data", "SMCI": "AI & Data",
    "NVDA": "Semiconductors", "AVGO": "Semiconductors", "AMD": "Semiconductors",
    "ARM": "Semiconductors", "TSM": "Semiconductors", "ASML": "Semiconductors",
    "MU": "Semiconductors", "MRVL": "Semiconductors", "INTC": "Semiconductors",
    "QCOM": "Semiconductors", "AMAT": "Semiconductors", "LRCX": "Semiconductors",
    "KLAC": "Semiconductors", "TXN": "Semiconductors",
    # Communication Services / Consumer Discretionary
    "GOOGL": "Communication Services", "GOOG": "Communication Services", "META": "Communication Services",
    "NFLX": "Communication Services", "DIS": "Communication Services", "TMUS": "Communication Services",
    "AMZN": "Consumer Discretionary", "TSLA": "Consumer Discretionary", "HD": "Consumer Discretionary",
    "LOW": "Consumer Discretionary", "MCD": "Consumer Discretionary", "SBUX": "Consumer Discretionary",
    "NKE": "Consumer Discretionary", "TGT": "Consumer Discretionary", "BKNG": "Consumer Discretionary",
    "ABNB": "Consumer Discretionary",
    # Consumer Staples
    "COST": "Consumer Staples", "WMT": "Consumer Staples", "PG": "Consumer Staples",
    "KO": "Consumer Staples", "PEP": "Consumer Staples",
    # Aerospace & Defense / Industrials
    "LMT": "Aerospace & Defense", "RTX": "Aerospace & Defense", "NOC": "Aerospace & Defense",
    "GD": "Aerospace & Defense", "BA": "Aerospace & Defense", "KTOS": "Aerospace & Defense",
    "AVAV": "Aerospace & Defense", "RKLB": "Aerospace & Defense", "LUNR": "Aerospace & Defense",
    "ASTS": "Aerospace & Defense",
    "CAT": "Industrials", "DE": "Industrials", "GE": "Industrials", "ETN": "Industrials",
    "PWR": "Industrials", "URI": "Industrials", "PH": "Industrials", "EMR": "Industrials",
    "HON": "Industrials", "MMM": "Industrials",
    # Biotech / Healthcare
    "REGN": "Biotech", "GILD": "Biotech", "VRTX": "Biotech", "AMGN": "Biotech",
    "CRSP": "Biotech", "MRNA": "Biotech", "BIIB": "Biotech", "BEAM": "Biotech",
    "LLY": "Healthcare", "NVO": "Healthcare", "UNH": "Healthcare", "JNJ": "Healthcare",
    "ISRG": "Healthcare", "ABBV": "Healthcare", "MRK": "Healthcare", "TMO": "Healthcare",
    # Financials
    "JPM": "Financials", "GS": "Financials", "MS": "Financials", "V": "Financials",
    "MA": "Financials", "COIN": "Financials", "HOOD": "Financials", "SOFI": "Financials",
    "XYZ": "Financials", "PYPL": "Financials", "BAC": "Financials", "BLK": "Financials",
    # Energy / Utilities / Materials / Real Estate
    "XOM": "Energy", "CVX": "Energy", "COP": "Energy", "SLB": "Energy", "OXY": "Energy",
    "CEG": "Utilities", "VST": "Utilities", "SMR": "Utilities", "OKLO": "Utilities", "NEE": "Utilities",
    "CCJ": "Energy", "FCX": "Materials", "LIN": "Materials", "NEM": "Materials", "AMT": "Real Estate",
}

_GICS_SECTOR_ALIASES: Dict[str, str] = {
    "information technology": "Technology",
    "technology": "Technology",
    "health care": "Healthcare",
    "healthcare": "Healthcare",
    "financials": "Financials",
    "energy": "Energy",
    "industrials": "Industrials",
    "consumer discretionary": "Consumer Discretionary",
    "consumer staples": "Consumer Staples",
    "utilities": "Utilities",
    "real estate": "Real Estate",
    "materials": "Materials",
    "communication services": "Communication Services",
    "ai & data": "AI & Data",
    "aerospace & defense": "Aerospace & Defense",
    "biotech": "Biotech",
    "biotechnology": "Biotech",
    "semiconductors": "Semiconductors",
}

_SECTOR_PARENTS: Dict[str, Tuple[str, ...]] = {
    "Technology": ("Technology", "Semiconductors", "AI & Data"),
    "Healthcare": ("Healthcare", "Biotech"),
    "Industrials": ("Industrials", "Aerospace & Defense"),
}


def normalize_sector_name(raw_sector: str, sub_industry: str = "", symbol: str = "") -> str:
    """Map GICS / thematic sector strings onto the dashboard's canonical sector taxonomy."""
    sym = str(symbol or "").strip().upper()
    if sym in _FALLBACK_SECTOR_MAP:
        return _FALLBACK_SECTOR_MAP[sym]
    sub_low = str(sub_industry or "").strip().lower()
    if "biotech" in sub_low:
        return "Biotech"
    if "semiconductor" in sub_low:
        return "Semiconductors"
    if "aerospace" in sub_low or "defense" in sub_low:
        return "Aerospace & Defense"
    if any(k in sub_low for k in ("artificial intelligence", "data processing", "cloud", "cybersecurity", "systems software")):
        return "AI & Data"
    sec_low = str(raw_sector or "").strip().lower()
    if sec_low in _GICS_SECTOR_ALIASES:
        return _GICS_SECTOR_ALIASES[sec_low]
    raw_clean = str(raw_sector or "").strip()
    return raw_clean[:48] if raw_clean else "Other"


def infer_sector_for_ticker(symbol: str, sector_map: Optional[Dict[str, str]] = None) -> str:
    """Resolve canonical sector label for an equity ticker from fetched GICS map or fallback dictionary."""
    sym = str(symbol or "").strip().upper()
    if sector_map and sym in sector_map and str(sector_map[sym]).strip():
        return normalize_sector_name(sector_map[sym], symbol=sym)
    return _FALLBACK_SECTOR_MAP.get(sym, "Other")


def _sector_matches_filter(candidate_sector: str, requested_sectors: Sequence[str]) -> bool:
    if not requested_sectors:
        return True
    cand_norm = normalize_sector_name(candidate_sector)
    cand_low = cand_norm.lower()
    for req in requested_sectors:
        req_norm = normalize_sector_name(str(req or ""))
        allowed_children = _SECTOR_PARENTS.get(req_norm)
        if allowed_children:
            if cand_norm in allowed_children:
                return True
        elif cand_low == req_norm.lower():
            return True
    return False


_FALLBACK_UNIVERSE: Tuple[str, ...] = (
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


def fetch_public_universe_with_sectors(max_tickers: int = 550) -> Tuple[List[str], Dict[str, str]]:
    """Fetch public S&P 500 + Nasdaq-100 constituents and GICS sector tags using pandas read_html."""
    collected: List[str] = list(_FALLBACK_UNIVERSE)
    sector_map: Dict[str, str] = dict(_FALLBACK_SECTOR_MAP)
    urls = (
        ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", 0, ("Symbol", "Ticker")),
        ("https://en.wikipedia.org/wiki/Nasdaq-100", 4, ("Ticker", "Symbol")),
    )
    for url, table_idx, col_names in urls:
        try:
            tables = pd.read_html(url)
            if len(tables) > table_idx:
                df = tables[table_idx]
                sym_col = next((c for c in col_names if c in df.columns), None)
                if sym_col is not None:
                    sec_col = next((c for c in ("GICS Sector", "Sector", "Industry") if c in df.columns), None)
                    sub_col = next((c for c in ("GICS Sub-Industry", "Sub-Industry", "Subsector") if c in df.columns), None)
                    for _, row in df.iterrows():
                        sym = str(row.get(sym_col) or "").strip().upper().replace(".", "-")
                        if not sym or len(sym) > 10 or sym == "NAN":
                            continue
                        collected.append(sym)
                        if sec_col is not None and sym not in sector_map:
                            raw_sec = str(row.get(sec_col) or "").strip()
                            raw_sub = str(row.get(sub_col) or "").strip() if sub_col is not None else ""
                            if raw_sec and raw_sec.lower() != "nan":
                                sector_map[sym] = normalize_sector_name(raw_sec, raw_sub, sym)
        except Exception:
            continue

    seen = set()
    unique: List[str] = []
    for sym in collected:
        if sym not in seen and not is_excluded_non_stock_ticker(sym):
            seen.add(sym)
            unique.append(sym)
            if len(unique) >= max_tickers:
                break
    return unique, sector_map


def fetch_public_universe(max_tickers: int = 550) -> List[str]:
    """Fetch public S&P 500 + Nasdaq-100 constituents using pandas read_html with offline fallback."""
    universe, _ = fetch_public_universe_with_sectors(max_tickers=max_tickers)
    return universe


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


def _compute_rsi_14(closes: pd.Series) -> float:
    """Compute latest 14-day RSI from closing prices."""
    if len(closes) < 15:
        return 50.0
    delta = closes.diff()
    gain = delta.clip(lower=0.0).rolling(14, min_periods=14).mean()
    loss = (-delta.clip(upper=0.0)).rolling(14, min_periods=14).mean()
    last_gain = float(gain.iloc[-1]) if pd.notna(gain.iloc[-1]) else 0.0
    last_loss = float(loss.iloc[-1]) if pd.notna(loss.iloc[-1]) else 0.0
    if last_loss <= 1e-12:
        rsi = 100.0 if last_gain > 0.0 else 50.0
    else:
        rs = last_gain / last_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
    return round(max(0.0, min(100.0, float(rsi))), 2)


def score_universe_histories(
    price_histories: Dict[str, pd.DataFrame],
    portfolio_weights_pct: Optional[Dict[str, float]] = None,
    *,
    top_n: int = 50,
    min_adv_usd_m: float = 20.0,
    sector_map: Optional[Dict[str, str]] = None,
    sectors_filter: Optional[Sequence[str]] = None,
    max_rsi: Optional[float] = None,
) -> Dict[str, Any]:
    """Score & rank universe OHLCV histories into a Schema v2.0 `quant_scout` payload."""
    weights = {str(k).strip().upper(): float(v) for k, v in (portfolio_weights_pct or {}).items() if v}
    held_set = set(weights.keys())
    clean_sectors = [str(s).strip() for s in (sectors_filter or ()) if str(s).strip()]
    rsi_cap = float(max_rsi) if max_rsi is not None else None

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
        if not sym or sym in held_set or is_excluded_non_stock_ticker(sym) or df is None or df.empty:
            continue
        sector_label = infer_sector_for_ticker(sym, sector_map)
        if clean_sectors and not _sector_matches_filter(sector_label, clean_sectors):
            continue
        if "Close" not in df.columns or "Volume" not in df.columns:
            continue

        close = pd.to_numeric(df["Close"], errors="coerce").dropna()
        vol = pd.to_numeric(df["Volume"], errors="coerce").reindex(close.index).fillna(0.0)
        if len(close) < 65:
            continue

        rsi_14 = _compute_rsi_14(close)
        if rsi_cap is not None and rsi_14 > rsi_cap:
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
            "sector": sector_label,
            "composite_score": composite_score,
            "rs_1m_pct": round(max(-100.0, min(1000.0, float(rs_1m))), 4),
            "rs_3m_pct": round(max(-100.0, min(2000.0, float(rs_3m))), 4),
            "rs_6m_pct": round(max(-100.0, min(5000.0, float(rs_6m))), 4),
            "rsi_14": rsi_14,
            "corr_to_portfolio": round(corr, 4),
            "vol_regime": vol_regime,
            "pead_score": round(pead_score, 4),
            "adv_usd_m": round(min(1_000_000.0, adv_usd_m), 4),
        })

    scored_rows.sort(key=lambda r: r["composite_score"], reverse=True)
    capped_n = max(1, min(100, int(top_n or 50)))
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
                f"[{row['sector']}] 3m RS {row['rs_3m_pct']:+.1f}%, 1m {row['rs_1m_pct']:+.1f}%, "
                f"RSI {row['rsi_14']:.0f}, corr {row['corr_to_portfolio']:+.2f}, "
                f"ADV ${row['adv_usd_m']:.0f}M ({row['vol_regime']})"
            )[:300]

    filter_parts: List[str] = []
    if clean_sectors:
        filter_parts.append(f"sectors={','.join(clean_sectors[:6])}")
    if rsi_cap is not None:
        filter_parts.append(f"RSI<={rsi_cap:.0f}")
    filter_note = f" Filters: {'; '.join(filter_parts)}." if filter_parts else ""
    return {
        "schema_version": "2.0",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "google_colab_mcp",
        "notes": f"Screened {len(price_histories)} universe ticker(s); ADV >= ${min_adv_usd_m:.0f}M.{filter_note}",
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
    top_n = int(options.get("top_n") or 50)
    weights = job_inputs.get("portfolio_weights_pct") or {}
    sectors_opt = options.get("sectors")
    clean_sectors = [str(s).strip() for s in sectors_opt if str(s).strip()] if isinstance(sectors_opt, list) else []
    max_rsi_opt = options.get("max_rsi")
    max_rsi = float(max_rsi_opt) if max_rsi_opt is not None else None

    custom_tickers = options.get("tickers")
    sector_map: Dict[str, str] = dict(_FALLBACK_SECTOR_MAP)
    if isinstance(custom_tickers, list) and custom_tickers:
        universe = [
            str(t).strip().upper()
            for t in custom_tickers
            if str(t).strip() and not is_excluded_non_stock_ticker(str(t))
        ]
    else:
        universe, sector_map = fetch_public_universe_with_sectors(max_tickers=550)
        if clean_sectors:
            targeted = [s for s in universe if _sector_matches_filter(infer_sector_for_ticker(s, sector_map), clean_sectors)]
            if targeted:
                universe = targeted

    # Include held portfolio symbols so correlation-to-portfolio can be computed accurately;
    # non-stock macro/index symbols from cached telemetry are intentionally omitted from screening.
    for sym in list(weights.keys()):
        s = str(sym or "").strip().upper()
        if s and s not in universe:
            universe.append(s)

    fetcher = history_fetcher or _yfinance_bulk_history_fetcher
    histories = fetcher(universe)
    return score_universe_histories(
        histories,
        weights,
        top_n=top_n,
        sector_map=sector_map,
        sectors_filter=clean_sectors or None,
        max_rsi=max_rsi,
    )


if __name__ == "__main__":
    _inputs = globals().get("GEM_JOB_INPUTS") or {}
    _result = run_quant_scout_job(_inputs)
    print(JSON_START_MARKER + json.dumps(_result, separators=(",", ":")) + JSON_END_MARKER)
