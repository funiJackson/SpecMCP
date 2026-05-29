---
description: Draft a new SpecDD `.sdd` spec, validate it, preview it, and write it only after you approve. Routes spec creation through the `specdd-mcp` MCP server's `create_spec`. Use as `/specc:draft <kind> <name>`, e.g. `/specc:draft service billing`.
argument-hint: <kind> <name>  — kind is a SpecLevel (app, module, feature, service, model, adapter, api, component, job, event, policy, custom)
---

# SpecDD draft

Help the user author a new `.sdd` spec. You draft the body, validate it, **show it, and ask before anything is written**. Writing happens only through the `specdd-mcp` `create_spec` tool — never `Write` a `.sdd` by hand, and never write without explicit approval.

## The request

> $ARGUMENTS

Parse `$ARGUMENTS` as `<kind> <name>`:

- **kind** — one of the SpecDD levels: `app`, `module`, `feature`, `service`, `model`, `adapter`, `api`, `component`, `job`, `event`, `policy`, `custom`. If it isn't one of these, ask the user to pick a valid kind and stop.
- **name** — the human name for the `Spec:` header (e.g. "Billing Service"). May be multiple words.

If `$ARGUMENTS` is empty or missing a piece, ask the user for the kind and name, then continue.

## Preflight

Confirm SpecDD is initialized (a `.specdd/` directory at or above the working directory, or at least one reachable `.sdd`). If not, tell the user and stop. Determine the absolute `repo_root`.

## Procedure (follow in order)

### 1. Agree on the path

Propose a file path whose location makes the level **infer to `kind`**, so the written spec carries the intended level without a mismatch warning. Conventions:

- A directory hint: `services/billing.sdd`, `models/invoice.sdd`, `components/form.sdd`.
- Or a filename suffix: `billing.service.sdd`, `invoice.model.sdd`.

Show the user the proposed path and **confirm it** before drafting. Let them override. The chosen path must not already exist (you'll confirm again at write time).

### 2. Gather inherited context

Before drafting rules, learn what this spec will inherit. Call:

```
get_effective_constraints(target=<proposed path's directory>, repo_root=<root>)
```

Use the result to draft *consistently* with ancestors:

- Don't restate a parent's `must` / `must_not` verbatim — inheritance carries it down (a duplicate trips `DUPLICATE_PARENT_RULE`).
- Don't draft a `Depends on:` that an ancestor `Forbids:` (that trips `CONFLICTING_INHERITANCE`).
- Note the `write_authority_source` and existing scope so the new spec's `Owns:` doesn't collide with a sibling's.

If the target sits outside any existing chain, that's fine — you're drafting a fresh root.

### 3. Draft the body

Work with the user to fill in the spec. `Purpose:` is the one section you should always include. Add the others when they apply to this kind:

- **Write authority** — `Owns:`, `Can modify:`, `Can read:`
- **Rules** — `Must:`, `Must not:`, `Depends on:`, `Forbids:`
- **Links** — `References:`
- **Work** — `Tasks:` (each becomes an open task)

Keep rules local and specific. Don't pad the spec with generic boilerplate; an empty section is better omitted than filled with filler.

### 4. Validate the draft (no write yet)

Render the draft as `.sdd` text in canonical section order — `Spec:`, `Platform:`, `Purpose:`, `Owns:`, `Can modify:`, `Can read:`, `References:`, `Must:`, `Must not:`, `Depends on:`, `Forbids:`, `Tasks:` — two-space-indented bullets, tasks written as `[ ] <text>`, one blank line between sections. Then call:

```
validate_spec(content=<rendered draft>, virtual_path=<proposed path>, check_inheritance=true, repo_root=<root>)
```

`check_inheritance=true` surfaces conflicts with ancestor specs while you can still fix them. If there are **errors**, fix the draft and re-validate before showing it. Carry any remaining **warnings** into the next step so the user sees them.

### 5. Show it and ASK

Show the user the full rendered draft and the validation result (errors fixed; warnings listed with a one-line note on each). Then ask, explicitly: **"Write this to `<path>`?"**

Do not proceed without a clear yes. If they want changes, loop back to step 3.

### 6. Write

On approval, write via:

```
create_spec(path=<path>, name=<name>, level=<kind>, purpose=..., owns=[...], must=[...], must_not=[...], depends_on=[...], forbids=[...], references=[...], can_modify=[...], can_read=[...], tasks=[...])
```

`create_spec` re-validates and writes atomically, refusing to overwrite. Handle its result:

- `ALREADY_EXISTS` — a file appeared at that path. Do **not** overwrite. Tell the user; offer a different path.
- `INVALID_INPUT` with `details.issues` — the assembled spec failed validation. Show the issues, fix, and retry (this should be rare since step 4 already validated).
- Success — note the returned `content_hash`; the caller can chain straight into `add_task` / `update_task_status` without a re-read.

### 7. Report

Confirm the path written and the spec's level. Offer the obvious next step: `/specc <task>` to start implementing against it, or `/specc:audit` to re-check it in the context of the whole repo.

## Hard rules

### Must

- Ask for explicit approval **before** the `create_spec` call. The draft/preview is the whole point of this command.
- Write only through `create_spec`. Never `Write` or `Edit` a `.sdd` file directly.
- Validate the draft (step 4) before showing it; surface warnings honestly.
- Pick a path whose inferred level matches `kind` so the spec isn't mislabeled.
- Draft consistently with inherited constraints from step 2.

### Must not

- Never write or overwrite a spec without explicit user approval.
- Never overwrite an existing file — `create_spec` refuses, and so do you.
- Never accept a `kind` outside the SpecDD level list.
- Never pad the spec with boilerplate sections that don't apply.
