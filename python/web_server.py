import os
from fastapi import BackgroundTasks, HTTPException
import sys
import uvicorn
import asyncio
import json
from typing import List
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
import threading

from agent_framework import AgentFramework
import agent_framework
import tools
import cloud_sync

# Import the existing FastAPI app from fetch_stocks
from fetch_stocks import app, run_daemon, GLOBAL_STATE

class BasketItem(BaseModel):
    ticker: str
    shares: float
    wac: float

class BasketRequest(BaseModel):
    basket: List[BasketItem]

class BasketSaveRequest(BaseModel):
    portfolio: List[BasketItem]
    unallocated_cash_eur: float

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
    "engine_instructions/execution.md", "engine_instructions/gex_engine.md"
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
    "ask_technical_validator": technical_validator
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
    ask_council
]

# Load Orchestrator context
terminal_instruction = framework.load_system_instruction("engine_instructions/terminal.md")

all_tools = terminal_tools + [tools.perform_web_forensic_search]

framework.setup_context_cache(
    subagent_files=subagent_instructions,
    system_instruction=terminal_instruction,
    tools=all_tools
)

if not getattr(framework, "cached_content_name", None):
    rules_path = os.path.join("gem_trading_rules", "rules.md")
    if os.path.exists(rules_path):
        with open(rules_path, "r", encoding="utf-8") as f:
            rules_content = f.read()
        terminal_instruction += f"\n\n--- ATTACHED KNOWLEDGE BASE (GEM_Rules_Data) ---\n{rules_content}"

# Find a valid model for the Orchestrator from the THINKING tier
terminal_models = framework._get_cloud_models("THINKING")
valid_model = None
for model_name in terminal_models:
    try:
        framework.client.models.generate_content(
            model=model_name, 
            contents="ping",
            config=agent_framework.types.GenerateContentConfig(
                http_options={'timeout': 30000}
            )
        )
        valid_model = model_name
        framework.log(f"[System] Web Orchestrator verified with {model_name}")
        break
    except Exception as e:
        error_str = str(e).lower()
        if "429" in error_str or "quota" in error_str:
            valid_model = model_name
            framework.log(f"[System] Web Orchestrator using {model_name} (Quota limited but verified)")
            break
        continue

# Dynamic fallback discovery: if thinking models are unsupported or unavailable, probe PRO and FLASH
if not valid_model:
    framework.log("[System] No valid THINKING model found. Scanning PRO and FLASH tiers for Orchestrator fallback...")
    for fallback_mode in ["PRO", "FLASH"]:
        for model_name in framework._get_cloud_models(fallback_mode):
            try:
                framework.client.models.generate_content(
                    model=model_name, 
                    contents="ping",
                    config=agent_framework.types.GenerateContentConfig(
                        http_options={'timeout': 10000}
                    )
                )
                valid_model = model_name
                framework.log(f"[System] Web Orchestrator verified with fallback {model_name}")
                break
            except Exception as e:
                error_str = str(e).lower()
                if "429" in error_str or "quota" in error_str:
                    valid_model = model_name
                    framework.log(f"[System] Web Orchestrator using fallback {model_name} (Quota limited)")
                    break
                continue
        if valid_model:
            break

# Use the verified valid model from the THINKING tier, fallback to framework default
ORCHESTRATOR_MODEL = valid_model or agent_framework.DEFAULT_MODEL_THINKING

# Initialize the global Chat object
def create_new_session():
    global ORCHESTRATOR_MODEL
    cache_to_use = None
    if getattr(framework, "cached_content_name", None):
        cache_to_use = framework.cached_content_name
        framework.log(f"[System] Binding Context Cache to Orchestrator chat: {cache_to_use}")
    else:
        framework.log("[System] Orchestrator session created without Context Cache.")

    return framework.client.chats.create(
        model=ORCHESTRATOR_MODEL,
        config=agent_framework.types.GenerateContentConfig(
            system_instruction=terminal_instruction if not cache_to_use else None,
            temperature=1.0,
            max_output_tokens=8192,
            tools=terminal_tools if not cache_to_use else None,
            cached_content=cache_to_use,
            safety_settings=[
                agent_framework.types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH",       threshold="BLOCK_NONE"),
                agent_framework.types.SafetySetting(category="HARM_CATEGORY_HARASSMENT",         threshold="BLOCK_NONE"),
                agent_framework.types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT",  threshold="BLOCK_NONE"),
                agent_framework.types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT",  threshold="BLOCK_NONE"),
            ]
        )
    )

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

