"""
Test script for AntigravityAgentRunner
"""

import os
import sys
import time
import subprocess
from types import SimpleNamespace

class AntigravityResponse:
    def __init__(self, text: str, prompt_tokens: int = 0, candidate_tokens: int = 0):
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

    def run_prompt(self, prompt: str, system_instruction: str = "", mode: str = "FAST", tools=None, timeout: int = 120) -> AntigravityResponse:
        if self.cancel_check and self.cancel_check():
            raise RuntimeError("Operation cancelled by user.")

        # Map execution mode
        if mode in ("PRO", "THINKING"):
            model = "gemini-3.8-flash-high"
            effort = "medium" if mode == "PRO" else "high"
        else:
            model = "gemini-3.8-flash-low"
            effort = "low"

        full_prompt = prompt
        if system_instruction:
            full_prompt = f"System Instructions:\n{system_instruction}\n\nTask / User Prompt:\n{prompt}\n"

        cmd = [
            self.agy_path,
            "--model", model,
            "--effort", effort,
            "--dangerously-skip-permissions",
        ]

        start_time = time.time()
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

        elapsed = time.time() - start_time
        if proc.returncode != 0:
            raise RuntimeError(f"Antigravity Agent failed (code {proc.returncode}): {err or out}")

        output_text = out.strip()
        # Estimate token usage for telemetry tracking
        p_tokens = len(full_prompt) // 4
        c_tokens = len(output_text) // 4
        return AntigravityResponse(output_text, prompt_tokens=p_tokens, candidate_tokens=c_tokens)

def main():
    print("Testing AntigravityAgentRunner...")
    runner = AntigravityAgentRunner()
    print(f"Using agy binary: {runner.agy_path}")
    
    resp = runner.run_prompt(
        prompt="Output valid JSON: {\"agent\": \"Macro Sentinel\", \"status\": \"ACTIVE\", \"inflation_risk\": \"MODERATE\"}",
        system_instruction="You are Macro Sentinel. Always output strictly valid JSON without markdown fences.",
        mode="FAST"
    )
    print("\n--- Agent Output ---")
    print(resp.text)
    print("\n--- Response Properties ---")
    print(f"Candidates count: {len(resp.candidates)}")
    print(f"First candidate text: {resp.candidates[0].content.parts[0].text[:50]}...")
    print(f"Token counts: {resp.usage_metadata.prompt_token_count} prompt, {resp.usage_metadata.candidates_token_count} candidate")
    print("Test passed successfully!")

if __name__ == "__main__":
    main()

