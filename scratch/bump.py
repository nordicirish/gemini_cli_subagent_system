import os
import glob
import datetime

OLD_VERSION = "v10.13-WAC-Persistence-Sync"
NEW_VERSION = "v10.14-Cross-Repo-Sync"
CHANGELOG_ENTRY = """
### [v10.14-Cross-Repo-Sync] - {date}
- **Architectural Update:** Implemented the Cross-Repository Synchronization Protocol (ENH_100-SYNC) in `antigravity.md`.
- **System Sync:** Antigravity will now autonomously verify file hashes/timestamps between `gemini_cli_subagent_system` and `gem_trading_agent_system` and initiate a unidirectional pull to ingest newer logic, rules, lessons, and state logs.
- **SSoT Mapping:** `local_ssot_shadow.json` from the trading system is automatically mapped to `ssot.json` during the ingestion cycle.
"""

def process_repo(repo_path):
    print(f"Processing repository: {repo_path}")
    md_files = glob.glob(os.path.join(repo_path, "*.md"))
    
    # Also check rules.md if it exists in .agents/rules/
    rules_path = os.path.join(repo_path, ".agents", "rules", "rules.md")
    if os.path.exists(rules_path):
        md_files.append(rules_path)
        
    for file_path in md_files:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        if OLD_VERSION in content:
            new_content = content.replace(OLD_VERSION, NEW_VERSION)
            
            # If it's README.md, append changelog
            if os.path.basename(file_path).lower() == "readme.md":
                date_str = datetime.datetime.now().strftime("%Y-%m-%d")
                entry = CHANGELOG_ENTRY.replace("{date}", date_str)
                
                # Find the changelog section or just append to end
                if "## Changelog" in new_content:
                    new_content = new_content.replace("## Changelog", f"## Changelog\n{entry}")
                elif "## 📜 Changelog" in new_content:
                    new_content = new_content.replace("## 📜 Changelog", f"## 📜 Changelog\n{entry}")
                else:
                    new_content += f"\n## Changelog\n{entry}"
                    
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            print(f"Updated: {file_path}")

process_repo(r"c:\github\gemini_cli_subagent_system")
process_repo(r"c:\github\gem_trading_agent_system")
print("Done.")
