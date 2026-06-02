import yfinance as yf
import pandas as pd
import numpy as np
import requests
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
import os
import time as t_time
import sys
import json
import pyperclip
import re
from scipy.stats import norm
from yfinance.data import YfData
import threading
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

try:
    import msvcrt
except ImportError:
    msvcrt = None

def load_scout_prompt() -> str:
    """Loads the scout prompt from prompts/scout_prompt.txt."""
    prompt_path = "prompts/scout_prompt.txt"
    
    # Read from the main prompts directory
    if os.path.exists(prompt_path):
        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception as e:
            print(f"[Warning] Failed to read prompt from {prompt_path}: {e}")
            
    # Hardcoded safety fallback if files are completely deleted/missing
    return (
        "ROLE: Market-structure signal scout.\n"
        "TASK: Perform a live web search to identify top trending equities showing technical breakout conditions in the '{category}' sector.\n"
        "RETURN FORMAT: Return ONLY a valid JSON list of uppercase ticker symbols, e.g. [\"SYM1\", \"SYM2\"]."
    )

def compile_master_document():
    """Aggregates gem_trading_rules/rules.md and all engine instruction markdown files
    into a single master document at scratch/master_trading_knowledge.md to optimize cloud uploads.
    """
    master_doc_path = os.path.join("scratch", "master_trading_knowledge.md")
    os.makedirs("scratch", exist_ok=True)
    
    print("[System] Compiling Master Trading Knowledge Document...")
    try:
        with open(master_doc_path, 'w', encoding='utf-8') as master_file:
            master_file.write("# Master Trading Knowledge Document\n\n")
            master_file.write("This document contains the Single Source of Truth (SSoT) rules and all engine instructions.\n\n")
            
            # Append rules.md
            rules_local_path = os.path.join("gem_trading_rules", "rules.md")
            if os.path.exists(rules_local_path):
                master_file.write("## 1. TRADING RULES (SSoT)\n\n")
                with open(rules_local_path, 'r', encoding='utf-8') as rules_file:
                    content = rules_file.read()
                    demoted_lines = [('##' + line) if line.startswith('#') else line for line in content.splitlines()]
                    master_file.write('\n'.join(demoted_lines))
                master_file.write("\n\n---\n\n")
                
            # Append engine instructions
            master_file.write("## 2. ENGINE INSTRUCTIONS\n\n")
            engine_dir = "engine_instructions"
            if os.path.exists(engine_dir):
                for filename in sorted(os.listdir(engine_dir)):
                    if filename.endswith('.md'):
                        local_path = os.path.join(engine_dir, filename)
                        master_file.write(f"### Component: {filename}\n\n")
                        with open(local_path, 'r', encoding='utf-8') as engine_file:
                            content = engine_file.read()
                            demoted_lines = [('###' + line) if line.startswith('#') else line for line in content.splitlines()]
                            master_file.write('\n'.join(demoted_lines))
                        master_file.write("\n\n---\n\n")
        print(f"[System] Master Document compiled successfully: {master_doc_path}")
    except Exception as e:
        print(f"[System Warning] Failed to compile master document: {e}")

def initialize_context_files():
    """Bootstraps missing context files for fresh repository clones."""
    if not os.path.exists("context"):
        os.makedirs("context")
    if not os.path.exists("logs"):
        os.makedirs("logs")
    if not os.path.exists("prompts"):
        os.makedirs("prompts")
        
    scout_prompt_content = ""
    prompt_path = "prompts/scout_prompt.txt"
    if os.path.exists(prompt_path):
        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                scout_prompt_content = f.read()
        except Exception: pass
    if not scout_prompt_content:
        scout_prompt_content = (
            "ROLE: Market-structure signal scout.\n"
            "TASK: Perform a live web search to identify top trending equities showing technical breakout conditions in the '{category}' sector.\n"
            "RETURN FORMAT: Return ONLY a valid JSON list of uppercase ticker symbols, e.g. [\"SYM1\", \"SYM2\"]."
        )
        
    defaults = {
        "context/ssot.json": "{}",
        "context/trade_lessons.json": "[]",
        "context/decision_log.json": "[]",
        "context/user_config.json": "{}",
        "context/config.json": '{\n  "GEMINI_API_KEY": "",\n  "GEMINI_FREE_TIER_API_KEY": "",\n  "FINNHUB_API_KEY": ""\n}',
        "logs/gem_handshakes.log": "",
        "prompts/scout_prompt.txt": scout_prompt_content
    }
    
    for filepath, default_content in defaults.items():
        if not os.path.exists(filepath):
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(default_content)
                print(f"[System] Initialized missing file: {filepath}")
            except Exception as e:
                print(f"[Warning] Could not initialize {filepath}: {e}")

initialize_context_files()
compile_master_document()

# Load configuration from JSON file
with open('context/config.json', 'r') as f:
    config = json.load(f)

# -----------------------------
# CONFIGURATION
# -----------------------------
FINNHUB_API_KEY = config.get("FINNHUB_API_KEY")
POLYGON_API_KEY = config.get("POLYGON_API_KEY")

USE_FINNHUB = True
USE_POLYGON = False  # keep false unless you want Polygon volume fallback

# ─── Persistent user config ───────────────────────────────────────────────────
USER_CONFIG_FILE = 'context/user_config.json'

SCOUT_TICKER_MAP = config.get("SCOUT_TICKER_MAP", {})

# Dynamic Sector Tickers Cache Deck (ENH_CACHE_02)
DYNAMIC_SCOUT_CACHE = {}
DYNAMIC_SCOUT_CACHE_LOCK = threading.Lock()

