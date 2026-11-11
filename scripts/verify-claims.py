# File: scripts/verify-claims.py
# stdlib + pytest (dev venv). Usage: python scripts/verify-claims.py [--hermes] [--report PATH]
# Without --hermes, rows backed by Hermes-tier tests are reported as SKIP, never as ok. "report" rows read
# docs/fixture-report.json unless --report names another saved report.
"""Check every CLAIMS.md row against its evidence. Exit 1 on any failure."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROW = re.compile(r"^\|\s*(CL-\d+[a-z]?)\s*\|\s*(.+?)\s*\|\s*(test|grep|absent|report)\s*\|\s*(.+?)\s*\|\s*$")
SHIPPED = ["__init__.py", "proving_ground", "fixtures"]
HERMES_TESTS = ("test_escape_abort", "test_home_untouched", "test_cleanup.py::test_signal", "test_drill_fixtures_e2e",
                "test_golden_fixture_matrix", "test_options_e2e", "test_seed.py::test_real", "test_extended_fixture_stack",
                "test_middleware_drill_e2e", "test_coverage_calls_e2e", "test_attended.py::test_pty",
                "test_wires.py::test_a_child_registers")  # the last one needs Hermes's own openai module
COMMITTED_REPORT = ROOT / "docs" / "fixture-report.json"
OK, SKIP, FAIL = "ok", "SKIP", "FAIL"


def rows() -> list[tuple[str, str, str, str]]:
    found = [m.groups() for line in (ROOT / "CLAIMS.md").read_text(encoding="utf-8").splitlines()
             if (m := ROW.match(line))]
    return [(a, b, c, d) for a, b, c, d in found]


def shipped_text() -> str:
    files = [ROOT / "__init__.py", *(ROOT / "proving_ground").glob("*.py"), *(ROOT / "fixtures").glob("*/__init__.py")]
    return "\n".join(p.read_text(encoding="utf-8") for p in files)


def check_test(node: str, hermes: bool) -> tuple[str, str]:
    if any(marker in node for marker in HERMES_TESTS) and not hermes:
        return SKIP, "needs --hermes (Hermes-tier test)"
    proc = subprocess.run([sys.executable, "-m", "pytest", node, "-q", "-rs"], cwd=ROOT, capture_output=True, text=True)
    summary = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else proc.stderr[-200:]
    if proc.returncode != 0:
        return FAIL, summary
    if "skipped" in summary and "passed" not in summary:
        return FAIL, f"the cited test was skipped, so it proves nothing: {summary}"
    return OK, summary


def verdict(ok: bool) -> str:
    return OK if ok else FAIL


def check_grep(evidence: str) -> tuple[str, str]:
    path, _, pattern = evidence.partition(" :: ")
    text = (ROOT / path.strip()).read_text(encoding="utf-8")
    return verdict(re.search(pattern.strip(), text) is not None), path.strip()


def check_report(evidence: str, report_path: Path) -> tuple[str, str]:
    dotted, _, expected = evidence.partition("==")
    value: object = json.loads(report_path.read_text(encoding="utf-8"))
    for key in dotted.strip().split("."):
        value = value[key] if isinstance(value, dict) else None
    return verdict(str(value) == expected.strip()), f"{report_path.name}: {dotted.strip()} = {value}"


def check(kind: str, evidence: str, args: argparse.Namespace) -> tuple[str, str]:
    if kind == "test":
        return check_test(evidence, args.hermes)
    if kind == "grep":
        return check_grep(evidence)
    if kind == "absent":
        return verdict(re.search(evidence, shipped_text()) is None), "shipped source"
    return check_report(evidence, Path(args.report) if args.report else COMMITTED_REPORT)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hermes", action="store_true", help="also run Hermes-tier tests")
    parser.add_argument("--report", default=None,
                        help="a saved provingground.report.v1 JSON file (default: docs/fixture-report.json)")
    args = parser.parse_args()
    counts = {OK: 0, SKIP: 0, FAIL: 0}
    for claim_id, claim, kind, evidence in rows():
        status, detail = check(kind, evidence, args)
        counts[status] += 1
        print(f"{status:<4} {claim_id} [{kind}] {claim} — {detail}")
    print(f"{len(rows())} claims: {counts[OK]} ok, {counts[SKIP]} skipped (needs --hermes), {counts[FAIL]} failing")
    return 1 if counts[FAIL] else 0


if __name__ == "__main__":
    sys.exit(main())
