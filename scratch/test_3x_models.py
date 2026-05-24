import os
import json
from google import genai

def test_models():
    # Load config to get API key
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception as e:
        print(f"Failed to load config.json: {e}")
        return

    api_key = config.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY not found in config.json. Trying environment variable.")
        api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key:
        print("No API key available.")
        return

    client = genai.Client(api_key=api_key)
    
    test_models = [
        "gemini-3.1-pro-preview",
        "gemini-3-flash-preview",
        "gemini-2.5-pro",
        "gemini-2.5-flash"
    ]

    for model in test_models:
        print(f"Testing model: {model}...")
        try:
            response = client.models.generate_content(
                model=model,
                contents="Hello, reply with the word 'OK' if you can hear me."
            )
            print(f"-> SUCCESS! Response: {response.text.strip()}")
        except Exception as e:
            print(f"-> FAILED: {e}")

if __name__ == "__main__":
    test_models()
