"""Integration tests for the parse_spec orchestrator.

These tests exercise the wiring between the lexer, section detector, level
inference, and per-section parsers. Unit tests for each component live in
their own test files; this file asserts that the full pipeline produces a
well-shaped :class:`ParsedSpec`.
"""

from __future__ import annotations

from pathlib import Path

from specdd_mcp.parser import parse_spec
from specdd_mcp.types import Err, Ok, ParsedSpec, ParsedTask

# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_returns_invalid_input_when_neither_path_nor_content() -> None:
    result = parse_spec()
    assert isinstance(result, Err)
    assert result.error == "INVALID_INPUT"


def test_returns_invalid_input_when_both_path_and_content() -> None:
    result = parse_spec(path="x.sdd", content="Spec: X\n")
    assert isinstance(result, Err)
    assert result.error == "INVALID_INPUT"


def test_returns_not_found_for_missing_path(tmp_path: Path) -> None:
    result = parse_spec(path=str(tmp_path / "does_not_exist.sdd"))
    assert isinstance(result, Err)
    assert result.error == "NOT_FOUND"


def test_returns_parse_error_for_binary_content() -> None:
    result = parse_spec(content="hello\x00binary")
    assert isinstance(result, Err)
    assert result.error == "PARSE_ERROR"
    assert result.details.get("kind") == "binary"


# ---------------------------------------------------------------------------
# Minimal specs
# ---------------------------------------------------------------------------


def test_parses_empty_content_with_warning() -> None:
    result = parse_spec(content="")
    assert isinstance(result, Ok)
    spec: ParsedSpec = result.data
    assert spec.name == ""
    assert "no `Spec:` header" in " ".join(result.warnings)


def test_parses_single_spec_header() -> None:
    result = parse_spec(content="Spec: Foo\n")
    assert isinstance(result, Ok)
    assert result.data.name == "Foo"
    assert result.warnings == []


def test_parses_minimal_viable_spec() -> None:
    """`Spec:` + `Purpose:` is the canonical minimum."""
    result = parse_spec(content="Spec: Example\n\nPurpose:\n  Demonstrate parsing.\n")
    assert isinstance(result, Ok)
    spec = result.data
    assert spec.name == "Example"
    assert spec.purpose == "Demonstrate parsing."


# ---------------------------------------------------------------------------
# Full-shape spec
# ---------------------------------------------------------------------------


_FULL_SPEC = """\
Spec: Invoice Service

Platform: TypeScript/Node

Purpose:
  Coordinate invoice creation through Stripe.

Structure:
  src: Source code
  tests: Test suite

Owns:
  invoice.ts
  invoice.test.ts

Can modify:
  invoice.ts

Can read:
  ../models/*

References:
  ../models/invoice.sdd

Must:
  Validate input before provider calls.
  Persist invoice after provider success.

Must not:
  Call Stripe directly.
  Calculate tax.

Depends on:
  InvoiceRepository
  BillingProviderPort

Forbids:
  stripe

Exposes:
  InvoiceService.createInvoice(input)

Accepts:
  CreateInvoiceInput

Returns:
  InvoiceResult

Raises:
  InvalidInvoiceError

Handles:
  provider timeout

Tasks:
  [ ] #1 Add validation for zero amount.
  [x] #2 Persist invoice id.
  [!] #3 Decide retry policy.

Scenario: invalid amount
  Given an invoice with amount <= 0
  When createInvoice is called
  Then validation fails

Scenario: success
  Given a valid invoice
  When createInvoice is called
  Then the invoice is persisted

Example:
  POST /invoices

Done when:
  All scenarios have tests.
"""


