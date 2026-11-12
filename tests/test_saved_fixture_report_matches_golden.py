# File: tests/test_saved_fixture_report_matches_golden.py
# A saved JSON report of `hermes provingground --fixtures` (from an installed copy, on any OS) must match the same
# golden matrix the in-process drill pins, with no aborted column and zero leftover processes and roots.
# CI's Linux drill sets PG_FIXTURE_REPORT to the report it just wrote; without it the committed (and hosted)
# docs/fixture-report.json is checked, so the published report cannot drift from the golden.
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from test_golden_fixture_matrix import CALLS, COLUMNS, CORE, FIRED, SNAPSHOT, liveness_of

REPORT_ENV = "PG_FIXTURE_REPORT"
GOLDEN_TALLY = "24 test calls · 3 plugins · 3 distinct effects recorded · 1 cross-plugin finding"

COMMITTED_REPORT = Path(__file__).resolve().parents[1] / "docs" / "fixture-report.json"


@pytest.fixture(scope="module")
def report() -> dict:
    return json.loads(Path(os.environ.get(REPORT_ENV) or COMMITTED_REPORT).read_text(encoding="utf-8"))


def test_saved_report_matrix_is_the_golden_matrix(report) -> None:
    assert tuple(report["columns"]) == COLUMNS
    assert tuple(c["id"] for c in report["calls"]) == CALLS
    cells = {(c["column"], c["call_id"]): c for c in report["cells"]}
    actual = {cid: tuple(cells[(col, cid)]["primary"] for col in COLUMNS) for cid in CALLS}
    assert actual == {cid: tuple(o.value for o in row) for cid, row in SNAPSHOT.items()}
    assert {(cid, col) for (col, cid), cell in cells.items() if cell["core"]} == CORE


def test_saved_report_liveness_and_census(report) -> None:
    cells = {(c["column"], c["call_id"]): c for c in report["cells"]}
    assert all(cells[(col, cid)].get("liveness", "") == "" for col in COLUMNS for cid in CALLS)
    assert "census" not in report


def test_saved_report_tally_and_findings(report) -> None:
    assert report["tally"] == GOLDEN_TALLY
    assert [f["kind"] for f in report["findings"]] == ["REWRITE_UNSEEN_BY"]
    assert report["aborted"] == {} and report["load_failed"] == {} and report["unstable"] == []


def test_saved_report_left_nothing_behind(report) -> None:
    conditions = report["conditions"]
    assert (conditions["leftover_processes"], conditions["leftover_roots"]) == (0, 0)
    assert conditions["connects_non_loopback"] == 0
    assert conditions["model"].startswith("NOT CALLED")
