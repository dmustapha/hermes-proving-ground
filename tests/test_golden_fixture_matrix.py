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
CALLS = tuple(f"TC{i:02d}" for i in range(1, 25))
P, B, W, R, A, X = (Outcome.PASSED_THROUGH, Outcome.BLOCKED, Outcome.REWROTE, Outcome.RESULT_CHANGED,
                    Outcome.APPROVAL_GATED, Outcome.NOT_EXECUTED)
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
            "TC10": (A, A, A, A, A),  # core approval gate
            # TC11-TC24 are added rows; TC01-TC10 above are unchanged. The demo plugins act on none of them.
            "TC11": (R, R, R, R, R),  # Hermes core strips the ANSI codes itself
            "TC12": (P, P, P, P, P),
            "TC13": (P, P, P, P, P),
            "TC14": (P, P, P, P, P),
            "TC15": (P, P, P, P, P),
            "TC16": (R, R, R, R, R),  # Hermes core masks the documented example key itself
            "TC17": (P, P, P, P, P),  # Hermes core refuses the .env read the same way in every column
            "TC18": (P, P, P, P, P),
            "TC19": (P, P, P, P, P),
            "TC20": (P, P, P, P, P),
            "TC21": (X, X, X, X, X),  # block-only probes: PROVING GROUND's own block, nothing ran
            "TC22": (X, X, X, X, X),
            "TC23": (X, X, X, X, X),
            "TC24": (X, X, X, X, X)}
CORE = {(cid, c) for cid in ("TC05", "TC10", "TC11", "TC16") for c in COLUMNS}
# Liveness of every PASSED THROUGH cell outside the baseline: fx_trimmer's transform_terminal_output never fires on
# the file-tool calls (TC06-TC08, TC17-TC20), so those cells are "not invoked"; every other one ran and changed
# nothing. NOT EXECUTED probe rows never carry a liveness suffix (liveness_of only labels PASSED THROUGH).
NOT_INVOKED = {(cid, "solo:fx_trimmer") for cid in ("TC06", "TC07", "TC08", "TC17", "TC18", "TC19", "TC20")}
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
