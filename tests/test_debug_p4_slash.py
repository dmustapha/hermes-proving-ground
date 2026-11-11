# Debug P4 M-01 and M-04: a failed slash drill is recorded, so `show` reports "last drill failed: <reason>" instead of
# serving the previous report as current; the lock is released when the drill thread cannot start; and the slash path
# registers atexit cleanup of its live roots and child groups (signal handlers cannot be installed off the main thread).
from __future__ import annotations

import argparse
import threading

import pytest
from helpers import fixture_like_matrix

from proving_ground import cli, runner, safety
from test_slash import _interactive_and_warm, _join_drill


def _fail_with(monkeypatch, exc: BaseException) -> None:
    def broken(*_a, **_k):
        raise exc

    monkeypatch.setattr(cli, "run_matrix", broken)


def test_a_failed_drill_is_reported_by_show_ahead_of_the_stale_report(monkeypatch) -> None:
    _interactive_and_warm(monkeypatch)
    cli.save(fixture_like_matrix())
    _fail_with(monkeypatch, safety.Refusal("SANDBOX_UNSUPPORTED", "warm cache belongs to another user"))
    assert cli.handle_slash("fixtures", platform="cli").startswith("Drill started")
    _join_drill()
    shown = cli.handle_slash("show")
    assert shown.startswith("last drill failed: SANDBOX_UNSUPPORTED: warm cache belongs to another user")
    assert "the report below is from the previous successful drill" in shown


def test_show_without_any_report_still_names_the_failure(monkeypatch) -> None:
    _interactive_and_warm(monkeypatch)
    _fail_with(monkeypatch, RuntimeError("child failed"))
    cli.handle_slash("fixtures", platform="cli")
    _join_drill()
    shown = cli.handle_slash("show")
    assert shown.startswith("last drill failed: RuntimeError: child failed") and "NO_REPORT" in shown


def test_cli_show_prints_the_failure_too(monkeypatch, capsys) -> None:
    cli.save(fixture_like_matrix())
    cli.record_drill_failure(RuntimeError("boom"))
    cli.cmd_show(argparse.Namespace(call=None, column=None))
    assert capsys.readouterr().out.startswith("last drill failed: RuntimeError: boom")


def test_a_later_successful_drill_clears_the_failure(monkeypatch) -> None:
    _interactive_and_warm(monkeypatch)
    cli.record_drill_failure(RuntimeError("old"))
    m = fixture_like_matrix()
    monkeypatch.setattr(cli, "run_matrix", lambda *a, **k: (m, ""))
    cli.handle_slash("fixtures", platform="cli")
    _join_drill()
    assert cli.last_drill_failure() is None
    assert not cli.handle_slash("show").startswith("last drill failed")


def test_the_lock_is_released_when_the_thread_cannot_start(monkeypatch) -> None:
    _interactive_and_warm(monkeypatch)
    monkeypatch.setattr(cli, "run_matrix", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))

    def no_threads(self) -> None:
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", no_threads)
    with pytest.raises(RuntimeError, match="can't start new thread"):
        cli.handle_slash("fixtures", platform="cli")
    assert cli._SLASH_LOCK.acquire(blocking=False)
    cli._SLASH_LOCK.release()


def test_the_slash_path_registers_atexit_cleanup_once(monkeypatch) -> None:
    _interactive_and_warm(monkeypatch)
    registered = []
    monkeypatch.setattr(cli.atexit, "register", registered.append)
    monkeypatch.setattr(cli, "_ATEXIT_REGISTERED", threading.Event())
    m = fixture_like_matrix()
    monkeypatch.setattr(cli, "run_matrix", lambda *a, **k: (m, ""))
    for _ in range(2):
        cli.handle_slash("fixtures", platform="cli")
        _join_drill()
    assert registered == [runner.cancel_everything]
