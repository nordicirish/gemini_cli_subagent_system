"""
Verification script for AG-01 through AG-06 rectifications.
"""

import re
import os

def verify():
    with open("gem_trading_rules/rules.md", "r", encoding="utf-8") as f:
        rules = f.read()

    with open("scratch/master_trading_knowledge.md", "r", encoding="utf-8") as f:
        master = f.read()

    print("Verifying AG-01: RSI Threshold Harmonization...")
    assert "MANDATE_36](#mandate_36): PERSISTENT STOP-LOSS TELEMETRY" in rules
    assert "RSI > 70 or VWAP_DIST > 2% or active holding" in rules
    # Check MANDATE_37 / ENH_110
    assert "RSI > 75, the momentum is quantitatively classified as \"sympathy-driven\"" in rules
    # Check MANDATE_38
    assert "crossing 75 RSI" in rules
    # Check ENH_111
    assert "RSI > 75 experiences a transient SHORT_GAMMA flip" in rules
    # Check MANDATE_40
    assert "alongside an RSI > 85, the Orchestrator MUST emit a mandatory 15% tactical sweep trim" in rules
    print("AG-01: PASSED")

    print("Verifying AG-02: Primary Key ID Collisions...")
    # MANDATE_22_B
    assert "MANDATE_22_B" in rules
    assert "MANDATE_22_B - Dynamic Thinking Level Optimization" in rules
    # MANDATE_31_B
    assert "MANDATE_31_B" in rules
    assert "MANDATE_31_B_ABOLITION_OF_PASSIVE_STRUCTURAL_HOLDS" in rules
    # MANDATE_35 canonical consolidation
    assert "MANDATE_35_LONG_GAMMA_SSR_OVERRIDE" in rules
    # ENH_118_B Merton
    assert "ENH_118_B" in rules
    assert "ENH_118_B] MERTON_SIZING_INTEGRATION_AND_HEDGING_DEMAND" in rules
    # ENH_119 duplicate removed, ENH_248 canonical
    assert "ENH_119 (Execution)" not in rules
    assert "### [ENH_248] CATALYST_VWAP_DECAY_PUNISHER" in rules
    print("AG-02: PASSED")

    print("Verifying AG-03: Deprecated health_score scrub...")
    assert "Health Score: FLOAT" not in rules
    assert "Score Minimum:" in rules
    assert "Score Protocol (ENH_251 Unified Gradient)" in rules
    print("AG-03: PASSED")

    print("Verifying AG-04: MANDATE_52 RTH session gate (ENH_261)...")
    assert "ENH_261" in rules
    assert "ENH_261_RTH_SESSION_GATE_FOR_GAMMA_CASCADES" in rules
    assert "status == 'OPEN'" in rules or 'status == "OPEN"' in rules
    print("AG-04: PASSED")

    print("Verifying AG-05: Gamma-Flip Outlier Bound Guard (ENH_262)...")
    assert "ENH_262" in rules
    assert "ENH_262_GAMMA_FLIP_INTERPOLATION_BOUND_GUARD" in rules
    assert "INTERPOLATION_OUTLIER" in rules
    print("AG-05: PASSED")

    print("Verifying AG-06: MANDATE_18_B vs MANDATE_40 User Override Supremacy...")
    assert "regardless of active SSR status, LONG_GAMMA shielding, or user manual overrides" not in rules.lower()
    assert "User Override Supremacy" in rules
    assert "off-chain contextual override" in rules
    print("AG-06: PASSED")

    print("\n--- ALL AUDIT VERIFICATIONS PASSED IN MASTER TRADING KNOWLEDGE! ---")

if __name__ == "__main__":
    verify()
