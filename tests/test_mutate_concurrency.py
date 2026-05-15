"""Stale-file + cross-process concurrency tests for ``update_task_status``.

These tests pin down the two correctness guarantees that the in-process
unit tests in ``test_mutate_orchestrator.py`` can't fully exercise:

  * **STALE_FILE recovery loop.** The recommended caller pattern after
    a stale-hash error is "re-parse → retry with the fresh hash". The
    tests here walk through that loop end-to-end and verify the returned
    ``details.expected_hash`` / ``details.actual_hash`` give the caller
    everything they need to act.
  * **Cross-process serialization.** ``operations/locks.py`` uses
    ``fcntl.flock`` / ``msvcrt.locking`` so two **separate processes**
    can never both have an open write window on the same file. The
    in-process lock tests verify the API; only a real subprocess race
    can prove the OS-level primitive is wired correctly.

The subprocess harness is hermetic: it launches Python subprocesses
under ``sys.executable`` (the venv that's running the test), so the
package layout, dependencies, and Python version match the test
environment exactly. Each test caps its subprocess wait with a generous
timeout to avoid hanging CI if the lock primitive ever deadlocks.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from specdd_mcp.operations.hashing import content_hash
from specdd_mcp.operations.mutate_tasks import update_task_status
from specdd_mcp.parser.parse_spec import parse_spec
from specdd_mcp.types import Err, Ok, UpdateRequest

# ---------------------------------------------------------------------------
# Subprocess worker
# ---------------------------------------------------------------------------

# The harness runs this inline via ``python -c``. Keeping it as a string
# (not a separate file under ``tests/``) avoids accidental pytest
# collection and keeps each test self-contained.
#
# Protocol: takes ``spec_path expected_hash new_state task_line`` on argv;
# prints exactly one line to stdout:
#   ``ok:<new_hash>``        — write succeeded; new content hash follows
#   ``err:<ERROR_CODE>``     — write failed cleanly with the listed code
# Any unhandled exception → non-zero exit + traceback on stderr, which
# the test harness surfaces.
_WORKER_SCRIPT = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from specdd_mcp.operations.mutate_tasks import update_task_status
    from specdd_mcp.types import Err, Ok, UpdateRequest

    spec_path = Path(sys.argv[1])
    expected_hash = sys.argv[2]
    new_state = sys.argv[3]
    task_line = int(sys.argv[4])

    result = update_task_status(
        spec_path,
        expected_content_hash=expected_hash,
        updates=[UpdateRequest(new_state=new_state, task_line=task_line)],
    )
    if isinstance(result, Ok):
        sys.stdout.write(f"ok:{result.data.new_content_hash}\\n")
    else:
        sys.stdout.write(f"err:{result.error}\\n")
    sys.stdout.flush()
    """
).strip()


def _launch_worker(
    spec: Path,
    expected_hash: str,
    new_state: str,
    task_line: int,
) -> subprocess.Popen[str]:
    """Spawn one worker subprocess. Non-blocking — caller communicates."""
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            _WORKER_SCRIPT,
            str(spec),
            expected_hash,
            new_state,
            str(task_line),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _collect(proc: subprocess.Popen[str]) -> tuple[int, str, str]:
    """Wait for ``proc`` (10 s cap) and return ``(returncode, stdout, stderr)``.

    The 10 s ceiling is deliberately generous to absorb slow CI runners;
    on dev hardware the workers finish in well under 100 ms. If we ever
    hit the ceiling, the lock primitive almost certainly deadlocked.
    """
    stdout, stderr = proc.communicate(timeout=10)
    return proc.returncode, stdout.strip(), stderr.strip()


# ---------------------------------------------------------------------------
# Shared fixture content
# ---------------------------------------------------------------------------


_SAMPLE = (
    "Spec: Race\n"
    "\n"
    "Tasks:\n"
    "  [ ] #1 race target\n"
)
_SAMPLE_BYTES = _SAMPLE.encode("utf-8")


