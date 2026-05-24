import os
import glob
import datetime

OLD_VERSION = "v10.16-Model-Optimization-Sync"
NEW_VERSION = "v10.17-Asset-Protection-Sync"

CHANGELOG_ENTRY = """
### v10.17-Asset-Protection-Sync *(2026-05-22)*
- **SSoT Sync Decoupling & Asset Protection:** Codified strict layout asset isolation inside the `ENH_100-SYNC` cross-repository protocol in `antigravity.md` to prevent automated overrides of interactive UI overlays.
- **Frontend Stable Reference:** Created a persistent, secure local backup (`scratch/index_interactive_backup.html`) of the restored interactive Council Chat overlay template.
- **Global Architectural Parity:** Proactively bumped all 14 engine instruction sets, Master rules.md SSoT, and terminal orchestrator versions to `v10.17-Asset-Protection-Sync` per MANDATE_29.
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
        if "ANTIGRAVITY-GLOBAL-SYNC-v10.16-Model-Optimization-Sync" in new_content:
            new_content = new_content.replace(
                "ANTIGRAVITY-GLOBAL-SYNC-v10.16-Model-Optimization-Sync",
                "ANTIGRAVITY-GLOBAL-SYNC-v10.17-Asset-Protection-Sync"
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
