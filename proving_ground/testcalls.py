# File: proving_ground/testcalls.py
# ruamel.yaml==0.18.16 is a Hermes core dependency (hermes-agent pyproject.toml:55); YAML(typ="safe", pure=True)
# is the same loader hermes_yaml.py:22-25 uses. The rest is pure logic.
"""Load, validate and render the inert test-call catalog (built-in) and an optional user file (--calls)."""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import safety

SCHEMA = "provingground.testcalls.v1"
PLACEHOLDERS = ("{SANDBOX}", "{RECORDER_URL}", "{CANARY}", "{CALL_ID}")
EXPECTATIONS = frozenset({"PASSED_THROUGH", "APPROVAL_GATED", "RESULT_CHANGED_BY_CORE"})
OUTCOME_TOKENS = frozenset({"ESCAPED", "ERRORED", "BLOCKED", "APPROVAL_GATED", "REWROTE", "RESULT_CHANGED",
                            "PASSED_THROUGH", "LOAD_FAILED", "NOT_RUN", "NOT_APPLICABLE"})
CANARY_PREFIX = "PGCANARY-"
REQUIRED_ARGS = {"terminal": ("command",), "write_file": ("path", "content"),
                 "read_file": ("path",), "patch": ("path", "old_string", "new_string")}
DEFAULT_CATALOG = Path(__file__).resolve().parent.parent / "testcalls.yaml"
BUILT_IN, USER, CROSSCHECK = "built-in", "user", "crosscheck"
USER_ID = re.compile(r"^U[A-Za-z0-9_-]{1,15}$")
EXPECT_COLUMN = re.compile(r"^(baseline|stack|solo:[A-Za-z0-9_./-]+)$")


class TestCallError(ValueError):
    """The catalog file is malformed or names something outside the drill rules."""

    __test__ = False


@dataclass(frozen=True)
class CallTemplate:
    id: str
    tool: str
    subject: str
    purpose: str
    expect_baseline: str
    args: Mapping[str, Any]
    seed: Mapping[str, str] = field(default_factory=dict)
    provenance: str = BUILT_IN
    expect: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class SandboxContext:
    sandbox: Path
    recorder_url: str
    canary: str


@dataclass(frozen=True)
class DrillCall:
    id: str
    call_ref: str
    tool: str
    subject: str
    purpose: str
    expect_baseline: str
    args: Mapping[str, Any]
    seed: Mapping[str, str]
    provenance: str = BUILT_IN

    def as_plan(self) -> dict[str, Any]:
        return {"id": self.id, "call_ref": self.call_ref, "tool": self.tool, "args": dict(self.args),
                "provenance": self.provenance}


def make_canary() -> str:
    return CANARY_PREFIX + secrets.token_hex(8)


def call_ref_for(call_id: str) -> str:
    return f"pgcall_{call_id}"


def _expect_from(raw: Any, where: str) -> dict[str, tuple[str, ...]]:
    if raw in (None, {}):
        return {}
    if not isinstance(raw, Mapping):
        raise TestCallError(f"{where}: expect must map a column to an outcome or a list of outcomes")
    out: dict[str, tuple[str, ...]] = {}
    for column, value in raw.items():
        values = tuple(str(v) for v in (value if isinstance(value, list) else [value]))
        if not EXPECT_COLUMN.match(str(column)) or not values or set(values) - OUTCOME_TOKENS:
            raise TestCallError(f"{where}: bad expectation {column!r}: {value!r}")
        out[str(column)] = values
    return out


def _seed_from(raw: Any, where: str) -> dict[str, str]:
    seed = raw or {}
    if not isinstance(seed, Mapping) or not all(isinstance(k, str) and isinstance(v, str) for k, v in seed.items()):
        raise TestCallError(f"{where}: seed must map sandbox-relative file names to text")
    return dict(seed)


def _template_from(raw: Any, provenance: str = BUILT_IN) -> CallTemplate:
    """One entry of a test-call file; a user file is external data, so every malformed shape is a TestCallError."""
    if not isinstance(raw, Mapping):
        raise TestCallError(f"malformed test call entry: expected a mapping, got {type(raw).__name__}")
    try:
        baseline = raw.get("expect_baseline", "PASSED_THROUGH") if provenance == USER else raw["expect_baseline"]
        if not isinstance(raw["args"], Mapping):
            raise TestCallError(f"{raw.get('id')}: args must be a mapping")
        return CallTemplate(id=str(raw["id"]), tool=str(raw["tool"]), subject=str(raw["subject"]),
                            purpose=str(raw["purpose"]), expect_baseline=str(baseline),
                            args=dict(raw["args"]), seed=_seed_from(raw.get("seed"), str(raw.get("id"))),
                            provenance=provenance, expect=_expect_from(raw.get("expect"), str(raw.get("id"))))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        if isinstance(exc, TestCallError):
            raise
        raise TestCallError(f"malformed test call entry: {type(exc).__name__}: {exc}") from exc


def _load_yaml(path: Path) -> dict[str, Any]:
    from ruamel.yaml import YAML

    text = path.read_text(encoding="utf-8")
    try:
        data = YAML(typ="safe", pure=True).load(text) or {}
    except Exception as exc:  # ruamel's YAMLError family: the file is not valid YAML
        raise TestCallError(f"{path}: not valid YAML ({type(exc).__name__})") from exc
    if not isinstance(data, Mapping) or data.get("schema") != SCHEMA:
        raise TestCallError(f"{path}: schema must be {SCHEMA}")
    if not isinstance(data.get("calls") or [], list):
        raise TestCallError(f"{path}: calls must be a list")
    return dict(data)