def _write_sample(tmp_path: Path) -> tuple[Path, str]:
    """Drop the standard race fixture into ``tmp_path``; return the path
    and its starting hash."""
    spec = tmp_path / "spec.sdd"
    spec.write_bytes(_SAMPLE_BYTES)
    return spec, content_hash(_SAMPLE_BYTES)


# ---------------------------------------------------------------------------
# 1. STALE_FILE recovery loop
# ---------------------------------------------------------------------------


def test_stale_file_after_external_write_returns_structured_err(
    tmp_path: Path,
) -> None:
    """The caller computed a hash, then someone (an editor, another
    agent) overwrote the file in place. Our update sees the byte
    change and refuses with STALE_FILE — including both hashes so the
    caller can decide how to recover."""
    spec, original_hash = _write_sample(tmp_path)

    # External rewrite: same task pattern, but extra trailing newline.
    new_bytes = _SAMPLE_BYTES + b"\n"
    spec.write_bytes(new_bytes)

    result = update_task_status(
        spec,
        expected_content_hash=original_hash,
        updates=[UpdateRequest(new_state="done", task_id="#1")],
    )

    assert isinstance(result, Err)
    assert result.error == "STALE_FILE"
    assert result.details["expected_hash"] == original_hash
    assert result.details["actual_hash"] == content_hash(new_bytes)
    assert result.details["path"] == str(spec)


def test_stale_file_recovery_via_reparse_and_retry(tmp_path: Path) -> None:
    """Documents the recommended recovery path. After STALE_FILE, the
    caller re-parses, grabs the fresh hash, and the retry succeeds."""
    spec, original_hash = _write_sample(tmp_path)

    # External edit lands first.
    drifted = _SAMPLE_BYTES.replace(b"race target", b"race target moved")
    spec.write_bytes(drifted)

    # First attempt fails STALE_FILE.
    first = update_task_status(
        spec,
        expected_content_hash=original_hash,
        updates=[UpdateRequest(new_state="done", task_id="#1")],
    )
    assert isinstance(first, Err)
    assert first.error == "STALE_FILE"

    # Caller re-parses and retries.
    reparsed = parse_spec(path=str(spec))
    assert isinstance(reparsed, Ok)
    fresh_hash = content_hash(spec.read_bytes())

    second = update_task_status(
        spec,
        expected_content_hash=fresh_hash,
        updates=[UpdateRequest(new_state="done", task_id="#1")],
    )

    assert isinstance(second, Ok)
    assert "[x] #1 race target moved" in spec.read_text()


def test_consecutive_updates_with_old_hash_returns_stale(tmp_path: Path) -> None:
    """The result envelope returns ``new_content_hash`` so chained calls
    don't need a re-parse. Using the **old** hash on the second call must
    trip STALE_FILE — proves the hash check fires correctly after our
    own previous write."""
    spec, original_hash = _write_sample(tmp_path)

    first = update_task_status(
        spec,
        expected_content_hash=original_hash,
        updates=[UpdateRequest(new_state="done", task_id="#1")],
    )
    assert isinstance(first, Ok)

    # Use the *original* hash again — stale relative to disk now.
    second = update_task_status(
        spec,
        expected_content_hash=original_hash,
        updates=[UpdateRequest(new_state="skipped", task_id="#1")],
    )
    assert isinstance(second, Err)
    assert second.error == "STALE_FILE"
    assert second.details["expected_hash"] == original_hash
    assert (
        second.details["actual_hash"] == first.data.new_content_hash
    ), "actual_hash must equal the hash the first update wrote"


def test_chained_updates_with_returned_hash_avoid_reparse(tmp_path: Path) -> None:
    """The dual of the previous test: chaining with the returned
    ``new_content_hash`` is the supported pattern for back-to-back
    updates without a parse round-trip."""
    spec, h0 = _write_sample(tmp_path)

    r1 = update_task_status(
        spec,
        expected_content_hash=h0,
        updates=[UpdateRequest(new_state="done", task_id="#1")],
    )
    assert isinstance(r1, Ok)

    r2 = update_task_status(
        spec,
        expected_content_hash=r1.data.new_content_hash,
        updates=[UpdateRequest(new_state="open", task_id="#1")],
    )
    assert isinstance(r2, Ok)
    assert spec.read_bytes() == _SAMPLE_BYTES, (
        "chaining done→open must round-trip back to the source bytes"
    )


