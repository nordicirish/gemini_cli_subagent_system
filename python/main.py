import os
import sys
import json

# `python python/main.py` puts only python/ on sys.path, but the database package imports
# `python.database...`, which also needs the repo root (tests get it from running in the root).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(1, _REPO_ROOT)

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

    from tool_registry import GLOBAL_OWNER_APPROVAL
    GLOBAL_OWNER_APPROVAL.interactive_cli_enabled = True

    # Initialize the Framework and unified runtime bundle (CLI/Web 100% parity)
    framework = AgentFramework()
    if not os.path.exists("engine_instructions/terminal.md"):
        print("Error: terminal.md not found!")
        sys.exit(1)

    try:
        from agent_factory import build_agent_runtime
    except ImportError:
        from python.agent_factory import build_agent_runtime

    runtime = build_agent_runtime(framework)
    terminal_tools = runtime.terminal_tools
    terminal_instruction = runtime.terminal_instruction
    orchestrator_model = runtime.orchestrator_model

    # ---------------------------------------------------------------------------
    # Startup banner
    # ---------------------------------------------------------------------------
    rules_path = os.path.join("gem_trading_rules", "rules.md")
    print("\n" + "=" * 60)
    print("💎 GEM CLI ORCHESTRATOR READY 💎")
    print("   Version : v11.56-Pure-Antigravity-Engine-Cloud-Decoupled")
    print(f"   Council : {len(runtime.council_members)} read-only agents | 7 deterministic engines")
    print(f"   Rules   : {'✅ Attached' if os.path.exists(rules_path) else '⚠️  Missing'}")
    antigravity_path = os.path.join(".agents", "rules", "antigravity.md")
    print(f"   Antigravity: {'✅ Active' if os.path.exists(antigravity_path) else '⚠️  Missing'}")
    print("=" * 60)
    print("Type 'exit' or 'quit' to stop.\n")

    sys_instruction = framework._get_sys_instruction(terminal_instruction)

    # Live EUR/USD rate for USD cash derivation (replaces the former hardcoded 1.08)
    import fx_rates
    fx_rates.start_background_refresher(log=framework.log)
    tools.start_market_daemon(suppress_daemon_stdout=True)

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
            auto_data_block = ""
            try:
                from agent_factory import load_live_analytics_context
                from analytics_engine import ANALYTICS_ENGINE
                live_state, slim_ssot, fx_snapshot, ny_dt = load_live_analytics_context()
                precomputed = ANALYTICS_ENGINE.evaluate(
                    live_state, ssot=slim_ssot, fx_info=fx_snapshot, ny_dt=ny_dt
                )
                cli_packet = {
                    "timestamp": live_state.get("timestamp", "") if isinstance(live_state, dict) else "",
                    "market_status": live_state.get("status", "UNKNOWN") if isinstance(live_state, dict) else "UNKNOWN",
                    "fx": fx_snapshot,
                    "ssot": slim_ssot,
                    "precomputed_analytics": precomputed,
                }
                auto_data_block = (
                    "\n[SYSTEM AUTO-INJECT: LIVE_DATA_PACKET]\n```json\n"
                    + json.dumps(cli_packet, separators=(",", ":"), default=str)
                    + "\n```\n[END_DATA_PACKET]\n"
                )
            except Exception:
                auto_data_block = ""

            payload_to_send = f"{active_model_str}{auto_data_block}[USER_QUERY]: {user_input}"

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

            _review_pending_proposals()

            turn_cost = framework.turn_usage['estimated_cost']
            session_cost = framework.session_cost
            print(f"[Diagnostics] Turn Cost: ${turn_cost:.4f} | Total Session Cost: ${session_cost:.4f}")

        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except Exception as e:
            print(f"\n[Error] {str(e)}")


def _review_pending_proposals():
    """Asks the owner to apply or discard portfolio changes the agents proposed this turn."""
    from portfolio_proposals import PROPOSAL_STORE

    for proposal in PROPOSAL_STORE.list(status="pending"):
        print(f"\n[Proposal {proposal['proposal_id']}] from {proposal['source']}:")
        for change in proposal["positions"]:
            print(
                f"   {change['kind'].upper():6} {change['ticker']:8} shares {change['shares_before'] or '-'} -> "
                f"{change['shares_after']} | wac {change['wac_before'] or '-'} -> {change['wac_after']}"
            )
        for change in proposal["cash"]:
            print(f"   CASH   {change['currency']:8} {change['before']} -> {change['after']}")
        answer = input("Apply this portfolio change? [y/N] > ").strip().lower()
        try:
            if answer in ("y", "yes"):
                PROPOSAL_STORE.apply(proposal["proposal_id"], approver_role="Owner")
                print("   Applied.")
            else:
                PROPOSAL_STORE.discard(proposal["proposal_id"])
                print("   Discarded. Nothing was written.")
        except Exception as e:
            print(f"   Not applied: {e}")


if __name__ == "__main__":
    main()
