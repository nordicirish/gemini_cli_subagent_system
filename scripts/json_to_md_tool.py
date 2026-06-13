import json
import os
import sys

def format_value(v, level=0, parent_key=""):
    """Recursively formats JSON values into Markdown lists."""
    indent = "  " * level
    if isinstance(v, dict):
        lines = []
        # Special handling for objects with an ID - use as subheader if in a list
        if level > 0 and 'id' in v:
            item_id = v['id']
            item_name = v.get('name', v.get('title', ''))
            header_prefix = "###" if level == 1 else "####"
            title_str = f"{item_id}"
            if item_name:
                title_str += f" - {item_name}"
            # This is tricky because we're inside a list. 
            # We'll just format the keys instead of making it a real header to keep it valid MD.
            # Actually, the user's manual version used real headers.
        
        for k, val in v.items():
            key_fmt = k.replace('_', ' ').title()
            if isinstance(val, (dict, list)) and len(val) > 0:
                lines.append(f"{indent}- **{key_fmt}:**")
                lines.append(format_value(val, level + 1, k))
            else:
                lines.append(f"{indent}- **{key_fmt}:** {val}")
        return "\n".join(lines)
    elif isinstance(v, list):
        if not v:
            return f"{indent}- (Empty)"
        lines = []
        for item in v:
            if isinstance(item, dict) and 'id' in item:
                # If it's a list of objects with IDs, we treat them as sections
                item_id = item['id']
                item_name = item.get('name', item.get('title', ''))
                title_str = f"{item_id}"
                if item_name:
                    title_str += f" - {item_name}"
                
                lines.append(f"{indent}- **[{title_str}]**")
                # Format the rest of the object without the ID
                sub_item = {ik: iv for ik, iv in item.items() if ik not in ['id', 'name', 'title']}
                lines.append(format_value(sub_item, level + 1))
            elif isinstance(item, (dict, list)):
                lines.append(f"{indent}- ")
                lines.append(format_value(item, level + 1))
            else:
                lines.append(f"{indent}- {item}")
        return "\n".join(lines)
    else:
        return str(v)

def convert_file(json_path):
    """Converts a single JSON file to Markdown."""
    if not json_path.endswith('.json'):
        return
    
    # Exclude specific state files that should remain JSON only
    basename = os.path.basename(json_path)
    if basename in ['context/trade_lessons.json', 'local_ssot_shadow.json']:
        print(f"Skipping state file: {json_path}")
        return
    
    md_path = json_path.replace('.json', '.md')
    print(f"Processing: {json_path}...")
    
    with open(json_path, 'r', encoding='utf-8') as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"  [ERROR] Could not decode {json_path}: {e}")
            return

    md = []
    
    # 1. Title/Header Section
    title = data.get('id', data.get('role', os.path.basename(json_path).replace('.json', '')))
    md.append(f"# {title}")
    
    # Primary metadata (Version, Role, etc.)
    metadata_keys = ['role', 'version', 'description', 'tone', 'status']
    for k in metadata_keys:
        if k in data:
            md.append(f"**{k.title()}:** {data[k]}")
    
    md.append("")
    md.append("---")
    md.append("")

    # 2. Main Content
    # Skip keys already used in header
    skip_keys = set(['id'] + metadata_keys)
    
    for k, v in data.items():
        if k in skip_keys:
            continue
        
        section_title = k.replace('_', ' ').title()
        md.append(f"## {section_title}")
        
        if isinstance(v, (dict, list)):
            md.append(format_value(v))
        else:
            md.append(str(v))
        md.append("")

    # 3. Footer
    md.append("---")
    md.append(f"*Generated from {os.path.basename(json_path)}*")

    with open(md_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(md))
    print(f"  [SUCCESS] Created {md_path}")

def main():
    if len(sys.argv) < 2:
        print("Usage: python json_to_md_tool.py <path_to_json_file_or_directory>")
        sys.exit(1)
        
    target = sys.argv[1]
    
    if os.path.isfile(target):
        convert_file(target)
    elif os.path.isdir(target):
        print(f"Scanning directory: {target}")
        for root, dirs, files in os.walk(target):
            for file in files:
                if file.endswith('.json'):
                    convert_file(os.path.join(root, file))
    else:
        print(f"Error: Path '{target}' not found.")

if __name__ == "__main__":
    main()
