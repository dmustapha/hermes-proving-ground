# Children run Hermes's own interpreter command with -B added, so a drill
# writes no bytecode into the Hermes tree; the launcher's -I isolation is kept.
from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from proving_ground import runner

RUNTIME = ["/opt/hermes/python3", "-I", "-c", "import hermes_bootstrap"]


def test_runtime_command_gains_dash_b_and_keeps_isolation() -> None:
    prefix = runner.parse_runtime_command(json.dumps(RUNTIME) + "\n")
    assert prefix == ("/opt/hermes/python3", "-B", "-I", "-c", "import hermes_bootstrap")


@pytest.mark.parametrize("stdout", ["", "usage: hermes ...", json.dumps({"argv": RUNTIME}), json.dumps(["python3"]),
                                    json.dumps(["/x/python3", "-c", "pass"])])
def test_unusable_runtime_command_is_refused(stdout: str) -> None:
    assert runner.parse_runtime_command(stdout) is None


def test_child_argv_starts_with_the_prefix() -> None:
    argv = runner.child_argv(("/opt/hermes/python3", "-B", "-I", "-c", "code"), 10)
    assert argv[:5] == ["/opt/hermes/python3", "-B", "-I", "-c", "code"] and argv[5] == "chat"


def _fake_launcher(tmp_path: Path, body: str) -> Path:
    exe = tmp_path / "hermes"
    exe.write_text("#!/bin/sh\n" + body)
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return exe


def _settings(tmp_path: Path, exe: Path) -> runner.RunnerSettings:
    return runner.RunnerSettings(exe, tmp_path, tmp_path / "cache" / "installs", tmp_path)


def test_launch_prefix_asks_the_launcher_and_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "_PREFIXES", {})
    good = _fake_launcher(tmp_path, f"[ \"$1\" = --print-runtime-command ] && echo '{json.dumps(RUNTIME)}'\n")
    assert runner.launch_prefix(_settings(tmp_path, good))[:3] == ("/opt/hermes/python3", "-B", "-I")
    (tmp_path / "old").mkdir()
    old = _fake_launcher(tmp_path / "old", "echo 'unknown option' >&2; exit 2\n")
    assert runner.launch_prefix(_settings(tmp_path, old)) == (str(old),)


@pytest.mark.hermes
def test_real_launcher_resolves_to_its_interpreter_with_dash_b(settings) -> None:
    prefix = runner.launch_prefix(settings)
    assert prefix[1:3] == ("-B", "-I") and Path(prefix[0]).name.startswith("python")
