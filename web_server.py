import os
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

# Import the existing FastAPI app from fetch_stocks
from fetch_stocks import app, run_daemon, GLOBAL_STATE

class BasketItem(BaseModel):
    ticker: str
    shares: float
    wac: float

class BasketRequest(BaseModel):
    basket: List[BasketItem]

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

# Initialize the AgentFramework with cloud-fallback
framework = AgentFramework(log_callback=log_to_queue)

# ---------------------------------------------------------------------------
# Subagent & Council Tool Definitions
# ---------------------------------------------------------------------------

# --- CONTEXT CACHING INITIALIZATION (ENH_CACHE_01) ---
subagent_instructions = [
    "macro_arbiter.json", "research.json", "sentiment_engine.json",
    "structural_engine.json", "context_engine.json", "technical_validator.json",
    "bullish_gem.json", "red_team_gem.json", "neutral_gem.json", "terminal.json"
]
framework.setup_context_cache(subagent_files=subagent_instructions)

# 1. Define the Sub-Agents with their instructions and specific tools
macro_sentinel = framework.create_agent_tool(
    name="Macro Sentinel",
    json_file="macro_arbiter.json",
    mode="PRO",
    agent_tools=[tools.read_ssot, tools.perform_web_forensic_search]
)

research_engine = framework.create_agent_tool(
    name="Research Engine", 
    json_file="research.json", 
    mode="PRO", 
    agent_tools=[tools.perform_web_forensic_search]
)

sentiment_engine = framework.create_agent_tool(
    name="Sentiment Engine", 
    json_file="sentiment_engine.json", 
    mode="PRO",
    agent_tools=[tools.perform_web_forensic_search]
)

structural_engine = framework.create_agent_tool(
    name="Structural Engine", 
    json_file="structural_engine.json", 
    mode="PRO",
    agent_tools=[tools.read_ssot, tools.get_market_data, tools.perform_web_forensic_search]
)

context_engine = framework.create_agent_tool(
    name="Context Engine", 
    json_file="context_engine.json", 
    mode="PRO",
    agent_tools=[tools.read_ssot, tools.update_ssot, tools.read_trade_lessons, tools.update_trade_lessons, tools.get_market_data, tools.perform_web_forensic_search, tools.update_rules]
)

technical_validator = framework.create_agent_tool(
    name="Technical Validator",
    json_file="technical_validator.json",
    mode="PRO",
    agent_tools=[tools.read_ssot, tools.read_trade_lessons, tools.get_market_data, tools.perform_web_forensic_search]
)

# Council Members
bullish_advocate = framework.create_agent_tool(
    name="Bullish Advocate", 
    json_file="bullish_gem.json",
    mode="PRO",
    agent_tools=[tools.perform_web_forensic_search]
)

red_team_pessimist = framework.create_agent_tool(
    name="Red Team Pessimist", 
    json_file="red_team_gem.json",
    mode="PRO",
    agent_tools=[tools.perform_web_forensic_search]
)


neutral_structuralist = framework.create_agent_tool(
    name="Neutral Structuralist", 
    json_file="neutral_gem.json",
    mode="PRO",
    agent_tools=[tools.perform_web_forensic_search]
)

# 2. Create the Council Dispatcher
council_members = {
    "ask_macro_sentinel": macro_sentinel,
    "ask_research_engine": research_engine,
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
    macro_sentinel,
    research_engine,
    sentiment_engine,
    structural_engine,
    context_engine,
    technical_validator,
    ask_council
]

# Load Orchestrator context
terminal_instruction = framework.load_system_instruction("terminal.json")
rules_path = os.path.join("GEM_Trading_Rules", "rules.json")
if not os.path.exists(rules_path):
    rules_path = os.path.join("GEM_Trading_Rules", "rules.md")

if os.path.exists(rules_path):
    with open(rules_path, "r", encoding="utf-8") as f:
        rules_content = f.read()
    terminal_instruction += f"\n\n--- ATTACHED KNOWLEDGE BASE (GEM_Rules_Data) ---\n{rules_content}"

# Find a valid model for the Orchestrator from the PRO tier
terminal_models = framework._get_cloud_models("PRO")
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

if not valid_model:
    framework.log("[Error] No valid cloud models found for Web Orchestrator!")
    valid_model = "gemini-1.5-flash" # Absolute last resort

# Initialize the global Chat object
def create_new_session():
    return framework.client.chats.create(
        model=valid_model,
        config=agent_framework.types.GenerateContentConfig(
            system_instruction=terminal_instruction,
            temperature=1.0,
            max_output_tokens=8192,
            tools=terminal_tools
        )
    )

global_chat_session = create_new_session()

