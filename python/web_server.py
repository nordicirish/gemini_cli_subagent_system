import os
from fastapi import BackgroundTasks, HTTPException, Request
import sys
import uvicorn
import asyncio
import json
from typing import List
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, JSONResponse
import threading
import glob
from agent_framework import types, Part

from agent_framework import AgentFramework
import agent_framework
import tools
# cloud_sync removed — GDrive Decoupling Guardrail (ANTIGRAVITY v11.03)

# Import the existing FastAPI app from fetch_stocks
from fetch_stocks import app, run_daemon, GLOBAL_STATE, CHART_SCREENSHOTS_BUFFER

def get_chart_parts() -> tuple[list, list]:
    """Load latest 1-minute chart screenshots from context/charts/ as multimodal Parts."""
    chart_parts = []
    chart_symbols = []
    chart_dir = "context/charts"
    if os.path.exists(chart_dir):
        for filepath in sorted(glob.glob(os.path.join(chart_dir, "chart_*_1m.png"))):
            try:
                base = os.path.basename(filepath)
                sym = base.replace("chart_", "").replace("_1m.png", "")
                with open(filepath, "rb") as f:
                    data = f.read()
                    if data:
                        chart_parts.append(
                            types.Part.from_bytes(data=data, mime_type="image/png")
                        )
                        chart_symbols.append(sym)
            except Exception as e:
                pass
    return chart_parts, chart_symbols

class BasketItem(BaseModel):
    ticker: str
    shares: float
    wac: float

class BasketRequest(BaseModel):
    basket: List[BasketItem]

class BasketSaveRequest(BaseModel):
    portfolio: List[BasketItem]
    unallocated_cash_eur: float
    unallocated_cash_usd: float = 0.0

# --- LOGGING SYSTEM ---
_system_logs_queue = None
main_loop = None

def get_log_queue():
    global _system_logs_queue
    if _system_logs_queue is None:
        _system_logs_queue = asyncio.Queue(maxsize=100)
    return _system_logs_queue

def log_to_queue(message: str):
    """Callback for AgentFramework to push logs to the async queue."""
    global main_loop
    if main_loop:
        try:
            main_loop.call_soon_threadsafe(_system_logs_queue.put_nowait, message)
        except:
            pass

import threading
cancel_event = threading.Event()

def check_cancelled():
    return cancel_event.is_set()

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

# --- CONTEXT CACHING INITIALIZATION (ENH_CACHE_01) ---
subagent_instructions = [
    "engine_instructions/macro_sentinel.md", "engine_instructions/research.md", "engine_instructions/sentiment_engine.md",
    "engine_instructions/structural_engine.md", "engine_instructions/context_engine.md", "engine_instructions/technical_validator.md",
    "engine_instructions/bullish_gem.md", "engine_instructions/red_team_gem.md", "engine_instructions/neutral_gem.md", "engine_instructions/terminal.md",
    "engine_instructions/post_trade_review.md", "engine_instructions/macro_narrative_engine.md",
    "engine_instructions/data_analyst.md", "engine_instructions/state_validation_router.md", "engine_instructions/rule_enforcer_engine.md",
    "engine_instructions/execution.md", "engine_instructions/gex_engine.md",
    "engine_instructions/regime_engine.md", "engine_instructions/strategy_engine.md"
]

# 1. Define the Sub-Agents with their instructions and specific tools
macro_sentinel = framework.create_agent_tool(
    name="Macro Sentinel",
    file_path="engine_instructions/macro_sentinel.md",
    mode="PRO",
    agent_tools=[tools.read_ssot, tools.perform_web_forensic_search]
)

research_engine = framework.create_agent_tool(
    name="Research Engine", 
    file_path="engine_instructions/research.md", 
    mode="THINKING", 
    agent_tools=[tools.perform_web_forensic_search]
)

macro_narrative_engine = framework.create_agent_tool(
    name="Macro Narrative Engine",
    file_path="engine_instructions/macro_narrative_engine.md",
    mode="THINKING",
    agent_tools=[tools.perform_web_forensic_search, tools.read_ssot, tools.get_market_data]
)

data_analyst = framework.create_agent_tool(
    name="Data Analyst",
    file_path="engine_instructions/data_analyst.md",
    mode="PRO",
    agent_tools=[tools.perform_web_forensic_search, tools.get_market_data]
)

state_validation_router = framework.create_agent_tool(
    name="State Validation Router",
    file_path="engine_instructions/state_validation_router.md",
    mode="PRO",
    agent_tools=[tools.read_ssot, tools.update_ssot, tools.get_market_data]
)

rule_enforcer_engine = framework.create_agent_tool(
    name="Rule Enforcer Engine",
    file_path="engine_instructions/rule_enforcer_engine.md",
    mode="GEMMA",
    agent_tools=[tools.read_ssot, tools.read_trade_lessons, tools.get_market_data]
)

execution = framework.create_agent_tool(
    name="Execution Engine",
    file_path="engine_instructions/execution.md",
    mode="GEMMA",
    agent_tools=[tools.read_ssot, tools.read_trade_lessons, tools.get_market_data]
)

gex_engine = framework.create_agent_tool(
    name="GEX Engine",
    file_path="engine_instructions/gex_engine.md",
    mode="GEMMA",
    agent_tools=[tools.read_ssot, tools.read_trade_lessons, tools.get_market_data]
)

