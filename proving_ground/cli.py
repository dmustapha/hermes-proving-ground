# File: proving_ground/cli.py  (part 1 of 2: settings, posture, the run)
# Public modules per hermes_cli/data/plugin_api.json; argparse setup_fn/handler_fn contract
# (plugins_context.py:442-453, docs/index.md "Register CLI commands"); slash handler keywords platform/session_id
# (docs/index.md "Calling-session context").
"""`hermes provingground` and `/provingground`."""
from __future__ import annotations

import argparse
import atexit
import contextvars
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import threading
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Sequence

from . import attended, matrix, report, runner, safety, seed, testcalls

PG_SOURCE = Path(__file__).resolve().parent.parent
REPORTS_SUBDIR = "reports"
LATEST = "latest.json"
LAST_ERROR = "last-error.json"
REPORT_MAX_BYTES = 8 * 1024 * 1024  # a saved report never exceeds this; long strings are clipped
REPORTS_KEPT = 20  # the newest report-*.json files kept; older ones are removed on save
CLIP_MARK = " …[clipped by PROVING GROUND: report size cap]"
EXPORT_FIELD_MAX = 8192  # characters per string in an exported JSON report; the saved report keeps more for `show`
EXPORT_CLIP_MARK = " …[{n} more characters clipped by PROVING GROUND: export field cap]"
CHANGED_EXIT = 6  # --compare, --since-last, diff: at least one plugin, call, cell or finding changed
FIXTURE_KEYS = ("fx_guard", "fx_rewriter", "fx_trimmer")
REPEAT_MAX = 5  # --repeat N: each repeated solo column runs at most this many times
# Columns in parallel. 5 holds the whole wave after the baseline of a three-plugin drill (baseline-repeat, three solos,
# the stack): three fixture drills each took 25.4-28.7 s at 3 and 21.2-25.0 s at 5 on the build machine, same matrix.
DEFAULT_JOBS = 5
DEBUG_ENV = "PROVINGGROUND_DEBUG"  # set it to see the Python traceback instead of the one-line error


def find_hermes(explicit: str | None) -> Path:
    for candidate in (explicit, shutil.which("hermes"), str(Path.home() / ".local/bin/hermes")):
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return Path(candidate).resolve()
    raise safety.Refusal("HERMES_NOT_FOUND", "could not find the hermes executable; pass --hermes-bin PATH.")


def hermes_scratch_dirs() -> list[Path]:
    """Where Hermes's boot points TMPDIR when the user left it unset (hermes_bootstrap.py:455-462): the scratch
    dir it exported, and <HERMES_HOME>/cache/scratch."""
    home = os.environ.get("HERMES_HOME", "").strip()
    dirs = [(Path(home).expanduser() if home else Path.home() / ".hermes") / "cache" / "scratch"]
    marked = os.environ.get("HERMES_SCRATCH_DIR", "").strip()
    return dirs + ([Path(marked)] if marked else [])


def drill_temp_base() -> Path:
    """The temp dir drills use. On Linux with TMPDIR unset, Hermes's boot moves the temp dir into its own scratch
    dir, which is not a system temp base; drills then use the first existing system temp base instead, and
    tempfile follows it so the warm cache, the roots and the sweeps all look at the same place."""
    temp = Path(tempfile.gettempdir()).resolve()
    if not any(temp.is_relative_to(d.resolve()) for d in hermes_scratch_dirs()):
        return temp
    system = next((Path(b) for b in safety.TEMP_BASES if Path(b).is_dir()), None)
    if system is None:
        return temp
    tempfile.tempdir = str(system)
    return system.resolve()


def build_settings(args: argparse.Namespace) -> runner.RunnerSettings:
    if sys.platform.startswith("win"):
        raise safety.Refusal("SANDBOX_UNSUPPORTED", "drills run on macOS and Linux only.")
    temp_base = drill_temp_base()
    if not safety.under_temp_base(temp_base):
        raise safety.Refusal("SANDBOX_UNSUPPORTED", f"temp dir {temp_base} is not under a system temp base; run with TMPDIR=/tmp.")
    return runner.RunnerSettings(hermes_bin=find_hermes(getattr(args, "hermes_bin", None)), pg_source=PG_SOURCE,
                                 warm_cache=temp_base / "provingground-cache" / "installs", temp_base=temp_base,
                                 cell_timeout_s=float(getattr(args, "timeout", 120.0)), jobs=int(getattr(args, "jobs", DEFAULT_JOBS)),
                                 wires=bool(getattr(args, "all_wires", False)),
                                 pairs=bool(getattr(args, "pairs", False)), repeat=repeat_count(args))


def repeat_count(args: argparse.Namespace) -> int:
    repeat = getattr(args, "repeat", 1)
    if not isinstance(repeat, int) or not 1 <= repeat <= REPEAT_MAX:
        raise safety.Refusal("BAD_OPTION", f"--repeat takes a whole number from 1 to {REPEAT_MAX}, not {repeat}.")
    return repeat


def user_config() -> dict[str, Any]:
    from hermes_cli.config import load_config_readonly

    return load_config_readonly() or {}


def check_live_posture(config: dict[str, Any], platform: str | None) -> str:
    """Runs before every spawning entry point: run, warm and the slash command."""
    from cron.execution_identity import current_cron_execution

    mode = safety.normalize_approval_mode((config.get("approvals") or {}).get("mode"))
    safety.check_posture(os.environ, mode, platform, current_cron_execution() is not None)
    if mode == "smart":
        return ("Note: your live approvals mode is smart (an auxiliary model decides). In the sandbox, every "
                "approval is denied; APPROVAL-GATED cells show what Hermes would have asked about.")
    return ""


