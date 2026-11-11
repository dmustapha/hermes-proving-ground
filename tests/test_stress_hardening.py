# File: tests/test_stress_hardening.py
# Hardening from a stress pass: derived calls carry only the argument names their tool needs, cannot blow up their
# output, cannot chmod the sandbox away; a --calls file is bounded and must be UTF-8 text; labels are one inert line
# in the terminal; flags outside their action and bad export paths are refused before a drill; and `sweep` stops
# orphaned drill daemons (found by the exact HERMES_HOME= word in their environment) before removing their roots.
from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterator

import pytest

from proving_ground import cli, matrix, report, runner, safety, testcalls

RECORDER = "http://127.0.0.1:4321/abc123"


@pytest.fixture
def sandbox() -> Iterator[Path]:
    root = Path(tempfile.mkdtemp(prefix=safety.ROOT_PREFIX)).resolve()
    (root / "sandbox" / "seed").mkdir(parents=True)
    yield root / "sandbox"
    shutil.rmtree(root, ignore_errors=True)


def _reasons(tool: str, args: dict, box: Path) -> list[str]:
    return safety.derived_call_reasons(tool, args, box, RECORDER)


def _terminal(command: str, box: Path) -> list[str]:
    return _reasons("terminal", {"command": command.replace("{S}", str(box))}, box)


def _calls_file(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "calls.yaml"
    path.write_text("schema: provingground.testcalls.v1\ncalls:\n" + body, encoding="utf-8")
    return path


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


# --- argument names --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("extra", ["background", "junk", "workdir", "timeout", "pty"])
def test_terminal_call_with_an_extra_argument_name_is_refused(sandbox: Path, extra: str) -> None:
    assert f"grammar:arg-key:{extra}" in _reasons("terminal", {"command": "echo hi", extra: True}, sandbox)


@pytest.mark.parametrize("tool,args", [("terminal", {"command": "echo hi"}),
                                       ("write_file", {"path": "{S}/a.txt", "content": "x"}),
                                       ("read_file", {"path": "{S}/a.txt"}),
                                       ("patch", {"path": "{S}/a.txt", "old_string": "a", "new_string": "b"})])
def test_each_tool_accepts_exactly_its_catalog_argument_names(sandbox: Path, tool: str, args: dict) -> None:
    filled = {k: v.replace("{S}", str(sandbox)) for k, v in args.items()}
    assert _reasons(tool, filled, sandbox) == []
    assert f"grammar:arg-key:offset" in _reasons(tool, {**filled, "offset": 5}, sandbox)


def test_a_background_terminal_call_in_a_calls_file_is_refused_before_any_spawn(tmp_path: Path) -> None:
    path = _calls_file(tmp_path, '  - {id: U01, tool: terminal, subject: s, purpose: p,'
                                 ' args: {command: "echo hi", background: true}}\n')
    with pytest.raises(safety.Refusal) as refused:
        cli.select_templates(argparse.Namespace(calls=str(path), only=None))
    assert refused.value.code == "BAD_CALLS_FILE" and "grammar:arg-key:background" in refused.value.message


# --- output bounds ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("command", ["printf %999999999d 1", "printf %.99999s x", "printf %n", "printf a%nb",
                                     "printf %-10000d 1", "seq 999999999999", "seq 1 0.0001 5", "seq 0 1 20001",
                                     "seq abc", "seq 1 0 5", "seq 1 2 3 4"])
def test_output_blowups_are_refused(sandbox: Path, command: str) -> None:
    assert _terminal(command, sandbox), command


@pytest.mark.parametrize("command", ["printf %5d 1", "printf %.0s 1 2 3", "printf 100%%", "printf %9999d 1",
                                     "seq 1 20000", "seq 20000", "seq 1 2 100", "seq 1.5 3"])
def test_bounded_output_still_passes(sandbox: Path, command: str) -> None:
    assert _terminal(command, sandbox) == [], command


