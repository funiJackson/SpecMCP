"""Lexer: turn a `.sdd` source (path / bytes / text) into a list of lines.

The lexer's job is narrow:

- Decode UTF-8 (with BOM stripping).
- Detect binary inputs early via the NUL-byte heuristic.
- Split into 1-indexed lines without terminators (terminators are PR 4's concern
  via :func:`str.splitlines` ``keepends=True`` in a separate read path).
- Surface filesystem errors with structured ``Err`` results.

It deliberately knows nothing about SpecDD syntax — that's
:mod:`specdd_mcp.parser.sections`' job.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from specdd_mcp.types import Err, Ok

# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


class Line(NamedTuple):
    """One logical line of source. ``line_no`` is 1-indexed.

    ``text`` does NOT include the terminator. Round-trip fidelity for writes is
    PR 4's responsibility; the parser only needs content.
    """

    line_no: int
    text: str


@dataclass(frozen=True)
class LexedFile:
    """Result of lexing a `.sdd` source.

    ``raw`` is the full text with any leading BOM stripped. ``bom_present``
    records whether a BOM was found so PR 4's byte-faithful writer can prepend
    it back on output.
    """

    raw: str
    lines: list[Line]
    bom_present: bool


# Type alias for the result envelope.
LexResult = Ok[LexedFile] | Err


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_UTF8_BOM_BYTES = b"\xef\xbb\xbf"
_UTF8_BOM_CHAR = "﻿"
_NUL_CHAR = "\x00"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def lex_text(text: str) -> LexResult:
    """Lex an already-decoded string.

    Detects character-level BOM at start and strips it. Detects binary content
    via NUL char and returns ``PARSE_ERROR``.
    """
    bom_present = text.startswith(_UTF8_BOM_CHAR)
    if bom_present:
        text = text[1:]
    if _NUL_CHAR in text:
        return Err(
            error="PARSE_ERROR",
            message="content appears to be binary (contains NUL characters)",
            details={"kind": "binary"},
        )
    lines = [
        Line(line_no=i, text=line)
        for i, line in enumerate(text.splitlines(), start=1)
    ]
    return Ok(data=LexedFile(raw=text, lines=lines, bom_present=bom_present))


def lex_bytes(raw: bytes) -> LexResult:
    """Lex raw bytes. Strips byte-level UTF-8 BOM, decodes, then delegates.

    Binary detection runs at the byte level **before** UTF-8 decoding, so files
    like PNG (whose magic bytes happen to be invalid UTF-8 but which also
    contain NULs) are correctly classified as binary rather than as encoding
    errors. Order matters here.

    Errors:

    - ``PARSE_ERROR`` (``details.kind == "binary"``) — NUL bytes detected.
    - ``ENCODING_ERROR`` — body not valid UTF-8 (and no NULs to suggest binary).
    """
    bom_present = raw.startswith(_UTF8_BOM_BYTES)
    body = raw[3:] if bom_present else raw
    # Check for NUL bytes BEFORE attempting decode — most binary formats trip
    # this and we want them classified as binary, not as encoding errors.
    if b"\x00" in body:
        return Err(
            error="PARSE_ERROR",
            message="content appears to be binary (contains NUL bytes)",
            details={"kind": "binary"},
        )
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        return Err(
            error="ENCODING_ERROR",
            message=f"file is not valid UTF-8: {exc.reason}",
            details={"reason": exc.reason, "position": exc.start},
        )
    inner = lex_text(text)
    if isinstance(inner, Err):  # pragma: no cover - unreachable via lex_bytes
        # Defensive: ``lex_text`` only returns Err on NUL chars, and we
        # already screen for NUL bytes above. Kept so that future failure
        # modes added to ``lex_text`` automatically propagate.
        return inner
    # Propagate the byte-level BOM detection (lex_text already stripped any
    # char-level BOM independently; either path may have set the flag).
    merged_bom = bom_present or inner.data.bom_present
    return Ok(
        data=LexedFile(
            raw=inner.data.raw,
            lines=inner.data.lines,
            bom_present=merged_bom,
        ),
        warnings=inner.warnings,
    )


def lex_path(path: Path) -> LexResult:
    """Read a file from disk and lex it.

    Maps filesystem errors to structured ``Err`` results:

    - missing file → ``NOT_FOUND``
    - permission denied / other OS errors → ``IO_ERROR``
    - target is a directory → ``IO_ERROR``
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return Err(
            error="NOT_FOUND",
            message=f"file does not exist: {path}",
            details={"path": str(path)},
        )
    except IsADirectoryError:
        return Err(
            error="IO_ERROR",
            message=f"path is a directory, not a file: {path}",
            details={"path": str(path)},
        )
    except PermissionError as exc:
        return Err(
            error="IO_ERROR",
            message=f"permission denied reading {path}: {exc}",
            details={"path": str(path)},
        )
    except OSError as exc:
        return Err(
            error="IO_ERROR",
            message=f"I/O error reading {path}: {exc}",
            details={"path": str(path)},
        )
    return lex_bytes(raw)
