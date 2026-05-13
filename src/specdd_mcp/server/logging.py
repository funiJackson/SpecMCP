"""Logging configuration for the MCP server.

All server output that is NOT the MCP protocol itself goes to stderr — stdout
is reserved for the JSON-RPC stream when using the stdio transport. Anything
written to stdout would corrupt the protocol and break the client.

This module provides:

- :func:`configure` — set up the root logger to write to stderr with a
  consistent format. Called once at server startup (:mod:`__main__`).
- :func:`log_tool_invocation` — record a tool call with its (truncated) input.
- :func:`log_tool_result` — record a tool call's completion (kind only;
  payload is never logged to keep stderr quiet and to avoid leaking spec
  content).

Logger names follow the convention ``specdd_mcp.<area>`` so log filters can
target a specific area (e.g. server lifecycle vs. tool invocations).
"""

from __future__ import annotations

import logging
import sys
from typing import Any

SERVER_LOGGER = "specdd_mcp.server"
"""Logger name for server lifecycle events (startup, shutdown, transport)."""

TOOL_LOGGER = "specdd_mcp.tool"
"""Logger name for every tool invocation and its result."""

_MAX_INPUT_LEN = 200
"""Per-value character cap when logging tool inputs. Avoids dumping a 50 KB
``content`` argument to stderr on every call."""


def configure(level: int = logging.INFO) -> None:
    """Configure the root logger to emit to stderr.

    Idempotent — safely callable multiple times (replaces prior handlers so
    repeat calls don't duplicate log lines, which matters in tests).
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            "[%(asctime)s] %(name)s %(levelname)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)


def log_tool_invocation(tool_name: str, inputs: dict[str, Any]) -> None:
    """Record a tool call's start. Input values are truncated by
    :data:`_MAX_INPUT_LEN`."""
    logger = logging.getLogger(TOOL_LOGGER)
    truncated = {key: _truncate(repr(value)) for key, value in inputs.items()}
    logger.info("%s called with %s", tool_name, truncated)


def log_tool_result(
    tool_name: str,
    *,
    ok: bool,
    error_code: str | None = None,
) -> None:
    """Record a tool call's completion. Only the result kind is logged — never
    the full payload."""
    logger = logging.getLogger(TOOL_LOGGER)
    if ok:
        logger.info("%s → ok", tool_name)
    else:
        logger.info("%s → err %s", tool_name, error_code or "?")


def _truncate(text: str, max_len: int = _MAX_INPUT_LEN) -> str:
    """Truncate a string to ``max_len`` chars; append a count-of-elided suffix
    so the reader knows content was dropped."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"... ({len(text) - max_len} more chars)"
