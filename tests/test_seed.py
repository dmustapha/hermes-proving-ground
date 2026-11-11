# Offline warm-up (`warm --from DIR`): the runtime is copied from an existing Hermes installs dir, which is only read;
# the copy lands in PG's private cache under the same ownership rules, is checked to start, and only then is warm.
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from proving_ground import cli, runner, safety, seed

INSTALL = "c93468153344913c"


def make_source(root: Path) -> Path:
    """A miniature installs dir shaped like Hermes's: facts.json with absolute paths, a venv with links, live leases."""
    install = root / "installs" / INSTALL
    env = install / "environments" / "e1"
    (env / "venv" / "bin").mkdir(parents=True)
    (env / ".leases").mkdir()
    (env / ".leases" / "lease-4242").write_text("pid 4242", encoding="utf-8")
    (env / "venv" / "pyvenv.cfg").write_text("home = /opt/python/bin\nrelocatable = true\n", encoding="utf-8")
    (env / "venv" / "bin" / "python").symlink_to("/opt/python/bin/python3")
    (env / "venv" / "bin" / "python3").symlink_to("python")
    facts = {"packages": {"venv": {"environment": str(env / "venv")}}, "schema": 1}
    (install / "facts.json").write_text(json.dumps(facts), encoding="utf-8")
    return root / "installs"


def digest(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        h.update(f"{path}|{info.st_mode}|{info.st_size}|{info.st_mtime_ns}".encode())
        if path.is_file() and not path.is_symlink():
            h.update(path.read_bytes())
    return h.hexdigest()


def settings_in(base: Path) -> runner.RunnerSettings:
    base.mkdir(parents=True, exist_ok=True)  # the temp dir a real drill uses always exists
    return runner.RunnerSettings(Path("/nonexistent/hermes"), base, base / "provingground-cache" / "installs", base)


def ok(_settings: runner.RunnerSettings) -> str:
    return "v0.23.1 @ 3b4215785882"


def test_seed_copies_points_the_copy_at_itself_and_leaves_the_source_alone(tmp_path: Path) -> None:
    source = make_source(tmp_path / "src")
    before = digest(source)
    settings = settings_in(tmp_path / "base")
    result = seed.seed_cache(settings, source, validate=ok)
    assert digest(source) == before
    copy = settings.warm_cache / INSTALL
    facts = json.loads((copy / "facts.json").read_text(encoding="utf-8"))
    assert facts["packages"]["venv"]["environment"] == str(copy / "environments" / "e1" / "venv")
    assert os.readlink(copy / "environments" / "e1" / "venv" / "bin" / "python3") == "python"
    assert list((copy / "environments" / "e1" / ".leases").iterdir()) == []  # another process's leases stay behind
    assert runner.is_warm(settings) and (settings.warm_cache.stat().st_mode & 0o777) == 0o700
    assert result.installs == (INSTALL,) and result.rewritten == 1 and result.version.startswith("v0.23.1")
    assert not list(settings.warm_cache.parent.glob("provingground-seed-*"))


def test_a_copy_that_does_not_start_is_removed_and_the_cache_stays_cold(tmp_path: Path) -> None:
    source = make_source(tmp_path / "src")
    settings = settings_in(tmp_path / "base")

    def fails(_settings: runner.RunnerSettings) -> str:
        raise safety.Refusal("WARMUP_FAILED", "the copied runtime does not start")

    with pytest.raises(safety.Refusal) as refused:
        seed.seed_cache(settings, source, validate=fails)
    assert refused.value.code == "WARMUP_FAILED"
    assert not runner.is_warm(settings) and list(settings.warm_cache.iterdir()) == []


def test_a_copy_that_fails_half_way_leaves_nothing_in_the_cache(tmp_path: Path, monkeypatch) -> None:
    source = make_source(tmp_path / "src")
    second = source / "d00000000000000b"
    shutil.copytree(source / INSTALL, second, symlinks=True)
    settings = settings_in(tmp_path / "base")
    real_rename, moved = os.rename, []

    def rename_once(src, dst):
        if moved:
            raise OSError("disk full")
        moved.append(dst)
        real_rename(src, dst)

    monkeypatch.setattr(seed.os, "rename", rename_once)
    with pytest.raises(OSError):
        seed.seed_cache(settings, source, validate=ok)
    assert moved and list(settings.warm_cache.iterdir()) == [] and not runner.is_warm(settings)
    assert not list(settings.warm_cache.parent.glob("provingground-seed-*"))


def test_validation_runs_hermes_version_and_reads_a_failure(tmp_path: Path, monkeypatch) -> None:
    settings = settings_in(tmp_path)
    seen: list[list[str]] = []

    def throwaway(_settings, argv):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 1, "", "hermes: no dependency environment is committed")

    monkeypatch.setattr(runner, "_run_throwaway", throwaway)
    with pytest.raises(safety.Refusal) as refused:
        seed._validate(settings)
    assert seen == [[str(settings.hermes_bin), "--version"]]
    assert refused.value.code == "WARMUP_FAILED" and "no dependency environment" in refused.value.message
    monkeypatch.setattr(runner, "_run_throwaway", lambda _s, argv: subprocess.CompletedProcess(
        argv, 0, "Hermes Agent v0.23.1 (2026.9.24) · stable · local 3b4215785882\n", ""))
    assert seed._validate(settings) == "v0.23.1 @ 3b4215785882"


