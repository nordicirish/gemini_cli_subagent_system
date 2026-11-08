import os
import sys
import json
from agent_framework import AgentFramework
import agent_framework
import tools
import threading
# cloud_sync removed — GDrive Decoupling Guardrail (ANTIGRAVITY v11.03)

def initialize_context_files():
    """Bootstraps missing context files for fresh repository clones."""
    if not os.path.exists("context"):
        os.makedirs("context")
    if not os.path.exists("logs"):
        os.makedirs("logs")
        
    user_config_default_content = json.dumps({
        "macro": [
            "^VIX", "^VVIX", "VIXY", "IEF", "UUP", "SPY", "GDX"
        ]
    }, indent=2)

    defaults = {
        "context/ssot.json": "{}",
        "context/trade_lessons.json": "[]",
        "context/decision_log.json": "[]",
        "context/user_config.json": user_config_default_content,
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

    # Ensure user_config.json contains default macro list if empty
    if os.path.exists("context/user_config.json"):
        try:
            with open("context/user_config.json", "r", encoding="utf-8") as f:
                uc_data = json.load(f)
            if not uc_data.get("macro"):
                uc_data["macro"] = [
                    "^VIX", "^VVIX", "VIXY", "IEF", "UUP", "SPY", "GDX"
                ]
                with open("context/user_config.json", "w", encoding="utf-8") as f:
                    json.dump(uc_data, f, indent=2)
        except Exception as e:
            pass

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
        "Regime Engine":           {"file": "engine_instructions/regime_engine.md",           "mode": "GEMMA"},
        "Strategy Engine":         {"file": "engine_instructions/strategy_engine.md",         "mode": "GEMMA"},
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
    print(f"   Version : v11.56-Pure-Antigravity-Engine-Cloud-Decoupled")
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
    except Exception as e:
        print(f"Failed to compile master document: {e}")

    sys_instruction = framework._get_sys_instruction(terminal_instruction)

    chat = framework.create_chat_session(sys_instruction, terminal_tools, orchestrator_model)

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
            
            # Multimodal Chart Part Loading
            chart_parts = []
            chart_dir = "context/charts"
            if os.path.exists(chart_dir):
                import glob
                for filepath in sorted(glob.glob(os.path.join(chart_dir, "chart_*_1m.png"))):
                    try:
                        with open(filepath, "rb") as f:
                            data = f.read()
                            if data:
                                chart_parts.append(
                                    agent_framework.types.Part.from_bytes(data=data, mime_type="image/png")
                                )
                    except Exception:
                        pass

            payload_to_send = [*chart_parts, current_message] if chart_parts else current_message

            try:
                from tool_dispatcher import GLOBAL_TOOL_DISPATCHER
                dispatch_res = GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session(
                    session=chat,
                    initial_message=payload_to_send,
                    part_factory=agent_framework.types.Part,
                    agent_role="Terminal Orchestrator",
                    log_callback=framework.log,
                )
                final_text = dispatch_res["response"]
            except Exception as exc:
                framework.log(f"[Orchestrator Error] Antigravity chat session failed: {exc}")
                raise exc

            print("\n[Terminal Orchestrator] Response:")
            print(final_text)

            turn_cost = framework.turn_usage['estimated_cost']
            session_cost = framework.session_cost
            print(f"[Diagnostics] Turn Cost: ${turn_cost:.4f} | Total Session Cost: ${session_cost:.4f}")

        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except Exception as e:
            print(f"\n[Error] {str(e)}")


if __name__ == "__main__":
    main()
