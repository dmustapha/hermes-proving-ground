# File: tests/test_layering.py
# The one-way dependency arrow: the pure core imports no adapters, no Hermes and no I/O modules.
from __future__ import annotations

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "proving_ground"
ALLOWED = {
    "matrix.py": {"__future__", "json", "re", "dataclasses", "enum", "typing"},
    "report.py": {"__future__", "difflib", "html", "io", "re", "dataclasses", "typing", "rich", "matrix"},
}


def imported_roots(path: Path) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".")[0] if node.level == 0 else
                      ("matrix" if any(a.name == "matrix" for a in node.names) else node.module or ""))
    return roots


def test_core_modules_import_only_allowed_names() -> None:
    for name, allowed in ALLOWED.items():
        extra = imported_roots(PKG / name) - allowed
        assert extra == set(), f"{name} imports {extra}"
