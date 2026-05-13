"""Tests for :mod:`specdd_mcp.parser.sections`.

Covers: header regex matching, multi-word section names, inline values, body
range computation, end-line truncation against trailing whitespace, multiple
``Scenario:`` blocks, unknown sections, indented non-headers, lowercase
non-headers.
"""

from __future__ import annotations

from specdd_mcp.parser.lexer import lex_text
from specdd_mcp.parser.sections import (
    SECTION_HEADER_RE,
    DetectedSections,
    detect_sections,
)
from specdd_mcp.types import Ok


def _detect(source: str) -> DetectedSections:
    """Lex + detect helper. Asserts lexing succeeded."""
    lex_result = lex_text(source)
    assert isinstance(lex_result, Ok)
    return detect_sections(lex_result.data)


# ---------------------------------------------------------------------------
# Header regex
# ---------------------------------------------------------------------------


def test_regex_matches_simple_header() -> None:
    m = SECTION_HEADER_RE.match("Spec: Foo")
    assert m is not None
    assert m.group("name") == "Spec"
    assert m.group("rest") == "Foo"


def test_regex_matches_header_with_no_inline_value() -> None:
    m = SECTION_HEADER_RE.match("Must:")
    assert m is not None
    assert m.group("name") == "Must"
    assert m.group("rest") == ""


def test_regex_matches_multi_word_header() -> None:
    m = SECTION_HEADER_RE.match("Must not:")
    assert m is not None
    assert m.group("name") == "Must not"


def test_regex_matches_can_modify_can_read_depends_on_done_when() -> None:
    for header in ("Can modify:", "Can read:", "Depends on:", "Done when:"):
        m = SECTION_HEADER_RE.match(header)
        assert m is not None, f"expected match for {header!r}"


def test_regex_rejects_lowercase_first_letter() -> None:
    """``must:`` is not a section header — section names start uppercase."""
    assert SECTION_HEADER_RE.match("must:") is None


def test_regex_rejects_indented_line() -> None:
    assert SECTION_HEADER_RE.match("  Must:") is None
    assert SECTION_HEADER_RE.match("\tMust:") is None


def test_regex_rejects_space_before_colon() -> None:
    assert SECTION_HEADER_RE.match("Must :") is None


def test_regex_allows_digits_in_section_name() -> None:
    """User-defined sections may contain digits (e.g. API2:, Section1:)."""
    for header in ("API2:", "Section1:", "Foo123 Bar2:"):
        m = SECTION_HEADER_RE.match(header)
        assert m is not None, f"expected match for {header!r}"


def test_regex_rejects_digit_as_first_character() -> None:
    """A section name must start with an uppercase LETTER, not a digit."""
    assert SECTION_HEADER_RE.match("2ndStep:") is None
    assert SECTION_HEADER_RE.match("123:") is None


def test_regex_allows_inline_value_with_colon() -> None:
    """A spec name containing a colon in the value works because the regex is
    non-greedy on the name part."""
    m = SECTION_HEADER_RE.match("Spec: Foo: Bar")
    assert m is not None
    assert m.group("name") == "Spec"
    assert m.group("rest") == "Foo: Bar"


# ---------------------------------------------------------------------------
# Single-section detection
# ---------------------------------------------------------------------------


def test_single_known_section_with_inline_value() -> None:
    result = _detect("Spec: Invoice Service\n")
    assert "spec" in result.known
    spec_section = result.known["spec"][0]
    assert spec_section.name == "spec"
    assert spec_section.is_known is True
    assert spec_section.header_line == 1
    assert spec_section.inline_value == "Invoice Service"
    assert spec_section.body_lines == []
    assert spec_section.start_line == 1
    assert spec_section.end_line == 1


def test_single_known_section_with_indented_body() -> None:
    result = _detect("Purpose:\n  Coordinate invoice creation.\n")
    purpose = result.known["purpose"][0]
    assert purpose.inline_value == ""
    assert len(purpose.body_lines) == 1
    assert purpose.body_lines[0].text == "  Coordinate invoice creation."
    assert purpose.body_lines[0].line_no == 2
    assert purpose.end_line == 2


def test_section_with_empty_body() -> None:
    result = _detect("Must:\n")
    must = result.known["must"][0]
    assert must.body_lines == []
    assert must.end_line == must.header_line


# ---------------------------------------------------------------------------
# Multiple sections
# ---------------------------------------------------------------------------


def test_multiple_sections_in_order() -> None:
    source = (
        "Spec: X\n"
        "\n"
        "Purpose:\n"
        "  Do a thing.\n"
        "\n"
        "Must:\n"
        "  Always do it.\n"
    )
    result = _detect(source)
    assert set(result.known.keys()) == {"spec", "purpose", "must"}
    assert result.known["spec"][0].header_line == 1
    assert result.known["purpose"][0].header_line == 3
    assert result.known["must"][0].header_line == 6


def test_section_body_extends_to_next_header() -> None:
    source = (
        "Purpose:\n"
        "  Line one.\n"
        "  Line two.\n"
        "  Line three.\n"
        "Must:\n"
        "  Always.\n"
    )
    result = _detect(source)
    purpose = result.known["purpose"][0]
    assert len(purpose.body_lines) == 3
    assert [line.line_no for line in purpose.body_lines] == [2, 3, 4]
    assert purpose.end_line == 4


def test_trailing_blank_lines_in_body_do_not_extend_end_line() -> None:
    """end_line should point at the last non-whitespace body line."""
    source = (
        "Purpose:\n"
        "  Real content.\n"
        "\n"
        "\n"
        "Must:\n"
        "  Always.\n"
    )
    result = _detect(source)
    purpose = result.known["purpose"][0]
    # Body includes the blank lines …
    assert len(purpose.body_lines) == 3
    # … but end_line is the last meaningful line.
    assert purpose.end_line == 2


