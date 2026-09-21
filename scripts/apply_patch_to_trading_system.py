import os
import sys
import re

def patch_trading_system():
    target_dir = r"C:\github\gem_trading_agent_system"
    fetch_stocks_path = os.path.join(target_dir, "python", "fetch_stocks.py")
    app_js_path = os.path.join(target_dir, "static", "app.js")
    styles_css_path = os.path.join(target_dir, "static", "styles.css")
    
    print(f"Checking target files in {target_dir}...")
    assert os.path.exists(fetch_stocks_path), f"Missing {fetch_stocks_path}"
    assert os.path.exists(app_js_path), f"Missing {app_js_path}"
    assert os.path.exists(styles_css_path), f"Missing {styles_css_path}"
    
    # 1. Patch python/fetch_stocks.py
    with open(fetch_stocks_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    pattern = r"def calculate_fib_forecast\(symbol: str, current_price: float, hist_df: pd\.DataFrame = None, atr: float = 0\.0, open_price: float = 0\.0, vwap: float = 0\.0, rvol: float = 0\.0, rsi: float = 0\.0\) -> dict:.*?(?=\ndef [a-zA-Z_]|\Z)"
    
    new_fib_func = '''def calculate_fib_forecast(symbol: str, current_price: float, hist_df: pd.DataFrame = None, atr: float = 0.0, open_price: float = 0.0, vwap: float = 0.0, rvol: float = 0.0, rsi: float = 0.0) -> dict:
    """
    Computes Trend-Based Fibonacci Extension resistance levels and optimal trim tranches
    strictly optimized for the active daily trading session and algorithmic front-running.
    Anchors to today's session high/low (HOD/LOD) and daily ATR volatility ceiling,
    preventing stale multi-week swing distortion.
    """
    try:
        p = float(current_price or 0.0)
        if p <= 0.0:
            return None

        # 1. Resolve today's session High (HOD), Low (LOD), and Open safely
        day_h = 0.0
        day_l = 0.0
        open_p = float(open_price or 0.0)
        atr_val = float(atr or 0.0)

        if hasattr(cache, 'day_high') and isinstance(cache.day_high, dict):
            day_h = float(cache.day_high.get(symbol, 0.0) or 0.0)
        if hasattr(cache, 'day_low') and isinstance(cache.day_low, dict):
            day_l = float(cache.day_low.get(symbol, 0.0) or 0.0)
        if open_p <= 0 and hasattr(cache, 'day_open') and isinstance(cache.day_open, dict):
            open_p = float(cache.day_open.get(symbol, 0.0) or 0.0)
        if open_p <= 0 and hasattr(cache, 'open_prices') and isinstance(cache.open_prices, dict):
            try:
                open_p = float(cache.open_prices.get(symbol, ("", 0.0))[1] or 0.0)
            except Exception:
                pass

        # Fallback to latest candle in hist_df (today's bar)
        if hist_df is not None and not hist_df.empty:
            latest_row = hist_df.iloc[-1]
            if day_h <= 0 and 'High' in latest_row:
                day_h = float(latest_row['High'])
            if day_l <= 0 and 'Low' in latest_row:
                day_l = float(latest_row['Low'])
            if open_p <= 0 and 'Open' in latest_row:
                open_p = float(latest_row['Open'])

        # If current price exceeds day_h or is below day_l, update
        if p > 0:
            if day_h <= 0 or p > day_h:
                day_h = p
            if day_l <= 0 or p < day_l:
                day_l = p
            if open_p <= 0:
                open_p = p

        # Anchor points strictly bounded to current daily trading session
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
            p_c = round(p_a + (impulse * 0.382), 2)

        # Fibonacci Extension Ratios
        ratios = [
            (0.618, "0.618", "0.618 Golden Ratio", "Initial Resistance / Friction Hurdle"),
            (0.786, "0.786", "0.786 Extension", "Harmonic Resistance Node"),
            (1.000, "1.000", "1.000 Measured Move (T1)", "T1: Measured Move (Trim 20-25%)"),
            (1.272, "1.272", "1.272 Expansion Peak", "Early Peak Resistance"),
            (1.618, "1.618", "1.618 Golden Peak Target (T2)", "T2: Primary Institutional Daily Peak (Trim 50%)"),
            (2.000, "2.000", "2.000 Momentum Target", "Momentum Expansion Target"),
            (2.618, "2.618", "2.618 Parabolic Blow-Off (T3)", "T3: Parabolic Runner Liquidation (Trim Remainder)")
        ]

        levels = {}
        next_res = None
        next_res_label = None
        dist_next_pct = None

        # If current price is below the Daily Session High (PB), PB is the immediate resistance ceiling
        if p > 0 and p_b > p:
            pb_dist = round(((p_b - p) / p) * 100, 2)
            next_res = round(p_b, 2)
            next_res_label = "Daily Peak"
            dist_next_pct = pb_dist

        # Calculate extension levels and 0.25% front-run limit prices
        for ratio, key, label, role in ratios:
            lvl_price = round(p_c + (ratio * impulse), 2)
            pct = round(((lvl_price - p) / p) * 100, 2)
            front_run = round(lvl_price * 0.9975, 2)  # 0.25% front-run discount
            levels[key] = {
                "price": lvl_price,
                "front_run_limit": front_run,
                "pct": pct,
                "label": label,
                "role": role,
                "is_above": lvl_price > p
            }
            if lvl_price > p and next_res is None:
                next_res = lvl_price
                next_res_label = label
                dist_next_pct = pct

        t1_price = levels["1.000"]["price"]
        t1_limit = levels["1.000"]["front_run_limit"]
        t2_price = levels["1.618"]["price"]
        t2_limit = levels["1.618"]["front_run_limit"]
        t3_price = levels["2.618"]["price"]
        t3_limit = levels["2.618"]["front_run_limit"]

        # ATR Daily Peak Barrier & Confluence
        daily_peak_atr = None
        atr_confluence = False
        confluence_level = None
        if open_p > 0 and atr_val > 0:
            daily_peak_atr = round(open_p + (1.25 * atr_val), 2)
            # Evaluate confluence with T1 (1.000), 1.272, or T2 (1.618)
            for check_key in ["1.000", "1.272", "1.618"]:
                chk_p = levels[check_key]["price"]
                if abs(daily_peak_atr - chk_p) / daily_peak_atr <= 0.018:
                    atr_confluence = True
                    confluence_level = levels[check_key]["label"]
                    break

        # Designate Primary Daily Peak Target
        if atr_confluence and confluence_level:
            daily_peak_target = levels["1.000"]["price"] if "1.000" in confluence_level else (levels["1.272"]["price"] if "1.272" in confluence_level else t2_price)
            daily_peak_limit = round(daily_peak_target * 0.9975, 2)
            daily_peak_label = confluence_level
        elif p < t1_limit:
            daily_peak_target = t1_price
            daily_peak_limit = t1_limit
            daily_peak_label = "T1: 100% Measured Move"
        elif p < t2_limit:
            daily_peak_target = t2_price
            daily_peak_limit = t2_limit
            daily_peak_label = "T2: 1.618 Golden Ratio Target"
        else:
            daily_peak_target = t3_price
            daily_peak_limit = t3_limit
            daily_peak_label = "T3: 2.618 Parabolic Blow-Off"

        dist_to_peak_pct = round(((daily_peak_target - p) / p) * 100, 2)

        # Detect Daily Peak Exhaustion Status
        if p >= t2_limit and (rsi > 80 or (vwap > 0 and (p - vwap) / vwap >= 0.10)):
            peak_status = "PARABOLIC_BLOW_OFF"
        elif p >= daily_peak_limit or abs(p - daily_peak_target) / daily_peak_target <= 0.003:
            peak_status = "AT_PEAK_RESISTANCE"
        elif p >= daily_peak_target * 0.985 and ((rvol > 0 and rvol < 1.5) or rsi > 72):
            peak_status = "PEAK_EXHAUSTED"
        elif abs(daily_peak_target - p) / p <= 0.015:
            peak_status = "PEAK_APPROACH"
        else:
            peak_status = "EXPANDING"

        # Structured Tranches for Daily Peak Profit-Taking
        tranches = {
            "tranche_1": {
                "ratio": 1.000,
                "target_price": t1_price,
                "front_run_limit": t1_limit,
                "trim_pct": "20%-25%",
                "role": "Initial Alpha-Harvest (Measured Move)",
                "status": "REACHED" if p >= t1_limit else "PENDING"
            },
            "tranche_2": {
                "ratio": 1.618,
                "target_price": t2_price,
                "front_run_limit": t2_limit,
                "trim_pct": "50% Cumulative",
                "role": "Primary Institutional Daily Peak Harvest",
                "status": "REACHED" if p >= t2_limit else "PENDING"
            },
            "tranche_3": {
                "ratio": 2.618,
                "target_price": t3_price,
                "front_run_limit": t3_limit,
                "trim_pct": "100% of Runners",
                "role": "Parabolic Climax / Runner Liquidation",
                "status": "REACHED" if p >= t3_limit else "PENDING"
            }
        }

        return {
            "pA": round(p_a, 2),
            "pB": round(p_b, 2),
            "pC": round(p_c, 2),
            "impulse": round(impulse, 2),
            "t1_100": t1_price,
            "t1_limit": t1_limit,
            "t1_pct": round(((t1_price - p) / p) * 100, 2),
            "t2_1618": t2_price,
            "t2_limit": t2_limit,
            "t2_pct": round(((t2_price - p) / p) * 100, 2),
            "t3_2618": t3_price,
            "t3_limit": t3_limit,
            "t3_pct": round(((t3_price - p) / p) * 100, 2),
            "next_resistance": next_res,
            "next_resistance_label": next_res_label,
            "distance_to_next_pct": dist_next_pct,
            "daily_peak_target": daily_peak_target,
            "daily_peak_limit": daily_peak_limit,
            "daily_peak_label": daily_peak_label,
            "daily_peak_status": peak_status,
            "distance_to_peak_pct": dist_to_peak_pct,
            "daily_peak_atr": daily_peak_atr,
            "atr_confluence": atr_confluence,
            "confluence_level": confluence_level,
            "levels": levels,
            "tranches": tranches
        }
    except Exception as e:
        return None
'''
    
    if re.search(pattern, content, flags=re.DOTALL):
        content = re.sub(pattern, new_fib_func + "\n", content, count=1, flags=re.DOTALL)
        with open(fetch_stocks_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Successfully patched {fetch_stocks_path}!")
    else:
        print(f"Warning: Could not match calculate_fib_forecast in {fetch_stocks_path}")

if __name__ == "__main__":
    patch_trading_system()
