# File: tests/test_testcalls_schema.py
# I9: only allow-listed tools; the loader rejects anything else.
from __future__ import annotations

from pathlib import Path

import pytest

from proving_ground import safety, testcalls


def test_catalog_uses_only_allowed_tools() -> None:
    templates = testcalls.load_templates()
    assert [t.id for t in templates] == [f"TC{i:02d}" for i in range(1, 25)]
    assert {t.tool for t in templates if not t.probe} <= safety.ALLOWED_TOOLS
    assert {t.tool for t in templates if t.probe} == safety.PROBE_TOOLS


def test_every_probe_expects_not_executed_and_nothing_else_does() -> None:
    for t in testcalls.load_templates():
        assert (t.expect_baseline == "NOT_EXECUTED") is t.probe, t.id


def test_probe_flag_cannot_turn_an_allowed_tool_into_a_probe(tmp_path: Path) -> None:
    bad = tmp_path / "testcalls.yaml"
    bad.write_text("schema: provingground.testcalls.v1\ncalls:\n  - {id: TCX, tool: terminal, probe: true, subject: s, "
                   "purpose: p, expect_baseline: NOT_EXECUTED, args: {command: echo x}}\n", encoding="utf-8")
    with pytest.raises(testcalls.TestCallError, match="block-only probe must use"):
        testcalls.load_templates(bad)


def test_a_probe_tool_without_the_probe_flag_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "testcalls.yaml"
    bad.write_text("schema: provingground.testcalls.v1\ncalls:\n  - {id: TCX, tool: memory, subject: s, purpose: p, "
                   "expect_baseline: PASSED_THROUGH, args: {target: memory, operations: []}}\n", encoding="utf-8")
    with pytest.raises(testcalls.TestCallError, match="not on the drill allow-list"):
        testcalls.load_templates(bad)


@pytest.mark.parametrize("probe", ["yes", 1, "true"])
def test_probe_must_be_a_real_boolean(tmp_path: Path, probe: object) -> None:
    bad = tmp_path / "testcalls.yaml"
    bad.write_text("schema: provingground.testcalls.v1\ncalls:\n  - {id: TCX, tool: execute_code, probe: " + repr(probe)
                   + ", subject: s, purpose: p, expect_baseline: NOT_EXECUTED, args: {code: x}}\n", encoding="utf-8")
    with pytest.raises(testcalls.TestCallError, match="probe must be true or false"):
        testcalls.load_templates(bad)


def test_user_calls_cannot_be_probes(tmp_path: Path) -> None:
    calls = tmp_path / "calls.yaml"
    calls.write_text("schema: provingground.testcalls.v1\ncalls:\n  - {id: U01, tool: execute_code, probe: true, "
                     "subject: s, purpose: p, args: {code: x}}\n", encoding="utf-8")
    with pytest.raises(testcalls.TestCallError, match="only built-in"):
        testcalls.load_user_calls(calls, testcalls.load_templates())


def test_probe_plans_carry_the_flag_and_executed_calls_do_not(tmp_path: Path) -> None:
    ctx = testcalls.SandboxContext(tmp_path, "http://127.0.0.1:4321/x", testcalls.make_canary())
    plans = {c.id: c.as_plan() for c in testcalls.render(testcalls.load_templates(), ctx)}
    assert {cid for cid, plan in plans.items() if plan.get("probe") is True} == {"TC21", "TC22", "TC23", "TC24"}
    assert all("probe" not in plans[f"TC{i:02d}"] for i in range(1, 21))
    assert plans["TC23"]["args"] == {"urls": ["http://127.0.0.1:4321/x/TC23"]}


def test_the_documented_fake_key_exists_only_after_rendering(tmp_path: Path) -> None:
    ctx = testcalls.SandboxContext(tmp_path, "http://127.0.0.1:4321/x", testcalls.make_canary())
    tc16 = next(c for c in testcalls.render(testcalls.load_templates(), ctx) if c.id == "TC16")
    assert testcalls.FAKE_AWS_KEY_ID in tc16.args["command"]
    assert testcalls.FAKE_AWS_KEY_ID.endswith("EXAMPLE") and len(testcalls.FAKE_AWS_KEY_ID) == 20
    assert "{FAKE_AWS_KEY_ID}" in Path(testcalls.DEFAULT_CATALOG).read_text(encoding="utf-8")


def test_home_shaped_seeds_and_writes_stay_in_the_sandbox(tmp_path: Path) -> None:
    ctx = testcalls.SandboxContext(tmp_path, "http://127.0.0.1:4321/x", testcalls.make_canary())
    calls = {c.id: c for c in testcalls.render(testcalls.load_templates(), ctx)}
    assert set(calls["TC17"].seed) == {".env"} and set(calls["TC18"].seed) == {".ssh/id_rsa"}
    for cid in ("TC17", "TC18", "TC19", "TC20"):
        assert Path(calls[cid].args["path"]).is_relative_to(tmp_path), cid
    assert "NOT-A-KEY" in calls["TC18"].seed[".ssh/id_rsa"]


def test_wire_probe_text_is_synthetic_single_line_text() -> None:
    probe = testcalls.load_wire_probe()
    assert set(probe) == {"user_turn", "reply"}
    assert "example.com" in probe["user_turn"] and "555-01" in probe["user_turn"]
    assert "10.5555/" in probe["reply"] and "你好" in probe["reply"] and probe["reply"].count("PG_REPEAT") >= 30


@pytest.mark.parametrize("body", ["wire_probe: {reply: \"a\\nb\"}", "wire_probe: {other: x}", "wire_probe: [x]",
                                  "wire_probe: {reply: \"  \"}"])
def test_malformed_wire_probe_is_refused(tmp_path: Path, body: str) -> None:
    bad = tmp_path / "testcalls.yaml"
    bad.write_text("schema: provingground.testcalls.v1\ncalls: []\n" + body + "\n", encoding="utf-8")
    with pytest.raises(testcalls.TestCallError, match="wire_probe"):
        testcalls.load_wire_probe(bad)


def test_a_catalog_without_wire_probe_has_none(tmp_path: Path) -> None:
    plain = tmp_path / "testcalls.yaml"
    plain.write_text("schema: provingground.testcalls.v1\ncalls: []\n", encoding="utf-8")
    assert testcalls.load_wire_probe(plain) == {}


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
