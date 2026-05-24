# 📘 Trade Lessons Registry

## #OTHER
- **L-1:** G-01: MELT-UP REGIME & RSI DECOUPLING (ENH_86) - SPY RSI > 75 no longer blocks high-beta accumulation if VIX < 20.
- **L-2:** L-216: LONG GAMMA SHIELD OVERRIDE - The LONG_GAMMA dealer posture provides a systemic shield against ENH_16_B forced liquidations. However, if the asset suffers a catastrophic intraday structural failure (defined as triggering the SEC Rule 201 Short Sale Restriction by dropping >10%), the LONG_GAMMA shield is instantly invalidated. Market-maker hedging bands will decay; the Orchestrator must permit ENH_16_B trims to proceed despite the positive GEX profile.
- **L-3:** L-217: GEX-SSR CONFLICT PROTOCOL - If an asset is shielded by LONG_GAMMA but drops past the -10% SSR threshold, the Council must prioritize the SSR structural failure. The Consensus Pipeline must forcefully override the Neutral Structuralist and execute a risk trim.
- **L-4:** L-219: PRE-MARKET GAP-DOWN CONVICTION THRESHOLD - If gap > 3% and score < 0, 50% trim is mandatory pre-RTH.
- **L-5:** L-220: PERSISTENT STOP-LOSS TELEMETRY - The Execution Payload must persistently emit a 'trailing_stop_audit' block detailing exact anchor prices and percentage distances for any active holding displaying an RSI > 65 or trading > 2% above its daily VWAP.
- **L-6:** L-221: OVEREXTENSION CATALYST LOCK - If an active portfolio asset achieves a maximum quantitative score (10), displays an RSI > 70, and trades > 3% above its intraday VWAP within 4 hours of a systemic mega-cap earnings event (e.g., NVDA), the Consensus Pipeline must automatically bypass LONG_GAMMA hold signals and queue a minimum 33% profit-taking trim to eliminate hardware-correlated beta risk.
- **L-7:** L-222: SYMPATHY MOMENTUM SHIELD BYPASS - If an asset's upward momentum is forensically flagged by the Data Analyst as 'sympathy-driven' or 'collateral drift' (lacking an idiosyncratic news catalyst), AND the asset trades > 3% above its intraday VWAP with an RSI > 65, the LONG_GAMMA hold shield is structurally bypassed. The system MUST queue a mechanical 25% profit-taking trim to lock in transient alpha before algorithmic mean-reversion occurs.
- **L-8:** ENH_105: Melt-Up Regime & RSI Decoupling - SPY RSI > 75 no longer blocks high-beta accumulation if VIX < 20.
- **L-9:** ENH_106: Long Gamma Shield Override - If asset drops >10% and triggers SSR, Long Gamma shield is invalidated.
- **L-10:** ENH_107: GEX-SSR Conflict Protocol - Prioritize SSR structural failure over GEX stabilization.
- **L-11:** ENH_108: Persistent Stop-Loss Telemetry - Mandatory audit blocks for RSI > 65 or VWAP extension > 2%.
