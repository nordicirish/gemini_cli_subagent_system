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

    # 1. Read existing MD rules to preserve any manual additions
    md_lessons = []
    if os.path.exists(md_path):
        with open(md_path, 'r', encoding='utf-8') as f:
            current_category = 'other'
            for line in f:
                line = line.strip()
                if not line: continue
                if line.startswith('## #'):
                    current_category = line[4:].lower()
                elif line.startswith('- **L-'):
                    md_lessons.append((current_category, line))
    
    # Track existing IDs and text in MD
    existing_md_ids = set()
    existing_md_text = set()
    for _, line in md_lessons:
        # extract ID from '- **L-123:**'
        match = re.search(r'-\s*\*\*L-(\d+):\*\*\s*(.*)', line)
        if match:
            existing_md_ids.add(int(match.group(1)))
            # Strip tags for comparison
            text_only = re.sub(r'#[\w-]+', '', match.group(2)).strip().lower()
            existing_md_text.add(text_only)

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
    all_categories = sorted(list(set(list(categories.keys()) + [c for c, _ in md_lessons])), 
                            key=lambda x: (x not in PRIMARY_CATEGORIES, x))

    for category in all_categories:
        md_lines.append(f"## #{category.upper()}")
        
        # Track what we've added to this category to avoid duplicates
        added_in_cat = set()

        # Add JSON lessons first (SSoT)
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
                if isinstance(lesson_id, int):
                    added_in_cat.add(lesson_id)
                
        # Add existing MD lessons ONLY if they aren't in the JSON (Manual additions)
        for cat, line in md_lessons:
            if cat == category:
                match = re.search(r'-\s*\*\*L-(\d+):\*\*\s*', line)
                if match:
                    lid = int(match.group(1))
                    if lid not in json_ids and lid not in added_in_cat:
                        md_lines.append(line)
                        added_in_cat.add(lid)
                else:
                    # Naked line or something weird, keep it if not already added by text? 
                    # For simplicity, if it doesn't have an L-id we recognize, we'll keep it.
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
    convert_json_to_md('trade_lessons.json', 'trade_lessons.md')
