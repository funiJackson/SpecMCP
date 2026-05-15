"""Tests for the byte-faithful I/O helpers in
:mod:`specdd_mcp.operations.mutate_tasks`.

These tests focus on **round-trip fidelity**: read a file, write its
lines back unchanged, assert the bytes on disk are identical. If
``read_preserving`` and ``write_atomic`` are correct, an unmodified
round-trip is a no-op at the byte level.

C8 will layer in fixture-based preservation tests against the surgical
edit logic (C4); this file locks in the I/O contract those tests rely on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from specdd_mcp.operations.hashing import content_hash
from specdd_mcp.operations.mutate_tasks import (
    ReadResult,
    read_preserving,
    write_atomic,
)

# ---------------------------------------------------------------------------
# ReadResult shape
# ---------------------------------------------------------------------------


def test_read_result_default_values() -> None:
    r = ReadResult(bom_present=False)
    assert r.bom_present is False
    assert r.lines == []
    assert r.content_hash == ""


# ---------------------------------------------------------------------------
# Round-trip fidelity
# ---------------------------------------------------------------------------


def _roundtrip(path: Path) -> bytes:
    """Helper: read the file, write its lines back unchanged, return the
    new raw bytes."""
    r = read_preserving(path)
    write_atomic(path, bom_present=r.bom_present, lines=r.lines)
    return path.read_bytes()


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"hello",
        b"Spec: X\n",
        b"Spec: X\nMust:\n  one\n  two\n",
        b"a\r\nb\r\nc\r\n",  # CRLF
        b"a\nb\nc\n",        # LF
        b"a\nb\r\nc\n",       # mixed
        b"Spec: X",           # no trailing newline
        b"emoji \xf0\x9f\x8e\x89\n",  # multi-byte (🎉)
        "中文\n".encode(),    # CJK
        b"   leading whitespace\n  inside body  \n",  # whitespace preserved
    ],
)
def test_roundtrip_preserves_bytes_without_bom(tmp_path: Path, raw: bytes) -> None:
    """Reading and writing back unchanged must reproduce every byte."""
    f = tmp_path / "spec.sdd"
    f.write_bytes(raw)
    assert _roundtrip(f) == raw


def test_roundtrip_with_bom_preserves_bom(tmp_path: Path) -> None:
    """A file starting with UTF-8 BOM round-trips byte-for-byte (BOM
    bytes + body intact)."""
    bom = b"\xef\xbb\xbf"
    body = b"Spec: X\nMust:\n  one\n"
    f = tmp_path / "spec.sdd"
    f.write_bytes(bom + body)
    assert _roundtrip(f) == bom + body


def test_roundtrip_empty_file_with_only_bom(tmp_path: Path) -> None:
    """A file containing **only** a BOM (3 bytes) round-trips as 3 bytes."""
    bom = b"\xef\xbb\xbf"
    f = tmp_path / "bom_only.sdd"
    f.write_bytes(bom)
    assert _roundtrip(f) == bom


# ---------------------------------------------------------------------------
# Fields on ReadResult
# ---------------------------------------------------------------------------


def test_read_preserving_captures_bom_flag(tmp_path: Path) -> None:
    f = tmp_path / "spec.sdd"
    f.write_bytes(b"\xef\xbb\xbfSpec: X\n")
    r = read_preserving(f)
    assert r.bom_present is True


def test_read_preserving_no_bom_flag(tmp_path: Path) -> None:
    f = tmp_path / "spec.sdd"
    f.write_bytes(b"Spec: X\n")
    r = read_preserving(f)
    assert r.bom_present is False


def test_read_preserving_hashes_raw_bytes_including_bom(tmp_path: Path) -> None:
    """The hash is over the FULL raw bytes (BOM included if present).
    Lock this in — STALE_FILE detection depends on it."""
    bom = b"\xef\xbb\xbf"
    body = b"Spec: X\n"
    f = tmp_path / "spec.sdd"
    f.write_bytes(bom + body)
    r = read_preserving(f)
    assert r.content_hash == content_hash(bom + body)


def test_read_preserving_lines_keep_terminators(tmp_path: Path) -> None:
    """``splitlines(keepends=True)`` preserves per-line terminators —
    that's what makes byte-faithful join possible."""
    f = tmp_path / "spec.sdd"
    f.write_bytes(b"a\r\nb\nc")
    r = read_preserving(f)
    assert r.lines == ["a\r\n", "b\n", "c"]


