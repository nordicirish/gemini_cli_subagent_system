import os

old_ver_suffix = "v10.53-Sympathy-Momentum-and-RSI-Trims-UX-Consistency-Patch"
new_ver_suffix = "v10.34-Scout-UX-and-Payload-Bypass"

# 1. Update engine_instructions
engine_dir = "engine_instructions"
files_updated = 0
for filename in os.listdir(engine_dir):
    if filename.endswith(".md"):
        filepath = os.path.join(engine_dir, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        
        if old_ver_suffix in content:
            updated_content = content.replace(old_ver_suffix, new_ver_suffix)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(updated_content)
            print(f"Updated engine instruction: {filename}")
            files_updated += 1

# 2. Update other files
other_files = [
    ("README.md", "README.md"),
    ("python/agent_framework.py", "python/agent_framework.py"),
    ("gem_trading_rules/rules.md", "gem_trading_rules/rules.md"),
    (".agents/rules/antigravity.md", ".agents/rules/antigravity.md")
]

for name, rel_path in other_files:
    if os.path.exists(rel_path):
        with open(rel_path, "r", encoding="utf-8") as f:
            content = f.read()
        if old_ver_suffix in content:
            updated_content = content.replace(old_ver_suffix, new_ver_suffix)
            with open(rel_path, "w", encoding="utf-8") as f:
                f.write(updated_content)
            print(f"Updated other file: {name}")
            files_updated += 1
    else:
        print(f"File not found for version update: {rel_path}")

print(f"Total files updated: {files_updated}")
