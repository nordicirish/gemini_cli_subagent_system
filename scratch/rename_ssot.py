import os
import glob

def rename_ssot_references():
    md_files = glob.glob('**/*.md', recursive=True)
    
    for md_file in md_files:
        with open(md_file, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Replace the filename and the term
        new_content = content.replace('local_ssot_shadow.json', 'ssot.json')
        new_content = new_content.replace('local_ssot_shadow', 'ssot')
        
        if new_content != content:
            with open(md_file, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print(f"Updated references in {md_file}")

if __name__ == "__main__":
    rename_ssot_references()
