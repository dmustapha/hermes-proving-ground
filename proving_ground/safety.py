# File: proving_ground/safety.py
# [VERIFIED by the §9.1 run] pure stdlib logic; no Hermes API. Temp-base list covers macOS (/var/folders, /private/tmp) and Linux.
"""Activation, posture refusal and sandbox containment for PROVING GROUND."""
from __future__ import annotations

import hmac
import os
import re
from pathlib import Path
from typing import Any, Iterator, Mapping

TOKEN_ENV = "PROVINGGROUND_DRILL_TOKEN"
STATE_DIRNAME = ".provingground"
TOKEN_FILENAME = "drill.token"
OWNER_MARKER = ".pg-root"  # ownership marker written atomically inside every root PG creates (ARCH-F1)
PID_FILENAME = OWNER_MARKER
ROOT_PREFIX = "provingground-"
TOKEN_HEX_LEN = 64
# tempfile.mkdtemp suffixes are exactly 8 chars from "abcdefghijklmnopqrstuvwxyz0123456789_" (tempfile._RandomNameSequence)
ROOT_NAME = re.compile(r"^provingground-[a-z0-9_]{8}$")
TEMP_BASES: tuple[str, ...] = ("/tmp", "/private/tmp", "/var/tmp", "/var/folders", "/private/var/folders")
ALLOWED_TOOLS = frozenset({"terminal", "write_file", "read_file", "patch"})
ALLOWED_ABSOLUTE_PATHS = frozenset({"/dev/null"})
ALLOWED_HOSTS = frozenset({"127.0.0.1"})
INTERACTIVE_PLATFORMS = frozenset({"cli", "tui", "local", "desktop"})
TRUTHY = frozenset({"1", "true", "yes", "on"})
NETWORK_PROGRAMS = frozenset({"curl", "wget", "nc", "ncat", "netcat", "telnet", "ssh", "scp", "sftp", "rsync",
                              "ftp", "socat", "http", "https"})
# Closed grammar for any call PG derives from someone else's output (cross-check) or a user file (ARCH-F3).
DERIVED_PROGRAMS = frozenset({"echo", "printf", "seq", "curl", "chmod"})
_DERIVED_TOKEN = re.compile(r"^[A-Za-z0-9_.,:=+@%/{}-]+$")
_DERIVED_FORBIDDEN = re.compile(r"[$`;&|<>\\()*?!\[\]]|\.\.|file:|~")
SECRET_NAME = re.compile(r"(?i)(^\.env(\..*)?$|\.pem$|\.key$|\.p12$|\.pfx$|^auth[^/]*\.json$|^credentials(\..*)?$|"
                         r"^id_(rsa|ed25519|ecdsa)$|^secrets?\.(json|ya?ml|toml)$)")
SECRET_KEY = re.compile(r"(?i)(key|token|secret|password|passwd|credential)")

_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"]*")
_FILE_SCHEME = re.compile(r"(?i)\bfile:")
_ABS_PATH = re.compile(r"(?<![\w.:/~-])(/[^\s'\";|&<>()]*)")
_FLAG_PATH = re.compile(r"(?:^|[\s'\"=])-[A-Za-z]+(/[^\s'\";|&<>()]*)")
_HOME_REF = re.compile(r"(?<![\w/.-])~")
_PARENT_REF = re.compile(r"(?:^|[\s'\"=/;&|<>()])\.\.(?=$|[/\s'\";&|<>()])")
_SHELL_EXPANSION = re.compile(r"\$\{?[A-Za-z_]|\$\(|`")


class Refusal(Exception):
    """A drill was refused before any child process was spawned."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def under_temp_base(path: Path) -> bool:
    for base in TEMP_BASES:
        base_path = Path(base)
        if base_path.exists() and path.resolve().is_relative_to(base_path.resolve()):
            return True
    return False


def drill_root_of(home: Path) -> Path | None:
    """Return <root> when *home* resolves to <temp>/provingground-XXXXXXXX/home, else None."""
    real = home.resolve()
    root = real.parent
    if real.name != "home" or not ROOT_NAME.match(root.name):
        return None
    return root if under_temp_base(root) else None


def is_drill_child(environ: Mapping[str, str] | None = None) -> bool:
    """Triple-key activation: env token + matching token file + temp-root path shape."""
    env = os.environ if environ is None else environ
    token = env.get(TOKEN_ENV, "")
    home_raw, tmp_raw = env.get("HERMES_HOME", ""), env.get("TMPDIR", "")
    if len(token) != TOKEN_HEX_LEN or not home_raw or not tmp_raw:
        return False
    root = drill_root_of(Path(home_raw))
    if root is None or Path(tmp_raw).resolve() != root / "tmp":
        return False
    try:
        on_disk = (Path(home_raw) / STATE_DIRNAME / TOKEN_FILENAME).read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return hmac.compare_digest(on_disk, token)


def normalize_approval_mode(value: Any) -> str:
    """Mirror Hermes's own reading (tools/approval_context.py _normalize_approval_mode): YAML 1.1 loads a bare
    `off` as False, so False means "off"; any other bool, a missing key or an empty value means "manual"."""
    if value is False:
        return "off"
    if value is None or isinstance(value, bool):
        return "manual"
    return str(value).strip().lower() or "manual"


def check_posture(environ: Mapping[str, str], approvals_mode: Any, platform: str | None, in_cron: bool) -> None:
    """Raise Refusal when the user's posture bypasses approvals or nobody is present."""
    if environ.get("HERMES_YOLO_MODE", "").strip().lower() in TRUTHY:
        raise Refusal("REFUSED_YOLO", "YOLO mode is on; PROVING GROUND runs only with approvals on.")
    if normalize_approval_mode(approvals_mode) == "off":
        raise Refusal("REFUSED_APPROVALS_OFF", "approvals.mode is off; turn approvals on to run a wiring test.")
    if in_cron:
        raise Refusal("REFUSED_UNATTENDED", "cron and unattended runs are refused; run it from a terminal.")
    if platform is not None and platform not in INTERACTIVE_PLATFORMS:
        raise Refusal("REFUSED_PLATFORM", f"/provingground runs from the CLI, TUI or Desktop, not {platform}.")


