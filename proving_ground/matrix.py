# File: proving_ground/matrix.py  (part 1 of 3: types and normalization)
# Pure logic over the child's events.jsonl format (proving_ground/observer.py). Hermes phrases are cited at their file:line.
"""Pure core: observations, outcome classification, attribution, findings. No I/O, no Hermes imports."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping, Sequence

TIMEOUT_BLOCK_MESSAGE = "pre_tool_call plugin callback timed out or is still running"  # plugins_dispatch.py:67
RAISED_BLOCK = re.compile(r"^pre_tool_call plugin callback \S+ raised ")  # plugins_dispatch.py:89-95 (fail-closed crash)
HARNESS_PREFIX = "PROVINGGROUND:"
APPROVAL_PHRASES = ("runs without a user present to approve it", "Command denied")  # tools/approval.py:703
VOLATILE_KEYS = frozenset({"duration", "duration_ms", "elapsed", "elapsed_ms", "started_at", "finished_at", "wall_time",
                           "full_output_path"})  # spill-file path, tools/terminal_tool_result.py:193
CORE_TRUNCATION_KEY = "truncation_note"  # core head+tail capture window, tools/terminal_tool_result.py:193
CORE_NOTE = "Hermes core changed the output (for example truncation); no plugin involved"
_SPILL_FILE = re.compile(r"out-\d+-\d+-[0-9a-f]+\.log")


class Outcome(str, Enum):
    ESCAPED = "ESCAPED"
    ERRORED = "ERRORED"
    BLOCKED = "BLOCKED"
    APPROVAL_GATED = "APPROVAL_GATED"
    REWROTE = "REWROTE"
    RESULT_CHANGED = "RESULT_CHANGED"
    CONTEXT_ADDED = "CONTEXT_ADDED"  # --all-wires only: the model's view of the turn differs from the baseline's
    REPLY_CHANGED = "REPLY_CHANGED"  # --all-wires only: the delivered reply differs from the one the model produced
    PASSED_THROUGH = "PASSED_THROUGH"
    LOAD_FAILED = "LOAD_FAILED"
    NOT_RUN = "NOT_RUN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


PRECEDENCE: tuple[Outcome, ...] = tuple(Outcome)
LABELS: dict[Outcome, str] = {
    Outcome.ESCAPED: "ESCAPED", Outcome.ERRORED: "ERRORED", Outcome.BLOCKED: "BLOCKED",
    Outcome.APPROVAL_GATED: "APPROVAL-GATED", Outcome.REWROTE: "REWROTE",
    Outcome.RESULT_CHANGED: "RESULT CHANGED", Outcome.CONTEXT_ADDED: "CONTEXT ADDED",
    Outcome.REPLY_CHANGED: "REPLY CHANGED", Outcome.PASSED_THROUGH: "PASSED THROUGH",
    Outcome.LOAD_FAILED: "LOAD FAILED", Outcome.NOT_RUN: "NOT RUN", Outcome.NOT_APPLICABLE: "N/A",
}
QUIET = frozenset({Outcome.PASSED_THROUGH, Outcome.LOAD_FAILED, Outcome.NOT_RUN, Outcome.NOT_APPLICABLE})
# A stack cell with one of these says nothing about what reached the model, so no solo effect can be "masked" by it.
NO_STACK_RESULT = frozenset({Outcome.LOAD_FAILED, Outcome.NOT_RUN, Outcome.NOT_APPLICABLE, Outcome.ERRORED})
VERBS: dict[Outcome, str] = {Outcome.BLOCKED: "blocks", Outcome.APPROVAL_GATED: "sends to approval"}


@dataclass(frozen=True)
class CallInfo:
    id: str
    tool: str
    subject: str
    purpose: str
    provenance: str = "built-in"
    expect: Mapping[str, Sequence[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class CallObservation:
    call_id: str
    tool: str
    ran: bool
    injected_args: Mapping[str, Any]
    final_args: Mapping[str, Any]
    status: str | None
    error_type: str | None
    error_message: str | None
    terminal_raw: str | None
    tool_raw: str | None
    delivered: str | None
    recorded: bool
    escaped_reasons: tuple[str, ...] = ()
    pre_seen_args: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ColumnRun:
    name: str
    plugins: tuple[str, ...]
    observations: Mapping[str, CallObservation]
    abort_reason: str | None
    wall_s: float
    connects: tuple[tuple[str, bool], ...] = ()
    load_failed: tuple[str, ...] = ()
    scripted_steps: int = 0
    stray_escapes: tuple[tuple[str, ...], ...] = ()  # escapes whose tool_call_id matched no drill call
    bytecode_off: bool | None = None  # the child's sys.dont_write_bytecode, from its activated event
    child_order: tuple[str, ...] | None = None  # subject plugins in the order the child loaded them


@dataclass(frozen=True)
class Cell:
    column: str
    call_id: str
    outcomes: tuple[Outcome, ...]
    hop: str | None = None
    core: bool = False
    unstable: bool = False
    note: str = ""
    core_also: bool = False  # a plugin changed the delivered result on a call Hermes core also changes
    varied: tuple[str, ...] = ()  # --repeat: this solo cell's label in each run, when the runs disagreed (UNSTABLE)

    @property
    def primary(self) -> Outcome:
        return min(self.outcomes, key=PRECEDENCE.index)


@dataclass(frozen=True)
class Attribution:
    call_id: str
    outcome: Outcome
    kind: str
    plugins: tuple[str, ...]
    note: str = ""


@dataclass(frozen=True)
class Finding:
    kind: str
    call_id: str
    plugins: tuple[str, ...]
    text: str


@dataclass(frozen=True)
class DrillConditions:
    hermes_version: str
    n_calls: int
    n_plugins: int
    load_order: tuple[str, ...]
    connects_total: int
    connects_non_loopback: int
    wall_s: float
    started_at: str
    model: str = "NOT CALLED (scripted turn)"
    provider: str = "none (closed loopback 127.0.0.1:9)"
    approvals: str = "manual; headless runs deny every approval"
    credentials: str = "no provider keys in the child env; plugin copies checked for credential-shaped files"
    sandbox: str = "one temporary directory per column, deleted after the run (cwd and HOME only, not an OS sandbox)"
    leftover_processes: int = -1
    leftover_roots: int = -1
    coverage: str = ""
    baseline_note: str = "baseline = Hermes core with only PROVING GROUND enabled (bundled plugins not enumerated)"
    order_note: str = ""  # set when a child loaded its plugins in another order than PROVING GROUND computed


@dataclass(frozen=True)
class Matrix:
    conditions: DrillConditions
    calls: tuple[CallInfo, ...]
    columns: tuple[str, ...]
    cells: Mapping[tuple[str, str], Cell]
    attributions: tuple[Attribution, ...]
    findings: tuple[Finding, ...]
    observations: Mapping[str, Mapping[str, CallObservation]]
    unstable: frozenset[str] = frozenset()
    skipped: tuple[tuple[str, str], ...] = ()
    needs_env: tuple[str, ...] = ()
    aborted: Mapping[str, str] = field(default_factory=dict)
    load_failed: Mapping[str, Sequence[str]] = field(default_factory=dict)
    unreplayable: tuple[tuple[str, tuple[str, ...]], ...] = ()
    options: Mapping[str, str] = field(default_factory=dict)  # opt-in drill options (wires, pairs, repeat): name -> note
    column_plugins: Mapping[str, tuple[str, ...]] = field(default_factory=dict)  # column -> plugins loaded in it
    declared_hooks: Mapping[str, tuple[str, ...]] = field(default_factory=dict)  # plugin -> manifest provides_hooks

    def cell(self, column: str, call_id: str) -> Cell:
        return self.cells[(column, call_id)]


def normalize_text(text: str, sandbox: str, recorder_url: str) -> str:
    """Replace per-run values: sandbox, its drill root (spill files live under <root>/home), recorder URL, spill names."""
    root = sandbox.rsplit("/", 1)[0] if sandbox.endswith("/sandbox") else sandbox
    text = text.replace(sandbox, "{SANDBOX}").replace(root, "{ROOT}").replace(recorder_url, "{RECORDER_URL}")
    return _SPILL_FILE.sub("out-{N}.log", text)


def normalize_value(value: Any, sandbox: str, recorder_url: str) -> Any:
    if isinstance(value, str):
        return normalize_text(value, sandbox, recorder_url)
    if isinstance(value, Mapping):
        return {k: normalize_value(v, sandbox, recorder_url) for k, v in value.items() if k not in VOLATILE_KEYS}
    if isinstance(value, list):
        return [normalize_value(v, sandbox, recorder_url) for v in value]
    return value


def normalize_result(result: Any, sandbox: str, recorder_url: str) -> str | None:
    if result is None:
        return None
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except (json.JSONDecodeError, ValueError):
            return normalize_text(result, sandbox, recorder_url)
        result = parsed
    return json.dumps(normalize_value(result, sandbox, recorder_url), sort_keys=True, ensure_ascii=False)

# File: proving_ground/matrix.py  (part 2 of 3: events -> observations -> cells)


CALL_EVENTS = ("pre_tool_call", "terminal_raw", "tool_raw", "post_tool_call", "escaped")


def _slots(plan_calls: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]]
           ) -> tuple[dict[str, dict[str, Any]], list[Mapping[str, Any]]]:
    """Attach each call event to its drill call by tool_call_id only; return the events that match none (stray).
    Never by call order: a plugin that adds or drops a call would shift every later call."""
    ref_to_id = {str(c["call_ref"]): str(c["id"]) for c in plan_calls}
    slots: dict[str, dict[str, Any]] = {str(c["id"]): {} for c in plan_calls}
    stray: list[Mapping[str, Any]] = []
    for event in events:
        if event.get("ev") not in CALL_EVENTS:
            continue
        target = ref_to_id.get(str(event.get("tool_call_id")))
        if target is None:
            stray.append(event)
        else:
            slots[target].setdefault(str(event["ev"]), event)
    return slots, stray


def mismatch_reason(stray: Sequence[Mapping[str, Any]]) -> str:
    ids = sorted({str(e.get("tool_call_id")) for e in stray})
    return (f"TOOL_CALL_ID_MISMATCH: {len(stray)} tool events carry an id that matches no drill call "
            f"({', '.join(ids[:3])}{' …' if len(ids) > 3 else ''}); not matched by order")


def _observation(call: Mapping[str, Any], slot: Mapping[str, Any], sandbox: str, url: str,
                 recorded: Sequence[str]) -> CallObservation:
    pre, post = slot.get("pre_tool_call") or {}, slot.get("post_tool_call") or {}
    terminal, tool_raw = slot.get("terminal_raw") or {}, slot.get("tool_raw") or {}
    injected = normalize_value(call.get("args") or {}, sandbox, url)  # the plan is authoritative
    pre_seen = normalize_value(pre.get("args") or {}, sandbox, url) if pre else {}
    return CallObservation(
        call_id=str(call["id"]), tool=str(call["tool"]), ran="post_tool_call" in slot,
        injected_args=injected, final_args=normalize_value(post.get("args") or injected, sandbox, url),
        status=post.get("status"), error_type=post.get("error_type"), error_message=post.get("error_message"),
        terminal_raw=normalize_text(str(terminal.get("output") or ""), sandbox, url) if terminal else None,
        tool_raw=normalize_result(tool_raw.get("result"), sandbox, url) if tool_raw else None,
        delivered=normalize_result(post.get("result"), sandbox, url) if post else None,
        recorded=str(call["id"]) in set(recorded),
        escaped_reasons=tuple((slot.get("escaped") or {}).get("reasons") or ()), pre_seen_args=pre_seen)


def unconfirmed_plugins(plugins: Sequence[str], events: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """Subject plugins whose load the child did not positively confirm (no plugins_loaded event = none confirmed)."""
    report = next((e for e in events if e.get("ev") == "plugins_loaded"), None)
    loaded = set(report.get("loaded") or ()) if report else set()
    return tuple(k for k in plugins if k not in loaded)


def column_from_events(name: str, plugins: tuple[str, ...], plan_calls: Sequence[Mapping[str, Any]],
                       events: Sequence[Mapping[str, Any]], sandbox: str, recorder_url: str,
                       recorded: Sequence[str], abort_reason: str | None, wall_s: float,
                       wires: Mapping[str, Any] | None = None) -> ColumnRun:
    """*wires* is the plan's context-and-reply spec (--all-wires); without it the column has only its test calls."""
    slots, stray = _slots(plan_calls, events)
    if stray and abort_reason is None:
        abort_reason = mismatch_reason(stray)
    stray_escapes = tuple(tuple(str(r) for r in e.get("reasons") or ()) for e in stray if e.get("ev") == "escaped")
    observations = {str(c["id"]): _observation(c, slots[str(c["id"])], sandbox, recorder_url, recorded)
                    for c in plan_calls}
    if wires:
        observations.update(wire_observations(wires, events, sandbox, recorder_url))
    connects = tuple((str(e.get("address")), bool(e.get("loopback"))) for e in events if e.get("ev") == "connect")
    steps = sum(1 for e in events if e.get("ev") == "llm_execution")
    activated = next((e for e in events if e.get("ev") == "activated"), {})
    loaded = next((e for e in events if e.get("ev") == "plugins_loaded"), {})
    order = tuple(str(k) for k in loaded["order"]) if isinstance(loaded.get("order"), list) else None
    return ColumnRun(name, plugins, observations, abort_reason, wall_s, connects,
                     unconfirmed_plugins(plugins, events), steps, stray_escapes, activated.get("dont_write_bytecode"),
                     order)


