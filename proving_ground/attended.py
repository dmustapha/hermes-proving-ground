# File: proving_ground/attended.py
# `--attended`: one extra full-stack column whose approval-worthy calls reach Hermes's own interactive approval
# prompt, answered by the person at the terminal. PROVING GROUND never answers, never writes a byte of its own to the
# child, and never configures an allow: the child's terminal is a pty whose only input is the user's own keystrokes.
from __future__ import annotations

import fcntl
import os
import re
import secrets
import select
import struct
import subprocess
import sys
import termios
import time
import tty
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import runner, safety, testcalls

APPROVAL_TIMEOUT_S = 60  # approvals.timeout in the attended child: Hermes's own prompt counts it down
APPROVAL_WORTHY = "APPROVAL_GATED"  # a built-in call whose baseline is headless-denied is the one Hermes asks about
STARTUP_S = 90.0  # child start-up and the scripted turns around the prompts
DONE_GRACE_S = 1.5  # let Hermes paint its last line after the scripted turn is done
DONE_NEEDLE = b'"ev": "done"'
COLUMN = "attended"
NON_INTERACTIVE_ENV = ("HERMES_GATEWAY_SESSION", "HERMES_CRON_SESSION", "HERMES_SINGLE_QUERY_SESSION",
                       "HERMES_EXEC_ASK")
TERMINAL_RESTORE = b"\x1b[0m\x1b[?25h\x1b[?2004l\r\n"  # written to the user's terminal, never to the child

PREFACE = ("PROVING GROUND --attended: Hermes will ask you to approve {n} {calls} from a test drill; they are inert "
           "sandbox operations; PROVING GROUND will not answer for you. Answer each prompt as you see fit "
           "(Hermes denies it after {timeout} s without an answer).")

# The tool result Hermes returns after its own prompt (tools/terminal_tool.py:1142 for an approval; the CLI deny and
# timeout texts in tools/approval.py _GateSpec cli_denied / cli_timeout and the terminal tool's own deny).
_APPROVED = re.compile(r"was approved by the user", re.I)
_DENIED = re.compile(r"denied by (the )?user|user denied|you denied", re.I)
_TIMED_OUT = re.compile(r"timed out without user response", re.I)
_UNANSWERED = re.compile(r"could not be delivered|was not answered", re.I)

APPROVED, DENIED, TIMED_OUT, UNANSWERED = ("approved by you", "denied by you", "timed out (denied)",
                                           "not answered (denied)")


@dataclass(frozen=True)
class AttendedCell:
    call_id: str
    asked: bool
    answer: str  # one of the four answers above when asked, else what happened instead
    seconds: float | None = None

    @property
    def label(self) -> str:
        if not self.asked:
            return self.answer
        shown = f" after {self.seconds:.0f} s" if self.seconds is not None else ""
        return f"ASKED → {self.answer}{shown}"


@dataclass
class AttendedRun:
    cells: list[AttendedCell]
    abort_reason: str | None
    wall_s: float
    plugins: tuple[str, ...]


def check_attended(environ: Mapping[str, str], stdin: Any, stdout: Any, *, slash: bool = False) -> None:
    """Refusals specific to --attended; the shared posture refusals (YOLO, approvals off, cron, platform) run too."""
    if slash:
        raise safety.Refusal("REFUSED_ATTENDED_SLASH", "--attended runs only from `hermes provingground` in a "
                             "terminal, not from the /provingground slash command.")
    platform = environ.get("HERMES_SESSION_PLATFORM", "").strip().lower()
    if platform and platform not in safety.INTERACTIVE_PLATFORMS:
        raise safety.Refusal("REFUSED_PLATFORM", f"--attended needs a person at a terminal, not {platform}.")
    if any(environ.get(name, "").strip().lower() in safety.TRUTHY for name in NON_INTERACTIVE_ENV):
        raise safety.Refusal("REFUSED_UNATTENDED", "--attended needs a person at a terminal; this session is "
                             "unattended (gateway, cron or single-query).")
    if not (_isatty(stdin) and _isatty(stdout)):
        raise safety.Refusal("REFUSED_NOT_A_TTY", "--attended needs an interactive terminal on stdin and stdout, "
                             "so you can answer Hermes's approval prompt yourself.")


def _isatty(stream: Any) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def approval_templates(templates: Sequence[testcalls.CallTemplate]) -> list[testcalls.CallTemplate]:
    asked = [t for t in templates if t.expect_baseline == APPROVAL_WORTHY]
    if not asked:
        raise safety.Refusal("ATTENDED_NOTHING_TO_ASK", "--attended needs at least one approval-worthy test call "
                             "(TC10); the selected calls have none.")
    return asked


def preface(n: int) -> str:
    return PREFACE.format(n=n, calls="call" if n == 1 else "calls", timeout=APPROVAL_TIMEOUT_S)


