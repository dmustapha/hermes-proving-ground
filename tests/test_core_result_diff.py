# TC05: a plugin column is diffed against the baseline's DELIVERED result, and a
# Hermes-core change of the output is labelled the same way in every column ("core" or "core + plugin").
from __future__ import annotations

import json

from helpers import LONG, column, fixture_like_matrix

from proving_ground import matrix, report
from proving_ground.matrix import Outcome

IDS = ("TC01", "TC02", "TC03", "TC04", "TC05")
INFOS = [matrix.CallInfo(i, "terminal", i, i) for i in IDS]


def _result(output: str) -> str:
    return json.dumps({"output": output, "exit_code": 0, "error": None})


# Core truncates TC05 at capture (tool_raw = first 120 chars) AND caps the delivered result after the tap.
CORE_CAP = {"TC05": {"delivered": _result(LONG[:60])}}
FURTHER_TRIM = {"TC05": {"delivered": _result(LONG[:20])}}


def _matrix(solo_effect: dict, stack_effect: dict) -> matrix.Matrix:
    cols = [column("baseline", (), CORE_CAP), column("solo:fx_trim", ("fx_trim",), solo_effect),
            column("solo:fx_quiet", ("fx_quiet",), CORE_CAP), column("stack", ("fx_quiet", "fx_trim"), stack_effect)]
    return matrix.build_matrix(cols, None, {}, INFOS, "v", "t", 1.0)


def test_plugin_trimming_an_already_core_changed_result_is_not_passed_through() -> None:
    m = _matrix(FURTHER_TRIM, FURTHER_TRIM)
    assert m.cell("baseline", "TC05").core
    for name in ("solo:fx_trim", "stack"):
        cell = m.cell(name, "TC05")
        assert cell.primary is Outcome.RESULT_CHANGED, name
        assert not cell.core and cell.core_also, name
        assert report.cell_label(cell) == "RESULT CHANGED (core + plugin)", name
    by_call = {(a.call_id, a.outcome): a for a in m.attributions}
    assert by_call[("TC05", Outcome.RESULT_CHANGED)].plugins == ("fx_trim",)


def test_core_only_change_renders_the_same_in_every_column() -> None:
    m = _matrix(CORE_CAP, CORE_CAP)
    for name in m.columns:
        cell = m.cell(name, "TC05")
        assert report.cell_label(cell) == "RESULT CHANGED (core)", name
        assert cell.core, name
    assert not any(a.call_id == "TC05" and a.kind != "core" for a in m.attributions)


def test_fixture_tc05_renders_consistently_across_columns() -> None:
    m = fixture_like_matrix()
    labels = {name: report.cell_label(m.cell(name, "TC05")) for name in m.columns}
    assert set(labels.values()) == {"RESULT CHANGED (core)"}, labels
    assert matrix.tally(m).endswith("3 distinct effects recorded · 1 cross-plugin finding")


def test_plugin_trim_hidden_by_core_only_stack_is_masked() -> None:
    m = _matrix(FURTHER_TRIM, CORE_CAP)
    assert m.cell("stack", "TC05").core
    masked = [f for f in m.findings if f.kind == "MASKED_IN_STACK"]
    assert [(f.call_id, f.plugins) for f in masked] == [("TC05", ("fx_trim",))]


def test_core_plus_plugin_survives_the_report_round_trip() -> None:
    m = _matrix(FURTHER_TRIM, FURTHER_TRIM)
    back = report.matrix_from_dict(json.loads(json.dumps(report.matrix_to_dict(m))))
    assert back.cell("stack", "TC05").core_also
