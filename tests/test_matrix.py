# File: tests/test_matrix.py
# Pure classification + attribution against the expected fixture matrix (ARCHITECTURE §3.10).
from __future__ import annotations

from helpers import fixture_like_matrix

from proving_ground.matrix import Outcome


def primary(m, column: str, cid: str) -> Outcome:
    return m.cell(column, cid).primary


def test_fixture_like_outcomes() -> None:
    m = fixture_like_matrix()
    assert primary(m, "baseline", "TC01") is Outcome.PASSED_THROUGH
    assert primary(m, "solo:fx_guard", "TC02") is Outcome.BLOCKED
    assert primary(m, "solo:fx_rewriter", "TC03") is Outcome.REWROTE
    assert primary(m, "solo:fx_trimmer", "TC04") is Outcome.RESULT_CHANGED
    assert m.cell("solo:fx_trimmer", "TC04").hop == "terminal_output"
    assert primary(m, "stack", "TC03") is Outcome.REWROTE


def test_core_truncation_is_never_blamed() -> None:
    m = fixture_like_matrix()
    assert m.cell("baseline", "TC05").core
    assert primary(m, "stack", "TC05") is Outcome.PASSED_THROUGH


def test_attribution_names_the_solo_plugin() -> None:
    m = fixture_like_matrix()
    by_call = {(a.call_id, a.outcome): a for a in m.attributions}
    assert by_call[("TC02", Outcome.BLOCKED)].plugins == ("fx_guard",)
    assert by_call[("TC04", Outcome.RESULT_CHANGED)].kind == "solo-match"


def test_rewrite_unseen_by_guard_finding() -> None:
    m = fixture_like_matrix()
    unseen = [f for f in m.findings if f.kind == "REWRITE_UNSEEN_BY"]
    assert len(unseen) == 1 and unseen[0].call_id == "TC03"
    assert unseen[0].plugins == ("fx_rewriter", "fx_guard")


def test_raised_callback_is_errored_not_blocked() -> None:
    from helpers import column
    from proving_ground import matrix

    crash = {"TC02": {"block": "pre_tool_call plugin callback fx_bad.pre raised RuntimeError: boom"}}
    m = matrix.build_matrix([column("baseline", (), {}), column("solo:fx_bad", ("fx_bad",), crash)], None, {},
                            [matrix.CallInfo(i, "terminal", i, i) for i in ("TC01", "TC02", "TC03", "TC04", "TC05")],
                            "v", "t", 1.0)
    assert primary(m, "solo:fx_bad", "TC02") is Outcome.ERRORED
    assert "raised an exception" in m.cell("solo:fx_bad", "TC02").note
    assert all(a.kind != "solo-match" for a in m.attributions if a.call_id == "TC02")


def test_unconfirmed_plugin_is_load_failed_never_passed_through() -> None:
    from helpers import column
    from proving_ground import matrix

    infos = [matrix.CallInfo(i, "terminal", i, i) for i in ("TC01", "TC02", "TC03", "TC04", "TC05")]
    cols = [column("baseline", (), {}), column("solo:fx_crash", ("fx_crash",), {}, loaded=()),
            column("solo:fx_guard", ("fx_guard",), {}),
            column("stack", ("fx_crash", "fx_guard"), {}, loaded=("fx_guard",))]
    m = matrix.build_matrix(cols, None, {}, infos, "v", "t", 1.0)
    for cid in ("TC01", "TC02", "TC03", "TC04", "TC05"):
        assert primary(m, "solo:fx_crash", cid) is Outcome.LOAD_FAILED
        assert primary(m, "stack", cid) is not Outcome.PASSED_THROUGH
    assert m.load_failed["solo:fx_crash"] == ("fx_crash",)
    assert "load-failed: 1" in m.conditions.coverage


def test_in_place_mutation_is_rewrote() -> None:
    from helpers import SANDBOX, URL, plan
    from proving_ground import matrix

    events = [{"ev": "activated"}, {"ev": "pre_tool_call", "tool": "terminal", "args": {"command": "echo changed"},
                                    "tool_call_id": "pgcall_TC01"},
              {"ev": "post_tool_call", "tool": "terminal", "args": {"command": "echo changed"}, "status": "ok",
               "result": "{}", "tool_call_id": "pgcall_TC01"}]
    col = matrix.column_from_events("solo:x", (), plan(("TC01",)), events, SANDBOX, URL, [], None, 1.0)
    obs = col.observations["TC01"]
    assert obs.injected_args == {"command": "echo PG_PLAIN_MARKER TC01"}
    cell = matrix.classify("solo:x", obs, obs, False)
    assert cell.primary is Outcome.REWROTE and cell.hop == "args-inplace"


def test_column_without_done_is_incomplete() -> None:
    from proving_ground import runner

    assert runner.abort_reason_from([{"ev": "activated"}], None, 1) == "INCOMPLETE (child exit 1)"
    assert runner.abort_reason_from([{"ev": "activated"}, {"ev": "done"}], None, 0) is None


def test_recorder_entries_are_per_column() -> None:
    from proving_ground import runner

    rec = runner.Recorder()
    rec.entries = ["aaaa/TC09", "bbbb/TC01"]
    assert rec.for_column("aaaa") == ["TC09"] and rec.for_column("bbbb") == ["TC01"] and rec.for_column("cccc") == []
    rec._server.server_close()


def test_tally_pluralises() -> None:
    assert "1 cross-plugin finding" in __import__("proving_ground.matrix", fromlist=["x"]).tally(fixture_like_matrix())


def test_contract_check_reports_unmet_expectations() -> None:
    from dataclasses import replace

    from proving_ground import matrix

    m = fixture_like_matrix()
    calls = tuple(replace(c, expect={"stack": ("PASSED_THROUGH", "BLOCKED")}) if c.id == "TC03" else c for c in m.calls)
    unmet, missing = matrix.check_expectations(replace(m, calls=calls))
    assert unmet == [("TC03", "stack", ("PASSED_THROUGH", "BLOCKED"), "REWROTE")] and missing == []


def test_requires_plugins_load_first() -> None:
    from pathlib import Path

    from proving_ground import runner

    a, b = runner.PluginSource("zz_base", Path("/x")), runner.PluginSource("aa_addon", Path("/y"), requires=("zz_base",))
    assert [s.key for s in runner.load_order([b, a])] == ["zz_base", "aa_addon"]
    assert [s.key for s in runner.plan_columns([b, a])[1].plugins] == ["zz_base", "aa_addon"]
