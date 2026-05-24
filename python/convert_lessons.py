import json
import os
import re
from collections import defaultdict

def convert_json_to_md(json_path, md_path):
    if not os.path.exists(json_path):
        print(f"Error: {json_path} not found.")
        return

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    lessons = data.get("trade_lessons", [])
    if not lessons:
        print("No lessons found in JSON.")
        return

    # 1. Read existing MD rules (Legacy preservation logic removed to support ENH_53 pruning)
    # We no longer re-insert lessons found in MD but missing from JSON to ensure SSoT integrity.
    # Manual additions should be made to trade_lessons.json.
    
    # 2. Group JSON rules
    categories = defaultdict(list)
    PRIMARY_CATEGORIES = ['volatility', 'defense', 'fundamentals', 'macro', 'risk-management', 'catalysts', 'technical', 'gamma', 'system-design', 'position-sizing', 'biotech', 'valuation', 'supply-chain']

    json_ids = set()
    for lesson in lessons:
        lesson_id = lesson.get('id')
        if isinstance(lesson_id, int):
            json_ids.add(lesson_id)
        
        # Determine best rule text
        rule = lesson.get('rule', lesson.get('lesson', lesson.get('mandate', lesson.get('content', ''))))
        title = lesson.get('title', '')
        if title and rule:
            rule = f"{title}: {rule}"
        elif title:
            rule = title
            
        lesson['_final_rule'] = rule # Store for later
        
        tags = lesson.get('tags', [])
        primary = "other"
        for tag in tags:
            t_lower = tag.lower().replace('#', '')
            if t_lower in PRIMARY_CATEGORIES:
                primary = t_lower
                break
        else:
            if tags:
                primary = tags[0].lower().replace('#', '')
        
        categories[primary].append(lesson)

    # 3. Generate new MD content
    md_lines = ["# 📘 Trade Lessons Registry", ""]
    
    # Categories to display
    all_categories = sorted(list(categories.keys()), 
                            key=lambda x: (x not in PRIMARY_CATEGORIES, x))

    for category in all_categories:
        md_lines.append(f"## #{category.upper()}")
        
        # Add JSON lessons (SSoT)
        if category in categories:
            for lesson in categories[category]:
                lesson_id = lesson.get('id', '?')
                rule = lesson.get('_final_rule', '')
                tags = lesson.get('tags', [])
                
                # Clean tags
                clean_tags = []
                for t in tags:
                    t = t.strip()
                    if not t: continue
                    if not t.startswith('#'): t = f"#{t}"
                    clean_tags.append(t)
                
                tag_str = " ".join(clean_tags)
                
                line = f"- **L-{lesson_id}:** {rule} {tag_str}".strip()
                md_lines.append(line)
                    
        md_lines.append("")
        md_lines.append("---")
        md_lines.append("")

    # Clean up trailing dashes
    while md_lines and (md_lines[-1] == "" or md_lines[-1] == "---"):
        md_lines.pop()

    with open(md_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(md_lines) + "\n")
    
    print(f"Successfully synchronized {len(lessons)} lessons from JSON to {md_path}")


if __name__ == "__main__":
    convert_json_to_md('context/trade_lessons.json', 'context/trade_lessons.md')
