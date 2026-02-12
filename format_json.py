import json

with open('c:/github/gem_python/Instructions.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

with open('c:/github/gem_python/Instructions.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, indent=2)