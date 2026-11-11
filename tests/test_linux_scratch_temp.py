# File: tests/test_linux_scratch_temp.py
# On Linux with TMPDIR unset, Hermes's boot points the temp dir at <HERMES_HOME>/cache/scratch
# (hermes_bootstrap.py:455-462). That is not a system temp base, so the drill uses the first existing system temp
# base instead, and tempfile follows it: the warm cache, the roots and the sweeps agree on one directory.
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import pytest

from proving_ground import cli, safety


@pytest.fixture()
def hermes_scratch_tmp(monkeypatch, tmp_path):
    home = tmp_path / "hermes-home"
    scratch = home / "cache" / "scratch"
    scratch.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    monkeypatch.setattr(cli, "find_hermes", lambda _explicit: Path("/bin/sh"))
    return scratch


def test_hermes_scratch_temp_dir_falls_back_to_the_first_system_temp_base(hermes_scratch_tmp) -> None:
    settings = cli.build_settings(argparse.Namespace())
    first = next(Path(b) for b in safety.TEMP_BASES if Path(b).is_dir())
    assert settings.temp_base == first.resolve()
    assert settings.warm_cache == first.resolve() / "provingground-cache" / "installs"
    assert Path(tempfile.gettempdir()).resolve() == settings.temp_base  # roots and sweeps look at the same place


def test_scratch_named_by_hermes_marker_env_also_falls_back(monkeypatch, tmp_path) -> None:
    marked = tmp_path / "elsewhere" / "scratch"
    marked.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "other-home"))
    monkeypatch.setenv("HERMES_SCRATCH_DIR", str(marked))
    monkeypatch.setattr(tempfile, "tempdir", str(marked))
    assert safety.under_temp_base(cli.drill_temp_base())


def test_a_user_set_temp_dir_is_kept(monkeypatch) -> None:
    monkeypatch.setattr(tempfile, "tempdir", None)
    expected = Path(tempfile.gettempdir()).resolve()
    assert cli.drill_temp_base() == expected and Path(tempfile.gettempdir()).resolve() == expected
