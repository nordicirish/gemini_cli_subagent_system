import os
import re
import json
import time
import hashlib
import requests
import subprocess
from types import SimpleNamespace
import concurrent.futures
import logging
from logging.handlers import TimedRotatingFileHandler
# Lightweight standalone Part class for payload encapsulation
class Part:
    def __init__(self, text: str = None, function_response=None, inline_data=None):
        self.text = text
        self.function_response = function_response
        self.inline_data = inline_data

    @classmethod
    def from_text(cls, text: str):
        return cls(text=text)

    @classmethod
    def from_function_response(cls, name: str, response: dict):
        fn_resp = SimpleNamespace(name=name, response=response)
        return cls(function_response=fn_resp)

    @classmethod
    def from_bytes(cls, data: bytes, mime_type: str = "image/png"):
        return cls(inline_data=SimpleNamespace(data=data, mime_type=mime_type))

types = SimpleNamespace(Part=Part)

# ---------------------------------------------------------------------------
# Antigravity App Agent Runtime (Local agy CLI integration)
# ---------------------------------------------------------------------------
class AntigravityResponse:
    def __init__(self, text: str, prompt_tokens: int = 0, candidate_tokens: int = 0, model_name: str = "gemini-3.8-flash"):
        self.text = text
        self.function_calls = []
        part = SimpleNamespace(text=text)
        content = SimpleNamespace(parts=[part])
        candidate = SimpleNamespace(content=content, finish_reason="STOP", safety_ratings=[])
        self.candidates = [candidate]
        self.usage_metadata = SimpleNamespace(
            prompt_token_count=prompt_tokens,
            candidates_token_count=candidate_tokens,
            cached_content_token_count=0
        )
        self.model_name = model_name

    def __str__(self):
        return self.text