regime_engine = framework.create_agent_tool(
    name="Regime Engine",
    file_path="engine_instructions/regime_engine.md",
    mode="GEMMA",
    agent_tools=[tools.read_ssot, tools.read_trade_lessons, tools.get_market_data]
)

strategy_engine = framework.create_agent_tool(
    name="Strategy Engine",
    file_path="engine_instructions/strategy_engine.md",
    mode="GEMMA",
    agent_tools=[tools.read_ssot, tools.read_trade_lessons, tools.get_market_data]
)

sentiment_engine = framework.create_agent_tool(
    name="Sentiment Engine", 
    file_path="engine_instructions/sentiment_engine.md", 
    mode="GEMMA",
    agent_tools=[tools.perform_web_forensic_search]
)

structural_engine = framework.create_agent_tool(
    name="Structural Engine", 
    file_path="engine_instructions/structural_engine.md", 
    mode="GEMMA",
    agent_tools=[tools.read_ssot, tools.get_market_data, tools.perform_web_forensic_search]
)

context_engine = framework.create_agent_tool(
    name="Context Engine", 
    file_path="engine_instructions/context_engine.md", 
    mode="GEMMA",
    agent_tools=[tools.read_ssot, tools.update_ssot, tools.read_trade_lessons, tools.update_trade_lessons, tools.get_market_data, tools.perform_web_forensic_search, tools.update_rules]
)

technical_validator = framework.create_agent_tool(
    name="Technical Validator",
    file_path="engine_instructions/technical_validator.md",
    mode="GEMMA",
    agent_tools=[tools.read_ssot, tools.read_trade_lessons, tools.get_market_data, tools.perform_web_forensic_search]
)

post_trade_review = framework.create_agent_tool(
    name="Post-Trade Review Engine",
    file_path="engine_instructions/post_trade_review.md",
    mode="FAST",
    agent_tools=[tools.read_decision_log, tools.read_trade_lessons, tools.update_trade_lessons, tools.perform_web_forensic_search]
)

# Council Members
bullish_advocate = framework.create_agent_tool(
    name="Bullish Advocate", 
    file_path="engine_instructions/bullish_gem.md",
    mode="THINKING",
    agent_tools=[tools.perform_web_forensic_search]
)

red_team_pessimist = framework.create_agent_tool(
    name="Red Team Pessimist", 
    file_path="engine_instructions/red_team_gem.md",
    mode="THINKING",
    agent_tools=[tools.perform_web_forensic_search]
)


neutral_structuralist = framework.create_agent_tool(
    name="Neutral Structuralist", 
    file_path="engine_instructions/neutral_gem.md", 
    mode="GEMMA",
    agent_tools=[tools.perform_web_forensic_search]
)

# 2. Create the Council Dispatcher
council_members = {
    "ask_macro_sentinel": macro_sentinel,
    "ask_research_engine": research_engine,
    "ask_macro_narrative_engine": macro_narrative_engine,
    "ask_data_analyst": data_analyst,
    "ask_state_validation_router": state_validation_router,
    "ask_rule_enforcer_engine": rule_enforcer_engine,
    "ask_execution_engine": execution,
    "ask_gex_engine": gex_engine,
    "ask_sentiment_engine": sentiment_engine,
    "ask_structural_engine": structural_engine,
    "ask_context_engine": context_engine,
    "ask_bullish_advocate": bullish_advocate,
    "ask_red_team_pessimist": red_team_pessimist,
    "ask_neutral_structuralist": neutral_structuralist,
    "ask_technical_validator": technical_validator,
    "ask_regime_engine": regime_engine,
    "ask_strategy_engine": strategy_engine
}
ask_council = framework.create_parallel_council_tool(council_members)

# 3. Define the final toolset for the Orchestrator
terminal_tools = [
    tools.read_ssot,
    tools.update_ssot,
    tools.read_trade_lessons,
    tools.update_trade_lessons,
    tools.get_market_data,
    tools.read_decision_log,
    macro_sentinel,
    research_engine,
    macro_narrative_engine,
    data_analyst,
    state_validation_router,
    rule_enforcer_engine,
    execution,
    gex_engine,
    sentiment_engine,
    structural_engine,
    context_engine,
    technical_validator,
    post_trade_review,
    bullish_advocate,
    red_team_pessimist,
    neutral_structuralist,
    regime_engine,
    strategy_engine,
    ask_council
]

# Load Orchestrator context
terminal_instruction = framework.load_system_instruction("engine_instructions/terminal.md")

all_tools = terminal_tools + [tools.perform_web_forensic_search]



# Resolve Orchestrator dynamically using _resolve_orchestrator
ORCHESTRATOR_MODEL = framework._resolve_orchestrator()



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

@app.post("/api/cancel_chat")
def cancel_chat():
    global cancel_event
    cancel_event.set()
    framework.log("[System] Cancellation signal received.")
    return {"status": "cancelled"}

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
        models = [
            {"name": "gemini-3.8-flash-high", "label": "GEMINI-3.8-FLASH-HIGH (ANTIGRAVITY PRO/THINKING)"},
            {"name": "gemini-3.8-flash-low", "label": "GEMINI-3.8-FLASH-LOW (ANTIGRAVITY FAST)"},
            {"name": "gemini-4-argon-high", "label": "GEMINI-4-ARGON-HIGH (ANTIGRAVITY EXPERT)"},
            {"name": "claude-opus-5-5-high", "label": "CLAUDE-OPUS-5.5-HIGH (ANTIGRAVITY REASONING)"},
            {"name": "gpt-oss-120b-medium", "label": "GPT-OSS-120B-MEDIUM (ANTIGRAVITY OPEN)"},
        ]
        global active_model_warning
        active_model_warning = check_model_validity(ORCHESTRATOR_MODEL)
        return {"status": "success", "models": models, "current_model": ORCHESTRATOR_MODEL, "warning": active_model_warning}
    except Exception as e:
        framework.log(f"[Error] Failed to compile models: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/api/reset_chat")
