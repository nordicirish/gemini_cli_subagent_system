"""
Pure Deterministic Merton Optimal Allocation Engine.

Extracted from `python/fetch_stocks.py` during Stage D to isolate portfolio math
and thread-safe memoization from daemon orchestration.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional, Sequence

import numpy as np
import pandas as pd


_MERTON_CACHE_LOCK = threading.Lock()
_MERTON_CACHE_KEY: Any = None
_MERTON_CACHE_VAL: Optional[dict] = None


def compute_merton_from_history(
    history_map: Dict[str, pd.DataFrame],
    tickers: Sequence[str],
    gamma: float = 3.0,
    r: float = 0.04,
) -> Dict[str, Any]:
    """Computes annualized Merton optimal weights and Sharpe stats with memoization."""
    global _MERTON_CACHE_KEY, _MERTON_CACHE_VAL
    try:
        history_sig = []
        prices_dict = {}
        for sym in tickers:
            sym_upper = str(sym).upper()
            df = history_map.get(sym_upper)
            if df is not None and not df.empty:
                history_sig.append((sym_upper, len(df), str(df.index[-1])))
                if "Close" in df.columns:
                    close = df["Close"]
                    if isinstance(close, pd.DataFrame):
                        close = close.iloc[:, 0]
                    valid_close = close[close > 0.0]
                    if len(valid_close) > 10:
                        prices_dict[sym_upper] = valid_close

        cache_key = (tuple(sorted(history_sig)), float(gamma), float(r))
        with _MERTON_CACHE_LOCK:
            if _MERTON_CACHE_KEY == cache_key and _MERTON_CACHE_VAL is not None:
                return dict(_MERTON_CACHE_VAL)

        if not prices_dict:
            return {
                "status": "error",
                "message": "No historical price data found in cache for active tickers.",
            }

        df_prices = pd.DataFrame(prices_dict).ffill().bfill()
        df_returns = df_prices.pct_change().dropna(how="all")
        if df_returns.empty:
            return {
                "status": "error",
                "message": "Calculated returns dataframe is empty.",
            }

        stds = df_returns.std()
        valid_cols = stds[stds > 1e-7].index.tolist()
        if len(valid_cols) < 1:
            return {
                "status": "error",
                "message": "Insufficient variance in asset price history to compute covariance matrix.",
            }

        df_returns = df_returns[valid_cols]
        df_prices = df_prices[valid_cols]

        mean_daily = df_returns.mean()
        mu = mean_daily * 252

        cov_daily = df_returns.cov()
        sigma = cov_daily * 252

        n_assets = sigma.shape[0]
        sigma_reg = sigma + np.eye(n_assets) * 1e-4

        excess_returns = mu - r
        weights = np.linalg.solve(sigma_reg, excess_returns) / gamma

        raw_allocation = {ticker: float(w) for ticker, w in zip(df_prices.columns, weights)}

        clipped_weights = np.clip(weights, 0, None)
        sum_clipped = np.sum(clipped_weights)
        if sum_clipped > 0:
            normalized_weights = clipped_weights / sum_clipped
        else:
            normalized_weights = np.zeros_like(weights)

        normalized_allocation = {ticker: float(w) for ticker, w in zip(df_prices.columns, normalized_weights)}

        vols = df_returns.std() * np.sqrt(252)
        sharpe_ratios = {}
        for ticker in df_prices.columns:
            vol = vols[ticker]
            expected_ret = mu[ticker]
            sharpe_ratios[ticker] = float((expected_ret - r) / vol) if vol > 0 else 0.0

        result = {
            "status": "success",
            "parameters": {
                "risk_aversion_gamma": float(gamma),
                "annual_risk_free_rate": float(r),
                "trading_days": 252,
            },
            "raw_merton_weights": raw_allocation,
            "normalized_long_only_weights": normalized_allocation,
            "annualized_statistics": {
                ticker: {
                    "expected_return": float(mu[ticker]),
                    "volatility": float(vols[ticker]),
                    "sharpe_ratio": sharpe_ratios[ticker],
                }
                for ticker in df_prices.columns
            },
        }
        with _MERTON_CACHE_LOCK:
            _MERTON_CACHE_KEY = cache_key
            _MERTON_CACHE_VAL = dict(result)
        return result
    except Exception as e:
        return {
            "status": "error",
            "message": f"Calculation failed: {str(e)}",
        }
