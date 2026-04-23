import os
import re

version_pattern = re.compile(r'- \*\*version\*\*: v[\d\.]+[^\s\n]*')
new_version = '- **version**: v5.2-FIFO-WAC-Aware'

for filename in os.listdir('.'):
    if filename.endswith('.md'):
        with open(filename, 'r', encoding='utf-8') as f:
            content = f.read()
        
        new_content = version_pattern.sub(new_version, content)
        
        if new_content != content:
            print(f"Updating {filename}")
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(new_content)

print("Synchronization complete.")
