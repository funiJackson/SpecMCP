"""JSON round-trip and basic shape tests for every type in :mod:`specdd_mcp.types`.

This file enforces that the Pydantic models are serializable, deserializable, and
preserve all fields. It is the contract test for downstream tools — any change
that breaks round-tripping will fail here before propagating into the parser
or operations layers.
"""

from __future__ import annotations

from typing import TypeVar

import pytest
from pydantic import ValidationError

from specdd_mcp.types import (
    ChainSummaryEntry,
    Conflict,
    Constraint,
    EffectiveConstraints,
    Err,
    MalformedSpec,
    Ok,
    ParsedScenario,
    ParsedSpec,
    ParsedTask,
    ReferenceEntry,
    SectionPosition,
    SpecChain,
    StructureEntry,
    TaskWithSource,
    UnknownSection,
    WriteScopeEntry,
)

_M = TypeVar("_M")


def _round_trip(model: _M) -> _M:
    """Serialize a Pydantic model to JSON and back; assert equality."""
    assert hasattr(model, "model_dump_json")
    cls = type(model)
    blob = model.model_dump_json()
    revived = cls.model_validate_json(blob)
    assert revived == model
    return revived  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Tasks and scenarios
# ---------------------------------------------------------------------------


def test_parsed_task_round_trip() -> None:
    task = ParsedTask(
        state="open",
        state_symbol=" ",
        text="Add validation for unsupported currency.",
        id="#1",
        line=42,
        indent="  ",
        raw="  [ ] #1 Add validation for unsupported currency.",
    )
    _round_trip(task)


def test_parsed_task_without_id_round_trip() -> None:
    task = ParsedTask(
        state="done",
        state_symbol="x",
        text="Define createInvoice public method.",
        line=10,
        indent="  ",
        raw="  [x] Define createInvoice public method.",
    )
    revived = _round_trip(task)
    assert revived.id is None


def test_parsed_task_rejects_bad_state() -> None:
    with pytest.raises(ValidationError):
        ParsedTask(
            state="bogus",  # type: ignore[arg-type]
            state_symbol=" ",
            text="x",
            line=1,
            indent="",
            raw="[ ] x",
        )


def test_parsed_scenario_round_trip() -> None:
    scenario = ParsedScenario(
        name="invalid invoice amount",
        steps=[
            "Given an invoice input with amount less than or equal to zero",
            "When createInvoice is called",
            "Then the invoice is rejected",
        ],
        start_line=20,
        end_line=23,
    )
    _round_trip(scenario)


# ---------------------------------------------------------------------------
# ParsedSpec
# ---------------------------------------------------------------------------


def _make_full_parsed_spec() -> ParsedSpec:
    """Construct a ParsedSpec with every section populated. Used as the
    'maximal' round-trip sample so any new field gets exercised."""
    return ParsedSpec(
        path="src/billing/invoice.sdd",
        name="Invoice Service",
        level="service",
        raw="Spec: Invoice Service\n...full content...\n",
        line_count=42,
        parser_version="0.2.0",
        platform="TypeScript/Node",
        purpose="Coordinate invoice creation.",
        structure=[
            StructureEntry(path="src", description="Source code"),
            StructureEntry(path="tests", description="Test suite"),
        ],
        owns=["invoice.ts", "invoice.test.ts"],
        can_modify=["invoice.ts"],
        can_read=["../models/*"],
        references=["../models/invoice.sdd"],
        must=["Validate input before provider calls."],
        must_not=["Call Stripe directly."],
        depends_on=["InvoiceRepository"],
        forbids=["stripe"],
        exposes=["InvoiceService.createInvoice(input)"],
        accepts=["CreateInvoiceInput"],
        returns=["InvoiceResult"],
        raises=["InvalidInvoiceError"],
        handles=["provider timeout"],
        tasks=[
            ParsedTask(
                state="open",
                state_symbol=" ",
                text="Add validation",
                line=30,
                indent="  ",
                raw="  [ ] Add validation",
            ),
        ],
        scenarios=[
            ParsedScenario(
                name="invalid",
                steps=["Given X", "When Y", "Then Z"],
                start_line=35,
                end_line=38,
            ),
        ],
        examples=["Example payload"],
        done_when=["All scenarios have tests."],
        positions={
            "spec": SectionPosition(start_line=1, end_line=1),
            "purpose": SectionPosition(start_line=3, end_line=4),
        },
        unknown_sections=[
            UnknownSection(
                name="Custom Section",
                lines=["  some content"],
                start_line=40,
                end_line=41,
            ),
        ],
    )


