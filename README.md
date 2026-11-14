# PROVING GROUND
**A wiring test for your Hermes plugin stack.**

Listed in the official [Hermes plugin catalog](https://github.com/NousResearch/hermes-agent/tree/main/plugin-catalog) (community tier), merged in [NousResearch/hermes-agent#153961](https://github.com/NousResearch/hermes-agent/pull/153961).

Your plugins don't run alone: every tool call passes through all of them, and Hermes merges `pre_tool_call` rewrites only after every check has already looked at the original arguments. So a guard and a rewriter that each behave correctly on their own can let through a call the guard would have stopped, and nothing inside either plugin can see it.

PROVING GROUND sends 24 known, harmless test calls through the plugins you have installed, with the model never called, and shows in one matrix what each plugin, and the whole stack, did to each call.

## Quickstart
```bash
hermes plugins install proving-ground --enable   # from the Hermes plugin catalog
hermes provingground --fixtures                  # the bundled three-plugin demo stack
```
Installing by name pins the catalog's reviewed commit; `hermes plugins install https://github.com/dmustapha/hermes-proving-ground` installs the latest. Drop `--fixtures` to test the plugins enabled in your own Hermes home. The first run copies Hermes's runtime from your own Hermes installs (no network, the source is only read); only when there are none does Hermes's own installer download it, about 650 MB, once (allowed up to 15 minutes).

![The fixture matrix in the terminal: findings first, the matrix, then the drill conditions](docs/matrix.png)

## What the demo finds
In the full stack, `fx_rewriter` changes TC03's command after every pre-call check has run. `fx_guard` blocks the rewritten command when it sees it directly, but in the stack it only saw the original, and the rewritten command ran.

The report says that in plain words before the matrix, and marks the cell itself: `REWROTE · unseen by fx_guard`. Leave the rewriter out (`--without fx_rewriter`) and the cell clears.

The three demo plugins are the author's own fixtures, each written to show one Hermes merge rule. Run without `--fixtures` to see what your own stack does.

On a real five-plugin catalog stack (canary-trap, hermes-detox, progress-squash, readonly-guard, safe-swap at their pinned catalog SHAs) one run found 2 cross-plugin findings: readonly-guard stops TC05 and TC12 before hermes-detox's and progress-squash's changes can reach the model ([docs/real-stack-report.md](docs/real-stack-report.md)).

## Explore a real report without installing
https://dmustapha.github.io/hermes-proving-ground/fixture-report.html (the fixture drill on v0.1.1) opens in any browser. Every cell opens its own detail: the args PROVING GROUND sent, the args that actually ran, and what reached the model, diffed against the zero-plugin baseline.

## What it tests, and what it doesn't
| It tests | It does not |
|---|---|
| 24 synthetic calls: twenty executed on four tools (`terminal`, `write_file`, `read_file`, `patch`) and four block-only probes, through Hermes's real hook chain | call the model: every report says `model: NOT CALLED` |
| A zero-plugin baseline, each plugin alone, the full stack, and a replay of each rewritten call to each guard | test pip / entry-point plugins; a plugin that declares `requires_env` is shown N/A |
| Your own trigger shapes with `expect:` contracts: `--calls my-calls.yaml --check` exits 5 unless every one is met | grade your stack: a quiet matrix says only what these inputs did |

The cells read BLOCKED, REWROTE, RESULT CHANGED, APPROVAL-GATED or PASSED THROUGH. `(core)` marks Hermes's own behaviour, such as its truncation of long output, and is never attributed to a plugin. The full outcome list, options and attribution rules are in [docs/USAGE.md](docs/USAGE.md).

## Safety model
> - **Throwaway child Hermes homes**, one per column. Not an OS sandbox: your plugins run with your permissions, and a call rewritten to land outside the temp directory is detected after it ran, not prevented.
> - **No model, no keys:** the child env has no provider keys, its model endpoint is a closed port on 127.0.0.1, and the only listener PROVING GROUND opens is on 127.0.0.1.
> - **Approvals stay on and are always denied** in the child. PROVING GROUND never answers one, and refuses to run under YOLO, approvals off, cron or a messaging platform.
> - **Nothing left behind:** your Hermes config, sessions, memories and installed plugins are unchanged by a drill, and every report counts the drill processes and roots left after it (0 and 0 in the committed drill).

> - **Also on your machine:** it reads process command lines and environments (`ps axeww`) to find its own children, installs an audit hook (`sys.addaudithook`) in each drill child to count non-loopback connects. It uses only public plugin APIs and never touches Hermes's private plugin-manager state, so per-plugin liveness is reported as unknown.

The full disclosure of what it does on your machine is in [SECURITY.md](SECURITY.md#disclosure).

## For plugin authors
```bash
hermes provingground lock stack.lock.yaml             # pin every cell of today's stack to what it showed
hermes provingground --calls stack.lock.yaml --check  # later, in CI: exit 5 when a cell changes
```
Put your guard's trigger in a `--calls` file to test it next to the plugins your users install. User terminal calls are a closed grammar: `echo` or `printf` whose first word is not a flag (later words, flags included, are printed, never run), `seq` without flags (at most 20000 lines), `curl -q` to the run's own recorder, and `chmod` with a 3-digit octal mode that keeps the owner's read and write on sandbox paths below the sandbox root; no pipes, `;`, `&`, `$`, redirection, globs, `..` or `~`, no `printf` `%n` or width over 9999, and only the argument names the tool needs (`command` for `terminal`; no `background` or `workdir`). Write your trigger as text your guard matches, e.g. `echo git push --force`. [docs/USAGE.md](docs/USAGE.md#test-your-own-guard) has the file format and a CI recipe.

## More
- [docs/USAGE.md](docs/USAGE.md): every action and option, the outcomes, how attribution works, comparing reports, the slash command, and what we found while building it.
- [SECURITY.md](SECURITY.md): threats, what is not defended against, and the full disclosure.
- [LIMITATIONS.md](LIMITATIONS.md): what is out of scope, and why.
- [CLAIMS.md](CLAIMS.md): the public claims and the test or command that checks each.

Verified on Hermes v0.23.1, on macOS and on Linux in CI. Licence: MIT.
