"""Per-rule tests for the 4 error-severity validation rules (PR 5 C3).

One test class per rule. Each class covers:

  1. The fixture that should trigger the rule fires it with the right
     line number.
  2. A clean spec doesn't trigger it (negative control).
  3. At least one edge case specific to the rule (e.g. duplicate id
     ordering, malformed task with multi-char bracket).

Per-rule fixture files plus a comprehensive ``clean.sdd`` negative
control move to ``tests/fixtures/validation/`` in PR 5 C9. Until then
the inputs are inline strings — keeps the C3 commit self-contained.
"""

from __future__ import annotations

from specdd_mcp.operations.validation.single_file import (
    check_duplicate_task_id,
    check_invalid_task_state,
    check_malformed_section,
    check_missing_spec_header,
)
from specdd_mcp.parser.parse_spec import parse_spec
from specdd_mcp.types import Ok, ParsedSpec


def _parsed(content: str) -> ParsedSpec:
    result = parse_spec(content=content)
    assert isinstance(result, Ok), f"parse failed: {result}"
    return result.data


# ===========================================================================
# MISSING_SPEC_HEADER
# ===========================================================================


class TestMissingSpecHeader:
    """The `Spec:` header is the spec's identity. Without it the spec
    can be parsed but downstream tools that quote the spec name break."""

    def test_fires_when_no_spec_header(self) -> None:
        spec = _parsed("Purpose: Do things.\nMust:\n  Validate input.\n")
        issues = check_missing_spec_header(spec)
        assert len(issues) == 1
        assert issues[0].severity == "error"
        assert issues[0].code == "MISSING_SPEC_HEADER"
        assert issues[0].line == 1

    def test_does_not_fire_for_well_formed_spec(self) -> None:
        spec = _parsed("Spec: Hello\nPurpose: Greet.\n")
        assert check_missing_spec_header(spec) == []

    def test_does_not_fire_for_minimal_spec_just_spec_header(self) -> None:
        """A one-line spec consisting of only ``Spec: X`` is technically
        valid — no other sections required."""
        spec = _parsed("Spec: Tiny\n")
        assert check_missing_spec_header(spec) == []


# ===========================================================================
# INVALID_TASK_STATE
# ===========================================================================


class TestInvalidTaskState:
    """The parser silently drops malformed task lines; this rule
    surfaces them as validation errors so the agent can fix them."""

    def test_fires_on_unknown_single_char_symbol(self) -> None:
        spec = _parsed(
            "Spec: X\n"
            "\n"
            "Tasks:\n"
            "  [y] bogus state\n"
        )
        issues = check_invalid_task_state(spec)
        assert len(issues) == 1
        assert issues[0].severity == "error"
        assert issues[0].code == "INVALID_TASK_STATE"
        assert issues[0].line == 4
        assert "'y'" in issues[0].message

    def test_fires_on_empty_bracket(self) -> None:
        spec = _parsed(
            "Spec: X\n"
            "\n"
            "Tasks:\n"
            "  [] missing symbol\n"
        )
        issues = check_invalid_task_state(spec)
        assert len(issues) == 1
        assert issues[0].line == 4

    def test_fires_on_multi_char_bracket(self) -> None:
        spec = _parsed(
            "Spec: X\n"
            "\n"
            "Tasks:\n"
            "  [ok] multi-char\n"
        )
        issues = check_invalid_task_state(spec)
        assert len(issues) == 1
        assert "'ok'" in issues[0].message

    def test_fires_for_every_bad_line(self) -> None:
        """Multiple bad lines → multiple issues, each at its own
        line number — a single batch fix from the agent needs them
        all."""
        spec = _parsed(
            "Spec: X\n"
            "\n"
            "Tasks:\n"
            "  [y] one\n"
            "  [ok] two\n"
            "  [] three\n"
        )
        issues = check_invalid_task_state(spec)
        assert len(issues) == 3
        assert [i.line for i in issues] == [4, 5, 6]

    def test_does_not_fire_for_canonical_states(self) -> None:
        spec = _parsed(
            "Spec: X\n"
            "\n"
            "Tasks:\n"
            "  [ ] open\n"
            "  [x] done\n"
            "  [-] skipped\n"
            "  [!] blocked\n"
            "  [?] needs decision\n"
        )
        assert check_invalid_task_state(spec) == []

    def test_does_not_fire_outside_tasks_section(self) -> None:
        """A ``[note]`` in a Must rule's text mustn't be confused with a
        bad task state. The rule walks only the ``Tasks:`` body."""
        spec = _parsed(
            "Spec: X\n"
            "\n"
            "Must:\n"
            "  Use the [note] convention.\n"
            "  [arbitrary text] is also fine here.\n"
        )
        assert check_invalid_task_state(spec) == []

    def test_does_not_fire_when_no_tasks_section(self) -> None:
        spec = _parsed("Spec: X\nMust:\n  Validate.\n")
        assert check_invalid_task_state(spec) == []

    def test_does_not_fire_for_empty_tasks_body(self) -> None:
        """``Tasks:`` header with no body content → ``end_line ==
        start_line``; the body-line helper returns ``[]`` without
        iterating. Exercises the empty-body short-circuit."""
        spec = _parsed("Spec: X\n\nTasks:\n")
        assert check_invalid_task_state(spec) == []

    def test_skips_non_bracket_lines_inside_tasks_body(self) -> None:
        """Blank lines or stray prose interleaved with tasks shouldn't
        be treated as malformed task attempts — the rule's regex
        requires a leading ``[``. Exercises the regex-no-match branch."""
        spec = _parsed(
            "Spec: X\n"
            "\n"
            "Tasks:\n"
            "  [ ] one\n"
            "\n"
            "  some prose without a bracket\n"
            "  [x] two\n"
        )
        assert check_invalid_task_state(spec) == []


