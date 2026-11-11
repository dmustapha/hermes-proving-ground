# File: tests/data/fx_crash/__init__.py
"""Test-only plugin whose register() raises."""
from __future__ import annotations

from typing import Any


def register(ctx: Any) -> None:
    raise RuntimeError("fx_crash: deliberate load failure for the PROVING GROUND test suite")
