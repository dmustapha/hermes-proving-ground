# Events attach to drill calls by tool_call_id only. An event whose id matches no
# drill call is an explicit harness error for its column, never matched to a call by order.
from __future__ import annotations

from helpers import IDS, SANDBOX, URL, call_events, plan

from proving_ground import cli, matrix
from proving_ground.matrix import Outcome


def _events(effects: dict, rename: dict[str, str]) -> list[dict]:
    events = [{"ev": "activated", "calls": len(IDS)}, {"ev": "plugins_loaded", "loaded": []}]
    for cid in IDS:
        for event in call_events(cid, {"core_truncate": cid == "TC05", **effects.get(cid, {})}):
            if cid in rename:
                event = {**event, "tool_call_id": rename[cid]}
            events.append(event)
    return events + [{"ev": "done"}]


def _column(rename: dict[str, str], effects: dict | None = None) -> matrix.ColumnRun:
    return matrix.column_from_events("stack", (), plan(), _events(effects or {}, rename), SANDBOX, URL, [], None, 1.0)


def test_matching_ids_attach_every_call() -> None:
    col = _column({})
    assert col.abort_reason is None
    assert all(obs.ran for obs in col.observations.values())


def test_unknown_id_is_a_harness_error_not_an_order_match() -> None:
    blocked = {"TC02": {"block": "fx_guard: marker"}}
    col = _column({"TC02": "call_from_elsewhere"}, blocked)
    assert col.abort_reason is not None and col.abort_reason.startswith("TOOL_CALL_ID_MISMATCH")
    assert not col.observations["TC02"].ran  # the BLOCKED event is not pinned on TC02 (or any call) by order
    assert col.observations["TC03"].status == "ok" and col.observations["TC03"].ran


def test_missing_id_is_a_harness_error() -> None:
    col = _column({"TC01": None})
    assert col.abort_reason is not None and col.abort_reason.startswith("TOOL_CALL_ID_MISMATCH")
    assert not col.observations["TC01"].ran


def test_mismatch_column_is_reported_aborted_with_exit_3() -> None:
    infos = [matrix.CallInfo(i, "terminal", i, i) for i in IDS]
    base = matrix.column_from_events("baseline", (), plan(), _events({}, {}), SANDBOX, URL, [], None, 1.0)
    m = matrix.build_matrix([base, _column({"TC04": "x"})], None, {}, infos, "v", "t", 1.0)
    assert "stack" in m.aborted and "TOOL_CALL_ID_MISMATCH" in m.aborted["stack"]
    assert m.cell("stack", "TC04").primary is Outcome.NOT_RUN
    assert cli.exit_code_for(m) == 3


def test_an_escape_with_an_unknown_id_is_still_an_escape_finding() -> None:
    events = _events({}, {}) + [{"ev": "escaped", "tool_call_id": "call_from_elsewhere", "reasons": ["path /etc"]}]
    col = matrix.column_from_events("solo:x", ("x",), plan(), events, SANDBOX, URL, [], "ESCAPED", 1.0)
    infos = [matrix.CallInfo(i, "terminal", i, i) for i in IDS]
    base = matrix.column_from_events("baseline", (), plan(), _events({}, {}), SANDBOX, URL, [], None, 1.0)
    m = matrix.build_matrix([base, col], None, {}, infos, "v", "t", 1.0)
    escapes = [f for f in m.findings if f.kind == "ESCAPED"]
    assert len(escapes) == 1 and "path /etc" in escapes[0].text
    assert cli.exit_code_for(m) == 4
