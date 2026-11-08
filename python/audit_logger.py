"""
Structured Append-Only Audit Logging Module
Governed by Protocol 03 and Stage 1 Section 7.
Logs engineering and runtime events to context/agent_logs/agent-events.jsonl.
"""

import os
import re
import json
import uuid
import datetime
from dataclasses import dataclass, asdict, field
from typing import Dict, Any, List, Optional
from atomic_io import FileLock

AUDIT_LOG_DIR = os.path.join("context", "agent_logs")
AUDIT_LOG_FILE = os.path.join(AUDIT_LOG_DIR, "agent-events.jsonl")

# Secret redaction patterns
SECRET_PATTERNS = [
    re.compile(r'(?i)(api[_-]?key|secret|password|token|bearer|authorization)\s*[:=]\s*["\']?([^"\'\s,;}{]+)'),
    re.compile(r'(?i)(AIza[0-9A-Za-z-_]+)'),
    re.compile(r'(?i)(ghp_[0-9a-zA-Z]+)'),
]


def redact_secrets(val: Any) -> Any:
    """Recursively redacts credential patterns from nested dictionaries, lists, or strings."""
    if isinstance(val, str):
        cleaned = val
        for pat in SECRET_PATTERNS:
            cleaned = pat.sub(r'[REDACTED_SECRET]', cleaned)
        return cleaned
    elif isinstance(val, dict):
        redacted_dict = {}
        for k, v in val.items():
            if any(term in k.lower() for term in ("key", "secret", "token", "password", "auth")):
                redacted_dict[k] = "[REDACTED_SECRET]"
            else:
                redacted_dict[k] = redact_secrets(v)
        return redacted_dict
    elif isinstance(val, list):
        return [redact_secrets(item) for item in val]
    return val


@dataclass
class AuditEvent:
    action_category: str
    status: str  # "ATTEMPTED", "SUCCESS", "DENIED", "FAILED"
    agent_role: str = "Unknown"
    agent_id: str = "default-agent"
    task_id: str = "default-task"
    tool_name: Optional[str] = None
    permission_decision: str = "GRANTED"  # "GRANTED", "DENIED", "NEEDS_APPROVAL"
    approval_status: str = "NOT_REQUIRED"  # "APPROVED", "UNAPPROVED", "NOT_REQUIRED"
    files_inspected: List[str] = field(default_factory=list)
    files_modified: List[str] = field(default_factory=list)
    error_classification: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp_utc: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())

    def to_jsonl_line(self) -> str:
        data = asdict(self)
        clean_data = redact_secrets(data)
        return json.dumps(clean_data, separators=(",", ":"))


class AuditLogger:
    """Thread-safe, append-only logger for structured engineering events."""

    def __init__(self, log_path: str = AUDIT_LOG_FILE):
        self.log_path = log_path
        self._ensure_dir()

    def _ensure_dir(self):
        parent = os.path.dirname(self.log_path)
        if parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)

    def log_event(self, event: AuditEvent) -> None:
        """Appends an event safely to the JSONL log with file locking."""
        self._ensure_dir()
        line = event.to_jsonl_line() + "\n"
        lock_path = self.log_path + ".lock"

        try:
            with FileLock(lock_path, timeout=5.0):
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()
        except Exception as e:
            # If audit logging fails, we must alert and NOT allow sensitive operations to pass unlogged
            raise RuntimeError(f"Critical Audit Failure: Failed to append to audit log '{self.log_path}': {e}")


# Canonical global audit logger
GLOBAL_AUDIT_LOGGER = AuditLogger()
