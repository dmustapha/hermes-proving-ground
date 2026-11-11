# File: scripts/verify-claims.py
# [VERIFIED by the §9.1 run] stdlib + pytest (dev venv). Usage: python scripts/verify-claims.py [--hermes] [--report PATH]
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
HERMES_TESTS = ("test_escape_abort", "test_home_untouched", "test_cleanup.py::test_signal", "test_drill_fixtures_e2e")


def rows() -> list[tuple[str, str, str, str]]:
    found = [m.groups() for line in (ROOT / "CLAIMS.md").read_text(encoding="utf-8").splitlines()
             if (m := ROW.match(line))]
    return [(a, b, c, d) for a, b, c, d in found]


def shipped_text() -> str:
    files = [ROOT / "__init__.py", *(ROOT / "proving_ground").glob("*.py"), *(ROOT / "fixtures").glob("*/__init__.py")]
    return "\n".join(p.read_text(encoding="utf-8") for p in files)


def check_test(node: str, hermes: bool) -> tuple[bool, str]:
    if any(marker in node for marker in HERMES_TESTS) and not hermes:
        return True, "skipped (Hermes tier; run with --hermes)"
    proc = subprocess.run([sys.executable, "-m", "pytest", node, "-q"], cwd=ROOT, capture_output=True, text=True)
    return proc.returncode == 0, proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else proc.stderr[-200:]


def check_grep(evidence: str) -> tuple[bool, str]:
    path, _, pattern = evidence.partition(" :: ")
    text = (ROOT / path.strip()).read_text(encoding="utf-8")
    return re.search(pattern.strip(), text) is not None, path.strip()


def check_report(evidence: str, report_path: Path | None) -> tuple[bool, str]:
    if report_path is None:
        return True, "skipped (pass --report PATH)"
    dotted, _, expected = evidence.partition("==")
    value: object = json.loads(report_path.read_text(encoding="utf-8"))
    for key in dotted.strip().split("."):
        value = value[key] if isinstance(value, dict) else None
    return str(value) == expected.strip(), f"{dotted.strip()} = {value}"


def check(kind: str, evidence: str, args: argparse.Namespace) -> tuple[bool, str]:
    if kind == "test":
        return check_test(evidence, args.hermes)
    if kind == "grep":
        return check_grep(evidence)
    if kind == "absent":
        return re.search(evidence, shipped_text()) is None, "shipped source"
    return check_report(evidence, Path(args.report) if args.report else None)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hermes", action="store_true", help="also run Hermes-tier tests")
    parser.add_argument("--report", default=None, help="a saved provingground.report.v1 JSON file")
    args = parser.parse_args()
    failures = 0
    for claim_id, claim, kind, evidence in rows():
        ok, detail = check(kind, evidence, args)
        failures += 0 if ok else 1
        print(f"{'ok ' if ok else 'FAIL'} {claim_id} [{kind}] {claim} — {detail}")
    print(f"{len(rows())} claims, {failures} failing")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