def test_blank_only_section_end_line_is_header_line() -> None:
    source = "Must:\n\n\nForbids:\n  stripe\n"
    result = _detect(source)
    must = result.known["must"][0]
    assert must.end_line == must.header_line  # entirely blank body


# ---------------------------------------------------------------------------
# Multi-word section names
# ---------------------------------------------------------------------------


def test_must_not_normalizes_to_must_not_literal() -> None:
    result = _detect("Must not:\n  Do bad things.\n")
    assert "must_not" in result.known
    assert result.known["must_not"][0].name == "must_not"


def test_can_modify_can_read_depends_on_done_when_all_normalize() -> None:
    source = (
        "Can modify:\n  a.ts\n"
        "Can read:\n  b.ts\n"
        "Depends on:\n  Repo\n"
        "Done when:\n  All tests pass.\n"
    )
    result = _detect(source)
    assert "can_modify" in result.known
    assert "can_read" in result.known
    assert "depends_on" in result.known
    assert "done_when" in result.known


# ---------------------------------------------------------------------------
# Scenarios and examples (multi-instance sections)
# ---------------------------------------------------------------------------


def test_multiple_scenarios_collected_under_scenarios_key() -> None:
    source = (
        "Scenario: invalid amount\n"
        "  Given x\n"
        "Scenario: missing currency\n"
        "  Given y\n"
        "Scenario: paid invoice\n"
        "  Given z\n"
    )
    result = _detect(source)
    assert "scenarios" in result.known
    assert len(result.known["scenarios"]) == 3
    inline_values = [s.inline_value for s in result.known["scenarios"]]
    assert inline_values == ["invalid amount", "missing currency", "paid invoice"]


def test_scenario_singular_and_plural_both_map_to_scenarios() -> None:
    """``Scenarios:`` is unusual but should not be a parse error."""
    source = "Scenarios:\n  Some content.\n"
    result = _detect(source)
    assert "scenarios" in result.known


def test_multiple_examples_collected() -> None:
    source = "Example:\n  one\nExample:\n  two\n"
    result = _detect(source)
    assert len(result.known["examples"]) == 2


# ---------------------------------------------------------------------------
# Unknown sections
# ---------------------------------------------------------------------------


def test_unknown_section_preserves_header_text() -> None:
    source = "Custom Header:\n  some content\n"
    result = _detect(source)
    assert result.known == {}
    assert len(result.unknown) == 1
    assert result.unknown[0].name == "Custom Header"
    assert result.unknown[0].is_known is False
    assert result.unknown[0].body_lines[0].text == "  some content"


def test_unknown_section_alongside_known() -> None:
    source = "Spec: X\n\nWat:\n  ?\n\nMust:\n  always\n"
    result = _detect(source)
    assert "spec" in result.known
    assert "must" in result.known
    assert len(result.unknown) == 1
    assert result.unknown[0].name == "Wat"


# ---------------------------------------------------------------------------
# Non-headers that look header-ish
# ---------------------------------------------------------------------------


def test_indented_must_in_body_is_not_a_header() -> None:
    """A capitalized word followed by a colon, but indented, belongs to the
    previous section's body."""
    source = "Purpose:\n  Note: this is the purpose body, not a section.\n"
    result = _detect(source)
    assert "purpose" in result.known
    assert result.unknown == []
    purpose = result.known["purpose"][0]
    assert len(purpose.body_lines) == 1
    assert "Note:" in purpose.body_lines[0].text


def test_lowercase_first_letter_is_not_a_header() -> None:
    source = "Purpose:\n  do thing.\n  not a header: not a section either\n"
    result = _detect(source)
    assert result.unknown == []
    assert len(result.known["purpose"][0].body_lines) == 2


# ---------------------------------------------------------------------------
# Empty inputs and edge cases
# ---------------------------------------------------------------------------


def test_empty_source_returns_empty_sections() -> None:
    result = _detect("")
    assert result.known == {}
    assert result.unknown == []


def test_only_blank_lines() -> None:
    result = _detect("\n\n\n")
    assert result.known == {}
    assert result.unknown == []


def test_preamble_lines_before_first_header_are_dropped() -> None:
    """Lines before any header don't belong to any section. They are silently
    dropped here — the orchestrator surfaces 'no Spec: line' separately."""
    source = "leading garbage\nmore garbage\nSpec: X\n  thing\n"
    result = _detect(source)
    assert "spec" in result.known
    assert result.known["spec"][0].header_line == 3


def test_duplicate_known_section_collected_as_list() -> None:
    """Two ``Tasks:`` headers (unusual but legal) are both kept."""
    source = (
        "Tasks:\n"
        "  [ ] one\n"
        "Tasks:\n"
        "  [ ] two\n"
    )
    result = _detect(source)
    assert len(result.known["tasks"]) == 2
    assert result.known["tasks"][0].header_line == 1
    assert result.known["tasks"][1].header_line == 3


# ---------------------------------------------------------------------------
# Inline values
# ---------------------------------------------------------------------------


def test_inline_value_is_stripped() -> None:
    result = _detect("Spec:    Trimmed Name    \n")
    assert result.known["spec"][0].inline_value == "Trimmed Name"


def test_scenario_name_taken_from_inline_value() -> None:
    result = _detect("Scenario: invalid invoice\n  Given x\n")
    scenario = result.known["scenarios"][0]
    assert scenario.inline_value == "invalid invoice"
    assert scenario.body_lines[0].text == "  Given x"