def reset_chat():
    global global_chat_session, _session_hydrated
    global_chat_session = create_new_session()
    _session_hydrated = False
    framework.log("[System] Chat session reset.")
    return {"status": "success"}

@app.post("/api/set_cache_policy")
def set_cache_policy(data: dict):
    global global_chat_session, _session_hydrated, ORCHESTRATOR_MODEL
    disable_cache = data.get("disable_cache", False)
    
    # Update config.json
    try:
        config_path = "context/config.json"
        config_data = {}
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = json.load(f)
        
        config_data["DISABLE_CACHE"] = disable_cache
        
        # Ensure context directory exists
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)
    except Exception as ce:
        framework.log(f"[System Error] Failed to write cache policy to config.json: {ce}")

    print("[System] Context Caching is permanently deprecated.")
    
    # Rebuild session
    global_chat_session = create_new_session()
    _session_hydrated = False
    return {"status": "success", "disable_cache": True}

@app.post("/api/set_model")
def set_model(data: dict):
    global ORCHESTRATOR_MODEL, global_chat_session, _session_hydrated
    # Fallback to current model if none provided
    new_model = data.get("model", ORCHESTRATOR_MODEL)
    include_paid = data.get("include_paid", True)
    gemini_linked = data.get("gemini_subscription_linked", False)

    # Update config.json
    try:
        config_path = "context/config.json"
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = json.load(f)
            
            if config_data.get("GEMINI_SUBSCRIPTION_LINKED") != gemini_linked:
                config_data["GEMINI_SUBSCRIPTION_LINKED"] = gemini_linked
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(config_data, f, indent=2)
    except Exception as ce:
        framework.log(f"[System Error] Failed to write subscription policy to config.json: {ce}")

    framework.gemini_subscription_linked = gemini_linked

    # Dynamically toggle free_tier_only in the framework
    old_free = getattr(framework, "free_tier_only", True)
    framework.free_tier_only = not include_paid
    if old_free != framework.free_tier_only:
        framework.log(f"[System] Paid Tiers Policy toggled. Free Tier Only: {framework.free_tier_only}")

    if new_model != ORCHESTRATOR_MODEL or old_free != framework.free_tier_only:
        framework.log(f"[System] Switching Reasoning Tier to {new_model}...")
        ORCHESTRATOR_MODEL = new_model
        
        # Force a fresh session on next chat
        global_chat_session = None 
        _session_hydrated = False
        
        # Re-create session
        global_chat_session = create_new_session()
        framework.log(f"[System] Council re-calibrated on {new_model}.")
    global active_model_warning
    active_model_warning = check_model_validity(ORCHESTRATOR_MODEL)
    return {"status": "success", "current_model": ORCHESTRATOR_MODEL, "warning": active_model_warning}

