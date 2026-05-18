import os, glob, re
files = glob.glob('*.md') + glob.glob('gemini_gem_rules/*.md')
for f in files:
    with open(f, 'r', encoding='utf-8') as file:
        content = file.read()
    content = re.sub(r'\*\*Version:\*\* v9\.96[^\n]+', '**Version:** v9.97-Routine-Turn-Directive-Sync', content)
    content = re.sub(r'\*\*Sync_ID:\*\* ANTIGRAVITY-GLOBAL-SYNC-v9\.96[^\n]+', '**Sync_ID:** ANTIGRAVITY-GLOBAL-SYNC-v9.97-Routine-Turn-Directive-Sync', content)
    with open(f, 'w', encoding='utf-8') as file:
        file.write(content)
print('Done replacing versions.')
