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
LIVE_BANNER = "model: NOT CALLED · provider: none · no provider keys · approvals: manual, headless deny"
STYLE = {mx.Outcome.BLOCKED: "bold cyan", mx.Outcome.REWROTE: "bold yellow", mx.Outcome.RESULT_CHANGED: "bold magenta",
         mx.Outcome.APPROVAL_GATED: "bold blue", mx.Outcome.PASSED_THROUGH: "dim", mx.Outcome.ESCAPED: "bold red",
         mx.Outcome.ERRORED: "red", mx.Outcome.LOAD_FAILED: "red", mx.Outcome.NOT_RUN: "dim",
         mx.Outcome.NOT_APPLICABLE: "dim"}
QUOTE_PREFIX = "│ "  # every line of plugin-authored text (raw output, block messages) is quoted, never PG's words
QUOTE_TITLE = "quoted from the plugin stack, not PROVING GROUND's words"
_HTML_QUOTED = re.compile(r'<pre class="quoted">.*?</pre>', re.S)
ANSI = {"bold cyan": "\033[1;36m", "bold yellow": "\033[1;33m", "bold magenta": "\033[1;35m", "bold blue": "\033[1;34m",
        "dim": "\033[2m", "bold red": "\033[1;31m", "red": "\033[31m"}
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
    return {"solo": "solo", "crosscheck": "cross-check"}.get(column.partition(":")[0], "stack")


def cell_label(cell: mx.Cell) -> str:
    label = " + ".join(mx.LABELS[o] for o in sorted(cell.outcomes, key=mx.PRECEDENCE.index))
    core = " (core)" if cell.core else " (core + plugin)" if cell.core_also else ""
    return label + core + (" ~" if cell.unstable else "")


def column_title(name: str) -> str:
    return {"baseline": "baseline (no plugins)", "stack": "FULL STACK"}.get(name, name.removeprefix("solo:"))


def short_title(name: str) -> str:
    return {"baseline": "baseline", "stack": "FULL STACK"}.get(name, name.removeprefix("solo:"))


def _source(m: mx.Matrix, cell: mx.Cell) -> str:
    """Who the effect is attributed to: the solo column's plugin, or the stack attribution's plugins."""
    if cell.core:
        return ""
    if cell.column.startswith("solo:"):
        return cell.column.removeprefix("solo:")
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
              "provenance": provenance(c.column)}
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
            "unreplayable": [[cid, list(r)] for cid, r in m.unreplayable]}


def _cell_from(raw: Mapping[str, Any]) -> mx.Cell:
    return mx.Cell(raw["column"], raw["call_id"], tuple(mx.Outcome(o) for o in raw["outcomes"]),
                   raw.get("hop"), bool(raw.get("core")), bool(raw.get("unstable")), raw.get("note", ""),
                   bool(raw.get("core_also")))


def _known(cls: type, raw: Mapping[str, Any]) -> dict[str, Any]:
    """Only the fields this version knows: a report written by a newer version (one extra key) still loads."""
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in raw.items() if k in names}


def _call_from(raw: Mapping[str, Any]) -> mx.CallInfo:
    return mx.CallInfo(**{**_known(mx.CallInfo, raw),
                          "expect": {k: tuple(v) for k, v in (raw.get("expect") or {}).items()}})


def _obs_from(raw: Mapping[str, Any]) -> mx.CallObservation:
    return mx.CallObservation(**{**_known(mx.CallObservation, raw),
                                 "escaped_reasons": tuple(raw.get("escaped_reasons") or ())})


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
                     tuple((cid, tuple(r)) for cid, r in data.get("unreplayable") or ()))

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
            f"coverage: {c.coverage or 'n/a'}", f"cleanup: {left}",
            f"wall time: {c.wall_s:.1f}s · started {c.started_at}"]