@app.get("/api/list_models")
def list_models_endpoint():
    """Dynamically pull authorized models from Google GenAI with defensive canonical fallbacks."""
    try:
        models = []
        try:
            for m in framework.client.models.list():
                supported = getattr(m, 'supported_methods', getattr(m, 'supported_generation_methods', getattr(m, 'supported_actions', [])))
                name = getattr(m, "name", "").replace("models/", "")
                
                if not supported or "generateContent" in supported or "generate_content" in supported or "gemini" in name.lower():
                    label = name.upper()
                    if "pro" in name: label += " (PRO)"
                    elif "thinking" in name: label += " (THINKING)"
                    elif "flash" in name: label += " (FLASH)"
                    models.append({"name": name, "label": label})
        except Exception as le:
            framework.log(f"[Warning] Failed to dynamically list models from API: {le}. Proceeding with canonical injection.")

        # Ensure key canonical models (including experimental thinking models) are ALWAYS present
        guaranteed_models = [
            {"name": "gemini-2.0-flash-thinking-exp", "label": "GEMINI-2.0-FLASH-THINKING-EXP (THINKING)"},
            {"name": "gemini-2.0-flash", "label": "GEMINI-2.0-FLASH (FLASH)"},
            {"name": "gemini-2.5-pro", "label": "GEMINI-2.5-PRO (PRO)"},
            {"name": "gemini-2.5-flash", "label": "GEMINI-2.5-FLASH (FLASH)"},
            {"name": "gemini-3.1-pro-preview", "label": "GEMINI-3.1-PRO-PREVIEW (PRO)"},
            {"name": "gemini-1.5-pro", "label": "GEMINI-1.5-PRO (PRO)"},
            {"name": "gemini-1.5-flash", "label": "GEMINI-1.5-FLASH (FLASH)"}
        ]
        
        existing_names = {m["name"] for m in models}
        for gm in guaranteed_models:
            if gm["name"] not in existing_names:
                models.append(gm)

        # Sort so Thinking is first, followed by Pro, then Flash
        def sort_key(x):
            name = x["name"].lower()
            if "thinking" in name:
                return (0, name)
            elif "pro" in name:
                return (1, name)
            else:
                return (2, name)

        models.sort(key=sort_key)
        return {"status": "success", "models": models, "current_model": ORCHESTRATOR_MODEL}
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

    framework.cache_disabled = disable_cache
    if disable_cache:
        framework.log("[System] Context Caching manually disabled by user.")
        if getattr(framework, "cached_content_name", None):
            try:
                framework.log(f"[System] Cleaning up cached content: {framework.cached_content_name}")
                framework.client.caches.delete(name=framework.cached_content_name)
            except Exception as e:
                framework.log(f"[System] Cache delete failed: {e}")
        framework.cached_content_name = None
        framework.last_cache_hash = None
    else:
        framework.log("[System] Context Caching manually enabled by user. Re-building cache...")
        framework.setup_context_cache(
            model=ORCHESTRATOR_MODEL,
            subagent_files=subagent_instructions,
            system_instruction=terminal_instruction,
            tools=all_tools
        )
    
    # Rebuild session to apply caching changes
    global_chat_session = create_new_session()
    _session_hydrated = False
    return {"status": "success", "disable_cache": framework.cache_disabled}

@app.post("/api/set_model")
def set_model(data: dict):
    global ORCHESTRATOR_MODEL, global_chat_session, _session_hydrated
    # Fallback to current model if none provided
    new_model = data.get("model", ORCHESTRATOR_MODEL)
    include_paid = data.get("include_paid", True)

    # Dynamically toggle free_tier_only in the framework
    old_free = getattr(framework, "free_tier_only", True)
    framework.free_tier_only = not include_paid
    if old_free != framework.free_tier_only:
        framework.log(f"[System] Paid Tiers Policy toggled. Free Tier Only: {framework.free_tier_only}")

    if new_model != ORCHESTRATOR_MODEL or old_free != framework.free_tier_only:
        framework.log(f"[System] Switching Reasoning Tier to {new_model}...")
        ORCHESTRATOR_MODEL = new_model
        
        # Force a fresh session and cache rebuild on next chat
        global_chat_session = None 
        _session_hydrated = False
        
        # Explicitly trigger cache setup for the new model
        framework.setup_context_cache(
            model=ORCHESTRATOR_MODEL, 
            subagent_files=subagent_instructions,
            system_instruction=terminal_instruction,
            tools=all_tools
        )
        
        # Re-create session
        global_chat_session = create_new_session()
        framework.log(f"[System] Council re-calibrated on {new_model}.")
    return {"status": "success", "current_model": ORCHESTRATOR_MODEL}