def test_a_yaml_alias_bomb_in_args_is_refused(tmp_path: Path) -> None:
    levels = ["a0: &a0 [x, x, x, x, x, x, x, x, x, x]"]
    levels += [f"a{i}: &a{i} [" + ", ".join([f"*a{i - 1}"] * 10) + "]" for i in range(1, 6)]
    bomb = ("  - id: U09\n    tool: terminal\n    subject: s\n    purpose: p\n    args:\n      command: echo hi\n"
            + "".join(f"      {lvl}\n" for lvl in levels))
    path = _calls_file(tmp_path, bomb)
    started = time.monotonic()
    with pytest.raises(testcalls.TestCallError, match="more than"):
        testcalls.load_user_calls(path, testcalls.load_templates())
    assert time.monotonic() - started < 2.0


def test_an_oversized_calls_file_is_refused(tmp_path: Path) -> None:
    path = _calls_file(tmp_path, "# " + "x" * testcalls.CALLS_FILE_MAX_BYTES + "\n")
    with pytest.raises(testcalls.TestCallError, match="larger than"):
        testcalls.load_user_calls(path, testcalls.load_templates())


# --- file shape and labels -------------------------------------------------------------------------------------

def test_a_non_utf8_calls_file_is_bad_calls_file_not_a_crash(tmp_path: Path) -> None:
    path = tmp_path / "calls.yaml"
    path.write_bytes(b"schema: provingground.testcalls.v1\ncalls: []\n# \xff\xfe\n")
    with pytest.raises(safety.Refusal) as refused:
        cli.select_templates(argparse.Namespace(calls=str(path), only=None))
    assert refused.value.code == "BAD_CALLS_FILE" and "not UTF-8" in refused.value.message


@pytest.mark.parametrize("call_id", ['"U01\\n"', '"U01\\r"', '"U01 "', '"XU01"'])
def test_user_ids_are_matched_whole(tmp_path: Path, call_id: str) -> None:
    path = _calls_file(tmp_path, f'  - {{id: {call_id}, tool: terminal, subject: s, purpose: p, args: {{command: "echo hi"}}}}\n')
    with pytest.raises(testcalls.TestCallError, match="start with U"):
        testcalls.load_user_calls(path, testcalls.load_templates())


def test_a_plain_user_id_still_loads(tmp_path: Path) -> None:
    path = _calls_file(tmp_path, '  - {id: U01, tool: terminal, subject: s, purpose: p, args: {command: "echo hi"}}\n')
    assert testcalls.load_user_calls(path, testcalls.load_templates())[-1].id == "U01"


@pytest.mark.parametrize("field,value", [("subject", '"a\\x1b[31mred"'), ("subject", '"two\\nlines"'),
                                         ("purpose", '"x\\ry"'), ("purpose", '"tab\\there"')])
def test_control_characters_in_subject_or_purpose_are_refused(tmp_path: Path, field: str, value: str) -> None:
    fields = {"subject": "s", "purpose": "p", field: value}
    path = _calls_file(tmp_path, f'  - {{id: U01, tool: terminal, subject: {fields["subject"]}, '
                                 f'purpose: {fields["purpose"]}, args: {{command: "echo hi"}}}}\n')
    with pytest.raises(testcalls.TestCallError, match=f"{field} must be one line"):
        testcalls.load_user_calls(path, testcalls.load_templates())


def test_terminal_table_prints_markup_and_control_characters_literally() -> None:
    from rich.console import Console

    calls = [matrix.CallInfo("U01", "terminal", "[/x] [red]spoof[/red] a\x1b[2Jb", "p")]
    console = Console(width=120, record=True, color_system=None)
    console.print(report._rich_table(calls, ["baseline"], {}))
    text = console.export_text()
    assert "[/x] [red]spoof[/red] a?[2Jb" in text and "\x1b" not in text


def test_terminal_safe_leaves_plain_labels_unchanged() -> None:
    assert report.terminal_safe("TC04 rm -rf build (destructive)") == "TC04 rm -rf build (destructive)"
    assert report.terminal_safe("a\nb\x9bc") == "a?b?c"


# --- chmod -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("special", ["4", "2", "1", "6", "7"])
def test_chmod_with_a_special_bits_digit_is_refused(sandbox: Path, special: str) -> None:
    mode = special + "755"  # composed, so this file never spells a set-id chmod for the install scanner
    assert _terminal(f"chmod {mode} {{S}}/a", sandbox) == [f"grammar:chmod:mode:{mode}"]


