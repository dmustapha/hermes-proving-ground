# File: tests/test_probes_and_verify.py
# Block-only probes (TC21-TC24) and the pre_verify tap, in the child's hooks and in the pure matrix.
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from helpers import SANDBOX, URL
from proving_ground import matrix, observer, probes, safety
from proving_ground.matrix import Outcome

PROBE_PLAN = [{"id": "TC21", "call_ref": "pgcall_TC21", "tool": "execute_code", "args": {"code": "print(1)"},
               "probe": True},
              {"id": "TC06", "call_ref": "pgcall_TC06", "tool": "write_file",
               "args": {"path": f"{SANDBOX}/notes/written.txt", "content": "x"}}]


def _child(tmp_path: Path, calls: list[dict[str, Any]] = PROBE_PLAN) -> tuple[dict[str, Any], Path, Path]:
    state = tmp_path / "home" / safety.STATE_DIRNAME
    state.mkdir(parents=True)
    box = tmp_path / "sandbox"
    box.mkdir()
    (state / safety.PLAN_FILENAME).write_text(json.dumps({"sandbox": str(box), "calls": calls}), encoding="utf-8")
    log = observer.EventLog(state / safety.EVENTS_FILENAME)
    return observer.make_hooks(state, box, log), state, box


def _events(state: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (state / safety.EVENTS_FILENAME).read_text().splitlines()]


# --- the child's hooks -------------------------------------------------------------------------------------------


def test_a_probe_call_is_blocked_with_the_probe_message_and_the_drill_goes_on(tmp_path: Path) -> None:
    hooks, state, _box = _child(tmp_path)
    out = hooks["pre_tool_call"](tool_name="execute_code", args={"code": "print(1)"}, tool_call_id="pgcall_TC21")
    assert out == {"action": "block", "message": safety.PROBE_BLOCK_MESSAGE}
    assert not (state / safety.ABORT_FILENAME).exists()


def test_a_probe_tool_call_that_is_not_in_the_plan_still_aborts(tmp_path: Path) -> None:
    hooks, state, _box = _child(tmp_path)
    out = hooks["pre_tool_call"](tool_name="execute_code", args={"code": "x"}, tool_call_id="pgcall_OTHER")
    assert out == {"action": "block", "message": observer.TOOL_BLOCK_MESSAGE}
    assert (state / safety.ABORT_FILENAME).read_text() == "TOOL_NOT_ALLOWED"


def test_a_planned_id_on_another_off_list_tool_aborts(tmp_path: Path) -> None:
    hooks, state, _box = _child(tmp_path)
    out = hooks["pre_tool_call"](tool_name="send_message", args={}, tool_call_id="pgcall_TC21")
    assert out["message"] == observer.TOOL_BLOCK_MESSAGE and (state / safety.ABORT_FILENAME).exists()


def test_a_plan_entry_without_the_probe_flag_is_not_a_probe(tmp_path: Path) -> None:
    unflagged = [{**PROBE_PLAN[0], "probe": "yes"}]
    hooks, state, _box = _child(tmp_path, unflagged)
    hooks["pre_tool_call"](tool_name="execute_code", args={"code": "print(1)"}, tool_call_id="pgcall_TC21")
    assert (state / safety.ABORT_FILENAME).read_text() == "TOOL_NOT_ALLOWED"


def test_an_unreadable_plan_has_no_probes(tmp_path: Path) -> None:
    assert observer.probe_refs(tmp_path / "nowhere") == frozenset()


def test_a_blocked_probe_whose_args_were_rewritten_outside_is_not_an_escape(tmp_path: Path) -> None:
    hooks, state, _box = _child(tmp_path)
    hooks["post_tool_call"](tool_name="execute_code", args={"code": "open('/etc/x')"}, status="blocked",
                            tool_call_id="pgcall_TC21")
    assert not (state / safety.ABORT_FILENAME).exists()
    assert [e["ev"] for e in _events(state)] == ["post_tool_call"]


def test_a_probe_that_somehow_ran_with_escaping_args_is_still_an_escape(tmp_path: Path) -> None:
    hooks, state, _box = _child(tmp_path)
    hooks["post_tool_call"](tool_name="execute_code", args={"code": "open('/etc/x')"}, status="ok",
                            tool_call_id="pgcall_TC21")
    assert (state / safety.ABORT_FILENAME).read_text() == "ESCAPED"


def test_the_pre_verify_tap_records_the_gate_and_never_nudges(tmp_path: Path) -> None:
    hooks, state, box = _child(tmp_path)
    assert hooks["pre_verify"](attempt=0, changed_paths={f"{box}/b", f"{box}/a"}, coding=False, model="m") is None
    assert hooks["pre_verify"](attempt=1, changed_paths="not-a-list") is None
    first, second = _events(state)
    assert first["changed_paths"] == [f"{box}/a", f"{box}/b"] and first["attempt"] == 0
    assert second["changed_paths"] == []


