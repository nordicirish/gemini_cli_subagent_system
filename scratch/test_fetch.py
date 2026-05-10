import sys
import fetch_stocks
import traceback

sys.stdout.reconfigure(line_buffering=True)
try:
    print("Testing fetch loop...")
    fetch_stocks.fetch_loop()
except Exception as e:
    print("ERROR:")
    traceback.print_exc()
