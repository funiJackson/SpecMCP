# PR 3 — `get_effective_constraints` + `list_tasks`

The two highest-value read tools. After this PR, `/specc` can finally do its job — an agent asks "what binds me here?" and "what's left to do?" and gets structured answers in one call each.

**This is the heaviest PR in the v1 sequence.** The merging logic, glob expansion, and four conflict detectors are all novel. Plan for it.

---

## Scope

### In scope

- `list_tasks` — cross-spec task discovery with state / text / id filters and `max_specs` guardrail.
- `get_effective_constraints` — the full merged view from DESIGN.md §3.6, with provenance and structured conflicts.
- Glob expansion utility (POSIX globs against the live filesystem, snapshot semantics).
- Conflict detection: all four kinds (`depends_on_vs_forbids`, `must_vs_must_not`, `duplicate_parent_rule`, `task_violates_must_not`).
- `chain_summary`, `effective_read_scope`, `done_when`, `references` aggregation.
- Bounded repo walks (the `max_specs` guardrail enforced in one place).
- Both tools registered with FastMCP, callable from Claude Code.

### Out of scope

- Write tools — PR 4.
- Pre-edit scope check — PR 5 (`check_modification_scope`).
- `validate_spec` — PR 5.
- `list_specs` / `find_ownership_conflicts` — PR 7.
- Resolving conflicts — never. We detect; the user / `/specc` decides.

---

## Project structure (incremental)

A new module `operations/` separating "things that consume `ParsedSpec`" from "things that produce it" (the parser). Cleaner layering and avoids dumping everything into `parser/`.

```
src/specdd_mcp/
├── parser/                  (from PR 1-2)
├── server/                  (from PR 2)
└── operations/              ← NEW module
    ├── __init__.py
    ├── walks.py             ← bounded .sdd iteration (max_specs)
    ├── tasks.py             ← list_tasks orchestration
    ├── globs.py             ← Owns:/Can modify: pattern expansion
    ├── merge.py             ← chain → EffectiveConstraints (sans conflicts)
    └── conflicts.py         ← the 4 detectors

tests/
├── fixtures/
│   ├── chains_with_conflicts/  ← NEW, one fixture per conflict kind
│   ├── globs/                  ← NEW, files for glob expansion tests
│   └── large_repo/             ← NEW, synthetic 1001-spec for guardrail test
├── test_walks.py            ← NEW
├── test_tasks_op.py         ← NEW
├── test_globs.py            ← NEW
├── test_merge.py            ← NEW
├── test_conflicts.py        ← NEW (one class per detector)
└── test_effective_e2e.py    ← NEW (full pipeline)
```

`server/tools.py` gains two more `@mcp.tool()` wrappers. No new dependencies.

---

## Implementation order

| # | Commit | Time |
|---|---|---|
| 1 | `walks.py`: bounded iterator over `.sdd` files under a scope; trips `TOO_LARGE` when count > `max_specs`. | 2 h |
| 2 | `tasks.py` operation: walk → parse → flatten tasks → filter (state, text, id) | 3 h |
| 3 | `list_tasks` MCP tool wrapper | 1 h |
| 4 | `globs.py`: pattern expansion with POSIX output, snapshot semantics, files-only | 3 h |
| 5 | `merge.py` scaffold: chain → `EffectiveConstraints` with all rule arrays populated, conflicts empty | 4 h |
| 6 | `merge.py`: `effective_write_scope` with pattern + matches + source provenance | 2 h |
| 7 | `merge.py`: `done_when`, `effective_read_scope`, `references`, `chain_summary` | 2 h |
| 8 | `conflicts.py`: `depends_on_vs_forbids` detector | 2 h |
| 9 | `conflicts.py`: `duplicate_parent_rule` detector | 2 h |
| 10 | `conflicts.py`: `task_violates_must_not` detector (warning-quality only) | 2 h |
| 11 | `conflicts.py`: `must_vs_must_not` detector (defensive, low hit rate) | 1 h |
| 12 | `get_effective_constraints` MCP tool wrapper | 1 h |
| 13 | Multi-spec conflict fixtures + per-detector tests | 5 h |
| 14 | Full pipeline integration test on `simple_3_level` and benchmark chains | 3 h |
| 15 | Large-repo guardrail test (1001 `.sdd` files synthetic) | 1 h |
| 16 | Performance smoke (parse + merge a 10-deep chain in < 200ms) | 1 h |
| 17 | E2E in Claude Code session; update README | 2 h |

**Total: ~37 hours, 4–5 focused days.**

---

## Key design decisions

