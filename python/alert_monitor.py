"""Deterministic Tick-Level Key-Level Alert Monitor.

Evaluates armed technical/key-level triggers against live scan snapshots at zero
LLM token cost, persists rules/events atomically under cache/, and auto-extracts
structured or explicit key levels from AI Council responses.
Never mutates authoritative portfolio SSoT or trading rules.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
import re
import threading
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

VALID_METRICS: Set[str] = {
    "price",
    "rsi",
    "rvol",
    "macd_hist",
    "session_change_pct",
    "distance_from_vwap",
    "net_gex_total",
}
VALID_OPS: Set[str] = {">=", "<=", "crosses_above", "crosses_below"}
VALID_TARGET_REFS: Set[str] = {"vwap", "hod", "fib_next", "fib_cap", "gamma_flip"}
VALID_SEVERITIES: Set[str] = {"critical", "warning", "info"}
VALID_SOURCES: Set[str] = {"manual", "council", "sentinel"}

MAX_RULES_PER_TICKER = 6
MAX_TOTAL_RULES = 40
MAX_EVENTS = 50
DEFAULT_STATE_PATH = os.path.join("cache", "alert_triggers.json")


def _utc_now_iso(now_ts: Optional[float] = None) -> str:
    ts = time.time() if now_ts is None else float(now_ts)
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(num):
        return None
    return num


def _normalize_ticker(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    clean = raw.strip().upper()
    if not re.match(r"^[\^A-Z0-9.\-]{1,12}$", clean):
        return ""
    return clean


def _normalize_rule_input(raw: Dict[str, Any], source: str = "manual") -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Alert rule must be a JSON object")

    ticker = _normalize_ticker(raw.get("ticker"))
    if not ticker:
        raise ValueError("Alert rule missing valid ticker symbol")

    metric = str(raw.get("metric") or "price").strip().lower()
    if metric not in VALID_METRICS:
        raise ValueError(f"Unsupported metric '{metric}'. Allowed: {sorted(VALID_METRICS)}")

    op = str(raw.get("op") or ">=").strip().lower()
    if op not in VALID_OPS:
        raise ValueError(f"Unsupported operator '{op}'. Allowed: {sorted(VALID_OPS)}")

    target_ref_raw = raw.get("target_ref")
    target_ref: Optional[str] = None
    if target_ref_raw is not None and str(target_ref_raw).strip():
        candidate_ref = str(target_ref_raw).strip().lower()
        if candidate_ref not in VALID_TARGET_REFS:
            raise ValueError(f"Unsupported target_ref '{candidate_ref}'. Allowed: {sorted(VALID_TARGET_REFS)}")
        target_ref = candidate_ref

    target = _safe_float(raw.get("target"))
    if target_ref is None and target is None:
        raise ValueError("Alert rule requires numeric 'target' or valid 'target_ref'")

    severity = str(raw.get("severity") or "info").strip().lower()
    if severity not in VALID_SEVERITIES:
        severity = "info"

    norm_source = str(source or raw.get("source") or "manual").strip().lower()
    if norm_source not in VALID_SOURCES:
        norm_source = "manual"

    confirm_raw = raw.get("confirm")
    confirm: Dict[str, Any] = {}
    if isinstance(confirm_raw, dict):
        if _safe_float(confirm_raw.get("rvol_gte")) is not None:
            confirm["rvol_gte"] = float(confirm_raw["rvol_gte"])
        if _safe_float(confirm_raw.get("rvol_lte")) is not None:
            confirm["rvol_lte"] = float(confirm_raw["rvol_lte"])
        if confirm_raw.get("above_vwap") is True:
            confirm["above_vwap"] = True
        if confirm_raw.get("below_vwap") is True:
            confirm["below_vwap"] = True
        trend_in = confirm_raw.get("trend_in")
        if isinstance(trend_in, list) and trend_in:
            confirm["trend_in"] = [str(t).strip().upper() for t in trend_in if str(t).strip()]

    one_shot = bool(raw.get("one_shot", True))
    cooldown_sec = _safe_float(raw.get("cooldown_sec"))
    cooldown_val = max(0, int(cooldown_sec)) if cooldown_sec is not None else 300

    if target_ref:
        default_label = f"{metric} {op} {target_ref.upper()}"
    else:
        default_label = f"{ticker} {metric} {op} {target:.2f}"
    label = str(raw.get("label") or default_label).strip()[:120]

    dedupe_target = target_ref if target_ref else f"{round(float(target), 2):.2f}"
    dedupe_key = f"{ticker}|{metric}|{op}|{dedupe_target}"

    return {
        "rule_id": str(raw.get("rule_id") or "").strip() or f"trg-{uuid.uuid4().hex[:10]}",
        "ticker": ticker,
        "metric": metric,
        "op": op,
        "target": round(float(target), 4) if target is not None else None,
        "target_ref": target_ref,
        "confirm": confirm,
        "label": label,
        "severity": severity,
        "source": norm_source,
        "enabled": bool(raw.get("enabled", True)),
        "one_shot": one_shot,
        "cooldown_sec": cooldown_val,
        "dedupe_key": dedupe_key,
        "last_value": _safe_float(raw.get("last_value")),
        "last_triggered_ts": _safe_float(raw.get("last_triggered_ts")),
        "last_triggered_at_utc": raw.get("last_triggered_at_utc"),
        "trigger_count": int(raw.get("trigger_count", 0) or 0),
        "created_at_utc": raw.get("created_at_utc") or _utc_now_iso(),
    }


def _atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = f"{path}.tmp.{os.getpid()}.{threading.get_ident()}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp_path, path)


_SEVERITY_RANK: Dict[str, int] = {"critical": 3, "warning": 2, "info": 1}


def _dedupe_unacknowledged_by_ticker(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse unacknowledged events to at most one per ticker (highest severity, newest on tie)."""
    order: List[str] = []
    winners: Dict[str, tuple[int, Dict[str, Any]]] = {}
    for idx, ev in enumerate(events):
        if ev.get("acknowledged"):
            continue
        ticker = str(ev.get("ticker") or "").strip().upper()
        key = ticker if ticker else f"__event__:{ev.get('event_id') or idx}"
        rank = _SEVERITY_RANK.get(str(ev.get("severity") or "info").lower(), 1)
        prev = winners.get(key)
        if prev is None:
            order.append(key)
            winners[key] = (rank, dict(ev))
        elif rank > prev[0]:
            winners[key] = (rank, dict(ev))
    return [winners[k][1] for k in order]


