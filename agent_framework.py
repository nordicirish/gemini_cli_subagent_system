import os
import json
import time
import requests
import concurrent.futures
from google import genai
from google.genai import types

# ---------------------------------------------------------------------------
# Model definitions
# ---------------------------------------------------------------------------
MODEL_MAPPING = {
    "PRO":      ["gemini-3.1-pro-preview", "gemini-2.5-pro", "gemini-2.0-flash"],
    "THINKING": ["gemini-3.1-pro-preview", "gemini-2.5-pro"],
    "FAST":     ["gemini-2.5-flash", "gemini-2.0-flash"],
    "LOCAL_1B": ["gemma4:e4b"],   # Consolidated to e4b for memory efficiency
    "LOCAL_4B": ["gemma4:e4b"],   # Analytical agents
}

# If Ollama is unreachable, fall back to these Gemini modes
LOCAL_FALLBACK = {
    "LOCAL_1B": "FAST",
    "LOCAL_4B": "PRO",
}

LOCAL_MODES = {"LOCAL_1B", "LOCAL_4B"}


# ---------------------------------------------------------------------------
# Thin response wrapper so Ollama responses look like Gemini responses
# ---------------------------------------------------------------------------
class _LocalResponse:
    """Makes Ollama text responses duck-type compatible with Gemini responses."""
    def __init__(self, text: str):
        self.text = text


# ---------------------------------------------------------------------------
# Ollama client
# ---------------------------------------------------------------------------
class LocalOllamaClient:
    """Lightweight client for Ollama's /api/chat endpoint."""

    def __init__(self, base_url: str = "http://localhost:11434"):
        self.base_url = base_url

    def is_available(self) -> bool:
        """Quick connectivity check — returns True if Ollama is running."""
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    def generate(self, model: str, system_instruction: str, prompt: str, tools=None) -> str:
        """Send a chat request. If tools provided, inject them into the system prompt."""
        full_system = system_instruction
        if tools:
            tool_desc = ""
            for t in tools:
                if hasattr(t, '__name__'):
                    name = t.__name__
                    doc = t.__doc__ or "No description."
                    tool_desc += f"- {name}: {doc}\n"
                elif isinstance(t, dict) and "google_search" in t:
                    tool_desc += "- google_search: Search the web for live information.\n"
            
            full_system += f"\n\n--- AVAILABLE TOOLS ---\n{tool_desc}\n"
            full_system += "\n[MANDATORY TOOL PROTOCOL]"
            full_system += "\nIf you need to read or update the state, you MUST output exactly one tool call in this format and NOTHING ELSE:"
            full_system += "\nCALL_TOOL: tool_name({\"arg\": \"val\"})"
            full_system += "\n\nExample to update state:"
            full_system += "\nCALL_TOOL: update_ssot({\"payload_json\": \"{\\\"mutable_state\\\": {\\\"remaining_cash_usd\\\": 100}}\"})"
            full_system += "\n\nDo not provide conversational filler if you are calling a tool. Call the tool FIRST."

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": full_system},
                {"role": "user",   "content": prompt},
            ],
            "stream": False,
        }
        response = requests.post(
            f"{self.base_url}/api/chat",
            json=payload,
            timeout=5, # Aggressive timeout for snappy UI
        )
        response.raise_for_status()
        return response.json()["message"]["content"]


