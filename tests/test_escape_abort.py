# File: tests/test_escape_abort.py
# I3 end to end: a plugin that rewrites a call to land outside the sandbox makes the column stop with ESCAPED.
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from proving_ground import matrix, runner, testcalls

DATA = Path(__file__).resolve().parent / "data"


@pytest.mark.hermes
def test_rewrite_outside_sandbox_is_escaped_and_stops(settings, tmp_path, tmp_path_factory) -> None:
    outside = tmp_path_factory.mktemp("outside-target")
    plugin = tmp_path / "fx_escaper"
    shutil.copytree(DATA / "fx_escaper", plugin)
    (plugin / "outside_path.txt").write_text(str(outside), encoding="utf-8")
    templates = testcalls.load_templates()
    with runner.Recorder() as recorder:
        run = runner.run_column(runner.ColumnSpec("solo:fx_escaper", (runner.PluginSource("fx_escaper", plugin),)),
                                templates, testcalls.make_canary(), recorder, settings)
    assert run.abort_reason == "ESCAPED"
    column = runner._to_column(run)
    assert column.observations["TC01"].escaped_reasons
    later = [cid for cid, obs in column.observations.items() if cid != "TC01"]
    assert all(not column.observations[cid].ran for cid in later)
    assert not Path(run.sandbox).parent.exists()  # this column's root is gone
    assert not [r for r in Path(settings.temp_base).glob("provingground-*") if runner._ours(r)]  # and none of ours
