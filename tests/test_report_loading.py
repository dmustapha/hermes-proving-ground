# File: tests/test_report_loading.py
# Saved and exported reports: a report from a newer version still loads, exports never carry this machine's home or
# temp paths, N/A cells link nowhere they cannot go, and FULL STACK is never the column a narrow terminal crops.
from __future__ import annotations

import io
import json
import re
import tempfile
from dataclasses import replace
from pathlib import Path

from helpers import fixture_like_matrix
from rich.console import Console

from proving_ground import cli, matrix, report

ROOT = Path(__file__).resolve().parent.parent


def test_generator_matches_the_manifest_version() -> None:
    version = re.search(r'^version: "([^"]+)"', (ROOT / "plugin.yaml").read_text(), re.M).group(1)
    assert report.GENERATOR == f"proving-ground {version}"
    assert report.matrix_to_dict(fixture_like_matrix())["generator"] == report.GENERATOR


def test_a_report_with_fields_from_a_newer_version_still_loads() -> None:
    data = report.matrix_to_dict(fixture_like_matrix())
    data["conditions"]["future_field"] = 1
    data["calls"][0]["future_field"] = "x"
    first_column = next(iter(data["observations"]))
    next(iter(data["observations"][first_column].values()))["future_field"] = []
    back = report.matrix_from_dict(json.loads(json.dumps(data)))
    assert back.cells == fixture_like_matrix().cells


def _with_private_paths(m: matrix.Matrix) -> matrix.Matrix:
    home, temp = str(Path.home()), tempfile.gettempdir()
    obs = m.observations["stack"]["TC04"]
    leaky = replace(obs, error_message=f"Traceback: File \"{home}/.hermes/x.py\" and {temp}/provingground-cache/a")
    return replace(m, observations={**m.observations, "stack": {**m.observations["stack"], "TC04": leaky}})


def test_saved_and_exported_reports_carry_no_home_or_temp_paths(tmp_path) -> None:
    m = _with_private_paths(fixture_like_matrix())
    targets = {"json_out": tmp_path / "r.json", "markdown_out": tmp_path / "r.md", "html_out": tmp_path / "r.html"}
    cli.write_exports(m, __import__("argparse").Namespace(**{k: str(v) for k, v in targets.items()}))
    saved = cli.save(m).read_text()
    for text in [saved] + [p.read_text() for p in targets.values()]:
        assert str(Path.home()) not in text and tempfile.gettempdir() not in text
    assert "~/.hermes/x.py" in saved and "{TMP}/provingground-cache/a" in saved


def test_scrub_matches_whole_paths_only() -> None:
    rules = report.path_rules({"/Users/al": "~"})
    assert report.scrub_paths(["/Users/al/x", "/Users/alice/x", "/Users/al"], rules) == ["~/x", "/Users/alice/x", "~"]


def test_not_applicable_cells_are_not_dead_links() -> None:
    base = fixture_like_matrix()
    na = {(f"solo:fx_needs_key", c.id): matrix.Cell("solo:fx_needs_key", c.id, (matrix.Outcome.NOT_APPLICABLE,))
          for c in base.calls}
    m = replace(base, columns=base.columns + ("solo:fx_needs_key",), cells={**base.cells, **na})
    page = report.render_html(m)
    anchors = set(re.findall(r'href="#(cell-[^"]+)"', page))
    panels = set(re.findall(r'id="(cell-[^"]+)"', page))
    assert anchors and anchors <= panels
    assert '<span class="NOT_APPLICABLE">N/A</span>' in page


def test_full_stack_is_never_the_cropped_column_on_a_wide_stack() -> None:
    m = fixture_like_matrix()
    solos = [f"solo:plugin_number_{i}" for i in range(6)]
    columns = ["baseline", *solos, "stack"]
    cells = {(col, c.id): m.cells[("stack", c.id)] for col in columns for c in m.calls}
    assert report.display_order(columns, 120)[:2] == ["baseline", "stack"]
    assert report.display_order(["baseline", "solo:a", "stack"], 120) == ["baseline", "solo:a", "stack"]
    buffer = io.StringIO()
    report._print_table(Console(file=buffer, width=120, force_terminal=False), m.calls, columns, cells)
    lines = buffer.getvalue().splitlines()
    full = next(x for x in lines if "FULL STACK" in x)  # shown whole, to the left of every solo column
    assert full.index("FULL STACK") < next(x for x in lines if "plugin_number_" in x).index("plugin_number_")
