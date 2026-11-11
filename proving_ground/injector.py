# File: proving_ground/injector.py
# ChatCompletion.model_validate body shape, step counting and final reply: a ChatCompletion
# built with model_validate ran end to end in a probe child (4 scripted steps, 5 llm_execution calls); request["messages"] is a list of dicts.
# ABORT-file handling and the guarded error path (tests/test_injector.py).
"""Scripted llm_execution middleware. Registered only inside a drill child (safety.is_drill_child)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from . import safety
from .observer import EventLog, ReplyLedger, clip_text, load_plan, trace_labels, write_abort

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


def _content_text(content: Any) -> str:
    """A message's text: a string as is, a list of content parts joined by their text fields."""
    if isinstance(content, list):
        return "\n".join(str(p.get("text", "")) for p in content if isinstance(p, dict) and p.get("type") == "text")
    return "" if content is None else str(content)


def context_view(request: Mapping[str, Any], messages: list[Any]) -> dict[str, Any]:
    """What this step sends the model besides tool results and PROVING GROUND's own scripted tool calls: the system
    and user messages, any assistant text, and the names of the tools offered (--all-wires only)."""
    kept = [{"role": str(m.get("role")), "content": clip_text(_content_text(m.get("content")))}
            for m in messages if isinstance(m, dict) and m.get("role") in ("system", "user", "assistant")
            and not m.get("tool_calls")]
    tools = request.get("tools") if isinstance(request.get("tools"), list) else []
    names = sorted({str((t.get("function") or {}).get("name")) for t in tools if isinstance(t, dict)})
    return {"messages": kept, "tools": names}


def _canon(value: Any) -> str:
    """A comparable form of one request field: JSON text, with any non-JSON object reduced to its type name (a deep
    copy of an object is a new object, so comparing objects would flag every copy as a change)."""
    return json.dumps(value, sort_keys=True, default=lambda o: type(o).__name__)


def request_rewrite(request: Mapping[str, Any], original: Any, trace: Any) -> list[str]:
    """What differs between the request this step received and the request before any middleware ran
    (agent/turn_api_call.py:139-145 passes both, plus the llm_request middleware_trace): the changed fields and
    the trace labels. Empty when nothing was rewritten."""
    labels = [f"llm_request: {label}" for label in trace_labels(trace)]
    if not isinstance(original, Mapping):
        return labels
    keys = sorted(set(request) | set(original), key=str)
    return [str(k) for k in keys if _canon(request.get(k)) != _canon(original.get(k))] + labels


def last_user_text(messages: list[Any]) -> str | None:
    """The newest user message's text: on a final reply after the first, the pre_verify nudge the turn continued on."""
    users = [m for m in messages if isinstance(m, dict) and m.get("role") == "user"]
    return _content_text(users[-1].get("content")) if users else None


def _step_messages(request: Mapping[str, Any], original: Any) -> list[Any] | None:
    """Steps are counted from the request before middleware: an llm_request middleware that trims history must not
    make the drill re-issue an earlier call."""
    for source in (original, request):
        messages = source.get("messages") if isinstance(source, Mapping) else None
        if isinstance(messages, list):
            return messages
    return None


@dataclass
class StepContext:
    """What one llm_execution call carries besides the request, and the child-side helpers that observe it."""
    original: Any = None
    trace: Any = None
    loaded_probe: Callable[[str], bool] | None = None
    order_probe: Callable[[], Any] | None = None
    ledger: ReplyLedger | None = None
    watcher: Any = None  # census.Watcher
    registry_probe: Callable[[], Mapping[str, Any]] | None = None


