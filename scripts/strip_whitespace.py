import glob
import os
import re

def strip_whitespace_from_file(file_path):
    if not os.path.exists(file_path):
        print(f"Warning: File {file_path} not found.")
        return False
        
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    # 1. Strip trailing spaces and tabs from each line
    lines = content.splitlines()
    cleaned_lines = [line.rstrip() for line in lines]
    new_content = "\n".join(cleaned_lines)
    
    # 2. Collapse 3 or more blank lines down to exactly 2 (single empty line between paragraphs)
    new_content = re.sub(r'\n{3,}', '\n\n', new_content)
    
    # 3. Ensure the file ends with exactly one newline and has no leading/trailing blank space
    new_content = new_content.strip() + "\n"
    
    if new_content != content:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        print(f"Successfully stripped whitespace from: {file_path}")
        return True
    else:
        print(f"No formatting changes needed for: {file_path}")
        return False

def main():
    target_files = [
        "gem_trading_rules/rules.md",
        "INSTRUCTIONS.md",
        "antigravity.md",
        "README.md"
    ]
    
    # Include all sub-agent instruction markdown files
    engine_files = glob.glob("engine_instructions/*.md")
    target_files.extend(engine_files)
    
    print("Starting system-wide whitespace stripping on master rules and instructions...")
    updated_count = 0
    for file_path in target_files:
        if strip_whitespace_from_file(file_path):
            updated_count += 1
            
    print(f"Completed whitespace stripping. Modified {updated_count} files.")

if __name__ == "__main__":
    main()
