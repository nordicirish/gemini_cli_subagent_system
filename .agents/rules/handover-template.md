---
trigger: always_on
description: Standardized GEM Implementation Handover Template
---

# Mandatory GEM Implementation Handover Template

All engineering and remediation turns must conclude using the following standardized handover template:

```markdown
GEM — Implementation Handover

Repository: nordicirish/gemini_cli_subagent_system
Branch: main
Baseline HEAD: [commit]
Ending HEAD: [commit]
Working tree: Clean / Modified (list files)

1. Implementation

Objective: [One sentence]

Changes: [Brief summary of actual changes]

Files changed: [List paths]

Deviations/blockers: None / [Details]

2. Tests

Test suite | Result
---|---
[Suite] | [Passed / Failed / Skipped]
[Suite] | [Passed / Failed / Skipped]

Compilation: Passed / Failed / Not run
Dependency check: Passed / Failed / Not run

3. Integrity and restrictions

Protected assets: Unchanged / Not verified

Production database operations: None / [Details]

Git write operations: None

Other authorised restrictions: Complied / [Details]

4. Outstanding issues

[None, or concise list]

Implementation complete. Awaiting independent ChatGPT review.

5. Comprehensive commit message to user
```
