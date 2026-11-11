# The opt-in drill options on real child Hermes runs: --all-wires on two test plugins that add context and rewrite
# the reply. The default drill is pinned separately by the golden snapshot (test_golden_fixture_matrix.py).
from __future__ import annotations

import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from proving_ground import cli, runner, testcalls
from proving_ground.matrix import CONTEXT_ID, REPLY_ID, Outcome

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(__file__).resolve().parent / "data"


def base_settings() -> runner.RunnerSettings:
    base = Path(tempfile.gettempdir()).resolve()
    hermes = Path(shutil.which("hermes") or str(Path.home() / ".local/bin/hermes"))
    return runner.RunnerSettings(hermes, ROOT, base / "provingground-cache" / "installs", base)


@pytest.fixture(scope="module")
def wire_matrix():
    settings = replace(base_settings(), wires=True)
    subjects = [runner.PluginSource(k, DATA / k) for k in ("fx_context", "fx_replier")]
    templates = testcalls.load_templates()[:2]
    drill = runner.run_drill(subjects, templates, settings)
    return cli.assemble(drill, cli.all_infos(templates, settings), "test", cli.now_iso())


@pytest.mark.hermes
def test_all_wires_sees_added_context_and_a_rewritten_reply(wire_matrix) -> None:
    m = wire_matrix
    assert m.aborted == {} and m.load_failed == {}
    expected = {("baseline", CONTEXT_ID): Outcome.PASSED_THROUGH, ("baseline", REPLY_ID): Outcome.PASSED_THROUGH,
                ("solo:fx_context", CONTEXT_ID): Outcome.CONTEXT_ADDED,
                ("solo:fx_context", REPLY_ID): Outcome.PASSED_THROUGH,
                ("solo:fx_replier", CONTEXT_ID): Outcome.PASSED_THROUGH,
                ("solo:fx_replier", REPLY_ID): Outcome.REPLY_CHANGED,
                ("stack", CONTEXT_ID): Outcome.CONTEXT_ADDED, ("stack", REPLY_ID): Outcome.REPLY_CHANGED}
    assert {key: m.cell(*key).primary for key in expected} == expected
    assert m.cell("stack", CONTEXT_ID).hop == "user turn"
    kinds = {(a.call_id, a.kind, a.plugins) for a in m.attributions}
    assert {(CONTEXT_ID, "solo-match", ("fx_context",)), (REPLY_ID, "solo-match", ("fx_replier",))} <= kinds
    assert m.conditions.n_calls == 2 and "wires" in m.options


@pytest.mark.hermes
def test_pairs_names_the_pair_behind_an_effect_seen_only_in_the_stack() -> None:
    settings = replace(base_settings(), pairs=True)
    subjects = [runner.PluginSource("fx_guard", ROOT / "fixtures" / "fx_guard")] + \
        [runner.PluginSource(k, DATA / k) for k in ("fx_pair_a", "fx_pair_b")]
    templates = [t for t in testcalls.load_templates() if t.id in ("TC01", "TC02")]
    drill = runner.run_drill(subjects, templates, settings)
    m = cli.assemble(drill, cli.all_infos(templates, settings), "test", cli.now_iso())
    assert m.aborted == {} and m.cell("stack", "TC01").outcomes == (Outcome.REWROTE, Outcome.RESULT_CHANGED)
    assert {c for c in m.columns if c.startswith("pair:")} == {"pair:fx_guard+fx_pair_a", "pair:fx_guard+fx_pair_b",
                                                               "pair:fx_pair_a+fx_pair_b"}
    [finding] = [f for f in m.findings if f.kind == "STACK_ONLY_INTERACTION"]
    assert finding.call_id == "TC01" and finding.plugins == ("fx_pair_a", "fx_pair_b")
    assert m.options["pairs"] == "3 pair columns run for 1 effect seen only in the full stack"


@pytest.mark.hermes
def test_repeat_marks_a_stateful_plugins_disagreeing_solo_runs_unstable(tmp_path) -> None:
    plugin = tmp_path / "fx_flaky"
    shutil.copytree(DATA / "fx_flaky", plugin)
    (plugin / "counter_path.txt").write_text(str(tmp_path / "visits"), encoding="utf-8")
    settings = replace(base_settings(), repeat=2)
    templates = [t for t in testcalls.load_templates() if t.id == "TC01"]
    drill = runner.run_drill([runner.PluginSource("fx_flaky", plugin)], templates, settings)
    m = cli.assemble(drill, cli.all_infos(templates, settings), "test", cli.now_iso())
    cell = m.cell("solo:fx_flaky", "TC01")
    assert cell.primary is Outcome.BLOCKED and cell.varied == ("BLOCKED", "PASSED THROUGH")
    assert (tmp_path / "visits").read_text(encoding="utf-8") == "2"
    assert "1 cells disagree" in m.options["repeat"]


@pytest.mark.hermes
def test_all_wires_leaves_the_test_call_cells_alone(wire_matrix) -> None:
    assert {wire_matrix.cell(col, cid).primary for col in wire_matrix.columns for cid in ("TC01", "TC02")} == \
        {Outcome.PASSED_THROUGH}
