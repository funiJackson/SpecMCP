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


def parse_bullets(body_lines: list[Line]) -> list[tuple[str, int]]:
    """Extract bullets from a list-shaped section's body.

    Returns ``(text, line_no)`` pairs. ``line_no`` is the source line where the
    bullet **started**; continuation lines (deeper indent) get merged into
    the preceding bullet's text but the line stays anchored at the start.

    Rules:

    - Blank lines are skipped.
    - The "base indent" is the minimum indent of any non-blank body line.
    - Lines at the base indent are new bullets.
    - Lines indented deeper than the base are continuations of the most recent
      bullet, joined with a single space.

    Returns an empty list when the body has no non-blank lines.
    """
    non_blank = [line for line in body_lines if line.text.strip()]
    if not non_blank:
        return []

    base_indent = min(_measure_indent(line.text) for line in non_blank)

    bullets: list[tuple[str, int]] = []
    for line in non_blank:
        indent = _measure_indent(line.text)
        content = line.text.strip()
        if indent > base_indent and bullets:
            prev_text, prev_line = bullets[-1]
            bullets[-1] = (prev_text + " " + content, prev_line)
        else:
            bullets.append((content, line.line_no))

    return bullets


def _measure_indent(text: str) -> int:
    """Count leading whitespace characters (tabs and spaces both count as 1)."""
    return len(text) - len(text.lstrip())
