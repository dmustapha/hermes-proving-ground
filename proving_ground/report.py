# File: proving_ground/report.py  (part 1 of 3: wording, JSON serialization)
# rich==14.3.4 is pinned in Hermes core (pyproject.toml:52); rich.table.Table / rich.console.Console are its
# public API. Pure: returns strings, never writes files.
"""Render a Matrix as a terminal table, Markdown, self-contained HTML, JSON, or one cell's detail."""
from __future__ import annotations

import difflib
import html
import io
import re
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Mapping, Sequence

from . import matrix as mx

REPORT_SCHEMA = "provingground.report.v1"
GENERATOR = "proving-ground 0.1.0"  # plugin.yaml version; tests/test_report_loading.py keeps them equal
HEADLINE = "PROVING GROUND · a wiring test for your plugin stack"
OBSERVED = "Observed on {n} synthetic inputs in a sandbox."
SCOPE_NOTE = ("This report describes what your plugins did to these specific test calls. "
              "It does not grade your stack, and results depend on each plugin's configuration.")
FORBIDDEN_CLAIMS = (re.compile(r"\bsafe\b", re.I), re.compile(r"\bsecure[ds]?\b", re.I),
                    re.compile(r"\bprotected\b", re.I), re.compile(r"\bguarantee", re.I),
                    re.compile(r"\bcertif", re.I), re.compile(r"\bpassed\b(?!\s+through)", re.I))
MAX_DETAIL_LINES = 40
MAX_DETAIL_CHARS = 8192  # per detail section; a single-line JSON result is otherwise unbounded
LIVE_BANNER = "model: NOT CALLED · provider: none · no provider keys · approvals: manual, headless deny"
STYLE = {mx.Outcome.BLOCKED: "bold cyan", mx.Outcome.REWROTE: "bold yellow", mx.Outcome.RESULT_CHANGED: "bold magenta",
         mx.Outcome.APPROVAL_GATED: "bold blue", mx.Outcome.PASSED_THROUGH: "dim", mx.Outcome.ESCAPED: "bold red",
         mx.Outcome.ERRORED: "red", mx.Outcome.LOAD_FAILED: "red", mx.Outcome.NOT_RUN: "dim",
         mx.Outcome.NOT_APPLICABLE: "dim", mx.Outcome.CONTEXT_ADDED: "bold green",
         mx.Outcome.REPLY_CHANGED: "bold green", mx.Outcome.EXECUTION_REPLACED: "bold cyan",
         mx.Outcome.REQUEST_REWRITTEN: "bold yellow", mx.Outcome.MODEL_REPLY_CHANGED: "bold green"}
QUOTE_PREFIX = "│ "  # every line of plugin-authored text (raw output, block messages) is quoted, never PG's words
QUOTE_TITLE = "quoted from the plugin stack, not PROVING GROUND's words"
_HTML_QUOTED = re.compile(r'<pre class="quoted">.*?</pre>', re.S)
ANSI = {"bold cyan": "\033[1;36m", "bold yellow": "\033[1;33m", "bold magenta": "\033[1;35m", "bold blue": "\033[1;34m",
        "dim": "\033[2m", "bold red": "\033[1;31m", "red": "\033[31m", "bold green": "\033[1;32m"}
RESET = "\033[0m"


def strip_quoted(text: str) -> str:
    """PG-authored text only: drop quoted plugin lines (terminal/Markdown) and quoted <pre> blocks (HTML)."""
    text = _HTML_QUOTED.sub("", text)
    return "\n".join(line for line in text.splitlines() if not line.lstrip("- ").startswith(QUOTE_PREFIX))


def forbidden_claims(text: str) -> list[str]:
    """Forbidden grading words in PROVING GROUND's own words; quoted plugin output is exempt."""
    own = strip_quoted(text)
    return [p.pattern for p in FORBIDDEN_CLAIMS if p.search(own)]


def quoted(lines: list[str]) -> list[str]:
    return [QUOTE_PREFIX + line for line in lines]


def provenance(column: str) -> str:
    """Mandatory provenance of every cell: baseline, solo, stack or cross-check."""
    if column in ("baseline", "baseline-repeat"):
        return "baseline"
    return {"solo": "solo", "crosscheck": "cross-check", "pair": "pair"}.get(column.partition(":")[0], "stack")


def cell_label(cell: mx.Cell) -> str:
    label = " + ".join(mx.LABELS[o] for o in sorted(cell.outcomes, key=mx.PRECEDENCE.index))
    core = " (core + plugin)" if cell.core_also or (cell.core and cell.added) else " (core)" if cell.core else ""
    live = f" ({cell.liveness})" if cell.liveness else ""
    return label + live + core + (" UNSTABLE" if cell.varied else " ~" if cell.unstable else "")


def _mark_for(f: mx.Finding) -> tuple[str, str] | None:
    """(phrase, plugin) for the stack cell a cross-plugin finding is about; an ESCAPED cell already says so."""
    if f.kind == "REWRITE_UNSEEN_BY" and f.plugins:
        return "unseen by", f.plugins[-1]
    if f.kind == "MASKED_IN_STACK":
        return "masks", f.plugins[0] if len(f.plugins) == 1 else "a solo effect"
    if f.kind == "STACK_ONLY_INTERACTION":
        return "only in combination", ""
    return None


def finding_marks(m: mx.Matrix) -> dict[tuple[str, str], str]:
    """The in-cell marker of every stack cell that carries a finding, e.g. "unseen by fx_guard"."""
    grouped: dict[tuple[str, str], dict[str, list[str]]] = {}
    for f in m.findings:
        mark = _mark_for(f)
        if mark is None or ("stack", f.call_id) not in m.cells:
            continue
        names = grouped.setdefault(("stack", f.call_id), {}).setdefault(mark[0], [])
        names += [mark[1]] if mark[1] and mark[1] not in names else []
    return {key: " · ".join(f"{phrase} {', '.join(names)}".strip() for phrase, names in marks.items())
            for key, marks in grouped.items()}


def marked_label(cell: mx.Cell, marks: Mapping[tuple[str, str], str]) -> str:
    """A matrix cell as the views print it: the outcome, then the finding it carries ("REWROTE · unseen by X")."""
    mark = marks.get((cell.column, cell.call_id))
    return cell_label(cell) + (f" · {mark}" if mark else "")


