import os
import glob
import re

python_dir = r"c:\github\gemini_cli_subagent_system\python"
py_files = glob.glob(os.path.join(python_dir, "*.py"))

replacements = {
    r'([\"\'])config\.json([\"\'])': r'\g<1>context/config.json\g<2>',
    r'([\"\'])ssot\.json([\"\'])': r'\g<1>context/ssot.json\g<2>',
    r'([\"\'])debug_api\.json([\"\'])': r'\g<1>context/debug_api.json\g<2>',
    r'([\"\'])decision_log\.json([\"\'])': r'\g<1>context/decision_log.json\g<2>',
    r'([\"\'])trade_lessons\.json([\"\'])': r'\g<1>context/trade_lessons.json\g<2>',
    r'([\"\'])user_config\.json([\"\'])': r'\g<1>context/user_config.json\g<2>',
    r'([\"\'])cloud_sync_manifest\.json([\"\'])': r'\g<1>context/cloud_sync_manifest.json\g<2>',
    
    r'([\"\'])trade_lessons\.md([\"\'])': r'\g<1>context/trade_lessons.md\g<2>',
    
    r'([\"\'])bullish_gem\.md([\"\'])': r'\g<1>engine_instructions/bullish_gem.md\g<2>',
    r'([\"\'])context_engine\.md([\"\'])': r'\g<1>engine_instructions/context_engine.md\g<2>',
    r'([\"\'])data_analyst\.md([\"\'])': r'\g<1>engine_instructions/data_analyst.md\g<2>',
    r'([\"\'])execution\.md([\"\'])': r'\g<1>engine_instructions/execution.md\g<2>',
    r'([\"\'])gex_engine\.md([\"\'])': r'\g<1>engine_instructions/gex_engine.md\g<2>',
    r'([\"\'])macro_narrative_engine\.md([\"\'])': r'\g<1>engine_instructions/macro_narrative_engine.md\g<2>',
    r'([\"\'])macro_sentinel\.md([\"\'])': r'\g<1>engine_instructions/macro_sentinel.md\g<2>',
    r'([\"\'])neutral_gem\.md([\"\'])': r'\g<1>engine_instructions/neutral_gem.md\g<2>',
    r'([\"\'])post_trade_review\.md([\"\'])': r'\g<1>engine_instructions/post_trade_review.md\g<2>',
    r'([\"\'])red_team_gem\.md([\"\'])': r'\g<1>engine_instructions/red_team_gem.md\g<2>',
    r'([\"\'])research\.md([\"\'])': r'\g<1>engine_instructions/research.md\g<2>',
    r'([\"\'])rule_enforcer_engine\.md([\"\'])': r'\g<1>engine_instructions/rule_enforcer_engine.md\g<2>',
    r'([\"\'])sentiment_engine\.md([\"\'])': r'\g<1>engine_instructions/sentiment_engine.md\g<2>',
    r'([\"\'])state_validation_router\.md([\"\'])': r'\g<1>engine_instructions/state_validation_router.md\g<2>',
    r'([\"\'])structural_engine\.md([\"\'])': r'\g<1>engine_instructions/structural_engine.md\g<2>',
    r'([\"\'])technical_validator\.md([\"\'])': r'\g<1>engine_instructions/technical_validator.md\g<2>',
    r'([\"\'])terminal\.md([\"\'])': r'\g<1>engine_instructions/terminal.md\g<2>',

    r'GEM_Trading_Rules': r'gem_trading_rules',
    
    r'([\"\'])models_list\.txt([\"\'])': r'\g<1>context/models_list.txt\g<2>',
    
    # Also fix glob paths
    r'glob\.glob\([\"\']\*\.md[\"\']\)': r'glob.glob("engine_instructions/*.md")'
}

for py_file in py_files:
    with open(py_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    original = content
    for pattern, repl in replacements.items():
        content = re.sub(pattern, repl, content)
        
    if content != original:
        with open(py_file, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Updated {os.path.basename(py_file)}")
