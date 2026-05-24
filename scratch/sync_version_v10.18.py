import os
import glob

OLD_VERSION = "v10.17-Asset-Protection-Sync"
NEW_VERSION = "v10.18-SSot-Schema-Sync"

CHANGELOG_ENTRY = """
### v10.18-SSot-Schema-Sync *(2026-05-22)*
- **SSoT Schema Realignment:** Realigned the `/api/basket` endpoints in `web_server.py` and aligned all basket and watchlist APIs in `fetch_stocks.py` to seamlessly support both flat and nested `mutable_state` structures inside `ssot.json`.
- **UI Redundancy Purge:** Removed obsolete clipboard-based "Export to Council" and "Import from Council" cards from `static/index.html` on both desktop and mobile layouts in favor of the active live SSE-enabled Gemini AI Council Chat Overlay.
- **Dynamic Hot-Reloading:** Added direct hot-reloading triggers to reload active and macro tickers within the background daemon instantly upon dashboard updates.
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
        if "ANTIGRAVITY-GLOBAL-SYNC-v10.17-Asset-Protection-Sync" in new_content:
            new_content = new_content.replace(
                "ANTIGRAVITY-GLOBAL-SYNC-v10.17-Asset-Protection-Sync",
                "ANTIGRAVITY-GLOBAL-SYNC-v10.18-SSot-Schema-Sync"
            )
            has_changed = True

        if os.path.basename(file_path).lower() == "readme.md":
            # Find the changelog section
            if "## 📋 Changelog" in new_content:
                new_content = new_content.replace("## 📋 Changelog", f"## 📋 Changelog\n{CHANGELOG_ENTRY.strip()}\n")
                has_changed = True
            elif "## Changelog" in new_content:
                new_content = new_content.replace("## Changelog", f"## Changelog\n{CHANGELOG_ENTRY.strip()}\n")
                has_changed = True
                
        if has_changed:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            print(f"Updated: {file_path}")

process_repo(r"c:\github\gemini_cli_subagent_system")
print("Sync complete.")
