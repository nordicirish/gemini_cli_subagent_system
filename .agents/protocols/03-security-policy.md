# Protocol 03: Security & Runtime Execution Policy

**Status:** Enforced  
**Standard:** Adapted from `tk-site-new` Protocol 03  

---

## 1. Scope & Purpose

This protocol establishes runtime security controls, execution sandboxing, tool permission validation, and secrets protection across all execution environments.

## 2. Subprocess Execution & Sandbox Controls

1. **Elimination of Unrestricted Permission Bypasses:**
   - The `--dangerously-skip-permissions` CLI flag is strictly forbidden and removed from all runtime paths.
   - Antigravity CLI subprocess invocations in `python/agent_framework.py` explicitly pass `--sandbox` (enabling terminal execution restrictions) and `--disable-slash-commands` (disabling slash-command/skill expansion in print mode).
   - In non-interactive headless subprocess calls, `agy.exe` automatically denies host tool execution permissions (`command`, file writes) that require interactive user confirmation.
   - **Explicit Distinction & Residual Local Risk:** Prompt-level instructions (`analytical_output_instruction`) guide model output formatting only and are **not** security controls. Because this is a local single-user application running under the owner's OS user account, subprocess isolation relies on the `agy.exe --sandbox` flag, headless permission auto-denial, and Python-layer `ToolRegistry` / `authorize_financial_mutation` checks rather than container/VM virtualization.
2. **Path Confinement:**
   - Subprocesses and file tools must confine operations strictly within the repository workspace.
   - Traversals outside the repository root (`..` attacks) or attempts to access system directories (`C:\Windows`, `C:\Users\...\AppData`) must be blocked fail-closed.
3. **Execution Timeouts:**
   - All subprocess calls must enforce hard timeouts (default: 120 seconds). Any hung process must be terminated via `proc.kill()`.

## 3. Tool Permission & Dispatch Registry

1. **Centralized Tool Registry:**
   - All tools must be registered in `python/tool_registry.py` with declared schemas (`parameters`), read/write `classification`, `allowed_roles`, `required_permission`, and sensitive operation flags.
2. **Runtime Boundary Validation:**
   - `ToolRegistry.validate_call()` enforces role recognition, `ROLE_CLASSIFICATIONS` (`READ` vs `WRITE`), `ROLE_PERMISSIONS` (`required_permission`), required arguments, unknown argument rejection, and runtime argument type validation before dispatching.
3. **Sensitive Operations & Local Owner Confirmation:**
   - Operations that modify canonical investment rules (`gem_trading_rules/rules.md`) require explicit local owner confirmation via `GLOBAL_OWNER_APPROVAL` (`OwnerApprovalManager`) bound to `(operation_name, target_resource, sha256_payload_digest)`.
   - Approvals are one-time use (consumed immediately upon execution) and cannot be self-granted or passed as tool arguments by an agent.

## 4. Secrets & Credentials Protection

1. **Zero Secret Exposure:**
   - API keys, credentials, session tokens, and passwords must never be logged, echoed to stdout, written to audit logs, or passed in prompt templates.
   - Any secret string detected in log payloads must be automatically redacted (`[REDACTED_SECRET]`).
