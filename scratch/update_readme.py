import re

readme_path = "README.md"
with open(readme_path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Programmatically extract the changelog
changelog_header = "## 📋 Changelog"
quick_start_header = "## 🚀 Quick Start"

idx_changelog = content.find(changelog_header)
idx_quick_start = content.find(quick_start_header)

if idx_changelog == -1 or idx_quick_start == -1:
    print("Error: Could not locate Changelog or Quick Start boundaries.")
    exit(1)

# The divider before ## 📋 Changelog
idx_divider_before = content.rfind("---\n", 0, idx_changelog)
if idx_divider_before == -1:
    print("Error: Could not locate divider before Changelog.")
    exit(1)

# Extract changelog block and strip trailing rule
changelog_content = content[idx_changelog:idx_quick_start].strip()
if changelog_content.endswith("---"):
    changelog_content = changelog_content[:-3].strip()

# Construct README without the changelog
top_part = content[:idx_divider_before].strip() + "\n\n---\n\n"
rest_part = content[idx_quick_start:]
no_changelog_readme = top_part + rest_part

# 2. Perform references replacement (pro models -> flash thinking)
# Replace Line 7 routing description
target_routing = "Gemini 2.5 Pro (PRO), Gemini 2.0 Flash Thinking (THINKING)"
replacement_routing = "Gemini 2.0 Flash Thinking (THINKING), Gemini 2.5 Pro (PRO)"
if target_routing in no_changelog_readme:
    no_changelog_readme = no_changelog_readme.replace(target_routing, replacement_routing)
    print("Updated hybrid model architecture routing tier order.")
else:
    print("Warning: Target routing description not found.")

# Replace Terminal Orchestrator description
target_orchestrator = "connects directly to a **Gemini 2.5 Pro** (or best available) model configured as the Terminal Orchestrator."
replacement_orchestrator = "connects directly to a **Gemini 2.0 Flash Thinking** (or best available) model configured as the Terminal Orchestrator."
if target_orchestrator in no_changelog_readme:
    no_changelog_readme = no_changelog_readme.replace(target_orchestrator, replacement_orchestrator)
    print("Updated Terminal Orchestrator model description.")
else:
    print("Warning: Terminal Orchestrator model description not found.")

# Replace Hybrid Model Routing tiers bullet points
target_routing_tiers = """- **GEMMA Tier (Gemma 4 31B)**: Handles deterministic, JSON-heavy, and structural analysis (Context, Structural, Technical Validator).
- **FLASH Tier (Gemini 2.5 Flash)**: Handles high-speed research, social sentiment, and news velocity monitoring.
- **PRO Tier (Gemini 2.5 Pro)**: Reserved for the Terminal Orchestrator and complex Macro Arbitration."""

replacement_routing_tiers = """- **GEMMA Tier (Gemma 4 31B)**: Handles deterministic, JSON-heavy, and structural analysis (Context, Structural, Technical Validator).
- **FLASH Tier (Gemini 2.5 Flash)**: Handles high-speed research, social sentiment, and news velocity monitoring.
- **THINKING Tier (Gemini 2.0 Flash Thinking)**: Reserved for the Terminal Orchestrator and deep-reasoning sub-agents (advocates, Research).
- **PRO Tier (Gemini 2.5 Pro)**: Reserved for complex Macro Arbitration (e.g. Macro Sentinel)."""

if target_routing_tiers in no_changelog_readme:
    no_changelog_readme = no_changelog_readme.replace(target_routing_tiers, replacement_routing_tiers)
    print("Updated hybrid model routing tiers.")
else:
    # Try normalized replacement in case of spacing/whitespace mismatches
    no_changelog_readme = no_changelog_readme.replace(
        "- **PRO Tier (Gemini 2.5 Pro)**: Reserved for the Terminal Orchestrator and complex Macro Arbitration.",
        "- **THINKING Tier (Gemini 2.0 Flash Thinking)**: Reserved for the Terminal Orchestrator and deep-reasoning sub-agents (advocates, Research).\n- **PRO Tier (Gemini 2.5 Pro)**: Reserved for complex Macro Arbitration (e.g. Macro Sentinel)."
    )
    print("Updated hybrid model routing tiers (fallback pattern).")

# Replace Sub-Agent Markdown Instructions table entry
target_table_entry = "| `terminal.md` | **Terminal Orchestrator** | PRO |"
replacement_table_entry = "| `terminal.md` | **Terminal Orchestrator** | THINKING |"
if target_table_entry in no_changelog_readme:
    no_changelog_readme = no_changelog_readme.replace(target_table_entry, replacement_table_entry)
    print("Updated sub-agents table terminal.md mode.")
else:
    print("Warning: terminal.md table entry not found.")

# Replace Model Hierarchy table entries
target_model_hierarchy = """| Mode | Model Priority | Used By |
|------|---------------|---------|
| `PRO` | `gemini-2.5-pro` | Orchestrator, complex engines |
| `THINKING` | `gemini-2.0-flash-thinking-exp-01-21` → `gemini-2.5-pro` | Council advocates, Research |"""

replacement_model_hierarchy = """| Mode | Model Priority | Used By |
|------|---------------|---------|
| `THINKING` | `gemini-2.0-flash-thinking-exp` → `gemini-2.5-pro` | Orchestrator, Council advocates, Research |
| `PRO` | `gemini-2.5-pro` | Macro Sentinel, complex fallbacks |"""

if target_model_hierarchy in no_changelog_readme:
    no_changelog_readme = no_changelog_readme.replace(target_model_hierarchy, replacement_model_hierarchy)
    print("Updated model hierarchy table.")
else:
    print("Warning: Model hierarchy table not found.")

# 3. Replace the architecture diagram with our new perfectly aligned one
idx_arch = no_changelog_readme.find("## 🏗️ Architecture")
if idx_arch == -1:
    print("Error: Could not locate ## 🏗️ Architecture header.")
    exit(1)

idx_fence_start = no_changelog_readme.find("```", idx_arch)
idx_fence_end = no_changelog_readme.find("```", idx_fence_start + 3)

if idx_fence_start == -1 or idx_fence_end == -1:
    print("Error: Could not locate architecture diagram code fences.")
    exit(1)

# Load the perfectly formatted diagram
with open("scratch/diagram.txt", "r", encoding="utf-8") as df:
    new_diagram = df.read().strip()

no_changelog_readme = (
    no_changelog_readme[:idx_fence_start] +
    "```\n" + new_diagram + "\n```" +
    no_changelog_readme[idx_fence_end + 3:]
)
print("Replaced architecture diagram with mathematically aligned UTF-8 version.")

# 4. Insert the changelog back near the bottom (before ## 📄 Licence)
idx_licence = no_changelog_readme.find("## 📄 Licence")
if idx_licence == -1:
    print("Error: Could not locate ## 📄 Licence header.")
    exit(1)

idx_divider_bottom = no_changelog_readme.rfind("---\n", 0, idx_licence)
if idx_divider_bottom == -1:
    print("Error: Could not locate divider before Licence.")
    exit(1)

final_readme = (
    no_changelog_readme[:idx_divider_bottom].strip() +
    "\n\n---\n\n" +
    changelog_content +
    "\n\n---\n\n" +
    no_changelog_readme[idx_licence:].strip() +
    "\n"
)
print("Moved changelog to near the bottom of the README (above Licence).")

# Save final content
with open(readme_path, "w", encoding="utf-8") as f:
    f.write(final_readme)

print("README.md successfully updated and synchronized!")
