"""
Entry points must import when launched as documented (`python python/web_server.py`,
`python python/main.py`, `python python/fetch_stocks.py`). Script mode puts only python/ on sys.path,
unlike the test runner, which also has the repo root, so a `python.database...` import used to crash only at launch.
"""

import os
import subprocess
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PY_DIR = os.path.join(ROOT, "python")

# Reproduce script-mode sys.path: script dir first, no cwd/repo root, no PYTHONPATH.
SCRIPT_MODE_IMPORT = (
    "import sys, os\n"
    "root, py_dir, module = sys.argv[1], sys.argv[2], sys.argv[3]\n"
    "norm = lambda p: os.path.normcase(os.path.abspath(p or os.getcwd()))\n"
    "sys.path[:] = [p for p in sys.path if p and norm(p) != norm(root)]\n"
    "sys.path.insert(0, py_dir)\n"
    "__import__(module)\n"
    "print('IMPORT_OK')\n"
)


class TestEntrypointsImportInScriptMode(unittest.TestCase):
    def _import_as_script(self, module):
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        proc = subprocess.run(
            [sys.executable, "-c", SCRIPT_MODE_IMPORT, ROOT, PY_DIR, module],
            cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=180,
        )
        self.assertIn("IMPORT_OK", proc.stdout, f"{module} failed to import in script mode:\n{proc.stderr[-3000:]}")

    def test_web_server_imports(self):
        self._import_as_script("web_server")

    def test_main_imports(self):
        self._import_as_script("main")

    def test_fetch_stocks_imports(self):
        self._import_as_script("fetch_stocks")


if __name__ == "__main__":
    unittest.main()
