"""
Owner-approved self-improvement proposals (trade lessons and rules.md amendments).

Agents never change their own lessons or rules directly:
  * an agent's ``update_trade_lessons`` call is staged here as one ``lesson`` proposal per lesson;
  * ``[RULE_PROPOSAL]`` lines in a Review Log reply are staged as ``rule`` proposals.

Nothing is written until the owner clicks Apply in the UI (optionally after editing the text).
Applying a lesson appends it to the trade_lessons registry. Applying a rule appends an amendment
under ``## Owner-Approved Amendments`` in gem_trading_rules/rules.md through ``tools.update_rules``,
bound to a one-time owner approval for that exact content.

Proposals persist in context/improvement_proposals.json so they survive server restarts.
"""

import datetime
import os
import re
import threading
import uuid
from typing import Any, Callable, Dict, List, Optional

try:
    from atomic_io import atomic_read_json, atomic_update_json
    from tool_registry import TRUSTED_SYSTEM_ROLES, get_active_caller_role
except ImportError:
    from python.atomic_io import atomic_read_json, atomic_update_json
    from python.tool_registry import TRUSTED_SYSTEM_ROLES, get_active_caller_role

KIND_LESSON = "lesson"
KIND_RULE = "rule"
STATUS_PENDING = "pending"
STATUS_APPLIED = "applied"
STATUS_DISCARDED = "discarded"

AMENDMENTS_HEADING = "## Owner-Approved Amendments"
MAX_TEXT_CHARS = 2000
MAX_STORED_RECORDS = 200
DEFAULT_STORE_PATH = os.path.join("context", "improvement_proposals.json")
DEFAULT_RULES_PATH = os.path.join("gem_trading_rules", "rules.md")

_RULE_ID_RE = re.compile(r"^(?:MANDATE|ENH|L)[-_ ]?[A-Z0-9_]+$|^[A-Z][A-Z0-9_\-]{2,40}$")


class ImprovementNotFoundError(KeyError):
    """Unknown proposal id."""


class ImprovementStateError(RuntimeError):
    """Proposal is no longer pending."""


class ImprovementValidationError(ValueError):
    """Proposal text or fields are invalid."""


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _clean_text(text: Any) -> str:
    cleaned = str(text or "").replace("\r\n", "\n").strip()
    if not cleaned:
        raise ImprovementValidationError("Proposal text cannot be empty.")
    if len(cleaned) > MAX_TEXT_CHARS:
        raise ImprovementValidationError(f"Proposal text must be at most {MAX_TEXT_CHARS} characters.")
    return cleaned


def is_agent_caller() -> bool:
    """True when the active caller is an agent role (not the owner/UI/system context)."""
    return get_active_caller_role() not in TRUSTED_SYSTEM_ROLES


def parse_rule_proposal_line(line_text: str) -> Optional[Dict[str, str]]:
    """Parses '[RULE_PROPOSAL] RULE_ID — (ADD|MODIFY) text ... Justification: ...' prose into fields."""
    text = str(line_text or "").replace("**", "").replace("`", "").strip()
    text = re.sub(r"^\s*(?:[*\-+]|\d+[.)])\s+", "", text)
    m = re.match(r"^\[?RULE_PROPOSAL\]?[\s:]*", text, re.IGNORECASE)
    if not m:
        return None
    body = text[m.end():].strip()
    if not body:
        return None

    rule_id = "NEW_RULE"
    head = re.match(r"^([A-Z][A-Z0-9_\-]{1,40})\b[\s:]*[—–\-:]*\s*", body)
    # Rule ids are written in caps and carry a digit or underscore (MANDATE_52, ENH_259, L-18, NEW_RULE),
    # so a capitalised first word of prose ("Always ...") is never mistaken for one.
    if head and _RULE_ID_RE.match(head.group(1)) and re.search(r"[\d_\-]", head.group(1)):
        rule_id = head.group(1)
        body = body[head.end():].strip()

    action = "ADD"
    act = re.match(r"^\(?\s*(ADD|MODIFY)\s*\)?[\s:—–\-]*", body, re.IGNORECASE)
    if act:
        action = act.group(1).upper()
        body = body[act.end():].strip()
    elif re.search(r"\bmodify\b", body[:80], re.IGNORECASE):
        action = "MODIFY"

    justification = ""
    jm = re.search(r"\b(?:Justification|Evidence)\s*:\s*", body, re.IGNORECASE)
    if jm:
        justification = body[jm.end():].strip()
        body = body[:jm.start()].strip().rstrip(".;,— -")
    body = body.strip().strip('"“”').strip()
    if not body:
        return None
    return {"rule_id": rule_id, "action": action, "text": body, "justification": justification}


