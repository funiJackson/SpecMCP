"""Tests for the ``specdd-mcp`` CLI (:mod:`specdd_mcp.cli`).

Exercises subcommand dispatch and each handler's behavior + exit code:
``version``, ``bootstrap`` (create / refuse-clobber / --with-app), and
``validate`` (clean / errors / single file / missing / empty). The blocking
``serve`` path is verified only at the dispatch level (handler identity and a
monkeypatched call), never actually started.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from specdd_mcp import __version__, cli

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def test_no_subcommand_defaults_to_serve() -> None:
    args = cli.build_parser().parse_args([])
    assert args.func is cli.cmd_serve


def test_serve_subcommand_routes_to_serve() -> None:
    args = cli.build_parser().parse_args(["serve"])
    assert args.func is cli.cmd_serve


def test_main_dispatches_to_serve_when_no_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[bool] = []
    monkeypatch.setattr(cli, "cmd_serve", lambda _args: (called.append(True), 0)[1])
    assert cli.main([]) == 0
    assert called == [True]


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


def test_version_prints_and_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["version"])
    assert code == 0
    assert capsys.readouterr().out.strip() == __version__


# ---------------------------------------------------------------------------
# bootstrap
# ---------------------------------------------------------------------------


def test_bootstrap_creates_files(tmp_path: Path) -> None:
    code = cli.main(["bootstrap", str(tmp_path)])
    assert code == 0
    assert (tmp_path / ".specdd" / "bootstrap.md").exists()
    assert (tmp_path / ".specdd" / "bootstrap.project.md").exists()
    assert (tmp_path / ".specdd" / "bootstrap.local.md").exists()
    assert (tmp_path / "AGENTS.md").exists()
    assert (tmp_path / "CLAUDE.md").exists()
    # the big one carries real content
    assert "SpecDD Bootstrap" in (tmp_path / ".specdd" / "bootstrap.md").read_text()


def test_bootstrap_with_app_scaffolds_app_spec(tmp_path: Path) -> None:
    code = cli.main(["bootstrap", "--with-app", str(tmp_path)])
    assert code == 0
    app = tmp_path / "app.sdd"
    assert app.exists()
    assert app.read_text().startswith("Spec: ")


def test_bootstrap_refuses_to_clobber(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    agents = tmp_path / "AGENTS.md"
    agents.write_text("DO NOT TOUCH\n")
    code = cli.main(["bootstrap", str(tmp_path)])
    assert code == 0
    assert agents.read_text() == "DO NOT TOUCH\n"  # untouched
    out = capsys.readouterr().out
    assert "skipped  AGENTS.md" in out
    assert "created  CLAUDE.md" in out  # the rest still created


def test_bootstrap_rerun_is_idempotent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["bootstrap", str(tmp_path)])
    capsys.readouterr()  # drain
    code = cli.main(["bootstrap", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "nothing to do" in out


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def test_validate_clean_dir_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "a.sdd").write_text("Spec: A\n\nPurpose:\n  Fine.\n")
    code = cli.main(["validate", str(tmp_path)])
    assert code == 0
    assert "1 clean" in capsys.readouterr().out


def test_validate_dir_with_error_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # duplicate task id is a single-file validation ERROR
    (tmp_path / "bad.sdd").write_text(
        "Spec: Bad\n\nTasks:\n  [ ] #1 one\n  [ ] #1 two\n"
    )
    code = cli.main(["validate", str(tmp_path)])
    assert code == 1
    out = capsys.readouterr().out
    assert "DUPLICATE_TASK_ID" in out
    assert "error" in out


def test_validate_single_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = tmp_path / "a.sdd"
    spec.write_text("Spec: A\n\nPurpose:\n  Fine.\n")
    code = cli.main(["validate", str(spec)])
    assert code == 0
    assert "1 spec(s)" in capsys.readouterr().out


def test_validate_missing_path_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["validate", str(tmp_path / "ghost")])
    assert code == 1
    assert "does not exist" in capsys.readouterr().out


def test_validate_empty_dir_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["validate", str(tmp_path)])
    assert code == 0
    assert "no .sdd files" in capsys.readouterr().out


def test_validate_unparseable_spec_counts_as_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "binary.sdd").write_bytes(b"\x00\x01\x02 not text")
    code = cli.main(["validate", str(tmp_path)])
    assert code == 1
    assert "error" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# install-commands
# ---------------------------------------------------------------------------


def test_install_commands_writes_all_files(tmp_path: Path) -> None:
    code = cli.main(["install-commands", "--dir", str(tmp_path)])
    assert code == 0
    assert (tmp_path / "specc.md").exists()
    assert (tmp_path / "specc" / "audit.md").exists()
    assert (tmp_path / "specc" / "status.md").exists()
    assert (tmp_path / "specc" / "draft.md").exists()


def test_install_commands_refuses_to_clobber(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    existing = tmp_path / "specc.md"
    existing.write_text("MINE\n")
    code = cli.main(["install-commands", "--dir", str(tmp_path)])
    assert code == 0
    assert existing.read_text() == "MINE\n"  # untouched
    out = capsys.readouterr().out
    assert "skipped    specc.md" in out
    assert "installed  specc/audit.md" in out  # the rest still installed


def test_install_commands_force_overwrites(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    existing = tmp_path / "specc.md"
    existing.write_text("MINE\n")
    code = cli.main(["install-commands", "--dir", str(tmp_path), "--force"])
    assert code == 0
    assert existing.read_text() != "MINE\n"  # overwritten with the real command
    assert "overwrote  specc.md" in capsys.readouterr().out


def test_bundled_commands_match_repo_root_source() -> None:
    """Drift guard: the packaged copies under templates/commands/ must be
    byte-identical to the human-editable source under the repo-root commands/.
    If this fails, someone edited one copy and not the other."""
    repo_root = Path(__file__).resolve().parents[1]
    for rel in cli._COMMAND_FILES:
        bundled = cli._command_template(rel)
        source = (repo_root / "commands" / rel).read_text(encoding="utf-8")
        assert bundled == source, f"{rel} drifted between commands/ and templates/"
