# File: tests/test_report_compare.py
# --compare, --since-last and `diff` print only what changed between two reports: plugins added or removed,
# cell outcome changes, new and no-longer-recorded findings. Exit 0 = no change, 6 = a change; a bad report file
# is refused (2) before anything is spawned.
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

import pytest
from helpers import IDS, LOG, column, fixture_like_matrix

from proving_ground import cli, matrix, report, runner


def without_rewriter() -> matrix.Matrix:
    """The fixture-like stack after `--without fx_rewriter`: TC03 passes through and the finding is gone."""
    guard = {"TC02": {"block": "fx_guard: command carries the guard marker"}}
    trim = {"TC04": {"tool_out": LOG.replace("test_beta ... FAILED\n", "")}}
    columns = [column("baseline", (), {}), column("solo:fx_guard", ("fx_guard",), guard),
               column("solo:fx_trimmer", ("fx_trimmer",), trim),
               column("stack", ("fx_guard", "fx_trimmer"), {**guard, **trim})]
    infos = [matrix.CallInfo(i, "terminal", f"subject {i}", f"purpose {i}") for i in IDS]
    return matrix.build_matrix(columns, column("baseline-repeat", (), {}), {}, infos, "v0.23.1 @ 3b4215785882",
                               "2026-11-11T01:00:00+00:00", 8.0)


def test_identical_reports_have_no_change() -> None:
    m = fixture_like_matrix()
    diff = matrix.compare(m, m)
    assert not diff.changed and diff == matrix.MatrixDiff()
    assert report.diff_lines(m, m, diff, "a.json", "b.json")[-1].startswith("no change:")


def test_removed_plugin_lists_the_plugin_once_and_only_the_changed_cells() -> None:
    old, new = fixture_like_matrix(), without_rewriter()
    diff = matrix.compare(old, new)
    assert diff.plugins_removed == ("fx_rewriter",) and diff.plugins_added == ()
    assert [(c.call_id, c.column) for c in diff.cells] == [("TC03", "stack")]
    assert diff.cells[0].before.primary is matrix.Outcome.REWROTE
    assert diff.cells[0].after.primary is matrix.Outcome.PASSED_THROUGH
    assert [f.kind for f in diff.findings_resolved] == ["REWRITE_UNSEEN_BY"] and diff.findings_new == ()


def test_diff_lines_name_each_change_and_count_them() -> None:
    old, new = fixture_like_matrix(), without_rewriter()
    lines = report.diff_lines(old, new, matrix.compare(old, new), "before.json", "after.json")
    assert "plugin removed: fx_rewriter" in lines
    assert "TC03 × FULL STACK: REWROTE → PASSED THROUGH" in lines
    assert any(x.startswith("finding no longer recorded REWRITE_UNSEEN_BY TC03") for x in lines)
    assert lines[-1] == "3 changes between the two reports"
    assert report.forbidden_claims("\n".join(lines)) == []


def test_reverse_direction_reports_the_plugin_and_finding_as_new() -> None:
    diff = matrix.compare(without_rewriter(), fixture_like_matrix())
    assert diff.plugins_added == ("fx_rewriter",) and [f.kind for f in diff.findings_new] == ["REWRITE_UNSEEN_BY"]


def test_a_note_or_timestamp_change_alone_is_not_a_change() -> None:
    m = fixture_like_matrix()
    key = ("stack", "TC03")
    cells = {**m.cells, key: replace(m.cells[key], note="other wording")}
    later = replace(m, cells=cells, conditions=replace(m.conditions, started_at="2026-11-12T00:00:00+00:00"))
    assert not matrix.compare(m, later).changed


def test_added_and_removed_calls_are_listed_without_their_cells() -> None:
    m = fixture_like_matrix()
    fewer = replace(m, calls=m.calls[:-1], cells={k: v for k, v in m.cells.items() if k[1] != "TC05"})
    diff = matrix.compare(m, fewer)
    assert diff.calls_removed == ("TC05",) and diff.cells == ()


def test_unstable_or_core_mark_change_is_a_change() -> None:
    m = fixture_like_matrix()
    key = ("stack", "TC01")
    later = replace(m, cells={**m.cells, key: replace(m.cells[key], unstable=True)})
    assert [(c.call_id, c.column) for c in matrix.compare(m, later).cells] == [("TC01", "stack")]


