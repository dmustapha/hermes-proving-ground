# File: tests/test_approvals_static.py
# I4a: PROVING GROUND never answers, approves or bypasses an approval.
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = ("register_approval_transport", ".respond(", "resolve_gateway_approval", "approve_session",
             "enable_session_yolo", "\"--yolo\"", "\"-z\"", "\"--oneshot\"", "HERMES_YOLO_MODE\": \"1",
             "single_query_mode: \"approve\"", "tools.approval")


def shipped_sources() -> list[Path]:
    return [ROOT / "__init__.py", *sorted((ROOT / "proving_ground").glob("*.py")),
            *sorted((ROOT / "fixtures").glob("*/__init__.py"))]


def test_no_approval_bypass_tokens_in_shipped_code() -> None:
    for path in shipped_sources():
        text = path.read_text(encoding="utf-8")
        found = [token for token in FORBIDDEN if token in text]
        assert found == [], f"{path.name}: {found}"