def test_full_spec_populates_every_field() -> None:
    result = parse_spec(content=_FULL_SPEC, virtual_path="src/billing/invoice.service.sdd")
    assert isinstance(result, Ok)
    spec = result.data

    assert spec.path == "src/billing/invoice.service.sdd"
    assert spec.name == "Invoice Service"
    assert spec.level == "service"
    assert spec.platform == "TypeScript/Node"
    assert spec.purpose == "Coordinate invoice creation through Stripe."

    assert spec.structure is not None
    assert [e.path for e in spec.structure] == ["src", "tests"]

    assert spec.owns == ["invoice.ts", "invoice.test.ts"]
    assert spec.can_modify == ["invoice.ts"]
    assert spec.can_read == ["../models/*"]
    assert spec.references == ["../models/invoice.sdd"]

    assert spec.must == [
        "Validate input before provider calls.",
        "Persist invoice after provider success.",
    ]
    assert spec.must_not == ["Call Stripe directly.", "Calculate tax."]
    assert spec.depends_on == ["InvoiceRepository", "BillingProviderPort"]
    assert spec.forbids == ["stripe"]

    assert spec.exposes == ["InvoiceService.createInvoice(input)"]
    assert spec.accepts == ["CreateInvoiceInput"]
    assert spec.returns == ["InvoiceResult"]
    assert spec.raises == ["InvalidInvoiceError"]
    assert spec.handles == ["provider timeout"]

    assert spec.done_when == ["All scenarios have tests."]
    assert spec.examples == ["POST /invoices"]

    assert spec.tasks is not None
    states = [(t.state, t.id) for t in spec.tasks]
    assert states == [
        ("open", "#1"),
        ("done", "#2"),
        ("blocked", "#3"),
    ]

    assert spec.scenarios is not None
    assert [s.name for s in spec.scenarios] == ["invalid amount", "success"]

    # parser_version defaults to package version.
    assert spec.parser_version
    assert spec.encoding == "utf-8"


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------


def test_positions_populated_for_every_known_section_present() -> None:
    result = parse_spec(content=_FULL_SPEC)
    assert isinstance(result, Ok)
    positions = result.data.positions

    # Every known section that appeared in the source has a position entry.
    expected = {
        "spec", "platform", "purpose", "structure",
        "owns", "can_modify", "can_read", "references",
        "must", "must_not", "depends_on", "forbids",
        "exposes", "accepts", "returns", "raises", "handles",
        "tasks", "scenarios", "examples", "done_when",
    }
    assert set(positions.keys()) == expected
    # Spec is on line 1, end_line is also 1 (single-line header with inline value).
    assert positions["spec"].start_line == 1
    assert positions["spec"].end_line == 1


def test_scenarios_position_spans_first_to_last() -> None:
    source = "Spec: X\n\nScenario: a\n  Given x\n\nScenario: b\n  Given y\n"
    result = parse_spec(content=source)
    assert isinstance(result, Ok)
    scenarios_pos = result.data.positions["scenarios"]
    # First scenario header is line 3, last meaningful body line is line 7.
    assert scenarios_pos.start_line == 3
    assert scenarios_pos.end_line == 7


# ---------------------------------------------------------------------------
# Unknown sections
# ---------------------------------------------------------------------------


def test_unknown_sections_preserved_with_line_numbers() -> None:
    source = (
        "Spec: X\n"
        "\n"
        "Custom Header:\n"
        "  some content\n"
        "  more content\n"
        "\n"
        "Another Unknown:\n"
        "  one line\n"
    )
    result = parse_spec(content=source)
    assert isinstance(result, Ok)
    unknowns = result.data.unknown_sections
    assert unknowns is not None
    assert len(unknowns) == 2
    assert unknowns[0].name == "Custom Header"
    assert unknowns[0].start_line == 3
    assert unknowns[0].lines == ["  some content", "  more content", ""]
    assert unknowns[1].name == "Another Unknown"


# ---------------------------------------------------------------------------
# Duplicate section warnings
# ---------------------------------------------------------------------------


def test_multiple_spec_headers_warns_and_uses_first() -> None:
    source = "Spec: First\n\nSpec: Second\n"
    result = parse_spec(content=source)
    assert isinstance(result, Ok)
    assert result.data.name == "First"
    assert any("multiple `Spec:`" in w for w in result.warnings)


