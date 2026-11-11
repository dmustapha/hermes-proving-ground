# LIMITATIONS — what PROVING GROUND does and does not do (v0.1.0)

## In scope
- **Directory plugins enabled in your Hermes home** (`<HERMES_HOME>/plugins/<key>/`), tested one at a time and all together against a zero-plugin baseline.
- **Ten synthetic test calls** using four tools (`terminal`, `write_file`, `read_file`, `patch`), all inside a temporary sandbox: prints, sandbox file writes/reads/patches, one loopback fetch from PG's own recorder, one approval-worthy permission change that headless approvals deny.
- **Six outcome classes plus four status classes:** BLOCKED, REWROTE, RESULT CHANGED (hop named), APPROVAL-GATED, PASSED THROUGH, ESCAPED; ERRORED, LOAD FAILED, NOT RUN, N/A.
- **Attribution** by solo runs (`solo-match`, `multiple-candidates`, `interaction`, `core`) and the **REWRITE_UNSEEN_BY** cross-check.
- **Hermes v0.23.1** on **macOS** (verified). **Linux: untested** (same mechanisms, `ps eww` and `/tmp`, but no recorded run; the manifest declares `platforms: [macos]`).
- **Surfaces:** `hermes provingground` (run/show/warm/sweep) and `/provingground` from the CLI, TUI, Desktop or local sessions.
- **Reports:** terminal, Markdown, self-contained HTML, JSON; stored locally.

## Feature-gated or not covered (with reasons)
| Limitation | Reason | What PG does instead |
|---|---|---|
| Rewrites and escapes are **detected after they ran, not prevented** | no plugin seam sees final args before execute (`hermes_cli/plugins.py:1132-1150`) | environment containment + ESCAPED stop; best-effort pin is a stretch item |
| **ASKED** cells (a person asked) | headless children can't ask; PG never answers approvals (rule 12) | APPROVAL-GATED (Hermes denied it headless) |
| Session-level **`/yolo`** toggle not detected | `is_approval_bypass_active_for_session` is not a public API (`tools/approval.py:579`) | refuses on `HERMES_YOLO_MODE` and `approvals.mode: off`; Hermes issue drafted |
| **pip / entry-point plugins** are not tested | they have no plugin directory to copy into the sandbox | listed under "skipped" with the reason |
| Plugins that declare **`requires_env`** are not run | the sandbox has no credentials by design | column shows N/A with the reason |
| **Other plugins' own network code** can't be blocked | a plugin cannot block another plugin's sockets | dead proxies, no credentials, synthetic data only; the socket audit counts connects from the child Hermes process (not from subprocesses such as the terminal tool's commands) |
| **Absolute-path writes** hard-coded by another plugin (outside a tool call) | HOME/XDG/TMPDIR redirection doesn't cover arbitrary absolute paths | disclosed; the real-home hash test guards Hermes's own files |
| **Messaging platforms** for `/provingground` | v1 is for attended local surfaces | `REFUSED_PLATFORM` |
| **Windows** | process and temp-path handling not built for Windows | `SANDBOX_UNSUPPORTED` |
| **Context / reply hooks** (`pre_llm_call`, `transform_llm_output`) not covered | MUST scope is the tool path | stretch S3 |
| **Stateful or time-dependent plugins** may differ between runs | each column is a fresh process | the baseline-repeat check marks unstable calls (`~`); stack-only effects are labelled `interaction` |
| **First run** needs network once and ~650 MB | Hermes's own runtime install for a new home | one warm-up, announced; later drills use dead proxies |
| **Finished launchd job labels** remain on macOS (no process) | Hermes's gateway on macOS starts a launchd one-off job per child home | Hermes clears them at its next gateway start (`gateway_launchd_runtime.py:106`); PG leaves no running process |
| A plugin storing a **secret in `plugins.entries`** has it copied into the temp child | PG copies plugin settings so plugins behave as configured | the child is deleted after the column; rule 11 says secrets belong in Hermes's secret routes, which PG never reads |
| Only **Hermes 0.23.1** tested; floor declared `>=0.23.1` | the build machine runs 0.23.1 | seams cited by file:line; re-test on upgrade |
| A **green matrix is not a grade** | ten synthetic inputs cannot cover a stack's behaviour | the wording is fixed: "Observed on N synthetic inputs in a sandbox" |

## Revision 1 tracked items (critical review findings not fully closed, with reasons)
| Item | Finding | Status | Reason / what to do |
|---|---|---|---|
| **The child is not an OS sandbox.** Subject plugin code runs with your file permissions and can read files by absolute path | PRD-F4 | disclosed | OS confinement (`sandbox-exec`/`bwrap`) is STRETCH; README first-run notice says it |
| **Each plugin runs in one solo run**; stateful plugins (counters, budgets, rate limits) can differ between runs and with call order | PRD-F8 | disclosed, not repeated | a repeat solo run per plugin roughly doubles drill time (~+15 s); attribution text says "observed in one solo run" |
| **Bundled / default-on Hermes plugins in the baseline are not enumerated** | PRD-F9 | disclosed | no public seam lists every loaded plugin; `has_plugin` only answers for known keys. Conditions line: "bundled plugins not enumerated" |
| **launchd user-domain env** (`launchctl setenv`) may reach the macOS gateway daemon | ARCH-F17 (low) | open check | before packaging: `launchctl getenv HERMES_YOLO_MODE` and `launchctl getenv OPENAI_API_KEY` must be empty on the demo machine; if a user has them set, Hermes's single-query deny still comes from the gateway-admitted turn flag, not the daemon env |
| **`python_dependencies` of real plugins** are not in PG's shared runtime cache | PRD-F3 | reported as LOAD FAILED | the plugin cannot import in the child; the column says so instead of passing |
| **Plugin names are user-chosen text** | PRD-F12 | partial | the forbidden-word check exempts quoted plugin output, not plugin *names* in findings; a plugin named e.g. `safe-x` would trip the dev-time check (reports still render) |
| **`ps eww` inspection** of hermes/python processes | PRD-F7 | disclosed, narrowed | only hermes/python pids are inspected and only for the PG HERMES_HOME needle; launchd-label matching was not attempted (time) |
| **Credential-shaped file names** are a heuristic | ARCH-F11 | partial | `.env*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `auth*.json`, `credentials*`, ssh key names, `secret(s).json/yaml/toml` are withheld; other names are copied |
| **The measured-home claim** covers config, state DB, SOUL.md, memories, sessions and plugins only | ARCH-F14 | narrowed claim | logs/ and other Hermes-owned files written by the parent `hermes` process are not claimed |