def _text(obs: CallObservation) -> str:
    return f"{obs.delivered or ''} {obs.error_message or ''}"


def _gate_outcome(obs: CallObservation) -> Outcome | None:
    if not obs.ran:
        return Outcome.NOT_RUN
    if obs.escaped_reasons:
        return Outcome.ESCAPED
    if obs.status == "blocked":
        message = obs.error_message or ""
        harness = message == TIMEOUT_BLOCK_MESSAGE or message.startswith(HARNESS_PREFIX) or bool(RAISED_BLOCK.match(message))
        return Outcome.ERRORED if harness else Outcome.BLOCKED
    if obs.status == "error" and any(p in _text(obs) for p in APPROVAL_PHRASES):
        return Outcome.APPROVAL_GATED
    return None


def tool_output(tool_raw: str | None) -> str | None:
    try:
        parsed = json.loads(tool_raw) if tool_raw else None
    except (json.JSONDecodeError, ValueError):
        return tool_raw
    return str(parsed.get("output")) if isinstance(parsed, dict) and "output" in parsed else tool_raw


def _core_changed(obs: CallObservation) -> bool:
    """True when Hermes core altered the output: its capture window truncated it, or raw tap and tool result differ."""
    try:
        parsed = json.loads(obs.tool_raw) if obs.tool_raw else None
    except (json.JSONDecodeError, ValueError):
        parsed = None
    if isinstance(parsed, dict) and parsed.get(CORE_TRUNCATION_KEY):
        return True
    out = tool_output(obs.tool_raw)
    if obs.terminal_raw is None or out is None:
        return False
    return obs.terminal_raw.rstrip("\n") != out.rstrip("\n")


