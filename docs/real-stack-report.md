# PROVING GROUND · a wiring test for your plugin stack

**Observed on 10 synthetic inputs in a sandbox.** 10 test calls · 5 plugins · 19 behaviours recorded · 1 cross-plugin finding

## Drill conditions
- model: NOT CALLED (scripted turn: 77 llm_execution steps answered by PROVING GROUND, 0 provider calls)
- provider: none (closed loopback 127.0.0.1:9)
- approvals: mode manual, single_query_mode deny, cron_mode deny, unattended_mode deny (headless child: every approval is denied)
- credentials: no provider keys in the child env (allow-list); no credential-shaped files or settings found in the copied plugins
- network seen inside the child Hermes process (not its subprocesses): 105 connects, 0 outside loopback
- sandbox: one temporary directory per column, deleted after the run (cwd and HOME only, not an OS sandbox)
- Hermes: v0.23.1 @ 3b4215785882
- baseline = Hermes core with only PROVING GROUND enabled (bundled plugins not enumerated)
- load order given to the child (requires_plugins first, then alphabetical): canary-trap, hermes-detox, progress-squash, readonly-guard, safe-swap
- coverage: executed: 5 · unavailable (requires_env): 0 · dependency-skipped / not a directory plugin: 0 · load-failed: 0
- cleanup: 0 drill processes, 0 drill roots left after the run
- wall time: 21.4s · started 2026-11-11T03:28:35+00:00

## Matrix

| test call | baseline (no plugins) | canary-trap | hermes-detox | progress-squash | readonly-guard | safe-swap | FULL STACK |
|---|---|---|---|---|---|---|---|
| TC01 plain command | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH | BLOCKED | PASSED THROUGH | BLOCKED |
| TC02 guard-marked command | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH | BLOCKED | PASSED THROUGH | BLOCKED |
| TC03 rewrite candidate | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH | BLOCKED | PASSED THROUGH | BLOCKED |
| TC04 test log with a FAILED line | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH | BLOCKED | PASSED THROUGH | BLOCKED |
| TC05 long output | RESULT CHANGED (core) | PASSED THROUGH | RESULT CHANGED | PASSED THROUGH | BLOCKED | PASSED THROUGH | BLOCKED |
| TC06 file write | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH | BLOCKED | PASSED THROUGH | BLOCKED |
| TC07 file read with canary | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH |
| TC08 file patch | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH | BLOCKED | PASSED THROUGH | BLOCKED |
| TC09 loopback fetch | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH | BLOCKED | PASSED THROUGH | BLOCKED |
| TC10 approval-worthy shape | APPROVAL-GATED (core) | APPROVAL-GATED (core) | APPROVAL-GATED (core) | APPROVAL-GATED (core) | BLOCKED | APPROVAL-GATED (core) | BLOCKED |

## Findings and attribution
- 10 test calls · 5 plugins · 19 behaviours recorded · 1 cross-plugin finding
- MASKED_IN_STACK: hermes-detox RESULT CHANGED TC05 on its own; in the full stack that effect does not reach the model.
- attribution TC01 BLOCKED: solo-match — observed in one solo run: readonly-guard (stateful plugins may differ between runs)
- attribution TC02 BLOCKED: solo-match — observed in one solo run: readonly-guard (stateful plugins may differ between runs)
- attribution TC03 BLOCKED: solo-match — observed in one solo run: readonly-guard (stateful plugins may differ between runs)
- attribution TC04 BLOCKED: solo-match — observed in one solo run: readonly-guard (stateful plugins may differ between runs)
- attribution TC05 BLOCKED: solo-match — observed in one solo run: readonly-guard (stateful plugins may differ between runs)
- attribution TC06 BLOCKED: solo-match — observed in one solo run: readonly-guard (stateful plugins may differ between runs)
- attribution TC08 BLOCKED: solo-match — observed in one solo run: readonly-guard (stateful plugins may differ between runs)
- attribution TC09 BLOCKED: solo-match — observed in one solo run: readonly-guard (stateful plugins may differ between runs)
- attribution TC10 BLOCKED: solo-match — observed in one solo run: readonly-guard (stateful plugins may differ between runs)
- This report describes what your plugins did to these specific test calls. It does not grade your stack, and results depend on each plugin's configuration.
- Detail: hermes provingground show <CALL> --column <COLUMN>
