# tool_execution middleware wraps the authorized dispatch (pre_tool_call, guardrails, execute); returning without
# next_call() skips all of it, and post_tool_call still fires (agent/tool_executor.py:776-818, :1366-1381).
"""Test-only plugin that answers the plain-marker terminal call itself."""
from __future__ import annotations

import json
from typing import Any

MARKER = "PG_PLAIN_MARKER"


def tool_execution(tool_name: str = "", args: Any = None, next_call: Any = None, **_: Any) -> Any:
    if tool_name == "terminal" and MARKER in str((args or {}).get("command", "")):
        return json.dumps({"output": "answered by tx_exec_replacer\n", "exit_code": 0, "error": None})
    return next_call()


def register(ctx: Any) -> None:
    ctx.register_middleware("tool_execution", tool_execution)
