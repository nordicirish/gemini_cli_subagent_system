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

import ast
import asyncio
import atexit
import concurrent.futures
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

try:
    from audit_logger import redact_secrets
except ImportError:
    try:
        from python.audit_logger import redact_secrets
    except ImportError:
        def redact_secrets(text: str) -> str:  # type: ignore[misc]
            return re.sub(r"(?i)(bearer\s+|token[=:]\s*|api_key[=:]\s*)[A-Za-z0-9._\-]{8,}", r"\1[REDACTED]", str(text or ""))

JSON_START_MARKER = "<<<GEM_COLAB_JSON_START>>>"
JSON_END_MARKER = "<<<GEM_COLAB_JSON_END>>>"

DEFAULT_STDIO_COMMAND = "uvx"
DEFAULT_STDIO_ARGS: Tuple[str, ...] = ("git+https://github.com/googlecolab/colab-mcp",)
DEFAULT_HTTP_URL = "https://colaboratory.googleapis.com/mcp"
DEFAULT_TIMEOUT_SEC = 600
MAX_EGRESS_PAYLOAD_BYTES = 512 * 1024

VALID_MCP_MODES = frozenset({"cli", "stdio", "http", "offline"})
ALLOWED_HTTP_MCP_HOSTS = frozenset({"colaboratory.googleapis.com"})
ALLOWED_CLI_GPU_ACCELERATORS = frozenset({"T4", "L4"})
VALID_CLI_AUTH_STRATEGIES = frozenset({"oauth2", "adc"})


class ColabHandshakeTimeoutError(RuntimeError):
    """Raised when `googlecolab/colab-mcp` times out waiting for the 60s browser link."""

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


def find_colab_cli(
    explicit_path: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    *,
    allow_host_fallback: Optional[bool] = None,
) -> Optional[str]:
    """Locate the `google-colab-cli` (`colab`) executable cleanly without requiring PATH edits."""
    source = os.environ if env is None else env
    path_env = source.get("PATH")
    use_host_fallback = (env is None) if allow_host_fallback is None else bool(allow_host_fallback)

    cand = str(explicit_path if explicit_path is not None else (source.get("COLAB_CLI_PATH") or "")).strip().strip('"')
    if cand:
        if os.path.isfile(cand):
            return os.path.abspath(cand)
        return shutil.which(cand, path=path_env)

    on_path = shutil.which("colab", path=path_env)
    if on_path:
        return on_path

    if not use_host_fallback:
        return None

    candidates: List[str] = []
    if os.name == "nt":
        appdata = source.get("APPDATA") or os.environ.get("APPDATA") or ""
        localappdata = source.get("LOCALAPPDATA") or os.environ.get("LOCALAPPDATA") or ""
        userprofile = source.get("USERPROFILE") or os.environ.get("USERPROFILE") or os.path.expanduser("~")
        if appdata:
            candidates.extend(sorted(glob.glob(os.path.join(appdata, "Python", "Python*", "Scripts", "colab.exe")), reverse=True))
        if userprofile:
            candidates.append(os.path.join(userprofile, ".local", "bin", "colab.exe"))
        if localappdata:
            candidates.extend(sorted(glob.glob(os.path.join(localappdata, "Programs", "Python", "Python*", "Scripts", "colab.exe")), reverse=True))
    else:
        candidates.append(os.path.expanduser("~/.local/bin/colab"))

    for path in candidates:
        if path and os.path.isfile(path):
            return os.path.abspath(path)
    return None


