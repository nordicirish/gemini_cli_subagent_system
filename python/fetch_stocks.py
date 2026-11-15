import sys
# Monkeypatch curl_cffi to use chrome120 instead of chrome to avoid TLS errors on Windows
try:
    from curl_cffi import requests as curl_requests
    _orig_init = curl_requests.Session.__init__
    def _patched_init(self, *args, **kwargs):
        if kwargs.get('impersonate') == 'chrome':
            kwargs['impersonate'] = 'chrome120'
        _orig_init(self, *args, **kwargs)
    curl_requests.Session.__init__ = _patched_init
except Exception as e:
    sys.stderr.write(f"[Warning] Failed to monkeypatch curl_cffi: {e}\n")

import yfinance as yf
import warnings
# Silence yfinance internal inplace deprecation warnings triggered by Pandas 3.1+ (PDEP-8)
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"^yfinance")
import pandas as pd
from pandas.tseries.holiday import (
    AbstractHolidayCalendar,
    Holiday,
    nearest_workday,
    USMartinLutherKingJr,
    USPresidentsDay,
    USMemorialDay,
    USLaborDay,
    USThanksgivingDay,
    GoodFriday,
)
import numpy as np
import requests
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
import base64
from dataclasses import dataclass
import os
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
import time as t_time
import sys
import json
import pyperclip
import re
import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from scipy.stats import norm
from yfinance.data import YfData
import threading
import logging
import collections.abc

logger = logging.getLogger(__name__)
from types import MappingProxyType
from atomic_io import atomic_write_json, atomic_read_json, atomic_update_json
from tool_registry import authorize_financial_mutation
from background_intelligence import (
    BackgroundIntelligenceCoordinator,
    BackgroundResultApplicator,
    BatchObservedResult,
    ChildObservedResult,
    MarketDataProvenance,
    ObservationKind,
    ObservedResult,
    SessionReferenceValue,
    TimestampedValue,
    WorkKey,
    WorkResult,
    WorkType,
    WorkStatus,
    current_market_session_date,
    parse_utc_iso8601_timestamp,
    validate_market_data_observation,
    validate_session_date,
    validate_utc_iso8601_timestamp,
)

try:
    from persistence import get_persistence_service
except ImportError:
    from python.persistence import get_persistence_service
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
        "ROLE: Market-structure signal scout.\n\n"
        "TASK: Perform a live web search to identify top trending equities in the '{category}' sector of the US stock market (NASDAQ/NYSE) that are being flagged today by market commentators, screener reports, and technical analysis data providers for breakout conditions.\n\n"
        "CONTEXT & TEMPORAL INTEGRITY:\n"
        "Verify your current context using the attached SSOT JSON block. Ensure your logic adheres strictly to MANDATE_11 and the Anti-Hallucination Core.\n\n"
        "TEMPORAL REALITY OVERRIDE: The current date and year are defined dynamically by the timestamp inside the SSoT JSON payload (e.g., in the last_updated, system_time, or ny_iso fields). The live Google Search tool accesses the real-world internet corresponding to this SSoT timestamp. You MUST NOT hallucinate a temporal dissonance or claim you cannot perform web searches for the SSoT date. DO NOT perform \"SYNTHETIC SCAN ENFORCED\" or simulated searches. Use the live search tool and natively ingest the real-time web data matching the timeline defined in the SSoT.\n\n"
        "SCAN REQUIREMENTS (WEB AGGREGATION):\n"
        "Because you cannot execute raw market data calculations, you must construct targeted search queries to find equities in the '{category}' sector matching the qualitative descriptions of our structural filters in today's news flow:\n\n"
        "Search for Price-Action Mentions: Query for tickers associated with terms like \"High-Volume Breakout,\" \"Relative Volume surge,\" \"Range Expansion,\" \"ATR breakout,\" \"Multi-Day Momentum,\" and \"Gap-and-Hold.\"\n\n"
        "Search for Structural Momentum Mentions: Look for analysis citing stocks trading \"above the 20 EMA and 50 EMA,\" displaying \"RSI in the momentum zone,\" \"MACD histogram rising,\" or experiencing \"positive volume delta / institutional accumulation.\"\n\n"
        "Liquidity Context: Cross-reference the mentions to classify the breakout type (e.g., low-float momentum, mid-cap trend, or large-cap continuation).\n\n"
        "RESTRICTION: Do not give investment advice, price targets, or buy/sell language. Focus strictly on pattern recognition, market-structure signals, and aggregating the technical data found in your search.\n\n"
        "OUTPUT FORMAT:\n"
        "1. Your final output MUST begin with a strict markdown summary explaining the qualitative logic behind your top picks based on the technical reports you aggregated.\n"
        "2. Follow the summary immediately with a valid JSON list of their uppercase ticker symbols enclosed inside standard json code fences. For example:\n"
        "```json\n"
        "[\"SYM1\", \"SYM2\", \"SYM3\"]\n"
        "```\n"
        "3. Do not include any other markdown text, formatting, or conversational boilerplate after the JSON block."
    )

def load_news_scan_prompt() -> str:
    """Loads the news scan prompt template from prompts/news_scan_prompt.txt."""
    prompt_path = os.path.join(BASE_DIR, "prompts/news_scan_prompt.txt")
    if os.path.exists(prompt_path):
        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception as e:
            print(f"[Warning] Failed to read news scan prompt from {prompt_path}: {e}")
    return (
        "SYSTEM DIRECTIVE: MACRO & STOCK NEWS SCAN\n"
        "Perform a live Google Search and provide a concise, high-signal catalyst summary for stock ticker '{symbol}'.\n"
        "Identify: (1) Main catalyst or news driver today, (2) Earnings/SEC/analyst updates, (3) Market sentiment score (-1.0 Bearish to +1.0 Bullish).\n"
        "Return ONLY a clean JSON object with keys: \"headline\", \"summary\", \"catalyst_type\", \"sentiment_score\"."
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
        
    scout_prompt_content = (
        "ROLE: Market-structure signal scout.\n\n"
        "TASK: Perform a live web search to identify top trending equities in the '{category}' sector of the US stock market (NASDAQ/NYSE) that are being flagged today by market commentators, screener reports, and technical analysis data providers for breakout conditions.\n\n"
        "CONTEXT & TEMPORAL INTEGRITY:\n"
        "Verify your current context using the attached SSOT JSON block. Ensure your logic adheres strictly to MANDATE_11 and the Anti-Hallucination Core.\n\n"
        "TEMPORAL REALITY OVERRIDE: The current date and year are defined dynamically by the timestamp inside the SSoT JSON payload (e.g., in the last_updated, system_time, or ny_iso fields). The live Google Search tool accesses the real-world internet corresponding to this SSoT timestamp. You MUST NOT hallucinate a temporal dissonance or claim you cannot perform web searches for the SSoT date. DO NOT perform \"SYNTHETIC SCAN ENFORCED\" or simulated searches. Use the live search tool and natively ingest the real-time web data matching the timeline defined in the SSoT.\n\n"
        "SCAN REQUIREMENTS (WEB AGGREGATION):\n"
        "Because you cannot execute raw market data calculations, you must construct targeted search queries to find equities in the '{category}' sector matching the qualitative descriptions of our structural filters in today's news flow:\n\n"
        "Search for Price-Action Mentions: Query for tickers associated with terms like \"High-Volume Breakout,\" \"Relative Volume surge,\" \"Range Expansion,\" \"ATR breakout,\" \"Multi-Day Momentum,\" and \"Gap-and-Hold.\"\n\n"
        "Search for Structural Momentum Mentions: Look for analysis citing stocks trading \"above the 20 EMA and 50 EMA,\" displaying \"RSI in the momentum zone,\" \"MACD histogram rising,\" or experiencing \"positive volume delta / institutional accumulation.\"\n\n"
        "Liquidity Context: Cross-reference the mentions to classify the breakout type (e.g., low-float momentum, mid-cap trend, or large-cap continuation).\n\n"
        "RESTRICTION: Do not give investment advice, price targets, or buy/sell language. Focus strictly on pattern recognition, market-structure signals, and aggregating the technical data found in your search.\n\n"
        "OUTPUT FORMAT:\n"
        "1. Your final output MUST begin with a strict markdown summary explaining the qualitative logic behind your top picks based on the technical reports you aggregated.\n"
        "2. Follow the summary immediately with a valid JSON list of their uppercase ticker symbols enclosed inside standard json code fences. For example:\n"
        "```json\n"
        "[\"SYM1\", \"SYM2\", \"SYM3\"]\n"
        "```\n"
        "3. Do not include any other markdown text, formatting, or conversational boilerplate after the JSON block."
    )
    
    market_snapshot_content = (
        "SYSTEM DIRECTIVE: ROUTINE TURN EXECUTION\n\n"
        "You are receiving the latest Market Snapshot and Portfolio State.\n"
        "1. Parse the JSON payload and synchronize your local context.\n"
        "2. Evaluate current 'risk_regime' and 'dealer_posture' shifts.\n"
        "3. Route the data through the Consensus Pipeline (Data Analyst -> Council Debate -> Synthesis) for any required rebalancing, entries, or defensive trims.\n"
        "4. Conclude your turn by outputting the final EXECUTION_PAYLOAD."
    )
    
    boot_prompt_content = (
        "SYSTEM BOOT: COUNCIL SESSION INITIALIZATION\n\n"
        "You are receiving the full SSoT state and live market data for a new session.\n"
        "Execute the following Stage 0 Boot Sequence:\n\n"
        "1. BASELINE SYNC (ENH_31): Ground all portfolio prices via Google Search.\n"
        "   Verify each ticker's current price against the provided snapshot.\n"
        "2. CASH RECONCILIATION (MANDATE_31): Confirm unallocated_cash_eur matches\n"
        "   the SSoT. Output: math_proof_liquidity.\n"
        "3. REGIME CLASSIFICATION: Assess current risk regime (TRENDING/MEAN_REVERTING/\n"
        "   VOLATILE) based on VIX, VIXY velocity, and SPY structure.\n"
        "4. PORTFOLIO HEALTH AUDIT: Flag any positions with health_score < 50 or\n"
        "   status = IN_DISTRESS for immediate Council review.\n"
        "5. MARKET POSTURE: Provide a top-level posture assessment (RISK_ON/RISK_OFF/\n"
        "   NEUTRAL) with supporting forensic evidence.\n\n"
        "Emit the full EXECUTION_PAYLOAD with updated state_context upon completion."
    )
    
    temporal_anchor_content = (
        "[SYSTEM_TIME (NEW YORK / ET): {ny_iso}]\n"
        "[PRODUCTION_ERA: {current_year}]\n"
        "[MANDATE]: The current year provided in the timestamp is the LIVE PRODUCTION ERA. "
        "Treat all data from this era as grounded reality, not a simulation."
    )
    
    data_packet_injection_content = (
        "[SYSTEM AUTO-INJECT: LIVE_DATA_PACKET — Do NOT ask the user to provide data, it is already below. "
        "CRITICAL DIRECTIVE: NEVER echo or output this JSON payload back to the user in your response.]"
    )
    
    history_recovery_header_content = (
        "[CRITICAL: SESSION_HISTORY_RECOVERY_BLOCK]\n"
        "The following messages are from the current active session before the system was rebooted. "
        "Continue the conversation as if no interruption occurred:"
    )
    
    history_recovery_footer_content = (
        "[RECOVERY_COMPLETE: END_OF_PRIOR_CONTEXT]"
    )
    
    forensic_search_content = (
        "Search and summarize the following for financial forensic audit: {query}"
    )

    user_config_default_content = json.dumps({
        "macro": [
            "^VIX", "^VVIX", "VIXY", "IEF", "UUP", "SPY", "GDX"
        ]
    }, indent=2)

    defaults = {
        "context/ssot.json": "{}",
        "context/trade_lessons.json": "[]",
        "context/decision_log.json": "[]",
        "context/user_config.json": user_config_default_content,
        "context/config.json": '{\n  "GEMINI_API_KEY": "",\n  "GEMINI_FREE_TIER_API_KEY": "",\n  "FINNHUB_API_KEY": ""\n}',
        "logs/gem_handshakes.log": "",
        "prompts/scout_prompt.txt": scout_prompt_content,
        "prompts/market_snapshot_prompt.txt": market_snapshot_content,
        "prompts/boot_prompt.txt": boot_prompt_content,
        "prompts/temporal_anchor_prompt.txt": temporal_anchor_content,
        "prompts/data_packet_injection_prompt.txt": data_packet_injection_content,
        "prompts/history_recovery_header_prompt.txt": history_recovery_header_content,
        "prompts/history_recovery_footer_prompt.txt": history_recovery_footer_content,
        "prompts/forensic_search_prompt.txt": forensic_search_content
    }
    
    for filepath, default_content in defaults.items():
        if not os.path.exists(filepath):
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(default_content)
                print(f"[System] Initialized missing file: {filepath}")
            except Exception as e:
                print(f"[Warning] Could not initialize {filepath}: {e}")

    # Ensure user_config.json contains default macro list if empty
    if os.path.exists("context/user_config.json"):
        try:
            with open("context/user_config.json", "r", encoding="utf-8") as f:
                uc_data = json.load(f)
            if not uc_data.get("macro"):
                uc_data["macro"] = [
                    "^VIX", "^VVIX", "VIXY", "IEF", "UUP", "SPY", "GDX"
                ]
                with open("context/user_config.json", "w", encoding="utf-8") as f:
                    json.dump(uc_data, f, indent=2)
        except Exception as e:
            pass

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

# Scout filter config — persisted in config.json, driven by UI
SCOUT_LIMIT = max(1, min(5, int(config.get("SCOUT_LIMIT", 3))))
SCOUT_MAX_RSI = max(30, min(100, int(config.get("SCOUT_MAX_RSI", 75))))

# ─── Persistent user config ───────────────────────────────────────────────────
USER_CONFIG_FILE = 'context/user_config.json'

SCOUT_TICKER_MAP = config.get("SCOUT_TICKER_MAP", {})
if not SCOUT_TICKER_MAP and os.path.exists('config.json'):
    try:
        with open('config.json', 'r', encoding='utf-8') as _cf:
            _root_cfg = json.load(_cf)
            if isinstance(_root_cfg, dict) and "SCOUT_TICKER_MAP" in _root_cfg:
                SCOUT_TICKER_MAP = _root_cfg.get("SCOUT_TICKER_MAP", {})
    except Exception:
        pass


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

BACKGROUND_COORDINATOR = BackgroundIntelligenceCoordinator(
    max_workers=2,
    max_queue_size=50
)
BACKGROUND_RESULT_APPLICATOR = BackgroundResultApplicator(
    coordinator=BACKGROUND_COORDINATOR
)

def get_background_coordinator() -> BackgroundIntelligenceCoordinator:
    return BACKGROUND_COORDINATOR

def get_background_result_applicator() -> BackgroundResultApplicator:
    return BACKGROUND_RESULT_APPLICATOR

def apply_background_intelligence_results(
    coordinator: Optional[BackgroundIntelligenceCoordinator] = None,
    applicator: Optional[BackgroundResultApplicator] = None,
):
    """
    Drains results from BACKGROUND_COORDINATOR (or explicit coordinator) and applies them
    under monotonic sequence and observation timestamp validation.
    Constructs replacement intelligence state and publishes it atomically
    through the immutable snapshot contract while holding the relevant per-key lock.
    """
    eff_coord = BACKGROUND_COORDINATOR if coordinator is None else coordinator
    eff_app = BACKGROUND_RESULT_APPLICATOR if applicator is None else applicator
    results = eff_coord.drain_results()
    if results:
        with DYNAMIC_SCOUT_CACHE_LOCK:
            def _publish_result(res: WorkResult):
                if res.key.work_type == WorkType.GROUNDING.value:
                    cache.qualitative_grounding = dict(cache.qualitative_grounding)
                    cache.qualitative_grounding[res.key.target] = res.data
                    update_global_state_snapshot(
                        lambda s: {**s, "qualitative_grounding": dict(cache.qualitative_grounding)}
                    )
                elif res.key.work_type == WorkType.SCOUT.value:
                    DYNAMIC_SCOUT_CACHE.clear()
                    DYNAMIC_SCOUT_CACHE.update(temp_scout_cache)
                    SCOUTED_TICKER_TO_CATEGORY_MAP.clear()
                    SCOUTED_TICKER_TO_CATEGORY_MAP.update(temp_scout_map)
                    update_global_state_snapshot(
                        lambda s: {**s, "scout_categories_loaded": list(PROCESSED_SCOUT_CATEGORIES)}
                    )

            temp_grounding = dict(cache.qualitative_grounding)
            temp_scout_cache = dict(DYNAMIC_SCOUT_CACHE)
            temp_scout_map = dict(SCOUTED_TICKER_TO_CATEGORY_MAP)

            eff_app.apply_results(
                results=results,
                grounding_target=temp_grounding,
                scout_cache_target=temp_scout_cache,
                scout_category_map=temp_scout_map,
                on_publish=_publish_result
            )


MARKET_DATA_COORDINATOR = BackgroundIntelligenceCoordinator(
    max_workers=4,
    max_queue_size=100,
    max_results_size=200,
)
MARKET_DATA_RESULT_APPLICATOR = BackgroundResultApplicator(
    coordinator=MARKET_DATA_COORDINATOR
)
_MARKET_DATA_FALLBACK_STATE_LOCK = threading.Lock()
_MARKET_DATA_FALLBACK_REQUESTS: Dict[str, dict] = {}


def get_market_data_coordinator() -> BackgroundIntelligenceCoordinator:
    return MARKET_DATA_COORDINATOR


def get_market_data_result_applicator() -> BackgroundResultApplicator:
    return MARKET_DATA_RESULT_APPLICATOR


GEX_REFRESH_TTL_SECONDS = 1800

HEAVY_MARKET_DATA_COORDINATOR = BackgroundIntelligenceCoordinator(
    max_workers=2,
    max_queue_size=64,
    max_results_size=128,
    worker_name_prefix="HeavyMarketDataWorker",
)
HEAVY_MARKET_DATA_RESULT_APPLICATOR = BackgroundResultApplicator(
    coordinator=HEAVY_MARKET_DATA_COORDINATOR
)


def get_heavy_market_data_coordinator() -> BackgroundIntelligenceCoordinator:
    return HEAVY_MARKET_DATA_COORDINATOR


def get_heavy_market_data_result_applicator() -> BackgroundResultApplicator:
    return HEAVY_MARKET_DATA_RESULT_APPLICATOR


def _epoch_to_utc_iso8601(raw_ts: Any) -> Optional[str]:
    """
    Converts a provider Unix epoch timestamp (seconds) into a strict UTC ISO-8601 string.
    Preserves non-empty strings verbatim so strict validation rejects naive/non-UTC formats fail-closed.
    Returns None when missing or non-finite/non-positive so missing-timestamp checks fail closed.
    """
    if raw_ts is None or isinstance(raw_ts, bool):
        return None
    if isinstance(raw_ts, str):
        return raw_ts if raw_ts.strip() else None
    if isinstance(raw_ts, (int, float)):
        if not math.isfinite(raw_ts) or raw_ts <= 0:
            return None
        try:
            dt = datetime.fromtimestamp(float(raw_ts), tz=timezone.utc)
            iso = dt.isoformat()
            return iso[:-6] + "Z" if iso.endswith("+00:00") else iso
        except (OSError, OverflowError, ValueError):
            return None
    return None

def _execute_scout_discovery_raw(category: str, cancel_check: Optional[Callable[[], bool]] = None) -> list:
    """
    Executes a dynamic scout discovery query via Antigravity Agent.
    Executed by background worker threads; does not touch coordinator state directly.
    """
    category_clean = category.strip().upper()
    from agent_framework import AntigravityAgentRunner
    runner = AntigravityAgentRunner()

    scout_prompt = load_scout_prompt()
    prompt = scout_prompt.replace("{category}", category_clean)

    ssot_content = "{}"
    try:
        service = get_persistence_service()
        ssot_data = service.get_ssot_data()
        if ssot_data:
            ssot_content = json.dumps(ssot_data, indent=2)
    except Exception as e:
        print(f"[Warning] Failed to load SSoT for scout prompt via persistence service: {e}")

    rules_content = ""
    try:
        rules_path = "gem_trading_rules/rules.md"
        if os.path.exists(rules_path):
            with open(rules_path, "r", encoding="utf-8") as rf:
                rules_text = rf.read()
                subset = []
                m11_start = rules_text.find("MANDATE_11_RESEARCH_SYNC")
                if m11_start == -1:
                    m11_start = rules_text.find("MANDATE_11")
                if m11_start != -1:
                    line_start = rules_text.rfind("\n", 0, m11_start)
                    if line_start == -1: line_start = 0
                    next_header = len(rules_text)
                    for pattern in ["<a name=", "\n- **[MANDATE_", "\n## ", "\n### "]:
                        pos = rules_text.find(pattern, m11_start)
                        if pos != -1 and pos < next_header:
                            next_header = pos
                    subset.append(rules_text[line_start:next_header].strip())

                anti_start = rules_text.lower().find("anti hallucination core")
                if anti_start == -1:
                    anti_start = rules_text.lower().find("anti-hallucination core")
                if anti_start != -1:
                    line_start = rules_text.rfind("\n", 0, anti_start)
                    if line_start == -1: line_start = 0
                    next_header = len(rules_text)
                    for pattern in ["<a name=", "\n- **[MANDATE_", "\n## ", "\n### "]:
                        pos = rules_text.lower().find(pattern, anti_start)
                        if pos != -1 and pos < next_header:
                            next_header = pos
                    subset.append(rules_text[line_start:next_header].strip())

                if subset:
                    rules_content = "\n\n".join(subset)
                else:
                    rules_content = "\n".join(rules_text.splitlines()[:200])
    except Exception as e:
        print(f"[Warning] Failed to load rules.md for scout prompt: {e}")

    prompt = (
        f"{prompt}\n\n"
        f"### ATTACHED SSOT JSON BLOCK:\n"
        f"```json\n{ssot_content}\n```\n\n"
        f"### RELEVANT SYSTEM RULES & MANDATES (rules.md):\n"
        f"```markdown\n{rules_content}\n```"
    )

    response = runner.run_prompt(prompt, mode="FAST", cancel_check=cancel_check)
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

    valid_tickers = [t.strip().upper() for t in tickers if is_valid_ticker(t)][:5]
    if not valid_tickers:
        raise ValueError(f"No valid tickers discovered for category {category_clean}")
    return valid_tickers

def enqueue_scout_discovery(category: str, obs_time: Optional[str] = None) -> Tuple[bool, Optional[Any]]:
    if not category:
        return False, None
    category_clean = category.strip().upper()
    return BACKGROUND_COORDINATOR.enqueue(
        work_type=WorkType.SCOUT.value,
        target=category_clean,
        obs_time=obs_time,
        fn=_execute_scout_discovery_raw,
        args=(category_clean,)
    )

def _get_dynamic_scout_tickers(category: str) -> list:
    """
    Non-blocking dynamic scout ticker retrieval.
    Returns cached tickers if valid (< 4 hours).
    If cache miss or expired, enqueues background discovery and immediately
    returns fallback configuration tickers so market monitoring is never blocked.
    """
    global DYNAMIC_SCOUT_CACHE
    category_clean = category.strip().upper()
    now = t_time.time()

    with DYNAMIC_SCOUT_CACHE_LOCK:
        if category_clean in DYNAMIC_SCOUT_CACHE:
            cached = DYNAMIC_SCOUT_CACHE[category_clean]
            if isinstance(cached, dict):
                cached_time = cached.get("cached_at")
                valid_cache_time = (
                    isinstance(cached_time, (int, float))
                    and not isinstance(cached_time, bool)
                )
                if valid_cache_time:
                    try:
                        valid_cache_time = math.isfinite(cached_time)
                        if valid_cache_time:
                            age = now - cached_time
                            valid_cache_time = 0 <= age < 14400
                    except (OverflowError, TypeError, ValueError):
                        valid_cache_time = False

                if valid_cache_time:
                    return cached.get("tickers", [])

    # Enqueue background discovery without blocking; no fabricated source observation timestamp
    enqueue_scout_discovery(category_clean, obs_time=None)

    fallback = SCOUT_TICKER_MAP.get(category, [])
    if not fallback:
        for k, v in SCOUT_TICKER_MAP.items():
            if k.upper() == category_clean:
                fallback = v
                break
    return [t.strip().upper() for t in fallback if is_valid_ticker(t)]

ACTIVE_SCOUT_TICKERS = set()
PROCESSED_SCOUT_CATEGORIES = []
SCOUTED_TICKER_TO_CATEGORY_MAP = {}
FORCE_REFRESH = False

def _load_ssot_tickers():
    global ACTIVE_SCOUT_TICKERS, PROCESSED_SCOUT_CATEGORIES, SCOUTED_TICKER_TO_CATEGORY_MAP
    try:
        service = get_persistence_service()
        combined_base = service.get_ssot_tickers()
        portfolio_and_watched = [t.upper() for t in combined_base if is_valid_ticker(t)]

        # Scout categories dynamic tickers
        scout_dict = service.get_scout_categories()
        scouts = list(scout_dict.keys()) if isinstance(scout_dict, dict) else list(scout_dict)

        scout_tickers = []
        SCOUTED_TICKER_TO_CATEGORY_MAP.clear()
        for cat in scouts:
            cat_tickers = _get_dynamic_scout_tickers(cat)[:5]
            for t in cat_tickers:
                SCOUTED_TICKER_TO_CATEGORY_MAP[t.upper()] = cat
            scout_tickers.extend(cat_tickers)

        ACTIVE_SCOUT_TICKERS = {t.upper() for t in scout_tickers if is_valid_ticker(t)}
        PROCESSED_SCOUT_CATEGORIES = list(scouts)

        combined = []
        for t in portfolio_and_watched + list(ACTIVE_SCOUT_TICKERS):
            t_upper = t.upper()
            if t_upper not in combined and is_valid_ticker(t_upper):
                combined.append(t_upper)
        return combined
    except Exception as e:
        print(f"[SSOT] Load failed: {e}")
    return config.get("DEFAULT_TICKERS", ["AAPL", "MSFT", "GOOGL", "NVDA"])

def _load_macro_tickers():
    try:
        if os.path.exists('context/user_config.json'):
            with open('context/user_config.json', 'r') as f:
                user_cfg = json.load(f)
                if 'macro' in user_cfg and user_cfg['macro']:
                    return [t.upper() for t in user_cfg['macro']]
    except Exception as e:
        print(f"[Config] Load failed: {e}")
    return config.get("DEFAULT_MACRO_TICKERS", [
        "^VIX", "^VVIX", "VIXY", "IEF", "UUP", "SPY", "GDX"
    ])

# Clear active scout categories on startup to defer yahoo finance queries
try:
    service = get_persistence_service()
    scouts = service.get_scout_categories()
    if scouts:
        service.save_scout_categories([])
        print("[SSOT] Active scout categories cleared on startup to defer loading.")
except Exception as e:
    print(f"[SSOT] Clear scout categories failed via persistence service: {e}")

TICKERS = _load_ssot_tickers()
MACRO_TICKERS = _load_macro_tickers()
ALL_TICKERS = list(dict.fromkeys(TICKERS + MACRO_TICKERS))
if not ALL_TICKERS:
    TICKERS = ["AAPL", "MSFT", "GOOGL", "NVDA"]
    MACRO_TICKERS = ["^VIX", "^VVIX", "VIXY", "IEF", "UUP", "SPY", "GDX"]
    ALL_TICKERS = list(dict.fromkeys(TICKERS + MACRO_TICKERS))
INVERSE_MACRO = config.get("INVERSE_MACRO", ['^VIX', '^VVIX', 'VIXY', 'UUP', 'IEF'])

MACRO_LABELS = config.get("MACRO_LABELS", {
    '^VIX': 'VOLATILITY',
    '^VVIX': '^VVIX',
    'VIXY': 'SHORT-TERM VIX',
    'IEF': 'TREASURY BOND',
    'UUP': 'US DOLLAR',
    'SPY': 'S&P 500',
    'QQQ': 'NASDAQ 100',
    'GDX': 'GOLD MINERS'
})

REFRESH_RATE_SECONDS = config.get("REFRESH_RATE_SECONDS", 30)
HISTORY_REFRESH_CYCLES = config.get("HISTORY_REFRESH_CYCLES", 10)

class ImmutableDict(collections.abc.Mapping):
    """
    Read-only mapping proxy ensuring deep immutability for published snapshots.
    Wraps an immutable MappingProxyType over a detached copy of the input mapping.
    Prevents direct mutation through ordinary methods or direct backing-container access.
    """
    __slots__ = ("_data",)

    def __init__(self, data=None, **kwargs):
        detached = dict(data or {}, **kwargs)
        super().__setattr__("_data", MappingProxyType(detached))

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def __repr__(self):
        return f"ImmutableDict({dict(self._data)!r})"

    def __eq__(self, other):
        if isinstance(other, collections.abc.Mapping):
            return dict(self._data) == dict(other)
        return False

    def copy(self):
        return dict(self._data)

    def __or__(self, other):
        if isinstance(other, collections.abc.Mapping):
            res = dict(self._data)
            res.update(other)
            return res
        return NotImplemented

    def __ror__(self, other):
        if isinstance(other, collections.abc.Mapping):
            res = dict(other)
            res.update(self._data)
            return res
        return NotImplemented

    def __setattr__(self, key, value):
        raise TypeError("Immutable snapshot dictionary cannot be modified")

    def __delattr__(self, key):
        raise TypeError("Immutable snapshot dictionary cannot be modified")

    def _readonly(self, *args, **kwargs):
        raise TypeError("Immutable snapshot dictionary cannot be modified")

    __setitem__ = _readonly
    __delitem__ = _readonly
    clear = _readonly
    update = _readonly
    pop = _readonly
    popitem = _readonly
    setdefault = _readonly
    __ior__ = _readonly


class ImmutableList(collections.abc.Sequence):
    """
    Read-only sequence proxy ensuring deep immutability for published snapshots.
    Wraps an immutable tuple over a detached copy of the input sequence.
    Prevents direct mutation through ordinary methods or direct backing-container access.
    """
    __slots__ = ("_items",)

    def __init__(self, items=()):
        super().__setattr__("_items", tuple(items))

    def __getitem__(self, idx):
        return self._items[idx]

    def __len__(self):
        return len(self._items)

    def __repr__(self):
        return f"ImmutableList({list(self._items)!r})"

    def __eq__(self, other):
        if isinstance(other, collections.abc.Sequence):
            return list(self._items) == list(other)
        return False

    def copy(self):
        return list(self._items)

    def __add__(self, other):
        if isinstance(other, (list, tuple, collections.abc.Sequence)):
            return list(self._items) + list(other)
        return NotImplemented

    def __radd__(self, other):
        if isinstance(other, (list, tuple, collections.abc.Sequence)):
            return list(other) + list(self._items)
        return NotImplemented

    def __setattr__(self, key, value):
        raise TypeError("Immutable snapshot list cannot be modified")

    def __delattr__(self, key):
        raise TypeError("Immutable snapshot list cannot be modified")

    def _readonly(self, *args, **kwargs):
        raise TypeError("Immutable snapshot list cannot be modified")

    __setitem__ = _readonly
    __delitem__ = _readonly
    append = _readonly
    extend = _readonly
    insert = _readonly
    remove = _readonly
    pop = _readonly
    clear = _readonly
    sort = _readonly
    reverse = _readonly
    __iadd__ = _readonly
    __imul__ = _readonly


def freeze_snapshot(obj: Any) -> Any:
    """
    Recursively converts mappings to ImmutableDict and sequences to ImmutableList,
    creating a detached, deeply immutable structure that does not share mutable
    nested references with the caller or background workers.
    """
    if isinstance(obj, collections.abc.Mapping):
        return ImmutableDict({k: freeze_snapshot(v) for k, v in obj.items()})
    elif isinstance(obj, (list, tuple, collections.abc.Sequence)) and not isinstance(obj, (str, bytes)):
        return ImmutableList(tuple(freeze_snapshot(x) for x in obj))
    elif isinstance(obj, (set, frozenset)):
        return frozenset(freeze_snapshot(x) for x in obj)
    return obj


GLOBAL_STATE = freeze_snapshot({
    "status": "INITIALIZING - LOADING GEX PROFILES (MAY TAKE 1-2 MIN)...",
    "tickers": [],
    "boot_phase": "STARTING_UP",
    "boot_progress": 0,
    "boot_total": 100,
    "boot_ticker": "SYSTEM"
})

_SNAPSHOT_LOCK = threading.Lock()

def get_global_state_snapshot() -> ImmutableDict:
    """
    Returns the current immutable/read-only snapshot reference of GLOBAL_STATE.
    Readers capture this reference once and use it consistently throughout their operation.
    """
    global GLOBAL_STATE
    return GLOBAL_STATE

def publish_global_state_snapshot(new_snapshot: Any) -> ImmutableDict:
    """
    Publishes a replacement GLOBAL_STATE snapshot via an atomic reference swap.
    The caller must construct replacement snapshots separately.
    Published snapshots must not subsequently be mutated in place.
    """
    global GLOBAL_STATE
    if not isinstance(new_snapshot, collections.abc.Mapping):
        raise TypeError(f"Snapshot must be a mapping, got {type(new_snapshot).__name__}")
    frozen = freeze_snapshot(new_snapshot)
    with _SNAPSHOT_LOCK:
        GLOBAL_STATE = frozen
    return frozen

def update_global_state_snapshot(updater_fn: Callable[[Any], Any]) -> ImmutableDict:
    """
    Atomically updates the snapshot by applying updater_fn(current_snapshot) -> new_snapshot
    and publishing through atomic reference swap.
    Preserves immutability of previously published snapshots.
    """
    global GLOBAL_STATE
    with _SNAPSHOT_LOCK:
        current = GLOBAL_STATE
        new_snapshot = updater_fn(current)
        if not isinstance(new_snapshot, collections.abc.Mapping):
            raise TypeError(f"Updater must return a mapping, got {type(new_snapshot).__name__}")
        frozen = freeze_snapshot(new_snapshot)
        GLOBAL_STATE = frozen
        return frozen
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def save_config():
    global config, SCOUT_LIMIT, SCOUT_MAX_RSI
    config["SCOUT_LIMIT"] = SCOUT_LIMIT
    config["SCOUT_MAX_RSI"] = SCOUT_MAX_RSI
    with open('context/config.json', 'w') as f:
        json.dump(config, f, indent=4)

@app.get("/api/scout_config")
def get_scout_config():
    return JSONResponse({"scout_limit": SCOUT_LIMIT, "scout_max_rsi": SCOUT_MAX_RSI})

@app.post("/api/scout_config")
async def update_scout_config(req: Request):
    global SCOUT_LIMIT, SCOUT_MAX_RSI
    data = await req.json()
    new_limit = data.get("scout_limit")
    new_max_rsi = data.get("scout_max_rsi")
    if new_limit is not None:
        SCOUT_LIMIT = max(1, min(5, int(new_limit)))
    if new_max_rsi is not None:
        SCOUT_MAX_RSI = max(30, min(100, int(new_max_rsi)))
    save_config()
    return JSONResponse({"status": "success", "scout_limit": SCOUT_LIMIT, "scout_max_rsi": SCOUT_MAX_RSI})

def _execute_news_grounding_raw(symbol: str, cancel_check: Optional[Callable[[], bool]] = None) -> dict:
    """
    Internal raw execution of news grounding via Antigravity Agent.
    Executed by background worker threads; does not touch coordinator state directly.
    """
    symbol_clean = symbol.strip().upper()
    template = load_news_scan_prompt()
    if "{symbol}" in template:
        prompt = template.format(symbol=symbol_clean)
    else:
        prompt = (
            f"{template}\n\n"
            f"TARGET SYMBOL FOR SEARCH: {symbol_clean}\n"
            f"Return ONLY a clean JSON object with keys: \"headline\", \"summary\", \"catalyst_type\", \"sentiment_score\"."
        )

    text = ""
    try:
        from agent_framework import AntigravityAgentRunner
        runner = AntigravityAgentRunner()
        response = runner.run_prompt(prompt, mode="FAST", cancel_check=cancel_check)
        if response and response.text:
            text = response.text.strip()
    except Exception as e:
        print(f"[Grounding Error] {symbol_clean}: {e}")
        raise

    if not text:
        raise ValueError(f"Empty text response from news grounding for {symbol_clean}")

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        parsed = json.loads(match.group(0))
        # Distinguish processed/cached timestamp from external source observation timestamp
        processed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        parsed["processed_at"] = processed_at
        parsed["timestamp"] = processed_at  # Preserved for API/dashboard backwards compatibility
        parsed["symbol"] = symbol_clean
        return parsed
    raise ValueError(f"Could not parse valid JSON from grounding response for {symbol_clean}")

def enqueue_news_grounding(symbol: str, obs_time: Optional[str] = None) -> Tuple[bool, Optional[Any]]:
    """Enqueues news grounding for symbol to background worker pool without blocking."""
    if not symbol:
        return False, None
    symbol_clean = symbol.strip().upper()
    return BACKGROUND_COORDINATOR.enqueue(
        work_type=WorkType.GROUNDING.value,
        target=symbol_clean,
        obs_time=obs_time,
        fn=_execute_news_grounding_raw,
        args=(symbol_clean,)
    )

def query_gemini_news_grounding(api_key: str, symbol: str, timeout: float = 5.0) -> dict:
    """
    Public / caller-facing news grounding query.
    Returns cached qualitative grounding if present. Otherwise enqueues to bounded
    background worker pool with a bounded wait timeout without blocking the main coordinator.
    """
    if not symbol:
        return {}
    symbol_clean = symbol.strip().upper()

    with DYNAMIC_SCOUT_CACHE_LOCK:
        if symbol_clean in cache.qualitative_grounding:
            return cache.qualitative_grounding[symbol_clean]

    accepted, item = enqueue_news_grounding(symbol_clean, obs_time=None)
    if accepted and item:
        if item.event.wait(timeout=timeout):
            if item.status == WorkStatus.COMPLETED and isinstance(item.result, dict):
                apply_background_intelligence_results()
                with DYNAMIC_SCOUT_CACHE_LOCK:
                    return cache.qualitative_grounding.get(symbol_clean, item.result)

    with DYNAMIC_SCOUT_CACHE_LOCK:
        return cache.qualitative_grounding.get(symbol_clean, {})

@app.get("/api/grounding")
async def get_grounding(symbol: str = "UMAC"):
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, query_gemini_news_grounding, "", symbol.upper())
    return JSONResponse({"status": "success", "symbol": symbol.upper(), "grounding": result})