def _plugin_title(name: str) -> str:
    """A solo column is titled by its plugin, a pair column (pair:a+b) as "a + b"."""
    if name.startswith("pair:"):
        return " + ".join(mx.pair_keys(name))
    return name.removeprefix("solo:")


def column_title(name: str) -> str:
    return {"baseline": "baseline (no plugins)", "stack": "FULL STACK"}.get(name, _plugin_title(name))


def header_title(m: mx.Matrix, name: str) -> str:
    """A column's header: a solo column that also loads its requires_plugins says so ("<key> (+ deps)")."""
    title = column_title(name)
    return f"{title} (+ deps)" if name.startswith("solo:") and len(m.column_plugins.get(name, ())) > 1 else title


def header_titles(m: mx.Matrix) -> dict[str, str]:
    return {name: header_title(m, name) for name in m.columns}


def column_hooks(m: mx.Matrix, name: str) -> list[tuple[str, tuple[str, ...]]]:
    """(plugin, declared hooks) for each plugin loaded in a column, from its manifest's provides_hooks."""
    return [(key, tuple(m.declared_hooks.get(key, ()))) for key in m.column_plugins.get(name, ())]


def hooks_summary(m: mx.Matrix, name: str) -> str:
    """One compact line under a column header: the hooks its plugins declare (a declaration, not an effect)."""
    if name == "baseline":
        return "no plugins"
    hooks = sorted({h for _key, declared in column_hooks(m, name) for h in declared})
    return ("hooks: " + ", ".join(hooks)) if hooks else "hooks: none declared"


def hooks_legend(m: mx.Matrix) -> list[str]:
    """`show --plugins`: every tested plugin, its declared hooks, and the plugins each column loaded."""
    keys = list(dict.fromkeys(k for name in m.columns for k in m.column_plugins.get(name, ())))
    lines = ["declared hooks (each plugin's plugin.yaml provides_hooks; what it declares, not what it did):"]
    lines += [f"  {key}: {', '.join(m.declared_hooks.get(key, ())) or 'none declared'}" for key in keys]
    lines += ["columns (the plugins loaded in each):"]
    lines += [f"  {header_title(m, name)}: {', '.join(m.column_plugins.get(name, ())) or 'no plugins'}"
              for name in m.columns]
    if not keys:
        return ["this report records no declared hooks (it predates them, or tested no plugin)"]
    return lines + (["registered in the child, and what fired in this drill:", *census_lines(m)] if m.census else [])


def _names(entries: Sequence[str]) -> str:
    return ", ".join(entries) or "none"


def census_line(key: str, c: mx.PluginCensus) -> str:
    """One plugin's registrations against what fired: hooks and middleware that never fired are UNTESTED here, and
    registered tools and commands are not exercised by the drill."""
    if c.registered is None:
        return f"{key}: registrations unknown (the child could not read them)"
    hooks = [*c.registered.get("hooks", ()), *(f"{k} (middleware)" for k in c.registered.get("middleware", ()))]
    hooks = list(dict.fromkeys(hooks))
    seen = {n.split(" ", 1)[1] + (" (middleware)" if n.startswith("middleware ") else "") for n in c.fired}
    if not c.watched:
        return (f"{key}: hooks registered: {_names(hooks)}; fired: not observed (a callback could not be matched "
                "to this plugin)" + _extras(c))
    fired = [h for h in hooks if h in seen]
    never = [h for h in hooks if h not in seen]
    late = " (watched from the first model step, so a hook that fires before it is listed as never fired)" if c.late else ""
    return (f"{key}: hooks registered: {_names(hooks)}; fired: {_names(fired)}; never fired in this drill "
            f"(UNTESTED): {_names(never)}{late}" + _extras(c))


def _extras(c: mx.PluginCensus) -> str:
    parts = []
    if c.registered and c.registered.get("tools"):
        parts.append(f"registered tools: {_names(c.registered['tools'])} (not exercised)")
    if c.registered and c.registered.get("commands"):
        parts.append(f"registered commands: {_names(c.registered['commands'])} (not exercised)")
    if c.inner and c.registered and "llm_execution" in c.registered.get("middleware", ()):
        parts.append("loaded after PROVING GROUND (requires_plugins), so its llm_execution middleware is inside "
                     "PROVING GROUND's scripted frame and is not reached")
    return "".join(f"; {p}" for p in parts)


def census_lines(m: mx.Matrix) -> list[str]:
    return [census_line(key, c) for key, c in m.census.items()]


def short_title(name: str) -> str:
    return {"baseline": "baseline", "stack": "FULL STACK"}.get(name, _plugin_title(name))


def _source(m: mx.Matrix, cell: mx.Cell) -> str:
    """Who the effect is attributed to: the solo column's plugin, or the stack attribution's plugins."""
    if cell.core:
        return ""
    if cell.column.startswith(("solo:", "pair:")):
        return _plugin_title(cell.column)
    return ", ".join(p for a in m.attributions if a.call_id == cell.call_id for p in a.plugins)


def notable_cells(m: mx.Matrix) -> list[str]:
    """Every cell that ran and is not PASSED THROUGH, as `<CALL> --column <COLUMN>` plus its label and source."""
    quiet = (mx.Outcome.PASSED_THROUGH, mx.Outcome.NOT_RUN, mx.Outcome.NOT_APPLICABLE)
    cells = [m.cells[(name, call.id)] for call in m.calls for name in m.columns
             if (name, call.id) in m.cells and m.cells[(name, call.id)].primary not in quiet]
    if not cells:
        return []
    width = max(len(f"{c.call_id} --column {c.column}") for c in cells) + 2
    return [f"{c.call_id} --column {c.column}".ljust(width) + cell_label(c)
            + (f"  ({_source(m, c)})" if _source(m, c) else "") for c in cells]


def path_rules(paths: Mapping[str, str]) -> list[tuple[re.Pattern[str], str]]:
    """{path: token} as whole-path patterns, longest first (a temp dir under home is replaced before home)."""
    ordered = sorted((p for p in paths if len(p) > 1), key=len, reverse=True)
    return [(re.compile(re.escape(p) + r"(?![A-Za-z0-9_.-])"), paths[p]) for p in ordered]