def classify_baseline(obs: CallObservation) -> Cell:
    gate = _gate_outcome(obs)
    if gate is not None:
        return Cell("baseline", obs.call_id, (gate,), core=gate is Outcome.APPROVAL_GATED)
    if _core_changed(obs):
        return Cell("baseline", obs.call_id, (Outcome.RESULT_CHANGED,), hop="core", core=True, note=CORE_NOTE)
    return Cell("baseline", obs.call_id, (Outcome.PASSED_THROUGH,))


def _result_hop(obs: CallObservation, base: CallObservation, comparable: bool) -> str | None:
    """Where a plugin changed the delivered result, judged against the baseline's DELIVERED result (TC05 fix)."""
    if obs.tool_raw is not None and obs.delivered != obs.tool_raw and base.delivered == base.tool_raw:
        return "tool_result"
    if not comparable or obs.terminal_raw != base.terminal_raw or obs.delivered == base.delivered:
        return None
    return "tool_result" if obs.tool_raw == base.tool_raw else "terminal_output"


def classify(column: str, obs: CallObservation, base: CallObservation, unstable: bool) -> Cell:
    gate = _gate_outcome(obs)
    if gate is not None:
        core = gate is Outcome.APPROVAL_GATED and _gate_outcome(base) is Outcome.APPROVAL_GATED
        raised = gate is Outcome.ERRORED and bool(RAISED_BLOCK.match(obs.error_message or ""))
        return Cell(column, obs.call_id, (gate,), core=core, note="a plugin raised an exception" if raised else "")
    outcomes: list[Outcome] = []
    hop: str | None = None
    inplace = bool(obs.pre_seen_args) and obs.pre_seen_args != obs.injected_args
    rewrote = obs.final_args != obs.injected_args or inplace
    if rewrote:
        outcomes.append(Outcome.REWROTE)
        hop = "args-inplace" if inplace else "args"
    result_hop = _result_hop(obs, base, not rewrote and not unstable)
    if result_hop is not None:
        outcomes.append(Outcome.RESULT_CHANGED)
        hop = result_hop
    core_base = not rewrote and classify_baseline(base).core and _gate_outcome(base) is None
    if core_base and result_hop is None:
        return Cell(column, obs.call_id, (Outcome.RESULT_CHANGED,), hop="core", core=True, unstable=unstable,
                    note=CORE_NOTE)
    return Cell(column, obs.call_id, tuple(outcomes) or (Outcome.PASSED_THROUGH,), hop=hop, unstable=unstable,
                core_also=core_base)


# Context and reply wire (--all-wires). Two extra rows per drill, built from the child's llm_context, reply_raw and
# reply_delivered events (observer.make_wire_hooks, injector.context_view) and judged with the same rules as a call:
# a column against the zero-plugin baseline, an effect the baseline shows too labelled (core).
WIRE_TOOL = "llm"
CONTEXT_ID, REPLY_ID = "LLM-CONTEXT", "LLM-REPLY"
WIRE_CALLS: tuple[CallInfo, ...] = (
    CallInfo(CONTEXT_ID, WIRE_TOOL, "context sent to the model",
             "every message the scripted turn sends the model besides tool results (system prompt, user turn), and "
             "the tools offered; plugins add here with pre_llm_call"),
    CallInfo(REPLY_ID, WIRE_TOOL, "final reply delivered",
             "the scripted final reply as the model produced it, against the reply the turn delivered; plugins "
             "rewrite it with transform_llm_output"))
