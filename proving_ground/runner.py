# File: proving_ground/runner.py  (part 1 of 5: types, cancellation, recorder)
# [VERIFIED by the §9.1 run] stdlib only. Recorder binds 127.0.0.1 (never all interfaces; scanner bind_all_interfaces is high,
# loopback hardcoded_ip_port is demoted to low per challenge-PG-safety §3a).
"""Child-drill runner: one throwaway Hermes home per matrix column."""
from __future__ import annotations

import atexit
import json
import os
import re
import secrets
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import matrix, safety, testcalls

DEAD_PROVIDER_URL = "http://127.0.0.1:9/v1"
DEAD_PROXY = "http://127.0.0.1:9"
SCRIPTED_MODEL = "provingground-scripted"
DRILL_PROMPT = "run provingground wiring test"
PG_PLUGIN_KEY = "proving-ground"
WARM_MARKER = ".provingground-warm"
KEEP_LOGS_ENV = "PROVINGGROUND_KEEP_LOGS"  # debug: copy child.log + events out before the root is removed
ENV_PASSTHROUGH = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "USER", "LOGNAME", "SHELL", "TERM")
IGNORED_NAMES = frozenset({".git", "__pycache__", ".venv", "node_modules", ".pytest_cache"})
ROOT_SUBDIRS = ("home", "tmp", "userhome", "sandbox", "xdg/config", "xdg/cache", "xdg/data", "xdg/state")
POLL_S = 0.2
KILL_GRACE_S = 3.0
VERSION_RE = re.compile(r"v(\d+\.\d+\.\d+).*?local\s+([0-9a-f]{7,40})")

CANCEL = threading.Event()  # set by SIGINT/SIGTERM/SIGHUP; checked before every spawn and in every poll loop
_LIVE_LOCK = threading.Lock()
_LIVE_PROCS: dict[int, subprocess.Popen] = {}
_LIVE_ROOTS: set[Path] = set()


class Cancelled(Exception):
    """The drill was cancelled by a signal; every live child group and root has been cleaned up."""


@dataclass(frozen=True)
class PluginSource:
    key: str
    path: Path
    settings: Mapping[str, Any] | None = None
    needs_env: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    plugins: tuple[PluginSource, ...]


@dataclass(frozen=True)
class RunnerSettings:
    hermes_bin: Path
    pg_source: Path
    warm_cache: Path
    temp_base: Path
    cell_timeout_s: float = 120.0
    jobs: int = 3


@dataclass
class CellRun:
    spec: ColumnSpec
    sandbox: str
    recorder_url: str
    plan_calls: list[dict[str, Any]]
    events: list[dict[str, Any]]
    exit_code: int | None
    abort_reason: str | None
    wall_s: float
    recorded: list[str] = field(default_factory=list)
    output_tail: str = ""
    copy_notes: tuple[str, ...] = ()


class Recorder:
    """127.0.0.1-only HTTP listener that records which drill calls reached execution, keyed per column token."""

    def __init__(self) -> None:
        self.entries: list[str] = []
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_class())
        self.port = int(self._server.server_address[1])
        self.url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                with recorder._lock:
                    recorder.entries.append(self.path.lstrip("/"))
                body = b"provingground recorder\n"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args: Any) -> None:
                return

        return Handler

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self.entries)

    def for_column(self, token: str) -> list[str]:
        """Only this column's recorded requests: entries are `<column-token>/<CALL_ID>` (ARCH-F8, PRD-F6)."""
        prefix = token + "/"
        return [e[len(prefix):] for e in self.snapshot() if e.startswith(prefix)]

    def __enter__(self) -> "Recorder":
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()

