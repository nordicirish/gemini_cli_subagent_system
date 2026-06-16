import glob
import os

def main():
    target_version = "v11.19-Orchestrator-Capability-Probing"
    legacy_versions = [
        "v11.18-Payload-Slicing-JIT-Cache",
        "v11.17-Dynamic-Model-Cache-Deprecation",
        "v11.16-Cache-Auto-Recovery",
        "v11.15-README-Engines-Sync",
        "v11.14-UI-Model-Selector-Sync",
        "v11.13-API-Gateway-Retry",
        "v11.12-Merton-JSON-Output",
        "v11.11-YF-TLS-Fix",
        "v11.10-UI-Clean-Debate-Collapse",
        "v11.09-UI-Debate-Toggle-Sync",
        "v11.08-UI-DeepDive-Patch",
        "v11.03-GDrive-Decoupling-Patch",
        "v11.03-SPY-Intraday-HUD",
        "v11.01-L249-Cascade-Patch",
        "v11.00-NotebookLM-Bridge",
        "v10.80-Advanced-Oscillator-Integration",
        "v10.70-News-Scan-Integration",
        "v10.69-Diversified-Retrieval-Matrix",
        "v10.50-Conflict-Resolutions",
        "v10.70-Indices-VWAP-and-3Dec-GEX"
    ]
    
    files_to_update = [
        "gem_trading_rules/rules.md",
        "INSTRUCTIONS.md",
        "antigravity.md",
        "README.md",
        "engine_instructions/terminal.md",
        "python/main.py"
    ]
    
    # Add all engine files
    engine_files = glob.glob("engine_instructions/*.md")
    files_to_update.extend(engine_files)
    
    print(f"Synchronizing all registry files to version: {target_version}...")
    
    updated_count = 0
    for file_path in files_to_update:
        if not os.path.exists(file_path):
            print(f"Warning: File {file_path} not found.")
            continue
            
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        new_content = content
        for legacy in legacy_versions:
            new_content = new_content.replace(legacy, target_version)
            
        if new_content != content:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            print(f"Successfully synchronized {file_path}")
            updated_count += 1
        else:
            if target_version in content:
                print(f"Already synchronized: {file_path}")
            else:
                print(f"No matches found in {file_path}")
                
    print(f"Completed synchronization. Updated {updated_count} files.")

if __name__ == "__main__":
    main()
