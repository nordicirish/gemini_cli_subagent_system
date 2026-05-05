import json
import os

def final_reconciliation():
    path = 'trade_lessons.json'
    if not os.path.exists(path):
        return
    
    with open(path, 'r') as f:
        data = json.load(f)
    
    lessons = data.get("trade_lessons", [])
    cleaned = []
    
    # Preserve truly valid lessons
    for l in lessons:
        rule = l.get("rule", l.get("content", ""))
        # Filter corruption
        if any(c in rule for c in ['"', '\r', '\n', ']', '{', '}']) and len(rule) > 100:
             if "[CODIFIED:" not in rule: # Keep codified ones even if they have newlines
                continue
        if "portfolio" in rule or "status" in rule or "current_price" in rule:
            continue
        
        # Normalize keys
        new_l = {"rule": rule, "tags": l.get("tags", [])}
        if "category" in l:
            new_l["tags"].append(l["category"])
        cleaned.append(new_l)

    # These are the 5 lessons the user just provided
    new_5 = [
        { "category": "MACRO", "content": "Treasury auction tails mandate 120-min execution freeze." },
        { "category": "TECHNICAL", "content": "$14.00 UMAC node converted to active resistance; requires VWAP reclaim." },
        { "category": "GAMMA", "content": "SPY Intraday Gamma flips serve as Bunker Thaw/Re-engagement signals." },
        { "category": "RISK", "content": "Concurrent SPY/IEF Short Gamma alignment functions as primary Bunker Trigger." },
        { "category": "RELATIVE-STRENGTH", "content": "Clinical anchors (DFTX) with RSI > 50 exempt from standard macro trims." }
    ]
    
    for item in new_5:
        cleaned.append({
            "rule": item["content"],
            "tags": [item["category"]]
        })

    # Re-index everything
    for i, l in enumerate(cleaned):
        l["id"] = i + 1
        
    data["trade_lessons"] = cleaned
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"Final reconciliation complete. Kept {len(cleaned)} lessons.")

if __name__ == "__main__":
    final_reconciliation()
