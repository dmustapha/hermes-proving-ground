# File: proving_ground/matrix.py  (part 1 of 3: types and normalization)
# [VERIFIED by the §9.1 run] pure logic over the event format of §3.5. Phrases cited inline are [VERIFIED] at their file:line.
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
APPROVAL_PHRASES = ("runs without a user present to approve it", "Command denied")  # approval.py:703, P4
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
    PASSED_THROUGH = "PASSED_THROUGH"
    LOAD_FAILED = "LOAD_FAILED"
    NOT_RUN = "NOT_RUN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


PRECEDENCE: tuple[Outcome, ...] = tuple(Outcome)
LABELS: dict[Outcome, str] = {
    Outcome.ESCAPED: "ESCAPED", Outcome.ERRORED: "ERRORED", Outcome.BLOCKED: "BLOCKED",
    Outcome.APPROVAL_GATED: "APPROVAL-GATED", Outcome.REWROTE: "REWROTE",
    Outcome.RESULT_CHANGED: "RESULT CHANGED", Outcome.PASSED_THROUGH: "PASSED THROUGH",
    Outcome.LOAD_FAILED: "LOAD FAILED", Outcome.NOT_RUN: "NOT RUN", Outcome.NOT_APPLICABLE: "N/A",
}
QUIET = frozenset({Outcome.PASSED_THROUGH, Outcome.LOAD_FAILED, Outcome.NOT_RUN, Outcome.NOT_APPLICABLE})
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
    Never by call order: a plugin that adds or drops a call would shift every later call (perspective point 4)."""
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
    injected = normalize_value(call.get("args") or {}, sandbox, url)  # the plan is authoritative (ARCH-F7)
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
                       recorded: Sequence[str], abort_reason: str | None, wall_s: float) -> ColumnRun:
    slots, stray = _slots(plan_calls, events)
    if stray and abort_reason is None:
        abort_reason = mismatch_reason(stray)
    stray_escapes = tuple(tuple(str(r) for r in e.get("reasons") or ()) for e in stray if e.get("ev") == "escaped")
    observations = {str(c["id"]): _observation(c, slots[str(c["id"])], sandbox, recorder_url, recorded)
                    for c in plan_calls}
    connects = tuple((str(e.get("address")), bool(e.get("loopback"))) for e in events if e.get("ev") == "connect")
    steps = sum(1 for e in events if e.get("ev") == "llm_execution")
    return ColumnRun(name, plugins, observations, abort_reason, wall_s, connects,
                     unconfirmed_plugins(plugins, events), steps, stray_escapes)


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
    """One finding per blocker x call: the other plugins' effects are consequences of one block (point b)."""
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
    """Survivor: a plugin not positively confirmed loaded is never rendered as PASSED THROUGH."""
    note = f"not confirmed loaded in the child: {', '.join(col.load_failed)}"
    if col.name.startswith("solo:") or cell.primary in QUIET:
        return Cell(cell.column, cell.call_id, (Outcome.LOAD_FAILED,), note=note)
    return replace(cell, note="; ".join(filter(None, [cell.note, note])))


def _column_cells(col: ColumnRun, base: ColumnRun, unstable: frozenset[str]) -> dict[str, Cell]:
    cells = {cid: classify(col.name, obs, base.observations[cid], cid in unstable)
             for cid, obs in col.observations.items()}
    return {cid: _load_failed_cell(c, col) for cid, c in cells.items()} if col.load_failed else cells


def _na_cells(key: str, calls: Sequence[CallInfo]) -> dict[str, Cell]:
    return {c.id: Cell(f"solo:{key}", c.id, (Outcome.NOT_APPLICABLE,),
                       note="declares requires_env; the sandbox has no credentials") for c in calls}


