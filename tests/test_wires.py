# The context and reply wire (--all-wires): two extra rows judged like a call, against the zero-plugin baseline,
# with core effects labelled (core), unstable baselines never blamed, and a default drill left exactly as it was.
from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Sequence

import pytest

from helpers import SANDBOX, URL, column, fixture_like_matrix
from proving_ground import injector, matrix, observer, report
from proving_ground.matrix import CONTEXT_ID, REPLY_ID, Outcome

ROOT = Path(__file__).resolve().parent.parent
PROMPT, DONE = "run provingground wiring test", injector.DONE_SENTINEL
WIRES = {"prompt": PROMPT, "reply": DONE}
SYSTEM = f"You are Hermes.\nCurrent working directory: {SANDBOX}\nConversation started: Wednesday, November 11, 2026"


def wire_events(user: str = PROMPT, system: str = SYSTEM, tools: Sequence[str] = ("terminal",),
                raw: str | None = DONE, delivered: str | None = DONE, steps: int = 2) -> list[dict[str, Any]]:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    events: list[dict[str, Any]] = [{"ev": "llm_context", "step": i, "messages": messages, "tools": list(tools)}
                                    for i in range(steps)]
    if raw is not None:
        events.append({"ev": "reply_raw", "text": raw})
    if delivered is not None:
        events.append({"ev": "reply_delivered", "text": delivered})
    return events


def wire_column(name: str, plugins: tuple[str, ...] = (), sandbox: str = SANDBOX, **kw: Any) -> matrix.ColumnRun:
    events = [{"ev": "activated", "calls": 0}, {"ev": "plugins_loaded", "loaded": list(plugins)},
              *wire_events(**kw), {"ev": "done"}]
    return matrix.column_from_events(name, plugins, [], events, sandbox, URL, [], None, 1.0, WIRES)


def build(columns: list[matrix.ColumnRun], repeat: matrix.ColumnRun | None = None) -> matrix.Matrix:
    return matrix.build_matrix(columns, repeat, {}, list(matrix.WIRE_CALLS), "v", "2026-11-11T00:00:00+00:00", 1.0,
                               load_order=[c.plugins[0] for c in columns if c.name.startswith("solo:")])


def adder_stack() -> matrix.Matrix:
    added = PROMPT + "\n\nfx_context: house notes"
    return build([wire_column("baseline"), wire_column("solo:fx_context", ("fx_context",), user=added),
                  wire_column("solo:fx_replier", ("fx_replier",), delivered=DONE + " [signed]"),
                  wire_column("stack", ("fx_context", "fx_replier"), user=added, delivered=DONE + " [signed]")])


def test_added_context_is_seen_solo_and_in_the_stack_and_attributed() -> None:
    m = adder_stack()
    assert m.cell("solo:fx_context", CONTEXT_ID).primary is Outcome.CONTEXT_ADDED
    assert m.cell("stack", CONTEXT_ID).primary is Outcome.CONTEXT_ADDED
    assert m.cell("stack", CONTEXT_ID).hop == "user turn"
    assert m.cell("solo:fx_replier", CONTEXT_ID).primary is Outcome.PASSED_THROUGH
    found = {(a.call_id, a.outcome, a.kind, a.plugins) for a in m.attributions}
    assert (CONTEXT_ID, Outcome.CONTEXT_ADDED, "solo-match", ("fx_context",)) in found


def test_rewritten_reply_is_reply_changed_and_attributed() -> None:
    m = adder_stack()
    assert m.cell("solo:fx_replier", REPLY_ID).primary is Outcome.REPLY_CHANGED
    assert m.cell("stack", REPLY_ID).hop == "transform_llm_output"
    assert m.cell("solo:fx_context", REPLY_ID).primary is Outcome.PASSED_THROUGH
    found = {(a.call_id, a.outcome, a.plugins) for a in m.attributions}
    assert (REPLY_ID, Outcome.REPLY_CHANGED, ("fx_replier",)) in found


def test_per_column_paths_and_the_date_line_are_not_context_changes() -> None:
    other = "/tmp/provingground-unittest9/sandbox"
    later = f"You are Hermes.\nCurrent working directory: {other}\nConversation started: Thursday, November 12, 2026"
    m = build([wire_column("baseline"), wire_column("solo:fx_a", ("fx_a",), sandbox=other, system=later)])
    assert m.cell("solo:fx_a", CONTEXT_ID).primary is Outcome.PASSED_THROUGH


def test_changed_tools_offered_name_their_hop() -> None:
    m = build([wire_column("baseline"), wire_column("solo:fx_tool", ("fx_tool",), tools=("terminal", "fx_lookup"))])
    cell = m.cell("solo:fx_tool", CONTEXT_ID)
    assert cell.primary is Outcome.CONTEXT_ADDED and cell.hop == "tools offered"


