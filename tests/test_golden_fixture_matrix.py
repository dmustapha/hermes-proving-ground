# The full-matrix golden snapshot of the real scripted fixture drill. Every
# (column, call) primary outcome is pinned, so a false positive anywhere (say fx_guard x TC03 turning RESULT CHANGED)
# fails loudly. One drill also proves that every child ran with bytecode off and loaded the stack in PG's
# computed order.
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from proving_ground import cli, runner, testcalls
from proving_ground.matrix import Outcome

ROOT = Path(__file__).resolve().parent.parent
COLUMNS = ("baseline", "solo:fx_guard", "solo:fx_rewriter", "solo:fx_trimmer", "stack")
CALLS = tuple(f"TC{i:02d}" for i in range(1, 11))
P, B, W, R, A = (Outcome.PASSED_THROUGH, Outcome.BLOCKED, Outcome.REWROTE, Outcome.RESULT_CHANGED,
                 Outcome.APPROVAL_GATED)
#            baseline  fx_guard  fx_rewriter  fx_trimmer  stack
SNAPSHOT = {"TC01": (P, P, P, P, P),
            "TC02": (P, B, P, P, B),
            "TC03": (P, P, W, P, W),
            "TC04": (P, P, P, R, R),
            "TC05": (R, R, R, R, R),  # core truncation, labelled (core) in every column
            "TC06": (P, P, P, P, P),
            "TC07": (P, P, P, P, P),
            "TC08": (P, P, P, P, P),
            "TC09": (P, P, P, P, P),
            "TC10": (A, A, A, A, A)}  # core approval gate
CORE = {("TC05", c) for c in COLUMNS} | {("TC10", c) for c in COLUMNS}
# Liveness of every PASSED THROUGH cell outside the baseline: fx_trimmer's transform_terminal_output never fires on
# the three file-tool calls, so those cells are "not invoked"; every other one ran and changed nothing.
NOT_INVOKED = {("TC06", "solo:fx_trimmer"), ("TC07", "solo:fx_trimmer"), ("TC08", "solo:fx_trimmer")}
FIRED = {"fx_guard": ("hook pre_tool_call",), "fx_rewriter": ("hook pre_tool_call",),
         "fx_trimmer": ("hook transform_terminal_output",)}


def liveness_of(primary: str, column: str, cid: str) -> str:
    if primary != Outcome.PASSED_THROUGH.value or column == "baseline":
        return ""
    return "not invoked" if (cid, column) in NOT_INVOKED else "ran, no change"


@pytest.fixture(scope="module")
def drill():
    base = Path(tempfile.gettempdir()).resolve()
    hermes = Path(shutil.which("hermes") or str(Path.home() / ".local/bin/hermes"))
    settings = runner.RunnerSettings(hermes, ROOT, base / "provingground-cache" / "installs", base)
    templates = testcalls.load_templates()
    run = runner.run_drill(runner.fixture_subjects(ROOT), templates, settings)
    m = cli.assemble(run, cli.call_infos(templates), runner.hermes_version(settings.hermes_bin, settings), cli.now_iso())
    return run, m


@pytest.mark.hermes
def test_full_matrix_snapshot(drill) -> None:
    _run, m = drill
    assert m.columns == COLUMNS and tuple(c.id for c in m.calls) == CALLS
    actual = {cid: tuple(m.cell(col, cid).primary for col in COLUMNS) for cid in CALLS}
    assert actual == SNAPSHOT
    assert {(cid, col) for (col, cid), cell in m.cells.items() if cell.core} == CORE
    assert m.aborted == {} and m.load_failed == {} and m.unstable == frozenset()
    assert [f.kind for f in m.findings] == ["REWRITE_UNSEEN_BY"]


@pytest.mark.hermes
def test_liveness_labels_and_census_snapshot(drill) -> None:
    _run, m = drill
    assert {(cid, col): m.cell(col, cid).liveness for col in COLUMNS for cid in CALLS} == \
        {(cid, col): liveness_of(m.cell(col, cid).primary.value, col, cid) for col in COLUMNS for cid in CALLS}
    assert {k: c.fired for k, c in m.census.items()} == FIRED
    assert all(c.watched and not c.late and not c.inner for c in m.census.values())


@pytest.mark.hermes
def test_every_child_ran_with_bytecode_off(drill) -> None:
    run, _m = drill
    columns = list(run.columns) + [run.baseline_repeat] + list(run.crosscheck.values())
    assert {c.name: c.bytecode_off for c in columns} == {c.name: True for c in columns}


@pytest.mark.hermes
def test_child_load_order_matches_the_computed_order(drill) -> None:
    run, m = drill
    stack = next(c for c in run.columns if c.name == "stack")
    assert stack.child_order == ("fx_guard", "fx_rewriter", "fx_trimmer") == m.conditions.load_order
    assert m.conditions.order_note == ""
