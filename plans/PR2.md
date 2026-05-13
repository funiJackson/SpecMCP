# PR 2 — MCP server skeleton + `parse_spec` + `resolve_spec_chain`

Wire FastMCP, expose the parser as the first MCP tool, add chain resolution. After this PR the project is **demonstrable in a real Claude Code session** — `claude mcp add` registers the server, Claude can call tools, you can see a chain resolve end-to-end.

This PR is mostly framework glue. The heaviest novel work is `resolve_spec_chain` itself.

---

## Scope

### In scope

- FastMCP server skeleton, stdio transport, stderr logging.
- `python -m specdd_mcp` and `specdd-mcp` entry points (the latter is just the no-arg form starting the server).
- `parse_spec` MCP tool wrapping PR 1's parser.
- `resolve_spec_chain` parser-level function **and** MCP tool wrapper.
- `repo_root` auto-detection (`.specdd/` preferred, `.git/` fallback, error otherwise).
- POSIX path normalization at all output boundaries (Windows backslashes → forward slashes).
- Symlink-skipping ancestor walk with `warnings` entries.
- `OUT_OF_SCOPE` enforcement (target paths outside repo_root rejected).
- End-to-end smoke test in a real Claude Code session against a fixture repo.

### Out of scope

- Every other tool — PRs 3–7.
- CLI subcommands beyond `python -m specdd_mcp` — PR 6/8.
- HTTP/SSE transport — not in v1 at all.
- Stale-file hash checking — PR 4 (with `update_task_status`).
- Glob expansion — PR 3 (with `get_effective_constraints`).

---

## Project structure (incremental)

Files **added** on top of PR 1:

```
src/specdd_mcp/
├── __main__.py              ← NEW: `python -m specdd_mcp` entry
├── parser/
│   └── resolve_chain.py     ← NEW: chain resolution (no MCP wiring)
└── server/
    ├── __init__.py          ← NEW
    ├── app.py               ← NEW: FastMCP instance + tool registry
    ├── tools.py             ← NEW: @mcp.tool() decorated wrappers
    ├── logging.py           ← NEW: stderr logging config
    └── paths.py             ← NEW: repo_root detection, POSIX normalization

tests/
├── fixtures/
│   └── chains/              ← NEW: multi-spec trees for chain tests
│       ├── simple_3_level/
│       ├── multiple_in_one_dir/
│       ├── symlink_ancestor/
│       └── no_repo_marker/
├── test_resolve_chain.py    ← NEW
├── test_paths.py            ← NEW
├── test_server.py           ← NEW (in-process FastMCP client)
└── e2e/
    └── README.md            ← NEW: manual E2E checklist for Claude Code
```

`pyproject.toml` gains the MCP SDK dependency and a console script:

```toml
dependencies = [
    "pydantic>=2.5",
    "mcp[cli]>=1.0",         # ships FastMCP
]

[project.scripts]
specdd-mcp = "specdd_mcp.__main__:main"
```

---

## Implementation order

