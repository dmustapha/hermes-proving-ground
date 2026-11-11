# File: __init__.py
# [VERIFIED] ctx.register_cli_command(name, help, setup_fn, handler_fn, description) hermes_cli/plugins_context.py:442-453;
# ctx.register_command(name, handler, description, args_hint) plugins_context.py:456-486;
# ctx.register_hook(name, cb) :741; ctx.register_middleware(kind, cb) :745 with kind in VALID_MIDDLEWARE
# (hermes_cli/middleware.py:24-26). Relative imports work: plugins load as a package with
# submodule_search_locations=[plugin_dir] (hermes_cli/plugins_loader.py:834-841).
"""PROVING GROUND plugin entry. The scripted-turn middleware exists only inside a drill child."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable

from .proving_ground import cli, safety

HOOK_NAMES: tuple[str, ...] = (
    "pre_tool_call",
    "post_tool_call",
    "transform_tool_result",
    "transform_terminal_output",
    "pre_approval_request",
    "post_approval_response",
)


def _inert_hook(name: str) -> Callable[..., None]:
    def inert(**_kwargs: Any) -> None:
        return None

    inert.__name__ = f"provingground_inert_{name}"
    return inert


def _register_surfaces(ctx: Any) -> None:
    ctx.register_cli_command(
        name="provingground",
        help="Run a wiring test of your installed plugin stack in throwaway child Hermes homes",
        setup_fn=cli.setup_parser,
        handler_fn=cli.handle_cli,
        description="Sends known, harmless test calls through your plugins in a throwaway child Hermes.",
    )
    ctx.register_command(
        "provingground",
        handler=cli.handle_slash,
        description="Wiring test of your plugin stack (throwaway child home, model not called)",
        args_hint="[fixtures] [show <cell>]",
    )


def _register_drill_child(ctx: Any) -> None:
    from .proving_ground import injector, observer

    state_dir = Path(os.environ["HERMES_HOME"]) / safety.STATE_DIRNAME
    plan = observer.load_plan(state_dir)
    log = observer.EventLog(state_dir / observer.EVENTS_FILENAME)
    hooks = observer.make_hooks(state_dir, Path(plan["sandbox"]), log)
    for name in HOOK_NAMES:
        ctx.register_hook(name, hooks[name])
    probe = getattr(ctx, "has_plugin", None)  # public PluginContext.has_plugin (plugins_context.py:57-63)
    ctx.register_middleware("llm_execution", injector.make_llm_execution(state_dir, log, probe))
    observer.install_socket_audit(log)
    log.write("activated", calls=len(plan["calls"]), dont_write_bytecode=sys.dont_write_bytecode)


def register(ctx: Any) -> None:
    _register_surfaces(ctx)
    if safety.is_drill_child():
        _register_drill_child(ctx)
        return
    for name in HOOK_NAMES:
        ctx.register_hook(name, _inert_hook(name))
