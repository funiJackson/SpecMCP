"""Tests for :mod:`specdd_mcp.parser.structure`."""

from __future__ import annotations

from specdd_mcp.parser.lexer import Line
from specdd_mcp.parser.structure import parse_structure


def _lines(*texts: str) -> list[Line]:
    return [Line(line_no=2 + i, text=text) for i, text in enumerate(texts)]


def test_empty_body() -> None:
    assert parse_structure([]) == []


def test_single_entry() -> None:
    entries = parse_structure(_lines("  lib: Libraries"))
    assert len(entries) == 1
    assert entries[0].path == "lib"
    assert entries[0].description == "Libraries"


def test_multiple_entries() -> None:
    entries = parse_structure(
        _lines(
            "  lib: Libraries",
            "  models: Models",
            "  templates: Project templates",
            "  templates/email: Email templates",
        )
    )
    assert [e.path for e in entries] == ["lib", "models", "templates", "templates/email"]
    assert entries[3].description == "Email templates"


def test_blank_lines_skipped() -> None:
    entries = parse_structure(_lines("  a: A", "", "  b: B"))
    assert len(entries) == 2


def test_lines_without_colon_skipped() -> None:
    """Malformed lines are silently dropped here. validate_spec flags them later."""
    entries = parse_structure(_lines("  good: ok", "  bad line no colon", "  also-good: yes"))
    assert [e.path for e in entries] == ["good", "also-good"]


def test_description_can_contain_colons() -> None:
    """Only the first colon is used as the separator."""
    entries = parse_structure(_lines("  api: HTTP API, see https://example.com"))
    assert entries[0].path == "api"
    assert entries[0].description == "HTTP API, see https://example.com"


def test_path_and_description_stripped() -> None:
    entries = parse_structure(_lines("    lib   :   Libraries   "))
    assert entries[0].path == "lib"
    assert entries[0].description == "Libraries"
