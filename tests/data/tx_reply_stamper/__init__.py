# llm_execution middleware wraps provider execution; it calls next_call() and may edit the response
# (hermes_cli/middleware.py:162-214). Its key sorts after "proving-ground", so it runs only when PROVING GROUND's
# copy in the child sorts after it (runner.child_pg_key).
"""Test-only plugin that adds text to every model reply that carries tool calls."""
from __future__ import annotations

from typing import Any

STAMP = "stamped by tx_reply_stamper"


def llm_execution(request: Any = None, next_call: Any = None, **_: Any) -> Any:
    response = next_call()
    message = response.choices[0].message
    if message.tool_calls:
        message.content = STAMP
    return response


def register(ctx: Any) -> None:
    ctx.register_middleware("llm_execution", llm_execution)
