import json
import os
import re

def super_clean():
    path = 'trade_lessons.json'
    if not os.path.exists(path):
        return
    
    with open(path, 'r') as f:
        data = json.load(f)
    
    lessons = data.get("trade_lessons", [])
    cleaned = []
    seen_text = set()
    
    # 1. New lessons provided by user
    user_new = [
        { "category": "MACRO", "content": "Treasury auction tails mandate 120-min execution freeze." },
        { "category": "TECHNICAL", "content": "$14.00 UMAC node converted to active resistance; requires VWAP reclaim." },
        { "category": "GAMMA", "content": "SPY Intraday Gamma flips serve as Bunker Thaw/Re-engagement signals." },
        { "category": "RISK", "content": "Concurrent SPY/IEF Short Gamma alignment functions as primary Bunker Trigger." },
        { "category": "RELATIVE-STRENGTH", "content": "Clinical anchors (DFTX) with RSI > 50 exempt from standard macro trims." }
    ]
    
    # 2. Process existing lessons
    for l in lessons:
        rule = l.get("rule", l.get("content", ""))
        if not rule: continue
        
        # Aggressive corruption filter
        if '\\r' in repr(rule) or '\\n' in repr(rule) or '\"' in rule or ']:' in rule:
            if "[CODIFIED:" not in rule: 
                continue
        
        if len(rule) > 300 and "{" in rule: continue # Likely JSON
        
        # Normalize
        txt_low = rule.strip().lower()
        if txt_low in seen_text: continue
        seen_text.add(txt_low)
        
        tags = l.get("tags", [])
        if "category" in l: tags.append(l["category"])
        
        cleaned.append({"rule": rule, "tags": list(set(tags))})

    # 3. Add user new lessons (ensure they are at the end and unique)
    for item in user_new:
        txt_low = item["content"].strip().lower()
        if txt_low in seen_text:
            # Update existing if found
            for c in cleaned:
                if c["rule"].strip().lower() == txt_low:
                    c["tags"] = list(set(c["tags"] + [item["category"]]))
                    break
        else:
            seen_text.add(txt_low)
            cleaned.append({"rule": item["content"], "tags": [item["category"]]})

    # 4. Final Re-indexing
    for i, l in enumerate(cleaned):
        l["id"] = i + 1
        
    data["trade_lessons"] = cleaned
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"Super clean complete. Kept {len(cleaned)} unique lessons.")

if __name__ == "__main__":
    super_clean()
