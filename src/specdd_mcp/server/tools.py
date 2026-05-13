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

from typing import Any

from specdd_mcp.parser import parse_spec as _parse_spec
from specdd_mcp.parser import resolve_spec_chain as _resolve_spec_chain
from specdd_mcp.server.app import mcp
from specdd_mcp.server.logging import log_tool_invocation, log_tool_result
from specdd_mcp.types import Err


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