def _write(path: Path, m: matrix.Matrix) -> str:
    path.write_text(json.dumps(report.matrix_to_dict(m)), encoding="utf-8")
    return str(path)


def test_diff_action_exits_0_without_change_and_6_with_one(tmp_path, capsys) -> None:
    a, b = _write(tmp_path / "a.json", fixture_like_matrix()), _write(tmp_path / "b.json", without_rewriter())
    assert cli.handle_cli(argparse.Namespace(action="diff", call=a, target=a)) == 0
    assert "no change" in capsys.readouterr().out
    assert cli.handle_cli(argparse.Namespace(action="diff", call=a, target=b)) == cli.CHANGED_EXIT == 6
    assert "TC03 × FULL STACK: REWROTE → PASSED THROUGH" in capsys.readouterr().out


@pytest.mark.parametrize("second, code", [(None, "BAD_INPUT"), ("missing.json", "NO_REPORT"), ("bad", "BAD_REPORT")])
def test_diff_action_refuses_missing_or_unreadable_reports(tmp_path, capsys, second, code) -> None:
    a = _write(tmp_path / "a.json", fixture_like_matrix())
    (tmp_path / "bad").write_text("{not json", encoding="utf-8")
    target = None if second is None else str(tmp_path / second)
    assert cli.handle_cli(argparse.Namespace(action="diff", call=a, target=target)) == 2
    assert capsys.readouterr().out.startswith(code)


def _fake_run(monkeypatch, results: list[matrix.Matrix]) -> list[int]:
    """cmd_run without a drill: each call returns the next matrix; the list records how many drills ran."""
    ran: list[int] = []

    def run_matrix(_args, on_partial=None, platform=None):
        ran.append(1)
        return results[len(ran) - 1], ""
    monkeypatch.setattr(cli, "run_matrix", run_matrix)
    monkeypatch.setattr(runner, "install_cancellation", lambda: None)
    return ran


def _run_args(**flags) -> argparse.Namespace:
    return argparse.Namespace(action="run", quiet=True, no_color=True, fixtures=True, **flags)


def test_since_last_compares_with_the_report_saved_before_this_drill(monkeypatch, capsys) -> None:
    _fake_run(monkeypatch, [fixture_like_matrix(), fixture_like_matrix(), without_rewriter()])
    assert cli.handle_cli(_run_args(since_last=True)) == 0
    assert cli.NO_EARLIER_REPORT in capsys.readouterr().out
    assert cli.handle_cli(_run_args(since_last=True)) == 0
    assert "no change: same plugins" in capsys.readouterr().out
    assert cli.handle_cli(_run_args(since_last=True)) == 6
    out = capsys.readouterr().out
    assert "compared the last saved report" in out and "plugin removed: fx_rewriter" in out


def test_compare_with_a_bad_file_is_refused_before_the_drill(monkeypatch, capsys, tmp_path) -> None:
    ran = _fake_run(monkeypatch, [fixture_like_matrix()])
    (tmp_path / "old.json").write_text("[]", encoding="utf-8")
    assert cli.handle_cli(_run_args(compare=str(tmp_path / "old.json"))) == 2
    assert capsys.readouterr().out.startswith("BAD_REPORT") and ran == []


def test_compare_exit_6_never_hides_a_failed_contract(monkeypatch, capsys, tmp_path) -> None:
    old = _write(tmp_path / "old.json", fixture_like_matrix())
    _fake_run(monkeypatch, [without_rewriter()])
    assert cli.handle_cli(_run_args(compare=old, check=True)) == 5
    assert "TC03 × FULL STACK: REWROTE → PASSED THROUGH" in capsys.readouterr().out


def test_compare_and_since_last_cannot_be_combined() -> None:
    parser = argparse.ArgumentParser()
    cli.setup_parser(parser)
    with pytest.raises(SystemExit):
        parser.parse_args(["--compare", "a.json", "--since-last"])
    args = parser.parse_args(["diff", "a.json", "b.json"])
    assert (args.action, args.call, args.target) == ("diff", "a.json", "b.json")


def test_help_documents_exit_6() -> None:
    assert "  6    --compare, --since-last or diff" in cli.USAGE_EPILOG
