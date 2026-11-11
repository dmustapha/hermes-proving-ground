# Debug P4 M-03: bounded reads and writes. The output tail reads from the end, events are read up to a cap (a longer
# log becomes a LOG_LIMIT abort), the escape scan reads only new bytes, an oversized child log stops the column, and the
# saved report is clipped to a size cap with only the newest reports kept.
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from helpers import fixture_like_matrix

from proving_ground import cli, report, runner, safety


def test_tail_reads_only_the_end(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "child.log"
    log.write_bytes(b"x" * 5_000_000 + b"THE END")
    reads = []
    real_open = Path.open

    def spy(self, *a, **k):
        handle = real_open(self, *a, **k)
        real_read = handle.read
        handle.read = lambda *ra: reads.append(r := real_read(*ra)) or r
        return handle

    monkeypatch.setattr(Path, "open", spy)
    assert runner._tail(log, 100).endswith("THE END")
    assert sum(map(len, reads)) <= 400


def _events(home: Path, lines: list[str]) -> Path:
    path = home / safety.STATE_DIRNAME / "events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return path


def test_events_over_the_cap_become_a_log_limit_abort(tmp_path: Path) -> None:
    filler = json.dumps({"t": 2.0, "ev": "noise", "x": "y" * 1000})
    _events(tmp_path, [json.dumps({"t": 1.0, "ev": "activated"})] + [filler] * 50)
    events = runner.read_events(tmp_path, max_bytes=10_000)
    assert events[0]["ev"] == "activated" and len(events) < 12
    assert runner.abort_reason_from(events, None) == "LOG_LIMIT"


def test_events_under_the_cap_are_read_whole(tmp_path: Path) -> None:
    _events(tmp_path, [json.dumps({"t": 1.0, "ev": "activated"}), json.dumps({"t": 2.0, "ev": "done"})])
    assert [e["ev"] for e in runner.read_events(tmp_path)] == ["activated", "done"]


def test_escape_scan_reads_only_new_bytes_and_finds_a_split_needle(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"ev": "call"}\n' * 1000)
    scan = runner.EscapeScan(path)
    assert not scan.seen() and scan.offset == path.stat().st_size
    with path.open("ab") as handle:
        handle.write(b'{"ev": "esc')
    assert not scan.seen()
    with path.open("ab") as handle:
        handle.write(b'aped"}\n')
    before = scan.offset
    assert scan.seen() and scan.offset - before == len(b'aped"}\n')


def test_an_oversized_child_log_stops_the_column(tmp_path: Path, settings, monkeypatch) -> None:
    monkeypatch.setattr(runner, "LOG_LIMIT_BYTES", 1000)
    log = tmp_path / "child.log"
    log.write_bytes(b"z" * 2000)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    try:
        _code, reason = runner._watch(proc, tmp_path / "events.jsonl", settings, log)
    finally:
        proc.kill()
        proc.wait()
    assert reason == "LOG_LIMIT"


def test_the_saved_report_is_clipped_to_the_cap(monkeypatch) -> None:
    m = fixture_like_matrix()
    big = report.matrix_to_dict(m)
    big["conditions"]["approvals"] = "A" * 3_000_000
    monkeypatch.setattr(report, "matrix_to_dict", lambda _m: big)
    text = cli.bounded_report_json(m, max_bytes=200_000)
    assert len(text.encode("utf-8")) <= 200_000 and cli.CLIP_MARK in text
    assert json.loads(text)["schema"] == big["schema"]


def test_a_normal_report_is_not_clipped_and_still_round_trips() -> None:
    m = fixture_like_matrix()
    text = cli.bounded_report_json(m)
    assert cli.CLIP_MARK not in text and json.loads(text) == json.loads(json.dumps(report.matrix_to_dict(m)))


def test_only_the_newest_reports_are_kept(tmp_path: Path) -> None:
    for i in range(cli.REPORTS_KEPT + 5):
        (tmp_path / f"report-2026-11-{i:02d}.json").write_text("{}", encoding="utf-8")
    (tmp_path / cli.LATEST).write_text("{}", encoding="utf-8")
    cli._prune_reports(tmp_path)
    kept = sorted(p.name for p in tmp_path.glob("report-*.json"))
    assert len(kept) == cli.REPORTS_KEPT and kept[0] == "report-2026-11-05.json" and (tmp_path / cli.LATEST).exists()