def test_a_symlinked_or_empty_source_is_refused(tmp_path: Path) -> None:
    source = make_source(tmp_path / "src")
    (tmp_path / "link").symlink_to(source)
    (tmp_path / "empty").mkdir()
    for bad in (tmp_path / "link", tmp_path / "empty", tmp_path / "missing"):
        with pytest.raises(safety.Refusal) as refused:
            seed.find_installs(bad)
        assert refused.value.code == "SEED_UNSUPPORTED", bad


@pytest.mark.parametrize("target", ["absolute-back", "relative-out"])
def test_links_that_lead_back_into_the_source_tree_are_refused(tmp_path: Path, target: str) -> None:
    source = make_source(tmp_path / "src")
    venv_bin = source / INSTALL / "environments" / "e1" / "venv" / "bin"
    other = source / "other-install"
    other.mkdir()
    link = venv_bin / "tool"
    link.symlink_to(other if target == "absolute-back" else "../../../../../other-install")
    with pytest.raises(safety.Refusal) as refused:
        seed.find_installs(source)
    assert refused.value.code == "SEED_UNSUPPORTED" and "cannot be moved" in refused.value.message


def test_a_cold_cache_with_contents_is_never_overwritten(tmp_path: Path) -> None:
    source = make_source(tmp_path / "src")
    settings = settings_in(tmp_path / "base")
    runner.ensure_private_cache(settings.warm_cache)
    (settings.warm_cache / "half-download").mkdir()
    with pytest.raises(safety.Refusal) as refused:
        seed.seed_cache(settings, source, validate=ok)
    assert refused.value.code == "SEED_CONFLICT" and (settings.warm_cache / "half-download").is_dir()


def test_a_symlinked_cache_is_refused_like_any_warm_cache(tmp_path: Path) -> None:
    source = make_source(tmp_path / "src")
    settings = settings_in(tmp_path / "base")
    (tmp_path / "base" / "provingground-cache").mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    settings.warm_cache.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(safety.Refusal) as refused:
        seed.seed_cache(settings, source, validate=ok)
    assert refused.value.code == "SANDBOX_UNSUPPORTED" and list((tmp_path / "elsewhere").iterdir()) == []


def warm_args(**kw) -> argparse.Namespace:
    return argparse.Namespace(**{"action": "warm", "check": False, "warm_from": "/nonexistent", **kw})


def test_from_is_refused_outside_warm_and_with_check(monkeypatch) -> None:
    monkeypatch.setattr(seed, "seed_cache", lambda *_a, **_k: pytest.fail("must not copy"))
    assert cli.handle_cli(warm_args(action="run")) == 2
    assert cli.handle_cli(warm_args(check=True)) == 2


def test_warm_from_refuses_under_yolo_before_copying(monkeypatch) -> None:
    cron_identity = pytest.importorskip("cron.execution_identity")  # a Hermes core module

    monkeypatch.setattr(cron_identity, "current_cron_execution", lambda: None)
    monkeypatch.setenv("HERMES_YOLO_MODE", "1")
    monkeypatch.setattr(cli, "user_config", lambda: {})
    monkeypatch.setattr(seed, "seed_cache", lambda *_a, **_k: pytest.fail("must not copy"))
    assert cli.handle_cli(warm_args()) == 2


def test_warm_from_on_a_warm_cache_copies_nothing(monkeypatch, capsys, tmp_path: Path) -> None:
    settings = settings_in(tmp_path)
    runner.ensure_private_cache(settings.warm_cache)
    (settings.warm_cache / runner.WARM_MARKER).write_text("1", encoding="utf-8")
    monkeypatch.setattr(seed, "seed_cache", lambda *_a, **_k: pytest.fail("must not copy"))
    assert cli.warm_from(settings, tmp_path / "src") == 0
    assert "already ready; nothing copied" in capsys.readouterr().out


REAL_INSTALLS = Path.home() / ".hermes" / "installs"


@pytest.mark.hermes
@pytest.mark.skipif(not REAL_INSTALLS.is_dir(), reason="no Hermes installs dir on this machine")
def test_real_installs_seed_a_cache_that_starts_without_the_network() -> None:
    base = Path(tempfile.mkdtemp(prefix="pg-seed-test-", dir=Path(tempfile.gettempdir()).resolve()))
    try:
        before = digest(REAL_INSTALLS)
        settings = runner.RunnerSettings(Path(shutil.which("hermes") or str(Path.home() / ".local/bin/hermes")),
                                         Path(__file__).resolve().parent.parent,
                                         base / "provingground-cache" / "installs", base)
        result = seed.seed_cache(settings, REAL_INSTALLS)
        assert result.version.startswith("v") and runner.is_warm(settings)
        assert digest(REAL_INSTALLS) == before
    finally:
        shutil.rmtree(base, ignore_errors=True)