def test_probe_tools_are_off_the_allow_list_and_their_toolsets_are_enabled() -> None:
    assert not safety.PROBE_TOOLS & safety.ALLOWED_TOOLS
    assert set(safety.CHILD_TOOLSETS.split(",")) == {"terminal", "file", *probes.PROBE_TOOLSETS.values()}
    assert safety.PROBE_BLOCK_MESSAGE.startswith(matrix.HARNESS_PREFIX)


# --- the matrix ----------------------------------------------------------------------------------------------------


def _probe_events(effect: dict[str, Any]) -> list[dict[str, Any]]:
    final = effect.get("final_args", PROBE_PLAN[0]["args"])
    if effect.get("offered") is False:
        return []
    return [{"ev": "pre_tool_call", "tool": "execute_code", "args": PROBE_PLAN[0]["args"], "tool_call_id": "pgcall_TC21"},
            {"ev": "post_tool_call", "tool": "execute_code", "args": final, "status": "blocked",
             "error_type": "plugin_block", "error_message": effect.get("block", safety.PROBE_BLOCK_MESSAGE),
             "result": "{}", "tool_call_id": "pgcall_TC21"}]


def _edit_events(path: str = f"{SANDBOX}/notes/written.txt") -> list[dict[str, Any]]:
    args = {"path": path, "content": "x"}
    return [{"ev": "pre_tool_call", "tool": "write_file", "args": args, "tool_call_id": "pgcall_TC06"},
            {"ev": "tool_raw", "tool": "write_file", "args": args, "result": '{"bytes_written": 1}',
             "tool_call_id": "pgcall_TC06"},
            {"ev": "post_tool_call", "tool": "write_file", "args": args, "status": "ok", "result": '{"bytes_written": 1}',
             "tool_call_id": "pgcall_TC06"}]


def _column(name: str, plugins: tuple[str, ...], probe: dict[str, Any] | None = None, nudges: int = 0,
            verify_path: str = f"{SANDBOX}/notes/written.txt") -> matrix.ColumnRun:
    events = [{"ev": "activated", "calls": 2}, {"ev": "plugins_loaded", "expected": list(plugins),
                                                 "loaded": list(plugins)}]
    events += _probe_events(probe or {}) + _edit_events()
    for attempt in range(nudges + 1):
        events += [{"ev": "done", "steps": 2}, {"ev": "pre_verify", "attempt": attempt, "changed_paths": [verify_path]}]
    return matrix.column_from_events(name, plugins, PROBE_PLAN, events, SANDBOX, URL, [], None, 1.0)


def _build(*columns: matrix.ColumnRun) -> matrix.Matrix:
    infos = [matrix.CallInfo("TC21", "execute_code", "probe", "p"), matrix.CallInfo("TC06", "write_file", "w", "p")]
    return matrix.build_matrix(list(columns), None, {}, infos, "v", "t", 1.0)


def test_pg_s_own_probe_block_is_not_executed_and_quiet() -> None:
    m = _build(_column("baseline", ()), _column("solo:a", ("a",)))
    for column in ("baseline", "solo:a"):
        cell = m.cell(column, "TC21")
        assert cell.primary is Outcome.NOT_EXECUTED and cell.note == probes.NOT_EXECUTED_NOTE
    assert m.attributions == () and matrix.distinct_effects(m) == frozenset()
    assert matrix.quiet_row(m, "TC21")


def test_a_subject_block_on_a_probe_is_blocked_and_attributed() -> None:
    blocked = {"block": "a: code execution is not allowed"}
    m = _build(_column("baseline", ()), _column("solo:a", ("a",), blocked), _column("solo:b", ("b",)),
               _column("stack", ("a", "b"), blocked))
    assert m.cell("solo:a", "TC21").primary is Outcome.BLOCKED
    assert m.cell("solo:b", "TC21").primary is Outcome.NOT_EXECUTED
    assert any(a.call_id == "TC21" and a.kind == "solo-match" and a.plugins == ("a",) for a in m.attributions)
    assert not m.findings


def test_a_subject_rewrite_of_a_probe_shows_even_though_pg_blocked_it() -> None:
    rewrite = {"final_args": {"code": "print(2)"}}
    m = _build(_column("baseline", ()), _column("solo:a", ("a",), rewrite))
    cell = m.cell("solo:a", "TC21")
    assert cell.outcomes == (Outcome.REWROTE, Outcome.NOT_EXECUTED) and cell.primary is Outcome.REWROTE
    assert matrix.rewrites_for_crosscheck(_column("solo:a", ("a",), rewrite)) == []  # a probe is never replayed


