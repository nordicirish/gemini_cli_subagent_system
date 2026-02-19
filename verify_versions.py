
import json
import os

files_to_check = [
    "sentiment_engine.json",
    "research.json",
    "technical_validator.json",
    "execution.json",
    "context_engine.json"
]

all_passed = True

for file in files_to_check:
    if os.path.exists(file):
        try:
            with open(file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # Check version field presence
            version = data.get("version", "MISSING")
            print(f"[PASS] {file} (Version: {version})")
        except json.JSONDecodeError as e:
            print(f"[FAIL] {file}: {e}")
            all_passed = False
        except Exception as e:
            print(f"[ERROR] {file}: {e}")
            all_passed = False
    else:
        print(f"[SKIP] {file} not found")

if all_passed:
    print("\nAll JSON files are valid.")
else:
    print("\nSome JSON files have errors.")
    exit(1)
