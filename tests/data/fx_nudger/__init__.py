# File: tests/data/fx_nudger/__init__.py
# pre_verify continue directive {"action": "continue", "message": "<text>"}: hermes_cli/plugins.py:1239-1257; the gate
# runs after a turn's file edits (agent/turn_stop_gates.py:53-76) and passes the attempt number, so a hook throttles
# itself on it.
"""Test-only plugin that asks the agent to verify its edits once, then lets the turn end."""
from __future__ import annotations

from typing import Any

NUDGE = "fx_nudger: re-read the files you edited before you finish."


def pre_verify(attempt: Any = 0, changed_paths: Any = None, **_: Any) -> Any:
    if attempt == 0 and changed_paths:
        return {"action": "continue", "message": NUDGE}
    return None


def register(ctx: Any) -> None:
    ctx.register_hook("pre_verify", pre_verify)
