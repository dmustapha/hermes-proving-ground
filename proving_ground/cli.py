# File: proving_ground/cli.py  (part 1 of 2: settings, posture, the run)
# [VERIFIED] public modules per hermes_cli/data/plugin_api.json; argparse setup_fn/handler_fn contract
# (plugins_context.py:442-453, docs/index.md "Register CLI commands"); slash handler keywords platform/session_id
# (docs/index.md "Calling-session context"). [VERIFIED by the §9.1 run] the composition.
"""`hermes provingground` and `/provingground`."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Sequence

from . import matrix, report, runner, safety, testcalls

PG_SOURCE = Path(__file__).resolve().parent.parent
REPORTS_SUBDIR = "reports"
LATEST = "latest.json"
FIXTURE_KEYS = ("fx_guard", "fx_rewriter", "fx_trimmer")


def find_hermes(explicit: str | None) -> Path:
    for candidate in (explicit, shutil.which("hermes"), str(Path.home() / ".local/bin/hermes")):
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return Path(candidate).resolve()
    raise safety.Refusal("HERMES_NOT_FOUND", "could not find the hermes executable; pass --hermes-bin PATH.")


def build_settings(args: argparse.Namespace) -> runner.RunnerSettings:
    if sys.platform.startswith("win"):
        raise safety.Refusal("SANDBOX_UNSUPPORTED", "drills run on macOS and Linux only.")
    temp_base = Path(tempfile.gettempdir()).resolve()
    if not safety.under_temp_base(temp_base):
        raise safety.Refusal("SANDBOX_UNSUPPORTED", f"temp dir {temp_base} is not under a system temp base.")
    return runner.RunnerSettings(hermes_bin=find_hermes(getattr(args, "hermes_bin", None)), pg_source=PG_SOURCE,
                                 warm_cache=temp_base / "provingground-cache" / "installs", temp_base=temp_base,
                                 cell_timeout_s=float(getattr(args, "timeout", 120.0)), jobs=int(getattr(args, "jobs", 3)))


def user_config() -> dict[str, Any]:
    from hermes_cli.config import load_config_readonly

    return load_config_readonly() or {}


def check_live_posture(config: dict[str, Any], platform: str | None) -> str:
    """Runs before every spawning entry point: run, warm and the slash command (ARCH-F4, ARCH-F9)."""
    from cron.execution_identity import current_cron_execution

    mode = safety.normalize_approval_mode((config.get("approvals") or {}).get("mode"))
    safety.check_posture(os.environ, mode, platform, current_cron_execution() is not None)
    if mode == "smart":
        return ("Note: your live approvals mode is smart (an auxiliary model decides). In the sandbox, every "
                "approval is denied; APPROVAL-GATED cells show what Hermes would have asked about.")
    return ""


def select_subjects(args: argparse.Namespace, settings: runner.RunnerSettings) -> tuple[list[runner.PluginSource], list[tuple[str, str]]]:
    if getattr(args, "fixtures", False):
        return runner.fixture_subjects(settings.pg_source, getattr(args, "without", None) or ()), []
    from hermes_constants import get_hermes_home

    subjects, skipped = runner.discover_subjects(Path(get_hermes_home()), user_config())
    without = set(getattr(args, "without", None) or ())
    return [s for s in subjects if s.key not in without], skipped


def select_templates(args: argparse.Namespace) -> list[testcalls.CallTemplate]:
    templates = testcalls.load_templates()
    if getattr(args, "calls", None):
        try:
            templates = testcalls.load_user_calls(Path(args.calls), templates)
        except (OSError, testcalls.TestCallError) as exc:
            raise safety.Refusal("BAD_CALLS_FILE", str(exc)) from exc
    only = set(getattr(args, "only", None) or ())
    return [t for t in templates if not only or t.id in only]


def call_infos(templates: Sequence[testcalls.CallTemplate]) -> list[matrix.CallInfo]:
    return [matrix.CallInfo(t.id, t.tool, t.subject, t.purpose, t.provenance, dict(t.expect)) for t in templates]


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def credentials_line(notes: Sequence[str]) -> str:
    base = "no provider keys in the child env (allow-list)"
    if not notes:
        return base + "; no credential-shaped files or settings found in the copied plugins"
    return base + f"; withheld from the copies: {'; '.join(notes[:4])}" + (" …" if len(notes) > 4 else "")


def assemble(drill: runner.DrillRun, infos: Sequence[matrix.CallInfo], version: str, started: str,
             leftover: tuple[int, int] | None = None) -> matrix.Matrix:
    extra: dict[str, Any] = {"credentials": credentials_line(drill.copy_notes),
                             "approvals": runner.approvals_summary()}
    if leftover is not None:
        extra.update(leftover_processes=leftover[0], leftover_roots=leftover[1])
    return matrix.build_matrix(drill.columns, drill.baseline_repeat, drill.crosscheck, infos, version, started,
                               drill.wall_s, drill.skipped, drill.needs_env, load_order=drill.load_order,
                               unreplayable=drill.unreplayable, conditions_extra=extra)


def planned_columns(subjects: Sequence[runner.PluginSource]) -> tuple[str, ...]:
    """Display order, fixed before any column finishes: baseline, solos in load order, FULL STACK."""
    return tuple(spec.name for spec in runner.plan_columns(runner.load_order(subjects)))


def run_matrix(args: argparse.Namespace, on_partial: Callable[[report.LiveFrame], None] | None = None,
               platform: str | None = None) -> tuple[matrix.Matrix, str]:
    config = user_config()
    note = check_live_posture(config, platform)
    settings = build_settings(args)
    runner.sweep_stale_roots(settings.temp_base)
    if not runner.is_warm(settings):
        print(runner.WARM_DISCLOSURE.format(cache=settings.warm_cache), file=sys.stderr, flush=True)
        runner.warm_up(settings)
    subjects, skipped = select_subjects(args, settings)
    templates = select_templates(args)
    infos, planned, done = call_infos(templates), planned_columns(subjects), []
    if on_partial is not None:
        on_partial(report.LiveFrame(tuple(infos), planned))
    version, started = runner.hermes_version(settings.hermes_bin, settings), now_iso()

    def partial(column: matrix.ColumnRun) -> None:
        done.append(column)
        if on_partial is not None:
            on_partial(live_frame(done, planned, infos, version, started))

    drill = runner.run_drill(subjects, templates, settings, partial, skipped)
    return assemble(drill, infos, version, started, runner.leftovers(settings.temp_base)), note


def live_frame(done: Sequence[matrix.ColumnRun], planned: Sequence[str], infos: Sequence[matrix.CallInfo],
               version: str, started: str) -> report.LiveFrame:
    """Columns finish out of order; the frame keeps the planned order (the baseline always reports first)."""
    rank = {name: index for index, name in enumerate(planned)}
    ordered = sorted(done, key=lambda c: rank.get(c.name, len(rank)))
    part = matrix.build_matrix(ordered, None, {}, infos, version, started, 0.0)
    return report.LiveFrame(tuple(infos), tuple(planned), part.cells,
                            {c.name: (c.wall_s, bool(c.abort_reason)) for c in done})

# File: proving_ground/cli.py  (part 2 of 2: persistence, commands, slash)


def reports_dir() -> Path:
    from plugins.plugin_storage import plugin_data_dir

    path = Path(plugin_data_dir("proving-ground")) / REPORTS_SUBDIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def save(m: matrix.Matrix) -> Path:
    data = json.dumps(report.matrix_to_dict(m), indent=2, ensure_ascii=False)
    stamp = m.conditions.started_at.replace(":", "").replace("+", "Z")
    target = reports_dir() / f"report-{stamp}.json"
    target.write_text(data, encoding="utf-8")
    (reports_dir() / LATEST).write_text(data, encoding="utf-8")
    return target


def load_latest() -> matrix.Matrix:
    path = reports_dir() / LATEST
    if not path.is_file():
        raise safety.Refusal("NO_REPORT", "no report yet; run `hermes provingground` (or add --fixtures) first.")
    return report.matrix_from_dict(json.loads(path.read_text(encoding="utf-8")))


def write_exports(m: matrix.Matrix, args: argparse.Namespace) -> list[str]:
    written = []
    for flag, render in (("json_out", lambda x: json.dumps(report.matrix_to_dict(x), indent=2, ensure_ascii=False)),
                         ("markdown_out", report.render_markdown), ("html_out", report.render_html)):
        target = getattr(args, flag, None)
        if target:
            Path(target).write_text(render(m), encoding="utf-8")
            written.append(str(target))
    return written


def exit_code_for(m: matrix.Matrix, check: bool = False) -> int:
    if any(f.kind == "ESCAPED" for f in m.findings):
        return 4
    if m.aborted:
        return 3
    return 5 if check and matrix.check_expectations(m)[0] else 0


def expectation_lines(m: matrix.Matrix) -> list[str]:
    unmet, missing = matrix.check_expectations(m)
    lines = [f"CHECK FAILED {cid} × {col}: expected {' or '.join(allowed)}, observed {actual}"
             for cid, col, allowed, actual in unmet]
    lines += [f"check not evaluated {cid} × {col}: that column is not in this run" for cid, col in missing]
    total = sum(len(c.expect or {}) for c in m.calls)
    return lines + [f"contract: {total - len(unmet) - len(missing)} of {total} expectations met"]


def terminal_width() -> int:
    return shutil.get_terminal_size((160, 40)).columns


def _live_run(args: argparse.Namespace) -> tuple[matrix.Matrix, str]:
    color, width = not args.no_color and sys.stdout.isatty(), terminal_width()
    try:
        from rich.console import Console
        from rich.live import Live
        from rich.text import Text
    except ImportError:
        return run_matrix(args)

    def frame(part: report.LiveFrame) -> Text:
        return Text.from_ansi(report.render_live(part, color, width))

    console = Console()
    with Live(frame(report.LiveFrame((), ())), console=console, refresh_per_second=8,
              transient=True) as live:  # the final render is printed once by cmd_run
        return run_matrix(args, lambda part: live.update(frame(part)))


def cmd_run(args: argparse.Namespace) -> int:
    runner.install_cancellation()  # SIGINT, SIGTERM, SIGHUP: stop queued columns, kill every child (ARCH-F2)
    m, note = _live_run(args) if not args.quiet else run_matrix(args)
    saved = save(m)
    if not args.quiet:
        print(report.render_terminal(m, color=not args.no_color and sys.stdout.isatty(), width=terminal_width()))
    for line in ([note] if note else []) + [f"saved {saved}"] + [f"wrote {p}" for p in write_exports(m, args)]:
        print(line)
    if getattr(args, "check", False):
        print("\n".join(expectation_lines(m)))
    if not m.columns[1:] and not getattr(args, "fixtures", False):
        print("ZERO_PLUGINS: no directory plugins besides PROVING GROUND are enabled; the baseline shows Hermes "
              "core only. Try: hermes provingground --fixtures")
    return exit_code_for(m, bool(getattr(args, "check", False)))


def notable_text(m: matrix.Matrix) -> str:
    lines = report.notable_cells(m)
    if not lines:
        return "every cell in the latest report is PASSED THROUGH; pick one with: hermes provingground show TC01"
    return "\n".join(["cells with a recorded effect (detail: hermes provingground show <CALL> --column <COLUMN>)",
                      *lines])


def cmd_show(args: argparse.Namespace) -> int:
    m = load_latest()
    if args.call is None:
        print(notable_text(m))
        return 0
    column = args.column or ("stack" if "stack" in m.columns else m.columns[-1])
    if column not in m.observations or args.call not in m.observations[column]:
        print(f"choose a call from {[c.id for c in m.calls]} and a column from {list(m.observations)}")
        return 2
    print(report.render_cell_detail(m, column, args.call))
    return 0


def cmd_warm(args: argparse.Namespace) -> int:
    check_live_posture(user_config(), None)  # warm spawns a child too: same refusals as run (ARCH-F9)
    settings = build_settings(args)
    print(runner.WARM_DISCLOSURE.format(cache=settings.warm_cache), flush=True)
    runner.install_cancellation()
    run = runner.warm_up(settings)
    print("ready" if runner.is_warm(settings) else f"warm-up did not finish: {run.output_tail[-600:]}")
    return 0 if runner.is_warm(settings) else 3


def cmd_sweep(args: argparse.Namespace) -> int:
    removed = runner.sweep_stale_roots(build_settings(args).temp_base)
    print(f"removed {len(removed)} stale sandbox roots")
    return 0


def setup_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("action", nargs="?", default="run", choices=("run", "show", "warm", "sweep"))
    subparser.add_argument("call", nargs="?", default=None, help="test call id for `show`, e.g. TC04")
    subparser.add_argument("--column", default=None, help="column for `show`: baseline, solo:<plugin>, stack")
    subparser.add_argument("--fixtures", action="store_true", help="use the bundled three-plugin demo stack")
    subparser.add_argument("--without", action="append", metavar="PLUGIN", help="leave a plugin out (repeatable)")
    subparser.add_argument("--only", action="append", metavar="CALL", help="run only these test calls (repeatable)")
    subparser.add_argument("--calls", default=None, metavar="FILE",
                           help="extra test calls (ids U…) and expect: contracts, same validator and grammar")
    subparser.add_argument("--check", action="store_true", help="exit 5 when a declared expect: is not met")
    subparser.add_argument("--jobs", type=int, default=3, help="columns run in parallel (default 3)")
    subparser.add_argument("--timeout", type=float, default=120.0, help="seconds per column before it is stopped")
    subparser.add_argument("--json", dest="json_out", metavar="PATH", help="also write the JSON report")
    subparser.add_argument("--markdown", dest="markdown_out", metavar="PATH", help="also write a Markdown report")
    subparser.add_argument("--html", dest="html_out", metavar="PATH", help="also write the HTML report")
    subparser.add_argument("--hermes-bin", default=None, help="path to the hermes executable")
    subparser.add_argument("--no-color", action="store_true")
    subparser.add_argument("--quiet", action="store_true", help="no live table; print only file paths")
    subparser.set_defaults(func=handle_cli)


COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {"run": cmd_run, "show": cmd_show, "warm": cmd_warm,
                                                            "sweep": cmd_sweep}


def handle_cli(args: argparse.Namespace) -> int:
    try:
        return COMMANDS[getattr(args, "action", "run") or "run"](args)
    except safety.Refusal as refusal:
        print(f"{refusal.code}: {refusal.message}")
        return 2


def _slash_args(raw: str) -> argparse.Namespace:
    words = raw.split()
    show = bool(words) and words[0] == "show"
    return argparse.Namespace(action="show" if show else "run", fixtures="fixtures" in words,
                              call=words[1] if show and len(words) > 1 else None,
                              column=words[2] if show and len(words) > 2 else None, without=None, only=None,
                              jobs=3, timeout=120.0, hermes_bin=None, json_out=None, markdown_out=None,
                              html_out=None, no_color=True, quiet=True, calls=None, check=False)


_SLASH_LOCK = threading.Lock()


def _slash_drill(args: argparse.Namespace, platform: str | None) -> None:
    try:
        m, _note = run_matrix(args, platform=platform)
        save(m)
    except Exception:
        return  # the saved report (or its absence) is what /provingground show reports
    finally:
        _SLASH_LOCK.release()


def handle_slash(raw_args: str = "", *, platform: str = "", session_id: str = "", **_: Any) -> str:
    args = _slash_args(raw_args or "")
    try:
        if args.action == "show":
            m = load_latest()
            if args.call is None:
                return notable_text(m)
            column = args.column or ("stack" if "stack" in m.columns else m.columns[-1])
            if column not in m.observations or args.call not in m.observations[column]:
                return f"choose a call from {[c.id for c in m.calls]} and a column from {list(m.observations)}"
            return report.render_cell_detail(m, column, args.call)
        note = check_live_posture(user_config(), platform or None)
        settings = build_settings(args)
        if not runner.is_warm(settings):
            return ("WARM_UP_NEEDED: run `hermes provingground warm` in a terminal first (one-time ~650 MB download "
                    "by Hermes's own installer); the slash command never warms inside your session.")
        if not _SLASH_LOCK.acquire(blocking=False):
            return "A drill is already running; `/provingground show` reads the latest saved report."
        threading.Thread(target=_slash_drill, args=(args, platform or None), daemon=True,
                         name="provingground-drill").start()
        return "\n".join(filter(None, ["Drill started in the background (about 30 s; model: NOT CALLED). "
                                        "Read it with `/provingground show` or `hermes provingground show`.", note]))
    except safety.Refusal as refusal:
        return f"{refusal.code}: {refusal.message}"
