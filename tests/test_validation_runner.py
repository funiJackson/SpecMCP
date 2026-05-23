"""Tests for the validation rule registry and runner.

This file covers the **wiring** of the registry — does
:func:`run_validation` faithfully invoke every rule and aggregate the
output? — not the rules themselves. Per-rule behavior tests live in
``test_validation_rules.py`` (PR 5 C9).

To keep the runner testable without depending on any specific rule's
implementation, the tests monkeypatch the two registries
(:data:`SINGLE_FILE_RULES` and :data:`CROSS_SPEC_RULES`) with stub
callables. Same pattern PR 7 will use when adding cross-spec rules.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from specdd_mcp.operations.validation import run_validation
from specdd_mcp.parser.parse_spec import parse_spec
from specdd_mcp.types import Ok, ParsedSpec, ValidationIssue

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse(content: str = "Spec: X\n") -> ParsedSpec:
    """Parse a tiny valid spec and unwrap the ``Ok``."""
    result = parse_spec(content=content)
    assert isinstance(result, Ok)
    return result.data


def _issue(
    *,
    severity: str = "error",
    code: str = "MISSING_SPEC_HEADER",
    message: str = "stub",
    line: int | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        severity=severity,  # type: ignore[arg-type]
        code=code,  # type: ignore[arg-type]
        message=message,
        line=line,
    )


# ---------------------------------------------------------------------------
# Empty registries (the PR 5 default)
# ---------------------------------------------------------------------------


def test_empty_registries_return_zero_issues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no rules are registered, the runner returns an empty issues
    list and zero counts. Sanity check that the runner doesn't crash on
    an empty registry."""
    monkeypatch.setattr(
        "specdd_mcp.operations.validation.SINGLE_FILE_RULES", []
    )
    monkeypatch.setattr(
        "specdd_mcp.operations.validation.CROSS_SPEC_RULES", []
    )

    data = run_validation(_parse())
    assert data.issues == []
    assert data.summary.errors == 0
    assert data.summary.warnings == 0


# ---------------------------------------------------------------------------
# Single-file rule wiring
# ---------------------------------------------------------------------------


def test_single_file_rules_are_invoked_in_registry_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each callable in ``SINGLE_FILE_RULES`` is called once per
    ``run_validation``, in list order. Output is concatenated."""
    call_order: list[str] = []

    def first(_spec: object) -> list[ValidationIssue]:
        call_order.append("first")
        return [_issue(code="MISSING_SPEC_HEADER", message="from first")]

    def second(_spec: object) -> list[ValidationIssue]:
        call_order.append("second")
        return [
            _issue(severity="warning", code="LONG_SPEC", message="from second"),
            _issue(severity="warning", code="LONG_SPEC", message="dup"),
        ]

    monkeypatch.setattr(
        "specdd_mcp.operations.validation.SINGLE_FILE_RULES",
        [first, second],
    )

    data = run_validation(_parse())
    assert call_order == ["first", "second"]
    assert len(data.issues) == 3
    assert data.summary.errors == 1
    assert data.summary.warnings == 2


def test_runner_tolerates_rules_returning_empty_lists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rule that finds nothing returns ``[]``. The runner must accept
    that without producing spurious issues or crashes."""

    def noisy(_spec: object) -> list[ValidationIssue]:
        return [_issue(code="MISSING_SPEC_HEADER")]

    def quiet(_spec: object) -> list[ValidationIssue]:
        return []

    monkeypatch.setattr(
        "specdd_mcp.operations.validation.SINGLE_FILE_RULES",
        [quiet, noisy, quiet],
    )

    data = run_validation(_parse())
    assert len(data.issues) == 1
    assert data.summary.errors == 1


# ---------------------------------------------------------------------------
# check_inheritance flag
# ---------------------------------------------------------------------------


