# File: tests/data/fx_pair_a/__init__.py
# modify directive {"action": "modify", "args": {...}} merged after every pre_tool_call ran (hermes_cli/plugins.py:1143-1150).
"""Test-only plugin: half of an effect that appears only when fx_pair_b is loaded too."""
from __future__ import annotations

from typing import Any

PLAIN, PAIR = "PG_PLAIN_MARKER", "PG_PAIR_MARKER"


def pre_tool_call(tool_name: str = "", args: Any = None, **_: Any) -> Any:
    command = str(args.get("command", "")) if isinstance(args, dict) else ""
    if tool_name == "terminal" and PLAIN in command:
        return {"action": "modify", "args": {"command": command.replace(PLAIN, PAIR)}}
    return None


def register(ctx: Any) -> None:
    ctx.register_hook("pre_tool_call", pre_tool_call)
