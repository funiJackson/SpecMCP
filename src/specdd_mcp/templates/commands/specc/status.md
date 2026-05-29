---
description: Show a SpecDD task dashboard — open work across the repo (or a scope), grouped by spec, with per-spec progress. Routes through the `specdd-mcp` MCP server. Use as `/specc:status` or `/specc:status <path>` inside a project containing `.sdd` files.
argument-hint: <optional path to scope the report, e.g. "src/billing">
---

# SpecDD status

Report what work is outstanding across the SpecDD specs in this repo. This command is **read-only** — it never edits specs or code. Use the `specdd-mcp` MCP server for every lookup; do not `Read` / `Grep` `.sdd` files to assemble this.

## Scope

> $ARGUMENTS

If `$ARGUMENTS` names a path, scope the report to that subtree. If it is empty, report the whole repo.

## Preflight

Confirm SpecDD is initialized:

- A `.specdd/` directory exists at or above the working directory, **or**
- At least one `.sdd` file is reachable.

If neither is true, tell the user this is not a SpecDD project and stop.

Determine `repo_root` (the directory containing `.specdd/`, or the repo root). You pass an absolute `repo_root` to every tool below.

## Procedure (follow in order)

### 1. Build the spec overview

Call:

```
list_specs(repo_root=<root>, scope=<$ARGUMENTS or omit>, include_task_summary=true)
```

Each entry carries `path`, `level`, `line_count`, and a `task_summary` of `{open, done, skipped, blocked, needs_decision}`.

If the call returns `TOO_LARGE`, the scope is too broad — ask the user to narrow it (pass a subdirectory) and stop.

If `warnings` is non-empty, some specs failed to parse. List those paths under a "could not parse" note so they aren't silently dropped.

### 2. Pull the actionable tasks

Call:

```
list_tasks(repo_root=<root>, scope=<$ARGUMENTS or omit>, include_blocked=true)
```

This returns `open` plus `blocked` (`[!]`) and `needs_decision` (`[?]`) tasks — every entry that needs attention — each with its `source` spec, `line`, `state`, and `text`. (Done and skipped tasks are intentionally excluded; the overview in step 1 already carries their counts.)

### 3. Report

Lead with a one-line headline: total open tasks, plus how many specs have open work.

Then a compact overview table from step 1 — one row per spec with open work, showing `done / (open+done)` progress and flagging any spec with `blocked` or `needs_decision` counts.

Then the detail from step 2, **grouped by spec** (`source`), specs in path order, tasks within a spec in `line` order. For each task show its state marker, `line`, and `text`:

- `[ ]` open
- `[!]` blocked — call these out; they're stuck
- `[?]` needs_decision — these need the user, not implementation

Close by surfacing the single most useful next action if it's obvious (e.g. "3 `[?]` tasks are waiting on your decision before any of this can move"). Don't pad the report — a clean repo with no open tasks is a one-line "all specs are complete."

## Hard rules

### Must

- Use `list_specs` and `list_tasks` for all data. Never `Read` / `Grep` `.sdd` files to count or list tasks.
- Group the detail by spec and preserve `line` order within each spec.
- Surface parse `warnings` rather than dropping the affected specs silently.

### Must not

- Never edit a spec, a task line, or any code. This command only reports.
- Never guess counts — if a tool didn't return a number, don't invent it.
- Never expand the scope past what `$ARGUMENTS` asked for.
