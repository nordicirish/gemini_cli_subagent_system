import re
import json
import os

def md_to_json(md_path, json_path):
    if not os.path.exists(md_path):
        print(f"Error: {md_path} not found.")
        return

    with open(md_path, 'r', encoding='utf-8') as f:
        content = f.read()

    lessons = []
    # Pattern to match: - **L-123:** Rule text #tag1 #tag2
    # Supporting multiline rules until the next bullet or header
    pattern = r'-\s*\*\*L-(\d+):\*\*\s*(.*?)(?=\s*\n-\s*\*\*L-|\s*\n##|\s*\n$|$)'
    
    for m in re.finditer(pattern, content, re.DOTALL):
        l_id = int(m.group(1))
        full_text = m.group(2).strip()
        
        # Extract tags
        tags = re.findall(r'#([\w-]+)', full_text)
        # Remove tags from rule text
        rule = re.sub(r'#[\w-]+', '', full_text).strip()
        
        lessons.append({
            "id": l_id,
            "rule": rule,
            "tags": tags
        })

    # Sort by ID
    lessons.sort(key=lambda x: x['id'])
    
    output = {"trade_lessons": lessons}
    
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2)
    
    print(f"Successfully restored {len(lessons)} lessons from {md_path} to {json_path}")

if __name__ == "__main__":
    md_to_json('trade_lessons.md', 'trade_lessons.json')
