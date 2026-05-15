"""End-to-end coverage of the ``TASK_AMBIGUOUS`` recovery contract.

DESIGN.md §5.5 commits to a specific shape for the ``details.candidates``
payload when ``update_task_status`` can't decide which task the caller
meant. The shape exists so a caller (Claude Code, a human reviewer, an
agent retry loop) can:

  1. See **every** task the identifier matched, in source order.
  2. Read each candidate's ``line`` and feed it back as a ``task_line``
     identifier to disambiguate without re-parsing.
  3. Distinguish candidates by ``id`` / ``text`` / ``current_state`` for
     human-facing display.

Lower-level resolver tests in ``test_mutate_resolver.py`` already cover
the candidates payload at the unit level. This file complements them
with the **integration** view — TASK_AMBIGUOUS as it surfaces through
the orchestrator on real on-disk spec files, plus the recovery flow
DESIGN.md prescribes.
"""

from __future__ import annotations

from pathlib import Path

from specdd_mcp.operations.hashing import content_hash
from specdd_mcp.operations.mutate_tasks import update_task_status
from specdd_mcp.types import Err, Ok, UpdateRequest

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, content: str) -> tuple[Path, str]:
    spec = tmp_path / "spec.sdd"
    spec.write_text(content)
    return spec, content_hash(content.encode("utf-8"))


_AMBIGUOUS_PREFIX_SPEC = (
    "Spec: AmbiguousPrefix\n"
    "\n"
    "Tasks:\n"
    "  [ ] #1 Add validation for currency\n"
    "  [x] #2 Add validation for amount\n"
    "  [ ] #3 Persist invoice\n"
    "  [!] #4 Add documentation\n"
)


_DUPLICATE_ID_SPEC = (
    "Spec: DuplicateId\n"
    "\n"
    "Tasks:\n"
    "  [ ] #1 First instance\n"
    "  [ ] #2 Middle task\n"
    "  [x] #1 Duplicate of first\n"
)


# ---------------------------------------------------------------------------
# 1. Candidates surface in source-line order
# ---------------------------------------------------------------------------


def test_candidates_appear_in_source_line_order(tmp_path: Path) -> None:
    """A UI that lists candidates should read top-to-bottom of the file.
    Reorder protection: any deterministic order other than line-asc
    would break that UX."""
    spec, h = _write(tmp_path, _AMBIGUOUS_PREFIX_SPEC)

    result = update_task_status(
        spec,
        expected_content_hash=h,
        updates=[UpdateRequest(new_state="done", task_text_prefix="Add")],
    )
    assert isinstance(result, Err)
    assert result.error == "TASK_AMBIGUOUS"
    candidates = result.details["candidates"]
    assert isinstance(candidates, list)
    lines = [c["line"] for c in candidates]  # type: ignore[index]
    assert lines == sorted(lines), (
        f"candidates must be in source-line order; got {lines}"
    )


def test_candidates_count_equals_match_count(tmp_path: Path) -> None:
    """Three of the four tasks start with ``Add`` — exactly three
    candidates surface, no more, no less."""
    spec, h = _write(tmp_path, _AMBIGUOUS_PREFIX_SPEC)

    result = update_task_status(
        spec,
        expected_content_hash=h,
        updates=[UpdateRequest(new_state="done", task_text_prefix="Add")],
    )
    assert isinstance(result, Err)
    candidates = result.details["candidates"]
    assert len(candidates) == 3


# ---------------------------------------------------------------------------
# 2. Candidate field shape (the "what the UI gets" contract)
# ---------------------------------------------------------------------------


def test_each_candidate_has_exactly_four_keys(tmp_path: Path) -> None:
    """The candidate dict is intentionally small — no ``indent`` /
    ``raw`` / ``state_symbol`` etc. — so it fits in an error panel.
    Adding fields would inflate every error payload; subtracting them
    breaks the disambiguation contract."""
    spec, h = _write(tmp_path, _AMBIGUOUS_PREFIX_SPEC)

    result = update_task_status(
        spec,
        expected_content_hash=h,
        updates=[UpdateRequest(new_state="done", task_text_prefix="Add")],
    )
    assert isinstance(result, Err)
    for candidate in result.details["candidates"]:
        assert set(candidate.keys()) == {  # type: ignore[union-attr]
            "line",
            "id",
            "text",
            "current_state",
        }


def test_candidate_current_state_reflects_disk_not_request(
    tmp_path: Path,
) -> None:
    """``current_state`` is the state observed on disk, **not** the
    state the failed update was trying to set. Catches a regression
    where the resolver might accidentally surface the requested state."""
    spec, h = _write(tmp_path, _AMBIGUOUS_PREFIX_SPEC)

    result = update_task_status(
        spec,
        expected_content_hash=h,
        updates=[UpdateRequest(new_state="done", task_text_prefix="Add")],
    )
    assert isinstance(result, Err)
    by_id = {c["id"]: c for c in result.details["candidates"]}  # type: ignore[index]
    assert by_id["#1"]["current_state"] == "open"
    assert by_id["#2"]["current_state"] == "done"  # was [x] in source
    assert by_id["#4"]["current_state"] == "blocked"  # was [!] in source


