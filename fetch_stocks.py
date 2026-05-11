import asyncio
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
from collections import defaultdict
import threading
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from convert_lessons import convert_json_to_md

try:
    import msvcrt
except ImportError:
    msvcrt = None

# Load configuration from JSON file
with open('config.json', 'r') as f:
    config = json.load(f)

# -----------------------------
# CONFIGURATION
# -----------------------------
FINNHUB_API_KEY = config.get("FINNHUB_API_KEY")
POLYGON_API_KEY = config.get("POLYGON_API_KEY")

USE_FINNHUB = True
USE_POLYGON = False  # keep false unless you want Polygon volume fallback

# WATCHLIST_TICKERS and MACRO_TICKERS persistence logic
WATCHLIST_TICKERS = config.get("WATCHLIST", [
    'ONDS', 'UMAC', 'RCAT', 'DFTX', 'GLD'
])

MACRO_TICKERS = config.get("MACRO_TICKERS", [
    '^VIX',
    'VIXY',
    'IEF',
    'UUP',
    'SPY',
    'GDX'
])

PORTFOLIO_TICKERS = []
SCOUT_TICKERS = []
# Load TICKERS from local SSoT if available (Persistence Mandate)
if os.path.exists('local_ssot_shadow.json'):
    try:
        with open('local_ssot_shadow.json', 'r') as f:
            ssot_data = json.load(f)
            # Check both root and mutable_state for scouted_assets_tracked
            scouts = ssot_data.get("scouted_assets_tracked")
            if not scouts and "mutable_state" in ssot_data:
                scouts = ssot_data["mutable_state"].get("scouted_assets_tracked")
            if isinstance(scouts, list):
                SCOUT_TICKERS = [s for s in scouts if isinstance(s, str)]
            
            # Load Portfolio tickers
            portfolio = ssot_data.get("mutable_state", {}).get("portfolio_snapshot", [])
            if isinstance(portfolio, list):
                PORTFOLIO_TICKERS = [p.get("ticker").upper() for p in portfolio if isinstance(p, dict) and p.get("ticker")]
    except:
        pass

ALL_TICKERS = list(dict.fromkeys(WATCHLIST_TICKERS + PORTFOLIO_TICKERS + MACRO_TICKERS + SCOUT_TICKERS)) # Deduplicated order
INVERSE_MACRO = ['^VIX', 'VIXY', 'UUP', 'IEF']

MACRO_LABELS = {
    'SPY': 'S&P 500',
    '^VIX': 'Volatility',
    'IEF': 'Treasury Bond',
    'UUP': 'US Dollar',
    'GLD': 'Gold',
    'GDX': 'Gold Miners',
    'VIXY': 'Short-Term VIX'
}

REFRESH_RATE_SECONDS = 30
HISTORY_REFRESH_CYCLES = 10

GLOBAL_STATE = {}
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def save_config():
    global config, WATCHLIST_TICKERS, MACRO_TICKERS
    config["WATCHLIST"] = WATCHLIST_TICKERS
    config["MACRO_TICKERS"] = MACRO_TICKERS
    with open('config.json', 'w') as f:
        json.dump(config, f, indent=4)

# [AUTONOMOUS MANDATE: ENH_84 Scout Screening]
def run_scout_pipeline(ticker):
    """
    Executes a zero-cost technical sweep via free-tier APIs (e.g., Finnhub)
    before invoking the expensive Analyst LLM node.
    """
    # 1. Fetch live baseline data (SMA50, SMA200, RVOL)
    # Placeholder for actual technical fetch
    technical_data = {"rvol": 2.0, "price": 100, "sma_200": 90} 
    
    # 2. Hard Gate: Block if structural minimums fail
    if technical_data.get('rvol', 0) < 1.5 or technical_data.get('price', 0) < technical_data.get('sma_200', float('inf')):
        return {"status": "REJECTED_BY_SCOUT", "reason": "Insufficient momentum/trend."}
        
    # 3. Graduate to Analyst
    return {"status": "Unverified Institutional Status", "data": technical_data}

@app.get("/api/data")
def get_data():
    return JSONResponse(fetch_stocks(GLOBAL_STATE))

@app.get("/api/tickers")
def get_tickers():
    return JSONResponse({"tickers": WATCHLIST_TICKERS, "macro": MACRO_TICKERS, "macro_labels": MACRO_LABELS})

@app.get("/api/eod_review_payload")
async def get_eod_review_payload():
    log_file = "decision_log.json"
    try:
        with open(log_file, "r") as f:
            decision_log = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        decision_log = []

    # Construct the automated prompt instruction
    eod_prompt = (
        "SYSTEM DIRECTIVE: EXECUTE [MANDATE_26_POST_TRADE_REVIEW]\n\n"
        "You are operating as the Review Engine. I am providing the continuous "
        "decision log of the Council's trades over the trailing 20-day period. "
        "You are mandated to execute an End-of-Day historical backtest.\n\n"
        "INSTRUCTIONS:\n"
        "1. Grade the historical `agreement_score_sa` and `trade_state` assumptions "
        "against the realized price action for each ticker.\n"
        "2. Distinguish between mechanistic flow misfires (e.g., rebalancing windows) "
        "and fundamental logic breakdowns.\n"
        "3. If a systemic vulnerability is identified, generate a corrective rule.\n"
        "4. You MUST output any new codified rules natively inside the JSON `EXECUTION_PAYLOAD` "
        "under the `new_trade_lessons` array so I can merge them into the SSoT.\n\n"
        "DECISION LOG DATA:\n"
        f"```json\n{json.dumps(decision_log, indent=4)}\n```"
    )
    
    return {"payload": eod_prompt}