class ChatRequest(BaseModel):
    message: str

import threading
cancel_event = threading.Event()

@app.post("/api/cancel_chat")
def cancel_chat():
    global cancel_event
    cancel_event.set()
    framework.log("[System] Cancellation signal received.")
    return {"status": "cancelled"}

@app.post("/api/reset_chat")
def reset_chat():
    global global_chat_session
    global_chat_session = create_new_session()
    framework.log("[System] Chat session reset.")
    return {"status": "success"}

@app.post("/api/chat")
def chat_endpoint(req: ChatRequest):
    """Primary chat route. Uses local fallback if cloud is exhausted."""
    global global_chat_session, cancel_event
    cancel_event.clear()
    try:
        # 1. Map names to actual tool functions for manual execution
        tool_map = {f.__name__: f for f in terminal_tools}
        
        import datetime
        # Calculate US/Eastern (New York) Time (EDT is UTC-4)
        ny_time = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-4)))
        current_iso = ny_time.strftime("%Y-%m-%dT%H:%M:%S")
        current_message = f"[SYSTEM_TIME (NEW YORK / ET): {current_iso}] [USER_QUERY]: {req.message}"
        
        all_text = []
        turn_count = 0
        
        while True:
            if cancel_event.is_set():
                framework.log("[Orchestrator] Interrupted by user.")
                return {"status": "success", "response": "[OFFLINE] Session terminated by user interrupt."}

            turn_count += 1
            framework.log(f"[Orchestrator] Starting turn {turn_count}...")
            response = global_chat_session.send_message(current_message)
            
            if cancel_event.is_set():
                framework.log("[Orchestrator] Interrupted after model response.")
                return {"status": "success", "response": "[OFFLINE] Session terminated by user interrupt."}
            
            # Capture any text in this turn (Iterate through all parts to ensure capture)
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
                # Prepend New York timestamp to tool output messages
                ny_time = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-4)))
                current_iso = ny_time.strftime("%Y-%m-%dT%H:%M:%S")
                current_message = [f"[SYSTEM_TIME (NEW YORK / ET): {current_iso}]"] + tool_responses
                continue # Next turn
            
            break # No more tools, we are done
        
        if not all_text:
            framework.log("[Orchestrator] WARNING: No text captured across all turns.")
            return {"status": "success", "response": "_[The model performed its internal reasoning and tools but did not emit a final text summary. Please try again or check system logs.]_"}

        full_response = "\n\n---\n\n".join(all_text)
        return {"status": "success", "response": full_response.strip()}
    except Exception as e:
        # Auto-fallback logic is now handled inside AgentFramework tiers
        return {"status": "error", "message": str(e)}

@app.get("/api/tickers")
@app.get("/api/get_settings")
def get_settings():
    if os.path.exists("user_config.json"):
        with open("user_config.json", "r") as f:
            return json.load(f)
    return {"tickers": [], "macro": []}

class SettingsRequest(BaseModel):
    tickers: list
    macro: list

@app.post("/api/save_settings")
def save_settings(req: SettingsRequest):
    with open("user_config.json", "w") as f:
        json.dump(req.dict(), f, indent=2)
    framework.log("[System] User settings updated.")
    return {"status": "success"}

@app.get("/api/get_basket")
def get_basket():
    try:
        if os.path.exists("ssot.json"):
            with open("ssot.json", "r") as f:
                content = f.read()
                if not content.strip(): return []
                data = json.loads(content)
                # Map SSoT fields to UI fields
                raw_basket = data.get("portfolio_snapshot", [])
                return [{"ticker": i["ticker"], "shares": i.get("shares", 0), "wac": i.get("wac", 0)} for i in raw_basket]
    except Exception as e:
        print(f"SSoT Read Warning: {e}")
    return []

@app.post("/api/save_basket")
def save_basket(basket: List[BasketItem]):
    try:
        if os.path.exists("ssot.json"):
            with open("ssot.json", "r") as f:
                data = json.load(f)
            
            # Update the specific snapshot while preserving other SSoT keys
            new_snapshot = []
            for item in basket:
                new_snapshot.append({
                    "ticker": item.ticker,
                    "shares": item.shares,
                    "wac": item.wac
                })
            data["portfolio_snapshot"] = new_snapshot
            
            with open("ssot.json", "w") as f:
                json.dump(data, f, indent=2)
            framework.log("[System] SSoT Basket synced manually.")
    except Exception as e:
        framework.log(f"[Error] Basket Sync Failed: {e}")
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
    try:
        uvicorn.run(app, host="0.0.0.0", port=port)
    except Exception as e:
        print(f"CRITICAL: Web Server exited with error: {e}")
