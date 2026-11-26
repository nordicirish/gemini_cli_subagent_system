import os
import collections
import contextlib
from fastapi import BackgroundTasks, Body, HTTPException, Request
import sys
import uvicorn
import asyncio
import json
import collections.abc
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ValidationError, field_validator, model_validator
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, JSONResponse
import threading
import glob

# `python python/web_server.py` puts only python/ on sys.path, but the database package imports
# `python.database...`, which also needs the repo root (tests get it from running in the root).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(1, _REPO_ROOT)

from agent_framework import types, Part

from agent_framework import AgentFramework
import agent_framework
import tools
from audit_logger import redact_secrets
try:
    from chat_payload_parser import (
        PAYLOAD_MARKERS as _PAYLOAD_MARKERS,
        assemble_slim_tickers,
        is_halted_execution_payload as _is_halted_execution_payload,
        looks_like_execution_payload as _looks_like_execution_payload,
        normalize_for_json as _normalize_for_json,
    )
    from colab_calibration import (
        ARTIFACT_REGISTRY,
        export_colab_bundle,
        get_artifact_status,
        get_calibration_status,
        import_colab_artifact,
        import_colab_calibration,
        load_colab_artifact,
    )
    from colab_jobs import ColabJobConflictError, get_colab_job_manager
except ImportError:
    from python.chat_payload_parser import (
        PAYLOAD_MARKERS as _PAYLOAD_MARKERS,
        assemble_slim_tickers,
        is_halted_execution_payload as _is_halted_execution_payload,
        looks_like_execution_payload as _looks_like_execution_payload,
        normalize_for_json as _normalize_for_json,
    )
    from python.colab_calibration import (
        ARTIFACT_REGISTRY,
        export_colab_bundle,
        get_artifact_status,
        get_calibration_status,
        import_colab_artifact,
        import_colab_calibration,
        load_colab_artifact,
    )
    from python.colab_jobs import ColabJobConflictError, get_colab_job_manager
# cloud_sync removed — GDrive Decoupling Guardrail (ANTIGRAVITY v11.03)

try:
    from persistence import get_persistence_service, PersistenceValidationError, strip_ssot_history
    from persistence.contracts import (
        serialize_json_with_exact_decimals,
        validate_financial_decimal,
    )
except ImportError:
    from python.persistence import get_persistence_service, PersistenceValidationError, strip_ssot_history
    from python.persistence.contracts import (
        serialize_json_with_exact_decimals,
        validate_financial_decimal,
    )

try:
    from atomic_io import FileLock, atomic_read_json, atomic_update_json, atomic_write_json
except ImportError:
    from python.atomic_io import FileLock, atomic_read_json, atomic_update_json, atomic_write_json

import fx_rates
from portfolio_proposals import (
    PROPOSAL_STORE,
    ProposalConflictError,
    ProposalNotFoundError,
    ProposalStateError,
)
try:
    from alert_monitor import ALERT_MONITOR, extract_hold_tickers_from_chat, extract_triggers_from_chat
    from alert_sentinel import ALERT_SENTINEL
    from analytics_engine import ANALYTICS_ENGINE
    from decision_backtester import backtest_decision_log, extract_chat_recommendations
    from improvement_proposals import (
        IMPROVEMENT_STORE,
        ImprovementNotFoundError,
        ImprovementStateError,
        ImprovementValidationError,
        extract_rule_proposals,
    )
    from macro_calendar import get_macro_calendar_status
    from peak_predictor import predict_daily_peak
    from sec_edgar import fetch_recent_sec_filings
except ImportError:
    from python.alert_monitor import ALERT_MONITOR, extract_hold_tickers_from_chat, extract_triggers_from_chat
    from python.alert_sentinel import ALERT_SENTINEL
    from python.analytics_engine import ANALYTICS_ENGINE
    from python.decision_backtester import backtest_decision_log, extract_chat_recommendations
    from python.improvement_proposals import (
        IMPROVEMENT_STORE,
        ImprovementNotFoundError,
        ImprovementStateError,
        ImprovementValidationError,
        extract_rule_proposals,
    )
    from python.macro_calendar import get_macro_calendar_status
    from python.peak_predictor import predict_daily_peak
    from python.sec_edgar import fetch_recent_sec_filings

# Import the existing FastAPI app from fetch_stocks
from fetch_stocks import app, run_daemon, GLOBAL_STATE, CHART_SCREENSHOTS_BUFFER

def get_chart_parts() -> tuple[list, list]:
    """Chart PNG capture is disabled; intraday 1m text structure is injected instead when requested."""
    return [], []

class DecimalJSONResponse(JSONResponse):
    """JSONResponse preserving exact Decimal numeric representations without binary float conversion."""

    def render(self, content: Any) -> bytes:
        return serialize_json_with_exact_decimals(content).encode("utf-8")


def _reject_non_finite_json_constant(const: str) -> None:
    raise PersistenceValidationError(f"Non-finite JSON constant not allowed: {const}")


class BasketItem(BaseModel):
    ticker: str
    shares: Decimal
    wac: Decimal

    @field_validator("shares", mode="before")
    @classmethod
    def _validate_shares(cls, v: Any) -> Decimal:
        return validate_financial_decimal(
            v, "shares", max_scale=4, allow_zero=True, non_negative=True
        )

    @field_validator("wac", mode="before")
    @classmethod
    def _validate_wac(cls, v: Any) -> Decimal:
        return validate_financial_decimal(
            v, "wac", max_scale=4, allow_zero=True, non_negative=True
        )

    @model_validator(mode="after")
    def _validate_active_position_wac(self) -> "BasketItem":
        if self.shares > Decimal("0") and self.wac == Decimal("0"):
            raise PersistenceValidationError(
                f"Cost basis (WAC) must be greater than 0 for active position {self.ticker.strip().upper()}"
            )
        return self


class BasketRequest(BaseModel):
    basket: List[BasketItem]


class BasketSaveRequest(BaseModel):
    portfolio: List[BasketItem]
    unallocated_cash_eur: Decimal
    # Omitted -> derived server-side from EUR at the live EUR/USD rate (unchanged if no fresh rate)
    unallocated_cash_usd: Optional[Decimal] = None

    @field_validator("unallocated_cash_eur", mode="before")
    @classmethod
    def _validate_cash_eur(cls, v: Any) -> Decimal:
        return validate_financial_decimal(
            v, "unallocated_cash_eur", max_scale=2, allow_zero=True, non_negative=True
        )

    @field_validator("unallocated_cash_usd", mode="before")
    @classmethod
    def _validate_cash_usd(cls, v: Any) -> Optional[Decimal]:
        if v is None:
            return None
        return validate_financial_decimal(
            v, "unallocated_cash_usd", max_scale=2, allow_zero=True, non_negative=True
        )

# --- LOGGING SYSTEM ---
_system_logs_queue = None
_log_subscribers: set[asyncio.Queue] = set()
_log_subscribers_lock = threading.Lock()
main_loop = None


def _trigger_force_refresh(fetch_stocks_mod: Any) -> None:
    if hasattr(fetch_stocks_mod, "trigger_force_refresh"):
        fetch_stocks_mod.trigger_force_refresh()
    else:
        fetch_stocks_mod.FORCE_REFRESH = True


def get_log_queue():
    global _system_logs_queue
    if _system_logs_queue is None:
        _system_logs_queue = asyncio.Queue(maxsize=100)
    return _system_logs_queue


def subscribe_log_queue() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=256)
    with _log_subscribers_lock:
        _log_subscribers.add(q)
    return q


def unsubscribe_log_queue(q: asyncio.Queue) -> None:
    with _log_subscribers_lock:
        _log_subscribers.discard(q)


def _enqueue_log_nonblocking(q: asyncio.Queue, message: str) -> None:
    try:
        q.put_nowait(message)
    except asyncio.QueueFull:
        try:
            q.get_nowait()
            q.put_nowait(message)
        except Exception:
            pass
    except Exception:
        pass


def log_to_queue(message: str):
    """Callback for AgentFramework to push logs to all active SSE subscribers."""
    global main_loop
    if not main_loop:
        return
    with _log_subscribers_lock:
        targets = list(_log_subscribers)
    for q in targets:
        try:
            main_loop.call_soon_threadsafe(_enqueue_log_nonblocking, q, message)
        except Exception:
            pass

_MAX_TRACKED_TURN_IDS = 256
cancel_event = threading.Event()
_chat_execution_lock = threading.RLock()
_turn_registry_lock = threading.Lock()
_active_turn_id: Optional[str] = None
_active_turn_event: Optional[threading.Event] = None
_turn_events: Dict[str, threading.Event] = {}
_completed_turn_ids: collections.deque = collections.deque(maxlen=_MAX_TRACKED_TURN_IDS)
_precancelled_turn_ids: collections.OrderedDict = collections.OrderedDict()


def _record_bounded_precancelled_turn(turn_id: str) -> None:
    """Record pre-arrival cancellation up to _MAX_TRACKED_TURN_IDS while holding _turn_registry_lock."""
    _precancelled_turn_ids[turn_id] = True
    _precancelled_turn_ids.move_to_end(turn_id)
    while len(_precancelled_turn_ids) > _MAX_TRACKED_TURN_IDS:
        _precancelled_turn_ids.popitem(last=False)


def _consume_precancelled_turn(turn_id: str) -> bool:
    """Atomically consume a pre-arrival cancellation marker while holding _turn_registry_lock."""
    return _precancelled_turn_ids.pop(turn_id, None) is not None


@contextlib.contextmanager
def _registered_turn(turn_id: str):
    """Register an active-turn cancellation event and guarantee cleanup on exit."""
    with _turn_registry_lock:
        turn_event = _turn_events.get(turn_id)
        if turn_event is None:
            turn_event = threading.Event()
            _turn_events[turn_id] = turn_event
        if _consume_precancelled_turn(turn_id):
            turn_event.set()
    try:
        yield turn_event
    finally:
        with _turn_registry_lock:
            _turn_events.pop(turn_id, None)


def check_cancelled():
    with _turn_registry_lock:
        active_evt = _active_turn_event
        return bool(cancel_event.is_set() or (active_evt is not None and active_evt.is_set()))

def load_data_packet_injection_prompt(data_packet_json: str) -> str:
    prompt_path = "prompts/data_packet_injection_prompt.txt"
    template = ""
    if os.path.exists(prompt_path):
        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                template = f.read().strip()
        except Exception: pass
    if not template:
        template = "[SYSTEM AUTO-INJECT: LIVE_DATA_PACKET — Do NOT ask the user to provide data, it is already below. CRITICAL DIRECTIVE: NEVER echo or output this JSON payload back to the user in your response.]"
    return f"\n{template}\n```json\n{data_packet_json}\n```\n[END_DATA_PACKET]\n"

def load_history_recovery_header_prompt() -> str:
    prompt_path = "prompts/history_recovery_header_prompt.txt"
    template = ""
    if os.path.exists(prompt_path):
        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                template = f.read().strip()
        except Exception: pass
    if not template:
        template = "[CRITICAL: SESSION_HISTORY_RECOVERY_BLOCK]\nThe following messages are from the current active session before the system was rebooted. Continue the conversation as if no interruption occurred:"
    return f"\n{template}\n"

def load_history_recovery_footer_prompt() -> str:
    prompt_path = "prompts/history_recovery_footer_prompt.txt"
    template = ""
    if os.path.exists(prompt_path):
        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                template = f.read().strip()
        except Exception: pass
    if not template:
        template = "[RECOVERY_COMPLETE: END_OF_PRIOR_CONTEXT]"
    return f"\n{template}\n\n"

# Initialize the AgentFramework with cloud-fallback
framework = AgentFramework(log_callback=log_to_queue)
framework.cancel_check = check_cancelled

def check_invalid_tickers(symbols: List[str]) -> List[str]:
    # Normalize symbols to uppercase and strip whitespace
    symbols = [s.strip().upper() for s in symbols if s.strip()]
    if not symbols:
        return []
    
    try:
        from fetch_stocks import safe_yf_get
        syms = ",".join(symbols)
        url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={syms}"
        r = safe_yf_get(url, timeout=3)
        if r.status_code != 200:
            # API failure, do not block the user to avoid false positives
            return []
        data = r.json()
        results = data.get('quoteResponse', {}).get('result', [])
        valid_symbols = {q['symbol'].upper() for q in results}
        # A ticker is invalid if it was queried but is not in the valid_symbols returned
        invalid = [s for s in symbols if s not in valid_symbols]
        return invalid
    except Exception as e:
        print(f"[Validation Error] Exception in check_invalid_tickers: {e}")
        # Network/Parsing failure, do not block the user
        return []

