# PROVING GROUND
xray tests one plugin for malice; crash-test tests the agent under tool faults; policy-backtester tests one rule against past calls. PROVING GROUND tests what your *installed stack* does to known inputs, through the real hook chain, with the model never called.

**A wiring test for your plugin stack.**

![PROVING GROUND: the fixture matrix, ten test calls by five columns](docs/banner.png)

**Your guard approved a command it never saw, and that command ran.** That is what the bundled demo drill shows. Hermes merges every `pre_tool_call` rewrite after all the checks have already looked at the original arguments, so a guard and a rewriter that each behave correctly on their own can still let through a call the guard would have stopped. You cannot see this from inside one plugin. PROVING GROUND sends ten known, harmless test calls through Hermes's real hook chain: once with no plugins, once per plugin alone, and once with the full stack. Each cell of the matrix says what happened.

## Safety model
> - **Throwaway child Hermes homes.** Every column runs in its own `hermes chat -q` child with a temporary Hermes home, home directory and working directory. This is not an OS sandbox: subject plugins run with your own file permissions, and a call rewritten to land outside the temporary directory is detected after it ran, not prevented, and its column is stopped.
> - **Approvals stay on and are always denied.** The child's config forces approvals to manual, with single-query, cron and unattended modes on deny. PROVING GROUND never answers or approves one.
> - **No provider keys, a closed loopback model, a loopback-only recorder.** The child env is an allow-list without provider keys, its model endpoint is a closed port on 127.0.0.1, and the only listener PROVING GROUND opens is on 127.0.0.1.
> - **Rule 12 (spawned children).** The child is a single-query `hermes chat -q`, but its config forces approvals to manual with single-query, cron and unattended modes all on deny, it never gets `--yolo`/`-z`/`--oneshot` or `HERMES_YOLO_MODE`, and the only commands it runs are the listed calls in `testcalls.yaml` (plus your own `--calls`, which must pass a closed grammar). Anything approval-worthy is denied by Hermes itself.

## Quickstart (about 3 minutes on a first run)
```bash
hermes plugins install proving-ground
hermes provingground warm                      # one time: Hermes's own installer fetches its runtime (~650 MB)
hermes provingground --fixtures                # the bundled three-plugin demo stack
hermes provingground show TC04 --column stack  # one cell, step by step
hermes provingground --fixtures --html report.html
```
Before the catalog entry is merged, install from the Git URL (`hermes plugins install dmustapha/hermes-proving-ground`) with `--ref` set to the commit SHA pinned in the catalog PR.

Run `hermes provingground` without `--fixtures` to test the directory plugins enabled in your own Hermes home. Verified on Hermes v0.23.1, macOS.

## The fixture drill
The committed report [docs/fixture-report.md](docs/fixture-report.md) (JSON: [docs/fixture-report.json](docs/fixture-report.json), HTML: [docs/fixture-report.html](docs/fixture-report.html)) is one run of `hermes provingground --fixtures` on Hermes v0.23.1: `10 test calls · 3 plugins · 6 behaviours recorded · 1 cross-plugin finding`, `model: NOT CALLED (scripted turn: 61 llm_execution steps answered by PROVING GROUND, 0 provider calls)`, 18.4 s on a warm cache. The three demo plugins are the author's own fixtures (`fixtures/`), written to show one Hermes merge rule each.

![The fixture matrix in the terminal](docs/matrix.png)

TC03 in the full stack: `fx_rewriter` changed the command after every pre-call check had run. `fx_guard` blocks the rewritten command when it sees it directly (its solo cross-check), but in the stack it only saw the original, and the rewritten command ran. The report states this as `REWRITE_UNSEEN_BY`.

TC04 in the full stack: the tool printed a three-line test log with one FAILED line, and the model would have received it without that line. One solo run names `fx_trimmer`.

![TC04 x FULL STACK cell detail in the HTML report](docs/cell-detail.png)

