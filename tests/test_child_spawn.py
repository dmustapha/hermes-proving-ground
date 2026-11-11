# File: tests/test_child_spawn.py
# I4b: the child env, argv and config force approvals on and carry no provider keys; plugin copies withhold
# credential-shaped files, secret-shaped settings and symlinks that leave the plugin dir; the warm cache is ours.
from __future__ import annotations

from pathlib import Path

import pytest

from proving_ground import runner


def test_env_is_an_allow_list_without_yolo_or_secrets(tmp_path: Path) -> None:
    base = {"PATH": "/usr/bin", "HERMES_YOLO_MODE": "1", "OPENAI_API_KEY": "unit-test-value",
            "HERMES_HOME": "/somewhere/real", "HOME": "/Users/someone"}
    env = runner.child_env(base, tmp_path, "a" * 64)
    assert "HERMES_YOLO_MODE" not in env and "OPENAI_API_KEY" not in env
    assert env["HERMES_HOME"] == str(tmp_path / "home") and env["HOME"] == str(tmp_path / "userhome")
    assert env["HTTPS_PROXY"] == runner.DEAD_PROXY and env["TMPDIR"] == str(tmp_path / "tmp")


def test_argv_never_bypasses_approvals() -> None:
    argv = runner.child_argv(Path("/usr/local/bin/hermes"), 10)
    assert not {"--yolo", "-z", "--oneshot"} & set(argv)
    assert argv[argv.index("--base-url") + 1] == runner.DEAD_PROVIDER_URL
    assert argv[argv.index("--max-turns") + 1] == "12"


def test_child_config_forces_manual_deny(tmp_path: Path) -> None:
    text = runner.render_child_config(["fx_guard", "proving-ground"], {}, tmp_path / "sandbox")
    for line in ('mode: "manual"', 'single_query_mode: "deny"', 'cron_mode: "deny"', 'unattended_mode: "deny"'):
        assert line in text
    assert '- "proving-ground"' in text


def test_plugin_copy_withholds_credentials_and_outside_symlinks(tmp_path: Path) -> None:
    src = tmp_path / "plug"
    src.mkdir()
    (src / "__init__.py").write_text("x = 1\n")
    (src / ".env").write_text("TOKEN=unit\n")
    (src / "server.key").write_text("k\n")
    (src / "auth.json").write_text("{}\n")
    outside = tmp_path / "outside.txt"
    outside.write_text("real\n")
    (src / "data").symlink_to(outside)
    dropped: list[str] = []
    runner._copy_plugin(src, tmp_path / "copy", dropped)
    assert sorted(p.name for p in (tmp_path / "copy").iterdir()) == ["__init__.py"]
    assert len(dropped) == 4
    assert runner.redact_entries({"api_key": "x", "mode": "strict", "nested": {"token": "y"}}, dropped, "p") == \
        {"mode": "strict", "nested": {}}


def test_foreign_or_symlinked_cache_is_refused(tmp_path: Path) -> None:
    from proving_ground import safety

    real = tmp_path / "elsewhere"
    real.mkdir()
    (tmp_path / "cache").symlink_to(real)
    with pytest.raises(safety.Refusal):
        runner.ensure_private_cache(tmp_path / "cache" / "installs")
    fresh = tmp_path / "pc" / "installs"
    runner.ensure_private_cache(fresh)
    assert oct(fresh.stat().st_mode & 0o777) == "0o700"


def test_approvals_line_is_derived_from_the_child_config() -> None:
    assert runner.approvals_summary().startswith("mode manual, single_query_mode deny, cron_mode deny")
