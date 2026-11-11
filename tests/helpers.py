# File: tests/helpers.py
# [VERIFIED by the §9.1 run] synthetic events in the exact observer format of ARCHITECTURE §3.5.
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from proving_ground import matrix

SANDBOX = "/tmp/provingground-unittest0/sandbox"
URL = "http://127.0.0.1:5555"
IDS = ("TC01", "TC02", "TC03", "TC04", "TC05")
LONG = "\n".join(str(i) for i in range(1, 200))
LOG = "test_alpha ... ok\ntest_beta ... FAILED\ntest_gamma ... ok\n"
COMMANDS = {"TC01": "echo PG_PLAIN_MARKER TC01", "TC02": "echo PG_GUARD_MARKER TC02",
            "TC03": "echo PG_REWRITE_CANDIDATE TC03", "TC04": "printf log", "TC05": "seq 1 199"}
OUTPUTS = {"TC01": "PG_PLAIN_MARKER TC01\n", "TC02": "PG_GUARD_MARKER TC02\n",
           "TC03": "PG_REWRITE_CANDIDATE TC03\n", "TC04": LOG, "TC05": LONG}


def plan(ids: Sequence[str] = IDS) -> list[dict[str, Any]]:
    return [{"id": i, "call_ref": f"pgcall_{i}", "tool": "terminal", "args": {"command": COMMANDS.get(i, f"echo {i}")}}
            for i in ids]


def _result(output: str) -> str:
    return json.dumps({"output": output, "exit_code": 0, "error": None})


def call_events(cid: str, effect: Mapping[str, Any]) -> list[dict[str, Any]]:
    injected = {"command": COMMANDS.get(cid, f"echo {cid}")}
    final = {"command": effect.get("final_command", injected["command"])}
    ref = f"pgcall_{cid}"
    if "block" in effect:
        return [{"ev": "pre_tool_call", "tool": "terminal", "args": injected, "tool_call_id": ref},
                {"ev": "post_tool_call", "tool": "terminal", "args": final, "status": "blocked",
                 "error_type": "plugin_block", "error_message": effect["block"],
                 "result": json.dumps({"error": effect["block"]}), "tool_call_id": ref}]
    raw = effect.get("terminal_out", OUTPUTS.get(cid, f"{cid}\n"))
    tool_out = effect.get("tool_out", raw[:120] if effect.get("core_truncate") else raw)
    delivered = effect.get("delivered", _result(tool_out))
    return [{"ev": "pre_tool_call", "tool": "terminal", "args": injected, "tool_call_id": ref},
            {"ev": "terminal_raw", "command": final["command"], "output": raw, "returncode": 0, "tool_call_id": ref},
            {"ev": "tool_raw", "tool": "terminal", "args": final, "result": _result(tool_out), "tool_call_id": ref},
            {"ev": "post_tool_call", "tool": "terminal", "args": final, "status": effect.get("status", "ok"),
             "error_type": None, "error_message": effect.get("error_message"), "result": delivered, "tool_call_id": ref}]


def column(name: str, plugins: tuple[str, ...], effects: Mapping[str, Mapping[str, Any]],
           ids: Sequence[str] = IDS, loaded: Sequence[str] | None = None) -> matrix.ColumnRun:
    events = [{"ev": "activated", "calls": len(ids)},
              {"ev": "plugins_loaded", "expected": list(plugins),
               "loaded": list(plugins if loaded is None else loaded)}]
    for cid in ids:
        events += call_events(cid, {"core_truncate": cid == "TC05", **effects.get(cid, {})})
    events.append({"ev": "done", "steps": len(ids)})
    return matrix.column_from_events(name, plugins, plan(ids), events, SANDBOX, URL, [], None, 1.0)


def fixture_like_matrix() -> matrix.Matrix:
    guard = {"TC02": {"block": "fx_guard: command carries the guard marker"}}
    rewrite = {"TC03": {"final_command": "echo PG_GUARD_MARKER TC03", "terminal_out": "PG_GUARD_MARKER TC03\n"}}
    trimmed = LOG.replace("test_beta ... FAILED\n", "")
    trim = {"TC04": {"tool_out": trimmed}}
    columns = [column("baseline", (), {}), column("solo:fx_guard", ("fx_guard",), guard),
               column("solo:fx_rewriter", ("fx_rewriter",), rewrite), column("solo:fx_trimmer", ("fx_trimmer",), trim),
               column("stack", ("fx_guard", "fx_rewriter", "fx_trimmer"), {**guard, **rewrite, **trim})]
    xc_plan = [{"id": "XC-TC03", "call_ref": "pgcall_XC-TC03", "tool": "terminal",
                "args": {"command": "echo PG_GUARD_MARKER TC03"}}]
    blocked = [{"ev": "activated", "calls": 1}, {"ev": "plugins_loaded", "loaded": ["fx_guard"]}, {"ev": "done"},
               {"ev": "pre_tool_call", "tool": "terminal", "args": xc_plan[0]["args"], "tool_call_id": "pgcall_XC-TC03"},
               {"ev": "post_tool_call", "tool": "terminal", "args": xc_plan[0]["args"], "status": "blocked",
                "error_type": "plugin_block", "error_message": "fx_guard: command carries the guard marker",
                "result": "{}", "tool_call_id": "pgcall_XC-TC03"}]
    cross = {"fx_guard": matrix.column_from_events("crosscheck:fx_guard", ("fx_guard",), xc_plan, blocked,
                                                   SANDBOX, URL, [], None, 1.0)}
    infos = [matrix.CallInfo(i, "terminal", f"subject {i}", f"purpose {i}") for i in IDS]
    return matrix.build_matrix(columns, column("baseline-repeat", (), {}), cross, infos, "v0.23.1 @ 3b4215785882",
                               "2026-11-11T00:00:00+00:00", 9.0)
