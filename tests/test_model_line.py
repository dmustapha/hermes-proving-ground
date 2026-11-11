# The conditions line never describes a drill whose children answered no scripted step as a scripted run.
from __future__ import annotations

from proving_ground import matrix


def test_a_drill_with_scripted_steps_reports_them() -> None:
    assert matrix.model_line(61, 10) == ("NOT CALLED (scripted turn: 61 llm_execution steps answered by PROVING GROUND,"
                                         " 0 provider calls)")


def test_a_drill_whose_middleware_never_answered_says_no_scripted_turn_ran() -> None:
    line = matrix.model_line(0, 10)
    assert "no scripted turn ran" in line and "scripted turn:" not in line