@app.post("/api/tickers")
async def update_tickers(req: Request):
    global WATCHLIST_TICKERS, ALL_TICKERS
    data = await req.json()
    new_tickers = data.get("tickers", [])
    valid_tickers = [t.upper() for t in new_tickers if t]
    WATCHLIST_TICKERS = valid_tickers
    ALL_TICKERS = list(dict.fromkeys(WATCHLIST_TICKERS + PORTFOLIO_TICKERS + MACRO_TICKERS + SCOUT_TICKERS))
    save_config()
    return JSONResponse({"status": "success", "tickers": WATCHLIST_TICKERS})

@app.get("/api/basket")
def get_basket():
    ssot_file = 'local_ssot_shadow.json'
    if os.path.exists(ssot_file):
        try:
            with open(ssot_file, 'r') as f:
                ssot = json.load(f)
                return JSONResponse(ssot.get("mutable_state", {}).get("portfolio_snapshot", []))
        except:
            return JSONResponse([])
    return JSONResponse([])

@app.post("/api/basket")
async def save_basket(req: Request):
    ssot_file = 'local_ssot_shadow.json'
    new_basket = await req.json()
    if os.path.exists(ssot_file):
        try:
            with open(ssot_file, 'r') as f:
                ssot = json.load(f)
            
            if "mutable_state" not in ssot:
                ssot["mutable_state"] = {}
            ssot["mutable_state"]["portfolio_snapshot"] = new_basket
            
            with open(ssot_file, 'w') as f:
                json.dump(ssot, f, indent=2)
            
            # Update global trackers
            global PORTFOLIO_TICKERS, ALL_TICKERS
            PORTFOLIO_TICKERS = [p.get("ticker").upper() for p in new_basket if isinstance(p, dict) and p.get("ticker")]
            ALL_TICKERS = list(dict.fromkeys(WATCHLIST_TICKERS + PORTFOLIO_TICKERS + MACRO_TICKERS + SCOUT_TICKERS))
            
            return JSONResponse({"status": "success"})
        except Exception as e:
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)
    return JSONResponse({"status": "error", "message": "SSoT not found"}, status_code=404)

@app.get("/api/watchlist")
def get_watchlist():
    return JSONResponse(WATCHLIST_TICKERS)

@app.post("/api/watchlist")
async def save_watchlist(req: Request):
    global WATCHLIST_TICKERS, ALL_TICKERS
    new_watchlist = await req.json()
    if isinstance(new_watchlist, list):
        WATCHLIST_TICKERS = [t.upper() for t in new_watchlist if isinstance(t, str)]
        ALL_TICKERS = list(dict.fromkeys(WATCHLIST_TICKERS + PORTFOLIO_TICKERS + MACRO_TICKERS + SCOUT_TICKERS))
        save_config()
        return JSONResponse({"status": "success"})
    return JSONResponse({"status": "error", "message": "Invalid data format"}, status_code=400)

@app.post("/api/macro")
async def update_macro_tickers(req: Request):
    global MACRO_TICKERS, ALL_TICKERS
    data = await req.json()
    new_macro = data.get("macro", [])
    valid_macro = [t.upper() for t in new_macro if t]
    MACRO_TICKERS = valid_macro
    ALL_TICKERS = list(dict.fromkeys(WATCHLIST_TICKERS + PORTFOLIO_TICKERS + MACRO_TICKERS + SCOUT_TICKERS))
    save_config()
    return JSONResponse({"status": "success", "macro": MACRO_TICKERS})

def _deep_merge(base, delta):
    merged = base.copy()
    for k, v in delta.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged

