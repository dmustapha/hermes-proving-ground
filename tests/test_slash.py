# File: tests/test_slash.py
# `/provingground` without a live model provider: the handler returns at once, drills in a background thread,
# holds one lock, releases it even when the drill fails, and refuses messaging platforms before anything spawns.
from __future__ import annotations

import sys
import threading
import types

from helpers import fixture_like_matrix

from proving_ground import cli, runner


def _interactive_and_warm(monkeypatch) -> None:
    stub = types.ModuleType("cron.execution_identity")
    stub.current_cron_execution = lambda: None
    monkeypatch.setitem(sys.modules, "cron", types.ModuleType("cron"))
    monkeypatch.setitem(sys.modules, "cron.execution_identity", stub)
    monkeypatch.delenv("HERMES_YOLO_MODE", raising=False)
    monkeypatch.setattr(cli, "user_config", lambda: {"approvals": {"mode": "manual"}})
    monkeypatch.setattr(cli, "find_hermes", lambda _explicit: cli.PG_SOURCE / "no-hermes-needed")
    monkeypatch.setattr(runner, "is_warm", lambda _s: True)


def _join_drill() -> None:
    for thread in threading.enumerate():
        if thread.name == "provingground-drill":
            thread.join(timeout=5)


def test_slash_starts_the_drill_in_the_background_and_saves(monkeypatch) -> None:
    _interactive_and_warm(monkeypatch)
    seen, saved = [], []
    m = fixture_like_matrix()

    def fake_run(args, on_partial=None, platform=None):
        seen.append((args.fixtures, args.quiet, platform, on_partial))
        return m, ""

    monkeypatch.setattr(cli, "run_matrix", fake_run)
    monkeypatch.setattr(cli, "save", saved.append)
    reply = cli.handle_slash("fixtures", platform="cli", session_id="s1")
    _join_drill()
    assert reply.startswith("Drill started in the background") and "model: NOT CALLED" in reply
    assert seen == [(True, True, "cli", None)] and saved == [m]
    assert cli._SLASH_LOCK.acquire(blocking=False)
    cli._SLASH_LOCK.release()


def test_slash_refuses_a_second_drill_while_one_runs(monkeypatch) -> None:
    _interactive_and_warm(monkeypatch)
    monkeypatch.setattr(cli, "run_matrix", lambda *a, **k: (_ for _ in ()).throw(AssertionError("spawned")))
    assert cli._SLASH_LOCK.acquire(blocking=False)
    try:
        assert cli.handle_slash("fixtures", platform="cli").startswith("A drill is already running")
    finally:
        cli._SLASH_LOCK.release()


def test_slash_releases_the_lock_when_the_drill_fails(monkeypatch) -> None:
    _interactive_and_warm(monkeypatch)

    def broken(*_a, **_k):
        raise RuntimeError("child failed")

    monkeypatch.setattr(cli, "run_matrix", broken)
    assert cli.handle_slash("fixtures", platform="cli").startswith("Drill started")
    _join_drill()
    assert cli._SLASH_LOCK.acquire(blocking=False)
    cli._SLASH_LOCK.release()


def test_slash_refuses_messaging_platforms_before_spawning(monkeypatch) -> None:
    _interactive_and_warm(monkeypatch)
    monkeypatch.setattr(cli, "run_matrix", lambda *a, **k: (_ for _ in ()).throw(AssertionError("spawned")))
    assert cli.handle_slash("fixtures", platform="telegram").startswith("REFUSED_PLATFORM")


def test_slash_show_without_a_report_says_how_to_make_one(monkeypatch) -> None:
    def none_yet():
        from proving_ground import safety
        raise safety.Refusal("NO_REPORT", "no report yet")

    monkeypatch.setattr(cli, "load_latest", none_yet)
    assert cli.handle_slash("show").startswith("NO_REPORT")


def test_slash_show_during_the_first_drill_says_it_is_running(monkeypatch) -> None:
    def none_yet():
        from proving_ground import safety
        raise safety.Refusal("NO_REPORT", "no report yet")

    monkeypatch.setattr(cli, "load_latest", none_yet)
    assert cli._SLASH_LOCK.acquire(blocking=False)
    try:
        assert cli.handle_slash("show") == cli.DRILL_RUNNING
    finally:
        cli._SLASH_LOCK.release()


def test_slash_show_during_a_later_drill_labels_the_report_as_the_previous_one(monkeypatch) -> None:
    monkeypatch.setattr(cli, "load_latest", fixture_like_matrix)
    assert cli._SLASH_LOCK.acquire(blocking=False)
    try:
        text = cli.handle_slash("show")
    finally:
        cli._SLASH_LOCK.release()
    assert text.startswith("A drill is running in the background; the report below is the previous one")
    assert "cells with a recorded effect" in text
