# PROVING GROUND
**A wiring test for your Hermes plugin stack.**

Your plugins don't run alone: every tool call passes through all of them, and Hermes merges `pre_tool_call` rewrites only after every check has already looked at the original arguments. So a guard and a rewriter that each behave correctly on their own can let through a call the guard would have stopped, and nothing inside either plugin can see it.

PROVING GROUND sends ten known, harmless test calls through the plugins you have installed, with the model never called, and shows in one matrix what each plugin, and the whole stack, did to each call.

## Quickstart
```bash
hermes plugins install https://github.com/dmustapha/hermes-proving-ground
hermes plugins enable proving-ground   # install leaves it disabled
hermes provingground --fixtures        # one command: the bundled three-plugin demo stack
```
Drop `--fixtures` to test the plugins enabled in your own Hermes home. The first run copies Hermes's runtime from your own Hermes installs (no network, the source is only read); only when there are none does Hermes's own installer download it, about 650 MB, once.

![The fixture matrix in the terminal: findings first, the matrix, then the drill conditions](docs/matrix.png)

## What the demo finds
In the full stack, `fx_rewriter` changes TC03's command after every pre-call check has run. `fx_guard` blocks the rewritten command when it sees it directly, but in the stack it only saw the original, and the rewritten command ran.

The report says that in plain words before the matrix, and marks the cell itself: `REWROTE · unseen by fx_guard`. Leave the rewriter out (`--without fx_rewriter`) and the cell clears.

The three demo plugins are the author's own fixtures, each written to show one Hermes merge rule. Run without `--fixtures` to see what your own stack does.

## Explore a real report without installing
https://dmustapha.github.io/hermes-proving-ground/fixture-report.html (the fixture drill at 8a7cf74) opens in any browser. Every cell opens its own detail: the args PROVING GROUND sent, the args that actually ran, and what reached the model, diffed against the zero-plugin baseline.

## What it tests, and what it doesn't
| It tests | It does not |
|---|---|
| Ten synthetic calls on four tools (`terminal`, `write_file`, `read_file`, `patch`), through Hermes's real hook chain | call the model: every report says `model: NOT CALLED` |
| A zero-plugin baseline, each plugin alone, the full stack, and a replay of each rewritten call to each guard | test pip / entry-point plugins; a plugin that declares `requires_env` is shown N/A |
| Your own trigger shapes with `expect:` contracts: `--calls my-calls.yaml --check` exits 5 unless every one is met | grade your stack: a quiet matrix says only what these inputs did |

The cells read BLOCKED, REWROTE, RESULT CHANGED, APPROVAL-GATED or PASSED THROUGH. `(core)` marks Hermes's own behaviour, such as its truncation of long output, and is never attributed to a plugin. The full outcome list, options and attribution rules are in [docs/USAGE.md](docs/USAGE.md).

## Safety model
> - **Throwaway child Hermes homes**, one per column. Not an OS sandbox: your plugins run with your permissions, and a call rewritten to land outside the temp directory is detected after it ran, not prevented.
> - **No model, no keys:** the child env has no provider keys, its model endpoint is a closed port on 127.0.0.1, and the only listener PROVING GROUND opens is on 127.0.0.1.
> - **Approvals stay on and are always denied** in the child. PROVING GROUND never answers one, and refuses to run under YOLO, approvals off, cron or a messaging platform.
> - **Nothing left behind:** your Hermes config, sessions, memories and installed plugins are unchanged by a drill, and every report counts the drill processes and roots left after it (0 and 0 in the committed drill).

The full disclosure of what it does on your machine is in [SECURITY.md](SECURITY.md#disclosure).

## For plugin authors
```bash
hermes provingground lock stack.lock.yaml             # pin every cell of today's stack to what it showed
hermes provingground --calls stack.lock.yaml --check  # later, in CI: exit 5 when a cell changes
```
Put your guard's trigger in a `--calls` file to test it next to the plugins your users install. [docs/USAGE.md](docs/USAGE.md#test-your-own-guard) has the file format and a CI recipe.

## More
- [docs/USAGE.md](docs/USAGE.md): every action and option, the outcomes, how attribution works, comparing reports, the slash command, and what we found while building it.
- [SECURITY.md](SECURITY.md): threats, what is not defended against, and the full disclosure.
- [LIMITATIONS.md](LIMITATIONS.md): what is out of scope, and why.
- [CLAIMS.md](CLAIMS.md): every public sentence and the test or command that checks it.

Verified on Hermes v0.23.1, on macOS and on Linux in CI. Licence: MIT.
