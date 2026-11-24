"""
Dashboard script integration: tests/js/dashboard_load.test.js loads static/index.html with
portfolio.js, app.js and modern_ui.js as real classic scripts in jsdom (mocked API) and checks
they load cleanly together, the portfolio panel has a single implementation, and the chat
controller holds no portfolio code. Requires `npm install`; skipped with a hint otherwise.
"""

import os
import re
import shutil
import subprocess
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class TestDashboardScripts(unittest.TestCase):
    def test_script_order_in_index(self):
        with open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8") as f:
            html = f.read()
        order = re.findall(r'<script src="(portfolio|app|modern_ui)\.js\?v=', html)
        self.assertEqual(order, ["portfolio", "app", "modern_ui"])

    def test_dashboard_loads_in_jsdom(self):
        node_bin = shutil.which("node")
        if node_bin is None:
            self.skipTest("node not found on PATH")
        if not os.path.isdir(os.path.join(ROOT, "node_modules", "jsdom")):
            self.skipTest("JS test dependencies not installed; run `npm install` in the repo root")
        proc = subprocess.run(
            [node_bin, os.path.join(ROOT, "tests", "js", "dashboard_load.test.js")],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
        self.assertEqual(proc.returncode, 0, f"Dashboard load checks failed:\n{proc.stdout}\n{proc.stderr}")
        self.assertIn("ALL PASSED", proc.stdout)


if __name__ == "__main__":
    unittest.main()
