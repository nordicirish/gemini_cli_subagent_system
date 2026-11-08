# Protocol 01: Agent Orchestration and Delegation

**Status:** Enforced  
**Standard:** Adapted from `tk-site-new` Protocol 01  

---

## 1. Scope & Purpose

This protocol governs how tasks are initiated, scoped, delegated, and coordinated among agents in the `gemini_cli_subagent_system` repository.

## 2. Core Directives

1. **Explicit Scope Definition:** Every task must declare an explicit scope before any file is created or modified. Changes outside the approved scope are rejected automatically.
2. **Single Primary Role per Agent:** An agent executing a task must act within a single specialist role (e.g., Python Engineer or Security Agent). An agent cannot simultaneously act as Implementer and Independent Reviewer.
3. **No Autonomous Delegation Chains:** Subagents must not autonomously launch secondary subagent swarms without explicit task boundaries.
4. **Context Budgeting & Minimization:** Agents must pass only necessary, minimal context to subagents. Excessive context bloat and recursive instruction loading are forbidden.
5. **Deterministic Termination:** Every subagent task must define explicit termination criteria. Infinite wait loops and unbounded turn cycles are forbidden. Maximum turns for tool dispatching is capped at 5 (`MAX_TOOL_TURNS = 5`).
