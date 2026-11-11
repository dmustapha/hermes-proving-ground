# File: tests/data/fx_flaky/__init__.py
# A test copies this plugin and writes counter_path.txt next to it: a file under the test's own temp dir that counts
# how many times the plain marker reached the plugin, across every child that loads a copy.
"""Test-only plugin whose block depends on state kept between runs: it blocks the plain marker on odd visits only."""
from __future__ import annotations

from pathlib import Path
from typing import Any

MARKER = "PG_PLAIN_MARKER"
COUNTER = Path(__file__).resolve().parent / "counter_path.txt"


def _visit() -> int:
    target = Path(COUNTER.read_text(encoding="utf-8").strip())
    count = int(target.read_text(encoding="utf-8") or "0") + 1 if target.exists() else 1
    target.write_text(str(count), encoding="utf-8")
    return count


def pre_tool_call(tool_name: str = "", args: Any = None, **_: Any) -> Any:
    command = str(args.get("command", "")) if isinstance(args, dict) else ""
    if tool_name == "terminal" and MARKER in command and _visit() % 2 == 1:
        return {"action": "block", "message": "fx_flaky: blocked on this visit"}
    return None


def register(ctx: Any) -> None:
    ctx.register_hook("pre_tool_call", pre_tool_call)
