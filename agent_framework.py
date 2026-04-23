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
    "PRO":      ["gemini-2.5-pro", "gemma-4-31b-it", "gemini-2.5-flash"],
    "GEMMA":    ["gemma-4-31b-it", "gemini-2.5-flash"],
    "FAST":     ["gemini-2.5-flash", "gemma-4-31b-it"],
    "THINKING": ["gemini-2.5-pro"]
}


# ---------------------------------------------------------------------------
# Main framework
# ---------------------------------------------------------------------------
class AgentFramework:
    def __init__(self, log_callback=None):
        # Load optional local model config from config.json
        self._local_config = self._load_local_config()
        self.log_callback = log_callback

        # Cloud client (Gemini)
        api_key = self._local_config.get("GEMINI_API_KEY")
        if api_key:
            self.client = genai.Client(api_key=api_key)
        else:
            self.client = genai.Client()

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

    def _get_cloud_models(self, mode: str) -> list:
        """Return the list of models for a cloud mode, respecting config.json overrides."""
        base_list = MODEL_MAPPING.get(mode, MODEL_MAPPING["PRO"])
        # Override with config.json if present
        overrides = {
            "gemini-2.5-pro": self._local_config.get("MODEL_PRO_1", "gemini-2.5-pro"),
            "gemma-4-31b-it": self._local_config.get("MODEL_GEMMA", "gemma-4-31b-it"),
            "gemini-2.5-flash": self._local_config.get("MODEL_FLASH", "gemini-2.5-flash"),
        }
        return [overrides.get(m, m) for m in base_list]


    def load_system_instruction(self, file_path: str) -> str:
        """Loads a file and formats it as a string for the system prompt. Supports JSON and Markdown."""
        # Check if the requested file exists, otherwise try swapping .json to .md
        if not os.path.exists(file_path):
            alt_path = file_path.replace('.json', '.md')
            if os.path.exists(alt_path):
                file_path = alt_path
            else:
                raise FileNotFoundError(f"Missing instruction file: {file_path}")
                
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        if file_path.endswith('.json'):
            try:
                # Validate JSON and format it nicely
                return json.dumps(json.loads(content), indent=2)
            except json.JSONDecodeError:
                return content # Fallback to raw text if malformed
        else:
            return content

    # -----------------------------------------------------------------------
    # Core generation method — routes to Ollama or Gemini based on mode
    # -----------------------------------------------------------------------
    def generate_response_with_fallback(self, prompt, instruction, mode, tools=None):
        """Generate a response, routing to Ollama (LOCAL modes) or Gemini (cloud modes)."""

        # --- Cloud path (Gemini/Gemma) ---
        models = self._get_cloud_models(mode)
        last_error = None

        for model_name in models:
            self.log(f"[Cloud Execution] Attempting with model: {model_name}...")
            
            # --- SDK COMPATIBILITY LAYER ---
            # Convert simple google_search dicts to official Tool objects
            final_tools = []
            if tools:
                for t in tools:
                    if isinstance(t, dict) and "google_search" in t:
                        final_tools.append(types.Tool(google_search=types.GoogleSearch()))
                    else:
                        final_tools.append(t)
            
            # --- Unified Config with BLOCK_NONE safety for Forensic Data ---
            config = types.GenerateContentConfig(
                system_instruction=instruction,
                temperature=1.0,
                max_output_tokens=8192,
                tools=final_tools if final_tools else None,
                safety_settings=[
                    types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
                    types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
                    types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
                    types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE")
                ]
            )

            def attempt_call():
                if final_tools:
                    chat = self.client.chats.create(
                        model=model_name,
                        config=config
                    )
                    return chat.send_message(prompt)
                else:
                    return self.client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config=config
                    )

            try:
                return attempt_call()
            except Exception as e:
                error_msg = str(e)
                last_error = e

                if "429" in error_msg or "quota" in error_msg.lower():
                    import re
                    wait_time = 32
                    match = re.search(r"retry in (\d+(?:\.\d+)?)s", error_msg, re.IGNORECASE)
                    if match:
                        wait_time = int(float(match.group(1))) + 1
                        
                    self.log(f"[System] Rate limit hit for {model_name}. Waiting {wait_time} seconds...")
                    time.sleep(wait_time)
                    try:
                        self.log(f"[System] Retrying {model_name} after backoff...")
                        return attempt_call()
                    except Exception as retry_e:
                        self.log(f"[System] Retry failed for {model_name}: {retry_e}")
                        last_error = retry_e
                        continue

                elif "503" in error_msg or "unavailable" in error_msg.lower():
                    # Transient capacity spike — one quick retry, then fall through to next model
                    self.log(f"[System] 503 UNAVAILABLE for {model_name}. Retrying in 5s...")
                    time.sleep(5)
                    try:
                        return attempt_call()
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
        backend_label = "Gemini"

        def call_subagent(query: str) -> str:
            self.log(f"\n[Orchestrator -> {name} ({backend_label})] Delegating: {query[:100]}...")
            response = self.generate_response_with_fallback(
                prompt=query,
                instruction=instruction,
                mode=mode,
                tools=agent_tools
            )
            self.log(f"[{name}] Responded.")
            
            # Robust text capture from parts
            res_text = ""
            try:
                for part in response.candidates[0].content.parts:
                    if part.text:
                        res_text += part.text
            except:
                if hasattr(response, 'text'):
                    res_text = response.text
            
            return res_text or str(response)

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
