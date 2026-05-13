---
description: Implement or modify code under SpecDD spec discipline. Routes all constraint extraction and task mutation through the `specdd-mcp` MCP server instead of raw file tools. Use as `/specc <what to do>` inside a project containing a `.specdd/` directory.
argument-hint: <e.g. "implement task #2 in src/billing/invoice.sdd">
---

# SpecDD implementation mode

You are now operating under SpecDD spec discipline for this turn. `.sdd` files in this repo are binding development contracts, not documentation. The `specdd-mcp` MCP server is available — use its tools for every operation listed below. Do not fall back to `Read` / `Edit` / `Grep` on `.sdd` files for those operations.

## The user's task

> $ARGUMENTS

If `$ARGUMENTS` is empty, ask the user what file they want to work on and which task to take, then continue.

## Preflight

Before starting, verify SpecDD is initialized in this repo:

- A `.specdd/` directory exists at or above the working directory, **or**
- At least one `.sdd` file is reachable from the working directory.

If neither is true, tell the user: "This project does not appear to be a SpecDD project. Run `/specc:bootstrap` to initialize, or run `specdd-mcp bootstrap` from the CLI." Then stop.

## Procedure (follow in order)

### 1. Identify the target

Determine the concrete file path the user wants to work on. If they named a `.sdd` file, the target is the code that spec governs (look at the spec's `Owns:` field). If they named a code file, that is the target. If the path is ambiguous, ask the user before continuing.

### 2. Resolve effective constraints

Call:

```
get_effective_constraints(target=<path>)
```

Read every field of the result:

- `must` / `must_not` — binding rules on what you implement
- `forbids` — banned imports, modules, paths
- `depends_on` — allowed collaborators
- `effective_write_scope` — the **only** files you may edit
- `write_authority_source` — which spec grants that scope (`null` means no scope)
- `tasks` — implementation candidates with provenance
- `conflicts` — disagreements within the chain
- `done_when` — completion criteria for the nearest spec

If `conflicts` is non-empty, **stop**. Quote both sides with their `path:line` provenance and ask the user how to resolve. Never pick a side yourself.

If `write_authority_source` is `null`, **stop**. Tell the user this area has no SpecDD coverage and ask whether to (a) proceed without spec discipline for this turn, (b) draft a spec first via `/specc:draft`, or (c) inherit from a sibling area via an explicit `References:` link.

### 3. Confirm the task

If `$ARGUMENTS` named a specific task (e.g. `#2`, "validate currency"), find it in the `tasks` returned by step 2. If not, list open tasks to the user and ask which to take. Do not pick yourself.

### 4. Plan the edits

Decide which files you intend to modify. Before writing anything, call:

```
check_modification_scope(target=<path>, proposed_files=[...])
```

If `out_of_scope` is non-empty, **stop**. Explain which files fall outside the scope granted by `write_authority_source` and ask the user whether to (a) narrow the plan, (b) extend the spec's `Can modify:` to include them, or (c) escalate.

### 5. Implement

Use `Edit` / `Write` only on files in the `allowed` list from step 4. While implementing:

- Honor every entry in `must` and `must_not` from step 2.
- Never import anything in `forbids`.
- When a decision is constrained by a rule, cite its provenance in your reasoning: "Not importing stripe because `Forbids:` in `src/billing/module.sdd:14`."

### 6. Verify

Run the project's tests / type-checks / linters. If you do not know the commands, ask the user. Do not skip this — task completion is gated on verification, not on code being written.

### 7. Update task state

Call:

```
update_task_status(spec_path=<path>, identifier={task_line | task_id}, new_state="done")
```

Use `task_line` (from step 2's task list) when possible — it is the safest identifier. Never use `Edit` to flip `[ ]` to `[x]`.

If the call returns `TASK_AMBIGUOUS`, the `details.candidates` field lists the matching tasks; pick the right one and retry with a more specific identifier.

If the call returns a stale-file error (file changed since you parsed it), re-run `get_effective_constraints` to get fresh state, re-identify the task, and retry once. If it fails again, surface to the user — the file is being edited concurrently.

### 8. Validate

Call:

```
validate_spec(path=<nearest_spec>, check_inheritance=true)
```

If new errors or warnings appear that did not exist before this turn, fix the cause or surface them to the user before declaring complete.

### 9. Report

End the turn with a concise summary:
- Which task moved to `[x]` (and where).
- Which files changed.
- Which tests / checks pass.
- Any constraint that came close to being violated and how you avoided it.
- Any open `[!]` or `[?]` you discovered but did not resolve.

## Hard rules

### Must

- Use `get_effective_constraints` as the **first** non-clarifying action. Not `Read`. Not your memory of the spec.
- Use `update_task_status` for every `[ ] ↔ [x] / [-] / [!] / [?]` transition.
- Treat `effective_write_scope` as the authoritative edit boundary.
- Cite provenance (`path:line`) when a rule binds a decision.
- Stop on `conflicts`, on `out_of_scope`, or on `write_authority_source: null` — these are user decisions, not your judgment calls.

### Must not

- Never `Read` a `.sdd` file for constraint extraction. (Reading to display verbatim to the user is fine.)
- Never `Edit` a task line. Ever.
- Never mark a task `[x]` before tests / checks pass.
- Never touch files outside `effective_write_scope` without explicit user permission for this turn.
- Never batch unrelated task completions opportunistically.
- Never resolve a `conflicts` entry by picking a side yourself.
- Never silently fall back to raw file tools when an MCP tool returns an error. Fix the cause or escalate.

## When to stop using this command

If the user types `/specc` in a turn where the task does not actually need spec discipline (a UX tweak, an exploratory question, a one-off script), proceed as normal but mention in your final report that the slash command added no real value — they can drop it next time.

If they need to do something SpecDD forbids and have explicitly authorized it ("ignore the spec for this turn"), do as asked but flag once that you are operating outside spec guarantees.
