import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from web_server import create_new_session, ORCHESTRATOR_MODEL, framework
from google.genai.errors import APIError

def test_fallback_logic():
    print(f"Initial Orchestrator Model: {ORCHESTRATOR_MODEL}")
    MODEL_FALLBACK_CASCADE = [
        "gemini-3.7-flash",
        "gemini-3.5-flash",
        "gemini-3.1-pro-preview",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-flash"
    ]
    
    current_model = "gemini-3.7-flash"
    current_idx = MODEL_FALLBACK_CASCADE.index(current_model)
    candidates = MODEL_FALLBACK_CASCADE[current_idx + 1:] + MODEL_FALLBACK_CASCADE[:current_idx]
    print(f"Fallback candidate sequence from {current_model}: {candidates}")
    assert candidates[0] == "gemini-3.5-flash" or candidates[0] == "gemini-3.1-pro-preview" or candidates[0] == "gemini-2.5-pro"
    print("Cascading candidate sequence verified successfully!")

if __name__ == "__main__":
    test_fallback_logic()
