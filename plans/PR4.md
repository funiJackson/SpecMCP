# PR 4 — `update_task_status` (the highest-risk PR)

The first and only write tool in v1. After this PR, `/specc` can close its loop: read constraints → implement → mark task done. Everything else in v1 is read-only.

**This is the highest-risk PR by a wide margin.** It's the only place where the server's bugs land as corruption in user-managed `.sdd` files. Plan accordingly: heavy on preservation tests, content-hash preconditions, atomic writes, and per-file locking.

---

## Scope

### In scope

- `update_task_status` MCP tool, full batch shape from DESIGN.md §5.5.
- Content-hash precondition (`expected_content_hash` required; `STALE_FILE` on mismatch).
- Per-file lock during write (`fcntl` on POSIX, `msvcrt` on Windows; stdlib only, no `filelock` dep).
- Atomic write via temp file + rename.
- Round-trip preservation: line endings, BOM, indentation, task IDs, comments, unrelated lines.
- Three identifier modes: `task_id`, `task_line`, `task_text_prefix`, with `TASK_AMBIGUOUS` (carrying `details.candidates`) for prefix collisions.
- Unified diff in the response.
- New content hash returned for chained calls.
- Content-hashing utility usable by future write tools (`add_task`, etc.).

### Out of scope

- `add_task` (v2): inserting new tasks rather than mutating state.
- `create_spec` (v2): writing whole new spec files.
- Any non-task mutation (changing `Must:` lines, etc.) — not in v1 at all.
- `check_modification_scope` and `validate_spec` — PR 5.
- Cross-spec validation — PR 7.

---

## Project structure (incremental)

```
src/specdd_mcp/
├── operations/
│   ├── ...                    (from PR 3)
│   ├── hashing.py             ← NEW: content_hash helper
│   ├── locks.py               ← NEW: per-file lock context manager
│   └── mutate_tasks.py        ← NEW: the write logic
└── server/
    └── tools.py               ← extended with update_task_status wrapper

tests/
├── fixtures/
│   ├── mutate/                ← NEW
│   │   ├── crlf.sdd
│   │   ├── lf.sdd
│   │   ├── bom.sdd
│   │   ├── tabs.sdd
│   │   ├── deep_indent.sdd
│   │   ├── multibyte.sdd          (emoji + CJK)
│   │   ├── ids.sdd
│   │   ├── adjacent_brackets.sdd  (scenario text with `[ ]`)
│   │   └── multi_line_task.sdd
├── test_hashing.py            ← NEW
├── test_locks.py              ← NEW
├── test_mutate_tasks.py       ← NEW (the big one)
└── test_mutate_e2e.py         ← NEW (through MCP server)
```

No new dependencies.

---

## Implementation order

| # | Commit | Time |
|---|---|---|
| 1 | `hashing.py`: `content_hash(bytes) -> str` (SHA-256 hex). Tests: stability across platforms, empty bytes, large input. | 1 h |
| 2 | `locks.py`: `with file_lock(path):` context manager. POSIX uses `fcntl.flock`, Windows uses `msvcrt.locking`. Test: nested acquire blocks; release on exception. | 3 h |
| 3 | `mutate_tasks.py`: byte-faithful read/write helper (`read_preserving(path)` returns `(bom_present, lines_with_terminators, content_hash)`; `write_atomic(path, bom_present, lines)`). | 3 h |
| 4 | `mutate_tasks.py`: state-symbol surgical edit (`replace_state_in_line(line: str, new_symbol: str) -> str`). Regex limited to first `[ ]`/`[x]`/`[-]`/`[!]`/`[?]` on the line, after any indentation. | 2 h |
| 5 | `mutate_tasks.py`: identifier resolver. Given `ParsedSpec.tasks` and one update spec (id / line / prefix), return the matching task or `TASK_NOT_FOUND` / `TASK_AMBIGUOUS` (with candidates). | 3 h |
| 6 | `mutate_tasks.py`: batch orchestrator. Validates `expected_content_hash`, resolves all identifiers (whole-batch-fails-on-any-failure), applies edits, computes diff, atomic-writes, returns new hash. | 4 h |
| 7 | `server/tools.py`: MCP wrapper. | 1 h |
| 8 | Preservation test fixtures (all in `tests/fixtures/mutate/`) + per-fixture tests asserting byte-level diff scope. | 6 h |
| 9 | Stale-file and concurrency tests. | 2 h |
| 10 | TASK_AMBIGUOUS and ambiguity-candidates tests. | 1 h |
| 11 | E2E in Claude Code: register, get_effective_constraints to find a task, update_task_status to mark it done, verify file changed exactly as expected. | 2 h |
| 12 | README "Modifying specs safely" section + DESIGN.md note. | 1 h |

**Total: ~29 hours, 3–4 days.**

---

## Key design decisions

### Surgical edit, not whole-file rewrite

Two approaches were considered:

