"""
Offline Google Colab Calibration Bundle Export & Fail-Closed Import.

Lets heavy batch quant research (multi-year backtests, Monte Carlo Merton stress
tests, and extended intraday peak-window calibration) run offline in Google Colab
and safely re-import compact, schema-validated calibration JSON into `cache/`.
Never modifies Protocol 06 files (`gem_trading_rules/`, `engine_instructions/`, `context/`).
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import math
import os
import re
import sys
import threading
from typing import Any, Dict, List, Optional, Tuple

try:
    from atomic_io import atomic_write_json
except ImportError:
    from python.atomic_io import atomic_write_json

try:
    from persistence import get_persistence_service
except ImportError:
    from python.persistence import get_persistence_service

try:
    from colab_workers.quant_scout_worker import infer_sector_for_ticker, is_excluded_non_stock_ticker
    from colab_workers.walk_forward_worker import rule_deltas_improve_baseline
except ImportError:
    from python.colab_workers.quant_scout_worker import infer_sector_for_ticker, is_excluded_non_stock_ticker
    from python.colab_workers.walk_forward_worker import rule_deltas_improve_baseline


CALIBRATION_SCHEMA_VERSION = "1.0"
ARTIFACT_SCHEMA_VERSION = "2.0"
DEFAULT_CACHE_DIR = "cache"
DEFAULT_CALIBRATION_PATH = os.path.join(DEFAULT_CACHE_DIR, "colab_calibration.json")
DEFAULT_DAILY_CACHE_PATH = os.path.join(DEFAULT_CACHE_DIR, "daily_history_cache.json")
DEFAULT_PEAK_CACHE_PATH = os.path.join(DEFAULT_CACHE_DIR, "peak_profiles_cache.json")

ARTIFACT_REGISTRY: Dict[str, Tuple[str, float]] = {
    "vol_surface": ("colab_vol_surface.json", 24.0),
    "quant_scout": ("colab_scout_candidates.json", 48.0),
    "sec_nlp": ("colab_sec_nlp.json", 72.0),
    "backtest_evidence": ("colab_backtest_evidence.json", 168.0),
}

_VALID_DEALER_FLOW_REGIMES = frozenset({
    "PINNING",
    "VANNA_TAILWIND",
    "CHARM_BLEED",
    "VOL_ACCELERATION",
    "NEUTRAL",
})

_TICKER_RE = re.compile(r"^[A-Z0-9.\-\^]{1,12}$")
_HHMM_RE = re.compile(r"^\d{2}:\d{2}$")
_ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$")

_ALLOWED_TOP_KEYS = frozenset({
    "schema_version",
    "generated_at_utc",
    "imported_at_utc",
    "source",
    "notes",
    "peak_priors",
    "merton_stress",
    "backtest_summary",
})

_CACHE_LOCK = threading.Lock()
_CALIBRATION_CACHE: Dict[str, Tuple[Tuple[int, int], Dict[str, Any]]] = {}


def _is_valid_number(val: Any, min_val: float, max_val: float) -> bool:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return False
    f = float(val)
    return math.isfinite(f) and min_val <= f <= max_val


def _is_valid_int(val: Any, min_val: int, max_val: int) -> bool:
    return not isinstance(val, bool) and isinstance(val, int) and min_val <= val <= max_val


def _validate_peak_priors(raw: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(raw, dict):
        raise ValueError("peak_priors must be a dictionary mapping ticker symbols to prior metrics.")
    cleaned: Dict[str, Dict[str, Any]] = {}
    allowed_fields = {
        "sessions_analyzed": ("int", 1, 2000),
        "p25_minute_from_open": ("float", 0.0, 390.0),
        "p50_minute_from_open": ("float", 0.0, 390.0),
        "p75_minute_from_open": ("float", 0.0, 390.0),
        "pct_peaking_before_1030": ("float", 0.0, 100.0),
        "pct_peaking_before_1100": ("float", 0.0, 100.0),
        "upside_from_open_p50_pct": ("float", 0.0, 100.0),
        "upside_from_open_p75_pct": ("float", 0.0, 100.0),
        "upside_from_open_p90_pct": ("float", 0.0, 100.0),
        "avg_fade_from_peak_pct": ("float", -100.0, 0.0),
    }
    time_str_fields = ("peak_window_start_et", "median_peak_time_et", "peak_window_end_et")
    for raw_sym, entry in raw.items():
        sym = str(raw_sym or "").strip().upper()
        if not _TICKER_RE.match(sym):
            raise ValueError(f"Invalid ticker symbol in peak_priors: {raw_sym!r}")
        if not isinstance(entry, dict):
            raise ValueError(f"peak_priors[{sym}] must be a dictionary.")
        out_entry: Dict[str, Any] = {}
        for k, v in entry.items():
            if k in allowed_fields:
                kind, lo, hi = allowed_fields[k]
                if kind == "int":
                    if not _is_valid_int(v, int(lo), int(hi)):
                        raise ValueError(f"peak_priors[{sym}].{k} must be an integer in [{int(lo)}, {int(hi)}].")
                    out_entry[k] = int(v)
                else:
                    if not _is_valid_number(v, float(lo), float(hi)):
                        raise ValueError(f"peak_priors[{sym}].{k} must be a finite number in [{lo}, {hi}].")
                    out_entry[k] = round(float(v), 4)
            elif k in time_str_fields:
                if not isinstance(v, str) or not _HHMM_RE.match(v.strip()):
                    raise ValueError(f"peak_priors[{sym}].{k} must match HH:MM format.")
                out_entry[k] = v.strip()
            else:
                raise ValueError(f"Unknown field {k!r} in peak_priors[{sym}].")
        if (
            "upside_from_open_p50_pct" in out_entry
            and "upside_from_open_p75_pct" in out_entry
            and out_entry["upside_from_open_p75_pct"] < out_entry["upside_from_open_p50_pct"]
        ):
            raise ValueError(f"peak_priors[{sym}].upside_from_open_p75_pct cannot be below p50.")
        cleaned[sym] = out_entry
    return cleaned


def _validate_merton_stress(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("merton_stress must be a dictionary.")
    out: Dict[str, Any] = {}
    if "monte_carlo_cvar_95_pct" in raw:
        val = raw["monte_carlo_cvar_95_pct"]
        if not _is_valid_number(val, -100.0, 0.0):
            raise ValueError("merton_stress.monte_carlo_cvar_95_pct must be in [-100.0, 0.0].")
        out["monte_carlo_cvar_95_pct"] = round(float(val), 4)
    if "recommended_gamma" in raw:
        val = raw["recommended_gamma"]
        if not _is_valid_number(val, 0.5, 20.0):
            raise ValueError("merton_stress.recommended_gamma must be in [0.5, 20.0].")
        out["recommended_gamma"] = round(float(val), 4)
    if "stress_correlation_regime" in raw:
        regime = raw["stress_correlation_regime"]
        if not isinstance(regime, str) or not regime.strip():
            raise ValueError("merton_stress.stress_correlation_regime must be a non-empty string.")
        out["stress_correlation_regime"] = regime.strip()[:64]
    if "ticker_weights" in raw:
        tw = raw["ticker_weights"]
        if not isinstance(tw, dict):
            raise ValueError("merton_stress.ticker_weights must be a dictionary.")
        cleaned_tw: Dict[str, float] = {}
        for k, v in tw.items():
            sym = str(k or "").strip().upper()
            if not _TICKER_RE.match(sym) or not _is_valid_number(v, 0.0, 1.0):
                raise ValueError(f"Invalid merton_stress.ticker_weights entry: {k!r}={v!r}")
            cleaned_tw[sym] = round(float(v), 6)
        out["ticker_weights"] = cleaned_tw
    unknown = set(raw.keys()) - {"monte_carlo_cvar_95_pct", "recommended_gamma", "stress_correlation_regime", "ticker_weights"}
    if unknown:
        raise ValueError(f"Unknown field(s) in merton_stress: {sorted(unknown)}")
    return out


def _validate_backtest_summary(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("backtest_summary must be a dictionary.")
    out: Dict[str, Any] = {}
    if "sessions_tested" in raw:
        if not _is_valid_int(raw["sessions_tested"], 0, 50000):
            raise ValueError("backtest_summary.sessions_tested must be a non-negative integer.")
        out["sessions_tested"] = int(raw["sessions_tested"])
    if "sharpe_ratio" in raw:
        if not _is_valid_number(raw["sharpe_ratio"], -10.0, 20.0):
            raise ValueError("backtest_summary.sharpe_ratio must be in [-10.0, 20.0].")
        out["sharpe_ratio"] = round(float(raw["sharpe_ratio"]), 4)
    if "max_drawdown_pct" in raw:
        if not _is_valid_number(raw["max_drawdown_pct"], -100.0, 0.0):
            raise ValueError("backtest_summary.max_drawdown_pct must be in [-100.0, 0.0].")
        out["max_drawdown_pct"] = round(float(raw["max_drawdown_pct"]), 4)
    if "hit_rate_pct" in raw:
        if not _is_valid_number(raw["hit_rate_pct"], 0.0, 100.0):
            raise ValueError("backtest_summary.hit_rate_pct must be in [0.0, 100.0].")
        out["hit_rate_pct"] = round(float(raw["hit_rate_pct"]), 2)
    unknown = set(raw.keys()) - {"sessions_tested", "sharpe_ratio", "max_drawdown_pct", "hit_rate_pct"}
    if unknown:
        raise ValueError(f"Unknown field(s) in backtest_summary: {sorted(unknown)}")
    return out


def validate_colab_calibration(payload: Any) -> Dict[str, Any]:
    """Fail-closed validation for imported Google Colab calibration payloads."""
    if not isinstance(payload, dict):
        raise ValueError("Calibration payload must be a JSON object.")

    unknown_top = set(payload.keys()) - _ALLOWED_TOP_KEYS
    if unknown_top:
        raise ValueError(f"Unknown top-level calibration key(s): {sorted(unknown_top)}")

    schema_ver = payload.get("schema_version")
    if schema_ver != CALIBRATION_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported calibration schema_version {schema_ver!r}; expected {CALIBRATION_SCHEMA_VERSION!r}."
        )

    validated: Dict[str, Any] = {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "source": str(payload.get("source") or "google_colab").strip()[:64] or "google_colab",
    }
    if "generated_at_utc" in payload and payload["generated_at_utc"] is not None:
        validated["generated_at_utc"] = str(payload["generated_at_utc"]).strip()[:64]
    if "imported_at_utc" in payload and payload["imported_at_utc"] is not None:
        validated["imported_at_utc"] = str(payload["imported_at_utc"]).strip()[:64]
    if "notes" in payload and payload["notes"] is not None:
        validated["notes"] = str(payload["notes"]).strip()[:500]
    if "peak_priors" in payload and payload["peak_priors"] is not None:
        validated["peak_priors"] = _validate_peak_priors(payload["peak_priors"])
    else:
        validated["peak_priors"] = {}
    if "merton_stress" in payload and payload["merton_stress"] is not None:
        validated["merton_stress"] = _validate_merton_stress(payload["merton_stress"])
    else:
        validated["merton_stress"] = {}
    if "backtest_summary" in payload and payload["backtest_summary"] is not None:
        validated["backtest_summary"] = _validate_backtest_summary(payload["backtest_summary"])
    else:
        validated["backtest_summary"] = {}

    return validated


def export_colab_bundle(
    service: Optional[Any] = None,
    daily_cache_path: str = DEFAULT_DAILY_CACHE_PATH,
    peak_cache_path: str = DEFAULT_PEAK_CACHE_PATH,
) -> Dict[str, Any]:
    """
    Exports a portable, zero-secret quantitative bundle for offline Colab notebooks.
    Excludes raw cash balances, API keys, and account identifiers.
    """
    eff_service = service if service is not None else get_persistence_service()

    symbols: List[str] = []
    seen = set()

    def _add_sym(raw_sym: Any) -> None:
        sym = str(raw_sym or "").strip().upper()
        if sym and sym not in seen and _TICKER_RE.match(sym):
            seen.add(sym)
            symbols.append(sym)

    portfolio_weights_pct: Dict[str, float] = {}
    try:
        basket = eff_service.get_portfolio_basket()
        raw_vals: Dict[str, float] = {}
        for pos in basket.get("portfolio", []):
            if not isinstance(pos, dict):
                continue
            sym = str(pos.get("ticker") or "").strip().upper()
            try:
                shares = float(pos.get("shares") or 0.0)
                wac = float(pos.get("wac") or 0.0)
            except (TypeError, ValueError):
                continue
            if sym and shares > 0 and wac > 0:
                _add_sym(sym)
                raw_vals[sym] = raw_vals.get(sym, 0.0) + (shares * wac)
        total_val = sum(raw_vals.values())
        if total_val > 0:
            portfolio_weights_pct = {
                s: round((v / total_val) * 100.0, 2)
                for s, v in sorted(raw_vals.items())
            }
    except Exception:
        pass

    try:
        for w in eff_service.get_watchlist():
            _add_sym(w)
    except Exception:
        pass

    daily_history: Dict[str, Any] = {}
    if daily_cache_path and os.path.exists(daily_cache_path):
        try:
            with open(daily_cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if isinstance(cached, dict) and isinstance(cached.get("history"), dict):
                daily_history = cached["history"]
                for sym in daily_history.keys():
                    _add_sym(sym)
        except Exception:
            daily_history = {}

    decision_log_summary: List[Dict[str, Any]] = []
    try:
        for entry in (eff_service.get_decision_log() or [])[-100:]:
            if not isinstance(entry, dict):
                continue
            decision_log_summary.append({
                "ticker": entry.get("ticker"),
                "action_proposed": entry.get("action_proposed"),
                "decision_outcome": entry.get("decision_outcome"),
                "timestamp": entry.get("timestamp") or entry.get("timestamp_utc"),
            })
    except Exception:
        pass

    current_peak_profiles: Dict[str, Any] = {}
    if peak_cache_path and os.path.exists(peak_cache_path):
        try:
            with open(peak_cache_path, "r", encoding="utf-8") as f:
                pdata = json.load(f)
            if isinstance(pdata, dict) and isinstance(pdata.get("tickers"), dict):
                current_peak_profiles = pdata["tickers"]
        except Exception:
            current_peak_profiles = {}

    return {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "exported_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "symbols": symbols,
        "portfolio_weights_pct": portfolio_weights_pct,
        "daily_history": daily_history,
        "decision_log_summary": decision_log_summary,
        "current_peak_profiles": current_peak_profiles,
        "calibration_template": {
            "schema_version": CALIBRATION_SCHEMA_VERSION,
            "generated_at_utc": "YYYY-MM-DDTHH:MM:SSZ",
            "source": "google_colab",
            "notes": "Extended quant calibration results",
            "peak_priors": {
                "SYMBOL": {
                    "sessions_analyzed": 120,
                    "p25_minute_from_open": 15.0,
                    "p50_minute_from_open": 35.0,
                    "p75_minute_from_open": 75.0,
                    "peak_window_start_et": "09:45",
                    "median_peak_time_et": "10:05",
                    "peak_window_end_et": "10:45",
                    "pct_peaking_before_1030": 62.5,
                    "pct_peaking_before_1100": 75.0,
                    "upside_from_open_p50_pct": 2.4,
                    "upside_from_open_p75_pct": 4.1,
                    "upside_from_open_p90_pct": 6.0,
                    "avg_fade_from_peak_pct": -1.8,
                }
            },
            "merton_stress": {
                "monte_carlo_cvar_95_pct": -12.5,
                "recommended_gamma": 3.5,
                "stress_correlation_regime": "ELEVATED",
                "ticker_weights": {"SYMBOL": 0.5},
            },
            "backtest_summary": {
                "sessions_tested": 250,
                "sharpe_ratio": 1.45,
                "max_drawdown_pct": -14.2,
                "hit_rate_pct": 58.0,
            },
        },
    }


def load_colab_calibration(calibration_path: str = DEFAULT_CALIBRATION_PATH) -> Optional[Dict[str, Any]]:
    """Loads and validates `colab_calibration.json` with mtime caching; returns None if missing/invalid."""
    if not calibration_path or not os.path.exists(calibration_path):
        return None
    try:
        abs_path = os.path.abspath(calibration_path)
        st = os.stat(abs_path)
        stat_key = (int(st.st_mtime_ns), int(st.st_size))
        with _CACHE_LOCK:
            hit = _CALIBRATION_CACHE.get(abs_path)
            if hit is not None and hit[0] == stat_key:
                return copy.deepcopy(hit[1])

        with open(abs_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        validated = validate_colab_calibration(raw)

        with _CACHE_LOCK:
            _CALIBRATION_CACHE[abs_path] = (stat_key, copy.deepcopy(validated))
        return validated
    except Exception:
        return None


def import_colab_calibration(
    payload: Any,
    calibration_path: str = DEFAULT_CALIBRATION_PATH,
) -> Dict[str, Any]:
    """Validates and atomically stores Colab calibration JSON into `cache/colab_calibration.json`."""
    validated = validate_colab_calibration(payload)
    validated["imported_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    parent = os.path.dirname(os.path.abspath(calibration_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    atomic_write_json(calibration_path, validated, indent=2)

    try:
        abs_path = os.path.abspath(calibration_path)
        st = os.stat(abs_path)
        with _CACHE_LOCK:
            _CALIBRATION_CACHE[abs_path] = ((int(st.st_mtime_ns), int(st.st_size)), copy.deepcopy(validated))
    except Exception:
        pass

    return {
        "status": "success",
        "schema_version": validated["schema_version"],
        "tickers_calibrated": sorted(validated.get("peak_priors", {}).keys()),
        "has_merton_stress": bool(validated.get("merton_stress")),
        "has_backtest_summary": bool(validated.get("backtest_summary")),
        "imported_at_utc": validated["imported_at_utc"],
        "path": calibration_path,
    }


def get_ticker_peak_prior(
    ticker: str,
    calibration_path: str = DEFAULT_CALIBRATION_PATH,
) -> Optional[Dict[str, Any]]:
    sym = str(ticker or "").strip().upper()
    if not sym:
        return None
    cal = load_colab_calibration(calibration_path)
    if not isinstance(cal, dict):
        return None
    prior = cal.get("peak_priors", {}).get(sym)
    return dict(prior) if isinstance(prior, dict) else None


def get_calibration_status(calibration_path: str = DEFAULT_CALIBRATION_PATH) -> Dict[str, Any]:
    cal = load_colab_calibration(calibration_path)
    if not cal:
        return {
            "loaded": False,
            "schema_version": CALIBRATION_SCHEMA_VERSION,
            "tickers_calibrated": [],
            "imported_at_utc": None,
            "generated_at_utc": None,
            "source": None,
            "merton_stress": {},
            "backtest_summary": {},
        }
    return {
        "loaded": True,
        "schema_version": cal.get("schema_version"),
        "tickers_calibrated": sorted(cal.get("peak_priors", {}).keys()),
        "imported_at_utc": cal.get("imported_at_utc"),
        "generated_at_utc": cal.get("generated_at_utc"),
        "source": cal.get("source"),
        "merton_stress": cal.get("merton_stress", {}),
        "backtest_summary": cal.get("backtest_summary", {}),
    }


def _validate_iso_utc(val: Any, field_name: str, *, required: bool = True) -> Optional[str]:
    if val is None or val == "":
        if required:
            raise ValueError(f"{field_name} is required and must be a UTC ISO-8601 string.")
        return None
    if not isinstance(val, str):
        raise ValueError(f"{field_name} must be a UTC ISO-8601 string.")
    s = val.strip()
    if not _ISO_UTC_RE.match(s):
        raise ValueError(f"{field_name} must match strict UTC ISO-8601 format (YYYY-MM-DDTHH:MM:SSZ).")
    return s


def _validate_v2_envelope(payload: Any, allowed_extra_keys: frozenset) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Artifact payload must be a JSON object.")
    base_keys = frozenset({"schema_version", "generated_at_utc", "imported_at_utc", "source", "notes"})
    allowed_top = base_keys | allowed_extra_keys
    unknown_top = set(payload.keys()) - allowed_top
    if unknown_top:
        raise ValueError(f"Unknown top-level artifact key(s): {sorted(unknown_top)}")
    if payload.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported artifact schema_version {payload.get('schema_version')!r}; expected {ARTIFACT_SCHEMA_VERSION!r}."
        )
    out: Dict[str, Any] = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "generated_at_utc": _validate_iso_utc(payload.get("generated_at_utc"), "generated_at_utc", required=True),
        "source": str(payload.get("source") or "google_colab_mcp").strip()[:64] or "google_colab_mcp",
    }
    imported_ts = _validate_iso_utc(payload.get("imported_at_utc"), "imported_at_utc", required=False)
    if imported_ts is not None:
        out["imported_at_utc"] = imported_ts
    if payload.get("notes") is not None:
        out["notes"] = str(payload["notes"]).strip()[:500]
    return out


def validate_vol_surface_artifact(payload: Any) -> Dict[str, Any]:
    """Fail-closed validation for Schema v2.0 multi-expiry volatility surface & 2nd-order Greeks."""
    out = _validate_v2_envelope(payload, frozenset({"tickers"}))
    raw_tickers = payload.get("tickers")
    if not isinstance(raw_tickers, dict):
        raise ValueError("vol_surface.tickers must be a dictionary.")

    float_bounds: Dict[str, Tuple[float, float]] = {
        "atm_iv_30d": (0.0, 1000.0),
        "svi_skew_25d": (-200.0, 200.0),
        "term_structure_slope": (-200.0, 200.0),
        "fit_rmse": (0.0, 100.0),
        "gamma_flip_multi_expiry": (0.0, 1_000_000.0),
        "net_gex_multi_expiry": (-1_000_000.0, 1_000_000.0),
        "vanna_exposure": (-1_000_000.0, 1_000_000.0),
        "charm_decay_bias": (-1_000_000.0, 1_000_000.0),
    }
    allowed_fields = set(float_bounds.keys()) | {"dealer_flow_regime", "expiries_analyzed"}
    cleaned_tickers: Dict[str, Dict[str, Any]] = {}
    for raw_sym, entry in raw_tickers.items():
        sym = str(raw_sym or "").strip().upper()
        if not _TICKER_RE.match(sym):
            raise ValueError(f"Invalid ticker symbol in vol_surface: {raw_sym!r}")
        if not isinstance(entry, dict):
            raise ValueError(f"vol_surface.tickers[{sym}] must be a dictionary.")
        unknown = set(entry.keys()) - allowed_fields
        if unknown:
            raise ValueError(f"Unknown field(s) in vol_surface.tickers[{sym}]: {sorted(unknown)}")
        clean_entry: Dict[str, Any] = {}
        for k, (lo, hi) in float_bounds.items():
            if k in entry and entry[k] is not None:
                if not _is_valid_number(entry[k], lo, hi):
                    raise ValueError(f"vol_surface.tickers[{sym}].{k} must be a finite number in [{lo}, {hi}].")
                clean_entry[k] = round(float(entry[k]), 6)
        if "expiries_analyzed" in entry and entry["expiries_analyzed"] is not None:
            if not _is_valid_int(entry["expiries_analyzed"], 1, 24):
                raise ValueError(f"vol_surface.tickers[{sym}].expiries_analyzed must be in [1, 24].")
            clean_entry["expiries_analyzed"] = int(entry["expiries_analyzed"])
        if "dealer_flow_regime" in entry and entry["dealer_flow_regime"] is not None:
            regime = str(entry["dealer_flow_regime"]).strip().upper()
            if regime not in _VALID_DEALER_FLOW_REGIMES:
                raise ValueError(f"vol_surface.tickers[{sym}].dealer_flow_regime invalid: {regime!r}")
            clean_entry["dealer_flow_regime"] = regime
        cleaned_tickers[sym] = clean_entry
    out["tickers"] = cleaned_tickers
    return out


def validate_quant_scout_artifact(payload: Any) -> Dict[str, Any]:
    """Fail-closed validation for Schema v2.0 broad 500+ ticker quantitative scout candidates."""
    out = _validate_v2_envelope(payload, frozenset({"universe_size", "candidates"}))
    if not _is_valid_int(payload.get("universe_size"), 1, 5000):
        raise ValueError("quant_scout.universe_size must be an integer in [1, 5000].")
    out["universe_size"] = int(payload["universe_size"])

    raw_cands = payload.get("candidates")
    if not isinstance(raw_cands, list) or len(raw_cands) > 100:
        raise ValueError("quant_scout.candidates must be a list of at most 100 items.")

    float_bounds: Dict[str, Tuple[float, float]] = {
        "composite_score": (-100.0, 100.0),
        "rs_1m_pct": (-100.0, 1000.0),
        "rs_3m_pct": (-100.0, 2000.0),
        "rs_6m_pct": (-100.0, 5000.0),
        "rsi_14": (0.0, 100.0),
        "corr_to_portfolio": (-1.0, 1.0),
        "pead_score": (-100.0, 100.0),
        "adv_usd_m": (0.0, 1_000_000.0),
    }
    allowed_fields = set(float_bounds.keys()) | {"ticker", "sector", "vol_regime", "cluster_label", "rationale"}
    required_fields = {"ticker", "composite_score", "rs_3m_pct", "corr_to_portfolio", "vol_regime", "cluster_label"}
    cleaned_cands: List[Dict[str, Any]] = []
    seen_syms = set()
    for idx, item in enumerate(raw_cands):
        if not isinstance(item, dict):
            raise ValueError(f"quant_scout.candidates[{idx}] must be a dictionary.")
        unknown = set(item.keys()) - allowed_fields
        if unknown:
            raise ValueError(f"Unknown field(s) in quant_scout.candidates[{idx}]: {sorted(unknown)}")
        missing = required_fields - set(item.keys())
        if missing:
            raise ValueError(f"Missing required field(s) in quant_scout.candidates[{idx}]: {sorted(missing)}")
        sym = str(item.get("ticker") or "").strip().upper()
        if not _TICKER_RE.match(sym) or sym in seen_syms:
            raise ValueError(f"Invalid or duplicate ticker in quant_scout.candidates[{idx}]: {sym!r}")
        seen_syms.add(sym)
        if is_excluded_non_stock_ticker(sym):
            continue
        clean_item: Dict[str, Any] = {"ticker": sym}
        if "sector" in item and item["sector"] is not None:
            if not isinstance(item["sector"], str):
                raise ValueError(f"quant_scout.candidates[{idx}].sector must be a string.")
            clean_item["sector"] = item["sector"].strip()[:48] or infer_sector_for_ticker(sym)
        else:
            clean_item["sector"] = infer_sector_for_ticker(sym)
        for k, (lo, hi) in float_bounds.items():
            if k in item and item[k] is not None:
                if not _is_valid_number(item[k], lo, hi):
                    raise ValueError(f"quant_scout.candidates[{idx}].{k} must be finite in [{lo}, {hi}].")
                clean_item[k] = round(float(item[k]), 4)
        for str_k, max_len in (("vol_regime", 32), ("cluster_label", 64)):
            sval = item.get(str_k)
            if not isinstance(sval, str) or not sval.strip():
                raise ValueError(f"quant_scout.candidates[{idx}].{str_k} must be a non-empty string.")
            clean_item[str_k] = sval.strip()[:max_len]
        if "rationale" in item and item["rationale"] is not None:
            if not isinstance(item["rationale"], str):
                raise ValueError(f"quant_scout.candidates[{idx}].rationale must be a string.")
            clean_item["rationale"] = item["rationale"].strip()[:300]
        cleaned_cands.append(clean_item)
    out["candidates"] = cleaned_cands
    return out


def validate_sec_nlp_artifact(payload: Any) -> Dict[str, Any]:
    """Fail-closed validation for Schema v2.0 zero-token GPU SEC/earnings NLP signals."""
    out = _validate_v2_envelope(payload, frozenset({"model_used", "tickers"}))
    model_used = payload.get("model_used")
    if not isinstance(model_used, str) or not model_used.strip():
        raise ValueError("sec_nlp.model_used must be a non-empty string.")
    out["model_used"] = model_used.strip()[:120]

    raw_tickers = payload.get("tickers")
    if not isinstance(raw_tickers, dict):
        raise ValueError("sec_nlp.tickers must be a dictionary.")

    float_bounds: Dict[str, Tuple[float, float]] = {
        "dilution_risk_score": (0.0, 100.0),
        "catalyst_sentiment": (-1.0, 1.0),
        "earnings_tone_delta": (-1.0, 1.0),
        "shelf_remaining_usd_m": (0.0, 1_000_000.0),
        "warrant_overhang_pct": (0.0, 500.0),
    }
    bool_fields = ("offering_overhang_flag", "going_concern_flag")
    allowed_fields = set(float_bounds.keys()) | set(bool_fields) | {"filings_analyzed", "evidence_snippets"}
    required_fields = {"dilution_risk_score", "catalyst_sentiment", "offering_overhang_flag", "going_concern_flag", "filings_analyzed"}
    cleaned_tickers: Dict[str, Dict[str, Any]] = {}
    for raw_sym, entry in raw_tickers.items():
        sym = str(raw_sym or "").strip().upper()
        if not _TICKER_RE.match(sym):
            raise ValueError(f"Invalid ticker symbol in sec_nlp: {raw_sym!r}")
        if not isinstance(entry, dict):
            raise ValueError(f"sec_nlp.tickers[{sym}] must be a dictionary.")
        unknown = set(entry.keys()) - allowed_fields
        if unknown:
            raise ValueError(f"Unknown field(s) in sec_nlp.tickers[{sym}]: {sorted(unknown)}")
        missing = required_fields - set(entry.keys())
        if missing:
            raise ValueError(f"Missing required field(s) in sec_nlp.tickers[{sym}]: {sorted(missing)}")
        clean_entry: Dict[str, Any] = {}
        for k, (lo, hi) in float_bounds.items():
            if k in entry and entry[k] is not None:
                if not _is_valid_number(entry[k], lo, hi):
                    raise ValueError(f"sec_nlp.tickers[{sym}].{k} must be finite in [{lo}, {hi}].")
                clean_entry[k] = round(float(entry[k]), 4)
        for bk in bool_fields:
            if not isinstance(entry[bk], bool):
                raise ValueError(f"sec_nlp.tickers[{sym}].{bk} must be a boolean.")
            clean_entry[bk] = bool(entry[bk])
        if not _is_valid_int(entry["filings_analyzed"], 0, 100):
            raise ValueError(f"sec_nlp.tickers[{sym}].filings_analyzed must be in [0, 100].")
        clean_entry["filings_analyzed"] = int(entry["filings_analyzed"])
        if "evidence_snippets" in entry and entry["evidence_snippets"] is not None:
            snips = entry["evidence_snippets"]
            if not isinstance(snips, list) or len(snips) > 5:
                raise ValueError(f"sec_nlp.tickers[{sym}].evidence_snippets must be a list of at most 5 strings.")
            clean_snips: List[str] = []
            for s_idx, snip in enumerate(snips):
                if not isinstance(snip, str):
                    raise ValueError(f"sec_nlp.tickers[{sym}].evidence_snippets[{s_idx}] must be a string.")
                clean_snips.append(snip.strip()[:240])
            clean_entry["evidence_snippets"] = clean_snips
        cleaned_tickers[sym] = clean_entry
    out["tickers"] = cleaned_tickers
    return out


def _validate_evidence_metrics_block(raw: Any, name: str) -> Dict[str, float]:
    if not isinstance(raw, dict):
        raise ValueError(f"backtest_evidence.{name} must be a dictionary.")
    bounds: Dict[str, Tuple[float, float]] = {
        "sharpe_ratio": (-10.0, 20.0),
        "sortino_ratio": (-10.0, 40.0),
        "max_drawdown_pct": (-100.0, 0.0),
        "hit_rate_pct": (0.0, 100.0),
        "profit_factor": (0.0, 100.0),
    }
    unknown = set(raw.keys()) - set(bounds.keys())
    if unknown:
        raise ValueError(f"Unknown key(s) in backtest_evidence.{name}: {sorted(unknown)}")
    missing = set(bounds.keys()) - set(raw.keys())
    if missing:
        raise ValueError(f"Missing required key(s) in backtest_evidence.{name}: {sorted(missing)}")
    out: Dict[str, float] = {}
    for k, (lo, hi) in bounds.items():
        if not _is_valid_number(raw[k], lo, hi):
            raise ValueError(f"backtest_evidence.{name}.{k} must be finite in [{lo}, {hi}].")
        out[k] = round(float(raw[k]), 4)
    return out


def validate_backtest_evidence_artifact(payload: Any) -> Dict[str, Any]:
    """Fail-closed validation for Schema v2.0 walk-forward Protocol 06 Evidence Pack (strictly read-only)."""
    extra = frozenset({
        "protocol_06_read_only",
        "folds",
        "universe_count",
        "friction_hurdle_pct",
        "baseline_metrics",
        "candidate_metrics",
        "stability_score",
        "proposed_rule_deltas",
    })
    out = _validate_v2_envelope(payload, extra)
    if payload.get("protocol_06_read_only") is not True:
        raise ValueError("backtest_evidence.protocol_06_read_only must be boolean True.")
    out["protocol_06_read_only"] = True

    if not _is_valid_int(payload.get("folds"), 1, 100):
        raise ValueError("backtest_evidence.folds must be an integer in [1, 100].")
    out["folds"] = int(payload["folds"])

    if not _is_valid_int(payload.get("universe_count"), 1, 2000):
        raise ValueError("backtest_evidence.universe_count must be an integer in [1, 2000].")
    out["universe_count"] = int(payload["universe_count"])

    if not _is_valid_number(payload.get("friction_hurdle_pct"), 0.0, 10.0):
        raise ValueError("backtest_evidence.friction_hurdle_pct must be finite in [0.0, 10.0].")
    out["friction_hurdle_pct"] = round(float(payload["friction_hurdle_pct"]), 4)

    if not _is_valid_number(payload.get("stability_score"), 0.0, 1.0):
        raise ValueError("backtest_evidence.stability_score must be finite in [0.0, 1.0].")
    out["stability_score"] = round(float(payload["stability_score"]), 4)

    out["baseline_metrics"] = _validate_evidence_metrics_block(payload.get("baseline_metrics"), "baseline_metrics")
    out["candidate_metrics"] = _validate_evidence_metrics_block(payload.get("candidate_metrics"), "candidate_metrics")

    raw_deltas = payload.get("proposed_rule_deltas")
    if not isinstance(raw_deltas, list) or len(raw_deltas) > 25:
        raise ValueError("backtest_evidence.proposed_rule_deltas must be a list of at most 25 entries.")
    allowed_delta_keys = {"rule_id", "parameter", "current_value", "proposed_value", "rationale"}
    cleaned_deltas: List[Dict[str, Any]] = []
    for idx, d in enumerate(raw_deltas):
        if not isinstance(d, dict):
            raise ValueError(f"backtest_evidence.proposed_rule_deltas[{idx}] must be a dictionary.")
        unknown = set(d.keys()) - allowed_delta_keys
        if unknown:
            raise ValueError(f"Unknown key(s) in proposed_rule_deltas[{idx}]: {sorted(unknown)}")
        missing = allowed_delta_keys - set(d.keys())
        if missing:
            raise ValueError(f"Missing key(s) in proposed_rule_deltas[{idx}]: {sorted(missing)}")
        for scalar_k in ("current_value", "proposed_value"):
            if isinstance(d[scalar_k], float) and not math.isfinite(d[scalar_k]):
                raise ValueError(f"proposed_rule_deltas[{idx}].{scalar_k} must be finite.")
        rule_id = str(d["rule_id"] or "").strip()
        param = str(d["parameter"] or "").strip()
        rationale = str(d["rationale"] or "").strip()
        if not rule_id or not param or not rationale:
            raise ValueError(f"proposed_rule_deltas[{idx}] requires non-empty rule_id, parameter, and rationale.")
        cleaned_deltas.append({
            "rule_id": rule_id[:64],
            "parameter": param[:64],
            "current_value": d["current_value"] if not isinstance(d["current_value"], str) else d["current_value"].strip()[:64],
            "proposed_value": d["proposed_value"] if not isinstance(d["proposed_value"], str) else d["proposed_value"].strip()[:64],
            "rationale": rationale[:300],
        })
    out["proposed_rule_deltas"] = (
        cleaned_deltas
        if rule_deltas_improve_baseline(out["baseline_metrics"], out["candidate_metrics"], out["stability_score"])
        else []
    )
    return out


_ARTIFACT_VALIDATORS = {
    "vol_surface": validate_vol_surface_artifact,
    "quant_scout": validate_quant_scout_artifact,
    "sec_nlp": validate_sec_nlp_artifact,
    "backtest_evidence": validate_backtest_evidence_artifact,
}


def get_artifact_cache_path(artifact_type: str, cache_dir: Optional[str] = None) -> str:
    if artifact_type not in ARTIFACT_REGISTRY:
        raise ValueError(f"Unknown Colab artifact type: {artifact_type!r}")
    fname, _ = ARTIFACT_REGISTRY[artifact_type]
    base_dir = cache_dir if cache_dir is not None else DEFAULT_CACHE_DIR
    return os.path.join(base_dir, fname)


def is_artifact_fresh(
    as_of_utc: Optional[str],
    ttl_hours: float,
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Evaluates UTC freshness against TTL hours without crashing on malformed timestamps."""
    ttl = float(ttl_hours)
    if not as_of_utc or not isinstance(as_of_utc, str):
        return {"fresh": False, "stale": True, "age_hours": None, "ttl_hours": ttl}
    try:
        parsed = datetime.fromisoformat(as_of_utc.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        ref = now_utc or datetime.now(timezone.utc)
        age_hours = max(0.0, (ref - parsed).total_seconds() / 3600.0)
        is_fresh = age_hours <= ttl
        return {
            "fresh": is_fresh,
            "stale": not is_fresh,
            "age_hours": round(age_hours, 2),
            "ttl_hours": ttl,
        }
    except Exception:
        return {"fresh": False, "stale": True, "age_hours": None, "ttl_hours": ttl}


def validate_colab_artifact(artifact_type: str, payload: Any) -> Dict[str, Any]:
    if artifact_type not in _ARTIFACT_VALIDATORS:
        raise ValueError(f"Unknown Colab artifact type: {artifact_type!r}")
    return _ARTIFACT_VALIDATORS[artifact_type](payload)


def import_colab_artifact(
    artifact_type: str,
    payload: Any,
    cache_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Fail-closed validation and atomic persistence for Schema v2.0 Colab artifacts."""
    validated = validate_colab_artifact(artifact_type, payload)
    validated["imported_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    target_path = get_artifact_cache_path(artifact_type, cache_dir=cache_dir)
    parent = os.path.dirname(os.path.abspath(target_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    atomic_write_json(target_path, validated, indent=2)

    try:
        abs_path = os.path.abspath(target_path)
        st = os.stat(abs_path)
        with _CACHE_LOCK:
            _CALIBRATION_CACHE[abs_path] = ((int(st.st_mtime_ns), int(st.st_size)), copy.deepcopy(validated))
    except Exception:
        pass

    item_count = (
        len(validated.get("tickers", {}))
        if "tickers" in validated
        else len(validated.get("candidates", validated.get("proposed_rule_deltas", [])))
    )
    return {
        "status": "success",
        "artifact_type": artifact_type,
        "schema_version": validated["schema_version"],
        "item_count": item_count,
        "generated_at_utc": validated.get("generated_at_utc"),
        "imported_at_utc": validated["imported_at_utc"],
        "path": target_path,
    }


def load_colab_artifact(
    artifact_type: str,
    cache_dir: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Loads and validates a Schema v2.0 Colab artifact with mtime caching; fails closed to None."""
    target_path = get_artifact_cache_path(artifact_type, cache_dir=cache_dir)
    if not target_path or not os.path.exists(target_path):
        return None
    try:
        abs_path = os.path.abspath(target_path)
        st = os.stat(abs_path)
        stat_key = (int(st.st_mtime_ns), int(st.st_size))
        with _CACHE_LOCK:
            hit = _CALIBRATION_CACHE.get(abs_path)
            if hit is not None and hit[0] == stat_key:
                return copy.deepcopy(hit[1])

        with open(abs_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        validated = validate_colab_artifact(artifact_type, raw)

        with _CACHE_LOCK:
            _CALIBRATION_CACHE[abs_path] = (stat_key, copy.deepcopy(validated))
        return validated
    except Exception:
        return None


def get_artifact_status(
    artifact_type: str,
    cache_dir: Optional[str] = None,
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    if artifact_type not in ARTIFACT_REGISTRY:
        raise ValueError(f"Unknown Colab artifact type: {artifact_type!r}")
    _, ttl_hours = ARTIFACT_REGISTRY[artifact_type]
    data = load_colab_artifact(artifact_type, cache_dir=cache_dir)
    if not data:
        return {
            "artifact_type": artifact_type,
            "loaded": False,
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "freshness": is_artifact_fresh(None, ttl_hours, now_utc=now_utc),
            "generated_at_utc": None,
            "imported_at_utc": None,
            "source": None,
            "item_count": 0,
        }
    freshness = is_artifact_fresh(data.get("generated_at_utc"), ttl_hours, now_utc=now_utc)
    item_count = (
        len(data.get("tickers", {}))
        if "tickers" in data
        else len(data.get("candidates", data.get("proposed_rule_deltas", [])))
    )
    return {
        "artifact_type": artifact_type,
        "loaded": True,
        "schema_version": data.get("schema_version"),
        "freshness": freshness,
        "generated_at_utc": data.get("generated_at_utc"),
        "imported_at_utc": data.get("imported_at_utc"),
        "source": data.get("source"),
        "item_count": item_count,
    }


def get_all_colab_artifacts_status(
    cache_dir: Optional[str] = None,
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    cal_path = os.path.join(cache_dir, "colab_calibration.json") if cache_dir else DEFAULT_CALIBRATION_PATH
    artifacts = {
        art_type: get_artifact_status(art_type, cache_dir=cache_dir, now_utc=now_utc)
        for art_type in ARTIFACT_REGISTRY
    }
    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "artifacts": artifacts,
        "calibration_v1": get_calibration_status(calibration_path=cal_path),
    }


def _cli(argv: List[str]) -> int:
    if len(argv) < 2 or argv[1] not in ("export", "import", "status"):
        print("Usage: python -m colab_calibration [export <out.json>|import <in.json>|status]")
        return 1
    cmd = argv[1]
    if cmd == "export":
        out_path = argv[2] if len(argv) > 2 else os.path.join("cache", "gem_colab_export.json")
        bundle = export_colab_bundle()
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        atomic_write_json(out_path, bundle, indent=2)
        print(f"Exported Colab bundle ({len(bundle.get('symbols', []))} symbols) to {out_path}")
        return 0
    if cmd == "import":
        if len(argv) < 3 or not os.path.exists(argv[2]):
            print("Specify an existing calibration JSON file to import.")
            return 1
        with open(argv[2], "r", encoding="utf-8") as f:
            payload = json.load(f)
        res = import_colab_calibration(payload)
        print(json.dumps(res, indent=2))
        return 0
    print(json.dumps(get_calibration_status(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
