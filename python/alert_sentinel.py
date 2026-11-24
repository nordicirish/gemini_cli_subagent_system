"""Low-Power Free-Tier Gemini Trigger Sentinel with Automatic Model Fallback.

Runs opt-in, market-hours & event-gated passes over public market technicals
only (strictly excluding portfolio holdings, shares, WAC, and cash) to arm or
adjust `source="sentinel"` key-level triggers in AlertMonitor.
Supports automatic fallback across Gemini Free Tier models on 404/429/5xx errors
with per-model cooldown/exponential backoff and optional Antigravity fallback.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from alert_monitor import ALERT_MONITOR, AlertMonitor, _safe_float
except ImportError:
    from python.alert_monitor import ALERT_MONITOR, AlertMonitor, _safe_float

DEFAULT_GEMINI_FREE_MODEL_CHAIN: List[str] = [
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash",
]
DEFAULT_ANTIGRAVITY_FALLBACK_MODEL = "gemini-3.8-flash-low"
DEFAULT_SENTINEL_CONFIG_PATH = os.path.join("cache", "alert_sentinel_config.json")

VALID_PROVIDERS = {"gemini_free", "auto", "antigravity"}
DEFAULT_MOVE_PCT_THRESHOLD = 0.5


def _utc_iso(ts: Optional[float] = None) -> str:
    val = time.time() if ts is None else float(ts)
    return datetime.fromtimestamp(val, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_gemini_api_key(env_path: str = ".env") -> Optional[str]:
    """Reads GEMINI_API_KEY / GOOGLE_API_KEY from env or gitignored .env without logging it."""
    for var_name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        val = os.environ.get(var_name, "").strip()
        if val:
            return val
    if env_path and os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    key_name = k.strip()
                    if key_name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
                        secret = v.strip().strip('"').strip("'")
                        if secret:
                            return secret
        except Exception:
            return None
    return None


def _classify_provider_error(exc: Exception) -> str:
    """Classifies model invocation errors for automatic fallback & cooldown scheduling."""
    msg = str(exc or "").lower()
    if any(tok in msg for tok in ("401", "403", "api key not valid", "api_key_invalid", "permission_denied", "unauthenticated")):
        return "auth_error"
    if any(tok in msg for tok in ("404", "not found", "is not found", "not supported", "deprecated", "invalid model", "does not exist")):
        return "not_found"
    if any(tok in msg for tok in ("429", "resource_exhausted", "quota", "rate limit", "too many requests")):
        return "rate_limited"
    return "transient"


def build_public_technicals_payload(
    snapshot: Dict[str, Any],
    existing_sentinel_rules: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Builds a strictly sanitized public-technicals payload for Gemini Free Tier.

    Excludes portfolio_snapshot, shares, wac, cash balances, local_storage_state,
    and trade_lessons to uphold confidentiality on Free Tier endpoints.
    """
    raw_tickers = snapshot.get("tickers", []) if isinstance(snapshot, dict) else []
    public_tickers: List[Dict[str, Any]] = []
    macro_tickers: List[Dict[str, Any]] = []

    for item in raw_tickers:
        if not isinstance(item, dict):
            continue
        sym = str(item.get("ticker") or "").strip().upper()
        if not sym or item.get("price_validity") != "VALID":
            continue
        price = _safe_float(item.get("price"))
        if price is None or price <= 0:
            continue

        fib = item.get("fib_forecast") if isinstance(item.get("fib_forecast"), dict) else {}
        compact = {
            "ticker": sym,
            "price": round(price, 4),
            "chg_pct": _safe_float(item.get("session_change_pct")),
            "vwap": _safe_float(item.get("vwap")),
            "rsi": _safe_float(item.get("rsi")),
            "rvol": _safe_float(item.get("rvol")),
            "trend": item.get("trend"),
            "macd_status": item.get("macd_status"),
            "gex": _safe_float(item.get("net_gex_total")),
            "gamma_flip": _safe_float(item.get("gamma_flip_price")),
            "fib": {
                "mode": fib.get("mode"),
                "low": fib.get("pA"),
                "hod": fib.get("pB"),
                "next_res": fib.get("next_resistance"),
                "peak_cap": fib.get("daily_peak_target"),
                "status": fib.get("daily_peak_status"),
            },
        }
        if sym.startswith("^") or sym in ("VIXY", "IEF", "UUP", "SPY", "QQQ"):
            macro_tickers.append(compact)
        else:
            public_tickers.append(compact)

    return {
        "market_status": snapshot.get("status", "UNKNOWN") if isinstance(snapshot, dict) else "UNKNOWN",
        "tickers": public_tickers[:15],
        "macro": macro_tickers[:8],
        "active_sentinel_rules": [
            {
                "ticker": r.get("ticker"),
                "metric": r.get("metric"),
                "op": r.get("op"),
                "target": r.get("target"),
                "target_ref": r.get("target_ref"),
                "label": r.get("label"),
            }
            for r in (existing_sentinel_rules or [])
        ],
    }


