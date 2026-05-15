"""Tests for :func:`specdd_mcp.operations.hashing.content_hash`."""

from __future__ import annotations

import hashlib

from specdd_mcp.operations.hashing import content_hash


def test_empty_bytes_produces_a_hash() -> None:
    """Hashing an empty file is valid — it produces a well-defined fingerprint
    (no Err, no special case)."""
    assert content_hash(b"") == hashlib.sha256(b"").hexdigest()


def test_known_input_matches_sha256_test_vector() -> None:
    """Lock in the well-known SHA-256 of ``b"hello"``. If this ever drifts,
    something is very wrong (different hash algo, encoding, etc.)."""
    expected = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    assert content_hash(b"hello") == expected


def test_deterministic_across_calls() -> None:
    """Same input → same hash, every time."""
    a = content_hash(b"Spec: Foo\nMust:\n  Validate.\n")
    b = content_hash(b"Spec: Foo\nMust:\n  Validate.\n")
    assert a == b


def test_different_inputs_produce_different_hashes() -> None:
    assert content_hash(b"a") != content_hash(b"b")


def test_one_byte_change_flips_hash() -> None:
    """A single-byte difference flips the hash — that's what makes
    STALE_FILE detection load-bearing for safe writes."""
    assert content_hash(b"Spec: X\n") != content_hash(b"Spec: Y\n")


def test_output_is_64_char_lowercase_hex() -> None:
    h = content_hash(b"anything")
    assert len(h) == 64
    assert h == h.lower()
    assert all(c in "0123456789abcdef" for c in h)


def test_bom_change_flips_hash() -> None:
    """Adding or removing a UTF-8 BOM (3 bytes) changes the hash, so
    update_task_status will detect such a change as STALE_FILE."""
    bom = b"\xef\xbb\xbf"
    body = b"Spec: X\n"
    assert content_hash(body) != content_hash(bom + body)


def test_line_ending_change_flips_hash() -> None:
    """Swapping CRLF → LF flips the hash (file mutation visible)."""
    assert content_hash(b"a\r\nb\r\n") != content_hash(b"a\nb\n")


def test_large_input_one_megabyte() -> None:
    """Hashing 1 MB completes without error. SHA-256 is fast; this is just
    a sanity smoke test on size."""
    big = b"x" * (1024 * 1024)
    h = content_hash(big)
    assert len(h) == 64


def test_no_special_handling_of_nul_bytes() -> None:
    """NUL bytes hash like any other byte. The binary-detection heuristic
    lives elsewhere; hashing is content-agnostic."""
    assert content_hash(b"\x00\x00\x00") != content_hash(b"\x00\x00\x01")
