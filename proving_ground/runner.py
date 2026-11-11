# File: proving_ground/runner.py  (part 1 of 5: types, cancellation, recorder)
# stdlib only. Recorder binds 127.0.0.1 (never all interfaces; scanner bind_all_interfaces is high,
# loopback hardcoded_ip_port is a low finding).
"""Child-drill runner: one throwaway Hermes home per matrix column."""
from __future__ import annotations

import atexit
import itertools
import json
import os
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import injector, matrix, safety, testcalls

DEAD_PROVIDER_URL = "http://127.0.0.1:9/v1"
DEAD_PROXY = "http://127.0.0.1:9"
DRILL_PROMPT = "run provingground wiring test"
PG_PLUGIN_KEY = "proving-ground"  # the catalog name and the key a user's own Hermes home installs it under
CHILD_PG_KEY = "zz-proving-ground"  # the copy inside each child; see child_pg_key
WARM_MARKER = ".provingground-warm"
KEEP_LOGS_ENV = "PROVINGGROUND_KEEP_LOGS"  # debug: copy child.log + events out before the root is removed
ENV_PASSTHROUGH = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "USER", "LOGNAME", "SHELL", "TERM")
IGNORED_NAMES = frozenset({".git", "__pycache__", ".venv", "node_modules", ".pytest_cache"})
ROOT_SUBDIRS = ("home", "tmp", "userhome", "sandbox", "xdg/config", "xdg/cache", "xdg/data", "xdg/state")
POLL_S = 0.2
KILL_GRACE_S = 3.0
# Bounded reads. A column whose child.log or events.jsonl passes LOG_LIMIT_BYTES is stopped (LOG_LIMIT);
# events are read up to EVENTS_MAX_BYTES, the output tail from the end, the escape scan only over new bytes.
LOG_LIMIT_BYTES = 32 * 1024 * 1024
EVENTS_MAX_BYTES = 16 * 1024 * 1024
SCAN_CHUNK_BYTES = 1024 * 1024
ESCAPED_NEEDLE = b'"ev": "escaped"'
VERSION_RE = re.compile(r"v(\d+\.\d+\.\d+).*?local\s+([0-9a-f]{7,40})")

CANCEL = threading.Event()  # set by SIGINT/SIGTERM/SIGHUP; checked before every spawn and in every poll loop
_LIVE_LOCK = threading.RLock()  # re-entrant: the signal handler runs cancel_everything in the main thread
_LIVE_PROCS: dict[int, subprocess.Popen] = {}
_LIVE_ROOTS: set[Path] = set()
_CLEANED: set[int] = set()  # pids cancel_everything stopped (groups and env-matched daemons), for the cancel message
WARM_TIMEOUT_S = 900.0  # the one-off runtime install (~650 MB) needs far longer than a drill column


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
    jobs: int = 5
    wires: bool = False  # --all-wires: children also record the context and reply wire
    pairs: bool = False  # --pairs: pair columns for effects seen only in the full stack
    repeat: int = 1  # --repeat N: solo columns with a plugin effect run N times in all


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
    wires: Mapping[str, Any] | None = None  # the plan's context-and-reply spec when the drill ran with --all-wires


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
        """Only this column's recorded requests: entries are `<column-token>/<CALL_ID>`."""
        prefix = token + "/"
        return [e[len(prefix):] for e in self.snapshot() if e.startswith(prefix)]

    def __enter__(self) -> "Recorder":
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()

# File: proving_ground/runner.py  (part 2 of 5: child home, config, env, argv)
# config shape plugins.enabled + terminal.backend/cwd: observed in probe child homes;
# approvals keys and values: hermes_cli/config_defaults.py:1657-1662 (mode, timeout, cron_mode, single_query_mode,
# unattended_mode); plugin settings live at plugins.entries.<key> (hermes_cli/plugins_state.py:60-63);
# argv: the probe's command line (`chat -q 'run drill' -Q -t terminal --provider custom --base-url http://127.0.0.1:9/v1 -m pg-model`);
# comma toolsets accepted (hermes_cli/_parser.py:255); a symlinked shared installs dir works (probe run).
# Observed 2026-11-11: HOME/XDG/TMPDIR redirection + dead proxies work once config carries a `model:` dict
# (hermes_cli/main.py _has_any_provider_configured: "model as a dict with provider/base_url/api_key means setup ran");
# without it the child stops at _first_run_setup_guard (main.py:1555, :1620); under the real HOME a child passes only via
# host-wide fallbacks. Assumption: a JSON flow mapping under plugins.entries parses (fixtures have no entries).


def render_child_config(plugin_keys: Sequence[str], entries: Mapping[str, Any], sandbox: Path) -> str:
    lines = ["plugins:", "  enabled:"]
    lines += [f"    - {json.dumps(key)}" for key in plugin_keys]
    if entries:
        lines.append(f"  entries: {json.dumps(entries, sort_keys=True)}")
    lines += ["model:", '  provider: "custom"', f"  base_url: {json.dumps(DEAD_PROVIDER_URL)}",
              f"  default: {json.dumps(safety.SCRIPTED_MODEL)}"]
    lines += ["approvals:", '  mode: "manual"', "  timeout: 5", '  cron_mode: "deny"',
              '  single_query_mode: "deny"', '  unattended_mode: "deny"',
              "terminal:", '  backend: "local"', f"  cwd: {json.dumps(str(sandbox))}"]
    return "\n".join(lines) + "\n"