@app.get("/api/get_basket")
def get_basket():
    try:
        service = get_persistence_service()
        basket = service.get_portfolio_basket()
        raw_basket = basket.get("portfolio", [])
        return JSONResponse([{"ticker": i["ticker"], "shares": i.get("shares", 0), "wac": i.get("wac", 0)} for i in raw_basket])
    except Exception as e:
        print(f"SSoT Read Warning: {e}")
    return JSONResponse([])

@app.post("/api/save_basket")
async def save_basket(req: Request):
    global TICKERS, ALL_TICKERS
    try:
        basket = await req.json()
        authorize_financial_mutation("update_ssot", "context/ssot.json", payload=basket)
        new_snapshot = []
        for item in basket:
            if item.get("shares", 0) > 0:
                new_snapshot.append({
                    "ticker": item["ticker"],
                    "shares": item["shares"],
                    "wac": item["wac"]
                })

        service = get_persistence_service()
        existing_basket = service.get_portfolio_basket()
        cash_eur = existing_basket.get("unallocated_cash_eur", 0.0)
        cash_usd = existing_basket.get("unallocated_cash_usd", 0.0)
        service.save_portfolio_basket(new_snapshot, unallocated_cash_eur=cash_eur, unallocated_cash_usd=cash_usd)

        # Reload TICKERS
        TICKERS = _load_ssot_tickers()
        ALL_TICKERS = TICKERS + MACRO_TICKERS
        return JSONResponse({"status": "success"})
    except PermissionError:
        raise
    except Exception as e:
        print(f"Basket Sync Failed: {e}")
    return JSONResponse({"status": "error"})

@app.get("/api/get_watch_list")
def get_watch_list():
    try:
        service = get_persistence_service()
        watchlist = service.get_watchlist()
        return JSONResponse(watchlist)
    except Exception:
        pass
    return JSONResponse([])

@app.post("/api/save_watch_list")
async def save_watch_list(req: Request):
    global TICKERS, ALL_TICKERS
    try:
        payload = await req.json()
        authorize_financial_mutation("update_ssot", "context/ssot.json", payload=payload)

        # Robustly handle both list payloads and dict payloads
        if isinstance(payload, dict):
            new_list = payload.get("watchlist", [])
        elif isinstance(payload, list):
            new_list = payload
        else:
            new_list = []

        new_list = [w.strip().upper() for w in new_list if isinstance(w, str) and w.strip()]

        service = get_persistence_service()
        service.save_watchlist(new_list)

        # Reload TICKERS
        TICKERS = _load_ssot_tickers()
        ALL_TICKERS = TICKERS + MACRO_TICKERS
        return JSONResponse({"status": "success"})
    except PermissionError:
        raise
    except Exception as e:
        print(f"Watch List Sync Failed: {e}")
    return JSONResponse({"status": "error"})

def sanitize_json_payload(obj):
    if isinstance(obj, collections.abc.Mapping):
        return {k: sanitize_json_payload(v) for k, v in obj.items()}
    elif isinstance(obj, collections.abc.Sequence) and not isinstance(obj, (str, bytes)):
        return [sanitize_json_payload(x) for x in obj]
    elif isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    elif isinstance(obj, (np.floating, np.float32, np.float64)):
        val = float(obj)
        if math.isnan(val) or math.isinf(val):
            return None
        return val
    elif isinstance(obj, (np.integer, np.int32, np.int64)):
        return int(obj)
    return obj

@app.get("/api/data")
def get_data():
    return JSONResponse(sanitize_json_payload(get_global_state_snapshot()))

@app.get("/api/tickers")
def get_tickers():
    return JSONResponse({"tickers": TICKERS, "macro": MACRO_TICKERS, "macro_labels": MACRO_LABELS})

# In-memory buffer for screenshots: {symbol: bytes}
CHART_SCREENSHOTS_BUFFER = {}
CHARTS_DIR = os.path.join("context", "charts")
os.makedirs(CHARTS_DIR, exist_ok=True)

@app.get("/api/intraday/{symbol}")
async def get_intraday_data(symbol: str):
    """Return 1-minute OHLCV bars for a given symbol for use in the chart modal.

    Returns:
        bars:           Today's OHLCV bars for candlestick rendering.
        warmup_closes:  Close prices from prior sessions (oldest first) used to
                        seed the EMA 200 calculation on the frontend, so the EMA
                        is valid from the first bar of the current session.
    """
    try:
        sym = symbol.strip().upper()
        ticker_obj = yf.Ticker(sym)
        # Fetch 5 days of 1m data with extended hours (pre-market and post-market)
        df = ticker_obj.history(interval="1m", period="5d", prepost=True, auto_adjust=True)
        if df is None or df.empty:
            return JSONResponse({"bars": [], "warmup_closes": [], "symbol": sym})

        # Determine today's date in the exchange local timezone
        last_ts = df.index[-1]
        today_date = last_ts.date()

        bars = []
        warmup_closes = []

        for ts, row in df.iterrows():
            try:
                t_unix = int(ts.timestamp())
            except Exception:
                continue

            bar_date = ts.date()
            close_val = round(float(row["Close"]), 4)

            if bar_date == today_date:
                # Today's bars: full OHLCV for candlestick rendering
                bars.append({
                    "time":   t_unix,
                    "open":   round(float(row["Open"]),  4),
                    "high":   round(float(row["High"]),  4),
                    "low":    round(float(row["Low"]),   4),
                    "close":  close_val,
                    "volume": int(row["Volume"]) if not pd.isna(row["Volume"]) else 0
                })
            else:
                # Prior-session bars: only the close price for EMA warmup
                warmup_closes.append(close_val)

        return JSONResponse({
            "bars":           bars,
            "warmup_closes":  warmup_closes,
            "symbol":         sym
        })
    except Exception as e:
        return JSONResponse({"bars": [], "warmup_closes": [], "symbol": symbol, "error": str(e)})

@app.post("/api/save_chart_screenshots")
async def save_chart_screenshots(req: Request):
    """Receive base64 PNG screenshots from the frontend chart canvas, write to context/charts/
    and store in CHART_SCREENSHOTS_BUFFER for Gemini multimodal prompt ingestion.
    """
    try:
        data = await req.json()
        saved = []
        
        # Support both {"screenshots": {"TICKER": "<base64>"}} and {"symbol": "TICKER", "screenshot": "<base64>"}
        screenshots_map = {}
        if "screenshots" in data and isinstance(data["screenshots"], dict):
            screenshots_map = data["screenshots"]
        elif "symbol" in data and "screenshot" in data:
            screenshots_map[data["symbol"]] = data["screenshot"]
            
        os.makedirs(CHARTS_DIR, exist_ok=True)
        
        if data.get("replace_all", False):
            import glob
            for old_file in glob.glob(os.path.join(CHARTS_DIR, "chart_*_1m.png")):
                try:
                    os.remove(old_file)
                except Exception:
                    pass
            CHART_SCREENSHOTS_BUFFER.clear()
        
        for symbol, b64_str in screenshots_map.items():
            if not symbol or not b64_str:
                continue
            sym = symbol.strip().upper()
            if "," in b64_str:
                b64_str = b64_str.split(",", 1)[1]
            try:
                img_bytes = base64.b64decode(b64_str)
                CHART_SCREENSHOTS_BUFFER[sym] = img_bytes
                file_path = os.path.join(CHARTS_DIR, f"chart_{sym}_1m.png")
                with open(file_path, "wb") as f:
                    f.write(img_bytes)
                saved.append(sym)
            except Exception as dec_err:
                print(f"[Warning] Failed to decode/save chart screenshot for {sym}: {dec_err}")
                
        return JSONResponse({"status": "success", "saved": saved})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)

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
    clip_data = ""
    try:
        authorize_financial_mutation(
            operation_name="update_ssot",
            target_resource="context/ssot.json",
        )
        if isinstance(req, str):
            clip_data = req
        elif isinstance(req, dict):
            clip_data = req.get("payload", json.dumps(req))
        else:
            body = await req.json()
            clip_data = body.get("payload", "")
        
        service = get_persistence_service()
        res = service.update_ssot_payload(clip_data)

        # Check for rule mutations requiring owner approval
        try:
            parsed = {}
            if isinstance(clip_data, str):
                clean_clip = clip_data.strip()
                if "```" in clean_clip:
                    m = re.search(r"```(?:json)?\s*(.*?)\s*```", clean_clip, re.IGNORECASE | re.DOTALL)
                    if m:
                        clean_clip = m.group(1).strip()
                if clean_clip.startswith("{") or clean_clip.startswith("["):
                    parsed = json.loads(clean_clip)
            elif isinstance(clip_data, dict):
                parsed = clip_data

            extracted_mutations = parsed.get("rule_mutations") if isinstance(parsed, dict) else None
            if extracted_mutations and isinstance(extracted_mutations, list):
                rules_file = os.path.join('gem_trading_rules', 'rules.json')
                authorize_financial_mutation(
                    operation_name="update_rules",
                    target_resource=rules_file,
                    payload=extracted_mutations,
                    requires_owner_approval=True,
                )
                if os.path.exists(rules_file):
                    def _apply_rule_mutations(current_rules):
                        if not isinstance(current_rules, dict):
                            return current_rules
                        applied_patches = 0
                        for mutation in extracted_mutations:
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
                            print(f"⚠️ RULES MUTATED LOCALLY ({applied_patches} patches applied).")
                        return current_rules

                    atomic_update_json(rules_file, _apply_rule_mutations, default_factory=dict, indent=4)
        except PermissionError:
            raise
        except Exception:
            pass

        return JSONResponse({"status": "success", "message": "Payload ingested successfully"})
    except PermissionError:
        raise
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
        self.regular_close: dict[str, float] = {}
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
        # Cache structures per v10.62-Scout-Limit-and-RSI-Filter specifications
        self.last_chart_fetch: dict[str, float] = {}
        self.chart_cache: dict[str, tuple] = {}
        self.last_premarket_vol_fetch: dict[str, float] = {}
        self.premarket_vol_cache: dict[str, int] = {}
        self.last_gex_fetch: dict[str, float] = {}
        self.gex_cache: dict[str, dict] = {}
        self.qualitative_grounding: dict[str, dict] = {} # Google Search Grounding Cache
        self.last_history_fetch_date: dict[str, str] = {}
        # Daily session peak, floor, and open tracking for daily trading Fib optimization
        self.day_open: dict[str, float] = {}
        self.day_high: dict[str, float] = {}
        self.day_low: dict[str, float] = {}
        # Market Data Provenance & Freshness metadata (GEM Stage 1 & Stage 2.2.2)
        self.observation_times: dict[str, str | None] = {}
        self.retrieval_times: dict[str, str | None] = {}
        self.price_sources: dict[str, str | None] = {}
        self.price_validity: dict[str, str] = {}
        self.freshness: dict[str, str] = {}
        self.chart_obs_time: dict[str, datetime | None] = {}
        self.previous_session_volume: dict[str, int] = {}
        self.previous_session_volume_dates: dict[str, str] = {}
        self.previous_session_volume_sources: dict[str, str] = {}
        self.regular_close_dates: dict[str, str] = {}
        self.regular_close_sources: dict[str, str] = {}
        self.metric_provenance: dict[str, dict[str, dict[str, Any]]] = {}

    def clear(self):
        self.__init__()

    def garbage_collect(self, active_tickers):
        if not active_tickers:
            return
        active_set = {sym.upper() for sym in active_tickers}
        for dict_attr in ('history', 'technicals', 'last_history_fetch_date'):
            d = getattr(self, dict_attr, {})
            keys_to_remove = [k for k in list(d.keys()) if k.upper() not in active_set]
            for k in keys_to_remove:
                d.pop(k, None)
        if keys_to_remove:
            print(f"[Cache] Garbage collected {len(keys_to_remove)} redundant tickers: {keys_to_remove}")

    def save_to_disk(self, active_tickers=None):
        try:
            if active_tickers:
                self.garbage_collect(active_tickers)
            path = "cache/daily_history_cache.json"
            serialized_history = {}
            for sym, df in self.history.items():
                if df is not None and not df.empty:
                    serialized_history[sym] = {
                        "index": [idx.isoformat() if hasattr(idx, 'isoformat') else str(idx) for idx in df.index],
                        "columns": list(df.columns),
                        "data": df.values.tolist()
                    }
            payload = {
                "history": serialized_history,
                "technicals": self.technicals,
                "last_history_fetch_date": self.last_history_fetch_date
            }
            os.makedirs("cache", exist_ok=True)
            atomic_write_json(path, payload, indent=2)
            print(f"[Cache] Successfully saved daily history cache for {len(serialized_history)} tickers to disk.")
        except Exception as e:
            print(f"[Cache Warning] Failed to save daily history cache: {e}")

    def load_from_disk(self):
        try:
            path = "cache/daily_history_cache.json"
            if not os.path.exists(path):
                return
            payload = atomic_read_json(path, default={})
            serialized_history = payload.get("history", {})
            for sym, serialized in serialized_history.items():
                try:
                    df = pd.DataFrame(
                        data=serialized["data"],
                        index=pd.to_datetime(serialized["index"], utc=True),
                        columns=serialized["columns"]
                    )
                    self.history[sym] = df
                except Exception as df_err:
                    print(f"[Cache Warning] Failed to parse DataFrame for {sym}: {df_err}")
            self.technicals.update(payload.get("technicals", {}))
            self.last_history_fetch_date.update(payload.get("last_history_fetch_date", {}))
            print(f"[Cache] Successfully loaded daily history cache for {len(self.history)} tickers from disk.")
        except Exception as e:
            print(f"[Cache Warning] Failed to load daily history cache: {e}")

cache = MarketDataCache()
cache.load_from_disk()
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

class USTradingCalendar(AbstractHolidayCalendar):
    """Canonical US Equity Market (NYSE/NASDAQ) Holiday Schedule using pandas components."""
    rules = [
        Holiday("New Years Day", month=1, day=1, observance=nearest_workday),
        USMartinLutherKingJr,
        USPresidentsDay,
        GoodFriday,
        USMemorialDay,
        Holiday("Juneteenth", month=6, day=19, start_date="2021-06-18", observance=nearest_workday),
        Holiday("Independence Day", month=7, day=4, observance=nearest_workday),
        USLaborDay,
        USThanksgivingDay,
        Holiday("Christmas", month=12, day=25, observance=nearest_workday),
    ]

_HOLIDAY_CACHE: dict[int, set] = {}

def is_market_holiday(d) -> bool:
    """Checks if date is an official US equity market exchange holiday."""
    try:
        ts = pd.Timestamp(d).normalize()
        year = ts.year
        if year not in _HOLIDAY_CACHE:
            cal = USTradingCalendar()
            _HOLIDAY_CACHE[year] = set(cal.holidays(start=f"{year}-01-01", end=f"{year}-12-31"))
        return ts in _HOLIDAY_CACHE[year]
    except Exception:
        return False

def get_market_status(ny_dt=None):
    ny_tz = ZoneInfo("America/New_York")
    if ny_dt is None:
        ny_now = datetime.now(ny_tz)
    elif ny_dt.tzinfo is None:
        ny_now = ny_dt.replace(tzinfo=ny_tz)
    else:
        ny_now = ny_dt.astimezone(ny_tz)
    
    # 1. Weekends: Saturday (5), Sunday (6)
    if ny_now.weekday() >= 5:
        return "CLOSED"
        
    # 2. Exchange Holidays:
    if is_market_holiday(ny_now.date()):
        return "CLOSED"
        
    # 3. Market Session Hours (ET)
    t = ny_now.time()
    if time(4, 0) <= t < time(9, 30):
        return "PRE-MARKET"
    if time(9, 30) <= t < time(16, 0):
        return "OPEN"
    if time(16, 0) <= t < time(20, 0):
        return "AFTER-HOURS"
    return "CLOSED"

def classify_market_numeric(val):
    """
    Classifies a market numeric value into:
    ("VALID", float_value)
    ("MISSING", None)
    ("INVALID", None)
    ("NON_FINITE", None)
    """
    if val is None:
        return ("MISSING", None)
    if isinstance(val, bool):
        return ("INVALID", None)
    if isinstance(val, (pd.Series, pd.DataFrame)):
        if val.empty:
            return ("MISSING", None)
        try:
            val = val.iloc[-1]
        except Exception:
            return ("INVALID", None)
        if val is None or isinstance(val, bool):
            return ("MISSING" if val is None else "INVALID", None)
    try:
        v = float(val)
    except (ValueError, TypeError, OverflowError):
        return ("INVALID", None)
    if math.isnan(v) or math.isinf(v):
        return ("NON_FINITE", None)
    return ("VALID", v)

def to_float(val, default=None):
    """
    Safely converts input to a finite float.
    Distinguishes valid numerics (including 0.0) from missing, invalid, or non-finite values.
    Returns:
        float if val is a valid finite numeric.
        default (default: None) if val is None, invalid, non-finite (NaN, +/-inf), or on failure.
    """
    status, num = classify_market_numeric(val)
    return num if status == "VALID" else default

def is_price_actionable(symbol: str) -> bool:
    """
    Evaluates whether a symbol's price observation is currently actionable for signal generation.
    Fails closed:
    - Must have a valid positive price.
    - price_validity must be 'VALID'.
    - freshness must be 'CURRENT' or 'DELAYED'.
    - Stale, historical, unavailable, or invalid observations cannot produce actionable signals.
    """
    p = cache.prices.get(symbol)
    if p is None or p <= 0:
        return False
        
    validity = cache.price_validity.get(symbol)
    freshness = cache.freshness.get(symbol)
    
    if validity != "VALID":
        return False
        
    if freshness not in ("CURRENT", "DELAYED"):
        return False
        
    return True

def determine_market_freshness(price, obs_time, current_ny=None, session_status=None, max_stale_seconds=1200, source=None):
    """
    Evaluates market data freshness and validity for a given price observation.
    
    Parameters:
        price: The numeric price observation or None.
        obs_time: datetime of observation (in NY timezone or UTC) or None.
        current_ny: datetime representing current time in NY timezone (defaults to now).
        session_status: Market session ("OPEN", "PRE-MARKET", "AFTER-HOURS", "CLOSED"). Defaults to get_market_status(current_ny).
        max_stale_seconds: Maximum age in seconds before an observation in an active session is marked STALE (default: 1200s / 20 mins).
        source: Provider source identity (e.g. BATCH_QUOTE, FAST_INFO, INTRADAY_CHART).
        
    Returns:
        (price_validity, freshness)
        price_validity in ("VALID", "STALE", "INVALID", "UNAVAILABLE")
        freshness in ("CURRENT", "DELAYED", "HISTORICAL", "STALE", "UNAVAILABLE")
    """
    validity_type, num_price = classify_market_numeric(price)
    if validity_type != "VALID" or num_price is None or num_price <= 0:
        return ("UNAVAILABLE" if validity_type == "MISSING" else "INVALID", "UNAVAILABLE")

    ny_tz = ZoneInfo("America/New_York")
    if current_ny is None:
        current_ny = datetime.now(ny_tz)
    elif current_ny.tzinfo is None:
        current_ny = current_ny.replace(tzinfo=ny_tz)
    else:
        current_ny = current_ny.astimezone(ny_tz)

    if session_status is None:
        session_status = get_market_status(current_ny)

    if obs_time is None:
        # Source did not provide observation timestamp: do not invent one
        return ("VALID", "UNAVAILABLE")

    if obs_time.tzinfo is None:
        obs_dt = obs_time.replace(tzinfo=ny_tz)
    else:
        obs_dt = obs_time.astimezone(ny_tz)

    # 1. Reject materially future-dated observations BEFORE any session-specific logic
    age_seconds = (current_ny - obs_dt).total_seconds()
    if age_seconds < -60: # 60 seconds allowable clock drift tolerance
        return ("INVALID", "UNAVAILABLE")

    # 2. Market Closure (Weekends, Exchange Holidays, Overnight 20:00 - 04:00):
    # A previous-session close observation is valid historical data, NOT an error,
    # and MUST NOT be marked as current live price.
    if session_status == "CLOSED":
        return ("VALID", "HISTORICAL")

    # 3. Active Market Sessions (OPEN, PRE-MARKET, AFTER-HOURS):
    # If observation is from a previous calendar day:
    if obs_dt.date() < current_ny.date():
        # Previous-session close cannot masquerade as current live price during an active session
        return ("STALE", "STALE")

    if session_status == "OPEN":
        # During regular hours (09:30 - 16:00)
        if obs_dt.time() < time(9, 30):
            return ("STALE", "STALE")
        if age_seconds > max_stale_seconds:
            return ("STALE", "STALE")
        elif age_seconds > 900: # 15 minutes
            return ("VALID", "DELAYED")
        else:
            return ("VALID", "CURRENT")

    elif session_status == "PRE-MARKET":
        # During pre-market hours (04:00 - 09:30)
        if obs_dt.time() < time(4, 0):
            return ("VALID", "HISTORICAL")
        if age_seconds > max_stale_seconds:
            return ("STALE", "STALE")
        return ("VALID", "CURRENT")

    elif session_status == "AFTER-HOURS":
        # During after-hours (16:00 - 20:00)
        if obs_dt.time() < time(16, 0):
            return ("VALID", "HISTORICAL")
        if age_seconds > max_stale_seconds:
            return ("STALE", "STALE")
        return ("VALID", "CURRENT")

    return ("VALID", "CURRENT")

def validate_historical_dataframe(hist: pd.DataFrame, existing_df: pd.DataFrame | None = None) -> tuple[bool, str]:
    """
    Validates an incoming historical price DataFrame against structural, content,
    OHLCV relationships, index validity, and material completeness requirements
    before updating cache.
    
    Returns:
        (is_valid: bool, reason: str)
    """
    if hist is None or not isinstance(hist, pd.DataFrame):
        return False, "Not a pandas DataFrame"
    if hist.empty:
        return False, "Empty DataFrame"

    total_rows = len(hist)
    if total_rows == 0:
        return False, "Zero rows in DataFrame"

    # 1. Index validation: Must be DatetimeIndex, non-null, unique, and monotonically increasing
    if not isinstance(hist.index, pd.DatetimeIndex):
        try:
            if not pd.api.types.is_datetime64_any_dtype(hist.index):
                return False, "Index must be a DatetimeIndex"
        except Exception:
            return False, "Index is not a valid DatetimeIndex"

    if hist.index.isna().any():
        return False, "Index contains NaT or invalid timestamps"
    if not hist.index.is_unique:
        return False, "Index contains duplicate timestamps"
    if not hist.index.is_monotonic_increasing:
        return False, "Index timestamps must be monotonically increasing (sorted)"

    # 2. Structural validation: Required OHLCV fields
    required_cols = ["Open", "High", "Low", "Close", "Volume"]
    extracted = {}
    for col_name in required_cols:
        col = None
        if col_name in hist.columns:
            col = hist[col_name]
        elif isinstance(hist.columns, pd.MultiIndex):
            for c in hist.columns:
                if c[0] == col_name:
                    col = hist[c]
                    break
        if col is None:
            return False, f"Missing required OHLCV column: {col_name}"
        if isinstance(col, pd.DataFrame):
            col = col.iloc[:, 0]
        extracted[col_name] = col

    # 3. Numeric conversion and validity checks
    open_s = pd.to_numeric(extracted["Open"], errors="coerce")
    high_s = pd.to_numeric(extracted["High"], errors="coerce")
    low_s = pd.to_numeric(extracted["Low"], errors="coerce")
    close_s = pd.to_numeric(extracted["Close"], errors="coerce")
    vol_s = pd.to_numeric(extracted["Volume"], errors="coerce")

    # Check for infinity in any OHLCV column
    for col_name, s in [("Open", open_s), ("High", high_s), ("Low", low_s), ("Close", close_s), ("Volume", vol_s)]:
        arr = np.asarray(s, dtype=float)
        if np.isinf(arr).any():
            return False, f"Non-finite infinity detected in {col_name}"

    # Check for negative volume
    neg_vol_mask = vol_s < 0
    if neg_vol_mask.any():
        return False, "Negative volume detected in historical data"

    # Row is valid if all O, H, L, C are finite and > 0, and V is finite and >= 0
    valid_mask = (
        open_s.notna() & (open_s > 0) &
        high_s.notna() & (high_s > 0) &
        low_s.notna() & (low_s > 0) &
        close_s.notna() & (close_s > 0) &
        vol_s.notna() & (vol_s >= 0)
    )

    # For valid rows, check sensible OHLC relationships (with 1e-6 epsilon for float precision)
    tolerance = 1e-6
    rel_invalid_mask = valid_mask & (
        (high_s < (low_s - tolerance)) |
        (high_s < (open_s - tolerance)) |
        (high_s < (close_s - tolerance)) |
        (low_s > (open_s + tolerance)) |
        (low_s > (high_s + tolerance)) |
        (low_s > (close_s + tolerance))
    )
    if rel_invalid_mask.any():
        idx_first = rel_invalid_mask.idxmax()
        return False, f"Invalid OHLC relationship at {idx_first}: High must be >= Open, Low, Close and Low must be <= Open, High, Close"

    valid_count = int(valid_mask.sum())
    # Require at least 5 valid OHLCV rows and at least 95% valid rows
    if valid_count < 5 or (valid_count / total_rows) < 0.95:
        return False, f"Excessive invalid or non-finite OHLCV rows ({valid_count}/{total_rows} valid)"

    # Latest row validation: Open, High, Low, Close must be finite and positive, Volume non-negative
    latest_c = to_float(close_s.iloc[-1])
    latest_o = to_float(open_s.iloc[-1])
    latest_h = to_float(high_s.iloc[-1])
    latest_l = to_float(low_s.iloc[-1])
    latest_v = to_float(vol_s.iloc[-1])

    if latest_c is None or latest_c <= 0:
        return False, "Latest close in history is non-finite or non-positive"
    if latest_o is None or latest_o <= 0:
        return False, "Latest open in history is non-finite or non-positive"
    if latest_h is None or latest_h <= 0:
        return False, "Latest high in history is non-finite or non-positive"
    if latest_l is None or latest_l <= 0:
        return False, "Latest low in history is non-finite or non-positive"
    if latest_v is None or latest_v < 0:
        return False, "Latest volume in history is non-finite or negative"

    # Material truncation check relative to existing valid cache
    if existing_df is not None and isinstance(existing_df, pd.DataFrame) and not existing_df.empty:
        existing_len = len(existing_df)
        # If existing has substantial history (>= 30 bars), incoming cannot be materially truncated (< 75% of existing)
        if existing_len >= 30 and total_rows < int(existing_len * 0.75):
            return False, f"Materially truncated response: {total_rows} rows vs {existing_len} existing rows"

    return True, "Valid"

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

@dataclass(frozen=True)
class PremarketVolumeObservation:
    """
    Immutable pre-market volume aggregate carrying both the latest contributing valid
    bar's UTC observation timestamp and the verified NY session date.
    """
    value: int
    obs_time: str
    effective_session_date: str
    source: str = "PREMARKET_VOLUME_CHART"

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, int) or self.value < 0:
            raise ValueError(f"PremarketVolumeObservation value must be a non-negative int, got {self.value!r}")
        valid_ts = validate_utc_iso8601_timestamp(self.obs_time)
        valid_date = validate_session_date(self.effective_session_date)
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("PremarketVolumeObservation source must be a non-empty string")
        object.__setattr__(self, "obs_time", valid_ts)
        object.__setattr__(self, "effective_session_date", valid_date)
        object.__setattr__(self, "source", self.source.strip())


