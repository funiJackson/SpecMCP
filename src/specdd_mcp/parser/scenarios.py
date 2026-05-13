"""Parser for Scenario sections.

Each ``Scenario:`` header in the source becomes its own
:class:`~specdd_mcp.parser.sections.DetectedSection`. This parser turns each
such detection into a :class:`ParsedScenario` carrying:

- The scenario name (from the header's inline value, e.g. ``Scenario: invalid
  invoice`` → ``name="invalid invoice"``).
- The list of step lines (Given/When/Then/And lines), with leading indent
  stripped, blank lines removed.
- The original line span (start_line / end_line) from the detector.
"""

from __future__ import annotations

from specdd_mcp.parser.sections import DetectedSection
from specdd_mcp.types import ParsedScenario


def parse_scenarios(sections: list[DetectedSection]) -> list[ParsedScenario]:
    """Convert a list of detected ``Scenario:`` sections into ParsedScenarios.

    Returns scenarios in source order. Each scenario's steps are non-blank
    body lines with leading whitespace stripped.
    """
    return [
        ParsedScenario(
            name=section.inline_value,
            steps=[line.text.strip() for line in section.body_lines if line.text.strip()],
            start_line=section.start_line,
            end_line=section.end_line,
        )
        for section in sections
    ]