def scripted_reply(request: Any, state_dir: Path, log: EventLog,
                   loaded_probe: Callable[[str], bool] | None = None,
                   order_probe: Callable[[], Any] | None = None, step: StepContext | None = None) -> dict[str, Any]:
    step = step or StepContext(loaded_probe=loaded_probe, order_probe=order_probe)
    abort_path = state_dir / safety.ABORT_FILENAME
    if abort_path.exists():
        reason = abort_path.read_text(encoding="utf-8").strip() or "ABORTED"
        return final_body(f"{ABORT_SENTINEL} {reason}")
    messages = _step_messages(request, step.original) if isinstance(request, dict) else None
    if not isinstance(messages, list):
        write_abort(state_dir, log, "REFUSED_API_MODE")
        return final_body(f"{ABORT_SENTINEL} REFUSED_API_MODE")
    plan = load_plan(state_dir)
    calls = plan["calls"]
    done = completed_steps(messages)
    call_id = str(calls[done]["id"]) if done < len(calls) else "final"
    rewrite = request_rewrite(request, step.original, step.trace)
    log.write("llm_execution", step=done, n_messages=len(messages), provider_called=False, call_id=call_id,
              **({"request_rewrite": rewrite} if rewrite else {}))
    if plan.get("wires"):
        log.write("llm_context", step=done, **context_view(request, messages))
    if done == 0:
        _first_step(plan, log, step)
    body = tool_call_body(done, calls[done]) if done < len(calls) else final_body(str(plan.get("final_reply") or DONE_SENTINEL))
    if done >= len(calls):
        log.write("done", steps=done, last_user=clip_text(last_user_text(messages)))
    if step.ledger is not None:
        step.ledger.expect(call_id, body)
    return body


def _first_step(plan: Mapping[str, Any], log: EventLog, step: StepContext) -> None:
    """Every plugin has loaded by the first model step: confirm the subjects, watch any that loaded after
    PROVING GROUND, and record what each registered."""
    if step.loaded_probe is None:
        return
    expected = [str(k) for k in plan.get("subjects") or []]
    loaded = [k for k in expected if _safe_probe(step.loaded_probe, k)]
    census = None
    if step.watcher is not None and step.registry_probe is not None:
        step.watcher.wrap_loaded(at_step=True)
        census = _safe_census(step)
    log.write("plugins_loaded", expected=expected, loaded=loaded, missing=[k for k in expected if k not in loaded],
              order=_child_order(step.order_probe, expected),
              inner=_loaded_after(_child_order(step.order_probe, None), str(plan.get("pg_key") or ""), expected),
              **({"census": census} if census is not None else {}))


def _loaded_after(order: list[str] | None, pg_key: str, expected: list[str]) -> list[str]:
    """Subjects the child loaded after PROVING GROUND (a requires_plugins layer): their llm_execution middleware
    sits inside PROVING GROUND's frame, which never calls next_call."""
    if not order or pg_key not in order:
        return []
    return [k for k in order[order.index(pg_key) + 1:] if k in expected]


def _safe_census(step: StepContext) -> dict[str, Any] | None:
    try:
        return step.watcher.census(step.registry_probe())
    except Exception:
        return None  # no census: the report says the registrations are unknown


def _child_order(probe: Callable[[], Any] | None, expected: list[str] | None) -> list[str] | None:
    """The subject plugins (every plugin when *expected* is None) in the order the child actually loaded them;
    None when the child cannot say."""
    if probe is None:
        return None
    try:
        return [str(k) for k in probe() if expected is None or str(k) in expected]
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
                       order_probe: Callable[[], Any] | None = None, *, ledger: ReplyLedger | None = None,
                       watcher: Any = None, registry_probe: Callable[[], Mapping[str, Any]] | None = None
                       ) -> Callable[..., Any]:
    """The last-resort reply is built here, at register time. Hermes takes only a ChatCompletion back from
    llm_execution (agent/turn_api_call.py:139 hands it to the same intake as a provider response), and a callback that
    raises is skipped to the next frame and finally to the provider (hermes_cli/middleware.py:206-214). Built up front,
    the error path returns an object that already exists; if it cannot be built at all, register() fails, the child
    never activates, and the runner reports NOT_ACTIVATED. PROVING GROUND loads last, so this is the innermost
    frame: every subject llm_execution middleware wraps it, and next_call is never invoked."""
    last_resort = _completion(final_body(f"{ABORT_SENTINEL} INJECTOR_ERROR"))

    def llm_execution(request: Any = None, next_call: Any = None, original_request: Any = None,
                      middleware_trace: Any = None, **_kwargs: Any) -> Any:
        step = StepContext(original_request, middleware_trace, loaded_probe, order_probe, ledger, watcher,
                           registry_probe)
        try:
            return _completion(scripted_reply(request, state_dir, log, step=step))
        except Exception as exc:  # middleware fails open; an exception here would reach the provider
            write_abort(state_dir, log, f"INJECTOR_ERROR:{type(exc).__name__}")
            return _fresh(last_resort)

    llm_execution.__name__ = "provingground_llm_execution"
    return llm_execution
