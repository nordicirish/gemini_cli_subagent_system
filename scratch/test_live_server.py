import requests

try:
    print("Testing GET request to localhost:8001/api/basket...")
    res = requests.get("http://127.0.0.1:8001/api/basket", timeout=5)
    print(f"Status Code: {res.status_code}")
    print(f"Response: {res.json()}")
except Exception as e:
    print(f"Request failed: {e}")
