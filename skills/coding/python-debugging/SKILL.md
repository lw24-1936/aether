---
name: python-debugging
description: Systematic Python debugging workflow. Read error → isolate → fix → verify.
triggers:
  - "debug"
  - "error"
  - "traceback"
  - "调试"
  - "报错"
tools_required:
  - read_file
  - terminal
  - patch_file
version: "1.0.0"
category: coding
---

# Python Debugging Workflow

When the user reports a Python error, follow this systematic approach:

## Steps

1. **Read the error carefully** — Identify the exception type, message, and file/line.
2. **Read the relevant code** — Use read_file to see the file around the error line.
3. **Understand the root cause** — Don't just fix symptoms. Trace the logic.
4. **Apply a minimal fix** — Use patch_file for targeted changes.
5. **Verify** — Run the test or script to confirm the fix.
6. **Explain** — Tell the user what was wrong and why your fix works.

## Common Patterns

- `NameError` → Missing import or undefined variable
- `TypeError` → Wrong argument type, check function signature
- `AttributeError` → Object doesn't have the method/attribute expected
- `KeyError` → Dictionary key doesn't exist
- `ImportError` / `ModuleNotFoundError` → Missing dependency

## Pitfalls

- Don't add try/except to swallow errors — fix the root cause
- Don't make multiple changes at once — one fix, one verify
- Always read the full function/method, not just the error line
