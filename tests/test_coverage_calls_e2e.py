# File: tests/test_coverage_calls_e2e.py
# The real drill on two test-only plugins: tests/data/fx_probe_guard (blocks execute_code, rewrites a delegate_task
# goal) and fx_nudger (one pre_verify nudge after edits). Proves the block-only probes reach other plugins'
# pre_tool_call, that none of them ran in any column, and that the nudge shows as TURN EXTENDED on the edits it
# followed. The guard is installed under the key tx_probe_guard, which sorts after "proving-ground": before PROVING
# GROUND loaded last (runner.child_pg_key), PG's own probe block came first and this guard's block was hidden.
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from proving_ground import cli, runner, testcalls
from proving_ground.matrix import Outcome

DATA = Path(__file__).resolve().parent / "data"
ROOT = Path(__file__).resolve().parent.parent
GUARD = "tx_probe_guard"  # fx_probe_guard, renamed so its key sorts after the old "proving-ground" key
PROBES = ("TC21", "TC22", "TC23", "TC24")
EDITS = ("TC06", "TC08", "TC19", "TC20")
BASELINE_EXPECT = {"PASSED_THROUGH": (Outcome.PASSED_THROUGH, False), "APPROVAL_GATED": (Outcome.APPROVAL_GATED, True),
                   "RESULT_CHANGED_BY_CORE": (Outcome.RESULT_CHANGED, True),
                   "NOT_EXECUTED": (Outcome.NOT_EXECUTED, False)}


@pytest.fixture(scope="module")
def drill():
    base = Path(tempfile.gettempdir()).resolve()
    hermes = Path(shutil.which("hermes") or str(Path.home() / ".local/bin/hermes"))
    settings = runner.RunnerSettings(hermes, ROOT, base / "provingground-cache" / "installs", base)
    work = Path(tempfile.mkdtemp(prefix="pg-coverage-test-", dir=base))
    try:
        shutil.copytree(DATA / "fx_nudger", work / "fx_nudger")
        shutil.copytree(DATA / "fx_probe_guard", work / GUARD)
        manifest = work / GUARD / "plugin.yaml"
        manifest.write_text(manifest.read_text(encoding="utf-8").replace("name: fx_probe_guard", f"name: {GUARD}"),
                            encoding="utf-8")
        subjects = [runner.PluginSource("fx_nudger", work / "fx_nudger"), runner.PluginSource(GUARD, work / GUARD)]
        templates = testcalls.load_templates()
        run = runner.run_drill(subjects, templates, settings)
        m = cli.assemble(run, cli.call_infos(templates), runner.hermes_version(settings.hermes_bin, settings),
                         cli.now_iso())
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return run, m, templates


@pytest.mark.hermes
def test_every_baseline_cell_is_what_its_call_expects(drill) -> None:
    _run, m, templates = drill
    for t in templates:
        outcome, core = BASELINE_EXPECT[t.expect_baseline]
        cell = m.cell("baseline", t.id)
        assert (cell.primary, cell.core) == (outcome, core), t.id
    assert m.aborted == {} and m.load_failed == {}


@pytest.mark.hermes
def test_a_subject_block_and_rewrite_on_probes_show(drill) -> None:
    _run, m, _t = drill
    assert m.cell(f"solo:{GUARD}", "TC21").primary is Outcome.BLOCKED
    assert m.cell("stack", "TC21").primary is Outcome.BLOCKED
    assert m.cell(f"solo:{GUARD}", "TC22").outcomes == (Outcome.REWROTE, Outcome.NOT_EXECUTED)
    assert {m.cell(col, cid).primary for col in ("baseline", "solo:fx_nudger") for cid in PROBES} == \
        {Outcome.NOT_EXECUTED}
    kinds = {(a.call_id, a.outcome, a.plugins) for a in m.attributions}
    assert {("TC21", Outcome.BLOCKED, (GUARD,)), ("TC22", Outcome.REWROTE, (GUARD,))} <= kinds


@pytest.mark.hermes
def test_a_guard_keyed_after_proving_ground_still_blocks_a_probe(drill) -> None:
    run, m, _t = drill
    assert GUARD > "proving-ground" > "fx_nudger"
    stack = next(c for c in run.columns if c.name == "stack")
    assert stack.child_order == ("fx_nudger", GUARD)
    assert m.cell(f"solo:{GUARD}", "TC21").primary is Outcome.BLOCKED
    solo = next(c for c in run.columns if c.name == f"solo:{GUARD}").observations["TC21"]
    assert "fx_probe_guard: code execution is not allowed here" in f"{solo.delivered} {solo.error_message}"


@pytest.mark.hermes
def test_no_probe_ran_in_any_column(drill) -> None:
    run, _m, _t = drill
    columns = list(run.columns) + [run.baseline_repeat]
    for column in columns:
        for cid in PROBES:
            obs = column.observations[cid]
            assert obs.ran and obs.status == "blocked", (column.name, cid)  # post_tool_call fired for the block
            assert (obs.tool_raw, obs.terminal_raw, obs.recorded) == (None, None, False), (column.name, cid)
        assert column.abort_reason is None, column.name
    assert all(c.connects == () or all(loop for _a, loop in c.connects) for c in columns)


@pytest.mark.hermes
def test_a_pre_verify_nudge_is_turn_extended_on_the_edits_and_attributed(drill) -> None:
    _run, m, _t = drill
    for cid in EDITS:
        assert m.cell("baseline", cid).primary is Outcome.PASSED_THROUGH
        assert m.cell("solo:fx_nudger", cid).primary is Outcome.TURN_EXTENDED, cid
        assert m.cell("stack", cid).primary is Outcome.TURN_EXTENDED, cid
        assert m.cell(f"solo:{GUARD}", cid).primary is Outcome.PASSED_THROUGH, cid
    turn = [a for a in m.attributions if a.outcome is Outcome.TURN_EXTENDED]
    assert {a.call_id for a in turn} == set(EDITS) and {a.plugins for a in turn} == {("fx_nudger",)}
