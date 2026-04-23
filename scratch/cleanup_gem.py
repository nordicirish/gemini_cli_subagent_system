import os
import glob

def cleanup_gem_references():
    md_files = glob.glob('*.md')
    
    for md_file in md_files:
        with open(md_file, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Replace role labels
        new_content = content.replace('**role**: GEM Bullish Advocate', '**role**: Bullish Advocate')
        new_content = new_content.replace('**role**: GEM Red Team Pessimist', '**role**: Red Team Pessimist')
        new_content = new_content.replace('**role**: GEM Neutral Structuralist', '**role**: Neutral Structuralist')
        new_content = new_content.replace('**role**: GEM Sentiment Engine', '**role**: Sentiment Engine')
        new_content = new_content.replace('**role**: GEM Structural Engine', '**role**: Structural Engine')
        new_content = new_content.replace('**role**: GEM Research Engine', '**role**: Research Engine')
        new_content = new_content.replace('**role**: GEM Review Engine', '**role**: Review Engine')
        new_content = new_content.replace('**role**: GEM Trading Terminal Orchestrator', '**role**: Terminal Orchestrator')
        new_content = new_content.replace('**role**: GEM Context Engine', '**role**: Context Engine')
        
        # Replace other common redundant "GEM" prefixes in roles
        new_content = new_content.replace('**role**: GEM ', '**role**: ')
        
        # Replace "SSoT GEM" if it still exists anywhere
        new_content = new_content.replace('SSoT GEM', 'CONTEXT_ENGINE')
        new_content = new_content.replace('Rules GEM', 'Rules Engine')
        
        if new_content != content:
            with open(md_file, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print(f"Cleaned up GEM references in {md_file}")

if __name__ == "__main__":
    cleanup_gem_references()