VALID_SINGLE_LETTER_TICKERS = {'A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'J', 'K', 'L', 'M', 'N', 'O', 'R', 'S', 'T', 'U', 'V', 'W', 'X', 'Z'}
STOP_WORDS = {"AND", "THE", "FOR", "JSON", "USA", "ETF", "SEC", "CEO", "IPO", "NYSE", "NASDAQ", "US", "PRICING", "INDEX"}

def is_valid_ticker(symbol: str) -> bool:
    if not isinstance(symbol, str):
        return False
    symbol = symbol.strip().upper()
    if not symbol:
        return False
    if symbol in STOP_WORDS:
        return False
    if len(symbol) == 1:
        return symbol in VALID_SINGLE_LETTER_TICKERS
    # Enforce starting and ending with alphanumeric, allowing internal hyphens, slashes, equals, length 2 to 10.
    # Must contain at least one letter to prevent purely numeric codes.
    if not re.match(r"^[A-Z0-9][A-Z0-9^=/-]{0,8}[A-Z0-9]$", symbol):
        return False
    if not any(c.isalpha() for c in symbol):
        return False
    return True

def _get_dynamic_scout_tickers(category: str) -> list:
    """
    Dynamically performs a live Google Search via Gemini Flash to retrieve the top 5
    trending, high-performing tickers for a given sector, falling back to configuration mappings.
    Caches results in memory for 4 hours to respect API quotas.
    """
    global DYNAMIC_SCOUT_CACHE
    now = t_time.time()
    
    with DYNAMIC_SCOUT_CACHE_LOCK:
        if category in DYNAMIC_SCOUT_CACHE:
            cached = DYNAMIC_SCOUT_CACHE[category]
            if now - cached["timestamp"] < 14400: # 4-hour cache TTL
                return cached["tickers"]
                
    fallback = SCOUT_TICKER_MAP.get(category, [])
    
    try:
        from google import genai
        from google.genai import types
        
        api_key = config.get("GEMINI_API_KEY")
        flash_model = config.get("MODEL_FLASH", "gemini-2.5-flash")
        
        client = genai.Client(api_key=api_key) if api_key else genai.Client()
        
        scout_prompt = load_scout_prompt()
        prompt = scout_prompt.replace("{category}", category)
        
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
        
        text = response.text.strip()
        match = re.search(r"\[\s*\"[A-Z0-9^=-]{1,10}\"(?:\s*,\s*\"[A-Z0-9^=-]{1,10}\")*\s*\]", text, re.IGNORECASE)
        if match:
            tickers = json.loads(match.group(0))
        else:
            words = re.findall(r"\"([A-Z0-9^=-]{1,10})\"", text)
            if not words:
                words = re.findall(r"\b([A-Z0-9^=-]{1,10})\b", text)
            filter_words = {"AND", "THE", "FOR", "JSON", "USA", "ETF", "SEC", "CEO", "IPO", "NYSE", "NASDAQ", "US"}
            tickers = [w.upper() for w in words if w.upper() not in filter_words]
            
        tickers = [t.strip().upper() for t in tickers if is_valid_ticker(t)][:5]
        if tickers:
            with DYNAMIC_SCOUT_CACHE_LOCK:
                DYNAMIC_SCOUT_CACHE[category] = {"tickers": tickers, "timestamp": now}
            print(f"[Scout Scanner] Successfully discovered dynamic tickers for {category}: {tickers}")
            return tickers
            
    except Exception as e:
        print(f"[Scout Scanner] Dynamic search failed for {category}: {e}. Using fallback tickers.")
        
    return [t.strip().upper() for t in fallback if is_valid_ticker(t)]

ACTIVE_SCOUT_TICKERS = set()
PROCESSED_SCOUT_CATEGORIES = []
SCOUTED_TICKER_TO_CATEGORY_MAP = {}
FORCE_REFRESH = False

def _load_ssot_tickers():
    global ACTIVE_SCOUT_TICKERS, PROCESSED_SCOUT_CATEGORIES, SCOUTED_TICKER_TO_CATEGORY_MAP
    try:
        with open('context/ssot.json', 'r') as f:
            ssot = json.load(f)
            ms = ssot.get('mutable_state', ssot)
            portfolio = [t['ticker'] for t in ms.get('portfolio_snapshot', [])]
            watched = ms.get('watched_tickers', [])
            scouts = ms.get('scout_categories', [])
            
            scout_tickers = []
            SCOUTED_TICKER_TO_CATEGORY_MAP.clear()
            for cat in scouts:
                # Take a maximum of the top 5 tickers from each category
                cat_tickers = _get_dynamic_scout_tickers(cat)[:5]
                for t in cat_tickers:
                    SCOUTED_TICKER_TO_CATEGORY_MAP[t.upper()] = cat
                scout_tickers.extend(cat_tickers)
            
            ACTIVE_SCOUT_TICKERS = {t.upper() for t in scout_tickers if is_valid_ticker(t)}
            PROCESSED_SCOUT_CATEGORIES = list(scouts)
                    
            # Combine unique tickers, maintaining order where possible
            combined = []
            for t in portfolio + watched + list(ACTIVE_SCOUT_TICKERS):
                t_upper = t.upper()
                if t_upper not in combined and is_valid_ticker(t_upper):
                    combined.append(t_upper)
            return combined
    except Exception as e:
        print(f"[SSOT] Load failed: {e}")
        return config.get("DEFAULT_TICKERS", [])

def _load_macro_tickers():
    try:
        if os.path.exists('context/user_config.json'):
            with open('context/user_config.json', 'r') as f:
                user_cfg = json.load(f)
                if 'macro' in user_cfg and user_cfg['macro']:
                    return [t.upper() for t in user_cfg['macro']]
    except Exception as e:
        print(f"[Config] Load failed: {e}")
    return config.get("DEFAULT_MACRO_TICKERS", [])

# Clear active scout categories on startup to defer yahoo finance queries
try:
    if os.path.exists('context/ssot.json'):
        with open('context/ssot.json', 'r', encoding='utf-8') as f:
            ssot_data = json.load(f)
        
        has_changed = False
        if "mutable_state" in ssot_data:
            if ssot_data["mutable_state"].get("scout_categories"):
                ssot_data["mutable_state"]["scout_categories"] = []
                has_changed = True
        else:
            if ssot_data.get("scout_categories"):
                ssot_data["scout_categories"] = []
                has_changed = True
                
        if has_changed:
            with open('context/ssot.json', 'w', encoding='utf-8') as f:
                json.dump(ssot_data, f, indent=2)
            print("[SSOT] Active scout categories cleared on startup to defer loading.")
except Exception as e:
    print(f"[SSOT] Clear scout categories failed: {e}")

TICKERS = _load_ssot_tickers()
MACRO_TICKERS = _load_macro_tickers()
ALL_TICKERS = TICKERS + MACRO_TICKERS
if not ALL_TICKERS:
    TICKERS = ["AAPL", "MSFT", "GOOGL", "NVDA"]
    MACRO_TICKERS = ["SPY", "QQQ"]
    ALL_TICKERS = TICKERS + MACRO_TICKERS
INVERSE_MACRO = config.get("INVERSE_MACRO", [])

MACRO_LABELS = config.get("MACRO_LABELS", {})

REFRESH_RATE_SECONDS = config.get("REFRESH_RATE_SECONDS", 30)
HISTORY_REFRESH_CYCLES = config.get("HISTORY_REFRESH_CYCLES", 10)

GLOBAL_STATE = {
    "status": "INITIALIZING - LOADING GEX PROFILES (MAY TAKE 1-2 MIN)...",
    "tickers": [],
    "boot_phase": "STARTING_UP",
    "boot_progress": 0,
    "boot_total": 100,
    "boot_ticker": "SYSTEM"
}
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.get("/api/get_basket")
def get_basket():
    try:
        if os.path.exists("context/ssot.json"):
            with open("context/ssot.json", "r") as f:
                data = json.load(f)
                ms = data.get("mutable_state", data)
                raw_basket = ms.get("portfolio_snapshot", [])
                return JSONResponse([{"ticker": i["ticker"], "shares": i.get("shares", 0), "wac": i.get("wac", 0)} for i in raw_basket])
    except Exception as e:
        print(f"SSoT Read Warning: {e}")
    return JSONResponse([])

@app.post("/api/save_basket")
async def save_basket(req: Request):
    global TICKERS, ALL_TICKERS
    try:
        basket = await req.json()
        if os.path.exists("context/ssot.json"):
            with open("context/ssot.json", "r") as f:
                data = json.load(f)
            
            new_snapshot = []
            for item in basket:
                if item.get("shares", 0) > 0:
                    new_snapshot.append({
                        "ticker": item["ticker"],
                        "shares": item["shares"],
                        "wac": item["wac"]
                    })
            
            if "mutable_state" in data:
                data["mutable_state"]["portfolio_snapshot"] = new_snapshot
            else:
                data["portfolio_snapshot"] = new_snapshot
            
            with open("context/ssot.json", "w") as f:
                json.dump(data, f, indent=2)
            
            # Reload TICKERS
            TICKERS = _load_ssot_tickers()
            ALL_TICKERS = TICKERS + MACRO_TICKERS
            return JSONResponse({"status": "success"})
    except Exception as e:
        print(f"Basket Sync Failed: {e}")
    return JSONResponse({"status": "error"})

@app.get("/api/get_watch_list")
def get_watch_list():
    try:
        watchlist = []
        if os.path.exists("context/config.json"):
            with open("context/config.json", "r") as f:
                cfg = json.load(f)
                watchlist = cfg.get("WATCHLIST", [])
                
        # Make sure ssot.json is in sync
        if os.path.exists("context/ssot.json"):
            with open("context/ssot.json", "r") as f:
                data = json.load(f)
            ms = data.get("mutable_state", data)
            if ms.get("watched_tickers") != watchlist:
                if "mutable_state" in data:
                    data["mutable_state"]["watched_tickers"] = watchlist
                else:
                    data["watched_tickers"] = watchlist
                with open("context/ssot.json", "w") as f:
                    json.dump(data, f, indent=2)
                    
        return JSONResponse(watchlist)
    except: pass
    return JSONResponse([])

@app.post("/api/save_watch_list")
async def save_watch_list(req: Request):
    global TICKERS, ALL_TICKERS
    try:
        payload = await req.json()
        
        # Robustly handle both list payloads and dict payloads
        if isinstance(payload, dict):
            new_list = payload.get("watchlist", [])
        elif isinstance(payload, list):
            new_list = payload
        else:
            new_list = []
            
        new_list = [w.strip().upper() for w in new_list if isinstance(w, str) and w.strip()]
        
        # 1. Update config.json (ENH_83)
        if os.path.exists("context/config.json"):
            with open("context/config.json", "r") as f:
                cfg = json.load(f)
            cfg["WATCHLIST"] = new_list
            with open("context/config.json", "w") as f:
                json.dump(cfg, f, indent=2)
                
        # 2. Update ssot.json
        if os.path.exists("context/ssot.json"):
            with open("context/ssot.json", "r") as f:
                data = json.load(f)
            
            if "mutable_state" in data:
                data["mutable_state"]["watched_tickers"] = new_list
            else:
                data["watched_tickers"] = new_list
                
            with open("context/ssot.json", "w") as f:
                json.dump(data, f, indent=2)
            
            # Reload TICKERS
            TICKERS = _load_ssot_tickers()
            ALL_TICKERS = TICKERS + MACRO_TICKERS
            return JSONResponse({"status": "success"})
    except Exception as e:
        print(f"Watch List Sync Failed: {e}")
    return JSONResponse({"status": "error"})

@app.get("/api/data")
def get_data():
    return JSONResponse(GLOBAL_STATE)

@app.get("/api/tickers")
def get_tickers():
    return JSONResponse({"tickers": TICKERS, "macro": MACRO_TICKERS, "macro_labels": MACRO_LABELS})

def _deep_merge(base, delta):
    merged = base.copy()
    for k, v in delta.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged

def _merge_portfolio(existing_list, delta_list):
    if not isinstance(existing_list, list): existing_list = []
    if not isinstance(delta_list, list): delta_list = []
    by_ticker = {}
    for item in existing_list:
        t = item.get('ticker')
        if t: by_ticker[t] = item
    for item in delta_list:
        t = item.get('ticker')
        if t:
            if t in by_ticker:
                by_ticker[t] = _deep_merge(by_ticker[t], item)
            else:
                by_ticker[t] = item
                
    # Prune any items where shares <= 0 to satisfy ENH_99 SSoT Curation Protocol
    pruned_list = []
    for item in by_ticker.values():
        try:
            shares = float(item.get("shares", 0))
        except (ValueError, TypeError):
            shares = 0.0
        if shares > 0:
            pruned_list.append(item)
            
    return pruned_list

def _process_deletions(state, delete_list):
    """Parses SSoT DELETE_FIELD strings (e.g., 'portfolio_snapshot[RCAT]') and prunes keys from the state tree."""
    if not isinstance(delete_list, list):
        return
    for path in delete_list:
        if not isinstance(path, str): continue
        if path.startswith("DELETE_FIELD:"): 
            path = path.split("DELETE_FIELD:", 1)[1].strip()
        
        # Parse dot and bracket notation
        tokens = re.findall(r'[^.\[\]]+|\[[^\]]+\]', path)
        if not tokens: continue
        
        current = state
        parent = None
        last_key = None
        
        for idx, token in enumerate(tokens):
            if token.startswith('[') and token.endswith(']'):
                target_ticker = token[1:-1]
                if isinstance(current, list):
                    found = False
                    for i, item in enumerate(current):
                        if isinstance(item, dict) and item.get("ticker", "").upper() == target_ticker.upper():
                            parent = current
                            last_key = i
                            current = item
                            found = True
                            break
                    if not found: break  # Ticker not found, invalidate path
                else: break  # Invalid path structure
            else:
                if isinstance(current, dict) and token in current:
                    parent = current
                    last_key = token
                    current = current[token]
                else: break  # Key not found
                
            if idx == len(tokens) - 1 and parent is not None and last_key is not None:
                # Target acquired, execute deletion
                if isinstance(parent, list):
                    parent.pop(last_key)
                elif isinstance(parent, dict):
                    parent.pop(last_key, None)

@app.post("/api/paste")
async def handle_paste(req: Request):
    try:
        body = await req.json()
        clip_data = body.get("payload", "")
        
        # Process the clipboard data string
        if "```" in clip_data:
            match = re.search(r"```(?:json)?\s*(.*?)\s*```", clip_data, re.IGNORECASE | re.DOTALL)
            if match: clip_data = match.group(1)
            
        clip_data = re.sub(r'\[cite_start\]', '', clip_data)
        clip_data = re.sub(r'\[cite:\s*\d+(?:,\s*\d+)*\]', '', clip_data)
        clip_data = clip_data.strip()
        
        clip_data = clip_data.strip()
        
        if not (clip_data.startswith('{') or clip_data.startswith('[')) or not (clip_data.endswith('}') or clip_data.endswith(']')):
            start_brace = clip_data.find('{')
            start_bracket = clip_data.find('[')
            start_idx = -1
            if start_brace != -1 and start_bracket != -1: start_idx = min(start_brace, start_bracket)
            elif start_brace != -1: start_idx = start_brace
            else: start_idx = start_bracket
            
            if start_idx != -1:
                end_brace = clip_data.rfind('}')
                end_bracket = clip_data.rfind(']')
                end_idx = max(end_brace, end_bracket)
                if end_idx != -1 and end_idx > start_idx:
                    clip_data = clip_data[start_idx:end_idx+1]
        
        payload = json.loads(clip_data.strip())
        
        # Map common payload aliases to canonical keys
        def map_aliases(data):
            if not isinstance(data, dict): return
            if "portfolio" in data and "portfolio_snapshot" not in data:
                data["portfolio_snapshot"] = data.pop("portfolio")
            if "terminal_state" in data and "state_context" not in data:
                data["state_context"] = data.pop("terminal_state")
            if "forensic_alerts" in data and "forensic_intelligence" not in data:
                data["forensic_intelligence"] = data.pop("forensic_alerts")
        
        map_aliases(payload)
        if isinstance(payload.get("mutable_state"), dict):
            map_aliases(payload["mutable_state"])
        if isinstance(payload.get("EXECUTION_PAYLOAD"), dict):
            map_aliases(payload["EXECUTION_PAYLOAD"])
            
        # Helper to extract and remove lessons from payload regardless of nesting
        def extract_and_remove(data, key):
            val = None
            if key in data:
                val = data.pop(key, None)
            elif "mutable_state" in data and isinstance(data["mutable_state"], dict) and key in data["mutable_state"]:
                val = data["mutable_state"].pop(key, None)
            elif "EXECUTION_PAYLOAD" in data and isinstance(data["EXECUTION_PAYLOAD"], dict) and key in data["EXECUTION_PAYLOAD"]:
                val = data["EXECUTION_PAYLOAD"].pop(key, None)
            return val

        # Extract lesson data *before* merging
        extracted_compressed = extract_and_remove(payload, "compressed_trade_lessons")
        extracted_new = extract_and_remove(payload, "new_trade_lessons")
        extracted_trade_lessons = extract_and_remove(payload, "trade_lessons") or []
        if isinstance(extracted_trade_lessons, dict) and "trade_lessons" in extracted_trade_lessons:
            extracted_trade_lessons = extracted_trade_lessons["trade_lessons"]
        if not isinstance(extracted_trade_lessons, list):
            extracted_trade_lessons = []
            
        extracted_rev_trade_lessons = extract_and_remove(payload, "trade_lessons_revision") or []
        if isinstance(extracted_rev_trade_lessons, dict) and "trade_lessons" in extracted_rev_trade_lessons:
            extracted_rev_trade_lessons = extracted_rev_trade_lessons["trade_lessons"]
        elif isinstance(extracted_rev_trade_lessons, dict) and "trade_lessons_revision" in extracted_rev_trade_lessons:
            extracted_rev_trade_lessons = extracted_rev_trade_lessons["trade_lessons_revision"]
        if not isinstance(extracted_rev_trade_lessons, list):
            extracted_rev_trade_lessons = []
            
        extracted_trade_lessons.extend(extracted_rev_trade_lessons)
        if not extracted_trade_lessons:
            extracted_trade_lessons = None
            
        extracted_mutations = extract_and_remove(payload, "rule_mutations")

        # ENH_31: Promote key fields from EXECUTION_PAYLOAD to state root for immediate SSoT synchronization
        # This ensures that 'allocations' (portfolio_snapshot) and other directives are applied to the active state.
        incoming_ep = payload.get("EXECUTION_PAYLOAD")
        if not incoming_ep and isinstance(payload.get("mutable_state"), dict):
            incoming_ep = payload["mutable_state"].get("EXECUTION_PAYLOAD")
            
        # Determine source of truth for promotion (dict or root payload if flag is True)
        ep_source = None
        if isinstance(incoming_ep, dict):
            ep_source = incoming_ep
        elif incoming_ep is True:
            ep_source = payload

        if ep_source:
            promotion_keys = ["portfolio_snapshot", "risk_metrics", "directive", "timestamp", 
                              "remaining_cash_eur", "remaining_cash_usd", 
                              "unallocated_cash_eur", "unallocated_cash_usd",
                              "base_currency", "exchange_rate", 
                              "portfolio_total_value_usd", "portfolio_total_value_eur",
                              "state_context", "forensic_intelligence"]
            
            # Source data can be at the root of ep_source or inside its mutable_state
            source_data = ep_source
            if "mutable_state" in ep_source and isinstance(ep_source["mutable_state"], dict):
                source_data = _deep_merge(ep_source, ep_source["mutable_state"])

            # Determine target container for promotion
            target_container = payload
            if isinstance(payload.get("mutable_state"), dict):
                target_container = payload["mutable_state"]
                
            for k in promotion_keys:
                if k in source_data:
                    # Directives Supremacy (ENH_31-P): Promote portfolio_snapshot as both active portfolio_snapshot and proposed_portfolio_snapshot
                    if k == "portfolio_snapshot":
                        target_container["portfolio_snapshot"] = source_data[k]
                    # For all other promotion keys, promote/overwrite them to target_container
                    else:
                        target_container[k] = source_data[k]

        # Merge local ssot
        existing_ssot = {}
        if os.path.exists('context/ssot.json'):
            try:
                with open('context/ssot.json', 'r') as f:
                    existing_ssot = json.load(f)
            except: pass
            
        # Check if existing SSoT uses the v4.9x Layer Model
        has_layer_model = "mutable_state" in existing_ssot
        
        # If payload provides naked keys but existing uses Layer Model, wrap the payload in mutable_state
        if has_layer_model and "mutable_state" not in payload and "immutable_background" not in payload:
            payload = {"mutable_state": payload}

        if existing_ssot:
            existing_portfolio = existing_ssot.get("mutable_state", {}).get("portfolio_snapshot", []) if has_layer_model else existing_ssot.get("portfolio_snapshot", [])
            
            # Check if portfolio_snapshot is explicitly specified in the payload (to handle empty arrays properly)
            # Default to None to distinguish between 'missing in payload' and 'explicitly empty'
            has_payload_portfolio = False
            payload_portfolio = None
            if has_layer_model:
                if "mutable_state" in payload and isinstance(payload["mutable_state"], dict) and "portfolio_snapshot" in payload["mutable_state"]:
                    payload_portfolio = payload["mutable_state"]["portfolio_snapshot"]
                    has_payload_portfolio = True
            else:
                if "portfolio_snapshot" in payload:
                    payload_portfolio = payload["portfolio_snapshot"]
                    has_payload_portfolio = True

            # If payload contains a portfolio_snapshot (even if empty) or if both exist
            if has_payload_portfolio:
                if not payload_portfolio and existing_portfolio:
                    # Incoming payload has an empty portfolio snapshot, but we have existing holdings.
                    # Per MERGE_BY_TICKER_PRESERVE_UNTOUCHED_TICKERS, do NOT wipe the existing portfolio!
                    # Preserve existing holdings unless explicitly deleted via DELETE_FIELD.
                    merged_portfolio = existing_portfolio
                else:
                    if ep_source is not None:
                        # Directives Supremacy (ENH_31-P): Overwrite active state fields with payload counterparts to maintain zero-drift
                        # However, we must preserve read-only local database properties (WAC and historical_context) from the existing state
                        # to prevent "state amnesia" or silent data loss on ingestion.
                        existing_by_ticker = {}
                        for item in existing_portfolio:
                            if isinstance(item, dict) and item.get("ticker"):
                                existing_by_ticker[item["ticker"].upper()] = item
                        
                        for item in payload_portfolio:
                            if isinstance(item, dict) and item.get("ticker"):
                                ticker_upper = item["ticker"].upper()
                                if ticker_upper in existing_by_ticker:
                                    existing_item = existing_by_ticker[ticker_upper]
                                    # If the payload item does not have 'wac' or it is 0/None, carry it forward from existing
                                    if ("wac" not in item or item.get("wac") in (0, 0.0, None)) and "wac" in existing_item:
                                        item["wac"] = existing_item["wac"]
                                    # Carry forward 'historical_context' if not present in the payload
                                    if ("historical_context" not in item or not item.get("historical_context")) and "historical_context" in existing_item:
                                        item["historical_context"] = existing_item["historical_context"]
                        merged_portfolio = payload_portfolio
                    else:
                        merged_portfolio = _merge_portfolio(existing_portfolio, payload_portfolio)
                
                payload_without_portfolio = _deep_merge({}, payload)
                if has_layer_model:
                    if "portfolio_snapshot" in payload_without_portfolio.get("mutable_state", {}):
                        payload_without_portfolio["mutable_state"].pop("portfolio_snapshot")
                else:
                    payload_without_portfolio.pop("portfolio_snapshot", None)
                    
                merged_ssot = _deep_merge(existing_ssot, payload_without_portfolio)
                
                if has_layer_model:
                    if "mutable_state" not in merged_ssot: merged_ssot["mutable_state"] = {}
                    merged_ssot["mutable_state"]["portfolio_snapshot"] = merged_portfolio
                else:
                    merged_ssot["portfolio_snapshot"] = merged_portfolio
            else:
                # portfolio_snapshot is missing entirely from incoming payload, so preserve existing portfolio
                merged_ssot = _deep_merge(existing_ssot, payload)
        else:
            merged_ssot = payload
            
        # Strip trade_lessons keys from ROOT, MUTABLE_STATE, and EXECUTION_PAYLOAD — they are routed to trade_lessons.json
        for tl_key in ("trade_lessons", "new_trade_lessons", "compressed_trade_lessons", "rule_mutations", "trade_lessons_revision"):
            merged_ssot.pop(tl_key, None)
            if "mutable_state" in merged_ssot:
                merged_ssot["mutable_state"].pop(tl_key, None)
            if "EXECUTION_PAYLOAD" in merged_ssot:
                merged_ssot["EXECUTION_PAYLOAD"].pop(tl_key, None)

        # SSoT schema validation — prune non-canonical top-level keys to prevent drift
        CANONICAL_SSOT_KEYS = {
            "state_context", "portfolio_snapshot", "forensic_intelligence",
            "runtime_flags", "macro_calendar_shield", "active_orders",
            "fin_account_gate", "registry_pointers", "overnight_posture",
            "strategy_timing", "lesson_integration", "immutable_background", "mutable_state",
            "remaining_cash_usd", "_meta"
        }
        
        # Prune from root
        non_canonical = [k for k in merged_ssot if k not in CANONICAL_SSOT_KEYS]
        for k in non_canonical:
            merged_ssot.pop(k)
            
        # Prune from mutable_state if using Layer Model
        if has_layer_model and "mutable_state" in merged_ssot and isinstance(merged_ssot["mutable_state"], dict):
            non_canonical_mutable = [k for k in merged_ssot["mutable_state"] if k not in CANONICAL_SSOT_KEYS]
            for k in non_canonical_mutable:
                merged_ssot["mutable_state"].pop(k)

        # Process Explicit Deletions
        del_list = payload.get("DELETE_FIELD", [])
        if not del_list and "mutable_state" in payload:
            del_list = payload["mutable_state"].get("DELETE_FIELD", [])
        if "DELETE_FIELD (optional)" in payload: del_list.extend(payload["DELETE_FIELD (optional)"])
        if isinstance(del_list, list) and len(del_list) > 0:
            target_state = merged_ssot.get("mutable_state", merged_ssot) if has_layer_model else merged_ssot
            _process_deletions(target_state, del_list)
            # Remove the literal deletion arrays from state if they merged over
            if has_layer_model and "mutable_state" in merged_ssot:
                merged_ssot["mutable_state"].pop("DELETE_FIELD", None)
                merged_ssot["mutable_state"].pop("DELETE_FIELD (optional)", None)
            merged_ssot.pop("DELETE_FIELD", None)
            merged_ssot.pop("DELETE_FIELD (optional)", None)

        with open('context/ssot.json', 'w') as f:
            json.dump(merged_ssot, f, indent=2)
            
        def _normalize_lessons(lessons_list):
            normalized = []
            for i, item in enumerate(lessons_list):
                if isinstance(item, str):
                    normalized.append({"id": i + 1, "rule": item})
                elif isinstance(item, dict):
                    # Keep existing keys (like context or rule), just ensure id is set/updated
                    new_item = item.copy()
                    new_item["id"] = i + 1
                    if "rule" not in new_item and "lesson" in new_item:
                        new_item["rule"] = new_item.pop("lesson")
                    normalized.append(new_item)
                else:
                    normalized.append({"id": i + 1, "rule": str(item)})
            return normalized

        # Trade lessons
        lessons_file = 'context/trade_lessons.json'
        incoming_lessons = []
        if extracted_compressed and isinstance(extracted_compressed, list):
            incoming_lessons.extend(extracted_compressed)
        if extracted_new and isinstance(extracted_new, list):
            incoming_lessons.extend(extracted_new)
        if extracted_trade_lessons and isinstance(extracted_trade_lessons, list):
            incoming_lessons.extend(extracted_trade_lessons)

        if incoming_lessons:
            existing_lessons = []
            existing_data = None
            if os.path.exists(lessons_file):
                try:
                    with open(lessons_file, 'r') as f:
                        existing_data = json.load(f)
                        if isinstance(existing_data, dict):
                            existing_lessons = existing_data.get("trade_lessons", [])
                        elif isinstance(existing_data, list):
                            existing_lessons = existing_data
                except json.JSONDecodeError:
                    pass
            
            # Build lookup of existing lessons by id for upsert
            # Build lookup of existing lessons by id and text
            existing_by_id = {}
            existing_by_text = {}
            for idx, lesson in enumerate(existing_lessons):
                if isinstance(lesson, dict):
                    if "id" in lesson:
                        existing_by_id[lesson["id"]] = lesson
                    rule_text = lesson.get("rule", lesson.get("lesson", "")).strip().lower()
                    if rule_text: existing_by_text[rule_text] = lesson
                elif isinstance(lesson, str):
                    rule_text = lesson.strip().lower()
                    if rule_text: existing_by_text[rule_text] = lesson
            
            kept_ids = set()
            new_compiled_lessons = []

            for item in incoming_lessons:
                if isinstance(item, str):
                    match = re.match(r'^(\d+)-(\d+).*PERMANENT', item, re.IGNORECASE)
                    if match:
                        start_id = int(match.group(1))
                        end_id = int(match.group(2))
                        kept_ids.update(range(start_id, end_id + 1))
                    else:
                        rule_text = item.strip().lower()
                        if rule_text and rule_text in existing_by_text:
                            continue
                        new_compiled_lessons.append(item)
                        if rule_text: existing_by_text[rule_text] = item
                elif isinstance(item, dict):
                    item_id = item.get("id")
                    rule_text = item.get("rule", item.get("lesson", "")).strip().lower()
                    
                    if item_id is not None:
                        kept_ids.add(item_id)
                        if item_id in existing_by_id:
                            # Upsert by ID
                            existing_by_id[item_id].update(item)
                            new_compiled_lessons.append(existing_by_id[item_id])
                            
                            # Update text registry too
                            if rule_text: existing_by_text[rule_text] = existing_by_id[item_id]
                            continue
                            
                    # If no ID match, check by text
                    if rule_text and rule_text in existing_by_text:
                        # Existing lesson found by text match
                        existing = existing_by_text[rule_text]
                        if isinstance(existing, dict):
                            # Restore its original ID to prevent disruption
                            orig_id = existing.get("id")
                            existing.update(item)
                            if orig_id is not None:
                                existing["id"] = orig_id
                        continue
                        
                    # It's completely new
                    new_compiled_lessons.append(item)
                    if rule_text: existing_by_text[rule_text] = item
                else:
                    new_compiled_lessons.append(item)

            # Re-inject preserved lessons that were NOT explicitly updated
            preserved_lessons = []
            for lesson in existing_lessons:
                if isinstance(lesson, dict):
                    lid = lesson.get("id")
                    updated = False
                    if lid is not None:
                        for new_lesson in new_compiled_lessons:
                            if isinstance(new_lesson, dict) and new_lesson.get("id") == lid:
                                updated = True
                                break
                    if not updated:
                        preserved_lessons.append(lesson)
                elif isinstance(lesson, str):
                    preserved_lessons.append(lesson)

            # Combine preserved lessons with newly updated/added ones
            existing_lessons = preserved_lessons + new_compiled_lessons
            
            final_normalized = _normalize_lessons(existing_lessons)
                
            if isinstance(existing_data, dict):
                existing_data["trade_lessons"] = final_normalized
                output_data = existing_data
            else:
                output_data = {"trade_lessons": final_normalized}
                
            with open(lessons_file, 'w') as f:
                json.dump(output_data, f, indent=2)
        # Rule mutations
        if extracted_mutations and isinstance(extracted_mutations, list):
            rules_file = os.path.join('gem_trading_rules', 'rules.json')
            if os.path.exists(rules_file):
                try:
                    with open(rules_file, 'r') as f:
                        current_rules = json.load(f)
                except:
                    current_rules = None
                    
                if current_rules:
                    applied_patches = 0
                    for mutation in payload["rule_mutations"]:
                        if isinstance(mutation, dict) and "path" in mutation and "value" in mutation:
                            path = mutation["path"]
                            val = mutation["value"]
                            if isinstance(path, list) and len(path) > 0:
                                target = current_rules
                                valid_path = True
                                for p in path[:-1]:
                                    if p in target and isinstance(target[p], dict):
                                        target = target[p]
                                    else:
                                        valid_path = False
                                        break
                                if valid_path:
                                    target[path[-1]] = val
                                    applied_patches += 1
                                    
                    if applied_patches > 0:
                        with open(rules_file, 'w') as f:
                            json.dump(current_rules, f, indent=4)
                        print(f"⚠️ RULES MUTATED LOCALLY ({applied_patches} patches applied).")
                        
        return JSONResponse({"status": "success", "message": "Payload ingested successfully"})
    except Exception as e:
        print(f"PASTE ERROR: {e}")
        print(f"RAW PAYLOAD TYPE: {type(clip_data)}, LENGTH: {len(clip_data)}")
        print(f"PAYLOAD TRUNCATED: {repr(clip_data[:500])}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)


# COLOR CODES
# -----------------------------
os.system("")
GREEN = "\033[38;5;46m"
RED = "\033[38;5;196m"
YELLOW = "\033[38;5;226m"
CYAN = "\033[38;5;51m"
BLUE = "\033[38;5;39m"
PURPLE = "\033[38;5;129m"
WHITE = "\033[38;5;255m"
RESET = "\033[0m"
BOLD = "\033[1m"
BG_NORMAL = ""
BG_STRIPE = "\033[48;5;236m"

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
})