@pytest.mark.parametrize("command", ["chmod 000 {S}/a", "chmod 444 {S}/a", "chmod 755 {S}", "chmod 755 {S}/", "chmod 700 {S}/.",
                                     "chmod 644 {S}/seed/..", "chmod 77777 {S}/a"])
def test_chmod_owner_lockout_and_the_sandbox_root_are_refused(sandbox: Path, command: str) -> None:
    assert _terminal(command, sandbox), command


@pytest.mark.parametrize("command", ["chmod 644 {S}/seed/a.txt", "chmod 0600 {S}/a", "chmod 755 {S}/seed"])
def test_chmod_inside_the_sandbox_still_passes(sandbox: Path, command: str) -> None:
    assert _terminal(command, sandbox) == [], command


def test_teardown_removes_a_root_with_a_locked_directory(tmp_path: Path) -> None:
    root = runner.create_drill_root(tmp_path)
    locked = root / "sandbox" / "locked"
    (locked / "inner").mkdir(parents=True)
    (locked / "inner" / "f.txt").write_text("x", encoding="utf-8")
    os.chmod(locked / "inner", 0)
    os.chmod(locked, 0)
    runner.remove_root(root)
    assert not root.exists() and not safety.owner_record_path(root).exists()


# --- export paths, misplaced flags, pairs ----------------------------------------------------------------------

@pytest.mark.parametrize("flag", ["json_out", "html_out", "markdown_out", "write_contract"])
def test_an_export_path_that_is_a_directory_is_refused_before_the_drill(monkeypatch, tmp_path: Path, flag: str) -> None:
    monkeypatch.setattr(cli, "run_matrix", lambda *_a, **_k: pytest.fail("the drill must not start"))
    args = cli._parser_defaults()
    setattr(args, flag, str(tmp_path))
    with pytest.raises(safety.Refusal, match="is a directory"):
        cli.cmd_run(args)