# File: proving_ground/runner.py  (part 2 of 5: child home, config, env, argv)
# [VERIFIED] config shape plugins.enabled + terminal.backend/cwd: pg-probe/mk_home.sh (P1, P5);
# approvals keys and values: hermes_cli/config_defaults.py:1657-1662 (mode, timeout, cron_mode, single_query_mode,
# unattended_mode); plugin settings live at plugins.entries.<key> (hermes_cli/plugins_state.py:60-63);
# argv: P1 command line (`chat -q 'run drill' -Q -t terminal --provider custom --base-url http://127.0.0.1:9/v1 -m pg-model`);
# comma toolsets accepted (hermes_cli/_parser.py:255); symlinked shared installs dir: P5.
# [VERIFIED 2026-11-11 forge run] HOME/XDG/TMPDIR redirection + dead proxies work once config carries a `model:` dict
# (hermes_cli/main.py _has_any_provider_configured: "model as a dict with provider/base_url/api_key means setup ran");
# without it the child stops at _first_run_setup_guard (main.py:1555, :1620). P1 passed only via host-wide fallbacks
# under the real HOME. [ASSUMED] a JSON flow mapping under plugins.entries parses (fixtures have no entries).


def render_child_config(plugin_keys: Sequence[str], entries: Mapping[str, Any], sandbox: Path) -> str:
    lines = ["plugins:", "  enabled:"]
    lines += [f"    - {json.dumps(key)}" for key in plugin_keys]
    if entries:
        lines.append(f"  entries: {json.dumps(entries, sort_keys=True)}")
    lines += ["model:", '  provider: "custom"', f"  base_url: {json.dumps(DEAD_PROVIDER_URL)}",
              f"  default: {json.dumps(SCRIPTED_MODEL)}"]
    lines += ["approvals:", '  mode: "manual"', "  timeout: 5", '  cron_mode: "deny"',
              '  single_query_mode: "deny"', '  unattended_mode: "deny"',
              "terminal:", '  backend: "local"', f"  cwd: {json.dumps(str(sandbox))}"]
    return "\n".join(lines) + "\n"


def approvals_summary() -> str:
    """Derived from the config PG actually writes into every child (ARCH-F15), not a hard-coded string."""
    text = render_child_config([], {}, Path("/nonexistent"))
    keys = ("mode", "single_query_mode", "cron_mode", "unattended_mode")
    found = dict(re.findall(r'^\s+(' + "|".join(keys) + r'): "([a-z]+)"$', text, re.M))
    return ", ".join(f"{k} {found.get(k, '?')}" for k in keys) + " (headless child: every approval is denied)"


def child_env(base: Mapping[str, str], root: Path, token: str, *, network: bool = False) -> dict[str, str]:
    env = {key: base[key] for key in ENV_PASSTHROUGH if key in base}
    env.update({
        "HOME": str(root / "userhome"), "HERMES_HOME": str(root / "home"), "TMPDIR": str(root / "tmp"),
        "XDG_CONFIG_HOME": str(root / "xdg/config"), "XDG_CACHE_HOME": str(root / "xdg/cache"),
        "XDG_DATA_HOME": str(root / "xdg/data"), "XDG_STATE_HOME": str(root / "xdg/state"),
        "TERMINAL_CWD": str(root / "sandbox"), safety.TOKEN_ENV: token, "PYTHONDONTWRITEBYTECODE": "1",
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
    })
    if not network:
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env[name] = DEAD_PROXY
    return env


def child_argv(hermes_bin: Path, n_calls: int) -> list[str]:
    return [str(hermes_bin), "chat", "-q", DRILL_PROMPT, "-Q", "-t", "terminal,file",
            "--provider", "custom", "--base-url", DEAD_PROVIDER_URL, "-m", SCRIPTED_MODEL,
            "--max-turns", str(n_calls + 2)]