def _merge_portfolio(existing_list, delta_list):
    if not isinstance(existing_list, list): return delta_list
    if not isinstance(delta_list, list): return existing_list
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
    return list(by_ticker.values())

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
        
        # Process the bifurcated clipboard data (JSON block + Markdown text)
        json_payload = {}
        lessons_from_md = []

        # 1. Extract JSON SSoT block and isolate Markdown part
        json_match = re.search(r"```json\s*(\{.*?\})\s*```", clip_data, re.IGNORECASE | re.DOTALL)
        md_part = clip_data
        if json_match:
            try:
                json_payload = json.loads(json_match.group(1).strip())
                # Remove the JSON block from the text we search for Markdown lessons
                md_part = clip_data[:json_match.start()] + clip_data[json_match.end():]
            except: pass
        else:
            # Fallback for naked JSON
            match = re.search(r"```(?:json)?\s*(.*?)\s*```", clip_data, re.IGNORECASE | re.DOTALL)
            if match:
                try:
                    json_payload = json.loads(match.group(1).strip())
                    md_part = clip_data[:match.start()] + clip_data[match.end():]
                except: pass
            else:
                # Last resort: try to find braces
                start_brace = clip_data.find('{')
                end_brace = clip_data.rfind('}')
                if start_brace != -1 and end_brace != -1 and end_brace > start_brace:
                    try:
                        json_payload = json.loads(clip_data[start_brace:end_brace+1].strip())
                        md_part = clip_data[:start_brace] + clip_data[end_brace+1:]
                    except: pass

        # 2. Extract Markdown Lessons block (Search ONLY the md_part)
        bullet_pattern = r'(?:-|\*)\s*(?:\*\*)?L-(\d+):(?:\*\*)?\s*(.*?)(?=\s*\n(?:-|\*)\s*(?:\*\*)?L-|\s*\n##|\s*\n$|$)'
        for m in re.finditer(bullet_pattern, md_part, re.DOTALL):
            try:
                l_id = int(m.group(1))
                content = m.group(2).strip()
                tags = re.findall(r'#([\w-]+)', content)
                rule = re.sub(r'#[\w-]+', '', content).strip()
                lessons_from_md.append({"id": l_id, "rule": rule, "tags": tags})
            except: continue

        discrete_pattern = r'L-(\d+):\s*(.*?)(?=\s*L-\d+:|\s*\n##|\s*MANDATE_|$)'
        for m in re.finditer(discrete_pattern, md_part, re.DOTALL):
            try:
                l_id = int(m.group(1))
                if any(l['id'] == l_id for l in lessons_from_md): continue
                content = m.group(2).strip()
                tags = re.findall(r'#([\w-]+)', content)
                rule = re.sub(r'#[\w-]+', '', content).strip()
                lessons_from_md.append({"id": l_id, "rule": rule, "tags": tags})
            except: continue

        if not json_payload and not lessons_from_md:
             return JSONResponse({"status": "error", "message": "No valid JSON or Markdown lessons found in payload"})

        payload = json_payload
        
        # ENH_49: Intercept scrutiny_audit and trade_state for decision_log.json early
        try:
            def find_key_recursive(data, target_key):
                if not isinstance(data, dict): return None
                if target_key in data: return data[target_key]
                for k in ("EXECUTION_PAYLOAD", "mutable_state", "payload"):
                    if k in data:
                        res = find_key_recursive(data[k], target_key)
                        if res is not None: return res
                return None

            incoming_portfolio = find_key_recursive(payload, "portfolio_snapshot")
            if incoming_portfolio and isinstance(incoming_portfolio, list):
                print(f"DEBUG: Found incoming_portfolio with {len(incoming_portfolio)} items")
                ts = find_key_recursive(payload, "timestamp") or datetime.now().isoformat()
                trigger = find_key_recursive(payload, "trigger_context") or "ROUTINE_OUTPUT"
                
                market_context = {
                    "regime": find_key_recursive(payload, "regime") or find_key_recursive(payload, "risk_regime"),
                    "vix_guard": find_key_recursive(payload, "vix_guard"),
                    "portfolio_value_usd": find_key_recursive(payload, "portfolio_total_value_usd"),
                    "remaining_cash_usd": find_key_recursive(payload, "remaining_cash_usd")
                }
                
                turn_log = {"timestamp": ts, "trigger_context": trigger, "market_context": market_context, "decisions": []}
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
                    print(f"DEBUG: Appending {len(turn_log['decisions'])} decisions to decision_log.json")
                    decision_log_file = 'decision_log.json'
                    log_data = []
                    if os.path.exists(decision_log_file):
                        try:
                            with open(decision_log_file, 'r') as f:
                                log_data = json.load(f)
                        except: pass
                    if not isinstance(log_data, list): log_data = []
                    log_data.append(turn_log)
                    if len(log_data) > 300: log_data = log_data[-300:]
                    with open(decision_log_file, 'w') as f:
                        json.dump(log_data, f, indent=2)
                else:
                    print("DEBUG: No valid decisions found in portfolio items")
            else:
                print("DEBUG: No portfolio_snapshot found in payload")
        except Exception as e:
            print(f"Failed to append to decision_log.json: {e}")
        
        # Map common payload aliases to canonical keys
        def map_aliases(data):
            if not isinstance(data, dict): return
            if "portfolio" in data and "portfolio_snapshot" not in data:
                data["portfolio_snapshot"] = data.pop("portfolio")
            if "terminal_state" in data and "state_context" not in data:
                data["state_context"] = data.pop("terminal_state")
            if "forensic_alerts" in data and "forensic_intelligence" not in data:
                data["forensic_intelligence"] = data.pop("forensic_alerts")
            if "allocations" in data and "portfolio_snapshot" not in data:
                data["portfolio_snapshot"] = data.pop("allocations")
            if "unallocated_cash_eur" in data and "remaining_cash_eur" not in data:
                data["remaining_cash_eur"] = data["unallocated_cash_eur"]
            if "unallocated_cash_usd" in data and "remaining_cash_usd" not in data:
                data["remaining_cash_usd"] = data["unallocated_cash_usd"]
            
            if "portfolio_snapshot" in data and isinstance(data["portfolio_snapshot"], list):
                for item in data["portfolio_snapshot"]:
                    if isinstance(item, dict):
                        if "wac_usd" in item and "wac" not in item:
                            item["wac"] = item.pop("wac_usd")
                        if "current_price_usd" in item and "price" not in item:
                            item["price"] = item.pop("current_price_usd")
                        if "action" in item and "trade_state" not in item:
                            item["trade_state"] = item.pop("action")
        
        map_aliases(payload)
        if isinstance(payload.get("mutable_state"), dict):
            map_aliases(payload["mutable_state"])
        if isinstance(payload.get("EXECUTION_PAYLOAD"), dict):
            map_aliases(payload["EXECUTION_PAYLOAD"])
            
        # ENH_31: Promote key fields from EXECUTION_PAYLOAD to state root for immediate SSoT synchronization
        # This ensures that 'allocations' (portfolio_snapshot) and other directives are applied to the active state.
        incoming_ep = payload.get("EXECUTION_PAYLOAD")
        if not incoming_ep and isinstance(payload.get("mutable_state"), dict):
            incoming_ep = payload["mutable_state"].get("EXECUTION_PAYLOAD")
            
        if isinstance(incoming_ep, dict):
            promotion_keys = ["portfolio_snapshot", "risk_metrics", "directive", "timestamp", 
                              "remaining_cash_eur", "remaining_cash_usd", 
                              "unallocated_cash_eur", "unallocated_cash_usd",
                              "base_currency", "exchange_rate", 
                              "portfolio_total_value_usd", "portfolio_total_value_eur"]
            
            # Source of truth can be at the root of incoming_ep or inside its mutable_state
            ep_source = incoming_ep
            if "mutable_state" in incoming_ep and isinstance(incoming_ep["mutable_state"], dict):
                # If ep has mutable_state, merge its keys into a temporary source
                ep_source = _deep_merge(incoming_ep, incoming_ep["mutable_state"])

            # Determine target container for promotion
            target_container = payload
            if isinstance(payload.get("mutable_state"), dict):
                target_container = payload["mutable_state"]
                
            for k in promotion_keys:
                if k in ep_source:
                    # Always promote portfolio_snapshot as it represents the target allocations
                    # For others, promote if not already present or if it's a primary directive/metric
                    if k == "portfolio_snapshot" or k not in target_container or k in ["directive", "risk_metrics", "timestamp"]:
                        target_container[k] = ep_source[k]
            
        # Helper to extract and remove fields from payload regardless of nesting
        def get_and_prune(data, key):
            if key in data: return data.pop(key)
            if "mutable_state" in data and isinstance(data["mutable_state"], dict) and key in data["mutable_state"]:
                return data["mutable_state"].pop(key)
            if "EXECUTION_PAYLOAD" in data and isinstance(data["EXECUTION_PAYLOAD"], dict) and key in data["EXECUTION_PAYLOAD"]:
                return data["EXECUTION_PAYLOAD"].pop(key)
            return None

        # 3. v8.6 Forensic Lesson Hunt: Recursively find any key containing 'trade_lessons'
        def hunt_and_extract_lessons(data):
            found_lessons = []
            found_compressed = None
            if not isinstance(data, dict): return found_lessons, found_compressed
            
            keys_to_prune = []
            for k, v in data.items():
                k_low = k.lower()
                if "trade_lessons" in k_low or "trade-lessons" in k_low:
                    if "compressed" in k_low:
                        found_compressed = v
                    else:
                        if isinstance(v, list):
                            for item in v:
                                if isinstance(item, dict):
                                    # Normalize Gem keys
                                    if "content" in item and "rule" not in item:
                                        item["rule"] = item.pop("content")
                                    if "mandate" in item and "rule" not in item:
                                        item["rule"] = item.get("mandate") # Keep mandate but ensure rule is set
                                    if "category" in item:
                                        cat = item.pop("category")
                                        if "tags" not in item: item["tags"] = []
                                        if cat not in item["tags"]: item["tags"].append(cat)
                                found_lessons.append(item)
                        elif isinstance(v, dict):
                            # Handle wrapped lessons e.g., {"trade_lessons": [...]}
                            if "trade_lessons" in v and isinstance(v["trade_lessons"], list):
                                found_lessons.extend(v["trade_lessons"])
                            elif "trade_lessons_revision" in v and isinstance(v["trade_lessons_revision"], list):
                                found_lessons.extend(v["trade_lessons_revision"])
                            else:
                                if "content" in v and "rule" not in v:
                                    v["rule"] = v.pop("content")
                                if "mandate" in v and "rule" not in v:
                                    v["rule"] = v.get("mandate")
                                found_lessons.append(v)
                    keys_to_prune.append(k)
                elif k in ("mutable_state", "EXECUTION_PAYLOAD") and isinstance(v, dict):
                    # Specifically recurse into primary payload containers
                    nested_lessons, nested_compressed = hunt_and_extract_lessons(v)
                    found_lessons.extend(nested_lessons)
                    if nested_compressed: found_compressed = nested_compressed
            
            for k in keys_to_prune:
                data.pop(k, None)
            return found_lessons, found_compressed

        extracted_trade_lessons, extracted_compressed = hunt_and_extract_lessons(payload)
        if not isinstance(extracted_trade_lessons, list): extracted_trade_lessons = []

        # Add lessons parsed from Markdown
        if lessons_from_md:
            extracted_trade_lessons.extend(lessons_from_md)
            
        if not extracted_trade_lessons:
            extracted_trade_lessons = None
            
        extracted_mutations = get_and_prune(payload, "rule_mutations")

        # Merge local ssot
        existing_ssot = {}
        if os.path.exists('local_ssot_shadow.json'):
            try:
                with open('local_ssot_shadow.json', 'r') as f:
                    existing_ssot = json.load(f)
            except: pass
            
        # Check if existing SSoT uses the v4.9x Layer Model
        has_layer_model = "mutable_state" in existing_ssot
        
        # If payload provides naked keys but existing uses Layer Model, wrap the payload in mutable_state
        if has_layer_model and "mutable_state" not in payload and "immutable_background" not in payload:
            payload = {"mutable_state": payload}

        if existing_ssot:
            existing_portfolio = existing_ssot.get("mutable_state", {}).get("portfolio_snapshot", []) if has_layer_model else existing_ssot.get("portfolio_snapshot", [])
            payload_portfolio = payload.get("mutable_state", {}).get("portfolio_snapshot", []) if has_layer_model else payload.get("portfolio_snapshot", [])

            if payload_portfolio and existing_portfolio:
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
                merged_ssot = _deep_merge(existing_ssot, payload)
        else:
            merged_ssot = payload
            
        # Strip trade_lessons keys from ROOT, MUTABLE_STATE, and EXECUTION_PAYLOAD — they are routed to trade_lessons.json
        # ENH_49: Strip scrutiny_audit and EXECUTION_PAYLOAD to prevent SSoT bloat
        # Decisions and votes are now preserved in decision_log.json exclusively.
        def strip_scrutiny(obj):
            if isinstance(obj, list):
                for i in obj: strip_scrutiny(i)
            elif isinstance(obj, dict):
                obj.pop("scrutiny_audit", None)
                for v in obj.values(): strip_scrutiny(v)

        strip_scrutiny(merged_ssot)

        for tl_key in ("trade_lessons", "new_trade_lessons", "compressed_trade_lessons", "rule_mutations", "trade_lessons_revision", "EXECUTION_PAYLOAD"):
            merged_ssot.pop(tl_key, None)
            if "mutable_state" in merged_ssot:
                merged_ssot["mutable_state"].pop(tl_key, None)

        # SSoT schema validation — prune non-canonical top-level keys to prevent drift
        CANONICAL_SSOT_KEYS = {
            "state_context", "portfolio_snapshot", "forensic_intelligence",
            "runtime_flags", "macro_calendar_shield", "active_orders",
            "fin_account_gate", "registry_pointers", "overnight_posture",
            "strategy_timing", "lesson_integration", "immutable_background", "mutable_state",
            "remaining_cash_usd", "remaining_cash_eur", "unallocated_cash_eur", "unallocated_cash_usd", 
            "portfolio_total_value_usd", "portfolio_total_value_eur", "base_currency", "exchange_rate",
            "scouted_assets_tracked", "risk_metrics", "directive", "timestamp", "EXECUTION_PAYLOAD", "_meta"
        }
        
        # Prune from root
        non_canonical = [k for k in merged_ssot if k not in CANONICAL_SSOT_KEYS]
        for k in non_canonical:
            merged_ssot.pop(k)
            
        # Prune from mutable_state if using Layer Model
        if has_layer_model and "mutable_state" in merged_ssot:
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

        with open('local_ssot_shadow.json', 'w') as f:
            json.dump(merged_ssot, f, indent=2)

        # ENH_84: Immediate Memory Sync for Scouts
        new_scouts = merged_ssot.get("scouted_assets_tracked")
        if not new_scouts and "mutable_state" in merged_ssot:
            new_scouts = merged_ssot["mutable_state"].get("scouted_assets_tracked")
        if isinstance(new_scouts, list):
            global SCOUT_TICKERS, ALL_TICKERS
            SCOUT_TICKERS = list(set(new_scouts))
            ALL_TICKERS = list(dict.fromkeys(WATCHLIST_TICKERS + PORTFOLIO_TICKERS + MACRO_TICKERS + SCOUT_TICKERS))
            
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
                    if "rule" not in new_item and "content" in new_item:
                        new_item["rule"] = new_item.pop("content")
                    if "rule" not in new_item and "mandate" in new_item:
                        new_item["rule"] = new_item.get("mandate")
                    if "category" in new_item:
                        cat = new_item.pop("category")
                        if "tags" not in new_item: new_item["tags"] = []
                        if cat not in new_item["tags"]: new_item["tags"].append(cat)
                    normalized.append(new_item)
                else:
                    normalized.append({"id": i + 1, "rule": str(item)})
            return normalized

        # Trade lessons
        lessons_file = 'trade_lessons.json'
        incoming_lessons = []
        if extracted_compressed and isinstance(extracted_compressed, list):
            incoming_lessons.extend(extracted_compressed)
        if extracted_trade_lessons and isinstance(extracted_trade_lessons, list):
            incoming_lessons.extend(extracted_trade_lessons)

        if incoming_lessons:
            final_normalized = []
            existing_data = None
            if os.path.exists(lessons_file):
                try:
                    with open(lessons_file, 'r') as f:
                        existing_data = json.load(f)
                except: pass

            if extracted_compressed:
                # ENH_53 OVERWRITE MANDATE: Replacement phase for State Compression
                final_normalized = _normalize_lessons(incoming_lessons)
            else:
                # MERGE/UPSERT logic for new or session lessons
                existing_lessons = []
                if existing_data:
                    if isinstance(existing_data, dict):
                        existing_lessons = existing_data.get("trade_lessons", [])
                    elif isinstance(existing_data, list):
                        existing_lessons = existing_data
                
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
                                existing_by_id[item_id].update(item)
                                new_compiled_lessons.append(existing_by_id[item_id])
                                if rule_text: existing_by_text[rule_text] = existing_by_id[item_id]
                                continue
                                
                        if rule_text and rule_text in existing_by_text:
                            existing = existing_by_text[rule_text]
                            if isinstance(existing, dict):
                                orig_id = existing.get("id")
                                existing.update(item)
                                if orig_id is not None:
                                    existing["id"] = orig_id
                            continue
                            
                        new_compiled_lessons.append(item)
                        if rule_text: existing_by_text[rule_text] = item
                    else:
                        new_compiled_lessons.append(item)

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

                final_normalized = _normalize_lessons(preserved_lessons + new_compiled_lessons)
                
            if isinstance(existing_data, dict):
                existing_data["trade_lessons"] = final_normalized
                output_data = existing_data
            else:
                output_data = {"trade_lessons": final_normalized}
                
            with open(lessons_file, 'w') as f:
                json.dump(output_data, f, indent=2)
                
            # Update GLOBAL_STATE immediately to prevent stale session init payloads
            if "GLOBAL_STATE" in globals() or "GLOBAL_STATE" in locals():
                global GLOBAL_STATE
                GLOBAL_STATE["trade_lessons"] = final_normalized

            # Sync to Markdown registry
            try:
                convert_json_to_md('trade_lessons.json', 'trade_lessons.md')
            except Exception as e:
                print(f"Failed to sync lessons to MD: {e}")
        # Rule mutations
        if extracted_mutations and isinstance(extracted_mutations, list):
            rules_file = os.path.join('Gemini_Gem_Rules', 'rules.json')
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
        self.cycles: int = 0
        self.vwap_pointer: int = 0

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
        r = requests.get(url, timeout=2) # Changed session.get to requests.get for simplicity if session isn't defined globally
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