def extract_rule_proposals(response_text: str) -> List[Dict[str, str]]:
    """Extracts every [RULE_PROPOSAL] bullet from a markdown reply."""
    found: List[Dict[str, str]] = []
    for line in str(response_text or "").splitlines():
        if "RULE_PROPOSAL" not in line.upper():
            continue
        parsed = parse_rule_proposal_line(line)
        if parsed:
            found.append(parsed)
    return found


def build_rules_with_amendment(rules_text: str, record: Dict[str, Any], text: str, approved_at: str) -> str:
    """Returns rules.md content with one amendment appended under the Owner-Approved Amendments section."""
    existing = re.findall(r"^### \[AMENDMENT A-(\d+)\]", rules_text, re.MULTILINE)
    next_n = max([int(x) for x in existing] or [0]) + 1
    rule_id = record.get("rule_id") or "NEW_RULE"
    action = record.get("action") or "ADD"
    lines = [
        f"### [AMENDMENT A-{next_n}] {rule_id} ({action}) — owner-approved {approved_at[:10]}",
        f"- **Rule:** {text}",
    ]
    if action == "MODIFY":
        lines.append(f"- **Precedence:** Supersedes any conflicting text in {rule_id}.")
    if record.get("justification"):
        lines.append(f"- **Justification:** {record['justification']}")
    lines.append(f"- **Source:** {record.get('source') or 'Review Engine'} proposal `{record['id']}`")
    block = "\n".join(lines) + "\n"

    body = rules_text.rstrip("\n") + "\n"
    if AMENDMENTS_HEADING not in body:
        intro = (
            f"\n{AMENDMENTS_HEADING}\n"
            "Rules approved by the owner from Review Engine proposals. Where an amendment conflicts with "
            "an earlier rule it names, the amendment takes precedence.\n"
        )
        return body + intro + "\n" + block

    # Insert at the end of the amendments section (before the next '## ' heading, if any).
    start = body.index(AMENDMENTS_HEADING)
    nxt = re.search(r"^## ", body[start + len(AMENDMENTS_HEADING):], re.MULTILINE)
    insert_at = len(body) if nxt is None else start + len(AMENDMENTS_HEADING) + nxt.start()
    head, tail = body[:insert_at].rstrip("\n") + "\n\n", body[insert_at:]
    return head + block + ("\n" + tail if tail else "")