def scrub_paths(value: Any, rules: Sequence[tuple[re.Pattern[str], str]]) -> Any:
    """Replace private paths in every string of a JSON-shaped value (keys are left alone)."""
    if isinstance(value, str):
        for pattern, token in rules:
            value = pattern.sub(token, value)
        return value
    if isinstance(value, dict):
        return {k: scrub_paths(v, rules) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_paths(v, rules) for v in value]
    return value


def matrix_to_dict(m: mx.Matrix) -> dict[str, Any]:
    cells = [{"column": c.column, "call_id": c.call_id, "outcomes": [o.value for o in c.outcomes],
              "primary": c.primary.value, "hop": c.hop, "core": c.core, "core_also": c.core_also,
              "unstable": c.unstable, "note": c.note,
              "provenance": provenance(c.column), **({"varied": list(c.varied)} if c.varied else {}),
              **({"added": [o.value for o in c.added]} if c.added else {}),
              **({"liveness": c.liveness} if c.liveness else {})}
             for c in m.cells.values()]
    calls = [{**asdict(c), "expect": {k: list(v) for k, v in (c.expect or {}).items()}} for c in m.calls]
    return {"schema": REPORT_SCHEMA, "generator": GENERATOR, "headline": HEADLINE,
            "observed": OBSERVED.format(n=m.conditions.n_calls),
            "tally": mx.tally(m), "conditions": asdict(m.conditions), "calls": calls,
            "columns": list(m.columns), "cells": cells,
            "attributions": [{**asdict(a), "outcome": a.outcome.value} for a in m.attributions],
            "findings": [asdict(f) for f in m.findings],
            "observations": {col: {cid: asdict(o) for cid, o in obs.items()} for col, obs in m.observations.items()},
            "unstable": sorted(m.unstable), "skipped": [list(s) for s in m.skipped],
            "needs_env": list(m.needs_env), "aborted": dict(m.aborted),
            "load_failed": {k: list(v) for k, v in m.load_failed.items()},
            "unreplayable": [[cid, list(r)] for cid, r in m.unreplayable],
            **({"options": dict(m.options)} if m.options else {}),
            **({"column_meta": _column_meta(m)} if m.column_plugins else {}),
            **({"census": {k: _census_dict(c) for k, c in m.census.items()}} if m.census else {})}


def _census_dict(c: mx.PluginCensus) -> dict[str, Any]:
    return {"registered": {k: list(v) for k, v in c.registered.items()} if c.registered is not None else None,
            "fired": list(c.fired), "watched": c.watched, "late": c.late, "inner": c.inner}


def _census_from(raw: Mapping[str, Any]) -> dict[str, mx.PluginCensus]:
    out = {}
    for key, c in raw.items():
        registered = c.get("registered")
        out[str(key)] = mx.PluginCensus({k: tuple(v) for k, v in registered.items()} if registered is not None else None,
                                        tuple(c.get("fired") or ()), bool(c.get("watched")), bool(c.get("late")),
                                        bool(c.get("inner")))
    return out


def _column_meta(m: mx.Matrix) -> dict[str, Any]:
    return {name: {"title": header_title(m, name), "plugins": list(m.column_plugins.get(name, ())),
                   "declared_hooks": {k: list(h) for k, h in column_hooks(m, name)}} for name in m.columns}  # a default drill's report keeps its old keys


def _cell_from(raw: Mapping[str, Any]) -> mx.Cell:
    return mx.Cell(raw["column"], raw["call_id"], tuple(mx.Outcome(o) for o in raw["outcomes"]),
                   raw.get("hop"), bool(raw.get("core")), bool(raw.get("unstable")), raw.get("note", ""),
                   bool(raw.get("core_also")), tuple(str(v) for v in raw.get("varied") or ()),
                   tuple(mx.Outcome(o) for o in raw.get("added") or ()), str(raw.get("liveness") or ""))


def _known(cls: type, raw: Mapping[str, Any]) -> dict[str, Any]:
    """Only the fields this version knows: a report written by a newer version (one extra key) still loads."""
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in raw.items() if k in names}


def _call_from(raw: Mapping[str, Any]) -> mx.CallInfo:
    return mx.CallInfo(**{**_known(mx.CallInfo, raw),
                          "expect": {k: tuple(v) for k, v in (raw.get("expect") or {}).items()}})


def _obs_from(raw: Mapping[str, Any]) -> mx.CallObservation:
    tuples = ("escaped_reasons", "request_rewrite", "reply_changes", "middleware_trace")
    return mx.CallObservation(**{**_known(mx.CallObservation, raw),
                                 **{k: tuple(raw.get(k) or ()) for k in tuples}})


def matrix_from_dict(data: Mapping[str, Any]) -> mx.Matrix:
    if not isinstance(data, Mapping) or data.get("schema") != REPORT_SCHEMA:
        raise ValueError(f"not a {REPORT_SCHEMA} document")
    cond = data["conditions"]
    conditions = mx.DrillConditions(**{**_known(mx.DrillConditions, cond), "load_order": tuple(cond["load_order"])})
    cells = {(c["column"], c["call_id"]): _cell_from(c) for c in data["cells"]}
    attributions = tuple(mx.Attribution(a["call_id"], mx.Outcome(a["outcome"]), a["kind"], tuple(a["plugins"]),
                                        a.get("note", "")) for a in data["attributions"])
    findings = tuple(mx.Finding(f["kind"], f["call_id"], tuple(f["plugins"]), f["text"]) for f in data["findings"])
    observations = {col: {cid: _obs_from(o) for cid, o in obs.items()} for col, obs in data["observations"].items()}
    return mx.Matrix(conditions, tuple(_call_from(c) for c in data["calls"]), tuple(data["columns"]), cells,
                     attributions, findings, observations, frozenset(data.get("unstable") or ()),
                     tuple(tuple(s) for s in data.get("skipped") or ()), tuple(data.get("needs_env") or ()),
                     dict(data.get("aborted") or {}),
                     {k: tuple(v) for k, v in (data.get("load_failed") or {}).items()},
                     tuple((cid, tuple(r)) for cid, r in data.get("unreplayable") or ()),
                     {str(k): str(v) for k, v in (data.get("options") or {}).items()},
                     *_meta_from(data.get("column_meta") or {}), _census_from(data.get("census") or {}))