def approvals_summary() -> str:
    """Derived from the config PG actually writes into every child, not a hard-coded string."""
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


def wire_probe_text() -> dict[str, str]:
    """testcalls.yaml's wire_probe: synthetic text the scripted turn carries so reply and user-turn plugins act."""
    return testcalls.load_wire_probe()


def drill_prompt(probe: Mapping[str, str]) -> str:
    """The child's user turn: the drill instruction, then the user-turn probe text."""
    return " ".join(filter(None, (DRILL_PROMPT, probe.get("user_turn"))))


def final_reply(probe: Mapping[str, str]) -> str:
    """The scripted final reply: the done sentinel first (the drill recognizes it), then the reply probe text."""
    return " ".join(filter(None, (injector.DONE_SENTINEL, probe.get("reply"))))


VERIFY_NUDGE_ROUNDS = 3  # Hermes stops asking pre_verify after three continue nudges (agent/turn_stop_gates.py:53-76)


def max_turns(n_calls: int) -> int:
    """One model step per call, the final reply and a spare, plus one step for each pre_verify nudge Hermes allows."""
    return n_calls + 2 + VERIFY_NUDGE_ROUNDS


def child_argv(launch: Path | Sequence[str], n_calls: int, prompt: str = DRILL_PROMPT) -> list[str]:
    """`launch` is the hermes executable or the resolved interpreter prefix (launch_prefix)."""
    prefix = [str(launch)] if isinstance(launch, (str, Path)) else [str(a) for a in launch]
    return [*prefix, "chat", "-q", prompt, "-Q", "-t", safety.CHILD_TOOLSETS,
            "--provider", "custom", "--base-url", DEAD_PROVIDER_URL, "-m", safety.SCRIPTED_MODEL,
            "--max-turns", str(max_turns(n_calls))]


def create_drill_root(temp_base: Path) -> Path:
    """A new marked root; if marking or populating it fails, it is removed (an unmarked root is never swept)."""
    root = Path(tempfile.mkdtemp(prefix=safety.ROOT_PREFIX, dir=temp_base)).resolve()
    try:
        safety.write_owner_marker(root, os.getpid(), time.time())
        with _LIVE_LOCK:
            _LIVE_ROOTS.add(root)
        for sub in ROOT_SUBDIRS:
            (root / sub).mkdir(parents=True, exist_ok=True)
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        with _LIVE_LOCK:
            _LIVE_ROOTS.discard(root)
        raise
    return root