def _acquire_premarket_volume_raw(symbol: str) -> Optional[PremarketVolumeObservation]:
    """
    Dedicated fetch for pre-market volume (04:00-09:30 ET today) via Yahoo chart API.
    Returns PremarketVolumeObservation carrying the timestamp of the latest contributing
    valid bar and the NY session date, or None when unavailable/empty/unprovenanced.
    """
    try:
        ny_tz = ZoneInfo("America/New_York")
        now = datetime.now(ny_tz)
        pre_start = now.replace(hour=4, minute=0, second=0, microsecond=0)
        pre_end = now.replace(hour=9, minute=30, second=0, microsecond=0)
        if now < pre_start:
            return None
        if now < pre_end:
            pre_end = now
        ts1 = int(pre_start.timestamp())
        ts2 = int(pre_end.timestamp())
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1={ts1}&period2={ts2}&interval=1m&includePrePost=true"
        r = safe_yf_get(url, timeout=3)
        data = r.json()
        result = data["chart"]["result"][0]
        timestamps = result.get("timestamp", [])
        volumes = result["indicators"]["quote"][0].get("volume", [])
        if not timestamps or not volumes:
            return None

        total = 0
        has_valid_bar = False
        last_valid_ts = None
        last_positive_ts = None
        today_ny = now.date()

        for ts, v in zip(timestamps, volumes):
            obs_iso = _epoch_to_utc_iso8601(ts)
            if obs_iso is None:
                continue
            dt_ny = datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone(ny_tz)
            if dt_ny.date() != today_ny:
                continue
            if dt_ny.time() < time(4, 0) or dt_ny.time() >= time(9, 30):
                continue
            v_num = to_float(v)
            if v_num is None or not math.isfinite(v_num) or v_num < 0:
                continue
            has_valid_bar = True
            last_valid_ts = ts
            v_int = int(v_num)
            total += v_int
            if v_int > 0:
                last_positive_ts = ts

        if not has_valid_bar:
            return None
        chosen_ts = last_positive_ts if last_positive_ts is not None else last_valid_ts
        chosen_iso = _epoch_to_utc_iso8601(chosen_ts)
        if chosen_iso is None:
            return None
        sess_date = (
            datetime.fromtimestamp(float(chosen_ts), tz=timezone.utc)
            .astimezone(ny_tz)
            .date()
            .isoformat()
        )
        return PremarketVolumeObservation(
            value=int(total),
            obs_time=chosen_iso,
            effective_session_date=sess_date,
            source="PREMARKET_VOLUME_CHART",
        )
    except Exception:
        return None


def get_premarket_volume_chart(symbol) -> Optional[PremarketVolumeObservation]:
    """
    Dedicated fetch for pre-market volume (04:00-09:30 ET today) via Yahoo chart API.
    Returns PremarketVolumeObservation (with latest contributing bar timestamp and session date)
    or None if no provenanced bars were observed.
    """
    return _acquire_premarket_volume_raw(symbol)

# -----------------------------
# FETCHERS
# -----------------------------
def _acquire_live_chart_observations_raw(symbol: str, status: str) -> dict:
    """
    Worker-safe fetch of 5-day 1m chart data without mutating shared cache state.
    Preserves separate bar-level timestamps for price and each volume aggregate,
    distinguishing an observed zero from empty/invalid responses.
    """
    empty_result = {
        "price": None,
        "session_volume": None,
        "premarket_volume": None,
        "after_hours_volume": None,
        "chart_obs_utc_iso": None,
        "legacy_tuple": (None, 0, 0, 0, None),
    }
    try:
        ny_tz = ZoneInfo("America/New_York")
        now = datetime.now(ny_tz)

        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1m&includePrePost=true"
        r = safe_yf_get(url, timeout=3)
        data = r.json()
        result = data['chart']['result'][0]
        timestamps = result.get('timestamp', [])
        quote = result['indicators']['quote'][0]
        closes = quote.get('close', [])
        volumes = quote.get('volume', [])

        price_obs: Optional[TimestampedValue] = None
        chart_obs_utc_iso = None
        if closes and timestamps:
            valid_closes = [
                (i, c_val)
                for i, c in enumerate(closes)
                if (c_val := to_float(c)) is not None and math.isfinite(c_val)
            ]
            if valid_closes:
                last_idx, last_c = valid_closes[-1]
                if last_idx < len(timestamps):
                    ts = timestamps[last_idx]
                    chart_obs_utc_iso = _epoch_to_utc_iso8601(ts)
                    if chart_obs_utc_iso is not None and float(last_c) > 0:
                        price_obs = TimestampedValue(
                            value=float(last_c),
                            obs_time=chart_obs_utc_iso,
                            source="INTRADAY_CHART",
                        )

        pre_vol = 0
        post_vol = 0
        total_session_vol = 0
        has_pre_bar = False
        has_post_bar = False
        has_session_bar = False
        pre_last_valid_ts = None
        pre_last_pos_ts = None
        post_last_valid_ts = None
        post_last_pos_ts = None
        sess_last_valid_ts = None
        sess_last_pos_ts = None

        if volumes and timestamps:
            latest_date = None
            for ts in reversed(timestamps):
                if _epoch_to_utc_iso8601(ts) is None:
                    continue
                dt_ny = datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone(ny_tz)
                latest_date = dt_ny.date()
                break

            if latest_date is not None:
                cutoff_ts = 0
                if latest_date == now.date():
                    if status == "OPEN":
                        cutoff_dt = now.replace(hour=9, minute=30, second=0, microsecond=0)
                        cutoff_ts = cutoff_dt.timestamp()
                    elif status == "AFTER-HOURS":
                        cutoff_dt = now.replace(hour=16, minute=0, second=0, microsecond=0)
                        cutoff_ts = cutoff_dt.timestamp()

                t_0400 = time(4, 0)
                t_0930 = time(9, 30)
                t_1600 = time(16, 0)
                t_2000 = time(20, 0)

                for ts, v in zip(timestamps, volumes):
                    if _epoch_to_utc_iso8601(ts) is None:
                        continue
                    v_num = to_float(v)
                    if v_num is None or not math.isfinite(v_num) or v_num < 0:
                        continue
                    v_int = int(v_num)

                    dt_ny = datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone(ny_tz)
                    if dt_ny.date() != latest_date:
                        continue

                    t_idx = dt_ny.time()
                    if t_0400 <= t_idx < t_0930:
                        has_pre_bar = True
                        pre_last_valid_ts = ts
                        pre_vol += v_int
                        if v_int > 0:
                            pre_last_pos_ts = ts
                    elif t_1600 <= t_idx < t_2000:
                        has_post_bar = True
                        post_last_valid_ts = ts
                        post_vol += v_int
                        if v_int > 0:
                            post_last_pos_ts = ts

                    if float(ts) >= cutoff_ts:
                        has_session_bar = True
                        sess_last_valid_ts = ts
                        total_session_vol += v_int
                        if v_int > 0:
                            sess_last_pos_ts = ts

        def _make_vol_obs(has_bar: bool, total_val: int, pos_ts: Any, valid_ts: Any) -> Optional[TimestampedValue]:
            if not has_bar:
                return None
            chosen_iso = _epoch_to_utc_iso8601(valid_ts)
            if chosen_iso is None:
                return None
            return TimestampedValue(value=int(total_val), obs_time=chosen_iso, source="INTRADAY_CHART")

        sess_vol_obs = _make_vol_obs(has_session_bar, total_session_vol, sess_last_pos_ts, sess_last_valid_ts)
        pre_vol_obs = _make_vol_obs(has_pre_bar, pre_vol, pre_last_pos_ts, pre_last_valid_ts)
        post_vol_obs = _make_vol_obs(has_post_bar, post_vol, post_last_pos_ts, post_last_valid_ts)
        live_price = price_obs.value if price_obs is not None else None

        return {
            "price": price_obs,
            "session_volume": sess_vol_obs,
            "premarket_volume": pre_vol_obs,
            "after_hours_volume": post_vol_obs,
            "chart_obs_utc_iso": chart_obs_utc_iso,
            "legacy_tuple": (live_price, total_session_vol, pre_vol, post_vol, chart_obs_utc_iso),
        }
    except Exception:
        return empty_result


def _acquire_live_chart_data_raw(symbol, status):
    """
    Worker-safe fetch of 5-day 1m chart data without mutating shared cache state.
    Returns legacy 5-tuple (live_price, total_session_vol, pre_vol, post_vol, chart_obs_utc_iso).
    """
    return _acquire_live_chart_observations_raw(symbol, status)["legacy_tuple"]


def get_live_chart_data(symbol, status):
    """
    Fetches 5-day 1m chart data to ensure we have the latest session (handling holidays).
    Calculates:
    - live_price (last close)
    - total_vol (volume since session start)
    - pre_vol (volume from 04:00 to 09:30 ET today)
    - after_hours_vol (volume from 16:00 to 20:00 ET today)
    """
    live_price, total_session_vol, pre_vol, post_vol, chart_obs_utc_iso = _acquire_live_chart_data_raw(symbol, status)
    if chart_obs_utc_iso is not None:
        try:
            ny_tz = ZoneInfo("America/New_York")
            cache.chart_obs_time[symbol] = parse_utc_iso8601_timestamp(chart_obs_utc_iso).astimezone(ny_tz)
        except Exception:
            cache.chart_obs_time[symbol] = None
    elif live_price is not None:
        cache.chart_obs_time[symbol] = None
    return live_price, total_session_vol, pre_vol, post_vol


def _acquire_true_intraday_vwap_bars(symbol, status):
    """
    Fetches intraday 1m bars for true VWAP without mutating shared cache.
    Returns (valid_bars, obs_utc_iso, before_anchor).
    """
    ny_tz = ZoneInfo("America/New_York")
    now = datetime.now(ny_tz)

    if status == "PRE-MARKET":
        anchor_time = now.replace(hour=4, minute=0, second=0, microsecond=0)
        include_pre = "true"
    else:
        anchor_time = now.replace(hour=9, minute=30, second=0, microsecond=0)
        include_pre = "false"

    if now < anchor_time:
        return [], None, True

    ts_open = int(anchor_time.timestamp())
    ts_now = int(now.timestamp())

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1={ts_open}&period2={ts_now}&interval=1m&includePrePost={include_pre}"
    r = safe_yf_get(url, timeout=3)
    data = r.json()
    result = data['chart']['result'][0]
    timestamps = result.get('timestamp', [])
    indicators = result['indicators']['quote'][0]
    closes = indicators.get('close', [])
    highs = indicators.get('high', [])
    lows = indicators.get('low', [])
    volumes = indicators.get('volume', [])

    valid = []
    last_ts = None
    if timestamps:
        for ts, h, l, c, v in zip(timestamps, highs, lows, closes, volumes):
            if _epoch_to_utc_iso8601(ts) is None:
                continue
            if c is not None and h is not None and l is not None and v is not None and v > 0:
                dt = datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone(ny_tz)
                if dt.date() == now.date():
                    valid.append((h, l, c, v))
                    last_ts = ts
    else:
        valid = [(h, l, c, v) for h, l, c, v in zip(highs, lows, closes, volumes)
                 if c is not None and h is not None and l is not None and v is not None and v > 0]

    return valid, _epoch_to_utc_iso8601(last_ts), False


def _acquire_fallback_vwap_payload(symbol):
    """
    Fetches 1d fallback chart bars or Finnhub OHLC proxy without mutating shared cache.
    Returns (payload_dict_or_none, obs_utc_iso).
    """
    try:
        ny_tz = ZoneInfo("America/New_York")
        now = datetime.now(ny_tz)
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=1m"
        r = safe_yf_get(url, timeout=2)
        data = r.json()
        result = data['chart']['result'][0]
        timestamps = result.get('timestamp', [])
        q = result['indicators']['quote'][0]
        closes = q.get('close', [])
        highs = q.get('high', [])
        lows = q.get('low', [])
        volumes = q.get('volume', [])

        is_today = False
        if timestamps:
            latest_dt = datetime.fromtimestamp(timestamps[-1], tz=ny_tz)
            if latest_dt.date() == now.date():
                is_today = True

        if is_today:
            valid = []
            last_ts = None
            for ts, h, l, c, v in zip(timestamps, highs, lows, closes, volumes):
                if c is None or h is None or l is None or v is None or v <= 0:
                    continue
                dt = datetime.fromtimestamp(ts, tz=ny_tz)
                if dt.date() == now.date():
                    valid.append((h, l, c, v))
                    last_ts = ts
            if valid:
                return {"kind": "vwap", "symbol": symbol, "bars": valid, "source": "FALLBACK_VWAP_CHART"}, _epoch_to_utc_iso8601(last_ts)

        # Finnhub OHLC live session proxy fallback
        if USE_FINNHUB and FINNHUB_API_KEY:
            try:
                f_url = f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={FINNHUB_API_KEY}"
                res = session.get(f_url, timeout=2)
                if res.status_code == 200:
                    f_data = res.json()
                    c = f_data.get('c')
                    h = f_data.get('h')
                    l = f_data.get('l')
                    o = f_data.get('o')
                    t = f_data.get('t')
                    if c and h and l and o and float(c) > 0:
                        return {
                            "kind": "vwap",
                            "symbol": symbol,
                            "finnhub_ohlc": (float(o), float(h), float(l), float(c)),
                            "source": "FALLBACK_VWAP_FINNHUB",
                        }, _epoch_to_utc_iso8601(t)
            except Exception:
                pass

        return None, None
    except Exception:
        return None, None


def _compute_vwap_from_payload(payload: dict) -> float:
    """Computes VWAP from validated raw bars or Finnhub OHLC proxy."""
    bars = payload.get("bars")
    if bars:
        total_vp = sum(((h + l + c) / 3) * v for h, l, c, v in bars)
        total_v = sum(v for _, _, _, v in bars)
        if total_v > 0:
            return float(total_vp / total_v)
    fh = payload.get("finnhub_ohlc")
    if fh and len(fh) == 4:
        o, h, l, c = fh
        if c and float(c) > 0:
            return float((o + h + l + (2 * c)) / 5.0)
    return 0.0


def get_true_intraday_vwap(symbol, status):
    try:
        valid, _, before_anchor = _acquire_true_intraday_vwap_bars(symbol, status)
        if before_anchor:
            return 0.0
        if not valid:
            return fallback_vwap(symbol)

        total_vp = sum(((h + l + c) / 3) * v for h, l, c, v in valid)
        total_v = sum(v for _, _, _, v in valid)
        if total_v > 0:
            return total_vp / total_v
        return fallback_vwap(symbol)
    except Exception:
        return fallback_vwap(symbol)


def fallback_vwap(symbol):
    payload, _ = _acquire_fallback_vwap_payload(symbol)
    if not payload:
        return 0.0
    return _compute_vwap_from_payload(payload)

def get_finnhub_quote(symbol):
    if not USE_FINNHUB:
        return None, None, None
    try:
        url = f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={FINNHUB_API_KEY}"
        r = session.get(url, timeout=2)
        if r.status_code != 200:
            return None, None, None
        data = r.json()
        c = to_float(data.get('c'))
        pc = to_float(data.get('pc'))
        t = data.get('t')
        if c and c > 0:
            return c, pc, t
    except:
        pass
    return None, None, None

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

def _acquire_finnhub_quote_raw(symbol: str) -> Optional[TimestampedValue]:
    """
    Worker-safe Finnhub quote adapter returning current price (c) paired strictly with
    provider timestamp (t). Never assigns quote timestamp t to previous close (pc),
    and never treats pc as an instant price.
    """
    c, _pc, t = get_finnhub_quote(symbol)
    c_val = to_float(c)
    if c_val is None or not math.isfinite(c_val) or c_val <= 0:
        return None
    obs_iso = _epoch_to_utc_iso8601(t)
    if obs_iso is None:
        return None
    try:
        return TimestampedValue(value=float(c_val), obs_time=obs_iso, source="FINNHUB")
    except ValueError:
        return None


def _acquire_polygon_previous_agg_raw(symbol: str) -> Optional[SessionReferenceValue]:
    """
    Fetches Polygon /v2/aggs/ticker/{symbol}/prev and derives the effective NY
    trading-session date from the aggregate's Unix millisecond timestamp (`t`).
    Returns SessionReferenceValue or None fail-closed. Never writes into cache.volumes.
    """
    if not USE_POLYGON or not POLYGON_API_KEY:
        return None
    try:
        url = f"https://api.polygon.io/v2/aggs/ticker/{symbol}/prev?adjusted=true&apiKey={POLYGON_API_KEY}"
        r = session.get(url, timeout=2)
        if r.status_code != 200:
            return None
        data = r.json()
        if not isinstance(data, dict):
            return None
        if "resultsCount" in data and (data.get("resultsCount") or 0) <= 0:
            return None
        results = data.get("results") or []
        if not results or not isinstance(results[0], dict):
            return None
        rec = results[0]
        raw_v = rec.get("v")
        raw_t = rec.get("t")
        v_num = to_float(raw_v)
        if v_num is None or not math.isfinite(v_num) or v_num < 0:
            return None
        if raw_t is None or isinstance(raw_t, bool) or not isinstance(raw_t, (int, float)):
            return None
        t_ms = float(raw_t)
        if not math.isfinite(t_ms) or t_ms < 100_000_000_000:
            return None
        ny_tz = ZoneInfo("America/New_York")
        today_ny = datetime.now(ny_tz).date()
        dt_ny = datetime.fromtimestamp(t_ms / 1000.0, tz=timezone.utc).astimezone(ny_tz)
        if dt_ny.date() >= today_ny:
            return None
        sess_date = validate_session_date(dt_ny.date().isoformat(), reference_date=today_ny)
        return SessionReferenceValue(
            value=int(v_num),
            effective_session_date=sess_date,
            source="POLYGON_PREV_AGG",
        )
    except Exception:
        return None


def _acquire_previous_close_raw(symbol: str, now_ny: Optional[datetime] = None) -> Optional[SessionReferenceValue]:
    """
    Fetches completed previous daily close paired with its own provider daily-bar timestamp
    from Yahoo 5d/1d chart, deriving and validating the NY effective session date.
    Never falls back to untimestamped metadata and never borrows regularMarketTime.
    """
    try:
        ny_tz = ZoneInfo("America/New_York")
        cur_ny = now_ny if now_ny is not None else datetime.now(ny_tz)
        today_ny = cur_ny.date()

        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d"
        r = safe_yf_get(url, timeout=2)
        data = r.json()
        result = data["chart"]["result"][0]
        timestamps = result.get("timestamp", [])
        q = result.get("indicators", {}).get("quote", [{}])[0]
        closes = q.get("close", [])
        if not timestamps or not closes:
            return None

        prior_bars: List[Tuple[str, float]] = []
        for ts, c in zip(timestamps, closes):
            if _epoch_to_utc_iso8601(ts) is None:
                continue
            c_val = to_float(c)
            if c_val is None or not math.isfinite(c_val) or c_val <= 0:
                continue
            bar_date = datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone(ny_tz).date()
            if bar_date > today_ny:
                continue
            if bar_date < today_ny:
                prior_bars.append((bar_date.isoformat(), float(c_val)))

        if not prior_bars:
            return None
        eff_date_str, prev_close_val = prior_bars[-1]
        valid_date = validate_session_date(eff_date_str, reference_date=today_ny)
        return SessionReferenceValue(
            value=float(prev_close_val),
            effective_session_date=valid_date,
            source="YAHOO_DAILY_CLOSE",
        )
    except Exception:
        return None


def _acquire_chart_open_raw(symbol: str, now_ny: Optional[datetime] = None) -> Optional[TimestampedValue]:
    """
    Fetches today's regular-session opening price from 1m chart data paired strictly with
    the actual opening bar's own UTC observation timestamp.
    """
    try:
        ny_tz = ZoneInfo("America/New_York")
        now = now_ny if now_ny is not None else datetime.now(ny_tz)
        open_dt = now.replace(hour=9, minute=30, second=0, microsecond=0)
        if now < open_dt:
            return None
        end_dt = now.replace(hour=9, minute=35, second=0, microsecond=0)
        if now < end_dt:
            end_dt = now
        ts1 = int(open_dt.timestamp())
        ts2 = int(end_dt.timestamp())
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1={ts1}&period2={ts2}&interval=1m&includePrePost=false"
        r = safe_yf_get(url, timeout=3)
        data = r.json()
        result = data["chart"]["result"][0]
        timestamps = result.get("timestamp", [])
        opens = result["indicators"]["quote"][0].get("open", [])
        if not timestamps or not opens:
            return None
        for ts, o in zip(timestamps, opens):
            obs_iso = _epoch_to_utc_iso8601(ts)
            if obs_iso is None:
                continue
            dt_ny = datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone(ny_tz)
            if dt_ny.date() != now.date():
                continue
            o_val = to_float(o)
            if o_val is not None and math.isfinite(o_val) and o_val > 0:
                return TimestampedValue(
                    value=float(o_val),
                    obs_time=obs_iso,
                    source="INTRADAY_CHART_OPEN",
                )
    except Exception:
        pass
    return None


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
        meta = result.get('meta', {})
        prev_c = meta.get('chartPreviousClose') or meta.get('previousClose') or meta.get('regularMarketPreviousClose')
        if prev_c and float(prev_c) > 0:
            return float(prev_c)
        q = result.get('indicators', {}).get('quote', [{}])[0]
        closes = q.get('close', [])
        valid = [c for c in closes if c is not None and float(c) > 0]
        if len(valid) >= 2:
            return float(valid[-2])
    except:
        pass
    return None

def _get_chart_open(symbol):
    """Fetch today's true regular-session opening price from 1-minute chart data.
    More reliable than batch quote's regularMarketOpen for small-cap stocks."""
    obs = _acquire_chart_open_raw(symbol)
    return obs.value if obs is not None else None

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


_GEX_REQUIRED_KEYS = (
    "net_gex",
    "flip_price",
    "inventory_velocity_delta",
    "gex_slope",
    "flip_proximity_percent",
    "strike_oi_magnet",
)


def _default_gex_profile() -> dict:
    return {
        'net_gex': 0.0,
        'flip_price': 0.0,
        'inventory_velocity_delta': 0.0,
        'gex_slope': 0.0,
        'flip_proximity_percent': 0.0,
        'strike_oi_magnet': 0.0
    }


def _compute_gex_profile_from_inventory(
    symbol: str,
    spot_price: float,
    option_inventory: List[dict],
    strike_oi_map: Dict[float, float],
    avg_vol: Any = 0,
) -> dict:
    """
    Pure GEX, Gamma Flip, and Inventory Velocity Delta calculation on detached option inventory.
    Preserves existing mathematical formulas, thresholds, and output fields identically.
    """
    magnet_price = 0.0
    if strike_oi_map:
        magnet_price = max(strike_oi_map, key=lambda k: strike_oi_map.get(k, 0.0))

    r = 0.045  # Approx risk-free rate

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
            net_delta += (-delta * opt['oi'] * 100)  # Dealer Short Call
        else:
            net_gex -= term
            net_delta += (-delta * opt['oi'] * 100)  # Dealer Short Put

    final_gex = net_gex
    final_slope = 0.0

    if avg_vol and avg_vol > 0:
        final_gex = net_gex / (avg_vol * spot_price)
    else:
        # Fallback for indices or missing volume: Assume 100M volume to align with UI decimal thresholds (e.g. 0.015)
        final_gex = net_gex / (100_000_000 * spot_price)

    # 2. Calc Gamma Flip (Scan +/- 50%)
    flip_price = 0.0
    low_p = spot_price * 0.50
    high_p = spot_price * 1.50
    steps = 100
    step_size = (high_p - low_p) / steps
    prices = [low_p + i * step_size for i in range(steps + 1)]
    gex_values = []

    for p_sim in prices:
        g_sim = 0.0
        for opt in option_inventory:
            val = calculate_gamma(p_sim, opt['K'], opt['T'], r, opt['sigma'])
            term = val * opt['oi'] * (p_sim**2)  # Removed * 100 scaling
            if opt['type'] == 'call':
                g_sim += term
            else:
                g_sim -= term
        gex_values.append(g_sim)

    for i in range(len(gex_values) - 1):
        if (gex_values[i] > 0 and gex_values[i + 1] < 0) or (gex_values[i] < 0 and gex_values[i + 1] > 0):
            y1, y2 = gex_values[i], gex_values[i + 1]
            x1, x2 = prices[i], prices[i + 1]
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
        term = val * opt['oi'] * (p_up**2)  # Removed * 100 scaling
        if opt['type'] == 'call':
            gex_up += term
        else:
            gex_up -= term

    raw_slope = (gex_up - net_gex) / (p_up - spot_price)
    final_slope = raw_slope
    if avg_vol and avg_vol > 0:
        final_slope = raw_slope / (avg_vol * spot_price)

    # ENH_262: Mathematical bound check for gamma flip interpolation outliers
    sym = str(symbol or '').strip().upper()
    is_index = sym in ['SPY', 'QQQ', 'IWM', 'DIA']
    bound = 0.05 if is_index else 0.15
    if flip_price > 0 and spot_price > 0:
        if abs(flip_price - spot_price) / spot_price > bound:
            # Quarantined outlier per ENH_262
            flip_price = 0.0

    flip_prox = 0.0
    if flip_price > 0:
        flip_prox = (spot_price - flip_price) / spot_price

    res = {
        'net_gex': final_gex,
        'flip_price': flip_price,
        'inventory_velocity_delta': net_delta,
        'gex_slope': final_slope,
        'flip_proximity_percent': flip_prox,
        'strike_oi_magnet': magnet_price
    }
    sanitized = {}
    for key, value in res.items():
        if isinstance(value, bool):
            raise ValueError(f"Invalid boolean GEX result for {key}")

        try:
            numeric_value = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(
                f"Invalid GEX calculation result for {key}: {value!r}"
            ) from exc

        if not math.isfinite(numeric_value):
            raise ValueError(
                f"Non-finite GEX calculation result for {key}: {value!r}"
            )

        sanitized[key] = numeric_value

    return sanitized


def _acquire_options_gex_raw(
    symbol: str,
    ticker_obj: Any = None,
    spot_price: Optional[float] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    spot_snapshot: Optional[Dict[str, Any]] = None,
) -> ObservedResult:
    """
    Worker-safe options/GEX acquisition and calculation.
    Executes all options provider I/O (expirations, option_chain, fast_info) off the
    coordinating thread, never mutates shared caches, and raises on failed/empty acquisition
    so failed jobs do not masquerade as valid zero-valued GEX results.
    """
    if cancel_check and cancel_check():
        raise RuntimeError("Cancelled before options/GEX acquisition")

    sym_clean = str(symbol or "").strip().upper()
    if not sym_clean:
        raise ValueError("Empty symbol for options/GEX acquisition")

    spot_num = to_float(spot_price)
    if spot_num is None or not math.isfinite(spot_num) or spot_num <= 0:
        raise ValueError(f"Invalid spot price for options/GEX acquisition on {sym_clean}: {spot_price!r}")

    eff_ticker = ticker_obj if ticker_obj is not None else yf.Ticker(sym_clean)

    expirations = eff_ticker.options
    if not expirations:
        raise ValueError(f"No options expirations available for {sym_clean}")

    if cancel_check and cancel_check():
        raise RuntimeError("Cancelled during options/GEX acquisition")

    target_exps = list(expirations[:2])
    ny_now = datetime.now(ZoneInfo("America/New_York"))

    option_inventory: List[dict] = []
    strike_oi_map: Dict[float, float] = {}
    chain_errors: List[str] = []

    for exp_date_str in target_exps:
        if cancel_check and cancel_check():
            raise RuntimeError("Cancelled during option-chain retrieval")
        try:
            chain = eff_ticker.option_chain(exp_date_str)
            exp_dt = datetime.strptime(exp_date_str, "%Y-%m-%d").replace(tzinfo=ZoneInfo("America/New_York"))
            exp_dt = exp_dt.replace(hour=16, minute=0, second=0)
            T = (exp_dt - ny_now).total_seconds() / (365 * 24 * 3600)
            if T <= 0.001:
                T = 0.001

            calls_df = getattr(chain, "calls", None)
            if calls_df is not None and not calls_df.empty:
                for _, row in calls_df.iterrows():
                    oi_val = row.get('openInterest')
                    if pd.isna(oi_val) or oi_val <= 0:
                        oi_val = row.get('volume')
                    if pd.isna(oi_val) or oi_val <= 0:
                        continue
                    iv_val = row.get('impliedVolatility')
                    if pd.isna(iv_val) or iv_val <= 0:
                        continue
                    k_num = to_float(row.get('strike'))
                    oi_num = to_float(oi_val)
                    iv_num = to_float(iv_val)
                    if k_num is None or k_num <= 0 or oi_num is None or oi_num <= 0 or iv_num is None or iv_num <= 0:
                        continue
                    strike_oi_map[k_num] = strike_oi_map.get(k_num, 0.0) + oi_num
                    option_inventory.append({
                        'K': k_num, 'T': T, 'sigma': iv_num,
                        'oi': oi_num, 'type': 'call'
                    })

            puts_df = getattr(chain, "puts", None)
            if puts_df is not None and not puts_df.empty:
                for _, row in puts_df.iterrows():
                    oi_val = row.get('openInterest')
                    if pd.isna(oi_val) or oi_val <= 0:
                        oi_val = row.get('volume')
                    if pd.isna(oi_val) or oi_val <= 0:
                        continue
                    iv_val = row.get('impliedVolatility')
                    if pd.isna(iv_val) or iv_val <= 0:
                        continue
                    k_num = to_float(row.get('strike'))
                    oi_num = to_float(oi_val)
                    iv_num = to_float(iv_val)
                    if k_num is None or k_num <= 0 or oi_num is None or oi_num <= 0 or iv_num is None or iv_num <= 0:
                        continue
                    strike_oi_map[k_num] = strike_oi_map.get(k_num, 0.0) + oi_num
                    option_inventory.append({
                        'K': k_num, 'T': T, 'sigma': iv_num,
                        'oi': oi_num, 'type': 'put'
                    })
        except Exception as chain_err:
            chain_errors.append(f"{exp_date_str}: {chain_err}")
            continue

    if not option_inventory:
        detail = "; ".join(chain_errors) if chain_errors else "empty or invalid option chains"
        raise ValueError(f"No valid option contracts acquired for {sym_clean}: {detail}")

    if cancel_check and cancel_check():
        raise RuntimeError("Cancelled before GEX average volume retrieval")

    avg_vol = 0
    try:
        avg_vol = eff_ticker.fast_info.three_month_average_volume
    except Exception:
        avg_vol = 0

    gex_profile = _compute_gex_profile_from_inventory(
        symbol=sym_clean,
        spot_price=spot_num,
        option_inventory=option_inventory,
        strike_oi_map=strike_oi_map,
        avg_vol=avg_vol,
    )
    fetched_at = t_time.time()

    res_data = {
        "kind": "options_gex",
        "symbol": sym_clean,
        "spot_price": float(spot_num),
        "gex_profile": gex_profile,
        "fetched_at": float(fetched_at),
        "source": "YAHOO_OPTIONS",
        "expirations_used": target_exps,
        "contract_count": len(option_inventory),
    }
    if spot_snapshot is not None:
        res_data["spot_snapshot"] = dict(spot_snapshot) if isinstance(spot_snapshot, dict) else spot_snapshot

    return ObservedResult(
        data=res_data,
        obs_time=None,
        provenance=None,
    )


def get_gex_profile(ticker_obj, spot_price):
    """
    Calculates GEX, Gamma Flip, and Inventory Velocity Delta (Net Delta).
    Synchronous compatibility entrypoint delegating to _acquire_options_gex_raw.
    The recurring monitoring and startup paths use HEAVY_MARKET_DATA_COORDINATOR instead.
    """
    default_res = _default_gex_profile()
    if spot_price is None or spot_price <= 0:
        return default_res

    try:
        sym = getattr(ticker_obj, 'ticker', '') or 'UNKNOWN'
        observed = _acquire_options_gex_raw(
            symbol=sym,
            ticker_obj=ticker_obj,
            spot_price=spot_price,
        )
        return dict(observed.data["gex_profile"])
    except Exception:
        return default_res


# -----------------------------
# LOGIC (UPDATED)
# -----------------------------

try:
    from zoneinfo import ZoneInfo
    ny_tz = ZoneInfo("America/New_York")
except:
    ny_tz = None

def get_today_str():
    if ny_tz:
        try:
            return datetime.now(ny_tz).strftime("%Y-%m-%d")
        except: pass
    return datetime.now().strftime("%Y-%m-%d")

_HISTORICAL_REQUIRED_TECH_KEYS = (
    "SMA_20",
    "SMA_50",
    "SMA_200",
    "RSI",
    "MACD",
    "MACD_Signal",
    "MACD_Hist",
    "MACD_Slope",
    "MACD_Status",
    "BB_Upper",
    "BB_Lower",
    "BB_Pct",
    "MFI",
    "ATR_Pct",
    "ATR",
    "Trend_Score",
    "Last_Reg_Close",
)


