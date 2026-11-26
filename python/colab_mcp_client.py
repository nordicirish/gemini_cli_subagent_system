"""
Dual-Mode Google Colab MCP Client & Zero-Secret Egress Guard (Stage M1).

Supports:
  - `stdio`: Google's official local `googlecolab/colab-mcp` bridge via `uvx`
  - `http`: Official hosted Google Colab remote MCP endpoint (`https://colaboratory.googleapis.com/mcp`)
  - `offline`: No remote MCP calls; offline JSON calibration import/export remains available

Security & Boundary Guarantees:
  - Loopback/SSRF host allowlist on Streamable HTTP endpoints (`ALLOWED_HTTP_MCP_HOSTS`).
  - Zero-secret egress sanitizer blocks cash balances, share counts, WAC, API keys, and tokens
    before any payload crosses to Colab.
  - Strict stdout marker parsing (`<<<GEM_COLAB_JSON_START>>>` .. `<<<GEM_COLAB_JSON_END>>>`).
  - Uses list-argv stdio subprocess parameters without shell interpolation.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
import math
import os
import re
import shutil
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

JSON_START_MARKER = "<<<GEM_COLAB_JSON_START>>>"
JSON_END_MARKER = "<<<GEM_COLAB_JSON_END>>>"

DEFAULT_STDIO_COMMAND = "uvx"
DEFAULT_STDIO_ARGS: Tuple[str, ...] = ("git+https://github.com/googlecolab/colab-mcp",)
DEFAULT_HTTP_URL = "https://colaboratory.googleapis.com/mcp"
DEFAULT_TIMEOUT_SEC = 600
MAX_EGRESS_PAYLOAD_BYTES = 512 * 1024

VALID_MCP_MODES = frozenset({"stdio", "http", "offline"})
ALLOWED_HTTP_MCP_HOSTS = frozenset({"colaboratory.googleapis.com"})

FORBIDDEN_EGRESS_KEYS = frozenset({
    "unallocated_cash_eur",
    "unallocated_cash_usd",
    "shares",
    "wac",
    "api_key",
    "token",
    "secret",
    "password",
    "credentials",
    "authorization",
    "cookie",
    "session_id",
    "account_id",
    "private_key",
})

_FORBIDDEN_KEY_SUBSTRINGS = (
    "api_key",
    "secret",
    "password",
    "token",
    "unallocated_cash",
    "credential",
    "authorization",
)
_TICKER_RE = re.compile(r"^[A-Z0-9.\-\^]{1,12}$")


def is_mcp_sdk_available() -> bool:
    """Return True if the official Python `mcp` SDK can be imported cleanly."""
    try:
        import mcp  # noqa: F401
        from mcp import ClientSession  # noqa: F401
        return True
    except ImportError:
        return False


def _validate_http_url(url: str, *, allow_loopback: bool = False) -> str:
    raw = str(url or "").strip()
    if not raw:
        raise ValueError("HTTP MCP URL cannot be empty.")
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    scheme = (parsed.scheme or "").lower()
    if host in ALLOWED_HTTP_MCP_HOSTS:
        if scheme != "https":
            raise ValueError(f"Hosted Colab MCP URL must use https://, got {scheme!r}.")
        return raw
    if allow_loopback and host in {"127.0.0.1", "localhost"} and scheme in {"http", "https"}:
        return raw
    raise ValueError(
        f"Blocked untrusted HTTP MCP host {host!r}; allowed hosts: {sorted(ALLOWED_HTTP_MCP_HOSTS)}."
    )


@dataclass(frozen=True)
class ColabMCPConfig:
    mode: str = "stdio"
    stdio_command: str = DEFAULT_STDIO_COMMAND
    stdio_args: Tuple[str, ...] = DEFAULT_STDIO_ARGS
    http_url: str = DEFAULT_HTTP_URL
    http_bearer_token: Optional[str] = None
    timeout_sec: int = DEFAULT_TIMEOUT_SEC
    allow_loopback_http: bool = False

    def __post_init__(self) -> None:
        norm_mode = str(self.mode or "stdio").strip().lower()
        if norm_mode not in VALID_MCP_MODES:
            raise ValueError(f"Invalid Colab MCP mode {self.mode!r}; expected one of {sorted(VALID_MCP_MODES)}.")
        object.__setattr__(self, "mode", norm_mode)

        timeout_val = max(15, min(3600, int(self.timeout_sec)))
        object.__setattr__(self, "timeout_sec", timeout_val)

        if self.http_url:
            _validate_http_url(self.http_url, allow_loopback=bool(self.allow_loopback_http))

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "ColabMCPConfig":
        source = os.environ if env is None else env
        mode = (source.get("COLAB_MCP_MODE") or "stdio").strip().lower()
        cmd = (source.get("COLAB_MCP_COMMAND") or DEFAULT_STDIO_COMMAND).strip() or DEFAULT_STDIO_COMMAND
        raw_args = source.get("COLAB_MCP_ARGS")
        if raw_args is not None and raw_args.strip():
            args = tuple(part for part in raw_args.strip().split() if part)
        else:
            args = DEFAULT_STDIO_ARGS
        http_url = (source.get("COLAB_MCP_URL") or DEFAULT_HTTP_URL).strip() or DEFAULT_HTTP_URL
        bearer = (source.get("COLAB_MCP_BEARER_TOKEN") or "").strip() or None
        raw_timeout = source.get("COLAB_MCP_TIMEOUT_SEC")
        try:
            timeout_sec = int(raw_timeout) if raw_timeout else DEFAULT_TIMEOUT_SEC
        except ValueError:
            timeout_sec = DEFAULT_TIMEOUT_SEC
        return cls(
            mode=mode,
            stdio_command=cmd,
            stdio_args=args,
            http_url=http_url,
            http_bearer_token=bearer,
            timeout_sec=timeout_sec,
        )


def _check_forbidden_keys_recursive(obj: Any, path: str = "$") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            key_low = str(k).strip().lower()
            if key_low in FORBIDDEN_EGRESS_KEYS or any(sub in key_low for sub in _FORBIDDEN_KEY_SUBSTRINGS):
                raise ValueError(f"Blocked forbidden financial/secret key {k!r} at {path} during Colab egress sanitization.")
            _check_forbidden_keys_recursive(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for idx, item in enumerate(obj):
            _check_forbidden_keys_recursive(item, f"{path}[{idx}]")


def sanitize_job_inputs(payload: Any) -> Dict[str, Any]:
    """
    Fail-closed egress sanitizer for payloads sent to remote Colab runtimes.
    Rejects cash balances, share counts, WACs, and secrets; normalizes tickers and weights.
    """
    if not isinstance(payload, dict):
        raise ValueError("Colab job inputs must be a JSON object.")

    _check_forbidden_keys_recursive(payload)

    clean: Dict[str, Any] = json.loads(json.dumps(payload, allow_nan=False))

    if "symbols" in clean:
        raw_syms = clean["symbols"]
        if not isinstance(raw_syms, list):
            raise ValueError("job_inputs.symbols must be a list of ticker symbols.")
        norm_syms: List[str] = []
        seen = set()
        for raw in raw_syms:
            sym = str(raw or "").strip().upper()
            if sym and _TICKER_RE.match(sym) and sym not in seen:
                seen.add(sym)
                norm_syms.append(sym)
        clean["symbols"] = norm_syms

    if "portfolio_weights_pct" in clean:
        raw_w = clean["portfolio_weights_pct"]
        if not isinstance(raw_w, dict):
            raise ValueError("job_inputs.portfolio_weights_pct must be a dictionary.")
        norm_w: Dict[str, float] = {}
        for k, v in raw_w.items():
            sym = str(k or "").strip().upper()
            if not _TICKER_RE.match(sym):
                raise ValueError(f"Invalid ticker symbol in portfolio_weights_pct: {k!r}")
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
                raise ValueError(f"Weight for {sym} must be a finite number.")
            f = float(v)
            if f < 0.0 or f > 100.0:
                raise ValueError(f"Weight for {sym} must be within [0, 100], got {f}.")
            norm_w[sym] = round(f, 4)
        clean["portfolio_weights_pct"] = norm_w

    encoded = json.dumps(clean, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_EGRESS_PAYLOAD_BYTES:
        raise ValueError(
            f"Sanitized Colab job payload exceeds max egress limit ({len(encoded)} > {MAX_EGRESS_PAYLOAD_BYTES} bytes)."
        )
    return clean


def extract_marked_json(stdout_text: str) -> Dict[str, Any]:
    """Extract and parse strict JSON wrapped in GEM Colab stdout markers."""
    text = str(stdout_text or "")
    start_idx = text.rfind(JSON_START_MARKER)
    if start_idx == -1:
        raise ValueError(f"Remote Colab output missing start marker {JSON_START_MARKER!r}.")
    body_start = start_idx + len(JSON_START_MARKER)
    end_idx = text.find(JSON_END_MARKER, body_start)
    if end_idx == -1:
        raise ValueError(f"Remote Colab output missing end marker {JSON_END_MARKER!r}.")
    raw_json = text[body_start:end_idx].strip()
    if not raw_json:
        raise ValueError("Marked Colab JSON payload is empty.")
    try:
        parsed = json.loads(raw_json)
    except ValueError as exc:
        raise ValueError(f"Failed to parse marked Colab JSON payload: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Marked Colab JSON payload must decode to a JSON object.")
    return parsed


def probe_transport(config: Optional[ColabMCPConfig] = None) -> Dict[str, Any]:
    """Inspect local MCP transport readiness without spawning remote runtimes or leaking tokens."""
    cfg = config or ColabMCPConfig.from_env()
    sdk_ok = is_mcp_sdk_available()
    cmd_name = cfg.stdio_command
    uvx_path = shutil.which(cmd_name) if cmd_name else None
    uvx_ok = bool(uvx_path)

    if cfg.mode == "offline":
        ready = True
        hint = "Offline JSON calibration import/export active; live MCP execution disabled."
    elif cfg.mode == "stdio":
        ready = bool(sdk_ok and uvx_ok)
        if not sdk_ok:
            hint = "Install the Python `mcp` SDK (`pip install mcp==2.3.0`)."
        elif not uvx_ok:
            hint = (
                "Install `uv` so `uvx` is on PATH (`winget install --id=astral-sh.uv -e` or `pip install uv`) "
                "to launch googlecolab/colab-mcp over stdio, or upload calibration JSON in offline mode."
            )
        else:
            hint = "Ready to bridge to Google Colab via local googlecolab/colab-mcp stdio server."
    else:  # http
        has_token = bool(cfg.http_bearer_token)
        ready = bool(sdk_ok and has_token)
        if not sdk_ok:
            hint = "Install the Python `mcp` SDK (`pip install mcp==2.3.0`)."
        elif not has_token:
            hint = "Set COLAB_MCP_BEARER_TOKEN with a valid Colaboratory OAuth bearer token for hosted MCP."
        else:
            hint = "Ready to connect to hosted Colab Streamable HTTP MCP endpoint."

    return {
        "mode": cfg.mode,
        "ready": ready,
        "mcp_sdk_available": sdk_ok,
        "uvx_available": uvx_ok,
        "uvx_path": uvx_path,
        "stdio_command": cfg.stdio_command,
        "http_url": cfg.http_url,
        "http_token_configured": bool(cfg.http_bearer_token),
        "timeout_sec": cfg.timeout_sec,
        "setup_hint": hint,
    }


def _extract_tool_text(result: Any) -> str:
    if getattr(result, "isError", False):
        chunks = []
        for item in getattr(result, "content", None) or ():
            txt = getattr(item, "text", None)
            if txt is None and isinstance(item, dict):
                txt = item.get("text")
            if txt:
                chunks.append(str(txt))
        detail = "\n".join(chunks).strip() or "Unknown MCP tool error"
        raise RuntimeError(f"Colab MCP tool returned an error: {detail}")

    parts: List[str] = []
    for item in getattr(result, "content", None) or ():
        txt = getattr(item, "text", None)
        if txt is None and isinstance(item, dict):
            txt = item.get("text")
        if txt is not None:
            parts.append(str(txt))
    structured = getattr(result, "structuredContent", None)
    if structured is not None and not parts:
        parts.append(json.dumps(structured))
    return "\n".join(parts)


def _match_arg_name(props: Dict[str, Any], candidates: Iterable[str], fallback: Optional[str] = None) -> Optional[str]:
    lower_map = {k.lower(): k for k in props.keys()}
    for cand in candidates:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    return fallback


def _extract_identifier(text: str, keys: Iterable[str]) -> Optional[str]:
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            for k in keys:
                if obj.get(k):
                    return str(obj[k])
    except Exception:
        pass
    for k in keys:
        m = re.search(rf'"{re.escape(k)}"\s*:\s*"([^"]+)"', text)
        if m:
            return m.group(1)
    return None


class ColabMCPClient:
    """Async/sync MCP client targeting googlecolab/colab-mcp (stdio) or hosted Colab MCP (Streamable HTTP)."""

    def __init__(
        self,
        config: Optional[ColabMCPConfig] = None,
        session_factory: Optional[Callable[[ColabMCPConfig], Any]] = None,
    ) -> None:
        self.config = config or ColabMCPConfig.from_env()
        self._session_factory = session_factory

    @asynccontextmanager
    async def _open_session(self):
        if self._session_factory is not None:
            async with self._session_factory(self.config) as session:
                yield session
            return

        if self.config.mode == "offline":
            raise RuntimeError("ColabMCPClient cannot execute remote code while mode='offline'.")

        if not is_mcp_sdk_available():
            raise RuntimeError("Official `mcp` Python SDK is not installed.")

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        if self.config.mode == "stdio":
            if not shutil.which(self.config.stdio_command):
                raise RuntimeError(
                    f"Required stdio bridge command {self.config.stdio_command!r} was not found on PATH."
                )
            server_params = StdioServerParameters(
                command=self.config.stdio_command,
                args=list(self.config.stdio_args),
            )
            async with stdio_client(server_params) as streams:
                read_stream, write_stream = streams[0], streams[1]
                async with ClientSession(read_stream, write_stream) as session:
                    yield session
            return

        # Hosted / Streamable HTTP mode
        from mcp.client.streamable_http import streamable_http_client
        import httpx2

        url = _validate_http_url(self.config.http_url, allow_loopback=self.config.allow_loopback_http)
        headers: Dict[str, str] = {}
        if self.config.http_bearer_token:
            headers["Authorization"] = f"Bearer {self.config.http_bearer_token}"
        async with httpx2.AsyncClient(headers=headers, timeout=float(self.config.timeout_sec)) as http_client:
            async with streamable_http_client(url, http_client=http_client) as streams:
                read_stream, write_stream = streams[0], streams[1]
                async with ClientSession(read_stream, write_stream) as session:
                    yield session

    @staticmethod
    def _wrap_script(script_source: str, sanitized_inputs: Dict[str, Any]) -> str:
        inputs_literal = json.dumps(sanitized_inputs, separators=(",", ":"), ensure_ascii=True)
        preamble = (
            "import json as _gem_json\n"
            f"GEM_JOB_INPUTS = _gem_json.loads({inputs_literal!r})\n"
        )
        return preamble + "\n" + str(script_source or "").lstrip()

    async def _run_on_session(
        self,
        session: Any,
        wrapped_code: str,
        *,
        accelerator: str = "CPU",
    ) -> str:
        await session.initialize()
        listed = await session.list_tools()
        tools = list(getattr(listed, "tools", None) or ())
        tool_by_name = {getattr(t, "name", ""): t for t in tools if getattr(t, "name", None)}
        discovered_names = sorted(tool_by_name.keys())

        runtime_id: Optional[str] = None
        session_id: Optional[str] = None

        # Step 1 (optional hosted runtime/session provisioning if exposed)
        for rt_tool_name in ("create_runtime", "get_or_create_runtime"):
            if rt_tool_name in tool_by_name:
                props = getattr(tool_by_name[rt_tool_name], "inputSchema", None) or {}
                prop_dict = props.get("properties", {}) if isinstance(props, dict) else {}
                rt_args: Dict[str, Any] = {}
                acc_key = _match_arg_name(prop_dict, ("accelerator", "accelerator_type", "runtime_type"))
                if acc_key and accelerator:
                    rt_args[acc_key] = accelerator
                rt_res = await session.call_tool(rt_tool_name, rt_args)
                rt_txt = _extract_tool_text(rt_res)
                runtime_id = _extract_identifier(rt_txt, ("runtime_id", "runtimeId", "id", "name"))
                break

        if "create_session" in tool_by_name:
            props = getattr(tool_by_name["create_session"], "inputSchema", None) or {}
            prop_dict = props.get("properties", {}) if isinstance(props, dict) else {}
            sess_args: Dict[str, Any] = {}
            rt_key = _match_arg_name(prop_dict, ("runtime_id", "runtimeId", "runtime_name"))
            if rt_key and runtime_id:
                sess_args[rt_key] = runtime_id
            sess_res = await session.call_tool("create_session", sess_args)
            sess_txt = _extract_tool_text(sess_res)
            session_id = _extract_identifier(sess_txt, ("session_id", "sessionId", "id", "name"))

        # Step 2: Execute wrapped Python code via the first matching execution tool
        exec_candidates = ("execute_code", "run_code", "execute_cell", "run_cell", "execute_python")
        chosen_name = next((name for name in exec_candidates if name in tool_by_name), None)
        if chosen_name is None:
            raise RuntimeError(
                f"Colab MCP server did not expose a supported code-execution tool; discovered tools: {discovered_names}"
            )

        schema = getattr(tool_by_name[chosen_name], "inputSchema", None) or {}
        prop_dict = schema.get("properties", {}) if isinstance(schema, dict) else {}
        code_key = _match_arg_name(prop_dict, ("code", "source", "script", "python_code", "cell_source"), fallback="code")
        exec_args: Dict[str, Any] = {code_key: wrapped_code}

        rt_key = _match_arg_name(prop_dict, ("runtime_id", "runtimeId"))
        if rt_key and runtime_id:
            exec_args[rt_key] = runtime_id
        sess_key = _match_arg_name(prop_dict, ("session_id", "sessionId"))
        if sess_key and session_id:
            exec_args[sess_key] = session_id

        exec_res = await session.call_tool(chosen_name, exec_args)
        return _extract_tool_text(exec_res)

    async def execute_python_script(
        self,
        script_source: str,
        job_inputs: Dict[str, Any],
        *,
        accelerator: str = "CPU",
    ) -> Dict[str, Any]:
        """Sanitizes inputs, executes script on remote Colab MCP runtime under timeout, and parses marked JSON."""
        sanitized = sanitize_job_inputs(job_inputs)
        wrapped = self._wrap_script(script_source, sanitized)

        async def _inner() -> Dict[str, Any]:
            async with self._open_session() as session:
                stdout_text = await self._run_on_session(session, wrapped, accelerator=accelerator)
                return extract_marked_json(stdout_text)

        return await asyncio.wait_for(_inner(), timeout=float(self.config.timeout_sec))

    def execute_python_script_sync(
        self,
        script_source: str,
        job_inputs: Dict[str, Any],
        *,
        accelerator: str = "CPU",
    ) -> Dict[str, Any]:
        """Blocking wrapper for daemon worker threads."""
        return asyncio.run(
            self.execute_python_script(script_source, job_inputs, accelerator=accelerator)
        )
