"""Section detector: classify the section headers in a lexed `.sdd` file.

A SpecDD section header is an unindented line of the form ``Name:`` or
``Multi word name: optional inline content``. The detector:

1. Walks the lexed lines, identifying every line that matches the header
   pattern.
2. Normalizes each header name to either a canonical ``KnownSection`` literal
   (e.g. ``"Must not:"`` → ``"must_not"``) or marks it as unknown.
3. Assigns each section a body range: lines after its header up to (but not
   including) the next header, or the end of file.
4. Records a "meaningful" end-line (last non-whitespace body line) so
   ``validate_spec`` can flag ``EMPTY_SECTION`` and so ``positions`` in
   :class:`~specdd_mcp.types.ParsedSpec` point at real content rather than
   trailing whitespace.

No content interpretation lives here. Per-section parsers (tasks, scenarios,
structure, list-of-bullets, etc.) consume :class:`DetectedSection` instances.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from specdd_mcp.parser.lexer import LexedFile, Line
from specdd_mcp.types import KnownSection

# Section header pattern:
#   - anchored to start of line (NO leading whitespace — indented lines are body)
#   - one or more words separated by single spaces
#   - each word starts with a letter; subsequent characters may be alphanumeric
#     (so user-defined sections like ``API2:`` or ``Section1:`` work)
#   - the FIRST word starts with an uppercase letter (so "must:" is not a header
#     and ``2ndStep:`` is not either)
#   - subsequent words may start with either case (handles "Can read" AND
#     hypothetical "Custom Section")
#   - immediately followed by ``:`` (no space before the colon)
#   - optionally followed by inline content on the same line
SECTION_HEADER_RE = re.compile(
    r"^(?P<name>[A-Z][A-Za-z0-9]*(?:\s+[A-Za-z][A-Za-z0-9]*)*):\s*(?P<rest>.*)$"
)


# Maps the lowercased detected header (e.g. ``"must not"``) to the canonical
# ``KnownSection`` literal. Both ``Scenario`` (singular, from source) and
# ``Scenarios`` (plural) map to ``"scenarios"``. Same for ``Example`` /
# ``Examples``. Section header matching is case-insensitive on the lookup
# key — the detector lowercases before lookup.
_KNOWN_SECTIONS: dict[str, KnownSection] = {
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
    "scenario": "scenarios",
    "scenarios": "scenarios",
    "example": "examples",
    "examples": "examples",
    "done when": "done_when",
}


@dataclass(frozen=True)
class DetectedSection:
    """One section detected in the source.

    Attributes:
        name: For known sections, the canonical ``KnownSection`` literal (e.g.
            ``"must_not"``). For unknown sections, the raw header text as
            written (e.g. ``"Custom Header"``).
        is_known: Whether ``name`` is one of the canonical SpecDD sections.
        header_line: 1-indexed line number of the header.
        inline_value: Content on the same line as the header, after the
            colon, stripped. Empty when the header line has no trailing
            content. Used by single-value sections like ``Spec: Foo`` and
            ``Scenario: invalid invoice``.
        body_lines: Lines appearing between this header and the next header
            (or end of file). Whitespace and blank lines are preserved
            verbatim; per-section parsers decide what to ignore.
        start_line: Same as ``header_line``.
        end_line: 1-indexed line number of the last non-whitespace body line,
            or ``header_line`` if the section is empty / blank-only.
    """

    name: str
    is_known: bool
    header_line: int
    inline_value: str
    body_lines: list[Line]
    start_line: int
    end_line: int


@dataclass
class DetectedSections:
    """Output of :func:`detect_sections`.

    ``known`` maps a canonical ``KnownSection`` literal to a list of detections.
    Most sections appear at most once and will have a single-element list. The
    ``scenarios`` and ``examples`` keys commonly carry multiple detections
    because SpecDD specs typically contain multiple ``Scenario:`` headers, each
    its own section. A duplicate detection of a normally-single section (e.g.
    two ``Tasks:`` headers) is collected here without warning — the parser
    orchestrator surfaces it.

    ``unknown`` lists every header whose name isn't in the canonical table,
    preserving the source order.
    """

    known: dict[KnownSection, list[DetectedSection]] = field(default_factory=dict)
    unknown: list[DetectedSection] = field(default_factory=list)


def detect_sections(lexed: LexedFile) -> DetectedSections:
    """Classify every section header in the source and compute body ranges.

    This function is total — it never fails. Malformed inputs (no headers,
    binary-looking content, etc.) are caught upstream by the lexer. The
    parser orchestrator is responsible for treating ``empty known`` as an
    abnormal case.
    """
    # First pass: find every header line.
    headers: list[_Header] = []
    for line in lexed.lines:
        match = SECTION_HEADER_RE.match(line.text)
        if match:
            raw_name = match.group("name")
            normalized = " ".join(raw_name.lower().split())
            inline_value = match.group("rest").strip()
            headers.append(
                _Header(
                    line_no=line.line_no,
                    raw_name=raw_name,
                    normalized=normalized,
                    is_known=normalized in _KNOWN_SECTIONS,
                    inline_value=inline_value,
                )
            )

    # Second pass: assign each header a body slice and build DetectedSection.
    result = DetectedSections()
    for idx, header in enumerate(headers):
        next_header_line_no = headers[idx + 1].line_no if idx + 1 < len(headers) else None
        body_lines = [
            line
            for line in lexed.lines
            if line.line_no > header.line_no
            and (next_header_line_no is None or line.line_no < next_header_line_no)
        ]
        end_line = _meaningful_end_line(header.line_no, body_lines)
        if header.is_known:
            canonical = _KNOWN_SECTIONS[header.normalized]
            section = DetectedSection(
                name=canonical,
                is_known=True,
                header_line=header.line_no,
                inline_value=header.inline_value,
                body_lines=body_lines,
                start_line=header.line_no,
                end_line=end_line,
            )
            result.known.setdefault(canonical, []).append(section)
        else:
            section = DetectedSection(
                name=header.raw_name,
                is_known=False,
                header_line=header.line_no,
                inline_value=header.inline_value,
                body_lines=body_lines,
                start_line=header.line_no,
                end_line=end_line,
            )
            result.unknown.append(section)

    return result


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Header:
    """First-pass result: a header line with classification."""

    line_no: int
    raw_name: str
    normalized: str
    is_known: bool
    inline_value: str


def _meaningful_end_line(header_line: int, body: list[Line]) -> int:
    """Return the line number of the last body line with non-whitespace content.

    Falls back to ``header_line`` when the section has no body or its body is
    entirely whitespace. This keeps ``positions[section].end_line`` pointing at
    real content rather than trailing blanks that belong to "the gap between
    sections."
    """
    for line in reversed(body):
        if line.text.strip():
            return line.line_no
    return header_line
