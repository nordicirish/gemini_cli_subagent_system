# 📘 Trade Lessons Registry

## #OTHER
- **L-1:** G-01: MELT-UP REGIME & RSI DECOUPLING (ENH_86) - SPY RSI > 75 no longer blocks high-beta accumulation if VIX < 20.
- **L-2:** L-216: LONG GAMMA SHIELD OVERRIDE - The LONG_GAMMA dealer posture provides a systemic shield against ENH_16_B forced liquidations. However, if the asset suffers a catastrophic intraday structural failure (defined as triggering the SEC Rule 201 Short Sale Restriction by dropping >10%), the LONG_GAMMA shield is instantly invalidated. Market-maker hedging bands will decay; the Orchestrator must permit ENH_16_B trims to proceed despite the positive GEX profile.
- **L-3:** L-217: GEX-SSR CONFLICT PROTOCOL - If an asset is shielded by LONG_GAMMA but drops past the -10% SSR threshold, the Council must prioritize the SSR structural failure. The Consensus Pipeline must forcefully override the Neutral Structuralist and execute a risk trim.
- **L-4:** L-219: PRE-MARKET GAP-DOWN CONVICTION THRESHOLD - If gap > 3% and score < 0, 50% trim is mandatory pre-RTH.
- **L-5:** L-220: PERSISTENT STOP-LOSS TELEMETRY - The Execution Payload must persistently emit a 'trailing_stop_audit' block detailing exact anchor prices and percentage distances for any active holding displaying an RSI > 65 or trading > 2% above its daily VWAP.
