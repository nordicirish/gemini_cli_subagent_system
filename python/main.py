import os
import sys
from agent_framework import AgentFramework
import agent_framework
import tools
import threading
import cloud_sync

def initialize_context_files():
    """Bootstraps missing context files for fresh repository clones."""
    if not os.path.exists("context"):
        os.makedirs("context")
    if not os.path.exists("logs"):
        os.makedirs("logs")
        
    defaults = {
        "context/ssot.json": "{}",
        "context/trade_lessons.json": "[]",
        "context/decision_log.json": "[]",
        "context/user_config.json": "{}",
        "context/config.json": '{\n  "GEMINI_API_KEY": "",\n  "GEMINI_FREE_TIER_API_KEY": "",\n  "FINNHUB_API_KEY": ""\n}',
        "logs/gem_handshakes.log": ""
    }
    
    for filepath, default_content in defaults.items():
        if not os.path.exists(filepath):
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(default_content)
                print(f"[System] Initialized missing file: {filepath}")
            except Exception as e:
                print(f"[Warning] Could not initialize {filepath}: {e}")

def main():
    print("Initializing GEM Trading CLI Subagent System...")
    initialize_context_files()

    # Initialize the Framework
    framework = AgentFramework()

    # ---------------------------------------------------------------------------
    # Sub-agent registry — v10.03-ESA-Deadlock-Sync
    # Mode tiers per terminal.md > Mode Selection Matrix (Canonical)
    # ---------------------------------------------------------------------------
    sub_agent_configs = {
        # --- Tier 1: PRO chain ---
        "Macro Sentinel":          {"file": "engine_instructions/macro_sentinel.md",          "mode": "PRO"},
        "Data Analyst":            {"file": "engine_instructions/data_analyst.md",            "mode": "PRO"},
        "State Validation Router": {"file": "engine_instructions/state_validation_router.md", "mode": "PRO"},

        # --- Tier 2: THINKING chain (reasoning-heavy advocates & research) ---
        "Research Engine":         {"file": "engine_instructions/research.md",                "mode": "THINKING"},
        "Macro Narrative Engine":  {"file": "engine_instructions/macro_narrative_engine.md",  "mode": "THINKING"},
        "Bullish Advocate":        {"file": "engine_instructions/bullish_gem.md",             "mode": "THINKING"},
        "Red Team Pessimist":      {"file": "engine_instructions/red_team_gem.md",            "mode": "THINKING"},

        # --- Tier 3: FAST chain ---
        "Review Engine":           {"file": "engine_instructions/post_trade_review.md",       "mode": "FAST"},

        # --- Tier 4: GEMMA chain (local / cost-efficient engines) ---
        "Neutral Structuralist":   {"file": "engine_instructions/neutral_gem.md",             "mode": "GEMMA"},
        "Sentiment Engine":        {"file": "engine_instructions/sentiment_engine.md",        "mode": "GEMMA"},
        "Structural Engine":       {"file": "engine_instructions/structural_engine.md",       "mode": "GEMMA"},
        "Rule Enforcer Engine":    {"file": "engine_instructions/rule_enforcer_engine.md",    "mode": "GEMMA"},
        "Context Engine":          {"file": "engine_instructions/context_engine.md",          "mode": "GEMMA"},
        "Execution Engine":        {"file": "engine_instructions/execution.md",               "mode": "GEMMA"},
        "Technical Validator":     {"file": "engine_instructions/technical_validator.md",     "mode": "GEMMA"},
        "GEX Engine":              {"file": "engine_instructions/gex_engine.md",              "mode": "GEMMA"},
    }

    # --- CONTEXT CACHING INITIALIZATION (ENH_CACHE_01) ---
    # Must be called AFTER sub_agent_configs is defined (bug-fix vs. prior version)
    subagent_files = [c["file"] for c in sub_agent_configs.values()] + ["engine_instructions/terminal.md"]

    # ---------------------------------------------------------------------------
    # Build tool list — start with core data tools
    # ---------------------------------------------------------------------------
    terminal_tools = [
        tools.read_ssot,
        tools.update_ssot,
        tools.read_trade_lessons,
        tools.get_market_data,
    ]

    print("Loading Sub-Agents...")
    agents_dict = {}

    for name, config in sub_agent_configs.items():
        if os.path.exists(config["file"]):

            # Per-agent toolsets — Tool Supremacy Hierarchy (ENH_31 / ENH_55)
            if name == "Data Analyst":
                sub_tools = [tools.perform_web_forensic_search, tools.get_market_data]
            elif name == "Macro Narrative Engine":
                sub_tools = [tools.perform_web_forensic_search, tools.read_ssot, tools.get_market_data]
            elif name == "State Validation Router":
                sub_tools = [tools.read_ssot, tools.update_ssot, tools.get_market_data]
            elif name == "Structural Engine":
                sub_tools = [tools.read_ssot, tools.get_market_data, tools.perform_web_forensic_search]
            elif name == "Context Engine":
                sub_tools = [
                    tools.read_ssot, tools.update_ssot,
                    tools.read_trade_lessons, tools.update_trade_lessons,
                    tools.get_market_data, tools.perform_web_forensic_search,
                    tools.update_rules,
                ]
            elif name == "Technical Validator":
                sub_tools = [tools.read_ssot, tools.read_trade_lessons, tools.get_market_data, tools.perform_web_forensic_search]
            elif name in [
                "Research Engine", "Sentiment Engine",
                "Bullish Advocate", "Red Team Pessimist",
                "Macro Sentinel", "Neutral Structuralist",
                "Macro Narrative Engine",
            ]:
                sub_tools = [tools.perform_web_forensic_search]
            else:
                sub_tools = [tools.read_ssot, tools.read_trade_lessons, tools.get_market_data]

            tool_func = framework.create_agent_tool(
                name, config["file"], config["mode"], agent_tools=sub_tools
            )
            terminal_tools.append(tool_func)
            agents_dict[f"ask_{name.lower().replace(' ', '_')}"] = tool_func
        else:
            print(f"Warning: {config['file']} not found. Skipping {name} sub-agent.")

    # Register the parallel council dispatcher (ask_council)
    ask_council = framework.create_parallel_council_tool(agents_dict)
    terminal_tools.append(ask_council)

    # ---------------------------------------------------------------------------
    # Load Terminal Orchestrator instruction
    # ---------------------------------------------------------------------------
    print("Loading Terminal Orchestrator...")
    if not os.path.exists("engine_instructions/terminal.md"):
        print("Error: terminal.md not found!")
        sys.exit(1)

    terminal_instruction = framework.load_system_instruction("engine_instructions/terminal.md")

    all_tools = terminal_tools + [tools.perform_web_forensic_search]





    # ---------------------------------------------------------------------------
    # Startup banner
    # ---------------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("💎 GEM CLI ORCHESTRATOR READY 💎")
    print(f"   Version : v11.23-UI-Feedback-Cost-Fix")
    print(f"   Agents  : {len(sub_agent_configs)} loaded")
    print(f"   Rules   : {'✅ Attached' if os.path.exists(rules_path) else '⚠️  Missing'}")
    antigravity_path = os.path.join(".agents", "rules", "antigravity.md")
    print(f"   Antigravity: {'✅ Active' if os.path.exists(antigravity_path) else '⚠️  Missing'}")
    print("=" * 60)
    print("Type 'exit' or 'quit' to stop.\n")

    # ---------------------------------------------------------------------------
    # Resolve Orchestrator dynamically using _resolve_orchestrator
    # ---------------------------------------------------------------------------
    orchestrator_model = framework._resolve_orchestrator()

    try:
        import fetch_stocks
        fetch_stocks.compile_master_document()
        sync_daemon = cloud_sync.CloudSyncDaemon(framework.client)
        sync_files = [
            "scratch/master_trading_knowledge.md",
            "context/decision_log.json",
            "context/trade_lessons.json",
            "context/ssot.json"
        ]
        sync_thread = threading.Thread(target=sync_daemon.start_background_sync, args=(sync_files,), daemon=True)
        sync_thread.start()
    except Exception as e:
        print(f"Failed to start Cloud Sync Daemon: {e}")

    sys_instruction = framework._get_sys_instruction(terminal_instruction)

    chat = framework.client.chats.create(
        model=orchestrator_model,
        config=agent_framework.types.GenerateContentConfig(
            system_instruction=sys_instruction,
            temperature=1.0,
            tools=terminal_tools,
            automatic_function_calling={"disable": True},
            safety_settings=[
                agent_framework.types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH",       threshold="BLOCK_NONE"),
                agent_framework.types.SafetySetting(category="HARM_CATEGORY_HARASSMENT",         threshold="BLOCK_NONE"),
                agent_framework.types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT",  threshold="BLOCK_NONE"),
                agent_framework.types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT",  threshold="BLOCK_NONE"),
            ]
        ),
    )

    while True:
        try:
            user_input = input("\n[USER] > ")
            if user_input.lower() in ["exit", "quit"]:
                break
            if not user_input.strip():
                continue

            framework.reset_turn_usage()
            print("\n[Terminal Orchestrator] Thinking...")
            active_model_str = f"[ACTIVE_MODEL]: {orchestrator_model}\n"
            current_message = f"{active_model_str}[USER_QUERY]: {user_input}"
            try:
                response = chat.send_message(current_message)
            except Exception as exc:
                from google.genai.errors import APIError
                if isinstance(exc, APIError):
                    framework.log(f"[Emergency Failover] APIError encountered on primary orchestrator ({exc}). Redirecting request to gemini-3.5-flash...")
                    orchestrator_model = "gemini-3.5-flash"
                    sys_instruction = framework._get_sys_instruction(terminal_instruction)
                    chat = framework.client.chats.create(
                        model=orchestrator_model,
                        config=agent_framework.types.GenerateContentConfig(
                            system_instruction=sys_instruction,
                            temperature=1.0,
                            tools=terminal_tools,
                            automatic_function_calling={"disable": True},
                            safety_settings=[
                                agent_framework.types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH",       threshold="BLOCK_NONE"),
                                agent_framework.types.SafetySetting(category="HARM_CATEGORY_HARASSMENT",         threshold="BLOCK_NONE"),
                                agent_framework.types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT",  threshold="BLOCK_NONE"),
                                agent_framework.types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT",  threshold="BLOCK_NONE"),
                            ]
                        ),
                    )
                    active_model_str = f"[ACTIVE_MODEL]: {orchestrator_model}\n"
                    current_message = f"{active_model_str}[USER_QUERY]: {user_input}"
                    response = chat.send_message(current_message)
                else:
                    raise exc

            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                raw_prompt_tokens = response.usage_metadata.prompt_token_count or 0
                cached_tokens = getattr(response.usage_metadata, 'cached_content_token_count', 0) or 0
                p_tokens = raw_prompt_tokens - cached_tokens
                c_tokens = response.usage_metadata.candidates_token_count or 0
                
                framework.turn_usage['prompt_tokens'] += p_tokens
                framework.turn_usage['candidates_tokens'] += c_tokens
                framework.turn_usage['cached_tokens'] += cached_tokens
                
                call_cost = framework._calculate_call_cost(orchestrator_model, "Primary Key", p_tokens, c_tokens, cached_tokens)
                framework.turn_usage['estimated_cost'] += call_cost
                framework.session_cost += call_cost

            print("\n[Terminal Orchestrator] Response:")
            print(response.text)

            turn_cost = framework.turn_usage['estimated_cost']
            session_cost = framework.session_cost
            print(f"[Diagnostics] Turn Cost: ${turn_cost:.4f} | Total Session Cost: ${session_cost:.4f}")

        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except Exception as e:
            print(f"\n[Error] {str(e)}")


if __name__ == "__main__":
    if not os.environ.get("GEMINI_API_KEY") and not os.environ.get("GOOGLE_API_KEY"):
        print("ERROR: GEMINI_API_KEY or GOOGLE_API_KEY environment variable is not set.")
        print("Please set it before running this CLI.")
        sys.exit(1)

    main()
