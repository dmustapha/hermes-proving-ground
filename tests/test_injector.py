# File: tests/test_injector.py
# The injector never raises, never calls next_call, and ends with DRILL_DONE or DRILL_ABORTED.
from __future__ import annotations

import json
from pathlib import Path

import pytest

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


def test_error_path_survives_a_failing_reply_builder(tmp_path: Path, monkeypatch) -> None:
    """Even when building a reply fails on every step, the prebuilt last-resort reply comes back; nothing raises."""
    built = {"n": 0}

    def builder(body):
        built["n"] += 1
        if built["n"] > 1:
            raise ValueError("model_validate rejected the body")
        return body

    monkeypatch.setattr(injector, "_completion", builder)
    state, log = setup_state(tmp_path, [])
    middleware = injector.make_llm_execution(state, log)
    reply = middleware(request={"messages": [{"role": "user", "content": "x"}]}, next_call=never)
    assert reply["choices"][0]["message"]["content"] == f"{injector.ABORT_SENTINEL} INJECTOR_ERROR"
    assert (tmp_path / "ABORT").read_text(encoding="utf-8") == "INJECTOR_ERROR:ValueError"


def test_last_resort_reply_is_a_real_chat_completion_copied_per_use(tmp_path: Path) -> None:
    from openai.types.chat import ChatCompletion

    state, log = setup_state(tmp_path, [])
    middleware = injector.make_llm_execution(state, log)
    first = middleware(request=None, next_call=never)
    second = middleware(request=None, next_call=never)
    assert isinstance(first, ChatCompletion) and first is not second
    assert (first.choices[0].message.content or "").startswith(injector.ABORT_SENTINEL)


def test_unbuildable_last_resort_fails_at_register_not_at_run(tmp_path: Path, monkeypatch) -> None:
    def broken(_body):
        raise ImportError("no openai in this interpreter")

    monkeypatch.setattr(injector, "_completion", broken)
    with pytest.raises(ImportError):
        injector.make_llm_execution(*setup_state(tmp_path, []))
