"""
Owner-approved portfolio change proposals.

Model-originated EXECUTION_PAYLOADs (chat replies and agent ``update_ssot`` tool calls)
never write positions or cash directly. They are staged here as proposals carrying a
before/after diff, and are written only when the owner approves them.

Merge semantics are per ticker:
  * a ticker absent from the proposal is left unchanged (never deleted);
  * a position closes only when the proposal explicitly sets its shares to 0;
  * a field (shares / wac) absent from a proposal entry keeps its current value;
  * when EUR cash changes and USD is not proposed, USD is derived at the live EUR/USD rate and
    shown as its own diff row; with no fresh rate it keeps its current value (never a guessed rate).

At apply time the touched tickers and currencies must still hold the values the diff was
computed against; otherwise the proposal is rejected as stale and nothing is written.
"""

import copy
import datetime
import json
import threading
import uuid
from collections import OrderedDict
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

try:
    from persistence import get_persistence_service, PersistenceValidationError
    from persistence.contracts import validate_financial_decimal
except ImportError:
    from python.persistence import get_persistence_service, PersistenceValidationError
    from python.persistence.contracts import validate_financial_decimal

from fx_rates import derive_usd_cash, get_eurusd
from tool_registry import TRUSTED_SYSTEM_ROLES, get_active_caller_role

_PORTFOLIO_KEYS = ("portfolio_snapshot", "portfolio")
_CASH_FIELDS = (("EUR", "unallocated_cash_eur"), ("USD", "unallocated_cash_usd"))
_MAX_TRACKED_PROPOSALS = 64

STATUS_PENDING = "pending"
STATUS_APPLIED = "applied"
STATUS_DISCARDED = "discarded"
STATUS_SUPERSEDED = "superseded"


class ProposalNotFoundError(KeyError):
    """Raised when a proposal id is unknown or has been evicted."""


class ProposalStateError(RuntimeError):
    """Raised when a proposal is no longer pending (applied, discarded, or superseded)."""


class ProposalConflictError(RuntimeError):
    """Raised when portfolio state changed after the proposal's diff was computed."""


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_payload(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, dict):
        data = copy.deepcopy(payload)
    elif isinstance(payload, str):
        try:
            data = json.loads(payload, parse_float=Decimal)
        except json.JSONDecodeError as jde:
            raise PersistenceValidationError(f"Invalid EXECUTION_PAYLOAD JSON: {jde}") from jde
    else:
        raise PersistenceValidationError("EXECUTION_PAYLOAD must be a JSON string or object.")
    if not isinstance(data, dict):
        raise PersistenceValidationError("EXECUTION_PAYLOAD must resolve to a JSON object.")
    for key in ("mutable_state", "EXECUTION_PAYLOAD"):
        if key in data and not isinstance(data[key], dict):
            raise PersistenceValidationError(f"{key} in payload must be a dictionary")
    return data


