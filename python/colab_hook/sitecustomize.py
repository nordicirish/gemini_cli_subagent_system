"""
In-Process Browser URL Capture Hook for Google Colab MCP (`open_colab_browser_connection`).

Loaded automatically inside the `colab-mcp` Python process when `python/colab_hook`
is prepended to `PYTHONPATH`. Wraps `webbrowser.open`, `webbrowser.open_new`, and
`webbrowser.open_new_tab` so allowed Google Colab connection URLs are captured
atomically to `GEM_COLAB_CONNECT_URL_FILE` before best-effort browser launch.
"""

from __future__ import annotations

import os
import tempfile
from urllib.parse import urlparse
import webbrowser

ALLOWED_COLAB_HOSTS = frozenset({"colab.research.google.com", "colab.google.com"})
_HOOK_INSTALLED = False


def is_valid_colab_url(url: str) -> bool:
    raw = str(url or "").strip()
    if not raw or any(ch.isspace() for ch in raw):
        return False
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    return parsed.scheme.lower() == "https" and host in ALLOWED_COLAB_HOSTS


def capture_colab_url(url: str, out_path: str | None = None) -> bool:
    raw = str(url or "").strip()
    if not is_valid_colab_url(raw):
        return False
    target = (os.environ.get("GEM_COLAB_CONNECT_URL_FILE", "") if out_path is None else out_path).strip()
    if not target:
        return False

    parent = os.path.dirname(os.path.abspath(target)) or "."
    tmp_path = ""
    try:
        fd, tmp_path = tempfile.mkstemp(prefix=".gem-colab-url-", dir=parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(raw)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        os.replace(tmp_path, target)
        return True
    except OSError:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        try:
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(raw)
            return True
        except OSError:
            return False


def _install_webbrowser_hook() -> None:
    global _HOOK_INSTALLED
    if _HOOK_INSTALLED:
        return
    if not os.environ.get("GEM_COLAB_CONNECT_URL_FILE", "").strip():
        return

    _HOOK_INSTALLED = True
    orig_open = webbrowser.open
    orig_open_new = webbrowser.open_new
    orig_open_new_tab = webbrowser.open_new_tab

    def _wrap(orig_fn):
        def _wrapped(url: str, *args, **kwargs) -> bool:
            capture_colab_url(url)
            if os.environ.get("GEM_COLAB_SKIP_OS_OPEN") == "1":
                return True
            try:
                return bool(orig_fn(url, *args, **kwargs))
            except Exception:
                return False

        return _wrapped

    webbrowser.open = _wrap(orig_open)
    webbrowser.open_new = _wrap(orig_open_new)
    webbrowser.open_new_tab = _wrap(orig_open_new_tab)


_install_webbrowser_hook()
