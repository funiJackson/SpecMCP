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

`specdd-mcp` currently exposes **5 of the 9 planned v1 tools**:

| Tool | Status | What it does |
|---|---|---|
| `mcp__specdd__parse_spec` | ✅ PR 2 | Parse a `.sdd` file or content into ParsedSpec |
| `mcp__specdd__resolve_spec_chain` | ✅ PR 2 | Build the ordered chain of specs from repo root to a target |
| `mcp__specdd__list_tasks` | ✅ PR 3 | Cross-spec task discovery with state/text/id filters |
| `mcp__specdd__get_effective_constraints` | ✅ PR 3 | Merged view of all inherited rules + 4 conflict detectors |
| `mcp__specdd__update_task_status` | ✅ PR 4 | Atomic byte-faithful batch task-state writes |
| `mcp__specdd__check_modification_scope` | ⏳ PR 5 | Pre-edit gate for write authority |
| `mcp__specdd__validate_spec` | ⏳ PR 5 | Spec health check |
| `mcp__specdd__list_specs` | ⏳ PR 7 | Repo-wide spec index |
| `mcp__specdd__find_ownership_conflicts` | ⏳ PR 7 | Multi-owner overlap detection |

See [`DESIGN.md`](./DESIGN.md) §5 for the full tool contracts and
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

## License

Apache 2.0.
