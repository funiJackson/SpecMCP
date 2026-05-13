"""Edge-case stress tests for parse_spec.

These tests are deliberately adversarial. Each one is either:

- A regression check for a corner that's been debugged before.
- A locked-in behavior we want to commit to for v1.

A failure here means the parser pipeline (lexer → sections → sub-parsers →
orchestrator) has a real gap, not that the test is wrong.
"""

from __future__ import annotations

from pathlib import Path

from specdd_mcp.parser import parse_spec
from specdd_mcp.types import Err, Ok

# ---------------------------------------------------------------------------
# 1. Empty and near-empty inputs
# ---------------------------------------------------------------------------


def test_empty_string_produces_warning_not_error() -> None:
    result = parse_spec(content="")
    assert isinstance(result, Ok)
    assert result.data.name == ""
    assert result.data.line_count == 0
    assert any("no `Spec:` header" in w for w in result.warnings)


def test_only_newlines() -> None:
    result = parse_spec(content="\n\n\n")
    assert isinstance(result, Ok)
    assert result.data.line_count == 3


def test_only_whitespace_lines() -> None:
    result = parse_spec(content="   \n\t\n     \n")
    assert isinstance(result, Ok)
    assert result.data.name == ""


def test_file_with_only_bom_bytes(tmp_path: Path) -> None:
    p = tmp_path / "bom.sdd"
    p.write_bytes(b"\xef\xbb\xbf")
    result = parse_spec(path=str(p))
    assert isinstance(result, Ok)
    assert result.data.line_count == 0


# ---------------------------------------------------------------------------
# 2. Spec header on / near EOF
# ---------------------------------------------------------------------------


def test_spec_header_on_final_line_with_no_trailing_newline() -> None:
    result = parse_spec(content="Spec: NoFinalNewline")
    assert isinstance(result, Ok)
    assert result.data.name == "NoFinalNewline"


def test_section_header_on_final_line_with_no_body_and_no_newline() -> None:
    result = parse_spec(content="Spec: X\nMust:")
    assert isinstance(result, Ok)
    assert result.data.name == "X"
    # `Must:` is detected even without body or trailing newline.
    assert "must" in result.data.positions


def test_spec_header_with_no_value() -> None:
    """`Spec:` with nothing after the colon — degenerate but not an error in
    PR 1. validate_spec flags it later."""
    result = parse_spec(content="Spec:\n")
    assert isinstance(result, Ok)
    assert result.data.name == ""


def test_spec_header_with_only_trailing_whitespace_after_colon() -> None:
    result = parse_spec(content="Spec:   \n")
    assert isinstance(result, Ok)
    assert result.data.name == ""


# ---------------------------------------------------------------------------
# 3. Line ending variations
# ---------------------------------------------------------------------------


def test_crlf_line_endings_throughout() -> None:
    source = "Spec: X\r\n\r\nMust:\r\n  one\r\n  two\r\n"
    result = parse_spec(content=source)
    assert isinstance(result, Ok)
    assert result.data.name == "X"
    assert result.data.must == ["one", "two"]


def test_old_mac_cr_only_line_endings() -> None:
    """Python's splitlines() handles `\\r`-only line endings transparently."""
    source = "Spec: X\rMust:\r  one\r"
    result = parse_spec(content=source)
    assert isinstance(result, Ok)
    assert result.data.name == "X"
    assert result.data.must == ["one"]


def test_mixed_line_endings_within_one_file() -> None:
    source = "Spec: X\n\r\nMust:\n  one\r\n  two\n"
    result = parse_spec(content=source)
    assert isinstance(result, Ok)
    assert result.data.must == ["one", "two"]


def test_bom_plus_crlf(tmp_path: Path) -> None:
    p = tmp_path / "bom_crlf.sdd"
    p.write_bytes(b"\xef\xbb\xbf" + b"Spec: BOM\r\n\r\nPurpose: Hello.\r\n")
    result = parse_spec(path=str(p))
    assert isinstance(result, Ok)
    assert result.data.name == "BOM"
    assert result.data.purpose == "Hello."


# ---------------------------------------------------------------------------
# 4. Scale / long content
# ---------------------------------------------------------------------------


def test_very_long_single_line() -> None:
    """A single line of 10K chars should parse without choking."""
    long_value = "x" * 10_000
    result = parse_spec(content=f"Spec: {long_value}\n")
    assert isinstance(result, Ok)
    assert result.data.name == long_value


