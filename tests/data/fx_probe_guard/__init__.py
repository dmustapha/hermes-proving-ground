# File: tests/data/fx_probe_guard/__init__.py
# pre_tool_call directives: block {"action": "block", "message"} and modify {"action": "modify", "args"}
# (hermes_cli/plugins.py:1132-1160: modify merges are kept even when a block wins; the first block wins).
"""Test-only guard for tools outside the drill allow-list: blocks code execution, rewrites a delegation goal."""
from __future__ import annotations

from typing import Any

BLOCK_MESSAGE = "fx_probe_guard: code execution is not allowed here"
REWRITTEN_GOAL = "fx_probe_guard: rewritten goal"


def pre_tool_call(tool_name: str = "", args: Any = None, **_: Any) -> Any:
    if tool_name == "execute_code":
        return {"action": "block", "message": BLOCK_MESSAGE}
    if tool_name == "delegate_task":
        return {"action": "modify", "args": {"goal": REWRITTEN_GOAL}}
    return None


def register(ctx: Any) -> None:
    ctx.register_hook("pre_tool_call", pre_tool_call)
