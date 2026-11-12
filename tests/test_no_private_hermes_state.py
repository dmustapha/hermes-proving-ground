# File: tests/test_no_private_hermes_state.py
# Catalog review (PR #153961): a listed plugin must not read or rewrite Hermes's private plugin-manager state, or
# change process-wide state of the live Hermes process. These are static checks over the shipped source.
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = [ROOT / "__init__.py", *sorted((ROOT / "proving_ground").glob("*.py"))]
CODE = {p.name: "\n".join(l for l in p.read_text(encoding="utf-8").splitlines() if not l.lstrip().startswith("#"))
        for p in SHIPPED}


def _strip_strings(text: str) -> str:
    return re.sub(r'(?s)""".*?"""|"[^"\n]*"|\'[^\'\n]*\'', '""', text)


def test_no_private_plugin_manager_access() -> None:
    pattern = re.compile(r"\b_manager\b|\._hooks\b|\._middleware\b|\._plugins\b|[\"'](_manager|_hooks|_middleware|_plugins)[\"']")
    hits = {name: pattern.findall(_strip_strings(code)) for name, code in CODE.items()}
    raw = {name: re.findall(r"getattr\([^)]*[\"']_(manager|hooks|middleware|plugins)[\"']", code) for name, code in CODE.items()}
    assert {k: v for k, v in hits.items() if v} == {} and {k: v for k, v in raw.items() if v} == {}


def test_tempfile_tempdir_is_never_assigned() -> None:
    assert {n for n, code in CODE.items() if re.search(r"tempfile\.tempdir\s*=", code)} == set()
