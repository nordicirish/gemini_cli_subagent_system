# Protocol 02: Development & Engineering Standards

**Status:** Enforced  
**Standard:** Adapted from `tk-site-new` Protocol 02  

---

## 1. Scope & Purpose

This protocol defines coding standards, error handling requirements, dependency management, and quality controls for all Python code in this repository.

## 2. Python Code Standards

1. **Python Version Compatibility:** All code must run cleanly under Python 3.12+ (local runtime: Python 3.13) without deprecated syntax.
2. **Type Annotations:** New modules and refactored functions must include type annotations (`typing` / `types`).
3. **Clean Error Handling:**
   - Raw, bare `except:` clauses are prohibited. Catch specific exceptions (`FileNotFoundError`, `ValueError`, `json.JSONDecodeError`, `TimeoutError`).
   - Every exception block must log or handle the error deterministically; silent pass on unexpected exceptions is prohibited.
4. **Zero Unapproved Dependencies:**
   - No new external packages may be added to `requirements.txt` without explicit human authorization.
   - Standard library modules (`unittest`, `threading`, `json`, `os`, `sys`, `subprocess`, `hashlib`, `concurrent.futures`) must be preferred over external libraries.
5. **Atomic Operations:**
   - Any disk persistence operation must be atomic. Write to a temporary file in the same filesystem directory and rename via `os.replace`.
6. **Code Cleanliness & Compilation:**
   - All Python files must compile cleanly with `python -m py_compile`.
   - `pip check` must report zero broken requirements after any modification.
