# File: tests/test_untrusted_inputs.py
# Data the drill does not author (child event lines, plugin manifests, plugin settings) cannot stop the drill or
# leak a secret, and one column's unexpected error is that column's result, not the end of the drill.
from __future__ import annotations

import json
import subprocess

import pytest

from proving_ground import observer, runner, safety, testcalls


def test_event_lines_of_the_wrong_shape_are_skipped(tmp_path) -> None:
    state = tmp_path / "home" / safety.STATE_DIRNAME
    state.mkdir(parents=True)
    good = {"t": 2.0, "ev": "done"}
    lines = ["[1, 2]", "7", '"text"', '{"t": "soon", "ev": "x"}', '{"t": 1.0, "ev": 5}', '{"t": true, "ev": "x"}',
             "{torn", json.dumps({"t": 1.0, "ev": "activated"}), json.dumps(good)]
    (state / "events.jsonl").write_text("\n".join(lines) + "\n")
    events = runner.read_events(tmp_path / "home")
    assert [e["ev"] for e in events] == ["activated", "done"]
    assert runner.abort_reason_from(events, None) is None


@pytest.mark.parametrize("text, env, requires", [
    ("- a\n- b\n", (), ()),
    ("requires_env: FOO\nrequires_plugins: bar\n", (), ()),
    ("requires_env: [{name: X}, {nope: 1}, '', 3, KEY]\nrequires_plugins: [{id: dep}, {}, 4]\n", ("X", "KEY"), ("dep",)),
])
def test_plugin_manifests_are_read_as_data(tmp_path, text, env, requires) -> None:
    (tmp_path / "plugin.yaml").write_text(text)
    manifest = runner._manifest(tmp_path)
    assert runner._required_env(manifest) == env and runner._requires(manifest) == requires


def test_non_mapping_plugin_settings_are_ignored(tmp_path) -> None:
    (tmp_path / "plugins" / "p").mkdir(parents=True)
    (tmp_path / "plugins" / "p" / "plugin.yaml").write_text("name: p\n")
    config = {"plugins": {"enabled": ["p"], "entries": {"p": ["not", "a", "mapping"]}}}
    subjects, _skipped = runner.discover_subjects(tmp_path, config)
    assert subjects[0].settings is None


def test_secrets_nested_in_lists_and_token_shaped_values_are_withheld() -> None:
    dropped: list[str] = []
    settings = {"hosts": [{"api_key": "abc", "name": "h"}], "note": "sk-" + "a" * 24, "plain": "hello"}
    out = runner.redact_entries(settings, dropped, "p")
    assert out == {"hosts": [{"name": "h"}], "note": "[withheld by PROVING GROUND]", "plain": "hello"}
    assert len(dropped) == 2


def test_a_column_error_is_that_columns_result(monkeypatch, settings) -> None:
    def broken(*_a, **_k):
        raise PermissionError("unreadable plugin file")

    monkeypatch.setattr(runner, "build_child_home", broken)
    template = testcalls.load_templates()[:2]
    with runner.Recorder() as recorder:
        run = runner.run_column(runner.ColumnSpec("solo:x", ()), template, "PGCANARY-x", recorder, settings)
    assert run.abort_reason == "COLUMN_ERROR:PermissionError" and "unreadable" in run.output_tail
    column = runner._to_column(run)
    assert {o.ran for o in column.observations.values()} == {False}
    assert not any(p.name.startswith(safety.ROOT_PREFIX) and safety.read_owner_marker(p) and
                   safety.read_owner_marker(p)[0] == __import__("os").getpid()
                   for p in settings.temp_base.glob(safety.ROOT_PREFIX + "*"))


def test_a_refusal_inside_a_column_still_stops_the_drill(monkeypatch, settings) -> None:
    def refused(*_a, **_k):
        raise safety.Refusal("SANDBOX_UNSUPPORTED", "warm cache is a symlink")

    monkeypatch.setattr(runner, "build_child_home", refused)
    with runner.Recorder() as recorder, pytest.raises(safety.Refusal):
        runner.run_column(runner.ColumnSpec("solo:x", ()), [], "PGCANARY-x", recorder, settings)


def test_a_root_that_cannot_be_marked_is_removed(monkeypatch, settings) -> None:
    created = []
    real_mkdtemp = runner.tempfile.mkdtemp

    def spy(*a, **k):
        created.append(real_mkdtemp(*a, **k))
        return created[-1]

    def no_marker(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(runner.tempfile, "mkdtemp", spy)
    monkeypatch.setattr(safety, "write_owner_marker", no_marker)
    with pytest.raises(OSError):
        runner.create_drill_root(settings.temp_base)
    assert created and not __import__("pathlib").Path(created[0]).exists()


def test_an_unresponsive_hermes_is_a_refusal(monkeypatch, settings) -> None:
    def hang(*_a, **_k):
        raise subprocess.TimeoutExpired("hermes", 60)

    monkeypatch.setattr(runner.subprocess, "run", hang)
    monkeypatch.setattr(runner, "ensure_private_cache", lambda _c: None)
    with pytest.raises(safety.Refusal) as excinfo:
        runner._run_throwaway(settings, ["/x/hermes", "--version"])
    assert excinfo.value.code == "HERMES_UNRESPONSIVE"


def test_a_failed_event_write_never_raises(tmp_path) -> None:
    log = observer.EventLog(tmp_path / "missing-dir" / "events.jsonl")
    log.write("pre_tool_call", args={"x": 1})
    circular: dict = {}
    circular["self"] = circular
    observer.EventLog(tmp_path / "events.jsonl").write("post_tool_call", result=circular)


@pytest.mark.parametrize("entry", ["just text", {"id": "U01", "tool": "terminal", "subject": "s", "purpose": "p",
                                                 "args": ["not", "a", "mapping"]},
                                   {"id": "U01", "tool": "terminal", "subject": "s", "purpose": "p",
                                    "args": {"command": "echo"}, "seed": {1: "x"}}])
def test_malformed_user_call_entries_are_test_call_errors(entry) -> None:
    with pytest.raises(testcalls.TestCallError):
        testcalls._template_from(entry, testcalls.USER)
