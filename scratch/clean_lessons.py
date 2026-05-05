import json
import os

def clean_lessons():
    path = 'trade_lessons.json'
    if not os.path.exists(path):
        return
    
    with open(path, 'r') as f:
        data = json.load(f)
    
    lessons = data.get("trade_lessons", [])
    cleaned = []
    
    for l in lessons:
        rule = l.get("rule", l.get("content", ""))
        # If the rule contains "portfolio" and "shares", it's likely a corrupted JSON dump
        if "portfolio" in rule and "shares" in rule:
            continue
        # If rule is very long and looks like JSON
        if len(rule) > 500 and "{" in rule and "}" in rule:
            continue
        cleaned.append(l)
        
    # Re-index
    for i, l in enumerate(cleaned):
        l["id"] = i + 1
        
    data["trade_lessons"] = cleaned
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"Cleaned lessons. Kept {len(cleaned)} out of {len(lessons)}.")

if __name__ == "__main__":
    clean_lessons()