def create_drill_root(temp_base: Path) -> Path:
    root = Path(tempfile.mkdtemp(prefix=safety.ROOT_PREFIX, dir=temp_base)).resolve()
    safety.write_owner_marker(root, os.getpid(), time.time())
    with _LIVE_LOCK:
        _LIVE_ROOTS.add(root)
    for sub in ROOT_SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def ensure_private_cache(cache: Path) -> None:
    """The shared warm cache must be ours: no symlink component, our uid, mode 0700 (ARCH-F10)."""
    for path in (cache.parent, cache):
        if path.is_symlink():
            raise safety.Refusal("SANDBOX_UNSUPPORTED", f"warm cache {path} is a symlink; remove it and rerun.")
        if not path.exists():
            path.mkdir(mode=0o700)
        info = path.lstat()
        if info.st_uid != os.getuid():
            raise safety.Refusal("SANDBOX_UNSUPPORTED", f"warm cache {path} belongs to another user; remove it.")
        if stat.S_IMODE(info.st_mode) & 0o077:
            os.chmod(path, 0o700)


def _copy_filter(src_root: Path, dropped: list[str]) -> Callable[[str, list[str]], set[str]]:
    """copytree ignore: drop build dirs, credential-shaped files and symlinks that leave the plugin dir (ARCH-F11)."""
    real_root = src_root.resolve()

    def ignore(directory: str, names: list[str]) -> set[str]:
        skip = set()
        for name in names:
            path = Path(directory) / name
            if name in IGNORED_NAMES or name.endswith((".pyc", ".pyo")):
                skip.add(name)
            elif safety.SECRET_NAME.search(name):
                skip.add(name)
                dropped.append(f"credential-shaped file {path.relative_to(src_root)}")
            elif path.is_symlink() and not path.resolve().is_relative_to(real_root):
                skip.add(name)
                dropped.append(f"symlink leaving the plugin dir {path.relative_to(src_root)}")
        return skip

    return ignore


def _copy_plugin(src: Path, dest: Path, dropped: list[str] | None = None) -> None:
    sink = dropped if dropped is not None else []
    shutil.copytree(src, dest, ignore=_copy_filter(src, sink), symlinks=True)


def redact_entries(settings: Mapping[str, Any], dropped: list[str], key: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, value in settings.items():
        if safety.SECRET_KEY.search(str(name)):
            dropped.append(f"{key}: setting {name} (secret-shaped) not copied")
        else:
            out[name] = redact_entries(value, dropped, key) if isinstance(value, Mapping) else value
    return out


def build_child_home(root: Path, spec: ColumnSpec, settings: RunnerSettings,
                     calls: Sequence[testcalls.DrillCall], token: str, dropped: list[str] | None = None) -> Path:
    home, notes = root / "home", dropped if dropped is not None else []
    for source in spec.plugins:
        _copy_plugin(source.path, home / "plugins" / source.key, notes)
    _copy_plugin(settings.pg_source, home / "plugins" / PG_PLUGIN_KEY)
    ensure_private_cache(settings.warm_cache)
    (home / "installs").symlink_to(settings.warm_cache, target_is_directory=True)
    keys = [s.key for s in spec.plugins] + [PG_PLUGIN_KEY]
    entries = {s.key: redact_entries(s.settings, notes, s.key) for s in spec.plugins if s.settings}
    (home / "config.yaml").write_text(render_child_config(keys, entries, root / "sandbox"), encoding="utf-8")
    state = home / safety.STATE_DIRNAME
    state.mkdir(parents=True, exist_ok=True)
    (state / safety.TOKEN_FILENAME).write_text(token, encoding="utf-8")
    plan = {"sandbox": str(root / "sandbox"), "subjects": [s.key for s in spec.plugins],
            "calls": [c.as_plan() for c in calls]}
    (state / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    return home


def seed_sandbox(sandbox: Path, calls: Sequence[testcalls.DrillCall]) -> None:
    for call in calls:
        for rel, text in call.seed.items():
            target = sandbox / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")

# File: proving_ground/runner.py  (part 3 of 5: spawn, watch, kill, cancel)
# [VERIFIED] env-match kill of the detached per-home daemon: pg-probe/cleanup.sh (`ps eww -p PID | grep HERMES_HOME=<home>`),
# P5 ("kill the daemon after each run"); daemon command contains hermes_cli (gateway_runtime_start.py:62-63).
# [VERIFIED by the §9.1 run] process-group timeout path and ESCAPED early kill (tests/test_cleanup.py, tests/test_escape_abort.py).


def _ps_candidates() -> list[int]:
    try:
        out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=15, check=False)
    except (subprocess.SubprocessError, OSError):
        return []
    pids: list[int] = []
    for line in out.stdout.splitlines():
        pid_text, _, command = line.strip().partition(" ")
        if pid_text.isdigit() and int(pid_text) != os.getpid() and ("hermes" in command or "python" in command):
            pids.append(int(pid_text))
    return pids


def _env_contains(pid: int, needle: str) -> bool:
    try:
        out = subprocess.run(["ps", "eww", "-p", str(pid)], capture_output=True, text=True, timeout=15, check=False)
    except (subprocess.SubprocessError, OSError):
        return False
    return needle in out.stdout


def _signal_all(pids: Sequence[int], sig: int) -> None:
    for pid in pids:
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            continue


def kill_home_processes(home: Path) -> list[int]:
    """Terminate every process whose environment names this exact child HERMES_HOME."""
    needle = f"HERMES_HOME={home}"
    victims = [pid for pid in _ps_candidates() if _env_contains(pid, needle)]
    _signal_all(victims, signal.SIGTERM)
    deadline = time.monotonic() + KILL_GRACE_S
    while time.monotonic() < deadline and any(safety.pid_alive(p) for p in victims):
        time.sleep(0.1)
    _signal_all([p for p in victims if safety.pid_alive(p)], signal.SIGKILL)
    return victims


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=KILL_GRACE_S)
    except (ProcessLookupError, PermissionError):
        return
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=KILL_GRACE_S)
        except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
            return