WIRE_CORE_NOTE = "Hermes core changed this without any plugin (the zero-plugin baseline shows it too)"
_CONTEXT_PARTS = (("system prompt", "system"), ("user turn", "user"), ("assistant text", "assistant"))
# The system prompt's date line (agent/system_prompt.py:517) changes at midnight, not with any plugin.
_START_DATE = re.compile(r"^(Conversation started: ).*$", re.M)


def _context_part(view: Mapping[str, Any], role: str) -> list[str]:
    return [str(m.get("content")) for m in view.get("messages") or () if isinstance(m, Mapping) and m.get("role") == role]


def _normalized_view(event: Mapping[str, Any], sandbox: str, url: str) -> dict[str, Any]:
    messages = [{"role": str(m.get("role")),
                 "content": _START_DATE.sub(r"\1{DATE}", normalize_text(str(m.get("content") or ""), sandbox, url))}
                for m in event.get("messages") or () if isinstance(m, Mapping)]
    return {"messages": messages, "tools": sorted(str(t) for t in event.get("tools") or ())}


def wire_observations(wires: Mapping[str, Any], events: Sequence[Mapping[str, Any]], sandbox: str,
                      url: str) -> dict[str, CallObservation]:
    """The two wire rows of one column. The context row keeps each distinct view once, in step order."""
    views: list[dict[str, Any]] = []
    for event in (e for e in events if e.get("ev") == "llm_context"):
        view = _normalized_view(event, sandbox, url)
        if view not in views:
            views.append(view)
    raw = next((e for e in events if e.get("ev") == "reply_raw"), None)
    out = next((e for e in events if e.get("ev") == "reply_delivered"), None)
    prompt, reply = {"prompt": str(wires.get("prompt"))}, {"reply": str(wires.get("reply"))}
    text = lambda e: normalize_text(str(e.get("text") or ""), sandbox, url) if e else None  # noqa: E731
    return {CONTEXT_ID: CallObservation(CONTEXT_ID, WIRE_TOOL, bool(views), prompt, prompt, None, None, None, None,
                                        None, json.dumps(views, sort_keys=True, ensure_ascii=False) if views else None,
                                        False),
            REPLY_ID: CallObservation(REPLY_ID, WIRE_TOOL, out is not None, reply, reply, None, None, None, None,
                                      text(raw), text(out), False)}


def context_views(obs: CallObservation) -> list[Mapping[str, Any]]:
    try:
        views = json.loads(obs.delivered) if obs.delivered else []
    except (json.JSONDecodeError, ValueError):
        return []
    return [v for v in views if isinstance(v, Mapping)] if isinstance(views, list) else []


def context_hop(obs: CallObservation, base: CallObservation) -> str:
    """Which parts of the model's view differ from the baseline's: system prompt, user turn, assistant text, tools."""
    mine, theirs = context_views(obs), context_views(base)
    parts = [label for label, role in _CONTEXT_PARTS
             if [_context_part(v, role) for v in mine] != [_context_part(v, role) for v in theirs]]
    if [v.get("tools") for v in mine] != [v.get("tools") for v in theirs]:
        parts.append("tools offered")
    return ", ".join(parts) or "message order"


def _prompt_extended(obs: CallObservation) -> bool:
    """The baseline's own test: does any step's user turn differ from the prompt PROVING GROUND sent?"""
    prompt = str(obs.injected_args.get("prompt"))
    return any(turn != prompt for view in context_views(obs) for turn in _context_part(view, "user"))


def _reply_changed(obs: CallObservation) -> bool:
    return obs.tool_raw is not None and obs.delivered != obs.tool_raw


def _wire_changed_alone(obs: CallObservation) -> bool:
    return _prompt_extended(obs) if obs.call_id == CONTEXT_ID else _reply_changed(obs)


def _wire_outcome(call_id: str) -> Outcome:
    return Outcome.CONTEXT_ADDED if call_id == CONTEXT_ID else Outcome.REPLY_CHANGED


def classify_wire_baseline(obs: CallObservation) -> Cell:
    if not obs.ran:
        return Cell("baseline", obs.call_id, (Outcome.NOT_RUN,), note="the child recorded nothing on this wire")
    if _wire_changed_alone(obs):
        return Cell("baseline", obs.call_id, (_wire_outcome(obs.call_id),), hop="core", core=True, note=WIRE_CORE_NOTE)
    return Cell("baseline", obs.call_id, (Outcome.PASSED_THROUGH,))


def classify_wire(column: str, obs: CallObservation, base: CallObservation, unstable: bool) -> Cell:
    """A column's wire cell: a difference from the baseline is the plugin effect (never when the baseline itself
    differed between its two runs); a difference the baseline shows too is core."""
    if not obs.ran:
        return Cell(column, obs.call_id, (Outcome.NOT_RUN,), note="the child recorded nothing on this wire")
    base_core = base.ran and _wire_changed_alone(base)
    if obs.call_id == CONTEXT_ID:
        differs, hop = obs.delivered != base.delivered, context_hop(obs, base)
    else:
        differs, hop = _reply_changed(obs) and obs.delivered != base.delivered, "transform_llm_output"
    if differs and not unstable and base.ran:
        return Cell(column, obs.call_id, (_wire_outcome(obs.call_id),), hop=hop, core_also=base_core)
    if base_core:
        return Cell(column, obs.call_id, (_wire_outcome(obs.call_id),), hop="core", core=True, unstable=unstable,
                    note=WIRE_CORE_NOTE)
    return Cell(column, obs.call_id, (Outcome.PASSED_THROUGH,), unstable=unstable)


def baseline_cell(obs: CallObservation) -> Cell:
    return classify_wire_baseline(obs) if obs.tool == WIRE_TOOL else classify_baseline(obs)


def column_cell(column: str, obs: CallObservation, base: CallObservation, unstable: bool) -> Cell:
    return (classify_wire if obs.tool == WIRE_TOOL else classify)(column, obs, base, unstable)