def _meta_from(meta: Mapping[str, Any]) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    plugins = {str(col): tuple(v.get("plugins") or ()) for col, v in meta.items()}
    hooks = {str(k): tuple(h) for v in meta.values() for k, h in (v.get("declared_hooks") or {}).items()}
    return plugins, hooks

# File: proving_ground/report.py  (part 2 of 3: terminal table, Markdown, cell detail)


def conditions_lines(m: mx.Matrix) -> list[str]:
    c = m.conditions
    network = f"{c.connects_total} connects, {c.connects_non_loopback} outside loopback"
    left = ("not measured" if c.leftover_processes < 0 else
            f"{c.leftover_processes} drill processes, {c.leftover_roots} drill roots left after the run")
    return [f"model: {c.model}", f"provider: {c.provider}", f"approvals: {c.approvals}",
            f"credentials: {c.credentials}",
            f"network seen inside the child Hermes process (not its subprocesses): {network}",
            f"sandbox: {c.sandbox}", f"Hermes: {c.hermes_version}", c.baseline_note,
            f"load order given to the child (requires_plugins first, then alphabetical): {', '.join(c.load_order) or 'none'}",
            *([f"load order mismatch: {c.order_note}"] if c.order_note else []),
            f"coverage: {c.coverage or 'n/a'}", *[f"{name}: {note}" for name, note in m.options.items()],
            f"cleanup: {left}",
            f"wall time: {c.wall_s:.1f}s · started {c.started_at}"]


def _rich_table(calls: Sequence[mx.CallInfo], columns: Sequence[str],
                cells: Mapping[tuple[str, str], mx.Cell], call_width: int | None = None,
                observed: int | None = None, titles: Mapping[str, str] | None = None,
                marks: Mapping[tuple[str, str], str] | None = None) -> Any:
    """Outcome labels wrap only at their "(core)" suffix; the call column wraps (to call_width) on narrow terminals.
    *observed* is the drill's call count when --focus prints fewer rows than the drill ran. With *marks* (the final
    report) the headline is printed above the findings instead, so the table has no title of its own."""
    from rich import box
    from rich.markup import escape
    from rich.table import Table

    n = mx.count_test_calls(calls) if observed is None else observed
    title = None if marks is not None else f"{HEADLINE}\n{OBSERVED.format(n=n)}"
    table = Table(title=title, box=box.SIMPLE_HEAD, expand=False,
                  title_style="bold", header_style="bold", pad_edge=False)
    table.add_column("test call", no_wrap=call_width is None, width=call_width)
    for name in columns:
        table.add_column((titles or {}).get(name) or column_title(name), min_width=14, overflow="fold")
    for call in calls:
        row = [f"[bold]{call.id}[/] {call.subject}"]
        for name in columns:
            cell = cells.get((name, call.id))
            mark = (marks or {}).get((name, call.id))
            text = f"[{STYLE[cell.primary]}]{cell_label(cell)}[/]" if cell else "[dim]…[/]"
            row.append(text + (f" [bold]· {escape(mark)}[/]" if mark else ""))
        table.add_row(*row)
    return table


def _outcome_columns_width(columns: Sequence[str], titles: Mapping[str, str] | None = None) -> int:
    """Width the outcome columns need without splitting a word (rich's Measurement ignores min_width)."""
    words = [max([14] + [len(word) for word in ((titles or {}).get(n) or column_title(n)).split()]) for n in columns]
    return sum(words) + 3 * len(columns) + 2  # 2 padding + 1 separator per column, 2 edge characters


def display_order(columns: Sequence[str], width: int, titles: Mapping[str, str] | None = None) -> list[str]:
    """When every column cannot fit, FULL STACK moves next to the baseline so the column the report exists for
    is never the one cropped; the solos follow."""
    if "stack" not in columns or _outcome_columns_width(columns, titles) <= width - 24:
        return list(columns)
    return [c for c in columns if c == "baseline"] + ["stack"] + [c for c in columns if c not in ("baseline", "stack")]


def _print_table(console: Any, calls: Sequence[mx.CallInfo], columns: Sequence[str],
                 cells: Mapping[tuple[str, str], mx.Cell], observed: int | None = None,
                 titles: Mapping[str, str] | None = None, marks: Mapping[tuple[str, str], str] | None = None) -> None:
    columns = display_order(columns, console.width, titles)
    longest_call = max([len("test call")] + [len(f"{c.id} {c.subject}") for c in calls])
    budget = console.width - _outcome_columns_width(columns, titles)
    call_width = None if longest_call <= budget else max(8, budget)
    console.print(_rich_table(calls, columns, cells, call_width, observed, titles, marks))


def shown_calls(m: mx.Matrix, focus: bool) -> tuple[mx.CallInfo, ...]:
    """The rows a view prints: every call, or with --focus only the rows where some cell is not PASSED THROUGH."""
    return tuple(c for c in m.calls if not focus or not mx.quiet_row(m, c.id))


def focus_line(m: mx.Matrix) -> str:
    """Names what --focus left out, so a shorter table is never read as a shorter drill."""
    hidden = [c.id for c in m.calls if mx.quiet_row(m, c.id)]
    if not hidden:
        return "focus: no rows hidden; every row has a cell that is not PASSED THROUGH"
    return (f"focus: {len(hidden)} of {len(m.calls)} rows hidden, every cell in them PASSED THROUGH "
            f"(N/A cells aside): {', '.join(hidden)}")


def _plain_table(m: mx.Matrix, color: bool, calls: Sequence[mx.CallInfo] | None = None) -> str:
    titles, marks = header_titles(m), finding_marks(m)
    width = max(14, *(len(titles[n]) for n in m.columns)) + 2
    head = "test call".ljust(34) + "".join(titles[n].ljust(width) for n in m.columns)
    lines = [head, "-" * len(head)]
    for call in m.calls if calls is None else calls:
        row = f"{call.id} {call.subject}"[:33].ljust(34)
        for name in m.columns:
            cell = m.cells.get((name, call.id))
            text = (marked_label(cell, marks) if cell else "pending").ljust(width)
            row += f"{ANSI[STYLE[cell.primary]]}{text}{RESET}" if (cell and color) else text
        lines.append(row)
    return "\n".join(lines)