## What the outcomes mean
| Outcome | In plain words |
|---|---|
| BLOCKED | A plugin stopped the call; the tool did not run. |
| REWROTE | A plugin changed the arguments; the changed call ran. |
| RESULT CHANGED | The call ran, but what reached the model differs from the zero-plugin baseline. The cell names the hop (`terminal_output` or `tool_result`). |
| APPROVAL-GATED | The call needed approval and Hermes denied it headless. `(core)` when the baseline does the same. |
| PASSED THROUGH | Nothing in the stack changed this call or its result. |
| ESCAPED | A rewritten call ran outside the temporary directory. Detected after it ran, not prevented; the column is stopped. |
| ERRORED | A plugin hook raised, or the harness stopped the column (for example a timeout). |
| LOAD FAILED | The plugin was not confirmed loaded in its child, so its cells are never shown as PASSED THROUGH. |
| NOT RUN | The call never reached execution in this column. |
| N/A | The plugin declares `requires_env`; the child has no credentials, so it is not run. |

`(core)` marks Hermes's own behaviour, such as its head+tail truncation of long output (TC05) or its headless approval deny (TC10). It is never attributed to a plugin. Every report prints a coverage line, for example `executed: 3 · unavailable (requires_env): 0 · dependency-skipped / not a directory plugin: 0 · load-failed: 0`, so an all-quiet matrix never reads as full coverage.

## How attribution works
Each drill runs four kinds of column: a **baseline** (Hermes with only PROVING GROUND enabled), a **solo** column per plugin, the **full stack**, and a **cross-check** that replays a rewritten call directly to each guard. A full-stack effect is attributed to a plugin when that plugin's solo column shows the same effect: "observed in one solo run" (stateful plugins may differ between runs). Effects that no solo run shows are labelled `interaction`. Hook order is never used for attribution. Rewrites and escapes are detected after they ran, not prevented.

The Hermes v0.23.1 composition rules this depends on:

| Rule | Source | What PROVING GROUND does with it |
|---|---|---|
| every `pre_tool_call` callback gets the same original args; `modify` directives merge after all ran; block beats modify | `hermes_cli/plugins.py:1132-1159` | REWROTE and the REWRITE_UNSEEN_BY cross-check |
| a crashing fail-closed `pre_tool_call` becomes a block whose message starts `pre_tool_call plugin callback <name> raised` | `hermes_cli/plugins_dispatch.py:89-95` | ERRORED, never BLOCKED |
| `transform_terminal_output` callbacks all get the raw output; the first string wins | `tools/terminal_tool_result.py:136-147` | hop `terminal_output` |
| core truncates terminal output to a head+tail window before `transform_tool_result` | `tools/terminal_tool_result.py:193`, `tools/tool_output_limits.py:13` | `(core)` in the baseline; never blamed |
| `transform_tool_result` callbacks all get the same result; the first string wins | `model_tools.py:870-887` | hop `tool_result`; `multiple-candidates` |
| `post_tool_call` sees the final effective args after execution | `model_tools.py:697-720` | ESCAPED is detected after it ran |
| headless single-query approvals deny instantly | `tools/approval.py:700-717` | APPROVAL-GATED (core) |
| `PluginContext.has_plugin(key)` is true only for a loaded, enabled plugin | `hermes_cli/plugins_context.py:57-63` | LOAD FAILED when false |

These rules are version-scoped. A Hermes upgrade that changes one of them changes the matrix, which is what contract mode is for.

## Test your own guard
Add your own trigger as a user call (ids start with `U`) and declare what each column should show. User calls go through the same validator as the built-in ones, plus a closed call grammar, before anything is spawned.

```yaml
# my-calls.yaml
schema: provingground.testcalls.v1
calls:
  - {id: U01, tool: terminal, subject: my trigger, purpose: "My guard's trigger shape.",
     args: {command: "echo MY_GUARD_TRIGGER {CALL_ID}"}, expect: {stack: BLOCKED}}
expect:
  TC03: {stack: [PASSED_THROUGH, BLOCKED]}
```

```bash
hermes provingground --calls my-calls.yaml --check   # exit 5 when an expect: is not met
```

The bundled contract shows the loop: `hermes provingground --fixtures --without fx_rewriter --calls fixtures/contract.yaml --check` clears TC03 in the full stack and prints `contract: 1 of 1 expectations met` (exit 0).

In CI, on a machine with Hermes v0.23.1 and your plugin enabled:
```yaml
- run: hermes provingground warm
- run: hermes provingground --calls my-calls.yaml --check --json pg-report.json
```

