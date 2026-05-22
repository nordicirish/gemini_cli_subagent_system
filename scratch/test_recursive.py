import json
from datetime import datetime

def find_key_recursive(data, target_key):
    if not isinstance(data, dict): return None
    if target_key in data: return data[target_key]
    for k in ("EXECUTION_PAYLOAD", "mutable_state", "payload"):
        if k in data:
            res = find_key_recursive(data[k], target_key)
            if res is not None: return res
    return None

payload = {
  "EXECUTION_PAYLOAD": {
    "sync_id": 12,
    "timestamp": "2026-05-11 16:27:22",
    "regime": "MELT_UP",
    "vix_guard": "DISENGAGED",
    "portfolio_snapshot": [
      {
        "ticker": "UMAC",
        "trade_state": "NO_TRADE",
        "scrutiny_audit": {}
      }
    ]
  }
}

portfolio = find_key_recursive(payload, "portfolio_snapshot")
print(f"Portfolio found: {portfolio is not None}")
ts = find_key_recursive(payload, "timestamp")
print(f"Timestamp found: {ts}")
regime = find_key_recursive(payload, "regime")
print(f"Regime found: {regime}")
