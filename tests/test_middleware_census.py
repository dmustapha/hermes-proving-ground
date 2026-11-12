# File: tests/test_middleware_census.py
# PROVING GROUND as the innermost middleware, the subject middleware outcomes, and the registration census with
# liveness. Pure: synthetic events in the format the child writes, and fake managers shaped like Hermes's.
from __future__ import annotations

import json
import types
from pathlib import Path
from typing import Any

import pytest
from helpers import IDS, SANDBOX, URL, call_events, plan

from proving_ground import injector, matrix, observer, report, runner
from proving_ground.matrix import Outcome

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "tests" / "data"


# --- the child-internal key --------------------------------------------------------------------------------------


def test_child_key_sorts_after_every_subject_including_test_plugins_after_the_catalog_name() -> None:
    keys = ["fx_guard", "tx_reply_stamper", "tx_request_tagger", "tx_exec_replacer"]
    assert all(runner.child_pg_key(keys) > k for k in keys)
    # the old child key sorted before these, so their llm_execution middleware sat outside PG's view and never ran
    assert runner.PG_PLUGIN_KEY < "tx_reply_stamper"


def test_child_key_grows_past_a_subject_that_already_starts_with_z() -> None:
    key = runner.child_pg_key(["zz-proving-ground", "zzz", "zzzz-tool"])
    assert key > "zzzz-tool" and key.endswith("-proving-ground")
    assert runner.child_pg_key([]) == runner.CHILD_PG_KEY


def test_rename_manifest_rewrites_exactly_the_top_level_name(tmp_path: Path) -> None:
    manifest = tmp_path / "plugin.yaml"
    manifest.write_text("# comment name: x\nname: proving-ground\nversion: \"0.1.0\"\n", encoding="utf-8")
    runner.rename_manifest(manifest, "zz-proving-ground")
    assert manifest.read_text(encoding="utf-8").splitlines()[1] == "name: zz-proving-ground"


@pytest.mark.parametrize("text", ["version: 1\n", "name: a\nname: b\n"])
def test_rename_manifest_refuses_a_manifest_without_exactly_one_name(tmp_path: Path, text: str) -> None:
    manifest = tmp_path / "plugin.yaml"
    manifest.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        runner.rename_manifest(manifest, "zz-proving-ground")


def test_child_home_enables_the_renamed_copy_last(tmp_path: Path) -> None:
    settings = runner.RunnerSettings(Path("/nonexistent/hermes"), ROOT, tmp_path / "cache" / "installs", tmp_path)
    (tmp_path / "root" / "sandbox").mkdir(parents=True)
    spec = runner.ColumnSpec("solo:tx_reply_stamper", (runner.PluginSource("tx_reply_stamper", DATA / "tx_reply_stamper"),))
    home = runner.build_child_home(tmp_path / "root", spec, settings, [], "0" * 64)
    key = runner.child_pg_key(["tx_reply_stamper"])
    lines = (home / "plugins" / key / "plugin.yaml").read_text(encoding="utf-8").splitlines()
    assert f"name: {key}" in lines and "name: proving-ground" not in lines
    assert "name: proving-ground" in (ROOT / "plugin.yaml").read_text(encoding="utf-8").splitlines()  # source kept
    enabled = [line.strip() for line in (home / "config.yaml").read_text(encoding="utf-8").splitlines()
               if line.startswith("    - ")]
    assert enabled == ['- "tx_reply_stamper"', f'- "{key}"']
    assert json.loads((home / ".provingground" / "plan.json").read_text(encoding="utf-8"))["pg_key"] == key


# --- the injector ------------------------------------------------------------------------------------------------


def _state(tmp_path: Path, calls: list[dict[str, Any]]) -> observer.EventLog:
    (tmp_path / "plan.json").write_text(json.dumps({"sandbox": str(tmp_path), "calls": calls}), encoding="utf-8")
    return observer.EventLog(tmp_path / "events.jsonl")


def _events(tmp_path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]


TWO = [{"id": i, "call_ref": f"pgcall_{i}", "tool": "terminal", "args": {"command": f"echo {i}"}} for i in ("TC01", "TC02")]