def load_templates(path: Path = DEFAULT_CATALOG) -> list[CallTemplate]:
    data = _load_yaml(path)
    templates = [_template_from(raw) for raw in data.get("calls") or []]
    validate_templates(templates)
    return templates


def load_user_calls(path: Path, built_in: Sequence[CallTemplate]) -> list[CallTemplate]:
    """--calls FILE: extra user calls (ids U..., provenance user) plus `expect:` contracts on built-in ids.
    Same validator as the catalog; at render time user calls must also pass safety.derived_call_reasons."""
    data = _load_yaml(path)
    extra = [_template_from(raw, USER) for raw in data.get("calls") or []]
    for template in extra:
        if not USER_ID.match(template.id):
            raise TestCallError(f"{template.id}: user call ids start with U (e.g. U01) so they never shadow TCxx")
    contracts = data.get("expect") or {}
    if not isinstance(contracts, Mapping):
        raise TestCallError(f"{path}: top-level expect must map a call id to column expectations")
    known = {t.id for t in built_in}
    merged = []
    for t in built_in:
        merged.append(CallTemplate(**{**t.__dict__, "expect": {**t.expect, **_expect_from(contracts.get(t.id), t.id)}}))
    unknown = set(map(str, contracts)) - known
    if unknown:
        raise TestCallError(f"{path}: expect names unknown built-in calls {sorted(unknown)}")
    validate_templates(merged + extra)
    return merged + extra


def _check_one(template: CallTemplate) -> None:
    if template.tool not in safety.ALLOWED_TOOLS:
        raise TestCallError(f"{template.id}: tool {template.tool!r} is not on the drill allow-list")
    missing = [name for name in REQUIRED_ARGS[template.tool] if name not in template.args]
    if missing:
        raise TestCallError(f"{template.id}: missing args {missing}")
    if template.expect_baseline not in EXPECTATIONS:
        raise TestCallError(f"{template.id}: unknown expect_baseline {template.expect_baseline!r}")
    for rel in template.seed:
        if rel.startswith("/") or ".." in Path(rel).parts:
            raise TestCallError(f"{template.id}: seed path {rel!r} must be sandbox-relative")
    words = str(template.args.get("command", "")).split(" ") if template.tool == "terminal" else []
    if words[:1] == ["curl"] and words[1:2] != [safety.CURL_NO_RC]:
        raise TestCallError(f"{template.id}: every curl call starts `curl -q` so no .curlrc is read")


def validate_templates(templates: Sequence[CallTemplate]) -> None:
    ids = [t.id for t in templates]
    if len(ids) != len(set(ids)):
        raise TestCallError("duplicate test call ids")
    for template in templates:
        _check_one(template)


def _fill(value: Any, mapping: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        for key, replacement in mapping.items():
            value = value.replace(key, replacement)
        return value
    if isinstance(value, Mapping):
        return {k: _fill(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, mapping) for v in value]
    return value


def render(templates: Sequence[CallTemplate], ctx: SandboxContext) -> list[DrillCall]:
    """Pure: turn templates into concrete calls for one sandbox. Raises if a result leaves the sandbox, or if a
    user or cross-check call fails the closed allow-list grammar (safety.derived_call_reasons)."""
    calls: list[DrillCall] = []
    for t in templates:
        mapping = {"{SANDBOX}": str(ctx.sandbox), "{RECORDER_URL}": ctx.recorder_url,
                   "{CANARY}": ctx.canary, "{CALL_ID}": t.id}
        args = _fill(dict(t.args), mapping)
        reasons = safety.escape_reasons(args, ctx.sandbox)
        if t.provenance != BUILT_IN:
            reasons = safety.derived_call_reasons(t.tool, args, ctx.sandbox, ctx.recorder_url)
        if reasons:
            raise TestCallError(f"{t.id}: rendered args leave the sandbox or the drill grammar: {reasons}")
        seed = {rel: _fill(text, mapping) for rel, text in t.seed.items()}
        calls.append(DrillCall(t.id, call_ref_for(t.id), t.tool, t.subject, t.purpose,
                               t.expect_baseline, args, seed, t.provenance))
    return calls


def replayable(template: CallTemplate, probe: SandboxContext) -> list[str]:
    """Grammar reasons for one derived template, rendered into a probe context; empty list = may be replayed."""
    mapping = {"{SANDBOX}": str(probe.sandbox), "{RECORDER_URL}": probe.recorder_url,
               "{CANARY}": probe.canary, "{CALL_ID}": template.id}
    return safety.derived_call_reasons(template.tool, _fill(dict(template.args), mapping), probe.sandbox,
                                       probe.recorder_url)


def crosscheck_templates(rewrites: Sequence[tuple[str, str, Mapping[str, Any]]],
                         probe: SandboxContext) -> tuple[list[CallTemplate], list[tuple[str, tuple[str, ...]]]]:
    """Pure: (source id, tool, normalized final args) -> (replayable XC-<id> templates, unreplayable [(id, reasons)]).
    A third-party-authored rewrite that fails the closed grammar is never executed."""
    keep, refused = [], []
    for source, tool, args in rewrites:
        template = CallTemplate(id=f"XC-{source}", tool=tool, subject="final args of a rewritten call",
                                purpose=f"Sends the final arguments of {source} directly, to see which checks would act on them.",
                                expect_baseline="PASSED_THROUGH", args=dict(args), provenance=CROSSCHECK)
        reasons = replayable(template, probe)
        if reasons:
            refused.append((source, tuple(reasons)))
        else:
            keep.append(template)
    return keep, refused
