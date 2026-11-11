# File: tests/test_contract_lockfile.py
# --write-contract writes an expect: file (the --calls format) from a matrix: the same stack meets it, and a stack
# with a plugin left out fails it (exit 5). User calls and cells that never ran are not pinned.
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from helpers import fixture_like_matrix
from test_report_compare import without_rewriter

from proving_ground import cli, matrix, report, testcalls


def _with_contract(m: matrix.Matrix, contract: Path) -> matrix.Matrix:
    """The matrix a --calls CONTRACT --check run would carry: the real loader merges the file into the built-in
    calls, and each call of *m* takes its expectations from there."""
    loaded = {t.id: t.expect for t in testcalls.load_user_calls(contract, testcalls.load_templates())}
    return replace(m, calls=tuple(replace(c, expect=loaded.get(c.id, {})) for c in m.calls))


def _write(tmp_path: Path, m: matrix.Matrix) -> Path:
    path = tmp_path / "stack.lock.yaml"
    args = argparse.Namespace(write_contract=str(path))
    assert cli.write_exports(m, args) == [str(path)]
    return path


def test_contract_pins_every_built_in_cell_to_its_observed_outcome(tmp_path) -> None:
    m = fixture_like_matrix()
    checked = _with_contract(m, _write(tmp_path, m))
    assert cli.contract_total(checked) == len(m.calls) * len(m.columns)
    assert matrix.check_expectations(checked) == ([], [])
    assert cli.exit_code_for(checked, check=True) == 0
    assert cli.expectation_lines(checked)[-1] == "contract: 25 of 25 expectations met"


def test_the_same_contract_fails_with_exit_5_after_leaving_a_plugin_out(tmp_path) -> None:
    contract = _write(tmp_path, fixture_like_matrix())
    checked = _with_contract(without_rewriter(), contract)
    unmet, missing = matrix.check_expectations(checked)
    assert ("TC03", "stack", ("REWROTE",), "PASSED_THROUGH") in unmet
    assert {col for _cid, col in missing} == {"solo:fx_rewriter"}
    assert cli.exit_code_for(checked, check=True) == 5
    assert "CHECK FAILED TC03 × stack: expected REWROTE, observed PASSED THROUGH" in cli.expectation_lines(checked)


def test_user_calls_and_cells_that_never_ran_are_not_pinned(tmp_path) -> None:
    m = fixture_like_matrix()
    user = replace(m.calls[0], id="U01", provenance="user")
    not_run = replace(m.cells[("stack", "TC02")], outcomes=(matrix.Outcome.NOT_RUN,))
    cells = {**{(col, "U01"): replace(c, call_id="U01") for (col, cid), c in m.cells.items() if cid == "TC01"},
             **m.cells, ("stack", "TC02"): not_run}
    text = report.render_contract(replace(m, calls=(*m.calls, user), cells=cells))
    assert "# not pinned (a user call, or no cell that ran): U01" in text
    assert "U01:" not in text.split("expect:", 1)[1]
    tc02 = next(line for line in text.splitlines() if line.startswith("  TC02:"))
    assert '"stack"' not in tc02 and '"solo:fx_guard": [BLOCKED]' in tc02


def test_contract_from_a_matrix_with_no_pinnable_cell_is_an_empty_contract(tmp_path) -> None:
    m = fixture_like_matrix()
    empty = replace(m, cells={k: replace(c, outcomes=(matrix.Outcome.NOT_RUN,)) for k, c in m.cells.items()})
    path = tmp_path / "empty.yaml"
    path.write_text(report.render_contract(empty), encoding="utf-8")
    checked = _with_contract(empty, path)
    assert cli.contract_total(checked) == 0 and cli.exit_code_for(checked, check=True) == 5


def test_contract_text_is_plain_and_neutral() -> None:
    text = report.render_contract(fixture_like_matrix())
    assert text.startswith("# expect: contract written by proving-ground ")
    assert "schema: provingground.testcalls.v1\nexpect:\n" in text
    assert report.forbidden_claims(text) == []


def test_write_contract_to_an_unwritable_place_is_export_failed(tmp_path, capsys) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    args = argparse.Namespace(action="show", call=None, write_contract=str(blocker / "lock.yaml"))
    cli.save(fixture_like_matrix())
    assert cli.handle_cli(args) == 2
    assert capsys.readouterr().out.startswith("EXPORT_FAILED")