| | Whole-file rewrite | Surgical edit |
|---|---|---|
| Mental model | Pure function `text → text` | "Find the byte position; change one byte" |
| Preservation risk | High (any formatting we don't preserve gets normalized) | Low (we only touch the state symbol) |
| Test surface | Big (need to assert every preserved feature) | Small (one regex on one line) |
| Wins on | Conceptual cleanliness | Safety |

**Decision: surgical edit.** The whole-file rewrite is more elegant in code but more dangerous in practice. We commit to never normalizing formatting outside the bracketed state symbol we're explicitly changing.

### Byte-faithful read/write

```python
def read_preserving(path: Path) -> tuple[bool, list[str], str]:
    """
    Read a file with full preservation:
    - returns (bom_present, lines_with_terminators, content_hash)
    - lines_with_terminators uses str.splitlines(keepends=True)
    - content_hash is over the bytes (including BOM if present)
    """
    raw_bytes = path.read_bytes()
    digest = content_hash(raw_bytes)
    bom = raw_bytes.startswith(b"\xef\xbb\xbf")
    body = raw_bytes[3:] if bom else raw_bytes
    text = body.decode("utf-8")
    lines = text.splitlines(keepends=True)
    return bom, lines, digest
```

`splitlines(keepends=True)` is the key: each line retains its terminator (`\r\n`, `\n`, or empty for the final line if unterminated). Joining the list back gives byte-identical content. Our edits replace exactly one character in exactly one line; everything else is preserved by construction.

### Atomic write

```python
def write_atomic(path: Path, bom: bool, lines: list[str]) -> str:
    text = "".join(lines)
    raw = b"\xef\xbb\xbf" + text.encode("utf-8") if bom else text.encode("utf-8")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(raw)
    tmp.replace(path)        # atomic on POSIX, near-atomic on Windows
    return content_hash(raw)
```

Temp file is in the same directory as the target so `rename` is atomic on POSIX.

### Per-file lock

POSIX (`fcntl.flock(LOCK_EX)`) and Windows (`msvcrt.locking(LK_LOCK)`) wrapped behind a single context manager:

```python
@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    # Open a sidecar lock file so we don't hold the actual spec file open.
    lock_path = path.with_suffix(path.suffix + ".lock")
    ...
```

The lock is per-`.sdd`-file. Other operations on other specs are unaffected. The lock is **belt-and-suspenders** alongside the content-hash check; the hash catches editor races, the lock catches in-process races.

### Whole-batch-fails-on-any-failure

DESIGN.md §5.5: "Any single update in the batch fails identification → the whole batch fails. No partial writes."

Implementation: resolve all identifiers first (read-only), THEN apply edits. If any resolution fails, return the error without touching disk. This makes the operation atomic at the semantic level, not just the byte level.

### State-symbol regex

```python
STATE_SYMBOL_RE = re.compile(
    r"^(?P<prefix>\s*\[)(?P<symbol>[ x\-!?])(?P<suffix>\].*)$"
)
```

Anchored to start of line, optional whitespace, literal `[`, exactly one of the five symbols, literal `]`, anything after. Replacement is `prefix + new_symbol + suffix`. The regex deliberately excludes anything fancy — no character classes that might accidentally consume `[ ]` patterns appearing later in scenario text (those aren't on this line, but defensive).

### What about lines like `Given the task [x] is complete`?

Scenario steps can contain literal `[ ]` or `[x]`. Our surgical edit operates on a specific line number (from `ParsedTask.line`, populated by PR 1's parser). The parser only emits `ParsedTask` for lines inside a `Tasks:` section. So we never edit a scenario line by accident — the line number we operate on is provably a task line.

### TASK_AMBIGUOUS candidate shape

```python
{
    "ok": False,
    "error": "TASK_AMBIGUOUS",
    "message": "task_text_prefix matched 3 tasks",
    "details": {
        "candidates": [
            {"line": 42, "id": "#1", "text": "Add validation for currency", "current_state": "open"},
            {"line": 43, "id": "#2", "text": "Add validation for amount", "current_state": "open"},
            ...
        ]
    }
}
```

`/specc` can re-call with `task_line` from the right candidate.

### STALE_FILE shape

```python
{
    "ok": False,
    "error": "STALE_FILE",
    "message": "spec content changed since last read",
    "details": {
        "expected_hash": "abc123...",
        "actual_hash": "def456..."
    }
}
```

`/specc` re-runs `get_effective_constraints`, re-extracts the fresh hash, retries once.

---

## Test strategy

Preservation is the entire game. The test suite is heavy on fixtures.

### Per-feature preservation tests

For each fixture in `tests/fixtures/mutate/`, the test asserts:
1. Read original bytes, hash.
2. Apply a known-good update.
3. Diff the original and new bytes.
4. Assert: only the expected state-symbol byte differs.

This catches any accidental normalization.

| Fixture | What it stress-tests |
|---|---|
| `crlf.sdd` | Windows line endings preserved |
| `lf.sdd` | Unix line endings preserved |
| `bom.sdd` | UTF-8 BOM preserved at file start |
| `tabs.sdd` | Tab indentation preserved on task lines |
| `deep_indent.sdd` | 8-space, mixed-indent tasks preserved |
| `multibyte.sdd` | CJK + emoji in task text preserved (byte offset != char offset) |
| `ids.sdd` | `#1` `#2` IDs preserved through state changes |
| `adjacent_brackets.sdd` | Scenario lines with `[x]`-looking text untouched |
| `multi_line_task.sdd` | Only first line's symbol changes; continuation lines untouched |

### Stale-file test

```python
def test_stale_file_returns_error(tmp_path):
    spec = tmp_path / "a.sdd"
    spec.write_text("Spec: X\n\nTasks:\n  [ ] do thing\n")
    # First read captures hash
    _, _, hash_a = read_preserving(spec)
    # External edit
    spec.write_text("Spec: X\n\nTasks:\n  [ ] do thing\n  [ ] new task\n")
    # Mutation with stale hash fails
    result = update_task_status(
        spec_path=str(spec),
        expected_content_hash=hash_a,
        updates=[{"new_state": "done", "task_line": 4}],
    )
    assert not result.ok
    assert result.error == "STALE_FILE"
    assert result.details["expected_hash"] == hash_a
```

### Concurrency test (manual harness)

Two processes, both holding the same `expected_content_hash`, attempt to update the same file:

- Process A acquires the lock, applies its update, releases.
- Process B blocks on the lock, then sees `STALE_FILE` (because the hash now differs).

Result: one update succeeds, the other gets a clean `STALE_FILE`. No corruption, no silent overwrite. Tested with a small `subprocess` harness in `test_mutate_tasks.py`.

### Whole-batch atomicity test

A batch with two updates where the second has a bad identifier. Assert:
1. Function returns `TASK_NOT_FOUND` / `TASK_AMBIGUOUS`.
2. **File on disk is unchanged.** (Read the bytes back, compare to original.)

### E2E in Claude Code

Append to `tests/e2e/README.md`:

> 1. Open Claude Code in `tests/fixtures/chains/simple_3_level/`.
> 2. Ask: "Get effective constraints for `src/feature/invoice.ts`. Then mark task `#2` in the service spec as done."
> 3. Verify:
>    - `update_task_status` is called with a non-empty `expected_content_hash`.
>    - The file on disk has `[x]` on the right line and `[ ]` everywhere else.
>    - No surrounding lines changed.

---

## Acceptance criteria

- [ ] `update_task_status` callable from Claude Code with batch updates.
- [ ] Every preservation fixture passes a byte-level diff assertion.
- [ ] `expected_content_hash` required; omitting it returns `INVALID_INPUT`.
- [ ] Wrong hash returns `STALE_FILE` with both hashes in `details`.
- [ ] `task_text_prefix` ambiguity returns `TASK_AMBIGUOUS` with full `candidates` list.
- [ ] Whole-batch atomicity verified: failing identifier in a batch leaves the file untouched.
- [ ] Concurrency harness shows one-succeeds-one-stale, no corruption.
- [ ] Per-file lock acquired on POSIX (verified with `fuser`) and Windows (verified with file open semantics).
- [ ] Diff in the response is a valid unified diff parsable by `git apply`.
- [ ] `mypy --strict` and `ruff` pass.
- [ ] Coverage ≥ 95% on `operations/mutate_tasks.py` (higher bar than other PRs because this is the write path).

---

## Risks

| Risk | Mitigation |
|---|---|
| Line-ending or BOM corruption | `splitlines(keepends=True)` plus byte-level diff tests for every line-ending fixture |
| Multi-byte text corruption | Multibyte fixture asserts byte-level identity for every code point outside the edited symbol |
| Windows `rename` non-atomicity if target is open | Document; recommend users close their editors before running `/specc`. v2 could add a retry. |
| `fcntl` semantics differ subtly across BSD/Linux/macOS | Stick to `LOCK_EX` (exclusive); `LOCK_NB` not used in v1 (we want to block, not poll) |
| Stale-hash false positives if user adds whitespace at EOF | Documented: any byte change invalidates the hash. Users opening + saving in an editor will trip it. The fix is to re-run `get_effective_constraints` — cheap. |
| Diff library output differs from `git apply` expectations | Use stdlib `difflib.unified_diff` with explicit `n=3`, `fromfile`/`tofile` set to repo-relative path; test that `git apply --check` accepts it |

---

## Definition of done

- All acceptance criteria met.
- `README.md` has a "Modifying specs safely" section explaining the hash precondition.
- DESIGN.md §9 marks Q1 (task identifier) and Q8 (concurrency) as **Implemented as documented**.
- `tests/e2e/README.md` has the update-task walkthrough.
- One mutation has been performed against a real `.sdd` in a personal project (smoke test outside the test repo) and inspected by hand.

---

## Preview of PR 5

Two read tools that finish out the `/specc` workflow:

- `check_modification_scope` — the pre-edit gate. Tells `/specc` step 4 which proposed files are allowed.
- `validate_spec` (single-file rules only) — the `/specc` step 8 health check.

Lower risk than PR 4. Mostly reuses chain + glob logic from PR 3.
