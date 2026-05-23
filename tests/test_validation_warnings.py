"""Per-rule tests for the 5 warning-severity validation rules (PR 5 C4).

One test class per rule, mirroring ``test_validation_errors.py``. Each
class covers:

  1. The input that should trigger the rule fires it with the right
     line number (or ``None`` for whole-file findings).
  2. A clean spec doesn't trigger it (negative control).
  3. At least one edge case specific to the rule (e.g. an empty
     ``Purpose:`` is EMPTY_SECTION, not MISSING_PURPOSE; a section with
     unparseable body is MALFORMED, not EMPTY).

Inputs are inline strings — keeps the C4 commit self-contained until the
per-rule fixture files arrive in C9.
"""

from __future__ import annotations

from specdd_mcp.operations.validation.single_file import (
    DEFAULT_MAX_LINES,
    check_empty_section,
    check_long_spec,
    check_malformed_section,
    check_missing_purpose,
    check_ownership_outside_directory,
    check_unknown_section,
)
from specdd_mcp.parser.parse_spec import parse_spec
from specdd_mcp.types import Ok, ParsedSpec


def _parsed(content: str) -> ParsedSpec:
    result = parse_spec(content=content)
    assert isinstance(result, Ok), f"parse failed: {result}"
    return result.data


# ===========================================================================
# MISSING_PURPOSE
# ===========================================================================


class TestMissingPurpose:
    """Absent ``Purpose:`` is a recommendation miss (warning), not a hard
    error — and must be distinguished from a present-but-blank purpose."""

    def test_fires_when_no_purpose_section(self) -> None:
        spec = _parsed("Spec: X\nMust:\n  Validate.\n")
        issues = check_missing_purpose(spec)
        assert len(issues) == 1
        assert issues[0].severity == "warning"
        assert issues[0].code == "MISSING_PURPOSE"
        # Whole-file absence — no line to point at.
        assert issues[0].line is None

    def test_does_not_fire_when_purpose_present(self) -> None:
        spec = _parsed("Spec: X\nPurpose: Coordinate invoices.\n")
        assert check_missing_purpose(spec) == []

    def test_does_not_fire_for_empty_purpose_header(self) -> None:
        """An empty ``Purpose:`` parses to ``""`` (not ``None``), so it's
        EMPTY_SECTION territory, not MISSING_PURPOSE. The two rules must
        not both fire on the same defect."""
        spec = _parsed("Spec: X\nPurpose:\n")
        assert check_missing_purpose(spec) == []


# ===========================================================================
# UNKNOWN_SECTION
# ===========================================================================


class TestUnknownSection:
    """SpecDD is extensible — a non-canonical section is a warning, never
    an error, and is preserved verbatim by the parser."""

    def test_fires_on_unknown_section(self) -> None:
        spec = _parsed(
            "Spec: X\n"
            "\n"
            "Roadmap:\n"
            "  ship v2\n"
        )
        issues = check_unknown_section(spec)
        assert len(issues) == 1
        assert issues[0].severity == "warning"
        assert issues[0].code == "UNKNOWN_SECTION"
        assert issues[0].line == 3
        assert "Roadmap" in issues[0].message

    def test_fires_once_per_unknown_section(self) -> None:
        spec = _parsed(
            "Spec: X\n"
            "\n"
            "Roadmap:\n"
            "  later\n"
            "\n"
            "Glossary:\n"
            "  terms\n"
        )
        issues = check_unknown_section(spec)
        assert len(issues) == 2
        assert [i.line for i in issues] == [3, 6]
        assert {i.code for i in issues} == {"UNKNOWN_SECTION"}

    def test_does_not_fire_for_all_canonical_sections(self) -> None:
        spec = _parsed(
            "Spec: X\n"
            "Purpose: Greet.\n"
            "Must:\n"
            "  Validate.\n"
        )
        assert check_unknown_section(spec) == []