class ImprovementProposalStore:
    """Persistent queue of agent-proposed lessons and rule amendments awaiting owner approval."""

    def __init__(
        self,
        store_path: str = DEFAULT_STORE_PATH,
        rules_path: str = DEFAULT_RULES_PATH,
        service_factory: Optional[Callable[[], Any]] = None,
        rules_writer: Optional[Callable[[str], Any]] = None,
    ):
        self.store_path = store_path
        self.rules_path = rules_path
        self._service_factory = service_factory
        self._rules_writer = rules_writer
        self._lock = threading.RLock()
        self.current_turn_id: Optional[str] = None

    # --- persistence -----------------------------------------------------------------

    def _read_all(self) -> List[Dict[str, Any]]:
        data = atomic_read_json(self.store_path, default={"proposals": []}) if os.path.exists(self.store_path) else {}
        items = data.get("proposals") if isinstance(data, dict) else None
        return [x for x in (items or []) if isinstance(x, dict)]

    def _mutate(self, fn: Callable[[List[Dict[str, Any]]], Any]) -> Any:
        result_box: Dict[str, Any] = {}

        def _apply(current: Any) -> Dict[str, Any]:
            items = current.get("proposals") if isinstance(current, dict) else None
            items = [x for x in (items or []) if isinstance(x, dict)]
            result_box["value"] = fn(items)
            if len(items) > MAX_STORED_RECORDS:
                # Never drop pending proposals; trim the oldest resolved ones first.
                pending = [x for x in items if x.get("status") == STATUS_PENDING]
                resolved = [x for x in items if x.get("status") != STATUS_PENDING]
                keep_resolved = max(0, MAX_STORED_RECORDS - len(pending))
                items = (resolved[-keep_resolved:] if keep_resolved else []) + pending
                items.sort(key=lambda x: x.get("created_at") or "")
            return {"proposals": items}

        with self._lock:
            parent = os.path.dirname(self.store_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            atomic_update_json(self.store_path, _apply, default_factory=lambda: {"proposals": []})
        return result_box.get("value")

    # --- staging ---------------------------------------------------------------------

    def _stage(self, record: Dict[str, Any]) -> Dict[str, Any]:
        def _fn(items: List[Dict[str, Any]]) -> Dict[str, Any]:
            key = (record["kind"], _norm(record["text"]))
            for existing in items:
                if existing.get("status") == STATUS_PENDING and (existing.get("kind"), _norm(existing.get("text"))) == key:
                    return dict(existing)
            items.append(record)
            return dict(record)

        return self._mutate(_fn)

    def _base_record(self, kind: str, text: str, source: str, turn_id: Optional[str]) -> Dict[str, Any]:
        return {
            "id": f"imp-{uuid.uuid4().hex[:12]}",
            "kind": kind,
            "status": STATUS_PENDING,
            "text": _clean_text(text),
            "source": str(source or "Agent")[:64],
            "turn_id": turn_id or self.current_turn_id,
            "created_at": _utc_now_iso(),
        }

    def stage_lesson(self, text: str, ticker: str = "PORTFOLIO", source: str = "Agent", turn_id: Optional[str] = None) -> Dict[str, Any]:
        rec = self._base_record(KIND_LESSON, text, source, turn_id)
        sym = str(ticker or "").strip().upper()
        rec["ticker"] = sym if (sym and len(sym) <= 10) else "PORTFOLIO"
        return self._stage(rec)

    def stage_rule(
        self,
        rule_id: str,
        text: str,
        action: str = "ADD",
        justification: str = "",
        source: str = "Review Engine",
        turn_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        rec = self._base_record(KIND_RULE, text, source, turn_id)
        rec["rule_id"] = (str(rule_id or "NEW_RULE").strip().upper() or "NEW_RULE")[:48]
        rec["action"] = "MODIFY" if str(action or "").upper() == "MODIFY" else "ADD"
        rec["justification"] = str(justification or "").strip()[:MAX_TEXT_CHARS]
        return self._stage(rec)

    # --- queries ---------------------------------------------------------------------

    def get(self, proposal_id: str) -> Dict[str, Any]:
        for item in self._read_all():
            if item.get("id") == proposal_id:
                return dict(item)
        raise ImprovementNotFoundError(proposal_id)

    def list(self, status: Optional[str] = STATUS_PENDING, turn_id: Optional[str] = None) -> List[Dict[str, Any]]:
        items = self._read_all()
        return [
            dict(x) for x in items
            if (status is None or x.get("status") == status) and (turn_id is None or x.get("turn_id") == turn_id)
        ]

    # --- resolution ------------------------------------------------------------------

    def _resolve(self, proposal_id: str, new_status: str, extra: Dict[str, Any]) -> Dict[str, Any]:
        def _fn(items: List[Dict[str, Any]]) -> Dict[str, Any]:
            for item in items:
                if item.get("id") == proposal_id:
                    if item.get("status") != STATUS_PENDING:
                        raise ImprovementStateError(f"Proposal {proposal_id} is already {item.get('status')}.")
                    item["status"] = new_status
                    item["resolved_at"] = _utc_now_iso()
                    item.update(extra)
                    return dict(item)
            raise ImprovementNotFoundError(proposal_id)

        return self._mutate(_fn)

    def discard(self, proposal_id: str) -> Dict[str, Any]:
        return self._resolve(proposal_id, STATUS_DISCARDED, {})

    def apply(self, proposal_id: str, edited_text: Optional[str] = None) -> Dict[str, Any]:
        """Owner approval: writes the (optionally edited) lesson or rule amendment, then marks it applied."""
        if is_agent_caller():
            raise PermissionError("Only the owner can apply improvement proposals.")
        with self._lock:
            record = self.get(proposal_id)
            if record.get("status") != STATUS_PENDING:
                raise ImprovementStateError(f"Proposal {proposal_id} is already {record.get('status')}.")
            text = _clean_text(edited_text) if edited_text is not None else record["text"]
            if record["kind"] == KIND_LESSON:
                result = append_owner_lesson(
                    self._service(), text, record.get("ticker") or "PORTFOLIO",
                    source=f"OWNER_APPROVED_{(record.get('source') or 'AGENT').upper().replace(' ', '_')}"[:64],
                )
                extra = {"applied_text": text, "duplicate": result["duplicate"]}
            elif record["kind"] == KIND_RULE:
                extra = {"applied_text": text, **self._apply_rule(record, text)}
            else:
                raise ImprovementValidationError(f"Unknown proposal kind: {record.get('kind')}")
            return self._resolve(proposal_id, STATUS_APPLIED, extra)

    def _service(self) -> Any:
        if self._service_factory is not None:
            return self._service_factory()
        try:
            from persistence import get_persistence_service
        except ImportError:
            from python.persistence import get_persistence_service
        return get_persistence_service()

    def _apply_rule(self, record: Dict[str, Any], text: str) -> Dict[str, Any]:
        with open(self.rules_path, "r", encoding="utf-8") as f:
            current = f.read()
        approved_at = _utc_now_iso()
        new_content = build_rules_with_amendment(current, record, text, approved_at)
        if self._rules_writer is not None:
            self._rules_writer(new_content)
        else:
            try:
                import tools
                from tool_registry import GLOBAL_OWNER_APPROVAL
            except ImportError:
                from python import tools
                from python.tool_registry import GLOBAL_OWNER_APPROVAL
            # Bind a one-time owner approval to exactly this content, then go through the guarded writer.
            GLOBAL_OWNER_APPROVAL.grant_one_time_approval("update_rules", self.rules_path, new_content)
            tools.update_rules(new_content)
        m = re.findall(r"^### \[AMENDMENT (A-\d+)\]", new_content, re.MULTILINE)
        return {"amendment_id": m[-1] if m else None, "approved_at": approved_at}


def append_owner_lesson(service: Any, lesson_text: str, ticker: str = "PORTFOLIO", source: str = "OWNER_APPROVED_REVIEW") -> Dict[str, Any]:
    """Appends one owner-approved lesson to the trade_lessons registry (dedup by exact text)."""
    try:
        from tool_registry import authorize_financial_mutation
    except ImportError:
        from python.tool_registry import authorize_financial_mutation
    lesson_text = _clean_text(lesson_text)
    sym = str(ticker or "").strip().upper()
    sym = sym if (sym and len(sym) <= 10) else "PORTFOLIO"
    entry = {
        "date": datetime.datetime.now().strftime("%Y-%m-%d"),
        "ticker": sym,
        "lesson": lesson_text,
        "source": str(source or "OWNER_APPROVED_REVIEW").strip()[:64],
    }
    authorize_financial_mutation(
        operation_name="update_trade_lessons",
        target_resource="context/trade_lessons.json",
        payload=entry,
    )
    existing = service.get_trade_lessons()
    lessons_list = list(existing) if isinstance(existing, list) else []
    already = any(
        isinstance(x, dict) and str(x.get("lesson") or x.get("rule") or "").strip() == lesson_text
        for x in lessons_list
    )
    if not already:
        lessons_list.append(entry)
        service.save_trade_lessons(lessons_list)
    return {"entry": entry, "duplicate": already, "total_lessons": len(lessons_list)}


def lesson_items_from_payload(parsed: Any) -> List[Dict[str, str]]:
    """Normalizes an update_trade_lessons payload (list/dict/str) into [{'text','ticker'}]."""
    if isinstance(parsed, dict):
        for key in ("trade_lessons", "new_trade_lessons", "lessons"):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
        else:
            parsed = [parsed]
    if isinstance(parsed, str):
        parsed = [parsed]
    items: List[Dict[str, str]] = []
    for raw in parsed if isinstance(parsed, list) else []:
        if isinstance(raw, str):
            text, ticker = raw, "PORTFOLIO"
        elif isinstance(raw, dict):
            text = next((str(raw[k]) for k in ("rule", "lesson", "directive_text", "text") if raw.get(k)), "")
            ticker = str(raw.get("ticker") or "PORTFOLIO")
        else:
            continue
        if text.strip():
            items.append({"text": text.strip()[:MAX_TEXT_CHARS], "ticker": ticker})
    return items


IMPROVEMENT_STORE = ImprovementProposalStore()