def cancel_everything(*_signal_args: Any) -> None:
    """Signal handler and atexit hook: stop queued work, kill every live child group and env-matched daemon."""
    CANCEL.set()
    with _LIVE_LOCK:
        procs, roots = list(_LIVE_PROCS.values()), list(_LIVE_ROOTS)
    for proc in procs:
        _kill_group(proc)
    for root in roots:
        kill_home_processes(root / "home")
        shutil.rmtree(root, ignore_errors=True)
        with _LIVE_LOCK:
            _LIVE_ROOTS.discard(root)


def install_cancellation() -> None:
    """Main thread only (CLI): SIGINT, SIGTERM and SIGHUP all cancel; atexit sweeps whatever is still registered."""
    if threading.current_thread() is not threading.main_thread():
        return

    def handler(signum: int, _frame: Any) -> None:
        cancel_everything()
        raise KeyboardInterrupt(f"signal {signum}")

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, handler)
    atexit.register(cancel_everything)


def _escaped_seen(events_path: Path) -> bool:
    try:
        return '"ev": "escaped"' in events_path.read_text(encoding="utf-8")
    except OSError:
        return False


def spawn_and_watch(root: Path, n_calls: int, settings: RunnerSettings, token: str,
                    *, network: bool = False) -> tuple[int | None, str | None]:
    if CANCEL.is_set():
        raise Cancelled("cancelled before spawn")
    events_path = root / "home" / safety.STATE_DIRNAME / "events.jsonl"
    with (root / "child.log").open("wb") as log:
        proc = subprocess.Popen(child_argv(settings.hermes_bin, n_calls),
                                env=child_env(os.environ, root, token, network=network),
                                cwd=root / "sandbox", stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)
        with _LIVE_LOCK:
            _LIVE_PROCS[proc.pid] = proc
        try:
            return _watch(proc, events_path, settings)
        finally:
            with _LIVE_LOCK:
                _LIVE_PROCS.pop(proc.pid, None)


def _watch(proc: subprocess.Popen, events_path: Path, settings: RunnerSettings) -> tuple[int | None, str | None]:
    deadline = time.monotonic() + settings.cell_timeout_s
    while proc.poll() is None:
        if CANCEL.is_set():
            _kill_group(proc)
            return proc.returncode, "CANCELLED"
        if _escaped_seen(events_path):
            _kill_group(proc)
            return proc.returncode, "ESCAPED"
        if time.monotonic() > deadline:
            _kill_group(proc)
            return proc.returncode, "CELL_TIMEOUT"
        time.sleep(POLL_S)
    return proc.returncode, None