@dataclass(frozen=True)
class ColabMCPConfig:
    mode: str = "stdio"
    stdio_command: str = DEFAULT_STDIO_COMMAND
    stdio_args: Tuple[str, ...] = DEFAULT_STDIO_ARGS
    http_url: str = DEFAULT_HTTP_URL
    http_bearer_token: Optional[str] = None
    timeout_sec: int = DEFAULT_TIMEOUT_SEC
    allow_loopback_http: bool = False
    cli_path: Optional[str] = None
    cli_auth: Optional[str] = None

    def __post_init__(self) -> None:
        norm_mode = str(self.mode or "stdio").strip().lower()
        if norm_mode not in VALID_MCP_MODES:
            raise ValueError(f"Invalid Colab MCP mode {self.mode!r}; expected one of {sorted(VALID_MCP_MODES)}.")
        object.__setattr__(self, "mode", norm_mode)

        timeout_val = max(15, min(3600, int(self.timeout_sec)))
        object.__setattr__(self, "timeout_sec", timeout_val)

        if self.http_url:
            _validate_http_url(self.http_url, allow_loopback=bool(self.allow_loopback_http))

        norm_cli_path = (str(self.cli_path).strip() or None) if self.cli_path else None
        object.__setattr__(self, "cli_path", norm_cli_path)

        norm_auth = (str(self.cli_auth).strip().lower() or None) if self.cli_auth else None
        if norm_auth is not None and norm_auth not in VALID_CLI_AUTH_STRATEGIES:
            raise ValueError(
                f"Invalid COLAB_CLI_AUTH {self.cli_auth!r}; expected one of {sorted(VALID_CLI_AUTH_STRATEGIES)}."
            )
        object.__setattr__(self, "cli_auth", norm_auth)

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "ColabMCPConfig":
        source = os.environ if env is None else env
        cli_path = (source.get("COLAB_CLI_PATH") or "").strip() or None
        cli_auth = (source.get("COLAB_CLI_AUTH") or "").strip() or None
        raw_mode = (source.get("COLAB_MCP_MODE") or "").strip().lower()
        if raw_mode:
            mode = raw_mode
        else:
            mode = "cli" if find_colab_cli(cli_path, env=env) else "stdio"
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
            cli_path=cli_path,
            cli_auth=cli_auth,
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
    """Inspect local Colab transport readiness without spawning remote runtimes or leaking tokens."""
    cfg = config or ColabMCPConfig.from_env()
    sdk_ok = is_mcp_sdk_available()
    cmd_name = cfg.stdio_command
    uvx_path = shutil.which(cmd_name) if cmd_name else None
    uvx_ok = bool(uvx_path)
    cli_path = find_colab_cli(cfg.cli_path)
    cli_ok = bool(cli_path)

    if cfg.mode == "cli":
        ready = cli_ok
        if ready:
            hint = "Ready to run headless on Google Colab via `google-colab-cli` (no browser tab required)."
        else:
            hint = (
                "Install `google-colab-cli` (`pip install google-colab-cli`), authorize once with `colab sessions`, "
                "or set COLAB_CLI_PATH."
            )
    elif cfg.mode == "offline":
        ready = True
        hint = "Offline JSON calibration import/export active; live execution disabled."
    elif cfg.mode == "stdio":
        ready = bool(sdk_ok and uvx_ok)
        if not sdk_ok:
            hint = "Install the Python `mcp` SDK (`pip install mcp==2.3.0`)."
        elif not uvx_ok:
            hint = (
                "Install `uv` so `uvx` is on PATH (`winget install --id=astral-sh.uv -e` or `pip install uv`) "
                "to launch googlecolab/colab-mcp over stdio, or install google-colab-cli for headless mode."
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
        "cli_available": cli_ok,
        "cli_path": cli_path,
        "mcp_sdk_available": sdk_ok,
        "uvx_available": uvx_ok,
        "uvx_path": uvx_path,
        "stdio_command": cfg.stdio_command,
        "http_url": cfg.http_url,
        "http_token_configured": bool(cfg.http_bearer_token),
        "timeout_sec": cfg.timeout_sec,
        "setup_hint": hint,
    }


EXEC_TOOL_CANDIDATES: Tuple[str, ...] = (
    "execute_code",
    "run_code",
    "execute_cell",
    "run_cell",
    "execute_python",
)


def unwrap_exception_message(exc: BaseException) -> str:
    """Recursively unwrap ExceptionGroup / BaseExceptionGroup into a readable root-cause string."""
    sub_excs = getattr(exc, "exceptions", None)
    if sub_excs:
        parts: List[str] = []
        for sub in sub_excs:
            msg = unwrap_exception_message(sub)
            if msg and msg not in parts:
                parts.append(msg)
        if parts:
            return "; ".join(parts)
    text = str(exc).strip()
    cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    if (not text or "TaskGroup" in text) and cause is not None and cause is not exc:
        inner = unwrap_exception_message(cause)
        if inner:
            return inner
    return text or exc.__class__.__name__


def _tool_input_schema(tool: Any) -> Dict[str, Any]:
    """Read tool JSON schema across mcp==2.3.0 (`input_schema`) and camelCase (`inputSchema`) objects."""
    raw = getattr(tool, "input_schema", None)
    if isinstance(raw, dict):
        return raw
    raw_camel = getattr(tool, "inputSchema", None)
    return raw_camel if isinstance(raw_camel, dict) else {}


