# File: proving_ground/injector.py
# ChatCompletion.model_validate body shape, step counting and final reply: a ChatCompletion
# built with model_validate ran end to end in a probe child (4 scripted steps, 5 llm_execution calls); request["messages"] is a list of dicts.
# ABORT-file handling and the guarded error path (tests/test_injector.py).
"""Scripted llm_execution middleware. Registered only inside a drill child (safety.is_drill_child)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from . import safety
from .observer import EventLog, load_plan, write_abort

ABORT_SENTINEL = "PROVINGGROUND DRILL_ABORTED"
DONE_SENTINEL = "PROVINGGROUND DRILL_DONE"


def _usage() -> dict[str, int]:
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def _completion(body: dict[str, Any]) -> Any:
    from openai.types.chat import ChatCompletion

    return ChatCompletion.model_validate(body)


def tool_call_body(index: int, call: dict[str, Any]) -> dict[str, Any]:
    tool_call = {"id": call["call_ref"], "type": "function",
                 "function": {"name": call["tool"], "arguments": json.dumps(call["args"])}}
    message = {"role": "assistant", "content": None, "tool_calls": [tool_call]}
    return {"id": f"pg-{index}", "object": "chat.completion", "created": 0, "model": safety.SCRIPTED_MODEL,
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": message}], "usage": _usage()}


def final_body(text: str) -> dict[str, Any]:
    message = {"role": "assistant", "content": text}
    return {"id": "pg-final", "object": "chat.completion", "created": 0, "model": safety.SCRIPTED_MODEL,
            "choices": [{"index": 0, "finish_reason": "stop", "message": message}], "usage": _usage()}


def completed_steps(messages: list[Any]) -> int:
    return sum(1 for m in messages if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls"))


def scripted_reply(request: Any, state_dir: Path, log: EventLog,
                   loaded_probe: Callable[[str], bool] | None = None,
                   order_probe: Callable[[], Any] | None = None) -> dict[str, Any]:
    abort_path = state_dir / safety.ABORT_FILENAME
    if abort_path.exists():
        reason = abort_path.read_text(encoding="utf-8").strip() or "ABORTED"
        return final_body(f"{ABORT_SENTINEL} {reason}")
    messages = request.get("messages") if isinstance(request, dict) else None
    if not isinstance(messages, list):
        write_abort(state_dir, log, "REFUSED_API_MODE")
        return final_body(f"{ABORT_SENTINEL} REFUSED_API_MODE")
    plan = load_plan(state_dir)
    calls = plan["calls"]
    done = completed_steps(messages)
    log.write("llm_execution", step=done, n_messages=len(messages), provider_called=False)
    if done == 0 and loaded_probe is not None:
        expected = [str(k) for k in plan.get("subjects") or []]
        loaded = [k for k in expected if _safe_probe(loaded_probe, k)]
        log.write("plugins_loaded", expected=expected, loaded=loaded,
                  missing=[k for k in expected if k not in loaded], order=_child_order(order_probe, expected))
    if done < len(calls):
        return tool_call_body(done, calls[done])
    log.write("done", steps=done)
    return final_body(DONE_SENTINEL)


def _child_order(probe: Callable[[], Any] | None, expected: list[str]) -> list[str] | None:
    """The subject plugins in the order the child actually loaded them; None when the child cannot say."""
    if probe is None:
        return None
    try:
        return [str(k) for k in probe() if str(k) in expected]
    except Exception:
        return None


def _safe_probe(probe: Callable[[str], bool], key: str) -> bool:
    try:
        return bool(probe(key))
    except Exception:
        return False  # unknown load state is reported as LOAD FAILED, never as loaded


def _fresh(reply: Any) -> Any:
    """A copy of the prebuilt reply when it can make one (pydantic model_copy), otherwise the reply itself."""
    try:
        return reply.model_copy(deep=True)
    except Exception:
        return reply


def make_llm_execution(state_dir: Path, log: EventLog, loaded_probe: Callable[[str], bool] | None = None,
                       order_probe: Callable[[], Any] | None = None) -> Callable[..., Any]:
    """The last-resort reply is built here, at register time. Hermes takes only a ChatCompletion back from
    llm_execution (agent/turn_api_call.py:139 hands it to the same intake as a provider response), and a callback that
    raises is skipped to the next frame and finally to the provider (hermes_cli/middleware.py:206-214). Built up front,
    the error path returns an object that already exists; if it cannot be built at all, register() fails, the child
    never activates, and the runner reports NOT_ACTIVATED."""
    last_resort = _completion(final_body(f"{ABORT_SENTINEL} INJECTOR_ERROR"))

    def llm_execution(request: Any = None, next_call: Any = None, **_kwargs: Any) -> Any:
        try:
            return _completion(scripted_reply(request, state_dir, log, loaded_probe, order_probe))
        except Exception as exc:  # middleware fails open; an exception here would reach the provider
            write_abort(state_dir, log, f"INJECTOR_ERROR:{type(exc).__name__}")
            return _fresh(last_resort)

    llm_execution.__name__ = "provingground_llm_execution"
    return llm_execution
