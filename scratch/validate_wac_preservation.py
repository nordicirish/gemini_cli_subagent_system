import os
import json
import shutil
from fastapi.testclient import TestClient
from fetch_stocks import app

# Path to the shadow SSoT
SSOT_FILE = 'local_ssot_shadow.json'
BACKUP_FILE = 'local_ssot_shadow.json.bak'

def setup_test_state():
    # 1. Back up existing local_ssot_shadow.json if it exists
    if os.path.exists(SSOT_FILE):
        shutil.copyfile(SSOT_FILE, BACKUP_FILE)
        print("[TEST] Backed up existing local_ssot_shadow.json")
    
    # 2. Write a clean starting SSoT state with specific WAC and historical_context values
    test_state = {
      "immutable_background": {
        "role": "Gemini_Gem_Working_Data_Store",
        "base_currency": "EUR"
      },
      "mutable_state": {
        "state_context": {
          "timestamp": "2026-05-22T10:44:38-04:00",
          "status": "OPEN",
          "risk_regime": "NORMAL"
        },
        "portfolio_snapshot": [
          {
            "ticker": "UMAC",
            "shares": 2400,
            "trade_state": "LONG",
            "wac": 15.5557,
            "historical_context": "UMAC Q1 Earnings support active breakout posture."
          },
          {
            "ticker": "DFTX",
            "shares": 251,
            "trade_state": "NO_TRADE",
            "wac": 22.7241
          }
        ],
        "unallocated_cash_eur": 2745,
        "unallocated_cash_usd": 3186.3
      }
    }
    with open(SSOT_FILE, 'w', encoding='utf-8') as f:
        json.dump(test_state, f, indent=2)
    print("[TEST] Initialized test shadow SSoT with UMAC WAC = 15.5557, DFTX WAC = 22.7241")

def restore_original_state():
    # Restore original file if backed up
    if os.path.exists(BACKUP_FILE):
        shutil.copyfile(BACKUP_FILE, SSOT_FILE)
        os.remove(BACKUP_FILE)
        print("[TEST] Restored original local_ssot_shadow.json from backup")
    elif os.path.exists(SSOT_FILE):
        os.remove(SSOT_FILE)

def run_verification():
    client = TestClient(app)
    
    # The testing incoming EXECUTION_PAYLOAD (does not contain any "wac" fields)
    test_incoming_payload = """
    ```json
    {
      "EXECUTION_PAYLOAD": {
        "thoughtSignature": "context_engineering_is_the_way_to_go",
        "unallocated_cash_eur": 2745,
        "unallocated_cash_usd": 3186.30,
        "state_context": {
          "timestamp": "2026-05-22T10:44:38-04:00",
          "status": "OPEN",
          "risk_regime": "NORMAL",
          "session_indicator": "REGULAR_SESSION_TRADING_ACTIVE"
        },
        "portfolio_snapshot": [
          {
            "ticker": "UMAC",
            "shares": 2400,
            "trade_state": "LONG",
            "no_trade_reason": null,
            "trailing_stop_audit": {
              "anchor_price": 15.5557,
              "current_price": 15.745
            }
          },
          {
            "ticker": "DFTX",
            "shares": 251,
            "trade_state": "NO_TRADE",
            "no_trade_reason": "Price < VWAP. Vetoed per ENH_87."
          }
        ]
      }
    }
    ```
    """
    
    print("[TEST] Sending /api/paste request with mock payload containing NO WAC fields...")
    response = client.post("/api/paste", json={"payload": test_incoming_payload})
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    # Read the updated shadow SSoT to verify paste-level merge
    with open(SSOT_FILE, 'r', encoding='utf-8') as f:
        pasted_state = json.load(f)
        
    portfolio = pasted_state["mutable_state"]["portfolio_snapshot"]
    proposed = pasted_state["mutable_state"]["proposed_portfolio_snapshot"]
    
    # 1. Verify that WAC and historical_context were successfully carried forward to portfolio_snapshot
    umac_item = next(p for p in portfolio if p["ticker"] == "UMAC")
    assert umac_item.get("wac") == 15.5557, f"Expected WAC 15.5557 for UMAC, got {umac_item.get('wac')}"
    assert umac_item.get("historical_context") == "UMAC Q1 Earnings support active breakout posture.", "Historical context was not carried forward!"
    
    dftx_item = next(p for p in portfolio if p["ticker"] == "DFTX")
    assert dftx_item.get("wac") == 22.7241, f"Expected WAC 22.7241 for DFTX, got {dftx_item.get('wac')}"
    
    print("[PASS] Verified that WAC and historical_context were carried forward in portfolio_snapshot during paste ingestion.")
    
    # 2. Verify that WAC was successfully copied to proposed_portfolio_snapshot too
    umac_prop = next(p for p in proposed if p["ticker"] == "UMAC")
    assert umac_prop.get("wac") == 15.5557, f"Expected WAC 15.5557 in proposed for UMAC, got {umac_prop.get('wac')}"
    
    print("[PASS] Verified that WAC was carried forward in proposed_portfolio_snapshot.")
    
    # 3. Call confirm_execution to verify cost-basis persistence on execution promotion
    print("[TEST] Confirming execution via /api/confirm_execution...")
    confirm_response = client.post("/api/confirm_execution")
    assert confirm_response.status_code == 200, f"Expected 200, got {confirm_response.status_code}"
    
    # Read the final SSoT shadow state
    with open(SSOT_FILE, 'r', encoding='utf-8') as f:
        final_state = json.load(f)
        
    final_portfolio = final_state["mutable_state"]["portfolio_snapshot"]
    
    # 4. Verify that WAC values are fully preserved post-confirmation
    umac_final = next(p for p in final_portfolio if p["ticker"] == "UMAC")
    assert umac_final.get("wac") == 15.5557, f"Expected WAC 15.5557 post-confirmation, got {umac_final.get('wac')}"
    
    dftx_final = next(p for p in final_portfolio if p["ticker"] == "DFTX")
    assert dftx_final.get("wac") == 22.7241, f"Expected WAC 22.7241 post-confirmation, got {dftx_final.get('wac')}"
    
    print("[PASS] Verified cost-basis is fully preserved post-confirmation! No state amnesia detected.")

if __name__ == "__main__":
    setup_test_state()
    try:
        run_verification()
        print("\n[SUCCESS] All verification tests passed flawlessly! Data integrity is 100% synchronized.")
    finally:
        restore_original_state()
