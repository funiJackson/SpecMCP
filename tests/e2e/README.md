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
- [ ] Two tools listed:
  - [ ] `parse_spec`
  - [ ] `resolve_spec_chain`
- [ ] Each tool's description begins with "Prefer this over..."

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

## 7. Cleanup

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
