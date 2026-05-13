"""Parser for single-value text sections: Spec, Platform, Purpose.

A text section's value can appear in two places:

1. Inline on the header line: ``Purpose: Coordinate invoice creation.``
2. As an indented body on subsequent lines.

The parser prefers (1) when present; otherwise it joins non-blank body lines
with a single space. Multi-line semantics (paragraph breaks) are flattened in
v1 — SpecDD treats these fields as short single-sentence summaries.
"""

from __future__ import annotations

from specdd_mcp.parser.sections import DetectedSection


def parse_text(section: DetectedSection) -> str:
    """Extract a single string from a text section.

    Returns the inline value if non-empty, otherwise joins non-blank body
    lines (stripped of indent) with a space. Returns empty string if both
    inline and body are blank.
    """
    if section.inline_value:
        return section.inline_value
    parts = [line.text.strip() for line in section.body_lines if line.text.strip()]
    return " ".join(parts)
