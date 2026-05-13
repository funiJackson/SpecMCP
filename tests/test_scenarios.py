"""Tests for :mod:`specdd_mcp.parser.scenarios`."""

from __future__ import annotations

from specdd_mcp.parser.lexer import Line
from specdd_mcp.parser.scenarios import parse_scenarios
from specdd_mcp.parser.sections import DetectedSection


def _make_scenario_section(
    name: str,
    steps: list[str],
    *,
    header_line: int = 10,
) -> DetectedSection:
    body_lines = [Line(line_no=header_line + 1 + i, text=text) for i, text in enumerate(steps)]
    end_line = body_lines[-1].line_no if body_lines else header_line
    return DetectedSection(
        name="scenarios",
        is_known=True,
        header_line=header_line,
        inline_value=name,
        body_lines=body_lines,
        start_line=header_line,
        end_line=end_line,
    )


def test_empty_sections_list() -> None:
    assert parse_scenarios([]) == []


def test_single_scenario_with_given_when_then() -> None:
    section = _make_scenario_section(
        "invalid invoice amount",
        [
            "  Given an invoice input with amount less than or equal to zero",
            "  When createInvoice is called",
            "  Then validation fails",
        ],
    )
    scenarios = parse_scenarios([section])
    assert len(scenarios) == 1
    s = scenarios[0]
    assert s.name == "invalid invoice amount"
    assert s.steps == [
        "Given an invoice input with amount less than or equal to zero",
        "When createInvoice is called",
        "Then validation fails",
    ]
    assert s.start_line == 10
    assert s.end_line == 13


def test_multiple_scenarios_preserve_order() -> None:
    sections = [
        _make_scenario_section("first", ["  Given a", "  When b"], header_line=10),
        _make_scenario_section("second", ["  Given c"], header_line=14),
        _make_scenario_section("third", ["  Given d", "  Then e"], header_line=17),
    ]
    scenarios = parse_scenarios(sections)
    assert [s.name for s in scenarios] == ["first", "second", "third"]


def test_blank_lines_in_steps_are_dropped() -> None:
    section = _make_scenario_section(
        "blank-tolerant",
        ["  Given a", "", "  When b", "  ", "  Then c"],
    )
    scenarios = parse_scenarios([section])
    assert scenarios[0].steps == ["Given a", "When b", "Then c"]


def test_step_lines_have_leading_indent_stripped() -> None:
    section = _make_scenario_section("indented", ["      Given indented"])
    scenarios = parse_scenarios([section])
    assert scenarios[0].steps == ["Given indented"]


def test_scenario_without_steps() -> None:
    section = _make_scenario_section("empty", [])
    scenarios = parse_scenarios([section])
    assert scenarios[0].steps == []
    # end_line falls back to header_line when body is empty.
    assert scenarios[0].end_line == scenarios[0].start_line


def test_scenario_name_can_contain_punctuation() -> None:
    section = _make_scenario_section(
        "a name: with colon, comma, and (parens)",
        ["  Given x"],
    )
    scenarios = parse_scenarios([section])
    assert scenarios[0].name == "a name: with colon, comma, and (parens)"
