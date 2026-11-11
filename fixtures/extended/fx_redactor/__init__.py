# File: fixtures/extended/fx_redactor/__init__.py
# transform_terminal_output(command, output, returncode, task_id, env_type, tool_call_id) -> str replaces the output;
# Hermes keeps the FIRST string any callback returns and ignores the rest: tools/terminal_tool_result.py:136-147.
# Plugins load alphabetically when no requires_plugins says otherwise (hermes_cli/plugins_manifest.py:238-246),
# so fx_redactor's callback runs before fx_trimmer's.
"""Demo redactor: hides the plain marker in terminal output, and returns the output string on every call.

Returning the unchanged string (instead of None) when there is nothing to redact is the composition point this
fixture demonstrates: Hermes keeps the first string, so every output transform that loads after this one is
silently ignored. In the extended stack, fx_trimmer's change to TC04 never reaches the model."""
from __future__ import annotations

from typing import Any

MARKER = "PG_PLAIN_MARKER"
REPLACEMENT = "[redacted by fx_redactor]"


def transform_terminal_output(command: str = "", output: Any = None, **_: Any) -> Any:
    if not isinstance(output, str):
        return None
    return output.replace(MARKER, REPLACEMENT)


def register(ctx: Any) -> None:
    ctx.register_hook("transform_terminal_output", transform_terminal_output)
