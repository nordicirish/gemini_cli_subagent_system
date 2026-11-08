"""
Integration verification test for Antigravity App Agent reconfiguration
"""

import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from agent_framework import AgentFramework
import tools

def main():
    print("=" * 60)
    print("🚀 Verifying GEM Antigravity App Agent Reconfiguration")
    print("=" * 60)

    framework = AgentFramework()
    print(f"[Verification 1] Active Backend: {framework.backend}")
    assert framework.backend == "antigravity", f"Expected 'antigravity', got {framework.backend}"

    print(f"[Verification 2] Antigravity Runner binary: {framework.antigravity_runner.agy_path}")
    assert framework.antigravity_runner.is_available, "Antigravity runner is not available!"

    # Test single subagent tool generation
    print("\n[Verification 3] Testing subagent generation via Antigravity...")
    sub_tool = framework.create_agent_tool(
        "Macro Sentinel",
        "engine_instructions/macro_sentinel.md",
        mode="PRO"
    )
    print("Subagent tool created:", sub_tool.__name__)

    # Test quick direct call
    print("\n[Verification 4] Invoking subagent directly...")
    res = sub_tool("Test status check. Output JSON: {\"status\": \"ONLINE\", \"engine\": \"Macro Sentinel\"}")
    print("Subagent Response:\n", res)
    assert "Macro Sentinel" in res, "Expected 'Macro Sentinel' in response"

    # Test Chat session adapter
    print("\n[Verification 5] Verifying AntigravityChatSession adapter...")
    session = framework.create_chat_session("You are GEM Orchestrator.", [sub_tool])
    assert hasattr(session, "send_message"), "Session missing send_message"
    assert hasattr(session, "get_history"), "Session missing get_history"
    print("Chat session adapter successfully initialized.")

    print("\n" + "=" * 60)
    print("✅ All Antigravity Integration Verifications PASSED!")
    print("=" * 60)

if __name__ == "__main__":
    main()

