# PR 5 — `check_modification_scope` + `validate_spec` (single-file)

Two read tools that close out the `/specc` workflow's missing steps: the pre-edit gate (step 4) and the post-implementation health check (step 8). After this PR, `/specc` runs end-to-end with all of its tool calls implemented.

Lower-risk than PR 4. Mostly composition of existing pieces from PR 2 (chain) and PR 3 (glob expansion).

---

## Scope

### In scope

- `check_modification_scope`: pre-edit gate that classifies proposed files as `allowed` / `out_of_scope`, surfaces `multiple_authorities` when the chain has competing claims, and reports the `authority_source`.
- `validate_spec` **single-file rules only**: 4 errors and 5 warnings (full list below). `check_inheritance: true` is accepted as a parameter but the cross-spec rules are no-ops until PR 7.
- Rule registry pattern so PR 7 can add cross-spec rules by appending to a list.
- Both tools registered with FastMCP.
- E2E in Claude Code that runs the full `/specc` flow against a fixture.

### Out of scope

- Cross-spec validation rules (`DUPLICATE_PARENT_RULE`, `CONFLICTING_INHERITANCE`, `TASK_VIOLATES_MUSTNOT`) — PR 7.
- `list_specs`, `find_ownership_conflicts` — PR 7.
- Slash command installation, skill installation, bootstrap CLI — PR 6.
- `bootstrap_project` MCP tool — PR 8.

---

## Project structure (incremental)

```
src/specdd_mcp/
├── operations/
│   ├── ...                       (from PR 2–4)
│   ├── scope.py                  ← NEW: check_modification_scope orchestration
│   └── validation/
│       ├── __init__.py           ← NEW: rule registry + run_validation entry
│       ├── single_file.py        ← NEW: the 9 single-file rules
│       └── types.py              ← NEW: ValidationIssue model, rule signature

tests/
├── fixtures/
│   ├── scope/                    ← NEW
│   │   ├── single_authority/
│   │   ├── multiple_authorities/
│   │   ├── new_file_in_glob/
│   │   ├── no_spec_coverage/
│   │   └── glob_vs_literal/
│   └── validation/               ← NEW
│       ├── missing_spec_header.sdd
│       ├── invalid_task_state.sdd
│       ├── duplicate_task_id.sdd
│       ├── malformed_section.sdd
│       ├── missing_purpose.sdd
│       ├── unknown_section.sdd
│       ├── empty_section.sdd
│       ├── long_spec.sdd            (> 80 lines)
│       ├── ownership_outside_dir.sdd
│       └── clean.sdd                (passes everything)
├── test_scope.py                 ← NEW
├── test_validation_rules.py      ← NEW (one test class per rule)
└── test_specc_full_flow.py       ← NEW: simulates the 9-step /specc workflow
```

No new dependencies.

---

## Implementation order

| # | Commit | Time |
|---|---|---|
| 1 | `validation/types.py`: `ValidationIssue` Pydantic model, `ValidationRule = Callable[[ParsedSpec], list[ValidationIssue]]` | 1 h |
| 2 | `validation/__init__.py`: `run_validation(spec, *, check_inheritance, repo_root)` registry runner. Cross-spec rules accepted but no-op for PR 5. | 1 h |
| 3 | `validation/single_file.py`: implement the 4 error rules | 3 h |
| 4 | `validation/single_file.py`: implement the 5 warning rules | 3 h |
| 5 | `server/tools.py`: `validate_spec` wrapper. Accepts `path` or `content`. | 2 h |
| 6 | `operations/scope.py`: chain resolution → nearest spec → glob expansion → classify proposed files | 3 h |
| 7 | `operations/scope.py`: multiple-authorities detection | 2 h |
| 8 | `server/tools.py`: `check_modification_scope` wrapper | 1 h |
| 9 | Per-rule fixture + test (one class per rule in `test_validation_rules.py`) | 4 h |
| 10 | `test_scope.py`: 5 scope scenarios | 3 h |
| 11 | `test_specc_full_flow.py`: simulate /specc end-to-end in-process (no Claude, just tool calls) | 2 h |
| 12 | E2E in Claude Code: run /specc against a fixture, verify each step's tool call | 2 h |

**Total: ~27 hours, 3 days.**

---

## Key design decisions

### Rule registry pattern

```python
# operations/validation/single_file.py
from .types import ValidationIssue, ValidationRule

def check_missing_spec_header(spec: ParsedSpec) -> list[ValidationIssue]:
    if not spec.name:
        return [ValidationIssue(
            severity="error",
            code="MISSING_SPEC_HEADER",
            message="No 'Spec:' line found.",
            line=1,
        )]
    return []

SINGLE_FILE_RULES: list[ValidationRule] = [
    check_missing_spec_header,
    check_invalid_task_state,
    check_duplicate_task_id,
    check_malformed_section,
    check_missing_purpose,
    check_unknown_section,
    check_empty_section,
    check_long_spec,
    check_ownership_outside_dir,
]
```

PR 7 just appends to a parallel `CROSS_SPEC_RULES` list. No changes to `run_validation`'s interface.

