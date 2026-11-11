# File: tests/test_masked_needs_a_stack_result.py
# MASKED_IN_STACK says a solo effect "does not reach the model" in the full stack. That is only known when the
# stack cell produced a result: a stack cell that did not load, did not run, does not apply or errored proves nothing,
# so it never accuses a plugin.
from __future__ import annotations

import pytest

from proving_ground import matrix

O, C = matrix.Outcome, matrix.Cell
SOLOS = {"a": {"TC03": C("solo:a", "TC03", (O.REWROTE,))}, "b": {"TC03": C("solo:b", "TC03", (O.BLOCKED,))}}


@pytest.mark.parametrize("outcome", [O.LOAD_FAILED, O.NOT_RUN, O.NOT_APPLICABLE, O.ERRORED])
def test_no_masked_finding_without_a_stack_result(outcome) -> None:
    stack = {"TC03": C("stack", "TC03", (outcome,))}
    assert matrix._masked(stack, SOLOS, []) == []


def test_a_stack_result_still_reports_what_is_masked() -> None:
    stack = {"TC03": C("stack", "TC03", (O.PASSED_THROUGH,))}
    found = matrix._masked(stack, SOLOS, [])
    assert [(f.kind, f.plugins) for f in found] == [("MASKED_IN_STACK", ("a",)), ("MASKED_IN_STACK", ("b",))]


def test_a_stack_block_still_groups_the_masked_effects() -> None:
    stack = {"TC03": C("stack", "TC03", (O.BLOCKED,))}
    attributions = [matrix.Attribution("TC03", O.BLOCKED, "solo-match", ("b",))]
    found = matrix._masked(stack, SOLOS, attributions)
    assert len(found) == 1 and found[0].plugins == ("b", "a")