def refuse_unknown(code: str, what: str, asked: Sequence[str], known: Sequence[str]) -> None:
    """A typo in --without or --only must not silently run another drill than the one asked for."""
    unknown = sorted(set(asked) - set(known))
    if unknown:
        raise safety.Refusal(code, f"unknown {what} {', '.join(unknown)}; choose from {', '.join(sorted(known))}.")


def fixture_stack(args: argparse.Namespace) -> str | None:
    """The bundled stack a drill uses: "demo" (--fixtures), "extended" (--extended, with or without --fixtures; the
    slash command passes fixtures="extended"), or None for the plugins enabled in your Hermes home."""
    fixtures = getattr(args, "fixtures", None)
    if getattr(args, "extended", False) or fixtures == "extended":
        return "extended"
    return "demo" if fixtures else None


def select_subjects(args: argparse.Namespace, settings: runner.RunnerSettings) -> tuple[list[runner.PluginSource], list[tuple[str, str]]]:
    without = list(getattr(args, "without", None) or ())
    if fixture_stack(args):
        extended = fixture_stack(args) == "extended"
        known = runner.fixture_subjects(settings.pg_source, extended=extended)
        refuse_unknown("UNKNOWN_PLUGIN", "plugin", without, [s.key for s in known])
        return runner.fixture_subjects(settings.pg_source, without, extended=extended), []
    from hermes_constants import get_hermes_home

    subjects, skipped = runner.discover_subjects(Path(get_hermes_home()), user_config())
    refuse_unknown("UNKNOWN_PLUGIN", "plugin", without, [s.key for s in subjects] + [k for k, _w in skipped])
    return [s for s in subjects if s.key not in set(without)], skipped


def calls_probe() -> testcalls.SandboxContext:
    """A render-only context shaped like a column's (nothing is created there): user calls are checked against
    the same containment rules and grammar a column applies."""
    sandbox = Path(tempfile.gettempdir()).resolve() / "pg-calls-probe" / "sandbox"
    return testcalls.SandboxContext(sandbox, "http://127.0.0.1:9/probe", testcalls.make_canary())


def select_templates(args: argparse.Namespace) -> list[testcalls.CallTemplate]:
    templates = testcalls.load_templates()
    if getattr(args, "calls", None):
        try:
            templates = testcalls.load_user_calls(Path(args.calls), templates)
            testcalls.render(templates, calls_probe())  # a call that fails the grammar is refused before any spawn
        except (OSError, testcalls.TestCallError) as exc:
            raise safety.Refusal("BAD_CALLS_FILE", str(exc)) from exc
    only = list(getattr(args, "only", None) or ())
    refuse_unknown("UNKNOWN_CALL", "test call", only, [t.id for t in templates])
    return [t for t in templates if not only or t.id in set(only)]


AUTO_WARM_NOTICE = ("warm-up: copying Hermes's runtime from {source} into {cache} (no network; the source is only "
                    "read, and the copy is checked to start before it is used); once per temp dir.")
AUTO_WARM_FALLBACK = "warm-up: the local copy was not used ({reason})."
WARM_UP_NEEDED = ("WARM_UP_NEEDED: run `hermes provingground warm` in a terminal first (one-time ~650 MB download "
                  "by Hermes's own installer); the slash command never downloads inside your session.")


def local_installs() -> Path | None:
    """The runtime Hermes already downloaded: the installs dir of the current Hermes home, else of ~/.hermes (a
    profile or a custom HERMES_HOME may have none of its own). Only ever read."""
    homes = [Path.home() / ".hermes"]
    try:
        from hermes_constants import get_hermes_home

        homes.insert(0, Path(get_hermes_home()))
    except Exception:  # a Hermes without hermes_constants: only its default home
        pass
    return next((home / "installs" for home in homes if (home / "installs").is_dir()), None)


def warm_from_local(settings: runner.RunnerSettings) -> bool:
    """Cold cache, local Hermes installs present: seed the cache from them (`warm --from` logic), in one line.
    False when there is nothing to copy from or the copy was refused; the refusal is printed, nothing is left."""
    source = local_installs()
    if source is None:
        return False
    print(AUTO_WARM_NOTICE.format(source=source, cache=settings.warm_cache), file=sys.stderr, flush=True)
    try:
        seed.seed_cache(settings, source)
    except safety.Refusal as refusal:
        print(AUTO_WARM_FALLBACK.format(reason=f"{refusal.code}: {refusal.message}"), file=sys.stderr, flush=True)
        return False
    return runner.is_warm(settings)


def ensure_warm(settings: runner.RunnerSettings, allow_download: bool = True) -> None:
    """Warm the runtime cache once: from your local Hermes installs when they exist (no network), else by
    Hermes's own installer (announced in one line). A warm-up that fails is a clean refusal, never a drill against
    a cold cache; without *allow_download* (the slash command) the download is refused instead."""
    if runner.is_warm(settings) or warm_from_local(settings):
        return
    if not allow_download:
        code, _, message = WARM_UP_NEEDED.partition(": ")
        raise safety.Refusal(code, message)
    print(runner.WARM_NOTICE.format(cache=settings.warm_cache), file=sys.stderr, flush=True)
    run = runner.warm_up(settings)
    if not runner.is_warm(settings):
        raise runner.warm_failure(run)


def call_infos(templates: Sequence[testcalls.CallTemplate]) -> list[matrix.CallInfo]:
    return [matrix.CallInfo(t.id, t.tool, t.subject, t.purpose, t.provenance, dict(t.expect)) for t in templates]


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def credentials_line(notes: Sequence[str]) -> str:
    base = "no provider keys in the child env (allow-list)"
    if not notes:
        return base + "; no credential-shaped files or settings found in the copied plugins"
    return base + f"; withheld from the copies: {'; '.join(notes[:4])}" + (" …" if len(notes) > 4 else "")


WIRES_NOTE = ("context and reply recorded in every column (pre_llm_call, transform_llm_output and post_llm_call "
              "taps that return nothing); two extra rows, not counted as test calls")


