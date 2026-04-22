import requests
import time

url = "http://127.0.0.1:11434/api/generate"
payload = {
    "model": "gemma4:e4b",
    "prompt": "hi",
    "stream": False
}

print(f"Connecting to {url}...")
start_time = time.time()
try:
    response = requests.post(url, json=payload, timeout=300)
    duration = time.time() - start_time
    print(f"Status Code: {response.status_code}")
    print(f"Response Time: {duration:.2f} seconds")
    print(f"Response: {response.json().get('response')}")
except Exception as e:
    print(f"Error: {e}")