def _rich_table(calls: Sequence[mx.CallInfo], columns: Sequence[str],
                cells: Mapping[tuple[str, str], mx.Cell], call_width: int | None = None) -> Any:
    """Outcome labels wrap only at their "(core)" suffix; the call column wraps (to call_width) on narrow terminals."""
    from rich import box
    from rich.table import Table

    table = Table(title=f"{HEADLINE}\n{OBSERVED.format(n=len(calls))}", box=box.SIMPLE_HEAD, expand=False,
                  title_style="bold", header_style="bold", pad_edge=False)
    table.add_column("test call", no_wrap=call_width is None, width=call_width)
    for name in columns:
        table.add_column(column_title(name), min_width=14, overflow="fold")
    for call in calls:
        row = [f"[bold]{call.id}[/] {call.subject}"]
        for name in columns:
            cell = cells.get((name, call.id))
            row.append(f"[{STYLE[cell.primary]}]{cell_label(cell)}[/]" if cell else "[dim]…[/]")
        table.add_row(*row)
    return table


def _outcome_columns_width(columns: Sequence[str]) -> int:
    """Width the outcome columns need without splitting a word (rich's Measurement ignores min_width)."""
    words = [max([14] + [len(word) for word in column_title(n).split()]) for n in columns]
    return sum(words) + 3 * len(columns) + 2  # 2 padding + 1 separator per column, 2 edge characters


def display_order(columns: Sequence[str], width: int) -> list[str]:
    """When every column cannot fit, FULL STACK moves next to the baseline so the column the report exists for
    is never the one cropped; the solos follow."""
    if "stack" not in columns or _outcome_columns_width(columns) <= width - 24:
        return list(columns)
    return [c for c in columns if c == "baseline"] + ["stack"] + [c for c in columns if c not in ("baseline", "stack")]


def _print_table(console: Any, calls: Sequence[mx.CallInfo], columns: Sequence[str],
                 cells: Mapping[tuple[str, str], mx.Cell]) -> None:
    columns = display_order(columns, console.width)
    longest_call = max([len("test call")] + [len(f"{c.id} {c.subject}") for c in calls])
    budget = console.width - _outcome_columns_width(columns)
    call_width = None if longest_call <= budget else max(8, budget)
    console.print(_rich_table(calls, columns, cells, call_width))


def _plain_table(m: mx.Matrix, color: bool) -> str:
    width = max(14, *(len(column_title(n)) for n in m.columns)) + 2
    head = "test call".ljust(34) + "".join(column_title(n).ljust(width) for n in m.columns)
    lines = [HEADLINE, OBSERVED.format(n=m.conditions.n_calls), head, "-" * len(head)]
    for call in m.calls:
        row = f"{call.id} {call.subject}"[:33].ljust(34)
        for name in m.columns:
            cell = m.cells.get((name, call.id))
            text = (cell_label(cell) if cell else "pending").ljust(width)
            row += f"{ANSI[STYLE[cell.primary]]}{text}{RESET}" if (cell and color) else text
        lines.append(row)
    return "\n".join(lines)


def _footer_lines(m: mx.Matrix) -> list[str]:
    lines = [mx.tally(m)]
    lines += [f"• {f.kind}: {f.text}" for f in m.findings]
    lines += [f"• attribution {a.call_id} {mx.LABELS[a.outcome]}: {a.kind} — {a.note}" for a in m.attributions]
    lines += [f"• skipped {key}: {why}" for key, why in m.skipped]
    lines += [f"• column {col} stopped: DRILL_ABORTED ({why})" for col, why in m.aborted.items()]
    lines += [f"• LOAD FAILED in {col}: {', '.join(keys)} not confirmed loaded; its cells are not results"
              for col, keys in m.load_failed.items()]
    lines += [f"• cross-check of {cid} not replayed (fails the closed call grammar: {', '.join(why[:3])})"
              for cid, why in m.unreplayable]
    if m.unstable:
        lines.append(f"• unstable between two baseline runs (never reported as RESULT CHANGED): {', '.join(sorted(m.unstable))}")
    return lines + [SCOPE_NOTE, "Detail: hermes provingground show <CALL> --column <COLUMN>"]


def render_terminal(m: mx.Matrix, color: bool = True, width: int = 160) -> str:
    try:
        from rich.console import Console
        from rich.panel import Panel
    except ImportError:
        return "\n".join(conditions_lines(m) + ["", _plain_table(m, color), ""] + _footer_lines(m))
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=color, no_color=not color, width=width)
    console.print(Panel("\n".join(conditions_lines(m)), title="drill conditions", expand=False))
    _print_table(console, m.calls, m.columns, m.cells)
    for line in _footer_lines(m):
        console.print(line, markup=False, highlight=False)
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