# ===========================================================================
# EMPTY_SECTION
# ===========================================================================


class TestEmptySection:
    """A known header with no content. Must avoid double-firing with
    MISSING_SPEC_HEADER (empty ``Spec:``) and MALFORMED_SECTION (header
    with unparseable body)."""

    def test_fires_on_empty_list_section(self) -> None:
        spec = _parsed("Spec: X\nPurpose: Greet.\n\nMust:\n")
        issues = check_empty_section(spec)
        codes = [(i.code, i.line) for i in issues]
        assert ("EMPTY_SECTION", 4) in codes
        empties = [i for i in issues if i.code == "EMPTY_SECTION"]
        assert all(i.severity == "warning" for i in empties)

    def test_fires_on_empty_purpose(self) -> None:
        spec = _parsed("Spec: X\nPurpose:\n")
        issues = check_empty_section(spec)
        assert any(i.code == "EMPTY_SECTION" and i.line == 2 for i in issues)

    def test_does_not_fire_on_populated_sections(self) -> None:
        spec = _parsed(
            "Spec: X\n"
            "Purpose: Greet.\n"
            "Must:\n"
            "  Validate input.\n"
        )
        assert check_empty_section(spec) == []

    def test_does_not_fire_on_spec_header(self) -> None:
        """An empty/blank ``Spec:`` is MISSING_SPEC_HEADER's job — this
        rule skips the ``spec`` section entirely to avoid reporting one
        defect as both an error and a warning."""
        spec = _parsed("Spec:\nPurpose: Greet.\n")
        assert all(
            not (i.code == "EMPTY_SECTION" and i.line == 1)
            for i in check_empty_section(spec)
        )

    def test_does_not_fire_on_malformed_structure(self) -> None:
        """``Structure:`` with body the parser can't interpret has
        content (``end_line > start_line``) → MALFORMED, not EMPTY. The
        two rules must not double-fire."""
        spec = _parsed(
            "Spec: X\n"
            "Purpose: Greet.\n"
            "Structure:\n"
            "  no colon here\n"
        )
        empty = check_empty_section(spec)
        malformed = check_malformed_section(spec)
        assert not any(i.code == "EMPTY_SECTION" for i in empty)
        assert any(i.code == "MALFORMED_SECTION" for i in malformed)

    def test_inline_value_section_is_not_empty(self) -> None:
        """``Platform: web`` carries its content on the header line
        (``end_line == start_line``) but is not empty."""
        spec = _parsed("Spec: X\nPlatform: web\nPurpose: Greet.\n")
        assert all(
            not (i.code == "EMPTY_SECTION" and i.line == 2)
            for i in check_empty_section(spec)
        )


# ===========================================================================
# LONG_SPEC
# ===========================================================================


class TestLongSpec:
    """File-length warning. Strictly greater-than the threshold, and the
    threshold is overridable for callers (and tests)."""

    def test_fires_when_over_default_threshold(self) -> None:
        content = "Spec: X\n" + "".join(
            f"  line {n}\n" for n in range(DEFAULT_MAX_LINES + 5)
        )
        spec = _parsed(content)
        assert spec.line_count > DEFAULT_MAX_LINES
        issues = check_long_spec(spec)
        assert len(issues) == 1
        assert issues[0].severity == "warning"
        assert issues[0].code == "LONG_SPEC"
        assert issues[0].line is None
        assert str(spec.line_count) in issues[0].message

    def test_does_not_fire_at_exactly_threshold(self) -> None:
        """Boundary: a spec of exactly ``max_lines`` lines passes
        (strict ``>``)."""
        content = "".join(f"line {n}\n" for n in range(DEFAULT_MAX_LINES))
        spec = _parsed(content)
        assert spec.line_count == DEFAULT_MAX_LINES
        assert check_long_spec(spec) == []

    def test_does_not_fire_for_short_spec(self) -> None:
        spec = _parsed("Spec: X\nPurpose: Greet.\n")
        assert check_long_spec(spec) == []

    def test_respects_overridden_max_lines(self) -> None:
        spec = _parsed("Spec: X\nPurpose: Greet.\nMust:\n  Validate.\n")
        # Default 80 wouldn't fire; a tight override does.
        assert check_long_spec(spec) == []
        issues = check_long_spec(spec, max_lines=2)
        assert len(issues) == 1
        assert "> 2" in issues[0].message


