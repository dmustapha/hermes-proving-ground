# File: proving_ground/observer.py
# hook kwargs: pre_tool_call(tool_name, args, task_id, session_id, tool_call_id, turn_id, api_request_id) hermes_cli/plugins.py:1133-1137;
# transform_terminal_output(command, output, returncode, task_id, env_type, tool_call_id) tools/terminal_tool_result.py:144-146;
# transform_tool_result(tool_name, args, result, <call ids>, duration_ms, status, error_type, error_message) model_tools.py:880-883;
# post_tool_call(tool_name, args, result, <call ids>, duration_ms, status, error_type, error_message) model_tools.py:715-718.
# None-returning transform hooks observe raw values; post_tool_call args are the final effective args (both observed
# in probe runs). The injected tool_call id reaches every call hook as tool_call_id; matrix.py treats
# an event whose id matches no drill call as TOOL_CALL_ID_MISMATCH (never matched by order).
# Context and reply wire (--all-wires only): pre_llm_call(session_id, task_id, turn_id, user_message,
# conversation_history, is_first_turn, model, platform, ...) agent/turn_context.py:919-931, whose {"context": ...}
# returns are appended to the user message (turn_context.py:942-964, compose_user_api_content :126-137);
# transform_llm_output(response_text, session_id, model, platform, turn_id) agent/turn_finalizer.py:535-543, where the
# first non-empty string wins, so a None-returning tap sees the reply as the model produced it;
# post_llm_call(..., assistant_response=final_response) turn_finalizer.py:494-505 carries the reply after the transform.
"""Observer hooks for the drill child. Records; never changes a result."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from . import safety

PIN_BLOCK_MESSAGE = "PROVINGGROUND: the harness refused its own call (outside the sandbox)"
TOOL_BLOCK_MESSAGE = "PROVINGGROUND: the harness refused its own call (tool not on the drill allow-list)"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class EventLog:
    """Append-only JSON-lines log shared by the child CLI and daemon processes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def write(self, ev: str, **fields: Any) -> None:
        """Never raises: a failed log write (disk full, an unserializable value) must not stop a hook from
        blocking or recording an escape after it."""
        try:
            line = json.dumps({"t": time.time(), "pid": os.getpid(), "ev": ev, **fields}, default=str)
            with self._lock:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                try:
                    os.write(fd, (line + "\n").encode("utf-8"))
                finally:
                    os.close(fd)
        except (OSError, ValueError, TypeError, RecursionError):
            return


def load_plan(state_dir: Path) -> dict[str, Any]:
    return json.loads((state_dir / safety.PLAN_FILENAME).read_text(encoding="utf-8"))


def write_abort(state_dir: Path, log: EventLog, reason: str) -> None:
    """Stop the drill: the ABORT file makes every later scripted step reply DRILL_ABORTED. Never raises."""
    try:
        (state_dir / safety.ABORT_FILENAME).write_text(reason, encoding="utf-8")
    except OSError:
        pass
    log.write("abort", reason=reason)


def _as_args(args: Any) -> dict[str, Any]:
    return dict(args) if isinstance(args, Mapping) else {}


def make_hooks(state_dir: Path, sandbox: Path, log: EventLog) -> dict[str, Callable[..., Any]]:
    def pre_tool_call(tool_name: str = "", args: Any = None, tool_call_id: Any = None, **_: Any) -> Any:
        injected = _as_args(args)
        log.write("pre_tool_call", tool=tool_name, args=injected, tool_call_id=tool_call_id)
        if tool_name not in safety.ALLOWED_TOOLS:
            write_abort(state_dir, log, "TOOL_NOT_ALLOWED")
            return {"action": "block", "message": TOOL_BLOCK_MESSAGE}
        if safety.escape_reasons(injected, sandbox):
            write_abort(state_dir, log, "INJECTED_OUTSIDE_SANDBOX")
            return {"action": "block", "message": PIN_BLOCK_MESSAGE}
        return None

    def transform_terminal_output(command: str = "", output: Any = None, returncode: Any = None,
                                  tool_call_id: Any = None, **_: Any) -> None:
        log.write("terminal_raw", command=command, output=output, returncode=returncode, tool_call_id=tool_call_id)
        return None

    def transform_tool_result(tool_name: str = "", args: Any = None, result: Any = None,
                              tool_call_id: Any = None, **_: Any) -> None:
        log.write("tool_raw", tool=tool_name, args=_as_args(args), result=result, tool_call_id=tool_call_id)
        return None

    def post_tool_call(tool_name: str = "", args: Any = None, result: Any = None, status: Any = None,
                       error_type: Any = None, error_message: Any = None, tool_call_id: Any = None,
                       **_: Any) -> None:
        final = _as_args(args)
        log.write("post_tool_call", tool=tool_name, args=final, result=result, status=status,
                  error_type=error_type, error_message=error_message, tool_call_id=tool_call_id)
        reasons = safety.escape_reasons(final, sandbox)
        if reasons:
            log.write("escaped", tool_call_id=tool_call_id, reasons=reasons, args=final)
            write_abort(state_dir, log, "ESCAPED")

    def approval_observer(hook: str) -> Callable[..., None]:
        def observe(**fields: Any) -> None:
            log.write("approval", hook=hook, fields={k: str(v)[:500] for k, v in fields.items()})

        observe.__name__ = f"provingground_{hook}"
        return observe

    return {"pre_tool_call": pre_tool_call, "post_tool_call": post_tool_call,
            "transform_tool_result": transform_tool_result,
            "transform_terminal_output": transform_terminal_output,
            "pre_approval_request": approval_observer("pre_approval_request"),
            "post_approval_response": approval_observer("post_approval_response")}


WIRE_HOOK_NAMES: tuple[str, ...] = ("pre_llm_call", "transform_llm_output", "post_llm_call")
WIRE_TEXT_CAP = 32 * 1024  # each recorded context or reply text is clipped to this many characters


def clip_text(value: Any) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= WIRE_TEXT_CAP else text[:WIRE_TEXT_CAP] + f" …[{len(text) - WIRE_TEXT_CAP} more chars]"


def make_wire_hooks(log: EventLog) -> dict[str, Callable[..., None]]:
    """Taps for the context and reply wire. Each returns None, so none of them adds context or rewrites a reply."""
    def pre_llm_call(user_message: Any = None, is_first_turn: Any = None, **_: Any) -> None:
        log.write("pre_llm_call", user_message=clip_text(user_message), is_first_turn=is_first_turn)
        return None

    def transform_llm_output(response_text: Any = None, **_: Any) -> None:
        log.write("reply_raw", text=clip_text(response_text))
        return None

    def post_llm_call(assistant_response: Any = None, **_: Any) -> None:
        log.write("reply_delivered", text=clip_text(assistant_response))
        return None

    return {"pre_llm_call": pre_llm_call, "transform_llm_output": transform_llm_output,
            "post_llm_call": post_llm_call}


def _connect_target(args: tuple[Any, ...]) -> tuple[str, bool]:
    address = args[1] if len(args) > 1 else None
    if isinstance(address, tuple) and address:
        host = str(address[0])
        return f"{host}:{address[1] if len(address) > 1 else ''}", host in LOOPBACK_HOSTS
    return str(address), True


def install_socket_audit(log: EventLog) -> None:
    """Child only: log every socket.connect target. Audit hooks cannot be removed, so never call this in the live process."""
    def audit(event: str, args: tuple[Any, ...]) -> None:
        if event != "socket.connect":
            return
        try:
            target, loopback = _connect_target(args)
            log.write("connect", address=target, loopback=loopback)
        except Exception:
            return

    sys.addaudithook(audit)
