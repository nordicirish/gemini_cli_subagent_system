"""Deterministic US Macro Economic Calendar Shield (ENH_47 / ENH_259).

Tracks scheduled Tier-1/Tier-2 US macroeconomic releases (FOMC, CPI, Core PCE, NFP)
in America/New_York time with optional local override via cache/macro_calendar.json.
Flags the 24-48 hour pre-event window for automatic trailing stop tightening (50%)
and sizing dampening without consuming LLM search turns.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")
DEFAULT_MACRO_CALENDAR_PATH = os.path.join("cache", "macro_calendar.json")

# Scheduled US Tier-1/Tier-2 releases (times in America/New_York HH:MM).
# Custom or updated dates can be merged from cache/macro_calendar.json.
DEFAULT_MACRO_EVENTS: List[Dict[str, str]] = [
    {"name": "Core PCE", "date": "2026-11-25", "time_et": "10:00", "tier": "TIER_1"},
    {"name": "Non-Farm Payrolls", "date": "2026-12-04", "time_et": "08:30", "tier": "TIER_1"},
    {"name": "FOMC Rate Decision", "date": "2026-12-09", "time_et": "14:00", "tier": "TIER_1"},
    {"name": "US CPI", "date": "2026-12-10", "time_et": "08:30", "tier": "TIER_1"},
    {"name": "Core PCE", "date": "2026-12-23", "time_et": "08:30", "tier": "TIER_1"},
    {"name": "Non-Farm Payrolls", "date": "2027-01-08", "time_et": "08:30", "tier": "TIER_1"},
    {"name": "US CPI", "date": "2027-01-13", "time_et": "08:30", "tier": "TIER_1"},
    {"name": "FOMC Rate Decision", "date": "2027-01-27", "time_et": "14:00", "tier": "TIER_1"},
    {"name": "Core PCE", "date": "2027-01-29", "time_et": "08:30", "tier": "TIER_1"},
    {"name": "Non-Farm Payrolls", "date": "2027-02-05", "time_et": "08:30", "tier": "TIER_1"},
    {"name": "US CPI", "date": "2027-02-10", "time_et": "08:30", "tier": "TIER_1"},
    {"name": "Core PCE", "date": "2027-02-26", "time_et": "08:30", "tier": "TIER_1"},
    {"name": "Non-Farm Payrolls", "date": "2027-03-05", "time_et": "08:30", "tier": "TIER_1"},
    {"name": "US CPI", "date": "2027-03-10", "time_et": "08:30", "tier": "TIER_1"},
    {"name": "FOMC Rate Decision", "date": "2027-03-17", "time_et": "14:00", "tier": "TIER_1"},
]


def _load_calendar_events(calendar_path: Optional[str]) -> List[Dict[str, str]]:
    events = [dict(ev) for ev in DEFAULT_MACRO_EVENTS]
    if calendar_path and os.path.exists(calendar_path):
        try:
            with open(calendar_path, "r", encoding="utf-8") as f:
                custom = json.load(f)
            if isinstance(custom, list):
                cleaned: List[Dict[str, str]] = []
                for item in custom:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or "").strip()
                    date_s = str(item.get("date") or "").strip()
                    time_s = str(item.get("time_et") or "08:30").strip()
                    tier_s = str(item.get("tier") or "TIER_1").strip().upper()
                    if name and date_s:
                        cleaned.append({"name": name, "date": date_s, "time_et": time_s, "tier": tier_s})
                if cleaned:
                    events = cleaned
        except Exception:
            pass
    return events


def _parse_event_dt(ev: Dict[str, str]) -> Optional[datetime]:
    try:
        dt = datetime.strptime(f"{ev['date'][:10]} {ev.get('time_et', '08:30')[:5]}", "%Y-%m-%d %H:%M")
        return dt.replace(tzinfo=NY_TZ)
    except Exception:
        return None


def get_macro_calendar_status(
    now_ny: Optional[datetime] = None,
    calendar_path: str = DEFAULT_MACRO_CALENDAR_PATH,
) -> Dict[str, Any]:
    """Evaluates ENH_47 Macro Calendar Shield & ENH_259 48h pre-event stop tightening."""
    current = now_ny or datetime.now(NY_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=NY_TZ)
    else:
        current = current.astimezone(NY_TZ)

    cutoff = current - timedelta(hours=2)
    upcoming: List[tuple[datetime, Dict[str, Any]]] = []
    for ev in _load_calendar_events(calendar_path):
        ev_dt = _parse_event_dt(ev)
        if ev_dt is None or ev_dt < cutoff:
            continue
        hours_away = max(0.0, round((ev_dt - current).total_seconds() / 3600.0, 1))
        upcoming.append((
            ev_dt,
            {
                "name": ev["name"],
                "date": ev["date"],
                "time_et": ev.get("time_et", "08:30"),
                "tier": ev.get("tier", "TIER_1"),
                "hours_away": hours_away,
            },
        ))

    upcoming.sort(key=lambda pair: pair[0])
    upcoming_list = [pair[1] for pair in upcoming[:4]]
    next_event = upcoming_list[0] if upcoming_list else None
    hours_to_next = next_event["hours_away"] if next_event else None

    shield_active = bool(next_event is not None and hours_to_next is not None and hours_to_next <= 48.0)
    stop_tighten_factor = 0.5 if shield_active else 1.0
    if shield_active and hours_to_next is not None and hours_to_next <= 24.0:
        sizing_dampener = 0.5
    elif shield_active:
        sizing_dampener = 0.75
    else:
        sizing_dampener = 1.0

    return {
        "as_of_ny": current.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "shield_active": shield_active,
        "stop_tighten_factor": stop_tighten_factor,
        "sizing_dampener": sizing_dampener,
        "active_rules": ["ENH_47", "ENH_259"] if shield_active else [],
        "next_event": next_event,
        "upcoming_events": upcoming_list,
    }