def _plain_lead(m: mx.Matrix, f: mx.Finding) -> list[str]:
    """PRD 1.8: the practical harm in plain words comes before the REWRITE_UNSEEN_BY token."""
    if f.kind != "REWRITE_UNSEEN_BY" or not f.plugins:
        return []
    tool = next((c.tool for c in m.calls if c.id == f.call_id), "")
    noun = "command" if tool == "terminal" else "call"
    return [f"• In plain words: {f.plugins[-1]} checked the original {noun} and never saw the rewritten one, "
            f"and the rewritten {noun} ran."]


NO_FINDING = "• no cross-plugin finding recorded on these test calls"


def finding_lines(m: mx.Matrix) -> list[str]:
    """What the report leads with: each cross-plugin finding, the plain-words harm first."""
    lines = [line for f in m.findings for line in (*_plain_lead(m, f), f"• {f.kind}: {f.text}")]
    return lines or [NO_FINDING]


def _note_lines(m: mx.Matrix) -> list[str]:
    """Under the matrix: who each stack effect is attributed to, and everything that qualifies a cell."""
    lines = [f"• attribution {a.call_id} {mx.LABELS[a.outcome]}: {a.kind} — {a.note}" for a in m.attributions]
    lines += [f"• skipped {key}: {why}" for key, why in m.skipped]
    lines += [f"• column {col} stopped: DRILL_ABORTED ({why})" for col, why in m.aborted.items()]
    lines += [f"• LOAD FAILED in {col}: {', '.join(keys)} not confirmed loaded; its cells are not results"
              for col, keys in m.load_failed.items()]
    lines += [f"• cross-check of {cid} not replayed (fails the closed call grammar: {', '.join(why[:3])})"
              for cid, why in m.unreplayable]
    lines += [f"• UNSTABLE {c.call_id} in {c.column}: its {len(c.varied)} solo runs showed {' / '.join(c.varied)}"
              for c in m.cells.values() if c.varied]
    lines += [f"• coverage of {line}" for line in census_lines(m)]
    if m.unstable:
        lines.append(f"• unstable between two baseline runs (never reported as RESULT CHANGED): {', '.join(sorted(m.unstable))}")
    return lines


DETAIL_HINT = "Detail: hermes provingground show <CALL> --column <COLUMN>"


def _head_lines(m: mx.Matrix) -> list[str]:
    return [HEADLINE, OBSERVED.format(n=m.conditions.n_calls), mx.tally(m)]


def render_terminal(m: mx.Matrix, color: bool = True, width: int = 160, focus: bool = False) -> str:
    """The whole matrix; with *focus*, only the rows with an effect, then one line naming the rows left out."""
    calls, focused = shown_calls(m, focus), [focus_line(m)] if focus else []
    head, notes = _head_lines(m) + finding_lines(m), [*focused, *_note_lines(m), SCOPE_NOTE]
    try:
        from rich.console import Console
        from rich.panel import Panel
    except ImportError:
        return "\n".join([*head, "", _plain_table(m, color, calls), *notes, "", "drill conditions:",
                          *conditions_lines(m), DETAIL_HINT])
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=color, no_color=not color, width=width)
    console.print(head[0], style="bold", markup=False, highlight=False)
    for line in head[1:]:
        console.print(line, markup=False, highlight=False)
    _print_table(console, calls, m.columns, m.cells, m.conditions.n_calls if focus else None, header_titles(m),
                 finding_marks(m))
    for line in notes:
        console.print(line, markup=False, highlight=False)
    console.print(Panel("\n".join(conditions_lines(m)), title="drill conditions", expand=False))
    console.print(DETAIL_HINT, markup=False, highlight=False)
    return buffer.getvalue()


@dataclass(frozen=True)
class LiveFrame:
    """One redraw of the live view: every planned column in display order, and the cells finished so far."""
    calls: tuple[mx.CallInfo, ...]
    planned: tuple[str, ...]
    cells: Mapping[tuple[str, str], mx.Cell] = field(default_factory=dict)
    finished: Mapping[str, tuple[float, bool]] = field(default_factory=dict)  # column -> (wall seconds, stopped)


def progress_line(frame: LiveFrame) -> str:
    def status(name: str) -> str:
        if name not in frame.finished:
            return f"{short_title(name)} …"
        wall, stopped = frame.finished[name]
        return f"{short_title(name)} {'stopped' if stopped else '✓'} {wall:.1f}s"
    parts = [status(name) for name in frame.planned]
    if "stack" in frame.planned and set(frame.planned) <= set(frame.finished):
        parts.append("cross-check of rewritten calls …")  # runs after the stack, only when something was rewritten
    return " · ".join(parts)


def render_live(frame: LiveFrame, color: bool = True, width: int = 160) -> str:
    """Banner first (it is the first frame of the demo), then the filling table, then column progress."""
    from rich.console import Console

    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=color, no_color=not color, width=width, highlight=False)
    head, _, rest = LIVE_BANNER.partition(" · ")
    console.print(f"[bold]{head}[/] · {rest}")
    if frame.planned:
        _print_table(console, frame.calls, frame.planned, frame.cells)
        console.print(progress_line(frame), style="dim", markup=False)
    else:
        console.print("starting the throwaway child Hermes …", style="dim", markup=False)
    return buffer.getvalue()


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|")


