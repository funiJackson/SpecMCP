"""Parser for list-shaped sections: Owns, Must, Must not, Forbids, etc.

Convention used throughout SpecDD: a list section's body is one bullet per
line. Bullets are unmarked (no leading ``-`` or ``*``) — they are just
indented lines of text. Multi-line wrapping is handled by indenting the
continuation lines deeper than the base bullet indent.

This module turns a section's body lines into a clean ``list[str]``. Per-line
leading whitespace is stripped from each bullet; continuation lines are joined
with a single space.
"""

from __future__ import annotations

from specdd_mcp.parser.lexer import Line


def parse_bullets(body_lines: list[Line]) -> list[str]:
    """Extract bullets from a list-shaped section's body.

    - Blank lines are skipped.
    - The "base indent" is the minimum indent of any non-blank body line.
    - Lines at the base indent are bullets.
    - Lines indented deeper than the base are continuations of the most recent
      bullet, joined with a single space.

    Returns an empty list when the body has no non-blank lines.
    """
    non_blank = [line for line in body_lines if line.text.strip()]
    if not non_blank:
        return []

    base_indent = min(_measure_indent(line.text) for line in non_blank)

    bullets: list[str] = []
    for line in non_blank:
        indent = _measure_indent(line.text)
        content = line.text.strip()
        if indent > base_indent and bullets:
            bullets[-1] += " " + content
        else:
            bullets.append(content)

    return bullets


def _measure_indent(text: str) -> int:
    """Count leading whitespace characters (tabs and spaces both count as 1)."""
    return len(text) - len(text.lstrip())