def test_one_thousand_tasks() -> None:
    n = 1_000
    lines = ["Spec: Many", "", "Tasks:"]
    lines.extend(f"  [ ] #{i} task {i}" for i in range(n))
    source = "\n".join(lines) + "\n"
    result = parse_spec(content=source)
    assert isinstance(result, Ok)
    assert result.data.tasks is not None
    assert len(result.data.tasks) == n
    # Spot-check the last task.
    assert result.data.tasks[-1].id == f"#{n - 1}"


def test_many_unknown_sections_do_not_crash() -> None:
    blocks = []
    for i in range(50):
        blocks.append(f"Section{i}:\n  content of {i}\n")
    source = "Spec: X\n\n" + "\n".join(blocks)
    result = parse_spec(content=source)
    assert isinstance(result, Ok)
    assert result.data.unknown_sections is not None
    assert len(result.data.unknown_sections) == 50


# ---------------------------------------------------------------------------
# 5. Binary detection across magic numbers
# ---------------------------------------------------------------------------


def test_png_classified_as_binary(tmp_path: Path) -> None:
    p = tmp_path / "image.sdd"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR")
    result = parse_spec(path=str(p))
    assert isinstance(result, Err)
    assert result.error == "PARSE_ERROR"
    assert result.details.get("kind") == "binary"


