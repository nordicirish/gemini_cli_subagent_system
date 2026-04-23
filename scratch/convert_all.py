import json
import os
import glob

# Files that should NOT be converted to Markdown
EXCLUDE_FILES = {
    'local_ssot_shadow.json',
    'trade_lessons.json',
    'config.json',
    'user_config.json',
    'output.json'
}

def convert_to_md(json_path, md_path):
    with open(json_path, 'r', encoding='utf-8') as f:
        try:
            data = json.load(f)
        except Exception as e:
            print(f"Skipping {json_path}: {e}")
            return False

    md_lines = []
    # Add a title based on filename
    title = os.path.basename(json_path).replace('.json', '').replace('_', ' ').title()
    md_lines.append(f"# {title}\n")

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

    with open(md_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(md_lines))
    return True

json_files = glob.glob('*.json')
for jf in json_files:
    if jf in EXCLUDE_FILES:
        continue
    
    md_file = jf.replace('.json', '.md')
    print(f"Converting {jf} to {md_file}...")
    if convert_to_md(jf, md_file):
        os.rename(jf, jf + ".bak")

print("All eligible engine files converted successfully.")