def test_full_parsed_spec_round_trip() -> None:
    spec = _make_full_parsed_spec()
    _round_trip(spec)


def test_minimal_parsed_spec_round_trip() -> None:
    """A spec with only the two required fields beyond identity round-trips fine."""
    spec = ParsedSpec(
        path="example.sdd",
        name="Example",
        level="unknown",
        raw="Spec: Example\n",
        line_count=1,
        parser_version="0.2.0",
    )
    revived = _round_trip(spec)
    assert revived.purpose is None
    assert revived.tasks is None
    assert revived.positions == {}
    assert revived.unknown_sections is None


def test_parsed_spec_default_encoding_and_parser_version() -> None:
    spec = ParsedSpec(
        path="x.sdd",
        name="X",
        level="unknown",
        raw="",
        line_count=0,
    )
    assert spec.encoding == "utf-8"
    assert spec.parser_version  # populated from package __version__


def test_parsed_spec_rejects_bad_level() -> None:
    with pytest.raises(ValidationError):
        ParsedSpec(
            path="x.sdd",
            name="X",
            level="cromulent",  # type: ignore[arg-type]
            raw="",
            line_count=0,
        )


# ---------------------------------------------------------------------------
# SpecChain
# ---------------------------------------------------------------------------


def test_spec_chain_round_trip() -> None:
    spec = _make_full_parsed_spec()
    chain = SpecChain(
        target="src/billing/invoice.ts",
        repo_root="/abs/path/to/repo",
        chain=[spec],
        nearest=spec,
        malformed=[MalformedSpec(path="src/broken.sdd", error="PARSE_ERROR")],
    )
    _round_trip(chain)


def test_spec_chain_empty_defaults() -> None:
    chain = SpecChain(target="x", repo_root="/r")
    assert chain.chain == []
    assert chain.nearest is None
    assert chain.malformed == []


# ---------------------------------------------------------------------------
# Constraints + merged-view types
# ---------------------------------------------------------------------------


def test_constraint_requires_line() -> None:
    with pytest.raises(ValidationError):
        Constraint(rule="x", source="y.sdd")  # type: ignore[call-arg]


def test_constraint_round_trip() -> None:
    c = Constraint(rule="Call Stripe directly.", source="module.sdd", line=14)
    _round_trip(c)


def test_reference_entry_uses_from_alias() -> None:
    entry = ReferenceEntry(**{"from": "a.sdd", "to": "b.sdd", "line": 3})
    assert entry.from_ == "a.sdd"
    blob = entry.model_dump_json(by_alias=True)
    assert '"from":"a.sdd"' in blob


def test_reference_entry_round_trip_with_alias() -> None:
    entry = ReferenceEntry.model_validate({"from": "a.sdd", "to": "b.sdd", "line": 3})
    blob = entry.model_dump_json(by_alias=True)
    revived = ReferenceEntry.model_validate_json(blob)
    assert revived == entry


def test_conflict_round_trip() -> None:
    conflict = Conflict(
        kind="depends_on_vs_forbids",
        rule_a=Constraint(rule="stripe", source="service.sdd", line=10),
        rule_b=Constraint(rule="stripe", source="module.sdd", line=14),
    )
    _round_trip(conflict)


