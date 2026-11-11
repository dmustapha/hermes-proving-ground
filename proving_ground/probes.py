# File: proving_ground/probes.py
# Pure logic over the child's events.jsonl (observer.py). The turn-stop gate: after a turn's file edits Hermes calls
# pre_verify, and a hook returning {"action": "continue", "message"} keeps the turn going one more model step
# (agent/turn_stop_gates.py:53-76, :190-199; hermes_cli/plugins.py:1239-1257). PROVING GROUND's tap returns None, so
# every pre_verify event is the turn reaching its end, and every scripted final reply logs a "done" event
# (injector.scripted_reply): two finals in one turn mean the turn was extended.
"""Block-only probes and the pre_verify turn-stop gate: notes and observation helpers for matrix.py."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Mapping, Sequence

# Block-only probes: tools outside the drill allow-list that a drill offers the child only so other plugins'
# pre_tool_call hooks see a call to them. PROVING GROUND's own pre_tool_call blocks every probe call, so none runs:
# any block stops dispatch (agent/tool_executor.py:702-735), and every pre_tool_call callback runs before the first
# block in load order wins (hermes_cli/plugins.py:1132-1160). Tool -> its Hermes toolset (toolsets.py:72,125,151-152).
PROBE_TOOLSETS: Mapping[str, str] = {"execute_code": "code_execution", "delegate_task": "delegation",
                                     "web_extract": "web", "memory": "memory"}
PROBE_TOOLS = frozenset(PROBE_TOOLSETS)
PROBE_BLOCK_MESSAGE = "PROVINGGROUND: block-only probe, not executed (no plugin blocked it first)"
EDIT_TOOLS = frozenset({"write_file", "patch"})
NOT_EXECUTED_NOTE = ("block-only probe: PROVING GROUND blocked it, so it never ran; no plugin blocked it first "
                     "(PROVING GROUND loads last, so only a requires_plugins subject could block after it)")
NOT_OFFERED_NOTE = ("block-only probe not offered in this child: Hermes rejects a tool it does not offer before any "
                    "plugin hook runs (agent/turn_tool_round.py:60,122), for example a web tool with no web backend")


def gate_note(gate: str) -> str:
    """The cell note for a probe's gate outcome; empty for every other outcome."""
    return {"NOT_EXECUTED": NOT_EXECUTED_NOTE, "NOT_APPLICABLE": NOT_OFFERED_NOTE}.get(str(getattr(gate, "value", gate)), "")


def turn_extensions(events: Sequence[Mapping[str, Any]]) -> int:
    """How many extra final replies the turn needed after its edits reached the stop gate: 0 without a pre_verify."""
    if not any(e.get("ev") == "pre_verify" for e in events):
        return 0
    return max(0, sum(1 for e in events if e.get("ev") == "done") - 1)


def changed_paths(events: Sequence[Mapping[str, Any]]) -> frozenset[str]:
    return frozenset(str(p) for e in events if e.get("ev") == "pre_verify" for p in e.get("changed_paths") or ())


Normalize = Callable[[str, str, str], str]  # matrix.normalize_text(text, sandbox, recorder_url)


def _edited_paths(events: Sequence[Mapping[str, Any]], sandbox: str, recorder_url: str,
                  normalize: Normalize) -> frozenset[str]:
    """Each changed path as the drill names it ({SANDBOX}/...). macOS resolves /tmp to /private/tmp, so the
    sandbox is matched in both spellings."""
    bare = sandbox.removeprefix("/private")
    roots = (sandbox, bare, "/private" + bare)
    found = set()
    for path in changed_paths(events):
        for root in roots:
            if path == root or path.startswith(root + "/"):
                path = "{SANDBOX}" + path[len(root):]
        found.add(normalize(path, sandbox, recorder_url))
    return frozenset(found)


def with_turn_extensions(observations: Mapping[str, Any], events: Sequence[Mapping[str, Any]], sandbox: str,
                         recorder_url: str, normalize: Normalize) -> dict[str, Any]:
    """Mark each file-edit observation whose final path is among the gate's changed paths with the turn's extra
    final replies, so a pre_verify nudge shows on the edits that triggered it."""
    extra = turn_extensions(events)
    if not extra:
        return dict(observations)
    edited = _edited_paths(events, sandbox, recorder_url, normalize)
    return {cid: replace(obs, turn_extended=extra)
            if obs.tool in EDIT_TOOLS and obs.ran and str(obs.final_args.get("path")) in edited else obs
            for cid, obs in observations.items()}
