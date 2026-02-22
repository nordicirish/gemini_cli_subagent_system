import yfinance as yf
import time

tickers = ['SPY', 'RKLB']
print(f"Fetching data for {tickers}...")

start = time.time()
for sym in tickers:
    try:
        t = yf.Ticker(sym)
        # Try to get options chain
        dates = t.options
        if not dates:
            print(f"{sym}: No options dates found.")
            continue
            
        # Get nearest expiration
        chain = t.option_chain(dates[0])
        calls = chain.calls
        
        # Find ATM option
        price = t.fast_info.last_price
        # Find strike closest to price
        atm_idx = (calls['strike'] - price).abs().idxmin()
        atm_iv = calls.loc[atm_idx, 'impliedVolatility']
        
        print(f"{sym}: Price {price:.2f}, ATM Strike {calls.loc[atm_idx, 'strike']}, IV {atm_iv:.4f}")
        
    except Exception as e:
        print(f"{sym}: Failed - {e}")

print(f"Time elapsed: {time.time() - start:.2f}s")