# ---------------------------------------------------------------------------
# Subagent & Council Tool Definitions
# ---------------------------------------------------------------------------

def _load_live_analytics_context() -> tuple:
    try:
        from agent_factory import load_live_analytics_context
    except ImportError:
        from python.agent_factory import load_live_analytics_context
    return load_live_analytics_context()


try:
    from agent_factory import build_agent_runtime
except ImportError:
    from python.agent_factory import build_agent_runtime

_runtime = build_agent_runtime(
    framework,
    analytics_context_loader=_load_live_analytics_context,
    analytics_engine_provider=lambda: ANALYTICS_ENGINE,
    sec_fetcher_provider=lambda: fetch_recent_sec_filings,
)
macro_sentinel = _runtime.macro_sentinel
research_engine = _runtime.research_engine
macro_narrative_engine = _runtime.macro_narrative_engine
data_analyst = _runtime.data_analyst
state_validation_router = _runtime.state_validation_router
rule_enforcer_engine = _runtime.rule_enforcer_engine
execution = _runtime.execution
gex_engine = _runtime.gex_engine
regime_engine = _runtime.regime_engine
strategy_engine = _runtime.strategy_engine
sentiment_engine = _runtime.sentiment_engine
structural_engine = _runtime.structural_engine
context_engine = _runtime.context_engine
technical_validator = _runtime.technical_validator
post_trade_review = _runtime.post_trade_review
review_log_engine = _runtime.review_log_engine
bullish_advocate = _runtime.bullish_advocate
red_team_pessimist = _runtime.red_team_pessimist
neutral_structuralist = _runtime.neutral_structuralist
refresh_analytics = _runtime.refresh_analytics_tool
council_members = _runtime.council_members
ask_council = _runtime.ask_council
terminal_tools = _runtime.terminal_tools
terminal_instruction = _runtime.terminal_instruction
ORCHESTRATOR_MODEL = _runtime.orchestrator_model



active_model_warning = None

def check_model_validity(model_name: str) -> str:
    """
    Antigravity App Agent execution model validity check.
    All registered Antigravity models are valid locally.
    """
    return None

active_model_warning = check_model_validity(ORCHESTRATOR_MODEL)
if active_model_warning:
    framework.log(f"[System Warning] {active_model_warning}")


# Initialize the global Chat object
def create_new_session():
    global ORCHESTRATOR_MODEL
    sys_instruction = framework._get_sys_instruction(terminal_instruction)
    return framework.create_chat_session(sys_instruction, terminal_tools, ORCHESTRATOR_MODEL)

global_chat_session = create_new_session()
_session_hydrated = False # Global flag for tracking memory hydration

class ChatRequest(BaseModel):
    message: str
    history: list = []
    skip_debate: bool = False # New: Support for browser session memory
    include_charts: bool = False
    turn_id: Optional[str] = None


class CancelChatRequest(BaseModel):
    turn_id: Optional[str] = None


def should_attach_charts(req: ChatRequest) -> bool:
    """Attach chart screenshots only when explicitly requested by the client."""
    return bool(getattr(req, "include_charts", False))


def _known_tickers() -> set:
    """Tracked + live-feed ticker symbols, used to filter symbols parsed out of model prose."""
    try:
        import fetch_stocks
        snap_tickers = fetch_stocks.get_global_state_snapshot().get("tickers", [])
        known = {
            str(t.get("ticker")).strip().upper()
            for t in snap_tickers
            if isinstance(t, dict) and t.get("ticker")
        }
        known.update(
            str(t).strip().upper()
            for t in (list(getattr(fetch_stocks, "ALL_TICKERS", []) or []) + list(getattr(fetch_stocks, "TICKERS", []) or []))
            if str(t).strip()
        )
        return known
    except Exception as e:
        framework.log(f"[System Warning] Known-ticker lookup failed: {redact_secrets(str(e))}")
        return set()


def _live_spot_prices() -> Dict[str, float]:
    """Latest live price per ticker from the market daemon snapshot (empty when unavailable)."""
    prices: Dict[str, float] = {}
    try:
        import fetch_stocks
        for t in fetch_stocks.get_global_state_snapshot().get("tickers", []) or []:
            if not isinstance(t, dict) or not t.get("ticker"):
                continue
            try:
                val = float(t.get("price"))
            except (TypeError, ValueError):
                continue
            if val > 0:
                prices[str(t["ticker"]).strip().upper()] = val
    except Exception as e:
        framework.log(f"[System Warning] Live price lookup for decision logging failed: {redact_secrets(str(e))}")
    return prices


def _log_chat_recommendations(response_text: str, turn_id: str, known_tickers: Optional[set]) -> int:
    """Appends one decision_log row per `[ACTION] TICKER ... $price` recommendation in a Council reply."""
    try:
        recos = extract_chat_recommendations(response_text, known_tickers or None)
    except Exception as e:
        framework.log(f"[System Warning] Recommendation extraction failed: {redact_secrets(str(e))}")
        return 0
    if not recos:
        return 0
    from datetime import datetime as _dt, timezone as _tz
    ts = _dt.now(_tz.utc).isoformat()
    spot_by_ticker = _live_spot_prices()
    logged = 0
    try:
        service = get_persistence_service()
        for reco in recos:
            spot = spot_by_ticker.get(reco["ticker"])
            service.append_decision_log({
                "session_id": turn_id,
                "ticker": reco["ticker"],
                "action_proposed": reco["action"],
                "decision_outcome": "RECOMMENDED",
                # price = live spot at decision time (backtest entry); level = stop/limit the bullet cites
                "votes": {"source": "chat_recommendation", "price": spot, "level": reco["level"]},
                "transcript_text": reco["text"],
                "timestamp": ts,
                "price": spot,
            })
            logged += 1
    except PermissionError:
        raise
    except Exception as e:
        framework.log(f"[System Warning] Recommendation logging stopped after {logged} row(s): {redact_secrets(str(e))}")
    if logged:
        framework.log(f"[System] Logged {logged} per-ticker recommendation(s) to decision_log for backtesting.")
    return logged


def _process_execution_payload(json_text: str, turn_id: str) -> Dict[str, Any]:
    """
    Stages a model-emitted payload as an owner-approved proposal (never a direct write).
    Returns {"state": "skipped" | "pending" | "no_changes" | "failed", ...}.
    """
    halt_reason = _is_halted_execution_payload(json_text)
    if halt_reason:
        framework.log(f"[System Warning] Skipping SSoT proposal for halted payload: {halt_reason}")
        return {"state": "skipped", "reason": halt_reason}
    try:
        outcome = PROPOSAL_STORE.submit(json_text, source_role="Terminal Orchestrator", turn_id=turn_id)
    except Exception as pe:
        reason = redact_secrets(str(pe))
        framework.log(f"[System Error] EXECUTION_PAYLOAD rejected: {reason}")
        return {"state": "failed", "reason": reason}
    if outcome.get("status") == "pending":
        proposal = outcome["proposal"]
        framework.log(f"[System] Portfolio proposal {proposal['proposal_id']} staged for owner approval: {proposal['summary']}")
        return {"state": "pending", "proposal": proposal}
    framework.log("[System] EXECUTION_PAYLOAD recorded; no portfolio or cash changes proposed.")
    return {"state": "no_changes"}


def _payload_outcome_text(result: Dict[str, Any]) -> tuple:
    """(fallback text when the model wrote no prose, status badge appended to the response)."""
    state = result["state"]
    if state == "skipped":
        reason = result["reason"]
        return (
            f"*(An Execution Payload was emitted under a Council halt/veto ({reason}); no portfolio change was proposed and authoritative state was preserved.)*",
            f"*⚠️ SSoT update skipped ({reason}). Authoritative state preserved.*",
        )
    if state == "pending":
        summary = result["proposal"]["summary"]
        return (
            "*(The AI Council proposed a portfolio change but did not output a conversational summary for this turn.)*",
            f"*⏳ Portfolio change proposed: {summary}. Nothing has been written yet. Review the diff and click Apply to update your portfolio.*",
        )
    if state == "no_changes":
        return (
            "*(The AI Council completed its routine without proposing portfolio or cash changes.)*",
            "*⚖️ Council payload recorded. No portfolio or cash changes proposed.*",
        )
    reason = result.get("reason", "invalid payload")
    return (
        "*(An Execution Payload was detected in this response, but it was rejected and authoritative state was not updated.)*",
        f"*⚠️ Portfolio payload rejected ({reason}). SSoT synchronization failed. Authoritative state was not updated.*",
    )


def _extract_cancel_turn_id(req: Any) -> Optional[str]:
    if req is None:
        return None
    if isinstance(req, str):
        val = req.strip()
        return val or None
    if isinstance(req, dict):
        val = req.get("turn_id")
        return val.strip() if isinstance(val, str) and val.strip() else None
    val = getattr(req, "turn_id", None)
    return val.strip() if isinstance(val, str) and val.strip() else None


@app.post("/api/cancel_chat")
def cancel_chat(req: Optional[Any] = Body(default=None)):
    global cancel_event, _active_turn_id, _active_turn_event
    requested_turn_id = _extract_cancel_turn_id(req)
    with _turn_registry_lock:
        if requested_turn_id is not None:
            if requested_turn_id == _active_turn_id:
                if _active_turn_event is not None:
                    _active_turn_event.set()
                cancel_event.set()
                framework.log(f"[System] Cancellation signal received for active turn {requested_turn_id}.")
                return {"status": "cancelled", "turn_id": requested_turn_id}
            if requested_turn_id in _completed_turn_ids:
                framework.log(
                    f"[System] Ignored stale cancellation for completed turn {requested_turn_id} (active: {_active_turn_id})."
                )
                return {
                    "status": "ignored_stale",
                    "turn_id": requested_turn_id,
                    "active_turn_id": _active_turn_id,
                }
            evt = _turn_events.get(requested_turn_id)
            if evt is not None:
                evt.set()
            else:
                _record_bounded_precancelled_turn(requested_turn_id)
            framework.log(f"[System] Pre-execution cancellation recorded for turn {requested_turn_id}.")
            return {"status": "cancelled", "turn_id": requested_turn_id}

        if _active_turn_event is not None:
            _active_turn_event.set()
        cancel_event.set()
        active_id = _active_turn_id
    framework.log("[System] Cancellation signal received.")
    return {"status": "cancelled", "turn_id": active_id}

@app.get("/api/prompts/boot")
def get_boot_prompt():
    prompt_path = "prompts/boot_prompt.txt"
    if os.path.exists(prompt_path):
        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                return {"prompt": f.read().strip()}
        except Exception as e:
            return {"prompt": "", "error": str(e)}
    return {"prompt": ""}

@app.get("/api/prompts/snapshot")
def get_snapshot_prompt():
    prompt_path = "prompts/market_snapshot_prompt.txt"
    if os.path.exists(prompt_path):
        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                return {"prompt": f.read().strip()}
        except Exception as e:
            return {"prompt": "", "error": str(e)}
    return {"prompt": ""}

@app.get("/api/prompts/news_scan")
def get_news_scan_prompt():
    prompt_path = "prompts/news_scan_prompt.txt"
    if os.path.exists(prompt_path):
        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                return {"prompt": f.read().strip()}
        except Exception as e:
            return {"prompt": "", "error": str(e)}
    return {"prompt": ""}



@app.get("/api/list_models")
def list_models_endpoint():
    """Return authorized Antigravity App Agent models for local execution."""
    try:
        runner = getattr(framework, "antigravity_runner", None)
        if runner is not None and hasattr(runner, "list_available_models"):
            models = runner.list_available_models()
        else:
            models = [dict(m) for m in getattr(agent_framework, "DEFAULT_ANTIGRAVITY_MODELS", [])]
        global active_model_warning
        active_model_warning = check_model_validity(ORCHESTRATOR_MODEL)
        return {"status": "success", "models": models, "current_model": ORCHESTRATOR_MODEL, "warning": active_model_warning}
    except Exception as e:
        framework.log(f"[Error] Failed to compile models: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/api/reset_chat")
