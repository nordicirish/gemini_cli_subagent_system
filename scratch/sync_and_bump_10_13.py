import os
import re

# Files list
main_files = [
    "antigravity.md",
    "README.md",
    "terminal.md",
    "bullish_gem.md",
    "data_analyst.md",
    "execution.md",
    "gex_engine.md",
    "macro_narrative_engine.md",
    "macro_sentinel.md",
    "neutral_gem.md",
    "post_trade_review.md",
    "red_team_gem.md",
    "rule_enforcer_engine.md",
    "state_validation_router.md",
    "structural_engine.md"
]
rules_file = "gemini_gem_rules/rules.md"
all_files = main_files + [rules_file]

new_version = "v10.13-WAC-Persistence-Sync"
new_sync_id = "ANTIGRAVITY-GLOBAL-SYNC-v10.13-WAC-Persistence-Sync"

print("Starting global synchronization and version bump to:", new_version)

# 1. Update Version strings across all 16 files
# Matches both **Version:** v10.XX... and Version: v10.XX...
version_re = re.compile(r'(\*?\*?Version:\*?\*?\s+)v10\.[0-9]+(-[A-Za-z0-9-]+)?')
sync_id_re = re.compile(r'(\*?\*?Sync_ID:\*?\*?\s+)ANTIGRAVITY-GLOBAL-SYNC-v10\.[0-9]+(-[A-Za-z0-9-]+)?')

for filepath in all_files:
    if not os.path.exists(filepath):
        print(f"Error: File {filepath} does not exist.")
        continue
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Replace Version
    new_content, count = version_re.subn(rf"\g<1>{new_version}", content)
    if count > 0:
        print(f"Bumped version in {filepath} ({count} replacement(s))")
    else:
        print(f"Warning: Could not find version string in {filepath}")
    
    # Replace Sync_ID in antigravity.md or other files
    new_content, count = sync_id_re.subn(rf"\g<1>{new_sync_id}", new_content)
    if count > 0:
        print(f"Bumped Sync_ID in {filepath}")
        
    with open(filepath, 'w', encoding='utf-8', newline='\n') as f:
        f.write(new_content)

# 2. Add changelog entry in README.md
print("\nUpdating README.md changelog...")
with open("README.md", 'r', encoding='utf-8') as f:
    readme_content = f.read()

target_changelog_header = "### 🔒 v10.12 Regime Guardrail & Telemetry Sync (2026-05-22)"
new_changelog_block = """### 🔒 v10.13 WAC Persistence Sync (2026-05-22)

| Enhancement | ID | Summary |
|---|---|---|
| **WAC State Persistence** | `DATA_INTEGRITY` | Hardens the ingestion bridge (`fetch_stocks.py`) to carry forward `wac` and `historical_context` during Council promotions, preventing state amnesia upon execution confirmation. |

"""

if target_changelog_header in readme_content:
    readme_content = readme_content.replace(target_changelog_header, new_changelog_block + target_changelog_header, 1)
    print("Added v10.13 changelog block to README.md.")
else:
    print("Error: Could not find v10.12 changelog header in README.md.")

with open("README.md", 'w', encoding='utf-8', newline='\n') as f:
    f.write(readme_content)

print("\nPrimary sync complete!")
