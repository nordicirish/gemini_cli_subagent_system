"""
Backfill scorable per-ticker decisions from SESSION-level decision_log transcripts.

Before per-ticker logging existed, every Council turn was stored as one SESSION row whose
transcript held either the EXECUTION_PAYLOAD JSON or the first ~2000 chars of the reply. The
backtester skips SESSION rows, so those decisions could never be scored. This script recovers them:

  * EXECUTION_PAYLOAD rows: the verdict (state_context.directive), the trade state
    (runtime_flags.trade_state), agreement_score_sa, and the held tickers with their spot price
    (forensic_intelligence.trailing_stop_audit / mutable_state.portfolio_snapshot).
      - EXECUTE + TRIM/SELL/EXIT/REDUCE  -> that action on each held ticker
      - EXECUTE + BUY/ADD               -> that action on each held ticker
      - HOLD / REJECT / NO_TRADE        -> HOLD on each held ticker (the position was kept)
  * Markdown rows: explicit "[ACTION] TICKER" bullets under Actionable Recommendations.

Many turns on one day describe the same position and the same forward window, so rows are
collapsed to one decision per (trading day, ticker, action), keeping the latest turn's data.
Already-backfilled sources (votes.backfilled_from) are skipped, so the script is idempotent.

Usage (from the repo root):
    python scripts/backfill_decision_log.py            # dry run: prints what would be added
    python scripts/backfill_decision_log.py --apply    # backs up context/gem.db, then appends rows
"""

