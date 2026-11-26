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


CALIBRATION_SCHEMA_VERSION = "1.0"
DEFAULT_CALIBRATION_PATH = os.path.join("cache", "colab_calibration.json")
DEFAULT_DAILY_CACHE_PATH = os.path.join("cache", "daily_history_cache.json")
DEFAULT_PEAK_CACHE_PATH = os.path.join("cache", "peak_profiles_cache.json")

_TICKER_RE = re.compile(r"^[A-Z0-9.\-\^]{1,12}$")
_HHMM_RE = re.compile(r"^\d{2}:\d{2}$")

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
