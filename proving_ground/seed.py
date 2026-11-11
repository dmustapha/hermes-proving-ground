# File: proving_ground/seed.py
# Offline warm-up (`hermes provingground warm --from DIR`). The layout copied is Hermes's installs dir: one
# <install id>/ per Hermes checkout holding facts.json (absolute paths to its environments, observed in v0.23.1),
# environments/<id>/venv (a relocatable uv venv: pyvenv.cfg "relocatable = true"), pm-runtime/ and per-environment
# .leases/ (live lease files of the processes using it). On a cold cache `hermes --version` exits 1 with "no
# dependency environment is committed for this install" (observed), so a clean --version in a throwaway home is the
# check that the copied runtime is usable without the network.
"""Seed PROVING GROUND's private warm cache by copying an existing Hermes installs dir instead of downloading."""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import runner, safety

REWRITE_SUFFIXES = frozenset({".json", ".cfg", ".pth", ".toml", ".txt"})  # small text files that may name the source
REWRITE_MAX_BYTES = 1024 * 1024
SKIPPED_DIRS = frozenset({".leases"})  # another process's live leases; the copy gets an empty directory
SEED_DISCLOSURE = ("PROVING GROUND is copying Hermes's runtime from {source} into {cache} instead of downloading it. "
                   "The source is only read; nothing there is changed. No network is used.")


@dataclass(frozen=True)
class SeedResult:
    installs: tuple[str, ...]
    rewritten: int
    version: str


def _refuse(message: str) -> safety.Refusal:
    return safety.Refusal("SEED_UNSUPPORTED", message)


def _check_owned_dir(path: Path, what: str) -> None:
    """The same ownership rules as the warm cache, minus the mode: not a symlink, a directory, our own uid."""
    if path.is_symlink():
        raise _refuse(f"{what} {path} is a symlink; pass the directory it points to.")
    if not path.is_dir():
        raise _refuse(f"{what} {path} is not a directory.")
    if path.lstat().st_uid != os.getuid():
        raise _refuse(f"{what} {path} belongs to another user.")


def _check_links(install: Path) -> None:
    """A link inside the source must not lead back into it by absolute path, nor climb out of its install dir by a
    relative one: either would make the copy use (and lease) the user's own tree instead of PG's."""
    roots = {install.parent.absolute(), install.parent.resolve()}
    for path in install.rglob("*"):
        if not path.is_symlink():
            continue
        target = os.readlink(path)
        if os.path.isabs(target):
            if any(Path(os.path.normpath(target)).is_relative_to(r) or Path(target).resolve().is_relative_to(r)
                   for r in roots):
                raise _refuse(f"{path} links back into the source by absolute path; this runtime cannot be moved.")
        elif not Path(os.path.normpath(path.parent.absolute() / target)).is_relative_to(install.absolute()):
            raise _refuse(f"{path} is a relative link that leaves {install.name}; this runtime cannot be moved.")


def find_installs(source: Path) -> list[Path]:
    """The install dirs under *source* (each holds facts.json); refuses anything else."""
    _check_owned_dir(source, "source")
    installs = sorted(p for p in source.iterdir() if (p / "facts.json").is_file() and not p.is_symlink())
    if not installs:
        raise _refuse(f"{source} holds no Hermes install (no <id>/facts.json); pass your Hermes home's installs dir.")
    for install in installs:
        _check_owned_dir(install, "install")
        _check_links(install)
    return installs


def _ignore_leases(directory: str, names: list[str]) -> set[str]:
    return set(names) if Path(directory).name in SKIPPED_DIRS else set()


def _rewrite_paths(tree: Path, old: str, new: str) -> int:
    """Point the copy's own records (facts.json and the like) at its new location; returns the files changed."""
    changed = 0
    for path in tree.rglob("*"):
        if path.is_symlink() or not path.is_file() or path.suffix not in REWRITE_SUFFIXES:
            continue
        if path.stat().st_size > REWRITE_MAX_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if old in text:
            path.write_text(text.replace(old, new), encoding="utf-8")
            changed += 1
    return changed


def _copy_installs(installs: list[Path], cache: Path) -> int:
    """Copy into a private staging dir next to the cache, fix the paths, then move each install into place."""
    staging = Path(tempfile.mkdtemp(prefix="provingground-seed-", dir=cache.parent))
    rewritten = 0
    try:
        for install in installs:
            shutil.copytree(install, staging / install.name, symlinks=True, ignore=_ignore_leases)
            rewritten += _rewrite_paths(staging / install.name, str(install.resolve()), str(cache / install.name))
        for install in installs:
            os.rename(staging / install.name, cache / install.name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return rewritten


def _validate(settings: runner.RunnerSettings) -> str:
    """`hermes --version` in a throwaway home whose installs is the seeded cache, with the network pointed at a
    closed port: a copy Hermes cannot use fails here instead of in the first drill."""
    out = runner._run_throwaway(settings, [str(settings.hermes_bin), "--version"])
    version = runner._parse_version(out.stdout)
    if out.returncode != 0 or not runner.VERSION_RE.search(out.stdout):
        detail = " ".join((out.stderr or out.stdout).split())[-300:]
        raise safety.Refusal("WARMUP_FAILED", f"the copied runtime does not start ({detail or 'no output'}); it was "
                             "removed. Run `hermes provingground warm` to download it instead.")
    return version


def seed_cache(settings: runner.RunnerSettings, source: Path,
               validate: Callable[[runner.RunnerSettings], str] = _validate) -> SeedResult:
    """Copy *source* (an installs dir) into the private warm cache, check it starts, then mark the cache warm."""
    installs = find_installs(source.expanduser())
    runner.ensure_private_cache(settings.warm_cache)
    if any(settings.warm_cache.iterdir()):
        raise safety.Refusal("SEED_CONFLICT", f"the runtime cache {settings.warm_cache} already has contents but is "
                             "not marked ready; remove that directory and rerun.")
    rewritten = _copy_installs(installs, settings.warm_cache)
    try:
        version = validate(settings)
    except BaseException:
        for install in installs:
            shutil.rmtree(settings.warm_cache / install.name, ignore_errors=True)
        raise
    (settings.warm_cache / runner.WARM_MARKER).write_text(str(time.time()), encoding="utf-8")
    return SeedResult(tuple(i.name for i in installs), rewritten, version)
