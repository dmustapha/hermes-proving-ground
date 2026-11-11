# PROVINGGROUND_KEEP_LOGS output gets the warm cache's checks: the logs dir is not a symlink, is ours,
# is 0700; each copy is a new 0600 file that never follows or overwrites an existing name.
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from proving_ground import runner, safety


@pytest.fixture()
def column_root(tmp_path: Path) -> Path:
    root = tmp_path / "provingground-abcd1234"
    (root / "home" / safety.STATE_DIRNAME).mkdir(parents=True)
    (root / "child.log").write_text("child output\n", encoding="utf-8")
    (root / "home" / safety.STATE_DIRNAME / "events.jsonl").write_text("{}\n", encoding="utf-8")
    return root


def test_logs_are_kept_privately(column_root: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(runner.KEEP_LOGS_ENV, "1")
    base = tmp_path / "base"
    base.mkdir()
    runner._keep_logs(column_root, runner.ColumnSpec("solo:x", ()), base)
    logs = base / "provingground-logs"
    assert stat.S_IMODE(logs.lstat().st_mode) == 0o700
    kept = sorted(logs.iterdir())
    assert [p.name for p in kept] == ["solo_x-provingground-abcd1234.events.jsonl", "solo_x-provingground-abcd1234.log"]
    assert all(stat.S_IMODE(p.lstat().st_mode) == 0o600 for p in kept)


def test_a_symlinked_logs_dir_is_refused(column_root: Path, tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv(runner.KEEP_LOGS_ENV, "1")
    base, elsewhere = tmp_path / "base", tmp_path / "elsewhere"
    base.mkdir(), elsewhere.mkdir()
    (base / "provingground-logs").symlink_to(elsewhere, target_is_directory=True)
    runner._keep_logs(column_root, runner.ColumnSpec("baseline", ()), base)
    assert list(elsewhere.iterdir()) == [] and "is a symlink" in capsys.readouterr().err


def test_an_open_logs_dir_is_tightened_to_0700(column_root: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(runner.KEEP_LOGS_ENV, "1")
    logs = tmp_path / "provingground-logs"
    logs.mkdir()
    os.chmod(logs, 0o777)
    runner._keep_logs(column_root, runner.ColumnSpec("baseline", ()), tmp_path)
    assert stat.S_IMODE(logs.lstat().st_mode) == 0o700


def test_a_planted_symlink_at_the_destination_is_never_followed(column_root: Path, tmp_path: Path,
                                                                 monkeypatch, capsys) -> None:
    monkeypatch.setenv(runner.KEEP_LOGS_ENV, "1")
    logs, victim = tmp_path / "provingground-logs", tmp_path / "victim.rc"
    logs.mkdir(mode=0o700)
    victim.write_text("original\n", encoding="utf-8")
    (logs / "baseline-provingground-abcd1234.log").symlink_to(victim)
    runner._keep_logs(column_root, runner.ColumnSpec("baseline", ()), tmp_path)
    assert victim.read_text(encoding="utf-8") == "original\n" and "logs not kept" in capsys.readouterr().err


def test_a_foreign_owner_is_refused(tmp_path: Path, monkeypatch) -> None:
    logs = tmp_path / "provingground-logs"
    logs.mkdir(mode=0o700)
    other_uid = os.getuid() + 1
    monkeypatch.setattr(runner.os, "getuid", lambda: other_uid)
    with pytest.raises(safety.Refusal, match="belongs to another user"):
        runner.ensure_private_dir(logs, "logs dir")
