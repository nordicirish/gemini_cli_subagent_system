import os
import re
import json
import glob
import subprocess
from google import genai

def read_file_from_git_head(file_path):
    try:
        # Run git show HEAD:file_path
        # Normalize path for git (use forward slashes)
        git_path = file_path.replace("\\", "/")
        result = subprocess.run(
            ["git", "show", f"HEAD:{git_path}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            check=True
        )
        return result.stdout
    except Exception as e:
        print(f"Failed to read {file_path} from git HEAD: {e}")
        return None

def compile_master_doc_content(files_dict):
    """Compiles files from a dict mapping filepath to content into a master document string."""
    lines = ["# Master Trading Knowledge Document\n\n", "This document contains the Single Source of Truth (SSoT) rules and all engine instructions.\n\n"]
    
    # Append rules.md
    rules_path = "GEM_Trading_Rules/rules.md"
    if rules_path in files_dict and files_dict[rules_path]:
        lines.append("## 1. TRADING RULES (SSoT)\n\n")
        content = files_dict[rules_path]
        demoted = [('##' + line) if line.startswith('#') else line for line in content.splitlines()]
        lines.append('\n'.join(demoted))
        lines.append("\n\n---\n\n")
        
    # Append engine instructions
    lines.append("## 2. ENGINE INSTRUCTIONS\n\n")
    engine_files = sorted([k for k in files_dict.keys() if k.startswith("engine_instructions/") and k.endswith(".md")])
    for filepath in engine_files:
        filename = os.path.basename(filepath)
        lines.append(f"### Component: {filename}\n\n")
        content = files_dict[filepath]
        demoted = [('###' + line) if line.startswith('#') else line for line in content.splitlines()]
        lines.append('\n'.join(demoted))
        lines.append("\n\n---\n\n")
        
    return "".join(lines)

def main():
    # Load Gemini API Key
    config_data = {}
    if os.path.exists("context/config.json"):
        with open("context/config.json", "r", encoding="utf-8") as f:
            config_data = json.load(f)
            
    api_key = config_data.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY is not configured.")
        return
        
    client = genai.Client(api_key=api_key)
    model = "gemini-3.5-flash"
    
    # 1. Identify target files
    target_files = ["GEM_Trading_Rules/rules.md"]
    engine_files = sorted(glob.glob("engine_instructions/*.md"))
    # Normalize paths to use forward slashes for git matching
    target_files.extend([f.replace("\\", "/") for f in engine_files])
    
    # 2. Get HEAD versions and compile original doc
    original_files = {}
    for f in target_files:
        original_files[f] = read_file_from_git_head(f)
        
    original_master_doc = compile_master_doc_content(original_files)
    
    # 3. Get current working copy versions and compile current doc
    current_files = {}
    for f in target_files:
        if os.path.exists(f):
            with open(f, "r", encoding="utf-8") as file:
                current_files[f] = file.read()
                
    current_master_doc = compile_master_doc_content(current_files)
    
    print("\nCounting tokens via Gemini API...")
    try:
        orig_tokens = client.models.count_tokens(model=model, contents=original_master_doc).total_tokens
        curr_tokens = client.models.count_tokens(model=model, contents=current_master_doc).total_tokens
        
        saved_tokens = orig_tokens - curr_tokens
        saved_pct = (saved_tokens / orig_tokens) * 100 if orig_tokens > 0 else 0.0
        
        orig_chars = len(original_master_doc)
        curr_chars = len(current_master_doc)
        saved_chars = orig_chars - curr_chars
        saved_char_pct = (saved_chars / orig_chars) * 100 if orig_chars > 0 else 0.0
        
        print("\n================== TOKEN SAVINGS AUDIT ==================")
        print(f"Original Master Doc: {orig_chars:,} characters | {orig_tokens:,} tokens")
        print(f"Cleaned Master Doc:  {curr_chars:,} characters | {curr_tokens:,} tokens")
        print("---------------------------------------------------------")
        print(f"Character Savings:   {saved_chars:,} chars ({saved_char_pct:.2f}%)")
        print(f"Token Savings:       {saved_tokens:,} tokens ({saved_pct:.2f}%)")
        print("=========================================================\n")
        
    except Exception as e:
        print(f"API token count failed: {e}")

if __name__ == "__main__":
    main()
