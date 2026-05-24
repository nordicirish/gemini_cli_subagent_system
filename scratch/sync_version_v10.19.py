import os
import glob

OLD_VERSION = "v10.18-SSot-Schema-Sync"
NEW_VERSION = "v10.19-Cache-Tool-Validation-Sync"

CHANGELOG_ENTRY = """
### v10.19-Cache-Tool-Validation-Sync *(2026-05-22)*
- **Context Cache Tool Validation Resolution:** Fixed Pydantic validation errors during client-side `CreateCachedContentConfig` instantiation inside `agent_framework.py` by dynamically parsing and converting all Python callable tools into valid `types.Tool` models wrapping `FunctionDeclaration` objects using the `types.FunctionDeclaration.from_callable` SDK method.
- **Global Architectural Parity:** Proactively bumped all 14 engine instruction sets, Master rules.md SSoT, and terminal orchestrator versions to `v10.19-Cache-Tool-Validation-Sync` per MANDATE_29 across all synchronized repositories.
"""

def process_repo(repo_path):
    if not os.path.exists(repo_path):
        print(f"Repository path does not exist: {repo_path}")
        return
        
    print(f"Processing repository: {repo_path}")
    md_files = glob.glob(os.path.join(repo_path, "*.md"))
    
    # Also check rules.md if it exists in GEM_Trading_Rules/
    rules_path = os.path.join(repo_path, "GEM_Trading_Rules", "rules.md")
    if os.path.exists(rules_path):
        md_files.append(rules_path)
        
    # Also scan nested directories for instruction sets (e.g. .agents/rules/)
    agents_rules_path = os.path.join(repo_path, ".agents", "rules", "antigravity.md")
    if os.path.exists(agents_rules_path):
        md_files.append(agents_rules_path)
        
    for file_path in md_files:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        has_changed = False
        new_content = content
        
        if OLD_VERSION in new_content:
            new_content = new_content.replace(OLD_VERSION, NEW_VERSION)
            has_changed = True
            
        # Also catch any residual sync ID variations
        old_sync_id = f"ANTIGRAVITY-GLOBAL-SYNC-{OLD_VERSION}"
        new_sync_id = f"ANTIGRAVITY-GLOBAL-SYNC-{NEW_VERSION}"
        if old_sync_id in new_content:
            new_content = new_content.replace(old_sync_id, new_sync_id)
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
process_repo(r"c:\github\gem_trading_agent_system")
print("Sync complete.")
