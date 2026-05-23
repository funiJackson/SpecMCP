"""Tests for the validation type definitions.

These are pure shape tests — wire-shape models in :mod:`specdd_mcp.types`
and the internal rule-signature aliases in
:mod:`specdd_mcp.operations.validation.types`. Behavioral tests for the
runner and individual rules live in ``test_validation_rules.py`` (PR 5 C9).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from specdd_mcp.operations.validation.types import (
    CrossSpecRule,
    SingleFileRule,
)
from specdd_mcp.types import (
    ScopeReport,
    ValidateSpecData,
    ValidationIssue,
    ValidationSummary,
)

# ---------------------------------------------------------------------------
# ValidationIssue
# ---------------------------------------------------------------------------


def test_validation_issue_minimum_fields() -> None:
    """An error-severity issue can be constructed with just severity +
    code + message. ``line`` and the related-spec fields stay ``None``."""
    issue = ValidationIssue(
        severity="error",
        code="MISSING_SPEC_HEADER",
        message="No 'Spec:' line found.",
    )
    assert issue.severity == "error"
    assert issue.code == "MISSING_SPEC_HEADER"
    assert issue.line is None
    assert issue.related_spec is None
    assert issue.related_line is None


def test_validation_issue_with_line() -> None:
    issue = ValidationIssue(
        severity="warning",
        code="EMPTY_SECTION",
        message="Section is empty.",
        line=12,
    )
    assert issue.line == 12


def test_validation_issue_with_inheritance_context() -> None:
    """Cross-spec rules populate ``related_spec`` + ``related_line`` so the
    slash command can quote both sides of the inheritance finding."""
    issue = ValidationIssue(
        severity="warning",
        code="DUPLICATE_PARENT_RULE",
        message="Same rule appears in a parent.",
        line=8,
        related_spec="src/billing/module.sdd",
        related_line=14,
    )
    assert issue.related_spec == "src/billing/module.sdd"
    assert issue.related_line == 14


def test_validation_issue_rejects_unknown_severity() -> None:
    with pytest.raises(ValidationError):
        ValidationIssue(
            severity="critical",  # type: ignore[arg-type]
            code="MISSING_SPEC_HEADER",
            message="x",
        )


def test_validation_issue_rejects_unknown_code() -> None:
    with pytest.raises(ValidationError):
        ValidationIssue(
            severity="error",
            code="TOTALLY_INVENTED",  # type: ignore[arg-type]
            message="x",
        )


def test_validation_issue_round_trips_through_model_dump() -> None:
    """``Err`` / ``Ok`` envelopes go over the wire via ``model_dump``;
    verify the dict shape is plain JSON-friendly (no Pydantic objects)."""
    issue = ValidationIssue(
        severity="error",
        code="DUPLICATE_TASK_ID",
        message="#1 appears twice.",
        line=14,
    )
    dumped = issue.model_dump()
    assert isinstance(dumped, dict)
    assert dumped["severity"] == "error"
    assert dumped["code"] == "DUPLICATE_TASK_ID"
    assert dumped["line"] == 14


# ---------------------------------------------------------------------------
# ValidationSummary + ValidateSpecData
# ---------------------------------------------------------------------------


def test_validation_summary_pre_computes_counts() -> None:
    summary = ValidationSummary(errors=2, warnings=5)
    assert summary.errors == 2
    assert summary.warnings == 5


def test_validate_spec_data_default_empty_issues() -> None:
    """A clean spec returns ``issues=[]`` and a zero summary."""
    data = ValidateSpecData(summary=ValidationSummary(errors=0, warnings=0))
    assert data.issues == []
    assert data.summary.errors == 0
    assert data.summary.warnings == 0


def test_validate_spec_data_carries_issues() -> None:
    data = ValidateSpecData(
        issues=[
            ValidationIssue(
                severity="error",
                code="MISSING_SPEC_HEADER",
                message="No 'Spec:' line.",
            ),
            ValidationIssue(
                severity="warning",
                code="LONG_SPEC",
                message="File > 80 lines.",
                line=81,
            ),
        ],
        summary=ValidationSummary(errors=1, warnings=1),
    )
    assert len(data.issues) == 2
    assert {i.severity for i in data.issues} == {"error", "warning"}


# ---------------------------------------------------------------------------
# Rule signature aliases (internal, never wire)
# ---------------------------------------------------------------------------


def test_single_file_rule_signature_accepts_pure_callable() -> None:
    """A ``SingleFileRule`` is structurally just
    ``Callable[[ParsedSpec], list[ValidationIssue]]`` — verify a stub
    matches at runtime (no static-check guarantee, but a smoke check)."""

    def stub_rule(_spec: object) -> list[ValidationIssue]:
        return []

    # Annotate locally so mypy sees the alias used.
    rule: SingleFileRule = stub_rule  # type: ignore[assignment]
    assert callable(rule)


def test_cross_spec_rule_signature_accepts_keyword_repo_root() -> None:
    """A ``CrossSpecRule`` takes ``(spec, repo_root)`` positionally per
    the registered shape. Empty placeholder for PR 5; PR 7 lights it up."""

    def stub_rule(
        _spec: object, _repo_root: object
    ) -> list[ValidationIssue]:
        return []

    rule: CrossSpecRule = stub_rule  # type: ignore[assignment]
    assert callable(rule)


# ---------------------------------------------------------------------------
# ScopeReport — included here because it's the other PR 5 wire shape
# ---------------------------------------------------------------------------


def test_scope_report_minimum_fields_when_no_coverage() -> None:
    report = ScopeReport(
        authority_source=None,
        reason="No SpecDD coverage for this target.",
    )
    assert report.authority_source is None
    assert report.effective_scope == []
    assert report.allowed == []
    assert report.out_of_scope == []
    assert report.multiple_authorities is None
    assert "No SpecDD" in (report.reason or "")


def test_scope_report_can_split_files_into_allowed_and_out_of_scope() -> None:
    report = ScopeReport(
        authority_source="src/billing/services/invoice.sdd",
        allowed=["src/billing/services/invoice.ts"],
        out_of_scope=["src/payments/charge.ts"],
    )
    assert report.allowed == ["src/billing/services/invoice.ts"]
    assert report.out_of_scope == ["src/payments/charge.ts"]
    assert report.multiple_authorities is None