def test_read_preserving_empty_file(tmp_path: Path) -> None:
    f = tmp_path / "empty.sdd"
    f.write_bytes(b"")
    r = read_preserving(f)
    assert r.lines == []
    assert r.bom_present is False
    assert r.content_hash == content_hash(b"")


# ---------------------------------------------------------------------------
# write_atomic
# ---------------------------------------------------------------------------


def test_write_atomic_creates_file_when_missing(tmp_path: Path) -> None:
    f = tmp_path / "new.sdd"
    assert not f.exists()
    new_hash = write_atomic(f, bom_present=False, lines=["Spec: X\n"])
    assert f.exists()
    assert f.read_bytes() == b"Spec: X\n"
    assert new_hash == content_hash(b"Spec: X\n")


def test_write_atomic_overwrites_existing_file(tmp_path: Path) -> None:
    f = tmp_path / "spec.sdd"
    f.write_bytes(b"old content\n")
    new_hash = write_atomic(f, bom_present=False, lines=["new content\n"])
    assert f.read_bytes() == b"new content\n"
    assert new_hash == content_hash(b"new content\n")


def test_write_atomic_returns_hash_of_written_bytes(tmp_path: Path) -> None:
    """The returned hash is what the caller chains into the next call as
    ``expected_content_hash``."""
    f = tmp_path / "spec.sdd"
    new_hash = write_atomic(
        f, bom_present=False, lines=["Spec: X\n", "Must:\n", "  one\n"]
    )
    assert new_hash == content_hash(f.read_bytes())


def test_write_atomic_prepends_bom_when_flag_set(tmp_path: Path) -> None:
    """``bom_present=True`` produces a file starting with the 3 BOM bytes
    followed by the encoded text."""
    f = tmp_path / "spec.sdd"
    write_atomic(f, bom_present=True, lines=["Spec: X\n"])
    assert f.read_bytes() == b"\xef\xbb\xbfSpec: X\n"


def test_write_atomic_no_bom_when_flag_unset(tmp_path: Path) -> None:
    f = tmp_path / "spec.sdd"
    write_atomic(f, bom_present=False, lines=["Spec: X\n"])
    assert f.read_bytes() == b"Spec: X\n"


def test_write_atomic_handles_compound_extension_safely(tmp_path: Path) -> None:
    """``foo.tar.gz`` → tmp = ``foo.tar.gz.tmp`` (appended, not replacing
    a suffix). Verifies the ``with_name`` choice over ``with_suffix``."""
    f = tmp_path / "foo.tar.gz"
    f.write_bytes(b"before\n")
    new_hash = write_atomic(f, bom_present=False, lines=["after\n"])
    assert f.read_bytes() == b"after\n"
    # No leftover tmp file with mangled name.
    assert not (tmp_path / "foo.tar.tar.gz.tmp").exists()
    assert not (tmp_path / "foo.tar.gz.tmp").exists()
    assert new_hash == content_hash(b"after\n")


def test_write_atomic_cleans_up_tmp_after_rename(tmp_path: Path) -> None:
    """The .tmp file gets renamed away — it should not persist after a
    successful write."""
    f = tmp_path / "spec.sdd"
    write_atomic(f, bom_present=False, lines=["Spec: X\n"])
    assert not (tmp_path / "spec.sdd.tmp").exists()


def test_write_atomic_with_empty_lines_writes_empty_file(tmp_path: Path) -> None:
    f = tmp_path / "spec.sdd"
    new_hash = write_atomic(f, bom_present=False, lines=[])
    assert f.read_bytes() == b""
    assert new_hash == content_hash(b"")


# ---------------------------------------------------------------------------
# Hash chains correctly across read → write → read
# ---------------------------------------------------------------------------


def test_unchanged_roundtrip_hash_stays_stable(tmp_path: Path) -> None:
    """Read → write unchanged → read again. Hash before == hash after.
    This is what callers rely on when they ``expected_content_hash`` from
    a prior read."""
    raw = b"Spec: X\nMust:\n  one\n  two\n"
    f = tmp_path / "spec.sdd"
    f.write_bytes(raw)

    r1 = read_preserving(f)
    new_hash = write_atomic(f, bom_present=r1.bom_present, lines=r1.lines)
    r2 = read_preserving(f)

    assert r1.content_hash == new_hash == r2.content_hash
