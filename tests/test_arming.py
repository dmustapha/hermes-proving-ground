# File: tests/test_arming.py
# I1: the scripted-turn middleware is registered only in a drill child (triple-key activation).
from __future__ import annotations

import secrets
import shutil
import tempfile
from pathlib import Path
from typing import Iterator

import pytest

from proving_ground import safety


def make_child_env(tmp_root: Path, token: str) -> dict[str, str]:
    (tmp_root / "home" / safety.STATE_DIRNAME).mkdir(parents=True)
    (tmp_root / "tmp").mkdir()
    (tmp_root / "home" / safety.STATE_DIRNAME / safety.TOKEN_FILENAME).write_text(token, encoding="utf-8")
    return {safety.TOKEN_ENV: token, "HERMES_HOME": str(tmp_root / "home"), "TMPDIR": str(tmp_root / "tmp")}


@pytest.fixture()
def drill_root() -> Iterator[Path]:
    root = Path(tempfile.mkdtemp(prefix=safety.ROOT_PREFIX, dir=Path(tempfile.gettempdir()).resolve())).resolve()
    yield root
    shutil.rmtree(root, ignore_errors=True)


def test_all_three_keys_activate(drill_root: Path) -> None:
    assert safety.is_drill_child(make_child_env(drill_root, secrets.token_hex(32)))


@pytest.mark.parametrize("breaker", ["no_env_token", "wrong_file_token", "no_file", "wrong_tmpdir", "home_name"])
def test_each_missing_key_blocks_activation(drill_root: Path, breaker: str) -> None:
    env = make_child_env(drill_root, secrets.token_hex(32))
    if breaker == "no_env_token":
        env.pop(safety.TOKEN_ENV)
    elif breaker == "wrong_file_token":
        (drill_root / "home" / safety.STATE_DIRNAME / safety.TOKEN_FILENAME).write_text("0" * 64, encoding="utf-8")
    elif breaker == "no_file":
        (drill_root / "home" / safety.STATE_DIRNAME / safety.TOKEN_FILENAME).unlink()
    elif breaker == "wrong_tmpdir":
        env["TMPDIR"] = tempfile.gettempdir()
    else:
        renamed = drill_root / "notHome"
        (drill_root / "home").rename(renamed)
        env["HERMES_HOME"] = str(renamed)
    assert not safety.is_drill_child(env)


def test_real_hermes_home_never_activates() -> None:
    token = secrets.token_hex(32)
    env = {safety.TOKEN_ENV: token, "HERMES_HOME": str(Path.home() / ".hermes"), "TMPDIR": tempfile.gettempdir()}
    assert not safety.is_drill_child(env)


def test_live_register_adds_no_middleware(plugin_root_module, fake_ctx, monkeypatch) -> None:
    monkeypatch.delenv(safety.TOKEN_ENV, raising=False)
    plugin_root_module.register(fake_ctx)
    assert fake_ctx.middleware == []
    assert sorted(fake_ctx.hooks) == sorted(plugin_root_module.HOOK_NAMES)
    assert fake_ctx.commands == ["provingground"] and fake_ctx.cli_commands == ["provingground"]