def repeat_note(drill: runner.DrillRun, m: matrix.Matrix) -> str:
    if not drill.repeats:
        return f"{drill.repeat} runs asked; no solo column has a plugin effect, so none was run again"
    stopped = sum(1 for runs in drill.repeats.values() for r in runs if r.abort_reason or r.load_failed)
    unstable = sum(1 for cell in m.cells.values() if cell.varied)
    names = ", ".join(n.removeprefix("solo:") for n in drill.repeats)
    disagree = f"{unstable} cell disagrees" if unstable == 1 else f"{unstable} cells disagree"
    why = ("every solo column, since an effect appears only in the full stack"
           if any(a.kind == "interaction" for a in m.attributions) else "the solo columns with a plugin effect")
    return (f"{drill.repeat} solo runs each of {names} ({why}); "
            f"{disagree} between runs (UNSTABLE)"
            + (f"; {stopped} repeat runs stopped and were not compared" if stopped else ""))


def drill_options(drill: runner.DrillRun, infos: Sequence[matrix.CallInfo], m: matrix.Matrix) -> dict[str, str]:
    """The opt-in options this drill ran with, as report lines; empty for a default drill."""
    options = {"wires": WIRES_NOTE} if any(i.tool == matrix.WIRE_TOOL for i in infos) else {}
    if drill.pair_total is not None:
        options["pairs"] = drill.pair_note
    if drill.repeat > 1:
        options["repeat"] = repeat_note(drill, m)
    return options


def all_infos(templates: Sequence[testcalls.CallTemplate], settings: runner.RunnerSettings) -> list[matrix.CallInfo]:
    return call_infos(templates) + (list(matrix.WIRE_CALLS) if settings.wires else [])


def assemble(drill: runner.DrillRun, infos: Sequence[matrix.CallInfo], version: str, started: str,
             leftover: tuple[int, int] | None = None) -> matrix.Matrix:
    extra: dict[str, Any] = {"credentials": credentials_line(drill.copy_notes),
                             "approvals": runner.approvals_summary()}
    if leftover is not None:
        extra.update(leftover_processes=leftover[0], leftover_roots=leftover[1])
    m = matrix.build_matrix(drill.columns, drill.baseline_repeat, drill.crosscheck, infos, version, started,
                            drill.wall_s, drill.skipped, drill.needs_env, load_order=drill.load_order,
                            unreplayable=drill.unreplayable, conditions_extra=extra, repeats=drill.repeats,
                            pair_total=drill.pair_total)
    plugins = {c.name: c.plugins for c in drill.columns if c.name in m.columns}
    plugins.update({f"solo:{k}": (k,) for k in drill.needs_env})
    return replace(m, options=drill_options(drill, infos, m), column_plugins=plugins,
                   declared_hooks=dict(drill.declared_hooks))


def planned_columns(subjects: Sequence[runner.PluginSource]) -> tuple[str, ...]:
    """Display order, fixed before any column finishes: baseline, solos in load order, FULL STACK."""
    return tuple(spec.name for spec in runner.plan_columns(runner.load_order(subjects)))


def run_matrix(args: argparse.Namespace, on_partial: Callable[[report.LiveFrame], None] | None = None,
               platform: str | None = None) -> tuple[matrix.Matrix, str]:
    config = user_config()
    note = check_live_posture(config, platform)
    settings = build_settings(args)
    runner.sweep_stale_roots(settings.temp_base)
    subjects, skipped = select_subjects(args, settings)
    templates = select_templates(args)
    ensure_warm(settings, allow_download=getattr(args, "download_ok", True))
    infos, planned, done = all_infos(templates, settings), planned_columns(subjects), []
    if on_partial is not None:
        on_partial(report.LiveFrame(tuple(infos), planned))
    version, started = runner.hermes_version(settings.hermes_bin, settings), now_iso()

    def partial(column: matrix.ColumnRun) -> None:
        done.append(column)
        if on_partial is not None:
            on_partial(live_frame(done, planned, infos, version, started))

    drill = runner.run_drill(subjects, templates, settings, partial, skipped)
    runner.reap_orphans(settings.temp_base)
    leftover = runner.leftovers(settings.temp_base)
    return assemble(drill, infos, version, started, leftover), note


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


def _clip(value: Any, limit: int, mark: str = CLIP_MARK) -> Any:
    """Every string longer than *limit* is cut and marked; *mark* may name the cut length as {n}."""
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + mark.format(n=len(value) - limit)
    if isinstance(value, dict):
        return {k: _clip(v, limit, mark) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v, limit, mark) for v in value]
    return value


def export_json(m: matrix.Matrix) -> str:
    """The exported JSON report: each string capped at EXPORT_FIELD_MAX characters (a long raw output is the
    only field that reaches it), so the file stays small enough to attach or paste."""
    return json.dumps(_clip(report.matrix_to_dict(m), EXPORT_FIELD_MAX, EXPORT_CLIP_MARK), indent=2,
                      ensure_ascii=False)


def shareable(m: matrix.Matrix) -> matrix.Matrix:
    """The matrix as it is saved and exported: this machine's home and temp paths in any recorded text (a plugin
    traceback, an error message) become ~ and {TMP}. The terminal view, which stays local, is not changed."""
    temp = tempfile.gettempdir()
    rules = report.path_rules({str(Path.home()): "~", temp: "{TMP}", os.path.realpath(temp): "{TMP}"})
    return report.matrix_from_dict(report.scrub_paths(report.matrix_to_dict(m), rules))


def bounded_report_json(m: matrix.Matrix, max_bytes: int = REPORT_MAX_BYTES) -> str:
    """The report JSON, with the longest strings clipped (and marked) until it fits *max_bytes*."""
    data, limit = report.matrix_to_dict(shareable(m)), 1 << 20
    text = json.dumps(data, indent=2, ensure_ascii=False)
    while len(text.encode("utf-8")) > max_bytes and limit >= 64:
        text, limit = json.dumps(_clip(data, limit), indent=2, ensure_ascii=False), limit // 4
    return text


