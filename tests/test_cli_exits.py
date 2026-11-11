# File: tests/test_cli_exits.py
# Every CLI outcome ends in one readable line and a documented exit code: Ctrl-C is a clean cancel (130), an
# unexpected error is one line plus a crash log (1), bad input is refused before anything is spawned (2), and
# --check fails on unmet, unevaluated or absent contracts (5).
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

import pytest
from helpers import fixture_like_matrix

from proving_ground import cli, matrix, runner
from test_warm_up import _interactive, _no_spawn


def _raise(exc: BaseException):
    def boom(_args):
        raise exc
    return boom


def test_ctrl_c_is_a_clean_cancel_with_exit_130(monkeypatch, capsys) -> None:
    monkeypatch.setitem(cli.COMMANDS, "run", _raise(KeyboardInterrupt("signal 2")))
    monkeypatch.setattr(runner, "cancel_everything", lambda: 4)
    assert cli.handle_cli(argparse.Namespace(action="run")) == 130
    assert capsys.readouterr().out == "drill cancelled; cleaned up 4 processes\n"


def test_cancelled_drill_is_the_same_clean_cancel(monkeypatch, capsys) -> None:
    monkeypatch.setitem(cli.COMMANDS, "run", _raise(runner.Cancelled("cancelled before spawn")))
    monkeypatch.setattr(runner, "cancel_everything", lambda: 0)
    assert cli.handle_cli(argparse.Namespace(action="run")) == 130
    assert "drill cancelled" in capsys.readouterr().out


def test_unexpected_error_is_one_line_and_a_private_crash_log(monkeypatch, capsys) -> None:
    monkeypatch.delenv(cli.DEBUG_ENV, raising=False)
    monkeypatch.setitem(cli.COMMANDS, "run", _raise(OSError("disk full")))
    assert cli.handle_cli(argparse.Namespace(action="run")) == 1
    out = capsys.readouterr().out.strip()
    assert len(out.splitlines()) == 1 and out.startswith("DRILL_ERROR: OSError: disk full (crash log: ")
    log = Path(out.split("(crash log: ")[1].split(")")[0])
    assert "Traceback" in log.read_text() and (log.stat().st_mode & 0o077) == 0
    assert "Traceback" not in out


def test_debug_env_shows_the_traceback(monkeypatch) -> None:
    monkeypatch.setenv(cli.DEBUG_ENV, "1")
    monkeypatch.setitem(cli.COMMANDS, "run", _raise(OSError("disk full")))
    with pytest.raises(OSError):
        cli.handle_cli(argparse.Namespace(action="run"))


@pytest.mark.parametrize("flags, code", [({"without": ["fx_rewritr"]}, "UNKNOWN_PLUGIN: unknown plugin fx_rewritr"),
                                         ({"only": ["TC99"]}, "UNKNOWN_CALL: unknown test call TC99")])
def test_unknown_names_are_refused_before_any_spawn(monkeypatch, capsys, flags, code) -> None:
    _interactive(monkeypatch)
    monkeypatch.setattr(runner, "warm_up", _no_spawn)
    monkeypatch.setattr(runner, "run_drill", _no_spawn)
    args = argparse.Namespace(action="run", fixtures=True, no_color=True, quiet=True, **flags)
    assert cli.handle_cli(args) == 2
    out = capsys.readouterr().out
    assert out.startswith(code) and "choose from" in out


def test_malformed_user_calls_are_refused_before_any_spawn(monkeypatch, capsys, tmp_path) -> None:
    _interactive(monkeypatch)
    monkeypatch.setattr(runner, "warm_up", _no_spawn)
    monkeypatch.setattr(runner, "run_drill", _no_spawn)
    for body in ("schema: provingground.testcalls.v1\ncalls: [1, 2]\n", "schema: [unclosed\n",
                 "schema: provingground.testcalls.v1\ncalls:\n  - {id: U01, tool: terminal, subject: s, purpose: p, "
                 "args: {command: 'echo hi > /etc/x {CALL_ID}'}}\n"):
        path = tmp_path / "calls.yaml"
        path.write_text(body)
        args = argparse.Namespace(action="run", fixtures=True, no_color=True, quiet=True, calls=str(path))
        assert cli.handle_cli(args) == 2
        assert capsys.readouterr().out.startswith("BAD_CALLS_FILE")


def _with_expect(m: matrix.Matrix, expect: dict) -> matrix.Matrix:
    calls = tuple(replace(c, expect=expect) if c.id == "TC03" else replace(c, expect={}) for c in m.calls)
    return replace(m, calls=calls)


