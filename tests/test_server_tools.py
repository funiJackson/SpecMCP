"""Tests for the MCP tool wrappers in :mod:`specdd_mcp.server.tools`.

Covers:
- The wrapper produces the Result envelope shape FastMCP serializes.
- Logging happens at invocation and completion.
- Unexpected exceptions are caught and converted to Err.
- The tool is registered against the FastMCP singleton (verified via
  ``mcp.list_tools()``, the supported public API).

End-to-end protocol behavior is tested in PR 2 commit 8 (`test_server.py`).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from specdd_mcp.server.app import mcp
from specdd_mcp.server.logging import TOOL_LOGGER
from specdd_mcp.server.tools import (
    get_effective_constraints,
    list_tasks,
    parse_spec,
    resolve_spec_chain,
    update_task_status,
)

# ---------------------------------------------------------------------------
# Wrapper output shape
# ---------------------------------------------------------------------------


def test_parse_spec_returns_ok_for_valid_content() -> None:
    result = parse_spec(content="Spec: Foo\n")
    assert result["ok"] is True
    assert result["data"]["name"] == "Foo"
    assert result["data"]["level"] == "unknown"
    assert "warnings" in result


def test_parse_spec_with_virtual_path_infers_level() -> None:
    result = parse_spec(
        content="Spec: Inv\n",
        virtual_path="src/billing/services/invoice.sdd",
    )
    assert result["ok"] is True
    assert result["data"]["level"] == "service"


def test_parse_spec_returns_err_for_missing_file(tmp_path: Path) -> None:
    result = parse_spec(path=str(tmp_path / "missing.sdd"))
    assert result["ok"] is False
    assert result["error"] == "NOT_FOUND"


def test_parse_spec_returns_err_for_both_inputs() -> None:
    """Both path and content → INVALID_INPUT, propagated through the wrapper."""
    result = parse_spec(path="x.sdd", content="Spec: X\n")
    assert result["ok"] is False
    assert result["error"] == "INVALID_INPUT"


def test_parse_spec_returns_err_for_no_inputs() -> None:
    result = parse_spec()
    assert result["ok"] is False
    assert result["error"] == "INVALID_INPUT"


def test_parse_spec_returns_err_for_binary_content() -> None:
    result = parse_spec(content="some\x00binary\x00data")
    assert result["ok"] is False
    assert result["error"] == "PARSE_ERROR"
    assert result["details"].get("kind") == "binary"


def test_parse_spec_result_envelope_keys_present_on_success() -> None:
    result = parse_spec(content="Spec: X\n")
    assert set(result.keys()) >= {"ok", "data", "warnings"}


def test_parse_spec_result_envelope_keys_present_on_failure() -> None:
    result = parse_spec(path="/nonexistent/should-not-exist.sdd")
    assert set(result.keys()) >= {"ok", "error", "message", "details"}


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def test_logs_invocation_and_ok_result(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=TOOL_LOGGER):
        parse_spec(content="Spec: X\n")
    messages = [r.getMessage() for r in caplog.records]
    assert any("parse_spec called with" in m for m in messages)
    assert any("parse_spec → ok" in m for m in messages)


def test_logs_error_code_on_failure(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=TOOL_LOGGER):
        parse_spec(path="x.sdd", content="Spec: X\n")
    messages = [r.getMessage() for r in caplog.records]
    assert any("→ err INVALID_INPUT" in m for m in messages)


def test_logs_truncate_long_content_in_invocation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A 50 KB content arg should NOT dump 50 KB to stderr."""
    big = "Spec: X\n" + "x" * 50_000
    with caplog.at_level(logging.INFO, logger=TOOL_LOGGER):
        parse_spec(content=big)
    invocation_msg = next(
        m for r in caplog.records if "called with" in (m := r.getMessage())
    )
    assert "more chars" in invocation_msg
    assert len(invocation_msg) < 2_000  # generous upper bound


# ---------------------------------------------------------------------------
# Unexpected exception conversion
# ---------------------------------------------------------------------------


