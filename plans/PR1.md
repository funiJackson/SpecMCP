# PR 1 — Parser core (no MCP framework)

Foundation PR. Ships a Python module that turns a `.sdd` file into a `ParsedSpec` object exactly matching DESIGN.md §3.1. No MCP server wiring, no tools beyond `parse_spec`. Callable from a Python REPL.

**Why this is PR 1.** Every other PR depends on this. Locking the parser + data shape contract first prevents downstream churn across all 8 remaining PRs.

---

## Scope

### In scope

- Python package `specdd_mcp` (the top-level package — only the parser is shipped in PR 1, but the import path is the one we'll keep)
- `specdd_mcp.types` — all dataclasses for `ParsedSpec`, `ParsedTask`, `ParsedScenario`, `Result`, `ErrorCode`
- `specdd_mcp.parser` — the parser itself
- Public function: `parse_spec(*, path=None, content=None, virtual_path=None) -> Result[ParsedSpec]`
- All edge cases enumerated in DESIGN.md §5.1
- Pydantic v2 models throughout (JSON-serializable; the MCP layer in PR 2 will reuse them)
- Unit tests with synthetic fixtures
- Integration tests against the [`specdd/benchmark`](https://github.com/specdd/benchmark) corpus
- Type hints everywhere; `mypy --strict` passes
- `ruff` formatting and lint passes
- Coverage ≥ 90% for the parser module

### Out of scope (deferred to later PRs)

- MCP server framework, FastMCP wiring, stdio transport — PR 2
- Any tool other than `parse_spec` (no `resolve_spec_chain`, no `get_effective_constraints`, etc.) — PRs 2–7
- CLI (`specdd-mcp bootstrap`, `install-commands`) — PR 6/8
- Stale-file hash / mtime checking — PR 4 (with `update_task_status`)
- Glob expansion — PR 3 (with `get_effective_constraints`)
- Slash command file installation logic — already drafted, will be reused

---

## Project structure

```
SpecMCP/
├── pyproject.toml
├── README.md
├── DESIGN.md                       (already exists)
├── SKILL.md                        (already exists)
├── commands/
│   └── specc.md                    (already exists)
├── plans/
│   ├── PR1.md                      (this file)
│   └── PR2.md                      (future)
├── src/
│   └── specdd_mcp/
│       ├── __init__.py
│       ├── types.py                # ParsedSpec, ParsedTask, ParsedScenario,
│       │                             SpecChain, Constraint, EffectiveConstraints,
│       │                             Result, ErrorCode, SpecLevel, KnownSection
│       └── parser/
│           ├── __init__.py         # re-exports parse_spec
│           ├── parse_spec.py       # top-level orchestrator
│           ├── lexer.py            # file → list of (line_no, raw) tuples
│           ├── sections.py         # section header detection + range building
│           ├── tasks.py            # task line → ParsedTask
│           ├── scenarios.py        # scenario block → ParsedScenario
│           ├── structure.py        # structure section "path: description"
│           ├── levels.py           # SpecLevel inference from filename
│           └── errors.py           # error construction helpers
└── tests/
    ├── conftest.py
    ├── fixtures/
    │   ├── minimal.sdd
    │   ├── full.sdd
    │   ├── tasks_all_states.sdd
    │   ├── tasks_with_ids.sdd
    │   ├── unknown_sections.sdd
    │   ├── empty.sdd
    │   ├── missing_spec_header.sdd
    │   ├── crlf_line_endings.sdd
    │   ├── utf8_bom.sdd
    │   ├── deep_indentation.sdd
    │   └── malformed/
    │       ├── invalid_task_state.sdd
    │       ├── duplicate_task_id.sdd
    │       └── binary_content.sdd
    ├── benchmark/                  # cloned from specdd/benchmark (gitignored, fetched in conftest)
    ├── test_types.py
    ├── test_lexer.py
    ├── test_sections.py
    ├── test_tasks.py
    ├── test_scenarios.py
    ├── test_levels.py
    ├── test_parse_spec.py          # high-level integration of the orchestrator
    └── test_benchmark.py           # against real specs
```

---

## Implementation order (atomic commits)

Each numbered step is one commit. Times are rough estimates for one focused engineer.

| # | Commit | Time |
|---|---|---|
| 1 | Bootstrap: `pyproject.toml`, `ruff`/`mypy` config, `src/` layout, empty `__init__.py` files | 1 h |
| 2 | `types.py`: every Pydantic model from DESIGN.md §3. No logic. JSON round-trip test. | 2 h |
| 3 | `lexer.py`: open file → strip BOM → split lines preserving line endings → list of `(line_no, raw)`. Handle `ENCODING_ERROR`. | 2 h |
| 4 | `sections.py`: known section detection. Build `Dict[KnownSection, (start, end)]`. Capture unknown sections too. | 3 h |
| 5 | Per-section parsers, in this sub-order: | 6 h total |
|   | — list sections (`Owns`, `Must`, `Must not`, `Forbids`, etc.): trivial bullet extraction | |
|   | — text sections (`Purpose`, `Platform`): join non-empty lines | |
|   | — `Structure`: `path: description` splitting | |
|   | — `tasks.py`: state symbol detection + optional ID + text + indent preservation | |
|   | — `scenarios.py`: `Scenario:` followed by Given/When/Then-style lines | |
| 6 | `levels.py`: infer `SpecLevel` from filename pattern; fall back to `custom`/`unknown` | 1 h |
| 7 | `parse_spec.py` orchestrator: read → lex → detect → parse → assemble `ParsedSpec`. Wrap in `Result`. | 2 h |
| 8 | Edge-case hardening: empty file, missing `Spec:` line, binary content, deeply nested unknown sections | 2 h |
| 9 | Synthetic fixture tests (all in `tests/fixtures/`) | 4 h |
| 10 | Benchmark integration test (clones `specdd/benchmark` in `conftest.py`, parses every `.sdd`) | 2 h |
| 11 | `mypy --strict` polish, `ruff` formatting, README "How the parser works" section | 1 h |

**Total: ~26 hours, ≈ 3–4 focused days.**

---

## Key design decisions inside the parser

### Parser strategy

**Regex-per-section.** SpecDD sections are line-anchored — `^[A-Z][a-z A-Z]+:` is the canonical section-header form. We don't need a real grammar; we need to (a) split the file into section ranges and (b) parse each range with section-specific logic.

This loses zero expressiveness vs. a hand-rolled recursive descent and is ~3× shorter to write.

### Section header detection regex

```python
SECTION_HEADER = re.compile(
    r'^(?P<name>[A-Z][A-Za-z ]+?):\s*(?P<rest>.*)$'
)
```

A line matches if it starts with a capitalized identifier followed by `:`. The `rest` group captures inline content (e.g. `Spec: Invoice Service` vs. just `Must:`).

Two-word section names (`Must not`, `Can modify`, `Can read`, `Depends on`, `Done when`) are normalized via a lookup table:

```python
SECTION_ALIASES: dict[str, KnownSection] = {
    "spec": "spec",
    "platform": "platform",
    "purpose": "purpose",
    "structure": "structure",
    "owns": "owns",
    "can modify": "can_modify",
    "can read": "can_read",
    "references": "references",
    "must": "must",
    "must not": "must_not",
    "depends on": "depends_on",
    "forbids": "forbids",
    "exposes": "exposes",
    "accepts": "accepts",
    "returns": "returns",
    "raises": "raises",
    "handles": "handles",
    "tasks": "tasks",
    "scenario": "scenarios",   # plural in output, singular in source
    "example": "examples",
    "done when": "done_when",
}
```

Anything not in this table → `unknown_sections` (with line numbers, per DESIGN.md §3.1).

### Task parsing regex

```python
TASK_LINE = re.compile(
    r'^(?P<indent>\s*)\[(?P<symbol>[ x\-!?])\]\s*'
    r'(?P<id>#\d+\s+)?'
    r'(?P<text>.+?)\s*$'
)

SYMBOL_TO_STATE = {
    " ": "open",
    "x": "done",
    "-": "skipped",
    "!": "blocked",
    "?": "needs_decision",
}
```

A line in the `Tasks:` section that doesn't match this regex → flagged as `INVALID_TASK_STATE` (an error to be surfaced by `validate_spec` in PR 5; here in PR 1 we just include the raw line as a warning in the parse result).

### Encoding handling

- Open with `encoding='utf-8-sig'` to transparently strip BOM.
- Catch `UnicodeDecodeError` → return `ENCODING_ERROR`.
- No other encodings supported in v1 (DESIGN.md §3.8).

### Multi-line continuations

SpecDD spec is ambiguous about this. Decision for PR 1: **indented lines following a bullet are joined to that bullet with a space.** Real-world specs occasionally wrap; the README examples don't, but the parser should tolerate both.

```text
Must:
  Validate every invoice input
    before it reaches the provider layer.
```

→ becomes one entry: `"Validate every invoice input before it reaches the provider layer."`

A blank line ends a section.

### Result envelope in Python

```python
from typing import Generic, TypeVar, Literal
from pydantic import BaseModel

T = TypeVar("T")

class Ok(BaseModel, Generic[T]):
    ok: Literal[True] = True
    data: T
    warnings: list[str] = []

class Err(BaseModel):
    ok: Literal[False] = False
    error: ErrorCode
    message: str
    details: dict[str, object] = {}

Result = Ok[T] | Err
```

Pydantic v2 discriminated union on `ok`; serializes cleanly to the JSON shape DESIGN.md §3.7 specifies.

---

## Test strategy

### Unit tests (fixture-based)

One fixture file per behavior. Tests assert specific fields on the returned `ParsedSpec`. Example:

```python
# tests/test_tasks.py
def test_tasks_all_states(load_fixture):
    result = parse_spec(content=load_fixture("tasks_all_states.sdd"))
    assert result.ok
    tasks = result.data.tasks
    assert [t.state for t in tasks] == [
        "open", "done", "skipped", "blocked", "needs_decision"
    ]
    assert [t.line for t in tasks] == [10, 11, 12, 13, 14]
```

### Integration test against `specdd/benchmark`

```python
# tests/test_benchmark.py
@pytest.fixture(scope="session")
def benchmark_specs(tmp_path_factory):
    # clone-or-pull the benchmark repo to tests/benchmark/
    ...
    return sorted(Path("tests/benchmark").rglob("*.sdd"))

def test_every_benchmark_spec_parses(benchmark_specs):
    failures = []
    for path in benchmark_specs:
        result = parse_spec(path=str(path))
        if not result.ok:
            failures.append((path, result.error, result.message))
    assert not failures, f"{len(failures)} specs failed to parse:\n" + "\n".join(
        f"  {p}: {e}: {m}" for p, e, m in failures
    )
```

**If any benchmark spec fails to parse, that's a real signal**: either the parser has a gap, or the spec uses a form the README didn't document. Both cases need investigation.

### Coverage

`pytest --cov=specdd_mcp --cov-report=term-missing --cov-fail-under=90`.

### What not to test in PR 1

- Inheritance / chain semantics — PR 2's job.
- Glob expansion — PR 3's job.
- Conflict detection — PR 3's job.
- Validation rules (`MISSING_SPEC_HEADER` as error, etc.) — PR 5's job. PR 1 just parses; it doesn't judge.

---

## Acceptance criteria

- [ ] `from specdd_mcp.parser import parse_spec` works.
- [ ] Calling `parse_spec(path=...)` or `parse_spec(content=..., virtual_path=...)` returns a `Result[ParsedSpec]`.
- [ ] Every field in DESIGN.md §3.1 `ParsedSpec` is populated correctly when present in the source.
- [ ] Every error code in DESIGN.md §3.7 that `parse_spec` can return has at least one test exercising it.
- [ ] Every edge case listed in DESIGN.md §5.1 has at least one test.
- [ ] Every `.sdd` file in `specdd/benchmark` parses with `ok: true`.
- [ ] `mypy --strict src/` exits 0.
- [ ] `ruff check src/ tests/` exits 0.
- [ ] `pytest --cov=specdd_mcp --cov-fail-under=90` passes.
- [ ] `README.md` has a "How the parser works" section pointing readers at the key files.

---

## Dependencies

`pyproject.toml`:

```toml
[project]
name = "specdd-mcp"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
    "pydantic>=2.5",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=4.1",
    "mypy>=1.8",
    "ruff>=0.3",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/specdd_mcp"]
```

Hatchling for build because it's lightweight and PyPI-standard. No FastMCP / mcp-sdk yet — they come in PR 2.

---

## Risks

| Risk | Mitigation |
|---|---|
| SpecDD grammar is informal; some real specs may use unexpected forms | Run benchmark integration test from commit 10 onwards; treat any failure as a parser gap, not a spec bug |
| Whitespace handling — tabs vs. spaces, ragged indentation | Be permissive: normalize at parse time. Document the normalization rules in the parser README. |
| Section ordering — specs may not follow canonical order | Parser is order-tolerant by design (we use a header-detection pass, then per-section dispatch). |
| Multi-line continuation rules not standardized | Decide: indented lines after a bullet continue that bullet; blank line ends section. Document explicitly. |
| Benchmark repo unavailable in CI | `conftest.py` falls back to a vendored snapshot in `tests/benchmark_snapshot.tar.gz` if clone fails. |

---

## Definition of done

- All acceptance criteria above are met.
- Code mergeable to `main`; CI green on at least Python 3.10, 3.11, 3.12.
- `README.md` includes a runnable example:

  ```python
  from specdd_mcp.parser import parse_spec
  result = parse_spec(path="example.sdd")
  if result.ok:
      print(result.data.name, result.data.tasks)
  else:
      print(f"Error {result.error}: {result.message}")
  ```

- DESIGN.md updated if any implementation detail diverged from the spec (and the divergence is intentional; otherwise fix the code).
- A short note in DESIGN.md §8 marking the language/parser-style decisions as "Resolved: Python 3.10+, regex-per-section, Pydantic v2."

---

## What PR 2 picks up

Quick preview so you can sanity-check the seam:

- Wire FastMCP, expose `parse_spec` as the first MCP tool.
- Add `resolve_spec_chain` (uses `parse_spec` for each chain element).
- Add `repo_root` auto-detection (`.specdd/` preferred, `.git/` fallback).
- Add a smoke test: `claude mcp add specdd $(which specdd-mcp)` → call `parse_spec` from a real Claude Code session.

If PR 1's types and Result envelope are clean, PR 2 is mostly framework glue.

---

## Done — PR 1 retrospective

Status: **complete**. All 11 commits landed; all acceptance criteria met.

### Numbers

| Metric | Target | Actual |
|---|---|---|
| MCP tools shipped | `parse_spec` only | ✅ `parse_spec` (+ all 5 PR 1–7 data types in `types.py`) |
| Tests | comprehensive | **252 passing** |
| Coverage | ≥ 90% | **100%** (398 stmts, 86 branches, 0 missed) |
| `mypy --strict` | passes | ✅ passes on all 12 source files |
| `ruff` | passes | ✅ passes on src + tests |
| Benchmark integration | every `specdd/benchmark` spec parses | ✅ **8/8 with 0 warnings** |
| Hours estimated | ~26 | matched closely |

### Bugs caught by the test corpus

1. **Section names didn't allow digits** — caught by `test_many_unknown_sections_do_not_crash` (C8). `Section0:` `Section1:` etc. failed to match the section-header regex. Fixed by widening the regex character class to `[A-Z][A-Za-z0-9]*(?:\s+[A-Za-z][A-Za-z0-9]*)*`.
2. **macOS AppleDouble metadata in benchmark snapshot** — caught by C10's fallback path test. The first tarball included `._foo.sdd` resource forks; the parser correctly classified them as binary. Fix was twofold: rebuild snapshot with `COPYFILE_DISABLE=1` AND add defensive `._*` filter in `_collect_sdd_files`.

### What's locked in for downstream PRs

- `ParsedSpec` shape, every section type wired correctly.
- `Result[T] = Ok[T] | Err` envelope — Pydantic v2 discriminated union.
- 10 `ErrorCode` values, fully tested.
- All paths POSIX-normalized at level inference boundary (defense-in-depth for Windows callers).
- `ParsedTask.indent` + `ParsedTask.raw` preserved — PR 4's surgical writes have what they need.
- Section positions (`start_line`, `end_line`) populated for every known section in every fixture.
- Real-world parser fidelity confirmed against the `specdd/benchmark` TODO app.