import argparse
import datetime
import json
import os
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY_DIR = os.path.join(REPO_ROOT, "python")
# Both are needed: python/ modules import each other directly, and the database package
# imports itself as "python.database".
for _p in (PY_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from decision_backtester import (  # noqa: E402
    DEFENSIVE_ACTIONS,
    POSITIVE_ACTIONS,
    _NON_TICKER_LABELS,
    _parse_entry_date,
    extract_chat_recommendations,
)

BACKFILL_SOURCE = "backfill_session_transcript"


def _float(val):
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _payload_decisions(payload: dict) -> list:
    ep = payload.get("EXECUTION_PAYLOAD") if isinstance(payload.get("EXECUTION_PAYLOAD"), dict) else {}
    ms = payload.get("mutable_state") if isinstance(payload.get("mutable_state"), dict) else {}
    if not ms and isinstance(ep.get("mutable_state"), dict):
        ms = ep["mutable_state"]
    state_ctx = ep.get("state_context") if isinstance(ep.get("state_context"), dict) else {}
    flags = ep.get("runtime_flags") if isinstance(ep.get("runtime_flags"), dict) else {}
    risk = ep.get("risk_metrics") if isinstance(ep.get("risk_metrics"), dict) else {}
    forensic = ep.get("forensic_intelligence") if isinstance(ep.get("forensic_intelligence"), dict) else {}

    directive = str(state_ctx.get("directive") or ep.get("directive") or "").strip().upper()
    trade_state = str(flags.get("trade_state") or "").strip().upper()
    sa = _float(risk.get("agreement_score_sa"))

    spot = {}
    for row in forensic.get("trailing_stop_audit") or []:
        if isinstance(row, dict) and row.get("ticker"):
            spot[str(row["ticker"]).upper()] = _float(row.get("current_price") or row.get("price"))
    held = [
        str(p.get("ticker")).upper()
        for p in (ms.get("portfolio_snapshot") or [])
        if isinstance(p, dict) and p.get("ticker") and _float(p.get("shares"))
    ]
    tickers = list(dict.fromkeys(held + list(spot.keys())))
    if not tickers or not directive:
        return []

    if directive == "EXECUTE" and trade_state in (DEFENSIVE_ACTIONS | POSITIVE_ACTIONS) and trade_state != "HOLD":
        action = trade_state
    elif directive in ("HOLD", "REJECT") or trade_state in ("NO_TRADE", "HOLD", ""):
        action = "HOLD"
    else:
        return []

    return [
        {
            "ticker": t,
            "action": action,
            "price": spot.get(t),
            "agreement_score_sa": sa,
            "evidence": f"directive={directive or '-'} trade_state={trade_state or '-'}",
        }
        for t in tickers
        if t not in _NON_TICKER_LABELS
    ]


def build_backfill(rows: list, known_tickers: set) -> list:
    """rows: dicts with id, session_id, ticker, transcript_text, timestamp, votes."""
    already = set()
    for r in rows:
        votes = r.get("votes") if isinstance(r.get("votes"), dict) else {}
        if votes.get("source") == BACKFILL_SOURCE:
            already.update(votes.get("collapsed_sources") or [])
            already.add(votes.get("backfilled_from"))
    collapsed = {}
    for r in sorted(rows, key=lambda x: str(x.get("timestamp") or "")):
        if str(r.get("ticker") or "SESSION").upper() not in ("SESSION", ""):
            continue
        src = r.get("session_id")
        if src in already:
            continue
        ts = str(r.get("timestamp") or "")
        dt = _parse_entry_date(ts)
        if dt is None:
            continue
        text = r.get("transcript_text") or ""
        found = []
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            payload = None
        if isinstance(payload, dict):
            found = _payload_decisions(payload)
        else:
            for reco in extract_chat_recommendations(text, known_tickers or None):
                found.append({
                    "ticker": reco["ticker"], "action": reco["action"], "price": None,
                    "agreement_score_sa": None, "evidence": reco["text"][:200], "level": reco["level"],
                })
        for d in found:
            key = (dt.date().isoformat(), d["ticker"], d["action"])
            prev = collapsed.get(key)
            merged = dict(d)
            if prev:  # keep the latest turn, but don't lose a price / score the earlier one had
                for f in ("price", "agreement_score_sa"):
                    if merged.get(f) is None:
                        merged[f] = prev.get(f)
                merged["sources"] = prev["sources"] + [src]
            else:
                merged["sources"] = [src]
            merged["timestamp"] = ts
            collapsed[key] = merged
    return [dict(v, day=k[0]) for k, v in sorted(collapsed.items())]


def _known_tickers() -> set:
    known = set()
    for rel in ("context/user_config.json", "context/config.json"):
        try:
            with open(os.path.join(REPO_ROOT, rel), "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            continue
        for key in ("tickers", "macro", "WATCHLIST"):
            known.update(str(t).upper() for t in cfg.get(key) or [])
    return known


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the rows (default is a dry run)")
    args = ap.parse_args(argv)

    os.chdir(REPO_ROOT)
    from persistence import get_persistence_service

    service = get_persistence_service()
    rows = service.get_decision_log()
    known = _known_tickers()
    for r in rows:  # tickers that already appear in scorable rows are known too
        t = str(r.get("ticker") or "").upper()
        if t and t not in _NON_TICKER_LABELS:
            known.add(t)

    plan = build_backfill(rows, known)
    print(f"decision_log rows: {len(rows)}; recoverable per-ticker decisions after collapsing: {len(plan)}")
    for d in plan:
        sa = f"{d['agreement_score_sa']:.2f}" if d.get("agreement_score_sa") is not None else "  - "
        px = f"${d['price']:.4f}" if d.get("price") is not None else "  (day close)"
        print(f"  {d['day']}  {d['action']:<5} {d['ticker']:<6} entry {px:<14} sa {sa}  from {len(d['sources'])} turn(s)  [{d['evidence'][:60]}]")

    if not args.apply:
        print("\nDry run only. Re-run with --apply to back up context/gem.db and append these rows.")
        return 0
    if not plan:
        print("Nothing to write.")
        return 0

    db_path = os.path.join(REPO_ROOT, "context", "gem.db")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = os.path.join(REPO_ROOT, "context", f"gem.db.bak-{stamp}")
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(backup_path)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    print(f"Backup written: {backup_path}")

    for d in plan:
        latest_source = d["sources"][-1]
        service.append_decision_log({
            "session_id": f"backfill-{latest_source}",
            "ticker": d["ticker"],
            "action_proposed": d["action"],
            "decision_outcome": "BACKFILLED",
            "votes": {
                "source": BACKFILL_SOURCE,
                "backfilled_from": latest_source,
                # every collapsed SESSION row is listed so a re-run skips all of them
                "collapsed_sources": d["sources"],
                "price": d.get("price"),
                "level": d.get("level"),
                "agreement_score_sa": d.get("agreement_score_sa"),
            },
            "transcript_text": f"[Backfilled from {len(d['sources'])} SESSION turn(s)] {d['evidence']}",
            "timestamp": d["timestamp"],
        })
    print(f"Appended {len(plan)} per-ticker decision row(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
