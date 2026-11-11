# The CLI module loads on first use: loading the plugin (a chat session, a drill child) does not import the
# report, runner and rich stack; the registered surfaces still reach the real CLI when used.
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
NAME = "pg_lazy_root"


def fresh_plugin() -> Any:
    for key in [k for k in sys.modules if k == NAME or k.startswith(NAME + ".")]:
        del sys.modules[key]
    spec = importlib.util.spec_from_file_location(NAME, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[NAME] = module
    spec.loader.exec_module(module)
    return module


class RecordingContext:
    def __init__(self) -> None:
        self.cli: dict[str, Any] = {}
        self.slash: Any = None

    def register_cli_command(self, **kwargs: Any) -> None:
        self.cli = kwargs

    def register_command(self, _name: str, handler: Any, **_: Any) -> None:
        self.slash = handler

    def register_hook(self, *_a: Any, **_k: Any) -> None:
        return None


def test_registering_the_plugin_does_not_import_the_cli() -> None:
    plugin = fresh_plugin()
    plugin.register(RecordingContext())
    assert f"{NAME}.proving_ground.cli" not in sys.modules
    assert f"{NAME}.proving_ground.report" not in sys.modules


def test_registered_parser_setup_reaches_the_real_cli_on_first_use() -> None:
    plugin = fresh_plugin()
    ctx = RecordingContext()
    plugin.register(ctx)
    parser = argparse.ArgumentParser()
    ctx.cli["setup_fn"](parser)
    assert f"{NAME}.proving_ground.cli" in sys.modules
    assert parser.parse_args(["show", "TC04"]).call == "TC04"


def test_slash_wrapper_passes_the_calling_session_through(monkeypatch) -> None:
    plugin = fresh_plugin()
    seen: dict[str, Any] = {}

    def fake_slash(raw_args: str = "", **kwargs: Any) -> str:
        seen.update(raw=raw_args, **kwargs)
        return "ok"

    monkeypatch.setattr(plugin._cli(), "handle_slash", fake_slash)
    assert plugin._handle_slash("show", platform="cli", session_id="s1") == "ok"
    assert seen == {"raw": "show", "platform": "cli", "session_id": "s1"}
