# A daemon that a killed child asked launchd for can start after its column's cleanup scan; once the root is gone,
# the end-of-drill reap stops it, and it never touches a process whose drill root still exists.
from __future__ import annotations

import signal
from pathlib import Path

from proving_ground import runner


def test_reap_stops_only_processes_whose_drill_root_is_gone(monkeypatch, tmp_path) -> None:
    live_root = tmp_path / "provingground-live0000"
    (live_root / "home").mkdir(parents=True)
    table = {101: f"python3 HERMES_HOME={tmp_path}/provingground-gone0000/home",
             102: f"python3 HERMES_HOME={live_root}/home", 103: "python3 HERMES_HOME=/Users/someone/.hermes"}
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(runner, "_ps_env_table", lambda: dict(table))
    monkeypatch.setattr(runner, "_signal_all", lambda pids, sig: sent.extend((p, sig) for p in pids))
    monkeypatch.setattr(runner.safety, "pid_alive", lambda _pid: False)
    assert runner.reap_orphans(tmp_path) == [101]
    assert sent == [(101, signal.SIGTERM)]


def test_drill_home_is_read_only_from_a_pg_root_shaped_hermes_home(tmp_path) -> None:
    env = f"python3 -m hermes_cli.main HERMES_HOME={tmp_path}/provingground-ab12cd34/home TERM=xterm"
    assert runner.drill_home_in(env, tmp_path) == Path(f"{tmp_path}/provingground-ab12cd34/home")
    assert runner.drill_home_in("HERMES_HOME=/Users/someone/.hermes", tmp_path) is None
    assert runner.drill_home_in(f"XHERMES_HOME={tmp_path}/provingground-ab12cd34/home", tmp_path) is None
    assert runner.drill_home_in(f"HERMES_HOME={tmp_path}/provingground-ab12cd34/home2", tmp_path) is None


def test_one_cleanup_reads_the_process_table_once(monkeypatch, tmp_path) -> None:
    home = tmp_path / "provingground-ab12cd34" / "home"
    lines = "\n".join([f"{n} python3 -m hermes_cli.main HERMES_HOME={tmp_path}/provingground-zz{n:06d}/home"
                       for n in range(200, 230)] + [f"77 python3 -m hermes_cli.main HERMES_HOME={home}"])
    calls: list[list[str]] = []

    class Done:
        stdout = lines

    def run(argv, **_kwargs):
        calls.append(list(argv))
        return Done()

    monkeypatch.setattr(runner.subprocess, "run", run)
    monkeypatch.setattr(runner, "_signal_all", lambda *_a: None)
    monkeypatch.setattr(runner.safety, "pid_alive", lambda _pid: False)
    assert runner.kill_home_processes(home) == [77]
    assert calls == [list(runner.PS_ENV_ARGV)]


def test_home_match_is_one_whole_environment_word(tmp_path) -> None:
    home = tmp_path / "provingground-ab12cd34" / "home"
    assert runner.names_home(f"python HERMES_HOME={home} TERM=x", home)
    assert runner.names_home(f"python HERMES_HOME={home}", home)
    assert not runner.names_home(f"python HERMES_HOME={home}-other TERM=x", home)
    assert not runner.names_home(f"python OLD_HERMES_HOME={home}", home)


def test_ps_failure_reads_as_no_processes(monkeypatch) -> None:
    def broken(*_a, **_k):
        raise OSError("ps not found")

    monkeypatch.setattr(runner.subprocess, "run", broken)
    assert runner._ps_env_table() == {}


def test_the_drill_reaps_before_it_counts_leftovers() -> None:
    source = (Path(__file__).resolve().parent.parent / "proving_ground" / "cli.py").read_text(encoding="utf-8")
    assert source.index("runner.reap_orphans(settings.temp_base)") < source.index("runner.leftovers(settings.temp_base)")


def test_leftovers_count_only_this_drills_roots_and_processes(monkeypatch, tmp_path) -> None:
    import os

    ours, theirs = tmp_path / "provingground-ours0000", tmp_path / "provingground-thrs0000"
    for root, pid in ((ours, os.getpid()), (theirs, os.getpid() + 100000)):
        (root / "home").mkdir(parents=True)
        runner.safety.write_owner_marker(root, pid, 0.0)
    table = {1: f"python HERMES_HOME={ours}/home", 2: f"python HERMES_HOME={theirs}/home",
             3: f"hermes HERMES_HOME={theirs}/home"}
    monkeypatch.setattr(runner, "_ps_env_table", lambda: dict(table))
    assert runner.leftovers(tmp_path) == (1, 1)
