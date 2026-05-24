import os
import json
import time
import hashlib
import requests
import concurrent.futures
from google import genai
from google.genai import types

# ---------------------------------------------------------------------------
# Model definitions — v10.38-Interactions-API-Mitigation
# Per terminal.md > Mode Selection Matrix (Canonical)
# ---------------------------------------------------------------------------
DEFAULT_MODEL_PRO = "gemini-2.5-pro"
DEFAULT_MODEL_FLASH = "gemini-2.5-flash"
DEFAULT_MODEL_GEMMA = "gemma-4-31b-it"
DEFAULT_MODEL_THINKING = "gemini-2.0-flash-thinking-exp"

MODEL_MAPPING = {
    "PRO":      [DEFAULT_MODEL_PRO, "gemini-3.1-pro-preview", "gemini-2.0-pro-exp", "gemini-1.5-pro", DEFAULT_MODEL_FLASH],
    "FLASH":    [DEFAULT_MODEL_FLASH, "gemini-3-flash-preview", DEFAULT_MODEL_GEMMA, "gemini-1.5-flash"],
    "GEMMA":    [DEFAULT_MODEL_GEMMA, DEFAULT_MODEL_FLASH, DEFAULT_MODEL_PRO],
    "THINKING": [DEFAULT_MODEL_THINKING, DEFAULT_MODEL_PRO, "gemini-3.1-pro-preview", "gemini-2.0-pro-exp", "gemini-1.5-pro", DEFAULT_MODEL_FLASH],
    "FAST":     [DEFAULT_MODEL_FLASH, "gemini-3-flash-preview", DEFAULT_MODEL_GEMMA, "gemini-1.5-flash"],
}

CACHE_VERSION = "GEM_CACHE_v10.35"