def count_test_calls(calls: Sequence[CallInfo]) -> int:
    """Test calls only: the two wire rows are not synthetic inputs."""
    return sum(1 for c in calls if c.tool != WIRE_TOOL)


# File: proving_ground/matrix.py  (part 3 of 3: attribution, findings, assembly)


def unstable_calls(first: ColumnRun, second: ColumnRun | None) -> frozenset[str]:
    if second is None:
        return frozenset()
    return frozenset(cid for cid, obs in first.observations.items()
                     if cid in second.observations and obs.delivered != second.observations[cid].delivered)


def rewrites_for_crosscheck(stack: ColumnRun) -> list[tuple[str, str, Mapping[str, Any]]]:
    return [(cid, obs.tool, dict(obs.final_args)) for cid, obs in stack.observations.items()
            if obs.ran and not obs.escaped_reasons and obs.status == "ok" and obs.final_args != obs.injected_args]


def _attribute_one(call_id: str, outcome: Outcome, base: Cell, solos: Mapping[str, Mapping[str, Cell]],
                   load_order: Sequence[str]) -> Attribution:
    found = _attribute_first_run(call_id, outcome, base, solos, load_order)
    varied = [k for k in load_order if solos[k][call_id].varied]
    if not varied:
        return found
    return replace(found, note=f"{found.note}; UNSTABLE: the repeated solo runs of {', '.join(varied)} disagree on "
                               f"{call_id}, so this attribution can change between drills")


def _attribute_first_run(call_id: str, outcome: Outcome, base: Cell, solos: Mapping[str, Mapping[str, Cell]],
                         load_order: Sequence[str]) -> Attribution:
    matches = tuple(k for k in load_order if outcome in solos[k][call_id].outcomes and not solos[k][call_id].core)
    if outcome in base.outcomes and base.core and not matches:
        return Attribution(call_id, outcome, "core", (), "the zero-plugin baseline shows the same effect")
    if outcome is Outcome.ERRORED:
        return Attribution(call_id, outcome, "errored", matches,
                           "a plugin callback raised or timed out; this is a failure, not a deliberate block")
    if len(matches) == 1:
        return Attribution(call_id, outcome, "solo-match", matches,
                           f"observed in one solo run: {matches[0]} (stateful plugins may differ between runs)")
    if matches:
        return Attribution(call_id, outcome, "multiple-candidates", matches,
                           "several plugins do this alone; which one's effect reached the model is not isolated "
                           f"(load order given to the child: {', '.join(load_order)})")
    return Attribution(call_id, outcome, "interaction", (), "no plugin does this alone; it appears only in combination")


def attribute(stack: Mapping[str, Cell], base: Mapping[str, Cell], solos: Mapping[str, Mapping[str, Cell]],
              load_order: Sequence[str]) -> list[Attribution]:
    return [_attribute_one(cid, outcome, base[cid], solos, load_order)
            for cid, cell in stack.items() for outcome in cell.outcomes if outcome not in QUIET]


GATES = frozenset({Outcome.BLOCKED, Outcome.APPROVAL_GATED, Outcome.ERRORED})


def _lost(cid: str, stack_cell: Cell, solos: Mapping[str, Mapping[str, Cell]]) -> list[tuple[str, Outcome]]:
    """(plugin, outcome) solo effects on cid that do not reach the model in the full stack."""
    reached = set() if stack_cell.core else set(stack_cell.outcomes)  # a core-only stack cell carries no plugin effect
    return [(key, outcome) for key, cells in solos.items() if not cells[cid].core  # core's own effect, not the plugin's
            for outcome in sorted(set(cells[cid].outcomes) - QUIET - reached, key=PRECEDENCE.index)]


def _grouped_mask(cid: str, gate: Outcome, lost: Sequence[tuple[str, Outcome]],
                  attributions: Sequence[Attribution]) -> Finding:
    """One finding per blocker x call: the other plugins' effects are consequences of one block."""
    blockers = next((a.plugins for a in attributions if a.call_id == cid and a.outcome is gate), ())
    masked = tuple(dict.fromkeys(k for k, _o in lost if k not in blockers))
    effects = ", ".join(f"{k} {LABELS[o]}" for k, o in lost)
    return Finding("MASKED_IN_STACK", cid, (*blockers, *masked),
                   f"{', '.join(blockers) or 'the stack'} {LABELS[gate]} {cid} in the full stack, so what other plugins "
                   f"do to it on their own does not reach the model: {effects}.")


def _masked(stack: Mapping[str, Cell], solos: Mapping[str, Mapping[str, Cell]],
            attributions: Sequence[Attribution] = ()) -> list[Finding]:
    found = []
    for cid, stack_cell in stack.items():
        if stack_cell.primary in NO_STACK_RESULT:
            continue
        lost = _lost(cid, stack_cell, solos)
        if lost and stack_cell.primary in GATES and not stack_cell.core:
            found.append(_grouped_mask(cid, stack_cell.primary, lost, attributions))
            continue
        found += [Finding("MASKED_IN_STACK", cid, (key,), f"{key} {LABELS[outcome]} {cid} on its own; in the full "
                          "stack that effect does not reach the model.") for key, outcome in lost]
    return found


def _unseen(stack: Mapping[str, Cell], cross: Mapping[str, Mapping[str, Cell]],
            attributions: Sequence[Attribution]) -> list[Finding]:
    found = []
    for key, cells in cross.items():
        for xc_id, cell in cells.items():
            source = xc_id.removeprefix("XC-")
            if source not in stack or cell.primary not in (Outcome.BLOCKED, Outcome.APPROVAL_GATED):
                continue
            rewriters = next((a.plugins for a in attributions if a.call_id == source and a.outcome is Outcome.REWROTE), ())
            who = ", ".join(rewriters) or "a plugin"
            found.append(Finding("REWRITE_UNSEEN_BY", source, (*rewriters, key),
                                 f"{who} rewrote {source} after every pre-call check had run. {key} "
                                 f"{VERBS[cell.primary]} the rewritten call when it sees it directly; in the stack "
                                 "it only saw the original, and the rewritten call ran."))
    return found


