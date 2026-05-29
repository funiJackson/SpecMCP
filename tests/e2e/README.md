# E2E manual checklist for specdd-mcp

Pre-release smoke test in a real Claude Code session. **Run this before tagging
a new version.** Takes ~5 minutes.

It covers the gaps left by the automated tests in [`test_server.py`](../test_server.py):

- `claude mcp add` actually registers the server
- Claude Code's UI surfaces our tool descriptions correctly
- Tools behave under realistic agent usage (Claude in the loop)

## Prerequisites

- [ ] Claude Code CLI installed — `claude --version` succeeds
- [ ] Python 3.10+
- [ ] Repo cloned and dev dependencies installed:
  ```bash
  python -m venv .venv
  source .venv/bin/activate
  pip install -e ".[dev]"
  ```
- [ ] Server starts standalone (sanity check):
  ```bash
  specdd-mcp < /dev/null && echo ok
  # → ok
  ```

## 1. Register the server

```bash
claude mcp add specdd "$(which specdd-mcp)"
```

If the syntax has changed, check `claude mcp add --help`.

**Last verified against:**

- Claude Code: _(fill in version when running)_
- MCP Python SDK: _(`pip show mcp | grep Version`)_

## 2. Verify the connection

Open a fresh Claude Code session **inside this repository**:

```bash
cd /path/to/SpecMCP
claude
```

In the session, run `/mcp`. Expected:

- [ ] `specdd` appears in the connected-servers list
- [ ] 13 tools listed — the 9 v1 tools:
  - [ ] `parse_spec`
  - [ ] `resolve_spec_chain`
  - [ ] `list_tasks`
  - [ ] `get_effective_constraints`
  - [ ] `update_task_status`
  - [ ] `check_modification_scope`
  - [ ] `validate_spec`
  - [ ] `list_specs`
  - [ ] `find_ownership_conflicts`
- [ ] ...plus the 4 v2 tools:
  - [ ] `add_task`
  - [ ] `check_dependencies`
  - [ ] `create_spec`
  - [ ] `bootstrap_project`
- [ ] Read-tool descriptions carry the "Prefer this over..." pitch;
      `update_task_status` advertises itself as "the **only** tool that
      changes a task's state" (no longer the *only* write tool — `add_task`,
      `create_spec`, and `bootstrap_project` also write)

If the server shows as failed/disconnected:

- Verify `which specdd-mcp` resolves to the venv binary, not a stale one
- Run the server directly: `specdd-mcp < /dev/null && echo ok`
- Check stderr — server startup logs to it

## 3. Smoke test: chain resolution

Ask Claude (verbatim or paraphrased):

> Use `mcp__specdd__resolve_spec_chain` on
> `tests/fixtures/chains/simple_3_level/src/billing/services/invoice.ts`
> and show me the chain in order.

Expected:

- [ ] Claude invokes the tool (visible in the tool-use trace)
- [ ] Response contains a chain of 3 specs in this order:
  1. `Billing Platform` (level=app)
  2. `Billing Module` (level=module)
  3. `Invoice Service` (level=service)
- [ ] `nearest.name == "Invoice Service"`
- [ ] All paths are POSIX repo-relative (forward slashes, no backslashes)

## 4. Smoke test: parse inline content

Ask Claude:

> Call `mcp__specdd__parse_spec` with `content` set to the string
> `Spec: Hello\n\nMust:\n  Be polite.\n` and show me the result.

Expected:

- [ ] Response `ok: true`
- [ ] `data.name == "Hello"`
- [ ] `data.must == ["Be polite."]`

## 5. Smoke test: structured error path

Ask Claude:

> Call `mcp__specdd__parse_spec` with `path` set to `/this-does-not-exist.sdd`.

Expected:

- [ ] Response `ok: false`
- [ ] `error == "NOT_FOUND"`
- [ ] Claude continues operating — the structured error is handled gracefully,
      no hard crash

## 6. Same-directory ordering (sanity)

Ask Claude:

> Use `mcp__specdd__resolve_spec_chain` on
> `tests/fixtures/chains/multiple_in_one_dir/src/billing/code.ts`.
> What's the order of specs in the chain?

Expected:

- [ ] 3 specs in this exact order:
  1. `Billing Module` (level=module)
  2. `Invoice Feature` (level=feature)
  3. `Invoice Service` (level=service)
