import os
import re

old_version_pattern = re.compile(r"v10\.\d{2}-[a-zA-Z0-9\-]+")
new_ver_suffix = "v10.37-Loading-Aesthetics-and-Custodian-Shrink"

def update_file(filepath):
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        return False
    
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Replace version strings
    matches = old_version_pattern.findall(content)
    if matches:
        # Check if version is already matching
        if any(m == new_ver_suffix for m in matches):
            print(f"File already updated: {filepath}")
            return False
        
        updated_content = old_version_pattern.sub(new_ver_suffix, content)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(updated_content)
        print(f"Updated: {filepath}")
        return True
    return False

# 1. Update engine instructions
engine_dir = "engine_instructions"
updated_count = 0
if os.path.exists(engine_dir):
    for filename in os.listdir(engine_dir):
        if filename.endswith(".md"):
            filepath = os.path.join(engine_dir, filename)
            if update_file(filepath):
                updated_count += 1

# 2. Update core registry files
core_files = [
    "README.md",
    "python/agent_framework.py",
    "gem_trading_rules/rules.md",
    ".agents/rules/antigravity.md"
]

for rel_path in core_files:
    if update_file(rel_path):
        updated_count += 1

# 3. Update version queries in index.html
html_path = "static/index.html"
if os.path.exists(html_path):
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Look for query strings like v=10.13 and replace them with v=10.37
    updated_html = re.sub(r"\?v=10\.\d+", "?v=10.37", content)
    if updated_html != content:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(updated_html)
        print(f"Updated index.html asset cache queries to v10.37")
        updated_count += 1

print(f"Total files synchronized: {updated_count}")
