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
    return cli.assemble(drill, cli.all_infos(templates, settings), "test", cli.now_iso(), None,
                        cli.drill_options(settings))


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
def test_all_wires_leaves_the_test_call_cells_alone(wire_matrix) -> None:
    assert {wire_matrix.cell(col, cid).primary for col in wire_matrix.columns for cid in ("TC01", "TC02")} == \
        {Outcome.PASSED_THROUGH}
