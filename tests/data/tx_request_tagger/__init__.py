# llm_request middleware returns {"request": {...}} to replace the provider kwargs, with optional source / reason /
# name labels recorded in middleware_trace (hermes_cli/middleware.py:63-99).
"""Test-only plugin that tags the system prompt of every step request."""
from __future__ import annotations

from typing import Any

TAG = " [tagged by tx_request_tagger]"


def llm_request(request: Any = None, **_: Any) -> Any:
    messages = [dict(m) for m in request.get("messages") or []]
    for message in messages:
        if message.get("role") == "system" and isinstance(message.get("content"), str):
            message["content"] += TAG
            break
    return {"request": {**request, "messages": messages}, "name": "tx_request_tagger", "reason": "tag"}


def register(ctx: Any) -> None:
    ctx.register_middleware("llm_request", llm_request)