# ===========================================================================
# OWNERSHIP_OUTSIDE_DIRECTORY
# ===========================================================================


class TestOwnershipOutsideDirectory:
    """``Owns:`` / ``Can modify:`` patterns that reach outside the spec's
    own subtree (absolute or ``..``) are flagged with their source line."""

    def test_fires_on_parent_traversal_in_owns(self) -> None:
        spec = _parsed(
            "Spec: X\n"
            "\n"
            "Owns:\n"
            "  ../sibling/*.ts\n"
        )
        issues = check_ownership_outside_directory(spec)
        assert len(issues) == 1
        assert issues[0].severity == "warning"
        assert issues[0].code == "OWNERSHIP_OUTSIDE_DIRECTORY"
        assert issues[0].line == 4
        assert "../sibling/*.ts" in issues[0].message

    def test_fires_on_absolute_path(self) -> None:
        spec = _parsed(
            "Spec: X\n"
            "\n"
            "Owns:\n"
            "  /etc/passwd\n"
        )
        issues = check_ownership_outside_directory(spec)
        assert len(issues) == 1
        assert "absolute" in issues[0].message

    def test_fires_in_can_modify_too(self) -> None:
        spec = _parsed(
            "Spec: X\n"
            "\n"
            "Can modify:\n"
            "  src/ok.ts\n"
            "  ../escape.ts\n"
        )
        issues = check_ownership_outside_directory(spec)
        assert len(issues) == 1
        assert issues[0].line == 5
        assert "../escape.ts" in issues[0].message

    def test_fires_once_per_offending_pattern(self) -> None:
        spec = _parsed(
            "Spec: X\n"
            "\n"
            "Owns:\n"
            "  ../a.ts\n"
            "  src/ok.ts\n"
            "  /abs/b.ts\n"
        )
        issues = check_ownership_outside_directory(spec)
        assert [i.line for i in issues] == [4, 6]

    def test_does_not_fire_for_in_directory_patterns(self) -> None:
        spec = _parsed(
            "Spec: X\n"
            "\n"
            "Owns:\n"
            "  src/billing/*.ts\n"
            "  src/billing/services/invoice.ts\n"
        )
        assert check_ownership_outside_directory(spec) == []

    def test_does_not_fire_when_dotdot_is_substring_not_segment(self) -> None:
        """A filename like ``my..config.ts`` contains ``..`` as a
        substring but not as a path *segment* — it doesn't escape, so it
        must not be flagged."""
        spec = _parsed(
            "Spec: X\n"
            "\n"
            "Owns:\n"
            "  src/my..config.ts\n"
        )
        assert check_ownership_outside_directory(spec) == []


# ===========================================================================
# Registry composition
# ===========================================================================


def test_all_nine_single_file_rules_registered() -> None:
    """C3 (4 errors) + C4 (5 warnings) = exactly nine single-file rules.
    Lock the count and the exact set so an accidental duplicate or
    dropped registration surfaces immediately."""
    from specdd_mcp.operations.validation.single_file import SINGLE_FILE_RULES

    assert len(SINGLE_FILE_RULES) == 9
    names = {rule.__name__ for rule in SINGLE_FILE_RULES}
    assert names == {
        "check_missing_spec_header",
        "check_invalid_task_state",
        "check_duplicate_task_id",
        "check_malformed_section",
        "check_missing_purpose",
        "check_unknown_section",
        "check_empty_section",
        "check_long_spec",
        "check_ownership_outside_directory",
    }