def child_config(home: Path, keys: Sequence[str], entries: Mapping[str, Any], sandbox: Path) -> None:
    """The headless child config with only the approval timeout changed; mode stays manual, unattended modes deny."""
    text = runner.render_child_config(keys, entries, sandbox, approval_timeout=APPROVAL_TIMEOUT_S)
    (home / "config.yaml").write_text(text, encoding="utf-8")


def child_argv(launch: Sequence[str], n_calls: int) -> list[str]:
    """Interactive: -q on a real TTY seeds an interactive session (hermes_cli/_parser.py:237-240, cli.py:846-853);
    no -Q, no --oneshot, so HERMES_SINGLE_QUERY_SESSION is never set and Hermes's own CLI prompt asks."""
    return [*launch, "chat", "-q", runner.DRILL_PROMPT, "--cli", "-t", "terminal,file",
            "--provider", "custom", "--base-url", runner.DEAD_PROVIDER_URL, "-m", safety.SCRIPTED_MODEL,
            "--max-turns", str(n_calls + 2)]


def stack_spec(subjects: Sequence[runner.PluginSource]) -> runner.ColumnSpec:
    runnable = tuple(runner.load_order([s for s in subjects if not s.needs_env]))
    return runner.ColumnSpec(COLUMN, runnable)


def _copy_winsize(src_fd: int, dst_fd: int) -> None:
    try:
        size = fcntl.ioctl(src_fd, termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0))
        fcntl.ioctl(dst_fd, termios.TIOCSWINSZ, size)
    except OSError:
        return


def _controlling_tty() -> None:  # runs in the child after setsid (start_new_session)
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


def spawn_in_pty(argv: Sequence[str], env: Mapping[str, str], cwd: Path, stdout_fd: int) -> tuple[subprocess.Popen, int]:
    master, slave = os.openpty()
    _copy_winsize(stdout_fd, slave)
    try:
        proc = subprocess.Popen(list(argv), env=dict(env), cwd=cwd, stdin=slave, stdout=slave, stderr=slave,
                                start_new_session=True, preexec_fn=_controlling_tty, close_fds=True)
    finally:
        os.close(slave)
    with runner._LIVE_LOCK:
        runner._LIVE_PROCS[proc.pid] = proc
    return proc, master


def _relay(master: int, stdin_fd: int, stdout_fd: int, timeout: float) -> bool:
    """One relay step. Child output goes to the user's terminal; the user's keystrokes go to the child. This is the
    only write to the child's terminal, and it only ever carries bytes read from the user's own stdin."""
    ready, _, _ = select.select([master, stdin_fd], [], [], timeout)
    if master in ready:
        try:
            data = os.read(master, 65536)
        except OSError:
            return False
        if not data:
            return False
        os.write(stdout_fd, data)
    if stdin_fd in ready:
        keys = os.read(stdin_fd, 1024)
        if keys:
            os.write(master, keys)
    return True


def _done(events_path: Path) -> bool:
    try:
        return DONE_NEEDLE in events_path.read_bytes()
    except OSError:
        return False


def _stop_reason(scan: runner.EscapeScan, deadline: float) -> str | None:
    if runner.CANCEL.is_set():
        return "CANCELLED"
    if scan.seen():
        return "ESCAPED"
    return "CELL_TIMEOUT" if time.monotonic() > deadline else None


def passthrough(proc: subprocess.Popen, master: int, events_path: Path, stdin_fd: int, stdout_fd: int,
                limit_s: float) -> str | None:
    """Relay until the scripted turn is done (then stop the interactive child), the child exits, or a stop reason."""
    deadline, scan, done_at = time.monotonic() + limit_s, runner.EscapeScan(events_path), None
    while proc.poll() is None:
        reason = _stop_reason(scan, deadline)
        if reason:
            return reason
        if done_at is None and _done(events_path):
            done_at = time.monotonic()
        if done_at is not None and time.monotonic() - done_at > DONE_GRACE_S:
            return None
        if not _relay(master, stdin_fd, stdout_fd, runner.POLL_S):
            break
    return None


class _RawInput:
    """The user's terminal in raw mode while Hermes owns it, restored however the column ends."""

    def __init__(self, fd: int) -> None:
        self.fd, self.saved = fd, None

    def __enter__(self) -> "_RawInput":
        if os.isatty(self.fd):
            self.saved = termios.tcgetattr(self.fd)
            tty.setraw(self.fd)
        return self

    def __exit__(self, *_exc: Any) -> None:
        if self.saved is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)


def _result_text(event: Mapping[str, Any]) -> str:
    return " ".join(str(event.get(k) or "") for k in ("result", "error_message"))


def _call_events(events: Sequence[Mapping[str, Any]], call_id: str) -> list[Mapping[str, Any]]:
    tool_call_id = f"pgcall_{call_id}"
    return [e for e in events if e.get("tool_call_id") == tool_call_id]


