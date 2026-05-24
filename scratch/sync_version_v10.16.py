import os
import glob
import datetime

OLD_VERSION = "v10.15-Free-Tier-Llm-Routing"
NEW_VERSION = "v10.16-Model-Optimization-Sync"

CHANGELOG_ENTRY = """
### v10.16-Model-Optimization-Sync *(2026-05-22)*
- **Architectural Cleanup:** Centralized LLM model default strings into canonical constants (`DEFAULT_MODEL_PRO`, `DEFAULT_MODEL_FLASH`, `DEFAULT_MODEL_GEMMA`) inside `agent_framework.py` to completely eliminate hardcoding and duplication.
- **Cost-Optimized Standard Default:** Prioritized standard `gemini-2.5-pro` and `gemini-2.5-flash` as primary defaults across all mappings to minimize operational compute costs.
- **Optimal Fallback Enablement:** Integrated newer Gemini 3.x models (`gemini-3.1-pro-preview` and `gemini-3-flash-preview`) as robust secondary fallback options within `MODEL_MAPPING`.
- **Reasoning Tier Alignment:** Corrected `THINKING` mode mapping within the framework to correctly route to reasoning-heavy Pro-tier models first.
- **Global Version Parity:** Synchronized version strings to `v10.16-Model-Optimization-Sync` across all 14 subagent instructions, rules, antigravity.md, and README.md.
"""

def process_repo(repo_path):
    print(f"Processing repository: {repo_path}")
    md_files = glob.glob(os.path.join(repo_path, "*.md"))
    
    # Also check rules.md if it exists in GEM_Trading_Rules/
    rules_path = os.path.join(repo_path, "GEM_Trading_Rules", "rules.md")
    if os.path.exists(rules_path):
        md_files.append(rules_path)
        
    for file_path in md_files:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        has_changed = False
        new_content = content
        
        if OLD_VERSION in new_content:
            new_content = new_content.replace(OLD_VERSION, NEW_VERSION)
            has_changed = True
            
        # Also catch any residual sync ID variations
        if "ANTIGRAVITY-GLOBAL-SYNC-v10.15-Free-Tier-Llm-Routing" in new_content:
            new_content = new_content.replace(
                "ANTIGRAVITY-GLOBAL-SYNC-v10.15-Free-Tier-Llm-Routing",
                "ANTIGRAVITY-GLOBAL-SYNC-v10.16-Model-Optimization-Sync"
            )
            has_changed = True

        if os.path.basename(file_path).lower() == "readme.md":
            # Find the changelog section
            if "## 📋 Changelog" in new_content:
                new_content = new_content.replace("## 📋 Changelog", f"## 📋 Changelog\n{CHANGELOG_ENTRY.strip()}")
                has_changed = True
            elif "## Changelog" in new_content:
                new_content = new_content.replace("## Changelog", f"## Changelog\n{CHANGELOG_ENTRY.strip()}")
                has_changed = True
                
        if has_changed:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            print(f"Updated: {file_path}")

process_repo(r"c:\github\gemini_cli_subagent_system")
print("Sync complete.")