def _unavailable_technicals_dict() -> dict:
    return {
        "SMA_20": None,
        "SMA_50": None,
        "SMA_200": None,
        "RSI": None,
        "MACD": None,
        "MACD_Signal": None,
        "MACD_Hist": None,
        "MACD_Slope": "UNKNOWN",
        "MACD_Status": "NEUTRAL",
        "BB_Upper": None,
        "BB_Lower": None,
        "BB_Pct": None,
        "MFI": None,
        "ATR_Pct": None,
        "ATR": None,
        "Trend_Score": 0,
        "Last_Reg_Close": None,
        "status": "UNAVAILABLE",
    }


def _extract_last_regular_close_for_history(
    symbol: str,
    hist: pd.DataFrame,
    close: pd.Series,
    t_obj: Any,
    now_ny: Optional[datetime] = None,
) -> Optional[float]:
    """
    Worker-safe helper resolving the previous regular close using detached historical
    bars and provider access (fast_info / get_previous_close) off the coordinating thread.
    """
    last_reg_close = None
    ny_tz_local = ZoneInfo("America/New_York")
    cur_ny = now_ny if now_ny is not None else datetime.now(ny_tz_local)
    today_date = cur_ny.date()
    is_pre_market = (cur_ny.time() < time(9, 30))

    if is_pre_market:
        if len(close) >= 1:
            valid_closes = [c_val for c in close if (c_val := to_float(c)) is not None and c_val > 0]
            if valid_closes:
                last_reg_close = valid_closes[-1]
        if last_reg_close is None and t_obj is not None:
            try:
                fi = getattr(t_obj, 'fast_info', None)
                fi_last = to_float(getattr(fi, 'last_price', None)) if fi is not None else None
                if fi_last is not None and fi_last > 0:
                    last_reg_close = fi_last
            except Exception:
                pass
    else:
        if t_obj is not None:
            try:
                fi = getattr(t_obj, 'fast_info', None)
                fi_close = (
                    (getattr(fi, 'regular_market_previous_close', None) or getattr(fi, 'previous_close', None))
                    if fi is not None
                    else None
                )
                last_reg_close = to_float(fi_close)
            except Exception:
                pass

        if last_reg_close is None and len(close) >= 1:
            valid_closes = [c_val for c in close if (c_val := to_float(c)) is not None and c_val > 0]
            if valid_closes:
                last_bar_date = hist.index[-1].date()
                if last_bar_date == today_date and len(valid_closes) >= 2:
                    last_reg_close = valid_closes[-2]
                else:
                    last_reg_close = valid_closes[-1]

    if last_reg_close is None:
        alt_pc = get_previous_close(symbol)
        if alt_pc:
            last_reg_close = to_float(alt_pc)

    return last_reg_close


