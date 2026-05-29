import os

directory = r"c:\github\gemini_cli_subagent_system\engine_instructions"
old_ver = "v10.54-Tactical-Sweep-and-Gamma-Locks"
new_ver = "v10.55-Overnight-Exhaustion-Trims"

files = [
    "bullish_gem.md",
    "context_engine.md",
    "data_analyst.md",
    "gex_engine.md",
    "macro_narrative_engine.md",
    "macro_sentinel.md",
    "post_trade_review.md",
    "red_team_gem.md",
    "research.md",
    "sentiment_engine.md",
    "state_validation_router.md",
    "structural_engine.md"
]

for filename in files:
    filepath = os.path.join(directory, filename)
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        
        if old_ver in content:
            new_content = content.replace(old_ver, new_ver)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(new_content)
            print(f"Successfully bumped {filename}")
        else:
            print(f"Could not find old version in {filename}")
    else:
        print(f"File not found: {filename}")
