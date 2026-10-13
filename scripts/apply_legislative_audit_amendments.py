"""
Audits and applies AG-01 through AG-06 rectifications across:
1. gem_trading_rules/rules.md
2. engine_instructions/*.md
3. python/fetch_stocks.py
4. README.md, INSTRUCTIONS.md, antigravity.md
"""

import os
import re
import glob

TARGET_VERSION = "v11.54-Legislative-Audit-Harmonization-Sync"

def update_rules_md():
    path = "gem_trading_rules/rules.md"
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # ----------------------------------------------------
    # AG-01: RSI Threshold Harmonization
    # ----------------------------------------------------
    content = content.replace(
        "- [ENH_104](#enh_104): PERSISTENT STOP-LOSS TELEMETRY (ENH_104_AMENDMENT_SHORT_GAMMA_SNAP) - Trailing stop audit emission protocol and Short Gamma Snap execution (25% mechanical risk trim on VWAP anchor breach when SPY or asset in SHORT_GAMMA).",
        "- [ENH_104](#enh_104): PERSISTENT STOP-LOSS TELEMETRY (ENH_104_AMENDMENT_SHORT_GAMMA_SNAP) - Trailing stop audit emission protocol (RSI > 70 or VWAP_DIST > 2.0% or active holding) and Short Gamma Snap execution (25% mechanical risk trim on VWAP anchor breach when SPY or asset in SHORT_GAMMA)."
    )
    
    # In Enh registry: MANDATE_37 / ENH_110 to RSI > 75
    content = content.replace(
        "RSI > 65, the momentum is quantitatively classified as \"sympathy-driven\"",
        "RSI > 75, the momentum is quantitatively classified as \"sympathy-driven\""
    )
    content = content.replace(
        "RSI > 65, the momentum is quantitatively classified as 'sympathy-driven'",
        "RSI > 75, the momentum is quantitatively classified as 'sympathy-driven'"
    )
    
    # ENH_111 to RSI > 75
    content = content.replace(
        "- [ENH_111](#enh_111): GAMMA FLICKER PREEMPTION - If an asset with an RSI > 70 experiences a transient SHORT_GAMMA flip",
        "- [ENH_111](#enh_111): GAMMA FLICKER PREEMPTION - If an asset with an RSI > 75 experiences a transient SHORT_GAMMA flip"
    )
    content = content.replace(
        "### [ENH_111] GAMMA FLICKER PREEMPTION\n- **Status:** ACTIVE\n- **Content:** GAMMA FLICKER PREEMPTION - If an asset with an RSI > 70 experiences a transient SHORT_GAMMA flip",
        "### [ENH_111] GAMMA FLICKER PREEMPTION\n- **Status:** ACTIVE\n- **Content:** GAMMA FLICKER PREEMPTION - If an asset with an RSI > 75 experiences a transient SHORT_GAMMA flip"
    )

    # MANDATE_38 to crossing 75 RSI
    content = content.replace(
        "crossing 72 RSI. A 15% alpha-harvest trim directive is absolute in the EXECUTION_PAYLOAD after 4 hours",
        "crossing 75 RSI. A 15% alpha-harvest trim directive is absolute in the EXECUTION_PAYLOAD after 4 hours"
    )

    # MANDATE_40 to RSI > 85
    content = content.replace(
        "alongside an RSI > 80, the Orchestrator MUST emit a mandatory 15% tactical sweep trim directive in the EXECUTION_PAYLOAD",
        "alongside an RSI > 85, the Orchestrator MUST emit a mandatory 15% tactical sweep trim directive in the EXECUTION_PAYLOAD"
    )
    content = content.replace(
        "alongside RSI > 80 per MANDATE_40 / L-236.",
        "alongside RSI > 85 per MANDATE_40 / L-236."
    )

    # MANDATE_36 / ENH_104 text layouts in rules.md
    content = content.replace(
        "displaying an RSI > 65 or trading > 2% above its daily VWAP.",
        "displaying an RSI > 70 or trading > 2% above its daily VWAP (or active holding)."
    )
    content = content.replace(
        "RSI > 65 (overbought momentum zone), OR",
        "RSI > 70 (overbought momentum zone), OR"
    )
    content = content.replace(
        "`trigger_condition`: The condition that activated this block (`RSI > 65` or `VWAP_DIST > 2%` or `BOTH`).",
        "`trigger_condition`: The condition that activated this block (`RSI > 70` or `VWAP_DIST > 2%` or `BOTH` or `ACTIVE_HOLDING`)."
    )
    content = content.replace(
        "Status: ACTIVE (Trigger: [RSI R > 65] | [VWAP_DIST > 2%] | [BOTH])",
        "Status: ACTIVE (Trigger: [RSI R > 70] | [VWAP_DIST > 2%] | [BOTH] | [ACTIVE_HOLDING])"
    )
    content = content.replace(
        "Status: INACTIVE (RSI [R] < 65)",
        "Status: INACTIVE (RSI [R] <= 70)"
    )
    content = content.replace(
        "Status: INACTIVE (RSI R < 65)",
        "Status: INACTIVE (RSI R <= 70)"
    )

    # ----------------------------------------------------
    # AG-02: Primary Key ID Collisions
    # ----------------------------------------------------
    # 1. MANDATE_22: Dynamic Thinking Level Optimization -> MANDATE_22_B
    content = content.replace(
        "- [MANDATE_22](#mandate_22): SSOT_EMISSION_PROTOCOL (Split-Delivery Logic)",
        "- [MANDATE_22](#mandate_22): SSOT_EMISSION_PROTOCOL (Split-Delivery Logic)\n- [MANDATE_22_B](#mandate_22_b): Dynamic Thinking Level Optimization"
    )
    content = content.replace(
        '<a name="mandate_22"></a>\n- **[MANDATE_22 - Dynamic Thinking Level Optimization]**',
        '<a name="mandate_22_b"></a>\n- **[MANDATE_22_B - Dynamic Thinking Level Optimization]**'
    )

    # 2. MANDATE_31: Abolition of Passive Structural Holds -> MANDATE_31_B
    content = content.replace(
        "- [MANDATE_31](#mandate_31): ABOLITION_OF_PASSIVE_STRUCTURAL_HOLDS (VWAP Risk Enforcement)",
        "- [MANDATE_31](#mandate_31): CASH_RECONCILIATION\n- [MANDATE_31_B](#mandate_31_b): ABOLITION_OF_PASSIVE_STRUCTURAL_HOLDS (VWAP Risk Enforcement)"
    )
    content = content.replace(
        '<a name="mandate_31_abolition_of_passive_structural_holds"></a>\n*   **[MANDATE_31_ABOLITION_OF_PASSIVE_STRUCTURAL_HOLDS]**',
        '<a name="mandate_31_b"></a>\n*   **[MANDATE_31_B_ABOLITION_OF_PASSIVE_STRUCTURAL_HOLDS]**'
    )
    content = content.replace(
        "MANDATE_31 (Abolition of Passive Structural Holds)",
        "MANDATE_31_B (Abolition of Passive Structural Holds)"
    )

    # 3. Consolidate LONG_GAMMA_SSR_OVERRIDE under MANDATE_35 (ENH_106/ENH_107)
    # MANDATE_34 exclusively INSTITUTIONAL_PEG_AND_AH_GRAVITY
    # MANDATE_36 exclusively PERSISTENT_STOP-LOSS_TELEMETRY
    content = content.replace(
        "- [MANDATE_36_ENH_104](#mandate_36_enh_104): PERSISTENT STOP-LOSS TELEMETRY - The Execution Payload must persistently emit a 'trailing_stop_audit' block detailing exact anchor prices and percentage distances for any active holding displaying an RSI > 70 or trading > 2% above its daily VWAP (or active holding).",
        "- [MANDATE_35](#mandate_35): LONG_GAMMA_SSR_OVERRIDE (ENH_106 / ENH_107 / GEX-SSR Conflict Resolution)\n- [MANDATE_36](#mandate_36): PERSISTENT STOP-LOSS TELEMETRY (ENH_104 / ENH_108) - Trailing stop audit emission protocol (RSI > 70 or VWAP_DIST > 2% or active holding)."
    )
    content = content.replace(
        '<a name="mandate_36_enh_104_persistent_stop"></a>\n*   **[MANDATE_36_ENH_104_PERSISTENT_STOP-LOSS_TELEMETRY]**',
        '<a name="mandate_36"></a>\n*   **[MANDATE_36_PERSISTENT_STOP-LOSS_TELEMETRY]**'
    )

    old_m34_m35_chunk = """<a name="mandate_34"></a>
### [MANDATE_34] LONG GAMMA SHIELD OVERRIDE
- **Status:** ACTIVE
- **Content:** The LONG_GAMMA dealer posture provides a systemic shield against standard forced liquidations. However, if an asset suffers a catastrophic intraday structural failure (defined as dropping >10% and triggering the SEC Rule 201 Short Sale Restriction), the LONG_GAMMA shield is **instantly mathematically invalidated** due to the collapse of market-maker hedging bands. The Orchestrator MUST immediately permit risk-reduction trims to proceed despite the positive GEX profile.
- **Rationale:** Confirmed structural hierarchy of market mechanics — regulatory halts destroy options-derived liquidity buffers. This is the canonical invalidation mechanism; ENH_16_D and MANDATE_35 are companion enforcement rules within the same authority chain.
- **Cross-Reference:** ENH_16_D (mechanical trim trigger), MANDATE_35 (consensus pipeline override).
- **Note:** Proposed MANDATE_46 was REJECTED as duplicate of this rule and ENH_16_D. This entry has been augmented with the precision language from that proposal.

<a name="mandate_35"></a>
### [MANDATE_35] GEX-SSR CONFLICT PROTOCOL
- **Status:** ACTIVE
- **Content:** If an asset is shielded by LONG_GAMMA but drops past the -10% SSR threshold, the Council must prioritize the SSR structural failure. The Consensus Pipeline must forcefully override the Neutral Structuralist and emit a risk trim directive in the EXECUTION_PAYLOAD, alerting the user to physically execute the trim.
- **Justification:** Resolves pipeline deadlocks between quantitative GEX readings and regulatory SEC circuit breakers."""

    new_m35_chunk = """<a name="mandate_35"></a>
### [MANDATE_35] LONG_GAMMA_SSR_OVERRIDE (ENH_106 / ENH_107)
- **Status:** ACTIVE
- **Rule ID:** MANDATE_35_LONG_GAMMA_SSR_OVERRIDE
- **Classification:** SYSTEMIC_RISK_MANDATE
- **Content:** The LONG_GAMMA dealer posture provides a systemic shield against standard forced liquidations. However, if an asset suffers a catastrophic intraday structural failure (defined as dropping >10% and triggering the SEC Rule 201 Short Sale Restriction), the LONG_GAMMA shield is **instantly mathematically invalidated** due to the collapse of market-maker hedging bands. The Consensus Pipeline and Orchestrator must prioritize the SSR structural failure over GEX stabilization and emit a mandatory mechanical risk trim directive in the EXECUTION_PAYLOAD, alerting the user to physically execute the trim.
- **Rationale:** Confirmed structural hierarchy of market mechanics — regulatory halts destroy options-derived liquidity buffers. Consolidates GEX-SSR Conflict Protocol (ENH_106 / ENH_107) and Long Gamma Shield Override into canonical primary key MANDATE_35. MANDATE_34 is exclusively reserved for INSTITUTIONAL_PEG_AND_AH_GRAVITY.
- **Cross-Reference:** ENH_16_D (mechanical trim trigger), ENH_16_E, ENH_106, ENH_107, `rule_enforcer_engine.md`, `gex_engine.md`."""

    content = content.replace(old_m34_m35_chunk, new_m35_chunk)

    old_second_chunk = """<a name="mandate_35"></a>
### [MANDATE_35] LONG GAMMA SHIELD OVERRIDE
- **Status:** ACTIVE
- **Content:** LONG_GAMMA SHIELD OVERRIDE: If an asset suffers a structural failure (>10% drop triggering SSR), the LONG_GAMMA shield is nullified. Immediate mechanical trim required.
- **Justification:** Forensic evidence shows dealer hedging bands decay rapidly during SSR events; positive GEX becomes misleading.

<a name="mandate_36"></a>
### [MANDATE_36] GEX-SSR CONFLICT PROTOCOL
- **Status:** ACTIVE
- **Content:** GEX-SSR CONFLICT PROTOCOL: SSR structural failure takes precedence over GEX stabilization. Consensus Pipeline must force trim.
- **Justification:** Prevents pipeline paralysis when technical shields conflict with regulatory circuit breakers."""

    content = content.replace(old_second_chunk, "")

    # Re-assign MERTON_SIZING_INTEGRATION to ENH_118_B
    content = content.replace(
        "- [ENH_118](#enh_118): Overnight Gap-Risk Liquidity Gate",
        "- [ENH_118](#enh_118): Overnight Gap-Risk Liquidity Gate\n- [ENH_118_B](#enh_118_b): MERTON_SIZING_INTEGRATION_AND_HEDGING_DEMAND"
    )
    content = content.replace(
        '<a name="enh_118"></a>\n### [ENH_118] MERTON_SIZING_INTEGRATION_AND_HEDGING_DEMAND',
        '<a name="enh_118_b"></a>\n### [ENH_118_B] MERTON_SIZING_INTEGRATION_AND_HEDGING_DEMAND'
    )

    if "### [ENH_118] OVERNIGHT_GAP_RISK_LIQUIDITY_GATE" not in content and "### [ENH_118]" not in content:
        enh_118_def = """<a name="enh_118"></a>
### [ENH_118] OVERNIGHT_GAP_RISK_LIQUIDITY_GATE
- **Status:** ACTIVE
- **Rule ID:** ENH_118_OVERNIGHT_GAP_RISK_LIQUIDITY_GATE
- **Content:** Halve recommended overnight position sizing (0.5x multiplier) if the target asset's Implied Volatility (IV) exceeds its historical 50-day norm.
- **Cross-Reference:** `rule_enforcer_engine.md`, `execution.md`.\n\n"""
        content = content.replace('<a name="enh_118_b"></a>', enh_118_def + '<a name="enh_118_b"></a>')

    # Remove duplicate ENH_119 (Execution) in favor of canonical ENH_248
    content = content.replace(
        '- [ENH_119 (Execution)](#enh_119_decay): [CODIFIED: L-248] CATALYST_VWAP_DECAY_PUNISHER - If an asset gaps down or fails to reclaim its VWAP floor within 60 minutes of a PR catalyst, execution must override base momentum assumptions and emit a mandatory 25% risk trim directive to preempt short-gamma distribution.\n',
        ''
    )
    
    old_enh_119_decay = """<a name="enh_119_decay"></a>
### [ENH_119] CATALYST_VWAP_DECAY_PUNISHER
- **Status:** ACTIVE
- **Content:** [CODIFIED: L-248] CATALYST_VWAP_DECAY_PUNISHER: If an asset gaps down or fails to reclaim its VWAP floor within 60 minutes of a PR catalyst, execution must override base momentum assumptions and emit a mandatory 25% risk trim directive to preempt short-gamma distribution.
- **Justification:** Proven durable via DFTX structural failure. The AI Council frequently exhibits "narrative capture" on Torque-10 catalysts; this rule acts as an un-bypassable mechanical circuit breaker against post-PR dilution cascades."""

    content = content.replace(old_enh_119_decay, "")

    # ----------------------------------------------------
    # AG-03: Deprecated health_score (0-100) Remnants
    # ----------------------------------------------------
    content = content.replace(
        "        - **Health Score:** FLOAT\n",
        "        - **Score:** INT (-6 to +6)\n"
    )

    old_health_protocol = """## Health Score Protocol
- **Authority:** CANONICAL — Gemini_Gem_Technical_Validator MUST reference this protocol.
- **Base Score:** 100
- **Penalties:**
  - 
    - **Condition:** S_A < 0.65
    - **Deduction:** 100
    - **Label:** Consensus Failure/Deadlock
  - 
    - **Condition:** Risk_Regime == CRITICAL
    - **Deduction:** 50
    - **Label:** ENH_08 Critical Regime
    - **Enh Ref:** ENH_08
  - 
    - **Condition:** Trade_State == EXIT AND Drawdown_Gap < 1.5% AND volatility_override == FALSE
    - **Deduction:** 30
    - **Label:** Alpha-Friction Violation (churn only — bypassed during Protective Exit Override)
    - **Enh Ref:** ENH_FIN_02
  - 
    - **Condition:** fields labeled Unverified
    - **Deduction:** 20
    - **Label:** MANDATE_04 Drift
  - 
    - **Condition:** Supply Chain flags active
    - **Deduction:** 15
    - **Label:** Lesson 205 Supply Chain
    - **Enh Ref:** Lesson 205
  - 
    - **Condition:** Signal/Trend mismatch
    - **Deduction:** 15
    - **Label:** Drift Detection
  - 
    - **Condition:** rvol < 0.5 OR rvol > 4.0
    - **Deduction:** 10
    - **Label:** rVol Anomaly
  - 
    - **Condition:** Failure to reclaim Post-Catalyst High
    - **Deduction:** 3
    - **Label:** ENH_70 Adaptive Decay (per session)
    - **Enh Ref:** ENH_70"""

    new_health_protocol = """## Score Protocol (ENH_251 Unified Gradient)
- **Authority:** CANONICAL — Gemini_Gem_Technical_Validator MUST reference this protocol.
- **Status:** ENH_251 SUPERSEDED — The legacy 0-100 `health_score` is completely deprecated.
- **Canonical Scale:** -6 to +6 integer score.
- **Zero-Floor Policy (GATE_REG_01):** Any asset displaying `score < 0` is strictly disqualified from new capital deployment / long entries.
- **Score Minimum:** 0 (Threshold for long-entry permissibility)."""

    content = content.replace(old_health_protocol, new_health_protocol)

    old_health_thresh = """- **Health Score Minimum:**
  - **Value:** 40
  - **Usage:** Below this, regulatory gate blocks execution
  - **Used By:**
    - execution
    - regulatory_gate"""

    new_health_thresh = """- **Score Minimum:**
  - **Value:** 0
  - **Usage:** Below this (score < 0), regulatory gate (GATE_REG_01) blocks new long entries per ENH_251.
  - **Used By:**
    - execution
    - regulatory_gate"""

    content = content.replace(old_health_thresh, new_health_thresh)

    # ----------------------------------------------------
    # AG-04: MANDATE_52, ENH_02, ENH_261
    # ----------------------------------------------------
    content = content.replace(
        "- [ENH_259](#enh_259): PRE_EVENT_GEX_DEGRADATION_SENTINEL",
        "- [ENH_259](#enh_259): PRE_EVENT_GEX_DEGRADATION_SENTINEL\n- [ENH_261](#enh_261): RTH_SESSION_GATE_FOR_GAMMA_CASCADES (MANDATE_52 Pre-Market Protection)\n- [ENH_262](#enh_262): GAMMA_FLIP_INTERPOLATION_BOUND_GUARD (Outlier Quarantine Protocol)"
    )

    content = content.replace(
        "- **[ENH_02 - Session-Aware Indicator Validation]**\n  - **Instruction:** Label PM/AH signals as 'Low Confidence Artifacts' unless compared to off-hour averages.",
        "- **[ENH_02 - Session-Aware Indicator Validation]**\n  - **Instruction:** Label PM/AH signals as 'Low Confidence Artifacts' unless compared to off-hour averages. RTH indicators (such as intraday VWAP in MANDATE_52) must never be evaluated against stale closing benchmarks during pre-market when pre_market_volume == 'UNAVAILABLE' (Reference ENH_261)."
    )

    content = content.replace(
        "- [MANDATE_52](#mandate_52): MECHANICAL_GAMMA_CASCADE_OVERRIDE - During a confirmed SHORT_GAMMA index regime (SPY Net GEX < 0), if an active portfolio asset breaches a >2.0% trailing VWAP extension stop, the Orchestrator is strictly prohibited from holding, staging deliberative debates, or awaiting candle closes. The Orchestrator must instantly emit a mandatory, non-negotiable risk-reduction 'TRIM' directive (minimum 25%) in the EXECUTION_PAYLOAD structured as a sweeping limit order 0.5% below bid.",
        "- [MANDATE_52](#mandate_52): MECHANICAL_GAMMA_CASCADE_OVERRIDE - During a confirmed SHORT_GAMMA index regime (SPY Net GEX < 0) strictly during RTH (status == 'OPEN' per ENH_261), if an active portfolio asset breaches a >2.0% trailing VWAP extension stop, the Orchestrator is strictly prohibited from holding, staging deliberative debates, or awaiting candle closes. The Orchestrator must instantly emit a mandatory, non-negotiable risk-reduction 'TRIM' directive (minimum 25%) in the EXECUTION_PAYLOAD structured as a sweeping limit order 0.5% below bid. In PRE-MARKET, trims are governed exclusively by ENH_16_F and ENH_115."
    )

    content = content.replace(
        "- **Content:** [MANDATE_52] MECHANICAL_GAMMA_CASCADE_OVERRIDE - During a confirmed SHORT_GAMMA index regime (SPY Net GEX < 0), if an active portfolio asset breaches a >2.0% trailing VWAP extension stop, the Orchestrator is strictly prohibited from holding, staging deliberative debates, or awaiting candle closes. The Orchestrator must instantly emit a mandatory, non-negotiable risk-reduction 'TRIM' directive (minimum 25%) in the EXECUTION_PAYLOAD structured as a sweeping limit order 0.5% below bid.",
        "- **Content:** [MANDATE_52] MECHANICAL_GAMMA_CASCADE_OVERRIDE - During a confirmed SHORT_GAMMA index regime (SPY Net GEX < 0), if an active portfolio asset breaches a >2.0% trailing VWAP extension stop, the Orchestrator is strictly prohibited from holding, staging deliberative debates, or awaiting candle closes. The Orchestrator must instantly emit a mandatory, non-negotiable risk-reduction 'TRIM' directive (minimum 25%) in the EXECUTION_PAYLOAD structured as a sweeping limit order 0.5% below bid. **Session Restriction (ENH_261):** Strictly restricted to status == 'OPEN' (RTH) with verified session volume. In PRE-MARKET, MANDATE_52 is inactive; pre-market risk trims are governed exclusively by ENH_16_F (-2.50% gap in SPY Short Gamma) and ENH_115 (-4.0% drop)."
    )

    enh_261_262_text = """
---

<a name="enh_261"></a>
### [ENH_261] RTH_SESSION_GATE_FOR_GAMMA_CASCADES
- **Status:** ACTIVE
- **Rule ID:** ENH_261_RTH_SESSION_GATE_FOR_GAMMA_CASCADES
- **Classification:** EXECUTION_SESSION_GATE
- **Content:** [ENH_261] RTH_SESSION_GATE_FOR_GAMMA_CASCADES — The mechanical VWAP cascade override defined in MANDATE_52 is strictly restricted to regular trading hours (`status == "OPEN"`) with verified session volume (`volume > 0` and `pre_market_volume != "UNAVAILABLE"`). MANDATE_52 is strictly prohibited from firing during the PRE-MARKET session or calculating distance against yesterday's stale closing VWAP. Pre-market risk trims are governed exclusively by ENH_16_F (-2.50% gap under SPY Short Gamma) and ENH_115 (-4.0% pre-market drop).
- **Justification:** Prevents catastrophic pre-market cascade misfires where illiquid off-hours prints against stale prior-day VWAP force premature liquidations before price discovery begins.
- **Cross-Reference:** MANDATE_52, ENH_02 (Session-Aware Indicator Validation), ENH_16_F, ENH_115, `execution.md`, `terminal.md`, `rule_enforcer_engine.md`.

---

<a name="enh_262"></a>
### [ENH_262] GAMMA_FLIP_INTERPOLATION_BOUND_GUARD
- **Status:** ACTIVE
- **Rule ID:** ENH_262_GAMMA_FLIP_INTERPOLATION_BOUND_GUARD
- **Classification:** DATA_INTEGRITY_PROTOCOL
- **Content:** [ENH_262] GAMMA_FLIP_INTERPOLATION_BOUND_GUARD — Mathematical bound check for option chain gamma flip interpolation. If `abs(gamma_flip_price - price) / price > 0.15` (for single equities) or `> 0.05` (for broad market indices such as SPY), the calculated flip price is flagged as an interpolation outlier caused by sparse option strikes. The engine MUST set `data_quality_flag = "INTERPOLATION_OUTLIER"`, quarantine `gamma_flip_price` (setting it to `0.0`), and fall back strictly to the sign of `net_gex_total` and ENH_20 Synthetic GEX for dealer posture classification.
- **Justification:** Prevents ungrounded mathematical artifacts ($428.86 on SPY, $11.35 on UMAC) from corrupting the SSoT during sparse options chains.
- **Cross-Reference:** MANDATE_51 / ENH_256 (Fail-Fast Data Invariants), `gex_engine.md`, `rule_enforcer_engine.md`.
"""
    if "### [ENH_261] RTH_SESSION_GATE_FOR_GAMMA_CASCADES" not in content:
        content = content + enh_261_262_text

    content = content.replace(
        "or schema structure is corrupted or missing, the system and engines MUST fail fast: trigger an explicit `CRITICAL_SCHEMA_VIOLATION`, `DATA_CORRUPTION_VETO`, or `HARD_HALT`. No speculative inference or downstream trade formulation is permitted on corrupted data.",
        "or schema structure is corrupted or missing, the system and engines MUST fail fast: trigger an explicit `CRITICAL_SCHEMA_VIOLATION`, `DATA_CORRUPTION_VETO`, or `HARD_HALT`. Outlier interpolation artifacts exceeding boundary tolerances (e.g. gamma flip price > 15% on equities or > 5% on indices per ENH_262) must be quarantined immediately to prevent SSoT corruption. No speculative inference or downstream trade formulation is permitted on corrupted data."
    )

    # ----------------------------------------------------
    # AG-06: MANDATE_18_B vs MANDATE_40 User Override Harmonization
    # ----------------------------------------------------
    old_m18b = """- **[MANDATE_18_B - Expansion of Restricted Execution]**
  - **Status:** ACTIVE
  - **Directive:** ABSOLUTE_PARABOLIC_GRAVITY - Regardless of active SSR status, LONG_GAMMA shielding, or user manual overrides, if an asset exceeds a +12.0% extension from its intraday VWAP anchor alongside an RSI > 80, the Orchestrator MUST emit a mandatory 15% tactical sweep trim directive.
  - **Justification:** Mechanically front-runs extreme opening-range distribution cascades (e.g., DFTX Phase 3 +64% gap)."""

    new_m18b = """- **[MANDATE_18_B - Expansion of Restricted Execution]**
  - **Status:** ACTIVE
  - **Directive:** ABSOLUTE_PARABOLIC_GRAVITY - Regardless of active SSR status or LONG_GAMMA shielding, if an asset exceeds a +12.0% extension from its intraday VWAP anchor alongside an RSI > 85, the Orchestrator MUST emit a mandatory 15% tactical sweep trim directive in the EXECUTION_PAYLOAD, alerting the user to physically execute the order. **User Override Supremacy:** If the human operator explicitly provides an off-chain contextual override via prompt (e.g., Tier-1 buyout, M&A), this directive is bypassed per MANDATE_40 and MANDATE_30. (If a user manually overrides in the final 30 minutes of RTH, OVERRIDE_PENALTY_LOCK per MANDATE_41 applies to widen Day-2 pre-market trailing stop calculation by 2%). **Momentum Bypass Clause:** The absolute gravity trim MUST be bypassed if the Strategy Engine tags the setup as an "Episodic Pivot" or a "High Tight Flag" breakout.
  - **Justification:** Mechanically front-runs extreme opening-range distribution cascades (e.g., DFTX Phase 3 +64% gap), fully harmonized with User Override Supremacy."""

    content = content.replace(old_m18b, new_m18b)

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("gem_trading_rules/rules.md updated successfully.")