def render_markdown(m: mx.Matrix, focus: bool = False) -> str:
    marks = finding_marks(m)
    lines = [f"# {HEADLINE}", "", f"**{OBSERVED.format(n=m.conditions.n_calls)}** {mx.tally(m)}", "",
             "## Findings", *[f"- {_md_escape(x.lstrip('• '))}" for x in finding_lines(m)], "", "## Matrix", "",
             "| test call | " + " | ".join(header_title(m, n) for n in m.columns) + " |",
             "|---|" + "---|" * len(m.columns)]
    for call in shown_calls(m, focus):
        cells = [_md_escape(marked_label(m.cells[(n, call.id)], marks)) if (n, call.id) in m.cells else "pending"
                 for n in m.columns]
        lines.append(f"| {call.id} {_md_escape(call.subject)} | " + " | ".join(cells) + " |")
    lines += ["", focus_line(m)] if focus else []
    lines += ["", "## Attribution and notes", *[f"- {_md_escape(x.lstrip('• '))}" for x in _note_lines(m)],
              f"- {SCOPE_NOTE}", f"- {DETAIL_HINT}",
              "", "## Drill conditions", *[f"- {line}" for line in conditions_lines(m)]]
    return "\n".join(lines) + "\n"


def _clip(text: str | None) -> list[str]:
    """At most MAX_DETAIL_LINES lines and MAX_DETAIL_CHARS characters: one long line (a JSON result) is cut too."""
    lines = (text or "").splitlines() or [""]
    kept, room = [], MAX_DETAIL_CHARS
    for line in lines[:MAX_DETAIL_LINES]:
        if len(line) > room:
            kept.append(line[:room] + f" [{len(line) - room} more characters]")
            break
        kept.append(line)
        room -= len(line)
    rest = len(lines) - len(kept)
    return kept + ([f"[{rest} more lines]"] if rest else [])


def _diff(old: str | None, new: str | None, what: str = "delivered") -> list[str]:
    return list(difflib.unified_diff(_clip(old), _clip(new), f"baseline {what}", f"this column {what}", lineterm=""))


def _delivered_diff(old: str | None, new: str | None) -> list[str]:
    """Diff a terminal result's output line by line; fall back to the whole string when only other fields differ."""
    return _diff(mx.tool_output(old), mx.tool_output(new), "delivered output") or _diff(old, new)


def _attribution_for(m: mx.Matrix, call_id: str) -> list[str]:
    return [f"{mx.LABELS[a.outcome]}: {a.kind} {', '.join(a.plugins)} — {a.note}"
            for a in m.attributions if a.call_id == call_id] or ["no plugin effect to attribute"]


def _context_lines(obs: mx.CallObservation) -> list[str]:
    """The model's view of the turn, one block per distinct step view: role headers, then the text."""
    lines: list[str] = []
    for index, view in enumerate(mx.context_views(obs), 1):
        lines.append(f"[view {index}] tools offered: {', '.join(view.get('tools') or []) or 'none'}")
        for message in view.get("messages") or []:
            lines += [f"[{message.get('role')}]", *str(message.get("content") or "").splitlines()]
    return lines


def _wire_sections(obs: mx.CallObservation, base: mx.CallObservation) -> list[tuple[str, list[str]]]:
    if obs.call_id == mx.CONTEXT_ID:
        mine, theirs = _context_lines(obs), _context_lines(base)
        return [("prompt as sent by PROVING GROUND", [str(obs.injected_args.get("prompt"))]),
                (f"what reached the model (system prompt, user turn, tools offered) · {QUOTE_TITLE}",
                 quoted(_clip("\n".join(mine)))),
                (f"model context: baseline vs this column · {QUOTE_TITLE}",
                 quoted(list(difflib.unified_diff(theirs, mine, "baseline context", "this column context",
                                                  lineterm=""))[:MAX_DETAIL_LINES] or ["identical"]))]
    return [("final reply as scripted by PROVING GROUND", [str(obs.injected_args.get("reply"))]),
            (f"1 · reply as the model produced it (seen by transform_llm_output) · {QUOTE_TITLE}",
             quoted(_clip(obs.tool_raw))),
            (f"2 · reply delivered (seen by post_llm_call) · {QUOTE_TITLE}", quoted(_clip(obs.delivered))),
            (f"delivered reply: baseline vs this column · {QUOTE_TITLE}",
             quoted(_diff(base.delivered, obs.delivered, "reply") or ["identical"]))]


def detail_sections(m: mx.Matrix, column: str, call_id: str) -> list[tuple[str, list[str]]]:
    obs, base = m.observations[column][call_id], m.observations["baseline"][call_id]
    cell = m.cells[(column, call_id)]
    info = next(c for c in m.calls if c.id == call_id)
    head = [("cell", [f"{call_id} × {column_title(column)}: {cell_label(cell)}",
                      f"provenance: {provenance(column)} column · call {info.provenance}", info.purpose, cell.note]),
            (f"full-stack attribution for {call_id} (observed in one solo run per plugin, against the zero-plugin "
             "baseline)",
             _attribution_for(m, call_id))]
    if obs.tool == mx.WIRE_TOOL:
        return head + _wire_sections(obs, base)
    return head + [
        ("args as sent by PROVING GROUND (from the plan)", [str(dict(obs.injected_args))]),
        (f"args that actually ran (after every plugin) · {QUOTE_TITLE}", quoted([str(dict(obs.final_args))])),
        (f"1 · terminal output, raw · {QUOTE_TITLE}", quoted(_clip(obs.terminal_raw))),
        (f"2 · tool result before result rewriters · {QUOTE_TITLE}", quoted(_clip(obs.tool_raw))),
        (f"3 · delivered to the model · {QUOTE_TITLE}", quoted(_clip(obs.delivered))),
        (f"delivered: baseline vs this column · {QUOTE_TITLE}",
         quoted(_delivered_diff(base.delivered, obs.delivered) or ["identical"]))]


def render_cell_detail(m: mx.Matrix, column: str, call_id: str) -> str:
    out: list[str] = []
    for title, lines in detail_sections(m, column, call_id):
        out += [f"── {title}", *lines, ""]
    return "\n".join(out)


def _changed_label(cell: mx.Cell | None) -> str:
    return cell_label(cell) if cell is not None else "not in that report"