- [ ] The order is NOT alphabetical by filename
      (alphabetical would put feature → invoice.service → module)

## 7. Smoke test: list_tasks (PR 3)

Ask Claude:

> Use `mcp__specdd__list_tasks` on the repo root of
> `tests/fixtures/chains/simple_3_level/`. Show me the open tasks.

Expected:

- [ ] Returns 2 open tasks (both from `invoice.sdd` in the services dir)
- [ ] Each task has `state == "open"`, an `id` like `"#1"` / `"#2"`,
      and a `source` ending in `/invoice.sdd`
- [ ] Default `states` filter is `["open"]` — done/skipped tasks NOT included

Then ask Claude to filter:

> Same call but with `include_blocked=true`.

- [ ] The result set expands to include `blocked` and `needs_decision` task
      states (any present in fixtures get surfaced).

## 8. Smoke test: get_effective_constraints (PR 3) — **THE main `/specc` tool**

Ask Claude:

> Use `mcp__specdd__get_effective_constraints` on
> `tests/fixtures/chains/simple_3_level/src/billing/services/invoice.ts`.

Expected:

- [ ] Response has a full `EffectiveConstraints` shape with:
  - [ ] `chain_summary` listing 3 specs (`Billing Platform`, `Billing Module`,
        `Invoice Service`)
  - [ ] `must` containing 5 rules, each carrying `source` + non-zero `line`
  - [ ] `must_not` containing 4 rules
  - [ ] `forbids == ["stripe"]` from `src/billing/module.sdd`
  - [ ] `effective_write_scope` with glob `src/billing/*` expanded plus
        the literal `invoice.ts` / `invoice.test.ts`
  - [ ] `write_authority_source ==
        "src/billing/services/invoice.sdd"` (leaf wins)
  - [ ] `tasks` listing 2 open tasks
  - [ ] `conflicts == []` (canonical fixture is clean)

Now test conflict surfacing on one of the conflict fixtures:

> Use `mcp__specdd__get_effective_constraints` on
> `tests/fixtures/chains_with_conflicts/depends_on_vs_forbids/src/code.ts`.
> What conflicts does it surface?

- [ ] `conflicts` has exactly one entry, kind=`depends_on_vs_forbids`
- [ ] Both sides carry source paths AND line numbers
- [ ] Claude correctly identifies this as a STOP-level conflict (not advisory)

## 9. Smoke test: update_task_status (PR 4) — **the task-state write tool**

This is the highest-risk surface in the server: it touches files on disk.
The e2e gate exercises (a) byte-preservation, (b) the STALE_FILE recovery
loop, and (c) the TASK_AMBIGUOUS recovery loop in a real session.

**Pre-step — prep a sandbox spec.** Don't update the canonical fixture
in place; copy it so step 9.5 leaves the repo clean.

```bash
mkdir -p /tmp/specdd-e2e
cp tests/fixtures/chains/simple_3_level/src/billing/services/invoice.sdd \
   /tmp/specdd-e2e/invoice.sdd
cat /tmp/specdd-e2e/invoice.sdd  # note the open tasks `[ ] #1` / `[ ] #2`
```

### 9.1 Happy path: flip one task to done

Ask Claude:

> Use `mcp__specdd__parse_spec` with `path=/tmp/specdd-e2e/invoice.sdd`
> to find the open tasks. Then compute the SHA-256 of the file's bytes
> and call `mcp__specdd__update_task_status` to mark task `#1` as done.

Expected:

- [ ] Claude reads the file, finds two open tasks `#1` / `#2`
- [ ] Claude computes the file's SHA-256 and passes it as
      `expected_content_hash`
- [ ] Tool returns `ok: true` with:
  - [ ] `data.applied[0].previous_state == "open"`
  - [ ] `data.applied[0].task.id == "#1"`
  - [ ] `data.diff` showing exactly one `-  [ ] #1` / `+  [x] #1` pair
  - [ ] `data.new_content_hash` is a 64-char hex string
- [ ] Inspect the file on disk:
      ```bash
      diff <(cat tests/fixtures/chains/simple_3_level/src/billing/services/invoice.sdd) \
           /tmp/specdd-e2e/invoice.sdd
      ```
      Diff shows **only** the `[ ] #1` → `[x] #1` line. No BOM/CRLF/
      whitespace drift on unrelated lines.