def _prune_reports(directory: Path, keep: int = REPORTS_KEPT) -> None:
    for old in sorted(directory.glob("report-*.json"))[:-keep]:
        old.unlink(missing_ok=True)


def save(m: matrix.Matrix) -> Path:
    data = bounded_report_json(m)
    stamp = m.conditions.started_at.replace(":", "").replace("+", "Z")
    target = reports_dir() / f"report-{stamp}.json"
    target.write_text(data, encoding="utf-8")
    (reports_dir() / LATEST).write_text(data, encoding="utf-8")
    _prune_reports(reports_dir())
    return target


def record_drill_failure(exc: BaseException) -> None:
    """A failed slash drill leaves last-error.json, so `show` never serves the previous report as current."""
    reason = {"code": getattr(exc, "code", type(exc).__name__), "message": str(getattr(exc, "message", exc))[:2000], "at": now_iso()}
    try:
        (reports_dir() / LAST_ERROR).write_text(json.dumps(reason), encoding="utf-8")
    except Exception:  # no plugin storage: nothing to record into; show then reports what is saved
        return


def clear_drill_failure() -> None:
    try:
        (reports_dir() / LAST_ERROR).unlink(missing_ok=True)
    except Exception:
        return


def last_drill_failure() -> str | None:
    """"last drill failed: <reason>" when the newest drill failed after the latest saved report, else None."""
    try:
        directory = reports_dir()
        error, latest = directory / LAST_ERROR, directory / LATEST
        if not error.is_file() or (latest.is_file() and latest.stat().st_mtime > error.stat().st_mtime):
            return None
        data = json.loads(error.read_text(encoding="utf-8"))
    except Exception:
        return None
    return f"last drill failed: {data.get('code')}: {data.get('message')} (at {data.get('at')})"


def read_report(path: Path, what: str = "the saved report",
                hint: str = "run a new drill to replace it") -> matrix.Matrix:
    """A report JSON from disk; a file that is not one is a clean BAD_REPORT refusal, never a traceback."""
    try:
        return report.matrix_from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise safety.Refusal("BAD_REPORT", f"{what} {path.name} cannot be read ({type(exc).__name__}: "
                             f"{str(exc)[:200]}); {hint}.") from exc


def load_latest() -> matrix.Matrix:
    path = reports_dir() / LATEST
    if not path.is_file():
        raise safety.Refusal("NO_REPORT", "no report yet; run `hermes provingground` (or add --fixtures) first.")
    return read_report(path)


def load_report_file(name: str) -> matrix.Matrix:
    """A report named on the command line (--compare, diff): saved by a drill or exported with --json."""
    path = Path(name)
    if not path.is_file():
        raise safety.Refusal("NO_REPORT", f"no report file at {name}; pass a JSON report saved by a drill or "
                             "written with --json.")
    return read_report(path, "the report", "pass a JSON report saved by a drill or written with --json")


def _render_for(flag: str, focus: bool) -> Callable[[matrix.Matrix], str]:
    return {"json_out": export_json, "markdown_out": lambda x: report.render_markdown(x, focus),
            "html_out": lambda x: report.render_html(x, focus), "write_contract": report.render_contract}[flag]


def write_exports(m: matrix.Matrix, args: argparse.Namespace) -> list[str]:
    written, m, focus = [], shareable(m), bool(getattr(args, "focus", False))
    for flag in ("json_out", "markdown_out", "html_out", "write_contract"):
        render = _render_for(flag, focus)
        target = getattr(args, flag, None)
        if not target:
            continue
        try:
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            Path(target).write_text(render(m), encoding="utf-8")
        except OSError as exc:
            raise safety.Refusal("EXPORT_FAILED", f"could not write {target} ({exc.strerror or exc}); the drill's "
                                 "report is saved: `hermes provingground show --html PATH` exports it again.") from exc
        written.append(str(target))
    return written


def contract_total(m: matrix.Matrix) -> int:
    return sum(len(c.expect or {}) for c in m.calls)


def exit_code_for(m: matrix.Matrix, check: bool = False) -> int:
    if any(f.kind == "ESCAPED" for f in m.findings):
        return 4
    if m.aborted:
        return 3
    if not check:
        return 0
    unmet, missing = matrix.check_expectations(m)
    return 5 if unmet or missing or contract_total(m) == 0 else 0


def expectation_lines(m: matrix.Matrix) -> list[str]:
    """--check output: unmet and unevaluated expectations both fail the contract, and so does an empty one."""
    unmet, missing = matrix.check_expectations(m)
    total = contract_total(m)
    if total == 0:
        return ["CHECK FAILED: --check was given but no test call declares an expect: contract"]
    label = lambda token: matrix.LABELS[matrix.Outcome(token)]  # noqa: E731
    lines = [f"CHECK FAILED {cid} × {col}: expected {' or '.join(label(a) for a in allowed)}, observed {label(actual)}"
             for cid, col, allowed, actual in unmet]
    lines += [f"CHECK FAILED {cid} × {col}: that column is not in this run" for cid, col in missing]
    return lines + [f"contract: {total - len(unmet) - len(missing)} of {total} expectations met"]


def terminal_width() -> int:
    return shutil.get_terminal_size((160, 40)).columns


def use_color(args: argparse.Namespace) -> bool:
    """Colour on a terminal unless --no-color or the NO_COLOR convention (any non-empty value) says otherwise."""
    return not getattr(args, "no_color", False) and not os.environ.get("NO_COLOR") and sys.stdout.isatty()


def _live_run(args: argparse.Namespace) -> tuple[matrix.Matrix, str]:
    color, width = use_color(args), terminal_width()
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