def test_a_stack_not_executed_probe_never_reports_a_masked_solo_block() -> None:
    blocked = {"block": "b: no"}
    m = _build(_column("baseline", ()), _column("solo:a", ("a",)), _column("solo:b", ("b",), blocked),
               _column("stack", ("a", "b")))  # b loaded after PROVING GROUND (requires_plugins): PG's block came first
    assert m.cell("stack", "TC21").primary is Outcome.NOT_EXECUTED and m.findings == ()


def test_a_probe_tool_the_child_did_not_offer_is_not_applicable() -> None:
    m = _build(_column("baseline", (), {"offered": False}))
    cell = m.cell("baseline", "TC21")
    assert cell.primary is Outcome.NOT_APPLICABLE and cell.note == probes.NOT_OFFERED_NOTE


def test_a_pre_verify_nudge_marks_the_edits_it_followed_and_is_attributed() -> None:
    m = _build(_column("baseline", ()), _column("solo:nudger", ("nudger",), nudges=1), _column("solo:quiet", ("quiet",)),
               _column("stack", ("nudger", "quiet"), nudges=1))
    assert m.cell("baseline", "TC06").primary is Outcome.PASSED_THROUGH
    cell = m.cell("solo:nudger", "TC06")
    assert cell.primary is Outcome.TURN_EXTENDED and cell.hop == "pre_verify"
    assert m.cell("solo:quiet", "TC06").primary is Outcome.PASSED_THROUGH
    assert [(a.kind, a.plugins) for a in m.attributions if a.outcome is Outcome.TURN_EXTENDED] == \
        [("solo-match", ("nudger",))]


def test_a_nudge_is_not_put_on_an_edit_the_gate_did_not_list() -> None:
    m = _build(_column("baseline", ()), _column("solo:n", ("n",), nudges=1, verify_path=f"{SANDBOX}/other.txt"))
    assert m.cell("solo:n", "TC06").primary is Outcome.PASSED_THROUGH


def test_the_gate_path_matches_whether_macos_reports_tmp_or_private_tmp() -> None:
    private = "/private" + SANDBOX
    events = _edit_events() + [{"ev": "done"}, {"ev": "pre_verify", "changed_paths": [private + "/notes/written.txt"]},
                               {"ev": "done"}]
    col = matrix.column_from_events("solo:n", ("n",), PROBE_PLAN[1:], events, SANDBOX, URL, [], None, 1.0)
    assert col.observations["TC06"].turn_extended == 1


@pytest.mark.parametrize("events, extensions", [([], 0), ([{"ev": "done"}, {"ev": "done"}], 0),
                                                ([{"ev": "done"}, {"ev": "pre_verify"}], 0),
                                                ([{"ev": "done"}, {"ev": "pre_verify"}, {"ev": "done"},
                                                  {"ev": "pre_verify"}, {"ev": "done"}], 2)])
def test_turn_extensions_count_extra_finals_only_when_the_gate_ran(events: list[dict[str, Any]],
                                                                   extensions: int) -> None:
    assert probes.turn_extensions(events) == extensions


def test_a_column_with_no_nudge_keeps_its_observations_unchanged() -> None:
    col = _column("solo:a", ("a",))
    assert col.observations["TC06"].turn_extended == 0


@pytest.mark.parametrize("gate, note", [(Outcome.NOT_EXECUTED, probes.NOT_EXECUTED_NOTE),
                                        (Outcome.NOT_APPLICABLE, probes.NOT_OFFERED_NOTE), (Outcome.BLOCKED, "")])
def test_gate_notes(gate: Outcome, note: str) -> None:
    assert probes.gate_note(gate) == note


def test_focus_hides_an_all_not_executed_probe_row_and_says_so() -> None:
    from proving_ground import report

    m = _build(_column("baseline", ()), _column("solo:a", ("a",)))
    line = report.focus_line(m)
    assert "TC21" in line and "PASSED THROUGH or NOT EXECUTED (probe)" in line
    assert report.forbidden_claims(report.render_markdown(m, focus=True)) == []


def test_a_probe_row_with_a_subject_block_is_not_quiet() -> None:
    m = _build(_column("baseline", ()), _column("solo:a", ("a",), {"block": "a: no"}))
    assert not matrix.quiet_row(m, "TC21")


def test_the_nudge_texts_are_the_user_messages_of_the_extra_final_replies() -> None:
    events = [{"ev": "done", "last_user": "run provingground wiring test"}, {"ev": "pre_verify", "attempt": 0},
              {"ev": "done", "last_user": "fx_nudger: re-read"}, {"ev": "pre_verify", "attempt": 1}]
    assert probes.nudges(events) == ("fx_nudger: re-read",)
    assert probes.nudges(events[:2]) == ()