# ---------------------------------------------------------------------------
# 2. Cross-process concurrency
# ---------------------------------------------------------------------------


def test_two_subprocess_writers_one_wins_other_gets_stale(
    tmp_path: Path,
) -> None:
    """The minimal cross-process race. Two workers see the same starting
    hash and both try to write. The lock serializes; whichever runs
    first writes successfully, the second one's hash is now stale.

    Result: exactly one ``ok``, exactly one ``err:STALE_FILE``. The
    file ends in *one* coherent post-update state — never partial,
    never both updates applied."""
    spec, h = _write_sample(tmp_path)

    p1 = _launch_worker(spec, h, "done", 4)
    p2 = _launch_worker(spec, h, "skipped", 4)

    code1, out1, err1 = _collect(p1)
    code2, out2, err2 = _collect(p2)

    assert code1 == 0, f"worker 1 crashed: {err1}"
    assert code2 == 0, f"worker 2 crashed: {err2}"

    outs = {out1, out2}
    ok_lines = [o for o in outs if o.startswith("ok:")]
    err_lines = [o for o in outs if o.startswith("err:")]
    assert len(ok_lines) == 1, (
        f"exactly one worker should succeed; got {outs}"
    )
    assert err_lines == ["err:STALE_FILE"], (
        f"the losing worker must report STALE_FILE; got {err_lines}"
    )

    final = spec.read_bytes()
    # Final disk state is exactly one of the two intended writes.
    assert final in (
        _SAMPLE_BYTES.replace(b"[ ]", b"[x]", 1),
        _SAMPLE_BYTES.replace(b"[ ]", b"[-]", 1),
    ), f"file is in an unexpected state: {final!r}"


def test_four_subprocess_writers_only_one_wins(tmp_path: Path) -> None:
    """Bigger race: four workers, each requesting a different state.
    Three must lose with STALE_FILE; one writes. The four state symbols
    are distinct so we can read the surviving symbol off the file."""
    spec, h = _write_sample(tmp_path)

    procs = [
        _launch_worker(spec, h, state, 4)
        for state in ("done", "skipped", "blocked", "needs_decision")
    ]
    results = [_collect(p) for p in procs]

    for code, _, err in results:
        assert code == 0, f"a worker crashed: {err}"

    outs = [out for _, out, _ in results]
    ok_lines = [o for o in outs if o.startswith("ok:")]
    err_lines = [o for o in outs if o == "err:STALE_FILE"]
    assert len(ok_lines) == 1, (
        f"exactly one worker should succeed; got {outs}"
    )
    assert len(err_lines) == 3, (
        f"three workers should lose with STALE_FILE; got {outs}"
    )

    # File must reflect exactly one of the four symbols.
    final = spec.read_bytes()
    matches = sum(
        1
        for sym in (b"[x]", b"[-]", b"[!]", b"[?]")
        if sym in final
    )
    assert matches == 1, (
        f"file should contain exactly one updated state symbol; "
        f"got {matches} in {final!r}"
    )


def test_winner_hash_matches_observed_disk_hash(tmp_path: Path) -> None:
    """The ``ok:<hash>`` reported by the winning worker matches the hash
    we compute by re-reading the file. Proves ``write_atomic`` returns
    the hash of bytes actually committed, not pre-write bytes."""
    spec, h = _write_sample(tmp_path)

    p1 = _launch_worker(spec, h, "done", 4)
    p2 = _launch_worker(spec, h, "skipped", 4)
    results = [_collect(p1), _collect(p2)]

    ok_hashes = [
        out.split(":", 1)[1] for _, out, _ in results if out.startswith("ok:")
    ]
    assert len(ok_hashes) == 1
    assert ok_hashes[0] == content_hash(spec.read_bytes())


