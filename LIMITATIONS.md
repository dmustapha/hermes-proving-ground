# LIMITATIONS — what PROVING GROUND does and does not do (v0.1.0)

## In scope
- **Directory plugins enabled in your Hermes home** (`<HERMES_HOME>/plugins/<key>/`), tested one at a time and all together against a zero-plugin baseline.
- **Ten synthetic test calls** using four tools (`terminal`, `write_file`, `read_file`, `patch`), all inside a temporary sandbox: prints, sandbox file writes/reads/patches, one loopback fetch from PG's own recorder, one approval-worthy permission change that headless approvals deny.
- **Six outcome classes plus four status classes:** BLOCKED, REWROTE, RESULT CHANGED (hop named), APPROVAL-GATED, PASSED THROUGH, ESCAPED; ERRORED, LOAD FAILED, NOT RUN, N/A.
- **Attribution** by solo runs (`solo-match`, `multiple-candidates`, `interaction`, `core`) and the **REWRITE_UNSEEN_BY** cross-check.
- **Hermes v0.23.1** on **macOS** (verified) and **Linux** (verified in CI: the `linux-drill` job on ubuntu-latest installs PROVING GROUND from git, runs `hermes provingground --fixtures` with TMPDIR unset and checks the golden matrix and zero leftovers; run 42994971722). The manifest declares `platforms: [macos, linux]`. With TMPDIR unset, Hermes points the temp dir at `<HERMES_HOME>/cache/scratch`; drills then use the first existing system temp dir (`/tmp` on Linux) instead.
- **Surfaces:** `hermes provingground` (run/show/warm/sweep) and `/provingground` from the CLI, TUI, Desktop or local sessions.
- **Reports:** terminal, Markdown, self-contained HTML, JSON; stored locally.

