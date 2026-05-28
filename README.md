# specdd-mcp

Deterministic MCP tools and a Claude Code slash command for working with [SpecDD](https://github.com/specdd/specdd) `.sdd` specification files.

> **Status:** under active development. v1 ships 9 MCP tools, 2 slash commands, and a CLI. See [`DESIGN.md`](./DESIGN.md) for the full design, and [`plans/`](./plans/) for per-PR implementation plans.

## What this is

SpecDD lets you put small `.sdd` spec files next to the code they govern. AI coding agents read those specs as binding contracts (must do, must not do, owned files, dependencies, tasks, scenarios). `specdd-mcp` gives Claude (and other MCP clients) deterministic tools to parse, chain-resolve, merge, validate, and update those specs — operations that the LLM cannot reliably do on its own with `Read`/`Edit`.

## Architecture

```
slash command  →  MCP server (this package)  →  .sdd files on disk
   /specc          parse / resolve / merge        binding contracts
                   list_tasks / update / scope
                   validate / list / conflicts
```

See [`DESIGN.md`](./DESIGN.md) for the long form.

## Quick links

- [`DESIGN.md`](./DESIGN.md) — full design contract for the server, tools, and surfaces.
- [`SKILL.md`](./SKILL.md) — the minimal ambient skill (nudges users toward `/specc`).
- [`commands/specc.md`](./commands/specc.md) — the main slash command playbook.
- [`plans/`](./plans/) — per-PR implementation plans (PR 1 through PR 5).

## Install (development)

```bash
python -m venv .venv
source .venv/bin/activate           # On Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Run the tests

```bash
pytest                              # all tests
pytest --cov=specdd_mcp             # with coverage
ruff check src/ tests/              # lint
mypy src/                           # type check
```

## Running the server

### Quick start (Claude Code)

```bash
# 1. Install the package (one-time)
pip install -e ".[dev]"

# 2. Register with Claude Code
claude mcp add specdd "$(which specdd-mcp)"

# 3. In a Claude Code session, verify:
/mcp                # should list `specdd` as connected
```

### What ships today

`specdd-mcp` exposes **all 9 v1 tools**, plus the first **v2** tools:

| Tool | Status | What it does |
|---|---|---|
| `mcp__specdd__parse_spec` | ✅ PR 2 | Parse a `.sdd` file or content into ParsedSpec |
| `mcp__specdd__resolve_spec_chain` | ✅ PR 2 | Build the ordered chain of specs from repo root to a target |
| `mcp__specdd__list_tasks` | ✅ PR 3 | Cross-spec task discovery with state/text/id filters |
| `mcp__specdd__get_effective_constraints` | ✅ PR 3 | Merged view of all inherited rules + 4 conflict detectors |
| `mcp__specdd__update_task_status` | ✅ PR 4 | Atomic byte-faithful batch task-state writes |
| `mcp__specdd__check_modification_scope` | ✅ PR 5 | Pre-edit gate for write authority |
| `mcp__specdd__validate_spec` | ✅ PR 5 + PR 7 | Spec health check — nine single-file rules plus three cross-spec inheritance rules |
| `mcp__specdd__list_specs` | ✅ PR 8 | Repo-wide spec index with optional per-state task summaries |
| `mcp__specdd__find_ownership_conflicts` | ✅ PR 8 | Multi-owner overlap detection across `Owns:` claims |
| `mcp__specdd__add_task` | ✅ PR 9 | Byte-faithful insertion of a new `open` task (v2) |
| `mcp__specdd__check_dependencies` | ✅ PR 10 | Vet proposed deps against inherited `Forbids:` / `Must not:` (v2) |

The full `/specc` workflow runs end-to-end — see
[Validating specs](#validating-specs) and
[Checking write scope before editing](#checking-write-scope-before-editing).

See [`DESIGN.md`](./DESIGN.md) §5–§6 for the full tool contracts and
[`plans/`](./plans/) for the PR-by-PR implementation schedule.

### Manual verification

After registering, run through [`tests/e2e/README.md`](./tests/e2e/README.md)
— a ~5-minute checklist that exercises the server in a real Claude Code
session. **Required before tagging a release.**

### Running standalone

```bash
specdd-mcp        # blocks on stdio, expects MCP JSON-RPC client
# Equivalent to:
python -m specdd_mcp
```

Logs are written to stderr (`stdout` is reserved for the protocol). The
server runs until its stdin closes — see `__main__.py` for the entry point.

## How the parser works

The PR 1 parser is a pure Python library — no MCP framework, no network, no
filesystem assumptions beyond reading a single file. The public entry point is
`parse_spec` in [`src/specdd_mcp/parser/__init__.py`](./src/specdd_mcp/parser/__init__.py).
The pipeline is:

```
parse_spec(path | content)
   │
   ▼
lexer.py          file/bytes/text → list[Line]  (BOM, encoding, binary detection)
   │
   ▼
sections.py       Line stream → DetectedSection ranges  (canonical + unknown)
   │
   ▼
{bullets, text, structure, tasks, scenarios}.py
                  Each known section → typed value
   │
   ▼
parse_spec.py     Assemble fields + positions → ParsedSpec
   │
   ▼
Ok(ParsedSpec, warnings=[...])  |  Err(error=ErrorCode, message=...)
```

### Key files

| File | Role |
|---|---|
| [`types.py`](./src/specdd_mcp/types.py) | Every Pydantic model (see [`DESIGN.md` §3](./DESIGN.md)). 100% coverage. |
| [`parser/lexer.py`](./src/specdd_mcp/parser/lexer.py) | Decode UTF-8, strip BOM, detect binary, split into `Line` tuples. |
| [`parser/sections.py`](./src/specdd_mcp/parser/sections.py) | Match section headers, classify known vs unknown, compute body ranges. |
| [`parser/bullets.py`](./src/specdd_mcp/parser/bullets.py) | List-shaped sections (`Must:`, `Owns:`, `Forbids:`, ...). Handles continuation indent. |
| [`parser/text.py`](./src/specdd_mcp/parser/text.py) | Single-value text sections (`Spec:`, `Platform:`, `Purpose:`). |
| [`parser/structure.py`](./src/specdd_mcp/parser/structure.py) | `Structure:` section — `path: description` pairs. |
| [`parser/tasks.py`](./src/specdd_mcp/parser/tasks.py) | `Tasks:` section. Preserves `indent` and `raw` for PR 4's surgical writes. |
| [`parser/scenarios.py`](./src/specdd_mcp/parser/scenarios.py) | Each `Scenario:` block → `ParsedScenario` with name + steps. |
| [`parser/levels.py`](./src/specdd_mcp/parser/levels.py) | Infer `SpecLevel` from path (suffix → whole-name → directory hint → custom → unknown). |
| [`parser/parse_spec.py`](./src/specdd_mcp/parser/parse_spec.py) | Orchestrator. Wires everything above into a `ParsedSpec`. |

### Design principles

- **Errors vs warnings:** parse errors (binary content, encoding, IO) are
  `Err`; soft anomalies (missing `Spec:` header, duplicate sections) are
  `warnings` on a successful `Ok`. Per-rule SpecDD validation
  (e.g. `MISSING_PURPOSE`, `INVALID_TASK_STATE`) is deferred to PR 5's
  `validate_spec`.
- **Byte-faithful task data:** every `ParsedTask` carries `indent` and `raw`
  so PR 4 can rewrite a single state symbol without touching anything else.
- **Position tracking:** every known section that appears in the source has a
  `SectionPosition(start_line, end_line)` entry — end_line is the last
  non-blank body line, so callers can quote `path:line` provenance.
- **No filesystem in `operations/` layer (added in PR 3):** the parser reads
  one file; everything else (chain walks, glob expansion) lives elsewhere.

### Example

```python
from specdd_mcp.parser import parse_spec

result = parse_spec(path="tests/fixtures/specs/full_service.sdd")
if result.ok:
    spec = result.data
    print(spec.name)          # "Invoice Service"
    print(spec.level)         # inferred from path
    print(len(spec.must))     # number of Must: rules
    for task in spec.tasks or []:
        print(task.state_symbol, task.text)
else:
    print(f"Error {result.error}: {result.message}")
```

## Modifying specs safely

`update_task_status` (PR 4) is the **only** write surface in the server.
Use it instead of `Edit` / `Write` / shell redirection whenever you need
to flip a task state, because the tool guarantees:

- **Byte-faithful preservation.** Every byte except the targeted state
  symbol(s) is preserved exactly: line endings (LF or CRLF), UTF-8 BOM,
  indentation (spaces or tabs), multi-byte characters, comments, blank
  lines, trailing whitespace.
- **Atomic write.** Writes go through a temp file + `os.replace`, so a
  partially-written file is never observable.
- **Cross-process serialization.** A per-spec lock (`fcntl.flock` on
  POSIX, `msvcrt.locking` on Windows) blocks concurrent writers from
  the same machine. Combined with the hash precondition, two agents
  racing on the same file cannot both succeed silently.
- **Stale-file detection.** Each call requires
  `expected_content_hash` — the SHA-256 of the file's bytes as the
  caller last observed them. If disk has drifted (an editor saved, an
  agent wrote), the call fails with `STALE_FILE` rather than clobbering.
- **Whole-batch atomicity.** Multi-update batches are all-or-nothing:
  if *any* identifier in the batch is unresolvable (`TASK_NOT_FOUND` /
  `TASK_AMBIGUOUS`), the file stays byte-identical to before the call.

### Recommended caller pattern

```python
from specdd_mcp.parser import parse_spec
from specdd_mcp.operations.hashing import content_hash
from specdd_mcp.operations.mutate_tasks import update_task_status
from specdd_mcp.types import Ok, UpdateRequest

# 1. Parse to find the right task.
parsed = parse_spec(path="src/billing/services/invoice.sdd")
assert isinstance(parsed, Ok)

# 2. Compute the file's SHA-256 — this is what the tool expects back as
#    `expected_content_hash` to detect drift.
expected = content_hash(open("src/billing/services/invoice.sdd", "rb").read())

# 3. Apply a batch. Each UpdateRequest picks ONE identifier mode.
result = update_task_status(
    "src/billing/services/invoice.sdd",
    expected_content_hash=expected,
    updates=[
        UpdateRequest(new_state="done", task_id="#1"),
        UpdateRequest(new_state="blocked", task_line=42),
    ],
)

# 4. Chain further updates using the returned hash — no re-parse needed.
if isinstance(result, Ok):
    next_hash = result.data.new_content_hash
```

### Recovering from `STALE_FILE` and `TASK_AMBIGUOUS`

- **`STALE_FILE`** — `details.expected_hash` and `details.actual_hash`
  show the drift. Re-parse the spec and retry with the fresh hash.
- **`TASK_AMBIGUOUS`** — `details.candidates` lists every match in
  source order with `{line, id, text, current_state}`. Retry with
  `task_line` (the safest identifier) using the candidate the user
  meant.

## Checking write scope before editing

`check_modification_scope` is the pre-edit gate (`/specc` step 4): before
touching code, confirm the files you're about to write are governed by the
nearest spec's `Owns:` / `Can modify:`.

```python
from specdd_mcp.operations.scope import check_modification_scope

report = check_modification_scope(
    target="src/billing/services/invoice.ts",
    proposed_files=[
        "src/billing/services/invoice.ts",       # exists, owned   → allowed
        "src/billing/services/invoice.test.ts",  # new file, owned → allowed
        "src/billing/services/secrets.py",       # not owned       → out_of_scope
    ],
).data

report.authority_source   # "src/billing/services/invoice.sdd"
report.allowed            # ["src/billing/services/invoice.ts", ".../invoice.test.ts"]
report.out_of_scope       # ["src/billing/services/secrets.py"]
```

Two-tier matching: an **existing** file is matched against the live glob
expansion; a **new** file (not yet on disk) is matched against the pattern
itself — so `allowed` means *"you may create this here,"* not *"this exists."*

When more than one spec in the chain claims the same file, `multiple_authorities`
is populated (the "two specs both Own the same thing" hazard the SpecDD README
warns against). The tool surfaces it rather than refusing to operate — the
caller decides. A `null` `authority_source` with a `reason` means the target
has no SpecDD coverage, or no spec in its chain declares write authority.

## Validating specs

`validate_spec` is the post-implementation health check (`/specc` step 8). It
runs nine single-file rules — and, with `check_inheritance=True` plus a
`repo_root`, three cross-spec rules — returning structured issues with
`path:line` provenance.

```python
from pathlib import Path

from specdd_mcp.operations.validation import run_validation
from specdd_mcp.parser.parse_spec import parse_spec

spec = parse_spec(path="src/billing/services/invoice.sdd").data
# Pass repo_root to activate the cross-spec inheritance rules; omit it
# (or validate raw content) to run single-file rules only.
result = run_validation(spec, check_inheritance=True, repo_root=Path("."))

result.summary   # {"errors": 0, "warnings": 0}  → clean
result.issues    # [ValidationIssue(severity, code, message, line?,
                 #                   related_spec?, related_line?), ...]
```

| Code | Severity | Triggers when |
|---|---|---|
| `MISSING_SPEC_HEADER` | error | No `Spec:` line. |
| `INVALID_TASK_STATE` | error | A `Tasks:` line uses a non-canonical state symbol. |
| `DUPLICATE_TASK_ID` | error | Two tasks share the same `#N`. |
| `MALFORMED_SECTION` | error | A section has body content the parser couldn't interpret. |
| `MISSING_PURPOSE` | warning | No `Purpose:` section. |
| `UNKNOWN_SECTION` | warning | A section name outside the canonical list (kept verbatim). |
| `EMPTY_SECTION` | warning | A known section header with no content. |
| `LONG_SPEC` | warning | File exceeds `max_lines` (default 80). |
| `OWNERSHIP_OUTSIDE_DIRECTORY` | warning | An `Owns:`/`Can modify:` pattern escapes the spec's subtree. |
| `DUPLICATE_PARENT_RULE` | warning | The spec restates an ancestor's `Must`/`Must not` verbatim (drift risk). |
| `CONFLICTING_INHERITANCE` | warning | The spec's `Depends on:` pulls in something an ancestor `Forbids:`. |
| `TASK_VIOLATES_MUSTNOT` | warning | A task mechanically restates an inherited `Must not:` (advisory; false positives expected). |

The last three are the **cross-spec** rules — they run only when
`check_inheritance=True` is passed with a `repo_root`. Each resolves the
target's spec chain, finds the conflicts where the validated spec is the
violator, and points `related_spec` / `related_line` at the inherited rule so
`/specc` can quote both sides. Without a `repo_root` (or when validating raw
`content` whose chain can't resolve) they degrade silently — the single-file
rules still run.

## License

Apache 2.0.
