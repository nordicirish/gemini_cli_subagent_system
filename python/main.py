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
        
    defaults = {
        "context/ssot.json": "{}",
        "context/trade_lessons.json": "[]",
        "context/decision_log.json": "[]",
        "context/user_config.json": "{}",
        "context/config.json": '{\n  "GEMINI_API_KEY": "",\n  "GEMINI_FREE_TIER_API_KEY": "",\n  "FINNHUB_API_KEY": ""\n}'
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

    framework.setup_context_cache(
        subagent_files=subagent_files,
        system_instruction=terminal_instruction,
        tools=all_tools
    )

    rules_path = os.path.join("gem_trading_rules", "rules.md")
    if not getattr(framework, "cached_content_name", None):
        # Attach canonical rules knowledge base
        if os.path.exists(rules_path):
            with open(rules_path, "r", encoding="utf-8") as f:
                rules_content = f.read()
            terminal_instruction += f"\n\n--- ATTACHED KNOWLEDGE BASE (GEM_Rules_Data) ---\n{rules_content}"

    # ---------------------------------------------------------------------------
    # Startup banner
    # ---------------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("💎 GEM CLI ORCHESTRATOR READY 💎")
    print(f"   Version : v10.03-ESA-Deadlock-Sync")
    print(f"   Agents  : {len(sub_agent_configs)} loaded")
    print(f"   Rules   : {'✅ Attached' if os.path.exists(rules_path) else '⚠️  Missing'}")
    antigravity_path = os.path.join(".agents", "rules", "antigravity.md")
    print(f"   Antigravity: {'✅ Active' if os.path.exists(antigravity_path) else '⚠️  Missing'}")
    print("=" * 60)
    print("Type 'exit' or 'quit' to stop.\n")

    # ---------------------------------------------------------------------------
    # Model probe — find first working PRO model for the Terminal Orchestrator
    # ---------------------------------------------------------------------------
    terminal_models = framework._get_cloud_models("PRO")
    valid_model = None

    for model_name in terminal_models:
        import datetime
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] Testing Terminal model {model_name}...", end="", flush=True)
        try:
            framework.client.models.generate_content(
                model=model_name,
                contents="ping",
                config=agent_framework.types.GenerateContentConfig(
                    http_options={"timeout": 10000}
                ),
            )
            valid_model = model_name
            print(" SUCCESS!")
            break
        except Exception as e:
            error_str = str(e).lower()
            if "429" in error_str or "quota" in error_str:
                valid_model = model_name
                print(" QUOTA LIMITED (but verified)")
                break
            print(" FAILED")
            continue

    if not valid_model:
        print("ERROR: All fallback models failed to verify. Exiting.")
        sys.exit(1)

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

    cache_to_use = None
    if getattr(framework, "cached_content_name", None):
        cache_to_use = framework.cached_content_name
        print(f"[System] Binding Context Cache to CLI chat session: {cache_to_use}")

    chat = framework.client.chats.create(
        model=valid_model,
        config=agent_framework.types.GenerateContentConfig(
            system_instruction=terminal_instruction if not cache_to_use else None,
            temperature=1.0,
            tools=terminal_tools if not cache_to_use else None,
            cached_content=cache_to_use,
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

            print("\n[Terminal Orchestrator] Thinking...")
            active_model_str = f"[ACTIVE_MODEL]: {valid_model}\n"
            current_message = f"{active_model_str}[USER_QUERY]: {user_input}"
            response = chat.send_message(current_message)
            print("\n[Terminal Orchestrator] Response:")
            print(response.text)

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
