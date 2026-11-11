# Debug P2 (perspective point d): PG's computed load order mirrors Hermes's layered resolver, the child reports the
# order it really loaded, and a mismatch becomes a report note instead of silently drifting.
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from helpers import column, fixture_like_matrix

from proving_ground import injector, matrix, report, runner
from proving_ground.observer import EventLog


def _keys(sources) -> list[str]:
    return [s.key for s in sources]


def _src(key: str, *requires: str) -> runner.PluginSource:
    return runner.PluginSource(key, Path("/x") / key, requires=requires)


def test_order_is_layered_like_hermes_not_depth_first() -> None:
    # Hermes (plugins_manifest.resolve_plugin_load_order): layer 1 = b, c, d (sorted); layer 2 = a.
    # The depth-first mirror PG used before gave d, a, b, c (1516 of 3000 random graphs disagreed; now 0).
    assert _keys(runner.load_order([_src("a", "d"), _src("b"), _src("c"), _src("d")])) == ["b", "c", "d", "a"]


def test_cycle_falls_back_to_alphabetical_for_all() -> None:
    assert _keys(runner.load_order([_src("c"), _src("b", "a"), _src("a", "b")])) == ["a", "b", "c"]


def test_requires_accepts_ids_and_mappings(tmp_path: Path) -> None:
    for key, body in (("base", ""), ("addon", "requires_plugins:\n  - id: zbase\n  - base\n"), ("zbase", "")):
        (tmp_path / "plugins" / key).mkdir(parents=True)
        (tmp_path / "plugins" / key / "plugin.yaml").write_text(f"name: {key}\n{body}", encoding="utf-8")
    subjects, _skipped = runner.discover_subjects(tmp_path, {"plugins": {"enabled": ["addon", "base", "zbase"]}})
    assert next(s for s in subjects if s.key == "addon").requires == ("zbase", "base")
    assert _keys(subjects) == ["base", "zbase", "addon"]


def test_child_reports_its_real_load_order(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(injector, "_completion", lambda body: body)
    (tmp_path / "plan.json").write_text(json.dumps({"sandbox": str(tmp_path), "subjects": ["fx_b", "fx_a"],
                                                    "calls": []}), encoding="utf-8")
    log = EventLog(tmp_path / "events.jsonl")
    order = lambda: ["fx_b", "proving-ground", "fx_a"]  # noqa: E731
    injector.make_llm_execution(tmp_path, log, lambda _k: True, order)(request={"messages": []}, next_call=None)
    loaded = next(json.loads(x) for x in (tmp_path / "events.jsonl").read_text().splitlines() if '"plugins_loaded"' in x)
    assert loaded["order"] == ["fx_b", "fx_a"]


def test_unknown_child_order_is_none_not_a_mismatch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(injector, "_completion", lambda body: body)
    (tmp_path / "plan.json").write_text(json.dumps({"sandbox": str(tmp_path), "subjects": ["fx_a"], "calls": []}))

    def broken() -> list[str]:
        raise LookupError("no registry")

    log = EventLog(tmp_path / "events.jsonl")
    injector.make_llm_execution(tmp_path, log, lambda _k: True, broken)(request={"messages": []}, next_call=None)
    assert '"order": null' in (tmp_path / "events.jsonl").read_text()


def _with_child_order(col: matrix.ColumnRun, order: tuple[str, ...] | None) -> matrix.ColumnRun:
    return replace(col, child_order=order)


def test_matching_order_adds_no_note_and_mismatch_adds_one() -> None:
    stack = column("stack", ("fx_guard", "fx_rewriter"), {})
    same = _with_child_order(stack, ("fx_guard", "fx_rewriter"))
    swapped = _with_child_order(stack, ("fx_rewriter", "fx_guard"))
    assert matrix.order_note([same, _with_child_order(stack, None)]) == ""
    note = matrix.order_note([swapped])
    assert note.startswith("stack: the child loaded fx_rewriter, fx_guard, PROVING GROUND computed fx_guard, fx_rewriter")


def test_mismatch_note_reaches_the_report() -> None:
    m = fixture_like_matrix()
    noted = replace(m, conditions=replace(m.conditions, order_note="stack: the child loaded b, a"))
    assert "load order mismatch: stack: the child loaded b, a" in report.conditions_lines(noted)
    assert not any(line.startswith("load order mismatch") for line in report.conditions_lines(m))
