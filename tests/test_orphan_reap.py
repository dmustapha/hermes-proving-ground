# A daemon that a killed child asked launchd for can start after its column's cleanup scan; once the root is gone,
# the end-of-drill reap stops it, and it never touches a process whose drill root still exists.
from __future__ import annotations

import signal
from pathlib import Path

from proving_ground import runner


def test_reap_stops_only_processes_whose_drill_root_is_gone(monkeypatch, tmp_path) -> None:
    live_root = tmp_path / "provingground-live0000"
    (live_root / "home").mkdir(parents=True)
    homes = {101: tmp_path / "provingground-gone0000" / "home", 102: live_root / "home", 103: None}
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(runner, "_ps_candidates", lambda: list(homes))
    monkeypatch.setattr(runner, "_drill_home_of", lambda pid, _base: homes[pid])
    monkeypatch.setattr(runner, "_signal_all", lambda pids, sig: sent.extend((p, sig) for p in pids))
    monkeypatch.setattr(runner.safety, "pid_alive", lambda _pid: False)
    assert runner.reap_orphans(tmp_path) == [101]
    assert sent == [(101, signal.SIGTERM)]


def test_drill_home_is_read_only_from_a_pg_root_shaped_hermes_home(monkeypatch, tmp_path) -> None:
    class Done:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    env = f"python3 -m hermes_cli.main HERMES_HOME={tmp_path}/provingground-ab12cd34/home TERM=xterm"
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: Done(env))
    assert runner._drill_home_of(1, tmp_path) == Path(f"{tmp_path}/provingground-ab12cd34/home")
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: Done("HERMES_HOME=/Users/someone/.hermes"))
    assert runner._drill_home_of(1, tmp_path) is None


def test_the_drill_reaps_before_it_counts_leftovers() -> None:
    source = (Path(__file__).resolve().parent.parent / "proving_ground" / "cli.py").read_text(encoding="utf-8")
    assert source.index("runner.reap_orphans(settings.temp_base)") < source.index("runner.leftovers(settings.temp_base)")


def test_leftovers_count_only_this_drills_roots_and_processes(monkeypatch, tmp_path) -> None:
    import os

    ours, theirs = tmp_path / "provingground-ours0000", tmp_path / "provingground-thrs0000"
    for root, pid in ((ours, os.getpid()), (theirs, os.getpid() + 100000)):
        (root / "home").mkdir(parents=True)
        runner.safety.write_owner_marker(root, pid, 0.0)
    homes = {1: ours / "home", 2: theirs / "home", 3: theirs / "home"}
    monkeypatch.setattr(runner, "_ps_candidates", lambda: list(homes))
    monkeypatch.setattr(runner, "_drill_home_of", lambda pid, _base: homes[pid])
    assert runner.leftovers(tmp_path) == (1, 1)
