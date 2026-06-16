import os
import re
import json
import time
import hashlib
import requests
import concurrent.futures
import logging
from logging.handlers import TimedRotatingFileHandler
from google import genai
from google.genai import types

# ---------------------------------------------------------------------------
# Rolling LLM Handshake Logger (5 Days Rolling)
# ---------------------------------------------------------------------------
os.makedirs("logs", exist_ok=True)
handshake_log_file = os.path.join("logs", "gem_handshakes.log")

# Configure a file handler rotating daily, keeping 5 backups
handshake_handler = TimedRotatingFileHandler(
    handshake_log_file,
    when="D",
    interval=1,
    backupCount=5,
    encoding="utf-8"
)
handshake_handler.setLevel(logging.INFO)
handshake_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handshake_handler.setFormatter(handshake_formatter)

llm_logger = logging.getLogger("gem_handshakes")
llm_logger.setLevel(logging.INFO)
# Clear existing handlers to prevent duplicate logs on reload
if llm_logger.hasHandlers():
    llm_logger.handlers.clear()
llm_logger.addHandler(handshake_handler)
# Prevent propagation to parent loggers to avoid flooding console
llm_logger.propagate = False

# ---------------------------------------------------------------------------
# Model definitions — v10.62-Scout-Limit-and-RSI-Filter
# Per terminal.md > Mode Selection Matrix (Canonical)

# ---------------------------------------------------------------------------
DEFAULT_MODEL_PRO = "gemini-3.5-flash"
DEFAULT_MODEL_FLASH = "gemini-3.5-flash"
DEFAULT_MODEL_GEMMA = "gemini-3.1-flash-lite"
DEFAULT_MODEL_THINKING = "gemini-3.5-flash"

MODEL_MAPPING = {
    "PRO":      ["gemini-3.5-flash"],
    "FLASH":    ["gemini-3.5-flash"],
    "GEMMA":    ["gemini-3.1-flash-lite"],
    "THINKING": ["gemini-3.5-flash"],
    "FAST":     ["gemini-3.1-flash-lite"],
}

CACHE_VERSION = "GEM_CACHE_v10.57-Editable-Scout-Prompt-Decoupling"