class AntigravityAgentRunner:
    def __init__(self, agy_path=None, cancel_check=None):
        if agy_path and os.path.exists(agy_path):
            self.agy_path = agy_path
        else:
            candidates = [
                os.path.expanduser(r"~\.gemini\bin\agy.exe"),
                r"C:\Users\roger\.gemini\bin\agy.exe",
            ]
            self.agy_path = next((c for c in candidates if os.path.exists(c)), "agy")
        self.cancel_check = cancel_check
        self.is_available = os.path.exists(self.agy_path) or bool(os.environ.get("AGY_PATH"))

    def run_prompt(self, prompt: str, system_instruction: str = "", mode: str = "FAST", tools=None, timeout: int = 120) -> AntigravityResponse:
        if self.cancel_check and self.cancel_check():
            raise RuntimeError("Operation cancelled by user.")

        # Map execution mode to Antigravity model tiers
        if mode in ("PRO", "THINKING"):
            model = "gemini-3.8-flash-high"
        else:
            model = "gemini-3.8-flash-low"

        # Output-formatting instruction (NOT a security control).
        # Actual execution restrictions are enforced by CLI flags (--sandbox,
        # --disable-slash-commands), headless permission auto-denial, and Python
        # ToolRegistry / authorize_financial_mutation boundaries.
        analytical_output_instruction = (
            "ANALYTICAL OUTPUT INSTRUCTION: You are an analytical financial intelligence engine. "
            "Do NOT attempt to execute host shell commands, local workspace edits, or terminal tasks. "
            "Output all analyses, reasoning, and JSON payloads directly as text."
        )

        full_prompt = f"{analytical_output_instruction}\n\nTask / User Prompt:\n{prompt}\n"
        if system_instruction:
            full_prompt = f"{analytical_output_instruction}\n\nSystem Instructions:\n{system_instruction}\n\nTask / User Prompt:\n{prompt}\n"

        cmd = [
            self.agy_path,
            "--sandbox",
            "--disable-slash-commands",
            "--model", model,
        ]

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8"
        )

        try:
            out, err = proc.communicate(input=full_prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise TimeoutError(f"Antigravity Agent timed out after {timeout}s.")

        if proc.returncode != 0:
            raise RuntimeError(f"Antigravity Agent failed (code {proc.returncode}): {err or out}")

        if "auto-denied" in (err or "").lower() and not out.strip():
            raise PermissionError(f"Antigravity Security Boundary: Subprocess attempted unauthorized host tool: {err.strip()}")

        output_text = out.strip()
        p_tokens = len(full_prompt) // 4
        c_tokens = len(output_text) // 4
        return AntigravityResponse(output_text, prompt_tokens=p_tokens, candidate_tokens=c_tokens, model_name=model)


MAX_SUBAGENT_TOOL_TURNS = 3


class AntigravityChatSession:
    """Session adapter allowing the Orchestrator and Subagents to run natively on Antigravity with bounded multi-turn tool dispatch."""
    def __init__(
        self,
        runner: AntigravityAgentRunner,
        sys_instruction: str,
        tools: list,
        mode: str = "PRO",
        generate_fn=None,
        is_subagent: bool = False,
    ):
        self.runner = runner
        self.sys_instruction = sys_instruction
        self.tools = tools or []
        self.tool_map = {getattr(t, "__name__", str(t)): t for t in self.tools}
        self.mode = mode
        self.generate_fn = generate_fn
        self.is_subagent = is_subagent
        self.history = []

    def get_history(self):
        return self.history

    def send_message(self, message):
        # Format available tools
        tool_desc = []
        for name, fn in self.tool_map.items():
            doc = getattr(fn, "__doc__", "") or f"Tool {name}"
            tool_desc.append(f"- {name}: {doc.strip()}")
        tools_block = "Available Tools:\n" + "\n".join(tool_desc) if tool_desc else ""

        # Format incoming user prompt or function returns
        if isinstance(message, list):
            res_parts = []
            for item in message:
                if hasattr(item, "function_response"):
                    res_parts.append(f"Tool Result [{item.function_response.name}]: {item.function_response.response}")
                elif hasattr(item, "text"):
                    res_parts.append(item.text)
                else:
                    res_parts.append(str(item))
            if self.is_subagent:
                user_input = (
                    "Sub-tool execution results:\n"
                    + "\n\n".join(res_parts)
                    + "\n\nPlease synthesize these results into your final analysis, or request additional tools if required."
                )
            else:
                user_input = (
                    "Tool execution results:\n"
                    + "\n\n".join(res_parts)
                    + "\n\nPlease synthesize these results into the final Markdown report and EXECUTION_PAYLOAD, or request additional tools."
                )
        else:
            user_input = str(message)

        closing_instruction = (
            "Otherwise, output your synthesized text analysis directly."
            if self.is_subagent
            else "Otherwise, output the complete single-pass Markdown analysis followed by the single valid ```json EXECUTION_PAYLOAD ``` block."
        )

        full_sys = (
            f"{self.sys_instruction}\n\n"
            f"{tools_block}\n\n"
            "If you need to query subagents or execute tools, output ONLY a JSON block specifying function calls:\n"
            "```json\n"
            "{\n"
            '  "function_calls": [\n'
            '    {"name": "tool_name", "args": {"query": "..."}}\n'
            "  ]\n"
            "}\n"
            "```\n"
            f"{closing_instruction}"
        )

        if self.generate_fn is not None:
            resp = self.generate_fn(user_input, full_sys, self.mode)
        else:
            resp = self.runner.run_prompt(user_input, system_instruction=full_sys, mode=self.mode)

        text = ""
        if hasattr(resp, "text") and resp.text:
            text = resp.text
        elif getattr(resp, "candidates", None):
            try:
                for part in resp.candidates[0].content.parts:
                    if getattr(part, "text", None):
                        text += part.text
            except Exception:
                pass
        if not hasattr(resp, "text") or not resp.text:
            resp.text = text
        if not hasattr(resp, "function_calls"):
            resp.function_calls = []

        # Detect tool call requests
        fc_match = re.search(r"```json\s*(\{[\s\S]*?\"function_calls\"[\s\S]*?\})\s*```", text, re.IGNORECASE)
        if fc_match:
            try:
                parsed = json.loads(fc_match.group(1))
                calls = []
                for fc in parsed.get("function_calls", []):
                    name = fc.get("name")
                    args = fc.get("args", {})
                    calls.append(SimpleNamespace(name=name, args=args))
                resp.function_calls = calls
            except Exception:
                pass

        self.history.append(SimpleNamespace(parts=[SimpleNamespace(text=text)]))
        return resp

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
# Model definitions — Antigravity Native Engine
# ---------------------------------------------------------------------------
DEFAULT_MODEL_PRO = "gemini-3.8-flash-high"
DEFAULT_MODEL_FLASH = "gemini-3.8-flash-low"
DEFAULT_MODEL_GEMMA = "gemini-3.8-flash-low"
DEFAULT_MODEL_THINKING = "gemini-3.8-flash-high"

MODEL_MAPPING = {
    "PRO":      ["gemini-3.8-flash-high", "gemini-4-argon-high"],
    "FLASH":    ["gemini-3.8-flash-low"],
    "GEMMA":    ["gemini-3.8-flash-low"],
    "THINKING": ["gemini-3.8-flash-high"],
    "FAST":     ["gemini-3.8-flash-low"],
}

CACHE_VERSION = "GEM_CACHE_v11.56-Pure-Antigravity-Sync"



# ---------------------------------------------------------------------------
# Main framework
# ---------------------------------------------------------------------------
class AgentFramework:
    def __init__(self, log_callback=None):
        self._local_config = self._load_local_config()
        self.log_callback = log_callback
        self.agents = {}
        
        # Telemetry
        self.session_cost = 0.0
        self.reset_turn_usage()

        # Pure Antigravity App Agent Runtime (Zero Cloud Dependency)
        self.backend = "antigravity"
        self.antigravity_runner = AntigravityAgentRunner(
            cancel_check=lambda: getattr(self, "cancel_check", lambda: False)()
        )
        if not self.antigravity_runner.is_available:
            raise RuntimeError(
                f"Antigravity CLI binary was not found at {self.antigravity_runner.agy_path}. "
                "Antigravity desktop environment is required."
            )

        self.log(f"[Framework] Pure Antigravity App Agent Engine active: {self.antigravity_runner.agy_path} (Cloud Gemini completely decoupled)")

    def create_chat_session(self, sys_instruction: str, tools: list, model_name: str = None):
        """Creates an AntigravityChatSession for the Master Orchestrator."""
        return AntigravityChatSession(self.antigravity_runner, sys_instruction, tools)

    def load_chart_parts(self) -> list:
        """Load latest 1-minute chart screenshots from context/charts/ as multimodal Part instances."""
        import glob
        chart_parts = []
        chart_dir = "context/charts"
        if os.path.exists(chart_dir):
            for filepath in sorted(glob.glob(os.path.join(chart_dir, "chart_*_1m.png"))):
                try:
                    with open(filepath, "rb") as f:
                        data = f.read()
                        if data:
                            chart_parts.append(
                                types.Part.from_bytes(data=data, mime_type="image/png")
                            )
                except Exception:
                    pass
        return chart_parts

    def _resolve_orchestrator(self) -> str:
        """Returns the primary Antigravity Orchestrator model."""
        return self._local_config.get("ANTIGRAVITY_MODEL_PRO", "gemini-3.8-flash-high")

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
        self.turn_usage = {'prompt_tokens': 0, 'candidates_tokens': 0, 'cached_tokens': 0, 'estimated_cost': 0.0}

    def _calculate_call_cost(self, model_name, client_label, input_tokens, output_tokens, cached_tokens=0) -> float:
        norm_model = model_name.replace("models/", "")
        if "antigravity" in norm_model.lower():
            return 0.0
        if norm_model == "gemini-3.5-flash":
            return (input_tokens / 1_000_000.0) * 1.50 + (cached_tokens / 1_000_000.0) * 0.375 + (output_tokens / 1_000_000.0) * 9.00
        if norm_model == "gemini-3.1-flash-lite":
            if "free" in client_label.lower():
                return 0.0
            return (input_tokens / 1_000_000.0) * 0.25 + (cached_tokens / 1_000_000.0) * 0.0625 + (output_tokens / 1_000_000.0) * 1.50
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
    # Core generation method — Pure Antigravity App Agent Engine
    # -----------------------------------------------------------------------
    def generate_response_with_fallback(self, prompt, instruction, mode, tools=None):
        """Generate a response using the local Antigravity App Agent runtime."""
        if getattr(self, "cancel_check", None) and self.cancel_check():
            self.log("[Framework] Cancel signal active before starting call.")
            raise RuntimeError("Operation cancelled by user.")

        target_model = "gemini-3.8-flash-high" if mode in ("PRO", "THINKING") else "gemini-3.8-flash-low"
        self.log(f"[Antigravity App Agent] Delegating turn with model: {target_model} (mode: {mode})...")
        start_time = time.time()
        try:
            self._log_handshake("REQUEST", target_model, "Antigravity Local", prompt, None)
            resp = self.antigravity_runner.run_prompt(prompt, system_instruction=instruction, mode=mode, tools=tools)
            elapsed = time.time() - start_time
            self._log_handshake("RESPONSE", target_model, "Antigravity Local", prompt, None, response=resp, latency=elapsed)

            # Zero marginal cost for local Antigravity sessions
            self.turn_usage['prompt_tokens'] += resp.usage_metadata.prompt_token_count
            self.turn_usage['candidates_tokens'] += resp.usage_metadata.candidates_token_count
            return resp
        except Exception as e:
            elapsed = time.time() - start_time
            self._log_handshake("ERROR", target_model, "Antigravity Local", prompt, None, error=str(e), latency=elapsed)
            self.log(f"[Antigravity Error] Agent call failed: {e}")
            raise e

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

            if agent_tools:
                from tool_dispatcher import GLOBAL_TOOL_DISPATCHER
                allowed_tool_names = {getattr(t, "__name__", str(t)) for t in agent_tools}
                sub_session = AntigravityChatSession(
                    runner=self.antigravity_runner,
                    sys_instruction=final_instruction,
                    tools=agent_tools,
                    mode=mode,
                    generate_fn=lambda p, sys_inst, m: self.generate_response_with_fallback(
                        prompt=p,
                        instruction=sys_inst,
                        mode=m,
                        tools=agent_tools,
                    ),
                    is_subagent=True,
                )
                dispatch_res = GLOBAL_TOOL_DISPATCHER.execute_multi_turn_session(
                    session=sub_session,
                    initial_message=f"[SYSTEM_TIME (NEW YORK / ET): {ny_iso}] {sliced_query}",
                    part_factory=types.Part,
                    agent_role=name,
                    cancel_check=lambda: getattr(self, "cancel_check", lambda: False)(),
                    log_callback=self.log,
                    allowed_tool_names=allowed_tool_names,
                    max_turns_override=MAX_SUBAGENT_TOOL_TURNS,
                )
                if dispatch_res.get("status") == "cancelled":
                    return dispatch_res.get("response", "[OFFLINE] Session terminated by user interrupt.")

                captured_text = dispatch_res.get("captured_text", [])
                non_fc_texts = []
                for txt in captured_text:
                    stripped = re.sub(
                        r"```json\s*(\{[\s\S]*?\"function_calls\"[\s\S]*?\})\s*```",
                        "",
                        txt,
                        flags=re.IGNORECASE,
                    ).strip()
                    if stripped:
                        non_fc_texts.append(stripped)

                if non_fc_texts:
                    return "\n\n---\n\n".join(non_fc_texts)
                return dispatch_res.get("response", "")

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
        Executes a batch of parallel sub-agent tasks concurrently via ThreadPoolExecutor on Antigravity.
        """
        self.log(f"[Antigravity Parallel Batch] Executing {len(parallel_tasks)} tasks concurrently via Antigravity Agents...")
        results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(parallel_tasks), 8)) as executor:
            future_to_agent = {executor.submit(task): name for name, task in parallel_tasks.items()}
            for future in concurrent.futures.as_completed(future_to_agent):
                agent_name = future_to_agent[future]
                try:
                    results[agent_name] = future.result()
                except Exception as exc:
                    results[agent_name] = f"Error: {exc}"
        return results

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