# ---------------------------------------------------------------------------
# Main framework
# ---------------------------------------------------------------------------
class AgentFramework:
    def __init__(self, log_callback=None):
        # Load optional local model config from config.json
        self._local_config = self._load_local_config()
        self.log_callback = log_callback

        # Cloud client (Gemini)
        self.client = genai.Client()

        # Local client (Ollama)
        ollama_base = self._local_config.get("LOCAL_API_BASE", "http://localhost:11434")
        self.ollama = LocalOllamaClient(base_url=ollama_base)

        # Cached availability — checked once at first LOCAL call
        self._ollama_available: bool | None = None

        self.agents = {}

    def log(self, message: str):
        """Helper to print to console and send to callback if exists."""
        print(message)
        if self.log_callback:
            try:
                self.log_callback(message)
            except:
                pass

    def _load_local_config(self) -> dict:
        """Read config.json for Ollama settings (fails silently with defaults)."""
        try:
            with open("config.json", "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _get_local_model(self, mode: str) -> str:
        """Return the Ollama model name for a LOCAL mode, respecting config.json overrides."""
        if mode == "LOCAL_1B":
            return self._local_config.get("LOCAL_MODEL_1B", "gemma4:e2b")
        if mode == "LOCAL_4B":
            return self._local_config.get("LOCAL_MODEL_4B", "gemma4:e4b")
        return MODEL_MAPPING[mode][0]

    def _check_ollama(self) -> bool:
        """Check Ollama availability once per session and cache the result."""
        if self._ollama_available is None:
            self._ollama_available = self.ollama.is_available()
            if self._ollama_available:
                self.log("[Local] Ollama detected at http://localhost:11434 OK")
            else:
                self.log("[Local] Ollama not reachable — local agents will fall back to Gemini.")
        return self._ollama_available

    def warmup_local_models(self):
        """Background task to force Ollama to load the large models into VRAM."""
        if not self._check_ollama():
            return
            
        models = list(set([self._get_local_model("LOCAL_1B"), self._get_local_model("LOCAL_4B")]))
        for model in models:
            self.log(f"[Warmup] Triggering background load for: {model}...")
            try:
                # We use a short timeout because we don't care about the response, 
                # only that Ollama starts the load process.
                self.ollama.generate(
                    model=model,
                    system_instruction="You are a system monitor. Reply 'OK' to pings.",
                    prompt="ping"
                )
            except Exception:
                # It will likely timeout if loading, which is what we want (it's loading in the background)
                pass

    def load_system_instruction(self, json_file_path: str) -> str:
        """Loads a JSON file and formats it as a string for the system prompt."""
        if not os.path.exists(json_file_path):
            raise FileNotFoundError(f"Missing instruction file: {json_file_path}")
        with open(json_file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return json.dumps(data, indent=2)

    # -----------------------------------------------------------------------
    # Core generation method — routes to Ollama or Gemini based on mode
    # -----------------------------------------------------------------------
    def generate_response_with_fallback(self, prompt, instruction, mode, tools=None):
        """Generate a response, routing to Ollama (LOCAL modes) or Gemini (cloud modes)."""

        # --- LOCAL (Ollama) path ---
        if mode in LOCAL_MODES:
            model_name = self._get_local_model(mode)

            if self._check_ollama():
                try:
                    self.log(f"[Local] Calling {model_name} via Ollama...")
                    text = self.ollama.generate(
                        model=model_name,
                        system_instruction=instruction,
                        prompt=prompt,
                        tools=tools
                    )
                    
                    # Manual tool calling loop for Local models
                    # If the model outputs 'CALL_TOOL: tool_name({...})', we execute and loop once
                    if "CALL_TOOL:" in text:
                        import re
                        match = re.search(r"CALL_TOOL:\s*(\w+)\((.*)\)", text, re.DOTALL)
                        if match:
                            tool_name = match.group(1)
                            tool_args_str = match.group(2)
                            self.log(f"[Local Tool] Model requested: {tool_name}")
                            
                            # Find the tool function
                            target_tool = None
                            if tools:
                                for t in tools:
                                    if hasattr(t, '__name__') and t.__name__ == tool_name:
                                        target_tool = t
                                        break
                            
                            if target_tool:
                                try:
                                    args = json.loads(tool_args_str)
                                    # Handle single-arg vs multi-arg or naked string
                                    if isinstance(args, dict):
                                        tool_result = target_tool(**args)
                                    else:
                                        tool_result = target_tool(args)
                                        
                                    self.log(f"[Local Tool] Result received. Recalling model with context...")
                                    
                                    # Second turn: Feed result back to model
                                    new_prompt = f"{prompt}\n\n[TOOL_RESULT: {tool_name}]\n{tool_result}\n\nPlease proceed with your final response based on this data."
                                    return self.generate_response_with_fallback(new_prompt, instruction, mode, tools=None) # tools=None to prevent infinite loop
                                except Exception as tool_e:
                                    self.log(f"[Local Tool] Execution failed: {tool_e}")
                                    text += f"\n\n[System Error: Tool {tool_name} failed: {tool_e}]"
                            else:
                                self.log(f"[Local Tool] Error: Tool {tool_name} not found in toolbox.")
                                text += f"\n\n[System Error: Tool {tool_name} not found.]"

                    self.log(f"[Local] {model_name} responded.")
                    return _LocalResponse(text)
                except Exception as e:
                    self.log(f"[Local] FATAL: Ollama call failed ({e}). Disabling local models for this session to prevent stalls.")
                    self._ollama_available = False
                    # Force immediate fallback

            # Graceful fallback — use Gemini equivalent
            fallback_mode = LOCAL_FALLBACK.get(mode, "PRO")
            self.log(f"[Local] Routing {mode} → Gemini {fallback_mode} for this session.")
            return self.generate_response_with_fallback(prompt, instruction, fallback_mode, tools)

        # --- Cloud (Gemini) path ---
        models = MODEL_MAPPING.get(mode, MODEL_MAPPING["PRO"])
        last_error = None

        for model_name in models:
            self.log(f"[System] Attempting with model: {model_name}...")
            try:
                # Use a Chat session if tools are present to handle the loop automatically
                if tools:
                    chat = self.client.chats.create(
                        model=model_name,
                        config=types.GenerateContentConfig(
                            system_instruction=instruction,
                            temperature=1.0,
                            tools=tools
                        )
                    )
                    response = chat.send_message(prompt)
                    return response

                # Standard generation for no-tool calls
                config = types.GenerateContentConfig(
                    system_instruction=instruction,
                    temperature=1.0
                )
                response = self.client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=config
                )
                return response

            except Exception as e:
                error_msg = str(e)
                last_error = e

                if "429" in error_msg or "quota" in error_msg.lower():
                    self.log(f"[System] Rate limit hit for {model_name}. Waiting 32 seconds...")
                    time.sleep(32)
                    try:
                        self.log(f"[System] Retrying {model_name} after backoff...")
                        response = self.client.models.generate_content(
                            model=model_name,
                            contents=prompt,
                            config=config
                        )
                        return response
                    except Exception as retry_e:
                        self.log(f"[System] Retry failed for {model_name}: {retry_e}")
                        last_error = retry_e
                        continue

                elif "503" in error_msg or "unavailable" in error_msg.lower():
                    # Transient capacity spike — one quick retry, then fall through to next model
                    self.log(f"[System] 503 UNAVAILABLE for {model_name}. Retrying in 5s...")
                    time.sleep(5)
                    try:
                        response = self.client.models.generate_content(
                            model=model_name,
                            contents=prompt,
                            config=config
                        )
                        self.log(f"[System] {model_name} recovered.")
                        return response
                    except Exception:
                        pass  # Still down — fall through to next model immediately
                    self.log(f"[System] {model_name} still unavailable. Trying next model...")
                    continue

                elif "not found" in error_msg.lower():
                    self.log(f"[System] Model {model_name} not found. Skipping...")
                    continue
                else:
                    self.log(f"[System] Warning: {model_name} failed with error: {e}")
                    raise e

        raise RuntimeError(f"All models for mode {mode} failed. Last error: {last_error}")

    # -----------------------------------------------------------------------
    # Agent tool factory
    # -----------------------------------------------------------------------
    def create_agent_tool(self, name, json_file, mode="PRO", agent_tools=None):
        """
        Creates a callable function that acts as a sub-agent tool.
        This function can be passed to the Terminal orchestrator as a tool.
        """
        instruction = self.load_system_instruction(json_file)
        backend_label = "Local" if mode in LOCAL_MODES else "Gemini"

        def call_subagent(query: str) -> str:
            self.log(f"\n[Orchestrator -> {name} ({backend_label})] Delegating: {query[:100]}...")
            response = self.generate_response_with_fallback(
                prompt=query,
                instruction=instruction,
                mode=mode,
                tools=agent_tools
            )
            self.log(f"[{name}] Responded.")
            if hasattr(response, 'text'):
                return response.text
            return str(response)

        # Naming the function explicitly for tool discovery
        call_subagent.__name__ = f"ask_{name.lower().replace(' ', '_')}"
        call_subagent.__doc__ = (
            f"Use this tool to ask the {name} sub-agent a query. "
            f"Pass your request as a string. (Backend: {backend_label})"
        )

        return call_subagent

    def create_parallel_council_tool(self, agents_dict):
        """
        Creates a tool that can run multiple agents in parallel.
        'agents_dict' is a map of tool_name -> tool_function.
        """
        def ask_council(queries: dict) -> str:
            """
            Use this tool to ask multiple council members at the same time for faster results.
            Pass a JSON object where keys are agent names and values are the specific queries for them.
            Available agents: bullish_advocate, red_team_pessimist, neutral_structuralist, sentiment_engine, research_engine.
            Example: {"bullish_advocate": "Analyze ONDS alpha", "red_team_pessimist": "Find RCAT risks"}
            """
            self.log(f"\n[Parallel Dispatcher] Initializing parallel session for {list(queries.keys())}...")
            
            results = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(queries)) as executor:
                future_to_agent = {}
                for agent_name, query in queries.items():
                    # Map simplified name to the actual tool function
                    actual_tool_name = f"ask_{agent_name}"
                    if actual_tool_name in agents_dict:
                        target_func = agents_dict[actual_tool_name]
                        future = executor.submit(target_func, query)
                        future_to_agent[future] = agent_name
                    else:
                        results[agent_name] = f"Error: Agent '{agent_name}' not found."

                for future in concurrent.futures.as_completed(future_to_agent):
                    agent_name = future_to_agent[future]
                    try:
                        results[agent_name] = future.result()
                    except Exception as exc:
                        results[agent_name] = f"Error: {exc}"

            self.log(f"[Parallel Dispatcher] All agents responded.")
            return json.dumps(results, indent=2)

        ask_council.__name__ = "ask_council"
        return ask_council
