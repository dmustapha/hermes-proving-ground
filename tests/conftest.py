# File: tests/conftest.py
# [VERIFIED by the §9.1 run] test scaffolding.
from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _hermes_bin() -> Path | None:
    for candidate in (shutil.which("hermes"), str(Path.home() / ".local/bin/hermes")):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


class _PluginRootAsPlainDir:
    """The repo root is the plugin package, but a checkout named "hermes-proving-ground" is not a valid module name,
    so importlib mode would import its __init__.py standalone and fail on the relative import. Collect the root as a
    plain directory instead; tests load the plugin root explicitly (plugin_root_module). Registered as a global
    plugin because conftest hooks for a directory are looked up from its parent."""

    @pytest.hookimpl(tryfirst=True)
    def pytest_collect_directory(self, path: Path, parent: Any) -> Any:
        if path == ROOT:
            return pytest.Dir.from_parent(parent, path=path)
        return None


def pytest_configure(config: Any) -> None:
    config.pluginmanager.register(_PluginRootAsPlainDir(), "pg-plugin-root-as-dir")


def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:
    if _hermes_bin() is not None:
        return
    skip = pytest.mark.skip(reason="no hermes executable on this machine")
    for item in items:
        if "hermes" in item.keywords:
            item.add_marker(skip)


@pytest.fixture()
def settings() -> Any:
    from proving_ground import runner

    temp_base = Path(tempfile.gettempdir()).resolve()
    return runner.RunnerSettings(hermes_bin=_hermes_bin() or Path("/nonexistent/hermes"), pg_source=ROOT,
                                 warm_cache=temp_base / "provingground-cache" / "installs", temp_base=temp_base,
                                 cell_timeout_s=120.0, jobs=3)


@pytest.fixture()
def plugin_root_module() -> Any:
    spec = importlib.util.spec_from_file_location("pg_plugin_root", ROOT / "__init__.py",
                                                  submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["pg_plugin_root"] = module
    spec.loader.exec_module(module)
    return module


class FakeContext:
    """Records every register_* call the plugin makes."""

    def __init__(self) -> None:
        self.hooks: list[str] = []
        self.middleware: list[str] = []
        self.commands: list[str] = []
        self.cli_commands: list[str] = []

    def register_hook(self, name: str, callback: Any, **_: Any) -> None:
        self.hooks.append(name)

    def register_middleware(self, kind: str, callback: Any) -> None:
        self.middleware.append(kind)

    def register_command(self, name: str, handler: Any, **_: Any) -> None:
        self.commands.append(name)

    def register_cli_command(self, name: str, **_: Any) -> None:
        self.cli_commands.append(name)


@pytest.fixture()
def fake_ctx() -> FakeContext:
    return FakeContext()
