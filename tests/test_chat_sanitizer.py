"""
Chat HTML sanitizer regression tests.

* The page (static/index.html) and the JS test dependencies (package.json) must pin the same
  marked / DOMPurify versions, so the tests exercise exactly what the browser loads.
* tests/js/sanitize_chat_html.test.js runs the sanitizer from static/modern_ui.js against real
  DOMPurify + marked in jsdom. Requires `npm install` (test-only devDependencies); skipped with
  an install hint when node or node_modules are missing.
"""

import json
import os
import re
import shutil
import subprocess
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
JS_TEST = os.path.join(ROOT, "tests", "js", "sanitize_chat_html.test.js")


def _read(rel_path: str) -> str:
    with open(os.path.join(ROOT, rel_path), "r", encoding="utf-8") as f:
        return f.read()


class TestChatSanitizer(unittest.TestCase):
    def test_page_and_test_dependencies_pin_same_versions(self):
        html = _read("static/index.html")
        dev = json.loads(_read("package.json"))["devDependencies"]

        marked = re.search(r'src="https://cdn\.jsdelivr\.net/npm/marked@([0-9][0-9.]*)/marked\.min\.js"', html)
        purify = re.search(r'src="https://cdnjs\.cloudflare\.com/ajax/libs/dompurify/([0-9][0-9.]*)/purify\.min\.js"', html)
        self.assertIsNotNone(marked, "index.html must load an exact, pinned marked version")
        self.assertIsNotNone(purify, "index.html must load an exact, pinned DOMPurify version")
        self.assertEqual(marked.group(1), dev["marked"], "marked version differs between index.html and package.json")
        self.assertEqual(purify.group(1), dev["dompurify"], "DOMPurify version differs between index.html and package.json")

    def test_dompurify_loads_before_chat_ui(self):
        html = _read("static/index.html")
        self.assertLess(html.index("purify.min.js"), html.index("modern_ui.js"))

    def test_sanitizer_blocks_xss_in_jsdom(self):
        node_bin = shutil.which("node")
        if node_bin is None:
            self.skipTest("node not found on PATH")
        if not os.path.isdir(os.path.join(ROOT, "node_modules", "jsdom")):
            self.skipTest("JS test dependencies not installed; run `npm install` in the repo root")
        proc = subprocess.run(
            [node_bin, JS_TEST, os.path.join(ROOT, "static", "modern_ui.js")],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(proc.returncode, 0, f"Sanitizer checks failed:\n{proc.stdout}\n{proc.stderr}")
        self.assertIn("ALL PASSED", proc.stdout)


if __name__ == "__main__":
    unittest.main()