def test_steps_are_counted_from_the_original_request_when_middleware_trimmed_history(tmp_path: Path,
                                                                                     monkeypatch) -> None:
    monkeypatch.setattr(injector, "_completion", lambda body: body)
    log = _state(tmp_path, TWO)
    original = {"messages": [{"role": "user", "content": "go"}, {"role": "assistant", "tool_calls": [{}]},
                             {"role": "tool", "content": "ok"}]}
    trimmed = {"messages": [{"role": "user", "content": "go"}]}  # an llm_request trimmer dropped the earlier step
    reply = injector.make_llm_execution(tmp_path, log)(request=trimmed, original_request=original, next_call=None)
    assert reply["choices"][0]["message"]["tool_calls"][0]["id"] == "pgcall_TC02"  # not TC01 again
    step = next(e for e in _events(tmp_path) if e["ev"] == "llm_execution")
    assert step["call_id"] == "TC02" and step["request_rewrite"] == ["messages"]


def test_request_rewrite_names_changed_fields_and_trace_labels_but_not_object_copies() -> None:
    class Opaque:
        pass

    original = {"messages": [{"role": "system", "content": "s"}], "timeout": Opaque(), "model": "m"}
    copy = {**original, "timeout": Opaque()}  # a deep copy makes a new object; that is not a rewrite
    assert injector.request_rewrite(copy, original, []) == []
    rewritten = {**original, "messages": [{"role": "system", "content": "s [tag]"}], "tools": []}
    trace = [{"source": "plugin", "name": "tx_request_tagger", "reason": "tag"}]
    assert injector.request_rewrite(rewritten, original, trace) == ["messages", "tools",
                                                                    "llm_request: tx_request_tagger / plugin / tag"]
    assert injector.request_rewrite(rewritten, None, None) == []  # no original to compare: nothing claimed


