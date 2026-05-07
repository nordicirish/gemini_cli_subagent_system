import json
import os
from convert_lessons import convert_json_to_md

def add_new_lesson():
    path = 'trade_lessons.json'
    md_path = 'trade_lessons.md'
    if not os.path.exists(path):
        return
    
    with open(path, 'r') as f:
        data = json.load(f)
    
    lessons = data.get("trade_lessons", [])
    
    new_lesson = {
        "id": 166, # Next in sequence
        "rule": 'The "Heuristic Date Trap" confirmed: UMAC Q1 2026 earnings are forensicly locked to May 13, 2026. All "Bunker" positions and "Scaling Margin Trap" (L-128) risk assessments must be recalibrated to this 7-day acceleration.',
        "tags": ["TEMPORAL", "UMAC", "EARNINGS"]
    }
    
    # Check for duplicates
    for l in lessons:
        if l["rule"].strip() == new_lesson["rule"]:
            print("Duplicate detected. Skipping.")
            return

    lessons.append(new_lesson)
    data["trade_lessons"] = lessons
    
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
    print("Successfully added L-166 (User L-233).")
    
    # Sync to Markdown
    try:
        convert_json_to_md(path, md_path)
    except Exception as e:
        print(f"Failed to sync to MD: {e}")

if __name__ == "__main__":
    add_new_lesson()
