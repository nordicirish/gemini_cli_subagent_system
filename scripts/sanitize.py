import os
import glob
import re

replacements = {
    r"Sniper Protocol": "Precision Protocol",
    r"Sniper-Bid": "Precision-Bid",
    r"sniper bid": "precision bid",
    r"Combat Override": "Field-Validation Override",
    r"kinetic conflict": "geopolitical escalation",
    r"kinetic prove-outs": "field prove-outs",
    r"Kinetic Overlap": "Geopolitical Overlap",
    r"War-Bid": "Defense-Bid",
    r"IRGC activity": "regional military activity",
    r"Truth Social": "Executive Social Media"
}

files_to_check = [
    "context/trade_lessons.json",
    "context/trade_lessons.md",
    "Gemini_Gem_Rules/rules.md",
    "README.md"
]

for filename in files_to_check:
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            content = f.read()
        
        original_content = content
        for pattern, replacement in replacements.items():
            content = re.sub(pattern, replacement, content, flags=re.IGNORECASE)
            
        if content != original_content:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"Updated {filename}")
        else:
            print(f"No changes needed for {filename}")
    else:
        print(f"File not found: {filename}")