class MarketDataCache:
    def __init__(self):
        self.history: dict[str, pd.DataFrame] = {}
        self.technicals: dict[str, dict] = {}
        self.prices: dict[str, float] = {}
        self.gaps: dict[str, float] = {}
        self.session_change: dict[str, float] = {}
        self.vwaps: dict[str, float] = {}
        self.volumes: dict[str, int] = {}
        self.gex: dict[str, dict] = {}
        self.session: dict[str, str] = {}
        self.session_liquidity: dict[str, str] = {}
        self.pre_market_change: dict[str, float] = {}
        self.after_hours_change: dict[str, float] = {}
        self.overnight_return: dict[str, float] = {}
        self.pre_market_price: dict[str, float] = {}
        self.after_hours_price: dict[str, float] = {}
        self.pre_market_volume: dict[str, int] = {}
        self.after_hours_volume: dict[str, int] = {}
        self.ssr_active: dict[str, bool] = {} # New: SSR Trigger Tracker
        self.cycles: int = 0
        self.vwap_pointer: int = 0
        
        # Cache structures per v10.61-Scout-Ticker-Validation specifications
        self.last_chart_fetch: dict[str, float] = {}
        self.chart_cache: dict[str, tuple] = {}
        self.last_premarket_vol_fetch: dict[str, float] = {}
        self.premarket_vol_cache: dict[str, int] = {}
        self.last_gex_fetch: dict[str, float] = {}
        self.gex_cache: dict[str, dict] = {}

    def clear(self):
        self.__init__()

