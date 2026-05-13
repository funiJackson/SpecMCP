"""Parser for the Structure section.

Each line in a Structure body has the form ``path: description``. Both
sides are stripped. Lines without a colon are skipped silently — PR 5's
``validate_spec`` flags those as MALFORMED_SECTION.
"""

from __future__ import annotations

from specdd_mcp.parser.lexer import Line
from specdd_mcp.types import StructureEntry


def parse_structure(body_lines: list[Line]) -> list[StructureEntry]:
    """Extract path/description pairs from a Structure section's body.

    Splits each non-blank line on the first ``:``. Lines missing a colon are
    skipped (validate_spec catches them in a later PR). Both fields are
    stripped of surrounding whitespace.
    """
    entries: list[StructureEntry] = []
    for line in body_lines:
        stripped = line.text.strip()
        if not stripped:
            continue
        if ":" not in stripped:
            continue
        path, description = stripped.split(":", 1)
        entries.append(
            StructureEntry(
                path=path.strip(),
                description=description.strip(),
            )
        )
    return entries
