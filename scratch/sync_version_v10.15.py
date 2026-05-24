import os
import glob
import datetime

OLD_VERSION = "v10.14-Cross-Repo-Sync"
NEW_VERSION = "v10.15-Free-Tier-Llm-Routing"

CHANGELOG_ENTRY = """
### v10.15-Free-Tier-Llm-Routing *(2026-05-22)*
- **Architectural Update:** Implemented the Gemini Free Tier Key Routing Protocol in `agent_framework.py`.
- **System Cost Optimization:** Enabled dedicated key routing for free-tier LLMs (such as `FLASH` and `GEMMA` tiers) via `GEMINI_FREE_TIER_API_KEY` (configured in `config.json` or loaded from environment variables).
- **Proactive Fallbacks:** Integrated real-time client failovers, seamlessly falling back to the primary key upon encountering rate limits (429), quota limits, or authentication failures.
- **Config & Model Calibration:** Synchronized the `Mode Selection Matrix` in `terminal.md` with active subagent modes, and appended the `GEMINI_FREE_TIER_API_KEY` placeholder in `config.json`.
- **Parity Alignment:** Performed a global version synchronization across all subagent instruction sets, rules, and the custodian engine to maintain absolute structural integrity.
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
            
        if OLD_VERSION in content:
            new_content = content.replace(OLD_VERSION, NEW_VERSION)
            
            # If it's README.md, append changelog
            if os.path.basename(file_path).lower() == "readme.md":
                # Find the changelog section
                if "## 📋 Changelog" in new_content:
                    new_content = new_content.replace("## 📋 Changelog", f"## 📋 Changelog\n{CHANGELOG_ENTRY.strip()}")
                elif "## Changelog" in new_content:
                    new_content = new_content.replace("## Changelog", f"## Changelog\n{CHANGELOG_ENTRY.strip()}")
                else:
                    new_content += f"\n## Changelog\n{CHANGELOG_ENTRY.strip()}"
                    
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            print(f"Updated: {file_path}")

process_repo(r"c:\github\gemini_cli_subagent_system")
print("Sync complete.")