NO_EARLIER_REPORT = "compare latest: no earlier saved report to compare with; this drill is now the saved one"
COMPARE_LATEST = "latest"  # `--compare latest`: the report saved before this drill (--since-last is its old name)


def compares_latest(args: argparse.Namespace) -> bool:
    return getattr(args, "compare", None) == COMPARE_LATEST or bool(getattr(args, "since_last", False))


def earlier_report(args: argparse.Namespace) -> tuple[matrix.Matrix, str] | None:
    """The report --compare compares against, read before the drill: a bad file is refused before anything is
    spawned, and latest.json is read before this drill replaces it."""
    if compares_latest(args):
        return (load_latest(), "the last saved report") if (reports_dir() / LATEST).is_file() else None
    if getattr(args, "compare", None):
        return load_report_file(args.compare), str(args.compare)
    return None


def print_comparison(old: matrix.Matrix, old_name: str, new: matrix.Matrix, new_name: str) -> int:
    """Prints only what changed; CHANGED_EXIT when anything did, 0 when nothing did."""
    diff = matrix.compare(old, new)
    print("\n".join(report.diff_lines(old, new, diff, old_name, new_name)))
    return CHANGED_EXIT if diff.changed else 0


def check_attended_posture(args: argparse.Namespace) -> list[testcalls.CallTemplate]:
    """--attended: every refusal runs before anything is spawned; returns the calls Hermes will ask about."""
    attended.check_attended(os.environ, sys.stdin, sys.stdout)
    check_live_posture(user_config(), None)
    return attended.approval_templates(select_templates(args))


def run_attended_column(args: argparse.Namespace, asked: Sequence[testcalls.CallTemplate]) -> int:
    """The extra full-stack column after the headless matrix: Hermes asks, the person at the terminal answers."""
    settings = build_settings(args)
    subjects, _skipped = select_subjects(args, settings)
    print(attended.preface(len(asked)), flush=True)
    run = attended.run_attended(subjects, asked, settings, *attended.stdio_fds())
    runner.reap_orphans(settings.temp_base)
    print(attended.render_column(run, {t.id: t.subject for t in asked}), flush=True)
    return attended.exit_code(run)


def cmd_run(args: argparse.Namespace) -> int:
    asked = check_attended_posture(args) if getattr(args, "attended", False) else None
    runner.install_cancellation()  # SIGINT, SIGTERM, SIGHUP: stop queued columns, kill every child
    earlier = earlier_report(args)
    m, note = _live_run(args) if not args.quiet else run_matrix(args)
    saved = save(m)
    if not args.quiet:
        print(report.render_terminal(m, color=use_color(args), width=terminal_width(),
                                     focus=bool(getattr(args, "focus", False))))
    for line in ([note] if note else []) + [f"saved {saved}"] + [f"wrote {p}" for p in write_exports(m, args)]:
        print(line)
    if getattr(args, "check", False):
        print("\n".join(expectation_lines(m)))
    if not m.columns[1:] and not fixture_stack(args):
        print("ZERO_PLUGINS: no directory plugins besides PROVING GROUND are enabled; the baseline shows Hermes "
              "core only. Try: hermes provingground --fixtures")
    changed = print_comparison(*earlier, shareable(m), "this drill") if earlier else 0
    if compares_latest(args) and earlier is None:
        print(NO_EARLIER_REPORT)
    attended_code = run_attended_column(args, asked) if asked is not None else 0
    return exit_code_for(m, bool(getattr(args, "check", False))) or attended_code or changed


def notable_text(m: matrix.Matrix) -> str:
    lines = report.notable_cells(m)
    if not lines:
        return "every cell in the latest report is PASSED THROUGH; pick one with: hermes provingground show TC01"
    return "\n".join(["cells with a recorded effect (detail: hermes provingground show <CALL> --column <COLUMN>)",
                      *lines])


def resolve_column(m: matrix.Matrix, column: str | None) -> str:
    """`--column` takes baseline, stack, solo:<plugin> or a bare plugin name."""
    if column is None:
        return "stack" if "stack" in m.columns else m.columns[-1]
    return f"solo:{column}" if column not in m.observations and f"solo:{column}" in m.observations else column


def cmd_show(args: argparse.Namespace) -> int:
    failure = last_drill_failure()
    if failure:
        print(failure + "\nthe report below is from the previous successful drill")
    m = load_latest()
    if getattr(args, "plugins", False):
        print("\n".join(report.hooks_legend(m)))
        return 0
    written = write_exports(m, args)
    for path in written:
        print(f"wrote {path}")
    if args.call is None:
        if not written:
            print(notable_text(m))
        return 0
    column = resolve_column(m, args.column)
    if column not in m.observations or args.call not in m.observations[column]:
        print(f"choose a call from {[c.id for c in m.calls]} and a column from {list(m.observations)}")
        return 2
    print(report.render_cell_detail(m, column, args.call))
    return 0


def warm_status_asked(args: argparse.Namespace) -> bool:
    """`warm --status` (`warm --check` is its old name, still accepted)."""
    return bool(getattr(args, "status", False) or getattr(args, "check", False))


def cmd_warm(args: argparse.Namespace) -> int:
    if warm_status_asked(args):  # read-only preflight: spawns nothing, downloads nothing
        settings = build_settings(args)
        warm = runner.is_warm(settings)
        print(f"{'warm' if warm else 'COLD'}: runtime cache {settings.warm_cache} (temp dir {settings.temp_base})"
              + ("" if warm else "; run `hermes provingground warm` in this same shell (same TMPDIR) first"))
        return 0 if warm else 3
    check_live_posture(user_config(), None)  # warm spawns a child too: same refusals as run
    settings = build_settings(args)
    if getattr(args, "warm_from", None):
        return warm_from(settings, Path(args.warm_from))
    if runner.is_warm(settings) or warm_from_local(settings):
        print(f"ready: runtime cache {settings.warm_cache}")
        return 0
    print(runner.WARM_DISCLOSURE.format(cache=settings.warm_cache), flush=True)
    runner.install_cancellation()
    run = runner.warm_up(settings)
    if not runner.is_warm(settings):
        raise runner.warm_failure(run)
    print("ready")
    return 0