### 9.2 Chained update via returned hash

Ask Claude:

> Now mark task `#2` as `blocked`, using the `new_content_hash` from
> the previous response as `expected_content_hash` — don't re-read the
> file.

Expected:

- [ ] Claude passes the returned hash (no fresh `parse_spec` / read)
- [ ] Tool returns `ok: true`
- [ ] On disk: `[x] #1 …` and `[!] #2 …`

### 9.3 STALE_FILE recovery loop

Modify the file out-of-band to simulate a concurrent editor save:

```bash
printf "\n# trailing comment\n" >> /tmp/specdd-e2e/invoice.sdd
```

Ask Claude:

> Set task `#1` back to `open` using the `new_content_hash` from step 9.2.

Expected:

- [ ] Tool returns `ok: false`, `error: "STALE_FILE"`
- [ ] `details.expected_hash` matches the hash from step 9.2
- [ ] `details.actual_hash` is **different** (the trailing comment changed
      the file)
- [ ] Claude recognises the situation and re-parses the file to get the
      fresh hash, then retries — second call returns `ok: true`

### 9.4 TASK_AMBIGUOUS recovery loop

Append two tasks sharing a prefix:

```bash
cat <<'EOF' >> /tmp/specdd-e2e/invoice.sdd

Tasks:
  [ ] #10 Add validation for currency
  [ ] #11 Add validation for amount
EOF
```

Ask Claude:

> Mark the task starting with "Add validation" as `done`.

Expected:

- [ ] Tool returns `ok: false`, `error: "TASK_AMBIGUOUS"`
- [ ] `details.candidates` lists both tasks, each with `line`, `id`,
      `text`, `current_state` keys
- [ ] Claude reads the candidates, picks the one the user meant, and
      retries with `task_line` (NOT `task_text_prefix`) — the second
      call returns `ok: true`

### 9.5 Cleanup the sandbox

```bash
rm -rf /tmp/specdd-e2e
```

- [ ] Canonical fixture under `tests/fixtures/chains/simple_3_level/` is
      byte-identical to the committed version (`git status` is clean)

## 10. Smoke test: check_modification_scope (PR 5) — the pre-edit gate

Ask Claude:

> Use `mcp__specdd__check_modification_scope` with
> `target=tests/fixtures/chains/simple_3_level/src/billing/services/invoice.ts`
> and `proposed_files` set to `["src/billing/services/invoice.ts",
> "src/billing/services/invoice.test.ts", "src/billing/services/secrets.py"]`.

Expected:

- [ ] `ok: true`
- [ ] `authority_source == "src/billing/services/invoice.sdd"`
- [ ] `allowed` contains both `invoice.ts` (exists) and `invoice.test.ts`
      (a **new** file, allowed by the literal pattern even though it isn't
      on disk)
- [ ] `out_of_scope == ["src/billing/services/secrets.py"]`
- [ ] `multiple_authorities` is `null` (this chain has a single authority)

## 11. Smoke test: validate_spec (PR 5) — the health check

Ask Claude:

> Use `mcp__specdd__validate_spec` with
> `path=tests/fixtures/chains/simple_3_level/src/billing/services/invoice.sdd`
> and `check_inheritance=true`.

Expected:

- [ ] `ok: true`
- [ ] `summary.errors == 0` (the canonical fixture is clean)
- [ ] `check_inheritance=true` runs the cross-spec rules (`DUPLICATE_PARENT_RULE`,
      `CONFLICTING_INHERITANCE`, `TASK_VIOLATES_MUSTNOT`); the clean fixture
      surfaces none — no error, no extra findings

Then exercise the error rules:

> Call `mcp__specdd__validate_spec` with `content` set to
> `Tasks:\n  [y] #1 bad state\n  [ ] #2 ok\n`.

- [ ] `summary.errors >= 2`
- [ ] `issues` includes `MISSING_SPEC_HEADER` and `INVALID_TASK_STATE`,
      each with a `line` (the `[y]` line is rejected as an invalid state)

To see `DUPLICATE_TASK_ID`, two **valid** tasks must share an id — an
invalid-state line isn't counted as a task:

