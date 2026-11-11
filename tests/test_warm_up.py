# File: tests/test_warm_up.py
# The one-off runtime warm-up: its own long timeout, a one-line notice, a clean refusal when it fails (never a
# drill against a cold cache), and `warm --check`, a read-only preflight for the current temp dir.
from __future__ import annotations

import argparse
import sys
import types

import pytest

from proving_ground import cli, runner, safety


def _interactive(monkeypatch) -> None:
    stub = types.ModuleType("cron.execution_identity")
    stub.current_cron_execution = lambda: None
    monkeypatch.setitem(sys.modules, "cron", types.ModuleType("cron"))
    monkeypatch.setitem(sys.modules, "cron.execution_identity", stub)
    monkeypatch.setattr(cli, "user_config", lambda: {})
    monkeypatch.setattr(cli, "find_hermes", lambda _explicit: cli.PG_SOURCE / "no-hermes-needed")


def _failed_run(settings: runner.RunnerSettings) -> runner.CellRun:
    return runner.CellRun(runner.ColumnSpec("warm-up", ()), "", "", [], [], None, "CELL_TIMEOUT", 900.0,
                          output_tail="installer: connection reset")


def _no_spawn(*_a, **_k):
    raise AssertionError("a child was spawned")


def test_failed_warm_up_refuses_the_drill_cleanly(monkeypatch, capsys) -> None:
    _interactive(monkeypatch)
    monkeypatch.setattr(runner, "is_warm", lambda _s: False)
    monkeypatch.setattr(runner, "warm_up", _failed_run)
    monkeypatch.setattr(runner, "run_drill", _no_spawn)
    code = cli.handle_cli(argparse.Namespace(action="run", fixtures=True, no_color=True, quiet=True))
    out = capsys.readouterr()
    assert code == 2
    assert out.out.startswith("WARMUP_FAILED: Hermes's runtime install did not finish (CELL_TIMEOUT)")
    assert "connection reset" in out.out and "Traceback" not in out.out + out.err
    notice = [line for line in out.err.splitlines() if line.startswith("warm-up:")]
    assert len(notice) == 1 and "650 MB" in notice[0] and "network on" in notice[0]


def test_failed_warm_command_is_a_refusal(monkeypatch, capsys) -> None:
    _interactive(monkeypatch)
    monkeypatch.setattr(runner, "is_warm", lambda _s: False)
    monkeypatch.setattr(runner, "warm_up", _failed_run)
    monkeypatch.setattr(runner, "install_cancellation", lambda: None)
    assert cli.handle_cli(argparse.Namespace(action="warm", check=False)) == 2
    assert "WARMUP_FAILED" in capsys.readouterr().out


def test_warm_up_gets_its_own_long_timeout(monkeypatch, settings) -> None:
    seen = {}

    def fake_column(spec, templates, canary, recorder, column_settings, *, network=False):
        seen.update(timeout=column_settings.cell_timeout_s, network=network)
        return _failed_run(column_settings)

    monkeypatch.setattr(runner, "run_column", fake_column)
    monkeypatch.setattr(runner, "ensure_private_cache", lambda _c: None)
    runner.warm_up(settings)
    assert seen == {"timeout": runner.WARM_TIMEOUT_S, "network": True} and runner.WARM_TIMEOUT_S >= 900


@pytest.mark.parametrize("warm, code, word", [(True, 0, "warm"), (False, 3, "COLD")])
def test_warm_check_reports_the_cache_for_this_temp_dir_and_spawns_nothing(monkeypatch, capsys, warm, code, word):
    _interactive(monkeypatch)
    monkeypatch.setattr(runner, "is_warm", lambda _s: warm)
    monkeypatch.setattr(runner, "warm_up", _no_spawn)
    monkeypatch.setattr(runner, "run_column", _no_spawn)
    assert cli.handle_cli(argparse.Namespace(action="warm", check=True)) == code
    out = capsys.readouterr().out
    settings = cli.build_settings(argparse.Namespace())
    assert out.startswith(f"{word}: runtime cache {settings.warm_cache} (temp dir {settings.temp_base})")


def test_warm_failure_names_the_reason() -> None:
    refusal = runner.warm_failure(_failed_run(None))  # type: ignore[arg-type]
    assert isinstance(refusal, safety.Refusal) and refusal.code == "WARMUP_FAILED"
    assert "CELL_TIMEOUT" in refusal.message and "hermes provingground warm" in refusal.message