def diff_lines(old: mx.Matrix, new: mx.Matrix, d: mx.MatrixDiff, old_name: str, new_name: str) -> list[str]:
    """--compare, --since-last and `diff`: only what changed between two reports, then a count."""
    changes = [*(f"plugin added: {p}" for p in d.plugins_added), *(f"plugin removed: {p}" for p in d.plugins_removed),
               *(f"test call added: {c}" for c in d.calls_added), *(f"test call removed: {c}" for c in d.calls_removed),
               *(f"{c.call_id} × {column_title(c.column)}: {_changed_label(c.before)} → {_changed_label(c.after)}"
                 for c in d.cells),
               *(f"new finding {f.kind} {f.call_id}: {f.text}" for f in d.findings_new),
               *(f"finding no longer recorded {f.kind} {f.call_id}: {f.text}" for f in d.findings_resolved)]
    head = [f"compared {old_name} (drill started {old.conditions.started_at})",
            f"      to {new_name} (drill started {new.conditions.started_at})"]
    if not changes:
        return head + ["no change: same plugins, test calls, cell outcomes and cross-plugin findings"]
    return head + changes + [f"{_plural(len(changes), 'change')} between the two reports"]


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


CONTRACT_SKIPPED = frozenset({mx.Outcome.NOT_RUN})  # a cell that never ran is not a known-good observation


