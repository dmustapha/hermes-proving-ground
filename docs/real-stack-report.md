# PROVING GROUND · a wiring test for your plugin stack

**Observed on 24 synthetic inputs in a sandbox.** 24 test calls · 5 plugins · 22 distinct effects recorded · 2 cross-plugin findings

## Findings
- MASKED_IN_STACK: readonly-guard BLOCKED TC05 in the full stack, so what other plugins do to it on their own does not reach the model: hermes-detox RESULT CHANGED.
- MASKED_IN_STACK: readonly-guard BLOCKED TC12 in the full stack, so what other plugins do to it on their own does not reach the model: progress-squash RESULT CHANGED.

## Matrix

| test call | baseline (no plugins) | canary-trap | hermes-detox | progress-squash | readonly-guard | safe-swap | FULL STACK |
|---|---|---|---|---|---|---|---|
| TC01 plain command | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | BLOCKED | PASSED THROUGH (ran, no change) | BLOCKED |
| TC02 guard-marked command | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | BLOCKED | PASSED THROUGH (ran, no change) | BLOCKED |
| TC03 rewrite candidate | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | BLOCKED | PASSED THROUGH (ran, no change) | BLOCKED |
| TC04 test log with a FAILED line | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | BLOCKED | PASSED THROUGH (ran, no change) | BLOCKED |
| TC05 long output | RESULT CHANGED (core) | RESULT CHANGED (core) | RESULT CHANGED (core + plugin) | RESULT CHANGED (core) | BLOCKED | RESULT CHANGED (core) | BLOCKED · masks a solo effect |
| TC06 file write | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (not invoked) | BLOCKED | PASSED THROUGH (ran, no change) | BLOCKED |
| TC07 file read with canary | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (not invoked) | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) |
| TC08 file patch | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (not invoked) | BLOCKED | PASSED THROUGH (ran, no change) | BLOCKED |
| TC09 loopback fetch | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | BLOCKED | PASSED THROUGH (ran, no change) | BLOCKED |
| TC10 approval-worthy shape | APPROVAL-GATED (core) | APPROVAL-GATED (core) | APPROVAL-GATED (core) | APPROVAL-GATED (core) | BLOCKED | APPROVAL-GATED (core) | BLOCKED |
| TC11 ANSI colour output | RESULT CHANGED (core) | RESULT CHANGED (core) | RESULT CHANGED (core) | RESULT CHANGED (core) | BLOCKED | RESULT CHANGED (core) | BLOCKED |
| TC12 carriage-return progress bar | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | RESULT CHANGED | BLOCKED | PASSED THROUGH (ran, no change) | BLOCKED · masks a solo effect |
| TC13 duplicate lines | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | BLOCKED | PASSED THROUGH (ran, no change) | BLOCKED |
| TC14 base64-looking blob | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | BLOCKED | PASSED THROUGH (ran, no change) | BLOCKED |
| TC15 minified JSON blob | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | BLOCKED | PASSED THROUGH (ran, no change) | BLOCKED |
| TC16 credential-shaped output (documented fake) | RESULT CHANGED (core) | RESULT CHANGED (core) | RESULT CHANGED (core) | RESULT CHANGED (core) | BLOCKED | RESULT CHANGED (core) | BLOCKED |
| TC17 env file read (synthetic value) | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (not invoked) | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) |
| TC18 ssh key file read (synthetic text) | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (not invoked) | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) |
| TC19 shell startup file write | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (not invoked) | BLOCKED | PASSED THROUGH (ran, no change) | BLOCKED |
| TC20 config file write | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (not invoked) | BLOCKED | PASSED THROUGH (ran, no change) | BLOCKED |
| TC21 code execution (probe, never run) | NOT EXECUTED (probe) | NOT EXECUTED (probe) | NOT EXECUTED (probe) | NOT EXECUTED (probe) | BLOCKED | NOT EXECUTED (probe) | BLOCKED |
| TC22 subagent delegation (probe, never run) | NOT EXECUTED (probe) | NOT EXECUTED (probe) | NOT EXECUTED (probe) | NOT EXECUTED (probe) | BLOCKED | NOT EXECUTED (probe) | BLOCKED |
| TC23 web page extract (probe, never run) | NOT EXECUTED (probe) | NOT EXECUTED (probe) | NOT EXECUTED (probe) | NOT EXECUTED (probe) | NOT EXECUTED (probe) | NOT EXECUTED (probe) | NOT EXECUTED (probe) |
| TC24 memory write (probe, never run) | NOT EXECUTED (probe) | NOT EXECUTED (probe) | NOT EXECUTED (probe) | NOT EXECUTED (probe) | BLOCKED | NOT EXECUTED (probe) | BLOCKED |

