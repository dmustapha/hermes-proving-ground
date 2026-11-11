# File: tests/test_drill_fixtures_e2e.py
# The real scripted drill on the fixture stack: model never called, golden outcomes, cross-check finding.
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from proving_ground import cli, runner, testcalls
from proving_ground.matrix import Outcome

DATA = Path(__file__).resolve().parent / "data"
GOLDEN = {("solo:fx_guard", "TC02"): Outcome.BLOCKED, ("solo:fx_rewriter", "TC03"): Outcome.REWROTE,
          ("solo:fx_trimmer", "TC04"): Outcome.RESULT_CHANGED, ("stack", "TC02"): Outcome.BLOCKED,
          ("stack", "TC03"): Outcome.REWROTE, ("stack", "TC04"): Outcome.RESULT_CHANGED,
          ("baseline", "TC10"): Outcome.APPROVAL_GATED, ("baseline", "TC01"): Outcome.PASSED_THROUGH}


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def fixture_matrix():
    import tempfile

    base = Path(tempfile.gettempdir()).resolve()
    hermes = Path(shutil.which("hermes") or str(Path.home() / ".local/bin/hermes"))
    settings = runner.RunnerSettings(hermes, ROOT, base / "provingground-cache" / "installs", base)
    templates = testcalls.load_templates()
    drill = runner.run_drill(runner.fixture_subjects(ROOT), templates, settings)
    return cli.assemble(drill, cli.call_infos(templates), runner.hermes_version(settings.hermes_bin, settings), cli.now_iso())


@pytest.mark.hermes
def test_golden_outcomes(fixture_matrix) -> None:
    for (column, cid), outcome in GOLDEN.items():
        assert fixture_matrix.cell(column, cid).primary is outcome, (column, cid)
    assert fixture_matrix.aborted == {}


@pytest.mark.hermes
def test_cross_plugin_finding_is_found(fixture_matrix) -> None:
    assert any(f.kind == "REWRITE_UNSEEN_BY" and f.call_id == "TC03" for f in fixture_matrix.findings)


@pytest.mark.hermes
def test_crashing_plugin_is_reported_load_failed(settings, tmp_path) -> None:
    shutil.copytree(DATA / "fx_crash", tmp_path / "fx_crash")
    subjects = [runner.PluginSource("fx_crash", tmp_path / "fx_crash")]
    templates = testcalls.load_templates()[:2]
    drill = runner.run_drill(subjects, templates, settings)
    assert all(column.abort_reason is None for column in drill.columns)
    m = cli.assemble(drill, cli.call_infos(templates), "test", cli.now_iso())
    assert {m.cell("solo:fx_crash", t.id).primary for t in templates} == {Outcome.LOAD_FAILED}
    assert m.load_failed == {"solo:fx_crash": ("fx_crash",)}


@pytest.mark.hermes
def test_without_rerun_clears_the_cell_and_the_contract(settings, fixture_matrix) -> None:
    """The fix loop (demo scene 5): --without the implicated plugin, rerun, the cell and the contract clear."""
    from dataclasses import replace

    from proving_ground import matrix

    contract = {"stack": ("PASSED_THROUGH", "BLOCKED")}
    with_rewriter = replace(fixture_matrix, calls=tuple(replace(c, expect=contract) if c.id == "TC03" else c
                                                        for c in fixture_matrix.calls))
    assert matrix.check_expectations(with_rewriter)[0], "the full stack must break the TC03 contract"
    templates = [t for t in testcalls.load_templates() if t.id in ("TC02", "TC03")]
    drill = runner.run_drill(runner.fixture_subjects(ROOT, without=("fx_rewriter",)), templates, settings)
    infos = [replace(i, expect=contract) if i.id == "TC03" else i for i in cli.call_infos(templates)]
    m = cli.assemble(drill, infos, "test", cli.now_iso())
    assert m.cell("stack", "TC03").primary is Outcome.PASSED_THROUGH
    assert not any(f.kind == "REWRITE_UNSEEN_BY" for f in m.findings)
    assert matrix.check_expectations(m) == ([], [])
    assert cli.exit_code_for(m, check=True) == 0