def test_context_hermes_adds_itself_is_core_and_names_no_plugin() -> None:
    prefetched = PROMPT + "\n\n<memory-context>notes</memory-context>"
    m = build([wire_column("baseline", user=prefetched), wire_column("solo:fx_a", ("fx_a",), user=prefetched)])
    assert m.cell("baseline", CONTEXT_ID).core and m.cell("baseline", CONTEXT_ID).primary is Outcome.CONTEXT_ADDED
    assert m.cell("solo:fx_a", CONTEXT_ID).core
    assert all(a.kind == "core" and a.plugins == () for a in m.attributions if a.call_id == CONTEXT_ID)
    assert (CONTEXT_ID, Outcome.CONTEXT_ADDED) not in matrix.distinct_effects(m)


def test_a_plugin_adding_on_top_of_core_context_is_still_its_effect() -> None:
    prefetched = PROMPT + "\n\n<memory-context>notes</memory-context>"
    m = build([wire_column("baseline", user=prefetched),
               wire_column("solo:fx_a", ("fx_a",), user=prefetched + "\n\nfx_a: more")])
    cell = m.cell("solo:fx_a", CONTEXT_ID)
    assert cell.primary is Outcome.CONTEXT_ADDED and not cell.core and cell.core_also


def test_unstable_baseline_context_is_never_blamed_on_a_plugin() -> None:
    m = build([wire_column("baseline"), wire_column("solo:fx_a", ("fx_a",), system=SYSTEM + "\nrandom: 2")],
              repeat=wire_column("baseline-repeat", system=SYSTEM + "\nrandom: 1"))
    cell = m.cell("solo:fx_a", CONTEXT_ID)
    assert CONTEXT_ID in m.unstable and cell.primary is Outcome.PASSED_THROUGH and cell.unstable


def test_a_column_that_recorded_no_wire_events_is_not_run_never_passed_through() -> None:
    empty = matrix.column_from_events("solo:fx_a", ("fx_a",), [], [{"ev": "activated"},
                                      {"ev": "plugins_loaded", "loaded": ["fx_a"]}], SANDBOX, URL, [], None, 1.0, WIRES)
    m = build([wire_column("baseline"), empty])
    assert {m.cell("solo:fx_a", cid).primary for cid in (CONTEXT_ID, REPLY_ID)} == {Outcome.NOT_RUN}


def test_an_aborted_column_reply_is_not_a_reply_change() -> None:
    aborted = f"{injector.ABORT_SENTINEL} ESCAPED"
    m = build([wire_column("baseline"), wire_column("solo:fx_a", ("fx_a",), raw=aborted, delivered=aborted)])
    assert m.cell("solo:fx_a", REPLY_ID).primary is Outcome.PASSED_THROUGH


def test_reply_without_the_transform_tap_is_judged_unseen_not_changed() -> None:
    m = build([wire_column("baseline", raw=None), wire_column("solo:fx_a", ("fx_a",), raw=None, delivered="other")])
    assert m.cell("solo:fx_a", REPLY_ID).primary is Outcome.PASSED_THROUGH  # no raw reply: no change is claimed


def test_wire_rows_are_not_counted_as_test_calls() -> None:
    m = adder_stack()
    assert m.conditions.n_calls == 0 and matrix.count_test_calls(m.calls) == 0
    assert "0 test calls" in matrix.tally(m)
    assert report.OBSERVED.format(n=0) in report.render_terminal(m, color=False)


def test_default_drill_has_no_wire_rows_and_no_options_key() -> None:
    m = fixture_like_matrix()
    assert not any(c.tool == matrix.WIRE_TOOL for c in m.calls)
    assert all(cid not in (CONTEXT_ID, REPLY_ID) for cid in m.observations["stack"])
    data = report.matrix_to_dict(m)
    assert "options" not in data
    assert "options" not in report.matrix_to_dict(report.matrix_from_dict(data))


def test_options_round_trip_and_show_in_the_conditions() -> None:
    m = matrix.build_matrix([column("baseline", (), {})], None, {}, [matrix.CallInfo("TC01", "terminal", "s", "p")],
                            "v", "t", 1.0, options={"wires": "context and reply recorded"})
    again = report.matrix_from_dict(json.loads(json.dumps(report.matrix_to_dict(m))))
    assert again.options == {"wires": "context and reply recorded"}
    assert "wires: context and reply recorded" in report.conditions_lines(again)


@pytest.mark.parametrize("call_id", [CONTEXT_ID, REPLY_ID])
def test_wire_cell_detail_renders_in_every_format_without_grading_words(call_id: str) -> None:
    m = adder_stack()
    detail = report.render_cell_detail(m, "stack", call_id)
    assert ("fx_context: house notes" in detail) if call_id == CONTEXT_ID else ("[signed]" in detail)
    for text in (detail, report.render_markdown(m), report.render_html(m), report.render_terminal(m, color=False)):
        assert report.forbidden_claims(text) == []
    assert 'class="CONTEXT_ADDED"' in report.render_html(m) and ".CONTEXT_ADDED" in report.CSS


