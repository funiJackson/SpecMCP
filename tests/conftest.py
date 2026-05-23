"""Shared pytest configuration and fixture paths.

Provides:

- ``FIXTURES_DIR`` — committed synthetic .sdd specs used by ``test_fixtures.py``.
- ``benchmark_repo`` (session fixture) — a checkout of github.com/specdd/benchmark.
  Tries a fresh ``git clone`` first; falls back to extracting
  ``benchmark_snapshot.tar.gz`` so the test still runs without network.
"""

from __future__ import annotations

import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixtures directory (synthetic specs, committed in source)
# ---------------------------------------------------------------------------

FIXTURES_DIR: Path = Path(__file__).parent / "fixtures" / "specs"

CHAINS_DIR: Path = Path(__file__).parent / "fixtures" / "chains"
"""Directory of committed multi-file SpecDD trees for chain resolution tests."""

CONFLICT_FIXTURES_DIR: Path = (
    Path(__file__).parent / "fixtures" / "chains_with_conflicts"
)
"""One subdirectory per conflict kind, each a minimal spec tree that fires
exactly that kind. Used by ``test_conflict_fixtures.py`` to lock in detector
behavior against real files (not just inline strings)."""

SCOPE_FIXTURES_DIR: Path = Path(__file__).parent / "fixtures" / "scope"
"""One subdirectory per ``check_modification_scope`` scenario (single vs.
multiple authority, glob vs. literal, new-file-in-glob, no coverage). Each is
a minimal SpecDD tree with a ``.specdd/`` marker. Used by ``test_scope.py``."""

# ---------------------------------------------------------------------------
# Benchmark corpus (live clone with vendored snapshot fallback)
# ---------------------------------------------------------------------------

_BENCHMARK_REPO_URL = "https://github.com/specdd/benchmark.git"
_BENCHMARK_SNAPSHOT = Path(__file__).parent / "benchmark_snapshot.tar.gz"
_BENCHMARK_CLONE_TIMEOUT_SECONDS = 30


@pytest.fixture(scope="session")
def benchmark_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Return the path to a SpecDD benchmark repository checkout.

    Strategy:
        1. Try a shallow ``git clone`` (fast, gets the latest specs).
        2. On any failure (no network, no git binary, timeout, clone error)
           fall back to the vendored snapshot in ``tests/benchmark_snapshot.tar.gz``.
        3. If neither works, skip the test with a clear message.

    The returned path is a session-scoped temporary directory; pytest cleans
    it up automatically.
    """
    target = tmp_path_factory.mktemp("benchmark")
    repo_dir = target / "repo"

    if _try_clone(repo_dir):
        return repo_dir

    if _BENCHMARK_SNAPSHOT.exists():
        repo_dir.mkdir()
        with tarfile.open(_BENCHMARK_SNAPSHOT) as tar:
            # filter="data" prevents extraction outside the target dir (CVE-safe).
            tar.extractall(repo_dir, filter="data")
        return repo_dir

    pytest.skip(
        "benchmark corpus unavailable: git clone failed and no vendored snapshot"
    )


def _try_clone(target: Path) -> bool:
    """Attempt a shallow git clone of the benchmark repo. Return True on success."""
    if shutil.which("git") is None:
        return False
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", _BENCHMARK_REPO_URL, str(target)],
            check=True,
            capture_output=True,
            timeout=_BENCHMARK_CLONE_TIMEOUT_SECONDS,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        # Any failure mode collapses to the fallback path. The exact error
        # doesn't matter — the snapshot is the safety net.
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        return False
    return True
