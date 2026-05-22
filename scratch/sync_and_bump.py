import os
import re

# Files list
main_files = [
    "antigravity.md",
    "README.md",
    "terminal.md",
    "bullish_gem.md",
    "data_analyst.md",
    "execution.md",
    "gex_engine.md",
    "macro_narrative_engine.md",
    "macro_sentinel.md",
    "neutral_gem.md",
    "post_trade_review.md",
    "red_team_gem.md",
    "rule_enforcer_engine.md",
    "state_validation_router.md",
    "structural_engine.md"
]
rules_file = "gemini_gem_rules/rules.md"
all_files = main_files + [rules_file]

new_version = "v10.12-Regime-Guardrail-Telemetry-Sync"
new_sync_id = "ANTIGRAVITY-GLOBAL-SYNC-v10.12-Regime-Guardrail-Telemetry-Sync"

print("Starting global synchronization and version bump to:", new_version)

# 1. Update Version strings across all 16 files
version_re = re.compile(r'\*\*Version:\*\*\s+v10\.[0-9]+(-[A-Za-z0-9-]+)?')
sync_id_re = re.compile(r'\*\*Sync_ID:\*\*\s+ANTIGRAVITY-GLOBAL-SYNC-v10\.[0-9]+(-[A-Za-z0-9-]+)?')

for filepath in all_files:
    if not os.path.exists(filepath):
        print(f"Error: File {filepath} does not exist.")
        continue
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Replace Version
    new_content, count = version_re.subn(f"**Version:** {new_version}", content)
    if count > 0:
        print(f"Bumped version in {filepath} ({count} replacement(s))")
    else:
        # Try a simpler match if double asterisks are missing
        alt_version_re = re.compile(r'Version:\s+v10\.[0-9]+(-[A-Za-z0-9-]+)?')
        new_content, count = alt_version_re.subn(f"Version: {new_version}", content)
        if count > 0:
            print(f"Bumped version in {filepath} (alternative regex match)")
        else:
            print(f"Warning: Could not find version string in {filepath}")
    
    # Replace Sync_ID in antigravity.md or other files
    new_content, count = sync_id_re.subn(f"**Sync_ID:** {new_sync_id}", new_content)
    if count > 0:
        print(f"Bumped Sync_ID in {filepath}")
        
    with open(filepath, 'w', encoding='utf-8', newline='\n') as f:
        f.write(new_content)

# 2. Append new rules registry to gemini_gem_rules/rules.md
print("\nUpdating rules.md...")
with open(rules_file, 'r', encoding='utf-8') as f:
    rules_content = f.read()

# Register the 4 new rules in the registry
target_registry_line = "- ENH_104: PERSISTENT STOP-LOSS TELEMETRY (trailing_stop_audit Emission Protocol)"
new_registry_entries = """- ENH_104: PERSISTENT STOP-LOSS TELEMETRY (trailing_stop_audit Emission Protocol)
- ENH_105: Melt-Up Regime & RSI Decoupling
- ENH_106: Long Gamma Shield Override
- ENH_107: GEX-SSR Conflict Protocol
- ENH_108: Persistent Stop-Loss Telemetry"""

if target_registry_line in rules_content:
    rules_content = rules_content.replace(target_registry_line, new_registry_entries, 1)
    print("Registered ENH_105, ENH_106, ENH_107, and ENH_108 in ## Enh Xx Registry.")
else:
    print("Error: Could not find ENH_104 in rules.md registry to replace.")

# Insert detailed rules before ## Infrastructure
target_infrastructure_header = "## Infrastructure"
new_rule_sections = """## Fundamental Guardrails

### [ENH_105] MELT-UP REGIME & RSI DECOUPLING
- **Status:** ACTIVE
- **Content:** SPY RSI > 75 does not trigger automatic liquidation if VIX < 20.
- **Justification:** Validated by sustained performance in recent high-beta breakouts.

## Risk Management

### [ENH_106] LONG GAMMA SHIELD OVERRIDE
- **Status:** ACTIVE
- **Content:** If SSR is triggered (>10% drop), Long Gamma protection is mathematically invalidated.
- **Justification:** Necessary to prevent passive holding during active distribution.

### [ENH_107] GEX-SSR CONFLICT PROTOCOL
- **Status:** ACTIVE
- **Content:** Prioritize SSR circuit breakers over GEX stabilization.
- **Justification:** Prevents pipeline deadlock in conflict scenarios.

## Telemetry

### [ENH_108] PERSISTENT STOP-LOSS TELEMETRY
- **Status:** ACTIVE
- **Content:** Emission of stop-loss audit blocks is mandatory for all holdings with RSI > 65 or VWAP extension > 2%.
- **Justification:** Prevents passive holding of overextended assets by ensuring telemetry trails.

"""

if target_infrastructure_header in rules_content:
    rules_content = rules_content.replace(target_infrastructure_header, new_rule_sections + target_infrastructure_header, 1)
    print("Inserted new rule detail sections before ## Infrastructure.")
else:
    print("Error: Could not find ## Infrastructure header in rules.md to append sections.")

with open(rules_file, 'w', encoding='utf-8', newline='\n') as f:
    f.write(rules_content)

# 3. Add changelog entry in README.md
print("\nUpdating README.md changelog...")
with open("README.md", 'r', encoding='utf-8') as f:
    readme_content = f.read()

target_changelog_header = "### 🔒 v10.11 Review Engine & Audit Sync (2026-05-21)"
new_changelog_block = """### 🔒 v10.12 Regime Guardrail & Telemetry Sync (2026-05-22)

| Enhancement | ID | Summary |
|---|---|---|
| **Melt-Up Decoupling** | `ENH_105` | Codifies SPY RSI > 75 decoupling from automatic liquidations when VIX < 20. |
| **Long Gamma Override** | `ENH_106` | Mathematically invalidates Long Gamma shield protection when an asset triggers SSR (>10% drop). |
| **GEX-SSR Conflict** | `ENH_107` | Establishes a conflict protocol prioritizing SSR circuit breakers over GEX stabilization. |
| **Stop-Loss Telemetry** | `ENH_108` | Mandates trailing stop-loss audit emissions for active holdings with RSI > 65 or VWAP extension > 2%. |

"""

if target_changelog_header in readme_content:
    readme_content = readme_content.replace(target_changelog_header, new_changelog_block + target_changelog_header, 1)
    print("Added v10.12 changelog block to README.md.")
else:
    print("Error: Could not find v10.11 changelog header in README.md.")

with open("README.md", 'w', encoding='utf-8', newline='\n') as f:
    f.write(readme_content)

print("\nPrimary sync complete!")