def _escapes(columns: Sequence[ColumnRun]) -> list[Finding]:
    found = [Finding("ESCAPED", cid, col.plugins, f"In column {col.name}, {cid} left the sandbox "
                     f"({', '.join(obs.escaped_reasons)}); the drill stopped that column.")
             for col in columns for cid, obs in col.observations.items() if obs.escaped_reasons]
    return found + [Finding("ESCAPED", "unknown call", col.plugins, f"In column {col.name}, a tool call with an id "
                            f"that matches no drill call left the sandbox ({', '.join(reasons)}); the drill stopped "
                            "that column.") for col in columns for reasons in col.stray_escapes]


def _load_failed_cell(cell: Cell, col: ColumnRun) -> Cell:
    """Release rule: a plugin not positively confirmed loaded is never rendered as PASSED THROUGH."""
    note = f"not confirmed loaded in the child: {', '.join(col.load_failed)}"
    if col.name.startswith(("solo:", "pair:")) or cell.primary in QUIET:
        return Cell(cell.column, cell.call_id, (Outcome.LOAD_FAILED,), note=note)
    return replace(cell, note="; ".join(filter(None, [cell.note, note])))


def _column_cells(col: ColumnRun, base: ColumnRun, unstable: frozenset[str]) -> dict[str, Cell]:
    cells = {cid: column_cell(col.name, obs, base.observations[cid], cid in unstable)
             for cid, obs in col.observations.items() if cid in base.observations}
    return {cid: _load_failed_cell(c, col) for cid, c in cells.items()} if col.load_failed else cells


def _na_cells(key: str, calls: Sequence[CallInfo]) -> dict[str, Cell]:
    return {c.id: Cell(f"solo:{key}", c.id, (Outcome.NOT_APPLICABLE,),
                       note="declares requires_env; the sandbox has no credentials") for c in calls}


def order_note(columns: Sequence[ColumnRun]) -> str:
    """Point d: compare each child's reported load order with the order PROVING GROUND gave it."""
    off = [c for c in columns if c.child_order is not None and not c.load_failed
           and c.child_order != tuple(k for k in c.plugins if k in c.child_order)]
    return "; ".join(f"{c.name}: the child loaded {', '.join(c.child_order)}, PROVING GROUND computed "
                     f"{', '.join(c.plugins)} (attribution names candidates in the computed order)" for c in off)


def coverage_line(columns: Sequence[ColumnRun], needs_env: Sequence[str], skipped: Sequence[tuple[str, str]]) -> str:
    solos = [c for c in columns if c.name.startswith("solo:")]
    failed = sorted({k for c in columns for k in c.load_failed})
    executed = [c.name.removeprefix("solo:") for c in solos if not c.load_failed]
    return (f"executed: {len(executed)} · unavailable (requires_env): {len(needs_env)} · "
            f"dependency-skipped / not a directory plugin: {len(skipped)} · load-failed: {len(failed)}"
            + (f" ({', '.join(failed)})" if failed else ""))


def model_line(steps: int, n_calls: int) -> str:
    """No scripted step with calls planned means no drill turn ran: say so instead of implying a scripted run."""
    if steps == 0 and n_calls:
        return ("NOT CALLED, and no scripted turn ran (0 llm_execution steps answered by PROVING GROUND); "
                "the only model endpoint was the closed loopback port")
    return f"NOT CALLED (scripted turn: {steps} llm_execution steps answered by PROVING GROUND, 0 provider calls)"


# Pairwise columns (--pairs) and repeated solo runs (--repeat N), both opt-in and both judged with the rules above.
PAIR_CAP = 10  # at most this many pair columns per drill (all pairs of five plugins); the report says when it capped


def pair_keys(name: str) -> tuple[str, ...]:
    return tuple(name.removeprefix("pair:").split("+"))


def _pair_finding(a: Attribution, stack_plugins: tuple[str, ...], pairs: Mapping[str, Mapping[str, Cell]],
                  pair_total: int) -> Finding:
    label = f"{a.call_id} {LABELS[a.outcome]}"
    if len(stack_plugins) == 2:
        return Finding("STACK_ONLY_INTERACTION", a.call_id, stack_plugins,
                       f"{label} appears only with {stack_plugins[0]} and {stack_plugins[1]} together and neither does "
                       "it alone, so the smallest set that reproduces it is these two plugins.")
    hits = [name for name, cells in pairs.items()
            if a.call_id in cells and a.outcome in cells[a.call_id].outcomes and not cells[a.call_id].core]
    if hits:
        keys = tuple(dict.fromkeys(k for name in hits for k in pair_keys(name)))
        return Finding("STACK_ONLY_INTERACTION", a.call_id, keys,
                       f"{label} appears only in combination; no plugin does it alone. The smallest set observed to "
                       f"reproduce it is a pair: {'; '.join(' + '.join(pair_keys(n)) for n in hits)}.")
    if len(pairs) < pair_total:
        capped = f"; capped at {PAIR_CAP}" if pair_total > PAIR_CAP else ""
        return Finding("STACK_ONLY_INTERACTION", a.call_id, (),
                       f"{label} appears only in combination. None of the {len(pairs)} pair columns that ran to the "
                       f"end reproduces it ({pair_total} pairs exist{capped}), so it may need a pair not run here "
                       "or 3+ plugins.")
    return Finding("STACK_ONLY_INTERACTION", a.call_id, (),
                   f"{label} appears only in combination, and no pair of these plugins reproduces it on its own: "
                   "it needs 3+ plugins together.")


def pair_findings(columns: Sequence[ColumnRun], grid: Mapping[str, Mapping[str, Cell]],
                  attributions: Sequence[Attribution], pair_total: int) -> list[Finding]:
    """One STACK_ONLY_INTERACTION per interaction-kind attribution, naming the smallest set observed to reproduce it.
    A pair column that stopped or did not confirm its plugins loaded is not evidence either way."""
    stack = next((c for c in columns if c.name == "stack"), None)
    if stack is None:
        return []
    usable = {c.name: grid[c.name] for c in columns
              if c.name.startswith("pair:") and not c.load_failed and not c.abort_reason}
    return [_pair_finding(a, stack.plugins, usable, pair_total) for a in attributions if a.kind == "interaction"]


def _run_label(cell: Cell) -> str:
    return " + ".join(LABELS[o] for o in sorted(cell.outcomes, key=PRECEDENCE.index))


