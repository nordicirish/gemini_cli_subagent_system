"""Read-Only SEC EDGAR Filings & Dilution Metadata Offload (Phase 2).

Retrieves official SEC EDGAR company CIK mappings and recent submission metadata
with atomic local caching under cache/, flagging 72-hour equity dilution / shelf
offerings (S-3, S-3ASR, 424B5, S-1) without spending LLM search turns.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional

SEC_USER_AGENT = os.environ.get(
    "SEC_EDGAR_USER_AGENT",
    "GEMMarketCouncilResearch/1.0 (research-desk@gemcouncil.org)",
)
COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL_TMPL = "https://data.sec.gov/submissions/CIK{cik}.json"

DEFAULT_SEC_CACHE_PATH = os.path.join("cache", "sec_edgar_cache.json")
CIK_MAP_TTL_SEC = 86400
FILINGS_TTL_SEC = 21600
DILUTION_FORMS = {"S-3", "S-3ASR", "424B5", "424B3", "S-1"}
HIGH_SIGNAL_FORMS = DILUTION_FORMS | {"8-K", "10-Q", "10-K", "SC 13D", "SC 13G", "4"}

_CACHE_LOCK = threading.RLock()


def _default_http_get(url: str) -> Dict[str, Any]:
    import requests
    resp = requests.get(url, headers={"User-Agent": SEC_USER_AGENT}, timeout=12)
    resp.raise_for_status()
    return resp.json()


def _read_cache(cache_path: str) -> Dict[str, Any]:
    if not cache_path or not os.path.exists(cache_path):
        return {"cik_map": {}, "cik_map_ts": 0.0, "tickers": {}}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("cik_map", {})
            data.setdefault("cik_map_ts", 0.0)
            data.setdefault("tickers", {})
            return data
    except Exception:
        pass
    return {"cik_map": {}, "cik_map_ts": 0.0, "tickers": {}}


def _write_cache(cache_path: str, payload: Dict[str, Any]) -> None:
    if not cache_path:
        return
    parent = os.path.dirname(os.path.abspath(cache_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = f"{cache_path}.tmp.{os.getpid()}.{threading.get_ident()}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp_path, cache_path)


def _is_within_72h(filing_date: str, now_ts: float) -> bool:
    try:
        dt = datetime.strptime(filing_date[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        age_hours = (now_ts - dt.timestamp()) / 3600.0
        return -24.0 <= age_hours <= 72.0
    except Exception:
        return False


def fetch_recent_sec_filings(
    ticker: str,
    limit: int = 8,
    cache_path: str = DEFAULT_SEC_CACHE_PATH,
    http_get: Optional[Callable[[str], Dict[str, Any]]] = None,
    now_ts: Optional[float] = None,
) -> Dict[str, Any]:
    """Returns verified SEC EDGAR recent filings & 72h dilution warning for `ticker`."""
    sym = str(ticker or "").strip().upper()
    if not re.match(r"^[A-Z0-9.\-]{1,10}$", sym):
        return {"status": "unavailable", "ticker": sym, "cik": None, "dilution_warning_72h": False, "filings": [], "error": "Invalid equity ticker"}

    ts = time.time() if now_ts is None else float(now_ts)
    getter = http_get or _default_http_get
    max_items = max(1, min(20, int(limit or 8)))

    with _CACHE_LOCK:
        cache_data = _read_cache(cache_path)
        cached_entry = cache_data["tickers"].get(sym)
        if isinstance(cached_entry, dict) and (ts - float(cached_entry.get("cached_at", 0.0))) < FILINGS_TTL_SEC:
            out = dict(cached_entry["result"])
            out["cached"] = True
            return out

    try:
        with _CACHE_LOCK:
            cache_data = _read_cache(cache_path)
            cik_map = cache_data.get("cik_map") or {}
            cik_map_ts = float(cache_data.get("cik_map_ts", 0.0) or 0.0)
        if not cik_map or (ts - cik_map_ts) >= CIK_MAP_TTL_SEC:
            raw_map = getter(COMPANY_TICKERS_URL)
            parsed_map: Dict[str, str] = {}
            if isinstance(raw_map, dict):
                for entry in raw_map.values():
                    if isinstance(entry, dict) and entry.get("ticker") and entry.get("cik_str") is not None:
                        parsed_map[str(entry["ticker"]).strip().upper()] = f"{int(entry['cik_str']):010d}"
            if parsed_map:
                cik_map = parsed_map
                with _CACHE_LOCK:
                    c2 = _read_cache(cache_path)
                    c2["cik_map"] = parsed_map
                    c2["cik_map_ts"] = ts
                    _write_cache(cache_path, c2)

        cik = cik_map.get(sym)
        if not cik:
            return {"status": "unavailable", "ticker": sym, "cik": None, "dilution_warning_72h": False, "filings": [], "error": f"No SEC CIK mapping for {sym}"}

        subs = getter(SUBMISSIONS_URL_TMPL.format(cik=cik))
        recent = (subs.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        dates = recent.get("filingDate") or []
        accessions = recent.get("accessionNumber") or []
        primary_docs = recent.get("primaryDocument") or []
        descriptions = recent.get("primaryDocDescription") or []

        cik_num = str(int(cik))
        filings: List[Dict[str, Any]] = []
        dilution_72h = False

        total = min(len(forms), len(dates), len(accessions))
        for i in range(total):
            form = str(forms[i] or "").strip().upper()
            fdate = str(dates[i] or "").strip()
            acc = str(accessions[i] or "").strip()
            pdoc = str(primary_docs[i] or "").strip() if i < len(primary_docs) else ""
            desc = str(descriptions[i] or "").strip() if i < len(descriptions) else ""
            if form not in HIGH_SIGNAL_FORMS:
                continue

            is_dilution = form in DILUTION_FORMS
            if is_dilution and _is_within_72h(fdate, ts):
                dilution_72h = True

            acc_clean = acc.replace("-", "")
            doc_url = (
                f"https://www.sec.gov/Archives/edgar/data/{cik_num}/{acc_clean}/{pdoc}"
                if (acc_clean and pdoc)
                else f"https://www.sec.gov/edgar/browse/?CIK={cik}"
            )
            filings.append({
                "form": form,
                "filing_date": fdate,
                "accession_number": acc,
                "description": desc,
                "is_dilution_form": is_dilution,
                "url": doc_url,
            })
            if len(filings) >= max_items:
                break

        result = {
            "status": "ok",
            "ticker": sym,
            "cik": cik,
            "company_name": subs.get("name"),
            "dilution_warning_72h": dilution_72h,
            "filings": filings,
            "cached": False,
        }
        with _CACHE_LOCK:
            c3 = _read_cache(cache_path)
            c3["tickers"][sym] = {"cached_at": ts, "result": result}
            _write_cache(cache_path, c3)
        return result
    except Exception as exc:
        return {
            "status": "unavailable",
            "ticker": sym,
            "cik": None,
            "dilution_warning_72h": False,
            "filings": [],
            "error": str(exc)[:180],
        }