cache = MarketDataCache()
yf_data = YfData()  # Initialize yfinance data handler (handles crumbs/cookies)

# -----------------------------
# UTILS
# -----------------------------
def safe_yf_get(url, timeout=3):
    """Fallback fetcher for yfinance curl_cffi TLS errors."""
    try:
        r = yf_data.get(url, timeout=timeout)
        if r.status_code == 200:
            return r
    except Exception:
        pass
    # Fallback to standard requests.Session if curl_cffi fails internally
    return session.get(url, timeout=timeout)

def get_market_status():
    ny_now = datetime.now(ZoneInfo("America/New_York"))
    t = ny_now.time()
    if ny_now.weekday() >= 5:
        return "CLOSED"
    if time(4, 0) <= t < time(9, 30):
        return "PRE-MARKET"
    if time(9, 30) <= t < time(16, 0):
        return "OPEN"
    if time(16, 0) <= t < time(20, 0):
        return "AFTER-HOURS"
    return "CLOSED"

def to_float(val):
    try:
        if isinstance(val, (pd.Series, pd.DataFrame)):
            return float(val.iloc[-1])
        return float(val)
    except:
        return 0.0

def format_volume(vol, symbol):
    try:
        if vol >= 1_000_000_000:
            return f"{vol/1_000_000_000:.2f}B"
        if vol >= 1_000_000:
            return f"{vol/1_000_000:.2f}M"
        if vol >= 1_000:
            return f"{vol/1_000:.0f}K"
        return str(int(vol))
    except:
        return "-"

def get_visible_length(s):
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return len(ansi_escape.sub('', s))
def calculate_gamma(S, K, T, r, sigma):
    """
    Black-Scholes Gamma.
    S     = spot price
    K     = strike
    T     = time to expiry (years)
    r     = risk-free rate
    sigma = implied volatility (decimal, e.g. 0.35)
    """

    try:
        if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
            return 0.0

        d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        gamma = norm.pdf(d1) / (S * sigma * np.sqrt(T))

        # Numerical safety
        if np.isnan(gamma) or np.isinf(gamma):
            return 0.0

        return float(gamma)

    except Exception:
        return 0.0

def calculate_delta(S, K, T, r, sigma, option_type):
    try:
        if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
            return 0.0
        d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        if option_type == 'call':
            return float(norm.cdf(d1))
        elif option_type == 'put':
            return float(norm.cdf(d1) - 1.0)
        return 0.0
    except:
        return 0.0

def get_premarket_volume_chart(symbol):
    """
    Dedicated fetch for pre-market volume (04:00-09:30 ET today) via Yahoo chart API.
    This is more reliable than the batch quote endpoint which often omits preMarketVolume
    during regular hours.
    """
    try:
        ny_tz = ZoneInfo("America/New_York")
        now = datetime.now(ny_tz)
        # Build period for today's premarket window: 04:00 to 09:30 ET
        pre_start = now.replace(hour=4, minute=0, second=0, microsecond=0)
        pre_end = now.replace(hour=9, minute=30, second=0, microsecond=0)
        # Only fetch if we're past the premarket window
        if now < pre_start:
            return 0
        # Cap end at market open or current time, whichever is earlier
        if now < pre_end:
            pre_end = now
        ts1 = int(pre_start.timestamp())
        ts2 = int(pre_end.timestamp())
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1={ts1}&period2={ts2}&interval=1m&includePrePost=true"
        r = safe_yf_get(url, timeout=3)
        data = r.json()
        result = data['chart']['result'][0]
        volumes = result['indicators']['quote'][0].get('volume', [])
        total = sum(v for v in volumes if v is not None and v > 0)
        return total
    except:
        return 0

# -----------------------------
# FETCHERS
# -----------------------------
def get_live_chart_data(symbol, status):
    """
    Fetches 5-day 1m chart data to ensure we have the latest session (handling holidays).
    Calculates:
    - live_price (last close)
    - total_vol (volume since session start)
    - pre_vol (volume from 04:00 to 09:30 ET today)
    - after_hours_vol (volume from 16:00 to 20:00 ET today)
    """
    try:
        # Use range=5d to bridge weekends/holidays and ensure we get today's data
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1m&includePrePost=true"
        # Use safe_yf_get to fallback if curl_cffi fails
        r = safe_yf_get(url, timeout=3)
        data = r.json()
        result = data['chart']['result'][0]
        timestamps = result['timestamp']
        quote = result['indicators']['quote'][0]
        closes = quote.get('close', [])
        volumes = quote.get('volume', [])

        live_price = 0.0
        if closes:
            valid_closes = [c for c in closes if c is not None]
            if valid_closes:
                live_price = float(valid_closes[-1])

        ny_tz = ZoneInfo("America/New_York")
        now = datetime.now(ny_tz)

        pre_vol = 0
        reg_vol = 0
        post_vol = 0
        total_session_vol = 0 # Volume matching current status

        if volumes and timestamps:
            # First pass: find the most recent date available in the dataset
            latest_date = None
            for ts in reversed(timestamps):
                dt_ny = datetime.fromtimestamp(ts, ZoneInfo("UTC")).astimezone(ny_tz)
                latest_date = dt_ny.date()
                break # the last timestamp is the most recent date

            cutoff_ts = 0
            # Only use cutoff if today is the latest date (meaning the market is actively open)
            if latest_date == now.date():
                if status == "OPEN":
                    cutoff_dt = now.replace(hour=9, minute=30, second=0, microsecond=0)
                    cutoff_ts = cutoff_dt.timestamp()
                elif status == "AFTER-HOURS":
                    cutoff_dt = now.replace(hour=16, minute=0, second=0, microsecond=0)
                    cutoff_ts = cutoff_dt.timestamp()

            for ts, v in zip(timestamps, volumes):
                if v is None: 
                    continue
                
                dt_utc = datetime.fromtimestamp(ts, ZoneInfo("UTC"))
                dt_ny = dt_utc.astimezone(ny_tz)
                
                # Filter for the most recent active trading day
                if dt_ny.date() != latest_date:
                    continue

                # Calculate specific session volumes
                # Pre: 04:00 <= t < 09:30
                # Reg: 09:30 <= t < 16:00
                # Post: 16:00 <= t < 20:00
                
                t_idx = dt_ny.time()
                t_0400 = time(4, 0)
                t_0930 = time(9, 30)
                t_1600 = time(16, 0)
                t_2000 = time(20, 0)

                # Pre-Market
                if t_0400 <= t_idx < t_0930:
                    pre_vol += v
                # Regular
                elif t_0930 <= t_idx < t_1600:
                    reg_vol += v
                # Post-Market
                elif t_1600 <= t_idx < t_2000:
                    post_vol += v

                # Session-specific calculation (for fallback logic compatibility)
                if ts >= cutoff_ts:
                    # If status is OPEN, this sums volume from 09:30 onwards
                    # If status is AFTER-HOURS, this sums volume from 16:00 onwards
                    total_session_vol += v

        return live_price, total_session_vol, pre_vol, post_vol
    except:
        return 0.0, 0, 0, 0

def get_true_intraday_vwap(symbol, status):
    try:
        ny_tz = ZoneInfo("America/New_York")
        now = datetime.now(ny_tz)

        if status == "PRE-MARKET":
            anchor_time = now.replace(hour=4, minute=0, second=0, microsecond=0)
            include_pre = "true"
        else:
            anchor_time = now.replace(hour=9, minute=30, second=0, microsecond=0)
            include_pre = "false"

        if now < anchor_time:
            return 0.0

        ts_open = int(anchor_time.timestamp())
        ts_now = int(now.timestamp())

        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1={ts_open}&period2={ts_now}&interval=1m&includePrePost={include_pre}"
        # Use safe_yf_get to fallback if curl_cffi fails
        r = safe_yf_get(url, timeout=3)
        data = r.json()
        result = data['chart']['result'][0]
        indicators = result['indicators']['quote'][0]
        closes = indicators.get('close', [])
        highs = indicators.get('high', [])
        lows = indicators.get('low', [])
        volumes = indicators.get('volume', [])

        valid = [(h, l, c, v) for h, l, c, v in zip(highs, lows, closes, volumes)
                 if c is not None and h is not None and l is not None and v is not None and v > 0]
        if not valid:
            return 0.0

        total_vp = sum(((h + l + c) / 3) * v for h, l, c, v in valid)
        total_v = sum(v for _, _, _, v in valid)
        if total_v > 0:
            return total_vp / total_v
        return 0.0
    except:
        return 0.0

def fallback_vwap(symbol):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=1m"
        # Use safe_yf_get to fallback if curl_cffi fails
        r = safe_yf_get(url, timeout=2)
        data = r.json()
        result = data['chart']['result'][0]
        q = result['indicators']['quote'][0]
        closes = q.get('close', [])
        highs = q.get('high', [])
        lows = q.get('low', [])
        volumes = q.get('volume', [])
        vp = 0.0
        tv = 0.0
        for h, l, c, v in zip(highs, lows, closes, volumes):
            if c is None or h is None or l is None or v is None or v <= 0:
                continue
            typical_price = (h + l + c) / 3
            vp += typical_price * v
            tv += v
        if tv > 0:
            return vp / tv
        return 0.0
    except:
        return 0.0

def get_finnhub_quote(symbol):
    if not USE_FINNHUB:
        return None, None
    try:
        url = f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={FINNHUB_API_KEY}"
        r = session.get(url, timeout=2)
        if r.status_code != 200:
            return None, None
        data = r.json()
        c = float(data.get('c', 0))
        pc = float(data.get('pc', 0))
        if c > 0:
            return c, pc
    except:
        pass
    return None, None

def get_polygon_history_df(symbol):
    if not USE_POLYGON:
        return pd.DataFrame()
    end_dt = datetime.now().strftime('%Y-%m-%d')
    start_dt = (datetime.now() - timedelta(days=730)).strftime('%Y-%m-%d')
    url = f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/1/day/{start_dt}/{end_dt}?adjusted=true&sort=asc&apiKey={POLYGON_API_KEY}"
    try:
        t_time.sleep(15)
        r = session.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json()
            if data.get('resultsCount', 0) > 0:
                results = data['results']
                df = pd.DataFrame(results)
                df['Date'] = pd.to_datetime(df['t'], unit='ms')
                df.set_index('Date', inplace=True)
                df.rename(columns={'c': 'Close', 'h': 'High', 'l': 'Low', 'o': 'Open', 'v': 'Volume'}, inplace=True)
                return df[['Open', 'High', 'Low', 'Close', 'Volume']]
    except:
        pass
    return pd.DataFrame()

def polygon_volume(symbol):
    if not USE_POLYGON:
        return None
    try:
        url = f"https://api.polygon.io/v2/aggs/ticker/{symbol}/prev?adjusted=true&apiKey={POLYGON_API_KEY}"
        r = session.get(url, timeout=2)
        if r.status_code == 200:
            data = r.json()
            if data.get('resultsCount', 0) > 0:
                return data['results'][0].get('v', None)
    except:
        pass
    return None

def get_previous_close(symbol):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d"
        # Use safe_yf_get to fallback if curl_cffi fails
        r = safe_yf_get(url, timeout=2)
        data = r.json()
        result = data['chart']['result'][0]
        q = result['indicators']['quote'][0]
        closes = q.get('close', [])
        valid = [c for c in closes if c is not None]
        if len(valid) >= 2:
            return float(valid[-2])
    except:
        pass
    return None