def test_an_export_path_under_an_unwritable_parent_is_refused(tmp_path: Path) -> None:
    locked = tmp_path / "ro"
    locked.mkdir()
    os.chmod(locked, stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(safety.Refusal, match="not a writable directory"):
            cli.check_export_targets(argparse.Namespace(json_out=str(locked / "new" / "x.json")))
    finally:
        os.chmod(locked, stat.S_IRWXU)


def test_an_export_path_in_a_new_subdirectory_of_a_writable_dir_is_accepted(tmp_path: Path) -> None:
    cli.check_export_targets(argparse.Namespace(json_out=str(tmp_path / "a" / "b" / "x.json"), html_out=None))


def _parsed(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    cli.setup_parser(parser)
    return parser.parse_args(list(argv))


@pytest.mark.parametrize("argv,flag", [(("--column", "stack"), "--column"), (("run", "extra"), "an extra argument"),
                                       (("show", "--check"), "--check"), (("warm", "--focus"), "--focus"),
                                       (("diff", "a", "b", "--pairs"), "--pairs"), (("sweep", "--json", "x"), "--json"),
                                       (("show", "--repeat", "3"), "--repeat"), (("run", "--plugins"), "--plugins")])
def test_a_flag_outside_its_action_exits_2_with_a_message(capsys, argv: tuple[str, ...], flag: str) -> None:
    assert cli.handle_cli(_parsed(*argv)) == 2
    out = capsys.readouterr().out
    assert out.startswith("BAD_OPTION: ") and flag in out


@pytest.mark.parametrize("argv", [("show", "TC04", "--column", "stack"), ("show", "--html", "x.html", "--focus"),
                                  ("warm", "--check"), ("lock", "f.yaml", "--fixtures"), ("diff", "a", "b"),
                                  ("--fixtures", "--pairs", "--repeat", "2", "--json", "x.json")])
def test_flags_inside_their_action_are_not_refused(argv: tuple[str, ...]) -> None:
    cli.refuse_misplaced_options(_parsed(*argv))


def test_pairs_with_fewer_than_two_plugins_is_refused_before_warm_up(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "user_config", lambda: {})
    monkeypatch.setattr(cli, "check_live_posture", lambda *_a: "")
    monkeypatch.setattr(cli, "build_settings", lambda _a: argparse.Namespace(temp_base=tmp_path))
    monkeypatch.setattr(cli, "select_subjects", lambda *_a: (["only-one"], []))
    monkeypatch.setattr(cli, "ensure_warm", lambda *_a, **_k: pytest.fail("must refuse before warm-up"))
    with pytest.raises(safety.Refusal, match="at least 2 plugins"):
        cli.run_matrix(argparse.Namespace(pairs=True))


# --- approvals.mode --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["yolo", "none", "never", "auto", "Ask", " SMART-ish "])
def test_unknown_approval_modes_read_as_manual_like_hermes(raw: str) -> None:
    assert safety.normalize_approval_mode(raw) == "manual"
    safety.check_posture({}, raw, None, False)


@pytest.mark.parametrize("raw", ["off", " OFF ", False])
def test_only_off_turns_approvals_off_and_is_refused(raw) -> None:
    with pytest.raises(safety.Refusal) as refused:
        safety.check_posture({}, raw, None, False)
    assert refused.value.code == "REFUSED_APPROVALS_OFF"


def test_known_modes_match_hermes_when_its_source_is_present() -> None:
    source = Path.home() / ".hermes" / "hermes-agent" / "tools" / "approval_context.py"
    if not source.is_file():
        pytest.skip("no local Hermes source")
    import ast
    import re

    found = re.search(r"^_VALID_MODES = (\(.*\))$", source.read_text(encoding="utf-8"), re.M)
    assert found and ast.literal_eval(found.group(1)) == safety.HERMES_APPROVAL_MODES


# --- sweep stops orphaned daemons ------------------------------------------------------------------------------

@pytest.fixture
def fake_processes(monkeypatch):
    """A fake process table (pid -> command plus environment) and a recording _terminate."""
    table: dict[int, str] = {}
    stopped: list[int] = []

    def terminate(pids):
        stopped.extend(pids)
        for pid in pids:
            table.pop(pid, None)

    monkeypatch.setattr(runner, "_ps_env_table", lambda: dict(table))
    monkeypatch.setattr(runner, "_terminate", terminate)
    return table, stopped


def _daemon_text(root: Path) -> str:
    return f"osascript -l JavaScript gateway run --idle-exit PATH=/usr/bin HERMES_HOME={root.resolve()}/home TERM=x"


def test_sweep_stops_a_dead_drills_daemon_then_removes_its_root(tmp_path: Path, fake_processes) -> None:
    table, stopped = fake_processes
    root = runner.create_drill_root(tmp_path)
    safety.write_owner_marker(root, _dead_pid(), time.time())
    table[4242] = _daemon_text(root)
    table[4343] = f"python -m hermes_cli HERMES_HOME={root.resolve()}/homex"  # another home: never matched
    removed, pids = runner.sweep_stale(tmp_path)
    assert [p.resolve() for p in removed] == [root] and pids == [4242] and stopped == [4242]
    assert not root.exists() and not safety.owner_record_path(root).exists() and 4343 in table


def test_sweep_removes_a_root_a_live_daemon_recreated_without_its_marker(tmp_path: Path, fake_processes) -> None:
    table, stopped = fake_processes
    root = runner.create_drill_root(tmp_path)
    runner.remove_root(root)
    (root / "home" / "logs").mkdir(parents=True)  # the daemon's log writes bring the root back, unmarked
    table[5151] = _daemon_text(root)
    removed, pids = runner.sweep_stale(tmp_path)
    assert [p.resolve() for p in removed] == [root] and pids == [5151] and not root.exists()


def test_sweep_leaves_an_unmarked_root_no_process_names(tmp_path: Path, fake_processes) -> None:
    table, stopped = fake_processes
    unmarked = tmp_path / "provingground-abcd1234"
    (unmarked / "home").mkdir(parents=True)
    table[6161] = _daemon_text(tmp_path / "provingground-zzzz9999")
    assert runner.sweep_stale(tmp_path) == ([], []) and unmarked.is_dir() and stopped == []


def test_sweep_never_touches_a_live_drills_root(tmp_path: Path, fake_processes) -> None:
    table, stopped = fake_processes
    root = runner.create_drill_root(tmp_path)  # owned by this live process
    table[7171] = _daemon_text(root)
    try:
        assert runner.sweep_stale(tmp_path) == ([], []) and root.is_dir() and stopped == []
    finally:
        runner.remove_root(root)


def test_sweep_uses_the_sidecar_when_the_root_is_already_gone(tmp_path: Path, fake_processes) -> None:
    table, stopped = fake_processes
    root = runner.create_drill_root(tmp_path)
    safety.write_owner_record(root, _dead_pid(), time.time())
    shutil.rmtree(root)
    table[8181] = _daemon_text(root)
    removed, pids = runner.sweep_stale(tmp_path)
    assert removed == [] and pids == [8181] and not safety.owner_record_path(root).exists()


def test_sweep_repeats_while_a_daemon_recreates_the_root(tmp_path: Path, monkeypatch) -> None:
    root = runner.create_drill_root(tmp_path)
    safety.write_owner_marker(root, _dead_pid(), time.time())
    real_remove, passes = runner.remove_root, []

    def recreating_remove(target: Path) -> None:  # a daemon mid-start escapes the first scan and recreates the root
        real_remove(target)
        if not passes[1:]:
            (target / "home" / "logs").mkdir(parents=True)

    monkeypatch.setattr(runner, "kill_home_processes", lambda home: passes.append(home) or [len(passes)])
    monkeypatch.setattr(runner, "remove_root", recreating_remove)
    removed, pids = runner.sweep_stale(tmp_path)
    assert len(passes) == 2 and pids == [1, 2] and not root.exists()


def test_the_owner_sidecar_records_the_drill_and_its_child(tmp_path: Path) -> None:
    root = runner.create_drill_root(tmp_path)
    try:
        safety.record_child_pid(root, 31337)
        pid, _created, children = safety.read_owner_record(root)
        assert pid == os.getpid() and children == (31337,)
        assert not safety.ROOT_NAME.match(safety.owner_record_path(root).name)
    finally:
        runner.remove_root(root)
    assert not safety.owner_record_path(root).exists() and safety.read_owner_record(root) is None


def test_sweep_command_reports_stopped_processes(monkeypatch, capsys, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "build_settings", lambda _a: argparse.Namespace(temp_base=tmp_path))
    monkeypatch.setattr(runner, "sweep_stale", lambda _base: ([tmp_path / "provingground-abcd1234"], [11, 12]))
    assert cli.cmd_sweep(argparse.Namespace()) == 0
    assert capsys.readouterr().out == "stopped 2 orphaned drill processes\nremoved 1 stale sandbox roots\n"


def test_sweep_kills_a_real_orphan_whose_environment_names_the_root(tmp_path: Path) -> None:
    """A real (harmless) python child standing in for the reparented daemon: env-matched, stopped, root removed."""
    root = runner.create_drill_root(tmp_path)
    env = {**os.environ, "HERMES_HOME": str(root / "home")}
    orphan = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], env=env)
    try:
        safety.write_owner_marker(root, _dead_pid(), time.time())
        deadline = time.monotonic() + 5
        while orphan.pid not in runner._ps_env_table() and time.monotonic() < deadline:
            time.sleep(0.1)
        removed, pids = runner.sweep_stale(tmp_path)
        assert orphan.wait(timeout=10) is not None and orphan.pid in pids and removed and not root.exists()
    finally:
        if orphan.poll() is None:
            orphan.kill()
            orphan.wait()
        runner.remove_root(root)


@pytest.mark.parametrize("key", ["~/x", "~", "/abs"])
def test_seed_names_starting_with_home_or_root_are_refused(tmp_path: Path, key: str) -> None:
    path = _calls_file(tmp_path, f'  - {{id: U01, tool: terminal, subject: s, purpose: p, args: {{command: "echo hi"}},'
                                 f' seed: {{"{key}": "x"}}}}\n')
    with pytest.raises(testcalls.TestCallError, match="sandbox-relative"):
        testcalls.load_user_calls(path, testcalls.load_templates())


def test_a_plain_seed_name_still_loads(tmp_path: Path) -> None:
    path = _calls_file(tmp_path, '  - {id: U01, tool: terminal, subject: s, purpose: p, args: {command: "echo hi"},'
                                 ' seed: {"notes/a.txt": "x"}}\n')
    assert testcalls.load_user_calls(path, testcalls.load_templates())[-1].seed == {"notes/a.txt": "x"}