def warm_from(settings: runner.RunnerSettings, source: Path) -> int:
    """Offline warm-up: copy an existing Hermes installs dir into the private cache (the source is only read)."""
    if runner.is_warm(settings):
        print(f"warm: runtime cache {settings.warm_cache} is already ready; nothing copied")
        return 0
    print(seed.SEED_DISCLOSURE.format(source=source, cache=settings.warm_cache), flush=True)
    runner.install_cancellation()
    result = seed.seed_cache(settings, source)
    print(f"copied {len(result.installs)} install(s) ({', '.join(result.installs)}); {result.rewritten} record(s) "
          f"pointed at the copy; checked with `hermes --version` in a throwaway home: {result.version}")
    print("ready")
    return 0


def refuse_misplaced_options(args: argparse.Namespace) -> None:
    """--from and --status belong to warm only, and not together (--status never copies anything)."""
    action = getattr(args, "action", "run") or "run"
    if getattr(args, "attended", False) and action != "run":
        raise safety.Refusal("BAD_OPTION", "--attended is for a drill run only: hermes provingground --attended.")
    if getattr(args, "status", False) and action != "warm":
        raise safety.Refusal("BAD_OPTION", "--status is for `hermes provingground warm --status` only.")
    if not getattr(args, "warm_from", None):
        return
    if action != "warm":
        raise safety.Refusal("BAD_OPTION", "--from is for `hermes provingground warm --from DIR` only.")
    if warm_status_asked(args):
        raise safety.Refusal("BAD_OPTION", "--status only reports the cache state; drop --from, or drop --status.")


def cmd_sweep(args: argparse.Namespace) -> int:
    removed = runner.sweep_stale_roots(build_settings(args).temp_base)
    print(f"removed {len(removed)} stale sandbox roots")
    return 0


def cmd_lock(args: argparse.Namespace) -> int:
    """`lock FILE`: run the drill and write the expect: contract pinning every built-in cell to what it observed."""
    target = getattr(args, "call", None)
    if not target:
        raise safety.Refusal("BAD_INPUT", "lock writes a contract file: hermes provingground lock stack.lock.yaml "
                             "(later: --calls stack.lock.yaml --check)")
    args.write_contract = target
    return cmd_run(args)


def cmd_diff(args: argparse.Namespace) -> int:
    """Two saved reports, no drill: prints only what changed between them."""
    first, second = getattr(args, "call", None), getattr(args, "target", None)
    if not first or not second:
        raise safety.Refusal("BAD_INPUT", "diff compares two reports: hermes provingground diff A.json B.json")
    return print_comparison(load_report_file(first), first, load_report_file(second), second)


USAGE_EPILOG = """actions:
  run    (default) drill the directory plugins enabled in your Hermes home, or the demo stack with --fixtures
  show   the cells with an effect in the latest saved report; `show TC04 --column stack` prints one cell;
         `show --html PATH` (or --json / --markdown / --write-contract) exports the saved report again;
         `show --plugins` lists each plugin's declared hooks (plugin.yaml provides_hooks) and each column's plugins
  lock   `lock FILE` runs the drill and writes an expect: contract pinning every built-in cell to what it saw
  diff   `diff A.json B.json` prints only what changed between two saved reports (no drill)
  warm   prepare the isolated Hermes runtime once: copied from your Hermes installs when there are any (no
         network), else downloaded by Hermes's own installer; `warm --status` only reports whether it is ready;
         `warm --from DIR` copies it from another installs dir (the source is only read)
  sweep  remove stale temporary drill roots left by an interrupted drill (only roots PROVING GROUND marked)

outcomes (one per cell; a stack cell that carries a cross-plugin finding says so, e.g. "unseen by fx_guard"):
  BLOCKED · REWROTE · RESULT CHANGED · APPROVAL-GATED · PASSED THROUGH
  middleware: REQUEST REWRITTEN · MODEL REPLY CHANGED · EXECUTION REPLACED; --attended: ASKED (a person answered)
  more: ESCAPED, ERRORED, LOAD FAILED, NOT RUN, N/A (statuses, never results); "(core)" = Hermes itself

examples:
  hermes provingground --fixtures
  hermes provingground show TC04 --column stack
  hermes provingground --calls my-calls.yaml --check --json pg-report.json
  hermes provingground lock stack.lock.yaml --fixtures      # later: --calls stack.lock.yaml --check
  hermes provingground --compare latest --focus

user calls (--calls): a closed grammar: echo/printf whose first word is not a flag (later words are printed),
  seq without flags, curl -q to the run's recorder, chmod with an octal mode on sandbox paths; no | ; & $ .. ~

also accepted: --jobs N, --timeout SECONDS, --hermes-bin PATH, --quiet, --no-color (NO_COLOR is honoured)

exit codes:
  0    done (with --check: every expect: met; with --compare or diff: nothing changed)
  1    unexpected error (one line plus a crash log path)
  2    refused before running: posture, bad input, unknown name, failed warm-up, no or unreadable report
  3    a column stopped (timeout, abort), or `warm --status` found the cache cold
  4    a call was ESCAPED (detected after it ran; that column was stopped)
  5    --check: an expect: was not met or not evaluated, or no expect: was declared
  6    --compare or diff: a plugin, test call, cell outcome or finding changed
       (3, 4 and 5 take precedence)
  130  cancelled (Ctrl-C, SIGTERM, SIGHUP); every child was stopped
"""
ACTIONS = ("run", "show", "lock", "diff", "warm", "sweep")
HIDDEN = argparse.SUPPRESS  # accepted, left out of the short help; the epilog names them


