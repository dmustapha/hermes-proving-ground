# File: tests/data/fx_context/__init__.py
# pre_llm_call may return {"context": "..."}; Hermes appends it to the user message (agent/turn_context.py:942-964).
"""Test-only plugin that adds context to the model's view of the turn."""
from __future__ import annotations

from typing import Any

CONTEXT = "fx_context: house notes for this turn"


def pre_llm_call(**_: Any) -> Any:
    return {"context": CONTEXT}


def register(ctx: Any) -> None:
    ctx.register_hook("pre_llm_call", pre_llm_call)