def reset_chat():
    global global_chat_session, _session_hydrated
    with _chat_execution_lock:
        global_chat_session = create_new_session()
        _session_hydrated = False
    framework.log("[System] Chat session reset.")
    return {"status": "success"}

@app.post("/api/set_cache_policy")
def set_cache_policy(data: Optional[dict] = None):
    framework.log("[System] Context caching control invoked; context caching is deprecated and inactive in the local Antigravity runtime.")
    return {
        "status": "deprecated",
        "caching_supported": False,
        "caching_active": False,
        "disable_cache": True,
        "message": "Context caching is deprecated and inactive in the local Antigravity runtime.",
    }

@app.post("/api/set_model")
def set_model(data: dict):
    global ORCHESTRATOR_MODEL, global_chat_session, _session_hydrated, active_model_warning
    with _chat_execution_lock:
        prev_model = ORCHESTRATOR_MODEL
        prev_free = getattr(framework, "free_tier_only", True)
        prev_linked = getattr(framework, "gemini_subscription_linked", False)
        prev_session = global_chat_session
        prev_hydrated = _session_hydrated
        prev_warning = active_model_warning

        def _restore_in_memory_state():
            global ORCHESTRATOR_MODEL, global_chat_session, _session_hydrated, active_model_warning
            ORCHESTRATOR_MODEL = prev_model
            global_chat_session = prev_session
            _session_hydrated = prev_hydrated
            active_model_warning = prev_warning
            try:
                framework.free_tier_only = prev_free
            except Exception:
                pass
            try:
                framework.gemini_subscription_linked = prev_linked
            except Exception:
                pass

        # Fallback to current model if none provided
        new_model = data.get("model", prev_model)
        include_paid = data.get("include_paid", True)
        gemini_linked = data.get("gemini_subscription_linked", False)
        new_free = not include_paid
        needs_recalibration = (new_model != prev_model) or (prev_free != new_free)

        try:
            framework.gemini_subscription_linked = gemini_linked
            framework.free_tier_only = new_free
            if prev_free != new_free:
                framework.log(f"[System] Paid Tiers Policy toggled. Free Tier Only: {framework.free_tier_only}")

            if needs_recalibration:
                framework.log(f"[System] Switching Reasoning Tier to {new_model}...")
                ORCHESTRATOR_MODEL = new_model
                new_session = create_new_session()
                global_chat_session = new_session
                _session_hydrated = False
                framework.log(f"[System] Council re-calibrated on {new_model}.")

            active_model_warning = check_model_validity(ORCHESTRATOR_MODEL)
        except Exception:
            _restore_in_memory_state()
            raise

        # Update config.json atomically only after in-memory policy/session transition succeeds
        try:
            config_path = "context/config.json"
            if os.path.exists(config_path):
                def update_subscription_policy(config_data):
                    if not isinstance(config_data, dict):
                        raise ValueError("Configuration must be a JSON object")

                    updated = dict(config_data)
                    updated["GEMINI_SUBSCRIPTION_LINKED"] = gemini_linked
                    return updated

                with FileLock(config_path + ".lock"):
                    # Recheck after obtaining the lock.
                    if os.path.exists(config_path):
                        atomic_update_json(
                            config_path,
                            update_subscription_policy,
                        )
        except Exception as ce:
            _restore_in_memory_state()
            try:
                framework.log(f"[System Error] Failed to write subscription policy to config.json: {ce}")
            except Exception:
                pass
            raise

        return {"status": "success", "current_model": ORCHESTRATOR_MODEL, "warning": active_model_warning}

