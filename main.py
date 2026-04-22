import os
import sys
from agent_framework import AgentFramework
import agent_framework
import tools

def main():
    print("Initializing GEM Trading CLI Subagent System...")
    
    # Initialize the Framework
    framework = AgentFramework()
    
    # Map of all our sub-agents (engines and advocates)
    # LOCAL_1B  → Ollama gemma4:e2b  (fast deterministic agents)
    # LOCAL_4B  → Ollama gemma4:e4b  (analytical agents)
    # PRO/THINKING/FAST → Gemini cloud (reasoning, web search, orchestration)
    sub_agent_configs = {
        # --- Tier 1 (PRO Chain: 3.1 Pro -> 2.5 Pro -> Gemma 4 -> Flash) ---
        "Macro Sentinel":        {"file": "macro_arbiter.json",        "mode": "PRO"},
        "Review Engine":         {"file": "post_trade_review.json",    "mode": "PRO"},
        
        # --- Tier 2 (GEMMA Chain: Gemma 4 -> Flash) ---
        "Bullish Advocate":      {"file": "bullish_gem.json",          "mode": "GEMMA"},
        "Red Team Pessimist":    {"file": "red_team_gem.json",         "mode": "GEMMA"},
        "Neutral Structuralist": {"file": "neutral_gem.json",          "mode": "GEMMA"},
        "Sentiment Engine":      {"file": "sentiment_engine.json",     "mode": "GEMMA"},
        "Structural Engine":     {"file": "structural_engine.json",    "mode": "GEMMA"},
        "Rule Enforcer Engine":  {"file": "rule_enforcer_engine.json", "mode": "GEMMA"},
        "Context Engine":        {"file": "context_engine.json",       "mode": "GEMMA"},
        "Execution Engine":      {"file": "execution.json",            "mode": "GEMMA"},
        "Technical Validator":   {"file": "technical_validator.json",  "mode": "GEMMA"},
        "GEX Engine":            {"file": "gex_engine.json",           "mode": "GEMMA"},
        
        # --- Tier 3 (FAST Chain: Flash -> Gemma 4) ---
        "Research Engine":       {"file": "research.json",             "mode": "FAST"},
    }
    
    # Create the tools list
    terminal_tools = [
        tools.read_ssot,
        tools.update_ssot,
        tools.read_trade_lessons,
        tools.get_market_data
    ]
    
    print("Loading Sub-Agents...")
    for name, config in sub_agent_configs.items():
        if os.path.exists(config["file"]):
            if name in ["Research Engine", "Sentiment Engine", "Context Engine"]:
                sub_tools = [{"google_search": {}}]
            else:
                sub_tools = [tools.read_ssot, tools.read_trade_lessons, tools.get_market_data]
                
            tool_func = framework.create_agent_tool(name, config["file"], config["mode"], agent_tools=sub_tools)
            terminal_tools.append(tool_func)
        else:
            print(f"Warning: {config['file']} not found. Skipping {name} sub-agent.")
            
    print("Loading Terminal Orchestrator...")
    if not os.path.exists("terminal.json"):
        print("Error: terminal.json not found!")
        sys.exit(1)
        
    terminal_instruction = framework.load_system_instruction("terminal.json")
    
    # Optional: Attach Rules.json if it exists to the terminal instruction
    rules_path = os.path.join("GEM_Trading_Rules", "rules.json")
    if os.path.exists(rules_path):
        with open(rules_path, "r", encoding="utf-8") as f:
            rules_content = f.read()
        terminal_instruction += f"\n\n--- ATTACHED KNOWLEDGE BASE (GEM_Rules_Data) ---\n{rules_content}"
        
    print("\n" + "="*60)
    print("💎 GEM CLI ORCHESTRATOR READY 💎")
    print("="*60)
    print("Type 'exit' or 'quit' to stop.\n")
    
    # Start the conversation loop
    # We maintain a manual chat history to pass to generate_content, 
    # We use the fallback logic to find a valid model for the Orchestrator
    terminal_models = framework._get_cloud_models("PRO")
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
        
    chat = framework.client.chats.create(
        model=valid_model,
        config=agent_framework.types.GenerateContentConfig(
            system_instruction=terminal_instruction,
            temperature=1.0,
            tools=terminal_tools
        )
    )
    
    while True:
        try:
            user_input = input("\n[USER] > ")
            if user_input.lower() in ['exit', 'quit']:
                break
            if not user_input.strip():
                continue
                
            print("\n[Terminal Orchestrator] Thinking...")
            
            # Send the message
            # The google-genai chat handles automatic tool calling if tools are provided
            response = chat.send_message(user_input)
            
            print("\n[Terminal Orchestrator] Response:")
            print(response.text)
            
        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except Exception as e:
            print(f"\n[Error] {str(e)}")

if __name__ == "__main__":
    # Ensure GOOGLE_API_KEY or GEMINI_API_KEY is present
    if not os.environ.get("GEMINI_API_KEY") and not os.environ.get("GOOGLE_API_KEY"):
        print("ERROR: GEMINI_API_KEY or GOOGLE_API_KEY environment variable is not set.")
        print("Please set it before running this CLI.")
        sys.exit(1)
        
    main()

