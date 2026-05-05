import json
import os
import re
from fastapi.responses import JSONResponse

# Mocking the parts of fetch_stocks.py needed for the test
def mock_handle_paste(clip_data):
    json_payload = {}
    lessons_from_md = []

    # 1. Extract JSON SSoT block and isolate Markdown part
    json_match = re.search(r"```json\s*(\{.*?\})\s*```", clip_data, re.IGNORECASE | re.DOTALL)
    md_part = clip_data
    if json_match:
        try:
            json_payload = json.loads(json_match.group(1).strip())
            md_part = clip_data[:json_match.start()] + clip_data[json_match.end():]
        except: pass
    else:
        # Fallback for naked JSON
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", clip_data, re.IGNORECASE | re.DOTALL)
        if match:
            try:
                json_payload = json.loads(match.group(1).strip())
                md_part = clip_data[:match.start()] + clip_data[match.end():]
            except: pass
        else:
            # Last resort: try to find braces
            start_brace = clip_data.find('{')
            end_brace = clip_data.rfind('}')
            if start_brace != -1 and end_brace != -1 and end_brace > start_brace:
                try:
                    json_payload = json.loads(clip_data[start_brace:end_brace+1].strip())
                    md_part = clip_data[:start_brace] + clip_data[end_brace+1:]
                except: pass

    payload = json_payload
    
    # 3. v8.6 Forensic Lesson Hunt: Recursively find any key containing 'trade_lessons'
    def hunt_and_extract_lessons(data):
        found_lessons = []
        found_compressed = None
        if not isinstance(data, dict): return found_lessons, found_compressed
        
        keys_to_prune = []
        for k, v in data.items():
            k_low = k.lower()
            if "trade_lessons" in k_low or "trade-lessons" in k_low:
                if "compressed" in k_low:
                    found_compressed = v
                else:
                    if isinstance(v, list):
                        for item in v:
                            if isinstance(item, dict):
                                if "content" in item and "rule" not in item:
                                    item["rule"] = item.pop("content")
                                if "category" in item:
                                    cat = item.pop("category")
                                    if "tags" not in item: item["tags"] = []
                                    if cat not in item["tags"]: item["tags"].append(cat)
                            found_lessons.append(item)
                    elif isinstance(v, dict):
                        if "trade_lessons" in v and isinstance(v["trade_lessons"], list):
                            found_lessons.extend(v["trade_lessons"])
                        else:
                            if "content" in v and "rule" not in v:
                                v["rule"] = v.pop("content")
                            found_lessons.append(v)
                keys_to_prune.append(k)
            elif k in ("mutable_state", "EXECUTION_PAYLOAD") and isinstance(v, dict):
                nested_lessons, nested_compressed = hunt_and_extract_lessons(v)
                found_lessons.extend(nested_lessons)
                if nested_compressed: found_compressed = nested_compressed
        
        for k in keys_to_prune:
            data.pop(k, None)
        return found_lessons, found_compressed

    extracted_trade_lessons, extracted_compressed = hunt_and_extract_lessons(payload)
    return extracted_trade_lessons, extracted_compressed

# The test payload
test_clip = """
{
  "mutable_state": {
    "session_trade_lessons": [
      { "id": "L-228", "category": "MACRO", "content": "Treasury auction tails mandate 120-min execution freeze." },
      { "id": "L-229", "category": "TECHNICAL", "content": "$14.00 UMAC node converted to active resistance; requires VWAP reclaim." },
      { "id": "L-230", "category": "GAMMA", "content": "SPY Intraday Gamma flips serve as Bunker Thaw/Re-engagement signals." },
      { "id": "L-231", "category": "RISK", "content": "Concurrent SPY/IEF Short Gamma alignment functions as primary Bunker Trigger." },
      { "id": "L-232", "category": "RELATIVE-STRENGTH", "content": "Clinical anchors (DFTX) with RSI > 50 exempt from standard macro trims." }
    ]
  }
}
"""

if __name__ == "__main__":
    lessons, compressed = mock_handle_paste(test_clip)
    print(f"Extracted Lessons Count: {len(lessons)}")
    for l in lessons:
        print(f" - ID: {l.get('id')} | Rule: {l.get('rule')} | Tags: {l.get('tags')}")
    print(f"Compressed Lessons: {compressed}")
