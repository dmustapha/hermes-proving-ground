# File: tests/data/fx_replier/__init__.py
# transform_llm_output: the first hook to return a non-empty string replaces the reply (agent/turn_finalizer.py:541-545).
"""Test-only plugin that rewrites the reply the user sees."""
from __future__ import annotations

from typing import Any

SUFFIX = " [signed by fx_replier]"


def transform_llm_output(response_text: Any = None, **_: Any) -> Any:
    return f"{response_text}{SUFFIX}" if isinstance(response_text, str) and response_text else None


def register(ctx: Any) -> None:
    ctx.register_hook("transform_llm_output", transform_llm_output)