def test_pdf_classified_as_binary(tmp_path: Path) -> None:
    p = tmp_path / "doc.sdd"
    p.write_bytes(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n" + b"\x00\x00trailer")
    result = parse_spec(path=str(p))
    assert isinstance(result, Err)
    assert result.error == "PARSE_ERROR"
    assert result.details.get("kind") == "binary"


def test_zip_with_nul_padding_classified_as_binary(tmp_path: Path) -> None:
    p = tmp_path / "archive.sdd"
    p.write_bytes(b"PK\x03\x04" + b"\x00" * 20 + b"contents")
    result = parse_spec(path=str(p))
    assert isinstance(result, Err)
    assert result.error == "PARSE_ERROR"


def test_inline_binary_string() -> None:
    result = parse_spec(content="Spec: X\n\x00binary leak")
    assert isinstance(result, Err)
    assert result.error == "PARSE_ERROR"


# ---------------------------------------------------------------------------
# 6. Unicode / multibyte
# ---------------------------------------------------------------------------


def test_emoji_in_section_values() -> None:
    source = "Spec: Party 🎉\n\nPurpose:\n  Celebrate 🥳 always.\n"
    result = parse_spec(content=source)
    assert isinstance(result, Ok)
    assert result.data.name == "Party 🎉"
    assert result.data.purpose == "Celebrate 🥳 always."


def test_cjk_in_task_text() -> None:
    source = "Spec: 计算器\n\nTasks:\n  [ ] 添加加法\n  [x] 测试边界\n"
    result = parse_spec(content=source)
    assert isinstance(result, Ok)
    assert result.data.tasks is not None
    assert result.data.tasks[0].text == "添加加法"
    assert result.data.tasks[1].text == "测试边界"


def test_unicode_header_name_is_not_treated_as_section() -> None:
    """ASCII-only regex means `规范:` is ignored, not classified as unknown."""
    source = "Spec: X\n\n规范: 中文 header\n"
    result = parse_spec(content=source)
    assert isinstance(result, Ok)
    # The Chinese-named "section" doesn't match, so no unknown section appears.
    assert result.data.unknown_sections is None


# ---------------------------------------------------------------------------
# 7. Section header tricks
# ---------------------------------------------------------------------------


def test_indented_colon_line_is_body_not_section() -> None:
    """`Note: ...` indented inside a body must NOT start a new section."""
    source = "Spec: X\n\nPurpose:\n  Note: still part of purpose body.\n"
    result = parse_spec(content=source)
    assert isinstance(result, Ok)
    assert "Note:" in (result.data.purpose or "")


def test_section_header_inside_body_does_start_new_section() -> None:
    """At column 0, ANY capitalized colon-line is a section header."""
    source = (
        "Spec: X\n"
        "\n"
        "Purpose:\n"
        "  Body line one.\n"
        "AnotherHeader:\n"
        "  Body line two.\n"
    )
    result = parse_spec(content=source)
    assert isinstance(result, Ok)
    assert result.data.purpose == "Body line one."
    assert result.data.unknown_sections is not None
    assert result.data.unknown_sections[0].name == "AnotherHeader"


def test_consecutive_blank_lines_dont_break_pipeline() -> None:
    source = "Spec: X\n\n\n\n\nMust:\n\n\n  always\n\n\n"
    result = parse_spec(content=source)
    assert isinstance(result, Ok)
    assert result.data.must == ["always"]


def test_tab_only_indentation_for_bullets() -> None:
    source = "Spec: X\n\nMust:\n\tone\n\ttwo\n"
    result = parse_spec(content=source)
    assert isinstance(result, Ok)
    assert result.data.must == ["one", "two"]


def test_mixed_tab_and_space_indentation_for_bullets() -> None:
    """Min-indent algorithm uses character count, so mixed indents still work
    as long as bullets are at the same depth."""
    source = "Spec: X\n\nMust:\n  one\n\ttwo\n"
    result = parse_spec(content=source)
    assert isinstance(result, Ok)
    # Both have indent length 1 or 2 — they'll be separate bullets.
    assert result.data.must is not None
    assert len(result.data.must) == 2


# ---------------------------------------------------------------------------
# 8. Tasks corner cases
# ---------------------------------------------------------------------------


def test_task_with_brackets_in_text() -> None:
    """Square brackets appearing later in the text don't break the regex."""
    source = "Spec: X\n\nTasks:\n  [ ] handle [special] cases\n"
    result = parse_spec(content=source)
    assert isinstance(result, Ok)
    assert result.data.tasks is not None
    assert result.data.tasks[0].text == "handle [special] cases"


def test_task_id_with_no_space_separator_still_works() -> None:
    """`[ ]#1 task` (no space between bracket and id) is lenient-parsed."""
    source = "Spec: X\n\nTasks:\n  [ ]#1 task one\n"
    result = parse_spec(content=source)
    assert isinstance(result, Ok)
    assert result.data.tasks is not None
    assert result.data.tasks[0].id == "#1"
    assert result.data.tasks[0].text == "task one"


def test_task_text_starting_with_hash_is_not_an_id() -> None:
    """`#important` (non-digit after hash) is text, not an ID."""
    source = "Spec: X\n\nTasks:\n  [ ] #important reminder\n"
    result = parse_spec(content=source)
    assert isinstance(result, Ok)
    assert result.data.tasks is not None
    assert result.data.tasks[0].id is None
    assert result.data.tasks[0].text == "#important reminder"


def test_empty_task_line_skipped() -> None:
    """`[ ]` alone (no text) is malformed and skipped."""
    source = "Spec: X\n\nTasks:\n  [ ]\n  [ ] real task\n"
    result = parse_spec(content=source)
    assert isinstance(result, Ok)
    assert result.data.tasks is not None
    assert len(result.data.tasks) == 1


# ---------------------------------------------------------------------------
# 9. Real SpecDD README example (regression)
# ---------------------------------------------------------------------------


def test_readme_calculator_example_parses_exactly() -> None:
    """The Calculator Add example verbatim from the SpecDD README."""
    source = """\
Spec: Calculator Add

Purpose:
  Add two finite numbers.

Owns:
  calculator.js

Exposes:
  Calculator.add(a, b)

Must:
  Return a + b.
  Reject non-number inputs.

Must not:
  Round results.

Scenario: add numbers
  Given a is 2
  And b is 3
  When add is called
  Then 5 is returned
"""
    result = parse_spec(content=source)
    assert isinstance(result, Ok)
    spec = result.data
    assert spec.name == "Calculator Add"
    assert spec.purpose == "Add two finite numbers."
    assert spec.owns == ["calculator.js"]
    assert spec.exposes == ["Calculator.add(a, b)"]
    assert spec.must == ["Return a + b.", "Reject non-number inputs."]
    assert spec.must_not == ["Round results."]
    assert spec.scenarios is not None
    assert len(spec.scenarios) == 1
    scenario = spec.scenarios[0]
    assert scenario.name == "add numbers"
    assert scenario.steps == [
        "Given a is 2",
        "And b is 3",
        "When add is called",
        "Then 5 is returned",
    ]
    # No warnings — README example is canonical.
    assert result.warnings == []