## Cleanup you can check
```console
$ hermes provingground sweep
removed 0 stale sandbox roots
$ jq '.conditions | {leftover_processes, leftover_roots}' docs/fixture-report.json
{
  "leftover_processes": 0,
  "leftover_roots": 0
}
```
Every report records how many drill processes and drill roots were left after the run. The sweep deletes only temporary directories that carry PROVING GROUND's own ownership marker and a dead owner pid. Find your temp directory with `python3 -c 'import tempfile; print(tempfile.gettempdir())'`.

## What it found while we built it
These are facts about Hermes's plugin seams, not about any plugin. Each is written up as a Hermes issue draft (not yet filed).
- **Modify after check.** Every `pre_tool_call` check sees the original arguments; `modify` directives are merged after all of them ran (`hermes_cli/plugins.py:1132-1150`). No plugin seam sees the final arguments before execution, which is why PROVING GROUND detects escapes after they ran instead of preventing them. Issue drafted.
- **First rewriter wins.** `transform_tool_result` and `transform_terminal_output` keep the first string any callback returns (`model_tools.py:887`, `tools/terminal_tool_result.py:144-147`). When several plugins rewrite a result, only one string is kept, so PROVING GROUND reports `multiple-candidates` instead of naming which one reached the model. Issue drafted.
- **First-run guard.** A child Hermes with a redirected HOME stops at the first-run setup guard unless its config names a model provider (`hermes_cli/main.py`, `_has_any_provider_configured`). PROVING GROUND writes a closed-loopback `custom` provider into each child's config. Issue drafted (documentation).

Running PROVING GROUND on a real stack of catalog plugins also found three bugs in PROVING GROUND itself, each fixed with a test: a core approval gate hidden by a stack block was reported as a masked plugin effect (58cab27); the cell detail did not say its attribution is the full-stack one (5e39586); a plugin's change on top of core truncation was not attributed to the plugin (bcfbe4c).

<!-- A10: Dami to confirm naming before submission -->
## Optional: a real catalog stack
Five catalog plugins at their pinned catalog SHAs, installed into a separate Hermes home: canary-trap, hermes-detox, progress-squash, readonly-guard, safe-swap. One run on Hermes v0.23.1 recorded `10 test calls · 5 plugins · 19 behaviours recorded · 1 cross-plugin finding` ([docs/real-stack-report.md](docs/real-stack-report.md)).

The finding is `MASKED_IN_STACK` on TC05: alone, hermes-detox changes the long-output result; in the full stack, readonly-guard in its default read-only mode stops TC05 before the call runs, so hermes-detox's change does not reach the model. Each plugin did what its catalog entry describes. Only the combination is new information.

## Why this has to be a plugin
A prompt or a skill runs inside the model's turn. PROVING GROUND runs with the model not called: every report's drill conditions say `model: NOT CALLED`, and its observations come from real hook-chain events.
That takes `llm_execution` middleware to answer each step, `pre_tool_call`, `post_tool_call`, `transform_terminal_output` and `transform_tool_result` taps to watch every hop, and a child Hermes subprocess per column.
None of these is reachable from a prompt or a skill.