def test_the_scripted_reply_of_each_step_is_left_for_the_reply_tap(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(injector, "_completion", lambda body: body)
    log, ledger = _state(tmp_path, TWO), observer.ReplyLedger()
    injector.make_llm_execution(tmp_path, log, ledger=ledger)(request={"messages": []}, next_call=None)
    call_id, shape = ledger.take()
    assert call_id == "TC01" and shape["tool_calls"] == [["pgcall_TC01", "terminal", {"command": "echo TC01"}]]
    assert ledger.take() is None


# --- the observer taps ---------------------------------------------------------------------------------------------


def _assistant(content: Any, calls: list[tuple[str, str, str]]) -> Any:
    tool_calls = [types.SimpleNamespace(id=i, function=types.SimpleNamespace(name=n, arguments=a)) for i, n, a in calls]
    return types.SimpleNamespace(content=content, tool_calls=tool_calls or None)


@pytest.mark.parametrize(("message", "changes"), [
    (_assistant(None, [("pgcall_TC01", "terminal", '{"command": "echo TC01"}')]), []),
    (_assistant("stamped", [("pgcall_TC01", "terminal", '{"command": "echo TC01"}')]), ["reply text"]),
    (_assistant(None, [("pgcall_TC01", "terminal", '{"command": "echo other"}')]), ["tool call arguments"]),
    (_assistant(None, []), ["tool calls"]),
])
def test_reply_tap_reports_what_a_middleware_changed_in_the_scripted_reply(tmp_path: Path, message: Any,
                                                                           changes: list[str]) -> None:
    log, ledger = observer.EventLog(tmp_path / "events.jsonl"), observer.ReplyLedger()
    ledger.expect("TC01", injector.tool_call_body(0, TWO[0]))
    observer.make_reply_tap(log, ledger)(assistant_message=message)
    event = _events(tmp_path)[-1]
    assert event["ev"] == "model_reply" and event["call_id"] == "TC01" and event["changes"] == changes


def test_reply_tap_names_a_step_proving_ground_never_answered(tmp_path: Path) -> None:
    log = observer.EventLog(tmp_path / "events.jsonl")
    observer.make_reply_tap(log, observer.ReplyLedger())(assistant_message=_assistant("cached", []))
    assert _events(tmp_path)[-1]["changes"] == ["step answered without PROVING GROUND"]


def test_tool_execution_tap_records_then_hands_the_call_on_once(tmp_path: Path) -> None:
    log, calls = observer.EventLog(tmp_path / "events.jsonl"), []
    tap = observer.make_tool_execution_tap(log)
    assert tap(tool_name="terminal", args={"command": "x"}, next_call=lambda: calls.append(1) or "result",
               tool_call_id="pgcall_TC01") == "result"
    assert calls == [1] and _events(tmp_path)[-1]["tool_call_id"] == "pgcall_TC01"


def test_tool_execution_tap_lets_a_downstream_error_propagate(tmp_path: Path) -> None:
    def broken() -> Any:
        raise RuntimeError("downstream")

    with pytest.raises(RuntimeError, match="downstream"):
        observer.make_tool_execution_tap(observer.EventLog(tmp_path / "e.jsonl"))(next_call=broken)


def test_post_tool_call_keeps_the_middleware_trace_labels(tmp_path: Path) -> None:
    log = observer.EventLog(tmp_path / "events.jsonl")
    hooks = observer.make_hooks(tmp_path, tmp_path, log)
    hooks["post_tool_call"](tool_name="terminal", args={"command": "echo"}, tool_call_id="pgcall_TC01",
                            middleware_trace=[{"source": "plugin", "name": "relabel"}, "junk"])
    assert _events(tmp_path)[-1]["middleware_trace"] == ["relabel / plugin"]


# --- classification and liveness -----------------------------------------------------------------------------------


def _column(name: str, plugins: tuple[str, ...], extra: list[dict[str, Any]], drop: dict[str, set[str]] | None = None,
            census_entry: dict[str, Any] | None = None) -> matrix.ColumnRun:
    loaded = {"ev": "plugins_loaded", "expected": list(plugins), "loaded": list(plugins)}
    if census_entry is not None:
        loaded["census"] = {k: census_entry for k in plugins}
    events = [{"ev": "activated"}, loaded]
    for cid in IDS:
        events += [e for e in call_events(cid, {"core_truncate": cid == "TC05"})
                   if e["ev"] not in (drop or {}).get(cid, set())]
    return matrix.column_from_events(name, plugins, plan(), events + extra, SANDBOX, URL, [], None, 1.0)


WATCHED = {"registered": {"hooks": ["pre_tool_call"], "middleware": [], "tools": [], "commands": []},
           "watched": True, "late": False}


def _build(*columns: matrix.ColumnRun) -> matrix.Matrix:
    infos = [matrix.CallInfo(i, "terminal", f"subject {i}", "") for i in IDS]
    return matrix.build_matrix([_column("baseline", (), []), *columns], None, {}, infos, "t", "s", 1.0)


def test_a_tool_execution_short_circuit_is_execution_replaced_and_attributed() -> None:
    replaced = _column("solo:fx_exec", ("fx_exec",), [], drop={"TC01": {"pre_tool_call", "terminal_raw", "tool_raw"}})
    m = _build(replaced)
    assert m.cell("solo:fx_exec", "TC01").outcomes == (Outcome.EXECUTION_REPLACED,)
    assert m.cell("solo:fx_exec", "TC01").hop == "tool_execution"
    assert m.cell("solo:fx_exec", "TC02").primary is Outcome.PASSED_THROUGH


def test_request_and_reply_middleware_effects_land_on_the_call_their_step_produced() -> None:
    extra = [{"ev": "llm_execution", "call_id": "TC02", "request_rewrite": ["messages"]},
             {"ev": "model_reply", "call_id": "TC03", "changes": ["reply text"]},
             {"ev": "model_reply", "call_id": "TC01", "changes": []}]
    m = _build(_column("solo:fx_mw", ("fx_mw",), extra))
    assert m.cell("solo:fx_mw", "TC02").outcomes == (Outcome.REQUEST_REWRITTEN,)
    assert m.cell("solo:fx_mw", "TC03").outcomes == (Outcome.MODEL_REPLY_CHANGED,)
    assert m.cell("solo:fx_mw", "TC01").primary is Outcome.PASSED_THROUGH


def test_a_middleware_effect_on_a_core_cell_never_blames_the_plugin_for_core_truncation() -> None:
    stamped = [{"ev": "model_reply", "call_id": "TC05", "changes": ["reply text"]}]
    solo, stack = _column("solo:fx_mw", ("fx_mw",), stamped), _column("stack", ("fx_mw", "fx_b"), stamped)
    m = _build(solo, _column("solo:fx_b", ("fx_b",), []), stack)
    cell = m.cell("solo:fx_mw", "TC05")
    assert cell.core and cell.added == (Outcome.MODEL_REPLY_CHANGED,)
    assert report.cell_label(cell) == "MODEL REPLY CHANGED + RESULT CHANGED (core + plugin)"
    kinds = {(a.outcome, a.kind, a.plugins) for a in m.attributions if a.call_id == "TC05"}
    assert kinds == {(Outcome.RESULT_CHANGED, "core", ()), (Outcome.MODEL_REPLY_CHANGED, "solo-match", ("fx_mw",))}
    assert (("TC05", Outcome.RESULT_CHANGED) not in matrix.distinct_effects(m)
            and ("TC05", Outcome.MODEL_REPLY_CHANGED) in matrix.distinct_effects(m))


def test_passed_through_cells_say_whether_the_plugin_ran_on_that_call() -> None:
    fired = [{"ev": "fired", "plugin": "fx_one", "kind": "hook", "name": "pre_tool_call", "tool_call_id": "pgcall_TC01"}]
    m = _build(_column("solo:fx_one", ("fx_one",), fired, census_entry=WATCHED))
    assert m.cell("solo:fx_one", "TC01").liveness == "ran, no change"
    assert m.cell("solo:fx_one", "TC02").liveness == "not invoked"
    assert report.cell_label(m.cell("solo:fx_one", "TC02")) == "PASSED THROUGH (not invoked)"
    assert m.cell("baseline", "TC01").liveness == ""


def test_liveness_is_not_claimed_when_the_census_is_unknown_or_unwatched() -> None:
    unwatched = {**WATCHED, "watched": False}
    m = _build(_column("solo:fx_one", ("fx_one",), []), _column("solo:fx_two", ("fx_two",), [], census_entry=unwatched))
    assert m.cell("solo:fx_one", "TC01").liveness == "" and m.cell("solo:fx_two", "TC01").liveness == ""
    assert report.cell_label(m.cell("solo:fx_one", "TC01")) == "PASSED THROUGH"


def test_a_new_drill_reports_liveness_as_unknown_instead_of_guessing() -> None:
    m = _build(_column("solo:fx_one", ("fx_one",), []))
    assert m.census == {} and m.cell("solo:fx_one", "TC01").liveness == ""
    for text in (report.render_terminal(m, color=False), report.render_markdown(m), report.render_html(m)):
        assert "per-plugin liveness: unknown" in text and "private hook tables" in " ".join(text.split())


# --- the report -----------------------------------------------------------------------------------------------------


def test_census_line_lists_registered_fired_untested_and_unexercised() -> None:
    c = matrix.PluginCensus({"hooks": ("pre_tool_call", "pre_verify"), "middleware": ("llm_execution",),
                             "tools": ("fx_tool",), "commands": ("fxcmd",)}, ("hook pre_tool_call",), True, False, True)
    assert report.census_line("fx_one", c) == (
        "fx_one: hooks registered: pre_tool_call, pre_verify, llm_execution (middleware); fired: pre_tool_call; "
        "never fired in this drill (UNTESTED): pre_verify, llm_execution (middleware); registered tools: fx_tool "
        "(not exercised); registered commands: fxcmd (not exercised); loaded after PROVING GROUND (requires_plugins), "
        "so its llm_execution middleware is inside PROVING GROUND's scripted frame and is not reached")
    assert report.census_line("fx_x", matrix.PluginCensus(None)) == \
        "fx_x: registrations unknown (the child could not read them)"
    assert "not observed" in report.census_line("fx_y", matrix.PluginCensus({"hooks": ("a",)}, (), False))


def test_census_and_liveness_round_trip_through_json_and_stay_neutral() -> None:
    fired = [{"ev": "fired", "plugin": "fx_one", "kind": "hook", "name": "pre_tool_call", "tool_call_id": "pgcall_TC01"},
             {"ev": "model_reply", "call_id": "TC05", "changes": ["reply text"]}]
    m = _build(_column("solo:fx_one", ("fx_one",), fired, census_entry=WATCHED))
    data = report.matrix_to_dict(m)
    again = report.matrix_from_dict(json.loads(json.dumps(data)))
    assert report.matrix_to_dict(again) == data and again.census == m.census
    assert again.cell("solo:fx_one", "TC05").added == (Outcome.MODEL_REPLY_CHANGED,)
    for text in (report.render_terminal(m, color=False), report.render_markdown(m), report.render_html(m)):
        assert "coverage of fx_one: hooks registered: pre_tool_call; fired: pre_tool_call" in text
        assert report.forbidden_claims(text) == []


# --- declared capabilities -----------------------------------------------------------------------------------------


def test_nothing_is_declared_that_a_drill_child_cannot_fire(plugin_root_module) -> None:
    from ruamel.yaml import YAML

    load = YAML(typ="safe", pure=True).load
    manifest = load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))
    entry = load((ROOT / "catalog" / "proving-ground.yaml").read_text(encoding="utf-8"))["capabilities"]
    assert not {"pre_approval_request", "post_approval_response"} & set(manifest["provides_hooks"])
    assert "post_api_request" in manifest["provides_hooks"]
    assert entry["provides_hooks"] == manifest["provides_hooks"] == list(plugin_root_module.HOOK_NAMES)
    assert manifest["provides_middleware"] == entry["provides_middleware"] == ["llm_execution", "tool_execution"]
