import json
import os

with open('GEM_Trading_Rules/rules.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

md_lines = ["# GEM Trading Rules & System Architecture\n"]

for key, value in data.items():
    if isinstance(value, dict):
        md_lines.append(f"## {key.replace('_', ' ').title()}")
        for k, v in value.items():
            if isinstance(v, dict):
                md_lines.append(f"### {k.replace('_', ' ').title()}")
                for sub_k, sub_v in v.items():
                    if isinstance(sub_v, list):
                        md_lines.append(f"- **{sub_k}**:")
                        for item in sub_v:
                            md_lines.append(f"  - {item}")
                    else:
                        md_lines.append(f"- **{sub_k}**: {sub_v}")
            elif isinstance(v, list):
                md_lines.append(f"### {k.replace('_', ' ').title()}")
                for item in v:
                    if isinstance(item, dict):
                        for mk, mv in item.items():
                            md_lines.append(f"- **{mk}**: {mv}")
                        md_lines.append("")
                    else:
                        md_lines.append(f"- {item}")
            else:
                md_lines.append(f"- **{k}**: {v}")
        md_lines.append("\n")
    elif isinstance(value, list):
        md_lines.append(f"## {key.replace('_', ' ').title()}")
        for item in value:
            md_lines.append(f"- {item}")
        md_lines.append("\n")
    else:
        md_lines.append(f"- **{key}**: {value}\n")

with open('GEM_Trading_Rules/rules.md', 'w', encoding='utf-8') as f:
    f.write('\n'.join(md_lines))

print("Converted rules.json to rules.md successfully.")