### Layering: `parser/` produces, `operations/` consumes

`parser/parse_spec` and `parser/resolve_chain` return `ParsedSpec` / `SpecChain`. They never touch the filesystem beyond reading the spec files themselves. `operations/` is where filesystem-walking, glob expansion, and cross-spec logic lives. This separation makes the parser trivially testable with pure strings, and makes the operations layer trivially testable with pre-built `ParsedSpec` fixtures.

### `walks.py` is the only place that scans the repo

Every tool that walks the filesystem goes through this helper. It:
- Respects `scope` (filter to a subtree).
- Skips symlinks (warning entry).
- Counts files; trips `TOO_LARGE` at `max_specs`.
- Returns `Iterator[Path]` so callers can `break` early if they want.

Single chokepoint = consistent guardrails across `list_tasks` (now), `list_specs` (PR 7), `find_ownership_conflicts` (PR 7), `/specc:audit` etc.

### Glob expansion semantics

```python
def expand_pattern(
    pattern: str,
    spec_dir: Path,
    repo_root: Path,
) -> list[str]:
    """
    Expand a SpecDD Owns:/Can modify: pattern.
    - Pattern is relative to the spec's own directory.
    - Returns POSIX paths relative to repo_root.
    - Matches files only (no directories).
    - Supports `*`, `**`, `?`.
    - Snapshot at call time — caller refreshes as needed.
    """
```

Implementation: `pathlib.Path.glob` with `recursive=True` when `**` appears. Absolute paths in patterns are not rejected here (`validate_spec` in PR 5 catches them as `OWNERSHIP_OUTSIDE_DIRECTORY` warning); they just won't match anything useful.

### Conflict detection: four detectors, four files of logic

```python
# operations/conflicts.py

def detect_all(chain: list[ParsedSpec]) -> list[Conflict]:
    return (
        detect_depends_vs_forbids(chain)
        + detect_duplicate_parent_rule(chain)
        + detect_task_violates_must_not(chain)
        + detect_must_vs_must_not(chain)
    )
```

#### `depends_on_vs_forbids`

For each `depends_on` entry across the chain, check whether any `forbids` entry in any other chain spec matches. Match = exact case-sensitive equality, **and** substring match (e.g. `Depends on: stripe-node` flagged when ancestor has `Forbids: stripe`). Substring matches emit the conflict with a `details.match_kind: "substring"` in the future; v1 just emits.

#### `duplicate_parent_rule`

For each rule in `must` / `must_not` of a child, check if the byte-identical rule appears in an ancestor's same section. Skip rules in the root spec (no parent to duplicate from). High signal — drift risk.

#### `task_violates_must_not`

For each task in the chain, lowercase the task text, then check if any `must_not` rule (also lowercased, prefix-stripped) is a substring. **High false-positive rate** — a task like "Add validation for unsupported currency" matches a `Must not: Calculate tax` rule never, but matches `Must not: Validate amount` sometimes-incorrectly. Document the caveat in the tool docstring and the slash command treats this kind as advisory, not a hard stop.

#### `must_vs_must_not`

The most semantically rich and the least mechanically detectable. v1 implementation: only flag when a `Must:` rule and a `Must not:` rule are byte-identical after stripping the section name. In practice this hits ~0% of real specs — it's defensive. Future PR could swap in embedding-based detection but that violates the "no LLM in tool" principle, so probably never.

### `chain_summary`

Per DESIGN.md §3.6, just `{ path, level }`. Resist the urge to add `name` here even though it's free — DESIGN is the contract.

### `Constraint` provenance is non-negotiable

Every `Constraint` instance MUST have `line` populated. If a parser can't determine the line, that's a parser bug (caught by PR 1 tests), not a graceful fallback. The merging layer asserts this on every Constraint it constructs.

### `list_tasks` filtering rules

```python
def list_tasks(
    repo_root: str | None = None,
    scope: str | None = None,
    states: list[TaskState] = None,           # default ["open"]
    text_contains: str | None = None,
    task_id: str | None = None,
    include_blocked: bool = False,
    max_specs: int = 1000,
) -> Result[list[TaskWithSource]]:
    ...
```

- `include_blocked: true` adds `"blocked"` and `"needs_decision"` to whatever `states` was passed.
- `text_contains` is case-insensitive substring.
- `task_id` is exact match on the `#N` form.
- Returned tasks include `source: str` (repo-relative spec path) in addition to the standard `ParsedTask` fields.
- Output sorted by `(spec_path, line)` for stable ordering.

---

## Test strategy

### Unit

