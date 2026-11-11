# File: proving_ground/census.py
# Drill child only, read-only. LoadedPlugin.{hooks,middleware,tools,commands}_registered (hermes_cli/plugins.py:226-229)
# are filled from the registration ledger after each plugin's register() (plugins_loader.py:715-733). Hooks dispatch
# from manager._hooks[name] (plugins_dispatch.py:293) and execution middleware from manager._middleware[kind]
# (middleware.py:166-168), both read at call time, so a callback replaced in place is the one that runs. A directory
# plugin's module is hermes_plugins.<slug> (plugins_loader.py:815-841); a callback belongs to the subject whose module
# name its __module__ starts with. Narrow callbacks get only the kwargs they declare (plugins_dispatch.py:250-263):
# the watcher accepts **kwargs and applies the same narrowing before calling the plugin, so the plugin sees exactly
# what it saw before.
"""Registration census and liveness for subject plugins: what each registered, and which of it fired."""
from __future__ import annotations

import functools
import inspect
import threading
from typing import Any, Callable, Mapping, Sequence

from .observer import EventLog

KINDS = (("hooks", "hooks_registered"), ("middleware", "middleware_registered"),
         ("tools", "tools_registered"), ("commands", "commands_registered"))
WATCHED = "__provingground_watched__"


def _narrow_kwargs(callback: Callable[..., Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    """The rule Hermes applies before calling a hook or middleware (PluginDispatchMixin._hook_callback_kwargs,
    plugins_dispatch.py:250-263), restated here so no private core module is imported: **kwargs callbacks and ones
    without an introspectable signature get everything, narrow ones only the keyword names they declare."""
    try:
        parameters = inspect.signature(callback).parameters
    except (TypeError, ValueError):
        return dict(payload)
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
        return dict(payload)
    keyword = {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
    return {k: v for k, v in payload.items() if k in parameters and parameters[k].kind in keyword}


def registrations(registry: Mapping[str, Any], keys: Sequence[str]) -> dict[str, Any]:
    """{key: {"hooks": [...], "middleware": [...], "tools": [...], "commands": [...]}} or {key: None} when unknown."""
    out: dict[str, Any] = {}
    for key in keys:
        loaded = registry.get(key)
        lists = {name: getattr(loaded, attr, None) for name, attr in KINDS}
        out[key] = ({name: [str(v) for v in value] for name, value in lists.items()}
                    if loaded is not None and all(isinstance(v, list) for v in lists.values()) else None)
    return out


class Watcher:
    """Replaces each subject callback with a pass-through that logs its first firing (per call when the payload names
    a tool_call_id). Idempotent: run once at register time and again at the first model step for later loaders."""

    def __init__(self, log: EventLog, manager_probe: Callable[[], Any], subjects: Sequence[str]) -> None:
        self.log, self._manager, self.subjects = log, manager_probe, tuple(subjects)
        self._seen: set[tuple[str, str, str, str]] = set()
        self._lock = threading.Lock()
        self.wrapped: dict[tuple[str, str, str], int] = {}  # (plugin, kind, name) -> callbacks watched
        self.late: set[str] = set()  # subjects first watched at the model step, not at PROVING GROUND's load

    def _modules(self) -> dict[str, str]:
        registry = getattr(self._manager(), "_plugins", None) or {}
        names = {k: getattr(getattr(registry.get(k), "module", None), "__name__", None) for k in self.subjects}
        return {k: n for k, n in names.items() if isinstance(n, str) and n}

    @staticmethod
    def owner(callback: Any, modules: Mapping[str, str]) -> str | None:
        module = getattr(callback, "__module__", None)
        if not isinstance(module, str):
            return None
        return next((k for k, m in modules.items() if module == m or module.startswith(m + ".")), None)

    def _note(self, plugin: str, kind: str, name: str, call_id: Any) -> None:
        key = (plugin, kind, name, str(call_id or ""))
        with self._lock:
            if key in self._seen:
                return
            self._seen.add(key)
        self.log.write("fired", plugin=plugin, kind=kind, name=name, tool_call_id=call_id or None)

    def _wrap(self, callback: Callable[..., Any], plugin: str, kind: str, name: str) -> Callable[..., Any]:
        def watched(*args: Any, **kwargs: Any) -> Any:
            self._note(plugin, kind, name, kwargs.get("tool_call_id"))
            return callback(*args, **_narrow_kwargs(callback, kwargs))

        functools.update_wrapper(watched, callback)
        watched.__dict__.pop("__wrapped__", None)  # dispatch must see **kwargs here, not the plugin's narrow signature
        setattr(watched, WATCHED, plugin)
        return watched

    def wrap_loaded(self, at_step: bool = False) -> None:
        """Never raises: liveness is a report detail, never a reason to stop a drill."""
        try:
            manager = self._manager()
            modules = self._modules()
            for kind, table in (("hook", getattr(manager, "_hooks", None)),
                                ("middleware", getattr(manager, "_middleware", None))):
                if isinstance(table, dict):
                    self._wrap_table(kind, table, modules, at_step)
        except Exception as exc:
            self.log.write("census_error", error=type(exc).__name__)

    def _wrap_table(self, kind: str, table: dict[str, list[Any]], modules: Mapping[str, str], at_step: bool) -> None:
        for name, callbacks in table.items():
            for index, callback in enumerate(list(callbacks)):
                plugin = None if getattr(callback, WATCHED, None) else self.owner(callback, modules)
                if plugin is None:
                    continue
                callbacks[index] = self._wrap(callback, plugin, kind, name)
                slot = (plugin, kind, name)
                self.wrapped[slot] = self.wrapped.get(slot, 0) + 1
                if at_step:
                    self.late.add(plugin)

    def census(self, registry: Mapping[str, Any]) -> dict[str, Any]:
        """Per subject: what it registered, and whether every hook and middleware registration is watched."""
        found = registrations(registry, self.subjects)
        out: dict[str, Any] = {}
        for key, registered in found.items():
            watched = registered is not None and all(
                self.wrapped.get((key, kind, name), 0) >= registered[field].count(name)
                for kind, field in (("hook", "hooks"), ("middleware", "middleware")) for name in set(registered[field]))
            out[key] = {"registered": registered, "watched": watched, "late": key in self.late}
        return out