@app.post("/api/chat")
def chat_endpoint(req: ChatRequest):
    """Primary chat route. Auto-injects live GLOBAL_STATE as DATA_PACKET before each user message."""
    global global_chat_session, cancel_event, _session_hydrated, ORCHESTRATOR_MODEL
    
    # Ensure session exists (handle model switches)
    if global_chat_session is None:
        global_chat_session = create_new_session()

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
                    "atr_percent": t.get("atr_percent"),
                    "vwap": t.get("vwap"),
                    "trend": t.get("trend"),
                    "signal": t.get("signal"),
                    "score": t.get("score"),
                    "dealer_posture": t.get("dealer_posture"),
                    "net_gex_total": t.get("net_gex_total"),
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
                "ssot": {
                    "portfolio_snapshot": slim_portfolio,
                    "watched_tickers": watched,
                    "scout_categories": scouts,
                    "unallocated_cash_eur": ms.get("unallocated_cash_eur", state_ctx.get("unallocated_cash_eur", 0)),
                    "total_liquidity_eur": ms.get("total_liquidity_eur", state_ctx.get("total_liquidity_eur", 0)),
                    "risk_regime": state_ctx.get("risk_regime", ""),
                },
                "trade_lessons": trade_lessons[-10:] if isinstance(trade_lessons, list) else [],
                "decision_log": decision_log[-10:] if isinstance(decision_log, list) else [],
            }
            data_packet_json = json.dumps(data_packet, indent=2, default=str)
            auto_data_block = (
                "\n[SYSTEM AUTO-INJECT: LIVE_DATA_PACKET — Do NOT ask the user to provide data, it is already below. CRITICAL DIRECTIVE: NEVER echo or output this JSON payload back to the user in your response.]\n"
                f"```json\n{data_packet_json}\n```\n"
                "[END_DATA_PACKET]\n"
            )
            framework.log(f"[System] DATA_PACKET injected: {len(slim_tickers)} tickers, {len(slim_portfolio)} positions.")
        except Exception as de:
            auto_data_block = f"\n[SYSTEM NOTE: Live data unavailable — {de}]\n"
            framework.log(f"[System] DATA_PACKET injection failed: {de}")
        # --- END AUTO DATA INJECTION ---
        
        # HYDRATION: If server restarted but browser has history, re-prime the LLM
        prompt_prefix = auto_data_block  # Always prepend live data
        if req.history and not _session_hydrated:
            framework.log("[System] Server restart detected. Hydrating memory from browser session...")
            history_context = "\n[CRITICAL: SESSION_HISTORY_RECOVERY_BLOCK]\n"
            history_context += "The following messages are from the current active session before the system was rebooted. Continue the conversation as if no interruption occurred:\n"
            for msg in req.history: 
                role = "USER" if msg["role"] == "user" else "COUNCIL"
                content = msg.get("text", "").replace("\n", " ")[:500]
                history_context += f"- {role}: {content}\n"
            prompt_prefix = auto_data_block + history_context + "\n[RECOVERY_COMPLETE: END_OF_PRIOR_CONTEXT]\n\n"
            _session_hydrated = True

        active_model_str = f"[ACTIVE_MODEL]: {ORCHESTRATOR_MODEL}\n"
        current_message = f"{active_model_str}{prompt_prefix}[SYSTEM_TIME (NEW YORK / ET): {current_iso}] [USER_QUERY]: {req.message}"
        
        all_text = []
        turn_count = 0
        
        while True:
            if cancel_event.is_set():
                framework.log("[Orchestrator] Interrupted by user.")
                return {"status": "success", "response": "[OFFLINE] Session terminated by user interrupt."}

            turn_count += 1
            framework.log(f"[Orchestrator] Starting turn {turn_count}...")
            response = global_chat_session.send_message(current_message)
            
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                framework.turn_usage['prompt_tokens'] += (response.usage_metadata.prompt_token_count or 0)
                framework.turn_usage['candidates_tokens'] += (response.usage_metadata.candidates_token_count or 0)
                framework.turn_usage['cached_tokens'] += (response.usage_metadata.cached_content_token_count or 0)
            
            if cancel_event.is_set():
                framework.log("[Orchestrator] Interrupted after model response.")
                return {"status": "success", "response": "[OFFLINE] Session terminated by user interrupt."}
            
            # Capture any text in this turn
            found_text_in_turn = False
            try:
                for part in response.candidates[0].content.parts:
                    if part.text:
                        txt = part.text.strip()
                        if txt:
                            framework.log(f"[Orchestrator] Turn {turn_count} captured {len(txt)} chars.")
                            all_text.append(txt)
                            found_text_in_turn = True
            except Exception as e:
                framework.log(f"[Orchestrator] Text capture warning: {e}")
                if hasattr(response, 'text') and response.text:
                    all_text.append(response.text)
                    found_text_in_turn = True
            
            # Manual function call handling
            if response.function_calls:
                framework.log(f"[Orchestrator] Turn {turn_count} requested {len(response.function_calls)} tools.")
                tool_responses = []
                for call in response.function_calls:
                    name = call.name
                    args = call.args or {}
                    framework.log(f"[Tool Execution] {name}")
                    
                    if name in tool_map:
                        try:
                            result = tool_map[name](**args)
                            tool_responses.append(agent_framework.types.Part.from_function_response(
                                name=name, response={'result': result}
                            ))
                        except Exception as te:
                            tool_responses.append(agent_framework.types.Part.from_function_response(
                                name=name, response={'error': str(te)}
                            ))
                    else:
                        tool_responses.append(agent_framework.types.Part.from_function_response(
                            name=name, response={'error': 'Tool not found'}
                        ))
                
                current_message = tool_responses
                continue
            
            break
        
        if not all_text:
            framework.log("[Orchestrator] WARNING: No text captured across all turns.")
            return {"status": "success", "response": "_[The model performed its internal reasoning and tools but did not emit a final text summary. Please try again or check system logs.]_"}

        full_response = "\n\n---\n\n".join(all_text)
        
        # Intercept and process EXECUTION_PAYLOAD dynamically (purging it from user display)
        cleaned_response = full_response
        payload_processed = False
        
        # Look for json markdown code block wrappers
        idx = full_response.find("```json")
        if idx == -1:
            idx = full_response.find("```")
            
        if idx != -1:
            close_idx = full_response.find("```", idx + 3)
            if close_idx != -1:
                block_content = full_response[idx:close_idx + 3]
                if "EXECUTION_PAYLOAD" in block_content:
                    inner_json = block_content
                    if inner_json.startswith("```json"):
                        inner_json = inner_json[7:-3].strip()
                    elif inner_json.startswith("```"):
                        inner_json = inner_json[3:-3].strip()
                        
                    framework.log("[System] Auto-processing slice-extracted EXECUTION_PAYLOAD...")
                    try:
                        res = tools.update_ssot(inner_json)
                        framework.log(f"[System] SSoT update result: {res}")
                        cleaned_response = full_response[:idx] + full_response[close_idx + 3:]
                        payload_processed = True
                    except Exception as pe:
                        framework.log(f"[System Error] Failed to update SSoT with sliced payload: {pe}")
                        
        if not payload_processed and "EXECUTION_PAYLOAD" in full_response:
            # Fallback to direct curly brace extraction
            start_idx = full_response.find('{')
            end_idx = full_response.rfind('}')
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                candidate = full_response[start_idx:end_idx+1]
                if "EXECUTION_PAYLOAD" in candidate:
                    try:
                        json.loads(candidate)
                        framework.log("[System] Auto-processing brace-extracted EXECUTION_PAYLOAD...")
                        res = tools.update_ssot(candidate)
                        framework.log(f"[System] SSoT update result: {res}")
                        cleaned_response = full_response[:start_idx] + full_response[end_idx+1:]
                        payload_processed = True
                    except Exception as pe:
                        framework.log(f"[System Error] Failed to update SSoT with brace payload: {pe}")
                        
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
            # Append success badge
            cleaned_response += "\n\n---\n\n*⚖️ SSoT Shadow State synchronized successfully.*"
            full_response = cleaned_response
        else:
            # Fallback to decision logging if no payload was parsed/updated
            try:
                tools.intercept_and_log_decision(full_response)
            except Exception as ie:
                framework.log(f"[System Warning] Decision interception inside chat_endpoint failed: {ie}")
            
        return {
            "status": "success", 
            "response": full_response.strip(),
            "usage": framework.turn_usage,
            "model": ORCHESTRATOR_MODEL
        }
    except Exception as e:
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
        existing = []
        if os.path.exists(lessons_file):
            try:
                with open(lessons_file, "r", encoding="utf-8") as f:
                    td = json.load(f)
                    existing = td.get("decision_log", td) if isinstance(td, dict) else td
            except: pass
        existing.insert(0, entry)
        with open(lessons_file, "w", encoding="utf-8") as f:
            json.dump({"decision_log": existing}, f, indent=2)
        framework.log(f"[System] Decision log saved: {req.ticker} — {req.lesson[:60]}...")
        return {"status": "success", "entry": entry}
    except Exception as e:
        framework.log(f"[Error] Decision log save failed: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/api/clear_decision_log")
