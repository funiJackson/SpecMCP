"""Tests for :mod:`specdd_mcp.parser.text`."""

from __future__ import annotations

from specdd_mcp.parser.lexer import Line
from specdd_mcp.parser.sections import DetectedSection
from specdd_mcp.parser.text import parse_text


def _make_section(inline: str = "", body: list[str] | None = None) -> DetectedSection:
    body_lines: list[Line] = [
        Line(line_no=2 + i, text=text) for i, text in enumerate(body or [])
    ]
    end_line = body_lines[-1].line_no if body_lines else 1
    return DetectedSection(
        name="purpose",
        is_known=True,
        header_line=1,
        inline_value=inline,
        body_lines=body_lines,
        start_line=1,
        end_line=end_line,
    )


def test_inline_value_takes_precedence() -> None:
    section = _make_section(inline="Coordinate invoice creation.", body=["  ignored body"])
    assert parse_text(section) == "Coordinate invoice creation."


def test_body_used_when_inline_empty() -> None:
    section = _make_section(inline="", body=["  Coordinate invoice creation."])
    assert parse_text(section) == "Coordinate invoice creation."


def test_multi_line_body_joined_with_spaces() -> None:
    section = _make_section(
        body=[
            "  Coordinate invoice creation.",
            "  Persist after success.",
        ]
    )
    assert parse_text(section) == "Coordinate invoice creation. Persist after success."


def test_blank_lines_in_body_ignored() -> None:
    section = _make_section(body=["  First.", "", "  Second."])
    assert parse_text(section) == "First. Second."


def test_empty_inline_and_empty_body_returns_empty_string() -> None:
    section = _make_section(inline="", body=[])
    assert parse_text(section) == ""


def test_body_only_blank_lines() -> None:
    section = _make_section(body=["", "  ", "\t"])
    assert parse_text(section) == ""