def test_multiple_tasks_headers_warns_and_uses_first() -> None:
    source = (
        "Spec: X\n"
        "\n"
        "Tasks:\n"
        "  [ ] from first block\n"
        "\n"
        "Tasks:\n"
        "  [ ] from second block\n"
    )
    result = parse_spec(content=source)
    assert isinstance(result, Ok)
    assert any("Tasks" in w for w in result.warnings)
    assert result.data.tasks is not None
    assert result.data.tasks[0].text == "from first block"


# ---------------------------------------------------------------------------
# Level inference via virtual_path
# ---------------------------------------------------------------------------


def test_virtual_path_drives_level_inference() -> None:
    """When content is provided, virtual_path lets the caller signal the level."""
    result = parse_spec(content="Spec: X\n", virtual_path="src/billing/models/invoice.sdd")
    assert isinstance(result, Ok)
    assert result.data.level == "model"


def test_inline_path_when_no_path_or_virtual() -> None:
    result = parse_spec(content="Spec: X\n")
    assert isinstance(result, Ok)
    assert result.data.path == "<inline>"
    assert result.data.level == "unknown"


# ---------------------------------------------------------------------------
# Reading from disk
# ---------------------------------------------------------------------------


def test_parses_from_disk_path(tmp_path: Path) -> None:
    p = tmp_path / "example.service.sdd"
    p.write_text("Spec: Example\n\nMust:\n  Always do X.\n", encoding="utf-8")
    result = parse_spec(path=str(p))
    assert isinstance(result, Ok)
    spec = result.data
    assert spec.path == str(p)
    assert spec.level == "service"
    assert spec.name == "Example"
    assert spec.must == ["Always do X."]


# ---------------------------------------------------------------------------
# Tasks preservation through the pipeline
# ---------------------------------------------------------------------------


def test_bullet_lines_populated_for_every_bullet_section() -> None:
    """`bullet_lines` is the per-rule line number map used by merge.py to
    build Constraint(rule, source, line) tuples with exact provenance."""
    source = (
        "Spec: X\n"          # line 1
        "\n"                  # line 2
        "Must:\n"             # line 3
        "  Rule one.\n"       # line 4
        "  Rule two.\n"       # line 5
        "  Rule three\n"      # line 6
        "    continuation.\n" # line 7 (continuation, anchors at line 6)
        "\n"                  # line 8
        "Forbids:\n"          # line 9
        "  stripe\n"          # line 10
    )
    result = parse_spec(content=source)
    assert isinstance(result, Ok)
    spec = result.data
    # Must has 3 bullets (last is multi-line, anchored at line 6).
    assert spec.must == ["Rule one.", "Rule two.", "Rule three continuation."]
    assert spec.bullet_lines["must"] == [4, 5, 6]
    # Forbids has 1 bullet.
    assert spec.forbids == ["stripe"]
    assert spec.bullet_lines["forbids"] == [10]


def test_bullet_lines_empty_when_no_bullet_sections_present() -> None:
    """If a spec has no bullet-shaped sections, bullet_lines stays empty."""
    result = parse_spec(content="Spec: X\n\nPurpose:\n  No bullets here.\n")
    assert isinstance(result, Ok)
    assert result.data.bullet_lines == {}


def test_tasks_preserve_indent_and_raw_through_orchestrator() -> None:
    source = "Spec: X\n\nTasks:\n  [ ] first\n    [x] deeper indent\n"
    result = parse_spec(content=source)
    assert isinstance(result, Ok)
    tasks = result.data.tasks
    assert tasks is not None
    assert len(tasks) == 2
    t1: ParsedTask = tasks[0]
    t2: ParsedTask = tasks[1]
    assert t1.indent == "  "
    assert t1.raw == "  [ ] first"
    assert t2.indent == "    "
    assert t2.raw == "    [x] deeper indent"
