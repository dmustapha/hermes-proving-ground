# File: tests/test_declared_hooks.py
# Each tested plugin's declared hooks (its plugin.yaml provides_hooks) are part of the report: a hover title and a
# compact line under every HTML column header, column metadata in the JSON, and `show --plugins` in the terminal.
# The default terminal table is unchanged. A solo column that also loads its requires_plugins is "<key> (+ deps)".
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from helpers import IDS, column, fixture_like_matrix

from proving_ground import cli, matrix, report, runner

ROOT = Path(__file__).resolve().parent.parent
HOOKS = {"fx_guard": ("pre_tool_call",), "fx_rewriter": ("pre_tool_call",), "fx_trimmer": ("transform_terminal_output",)}
PLUGINS = {"baseline": (), "solo:fx_guard": ("fx_guard",), "solo:fx_rewriter": ("fx_rewriter",),
           "solo:fx_trimmer": ("fx_trimmer",), "stack": ("fx_guard", "fx_rewriter", "fx_trimmer")}


def with_hooks(plugins=None):
    return replace(fixture_like_matrix(), column_plugins=plugins or PLUGINS, declared_hooks=HOOKS)


def test_declared_hooks_are_read_from_each_fixture_manifest() -> None:
    assert {s.key: runner.declared_hooks(s.path) for s in runner.fixture_subjects(ROOT)} == HOOKS
    assert runner.declared_hooks(ROOT / "fixtures" / "extended" / "fx_redactor") == ("transform_terminal_output",)


def test_html_headers_carry_the_declared_hooks() -> None:
    page = report.render_html(with_hooks())
    assert "title='declared hooks: fx_trimmer: transform_terminal_output'" in page
    assert "<span class='hooks'>hooks: pre_tool_call, transform_terminal_output</span>" in page  # the stack
    assert "<span class='hooks'>no plugins</span>" in page  # the baseline


def test_json_report_has_column_metadata_that_round_trips() -> None:
    data = json.loads(json.dumps(report.matrix_to_dict(with_hooks())))
    assert data["columns"] == list(PLUGINS)  # the columns list itself keeps its shape
    assert data["column_meta"]["solo:fx_trimmer"] == {"title": "fx_trimmer", "plugins": ["fx_trimmer"],
                                                      "declared_hooks": {"fx_trimmer": ["transform_terminal_output"]}}
    loaded = report.matrix_from_dict(data)
    assert loaded.column_plugins == PLUGINS and loaded.declared_hooks == HOOKS


def test_a_report_without_declared_hooks_keeps_its_old_keys_and_headers() -> None:
    m = fixture_like_matrix()
    assert "column_meta" not in report.matrix_to_dict(m)
    assert "<th scope='col'>fx_trimmer</th>" in report.render_html(m)


def test_default_terminal_table_is_unchanged_by_declared_hooks() -> None:
    assert report.render_terminal(with_hooks(), color=False) == report.render_terminal(fixture_like_matrix(),
                                                                                        color=False)


def test_show_plugins_lists_each_plugins_declared_hooks(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "last_drill_failure", lambda: None)
    monkeypatch.setattr(cli, "load_latest", with_hooks)
    parser = __import__("argparse").ArgumentParser()
    cli.setup_parser(parser)
    assert cli.cmd_show(parser.parse_args(["show", "--plugins"])) == 0
    out = capsys.readouterr().out
    assert "  fx_trimmer: transform_terminal_output" in out
    assert "  FULL STACK: fx_guard, fx_rewriter, fx_trimmer" in out


def test_solo_column_with_required_plugins_is_labelled_plus_deps() -> None:
    m = with_hooks({**PLUGINS, "solo:fx_trimmer": ("fx_guard", "fx_trimmer")})
    assert report.header_title(m, "solo:fx_trimmer") == "fx_trimmer (+ deps)"
    assert report.header_title(m, "solo:fx_guard") == "fx_guard"
    assert "fx_trimmer (+ deps)" in report.render_terminal(m, color=False)
    assert "| fx_trimmer (+ deps) |" in report.render_markdown(m)
    assert "fx_trimmer (+ deps)<br>" in report.render_html(m)


def test_assemble_records_each_columns_plugins_and_hooks() -> None:
    cols = [column("baseline", (), {}), column("solo:fx_trimmer", ("fx_guard", "fx_trimmer"), {}),
            column("stack", ("fx_guard", "fx_trimmer"), {})]
    drill = runner.DrillRun(cols, column("baseline-repeat", (), {}), {}, [], ["fx_env"], "canary", 1.0,
                            ("fx_guard", "fx_trimmer"), declared_hooks=HOOKS)
    infos = [matrix.CallInfo(i, "terminal", f"subject {i}", f"purpose {i}") for i in IDS]
    m = cli.assemble(drill, infos, "test", cli.now_iso())
    assert m.column_plugins["solo:fx_trimmer"] == ("fx_guard", "fx_trimmer")
    assert m.column_plugins["solo:fx_env"] == ("fx_env",) and m.declared_hooks == HOOKS
    assert report.header_title(m, "solo:fx_trimmer") == "fx_trimmer (+ deps)"
