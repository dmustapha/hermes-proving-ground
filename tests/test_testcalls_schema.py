# File: tests/test_testcalls_schema.py
# I9: only allow-listed tools; the loader rejects anything else.
from __future__ import annotations

from pathlib import Path

import pytest

from proving_ground import safety, testcalls


def test_catalog_uses_only_allowed_tools() -> None:
    templates = testcalls.load_templates()
    assert len(templates) == 10
    assert {t.tool for t in templates} <= safety.ALLOWED_TOOLS


def test_loader_rejects_unknown_tool(tmp_path: Path) -> None:
    bad = tmp_path / "testcalls.yaml"
    bad.write_text("schema: provingground.testcalls.v1\ncalls:\n  - {id: TCX, tool: execute_code, subject: s, "
                   "purpose: p, expect_baseline: PASSED_THROUGH, args: {code: x}}\n", encoding="utf-8")
    with pytest.raises(testcalls.TestCallError):
        testcalls.load_templates(bad)


def test_catalog_has_no_provider_shaped_tokens() -> None:
    text = (Path(testcalls.DEFAULT_CATALOG)).read_text(encoding="utf-8")
    for prefix in ("sk-", "ghp_", "AKIA", "xoxb-"):
        assert prefix not in text


def test_user_calls_use_the_same_validator_and_grammar(tmp_path: Path) -> None:
    good = tmp_path / "calls.yaml"
    good.write_text("schema: provingground.testcalls.v1\ncalls:\n  - {id: U01, tool: terminal, subject: my trigger, "
                    "purpose: p, args: {command: 'echo MY_GUARD_TRIGGER {CALL_ID}'}, expect: {stack: BLOCKED}}\n"
                    "expect:\n  TC03: {stack: [PASSED_THROUGH, BLOCKED]}\n", encoding="utf-8")
    templates = testcalls.load_user_calls(good, testcalls.load_templates())
    user = [t for t in templates if t.provenance == testcalls.USER]
    assert [t.id for t in user] == ["U01"] and user[0].expect == {"stack": ("BLOCKED",)}
    assert next(t for t in templates if t.id == "TC03").expect == {"stack": ("PASSED_THROUGH", "BLOCKED")}
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    ctx = testcalls.SandboxContext(sandbox, "http://127.0.0.1:1/tok", "PGCANARY-0")
    assert testcalls.render(user, ctx)[0].provenance == "user"


@pytest.mark.parametrize("call", ["{id: TC77, tool: terminal, subject: s, purpose: p, args: {command: 'echo x'}}",
                                  "{id: U02, tool: execute_code, subject: s, purpose: p, args: {code: x}}"])
def test_user_calls_reject_bad_ids_and_tools(tmp_path: Path, call: str) -> None:
    bad = tmp_path / "calls.yaml"
    bad.write_text(f"schema: provingground.testcalls.v1\ncalls:\n  - {call}\n", encoding="utf-8")
    with pytest.raises(testcalls.TestCallError):
        testcalls.load_user_calls(bad, testcalls.load_templates())


def test_user_call_outside_the_grammar_is_refused_at_render(tmp_path: Path) -> None:
    bad = testcalls.CallTemplate("U03", "terminal", "s", "p", "PASSED_THROUGH", {"command": "echo hi; cat x"},
                                 provenance=testcalls.USER)
    with pytest.raises(testcalls.TestCallError):
        testcalls.render([bad], testcalls.SandboxContext(tmp_path, "http://127.0.0.1:1/t", "PGCANARY-0"))


def test_demo_contract_loads() -> None:
    contract = Path(testcalls.DEFAULT_CATALOG).parent / "fixtures" / "contract.yaml"
    templates = testcalls.load_user_calls(contract, testcalls.load_templates())
    assert next(t for t in templates if t.id == "TC03").expect == {"stack": ("PASSED_THROUGH", "BLOCKED")}
