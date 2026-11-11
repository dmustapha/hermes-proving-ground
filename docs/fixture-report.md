# PROVING GROUND · a wiring test for your plugin stack

**Observed on 10 synthetic inputs in a sandbox.** 10 test calls · 3 plugins · 3 distinct effects recorded · 1 cross-plugin finding

## Drill conditions
- model: NOT CALLED (scripted turn: 61 llm_execution steps answered by PROVING GROUND, 0 provider calls)
- provider: none (closed loopback 127.0.0.1:9)
- approvals: mode manual, single_query_mode deny, cron_mode deny, unattended_mode deny (headless child: every approval is denied)
- credentials: no provider keys in the child env (allow-list); no credential-shaped files or settings found in the copied plugins
- network seen inside the child Hermes process (not its subprocesses): 120 connects, 0 outside loopback
- sandbox: one temporary directory per column, deleted after the run (cwd and HOME only, not an OS sandbox)
- Hermes: v0.23.1 @ 3b4215785882
- baseline = Hermes core with only PROVING GROUND enabled (bundled plugins not enumerated)
- load order given to the child (requires_plugins first, then alphabetical): fx_guard, fx_rewriter, fx_trimmer
- coverage: executed: 3 · unavailable (requires_env): 0 · dependency-skipped / not a directory plugin: 0 · load-failed: 0
- cleanup: 0 drill processes, 0 drill roots left after the run
- wall time: 28.9s · started 2026-11-11T09:45:36+00:00

## Matrix

| test call | baseline (no plugins) | fx_guard | fx_rewriter | fx_trimmer | FULL STACK |
|---|---|---|---|---|---|
| TC01 plain command | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH |
| TC02 guard-marked command | PASSED THROUGH | BLOCKED | PASSED THROUGH | PASSED THROUGH | BLOCKED |
| TC03 rewrite candidate | PASSED THROUGH | PASSED THROUGH | REWROTE | PASSED THROUGH | REWROTE |
| TC04 test log with a FAILED line | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH | RESULT CHANGED | RESULT CHANGED |
| TC05 long output | RESULT CHANGED (core) | RESULT CHANGED (core) | RESULT CHANGED (core) | RESULT CHANGED (core) | RESULT CHANGED (core) |
| TC06 file write | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH |
| TC07 file read with canary | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH |
| TC08 file patch | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH |
| TC09 loopback fetch | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH | PASSED THROUGH |
| TC10 approval-worthy shape | APPROVAL-GATED (core) | APPROVAL-GATED (core) | APPROVAL-GATED (core) | APPROVAL-GATED (core) | APPROVAL-GATED (core) |

## Findings and attribution
- 10 test calls · 3 plugins · 3 distinct effects recorded · 1 cross-plugin finding
- In plain words: fx_guard approved a command it never saw, and that command ran.
- REWRITE_UNSEEN_BY: fx_rewriter rewrote TC03 after every pre-call check had run. fx_guard blocks the rewritten call when it sees it directly; in the stack it only saw the original, and the rewritten call ran.
- attribution TC02 BLOCKED: solo-match — observed in one solo run: fx_guard (stateful plugins may differ between runs)
- attribution TC03 REWROTE: solo-match — observed in one solo run: fx_rewriter (stateful plugins may differ between runs)
- attribution TC04 RESULT CHANGED: solo-match — observed in one solo run: fx_trimmer (stateful plugins may differ between runs)
- attribution TC05 RESULT CHANGED: core — the zero-plugin baseline shows the same effect
- attribution TC10 APPROVAL-GATED: core — the zero-plugin baseline shows the same effect
- This report describes what your plugins did to these specific test calls. It does not grade your stack, and results depend on each plugin's configuration.
- Detail: hermes provingground show <CALL> --column <COLUMN>
