import os
import sys
import uvicorn
import asyncio
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
import threading

from agent_framework import AgentFramework
import agent_framework
import tools

# Import the existing FastAPI app from fetch_stocks
from fetch_stocks import app, run_daemon, GLOBAL_STATE

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

# Register local engine sub-tools (Read-Only by default)
for name, agent in framework.agents.items():
    if name == "Research Engine":
        sub_tools = [{"google_search": {}}]
    elif name == "Context Engine":
        sub_tools = [tools.read_ssot, tools.update_ssot, tools.read_trade_lessons, tools.update_trade_lessons, tools.get_market_data]
    else:
        sub_tools = [tools.read_ssot, tools.read_trade_lessons, tools.get_market_data]
    agent.tools = sub_tools

def ask_subagent(name: str, query: str):
    """Bridge function for the Orchestrator to call other engines."""
    return framework.generate_response_with_fallback(query, "", name, tools=None)

# Parallel Council Dispatcher
def ask_council(queries_json: str):
    """Run multiple council engines in parallel. Input: JSON array of {agent: name, query: text}"""
    import json
    try:
        reqs = json.loads(queries_json)
        results = framework.create_parallel_council_tool(reqs)
        return json.dumps(results, indent=2)
    except Exception as e:
        return f"Council Error: {str(e)}"

# Define available tools for the Terminal Orchestrator
terminal_tools = [
    tools.read_ssot,
    tools.update_ssot,
    tools.read_trade_lessons,
    tools.update_trade_lessons,
    tools.get_market_data,
    ask_subagent,
    ask_council
]

# Load Orchestrator context
terminal_instruction = framework.load_system_instruction("terminal.json")
rules_path = os.path.join("GEM_Trading_Rules", "rules.json")
if os.path.exists(rules_path):
    with open(rules_path, "r", encoding="utf-8") as f:
        rules_content = f.read()
    terminal_instruction += f"\n\n--- ATTACHED KNOWLEDGE BASE (GEM_Rules_Data) ---\n{rules_content}"

# FORCE LOCAL MODE DUE TO QUOTA EXHAUSTION
framework.log("SYSTEM: Forcing LOCAL-ONLY mode due to detected Cloud Quota Exhaustion.")
GLOBAL_STATE["system_status"] = {
    "mode": "LOCAL_ONLY",
    "warning": "Cloud Quota Exhausted. Using Local Gemma 4 Brain."
}
valid_model = "gemini-1.5-flash" # Placeholder for API initialization

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
threading.Thread(target=framework.warmup_local_models, daemon=True).start()

print("\n" + "="*60)
print("--- GEM WEB UI ORCHESTRATOR READY (LOCAL MODE) ---")
print("="*60)

# --- FASTAPI ENDPOINTS ---

class ChatRequest(BaseModel):
    message: str

@app.post("/api/chat")
def chat_endpoint(req: ChatRequest):
    """Primary chat route. Uses local fallback if cloud is exhausted."""
    try:
        # Check if we are in Local Only mode
        is_local = GLOBAL_STATE.get("system_status", {}).get("mode") == "LOCAL_ONLY"
        
        if is_local:
            # Use Gemma 4 directly as the Orchestrator
            framework.log("[Local] Orchestrator running on Gemma 4 e4b...")
            full_response = framework.generate_response_with_fallback(
                req.message, 
                terminal_instruction, 
                "LOCAL_4B", 
                tools=terminal_tools
            )
        else:
            # Standard Cloud path
            response = global_chat.send_message(req.message)
            full_response = ""
            if hasattr(response, 'candidates') and response.candidates:
                parts = []
                for candidate in response.candidates:
                    for part in candidate.content.parts:
                        if hasattr(part, 'text') and part.text:
                            parts.append(part.text)
                full_response = "\n\n".join(parts)
            else:
                full_response = response.text or "No textual response received."
            
        return {"status": "success", "response": full_response.strip()}
    except Exception as e:
        # If cloud fails mid-session, try to flip to local automatically
        if not is_local and ("429" in str(e) or "quota" in str(e).lower()):
            GLOBAL_STATE["system_status"] = {
                "mode": "LOCAL_ONLY",
                "warning": "Cloud Quota Exhausted Mid-Session. Switching to Local Gemma 4."
            }
            return chat_endpoint(req) # Retry as local
        return {"status": "error", "message": str(e)}

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
