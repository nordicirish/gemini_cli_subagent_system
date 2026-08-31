import glob
import os
import re

def main():
    target_version = "v11.38-TradingView-Lightweight-Charts-Multimodal-Sync"
    
    files_to_update = [
        "gem_trading_rules/rules.md",
        "INSTRUCTIONS.md",
        "antigravity.md",
        "README.md",
        "python/main.py",
        "python/web_server.py",
        "python/agent_framework.py"
    ]
    
    engine_files = glob.glob("engine_instructions/*.md")
    files_to_update.extend(engine_files)
    
    print(f"Synchronizing all registry files to version: {target_version}...")
    
    updated_count = 0
    for file_path in set(files_to_update):
        if not os.path.exists(file_path):
            continue
            
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        # Replace **Version:** headers
        new_content = re.sub(
            r"\*\*Version:\*\*\s+v11\.[A-Za-z0-9\.\-]+",
            f"**Version:** {target_version}",
            content
        )
        # Replace Sync_ID headers
        new_content = re.sub(
            r"\*\*Sync_ID:\*\*\s+ANTIGRAVITY-GLOBAL-SYNC-v11\.[A-Za-z0-9\.\-]+",
            f"**Sync_ID:** ANTIGRAVITY-GLOBAL-SYNC-{target_version}",
            new_content
        )
        
        # Replace specific prior version strings
        prior_versions = [
            "v11.35-Market-Data-Cache-Baseline-Sync",
            "v11.34-Gemini-3.7-Flash-Extended-Model-Sync",
            "v11.33-Volume-Override-and-PreMkt-ShortGamma-Gap-Sync",
            "v11.25-Catalyst-Override-and-Short-Gamma-Liquidation",
            "v11.24-High-Beta-Swing-Trading-Architecture",
            "v11.23-UI-Feedback-Cost-Fix"
        ]
        
        if file_path.endswith(".md") and "README.md" not in file_path:
            for pv in prior_versions:
                new_content = new_content.replace(pv, target_version)
                
        if new_content != content:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            print(f"Successfully synchronized {file_path}")
            updated_count += 1
        else:
            print(f"No changes required for: {file_path}")
            
    print(f"Completed synchronization. Updated {updated_count} files.")

if __name__ == "__main__":
    main()
