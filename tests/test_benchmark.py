"""Integration tests against the SpecDD benchmark corpus.

The benchmark repo (`github.com/specdd/benchmark`) is the closest thing to a
"real-world" SpecDD corpus we have. If the parser can't handle every spec in
it, the parser has a real gap. This file makes that signal loud:

- Every `.sdd` file in the benchmark must parse with ``Ok``.
- Every spec must produce zero warnings — the benchmark is canonical
  SpecDD; if our parser is emitting warnings, either the spec violates
  conventions (rare) or our warnings are over-eager (more likely, and
  worth fixing).

Failures are accumulated and reported in one block so we can see the full
picture without re-running the test repeatedly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from specdd_mcp.parser import parse_spec
from specdd_mcp.types import Err, Ok


def _collect_sdd_files(repo: Path) -> list[Path]:
    """Return all `.sdd` files in the corpus.

    Excludes files inside ``.git/`` and macOS AppleDouble resource forks
    (``._foo.sdd``) that can sneak in if a tarball is built without
    ``COPYFILE_DISABLE=1``.
    """
    return sorted(
        p for p in repo.rglob("*.sdd")
        if ".git" not in p.parts and not p.name.startswith("._")
    )


def test_benchmark_corpus_is_not_empty(benchmark_repo: Path) -> None:
    """Sanity check: the corpus must contain at least one `.sdd` file."""
    specs = _collect_sdd_files(benchmark_repo)
    assert specs, f"expected `.sdd` files in {benchmark_repo}, found none"


def test_all_benchmark_specs_parse_ok(benchmark_repo: Path) -> None:
    """Every benchmark spec must parse without an Err result.

    On failure, every offending spec is listed with its error code and message,
    so a single test run reports the entire surface.
    """
    specs = _collect_sdd_files(benchmark_repo)
    failures: list[tuple[Path, str, str]] = []
    for spec_path in specs:
        result = parse_spec(path=str(spec_path))
        if isinstance(result, Err):
            failures.append((spec_path, result.error, result.message))

    if failures:
        lines = [f"{len(failures)} of {len(specs)} benchmark spec(s) failed to parse:"]
        for path, error, message in failures:
            lines.append(f"  - {path.relative_to(benchmark_repo)}: {error}: {message}")
        pytest.fail("\n".join(lines))


def test_all_benchmark_specs_produce_zero_warnings(benchmark_repo: Path) -> None:
    """The benchmark is canonical SpecDD — our parser should emit no warnings.

    If a warning shows up here, investigate:
        - Has the benchmark added a deliberately-malformed example? Update this
          test to allow it.
        - Are our warnings over-eager (false positives)? Fix the parser.
    """
    specs = _collect_sdd_files(benchmark_repo)
    warned: list[tuple[Path, list[str]]] = []
    for spec_path in specs:
        result = parse_spec(path=str(spec_path))
        if isinstance(result, Ok) and result.warnings:
            warned.append((spec_path, result.warnings))

    if warned:
        lines = [f"{len(warned)} benchmark spec(s) produced parser warnings:"]
        for path, warnings in warned:
            lines.append(f"  - {path.relative_to(benchmark_repo)}:")
            for warning in warnings:
                lines.append(f"      * {warning}")
        pytest.fail("\n".join(lines))


def test_benchmark_specs_have_recognizable_levels(benchmark_repo: Path) -> None:
    """At least half of the corpus should resolve to a non-unknown level.

    This is a sanity check on level inference, not a strict count. If the
    benchmark's directory conventions or filename suffixes ever drift far
    enough that inference fails wholesale, this fires.
    """
    specs = _collect_sdd_files(benchmark_repo)
    inferred = []
    for spec_path in specs:
        result = parse_spec(path=str(spec_path))
        assert isinstance(result, Ok)
        inferred.append(result.data.level)

    non_unknown = sum(1 for level in inferred if level != "unknown")
    assert non_unknown >= len(specs) // 2, (
        f"only {non_unknown}/{len(specs)} specs inferred a non-unknown level: "
        f"{inferred}"
    )
