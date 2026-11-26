"""
Extracted Chat Payload Serialization & Security Veto Inspection Helpers.

Moved out of `python/web_server.py` during Stage D cleanup while preserving
full backwards-compatible re-exports on `web_server`.
"""

from __future__ import annotations

import collections.abc
from decimal import Decimal
import json
from typing import Any, Optional


PAYLOAD_MARKERS = ("EXECUTION_PAYLOAD", "thoughtSignature", "portfolio_snapshot")


def normalize_for_json(obj: Any) -> Any:
    """Recursively converts read-only Mapping/Sequence views into plain JSON-serializable structures."""
    try:
        from fetch_stocks import sanitize_json_payload
        return sanitize_json_payload(obj)
    except ImportError:
        try:
            from python.fetch_stocks import sanitize_json_payload
            return sanitize_json_payload(obj)
        except ImportError:
            if isinstance(obj, collections.abc.Mapping):
                return {k: normalize_for_json(v) for k, v in obj.items()}
            if isinstance(obj, collections.abc.Sequence) and not isinstance(obj, (str, bytes)):
                return [normalize_for_json(x) for x in obj]
            return obj


def assemble_slim_tickers(raw_tickers: Any) -> list[dict]:
    """
    Assembles non-authoritative market feed telemetry for DATA_PACKET injection.
    Fails closed: missing price_validity and freshness explicitly default to UNAVAILABLE.
    """
    slim_tickers = []
    if isinstance(raw_tickers, collections.abc.Sequence) and not isinstance(raw_tickers, (str, bytes)):
        for t in raw_tickers:
            if isinstance(t, collections.abc.Mapping):
                slim_tickers.append({
                    "ticker": t.get("ticker"),
                    "price": t.get("price"),
                    "price_validity": t.get("price_validity", "UNAVAILABLE"),
                    "freshness": t.get("freshness", "UNAVAILABLE"),
                    "price_source": t.get("price_source"),
                    "observation_time": t.get("observation_time"),
                    "retrieval_time": t.get("retrieval_time"),
                    "session_change_pct": t.get("session_change_pct"),
                    "gap_percent": t.get("gap_percent"),
                    "rsi": t.get("rsi"),
                    "macd": t.get("macd", 0.0),
                    "macd_signal": t.get("macd_signal", 0.0),
                    "macd_hist": t.get("macd_hist", 0.0),
                    "macd_slope": t.get("macd_slope", "EXPANDING_POSITIVE"),
                    "macd_status": t.get("macd_status", "NEUTRAL"),
                    "atr_percent": t.get("atr_percent"),
                    "vwap": t.get("vwap"),
                    "trend": t.get("trend"),
                    "signal": t.get("signal"),
                    "score": t.get("score"),
                    "dealer_posture": t.get("dealer_posture"),
                    "net_gex_total": round(t.get("net_gex_total", 0.0), 3) if t.get("net_gex_total") is not None else 0.0,
                    "fib_forecast": t.get("fib_forecast"),
                    "note": t.get("note"),
                })
    return slim_tickers


def is_halted_execution_payload(raw_json: str) -> Optional[str]:
    """
    Returns a reason string if an extracted EXECUTION_PAYLOAD represents a halted,
    truncated, or ENH_12 state-absence veto response that must never overwrite SSoT.
    """
    try:
        parsed = json.loads(raw_json, parse_float=Decimal)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None

    ep = parsed.get("EXECUTION_PAYLOAD") if isinstance(parsed.get("EXECUTION_PAYLOAD"), dict) else parsed
    runtime_flags = ep.get("runtime_flags") if isinstance(ep.get("runtime_flags"), dict) else {}
    if runtime_flags.get("halt_active") is True or runtime_flags.get("data_gap") is True:
        return "Council halt / data-gap veto active"

    state_ctx = ep.get("state_context") if isinstance(ep.get("state_context"), dict) else {}
    state_status = str(state_ctx.get("status", "")).upper()
    state_err = str(state_ctx.get("error", "")).upper()
    if state_status == "CRITICAL_SYNC_ALERT" or "STATE_ABSENCE_VETO" in state_err:
        return "CRITICAL_SYNC_ALERT / ENH_12 state-absence veto active"

    forensic = ep.get("forensic_intelligence") if isinstance(ep.get("forensic_intelligence"), dict) else {}
    active_flags = forensic.get("active_flags", [])
    if isinstance(active_flags, list):
        for flag in active_flags:
            flag_u = str(flag).upper()
            if any(marker in flag_u for marker in ("CRITICAL_SYNC_ALERT", "STATE_ABSENCE_VETO", "INPUT_TRUNCATION")):
                return "Council truncation / state-absence veto active"

    return None


def looks_like_execution_payload(text: str) -> bool:
    return any(marker in text for marker in PAYLOAD_MARKERS)