## Attribution and notes
- attribution TC01 BLOCKED: solo-match — observed in one solo run: readonly-guard (stateful plugins may differ between runs)
- attribution TC02 BLOCKED: solo-match — observed in one solo run: readonly-guard (stateful plugins may differ between runs)
- attribution TC03 BLOCKED: solo-match — observed in one solo run: readonly-guard (stateful plugins may differ between runs)
- attribution TC04 BLOCKED: solo-match — observed in one solo run: readonly-guard (stateful plugins may differ between runs)
- attribution TC05 BLOCKED: solo-match — observed in one solo run: readonly-guard (stateful plugins may differ between runs)
- attribution TC06 BLOCKED: solo-match — observed in one solo run: readonly-guard (stateful plugins may differ between runs)
- attribution TC08 BLOCKED: solo-match — observed in one solo run: readonly-guard (stateful plugins may differ between runs)
- attribution TC09 BLOCKED: solo-match — observed in one solo run: readonly-guard (stateful plugins may differ between runs)
- attribution TC10 BLOCKED: solo-match — observed in one solo run: readonly-guard (stateful plugins may differ between runs)
- attribution TC11 BLOCKED: solo-match — observed in one solo run: readonly-guard (stateful plugins may differ between runs)
- attribution TC12 BLOCKED: solo-match — observed in one solo run: readonly-guard (stateful plugins may differ between runs)
- attribution TC13 BLOCKED: solo-match — observed in one solo run: readonly-guard (stateful plugins may differ between runs)
- attribution TC14 BLOCKED: solo-match — observed in one solo run: readonly-guard (stateful plugins may differ between runs)
- attribution TC15 BLOCKED: solo-match — observed in one solo run: readonly-guard (stateful plugins may differ between runs)
- attribution TC16 BLOCKED: solo-match — observed in one solo run: readonly-guard (stateful plugins may differ between runs)
- attribution TC19 BLOCKED: solo-match — observed in one solo run: readonly-guard (stateful plugins may differ between runs)
- attribution TC20 BLOCKED: solo-match — observed in one solo run: readonly-guard (stateful plugins may differ between runs)
- attribution TC21 BLOCKED: solo-match — observed in one solo run: readonly-guard (stateful plugins may differ between runs)
- attribution TC22 BLOCKED: solo-match — observed in one solo run: readonly-guard (stateful plugins may differ between runs)
- attribution TC24 BLOCKED: solo-match — observed in one solo run: readonly-guard (stateful plugins may differ between runs)
- coverage of canary-trap: hooks registered: pre_llm_call, pre_tool_call, transform_llm_output; fired: pre_llm_call, pre_tool_call, transform_llm_output; never fired in this drill (UNTESTED): none; registered commands: canary (not exercised)
- coverage of hermes-detox: hooks registered: transform_tool_result, transform_terminal_output, on_session_reset, post_compaction, on_session_delete; fired: transform_tool_result, transform_terminal_output; never fired in this drill (UNTESTED): on_session_reset, post_compaction, on_session_delete; registered tools: detox_inspect, detox_search, detox_status (not exercised); registered commands: detox (not exercised)
- coverage of progress-squash: hooks registered: transform_terminal_output; fired: transform_terminal_output; never fired in this drill (UNTESTED): none
- coverage of readonly-guard: hooks registered: pre_tool_call, pre_llm_call; fired: pre_tool_call, pre_llm_call; never fired in this drill (UNTESTED): none; registered commands: readonly (not exercised)
- coverage of safe-swap: hooks registered: pre_tool_call, post_tool_call, transform_tool_result; fired: pre_tool_call, post_tool_call, transform_tool_result; never fired in this drill (UNTESTED): none; registered commands: swaps, unswap (not exercised)
- This report describes what your plugins did to these specific test calls. It does not grade your stack, and results depend on each plugin's configuration.
- Detail: hermes provingground show <CALL> --column <COLUMN>

## Drill conditions
- model: NOT CALLED (scripted turn: 175 llm_execution steps answered by PROVING GROUND, 0 provider calls)
- provider: none (closed loopback 127.0.0.1:9)
- approvals: mode manual, single_query_mode deny, cron_mode deny, unattended_mode deny (headless child: every approval is denied)
- credentials: no provider keys in the child env (allow-list); no credential-shaped files or settings found in the copied plugins
- network seen inside the child Hermes process (not its subprocesses): 119 connects, 0 outside loopback
- sandbox: one temporary directory per column, deleted after the run (cwd and HOME only, not an OS sandbox)
- Hermes: v0.23.1 @ 3b4215785882
- baseline = Hermes core with only PROVING GROUND enabled (bundled plugins not enumerated)
- load order given to the child (requires_plugins first, then alphabetical): canary-trap, hermes-detox, progress-squash, readonly-guard, safe-swap
- coverage: executed: 5 · unavailable (requires_env): 0 · dependency-skipped / not a directory plugin: 0 · load-failed: 0
- cleanup: 0 drill processes, 0 drill roots left after the run
- wall time: 19.5s · started 2026-11-11T18:32:41+00:00
