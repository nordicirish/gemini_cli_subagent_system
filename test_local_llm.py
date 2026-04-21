"""
test_local_llm.py - Diagnostic test for the Gemma 4 / Ollama local setup.

Run with: python test_local_llm.py
"""

import json
import sys
import time
import requests

CONFIG_FILE = "config.json"
PASS = "[PASS]"
FAIL = "[FAIL]"
WARN = "[WARN]"

# -- helpers ----------------------------------------------------------------

def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def hr(label=""):
    width = 60
    if label:
        pad = (width - len(label) - 2) // 2
        print(f"\n{'-' * pad} {label} {'-' * pad}")
    else:
        print("-" * width)

def result(status, msg):
    print(f"  {status}  {msg}")

# -- tests ------------------------------------------------------------------

def test_ollama_reachable(base_url):
    hr("1. Ollama connectivity")
    try:
        r = requests.get(f"{base_url}/api/tags", timeout=3)
        if r.status_code == 200:
            result(PASS, f"Ollama is running at {base_url}")
            return True
        else:
            result(FAIL, f"Ollama returned HTTP {r.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        result(FAIL, f"Cannot reach {base_url} — is Ollama installed and running?")
        print()
        print("  To install:  winget install Ollama.Ollama")
        print("  Then restart your terminal and try again.")
        return False
    except Exception as e:
        result(FAIL, f"Unexpected error: {e}")
        return False


def test_models_pulled(base_url, model_1b, model_4b):
    hr("2. Models pulled")
    try:
        r = requests.get(f"{base_url}/api/tags", timeout=3)
        available = {m["name"] for m in r.json().get("models", [])}

        ok = True
        for model in [model_1b, model_4b]:
            # Ollama tags can be "gemma4:e2b" or "gemma4:e2b:latest" — check prefix
            found = any(a.startswith(model.split(":")[0]) and model.split(":")[-1] in a
                        for a in available) or model in available
            if found:
                result(PASS, f"{model} is available locally")
            else:
                result(FAIL, f"{model} NOT found — run:  ollama pull {model}")
                ok = False

        if available:
            print(f"\n  All local models on this machine: {', '.join(sorted(available))}")
        return ok
    except Exception as e:
        result(FAIL, f"Could not list models: {e}")
        return False


def test_inference(base_url, model, label):
    prompt = (
        "You are a trading analysis assistant. "
        "Reply with exactly one sentence: confirm you are working and state your model name."
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a concise assistant."},
            {"role": "user",   "content": prompt},
        ],
        "stream": False,
    }
    try:
        t0 = time.time()
        r = requests.post(f"{base_url}/api/chat", json=payload, timeout=120)
        elapsed = time.time() - t0
        r.raise_for_status()
        reply = r.json()["message"]["content"].strip()
        tokens_per_sec = len(reply.split()) / elapsed  # rough estimate
        result(PASS, f"{label} ({model}) responded in {elapsed:.1f}s (~{tokens_per_sec:.1f} words/sec)")
        print(f"  - Reply: \"{reply[:120]}\"")
        return True
    except requests.exceptions.Timeout:
        result(FAIL, f"{label} timed out — model may still be loading, try again in 30s")
        return False
    except Exception as e:
        result(FAIL, f"{label} inference failed: {e}")
        return False


def test_agent_framework():
    hr("4. AgentFramework integration")
    try:
        from agent_framework import AgentFramework, LOCAL_MODES
        fw = AgentFramework()

        # Check LOCAL_MODES recognised
        if "LOCAL_1B" in LOCAL_MODES and "LOCAL_4B" in LOCAL_MODES:
            result(PASS, "LOCAL_1B and LOCAL_4B modes are registered in AgentFramework")
        else:
            result(FAIL, "LOCAL modes not found — did agent_framework.py get updated?")
            return False

        # Check config was loaded correctly
        m1b = fw._get_local_model("LOCAL_1B")
        m4b = fw._get_local_model("LOCAL_4B")
        result(PASS, f"LOCAL_1B model resolved to: {m1b}")
        result(PASS, f"LOCAL_4B model resolved to: {m4b}")

        # Check Ollama detection
        available = fw._check_ollama()
        if available:
            result(PASS, "AgentFramework successfully detected Ollama")
        else:
            result(WARN, "AgentFramework could not detect Ollama (will fall back to Gemini)")

        return True
    except ImportError as e:
        result(FAIL, f"Could not import agent_framework: {e}")
        return False
    except Exception as e:
        result(FAIL, f"AgentFramework test error: {e}")
        return False


