# File: tests/data/fx_pair_b/__init__.py
# transform_tool_result(tool_name, args, result, ...) -> str replaces the result, first string wins (model_tools.py:880-883).
"""Test-only plugin: the other half of the effect fx_pair_a sets up."""
from __future__ import annotations

from typing import Any

PAIR, SEEN = "PG_PAIR_MARKER", "PG_PAIR_SEEN"


def transform_tool_result(result: Any = None, **_: Any) -> Any:
    return result.replace(PAIR, SEEN) if isinstance(result, str) and PAIR in result else None


def register(ctx: Any) -> None:
    ctx.register_hook("transform_tool_result", transform_tool_result)