def test_candidate_text_is_the_task_body_only(tmp_path: Path) -> None:
    """``text`` is the parsed body — no leading ``[ ]``, no ``#N``,
    no indent. Important: a caller might display it inline as a list
    item; bracket leakage would look broken."""
    spec, h = _write(tmp_path, _AMBIGUOUS_PREFIX_SPEC)

    result = update_task_status(
        spec,
        expected_content_hash=h,
        updates=[UpdateRequest(new_state="done", task_text_prefix="Add")],
    )
    assert isinstance(result, Err)
    by_id = {c["id"]: c for c in result.details["candidates"]}  # type: ignore[index]
    assert by_id["#1"]["text"] == "Add validation for currency"
    assert by_id["#2"]["text"] == "Add validation for amount"
    for c in result.details["candidates"]:
        assert "[" not in c["text"]  # type: ignore[index]
        assert "#" not in c["text"]  # type: ignore[index]


# ---------------------------------------------------------------------------
# 3. details.identifier echoes the caller's input
# ---------------------------------------------------------------------------


def test_details_identifier_echoes_task_text_prefix(tmp_path: Path) -> None:
    """The ``details.identifier`` string lets the caller include the
    original input in error UI without re-tracking what they passed."""
    spec, h = _write(tmp_path, _AMBIGUOUS_PREFIX_SPEC)

    result = update_task_status(
        spec,
        expected_content_hash=h,
        updates=[UpdateRequest(new_state="done", task_text_prefix="Add")],
    )
    assert isinstance(result, Err)
    assert "task_text_prefix" in result.details["identifier"]  # type: ignore[operator]
    assert "Add" in result.details["identifier"]  # type: ignore[operator]


def test_details_identifier_echoes_duplicate_task_id(tmp_path: Path) -> None:
    spec, h = _write(tmp_path, _DUPLICATE_ID_SPEC)

    result = update_task_status(
        spec,
        expected_content_hash=h,
        updates=[UpdateRequest(new_state="done", task_id="#1")],
    )
    assert isinstance(result, Err)
    assert result.error == "TASK_AMBIGUOUS"
    assert "task_id" in result.details["identifier"]  # type: ignore[operator]
    assert "#1" in result.details["identifier"]  # type: ignore[operator]


# ---------------------------------------------------------------------------
# 4. Caller recovery flow: use candidate.line to disambiguate
# ---------------------------------------------------------------------------


def test_caller_retries_with_candidate_line_and_succeeds(
    tmp_path: Path,
) -> None:
    """The end-to-end recovery flow DESIGN.md prescribes:
      1. Ambiguous call → ``TASK_AMBIGUOUS`` + candidates.
      2. Caller picks one candidate (here: the 1st by source order).
      3. Caller retries with ``task_line=<candidate.line>``.
      4. Retry succeeds — file written exactly once.
    """
    spec, h = _write(tmp_path, _AMBIGUOUS_PREFIX_SPEC)

    first = update_task_status(
        spec,
        expected_content_hash=h,
        updates=[UpdateRequest(new_state="done", task_text_prefix="Add")],
    )
    assert isinstance(first, Err)
    candidate = first.details["candidates"][0]  # type: ignore[index]
    pick_line = candidate["line"]
    assert isinstance(pick_line, int) and pick_line > 0

    # File untouched by the first call → hash is still ``h``.
    retry = update_task_status(
        spec,
        expected_content_hash=h,
        updates=[UpdateRequest(new_state="done", task_line=pick_line)],
    )
    assert isinstance(retry, Ok)
    # The one we picked got flipped to done; the other "Add..." tasks
    # stayed at their original state.
    text = spec.read_text()
    assert "[x] #1 Add validation for currency" in text
    assert "[x] #2 Add validation for amount" in text  # was already x
    assert "[!] #4 Add documentation" in text  # untouched


def test_caller_retries_with_longer_prefix_to_disambiguate(
    tmp_path: Path,
) -> None:
    """Alternative recovery: extend the prefix until it's unique. Picks
    candidates with prefix ``Add validation for amount`` (exactly one)."""
    spec, h = _write(tmp_path, _AMBIGUOUS_PREFIX_SPEC)

    retry = update_task_status(
        spec,
        expected_content_hash=h,
        updates=[
            UpdateRequest(
                new_state="open",  # currently done — flip back
                task_text_prefix="Add validation for amount",
            )
        ],
    )
    assert isinstance(retry, Ok)
    assert "[ ] #2 Add validation for amount" in spec.read_text()


# ---------------------------------------------------------------------------
# 5. Whole-batch atomicity with ambiguous identifier
# ---------------------------------------------------------------------------


