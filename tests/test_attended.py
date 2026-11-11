# File: tests/test_attended.py
# `--attended`: refusals before any spawn, the attended child's config and argv, classification from recorded
# events, and a real-Hermes pty run where this TEST HARNESS (never PROVING GROUND) plays the person at the terminal.
from __future__ import annotations

import argparse
import ast
import json
import os
import pty
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

import pytest

from proving_ground import attended, cli, runner, safety, testcalls

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "proving_ground" / "attended.py"


class _Tty:
    def __init__(self, tty: bool) -> None:
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


def _no_cron(monkeypatch) -> None:
    stub = types.ModuleType("cron.execution_identity")
    stub.current_cron_execution = lambda: None
    monkeypatch.setitem(sys.modules, "cron", types.ModuleType("cron"))
    monkeypatch.setitem(sys.modules, "cron.execution_identity", stub)


def _boom(*_a, **_k):
    raise AssertionError("a child was spawned")


def _clean_env() -> dict[str, str]:
    return {"PATH": "/usr/bin"}


@pytest.mark.parametrize("env, stdin, stdout, code", [
    ({}, False, True, "REFUSED_NOT_A_TTY"),
    ({}, True, False, "REFUSED_NOT_A_TTY"),
    ({"HERMES_SESSION_PLATFORM": "telegram"}, True, True, "REFUSED_PLATFORM"),
    ({"HERMES_GATEWAY_SESSION": "1"}, True, True, "REFUSED_UNATTENDED"),
    ({"HERMES_CRON_SESSION": "1"}, True, True, "REFUSED_UNATTENDED"),
    ({"HERMES_SINGLE_QUERY_SESSION": "1"}, True, True, "REFUSED_UNATTENDED"),
])
def test_attended_refusals(env, stdin, stdout, code) -> None:
    with pytest.raises(safety.Refusal) as refusal:
        attended.check_attended({**_clean_env(), **env}, _Tty(stdin), _Tty(stdout))
    assert refusal.value.code == code


def test_a_real_terminal_is_allowed() -> None:
    attended.check_attended({**_clean_env(), "HERMES_SESSION_PLATFORM": "cli"}, _Tty(True), _Tty(True))


def test_cli_refuses_without_a_tty_and_spawns_nothing(monkeypatch, capsys) -> None:
    monkeypatch.setattr(runner, "run_column", _boom)
    monkeypatch.setattr(attended, "run_attended", _boom)
    monkeypatch.setattr(sys, "stdin", _Tty(False))
    args = argparse.Namespace(action="run", fixtures=True, attended=True, no_color=True, quiet=True)
    assert cli.handle_cli(args) == 2
    assert capsys.readouterr().out.startswith("REFUSED_NOT_A_TTY")


@pytest.mark.parametrize("env, mode, code", [({"HERMES_YOLO_MODE": "1"}, "manual", "REFUSED_YOLO"),
                                             ({}, False, "REFUSED_APPROVALS_OFF"), ({}, "off", "REFUSED_APPROVALS_OFF")])
def test_cli_refuses_yolo_and_approvals_off_on_a_tty(monkeypatch, capsys, env, mode, code) -> None:
    _no_cron(monkeypatch)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(cli, "user_config", lambda: {"approvals": {"mode": mode}})
    monkeypatch.setattr(attended, "check_attended", lambda *_a, **_k: None)
    monkeypatch.setattr(runner, "run_column", _boom)
    monkeypatch.setattr(attended, "run_attended", _boom)
    args = argparse.Namespace(action="run", fixtures=True, attended=True, no_color=True, quiet=True)
    assert cli.handle_cli(args) == 2
    assert capsys.readouterr().out.startswith(code)


def test_cli_refuses_under_cron(monkeypatch, capsys) -> None:
    stub = types.ModuleType("cron.execution_identity")
    stub.current_cron_execution = lambda: object()
    monkeypatch.setitem(sys.modules, "cron", types.ModuleType("cron"))
    monkeypatch.setitem(sys.modules, "cron.execution_identity", stub)
    monkeypatch.setattr(cli, "user_config", lambda: {})
    monkeypatch.setattr(attended, "check_attended", lambda *_a, **_k: None)
    monkeypatch.setattr(attended, "run_attended", _boom)
    args = argparse.Namespace(action="run", fixtures=True, attended=True, no_color=True, quiet=True)
    assert cli.handle_cli(args) == 2
    assert capsys.readouterr().out.startswith("REFUSED_UNATTENDED")


