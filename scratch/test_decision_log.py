import json
import re
from datetime import datetime

def _deep_merge(base, delta):
    merged = base.copy()
    for k, v in delta.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged

def test_interception(clip_data):
    # 1. Extract JSON SSoT block
    json_match = re.search(r"```json\s*(\{.*?\})\s*```", clip_data, re.IGNORECASE | re.DOTALL)
    if not json_match:
        # Try finding braces directly if no markdown block
        start_brace = clip_data.find('{')
        end_brace = clip_data.rfind('}')
        if start_brace != -1 and end_brace != -1 and end_brace > start_brace:
            json_payload = json.loads(clip_data[start_brace:end_brace+1].strip())
        else:
            print("No JSON found")
            return
    else:
        json_payload = json.loads(json_match.group(1).strip())

    payload = json_payload
    
    incoming_ep = payload.get("EXECUTION_PAYLOAD")
    
    # PROMOTION LOGIC (from fetch_stocks.py)
    if isinstance(incoming_ep, dict):
        promotion_keys = ["portfolio_snapshot", "risk_metrics", "directive", "timestamp"]
        
        # Source of truth can be at the root of incoming_ep or inside its mutable_state
        ep_source = incoming_ep
        if "mutable_state" in incoming_ep and isinstance(incoming_ep["mutable_state"], dict):
            # If ep has mutable_state, merge its keys into a temporary source
            ep_source = _deep_merge(incoming_ep, incoming_ep["mutable_state"])

        # Determine target container for promotion
        target_container = payload
        if isinstance(payload.get("mutable_state"), dict):
            target_container = payload["mutable_state"]
            
        for k in promotion_keys:
            if k in ep_source:
                if k == "portfolio_snapshot" or k not in target_container or k in ["directive", "risk_metrics", "timestamp"]:
                    target_container[k] = ep_source[k]

    # INTERCEPTION LOGIC (from fetch_stocks.py)
    incoming_portfolio = payload.get("portfolio_snapshot", [])
    if not incoming_portfolio and isinstance(payload.get("mutable_state"), dict):
        incoming_portfolio = payload["mutable_state"].get("portfolio_snapshot", [])
    
    if not incoming_portfolio and isinstance(payload.get("EXECUTION_PAYLOAD"), dict):
        incoming_portfolio = payload["EXECUTION_PAYLOAD"].get("portfolio_snapshot", [])
        if not incoming_portfolio and isinstance(payload["EXECUTION_PAYLOAD"].get("mutable_state"), dict):
            incoming_portfolio = payload["EXECUTION_PAYLOAD"]["mutable_state"].get("portfolio_snapshot", [])
            
    print(f"Found incoming_portfolio: {len(incoming_portfolio) if incoming_portfolio else 0} items")
    
    if incoming_portfolio and isinstance(incoming_portfolio, list):
        turn_log = {
            "timestamp": payload.get("timestamp", datetime.now().isoformat()),
            "decisions": []
        }
        
        for item in incoming_portfolio:
            if isinstance(item, dict):
                ticker = item.get("ticker")
                trade_state = item.get("trade_state", item.get("action"))
                scrutiny_audit = item.get("scrutiny_audit")
                
                if ticker and (trade_state or scrutiny_audit):
                    turn_log["decisions"].append({
                        "ticker": ticker,
                        "trade_state": trade_state,
                        "scrutiny_audit": scrutiny_audit
                    })
        
        print(f"Decisions found: {len(turn_log['decisions'])}")
        print(json.dumps(turn_log, indent=2))

# The payload provided by the user
user_payload = """
antigravity agent {
  "EXECUTION_PAYLOAD": {
    "sync_id": 18,
    "timestamp": "2026-05-11 16:04:41",
    "regime": "MELT_UP",
    "vix_guard": "DISENGAGED",
    "directives": [
      {
        "ticker": "SYSTEM_STATE",
        "action": "EXECUTE",
        "sizing": "NO_ALLOCATION",
        "rationale": "PROTOCOL_02 authorized. Validated rules.md ingested. MANDATE_22 schema updated to include decision log persistence.",
        "math_proof": "Proof: (Price 1.0 - PrevClose 1.0) / 1.0 = 0.00%",
        "fx_proof": "Proof: (USD_Value 0.00 * 0.85237) = 0.00 EUR"
      }
    ],
    "portfolio_snapshot": [
      {
        "ticker": "UMAC",
        "shares": 2713,
        "wac": 13.32,
        "trade_state": "NO_TRADE",
        "no_trade_reason": "Awaiting earnings validation per ENH_31.",
        "scrutiny_audit": {
          "agreement_score_sa": 0.80,
          "fatal_flaw_score": 4,
          "final_posture": "STABLE",
          "agent_votes": [
            {
              "agent": "BULLISH_ADVOCATE",
              "verdict": "HOLD",
              "confidence": 0.85,
              "self_critique": "Awaiting GAAP execution proof."
            },
            {
              "agent": "RED_TEAM_PESSIMIST",
              "verdict": "HOLD",
              "confidence": 0.70,
              "self_critique": "Liquidity sufficient for near-term operations."
            },
            {
              "agent": "NEUTRAL_STRUCTURALIST",
              "verdict": "HOLD",
              "confidence": 0.90,
              "self_critique": "VIX contained; structural metrics stable."
            }
          ]
        }
      },
      {
        "ticker": "DFTX",
        "shares": 251,
        "wac": 19.09,
        "trade_state": "NO_TRADE",
        "no_trade_reason": "Structural hold pending Phase 3 readouts.",
        "scrutiny_audit": {
          "agreement_score_sa": 0.85,
          "fatal_flaw_score": 2,
          "final_posture": "STABLE",
          "agent_votes": [
            {
              "agent": "BULLISH_ADVOCATE",
              "verdict": "HOLD",
              "confidence": 0.90,
              "self_critique": "Clinical pipeline derisked by cash runway."
            },
            {
              "agent": "RED_TEAM_PESSIMIST",
              "verdict": "HOLD",
              "confidence": 0.60,
              "self_critique": "No near-term dilution risk identified."
            },
            {
              "agent": "NEUTRAL_STRUCTURALIST",
              "verdict": "HOLD",
              "confidence": 0.85,
              "self_critique": "Residual floor sizing logic remains valid."
            }
          ]
        }
      },
      {
        "ticker": "MU",
        "shares": 4,
        "wac": 662.0,
        "trade_state": "NO_TRADE",
        "no_trade_reason": "Trailing VWAP stop active per ENH_87.",
        "scrutiny_audit": {
          "agreement_score_sa": 0.90,
          "fatal_flaw_score": 1,
          "final_posture": "STABLE",
          "agent_votes": [
            {
              "agent": "BULLISH_ADVOCATE",
              "verdict": "HOLD",
              "confidence": 0.95,
              "self_critique": "Secular AI demand intact."
            },
            {
              "agent": "RED_TEAM_PESSIMIST",
              "verdict": "HOLD",
              "confidence": 0.75,
              "self_critique": "CapEx trap risk mitigated by current margins."
            },
            {
              "agent": "NEUTRAL_STRUCTURALIST",
              "verdict": "HOLD",
              "confidence": 0.90,
              "self_critique": "Momentum metrics decouple authorized per ENH_86."
            }
          ]
        }
      }
    ],
    "risk_management": {
      "alpha_friction_hurdle": 0.0117,
      "base_currency": "EUR"
    }
  }
}
"""

test_interception(user_payload)