def _answer(text: str) -> str | None:
    for pattern, answer in ((_APPROVED, APPROVED), (_TIMED_OUT, TIMED_OUT), (_UNANSWERED, UNANSWERED),
                            (_DENIED, DENIED)):
        if pattern.search(text):
            return answer
    return None


def classify_call(events: Sequence[Mapping[str, Any]], call_id: str) -> AttendedCell:
    """From the recorded hook events alone: the tool result Hermes returned after its own prompt says who answered."""
    mine = _call_events(events, call_id)
    post = next((e for e in mine if e.get("ev") == "post_tool_call"), None)
    if post is None:
        return AttendedCell(call_id, False, "NOT RUN (the call never reached a tool)")
    answer = _answer(_result_text(post))
    if answer is None:
        ran = any(e.get("ev") == "terminal_raw" for e in mine)
        return AttendedCell(call_id, False, "not asked (ran without a prompt)" if ran else "not asked")
    pre = next((e for e in mine if e.get("ev") == "pre_tool_call"), None)
    seconds = float(post["t"]) - float(pre["t"]) if pre and "t" in pre and "t" in post else None
    return AttendedCell(call_id, True, answer, seconds)


def classify(events: Sequence[Mapping[str, Any]], call_ids: Sequence[str],
             abort_reason: str | None = None) -> list[AttendedCell]:
    cells = [classify_call(events, cid) for cid in call_ids]
    if abort_reason:
        return [c if c.asked else AttendedCell(c.call_id, False, f"NOT RUN ({abort_reason})") for c in cells]
    return cells


def run_attended(subjects: Sequence[runner.PluginSource], templates: Sequence[testcalls.CallTemplate],
                 settings: runner.RunnerSettings, stdin_fd: int, stdout_fd: int) -> AttendedRun:
    asked, spec = approval_templates(templates), stack_spec(subjects)
    root, token, started = runner.create_drill_root(settings.temp_base), secrets.token_hex(32), time.monotonic()
    master = None
    try:
        with runner.Recorder() as recorder:
            ctx = testcalls.SandboxContext(root / "sandbox", f"{recorder.url}/{secrets.token_hex(6)}",
                                           testcalls.make_canary())
            calls = testcalls.render(asked, ctx)
            runner.seed_sandbox(root / "sandbox", calls)
            home = runner.build_child_home(root, spec, settings, calls, token, [])
            keys = [s.key for s in spec.plugins] + [runner.child_pg_key([s.key for s in spec.plugins])]
            entries = {s.key: runner.redact_entries(s.settings, [], s.key) for s in spec.plugins if s.settings}
            child_config(home, keys, entries, root / "sandbox")
            env = runner.child_env(os.environ, root, token)
            proc, master = spawn_in_pty(child_argv(runner.launch_prefix(settings), len(calls)), env,
                                        root / "sandbox", stdout_fd)
            limit = STARTUP_S + APPROVAL_TIMEOUT_S * len(calls) + 15.0
            events_path = home / safety.STATE_DIRNAME / safety.EVENTS_FILENAME
            try:
                with _RawInput(stdin_fd):
                    watch = passthrough(proc, master, events_path, stdin_fd, stdout_fd, limit)
            finally:
                runner._kill_group(proc)
                with runner._LIVE_LOCK:
                    runner._LIVE_PROCS.pop(proc.pid, None)
                os.write(stdout_fd, TERMINAL_RESTORE)
            events = runner.read_events(home)
            reason = runner.abort_reason_from(events, watch, None, expect_done=True)
            return AttendedRun(classify(events, [c.id for c in calls], reason), reason,
                               time.monotonic() - started, tuple(s.key for s in spec.plugins))
    finally:
        if master is not None:
            os.close(master)
        runner._cleanup_root(root, spec, settings.temp_base)


def render_column(run: AttendedRun, subjects: Mapping[str, str]) -> str:
    """The extra column, printed after the headless matrix; ASKED appears only here, never in the default output."""
    plugins = ", ".join(run.plugins) or "no plugins besides PROVING GROUND"
    lines = [f"attended column (full stack: {plugins}; Hermes asked, you answered; PROVING GROUND did not):"]
    width = max((len(c.call_id) + len(subjects.get(c.call_id, "")) + 1 for c in run.cells), default=0)
    for cell in run.cells:
        name = f"{cell.call_id} {subjects.get(cell.call_id, '')}".strip()
        lines.append(f"  {name.ljust(width)}  {cell.label}")
    if run.abort_reason:
        lines.append(f"  attended column stopped: {run.abort_reason}")
    return "\n".join(lines)


def exit_code(run: AttendedRun) -> int:
    if run.abort_reason == "ESCAPED":
        return 4
    return 3 if run.abort_reason else 0


def stdio_fds() -> tuple[int, int]:
    return sys.stdin.fileno(), sys.stdout.fileno()
