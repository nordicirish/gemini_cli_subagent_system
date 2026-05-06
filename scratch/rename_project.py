import os
import re

# Replacement map for lower‑casing the rules directory references
replacements = {
    "Gemini_Gem_Rules/": "gemini_gem_rules/",
    "Gemini_Gem_Rules": "gemini_gem_rules",
    # preserve other existing global replacements (already applied earlier)
    "GEM_Rules_Data": "Gemini_Gem_Rules_Data",
    "GEM_Terminal": "Gemini_Gem_Terminal",
    "GEM_Rule_Enforcer_Engine": "Gemini_Gem_Rule_Enforcer_Engine",
    "GEM_Trading_Rules": "Gemini_Gem_Rules",
    "GEM Trading Agent System": "Gemini Gem Stock Market Council",
    "GEM Trading Terminal": "Gemini Gem Stock Market Council Terminal",
    "GEM Council Debate": "Gemini Gem Council Debate",
    "GEM Dashboard": "Gemini Gem Dashboard",
    "GEM Trading Dashboard": "Gemini Gem Stock Market Council Dashboard",
    "# GEM ": "# Gemini Gem ",
    "GEM-": "Gemini-Gem-",
    "GEM_": "Gemini_Gem_"
}

def update_files():
    for root, dirs, files in os.walk('.'):
        # Exclude virtual env, git, __pycache__
        if '.git' in dirs:
            dirs.remove('.git')
        if '.venv' in dirs:
            dirs.remove('.venv')
        if '__pycache__' in dirs:
            dirs.remove('__pycache__')
        for filename in files:
            if filename.endswith(('.md', '.html', '.js', '.py', '.json')):
                path = os.path.join(root, filename)
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    new_content = content
                    for old, new in replacements.items():
                        new_content = new_content.replace(old, new)
                    if new_content != content:
                        with open(path, 'w', encoding='utf-8') as f:
                            f.write(new_content)
                        print(f"Updated {path}")
                except Exception as e:
                    print(f"Error processing {path}: {e}")

if __name__ == "__main__":
    update_files()
