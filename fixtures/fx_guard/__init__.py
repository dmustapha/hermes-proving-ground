# File: fixtures/fx_guard/__init__.py
# pre_tool_call block directive {"action": "block", "message": "<text>"}: hermes_cli/plugins.py:1153-1159 (observed in a probe run).
"""Demo guard: blocks any terminal command that contains the guard marker."""
from __future__ import annotations

from typing import Any

MARKER = "PG_GUARD_MARKER"


def pre_tool_call(tool_name: str = "", args: Any = None, **_: Any) -> Any:
    command = str((args or {}).get("command", "")) if isinstance(args, dict) else ""
    if tool_name == "terminal" and MARKER in command:
        return {"action": "block", "message": "fx_guard: command carries the guard marker"}
    return None


def register(ctx: Any) -> None:
    ctx.register_hook("pre_tool_call", pre_tool_call)
