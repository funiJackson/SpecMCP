---
description: Health-check every SpecDD spec in scope — runs `validate_spec` (including cross-spec inheritance rules) across all `.sdd` files and reports an aggregated summary. Routes through the `specdd-mcp` MCP server. Use as `/specc:audit` or `/specc:audit <path>`.
argument-hint: <optional path to scope the audit, e.g. "src/billing">
---

# SpecDD audit

Validate the SpecDD specs in this repo and report their health. This command is **read-only** — it surfaces problems, it never fixes them. Use the `specdd-mcp` MCP server for every operation; do not hand-roll linting by `Read`-ing `.sdd` files.

## Scope

> $ARGUMENTS

If `$ARGUMENTS` names a path, audit only that subtree. If empty, audit the whole repo.

## Preflight

Confirm SpecDD is initialized:

- A `.specdd/` directory exists at or above the working directory, **or**
- At least one `.sdd` file is reachable.

If neither is true, tell the user this is not a SpecDD project and stop.

Determine the absolute `repo_root` (the directory holding `.specdd/`, or the repo root). You pass it to every call below.

## Procedure (follow in order)

### 1. Enumerate the specs

Call:

```
list_specs(repo_root=<root>, scope=<$ARGUMENTS or omit>, include_task_summary=false)
```

This is the authoritative list of specs to audit. Each entry's `path` is **repo-relative POSIX**.

If the call returns `TOO_LARGE`, the scope is too broad — ask the user to narrow it and stop. If `warnings` is non-empty, those specs failed to parse; record them as audit failures (a spec that won't parse is the most severe finding) and keep going.

### 2. Validate each spec

For every `path` from step 1, build the absolute path `<repo_root>/<path>` and call:

```
validate_spec(path=<repo_root>/<path>, check_inheritance=true, repo_root=<root>)
```

`check_inheritance=true` plus `repo_root` lights up the cross-spec rules (`DUPLICATE_PARENT_RULE`, `CONFLICTING_INHERITANCE`, `TASK_VIOLATES_MUSTNOT`) on top of the single-file rules. Collect each spec's `issues` and `summary` (`{errors, warnings}`).

Do not stop on the first spec with errors — audit is a full sweep. One spec's failure never aborts the rest.

### 3. Aggregate

Tally across all specs:

- total `errors` and total `warnings`
- number of specs that are clean (zero issues)
- specs that failed to parse (from step 1 warnings)

### 4. Report

Lead with a headline verdict: e.g. "12 specs audited — 2 errors in 1 spec, 5 warnings in 3 specs" or "All 12 specs clean."

Then, **errors first**, list each spec that has findings, in this order: parse failures, then specs with errors, then specs with only warnings. For each, show the spec path and its issues as `severity code message (path:line)`. For cross-spec findings, also quote the `related_spec:related_line` so the user can see both ends of the relationship.

Distinguish severities clearly:

- **errors** — block a healthy spec; the user should fix these (`MISSING_SPEC_HEADER`, `INVALID_TASK_STATE`, `DUPLICATE_TASK_ID`, `MALFORMED_SECTION`).
- **warnings** — advisory; some are noisy by design. Call out that `TASK_VIOLATES_MUSTNOT` is a mechanical, high-false-positive match and may be fine.

End with the single highest-leverage fix if one stands out. Keep a clean result to one line.

## Hard rules

### Must

- Use `list_specs` to enumerate and `validate_spec` to check. Never substitute `Read` + manual inspection.
- Pass `check_inheritance=true` and `repo_root` so cross-spec rules actually run.
- Audit every spec in scope, even after finding errors. Report parse failures as the most severe finding.
- Quote `path:line` (and `related_spec:related_line` for cross-spec issues) for every finding.

### Must not

- Never edit a spec or code to fix a finding. This command reports; fixing is a separate `/specc` turn the user initiates.
- Never downgrade or hide an error to make the report look cleaner.
- Never expand scope beyond what `$ARGUMENTS` requested.
