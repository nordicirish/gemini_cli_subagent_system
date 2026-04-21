import os
import sys
import uvicorn
import asyncio
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse

from agent_framework import AgentFramework
import agent_framework
import tools

# Import the existing FastAPI app from fetch_stocks
from fetch_stocks import app, run_daemon

# --- LOGGING SYSTEM ---
# This queue will hold system messages to be streamed to the UI via SSE
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
            queue = get_log_queue()
            main_loop.call_soon_threadsafe(queue.put_nowait, message)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
                main_loop.call_soon_threadsafe(queue.put_nowait, message)
            except:
                pass
        except Exception:
            pass
    else:
        # Before the UI is connected or loop is captured, just print
        print(f"[System] {message}")


# --- ORCHESTRATOR INITIALIZATION ---
print("Initializing GEM Trading Web UI Server...")

# Initialize framework with the queue callback
framework = AgentFramework(log_callback=log_to_queue)

sub_agent_configs = {
    # --- Gemini cloud agents (deep reasoning / web search required) ---
    "Macro Sentinel":        {"file": "macro_arbiter.json",        "mode": "PRO"},
    "Bullish Advocate":      {"file": "bullish_gem.json",          "mode": "THINKING"},
    "Red Team Pessimist":    {"file": "red_team_gem.json",         "mode": "THINKING"},
    "Neutral Structuralist": {"file": "neutral_gem.json",          "mode": "PRO"},
    "Research Engine":       {"file": "research.json",             "mode": "THINKING"},
    "Sentiment Engine":      {"file": "sentiment_engine.json",     "mode": "FAST"},
    "Review Engine":         {"file": "post_trade_review.json",    "mode": "FAST"},
    # --- Local Gemma agents (gemma4:e2b — fast, deterministic) ---
    "Structural Engine":     {"file": "structural_engine.json",    "mode": "LOCAL_1B"},
    "Rule Enforcer Engine":  {"file": "rule_enforcer_engine.json", "mode": "LOCAL_1B"},
    # --- Local Gemma agents (gemma4:e4b — analytical, structured data) ---
    "Context Engine":        {"file": "context_engine.json",       "mode": "LOCAL_4B"},
    "Execution Engine":      {"file": "execution.json",            "mode": "LOCAL_4B"},
    "Technical Validator":   {"file": "technical_validator.json",  "mode": "LOCAL_4B"},
    "GEX Engine":            {"file": "gex_engine.json",           "mode": "LOCAL_4B"},
}

terminal_tools = [
    tools.read_ssot,
    tools.update_ssot,
    tools.read_trade_lessons,
    tools.get_market_data
]

for name, config in sub_agent_configs.items():
    if os.path.exists(config["file"]):
        if name in ["Research Engine", "Sentiment Engine"]:
            # These engines focus on Web Research ONLY (No custom tools allowed by Gemini)
            sub_tools = [{"google_search": {}}]
        elif name == "Context Engine":
            # The SSoT owner gets custom tools ONLY (No google_search)
            sub_tools = [tools.read_ssot, tools.update_ssot, tools.read_trade_lessons, tools.update_trade_lessons, tools.get_market_data]
        else:
            # All other engines get custom Read-Only tools
            sub_tools = [tools.read_ssot, tools.read_trade_lessons, tools.get_market_data]
            
        tool_func = framework.create_agent_tool(name, config["file"], config["mode"], agent_tools=sub_tools)
        terminal_tools.append(tool_func)

# Create a parallel dispatcher tool that can call other tools
agents_map = {f. __name__: f for f in terminal_tools}
parallel_tool = framework.create_parallel_council_tool(agents_map)
terminal_tools.append(parallel_tool)

if not os.path.exists("terminal.json"):
    print("Error: terminal.json not found!")
    sys.exit(1)

terminal_instruction = framework.load_system_instruction("terminal.json")

rules_path = os.path.join("GEM_Trading_Rules", "rules.json")
if os.path.exists(rules_path):
    with open(rules_path, "r", encoding="utf-8") as f:
        rules_content = f.read()
    terminal_instruction += f"\n\n--- ATTACHED KNOWLEDGE BASE (GEM_Rules_Data) ---\n{rules_content}"

# Ping to find a working model
# Use THINKING models for the Orchestrator to ensure deep reasoning and council debate
terminal_models = agent_framework.MODEL_MAPPING["THINKING"]
valid_model = None
for model_name in terminal_models:
    framework.log(f"Testing Terminal model {model_name}...")
    try:
        framework.client.models.generate_content(
            model=model_name,
            contents="ping"
        )
        valid_model = model_name
        framework.log(f"Successfully verified {model_name}!")
        break
    except Exception as e:
        error_str = str(e).lower()
        if "429" in error_str or "quota" in error_str:
            framework.log(f"Model {model_name} is rate limited (429), but it exists! Setting as valid model.")
            valid_model = model_name
            break
        framework.log(f"Failed with {model_name}: {e}")

if not valid_model:
    print("ERROR: All fallback models failed to verify. Exiting.")
    sys.exit(1)

# Initialize the global Chat object
global_chat = framework.client.chats.create(
    model=valid_model,
    config=agent_framework.types.GenerateContentConfig(
        system_instruction=terminal_instruction,
        temperature=1.0,
        tools=terminal_tools
    )
)

# Start background warmup for local models
import threading
threading.Thread(target=framework.warmup_local_models, daemon=True).start()

print("\n" + "="*60)
print("--- GEM WEB UI ORCHESTRATOR READY ---")
print("="*60)

# --- FASTAPI ENDPOINTS ---

class ChatRequest(BaseModel):
    message: str

@app.post("/api/chat")
def chat_endpoint(req: ChatRequest):
    try:
        if not req.message.strip():
            return {"status": "error", "message": "Empty message."}
            
        # Collect history length before sending
        history_len_before = len(global_chat.get_history())
        response = global_chat.send_message(req.message)
        
        # Aggregate all model responses from this interaction
        all_new_messages = global_chat.get_history()[history_len_before:]
        full_response = ""
        for msg in all_new_messages:
            if msg.role == 'model':
                for part in msg.parts:
                    if part.text:
                        full_response += part.text + "\n\n"
        
        if not full_response.strip():
            full_response = response.text or "No textual response received."
            
        return {"status": "success", "response": full_response.strip()}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/system_logs")
async def system_logs_endpoint():
    """SSE endpoint to stream system logs to the frontend."""
    global main_loop
    main_loop = asyncio.get_running_loop()
    
    queue = get_log_queue()
    
    async def event_generator():
        while True:
            # Wait for a new log message
            log_msg = await queue.get()
            # Format as SSE
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
                print(f"Port {port} is in use, trying {port + 1}...")
                port += 1

    print(f"Starting Web Server on http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
