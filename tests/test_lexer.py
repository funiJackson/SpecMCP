"""Tests for :mod:`specdd_mcp.parser.lexer`.

Covers: line numbering, BOM handling (byte + char), CRLF/LF/mixed line endings,
binary-content detection (NUL bytes), UTF-8 decode failures, and filesystem
error mapping.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from specdd_mcp.parser.lexer import LexedFile, lex_bytes, lex_path, lex_text
from specdd_mcp.types import Err, Ok


def _assert_ok(result: object) -> LexedFile:
    """Narrow a ``LexResult`` to ``LexedFile`` or fail the test."""
    assert isinstance(result, Ok), f"expected Ok, got {result!r}"
    assert isinstance(result.data, LexedFile)
    return result.data


# ---------------------------------------------------------------------------
# lex_text
# ---------------------------------------------------------------------------


def test_lex_text_empty_string() -> None:
    data = _assert_ok(lex_text(""))
    assert data.raw == ""
    assert data.lines == []
    assert data.bom_present is False


def test_lex_text_single_line_no_terminator() -> None:
    data = _assert_ok(lex_text("Spec: Foo"))
    assert data.lines == [(1, "Spec: Foo")]
    assert data.raw == "Spec: Foo"


def test_lex_text_multiple_lines_lf() -> None:
    data = _assert_ok(lex_text("a\nb\nc\n"))
    assert [line.line_no for line in data.lines] == [1, 2, 3]
    assert [line.text for line in data.lines] == ["a", "b", "c"]


def test_lex_text_crlf_line_endings_split_correctly() -> None:
    data = _assert_ok(lex_text("a\r\nb\r\nc"))
    assert [line.text for line in data.lines] == ["a", "b", "c"]
    # Critically: the terminator is NOT part of the line text.
    assert "\r" not in data.lines[0].text


def test_lex_text_mixed_line_endings() -> None:
    data = _assert_ok(lex_text("a\nb\r\nc\nd"))
    assert [line.text for line in data.lines] == ["a", "b", "c", "d"]


def test_lex_text_strips_char_level_bom() -> None:
    data = _assert_ok(lex_text("﻿Spec: Foo\n"))
    assert data.bom_present is True
    assert data.raw == "Spec: Foo\n"
    assert data.lines[0].text == "Spec: Foo"


def test_lex_text_without_bom() -> None:
    data = _assert_ok(lex_text("Spec: Foo\n"))
    assert data.bom_present is False


def test_lex_text_binary_content_returns_parse_error() -> None:
    result = lex_text("normal\x00binary")
    assert isinstance(result, Err)
    assert result.error == "PARSE_ERROR"
    assert result.details.get("kind") == "binary"


def test_lex_text_unicode_content_preserved() -> None:
    data = _assert_ok(lex_text("Spec: 计算器\n  描述\n"))
    assert data.lines[0].text == "Spec: 计算器"
    assert data.lines[1].text == "  描述"


def test_lex_text_blank_lines_have_empty_text() -> None:
    data = _assert_ok(lex_text("a\n\nb\n"))
    assert [line.text for line in data.lines] == ["a", "", "b"]


def test_lex_text_preserves_trailing_whitespace_in_lines() -> None:
    """Trailing whitespace inside a line (not its terminator) is preserved."""
    data = _assert_ok(lex_text("a   \nb\n"))
    assert data.lines[0].text == "a   "


# ---------------------------------------------------------------------------
# lex_bytes
# ---------------------------------------------------------------------------


def test_lex_bytes_plain_utf8() -> None:
    data = _assert_ok(lex_bytes(b"hello\nworld\n"))
    assert [line.text for line in data.lines] == ["hello", "world"]
    assert data.bom_present is False


def test_lex_bytes_with_utf8_bom() -> None:
    data = _assert_ok(lex_bytes(b"\xef\xbb\xbfSpec: X\n"))
    assert data.bom_present is True
    assert data.lines[0].text == "Spec: X"


def test_lex_bytes_invalid_utf8_returns_encoding_error() -> None:
    result = lex_bytes(b"\xff\xfe invalid")
    assert isinstance(result, Err)
    assert result.error == "ENCODING_ERROR"
    assert "reason" in result.details


def test_lex_bytes_with_nul_returns_binary_parse_error() -> None:
    # Valid UTF-8 that just happens to contain a NUL — common in binary files
    # that get misnamed.
    result = lex_bytes(b"prefix\x00suffix")
    assert isinstance(result, Err)
    assert result.error == "PARSE_ERROR"
    assert result.details.get("kind") == "binary"


def test_lex_bytes_empty_bytes() -> None:
    data = _assert_ok(lex_bytes(b""))
    assert data.lines == []
    assert data.raw == ""
    assert data.bom_present is False


def test_lex_bytes_only_bom() -> None:
    data = _assert_ok(lex_bytes(b"\xef\xbb\xbf"))
    assert data.bom_present is True
    assert data.raw == ""
    assert data.lines == []


def test_lex_bytes_multibyte_codepoints_count_as_text() -> None:
    data = _assert_ok(lex_bytes("emoji 🎉\n中文\n".encode()))
    assert data.lines[0].text == "emoji 🎉"
    assert data.lines[1].text == "中文"


# ---------------------------------------------------------------------------
# lex_path
# ---------------------------------------------------------------------------


def test_lex_path_reads_file(tmp_path: Path) -> None:
    p = tmp_path / "x.sdd"
    p.write_text("Spec: Foo\nPurpose: bar\n", encoding="utf-8")
    data = _assert_ok(lex_path(p))
    assert data.lines[0].text == "Spec: Foo"
    assert data.lines[1].text == "Purpose: bar"


def test_lex_path_missing_file(tmp_path: Path) -> None:
    result = lex_path(tmp_path / "does_not_exist.sdd")
    assert isinstance(result, Err)
    assert result.error == "NOT_FOUND"
    assert "path" in result.details


def test_lex_path_directory_returns_io_error(tmp_path: Path) -> None:
    """A directory at the target path is not a file — surface as IO_ERROR."""
    result = lex_path(tmp_path)
    assert isinstance(result, Err)
    assert result.error == "IO_ERROR"


def test_lex_path_with_bom_file(tmp_path: Path) -> None:
    p = tmp_path / "bom.sdd"
    p.write_bytes(b"\xef\xbb\xbfSpec: BOM\n")
    data = _assert_ok(lex_path(p))
    assert data.bom_present is True
    assert data.lines[0].text == "Spec: BOM"


def test_lex_path_with_crlf_file(tmp_path: Path) -> None:
    p = tmp_path / "crlf.sdd"
    p.write_bytes(b"a\r\nb\r\nc\r\n")
    data = _assert_ok(lex_path(p))
    assert [line.text for line in data.lines] == ["a", "b", "c"]


def test_lex_path_with_invalid_utf8(tmp_path: Path) -> None:
    p = tmp_path / "bad.sdd"
    p.write_bytes(b"\xff\xfeInvalid bytes")
    result = lex_path(p)
    assert isinstance(result, Err)
    assert result.error == "ENCODING_ERROR"


def test_lex_path_permission_denied(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Permission errors during read map to IO_ERROR with a clear message."""
    p = tmp_path / "locked.sdd"
    p.write_text("Spec: X\n")

    def _raise_permission(self: Path) -> bytes:
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "read_bytes", _raise_permission)
    result = lex_path(p)
    assert isinstance(result, Err)
    assert result.error == "IO_ERROR"
    assert "permission denied" in result.message.lower()


def test_lex_path_generic_oserror(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Other OS errors (disk fault, EIO, etc.) also map to IO_ERROR."""
    p = tmp_path / "flaky.sdd"
    p.write_text("Spec: X\n")

    def _raise_oserror(self: Path) -> bytes:
        raise OSError(5, "Input/output error", str(self))

    monkeypatch.setattr(Path, "read_bytes", _raise_oserror)
    result = lex_path(p)
    assert isinstance(result, Err)
    assert result.error == "IO_ERROR"
    assert "i/o error" in result.message.lower()


def test_lex_path_with_binary_file(tmp_path: Path) -> None:
    """A binary file containing NUL bytes is detected via the heuristic.

    Note: binary files that happen to contain no NULs (e.g. some misnamed
    archives) will slip through this layer and fail later in the section
    detector. NUL detection covers the common case (PNG, JPEG, ELF, Mach-O,
    PDF, plus any file with embedded headers / length prefixes).
    """
    p = tmp_path / "binary.sdd"
    # PNG-ish: magic header followed by zero-padded IHDR chunk length.
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR")
    result = lex_path(p)
    assert isinstance(result, Err)
    assert result.error == "PARSE_ERROR"
    assert result.details.get("kind") == "binary"
