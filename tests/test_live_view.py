# File: tests/test_live_view.py
# The live-filling matrix (banner in the first frame, fixed column order, progress footer), terminal-width layout,
# and `show` without a call id.
from __future__ import annotations

import argparse
from dataclasses import replace

from helpers import fixture_like_matrix

from proving_ground import cli, matrix, report


def _frame(done: tuple[str, ...]) -> report.LiveFrame:
    m = fixture_like_matrix()
    cells = {k: v for k, v in m.cells.items() if k[0] in done}
    return report.LiveFrame(m.calls, tuple(m.columns), cells, {name: (5.42, False) for name in done})


def test_first_frame_is_the_banner_before_any_column_is_planned() -> None:
    text = report.render_live(report.LiveFrame((), ()), color=False)
    assert text.splitlines()[0] == report.LIVE_BANNER
    assert "starting the throwaway child Hermes" in text


def test_banner_wording_is_neutral_and_not_credentials_none() -> None:
    assert report.LIVE_BANNER.startswith("model: NOT CALLED · provider: none · no provider keys")
    assert "credentials: none" not in report.LIVE_BANNER
    assert report.forbidden_claims(report.LIVE_BANNER) == []


def test_every_planned_column_is_drawn_and_unfinished_cells_are_pending() -> None:
    text = report.render_live(_frame(("baseline",)), color=False, width=160)
    assert text.splitlines()[0] == report.LIVE_BANNER
    for title in ("baseline (no plugins)", "fx_guard", "fx_rewriter", "fx_trimmer", "FULL STACK"):
        assert title in text
    assert "…" in text and "BLOCKED" not in text


def test_progress_line_marks_finished_stopped_and_pending_columns() -> None:
    frame = replace(_frame(("baseline", "solo:fx_trimmer")),
                    finished={"baseline": (5.42, False), "solo:fx_trimmer": (3.0, False), "solo:fx_guard": (9.0, True)})
    assert report.progress_line(frame) == ("baseline ✓ 5.4s · fx_guard stopped 9.0s · fx_rewriter … · "
                                           "fx_trimmer ✓ 3.0s · FULL STACK …")
    every = replace(frame, finished={name: (1.0, False) for name in frame.planned})
    assert report.progress_line(every).endswith("FULL STACK ✓ 1.0s · cross-check of rewritten calls …")


def test_live_frame_keeps_planned_order_when_columns_finish_out_of_order() -> None:
    from helpers import column

    planned = ("baseline", "solo:fx_trimmer", "solo:fx_guard", "stack")  # a load order that is not alphabetical
    done = [column("baseline", (), {}), column("stack", ("fx_guard", "fx_trimmer"), {}),
            column("solo:fx_guard", ("fx_guard",), {})]
    infos = fixture_like_matrix().calls
    frame = cli.live_frame(done, planned, infos, "v0", "2026-11-11T00:00:00+00:00")
    assert frame.planned == planned
    assert set(frame.finished) == {"baseline", "stack", "solo:fx_guard"}
    assert {col for col, _cid in frame.cells} == {"baseline", "stack", "solo:fx_guard"}
    text = report.render_live(frame, color=False)
    header = next(line for line in text.splitlines() if "fx_trimmer" in line and "fx_guard" in line)
    assert header.index("fx_trimmer") < header.index("fx_guard") < header.index("FULL STACK")


def test_planned_columns_follow_load_order(tmp_path) -> None:
    from proving_ground import runner

    subjects = [runner.PluginSource(k, tmp_path / k) for k in ("fx_trimmer", "fx_guard")]
    assert cli.planned_columns(subjects) == ("baseline", "solo:fx_guard", "solo:fx_trimmer", "stack")
    assert cli.planned_columns([]) == ("baseline",)


def test_table_fits_a_120_column_terminal_without_cropping() -> None:
    m = fixture_like_matrix()
    for width in (120, 100):
        text = report.render_terminal(m, color=False, width=width)
        assert max(len(line) for line in text.splitlines()) <= width
        table = text.split(matrix.tally(m))[1].split("• attribution")[0]
        assert "FULL STACK" in table and table.count("BLOCKED") == 2 and table.count("REWROTE") == 2
        assert table.count("unseen") == 1  # the finding's cell is marked in-cell (it may wrap: "unseen by fx_guard")
        # TC05: core truncation reads RESULT CHANGED (core) in all 5 columns, not only the baseline
        assert table.count("RESULT CHANGED") == 7 and table.count("PASSED THROUGH") == 14


def _show_args(call: str | None = None, column: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(action="show", call=call, column=column)


def test_show_without_a_call_lists_notable_cells(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "load_latest", fixture_like_matrix)
    assert cli.cmd_show(_show_args()) == 0
    out = capsys.readouterr().out
    assert "TC04 --column stack" in out and "TC01 " not in out
    assert "/provingground" not in cli.handle_slash("show") and "TC04 --column stack" in cli.handle_slash("show")


def test_show_with_an_unknown_call_explains_the_choices(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "load_latest", fixture_like_matrix)
    assert cli.cmd_show(_show_args("TC99")) == 2
    assert "choose a call from" in capsys.readouterr().out
    assert cli.handle_slash("show TC99").startswith("choose a call from")
    assert cli.handle_slash("show TC04 stack").startswith("── cell")


def test_show_when_every_cell_passed_through(monkeypatch, capsys) -> None:
    m = fixture_like_matrix()
    quiet = {k: replace(v, outcomes=(matrix.Outcome.PASSED_THROUGH,), core=False) for k, v in m.cells.items()}
    monkeypatch.setattr(cli, "load_latest", lambda: replace(m, cells=quiet))
    assert cli.cmd_show(_show_args()) == 0
    out = capsys.readouterr().out
    assert "every cell in the latest report is PASSED THROUGH" in out
    assert report.forbidden_claims(out) == []