def clear_decision_log():
    """Wipe decision_log.json — all entries removed."""
    try:
        with open("context/decision_log.json", "w", encoding="utf-8") as f:
            json.dump({"decision_log": []}, f, indent=2)
        framework.log("[System] Decision log cleared.")
        return {"status": "success"}
    except Exception as e:
        framework.log(f"[Error] Decision log clear failed: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/api/tickers")
@app.get("/api/get_settings")
def get_settings():
    if os.path.exists("context/user_config.json"):
        with open("context/user_config.json", "r") as f:
            return json.load(f)
    return {"tickers": [], "macro": []}

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
            with open("context/ssot.json", "r") as f:
                data = json.load(f)
                ms = data.get("mutable_state", data)
                raw_basket = ms.get("portfolio_snapshot", [])
                portfolio = [{"ticker": i["ticker"], "shares": i.get("shares", 0), "wac": i.get("wac", 0)} for i in raw_basket]
                cash = ms.get("unallocated_cash_eur", 0.0)
                state_ctx = ms.get("state_context", {})
                rate = ms.get("eurusd_rate", state_ctx.get("eurusd_rate", 1.08))
                return {
                    "portfolio": portfolio,
                    "unallocated_cash_eur": cash,
                    "eurusd_rate": rate
                }
    except Exception as e:
        framework.log(f"[Error] get_basket failed: {e}")
    return {"portfolio": [], "unallocated_cash_eur": 0.0, "eurusd_rate": 1.08}

