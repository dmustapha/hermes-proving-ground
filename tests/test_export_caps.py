# File: tests/test_export_caps.py
# Exported JSON caps each string at EXPORT_FIELD_MAX characters (marked with the count cut); the saved report keeps
# the longer text for `show`. Cell detail sections are capped by characters as well as lines, so one long JSON
# line cannot make the HTML unbounded.
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from helpers import fixture_like_matrix

from proving_ground import cli, report

ROOT = Path(__file__).resolve().parent.parent


def _with_long_output(n: int):
    m = fixture_like_matrix()
    obs = m.observations["stack"]["TC01"]
    long_obs = replace(obs, terminal_raw="x" * n)
    return replace(m, observations={**m.observations, "stack": {**m.observations["stack"], "TC01": long_obs}})


def test_json_export_caps_a_long_field_and_says_how_much_was_cut(tmp_path) -> None:
    path = tmp_path / "r.json"
    cli.write_exports(_with_long_output(20000), argparse.Namespace(json_out=str(path)))
    raw = json.loads(path.read_text(encoding="utf-8"))["observations"]["stack"]["TC01"]["terminal_raw"]
    assert raw == "x" * cli.EXPORT_FIELD_MAX + f" …[{20000 - cli.EXPORT_FIELD_MAX} more characters clipped by " \
                                                "PROVING GROUND: export field cap]"
    report.matrix_from_dict(json.loads(path.read_text(encoding="utf-8")))  # still a loadable report


def test_fields_at_or_under_the_cap_are_exported_byte_for_byte(tmp_path) -> None:
    m = _with_long_output(cli.EXPORT_FIELD_MAX)
    path = tmp_path / "r.json"
    cli.write_exports(m, argparse.Namespace(json_out=str(path)))
    assert path.read_text(encoding="utf-8") == json.dumps(report.matrix_to_dict(cli.shareable(m)), indent=2,
                                                          ensure_ascii=False)


def test_the_saved_report_keeps_the_long_field_for_show() -> None:
    saved = json.loads(cli.bounded_report_json(_with_long_output(20000)))
    assert saved["observations"]["stack"]["TC01"]["terminal_raw"] == "x" * 20000


def test_exported_json_of_the_committed_report_differs_only_in_capped_fields() -> None:
    committed = json.loads((ROOT / "docs" / "fixture-report.json").read_text(encoding="utf-8"))
    exported = json.loads(cli.export_json(cli.shareable(report.matrix_from_dict(committed))))
    assert {k: v for k, v in exported.items() if k != "observations"} == \
           {k: v for k, v in committed.items() if k != "observations"}
    for col, calls in committed["observations"].items():
        for cid, obs in calls.items():
            for key, value in obs.items():
                if isinstance(value, str) and len(value) > cli.EXPORT_FIELD_MAX:
                    assert exported["observations"][col][cid][key].startswith(value[:cli.EXPORT_FIELD_MAX] + " …[")
                else:
                    assert exported["observations"][col][cid][key] == value, (col, cid, key)


def test_cell_detail_caps_one_long_line_by_characters() -> None:
    m = _with_long_output(20000)
    detail = report.render_cell_detail(m, "stack", "TC01")
    assert f"[{20000 - report.MAX_DETAIL_CHARS} more characters]" in detail
    assert len(detail) < 3 * report.MAX_DETAIL_CHARS


def test_cell_detail_line_cap_is_unchanged_for_short_lines() -> None:
    m = _with_long_output(0)
    many = "\n".join(str(i) for i in range(100))
    obs = replace(m.observations["stack"]["TC01"], terminal_raw=many)
    m = replace(m, observations={**m.observations, "stack": {**m.observations["stack"], "TC01": obs}})
    detail = report.render_cell_detail(m, "stack", "TC01")
    assert "│ 39\n│ [60 more lines]" in detail and "more characters" not in detail
