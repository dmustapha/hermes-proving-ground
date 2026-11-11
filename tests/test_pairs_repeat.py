# --pairs names the smallest set observed to reproduce an effect seen only in the full stack; --repeat N marks solo
# cells whose runs disagree UNSTABLE. Both are opt-in: without them the matrix is exactly the default one.
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

import pytest

from helpers import column, fixture_like_matrix
from proving_ground import cli, matrix, report, runner
from proving_ground.matrix import Outcome

TRIO = ("fx_a", "fx_b", "fx_c")
CHANGED = {"TC01": {"tool_out": "changed only in combination\n"}}


def stack_cols(plugins: Sequence[str] = TRIO) -> list[matrix.ColumnRun]:
    return [column("baseline", (), {})] + [column(f"solo:{k}", (k,), {}) for k in plugins] + \
        [column("stack", tuple(plugins), CHANGED)]


def with_pairs(pairs: dict[str, dict], total: int, plugins: Sequence[str] = TRIO,
               loaded: dict[str, Sequence[str]] | None = None) -> matrix.Matrix:
    cols = stack_cols(plugins) + [column(f"pair:{name}", tuple(name.split("+")), effects,
                                         loaded=(loaded or {}).get(name)) for name, effects in pairs.items()]
    return matrix.build_matrix(cols, None, {}, infos(), "v", "t", 1.0, load_order=list(plugins), pair_total=total)


def infos() -> list[matrix.CallInfo]:
    return [matrix.CallInfo(i, "terminal", f"subject {i}", f"purpose {i}") for i in ("TC01", "TC02", "TC03", "TC04",
                                                                                     "TC05")]


def interaction_findings(m: matrix.Matrix) -> list[matrix.Finding]:
    return [f for f in m.findings if f.kind == "STACK_ONLY_INTERACTION"]


def test_the_pair_that_reproduces_a_stack_only_effect_is_named() -> None:
    m = with_pairs({"fx_a+fx_b": CHANGED, "fx_a+fx_c": {}, "fx_b+fx_c": {}}, total=3)
    [finding] = interaction_findings(m)
    assert finding.call_id == "TC01" and finding.plugins == ("fx_a", "fx_b")
    assert "smallest set observed to reproduce it is a pair: fx_a + fx_b" in finding.text
    assert m.cell("pair:fx_a+fx_b", "TC01").primary is Outcome.RESULT_CHANGED


def test_no_reproducing_pair_among_all_pairs_says_it_needs_three_or_more() -> None:
    m = with_pairs({"fx_a+fx_b": {}, "fx_a+fx_c": {}, "fx_b+fx_c": {}}, total=3)
    [finding] = interaction_findings(m)
    assert finding.plugins == () and "needs 3+ plugins together" in finding.text


def test_a_capped_pair_search_says_so_instead_of_claiming_three_or_more() -> None:
    m = with_pairs({"fx_a+fx_b": {}}, total=15)
    [finding] = interaction_findings(m)
    assert "capped at 10" in finding.text and "3+ plugins" in finding.text and "needs 3+" not in finding.text


def test_a_pair_that_did_not_load_is_not_evidence_either_way() -> None:
    m = with_pairs({"fx_a+fx_b": CHANGED, "fx_a+fx_c": {}, "fx_b+fx_c": {}}, total=3, loaded={"fx_a+fx_b": ["fx_a"]})
    assert m.cell("pair:fx_a+fx_b", "TC01").primary is Outcome.LOAD_FAILED
    [finding] = interaction_findings(m)
    assert finding.plugins == () and "None of the 2 pair columns" in finding.text


def test_a_two_plugin_stack_is_its_own_smallest_set() -> None:
    m = matrix.build_matrix(stack_cols(("fx_a", "fx_b")), None, {}, infos(), "v", "t", 1.0,
                            load_order=["fx_a", "fx_b"], pair_total=1)
    [finding] = interaction_findings(m)
    assert finding.plugins == ("fx_a", "fx_b") and "smallest set that reproduces it is these two" in finding.text


def test_without_pairs_there_is_no_pair_finding_and_the_interaction_stays_unnamed() -> None:
    m = matrix.build_matrix(stack_cols(), None, {}, infos(), "v", "t", 1.0, load_order=list(TRIO))
    assert interaction_findings(m) == []
    assert any(a.kind == "interaction" and a.call_id == "TC01" for a in m.attributions)


def test_pair_columns_render_with_titles_and_provenance() -> None:
    m = with_pairs({"fx_a+fx_b": CHANGED}, total=3)
    assert report.column_title("pair:fx_a+fx_b") == "fx_a + fx_b" and report.provenance("pair:fx_a+fx_b") == "pair"
    assert "TC01 --column pair:fx_a+fx_b" in "\n".join(report.notable_cells(m))
    for text in (report.render_markdown(m), report.render_html(m), report.render_cell_detail(m, "pair:fx_a+fx_b", "TC01")):
        assert "fx_a + fx_b" in text and report.forbidden_claims(text) == []