def coverage_line(columns: Sequence[ColumnRun], needs_env: Sequence[str], skipped: Sequence[tuple[str, str]]) -> str:
    solos = [c for c in columns if c.name.startswith("solo:")]
    failed = sorted({k for c in columns for k in c.load_failed})
    executed = [c.name.removeprefix("solo:") for c in solos if not c.load_failed]
    return (f"executed: {len(executed)} · unavailable (requires_env): {len(needs_env)} · "
            f"dependency-skipped / not a directory plugin: {len(skipped)} · load-failed: {len(failed)}"
            + (f" ({', '.join(failed)})" if failed else ""))


def build_matrix(columns: Sequence[ColumnRun], baseline_repeat: ColumnRun | None,
                 crosscheck: Mapping[str, ColumnRun], calls: Sequence[CallInfo], hermes_version: str,
                 started_at: str, wall_s: float, skipped: Sequence[tuple[str, str]] = (),
                 needs_env: Sequence[str] = (), *, load_order: Sequence[str] | None = None,
                 unreplayable: Sequence[tuple[str, Sequence[str]]] = (), conditions_extra: Mapping[str, Any] | None = None
                 ) -> Matrix:
    base, others = columns[0], list(columns[1:])
    unstable = unstable_calls(base, baseline_repeat)
    grid: dict[str, dict[str, Cell]] = {"baseline": {cid: classify_baseline(o) for cid, o in base.observations.items()}}
    for col in others:
        grid[col.name] = _column_cells(col, base, unstable)
    for key in needs_env:
        grid[f"solo:{key}"] = _na_cells(key, calls)
    failed = {c.name: c.load_failed for c in list(columns) + list(crosscheck.values()) if c.load_failed}
    solos = {name.removeprefix("solo:"): cells for name, cells in grid.items() if name.startswith("solo:")}
    order = tuple(k for k in (load_order if load_order is not None else sorted(solos)) if k in solos)
    usable = {k: v for k, v in solos.items() if k not in needs_env and f"solo:{k}" not in failed}
    stack = grid.get("stack") or (next(iter(solos.values())) if len(solos) == 1 else {})
    attributions = attribute(stack, grid["baseline"], usable, tuple(k for k in order if k in usable)) if stack else []
    cross = {k: _column_cells(c, c, frozenset()) for k, c in crosscheck.items() if not c.load_failed}
    findings = _unseen(grid.get("stack", {}), cross, attributions) + _masked(grid.get("stack", {}), usable, attributions) \
        if "stack" in grid else []
    findings += _escapes(list(columns) + list(crosscheck.values()))
    connects = [c for col in list(columns) + list(crosscheck.values()) for c in col.connects]
    steps = sum(c.scripted_steps for c in list(columns) + list(crosscheck.values()))
    extra = {"model": f"NOT CALLED (scripted turn: {steps} llm_execution steps answered by PROVING GROUND, "
                      "0 provider calls)", "coverage": coverage_line(columns, needs_env, skipped),
             **dict(conditions_extra or {})}
    conditions = DrillConditions(hermes_version, len(calls), len(solos), order, len(connects),
                                 sum(1 for _a, loop in connects if not loop), wall_s, started_at, **extra)
    cells = {(col, cid): cell for col, row in grid.items() for cid, cell in row.items()}
    aborted = {c.name: c.abort_reason for c in list(columns) + list(crosscheck.values())
               if c.abort_reason and c.abort_reason != "ESCAPED"}
    observations = {c.name: c.observations for c in list(columns) + list(crosscheck.values())}
    return Matrix(conditions, tuple(calls), tuple(grid), cells, tuple(attributions), tuple(findings),
                  observations, unstable, tuple(skipped), tuple(needs_env), aborted,
                  {k: tuple(v) for k, v in failed.items()}, tuple((i, tuple(r)) for i, r in unreplayable))


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def tally(m: Matrix) -> str:
    behaviours = sum(1 for (col, _c), cell in m.cells.items()
                     if col != "baseline" and cell.primary not in QUIET and not cell.core)
    return (f"{_plural(m.conditions.n_calls, 'test call')} · {_plural(m.conditions.n_plugins, 'plugin')} · "
            f"{_plural(behaviours, 'behaviour')} recorded · {_plural(len(m.findings), 'cross-plugin finding')}")


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
