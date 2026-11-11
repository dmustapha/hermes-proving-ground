# File: tests/test_focus_view.py
# --focus hides rows where every cell is PASSED THROUGH in the terminal and Markdown views and names what it hid;
# the HTML gets a CSS-only toggle (no script, no network). Without --focus every view is unchanged.
from __future__ import annotations

import argparse
import json
import re
from dataclasses import replace
from pathlib import Path

from helpers import fixture_like_matrix

from proving_ground import cli, matrix, report

ROOT = Path(__file__).resolve().parent.parent


def _committed() -> matrix.Matrix:
    return report.matrix_from_dict(json.loads((ROOT / "docs" / "fixture-report.json").read_text(encoding="utf-8")))


def _rows(markdown: str) -> list[str]:
    return [line.split()[1] for line in markdown.splitlines() if re.match(r"^\| (TC|U)\w+ ", line)]


def test_quiet_row_needs_every_cell_passed_through() -> None:
    m = fixture_like_matrix()
    assert [c.id for c in m.calls if matrix.quiet_row(m, c.id)] == ["TC01"]


def test_na_cells_do_not_keep_a_row_but_a_missing_cell_does() -> None:
    m = fixture_like_matrix()
    na = replace(m.cells[("solo:fx_guard", "TC01")], outcomes=(matrix.Outcome.NOT_APPLICABLE,))
    assert matrix.quiet_row(replace(m, cells={**m.cells, ("solo:fx_guard", "TC01"): na}), "TC01")
    partial = {k: v for k, v in m.cells.items() if k != ("stack", "TC01")}
    assert not matrix.quiet_row(replace(m, cells=partial), "TC01")
    all_na = {k: replace(v, outcomes=(matrix.Outcome.NOT_APPLICABLE,)) for k, v in m.cells.items()}
    assert not matrix.quiet_row(replace(m, cells=all_na), "TC01")


def test_focus_markdown_hides_quiet_rows_and_names_them() -> None:
    m = _committed()
    full, focused = report.render_markdown(m), report.render_markdown(m, focus=True)
    assert _rows(full) == [c.id for c in m.calls]
    assert _rows(focused) == ["TC02", "TC03", "TC04", "TC05", "TC10", "TC11", "TC16"]  # core-only rows stay
    assert "focus: 17 of 24 rows hidden, every cell in them PASSED THROUGH or NOT EXECUTED (probe) (N/A cells " \
           "aside): TC01, TC06, TC07, TC08, TC09, TC12, TC13, TC14, TC15, TC17, TC18, TC19, TC20, TC21, TC22, TC23, " \
           "TC24" in focused
    assert "Observed on 24 synthetic inputs in a sandbox." in focused


def test_focus_terminal_keeps_the_drill_count_and_hides_quiet_rows() -> None:
    m = _committed()
    text = report.render_terminal(m, color=False, width=160, focus=True)
    assert "Observed on 24 synthetic inputs in a sandbox." in text
    assert "TC04" in text and "TC01 plain command" not in text and "focus: 17 of 24 rows hidden" in text
    assert report.forbidden_claims(text) == []


def test_focus_with_nothing_to_hide_says_so() -> None:
    m = fixture_like_matrix()
    busy = replace(m, calls=m.calls[1:])
    assert report.focus_line(busy) == "focus: no rows hidden; every row has a cell that is not PASSED THROUGH"


def test_default_views_are_unchanged_by_the_focus_option() -> None:
    m = _committed()
    assert report.render_terminal(m, color=False, width=160) == report.render_terminal(m, False, 160, focus=False)
    assert report.render_markdown(m) == report.render_markdown(m, focus=False)
    assert "focus:" not in report.render_markdown(m) and "focus:" not in report.render_terminal(m, color=False)


def test_html_toggle_is_css_only_and_checked_by_focus() -> None:
    m = _committed()
    plain, focused = report.render_html(m), report.render_html(m, focus=True)
    assert '<input type="checkbox" id="focus">' in plain and '<input type="checkbox" id="focus" checked>' in focused
    assert "#focus:checked~#matrix tr.quiet{display:none}" in plain
    assert plain.count("<tr class=quiet>") == 17
    for page in (plain, focused):
        assert "<script" not in page and not re.search(r"(src|href)=[\"']?https?:", page)
        assert report.forbidden_claims(page) == []


def test_focus_flag_reaches_the_markdown_and_html_exports(tmp_path) -> None:
    md, page = tmp_path / "r.md", tmp_path / "r.html"
    cli.write_exports(_committed(), argparse.Namespace(markdown_out=str(md), html_out=str(page), focus=True))
    assert "focus: 17 of 24 rows hidden" in md.read_text(encoding="utf-8")
    assert 'id="focus" checked' in page.read_text(encoding="utf-8")