def get_finnhub_candles(symbol, resolution='D', count=250):
    if not USE_FINNHUB:
        return None
    try:
        to_ts = int(t_time.time())
        from_ts = to_ts - (count * 24 * 3600 * 1.5)
        url = f"https://finnhub.io/api/v1/stock/candle?symbol={symbol}&resolution={resolution}&from={int(from_ts)}&to={to_ts}&token={FINNHUB_API_KEY}"
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json()
            if data.get('s') == 'ok':
                return data
    except:
        pass
    
    # Fallback to yfinance if Finnhub fails (Zero-Cost Protocol Guard)
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="1y", interval="1d")
        if not df.empty and len(df) >= count:
            return {
                's': 'ok',
                'c': df['Close'].tolist(),
                'v': df['Volume'].tolist()
            }
    except:
        pass
    return None

async def run_finnhub_scout_sweep(target_sectors):
    """
    ENH_84: Zero-Cost Scout Sweep.
    Natively calculates SMA50/200 and RVOL from Finnhub OHLCV.
    Returns max 2 candidates to prevent context bloat.
    """
    global SCOUT_TICKERS, ALL_TICKERS
    
    # Generic sector mapping for the sweep
    sector_map = {
        "DIB": ["RCAT", "UMAC", "ONDS", "AVAV", "KTOS", "BA", "LMT", "NOC", "GD", "LHX"],
        "BIOTECH": ["DFTX", "BNTX", "MRNA", "VRTX", "AMGN", "REGN", "GILD"],
        "AI": ["PLTR", "SOUN", "BBAI", "NVDA", "MSFT", "GOOGL", "AMD"]
    }
    
    tickers_to_scan = []
    for sector in target_sectors:
        tickers_to_scan.extend(sector_map.get(sector.upper(), []))
    
    # Remove duplicates and already watched tickers
    # Remove duplicates and already watched tickers
    # Use lowercase comparison to ensure zero collisions
    current_all_upper = [t.upper() for t in ALL_TICKERS]
    tickers_to_scan = [t for t in tickers_to_scan if t.upper() not in current_all_upper]
    
    candidates = []
    for symbol in tickers_to_scan:
        # Rate Limit Protection: 30 calls/sec -> throttle to 0.05s
        await asyncio.sleep(0.05) 
        
        candles = get_finnhub_candles(symbol)
        if not candles or candles.get('s') != 'ok':
            continue
            
        c = candles.get('c', [])
        v = candles.get('v', [])
        if len(c) < 200:
            continue
            
        # Calculate Technicals natively
        sma50 = sum(c[-50:]) / 50
        sma200 = sum(c[-200:]) / 200
        last_price = c[-1]
        
        # Calculate RVOL (Current Vol / 20-day Avg Vol)
        avg_vol_20 = sum(v[-21:-1]) / 20
        last_vol = v[-1]
        rvol = last_vol / avg_vol_20 if avg_vol_20 > 0 else 0
        
        # Filtering logic: Golden Cross or high RVOL momentum
        if (last_price > sma50 > sma200) or (rvol > 2.5 and last_price > sma50):
            candidates.append({
                "ticker": symbol,
                "score": rvol + (1.0 if last_price > sma50 else 0)
            })
            
    # Strictly cap at 2 candidates per protocol
    candidates.sort(key=lambda x: x['score'], reverse=True)
    new_scouts = [cand['ticker'] for cand in candidates[:2]]
    
    # Integrate into processing queue
    if new_scouts:
        print(f"\n{CYAN}[ENH_84] Scout Integration: Found {new_scouts}{RESET}")
        for s in new_scouts:
            if s not in SCOUT_TICKERS:
                SCOUT_TICKERS.append(s)
        # Update systemic ALL_TICKERS queue (Deduplicated)
        ALL_TICKERS = list(dict.fromkeys(WATCHLIST_TICKERS + PORTFOLIO_TICKERS + MACRO_TICKERS + SCOUT_TICKERS))
    
    return new_scouts

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
    batch_prev_close = None
    pre_vol = 0
    post_vol = 0

    if quote_data:
        try:
            pre_price = quote_data.get('preMarketPrice')
            post_price = quote_data.get('postMarketPrice')
            reg_price = quote_data.get('regularMarketPrice')
            reg_open = quote_data.get('regularMarketOpen')
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
        api_price, api_vol, api_pre_vol, api_post_vol = get_live_chart_data(symbol, status)
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
        chart_pre_vol = get_premarket_volume_chart(symbol)
        if chart_pre_vol > 0:
            pre_vol = chart_pre_vol

    if price == 0 and USE_FINNHUB:
        fh_c, fh_pc = get_finnhub_quote(symbol)
        if fh_c:
            price = fh_c

    # --- Final fallback to fast_info ---
    try:
        fi = t_obj.fast_info
        if vol == 0:
            vol = fi.last_volume or fi.three_month_average_volume
        if price == 0:
            if status == "PRE-MARKET" and getattr(fi, 'pre_market_price', None):
                price = float(fi.pre_market_price)
            elif status in ("AFTER-HOURS", "CLOSED") and getattr(fi, 'post_market_price', None):
                price = float(fi.post_market_price)
            elif getattr(fi, 'last_price', None):
                price = float(fi.last_price)
    except:
        pass

    # --- Volume fallback ---
    if vol == 0:
        alt_vol = polygon_volume(symbol)
        if alt_vol:
            vol = alt_vol

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


