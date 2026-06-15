import glob
import os
import sys

def main():
    target_version = "v11.13-API-Gateway-Retry"
    legacy_versions = [
        "v11.12-Merton-JSON-Output",
        "v11.11-YF-TLS-Fix",
        "v11.10-UI-Clean-Debate-Collapse",
        "v11.03-GDrive-Decoupling-Patch"
    ]
    
    files_to_update = [
        "gem_trading_rules/rules.md",
        "INSTRUCTIONS.md",
        "README.md"
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
