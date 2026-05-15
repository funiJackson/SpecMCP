"""Tests for :mod:`specdd_mcp.parser.bullets`.

After PR 3 C5 the parser returns ``(text, line_no)`` tuples so downstream
``merge.py`` can build ``Constraint`` objects with exact ``path:line``
provenance. Continuation lines anchor at the starting line of their bullet.
"""

from __future__ import annotations

from specdd_mcp.parser.bullets import parse_bullets
from specdd_mcp.parser.lexer import Line


def _lines(*texts: str, start: int = 2) -> list[Line]:
    """Build a list of Line tuples for testing. Defaults to starting at line 2
    (line 1 is conventionally the section header)."""
    return [Line(line_no=start + i, text=text) for i, text in enumerate(texts)]


def test_empty_body() -> None:
    assert parse_bullets([]) == []


def test_all_blank_body() -> None:
    assert parse_bullets(_lines("", "  ", "\t")) == []


def test_single_bullet() -> None:
    assert parse_bullets(_lines("  Validate input.")) == [("Validate input.", 2)]


def test_multiple_bullets_same_indent() -> None:
    assert parse_bullets(
        _lines(
            "  Validate input.",
            "  Persist after success.",
            "  Normalize errors.",
        )
    ) == [
        ("Validate input.", 2),
        ("Persist after success.", 3),
        ("Normalize errors.", 4),
    ]


def test_continuation_with_deeper_indent_joins_to_previous_bullet() -> None:
    """The continuation's text gets merged in, but the bullet keeps its
    starting line — that's where `path:line` provenance points."""
    assert parse_bullets(
        _lines(
            "  Validate invoice input",
            "    before it reaches the provider layer.",
        )
    ) == [("Validate invoice input before it reaches the provider layer.", 2)]


def test_blank_lines_between_bullets_are_skipped() -> None:
    assert parse_bullets(
        _lines(
            "  Bullet one.",
            "",
            "  Bullet two.",
            "  ",
            "  Bullet three.",
        )
    ) == [
        ("Bullet one.", 2),
        ("Bullet two.", 4),
        ("Bullet three.", 6),
    ]


def test_base_indent_is_minimum_across_all_lines() -> None:
    """A line at the minimum indent is a bullet; a deeper-indented line with
    no preceding bullet becomes its own bullet (cannot continue nothing); a
    deeper-indented line WITH a preceding bullet at base depth is a
    continuation, and the bullet keeps the starting line."""
    # Deep first line: no preceding bullet, so it becomes its own bullet
    # even though it's deeper than base (2).
    assert parse_bullets(
        _lines(
            "      deep first",
            "  shallow second",
            "  shallow third",
        )
    ) == [
        ("deep first", 2),
        ("shallow second", 3),
        ("shallow third", 4),
    ]

    # Deep line that DOES have a preceding bullet at the base indent becomes
    # a continuation, anchored at the shallow bullet's line.
    assert parse_bullets(
        _lines(
            "  shallow first",
            "      deep continuation",
            "  shallow second",
        )
    ) == [
        ("shallow first deep continuation", 2),
        ("shallow second", 4),
    ]


def test_deep_continuation_after_shallow_bullet() -> None:
    assert parse_bullets(
        _lines(
            "  bullet one",
            "      deep continuation",
            "        even deeper continuation",
            "  bullet two",
        )
    ) == [
        ("bullet one deep continuation even deeper continuation", 2),
        ("bullet two", 5),
    ]


def test_tab_indentation_is_treated_as_indent() -> None:
    """Tabs count as indent characters (1 char each, not expanded)."""
    assert parse_bullets(
        _lines(
            "\tbullet one",
            "\t\tcontinuation",
            "\tbullet two",
        )
    ) == [
        ("bullet one continuation", 2),
        ("bullet two", 4),
    ]


def test_unicode_content_preserved() -> None:
    assert parse_bullets(_lines("  支持中文", "  与 emoji 🎉")) == [
        ("支持中文", 2),
        ("与 emoji 🎉", 3),
    ]