def fetch_stocks(state):
    """
    Consolidates state into a split-delivery payload optimized for LLM context.
    - SSoT_JSON: Quantitative state (Prices, GEX, VWAP, Portfolio, Macro).
    - Trade_Lessons_MD: Qualitative lessons in Markdown format.
    """
    ssot = state.get("local_storage_state", {})
    ms = ssot.get("mutable_state", ssot)
    portfolio = ms.get("portfolio_snapshot", [])

    # 1. Quantitative SSoT
    tickers_data = state.get("tickers", [])
    compact_tickers = []
    for t in tickers_data:
        ticker_symbol = t.get("ticker")
        
        hist_ctx = None
        for p in portfolio:
            if p.get("ticker") == ticker_symbol:
                hist_ctx = p.get("historical_context")
                break

        ticker_payload = {
            "ticker": ticker_symbol,
            "price": t.get("price"),
            "vwap": t.get("vwap"),
            "gap": t.get("gap_percent"),
            "net_gex": t.get("net_gex_total"),
            "dealer": t.get("dealer_posture"),
            "score": t.get("score"),
            "rsi": t.get("rsi")
        }
        
        # ENH_84: Inject Unverified Flag for Scout Candidates (Hardened Normalized Check)
        if ticker_symbol.strip().upper() in [s.strip().upper() for s in SCOUT_TICKERS]:
            ticker_payload["institutional_status"] = "Unverified Institutional Status"
        
        if hist_ctx:
            ticker_payload["historical_context"] = hist_ctx
            
        compact_tickers.append(ticker_payload)
    
    compact_portfolio = []
    for p in portfolio:
        compact_portfolio.append({
            "ticker": p.get("ticker"),
            "shares": p.get("shares"),
            "wac": p.get("wac"),
            "historical_context": p.get("historical_context"),
            "status": p.get("status"),
            "trade_state": p.get("trade_state")
        })

    macro_est = {}
    for t in tickers_data:
        if t.get("ticker") in ['^VIX', 'IEF', 'UUP', 'SPY']:
            macro_est[t["ticker"]] = {
                "price": t.get("price"),
                "chg": t.get("session_change_pct")
            }

    ssot_json = {
        "timestamp": state.get("timestamp"),
        "status": state.get("status"),
        "tickers": compact_tickers,
        "portfolio": compact_portfolio,
        "macro": macro_est,
        "unallocated_cash": ms.get("forensic_intelligence", {}).get("unallocated_cash_eur", 0)
    }

    # 2. Qualitative Lessons (Markdown)
    lessons = state.get("trade_lessons", [])
    md_lines = ["## 🔬 Trade Lessons Registry"]
    
    # Simple formatting per pseudo-code
    for l in lessons:
        l_id = l.get('id', '?')
        rule = l.get('rule', l.get('lesson', ''))
        tag_str = " ".join([f"#{t}" for t in l.get('tags', [])])
        md_lines.append(f"- **L-{l_id}:** {rule} {tag_str}")
    
    trade_lessons_md = "\n".join(md_lines)
    

    # Return bifurcated structure + full state for dashboard compatibility
    output = {
        "SSoT_JSON": ssot_json,
        "Trade_Lessons_MD": trade_lessons_md
    }
    output.update(state)
    return output


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
    global GLOBAL_STATE
    print(f"{CYAN}Initializing Gemini Gem Dashboard v18.0 (Scout Intelligence Grounding)...{RESET}")
    tickers_obj = {sym: yf.Ticker(sym) for sym in ALL_TICKERS}
    if SCOUT_TICKERS:
        print(f"{CYAN}[ENH_84] Active Scouts Tracked: {SCOUT_TICKERS}{RESET}")

    print(f"{YELLOW}Performing initial heavy fetch...{RESET}")
    for sym, obj in tickers_obj.items():
        print(f"Loading {sym}...", end="\r")
        update_history_and_technicals(sym, obj)

    # Initial GEX population — uses batch quotes for spot price, then computes
    # GEX per ticker with a throttled delay to avoid Yahoo Finance rate limits.
    print(f"{YELLOW}Loading initial GEX profiles...{RESET}")
    status = get_market_status()
    boot_quotes = get_batch_quotes(ALL_TICKERS)
    for idx, sym in enumerate(ALL_TICKERS):
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
            print(f"  GEX [{idx+1}/{len(ALL_TICKERS)}] {sym}...", end="\r")
            cache.gex[sym] = get_gex_profile(obj, spot)
            # GEX loop sleep (lines 914)
            t_time.sleep(0.2)  # OPTIMIZED: Reduced from 2s
        else:
            cache.gex[sym] = {
                'net_gex': 0.0, 'flip_price': 0.0,
                'inventory_velocity_delta': 0.0, 'gex_slope': 0.0,
                'flip_proximity_percent': 0.0, 'strike_oi_magnet': 0.0
            }
    print(f"{GREEN}GEX profiles loaded.{RESET}                    ")

    try:
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            print("\033[?25l", end="", flush=True)
            ny_now = datetime.now(ZoneInfo("America/New_York"))
            status = get_market_status()

            cache.cycles += 1
            # Trigger heavy refresh on first cycle or every 10th cycle
            is_heavy = (cache.cycles == 1 or cache.cycles % HISTORY_REFRESH_CYCLES == 0)

            # Immediately notify frontend that heavy fetching has begun
            if is_heavy:
                if GLOBAL_STATE.get("tickers"):
                    GLOBAL_STATE["is_heavy_refresh"] = True
                
                # ENH_84: Run Zero-Cost Scout Sweep on Heavy Cycle
                try:
                    # Target current core sectors for scouting
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(run_finnhub_scout_sweep(["DIB", "BIOTECH", "AI"]))
                    loop.close()
                except Exception as e:
                    print(f"{RED}Scout Sweep Error: {e}{RESET}")

            # Sync tickers_obj with ALL_TICKERS (Moved after scout sweep for immediate processing)
            for sym in ALL_TICKERS:
                if sym not in tickers_obj:
                    tickers_obj[sym] = yf.Ticker(sym)
            keys_to_remove = [sym for sym in list(tickers_obj.keys()) if sym not in ALL_TICKERS]
            for sym in keys_to_remove:
                del tickers_obj[sym]

            BATCH_SIZE = 10

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
            print(f"\n{BOLD}Gemini Gem Dashboard (v18.0 - Scout Intelligence Grounding){RESET}")
            if SCOUT_TICKERS:
                print(f"{CYAN}Active Scouts: {SCOUT_TICKERS}{RESET}")
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
            print(sep_line)

            data = []

            macro_state = {
                'IEF': {'price': 0.0, 'gap': 0.0, 'trend': 'FLAT'},
                '^VIX': {'price': 0.0, 'gap': 0.0},
                'VIXY': {'price': 0.0, 'gap': 0.0}
            }

            # Pre-load portfolio context for historical_context injection
            portfolio_ctx_map = {}
            if os.path.exists('local_ssot_shadow.json'):
                try:
                    with open('local_ssot_shadow.json', 'r') as f:
                        s_data = json.load(f)
                        p_snap = s_data.get("mutable_state", {}).get("portfolio_snapshot", [])
                        if not p_snap: p_snap = s_data.get("portfolio_snapshot", [])
                        for p in p_snap:
                            if p.get("historical_context"):
                                portfolio_ctx_map[p["ticker"].upper()] = p["historical_context"]
                except: pass

            for i, sym in enumerate(ALL_TICKERS):
                # Dynamic sleep: slower on heavy refresh to respect rate limits
                if is_heavy or sym not in cache.history or cache.history[sym].empty:
                    t_time.sleep(0.5) 
                else:
                    t_time.sleep(0.05)

                obj = tickers_obj[sym]

                if is_heavy or sym not in cache.history or cache.history[sym].empty:
                    update_history_and_technicals(sym, obj)

                update_price_tick(sym, obj, status, batch_quotes.get(sym))

                # GEX is heavy, run only on heavy cycles after price is known
                if is_heavy:
                    spot_price = cache.prices.get(sym)
                    if spot_price and spot_price > 0:
                        gex_profile = get_gex_profile(obj, spot_price)
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
                # GoogleDrive://Gemini_Gem_Rules/rules > ENH_20 (Synthetic GEX Logic)
                _raw_gex = float(gex_data.get('net_gex', 0.0))
                if _raw_gex > 0:
                    _dealer_posture = "LONG_GAMMA"
                elif _raw_gex < 0:
                    _dealer_posture = "SHORT_GAMMA"
                else:
                    _dealer_posture = "NEUTRAL"

                data.append({
                    "ticker": sym,
                    "session": cache.session.get(sym),
                    "session_liquidity": cache.session_liquidity.get(sym),
                    "status": "ACTIVE",
                    "institutional_status": "Unverified Institutional Status" if sym in SCOUT_TICKERS else "Active",
                    
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
                    # trend_score thresholds: GoogleDrive://Gemini_Gem_Rules/rules > TREND_SCORE_UP_THRESHOLD (3) / TREND_SCORE_DOWN_THRESHOLD (-3)
                    # For INVERSE_MACRO tickers: negated so rising price = negative (bearish for equities)
                    "trend_score": int(-techs.get("Trend_Score", 0)) if sym in INVERSE_MACRO else int(techs.get("Trend_Score", 0)),
                    
                    # Macro Context & Volatility Regime (Rules > VOLATILITY_REGIME_THRESHOLDS)
                    "vix_price": float(cache.prices.get('^VIX', 0.0)),
                    "volatility_regime": "HIGH_VOL" if cache.prices.get('^VIX', 0.0) > 20.0 else "LOW_VOL" if cache.prices.get('^VIX', 0.0) < 12.0 and cache.prices.get('^VIX', 0.0) > 0 else "NORMAL",
                    "vixy_roc": round(float(macro_state.get('VIXY', {}).get('gap', 0.0)), 2),

                    # GEX — field names per ENH_32 canonical schema in rules.json
                    "net_gex_total": _raw_gex,
                    "gex_exposure": _raw_gex,  # Normalized GEX exposure (position-level scaling done by SSoT Gem)
                    "dealer_posture": _dealer_posture,  # GoogleDrive://Gemini_Gem_Rules/rules > dealer_posture_logic
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
                    # trend label thresholds: GoogleDrive://Gemini_Gem_Rules/rules > TREND_SCORE_UP_THRESHOLD / TREND_SCORE_DOWN_THRESHOLD
                    # For INVERSE_MACRO: JSON trend uses equity-perspective (negated score)
                    "trend": ("UP" if (-ts if sym in INVERSE_MACRO else ts) >= 3 else "DOWN" if (-ts if sym in INVERSE_MACRO else ts) <= -3 else "FLAT"),
                    
                    # Phase 6 Enhancements (Already computed inline above)

                    "note": note.strip(),
                    "historical_context": portfolio_ctx_map.get(sym.upper())
                })



            print("-" * table_width)
            ief = macro_state.get('IEF', {})
            ief_gap = float(ief['gap'])
            ief_price = float(ief['price'])
            # IEF bond alert — threshold: GoogleDrive://Gemini_Gem_Rules/rules > system_thresholds > IEF_YIELD_ALERT_THRESHOLD (-0.15)
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
            if os.path.exists('trade_lessons.json'):
                try:
                    with open('trade_lessons.json', 'r') as f:
                        lessons_data = json.load(f)
                        if isinstance(lessons_data, dict) and "trade_lessons" in lessons_data:
                            supplemental_lessons = lessons_data["trade_lessons"]
                        elif isinstance(lessons_data, list):
                            supplemental_lessons = lessons_data
                except: pass
            
            # Fallback to trade_lessons.md if JSON is missing or empty
            if not supplemental_lessons and os.path.exists('trade_lessons.md'):
                try:
                    with open('trade_lessons.md', 'r', encoding='utf-8') as f:
                        md_content = f.read()
                        # Extract basic lesson structure from MD bullets
                        md_matches = re.findall(r'-\s*\*\*L-(\d+):\*\*\s*(.*?)(?=\s*\n-|\s*\n##|$)', md_content, re.DOTALL)
                        for l_id, content in md_matches:
                            tags = re.findall(r'#([\w-]+)', content)
                            rule = re.sub(r'#[\w-]+', '', content).strip()
                            supplemental_lessons.append({"id": int(l_id), "rule": rule, "tags": tags})
                    if supplemental_lessons:
                        print(f"{YELLOW}Loaded {len(supplemental_lessons)} lessons from trade_lessons.md (JSON fallback).{RESET}")
                except: pass

            supplemental_ssot = {}
            if os.path.exists('local_ssot_shadow.json'):
                try:
                    with open('local_ssot_shadow.json', 'r') as f:
                        supplemental_ssot = json.load(f)
                except: pass

            final_output = {
                "_meta": {
                    "source": "Real-time Exchange Feed",
                    "reliability": "High"
                },
                "timestamp": datetime.now(ZoneInfo("America/New_York")).strftime('%Y-%m-%d %H:%M:%S'),
                "status": status,
                "is_heavy_refresh": False,
                "tickers": data,
                "watchlist": WATCHLIST_TICKERS,
                "local_storage_state": supplemental_ssot,
                "trade_lessons": supplemental_lessons
            }
            GLOBAL_STATE = final_output

            wait_start = t_time.time()
            while True:
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
