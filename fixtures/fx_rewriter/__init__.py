# File: fixtures/fx_rewriter/__init__.py
# modify directive {"action": "modify", "args": {"command": "<new>"}} merged after every pre_tool_call ran:
# hermes_cli/plugins.py:1143-1150 (observed in a probe run).
"""Demo rewriter: turns the candidate marker into the guard marker, after other checks already saw the original."""
from __future__ import annotations

from typing import Any

CANDIDATE = "PG_REWRITE_CANDIDATE"
REPLACEMENT = "PG_GUARD_MARKER"


def pre_tool_call(tool_name: str = "", args: Any = None, **_: Any) -> Any:
    command = str((args or {}).get("command", "")) if isinstance(args, dict) else ""
    if tool_name == "terminal" and CANDIDATE in command:
        return {"action": "modify", "args": {"command": command.replace(CANDIDATE, REPLACEMENT)}}
    return None


def register(ctx: Any) -> None:
    ctx.register_hook("pre_tool_call", pre_tool_call)