def _is_valid_colab_connect_url(url: str) -> bool:
    raw = str(url or "").strip()
    if not raw or any(ch.isspace() for ch in raw):
        return False
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    return parsed.scheme.lower() == "https" and host in {"colab.research.google.com", "colab.google.com"}


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
        self._connect_url_file: Optional[str] = None
        self._browser_linked: bool = False
        self._session_broken: bool = False

    def _use_persistent_session(self) -> bool:
        """Reuse one long-lived stdio session (one browser link per server run) unless disabled or injected."""
        return (
            self._session_factory is None
            and self.config.mode == "stdio"
            and is_persistent_session_enabled()
        )

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
        from mcp.client.stdio import get_default_environment, stdio_client

        if self.config.mode == "stdio":
            if not shutil.which(self.config.stdio_command):
                raise RuntimeError(
                    f"Required stdio bridge command {self.config.stdio_command!r} was not found on PATH."
                )
            fd, url_tmp_path = tempfile.mkstemp(prefix="gem-colab-url-", suffix=".txt")
            os.close(fd)
            self._connect_url_file = url_tmp_path
            try:
                hook_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "colab_hook")
                stdio_env = dict(get_default_environment())
                if os.environ.get("PATH"):
                    stdio_env["PATH"] = os.environ["PATH"]
                stdio_env["GEM_COLAB_CONNECT_URL_FILE"] = url_tmp_path
                if os.path.isdir(hook_dir):
                    existing_py_path = stdio_env.get("PYTHONPATH", "").strip()
                    stdio_env["PYTHONPATH"] = f"{hook_dir}{os.pathsep}{existing_py_path}" if existing_py_path else hook_dir
                server_params = StdioServerParameters(
                    command=self.config.stdio_command,
                    args=list(self.config.stdio_args),
                    env=stdio_env,
                )
                async with stdio_client(server_params) as streams:
                    read_stream, write_stream = streams[0], streams[1]
                    async with ClientSession(read_stream, write_stream) as session:
                        yield session
            finally:
                self._connect_url_file = None
                try:
                    if os.path.exists(url_tmp_path):
                        os.unlink(url_tmp_path)
                except OSError:
                    pass
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
            f"GEM_JOB_INPUTS = _gem_json.loads({inputs_literal!r})\n\n"
        )
        src = str(script_source or "").lstrip()
        if not src:
            return preamble
        insert_after_line = 0
        try:
            tree = ast.parse(src)
            for idx, node in enumerate(tree.body):
                if (
                    idx == 0
                    and isinstance(node, ast.Expr)
                    and isinstance(getattr(node, "value", None), ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    insert_after_line = getattr(node, "end_lineno", node.lineno)
                    continue
                if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                    insert_after_line = getattr(node, "end_lineno", node.lineno)
                    continue
                break
        except SyntaxError:
            insert_after_line = 0

        if insert_after_line <= 0:
            return preamble + src
        lines = src.splitlines(keepends=True)
        head = "".join(lines[:insert_after_line])
        tail = "".join(lines[insert_after_line:])
        if head and not head.endswith("\n"):
            head += "\n"
        return f"{head}\n{preamble}{tail}"

    @staticmethod
    def _normalize_cli_gpu(accelerator: str) -> Optional[str]:
        acc = str(accelerator or "").strip().upper()
        if not acc or acc == "CPU":
            return None
        if acc in ALLOWED_CLI_GPU_ACCELERATORS:
            return acc
        raise ValueError(
            f"Unsupported Colab CLI accelerator {accelerator!r}; allowed: CPU, {sorted(ALLOWED_CLI_GPU_ACCELERATORS)}."
        )

    def _execute_via_cli(
        self,
        wrapped_code: str,
        *,
        accelerator: str = "CPU",
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> str:
        cli_exe = find_colab_cli(self.config.cli_path)
        if not cli_exe:
            raise RuntimeError(
                "Google Colab CLI (`colab`) was not found. Install `google-colab-cli`, authorize with `colab sessions`, "
                "or set COLAB_CLI_PATH."
            )
        gpu = self._normalize_cli_gpu(accelerator)
        timeout_sec = int(self.config.timeout_sec)

        with tempfile.TemporaryDirectory(prefix="gem-colab-cli-") as td:
            script_path = os.path.join(td, "worker.py")
            cfg_path = os.path.join(td, "sessions.json")
            with open(script_path, "w", encoding="utf-8") as fh:
                fh.write(wrapped_code)

            cmd: List[str] = [cli_exe, "--config", cfg_path]
            if self.config.cli_auth:
                cmd.extend(["--auth", self.config.cli_auth])
            cmd.extend(["run", "--timeout", str(timeout_sec)])
            if gpu:
                cmd.extend(["--gpu", gpu])
            cmd.append(script_path)

            if status_callback:
                status_callback(f"Running on Google Colab CLI ({gpu or 'CPU'} VM)...")

            kwargs: Dict[str, Any] = {
                "capture_output": True,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "timeout": float(timeout_sec) + 45.0,
            }
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

            try:
                proc = subprocess.run(cmd, **kwargs)
            except subprocess.TimeoutExpired as exc:
                raise asyncio.TimeoutError(
                    f"Colab CLI job timed out after {timeout_sec}s; remote VM was released."
                ) from exc

            stdout_txt = str(proc.stdout or "")
            stderr_txt = str(proc.stderr or "")
            if proc.returncode != 0:
                raw_err = (stderr_txt.strip() or stdout_txt.strip() or f"exit code {proc.returncode}")
                tail = redact_secrets(raw_err[-600:])
                low = tail.lower()
                if any(tok in low for tok in ("401", "403", "credential", "oauth", "login", "unauthenticated")):
                    tail += " Run 'colab sessions' in your terminal to refresh Google Colab CLI authorization."
                raise RuntimeError(f"Colab CLI execution failed: {tail}")
            return stdout_txt

    def _run_cli_batch_sync(
        self,
        prepared: List[Tuple[str, str, str]],
        progress_callback: Optional[Callable[[str, str, str], None]],
    ) -> Dict[str, Dict[str, Any]]:
        results: Dict[str, Dict[str, Any]] = {}
        abort_auth_msg: Optional[str] = None
        for job_type, wrapped, accelerator in prepared:
            if abort_auth_msg is not None:
                results[job_type] = {"ok": False, "error": abort_auth_msg}
                continue
            vm_kind = self._normalize_cli_gpu(accelerator) or "CPU"
            try:
                if progress_callback:
                    progress_callback(job_type, "running", f"Running {job_type} on Google Colab CLI ({vm_kind} VM)...")
                stdout_text = self._execute_via_cli(
                    wrapped,
                    accelerator=accelerator,
                    status_callback=(lambda msg, _jt=job_type: progress_callback(_jt, "handshake", msg)) if progress_callback else None,
                )
                parsed = extract_marked_json(stdout_text)
                results[job_type] = {"ok": True, "payload": parsed}
                if progress_callback:
                    progress_callback(job_type, "succeeded", f"Completed {job_type}")
            except BaseException as exc:
                err_msg = unwrap_exception_message(exc)
                results[job_type] = {"ok": False, "error": err_msg}
                if progress_callback:
                    progress_callback(job_type, "failed", err_msg)
                low = err_msg.lower()
                if "run 'colab sessions'" in low or "was not found" in low:
                    abort_auth_msg = err_msg
        return results

    async def _watch_connect_url_file(
        self,
        url_file: Optional[str],
        connect_url_callback: Optional[Callable[[Optional[str]], None]],
        status_callback: Optional[Callable[[str], None]],
    ) -> None:
        if not url_file or not connect_url_callback:
            return
        last_sent: Optional[str] = None
        while True:
            try:
                if os.path.isfile(url_file):
                    with open(url_file, "r", encoding="utf-8") as fh:
                        candidate = fh.read().strip()
                    if candidate and candidate != last_sent and _is_valid_colab_connect_url(candidate):
                        last_sent = candidate
                        connect_url_callback(candidate)
                        if status_callback:
                            status_callback("Click 'Open Google Colab to Connect' in the banner to link your browser tab (up to 60s)...")
            except OSError:
                pass
            await asyncio.sleep(0.2)

    async def _run_on_session(
        self,
        session: Any,
        wrapped_code: str,
        *,
        accelerator: str = "CPU",
        initialized: bool = False,
        status_callback: Optional[Callable[[str], None]] = None,
        connect_url_callback: Optional[Callable[[Optional[str]], None]] = None,
    ) -> str:
        if not initialized:
            await session.initialize()
        listed = await session.list_tools()
        tools = list(getattr(listed, "tools", None) or ())
        tool_by_name = {getattr(t, "name", ""): t for t in tools if getattr(t, "name", None)}
        discovered_names = sorted(tool_by_name.keys())

        chosen_name = next((name for name in EXEC_TOOL_CANDIDATES if name in tool_by_name), None)
        if chosen_name is None and "open_colab_browser_connection" in tool_by_name:
            if status_callback:
                status_callback("Opening Google Colab in your browser — click Connect in the banner if a tab didn't pop up (up to 60s)...")
            watcher_task = asyncio.create_task(
                self._watch_connect_url_file(self._connect_url_file, connect_url_callback, status_callback)
            )
            try:
                conn_res = await session.call_tool("open_colab_browser_connection", {})
            finally:
                watcher_task.cancel()
                try:
                    await watcher_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            conn_txt = _extract_tool_text(conn_res).strip().lower()
            sc = getattr(conn_res, "structuredContent", None) or getattr(conn_res, "structured_content", None)
            sc_result = sc.get("result") if isinstance(sc, dict) else None
            if _is_valid_colab_connect_url(conn_txt) and connect_url_callback:
                connect_url_callback(conn_txt)
            connected = (sc_result is True) or (conn_txt in {"true", "connected", "ok"})
            if sc_result is False or (not connected and "false" in conn_txt):
                if connect_url_callback:
                    connect_url_callback(None)
                self._session_broken = True
                raise ColabHandshakeTimeoutError(
                    "Google Colab browser session did not connect within 60s. "
                    "Click 'Open Google Colab to Connect' (or sign into Google Colab in the opened tab) and click Retry."
                )
            if connect_url_callback:
                connect_url_callback(None)
            listed = await session.list_tools()
            tools = list(getattr(listed, "tools", None) or ())
            tool_by_name = {getattr(t, "name", ""): t for t in tools if getattr(t, "name", None)}
            discovered_names = sorted(tool_by_name.keys())
            chosen_name = next((name for name in EXEC_TOOL_CANDIDATES if name in tool_by_name), None)

        runtime_id: Optional[str] = None
        session_id: Optional[str] = None

        # Step 1 (optional hosted runtime/session provisioning if exposed)
        for rt_tool_name in ("create_runtime", "get_or_create_runtime"):
            if rt_tool_name in tool_by_name:
                props = _tool_input_schema(tool_by_name[rt_tool_name])
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
            props = _tool_input_schema(tool_by_name["create_session"])
            prop_dict = props.get("properties", {}) if isinstance(props, dict) else {}
            sess_args: Dict[str, Any] = {}
            rt_key = _match_arg_name(prop_dict, ("runtime_id", "runtimeId", "runtime_name"))
            if rt_key and runtime_id:
                sess_args[rt_key] = runtime_id
            sess_res = await session.call_tool("create_session", sess_args)
            sess_txt = _extract_tool_text(sess_res)
            session_id = _extract_identifier(sess_txt, ("session_id", "sessionId", "id", "name"))

        # Step 2: Execute wrapped Python code via the first matching execution tool
        if chosen_name is None:
            chosen_name = next((name for name in EXEC_TOOL_CANDIDATES if name in tool_by_name), None)
        self._browser_linked = chosen_name is not None
        if chosen_name is None:
            raise RuntimeError(
                f"Colab MCP server did not expose a supported code-execution tool; discovered tools: {discovered_names}. "
                "Ensure Google Colab opened in your browser and the MCP notebook session is active."
            )

        schema = _tool_input_schema(tool_by_name[chosen_name])
        prop_dict = schema.get("properties", {}) if isinstance(schema, dict) else {}
        code_key = _match_arg_name(prop_dict, ("code", "source", "script", "python_code", "cell_source"), fallback="code")
        exec_args: Dict[str, Any] = {code_key: wrapped_code}

        rt_key = _match_arg_name(prop_dict, ("runtime_id", "runtimeId"))
        if rt_key and runtime_id:
            exec_args[rt_key] = runtime_id
        sess_key = _match_arg_name(prop_dict, ("session_id", "sessionId"))
        if sess_key and session_id:
            exec_args[sess_key] = session_id

        if status_callback:
            status_callback(f"Running code on Google Colab via `{chosen_name}`...")
        exec_res = await session.call_tool(chosen_name, exec_args)
        return _extract_tool_text(exec_res)

    async def execute_python_script(
        self,
        script_source: str,
        job_inputs: Dict[str, Any],
        *,
        accelerator: str = "CPU",
        status_callback: Optional[Callable[[str], None]] = None,
        connect_url_callback: Optional[Callable[[Optional[str]], None]] = None,
    ) -> Dict[str, Any]:
        """Sanitizes inputs, executes script on remote Colab MCP runtime under timeout, and parses marked JSON."""
        sanitized = sanitize_job_inputs(job_inputs)
        wrapped = self._wrap_script(script_source, sanitized)

        if self._session_factory is None and self.config.mode == "cli":
            stdout_text = await asyncio.wait_for(
                asyncio.to_thread(
                    self._execute_via_cli,
                    wrapped,
                    accelerator=accelerator,
                    status_callback=status_callback,
                ),
                timeout=float(self.config.timeout_sec) + 50.0,
            )
            return extract_marked_json(stdout_text)

        async def _inner() -> Dict[str, Any]:
            async with self._open_session() as session:
                stdout_text = await self._run_on_session(
                    session,
                    wrapped,
                    accelerator=accelerator,
                    status_callback=status_callback,
                    connect_url_callback=connect_url_callback,
                )
                return extract_marked_json(stdout_text)

        try:
            return await asyncio.wait_for(_inner(), timeout=float(self.config.timeout_sec))
        except BaseException as exc:
            if hasattr(exc, "exceptions"):
                raise RuntimeError(unwrap_exception_message(exc)) from exc
            raise

    def execute_python_script_sync(
        self,
        script_source: str,
        job_inputs: Dict[str, Any],
        *,
        accelerator: str = "CPU",
        status_callback: Optional[Callable[[str], None]] = None,
        connect_url_callback: Optional[Callable[[Optional[str]], None]] = None,
    ) -> Dict[str, Any]:
        """Blocking wrapper for daemon worker threads."""
        if self._session_factory is None and self.config.mode == "cli":
            sanitized = sanitize_job_inputs(job_inputs)
            wrapped = self._wrap_script(script_source, sanitized)
            stdout_text = self._execute_via_cli(
                wrapped,
                accelerator=accelerator,
                status_callback=status_callback,
            )
            return extract_marked_json(stdout_text)

        if self._use_persistent_session():
            sanitized = sanitize_job_inputs(job_inputs)
            wrapped = self._wrap_script(script_source, sanitized)
            persistent = get_persistent_session(self.config)

            async def _body(session: Any) -> Dict[str, Any]:
                self._connect_url_file = persistent.connect_url_file
                stdout_text = await self._run_on_session(
                    session,
                    wrapped,
                    accelerator=accelerator,
                    initialized=True,
                    status_callback=status_callback,
                    connect_url_callback=connect_url_callback,
                )
                return extract_marked_json(stdout_text)

            try:
                return persistent.run(_body, timeout=float(self.config.timeout_sec), client=self)
            except BaseException as exc:
                if hasattr(exc, "exceptions"):
                    raise RuntimeError(unwrap_exception_message(exc)) from exc
                raise

        try:
            return asyncio.run(
                self.execute_python_script(
                    script_source,
                    job_inputs,
                    accelerator=accelerator,
                    status_callback=status_callback,
                    connect_url_callback=connect_url_callback,
                )
            )
        except BaseException as exc:
            if hasattr(exc, "exceptions"):
                raise RuntimeError(unwrap_exception_message(exc)) from exc
            raise

    def _prepare_batch(self, items: List[Tuple[str, str, Dict[str, Any], str]]) -> List[Tuple[str, str, str]]:
        prepared: List[Tuple[str, str, str]] = []
        for job_type, script_source, job_inputs, accelerator in items:
            sanitized = sanitize_job_inputs(job_inputs)
            prepared.append((job_type, self._wrap_script(script_source, sanitized), accelerator))
        return prepared

    async def _run_batch_on_session(
        self,
        session: Any,
        prepared: List[Tuple[str, str, str]],
        results: Dict[str, Dict[str, Any]],
        progress_callback: Optional[Callable[[str, str, str], None]],
        connect_url_callback: Optional[Callable[[Optional[str]], None]],
    ) -> None:
        abort_remaining_msg: Optional[str] = None
        for job_type, wrapped, accelerator in prepared:
            if abort_remaining_msg is not None:
                results[job_type] = {"ok": False, "error": abort_remaining_msg}
                continue
            try:
                if progress_callback:
                    progress_callback(job_type, "running", f"Running {job_type} on Google Colab...")
                stdout_text = await self._run_on_session(
                    session,
                    wrapped,
                    accelerator=accelerator,
                    initialized=True,
                    status_callback=(lambda msg, _jt=job_type: progress_callback(_jt, "handshake", msg)) if progress_callback else None,
                    connect_url_callback=connect_url_callback,
                )
                parsed = extract_marked_json(stdout_text)
                results[job_type] = {"ok": True, "payload": parsed}
                if progress_callback:
                    progress_callback(job_type, "succeeded", f"Completed {job_type}")
            except BaseException as exc:
                if isinstance(exc, asyncio.CancelledError):
                    raise
                err_msg = unwrap_exception_message(exc)
                results[job_type] = {"ok": False, "error": err_msg}
                if progress_callback:
                    progress_callback(job_type, "failed", err_msg)
                if "browser session did not connect" in err_msg.lower():
                    abort_remaining_msg = err_msg

    async def execute_scripts_batch(
        self,
        items: List[Tuple[str, str, Dict[str, Any], str]],
        *,
        progress_callback: Optional[Callable[[str, str, str], None]] = None,
        connect_url_callback: Optional[Callable[[Optional[str]], None]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Execute multiple `(job_type, script_source, job_inputs, accelerator)` items sequentially."""
        prepared = self._prepare_batch(items)
        if self._session_factory is None and self.config.mode == "cli":
            return await asyncio.to_thread(self._run_cli_batch_sync, prepared, progress_callback)

        results: Dict[str, Dict[str, Any]] = {}

        async def _inner() -> None:
            async with self._open_session() as session:
                await session.initialize()
                await self._run_batch_on_session(session, prepared, results, progress_callback, connect_url_callback)

        try:
            total_timeout = float(self.config.timeout_sec) * max(1, len(prepared))
            await asyncio.wait_for(_inner(), timeout=total_timeout)
        except BaseException as exc:
            err_msg = unwrap_exception_message(exc)
            for job_type, _, _ in prepared:
                if job_type not in results:
                    results[job_type] = {"ok": False, "error": err_msg}
        return results

    def execute_scripts_batch_sync(
        self,
        items: List[Tuple[str, str, Dict[str, Any], str]],
        *,
        progress_callback: Optional[Callable[[str, str, str], None]] = None,
        connect_url_callback: Optional[Callable[[Optional[str]], None]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Blocking single-session batch wrapper for `ColabJobManager.run_pipeline()`."""
        if self._session_factory is None and self.config.mode == "cli":
            prepared = self._prepare_batch(items)
            return self._run_cli_batch_sync(prepared, progress_callback)

        if self._use_persistent_session():
            prepared = self._prepare_batch(items)
            results: Dict[str, Dict[str, Any]] = {}
            persistent = get_persistent_session(self.config)

            async def _body(session: Any) -> None:
                self._connect_url_file = persistent.connect_url_file
                await self._run_batch_on_session(session, prepared, results, progress_callback, connect_url_callback)

            try:
                total_timeout = float(self.config.timeout_sec) * max(1, len(prepared))
                persistent.run(_body, timeout=total_timeout, client=self)
            except BaseException as exc:
                err_msg = unwrap_exception_message(exc) or "Colab session failed"
                for job_type, _, _ in prepared:
                    if job_type not in results:
                        results[job_type] = {"ok": False, "error": err_msg}
            return results

        return asyncio.run(
            self.execute_scripts_batch(
                items,
                progress_callback=progress_callback,
                connect_url_callback=connect_url_callback,
            )
        )


def is_persistent_session_enabled(env: Optional[Dict[str, str]] = None) -> bool:
    """`COLAB_MCP_PERSISTENT_SESSION` (default on) keeps one colab-mcp process + browser link per server run."""
    source = os.environ if env is None else env
    raw = (source.get("COLAB_MCP_PERSISTENT_SESSION") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PersistentColabSession:
    """
    Holds one MCP session open on a dedicated background event loop so later jobs reuse
    the same colab-mcp process (and therefore the same linked Google Colab browser tab).
    Jobs are serialized; a crashed or timed-out session is torn down and lazily restarted.
    """

    START_TIMEOUT_SEC = 180.0

    def __init__(self, opener: Callable[[], Any], url_file_getter: Optional[Callable[[], Optional[str]]] = None) -> None:
        self._opener = opener
        self._url_file_getter = url_file_getter
        self._call_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_evt: Optional[asyncio.Event] = None
        self._ready = threading.Event()
        self._session: Any = None
        self._start_error: Optional[BaseException] = None
        self._alive = False
        self.started_at_utc: Optional[str] = None
        self.last_used_at_utc: Optional[str] = None
        self.browser_linked = False
        self.open_count = 0

    @property
    def connect_url_file(self) -> Optional[str]:
        return self._url_file_getter() if self._url_file_getter else None

    def is_alive(self) -> bool:
        return bool(self._alive and self._thread is not None and self._thread.is_alive())

    def status(self) -> Dict[str, Any]:
        return {
            "persistent": True,
            "alive": self.is_alive(),
            "browser_linked": bool(self.browser_linked and self.is_alive()),
            "started_at_utc": self.started_at_utc if self.is_alive() else None,
            "last_used_at_utc": self.last_used_at_utc,
            "open_count": self.open_count,
        }

    async def _hold(self) -> None:
        self._stop_evt = asyncio.Event()
        async with self._opener() as session:
            await session.initialize()
            self._session = session
            self._alive = True
            self.open_count += 1
            self.started_at_utc = _utc_iso()
            self._ready.set()
            await self._stop_evt.wait()

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._hold())
        except BaseException as exc:  # noqa: BLE001 - surfaced to callers via _start_error / cancelled futures
            if not self._ready.is_set():
                self._start_error = exc
        finally:
            self._alive = False
            self._session = None
            self.browser_linked = False
            try:
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for t in pending:
                    t.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            self._ready.set()
            try:
                loop.close()
            except Exception:
                pass
            self._loop = None

    def _ensure_started(self, timeout: float) -> None:
        with self._state_lock:
            if self.is_alive():
                return
            if self._thread is not None and self._thread.is_alive():
                self._thread.join(timeout=5.0)
            self._ready.clear()
            self._start_error = None
            self._thread = threading.Thread(target=self._thread_main, name="ColabMCPPersistentSession", daemon=True)
            self._thread.start()
        if not self._ready.wait(timeout=timeout):
            self.close()
            raise RuntimeError("Timed out starting the Google Colab MCP session.")
        if self._start_error is not None:
            err = self._start_error
            self._start_error = None
            raise RuntimeError(unwrap_exception_message(err)) from err
        if not self.is_alive():
            raise RuntimeError("Google Colab MCP session exited during startup.")

    def run(self, coro_factory: Callable[[Any], Awaitable[Any]], *, timeout: float, client: Any = None) -> Any:
        with self._call_lock:
            self._ensure_started(timeout=min(float(timeout), self.START_TIMEOUT_SEC))
            loop = self._loop
            if loop is None or self._session is None:
                raise RuntimeError("Google Colab MCP session is not available.")
            fut = asyncio.run_coroutine_threadsafe(coro_factory(self._session), loop)
            try:
                return fut.result(timeout=float(timeout))
            except concurrent.futures.TimeoutError as exc:
                fut.cancel()
                self.close()
                raise asyncio.TimeoutError("Google Colab MCP job timed out; session was reset.") from exc
            except concurrent.futures.CancelledError as exc:
                raise RuntimeError("Google Colab MCP session closed unexpectedly; click Retry to reconnect.") from exc
            finally:
                self.last_used_at_utc = _utc_iso()
                if client is not None and getattr(client, "_session_broken", False):
                    self.close()
                elif client is not None and self.is_alive():
                    self.browser_linked = bool(getattr(client, "_browser_linked", False)) or self.browser_linked

    def close(self) -> None:
        loop, stop_evt, thread = self._loop, self._stop_evt, self._thread
        if loop is not None and stop_evt is not None:
            try:
                loop.call_soon_threadsafe(stop_evt.set)
            except RuntimeError:
                pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=10.0)


_PERSISTENT_LOCK = threading.Lock()
_PERSISTENT_SESSIONS: Dict[Tuple[str, Tuple[str, ...]], PersistentColabSession] = {}


def get_persistent_session(config: ColabMCPConfig) -> PersistentColabSession:
    key = (config.stdio_command, tuple(config.stdio_args))
    with _PERSISTENT_LOCK:
        sess = _PERSISTENT_SESSIONS.get(key)
        if sess is None:
            opener_client = ColabMCPClient(config=config)
            sess = PersistentColabSession(
                opener=opener_client._open_session,
                url_file_getter=lambda: opener_client._connect_url_file,
            )
            _PERSISTENT_SESSIONS[key] = sess
        return sess


def get_persistent_session_status(config: Optional[ColabMCPConfig] = None) -> Dict[str, Any]:
    cfg = config or ColabMCPConfig.from_env()
    if cfg.mode != "stdio" or not is_persistent_session_enabled():
        return {"persistent": False, "alive": False, "browser_linked": False}
    with _PERSISTENT_LOCK:
        sess = _PERSISTENT_SESSIONS.get((cfg.stdio_command, tuple(cfg.stdio_args)))
    if sess is None:
        return {"persistent": True, "alive": False, "browser_linked": False, "open_count": 0}
    return sess.status()


def close_persistent_sessions() -> None:
    with _PERSISTENT_LOCK:
        sessions = list(_PERSISTENT_SESSIONS.values())
        _PERSISTENT_SESSIONS.clear()
    for sess in sessions:
        try:
            sess.close()
        except Exception:
            pass


atexit.register(close_persistent_sessions)
