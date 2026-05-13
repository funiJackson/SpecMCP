"""Tests for :mod:`specdd_mcp.parser.bullets`."""

from __future__ import annotations

from specdd_mcp.parser.bullets import parse_bullets
from specdd_mcp.parser.lexer import Line


def _lines(*texts: str, start: int = 2) -> list[Line]:
    """Build a list of Line tuples for testing."""
    return [Line(line_no=start + i, text=text) for i, text in enumerate(texts)]


def test_empty_body() -> None:
    assert parse_bullets([]) == []


def test_all_blank_body() -> None:
    assert parse_bullets(_lines("", "  ", "\t")) == []


def test_single_bullet() -> None:
    assert parse_bullets(_lines("  Validate input.")) == ["Validate input."]


def test_multiple_bullets_same_indent() -> None:
    assert parse_bullets(
        _lines(
            "  Validate input.",
            "  Persist after success.",
            "  Normalize errors.",
        )
    ) == [
        "Validate input.",
        "Persist after success.",
        "Normalize errors.",
    ]


def test_continuation_with_deeper_indent_joins_to_previous_bullet() -> None:
    assert parse_bullets(
        _lines(
            "  Validate invoice input",
            "    before it reaches the provider layer.",
        )
    ) == ["Validate invoice input before it reaches the provider layer."]


def test_blank_lines_between_bullets_are_skipped() -> None:
    assert parse_bullets(
        _lines(
            "  Bullet one.",
            "",
            "  Bullet two.",
            "  ",
            "  Bullet three.",
        )
    ) == ["Bullet one.", "Bullet two.", "Bullet three."]


def test_base_indent_is_minimum_across_all_lines() -> None:
    """When the first line is deeper than later lines, the minimum indent of
    any line is the base. A line at the minimum is a bullet; a deeper-indented
    line with no preceding bullet becomes its own bullet (cannot continue
    nothing); a deeper-indented line WITH a preceding bullet is a continuation.
    """
    # Deep first line: no preceding bullet, so it becomes its own bullet even
    # though it's deeper than base (2).
    assert parse_bullets(
        _lines(
            "      deep first",
            "  shallow second",
            "  shallow third",
        )
    ) == ["deep first", "shallow second", "shallow third"]

    # Deep line that DOES have a preceding bullet at the base indent becomes
    # a continuation of that bullet.
    assert parse_bullets(
        _lines(
            "  shallow first",
            "      deep continuation",
            "  shallow second",
        )
    ) == ["shallow first deep continuation", "shallow second"]


def test_deep_continuation_after_shallow_bullet() -> None:
    assert parse_bullets(
        _lines(
            "  bullet one",
            "      deep continuation",
            "        even deeper continuation",
            "  bullet two",
        )
    ) == ["bullet one deep continuation even deeper continuation", "bullet two"]


def test_tab_indentation_is_treated_as_indent() -> None:
    """Tabs count as indent characters (1 char each, not expanded)."""
    assert parse_bullets(
        _lines(
            "\tbullet one",
            "\t\tcontinuation",
            "\tbullet two",
        )
    ) == ["bullet one continuation", "bullet two"]


def test_unicode_content_preserved() -> None:
    assert parse_bullets(_lines("  支持中文", "  与 emoji 🎉")) == [
        "支持中文",
        "与 emoji 🎉",
    ]
