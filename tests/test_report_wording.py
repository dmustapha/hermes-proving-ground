# File: tests/test_report_wording.py
# I10: no report format ever claims the stack is safe, secure, protected or certified.
from __future__ import annotations

import json

from helpers import fixture_like_matrix

from proving_ground import report


def test_all_formats_free_of_forbidden_claims() -> None:
    m = fixture_like_matrix()
    rendered = [report.render_terminal(m, color=False), report.render_markdown(m), report.render_html(m),
                json.dumps(report.matrix_to_dict(m)), report.render_cell_detail(m, "stack", "TC04")]
    for text in rendered:
        assert report.forbidden_claims(text) == []
        assert "Observed on 5 synthetic inputs in a sandbox." in text or text.startswith("── cell")


def test_json_round_trip_is_lossless() -> None:
    m = fixture_like_matrix()
    again = report.matrix_from_dict(json.loads(json.dumps(report.matrix_to_dict(m))))
    assert report.matrix_to_dict(again) == report.matrix_to_dict(m)


def test_html_is_self_contained() -> None:
    page = report.render_html(fixture_like_matrix())
    assert "<script" not in page and "http://" not in page.split("<main>")[0]


def test_quoted_plugin_text_is_exempt_but_pg_text_is_checked() -> None:
    from dataclasses import replace

    m = fixture_like_matrix()
    obs = m.observations["stack"]["TC04"]
    noisy = {**m.observations, "stack": {**m.observations["stack"],
                                         "TC04": replace(obs, delivered="this command is safe and secure")}}
    m2 = replace(m, observations=noisy)
    for text in (report.render_cell_detail(m2, "stack", "TC04"), report.render_html(m2)):
        assert "safe and secure" in text
        assert report.forbidden_claims(text) == []
    assert report.forbidden_claims("PROVING GROUND says your stack is safe") != []


def test_every_cell_carries_provenance() -> None:
    data = report.matrix_to_dict(fixture_like_matrix())
    assert {c["provenance"] for c in data["cells"]} == {"baseline", "solo", "stack"}
    assert report.render_terminal(fixture_like_matrix(), color=False).count("model: NOT CALLED") == 1


def test_notable_cells_lists_effects() -> None:
    lines = report.notable_cells(fixture_like_matrix())
    text = "\n".join(lines)
    for call_id in ("TC02", "TC03", "TC04"):
        assert f"{call_id} --column stack" in text
    assert "TC01" not in text
    assert any(line.startswith("TC04 --column stack") and "RESULT CHANGED" in line and line.endswith("(fx_trimmer)")
               for line in lines)
    assert any(line.startswith("TC05 --column baseline") and line.endswith("RESULT CHANGED (core)") for line in lines)
    assert report.forbidden_claims(text) == []
