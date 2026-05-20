import os
import sys
from agent_framework import AgentFramework
import agent_framework
import tools


def main():
    print("Initializing GEM Trading CLI Subagent System...")

    # Initialize the Framework
    framework = AgentFramework()

    # ---------------------------------------------------------------------------
    # Sub-agent registry — v10.03-ESA-Deadlock-Sync
    # Mode tiers per terminal.md > Mode Selection Matrix (Canonical)
    # ---------------------------------------------------------------------------
    sub_agent_configs = {
        # --- Tier 1: PRO chain ---
        "Macro Sentinel":          {"file": "macro_sentinel.md",          "mode": "PRO"},
        "Data Analyst":            {"file": "data_analyst.md",            "mode": "PRO"},
        "State Validation Router": {"file": "state_validation_router.md", "mode": "PRO"},

        # --- Tier 2: THINKING chain (reasoning-heavy advocates & research) ---
        "Research Engine":         {"file": "research.md",                "mode": "THINKING"},
        "Macro Narrative Engine":  {"file": "macro_narrative_engine.md",  "mode": "THINKING"},
        "Bullish Advocate":        {"file": "bullish_gem.md",             "mode": "THINKING"},
        "Red Team Pessimist":      {"file": "red_team_gem.md",            "mode": "THINKING"},

        # --- Tier 3: FAST chain ---
        "Review Engine":           {"file": "post_trade_review.md",       "mode": "FAST"},

        # --- Tier 4: GEMMA chain (local / cost-efficient engines) ---
        "Neutral Structuralist":   {"file": "neutral_gem.md",             "mode": "GEMMA"},
        "Sentiment Engine":        {"file": "sentiment_engine.md",        "mode": "GEMMA"},
        "Structural Engine":       {"file": "structural_engine.md",       "mode": "GEMMA"},
        "Rule Enforcer Engine":    {"file": "rule_enforcer_engine.md",    "mode": "GEMMA"},
        "Context Engine":          {"file": "context_engine.md",          "mode": "GEMMA"},
        "Execution Engine":        {"file": "execution.md",               "mode": "GEMMA"},
        "Technical Validator":     {"file": "technical_validator.md",     "mode": "GEMMA"},
        "GEX Engine":              {"file": "gex_engine.md",              "mode": "GEMMA"},
    }

    # --- CONTEXT CACHING INITIALIZATION (ENH_CACHE_01) ---
    # Must be called AFTER sub_agent_configs is defined (bug-fix vs. prior version)
    subagent_files = [c["file"] for c in sub_agent_configs.values()] + ["terminal.md"]
    framework.setup_context_cache(subagent_files=subagent_files)

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
    if not os.path.exists("terminal.md"):
        print("Error: terminal.md not found!")
        sys.exit(1)

    terminal_instruction = framework.load_system_instruction("terminal.md")

    # Attach canonical rules knowledge base
    rules_path = os.path.join("GEM_Trading_Rules", "rules.md")
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
    print(f"   Antigravity: {'✅ Active' if os.path.exists('antigravity.md') else '⚠️  Missing'}")
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

    # ---------------------------------------------------------------------------
    # Chat session
    # ---------------------------------------------------------------------------
    chat = framework.client.chats.create(
        model=valid_model,
        config=agent_framework.types.GenerateContentConfig(
            system_instruction=terminal_instruction,
            temperature=1.0,
            tools=terminal_tools,
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
            response = chat.send_message(user_input)
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