def test_ambiguity_in_second_update_rolls_back_first(tmp_path: Path) -> None:
    """The first update would resolve cleanly; the second is ambiguous.
    DESIGN's "whole-batch atomic" rule means the file must stay
    untouched — the first update's change cannot land on disk."""
    spec, h = _write(tmp_path, _AMBIGUOUS_PREFIX_SPEC)
    before = spec.read_bytes()

    result = update_task_status(
        spec,
        expected_content_hash=h,
        updates=[
            UpdateRequest(new_state="done", task_id="#3"),  # unique
            UpdateRequest(new_state="done", task_text_prefix="Add"),  # 3 hits
        ],
    )
    assert isinstance(result, Err)
    assert result.error == "TASK_AMBIGUOUS"
    assert spec.read_bytes() == before, (
        "first update wrote despite ambiguity in second — batch atomicity broken"
    )


def test_ambiguity_in_first_update_does_not_silently_swallow_second(
    tmp_path: Path,
) -> None:
    """Symmetric to the previous test: when the **first** update is
    ambiguous, the resolver short-circuits and the second update never
    runs — but the error reported is for the first update, not the
    second. The candidates list belongs to the first update's prefix."""
    spec, h = _write(tmp_path, _AMBIGUOUS_PREFIX_SPEC)

    result = update_task_status(
        spec,
        expected_content_hash=h,
        updates=[
            UpdateRequest(new_state="done", task_text_prefix="Add"),
            UpdateRequest(new_state="done", task_id="#3"),  # would succeed
        ],
    )
    assert isinstance(result, Err)
    assert result.error == "TASK_AMBIGUOUS"
    assert "task_text_prefix" in result.details["identifier"]  # type: ignore[operator]
    # No #3 in candidates — they're for the ambiguous prefix, not the
    # unrelated second request.
    candidate_ids = {c["id"] for c in result.details["candidates"]}  # type: ignore[index]
    assert "#3" not in candidate_ids


# ---------------------------------------------------------------------------
# 6. Many candidates: scaling stays correct
# ---------------------------------------------------------------------------


def test_ten_candidates_all_appear_in_order(tmp_path: Path) -> None:
    """Defensive scaling: ten tasks share a prefix; all ten surface,
    none truncated, all in line-asc order. Catches a regression where
    the resolver might cap candidates (e.g. a ``[:5]`` slice)."""
    tasks_block = "".join(
        f"  [ ] #{i} Build feature\n" for i in range(1, 11)
    )
    content = f"Spec: Many\n\nTasks:\n{tasks_block}"
    spec, h = _write(tmp_path, content)

    result = update_task_status(
        spec,
        expected_content_hash=h,
        updates=[UpdateRequest(new_state="done", task_text_prefix="Build")],
    )
    assert isinstance(result, Err)
    candidates = result.details["candidates"]
    assert len(candidates) == 10
    ids_in_order = [c["id"] for c in candidates]  # type: ignore[index]
    assert ids_in_order == [f"#{i}" for i in range(1, 11)]


# ---------------------------------------------------------------------------
# 7. Candidates with optional ``id`` field
# ---------------------------------------------------------------------------


def test_candidates_with_no_id_have_explicit_none(tmp_path: Path) -> None:
    """Not every task has an ``#N``. The candidates payload returns
    ``None`` (a real JSON null after ``model_dump``) for those, so a
    UI can distinguish "no id assigned" from "id absent from response"."""
    content = (
        "Spec: NoIds\n"
        "\n"
        "Tasks:\n"
        "  [ ] Add A\n"
        "  [ ] Add B\n"
        "  [ ] #99 Add C\n"
    )
    spec, h = _write(tmp_path, content)

    result = update_task_status(
        spec,
        expected_content_hash=h,
        updates=[UpdateRequest(new_state="done", task_text_prefix="Add")],
    )
    assert isinstance(result, Err)
    candidates = result.details["candidates"]
    assert [c["id"] for c in candidates] == [None, None, "#99"]  # type: ignore[index]


# ---------------------------------------------------------------------------
# 8. MCP wire shape — candidates survive Err.model_dump()
# ---------------------------------------------------------------------------


def test_candidates_survive_model_dump_for_mcp_wire(tmp_path: Path) -> None:
    """``Err.model_dump()`` is what FastMCP serializes — verify the
    candidates round-trip as plain JSON-friendly dicts (no Pydantic
    objects leaking into the wire format)."""
    spec, h = _write(tmp_path, _AMBIGUOUS_PREFIX_SPEC)

    result = update_task_status(
        spec,
        expected_content_hash=h,
        updates=[UpdateRequest(new_state="done", task_text_prefix="Add")],
    )
    assert isinstance(result, Err)
    dumped = result.model_dump()
    assert dumped["ok"] is False
    assert dumped["error"] == "TASK_AMBIGUOUS"
    candidates = dumped["details"]["candidates"]
    # Every candidate is a plain dict (not a BaseModel instance).
    assert all(type(c) is dict for c in candidates)
    # JSON-friendly: ``id`` is either ``str`` or the literal ``None``;
    # everything else is ``str`` / ``int``.
    for c in candidates:
        assert isinstance(c["line"], int)
        assert isinstance(c["text"], str)
        assert isinstance(c["current_state"], str)
        assert c["id"] is None or isinstance(c["id"], str)