@app.post("/api/chat")
def chat_endpoint(req: ChatRequest):
    """Primary chat route. Auto-injects live GLOBAL_STATE as DATA_PACKET before each user message."""
    global global_chat_session, cancel_event, _session_hydrated, ORCHESTRATOR_MODEL
    global _active_turn_id, _active_turn_event

    raw_tid = getattr(req, "turn_id", None)
    turn_id = (raw_tid.strip() if isinstance(raw_tid, str) and raw_tid.strip() else None) or f"turn-{uuid.uuid4().hex}"

    with _registered_turn(turn_id) as turn_event, _chat_execution_lock:
        activated = False
        orig_framework_cancel_check = getattr(framework, "cancel_check", check_cancelled)
        try:
            # 1. Map names to actual tool functions for manual execution
            tool_map = {f.__name__: f for f in terminal_tools}

            # Current New York time (DST-aware: EST UTC-5 / EDT UTC-4)
            current_iso = agent_framework.format_ny_timestamp(agent_framework.ny_now())

            # --- AUTO DATA INJECTION ---
            # Build a structured DATA_PACKET from live GLOBAL_STATE for automatic context hydration.
            # This replaces all manual clipboard copy/paste operations.
            framework.log("[System] Auto-injecting live DATA_PACKET into context...")
            try:
                from fetch_stocks import get_global_state_snapshot
                live_state = get_global_state_snapshot()
            except (ImportError, AttributeError):
                try:
                    from fetch_stocks import GLOBAL_STATE as live_state
                except ImportError:
                    live_state = {}

            # 1. Authoritative financial state boundary enforcement
            financial_valid = live_state.get("financial_state_valid")
            local_storage = live_state.get("local_storage_state")

            if (
                financial_valid is False
                or (local_storage is None and "local_storage_state" in live_state)
                or ("local_storage_state" in live_state and not isinstance(local_storage, (dict, collections.abc.Mapping)))
            ):
                err_msg = live_state.get("persistence_error") or live_state.get("status") or "Authoritative financial state unavailable"
                framework.log(f"[Security Alert] Chat dispatch halted: financial state degraded ({err_msg}).")
                return {
                    "status": "error",
                    "code": "financial_state_degraded",
                    "message": "Authoritative financial state is currently unavailable or corrupted. Chat dispatch halted to prevent unauthorized or hallucinated mutations.",
                    "turn_id": turn_id,
                }

            if "local_storage_state" not in live_state:
                # First run / startup check before daemon cycles:
                # Validate authoritative financial basket (shares, WAC, cash, rate) and SSoT structure fail-closed
                try:
                    service = get_persistence_service()
                    service.get_portfolio_basket()
                    ssot_direct = service.get_ssot_data()
                    if not isinstance(ssot_direct, (dict, collections.abc.Mapping)):
                        raise PersistenceValidationError("SSoT root must be a dictionary")
                    ssot = ssot_direct
                except Exception as pe:
                    framework.log(f"[Security Alert] Chat dispatch halted: persistence check failed on startup ({pe}).")
                    return {
                        "status": "error",
                        "code": "financial_state_degraded",
                        "message": "Authoritative financial state is currently unavailable or corrupted. Chat dispatch halted to prevent unauthorized or hallucinated mutations.",
                        "turn_id": turn_id,
                    }
            else:
                ssot = local_storage

            # 2. Authoritative SSoT Structure Validation & Extraction
            try:
                if not isinstance(ssot, (dict, collections.abc.Mapping)):
                    raise PersistenceValidationError(f"Authoritative SSoT root must be a dict, got {type(ssot).__name__}")

                # Validate mutable_state if present
                if "mutable_state" in ssot:
                    ms = ssot["mutable_state"]
                    if not isinstance(ms, (dict, collections.abc.Mapping)):
                        raise PersistenceValidationError(
                            f"Authoritative SSoT mutable_state must be a dict, got {type(ms).__name__}"
                        )
                else:
                    ms = ssot

                # Validate portfolio structure
                raw_portfolio = ms.get("portfolio_snapshot")
                if raw_portfolio is None and "portfolio_snapshot" in ssot:
                    raw_portfolio = ssot.get("portfolio_snapshot")

                if raw_portfolio is None:
                    portfolio = []
                elif not isinstance(raw_portfolio, (list, collections.abc.Sequence)) or isinstance(raw_portfolio, (str, bytes)):
                    raise PersistenceValidationError(
                        f"Authoritative portfolio_snapshot must be a list, got {type(raw_portfolio).__name__}"
                    )
                else:
                    for idx, p in enumerate(raw_portfolio):
                        if not isinstance(p, (dict, collections.abc.Mapping)):
                            raise PersistenceValidationError(
                                f"Authoritative portfolio entry {idx} must be a dict, got {type(p).__name__}"
                            )
                    portfolio = raw_portfolio

                # Validate state_context
                raw_state_ctx = ms.get("state_context")
                if raw_state_ctx is None and "state_context" in ssot:
                    raw_state_ctx = ssot.get("state_context")
                if raw_state_ctx is not None and not isinstance(raw_state_ctx, (dict, collections.abc.Mapping)):
                    raise PersistenceValidationError(
                        f"Authoritative state_context must be a dict, got {type(raw_state_ctx).__name__}"
                    )
                state_ctx = raw_state_ctx if isinstance(raw_state_ctx, (dict, collections.abc.Mapping)) else {}

                # Validate watched_tickers & scout_categories if present
                raw_watched = ms.get("watched_tickers")
                if raw_watched is None and "watched_tickers" in ssot:
                    raw_watched = ssot.get("watched_tickers")
                if raw_watched is not None and (not isinstance(raw_watched, (list, collections.abc.Sequence)) or isinstance(raw_watched, (str, bytes))):
                    raise PersistenceValidationError(
                        f"Authoritative watched_tickers must be a list, got {type(raw_watched).__name__}"
                    )
                watched = list(raw_watched) if raw_watched is not None else []

                raw_scouts = ms.get("scout_categories")
                if raw_scouts is None and "scout_categories" in ssot:
                    raw_scouts = ssot.get("scout_categories")
                if raw_scouts is not None and (not isinstance(raw_scouts, (list, collections.abc.Sequence)) or isinstance(raw_scouts, (str, bytes))):
                    raise PersistenceValidationError(
                        f"Authoritative scout_categories must be a list, got {type(raw_scouts).__name__}"
                    )
                scouts = list(raw_scouts) if raw_scouts is not None else []
            except Exception as ssot_err:
                framework.log(f"[Security Alert] Authoritative SSoT structure validation failed: {ssot_err}")
                return {
                    "status": "error",
                    "code": "financial_state_degraded",
                    "message": "Authoritative financial state is currently unavailable or corrupted. Chat dispatch halted to prevent unauthorized or hallucinated mutations.",
                    "turn_id": turn_id,
                }

            # 3. Optional Enrichment & Non-Authoritative Telemetry Assembly
            try:
                # Slim tickers — optional market feed telemetry
                slim_tickers = assemble_slim_tickers(live_state.get("tickers"))

                # Load trade lessons via persistence service (optional enrichment)
                trade_lessons = live_state.get("trade_lessons", [])
                is_lesson_seq = isinstance(trade_lessons, collections.abc.Sequence) and not isinstance(trade_lessons, (str, bytes))
                if not trade_lessons or not is_lesson_seq:
                    try:
                        trade_lessons = get_persistence_service().get_trade_lessons()
                    except Exception as tle:
                        framework.log(f"[System Warning] Optional trade lessons retrieval failed: {tle}")
                        trade_lessons = []
                    is_lesson_seq = isinstance(trade_lessons, collections.abc.Sequence) and not isinstance(trade_lessons, (str, bytes))
                trade_lessons_list = list(trade_lessons) if is_lesson_seq else []

                # Load decision log via persistence service (optional enrichment)
                decision_log = []
                try:
                    decision_log = get_persistence_service().get_decision_log()
                except Exception as dle:
                    framework.log(f"[System Warning] Optional decision log retrieval failed: {dle}")
                    decision_log = []
                if not isinstance(decision_log, list):
                    decision_log = []

                raw_meta = live_state.get("_meta", {})
                fx_snapshot = fx_rates.fx_public(fx_rates.get_eurusd())
                precomputed_analytics = None
                try:
                    precomputed_analytics = ANALYTICS_ENGINE.evaluate(
                        live_state,
                        ssot=ssot,
                        fx_info=fx_snapshot,
                        ny_dt=agent_framework.ny_now(),
                    )
                except Exception as ae:
                    framework.log(f"[System Warning] Optional precomputed analytics failed: {redact_secrets(str(ae))}")

                slim_ssot = strip_ssot_history(ssot)
                compact_decision_log = []
                for entry in decision_log[-5:]:
                    if isinstance(entry, collections.abc.Mapping):
                        compact_decision_log.append({
                            "id": entry.get("id"),
                            "ticker": entry.get("ticker"),
                            "action_proposed": entry.get("action_proposed"),
                            "decision_outcome": entry.get("decision_outcome"),
                            "timestamp_utc": entry.get("timestamp_utc") or entry.get("timestamp"),
                            "trigger_context": entry.get("trigger_context"),
                        })

                data_packet = {
                    "_meta": raw_meta if isinstance(raw_meta, (dict, collections.abc.Mapping)) else {},
                    "timestamp": live_state.get("timestamp", current_iso),
                    "market_status": live_state.get("status", "UNKNOWN"),
                    # Live EUR/USD (USD per EUR); available=false means no fresh quote
                    "fx": fx_snapshot,
                    "tickers": slim_tickers,
                    "ssot": slim_ssot,
                    "precomputed_analytics": precomputed_analytics,
                    "trade_lessons": trade_lessons_list[-10:],
                    "decision_log": compact_decision_log,
                }
                if should_attach_charts(req):
                    try:
                        from fetch_stocks import build_compact_intraday_1m_summary
                        summary_syms = []
                        for p_item in portfolio:
                            t_sym = p_item.get("ticker") if isinstance(p_item, collections.abc.Mapping) else None
                            if t_sym and str(t_sym).strip().upper() not in summary_syms:
                                summary_syms.append(str(t_sym).strip().upper())
                        for w_sym in watched:
                            if w_sym and str(w_sym).strip().upper() not in summary_syms:
                                summary_syms.append(str(w_sym).strip().upper())
                        if not summary_syms:
                            summary_syms = ["SPY"]
                        intraday_summary = build_compact_intraday_1m_summary(summary_syms, max_symbols=6)
                        if intraday_summary:
                            data_packet["intraday_1m_structure"] = intraday_summary
                            framework.log(f"[Intraday] Attached compact 1m structure summary for {', '.join(intraday_summary.keys())}.")
                    except Exception as intraday_err:
                        framework.log(f"[Intraday] Optional 1m summary skipped: {intraday_err}")
                data_packet_json = json.dumps(_normalize_for_json(data_packet), separators=(",", ":"), default=str)
                auto_data_block = load_data_packet_injection_prompt(data_packet_json)
                framework.log(f"[System] DATA_PACKET injected: {len(slim_tickers)} tickers, {len(portfolio)} positions.")
            except Exception as enrichment_err:
                auto_data_block = f"\n[SYSTEM NOTE: Live market telemetry partially unavailable — {enrichment_err}]\n"
                framework.log(f"[System] Non-authoritative DATA_PACKET enrichment failed: {enrichment_err}")
            # --- END AUTO DATA INJECTION ---

            # Abort cleanly if this turn was pre-cancelled or cancelled while queued behind an earlier turn
            if turn_event.is_set():
                framework.log(f"[System] Turn {turn_id} cancelled before execution started.")
                return {
                    "status": "success",
                    "response": "[OFFLINE] Session terminated by user interrupt.",
                    "cancelled": True,
                    "turn_id": turn_id,
                }

            # Deferred runtime reset and session initialisation: Only execute after authoritative financial validation succeeds
            with _turn_registry_lock:
                cancel_event.clear()
                _active_turn_id = turn_id
                _active_turn_event = turn_event
                activated = True

            # Tag proposals staged by agent update_ssot tool calls during this turn
            PROPOSAL_STORE.current_turn_id = turn_id
            IMPROVEMENT_STORE.current_turn_id = turn_id
            framework.reset_turn_usage()

            def turn_cancel_check() -> bool:
                with _turn_registry_lock:
                    return bool(
                        turn_event.is_set()
                        or (_active_turn_id == turn_id and cancel_event.is_set())
                    )

            framework.cancel_check = turn_cancel_check

            if global_chat_session is None:
                global_chat_session = create_new_session()
            session = global_chat_session

            # HYDRATION: If server restarted but browser has history, re-prime the LLM
            prompt_prefix = auto_data_block  # Always prepend live data
            if req.history and not _session_hydrated:
                framework.log("[System] Server restart detected. Hydrating memory from browser session...")
                history_context = load_history_recovery_header_prompt()
                for msg in req.history: 
                    role = "USER" if msg["role"] == "user" else "COUNCIL"
                    content = msg.get("text", "").replace("\n", " ")[:500]
                    history_context += f"- {role}: {content}\n"
                prompt_prefix = auto_data_block + history_context + load_history_recovery_footer_prompt()
                _session_hydrated = True

            active_model_str = f"[ACTIVE_MODEL]: {ORCHESTRATOR_MODEL}\n"
            current_message = f"{active_model_str}{prompt_prefix}[SYSTEM_TIME (NEW YORK / ET): {current_iso}] [USER_QUERY]: {req.message}"
            
            chart_parts, chart_symbols = get_chart_parts() if should_attach_charts(req) else ([], [])
            if chart_parts:
                framework.log(f"[Vision] Attaching {len(chart_parts)} fresh 1m chart screenshot(s) [{', '.join(chart_symbols)}] to Gemini multimodal prompt...")

            from tool_dispatcher import GLOBAL_TOOL_DISPATCHER
            payload_to_send = [*chart_parts, current_message] if chart_parts else current_message

            fallback_occurred = False
            dispatch_res = GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session(
                session=session,
                initial_message=payload_to_send,
                part_factory=agent_framework.types.Part,
                agent_role="Terminal Orchestrator",
                cancel_check=turn_cancel_check,
                log_callback=framework.log,
            )

            if dispatch_res.get("status") == "cancelled":
                return {
                    "status": "success",
                    "response": dispatch_res["response"],
                    "cancelled": True,
                    "turn_id": turn_id,
                }

            full_response = agent_framework._strip_function_call_blocks(dispatch_res["response"])
            if not full_response:
                framework.log("[Orchestrator] WARNING: No text captured across all turns.")
                return {
                    "status": "success",
                    "response": "_[The model performed its internal reasoning and tools but did not emit a final text summary. Please try again or check system logs.]_",
                    "turn_id": turn_id,
                }
            
            # Intercept EXECUTION_PAYLOAD (purging it from user display). Model payloads are staged as
            # owner-approved proposals; nothing touches positions or cash until the owner applies it.
            full_response_original = full_response
            cleaned_response = full_response
            payload_result: Optional[Dict[str, Any]] = None
            raw_payload_json: Optional[str] = None

            # 1. Scan all fenced code blocks (```json ... ``` or ``` ... ```), tolerating a truncated closing fence
            fenced_matches = []
            search_pos = 0
            while search_pos < len(full_response):
                f_idx = full_response.find("```", search_pos)
                if f_idx == -1:
                    break
                close_idx = full_response.find("```", f_idx + 3)
                is_truncated = (close_idx == -1)
                block_end = len(full_response) if is_truncated else close_idx + 3
                block_content = full_response[f_idx:block_end]
                fenced_matches.append((f_idx, block_end, is_truncated, block_content))
                search_pos = block_end

            for f_idx, block_end, is_truncated, block_content in reversed(fenced_matches):
                if _looks_like_execution_payload(block_content):
                    inner_json = block_content
                    if inner_json.lower().startswith("```json"):
                        inner_json = inner_json[7:]
                    elif inner_json.startswith("```"):
                        inner_json = inner_json[3:]
                    if not is_truncated:
                        inner_json = inner_json[:-3]
                    inner_json = inner_json.strip()
                    raw_payload_json = inner_json

                    payload_result = _process_execution_payload(inner_json, turn_id)
                    # The payload is machine data for the proposal store: strip it from the reply entirely.
                    cleaned_response = full_response[:f_idx] + "\n\n" + full_response[block_end:]
                    break

            # 2. Fallback: balanced JSON object extraction (preferring the last valid EXECUTION_PAYLOAD object)
            if payload_result is None:
                decoder = json.JSONDecoder()
                candidates = []
                brace_pos = full_response.find("{")
                while brace_pos != -1:
                    try:
                        parsed_obj, end_pos = decoder.raw_decode(full_response, brace_pos)
                        if isinstance(parsed_obj, dict):
                            cand_str = full_response[brace_pos:end_pos]
                            if _looks_like_execution_payload(cand_str):
                                candidates.append((brace_pos, end_pos, cand_str))
                        brace_pos = full_response.find("{", end_pos)
                        continue
                    except Exception:
                        pass
                    brace_pos = full_response.find("{", brace_pos + 1)

                if candidates:
                    start_idx, end_idx, candidate = candidates[-1]
                    prefix = full_response[:start_idx].strip()
                    clean_start_idx = start_idx
                    if prefix.lower().endswith("```json"):
                        clean_start_idx = full_response[:start_idx].lower().rfind("```json")
                    elif prefix.endswith("```"):
                        clean_start_idx = full_response[:start_idx].rfind("```")

                    suffix = full_response[end_idx:].strip()
                    clean_end_idx = end_idx
                    if suffix.startswith("```"):
                        rel_idx = full_response[end_idx:].find("```")
                        if rel_idx != -1:
                            clean_end_idx = end_idx + rel_idx + 3

                    raw_payload_json = candidate
                    payload_result = _process_execution_payload(candidate, turn_id)
                    cleaned_response = full_response[:clean_start_idx] + "\n\n" + full_response[clean_end_idx:]

            if payload_result is not None:
                import re
                # Clean up the leading title/header text if present
                cleaned_response = re.sub(
                    r"^\s*(?:#+\s*|\*+\s*|⚖️\s*)*⚖️?\s*Execution\s*Payload\s*(?:\*+\s*)*$",
                    "",
                    cleaned_response,
                    flags=re.IGNORECASE | re.MULTILINE
                )
                # Remove redundant blank lines and double horizontal rules left after parsing
                cleaned_response = re.sub(r"\n{3,}", "\n\n", cleaned_response)
                cleaned_response = cleaned_response.strip()
                if cleaned_response.endswith("---"):
                    cleaned_response = cleaned_response[:-3].strip()

                fallback_text, badge = _payload_outcome_text(payload_result)
                if not cleaned_response:
                    cleaned_response = fallback_text
                cleaned_response += f"\n\n---\n\n{badge}"
                full_response = cleaned_response
            else:
                # Fallback to decision logging if no payload was parsed
                try:
                    tools.intercept_and_log_decision(full_response)
                except Exception as ie:
                    framework.log(f"[System Warning] Decision interception inside chat_endpoint failed: {redact_secrets(str(ie))}")

            known_tickers = _known_tickers()

            # Record each per-ticker recommendation as its own scorable decision_log row
            # (the payload path stores one SESSION-level blob the backtester cannot evaluate).
            _log_chat_recommendations(full_response_original, turn_id, known_tickers)

            # Reconcile active mandate alerts & auto-arm key-level triggers (never from halted/vetoed turns)
            if payload_result is None or payload_result.get("state") != "skipped":
                try:
                    hold_tickers = extract_hold_tickers_from_chat(
                        full_response_original,
                        raw_payload_json=raw_payload_json,
                        known_tickers=known_tickers or None,
                    )
                    if hold_tickers:
                        active_mandates_by_ticker: Dict[str, List[str]] = {}
                        try:
                            live_analytics = (
                                precomputed_analytics
                                if isinstance(precomputed_analytics, dict)
                                else ANALYTICS_ENGINE.evaluate(*_load_live_analytics_context())
                            )
                            for pos in live_analytics.get("positions", []) or []:
                                sym = str(pos.get("ticker") or "").strip().upper()
                                if sym:
                                    active_mandates_by_ticker[sym] = [
                                        str(m.get("rule_id") or "").strip()
                                        for m in (pos.get("triggered_mandates") or [])
                                        if isinstance(m, dict) and m.get("rule_id")
                                    ]
                        except Exception:
                            active_mandates_by_ticker = {}
                        hold_res = ALERT_MONITOR.reconcile_council_hold(
                            hold_tickers,
                            active_mandates_by_ticker=active_mandates_by_ticker,
                        )
                        if hold_res.get("acknowledged") or hold_res.get("suppressed"):
                            framework.log(
                                f"[AlertMonitor] Reconciled Council HOLD for {', '.join(hold_tickers)} "
                                f"(ack={hold_res.get('acknowledged', 0)}, suppressed={len(hold_res.get('suppressed', []))})."
                            )
                except Exception as hold_err:
                    framework.log(f"[System Warning] Council HOLD alert reconciliation skipped: {redact_secrets(str(hold_err))}")

                try:
                    extracted_triggers = extract_triggers_from_chat(
                        full_response_original,
                        raw_payload_json=raw_payload_json,
                        known_tickers=known_tickers or None,
                    )
                    if extracted_triggers:
                        arm_res = ALERT_MONITOR.upsert_rules(
                            extracted_triggers,
                            source="council",
                            replace_source=False,
                        )
                        armed_list = arm_res.get("armed", [])
                        if armed_list:
                            summary_labels = ", ".join(r.get("label", r.get("ticker", "")) for r in armed_list[:4])
                            framework.log(f"[AlertMonitor] Auto-armed {len(armed_list)} trigger(s) from Council reply.")
                            full_response += f"\n\n*🔔 Auto-armed {len(armed_list)} key-level trigger(s): {summary_labels}*"
                except Exception as trig_err:
                    framework.log(f"[System Warning] Alert trigger extraction skipped: {redact_secrets(str(trig_err))}")
                
            import re
            full_response = re.sub(
                r"TEMPORAL_CHECK:\s*[^\n\)]*\)\s*", 
                "", 
                full_response, 
                flags=re.IGNORECASE
            )

            global active_model_warning
            active_model_warning = check_model_validity(ORCHESTRATOR_MODEL)
            
            # Log diagnostics to console/queue
            usage_lock = getattr(framework, "_usage_lock", None)
            if usage_lock is not None:
                with usage_lock:
                    usage_snapshot = dict(framework.turn_usage)
            else:
                usage_snapshot = dict(framework.turn_usage)
            turn_cost = usage_snapshot.get('estimated_cost', 0.0)
            session_cost = framework.session_cost
            framework.log(f"[Diagnostics] Turn Cost: ${turn_cost:.4f} | Total Session Cost: ${session_cost:.4f}")

            return {
                "status": "success", 
                "response": full_response.strip(),
                "usage": usage_snapshot,
                "model": ORCHESTRATOR_MODEL,
                "active_model": ORCHESTRATOR_MODEL,
                "fallback_occurred": fallback_occurred,
                "warning": active_model_warning,
                "turn_id": turn_id,
                "proposals": PROPOSAL_STORE.list(status="pending", turn_id=turn_id),
                "improvement_proposals": IMPROVEMENT_STORE.list(status="pending", turn_id=turn_id),
            }
        except Exception as e:
            import traceback
            traceback.print_exc()
            error_msg = str(e)
            with _turn_registry_lock:
                turn_cancelled = bool(
                    turn_event.is_set()
                    or (_active_turn_id == turn_id and cancel_event.is_set())
                )
            if turn_cancelled or "cancelled" in error_msg.lower():
                return {
                    "status": "success",
                    "response": "[OFFLINE] Session terminated by user interrupt.",
                    "cancelled": True,
                    "turn_id": turn_id,
                }

            framework.log(f"[Error] Chat endpoint exception: {error_msg}")
            if "429" in error_msg or "quota" in error_msg.lower() or "resource_exhausted" in error_msg.lower():
                return {
                    "status": "error",
                    "code": "quota_exhausted",
                    "message": "Free tier quota exhausted. Upgrade to Paid Tier to continue.",
                    "turn_id": turn_id,
                }
            return {"status": "error", "message": error_msg, "turn_id": turn_id}
        finally:
            if activated:
                framework.cancel_check = orig_framework_cancel_check
                if PROPOSAL_STORE.current_turn_id == turn_id:
                    PROPOSAL_STORE.current_turn_id = None
                if IMPROVEMENT_STORE.current_turn_id == turn_id:
                    IMPROVEMENT_STORE.current_turn_id = None
            with _turn_registry_lock:
                if _active_turn_id == turn_id:
                    _active_turn_id = None
                    _active_turn_event = None
                _turn_events.pop(turn_id, None)
                _precancelled_turn_ids.pop(turn_id, None)
                if turn_id not in _completed_turn_ids:
                    _completed_turn_ids.append(turn_id)