def _unstable_note(labels: Sequence[str]) -> str:
    counts = ", ".join(f"{label} in {labels.count(label)} of {len(labels)}" for label in dict.fromkeys(labels))
    return (f"UNSTABLE: {len(labels)} solo runs of this plugin disagree ({counts}); its effect on this call may "
            "depend on state, timing or randomness, so no single run is the answer")


def mark_unstable_solos(grid: dict[str, dict[str, Cell]], repeats: Mapping[str, Sequence[ColumnRun]],
                        base: ColumnRun, unstable: frozenset[str]) -> None:
    """--repeat: a solo cell whose label differs between its runs is marked UNSTABLE (first run's label kept, every
    run's label listed). A repeat run that stopped or did not confirm its plugin is not compared."""
    for name, runs in repeats.items():
        extra = [_column_cells(r, base, unstable) for r in runs if not r.abort_reason and not r.load_failed]
        for cid, cell in list(grid.get(name, {}).items()):
            labels = [_run_label(cell)] + [_run_label(cells[cid]) for cells in extra if cid in cells]
            if len(set(labels)) > 1:
                grid[name][cid] = replace(cell, unstable=True, varied=tuple(labels),
                                          note="; ".join(filter(None, [cell.note, _unstable_note(labels)])))


def _with_repeat_count(a: Attribution, grid: Mapping[str, Mapping[str, Cell]],
                       repeats: Mapping[str, Sequence[ColumnRun]]) -> Attribution:
    """--repeat: a solo match whose repeated runs all agreed says how many runs showed it, not "one solo run"."""
    if a.kind != "solo-match":
        return a
    name = f"solo:{a.plugins[0]}"
    runs = 1 + sum(1 for r in repeats.get(name, ()) if not r.abort_reason and not r.load_failed)
    if runs == 1 or grid[name][a.call_id].varied:
        return a
    return replace(a, note=f"observed in all {runs} solo runs: {a.plugins[0]} (repeated with --repeat; a stateful "
                           "plugin can still differ in another drill)")


def effect_columns(m: Matrix) -> list[str]:
    """Solo columns with a plugin effect: a cell other than a quiet one (PASSED THROUGH, LOAD FAILED, NOT RUN, N/A)
    or a core effect."""
    return [name for name in m.columns if name.startswith("solo:")
            and any(c.primary not in QUIET and not c.core for (col, _cid), c in m.cells.items() if col == name)]


def repeat_columns(m: Matrix) -> list[str]:
    """What --repeat runs again: the solo columns with a plugin effect, and every confirmed solo column when an effect
    appears only in the full stack, since a stateful plugin that was quiet in its one solo run looks exactly like
    an interaction."""
    if not any(a.kind == "interaction" for a in m.attributions):
        return effect_columns(m)
    return [name for name in m.columns if name.startswith("solo:") and name not in m.load_failed]


def provisional(columns: Sequence[ColumnRun], baseline_repeat: ColumnRun | None,
                load_order: Sequence[str]) -> Matrix:
    """The matrix so far, without call texts: what the runner reads to decide which pair and repeat columns to run."""
    infos = [CallInfo(cid, obs.tool, "", "") for cid, obs in columns[0].observations.items()]
    return build_matrix(columns, baseline_repeat, {}, infos, "", "", 0.0, load_order=load_order)


def build_matrix(columns: Sequence[ColumnRun], baseline_repeat: ColumnRun | None,
                 crosscheck: Mapping[str, ColumnRun], calls: Sequence[CallInfo], hermes_version: str,
                 started_at: str, wall_s: float, skipped: Sequence[tuple[str, str]] = (),
                 needs_env: Sequence[str] = (), *, load_order: Sequence[str] | None = None,
                 unreplayable: Sequence[tuple[str, Sequence[str]]] = (), conditions_extra: Mapping[str, Any] | None = None,
                 options: Mapping[str, str] | None = None, repeats: Mapping[str, Sequence[ColumnRun]] | None = None,
                 pair_total: int | None = None) -> Matrix:
    """*repeats* (--repeat): extra runs per solo column name. *pair_total* (--pairs): how many pairs the stack has;
    the pair columns themselves arrive in *columns*, named pair:<a>+<b>."""
    base, others = columns[0], list(columns[1:])
    unstable = unstable_calls(base, baseline_repeat)
    grid: dict[str, dict[str, Cell]] = {"baseline": {cid: baseline_cell(o) for cid, o in base.observations.items()}}
    for col in others:
        grid[col.name] = _column_cells(col, base, unstable)
    for key in needs_env:
        grid[f"solo:{key}"] = _na_cells(key, calls)
    mark_unstable_solos(grid, repeats or {}, base, unstable)
    failed = {c.name: c.load_failed for c in list(columns) + list(crosscheck.values()) if c.load_failed}
    solos = {name.removeprefix("solo:"): cells for name, cells in grid.items() if name.startswith("solo:")}
    order = tuple(k for k in (load_order if load_order is not None else sorted(solos)) if k in solos)
    usable = {k: v for k, v in solos.items() if k not in needs_env and f"solo:{k}" not in failed}
    stack = grid.get("stack") or (next(iter(solos.values())) if len(solos) == 1 else {})
    attributions = attribute(stack, grid["baseline"], usable, tuple(k for k in order if k in usable)) if stack else []
    attributions = [_with_repeat_count(a, grid, repeats or {}) for a in attributions]
    cross = {k: _column_cells(c, c, frozenset()) for k, c in crosscheck.items() if not c.load_failed}
    findings = _unseen(grid.get("stack", {}), cross, attributions) + _masked(grid.get("stack", {}), usable, attributions) \
        if "stack" in grid else []
    findings += pair_findings(columns, grid, attributions, pair_total) if pair_total is not None else []
    findings += _escapes(list(columns) + list(crosscheck.values()))
    connects = [c for col in list(columns) + list(crosscheck.values()) for c in col.connects]
    steps = sum(c.scripted_steps for c in list(columns) + list(crosscheck.values()))
    extra = {"model": model_line(steps, count_test_calls(calls)), "coverage": coverage_line(columns, needs_env, skipped),
             "order_note": order_note(list(columns) + list(crosscheck.values())),
             **dict(conditions_extra or {})}
    conditions = DrillConditions(hermes_version, count_test_calls(calls), len(solos), order, len(connects),
                                 sum(1 for _a, loop in connects if not loop), wall_s, started_at, **extra)
    cells = {(col, cid): cell for col, row in grid.items() for cid, cell in row.items()}
    aborted = {c.name: c.abort_reason for c in list(columns) + list(crosscheck.values())
               if c.abort_reason and c.abort_reason != "ESCAPED"}
    observations = {c.name: c.observations for c in list(columns) + list(crosscheck.values())}
    return Matrix(conditions, tuple(calls), tuple(grid), cells, tuple(attributions), tuple(findings),
                  observations, unstable, tuple(skipped), tuple(needs_env), aborted,
                  {k: tuple(v) for k, v in failed.items()}, tuple((i, tuple(r)) for i, r in unreplayable),
                  dict(options or {}))


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def distinct_effects(m: Matrix) -> frozenset[tuple[str, Outcome]]:
    """Each (call, outcome) a plugin produced, counted once however many columns show it (a solo effect that also
    appears in the full stack is one effect, not two). Core effects and the baseline are not plugin effects."""
    return frozenset((cid, outcome) for (col, cid), cell in m.cells.items() if col != "baseline" and not cell.core
                     for outcome in cell.outcomes if outcome not in QUIET)