@app.post("/api/chat")
def chat_endpoint(req: ChatRequest):
    """Primary chat route. Auto-injects live GLOBAL_STATE as DATA_PACKET before each user message."""
    global global_chat_session, cancel_event, _session_hydrated, ORCHESTRATOR_MODEL
    
    # Ensure session exists (handle model switches)
    if global_chat_session is None:
        global_chat_session = create_new_session()
    session = global_chat_session

    cancel_event.clear()
    framework.reset_turn_usage()
    try:
        # 1. Map names to actual tool functions for manual execution
        tool_map = {f.__name__: f for f in terminal_tools}
        
        import datetime
        # Calculate US/Eastern (New York) Time (EDT is UTC-4)
        ny_time = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-4)))
        current_iso = ny_time.strftime("%Y-%m-%dT%H:%M:%S")

        # --- AUTO DATA INJECTION ---
        # Build a structured DATA_PACKET from live GLOBAL_STATE for automatic context hydration.
        # This replaces all manual clipboard copy/paste operations.
        framework.log("[System] Auto-injecting live DATA_PACKET into context...")
        try:
            from fetch_stocks import GLOBAL_STATE as live_state
            ssot = live_state.get("local_storage_state", {})
            ms = ssot.get("mutable_state", ssot)  # support both nested and flat layouts
            portfolio = ms.get("portfolio_snapshot", ssot.get("portfolio_snapshot", []))
            state_ctx = ms.get("state_context", ssot.get("state_context", {}))

            # Slim tickers — only key fields for context efficiency
            slim_tickers = [
                {
                    "ticker": t.get("ticker"),
                    "price": t.get("price"),
                    "session_change_pct": t.get("session_change_pct"),
                    "gap_percent": t.get("gap_percent"),
                    "rsi": t.get("rsi"),
                    "macd": t.get("macd", 0.0),
                    "macd_signal": t.get("macd_signal", 0.0),
                    "macd_hist": t.get("macd_hist", 0.0),
                    "macd_slope": t.get("macd_slope", "EXPANDING_POSITIVE"),
                    "macd_status": t.get("macd_status", "NEUTRAL"),
                    "atr_percent": t.get("atr_percent"),
                    "vwap": t.get("vwap"),
                    "trend": t.get("trend"),
                    "signal": t.get("signal"),
                    "score": t.get("score"),
                    "dealer_posture": t.get("dealer_posture"),
                    "net_gex_total": round(t.get("net_gex_total", 0.0), 3) if t.get("net_gex_total") is not None else 0.0,
                    "fib_forecast": t.get("fib_forecast"),
                    "note": t.get("note"),
                }
                for t in (live_state.get("tickers") or [])
            ]

            # Slim portfolio
            slim_portfolio = [
                {
                    "ticker": p.get("ticker"),
                    "shares": p.get("shares"),
                    "wac": p.get("wac"),
                    "status": p.get("status"),
                    "action": p.get("action"),
                }
                for p in portfolio
            ] if portfolio else []

            # Load trade lessons
            trade_lessons = live_state.get("trade_lessons", [])
            if not trade_lessons and os.path.exists("context/trade_lessons.json"):
                try:
                    with open("context/trade_lessons.json", "r", encoding="utf-8") as f:
                        td = json.load(f)
                        trade_lessons = td.get("trade_lessons", td) if isinstance(td, dict) else td
                except: pass

            # Load watched tickers & scout categories
            watched = ms.get("watched_tickers", ssot.get("watched_tickers", []))
            scouts = ms.get("scout_categories", ssot.get("scout_categories", []))

            # Load decision log
            decision_log = []
            if os.path.exists("context/decision_log.json"):
                try:
                    with open("context/decision_log.json", "r", encoding="utf-8") as f:
                        dd = json.load(f)
                        decision_log = dd.get("decision_log", dd) if isinstance(dd, dict) else dd
                except: pass

            data_packet = {
                "_meta": live_state.get("_meta", {}),
                "timestamp": live_state.get("timestamp", current_iso),
                "market_status": live_state.get("status", "UNKNOWN"),
                "tickers": slim_tickers,
                "ssot": ssot,
                "trade_lessons": trade_lessons[-10:] if isinstance(trade_lessons, list) else [],
                "decision_log": decision_log[-10:] if isinstance(decision_log, list) else [],
            }
            data_packet_json = json.dumps(data_packet, indent=2, default=str)
            auto_data_block = load_data_packet_injection_prompt(data_packet_json)
            framework.log(f"[System] DATA_PACKET injected: {len(slim_tickers)} tickers, {len(slim_portfolio)} positions.")
        except Exception as de:
            auto_data_block = f"\n[SYSTEM NOTE: Live data unavailable — {de}]\n"
            framework.log(f"[System] DATA_PACKET injection failed: {de}")
        # --- END AUTO DATA INJECTION ---
        
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
        
        chart_parts, chart_symbols = get_chart_parts()
        if chart_parts:
            framework.log(f"[Vision] Attaching {len(chart_parts)} fresh 1m chart screenshot(s) [{', '.join(chart_symbols)}] to Gemini multimodal prompt...")

        from tool_dispatcher import GLOBAL_TOOL_DISPATCHER
        payload_to_send = [*chart_parts, current_message] if chart_parts else current_message

        dispatch_res = GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session(
            session=session,
            initial_message=payload_to_send,
            part_factory=agent_framework.types.Part,
            agent_role="Terminal Orchestrator",
            cancel_check=lambda: cancel_event.is_set(),
            log_callback=framework.log,
        )

        if dispatch_res.get("status") == "cancelled":
            return {"status": "success", "response": dispatch_res["response"]}

        full_response = dispatch_res["response"]
        if not full_response:
            framework.log("[Orchestrator] WARNING: No text captured across all turns.")
            return {"status": "success", "response": "_[The model performed its internal reasoning and tools but did not emit a final text summary. Please try again or check system logs.]_"}
        
        # Intercept and process EXECUTION_PAYLOAD dynamically (purging it from user display)
        full_response_original = full_response
        cleaned_response = full_response
        payload_processed = False
        
        # Look for json markdown code block wrappers
        idx = full_response.find("```json")
        if idx == -1:
            idx = full_response.find("```")
            
        if idx != -1:
            close_idx = full_response.find("```", idx + 3)
            # Handle truncated code block
            is_truncated = (close_idx == -1)
            actual_close_idx = len(full_response) if is_truncated else close_idx
            
            block_content = full_response[idx:actual_close_idx + (0 if is_truncated else 3)]
            if "EXECUTION_PAYLOAD" in block_content or "thoughtSignature" in block_content or "portfolio_snapshot" in block_content:
                inner_json = block_content
                if inner_json.startswith("```json"):
                    inner_json = inner_json[7:]
                elif inner_json.startswith("```"):
                    inner_json = inner_json[3:]
                
                if not is_truncated:
                    inner_json = inner_json[:-3]
                inner_json = inner_json.strip()
                    
                framework.log("[System] Auto-processing slice-extracted EXECUTION_PAYLOAD...")
                try:
                    res = tools.update_ssot(inner_json)
                    framework.log(f"[System] SSoT update result: {res}")
                    payload_html = (
                        f'\n\n<details class="execution-payload-details" style="margin: 15px 0; cursor: pointer; color: #8b949e; border-left: 2px solid var(--accent-blue); padding-left: 15px; background: rgba(255,255,255,0.02); border-radius: 8px; padding-top: 8px; padding-bottom: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); border: 1px solid rgba(255,255,255,0.05);">'
                        f'\n<summary style="font-weight: 700; color: var(--text-secondary); letter-spacing: 0.5px; outline: none; user-select: none;">⚖️ SSoT Execution Payload (Hidden)</summary>'
                        f'\n<div style="margin-top: 12px; font-family: monospace; font-size: 0.82rem; overflow-x: auto;">\n\n```json\n{inner_json}\n```\n\n</div>\n</details>\n\n'
                    )
                    cleaned_response = full_response[:idx] + payload_html + full_response[actual_close_idx + (0 if is_truncated else 3):]
                    payload_processed = True
                except Exception as pe:
                    framework.log(f"[System Error] Failed to update SSoT with sliced payload: {pe}")
                    # Even if parsing fails, hide the broken payload from the chat UI
                    payload_html = (
                        f'\n\n<details class="execution-payload-details" style="margin: 15px 0; cursor: pointer; color: #f85149; border-left: 2px solid var(--red); padding-left: 15px; background: rgba(248,81,73,0.05); border-radius: 8px; padding-top: 8px; padding-bottom: 8px; border: 1px solid rgba(248,81,73,0.1);">'
                        f'\n<summary style="font-weight: 700; color: var(--red); letter-spacing: 0.5px; outline: none; user-select: none;">⚠️ Incomplete SSoT Payload (Truncated)</summary>'
                        f'\n<div style="margin-top: 12px; font-family: monospace; font-size: 0.82rem; overflow-x: auto; color: var(--text-secondary);">\n\n```json\n{inner_json}\n```\n\n</div>\n</details>\n\n'
                    )
                    cleaned_response = full_response[:idx] + payload_html + full_response[actual_close_idx + (0 if is_truncated else 3):]
                    payload_processed = True
                        
        if not payload_processed:
            # Fallback to direct curly brace extraction
            start_idx = full_response.find('{')
            end_idx = full_response.rfind('}')
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                candidate = full_response[start_idx:end_idx+1]
                if "EXECUTION_PAYLOAD" in candidate or "thoughtSignature" in candidate or "portfolio_snapshot" in candidate:
                    # Clean up code blocks surrounding braces
                    prefix = full_response[:start_idx].strip()
                    clean_start_idx = start_idx
                    if prefix.endswith("```json"):
                        clean_start_idx = full_response[:start_idx].rfind("```json")
                    elif prefix.endswith("```"):
                        clean_start_idx = full_response[:start_idx].rfind("```")
                        
                    suffix = full_response[end_idx+1:].strip()
                    clean_end_idx = end_idx + 1
                    if suffix.startswith("```"):
                        rel_idx = full_response[end_idx+1:].find("```")
                        if rel_idx != -1:
                            clean_end_idx = end_idx + 1 + rel_idx + 3
                            
                    try:
                        json.loads(candidate)
                        framework.log("[System] Auto-processing brace-extracted EXECUTION_PAYLOAD...")
                        res = tools.update_ssot(candidate)
                        framework.log(f"[System] SSoT update result: {res}")
                        payload_html = (
                            f'\n\n<details class="execution-payload-details" style="margin: 15px 0; cursor: pointer; color: #8b949e; border-left: 2px solid var(--accent-blue); padding-left: 15px; background: rgba(255,255,255,0.02); border-radius: 8px; padding-top: 8px; padding-bottom: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); border: 1px solid rgba(255,255,255,0.05);">'
                            f'\n<summary style="font-weight: 700; color: var(--text-secondary); letter-spacing: 0.5px; outline: none; user-select: none;">⚖️ SSoT Execution Payload (Hidden)</summary>'
                            f'\n<div style="margin-top: 12px; font-family: monospace; font-size: 0.82rem; overflow-x: auto;">\n\n```json\n{candidate}\n```\n\n</div>\n</details>\n\n'
                        )
                        cleaned_response = full_response[:clean_start_idx] + payload_html + full_response[clean_end_idx:]
                        payload_processed = True
                    except Exception as pe:
                        framework.log(f"[System Error] Failed to update SSoT with brace payload: {pe}")
                        # Even if parsing fails, hide the broken payload from the chat UI
                        payload_html = (
                            f'\n\n<details class="execution-payload-details" style="margin: 15px 0; cursor: pointer; color: #f85149; border-left: 2px solid var(--red); padding-left: 15px; background: rgba(248,81,73,0.05); border-radius: 8px; padding-top: 8px; padding-bottom: 8px; border: 1px solid rgba(248,81,73,0.1);">'
                            f'\n<summary style="font-weight: 700; color: var(--red); letter-spacing: 0.5px; outline: none; user-select: none;">⚠️ Incomplete SSoT Payload (Truncated)</summary>'
                            f'\n<div style="margin-top: 12px; font-family: monospace; font-size: 0.82rem; overflow-x: auto; color: var(--text-secondary);">\n\n```json\n{candidate}\n```\n\n</div>\n</details>\n\n'
                        )
                        cleaned_response = full_response[:clean_start_idx] + payload_html + full_response[clean_end_idx:]
                        payload_processed = True
                        
        if payload_processed:
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
            # Clean trailing dividers if any
            if cleaned_response.endswith("---"):
                cleaned_response = cleaned_response[:-3].strip()
            
            # Robust fallback if the AI generated NO conversational text
            if not cleaned_response:
                cleaned_response = "*(The AI Council executed its routine and synchronized the SSoT, but did not output a conversational summary for this turn.)*"
                
            # Append success badge
            cleaned_response += "\n\n---\n\n*⚖️ SSoT Shadow State synchronized successfully.*"
            full_response = cleaned_response
        else:
            # Fallback to decision logging if no payload was parsed/updated
            try:
                tools.intercept_and_log_decision(full_response)
            except Exception as ie:
                framework.log(f"[System Warning] Decision interception inside chat_endpoint failed: {ie}")
            
        import re
        full_response = re.sub(
            r"TEMPORAL_CHECK:\s*[^\n\)]*\)\s*", 
            "", 
            full_response, 
            flags=re.IGNORECASE
        )

        try:
            with open("scratch/debug_output.txt", "w", encoding="utf-8") as df:
                df.write("=== RAW FULL RESPONSE ===\n")
                df.write(full_response_original + "\n\n")
                df.write("=== CLEANED RESPONSE ===\n")
                df.write(full_response + "\n")
        except Exception as e:
            framework.log(f"Debug write failed: {e}")

        global active_model_warning
        active_model_warning = check_model_validity(ORCHESTRATOR_MODEL)
        
        # Log diagnostics to console/queue
        turn_cost = framework.turn_usage['estimated_cost']
        session_cost = framework.session_cost
        framework.log(f"[Diagnostics] Turn Cost: ${turn_cost:.4f} | Total Session Cost: ${session_cost:.4f}")

        return {
            "status": "success", 
            "response": full_response.strip(),
            "usage": framework.turn_usage,
            "model": ORCHESTRATOR_MODEL,
            "active_model": ORCHESTRATOR_MODEL,
            "fallback_occurred": fallback_occurred,
            "warning": active_model_warning
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        error_msg = str(e)
        if cancel_event.is_set() or "cancelled" in error_msg.lower():
            return {"status": "success", "response": "[OFFLINE] Session terminated by user interrupt."}
            


        framework.log(f"[Error] Chat endpoint exception: {error_msg}")
        if "429" in error_msg or "quota" in error_msg.lower() or "resource_exhausted" in error_msg.lower():
            return {
                "status": "error",
                "code": "quota_exhausted",
                "message": "Free tier quota exhausted. Upgrade to Paid Tier to continue."
            }
        return {"status": "error", "message": error_msg}

class DecisionLogRequest(BaseModel):
    lesson: str
    ticker: str = ""
    outcome: str = ""

@app.post("/api/save_decision_log")
def save_decision_log(req: DecisionLogRequest):
    """Auto-write a new lesson entry to decision_log.json from the AI Council."""
    from tool_registry import authorize_financial_mutation
    from atomic_io import atomic_update_json
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
        lessons_file = "context/decision_log.json"

        def _prepend_entry(td):
            existing = list(td.get("decision_log", td)) if isinstance(td, dict) else (list(td) if isinstance(td, list) else [])
            existing.insert(0, entry)
            return {"decision_log": existing}

        atomic_update_json(lessons_file, _prepend_entry, default_factory=dict)
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
    from atomic_io import atomic_write_json
    authorize_financial_mutation(
        operation_name="update_decision_log",
        target_resource="context/decision_log.json",
        payload={"decision_log": []},
    )
    try:
        atomic_write_json("context/decision_log.json", {"decision_log": []})
        framework.log("[System] Decision log cleared.")
        return {"status": "success"}
    except PermissionError:
        raise
    except Exception as e:
        framework.log(f"[Error] Decision log clear failed: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/api/tickers")
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

    with open("context/user_config.json", "w") as f:
        json.dump(req.dict(), f, indent=2)
        
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

    # Load existing config to update just the macro list
    config_path = "context/user_config.json"
    config = {"tickers": [], "macro": []}
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            try:
                config = json.load(f)
            except:
                pass
                
    config["macro"] = req.macro
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
        
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
        if os.path.exists("context/ssot.json"):
            with open("context/ssot.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                ms = data.get("mutable_state", {})
                ep = data.get("EXECUTION_PAYLOAD", {})
                state_ctx = ms.get("state_context", ep.get("state_context", data.get("state_context", {})))
                
                raw_basket = (
                    ms.get("portfolio_snapshot") or
                    data.get("portfolio_snapshot") or
                    ep.get("portfolio_snapshot") or
                    []
                )
                portfolio = [
                    {"ticker": i["ticker"], "shares": float(i.get("shares", 0)), "wac": float(i.get("wac", 0))} 
                    for i in raw_basket if isinstance(i, dict) and i.get("ticker")
                ]
                cash = (
                    ms.get("unallocated_cash_eur") if ms.get("unallocated_cash_eur") is not None else
                    data.get("unallocated_cash_eur") if data.get("unallocated_cash_eur") is not None else
                    ep.get("unallocated_cash_eur") if ep.get("unallocated_cash_eur") is not None else
                    state_ctx.get("unallocated_cash_eur", 0.0)
                )
                cash_usd = (
                    ms.get("unallocated_cash_usd") if ms.get("unallocated_cash_usd") is not None else
                    data.get("unallocated_cash_usd") if data.get("unallocated_cash_usd") is not None else
                    ep.get("unallocated_cash_usd") if ep.get("unallocated_cash_usd") is not None else
                    state_ctx.get("unallocated_cash_usd", 0.0)
                )
                import fetch_stocks
                rate = fetch_stocks.cache.prices.get("EURUSD=X", 1.08)
                return {
                    "portfolio": portfolio,
                    "unallocated_cash_eur": cash,
                    "unallocated_cash_usd": cash_usd,
                    "eurusd_rate": rate
                }
    except Exception as e:
        framework.log(f"[Error] get_basket failed: {e}")
    return {"portfolio": [], "unallocated_cash_eur": 0.0, "unallocated_cash_usd": 0.0, "eurusd_rate": 1.08}

@app.post("/api/basket")
def save_basket(req: BasketSaveRequest):
    from tool_registry import authorize_financial_mutation
    from atomic_io import atomic_update_json
    authorize_financial_mutation(
        operation_name="update_ssot",
        target_resource="context/ssot.json",
        payload=req.dict(),
    )
    try:
        # Validate portfolio symbols before saving (only active holdings with shares > 0)
        symbols = [item.ticker for item in req.portfolio if item.shares > 0]
        invalid = check_invalid_tickers(symbols)
        if invalid:
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid ticker symbol(s): {', '.join(invalid)}"
            )

        if os.path.exists("context/ssot.json"):
            new_snapshot = []
            for item in req.portfolio:
                if item.shares > 0:
                    new_snapshot.append({"ticker": item.ticker, "shares": item.shares, "wac": item.wac})
            
            import fetch_stocks
            rate = fetch_stocks.cache.prices.get("EURUSD=X", 1.08)
            usd_val = req.unallocated_cash_usd if req.unallocated_cash_usd > 0.0 else round(req.unallocated_cash_eur * rate, 2)

            def _update_basket_ssot(data):
                if not isinstance(data, dict):
                    data = {}
                data["portfolio_snapshot"] = new_snapshot
                data["unallocated_cash_eur"] = req.unallocated_cash_eur
                data["unallocated_cash_usd"] = usd_val

                if "mutable_state" in data and isinstance(data["mutable_state"], dict):
                    data["mutable_state"]["portfolio_snapshot"] = new_snapshot
                    data["mutable_state"]["unallocated_cash_eur"] = req.unallocated_cash_eur
                    data["mutable_state"]["unallocated_cash_usd"] = usd_val

                if "EXECUTION_PAYLOAD" in data and isinstance(data["EXECUTION_PAYLOAD"], dict):
                    data["EXECUTION_PAYLOAD"]["portfolio_snapshot"] = new_snapshot
                    data["EXECUTION_PAYLOAD"]["unallocated_cash_eur"] = req.unallocated_cash_eur
                    data["EXECUTION_PAYLOAD"]["unallocated_cash_usd"] = usd_val
                return data

            data = atomic_update_json("context/ssot.json", _update_basket_ssot, default_factory=dict)
            framework.log(f"[System] SSoT Basket & Cash updated: {len(new_snapshot)} holding(s). Cash: {req.unallocated_cash_eur} EUR | {req.unallocated_cash_usd} USD")
            
            # Hot-reload TICKERS in fetch_stocks so the scanning daemon immediately registers changes
            try:
                import fetch_stocks
                fetch_stocks.TICKERS = fetch_stocks._load_ssot_tickers()
                fetch_stocks.MACRO_TICKERS = fetch_stocks._load_macro_tickers()
                fetch_stocks.ALL_TICKERS = list(dict.fromkeys(fetch_stocks.TICKERS + fetch_stocks.MACRO_TICKERS))
                fetch_stocks.FORCE_REFRESH = True

                # Immediately update in-memory GLOBAL_STATE so the next GET /api/data instantly reflects changes
                if isinstance(getattr(fetch_stocks, "GLOBAL_STATE", None), dict):
                    fetch_stocks.GLOBAL_STATE["local_storage_state"] = data
                    held_set = {item["ticker"].upper() for item in new_snapshot}
                    watched_set = set(fetch_stocks._load_watchlist())
                    if "tickers" in fetch_stocks.GLOBAL_STATE and isinstance(fetch_stocks.GLOBAL_STATE["tickers"], list):
                        valid_active = held_set.union(watched_set)
                        fetch_stocks.GLOBAL_STATE["tickers"] = [
                            t for t in fetch_stocks.GLOBAL_STATE["tickers"]
                            if t.get("_isScout") or t.get("ticker", "").upper() in valid_active or t.get("ticker", "").upper() in fetch_stocks.MACRO_TICKERS
                        ]
            except Exception as se:
                framework.log(f"[Warning] Failed to hot-reload TICKERS: {se}")
                
            return {"status": "success", "portfolio": new_snapshot}
    except PermissionError:
        raise
    except HTTPException as he:
        raise he
    except Exception as e:
        framework.log(f"[Error] Basket Save Failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/watchlist")
def get_watch_list():
    try:
        from atomic_io import atomic_read_json, atomic_update_json
        watchlist = []
        # Load from config.json if it exists (ENH_83)
        if os.path.exists("context/config.json"):
            cfg = atomic_read_json("context/config.json", default={})
            watchlist = cfg.get("WATCHLIST", [])
        
        # Make sure ssot.json is in sync
        if os.path.exists("context/ssot.json"):
            data = atomic_read_json("context/ssot.json", default={})
            ms = data.get("mutable_state", data)
            if ms.get("watched_tickers") != watchlist:
                def _sync_wl(d):
                    if "mutable_state" in d and isinstance(d["mutable_state"], dict):
                        d["mutable_state"]["watched_tickers"] = watchlist
                    else:
                        d["watched_tickers"] = watchlist
                    return d
                atomic_update_json("context/ssot.json", _sync_wl, default_factory=dict)
                    
        return watchlist
    except Exception as e:
        framework.log(f"[Error] Failed to get watchlist: {e}")
    return []

from fastapi import Request

@app.post("/api/watchlist")
async def save_watch_list(req: Request):
    from tool_registry import authorize_financial_mutation
    from atomic_io import atomic_update_json
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

        # 1. Update config.json (ENH_83 SSoT active asset tracking)
        if os.path.exists("context/config.json"):
            def _update_cfg_wl(cfg):
                if not isinstance(cfg, dict):
                    cfg = {}
                cfg["WATCHLIST"] = watch_list
                return cfg
            atomic_update_json("context/config.json", _update_cfg_wl, default_factory=dict)
                
        # 2. Update ssot.json
        data = {}
        if os.path.exists("context/ssot.json"):
            def _update_ssot_wl(d):
                if not isinstance(d, dict):
                    d = {}
                if "mutable_state" in d and isinstance(d["mutable_state"], dict):
                    d["mutable_state"]["watched_tickers"] = watch_list
                else:
                    d["watched_tickers"] = watch_list
                return d
            data = atomic_update_json("context/ssot.json", _update_ssot_wl, default_factory=dict)
            framework.log("[System] Watch list updated in ssot.json and config.json.")
            
        # FORCE HOT-RELOAD OF DAEMON TICKERS
        try:
            import fetch_stocks
            fetch_stocks.TICKERS = fetch_stocks._load_ssot_tickers()
            fetch_stocks.ALL_TICKERS = fetch_stocks.TICKERS + fetch_stocks.MACRO_TICKERS
            fetch_stocks.FORCE_REFRESH = True
            if isinstance(getattr(fetch_stocks, "GLOBAL_STATE", None), dict):
                fetch_stocks.GLOBAL_STATE["local_storage_state"] = data
        except Exception as se:
            framework.log(f"[Warning] Failed to hot-reload TICKERS: {se}")
            
        return {"status": "success"}
    except PermissionError:
        raise
    except HTTPException as he:
        raise he
    except Exception as e:
        framework.log(f"[Error] Watch List Save Failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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
                with open("context/ssot.json", "r") as f:
                    ssot = json.load(f)
                ms = ssot.get("mutable_state", ssot)
                watchlist = ms.get("watched_tickers", ssot.get("watched_tickers", []))
            except Exception:
                pass

            if not watchlist:
                return JSONResponse({"status": "error", "message": "No tickers in Watchlist. Add tickers to the Watchlist first."})

            # Force a refresh cycle so daemon re-evaluates them
            fetch_stocks.FORCE_REFRESH = True
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
                with open("context/ssot.json", "r") as f:
                    ssot = json.load(f)
                ms = ssot.get("mutable_state", ssot)
                categories = ms.get("scout_categories", ssot.get("scout_categories", []))
            except Exception:
                pass

            if not categories:
                return JSONResponse({"status": "error", "message": "No Scout Sectors enabled. Toggle at least one sector to scan."})

            # Trigger the dynamic scout ticker discovery in background
            fetch_stocks.FORCE_REFRESH = True
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
        if os.path.exists("context/ssot.json"):
            with open("context/ssot.json", "r") as f:
                data = json.load(f)
                ms = data.get("mutable_state", data)
                return ms.get("scout_categories", [])
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
        fetch_stocks.FORCE_REFRESH = True
        print(f"[System] Tickers hot-reloaded and forced refresh triggered in background: {fetch_stocks.TICKERS}")
    except Exception as se:
        print(f"[Warning] Failed to hot-reload TICKERS in background: {se}")

@app.post("/api/scout_categories")
def save_scout_categories(categories: List[str], background_tasks: BackgroundTasks):
    try:
        from tool_registry import authorize_financial_mutation
        from atomic_io import atomic_update_json
        authorize_financial_mutation("update_ssot", "context/ssot.json", payload=categories)
        if os.path.exists("context/ssot.json"):
            def _update_scouts(data):
                if not isinstance(data, dict):
                    data = {}
                if "mutable_state" in data:
                    data["mutable_state"]["scout_categories"] = categories
                else:
                    data["scout_categories"] = categories
                return data
            atomic_update_json("context/ssot.json", _update_scouts, default_factory=dict)
            framework.log("[System] Scout categories updated.")
            
            # Queue the heavy Dynamic Search yfinance reload logic in the background
            background_tasks.add_task(reload_scout_tickers_task)
                
    except PermissionError:
        raise
    except Exception as e:
        framework.log(f"[Error] Scout Categories Save Failed: {e}")
    return {"status": "success", "categories": categories}

@app.get("/api/system_logs")
async def system_logs_endpoint():
    """SSE endpoint to stream system logs to the frontend."""
    global main_loop
    main_loop = asyncio.get_running_loop()
    queue = get_log_queue()
    
    # Flush existing queue messages to prevent stale logs from leaking into new chat sessions
    while not queue.empty():
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            break
            
    async def event_generator():
        while True:
            log_msg = await queue.get()
            yield f"data: {log_msg}\n\n"
            
    return StreamingResponse(event_generator(), media_type="text/event-stream")

# --- MOUNT STATIC AND RUN ---
if __name__ == "__main__":
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
    
    # Start the background data daemon
    daemon_thread = threading.Thread(target=run_daemon, daemon=True)
    daemon_thread.start()

    try:
        import fetch_stocks
        fetch_stocks.compile_master_document()
        # Cloud sync daemon removed — GDrive Decoupling Guardrail (ANTIGRAVITY v11.03)
    except Exception as e:
        print(f"Failed to compile master document: {e}")

    try:
        uvicorn.run(app, host="127.0.0.1", port=port)
    except Exception as e:
        print(f"CRITICAL: Web Server exited with error: {e}")