def _scopes(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Payload scopes in the same precedence the persistence adapters use."""
    ms = data.get("mutable_state") if isinstance(data.get("mutable_state"), dict) else {}
    ep = data.get("EXECUTION_PAYLOAD") if isinstance(data.get("EXECUTION_PAYLOAD"), dict) else {}
    return [ms, data, ep]


def _find_first(data: Dict[str, Any], keys: Tuple[str, ...]) -> Tuple[bool, Any]:
    for scope in _scopes(data):
        for key in keys:
            if key in scope:
                return True, scope[key]
    return False, None


def _shares(val: Any, ticker: str) -> Decimal:
    return validate_financial_decimal(val, f"shares for {ticker}", max_scale=4, allow_zero=True, non_negative=True)


def _wac(val: Any, ticker: str) -> Decimal:
    return validate_financial_decimal(val, f"wac for {ticker}", max_scale=4, allow_zero=True, non_negative=True)


def _cash(val: Any, field: str) -> Decimal:
    return validate_financial_decimal(val, field, max_scale=2, allow_zero=True, non_negative=True)


def _read_current_state(service: Any) -> Dict[str, Any]:
    basket = service.get_portfolio_basket()
    positions: Dict[str, Dict[str, Decimal]] = {}
    for item in basket.get("portfolio", []) or []:
        ticker = str(item.get("ticker", "")).strip().upper()
        if not ticker:
            continue
        positions[ticker] = {"shares": _shares(item["shares"], ticker), "wac": _wac(item["wac"], ticker)}
    cash = {
        currency: _cash(basket.get(field, Decimal("0.00")), field)
        for currency, field in _CASH_FIELDS
    }
    return {"positions": positions, "cash": cash}


def _proposed_entries(data: Dict[str, Any]) -> Tuple[bool, List[Tuple[str, Dict[str, Any]]]]:
    """Returns (has_portfolio, [(TICKER, original_entry_dict), ...]) with structural validation."""
    has_portfolio, raw = _find_first(data, _PORTFOLIO_KEYS)
    if not has_portfolio:
        return False, []
    if not isinstance(raw, list):
        raise PersistenceValidationError("portfolio_snapshot must be a list of position objects")
    entries: List[Tuple[str, Dict[str, Any]]] = []
    seen = set()
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            raise PersistenceValidationError(f"portfolio_snapshot entry {idx} must be an object")
        t_raw = item.get("ticker")
        if not isinstance(t_raw, str) or not t_raw.strip():
            raise PersistenceValidationError(f"portfolio_snapshot entry {idx} is missing a ticker")
        ticker = t_raw.strip().upper()
        if ticker in seen:
            raise PersistenceValidationError(f"portfolio_snapshot lists {ticker} more than once")
        seen.add(ticker)
        entries.append((ticker, item))
    return True, entries


def compute_changes(data: Dict[str, Any], current: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Computes per-ticker position changes and per-currency cash changes against current state."""
    _, entries = _proposed_entries(data)
    position_changes: List[Dict[str, Any]] = []
    for ticker, item in entries:
        before = current["positions"].get(ticker)
        shares_after = _shares(item["shares"], ticker) if "shares" in item else (before["shares"] if before else None)
        wac_after = _wac(item["wac"], ticker) if "wac" in item else (before["wac"] if before else None)

        if shares_after is None:
            continue  # Mentions a ticker that is not held without proposing shares: nothing to change.
        if shares_after == Decimal("0"):
            if before is not None:
                position_changes.append({
                    "ticker": ticker,
                    "kind": "close",
                    "shares_before": before["shares"],
                    "shares_after": Decimal("0"),
                    "wac_before": before["wac"],
                    "wac_after": before["wac"],
                })
            continue
        if wac_after is None or wac_after == Decimal("0"):
            raise PersistenceValidationError(f"Cost basis (WAC) must be greater than 0 for active position {ticker}")
        if before is None:
            kind = "open"
        elif before["shares"] != shares_after or before["wac"] != wac_after:
            kind = "modify"
        else:
            continue
        position_changes.append({
            "ticker": ticker,
            "kind": kind,
            "shares_before": before["shares"] if before else None,
            "shares_after": shares_after,
            "wac_before": before["wac"] if before else None,
            "wac_after": wac_after,
        })

    cash_changes: List[Dict[str, Any]] = []
    for currency, field in _CASH_FIELDS:
        has_field, raw = _find_first(data, (field,))
        if not has_field:
            continue
        after = _cash(raw, field)
        before = current["cash"][currency]
        if after != before:
            cash_changes.append({"currency": currency, "before": before, "after": after, "note": None})

    # USD cash mirrors EUR cash: when EUR changes and USD is not proposed, derive USD at the live
    # EUR/USD rate and show it as its own diff row. Without a fresh rate USD stays unchanged.
    eur_change = next((c for c in cash_changes if c["currency"] == "EUR"), None)
    usd_proposed, _ = _find_first(data, ("unallocated_cash_usd",))
    if eur_change is not None and not usd_proposed:
        quote = get_eurusd()
        if quote is not None:
            derived = derive_usd_cash(eur_change["after"], quote)
            if derived != current["cash"]["USD"]:
                cash_changes.append({
                    "currency": "USD",
                    "before": current["cash"]["USD"],
                    "after": derived,
                    "note": f"derived from EUR at live EUR/USD {quote.rate} ({quote.as_of_utc})",
                })
    return position_changes, cash_changes


def build_apply_payload(
    data: Dict[str, Any],
    current: Dict[str, Any],
    position_changes: List[Dict[str, Any]],
    cash_changes: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Rewrites a model payload into an explicit payload for the persistence adapters, whose
    portfolio update replaces the whole position set: the portfolio sent is the complete
    merged set (current positions + approved changes), and both cash balances are explicit.
    Non-financial content (decision log entries, trade lessons) is carried through unchanged.
    """
    has_portfolio, entries = _proposed_entries(data)
    applied = copy.deepcopy(data)
    for scope in _scopes(applied):
        for key in _PORTFOLIO_KEYS + tuple(field for _, field in _CASH_FIELDS):
            scope.pop(key, None)
    ms = applied.get("mutable_state")
    if not isinstance(ms, dict):
        ms = {}
        applied["mutable_state"] = ms

    if has_portfolio:
        changes_by_ticker = {c["ticker"]: c for c in position_changes}
        handled = set()
        merged: List[Dict[str, Any]] = []
        # Proposal entries first, keeping their metadata (action / trade_state) for decision logging.
        for ticker, item in entries:
            handled.add(ticker)
            change = changes_by_ticker.get(ticker)
            held = current["positions"].get(ticker)
            if change is not None:
                if change["kind"] == "close":
                    continue
                shares, wac = change["shares_after"], change["wac_after"]
            elif held is not None:
                shares, wac = held["shares"], held["wac"]
            else:
                continue
            entry = dict(item)
            entry.update({"ticker": ticker, "shares": shares, "wac": wac})
            merged.append(entry)
        for ticker, held in current["positions"].items():
            if ticker not in handled:
                merged.append({"ticker": ticker, "shares": held["shares"], "wac": held["wac"]})
        ms["portfolio_snapshot"] = merged

    if cash_changes:
        after_by_currency = {c["currency"]: c["after"] for c in cash_changes}
        for currency, field in _CASH_FIELDS:
            ms[field] = after_by_currency.get(currency, current["cash"][currency])
    return applied


def _dec_str(val: Optional[Decimal]) -> Optional[str]:
    return None if val is None else str(val)


class PortfolioProposal:
    def __init__(
        self,
        payload: Dict[str, Any],
        position_changes: List[Dict[str, Any]],
        cash_changes: List[Dict[str, Any]],
        source_role: str,
        turn_id: Optional[str],
    ):
        self.proposal_id = f"prop-{uuid.uuid4().hex[:12]}"
        self.created_at = _utc_now_iso()
        self.payload = payload
        self.position_changes = position_changes
        self.cash_changes = cash_changes
        self.source_role = source_role
        self.turn_id = turn_id
        self.status = STATUS_PENDING
        self.resolved_at: Optional[str] = None

    def summary(self) -> str:
        parts = []
        for c in self.position_changes:
            if c["kind"] == "open":
                parts.append(f"OPEN {c['ticker']} {c['shares_after']} @ {c['wac_after']}")
            elif c["kind"] == "close":
                parts.append(f"CLOSE {c['ticker']} ({c['shares_before']} shares)")
            else:
                parts.append(
                    f"MODIFY {c['ticker']} shares {c['shares_before']} -> {c['shares_after']}, "
                    f"wac {c['wac_before']} -> {c['wac_after']}"
                )
        for c in self.cash_changes:
            parts.append(f"CASH {c['currency']} {c['before']} -> {c['after']}")
        return "; ".join(parts)

    def to_public_dict(self) -> Dict[str, Any]:
        """JSON-safe view; Decimals are rendered as exact strings."""
        return {
            "proposal_id": self.proposal_id,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "status": self.status,
            "source": self.source_role,
            "turn_id": self.turn_id,
            "summary": self.summary(),
            "positions": [
                {
                    "ticker": c["ticker"],
                    "kind": c["kind"],
                    "shares_before": _dec_str(c["shares_before"]),
                    "shares_after": _dec_str(c["shares_after"]),
                    "wac_before": _dec_str(c["wac_before"]),
                    "wac_after": _dec_str(c["wac_after"]),
                }
                for c in self.position_changes
            ],
            "cash": [
                {"currency": c["currency"], "before": _dec_str(c["before"]), "after": _dec_str(c["after"]), "note": c.get("note")}
                for c in self.cash_changes
            ],
        }


class ProposalStore:
    """In-memory store of portfolio proposals (pending proposals do not survive a server restart)."""

    def __init__(self, service_factory=None):
        self._service_factory = service_factory or get_persistence_service
        self._lock = threading.RLock()
        self._proposals: "OrderedDict[str, PortfolioProposal]" = OrderedDict()
        self.current_turn_id: Optional[str] = None

    def submit(self, payload: Any, source_role: Optional[str] = None, turn_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Stages a model payload. Returns {"status": "pending", "proposal": {...}} when it would
        change positions or cash. A payload with no financial changes carries only decision/lesson
        content, which is recorded immediately: {"status": "no_changes"}.
        Raises PersistenceValidationError for malformed payloads.
        """
        data = _parse_payload(payload)
        role = source_role or get_active_caller_role()
        with self._lock:
            service = self._service_factory()
            current = _read_current_state(service)
            position_changes, cash_changes = compute_changes(data, current)
            if not position_changes and not cash_changes:
                applied = build_apply_payload(data, current, [], [])
                res = service.update_ssot_payload(applied)
                if not isinstance(res, dict) or res.get("status") != "success":
                    msg = res.get("message", "Update failed") if isinstance(res, dict) else "Update failed"
                    raise PersistenceValidationError(f"Failed to record council payload: {msg}")
                return {"status": "no_changes"}

            for existing in self._proposals.values():
                if existing.status == STATUS_PENDING:
                    existing.status = STATUS_SUPERSEDED
                    existing.resolved_at = _utc_now_iso()
            proposal = PortfolioProposal(
                payload=data,
                position_changes=position_changes,
                cash_changes=cash_changes,
                source_role=role,
                turn_id=turn_id if turn_id is not None else self.current_turn_id,
            )
            self._proposals[proposal.proposal_id] = proposal
            while len(self._proposals) > _MAX_TRACKED_PROPOSALS:
                self._proposals.popitem(last=False)
            return {"status": STATUS_PENDING, "proposal": proposal.to_public_dict()}

    def _get(self, proposal_id: str) -> PortfolioProposal:
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise ProposalNotFoundError(proposal_id)
        return proposal

    def get(self, proposal_id: str) -> Dict[str, Any]:
        with self._lock:
            return self._get(proposal_id).to_public_dict()

    def list(self, status: Optional[str] = STATUS_PENDING, turn_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                p.to_public_dict()
                for p in self._proposals.values()
                if (status is None or p.status == status) and (turn_id is None or p.turn_id == turn_id)
            ]

    def apply(self, proposal_id: str, approver_role: str = "Owner") -> Dict[str, Any]:
        """Writes an approved proposal. Only trusted (human/system) callers may approve."""
        active_role = get_active_caller_role()
        if active_role not in TRUSTED_SYSTEM_ROLES:
            raise PermissionError(f"Role '{active_role}' cannot approve portfolio proposals.")
        if approver_role not in TRUSTED_SYSTEM_ROLES:
            raise PermissionError(f"Approver role '{approver_role}' is not a trusted owner role.")

        with self._lock:
            proposal = self._get(proposal_id)
            if proposal.status != STATUS_PENDING:
                raise ProposalStateError(f"Proposal {proposal_id} is {proposal.status}, not pending.")

            service = self._service_factory()
            current = _read_current_state(service)
            stale = []
            for c in proposal.position_changes:
                held = current["positions"].get(c["ticker"])
                if c["shares_before"] is None:
                    if held is not None:
                        stale.append(c["ticker"])
                elif held is None or held["shares"] != c["shares_before"] or held["wac"] != c["wac_before"]:
                    stale.append(c["ticker"])
            for c in proposal.cash_changes:
                if current["cash"][c["currency"]] != c["before"]:
                    stale.append(f"cash {c['currency']}")
            if stale:
                raise ProposalConflictError(
                    "Portfolio changed since this proposal was made (" + ", ".join(stale) + "). "
                    "Nothing was written; ask the council for a fresh proposal."
                )

            applied = build_apply_payload(proposal.payload, current, proposal.position_changes, proposal.cash_changes)
            res = service.update_ssot_payload(applied, caller_role=approver_role)
            if not isinstance(res, dict) or res.get("status") != "success":
                msg = res.get("message", "Update failed") if isinstance(res, dict) else "Update failed"
                raise PersistenceValidationError(f"Failed to apply proposal {proposal_id}: {msg}")
            proposal.status = STATUS_APPLIED
            proposal.resolved_at = _utc_now_iso()
            return proposal.to_public_dict()

    def discard(self, proposal_id: str) -> Dict[str, Any]:
        with self._lock:
            proposal = self._get(proposal_id)
            if proposal.status != STATUS_PENDING:
                raise ProposalStateError(f"Proposal {proposal_id} is {proposal.status}, not pending.")
            proposal.status = STATUS_DISCARDED
            proposal.resolved_at = _utc_now_iso()
            return proposal.to_public_dict()


PROPOSAL_STORE = ProposalStore()