def _add_run_options(subparser: argparse.ArgumentParser) -> None:
    group = subparser.add_argument_group("run")
    group.add_argument("--fixtures", action="store_const", const="demo", default=None,
                       help="drill the bundled three-plugin demo stack instead of your enabled plugins")
    group.add_argument("--extended", action="store_true",
                       help="the demo stack plus a fourth demo plugin (fixtures/extended); implies --fixtures")
    group.add_argument("--without", action="append", metavar="PLUGIN", help="leave a plugin out (repeatable)")
    group.add_argument("--only", action="append", metavar="CALL", help="run only these test calls (repeatable)")
    group.add_argument("--calls", default=None, metavar="FILE",
                       help="extra test calls (ids U…) and expect: contracts, same validator and grammar")
    group.add_argument("--check", action="store_true", help="exit 5 unless every declared expect: is met")
    group.add_argument("--attended", action="store_true",
                       help="add one full-stack column where Hermes asks you to approve the approval-worthy calls "
                            f"(you answer; {attended.APPROVAL_TIMEOUT_S} s timeout; needs a terminal)")
    warm = subparser.add_argument_group("warm")
    warm.add_argument("--status", action="store_true", help="only report whether the runtime cache is ready")
    warm.add_argument("--from", dest="warm_from", default=None, metavar="DIR",
                      help="copy the runtime from this Hermes installs dir (only read)")


def _add_report_options(subparser: argparse.ArgumentParser) -> None:
    """Exports, views and comparisons of the matrix a drill (or `show`) produces."""
    group = subparser.add_argument_group("report")
    group.add_argument("--column", default=None,
                       help="column for `show`: baseline, stack, solo:<plugin> or just the plugin name")
    group.add_argument("--plugins", action="store_true",
                       help="show: list each tested plugin's declared hooks and the plugins in each column")
    group.add_argument("--json", dest="json_out", metavar="PATH", help="also write the JSON report")
    group.add_argument("--markdown", dest="markdown_out", metavar="PATH", help="also write a Markdown report")
    group.add_argument("--html", dest="html_out", metavar="PATH", help="also write the HTML report")
    group.add_argument("--write-contract", dest="write_contract", metavar="PATH",
                       help="write an expect: file pinning every built-in cell to this outcome (the `lock` action)")
    group.add_argument("--focus", action="store_true",
                       help="terminal and Markdown: hide rows where every cell is PASSED THROUGH; HTML: start with "
                            "its focus toggle on")
    since = subparser.add_argument_group("compare").add_mutually_exclusive_group()
    since.add_argument("--compare", metavar="REPORT", default=None,
                       help="after the drill, print only what changed against REPORT, or against the report saved "
                            "before this drill with `--compare latest` (exit 6 if anything changed)")
    since.add_argument("--since-last", dest="since_last", action="store_true", help=HIDDEN)


def _add_advanced_options(subparser: argparse.ArgumentParser) -> None:
    group = subparser.add_argument_group("advanced")
    group.add_argument("--all-wires", action="store_true",
                       help="also record the context sent to the model and the final reply: two extra rows, "
                            "CONTEXT ADDED and REPLY CHANGED")
    group.add_argument("--pairs", action="store_true",
                       help="for an effect seen only in the full stack, run pairs of plugins to name the smallest "
                            f"set that reproduces it (at most {matrix.PAIR_CAP} pair columns)")
    group.add_argument("--repeat", type=int, default=1, metavar="N",
                       help=f"run solo columns N times in all (1-{REPEAT_MAX}); runs that disagree mark the cell "
                            "UNSTABLE")
    group.add_argument("--jobs", type=int, default=DEFAULT_JOBS, help=HIDDEN)
    group.add_argument("--timeout", type=float, default=120.0, help=HIDDEN)
    group.add_argument("--hermes-bin", default=None, help=HIDDEN)
    group.add_argument("--no-color", action="store_true", help=HIDDEN)
    group.add_argument("--quiet", action="store_true", help=HIDDEN)


def setup_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.formatter_class = argparse.RawDescriptionHelpFormatter
    subparser.epilog = USAGE_EPILOG
    subparser.add_argument("action", nargs="?", default="run", choices=ACTIONS,
                           help="what to do (default: run); see actions below")
    subparser.add_argument("call", nargs="?", default=None,
                           help="test call id for `show` (e.g. TC04), the file for `lock`, or the first report "
                                "for `diff`")
    subparser.add_argument("target", nargs="?", default=None, help="the second report for `diff`")
    _add_run_options(subparser)
    _add_report_options(subparser)
    _add_advanced_options(subparser)
    subparser.set_defaults(func=handle_cli)


COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {"run": cmd_run, "show": cmd_show, "lock": cmd_lock,
                                                            "diff": cmd_diff, "warm": cmd_warm, "sweep": cmd_sweep}