def test_slash_command_refuses_attended(monkeypatch) -> None:
    monkeypatch.setattr(runner, "run_column", _boom)
    monkeypatch.setattr(attended, "run_attended", _boom)
    assert cli.handle_slash("fixtures --attended", platform="cli").startswith("REFUSED_ATTENDED_SLASH")
    assert cli.handle_slash("attended", platform="telegram").startswith("REFUSED_ATTENDED_SLASH")


def test_attended_is_for_run_only() -> None:
    assert cli.handle_cli(argparse.Namespace(action="show", attended=True)) == 2


def test_nothing_to_ask_is_refused() -> None:
    picked = [t for t in testcalls.load_templates() if t.id == "TC01"]
    with pytest.raises(safety.Refusal) as refusal:
        attended.approval_templates(picked)
    assert refusal.value.code == "ATTENDED_NOTHING_TO_ASK"
    assert [t.id for t in attended.approval_templates(testcalls.load_templates())] == ["TC10"]


def test_attended_child_config_keeps_manual_and_deny_with_a_60_s_prompt(tmp_path) -> None:
    attended.child_config(tmp_path, ["fx_guard", "zz-proving-ground"], {}, tmp_path / "sandbox")
    text = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert '  mode: "manual"' in text and "  timeout: 60\n" in text
    for key in ("cron_mode", "single_query_mode", "unattended_mode"):
        assert f'  {key}: "deny"' in text
    assert not re.search(r"(?i)yolo|allowlist|approve", text)
    headless = runner.render_child_config(["x"], {}, tmp_path)
    assert "  timeout: 5\n" in headless  # the default drill is unchanged


def test_attended_argv_is_interactive_without_any_bypass() -> None:
    argv = attended.child_argv(["/bin/hermes"], 1)
    assert argv[:4] == ["/bin/hermes", "chat", "-q", runner.DRILL_PROMPT] and "--cli" in argv
    for flag in ("-Q", "--quiet", "--oneshot", "-z", "--yolo", "--accept-hooks"):
        assert flag not in argv
    assert argv[argv.index("--base-url") + 1] == runner.DEAD_PROVIDER_URL