def _get_chart_open(symbol):
    """Fetch today's true regular-session opening price from 1-minute chart data.
    More reliable than batch quote's regularMarketOpen for small-cap stocks."""
    try:
        ny_tz = ZoneInfo("America/New_York")
        now = datetime.now(ny_tz)
        open_dt = now.replace(hour=9, minute=30, second=0, microsecond=0)
        # Fetch a small window around market open
        end_dt = now.replace(hour=9, minute=35, second=0, microsecond=0)
        if now < end_dt:
            end_dt = now
        ts1 = int(open_dt.timestamp())
        ts2 = int(end_dt.timestamp())
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1={ts1}&period2={ts2}&interval=1m&includePrePost=false"
        r = safe_yf_get(url, timeout=3)
        data = r.json()
        result = data['chart']['result'][0]
        opens = result['indicators']['quote'][0].get('open', [])
        valid_opens = [o for o in opens if o is not None and o > 0]
        if valid_opens:
            return float(valid_opens[0])
    except:
        pass
    return None

def get_batch_quotes(symbols):
    try:
        syms = ",".join(symbols)
        url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={syms}"
        # Use safe_yf_get for authenticated request (handles crumbs/cookies + fallback)
        r = safe_yf_get(url, timeout=3)
        data = r.json()
        return {q['symbol']: q for q in data['quoteResponse']['result']}
    except:
        return {}

# -----------------------------
# GEX LOGIC
# -----------------------------


def get_gex_profile(ticker_obj, spot_price):
    """
    Calculates GEX, Gamma Flip, and Inventory Velocity Delta (Net Delta).
    """
    default_res = {
        'net_gex': 0.0, 
        'flip_price': 0.0, 
        'inventory_velocity_delta': 0.0,
        'gex_slope': 0.0,
        'flip_proximity_percent': 0.0,
        'strike_oi_magnet': 0.0
    }
    if spot_price is None or spot_price <= 0:
        return default_res

    try:
        expirations = ticker_obj.options
        if not expirations:
            return default_res
        
        target_exps = expirations[:2]
        r = 0.045 # Approx risk-free rate
        ny_now = datetime.now(ZoneInfo("America/New_York"))
        
        option_inventory = []
        strike_oi_map = {}

        for exp_date_str in target_exps:
            try:
                chain = ticker_obj.option_chain(exp_date_str)
                
                exp_dt = datetime.strptime(exp_date_str, "%Y-%m-%d").replace(tzinfo=ZoneInfo("America/New_York"))
                exp_dt = exp_dt.replace(hour=16, minute=0, second=0)
                T = (exp_dt - ny_now).total_seconds() / (365 * 24 * 3600)
                if T <= 0.001: T = 0.001

                if chain.calls is not None and not chain.calls.empty:
                    for _, row in chain.calls.iterrows():
                        if pd.isna(row.get('openInterest')) or row.get('openInterest') <= 0: continue
                        if pd.isna(row.get('impliedVolatility')) or row.get('impliedVolatility') <= 0: continue
                        k = float(row['strike'])
                        oi = float(row['openInterest'])
                        strike_oi_map[k] = strike_oi_map.get(k, 0.0) + oi
                        option_inventory.append({
                            'K': float(row['strike']), 'T': T, 'sigma': float(row['impliedVolatility']),
                            'oi': float(row['openInterest']), 'type': 'call'
                        })

                if chain.puts is not None and not chain.puts.empty:
                    for _, row in chain.puts.iterrows():
                        if pd.isna(row.get('openInterest')) or row.get('openInterest') <= 0: continue
                        if pd.isna(row.get('impliedVolatility')) or row.get('impliedVolatility') <= 0: continue
                        k = float(row['strike'])
                        oi = float(row['openInterest'])
                        strike_oi_map[k] = strike_oi_map.get(k, 0.0) + oi
                        option_inventory.append({
                            'K': float(row['strike']), 'T': T, 'sigma': float(row['impliedVolatility']),
                            'oi': float(row['openInterest']), 'type': 'put'
                        })
            except:
                continue

        if not option_inventory:
            return default_res

        magnet_price = 0.0
        if strike_oi_map:
            magnet_price = max(strike_oi_map, key=lambda k: strike_oi_map.get(k, 0.0))

        # 1. Calc Current GEX and Net Delta (Inventory Velocity Delta)
        net_gex = 0.0
        net_delta = 0.0
        
        for opt in option_inventory:
            gamma = calculate_gamma(spot_price, opt['K'], opt['T'], r, opt['sigma'])
            delta = calculate_delta(spot_price, opt['K'], opt['T'], r, opt['sigma'], opt['type'])
            
            # Standard GEX = Gamma * S^2 * OI (Dollar Gamma per 1% move)
            # Origin formula had extra * 100 which implied 100% move or share-multiplier duplication
            # We want "Notional value of delta change for 1% move"
            term = gamma * opt['oi'] * (spot_price**2) 
            if opt['type'] == 'call':
                net_gex += term
                net_delta += (-delta * opt['oi'] * 100) # Dealer Short Call
            else:
                net_gex -= term
                net_delta += (-delta * opt['oi'] * 100) # Dealer Short Put

        avg_vol = 0
        try:
            avg_vol = ticker_obj.fast_info.three_month_average_volume
        except:
            pass
            
        final_gex = net_gex
        final_slope = 0.0
        
        if avg_vol and avg_vol > 0:
            final_gex = net_gex / (avg_vol * spot_price)

        # 2. Calc Gamma Flip (Scan +/- 50%)
        flip_price = 0.0
        low_p = spot_price * 0.50
        high_p = spot_price * 1.50
        steps = 100
        step_size = (high_p - low_p) / steps
        prices = [low_p + i*step_size for i in range(steps+1)]
        gex_values = []
        
        for p_sim in prices:
            g_sim = 0.0
            for opt in option_inventory:
                val = calculate_gamma(p_sim, opt['K'], opt['T'], r, opt['sigma'])
                term = val * opt['oi'] * (p_sim**2) # Removed * 100 scaling
                if opt['type'] == 'call': g_sim += term
                else: g_sim -= term
            gex_values.append(g_sim)
            
        for i in range(len(gex_values)-1):
            if (gex_values[i] > 0 and gex_values[i+1] < 0) or (gex_values[i] < 0 and gex_values[i+1] > 0):
                y1, y2 = gex_values[i], gex_values[i+1]
                x1, x2 = prices[i], prices[i+1]
                if (y2 - y1) != 0:
                    flip_price = x1 - y1 * (x2 - x1) / (y2 - y1)
                else:
                    flip_price = x1
                break

        # 3. Calc GEX Slope (Local derivative at spot)
        p_up = spot_price * 1.01
        gex_up = 0.0
        for opt in option_inventory:
            val = calculate_gamma(p_up, opt['K'], opt['T'], r, opt['sigma'])
            term = val * opt['oi'] * (p_up**2) # Removed * 100 scaling
            if opt['type'] == 'call': gex_up += term
            else: gex_up -= term
        
        raw_slope = (gex_up - net_gex) / (p_up - spot_price)
        final_slope = raw_slope
        if avg_vol and avg_vol > 0:
            final_slope = raw_slope / (avg_vol * spot_price)

        flip_prox = 0.0
        if flip_price > 0:
            flip_prox = (spot_price - flip_price) / spot_price

        return {
            'net_gex': final_gex,
            'flip_price': flip_price,
            'inventory_velocity_delta': net_delta,
            'gex_slope': final_slope,
            'flip_proximity_percent': flip_prox,
            'strike_oi_magnet': magnet_price
        }

    except Exception:
        return default_res


# -----------------------------
# LOGIC (UPDATED)
# -----------------------------