## Where it sits
As of 2026-11-11 (live `gh` search of catalog PRs, [docs/field-2026-11-11.md](docs/field-2026-11-11.md)): xray (#149414) tests one plugin, crash-test (#151102) the agent under faults, policy-backtester (#151434) one rule against past calls, benchforge (#152504) Arduino firmware. None tests the composed stack. The catalog grows daily, so this is a dated snapshot.

## Disclosure
> **Disclosure — what PROVING GROUND does on your machine**
> - **Starts Hermes child processes.** Each check runs `hermes chat -q` as a subprocess with its own temporary Hermes home, home directory and working directory, all under your system temp directory. Its tests check that your real Hermes config, sessions, memories, state database, SOUL.md and installed plugins are unchanged by a drill; reports go to `plugin-data/proving-ground/`. The children and their temporary files are deleted when the check finishes or is interrupted (SIGINT, SIGTERM, SIGHUP).
> - **Your plugins run with your permissions.** The child home is a throwaway directory, not an OS sandbox. Subject plugin code runs with your own file permissions; a call rewritten to land outside the temporary directory is detected after it ran, not prevented, and the column is stopped.
> - **Prepares a runtime cache once (network).** On first use, after saying so, one child lets Hermes's own installer download its runtime (about 650 MB) into `<temp>/provingground-cache` (mode 0700, refused if it belongs to someone else or is a symlink), exactly as it does for any new Hermes home. Later checks point every proxy at a closed local port.
> - **Finds and stops its own children.** It lists processes (`ps -axo pid,command`) and reads the environment of hermes/python processes only (`ps eww -p <pid>`) to find the ones whose `HERMES_HOME` is one of its own temporary homes, then sends them SIGTERM/SIGKILL. It deletes only temporary directories that carry its own ownership marker.
> - **Sends synthetic tool calls through your installed plugins.** Inside the child the model is never called. A fixed, readable list of ten harmless test calls (`testcalls.yaml`) goes through Hermes's normal tool path: file writes, reads and patches inside the temporary directory, and short shell commands that only print, write test markers inside it, or fetch from PROVING GROUND's own local recorder. Your plugins see these calls exactly as they would see real ones.
> - **Approvals stay on and are always denied.** The child runs with approvals forced to manual and every headless approval denied. PROVING GROUND never answers, approves or hides an approval, registers no approval transport, and refuses to run when YOLO, `approvals.mode: off`, cron or a messaging platform is active.
> - **No network calls of its own.** It opens one local listener on `127.0.0.1` (random port) to record whether a test call reached execution. The children have no API keys or credentials, and their model endpoint is a closed local port. PROVING GROUND cannot stop *other* plugins from making their own network calls during a check. Those plugins would see only synthetic data.
> - **Reads outside its own data:** your enabled-plugin list, those plugins' directories and their `plugins.entries` settings (read-only, copied into the temporary child). Credential-shaped files (`.env*`, `*.key`, `*.pem`, `auth*.json`, …), symlinks leaving the plugin dir and secret-shaped settings are withheld from the copy, and the report lists them. It never reads `.env`, `auth.json` or any credential store of its own accord. An optional `--calls FILE` you pass is read and validated.
> - **No telemetry.** Reports are written to PROVING GROUND's own plugin-data folder and shared only if you export them.
> - **What a report means:** it shows how your plugins treated these specific synthetic inputs in a sandbox. It does not grade your stack.
> - **Rule 12 (spawned children).** The child is a single-query `hermes chat -q`, but its config forces approvals to manual with single-query, cron and unattended modes all on deny, it never gets `--yolo`/`-z`/`--oneshot` or `HERMES_YOLO_MODE`, and the only commands it runs are the listed calls in `testcalls.yaml` (plus your own `--calls`, which must pass a closed grammar). Anything approval-worthy is denied by Hermes itself.

## Limits
Every report says "Observed on 10 synthetic inputs in a sandbox." Ten synthetic inputs cannot cover what a stack does, and a quiet matrix is not a grade. Rewrites and escapes are detected after they ran, not prevented. Only directory plugins are tested; plugins that declare `requires_env` are shown N/A. Verified on Hermes v0.23.1 on macOS; Linux is untested. The full list, with reasons: [LIMITATIONS.md](LIMITATIONS.md). Threats and what is not defended against: [SECURITY.md](SECURITY.md). Every public claim and the command that checks it: [CLAIMS.md](CLAIMS.md).

## For plugin authors
Test your guard against a real stack before you ship. Run your plugin next to the ones your users install, put your trigger shape in a `--calls` file with `expect:` contracts, and keep `--check` in CI so a Hermes upgrade or a neighbouring plugin that changes your cell turns the build red.

## Development
```bash
# a dev venv on Hermes's bundled Python, with a read-only path to Hermes (nothing is installed into ~/.hermes)
"$HERMES_PYTHON" -m venv .venv
.venv/bin/pip install "pytest==9.1.1" "ruamel.yaml==0.18.16" "rich==14.3.4"
printf '%s\n%s\n' "$HERMES_AGENT_DIR" "$HERMES_SITE_PACKAGES" > .venv/lib/python3.14/site-packages/hermes-readonly.pth

export TMPDIR=/private/tmp
.venv/bin/python -m pytest -m "not hermes"     # pure tier, about a second
.venv/bin/python -m pytest -m hermes           # Hermes tier: real child drills on a warm cache
.venv/bin/python scripts/verify-claims.py      # every CLAIMS.md row against its evidence; --hermes adds the Hermes tier
```
CI runs the pure tier, `verify-claims` and `hermes plugins validate` / `hermes plugins doctor --ci` on Hermes v0.23.1.

Licence: MIT (declared in `plugin.yaml`).
