# File: fixtures/fx_trimmer/__init__.py
# [VERIFIED] transform_terminal_output(command, output, returncode, task_id, env_type, tool_call_id) -> str replaces the output, first string wins:
# tools/terminal_tool_result.py:136-147. [VERIFIED by the §9.1 run] not exercised by the probe (the probe tapped transform_tool_result).
"""Demo trimmer: removes lines that contain FAILED from terminal output."""
from __future__ import annotations

from typing import Any


def transform_terminal_output(command: str = "", output: Any = None, **_: Any) -> Any:
    if not isinstance(output, str) or "FAILED" not in output:
        return None
    kept = [line for line in output.splitlines() if "FAILED" not in line]
    return "\n".join(kept) + ("\n" if output.endswith("\n") else "")


def register(ctx: Any) -> None:
    ctx.register_hook("transform_terminal_output", transform_terminal_output)