@app.post("/api/basket")
def save_basket(req: BasketSaveRequest):
    try:
        # Validate portfolio symbols before saving
        symbols = [item.ticker for item in req.portfolio]
        invalid = check_invalid_tickers(symbols)
        if invalid:
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid ticker symbol(s): {', '.join(invalid)}"
            )

        if os.path.exists("context/ssot.json"):
            with open("context/ssot.json", "r") as f:
                data = json.load(f)
            new_snapshot = []
            for item in req.portfolio:
                new_snapshot.append({"ticker": item.ticker, "shares": item.shares, "wac": item.wac})
            
            if "mutable_state" in data:
                data["mutable_state"]["portfolio_snapshot"] = new_snapshot
                data["mutable_state"]["unallocated_cash_eur"] = req.unallocated_cash_eur
            else:
                data["portfolio_snapshot"] = new_snapshot
                data["unallocated_cash_eur"] = req.unallocated_cash_eur
                
            with open("context/ssot.json", "w") as f:
                json.dump(data, f, indent=2)
            framework.log(f"[System] SSoT Basket & Cash updated. Cash: {req.unallocated_cash_eur} EUR")
            
            # Hot-reload TICKERS in fetch_stocks so the scanning daemon immediately registers changes
            try:
                import fetch_stocks
                fetch_stocks.TICKERS = fetch_stocks._load_ssot_tickers()
                fetch_stocks.ALL_TICKERS = fetch_stocks.TICKERS + fetch_stocks.MACRO_TICKERS
                fetch_stocks.FORCE_REFRESH = True
            except Exception as se:
                framework.log(f"[Warning] Failed to hot-reload TICKERS: {se}")
                
            return {"status": "success"}
    except HTTPException as he:
        raise he
    except Exception as e:
        framework.log(f"[Error] Basket Save Failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/watchlist")