def test_check_inheritance_false_skips_cross_spec_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the flag is off, cross-spec rules MUST NOT run. Otherwise
    callers calling ``validate_spec`` on raw content (no repo context)
    would hit rules that need ``repo_root``."""
    cross_called: list[bool] = []

    def single(_spec: object) -> list[ValidationIssue]:
        return []

    def cross(_spec: object, _repo: object) -> list[ValidationIssue]:
        cross_called.append(True)
        return []

    monkeypatch.setattr(
        "specdd_mcp.operations.validation.SINGLE_FILE_RULES",
        [single],
    )
    monkeypatch.setattr(
        "specdd_mcp.operations.validation.CROSS_SPEC_RULES",
        [cross],
    )

    run_validation(_parse(), check_inheritance=False)
    assert cross_called == [], "cross-spec rule ran with flag off"


def test_check_inheritance_true_runs_cross_spec_rules_with_repo_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forward-compat check for PR 7. The flag, when ``True``, calls
    every cross-spec rule with the supplied ``repo_root``."""
    seen_repo_roots: list[Path | None] = []

    def cross(_spec: object, repo_root: Path | None) -> list[ValidationIssue]:
        seen_repo_roots.append(repo_root)
        return [
            _issue(severity="warning", code="DUPLICATE_PARENT_RULE",
                   message="x")
        ]

    # Stub the single-file registry empty too, so the assertion below
    # measures only the cross-spec rule's contribution — this test is
    # about the inheritance-flag wiring, not the real single-file rules.
    monkeypatch.setattr(
        "specdd_mcp.operations.validation.SINGLE_FILE_RULES", []
    )
    monkeypatch.setattr(
        "specdd_mcp.operations.validation.CROSS_SPEC_RULES",
        [cross],
    )

    repo = Path("/tmp/example-repo")
    data = run_validation(
        _parse(), check_inheritance=True, repo_root=repo
    )
    assert seen_repo_roots == [repo]
    assert data.summary.warnings == 1


def test_check_inheritance_true_with_no_cross_rules_is_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The PR 5 reality: the flag is ``True`` (passed by ``/specc``)
    but the cross-spec registry is empty. Result: zero extra issues,
    no crash, no warning. This is the contract the slash command
    relies on."""
    monkeypatch.setattr(
        "specdd_mcp.operations.validation.SINGLE_FILE_RULES", []
    )
    monkeypatch.setattr(
        "specdd_mcp.operations.validation.CROSS_SPEC_RULES", []
    )

    data = run_validation(_parse(), check_inheritance=True)
    assert data.issues == []
    assert data.summary.errors == 0
    assert data.summary.warnings == 0


# ---------------------------------------------------------------------------
# Summary correctness
# ---------------------------------------------------------------------------


def test_summary_counts_errors_and_warnings_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def producer(_spec: object) -> list[ValidationIssue]:
        return [
            _issue(severity="error", code="MISSING_SPEC_HEADER"),
            _issue(severity="error", code="DUPLICATE_TASK_ID"),
            _issue(severity="warning", code="EMPTY_SECTION"),
            _issue(severity="warning", code="LONG_SPEC"),
            _issue(severity="warning", code="UNKNOWN_SECTION"),
        ]

    monkeypatch.setattr(
        "specdd_mcp.operations.validation.SINGLE_FILE_RULES", [producer]
    )

    data = run_validation(_parse())
    assert data.summary.errors == 2
    assert data.summary.warnings == 3
    assert len(data.issues) == 5


# ---------------------------------------------------------------------------
# Registry purity — adding a rule doesn't mutate prior runs
# ---------------------------------------------------------------------------


def test_registry_mutation_after_call_does_not_affect_prior_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A previous run's :class:`ValidateSpecData` must remain stable
    even if the registry is later mutated. Defensive immutability
    check — catches a future regression where the runner returns a
    live alias instead of a fresh list."""

    def producer(_spec: object) -> list[ValidationIssue]:
        return [_issue(code="MISSING_SPEC_HEADER")]

    rules: list = [producer]
    monkeypatch.setattr(
        "specdd_mcp.operations.validation.SINGLE_FILE_RULES", rules
    )

    data = run_validation(_parse())
    assert len(data.issues) == 1

    # Mutate the registry after the fact.
    rules.append(producer)
    rules.append(producer)

    assert len(data.issues) == 1, (
        "old result mutated when registry changed — runner is sharing state"
    )