def _extract_sentinel_rules_from_response(raw_text: str) -> List[Dict[str, Any]]:
    text = (raw_text or "").strip()
    if not text:
        raise ValueError("Empty response from Sentinel model")
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    parsed = json.loads(text)
    if isinstance(parsed, list):
        return [x for x in parsed if isinstance(x, dict)]
    if isinstance(parsed, dict):
        for key in ("triggers", "alert_triggers", "rules"):
            if isinstance(parsed.get(key), list):
                return [x for x in parsed[key] if isinstance(x, dict)]
    raise ValueError("Sentinel JSON missing 'triggers' list")


def _default_invoke_gemini(model_name: str, prompt: str, api_key: str) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1,
            max_output_tokens=1024,
        ),
    )
    return getattr(resp, "text", "") or ""


def _default_invoke_antigravity(model_name: str, prompt: str) -> str:
    try:
        from agent_framework import AntigravityAppAgent
    except ImportError:
        from python.agent_framework import AntigravityAppAgent

    agent = AntigravityAppAgent(model_name=model_name)
    return agent.run_prompt(prompt) or ""


class AlertSentinel:
    """Opt-in low-power LLM daemon for adjusting key-level technical triggers."""

    def __init__(
        self,
        alert_monitor: Optional[AlertMonitor] = None,
        config_path: str = DEFAULT_SENTINEL_CONFIG_PATH,
        env_path: str = ".env",
        gemini_invoker: Optional[Callable[[str, str, str], str]] = None,
        antigravity_invoker: Optional[Callable[[str, str], str]] = None,
    ):
        self.alert_monitor = alert_monitor or ALERT_MONITOR
        self.config_path = config_path
        self.env_path = env_path
        self._gemini_invoker = gemini_invoker or _default_invoke_gemini
        self._antigravity_invoker = antigravity_invoker or _default_invoke_antigravity
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._snapshot_provider: Optional[Callable[[], Dict[str, Any]]] = None
        self._log_callback: Optional[Callable[[str], None]] = None

        env_models = [
            m.strip() for m in os.environ.get("SENTINEL_GEMINI_MODELS", "").split(",") if m.strip()
        ]
        self._config: Dict[str, Any] = {
            "enabled": False,
            "provider": "gemini_free",
            "interval_sec": 600,
            "market_hours_only": True,
            "model_chain": env_models or list(DEFAULT_GEMINI_FREE_MODEL_CHAIN),
            "antigravity_model": DEFAULT_ANTIGRAVITY_FALLBACK_MODEL,
        }
        self._model_health: Dict[str, Dict[str, Any]] = {}
        self._last_fingerprints: Dict[str, Tuple[Any, ...]] = {}
        self._last_run_ts: Optional[float] = None
        self._last_status: Dict[str, Any] = {
            "status": "IDLE",
            "model_used": None,
            "provider_used": None,
            "fallback_count": 0,
            "armed_count": 0,
            "skipped_reason": None,
            "error": None,
            "last_run_at_utc": None,
        }
        self._load_config()

    def set_snapshot_provider(self, fn: Callable[[], Dict[str, Any]]) -> None:
        self._snapshot_provider = fn

    def set_log_callback(self, fn: Optional[Callable[[str], None]]) -> None:
        self._log_callback = fn

    def _log(self, msg: str) -> None:
        if self._log_callback:
            try:
                self._log_callback(msg)
                return
            except Exception:
                pass
        print(msg)

    def _load_config(self) -> None:
        if not os.path.exists(self.config_path):
            return
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                self.update_config(saved, persist=False)
        except Exception:
            pass

    def _persist_config(self) -> None:
        parent = os.path.dirname(os.path.abspath(self.config_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp_path = f"{self.config_path}.tmp.{os.getpid()}.{threading.get_ident()}"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self._config, f, indent=2)
        os.replace(tmp_path, self.config_path)

    def update_config(self, updates: Dict[str, Any], persist: bool = True) -> Dict[str, Any]:
        if not isinstance(updates, dict):
            return self.get_status()
        with self._lock:
            if "enabled" in updates:
                self._config["enabled"] = bool(updates["enabled"])
            if "provider" in updates:
                prov = str(updates["provider"]).strip().lower()
                if prov in VALID_PROVIDERS:
                    self._config["provider"] = prov
            if "interval_sec" in updates:
                iv = _safe_float(updates["interval_sec"])
                if iv is not None:
                    self._config["interval_sec"] = max(60, min(3600, int(iv)))
            if "market_hours_only" in updates:
                self._config["market_hours_only"] = bool(updates["market_hours_only"])
            if "model_chain" in updates and isinstance(updates["model_chain"], list):
                cleaned = [str(m).strip() for m in updates["model_chain"] if str(m).strip()]
                if cleaned:
                    self._config["model_chain"] = cleaned[:8]
            if "antigravity_model" in updates and str(updates["antigravity_model"]).strip():
                self._config["antigravity_model"] = str(updates["antigravity_model"]).strip()
            if persist:
                self._persist_config()
            return dict(self._config)

    def _has_structural_change(self, public_payload: Dict[str, Any], has_sentinel_rules: bool) -> bool:
        if not has_sentinel_rules:
            return True
        tickers = public_payload.get("tickers", [])
        if not tickers:
            return False

        changed = False
        new_fps: Dict[str, Tuple[Any, ...]] = {}
        for item in tickers:
            sym = item["ticker"]
            price = item["price"]
            vwap = item.get("vwap") or 0.0
            above_vwap = bool(vwap > 0 and price >= vwap)
            fib = item.get("fib") or {}
            fp = (
                price,
                above_vwap,
                item.get("trend"),
                fib.get("mode"),
                fib.get("status"),
            )
            new_fps[sym] = fp
            old_fp = self._last_fingerprints.get(sym)
            if old_fp is None:
                changed = True
                continue
            old_price = float(old_fp[0] or 0.0)
            pct_move = abs(price - old_price) / old_price * 100.0 if old_price > 0 else 0.0
            if pct_move >= DEFAULT_MOVE_PCT_THRESHOLD or fp[1:] != old_fp[1:]:
                changed = True

        if changed:
            self._last_fingerprints = new_fps
        return changed

    @staticmethod
    def _build_prompt(public_payload: Dict[str, Any]) -> str:
        return (
            "You are a concise intraday technical trigger tuner. "
            "Given ONLY public market technicals below, return JSON with key 'triggers' containing 1 to 6 "
            "actionable key-level alerts. Allowed metrics: price, rsi, rvol, macd_hist, session_change_pct. "
            "Allowed ops: >=, <=, crosses_above, crosses_below. Allowed target_ref: vwap, hod, fib_next, fib_cap, gamma_flip. "
            "Each item: {\"ticker\": str, \"metric\": \"price\", \"op\": \">=\"|\"<=\"|\"crosses_above\"|\"crosses_below\", "
            "\"target\": number (or omit if using target_ref), \"target_ref\": optional str, "
            "\"confirm\": {\"rvol_gte\": number, \"above_vwap\": bool}, "
            "\"severity\": \"info\"|\"warning\"|\"critical\", \"label\": short str, \"one_shot\": true}.\n\n"
            f"MARKET_TECHNICALS_JSON:\n{json.dumps(public_payload, separators=(',', ':'))}"
        )

    def _is_in_cooldown(self, model_name: str, now_ts: float) -> bool:
        rec = self._model_health.get(model_name)
        if not rec:
            return False
        return now_ts < float(rec.get("cooldown_until", 0.0))

    def _record_failure(self, model_name: str, kind: str, exc: Exception, now_ts: float) -> None:
        rec = self._model_health.setdefault(model_name, {"failures": 0})
        failures = int(rec.get("failures", 0)) + 1
        if kind == "not_found":
            cooldown = 21600
        elif kind == "rate_limited":
            schedule = (120, 300, 900, 1800)
            cooldown = schedule[min(failures - 1, len(schedule) - 1)]
        elif kind == "auth_error":
            cooldown = 3600
        else:
            cooldown = 180

        rec.update({
            "failures": failures,
            "last_error_kind": kind,
            "last_error": str(exc)[:180],
            "cooldown_until": now_ts + cooldown,
            "last_checked_utc": _utc_iso(now_ts),
        })

    def _record_success(self, model_name: str, now_ts: float) -> None:
        self._model_health[model_name] = {
            "failures": 0,
            "last_error_kind": None,
            "last_error": None,
            "cooldown_until": 0.0,
            "last_checked_utc": _utc_iso(now_ts),
        }

    def run_once(
        self,
        snapshot: Optional[Dict[str, Any]] = None,
        force: bool = False,
        now_ts: Optional[float] = None,
    ) -> Dict[str, Any]:
        ts = time.time() if now_ts is None else float(now_ts)
        snap = snapshot if snapshot is not None else (self._snapshot_provider() if self._snapshot_provider else {})
        if not isinstance(snap, dict) or not snap.get("tickers"):
            return self._finish_status("SKIPPED", skipped_reason="no_market_data", now_ts=ts)

        with self._lock:
            cfg = dict(self._config)

        market_status = str(snap.get("status") or "UNKNOWN").upper()
        if not force and cfg["market_hours_only"] and market_status not in ("OPEN", "PRE-MARKET"):
            return self._finish_status("SKIPPED", skipped_reason=f"market_{market_status.lower()}", now_ts=ts)

        dash_alerts = self.alert_monitor.get_dashboard_payload()
        sentinel_rules = [r for r in dash_alerts.get("rules", []) if r.get("source") == "sentinel"]
        public_payload = build_public_technicals_payload(snap, sentinel_rules)
        if not public_payload["tickers"]:
            return self._finish_status("SKIPPED", skipped_reason="no_valid_tickers", now_ts=ts)

        if not force and not self._has_structural_change(public_payload, bool(sentinel_rules)):
            return self._finish_status("SKIPPED", skipped_reason="no_structural_change", now_ts=ts)

        prompt = self._build_prompt(public_payload)
        provider_pref = cfg["provider"]
        api_key = _load_gemini_api_key(self.env_path)

        raw_response: Optional[str] = None
        model_used: Optional[str] = None
        provider_used: Optional[str] = None
        fallback_count = 0
        last_err: Optional[str] = None
        auth_failed = False

        # 1. Try Gemini Free Tier model chain if requested or auto
        if provider_pref in ("gemini_free", "auto"):
            if not api_key:
                last_err = "GEMINI_API_KEY not configured"
                if provider_pref == "gemini_free":
                    return self._finish_status("DEGRADED", error=last_err, now_ts=ts)
            else:
                chain = list(cfg["model_chain"])
                eligible = [m for m in chain if not self._is_in_cooldown(m, ts)]
                if not eligible:
                    # Wait complete? If every model is in cooldown, probe least-recently-failed when forced
                    eligible = chain if force else []
                for idx, candidate in enumerate(eligible):
                    try:
                        raw_response = self._gemini_invoker(candidate, prompt, api_key)
                        self._record_success(candidate, ts)
                        model_used = candidate
                        provider_used = "gemini_free"
                        break
                    except Exception as exc:
                        kind = _classify_provider_error(exc)
                        self._record_failure(candidate, kind, exc, ts)
                        last_err = f"{candidate} ({kind}): {str(exc)[:120]}"
                        self._log(f"[AlertSentinel] Fallback from {candidate} ({kind})")
                        if kind == "auth_error":
                            auth_failed = True
                            break
                        fallback_count += 1

        # 2. Fallback or explicit Antigravity local model
        if raw_response is None and (provider_pref == "antigravity" or provider_pref == "auto"):
            ag_model = cfg["antigravity_model"]
            try:
                if provider_pref == "auto" and (not api_key or auth_failed or last_err):
                    fallback_count += 1
                raw_response = self._antigravity_invoker(ag_model, prompt)
                self._record_success(ag_model, ts)
                model_used = ag_model
                provider_used = "antigravity"
            except Exception as exc:
                self._record_failure(ag_model, _classify_provider_error(exc), exc, ts)
                last_err = f"{ag_model}: {str(exc)[:120]}"

        if raw_response is None:
            return self._finish_status(
                "DEGRADED",
                fallback_count=fallback_count,
                error=last_err or "All sentinel models in cooldown or unavailable",
                now_ts=ts,
            )

        try:
            parsed_triggers = _extract_sentinel_rules_from_response(raw_response)
        except Exception as parse_err:
            return self._finish_status(
                "DEGRADED",
                model_used=model_used,
                provider_used=provider_used,
                fallback_count=fallback_count,
                error=f"Invalid Sentinel JSON: {parse_err}",
                now_ts=ts,
            )

        valid_symbols = {t["ticker"] for t in public_payload["tickers"]} | {t["ticker"] for t in public_payload["macro"]}
        scoped_triggers = [t for t in parsed_triggers if str(t.get("ticker") or "").strip().upper() in valid_symbols]
        arm_outcome = self.alert_monitor.upsert_rules(scoped_triggers, source="sentinel", replace_source=True)
        self._last_run_ts = ts
        self._log(
            f"[AlertSentinel] {provider_used}/{model_used} armed {len(arm_outcome['armed'])} trigger(s)"
            + (f" (fallbacks={fallback_count})" if fallback_count else "")
        )
        return self._finish_status(
            "OK",
            model_used=model_used,
            provider_used=provider_used,
            fallback_count=fallback_count,
            armed_count=len(arm_outcome["armed"]),
            now_ts=ts,
        )

    def _finish_status(
        self,
        status: str,
        model_used: Optional[str] = None,
        provider_used: Optional[str] = None,
        fallback_count: int = 0,
        armed_count: int = 0,
        skipped_reason: Optional[str] = None,
        error: Optional[str] = None,
        now_ts: Optional[float] = None,
    ) -> Dict[str, Any]:
        ts = time.time() if now_ts is None else float(now_ts)
        res = {
            "status": status,
            "model_used": model_used,
            "provider_used": provider_used,
            "fallback_count": fallback_count,
            "armed_count": armed_count,
            "skipped_reason": skipped_reason,
            "error": error,
            "last_run_at_utc": _utc_iso(ts),
        }
        with self._lock:
            self._last_status = dict(res)
            self._last_run_ts = ts
        return res

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            api_key_configured = bool(_load_gemini_api_key(self.env_path))
            return {
                "enabled": bool(self._config["enabled"]),
                "provider": self._config["provider"],
                "interval_sec": int(self._config["interval_sec"]),
                "market_hours_only": bool(self._config["market_hours_only"]),
                "model_chain": list(self._config["model_chain"]),
                "antigravity_model": self._config["antigravity_model"],
                "api_key_configured": api_key_configured,
                "model_health": {k: dict(v) for k, v in self._model_health.items()},
                "last_run": dict(self._last_status),
            }

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True, name="AlertSentinelWorker")
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _loop(self) -> None:
        while not self._stop_event.wait(5.0):
            try:
                with self._lock:
                    enabled = bool(self._config["enabled"])
                    interval_sec = int(self._config["interval_sec"])
                    last_ts = self._last_run_ts
                if not enabled:
                    continue
                now_ts = time.time()
                if last_ts is not None and (now_ts - last_ts) < interval_sec:
                    continue
                self.run_once(now_ts=now_ts)
            except Exception as exc:
                self._log(f"[AlertSentinel] Worker iteration error: {exc}")


ALERT_SENTINEL = AlertSentinel(alert_monitor=ALERT_MONITOR)