# ---------------------------------------------------------------------------
# Main framework
# ---------------------------------------------------------------------------
class AgentFramework:
    def __init__(self, log_callback=None):
        # Load optional local model config from config.json
        self._local_config = self._load_local_config()
        self.free_tier_only = self._local_config.get("FREE_TIER_ONLY", True)
        self.log_callback = log_callback

        # Cloud client (Gemini)
        api_key = self._local_config.get("GEMINI_API_KEY")
        if api_key:
            self.client = genai.Client(api_key=api_key)
        else:
            self.client = genai.Client()

        # Free tier client fallback (Gemini Free Key Routing)
        free_api_key = (
            self._local_config.get("GEMINI_FREE_TIER_API_KEY")
            or os.environ.get("GEMINI_FREE_TIER_API_KEY")
            or os.environ.get("GEMINI_FREE_API_KEY")
            or os.environ.get("GOOGLE_FREE_TIER_API_KEY")
        )
        if free_api_key:
            self.free_client = genai.Client(api_key=free_api_key)
        else:
            self.free_client = self.client

        self.agents = {}

        # Caching state (ENH_CACHE_01)
        self.cached_content_name = None
        self.last_cache_hash = None
        self.cache_disabled = self._local_config.get("DISABLE_CACHE", False)  # Track Free Tier or manual override
        
        # Telemetry
        self.reset_turn_usage()

    def _is_free_tier_model(self, model_name: str) -> bool:
        """Check if a model is considered a free-tier model."""
        name_lower = model_name.lower()
        return "flash" in name_lower or "gemma" in name_lower

    def reset_turn_usage(self):
        """Clear token counters for a new chat session turn."""
        self.turn_usage = {'prompt_tokens': 0, 'candidates_tokens': 0, 'cached_tokens': 0}

    def log(self, message: str):
        """Helper to print to console and send to callback if exists."""
        print(message)
        if self.log_callback:
            try:
                self.log_callback(message)
            except Exception:
                pass

    def _load_local_config(self) -> dict:
        """Read config.json for settings (fails silently with defaults)."""
        try:
            with open("context/config.json", "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _get_cloud_models(self, mode: str) -> list:
        """Return the list of models for a cloud mode, respecting config.json overrides and free tier priority."""
        base_list = MODEL_MAPPING.get(mode, MODEL_MAPPING["PRO"])
        overrides = {
            DEFAULT_MODEL_PRO:      self._local_config.get("MODEL_PRO_1",      DEFAULT_MODEL_PRO),
            DEFAULT_MODEL_GEMMA:    self._local_config.get("MODEL_GEMMA",      DEFAULT_MODEL_GEMMA),
            DEFAULT_MODEL_FLASH:    self._local_config.get("MODEL_FLASH",      DEFAULT_MODEL_FLASH),
            DEFAULT_MODEL_THINKING: self._local_config.get("MODEL_THINKING",   DEFAULT_MODEL_THINKING),
        }
        resolved = [overrides.get(m, m) for m in base_list]
        
        # If in free tier only mode, dynamically filter out paid/Pro models
        if getattr(self, "free_tier_only", True):
            filtered = []
            for m in resolved:
                m_lower = m.lower()
                is_paid = "pro" in m_lower or ("preview" in m_lower and "flash" not in m_lower and "thinking" not in m_lower)
                if not is_paid:
                    filtered.append(m)
            
            # Fallback to flash if all models were filtered out
            if not filtered:
                filtered.append(overrides.get(DEFAULT_MODEL_FLASH, DEFAULT_MODEL_FLASH))
            return filtered
            
        return resolved

    def load_system_instruction(self, file_path: str) -> str:
        """Loads a file and formats it as a string for the system prompt.
        Prefers .md directly; falls back .json → .md if needed."""
        # Prefer .md over .json (canonical format per v10.02)
        if not os.path.exists(file_path):
            # Try .md sibling
            alt_md = file_path.replace(".json", ".md")
            alt_json = file_path.replace(".md", ".json")
            if os.path.exists(alt_md):
                file_path = alt_md
            elif os.path.exists(alt_json):
                file_path = alt_json
            else:
                raise FileNotFoundError(f"Missing instruction file: {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        if file_path.endswith(".json"):
            try:
                return json.dumps(json.loads(content), indent=2)
            except json.JSONDecodeError:
                return content
        else:
            return content

    # -----------------------------------------------------------------------
    # Context Caching Logic (ENH_CACHE_01)
    # -----------------------------------------------------------------------
    def setup_context_cache(self, model: str = None, subagent_files: list = None, system_instruction=None, tools=None):
        """
        Aggregates rules.md, trade_lessons.json, and subagent instructions into a
        CachedContent object to reduce latency and token usage.
        Must be called AFTER sub_agent_configs is defined.
        """
        if getattr(self, "cache_disabled", False):
            if self.cached_content_name:
                try:
                    self.log(f"[System] Cleaning up cached content: {self.cached_content_name}")
                    self.client.caches.delete(name=self.cached_content_name)
                except Exception as e:
                    self.log(f"[System] Cache delete failed: {e}")
            self.cached_content_name = None
            self.last_cache_hash = None
            return

        self.log(f"[System] Initializing Context Cache assessment for {model or 'default'}...")

        parts = []

        # Add Rules.md (v10.02 canonical)
        rules_path = os.path.join("gem_trading_rules", "rules.md")
        if os.path.exists(rules_path):
            with open(rules_path, "r", encoding="utf-8") as f:
                parts.append(f"## CANONICAL RULES ENGINE (v10.02)\n{f.read()}\n\n")

        # Add Trade Lessons
        if os.path.exists("context/trade_lessons.json"):
            with open("context/trade_lessons.json", "r", encoding="utf-8") as f:
                parts.append(f"## TRADE LESSONS REPOSITORY\n{f.read()}\n\n")

        # Add Subagent Instructions
        if subagent_files:
            for f_name in subagent_files:
                try:
                    content = self.load_system_instruction(f_name)
                    parts.append(f"## SUBAGENT_INSTRUCTION: {f_name}\n{content}\n\n")
                except Exception:
                    continue

        full_content = "".join(parts)
        content_hash = hashlib.md5((full_content + str(model)).encode("utf-8")).hexdigest()

        # Check if cache is already valid
        if self.last_cache_hash == content_hash and self.cached_content_name:
            self.log("[System] Context Cache hit (Hash matches). Skipping recreation.")
            return

        target_model = model if model else self._get_cloud_models("PRO")[0]

        try:
            self.log(f"[System] Creating Context Cache for {target_model} (~{len(full_content)} chars)...")
            # Format tools for cache
            formatted_tools = []
            if tools:
                for t in tools:
                    if isinstance(t, dict) and "google_search" in t:
                        formatted_tools.append(types.Tool(google_search=types.GoogleSearch()))
                    elif isinstance(t, types.Tool):
                        formatted_tools.append(t)
                    elif callable(t):
                        fd = types.FunctionDeclaration.from_callable(client=self.client, callable=t)
                        formatted_tools.append(types.Tool(function_declarations=[fd]))
                    else:
                        formatted_tools.append(t)

            cache = self.client.caches.create(
                model=target_model,
                config=types.CreateCachedContentConfig(
                    system_instruction=system_instruction,
                    tools=formatted_tools if formatted_tools else None,
                    display_name=f"{CACHE_VERSION}_{content_hash[:8]}",
                    contents=[types.Content(role="user", parts=[types.Part(text=full_content)])],
                    ttl="7200s",  # 2-hour TTL
                ),
            )
            self.cached_content_name = cache.name
            self.last_cache_hash = content_hash
            self.log(f"[System] Context Cache SUCCESS: {cache.name}")
        except Exception as e:
            error_str = str(e)
            if "RESOURCE_EXHAUSTED" in error_str or "limit=0" in error_str:
                self.log("[Warning] Context Cache DISABLED (Free Tier detected). Switching to Standard Inference.")
                self.cache_disabled = True
            else:
                self.log(f"[System] Context Cache FAILED: {e}. Falling back to standard inference.")
            self.cached_content_name = None

    # -----------------------------------------------------------------------
    # Core generation method
    # -----------------------------------------------------------------------
    def generate_response_with_fallback(self, prompt, instruction, mode, tools=None):
        """Generate a response, routing to Gemini (cloud modes) with Caching support."""
        if getattr(self, "cancel_check", None) and self.cancel_check():
            self.log("[Framework] Cancel signal active before starting call.")
            raise RuntimeError("Operation cancelled by user.")

        models = self._get_cloud_models(mode)
        last_error = None

        for model_name in models:
            if getattr(self, "cancel_check", None) and self.cancel_check():
                self.log("[Framework] Cancel signal active. Aborting fallback loop.")
                raise RuntimeError("Operation cancelled by user.")

            self.log(f"[Cloud Execution] Attempting with model: {model_name}...")

            final_tools = []
            if tools:
                for t in tools:
                    if isinstance(t, dict) and "google_search" in t:
                        final_tools.append(types.Tool(google_search=types.GoogleSearch()))
                    else:
                        final_tools.append(t)

            # Cache association — PRO and THINKING modes only
            cache_to_use = None
            instruction_to_pass = instruction
            tools_to_pass = final_tools if final_tools else None

            if self.cached_content_name and mode in ["PRO", "THINKING"]:
                cache_to_use = self.cached_content_name
                if instruction:
                    # Append instruction to prompt since it can't be in GenerateContentConfig with cache
                    prompt = f"[SYSTEM INSTRUCTION OVERRIDE]\n{instruction}\n\n[USER PROMPT]\n{prompt}"
                    instruction_to_pass = None
                # Tools are assumed to be in the cache. Passing them in GenerateContentConfig raises an error.
                tools_to_pass = None

            config = types.GenerateContentConfig(
                system_instruction=instruction_to_pass,
                temperature=1.0,
                max_output_tokens=8192,
                tools=tools_to_pass,
                cached_content=cache_to_use,
                safety_settings=[
                    types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH",       threshold="BLOCK_NONE"),
                    types.SafetySetting(category="HARM_CATEGORY_HARASSMENT",         threshold="BLOCK_NONE"),
                    types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT",  threshold="BLOCK_NONE"),
                    types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT",  threshold="BLOCK_NONE"),
                ],
            )

            # Determine clients to try for this model
            is_free_tier = self._is_free_tier_model(model_name)
            clients_to_try = []
            if is_free_tier and getattr(self, "free_client", None) and self.free_client is not self.client:
                clients_to_try = [
                    ("Free-Tier Key", self.free_client),
                    ("Primary Key", self.client)
                ]
            else:
                clients_to_try = [
                    ("Primary Key", self.client)
                ]

            for client_label, active_client in clients_to_try:
                if len(clients_to_try) > 1:
                    self.log(f"[Cloud Execution] Trying client with {client_label} for model {model_name}...")

                def attempt_call(client_instance):
                    if getattr(self, "cancel_check", None) and self.cancel_check():
                        self.log("[Framework] Cancel signal active before starting API call.")
                        raise RuntimeError("Operation cancelled by user.")
                    if final_tools:
                        chat = client_instance.chats.create(model=model_name, config=config)
                        res = chat.send_message(prompt)
                    else:
                        res = client_instance.models.generate_content(
                            model=model_name, contents=prompt, config=config
                        )
                    
                    if hasattr(res, 'usage_metadata') and res.usage_metadata:
                        self.turn_usage['prompt_tokens'] += (res.usage_metadata.prompt_token_count or 0)
                        self.turn_usage['candidates_tokens'] += (res.usage_metadata.candidates_token_count or 0)
                        self.turn_usage['cached_tokens'] += (res.usage_metadata.cached_content_token_count or 0)
                    return res

                try:
                    return attempt_call(active_client)
                except Exception as e:
                    error_msg = str(e)
                    last_error = e
                    self.log(f"[Warning] Call failed using {client_label} for model {model_name}: {error_msg}")

                    # If this is the free tier client and we have a primary client fallback, proceed immediately to primary key
                    if client_label == "Free-Tier Key" and len(clients_to_try) > 1:
                        self.log(f"[System] Falling back from Free-Tier to Primary Key for {model_name}...")
                        continue

                    # Cache expired/invalid — retry immediately without cache
                    if "cache" in error_msg.lower() and cache_to_use:
                        self.log("[System] Cache expired or invalid. Retrying without cache...")
                        self.cached_content_name = None
                        return self.generate_response_with_fallback(prompt, instruction, mode, tools)

                    if "429" in error_msg or "quota" in error_msg.lower():
                        import re
                        wait_time = 32
                        match = re.search(r"retry in (\d+(?:\.\d+)?)s", error_msg, re.IGNORECASE)
                        if match:
                            wait_time = int(float(match.group(1))) + 1
                        self.log(f"[System] Rate limit hit for {model_name}. Waiting {wait_time}s...")
                        time.sleep(wait_time)
                        try:
                            return attempt_call(active_client)
                        except Exception as retry_e:
                            last_error = retry_e
                            continue

                    elif "503" in error_msg or "unavailable" in error_msg.lower():
                        self.log(f"[System] 503 UNAVAILABLE for {model_name}. Retrying in 5s...")
                        time.sleep(5)
                        try:
                            return attempt_call(active_client)
                        except Exception:
                            pass
                        continue

                    elif "not found" in error_msg.lower() or "404" in error_msg or "not supported" in error_msg.lower() or "interactions api" in error_msg.lower():
                        continue
                    else:
                        self.log(f"[System] Warning: {model_name} failed with error: {e}")
                        raise e

        raise RuntimeError(f"All models for mode {mode} failed. Last error: {last_error}")

    def create_agent_tool(self, name, file_path, mode="PRO", agent_tools=None):
        instruction = self.load_system_instruction(file_path)
        if self.cached_content_name:
            instruction += (
                "\n\nNOTE: You have access to the full CANONICAL RULES ENGINE (v10.02) "
                "and TRADE LESSONS in your cached context. "
                "Refer to them for all thresholds and mandates."
            )

        import datetime
        # Force US/Eastern (New York) Time — UTC-4 (EDT)
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-4)))
        ny_iso = now.strftime("%Y-%m-%dT%H:%M:%S")
        current_year = now.year

        temporal_anchor = (
            f"\n\n[SYSTEM_TIME (NEW YORK / ET): {ny_iso}]\n"
            f"[PRODUCTION_ERA: {current_year}]\n"
            f"[MANDATE]: The current year provided in the timestamp is the LIVE PRODUCTION ERA. "
            f"Treat all data from this era as grounded reality, not a simulation."
        )
        final_instruction = instruction + temporal_anchor

        def call_subagent(query: str) -> str:
            self.log(f"\n[Orchestrator -> {name}] Delegating: {query[:100]}...")
            response = self.generate_response_with_fallback(
                prompt=f"[SYSTEM_TIME (NEW YORK / ET): {ny_iso}] {query}",
                instruction=final_instruction,
                mode=mode,
                tools=agent_tools,
            )
            res_text = ""
            try:
                for part in response.candidates[0].content.parts:
                    if part.text:
                        res_text += part.text
            except Exception:
                if hasattr(response, "text"):
                    res_text = response.text
            return res_text or str(response)

        call_subagent.__name__ = f"ask_{name.lower().replace(' ', '_')}"
        call_subagent.__doc__ = f"Ask the {name} sub-agent."
        return call_subagent

    def create_parallel_council_tool(self, agents_dict):
        def ask_council(queries: dict) -> str:
            """Dispatch multiple council agents in parallel for 3x speed increase."""
            self.log(f"\n[Parallel Dispatcher] Initializing parallel session for {list(queries.keys())}...")
            results = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(queries)) as executor:
                future_to_agent = {}
                for agent_name, query in queries.items():
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
            self.log("[Parallel Dispatcher] All agents responded.")
            return json.dumps(results, indent=2)

        ask_council.__name__ = "ask_council"
        return ask_council
