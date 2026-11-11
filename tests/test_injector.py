# File: tests/test_injector.py
# The injector never raises, never calls next_call, and ends with DRILL_DONE or DRILL_ABORTED.
from __future__ import annotations

import json
from pathlib import Path

from proving_ground import injector
from proving_ground.observer import EventLog


def setup_state(tmp_path: Path, calls: list[dict]) -> tuple[Path, EventLog]:
    (tmp_path / "plan.json").write_text(json.dumps({"sandbox": str(tmp_path), "calls": calls}), encoding="utf-8")
    return tmp_path, EventLog(tmp_path / "events.jsonl")


def never(_payload):
    raise AssertionError("next_call must never be invoked")


def test_scripted_rounds_then_done(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(injector, "_completion", lambda body: body)
    state, log = setup_state(tmp_path, [{"id": "TC01", "call_ref": "pgcall_TC01", "tool": "terminal",
                                         "args": {"command": "echo hi"}}])
    middleware = injector.make_llm_execution(state, log)
    first = middleware(request={"messages": [{"role": "user", "content": "x"}]}, next_call=never)
    assert first["choices"][0]["message"]["tool_calls"][0]["id"] == "pgcall_TC01"
    done = middleware(request={"messages": [{"role": "assistant", "tool_calls": [{}]}]}, next_call=never)
    assert done["choices"][0]["message"]["content"] == injector.DONE_SENTINEL


def test_own_error_returns_abort_not_exception(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(injector, "_completion", lambda body: body)
    middleware = injector.make_llm_execution(tmp_path, EventLog(tmp_path / "events.jsonl"))
    reply = middleware(request={"messages": []}, next_call=never)
    assert reply["choices"][0]["message"]["content"].startswith(injector.ABORT_SENTINEL)
    assert (tmp_path / "ABORT").read_text(encoding="utf-8").startswith("INJECTOR_ERROR")


def test_non_chat_request_is_refused(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(injector, "_completion", lambda body: body)
    state, log = setup_state(tmp_path, [])
    reply = injector.make_llm_execution(state, log)(request={"input": "responses-api"}, next_call=never)
    assert "REFUSED_API_MODE" in reply["choices"][0]["message"]["content"]


def test_first_step_confirms_loaded_plugins_and_last_logs_done(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(injector, "_completion", lambda body: body)
    (tmp_path / "plan.json").write_text(json.dumps({"sandbox": str(tmp_path), "subjects": ["fx_guard", "fx_crash"],
                                                    "calls": []}), encoding="utf-8")
    log = EventLog(tmp_path / "events.jsonl")
    middleware = injector.make_llm_execution(tmp_path, log, lambda key: key == "fx_guard")
    middleware(request={"messages": [{"role": "user", "content": "x"}]}, next_call=never)
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    loaded = next(e for e in events if e["ev"] == "plugins_loaded")
    assert loaded["loaded"] == ["fx_guard"] and loaded["missing"] == ["fx_crash"]
    assert events[-1]["ev"] == "done"
    assert all(e.get("provider_called") is False for e in events if e["ev"] == "llm_execution")