def test_unexpected_parser_exception_becomes_err(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the underlying parser raises (programming bug, not modeled error),
    the wrapper converts to INVALID_INPUT with the exception details."""

    def _raise_unexpected(**_: object) -> None:
        raise RuntimeError("simulated parser bug")

    monkeypatch.setattr(
        "specdd_mcp.server.tools._parse_spec",
        _raise_unexpected,
    )
    result = parse_spec(content="Spec: X\n")
    assert result["ok"] is False
    assert result["error"] == "INVALID_INPUT"
    assert "simulated parser bug" in result["message"]
    assert result["details"]["exception_type"] == "RuntimeError"


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_spec_registered_with_mcp_singleton() -> None:
    """The @mcp.tool() decorator registers parse_spec on the FastMCP singleton.

    Uses the supported public API (`mcp.list_tools()`) rather than poking at
    internal attributes — this stays robust across MCP SDK versions.
    """
    tools = await mcp.list_tools()
    names = [tool.name for tool in tools]
    assert "parse_spec" in names


@pytest.mark.asyncio
async def test_parse_spec_tool_has_description_for_agents() -> None:
    """The docstring becomes the tool description — Claude reads this when
    deciding whether to use the tool."""
    tools = await mcp.list_tools()
    parse_spec_tool = next(t for t in tools if t.name == "parse_spec")
    assert parse_spec_tool.description is not None
    assert len(parse_spec_tool.description) > 100
    # The "when to prefer this" pitch is the load-bearing part of the
    # docstring. Regress if it's ever dropped.
    assert "Prefer this over" in parse_spec_tool.description


# ---------------------------------------------------------------------------
# resolve_spec_chain wrapper
# ---------------------------------------------------------------------------


def _make_repo_with_spec(tmp_path: Path) -> Path:
    (tmp_path / ".specdd").mkdir()
    (tmp_path / "app.sdd").write_text("Spec: App\n")
    return tmp_path


def test_resolve_chain_returns_ok_with_chain(tmp_path: Path) -> None:
    repo = _make_repo_with_spec(tmp_path)
    code = repo / "src" / "x.ts"
    code.parent.mkdir()
    code.write_text("// x\n")
    result = resolve_spec_chain(target=str(code))
    assert result["ok"] is True
    assert len(result["data"]["chain"]) == 1
    assert result["data"]["chain"][0]["name"] == "App"


def test_resolve_chain_returns_err_for_missing_target(tmp_path: Path) -> None:
    _make_repo_with_spec(tmp_path)
    result = resolve_spec_chain(target=str(tmp_path / "ghost.sdd"))
    assert result["ok"] is False
    assert result["error"] == "NOT_FOUND"


def test_resolve_chain_returns_err_for_relative_target_no_repo(tmp_path: Path) -> None:
    result = resolve_spec_chain(target="src/foo.sdd")
    assert result["ok"] is False
    assert result["error"] == "INVALID_INPUT"


def test_resolve_chain_logs_invocation_and_ok_result(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo = _make_repo_with_spec(tmp_path)
    code = repo / "x.ts"
    code.write_text("// x\n")
    with caplog.at_level(logging.INFO, logger=TOOL_LOGGER):
        resolve_spec_chain(target=str(code))
    messages = [r.getMessage() for r in caplog.records]
    assert any("resolve_spec_chain called with" in m for m in messages)
    assert any("resolve_spec_chain → ok" in m for m in messages)


def test_resolve_chain_unexpected_exception_becomes_err(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_unexpected(**_: object) -> None:
        raise RuntimeError("simulated chain bug")

    monkeypatch.setattr(
        "specdd_mcp.server.tools._resolve_spec_chain",
        _raise_unexpected,
    )
    result = resolve_spec_chain(target="/nonexistent.sdd")
    assert result["ok"] is False
    assert result["error"] == "INVALID_INPUT"
    assert "simulated chain bug" in result["message"]


@pytest.mark.asyncio
async def test_resolve_chain_registered_with_mcp_singleton() -> None:
    tools = await mcp.list_tools()
    names = [tool.name for tool in tools]
    assert "resolve_spec_chain" in names


@pytest.mark.asyncio
async def test_resolve_chain_tool_has_description_for_agents() -> None:
    tools = await mcp.list_tools()
    tool = next(t for t in tools if t.name == "resolve_spec_chain")
    assert tool.description is not None
    assert "Prefer this over" in tool.description


# ---------------------------------------------------------------------------
# list_tasks wrapper
# ---------------------------------------------------------------------------


def _make_repo_with_tasks(tmp_path: Path) -> Path:
    (tmp_path / ".specdd").mkdir()
    (tmp_path / "a.sdd").write_text(
        "Spec: A\n\nTasks:\n  [ ] open one\n  [x] done one\n  [!] blocked one\n"
    )
    return tmp_path


def test_list_tasks_returns_ok_with_default_filter(tmp_path: Path) -> None:
    repo = _make_repo_with_tasks(tmp_path)
    result = list_tasks(repo_root=str(repo))
    assert result["ok"] is True
    # Default states=["open"]: only open tasks.
    assert [t["text"] for t in result["data"]] == ["open one"]


def test_list_tasks_respects_states_filter(tmp_path: Path) -> None:
    repo = _make_repo_with_tasks(tmp_path)
    result = list_tasks(repo_root=str(repo), states=["done"])
    assert result["ok"] is True
    assert [t["state"] for t in result["data"]] == ["done"]


def test_list_tasks_include_blocked_adds_blocked_states(tmp_path: Path) -> None:
    repo = _make_repo_with_tasks(tmp_path)
    result = list_tasks(repo_root=str(repo), include_blocked=True)
    assert result["ok"] is True
    assert {t["state"] for t in result["data"]} == {"open", "blocked"}


def test_list_tasks_text_contains_filter(tmp_path: Path) -> None:
    repo = _make_repo_with_tasks(tmp_path)
    result = list_tasks(
        repo_root=str(repo), states=["open", "done", "blocked"], text_contains="blocked"
    )
    assert result["ok"] is True
    assert [t["text"] for t in result["data"]] == ["blocked one"]


def test_list_tasks_missing_repo_returns_not_found(tmp_path: Path) -> None:
    result = list_tasks(repo_root=str(tmp_path / "ghost"))
    assert result["ok"] is False
    assert result["error"] == "NOT_FOUND"


def test_list_tasks_too_large_returns_err(tmp_path: Path) -> None:
    (tmp_path / ".specdd").mkdir()
    for i in range(15):
        (tmp_path / f"s{i:02}.sdd").write_text(
            f"Spec: S{i}\n\nTasks:\n  [ ] one\n"
        )
    result = list_tasks(repo_root=str(tmp_path), max_specs=10)
    assert result["ok"] is False
    assert result["error"] == "TOO_LARGE"


def test_list_tasks_scope_relative_to_cwd_works(tmp_path: Path) -> None:
    """When scope is absolute, it works regardless of CWD."""
    repo = _make_repo_with_tasks(tmp_path)
    (repo / "sub").mkdir()
    (repo / "sub" / "b.sdd").write_text("Spec: B\n\nTasks:\n  [ ] only sub\n")
    result = list_tasks(repo_root=str(repo), scope=str(repo / "sub"))
    assert result["ok"] is True
    assert [t["text"] for t in result["data"]] == ["only sub"]


def test_list_tasks_logs_invocation_and_result(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo = _make_repo_with_tasks(tmp_path)
    with caplog.at_level(logging.INFO, logger=TOOL_LOGGER):
        list_tasks(repo_root=str(repo))
    messages = [r.getMessage() for r in caplog.records]
    assert any("list_tasks called with" in m for m in messages)
    assert any("list_tasks → ok" in m for m in messages)


def test_list_tasks_unexpected_exception_becomes_err(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(**_: object) -> None:
        raise RuntimeError("simulated tasks bug")

    monkeypatch.setattr("specdd_mcp.server.tools._list_tasks", _raise)
    result = list_tasks(repo_root="/nonexistent")
    assert result["ok"] is False
    assert result["error"] == "INVALID_INPUT"
    assert "simulated tasks bug" in result["message"]


@pytest.mark.asyncio
async def test_list_tasks_registered_with_mcp_singleton() -> None:
    tools = await mcp.list_tools()
    names = [t.name for t in tools]
    assert "list_tasks" in names


@pytest.mark.asyncio
async def test_list_tasks_tool_has_description_for_agents() -> None:
    tools = await mcp.list_tools()
    tool = next(t for t in tools if t.name == "list_tasks")
    assert tool.description is not None
    assert "Prefer this over" in tool.description


# ---------------------------------------------------------------------------
# get_effective_constraints wrapper
# ---------------------------------------------------------------------------


def _make_billing_repo(tmp_path: Path) -> Path:
    """Build a minimal repo whose chain has rules and tasks worth merging."""
    (tmp_path / ".specdd").mkdir()
    (tmp_path / "app.sdd").write_text(
        "Spec: App\n\nForbids:\n  stripe\n"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "service.sdd").write_text(
        "Spec: Service\n\nMust:\n  Validate input.\n"
        "\n"
        "Tasks:\n  [ ] Add tests.\n"
    )
    return tmp_path


def test_get_effective_constraints_returns_ok_with_merged_view(
    tmp_path: Path,
) -> None:
    repo = _make_billing_repo(tmp_path)
    code = repo / "src" / "code.py"
    code.write_text("x = 1\n")
    result = get_effective_constraints(target=str(code))
    assert result["ok"] is True
    data = result["data"]
    assert data["target"] == "src/code.py"
    # 2 specs in chain.
    assert len(data["chain_summary"]) == 2
    # Forbids from app, Must from service.
    assert [c["rule"] for c in data["forbids"]] == ["stripe"]
    assert [c["rule"] for c in data["must"]] == ["Validate input."]
    # Tasks from the chain.
    assert [t["text"] for t in data["tasks"]] == ["Add tests."]


def test_get_effective_constraints_surfaces_conflicts(tmp_path: Path) -> None:
    """A deliberate depends_on_vs_forbids conflict appears in `conflicts`."""
    (tmp_path / ".specdd").mkdir()
    (tmp_path / "app.sdd").write_text(
        "Spec: App\n\nForbids:\n  stripe\n"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "service.sdd").write_text(
        "Spec: S\n\nDepends on:\n  stripe\n"
    )
    target = tmp_path / "src" / "code.py"
    target.write_text("")
    result = get_effective_constraints(target=str(target))
    assert result["ok"] is True
    conflicts = result["data"]["conflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["kind"] == "depends_on_vs_forbids"


def test_get_effective_constraints_missing_target_returns_not_found(
    tmp_path: Path,
) -> None:
    result = get_effective_constraints(target=str(tmp_path / "ghost.py"))
    assert result["ok"] is False
    # NOT_FOUND propagates from resolve_spec_chain.
    assert result["error"] == "NOT_FOUND"


def test_get_effective_constraints_relative_target_no_repo_invalid(
    tmp_path: Path,
) -> None:
    result = get_effective_constraints(target="src/foo.py")
    assert result["ok"] is False
    assert result["error"] == "INVALID_INPUT"


def test_get_effective_constraints_logs_invocation_and_result(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo = _make_billing_repo(tmp_path)
    code = repo / "src" / "code.py"
    code.write_text("")
    with caplog.at_level(logging.INFO, logger=TOOL_LOGGER):
        get_effective_constraints(target=str(code))
    messages = [r.getMessage() for r in caplog.records]
    assert any("get_effective_constraints called with" in m for m in messages)
    assert any("get_effective_constraints → ok" in m for m in messages)


def test_get_effective_constraints_unexpected_exception_becomes_err(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(**_: object) -> None:
        raise RuntimeError("simulated chain bug")

    monkeypatch.setattr(
        "specdd_mcp.server.tools._resolve_spec_chain",
        _raise,
    )
    result = get_effective_constraints(target="/whatever.py")
    assert result["ok"] is False
    assert result["error"] == "INVALID_INPUT"
    assert "simulated chain bug" in result["message"]


@pytest.mark.asyncio
async def test_get_effective_constraints_registered_with_mcp_singleton() -> None:
    tools = await mcp.list_tools()
    names = [t.name for t in tools]
    assert "get_effective_constraints" in names


@pytest.mark.asyncio
async def test_get_effective_constraints_tool_has_agent_pitch() -> None:
    tools = await mcp.list_tools()
    tool = next(t for t in tools if t.name == "get_effective_constraints")
    assert tool.description is not None
    assert "Prefer this over" in tool.description
    # The conflict-handling guidance is the load-bearing part of the
    # docstring — locks in that the agent sees the STOP-vs-advise breakdown.
    assert "STOP" in tool.description
    assert "advisory" in tool.description.lower()


# ---------------------------------------------------------------------------
# update_task_status wrapper
# ---------------------------------------------------------------------------


_SAMPLE_SPEC_BYTES = (
    b"Spec: Sample\n"
    b"\n"
    b"Tasks:\n"
    b"  [ ] #1 First task\n"
    b"  [ ] #2 Second task\n"
)


def _write_sample(tmp_path: Path) -> Path:
    spec = tmp_path / "spec.sdd"
    spec.write_bytes(_SAMPLE_SPEC_BYTES)
    return spec


def _sample_hash() -> str:
    """SHA-256 of ``_SAMPLE_SPEC_BYTES`` — what the wrapper expects as
    ``expected_content_hash`` for a fresh write."""
    import hashlib

    return hashlib.sha256(_SAMPLE_SPEC_BYTES).hexdigest()


def test_update_task_status_returns_ok_for_valid_update(tmp_path: Path) -> None:
    spec = _write_sample(tmp_path)

    result = update_task_status(
        spec_path=str(spec),
        expected_content_hash=_sample_hash(),
        updates=[{"new_state": "done", "task_id": "#1"}],
    )

    assert result["ok"] is True
    assert "[x] #1 First task" in spec.read_text()
    data = result["data"]
    assert data["spec_path"] == str(spec)
    assert len(data["applied"]) == 1
    assert data["applied"][0]["previous_state"] == "open"
    assert "diff" in data
    assert isinstance(data["new_content_hash"], str)
    assert len(data["new_content_hash"]) == 64


def test_update_task_status_multi_update_serialized_as_dict_list(
    tmp_path: Path,
) -> None:
    """The wrapper accepts ``updates`` as a plain ``list[dict]`` (MCP wire
    shape) and converts each dict to ``UpdateRequest`` internally."""
    spec = _write_sample(tmp_path)

    result = update_task_status(
        spec_path=str(spec),
        expected_content_hash=_sample_hash(),
        updates=[
            {"new_state": "done", "task_id": "#1"},
            {"new_state": "blocked", "task_line": 5},
        ],
    )

    assert result["ok"] is True
    content = spec.read_text()
    assert "[x] #1 First task" in content
    assert "[!] #2 Second task" in content


def test_update_task_status_stale_hash_returns_err(tmp_path: Path) -> None:
    spec = _write_sample(tmp_path)
    before = spec.read_bytes()

    result = update_task_status(
        spec_path=str(spec),
        expected_content_hash="0" * 64,
        updates=[{"new_state": "done", "task_id": "#1"}],
    )

    assert result["ok"] is False
    assert result["error"] == "STALE_FILE"
    assert result["details"]["expected_hash"] == "0" * 64
    assert spec.read_bytes() == before


def test_update_task_status_missing_file_returns_not_found(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.sdd"
    result = update_task_status(
        spec_path=str(missing),
        expected_content_hash="0" * 64,
        updates=[{"new_state": "done", "task_id": "#1"}],
    )
    assert result["ok"] is False
    assert result["error"] == "NOT_FOUND"


def test_update_task_status_unresolvable_id_returns_task_not_found(
    tmp_path: Path,
) -> None:
    spec = _write_sample(tmp_path)
    before = spec.read_bytes()

    result = update_task_status(
        spec_path=str(spec),
        expected_content_hash=_sample_hash(),
        updates=[{"new_state": "done", "task_id": "#999"}],
    )

    assert result["ok"] is False
    assert result["error"] == "TASK_NOT_FOUND"
    assert spec.read_bytes() == before


def test_update_task_status_ambiguous_prefix_returns_candidates(
    tmp_path: Path,
) -> None:
    spec = tmp_path / "spec.sdd"
    content = (
        "Spec: Sample\n\nTasks:\n"
        "  [ ] Add validation for currency\n"
        "  [ ] Add validation for amount\n"
    )
    spec.write_text(content)
    import hashlib

    h = hashlib.sha256(content.encode("utf-8")).hexdigest()
    result = update_task_status(
        spec_path=str(spec),
        expected_content_hash=h,
        updates=[{"new_state": "done", "task_text_prefix": "Add"}],
    )

    assert result["ok"] is False
    assert result["error"] == "TASK_AMBIGUOUS"
    candidates = result["details"]["candidates"]
    assert isinstance(candidates, list) and len(candidates) == 2


def test_update_task_status_empty_updates_returns_invalid_input(
    tmp_path: Path,
) -> None:
    spec = _write_sample(tmp_path)
    result = update_task_status(
        spec_path=str(spec),
        expected_content_hash=_sample_hash(),
        updates=[],
    )
    assert result["ok"] is False
    assert result["error"] == "INVALID_INPUT"


def test_update_task_status_malformed_update_dict_becomes_invalid_input(
    tmp_path: Path,
) -> None:
    """A wire-shape dict with an unknown ``new_state`` triggers Pydantic
    validation in the wrapper — surfaces as INVALID_INPUT, not a crash."""
    spec = _write_sample(tmp_path)
    result = update_task_status(
        spec_path=str(spec),
        expected_content_hash=_sample_hash(),
        updates=[{"new_state": "frobnicated", "task_id": "#1"}],
    )
    assert result["ok"] is False
    assert result["error"] == "INVALID_INPUT"
    assert "ValidationError" in result["details"]["exception_type"]


def test_update_task_status_logs_invocation_and_result(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    spec = _write_sample(tmp_path)
    with caplog.at_level(logging.INFO, logger=TOOL_LOGGER):
        update_task_status(
            spec_path=str(spec),
            expected_content_hash=_sample_hash(),
            updates=[{"new_state": "done", "task_id": "#1"}],
        )
    messages = [r.getMessage() for r in caplog.records]
    assert any("update_task_status called with" in m for m in messages)
    assert any("update_task_status → ok" in m for m in messages)


def test_update_task_status_unexpected_exception_becomes_err(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    def _raise(*_: object, **__: object) -> None:
        raise RuntimeError("simulated mutate bug")

    monkeypatch.setattr(
        "specdd_mcp.server.tools._update_task_status",
        _raise,
    )
    spec = _write_sample(tmp_path)
    result = update_task_status(
        spec_path=str(spec),
        expected_content_hash=_sample_hash(),
        updates=[{"new_state": "done", "task_id": "#1"}],
    )
    assert result["ok"] is False
    assert result["error"] == "INVALID_INPUT"
    assert "simulated mutate bug" in result["message"]
    assert result["details"]["exception_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_update_task_status_registered_with_mcp_singleton() -> None:
    tools = await mcp.list_tools()
    names = [t.name for t in tools]
    assert "update_task_status" in names


@pytest.mark.asyncio
async def test_update_task_status_tool_has_agent_pitch() -> None:
    tools = await mcp.list_tools()
    tool = next(t for t in tools if t.name == "update_task_status")
    assert tool.description is not None
    # The "only write tool" + "use this instead of Edit" pitch is the
    # load-bearing part of the docstring. Regress if dropped.
    assert "only" in tool.description.lower()
    assert "Edit" in tool.description
    # The error-code list helps the agent recover from STALE_FILE without
    # asking the user.
    assert "STALE_FILE" in tool.description
    assert "TASK_AMBIGUOUS" in tool.description
