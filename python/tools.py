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
    path = 'context/ssot.json'
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
            
        # Try intercepting decision log early
        try:
            intercept_and_log_decision(payload_data)
        except Exception as ie:
            print(f"[System Warning] Decision interception inside update_ssot failed: {ie}")
            
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
    path = 'context/trade_lessons.json'
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
    path = 'context/trade_lessons.json'
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

def read_decision_log() -> str:
    """Reads the continuous time-series ledger of all Council decisions (decision_log.json)."""
    path = 'context/decision_log.json'
    if not os.path.exists(path):
        return "[]"
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Error reading decision log: {str(e)}"

def intercept_and_log_decision(data_input) -> None:
    """
    Parses and intercepts decisions and council debates from incoming data 
    (can be a raw dictionary or raw response text containing a JSON block)
    and appends them to decision_log.json.
    """
    try:
        import re
        import datetime
        
        parsed_payloads = []
        
        if isinstance(data_input, dict):
            parsed_payloads.append(data_input)
        elif isinstance(data_input, str):
            # Attempt to find JSON inside code blocks
            json_blocks = re.findall(r"```json\s*(.*?)\s*```", data_input, re.DOTALL | re.IGNORECASE)
            if not json_blocks:
                json_blocks = re.findall(r"```\s*(.*?)\s*```", data_input, re.DOTALL | re.IGNORECASE)
                
            for block in json_blocks:
                try:
                    block_clean = block.strip()
                    if block_clean.startswith("{") or block_clean.startswith("["):
                        parsed_payloads.append(json.loads(block_clean))
                except:
                    pass
            
            # If no blocks parsed, check for raw braces/brackets
            if not parsed_payloads:
                try:
                    start_idx = data_input.find('{')
                    end_idx = data_input.rfind('}')
                    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                        parsed_payloads.append(json.loads(data_input[start_idx:end_idx+1]))
                except:
                    pass
        else:
            return
            
        if not parsed_payloads:
            return
            
        def find_key_recursive(data, target_key):
            if not isinstance(data, dict):
                if isinstance(data, list):
                    for item in data:
                        res = find_key_recursive(item, target_key)
                        if res is not None:
                            return res
                return None
            if target_key in data:
                return data[target_key]
            for k in ("EXECUTION_PAYLOAD", "mutable_state", "state_context", "payload", "post_execution_state"):
                if k in data:
                    res = find_key_recursive(data[k], target_key)
                    if res is not None:
                        return res
            for val in data.values():
                if isinstance(val, (dict, list)):
                    res = find_key_recursive(val, target_key)
                    if res is not None:
                        return res
            return None

        for payload in parsed_payloads:
            incoming_portfolio = find_key_recursive(payload, "portfolio_snapshot")
            if incoming_portfolio and isinstance(incoming_portfolio, list):
                ts = find_key_recursive(payload, "timestamp") or datetime.datetime.now().isoformat()
                trigger = find_key_recursive(payload, "trigger_context") or "ROUTINE_OUTPUT"
                
                market_context = {
                    "regime": find_key_recursive(payload, "regime") or find_key_recursive(payload, "risk_regime"),
                    "vix_guard": find_key_recursive(payload, "vix_guard"),
                    "portfolio_value_eur": find_key_recursive(payload, "portfolio_total_value_eur") or find_key_recursive(payload, "total_liquidity_eur"),
                    "unallocated_cash_eur": find_key_recursive(payload, "unallocated_cash_eur") or find_key_recursive(payload, "remaining_cash_eur")
                }
                
                council_debate = find_key_recursive(payload, "council_debate")
                
                turn_log = {
                    "timestamp": ts, 
                    "trigger_context": trigger, 
                    "market_context": market_context, 
                    "council_debate": council_debate,
                    "decisions": []
                }
                
                for item in incoming_portfolio:
                    if isinstance(item, dict):
                        ticker = item.get("ticker")
                        trade_state = item.get("trade_state", item.get("action"))
                        scrutiny_audit = item.get("scrutiny_audit")
                        price = item.get("price") or item.get("current_price") or item.get("price_at_eval")
                        
                        if ticker and (trade_state or scrutiny_audit):
                            turn_log["decisions"].append({
                                "ticker": ticker,
                                "timestamp": ts,
                                "trigger_context": trigger,
                                "price_at_eval": price,
                                "trade_state": trade_state,
                                "scrutiny_audit": scrutiny_audit
                            })
                
                if turn_log["decisions"]:
                    decision_log_file = 'context/decision_log.json'
                    log_data = []
                    if os.path.exists(decision_log_file):
                        try:
                            with open(decision_log_file, 'r', encoding='utf-8') as f:
                                log_data = json.load(f)
                        except: pass
                    if not isinstance(log_data, list):
                        log_data = []
                    
                    duplicate = False
                    if log_data:
                        last_entry = log_data[-1]
                        if last_entry.get("timestamp") == ts and last_entry.get("decisions") == turn_log["decisions"]:
                            duplicate = True
                            
                    if not duplicate:
                        log_data.append(turn_log)
                        if len(log_data) > 300:
                            log_data = log_data[-300:]
                        with open(decision_log_file, 'w', encoding='utf-8') as f:
                            json.dump(log_data, f, indent=2)
                        print(f"[System] Decision Log Appended: {len(turn_log['decisions'])} decisions.")
                    break
    except Exception as e:
        print(f"[System Error] Decision log interception failure: {e}")

def update_rules(rules_md_content: str) -> str:
    """
    Appends or updates the canonical rules.md file. 
    MANDATORY: This tool MUST ONLY be called after the user has explicitly approved 
    a rule promotion proposal per MANDATE_21.
    """
    path = os.path.join('gem_trading_rules', 'rules.md')
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

def load_forensic_search_prompt(query: str) -> str:
    """Loads the forensic search prompt from prompts/forensic_search_prompt.txt and replaces the query placeholder."""
    prompt_path = "prompts/forensic_search_prompt.txt"
    template = ""
    if os.path.exists(prompt_path):
        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                template = f.read().strip()
        except Exception: pass
    if not template:
        template = "Search and summarize the following for financial forensic audit: {query}"
    return template.format(query=query)

def perform_web_forensic_search(query: str) -> str:
    """
    Performs a live web search to fetch current market filings (424B, S-3), 
    news catalysts, or structural data. Use this for forensic verification.
    """
    try:
        from google import genai
        from google.genai import types
        import agent_framework
        
        # Load config for model tier and API Key
        api_key = None
        flash_model = agent_framework.DEFAULT_MODEL_FLASH # Default
        if os.path.exists("context/config.json"):
            with open("context/config.json", "r") as f:
                config = json.load(f)
                api_key = config.get("GEMINI_API_KEY")
                flash_model = config.get("MODEL_FLASH", flash_model)
        
        client = genai.Client(api_key=api_key) if api_key else genai.Client()
        
        prompt = load_forensic_search_prompt(query)
        # Use a stable flash model for the search-only retrieval
        response = client.models.generate_content(
            model=flash_model, 
            contents=prompt,
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

