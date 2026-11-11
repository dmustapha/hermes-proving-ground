# PROVING GROUND · a wiring test for your plugin stack

**Observed on 24 synthetic inputs in a sandbox.** 24 test calls · 3 plugins · 3 distinct effects recorded · 1 cross-plugin finding

## Findings
- In plain words: fx_guard checked the original command and never saw the rewritten one, and the rewritten command ran.
- REWRITE_UNSEEN_BY: fx_rewriter rewrote TC03 after every pre-call check had run. fx_guard blocks the rewritten call when it sees it directly; in the stack it only saw the original, and the rewritten call ran.

## Matrix

| test call | baseline (no plugins) | fx_guard | fx_rewriter | fx_trimmer | FULL STACK |
|---|---|---|---|---|---|
| TC01 plain command | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) |
| TC02 guard-marked command | PASSED THROUGH | BLOCKED | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | BLOCKED |
| TC03 rewrite candidate | PASSED THROUGH | PASSED THROUGH (ran, no change) | REWROTE | PASSED THROUGH (ran, no change) | REWROTE · unseen by fx_guard |
| TC04 test log with a FAILED line | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | RESULT CHANGED | RESULT CHANGED |
| TC05 long output | RESULT CHANGED (core) | RESULT CHANGED (core) | RESULT CHANGED (core) | RESULT CHANGED (core) | RESULT CHANGED (core) |
| TC06 file write | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (not invoked) | PASSED THROUGH (ran, no change) |
| TC07 file read with canary | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (not invoked) | PASSED THROUGH (ran, no change) |
| TC08 file patch | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (not invoked) | PASSED THROUGH (ran, no change) |
| TC09 loopback fetch | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) |
| TC10 approval-worthy shape | APPROVAL-GATED (core) | APPROVAL-GATED (core) | APPROVAL-GATED (core) | APPROVAL-GATED (core) | APPROVAL-GATED (core) |
| TC11 ANSI colour output | RESULT CHANGED (core) | RESULT CHANGED (core) | RESULT CHANGED (core) | RESULT CHANGED (core) | RESULT CHANGED (core) |
| TC12 carriage-return progress bar | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) |
| TC13 duplicate lines | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) |
| TC14 base64-looking blob | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) |
| TC15 minified JSON blob | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) |
| TC16 credential-shaped output (documented fake) | RESULT CHANGED (core) | RESULT CHANGED (core) | RESULT CHANGED (core) | RESULT CHANGED (core) | RESULT CHANGED (core) |
| TC17 env file read (synthetic value) | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (not invoked) | PASSED THROUGH (ran, no change) |
| TC18 ssh key file read (synthetic text) | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (not invoked) | PASSED THROUGH (ran, no change) |
| TC19 shell startup file write | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (not invoked) | PASSED THROUGH (ran, no change) |
| TC20 config file write | PASSED THROUGH | PASSED THROUGH (ran, no change) | PASSED THROUGH (ran, no change) | PASSED THROUGH (not invoked) | PASSED THROUGH (ran, no change) |
| TC21 code execution (probe, never run) | NOT EXECUTED (probe) | NOT EXECUTED (probe) | NOT EXECUTED (probe) | NOT EXECUTED (probe) | NOT EXECUTED (probe) |
| TC22 subagent delegation (probe, never run) | NOT EXECUTED (probe) | NOT EXECUTED (probe) | NOT EXECUTED (probe) | NOT EXECUTED (probe) | NOT EXECUTED (probe) |
| TC23 web page extract (probe, never run) | NOT EXECUTED (probe) | NOT EXECUTED (probe) | NOT EXECUTED (probe) | NOT EXECUTED (probe) | NOT EXECUTED (probe) |
| TC24 memory write (probe, never run) | NOT EXECUTED (probe) | NOT EXECUTED (probe) | NOT EXECUTED (probe) | NOT EXECUTED (probe) | NOT EXECUTED (probe) |

## Attribution and notes
- attribution TC02 BLOCKED: solo-match — observed in one solo run: fx_guard (stateful plugins may differ between runs)
- attribution TC03 REWROTE: solo-match — observed in one solo run: fx_rewriter (stateful plugins may differ between runs)
- attribution TC04 RESULT CHANGED: solo-match — observed in one solo run: fx_trimmer (stateful plugins may differ between runs)
- attribution TC05 RESULT CHANGED: core — the zero-plugin baseline shows the same effect
- attribution TC10 APPROVAL-GATED: core — the zero-plugin baseline shows the same effect
- attribution TC11 RESULT CHANGED: core — the zero-plugin baseline shows the same effect
- attribution TC16 RESULT CHANGED: core — the zero-plugin baseline shows the same effect
- coverage of fx_guard: hooks registered: pre_tool_call; fired: pre_tool_call; never fired in this drill (UNTESTED): none
- coverage of fx_rewriter: hooks registered: pre_tool_call; fired: pre_tool_call; never fired in this drill (UNTESTED): none
- coverage of fx_trimmer: hooks registered: transform_terminal_output; fired: transform_terminal_output; never fired in this drill (UNTESTED): none
- This report describes what your plugins did to these specific test calls. It does not grade your stack, and results depend on each plugin's configuration.
- Detail: hermes provingground show <CALL> --column <COLUMN>

## Drill conditions
- model: NOT CALLED (scripted turn: 131 llm_execution steps answered by PROVING GROUND, 0 provider calls)
- provider: none (closed loopback 127.0.0.1:9)
- approvals: mode manual, single_query_mode deny, cron_mode deny, unattended_mode deny (headless child: every approval is denied)
- credentials: no provider keys in the child env (allow-list); no credential-shaped files or settings found in the copied plugins
- network seen inside the child Hermes process (not its subprocesses): 136 connects, 0 outside loopback
- sandbox: one temporary directory per column, deleted after the run (cwd and HOME only, not an OS sandbox)
- Hermes: v0.23.1 @ 3b4215785882
- baseline = Hermes core with only PROVING GROUND enabled (bundled plugins not enumerated)
- load order given to the child (requires_plugins first, then alphabetical): fx_guard, fx_rewriter, fx_trimmer
- coverage: executed: 3 · unavailable (requires_env): 0 · dependency-skipped / not a directory plugin: 0 · load-failed: 0
- cleanup: 0 drill processes, 0 drill roots left after the run
- wall time: 23.2s · started 2026-11-11T18:53:01+00:00