### Rules implemented in PR 5

| Code | Severity | Triggers when |
|---|---|---|
| `MISSING_SPEC_HEADER` | error | No `Spec:` line in the file. |
| `INVALID_TASK_STATE` | error | A line in `Tasks:` doesn't match the canonical task regex (PR 1 already flags this as a parse warning; here it becomes a validation error). |
| `DUPLICATE_TASK_ID` | error | Two tasks in the same spec share the same `#N`. |
| `MALFORMED_SECTION` | error | A section header followed by content the parser couldn't interpret (e.g. `Structure:` with no `path: description` pairs). |
| `MISSING_PURPOSE` | warning | No `Purpose:` section. Downgraded from v1's `error` per DESIGN.md §5.7 design note. |
| `UNKNOWN_SECTION` | warning | A section name not in the canonical list. SpecDD is intentionally extensible — warning, not error. |
| `EMPTY_SECTION` | warning | A section header with no body content. |
| `LONG_SPEC` | warning | File > 80 lines. Configurable via `max_lines` input parameter. |
| `OWNERSHIP_OUTSIDE_DIRECTORY` | warning | `Owns:` or `Can modify:` contains a path with `..` or starting with `/` (escapes the spec's own directory). |

The 9 rules deliberately stay short. Anything that requires the spec chain (`DUPLICATE_PARENT_RULE`, `CONFLICTING_INHERITANCE`, `TASK_VIOLATES_MUSTNOT`) is PR 7.

### `validate_spec` accepts `check_inheritance: true` even in PR 5

The parameter is exposed and accepted from day 1 so `/specc` step 8 can pass it consistently. In PR 5 the cross-spec branch returns an empty list of issues. In PR 7 it lights up.

This avoids a breaking signature change between PRs.

### `check_modification_scope` reuses chain + glob

```python
def check_modification_scope(
    target: str,
    proposed_files: list[str],
    repo_root: str | None = None,
) -> Result[ScopeReport]:
    chain = resolve_spec_chain(target, repo_root)
    if not chain.ok:
        return chain
    nearest = chain.data.nearest
    if nearest is None:
        return ok(ScopeReport(
            authority_source=None,
            effective_scope=[],
            allowed=[],
            out_of_scope=proposed_files,
            reason="No SpecDD coverage for this target.",
        ))
    write_scope = compute_write_scope(nearest, chain.data, repo_root)
    allowed, out_of_scope = classify(proposed_files, write_scope)
    multiple = detect_multiple_authorities(chain.data, proposed_files)
    return ok(ScopeReport(
        authority_source=nearest.path,
        effective_scope=write_scope,
        allowed=allowed,
        out_of_scope=out_of_scope,
        multiple_authorities=multiple or None,
    ))
```

`compute_write_scope` is extracted out of PR 3's `merge.py` into a shared helper so both `get_effective_constraints` and `check_modification_scope` use it. Single source of truth.

### Multiple-authorities detection

For each proposed file, walk the chain (not just the nearest spec) and check whether more than one spec's `Owns:` / `Can modify:` matches the file (literal or via glob).

```python
def detect_multiple_authorities(
    chain: SpecChain,
    proposed_files: list[str],
) -> list[MultipleAuthority]:
    out: list[MultipleAuthority] = []
    for file in proposed_files:
        claimants = [
            (spec.path, line)
            for spec in chain.chain
            for line in find_matching_lines(spec, file)
        ]
        if len(claimants) > 1:
            for spec_path, line in claimants:
                out.append(MultipleAuthority(spec=spec_path, line=line, file=file))
    return out
```

This is the "two specs both Own the same thing" check that the SpecDD README explicitly warns against. We surface it here rather than refusing to operate.

### New files not yet on disk

A common case: Claude wants to create `src/billing/invoice_payment.ts`, which doesn't exist yet. The file isn't in any glob's `matches` (snapshot is files-only). But it might match the literal pattern in `Owns:` (e.g. `src/billing/*`).

Approach: when classifying, first check matches; if none, fall back to pattern matching against the file's *intended* path. This means new files can be allowed by glob membership even though they don't exist yet. Document in the tool docstring.

### `check_inheritance` runner shape

```python
def run_validation(
    spec: ParsedSpec,
    *,
    check_inheritance: bool = False,
    repo_root: str | None = None,
) -> list[ValidationIssue]:
    issues = []
    for rule in SINGLE_FILE_RULES:
        issues.extend(rule(spec))
    if check_inheritance:
        for rule in CROSS_SPEC_RULES:  # empty in PR 5, populated in PR 7
            issues.extend(rule(spec, repo_root=repo_root))
    return issues
```

Same signature for both. PR 7 just changes which rules are in `CROSS_SPEC_RULES`.

---

## Test strategy

### Per-rule tests

One fixture per rule, one test class per rule:

```python
class TestMissingSpecHeader:
    def test_triggers_when_missing(self, load_fixture):
        spec = parse_spec(content=load_fixture("validation/missing_spec_header.sdd")).data
        issues = run_validation(spec)
        codes = [i.code for i in issues]
        assert "MISSING_SPEC_HEADER" in codes
        assert next(i for i in issues if i.code == "MISSING_SPEC_HEADER").line == 1

    def test_clean_spec_doesnt_trigger(self, load_fixture):
        spec = parse_spec(content=load_fixture("validation/clean.sdd")).data
        issues = run_validation(spec)
        assert not any(i.code == "MISSING_SPEC_HEADER" for i in issues)
```

`clean.sdd` is exercised by every rule class as a negative control — assert no false positives.

### Scope tests

5 scenarios, one fixture directory each:

| Fixture | Setup | Assert |
|---|---|---|
| `single_authority/` | One spec owns `*.ts` | Proposed `.ts` → allowed; `.py` → out_of_scope |
| `multiple_authorities/` | Both module and feature claim `invoice.ts` | `multiple_authorities` populated with both specs |
| `new_file_in_glob/` | Spec owns `src/billing/*`; propose `src/billing/new_file.ts` (doesn't exist) | Allowed by pattern match |
| `no_spec_coverage/` | Target outside any `.sdd` chain | `authority_source: null`, full list in `out_of_scope` |
| `glob_vs_literal/` | One spec owns `src/billing/*`, another owns `src/billing/invoice.ts` | Both surface in `multiple_authorities` |

### Full-flow simulation

`test_specc_full_flow.py` runs the `/specc` 9-step workflow as a sequence of in-process tool calls against a fixture, without going through Claude. Asserts:

1. `get_effective_constraints` returns conflicts=[], non-null write authority.
2. Task selection by `task_line` returns a known task.
3. `check_modification_scope` for a planned file returns `allowed`.
4. `update_task_status` flips `[ ]` to `[x]`.
5. `validate_spec(check_inheritance=true)` returns ok with no new issues.

This is the in-process equivalent of the E2E. Catches regressions where individual tools work but their composition breaks.

### E2E in Claude Code

Append to `tests/e2e/README.md`:

> 1. Open Claude Code in `tests/fixtures/chains/simple_3_level/`.
> 2. Invoke: `/specc implement task #1 in invoice service`
> 3. Verify Claude calls, in order: `get_effective_constraints` → (user confirmation) → `check_modification_scope` → Edit → `update_task_status` → `validate_spec`.
> 4. Verify the file ends up with `[x]` on the right task and no other changes.

This is the **first time the full `/specc` workflow runs end-to-end in a real Claude Code session.**

---

## Acceptance criteria

- [ ] `validate_spec` callable from Claude Code with both `path` and `content` inputs.
- [ ] All 9 single-file rules trigger on their target fixture and don't trigger on `clean.sdd`.
- [ ] `check_inheritance: true` is accepted but adds zero issues in PR 5 (placeholder for PR 7).
- [ ] `check_modification_scope` callable from Claude Code.
- [ ] All 5 scope scenarios produce the documented output.
- [ ] `multiple_authorities` populated when more than one spec in the chain claims a proposed file.
- [ ] `new_files_in_glob` allowed via pattern match even when not yet on disk.
- [ ] Full-flow simulation test passes.
- [ ] E2E checklist: `/specc` runs end-to-end in Claude Code without manual tool selection.
- [ ] `mypy --strict` and `ruff` pass.
- [ ] Coverage ≥ 90% on `operations/scope.py` and `operations/validation/single_file.py`.

---

## Risks

| Risk | Mitigation |
|---|---|
| Rule registry pattern accumulates cruft as we add rules | One file per concern. PR 7 adds `cross_spec.py`. Keep `__init__.py` thin. |
| Glob expansion duplicated between `merge.py` (PR 3) and `scope.py` (PR 5) | Extract `compute_write_scope` to `operations/write_scope.py` and import from both call sites. Done as part of commit 6. |
| New-file-in-glob behavior counter-intuitive (file doesn't exist but is "allowed") | Document explicitly in the tool docstring and the `/specc` body. Allowed means "you may create this here," not "this exists." |
| Multiple-authorities case is rare, easy to forget | One dedicated fixture, one dedicated test. Won't silently regress. |
| `validate_spec` signature exposes a parameter (`check_inheritance`) that's a no-op in PR 5 | Documented in the docstring with "PR 7 will activate cross-spec rules." Avoids breaking change later. |

---

## Definition of done

- All acceptance criteria met.
- README has a "Validating specs" section + a "Running `/specc`" section.
- `tests/e2e/README.md` has the full-flow walkthrough.
- DESIGN.md §5.7 marked **partially implemented (single-file rules only; cross-spec rules deferred to PR 7).**

---

## Preview of PR 6

Distribution & UX, not new tools:

- The minimal `SKILL.md` (already drafted) installed.
- `/specc:bootstrap` slash command authored.
- `specdd-mcp bootstrap` and `specdd-mcp install-commands` CLI subcommands.
- Project initialization flow: empty repo → ready-to-use SpecDD project.

After PR 6, a new user can go from `pip install` to a working SpecDD project in two commands.
