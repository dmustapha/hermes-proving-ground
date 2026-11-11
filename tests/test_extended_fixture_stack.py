# File: tests/test_extended_fixture_stack.py
# The extended demo stack: the three demo plugins plus fixtures/extended/fx_redactor, a transform_terminal_output
# plugin that returns the output string on every call. Hermes keeps the first string an output transform returns
# (tools/terminal_tool_result.py:136-147) and loads plugins alphabetically, so in the full stack fx_redactor runs
# before fx_trimmer and fx_trimmer's TC04 change never reaches the model: MASKED_IN_STACK. Every (column, call)
# primary outcome of the real drill is pinned, and the trimmer author's contract fails in the stack and clears
# with --without fx_redactor.
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from proving_ground import cli, matrix, runner, testcalls
from proving_ground.matrix import Outcome

ROOT = Path(__file__).resolve().parent.parent
EXTENDED = ROOT / "fixtures" / "extended"
COLUMNS = ("baseline", "solo:fx_guard", "solo:fx_redactor", "solo:fx_rewriter", "solo:fx_trimmer", "stack")
CALLS = tuple(f"TC{i:02d}" for i in range(1, 11))
P, B, W, R, A = (Outcome.PASSED_THROUGH, Outcome.BLOCKED, Outcome.REWROTE, Outcome.RESULT_CHANGED,
                 Outcome.APPROVAL_GATED)
#            baseline  fx_guard  fx_redactor  fx_rewriter  fx_trimmer  stack
SNAPSHOT = {"TC01": (P, P, R, P, P, R),
            "TC02": (P, B, P, P, P, B),
            "TC03": (P, P, P, W, P, W),
            "TC04": (P, P, P, P, R, P),  # fx_trimmer's change is masked by fx_redactor's earlier string
            "TC05": (R, R, R, R, R, R),  # core truncation, labelled (core) in every column
            "TC06": (P, P, P, P, P, P),
            "TC07": (P, P, P, P, P, P),
            "TC08": (P, P, P, P, P, P),
            "TC09": (P, P, P, P, P, P),
            "TC10": (A, A, A, A, A, A)}  # core approval gate
CORE = {("TC05", c) for c in COLUMNS} | {("TC10", c) for c in COLUMNS}
LOAD_ORDER = ("fx_guard", "fx_redactor", "fx_rewriter", "fx_trimmer")


def extended_subjects(without: tuple[str, ...] = ()) -> list[runner.PluginSource]:
    """What `--fixtures extended` would select: the base demo plugins plus every plugin under fixtures/extended."""
    base = runner.fixture_subjects(ROOT, without)
    extra = [runner.PluginSource(p.name, p) for p in sorted(EXTENDED.iterdir())
             if (p / "plugin.yaml").is_file() and p.name not in without]
    return runner.load_order(base + extra)


def _settings() -> runner.RunnerSettings:
    base = Path(tempfile.gettempdir()).resolve()
    hermes = Path(shutil.which("hermes") or str(Path.home() / ".local/bin/hermes"))
    return runner.RunnerSettings(hermes, ROOT, base / "provingground-cache" / "installs", base)


def _with_contract(templates: list[testcalls.CallTemplate]) -> list[matrix.CallInfo]:
    with_contract = testcalls.load_user_calls(EXTENDED / "contract.yaml", templates)
    return cli.call_infos(with_contract)


@pytest.fixture(scope="module")
def drill():
    settings, templates = _settings(), testcalls.load_templates()
    run = runner.run_drill(extended_subjects(), templates, settings)
    version = runner.hermes_version(settings.hermes_bin, settings)
    return run, cli.assemble(run, _with_contract(templates), version, cli.now_iso())


def test_extended_subjects_are_the_demo_stack_plus_the_redactor() -> None:
    assert tuple(s.key for s in extended_subjects()) == LOAD_ORDER
    assert tuple(s.key for s in extended_subjects(("fx_redactor",))) == ("fx_guard", "fx_rewriter", "fx_trimmer")


def test_extended_contract_loads_with_the_same_validator() -> None:
    infos = {i.id: i for i in _with_contract(testcalls.load_templates())}
    assert dict(infos["TC04"].expect) == {"solo:fx_trimmer": ("RESULT_CHANGED",), "stack": ("RESULT_CHANGED",)}


@pytest.mark.hermes
def test_extended_matrix_snapshot(drill) -> None:
    _run, m = drill
    assert m.columns == COLUMNS and tuple(c.id for c in m.calls) == CALLS
    actual = {cid: tuple(m.cell(col, cid).primary for col in COLUMNS) for cid in CALLS}
    assert actual == SNAPSHOT
    assert {(cid, col) for (col, cid), cell in m.cells.items() if cell.core} == CORE
    assert m.aborted == {} and m.load_failed == {} and m.unstable == frozenset()
    assert m.conditions.load_order == LOAD_ORDER


@pytest.mark.hermes
def test_redactor_masks_the_trimmer_in_the_stack(drill) -> None:
    _run, m = drill
    assert [f.kind for f in m.findings] == ["REWRITE_UNSEEN_BY", "MASKED_IN_STACK"]
    masked = next(f for f in m.findings if f.kind == "MASKED_IN_STACK")
    assert (masked.call_id, masked.plugins) == ("TC04", ("fx_trimmer",))


@pytest.mark.hermes
def test_child_load_order_puts_the_redactor_before_the_trimmer(drill) -> None:
    run, _m = drill
    stack = next(c for c in run.columns if c.name == "stack")
    assert stack.child_order == LOAD_ORDER


@pytest.mark.hermes
def test_trimmer_contract_fails_in_the_stack_and_clears_without_the_redactor(drill) -> None:
    _run, m = drill
    unmet, missing = matrix.check_expectations(m)
    assert (unmet, missing) == ([("TC04", "stack", ("RESULT_CHANGED",), "PASSED_THROUGH")], [])
    assert cli.exit_code_for(m, check=True) == 5
    templates = [t for t in testcalls.load_templates() if t.id == "TC04"]
    rerun = runner.run_drill(extended_subjects(("fx_redactor",)), templates, _settings())
    cleared = cli.assemble(rerun, _with_contract(templates), "test", cli.now_iso())
    assert cleared.cell("stack", "TC04").primary is Outcome.RESULT_CHANGED
    assert not any(f.kind == "MASKED_IN_STACK" for f in cleared.findings)
    assert cli.exit_code_for(cleared, check=True) == 0
