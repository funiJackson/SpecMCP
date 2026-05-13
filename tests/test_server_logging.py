"""Tests for :mod:`specdd_mcp.server.logging`."""

from __future__ import annotations

import logging
import sys

import pytest

from specdd_mcp.server.logging import (
    SERVER_LOGGER,
    TOOL_LOGGER,
    _truncate,
    configure,
    log_tool_invocation,
    log_tool_result,
)

# ---------------------------------------------------------------------------
# configure()
# ---------------------------------------------------------------------------


def test_configure_attaches_stderr_handler() -> None:
    configure()
    root = logging.getLogger()
    streams = [
        h.stream for h in root.handlers
        if isinstance(h, logging.StreamHandler)
    ]
    assert sys.stderr in streams


def test_configure_is_idempotent() -> None:
    """Calling configure twice should not duplicate handlers."""
    configure()
    first_count = len(logging.getLogger().handlers)
    configure()
    second_count = len(logging.getLogger().handlers)
    assert first_count == second_count


def test_configure_replaces_existing_handlers() -> None:
    """A pre-existing handler should be removed when configure() is called."""
    sentinel = logging.NullHandler()
    logging.getLogger().addHandler(sentinel)
    configure()
    assert sentinel not in logging.getLogger().handlers


def test_configure_sets_info_level_by_default() -> None:
    configure()
    assert logging.getLogger().level == logging.INFO


def test_configure_accepts_custom_level() -> None:
    configure(level=logging.DEBUG)
    assert logging.getLogger().level == logging.DEBUG


# ---------------------------------------------------------------------------
# log_tool_invocation / log_tool_result
# ---------------------------------------------------------------------------


def test_log_tool_invocation_uses_tool_logger(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=TOOL_LOGGER):
        log_tool_invocation("parse_spec", {"path": "x.sdd"})
    records = [r for r in caplog.records if r.name == TOOL_LOGGER]
    assert records
    assert "parse_spec called with" in records[0].getMessage()
    assert "'path'" in records[0].getMessage()


def test_log_tool_invocation_truncates_long_inputs(caplog: pytest.LogCaptureFixture) -> None:
    long_content = "x" * 5_000
    with caplog.at_level(logging.INFO, logger=TOOL_LOGGER):
        log_tool_invocation("parse_spec", {"content": long_content})
    msg = caplog.records[-1].getMessage()
    # Truncation summary should appear; full content should not.
    assert "more chars" in msg
    assert long_content not in msg


def test_log_tool_result_ok(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=TOOL_LOGGER):
        log_tool_result("parse_spec", ok=True)
    msg = caplog.records[-1].getMessage()
    assert msg == "parse_spec → ok"


def test_log_tool_result_err_with_code(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=TOOL_LOGGER):
        log_tool_result("parse_spec", ok=False, error_code="NOT_FOUND")
    msg = caplog.records[-1].getMessage()
    assert msg == "parse_spec → err NOT_FOUND"


def test_log_tool_result_err_without_code(caplog: pytest.LogCaptureFixture) -> None:
    """If a caller forgets to pass the error code, log a `?` placeholder
    rather than crashing or omitting the error indicator entirely."""
    with caplog.at_level(logging.INFO, logger=TOOL_LOGGER):
        log_tool_result("parse_spec", ok=False)
    msg = caplog.records[-1].getMessage()
    assert msg == "parse_spec → err ?"


# ---------------------------------------------------------------------------
# Truncation helper
# ---------------------------------------------------------------------------


def test_truncate_short_string_unchanged() -> None:
    assert _truncate("hello") == "hello"


def test_truncate_long_string_gets_suffix() -> None:
    s = "x" * 500
    out = _truncate(s, max_len=100)
    assert out.startswith("x" * 100)
    assert "400 more chars" in out


def test_truncate_at_exact_limit_unchanged() -> None:
    s = "x" * 200
    assert _truncate(s, max_len=200) == s


# ---------------------------------------------------------------------------
# Logger name constants exist (regression: don't accidentally rename)
# ---------------------------------------------------------------------------


def test_logger_name_constants() -> None:
    assert SERVER_LOGGER == "specdd_mcp.server"
    assert TOOL_LOGGER == "specdd_mcp.tool"
