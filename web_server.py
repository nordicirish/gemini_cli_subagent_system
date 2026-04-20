import os
import sys
import uvicorn
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles

from agent_framework import AgentFramework
import agent_framework
import tools

# Import the existing FastAPI app from fetch_stocks
from fetch_stocks import app, run_daemon

# --- ORCHESTRATOR INITIALIZATION ---
print("Initializing GEM Trading Web UI Server...")

framework = AgentFramework()

sub_agent_configs = {
    "Macro Sentinel": {"file": "macro_arbiter.json", "mode": "PRO"},
    "Bullish Advocate": {"file": "bullish_gem.json", "mode": "THINKING"},
    "Red Team Pessimist": {"file": "red_team_gem.json", "mode": "THINKING"},
    "Neutral Structuralist": {"file": "neutral_gem.json", "mode": "PRO"},
    "Execution Engine": {"file": "execution.json", "mode": "PRO"},
    "Structural Engine": {"file": "structural_engine.json", "mode": "FAST"},
    "Technical Validator": {"file": "technical_validator.json", "mode": "PRO"},
    "Research Engine": {"file": "research.json", "mode": "THINKING"},
    "Sentiment Engine": {"file": "sentiment_engine.json", "mode": "PRO"},
    "Context Engine": {"file": "context_engine.json", "mode": "PRO"},
    "GEX Engine": {"file": "gex_engine.json", "mode": "PRO"},
    "Review Engine": {"file": "post_trade_review.json", "mode": "PRO"},
    "Rule Enforcer Engine": {"file": "rule_enforcer_engine.json", "mode": "PRO"}
}

terminal_tools = [
    tools.read_ssot,
    tools.update_ssot,
    tools.read_trade_lessons,
    tools.get_market_data
]

for name, config in sub_agent_configs.items():
    if os.path.exists(config["file"]):
        if name in ["Research Engine", "Sentiment Engine", "Context Engine"]:
            sub_tools = [{"google_search": {}}]
        else:
            sub_tools = [tools.read_ssot, tools.read_trade_lessons, tools.get_market_data]
            
        tool_func = framework.create_agent_tool(name, config["file"], config["mode"], agent_tools=sub_tools)
        terminal_tools.append(tool_func)

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
terminal_models = agent_framework.MODEL_MAPPING["PRO"]
valid_model = None
for model_name in terminal_models:
    print(f"Testing Terminal model {model_name}...")
    try:
        framework.client.models.generate_content(
            model=model_name,
            contents="ping"
        )
        valid_model = model_name
        print(f"Successfully verified {model_name}!")
        break
    except Exception as e:
        error_str = str(e).lower()
        if "429" in error_str or "quota" in error_str:
            print(f"Model {model_name} is rate limited (429), but it exists! Setting as valid model.")
            valid_model = model_name
            break
        print(f"Failed with {model_name}: {e}")

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

print("\n" + "="*60)
print("💎 GEM WEB UI ORCHESTRATOR READY 💎")
print("="*60)

# --- FASTAPI CHAT ENDPOINT ---

class ChatRequest(BaseModel):
    message: str

@app.post("/api/chat")
def chat_endpoint(req: ChatRequest):
    try:
        if not req.message.strip():
            return {"status": "error", "message": "Empty message."}
            
        # Running as a standard def offloads this to FastAPI's threadpool.
        # This prevents blocking the main event loop and allows the tools to use asyncio.run()
        response = global_chat.send_message(req.message)
        return {"status": "success", "response": response.text}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# --- MOUNT STATIC AND RUN ---
if __name__ == "__main__":
    if not os.path.exists("static"):
        os.makedirs("static")
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
    
    # Note: Daemon thread is already started in tools.py on import.
    
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
