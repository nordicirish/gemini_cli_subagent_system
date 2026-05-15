
import asyncio
import yfinance as yf
import pandas as pd
import numpy as np
import json
from datetime import datetime
from zoneinfo import ZoneInfo

# Mock some parts of fetch_stocks to test the logic
class Cache:
    def __init__(self):
        self.prices = {}
        self.session_change = {}
        self.gaps = {}
        self.technicals = {}
        self.session = {}

cache = Cache()

def get_market_status():
    now = datetime.now(ZoneInfo("America/New_York"))
    if now.weekday() >= 5: return "CLOSED"
    d_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    d_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    d_pre = now.replace(hour=4, minute=0, second=0, microsecond=0)
    d_post = now.replace(hour=20, minute=0, second=0, microsecond=0)

    if now < d_pre: return "CLOSED"
    if now < d_open: return "PRE-MARKET"
    if now < d_close: return "OPEN"
    if now < d_post: return "AFTER-HOURS"
    return "CLOSED"

def test_logic(ticker_symbol):
    status = get_market_status()
    print(f"Current Status: {status}")
    t_obj = yf.Ticker(ticker_symbol)
    
    # Simulate update_history_and_technicals
    hist = t_obj.history(period="5d")
    last_reg_close = 0.0
    try:
        last_reg_close = float(t_obj.fast_info.previous_close)
    except:
        last_reg_close = float(hist['Close'].iloc[-2])
    
    print(f"Last Reg Close: {last_reg_close}")
    
    # Simulate update_price_tick
    quote = t_obj.info
    pre_price = quote.get('preMarketPrice')
    reg_price = quote.get('regularMarketPrice')
    reg_open = quote.get('regularMarketOpen')
    
    print(f"Pre Price: {pre_price}")
    print(f"Reg Price: {reg_price}")
    print(f"Reg Open: {reg_open}")
    
    price = 0.0
    if status == "PRE-MARKET" and pre_price:
        price = float(pre_price)
    elif reg_price:
        price = float(reg_price)
    
    print(f"Selected Price: {price}")
    
    session_change = 0.0
    if last_reg_close > 0:
        session_change = ((price - last_reg_close) / last_reg_close) * 100
    
    print(f"Session Change %: {session_change}")
    
    true_gap_price = price
    if status == "OPEN" and reg_open:
        true_gap_price = float(reg_open)
    elif status == "PRE-MARKET" and pre_price:
        true_gap_price = float(pre_price)
        
    raw_gap = ((true_gap_price - last_reg_close) / last_reg_close) * 100
    print(f"Gap %: {raw_gap}")

if __name__ == "__main__":
    test_logic("RCAT")
