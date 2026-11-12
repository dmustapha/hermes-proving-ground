# File: __init__.py
# ctx.register_cli_command(name, help, setup_fn, handler_fn, description) hermes_cli/plugins_context.py:442-453;
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

from .proving_ground import safety

CALL_HOOK_NAMES: tuple[str, ...] = (
    "pre_tool_call",
    "post_tool_call",
    "transform_tool_result",
    "transform_terminal_output",
    "pre_verify",
)
# The step reply as the agent took it in, after every llm_execution middleware (observer.make_reply_tap).
STEP_HOOK_NAMES: tuple[str, ...] = ("post_api_request",)
# The context and reply wire, recorded only in a drill child run with --all-wires (observer.WIRE_HOOK_NAMES).
WIRE_HOOK_NAMES: tuple[str, ...] = ("pre_llm_call", "transform_llm_output", "post_llm_call")
HOOK_NAMES: tuple[str, ...] = CALL_HOOK_NAMES + STEP_HOOK_NAMES + WIRE_HOOK_NAMES


def _inert_hook(name: str) -> Callable[..., None]:
    def inert(**_kwargs: Any) -> None:
        return None

    inert.__name__ = f"provingground_inert_{name}"
    return inert


def _cli() -> Any:
    """The CLI module (report, runner, matrix, rich) loads on first use, not when Hermes loads the plugin: a chat
    session or a drill child that never runs the command never imports it."""
    from .proving_ground import cli

    return cli


def _setup_parser(subparser: Any) -> None:
    _cli().setup_parser(subparser)


def _handle_cli(args: Any) -> int:
    return _cli().handle_cli(args)


def _handle_slash(raw_args: str = "", *, platform: str = "", session_id: str = "", **kwargs: Any) -> str:
    return _cli().handle_slash(raw_args, platform=platform, session_id=session_id, **kwargs)


def _slash_handler(ctx: Any) -> Callable[..., str]:
    """The slash handler, carrying this context's post_message (plugins_context.py:414) so a background drill can
    post its summary into the chat when it finishes; None on a Hermes without it."""
    post = getattr(ctx, "post_message", None)

    def handler(raw_args: str = "", *, platform: str = "", session_id: str = "", **kwargs: Any) -> str:
        return _handle_slash(raw_args, platform=platform, session_id=session_id, post=post, **kwargs)

    return handler


def _register_surfaces(ctx: Any) -> None:
    ctx.register_cli_command(
        name="provingground",
        help="Run a wiring test of your installed plugin stack in throwaway child Hermes homes",
        setup_fn=_setup_parser,
        handler_fn=_handle_cli,
        description="Sends known, harmless test calls through your plugins in a throwaway child Hermes.",
    )
    ctx.register_command(
        "provingground",
        handler=_slash_handler(ctx),
        description="Wiring test of your plugin stack (throwaway child home, model not called)",
        args_hint="[fixtures] [show <cell>]",
    )


def _register_drill_child(ctx: Any) -> None:
    """Drill child only. Uses public PluginContext calls exclusively: register_hook, register_middleware and
    has_plugin. It never reads or rewrites the plugin manager's private state (_plugins, _hooks, _middleware), so a
    Hermes update cannot break it silently; what needs that state (per-plugin liveness) is reported as unknown."""
    from .proving_ground import injector, observer

    state_dir = Path(os.environ["HERMES_HOME"]) / safety.STATE_DIRNAME
    plan = observer.load_plan(state_dir)
    log = observer.EventLog(state_dir / safety.EVENTS_FILENAME)
    hooks = observer.make_hooks(state_dir, Path(plan["sandbox"]), log)
    for name in CALL_HOOK_NAMES:
        ctx.register_hook(name, hooks[name])
    ledger = observer.ReplyLedger()
    ctx.register_hook("post_api_request", observer.make_reply_tap(log, ledger))
    if plan.get("wires"):  # a default drill registers no wire hook at all, so its children are unchanged
        for name, tap in observer.make_wire_hooks(log).items():
            ctx.register_hook(name, tap)
    probe = getattr(ctx, "has_plugin", None)  # public PluginContext.has_plugin (plugins_context.py:57-63)
    ctx.register_middleware("llm_execution", injector.make_llm_execution(state_dir, log, probe, ledger=ledger))
    ctx.register_middleware("tool_execution", observer.make_tool_execution_tap(log))
    observer.install_socket_audit(log)
    log.write("activated", calls=len(plan["calls"]), dont_write_bytecode=sys.dont_write_bytecode)


def register(ctx: Any) -> None:
    _register_surfaces(ctx)
    if safety.is_drill_child():
        _register_drill_child(ctx)
        return
    for name in HOOK_NAMES:
        ctx.register_hook(name, _inert_hook(name))
