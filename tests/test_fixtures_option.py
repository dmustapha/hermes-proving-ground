# File: tests/test_fixtures_option.py
# `--fixtures` picks a bundled stack: bare it is the three-plugin demo stack (the golden matrix), `--fixtures
# extended` adds every plugin under fixtures/extended. --without is checked against the stack that was picked.
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from proving_ground import cli, runner, safety

ROOT = Path(__file__).resolve().parent.parent


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    cli.setup_parser(parser)
    return parser.parse_args(argv)


@pytest.mark.parametrize("argv, expected", [(["--fixtures"], "demo"), (["--fixtures", "demo"], "demo"),
                                            (["--fixtures", "extended"], "extended"), ([], None),
                                            (["--fixtures", "--html", "x.html"], "demo"),
                                            (["show", "TC04", "--fixtures"], "demo")])
def test_fixtures_flag_parses_bare_and_with_a_stack_name(argv, expected) -> None:
    assert parse(argv).fixtures == expected


def test_demo_usage_with_an_export_still_parses() -> None:
    args = parse(["--fixtures", "--html", "x.html"])
    assert (args.action, args.fixtures, args.html_out) == ("run", "demo", "x.html")


def test_unknown_stack_name_is_an_argparse_error() -> None:
    with pytest.raises(SystemExit):
        parse(["--fixtures", "everything"])


def test_default_fixture_stack_is_unchanged() -> None:
    assert [s.key for s in runner.fixture_subjects(ROOT)] == list(safety.FIXTURE_KEYS)


def test_extended_stack_adds_the_redactor_in_load_order() -> None:
    keys = [s.key for s in runner.fixture_subjects(ROOT, extended=True)]
    assert keys == ["fx_guard", "fx_redactor", "fx_rewriter", "fx_trimmer"]
    assert [s.key for s in runner.fixture_subjects(ROOT, ("fx_redactor",), extended=True)] == list(safety.FIXTURE_KEYS)


def test_without_is_checked_against_the_picked_stack(tmp_path) -> None:
    settings = runner.RunnerSettings(Path("/bin/sh"), ROOT, tmp_path / "cache", tmp_path)
    picked, _skipped = cli.select_subjects(argparse.Namespace(fixtures="extended", without=["fx_redactor"]), settings)
    assert [s.key for s in picked] == list(safety.FIXTURE_KEYS)
    with pytest.raises(safety.Refusal) as refused:
        cli.select_subjects(argparse.Namespace(fixtures="demo", without=["fx_redactor"]), settings)
    assert refused.value.code == "UNKNOWN_PLUGIN"


@pytest.mark.parametrize("raw, expected", [("fixtures", True), ("fixtures extended", "extended"), ("", False)])
def test_slash_keeps_a_truthy_fixtures_value_and_accepts_extended(raw, expected) -> None:
    assert cli._slash_args(raw).fixtures == expected
