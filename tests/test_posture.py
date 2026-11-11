# File: tests/test_posture.py
# I5: refuse under YOLO, approvals off (also YAML 1.1 False), cron/unattended, messaging platforms; zero children spawned.
from __future__ import annotations

import argparse
import sys
import types

import pytest

from proving_ground import cli, runner, safety


@pytest.mark.parametrize("env, mode, platform, cron, code", [
    ({"HERMES_YOLO_MODE": "true"}, "manual", None, False, "REFUSED_YOLO"),
    ({}, "off", None, False, "REFUSED_APPROVALS_OFF"),
    ({}, False, None, False, "REFUSED_APPROVALS_OFF"),
    ({}, "OFF", None, False, "REFUSED_APPROVALS_OFF"),
    ({}, " off ", None, False, "REFUSED_APPROVALS_OFF"),
    ({}, "manual", None, True, "REFUSED_UNATTENDED"),
    ({}, "manual", "telegram", False, "REFUSED_PLATFORM"),
])
def test_posture_refusals(env, mode, platform, cron, code) -> None:
    with pytest.raises(safety.Refusal) as excinfo:
        safety.check_posture(env, mode, platform, cron)
    assert excinfo.value.code == code


@pytest.mark.parametrize("raw, normalized", [(False, "off"), (True, "manual"), (None, "manual"),
                                             ("Smart", "smart"), ("", "manual")])
def test_mode_normalization_mirrors_hermes(raw, normalized) -> None:
    assert safety.normalize_approval_mode(raw) == normalized


def test_interactive_posture_is_allowed() -> None:
    safety.check_posture({}, "manual", "cli", False)


def _no_cron(monkeypatch) -> None:
    stub = types.ModuleType("cron.execution_identity")
    stub.current_cron_execution = lambda: None
    monkeypatch.setitem(sys.modules, "cron", types.ModuleType("cron"))
    monkeypatch.setitem(sys.modules, "cron.execution_identity", stub)


def _boom(*_a, **_k):
    raise AssertionError("a child was spawned")


@pytest.mark.parametrize("mode", ["off", False])
def test_refusal_spawns_nothing(monkeypatch, mode) -> None:
    _no_cron(monkeypatch)
    monkeypatch.setattr(cli, "user_config", lambda: {"approvals": {"mode": mode}})
    monkeypatch.setattr(runner, "run_column", _boom)
    assert cli.handle_cli(argparse.Namespace(action="run", fixtures=True, no_color=True, quiet=True)) == 2


def test_warm_refuses_under_yolo(monkeypatch) -> None:
    _no_cron(monkeypatch)
    monkeypatch.setenv("HERMES_YOLO_MODE", "1")
    monkeypatch.setattr(cli, "user_config", lambda: {})
    monkeypatch.setattr(runner, "warm_up", _boom)
    monkeypatch.setattr(runner, "run_column", _boom)
    assert cli.handle_cli(argparse.Namespace(action="warm")) == 2


def test_slash_refuses_under_yolo_and_never_warms(monkeypatch) -> None:
    _no_cron(monkeypatch)
    monkeypatch.setattr(cli, "user_config", lambda: {})
    monkeypatch.setattr(runner, "warm_up", _boom)
    monkeypatch.setattr(runner, "run_column", _boom)
    monkeypatch.setenv("HERMES_YOLO_MODE", "1")
    assert cli.handle_slash("fixtures", platform="cli").startswith("REFUSED_YOLO")
    monkeypatch.delenv("HERMES_YOLO_MODE")
    # pure tier: CI has no hermes executable, and the slash path must reach the warm check without one
    monkeypatch.setattr(cli, "find_hermes", lambda _explicit: cli.PG_SOURCE / "no-hermes-needed")
    monkeypatch.setattr(runner, "is_warm", lambda _s: False)
    assert cli.handle_slash("fixtures", platform="cli").startswith("WARM_UP_NEEDED")