def _refresh_live_portfolio_state() -> None:
    """Hot-reloads tracked tickers and the in-memory SSoT snapshot after an approved write."""
    try:
        import fetch_stocks
        service = get_persistence_service()
        fetch_stocks.TICKERS = fetch_stocks._load_ssot_tickers()
        fetch_stocks.MACRO_TICKERS = fetch_stocks._load_macro_tickers()
        fetch_stocks.ALL_TICKERS = list(dict.fromkeys(fetch_stocks.TICKERS + fetch_stocks.MACRO_TICKERS))
        _trigger_force_refresh(fetch_stocks)
        if hasattr(fetch_stocks, "update_global_state_snapshot"):
            ssot_now = service.get_ssot_data()
            fetch_stocks.update_global_state_snapshot(lambda s: {**s, "local_storage_state": ssot_now})
    except Exception as se:
        framework.log(f"[Warning] Failed to refresh live portfolio state: {redact_secrets(str(se))}")


@app.get("/api/proposals")
def list_proposals():
    """Pending model-proposed portfolio changes awaiting owner approval."""
    return {"status": "success", "proposals": PROPOSAL_STORE.list(status="pending")}


@app.post("/api/proposals/{proposal_id}/apply")
def apply_proposal(proposal_id: str):
    """Owner approval: writes a proposal's position and cash changes after a staleness check."""
    try:
        applied = PROPOSAL_STORE.apply(proposal_id, approver_role="Owner")
    except ProposalNotFoundError:
        return JSONResponse({"status": "error", "detail": "Proposal not found (it may have expired after a server restart)."}, status_code=404)
    except (ProposalStateError, ProposalConflictError) as ce:
        return JSONResponse({"status": "error", "detail": redact_secrets(str(ce))}, status_code=409)
    except PermissionError:
        raise
    except PersistenceValidationError as ve:
        return JSONResponse({"status": "error", "detail": redact_secrets(str(ve))}, status_code=422)
    except Exception as e:
        framework.log(f"[Error] Applying proposal {proposal_id} failed: {redact_secrets(str(e))}")
        return JSONResponse({"status": "error", "detail": "Failed to apply portfolio proposal"}, status_code=503)
    framework.log(f"[System] Owner applied portfolio proposal {proposal_id}: {applied['summary']}")
    _refresh_live_portfolio_state()
    return {"status": "success", "proposal": applied}


@app.post("/api/proposals/{proposal_id}/discard")
def discard_proposal(proposal_id: str):
    try:
        discarded = PROPOSAL_STORE.discard(proposal_id)
    except ProposalNotFoundError:
        return JSONResponse({"status": "error", "detail": "Proposal not found (it may have expired after a server restart)."}, status_code=404)
    except ProposalStateError as se:
        return JSONResponse({"status": "error", "detail": str(se)}, status_code=409)
    framework.log(f"[System] Owner discarded portfolio proposal {proposal_id}.")
    return {"status": "success", "proposal": discarded}


class DecisionLogRequest(BaseModel):
    lesson: str
    ticker: str = ""
    outcome: str = ""

@app.post("/api/save_decision_log")
def save_decision_log(req: DecisionLogRequest):
    """Auto-write a new lesson entry to decision_log.json from the AI Council."""
    from tool_registry import authorize_financial_mutation
    authorize_financial_mutation(
        operation_name="update_decision_log",
        target_resource="context/decision_log.json",
        payload=req.dict(),
    )
    try:
        import datetime
        entry = {
            "date": datetime.datetime.now().strftime("%Y-%m-%d"),
            "ticker": req.ticker,
            "lesson": req.lesson,
            "outcome": req.outcome,
            "source": "AI_COUNCIL_AUTO"
        }
        service = get_persistence_service()
        service.append_decision_log(entry, prepend=True)
        framework.log(f"[System] Decision log saved: {req.ticker} — {req.lesson[:60]}...")
        return {"status": "success", "entry": entry}
    except PermissionError:
        raise
    except Exception as e:
        framework.log(f"[Error] Decision log save failed: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/api/clear_decision_log")
def clear_decision_log():
    """Wipe decision_log.json — all entries removed."""
    from tool_registry import authorize_financial_mutation
    authorize_financial_mutation(
        operation_name="update_decision_log",
        target_resource="context/decision_log.json",
        payload={"decision_log": []},
    )
    try:
        service = get_persistence_service()
        service.clear_decision_log()
        framework.log("[System] Decision log cleared.")
        return {"status": "success"}
    except PermissionError:
        raise
    except Exception as e:
        framework.log(f"[Error] Decision log clear failed: {e}")
        return {"status": "error", "message": str(e)}

# /api/tickers is served by fetch_stocks.get_tickers (live tickers + macro labels); this
# function only answers /api/get_settings (it used to also register a shadowed /api/tickers).
@app.get("/api/get_settings")
def get_settings():
    labels = {
        "^VIX": "VOLATILITY",
        "^VVIX": "^VVIX",
        "VIXY": "SHORT-TERM VIX",
        "IEF": "TREASURY BOND",
        "UUP": "US DOLLAR",
        "SPY": "S&P 500",
        "QQQ": "NASDAQ 100",
        "GDX": "GOLD MINERS"
    }
    if os.path.exists("context/user_config.json"):
        try:
            with open("context/user_config.json", "r") as f:
                data = json.load(f)
                if not data.get("macro"):
                    data["macro"] = [
                        "^VIX", "^VVIX", "VIXY", "IEF", "UUP", "SPY", "GDX"
                    ]
                data["macro_labels"] = labels
                return data
        except:
            pass
    return {
        "tickers": [], 
        "macro": ["^VIX", "^VVIX", "VIXY", "IEF", "UUP", "SPY", "GDX"],
        "macro_labels": labels
    }

class SettingsRequest(BaseModel):
    tickers: list
    macro: list

@app.post("/api/save_settings")
def save_settings(req: SettingsRequest):
    # Auto-uppercase and fix common index prefixes
    cleaned_macro = []
    for m in req.macro:
        m = m.strip().upper()
        if m in ['VIX', 'VVIX', 'SPX', 'NDX', 'RUT']:
            m = '^' + m
        if m:
            cleaned_macro.append(m)
            
    req.macro = cleaned_macro

    atomic_write_json("context/user_config.json", req.model_dump() if hasattr(req, "model_dump") else req.dict(), indent=2)
        
    try:
        import fetch_stocks
        fetch_stocks.MACRO_TICKERS = req.macro if req.macro else fetch_stocks.config.get("DEFAULT_MACRO_TICKERS", [])
        fetch_stocks.ALL_TICKERS = fetch_stocks.TICKERS + fetch_stocks.MACRO_TICKERS
    except Exception as e:
        framework.log(f"[Error] Failed to hot-reload MACRO_TICKERS: {e}")
        
    framework.log("[System] User settings updated.")
    return {"status": "success"}

class MacroRequest(BaseModel):
    macro: list

@app.post("/api/macro")
def save_macro(req: MacroRequest):
    # Auto-uppercase and fix common index prefixes
    cleaned_macro = []
    for m in req.macro:
        m = m.strip().upper()
        if m in ['VIX', 'VVIX', 'SPX', 'NDX', 'RUT']:
            m = '^' + m
        if m:
            cleaned_macro.append(m)
            
    req.macro = cleaned_macro

    config_path = "context/user_config.json"
    config = {"tickers": [], "macro": []}
    if os.path.exists(config_path):
        try:
            loaded_cfg = atomic_read_json(config_path, default=config)
            if isinstance(loaded_cfg, dict):
                config = loaded_cfg
        except Exception:
            pass

    config["macro"] = req.macro
    atomic_write_json(config_path, config, indent=2)
        
    try:
        import fetch_stocks
        fetch_stocks.MACRO_TICKERS = req.macro if req.macro else fetch_stocks.config.get("DEFAULT_MACRO_TICKERS", [])
        fetch_stocks.ALL_TICKERS = fetch_stocks.TICKERS + fetch_stocks.MACRO_TICKERS
    except Exception as e:
        framework.log(f"[Error] Failed to hot-reload MACRO_TICKERS: {e}")
        
    framework.log(f"[System] Macro indices updated: {req.macro}")
    return {"status": "success", "macro": req.macro}