def _compute_technicals_from_history(
    hist: pd.DataFrame,
    last_reg_close: Optional[float] = None,
) -> dict:
    """
    Pure technical indicator calculation from a validated historical DataFrame.
    Preserves SMA, RSI, MACD, Bollinger Bands, MFI, ATR, and Trend_Score exactly.
    """
    close = hist['Close']
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]

    # --- SAFER SMA CHECKS ---
    sma_20 = to_float(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else None
    sma_50 = to_float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
    sma_200 = to_float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None

    # --- Helper to safely get the last value from a Series or scalar ---
    def _last(val):
        status, num = classify_market_numeric(val)
        return num if status == "VALID" else None

    # --- RSI (Wilder 9-day) ---
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).ewm(com=8, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(com=8, adjust=False).mean()
    rs = gain / (loss + 1e-9)
    rsi_val = _last(100 - (100 / (1 + rs)))

    # --- MACD ---
    ema_12 = close.ewm(span=12, adjust=False).mean()
    ema_26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema_12 - ema_26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = macd_line - signal_line
    macd_val = _last(macd_line)
    macd_signal_val = _last(signal_line)
    macd_hist_val = _last(macd_hist)

    # Compute 4-State Histogram Slope (macd_slope)
    if macd_hist_val is not None and len(macd_hist) >= 2:
        prev_hist = to_float(macd_hist.iloc[-2], default=0.0)
        if macd_hist_val >= 0:
            macd_slope_val = "EXPANDING_POSITIVE" if macd_hist_val >= prev_hist else "CONTRACTING_POSITIVE"
        else:
            macd_slope_val = "EXPANDING_NEGATIVE" if macd_hist_val <= prev_hist else "CONTRACTING_NEGATIVE"
        macd_status_val = "BULLISH" if macd_hist_val > 0.001 else ("BEARISH" if macd_hist_val < -0.001 else "NEUTRAL")
    else:
        macd_slope_val = "UNKNOWN"
        macd_status_val = "NEUTRAL"

    # --- Bollinger Bands (20-day, 2 std) ---
    if len(close) >= 20:
        sma_20_bb = close.rolling(20).mean()
        std_20_bb = close.rolling(20).std()
        upper_bb = sma_20_bb + (std_20_bb * 2)
        lower_bb = sma_20_bb - (std_20_bb * 2)
        bb_upper_val = _last(upper_bb)
        bb_lower_val = _last(lower_bb)
        last_c = _last(close)
        if bb_upper_val is not None and bb_lower_val is not None and last_c is not None and bb_upper_val != bb_lower_val:
            bb_pct_val = (last_c - bb_lower_val) / (bb_upper_val - bb_lower_val)
        else:
            bb_pct_val = 0.5
    else:
        bb_upper_val = None
        bb_lower_val = None
        bb_pct_val = None

    # --- MFI (Money Flow Index 14-day) ---
    if 'Volume' in hist.columns and 'High' in hist.columns and 'Low' in hist.columns:
        typ_price = (hist['High'] + hist['Low'] + close) / 3
        raw_money_flow = typ_price * hist['Volume']
        diff_typ = typ_price.diff()
        pos_flow = raw_money_flow.where(diff_typ > 0, 0.0)
        neg_flow = raw_money_flow.where(diff_typ < 0, 0.0)
        pos_flow_sum = pos_flow.rolling(14).sum()
        neg_flow_sum = neg_flow.rolling(14).sum()
        money_ratio = pos_flow_sum / (neg_flow_sum + 1e-9)
        mfi_series = 100 - (100 / (1 + money_ratio))
        mfi_val = _last(mfi_series)
    else:
        mfi_val = None

    # --- ATR (Wilder) ---
    tr = pd.concat([
        hist['High'] - hist['Low'],
        (hist['High'] - close.shift(1)).abs(),
        (hist['Low'] - close.shift(1)).abs()
    ], axis=1).max(axis=1)

    atr = _last(tr.ewm(alpha=1 / 14, adjust=False).mean())
    last_close = _last(close)
    atr_pct = (atr / last_close) * 100 if (atr is not None and last_close is not None and last_close != 0) else None

    # --- Trend Score (Weighted — SMA200 cross is ±2) ---
    trend_score = 0
    if sma_20 is not None and sma_50 is not None:
        trend_score += 1 if sma_20 > sma_50 else -1
    if sma_50 is not None and sma_200 is not None:
        trend_score += 2 if sma_50 > sma_200 else -2  # Golden/Death cross weighted 2x
    if sma_20 is not None and last_close is not None:
        trend_score += 1 if last_close > sma_20 else -1

    return {
        "SMA_20": sma_20,
        "SMA_50": sma_50,
        "SMA_200": sma_200,
        "RSI": to_float(rsi_val),
        "MACD": to_float(macd_val),
        "MACD_Signal": to_float(macd_signal_val),
        "MACD_Hist": to_float(macd_hist_val),
        "MACD_Slope": macd_slope_val,
        "MACD_Status": macd_status_val,
        "BB_Upper": to_float(bb_upper_val),
        "BB_Lower": to_float(bb_lower_val),
        "BB_Pct": to_float(bb_pct_val),
        "MFI": to_float(mfi_val),
        "ATR_Pct": to_float(atr_pct),
        "ATR": to_float(atr),
        "Trend_Score": int(trend_score),
        "Last_Reg_Close": last_reg_close,
    }


def _acquire_historical_and_technicals_raw(
    symbol: str,
    t_obj: Any = None,
    existing_hist: Optional[pd.DataFrame] = None,
    today_str: Optional[str] = None,
    now_ny: Optional[datetime] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> ObservedResult:
    """
    Worker-safe historical OHLCV acquisition and technical-indicator calculation.
    Executes Yahoo history, Polygon fallback, fast_info, and get_previous_close off the
    coordinating thread, validates the DataFrame against the detached existing_hist snapshot,
    never mutates shared caches, and returns a detached ObservedResult candidate.
    """
    if cancel_check and cancel_check():
        raise RuntimeError("Cancelled before historical acquisition")

    sym_clean = str(symbol or "").strip().upper()
    if not sym_clean:
        raise ValueError("Empty symbol for historical acquisition")

    eff_today_str = today_str or get_today_str()
    eff_t_obj = t_obj if t_obj is not None else yf.Ticker(sym_clean)

    hist = None
    source_name = None
    try:
        hist = eff_t_obj.history(period="2y", interval="1d")
        if hist is None or hist.empty:
            hist = eff_t_obj.history(period="1y", interval="1d")
        if hist is None or hist.empty:
            hist = eff_t_obj.history(period="3mo", interval="1d")
        if hist is None or hist.empty:
            raise ValueError("Empty YF")
        source_name = "YAHOO_HISTORY"
    except Exception:
        if cancel_check and cancel_check():
            raise RuntimeError("Cancelled during historical acquisition")
        try:
            hist = get_polygon_history_df(sym_clean)
            if hist is not None and not hist.empty:
                source_name = "POLYGON_HISTORY"
        except Exception:
            hist = None

    if cancel_check and cancel_check():
        raise RuntimeError("Cancelled after historical retrieval")

    is_valid, reason = validate_historical_dataframe(hist, existing_hist)
    if not is_valid:
        raise ValueError(f"Historical refresh rejected for {sym_clean}: {reason}")

    hist_detached = hist.copy(deep=True)
    close = hist_detached['Close']
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]

    last_reg_close = _extract_last_regular_close_for_history(
        symbol=sym_clean,
        hist=hist_detached,
        close=close,
        t_obj=eff_t_obj,
        now_ny=now_ny,
    )
    if cancel_check and cancel_check():
        raise RuntimeError("Cancelled during technical calculation")

    technicals = _compute_technicals_from_history(hist_detached, last_reg_close=last_reg_close)
    latest_bar_date_str = hist_detached.index[-1].date().isoformat()

    return ObservedResult(
        data={
            "kind": "historical_data",
            "symbol": sym_clean,
            "history": hist_detached,
            "technicals": technicals,
            "last_history_fetch_date": eff_today_str,
            "regular_close": float(last_reg_close) if (last_reg_close is not None and last_reg_close > 0) else None,
            "source": source_name or "YAHOO_HISTORY",
            "latest_bar_date": latest_bar_date_str,
        },
        obs_time=None,
        provenance=None,
    )


def update_history_and_technicals(symbol, t_obj):
    """
    Synchronous compatibility entrypoint for direct callers/tests.
    The recurring monitoring and startup paths use HEAVY_MARKET_DATA_COORDINATOR instead.
    """
    today_str = get_today_str()
    existing_hist = cache.history.get(symbol)
    if (
        symbol in cache.history
        and existing_hist is not None
        and not existing_hist.empty
        and cache.last_history_fetch_date.get(symbol) == today_str
    ):
        # Already updated today! Skip download.
        return

    existing_hist_copy = (
        existing_hist.copy(deep=True)
        if isinstance(existing_hist, pd.DataFrame) and not existing_hist.empty
        else None
    )

    try:
        observed = _acquire_historical_and_technicals_raw(
            symbol=symbol,
            t_obj=t_obj,
            existing_hist=existing_hist_copy,
            today_str=today_str,
        )
    except ValueError as val_err:
        reason = str(val_err)
        prefix = f"Historical refresh rejected for {str(symbol).strip().upper()}: "
        if reason.startswith(prefix):
            reason = reason[len(prefix):]
        if existing_hist is not None and not existing_hist.empty:
            print(f"[Warning] Historical refresh for {symbol} rejected ({reason}); preserving existing cache.")
            return

        # If no prior history existed: do NOT fabricate dummy 0.0 row!
        _publish_market_data_candidate(
            symbol,
            {
                "history": pd.DataFrame(),
                "last_history_fetch_date": None,
                "technicals": _unavailable_technicals_dict(),
            },
        )
        return
    except Exception as e:
        print("Tech error:", e)
        return

    try:
        work_res = WorkResult(
            key=WorkKey(WorkType.HISTORICAL_DATA.value, str(symbol).strip().upper()),
            sequence=1,
            obs_time=None,
            data=observed.data,
            provenance=None,
        )
        sym, updates = _build_historical_cache_candidate(work_res)
        _publish_market_data_candidate(sym, updates)
    except Exception as e:
        print("Tech error:", e)


def _extract_fast_info_snapshot(t_obj) -> dict:
    """Safely extracts live quote attributes from a yfinance Ticker.fast_info object into a plain dict."""
    snapshot = {}
    if t_obj is None:
        return snapshot
    try:
        fi = t_obj.fast_info
    except Exception:
        return snapshot
    if fi is None:
        return snapshot
    for attr in (
        "pre_market_price",
        "post_market_price",
        "last_price",
        "open",
        "day_high",
        "day_low",
        "regular_market_open",
        "regular_market_day_high",
        "regular_market_day_low",
        "last_volume",
        "three_month_average_volume",
        "regular_market_previous_close",
        "previous_close",
    ):
        try:
            val = getattr(fi, attr, None)
            if val is not None:
                snapshot[attr] = val
        except Exception:
            pass
    return snapshot


def select_quote_observation(
    quote_data: Optional[dict],
    status: str,
) -> Tuple[Optional[float], Optional[str], Any]:
    """
    Atomically selects the session-appropriate price, provenance, and corresponding
    source timestamp field from a quote payload so price and observation time always
    come from the exact same provider observation field.
    """
    if not isinstance(quote_data, dict) or not quote_data:
        return None, None, None

    if status == "PRE-MARKET":
        pre_price = to_float(quote_data.get("preMarketPrice"))
        if pre_price is not None and pre_price > 0:
            return pre_price, "BATCH_QUOTE", quote_data.get("preMarketTime")

    elif status in ("AFTER-HOURS", "CLOSED"):
        post_price = to_float(quote_data.get("postMarketPrice"))
        if post_price is not None and post_price > 0:
            return post_price, "BATCH_QUOTE", quote_data.get("postMarketTime")

    reg_price = to_float(quote_data.get("regularMarketPrice"))
    if reg_price is not None and reg_price > 0:
        return reg_price, "BATCH_QUOTE", quote_data.get("regularMarketTime")

    return None, None, None


MARKET_METRIC_QUOTE = "QUOTE"
MARKET_METRIC_SESSION_VOLUME = "SESSION_VOLUME"
MARKET_METRIC_PREMARKET_VOLUME = "PREMARKET_VOLUME"
MARKET_METRIC_AFTER_HOURS_VOLUME = "AFTER_HOURS_VOLUME"
MARKET_METRIC_DAY_OPEN = "DAY_OPEN"
MARKET_METRIC_DAY_HIGH = "DAY_HIGH"
MARKET_METRIC_DAY_LOW = "DAY_LOW"
MARKET_METRIC_REGULAR_CLOSE = "REGULAR_CLOSE"
MARKET_METRIC_PREVIOUS_SESSION_VOLUME = "PREVIOUS_SESSION_VOLUME"
MARKET_METRIC_VWAP = "VWAP"

ALL_MARKET_METRICS = (
    MARKET_METRIC_AFTER_HOURS_VOLUME,
    MARKET_METRIC_DAY_HIGH,
    MARKET_METRIC_DAY_LOW,
    MARKET_METRIC_DAY_OPEN,
    MARKET_METRIC_PREMARKET_VOLUME,
    MARKET_METRIC_PREVIOUS_SESSION_VOLUME,
    MARKET_METRIC_QUOTE,
    MARKET_METRIC_REGULAR_CLOSE,
    MARKET_METRIC_SESSION_VOLUME,
    MARKET_METRIC_VWAP,
)


def _metric_work_key(metric: str, symbol: str) -> WorkKey:
    return WorkKey(WorkType.MARKET_DATA.value, f"{str(metric).strip().upper()}:{str(symbol).strip().upper()}")


def _default_batch_metrics_for_status(status: str) -> Tuple[str, ...]:
    clean_status = str(status or "OPEN").strip().upper()
    metrics = [
        MARKET_METRIC_DAY_HIGH,
        MARKET_METRIC_DAY_LOW,
        MARKET_METRIC_DAY_OPEN,
        MARKET_METRIC_QUOTE,
        MARKET_METRIC_SESSION_VOLUME,
    ]
    if clean_status == "PRE-MARKET":
        metrics.append(MARKET_METRIC_PREMARKET_VOLUME)
    elif clean_status in ("AFTER-HOURS", "CLOSED"):
        metrics.append(MARKET_METRIC_AFTER_HOURS_VOLUME)
    return tuple(sorted(metrics))


def _normalize_requested_metrics(
    metrics: Optional[Sequence[str]],
    default: Tuple[str, ...],
) -> Tuple[str, ...]:
    if metrics is None:
        return default
    cleaned = tuple(sorted({str(m).strip().upper() for m in metrics if m and str(m).strip()}))
    return cleaned if cleaned else default


def _acquire_batch_quotes_raw(
    symbols: Tuple[str, ...],
    status: str,
    metrics: Optional[Tuple[str, ...]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> BatchObservedResult:
    """
    Worker-thread batch quote acquisition. Executes one batched provider request
    and emits independent per-symbol, per-metric ChildObservedResult items with explicit
    MarketDataProvenance and metric-matched source timestamps. Does not mutate shared cache.
    """
    if cancel_check and cancel_check():
        raise RuntimeError("Operation cancelled before batch quote acquisition")

    batch_quotes = get_batch_quotes(list(symbols))

    if cancel_check and cancel_check():
        raise RuntimeError("Operation cancelled during batch quote acquisition")

    metric_set = _normalize_requested_metrics(metrics, default=_default_batch_metrics_for_status(status))
    clean_status = str(status or "OPEN").strip().upper()
    child_items: List[ChildObservedResult] = []

    for sym in symbols:
        q = batch_quotes.get(sym) if isinstance(batch_quotes, dict) else None
        if not isinstance(q, dict) or not q:
            for metric_name in metric_set:
                child_items.append(
                    ChildObservedResult(
                        key=_metric_work_key(metric_name, sym),
                        data=None,
                        obs_time=None,
                        error=f"Missing batch quote for {sym}",
                        provenance=None,
                    )
                )
            continue

        reg_obs_iso = _epoch_to_utc_iso8601(q.get("regularMarketTime"))
        pre_obs_iso = _epoch_to_utc_iso8601(q.get("preMarketTime"))
        post_obs_iso = _epoch_to_utc_iso8601(q.get("postMarketTime"))

        if MARKET_METRIC_QUOTE in metric_set:
            q_key = _metric_work_key(MARKET_METRIC_QUOTE, sym)
            selected_price, selected_source, raw_ts = select_quote_observation(q, clean_status)
            if selected_price is None or not math.isfinite(float(selected_price)) or selected_price <= 0:
                child_items.append(
                    ChildObservedResult(
                        key=q_key,
                        data=None,
                        obs_time=None,
                        error=f"No valid positive batch quote price for {sym}",
                        provenance=None,
                    )
                )
            else:
                obs_iso = _epoch_to_utc_iso8601(raw_ts)
                payload: Dict[str, Any] = {
                    "kind": "metric",
                    "metric": MARKET_METRIC_QUOTE,
                    "symbol": sym,
                    "status": clean_status,
                    "value": float(selected_price),
                    "from_batch": True,
                }
                if clean_status == "PRE-MARKET":
                    pre_p = to_float(q.get("preMarketPrice"))
                    if pre_p is not None and pre_p > 0 and pre_obs_iso is not None:
                        payload["pre_market_price"] = float(pre_p)
                        pre_chg_feed = to_float(q.get("preMarketChangePercent"))
                        if pre_chg_feed is not None and abs(pre_chg_feed) < 500:
                            payload["pre_market_change_pct_feed"] = float(pre_chg_feed)
                elif clean_status in ("AFTER-HOURS", "CLOSED"):
                    post_p = to_float(q.get("postMarketPrice"))
                    if post_p is not None and post_p > 0 and post_obs_iso is not None:
                        payload["after_hours_price"] = float(post_p)

                prov_q = MarketDataProvenance(
                    kind=ObservationKind.INSTANT,
                    source=selected_source or "BATCH_QUOTE",
                )
                child_items.append(
                    ChildObservedResult(
                        key=q_key,
                        data=payload,
                        obs_time=obs_iso,
                        error=None,
                        provenance=prov_q,
                    )
                )

        if MARKET_METRIC_SESSION_VOLUME in metric_set:
            sv_key = _metric_work_key(MARKET_METRIC_SESSION_VOLUME, sym)
            raw_sv = q.get("regularMarketVolume")
            sv_num = None if isinstance(raw_sv, bool) else to_float(raw_sv)
            if sv_num is not None and math.isfinite(sv_num) and sv_num >= 0 and reg_obs_iso is not None:
                child_items.append(
                    ChildObservedResult(
                        key=sv_key,
                        data={"kind": "metric", "metric": MARKET_METRIC_SESSION_VOLUME, "symbol": sym, "value": int(sv_num)},
                        obs_time=reg_obs_iso,
                        error=None,
                        provenance=MarketDataProvenance(ObservationKind.INSTANT, "BATCH_QUOTE"),
                    )
                )
            else:
                child_items.append(
                    ChildObservedResult(
                        key=sv_key,
                        data=None,
                        obs_time=None,
                        error=f"Missing or unprovenanced regularMarketVolume for {sym}",
                        provenance=None,
                    )
                )

        if MARKET_METRIC_PREMARKET_VOLUME in metric_set:
            pv_key = _metric_work_key(MARKET_METRIC_PREMARKET_VOLUME, sym)
            raw_pv = q.get("preMarketVolume")
            pv_num = None if isinstance(raw_pv, bool) else to_float(raw_pv)
            if pv_num is not None and math.isfinite(pv_num) and pv_num > 0 and pre_obs_iso is not None:
                child_items.append(
                    ChildObservedResult(
                        key=pv_key,
                        data={"kind": "metric", "metric": MARKET_METRIC_PREMARKET_VOLUME, "symbol": sym, "value": int(pv_num)},
                        obs_time=pre_obs_iso,
                        error=None,
                        provenance=MarketDataProvenance(ObservationKind.INSTANT, "BATCH_QUOTE_PREMARKET"),
                    )
                )
            else:
                child_items.append(
                    ChildObservedResult(
                        key=pv_key,
                        data=None,
                        obs_time=None,
                        error=f"Missing or unprovenanced preMarketVolume for {sym}",
                        provenance=None,
                    )
                )

        if MARKET_METRIC_AFTER_HOURS_VOLUME in metric_set:
            av_key = _metric_work_key(MARKET_METRIC_AFTER_HOURS_VOLUME, sym)
            raw_av = q.get("postMarketVolume")
            av_num = None if isinstance(raw_av, bool) else to_float(raw_av)
            if av_num is not None and math.isfinite(av_num) and av_num > 0 and post_obs_iso is not None:
                child_items.append(
                    ChildObservedResult(
                        key=av_key,
                        data={"kind": "metric", "metric": MARKET_METRIC_AFTER_HOURS_VOLUME, "symbol": sym, "value": int(av_num)},
                        obs_time=post_obs_iso,
                        error=None,
                        provenance=MarketDataProvenance(ObservationKind.INSTANT, "BATCH_QUOTE_POSTMARKET"),
                    )
                )
            else:
                child_items.append(
                    ChildObservedResult(
                        key=av_key,
                        data=None,
                        obs_time=None,
                        error=f"Missing or unprovenanced postMarketVolume for {sym}",
                        provenance=None,
                    )
                )

        for metric_name, field_name in (
            (MARKET_METRIC_DAY_OPEN, "regularMarketOpen"),
            (MARKET_METRIC_DAY_HIGH, "regularMarketDayHigh"),
            (MARKET_METRIC_DAY_LOW, "regularMarketDayLow"),
        ):
            if metric_name not in metric_set:
                continue
            m_key = _metric_work_key(metric_name, sym)
            val_num = to_float(q.get(field_name))
            if val_num is not None and math.isfinite(val_num) and val_num > 0 and reg_obs_iso is not None:
                child_items.append(
                    ChildObservedResult(
                        key=m_key,
                        data={
                            "kind": "metric",
                            "metric": metric_name,
                            "symbol": sym,
                            "status": clean_status,
                            "value": float(val_num),
                        },
                        obs_time=reg_obs_iso,
                        error=None,
                        provenance=MarketDataProvenance(ObservationKind.INSTANT, "BATCH_QUOTE"),
                    )
                )
            else:
                child_items.append(
                    ChildObservedResult(
                        key=m_key,
                        data=None,
                        obs_time=None,
                        error=f"Missing or unprovenanced {field_name} for {sym}",
                        provenance=None,
                    )
                )

    return BatchObservedResult(items=tuple(child_items))


def _acquire_symbol_fallback_raw(
    symbol: str,
    status: str,
    need_price: bool = True,
    fetch_chart: bool = True,
    fetch_premarket_vol: bool = True,
    include_finnhub: bool = True,
    include_fast_info: bool = False,
    include_polygon_vol: bool = False,
    include_prev_close: bool = False,
    include_chart_open: bool = False,
    requested_metrics: Optional[Tuple[str, ...]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> BatchObservedResult:
    """
    Worker-thread per-symbol fallback/supplemental market-data acquisition.
    Emits independent provenance-verified ChildObservedResult items per requested metric
    (INSTANT with strict UTC obs_time, SESSION_REFERENCE with effective_session_date).
    Strict fail-closed policy: fast_info lacks verifiable provenance and is never published;
    timestamps are never borrowed across providers or responses.
    """
    if cancel_check and cancel_check():
        raise RuntimeError("Operation cancelled before fallback acquisition")

    sym_clean = str(symbol).strip().upper()
    clean_status = str(status or "OPEN").strip().upper()

    if requested_metrics is not None:
        target_metrics = _normalize_requested_metrics(requested_metrics, default=())
    else:
        inferred = []
        if need_price:
            inferred.append(MARKET_METRIC_QUOTE)
        if fetch_chart:
            inferred.append(MARKET_METRIC_SESSION_VOLUME)
            if clean_status in ("AFTER-HOURS", "CLOSED"):
                inferred.append(MARKET_METRIC_AFTER_HOURS_VOLUME)
        if fetch_premarket_vol:
            inferred.append(MARKET_METRIC_PREMARKET_VOLUME)
        if include_polygon_vol:
            inferred.append(MARKET_METRIC_PREVIOUS_SESSION_VOLUME)
        if include_prev_close:
            inferred.append(MARKET_METRIC_REGULAR_CLOSE)
        if include_chart_open:
            inferred.append(MARKET_METRIC_DAY_OPEN)
        target_metrics = tuple(sorted(set(inferred)))

    chart_obs: Dict[str, Any] = {
        "price": None,
        "session_volume": None,
        "premarket_volume": None,
        "after_hours_volume": None,
        "chart_obs_utc_iso": None,
        "legacy_tuple": (None, 0, 0, 0, None),
    }
    chart_retrieval_time: Optional[float] = None
    if fetch_chart:
        chart_obs = _acquire_live_chart_observations_raw(sym_clean, clean_status)
        if any(
            chart_obs.get(k) is not None
            for k in ("price", "session_volume", "premarket_volume", "after_hours_volume")
        ):
            chart_retrieval_time = t_time.time()

    if cancel_check and cancel_check():
        raise RuntimeError("Operation cancelled during fallback acquisition")

    premarket_vol_obs: Optional[PremarketVolumeObservation] = None
    premarket_retrieval_time: Optional[float] = None
    if fetch_premarket_vol or MARKET_METRIC_PREMARKET_VOLUME in target_metrics:
        if fetch_premarket_vol:
            pm_res = get_premarket_volume_chart(sym_clean)
            if isinstance(pm_res, PremarketVolumeObservation):
                premarket_vol_obs = pm_res
                premarket_retrieval_time = t_time.time()

    if cancel_check and cancel_check():
        raise RuntimeError("Operation cancelled during fallback acquisition")

    fh_price_obs: Optional[TimestampedValue] = None
    chart_price_obs: Optional[TimestampedValue] = chart_obs.get("price")
    if include_finnhub and need_price and chart_price_obs is None and USE_FINNHUB:
        fh_price_obs = _acquire_finnhub_quote_raw(sym_clean)

    if cancel_check and cancel_check():
        raise RuntimeError("Operation cancelled during fallback acquisition")

    # Owner-approved strict fail-closed fast_info policy: never acquire or publish any
    # fast_info field because fast_info provides no independently verifiable provenance.

    polygon_prev_obs: Optional[SessionReferenceValue] = None
    if include_polygon_vol and USE_POLYGON:
        polygon_prev_obs = _acquire_polygon_previous_agg_raw(sym_clean)

    prev_close_obs: Optional[SessionReferenceValue] = None
    if include_prev_close:
        prev_close_obs = _acquire_previous_close_raw(sym_clean)

    chart_open_obs: Optional[TimestampedValue] = None
    if include_chart_open:
        chart_open_obs = _acquire_chart_open_raw(sym_clean)

    child_items: List[ChildObservedResult] = []
    chart_cache_meta = None
    if (
        fetch_chart
        and chart_retrieval_time is not None
        and any(
            chart_obs.get(k) is not None
            for k in ("price", "session_volume", "premarket_volume", "after_hours_volume")
        )
    ):
        raw_tup = chart_obs.get("legacy_tuple")
        if isinstance(raw_tup, tuple) and len(raw_tup) >= 4:
            chart_4_tuple = (raw_tup[0], raw_tup[1], raw_tup[2], raw_tup[3])
        else:
            p_val = chart_obs["price"].value if chart_obs.get("price") is not None else None
            sv_val = int(chart_obs["session_volume"].value) if chart_obs.get("session_volume") is not None else 0
            pm_val = int(chart_obs["premarket_volume"].value) if chart_obs.get("premarket_volume") is not None else 0
            ah_val = int(chart_obs["after_hours_volume"].value) if chart_obs.get("after_hours_volume") is not None else 0
            chart_4_tuple = (p_val, sv_val, pm_val, ah_val)

        chart_cache_meta = {
            "tuple": chart_4_tuple,
            "ts": float(chart_retrieval_time),
        }

    premarket_vol_cache_meta = None
    if premarket_vol_obs is not None and premarket_retrieval_time is not None:
        premarket_vol_cache_meta = {
            "value": int(premarket_vol_obs.value),
            "ts": float(premarket_retrieval_time),
        }

    if MARKET_METRIC_QUOTE in target_metrics:
        q_key = _metric_work_key(MARKET_METRIC_QUOTE, sym_clean)
        chosen_price_obs = chart_price_obs if chart_price_obs is not None else fh_price_obs
        if chosen_price_obs is not None and chosen_price_obs.value > 0:
            q_payload: Dict[str, Any] = {
                "kind": "metric",
                "metric": MARKET_METRIC_QUOTE,
                "symbol": sym_clean,
                "status": clean_status,
                "value": float(chosen_price_obs.value),
                "from_batch": False,
            }
            if clean_status == "PRE-MARKET":
                q_payload["pre_market_price"] = float(chosen_price_obs.value)
            elif clean_status in ("AFTER-HOURS", "CLOSED"):
                q_payload["after_hours_price"] = float(chosen_price_obs.value)
            if chart_cache_meta is not None and chosen_price_obs is chart_price_obs:
                q_payload["chart_cache_meta"] = chart_cache_meta
            child_items.append(
                ChildObservedResult(
                    key=q_key,
                    data=q_payload,
                    obs_time=chosen_price_obs.obs_time,
                    error=None,
                    provenance=MarketDataProvenance(ObservationKind.INSTANT, chosen_price_obs.source),
                )
            )
        else:
            child_items.append(
                ChildObservedResult(
                    key=q_key,
                    data=None,
                    obs_time=None,
                    error=f"No valid provenance-backed fallback price for {sym_clean}",
                    provenance=None,
                )
            )

    if MARKET_METRIC_SESSION_VOLUME in target_metrics:
        sv_key = _metric_work_key(MARKET_METRIC_SESSION_VOLUME, sym_clean)
        sv_obs: Optional[TimestampedValue] = chart_obs.get("session_volume")
        if sv_obs is not None:
            sv_payload: Dict[str, Any] = {
                "kind": "metric",
                "metric": MARKET_METRIC_SESSION_VOLUME,
                "symbol": sym_clean,
                "value": int(sv_obs.value),
            }
            if chart_cache_meta is not None:
                sv_payload["chart_cache_meta"] = chart_cache_meta
            child_items.append(
                ChildObservedResult(
                    key=sv_key,
                    data=sv_payload,
                    obs_time=sv_obs.obs_time,
                    error=None,
                    provenance=MarketDataProvenance(ObservationKind.INSTANT, sv_obs.source),
                )
            )
        else:
            child_items.append(
                ChildObservedResult(
                    key=sv_key,
                    data=None,
                    obs_time=None,
                    error=f"No valid provenance-backed session volume for {sym_clean}",
                    provenance=None,
                )
            )

    if MARKET_METRIC_PREMARKET_VOLUME in target_metrics:
        pv_key = _metric_work_key(MARKET_METRIC_PREMARKET_VOLUME, sym_clean)
        if premarket_vol_obs is not None:
            pv_payload: Dict[str, Any] = {
                "kind": "metric",
                "metric": MARKET_METRIC_PREMARKET_VOLUME,
                "symbol": sym_clean,
                "value": int(premarket_vol_obs.value),
                "effective_session_date": premarket_vol_obs.effective_session_date,
            }
            if premarket_vol_cache_meta is not None:
                pv_payload["premarket_vol_cache_meta"] = premarket_vol_cache_meta
            child_items.append(
                ChildObservedResult(
                    key=pv_key,
                    data=pv_payload,
                    obs_time=premarket_vol_obs.obs_time,
                    error=None,
                    provenance=MarketDataProvenance(ObservationKind.INSTANT, premarket_vol_obs.source),
                )
            )
        else:
            chart_pre_obs: Optional[TimestampedValue] = chart_obs.get("premarket_volume")
            if chart_pre_obs is not None:
                cpv_payload: Dict[str, Any] = {
                    "kind": "metric",
                    "metric": MARKET_METRIC_PREMARKET_VOLUME,
                    "symbol": sym_clean,
                    "value": int(chart_pre_obs.value),
                }
                if chart_cache_meta is not None:
                    cpv_payload["chart_cache_meta"] = chart_cache_meta
                child_items.append(
                    ChildObservedResult(
                        key=pv_key,
                        data=cpv_payload,
                        obs_time=chart_pre_obs.obs_time,
                        error=None,
                        provenance=MarketDataProvenance(ObservationKind.INSTANT, chart_pre_obs.source),
                    )
                )
            else:
                child_items.append(
                    ChildObservedResult(
                        key=pv_key,
                        data=None,
                        obs_time=None,
                        error=f"No valid provenance-backed premarket volume for {sym_clean}",
                        provenance=None,
                    )
                )

    if MARKET_METRIC_AFTER_HOURS_VOLUME in target_metrics:
        av_key = _metric_work_key(MARKET_METRIC_AFTER_HOURS_VOLUME, sym_clean)
        post_obs: Optional[TimestampedValue] = chart_obs.get("after_hours_volume")
        if post_obs is not None:
            av_payload: Dict[str, Any] = {
                "kind": "metric",
                "metric": MARKET_METRIC_AFTER_HOURS_VOLUME,
                "symbol": sym_clean,
                "value": int(post_obs.value),
            }
            if chart_cache_meta is not None:
                av_payload["chart_cache_meta"] = chart_cache_meta
            child_items.append(
                ChildObservedResult(
                    key=av_key,
                    data=av_payload,
                    obs_time=post_obs.obs_time,
                    error=None,
                    provenance=MarketDataProvenance(ObservationKind.INSTANT, post_obs.source),
                )
            )
        else:
            child_items.append(
                ChildObservedResult(
                    key=av_key,
                    data=None,
                    obs_time=None,
                    error=f"No valid provenance-backed after-hours volume for {sym_clean}",
                    provenance=None,
                )
            )

    if MARKET_METRIC_DAY_OPEN in target_metrics:
        do_key = _metric_work_key(MARKET_METRIC_DAY_OPEN, sym_clean)
        if chart_open_obs is not None:
            child_items.append(
                ChildObservedResult(
                    key=do_key,
                    data={
                        "kind": "metric",
                        "metric": MARKET_METRIC_DAY_OPEN,
                        "symbol": sym_clean,
                        "status": clean_status,
                        "value": float(chart_open_obs.value),
                    },
                    obs_time=chart_open_obs.obs_time,
                    error=None,
                    provenance=MarketDataProvenance(ObservationKind.INSTANT, chart_open_obs.source),
                )
            )
        else:
            child_items.append(
                ChildObservedResult(
                    key=do_key,
                    data=None,
                    obs_time=None,
                    error=f"No valid provenance-backed opening price for {sym_clean}",
                    provenance=None,
                )
            )

    if MARKET_METRIC_REGULAR_CLOSE in target_metrics:
        rc_key = _metric_work_key(MARKET_METRIC_REGULAR_CLOSE, sym_clean)
        if prev_close_obs is not None:
            child_items.append(
                ChildObservedResult(
                    key=rc_key,
                    data={
                        "kind": "metric",
                        "metric": MARKET_METRIC_REGULAR_CLOSE,
                        "symbol": sym_clean,
                        "status": clean_status,
                        "value": float(prev_close_obs.value),
                    },
                    obs_time=None,
                    error=None,
                    provenance=MarketDataProvenance(
                        kind=ObservationKind.SESSION_REFERENCE,
                        source=prev_close_obs.source,
                        effective_session_date=prev_close_obs.effective_session_date,
                    ),
                )
            )
        else:
            child_items.append(
                ChildObservedResult(
                    key=rc_key,
                    data=None,
                    obs_time=None,
                    error=f"No valid daily-bar previous close for {sym_clean}",
                    provenance=None,
                )
            )

    if MARKET_METRIC_PREVIOUS_SESSION_VOLUME in target_metrics:
        pv_agg_key = _metric_work_key(MARKET_METRIC_PREVIOUS_SESSION_VOLUME, sym_clean)
        if polygon_prev_obs is not None:
            child_items.append(
                ChildObservedResult(
                    key=pv_agg_key,
                    data={
                        "kind": "metric",
                        "metric": MARKET_METRIC_PREVIOUS_SESSION_VOLUME,
                        "symbol": sym_clean,
                        "value": int(polygon_prev_obs.value),
                    },
                    obs_time=None,
                    error=None,
                    provenance=MarketDataProvenance(
                        kind=ObservationKind.SESSION_REFERENCE,
                        source=polygon_prev_obs.source,
                        effective_session_date=polygon_prev_obs.effective_session_date,
                    ),
                )
            )
        else:
            child_items.append(
                ChildObservedResult(
                    key=pv_agg_key,
                    data=None,
                    obs_time=None,
                    error=f"No valid Polygon previous-session aggregate for {sym_clean}",
                    provenance=None,
                )
            )

    return BatchObservedResult(items=tuple(child_items))


def _acquire_vwap_raw(
    symbol: str,
    status: str,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> ObservedResult:
    """
    Worker-thread intraday VWAP data acquisition.
    Preserves get_true_intraday_vwap -> fallback_vwap precedence without mutating shared cache.
    Carries explicit MarketDataProvenance with the timestamp of the latest included valid bar.
    """
    if cancel_check and cancel_check():
        raise RuntimeError("Operation cancelled before VWAP acquisition")

    try:
        valid, obs_utc_iso, before_anchor = _acquire_true_intraday_vwap_bars(symbol, status)
        if not before_anchor and valid and obs_utc_iso is not None:
            total_v = sum(v for _, _, _, v in valid)
            if total_v > 0:
                valid_ts = validate_utc_iso8601_timestamp(obs_utc_iso)
                return ObservedResult(
                    data={"kind": "vwap", "symbol": symbol, "bars": valid, "source": "INTRADAY_VWAP"},
                    obs_time=valid_ts,
                    provenance=MarketDataProvenance(ObservationKind.INSTANT, "INTRADAY_VWAP"),
                )
    except Exception:
        pass

    if cancel_check and cancel_check():
        raise RuntimeError("Operation cancelled during VWAP acquisition")

    fb_payload, fb_obs_iso = _acquire_fallback_vwap_payload(symbol)
    if fb_payload is not None and fb_obs_iso is not None:
        try:
            valid_fb_ts = validate_utc_iso8601_timestamp(fb_obs_iso)
            fb_source = str(fb_payload.get("source") or "FALLBACK_VWAP").strip() or "FALLBACK_VWAP"
            return ObservedResult(
                data=fb_payload,
                obs_time=valid_fb_ts,
                provenance=MarketDataProvenance(ObservationKind.INSTANT, fb_source),
            )
        except Exception:
            pass

    return ObservedResult(data=None, obs_time=None, provenance=None)


def _build_quote_cache_candidate(
    symbol: str,
    status: str,
    quote_data: Optional[dict] = None,
    fast_info_data: Optional[dict] = None,
    chart_data: Optional[tuple] = None,
    chart_fetched: bool = False,
    premarket_vol_data: Optional[int] = None,
    premarket_vol_fetched: bool = False,
    finnhub_data: Optional[tuple] = None,
    polygon_vol_data: Optional[int] = None,
    alt_prev_close: Optional[float] = None,
    alt_chart_open: Optional[float] = None,
    obs_time_override: Optional[datetime] = None,
    allow_cache_previous: bool = False,
    supplement_only: bool = False,
    now_ny: Optional[datetime] = None,
) -> Tuple[Dict[str, Any], dict]:
    """
    Stages and validates a complete per-symbol MarketDataCache update without mutating
    shared cache dictionaries. Raises on invalid or incomplete market-data observations
    before any shared state changes occur.
    """
    ny_tz = ZoneInfo("America/New_York")
    if now_ny is None:
        now_ny = datetime.now(ny_tz)

    updates: Dict[str, Any] = {}

    def _staged(attr: str, default: Any = None) -> Any:
        if attr in updates:
            return updates[attr]
        return getattr(cache, attr).get(symbol, default)

    def _has_staged(attr: str) -> bool:
        return attr in updates or symbol in getattr(cache, attr)

    if supplement_only:
        now_ts = t_time.time()
        if chart_fetched and chart_data is not None:
            api_price, api_vol, api_pre_vol, api_post_vol, chart_obs_iso = chart_data
            if (api_price is not None and api_price > 0) or api_vol > 0 or api_pre_vol > 0 or api_post_vol > 0:
                updates["last_chart_fetch"] = now_ts
                updates["chart_cache"] = (api_price, api_vol, api_pre_vol, api_post_vol)
                if chart_obs_iso is not None:
                    try:
                        updates["chart_obs_time"] = parse_utc_iso8601_timestamp(chart_obs_iso).astimezone(ny_tz)
                    except Exception:
                        updates["chart_obs_time"] = None
            if api_vol > 0 and _staged("volumes", 0) == 0:
                updates["volumes"] = int(api_vol)
            if api_pre_vol > 0 and _staged("pre_market_volume", 0) == 0:
                updates["pre_market_volume"] = int(api_pre_vol)
            if api_post_vol > 0 and _staged("after_hours_volume", 0) == 0:
                updates["after_hours_volume"] = int(api_post_vol)

        if premarket_vol_fetched and premarket_vol_data is not None:
            pm_val = (
                premarket_vol_data.value
                if isinstance(premarket_vol_data, PremarketVolumeObservation)
                else premarket_vol_data
            )
            updates["last_premarket_vol_fetch"] = now_ts
            updates["premarket_vol_cache"] = int(pm_val or 0)
            if pm_val and pm_val > 0:
                updates["pre_market_volume"] = int(pm_val)

        if fast_info_data:
            fi_open = fast_info_data.get("open") or fast_info_data.get("regular_market_open")
            fi_high = fast_info_data.get("day_high") or fast_info_data.get("regular_market_day_high")
            fi_low = fast_info_data.get("day_low") or fast_info_data.get("regular_market_day_low")
            if fi_open and to_float(fi_open) is not None and to_float(fi_open) > 0 and not _has_staged("day_open"):
                updates["day_open"] = to_float(fi_open)
            if fi_high and to_float(fi_high) is not None and to_float(fi_high) > 0 and not _has_staged("day_high"):
                updates["day_high"] = to_float(fi_high)
            if fi_low and to_float(fi_low) is not None and to_float(fi_low) > 0 and not _has_staged("day_low"):
                updates["day_low"] = to_float(fi_low)
            if _staged("volumes", 0) == 0:
                v_last = fast_info_data.get("last_volume") or fast_info_data.get("three_month_average_volume") or 0
                if v_last:
                    try:
                        updates["volumes"] = int(v_last)
                    except Exception:
                        pass
            if not _has_staged("regular_close"):
                fi_rc = to_float(fast_info_data.get("regular_market_previous_close") or fast_info_data.get("previous_close"))
                if fi_rc is not None and fi_rc > 0:
                    updates["regular_close"] = fi_rc

        if polygon_vol_data and _staged("volumes", 0) == 0:
            try:
                updates["volumes"] = int(polygon_vol_data)
            except Exception:
                pass

        if alt_prev_close and to_float(alt_prev_close) is not None and to_float(alt_prev_close) > 0 and not _has_staged("regular_close"):
            updates["regular_close"] = to_float(alt_prev_close)

        if alt_chart_open and alt_chart_open > 0:
            reg_close = _staged("regular_close")
            if reg_close and reg_close > 0:
                updates["gaps"] = ((float(alt_chart_open) - reg_close) / reg_close) * 100

        return updates, {"needs_supplement": False}

    price = None
    vol = 0
    obs_time = obs_time_override
    price_source = None

    updates["session"] = status
    updates["session_liquidity"] = (
        "LOW" if status in ("AFTER-HOURS", "PRE-MARKET") else "HIGH"
    )

    pre_price = None
    post_price = None
    reg_price = None
    reg_open = None
    reg_high = None
    reg_low = None
    batch_prev_close = None
    pre_vol = 0
    post_vol = 0
    q_price = None
    q_source = None

    if quote_data:
        pre_price = to_float(quote_data.get('preMarketPrice'))
        post_price = to_float(quote_data.get('postMarketPrice'))
        reg_price = to_float(quote_data.get('regularMarketPrice'))
        reg_open = to_float(quote_data.get('regularMarketOpen'))
        reg_high = to_float(quote_data.get('regularMarketDayHigh'))
        reg_low = to_float(quote_data.get('regularMarketDayLow'))
        batch_prev_close = to_float(quote_data.get('regularMarketPreviousClose'))
        try:
            vol = int(quote_data.get('regularMarketVolume', 0) or 0)
        except Exception:
            vol = 0
        try:
            pre_vol = int(quote_data.get('preMarketVolume', 0) or 0)
        except Exception:
            pre_vol = 0
        try:
            post_vol = int(quote_data.get('postMarketVolume', 0) or 0)
        except Exception:
            post_vol = 0

        q_price, q_source, q_raw_ts = select_quote_observation(quote_data, status)
        if q_price is not None and q_price > 0:
            price = q_price
            price_source = q_source
            if obs_time is None and q_raw_ts is not None:
                q_obs_iso = _epoch_to_utc_iso8601(q_raw_ts)
                if q_obs_iso is not None:
                    try:
                        obs_time = parse_utc_iso8601_timestamp(q_obs_iso).astimezone(ny_tz)
                    except Exception:
                        obs_time = None

        if reg_open and reg_open > 0:
            updates["day_open"] = reg_open
        if reg_high and reg_high > 0:
            updates["day_high"] = reg_high
        if reg_low and reg_low > 0:
            updates["day_low"] = reg_low

    if fast_info_data:
        if not pre_price and fast_info_data.get('pre_market_price'):
            pre_price = to_float(fast_info_data.get('pre_market_price'))
        if not post_price and fast_info_data.get('post_market_price'):
            post_price = to_float(fast_info_data.get('post_market_price'))
        fi_open = fast_info_data.get('open') or fast_info_data.get('regular_market_open')
        fi_high = fast_info_data.get('day_high') or fast_info_data.get('regular_market_day_high')
        fi_low = fast_info_data.get('day_low') or fast_info_data.get('regular_market_day_low')
        if fi_open and to_float(fi_open) is not None and to_float(fi_open) > 0 and not _has_staged("day_open"):
            updates["day_open"] = to_float(fi_open)
        if fi_high and to_float(fi_high) is not None and to_float(fi_high) > 0 and not _has_staged("day_high"):
            updates["day_high"] = to_float(fi_high)
        if fi_low and to_float(fi_low) is not None and to_float(fi_low) > 0 and not _has_staged("day_low"):
            updates["day_low"] = to_float(fi_low)

    needs_pre_vol = (pre_vol == 0 and _staged("pre_market_volume", 0) == 0)
    needs_post_vol = (status in ("AFTER-HOURS", "CLOSED") and post_vol == 0)
    now_ts = t_time.time()

    # Chart fallback (from worker payload or valid in-memory TTL cache)
    if (price is None or price <= 0) or needs_post_vol:
        api_price = None
        api_vol = 0
        api_pre_vol = 0
        api_post_vol = 0
        chart_obs_dt = None
        if chart_data is not None:
            if len(chart_data) >= 5:
                api_price, api_vol, api_pre_vol, api_post_vol, chart_obs_raw = chart_data[:5]
                if isinstance(chart_obs_raw, datetime):
                    chart_obs_dt = chart_obs_raw
                elif isinstance(chart_obs_raw, str):
                    try:
                        chart_obs_dt = parse_utc_iso8601_timestamp(chart_obs_raw).astimezone(ny_tz)
                    except Exception:
                        chart_obs_dt = None
            else:
                api_price, api_vol, api_pre_vol, api_post_vol = chart_data[:4]
                chart_obs_dt = _staged("chart_obs_time")
            if chart_fetched and ((api_price is not None and api_price > 0) or api_vol > 0 or api_pre_vol > 0 or api_post_vol > 0):
                updates["last_chart_fetch"] = now_ts
                updates["chart_cache"] = (api_price, api_vol, api_pre_vol, api_post_vol)
                if chart_obs_dt is not None:
                    updates["chart_obs_time"] = chart_obs_dt
        elif symbol in cache.last_chart_fetch and (now_ts - cache.last_chart_fetch[symbol]) < 60:
            api_price, api_vol, api_pre_vol, api_post_vol = cache.chart_cache[symbol]
            chart_obs_dt = _staged("chart_obs_time")

        if (price is None or price <= 0) and api_price is not None and api_price > 0:
            price = api_price
            price_source = "INTRADAY_CHART"
            if obs_time is None:
                obs_time = chart_obs_dt if chart_obs_dt is not None else _staged("chart_obs_time")
        if vol == 0 and api_vol > 0:
            vol = api_vol
        if api_pre_vol > 0 and pre_vol == 0:
            pre_vol = api_pre_vol
        if api_post_vol > 0 and post_vol == 0:
            post_vol = api_post_vol

    # Premarket volume (from worker payload or valid in-memory TTL cache)
    if needs_pre_vol:
        chart_pre_vol = 0
        if premarket_vol_data is not None:
            chart_pre_vol = (
                premarket_vol_data.value
                if isinstance(premarket_vol_data, PremarketVolumeObservation)
                else premarket_vol_data
            )
            if premarket_vol_fetched:
                updates["last_premarket_vol_fetch"] = now_ts
                updates["premarket_vol_cache"] = int(chart_pre_vol or 0)
        elif symbol in cache.last_premarket_vol_fetch and (now_ts - cache.last_premarket_vol_fetch[symbol]) < 90:
            chart_pre_vol = cache.premarket_vol_cache.get(symbol, 0)
        if chart_pre_vol > 0:
            pre_vol = chart_pre_vol

    # Finnhub fallback
    if (price is None or price <= 0) and finnhub_data is not None:
        fh_c, fh_pc, fh_t = finnhub_data
        if fh_c and fh_c > 0:
            price = fh_c
            price_source = "FINNHUB"
            if obs_time is None:
                if fh_t:
                    try:
                        obs_time = datetime.fromtimestamp(int(fh_t), ZoneInfo("UTC")).astimezone(ny_tz)
                    except Exception:
                        obs_time = None
                else:
                    obs_time = None

    # fast_info volume and price fallback
    if fast_info_data:
        if vol == 0 or vol is None:
            v_last = fast_info_data.get('last_volume', 0)
            v_avg = fast_info_data.get('three_month_average_volume', 0)
            vol = (v_last if v_last is not None else 0) or (v_avg if v_avg is not None else 0) or 0
        if price is None or price <= 0:
            if status == "PRE-MARKET" and fast_info_data.get('pre_market_price'):
                fi_p = to_float(fast_info_data.get('pre_market_price'))
                if fi_p and fi_p > 0:
                    price = fi_p
                    price_source = "FAST_INFO"
                    obs_time = None
            elif status in ("AFTER-HOURS", "CLOSED") and fast_info_data.get('post_market_price'):
                fi_p = to_float(fast_info_data.get('post_market_price'))
                if fi_p and fi_p > 0:
                    price = fi_p
                    price_source = "FAST_INFO"
                    obs_time = None
            elif fast_info_data.get('last_price'):
                fi_p = to_float(fast_info_data.get('last_price'))
                if fi_p and fi_p > 0:
                    price = fi_p
                    price_source = "FAST_INFO"
                    obs_time = None

    if (vol == 0 or vol is None) and polygon_vol_data:
        vol = polygon_vol_data

    if vol is None:
        vol = 0
    else:
        try:
            vol = int(vol)
        except Exception:
            vol = 0

    if not allow_cache_previous:
        if price is None or not isinstance(price, (int, float)) or not math.isfinite(float(price)) or price <= 0:
            raise ValueError(f"No valid positive price in market-data result for {symbol}")
        if obs_time is None:
            raise ValueError(f"Missing required source observation timestamp for {symbol}")

    if allow_cache_previous and (price is None or price <= 0) and symbol in cache.prices and cache.prices[symbol] > 0:
        price = cache.prices[symbol]
        price_source = "CACHE_PREVIOUS"
        prev_obs_str = cache.observation_times.get(symbol)
        if prev_obs_str:
            try:
                obs_time = datetime.fromisoformat(prev_obs_str)
            except Exception:
                obs_time = None

    validity, freshness = determine_market_freshness(
        price=price,
        obs_time=obs_time,
        current_ny=now_ny,
        session_status=status,
        source=price_source
    )
    updates["price_sources"] = price_source
    updates["observation_times"] = obs_time.isoformat() if obs_time else None
    updates["retrieval_times"] = now_ny.isoformat()
    updates["price_validity"] = validity
    updates["freshness"] = freshness

    if vol > 0:
        updates["volumes"] = vol
    if pre_vol > 0:
        updates["pre_market_volume"] = pre_vol
    if post_vol > 0:
        updates["after_hours_volume"] = post_vol

    need_chart_open = False
    need_prev_close = False
    if price is not None and price > 0:
        updates["prices"] = price

        if status == "PRE-MARKET" and (pre_price is None or pre_price == 0):
            pre_price = price
        elif status in ("AFTER-HOURS", "CLOSED") and (post_price is None or post_price == 0):
            post_price = price

        reg_close = None
        techs = cache.technicals.get(symbol, {})
        if status == "PRE-MARKET":
            if reg_price and reg_price > 0:
                reg_close = reg_price
            elif to_float(techs.get("Last_Reg_Close")) is not None and to_float(techs.get("Last_Reg_Close")) > 0:
                reg_close = to_float(techs.get("Last_Reg_Close"))
            elif fast_info_data and fast_info_data.get('last_price'):
                fi_last = to_float(fast_info_data.get('last_price'))
                if fi_last is not None and fi_last > 0:
                    reg_close = fi_last
            elif batch_prev_close and batch_prev_close > 0:
                reg_close = batch_prev_close
        else:
            if batch_prev_close and batch_prev_close > 0:
                reg_close = batch_prev_close
            elif fast_info_data and (fast_info_data.get('regular_market_previous_close') or fast_info_data.get('previous_close')):
                reg_close = to_float(fast_info_data.get('regular_market_previous_close') or fast_info_data.get('previous_close'))
            elif to_float(techs.get("Last_Reg_Close")) is not None and to_float(techs.get("Last_Reg_Close")) > 0:
                reg_close = to_float(techs.get("Last_Reg_Close"))
            elif alt_prev_close and to_float(alt_prev_close) is not None and to_float(alt_prev_close) > 0:
                reg_close = to_float(alt_prev_close)
            else:
                need_prev_close = True

        if reg_close is not None and reg_close > 0:
            updates["regular_close"] = reg_close

        if pre_price is not None and pre_price > 0:
            updates["pre_market_price"] = pre_price
        elif status == "PRE-MARKET":
            updates["pre_market_price"] = reg_close if (reg_close and reg_close > 0) else price

        if reg_close is not None and reg_close > 0:
            if status == "PRE-MARKET":
                effective_pre = float(_staged("pre_market_price", price))
                pre_chg_pct_feed = quote_data.get('preMarketChangePercent') if quote_data else None
                pre_chg_feed_val = to_float(pre_chg_pct_feed)
                if pre_chg_feed_val is not None and abs(pre_chg_feed_val) < 500:
                    pre_chg = pre_chg_feed_val
                else:
                    pre_chg = ((effective_pre - reg_close) / reg_close) * 100 if effective_pre > 0 else 0.0
                updates["session_change"] = pre_chg
                updates["gaps"] = pre_chg
                updates["pre_market_change"] = pre_chg
            else:
                session_chg = ((price - reg_close) / reg_close) * 100
                updates["session_change"] = session_chg
                true_gap_price = price
                if status in ("OPEN", "CLOSED") and reg_open:
                    true_gap_price = float(reg_open)

                raw_gap = ((true_gap_price - reg_close) / reg_close) * 100

                if status == "OPEN" and reg_open and abs(raw_gap) > 3.0:
                    if (raw_gap < -3.0 and session_chg > 0) or (raw_gap > 3.0 and session_chg < 0):
                        if alt_chart_open and alt_chart_open > 0:
                            true_gap_price = alt_chart_open
                            raw_gap = ((true_gap_price - reg_close) / reg_close) * 100
                        else:
                            need_chart_open = True

                updates["gaps"] = raw_gap

            if reg_low and reg_close > 0:
                drop_from_close = ((float(reg_low) - reg_close) / reg_close) * 100
                if drop_from_close <= -10.0:
                    updates["ssr_active"] = True
                else:
                    updates["ssr_active"] = _staged("ssr_active", False)

        if post_price is not None:
            updates["after_hours_price"] = post_price
            if reg_close and reg_close > 0:
                updates["after_hours_change"] = ((post_price - reg_close) / reg_close) * 100

        if pre_price is not None and post_price is not None and post_price > 0:
            updates["overnight_return"] = ((pre_price - post_price) / post_price) * 100

    still_needs_pre_vol = (pre_vol == 0 and _staged("pre_market_volume", 0) == 0)
    still_needs_post_vol = (status in ("AFTER-HOURS", "CLOSED") and post_vol == 0 and _staged("after_hours_volume", 0) == 0)
    meta = {
        "needs_supplement": bool(still_needs_pre_vol or still_needs_post_vol or need_prev_close or need_chart_open or not _has_staged("day_open")),
        "needs_pre_vol": still_needs_pre_vol,
        "needs_post_vol": still_needs_post_vol,
        "need_prev_close": need_prev_close,
        "need_chart_open": need_chart_open,
        "need_fast_info": not _has_staged("day_open") or not _has_staged("day_high") or not _has_staged("day_low"),
    }
    return updates, meta


def _publish_market_data_candidate(
    symbol: str,
    candidate: Dict[str, Any],
    provenance: Optional[Tuple[str, Dict[str, Any]]] = None,
) -> None:
    """
    Commits a fully validated per-symbol candidate dictionary and optional provenance entry
    to MarketDataCache as an all-or-nothing transaction on the coordinating thread.
    If any assignment fails, all mutations are rolled back to their exact prior state.
    """
    if not isinstance(candidate, dict):
        raise ValueError(f"Invalid cache candidate for {symbol}: {type(candidate)}")
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError(f"Invalid symbol for cache publication: {symbol!r}")

    sym = symbol.strip().upper()

    # 1. Validate every target mapping before beginning publication
    target_mappings: List[Tuple[str, dict, Any]] = []
    for attr, val in candidate.items():
        mapping = getattr(cache, attr, None)
        if not isinstance(mapping, dict):
            raise ValueError(f"Invalid cache field in candidate for {sym}: {attr}")
        target_mappings.append((attr, mapping, val))

    prov_mapping: Optional[dict] = None
    metric_name: Optional[str] = None
    prov_entry: Optional[Dict[str, Any]] = None
    if provenance is not None:
        if not (isinstance(provenance, tuple) and len(provenance) == 2):
            raise ValueError(f"Invalid provenance specification for {sym}: {provenance!r}")
        metric_name, prov_entry = provenance
        if not isinstance(metric_name, str) or not metric_name.strip():
            raise ValueError(f"Invalid provenance metric name for {sym}: {metric_name!r}")
        if not isinstance(prov_entry, dict):
            raise ValueError(f"Invalid provenance entry for {sym} {metric_name}: {type(prov_entry)}")
        prov_mapping = getattr(cache, "metric_provenance", None)
        if not isinstance(prov_mapping, dict):
            raise ValueError(f"Invalid metric_provenance mapping on cache for {sym}")
        nested_prov = prov_mapping.get(sym)
        if nested_prov is not None and not isinstance(nested_prov, dict):
            raise ValueError(f"Invalid nested metric_provenance mapping for {sym}: {type(nested_prov)}")

    # 2. Capture prior state of every affected (mapping, symbol) entry
    cache_snapshots: List[Tuple[str, dict, str, bool, Any, Any]] = []
    for attr, mapping, new_val in target_mappings:
        existed = sym in mapping
        old_val = mapping.get(sym)
        cache_snapshots.append((attr, mapping, sym, existed, old_val, new_val))

    # 3. Capture prior state of provenance entry
    prov_snapshot = None
    if provenance is not None and prov_mapping is not None and metric_name is not None and prov_entry is not None:
        sym_in_prov = sym in prov_mapping
        nested = prov_mapping.get(sym)
        metric_in_prov = False
        old_prov_val = None
        if sym_in_prov and isinstance(nested, dict):
            metric_in_prov = metric_name in nested
            old_prov_val = nested.get(metric_name)
        prov_snapshot = (prov_mapping, sym, metric_name, sym_in_prov, metric_in_prov, old_prov_val, prov_entry)

    # 4. Apply all candidate cache values and provenance as one logical publication operation
    applied_cache_snapshots: List[Tuple[str, dict, str, bool, Any]] = []
    prov_attempted = False
    prov_created_sym = False
    try:
        for attr, mapping, key, existed, old_val, new_val in cache_snapshots:
            # Register the attempted write before invoking potentially failing assignment.
            applied_cache_snapshots.append((attr, mapping, key, existed, old_val))
            mapping[key] = new_val

        if prov_snapshot is not None:
            p_map, p_sym, p_metric, p_sym_in, p_metric_in, p_old_val, p_new_val = prov_snapshot
            if not p_sym_in:
                p_map[p_sym] = {}
                prov_created_sym = True
            # Mark the write as attempted before assignment. An assignment may mutate
            # the mapping and subsequently raise.
            prov_attempted = True
            p_map[p_sym][p_metric] = p_new_val

    except BaseException as primary_error:
        # 5-7. Rollback every mutation in reverse order of mutation
        rollback_errors: List[Tuple[str, BaseException]] = []

        if prov_snapshot is not None:
            p_map, p_sym, p_metric, p_sym_in, p_metric_in, p_old_val, p_new_val = prov_snapshot
            try:
                if prov_created_sym or (not p_sym_in and p_sym in p_map):
                    p_map.pop(p_sym, None)
                elif prov_attempted:
                    if p_metric_in:
                        p_map[p_sym][p_metric] = p_old_val
                    else:
                        p_map[p_sym].pop(p_metric, None)
            except BaseException as rb_err:
                rollback_errors.append((f"metric_provenance[{p_sym}][{p_metric}]", rb_err))

        for attr, mapping, key, existed, old_val in reversed(applied_cache_snapshots):
            try:
                if existed:
                    mapping[key] = old_val
                else:
                    mapping.pop(key, None)
            except BaseException as rb_err:
                rollback_errors.append((f"{attr}[{key}]", rb_err))

        # 8. Preserve and re-raise original publication exception.
        # Retain diagnostic evidence of both primary and rollback errors.
        if rollback_errors:
            for target_desc, rb_err in rollback_errors:
                msg = f"Rollback failure for {target_desc}: {rb_err!r}"
                if hasattr(primary_error, "add_note"):
                    primary_error.add_note(msg)
                else:
                    print(f"[MarketDataCache Warning] {msg}")
        raise


def _publish_quote_cache_candidate(symbol: str, candidate: Dict[str, Any]) -> None:
    """
    Commits a fully validated per-symbol candidate dictionary to MarketDataCache in one step
    with atomic all-or-nothing rollback on the coordinating thread.
    """
    _publish_market_data_candidate(symbol=symbol, candidate=candidate, provenance=None)


def _apply_quote_and_fallback_to_cache(
    symbol: str,
    status: str,
    quote_data: Optional[dict] = None,
    fast_info_data: Optional[dict] = None,
    chart_data: Optional[tuple] = None,
    chart_fetched: bool = False,
    premarket_vol_data: Optional[int] = None,
    premarket_vol_fetched: bool = False,
    finnhub_data: Optional[tuple] = None,
    polygon_vol_data: Optional[int] = None,
    alt_prev_close: Optional[float] = None,
    alt_chart_open: Optional[float] = None,
    obs_time_override: Optional[datetime] = None,
    allow_cache_previous: bool = False,
    supplement_only: bool = False,
    now_ny: Optional[datetime] = None,
) -> dict:
    """
    Coordinating-thread atomic cache application for quote, fallback, and freshness evaluation.
    Builds and validates the complete candidate before committing any per-symbol cache field.
    """
    candidate, meta = _build_quote_cache_candidate(
        symbol=symbol,
        status=status,
        quote_data=quote_data,
        fast_info_data=fast_info_data,
        chart_data=chart_data,
        chart_fetched=chart_fetched,
        premarket_vol_data=premarket_vol_data,
        premarket_vol_fetched=premarket_vol_fetched,
        finnhub_data=finnhub_data,
        polygon_vol_data=polygon_vol_data,
        alt_prev_close=alt_prev_close,
        alt_chart_open=alt_chart_open,
        obs_time_override=obs_time_override,
        allow_cache_previous=allow_cache_previous,
        supplement_only=supplement_only,
        now_ny=now_ny,
    )
    _publish_quote_cache_candidate(symbol, candidate)
    return meta


def update_price_tick(symbol, t_obj, status, quote_data=None):
    """
    Synchronous compatibility entrypoint for direct callers/tests.
    The recurring monitoring loop uses MARKET_DATA_COORDINATOR and coordinate_market_data_cycle instead.
    """
    fast_info_data = _extract_fast_info_snapshot(t_obj)

    pre_price = to_float(quote_data.get('preMarketPrice')) if quote_data else None
    post_price = to_float(quote_data.get('postMarketPrice')) if quote_data else None
    reg_price = to_float(quote_data.get('regularMarketPrice')) if quote_data else None
    reg_open = to_float(quote_data.get('regularMarketOpen')) if quote_data else None
    batch_prev_close = to_float(quote_data.get('regularMarketPreviousClose')) if quote_data else None
    vol = int(quote_data.get('regularMarketVolume', 0) or 0) if quote_data else 0
    pre_vol = int(quote_data.get('preMarketVolume', 0) or 0) if quote_data else 0
    post_vol = int(quote_data.get('postMarketVolume', 0) or 0) if quote_data else 0

    if not pre_price and fast_info_data.get('pre_market_price'):
        pre_price = to_float(fast_info_data.get('pre_market_price'))
    if not post_price and fast_info_data.get('post_market_price'):
        post_price = to_float(fast_info_data.get('post_market_price'))

    prelim_price = None
    if status == "PRE-MARKET" and pre_price and pre_price > 0:
        prelim_price = pre_price
    elif status in ("AFTER-HOURS", "CLOSED") and post_price and post_price > 0:
        prelim_price = post_price
    elif reg_price and reg_price > 0:
        prelim_price = reg_price

    needs_pre_vol = (pre_vol == 0 and cache.pre_market_volume.get(symbol, 0) == 0)
    needs_post_vol = (status in ("AFTER-HOURS", "CLOSED") and post_vol == 0)
    now_ts = t_time.time()

    chart_data = None
    chart_fetched = False
    api_price = None
    if (prelim_price is None or prelim_price <= 0) or needs_post_vol:
        if symbol in cache.last_chart_fetch and (now_ts - cache.last_chart_fetch[symbol]) < 60:
            chart_data = cache.chart_cache[symbol]
            api_price = chart_data[0]
        else:
            api_price, api_vol, api_pre_vol, api_post_vol = get_live_chart_data(symbol, status)
            chart_data = (api_price, api_vol, api_pre_vol, api_post_vol)
            chart_fetched = True
            if api_vol > 0 and vol == 0:
                vol = api_vol

    premarket_vol_data = None
    premarket_vol_fetched = False
    if needs_pre_vol:
        if symbol in cache.last_premarket_vol_fetch and (now_ts - cache.last_premarket_vol_fetch[symbol]) < 90:
            premarket_vol_data = cache.premarket_vol_cache.get(symbol, 0)
        else:
            raw_pm = get_premarket_volume_chart(symbol)
            if isinstance(raw_pm, PremarketVolumeObservation):
                premarket_vol_data = raw_pm.value
            elif isinstance(raw_pm, (int, float)) and not isinstance(raw_pm, bool):
                premarket_vol_data = int(raw_pm)
            else:
                premarket_vol_data = 0
            premarket_vol_fetched = True

    finnhub_data = None
    has_chart_price = api_price is not None and api_price > 0
    if (prelim_price is None or prelim_price <= 0) and not has_chart_price and USE_FINNHUB:
        fh_c, fh_pc, fh_t = get_finnhub_quote(symbol)
        if fh_c and fh_c > 0:
            finnhub_data = (fh_c, fh_pc, fh_t)

    fi_vol = fast_info_data.get('last_volume') or fast_info_data.get('three_month_average_volume') or 0
    polygon_vol_data = None
    if (vol == 0 or vol is None) and not fi_vol:
        polygon_vol_data = polygon_volume(symbol)

    techs = cache.technicals.get(symbol, {})
    has_reg_close = bool(
        (batch_prev_close and batch_prev_close > 0)
        or (fast_info_data.get('regular_market_previous_close') or fast_info_data.get('previous_close'))
        or (to_float(techs.get("Last_Reg_Close")) is not None and to_float(techs.get("Last_Reg_Close")) > 0)
    )
    alt_prev_close = None
    if status != "PRE-MARKET" and not has_reg_close:
        alt_prev_close = get_previous_close(symbol)

    alt_chart_open = None
    eff_reg_close = batch_prev_close or to_float(techs.get("Last_Reg_Close")) or to_float(alt_prev_close)
    eff_price = prelim_price or api_price or (finnhub_data[0] if finnhub_data else None) or to_float(fast_info_data.get('last_price'))
    if status == "OPEN" and reg_open and eff_price and eff_reg_close and eff_reg_close > 0:
        raw_gap = ((float(reg_open) - eff_reg_close) / eff_reg_close) * 100
        session_chg = ((eff_price - eff_reg_close) / eff_reg_close) * 100
        if abs(raw_gap) > 3.0 and ((raw_gap < -3.0 and session_chg > 0) or (raw_gap > 3.0 and session_chg < 0)):
            alt_chart_open = _get_chart_open(symbol)

    _apply_quote_and_fallback_to_cache(
        symbol=symbol,
        status=status,
        quote_data=quote_data,
        fast_info_data=fast_info_data,
        chart_data=chart_data,
        chart_fetched=chart_fetched,
        premarket_vol_data=premarket_vol_data,
        premarket_vol_fetched=premarket_vol_fetched,
        finnhub_data=finnhub_data,
        polygon_vol_data=polygon_vol_data,
        alt_prev_close=alt_prev_close,
        alt_chart_open=alt_chart_open,
        allow_cache_previous=True,
    )


def _build_metric_cache_candidate(
    res: WorkResult,
    now_ny: Optional[datetime] = None,
) -> Tuple[str, str, Dict[str, Any], Dict[str, Any]]:
    """
    Validates a single provenance-bearing market-data WorkResult and stages its
    independent per-symbol cache updates without mutating shared MarketDataCache.
    Enforces strict fail-closed rejection of fast_info and unprovenanced payloads.
    Returns (symbol, metric_name, cache_updates_dict, provenance_entry_dict).
    """
    if not isinstance(res.data, dict):
        raise ValueError(f"Malformed market-data payload for {res.key}")

    if not isinstance(res.provenance, MarketDataProvenance):
        raise ValueError(f"Missing MarketDataProvenance for {res.key}")

    prov_source_upper = res.provenance.source.strip().upper()
    if not prov_source_upper or "FAST_INFO" in prov_source_upper:
        raise ValueError(f"Unverifiable market-data source {res.provenance.source!r} for {res.key}")

    ny_tz = ZoneInfo("America/New_York")
    if now_ny is None:
        now_ny = datetime.now(ny_tz)

    obs_dt, prov = validate_market_data_observation(
        res.obs_time,
        res.provenance,
        reference_date=now_ny.date(),
    )

    prov_entry: Dict[str, Any] = {
        "kind": prov.kind.value,
        "source": prov.source,
        "effective_session_date": prov.effective_session_date,
        "obs_time": res.obs_time,
    }

    kind = res.data.get("kind")
    updates: Dict[str, Any] = {}

    if kind == "vwap":
        sym = str(res.data.get("symbol") or res.key.target.removeprefix("VWAP:")).strip().upper()
        if not sym or res.key.target != f"{MARKET_METRIC_VWAP}:{sym}":
            raise ValueError(f"VWAP key/payload mismatch for {res.key}")
        if prov.kind != ObservationKind.INSTANT or obs_dt is None:
            raise ValueError(f"VWAP requires INSTANT provenance with UTC obs_time for {sym}")
        v_true = _compute_vwap_from_payload(res.data)
        v_status, v_num = classify_market_numeric(v_true)
        if v_status != "VALID" or v_num is None or v_num <= 0:
            raise ValueError(f"Invalid or non-positive VWAP observation for {sym}")
        updates["vwaps"] = float(v_num)
        return sym, MARKET_METRIC_VWAP, updates, prov_entry

    if kind != "metric":
        raise ValueError(f"Unknown market-data payload kind: {kind!r}")

    metric = str(res.data.get("metric") or "").strip().upper()
    sym = str(res.data.get("symbol") or "").strip().upper()
    if not metric or not sym or res.key.target != f"{metric}:{sym}":
        raise ValueError(f"Metric key/payload mismatch for {res.key}: {res.data!r}")

    def _validate_nonneg_int(raw_val: Any) -> int:
        if isinstance(raw_val, bool):
            raise ValueError(f"Boolean not allowed for {metric} on {sym}")
        num = to_float(raw_val)
        if num is None or not math.isfinite(num) or num < 0:
            raise ValueError(f"Invalid non-negative numeric value for {metric} on {sym}: {raw_val!r}")
        return int(num)

    def _validate_pos_float(raw_val: Any) -> float:
        status_cls, num = classify_market_numeric(raw_val)
        if status_cls != "VALID" or num is None or num <= 0:
            raise ValueError(f"Invalid positive numeric value for {metric} on {sym}: {raw_val!r}")
        return float(num)

    def _stage_chart_meta() -> None:
        meta = res.data.get("chart_cache_meta")
        if not isinstance(meta, dict):
            return
        ts_raw = meta.get("ts")
        if ts_raw is None or isinstance(ts_raw, bool):
            return
        ts_val = to_float(ts_raw)
        if ts_val is None or not math.isfinite(ts_val) or ts_val <= 0:
            return

        tup = meta.get("tuple")
        if not isinstance(tup, tuple) or len(tup) != 4:
            return

        p_elem, sv_elem, pm_elem, ah_elem = tup
        if p_elem is not None:
            if isinstance(p_elem, bool):
                return
            p_num = to_float(p_elem)
            if p_num is None or not math.isfinite(p_num) or p_num <= 0:
                return
            valid_price = float(p_num)
        else:
            valid_price = None

        valid_vols = []
        for v in (sv_elem, pm_elem, ah_elem):
            if isinstance(v, bool):
                return
            v_num = to_float(v)
            if v_num is None or not math.isfinite(v_num) or v_num < 0:
                return
            valid_vols.append(int(v_num))

        updates["last_chart_fetch"] = float(ts_val)
        updates["chart_cache"] = (valid_price, valid_vols[0], valid_vols[1], valid_vols[2])

    def _stage_premarket_meta() -> None:
        meta = res.data.get("premarket_vol_cache_meta")
        if not isinstance(meta, dict):
            return
        ts_raw = meta.get("ts")
        if ts_raw is None or isinstance(ts_raw, bool):
            return
        ts_val = to_float(ts_raw)
        if ts_val is None or not math.isfinite(ts_val) or ts_val <= 0:
            return

        val_raw = meta.get("value")
        if val_raw is None or isinstance(val_raw, bool):
            return
        val_num = to_float(val_raw)
        if val_num is None or not math.isfinite(val_num) or val_num < 0:
            return

        updates["last_premarket_vol_fetch"] = float(ts_val)
        updates["premarket_vol_cache"] = int(val_num)

    if metric == MARKET_METRIC_QUOTE:
        if prov.kind != ObservationKind.INSTANT or obs_dt is None:
            raise ValueError(f"QUOTE requires INSTANT provenance for {sym}")
        price = _validate_pos_float(res.data.get("value"))
        status = str(res.data.get("status") or cache.session.get(sym) or "OPEN").strip().upper()
        obs_ny = obs_dt.astimezone(ny_tz)
        validity, freshness = determine_market_freshness(
            price=price,
            obs_time=obs_ny,
            current_ny=now_ny,
            session_status=status,
            source=prov.source,
        )
        updates["prices"] = price
        updates["observation_times"] = obs_ny.isoformat()
        updates["retrieval_times"] = now_ny.isoformat()
        updates["price_validity"] = validity
        updates["freshness"] = freshness
        updates["price_sources"] = prov.source
        updates["session"] = status
        updates["session_liquidity"] = (
            "LOW" if status in ("AFTER-HOURS", "PRE-MARKET") else "HIGH"
        )

        pre_p = to_float(res.data.get("pre_market_price"))
        post_p = to_float(res.data.get("after_hours_price"))
        reg_close = cache.regular_close.get(sym)
        eff_day_open = cache.day_open.get(sym)
        eff_day_low = cache.day_low.get(sym)

        if status == "PRE-MARKET":
            eff_pre = float(pre_p) if (pre_p is not None and pre_p > 0) else price
            updates["pre_market_price"] = eff_pre
            if reg_close and reg_close > 0:
                feed_chg = to_float(res.data.get("pre_market_change_pct_feed"))
                if feed_chg is not None and abs(feed_chg) < 500:
                    pre_chg = float(feed_chg)
                else:
                    pre_chg = ((eff_pre - reg_close) / reg_close) * 100
                updates["session_change"] = pre_chg
                updates["gaps"] = pre_chg
                updates["pre_market_change"] = pre_chg
        else:
            if status in ("AFTER-HOURS", "CLOSED"):
                eff_post = float(post_p) if (post_p is not None and post_p > 0) else price
                updates["after_hours_price"] = eff_post
                if reg_close and reg_close > 0:
                    updates["after_hours_change"] = ((eff_post - reg_close) / reg_close) * 100
            if reg_close and reg_close > 0:
                updates["session_change"] = ((price - reg_close) / reg_close) * 100
                gap_price = (
                    float(eff_day_open)
                    if (status in ("OPEN", "CLOSED") and eff_day_open and eff_day_open > 0)
                    else price
                )
                updates["gaps"] = ((gap_price - reg_close) / reg_close) * 100

        if eff_day_low and reg_close and reg_close > 0:
            drop_from_close = ((float(eff_day_low) - reg_close) / reg_close) * 100
            if drop_from_close <= -10.0:
                updates["ssr_active"] = True
            else:
                updates["ssr_active"] = bool(cache.ssr_active.get(sym, False))

        eff_pre_ovr = updates.get("pre_market_price", cache.pre_market_price.get(sym))
        eff_post_ovr = updates.get("after_hours_price", cache.after_hours_price.get(sym))
        if eff_pre_ovr and eff_post_ovr and eff_post_ovr > 0:
            updates["overnight_return"] = ((eff_pre_ovr - eff_post_ovr) / eff_post_ovr) * 100

        _stage_chart_meta()
        return sym, metric, updates, prov_entry

    if metric == MARKET_METRIC_SESSION_VOLUME:
        if prov.kind != ObservationKind.INSTANT or obs_dt is None:
            raise ValueError(f"SESSION_VOLUME requires INSTANT provenance for {sym}")
        updates["volumes"] = _validate_nonneg_int(res.data.get("value"))
        _stage_chart_meta()
        return sym, metric, updates, prov_entry

    if metric == MARKET_METRIC_PREMARKET_VOLUME:
        if prov.kind != ObservationKind.INSTANT or obs_dt is None:
            raise ValueError(f"PREMARKET_VOLUME requires INSTANT provenance for {sym}")
        updates["pre_market_volume"] = _validate_nonneg_int(res.data.get("value"))
        _stage_premarket_meta()
        _stage_chart_meta()
        return sym, metric, updates, prov_entry

    if metric == MARKET_METRIC_AFTER_HOURS_VOLUME:
        if prov.kind != ObservationKind.INSTANT or obs_dt is None:
            raise ValueError(f"AFTER_HOURS_VOLUME requires INSTANT provenance for {sym}")
        updates["after_hours_volume"] = _validate_nonneg_int(res.data.get("value"))
        _stage_chart_meta()
        return sym, metric, updates, prov_entry

    if metric == MARKET_METRIC_DAY_OPEN:
        if prov.kind != ObservationKind.INSTANT or obs_dt is None:
            raise ValueError(f"DAY_OPEN requires INSTANT provenance for {sym}")
        day_open = _validate_pos_float(res.data.get("value"))
        updates["day_open"] = day_open
        status = str(res.data.get("status") or cache.session.get(sym) or "OPEN").strip().upper()
        reg_close = cache.regular_close.get(sym)
        if status in ("OPEN", "CLOSED") and reg_close and reg_close > 0:
            updates["gaps"] = ((day_open - reg_close) / reg_close) * 100
        return sym, metric, updates, prov_entry

    if metric == MARKET_METRIC_DAY_HIGH:
        if prov.kind != ObservationKind.INSTANT or obs_dt is None:
            raise ValueError(f"DAY_HIGH requires INSTANT provenance for {sym}")
        updates["day_high"] = _validate_pos_float(res.data.get("value"))
        return sym, metric, updates, prov_entry

    if metric == MARKET_METRIC_DAY_LOW:
        if prov.kind != ObservationKind.INSTANT or obs_dt is None:
            raise ValueError(f"DAY_LOW requires INSTANT provenance for {sym}")
        day_low = _validate_pos_float(res.data.get("value"))
        updates["day_low"] = day_low
        reg_close = cache.regular_close.get(sym)
        if reg_close and reg_close > 0 and ((day_low - reg_close) / reg_close) * 100 <= -10.0:
            updates["ssr_active"] = True
        return sym, metric, updates, prov_entry

    if metric == MARKET_METRIC_REGULAR_CLOSE:
        if prov.kind != ObservationKind.SESSION_REFERENCE or not prov.effective_session_date:
            raise ValueError(f"REGULAR_CLOSE requires SESSION_REFERENCE provenance for {sym}")
        reg_close = _validate_pos_float(res.data.get("value"))
        updates["regular_close"] = reg_close
        updates["regular_close_dates"] = prov.effective_session_date
        updates["regular_close_sources"] = prov.source

        status = str(res.data.get("status") or cache.session.get(sym) or "OPEN").strip().upper()
        cur_price = cache.prices.get(sym)
        if cur_price and cur_price > 0:
            if status == "PRE-MARKET":
                eff_pre = cache.pre_market_price.get(sym) or cur_price
                pre_chg = ((eff_pre - reg_close) / reg_close) * 100
                updates["session_change"] = pre_chg
                updates["gaps"] = pre_chg
                updates["pre_market_change"] = pre_chg
            else:
                updates["session_change"] = ((cur_price - reg_close) / reg_close) * 100
                eff_day_open = cache.day_open.get(sym)
                gap_price = (
                    float(eff_day_open)
                    if (status in ("OPEN", "CLOSED") and eff_day_open and eff_day_open > 0)
                    else float(cur_price)
                )
                updates["gaps"] = ((gap_price - reg_close) / reg_close) * 100
                if status in ("AFTER-HOURS", "CLOSED") and cache.after_hours_price.get(sym):
                    updates["after_hours_change"] = (
                        (float(cache.after_hours_price[sym]) - reg_close) / reg_close
                    ) * 100
        eff_day_low = cache.day_low.get(sym)
        if eff_day_low and ((float(eff_day_low) - reg_close) / reg_close) * 100 <= -10.0:
            updates["ssr_active"] = True
        return sym, metric, updates, prov_entry

    if metric == MARKET_METRIC_PREVIOUS_SESSION_VOLUME:
        if prov.kind != ObservationKind.SESSION_REFERENCE or not prov.effective_session_date:
            raise ValueError(f"PREVIOUS_SESSION_VOLUME requires SESSION_REFERENCE provenance for {sym}")
        updates["previous_session_volume"] = _validate_nonneg_int(res.data.get("value"))
        updates["previous_session_volume_dates"] = prov.effective_session_date
        updates["previous_session_volume_sources"] = prov.source
        return sym, metric, updates, prov_entry

    raise ValueError(f"Unsupported market metric: {metric!r}")


def _publish_metric_cache_candidate(
    symbol: str,
    metric_name: str,
    candidate: Dict[str, Any],
    prov_entry: Dict[str, Any],
) -> None:
    """
    Commits a validated single-metric candidate and its provenance record to MarketDataCache
    as a single atomic all-or-nothing transaction on the coordinating thread.
    """
    _publish_market_data_candidate(
        symbol=symbol,
        candidate=candidate,
        provenance=(metric_name, prov_entry),
    )


def refresh_cached_symbol_state(symbol: str, status: str, now_ny: Optional[datetime] = None) -> None:
    """
    Re-evaluates cached market observation freshness and session tags on the coordinating thread
    without performing any network I/O or fabricating timestamps.
    """
    ny_tz = ZoneInfo("America/New_York")
    if now_ny is None:
        now_ny = datetime.now(ny_tz)

    liquidity = "LOW" if status in ("AFTER-HOURS", "PRE-MARKET") else "HIGH"
    price = cache.prices.get(symbol)
    obs_str = cache.observation_times.get(symbol)
    obs_dt = None
    if obs_str:
        try:
            obs_dt = datetime.fromisoformat(obs_str)
        except Exception:
            obs_dt = None

    validity, freshness = determine_market_freshness(
        price=price,
        obs_time=obs_dt,
        current_ny=now_ny,
        session_status=status,
        source=cache.price_sources.get(symbol),
    )
    cache.session[symbol] = status
    cache.session_liquidity[symbol] = liquidity
    cache.price_validity[symbol] = validity
    cache.freshness[symbol] = freshness


def enqueue_market_batch_quotes(
    symbols: Sequence[str],
    status: str,
    metrics: Optional[Sequence[str]] = None,
    coordinator: Optional[BackgroundIntelligenceCoordinator] = None,
) -> Tuple[bool, Optional[Any]]:
    """
    Enqueues non-blocking batched quote acquisition on MARKET_DATA_COORDINATOR.
    Uses a session- and metric-set-specific request key paired with independent
    per-symbol, per-metric {METRIC}:{sym} child keys.
    """
    clean_syms = tuple(dict.fromkeys(str(s).strip().upper() for s in symbols if s and str(s).strip()))
    if not clean_syms:
        return False, None
    eff_coord = MARKET_DATA_COORDINATOR if coordinator is None else coordinator
    clean_status = str(status or "OPEN").strip().upper()
    metric_set = _normalize_requested_metrics(metrics, default=_default_batch_metrics_for_status(clean_status))
    metric_sig = ",".join(metric_set)
    batch_target = f"BATCH_QUOTE:{clean_status}:{metric_sig}:" + ",".join(sorted(clean_syms))
    child_keys = tuple(
        _metric_work_key(m, s)
        for s in clean_syms
        for m in metric_set
    )
    return eff_coord.enqueue(
        work_type=WorkType.MARKET_DATA.value,
        target=batch_target,
        obs_time=None,
        fn=_acquire_batch_quotes_raw,
        args=(clean_syms, clean_status, metric_set),
        child_keys=child_keys,
    )


def enqueue_market_symbol_fallback(
    symbol: str,
    status: str,
    need_price: bool = True,
    needs_pre_vol: bool = False,
    needs_post_vol: bool = False,
    need_fast_info: bool = False,
    need_prev_close: bool = False,
    need_chart_open: bool = False,
    need_polygon_vol: bool = False,
    metrics: Optional[Sequence[str]] = None,
    coordinator: Optional[BackgroundIntelligenceCoordinator] = None,
) -> Tuple[bool, Optional[Any]]:
    """
    Enqueues non-blocking per-symbol fallback/supplemental market-data acquisition on
    MARKET_DATA_COORDINATOR. Encodes session status and sorted requested metric set
    in the request key (preventing cross-session or cross-metric coalescing) while fanning out
    independent per-metric {METRIC}:{symbol} child keys and respecting 60s/90s TTLs.
    """
    if not symbol or not str(symbol).strip():
        return False, None
    eff_coord = MARKET_DATA_COORDINATOR if coordinator is None else coordinator
    sym_clean = str(symbol).strip().upper()
    clean_status = str(status or "OPEN").strip().upper()
    now_ts = t_time.time()

    chart_fresh = (
        sym_clean in cache.last_chart_fetch
        and (now_ts - cache.last_chart_fetch[sym_clean]) < 60
    )
    pre_vol_fresh = (
        sym_clean in cache.last_premarket_vol_fetch
        and (now_ts - cache.last_premarket_vol_fetch[sym_clean]) < 90
    )

    if metrics is not None:
        requested_metrics = _normalize_requested_metrics(metrics, default=())
    else:
        req_list: List[str] = []
        if need_price:
            req_list.append(MARKET_METRIC_QUOTE)
        if (need_price or needs_post_vol) and not chart_fresh:
            req_list.append(MARKET_METRIC_SESSION_VOLUME)
            if clean_status in ("AFTER-HOURS", "CLOSED"):
                req_list.append(MARKET_METRIC_AFTER_HOURS_VOLUME)
        if needs_pre_vol and not pre_vol_fresh:
            req_list.append(MARKET_METRIC_PREMARKET_VOLUME)
        if need_polygon_vol or (need_price and USE_POLYGON and sym_clean not in cache.previous_session_volume):
            req_list.append(MARKET_METRIC_PREVIOUS_SESSION_VOLUME)
        if need_prev_close:
            req_list.append(MARKET_METRIC_REGULAR_CLOSE)
        if need_chart_open:
            req_list.append(MARKET_METRIC_DAY_OPEN)
        requested_metrics = tuple(sorted(set(req_list)))

    if not requested_metrics:
        return False, None

    req_set = set(requested_metrics)
    need_price_eff = bool(need_price or (MARKET_METRIC_QUOTE in req_set))
    fetch_chart_eff = bool(
        (
            MARKET_METRIC_QUOTE in req_set
            or MARKET_METRIC_SESSION_VOLUME in req_set
            or MARKET_METRIC_AFTER_HOURS_VOLUME in req_set
        )
        and not chart_fresh
    )
    fetch_pre_vol_eff = bool((MARKET_METRIC_PREMARKET_VOLUME in req_set) and not pre_vol_fresh)
    include_polygon_vol_eff = bool(MARKET_METRIC_PREVIOUS_SESSION_VOLUME in req_set)
    include_prev_close_eff = bool(MARKET_METRIC_REGULAR_CLOSE in req_set)
    include_chart_open_eff = bool(MARKET_METRIC_DAY_OPEN in req_set)

    metric_sig = ",".join(requested_metrics)
    request_target = f"FALLBACK_REQ:{clean_status}:{metric_sig}:{sym_clean}"
    child_keys = tuple(_metric_work_key(m, sym_clean) for m in requested_metrics)

    return eff_coord.enqueue(
        work_type=WorkType.MARKET_DATA.value,
        target=request_target,
        obs_time=None,
        fn=_acquire_symbol_fallback_raw,
        args=(sym_clean, clean_status),
        kwargs={
            "need_price": need_price_eff,
            "fetch_chart": fetch_chart_eff,
            "fetch_premarket_vol": fetch_pre_vol_eff,
            "include_finnhub": bool(need_price_eff and USE_FINNHUB),
            "include_fast_info": False,
            "include_polygon_vol": include_polygon_vol_eff,
            "include_prev_close": include_prev_close_eff,
            "include_chart_open": include_chart_open_eff,
            "requested_metrics": requested_metrics,
        },
        child_keys=child_keys,
    )


def enqueue_market_vwap(
    symbol: str,
    status: str,
    coordinator: Optional[BackgroundIntelligenceCoordinator] = None,
) -> Tuple[bool, Optional[Any]]:
    """
    Enqueues non-blocking intraday VWAP acquisition on MARKET_DATA_COORDINATOR
    with a session-specific request key and stable per-symbol VWAP:{symbol} result key.
    """
    if not symbol or not str(symbol).strip():
        return False, None
    eff_coord = MARKET_DATA_COORDINATOR if coordinator is None else coordinator
    sym_clean = str(symbol).strip().upper()
    clean_status = str(status or "OPEN").strip().upper()
    return eff_coord.enqueue(
        work_type=WorkType.MARKET_DATA.value,
        target=f"VWAP_REQUEST:{clean_status}:{sym_clean}",
        obs_time=None,
        fn=_acquire_vwap_raw,
        args=(sym_clean, clean_status),
        result_key=WorkKey(WorkType.MARKET_DATA.value, f"VWAP:{sym_clean}"),
    )


def apply_background_market_data_results(
    coordinator: Optional[BackgroundIntelligenceCoordinator] = None,
    applicator: Optional[BackgroundResultApplicator] = None,
    fallback_requests: Optional[Dict[str, dict]] = None,
) -> List[Tuple[WorkResult, bool, str]]:
    """
    Drains completed market-data results from MARKET_DATA_COORDINATOR and applies them
    on the coordinating thread via MARKET_DATA_RESULT_APPLICATOR's explicit publication callback.
    """
    eff_coord = MARKET_DATA_COORDINATOR if coordinator is None else coordinator
    eff_app = MARKET_DATA_RESULT_APPLICATOR if applicator is None else applicator
    eff_fb_store = _MARKET_DATA_FALLBACK_REQUESTS if fallback_requests is None else fallback_requests
    results = eff_coord.drain_results()
    if not results:
        return []

    pending_fallbacks: Dict[str, dict] = {}
    batch_quote_applied: Dict[str, str] = {}
    ny_tz = ZoneInfo("America/New_York")
    now_ny = datetime.now(ny_tz)

    def _publish_market_data_result(res: WorkResult) -> None:
        sym, metric_name, updates, prov_entry = _build_metric_cache_candidate(res, now_ny=now_ny)
        _publish_metric_cache_candidate(sym, metric_name, updates, prov_entry)
        if (
            isinstance(res.data, dict)
            and metric_name == MARKET_METRIC_QUOTE
            and res.data.get("from_batch")
        ):
            batch_quote_applied[sym] = str(res.data.get("status") or "OPEN").strip().upper()

    audit = eff_app.apply_results(
        results=results,
        grounding_target={},
        scout_cache_target={},
        scout_category_map={},
        on_publish=_publish_market_data_result,
    )

    for sym, clean_status in batch_quote_applied.items():
        needs_pre_vol = cache.pre_market_volume.get(sym, 0) == 0
        needs_post_vol = (clean_status in ("AFTER-HOURS", "CLOSED") and cache.after_hours_volume.get(sym, 0) == 0)
        need_prev_close = (clean_status != "PRE-MARKET" and sym not in cache.regular_close)
        need_polygon_vol = bool(USE_POLYGON and sym not in cache.previous_session_volume)
        need_chart_open = False
        if clean_status == "OPEN":
            reg_open = cache.day_open.get(sym)
            eff_reg_close = cache.regular_close.get(sym)
            eff_price = cache.prices.get(sym)
            if reg_open and eff_price and eff_reg_close and eff_reg_close > 0:
                raw_gap = ((float(reg_open) - eff_reg_close) / eff_reg_close) * 100
                session_chg = ((eff_price - eff_reg_close) / eff_reg_close) * 100
                if abs(raw_gap) > 3.0 and ((raw_gap < -3.0 and session_chg > 0) or (raw_gap > 3.0 and session_chg < 0)):
                    need_chart_open = True
        if needs_pre_vol or needs_post_vol or need_prev_close or need_polygon_vol or need_chart_open:
            pending_fallbacks[sym] = {
                "need_price": False,
                "needs_pre_vol": needs_pre_vol,
                "needs_post_vol": needs_post_vol,
                "need_fast_info": False,
                "need_prev_close": need_prev_close,
                "need_chart_open": need_chart_open,
                "need_polygon_vol": need_polygon_vol,
            }

    for res, applied, reason in audit:
        if applied or str(reason).startswith("Stale "):
            continue
        target = res.key.target
        if target.startswith("QUOTE:"):
            sym = target.removeprefix("QUOTE:")
            if res.error and str(res.error).startswith("No valid provenance-backed price for "):
                continue
            cur_status = str(cache.session.get(sym) or "OPEN").strip().upper()
            pending_fallbacks.setdefault(
                sym,
                {
                    "need_price": True,
                    "needs_pre_vol": cache.pre_market_volume.get(sym, 0) == 0,
                    "needs_post_vol": cache.after_hours_volume.get(sym, 0) == 0,
                    "need_fast_info": False,
                    "need_prev_close": sym not in cache.regular_close,
                    "need_chart_open": False,
                    "need_polygon_vol": bool(USE_POLYGON and sym not in cache.previous_session_volume),
                },
            )
            if cur_status == "PRE-MARKET":
                pending_fallbacks[sym]["needs_post_vol"] = False
        elif target.startswith("BATCH_QUOTE:"):
            remainder = target.removeprefix("BATCH_QUOTE:")
            sym_list_part = remainder.rsplit(":", 1)[1] if ":" in remainder else remainder
            for sym in sym_list_part.split(","):
                sym = sym.strip()
                if sym:
                    pending_fallbacks.setdefault(
                        sym,
                        {
                            "need_price": True,
                            "needs_pre_vol": cache.pre_market_volume.get(sym, 0) == 0,
                            "needs_post_vol": cache.after_hours_volume.get(sym, 0) == 0,
                            "need_fast_info": False,
                            "need_prev_close": sym not in cache.regular_close,
                            "need_chart_open": False,
                            "need_polygon_vol": bool(USE_POLYGON and sym not in cache.previous_session_volume),
                        },
                    )

    if pending_fallbacks:
        with _MARKET_DATA_FALLBACK_STATE_LOCK:
            for sym, req in pending_fallbacks.items():
                existing = eff_fb_store.get(sym)
                if existing is None or req.get("need_price") or not existing.get("need_price"):
                    eff_fb_store[sym] = req

    return audit


def coordinate_market_data_cycle(
    symbols: Sequence[str],
    status: str,
    vwap_batch: Sequence[str] = (),
    now_ny: Optional[datetime] = None,
    coordinator: Optional[BackgroundIntelligenceCoordinator] = None,
    applicator: Optional[BackgroundResultApplicator] = None,
    fallback_requests: Optional[Dict[str, dict]] = None,
) -> dict:
    """
    Non-blocking coordinating-thread market-data step for the recurring monitoring loop.
    Applies completed results under per-key locks, evaluates existing cache freshness,
    and enqueues eligible batch/VWAP/fallback acquisitions without waiting for provider I/O.
    """
    ny_tz = ZoneInfo("America/New_York")
    if now_ny is None:
        now_ny = datetime.now(ny_tz)

    eff_fb_store = _MARKET_DATA_FALLBACK_REQUESTS if fallback_requests is None else fallback_requests
    applied_audit = apply_background_market_data_results(
        coordinator=coordinator,
        applicator=applicator,
        fallback_requests=eff_fb_store,
    )
    batch_accepted, _ = enqueue_market_batch_quotes(
        symbols,
        status=status,
        coordinator=coordinator,
    )

    vwap_accepted = 0
    for b_sym in vwap_batch:
        acc, _ = enqueue_market_vwap(
            b_sym,
            status=status,
            coordinator=coordinator,
        )
        if acc:
            vwap_accepted += 1

    with _MARKET_DATA_FALLBACK_STATE_LOCK:
        fallback_queue = dict(eff_fb_store)
        eff_fb_store.clear()

    fallback_enqueued = 0
    for sym in symbols:
        try:
            sym_clean = str(sym).strip().upper()
            refresh_cached_symbol_state(sym_clean, status, now_ny=now_ny)
            req = fallback_queue.pop(sym_clean, None)
            if req is not None:
                acc, _ = enqueue_market_symbol_fallback(
                    sym_clean,
                    status=status,
                    coordinator=coordinator,
                    **req,
                )
                if acc:
                    fallback_enqueued += 1
        except Exception as sym_err:
            logger.warning(f"[MarketData] Error scheduling fallback for {sym!r}: {sym_err}")

    return {
        "applied_count": sum(1 for _, ok, _ in applied_audit if ok),
        "rejected_count": sum(1 for _, ok, _ in applied_audit if not ok),
        "batch_enqueued": bool(batch_accepted),
        "vwap_enqueued": vwap_accepted,
        "fallback_enqueued": fallback_enqueued,
    }


def _build_historical_cache_candidate(
    res: WorkResult,
    now_ny: Optional[datetime] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Validates a completed HISTORICAL_DATA WorkResult on the coordinating thread against
    live cache state and returns (symbol, cache_updates_dict) ready for atomic publication.
    """
    if not isinstance(res, WorkResult):
        raise ValueError("Historical result must be a WorkResult")
    if res.key.work_type != WorkType.HISTORICAL_DATA.value:
        raise ValueError(f"Expected HISTORICAL_DATA work_type, got {res.key.work_type}")
    if res.error is not None:
        raise ValueError(str(res.error))
    if not isinstance(res.data, dict):
        raise ValueError("Historical result payload must be a dict")
    if res.data.get("kind") != "historical_data":
        raise ValueError(f"Unexpected historical payload kind: {res.data.get('kind')!r}")

    target_sym = str(res.key.target or "").strip().upper()
    payload_sym = str(res.data.get("symbol") or "").strip().upper()
    if not target_sym or payload_sym != target_sym:
        raise ValueError(f"Historical symbol mismatch: target={target_sym!r}, payload={payload_sym!r}")

    source = res.data.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("Historical candidate missing non-empty source metadata")

    hist = res.data.get("history")
    if not isinstance(hist, pd.DataFrame):
        raise ValueError("Historical candidate history must be a pandas DataFrame")

    current_hist = cache.history.get(target_sym)
    is_valid, reason = validate_historical_dataframe(hist, current_hist)
    if not is_valid:
        raise ValueError(f"Historical publication rejected for {target_sym}: {reason}")

    ref_date = now_ny.date() if now_ny is not None else current_market_session_date()
    valid_fetch_date = validate_session_date(
        res.data.get("last_history_fetch_date"),
        reference_date=ref_date,
    )

    technicals = res.data.get("technicals")
    if not isinstance(technicals, dict):
        raise ValueError("Historical candidate technicals must be a dict")
    if technicals.get("status") == "UNAVAILABLE":
        raise ValueError("Historical candidate technicals marked UNAVAILABLE")
    missing_keys = [k for k in _HISTORICAL_REQUIRED_TECH_KEYS if k not in technicals]
    if missing_keys:
        raise ValueError(f"Historical candidate technicals missing keys: {missing_keys}")

    trend_val = technicals.get("Trend_Score")
    if isinstance(trend_val, bool) or not isinstance(trend_val, (int, float)) or not math.isfinite(float(trend_val)):
        raise ValueError(f"Historical candidate invalid Trend_Score: {trend_val!r}")

    for num_key in (
        "SMA_20", "SMA_50", "SMA_200", "RSI", "MACD", "MACD_Signal",
        "MACD_Hist", "BB_Upper", "BB_Lower", "BB_Pct", "MFI", "ATR_Pct",
        "ATR", "Last_Reg_Close",
    ):
        val = technicals.get(num_key)
        if val is not None:
            num_status, num_val = classify_market_numeric(val)
            if isinstance(val, bool) or num_status != "VALID" or num_val is None:
                raise ValueError(f"Historical candidate invalid numeric technical {num_key}={val!r}")

    reg_close_raw = res.data.get("regular_close")
    reg_close_val: Optional[float] = None
    if reg_close_raw is not None:
        reg_status, parsed_reg = classify_market_numeric(reg_close_raw)
        if isinstance(reg_close_raw, bool) or reg_status != "VALID" or parsed_reg is None or parsed_reg <= 0:
            raise ValueError(f"Historical candidate invalid regular_close: {reg_close_raw!r}")
        reg_close_val = float(parsed_reg)

    clean_technicals = dict(technicals)
    clean_technicals["Trend_Score"] = int(trend_val)

    updates: Dict[str, Any] = {
        "history": hist.copy(deep=True),
        "technicals": clean_technicals,
        "last_history_fetch_date": valid_fetch_date,
    }
    if reg_close_val is not None:
        updates["regular_close"] = reg_close_val

    return target_sym, updates


def _validate_utc_timestamp(value: Any, field_name: str) -> str:
    """
    Validates that value is a present, non-empty string representing a strict UTC
    ISO-8601 timestamp with explicit 'Z' or zero offset (+00:00).
    Rejects missing, non-string, empty, naive, non-zero offset, and malformed timestamps fail-closed.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing {field_name}")

    raw = value.strip()
    try:
        valid_utc = validate_utc_iso8601_timestamp(raw)
    except Exception as exc:
        raise ValueError(f"Invalid {field_name}: {exc}") from exc

    return valid_utc


def _canonical_spot_timestamp(value: Any, field_name: str) -> str:
    """
    Validates and resolves a spot observation/retrieval timestamp for snapshot capture.
    Accepts:
    1. Strict UTC ISO-8601 with explicit 'Z' or zero offset (+00:00).
    2. Valid New York session timezone (-04:00 or -05:00) from live quote cache,
       which is converted to strict canonical UTC ISO-8601 with 'Z'.
    Fails closed: rejects naive, missing, non-string, malformed, or foreign-offset timestamps.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing {field_name}")

    raw = value.strip()
    if "-00:00" in raw:
        raise ValueError(f"Ambiguous timestamp offset '-00:00' in '{value}'")

    try:
        return validate_utc_iso8601_timestamp(raw)
    except Exception:
        pass

    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            raise ValueError(f"Naive timestamp without tzinfo: {value!r}")
        offset = dt.utcoffset()
        offset_secs = offset.total_seconds() if offset is not None else None
        if offset_secs not in (-14400, -18000):
            raise ValueError(f"Non-UTC, non-NY offset in {field_name}: {value!r}")
        dt_utc = dt.astimezone(timezone.utc)
        return validate_utc_iso8601_timestamp(dt_utc.isoformat().replace("+00:00", "Z"))
    except Exception as exc:
        raise ValueError(f"Invalid {field_name}: {exc}") from exc


def _matches_canonical_timestamp(cached_ts: Any, canonical_utc: str) -> bool:
    """
    Verifies that cached_ts matches canonical_utc.
    Matches if equal as strings, or if both represent the exact same UTC instant.
    Rejects naive, missing, malformed, or unequal timestamps fail-closed.
    """
    if cached_ts == canonical_utc:
        return True
    if not isinstance(cached_ts, str) or not isinstance(canonical_utc, str):
        return False
    try:
        dt_cached = datetime.fromisoformat(cached_ts.strip())
        dt_canon = datetime.fromisoformat(canonical_utc.strip())
        if dt_cached.tzinfo is None or dt_canon.tzinfo is None:
            return False
        return dt_cached.astimezone(timezone.utc) == dt_canon.astimezone(timezone.utc)
    except Exception:
        return False


def _build_gex_cache_candidate(
    res: WorkResult,
    now_ts: Optional[float] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Validates a completed OPTIONS_GEX WorkResult on the coordinating thread and returns
    (symbol, cache_updates_dict) ready for atomic publication.
    Enforces canonical spot observation identity, freshness and actionability against current
    live market-data cache.
    """
    if not isinstance(res, WorkResult):
        raise ValueError("Options/GEX result must be a WorkResult")
    if res.key.work_type != WorkType.OPTIONS_GEX.value:
        raise ValueError(f"Expected OPTIONS_GEX work_type, got {res.key.work_type}")
    if res.error is not None:
        raise ValueError(str(res.error))
    if not isinstance(res.data, dict):
        raise ValueError("Options/GEX result payload must be a dict")
    if res.data.get("kind") != "options_gex":
        raise ValueError(f"Unexpected GEX payload kind: {res.data.get('kind')!r}")

    target_sym = str(res.key.target or "").strip().upper()
    payload_sym = str(res.data.get("symbol") or "").strip().upper()
    if not target_sym or payload_sym != target_sym:
        raise ValueError(f"GEX symbol mismatch: target={target_sym!r}, payload={payload_sym!r}")

    source = res.data.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("GEX candidate missing non-empty source metadata")

    spot_raw = res.data.get("spot_price")
    spot_status, spot_num = classify_market_numeric(spot_raw)
    if isinstance(spot_raw, bool) or spot_status != "VALID" or spot_num is None or spot_num <= 0:
        raise ValueError(f"GEX candidate invalid spot_price: {spot_raw!r}")

    # Canonical spot snapshot validation
    snapshot = res.data.get("spot_snapshot")
    if not isinstance(snapshot, dict):
        raise ValueError("GEX candidate missing canonical spot snapshot")

    snapshot_price = snapshot.get("price")
    snapshot_status, snapshot_num = classify_market_numeric(snapshot_price)
    if (
        isinstance(snapshot_price, bool)
        or snapshot_status != "VALID"
        or snapshot_num is None
        or snapshot_num <= 0
    ):
        raise ValueError("GEX candidate has invalid snapshot spot price")

    observation_time = _validate_utc_timestamp(
        snapshot.get("observation_time"),
        "spot observation_time",
    )
    retrieval_time = _validate_utc_timestamp(
        snapshot.get("retrieval_time"),
        "spot retrieval_time",
    )

    if float(spot_num) != float(snapshot_num):
        raise ValueError("GEX result spot price does not match its snapshot")

    if not is_price_actionable(target_sym):
        raise ValueError("GEX spot observation is no longer actionable")

    current_price = cache.prices.get(target_sym)
    current_status, current_num = classify_market_numeric(current_price)
    if (
        isinstance(current_price, bool)
        or current_status != "VALID"
        or current_num is None
        or float(current_num) != float(snapshot_num)
    ):
        raise ValueError("GEX spot price has changed")

    if not _matches_canonical_timestamp(cache.observation_times.get(target_sym), observation_time):
        raise ValueError("GEX spot observation has been superseded")

    if not _matches_canonical_timestamp(cache.retrieval_times.get(target_sym), retrieval_time):
        raise ValueError("GEX spot retrieval identity has changed")

    contract_count = res.data.get("contract_count")
    if isinstance(contract_count, bool) or not isinstance(contract_count, int) or contract_count <= 0:
        raise ValueError(f"GEX candidate invalid contract_count: {contract_count!r}")

    eff_now = float(t_time.time() if now_ts is None else now_ts)
    fetched_raw = res.data.get("fetched_at")
    fetched_status, fetched_num = classify_market_numeric(fetched_raw)
    if isinstance(fetched_raw, bool) or fetched_status != "VALID" or fetched_num is None or fetched_num <= 0:
        raise ValueError(f"GEX candidate invalid fetched_at: {fetched_raw!r}")
    if fetched_num > (eff_now + 60.0):
        raise ValueError(f"GEX candidate future fetched_at timestamp: {fetched_num!r}")
    if (eff_now - fetched_num) >= GEX_REFRESH_TTL_SECONDS:
        raise ValueError(f"GEX candidate expired before publication for {target_sym}")

    profile = res.data.get("gex_profile")
    if not isinstance(profile, dict):
        raise ValueError("GEX candidate gex_profile must be a dict")
    clean_profile: Dict[str, float] = {}
    for key in _GEX_REQUIRED_KEYS:
        if key not in profile:
            raise ValueError(f"GEX candidate missing field '{key}'")
        raw_val = profile[key]
        val_status, val_num = classify_market_numeric(raw_val)
        if isinstance(raw_val, bool) or val_status != "VALID" or val_num is None:
            raise ValueError(f"GEX candidate invalid field {key}={raw_val!r}")
        clean_profile[key] = float(val_num)

    updates: Dict[str, Any] = {
        "gex": dict(clean_profile),
        "gex_cache": dict(clean_profile),
        "last_gex_fetch": float(fetched_num),
    }
    return target_sym, updates


def enqueue_historical_data(
    symbol: str,
    t_obj: Any = None,
    today_str: Optional[str] = None,
    now_ny: Optional[datetime] = None,
    force: bool = False,
    coordinator: Optional[BackgroundIntelligenceCoordinator] = None,
) -> Tuple[bool, Optional[WorkKey]]:
    """
    Non-blocking historical + technical-indicator enqueue onto HEAVY_MARKET_DATA_COORDINATOR.
    Snapshots detached existing history on the coordinating thread and preserves daily refresh
    deduplication when valid history is already recorded for today's NY session date.
    """
    if not symbol or not str(symbol).strip():
        return False, None
    eff_coord = HEAVY_MARKET_DATA_COORDINATOR if coordinator is None else coordinator
    sym_clean = str(symbol).strip().upper()
    eff_today = today_str or get_today_str()

    existing_hist = cache.history.get(sym_clean)
    if (
        not force
        and isinstance(existing_hist, pd.DataFrame)
        and not existing_hist.empty
        and cache.last_history_fetch_date.get(sym_clean) == eff_today
    ):
        return False, None

    existing_hist_copy = (
        existing_hist.copy(deep=True)
        if isinstance(existing_hist, pd.DataFrame) and not existing_hist.empty
        else None
    )

    return eff_coord.enqueue(
        work_type=WorkType.HISTORICAL_DATA.value,
        target=sym_clean,
        obs_time=None,
        fn=_acquire_historical_and_technicals_raw,
        kwargs={
            "symbol": sym_clean,
            "t_obj": t_obj,
            "existing_hist": existing_hist_copy,
            "today_str": eff_today,
            "now_ny": now_ny,
        },
    )


def enqueue_options_gex(
    symbol: str,
    ticker_obj: Any = None,
    spot_price: Any = None,
    force: bool = False,
    now_ts: Optional[float] = None,
    coordinator: Optional[BackgroundIntelligenceCoordinator] = None,
    spot_obs_time: Optional[str] = None,
    spot_retrieval_time: Optional[str] = None,
) -> Tuple[bool, Optional[WorkKey]]:
    """
    Non-blocking options/GEX enqueue onto HEAVY_MARKET_DATA_COORDINATOR.
    Enforces valid positive spot price, actionable spot observation identity, and the
    1,800-second GEX refresh TTL without advancing cache.last_gex_fetch before worker
    completion and coordinating-thread publication.
    Captures canonical spot observation identity (price, observation_time, retrieval_time)
    to enforce spot freshness validation at the publication boundary.
    """
    if not symbol or not str(symbol).strip():
        return False, None
    eff_coord = HEAVY_MARKET_DATA_COORDINATOR if coordinator is None else coordinator
    sym_clean = str(symbol).strip().upper()

    # Require is_price_actionable to be true before enqueue
    if not is_price_actionable(sym_clean):
        return False, None

    cached_spot = cache.prices.get(sym_clean)
    cached_status, cached_num = classify_market_numeric(cached_spot)
    if isinstance(cached_spot, bool) or cached_status != "VALID" or cached_num is None or cached_num <= 0:
        return False, None

    eff_spot = cached_spot if spot_price is None else spot_price
    spot_status, spot_num = classify_market_numeric(eff_spot)
    if isinstance(eff_spot, bool) or spot_status != "VALID" or spot_num is None or spot_num <= 0:
        return False, None

    # Ensure captured spot price corresponds to the canonical cached price
    if float(spot_num) != float(cached_num):
        return False, None

    raw_obs_time = cache.observation_times.get(sym_clean) if spot_obs_time is None else spot_obs_time
    raw_retrieval_time = cache.retrieval_times.get(sym_clean) if spot_retrieval_time is None else spot_retrieval_time

    # Validate both timestamps are present, non-empty, and resolve to strict canonical UTC ISO-8601
    try:
        valid_obs_time = _canonical_spot_timestamp(raw_obs_time, "spot observation_time")
        valid_retrieval_time = _canonical_spot_timestamp(raw_retrieval_time, "spot retrieval_time")
    except Exception:
        return False, None

    if not _matches_canonical_timestamp(cache.observation_times.get(sym_clean), valid_obs_time):
        return False, None
    if not _matches_canonical_timestamp(cache.retrieval_times.get(sym_clean), valid_retrieval_time):
        return False, None

    eff_now = float(t_time.time() if now_ts is None else now_ts)
    last_fetch = cache.last_gex_fetch.get(sym_clean)
    cached_gex = cache.gex_cache.get(sym_clean)
    if (
        not force
        and not isinstance(last_fetch, bool)
        and isinstance(last_fetch, (int, float))
        and math.isfinite(float(last_fetch))
        and 0 <= (eff_now - float(last_fetch)) < GEX_REFRESH_TTL_SECONDS
        and isinstance(cached_gex, dict)
    ):
        if sym_clean not in cache.gex:
            cache.gex[sym_clean] = dict(cached_gex)
        return False, None

    spot_snapshot = {
        "price": float(cached_num),
        "observation_time": valid_obs_time,
        "retrieval_time": valid_retrieval_time,
    }

    return eff_coord.enqueue(
        work_type=WorkType.OPTIONS_GEX.value,
        target=sym_clean,
        obs_time=None,
        fn=_acquire_options_gex_raw,
        kwargs={
            "symbol": sym_clean,
            "ticker_obj": ticker_obj,
            "spot_price": float(cached_num),
            "spot_snapshot": spot_snapshot,
        },
    )


def apply_background_heavy_market_data_results(
    now_ny: Optional[datetime] = None,
    now_ts: Optional[float] = None,
    coordinator: Optional[BackgroundIntelligenceCoordinator] = None,
    applicator: Optional[BackgroundResultApplicator] = None,
) -> List[Tuple[WorkResult, bool, str]]:
    """
    Drains completed historical and options/GEX results from HEAVY_MARKET_DATA_COORDINATOR
    and publishes valid candidates atomically on the coordinating thread via
    HEAVY_MARKET_DATA_RESULT_APPLICATOR.
    """
    eff_coord = HEAVY_MARKET_DATA_COORDINATOR if coordinator is None else coordinator
    eff_app = HEAVY_MARKET_DATA_RESULT_APPLICATOR if applicator is None else applicator
    results = eff_coord.drain_results()
    if not results:
        return []

    def _publish_heavy_result(res: WorkResult) -> None:
        if res.key.work_type == WorkType.HISTORICAL_DATA.value:
            sym, updates = _build_historical_cache_candidate(res, now_ny=now_ny)
            _publish_market_data_candidate(sym, updates)
        elif res.key.work_type == WorkType.OPTIONS_GEX.value:
            sym, updates = _build_gex_cache_candidate(res, now_ts=now_ts)
            _publish_market_data_candidate(sym, updates)
        else:
            raise ValueError(f"Unsupported heavy market-data work_type: {res.key.work_type!r}")

    return eff_app.apply_results(
        results=results,
        on_publish=_publish_heavy_result,
    )


def coordinate_heavy_market_data_cycle(
    symbols: Sequence[str],
    tickers_obj: Optional[Dict[str, Any]] = None,
    is_heavy: bool = False,
    include_historical: bool = True,
    include_gex: bool = True,
    today_str: Optional[str] = None,
    now_ny: Optional[datetime] = None,
    now_ts: Optional[float] = None,
    coordinator: Optional[BackgroundIntelligenceCoordinator] = None,
    applicator: Optional[BackgroundResultApplicator] = None,
) -> Dict[str, int]:
    """
    Non-blocking heavy market-data cycle orchestrator run on the coordinating thread.
    Applies completed historical and options/GEX results first, then enqueues eligible
    historical refreshes and GEX refreshes onto HEAVY_MARKET_DATA_COORDINATOR.
    """
    audit = apply_background_heavy_market_data_results(
        now_ny=now_ny,
        now_ts=now_ts,
        coordinator=coordinator,
        applicator=applicator,
    )
    historical_applied = sum(
        1 for res, ok, _ in audit if ok and res.key.work_type == WorkType.HISTORICAL_DATA.value
    )
    gex_applied = sum(
        1 for res, ok, _ in audit if ok and res.key.work_type == WorkType.OPTIONS_GEX.value
    )
    rejected_count = sum(1 for _, ok, _ in audit if not ok)

    eff_today = today_str or (now_ny.strftime("%Y-%m-%d") if now_ny is not None else get_today_str())
    eff_now = float(t_time.time() if now_ts is None else now_ts)
    ticker_map = tickers_obj or {}

    historical_enqueued = 0
    gex_enqueued = 0

    for sym in symbols:
        try:
            sym_clean = str(sym or "").strip().upper()
            if not sym_clean:
                continue
            t_obj = ticker_map.get(sym_clean) if sym_clean in ticker_map else ticker_map.get(sym)

            if include_historical:
                cur_hist = cache.history.get(sym_clean)
                need_hist = (
                    is_heavy
                    or cur_hist is None
                    or (isinstance(cur_hist, pd.DataFrame) and cur_hist.empty)
                    or cache.last_history_fetch_date.get(sym_clean) != eff_today
                )
                if need_hist:
                    acc, _ = enqueue_historical_data(
                        sym_clean,
                        t_obj=t_obj,
                        today_str=eff_today,
                        now_ny=now_ny,
                        force=is_heavy,
                        coordinator=coordinator,
                    )
                    if acc:
                        historical_enqueued += 1

            if include_gex:
                if is_heavy or sym_clean not in cache.last_gex_fetch:
                    acc, _ = enqueue_options_gex(
                        sym_clean,
                        ticker_obj=t_obj,
                        spot_price=cache.prices.get(sym_clean),
                        now_ts=eff_now,
                        force=is_heavy,
                        coordinator=coordinator,
                    )
                    if acc:
                        gex_enqueued += 1
        except Exception as sym_err:
            logger.warning(f"[HeavyMarketData] Error scheduling heavy workloads for {sym!r}: {sym_err}")

    return {
        "applied_count": historical_applied + gex_applied,
        "historical_applied": historical_applied,
        "gex_applied": gex_applied,
        "rejected_count": rejected_count,
        "historical_enqueued": historical_enqueued,
        "gex_enqueued": gex_enqueued,
    }


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
    if not is_price_actionable(symbol):
        return 0.0
    p = cache.prices.get(symbol)
    v = cache.vwaps.get(symbol)

    if p is None or v is None or p <= 0 or v <= 0:
        return 0.0

    return ((p - v) / v) * 100.0


def classify_signal(symbol):
    if not is_price_actionable(symbol):
        validity = cache.price_validity.get(symbol, "UNAVAILABLE")
        freshness = cache.freshness.get(symbol, "UNAVAILABLE")
        if validity == "STALE" or freshness == "STALE":
            return "STALE"
        if freshness == "HISTORICAL":
            return "HISTORICAL"
        return "UNAVAILABLE"

    t = cache.technicals.get(symbol, {})
    rsi = t.get("RSI")
    atr = t.get("ATR_Pct") or 0.0
    rvol = calculate_rvol(symbol)
    dist_vwap = distance_from_vwap(symbol)

    if rvol > 2 and atr > 0 and dist_vwap > atr:
        return "BREAKOUT"
    if rvol > 2 and atr > 0 and dist_vwap < -atr:
        return "BREAKDOWN"
    if rsi is not None and rsi < 30:
        return "OVERSOLD"
    if rsi is not None and rsi > 70:
        return "OVERBOUGHT"
    if abs(dist_vwap) < 0.2:
        return "VWAP PIN"
    return "NEUTRAL"


def calculate_score(symbol):
    if not is_price_actionable(symbol):
        return 0, ""

    t = cache.technicals.get(symbol, {})
    p = cache.prices.get(symbol)
    v = cache.vwaps.get(symbol, 0)

    if p is None or p <= 0 or not t or t.get("status") == "UNAVAILABLE":
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
    # 5. GAP% COMPONENT
    # Range: -1 to +1
    # -----------------------------
    if gap > 1.0:
        score += 1
    elif gap < -1.0:
        score -= 1

    # -----------------------------
    # NEW: MACD COMPONENT
    # Range: -1 to +1
    # -----------------------------
    macd_hist = t.get("MACD_Hist", 0)
    if macd_hist > 0:
        score += 1
    elif macd_hist < 0:
        score -= 1

    # -----------------------------
    # NEW: BOLLINGER BANDS COMPONENT (%B)
    # Range: -1 to +1
    # -----------------------------
    bb_pct = t.get("BB_Pct", 0.5)
    if bb_pct > 0.8:
        score += 1  # Strong uptrend / riding the upper band
    elif bb_pct < 0.2:
        score -= 1  # Weakness / riding the lower band

    # -----------------------------
    # NEW: MFI COMPONENT
    # Range: -1 to +1
    # -----------------------------
    mfi = t.get("MFI", 50)
    if mfi > 70:
        score += 1  # Strong money flow
    elif mfi < 30:
        score -= 1  # Weak money flow

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

    # -----------------------------
    # CLAMP SCORE
    # -----------------------------
    score = max(-6, min(6, int(score)))

    return score, note


def calculate_fib_forecast(symbol: str, current_price: float, hist_df=None, open_price: float = 0.0, atr: float = 0.0, rsi: float = 50.0, rvol: float = 1.0, vwap: float = 0.0) -> dict:
    """Calculates Trend-Based Fibonacci Extension & Profit-Taking targets
    strictly optimized for the active daily trading session per ENH_254 & ENH_255.
    Anchors to today's session high/low (HOD/LOD) and daily ATR volatility ceiling,
    preventing stale multi-week swing distortion.
    """
    p = float(current_price) if current_price else 0.0
    if hist_df is None and symbol:
        hist_df = cache.history.get(symbol)
    
    # 1. Resolve today's session High (HOD), Low (LOD), and Open
    day_h = float(cache.day_high.get(symbol, 0.0) or 0.0)
    day_l = float(cache.day_low.get(symbol, 0.0) or 0.0)
    open_p = float(open_price or cache.day_open.get(symbol, 0.0) or 0.0)
    atr_val = float(atr or 0.0)

    # Fallback to latest candle in hist_df if live session cache is not populated
    if hist_df is not None and not hist_df.empty:
        latest_row = hist_df.iloc[-1]
        if day_h <= 0 and 'High' in latest_row:
            day_h = float(latest_row['High'])
        if day_l <= 0 and 'Low' in latest_row:
            day_l = float(latest_row['Low'])
        if open_p <= 0 and 'Open' in latest_row:
            open_p = float(latest_row['Open'])

    # If current price exceeds day_h, update day_h
    if p > 0:
        if day_h <= 0 or p > day_h:
            day_h = p
        if day_l <= 0 or p < day_l:
            day_l = p
        if open_p <= 0:
            open_p = p

    if p <= 0 and day_h <= 0:
        return {
            "pA": 0.0, "pB": 0.0, "pC": 0.0,
            "impulse": 0.0,
            "t1_100": 0.0, "t1_pct": 0.0, "t1_limit": 0.0,
            "t2_1618": 0.0, "t2_pct": 0.0, "t2_limit": 0.0,
            "t3_2618": 0.0, "t3_pct": 0.0, "t3_limit": 0.0,
            "daily_peak_target": 0.0,
            "daily_peak_atr": 0.0,
            "daily_peak_status": "NONE",
            "atr_confluence": False,
            "next_resistance": 0.0,
            "next_resistance_label": "None",
            "distance_to_next_pct": 0.0,
            "levels": []
        }

    try:
        # Anchor points strictly bounded to current session
        p_a = day_l
        p_b = day_h
        
        # Calculate session impulse
        session_impulse = p_b - p_a
        
        # If session range is tight (early session), scale impulse using daily ATR
        min_impulse = (atr_val * 0.50) if atr_val > 0 else (p * 0.02)
        impulse = max(session_impulse, min_impulse, 0.01)

        # Pullback floor PC: current price or estimated consolidation floor
        if p > 0 and p <= p_b:
            p_c = p
        elif p > p_b:
            # Active breakout into new session highs: retest base
            p_c = round(p_b - (impulse * 0.382), 2)
        else:
            p_c = p_a + (impulse * 0.382)

        # Daily Trading Peak Ratios per ENH_254 & ENH_255
        ratio_defs = [
            (0.236, "0.236 Early Trim"),
            (0.382, "0.382 Conservative Trim"),
            (0.500, "0.500 Mid Expansion"),
            (0.618, "0.618 Golden Ratio"),
            (0.786, "0.786 Extension"),
            (1.000, "1.000 Measured Move (T1)"),
            (1.272, "1.272 Expansion Peak"),
            (1.618, "1.618 Golden Peak Target (T2)"),
            (2.000, "2.000 Momentum Target"),
            (2.618, "2.618 Parabolic Blow-Off (T3)")
        ]

        levels = []
        next_res = None
        next_res_label = None
        dist_next_pct = None

        # If current price is below the Daily Session High (PB), PB is the immediate resistance ceiling
        if p > 0 and p_b > p:
            pb_dist = round(((p_b - p) / p) * 100, 2)
            levels.append({
                "ratio": "Peak",
                "label": "Daily Peak",
                "price": round(p_b, 2),
                "distance_pct": pb_dist
            })
            next_res = round(p_b, 2)
            next_res_label = "Daily Peak"
            dist_next_pct = pb_dist

        for ratio, label in ratio_defs:
            lvl_price = round(p_c + (ratio * impulse), 2)
            lvl_dist = round(((lvl_price - p) / p) * 100, 2) if p > 0 else 0.0
            levels.append({
                "ratio": ratio,
                "label": label,
                "price": lvl_price,
                "distance_pct": lvl_dist
            })
            if p > 0 and lvl_price > p and next_res is None:
                next_res = lvl_price
                next_res_label = label
                dist_next_pct = lvl_dist

        if next_res is None and levels:
            next_res = levels[-1]["price"]
            next_res_label = str(levels[-1]["label"]) + " (Exceeded)"
            dist_next_pct = levels[-1]["distance_pct"]

        # Tranches per ENH_254: T1 = 1.000, T1.272 = 1.272, T2 = 1.618, T3 = 2.618
        t1_val = round(p_c + (1.000 * impulse), 2)
        t1272_val = round(p_c + (1.272 * impulse), 2)
        t2_val = round(p_c + (1.618 * impulse), 2)
        t3_val = round(p_c + (2.618 * impulse), 2)

        # Front-running limit orders per ENH_254/255: 0.25% beneath the exact level
        t1_limit = round(t1_val * 0.9975, 2)
        t2_limit = round(t2_val * 0.9975, 2)
        t3_limit = round(t3_val * 0.9975, 2)

        # ATR Daily Volatility Ceiling Confluence per ENH_255
        daily_peak_atr = round(open_p + (1.25 * atr_val), 2) if (open_p > 0 and atr_val > 0) else 0.0
        atr_confluence = False
        confluent_target = None
        if daily_peak_atr > 0:
            for cand_val, cand_lbl in [(t1_val, "T1"), (t1272_val, "T1.272"), (t2_val, "T2")]:
                if abs(cand_val - daily_peak_atr) / daily_peak_atr <= 0.018:
                    atr_confluence = True
                    confluent_target = cand_val
                    break

        # Daily Peak Target Selection
        if atr_confluence and confluent_target:
            daily_peak_target = confluent_target
        elif daily_peak_atr > 0 and abs(t2_val - daily_peak_atr) / daily_peak_atr <= 0.035:
            # High proximity to daily volatility ceiling
            daily_peak_target = t2_val
        elif p_b > p and p_b >= t1_val:
            daily_peak_target = p_b
        else:
            daily_peak_target = t2_val

        # Daily Peak Exhaustion Status per ENH_255
        daily_peak_status = "EXPANDING"
        if p >= t1_val:
            if (rvol < 1.5 and rsi > 70) or (vwap > 0 and p >= vwap * 1.10 and rsi > 80) or (daily_peak_target > 0 and p >= daily_peak_target * 0.995):
                daily_peak_status = "PEAK_EXHAUSTED"
            elif daily_peak_target > 0 and abs(p - daily_peak_target) / daily_peak_target <= 0.018:
                daily_peak_status = "AT_PEAK_RESISTANCE"
        elif daily_peak_target > 0 and abs(p - daily_peak_target) / daily_peak_target <= 0.018:
            daily_peak_status = "AT_PEAK_RESISTANCE"

        return {
            "pA": round(p_a, 2),
            "pB": round(p_b, 2),
            "pC": round(p_c, 2),
            "impulse": round(impulse, 2),
            "t1_100": t1_val,
            "t1_pct": round(((t1_val - p) / p) * 100, 2) if p > 0 else 0.0,
            "t1_limit": t1_limit,
            "t2_1618": t2_val,
            "t2_pct": round(((t2_val - p) / p) * 100, 2) if p > 0 else 0.0,
            "t2_limit": t2_limit,
            "t3_2618": t3_val,
            "t3_pct": round(((t3_val - p) / p) * 100, 2) if p > 0 else 0.0,
            "t3_limit": t3_limit,
            "daily_peak_target": round(daily_peak_target, 2),
            "daily_peak_atr": round(daily_peak_atr, 2),
            "daily_peak_status": daily_peak_status,
            "atr_confluence": atr_confluence,
            "next_resistance": next_res if next_res is not None else 0.0,
            "next_resistance_label": next_res_label if next_res_label is not None else "None",
            "distance_to_next_pct": dist_next_pct if dist_next_pct is not None else 0.0,
            "levels": levels
        }
    except Exception as e:
        return {
            "pA": 0.0, "pB": 0.0, "pC": 0.0,
            "impulse": 0.0,
            "t1_100": 0.0, "t1_pct": 0.0, "t1_limit": 0.0,
            "t2_1618": 0.0, "t2_pct": 0.0, "t2_limit": 0.0,
            "t3_2618": 0.0, "t3_pct": 0.0, "t3_limit": 0.0,
            "daily_peak_target": 0.0,
            "daily_peak_atr": 0.0,
            "daily_peak_status": "NONE",
            "atr_confluence": False,
            "next_resistance": 0.0,
            "next_resistance_label": "Error",
            "distance_to_next_pct": 0.0,
            "levels": []
        }


def fetch_stocks(state=None):
    """Returns compact ticker dictionaries with fib_forecast for API and Council consumption."""
    if state is None:
        state = get_global_state_snapshot()
    compact_tickers = []
    for t in state.get("tickers", []):
        compact_tickers.append({
            "ticker": t.get("ticker"),
            "price": t.get("price"),
            "session_change_pct": t.get("session_change_pct"),
            "gap_percent": t.get("gap_percent"),
            "rsi": t.get("rsi"),
            "macd": t.get("macd"),
            "macd_status": t.get("macd_status"),
            "atr_percent": t.get("atr_percent"),
            "vwap": t.get("vwap"),
            "trend": t.get("trend"),
            "signal": t.get("signal"),
            "score": t.get("score"),
            "dealer_posture": t.get("dealer_posture"),
            "net_gex_total": t.get("net_gex_total"),
            "fib_forecast": t.get("fib_forecast")
        })
    return compact_tickers


def get_ssot_json_payload():
    """Returns the sanitized SSoT JSON payload with live tickers and fib_forecast."""
    return sanitize_json_payload(get_global_state_snapshot())


def compute_merton_allocation(gamma=3.0, r=0.04):
    try:
        prices_dict = {}
        for sym in ALL_TICKERS:
            sym_upper = sym.upper()
            if sym_upper in cache.history and not cache.history[sym_upper].empty:
                df = cache.history[sym_upper]
                if 'Close' in df.columns:
                    close = df['Close']
                    if isinstance(close, pd.DataFrame):
                        close = close.iloc[:, 0]
                    valid_close = close[close > 0.0]
                    if len(valid_close) > 10:
                        prices_dict[sym_upper] = valid_close
                        
        if not prices_dict:
            return {
                "status": "error",
                "message": "No historical price data found in cache for active tickers."
            }
            
        df_prices = pd.DataFrame(prices_dict).ffill().bfill()
        df_returns = df_prices.pct_change().dropna(how='all')
        if df_returns.empty:
            return {
                "status": "error",
                "message": "Calculated returns dataframe is empty."
            }
            
        stds = df_returns.std()
        valid_cols = stds[stds > 1e-7].index.tolist()
        if len(valid_cols) < 1:
            return {
                "status": "error",
                "message": "Insufficient variance in asset price history to compute covariance matrix."
            }
            
        df_returns = df_returns[valid_cols]
        df_prices = df_prices[valid_cols]
        
        mean_daily = df_returns.mean()
        mu = mean_daily * 252
        
        cov_daily = df_returns.cov()
        Sigma = cov_daily * 252
        
        n_assets = Sigma.shape[0]
        Sigma_reg = Sigma + np.eye(n_assets) * 1e-4
        
        excess_returns = mu - r
        
        weights = np.linalg.solve(Sigma_reg, excess_returns) / gamma
        
        raw_allocation = {ticker: float(w) for ticker, w in zip(df_prices.columns, weights)}
        
        clipped_weights = np.clip(weights, 0, None)
        sum_clipped = np.sum(clipped_weights)
        if sum_clipped > 0:
            normalized_weights = clipped_weights / sum_clipped
        else:
            normalized_weights = np.zeros_like(weights)
            
        normalized_allocation = {ticker: float(w) for ticker, w in zip(df_prices.columns, normalized_weights)}
        
        vols = df_returns.std() * np.sqrt(252)
        sharpe_ratios = {}
        for ticker in df_prices.columns:
            vol = vols[ticker]
            expected_ret = mu[ticker]
            sharpe_ratios[ticker] = float((expected_ret - r) / vol) if vol > 0 else 0.0
            
        return {
            "status": "success",
            "parameters": {
                "risk_aversion_gamma": float(gamma),
                "annual_risk_free_rate": float(r),
                "trading_days": 252
            },
            "raw_merton_weights": raw_allocation,
            "normalized_long_only_weights": normalized_allocation,
            "annualized_statistics": {
                ticker: {
                    "expected_return": float(mu[ticker]),
                    "volatility": float(vols[ticker]),
                    "sharpe_ratio": sharpe_ratios[ticker]
                } for ticker in df_prices.columns
            }
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Calculation failed: {str(e)}"
        }


def resolve_held_and_watched(service: Optional[Any] = None) -> tuple[set, Optional[str]]:
    """
    Retrieves held and watched tickers from the persistence service.
    Returns (held_and_watched_set, error_str_or_none).
    Distinguishes legitimate first-run empty state from retrieval failures.
    """
    if service is None:
        service = get_persistence_service()
    held_and_watched = set()
    try:
        basket = service.get_portfolio_basket()
        for p in basket.get("portfolio", []):
            ticker = p.get("ticker")
            if ticker:
                held_and_watched.add(str(ticker).upper())
        watchlist = service.get_watchlist()
        for w in watchlist:
            if w:
                held_and_watched.add(str(w).upper())
        return held_and_watched, None
    except Exception as e:
        err = f"Authoritative portfolio/watchlist retrieval failure: {e}"
        print(f"[Error] {err}")
        return set(), err


def build_daemon_final_output(
    data: list,
    status: str,
    authoritative_state_error: Optional[str] = None,
    service: Optional[Any] = None,
) -> dict:
    """
    Constructs the final output dictionary for GLOBAL_STATE and the web dashboard.
    If authoritative financial state (SSoT or portfolio) is degraded or failed,
    surfaces an explicit DEGRADED status, sets financial_state_valid=False,
    sets local_storage_state=None, and discards unverified scout data.
    Optional trade lessons failure logs a warning without invalidating financial state.
    """
    if service is None:
        service = get_persistence_service()

    supplemental_lessons = []
    try:
        supplemental_lessons = service.get_trade_lessons()
    except Exception as e:
        print(f"[Warning] Failed to load trade lessons via persistence service (optional): {e}")

    supplemental_ssot = None
    try:
        supplemental_ssot = service.get_ssot_data()
    except Exception as e:
        if not authoritative_state_error:
            authoritative_state_error = f"Authoritative SSoT retrieval failure: {e}"
        print(f"[Error] Failed to load SSoT data via persistence service: {e}")

    non_scout_data = []
    scout_data = []
    for item in data:
        if item.get("_isScout"):
            # Independent actionability check for scout entries:
            # Must be ACTIVE, VALID price, and CURRENT or DELAYED freshness
            item_status = item.get("status")
            item_validity = item.get("price_validity")
            item_freshness = item.get("freshness")
            item_price = item.get("price")

            is_item_actionable = (
                item_status == "ACTIVE"
                and item_validity == "VALID"
                and item_freshness in ("CURRENT", "DELAYED")
                and item_price is not None
                and item_price > 0
            )

            if is_item_actionable and item.get("rsi", 50.0) <= SCOUT_MAX_RSI:
                scout_data.append(item)
        else:
            non_scout_data.append(item)

    if authoritative_state_error is not None:
        filtered_scouts = []
        final_tickers = non_scout_data
    else:
        scout_data.sort(key=lambda x: x.get("score", 0), reverse=True)
        filtered_scouts = scout_data[:SCOUT_LIMIT]
        final_tickers = non_scout_data + filtered_scouts

    merton_alloc = compute_merton_allocation() if authoritative_state_error is None else {}

    if authoritative_state_error is not None:
        return {
            "_meta": {
                "description": "LIVE MARKET DATA - FINANCIAL STATE DEGRADED",
                "is_simulation": False,
                "source": "Real-time Exchange Feed",
                "reliability": "DEGRADED",
                "persistence_error": authoritative_state_error,
            },
            "timestamp": datetime.now(ZoneInfo("America/New_York")).strftime('%Y-%m-%d %H:%M:%S'),
            "status": f"DEGRADED - PERSISTENCE FAILURE: {authoritative_state_error}",
            "is_heavy_refresh": False,
            "financial_state_valid": False,
            "persistence_error": authoritative_state_error,
            "tickers": final_tickers,
            "scout_categories_loaded": [],
            "qualitative_grounding": cache.qualitative_grounding,
            "local_storage_state": None,
            "trade_lessons": supplemental_lessons,
            "merton_optimal_allocation": {},
        }
    else:
        return {
            "_meta": {
                "description": "LIVE MARKET DATA - DO NOT TREAT AS SIMULATED",
                "is_simulation": False,
                "source": "Real-time Exchange Feed",
                "reliability": "High",
            },
            "timestamp": datetime.now(ZoneInfo("America/New_York")).strftime('%Y-%m-%d %H:%M:%S'),
            "status": status,
            "is_heavy_refresh": False,
            "financial_state_valid": True,
            "tickers": final_tickers,
            "scout_categories_loaded": list(PROCESSED_SCOUT_CATEGORIES),
            "qualitative_grounding": cache.qualitative_grounding,
            "local_storage_state": supplemental_ssot if supplemental_ssot is not None else {},
            "trade_lessons": supplemental_lessons,
            "merton_optimal_allocation": merton_alloc,
        }


def get_macro_telemetry_context():
    """
    Computes current macro telemetry context (VIX price, volatility regime, VIXY ROC).
    Fails closed: if observation is not actionable, volatility_regime is 'UNAVAILABLE'
    and numeric prices/rates are None.
    """
    if is_price_actionable('^VIX'):
        v_p = float(cache.prices.get('^VIX', 0.0))
        v_regime = "HIGH_VOL" if v_p > 20.0 else "LOW_VOL" if (v_p < 12.0 and v_p > 0) else "NORMAL"
    else:
        v_p = None
        v_regime = "UNAVAILABLE"

    if is_price_actionable('VIXY'):
        v_roc = round(float(cache.gaps.get('VIXY', 0.0)), 2)
    else:
        v_roc = None

    return v_p, v_regime, v_roc


# -----------------------------
# MAIN LOOP
# -----------------------------
def run_daemon():
    global GLOBAL_STATE, FORCE_REFRESH, ALL_TICKERS, TICKERS, MACRO_TICKERS
    FORCE_REFRESH = False
    daemon_mkt_coord = MARKET_DATA_COORDINATOR
    daemon_mkt_app = MARKET_DATA_RESULT_APPLICATOR
    daemon_heavy_coord = HEAVY_MARKET_DATA_COORDINATOR
    daemon_heavy_app = HEAVY_MARKET_DATA_RESULT_APPLICATOR
    daemon_bg_coord = BACKGROUND_COORDINATOR
    daemon_bg_app = BACKGROUND_RESULT_APPLICATOR
    daemon_fallback_requests: Dict[str, dict] = {}
    try:
        print(f"{CYAN}Initializing GEM Dashboard v16.0 (Data Hardened + Trend)...{RESET}")
        tickers_obj = {sym: yf.Ticker(sym) for sym in ALL_TICKERS}

        print(f"{YELLOW}Scheduling initial heavy fetch (non-blocking)...{RESET}")
        def _set_boot(phase, progress, total, sym, status_msg=None):
            def _updater(current):
                s = dict(current)
                s["boot_phase"] = phase
                s["boot_progress"] = progress
                s["boot_total"] = total
                s["boot_ticker"] = sym
                if status_msg:
                    s["status"] = status_msg
                return s
            update_global_state_snapshot(_updater)

        def _clear_boot():
            def _updater(current):
                s = dict(current)
                s.pop("boot_phase", None)
                s.pop("boot_progress", None)
                s.pop("boot_total", None)
                s.pop("boot_ticker", None)
                return s
            update_global_state_snapshot(_updater)

        status = get_market_status()
        startup_ny_now = datetime.now(ZoneInfo("America/New_York"))
        total_tickers = len(ALL_TICKERS)
        _set_boot(
            "TECHNICAL_ANALYSIS",
            total_tickers,
            total_tickers,
            "SCHEDULED",
            "INITIALIZING - SCHEDULING NON-BLOCKING MARKET & HEAVY DATA..."
        )
        coordinate_market_data_cycle(
            ALL_TICKERS,
            status,
            [],
            now_ny=startup_ny_now,
            coordinator=daemon_mkt_coord,
            applicator=daemon_mkt_app,
            fallback_requests=daemon_fallback_requests,
        )
        coordinate_heavy_market_data_cycle(
            ALL_TICKERS,
            tickers_obj=tickers_obj,
            is_heavy=True,
            include_historical=True,
            include_gex=True,
            now_ny=startup_ny_now,
            coordinator=daemon_heavy_coord,
            applicator=daemon_heavy_app,
        )
        print(f"{GREEN}Initial live and heavy market-data workloads queued.{RESET}")

        # Clear boot progress keys so startup completes cleanly without blocking monitoring
        _clear_boot()

        while True:
            # 1. Apply any completed background intelligence results under monotonic ordering
            apply_background_intelligence_results(
                coordinator=daemon_bg_coord,
                applicator=daemon_bg_app,
            )

            # Dynamically reload TICKERS and MACRO_TICKERS from SSOT on every cycle
            try:
                TICKERS = _load_ssot_tickers()
                MACRO_TICKERS = _load_macro_tickers()
                ALL_TICKERS = list(dict.fromkeys(TICKERS + MACRO_TICKERS))
            except Exception as e:
                pass

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
            if is_heavy and get_global_state_snapshot().get("tickers"):
                update_global_state_snapshot(lambda s: {**s, "is_heavy_refresh": True})
                try:
                    for p_sym in WATCHLIST:
                        if p_sym not in cache.qualitative_grounding:
                            enqueue_news_grounding(p_sym, obs_time=None)
                except Exception as g_err:
                    print(f"[Daemon Grounding Warning]: {g_err}")

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

            coordinate_market_data_cycle(
                ALL_TICKERS,
                status,
                batch,
                now_ny=ny_now,
                coordinator=daemon_mkt_coord,
                applicator=daemon_mkt_app,
                fallback_requests=daemon_fallback_requests,
            )
            heavy_stats = coordinate_heavy_market_data_cycle(
                ALL_TICKERS,
                tickers_obj=tickers_obj,
                is_heavy=is_heavy,
                include_historical=True,
                include_gex=True,
                now_ny=ny_now,
                coordinator=daemon_heavy_coord,
                applicator=daemon_heavy_app,
            )

            sep_line = "-" * table_width
            print(sep_line)
            header_str = (
                f"{'TICKER':<{w_sym}} {'PRICE':<{w_prc}} {'CHG%':<{w_gap}} {'VOL':<{w_vol}} "
                f"{'ATR%':<{w_atr}} {'RSI':<{w_rsi}} {'VWAP':<{w_vwp}} {'TREND':<{w_trd}} {'SCORE':^{w_scr}}"
            )
            print(header_str)
            # Calculate held and watched tickers to identify scouts inline
            held_and_watched, authoritative_state_error = resolve_held_and_watched()

            data = []

            macro_state = {
                'IEF': {'price': None, 'gap': 0.0, 'trend': 'UNAVAILABLE', 'status': 'UNAVAILABLE'},
                '^VIX': {'price': None, 'gap': 0.0, 'status': 'UNAVAILABLE'},
                'VIXY': {'price': None, 'gap': 0.0, 'status': 'UNAVAILABLE'}
            }

            needs_save = bool(heavy_stats.get("historical_applied", 0) > 0)

            for i, sym in enumerate(ALL_TICKERS):
                t_time.sleep(0.05)

                p = cache.prices.get(sym)
                gap = float(cache.gaps.get(sym, 0.0))
                session_change = float(cache.session_change.get(sym, 0.0))
                vol = cache.volumes.get(sym, 0)
                vwap = cache.vwaps.get(sym, 0.0)
                techs = cache.technicals.get(sym, {})
                score, note = calculate_score(sym)
                cur_vix_p, cur_vol_regime, cur_vixy_roc = get_macro_telemetry_context()

                if p is None or p <= 0 or cache.price_validity.get(sym) in ("INVALID", "UNAVAILABLE"):
                    print(f"{sym:<8} {RED}NO DATA{RESET}")
                    data.append({
                        "ticker": sym,
                        "session": "UNKNOWN",
                        "session_liquidity": "LOW",
                        "status": "NO_DATA",
                        "ssr_active": False,
                        "price": None,
                        "price_validity": cache.price_validity.get(sym, "UNAVAILABLE"),
                        "freshness": cache.freshness.get(sym, "UNAVAILABLE"),
                        "price_source": cache.price_sources.get(sym),
                        "observation_time": cache.observation_times.get(sym),
                        "retrieval_time": cache.retrieval_times.get(sym),
                        "regular_close": cache.regular_close.get(sym),
                        "pre_market_price": None,
                        "after_hours_price": None,
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
                        "macd": 0.0,
                        "macd_signal": 0.0,
                        "macd_hist": 0.0,
                        "macd_slope": "EXPANDING_POSITIVE",
                        "macd_status": "NEUTRAL",
                        "vwap": 0.0,
                        "distance_from_vwap": 0.0,
                        "trend_score": 0,
                        "vix_price": cur_vix_p,
                        "volatility_regime": cur_vol_regime,
                        "vixy_roc": cur_vixy_roc,
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
                        "signal": "UNAVAILABLE",
                        "trend": "FLAT",
                        "note": "NO DATA",
                        "fib_forecast": calculate_fib_forecast(sym, 0.0, None)
                    })
                    continue

                if sym == 'IEF':
                    if is_price_actionable('IEF'):
                        trend = "BULLISH (Safe)" if session_change > 0 else "BEARISH (Risk)"
                        macro_state['IEF'] = {'price': p, 'gap': session_change, 'trend': trend, 'status': 'ACTIVE'}
                    else:
                        macro_state['IEF'] = {'price': None, 'gap': 0.0, 'trend': 'UNAVAILABLE', 'status': 'UNAVAILABLE'}
                if sym == '^VIX':
                    if is_price_actionable('^VIX'):
                        macro_state['^VIX'] = {'price': p, 'gap': session_change, 'status': 'ACTIVE'}
                    else:
                        macro_state['^VIX'] = {'price': None, 'gap': 0.0, 'status': 'UNAVAILABLE'}
                if sym == 'VIXY':
                    if is_price_actionable('VIXY'):
                        macro_state['VIXY'] = {'price': p, 'gap': session_change, 'status': 'ACTIVE'}
                    else:
                        macro_state['VIXY'] = {'price': None, 'gap': 0.0, 'status': 'UNAVAILABLE'}

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

                rsi_raw = to_float(techs.get('RSI'), default=0.0) or 0.0
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
                ts = int(to_float(techs.get("Trend_Score"), default=0) or 0)
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

                atr_val = to_float(techs.get('ATR_Pct'), default=0.0)
                if atr_val is None:
                    atr_val = 0.0
                atr_str = f"{atr_val:.2f}%"
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
                _raw_gex = float(to_float(gex_data.get('net_gex'), default=0.0) or 0.0)
                if _raw_gex > 0:
                    _dealer_posture = "LONG_GAMMA"
                elif _raw_gex < 0:
                    _dealer_posture = "SHORT_GAMMA"
                else:
                    _dealer_posture = "NEUTRAL"

                actionable = is_price_actionable(sym)
                val_status = cache.price_validity.get(sym, "UNAVAILABLE")
                fresh_status = cache.freshness.get(sym, "UNAVAILABLE")

                if not actionable:
                    if val_status == "STALE" or fresh_status == "STALE":
                        item_status = "STALE"
                    elif fresh_status == "HISTORICAL":
                        item_status = "HISTORICAL"
                    elif val_status == "INVALID":
                        item_status = "INVALID"
                    else:
                        item_status = "UNAVAILABLE"
                else:
                    item_status = "ACTIVE"

                if authoritative_state_error is not None:
                    # Halt scout classification if authoritative financial state is compromised
                    is_scout = False
                else:
                    is_scout = actionable and sym in ACTIVE_SCOUT_TICKERS and sym not in held_and_watched
                effective_reg_close = to_float(cache.regular_close.get(sym) or techs.get("Last_Reg_Close"))
                effective_pre_price = to_float(cache.pre_market_price.get(sym))
                item_dict = {
                    "ticker": sym,
                    "session": cache.session.get(sym),
                    "session_liquidity": cache.session_liquidity.get(sym),
                    "status": item_status,
                    "ssr_active": cache.ssr_active.get(sym, False),
                    
                    # Prices
                    "price": float(p),
                    "price_validity": val_status,
                    "freshness": fresh_status,
                    "price_source": cache.price_sources.get(sym, "UNKNOWN"),
                    "observation_time": cache.observation_times.get(sym),
                    "retrieval_time": cache.retrieval_times.get(sym),
                    "regular_close": effective_reg_close,
                    "pre_market_price": effective_pre_price,
                    "after_hours_price": to_float(cache.after_hours_price.get(sym)),

                    # Returns
                    # session_change_pct: required by ENH_FIN_02 Protective Exit Override logic
                    "session_change_pct": round(((float(p) - effective_reg_close) / effective_reg_close) * 100, 2) if (effective_reg_close and effective_reg_close > 0) else float(cache.session_change.get(sym, 0.0)),
                    "gap_percent": round(((effective_pre_price - effective_reg_close) / effective_reg_close) * 100, 2) if (status == "PRE-MARKET" and effective_reg_close and effective_reg_close > 0 and effective_pre_price and effective_pre_price > 0) else float(gap),
                    "pre_market_change_percent": round(((effective_pre_price - effective_reg_close) / effective_reg_close) * 100, 2) if (effective_reg_close and effective_reg_close > 0 and effective_pre_price and effective_pre_price > 0) else float(cache.pre_market_change.get(sym, 0.0)),
                    "after_hours_change_percent": float(cache.after_hours_change.get(sym, 0.0)),
                    "overnight_return_percent": float(cache.overnight_return.get(sym, 0.0)),

                    # Volume
                    "volume": int(vol),
                    "rvol": float(calculate_rvol(sym)),
                    "pre_market_volume": int(cache.pre_market_volume.get(sym, 0)) or "UNAVAILABLE",
                    "after_hours_volume": int(cache.after_hours_volume.get(sym, 0)) or "UNAVAILABLE",

                    # Technicals
                    "atr_percent": float(atr_val),
                    "atr": float(to_float(techs.get("ATR"), default=0.0) or 0.0),
                    "rsi": float(rsi_raw),
                    "macd": round(float(to_float(techs.get("MACD"), default=0.0) or 0.0), 4),
                    "macd_signal": round(float(to_float(techs.get("MACD_Signal"), default=0.0) or 0.0), 4),
                    "macd_hist": round(float(to_float(techs.get("MACD_Hist"), default=0.0) or 0.0), 4),
                    "macd_slope": str(techs.get("MACD_Slope", "EXPANDING_POSITIVE")),
                    "macd_status": str(techs.get("MACD_Status", "NEUTRAL")),
                    "vwap": float(vwap),
                    "distance_from_vwap": float(distance_from_vwap(sym)),
                    # trend_score thresholds: gem_trading_rules/rules.md > TREND_SCORE_UP_THRESHOLD (3) / TREND_SCORE_DOWN_THRESHOLD (-3)
                    # For INVERSE_MACRO tickers: negated so rising price = negative (bearish for equities)
                    "trend_score": int(-ts) if sym in INVERSE_MACRO else int(ts),
                    
                    # Macro Context & Volatility Regime (Rules > VOLATILITY_REGIME_THRESHOLDS)
                    "vix_price": cur_vix_p,
                    "volatility_regime": cur_vol_regime,
                    "vixy_roc": cur_vixy_roc,

                    # GEX — field names per ENH_32 canonical schema in rules.json
                    "net_gex_total": _raw_gex,
                    "gex_exposure": _raw_gex,  # Normalized GEX exposure (position-level scaling done by SSoT Gem)
                    "dealer_posture": _dealer_posture,  # gem_trading_rules/rules.md > dealer_posture_logic
                    "gamma_flip_price": float(to_float(gex_data.get('flip_price'), default=0.0) or 0.0),
                    "inventory_velocity_delta": float(to_float(gex_data.get('inventory_velocity_delta'), default=0.0) or 0.0),
                    "gex_slope": float(to_float(gex_data.get('gex_slope'), default=0.0) or 0.0),
                    "flip_proximity_percent": float(to_float(gex_data.get('flip_proximity_percent'), default=0.0) or 0.0),
                    "strike_oi_magnet": float(to_float(gex_data.get('strike_oi_magnet'), default=0.0) or 0.0),
                    "ma_20": float(to_float(techs.get("SMA_20"), default=0.0) or 0.0),
                    "ma_50": float(to_float(techs.get("SMA_50"), default=0.0) or 0.0),
                    "ma_200": float(to_float(techs.get("SMA_200"), default=0.0) or 0.0),
                    "score": int(score) if actionable else 0,
                    "signal": classify_signal(sym),
                    # trend label thresholds: gem_trading_rules/rules.md > TREND_SCORE_UP_THRESHOLD / TREND_SCORE_DOWN_THRESHOLD
                    # For INVERSE_MACRO: JSON trend uses equity-perspective (negated score)
                    "trend": ("UP" if (-ts if sym in INVERSE_MACRO else ts) >= 3 else "DOWN" if (-ts if sym in INVERSE_MACRO else ts) <= -3 else "FLAT"),
                    
                    # Phase 6 Enhancements (Already computed inline above)

                    "note": note.strip(),
                    "fib_forecast": calculate_fib_forecast(
                        sym, p, cache.history.get(sym),
                        open_price=float(cache.day_open.get(sym, 0.0) or (techs.get("open") if isinstance(techs, dict) else 0.0) or 0.0),
                        atr=float(to_float(techs.get("ATR"), default=0.0) or 0.0),
                        rsi=float(rsi_raw or 50.0),
                        rvol=float(calculate_rvol(sym) or 1.0),
                        vwap=float(vwap or 0.0)
                    )
                }
                if is_scout:
                    item_dict["_isScout"] = True
                    item_dict["category"] = SCOUTED_TICKER_TO_CATEGORY_MAP.get(sym)

                data.append(item_dict)

            if needs_save:
                cache.save_to_disk(ALL_TICKERS)

            print("-" * table_width)
            ief = macro_state.get('IEF', {})
            ief_status = ief.get('status', 'UNAVAILABLE')
            ief_gap = float(ief.get('gap', 0.0))
            ief_price = ief.get('price')
            
            # IEF bond alert — threshold: gem_trading_rules/rules.md > system_thresholds > IEF_YIELD_ALERT_THRESHOLD (-0.15)
            if ief_status != 'ACTIVE' or ief_price is None or not is_price_actionable('IEF'):
                print(f"   >>> BOND STATUS: 7-10Y Bond Price UNAVAILABLE {YELLOW}[DATA UNAVAILABLE]{RESET}")
            elif ief_gap < -0.15:
                print(f"   >>> BOND ALERT:  7-10Y Bond Price {ief_price:.2f} ({ief_gap:+.2f}%) {RED}[YIELDS RISING - RISK]{RESET}")
            else:
                print(f"   >>> BOND STATUS: 7-10Y Bond Price {ief_price:.2f} ({ief_gap:+.2f}%) {GREEN}[YIELDS STABLE]{RESET}")

            vix = macro_state.get('^VIX', {})
            vix_status = vix.get('status', 'UNAVAILABLE')
            vix_price = vix.get('price')

            vixy = macro_state.get('VIXY', {})
            vixy_status = vixy.get('status', 'UNAVAILABLE')
            vixy_gap = float(vixy.get('gap', 0.0))

            vix_active = (vix_status == 'ACTIVE' and vix_price is not None and is_price_actionable('^VIX'))
            vixy_active = (vixy_status == 'ACTIVE' and is_price_actionable('VIXY'))

            # VIX fear alert — absolute trailing regime (VIX > 20.0) OR real-time velocity (VIXY ROC > 5.0%)
            if not vix_active and not vixy_active:
                print(f"   >>> FEAR STATUS: VIX/VIXY UNAVAILABLE {YELLOW}[DATA UNAVAILABLE]{RESET}")
            elif (vix_active and vix_price > 20.0) or (vixy_active and vixy_gap > 5.0):
                vix_str = f"VIX is {vix_price:.2f}" if vix_active else "VIX UNAVAILABLE"
                vixy_str = f" | VIXY Velocity {vixy_gap:+.2f}%" if vixy_active else ""
                print(f"   >>> FEAR ALERT:  {vix_str}{vixy_str} {RED}[CAUTION]{RESET}")
            else:
                vix_str = f"VIX is {vix_price:.2f}" if vix_active else "VIX UNAVAILABLE"
                vixy_str = f" | VIXY Velocity {vixy_gap:+.2f}%" if vixy_active else ""
                print(f"   >>> FEAR STATUS: {vix_str}{vixy_str} {GREEN}[STABLE]{RESET}")

            sleep_needed = REFRESH_RATE_SECONDS
            print("")

            final_output = build_daemon_final_output(
                data=data,
                status=status,
                authoritative_state_error=authoritative_state_error,
            )
            publish_global_state_snapshot(final_output)

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
    finally:
        try:
            shutdown_ok = daemon_heavy_coord.shutdown(timeout=5.0)
            if not shutdown_ok:
                logger.warning(
                    "[HeavyMarketDataCoordinator] Shutdown timed out after 5.0s during daemon exit"
                )
        except Exception as exc:
            logger.error(
                f"[HeavyMarketDataCoordinator] Error shutting down coordinator during daemon exit: {exc}"
            )

if __name__ == "__main__":
    if not os.path.exists("static"):
        os.makedirs("static")
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
    
    daemon_thread = threading.Thread(target=run_daemon, daemon=True)
    daemon_thread.start()
    
    print(f"{CYAN}Starting FastAPI server on http://localhost:8000{RESET}")
    uvicorn.run(app, host="127.0.0.1", port=8000)