class AlertMonitor:
    """Thread-safe key-level alert rule store and tick evaluator."""

    def __init__(self, state_path: str = DEFAULT_STATE_PATH):
        self.state_path = state_path
        self._lock = threading.RLock()
        self._rules: List[Dict[str, Any]] = []
        self._events: List[Dict[str, Any]] = []
        self._mandate_cooldowns: Dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        with self._lock:
            if not os.path.exists(self.state_path):
                self._rules = []
                self._events = []
                return
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    raw_rules = data.get("rules", [])
                    loaded_rules = []
                    if isinstance(raw_rules, list):
                        for item in raw_rules:
                            try:
                                loaded_rules.append(_normalize_rule_input(item, source=item.get("source", "manual")))
                            except Exception:
                                continue
                    self._rules = loaded_rules[:MAX_TOTAL_RULES]
                    raw_events = data.get("events", [])
                    self._events = [e for e in raw_events if isinstance(e, dict)][:MAX_EVENTS] if isinstance(raw_events, list) else []
            except Exception:
                self._rules = []
                self._events = []

    def _persist(self) -> None:
        _atomic_write_json(
            self.state_path,
            {
                "version": 1,
                "updated_at_utc": _utc_now_iso(),
                "rules": self._rules,
                "events": self._events[:MAX_EVENTS],
            },
        )

    def add_rule(self, rule_dict: Dict[str, Any], source: str = "manual") -> Dict[str, Any]:
        norm = _normalize_rule_input(rule_dict, source=source)
        with self._lock:
            for idx, existing in enumerate(self._rules):
                if existing.get("dedupe_key") == norm["dedupe_key"]:
                    # Preserve id/history when re-arming identical rule
                    merged = dict(existing)
                    merged.update({
                        "label": norm["label"],
                        "severity": norm["severity"],
                        "confirm": norm["confirm"],
                        "enabled": True,
                        "one_shot": norm["one_shot"],
                        "cooldown_sec": norm["cooldown_sec"],
                        "source": norm["source"],
                    })
                    self._rules[idx] = merged
                    self._persist()
                    return dict(merged)

            ticker_count = sum(1 for r in self._rules if r.get("ticker") == norm["ticker"])
            if ticker_count >= MAX_RULES_PER_TICKER:
                raise ValueError(f"Maximum {MAX_RULES_PER_TICKER} alert triggers reached for {norm['ticker']}")
            if len(self._rules) >= MAX_TOTAL_RULES:
                raise ValueError(f"Maximum total alert triggers ({MAX_TOTAL_RULES}) reached")

            self._rules.append(norm)
            self._persist()
            return dict(norm)

    def upsert_rules(
        self,
        rules: List[Dict[str, Any]],
        source: str = "council",
        replace_source: bool = False,
    ) -> Dict[str, Any]:
        norm_source = str(source or "council").strip().lower()
        if norm_source not in VALID_SOURCES:
            norm_source = "council"

        valid_incoming: List[Dict[str, Any]] = []
        for item in rules or []:
            try:
                valid_incoming.append(_normalize_rule_input(item, source=norm_source))
            except Exception:
                continue

        armed: List[Dict[str, Any]] = []
        removed = 0

        with self._lock:
            existing_by_dedupe: Dict[str, Dict[str, Any]] = {
                r["dedupe_key"]: r for r in self._rules if r.get("dedupe_key")
            }

            if replace_source:
                incoming_keys = {r["dedupe_key"] for r in valid_incoming}
                kept_rules: List[Dict[str, Any]] = []
                for r in self._rules:
                    if r.get("source") == norm_source and r.get("dedupe_key") not in incoming_keys:
                        removed += 1
                    else:
                        kept_rules.append(r)
                self._rules = kept_rules
                existing_by_dedupe = {
                    r["dedupe_key"]: r for r in self._rules if r.get("dedupe_key")
                }

            for norm in valid_incoming:
                dkey = norm["dedupe_key"]
                if dkey in existing_by_dedupe:
                    existing = existing_by_dedupe[dkey]
                    existing.update({
                        "label": norm["label"],
                        "severity": norm["severity"],
                        "confirm": norm["confirm"],
                        "enabled": True if not replace_source else existing.get("enabled", True),
                        "one_shot": norm["one_shot"],
                        "cooldown_sec": norm["cooldown_sec"],
                    })
                    armed.append(dict(existing))
                    continue

                ticker_count = sum(1 for r in self._rules if r.get("ticker") == norm["ticker"])
                if ticker_count >= MAX_RULES_PER_TICKER or len(self._rules) >= MAX_TOTAL_RULES:
                    continue

                self._rules.append(norm)
                existing_by_dedupe[dkey] = norm
                armed.append(dict(norm))

            self._persist()
            total_rules = len(self._rules)

        return {"armed": armed, "removed": removed, "total_rules": total_rules}

    def delete_rule(self, rule_id: str) -> bool:
        with self._lock:
            before = len(self._rules)
            self._rules = [r for r in self._rules if r.get("rule_id") != rule_id]
            changed = len(self._rules) < before
            if changed:
                self._persist()
            return changed

    def toggle_rule(self, rule_id: str, enabled: Optional[bool] = None) -> Optional[Dict[str, Any]]:
        with self._lock:
            for r in self._rules:
                if r.get("rule_id") == rule_id:
                    r["enabled"] = (not bool(r.get("enabled", True))) if enabled is None else bool(enabled)
                    self._persist()
                    return dict(r)
            return None

    def acknowledge_event(self, event_id: str) -> bool:
        with self._lock:
            target_ev = None
            for ev in self._events:
                if ev.get("event_id") == event_id:
                    target_ev = ev
                    break
            if target_ev is None:
                return False

            target_ticker = str(target_ev.get("ticker") or "").strip().upper()
            changed = False
            if target_ticker:
                for ev in self._events:
                    ev_ticker = str(ev.get("ticker") or "").strip().upper()
                    if ev_ticker == target_ticker and not ev.get("acknowledged"):
                        ev["acknowledged"] = True
                        changed = True
            elif not target_ev.get("acknowledged"):
                target_ev["acknowledged"] = True
                changed = True

            if changed:
                self._persist()
            return True

    def clear_events(self) -> int:
        with self._lock:
            changed = 0
            for ev in self._events:
                if not ev.get("acknowledged"):
                    ev["acknowledged"] = True
                    changed += 1
            if changed:
                self._persist()
            return changed

    @staticmethod
    def _resolve_target_value(rule: Dict[str, Any], row: Dict[str, Any]) -> Optional[float]:
        ref = rule.get("target_ref")
        if ref:
            fib = row.get("fib_forecast") if isinstance(row.get("fib_forecast"), dict) else {}
            if ref == "vwap":
                val = _safe_float(row.get("vwap"))
            elif ref == "hod":
                val = _safe_float(fib.get("pB"))
            elif ref == "fib_next":
                val = _safe_float(fib.get("next_resistance"))
            elif ref == "fib_cap":
                val = _safe_float(fib.get("daily_peak_target"))
            elif ref == "gamma_flip":
                val = _safe_float(row.get("gamma_flip_price"))
            else:
                val = None
            if val is None or val <= 0:
                return None
            return val
        return _safe_float(rule.get("target"))

    @staticmethod
    def _check_confirm(confirm: Dict[str, Any], row: Dict[str, Any], price: float) -> bool:
        if not confirm:
            return True
        rvol = _safe_float(row.get("rvol"))
        if "rvol_gte" in confirm and (rvol is None or rvol < float(confirm["rvol_gte"])):
            return False
        if "rvol_lte" in confirm and (rvol is None or rvol > float(confirm["rvol_lte"])):
            return False
        vwap = _safe_float(row.get("vwap"))
        if confirm.get("above_vwap") and (vwap is None or vwap <= 0 or price < vwap):
            return False
        if confirm.get("below_vwap") and (vwap is None or vwap <= 0 or price > vwap):
            return False
        if "trend_in" in confirm:
            trend = str(row.get("trend") or "").strip().upper()
            if trend not in confirm["trend_in"]:
                return False
        return True

    def evaluate_snapshot(
        self,
        tickers: List[Dict[str, Any]],
        market_status: Optional[str] = None,
        now_ts: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        if not isinstance(tickers, list):
            return []

        ts = time.time() if now_ts is None else float(now_ts)
        row_map: Dict[str, Dict[str, Any]] = {}
        for item in tickers:
            if isinstance(item, dict) and item.get("ticker"):
                row_map[str(item["ticker"]).strip().upper()] = item

        fired_events: List[Dict[str, Any]] = []
        mutated = False

        with self._lock:
            for rule in self._rules:
                if not rule.get("enabled", True):
                    continue
                row = row_map.get(rule["ticker"])
                if not row:
                    continue

                # Fail-closed data integrity gate: never fire on invalid, stale, or zero prices
                if row.get("price_validity") != "VALID":
                    continue
                if row.get("freshness") not in ("CURRENT", "DELAYED"):
                    continue
                price = _safe_float(row.get("price"))
                if price is None or price <= 0:
                    continue

                observed = _safe_float(row.get(rule["metric"]))
                if observed is None:
                    continue
                target_val = self._resolve_target_value(rule, row)
                if target_val is None:
                    continue

                prev_val = _safe_float(rule.get("last_value"))
                rule["last_value"] = round(observed, 6)
                mutated = True

                op = rule["op"]
                matched = False
                if op == ">=":
                    matched = observed >= target_val
                elif op == "<=":
                    matched = observed <= target_val
                elif op == "crosses_above":
                    matched = prev_val is not None and prev_val < target_val <= observed
                elif op == "crosses_below":
                    matched = prev_val is not None and prev_val > target_val >= observed

                if not matched:
                    continue

                if not self._check_confirm(rule.get("confirm") or {}, row, price):
                    continue

                last_trig_ts = _safe_float(rule.get("last_triggered_ts"))
                cooldown = int(rule.get("cooldown_sec", 300) or 0)
                if last_trig_ts is not None and (ts - last_trig_ts) < cooldown:
                    continue

                now_iso = _utc_now_iso(ts)
                rule["last_triggered_ts"] = ts
                rule["last_triggered_at_utc"] = now_iso
                rule["trigger_count"] = int(rule.get("trigger_count", 0) or 0) + 1
                if rule.get("one_shot", True):
                    rule["enabled"] = False

                event = {
                    "event_id": f"evt-{uuid.uuid4().hex[:10]}",
                    "rule_id": rule["rule_id"],
                    "ticker": rule["ticker"],
                    "label": rule["label"],
                    "severity": rule["severity"],
                    "source": rule["source"],
                    "metric": rule["metric"],
                    "op": rule["op"],
                    "observed_value": round(observed, 4),
                    "target_value": round(target_val, 4),
                    "market_status": market_status or "UNKNOWN",
                    "message": f"{rule['ticker']}: {rule['label']} ({rule['metric']}={observed:.2f} {rule['op']} {target_val:.2f})",
                    "triggered_at_utc": now_iso,
                    "acknowledged": False,
                }
                self._events.insert(0, event)
                self._events = self._events[:MAX_EVENTS]
                fired_events.append(dict(event))

            if mutated or fired_events:
                self._persist()

        return fired_events

    def record_mandate_alerts(
        self,
        positions_analytics: List[Dict[str, Any]],
        market_status: Optional[str] = None,
        cooldown_sec: int = 900,
        now_ts: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Auto-surfaces critical/warning deterministic rule breaches into top alert bar."""
        if not isinstance(positions_analytics, list) or not positions_analytics:
            return []

        ts = time.time() if now_ts is None else float(now_ts)
        now_iso = _utc_now_iso(ts)
        emitted: List[Dict[str, Any]] = []

        with self._lock:
            for pos in positions_analytics:
                if not isinstance(pos, dict) or not pos.get("valid_telemetry"):
                    continue
                ticker = _normalize_ticker(pos.get("ticker"))
                if not ticker:
                    continue
                price = _safe_float(pos.get("price"))
                tranches = pos.get("tranches") if isinstance(pos.get("tranches"), dict) else {}

                for m in pos.get("triggered_mandates") or []:
                    if not isinstance(m, dict):
                        continue
                    rule_id = str(m.get("rule_id") or "").strip()
                    severity = str(m.get("severity") or "info").strip().lower()
                    trim_pct = m.get("trim_pct")
                    if severity not in ("critical", "warning"):
                        continue
                    if trim_pct is None and rule_id not in ("ENH_16_E", "ENH_17_B", "ENH_111"):
                        continue

                    cd_key = f"{ticker}|{rule_id}"
                    last_ts = self._mandate_cooldowns.get(cd_key)
                    if last_ts is not None and (ts - last_ts) < max(0, int(cooldown_sec)):
                        continue

                    sizing_note = ""
                    if trim_pct is not None:
                        t_info = tranches.get(f"{int(trim_pct)}pct")
                        if isinstance(t_info, dict):
                            sell_sh = t_info.get("sell_shares")
                            lim = t_info.get("sweep_limit_usd") if "SWEEP" in str(m.get("action", "")) else t_info.get("front_run_limit_usd")
                            if sell_sh is not None and lim is not None:
                                sizing_note = f"sell {sell_sh} sh ({int(trim_pct)}%) limit ~${lim:.2f} — "

                    detail = str(m.get("detail") or m.get("action") or rule_id).strip()
                    msg = f"{ticker} {rule_id}: {sizing_note}{detail}"
                    event = {
                        "event_id": f"evt-{uuid.uuid4().hex[:10]}",
                        "rule_id": f"mandate:{rule_id}",
                        "ticker": ticker,
                        "label": f"{rule_id} ({m.get('action', '')})",
                        "severity": severity,
                        "source": "analytics",
                        "metric": "mandate",
                        "op": rule_id,
                        "observed_value": round(price, 4) if price is not None else None,
                        "target_value": None,
                        "market_status": market_status or "UNKNOWN",
                        "message": msg[:220],
                        "triggered_at_utc": now_iso,
                        "acknowledged": False,
                    }
                    self._mandate_cooldowns[cd_key] = ts
                    self._events.insert(0, event)
                    self._events = self._events[:MAX_EVENTS]
                    emitted.append(dict(event))

            if emitted:
                self._persist()

        return emitted

    def get_dashboard_payload(self) -> Dict[str, Any]:
        with self._lock:
            unacked = _dedupe_unacknowledged_by_ticker(self._events)
            rules_copy = [dict(r) for r in self._rules]
            events_copy = [dict(e) for e in self._events]
            return {
                "rules": rules_copy,
                "events": events_copy,
                "unacknowledged_events": unacked,
                "unacknowledged_count": len(unacked),
                "active_rules_count": sum(1 for r in rules_copy if r.get("enabled", True)),
                "total_rules_count": len(rules_copy),
            }


def _extract_json_triggers(parsed: Any, out: List[Dict[str, Any]]) -> None:
    if not isinstance(parsed, dict):
        return
    for key in ("alert_triggers", "key_level_alerts"):
        items = parsed.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    out.append(item)
    ep = parsed.get("EXECUTION_PAYLOAD")
    if isinstance(ep, dict):
        for key in ("alert_triggers", "key_level_alerts"):
            items = ep.get(key)
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        out.append(item)


_PROSE_NON_TICKERS: Set[str] = {
    "A", "I", "L", "P", "S", "T", "SL", "TP", "PT", "VWAP", "RSI", "ATR", "HOD", "LOD",
    "USD", "EUR", "GEX", "MACD", "RVOL", "EMA", "SMA", "HOLD", "BUY", "SELL", "TRIM",
    "EXIT", "WAIT", "STOP",
}


def extract_triggers_from_chat(
    response_text: str,
    raw_payload_json: Optional[str] = None,
    known_tickers: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    """Extracts auto-armable key-level alert triggers from AI Council responses.

    Checks structured `alert_triggers` / `key_level_alerts` arrays inside JSON
    payloads first; falls back to explicit ticker + price trigger phrases in prose
    restricted to `known_tickers` when provided.
    """
    candidates: List[Dict[str, Any]] = []
    allowed_tickers: Optional[Set[str]] = (
        {str(t).strip().upper() for t in known_tickers if str(t).strip()}
        if known_tickers is not None
        else None
    )

    if raw_payload_json:
        try:
            _extract_json_triggers(json.loads(raw_payload_json), candidates)
        except Exception:
            pass

    if response_text:
        for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", response_text, flags=re.IGNORECASE):
            block = match.group(1).strip()
            if not block.startswith("{"):
                continue
            try:
                _extract_json_triggers(json.loads(block), candidates)
            except Exception:
                continue

    if candidates:
        filtered: List[Dict[str, Any]] = []
        for item in candidates:
            ticker = _normalize_ticker(item.get("ticker"))
            if not ticker:
                continue
            if allowed_tickers is not None and ticker not in allowed_tickers:
                continue
            filtered.append(item)
        return filtered

    # Deterministic prose fallback for explicit trigger phrases per line
    if not response_text:
        return []

    prose_triggers: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str, float]] = set()
    if allowed_tickers:
        sym_pat = "|".join(re.escape(s) for s in sorted(allowed_tickers, key=len, reverse=True))
    else:
        sym_pat = r"[A-Z]{2,5}"

    up_re = re.compile(
        rf"\b(?P<sym>{sym_pat})\b[^\n]{{0,60}}?(?i:reclaim|breakout(?:\s+above)?|above|resistance(?:\s+near|\s+at)?|trim[^\n]{{0,20}}?near)\s*(?i:>=|>|at|near)?\s*\$(?P<price>\d+(?:\.\d{{1,2}})?)",
    )
    down_re = re.compile(
        rf"\b(?P<sym>{sym_pat})\b[^\n]{{0,60}}?(?i:below|stop(?:\s+at|\s+loss|\s+below)?|support(?:\s+near|\s+at)?|breakdown(?:\s+below)?)\s*(?i:<=|<|at|below)?\s*\$(?P<price>\d+(?:\.\d{{1,2}})?)",
    )

    for line in response_text.splitlines():
        clean_line = re.sub(r"[*_`#>\[\]]", " ", line)
        for regex, op, severity, tag in (
            (up_re, ">=", "info", "Resistance / Reclaim"),
            (down_re, "<=", "warning", "Support / Stop"),
        ):
            for m in regex.finditer(clean_line):
                sym = m.group("sym").upper()
                if sym in _PROSE_NON_TICKERS and (allowed_tickers is None or sym not in allowed_tickers):
                    continue
                if allowed_tickers is not None and sym not in allowed_tickers:
                    continue
                price_val = _safe_float(m.group("price"))
                if price_val is None or price_val <= 0:
                    continue
                sig = (sym, op, round(price_val, 2))
                if sig in seen:
                    continue
                seen.add(sig)
                prose_triggers.append({
                    "ticker": sym,
                    "metric": "price",
                    "op": op,
                    "target": round(price_val, 2),
                    "severity": severity,
                    "label": f"{sym} {tag} ${price_val:.2f}",
                    "one_shot": True,
                })
                if len(prose_triggers) >= 8:
                    return prose_triggers

    return prose_triggers


ALERT_MONITOR = AlertMonitor()
