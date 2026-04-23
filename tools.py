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
        try:
            return self.original_stdout.write(data)
        except UnicodeEncodeError:
            # Fallback for Windows terminals that don't support emojis
            return self.original_stdout.write(data.encode('ascii', 'replace').decode('ascii'))
            
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
    path = 'ssot.json'
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

def update_trade_lessons(lessons_json: str) -> str:
    """
    Appends or updates the trade_lessons.json file with new insights.
    Accepts a JSON array or a single lesson object.
    """
    path = 'trade_lessons.json'
    try:
        if isinstance(lessons_json, str):
            new_data = json.loads(lessons_json)
        else:
            new_data = lessons_json
            
        current_data = []
        is_dict_format = False
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                file_content = json.load(f)
                if isinstance(file_content, dict) and "trade_lessons" in file_content:
                    current_data = file_content["trade_lessons"]
                    is_dict_format = True
                else:
                    current_data = file_content
        
        if isinstance(new_data, list):
            current_data.extend(new_data)
        elif isinstance(new_data, dict) and "trade_lessons" in new_data:
            current_data.extend(new_data["trade_lessons"])
        else:
            current_data.append(new_data)
            
        save_data = {"trade_lessons": current_data} if is_dict_format else current_data
            
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, indent=2)
            
        return "Trade lessons updated successfully."
    except Exception as e:
        return f"Error updating trade lessons: {str(e)}"

def update_rules(rules_md_content: str) -> str:
    """
    Appends or updates the canonical rules.md file. 
    MANDATORY: This tool MUST ONLY be called after the user has explicitly approved 
    a rule promotion proposal per MANDATE_21.
    """
    path = os.path.join('GEM_Trading_Rules', 'rules.md')
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(rules_md_content)
        return "Rules.md updated successfully. NOTE: A server restart is required to refresh the Context Cache."
    except Exception as e:
        return f"Error updating rules: {str(e)}"

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
    return json.dumps(data_to_return)

def perform_web_forensic_search(query: str) -> str:
    """
    Performs a live web search to fetch current market filings (424B, S-3), 
    news catalysts, or structural data. Use this for forensic verification.
    """
    try:
        from google import genai
        from google.genai import types
        
        # Load config for API Key
        api_key = None
        if os.path.exists("config.json"):
            with open("config.json", "r") as f:
                config = json.load(f)
                api_key = config.get("GEMINI_API_KEY")
        
        client = genai.Client(api_key=api_key) if api_key else genai.Client()
        
        # Use a stable flash model for the search-only retrieval
        response = client.models.generate_content(
            model="gemini-2.5-flash", 
            contents=f"Search and summarize the following for financial forensic audit: {query}",
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                safety_settings=[
                    types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
                    types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
                    types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
                    types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE")
                ]
            )
        )
        return response.text
    except Exception as e:
        return f"Error performing web search: {str(e)}"