def iter_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from iter_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from iter_strings(item)


def _inside(path: Path, box: Path) -> bool:
    try:
        return path.resolve().is_relative_to(box)
    except (OSError, RuntimeError, ValueError):
        return False


def _url_host(url: str) -> str:
    from urllib.parse import urlsplit

    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return "unparseable"


def _host_reasons(text: str) -> list[str]:
    found = []
    for url in _URL.findall(text):
        host = _url_host(url)
        if host not in ALLOWED_HOSTS and not host.endswith(".invalid"):
            found.append(f"host:{host or 'none'}")
    if _FILE_SCHEME.search(text):
        found.append("file-scheme")
    return found


def _network_target_reasons(text: str) -> list[str]:
    """A network program's non-flag arguments must be a loopback URL; a bare host (`nc evil.com 80`) is an escape."""
    words, found, net = text.replace(";", " ; ").replace("&", " & ").replace("|", " | ").split(), [], False
    for word in words:
        if word in (";", "&", "|", "&&", "||"):
            net = False
        elif Path(word).name in NETWORK_PROGRAMS:
            net = True
        elif net and not word.startswith("-") and "://" not in word and not word.startswith("/") \
                and not word.isdigit() and not word.startswith("{"):
            found.append(f"network-target:{word}")
    return found


def _text_escape_reasons(text: str, box: Path) -> list[str]:
    found = _host_reasons(text) + _network_target_reasons(text)
    if _HOME_REF.search(text):
        found.append("home-relative-path")
    if _PARENT_REF.search(text):
        found.append("parent-relative-path")
    if _SHELL_EXPANSION.search(text):
        found.append("shell-expansion")
    for raw in _ABS_PATH.findall(text) + _FLAG_PATH.findall(text):
        if raw not in ALLOWED_ABSOLUTE_PATHS and not _inside(Path(raw), box):
            found.append(f"path:{raw}")
    return found


def escape_reasons(args: Mapping[str, Any], sandbox: Path) -> list[str]:
    """Every reason the tool-call *args* reach outside *sandbox*; empty list means contained.
    Post-hoc detector and first filter only; never the gate for replaying derived calls (see derived_call_reasons)."""
    box = sandbox.resolve()
    reasons: list[str] = []
    for text in iter_strings(args):
        reasons.extend(_text_escape_reasons(text, box))
    return sorted(set(reasons))


def _derived_terminal_reasons(command: str, recorder_url: str) -> list[str]:
    if _DERIVED_FORBIDDEN.search(command):
        return ["grammar:forbidden-character"]
    words = command.split()
    if not words or words[0] not in DERIVED_PROGRAMS:
        return [f"grammar:program:{words[0] if words else 'none'}"]
    found = []
    for word in words[1:]:
        if not _DERIVED_TOKEN.match(word):
            found.append(f"grammar:token:{word[:40]}")
        elif "://" in word and not (word == recorder_url or word.startswith(recorder_url + "/")):
            found.append(f"grammar:url:{word[:40]}")
    return found


def derived_call_reasons(tool: str, args: Mapping[str, Any], sandbox: Path, recorder_url: str) -> list[str]:
    """Closed allow-list grammar for calls PG did not author itself (cross-check replays, user test calls).
    A terminal command must be `echo|printf|seq|curl|chmod` followed by plain tokens: no shell metacharacters,
    no `$`, backticks, `..`, `~` or `file:`, URLs only on the run's own loopback recorder. Every path must also stay
    in the sandbox. Anything else is never executed; the caller records it as unreplayable."""
    if tool not in ALLOWED_TOOLS:
        return [f"grammar:tool:{tool}"]
    reasons = list(escape_reasons(args, sandbox))
    if tool == "terminal":
        reasons += _derived_terminal_reasons(str(args.get("command", "")), recorder_url)
    else:
        reasons += [f"grammar:forbidden-character:{k}" for k, v in args.items()
                    if k == "path" and _DERIVED_FORBIDDEN.search(str(v))]
    return sorted(set(reasons))


def write_owner_marker(root: Path, pid: int, created: float) -> None:
    """Atomically record that PG created *root* (tmp file + os.replace inside the fresh mkdtemp dir)."""
    tmp = root / (OWNER_MARKER + ".tmp")
    tmp.write_text(f"{pid} {created:.3f}\n", encoding="utf-8")
    os.replace(tmp, root / OWNER_MARKER)


def read_owner_marker(root: Path) -> tuple[int, float] | None:
    """(pid, created) from a parseable marker, else None. A missing or unreadable marker never means "stale"."""
    try:
        pid_text, created_text = (root / OWNER_MARKER).read_text(encoding="utf-8").split()
        return int(pid_text), float(created_text)
    except (OSError, ValueError):
        return None


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
