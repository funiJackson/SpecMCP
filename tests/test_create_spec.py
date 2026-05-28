"""Tests for :func:`specdd_mcp.operations.create_spec.create_spec`.

Covers canonical formatting and section ordering, empty-section omission,
refuse-to-overwrite, validation-before-write, the advisory level cross-check,
parent-directory creation, and round-trip parseability of the scaffold.
"""

from __future__ import annotations

from pathlib import Path

from specdd_mcp.operations.create_spec import create_spec
from specdd_mcp.operations.hashing import content_hash
from specdd_mcp.parser.parse_spec import parse_spec
from specdd_mcp.types import Err, Ok

# ---------------------------------------------------------------------------
# Formatting + ordering
# ---------------------------------------------------------------------------


def test_minimal_spec(tmp_path: Path) -> None:
    spec = tmp_path / "a.sdd"
    result = create_spec(spec, name="My App")
    assert isinstance(result, Ok)
    assert spec.read_text() == "Spec: My App\n"
    assert result.data.content == "Spec: My App\n"


def test_sections_in_canonical_order(tmp_path: Path) -> None:
    spec = tmp_path / "svc.sdd"
    result = create_spec(
        spec,
        name="Svc",
        platform="TypeScript/Node",
        purpose="Do the thing.",
        owns=["a.ts"],
        can_modify=["b.ts"],
        can_read=["../c.ts"],
        references=["../d.sdd"],
        must=["Validate."],
        must_not=["Cheat."],
        depends_on=["Repo"],
        forbids=["stripe"],
        tasks=["one", "two"],
    )
    assert isinstance(result, Ok)
    headers = [
        line for line in result.data.content.splitlines() if line and not line.startswith((" ", "\t"))
    ]
    assert headers == [
        "Spec: Svc",
        "Platform: TypeScript/Node",
        "Purpose:",
        "Owns:",
        "Can modify:",
        "Can read:",
        "References:",
        "Must:",
        "Must not:",
        "Depends on:",
        "Forbids:",
        "Tasks:",
    ]


def test_tasks_are_open(tmp_path: Path) -> None:
    spec = tmp_path / "a.sdd"
    result = create_spec(spec, name="A", tasks=["first", "second"])
    assert isinstance(result, Ok)
    assert "Tasks:\n  [ ] first\n  [ ] second\n" in result.data.content
    parsed = parse_spec(path=str(spec)).data
    assert [(t.state, t.text) for t in (parsed.tasks or [])] == [
        ("open", "first"),
        ("open", "second"),
    ]


def test_multiline_purpose_indented(tmp_path: Path) -> None:
    spec = tmp_path / "a.sdd"
    result = create_spec(spec, name="A", purpose="line one\nline two")
    assert isinstance(result, Ok)
    assert "Purpose:\n  line one\n  line two\n" in result.data.content


def test_empty_sections_omitted(tmp_path: Path) -> None:
    spec = tmp_path / "a.sdd"
    result = create_spec(spec, name="A", owns=[], must=["  ", "real"], forbids=None)
    assert isinstance(result, Ok)
    assert "Owns:" not in result.data.content
    assert "Forbids:" not in result.data.content
    # blank entries dropped, real one kept
    assert "Must:\n  real\n" in result.data.content


def test_entries_are_stripped(tmp_path: Path) -> None:
    spec = tmp_path / "a.sdd"
    result = create_spec(spec, name="  Padded  ", owns=["  x.ts  "])
    assert isinstance(result, Ok)
    assert result.data.content.startswith("Spec: Padded\n")
    assert "  x.ts\n" in result.data.content


def test_roundtrips_through_parser(tmp_path: Path) -> None:
    spec = tmp_path / "svc.sdd"
    result = create_spec(
        spec, name="Svc", owns=["a.ts"], must=["Validate."], forbids=["stripe"]
    )
    assert isinstance(result, Ok)
    parsed = parse_spec(path=str(spec)).data
    assert parsed.name == "Svc"
    assert parsed.owns == ["a.ts"]
    assert parsed.must == ["Validate."]
    assert parsed.forbids == ["stripe"]


# ---------------------------------------------------------------------------
# Write semantics
# ---------------------------------------------------------------------------


def test_refuses_to_overwrite(tmp_path: Path) -> None:
    spec = tmp_path / "a.sdd"
    spec.write_text("Spec: Existing\n")
    result = create_spec(spec, name="New")
    assert isinstance(result, Err)
    assert result.error == "ALREADY_EXISTS"
    assert spec.read_text() == "Spec: Existing\n"  # untouched


def test_creates_missing_parent_directories(tmp_path: Path) -> None:
    spec = tmp_path / "src" / "billing" / "invoice.sdd"
    result = create_spec(spec, name="Invoice")
    assert isinstance(result, Ok)
    assert spec.exists()


def test_content_hash_matches_disk_and_chains(tmp_path: Path) -> None:
    spec = tmp_path / "a.sdd"
    result = create_spec(spec, name="A", tasks=["one"])
    assert isinstance(result, Ok)
    assert result.data.content_hash == content_hash(spec.read_bytes())


def test_empty_name_rejected(tmp_path: Path) -> None:
    spec = tmp_path / "a.sdd"
    result = create_spec(spec, name="   ")
    assert isinstance(result, Err)
    assert result.error == "INVALID_INPUT"
    assert not spec.exists()


# ---------------------------------------------------------------------------
# Validation + level cross-check
# ---------------------------------------------------------------------------


def test_ownership_outside_directory_surfaces_warning(tmp_path: Path) -> None:
    spec = tmp_path / "a.sdd"
    result = create_spec(spec, name="A", owns=["../escapes.ts"])
    assert isinstance(result, Ok)  # warning, not error — still written
    assert any("OWNERSHIP_OUTSIDE_DIRECTORY" in w for w in result.warnings)
    assert spec.exists()


def test_level_mismatch_warns(tmp_path: Path) -> None:
    spec = tmp_path / "plain.sdd"  # path infers "unknown"
    result = create_spec(spec, name="Plain", level="service")
    assert isinstance(result, Ok)
    assert any("level" in w for w in result.warnings)


def test_level_match_no_warning(tmp_path: Path) -> None:
    services = tmp_path / "services"
    services.mkdir()
    spec = services / "billing.sdd"  # path infers "service"
    result = create_spec(spec, name="Billing", level="service")
    assert isinstance(result, Ok)
    assert not any("level" in w for w in result.warnings)