def test_effective_constraints_round_trip() -> None:
    ec = EffectiveConstraints(
        target="src/billing/invoice.ts",
        chain_summary=[
            ChainSummaryEntry(path="app.sdd", level="app"),
            ChainSummaryEntry(path="src/billing/module.sdd", level="module"),
        ],
        must=[Constraint(rule="Validate input", source="invoice.sdd", line=8)],
        must_not=[Constraint(rule="Call Stripe", source="module.sdd", line=14)],
        forbids=[Constraint(rule="stripe", source="module.sdd", line=20)],
        depends_on=[Constraint(rule="InvoiceRepository", source="invoice.sdd", line=12)],
        done_when=[Constraint(rule="All scenarios tested", source="invoice.sdd", line=30)],
        effective_read_scope=[Constraint(rule="../models/*", source="invoice.sdd", line=16)],
        effective_write_scope=[
            WriteScopeEntry(
                pattern="invoice.ts",
                matches=["src/billing/invoice.ts"],
                source="invoice.sdd",
                source_line=4,
            ),
        ],
        write_authority_source="src/billing/invoice.sdd",
        tasks=[
            TaskWithSource(
                state="open",
                state_symbol=" ",
                text="Add validation",
                line=30,
                indent="  ",
                raw="  [ ] Add validation",
                source="src/billing/invoice.sdd",
            ),
        ],
        conflicts=[
            Conflict(
                kind="duplicate_parent_rule",
                rule_a=Constraint(rule="X", source="child.sdd", line=1),
                rule_b=Constraint(rule="X", source="parent.sdd", line=1),
            ),
        ],
        references=[ReferenceEntry(**{"from": "a.sdd", "to": "b.sdd", "line": 3})],
    )
    _round_trip(ec)


def test_effective_constraints_empty_defaults() -> None:
    ec = EffectiveConstraints(target="x")
    assert ec.must == []
    assert ec.must_not == []
    assert ec.forbids == []
    assert ec.conflicts == []
    assert ec.write_authority_source is None


# ---------------------------------------------------------------------------
# Result envelope (Ok / Err discriminated union)
# ---------------------------------------------------------------------------


def test_ok_round_trip_with_parsed_spec() -> None:
    spec = _make_full_parsed_spec()
    ok = Ok[ParsedSpec](data=spec, warnings=["benign"])
    blob = ok.model_dump_json()
    revived = Ok[ParsedSpec].model_validate_json(blob)
    assert revived.ok is True
    assert revived.data == spec
    assert revived.warnings == ["benign"]


def test_err_round_trip() -> None:
    err = Err(
        error="TASK_AMBIGUOUS",
        message="prefix matched 2 tasks",
        details={"candidates": [{"line": 10, "text": "a"}, {"line": 11, "text": "b"}]},
    )
    _round_trip(err)
    assert err.ok is False


def test_ok_discriminator_field_is_literal_true() -> None:
    """Smoke check: the Ok branch never serializes ok:false and vice versa."""
    ok = Ok[int](data=1)
    assert '"ok":true' in ok.model_dump_json()
    err = Err(error="NOT_FOUND", message="x")
    assert '"ok":false' in err.model_dump_json()


def test_err_rejects_unknown_error_code() -> None:
    with pytest.raises(ValidationError):
        Err(error="WAT", message="x")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Cross-cutting: no silent field drop on json round-trip
# ---------------------------------------------------------------------------


def test_parsed_spec_json_field_set_completeness() -> None:
    """Asserts the maximal ParsedSpec serializes ALL fields. Catches accidental
    Field(exclude=True) or similar misconfiguration."""
    spec = _make_full_parsed_spec()
    data = spec.model_dump()
    # Spot-check a sample of every section family rather than every field name,
    # to keep this test stable as DESIGN evolves.
    for required_key in (
        "path",
        "name",
        "level",
        "raw",
        "line_count",
        "encoding",
        "parser_version",
        "platform",
        "purpose",
        "structure",
        "owns",
        "must",
        "must_not",
        "forbids",
        "tasks",
        "scenarios",
        "examples",
        "done_when",
        "positions",
        "unknown_sections",
    ):
        assert required_key in data, f"missing field on dump: {required_key}"
