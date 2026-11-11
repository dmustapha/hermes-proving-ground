# File: tests/test_cell_detail.py
# The "click a cell" content: the delivered diff reads line by line, and the live view never duplicates the table.
from __future__ import annotations

import argparse
import io
import json
from dataclasses import replace

from helpers import fixture_like_matrix

from proving_ground import cli, report


def _envelope(output: str, exit_code: int = 0) -> str:
    return json.dumps({"error": None, "exit_code": exit_code, "output": output})


def _diff_section(m, column: str = "stack", call_id: str = "TC04") -> list[str]:
    sections = dict(report.detail_sections(m, column, call_id))
    return next(lines for title, lines in sections.items() if title.startswith("delivered: baseline"))


def _with_delivered(m, column: str, call_id: str, delivered: str):
    obs = replace(m.observations[column][call_id], delivered=delivered)
    return replace(m, observations={**m.observations, column: {**m.observations[column], call_id: obs}})


def test_trimmed_failed_line_is_one_removed_output_line() -> None:
    lines = _diff_section(fixture_like_matrix())
    removed = [line for line in lines if line.startswith("│ -") and not line.startswith("│ ---")]
    assert removed == ["│ -test_beta ... FAILED"]
    assert not any(line.startswith("│ +") and not line.startswith("│ +++") for line in lines)


def test_identical_delivered_reads_identical() -> None:
    m = fixture_like_matrix()
    assert _diff_section(m, "stack", "TC01") == ["│ identical"]


def test_change_outside_the_output_still_shows_a_diff() -> None:
    m = fixture_like_matrix()
    base_output = json.loads(m.observations["baseline"]["TC01"].delivered)["output"]
    lines = _diff_section(_with_delivered(m, "stack", "TC01", _envelope(base_output, exit_code=1)), "stack", "TC01")
    assert lines != ["│ identical"]
    assert any('"exit_code": 1' in line for line in lines)


def test_non_json_delivered_is_diffed_as_text() -> None:
    m = _with_delivered(fixture_like_matrix(), "stack", "TC04", "plain text\nsecond line")
    lines = _diff_section(m)
    assert "│ +plain text" in lines and "│ +second line" in lines


def test_live_view_is_transient_so_the_table_prints_once(monkeypatch) -> None:
    m = fixture_like_matrix()

    def fake_run(args, on_partial=None, platform=None):
        on_partial(m)
        return m, ""

    monkeypatch.setattr(cli, "run_matrix", fake_run)
    monkeypatch.setattr(cli.report, "render_terminal", lambda part, color=True: "LIVE-FRAME\n")
    import rich.console

    sink, real_console = io.StringIO(), rich.console.Console
    monkeypatch.setattr(rich.console, "Console", lambda *a, **k: real_console(file=sink, force_terminal=False))
    assert cli._live_run(argparse.Namespace(no_color=True))[0] is m
    assert "LIVE-FRAME" not in sink.getvalue()
