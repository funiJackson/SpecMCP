"""MCP tool wrappers — the surface Claude Code sees over stdio.

Each ``@mcp.tool()``-decorated function in this module is one MCP tool. The
wrapper is intentionally thin:

1. Log the invocation (with truncated inputs) to stderr.
2. Call into the parser/operations layer.
3. Catch *unexpected* exceptions and convert them to an ``Err`` payload —
   our parser already returns ``Err`` for known failure modes, so an
   exception here means a programming bug or an OS error not modeled by
   :data:`~specdd_mcp.types.ErrorCode`.
4. Log the result kind (``ok`` / specific error code) to stderr.
5. Return ``result.model_dump()`` so FastMCP serializes a stable JSON shape.

The module is imported for its side effect: each decorator registers a tool
on the :class:`FastMCP` singleton in :mod:`specdd_mcp.server.app`. Reordering
or deleting imports here changes which tools are exposed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from specdd_mcp.operations.merge import (
    build_effective_constraints as _build_effective_constraints,
)
from specdd_mcp.operations.mutate_tasks import (
    update_task_status as _update_task_status,
)
from specdd_mcp.operations.scope import (
    check_modification_scope as _check_modification_scope,
)
from specdd_mcp.operations.tasks import list_tasks as _list_tasks
from specdd_mcp.operations.validation import run_validation as _run_validation
from specdd_mcp.parser import parse_spec as _parse_spec
from specdd_mcp.parser import resolve_spec_chain as _resolve_spec_chain
from specdd_mcp.server.app import mcp
from specdd_mcp.server.logging import log_tool_invocation, log_tool_result
from specdd_mcp.types import Err, Ok, TaskState, UpdateRequest


@mcp.tool()
def parse_spec(
    path: str | None = None,
    content: str | None = None,
    virtual_path: str | None = None,
) -> dict[str, Any]:
    """Parse a SpecDD `.sdd` file or raw content into a structured ParsedSpec.

    Prefer this over `Read` whenever you need any of:
      - section content (must, must_not, owns, depends_on, forbids, ...)
      - task data (state, id, text, line number, indent)
      - scenario name + steps
      - section line positions for `path:line` provenance
      - unknown-section detection

    The parser handles UTF-8 BOM, encoding errors, binary detection, indentation
    edge cases, and emits a stable JSON shape. Returns a Result envelope:

      Success: {"ok": true, "data": ParsedSpec, "warnings": [...]}
      Failure: {"ok": false, "error": ErrorCode, "message": "...", "details": {...}}

    Provide exactly one of `path` or `content`. When using `content`, pass
    `virtual_path` if you want level inference (e.g. `services/foo.sdd` →
    level=`service`) and clearer error messages.

    Error codes:
      INVALID_INPUT  — both `path` and `content`, or neither
      NOT_FOUND      — `path` does not exist
      IO_ERROR       — read failed (permission, EIO, etc.)
      ENCODING_ERROR — file is not valid UTF-8
      PARSE_ERROR    — binary content detected (details.kind == "binary")
    """
    log_tool_invocation(
        "parse_spec",
        {"path": path, "content": content, "virtual_path": virtual_path},
    )
    try:
        result = _parse_spec(path=path, content=content, virtual_path=virtual_path)
    except Exception as exc:
        log_tool_result("parse_spec", ok=False, error_code="INVALID_INPUT")
        return Err(
            error="INVALID_INPUT",
            message=f"unexpected error in parse_spec: {exc}",
            details={"exception_type": type(exc).__name__},
        ).model_dump()
    error_code = result.error if isinstance(result, Err) else None
    log_tool_result("parse_spec", ok=result.ok, error_code=error_code)
    return result.model_dump()


@mcp.tool()
def resolve_spec_chain(
    target: str,
    repo_root: str | None = None,
) -> dict[str, Any]:
    """Resolve the ordered chain of `.sdd` specs from repo root to `target`.

    This is the operation `/specc` performs at the start of every implementation
    task. Prefer this over `Read` / shell `find` when you need:
      - every spec that governs a target (inheritance, root → leaf order)
      - the "nearest" spec (the most-specific local contract)
      - malformed specs surfaced separately (parse failures don't break the chain)
      - POSIX repo-relative paths for downstream `path:line` provenance

    Multiple specs in one directory are ordered by `SpecLevel` precedence
    (app < module < feature < service < model < adapter < api < component <
    job < event < policy < custom < unknown). Symlinked ancestor directories
    are skipped with warnings.

    Inputs:
      target:    repo-relative path (when `repo_root` is given) or absolute
                 path to a file or directory inside the repo.
      repo_root: absolute path. If omitted, auto-detected by walking up from
                 `target` looking for `.specdd/` (preferred) or `.git/`.

    Returns Result envelope:
      Success: {"ok": true, "data": SpecChain, "warnings": [...]}
      Failure: {"ok": false, "error": ErrorCode, "message": "...", "details": {...}}

    Error codes:
      INVALID_INPUT  — relative `target` without `repo_root`
      NOT_FOUND      — target missing, or no repo root detectable
      OUT_OF_SCOPE   — target resolves outside `repo_root`
    """
    log_tool_invocation(
        "resolve_spec_chain",
        {"target": target, "repo_root": repo_root},
    )
    try:
        result = _resolve_spec_chain(target=target, repo_root=repo_root)
    except Exception as exc:
        log_tool_result(
            "resolve_spec_chain", ok=False, error_code="INVALID_INPUT"
        )
        return Err(
            error="INVALID_INPUT",
            message=f"unexpected error in resolve_spec_chain: {exc}",
            details={"exception_type": type(exc).__name__},
        ).model_dump()
    error_code = result.error if isinstance(result, Err) else None
    log_tool_result("resolve_spec_chain", ok=result.ok, error_code=error_code)
    return result.model_dump()


@mcp.tool()
def list_tasks(
    repo_root: str,
    scope: str | None = None,
    states: list[TaskState] | None = None,
    text_contains: str | None = None,
    task_id: str | None = None,
    include_blocked: bool = False,
    max_specs: int = 1000,
) -> dict[str, Any]:
    """Find tasks across one or many `.sdd` files.

    Prefer this over `Grep` when you need:
      - reliable task-state parsing (`[ ]` vs `[x]` vs `[!]` etc.; grep can't
        distinguish state symbols cleanly from scenario text)
      - structured fields (state, id, line, indent, raw) on every result
      - cross-spec aggregation with monorepo guardrail
      - case-insensitive substring filtering on task text

    Default behavior returns OPEN tasks only. Use `include_blocked=true` to
    also surface `[!]` (blocked) and `[?]` (needs_decision). Pass an
    explicit `states` list for any other combination.

    Inputs:
      repo_root:      Absolute path to the repo root.
      scope:          Optional path to limit the search (absolute or relative
                      to `repo_root`). When pointing at a file, the file's
                      directory is walked.
      states:         Task states to include. Default `["open"]`.
                      Empty list `[]` returns no tasks.
      text_contains:  Case-insensitive substring filter on task text.
      task_id:        Exact match on `#N` id.
      include_blocked: Adds "blocked" and "needs_decision" to whatever
                      `states` was passed.
      max_specs:      Walk-time cap. Default 1000.

    Returns Result envelope. On success, `data` is a list of TaskWithSource
    sorted by `(source, line)`. Each entry has the spec's repo-relative path
    in `source`.

    Error codes:
      NOT_FOUND     — `repo_root` or `scope` does not exist
      OUT_OF_SCOPE  — `scope` resolves outside `repo_root`
      TOO_LARGE     — walk would exceed `max_specs` `.sdd` files
    """
    log_tool_invocation(
        "list_tasks",
        {
            "repo_root": repo_root,
            "scope": scope,
            "states": states,
            "text_contains": text_contains,
            "task_id": task_id,
            "include_blocked": include_blocked,
            "max_specs": max_specs,
        },
    )
    try:
        scope_path = Path(scope) if scope is not None else None
        result = _list_tasks(
            repo_root=Path(repo_root),
            scope=scope_path,
            states=states,
            text_contains=text_contains,
            task_id=task_id,
            include_blocked=include_blocked,
            max_specs=max_specs,
        )
    except Exception as exc:
        log_tool_result("list_tasks", ok=False, error_code="INVALID_INPUT")
        return Err(
            error="INVALID_INPUT",
            message=f"unexpected error in list_tasks: {exc}",
            details={"exception_type": type(exc).__name__},
        ).model_dump()
    error_code = result.error if isinstance(result, Err) else None
    log_tool_result("list_tasks", ok=result.ok, error_code=error_code)
    return result.model_dump()


@mcp.tool()
def get_effective_constraints(
    target: str,
    repo_root: str | None = None,
) -> dict[str, Any]:
    """Return the merged view of every rule binding work on `target`.

    This is **THE** load-bearing tool for `/specc` — it's what the slash
    command calls once at the start of every implementation task to know
    what binds the agent's work.

    Prefer this over `Read`+grep on `.sdd` files whenever you need:
      - merged Must / Must not / Forbids / Depends on across the chain,
        each carrying `source` path + `line` provenance
      - structured `conflicts` the chain disagrees on (four kinds, see below)
      - `effective_write_scope` with glob patterns expanded against the live
        filesystem and `write_authority_source` (the nearest spec granting
        write authority)
      - `effective_read_scope`, `done_when`, `references`, `chain_summary`
      - `tasks` from every spec in the chain with their `source` attached

    Conflict kinds in `conflicts`:
      - `depends_on_vs_forbids` — high signal; `/specc` should STOP.
      - `duplicate_parent_rule` — high signal; drift risk; STOP.
      - `must_vs_must_not`      — high signal; defensive but real; STOP.
      - `task_violates_must_not` — advisory only (high false-positive rate);
                                   surface to user but continue.

    Inputs:
      target:    repo-relative path (when `repo_root` given) or absolute path
                 to a file or directory inside the repo.
      repo_root: absolute path. If omitted, auto-detected by walking up from
                 `target` looking for `.specdd/` (preferred) or `.git/`.

    Returns Result envelope:
      Success: {"ok": true, "data": EffectiveConstraints, "warnings": [...]}
      Failure: {"ok": false, "error": ErrorCode, "message": "...", "details": {...}}

    Error codes (propagated from `resolve_spec_chain`):
      INVALID_INPUT — relative `target` without `repo_root`
      NOT_FOUND     — target missing, or no repo root detectable
      OUT_OF_SCOPE  — target resolves outside `repo_root`
    """
    log_tool_invocation(
        "get_effective_constraints",
        {"target": target, "repo_root": repo_root},
    )
    try:
        chain_result = _resolve_spec_chain(target=target, repo_root=repo_root)
        if isinstance(chain_result, Err):
            log_tool_result(
                "get_effective_constraints",
                ok=False,
                error_code=chain_result.error,
            )
            return chain_result.model_dump()
        constraints = _build_effective_constraints(
            chain_result.data,
            repo_root=Path(chain_result.data.repo_root),
        )
    except Exception as exc:
        log_tool_result(
            "get_effective_constraints",
            ok=False,
            error_code="INVALID_INPUT",
        )
        return Err(
            error="INVALID_INPUT",
            message=f"unexpected error in get_effective_constraints: {exc}",
            details={"exception_type": type(exc).__name__},
        ).model_dump()
    log_tool_result("get_effective_constraints", ok=True)
    return Ok(data=constraints, warnings=chain_result.warnings).model_dump()


@mcp.tool()
def update_task_status(
    spec_path: str,
    expected_content_hash: str,
    updates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Atomically update one or more task states in a single `.sdd` file.

    This is the **only** write tool — use it instead of `Edit` or shell
    redirection whenever you need to flip a task between states. The wrapper
    guarantees:
      - byte-faithful preservation of every other line in the file
      (BOM, CRLF/LF, indentation, multi-byte chars, comments)
      - atomic write via temp file + rename (no torn writes ever visible)
      - per-file cross-process lock to serialize concurrent calls
      - whole-batch atomicity: if **any** update fails to resolve, the file
      is left byte-for-byte unchanged
      - stale-file detection via `expected_content_hash` — supply the SHA-256
      you got from the most recent `parse_spec` / `update_task_status` call,
      and the tool refuses to write if disk has drifted

    Each entry in `updates` identifies one task and the state to set:
      {
        "new_state": "open" | "done" | "skipped" | "blocked" | "needs_decision",
        # provide EXACTLY ONE of:
        "task_id":   "#42",              # exact match on the task's `#N`
        "task_line": 27,                  # 1-indexed line number (safest)
        "task_text_prefix": "Add valid",  # case-sensitive `text.startswith`
      }

    Recommended caller pattern:
      1. `parse_spec(path=...)` → grab `tasks` to find the right task
      2. Pass the file's SHA-256 from your last read as `expected_content_hash`
      3. Build one or more `updates` (mix identifier modes freely)
      4. Call this tool — chain further updates using the returned
         `new_content_hash` to avoid a re-read

    Returns Result envelope:
      Success: {"ok": true, "data": UpdateResult, "warnings": []}
        UpdateResult: {"spec_path", "applied" (per-update before-state + task),
                       "diff" (unified diff), "new_content_hash"}
      Failure: {"ok": false, "error": ErrorCode, "message": "...", "details": {...}}

    Error codes:
      INVALID_INPUT  — empty `updates`, zero/multiple identifiers per entry,
                       or invalid `new_state`
      NOT_FOUND      — `spec_path` does not exist
      ENCODING_ERROR — file is not valid UTF-8
      STALE_FILE     — current SHA-256 ≠ `expected_content_hash`; re-parse
                       and retry (details.expected_hash, details.actual_hash)
      TASK_NOT_FOUND — an identifier in `updates` matched no task
      TASK_AMBIGUOUS — an identifier matched multiple tasks; details.candidates
                       lists `{line, id, text, current_state}` so the caller
                       can retry with `task_line`
    """
    log_tool_invocation(
        "update_task_status",
        {
            "spec_path": spec_path,
            "expected_content_hash": expected_content_hash,
            "updates": updates,
        },
    )
    try:
        update_requests = [UpdateRequest.model_validate(u) for u in updates]
        result = _update_task_status(
            Path(spec_path),
            expected_content_hash=expected_content_hash,
            updates=update_requests,
        )
    except Exception as exc:
        log_tool_result(
            "update_task_status", ok=False, error_code="INVALID_INPUT"
        )
        return Err(
            error="INVALID_INPUT",
            message=f"unexpected error in update_task_status: {exc}",
            details={"exception_type": type(exc).__name__},
        ).model_dump()
    error_code = result.error if isinstance(result, Err) else None
    log_tool_result("update_task_status", ok=result.ok, error_code=error_code)
    return result.model_dump()


@mcp.tool()
def validate_spec(
    path: str | None = None,
    content: str | None = None,
    virtual_path: str | None = None,
    check_inheritance: bool = False,
    max_lines: int = 80,
    repo_root: str | None = None,
) -> dict[str, Any]:
    """Validate one `.sdd` spec against the SpecDD rule set (DESIGN §5.7).

    This is `/specc` step 8 — the post-implementation health check. Prefer it
    over hand-rolled `Read`+grep linting whenever you need structured findings
    with `path:line` provenance.

    Provide exactly one of `path` or `content` (same contract as `parse_spec`).
    A parse-level failure (missing file, bad encoding, binary) is returned as
    the parser's own `Err` — the rule set only runs on a spec that parsed.

    Single-file rules (run always):
      Errors:
        MISSING_SPEC_HEADER  — no `Spec:` line.
        INVALID_TASK_STATE   — a `Tasks:` line uses a non-canonical state symbol.
        DUPLICATE_TASK_ID     — two tasks share the same `#N`.
        MALFORMED_SECTION     — a section has body content the parser couldn't
                                interpret (e.g. `Structure:` with no `path: desc`).
      Warnings:
        MISSING_PURPOSE             — no `Purpose:` section (recommended, not required).
        UNKNOWN_SECTION             — a section name outside the canonical list
                                      (SpecDD is extensible — kept verbatim).
        EMPTY_SECTION               — a known section header with no content.
        LONG_SPEC                   — file exceeds `max_lines` (default 80).
        OWNERSHIP_OUTSIDE_DIRECTORY — an `Owns:`/`Can modify:` pattern escapes
                                      the spec's own subtree (`..` or absolute).

    Inputs:
      path / content: exactly one. `virtual_path` aids level inference and
                      error messages when using `content`.
      check_inheritance: accepted now for forward-compat; the cross-spec rules
                      (DUPLICATE_PARENT_RULE, CONFLICTING_INHERITANCE,
                      TASK_VIOLATES_MUSTNOT) light up in a later PR. Passing
                      `true` today adds zero issues — no breaking signature
                      change when they arrive.
      max_lines:      LONG_SPEC threshold. Default 80.
      repo_root:      reserved for the cross-spec rules; ignored today.

    Returns Result envelope:
      Success: {"ok": true, "data": {"issues": [...], "summary": {"errors", "warnings"}}}
      Failure: parser `Err` (INVALID_INPUT / NOT_FOUND / IO_ERROR /
               ENCODING_ERROR / PARSE_ERROR).

    Each issue: {"severity", "code", "message", "line"?}. An empty `issues`
    list with zero counts means the spec is clean.
    """
    log_tool_invocation(
        "validate_spec",
        {
            "path": path,
            "content": content,
            "virtual_path": virtual_path,
            "check_inheritance": check_inheritance,
            "max_lines": max_lines,
            "repo_root": repo_root,
        },
    )
    try:
        parse_result = _parse_spec(
            path=path, content=content, virtual_path=virtual_path
        )
        if isinstance(parse_result, Err):
            log_tool_result(
                "validate_spec", ok=False, error_code=parse_result.error
            )
            return parse_result.model_dump()
        data = _run_validation(
            parse_result.data,
            check_inheritance=check_inheritance,
            repo_root=Path(repo_root) if repo_root is not None else None,
            max_lines=max_lines,
        )
    except Exception as exc:
        log_tool_result("validate_spec", ok=False, error_code="INVALID_INPUT")
        return Err(
            error="INVALID_INPUT",
            message=f"unexpected error in validate_spec: {exc}",
            details={"exception_type": type(exc).__name__},
        ).model_dump()
    log_tool_result("validate_spec", ok=True)
    return Ok(data=data, warnings=parse_result.warnings).model_dump()


@mcp.tool()
def check_modification_scope(
    target: str,
    proposed_files: list[str],
    repo_root: str | None = None,
) -> dict[str, Any]:
    """Check whether proposed file edits are inside the spec chain's write scope.

    This is `/specc` step 4 — the pre-edit gate. Call it before `Edit`/`Write`
    to confirm the files you're about to touch are governed by the nearest
    spec's `Owns:` / `Can modify:`, and to surface when more than one spec
    claims the same file.

    Two-tier matching per proposed file:
      - existing file → matched against the live-filesystem glob expansion.
      - new file (not yet on disk) → matched against the `Owns:`/`Can modify:`
        pattern itself. "Allowed" then means "you may create this here," not
        "this file exists." This is how a brand-new module file inside an
        owned directory comes back allowed.

    Inputs:
      target:         repo-relative (with `repo_root`) or absolute path to the
                      file/dir the work concerns. Resolved through the same
                      chain walk as `resolve_spec_chain`.
      proposed_files: paths you intend to create or modify. Repo-relative or
                      absolute; normalized to POSIX repo-relative for matching.
      repo_root:      absolute path. If omitted, auto-detected (`.specdd/` or
                      `.git/`).

    Returns Result envelope. On success `data` is a ScopeReport:
      {
        "authority_source":     "<nearest spec granting write authority>" | null,
        "effective_scope":      [WriteScopeEntry, ...],   # the authority's surface
        "allowed":              ["<repo-relative path>", ...],
        "out_of_scope":         ["<path>", ...],
        "multiple_authorities": [{"spec", "line", "file"}, ...] | null,
        "reason":               "<why nothing has authority>" | null
      }

    `authority_source: null` (with a `reason`) means either no SpecDD coverage
    for the target, or coverage that declares no write authority — every
    proposed file lands in `out_of_scope`. A populated `multiple_authorities`
    is the "two specs both Own the same thing" hazard the README warns about:
    surfaced, not blocked.

    Error codes (propagated from `resolve_spec_chain`):
      INVALID_INPUT — relative `target` without `repo_root`
      NOT_FOUND     — target missing, or no repo root detectable
      OUT_OF_SCOPE  — target resolves outside `repo_root`
    """
    log_tool_invocation(
        "check_modification_scope",
        {
            "target": target,
            "proposed_files": proposed_files,
            "repo_root": repo_root,
        },
    )
    try:
        result = _check_modification_scope(
            target=target,
            proposed_files=proposed_files,
            repo_root=repo_root,
        )
    except Exception as exc:
        log_tool_result(
            "check_modification_scope", ok=False, error_code="INVALID_INPUT"
        )
        return Err(
            error="INVALID_INPUT",
            message=f"unexpected error in check_modification_scope: {exc}",
            details={"exception_type": type(exc).__name__},
        ).model_dump()
    error_code = result.error if isinstance(result, Err) else None
    log_tool_result(
        "check_modification_scope", ok=result.ok, error_code=error_code
    )
    return result.model_dump()
