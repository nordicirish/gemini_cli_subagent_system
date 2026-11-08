# Protocol 03: Security & Runtime Execution Policy

**Status:** Enforced  
**Standard:** Adapted from `tk-site-new` Protocol 03  

---

## 1. Scope & Purpose

This protocol establishes runtime security controls, execution sandboxing, tool permission validation, and secrets protection across all execution environments.

## 2. Subprocess Execution & Sandbox Controls

1. **Elimination of Unrestricted Permission Bypasses:**
   - The `--dangerously-skip-permissions` CLI flag must not be used without application-level tool gating.
   - Where the Antigravity CLI is executed in headless mode, `--sandbox` must be enabled to restrict shell execution capabilities.
2. **Path Confinement:**
   - Subprocesses and file tools must confine operations strictly within the repository workspace.
   - Traversals outside the repository root (`..` attacks) or attempts to access system directories (`C:\Windows`, `C:\Users\...\AppData`) must be blocked fail-closed.
3. **Execution Timeouts:**
   - All subprocess calls must enforce hard timeouts (default: 120 seconds). Any hung process must be terminated via `proc.kill()`.

## 3. Tool Permission & Dispatch Registry

1. **Centralized Tool Registry:**
   - All tools must be registered in `python/tool_registry.py` with declared schemas, read/write classification, role permissions, and sensitive operation flags.
2. **Runtime Boundary Validation:**
   - Tools must be validated at the execution boundary before dispatching. Unregistered tools, unauthorized agent roles, and malformed arguments must be rejected fail-closed.
3. **Sensitive Operations & Human Approval:**
   - Operations that modify canonical investment rules (`gem_trading_rules/rules.md`) or trigger capital rebalancing require explicit human authorization tokens.

## 4. Secrets & Credentials Protection

1. **Zero Secret Exposure:**
   - API keys, credentials, session tokens, and passwords must never be logged, echoed to stdout, written to audit logs, or passed in prompt templates.
   - Any secret string detected in log payloads must be automatically redacted (`[REDACTED_SECRET]`).