| # | Commit | Time |
|---|---|---|
| 1 | Add MCP SDK dep + console script + `__main__.py` skeleton that just starts an empty FastMCP app | 1 h |
| 2 | `server/logging.py`: stderr logging with `[server]` and `[tool]` prefixes | 1 h |
| 3 | `server/paths.py`: `find_repo_root`, `to_posix`, `to_repo_relative`, `OUT_OF_SCOPE` check | 3 h |
| 4 | `server/tools.py`: `parse_spec` MCP wrapper (just calls PR 1's parser, logs invocation, returns `.model_dump()`) | 2 h |
| 5 | `parser/resolve_chain.py`: ancestor walk, same-dir level ordering, symlink skip, parse-or-malformed | 4 h |
| 6 | `server/tools.py`: `resolve_spec_chain` wrapper | 1 h |
| 7 | Synthetic chain fixtures + unit tests (`test_resolve_chain.py`) | 4 h |
| 8 | In-process MCP client test (`test_server.py`) — call tools through FastMCP without spawning a subprocess | 2 h |
| 9 | E2E manual checklist (`tests/e2e/README.md`) — concrete commands to register and test in Claude Code | 1 h |
| 10 | README "Running the server" + "Registering with Claude Code" sections | 1 h |

**Total: ~20 hours, 2–3 days.**

---

## Key design decisions

### FastMCP, not raw `mcp` SDK

FastMCP gives free JSON-schema generation from type hints and decorator-based registration. The raw SDK would require hand-written schemas.

```python
from mcp.server.fastmcp import FastMCP
from specdd_mcp.parser import parse_spec as _parse_spec

mcp = FastMCP("specdd")

@mcp.tool()
def parse_spec(
    path: str | None = None,
    content: str | None = None,
    virtual_path: str | None = None,
) -> dict:
    """Parse a SpecDD .sdd file (or raw content) into a structured ParsedSpec.

    Prefer this over Read+regex when you need the parsed sections, tasks,
    or scenarios. Returns a Result envelope with `ok: true|false`.
    """
    result = _parse_spec(path=path, content=content, virtual_path=virtual_path)
    return result.model_dump()
```

### Docstrings ARE the tool description

The docstring becomes Claude's tool description. Write for the agent, not just humans. Always include:
1. What the tool does in one sentence.
2. **When to prefer it over native tools** — this is the "code-better-than-LLM gate" sales pitch.
3. The Result envelope shape hint.

### Always return dicts, never raise

Inside a tool body, catch every exception and convert to `Err`. FastMCP's default exception-to-error-message conversion is opaque; we want Claude to see `error: "PARSE_ERROR"` not `"Tool execution failed"`.

### `repo_root` auto-detection

```python
def find_repo_root(target: Path) -> Path | None:
    target = target.resolve()
    # Prefer .specdd/
    for parent in [target, *target.parents]:
        if (parent / ".specdd").is_dir():
            return parent
    # Fall back to .git/
    for parent in [target, *target.parents]:
        if (parent / ".git").exists():
            return parent
    return None
```

Two passes intentionally: a SpecDD-managed subtree of a larger git monorepo should treat the SpecDD root as `repo_root`, not the outer git root.

### Same-directory spec ordering

A directory containing both `module.sdd` and `feature.sdd` requires a precedence rule (lexicographic would put `feature` before `module`, which is semantically wrong):

```python
LEVEL_PRECEDENCE = {
    "app": 0, "module": 1, "feature": 2, "service": 3, "model": 4,
    "adapter": 5, "api": 6, "component": 7, "job": 8, "event": 9,
    "policy": 10, "custom": 90, "unknown": 99,
}
```

Sort by `(LEVEL_PRECEDENCE[spec.level], spec.path)` — level first, then lexicographic tiebreak.

### POSIX paths everywhere on output

```python
def to_posix(path: str | Path) -> str:
    return str(Path(path).as_posix())
```

Called at every output boundary. Inputs are accepted as-is (Pydantic normalizes); outputs are always `/`-separated.

### Symlink handling

```python
def walk_ancestors(target: Path, root: Path) -> Iterator[tuple[Path, list[str]]]:
    # Yields (directory, warnings) for each level from root down to target.
    # Skips symlinked directories entirely with a warning.
    ...
```

Each yielded warning is later attached to the `Result.warnings` field of the tool response.

### Stdio is the only transport

```python
# __main__.py
import sys
from specdd_mcp.server.app import mcp

def main() -> None:
    mcp.run(transport="stdio")

if __name__ == "__main__":
    main()
```

---

## Test strategy

### Unit (in-process)

- `find_repo_root`: `.specdd` only / `.git` only / both / neither / nested SpecDD inside outer git.
- `to_posix`: known Windows-style inputs.
- `walk_ancestors`: handles symlinks, deeply nested, monorepo guardrail respected.
- Same-dir ordering: synthetic directory with all levels present, asserts canonical order.
- `OUT_OF_SCOPE`: target above repo_root, target on a different drive (mocked).

### Integration (synthetic chains)

`tests/fixtures/chains/simple_3_level/`:

```
.specdd/
app.sdd                              (level=app)
src/
  module.sdd                         (level=module)
  feature/
    feature.sdd                      (level=feature)
    invoice.sdd                      (level=service)
    invoice.ts                       (target)
```

Assertion: `resolve_spec_chain(target="src/feature/invoice.ts")` returns chain in order `[app, module, feature, service]`, `nearest = service`.

### Server-level test (in-process FastMCP client)

```python
# tests/test_server.py
from mcp import ClientSession
from mcp.client.stdio import stdio_client

async def test_parse_spec_through_mcp(tmp_repo):
    async with stdio_client(...) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("parse_spec", {
                "path": "src/feature/invoice.sdd"
            })
            assert result.content[0].type == "text"
            assert '"ok": true' in result.content[0].text
```

Validates the tool is registered correctly, schemas generate, and the JSON shape matches what the docstring claims.

### E2E (manual checklist)

`tests/e2e/README.md` documents:

1. `pip install -e .` in the SpecMCP repo
2. `claude mcp add specdd specdd-mcp` (from a project directory)
3. Open Claude Code in `tests/fixtures/chains/simple_3_level/`
4. Ask: "Use mcp__specdd__resolve_spec_chain on src/feature/invoice.ts and show me the result."
5. Verify: chain has 4 specs in the expected order, nearest is the service spec.

This is not automated yet — it's a smoke test owners run before tagging.

---

## Acceptance criteria

- [ ] `python -m specdd_mcp` starts an MCP server on stdio without errors.
- [ ] `claude mcp add specdd specdd-mcp` registers the server (verified manually).
- [ ] `mcp__specdd__parse_spec` callable from a Claude Code session, returns a valid `ParsedSpec` JSON.
- [ ] `mcp__specdd__resolve_spec_chain` callable from a Claude Code session, returns ordered chain.
- [ ] Symlinks in ancestor paths are skipped with a `warnings` entry, not followed.
- [ ] `OUT_OF_SCOPE` is returned when target is outside repo_root.
- [ ] Auto-detection prefers `.specdd/` over `.git/`.
- [ ] All output paths POSIX (tested via path-normalization unit tests with mocked OS).
- [ ] `mypy --strict` and `ruff` pass.
- [ ] Coverage ≥ 85% for new code (lower bar than PR 1 because some code is hard to unit-test — manual E2E covers the gap).

---

## Risks

| Risk | Mitigation |
|---|---|
| FastMCP API changes between minor versions | Pin `mcp[cli]>=1.0,<2.0`; document version compat in README |
| `claude mcp add` syntax may evolve | Document current command in README with a "Last verified against Claude Code X.Y" line |
| Windows path edge cases not caught by mocks | Run E2E manually on Windows once before tagging the PR |
| Multi-spec same-directory ordering surprises users | Document the precedence rule prominently in README and in `resolve_spec_chain` docstring |

---

## Definition of done

- All criteria met.
- README has a "Running the server" section with concrete shell commands.
- `tests/e2e/README.md` has the manual checklist.
- DESIGN.md §8 marks the "Implementation choices" row for Language → **Resolved: Python 3.10+** and the parser style → **Resolved: regex-per-section**.

---

## Preview of PR 3

The biggest PR by volume. Adds the two highest-value read tools — `list_tasks` and `get_effective_constraints` — including:

- Glob expansion against the live filesystem
- Structured conflict detection (4 kinds)
- Cross-chain rule merging with full provenance

After PR 3, `/specc` can actually do its job. PR 4 then adds writes.

---

## Done — PR 2 retrospective

Status: **complete**. All 10 commits landed; all acceptance criteria met.

### Numbers

| Metric | Target | Actual |
|---|---|---|
| New MCP tools | `parse_spec`, `resolve_spec_chain` | ✅ both registered, both callable via the MCP protocol |
| New tests | comprehensive | **+96** (252 → 348) |
| Coverage | ≥ 85% (lower bar for hard-to-unit-test server code) | **100%** (589 stmts, 134 branches, 0 missed) |
| `mypy --strict` | passes | ✅ 19 source files clean |
| `ruff` | passes | ✅ src + tests clean |
| End-to-end protocol smoke | passes against subprocess | ✅ 8 tests via `stdio_client` + `ClientSession` |
| Hours estimated | ~20 | matched closely |

### Bugs caught / corner cases hit

1. **`anyio` cancel scope across asyncio tasks** — using `@pytest_asyncio.fixture` to wrap `stdio_client + ClientSession` caused `RuntimeError: Attempted to exit cancel scope in a different task` on teardown (assertions passed, fixture cleanup failed). Fix: switch to inline `@asynccontextmanager` so setup and teardown happen in the same test task. The pattern is the lesson here — documented at the top of `tests/test_server.py` to stop the next maintainer from "refactoring to a fixture".
2. **Reverse layering** — initial plan put `paths.py` in `server/`, but `parser/resolve_chain.py` needs it. Parser → server is the wrong direction. Fix: moved `paths.py` to top-level `specdd_mcp/paths.py` so both layers can depend on it without cycles.
3. **macOS-style `_make_repo` helper** — tests passing `tmp_path / "subdir"` to the test helper failed because the helper assumed the directory already existed. Fix: `mkdir(parents=True, exist_ok=True)` defensively.

### What's locked in for downstream PRs

- **`mcp[cli]>=1.0,<2.0`** pinned. FastMCP is the entry point for every future tool.
- **Tool wrapper pattern** — log invocation, try / catch unexpected → `Err`, `model_dump()` return. PR 3-8 tool wrappers reuse this verbatim.
- **`@mcp.tool()` decorator runs at import time** — `__main__.py` imports `server.tools` for the side effect. Adding tools is just adding more decorated functions in `tools.py`.
- **stderr-only logging** with two named loggers. Any new code that writes to stdout is a bug.
- **`paths.find_repo_root`** — `.specdd/` preferred, `.git/` fallback, two-pass walk. Every operation that needs a repo root reuses this.
- **POSIX path normalization at boundaries** — `to_posix`, `to_repo_relative` applied consistently. Output never has backslashes.
- **Same-directory spec ordering** by `SpecLevel` precedence with lexicographic tiebreak. `_LEVEL_PRECEDENCE` table in `resolve_chain.py` is the canonical source.
- **`Result.model_dump()`** is the JSON shape every MCP tool returns. `_extract_payload` helper in `test_server.py` handles both `structuredContent` and `content[0].text` paths defensively for SDK version drift.
- **`tests/e2e/README.md`** — the human-driven release-gate checklist. Auto-tests cover protocol; this covers Claude-Code-in-the-loop reality.

### Architecture as it stands at end of PR 2

```
specdd_mcp/
├── __init__.py
├── __main__.py              Console script + `python -m` entry; calls mcp.run
├── paths.py                 Top-level filesystem util (top-level: parser AND server both depend on it)
├── types.py                 Pydantic models (DESIGN §3)
├── parser/                  Pure: string/bytes → ParsedSpec | SpecChain
│   ├── __init__.py          Public API: parse_spec, resolve_spec_chain
│   ├── parse_spec.py
│   ├── resolve_chain.py
│   ├── lexer.py
│   ├── sections.py
│   ├── bullets.py / text.py / structure.py / tasks.py / scenarios.py
│   └── levels.py
└── server/                  MCP protocol layer: thin wrappers around parser/
    ├── __init__.py          Re-exports the FastMCP singleton
    ├── app.py               `mcp = FastMCP("specdd")`
    ├── logging.py           stderr config + log helpers
    └── tools.py             @mcp.tool()-decorated wrappers
```

PR 3 adds an `operations/` sibling to `parser/` for cross-spec / filesystem-walking work (glob expansion, conflict detection, task scanning).