def test_sequential_subprocess_updates_chain_via_returned_hash(
    tmp_path: Path,
) -> None:
    """Two workers run sequentially (no race): the second one uses the
    first one's returned ``new_content_hash`` and both should succeed.
    Verifies the hash is a stable wire value across process boundaries."""
    spec, h0 = _write_sample(tmp_path)

    p1 = _launch_worker(spec, h0, "done", 4)
    code1, out1, err1 = _collect(p1)
    assert code1 == 0, err1
    assert out1.startswith("ok:")
    h1 = out1.split(":", 1)[1]

    p2 = _launch_worker(spec, h1, "open", 4)
    code2, out2, err2 = _collect(p2)
    assert code2 == 0, err2
    assert out2.startswith("ok:")

    # We've undone our own change — bytes match the source.
    assert spec.read_bytes() == _SAMPLE_BYTES


# ---------------------------------------------------------------------------
# 3. Stress: many sequential writers chain cleanly
# ---------------------------------------------------------------------------


def test_long_chain_of_in_process_updates_round_trips(tmp_path: Path) -> None:
    """20 sequential in-process updates flipping ``open ↔ done`` should
    round-trip to the original bytes on every even iteration. Catches
    accumulation bugs (hash drift, BOM creep, trailing whitespace)
    that single-update tests can't see."""
    spec, h = _write_sample(tmp_path)

    state = "open"
    for _ in range(20):
        state = "done" if state == "open" else "open"
        result = update_task_status(
            spec,
            expected_content_hash=h,
            updates=[UpdateRequest(new_state=state, task_id="#1")],
        )
        assert isinstance(result, Ok), result
        h = result.data.new_content_hash

    # Ended on "open" (20 flips from open is open) — bytes equal source.
    assert state == "open"
    assert spec.read_bytes() == _SAMPLE_BYTES


# ---------------------------------------------------------------------------
# 4. The lock sidecar is reused across runs (no leaked state)
# ---------------------------------------------------------------------------


def test_lock_sidecar_persists_and_is_reusable_across_updates(
    tmp_path: Path,
) -> None:
    """First update creates ``spec.sdd.lock``. A later update sees the
    sidecar already present and reuses it without error. The sidecar
    is not deleted on release (see ``operations/locks.py`` for the
    TOCTOU rationale)."""
    spec, h = _write_sample(tmp_path)
    lock_sidecar = tmp_path / "spec.sdd.lock"

    r1 = update_task_status(
        spec,
        expected_content_hash=h,
        updates=[UpdateRequest(new_state="done", task_id="#1")],
    )
    assert isinstance(r1, Ok)
    assert lock_sidecar.exists()

    r2 = update_task_status(
        spec,
        expected_content_hash=r1.data.new_content_hash,
        updates=[UpdateRequest(new_state="open", task_id="#1")],
    )
    assert isinstance(r2, Ok)
    assert lock_sidecar.exists()


# ---------------------------------------------------------------------------
# 5. Smoke: subprocess harness itself is sane
# ---------------------------------------------------------------------------


def test_worker_reports_task_not_found_when_id_missing(tmp_path: Path) -> None:
    """The worker propagates clean errors from the orchestrator. If this
    breaks, every concurrency test above would fail with confusing
    output — keep a sanity check here."""
    spec, h = _write_sample(tmp_path)

    proc = _launch_worker(spec, h, "done", 999)  # line 999 doesn't exist
    code, out, err = _collect(proc)

    assert code == 0, err
    assert out == "err:TASK_NOT_FOUND"


@pytest.mark.parametrize("state", ["done", "skipped", "blocked", "needs_decision"])
def test_worker_writes_expected_symbol(tmp_path: Path, state: str) -> None:
    """One per state — confirms the worker round-trips state strings to
    the correct on-disk symbols. Indirectly verifies that the
    parser↔mutate symbol mappings agree across process boundaries."""
    spec, h = _write_sample(tmp_path)
    expected_symbol = {
        "done": b"[x]",
        "skipped": b"[-]",
        "blocked": b"[!]",
        "needs_decision": b"[?]",
    }[state]

    proc = _launch_worker(spec, h, state, 4)
    code, out, err = _collect(proc)

    assert code == 0, err
    assert out.startswith("ok:"), out
    assert expected_symbol in spec.read_bytes()