class FakeRun:
    def __init__(self, spec: runner.ColumnSpec) -> None:
        self.spec = spec


def fake_parallel(seen: list[runner.ColumnSpec]) -> Any:
    def parallel(specs, _work, _jobs, _on_done=None):
        seen.extend(specs)
        return [FakeRun(s) for s in specs]
    return parallel


def sources(keys: Sequence[str]) -> list[runner.PluginSource]:
    return [runner.PluginSource(k, Path("/nonexistent") / k) for k in keys]


def test_pair_search_is_capped_and_the_note_says_so(monkeypatch) -> None:
    seen: list[runner.ColumnSpec] = []
    monkeypatch.setattr(runner, "_parallel", fake_parallel(seen))
    monkeypatch.setattr(runner, "_to_column", lambda run: run.spec.name)
    six = [f"fx_{c}" for c in "abcdef"]
    view = matrix.build_matrix(stack_cols(six), None, {}, infos(), "", "", 0.0, load_order=six)
    runs, total, note = runner._run_pairs(view, sources(six), lambda s: s, runner.RunnerSettings(*[Path("/x")] * 4))
    assert total == 15 and len(seen) == matrix.PAIR_CAP == len(runs)
    assert seen[0].name == "pair:fx_a+fx_b" and "capped at 10 of 15 pairs" in note


def test_no_stack_only_effect_runs_no_pair_columns(monkeypatch) -> None:
    seen: list[runner.ColumnSpec] = []
    monkeypatch.setattr(runner, "_parallel", fake_parallel(seen))
    m = fixture_like_matrix()
    runs, total, note = runner._run_pairs(m, sources(("fx_guard", "fx_rewriter", "fx_trimmer")), lambda s: s,
                                          runner.RunnerSettings(*[Path("/x")] * 4))
    assert (runs, total, seen) == ([], 3, []) and "no pair columns were run" in note


def test_a_pair_column_carries_both_plugins_and_their_dependencies() -> None:
    dep = runner.PluginSource("fx_dep", Path("/x/fx_dep"))
    a = runner.PluginSource("fx_a", Path("/x/fx_a"), requires=("fx_dep",))
    b = runner.PluginSource("fx_b", Path("/x/fx_b"))
    spec = runner._pair_spec(a, b, [dep, a, b])
    # Hermes's order: layers with no unmet requires_plugins first, each layer alphabetical
    assert spec.name == "pair:fx_a+fx_b" and [s.key for s in spec.plugins] == ["fx_b", "fx_dep", "fx_a"]


GUARD_BLOCK = {"TC02": {"block": "fx_guard: command carries the guard marker"}}


def guard_matrix(repeat_effects: Sequence[dict], aborted: bool = False) -> matrix.Matrix:
    cols = [column("baseline", (), {}), column("solo:fx_guard", ("fx_guard",), GUARD_BLOCK),
            column("solo:fx_quiet", ("fx_quiet",), {}), column("stack", ("fx_guard", "fx_quiet"), GUARD_BLOCK)]
    runs = [column("solo:fx_guard", ("fx_guard",), effects) for effects in repeat_effects]
    if aborted:
        runs = [matrix.replace(r, abort_reason="CELL_TIMEOUT") for r in runs]
    return matrix.build_matrix(cols, None, {}, infos(), "v", "t", 1.0, load_order=["fx_guard", "fx_quiet"],
                               repeats={"solo:fx_guard": runs})


def test_solo_runs_that_disagree_mark_the_cell_unstable_with_every_label() -> None:
    m = guard_matrix([{}])
    cell = m.cell("solo:fx_guard", "TC02")
    assert cell.primary is Outcome.BLOCKED and cell.unstable and cell.varied == ("BLOCKED", "PASSED THROUGH")
    assert "BLOCKED in 1 of 2, PASSED THROUGH in 1 of 2" in cell.note
    assert report.cell_label(cell).endswith("UNSTABLE")
    [attribution] = [a for a in m.attributions if a.call_id == "TC02"]
    assert "UNSTABLE" in attribution.note and attribution.plugins == ("fx_guard",)


def test_solo_runs_that_agree_leave_the_cells_alone_and_count_the_runs() -> None:
    plain = guard_matrix([])
    again = guard_matrix([GUARD_BLOCK, GUARD_BLOCK])
    assert again.cells == plain.cells and again.findings == plain.findings
    [before] = [a for a in plain.attributions if a.call_id == "TC02"]
    [after] = [a for a in again.attributions if a.call_id == "TC02"]
    assert before.note.startswith("observed in one solo run: fx_guard")
    assert after.note.startswith("observed in all 3 solo runs: fx_guard") and after.plugins == before.plugins


def test_a_repeat_run_that_stopped_is_not_compared() -> None:
    assert not guard_matrix([{}], aborted=True).cell("solo:fx_guard", "TC02").varied


