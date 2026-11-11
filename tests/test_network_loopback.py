# File: tests/test_network_loopback.py
# I6: nothing PROVING GROUND generates leaves the machine.
from __future__ import annotations

from pathlib import Path

import pytest

from proving_ground import cli, runner, testcalls

ROOT = Path(__file__).resolve().parent.parent


def test_recorder_binds_loopback_only() -> None:
    with runner.Recorder() as recorder:
        assert recorder._server.server_address[0] == "127.0.0.1"


def test_no_all_interfaces_bind_in_source() -> None:
    for path in (ROOT / "proving_ground").glob("*.py"):
        assert "0.0.0.0" not in path.read_text(encoding="utf-8"), path.name


@pytest.mark.hermes
def test_fixture_drill_connects_only_to_loopback(settings) -> None:
    drill = runner.run_drill(runner.fixture_subjects(settings.pg_source), testcalls.load_templates(), settings)
    m = cli.assemble(drill, cli.call_infos(testcalls.load_templates()), "test", cli.now_iso())
    assert m.conditions.connects_non_loopback == 0
