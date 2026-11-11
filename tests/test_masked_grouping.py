# When one plugin blocks a call in the full stack, the other plugins' solo effects on
# that call are ONE grouped MASKED_IN_STACK finding under the blocking cause (one per blocker x call), not a flood.
from __future__ import annotations

import json

from helpers import LOG, LONG, column

from proving_ground import matrix

IDS = ("TC01", "TC02", "TC03", "TC04", "TC05")
INFOS = [matrix.CallInfo(i, "terminal", i, i) for i in IDS]
BLOCK = {"block": "fx_guard: read-only session"}


def _result(output: str) -> str:
    return json.dumps({"output": output, "exit_code": 0, "error": None})


def _matrix() -> matrix.Matrix:
    guard = {"TC05": BLOCK}
    compactor = {"TC05": {"tool_out": "[compacted]"}, "TC04": {"tool_out": LOG.replace("FAILED", "")}}
    rewriter = {"TC05": {"final_command": "seq 1 5", "terminal_out": "1\n2\n3\n4\n5\n"}}
    redactor = {"TC05": {"delivered": _result(LONG[:10])}}
    cols = [column("baseline", (), {}), column("solo:fx_compactor", ("fx_compactor",), compactor),
            column("solo:fx_guard", ("fx_guard",), guard), column("solo:fx_redactor", ("fx_redactor",), redactor),
            column("solo:fx_rewriter", ("fx_rewriter",), rewriter),
            column("stack", ("fx_compactor", "fx_guard", "fx_redactor", "fx_rewriter"), guard)]
    return matrix.build_matrix(cols, None, {}, INFOS, "v", "t", 1.0,
                               load_order=("fx_compactor", "fx_guard", "fx_redactor", "fx_rewriter"))


def test_one_grouped_finding_per_blocker_and_call() -> None:
    masked = [f for f in _matrix().findings if f.kind == "MASKED_IN_STACK" and f.call_id == "TC05"]
    assert len(masked) == 1, masked
    finding = masked[0]
    assert finding.plugins == ("fx_guard", "fx_compactor", "fx_redactor", "fx_rewriter")
    assert finding.text.startswith("fx_guard BLOCKED TC05 in the full stack")
    for name in ("fx_compactor RESULT CHANGED", "fx_redactor RESULT CHANGED", "fx_rewriter REWROTE"):
        assert name in finding.text


def test_masking_without_a_blocking_cause_stays_per_plugin() -> None:
    masked = [f for f in _matrix().findings if f.kind == "MASKED_IN_STACK" and f.call_id == "TC04"]
    assert [(f.plugins, f.text) for f in masked] == [
        (("fx_compactor",), "fx_compactor RESULT CHANGED TC04 on its own; in the full stack that effect does not "
                            "reach the model.")]


def test_findings_count_is_one_per_cause() -> None:
    m = _matrix()
    assert len([f for f in m.findings if f.kind == "MASKED_IN_STACK"]) == 2