def test_wire_taps_record_and_never_add_or_rewrite(tmp_path: Path) -> None:
    log = observer.EventLog(tmp_path / "events.jsonl")
    hooks = observer.make_wire_hooks(log)
    assert set(hooks) == set(observer.WIRE_HOOK_NAMES)
    assert hooks["pre_llm_call"](user_message="hi", is_first_turn=True, conversation_history=[]) is None
    assert hooks["transform_llm_output"](response_text=DONE, session_id="s") is None
    assert hooks["post_llm_call"](assistant_response="x" * (observer.WIRE_TEXT_CAP + 5)) is None
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert [e["ev"] for e in events] == ["pre_llm_call", "reply_raw", "reply_delivered"]
    assert events[2]["text"].endswith("…[5 more chars]") and len(events[2]["text"]) < observer.WIRE_TEXT_CAP + 40


def _plan(tmp_path: Path, wires: bool) -> None:
    plan = {"sandbox": str(tmp_path), "calls": [], **({"wires": WIRES} if wires else {})}
    (tmp_path / "plan.json").write_text(json.dumps(plan), encoding="utf-8")


@pytest.mark.parametrize("wires", [True, False])
def test_injector_records_the_model_view_only_with_wires(tmp_path: Path, monkeypatch, wires: bool) -> None:
    monkeypatch.setattr(injector, "_completion", lambda body: body)
    _plan(tmp_path, wires)
    middleware = injector.make_llm_execution(tmp_path, observer.EventLog(tmp_path / "events.jsonl"))
    request = {"messages": [{"role": "system", "content": "sys"},
                            {"role": "user", "content": [{"type": "text", "text": PROMPT}, {"type": "image_url"}]},
                            {"role": "assistant", "content": None, "tool_calls": [{"id": "x"}]},
                            {"role": "tool", "content": "tool output"}],
               "tools": [{"function": {"name": "terminal"}}, {"function": {"name": "patch"}}, "junk"]}
    middleware(request=request, next_call=None)
    events = [json.loads(x) for x in (tmp_path / "events.jsonl").read_text().splitlines()]
    views = [e for e in events if e["ev"] == "llm_context"]
    if not wires:
        assert views == []
        return
    assert views[0]["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": PROMPT}]
    assert views[0]["tools"] == ["patch", "terminal"]


def test_a_malformed_tools_list_does_not_stop_the_turn(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(injector, "_completion", lambda body: body)
    _plan(tmp_path, True)
    middleware = injector.make_llm_execution(tmp_path, observer.EventLog(tmp_path / "events.jsonl"))
    reply = middleware(request={"messages": [{"role": "user", "content": PROMPT}], "tools": {"not": "a list"}},
                       next_call=None)
    assert reply["choices"][0]["message"]["content"] == DONE
    assert not (tmp_path / "ABORT").exists()


def _child(plugin_root_module, fake_ctx, monkeypatch, tmp_path: Path, wires: bool) -> list[str]:
    from proving_ground import safety

    state = tmp_path / safety.STATE_DIRNAME
    state.mkdir()
    _plan(state, wires)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    observer_module = importlib.import_module(f"{plugin_root_module.__name__}.proving_ground.observer")
    monkeypatch.setattr(observer_module, "install_socket_audit", lambda _log: None)
    plugin_root_module._register_drill_child(fake_ctx)
    return fake_ctx.hooks


@pytest.mark.parametrize("wires", [True, False])
def test_a_child_registers_wire_taps_only_when_its_plan_asks(plugin_root_module, fake_ctx, monkeypatch, tmp_path,
                                                             wires: bool) -> None:
    pytest.importorskip("openai")  # the child's injector builds its replies with Hermes's openai
    hooks = _child(plugin_root_module, fake_ctx, monkeypatch, tmp_path, wires)
    always = plugin_root_module.CALL_HOOK_NAMES + plugin_root_module.STEP_HOOK_NAMES
    expected = plugin_root_module.HOOK_NAMES if wires else always
    assert sorted(hooks) == sorted(expected)
    assert fake_ctx.middleware == ["llm_execution", "tool_execution"]


def test_manifest_declares_exactly_the_hooks_the_plugin_registers(plugin_root_module) -> None:
    from ruamel.yaml import YAML

    manifest = YAML(typ="safe", pure=True).load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))
    assert manifest["provides_hooks"] == list(plugin_root_module.HOOK_NAMES)
    assert plugin_root_module.WIRE_HOOK_NAMES == observer.WIRE_HOOK_NAMES