def _yaml_quoted(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _expect_flow(expect: Mapping[str, str]) -> str:
    """One call's expectations as a YAML flow mapping: {"baseline": [PASSED_THROUGH], "stack": [BLOCKED]}."""
    return "{" + ", ".join(f"{_yaml_quoted(column)}: [{token}]" for column, token in expect.items()) + "}"


def render_contract(m: mx.Matrix) -> str:
    """--write-contract: an `expect:` file (the --calls format) pinning every built-in cell to the outcome this
    drill observed, so `--calls FILE --check` on an unchanged stack is met and any change fails it with exit 5."""
    pinned, left_out = [], []
    for call in m.calls:
        cells = [m.cells[(col, call.id)] for col in m.columns if (col, call.id) in m.cells]
        expect = {c.column: c.primary.value for c in cells if c.primary not in CONTRACT_SKIPPED}
        if call.provenance != "built-in" or not expect:
            left_out.append(call.id)
            continue
        pinned.append(f"  {call.id}: {_expect_flow(expect)}")
    header = [f"# expect: contract written by {GENERATOR} from the drill started {m.conditions.started_at}",
              f"# ({mx.tally(m)}). Each cell expects the outcome that drill observed.",
              "# Check a stack against it: hermes provingground --calls THIS_FILE --check  (exit 5 when any cell differs)"]
    if left_out:
        header.append(f"# not pinned (a user call, or no cell that ran): {', '.join(left_out)}")
    return "\n".join(header + ["schema: provingground.testcalls.v1", "expect:" + ("" if pinned else " {}"), *pinned]) + "\n"

# File: proving_ground/report.py  (part 3 of 3: self-contained HTML)

CSS = """
:root{color-scheme:light dark;--bg:#fbfaf7;--fg:#1d1d1f;--muted:#6b6b70;--line:#e6e3dd;--card:#ffffff;--code:#f6f4ef;
--blocked:#0e7490;--rewrote:#a16207;--changed:#a21caf;--gated:#1d4ed8;--through:#8a8a8f;--escaped:#b91c1c;--errored:#b91c1c;--wire:#15803d}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#121214;--fg:#ececef;--muted:#9a9aa2;
--line:#2a2a2e;--card:#1a1a1d;--code:#141416;--blocked:#22d3ee;--rewrote:#facc15;--changed:#e879f9;--gated:#60a5fa;
--through:#8a8a8f;--escaped:#f87171;--errored:#f87171;--wire:#4ade80}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:32px 16px 48px}a{color:var(--gated)}
h1{font-size:clamp(19px,4.8vw,26px);line-height:1.25;letter-spacing:-.01em;margin:0 0 6px}.muted{color:var(--muted)}
h3{font-size:13px;font-weight:600;color:var(--muted);margin:18px 0 6px}.panel h3:first-child{margin-top:0}
.box{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:16px 0}
.box ul{margin:8px 0 0;padding-left:18px}.box li{margin:3px 0;overflow-wrap:anywhere}
.small{font-size:13.5px}.scroll{overflow-x:auto;padding:6px 8px}details.box summary{cursor:pointer;font-weight:600}
.mark{display:block;margin-top:4px;font:600 11.5px/1.3 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--escaped)}
.lead{font-size:16px;font-weight:600}
table{border-collapse:separate;border-spacing:0;width:100%}th,td{border-bottom:1px solid var(--line);padding:9px 10px;
text-align:left;white-space:nowrap;vertical-align:top}tbody tr:last-child>*{border-bottom:0}
thead th{font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
thead th .hooks{display:block;font:400 11px/1.3 ui-monospace,SFMono-Regular,Menlo,monospace;text-transform:none;letter-spacing:0;white-space:normal;max-width:190px}
tbody th{font-weight:600;white-space:normal;min-width:150px}tbody th .muted{display:block;font-weight:400;font-size:13px}
th:first-child{position:sticky;left:0;z-index:1;background:var(--card)}
td a{display:inline-block;text-decoration:none;font:600 12px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace;
letter-spacing:.02em;padding:5px 8px;border-radius:6px}td a:hover{outline:1px solid currentColor}
td a:focus-visible{outline:2px solid currentColor;outline-offset:2px}
.BLOCKED{color:var(--blocked);background:color-mix(in srgb,var(--blocked) 13%,transparent)}
.REWROTE{color:var(--rewrote);background:color-mix(in srgb,var(--rewrote) 13%,transparent)}
.RESULT_CHANGED{color:var(--changed);background:color-mix(in srgb,var(--changed) 13%,transparent)}
.APPROVAL_GATED{color:var(--gated);background:color-mix(in srgb,var(--gated) 13%,transparent)}
.REQUEST_REWRITTEN{color:var(--rewrote);background:color-mix(in srgb,var(--rewrote) 13%,transparent)}
.EXECUTION_REPLACED{color:var(--blocked);background:color-mix(in srgb,var(--blocked) 13%,transparent)}
.CONTEXT_ADDED,.REPLY_CHANGED,.MODEL_REPLY_CHANGED{color:var(--wire);background:color-mix(in srgb,var(--wire) 13%,transparent)}
.ESCAPED{color:var(--escaped);background:color-mix(in srgb,var(--escaped) 16%,transparent)}
.ERRORED,.LOAD_FAILED{color:var(--errored);background:color-mix(in srgb,var(--errored) 10%,transparent)}
.PASSED_THROUGH,.NOT_RUN,.NOT_APPLICABLE{color:var(--through);font-weight:500}
.panel{display:none;scroll-margin-top:16px}.panel:target{display:block}
pre{background:var(--code);border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin:0;overflow-x:auto;
white-space:pre-wrap;overflow-wrap:anywhere;font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}pre.quoted{border-left:3px solid var(--muted)}
.add,.del{display:inline-block;min-width:100%}.add{color:var(--changed);background:color-mix(in srgb,var(--changed) 11%,transparent)}
.del{color:var(--rewrote);background:color-mix(in srgb,var(--rewrote) 11%,transparent)}
.panel p a{display:inline-block;margin-top:14px;text-decoration:none;font-weight:600}
#focus{margin:0 6px 0 2px;vertical-align:-1px;accent-color:var(--gated)}#focus+label{font-size:13.5px;color:var(--muted);cursor:pointer}
#focus:checked~#matrix tr.quiet{display:none}#focus:focus-visible+label{outline:2px solid var(--gated);outline-offset:2px}
@media (max-width:480px){main{padding:20px 12px 40px}.box{padding:14px}tbody th{min-width:118px}}
"""


def _anchor(column: str, call_id: str) -> str:
    return "cell-" + re.sub(r"[^A-Za-z0-9_-]", "-", f"{column}-{call_id}")


def _html_cell(m: mx.Matrix, column: str, call_id: str, marks: Mapping[tuple[str, str], str]) -> str:
    cell = m.cells.get((column, call_id))
    if cell is None:
        return "<td>pending</td>"
    mark = marks.get((column, call_id))
    flag = f"<span class='mark'>· {html.escape(mark)}</span>" if mark else ""
    if column not in m.observations or call_id not in m.observations[column]:  # N/A: no detail panel to link
        return f'<td><span class="{cell.primary.value}">{html.escape(cell_label(cell))}</span>{flag}</td>'
    return (f'<td><a class="{cell.primary.value}" href="#{_anchor(column, call_id)}">'
            f"{html.escape(cell_label(cell))}</a>{flag}</td>")


def _html_lines(lines: list[str]) -> str:
    def cls(line: str) -> str:
        return "add" if line.startswith("+") else "del" if line.startswith("-") else ""
    return "\n".join(f'<span class="{cls(x)}">{html.escape(x)}</span>' for x in lines)


def _html_panel(m: mx.Matrix, column: str, call_id: str) -> str:
    parts = [f'<section class="panel box" id="{_anchor(column, call_id)}">']
    for title, lines in detail_sections(m, column, call_id):
        quote = bool(lines) and all(x.startswith(QUOTE_PREFIX) for x in lines)
        body = [x[len(QUOTE_PREFIX):] for x in lines] if quote else lines
        cls = ' class="quoted"' if quote else ""
        parts.append(f"<h3>{html.escape(title)}</h3><pre{cls}>{_html_lines(body)}</pre>")
    return "".join(parts) + '<p><a href="#matrix">← back to the matrix</a></p></section>'


def _html_focus_toggle(m: mx.Matrix, focus: bool) -> str:
    """A checkbox, not a script: CSS hides the quiet rows while it is checked (--focus checks it)."""
    hidden = sum(mx.quiet_row(m, c.id) for c in m.calls)
    return (f'<input type="checkbox" id="focus"{" checked" if focus else ""}><label for="focus">show only the rows '
            f"with a recorded effect ({hidden} of {len(m.calls)} rows are PASSED THROUGH in every cell)</label>")


def _html_header(m: mx.Matrix, name: str) -> str:
    """The column title, the hooks its plugins declare in a hover title, and a compact line under the title."""
    if not m.column_plugins:  # a report from before declared hooks were recorded
        return f"<th>{html.escape(header_title(m, name))}</th>"
    full = "; ".join(f"{k}: {', '.join(h) or 'none declared'}" for k, h in column_hooks(m, name)) or "no plugins"
    return (f"<th title='{html.escape(f'declared hooks: {full}', quote=True)}'>{html.escape(header_title(m, name))}"
            f"<br><span class='hooks'>{html.escape(hooks_summary(m, name))}</span></th>")


def render_html(m: mx.Matrix, focus: bool = False) -> str:
    head, marks = "".join(_html_header(m, n) for n in m.columns), finding_marks(m)
    rows = "".join(f"<tr{' class=quiet' if mx.quiet_row(m, c.id) else ''}><th>{html.escape(c.id)} "
                   f"<span class='muted'>{html.escape(c.subject)}</span></th>"
                   + "".join(_html_cell(m, n, c.id, marks) for n in m.columns) + "</tr>" for c in m.calls)
    panels = "".join(_html_panel(m, col, cid) for (col, cid) in m.cells
                     if col in m.observations and cid in m.observations[col])
    findings = "".join(f"<li{' class=lead' if x.startswith('• In plain words') else ''}>"
                       f"{html.escape(x.lstrip('• '))}</li>" for x in finding_lines(m))
    notes = "".join(f"<li>{html.escape(x.lstrip('• '))}</li>" for x in _note_lines(m))
    cond = "".join(f"<li>{html.escape(x)}</li>" for x in conditions_lines(m))
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>Proving Ground Report</title>'
            f"<style>{CSS}</style></head><body><main><h1>{html.escape(HEADLINE)}</h1>"
            f"<p class='muted'>{html.escape(OBSERVED.format(n=m.conditions.n_calls))} {html.escape(mx.tally(m))}</p>"
            f"<div class='box'><strong>Findings</strong><ul>{findings}</ul></div>{_html_focus_toggle(m, focus)}"
            f"<div class='box scroll' id='matrix'><table><thead><tr><th>test call</th>{head}</tr></thead>"
            f"<tbody>{rows}</tbody></table></div><div class='box small'><ul>{notes}</ul></div>"
            f"<details class='box small'><summary>Drill conditions</summary><ul>{cond}</ul></details>{panels}"
            f"<p class='muted'>{html.escape(SCOPE_NOTE)}</p></main></body></html>")