def update_history_and_technicals(symbol, t_obj):
    try:
        hist = t_obj.history(period="2y", interval="1d")
        if hist.empty:
            hist = t_obj.history(period="1y", interval="1d")
        if hist.empty:
            hist = t_obj.history(period="3mo", interval="1d")
        if hist.empty:
            raise ValueError("Empty YF")
    except:
        hist = get_polygon_history_df(symbol)

    if hist.empty:
        # Create a dummy row to prevent repeated slow refetches
        hist = pd.DataFrame([{
            'Open': 0.0, 'High': 0.0, 'Low': 0.0, 'Close': 0.0, 'Volume': 0
        }], index=[pd.Timestamp.now()])
        cache.history[symbol] = hist
        cache.technicals[symbol] = {
            "SMA_20": None,
            "SMA_50": None,
            "SMA_200": None,
            "RSI": 50.0,
            "ATR_Pct": 0.0,
            "ATR": 0.0,
            "Trend_Score": 0,
            "Last_Reg_Close": 0.0
        }
        return

    cache.history[symbol] = hist

    if not hist.empty:
        try:
            close = hist['Close']
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]

            # --- SAFER SMA CHECKS ---
            sma_20 = to_float(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else None
            sma_50 = to_float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
            sma_200 = to_float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None

            # --- Helper to safely get the last value from a Series or scalar ---
            def _last(val):
                if isinstance(val, (int, float, np.floating, np.integer)):
                    return float(val)
                return float(val.iloc[-1])

            # --- RSI (Wilder) ---
            delta = close.diff()
            gain = (delta.where(delta > 0, 0)).ewm(com=13, adjust=False).mean()
            loss = (-delta.where(delta < 0, 0)).ewm(com=13, adjust=False).mean()
            rs = gain / (loss + 1e-9)
            rsi_val = _last(100 - (100 / (1 + rs)))

            # --- ATR (Wilder) ---
            tr = pd.concat([
                hist['High'] - hist['Low'],
                (hist['High'] - close.shift(1)).abs(),
                (hist['Low'] - close.shift(1)).abs()
            ], axis=1).max(axis=1)

            atr = _last(tr.ewm(alpha=1/14, adjust=False).mean())
            last_close = _last(close)
            atr_pct = (atr / last_close) * 100 if last_close != 0 else 0

            # --- Previous Regular Close ---
            last_reg_close = 0.0
            if hasattr(t_obj, 'fast_info'):
                try:
                    last_reg_close = float(t_obj.fast_info.previous_close)
                except:
                    pass

            if last_reg_close == 0.0 and len(close) >= 2:
                if isinstance(close, (int, float, np.floating, np.integer)):
                    last_reg_close = float(close)
                else:
                    last_reg_close = to_float(close.iloc[-2])

            if last_reg_close == 0.0:
                alt_pc = get_previous_close(symbol)
                if alt_pc:
                    last_reg_close = alt_pc

            # --- Trend Score (Weighted — SMA200 cross is ±2) ---
            trend_score = 0
            if sma_20 is not None and sma_50 is not None:
                trend_score += 1 if sma_20 > sma_50 else -1
            if sma_50 is not None and sma_200 is not None:
                trend_score += 2 if sma_50 > sma_200 else -2  # Golden/Death cross weighted 2x
            if sma_20 is not None:
                trend_score += 1 if last_close > sma_20 else -1

            cache.technicals[symbol] = {
                "SMA_20": sma_20,
                "SMA_50": sma_50,
                "SMA_200": sma_200,
                "RSI": to_float(rsi_val),
                "ATR_Pct": to_float(atr_pct),
                "ATR": to_float(atr),
                "Trend_Score": int(trend_score),
                "Last_Reg_Close": last_reg_close
            }

        except Exception as e:
            print("Tech error:", e)
            pass


def update_price_tick(symbol, t_obj, status, quote_data=None):
    price = 0.0
    vol = 0
    used_batch = False

    # --- SESSION TAG ---
    cache.session[symbol] = status

    # --- SESSION LIQUIDITY ---
    cache.session_liquidity[symbol] = (
        "LOW" if status in ("AFTER-HOURS", "PRE-MARKET") else "HIGH"
    )

    # --- Extract session prices ---
    pre_price = None
    post_price = None
    reg_price = None
    reg_open = None
    reg_low = None
    batch_prev_close = None
    pre_vol = 0
    post_vol = 0

    if quote_data:
        try:
            pre_price = quote_data.get('preMarketPrice')
            post_price = quote_data.get('postMarketPrice')
            reg_price = quote_data.get('regularMarketPrice')
            reg_open = quote_data.get('regularMarketOpen')
            reg_low = quote_data.get('regularMarketDayLow') # For SSR trigger check
            batch_prev_close = quote_data.get('regularMarketPreviousClose')
            vol = int(quote_data.get('regularMarketVolume', 0) or 0)
            pre_vol = int(quote_data.get('preMarketVolume', 0) or 0)
            post_vol = int(quote_data.get('postMarketVolume', 0) or 0)
            used_batch = True
        except:
            pass

    # Attempt to fetch missing pre/post prices from fast_info as a first-line fallback
    try:
        fi = t_obj.fast_info
        if not pre_price and getattr(fi, 'pre_market_price', None):
            pre_price = float(fi.pre_market_price)
        if not post_price and getattr(fi, 'post_market_price', None):
            post_price = float(fi.post_market_price)
    except:
        pass

    # --- Session-aware price selection ---
    if status == "PRE-MARKET" and pre_price:
        price = float(pre_price)
    elif status in ("AFTER-HOURS", "CLOSED") and post_price:
        price = float(post_price)
    elif reg_price:
        price = float(reg_price)

    # --- Fallbacks ---
    needs_pre_vol = (pre_vol == 0 and cache.pre_market_volume.get(symbol, 0) == 0)
    needs_post_vol = (status in ("AFTER-HOURS", "CLOSED") and post_vol == 0)

    if price == 0 or needs_post_vol:
        now_ts = t_time.time()
        if symbol in cache.last_chart_fetch and (now_ts - cache.last_chart_fetch[symbol]) < 60:
            api_price, api_vol, api_pre_vol, api_post_vol = cache.chart_cache[symbol]
        else:
            api_price, api_vol, api_pre_vol, api_post_vol = get_live_chart_data(symbol, status)
            if api_price > 0 or api_vol > 0 or api_pre_vol > 0 or api_post_vol > 0:
                cache.last_chart_fetch[symbol] = now_ts
                cache.chart_cache[symbol] = (api_price, api_vol, api_pre_vol, api_post_vol)

        if price == 0 and api_price > 0:
            price = api_price
        if vol == 0 and api_vol > 0:
            vol = api_vol
        
        # Fallback for pre-market volume if batch failed
        if api_pre_vol > 0 and pre_vol == 0:
            pre_vol = api_pre_vol
        if api_post_vol > 0 and post_vol == 0:
            post_vol = api_post_vol

    # Dedicated pre-market volume fetch (more reliable than batch quote during regular hours)
    if needs_pre_vol:
        now_ts = t_time.time()
        if symbol in cache.last_premarket_vol_fetch and (now_ts - cache.last_premarket_vol_fetch[symbol]) < 90:
            chart_pre_vol = cache.premarket_vol_cache.get(symbol, 0)
        else:
            chart_pre_vol = get_premarket_volume_chart(symbol)
            cache.last_premarket_vol_fetch[symbol] = now_ts
            cache.premarket_vol_cache[symbol] = chart_pre_vol

        if chart_pre_vol > 0:
            pre_vol = chart_pre_vol

    if price == 0 and USE_FINNHUB:
        fh_c, fh_pc = get_finnhub_quote(symbol)
        if fh_c:
            price = fh_c

    # --- Final fallback to fast_info ---
    try:
        fi = t_obj.fast_info
        if vol == 0 or vol is None:
            v_last = getattr(fi, 'last_volume', 0)
            v_avg = getattr(fi, 'three_month_average_volume', 0)
            vol = (v_last if v_last is not None else 0) or (v_avg if v_avg is not None else 0) or 0
        if price == 0:
            if status == "PRE-MARKET" and getattr(fi, 'pre_market_price', None):
                price = float(fi.pre_market_price)
            elif status in ("AFTER-HOURS", "CLOSED" ) and getattr(fi, 'post_market_price', None):
                price = float(fi.post_market_price)
            elif getattr(fi, 'last_price', None):
                price = float(fi.last_price)
    except:
        pass

    # --- Volume fallback ---
    if vol == 0 or vol is None:
        alt_vol = polygon_volume(symbol)
        if alt_vol:
            vol = alt_vol

    # Ensure vol is safe and not None
    if vol is None:
        vol = 0
    else:
        try:
            vol = int(vol)
        except:
            vol = 0

    # --- Cache updates ---
    if vol > 0:
        cache.volumes[symbol] = vol

    if pre_vol > 0:
        cache.pre_market_volume[symbol] = pre_vol
    if post_vol > 0:
        cache.after_hours_volume[symbol] = post_vol

    if price > 0:
        cache.prices[symbol] = price

        if status == "PRE-MARKET" and (pre_price is None or pre_price == 0):
            pre_price = price
        elif status in ("AFTER-HOURS", "CLOSED") and (post_price is None or post_price == 0):
            post_price = price

        techs = cache.technicals.get(symbol, {})
        reg_close = techs.get("Last_Reg_Close", 0.0)

        # Prefer batch quote's regularMarketPreviousClose (most authoritative source)
        # fast_info.previous_close can be stale/inconsistent for small-cap stocks
        if batch_prev_close and float(batch_prev_close) > 0:
            reg_close = float(batch_prev_close)

        # --- Session-aware returns ---
        if reg_close > 0:
            cache.session_change[symbol] = ((price - reg_close) / reg_close) * 100
            
            true_gap_price = price
            if status in ("OPEN", "CLOSED") and reg_open:
                true_gap_price = float(reg_open)
            elif status == "PRE-MARKET" and pre_price:
                true_gap_price = float(pre_price)

            raw_gap = ((true_gap_price - reg_close) / reg_close) * 100
            session_chg = cache.session_change[symbol]

            # Sanity check: if gap diverges wildly from session_change,
            # the regularMarketOpen is likely stale (Yahoo data quality issue
            # common with small-cap stocks early in the session).
            # A genuine gap should not be more than 2x the magnitude of session
            # change in the OPPOSITE direction when the market has been open.
            if status == "OPEN" and reg_open and abs(raw_gap) > 3.0:
                # If gap is negative but price is UP (or vice versa), open is suspect
                if (raw_gap < -3.0 and session_chg > 0) or (raw_gap > 3.0 and session_chg < 0):
                    # Stale open detected — try to get true open from chart API
                    chart_open = _get_chart_open(symbol)
                    if chart_open and chart_open > 0:
                        true_gap_price = chart_open
                        raw_gap = ((true_gap_price - reg_close) / reg_close) * 100
                    
            cache.gaps[symbol] = raw_gap

            # --- SSR TRIGGER CHECK (Rules > ENH_65) ---
            # Triggered if low is <= 10% below previous close
            if reg_low and reg_close > 0:
                drop_from_close = ((float(reg_low) - reg_close) / reg_close) * 100
                if drop_from_close <= -10.0:
                    cache.ssr_active[symbol] = True
                else:
                    # Note: Once triggered, SSR usually lasts today + tomorrow.
                    # For this real-time tracker, we flag if the day's low hit the threshold.
                    cache.ssr_active[symbol] = cache.ssr_active.get(symbol, False)

        if pre_price is not None:
            cache.pre_market_price[symbol] = pre_price
            if reg_close > 0:
                cache.pre_market_change[symbol] = ((pre_price - reg_close) / reg_close) * 100

        if post_price is not None:
            cache.after_hours_price[symbol] = post_price
            if reg_close > 0:
                cache.after_hours_change[symbol] = ((post_price - reg_close) / reg_close) * 100

        # --- Overnight return (non-compounded) ---
        if pre_price is not None and post_price is not None and post_price > 0:
            cache.overnight_return[symbol] = ((pre_price - post_price) / post_price) * 100


def calculate_rvol(symbol):
    hist = cache.history.get(symbol)
    if hist is None:
        return 1.0
    if hist.empty or 'Volume' not in hist.columns:
        return 1.0

    avg_vol = hist['Volume'].tail(20).mean()
    cur_vol = cache.volumes.get(symbol, 0)

    if avg_vol == 0:
        return 1.0

    # Calculate Time-Based Pacing (Linear Projection)
    # We compare "Projected EOD Volume" to "Average Daily Volume"
    # rVol = (Current / Time%) / Avg
    
    ny_now = datetime.now(ZoneInfo("America/New_York"))
    status = get_market_status()
    
    if status == "OPEN":
        # Calculate percent of trading day elapsed (09:30 - 16:00 = 390 mins)
        market_open = ny_now.replace(hour=9, minute=30, second=0, microsecond=0)
        minutes_elapsed = (ny_now - market_open).total_seconds() / 60
        
        # Avoid division by zero or extreme outliers in first minute
        if minutes_elapsed < 1:
            minutes_elapsed = 1
            
        pct_elapsed = minutes_elapsed / 390.0
        
        if pct_elapsed > 1.0: 
            pct_elapsed = 1.0
            
        # Linear projection
        projected_vol = cur_vol / pct_elapsed
        return projected_vol / avg_vol

    elif status == "CLOSED":
        # If closed, just comparison of totals
        return cur_vol / avg_vol
        
    else:
        # Pre/Post market: Valid comparison is hard without specific pre-market avg data.
        # Fallback to simple ratio (will be low, but expected for off-hours)
        return cur_vol / avg_vol


def distance_from_vwap(symbol):
    p = cache.prices.get(symbol, 0)
    v = cache.vwaps.get(symbol, 0)

    if p == 0 or v == 0:
        return 0.0

    return ((p - v) / v) * 100.0


def classify_signal(symbol):
    t = cache.technicals.get(symbol, {})
    rsi = t.get("RSI", 50)
    atr = t.get("ATR_Pct", 0)
    rvol = calculate_rvol(symbol)
    dist_vwap = distance_from_vwap(symbol)

    if rvol > 2 and dist_vwap > atr:
        return "BREAKOUT"
    if rvol > 2 and dist_vwap < -atr:
        return "BREAKDOWN"
    if rsi < 30:
        return "OVERSOLD"
    if rsi > 70:
        return "OVERBOUGHT"
    if abs(dist_vwap) < 0.2:
        return "VWAP PIN"
    return "NEUTRAL"


def calculate_score(symbol):
    t = cache.technicals.get(symbol, {})
    p = cache.prices.get(symbol, 0)
    v = cache.vwaps.get(symbol, 0)

    if p == 0 or not t:
        return 0, ""

    score = 0
    note = ""

    # -----------------------------
    # 1. TREND SCORE (weighted — SMA200 cross is ±2)
    # Range: -4 to +4
    # -----------------------------
    trend_component = t.get("Trend_Score", 0)
    score += trend_component

    # -----------------------------
    # 2. RSI COMPONENT (tiered)
    # Range: -2 to +2
    # -----------------------------
    rsi = t.get("RSI", 50)
    if rsi > 70:
        score += 2
    elif rsi > 60:
        score += 1
    elif rsi < 30:
        score -= 2
    elif rsi < 40:
        score -= 1

    # -----------------------------
    # 3. VWAP COMPONENT (symmetric)
    # Range: -2 to +2
    # -----------------------------
    if v > 0:
        dist = distance_from_vwap(symbol)      # % distance
        atr = t.get("ATR_Pct", 0)              # ATR%

        if dist > 0:
            score += 1
            # HV BREAKOUT: strong move above VWAP with volume confirmation
            if dist > atr * 0.25:
                score += 1
                note = "(HV BREAKOUT)"
        else:
            score -= 1
            # HV BREAK: strong move below VWAP
            if abs(dist) > atr * 0.25:
                score -= 1
                note = "(HV BREAK)"

    # -----------------------------
    # 4. RVOL COMPONENT (direction-aware)
    # Range: -1 to +1
    # -----------------------------
    rvol = calculate_rvol(symbol)
    gap = cache.gaps.get(symbol, 0)
    session_change = cache.session_change.get(symbol, 0)
    if rvol > 2:
        # High volume confirms the direction of the move
        if session_change >= 0:
            score += 1  # High vol on up-move = bullish
        else:
            score -= 1  # High vol on down-move = bearish (distribution)
    elif rvol < 0.5:
        score -= 1  # Low volume = lack of conviction

    # -----------------------------
    # 5. GAP% COMPONENT (new)
    # Range: -1 to +1
    # -----------------------------
    if gap > 1.0:
        score += 1
    elif gap < -1.0:
        score -= 1

    # -----------------------------
    # 6. GEX REGIME MODIFIER (new)
    # Range: -1 to +1
    # -----------------------------
    gex_data = cache.gex.get(symbol, {})
    if isinstance(gex_data, dict):
        net_gex = gex_data.get('net_gex', 0.0)
        flip_prox = gex_data.get('flip_proximity_percent', 0.0)
        if net_gex > 0:
            score += 1  # Positive gamma = supportive (dealers buy dips, sell rips)
        elif net_gex < 0:
            score -= 1  # Negative gamma = amplifies moves (dealers sell dips, buy rips)

    # -----------------------------
    # 7. SESSION LIQUIDITY DISCOUNT (new)
    # Reduces confidence during low-liquidity sessions
    # -----------------------------
    session = cache.session.get(symbol, "OPEN")
    if session in ("PRE-MARKET", "AFTER-HOURS"):
        score = round(score * 0.7)
        if note:
            note += " [LIQ⚠️]"
        else:
            note = "[LIQ⚠️]"

    return score, note


# -----------------------------
# MAIN LOOP
# -----------------------------
def run_daemon():
    global GLOBAL_STATE, FORCE_REFRESH
    print(f"{CYAN}Initializing GEM Dashboard v16.0 (Data Hardened + Trend)...{RESET}")
    tickers_obj = {sym: yf.Ticker(sym) for sym in ALL_TICKERS}

    print(f"{YELLOW}Performing initial heavy fetch...{RESET}")
    total_tickers = len(tickers_obj)
    for idx, (sym, obj) in enumerate(tickers_obj.items()):
        GLOBAL_STATE["status"] = "INITIALIZING - LOADING TECHNICALS (MAY TAKE 1 MIN)..."
        GLOBAL_STATE["boot_phase"] = "TECHNICAL_ANALYSIS"
        GLOBAL_STATE["boot_progress"] = idx + 1
        GLOBAL_STATE["boot_total"] = total_tickers
        GLOBAL_STATE["boot_ticker"] = sym

        print(f"Loading {sym}...", end="\r")
        update_history_and_technicals(sym, obj)

    # Initial GEX population — uses batch quotes for spot price, then computes
    # GEX per ticker with a throttled delay to avoid Yahoo Finance rate limits.
    print(f"{YELLOW}Loading initial GEX profiles...{RESET}")
    status = get_market_status()
    boot_quotes = get_batch_quotes(ALL_TICKERS)
    total_gex = len(ALL_TICKERS)
    for idx, sym in enumerate(ALL_TICKERS):
        GLOBAL_STATE["status"] = "INITIALIZING - LOADING GEX PROFILES (MAY TAKE 1 MIN)..."
        GLOBAL_STATE["boot_phase"] = "GEX_PROFILES"
        GLOBAL_STATE["boot_progress"] = idx + 1
        GLOBAL_STATE["boot_total"] = total_gex
        GLOBAL_STATE["boot_ticker"] = sym

        obj = tickers_obj[sym]
        # Get an initial price from batch quotes
        q = boot_quotes.get(sym, {})
        spot = None
        if status == "PRE-MARKET":
            spot = q.get('preMarketPrice')
        elif status == "AFTER-HOURS":
            spot = q.get('postMarketPrice')
        if not spot:
            spot = q.get('regularMarketPrice')
        if not spot:
            spot = cache.prices.get(sym)
        if spot and float(spot) > 0:
            spot = float(spot)
            cache.prices[sym] = spot
            print(f"  GEX [{idx+1}/{total_gex}] {sym}...", end="\r")
            
            now_ts = t_time.time()
            if sym in cache.last_gex_fetch and (now_ts - cache.last_gex_fetch[sym]) < 1800:
                cache.gex[sym] = cache.gex_cache[sym]
            else:
                gex_profile = get_gex_profile(obj, spot)
                cache.gex[sym] = gex_profile
                cache.last_gex_fetch[sym] = now_ts
                cache.gex_cache[sym] = gex_profile

            # GEX loop sleep (lines 914)
            t_time.sleep(0.2)  # OPTIMIZED: Reduced from 2s
        else:
            cache.gex[sym] = {
                'net_gex': 0.0, 'flip_price': 0.0,
                'inventory_velocity_delta': 0.0, 'gex_slope': 0.0,
                'flip_proximity_percent': 0.0, 'strike_oi_magnet': 0.0
            }
    print(f"{GREEN}GEX profiles loaded.{RESET}                    ")

    # Clear boot progress keys to signal completion to the frontend
    GLOBAL_STATE.pop("boot_phase", None)
    GLOBAL_STATE.pop("boot_progress", None)
    GLOBAL_STATE.pop("boot_total", None)
    GLOBAL_STATE.pop("boot_ticker", None)

    try:
        while True:
            # Sync tickers_obj with ALL_TICKERS
            for sym in ALL_TICKERS:
                if sym not in tickers_obj:
                    tickers_obj[sym] = yf.Ticker(sym)
            keys_to_remove = [sym for sym in list(tickers_obj.keys()) if sym not in ALL_TICKERS]
            for sym in keys_to_remove:
                del tickers_obj[sym]

            pass # os.system removed for CLI compatibility
            print("\033[?25l", end="", flush=True)
            ny_now = datetime.now(ZoneInfo("America/New_York"))
            status = get_market_status()

            cache.cycles += 1
            is_heavy = (cache.cycles % HISTORY_REFRESH_CYCLES == 0)

            # Immediately notify frontend that heavy fetching has begun
            if is_heavy and GLOBAL_STATE.get("tickers"):
                GLOBAL_STATE["is_heavy_refresh"] = True
                GLOBAL_STATE["is_heavy_refresh"] = True

            BATCH_SIZE = 2

            # Dynamic Table Width Calculation
            try:
                term_cols = os.get_terminal_size().columns
            except:
                term_cols = 115
            eff_width = max(70, min(term_cols, 140))
            avail = eff_width - 8 # 8 spaces between columns
            scale = avail / 107.0 # 107 is base content width
            w_sym = max(5, int(8 * scale))
            w_prc = max(7, int(10 * scale))
            w_gap = max(8, int(12 * scale))
            w_vol = max(6, int(10 * scale))
            w_atr = max(6, int(10 * scale))
            w_rsi = max(5, int(10 * scale))
            w_vwp = max(7, int(12 * scale))
            w_trd = max(5, int(10 * scale))
            used = w_sym + w_prc + w_gap + w_vol + w_atr + w_rsi + w_vwp + w_trd
            w_scr = max(10, avail - used)
            table_width = used + w_scr + 8

            pointer = int(cache.vwap_pointer)
            if pointer >= len(ALL_TICKERS):
                pointer = 0
            batch = ALL_TICKERS[pointer: pointer + BATCH_SIZE]  # type: ignore[call-overload]
            cache.vwap_pointer = (pointer + BATCH_SIZE) % len(ALL_TICKERS)

            status_color = GREEN
            if status == "PRE-MARKET":
                status_color = BLUE
            elif status == "AFTER-HOURS":
                status_color = PURPLE
            elif status == "CLOSED":
                status_color = RED

            if is_heavy:
                mode_str = f"{YELLOW}HEAVY REFRESH (OPTIMIZED){RESET}"
            else:
                mode_str = f"Tick Update (VWAP Batch: {batch[0]}...)"

            remaining_tickers = [s for s in ALL_TICKERS if s not in batch]
            print(f"\n{BOLD}GEM Dashboard (v17.1 - Institutional [Optional Social]){RESET}")
            print(f"Time: {ny_now.strftime('%H:%M:%S')} | Status: {status_color}{status}{RESET}")
            print(f"Mode: {mode_str}")
            if remaining_tickers and not is_heavy:
                print(f"{YELLOW}⏳ VWAP batch: [{', '.join(batch)}] fresh | [{', '.join(remaining_tickers)}] cached from prior cycle{RESET}")

            batch_quotes = get_batch_quotes(ALL_TICKERS)

            for b_sym in batch:
                v_true = get_true_intraday_vwap(b_sym, status)
                if v_true == 0.0:
                    v_true = fallback_vwap(b_sym)
                if v_true > 0:
                    cache.vwaps[b_sym] = v_true

            sep_line = "-" * table_width
            print(sep_line)
            header_str = (
                f"{'TICKER':<{w_sym}} {'PRICE':<{w_prc}} {'CHG%':<{w_gap}} {'VOL':<{w_vol}} "
                f"{'ATR%':<{w_atr}} {'RSI':<{w_rsi}} {'VWAP':<{w_vwp}} {'TREND':<{w_trd}} {'SCORE':^{w_scr}}"
            )
            print(header_str)
            # Calculate held and watched tickers to identify scouts inline
            held_and_watched = set()
            try:
                if os.path.exists('context/ssot.json'):
                    with open('context/ssot.json', 'r', encoding='utf-8') as f:
                        ss_data = json.load(f)
                        ms = ss_data.get('mutable_state', ss_data)
                        for p in ms.get('portfolio_snapshot', []):
                            held_and_watched.add(p['ticker'].upper())
                        for w in ms.get('watched_tickers', []):
                            held_and_watched.add(w.upper())
            except: pass

            data = []

            macro_state = {
                'IEF': {'price': 0.0, 'gap': 0.0, 'trend': 'FLAT'},
                '^VIX': {'price': 0.0, 'gap': 0.0},
                'VIXY': {'price': 0.0, 'gap': 0.0}
            }

            for i, sym in enumerate(ALL_TICKERS):
                # Dynamic sleep: slower on heavy refresh to respect rate limits
                if is_heavy or sym not in cache.history or cache.history[sym].empty:
                    # OPTIMIZED: Reduced from 1.5s
                    t_time.sleep(0.5) 
                else:
                    # OPTIMIZED: Reduced from 0.1s
                    t_time.sleep(0.05)

                obj = tickers_obj[sym]

                if is_heavy or sym not in cache.history or cache.history[sym].empty:
                    update_history_and_technicals(sym, obj)

                update_price_tick(sym, obj, status, batch_quotes.get(sym))

                # GEX is heavy, run only on heavy cycles after price is known
                if is_heavy:
                    spot_price = cache.prices.get(sym)
                    if spot_price and spot_price > 0:
                        now_ts = t_time.time()
                        if sym in cache.last_gex_fetch and (now_ts - cache.last_gex_fetch[sym]) < 1800:
                            gex_profile = cache.gex_cache[sym]
                        else:
                            gex_profile = get_gex_profile(obj, spot_price)
                            cache.last_gex_fetch[sym] = now_ts
                            cache.gex_cache[sym] = gex_profile
                        cache.gex[sym] = gex_profile
                    else:
                        cache.gex[sym] = {
                            'net_gex': 0.0, 
                            'flip_price': 0.0, 
                            'inventory_velocity_delta': 0.0,
                            'gex_slope': 0.0,
                            'flip_proximity_percent': 0.0,
                            'strike_oi_magnet': 0.0
                        }

                p = float(cache.prices.get(sym, 0))
                gap = float(cache.gaps.get(sym, 0))
                session_change = float(cache.session_change.get(sym, 0))
                vol = cache.volumes.get(sym, 0)
                vwap = cache.vwaps.get(sym, 0)
                techs = cache.technicals.get(sym, {})
                score, note = calculate_score(sym)

                if p == 0:
                    print(f"{sym:<8} {RED}NO DATA{RESET}")
                    data.append({
                        "ticker": sym,
                        "session": "UNKNOWN",
                        "session_liquidity": "LOW",
                        "status": "NO_DATA",
                        "ssr_active": False,
                        "price": 0.0,
                        "regular_close": 0.0,
                        "pre_market_price": 0.0,
                        "after_hours_price": 0.0,
                        "session_change_pct": 0.0,
                        "gap_percent": 0.0,
                        "pre_market_change_percent": 0.0,
                        "after_hours_change_percent": 0.0,
                        "overnight_return_percent": 0.0,
                        "volume": 0,
                        "rvol": 1.0,
                        "pre_market_volume": "UNAVAILABLE",
                        "after_hours_volume": "UNAVAILABLE",
                        "atr_percent": 0.0,
                        "atr": 0.0,
                        "rsi": 50.0,
                        "vwap": 0.0,
                        "distance_from_vwap": 0.0,
                        "trend_score": 0,
                        "vix_price": 0.0,
                        "volatility_regime": "NORMAL",
                        "vixy_roc": 0.0,
                        "net_gex_total": 0.0,
                        "gex_exposure": 0.0,
                        "dealer_posture": "NEUTRAL",
                        "gamma_flip_price": 0.0,
                        "inventory_velocity_delta": 0.0,
                        "gex_slope": 0.0,
                        "flip_proximity_percent": 0.0,
                        "strike_oi_magnet": 0.0,
                        "ma_20": 0.0,
                        "ma_50": 0.0,
                        "ma_200": 0.0,
                        "score": 0,
                        "signal": "NEUTRAL",
                        "trend": "FLAT",
                        "note": "NO DATA"
                    })
                    continue

                if sym == 'IEF':
                    trend = "BULLISH (Safe)" if session_change > 0 else "BEARISH (Risk)"
                    macro_state['IEF'] = {'price': p, 'gap': session_change, 'trend': trend}
                if sym == '^VIX':
                    macro_state['^VIX'] = {'price': p, 'gap': session_change}
                if sym == 'VIXY':
                    macro_state['VIXY'] = {'price': p, 'gap': session_change}

                if i % 2 == 1:
                    ROW_BG = BG_STRIPE
                    R_RST = f"{RESET}{BG_STRIPE}"
                else:
                    ROW_BG = BG_NORMAL
                    R_RST = RESET

                chg_val = f"{session_change:+.2f}%"
                if session_change > 0:
                    chg_cell = f"{GREEN}{chg_val:<{w_gap}}{R_RST}"
                elif session_change < 0:
                    chg_cell = f"{RED}{chg_val:<{w_gap}}{R_RST}"
                else:
                    chg_cell = f"{WHITE}{chg_val:<{w_gap}}{R_RST}"

                rsi_raw = techs.get('RSI', 0)
                rsi_val = f"{rsi_raw:.1f}"
                if rsi_raw >= 70:
                    rsi_cell = f"{RED}{rsi_val:<{w_rsi}}{R_RST}"
                elif rsi_raw <= 30:
                    rsi_cell = f"{GREEN}{rsi_val:<{w_rsi}}{R_RST}"
                else:
                    rsi_cell = f"{WHITE}{rsi_val:<{w_rsi}}{R_RST}"

                # TREND LABEL (color-coded — updated thresholds for weighted SMA200)
                # For INVERSE_MACRO tickers (VIX, IEF, UUP): rising price = bearish for equities,
                # so we invert the COLORS (UP=RED, DOWN=GREEN) while keeping the label
                # matching actual price direction.
                ts = techs.get("Trend_Score", 0)
                if ts >= 3:
                    # UP: good for normal tickers (GREEN), bad for inverse tickers (RED)
                    up_color = RED if sym in INVERSE_MACRO else GREEN
                    trend_label = f"{up_color}{'UP':<{w_trd}}{R_RST}"
                elif ts <= -3:
                    # DOWN: bad for normal tickers (RED), good for inverse tickers (GREEN)
                    dn_color = GREEN if sym in INVERSE_MACRO else RED
                    trend_label = f"{dn_color}{'DOWN':<{w_trd}}{R_RST}"
                else:
                    trend_label = f"{YELLOW}{'FLAT':<{w_trd}}{R_RST}"

                vwap_warn = ""
                if sym not in INVERSE_MACRO and vwap > 0 and p < vwap and score > 0:
                    vwap_warn = f"{RED}(⚠️ < VWAP){R_RST}"

                if score >= 5:
                    score_disp = f"{GREEN}+{score} (BULL){R_RST} {vwap_warn}"
                elif score <= -5:
                    score_disp = f"{RED}{score} (BEAR){R_RST}{RED}{note}{R_RST}"
                else:
                    score_disp = f"{YELLOW}{score:+}{R_RST}{YELLOW}{note}{R_RST}"

                if sym in INVERSE_MACRO:
                    if score <= -3:
                        score_disp = f"{GREEN}{score} (SAFE){R_RST}"
                    elif score >= 3:
                        score_disp = f"{RED}+{score} (RISK){R_RST}"

                atr_val = techs.get('ATR_Pct', 0)
                atr_str = "0.00%" if pd.isna(atr_val) else f"{atr_val:.2f}%"
                vol_str = format_volume(vol, sym)
                vwap_str = f"{vwap:.2f}" if vwap > 0 else "-"

                # Center SCORE
                s_vis = get_visible_length(score_disp)
                s_pad = w_scr - s_vis
                if s_pad > 0:
                    pad_l = s_pad // 2
                    pad_r = s_pad - pad_l
                    score_cell = f"{' '*pad_l}{score_disp}{' '*pad_r}"
                else:
                    score_cell = score_disp

                row_content = (
                    f"{sym:<{w_sym}} {p:<{w_prc}.2f} {chg_cell} {vol_str:<{w_vol}} {atr_str:<{w_atr}} "
                    f"{rsi_cell} {vwap_str:<{w_vwp}} {trend_label} {score_cell}"
                )

                vis_len = get_visible_length(row_content)
                padding = table_width - vis_len
                if padding > 0:
                    row_content += " " * padding

                print(f"{ROW_BG}{row_content}{RESET}")

                if is_heavy:
                    p_pct = (i + 1) / len(ALL_TICKERS)
                    p_len = 20
                    p_fill = int(p_len * p_pct)
                    p_bar = f"{YELLOW}[{'=' * p_fill}{' ' * (p_len - p_fill)}]{RESET}"
                    # Update Mode line (Line 4) with progress bar using ANSI Save/Restore cursor
                    print(f"\033[s\033[4;1HMode: {YELLOW}HEAVY REFRESH{RESET} {p_bar} {int(p_pct * 100)}%\033[u", end="", flush=True)

                gex_data = cache.gex.get(sym, {})
                if isinstance(gex_data, float): # Fallback if cache has old float format
                    gex_data = {'net_gex': gex_data}

                # Derive dealer_posture from net_gex sign per
                # gem_trading_rules/rules.md > ENH_20 (Synthetic GEX Logic)
                _raw_gex = float(gex_data.get('net_gex', 0.0))
                if _raw_gex > 0:
                    _dealer_posture = "LONG_GAMMA"
                elif _raw_gex < 0:
                    _dealer_posture = "SHORT_GAMMA"
                else:
                    _dealer_posture = "NEUTRAL"

                is_scout = sym in ACTIVE_SCOUT_TICKERS and sym not in held_and_watched
                item_dict = {
                    "ticker": sym,
                    "session": cache.session.get(sym),
                    "session_liquidity": cache.session_liquidity.get(sym),
                    "status": "ACTIVE",
                    "ssr_active": cache.ssr_active.get(sym, False),
                    
                    # Prices
                    "price": float(p),
                    "regular_close": float(techs.get("Last_Reg_Close", 0.0)),
                    "pre_market_price": float(cache.pre_market_price.get(sym, 0)),
                    "after_hours_price": float(cache.after_hours_price.get(sym, 0)),

                    # Returns
                    # session_change_pct: required by ENH_FIN_02 Protective Exit Override logic
                    "session_change_pct": float(cache.session_change.get(sym, 0)),
                    "gap_percent": float(gap),
                    "pre_market_change_percent": float(cache.pre_market_change.get(sym, 0)),
                    "after_hours_change_percent": float(cache.after_hours_change.get(sym, 0)),
                    "overnight_return_percent": float(cache.overnight_return.get(sym, 0)),

                    # Volume
                    "volume": int(vol),
                    "rvol": float(calculate_rvol(sym)),
                    "pre_market_volume": int(cache.pre_market_volume.get(sym, 0)) or "UNAVAILABLE",
                    "after_hours_volume": int(cache.after_hours_volume.get(sym, 0)) or "UNAVAILABLE",

                    # Technicals
                    "atr_percent": float(atr_val),
                    "atr": float(techs.get("ATR", 0)),
                    "rsi": float(rsi_raw),
                    "vwap": float(vwap),
                    "distance_from_vwap": float(distance_from_vwap(sym)),
                    # trend_score thresholds: gem_trading_rules/rules.md > TREND_SCORE_UP_THRESHOLD (3) / TREND_SCORE_DOWN_THRESHOLD (-3)
                    # For INVERSE_MACRO tickers: negated so rising price = negative (bearish for equities)
                    "trend_score": int(-techs.get("Trend_Score", 0)) if sym in INVERSE_MACRO else int(techs.get("Trend_Score", 0)),
                    
                    # Macro Context & Volatility Regime (Rules > VOLATILITY_REGIME_THRESHOLDS)
                    "vix_price": float(cache.prices.get('^VIX', 0.0)),
                    "volatility_regime": "HIGH_VOL" if cache.prices.get('^VIX', 0.0) > 20.0 else "LOW_VOL" if cache.prices.get('^VIX', 0.0) < 12.0 and cache.prices.get('^VIX', 0.0) > 0 else "NORMAL",
                    "vixy_roc": round(float(macro_state.get('VIXY', {}).get('gap', 0.0)), 2),

                    # GEX — field names per ENH_32 canonical schema in rules.json
                    "net_gex_total": _raw_gex,
                    "gex_exposure": _raw_gex,  # Normalized GEX exposure (position-level scaling done by SSoT Gem)
                    "dealer_posture": _dealer_posture,  # gem_trading_rules/rules.md > dealer_posture_logic
                    "gamma_flip_price": float(gex_data.get('flip_price', 0.0)),
                    "inventory_velocity_delta": float(gex_data.get('inventory_velocity_delta', 0.0)),
                    "gex_slope": float(gex_data.get('gex_slope', 0.0)),
                    "flip_proximity_percent": float(gex_data.get('flip_proximity_percent', 0.0)),
                    "strike_oi_magnet": float(gex_data.get('strike_oi_magnet', 0.0)),
                    "ma_20": float(techs.get("SMA_20") or 0.0),
                    "ma_50": float(techs.get("SMA_50") or 0.0),
                    "ma_200": float(techs.get("SMA_200") or 0.0),
                    "score": int(score),
                    "signal": classify_signal(sym),
                    # trend label thresholds: gem_trading_rules/rules.md > TREND_SCORE_UP_THRESHOLD / TREND_SCORE_DOWN_THRESHOLD
                    # For INVERSE_MACRO: JSON trend uses equity-perspective (negated score)
                    "trend": ("UP" if (-ts if sym in INVERSE_MACRO else ts) >= 3 else "DOWN" if (-ts if sym in INVERSE_MACRO else ts) <= -3 else "FLAT"),
                    
                    # Phase 6 Enhancements (Already computed inline above)

                    "note": note.strip()
                }
                if is_scout:
                    item_dict["_isScout"] = True
                    item_dict["category"] = SCOUTED_TICKER_TO_CATEGORY_MAP.get(sym)

                data.append(item_dict)



            print("-" * table_width)
            ief = macro_state.get('IEF', {})
            ief_gap = float(ief['gap'])
            ief_price = float(ief['price'])
            # IEF bond alert — threshold: gem_trading_rules/rules.md > system_thresholds > IEF_YIELD_ALERT_THRESHOLD (-0.15)
            if ief_gap < -0.15:
                print(f"   >>> BOND ALERT:  7-10Y Bond Price {ief_price:.2f} ({ief_gap:+.2f}%) {RED}[YIELDS RISING - RISK]{RESET}")
            else:
                print(f"   >>> BOND STATUS: 7-10Y Bond Price {ief_price:.2f} ({ief_gap:+.2f}%) {GREEN}[YIELDS STABLE]{RESET}")

            vix = macro_state['^VIX']
            vix_price = float(vix['price'])

            vixy = macro_state.get('VIXY', {'price': 0.0, 'gap': 0.0})
            vixy_gap = float(vixy['gap'])

            # VIX fear alert — absolute trailing regime (VIX > 20.0) OR real-time velocity (VIXY ROC > 5.0%)
            if vix_price > 20.0 or vixy_gap > 5.0:
                print(f"   >>> FEAR ALERT:  VIX is {vix_price:.2f} | VIXY Velocity {vixy_gap:+.2f}% {RED}[CAUTION]{RESET}")
            else:
                print(f"   >>> FEAR STATUS: VIX is {vix_price:.2f} | VIXY Velocity {vixy_gap:+.2f}% {GREEN}[STABLE]{RESET}")

            sleep_needed = REFRESH_RATE_SECONDS
            print("")

            # Build final_output for web dashboard
            supplemental_lessons = []
            if os.path.exists('context/trade_lessons.json'):
                try:
                    with open('context/trade_lessons.json', 'r') as f:
                        lessons_data = json.load(f)
                        if isinstance(lessons_data, dict) and "trade_lessons" in lessons_data:
                            supplemental_lessons = lessons_data["trade_lessons"]
                        elif isinstance(lessons_data, list):
                            supplemental_lessons = lessons_data
                except: pass

            supplemental_ssot = {}
            if os.path.exists('context/ssot.json'):
                try:
                    with open('context/ssot.json', 'r') as f:
                        supplemental_ssot = json.load(f)
                except: pass

            # Filter and sort scout tickers: keep at most 6, sorted by score in descending order, excluding held/watched assets
            held_and_watched = set()
            try:
                if os.path.exists('context/ssot.json'):
                    with open('context/ssot.json', 'r', encoding='utf-8') as f:
                        ss_data = json.load(f)
                        ms = ss_data.get('mutable_state', ss_data)
                        for p in ms.get('portfolio_snapshot', []):
                            held_and_watched.add(p['ticker'].upper())
                        for w in ms.get('watched_tickers', []):
                            held_and_watched.add(w.upper())
            except: pass

            non_scout_data = []
            scout_data = []
            for item in data:
                if item.get("_isScout"):
                    scout_data.append(item)
                else:
                    non_scout_data.append(item)
            
            scout_data.sort(key=lambda x: x.get("score", 0), reverse=True)
            filtered_scouts = scout_data[:5]
            final_tickers = non_scout_data + filtered_scouts

            final_output = {
                "_meta": {
                    "description": "LIVE MARKET DATA - DO NOT TREAT AS SIMULATED",
                    "is_simulation": False,
                    "source": "Real-time Exchange Feed",
                    "reliability": "High"
                },
                "timestamp": datetime.now(ZoneInfo("America/New_York")).strftime('%Y-%m-%d %H:%M:%S'),
                "status": status,
                "is_heavy_refresh": False,
                "tickers": final_tickers,
                "scout_categories_loaded": list(PROCESSED_SCOUT_CATEGORIES),
                "local_storage_state": supplemental_ssot,
                "trade_lessons": supplemental_lessons
            }
            GLOBAL_STATE = final_output

            wait_start = t_time.time()
            while True:
                if FORCE_REFRESH:
                    FORCE_REFRESH = False
                    break
                remaining = sleep_needed - (t_time.time() - wait_start)
                if remaining <= 0:
                    break
                print(f"\r\033[?25l{CYAN}Next Update in {int(remaining)}s... Web Dashboard is active.{RESET}    ", end="", flush=True)
                t_time.sleep(0.1)

    except KeyboardInterrupt:
        print(f"\033[?25h", end="")
        print(f"\n{YELLOW}Daemon Shutdown.{RESET}")

if __name__ == "__main__":
    if not os.path.exists("static"):
        os.makedirs("static")
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
    
    daemon_thread = threading.Thread(target=run_daemon, daemon=True)
    daemon_thread.start()
    
    print(f"{CYAN}Starting FastAPI server on http://localhost:8000{RESET}")
    uvicorn.run(app, host="0.0.0.0", port=8000)
