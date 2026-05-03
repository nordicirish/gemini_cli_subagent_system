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
                if line.startswith('## #'):
                    current_category = line[4:].lower()
                elif line.startswith('- **L-'):
                    md_lessons.append((current_category, line))
    
    # Track existing IDs and text in MD to avoid duplicates
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
    PRIMARY_CATEGORIES = ['volatility', 'defense', 'fundamentals', 'macro', 'risk-management', 'catalysts', 'technical', 'gamma', 'system-design']

    for lesson in lessons:
        lesson_id = lesson.get('id', '?')
        rule = lesson.get('rule', lesson.get('lesson', ''))
        tags = lesson.get('tags', [])
        
        # Skip if already in MD manually (by ID or text)
        if isinstance(lesson_id, int) and lesson_id in existing_md_ids:
            continue
        rule_text_only = rule.strip().lower()
        if rule_text_only in existing_md_text:
            continue
            
        primary = "other"
        for tag in tags:
            t_lower = tag.lower()
            if t_lower in PRIMARY_CATEGORIES:
                primary = t_lower
                break
        else:
            if tags:
                primary = tags[0].lower()
        
        categories[primary].append(lesson)

    # 3. Generate new MD content
    md_lines = ["# 📘 Trade Lessons Registry", ""]
    
    all_categories = set(categories.keys())
    for cat, _ in md_lessons:
        all_categories.add(cat)
        
    sorted_categories = sorted(list(all_categories), key=lambda x: (x not in ['volatility', 'defense', 'fundamentals'], x))

    for category in sorted_categories:
        md_lines.append(f"## #{category.upper()}")
        
        # Add existing MD lessons for this category
        for cat, line in md_lessons:
            if cat == category:
                md_lines.append(line)
                
        # Add JSON lessons for this category
        if category in categories:
            for lesson in categories[category]:
                lesson_id = lesson.get('id', '?')
                rule = lesson.get('rule', lesson.get('lesson', ''))
                tags = lesson.get('tags', [])
                
                tag_str = " ".join([f"#{t}" for t in tags]) if tags else ""
                
                if rule.startswith("L-") and ":" in rule:
                    md_lines.append(f"- **L-{lesson_id}:** {rule} {tag_str}".strip())
                else:
                    md_lines.append(f"- **L-{lesson_id}:** {rule} {tag_str}".strip())
                    
        md_lines.append("")
        md_lines.append("---")
        md_lines.append("")

    # Clean up trailing dashes
    while md_lines and (md_lines[-1] == "" or md_lines[-1] == "---"):
        md_lines.pop()

    with open(md_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(md_lines) + "\n")
    
    print(f"Successfully converted and merged {len(lessons)} lessons to {md_path}")

if __name__ == "__main__":
    convert_json_to_md('trade_lessons.json', 'trade_lessons.md')
