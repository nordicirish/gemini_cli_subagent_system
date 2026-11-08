"""
GEM Trading System - Antigravity Sidecar Launcher
Launches or proxies the GEM Trading Dashboard inside the Antigravity Auxiliary Side Pane.
"""

import os
import sys
import socket
import subprocess
import http.server
import urllib.request

DEFAULT_TARGET_PORT = 8000

def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.connect(("127.0.0.1", port))
            return True
        except (ConnectionRefusedError, OSError):
            return False

def run_reverse_proxy(listen_port: int, target_port: int):
    """Simple reverse proxy to forward traffic to the already-running server."""
    class ProxyHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self._proxy()

        def do_POST(self):
            self._proxy()

        def do_PUT(self):
            self._proxy()

        def do_DELETE(self):
            self._proxy()

        def _proxy(self):
            url = f"http://127.0.0.1:{target_port}{self.path}"
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len) if content_len > 0 else None
            
            headers = {k: v for k, v in self.headers.items() if k.lower() != "host"}
            req = urllib.request.Request(url, data=body, headers=headers, method=self.command)
            try:
                with urllib.request.urlopen(req) as resp:
                    self.send_response(resp.status)
                    for k, v in resp.headers.items():
                        self.send_header(k, v)
                    self.end_headers()
                    self.wfile.write(resp.read())
            except urllib.error.HTTPError as he:
                self.send_response(he.code)
                for k, v in he.headers.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(he.read())
            except Exception as e:
                self.send_response(502)
                self.end_headers()
                self.wfile.write(f"Proxy error: {e}".encode("utf-8"))

        def log_message(self, format, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", listen_port), ProxyHandler)
    print(f"[Sidecar Proxy] Forwarding Antigravity Side Pane ({listen_port}) -> Running Server ({target_port})")
    server.serve_forever()

def main():
    sidecar_port = int(os.environ.get("ANTIGRAVITY_SIDECAR_WEB_PORT", DEFAULT_TARGET_PORT))
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    web_server_py = os.path.join(repo_root, "python", "web_server.py")
    
    # Check if a standalone server is already running on port 8000
    if is_port_in_use(DEFAULT_TARGET_PORT):
        if sidecar_port == DEFAULT_TARGET_PORT:
            print(f"[Sidecar] GEM Web Server is already active on port {DEFAULT_TARGET_PORT}.")
            # Keep process alive so Antigravity doesn't think sidecar died
            import time
            while is_port_in_use(DEFAULT_TARGET_PORT):
                time.sleep(2)
            sys.exit(0)
        else:
            # Reverse-proxy sidecar_port to target_port
            run_reverse_proxy(sidecar_port, DEFAULT_TARGET_PORT)
            return

    # No server running: launch web_server.py directly
    venv_python = os.path.join(repo_root, ".venv", "Scripts", "python.exe")
    python_bin = venv_python if os.path.exists(venv_python) else sys.executable
    
    env = os.environ.copy()
    env["ANTIGRAVITY_SIDECAR_WEB_PORT"] = str(sidecar_port)
    
    print(f"[Sidecar] Spawning GEM Web Server on port {sidecar_port}...")
    proc = subprocess.Popen([python_bin, web_server_py], cwd=repo_root, env=env)
    proc.wait()

if __name__ == "__main__":
    main()