@app.get("/api/basket")
def get_basket():
    try:
        service = get_persistence_service()
        basket = service.get_portfolio_basket()
        return DecimalJSONResponse(basket)
    except Exception as e:
        framework.log(f"[Error] Portfolio Basket Retrieval Failed: {redact_secrets(str(e))}")
        return JSONResponse(
            {"status": "error", "detail": "Authoritative portfolio persistence unavailable"},
            status_code=503,
        )


@app.post("/api/basket")
async def save_basket(request: Request):
    from tool_registry import authorize_financial_mutation
    authorize_financial_mutation(
        operation_name="update_ssot",
        target_resource="context/ssot.json",
    )
    try:
        raw_bytes = await request.body()
        try:
            raw_payload = json.loads(
                raw_bytes.decode("utf-8"),
                parse_float=Decimal,
                parse_constant=_reject_non_finite_json_constant,
            )
        except PersistenceValidationError as pve:
            return JSONResponse({"status": "error", "detail": redact_secrets(str(pve))}, status_code=422)
        except Exception as parse_err:
            return JSONResponse(
                {"status": "error", "detail": f"Invalid financial JSON payload: {redact_secrets(str(parse_err))}"},
                status_code=422,
            )

        try:
            req = BasketSaveRequest.model_validate(raw_payload)
        except (ValidationError, PersistenceValidationError) as ve:
            return JSONResponse({"status": "error", "detail": redact_secrets(str(ve))}, status_code=422)

        authorize_financial_mutation(
            operation_name="update_ssot",
            target_resource="context/ssot.json",
            payload=req.model_dump(),
        )

        # Validate portfolio symbols before saving (only active holdings with shares > 0)
        symbols = [item.ticker for item in req.portfolio if item.shares > Decimal("0")]
        invalid = check_invalid_tickers(symbols)
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid ticker symbol(s): {', '.join(invalid)}",
            )

        new_snapshot = [
            {"ticker": item.ticker, "shares": item.shares, "wac": item.wac}
            for item in req.portfolio if item.shares > Decimal("0")
        ]

        service = get_persistence_service()
        updated_basket = service.save_portfolio_basket(
            portfolio=new_snapshot,
            unallocated_cash_eur=req.unallocated_cash_eur,
            unallocated_cash_usd=req.unallocated_cash_usd,
        )
        framework.log(
            f"[System] SSoT Basket & Cash updated: {len(new_snapshot)} holding(s). "
            f"Cash: {req.unallocated_cash_eur} EUR | {updated_basket.get('unallocated_cash_usd')} USD"
        )

        # Hot-reload TICKERS in fetch_stocks so the scanning daemon immediately registers changes
        try:
            import fetch_stocks
            fetch_stocks.TICKERS = fetch_stocks._load_ssot_tickers()
            fetch_stocks.MACRO_TICKERS = fetch_stocks._load_macro_tickers()
            fetch_stocks.ALL_TICKERS = list(dict.fromkeys(fetch_stocks.TICKERS + fetch_stocks.MACRO_TICKERS))
            _trigger_force_refresh(fetch_stocks)

            # Immediately update in-memory GLOBAL_STATE so the next GET /api/data instantly reflects changes
            if hasattr(fetch_stocks, "update_global_state_snapshot"):
                def _update_basket_state(current):
                    new_state = dict(current)
                    new_state["local_storage_state"] = service.get_ssot_data()
                    held_set = {item["ticker"].upper() for item in new_snapshot}
                    watched_set = set(service.get_watchlist())
                    if "tickers" in new_state and isinstance(new_state["tickers"], list):
                        valid_active = held_set.union(watched_set)
                        new_state["tickers"] = [
                            t for t in new_state["tickers"]
                            if isinstance(t, dict) and (
                                t.get("_isScout")
                                or t.get("ticker", "").upper() in valid_active
                                or t.get("ticker", "").upper() in fetch_stocks.MACRO_TICKERS
                            )
                        ]
                    return new_state
                fetch_stocks.update_global_state_snapshot(_update_basket_state)
            elif isinstance(getattr(fetch_stocks, "GLOBAL_STATE", None), dict):
                fetch_stocks.GLOBAL_STATE["local_storage_state"] = service.get_ssot_data()
        except Exception as se:
            framework.log(f"[Warning] Failed to hot-reload TICKERS: {redact_secrets(str(se))}")

        return DecimalJSONResponse({
            "status": "success",
            "portfolio": updated_basket.get("portfolio", new_snapshot),
            "unallocated_cash_eur": updated_basket.get("unallocated_cash_eur", req.unallocated_cash_eur),
            "unallocated_cash_usd": updated_basket.get("unallocated_cash_usd", req.unallocated_cash_usd),
        })
    except PermissionError:
        raise
    except HTTPException as he:
        raise he
    except PersistenceValidationError as ve:
        return JSONResponse({"status": "error", "detail": redact_secrets(str(ve))}, status_code=422)
    except Exception as e:
        framework.log(f"[Error] Basket Save Failed: {redact_secrets(str(e))}")
        return JSONResponse(
            {"status": "error", "detail": "Failed to persist portfolio basket"},
            status_code=503,
        )

@app.get("/api/watchlist")
def get_watch_list():
    try:
        service = get_persistence_service()
        return service.get_watchlist()
    except Exception as e:
        framework.log(f"[Error] Failed to get watchlist: {e}")
    return []

from fastapi import Request

@app.post("/api/watchlist")
async def save_watch_list(req: Request):
    from tool_registry import authorize_financial_mutation
    try:
        payload = await req.json()
        authorize_financial_mutation(
            operation_name="update_ssot",
            target_resource="context/ssot.json",
            payload=payload,
        )
        
        # Robustly handle both list payloads and dict payloads
        if isinstance(payload, dict):
            watch_list = payload.get("watchlist", [])
        elif isinstance(payload, list):
            watch_list = payload
        else:
            watch_list = []
            
        # Clean and uppercase
        watch_list = [w.strip().upper() for w in watch_list if isinstance(w, str) and w.strip()]
        
        # Validate watchlist symbols before saving
        invalid = check_invalid_tickers(watch_list)
        if invalid:
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid ticker symbol(s): {', '.join(invalid)}"
            )

        service = get_persistence_service()
        service.save_watchlist(watch_list)
        framework.log("[System] Watch list updated in ssot.json and config.json.")
            
        # FORCE HOT-RELOAD OF DAEMON TICKERS
        try:
            import fetch_stocks
            fetch_stocks.TICKERS = fetch_stocks._load_ssot_tickers()
            fetch_stocks.ALL_TICKERS = fetch_stocks.TICKERS + fetch_stocks.MACRO_TICKERS
            _trigger_force_refresh(fetch_stocks)
            if hasattr(fetch_stocks, "update_global_state_snapshot"):
                def _update_watchlist_state(current):
                    new_state = dict(current)
                    new_state["local_storage_state"] = service.get_ssot_data()
                    return new_state
                fetch_stocks.update_global_state_snapshot(_update_watchlist_state)
            elif isinstance(getattr(fetch_stocks, "GLOBAL_STATE", None), dict):
                fetch_stocks.GLOBAL_STATE["local_storage_state"] = service.get_ssot_data()
        except Exception as se:
            framework.log(f"[Warning] Failed to hot-reload TICKERS: {se}")
            
        return {"status": "success"}
    except PermissionError:
        raise
    except HTTPException as he:
        raise he
    except PersistenceValidationError as ve:
        return JSONResponse({"status": "error", "detail": redact_secrets(str(ve))}, status_code=422)
    except Exception as e:
        # Fail with a real error status (was HTTP 200 + {"status": "error"}), like /api/basket
        framework.log(f"[Error] Watch List Save Failed: {redact_secrets(str(e))}")
        return JSONResponse({"status": "error", "detail": "Failed to persist watchlist"}, status_code=503)

