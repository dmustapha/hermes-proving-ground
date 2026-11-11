# File: tests/test_cleanup.py
# I8: interrupts and crashes leave no child, daemon or temp root behind; the sweep only deletes what PG created.
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from proving_ground import runner, safety

ROOT = Path(__file__).resolve().parent.parent
DRIVER = ("import sys; sys.path.insert(0, sys.argv[1]); from proving_ground import cli; "
          "import argparse; cli.handle_cli(argparse.Namespace(action='run', fixtures=True, no_color=True, quiet=True, "
          "json_out=None, markdown_out=None, html_out=None, jobs=3, timeout=120.0, hermes_bin=None, without=None, only=None))")


def live_roots(base: Path) -> list[Path]:
    return [p for p in base.glob(safety.ROOT_PREFIX + "*") if safety.ROOT_NAME.match(p.name)]


def driver_roots(base: Path, driver_pid: int) -> set[Path]:
    """The roots this driver created (its pid in the owner marker): another drill in the same temp dir is not ours."""
    return {r for r in live_roots(base) if (safety.read_owner_marker(r) or (0, 0))[0] == driver_pid}


def leftover_processes(base: Path, roots: set[Path]) -> list[int]:
    """Processes still naming one of *roots* as their HERMES_HOME, from one process-table scan."""
    return [pid for pid, home in runner._drill_homes(base).items() if home.parent in roots]


def wait_for_parallel_columns(base: Path, proc: subprocess.Popen, timeout_s: float = 120.0) -> set[Path]:
    """Signal only once the driver has two child Hermes columns running at the same time (the baseline is done and
    the parallel wave is in flight), so queued and in-flight columns both exist, however fast the machine is.
    Returns every root the driver was seen to create, so the leftover check looks at this drill only."""
    deadline, seen = time.monotonic() + timeout_s, set()
    while time.monotonic() < deadline:
        assert proc.poll() is None, "the drill finished before any column was in flight"
        mine = driver_roots(base, proc.pid)
        seen |= mine
        if len([r for r in mine if (r / "child.log").exists()]) >= 2:
            return seen
        time.sleep(0.2)
    raise AssertionError("no two columns were in flight within the timeout")


def test_stale_root_with_dead_pid_is_swept(settings) -> None:
    root = runner.create_drill_root(settings.temp_base)
    safety.write_owner_marker(root, 999999, time.time())
    assert root in [p.resolve() for p in runner.sweep_stale_roots(settings.temp_base)]
    assert not root.exists()


def test_unowned_directories_are_never_swept(settings) -> None:
    lookalikes = [settings.temp_base / "provingground-workspace", settings.temp_base / "provingground-abcd1234"]
    for path in lookalikes:
        path.mkdir(exist_ok=True)
    unmarked = runner.create_drill_root(settings.temp_base)
    (unmarked / safety.OWNER_MARKER).unlink()
    try:
        runner.sweep_stale_roots(settings.temp_base)
        assert all(p.is_dir() for p in lookalikes + [unmarked])
    finally:
        for path in lookalikes:
            path.rmdir()
        shutil.rmtree(unmarked)


def test_root_names_are_exactly_eight_mkdtemp_chars() -> None:
    assert safety.ROOT_NAME.match("provingground-ab_12cd3")
    for name in ("provingground-workspace", "provingground-unittest0", "provingground-ABCD1234", "provingground-abc"):
        assert not safety.ROOT_NAME.match(name), name


def test_ps_failure_never_skips_root_removal(settings, monkeypatch) -> None:
    def hang(*_a, **_k):
        raise subprocess.TimeoutExpired("ps", 15)

    monkeypatch.setattr(runner.subprocess, "run", hang)
    assert runner._ps_env_table() == {}
    root = runner.create_drill_root(settings.temp_base)

    def broken_kill(_home):
        raise subprocess.TimeoutExpired("ps", 15)

    monkeypatch.setattr(runner, "kill_home_processes", broken_kill)
    with pytest.raises(subprocess.TimeoutExpired):
        runner._cleanup_root(root, runner.ColumnSpec("baseline", ()), settings.temp_base)
    assert not root.exists()


@pytest.mark.hermes
@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_signal_mid_drill_stops_promptly_and_leaves_nothing(settings, sig) -> None:
    proc = subprocess.Popen([sys.executable, "-c", DRIVER, str(ROOT)], start_new_session=True)
    seen = wait_for_parallel_columns(settings.temp_base, proc)
    seen |= driver_roots(settings.temp_base, proc.pid)
    os.killpg(proc.pid, sig)
    sent = time.monotonic()
    proc.wait(timeout=60)
    assert time.monotonic() - sent < 15, "the driver kept running queued columns after the signal"
    deadline = time.monotonic() + 10
    while leftover_processes(settings.temp_base, seen) and time.monotonic() < deadline:
        time.sleep(0.5)
    assert leftover_processes(settings.temp_base, seen) == []
    assert driver_roots(settings.temp_base, proc.pid) == set() and not any(r.exists() for r in seen)