- `walks.py`: scope filter, symlink skip, guardrail trip on 1001 files (synthetic via mock).
- `globs.py`: literal path, `*`, `**`, `?`, empty match, files vs. directories, snapshot vs. stale.
- Each conflict detector: minimal 2-spec chain demonstrating exactly the targeted kind.

### Integration

- `chains_with_conflicts/` fixtures, one subdirectory per kind:
  - `depends_vs_forbids/` — child says `Depends on: stripe`, parent says `Forbids: stripe`.
  - `duplicate_parent_rule/` — child copies a `Must not` line verbatim.
  - `task_violates_must_not/` — task text mechanically restates a parent rule.
  - `must_vs_must_not/` — byte-identical `Must:` and `Must not:` lines.
- Assert exactly the expected conflicts surface, with correct provenance.

### Pipeline E2E

- Build a 4-level synthetic chain with rules at every level. Call `get_effective_constraints`. Assert every section of `EffectiveConstraints` is populated correctly: line numbers, source paths, write scope expanded, conflicts detected.
- Run against `specdd/benchmark` chains; assert `ok: true` for every reachable target.

### Performance smoke

Build a chain 10 specs deep, ~50 rules per spec. Assert `get_effective_constraints` returns in < 200ms on dev hardware. (Not a regression guard yet; a sanity check that O(N×M) conflict detection isn't pathological at realistic sizes.)

### Claude Code E2E (manual)

Append to `tests/e2e/README.md`:

> 1. Open Claude Code in `tests/fixtures/chains/simple_3_level/`.
> 2. Ask: "Use mcp__specdd__get_effective_constraints on `src/feature/invoice.ts` and tell me what binds me."
> 3. Verify: response includes merged must/must_not with line provenance, expanded write scope, and any conflicts.

---

## Acceptance criteria

- [ ] `list_tasks` callable from Claude Code, filters work as documented, sorted output.
- [ ] `get_effective_constraints` returns a fully populated `EffectiveConstraints`:
  - merged `must`, `must_not`, `forbids`, `depends_on`, `done_when` arrays, each entry carrying `source` + `line`
  - `effective_write_scope` with both `pattern` and `matches`
  - `effective_read_scope`, `references`, `chain_summary` populated
  - `tasks` aggregated across chain with `source`
  - `conflicts` populated when synthetic fixtures inject them
- [ ] All four conflict kinds detected; each has at least one fixture-based test.
- [ ] `TOO_LARGE` returned when `max_specs` exceeded.
- [ ] Glob expansion is files-only, POSIX, snapshot.
- [ ] `mypy --strict` and `ruff` pass.
- [ ] Coverage ≥ 90% on `operations/`.
- [ ] Performance smoke passes (< 200ms for the 10-deep chain).

---

## Risks

| Risk | Mitigation |
|---|---|
| `task_violates_must_not` false positives erode trust in the conflicts surface | Document as warning-quality in both the tool docstring AND the `/specc` body. `/specc` treats it as advisory, not a hard stop. |
| `must_vs_must_not` hit rate is ~0% in practice; tempting to delete | Keep it. It's cheap, the structured field exists in DESIGN, and when it does fire it's a real bug in someone's spec. |
| Conflict detection is O(N×M); pathological at huge specs | Performance smoke is the canary. If real specs trip it, add a fast-path that pre-builds rule sets and uses set intersection. |
| Glob expansion against a moving filesystem | Document snapshot semantics in the tool docstring. Caller refreshes. |
| Layering between `parser/` and `operations/` blurs | Lint rule: nothing in `parser/` may `from specdd_mcp.operations import ...`. Add an import-check to CI. |
| Merging logic accidentally drops a section (e.g. forgets `done_when`) | Coverage-driven: one test per output field, one assertion per inherited rule kind. |

---

## Definition of done

- All acceptance criteria met.
- README has a `get_effective_constraints` walkthrough.
- `tests/e2e/README.md` updated with the new E2E step.
- DESIGN.md §9 marks Q2 (glob expansion) as **Implemented as documented**.
- One short note added to DESIGN.md §3.6: the implemented `must_vs_must_not` detector is byte-identical only; richer detection deferred (and out-of-scope-forever per the no-LLM rule).

---

## Preview of PR 4

The riskiest PR. `update_task_status` writes to files. Heavy emphasis on:

- Content-hash precondition (`STALE_FILE`)
- Round-trip preservation tests: CRLF, BOM, tab indentation, multi-byte text
- Batch updates atomic via single file write
- Per-file lock during write (belt-and-suspenders)

After PR 4, `/specc` can complete its full loop: read → implement → mark done.