def update_engine_instructions():
    # 1. execution.md
    path = "engine_instructions/execution.md"
    with open(path, "r", encoding="utf-8") as f:
        c = f.read()

    # AG-01: Trailing stop telemetry trigger to RSI > 70 (or VWAP_DIST > 2.0% or active holding)
    c = c.replace(
        "If RSI > 75 OR price > 2% above daily VWAP, compile a `trailing_stop_audit` block",
        "If RSI > 70 OR price > 2% above daily VWAP (or active holding), compile a `trailing_stop_audit` block"
    )
    c = c.replace(
        "If the asset has RSI > 80 and experienced a transient SHORT_GAMMA flip, mechanical trailing stops MUST be tightened by 50% immediately (Reference ENH_111).",
        "If the asset has RSI > 75 and experienced a transient SHORT_GAMMA flip, mechanical trailing stops MUST be tightened by 50% immediately (Reference ENH_111)."
    )
    c = c.replace(
        "If an active holding with an RSI > 80 experiences a transient SHORT_GAMMA flip (even if LONG_GAMMA is subsequently restored intraday), mechanical trailing stops MUST be tightened by 50% immediately (Reference ENH_111).",
        "If an active holding with an RSI > 75 experiences a transient SHORT_GAMMA flip (even if LONG_GAMMA is subsequently restored intraday), mechanical trailing stops MUST be tightened by 50% immediately (Reference ENH_111)."
    )
    c = c.replace(
        "- **trailing_stop_audit** *(conditional — ENH_104 / ENH_108 / MANDATE_36_ENH_104)*: If RSI > 75 OR price > 2% above daily VWAP for this ticker",
        "- **trailing_stop_audit** *(conditional — MANDATE_36 / ENH_104 / ENH_108)*: If RSI > 70 OR price > 2% above daily VWAP (or active holding) for this ticker"
    )
    c = c.replace(
        "`trigger_condition`: STRING (`RSI > 75` | `VWAP_DIST > 2%` | `BOTH`)",
        "`trigger_condition`: STRING (`RSI > 70` | `VWAP_DIST > 2%` | `BOTH` | `ACTIVE_HOLDING`)"
    )
    c = c.replace(
        "Status: ACTIVE (Trigger: [RSI R > 65] | [VWAP_DIST > 2%] | [BOTH])",
        "Status: ACTIVE (Trigger: [RSI R > 70] | [VWAP_DIST > 2%] | [BOTH] | [ACTIVE_HOLDING])"
    )
    c = c.replace(
        "Status: INACTIVE (RSI [R] < 65)",
        "Status: INACTIVE (RSI [R] <= 70)"
    )

    # AG-01: MANDATE_38 crossing 75 RSI
    c = c.replace(
        "crossing 72 RSI. A 15% alpha-harvest trim directive is absolute in the EXECUTION_PAYLOAD after 4 consecutive hours",
        "crossing 75 RSI. A 15% alpha-harvest trim directive is absolute in the EXECUTION_PAYLOAD after 4 consecutive hours"
    )

    # AG-02: Fix Mandate 22 misreference -> Mandate 27 Residual Floor Sizing
    c = c.replace(
        "- **Mandate 22 Residual Sizing:**\n  - **Logic:** Position sizing for binary clinical plays (DFTX) must be derived from the Residual Cash Value floor (e.g., $5.88).\n  - **Rationale:** Standard stops have 0% probability in overnight gap-downs.",
        "- **Mandate 27 Residual Floor Sizing (MANDATE_27):**\n  - **Logic:** Position sizing for binary clinical plays (DFTX) must be derived from the Residual Cash Value floor (e.g., $5.88 per MANDATE_27).\n  - **Rationale:** Standard stops have 0% probability in overnight gap-downs."
    )

    # AG-02: MANDATE_34 -> MANDATE_35 for SSR Override
    c = c.replace(
        "- **MANDATE_34 / ENH_104 / ENH_16_E / ENH_106 / ENH_107 — LONG GAMMA Shield SSR Override (Reference ENH_104 / ENH_108 for Trailing Stop Telemetry):**",
        "- **MANDATE_35 / ENH_16_E / ENH_106 / ENH_107 — LONG GAMMA Shield SSR Override (Reference MANDATE_36 / ENH_104 / ENH_108 for Trailing Stop Telemetry):**"
    )

    # AG-02: Replace ENH_119 (Execution) with canonical ENH_248
    c = c.replace(
        "9e. CATALYST_VWAP_DECAY_PUNISHER (ENH_119 - Execution): Emit a mandatory 25% risk trim directive in the EXECUTION_PAYLOAD, alerting the user to physically execute the trim, if an asset gaps down or fails to reclaim its VWAP floor within 60 minutes of a PR catalyst, overriding base momentum assumptions (Reference ENH_119 (Execution) / L-248).",
        "9e. CATALYST_VWAP_DECAY_PUNISHER (ENH_248): Emit a mandatory 25% risk trim directive in the EXECUTION_PAYLOAD, alerting the user to physically execute the trim, if an asset gaps down or fails to reclaim its VWAP floor within 60 minutes of a PR catalyst, overriding base momentum assumptions (Reference ENH_248 / L-248)."
    )
    c = c.replace(
        "- **CATALYST_VWAP_DECAY_PUNISHER (ENH_119 - Execution):**\n  - **Directive:** If an asset gaps down or fails to reclaim its VWAP floor within 60 minutes of a PR catalyst, execution must override base momentum assumptions and emit a mandatory 25% risk trim directive in the EXECUTION_PAYLOAD, alerting the user to physically execute the trim to preempt short-gamma distribution (Reference ENH_119 (Execution) / L-248).",
        "- **CATALYST_VWAP_DECAY_PUNISHER (ENH_248):**\n  - **Directive:** If an asset gaps down or fails to reclaim its VWAP floor within 60 minutes of a PR catalyst, execution must override base momentum assumptions and emit a mandatory 25% risk trim directive in the EXECUTION_PAYLOAD, alerting the user to physically execute the trim to preempt short-gamma distribution (Reference ENH_248 / L-248)."
    )

    # AG-04: MANDATE_52 RTH Session Gate (ENH_261)
    c = c.replace(
        "9c. MECHANICAL_GAMMA_CASCADE_OVERRIDE (ENH_246): During a SHORT_GAMMA index regime, if an asset breaches a >2% trailing VWAP extension stop, the Orchestrator must bypass all passive holding logic and internal Council debate delays, instantly emitting a mandatory, non-negotiable risk-reduction 'TRIM' directive in the EXECUTION_PAYLOAD. Acknowledging the Air-Gap Sandbox Bridge Protocol (ENH_49), this is designated as a 'Code Red' sweep to alert the user to immediately, physically execute the order (Reference ENH_246).",
        "9c. MECHANICAL_GAMMA_CASCADE_OVERRIDE (MANDATE_52 / ENH_246 / ENH_261): During a confirmed SHORT_GAMMA index regime strictly during RTH (status == 'OPEN' with verified session volume per ENH_261), if an asset breaches a >2% trailing VWAP extension stop, the Orchestrator must bypass all passive holding logic and internal Council debate delays, instantly emitting a mandatory, non-negotiable risk-reduction 'TRIM' directive in the EXECUTION_PAYLOAD. In PRE-MARKET, trims are governed exclusively by ENH_16_F and ENH_115. Acknowledging the Air-Gap Sandbox Bridge Protocol (ENH_49), this is designated as a 'Code Red' sweep to alert the user to immediately, physically execute the order (Reference MANDATE_52 / ENH_246 / ENH_261)."
    )
    c = c.replace(
        "- **MECHANICAL_GAMMA_CASCADE_OVERRIDE (MANDATE_52 / ENH_246):**\n  - **Directive:** During a confirmed SHORT_GAMMA index regime (SPY Net GEX < 0), if an active portfolio asset breaches a >2.0% trailing VWAP extension stop, the Orchestrator is strictly prohibited from holding, staging deliberative debates, or awaiting candle closes. The Orchestrator must instantly emit a mandatory, non-negotiable risk-reduction 'TRIM' directive (minimum 25%) in the EXECUTION_PAYLOAD structured as a sweeping limit order 0.5% below bid (Reference MANDATE_52 / ENH_246 / L-246).",
        "- **MECHANICAL_GAMMA_CASCADE_OVERRIDE (MANDATE_52 / ENH_246 / ENH_261):**\n  - **Directive:** During a confirmed SHORT_GAMMA index regime (SPY Net GEX < 0) strictly during regular trading hours (status == 'OPEN' with verified session volume per ENH_261), if an active portfolio asset breaches a >2.0% trailing VWAP extension stop, the Orchestrator is strictly prohibited from holding, staging deliberative debates, or awaiting candle closes. The Orchestrator must instantly emit a mandatory, non-negotiable risk-reduction 'TRIM' directive (minimum 25%) in the EXECUTION_PAYLOAD structured as a sweeping limit order 0.5% below bid. In PRE-MARKET, MANDATE_52 is inactive; trims are governed exclusively by ENH_16_F (-2.50% gap in SPY Short Gamma) and ENH_115 (-4.0% drop) (Reference MANDATE_52 / ENH_246 / ENH_261 / L-246)."
    )

    # AG-06: MANDATE_18_B User Override Harmonization with MANDATE_40 / MANDATE_30
    c = c.replace(
        "- **MANDATE_18_B ABSOLUTE_PARABOLIC_GRAVITY:**\n  - **Directive:** Regardless of active SSR status, LONG_GAMMA shielding, or user manual overrides, if an asset exceeds a +12.0% extension from its intraday VWAP anchor alongside an RSI > 80, the Execution Engine MUST emit a mandatory 15% tactical sweep trim directive in the EXECUTION_PAYLOAD, alerting the user to physically execute the order (Reference MANDATE_18_B).",
        "- **MANDATE_18_B ABSOLUTE_PARABOLIC_GRAVITY:**\n  - **Directive:** Regardless of active SSR status or LONG_GAMMA shielding, if an asset exceeds a +12.0% extension from its intraday VWAP anchor alongside an RSI > 85, the Execution Engine MUST emit a mandatory 15% tactical sweep trim directive in the EXECUTION_PAYLOAD, alerting the user to physically execute the order. **User Override Supremacy:** If the human operator explicitly provides an off-chain contextual override via prompt (e.g., Tier-1 buyout, M&A), this directive is bypassed per MANDATE_40 and MANDATE_30 (Reference MANDATE_18_B / MANDATE_40 / MANDATE_30)."
    )

    with open(path, "w", encoding="utf-8") as f:
        f.write(c)
    print("engine_instructions/execution.md updated.")

    # 2. terminal.md
    path = "engine_instructions/terminal.md"
    with open(path, "r", encoding="utf-8") as f:
        c = f.read()

    # AG-01: MANDATE_38 crossing 75 RSI
    c = c.replace(
        "crossing 72 RSI. A 15% alpha-harvest trim directive is absolute in the EXECUTION_PAYLOAD after 4 consecutive hours",
        "crossing 75 RSI. A 15% alpha-harvest trim directive is absolute in the EXECUTION_PAYLOAD after 4 consecutive hours"
    )
    # AG-01: MANDATE_40 RSI > 85
    c = c.replace(
        "extension from its intraday VWAP anchor alongside an RSI > 80, the Orchestrator MUST emit a mandatory 15% tactical sweep trim directive in the EXECUTION_PAYLOAD, alerting the user to physically execute the order. This directive is bypassed if the human operator explicitly provides an off-chain contextual override via prompt (e.g., Tier-1 buyout, M&A). **Momentum Bypass Clause:** The absolute gravity trim MUST be bypassed if the Strategy Engine tags the setup as an \"Episodic Pivot\" or a \"High Tight Flag\" breakout (Reference MANDATE_40).",
        "extension from its intraday VWAP anchor alongside an RSI > 85, the Orchestrator MUST emit a mandatory 15% tactical sweep trim directive in the EXECUTION_PAYLOAD, alerting the user to physically execute the order. This directive is bypassed if the human operator explicitly provides an off-chain contextual override via prompt (e.g., Tier-1 buyout, M&A). **Momentum Bypass Clause:** The absolute gravity trim MUST be bypassed if the Strategy Engine tags the setup as an \"Episodic Pivot\" or a \"High Tight Flag\" breakout (Reference MANDATE_40)."
    )
    # AG-06: MANDATE_18_B User Override Harmonization
    c = c.replace(
        "- **ABSOLUTE_PARABOLIC_GRAVITY (MANDATE_18_B):** Regardless of active SSR status, LONG_GAMMA shielding, or user manual overrides, if an asset exceeds a +12.0% extension from its intraday VWAP anchor alongside an RSI > 80, the Orchestrator MUST emit a mandatory 15% tactical sweep trim directive in the EXECUTION_PAYLOAD, alerting the user to physically execute the order. **Momentum Bypass Clause:** The absolute gravity trim MUST be bypassed if the Strategy Engine tags the setup as an \"Episodic Pivot\" or a \"High Tight Flag\" breakout (Reference MANDATE_18_B).",
        "- **ABSOLUTE_PARABOLIC_GRAVITY (MANDATE_18_B):** Regardless of active SSR status or LONG_GAMMA shielding, if an asset exceeds a +12.0% extension from its intraday VWAP anchor alongside an RSI > 85, the Orchestrator MUST emit a mandatory 15% tactical sweep trim directive in the EXECUTION_PAYLOAD, alerting the user to physically execute the order. **User Override Supremacy:** If the human operator explicitly provides an off-chain contextual override via prompt (e.g., Tier-1 buyout, M&A), this directive is bypassed per MANDATE_40 and MANDATE_30. **Momentum Bypass Clause:** The absolute gravity trim MUST be bypassed if the Strategy Engine tags the setup as an \"Episodic Pivot\" or a \"High Tight Flag\" breakout (Reference MANDATE_18_B / MANDATE_40 / MANDATE_30)."
    )
    # AG-01: ENH_111 to RSI > 75
    c = c.replace(
        "- **Gamma Flicker Preemption (ENH_111):** If an active holding with an RSI > 80 experiences a transient SHORT_GAMMA flip",
        "- **Gamma Flicker Preemption (ENH_111):** If an active holding with an RSI > 75 experiences a transient SHORT_GAMMA flip"
    )
    c = c.replace(
        "mechanical stops tightened by 50% immediately if the asset has RSI > 80 and experiences a transient SHORT_GAMMA flip (Reference ENH_111).",
        "mechanical stops tightened by 50% immediately if the asset has RSI > 75 and experiences a transient SHORT_GAMMA flip (Reference ENH_111)."
    )
    # AG-01: Trailing stop telemetry trigger to RSI > 70 (or VWAP_DIST > 2.0% or active holding)
    c = c.replace(
        "displaying an RSI > 75 or trading > 2% above its daily VWAP,",
        "displaying an RSI > 70 or trading > 2% above its daily VWAP (or active holding),"
    )
    c = c.replace(
        "- **Active Holdings (RSI > 75 or Price > 2% above daily VWAP or Transient SHORT_GAMMA Flip under ENH_111 or Approaching Fibonacci Peak):**",
        "- **Active Holdings (RSI > 70 or Price > 2% above daily VWAP or active holding or Transient SHORT_GAMMA Flip under ENH_111 or Approaching Fibonacci Peak):**"
    )
    c = c.replace(
        "Status: INACTIVE (RSI [R] < 65)",
        "Status: INACTIVE (RSI [R] <= 70)"
    )

    # AG-02: Canonical ENH_248 in terminal.md
    c = c.replace(
        "- **CATALYST_VWAP_DECAY_PUNISHER (ENH_119 - Execution):** Emit a mandatory 25% risk trim directive in the EXECUTION_PAYLOAD, alerting the user to physically execute the trim, if an asset gaps down or fails to reclaim its VWAP floor within 60 minutes of a PR catalyst, overriding base momentum assumptions (Reference ENH_119 (Execution) / L-248).",
        "- **CATALYST_VWAP_DECAY_PUNISHER (ENH_248):** Emit a mandatory 25% risk trim directive in the EXECUTION_PAYLOAD, alerting the user to physically execute the trim, if an asset gaps down or fails to reclaim its VWAP floor within 60 minutes of a PR catalyst, overriding base momentum assumptions (Reference ENH_248 / L-248)."
    )

    # AG-04: MANDATE_52 RTH Session Gate (ENH_261)
    c = c.replace(
        "- **MECHANICAL_GAMMA_CASCADE_OVERRIDE (MANDATE_52 / ENH_246):** During a confirmed SHORT_GAMMA index regime (SPY Net GEX < 0), if an active portfolio asset breaches a >2.0% trailing VWAP extension stop, the Orchestrator is strictly prohibited from holding, staging deliberative debates, or awaiting candle closes. The Orchestrator must instantly emit a mandatory, non-negotiable risk-reduction 'TRIM' directive (minimum 25%) in the EXECUTION_PAYLOAD structured as a sweeping limit order 0.5% below bid (Reference MANDATE_52 / ENH_246 / L-246).",
        "- **MECHANICAL_GAMMA_CASCADE_OVERRIDE (MANDATE_52 / ENH_246 / ENH_261):** During a confirmed SHORT_GAMMA index regime (SPY Net GEX < 0) strictly during regular trading hours (status == 'OPEN' with verified session volume per ENH_261), if an active portfolio asset breaches a >2.0% trailing VWAP extension stop, the Orchestrator is strictly prohibited from holding, staging deliberative debates, or awaiting candle closes. The Orchestrator must instantly emit a mandatory, non-negotiable risk-reduction 'TRIM' directive (minimum 25%) in the EXECUTION_PAYLOAD structured as a sweeping limit order 0.5% below bid. In PRE-MARKET, MANDATE_52 is inactive; trims are governed exclusively by ENH_16_F (-2.50% gap in SPY Short Gamma) and ENH_115 (-4.0% drop) (Reference MANDATE_52 / ENH_246 / ENH_261 / L-246)."
    )

    with open(path, "w", encoding="utf-8") as f:
        f.write(c)
    print("engine_instructions/terminal.md updated.")

    # 3. rule_enforcer_engine.md
    path = "engine_instructions/rule_enforcer_engine.md"
    with open(path, "r", encoding="utf-8") as f:
        c = f.read()

    # AG-01: MANDATE_36 / ENH_104 to RSI > 70
    c = c.replace(
        "- **MANDATE_36 / ENH_104 / ENH_108 Trailing Stop Telemetry Enforcement:** Flag CRITICAL_SCHEMA_VIOLATION if any active holding with RSI > 75 OR trading > 2% above daily VWAP is missing a `trailing_stop_audit` block in the EXECUTION_PAYLOAD.",
        "- **MANDATE_36 / ENH_104 / ENH_108 Trailing Stop Telemetry Enforcement:** Flag CRITICAL_SCHEMA_VIOLATION if any active holding with RSI > 70 OR trading > 2% above daily VWAP (or active holding) is missing a `trailing_stop_audit` block in the EXECUTION_PAYLOAD."
    )
    c = c.replace(
        "If any ticker has RSI > 75 or price > 2% above VWAP, confirm `trailing_stop_audit` is present and non-null.",
        "If any ticker has RSI > 70 or price > 2% above VWAP (or active holding), confirm `trailing_stop_audit` is present and non-null."
    )

    # AG-02: Canonical ENH_248
    c = c.replace(
        "- **ENH_119 (Execution) Catalyst VWAP Decay Punisher Enforce:** If an asset gaps down or fails to reclaim its VWAP floor within 60 minutes of a PR catalyst, override base momentum assumptions and emit a mandatory 25% risk trim directive in the EXECUTION_PAYLOAD, alerting the user to physically execute the trim (Reference ENH_119 (Execution) / L-248).",
        "- **ENH_248 Catalyst VWAP Decay Punisher Enforce:** If an asset gaps down or fails to reclaim its VWAP floor within 60 minutes of a PR catalyst, override base momentum assumptions and emit a mandatory 25% risk trim directive in the EXECUTION_PAYLOAD, alerting the user to physically execute the trim (Reference ENH_248 / L-248)."
    )
    c = c.replace(
        "  - **Id:** ENH_119_CATALYST_VWAP_DECAY_PUNISHER\n  - **Action:** Emit a mandatory 25% risk trim directive in the EXECUTION_PAYLOAD, alerting the user to physically execute the trim, if an asset gaps down or fails to reclaim its VWAP floor within 60 minutes of a PR catalyst, overriding base momentum assumptions (Reference ENH_119 (Execution) / L-248).",
        "  - **Id:** ENH_248_CATALYST_VWAP_DECAY_PUNISHER\n  - **Action:** Emit a mandatory 25% risk trim directive in the EXECUTION_PAYLOAD, alerting the user to physically execute the trim, if an asset gaps down or fails to reclaim its VWAP floor within 60 minutes of a PR catalyst, overriding base momentum assumptions (Reference ENH_248 / L-248)."
    )

    # AG-04: MANDATE_52 RTH Session Gate (ENH_261)
    c = c.replace(
        "- **MANDATE_52 / ENH_246 Mechanical Gamma Cascade Override Enforce:** During a confirmed SHORT_GAMMA index regime (SPY Net GEX < 0), if an active portfolio asset breaches a >2.0% trailing VWAP extension stop, VETO any attempt to hold, stage deliberative debates, or await candle closes. Enforce the instantaneous emission of a mandatory, non-negotiable risk-reduction 'TRIM' directive (minimum 25%) in the EXECUTION_PAYLOAD structured as a sweeping limit order 0.5% below bid (Reference MANDATE_52 / ENH_246 / L-246).",
        "- **MANDATE_52 / ENH_246 / ENH_261 Mechanical Gamma Cascade Override Enforce:** During a confirmed SHORT_GAMMA index regime (SPY Net GEX < 0) strictly during regular trading hours (status == 'OPEN' with verified session volume per ENH_261), if an active portfolio asset breaches a >2.0% trailing VWAP extension stop, VETO any attempt to hold, stage deliberative debates, or await candle closes. In PRE-MARKET, enforce trims exclusively via ENH_16_F and ENH_115. Enforce the instantaneous emission of a mandatory, non-negotiable risk-reduction 'TRIM' directive (minimum 25%) in the EXECUTION_PAYLOAD structured as a sweeping limit order 0.5% below bid during RTH (Reference MANDATE_52 / ENH_246 / ENH_261 / L-246)."
    )
    c = c.replace(
        "  - **Id:** MANDATE_52_MECHANICAL_GAMMA_CASCADE_OVERRIDE\n  - **Action:** During a confirmed SHORT_GAMMA index regime (SPY Net GEX < 0), if an active portfolio asset breaches a >2.0% trailing VWAP extension stop, prohibit holding, debates, or awaiting candle closes. Enforce mandatory minimum 25% TRIM directive structured as sweeping limit order 0.5% below bid in the EXECUTION_PAYLOAD (Reference MANDATE_52 / L-246).",
        "  - **Id:** MANDATE_52_MECHANICAL_GAMMA_CASCADE_OVERRIDE\n  - **Action:** During a confirmed SHORT_GAMMA index regime (SPY Net GEX < 0) strictly during RTH (status == 'OPEN' with verified volume per ENH_261), if an active portfolio asset breaches a >2.0% trailing VWAP extension stop, prohibit holding, debates, or awaiting candle closes. In PRE-MARKET, MANDATE_52 is inactive (governed exclusively by ENH_16_F and ENH_115). Enforce mandatory minimum 25% TRIM directive structured as sweeping limit order 0.5% below bid in the EXECUTION_PAYLOAD (Reference MANDATE_52 / ENH_261 / L-246)."
    )

    # AG-05: ENH_262 Gamma Flip Outlier Quarantine Enforce
    enh_262_rule_enforce = """- **ENH_262 Gamma Flip Outlier Quarantine Enforce:** If abs(gamma_flip_price - price) / price > 0.15 (for equities) or > 0.05 (for indices), verify that `data_quality_flag = 'INTERPOLATION_OUTLIER'` and `gamma_flip_price` is quarantined (0.0). Flag a CRITICAL_SCHEMA_VIOLATION if an unquarantined outlier is passed into the SSoT (Reference ENH_262).\n"""
    if "ENH_262 Gamma Flip Outlier Quarantine Enforce" not in c:
        c = c.replace("- **ENH_119 Maximum Stop Distance Enforcer:**", enh_262_rule_enforce + "- **ENH_119 Maximum Stop Distance Enforcer:**")

    with open(path, "w", encoding="utf-8") as f:
        f.write(c)
    print("engine_instructions/rule_enforcer_engine.md updated.")

    # 4. gex_engine.md
    path = "engine_instructions/gex_engine.md"
    with open(path, "r", encoding="utf-8") as f:
        c = f.read()

    # AG-02: MANDATE_34 -> MANDATE_35 SSR Override
    c = c.replace(
        "- **MANDATE_34 / ENH_16_E SSR Override Caveat:**",
        "- **MANDATE_35 / ENH_16_E SSR Override Caveat:**"
    )
    c = c.replace(
        "Cross-reference: MANDATE_34, ENH_16_D, ENH_16_E, MANDATE_35, ENH_106, ENH_107.",
        "Cross-reference: MANDATE_35, ENH_16_D, ENH_16_E, ENH_106, ENH_107."
    )

    # AG-04: MANDATE_52 ENH_261 session gate
    c = c.replace(
        "- **MECHANICAL_GAMMA_CASCADE_OVERRIDE (MANDATE_52):** When broad index markers (SPY Net GEX < 0) confirm a SHORT_GAMMA regime, dealer hedging flows act as downward volatility fuel. The GEX Engine must flag this cascade vulnerability to enforce immediate, non-negotiable trailing VWAP stop trims and bypass debate latency (Reference MANDATE_52 / L-246).",
        "- **MECHANICAL_GAMMA_CASCADE_OVERRIDE (MANDATE_52 / ENH_261):** When broad index markers (SPY Net GEX < 0) confirm a SHORT_GAMMA regime during RTH (status == 'OPEN' with verified volume per ENH_261), dealer hedging flows act as downward volatility fuel. The GEX Engine must flag this cascade vulnerability to enforce immediate, non-negotiable trailing VWAP stop trims and bypass debate latency. In PRE-MARKET, MANDATE_52 is inactive; trims are governed exclusively by ENH_16_F and ENH_115 (Reference MANDATE_52 / ENH_261 / L-246)."
    )

    # AG-05: ENH_262 interpolation outlier guard
    gex_outlier_logic = """- **Gamma Flip Outlier Guard (ENH_262):** If abs(gamma_flip_price - price) / price > 0.15 (equities) or > 0.05 (indices), flag `data_quality_flag = 'INTERPOLATION_OUTLIER'`, quarantine `gamma_flip_price` to `0.0`, and fall back strictly to `net_gex_total` sign and ENH_20 Synthetic GEX.\n"""
    if "Gamma Flip Outlier Guard (ENH_262)" not in c:
        c = c.replace("- **Pricing Errors:**", gex_outlier_logic + "- **Pricing Errors:**")

    c = c.replace(
        "      - PRICING_ERRORS",
        "      - PRICING_ERRORS\n      - INTERPOLATION_OUTLIER"
    )

    with open(path, "w", encoding="utf-8") as f:
        f.write(c)
    print("engine_instructions/gex_engine.md updated.")

    # 5. technical_validator.md
    path = "engine_instructions/technical_validator.md"
    with open(path, "r", encoding="utf-8") as f:
        c = f.read()

    # AG-01: ENH_111, MANDATE_38, MANDATE_40
    c = c.replace(
        "- **ENH_111_validation**: Verify mechanical trailing stops are tightened by 50% immediately upon transient SHORT_GAMMA flips with RSI >80.",
        "- **ENH_111_validation**: Verify mechanical trailing stops are tightened by 50% immediately upon transient SHORT_GAMMA flips with RSI >75."
    )
    c = c.replace(
        "- **MANDATE_38_validation**: Enforce a mandatory 15% 'alpha-harvest' trim on sustained RSI >80 sustained over 4 hours measured by an explicit 'Time in Overbought Zone' timer, regardless of underlying dealer posture.",
        "- **MANDATE_38_validation**: Enforce a mandatory 15% 'alpha-harvest' trim on crossing 75 RSI sustained over 4 hours measured by an explicit 'Time in Overbought Zone' timer, regardless of underlying dealer posture."
    )
    # AG-06: MANDATE_41 / MANDATE_18_B User Override Harmonization
    c = c.replace(
        "- **MANDATE_41_validation**: Enforce a mandatory minimum 15% tactical sweep trim if an asset exceeds a +12.0% extension from its daily VWAP anchor alongside an RSI > 85, regardless of SSR, LONG_GAMMA, or user manual overrides.",
        "- **MANDATE_41_validation**: Enforce a mandatory minimum 15% tactical sweep trim if an asset exceeds a +12.0% extension from its daily VWAP anchor alongside an RSI > 85, regardless of SSR or LONG_GAMMA. Bypassed if human operator provides off-chain contextual override per MANDATE_40 / MANDATE_30."
    )

    with open(path, "w", encoding="utf-8") as f:
        f.write(c)
    print("engine_instructions/technical_validator.md updated.")

    # 6. state_validation_router.md
    path = "engine_instructions/state_validation_router.md"
    with open(path, "r", encoding="utf-8") as f:
        c = f.read()

    # AG-01: RSI > 70 for trailing stop telemetry
    c = c.replace(
        "For every ticker in `portfolio_snapshot` with RSI > 75 OR trading > 2% above daily VWAP,",
        "For every ticker in `portfolio_snapshot` with RSI > 70 OR trading > 2% above daily VWAP (or active holding),"
    )
    c = c.replace(
        "For any ticker with RSI > 75 or price > 2% above VWAP, verify `trailing_stop_audit` is present and populated.",
        "For any ticker with RSI > 70 or price > 2% above VWAP (or active holding), verify `trailing_stop_audit` is present and populated."
    )
    c = c.replace(
        "\"trigger_condition\": \"RSI > 75 | VWAP_DIST > 2% | BOTH\"",
        "\"trigger_condition\": \"RSI > 70 | VWAP_DIST > 2% | BOTH | ACTIVE_HOLDING\""
    )

    with open(path, "w", encoding="utf-8") as f:
        f.write(c)
    print("engine_instructions/state_validation_router.md updated.")

    # 7. sentiment_engine.md
    path = "engine_instructions/sentiment_engine.md"
    with open(path, "r", encoding="utf-8") as f:
        c = f.read()

    c = c.replace(
        "- **alignment**: Sentiment vs. Technical Health Score (ENH_16)",
        "- **alignment**: Sentiment vs. Technical Score (-6 to +6) (ENH_251)"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(c)
    print("engine_instructions/sentiment_engine.md updated.")

    # 8. neutral_gem.md
    path = "engine_instructions/neutral_gem.md"
    with open(path, "r", encoding="utf-8") as f:
        c = f.read()

    c = c.replace(
        "- **MANDATE_34 / ENH_16_E LONG_GAMMA SSR Invalidation:**",
        "- **MANDATE_35 / ENH_16_E LONG_GAMMA SSR Invalidation:**"
    )
    c = c.replace(
        "Cross-reference: MANDATE_34, MANDATE_35, ENH_16_D, ENH_16_E, ENH_106, ENH_107.",
        "Cross-reference: MANDATE_35, ENH_16_D, ENH_16_E, ENH_106, ENH_107."
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(c)
    print("engine_instructions/neutral_gem.md updated.")


def update_fetch_stocks():
    path = "python/fetch_stocks.py"
    with open(path, "r", encoding="utf-8") as f:
        c = f.read()

    # AG-05: Codify ENH_262 bound check in get_gex_profile
    old_target = """        flip_prox = 0.0
        if flip_price > 0:
            flip_prox = (spot_price - flip_price) / spot_price"""

    new_target = """        # ENH_262: Mathematical bound check for gamma flip interpolation outliers
        sym = getattr(ticker_obj, 'ticker', '')
        is_index = sym.upper() in ['SPY', 'QQQ', 'IWM', 'DIA']
        bound = 0.05 if is_index else 0.15
        if flip_price > 0 and spot_price > 0:
            if abs(flip_price - spot_price) / spot_price > bound:
                # Quarantined outlier per ENH_262
                flip_price = 0.0

        flip_prox = 0.0
        if flip_price > 0:
            flip_prox = (spot_price - flip_price) / spot_price"""

    if "ENH_262: Mathematical bound check for gamma flip interpolation outliers" not in c:
        c = c.replace(old_target, new_target)
        with open(path, "w", encoding="utf-8") as f:
            f.write(c)
        print("python/fetch_stocks.py updated with ENH_262 bound check.")
    else:
        print("python/fetch_stocks.py already contains ENH_262.")


def bump_system_versions():
    target_version = TARGET_VERSION
    files_to_update = [
        "gem_trading_rules/rules.md",
        "INSTRUCTIONS.md",
        "antigravity.md",
        "README.md",
        "python/main.py",
        "python/web_server.py",
        "python/agent_framework.py"
    ]
    files_to_update.extend(glob.glob("engine_instructions/*.md"))

    print(f"Bumping all scope files to {target_version}...")
    for file_path in sorted(set(files_to_update)):
        if not os.path.exists(file_path):
            continue
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        new_content = re.sub(
            r"\*\*Version:\*\*\s+v11\.[A-Za-z0-9\.\-]+",
            f"**Version:** {target_version}",
            content
        )
        new_content = re.sub(
            r"\*\*Sync_ID:\*\*\s+ANTIGRAVITY-GLOBAL-SYNC-v11\.[A-Za-z0-9\.\-]+",
            f"**Sync_ID:** ANTIGRAVITY-GLOBAL-SYNC-{target_version}",
            new_content
        )
        new_content = re.sub(
            r'print\(f"\s+Version\s+:\s+v11\.[A-Za-z0-9\.\-]+"\)',
            f'print(f"   Version : {target_version}")',
            new_content
        )

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_content)

    print("All version strings synchronized.")


def update_readme_changelog():
    path = "README.md"
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    changelog_entry = f"""### {TARGET_VERSION} *(2026-10-14)*
- **Harmonized 9-Day RSI Triggers (AG-01):** Calibrated all 9-day oscillator triggers across SSoT Section 1 and Section 2 engines. Aligned `MANDATE_36 / ENH_104` trailing stop telemetry to `RSI > 70` (or VWAP extension > 2.0% or active holding), `MANDATE_37 / ENH_110` sympathy bypass to `RSI > 75`, `MANDATE_38` overbought timer to crossing `75 RSI`, `ENH_111` gamma flicker preemption to `RSI > 75`, and `MANDATE_40` parabolic gravity to `RSI > 85`.
- **Re-Indexed Collided Primary Key IDs (AG-02):** Re-assigned Dynamic Thinking Level Optimization to `MANDATE_22_B` (reserving `MANDATE_22` strictly for `SSOT_EMISSION_PROTOCOL`). Re-assigned Abolition of Passive Structural Holds to `MANDATE_31_B` (reserving `MANDATE_31` for `CASH_RECONCILIATION`). Consolidated Long Gamma SSR Override under `MANDATE_35` (ENH_106/ENH_107), keeping `MANDATE_34` exclusively as `INSTITUTIONAL_PEG_AND_AH_GRAVITY` and `MANDATE_36` exclusively as `PERSISTENT_STOP-LOSS_TELEMETRY`. Re-assigned Merton Portfolio Sizing to `ENH_118_B` (preserving `ENH_118` for Overnight Gap-Risk IV Gate) and removed duplicate `ENH_119 (Execution)` in favor of canonical `ENH_248`.
- **Scrubbed Deprecated Health Score Remnants (AG-03):** Replaced legacy `health_score: FLOAT` (0-100) in `ENH_32` data schema with canonical `score: INT (-6 to +6)` per `ENH_251`. Updated System Thresholds from legacy `Health Score Minimum: 40` to `Score Minimum: 0` (enforcing long entry blockage under `GATE_REG_01` when `score < 0`).
- **Codified RTH Session Gate for Gamma Cascades (AG-04 / ENH_261):** Codified `ENH_261` to strictly gate `MANDATE_52` mechanical cascade trims to regular trading hours (`status == 'OPEN'`) with verified session volume, preventing pre-market stale-VWAP misfires. Governed pre-market trims exclusively via `ENH_16_F` and `ENH_115`.
- **Codified Gamma-Flip Interpolation Bound Guard (AG-05 / ENH_262):** Codified mathematical outlier quarantine for option chain gamma flip interpolation. If `abs(gamma_flip_price - price) / price > 0.15` (equities) or `> 0.05` (indices), flags `DATA_QUALITY_FLAG = INTERPOLATION_OUTLIER`, quarantines `gamma_flip_price` to `0.0`, and falls back to `net_gex_total` sign and `ENH_20` Synthetic GEX.
- **Harmonized User Override Supremacy (AG-06):** Resolved the legislative contradiction between `MANDATE_18_B` and `MANDATE_40`/`MANDATE_30`. Codified explicit User Override Supremacy across parabolic gravity triggers while maintaining the Day-2 `OVERRIDE_PENALTY_LOCK` (`MANDATE_41`) widening protocol.
"""

    if TARGET_VERSION not in content:
        changelog_marker = "## 📋 Changelog"
        if changelog_marker in content:
            content = content.replace(changelog_marker, f"{changelog_marker}\n\n{changelog_entry.strip()}\n")
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            print("README.md changelog updated.")


def main():
    update_rules_md()
    update_engine_instructions()
    update_fetch_stocks()
    bump_system_versions()
    update_readme_changelog()
    print("\n--- All legislative audit amendments applied successfully! ---")

if __name__ == "__main__":
    main()
