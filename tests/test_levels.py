"""Tests for :mod:`specdd_mcp.parser.levels`.

Covers each of the 5 resolution rules plus case-insensitivity, plural directory
hints, precedence between rules, and the path-normalization fallback.
"""

from __future__ import annotations

import pytest

from specdd_mcp.parser.levels import infer_level
from specdd_mcp.types import SpecLevel

# ---------------------------------------------------------------------------
# Rule 1: filename suffix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,expected",
    [
        ("invoice.service.sdd", "service"),
        ("stripe.adapter.sdd", "adapter"),
        ("invoice.model.sdd", "model"),
        ("create-invoice.api.sdd", "api"),
        ("invoice-form.component.sdd", "component"),
        ("invoice-sync.job.sdd", "job"),
        ("invoice-created.event.sdd", "event"),
        ("invoice-access.policy.sdd", "policy"),
        ("src/billing/invoice.service.sdd", "service"),
        ("deep/nested/path/foo.service.sdd", "service"),
    ],
)
def test_filename_suffix_inference(path: str, expected: SpecLevel) -> None:
    assert infer_level(path) == expected


# ---------------------------------------------------------------------------
# Rule 2: whole-filename match
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("app.sdd", "app"),
        ("module.sdd", "module"),
        ("feature.sdd", "feature"),
        ("service.sdd", "service"),
        ("model.sdd", "model"),
        ("adapter.sdd", "adapter"),
        ("api.sdd", "api"),
        ("component.sdd", "component"),
        ("job.sdd", "job"),
        ("event.sdd", "event"),
        ("policy.sdd", "policy"),
    ],
)
def test_whole_filename_match(filename: str, expected: SpecLevel) -> None:
    assert infer_level(filename) == expected


def test_whole_filename_with_directory() -> None:
    assert infer_level("src/billing/module.sdd") == "module"


# ---------------------------------------------------------------------------
# Rule 3: parent directory hint
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,expected",
    [
        ("services/foo.sdd", "service"),
        ("service/foo.sdd", "service"),
        ("models/invoice.sdd", "model"),
        ("model/invoice.sdd", "model"),
        ("adapters/stripe.sdd", "adapter"),
        ("apis/health.sdd", "api"),
        ("components/button.sdd", "component"),
        ("jobs/sync.sdd", "job"),
        ("events/created.sdd", "event"),
        ("policies/access.sdd", "policy"),
        ("policy/access.sdd", "policy"),
        ("features/billing.sdd", "feature"),
        ("modules/x.sdd", "module"),
        ("src/billing/services/invoice.sdd", "service"),
    ],
)
def test_parent_directory_hint(path: str, expected: SpecLevel) -> None:
    assert infer_level(path) == expected


# ---------------------------------------------------------------------------
# Rule 4: custom fallback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "something.weird.sdd",
        "foo.bar.sdd",
        "deeply.nested.unknown.sdd",  # rule 1 picks the LAST part ("unknown")
    ],
)
def test_custom_fallback(path: str) -> None:
    # These have a dot suffix but no canonical match and no directory hint.
    # They are structurally a "named" spec but at a custom level.
    assert infer_level(path) == "custom"


def test_custom_with_no_directory_hint() -> None:
    assert infer_level("plain/foo.bar.sdd") == "custom"


# ---------------------------------------------------------------------------
# Rule 5: unknown
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "foo.sdd",
        "bar.sdd",
        "src/random/foo.sdd",
        "deep/path/no_hints/foo.sdd",
    ],
)
def test_unknown_when_no_signal(path: str) -> None:
    assert infer_level(path) == "unknown"


def test_non_sdd_file_returns_unknown() -> None:
    assert infer_level("invoice.ts") == "unknown"
    assert infer_level("invoice.service.txt") == "unknown"


def test_empty_string_returns_unknown() -> None:
    assert infer_level("") == "unknown"


# ---------------------------------------------------------------------------
# Precedence between rules
# ---------------------------------------------------------------------------


def test_filename_suffix_wins_over_directory_hint() -> None:
    """A spec named foo.adapter.sdd inside services/ is an adapter, not a service."""
    assert infer_level("services/stripe.adapter.sdd") == "adapter"


def test_whole_filename_wins_over_directory_hint() -> None:
    """`models/service.sdd` is literally named service — that beats the model hint."""
    assert infer_level("models/service.sdd") == "service"


def test_filename_suffix_wins_over_whole_filename_pattern() -> None:
    """`module.adapter.sdd` is an adapter, not a module."""
    assert infer_level("module.adapter.sdd") == "adapter"


# ---------------------------------------------------------------------------
# Case insensitivity
# ---------------------------------------------------------------------------


def test_case_insensitive_filename_suffix() -> None:
    assert infer_level("Invoice.Service.SDD") == "service"
    assert infer_level("STRIPE.ADAPTER.sdd") == "adapter"


def test_case_insensitive_whole_filename() -> None:
    assert infer_level("MODULE.sdd") == "module"
    assert infer_level("APP.SDD") == "app"


def test_case_insensitive_directory_hint() -> None:
    assert infer_level("Services/Invoice.sdd") == "service"
    assert infer_level("MODELS/foo.sdd") == "model"


# ---------------------------------------------------------------------------
# Path normalization fallback
# ---------------------------------------------------------------------------


def test_backslashes_normalized_to_posix() -> None:
    """Defensive: a caller passing Windows-style separators should still get
    a sensible result rather than degrading to 'unknown'."""
    assert infer_level("src\\billing\\invoice.service.sdd") == "service"
    assert infer_level("services\\foo.sdd") == "service"


def test_mixed_separators() -> None:
    assert infer_level("src/billing\\models/invoice.sdd") == "model"
