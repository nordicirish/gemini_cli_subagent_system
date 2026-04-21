import os
import json
import asyncio
from google import genai
from google.genai import types
from agent_framework import AgentFramework
import tools

# Mock the log callback
def log_callback(msg):
    print(msg)

# Initialize framework
framework = AgentFramework(log_callback=log_callback)

# Setup Context Engine tool
context_engine_file = "context_engine.json"
context_tools = [tools.read_ssot, tools.update_ssot, tools.read_trade_lessons, tools.get_market_data]
ask_context_engine = framework.create_agent_tool("Context Engine", context_engine_file, mode="PRO", agent_tools=context_tools)

# Setup Terminal with Context Engine tool
terminal_instruction = framework.load_system_instruction("terminal.json")
terminal_tools = [tools.read_ssot, ask_context_engine]

# Create session using native Gemini SDK (matches web_server.py)
chat = framework.client.chats.create(
    model="gemini-2.5-pro",
    config=types.GenerateContentConfig(
        system_instruction=terminal_instruction,
        temperature=1.0,
        tools=terminal_tools
    )
)

# Test prompt: Force a state update through the context engine
test_prompt = "UPDATE_TEST: UMAC $5M order confirmed. Commit the flag 'UMAC_$5M_ORDER_SUCCESS_VERIFIED' to the forensic_intelligence active_flags in the SSoT via the Context Engine. Do not delegate to others, just do the commit flow."

print("\n--- STARTING STATE SYNC TEST ---")
response = chat.send_message(test_prompt)
print("\n--- TEST RESPONSE ---")
print(response.text)

# Verify local_ssot_shadow.json was updated
if os.path.exists('local_ssot_shadow.json'):
    with open('local_ssot_shadow.json', 'r') as f:
        ssot = json.load(f)
        flags = ssot.get("mutable_state", {}).get("forensic_intelligence", {}).get("active_flags", [])
        print("\n--- VERIFICATION ---")
        print(f"Active Flags: {flags}")
        if any("UMAC_$5M_ORDER_SUCCESS_VERIFIED" in f.upper() for f in flags):
            print("SUCCESS: SSoT was updated via the Context Engine.")
        else:
            print("FAILURE: SSoT does not contain the expected flag.")