## Feature-gated or not covered (with reasons)
| Limitation | Reason | What PG does instead |
|---|---|---|
| Rewrites and escapes are **detected after they ran, not prevented** | no plugin seam sees final args before execute (`hermes_cli/plugins.py:1132-1150`) | environment containment + ESCAPED stop; no pre-execution pin is claimed |
| **ASKED** cells (a person asked) | headless children can't ask; PG never answers approvals (rule 12) | APPROVAL-GATED (Hermes denied it headless) |
| Session-level **`/yolo`** toggle not detected | `is_approval_bypass_active_for_session` is not a public API (`tools/approval.py:579`) | refuses on `HERMES_YOLO_MODE` and `approvals.mode: off`; Hermes issue drafted |
| **pip / entry-point plugins** are not tested | they have no plugin directory to copy into the sandbox | listed under "skipped" with the reason |
| Plugins that declare **`requires_env`** are not run | the sandbox has no credentials by design | column shows N/A with the reason |
| **Other plugins' own network code** can't be blocked | a plugin cannot block another plugin's sockets | dead proxies, no credentials, synthetic data only; the socket audit counts connects from the child Hermes process (not from subprocesses such as the terminal tool's commands) |
| **Absolute-path writes** hard-coded by another plugin (outside a tool call) | HOME/XDG/TMPDIR redirection doesn't cover arbitrary absolute paths | disclosed; the real-home hash test guards Hermes's own files |
| **Messaging platforms** for `/provingground` | v1 is for attended local surfaces | `REFUSED_PLATFORM` |
| **Windows** | process and temp-path handling not built for Windows | `SANDBOX_UNSUPPORTED` |
| **Context / reply hooks** (`pre_llm_call`, `transform_llm_output`) are recorded only with `--all-wires` | a default drill tests the tool path only | `--all-wires` adds an LLM-CONTEXT and an LLM-REPLY row; the context row compares the system prompt, user turn and tools offered with the baseline's, so a plugin's system-prompt section shows there too |
| **Stateful or time-dependent plugins** may differ between runs | each column is a fresh process | the baseline-repeat check marks unstable calls (`~`); stack-only effects are labelled `interaction`; `--repeat N` runs the solo columns with an effect again and marks disagreeing cells UNSTABLE; `--pairs` looks for the smallest set behind an `interaction` |
| **First run** needs network once and ~650 MB | Hermes's own runtime install for a new home | one warm-up, announced in one line, with its own 15-minute limit; a failed warm-up refuses the drill (`WARMUP_FAILED`); `warm --check` reports readiness; later drills use dead proxies; `warm --from ~/.hermes/installs` copies the runtime from your own Hermes install instead (no network; the source is only read) |
| The warm cache lives under the **temp directory of the shell** | it is `<temp>/provingground-cache`, so a shell with another `TMPDIR` (or macOS's periodic temp cleanup) finds it cold | `hermes provingground warm --check` names the cache path for the current shell |
| **Finished launchd job labels** remain on macOS (no process) | Hermes's gateway on macOS starts a one-off launchd job per child home | Hermes removes finished labels at its next gateway start (`gateway_launchd_runtime.py:91-97`); PG leaves no running process; disclosed in the README |
| A plugin storing a **secret in `plugins.entries`** has it copied into the temp child | PG copies plugin settings so plugins behave as configured | the child is deleted after the column; rule 11 says secrets belong in Hermes's secret routes, which PG never reads |
| Only **Hermes 0.23.1** tested; floor declared `>=0.23.1` | the build machine runs 0.23.1 | seams cited by file:line; re-test on upgrade |
| The **parent `hermes` process** can write Hermes's own bytecode (`.pyc`) into your Hermes install | the stock launcher runs Python with `-I`, which ignores `PYTHONDONTWRITEBYTECODE`; that is Hermes's launcher, not PG | every drill child (and the `--version` probe) runs Hermes's interpreter command with `-B` and reports bytecode off; to keep the parent from writing too, run PG from a launcher copy that adds `-B` |
| A **green matrix is not a grade** | ten synthetic inputs cannot cover a stack's behaviour | the wording is fixed: "Observed on N synthetic inputs in a sandbox" |

## Known gaps, with reasons
| Item | Status | Reason / what to do |
|---|---|---|
| **The child is not an OS sandbox.** Subject plugin code runs with your file permissions and can read files by absolute path | disclosed | OS-level confinement (`sandbox-exec`/`bwrap`) is not implemented; the README Safety model and Disclosure say so |
| **Each plugin runs in one solo run** by default; stateful plugins (counters, budgets, rate limits) can differ between runs and with call order | disclosed; `--repeat N` repeats on request | a repeat run of every solo column costs drill time, so it is opt-in and only covers the solo columns with an effect; attribution text says "observed in one solo run" |
| **Bundled / default-on Hermes plugins in the baseline are not enumerated** | disclosed | no public seam lists every loaded plugin; `has_plugin` only answers for known keys. Conditions line: "bundled plugins not enumerated" |
| **launchd user-domain env** (`launchctl setenv`) can reach the macOS gateway job | disclosed | launchd may add variables you set with `launchctl setenv` to each child home's gateway job; PG does not remove them. The drill's approvals come from the child config (manual, every headless mode on deny), not that environment. `launchctl getenv HERMES_YOLO_MODE` shows whether you have set one |
| **`python_dependencies` of real plugins** are not in PG's shared runtime cache | reported as LOAD FAILED | the plugin cannot import in the child; the column says so instead of passing |
| **Plugin names are user-chosen text** | partial | the forbidden-word check exempts quoted plugin output, not plugin *names* in findings; a plugin named e.g. `safe-x` would trip the development-time check (reports still render) |
| **`ps eww` inspection** of hermes/python processes | disclosed, narrowed | only hermes/python pids are inspected and only for the PG HERMES_HOME needle; launchd labels are not matched |
| **Credential-shaped file names** are a heuristic | partial | `.env*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `auth*.json`, `credentials*`, ssh key names, `secret(s).json/yaml/toml` are withheld; other names are copied |
| **The drill child reads one private Hermes field** | disclosed, read-only | to report the order the child really loaded plugins in, the child reads `ctx._manager._plugins` (Hermes has no public accessor). It only reads it, only inside a throwaway drill child (never in your session), and the report says "unknown" if the field is absent |
| **Saved reports are pruned** | by design | `plugin-data/proving-ground/reports` keeps the newest 20 `report-*.json` files (plus `latest.json`); older ones are removed when a new report is saved. Export a report with `--json` to keep it |
| **Column times vary with machine load** | measured, not a PG delay | one earlier verification run saw a 57 s solo column (1 of 5 runs). It did not recur in 11 later fixture drills on the build machine (2026-11-11, load average 15-19 from other work): drill walls 24.6-47.6 s, slowest column 22.8 s. Per column, Hermes's start up to PG's activation took 2.2-10.2 s, the scripted turn 2.6-12.0 s, and the child's exit after the turn 0.2-0.5 s, so the gateway daemon and launchd did not hold a column open. When a wave was slow, all five parallel columns were slow by the same amount, which points at the host, not one plugin or PG. Each column has a 120 s limit (`--timeout`) with one retry, and a column that still overruns is reported CELL_TIMEOUT |
| **The measured-home claim** covers config, state DB, SOUL.md, memories, sessions and plugins only | narrowed claim | logs/ and other Hermes-owned files written by the parent `hermes` process are not claimed |