@pytest.mark.parametrize("expect, code, last", [
    ({"stack": ("REWROTE",)}, 0, "contract: 1 of 1 expectations met"),
    ({"solo:fx_missing": ("PASSED_THROUGH",)}, 5, "contract: 0 of 1 expectations met"),
    ({}, 5, "CHECK FAILED: --check was given but no test call declares an expect: contract"),
])
def test_check_fails_on_unevaluated_or_empty_contracts(expect, code, last) -> None:
    m = _with_expect(fixture_like_matrix(), expect)
    assert cli.exit_code_for(m, check=True) == code
    lines = cli.expectation_lines(m)
    assert lines[-1] == last
    assert not any("_" in line.split("observed")[-1] for line in lines if "observed" in line)


def test_check_prints_outcomes_in_report_words() -> None:
    m = _with_expect(fixture_like_matrix(), {"stack": ("PASSED_THROUGH", "BLOCKED")})
    assert cli.expectation_lines(m)[0] == ("CHECK FAILED TC03 × stack: expected PASSED THROUGH or BLOCKED, "
                                           "observed REWROTE")


def test_export_creates_missing_dirs_and_refuses_cleanly_when_unwritable(tmp_path, capsys) -> None:
    m = fixture_like_matrix()
    target = tmp_path / "out" / "deeper" / "report.html"
    assert cli.write_exports(m, argparse.Namespace(html_out=str(target))) == [str(target)]
    assert target.read_text().startswith("<!doctype html>")
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(cli.safety.Refusal) as excinfo:
        cli.write_exports(m, argparse.Namespace(json_out=str(blocker / "report.json")))
    assert excinfo.value.code == "EXPORT_FAILED"


def test_corrupt_saved_report_is_a_clean_refusal(capsys) -> None:
    (cli.reports_dir() / cli.LATEST).write_text("{not json")
    assert cli.handle_cli(argparse.Namespace(action="show", call=None, column=None)) == 2
    assert capsys.readouterr().out.startswith("BAD_REPORT: the saved report latest.json cannot be read")
    (cli.reports_dir() / cli.LATEST).write_text(json.dumps({"schema": "provingground.report.v1"}))
    assert cli.handle_cli(argparse.Namespace(action="show", call=None, column=None)) == 2


def test_show_accepts_a_bare_plugin_name_for_column(capsys) -> None:
    cli.save(fixture_like_matrix())
    assert cli.handle_cli(argparse.Namespace(action="show", call="TC04", column="fx_trimmer")) == 0
    assert capsys.readouterr().out.startswith("── cell\nTC04 × fx_trimmer:")


def test_help_documents_every_action_and_exit_code() -> None:
    parser = argparse.ArgumentParser(prog="hermes provingground")
    cli.setup_parser(parser)
    text = parser.format_help()
    for word in ("run ", "show ", "lock ", "warm ", "sweep ", "warm --status", "130", "--column"):
        assert word in text, word
    for code in ("0 ", "1 ", "2 ", "3 ", "4 ", "5 "):
        assert f"\n  {code}" in text, code


def test_short_help_groups_options_and_hides_the_advanced_knobs() -> None:
    parser = argparse.ArgumentParser(prog="hermes provingground")
    cli.setup_parser(parser)
    text = parser.format_help()
    for group in ("\nrun:\n", "\nreport:\n", "\ncompare:\n", "\nadvanced:\n"):
        assert group in text, group
    for hidden in ("--jobs", "--timeout", "--hermes-bin", "--quiet", "--no-color"):
        assert f"  {hidden}" not in text.split("actions:")[0], hidden  # named once in the epilog instead
    args = parser.parse_args(["--jobs", "2", "--timeout", "9", "--quiet", "--no-color", "--hermes-bin", "/x"])
    assert (args.jobs, args.timeout, args.quiet, args.no_color, args.hermes_bin) == (2, 9.0, True, True, "/x")
    assert "BLOCKED · REWROTE · RESULT CHANGED · APPROVAL-GATED · PASSED THROUGH" in text
    assert "more: ESCAPED, ERRORED, LOAD FAILED, NOT RUN, N/A" in text


def test_lock_runs_the_drill_and_writes_the_contract(monkeypatch, tmp_path) -> None:
    seen = []
    monkeypatch.setattr(cli, "cmd_run", lambda args: seen.append(args.write_contract) or 0)
    parser = argparse.ArgumentParser()
    cli.setup_parser(parser)
    assert cli.handle_cli(parser.parse_args(["lock", str(tmp_path / "stack.lock.yaml"), "--fixtures"])) == 0
    assert seen == [str(tmp_path / "stack.lock.yaml")]
    assert cli.handle_cli(parser.parse_args(["lock"])) == 2 and seen == [str(tmp_path / "stack.lock.yaml")]