# ---------------------------------------------------------------------------
# Main framework
# ---------------------------------------------------------------------------
class AgentFramework:
    def __init__(self, log_callback=None):
        # Load optional local model config from config.json
        self._local_config = self._load_local_config()
        self.free_tier_only = self._local_config.get("FREE_TIER_ONLY", True)
        self.gemini_subscription_linked = self._local_config.get("GEMINI_SUBSCRIPTION_LINKED", False)
        
        if self.gemini_subscription_linked:
            self.free_tier_only = False
            
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
        
        # Telemetry
        self.session_cost = 0.0
        self.reset_turn_usage()

    def _resolve_orchestrator(self) -> str:
        """
        Dynamically probe available models, filter for 'antigravity',
        and return the latest version alphabetically.
        Falls back to 'gemini-3.5-flash' on failure or empty match.
        """
        try:
            available_models = []
            for m in self.client.models.list():
                name = getattr(m, "name", "").replace("models/", "")
                available_models.append(name)
            
            antigravity_models = [name for name in available_models if "antigravity" in name.lower()]
            if antigravity_models:
                antigravity_models.sort()
                resolved = antigravity_models[-1]
                self.log(f"[Dynamic Discovery] Resolved primary orchestrator: {resolved}")
                return resolved
        except Exception as e:
            self.log(f"[Dynamic Discovery Warning] Failed to dynamically list models: {e}")
        
        self.log("[Dynamic Discovery] Defaulting primary orchestrator to gemini-3.5-flash")
        return "gemini-3.5-flash"

    def _minify_payload(self, text: str) -> str:
        # Strip HTML/Markdown comments
        text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
        # Strip trailing spaces/tabs on multiline
        text = re.sub(r'[ \t]+$', '', text, flags=re.MULTILINE)
        # Collapse excessive blank lines (3 or more down to exactly 2)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text

    def _get_sys_instruction(self, base_instruction: str) -> str:
        """Appends the full rules and SSoT documentation to the system instruction."""
        parts = [base_instruction]
        rules_path = os.path.join("gem_trading_rules", "rules.md")
        if os.path.exists(rules_path):
            try:
                with open(rules_path, "r", encoding="utf-8") as f:
                    raw_rules = f.read()
                    parts.append(f"\n\n--- ATTACHED KNOWLEDGE BASE (GEM_Rules_Data) ---\n{self._minify_payload(raw_rules)}")
            except Exception as e:
                self.log(f"[Warning] Failed to read rules.md: {e}")
        
        lessons_path = os.path.join("context", "trade_lessons.json")
        if os.path.exists(lessons_path):
            try:
                with open(lessons_path, "r", encoding="utf-8") as f:
                    lessons_data = json.load(f)
                    lessons_str = json.dumps(lessons_data, indent=2)
                    parts.append(f"\n\n--- TRADE LESSONS REPOSITORY ---\n{self._minify_payload(lessons_str)}")
            except Exception as e:
                self.log(f"[Warning] Failed to read trade_lessons.json: {e}")
        return "".join(parts)

    def _log_handshake(self, direction, model_name, client_label, prompt, config, response=None, error=None, latency=None):
        """Record structured LLM interactions to gem_handshakes.log"""
        try:
            p_len = len(str(prompt))
            instruct_len = 0
            if config:
                if getattr(config, "system_instruction", None):
                    instruct_len = len(str(config.system_instruction))

            latency_str = f", Latency: {latency:.2f}s" if latency is not None else ""
            
            if direction == "REQUEST":
                msg = (
                    f"[{direction}] Model: {model_name} ({client_label}) | "
                    f"Prompt Len: {p_len}, Instruct Len: {instruct_len}"
                )
                llm_logger.info(msg)
            elif direction == "RESPONSE" and response:
                t_p, t_c = 0, 0
                if hasattr(response, 'usage_metadata') and response.usage_metadata:
                    t_p = response.usage_metadata.prompt_token_count or 0
                    t_c = response.usage_metadata.candidates_token_count or 0

                text_preview = "No text output"
                try:
                    if hasattr(response, "text") and response.text:
                        text_preview = response.text[:300].replace("\n", " ") + "..."
                    elif response.candidates and response.candidates[0].content.parts:
                        parts_text = "".join(part.text for part in response.candidates[0].content.parts if part.text)
                        if parts_text:
                            text_preview = parts_text[:300].replace("\n", " ") + "..."
                except Exception:
                    pass

                msg = (
                    f"[{direction}] Model: {model_name} ({client_label}) | Status: SUCCESS{latency_str} | "
                    f"Tokens: [Prompt: {t_p}, Candidate: {t_c}] | "
                    f"Preview: {text_preview}"
                )
                llm_logger.info(msg)
            elif direction == "WARNING":
                msg = f"[{direction}] Model: {model_name} ({client_label}) | Msg: {error}"
                llm_logger.warning(msg)
            elif direction == "ERROR":
                msg = f"[{direction}] Model: {model_name} ({client_label}) | Status: FAILED{latency_str} | Error: {error}"
                llm_logger.error(msg)
        except Exception as e:
            self.log(f"[Logging Error] Failed to write to handshake log: {e}")

    def reset_turn_usage(self):
        """Clear token counters for a new chat session turn."""
        self.turn_usage = {'prompt_tokens': 0, 'candidates_tokens': 0, 'estimated_cost': 0.0}

    def _calculate_call_cost(self, model_name, client_label, input_tokens, output_tokens) -> float:
        norm_model = model_name.replace("models/", "")
        if "antigravity" in norm_model.lower():
            return 0.0
        if norm_model == "gemini-3.5-flash":
            return (input_tokens / 1_000_000.0) * 1.50 + (output_tokens / 1_000_000.0) * 9.00
        if norm_model == "gemini-3.1-flash-lite":
            if "free" in client_label.lower():
                return 0.0
            return (input_tokens / 1_000_000.0) * 0.25 + (output_tokens / 1_000_000.0) * 1.50
        return 0.0

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
        """Return the list of models for a cloud mode."""
        return MODEL_MAPPING.get(mode, ["gemini-3.5-flash"])

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
    # Core generation method
    # -----------------------------------------------------------------------
    def generate_response_with_fallback(self, prompt, instruction, mode, tools=None):
        """Generate a response, routing to Gemini (cloud modes) with fallback and hybrid execution."""
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

            # Determine clients to try based on hybrid execution
            if model_name == "gemini-3.1-flash-lite":
                clients_to_try = [("Free-Tier Key", self.free_client)]
            else:
                clients_to_try = [("Primary Key", self.client)]

            for client_label, active_client in clients_to_try:
                # Check if we should use JIT caching
                cache_to_use = getattr(self, "active_cache_name", None)
                cache_target_model = getattr(self, "active_cache_target_model", None)
                
                if cache_to_use and cache_target_model and model_name == cache_target_model:
                    client_cache = cache_to_use
                    # Standardize system instruction payload without duplicating SSoT
                    unified_instruction = instruction if instruction else ""
                else:
                    client_cache = None
                    # Standardize system instruction payload by injecting rules/SSoT
                    unified_instruction = self._get_sys_instruction(instruction) if instruction else self._get_sys_instruction("")

                config = types.GenerateContentConfig(
                    system_instruction=unified_instruction,
                    temperature=1.0,
                    max_output_tokens=8192,
                    tools=final_tools if final_tools else None,
                    automatic_function_calling={"disable": True},
                    cached_content=client_cache, # Inject JIT cache name if applicable
                    safety_settings=[
                        types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH",       threshold="BLOCK_NONE"),
                        types.SafetySetting(category="HARM_CATEGORY_HARASSMENT",         threshold="BLOCK_NONE"),
                        types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT",  threshold="BLOCK_NONE"),
                        types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT",  threshold="BLOCK_NONE"),
                    ],
                )

                def attempt_call(client_instance, client_config, p_content):
                    if getattr(self, "cancel_check", None) and self.cancel_check():
                        self.log("[Framework] Cancel signal active before starting API call.")
                        raise RuntimeError("Operation cancelled by user.")
                    if final_tools:
                        chat = client_instance.chats.create(model=model_name, config=client_config)
                        res = chat.send_message(p_content)
                    else:
                        res = client_instance.models.generate_content(
                            model=model_name, contents=p_content, config=client_config
                        )
                    
                    if hasattr(res, 'usage_metadata') and res.usage_metadata:
                        p_tokens = res.usage_metadata.prompt_token_count or 0
                        c_tokens = res.usage_metadata.candidates_token_count or 0
                        self.turn_usage['prompt_tokens'] += p_tokens
                        self.turn_usage['candidates_tokens'] += c_tokens
                        call_cost = self._calculate_call_cost(model_name, client_label, p_tokens, c_tokens)
                        self.turn_usage['estimated_cost'] += call_cost
                        self.session_cost += call_cost
                    return res

                try:
                    self._log_handshake("REQUEST", model_name, client_label, prompt, config)
                    start_time = time.time()
                    res = attempt_call(active_client, config, prompt)
                    elapsed = time.time() - start_time
                    self._log_handshake("RESPONSE", model_name, client_label, prompt, config, response=res, latency=elapsed)
                    return res
                except Exception as e:
                    error_msg = str(e)
                    last_error = e
                    elapsed = time.time() - start_time
                    self._log_handshake("ERROR", model_name, client_label, prompt, config, error=error_msg, latency=elapsed)
                    self.log(f"[Warning] Call failed using {client_label} for model {model_name}: {error_msg}")

                    is_daily_limit = any(term in error_msg.lower() for term in ["daily", "limit exceeded", "exhausted", "free tier", "quota"])

                    if "429" in error_msg or "quota" in error_msg.lower():
                        if is_daily_limit:
                            self._log_handshake("WARNING", model_name, client_label, prompt, config, error=f"Daily limit reached. Instantly skipping: {error_msg}")
                            self.log(f"[System] Daily quota/limit hit for {model_name}. Skipping retry wait, instantly falling back to next available model...")
                            continue

                        import re
                        wait_time = 32
                        match = re.search(r"retry in (\d+(?:\.\d+)?)s", error_msg, re.IGNORECASE)
                        if match:
                            wait_time = int(float(match.group(1))) + 1
                            
                        self._log_handshake("WARNING", model_name, client_label, prompt, config, error=f"Rate limit hit. Waiting {wait_time}s to retry...")
                        self.log(f"[System] Rate limit hit for {model_name}. Waiting {wait_time}s...")
                        time.sleep(wait_time)
                        try:
                            self._log_handshake("REQUEST", model_name, client_label, prompt, config)
                            start_time = time.time()
                            res = attempt_call(active_client, config, prompt)
                            elapsed = time.time() - start_time
                            self._log_handshake("RESPONSE", model_name, client_label, prompt, config, response=res, latency=elapsed)
                            return res
                        except Exception as retry_e:
                            last_error = retry_e
                            elapsed = time.time() - start_time
                            self._log_handshake("ERROR", model_name, client_label, prompt, config, error=f"Retry attempt failed: {retry_e}", latency=elapsed)
                            continue

                    elif "503" in error_msg or "502" in error_msg or "unavailable" in error_msg.lower() or "bad gateway" in error_msg.lower():
                        self._log_handshake("WARNING", model_name, client_label, prompt, config, error=f"{error_msg}. Retrying in 5s...")
                        self.log(f"[System] {error_msg} for {model_name}. Retrying in 5s...")
                        time.sleep(5)
                        try:
                            self._log_handshake("REQUEST", model_name, client_label, prompt, config)
                            start_time = time.time()
                            res = attempt_call(active_client, config, prompt)
                            elapsed = time.time() - start_time
                            self._log_handshake("RESPONSE", model_name, client_label, prompt, config, response=res, latency=elapsed)
                            return res
                        except Exception as retry_e:
                            last_error = retry_e
                            elapsed = time.time() - start_time
                            self._log_handshake("ERROR", model_name, client_label, prompt, config, error=f"Retry after 502/503 failed: {retry_e}", latency=elapsed)
                            pass
                        continue

                    elif "not found" in error_msg.lower() or "404" in error_msg or "not supported" in error_msg.lower() or "interactions api" in error_msg.lower():
                        self._log_handshake("WARNING", model_name, client_label, prompt, config, error=f"Model not supported / returned 404: {error_msg}")
                        continue
                    else:
                        self.log(f"[System] Warning: {model_name} failed with error: {e}")
                        raise e

        raise RuntimeError(f"All models for mode {mode} failed. Last error: {last_error}")

    def load_temporal_anchor_prompt(self, ny_iso: str, current_year: int) -> str:
        """Loads the temporal anchor prompt from prompts/temporal_anchor_prompt.txt and templates the parameters."""
        prompt_path = "prompts/temporal_anchor_prompt.txt"
        template = ""
        if os.path.exists(prompt_path):
            try:
                with open(prompt_path, "r", encoding="utf-8") as f:
                    template = f.read().strip()
            except Exception: pass
        if not template:
            template = (
                "[SYSTEM_TIME (NEW YORK / ET): {ny_iso}]\n"
                "[PRODUCTION_ERA: {current_year}]\n"
                "[MANDATE]: The current year provided in the timestamp is the LIVE PRODUCTION ERA. "
                "Treat all data from this era as grounded reality, not a simulation."
            )
        return "\n\n" + template.format(ny_iso=ny_iso, current_year=current_year)

    def create_agent_tool(self, name, file_path, mode="PRO", agent_tools=None):
        instruction = self.load_system_instruction(file_path)

        import datetime
        # Force US/Eastern (New York) Time — UTC-4 (EDT)
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-4)))
        ny_iso = now.strftime("%Y-%m-%dT%H:%M:%S")
        current_year = now.year

        temporal_anchor = self.load_temporal_anchor_prompt(ny_iso, current_year)
        final_instruction = instruction + temporal_anchor

        def call_subagent(query: str) -> str:
            self.log(f"\n[Orchestrator -> {name}] Delegating: {query[:100]}...")
            
            # Programmatic payload slicing based on agent role
            sliced_query = query
            try:
                json_blocks = re.findall(r"```json\s*(.*?)\s*```", query, re.DOTALL | re.IGNORECASE)
                if not json_blocks:
                    json_blocks = re.findall(r"({[\s\S]*?})", query)
                
                for block in json_blocks:
                    try:
                        data = json.loads(block.strip())
                        if isinstance(data, dict) and ("tickers" in data or "ssot" in data):
                            sliced_data = {}
                            if "ssot" in data:
                                sliced_data["ssot"] = data["ssot"]
                            
                            if name == "GEX Engine":
                                if "tickers" in data:
                                    sliced_data["tickers"] = [
                                        {
                                            "ticker": t.get("ticker"),
                                            "price": t.get("price"),
                                            "dealer_posture": t.get("dealer_posture"),
                                            "net_gex_total": t.get("net_gex_total"),
                                            "gamma_flip_price": t.get("gamma_flip_price"),
                                            "atr_percent": t.get("atr_percent"),
                                        }
                                        for t in data["tickers"]
                                    ]
                            elif name in ["Macro Sentinel", "Sentiment Engine"]:
                                if "tickers" in data:
                                    sliced_data["tickers"] = [
                                        {
                                            "ticker": t.get("ticker"),
                                            "price": t.get("price"),
                                            "rsi": t.get("rsi"),
                                            "trend": t.get("trend"),
                                            "signal": t.get("signal"),
                                            "note": t.get("note"),
                                        }
                                        for t in data["tickers"]
                                    ]
                            elif name == "Technical Validator":
                                sliced_data["rules"] = "Strict JSON schema formatting rules: Output MUST be a single valid JSON block complying with the schema. No markdown formatting outside of JSON."
                                if "tickers" in data:
                                    sliced_data["tickers"] = [
                                        {
                                            "ticker": t.get("ticker"),
                                            "price": t.get("price"),
                                            "vwap": t.get("vwap"),
                                        }
                                        for t in data["tickers"]
                                    ]
                            else:
                                continue
                            
                            sliced_query = sliced_query.replace(block, json.dumps(sliced_data, indent=2))
                    except Exception:
                        pass
            except Exception as e:
                self.log(f"[Warning] Slicing failure: {e}")

            response = self.generate_response_with_fallback(
                prompt=f"[SYSTEM_TIME (NEW YORK / ET): {ny_iso}] {sliced_query}",
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

    def execute_ephemeral_batch(self, shared_ssot_base: str, parallel_tasks: dict) -> dict:
        """
        Executes a batch of parallel sub-agent tasks. If the shared_ssot_base exceeds
        32,768 tokens, it is cached dynamically using self.client.caches.create.
        Otherwise, tasks are executed in parallel without caching.
        """
        try:
            model = self._resolve_orchestrator()
            token_count = self.client.models.count_tokens(model=model, contents=shared_ssot_base).total_tokens
        except Exception as e:
            self.log(f"[Warning] Token count failed: {e}. Estimating via length.")
            token_count = len(shared_ssot_base) // 4
            
        use_cache = token_count > 32768
        
        if not use_cache:
            self.log(f"[Ephemeral Batch] Base size ({token_count} tokens) <= 32768. Running without cache.")
            results = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(parallel_tasks)) as executor:
                future_to_agent = {executor.submit(task): name for name, task in parallel_tasks.items()}
                for future in concurrent.futures.as_completed(future_to_agent):
                    agent_name = future_to_agent[future]
                    try:
                        results[agent_name] = future.result()
                    except Exception as exc:
                        results[agent_name] = f"Error: {exc}"
            return results
            
        cache_target_model = MODEL_MAPPING.get('PRO', [DEFAULT_MODEL_PRO])[0]
        self.active_cache_target_model = cache_target_model
        
        self.log(f"[Ephemeral Batch] Base size ({token_count} tokens) > 32768. Target Cache Model: {cache_target_model}. Initializing ephemeral JIT cache...")
        cache = None
        try:
            cache = self.client.caches.create(
                model=cache_target_model,
                config=types.CreateCachedContentConfig(
                    contents=[types.Content(role="user", parts=[types.Part(text=shared_ssot_base)])],
                    ttl="900s"
                )
            )
            self.active_cache_name = cache.name
            self.log(f"[Ephemeral Batch] Created ephemeral JIT cache: {cache.name}")
            
            results = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(parallel_tasks)) as executor:
                future_to_agent = {executor.submit(task): name for name, task in parallel_tasks.items()}
                for future in concurrent.futures.as_completed(future_to_agent):
                    agent_name = future_to_agent[future]
                    try:
                        results[agent_name] = future.result()
                    except Exception as exc:
                        results[agent_name] = f"Error: {exc}"
            return results
        finally:
            if cache:
                try:
                    self.log(f"[Ephemeral Batch] Deleting ephemeral JIT cache: {cache.name}")
                    self.client.caches.delete(name=cache.name)
                except Exception as e:
                    self.log(f"[Warning] Failed to delete JIT cache {cache.name}: {e}")
                finally:
                    self.active_cache_name = None
                    self.active_cache_target_model = None

    def create_parallel_council_tool(self, agents_dict):
        def ask_council(queries_json: str) -> str:
            """Dispatch multiple council agents in parallel for 3x speed increase.
            queries_json: A JSON string mapping agent names (e.g. 'bullish_advocate', 'red_team_pessimist') to their respective sub-query.
            """
            try:
                queries = json.loads(queries_json)
            except Exception as e:
                try:
                    import re
                    cleaned = queries_json.strip()
                    if cleaned.startswith("```json"):
                        cleaned = cleaned[7:]
                    elif cleaned.startswith("```"):
                        cleaned = cleaned[3:]
                    if cleaned.endswith("```"):
                        cleaned = cleaned[:-3]
                    cleaned = cleaned.strip()
                    
                    try:
                        queries = json.loads(cleaned)
                    except Exception:
                        pattern = re.compile(r'(\\(?:["\\/n]|u[0-9a-fA-F]{4}))|\\')
                        sanitized = pattern.sub(lambda m: m.group(1) if m.group(1) else r'\\', cleaned)
                        queries = json.loads(sanitized)
                except Exception as sanitize_err:
                    self.log(f"[Parallel Dispatcher Error] Failed to parse queries_json: {e} (Sanitization also failed: {sanitize_err})")
                    return json.dumps({"error": f"Failed to parse queries_json: {e} | Sanitization error: {sanitize_err}"})

            self.log(f"\n[Parallel Dispatcher] Initializing parallel session for {list(queries.keys())}...")
            results = {}
            parallel_tasks = {}
            for agent_name, query in queries.items():
                actual_tool_name = f"ask_{agent_name}"
                if actual_tool_name in agents_dict:
                    target_func = agents_dict[actual_tool_name]
                    parallel_tasks[agent_name] = lambda tf=target_func, q=query: tf(q)
                else:
                    results[agent_name] = f"Error: Agent '{agent_name}' not found."
            
            if parallel_tasks:
                shared_ssot_base = self._get_sys_instruction("")
                batch_results = self.execute_ephemeral_batch(shared_ssot_base, parallel_tasks)
                results.update(batch_results)
                
            self.log("[Parallel Dispatcher] All agents responded.")
            return json.dumps(results, indent=2)

        ask_council.__name__ = "ask_council"
        return ask_council
