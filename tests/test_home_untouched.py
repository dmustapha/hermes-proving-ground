# File: tests/test_home_untouched.py
# I7: the measured parts of the real Hermes home (WATCHED below) are unchanged by a full drill. This is the whole
# public claim (CL-17); logs/, plugin-data/proving-ground and other Hermes-owned files are not claimed.
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from proving_ground import runner, testcalls

REAL_HOME = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
WATCHED = ("config.yaml", "state.db", "state.db-wal", "SOUL.md", "memories", "sessions", "plugins")
EXCLUDED = ("plugin-data/proving-ground",)


def digest(home: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in WATCHED:
        target = home / name
        files = [target] if target.is_file() else sorted(p for p in target.rglob("*") if p.is_file()) if target.is_dir() else []
        for path in files:
            if not any(str(path).startswith(str(home / ex)) for ex in EXCLUDED):
                out[str(path.relative_to(home))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


@pytest.mark.hermes
@pytest.mark.skipif(not REAL_HOME.is_dir(), reason="no real Hermes home on this machine")
def test_real_home_unchanged_by_drill(settings) -> None:
    before = digest(REAL_HOME)
    runner.run_drill(runner.fixture_subjects(settings.pg_source), testcalls.load_templates(), settings)
    assert digest(REAL_HOME) == before