def test_main_routing():
    hr("5. main.py agent routing")
    try:
        import ast, os
        with open("main.py", "r", encoding="utf-8") as f:
            src = f.read()

        local_agents = []
        gemini_agents = []

        # Simple text scan — look for LOCAL_ assignments
        for line in src.splitlines():
            if '"mode"' in line or "'mode'" in line:
                if "LOCAL_1B" in line or "LOCAL_4B" in line:
                    # Extract agent name from the line above
                    local_agents.append(line.strip())
                elif any(m in line for m in ['"PRO"', '"THINKING"', '"FAST"']):
                    gemini_agents.append(line.strip())

        result(PASS, f"{len(local_agents)} agents routed to LOCAL (Gemma):")
        for a in local_agents:
            print(f"  - {a}")
        result(PASS, f"{len(gemini_agents)} agents remain on Gemini:")
        for a in gemini_agents:
            print(f"  - {a}")
        return True
    except Exception as e:
        result(FAIL, f"Could not inspect main.py: {e}")
        return False


# -- main -------------------------------------------------------------------

def main():
    print()
    print("\n" + "="*60)
    print("     GEM System - Local Gemma 4 / Ollama Diagnostic        ")
    print("="*60)

    cfg = load_config()
    base_url = cfg.get("LOCAL_API_BASE", "http://localhost:11434")
    model_1b = cfg.get("LOCAL_MODEL_1B", "gemma4:e2b")
    model_4b = cfg.get("LOCAL_MODEL_4B", "gemma4:e4b")

    print(f"\n  Config loaded from {CONFIG_FILE}")
    print(f"  LOCAL_API_BASE  = {base_url}")
    print(f"  LOCAL_MODEL_1B  = {model_1b}")
    print(f"  LOCAL_MODEL_4B  = {model_4b}")

    results = []

    # 1. Connectivity
    ollama_ok = test_ollama_reachable(base_url)
    results.append(("Ollama connectivity", ollama_ok))

    if not ollama_ok:
        print("\n  [STOP] Ollama is not running - skipping remaining tests.")
        print("  To install:  winget install Ollama.Ollama")
        print("  Then restart your terminal and try again.\n")
        sys.exit(1)

    # 2. Models
    models_ok = test_models_pulled(base_url, model_1b, model_4b)
    results.append(("Models pulled", models_ok))

    # 3. Inference tests
    hr("3. Live inference tests (this may take 30–90s on CPU)")
    print(f"  Testing {model_1b} (fast rule agents)...")
    inf_1b = test_inference(base_url, model_1b, "LOCAL_1B")
    results.append((f"{model_1b} inference", inf_1b))

    print(f"\n  Testing {model_4b} (analytical agents)...")
    inf_4b = test_inference(base_url, model_4b, "LOCAL_4B")
    results.append((f"{model_4b} inference", inf_4b))

    # 4. Framework integration
    fw_ok = test_agent_framework()
    results.append(("AgentFramework integration", fw_ok))

    # 5. Routing check
    routing_ok = test_main_routing()
    results.append(("main.py routing", routing_ok))

    # Summary
    hr("Summary")
    all_passed = True
    for name, ok in results:
        status = PASS if ok else FAIL
        result(status, name)
        if not ok:
            all_passed = False

    print()
    if all_passed:
        print("  DONE All tests passed! Your Gemma 4 local setup is working correctly.")
        print("     Run  python main.py  to start the full agent system.")
    else:
        print("  WARN Some tests failed. Fix the issues above and re-run this script.")
    print()


if __name__ == "__main__":
    main()
