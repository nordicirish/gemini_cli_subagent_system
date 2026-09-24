import glob
import os
import re

def main():
    target_version = "v11.52-Gamma-Cascade-Alpha-Rotation-Sentinel-Sync"
    
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
    
    print(f"Synchronizing all scope files to version: {target_version}...")
    
    updated_count = 0
    for file_path in sorted(set(files_to_update)):
        if not os.path.exists(file_path):
            print(f"Skipping missing: {file_path}")
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
        
        # Replace python main.py version print
        new_content = re.sub(
            r'print\(f"\s+Version\s+:\s+v11\.[A-Za-z0-9\.\-]+"\)',
            f'print(f"   Version : {target_version}")',
            new_content
        )
        
        # Replace specific prior version strings
        prior_versions = [
            "v11.51-Design-Principles-Architecture-Harness",
            "v11.49-ENH-255-Daily-Trading-Peak-Fib-Sync",
            "v11.48-ENH-254-Fib-Profit-Taking-Tranches-Sync"
        ]
        
        # In README.md, do not overwrite changelog headers of past versions
        if file_path != "README.md":
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