def render_markdown(m: mx.Matrix) -> str:
    lines = [f"# {HEADLINE}", "", f"**{OBSERVED.format(n=m.conditions.n_calls)}** {mx.tally(m)}", "",
             "## Drill conditions", *[f"- {line}" for line in conditions_lines(m)], "", "## Matrix", "",
             "| test call | " + " | ".join(column_title(n) for n in m.columns) + " |",
             "|---|" + "---|" * len(m.columns)]
    for call in m.calls:
        cells = [cell_label(m.cells[(n, call.id)]) if (n, call.id) in m.cells else "pending" for n in m.columns]
        lines.append(f"| {call.id} {_md_escape(call.subject)} | " + " | ".join(cells) + " |")
    lines += ["", "## Findings and attribution", *[f"- {_md_escape(x.lstrip('• '))}" for x in _footer_lines(m)]]
    return "\n".join(lines) + "\n"


def _clip(text: str | None) -> list[str]:
    lines = (text or "").splitlines() or [""]
    if len(lines) <= MAX_DETAIL_LINES:
        return lines
    return lines[:MAX_DETAIL_LINES] + [f"[{len(lines) - MAX_DETAIL_LINES} more lines]"]


def _diff(old: str | None, new: str | None, what: str = "delivered") -> list[str]:
    return list(difflib.unified_diff(_clip(old), _clip(new), f"baseline {what}", f"this column {what}", lineterm=""))


def _delivered_diff(old: str | None, new: str | None) -> list[str]:
    """Diff a terminal result's output line by line; fall back to the whole string when only other fields differ."""
    return _diff(mx.tool_output(old), mx.tool_output(new), "delivered output") or _diff(old, new)


def _attribution_for(m: mx.Matrix, call_id: str) -> list[str]:
    return [f"{mx.LABELS[a.outcome]}: {a.kind} {', '.join(a.plugins)} — {a.note}"
            for a in m.attributions if a.call_id == call_id] or ["no plugin effect to attribute"]


