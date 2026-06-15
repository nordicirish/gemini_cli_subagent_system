```json\n{
  "trade_lessons": [
    {
      "id": 1,
      "rule": "L-219: PRE-MARKET GAP-DOWN CONVICTION THRESHOLD - If gap > 3% and score < 0, 50% trim is mandatory pre-RTH."
    },
    {
      "id": 2,
      "rule": "L-230: GAMMA_WHIPLASH_LOCK - If an asset experiences a LONG_GAMMA to SHORT_GAMMA and back to LONG_GAMMA dealer posture flip within a 30-minute window, the asset must be placed on a mandatory 15-minute `COOL_DOWN_LOCK` preventing any new capital allocation, as the structural shield is highly unstable and indicative of toxic/spoofed liquidity."
    },
    {
      "id": 3,
      "rule": "L-228: DILUTION_RESISTANCE_WALL - Assets with active recent equity offerings exhibit structural supply walls; avoid accumulation into these price zones without rVol > 2.0 confirmation."
    },
    {
      "id": 4,
      "rule": "L-232: BROKER_LATENCY_LIMIT_SWEEP - If a critical mechanical risk trim (e.g., >4% VWAP extension) fails to execute due to broker API latency or rejection, the Orchestrator MUST NOT re-queue the order at the identical or higher limit. It must instantly queue a 'Sweeping Limit Order' priced 0.5% below the current bid to guarantee extraction of extended alpha without violating the ROM_01 Market Order ban."
    },
    {
      "id": 5,
      "rule": "L-234: MANDATE_38_STRICT_ENFORCEMENT_TIMER - The Orchestrator MUST instantiate an explicit 'Time in Overbought Zone' timer for any asset crossing 72 RSI. Trailing VWAP anchors DO NOT supersede time-based overbought exhaustion mandates. The 15% alpha-harvest trim is absolute after 4 hours."
    },
    {
      "id": 6,
      "rule": "L-236: ABSOLUTE_PARABOLIC_GRAVITY - Regardless of active SSR status, LONG_GAMMA shielding, or user manual overrides, if an asset exceeds a +12.0% extension from its intraday VWAP anchor alongside an RSI > 80, the Orchestrator MUST forcefully execute a minimum 15% tactical sweep trim. This serves as an un-bypassable terminal gravity layer against total narrative capture."
    },
    {
      "id": 7,
      "rule": "L-237: OVERRIDE_PENALTY_LOCK - If a user manually overrides an automated MANDATE_38 (Time-In-Overbought) or ENH_112 liquidation within the final 30 minutes of RTH, the system must automatically widen the Day-2 pre-market trailing stop by 2% to absorb the mathematically guaranteed exhaustion gap-down without prematurely shaking out the core position."
    },
    {
      "id": 8,
      "rule": "L-238: PARABOLIC_VWAP_CASCADES - If an asset previously exceeded a +10% VWAP extension, suffered a manual user override of a required trim, and subsequently breaches its VWAP floor within the following 48 hours while the broader index is in SHORT_GAMMA, the Orchestrator MUST execute an immediate 50% punitive liquidity sweep (superseding the standard 25% trim) to instantly neutralize the compounded tail-risk."
    },
    {
      "id": 9,
      "rule": "L-239: PRE_MARKET_SHORT_GAMMA_BLEED - If an asset drops >4% in the pre-market session while dealer posture shifts to SHORT_GAMMA, the Orchestrator MUST immediately advise a manual 25% risk trim at the RTH open to preempt liquidity cascades, overriding standard RTH VWAP confirmation delays."
    },
    {
      "id": 10,
      "rule": "L-242: STRICT_ATTRIBUTION_INTEGRITY - The system MUST NOT falsely attribute user-provided insights, correlations, or macro observations to its own autonomous scanning capabilities. If the user introduces a variable that the system previously missed, the system must explicitly log the miss as a `forensic_blindspot` and attribute the discovery exclusively to user input. Falsifying system competence degrades SSoT reliability and is strictly forbidden."
    },
    {
      "id": 11,
      "rule": "L-243: FRICTION_OVERRIDE_ON_STRUCTURAL_FAILURE - If an asset exhibits structural failure (defined as losing its daily VWAP floor accompanied by rising distribution volume or negative pre-market gap metrics), the Orchestrator MUST override standard FX/commission friction hurdles (such as the 0.6% EUR round-trip constraint) and execute an immediate defensive exit. Capital preservation supersedes transactional friction optimization."
    },
    {
      "id": 12,
      "rule": "L-244: HIGH_ATR_STAGGERED_TRIM - When executing a mandated alpha-harvest trim on an asset exhibiting high volatility (ATR > 8%) during a parabolic extension, the Orchestrator MUST default to a multi-tranche staggered limit sequence (e.g., slicing the trim across spot +0.5% and spot -0.5%) rather than a single-block market-sweep, provided the underlying VWAP support floor remains intact."
    },
    {
      "id": 13,
      "rule": "L-245: INDEX_SHORT_GAMMA_LOCK - When broad index markers (SPY) exhibit short gamma architectures, the entry-confirmation latency on manual gates rises by 400%. Automated defensive tranches must scale size down by 25% to accommodate downstream execution lag."
    },
    {
      "id": 14,
      "rule": "L-246: AUTOMATED_GAMMA_CASCADE_OVERRIDE - During a SHORT_GAMMA index regime, if an asset breaches a >2% trailing VWAP extension stop, the Orchestrator must bypass manual confirmation gates and automatically execute the required risk-reduction trim to prevent catastrophic alpha bleed from execution latency."
    },
    {
      "id": 15,
      "rule": "L-247: OPENING_RANGE_WHIPSAW_SHIELD - Any structural VWAP breakdown occurring before 10:30 AM EST must require a subsequent 15-minute time confirmation or a >5% distance extension before triggering a hard EXIT. This mitigates false-positive mechanical stops during artificial market-maker liquidity flushes."
    },
    {
      "id": 16,
      "rule": "L-248: [CODIFIED: ENH_88_VULNERABILITY] CATALYST_VWAP_DECAY_PUNISHER: If an asset gaps down or fails to reclaim its VWAP floor within 60 minutes of an unquantified PR catalyst, execution must override ENH_88 OEM Multiplier assumptions and mandate a 25% risk trim to preempt short-gamma distribution."
    }
  ]
}\n```