def get_watch_list():
    try:
        watchlist = []
        # Load from config.json if it exists (ENH_83)
        if os.path.exists("context/config.json"):
            with open("context/config.json", "r") as f:
                cfg = json.load(f)
                watchlist = cfg.get("WATCHLIST", [])
        
        # Make sure ssot.json is in sync
        if os.path.exists("context/ssot.json"):
            with open("context/ssot.json", "r") as f:
                data = json.load(f)
            ms = data.get("mutable_state", data)
            if ms.get("watched_tickers") != watchlist:
                if "mutable_state" in data:
                    data["mutable_state"]["watched_tickers"] = watchlist
                else:
                    data["watched_tickers"] = watchlist
                with open("context/ssot.json", "w") as f:
                    json.dump(data, f, indent=2)
                    
        return watchlist
    except Exception as e:
        framework.log(f"[Error] Failed to get watchlist: {e}")
    return []

from fastapi import Request

@app.post("/api/watchlist")
async def save_watch_list(req: Request):
    try:
        payload = await req.json()
        
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
            with open("context/config.json", "r") as f:
                cfg = json.load(f)
            cfg["WATCHLIST"] = watch_list
            with open("context/config.json", "w") as f:
                json.dump(cfg, f, indent=2)
                
        # 2. Update ssot.json
        if os.path.exists("context/ssot.json"):
            with open("context/ssot.json", "r") as f:
                data = json.load(f)
            
            if "mutable_state" in data:
                data["mutable_state"]["watched_tickers"] = watch_list
            else:
                data["watched_tickers"] = watch_list
                
            with open("context/ssot.json", "w") as f:
                json.dump(data, f, indent=2)
            framework.log("[System] Watch list updated in ssot.json and config.json.")
            
        # FORCE HOT-RELOAD OF DAEMON TICKERS
        try:
            import fetch_stocks
            fetch_stocks.TICKERS = fetch_stocks._load_ssot_tickers()
            fetch_stocks.ALL_TICKERS = fetch_stocks.TICKERS + fetch_stocks.MACRO_TICKERS
            fetch_stocks.FORCE_REFRESH = True
        except Exception as se:
            framework.log(f"[Warning] Failed to hot-reload TICKERS: {se}")
            
        return {"status": "success"}
    except HTTPException as he:
        raise he
    except Exception as e:
        framework.log(f"[Error] Watch List Save Failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/scout")
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

@app.post("/api/scout")
def save_scout_categories(categories: List[str], background_tasks: BackgroundTasks):
    try:
        if os.path.exists("context/ssot.json"):
            with open("context/ssot.json", "r") as f:
                data = json.load(f)
                
            if "mutable_state" in data:
                data["mutable_state"]["scout_categories"] = categories
            else:
                data["scout_categories"] = categories
                
            with open("context/ssot.json", "w") as f:
                json.dump(data, f, indent=2)
            framework.log("[System] Scout categories updated.")
            
            # Queue the heavy Dynamic Search yfinance reload logic in the background
            background_tasks.add_task(reload_scout_tickers_task)
                
    except Exception as e:
        framework.log(f"[Error] Scout Categories Save Failed: {e}")
    return {"status": "success"}

@app.get("/api/system_logs")
async def system_logs_endpoint():
    """SSE endpoint to stream system logs to the frontend."""
    global main_loop
    main_loop = asyncio.get_running_loop()
    queue = get_log_queue()
    
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
    
    # Auto-select an available port starting from 8000
    import socket
    port = 8000
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", port))
                break  # Port is available
            except OSError:
                port += 1

    print(f"Starting Web Server on http://localhost:{port}")
    
    # Start the background data daemon
    daemon_thread = threading.Thread(target=run_daemon, daemon=True)
    daemon_thread.start()

    try:
        import glob
        sync_daemon = cloud_sync.CloudSyncDaemon(framework.client)
        sync_files = glob.glob("engine_instructions/*.md") + [
            os.path.join("gem_trading_rules", "rules.md"),
            "context/decision_log.json",
            "context/trade_lessons.json",
            "context/ssot.json"
        ]
        sync_thread = threading.Thread(target=sync_daemon.start_background_sync, args=(sync_files,), daemon=True)
        sync_thread.start()
    except Exception as e:
        print(f"Failed to start Cloud Sync Daemon: {e}")

    try:
        uvicorn.run(app, host="0.0.0.0", port=port)
    except Exception as e:
        print(f"CRITICAL: Web Server exited with error: {e}")