def test_the_only_write_to_the_child_carries_the_users_own_keystrokes() -> None:
    """Structural: PG never answers. The child's terminal is written in exactly one place, with bytes read from
    the user's stdin in the same function; nothing else in the module writes to the pty master."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    writes = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "write"
              and isinstance(n.func.value, ast.Name) and n.func.value.id == "os"]
    to_master = [n for n in writes if isinstance(n.args[0], ast.Name) and n.args[0].id == "master"]
    assert len(to_master) == 1 and isinstance(to_master[0].args[1], ast.Name) and to_master[0].args[1].id == "keys"
    relay = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_relay")
    assert "keys = os.read(stdin_fd" in ast.unparse(relay)
    assert all(isinstance(n.args[0], ast.Name) and n.args[0].id in ("master", "stdout_fd") for n in writes)


def _events(result: str, *, ran: bool = False, t0: float = 100.0, t1: float = 107.4) -> list[dict]:
    tcid = "pgcall_TC10"
    events = [{"ev": "activated"}, {"ev": "pre_tool_call", "t": t0, "tool_call_id": tcid}]
    if ran:
        events.append({"ev": "terminal_raw", "tool_call_id": tcid, "returncode": 0})
    events += [{"ev": "post_tool_call", "t": t1, "tool_call_id": tcid, "result": result}, {"ev": "done"}]
    return events


@pytest.mark.parametrize("result, ran, label", [
    ('{"exit_code": 0, "approval": "Command required approval (world/other-writable permissions) and was approved '
     'by the user."}', True, "ASKED → approved by you after 7 s"),
    ('{"exit_code": -1, "error": "BLOCKED: Command denied by user. The user has NOT consented to this action."}',
     False, "ASKED → denied by you after 7 s"),
    ('{"error": "BLOCKED: User denied this command."}', False, "ASKED → denied by you after 7 s"),
    ('{"error": "BLOCKED: Command timed out without user response."}', False, "ASKED → timed out (denied) after 7 s"),
    ('{"error": "was not approved: the approval prompt could not be delivered or was not answered"}', False,
     "ASKED → not answered (denied) after 7 s"),
    ('{"output": "", "exit_code": 0}', True, "not asked (ran without a prompt)"),
    ('{"error": "blocked by a plugin"}', False, "not asked"),
])
def test_classification_from_recorded_events(result, ran, label) -> None:
    [cell] = attended.classify(_events(result, ran=ran), ["TC10"])
    assert cell.label == label


def test_a_stopped_column_never_invents_an_answer() -> None:
    [cell] = attended.classify([{"ev": "activated"}], ["TC10"], "CELL_TIMEOUT")
    assert not cell.asked and cell.label == "NOT RUN (CELL_TIMEOUT)"


def test_column_rendering_and_default_output_never_say_asked() -> None:
    run = attended.AttendedRun([attended.AttendedCell("TC10", True, attended.DENIED, 3.2)], None, 9.0, ("fx_guard",))
    text = attended.render_column(run, {"TC10": "approval-worthy shape"})
    assert "TC10 approval-worthy shape  ASKED → denied by you after 3 s" in text
    assert "PROVING GROUND did not" in text
    assert "ASKED" not in (ROOT / "docs" / "fixture-report.md").read_text(encoding="utf-8")
    assert "inert sandbox operations; PROVING GROUND will not answer for you" in attended.preface(1)


# --- real Hermes, real pty: the harness below plays the human; PROVING GROUND only relays its keystrokes ---------

DRIVER = r"""
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from proving_ground import attended, runner, testcalls
root, hermes = Path(sys.argv[1]), Path(sys.argv[2])
base = Path(sys.argv[3])
settings = runner.RunnerSettings(hermes, root, base / "provingground-cache" / "installs", base)
templates = [t for t in testcalls.load_templates() if t.id == "TC10"]
print(attended.preface(1), flush=True)
run = attended.run_attended(runner.fixture_subjects(root), templates, settings, 0, 1)
print(attended.render_column(run, {"TC10": "approval-worthy shape"}), flush=True)
print("PGRESULT " + json.dumps([[c.call_id, c.asked, c.answer] for c in run.cells] + [run.abort_reason]), flush=True)
"""

KEYS = {"approve": b"\r", "deny": b"\x1b[B\x1b[B\x1b[B\r"}  # Hermes's panel: 1. Allow once … 4. Deny
ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Z0-9]|\x1b[=>]|\x1b\][^\x07]*\x07")


def _hermes_bin() -> str:
    return os.environ.get("PG_HERMES_BIN") or shutil.which("hermes") or str(Path.home() / ".local/bin/hermes")


def _play_human(answer: str, home: Path) -> tuple[str, list]:
    base = Path(tempfile.gettempdir()).resolve()
    env = {**os.environ, "HERMES_HOME": str(home), "TERM": "xterm-256color", "PYTHONDONTWRITEBYTECODE": "1"}
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(sys.executable, [sys.executable, "-B", "-c", DRIVER, str(ROOT), _hermes_bin(), str(base)], env)
    buf, sent, deadline = b"", False, time.monotonic() + 240
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.3)
            if ready:
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
            screen = ANSI.sub(b"", buf)
            if not sent and b"Dangerous Command" in screen and b"Deny" in screen:
                time.sleep(1.0)  # a person reads the prompt first
                os.write(fd, KEYS.get(answer, b""))  # "wait": the person walks away; Hermes times out
                sent = True
            if b"PGRESULT " in screen and screen.rstrip().endswith(b"]"):
                break
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        os.waitpid(pid, 0)
    text = ANSI.sub(b"", buf).decode("utf-8", "replace").replace("\r", "")
    result = next(line for line in text.splitlines() if line.startswith("PGRESULT "))
    assert sent, "Hermes's approval prompt never appeared"
    return text, json.loads(result[len("PGRESULT "):])


@pytest.mark.hermes
@pytest.mark.parametrize("answer, expected", [("deny", attended.DENIED), ("approve", attended.APPROVED),
                                              ("wait", attended.TIMED_OUT)])
def test_pty_hermes_asks_and_the_human_answers(answer, expected) -> None:
    home = Path(tempfile.mkdtemp(prefix="pgS4-", dir=Path(tempfile.gettempdir()).resolve()))
    try:
        text, result = _play_human(answer, home)
        cells, reason = result[:-1], result[-1]
        assert reason is None and cells == [["TC10", True, expected]], text[-3000:]
        assert "inert sandbox operations; PROVING GROUND will not answer for you" in text
        assert "Dangerous Command" in text and "Deny" in text  # Hermes's own prompt, painted by Hermes
        assert f"ASKED → {expected}" in text
    finally:
        shutil.rmtree(home, ignore_errors=True)