def detail_sections(m: mx.Matrix, column: str, call_id: str) -> list[tuple[str, list[str]]]:
    obs, base = m.observations[column][call_id], m.observations["baseline"][call_id]
    cell = m.cells[(column, call_id)]
    info = next(c for c in m.calls if c.id == call_id)
    return [("cell", [f"{call_id} × {column_title(column)}: {cell_label(cell)}",
                      f"provenance: {provenance(column)} column · call {info.provenance}", info.purpose, cell.note]),
            (f"full-stack attribution for {call_id} (observed in one solo run per plugin, against the zero-plugin "
             "baseline)",
             _attribution_for(m, call_id)),
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

# File: proving_ground/report.py  (part 3 of 3: self-contained HTML)

CSS = """
:root{color-scheme:light dark;--bg:#fbfaf7;--fg:#1d1d1f;--muted:#6b6b70;--line:#e6e3dd;--card:#ffffff;--code:#f6f4ef;
--blocked:#0e7490;--rewrote:#a16207;--changed:#a21caf;--gated:#1d4ed8;--through:#8a8a8f;--escaped:#b91c1c;--errored:#b91c1c}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#121214;--fg:#ececef;--muted:#9a9aa2;
--line:#2a2a2e;--card:#1a1a1d;--code:#141416;--blocked:#22d3ee;--rewrote:#facc15;--changed:#e879f9;--gated:#60a5fa;
--through:#8a8a8f;--escaped:#f87171;--errored:#f87171}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:32px 16px 48px}a{color:var(--gated)}
h1{font-size:clamp(19px,4.8vw,26px);line-height:1.25;letter-spacing:-.01em;margin:0 0 6px}.muted{color:var(--muted)}
h3{font-size:13px;font-weight:600;color:var(--muted);margin:18px 0 6px}.panel h3:first-child{margin-top:0}
.box{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:16px 0}
.box ul{margin:8px 0 0;padding-left:18px}.box li{margin:3px 0;overflow-wrap:anywhere}
main>div:first-of-type{font-size:13.5px}.scroll{overflow-x:auto;padding:6px 8px}
table{border-collapse:separate;border-spacing:0;width:100%}th,td{border-bottom:1px solid var(--line);padding:9px 10px;
text-align:left;white-space:nowrap;vertical-align:top}tbody tr:last-child>*{border-bottom:0}
thead th{font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
tbody th{font-weight:600;white-space:normal;min-width:150px}tbody th .muted{display:block;font-weight:400;font-size:13px}
th:first-child{position:sticky;left:0;z-index:1;background:var(--card)}
td a{display:inline-block;text-decoration:none;font:600 12px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace;
letter-spacing:.02em;padding:5px 8px;border-radius:6px}td a:hover{outline:1px solid currentColor}
td a:focus-visible{outline:2px solid currentColor;outline-offset:2px}
.BLOCKED{color:var(--blocked);background:color-mix(in srgb,var(--blocked) 13%,transparent)}
.REWROTE{color:var(--rewrote);background:color-mix(in srgb,var(--rewrote) 13%,transparent)}
.RESULT_CHANGED{color:var(--changed);background:color-mix(in srgb,var(--changed) 13%,transparent)}
.APPROVAL_GATED{color:var(--gated);background:color-mix(in srgb,var(--gated) 13%,transparent)}
.ESCAPED{color:var(--escaped);background:color-mix(in srgb,var(--escaped) 16%,transparent)}
.ERRORED,.LOAD_FAILED{color:var(--errored);background:color-mix(in srgb,var(--errored) 10%,transparent)}
.PASSED_THROUGH,.NOT_RUN,.NOT_APPLICABLE{color:var(--through);font-weight:500}
.panel{display:none;scroll-margin-top:16px}.panel:target{display:block}
pre{background:var(--code);border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin:0;overflow-x:auto;
white-space:pre-wrap;overflow-wrap:anywhere;font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}pre.quoted{border-left:3px solid var(--muted)}
.add,.del{display:inline-block;min-width:100%}.add{color:var(--changed);background:color-mix(in srgb,var(--changed) 11%,transparent)}
.del{color:var(--rewrote);background:color-mix(in srgb,var(--rewrote) 11%,transparent)}
.panel p a{display:inline-block;margin-top:14px;text-decoration:none;font-weight:600}
@media (max-width:480px){main{padding:20px 12px 40px}.box{padding:14px}tbody th{min-width:118px}}
"""


def _anchor(column: str, call_id: str) -> str:
    return "cell-" + re.sub(r"[^A-Za-z0-9_-]", "-", f"{column}-{call_id}")


def _html_cell(m: mx.Matrix, column: str, call_id: str) -> str:
    cell = m.cells.get((column, call_id))
    if cell is None:
        return "<td>pending</td>"
    if column not in m.observations or call_id not in m.observations[column]:  # N/A: no detail panel to link
        return f'<td><span class="{cell.primary.value}">{html.escape(cell_label(cell))}</span></td>'
    return (f'<td><a class="{cell.primary.value}" href="#{_anchor(column, call_id)}">'
            f"{html.escape(cell_label(cell))}</a></td>")


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


def render_html(m: mx.Matrix) -> str:
    head = "".join(f"<th>{html.escape(column_title(n))}</th>" for n in m.columns)
    rows = "".join(f"<tr><th>{html.escape(c.id)} <span class='muted'>{html.escape(c.subject)}</span></th>"
                   + "".join(_html_cell(m, n, c.id) for n in m.columns) + "</tr>" for c in m.calls)
    panels = "".join(_html_panel(m, col, cid) for (col, cid) in m.cells
                     if col in m.observations and cid in m.observations[col])
    footer = "".join(f"<li>{html.escape(x.lstrip('• '))}</li>" for x in _footer_lines(m) if x != SCOPE_NOTE)
    cond = "".join(f"<li>{html.escape(x)}</li>" for x in conditions_lines(m))
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>Proving Ground Report</title>'
            f"<style>{CSS}</style></head><body><main><h1>{html.escape(HEADLINE)}</h1>"
            f"<p class='muted'>{html.escape(OBSERVED.format(n=m.conditions.n_calls))} {html.escape(mx.tally(m))}</p>"
            f"<div class='box'><strong>Drill conditions</strong><ul>{cond}</ul></div>"
            f"<div class='box scroll' id='matrix'><table><thead><tr><th>test call</th>{head}</tr></thead>"
            f"<tbody>{rows}</tbody></table></div><div class='box'><ul>{footer}</ul></div>{panels}"
            f"<p class='muted'>{html.escape(SCOPE_NOTE)}</p></main></body></html>")
