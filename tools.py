import os
import json
import asyncio
import threading
import time

# We import from the existing fetch_stocks to leverage its battle-tested logic
from fetch_stocks import handle_paste, run_daemon
import fetch_stocks

import sys

# Suppress stdout specifically for the daemon thread
class ThreadAwareStdout:
    def __init__(self, original_stdout):
        self.original_stdout = original_stdout
        self.daemon_thread_id = None
        
    def write(self, data):
        if self.daemon_thread_id and threading.get_ident() == self.daemon_thread_id:
            return len(data) # suppress
        return self.original_stdout.write(data)
            
    def flush(self):
        if self.daemon_thread_id and threading.get_ident() == self.daemon_thread_id:
            return
        self.original_stdout.flush()
        
    def __getattr__(self, name):
        return getattr(self.original_stdout, name)

sys.stdout = ThreadAwareStdout(sys.stdout)

# Start the background data daemon to keep GLOBAL_STATE updated
daemon_thread = threading.Thread(target=run_daemon, daemon=True)
daemon_thread.start()
sys.stdout.daemon_thread_id = daemon_thread.ident

# A mock request to satisfy FastAPI's Request object in handle_paste
class MockRequest:
    def __init__(self, json_data):
        self._json_data = json_data
    
    async def json(self):
        return self._json_data

def read_ssot() -> str:
    """Reads the current Single Source of Truth (SSoT) state."""
    path = 'local_ssot_shadow.json'
    if not os.path.exists(path):
        return "{}"
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Error reading SSoT: {str(e)}"

def update_ssot(payload_json: str) -> str:
    """
    Takes an EXECUTION_PAYLOAD JSON string and merges it into the local SSoT.
    Returns a success message or error.
    """
    try:
        # Convert string to dict if it's a string
        if isinstance(payload_json, str):
            payload_data = json.loads(payload_json)
        else:
            payload_data = payload_json
            
        req = MockRequest({"payload": json.dumps(payload_data)})
        
        # Run the async handler synchronously
        res = asyncio.run(handle_paste(req))
        
        # Check response
        res_body = json.loads(res.body.decode('utf-8'))
        
        if res_body.get("status") == "success":
            return "SSoT updated successfully."
        else:
            return f"Error updating SSoT: {res_body.get('message')}"
            
    except Exception as e:
        return f"Exception in update_ssot: {str(e)}"

def read_trade_lessons() -> str:
    """Reads the historical trade lessons."""
    path = 'trade_lessons.json'
    if not os.path.exists(path):
        return "[]"
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Error reading trade lessons: {str(e)}"

def get_market_data() -> str:
    """
    Returns the latest live market data for all tickers and macro benchmarks.
    This fetches from the background daemon that continuously updates.
    """
    # Wait until the daemon populates the GLOBAL_STATE
    retries = 0
    while not fetch_stocks.GLOBAL_STATE and retries < 15:
        time.sleep(1)
        retries += 1
        
    state = fetch_stocks.GLOBAL_STATE
    if not state:
        return json.dumps({"error": "Market data daemon is still initializing or failed."})
        
    # We return the slim 'tickers' view, similar to the "Copy Turn Data" approach
    data_to_return = {
        "status": state.get("status"),
        "timestamp": state.get("timestamp"),
        "tickers": state.get("tickers")
    }
    return json.dumps(data_to_return, indent=2)