> Call `mcp__specdd__validate_spec` with `content` set to
> `Spec: Dup\n\nTasks:\n  [ ] #1 one\n  [ ] #1 two\n`.

- [ ] `issues` includes `DUPLICATE_TASK_ID` with a `line`

## 12. Full `/specc` flow (PR 5) — **the whole workflow, end to end**

This is the first time every tool composes in one real session. With
`/specc` installed (PR 6) you invoke it directly; until then, drive the
sequence by hand.

> In `tests/fixtures/chains/simple_3_level/`, implement task `#1` in the
> invoice service.

Verify Claude calls, **in order**:

- [ ] `get_effective_constraints` — first non-clarifying action; returns
      `conflicts: []` and `write_authority_source: src/billing/services/invoice.sdd`
- [ ] (confirms the task with you before editing)
- [ ] `check_modification_scope` — the planned file comes back `allowed`
- [ ] `Edit` / `Write` — only on files in the `allowed` list
- [ ] `update_task_status` — flips `[ ] #1` to `[x] #1` (NOT a raw `Edit`)
- [ ] `validate_spec` — `ok: true`, no new errors

Then confirm the file landed correctly:

- [ ] The spec shows `[x]` on task `#1` and `[ ]` still on `#2`
- [ ] No unrelated lines changed (`git diff` is limited to the one task line
      plus whatever code the implementation legitimately added)

> Restore the fixture afterward: `git checkout
> tests/fixtures/chains/simple_3_level/`

## 13. v2 tools (quick pass)

The four v2 tools. Behavior is covered deterministically by the automated
suite; this is a UI/round-trip sanity pass in a real session.

> Use `mcp__specdd__list_specs` on the repo root of
> `tests/fixtures/chains/simple_3_level/`.

- [ ] `ok: true`, 3 entries (`app`, `module`, `service`), each with a
      `task_summary` of per-state counts.

> Use `mcp__specdd__find_ownership_conflicts` on the same repo root.

- [ ] `ok: true`, `data` is a list (empty for this single-owner fixture).

> Use `mcp__specdd__check_dependencies` on
> `tests/fixtures/chains_with_conflicts/depends_on_vs_forbids/src/code.ts`
> with `proposed_dependencies` set to `["stripe-sdk", "react"]`.

- [ ] `stripe-sdk` is flagged `kind: "forbids"` with `path:line` provenance;
      `react` is not flagged.

In a scratch dir (e.g. `/tmp/specdd-v2`):

> Use `mcp__specdd__create_spec` to scaffold `/tmp/specdd-v2/svc.sdd`
> (name "Svc", a purpose, one task). Then `mcp__specdd__add_task` to add a
> second task using the returned `content_hash`. Then
> `mcp__specdd__bootstrap_project` on `/tmp/specdd-v2/repo` with
> `with_app=true`.

- [ ] `create_spec` writes the file (refuses to overwrite on a second call).
- [ ] `add_task` appends the second task using the chained hash (no re-read).
- [ ] `bootstrap_project` creates `.specdd/bootstrap*.md`, `AGENTS.md`,
      `CLAUDE.md`, and `app.sdd`; a re-run reports them all `skipped`.
- [ ] Cleanup: `rm -rf /tmp/specdd-v2`.

## 14. CLI surface (no MCP client needed)

Run directly in a shell — these don't go through Claude:

```bash
specdd-mcp version                     # prints the version
specdd-mcp validate tests/fixtures/chains/simple_3_level/   # exit 0, all clean
specdd-mcp bootstrap /tmp/specdd-cli   # creates bootstrap files; re-run skips
specdd-mcp install-commands --dir /tmp/specdd-cli/cmds      # copies /specc commands
rm -rf /tmp/specdd-cli
```

- [ ] `validate` exits non-zero when pointed at a spec with an error
      (e.g. a file containing `Tasks:\n  [ ] #1 a\n  [ ] #1 b\n`).

## 15. Cleanup

```bash
claude mcp remove specdd
```

- [ ] `/mcp` no longer lists `specdd`

## Done

All boxes ticked → the server is shippable for this version.

Any failure → file an issue including:

- Which step failed
- `claude --version`
- `pip show mcp | grep Version`
- Server stderr from the failure (useful: redirect with
  `claude mcp add specdd "specdd-mcp 2>/tmp/specdd.log"` then `tail` the log)