def ensure_private_dir(path: Path, what: str) -> None:
    """*path* must be ours: not a symlink, our uid, mode 0700 (created 0700 when missing)."""
    if path.is_symlink():
        raise safety.Refusal("SANDBOX_UNSUPPORTED", f"{what} {path} is a symlink; remove it and rerun.")
    if not path.exists():
        path.mkdir(mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise safety.Refusal("SANDBOX_UNSUPPORTED", f"{what} {path} is not a directory; remove it and rerun.")
    if info.st_uid != os.getuid():
        raise safety.Refusal("SANDBOX_UNSUPPORTED", f"{what} {path} belongs to another user; remove it.")
    if stat.S_IMODE(info.st_mode) & 0o077:
        os.chmod(path, 0o700)


def ensure_private_cache(cache: Path) -> None:
    """The shared warm cache must be ours: no symlink component, our uid, mode 0700."""
    for path in (cache.parent, cache):
        ensure_private_dir(path, "warm cache")


def _copy_filter(src_root: Path, dropped: list[str]) -> Callable[[str, list[str]], set[str]]:
    """copytree ignore: drop build dirs, credential-shaped files and symlinks that leave the plugin dir."""
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


TOKEN_VALUE = re.compile(r"^(sk|ghp|gho|github_pat|xox[abp]|AKIA)[-_A-Za-z0-9]{16,}$")


def _redact_value(value: Any, dropped: list[str], key: str, name: str) -> Any:
    """Nested mappings and lists are redacted too; a token-shaped string under any name is withheld."""
    if isinstance(value, Mapping):
        return redact_entries(value, dropped, key)
    if isinstance(value, (list, tuple)):
        return [_redact_value(item, dropped, key, name) for item in value]
    if isinstance(value, str) and TOKEN_VALUE.match(value):
        dropped.append(f"{key}: a token-shaped value under {name} not copied")
        return "[withheld by PROVING GROUND]"
    return value


def redact_entries(settings: Mapping[str, Any], dropped: list[str], key: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, value in settings.items():
        if safety.SECRET_KEY.search(str(name)):
            dropped.append(f"{key}: setting {name} (secret-shaped) not copied")
        else:
            out[name] = _redact_value(value, dropped, key, str(name))
    return out


def child_pg_key(subject_keys: Sequence[str]) -> str:
    """The registry key of PROVING GROUND's copy in a child: one that sorts after every subject key. A flat
    directory plugin's key is its manifest name (hermes_cli/plugins_manifest.py:562-563, manifest_key :355-357), so
    the copy's plugin.yaml is renamed to it (and its directory too); the catalog name stays proving-ground. Hermes loads each dependency layer alphabetically by key
    (plugins_manifest.py:237-283) and execution middleware runs in registration order with index 0 outermost
    (hermes_cli/middleware.py:162-214), so loading last makes PROVING GROUND the innermost llm_execution and
    tool_execution frame: every subject middleware wraps it and runs. Subjects with requires_plugins load in a later
    layer, after it whatever their name; the child reports where each one loaded."""
    key = CHILD_PG_KEY
    while any(k >= key for k in subject_keys):
        key = "z" + key
    return key


MANIFEST_NAME = re.compile(r"^name: *\S+ *$", re.M)


def rename_manifest(path: Path, key: str) -> None:
    """Point the copied manifest's top-level `name:` at *key*; exactly one such line, or the copy is refused."""
    text = path.read_text(encoding="utf-8")
    renamed, count = MANIFEST_NAME.subn(f"name: {key}", text)
    if count != 1:
        raise ValueError(f"{path}: expected one top-level name line, found {count}")
    path.write_text(renamed, encoding="utf-8")


def build_child_home(root: Path, spec: ColumnSpec, settings: RunnerSettings,
                     calls: Sequence[testcalls.DrillCall], token: str, dropped: list[str] | None = None) -> Path:
    home, notes = root / "home", dropped if dropped is not None else []
    for source in spec.plugins:
        _copy_plugin(source.path, home / "plugins" / source.key, notes)
    pg_key = child_pg_key([s.key for s in spec.plugins])
    _copy_plugin(settings.pg_source, home / "plugins" / pg_key)
    rename_manifest(home / "plugins" / pg_key / "plugin.yaml", pg_key)
    ensure_private_cache(settings.warm_cache)
    (home / "installs").symlink_to(settings.warm_cache, target_is_directory=True)
    keys = [s.key for s in spec.plugins] + [pg_key]
    entries = {s.key: redact_entries(s.settings, notes, s.key) for s in spec.plugins if s.settings}
    (home / "config.yaml").write_text(render_child_config(keys, entries, root / "sandbox"), encoding="utf-8")
    state = home / safety.STATE_DIRNAME
    state.mkdir(parents=True, exist_ok=True)
    (state / safety.TOKEN_FILENAME).write_text(token, encoding="utf-8")
    plan: dict[str, Any] = {"sandbox": str(root / "sandbox"), "subjects": [s.key for s in spec.plugins], "pg_key": pg_key,
                            "calls": [c.as_plan() for c in calls], "final_reply": final_reply(wire_probe_text())}
    if settings.wires:
        plan["wires"] = wire_spec(settings)
    (state / safety.PLAN_FILENAME).write_text(json.dumps(plan), encoding="utf-8")
    return home


def wire_spec(settings: RunnerSettings) -> dict[str, str] | None:
    """What the scripted turn sends and replies, for the context and reply wire; None without --all-wires."""
    probe = wire_probe_text()
    return {"prompt": drill_prompt(probe), "reply": final_reply(probe)} if settings.wires else None


def seed_sandbox(sandbox: Path, calls: Sequence[testcalls.DrillCall]) -> None:
    for call in calls:
        for rel, text in call.seed.items():
            target = sandbox / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")

# File: proving_ground/runner.py  (part 3 of 5: spawn, watch, kill, cancel)
# env-match kill of the detached per-home daemon: the process environment matched on HERMES_HOME=<home>
# (a probe run showed the daemon must be killed after each run); daemon command contains hermes_cli (gateway_runtime_start.py:62-63).
# One BSD-style `ps axeww` scan lists every process with its environment appended (the dash form `-axeww` prints no
# environment on macOS); each cleanup reads it once instead of one `ps eww -p PID` per candidate.
# process-group timeout path and ESCAPED early kill (tests/test_cleanup.py, tests/test_escape_abort.py).
PS_ENV_ARGV = ("ps", "axeww", "-o", "pid=,command=")


def _ps_env_table() -> dict[int, str]:
    """pid -> command line plus environment, for every hermes or python process except this one; {} when ps fails."""
    try:
        out = subprocess.run(list(PS_ENV_ARGV), capture_output=True, text=True, timeout=15, check=False)
    except (subprocess.SubprocessError, OSError):
        return {}
    table: dict[int, str] = {}
    for line in out.stdout.splitlines():
        pid_text, _, text = line.strip().partition(" ")
        if pid_text.isdigit() and int(pid_text) != os.getpid() and ("hermes" in text or "python" in text):
            table[int(pid_text)] = text
    return table


def names_home(text: str, home: Path | str) -> bool:
    """True when this process text carries exactly HERMES_HOME=<home> as one environment word."""
    return re.search(r"(?:^|\s)HERMES_HOME=" + re.escape(str(home)) + r"(?=\s|$)", text) is not None


def _signal_all(pids: Sequence[int], sig: int) -> None:
    for pid in pids:
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            continue


def kill_home_processes(home: Path) -> list[int]:
    """Terminate every process whose environment names this exact child HERMES_HOME."""
    victims = [pid for pid, text in _ps_env_table().items() if names_home(text, home)]
    _terminate(victims)
    return victims


def _terminate(pids: Sequence[int]) -> None:
    """SIGTERM, up to KILL_GRACE_S for them to exit, then SIGKILL for any still alive."""
    _signal_all(pids, signal.SIGTERM)
    deadline = time.monotonic() + KILL_GRACE_S
    while time.monotonic() < deadline and any(safety.pid_alive(p) for p in pids):
        time.sleep(0.1)
    _signal_all([p for p in pids if safety.pid_alive(p)], signal.SIGKILL)


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


def cancel_everything(*_signal_args: Any) -> int:
    """Signal handler and atexit hook: stop queued work, kill every live child group and env-matched daemon.
    Returns how many distinct processes cancellation has stopped so far in this process."""
    CANCEL.set()
    with _LIVE_LOCK:
        procs, roots = list(_LIVE_PROCS.values()), list(_LIVE_ROOTS)
    for proc in procs:
        _kill_group(proc)
        _CLEANED.add(proc.pid)
    for root in roots:
        _CLEANED.update(kill_home_processes(root / "home"))
        shutil.rmtree(root, ignore_errors=True)
        with _LIVE_LOCK:
            _LIVE_ROOTS.discard(root)
    return len(_CLEANED)


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


class EscapeScan:
    """Incremental scan of events.jsonl for the escaped event: each poll reads only the bytes added since the last
    one (at most SCAN_CHUNK_BYTES), keeping a short carry so a needle split across reads is still found."""

    def __init__(self, path: Path) -> None:
        self.path, self.offset, self._carry = path, 0, b""

    def seen(self) -> bool:
        try:
            with self.path.open("rb") as handle:
                handle.seek(self.offset)
                chunk = handle.read(SCAN_CHUNK_BYTES)
        except OSError:
            return False
        self.offset += len(chunk)
        data = self._carry + chunk
        self._carry = data[-(len(ESCAPED_NEEDLE) - 1):]
        return ESCAPED_NEEDLE in data


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def spawn_and_watch(root: Path, n_calls: int, settings: RunnerSettings, token: str,
                    *, network: bool = False) -> tuple[int | None, str | None]:
    if CANCEL.is_set():
        raise Cancelled("cancelled before spawn")
    events_path = root / "home" / safety.STATE_DIRNAME / safety.EVENTS_FILENAME
    with (root / "child.log").open("wb") as log:
        proc = subprocess.Popen(child_argv(launch_prefix(settings), n_calls, drill_prompt(wire_probe_text())),
                                env=child_env(os.environ, root, token, network=network),
                                cwd=root / "sandbox", stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)
        with _LIVE_LOCK:
            _LIVE_PROCS[proc.pid] = proc
        try:
            return _watch(proc, events_path, settings, root / "child.log")
        finally:
            with _LIVE_LOCK:
                _LIVE_PROCS.pop(proc.pid, None)


def _watch_reason(scan: EscapeScan, log_path: Path | None) -> str | None:
    if scan.seen():
        return "ESCAPED"
    if max(_size(scan.path), _size(log_path) if log_path else 0) > LOG_LIMIT_BYTES:
        return "LOG_LIMIT"
    return None


def _watch(proc: subprocess.Popen, events_path: Path, settings: RunnerSettings,
           log_path: Path | None = None) -> tuple[int | None, str | None]:
    deadline, scan = time.monotonic() + settings.cell_timeout_s, EscapeScan(events_path)
    while proc.poll() is None:
        if CANCEL.is_set():
            _kill_group(proc)
            return proc.returncode, "CANCELLED"
        reason = _watch_reason(scan, log_path)
        if reason:
            _kill_group(proc)
            return proc.returncode, reason
        if time.monotonic() > deadline:
            _kill_group(proc)
            return proc.returncode, "CELL_TIMEOUT"
        time.sleep(POLL_S)
    return proc.returncode, None

# File: proving_ground/runner.py  (part 4 of 5: one column, events, sweep, warm-up)
# composition of the verified pieces above.


def _event_from(line: str) -> dict[str, Any] | None:
    """One events.jsonl line, kept only when it is an object with a numeric `t` and a string `ev`: a torn append
    or a line written by another plugin must not stop the drill."""
    try:
        event = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(event, dict) or not isinstance(event.get("ev"), str):
        return None
    t = event.get("t", 0.0)
    return event if isinstance(t, (int, float)) and not isinstance(t, bool) else None


def read_events(home: Path, max_bytes: int = EVENTS_MAX_BYTES) -> list[dict[str, Any]]:
    """At most *max_bytes* of events.jsonl; a longer log adds a LOG_LIMIT abort instead of loading it all."""
    path = home / safety.STATE_DIRNAME / safety.EVENTS_FILENAME
    events: list[dict[str, Any]] = []
    try:
        with path.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
    except OSError:
        return events
    clipped = len(raw) > max_bytes
    lines = raw[:max_bytes].decode("utf-8", errors="replace").splitlines()
    for line in lines[:-1] if clipped else lines:
        event = _event_from(line)
        if event is not None:
            events.append(event)
    if clipped:
        events.append({"t": float("inf"), "ev": "abort", "reason": "LOG_LIMIT"})
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
    """The last *limit* characters, reading at most 4 * limit bytes from the end."""
    try:
        with path.open("rb") as handle:
            handle.seek(max(0, _size(path) - 4 * limit))
            return handle.read().decode("utf-8", errors="replace")[-limit:]
    except OSError:
        return ""


def _copy_private(src: Path, dst: Path) -> None:
    """Copy into a new 0600 file; never follows or overwrites an existing name (O_EXCL | O_NOFOLLOW)."""
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as out, src.open("rb") as inp:
        shutil.copyfileobj(inp, out)


def _keep_logs(root: Path, spec: ColumnSpec, temp_base: Path) -> None:
    """PROVINGGROUND_KEEP_LOGS: the logs dir gets the warm cache's checks (no symlink, our uid, 0700)."""
    if not os.environ.get(KEEP_LOGS_ENV):
        return
    target = temp_base / "provingground-logs"
    name = re.sub(r"[^A-Za-z0-9_-]", "_", spec.name)
    try:
        ensure_private_dir(target, "logs dir")
        for src, suffix in ((root / "child.log", "log"),
                            (root / "home" / safety.STATE_DIRNAME / safety.EVENTS_FILENAME, safety.EVENTS_FILENAME)):
            if src.is_file():
                _copy_private(src, target / f"{name}-{root.name}.{suffix}")
    except (safety.Refusal, OSError) as exc:
        print(f"{KEEP_LOGS_ENV}: logs not kept: {exc}", file=sys.stderr)


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
    """One column. An unexpected error inside it (an unreadable plugin file, a bad call) becomes that column's
    COLUMN_ERROR abort, reported like any stopped column, instead of cancelling the sibling columns."""
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
                       _tail(root / "child.log"), tuple(dropped), wire_spec(settings))
    except (Cancelled, safety.Refusal):
        raise
    except Exception as exc:
        unrun = [{"id": t.id, "call_ref": testcalls.call_ref_for(t.id), "tool": t.tool, "args": {}} for t in templates]
        return CellRun(spec, str(root / "sandbox"), column_url, unrun, [], None, f"COLUMN_ERROR:{type(exc).__name__}",
                       time.monotonic() - started, output_tail=str(exc)[:600], copy_notes=tuple(dropped),
                       wires=wire_spec(settings))
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


def drill_home_in(text: str, temp_base: Path) -> Path | None:
    """The PG drill HERMES_HOME named in this process text (command line plus environment), or None."""
    match = re.search(r"(?:^|\s)HERMES_HOME=(" + re.escape(f"{temp_base}/{safety.ROOT_PREFIX}")
                      + r"[a-z0-9_]{8}/home)(?=\s|$)", text)
    return Path(match.group(1)) if match else None


def _drill_homes(temp_base: Path) -> dict[int, Path]:
    """pid -> drill HERMES_HOME for every running drill process under temp_base, from one ps scan."""
    return {pid: home for pid, text in _ps_env_table().items() if (home := drill_home_in(text, temp_base)) is not None}


def reap_orphans(temp_base: Path) -> list[int]:
    """Stop drill processes whose root is already removed: a gateway daemon a killed child had just asked launchd
    for can start after that column's cleanup scan. Its root is gone, so nothing else will ever stop it."""
    orphans = [pid for pid, home in _drill_homes(temp_base).items() if not home.parent.exists()]
    _terminate(orphans)
    return orphans


def _ours(root: Path) -> bool:
    """A root this process created (its owner marker names our pid)."""
    marker = safety.read_owner_marker(root)
    return marker is not None and marker[0] == os.getpid()


def leftovers(temp_base: Path) -> tuple[int, int]:
    """(this drill's processes still running, this drill's PG-marked roots still on disk) right now; the report
    records both. Another drill running at the same time in the same temp dir is not counted as ours."""
    procs = [pid for pid, home in _drill_homes(temp_base).items() if not home.parent.exists() or _ours(home.parent)]
    roots = [r for r in temp_base.glob(safety.ROOT_PREFIX + "*") if safety.ROOT_NAME.match(r.name) and _ours(r)]
    return len(procs), len(roots)


def run_column_reliably(spec: ColumnSpec, templates: Sequence[testcalls.CallTemplate], canary: str,
                        recorder: Recorder, settings: RunnerSettings) -> CellRun:
    """One retry for a column that reached CELL_TIMEOUT (observed once under parallel cold starts)."""
    run = run_column(spec, templates, canary, recorder, settings)
    if run.abort_reason != "CELL_TIMEOUT" or CANCEL.is_set():
        return run
    return run_column(spec, templates, canary, recorder, settings)


def is_warm(settings: RunnerSettings) -> bool:
    return (settings.warm_cache / WARM_MARKER).exists()


WARM_DISCLOSURE = ("PROVING GROUND is preparing its isolated Hermes runtime once: Hermes's own installer downloads "
                   "about 650 MB from the network into {cache} (about 1 minute). No credentials are used and "
                   "approvals stay on deny. Later drills reuse it with all proxies pointed at a closed loopback port.")


WARM_NOTICE = ("warm-up: Hermes's own installer is fetching its runtime (~650 MB, network on, no credentials, "
               "approvals deny) into {cache}; up to 15 minutes, once per temp dir.")


def warm_up(settings: RunnerSettings) -> CellRun:
    """One zero-call child with network allowed so Hermes installs its runtime into the shared cache.
    It gets its own long timeout (WARM_TIMEOUT_S): the first install is far slower than a drill column."""
    ensure_private_cache(settings.warm_cache)
    slow = replace(settings, cell_timeout_s=max(settings.cell_timeout_s, WARM_TIMEOUT_S))
    with Recorder() as recorder:
        run = run_column(ColumnSpec("warm-up", ()), [], testcalls.make_canary(), recorder, slow, network=True)
    if run.abort_reason in (None, "NOT_ACTIVATED") and run.exit_code == 0:
        (settings.warm_cache / WARM_MARKER).write_text(str(time.time()), encoding="utf-8")
    return run


def warm_failure(run: CellRun) -> safety.Refusal:
    """The clean refusal for a warm-up that did not leave the cache warm."""
    why = run.abort_reason or f"child exit {run.exit_code}"
    tail = " ".join(run.output_tail.split())[-300:]
    return safety.Refusal("WARMUP_FAILED", f"Hermes's runtime install did not finish ({why}). Check the network and "
                          f"rerun `hermes provingground warm`." + (f" Last output: {tail}" if tail else ""))

# File: proving_ground/runner.py  (part 5 of 5: subjects, columns, the drill)
# enabled list at plugins.enabled and user plugins under <HERMES_HOME>/plugins/<key> (a flat plugin's registry key is
# its manifest name, hermes_cli/plugins_manifest.py:562-563; a subject whose name differs from its directory is not
# confirmed loaded and shows LOAD FAILED); load order alphabetical by key with requires_plugins first
# (plugins_manifest.py:246-283). `hermes --version` prints "Hermes Agent v0.23.1 (2026.9.24) · stable · local 3b4215785882" (observed).
# Assumption: pip/entry-point plugins have no directory and are reported as skipped (LIMITATIONS.md).


def _manifest(path: Path) -> Mapping[str, Any]:
    """A plugin's manifest is third-party data: anything but a mapping reads as an empty manifest."""
    from ruamel.yaml import YAML

    try:
        data = YAML(typ="safe", pure=True).load((path / "plugin.yaml").read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, Mapping) else {}


def _listed(manifest: Mapping[str, Any], key: str) -> list[Any]:
    value = manifest.get(key)
    return value if isinstance(value, list) else []


def _required_env(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    """requires_env items are names or {name: ...}; anything else (a bare string, a nameless dict) is ignored."""
    names = [item.get("name") if isinstance(item, Mapping) else item for item in _listed(manifest, "requires_env")]
    return tuple(n for n in names if isinstance(n, str) and n)


def load_order(subjects: Sequence[PluginSource]) -> list[PluginSource]:
    """Mirror of plugins_manifest.resolve_plugin_load_order (plugins_manifest.py:237-283): graphlib layers, each
    layer alphabetical, so a plugin's requires_plugins load first; a cycle falls back to alphabetical for all.
    The child reports the order it really used and a mismatch becomes a report note."""
    import graphlib

    by_key = {s.key: s for s in subjects}
    graph = {k: {d for d in by_key[k].requires if d in by_key and d != k} for k in sorted(by_key)}
    sorter = graphlib.TopologicalSorter(graph)
    try:
        sorter.prepare()
    except graphlib.CycleError:
        return [by_key[k] for k in sorted(by_key)]
    ordered: list[str] = []
    while sorter.is_active():
        ready = sorted(sorter.get_ready())
        ordered.extend(ready)
        sorter.done(*ready)
    return [by_key[k] for k in ordered]


def _requires(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    """requires_plugins items are ids or {id, version_range} (plugins_manifest.py:123-130)."""
    ids = [item.get("id") if isinstance(item, Mapping) else item for item in _listed(manifest, "requires_plugins")]
    return tuple(str(i) for i in ids if isinstance(i, str) and i)


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
        settings = entries.get(key) if isinstance(entries, Mapping) else None
        settings = settings if isinstance(settings, Mapping) else None
        manifest = _manifest(path)
        requires = _requires(manifest)
        subjects.append(PluginSource(key, path, settings or None, _required_env(manifest), requires))
    return load_order(subjects), skipped


def fixture_subjects(pg_source: Path, without: Sequence[str] = (), extended: bool = False) -> list[PluginSource]:
    """The bundled demo stack; `extended` adds every plugin directory under fixtures/extended."""
    subjects = [PluginSource(k, pg_source / "fixtures" / k) for k in safety.FIXTURE_KEYS]
    if extended:
        extra = sorted(p for p in (pg_source / "fixtures" / "extended").iterdir() if (p / "plugin.yaml").is_file())
        subjects += [PluginSource(p.name, p, requires=_requires(_manifest(p))) for p in extra]
    return load_order([s for s in subjects if s.key not in set(without)])


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


def _run_throwaway(settings: RunnerSettings, argv: Sequence[str]) -> subprocess.CompletedProcess:
    """Run a short Hermes command in a throwaway PG root (installs -> warm cache), so the real Hermes tree is not
    touched: even --version takes a lease on the runtime environment it resolves (found in review)."""
    root = create_drill_root(settings.temp_base)
    try:
        ensure_private_cache(settings.warm_cache)
        (root / "home" / "installs").symlink_to(settings.warm_cache, target_is_directory=True)
        return subprocess.run(list(argv), capture_output=True, text=True, timeout=60, check=False,
                              env=child_env(os.environ, root, ""), cwd=root / "sandbox", stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        raise safety.Refusal("HERMES_UNRESPONSIVE", f"`{Path(argv[0]).name} {' '.join(argv[1:])[-60:]}` did not "
                             "answer within 60 s; check that hermes starts on its own.") from exc
    finally:
        _cleanup_root(root, ColumnSpec("version", ()), settings.temp_base)


_PREFIX_LOCK = threading.Lock()
_PREFIXES: dict[Path, tuple[str, ...]] = {}


def parse_runtime_command(stdout: str) -> tuple[str, ...] | None:
    """`hermes --print-runtime-command` prints the interpreter argv as JSON (hermes_cli/_launchers.py:75-84,
    "machine boundary for consumers holding the exact published launcher"). Insert -B after the interpreter so the
    child writes no bytecode into the Hermes tree; the launcher's -I isolation is kept."""
    try:
        argv = json.loads(stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, ValueError, IndexError):
        return None
    if not (isinstance(argv, list) and len(argv) >= 3 and all(isinstance(a, str) for a in argv) and "-I" in argv):
        return None
    return (argv[0], "-B", *argv[1:])


def launch_prefix(settings: RunnerSettings) -> tuple[str, ...]:
    """The argv prefix every child runs with: Hermes's own interpreter command plus -B, resolved once per launcher.
    Falls back to the launcher itself when it cannot print its runtime command (older Hermes)."""
    with _PREFIX_LOCK:
        if settings.hermes_bin not in _PREFIXES:
            out = _run_throwaway(settings, [str(settings.hermes_bin), "--print-runtime-command"])
            parsed = parse_runtime_command(out.stdout) if out.returncode == 0 else None
            _PREFIXES[settings.hermes_bin] = parsed or (str(settings.hermes_bin),)
        return _PREFIXES[settings.hermes_bin]


def hermes_version(hermes_bin: Path, settings: RunnerSettings | None = None) -> str:
    """`hermes --version`, through the same -B interpreter prefix as the children when settings are given."""
    if settings is None:
        out = subprocess.run([str(hermes_bin), "--version"], capture_output=True, text=True, timeout=60, check=False)
        return _parse_version(out.stdout)
    return _parse_version(_run_throwaway(settings, [*launch_prefix(settings), "--version"]).stdout)


def _parse_version(stdout: str) -> str:
    match = VERSION_RE.search(stdout)
    if match:
        return f"v{match.group(1)} @ {match.group(2)}"
    lines = [line for line in stdout.strip().splitlines() if line.startswith("Hermes")]
    return lines[0] if lines else "unknown"


def _to_column(run: CellRun) -> matrix.ColumnRun:
    return matrix.column_from_events(run.spec.name, tuple(s.key for s in run.spec.plugins), run.plan_calls,
                                     run.events, run.sandbox, run.recorder_url, run.recorded,
                                     run.abort_reason, run.wall_s, run.wires)


def _parallel(specs: Sequence[ColumnSpec], work: Callable[[ColumnSpec], CellRun], jobs: int,
              on_done: Callable[[CellRun], None] | None = None) -> list[CellRun]:
    """Run columns concurrently; report each as it finishes; return results in spec order.
    Any exception or signal cancels queued columns (cancel_futures) and kills in-flight children."""
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
    pair_total: int | None = None  # --pairs: how many pairs the stack has (None: not asked); pair columns are in columns
    pair_note: str = ""
    repeats: dict[str, list[matrix.ColumnRun]] = field(default_factory=dict)  # --repeat: extra runs per solo column
    repeat: int = 1  # --repeat N as asked
    declared_hooks: dict[str, tuple[str, ...]] = field(default_factory=dict)  # plugin -> its manifest's provides_hooks


def declared_hooks(path: Path) -> tuple[str, ...]:
    """What a plugin's manifest says it hooks (provides_hooks); a declaration, never proof of what it did."""
    return tuple(str(h) for h in _listed(_manifest(path), "provides_hooks") if isinstance(h, str) and h)


def run_drill(subjects: Sequence[PluginSource], templates: Sequence[testcalls.CallTemplate],
              settings: RunnerSettings, on_column: Callable[[matrix.ColumnRun], None] | None = None,
              skipped: Sequence[tuple[str, str]] = ()) -> DrillRun:
    CANCEL.clear()  # a new drill starts uncancelled; a signal during it sets CANCEL again
    started, canary, notify = time.monotonic(), testcalls.make_canary(), on_column or (lambda _c: None)
    subjects = load_order(subjects)
    columns, order = plan_columns(subjects), tuple(s.key for s in subjects if not s.needs_env)
    notes: list[str] = []
    hooks = {s.key: declared_hooks(s.path) for s in subjects}
    with Recorder() as recorder:
        def work(spec: ColumnSpec) -> CellRun:
            run = run_column_reliably(spec, templates, canary, recorder, settings)
            notes.extend(run.copy_notes)
            return run

        baseline = _to_column(work(columns[0]))
        notify(baseline)
        if baseline.abort_reason:  # a broken harness must not run every other column against a non-scripted child
            return DrillRun([baseline], None, {}, list(skipped), [s.key for s in subjects if s.needs_env], canary,
                            time.monotonic() - started, order, declared_hooks=hooks)
        repeat_and_rest = [ColumnSpec("baseline-repeat", ())] + columns[1:]

        def done(run: CellRun) -> None:
            if run.spec.name != "baseline-repeat":
                notify(_to_column(run))

        results = [_to_column(r) for r in _parallel(repeat_and_rest, work, settings.jobs, done)]
        crosscheck, refused = _run_crosscheck(results, subjects, canary, recorder, settings)
        view = matrix.provisional([baseline] + results[1:], results[0], order)
        pair_runs, pair_total, pair_note = _run_pairs(view, subjects, work, settings) if settings.pairs else ([], None, "")
        repeats = _run_repeats(view, columns, work, settings) if settings.repeat > 1 else {}
    return DrillRun([baseline] + results[1:] + pair_runs, results[0], crosscheck, list(skipped),
                    [s.key for s in subjects if s.needs_env], canary, time.monotonic() - started, order, refused,
                    tuple(sorted(set(notes))), pair_total, pair_note, repeats, settings.repeat, hooks)


def _pair_spec(a: PluginSource, b: PluginSource, runnable: Sequence[PluginSource]) -> ColumnSpec:
    """A pair column carries both plugins plus their requires_plugins, in load order."""
    both = {s.key: s for s in (*_solo_plugins(a, runnable), *_solo_plugins(b, runnable))}
    return ColumnSpec(f"pair:{a.key}+{b.key}", tuple(load_order(list(both.values()))))


def _run_pairs(view: matrix.Matrix, subjects: Sequence[PluginSource], work: Callable[[ColumnSpec], CellRun],
               settings: RunnerSettings) -> tuple[list[matrix.ColumnRun], int, str]:
    """--pairs: when an effect appears only in the full stack, run pairs of its plugins (at most PAIR_CAP, in load
    order) to find the smallest set that reproduces it. Returns (pair columns, pairs that exist, report note)."""
    runnable = [s for s in subjects if not s.needs_env]
    every = list(itertools.combinations(runnable, 2))
    interactions = [a for a in view.attributions if a.kind == "interaction"]
    if not interactions:
        return [], len(every), "no effect appears only in the full stack, so no pair columns were run"
    if len(runnable) == 2:
        return [], len(every), "the full stack is itself one pair, so no extra pair columns were run"
    chosen = every[:matrix.PAIR_CAP]
    runs = [_to_column(r) for r in _parallel([_pair_spec(a, b, runnable) for a, b in chosen], work, settings.jobs)]
    capped = f"; capped at {matrix.PAIR_CAP} of {len(every)} pairs" if len(every) > len(chosen) else ""
    effects = f"{len(interactions)} effect{'' if len(interactions) == 1 else 's'}"
    return runs, len(every), f"{len(runs)} pair columns run for {effects} seen only in the full stack{capped}"


def _run_repeats(view: matrix.Matrix, columns: Sequence[ColumnSpec], work: Callable[[ColumnSpec], CellRun],
                 settings: RunnerSettings) -> dict[str, list[matrix.ColumnRun]]:
    """--repeat N: the solo columns matrix.repeat_columns picks run N - 1 more times each."""
    wanted = set(matrix.repeat_columns(view))
    specs = [spec for spec in columns if spec.name in wanted for _ in range(settings.repeat - 1)]
    repeats: dict[str, list[matrix.ColumnRun]] = {name: [] for name in sorted(wanted)}
    for run in _parallel(specs, work, settings.jobs):
        repeats[run.spec.name].append(_to_column(run))
    return repeats


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