# ===========================================================================
# DUPLICATE_TASK_ID
# ===========================================================================


class TestDuplicateTaskId:
    """The orchestrator's resolver returns ``TASK_AMBIGUOUS`` when two
    tasks share an id; validate_spec surfaces the same condition as a
    static error so an agent can fix the spec before writes happen."""

    def test_fires_on_duplicate_id(self) -> None:
        spec = _parsed(
            "Spec: X\n"
            "\n"
            "Tasks:\n"
            "  [ ] #1 first\n"
            "  [ ] #1 second\n"
        )
        issues = check_duplicate_task_id(spec)
        assert len(issues) == 1
        assert issues[0].code == "DUPLICATE_TASK_ID"
        # The original (line 4) is not flagged; the duplicate (line 5) is.
        assert issues[0].line == 5
        assert "#1" in issues[0].message
        assert "line 4" in issues[0].message

    def test_emits_one_issue_per_duplicate_occurrence(self) -> None:
        """``#1`` at lines 4, 10, 15 → issues at 10 and 15. Lets a UI
        list every offending line without expanding ranges."""
        spec = _parsed(
            "Spec: X\n"
            "\n"
            "Tasks:\n"
            "  [ ] #1 first\n"
            "  [ ] #2 second\n"
            "  [ ] #1 dup-a\n"
            "  [ ] #3 third\n"
            "  [ ] #1 dup-b\n"
        )
        issues = check_duplicate_task_id(spec)
        assert len(issues) == 2
        assert [i.line for i in issues] == [6, 8]

    def test_does_not_fire_for_unique_ids(self) -> None:
        spec = _parsed(
            "Spec: X\n"
            "\n"
            "Tasks:\n"
            "  [ ] #1 a\n"
            "  [ ] #2 b\n"
            "  [ ] #3 c\n"
        )
        assert check_duplicate_task_id(spec) == []

    def test_does_not_fire_for_tasks_without_ids(self) -> None:
        """Untagged tasks are legal and can repeat freely — no id, no
        duplicate."""
        spec = _parsed(
            "Spec: X\n"
            "\n"
            "Tasks:\n"
            "  [ ] no id here\n"
            "  [ ] no id there\n"
        )
        assert check_duplicate_task_id(spec) == []

    def test_mixed_tagged_and_untagged_only_flags_duplicate_tags(self) -> None:
        spec = _parsed(
            "Spec: X\n"
            "\n"
            "Tasks:\n"
            "  [ ] #1 tagged\n"
            "  [ ] no id\n"
            "  [ ] #1 tagged dup\n"
            "  [ ] no id again\n"
        )
        issues = check_duplicate_task_id(spec)
        assert len(issues) == 1
        assert issues[0].line == 6


# ===========================================================================
# MALFORMED_SECTION
# ===========================================================================


class TestMalformedSection:
    """A section header followed by content the parser can't interpret.
    The canonical case is ``Structure:`` with no ``path: description``
    pairs — the agent will treat the section as missing and ship
    incorrect file-layout expectations."""

    def test_fires_on_structure_with_unparseable_body(self) -> None:
        spec = _parsed(
            "Spec: X\n"
            "\n"
            "Structure:\n"
            "  just some words without a colon\n"
            "  more nonsense\n"
        )
        issues = check_malformed_section(spec)
        assert len(issues) == 1
        assert issues[0].severity == "error"
        assert issues[0].code == "MALFORMED_SECTION"
        # Points at the header line — the user fixes the section as a whole.
        assert issues[0].line == 3
        assert "Structure" in issues[0].message

    def test_does_not_fire_on_well_formed_structure(self) -> None:
        spec = _parsed(
            "Spec: X\n"
            "\n"
            "Structure:\n"
            "  src/billing/services/invoice.ts: the invoice service\n"
            "  src/billing/services/invoice.test.ts: tests for it\n"
        )
        assert check_malformed_section(spec) == []

    def test_does_not_fire_on_empty_structure(self) -> None:
        """``Structure:`` with no body is empty (warning territory),
        not malformed. The two rules must not double-fire."""
        spec = _parsed(
            "Spec: X\n"
            "\n"
            "Structure:\n"
        )
        assert check_malformed_section(spec) == []

    def test_does_not_fire_when_structure_absent(self) -> None:
        spec = _parsed("Spec: X\nMust:\n  Validate.\n")
        assert check_malformed_section(spec) == []


# ===========================================================================
# Registry composition
# ===========================================================================


def test_all_four_error_rules_registered() -> None:
    """The four error rules must always be present in the registry.

    C4 appended five warnings, so the exact registry count is locked by
    ``test_validation_warnings.test_all_nine_single_file_rules_registered``;
    here we only assert the error rules remain registered as a subset, so
    a dropped error rule surfaces immediately."""
    from specdd_mcp.operations.validation.single_file import SINGLE_FILE_RULES

    names = {rule.__name__ for rule in SINGLE_FILE_RULES}
    assert {
        "check_missing_spec_header",
        "check_invalid_task_state",
        "check_duplicate_task_id",
        "check_malformed_section",
    } <= names
