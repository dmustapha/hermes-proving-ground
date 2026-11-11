# File: tests/test_middleware_drill_e2e.py
# A real drill over three test plugins whose keys sort after "proving-ground": an llm_execution reply stamper, an
# llm_request tagger and a tool_execution short-circuit. With PROVING GROUND's child copy keyed last, each one runs
# inside a real child and its effect is attributed to it by its solo run. Before, the stamper sat outside PG's
# frame and never ran, and its column read PASSED THROUGH.
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from proving_ground import cli, report, runner, testcalls
from proving_ground.matrix import Outcome

ROOT = Path(__file__).resolve().parent.parent
KEYS = ("tx_exec_replacer", "tx_reply_stamper", "tx_request_tagger")
CALLS = tuple(f"TC{i:02d}" for i in range(1, 11))


@pytest.fixture(scope="module")
def drill():
    base = Path(tempfile.gettempdir()).resolve()
    hermes = Path(shutil.which("hermes") or str(Path.home() / ".local/bin/hermes"))
    settings = runner.RunnerSettings(hermes, ROOT, base / "provingground-cache" / "installs", base)
    subjects = [runner.PluginSource(k, ROOT / "tests" / "data" / k) for k in KEYS]
    templates = testcalls.load_templates()
    run = runner.run_drill(subjects, templates, settings)
    return cli.assemble(run, cli.call_infos(templates), "test", cli.now_iso(), runner.leftovers(base))


@pytest.mark.hermes
def test_an_llm_execution_middleware_keyed_after_proving_ground_runs_and_is_attributed(drill) -> None:
    assert all(Outcome.MODEL_REPLY_CHANGED in drill.cell("solo:tx_reply_stamper", cid).outcomes for cid in CALLS)
    assert {a.plugins for a in drill.attributions if a.outcome is Outcome.MODEL_REPLY_CHANGED} == {("tx_reply_stamper",)}
    assert drill.census["tx_reply_stamper"].fired == ("middleware llm_execution",)


@pytest.mark.hermes
def test_an_llm_request_rewrite_is_request_rewritten_with_its_trace_label(drill) -> None:
    cell = drill.cell("solo:tx_request_tagger", "TC01")
    assert cell.outcomes == (Outcome.REQUEST_REWRITTEN,) and "tx_request_tagger" in cell.note
    assert {a.plugins for a in drill.attributions if a.outcome is Outcome.REQUEST_REWRITTEN} == {("tx_request_tagger",)}


@pytest.mark.hermes
def test_a_tool_execution_short_circuit_is_execution_replaced_and_the_rest_ran(drill) -> None:
    assert drill.cell("solo:tx_exec_replacer", "TC01").outcomes == (Outcome.EXECUTION_REPLACED,)
    assert report.cell_label(drill.cell("solo:tx_exec_replacer", "TC02")) == "PASSED THROUGH (ran, no change)"
    attributed = [a for a in drill.attributions if a.outcome is Outcome.EXECUTION_REPLACED]
    assert [(a.call_id, a.kind, a.plugins) for a in attributed] == [("TC01", "solo-match", ("tx_exec_replacer",))]


@pytest.mark.hermes
def test_core_effects_stay_core_and_the_drill_left_nothing_behind(drill) -> None:
    for key in KEYS:
        assert drill.cell(f"solo:{key}", "TC05").core and drill.cell(f"solo:{key}", "TC10").core
    assert [a.kind for a in drill.attributions if a.call_id == "TC05" and a.outcome is Outcome.RESULT_CHANGED] == ["core"]
    assert drill.aborted == {} and drill.load_failed == {}
    assert (drill.conditions.leftover_processes, drill.conditions.leftover_roots) == (0, 0)
    assert drill.conditions.connects_non_loopback == 0