def tally(m: Matrix) -> str:
    return (f"{_plural(m.conditions.n_calls, 'test call')} · {_plural(m.conditions.n_plugins, 'plugin')} · "
            f"{_plural(len(distinct_effects(m)), 'distinct effect')} recorded · "
            f"{_plural(len(m.findings), 'cross-plugin finding')}")


def check_expectations(m: Matrix) -> tuple[list[tuple[str, str, tuple[str, ...], str]], list[tuple[str, str]]]:
    """Contract mode (--check): (unmet [(call, column, allowed, actual)], not evaluated [(call, column)])."""
    unmet, missing = [], []
    for call in m.calls:
        for column, allowed in (call.expect or {}).items():
            cell = m.cells.get((column, call.id))
            if cell is None:
                missing.append((call.id, column))
            elif cell.primary.value not in allowed:
                unmet.append((call.id, column, tuple(allowed), cell.primary.value))
    return unmet, missing


# Report comparison (--compare, --since-last, `diff`): pure, over two matrices already loaded.


@dataclass(frozen=True)
class CellChange:
    column: str
    call_id: str
    before: Cell | None  # None: the cell is not in the earlier report
    after: Cell | None  # None: the cell is not in the later report


@dataclass(frozen=True)
class MatrixDiff:
    plugins_added: tuple[str, ...] = ()
    plugins_removed: tuple[str, ...] = ()
    calls_added: tuple[str, ...] = ()
    calls_removed: tuple[str, ...] = ()
    cells: tuple[CellChange, ...] = ()
    findings_new: tuple[Finding, ...] = ()
    findings_resolved: tuple[Finding, ...] = ()

    @property
    def changed(self) -> bool:
        return any((self.plugins_added, self.plugins_removed, self.calls_added, self.calls_removed, self.cells,
                    self.findings_new, self.findings_resolved))


def subject_plugins(m: Matrix) -> tuple[str, ...]:
    """The plugins a drill tested: the load order given to the child plus its solo columns (N/A ones included)."""
    solos = [c.removeprefix("solo:") for c in m.columns if c.startswith("solo:")]
    return tuple(dict.fromkeys([*m.conditions.load_order, *solos]))


def _cell_signature(cell: Cell) -> tuple[Any, ...]:
    """What a reader of the matrix sees in a cell: its outcomes and its (core) and unstable marks, not its note."""
    return tuple(sorted(o.value for o in cell.outcomes)), cell.core, cell.core_also, cell.unstable


def _added_and_removed(before: Sequence[str], after: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return tuple(x for x in after if x not in set(before)), tuple(x for x in before if x not in set(after))


def _changed_cells(old: Matrix, new: Matrix, calls: Sequence[str], skip_columns: set[str]) -> tuple[CellChange, ...]:
    """Cells whose outcome changed, on the calls both reports ran. The column of an added or removed plugin is
    reported once, as that plugin, not cell by cell."""
    columns = [c for c in dict.fromkeys([*old.columns, *new.columns]) if c not in skip_columns]
    changes = []
    for cid in calls:
        for column in columns:
            before, after = old.cells.get((column, cid)), new.cells.get((column, cid))
            if before is None and after is None:
                continue
            if before is None or after is None or _cell_signature(before) != _cell_signature(after):
                changes.append(CellChange(column, cid, before, after))
    return tuple(changes)


def _finding_key(f: Finding) -> tuple[str, str, tuple[str, ...]]:
    return f.kind, f.call_id, tuple(f.plugins)


def compare(old: Matrix, new: Matrix) -> MatrixDiff:
    """Only what changed from *old* to *new*: plugins, test calls, cell outcomes and cross-plugin findings."""
    added, removed = _added_and_removed(subject_plugins(old), subject_plugins(new))
    calls_added, calls_removed = _added_and_removed([c.id for c in old.calls], [c.id for c in new.calls])
    shared_calls = [c.id for c in new.calls if c.id not in set(calls_added)]
    skip = {f"solo:{p}" for p in (*added, *removed)}
    old_keys, new_keys = {_finding_key(f) for f in old.findings}, {_finding_key(f) for f in new.findings}
    return MatrixDiff(added, removed, calls_added, calls_removed, _changed_cells(old, new, shared_calls, skip),
                      tuple(f for f in new.findings if _finding_key(f) not in old_keys),
                      tuple(f for f in old.findings if _finding_key(f) not in new_keys))


def quiet_row(m: Matrix, call_id: str) -> bool:
    """--focus hides a row only when every cell in it is PASSED THROUGH (N/A cells, which never ran, do not count
    against it); one cell with any other outcome, or a cell not filled in yet, keeps the row."""
    row = [m.cells.get((column, call_id)) for column in m.columns]
    if any(cell is None for cell in row):
        return False
    kinds = {outcome for cell in row if cell is not None for outcome in cell.outcomes}
    return Outcome.PASSED_THROUGH in kinds and kinds <= {Outcome.PASSED_THROUGH, Outcome.NOT_APPLICABLE}