# File: proving_ground/runner.py  (part 4 of 5: one column, events, sweep, warm-up)
# [VERIFIED by the §9.1 run] composition of the verified pieces above.


def read_events(home: Path) -> list[dict[str, Any]]:
    path = home / safety.STATE_DIRNAME / "events.jsonl"
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return events
    for line in lines:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return sorted(events, key=lambda e: float(e.get("t", 0.0)))


def abort_reason_from(events: Sequence[Mapping[str, Any]], watch_reason: str | None,
                      exit_code: int | None = 0, expect_done: bool = True) -> str | None:
    if watch_reason:
        return watch_reason
    if not any(e.get("ev") == "activated" for e in events):
        return "NOT_ACTIVATED"
    aborts = [str(e.get("reason")) for e in events if e.get("ev") == "abort"]
    if aborts:
        return aborts[0]
    if expect_done and not any(e.get("ev") == "done" for e in events):
        return f"INCOMPLETE (child exit {exit_code})" if exit_code else "INCOMPLETE"
    return None


def _tail(path: Path, limit: int = 4000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError:
        return ""


def _keep_logs(root: Path, spec: ColumnSpec, temp_base: Path) -> None:
    if not os.environ.get(KEEP_LOGS_ENV):
        return
    target = temp_base / "provingground-logs"
    target.mkdir(mode=0o700, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9_-]", "_", spec.name)
    for src, suffix in ((root / "child.log", "log"), (root / "home" / safety.STATE_DIRNAME / "events.jsonl", "events.jsonl")):
        if src.is_file():
            shutil.copyfile(src, target / f"{name}-{root.name}.{suffix}")


def _cleanup_root(root: Path, spec: ColumnSpec, temp_base: Path) -> None:
    try:
        _keep_logs(root, spec, temp_base)
        kill_home_processes(root / "home")
    finally:
        shutil.rmtree(root, ignore_errors=True)
        with _LIVE_LOCK:
            _LIVE_ROOTS.discard(root)


def run_column(spec: ColumnSpec, templates: Sequence[testcalls.CallTemplate], canary: str,
               recorder: Recorder, settings: RunnerSettings, *, network: bool = False) -> CellRun:
    if CANCEL.is_set():
        raise Cancelled("cancelled before the column started")
    root = create_drill_root(settings.temp_base)
    token, started, column_token = secrets.token_hex(32), time.monotonic(), secrets.token_hex(6)
    column_url, dropped = f"{recorder.url}/{column_token}", []
    try:
        ctx = testcalls.SandboxContext(root / "sandbox", column_url, canary)
        calls = testcalls.render(templates, ctx)
        seed_sandbox(root / "sandbox", calls)
        build_child_home(root, spec, settings, calls, token, dropped)
        exit_code, watch_reason = spawn_and_watch(root, len(calls), settings, token, network=network)
        events = read_events(root / "home")
        reason = abort_reason_from(events, watch_reason, exit_code, expect_done=bool(calls))
        return CellRun(spec, str(root / "sandbox"), column_url, [c.as_plan() for c in calls], events,
                       exit_code, reason, time.monotonic() - started, recorder.for_column(column_token),
                       _tail(root / "child.log"), tuple(dropped))
    finally:
        _cleanup_root(root, spec, settings.temp_base)


def sweep_stale_roots(temp_base: Path, max_age_s: float | None = None) -> list[Path]:
    """Remove only roots PG provably created (exact name + parseable marker) whose creator pid is dead."""
    removed: list[Path] = []
    for root in sorted(temp_base.glob(safety.ROOT_PREFIX + "*")):
        if root.is_symlink() or not root.is_dir() or not safety.ROOT_NAME.match(root.name):
            continue
        marker = safety.read_owner_marker(root)
        if marker is None:
            continue
        pid, created = marker
        too_old = max_age_s is not None and time.time() - created > max_age_s
        if pid == os.getpid() or (safety.pid_alive(pid) and not too_old):
            continue
        kill_home_processes(root.resolve() / "home")
        shutil.rmtree(root, ignore_errors=True)
        removed.append(root)
    return removed


def leftovers(temp_base: Path) -> tuple[int, int]:
    """(processes whose env names a PG root home, PG-marked roots on disk) right now; the report records both."""
    needle = f"HERMES_HOME={temp_base}/{safety.ROOT_PREFIX}"
    procs = [pid for pid in _ps_candidates() if _env_contains(pid, needle)]
    roots = [r for r in temp_base.glob(safety.ROOT_PREFIX + "*")
             if safety.ROOT_NAME.match(r.name) and safety.read_owner_marker(r) is not None]
    return len(procs), len(roots)


def run_column_reliably(spec: ColumnSpec, templates: Sequence[testcalls.CallTemplate], canary: str,
                        recorder: Recorder, settings: RunnerSettings) -> CellRun:
    """One retry for a column that reached CELL_TIMEOUT (observed once under parallel cold starts, DT-7)."""
    run = run_column(spec, templates, canary, recorder, settings)
    if run.abort_reason != "CELL_TIMEOUT" or CANCEL.is_set():
        return run
    return run_column(spec, templates, canary, recorder, settings)


def is_warm(settings: RunnerSettings) -> bool:
    return (settings.warm_cache / WARM_MARKER).exists()


WARM_DISCLOSURE = ("PROVING GROUND is preparing its isolated Hermes runtime once: Hermes's own installer downloads "
                   "about 650 MB from the network into {cache} (about 1 minute). No credentials are used and "
                   "approvals stay on deny. Later drills reuse it with all proxies pointed at a closed loopback port.")


def warm_up(settings: RunnerSettings) -> CellRun:
    """One zero-call child with network allowed so Hermes installs its runtime into the shared cache."""
    ensure_private_cache(settings.warm_cache)
    with Recorder() as recorder:
        run = run_column(ColumnSpec("warm-up", ()), [], testcalls.make_canary(), recorder, settings, network=True)
    if run.abort_reason in (None, "NOT_ACTIVATED") and run.exit_code == 0:
        (settings.warm_cache / WARM_MARKER).write_text(str(time.time()), encoding="utf-8")
    return run

# File: proving_ground/runner.py  (part 5 of 5: subjects, columns, the drill)
# [VERIFIED] enabled list at plugins.enabled and user plugins under <HERMES_HOME>/plugins/<key> (registry key = dir
# name, hermes_cli/plugins_manifest.py:378); load order alphabetical by key with requires_plugins first (P3,
# plugins_manifest.py:246-283). `hermes --version` prints "Hermes Agent v0.23.1 (2026.9.24) · stable · local 3b4215785882" (observed).
# [ASSUMED] pip/entry-point plugins have no directory and are reported as skipped (LIMITATIONS.md).


def _manifest(path: Path) -> dict[str, Any]:
    from ruamel.yaml import YAML

    try:
        return YAML(typ="safe", pure=True).load((path / "plugin.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _required_env(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    names = []
    for item in manifest.get("requires_env") or []:
        names.append(str(item.get("name")) if isinstance(item, Mapping) else str(item))
    return tuple(n for n in names if n)


def load_order(subjects: Sequence[PluginSource]) -> list[PluginSource]:
    """requires_plugins first (topological), alphabetical ties; mirrors plugins_manifest.py:246-283 (ARCH-F16)."""
    by_key, ordered, seen = {s.key: s for s in subjects}, [], set()

    def visit(source: PluginSource, stack: frozenset[str]) -> None:
        if source.key in seen or source.key in stack:
            return
        for dep in sorted(source.requires):
            if dep in by_key:
                visit(by_key[dep], stack | {source.key})
        seen.add(source.key)
        ordered.append(source)

    for source in sorted(subjects, key=lambda s: s.key):
        visit(source, frozenset())
    return ordered


def discover_subjects(hermes_home: Path, config: Mapping[str, Any]) -> tuple[list[PluginSource], list[tuple[str, str]]]:
    plugins_cfg = config.get("plugins") or {}
    entries = plugins_cfg.get("entries") or {}
    subjects, skipped = [], []
    for key in sorted(str(k) for k in plugins_cfg.get("enabled") or []):
        path = hermes_home / "plugins" / key
        if key == PG_PLUGIN_KEY:
            continue
        if not (path / "plugin.yaml").is_file():
            skipped.append((key, "not a directory plugin under your Hermes home"))
            continue
        settings = (entries.get(key) or {}) if isinstance(entries, Mapping) else {}
        manifest = _manifest(path)
        requires = tuple(str(r) for r in manifest.get("requires_plugins") or [])
        subjects.append(PluginSource(key, path, settings or None, _required_env(manifest), requires))
    return load_order(subjects), skipped


def fixture_subjects(pg_source: Path, without: Sequence[str] = ()) -> list[PluginSource]:
    keys = [k for k in ("fx_guard", "fx_rewriter", "fx_trimmer") if k not in set(without)]
    return [PluginSource(k, pg_source / "fixtures" / k) for k in keys]


def _solo_plugins(source: PluginSource, runnable: Sequence[PluginSource]) -> tuple[PluginSource, ...]:
    """A solo column carries the plugin plus its requires_plugins (labelled "solo + deps" in the report)."""
    deps = {s.key: s for s in runnable}
    return tuple(load_order([deps[d] for d in source.requires if d in deps] + [source]))


def plan_columns(subjects: Sequence[PluginSource]) -> list[ColumnSpec]:
    runnable = tuple(s for s in subjects if not s.needs_env)
    columns = [ColumnSpec("baseline", ())]
    columns += [ColumnSpec(f"solo:{s.key}", _solo_plugins(s, runnable)) for s in runnable]
    if len(runnable) > 1:
        columns.append(ColumnSpec("stack", tuple(load_order(runnable))))
    return columns


def hermes_version(hermes_bin: Path, settings: RunnerSettings | None = None) -> str:
    """`hermes --version`, run in a throwaway PG root (installs -> warm cache) so the real Hermes tree is not touched:
    even --version takes a lease on the runtime environment it resolves (revision-1 finding)."""
    if settings is None:
        out = subprocess.run([str(hermes_bin), "--version"], capture_output=True, text=True, timeout=60, check=False)
        return _parse_version(out.stdout)
    root = create_drill_root(settings.temp_base)
    try:
        ensure_private_cache(settings.warm_cache)
        (root / "home" / "installs").symlink_to(settings.warm_cache, target_is_directory=True)
        out = subprocess.run([str(hermes_bin), "--version"], capture_output=True, text=True, timeout=60, check=False,
                             env=child_env(os.environ, root, ""), cwd=root / "sandbox", stdin=subprocess.DEVNULL)
        return _parse_version(out.stdout)
    finally:
        _cleanup_root(root, ColumnSpec("version", ()), settings.temp_base)


def _parse_version(stdout: str) -> str:
    match = VERSION_RE.search(stdout)
    if match:
        return f"v{match.group(1)} @ {match.group(2)}"
    lines = [line for line in stdout.strip().splitlines() if line.startswith("Hermes")]
    return lines[0] if lines else "unknown"


def _to_column(run: CellRun) -> matrix.ColumnRun:
    return matrix.column_from_events(run.spec.name, tuple(s.key for s in run.spec.plugins), run.plan_calls,
                                     run.events, run.sandbox, run.recorder_url, run.recorded,
                                     run.abort_reason, run.wall_s)


def _parallel(specs: Sequence[ColumnSpec], work: Callable[[ColumnSpec], CellRun], jobs: int,
              on_done: Callable[[CellRun], None] | None = None) -> list[CellRun]:
    """Run columns concurrently; report each as it finishes; return results in spec order.
    Any exception or signal cancels queued columns (cancel_futures) and kills in-flight children (ARCH-F2)."""
    pool = ThreadPoolExecutor(max_workers=max(1, jobs))
    results: list[CellRun | None] = [None] * len(specs)
    try:
        futures = {pool.submit(work, spec): index for index, spec in enumerate(specs)}
        for future in as_completed(futures):
            results[futures[future]] = future.result()
            if on_done is not None:
                on_done(future.result())
    except BaseException:
        cancel_everything()
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    return [r for r in results if r is not None]


@dataclass
class DrillRun:
    columns: list[matrix.ColumnRun]
    baseline_repeat: matrix.ColumnRun | None
    crosscheck: dict[str, matrix.ColumnRun]
    skipped: list[tuple[str, str]]
    needs_env: list[str]
    canary: str
    wall_s: float
    load_order: tuple[str, ...] = ()
    unreplayable: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    copy_notes: tuple[str, ...] = ()


def run_drill(subjects: Sequence[PluginSource], templates: Sequence[testcalls.CallTemplate],
              settings: RunnerSettings, on_column: Callable[[matrix.ColumnRun], None] | None = None,
              skipped: Sequence[tuple[str, str]] = ()) -> DrillRun:
    CANCEL.clear()  # a new drill starts uncancelled; a signal during it sets CANCEL again
    started, canary, notify = time.monotonic(), testcalls.make_canary(), on_column or (lambda _c: None)
    subjects = load_order(subjects)
    columns, order = plan_columns(subjects), tuple(s.key for s in subjects if not s.needs_env)
    notes: list[str] = []
    with Recorder() as recorder:
        def work(spec: ColumnSpec) -> CellRun:
            run = run_column_reliably(spec, templates, canary, recorder, settings)
            notes.extend(run.copy_notes)
            return run

        baseline = _to_column(work(columns[0]))
        notify(baseline)
        if baseline.abort_reason:  # a broken harness must not run every other column against a non-scripted child
            return DrillRun([baseline], None, {}, list(skipped), [s.key for s in subjects if s.needs_env], canary,
                            time.monotonic() - started, order)
        repeat_and_rest = [ColumnSpec("baseline-repeat", ())] + columns[1:]

        def done(run: CellRun) -> None:
            if run.spec.name != "baseline-repeat":
                notify(_to_column(run))

        results = [_to_column(r) for r in _parallel(repeat_and_rest, work, settings.jobs, done)]
        crosscheck, refused = _run_crosscheck(results, subjects, canary, recorder, settings)
    return DrillRun([baseline] + results[1:], results[0], crosscheck, list(skipped),
                    [s.key for s in subjects if s.needs_env], canary, time.monotonic() - started, order, refused,
                    tuple(sorted(set(notes))))


def _run_crosscheck(results: Sequence[matrix.ColumnRun], subjects: Sequence[PluginSource], canary: str,
                    recorder: Recorder, settings: RunnerSettings
                    ) -> tuple[dict[str, matrix.ColumnRun], list[tuple[str, tuple[str, ...]]]]:
    stack = next((c for c in results if c.name == "stack"), None)
    rewrites = matrix.rewrites_for_crosscheck(stack) if stack else []
    if not rewrites:
        return {}, []
    probe = testcalls.SandboxContext(settings.temp_base / "provingground-grammar-probe" / "sandbox",
                                     f"{recorder.url}/grammar", canary)
    templates, refused = testcalls.crosscheck_templates(rewrites, probe)
    if not templates:
        return {}, refused
    specs = [ColumnSpec(f"crosscheck:{s.key}", (s,)) for s in subjects if not s.needs_env]
    runs = _parallel(specs, lambda spec: run_column_reliably(spec, templates, canary, recorder, settings), settings.jobs)
    return {run.spec.plugins[0].key: _to_column(run) for run in runs}, refused
