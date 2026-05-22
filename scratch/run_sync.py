import os
import shutil
import glob

src_dir = r"c:\github\gem_trading_agent_system"
dst_dir = r"c:\github\gemini_cli_subagent_system"

print(f"Initiating ENH_100-SYNC Unidirectional Pull...")

# 1. Sync *.md (Engine instructions and trade_lessons.md)
md_files = glob.glob(os.path.join(src_dir, "*.md"))
for src_file in md_files:
    basename = os.path.basename(src_file)
    if basename.lower() in ["readme.md", "antigravity.md"]:
        continue
    dst_file = os.path.join(dst_dir, basename)
    shutil.copy2(src_file, dst_file)
    print(f"Synced: {basename}")

# 2. Sync specific JSON files
json_files = ["trade_lessons.json", "decision_log.json"]
for f in json_files:
    src_file = os.path.join(src_dir, f)
    dst_file = os.path.join(dst_dir, f)
    if os.path.exists(src_file):
        shutil.copy2(src_file, dst_file)
        print(f"Synced: {f}")

# 3. SSoT Mapping
src_ssot = os.path.join(src_dir, "local_ssot_shadow.json")
dst_ssot = os.path.join(dst_dir, "ssot.json")
if os.path.exists(src_ssot):
    shutil.copy2(src_ssot, dst_ssot)
    print(f"Synced SSoT: local_ssot_shadow.json -> ssot.json")

# 4. Sync rules.md
src_rules = os.path.join(src_dir, "gemini_gem_rules", "rules.md")
dst_rules = os.path.join(dst_dir, "GEM_Trading_Rules", "rules.md")
if os.path.exists(src_rules):
    os.makedirs(os.path.dirname(dst_rules), exist_ok=True)
    shutil.copy2(src_rules, dst_rules)
    print(f"Synced Rules: rules.md")

print("Sync Complete.")