def test_only_solo_columns_with_a_plugin_effect_are_repeated(monkeypatch) -> None:
    m = guard_matrix([])
    assert matrix.effect_columns(m) == ["solo:fx_guard"]
    seen: list[runner.ColumnSpec] = []
    monkeypatch.setattr(runner, "_parallel", fake_parallel(seen))
    monkeypatch.setattr(runner, "_to_column", lambda run: run.spec.name)
    specs = [runner.ColumnSpec(n, ()) for n in ("baseline", "solo:fx_guard", "solo:fx_quiet", "stack")]
    settings = runner.RunnerSettings(*[Path("/x")] * 4, repeat=3)
    repeats = runner._run_repeats(m, specs, lambda s: s, settings)
    assert [s.name for s in seen] == ["solo:fx_guard", "solo:fx_guard"] and list(repeats) == ["solo:fx_guard"]


def test_an_effect_seen_only_in_the_stack_repeats_every_solo_column() -> None:
    """A stateful plugin quiet in its one solo run looks exactly like an interaction, so every solo runs again."""
    m = matrix.build_matrix(stack_cols(), None, {}, infos(), "v", "t", 1.0, load_order=list(TRIO))
    assert matrix.effect_columns(m) == []
    assert matrix.repeat_columns(m) == ["solo:fx_a", "solo:fx_b", "solo:fx_c"]
    assert matrix.repeat_columns(guard_matrix([])) == ["solo:fx_guard"]


def test_an_interaction_whose_solo_runs_disagree_says_it_can_change() -> None:
    cols = stack_cols()
    runs = {"solo:fx_a": [column("solo:fx_a", ("fx_a",), CHANGED)]}
    m = matrix.build_matrix(cols, None, {}, infos(), "v", "t", 1.0, load_order=list(TRIO), repeats=runs)
    assert m.cell("solo:fx_a", "TC01").varied == ("PASSED THROUGH", "RESULT CHANGED")
    [attribution] = [a for a in m.attributions if a.call_id == "TC01"]
    assert attribution.kind == "interaction" and "repeated solo runs of fx_a disagree on TC01" in attribution.note


def test_core_only_solo_columns_are_not_repeated() -> None:
    m = fixture_like_matrix()
    assert set(matrix.effect_columns(m)) == {"solo:fx_guard", "solo:fx_rewriter", "solo:fx_trimmer"}
    quiet = matrix.build_matrix([column("baseline", (), {}), column("solo:fx_q", ("fx_q",), {})], None, {}, infos(),
                                "v", "t", 1.0)
    assert matrix.effect_columns(quiet) == []  # its TC05 cell is RESULT CHANGED (core) only


@pytest.mark.parametrize("value", [0, 6, -1])
def test_repeat_outside_one_to_five_is_refused(value: int) -> None:
    with pytest.raises(cli.safety.Refusal) as refused:
        cli.repeat_count(argparse.Namespace(repeat=value))
    assert refused.value.code == "BAD_OPTION"


def test_repeat_defaults_to_one_and_the_parser_takes_the_new_flags() -> None:
    assert cli.repeat_count(argparse.Namespace()) == 1
    parser = argparse.ArgumentParser()
    cli.setup_parser(parser)
    args = parser.parse_args(["--fixtures", "--pairs", "--repeat", "3", "--all-wires"])
    assert (args.pairs, args.repeat, args.all_wires) == (True, 3, True)
    defaults = parser.parse_args([])
    assert (defaults.pairs, defaults.repeat, defaults.all_wires) == (False, 1, False)


def test_unstable_cells_round_trip_and_default_cells_keep_their_keys() -> None:
    m = guard_matrix([{}])
    data = report.matrix_to_dict(m)
    again = report.matrix_from_dict(json.loads(json.dumps(data)))
    assert again.cell("solo:fx_guard", "TC02").varied == ("BLOCKED", "PASSED THROUGH")
    plain = report.matrix_to_dict(fixture_like_matrix())
    assert all("varied" not in c for c in plain["cells"])


def test_repeat_and_pair_notes_never_grade() -> None:
    drill = runner.DrillRun([], None, {}, [], [], "", 0.0, repeats={"solo:fx_guard": []}, repeat=2,
                            pair_total=3, pair_note="3 pair columns run for 1 effect seen only in the full stack")
    m = guard_matrix([{}])
    options = cli.drill_options(drill, infos(), m)
    assert options["repeat"].startswith("2 solo runs each of fx_guard") and "1 cell disagrees" in options["repeat"]
    assert report.forbidden_claims("\n".join(options.values())) == []
    quiet = runner.DrillRun([], None, {}, [], [], "", 0.0, repeat=2)
    assert "none was run again" in cli.drill_options(quiet, infos(), m)["repeat"]
    assert cli.drill_options(runner.DrillRun([], None, {}, [], [], "", 0.0), infos(), m) == {}