@app.post("/api/ai_scout")
async def run_ai_scout(request: Request, background_tasks: BackgroundTasks):
    """
    Triggers an AI-powered scout scan in one of two modes:
      - mode='sectors': discovers new tickers from active scout sector categories
      - mode='watchlist': rescans current watchlist tickers with signal model
    Both modes reload the daemon tickers after finding candidates.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    mode = body.get("mode", "sectors")  # 'sectors' | 'watchlist'
    limit = int(body.get("limit", 2))
    max_rsi = int(body.get("max_rsi", 75))

    try:
        import fetch_stocks

        # ── Mode: Watchlist rescan ───────────────────────────────────────────
        if mode == "watchlist":
            watchlist = []
            try:
                watchlist = get_persistence_service().get_watchlist()
            except Exception:
                pass

            if not watchlist:
                return JSONResponse({"status": "error", "message": "No tickers in Watchlist. Add tickers to the Watchlist first."})

            # Force a refresh cycle so daemon re-evaluates them
            _trigger_force_refresh(fetch_stocks)
            background_tasks.add_task(reload_scout_tickers_task)
            return JSONResponse({
                "status": "success",
                "scouts": watchlist,
                "message": f"Watchlist rescan triggered for {len(watchlist)} tickers."
            })

        # ── Mode: Sector discovery ───────────────────────────────────────────
        else:
            categories = []
            try:
                scout_data = get_persistence_service().get_scout_categories()
                categories = list(scout_data.keys()) if isinstance(scout_data, dict) else list(scout_data)
            except Exception:
                pass

            if not categories:
                return JSONResponse({"status": "error", "message": "No Scout Sectors enabled. Toggle at least one sector to scan."})

            # Trigger the dynamic scout ticker discovery in background
            _trigger_force_refresh(fetch_stocks)
            background_tasks.add_task(reload_scout_tickers_task)

            return JSONResponse({
                "status": "success",
                "scouts": [],
                "message": f"Sector scout triggered for {len(categories)} sector(s): {', '.join(categories)}. New tickers will appear in the table shortly."
            })

    except Exception as e:
        framework.log(f"[Error] AI Scout Failed: {e}")
        return JSONResponse({"status": "error", "message": str(e)})

@app.get("/api/scout_categories")
def get_scout_categories():
    try:
        scouts = get_persistence_service().get_scout_categories()
        return list(scouts.keys()) if isinstance(scouts, dict) else list(scouts)
    except: pass
    return []

@app.get("/api/scout_sectors")
def get_scout_sectors():
    try:
        if os.path.exists("context/config.json"):
            with open("context/config.json", "r") as f:
                cfg = json.load(f)
                scout_map = cfg.get("SCOUT_TICKER_MAP", {})
                if scout_map:
                    return list(scout_map.keys())
    except Exception as e:
        framework.log(f"[Error] Failed to read scout sectors: {e}")
    return []

def reload_scout_tickers_task():
    try:
        import fetch_stocks
        fetch_stocks.TICKERS = fetch_stocks._load_ssot_tickers()
        fetch_stocks.ALL_TICKERS = fetch_stocks.TICKERS + fetch_stocks.MACRO_TICKERS
        _trigger_force_refresh(fetch_stocks)
        print(f"[System] Tickers hot-reloaded and forced refresh triggered in background: {fetch_stocks.TICKERS}")
    except Exception as se:
        print(f"[Warning] Failed to hot-reload TICKERS in background: {se}")

@app.post("/api/scout_categories")
def save_scout_categories(categories: List[str], background_tasks: BackgroundTasks):
    try:
        from tool_registry import authorize_financial_mutation
        authorize_financial_mutation("update_ssot", "context/ssot.json", payload=categories)
        get_persistence_service().save_scout_categories(categories)
        framework.log("[System] Scout categories updated.")

        # Queue the heavy Dynamic Search yfinance reload logic in the background
        background_tasks.add_task(reload_scout_tickers_task)
        return {"status": "success", "categories": categories}
    except PermissionError:
        raise
    except Exception as e:
        framework.log(f"[Error] Scout Categories Save Failed: {redact_secrets(str(e))}")
        return JSONResponse(
            {"status": "error", "detail": "Failed to persist scout categories"},
            status_code=503,
        )

@app.post("/api/trade_lessons/add")
async def add_trade_lesson_endpoint(req: Request):
    """Owner-triggered endpoint saving an approved post-trade lesson to trade_lessons."""
    from tool_registry import authorize_financial_mutation
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"status": "error", "detail": "Invalid JSON lesson payload"}, status_code=422)
    if not isinstance(body, dict):
        return JSONResponse({"status": "error", "detail": "Lesson payload must be a JSON object"}, status_code=422)

    lesson_text = str(body.get("lesson") or "").strip()
    if not lesson_text or len(lesson_text) > 1000:
        return JSONResponse({"status": "error", "detail": "Lesson must be between 1 and 1000 characters"}, status_code=422)

    raw_ticker = str(body.get("ticker") or "").strip().upper()
    ticker_sym = raw_ticker if (raw_ticker and len(raw_ticker) <= 10) else "PORTFOLIO"

    try:
        authorize_financial_mutation(
            operation_name="update_trade_lessons",
            target_resource="context/trade_lessons.json",
            payload=body,
        )
        service = get_persistence_service()
        existing = service.get_trade_lessons()
        lessons_list = list(existing) if isinstance(existing, list) else []
        entry = {
            "date": agent_framework.ny_now().strftime("%Y-%m-%d"),
            "ticker": ticker_sym,
            "lesson": lesson_text,
            "source": str(body.get("source") or "OWNER_APPROVED_REVIEW").strip()[:64],
        }
        already = any(
            isinstance(x, dict) and str(x.get("lesson") or "").strip() == lesson_text
            for x in lessons_list
        )
        if not already:
            lessons_list.append(entry)
            service.save_trade_lessons(lessons_list)
        return {"status": "success", "total_lessons": len(lessons_list), "entry": entry, "duplicate": already}
    except PermissionError:
        raise
    except PersistenceValidationError as ve:
        return JSONResponse({"status": "error", "detail": redact_secrets(str(ve))}, status_code=422)
    except Exception as e:
        framework.log(f"[Error] Save Trade Lesson Failed: {redact_secrets(str(e))}")
        return JSONResponse({"status": "error", "detail": "Failed to persist trade lesson"}, status_code=503)


@app.get("/api/improvements")
def list_improvement_proposals():
    """Pending agent-proposed lessons and rules.md amendments awaiting owner approval."""
    return {"status": "success", "proposals": IMPROVEMENT_STORE.list(status="pending")}


@app.post("/api/improvements/{proposal_id}/apply")
async def apply_improvement_proposal(proposal_id: str, req: Request):
    """Owner approval: writes the (optionally edited) lesson or rule amendment."""
    edited_text = None
    try:
        raw = await req.body()
        if raw:
            body = json.loads(raw)
            if isinstance(body, dict) and body.get("text") is not None:
                edited_text = str(body.get("text"))
    except Exception:
        return JSONResponse({"status": "error", "detail": "Invalid JSON body"}, status_code=422)
    try:
        applied = IMPROVEMENT_STORE.apply(proposal_id, edited_text=edited_text)
    except ImprovementNotFoundError:
        return JSONResponse({"status": "error", "detail": "Proposal not found."}, status_code=404)
    except ImprovementStateError as se:
        return JSONResponse({"status": "error", "detail": str(se)}, status_code=409)
    except (ImprovementValidationError, PersistenceValidationError) as ve:
        return JSONResponse({"status": "error", "detail": redact_secrets(str(ve))}, status_code=422)
    except PermissionError:
        raise
    except Exception as e:
        framework.log(f"[Error] Applying improvement {proposal_id} failed: {redact_secrets(str(e))}")
        return JSONResponse({"status": "error", "detail": "Failed to apply proposal. Nothing was marked applied."}, status_code=503)
    label = applied.get("amendment_id") or applied.get("kind")
    framework.log(f"[System] Owner applied improvement proposal {proposal_id} ({label}).")
    return {"status": "success", "proposal": applied}


@app.post("/api/improvements/{proposal_id}/discard")
def discard_improvement_proposal(proposal_id: str):
    try:
        discarded = IMPROVEMENT_STORE.discard(proposal_id)
    except ImprovementNotFoundError:
        return JSONResponse({"status": "error", "detail": "Proposal not found."}, status_code=404)
    except ImprovementStateError as se:
        return JSONResponse({"status": "error", "detail": str(se)}, status_code=409)
    framework.log(f"[System] Owner discarded improvement proposal {proposal_id}.")
    return {"status": "success", "proposal": discarded}


@app.get("/api/alerts")
def get_alerts():
    payload = ALERT_MONITOR.get_dashboard_payload()
    payload["sentinel"] = ALERT_SENTINEL.get_status()
    return payload


@app.post("/api/alerts")
async def create_alert_rule(req: Request):
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"status": "error", "detail": "Invalid JSON alert rule payload"}, status_code=422)
    try:
        rule = ALERT_MONITOR.add_rule(body, source="manual")
        return {"status": "success", "rule": rule, "alerts": ALERT_MONITOR.get_dashboard_payload()}
    except ValueError as ve:
        return JSONResponse({"status": "error", "detail": str(ve)}, status_code=422)


@app.delete("/api/alerts/{rule_id}")
def delete_alert_rule(rule_id: str):
    if not ALERT_MONITOR.delete_rule(rule_id):
        raise HTTPException(status_code=404, detail=f"Alert rule '{rule_id}' not found")
    return {"status": "success", "alerts": ALERT_MONITOR.get_dashboard_payload()}


@app.post("/api/alerts/{rule_id}/toggle")
def toggle_alert_rule(rule_id: str):
    updated = ALERT_MONITOR.toggle_rule(rule_id)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Alert rule '{rule_id}' not found")
    return {"status": "success", "rule": updated, "alerts": ALERT_MONITOR.get_dashboard_payload()}


@app.post("/api/alerts/events/{event_id}/ack")
def acknowledge_alert_event(event_id: str):
    if not ALERT_MONITOR.acknowledge_event(event_id):
        raise HTTPException(status_code=404, detail=f"Alert event '{event_id}' not found")
    return {"status": "success", "alerts": ALERT_MONITOR.get_dashboard_payload()}


@app.post("/api/alerts/events/clear")
def clear_alert_events():
    cleared = ALERT_MONITOR.clear_events()
    return {"status": "success", "cleared": cleared, "alerts": ALERT_MONITOR.get_dashboard_payload()}


@app.get("/api/alert_sentinel")
def get_alert_sentinel_status():
    return ALERT_SENTINEL.get_status()


@app.post("/api/alert_sentinel/config")
async def update_alert_sentinel_config(req: Request):
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"status": "error", "detail": "Invalid Sentinel config JSON"}, status_code=422)
    ALERT_SENTINEL.update_config(body, persist=True)
    return {"status": "success", "sentinel": ALERT_SENTINEL.get_status()}


@app.post("/api/alert_sentinel/run")
def run_alert_sentinel_now():
    from fetch_stocks import get_global_state_snapshot
    snap = get_global_state_snapshot()
    outcome = ALERT_SENTINEL.run_once(snapshot=snap, force=True)
    return {
        "status": "success" if outcome.get("status") == "OK" else outcome.get("status", "DEGRADED"),
        "outcome": outcome,
        "sentinel": ALERT_SENTINEL.get_status(),
        "alerts": ALERT_MONITOR.get_dashboard_payload(),
    }


@app.get("/api/analytics")
def get_precomputed_analytics():
    """Read-only deterministic Regime, Veto, Mandate, and Trim Sizer analytics."""
    return ANALYTICS_ENGINE.evaluate(*_load_live_analytics_context())


@app.get("/api/analytics/backtest")
def get_decision_backtest(limit: int = 25):
    """Read-only deterministic forward-return & MAE backtest over decision_log."""
    try:
        raw_log = get_persistence_service().get_decision_log()
    except Exception:
        raw_log = []
    if not isinstance(raw_log, list):
        raw_log = []
    return backtest_decision_log(raw_log, max_entries=max(1, min(100, int(limit or 25))))


REVIEW_LOG_PROMPT_PATH = "prompts/review_log_prompt.txt"
_REVIEW_LOG_FALLBACK_PROMPT = (
    "SYSTEM DIRECTIVE: POST-TRADE REVIEW (MANDATE_26). Audit the Council's past recommendations using the "
    "REVIEW_EVIDENCE below. Open with ### 🚨 Alerts & Risk Warnings and ### 🎯 Actionable Recommendations "
    "(3-5 bullets prefixed [LESSON], [TRIGGER] or [RULE_PROPOSAL]). Do not emit an EXECUTION_PAYLOAD."
)


def _lesson_text(lesson: Any) -> str:
    if not isinstance(lesson, dict):
        return str(lesson)[:300]
    for key in ("rule", "lesson", "directive_text", "text"):
        val = lesson.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()[:300]
    return json.dumps(lesson, default=str)[:300]


def _build_review_evidence(limit: int = 25) -> Dict[str, Any]:
    """Deterministic evidence packet for the Review Engine: backtest, data-quality counts, recent lessons."""
    service = get_persistence_service()
    raw_log = service.get_decision_log()
    if not isinstance(raw_log, list):
        raw_log = []
    bt = backtest_decision_log(raw_log, max_entries=limit)
    session_rows = sum(
        1 for e in raw_log
        if isinstance(e, dict)
        and not e.get("decisions")
        and str(e.get("ticker") or "SESSION").strip().upper() in {"SESSION", "PORTFOLIO", ""}
    )
    try:
        lessons = service.get_trade_lessons()
    except Exception:
        lessons = []
    lessons = list(lessons) if isinstance(lessons, list) else []
    return {
        "data_quality": {
            "decision_log_rows": len(raw_log),
            "session_level_rows_not_scorable": session_rows,
            "per_ticker_decisions": bt.get("total_logged", 0),
            "scored_against_price_history": bt.get("evaluated_count", 0),
        },
        "backtest_summary": {k: v for k, v in bt.items() if k != "entries"},
        "backtest_entries": (bt.get("entries") or [])[-limit:],
        "trade_lessons_registry": {
            "count": len(lessons),
            "recent": [_lesson_text(x) for x in lessons[-15:]],
        },
    }


def _load_review_log_prompt() -> str:
    if os.path.exists(REVIEW_LOG_PROMPT_PATH):
        try:
            with open(REVIEW_LOG_PROMPT_PATH, "r", encoding="utf-8") as f:
                text = f.read().strip()
            if text:
                return text
        except Exception as e:
            framework.log(f"[System Warning] Review prompt load failed: {e}")
    return _REVIEW_LOG_FALLBACK_PROMPT


def _stage_review_rule_proposals(review_text: str, turn_id: str) -> List[Dict[str, Any]]:
    """Stages each [RULE_PROPOSAL] line of a review as a pending owner-approval proposal."""
    try:
        parsed = extract_rule_proposals(review_text)
    except Exception as e:
        framework.log(f"[System Warning] Rule proposal extraction failed: {redact_secrets(str(e))}")
        return []
    for prop in parsed:
        try:
            IMPROVEMENT_STORE.stage_rule(
                prop["rule_id"], prop["text"], action=prop["action"],
                justification=prop["justification"], source="Review Engine", turn_id=turn_id,
            )
        except Exception as e:
            framework.log(f"[System Warning] Could not stage rule proposal: {redact_secrets(str(e))}")
    if parsed:
        framework.log(f"[System] Review Log staged {len(parsed)} rule proposal(s) for owner approval.")
    return IMPROVEMENT_STORE.list(status="pending", turn_id=turn_id)


@app.post("/api/review")
def review_endpoint(req: ChatRequest):
    """Review Log chip: runs the read-only Review Engine over the decision log + deterministic backtest."""
    global _active_turn_id, _active_turn_event

    raw_tid = getattr(req, "turn_id", None)
    turn_id = (raw_tid.strip() if isinstance(raw_tid, str) and raw_tid.strip() else None) or f"turn-{uuid.uuid4().hex}"

    with _registered_turn(turn_id) as turn_event, _chat_execution_lock:
        activated = False
        orig_framework_cancel_check = getattr(framework, "cancel_check", check_cancelled)
        cancelled_res = {
            "status": "success",
            "response": "[OFFLINE] Session terminated by user interrupt.",
            "cancelled": True,
            "turn_id": turn_id,
        }
        try:
            if turn_event.is_set():
                return cancelled_res
            with _turn_registry_lock:
                cancel_event.clear()
                _active_turn_id = turn_id
                _active_turn_event = turn_event
                activated = True

            def turn_cancel_check() -> bool:
                with _turn_registry_lock:
                    return bool(turn_event.is_set() or (_active_turn_id == turn_id and cancel_event.is_set()))

            framework.cancel_check = turn_cancel_check
            IMPROVEMENT_STORE.current_turn_id = turn_id
            framework.reset_turn_usage()

            framework.log("[System] Review Log: building decision-log evidence and backtest...")
            evidence = _build_review_evidence()
            dq = evidence["data_quality"]
            query = (
                f"{_load_review_log_prompt()}\n\n[REVIEW_EVIDENCE]:\n```json\n"
                f"{json.dumps(_normalize_for_json(evidence), indent=1, default=str)}\n```"
            )
            framework.log("[System] Review Log: delegating to Review Engine...")
            review_text = review_log_engine(query)
            if turn_cancel_check():
                return cancelled_res

            review_text = (review_text or "").strip() or (
                "_[The Review Engine returned no text. Please try again or check system logs.]_"
            )
            staged = _stage_review_rule_proposals(review_text, turn_id)
            evidence_line = (
                f"> 🧾 **Evidence base:** {dq['scored_against_price_history']} decision(s) scored against price history, "
                f"from {dq['per_ticker_decisions']} per-ticker decision(s) in {dq['decision_log_rows']} log rows "
                f"({dq['session_level_rows_not_scorable']} session-level transcripts cannot be scored)."
            )
            usage_lock = getattr(framework, "_usage_lock", None)
            if usage_lock is not None:
                with usage_lock:
                    usage_snapshot = dict(framework.turn_usage)
            else:
                usage_snapshot = dict(framework.turn_usage)
            return {
                "status": "success",
                "response": f"{review_text}\n\n---\n\n{evidence_line}",
                "usage": usage_snapshot,
                "turn_id": turn_id,
                "evidence": dq,
                "improvement_proposals": staged,
            }
        except Exception as e:
            error_msg = redact_secrets(str(e))
            if turn_event.is_set() or "cancelled" in error_msg.lower():
                return cancelled_res
            framework.log(f"[Error] Review endpoint exception: {error_msg}")
            if "429" in error_msg or "quota" in error_msg.lower() or "resource_exhausted" in error_msg.lower():
                return {
                    "status": "error",
                    "code": "quota_exhausted",
                    "message": "Free tier quota exhausted. Upgrade to Paid Tier to continue.",
                    "turn_id": turn_id,
                }
            return {"status": "error", "message": error_msg, "turn_id": turn_id}
        finally:
            if activated:
                framework.cancel_check = orig_framework_cancel_check
                if IMPROVEMENT_STORE.current_turn_id == turn_id:
                    IMPROVEMENT_STORE.current_turn_id = None
            with _turn_registry_lock:
                if _active_turn_id == turn_id:
                    _active_turn_id = None
                    _active_turn_event = None
                _precancelled_turn_ids.pop(turn_id, None)
                if turn_id not in _completed_turn_ids:
                    _completed_turn_ids.append(turn_id)


@app.get("/api/analytics/sec/{ticker}")
def get_sec_filings_endpoint(ticker: str, limit: int = 8):
    """Read-only SEC EDGAR recent filings & 72h dilution warning lookup."""
    return fetch_recent_sec_filings(ticker=ticker, limit=max(1, min(20, int(limit or 8))))


@app.get("/api/analytics/execution")
def get_execution_plan_endpoint():
    """Read-only deterministic execution plan (trailing stops, Fib ladders, cash proof)."""
    return ANALYTICS_ENGINE.build_execution_plan(*_load_live_analytics_context())


@app.get("/api/analytics/calendar")
def get_macro_calendar_endpoint():
    """Read-only Tier-1 Macro Economic Calendar Shield status (ENH_47 / ENH_259)."""
    return get_macro_calendar_status(now_ny=agent_framework.ny_now())


@app.get("/api/analytics/peak/{ticker}")
def get_daily_peak_prediction(ticker: str):
    """Read-only 60d empirical RTH peak timing & intraday exhaustion predictor."""
    sym = str(ticker or "").strip().upper()
    live_state, _ssot, _fx, ny_dt = _load_live_analytics_context()
    tickers = live_state.get("tickers", []) if isinstance(live_state, dict) else []
    live_row = next(
        (t for t in tickers if isinstance(t, dict) and str(t.get("ticker") or "").strip().upper() == sym),
        None,
    )
    tracker = ANALYTICS_ENGINE._get_tracker_snapshot(sym)
    return predict_daily_peak(sym, live_row=live_row, tracker=tracker, ny_dt=ny_dt)


@app.get("/api/calibration/export")
def export_colab_calibration_endpoint():
    """Exports zero-secret quant telemetry and history bundle for offline Google Colab analysis."""
    bundle = export_colab_bundle(service=get_persistence_service())
    return JSONResponse(
        content=bundle,
        headers={"Content-Disposition": 'attachment; filename="gem_colab_export.json"'},
    )


@app.post("/api/calibration/import")
async def import_colab_calibration_endpoint(req: Request):
    """Fail-closed import of schema-validated Google Colab calibration JSON into cache/."""
    try:
        payload = await req.json()
    except Exception:
        return JSONResponse({"status": "error", "detail": "Invalid JSON body."}, status_code=422)
    try:
        summary = import_colab_calibration(payload)
        framework.log(f"[Colab Calibration] Imported priors for {len(summary.get('tickers_calibrated', []))} ticker(s).")
        return summary
    except ValueError as ve:
        return JSONResponse({"status": "error", "detail": redact_secrets(str(ve))}, status_code=422)
    except Exception as exc:
        framework.log(f"[Colab Calibration] Import failed: {redact_secrets(str(exc))}")
        return JSONResponse({"status": "error", "detail": "Failed to import calibration."}, status_code=500)


@app.get("/api/calibration/status")
def get_colab_calibration_status_endpoint():
    """Returns currently loaded offline Colab calibration metadata."""
    return get_calibration_status()


@app.get("/api/colab/status")
def get_colab_mcp_status_endpoint():
    """Returns Google Colab MCP transport readiness, active/recent jobs, and v2 artifact status."""
    return get_colab_job_manager().get_status()


@app.post("/api/colab/jobs/{job_type}")
async def launch_colab_mcp_job_endpoint(job_type: str, req: Request):
    """Launches a single-flight background Google Colab MCP quant job."""
    raw_body = await req.body()
    if not raw_body or not raw_body.strip():
        body: Dict[str, Any] = {}
    else:
        try:
            body = json.loads(raw_body.decode("utf-8"))
        except Exception:
            return JSONResponse({"status": "error", "detail": "Invalid JSON body."}, status_code=422)
        if not isinstance(body, dict):
            return JSONResponse({"status": "error", "detail": "Request body must be a JSON object."}, status_code=422)

    unknown_keys = set(body.keys()) - {"options", "mode"}
    if unknown_keys:
        return JSONResponse(
            {"status": "error", "detail": f"Unknown request key(s): {sorted(unknown_keys)}"},
            status_code=422,
        )

    norm_type = str(job_type or "").strip().lower()
    try:
        record = get_colab_job_manager().submit_job(
            norm_type,
            options=body.get("options"),
            mode=body.get("mode"),
        )
        framework.log(f"[Colab MCP] Queued {norm_type} job {record['job_id']} (mode={record['mode']}).")
        return JSONResponse(content=record, status_code=202)
    except ColabJobConflictError as ce:
        return JSONResponse({"status": "error", "detail": redact_secrets(str(ce))}, status_code=409)
    except ValueError as ve:
        status_code = 400 if "Unknown Colab job_type" in str(ve) else 422
        return JSONResponse({"status": "error", "detail": redact_secrets(str(ve))}, status_code=status_code)
    except Exception as exc:
        framework.log(f"[Colab MCP] Job launch failed: {redact_secrets(str(exc))}")
        return JSONResponse({"status": "error", "detail": "Failed to launch Colab job."}, status_code=500)


@app.get("/api/colab/jobs/{job_id}")
def get_colab_mcp_job_endpoint(job_id: str):
    """Returns status and progress of a previously queued Colab MCP job."""
    record = get_colab_job_manager().get_job(job_id)
    if record is None:
        return JSONResponse({"status": "error", "detail": f"Unknown Colab job_id: {job_id}"}, status_code=404)
    return record


@app.get("/api/colab/artifacts/{artifact_type}")
def get_colab_mcp_artifact_endpoint(artifact_type: str):
    """Returns read-only Schema v2.0 cached Colab quant artifact and freshness metadata."""
    norm_type = str(artifact_type or "").strip().lower()
    if norm_type not in ARTIFACT_REGISTRY:
        return JSONResponse({"status": "error", "detail": f"Unknown Colab artifact type: {artifact_type}"}, status_code=404)
    status = get_artifact_status(norm_type)
    data = load_colab_artifact(norm_type)
    return {
        "artifact_type": norm_type,
        "loaded": bool(data),
        "freshness": status["freshness"],
        "data": data,
    }


@app.post("/api/colab/artifacts/{artifact_type}/import")
async def import_colab_mcp_artifact_endpoint(artifact_type: str, req: Request):
    """Fail-closed offline JSON import fallback for Schema v2.0 Colab artifacts."""
    norm_type = str(artifact_type or "").strip().lower()
    if norm_type not in ARTIFACT_REGISTRY:
        return JSONResponse({"status": "error", "detail": f"Unknown Colab artifact type: {artifact_type}"}, status_code=404)
    try:
        payload = await req.json()
    except Exception:
        return JSONResponse({"status": "error", "detail": "Invalid JSON body."}, status_code=422)
    try:
        summary = import_colab_artifact(norm_type, payload)
        framework.log(f"[Colab MCP] Imported offline {norm_type} artifact ({summary['item_count']} item(s)).")
        return summary
    except ValueError as ve:
        return JSONResponse({"status": "error", "detail": redact_secrets(str(ve))}, status_code=422)
    except Exception as exc:
        framework.log(f"[Colab MCP] Artifact import failed: {redact_secrets(str(exc))}")
        return JSONResponse({"status": "error", "detail": "Failed to import Colab artifact."}, status_code=500)


@app.get("/api/system_logs")
async def system_logs_endpoint():
    """SSE endpoint to stream system logs to each connected dashboard tab independently."""
    global main_loop
    main_loop = asyncio.get_running_loop()
    queue = subscribe_log_queue()

    async def event_generator():
        try:
            while True:
                log_msg = await queue.get()
                yield f"data: {log_msg}\n\n"
        finally:
            unsubscribe_log_queue(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.middleware("http")
async def set_static_html_no_cache(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith(".html"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    elif "v" in request.query_params and path.endswith((".js", ".css")):
        response.headers["Cache-Control"] = "public, max-age=86400"
    return response


# --- MOUNT STATIC AND RUN ---
def main() -> int:
    if not os.path.exists("static"):
        os.makedirs("static")
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
    
    # Prioritize Antigravity sidecar port if provided, otherwise auto-select starting from 8000
    import socket
    sidecar_port_env = os.environ.get("ANTIGRAVITY_SIDECAR_WEB_PORT")
    if sidecar_port_env:
        try:
            port = int(sidecar_port_env)
        except ValueError:
            port = 8000
    else:
        port = 8000
        while True:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("127.0.0.1", port))
                    break  # Port is available
                except OSError:
                    port += 1

    print(f"Starting Web Server on http://localhost:{port}")
    
    # Start the background data daemon once
    tools.start_market_daemon(suppress_daemon_stdout=False)

    # Live EUR/USD rate (refreshes every 5 minutes; replaces the former hardcoded 1.08)
    fx_rates.start_background_refresher(log=framework.log)

    try:
        import fetch_stocks
        fetch_stocks.compile_master_document()
        ALERT_SENTINEL.set_snapshot_provider(fetch_stocks.get_global_state_snapshot)
        ALERT_SENTINEL.set_log_callback(framework.log)
        ALERT_SENTINEL.start()
        # Cloud sync daemon removed — GDrive Decoupling Guardrail (ANTIGRAVITY v11.03)
    except Exception as e:
        print(f"Failed to compile master document: {e}")

    try:
        uvicorn.run(app, host="127.0.0.1", port=port)
        return 0
    except Exception as e:
        print(f"CRITICAL: Web Server exited with error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