def write_crash_log(exc: BaseException) -> Path | None:
    """The full traceback goes to a private file; the user sees one line naming it."""
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    name = f"crash-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.log"
    for directory in (reports_dir, lambda: Path(tempfile.gettempdir())):
        try:
            fd = os.open(directory() / name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            return directory() / name
        except Exception:
            continue
    return None


def handle_cli(args: argparse.Namespace) -> int:
    """Every outcome ends in one readable line and an exit code, never a Python traceback."""
    try:
        refuse_misplaced_options(args)
        return COMMANDS[getattr(args, "action", "run") or "run"](args)
    except safety.Refusal as refusal:
        print(f"{refusal.code}: {refusal.message}")
        return 2
    except (KeyboardInterrupt, runner.Cancelled):
        print(f"drill cancelled; cleaned up {runner.cancel_everything()} processes", flush=True)
        return 130
    except Exception as exc:
        if os.environ.get(DEBUG_ENV):
            raise
        log = write_crash_log(exc)
        print(f"DRILL_ERROR: {type(exc).__name__}: {str(exc)[:300]}"
              + (f" (crash log: {log})" if log else "") + f"; set {DEBUG_ENV}=1 for the traceback")
        return 1


def _parser_defaults() -> argparse.Namespace:
    """Every option at its CLI default, so the slash path carries the same complete namespace as the CLI."""
    parser = argparse.ArgumentParser(add_help=False)
    setup_parser(parser)
    return parser.parse_args([])


def _slash_args(raw: str) -> argparse.Namespace:
    words = raw.split()
    show = bool(words) and words[0] == "show"
    fixtures = ("extended" if "extended" in words else True) if "fixtures" in words else False
    args = _parser_defaults()
    args.__dict__.update(action="show" if show else "run", fixtures=fixtures,
                         call=words[1] if show and len(words) > 1 else None,
                         column=words[2] if show and len(words) > 2 else None,
                         no_color=True, quiet=True, download_ok=False)  # a slash drill never downloads
    return args


_SLASH_LOCK = threading.Lock()


_ATEXIT_REGISTERED = threading.Event()


def _register_slash_cleanup() -> None:
    """The slash drill runs in a thread of the live Hermes process, where install_cancellation cannot install
    signal handlers; atexit still kills every live child group and removes every live root."""
    if not _ATEXIT_REGISTERED.is_set():
        _ATEXIT_REGISTERED.set()
        atexit.register(runner.cancel_everything)


def slash_summary(m: matrix.Matrix) -> str:
    """What a finished slash drill posts into the chat: the tally, the findings, the cells with an effect."""
    return "\n".join(["PROVING GROUND drill finished (model: NOT CALLED).",
                      f"{report.OBSERVED.format(n=m.conditions.n_calls)} {matrix.tally(m)}",
                      *report.finding_lines(m), notable_text(m)])


def post_quietly(post: Callable[[str], Any] | None, text: str) -> None:
    """ctx.post_message never raises by contract (ok=False when this surface has no conversation); a Hermes
    without it, or a refused post, leaves `/provingground show` as the way to read the result."""
    if post is None:
        return
    try:
        post(text)
    except Exception:
        return


def _slash_drill(args: argparse.Namespace, platform: str | None, post: Callable[[str], Any] | None = None) -> None:
    try:
        m, _note = run_matrix(args, platform=platform)
        save(m)
        summary = slash_summary(m)
    except BaseException as exc:  # recorded, so show reports the failure instead of a stale report
        record_drill_failure(exc)
        summary = (f"PROVING GROUND drill failed: {getattr(exc, 'code', type(exc).__name__)}: "
                   f"{str(getattr(exc, 'message', exc))[:300]}")
    finally:
        _SLASH_LOCK.release()
    post_quietly(post, summary)


def _start_slash_drill(args: argparse.Namespace, platform: str | None,
                       post: Callable[[str], Any] | None = None) -> None:
    """The drill thread runs in a copy of the caller's context, so post_message still finds the calling session
    (Hermes binds it in context variables, which a plain thread does not inherit)."""
    clear_drill_failure()
    _register_slash_cleanup()
    try:
        threading.Thread(target=contextvars.copy_context().run, args=(_slash_drill, args, platform, post),
                         daemon=True, name="provingground-drill").start()
    except BaseException:
        _SLASH_LOCK.release()  # a thread that never started never releases the lock itself
        raise


DRILL_RUNNING = "A drill is running in the background; its report is not saved yet. Try `/provingground show` again in about 30 s."


def _slash_show(args: argparse.Namespace) -> str:
    failure, running = last_drill_failure(), _SLASH_LOCK.locked()
    try:
        m = load_latest()
    except safety.Refusal as refusal:
        if running and refusal.code == "NO_REPORT":
            return DRILL_RUNNING
        return "\n".join(filter(None, [failure, f"{refusal.code}: {refusal.message}"]))
    if args.call is None:
        body = notable_text(m)
    else:
        column = resolve_column(m, args.column)
        if column not in m.observations or args.call not in m.observations[column]:
            return f"choose a call from {[c.id for c in m.calls]} and a column from {list(m.observations)}"
        body = report.render_cell_detail(m, column, args.call)
    if failure:
        return f"{failure}\nthe report below is from the previous successful drill ({m.conditions.started_at}):\n{body}"
    if running:
        return f"A drill is running in the background; the report below is the previous one ({m.conditions.started_at}).\n{body}"
    return body


SLASH_STARTED = ("Drill started in the background (about 30 s; model: NOT CALLED). When it finishes, its summary is "
                 "posted here where this chat surface supports it; `/provingground show` reads it any time.")
SLASH_COPYING = ("First run: Hermes's runtime is copied from {source} first (no network; the source is only read), "
                 "which adds about a minute.")


def handle_slash(raw_args: str = "", *, platform: str = "", session_id: str = "",
                 post: Callable[[str], Any] | None = None, **_: Any) -> str:
    """*post* is the plugin context's post_message (Hermes 0.23+), passed by the plugin entry when it exists."""
    args = _slash_args(raw_args or "")
    try:
        if {"attended", "--attended"} & set((raw_args or "").split()):
            attended.check_attended(os.environ, None, None, slash=True)
        if args.action == "show":
            return _slash_show(args)
        note = check_live_posture(user_config(), platform or None)
        settings = build_settings(args)
        source = None if runner.is_warm(settings) else local_installs()
        if not runner.is_warm(settings) and source is None:
            return WARM_UP_NEEDED
        if not _SLASH_LOCK.acquire(blocking=False):
            return "A drill is already running; `/provingground show` reads the latest saved report."
        _start_slash_drill(args, platform or None, post)
        copying = SLASH_COPYING.format(source=source) if source else ""
        return "\n".join(filter(None, [SLASH_STARTED, copying, note]))
    except safety.Refusal as refusal:
        return f"{refusal.code}: {refusal.message}"
