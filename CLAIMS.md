<!-- File: CLAIMS.md -->
<!-- seed ledger; rows are added before any new public sentence is written. -->
# CLAIMS — every public sentence and the evidence that backs it

| id | claim | kind | evidence |
|---|---|---|---|
| CL-01 | The model is never called during a drill. | test | tests/test_injector.py::test_scripted_rounds_then_done |
| CL-01b | Every scripted step is logged with provider_called false, and the turn ends with done. | test | tests/test_injector.py::test_first_step_confirms_loaded_plugins_and_last_logs_done |
| CL-02 | The scripted-turn middleware is registered only inside a throwaway drill child. | test | tests/test_arming.py::test_live_register_adds_no_middleware |
| CL-03 | Each missing activation key keeps the middleware off. | test | tests/test_arming.py::test_each_missing_key_blocks_activation |
| CL-04 | PROVING GROUND never answers or approves an approval. | test | tests/test_approvals_static.py::test_no_approval_bypass_tokens_in_shipped_code |
| CL-05 | It refuses to run under YOLO, approvals off, cron or a messaging platform. | test | tests/test_posture.py::test_posture_refusals |
| CL-06 | The child gets no credentials and a closed loopback model endpoint. | test | tests/test_child_spawn.py::test_env_is_an_allow_list_without_yolo_or_secrets |
| CL-07 | Every built-in test call stays inside the sandbox. | test | tests/test_containment.py::test_every_rendered_call_stays_inside |
| CL-07b | A rewrite that fails the closed call grammar is never replayed. | test | tests/test_containment.py::test_crosscheck_never_replays_a_rewrite_that_fails_the_grammar |
| CL-08 | A call rewritten to leave the sandbox is flagged ESCAPED after it ran, and the column is stopped. | test | tests/test_escape_abort.py::test_rewrite_outside_sandbox_is_escaped_and_stops |
| CL-09 | Core truncation is never blamed on a plugin (zero-plugin baseline). | test | tests/test_matrix.py::test_core_truncation_is_never_blamed |
| CL-10 | A stack effect is attributed to a named plugin when one solo run shows it. | test | tests/test_matrix.py::test_attribution_names_the_solo_plugin |
| CL-11 | It finds a rewrite that another plugin's check never saw. | test | tests/test_matrix.py::test_rewrite_unseen_by_guard_finding |
| CL-12 | Reports never call a stack safe or secure in their own words (plugin output is quoted). | test | tests/test_report_wording.py::test_all_formats_free_of_forbidden_claims |
| CL-13 | The recorder listens on 127.0.0.1 only. | test | tests/test_network_loopback.py::test_recorder_binds_loopback_only |
| CL-14 | No telemetry: PROVING GROUND's own code makes no network call outside loopback; the one-off warm-up lets Hermes's installer fetch its runtime. | absent | (?i)(requests\.|urllib\.request|httpx|socket\.create_connection) |
| CL-15 | Ten test calls, all in a readable file at the repository root. | test | tests/test_testcalls_schema.py::test_catalog_uses_only_allowed_tools |
| CL-16 | The fixture drill's child Hermes processes connected only to loopback. | report | conditions.connects_non_loopback == 0 |
| CL-17 | A drill leaves your real Hermes config, state DB, SOUL.md, memories, sessions and installed plugins unchanged; reports go to plugin-data/proving-ground. | test | tests/test_home_untouched.py::test_real_home_unchanged_by_drill |
| CL-18 | Nothing PROVING GROUND started is left running after a drill or an interrupt (SIGINT/SIGTERM). | test | tests/test_cleanup.py::test_signal_mid_drill_stops_promptly_and_leaves_nothing |
| CL-19 | Your own test calls go through the same validator and a closed call grammar. | test | tests/test_testcalls_schema.py::test_user_calls_use_the_same_validator_and_grammar |
| CL-20 | Leave the implicated plugin out, run again, and the cell and the contract clear. | test | tests/test_drill_fixtures_e2e.py::test_without_rerun_clears_the_cell_and_the_contract |
| CL-21 | A plugin that did not load is reported LOAD FAILED, never PASSED THROUGH. | test | tests/test_matrix.py::test_unconfirmed_plugin_is_load_failed_never_passed_through |
| CL-22 | PROVING GROUND deletes only temp directories it created. | test | tests/test_cleanup.py::test_unowned_directories_are_never_swept |
| CL-23 | The committed fixture drill recorded 10 test calls, 3 plugins, 3 distinct effects and 1 cross-plugin finding. | grep | docs/fixture-report.json :: "tally": "10 test calls · 3 plugins · 3 distinct effects recorded · 1 cross-plugin finding" |
| CL-24 | In that drill the model was not called: 61 scripted steps answered by PROVING GROUND, 0 provider calls. | grep | docs/fixture-report.json :: NOT CALLED \(scripted turn: 61 llm_execution steps answered by PROVING GROUND, 0 provider calls\) |
| CL-25 | That drill took 19.5 s on a warm cache. | grep | docs/fixture-report.json :: "wall_s": 19\.[45]\d* |
| CL-26 | That drill left 0 drill processes and 0 drill roots. | grep | docs/fixture-report.json :: "leftover_processes": 0,\s*"leftover_roots": 0 |
| CL-27 | In that drill fx_guard only saw TC03's original command in the stack, and the rewritten command ran (REWRITE_UNSEEN_BY). | grep | docs/fixture-report.json :: "kind": "REWRITE_UNSEEN_BY",\s*"call_id": "TC03" |
| CL-28 | In that drill TC04's removed FAILED line is attributed to fx_trimmer by one solo run. | grep | docs/fixture-report.md :: attribution TC04 RESULT CHANGED: solo-match — observed in one solo run: fx_trimmer |
| CL-29 | `--check` exits 5 unless every declared expect: is met, and also when an expectation is not evaluated or none is declared. | test | tests/test_cli_exits.py::test_check_fails_on_unevaluated_or_empty_contracts |
| CL-30 | `sweep` prints how many stale temporary roots it removed. | grep | proving_ground/cli.py :: removed \{len\(removed\)\} stale sandbox roots |
| CL-31 | Plugins that declare requires_env are shown N/A, because the child has no credentials. | grep | proving_ground/matrix.py :: declares requires_env; the sandbox has no credentials |
| CL-32 | The child never gets --yolo, -z or --oneshot. | test | tests/test_child_spawn.py::test_argv_never_bypasses_approvals |
| CL-33 | The child config forces approvals to manual with headless deny. | test | tests/test_child_spawn.py::test_child_config_forces_manual_deny |
| CL-34 | PROVING GROUND registers llm_execution middleware and tool-hook taps, and a child Hermes subprocess runs each column. | grep | __init__.py :: ctx\.register_middleware\("llm_execution" |
| CL-35 | A core approval gate hidden by a stack block is not reported as a masked plugin effect (58cab27). | test | tests/test_matrix.py::test_core_approval_gate_hidden_by_a_stack_block_is_not_masked |
| CL-36 | The cell detail says its attribution is the full-stack one (5e39586). | test | tests/test_cell_detail.py::test_solo_cell_detail_labels_attribution_as_the_full_stack_one |
| CL-37 | A plugin's change on top of core truncation is attributed to the plugin (bcfbe4c). | test | tests/test_matrix.py::test_plugin_result_change_over_core_truncation_is_attributed_to_the_plugin |
| CL-38 | The real catalog stack run recorded 10 test calls, 5 plugins, 10 distinct effects and 1 cross-plugin finding. | grep | docs/real-stack-report.md :: 10 test calls · 5 plugins · 10 distinct effects recorded · 1 cross-plugin finding |
| CL-39 | Its finding is MASKED_IN_STACK on TC05 for hermes-detox. | grep | docs/real-stack-report.md :: MASKED_IN_STACK: hermes-detox RESULT CHANGED TC05 on its own; in the full stack that effect does not reach the model\. |
| CL-40 | In that run readonly-guard stops TC05 alone and in the full stack. | grep | docs/real-stack-report.md :: TC05 long output \x7c RESULT CHANGED \(core\) \x7c PASSED THROUGH \x7c RESULT CHANGED \x7c PASSED THROUGH \x7c BLOCKED \x7c PASSED THROUGH \x7c BLOCKED \x7c |
| CL-41 | As of 2026-11-11, xray (#149414), crash-test (#151102), policy-backtester (#151434) and benchforge (#152504) are merged catalog entries. | grep | docs/field-2026-11-11.md :: (?s)#149414 MERGED.*xray.*#151102 MERGED.*crash-test.*#151434 MERGED.*policy-backtester.*#152504 MERGED.*benchforge |
| CL-41b | Each of those four entries is described in the README by a sentence quoted verbatim from its own PR description. | grep | docs/field-2026-11-11.md :: (?s)X-Ray sets traps for plugins instead of reading their code\..*For one armed turn it makes tools fail the way real systems fail.*a test harness for Hermes guardrails: what a rule would break, and what it would miss\..*BenchForge is the flight simulator for AI-written Arduino firmware\. |
| CL-42 | As of 2026-11-11, catalog title searches for plugin stack, hook chain, plugin conflict, plugins interact and hook order returned no PR about running several installed plugins together. | grep | docs/field-2026-11-11.md :: These title searches returned no catalog PR about running several installed plugins together |
| CL-43 | The first run fetches Hermes's runtime once, about 650 MB. | grep | proving_ground/cli.py :: one-time ~650 MB download |
| CL-44 | CI runs the pure tier, verify-claims, hermes plugins validate and doctor on Hermes v0.23.1. | grep | .github/workflows/validate.yml :: (?s)pytest -m "not hermes".*verify-claims\.py.*plugins validate \..*plugins doctor \. --ci |
| CL-45 | Ctrl-C during a drill stops every child and prints `drill cancelled; cleaned up N processes` (exit 130); an unexpected error is one line plus a crash log. | test | tests/test_cli_exits.py |
| CL-46 | A failed warm-up refuses the drill (WARMUP_FAILED) and `warm --check` reports readiness without spawning anything. | test | tests/test_warm_up.py |
| CL-47 | MASKED_IN_STACK is never reported when the stack cell did not load, did not run, does not apply or errored. | test | tests/test_masked_needs_a_stack_result.py |
| CL-48 | Saved and exported reports replace your home and temp paths with ~ and {TMP}. | test | tests/test_report_loading.py::test_saved_and_exported_reports_carry_no_home_or_temp_paths |
| CL-49 | The committed fixture drill left 0 drill processes after the run. | report | conditions.leftover_processes == 0 |
| CL-50 | The Git-URL install leaves the plugin disabled until `hermes plugins enable proving-ground`. | grep | README.md :: hermes plugins enable proving-ground\s+# install leaves it disabled |
| CL-60 | `--write-contract` pins every built-in cell to the outcome the drill observed, and the same stack meets it. | test | tests/test_contract_lockfile.py::test_contract_pins_every_built_in_cell_to_its_observed_outcome |
| CL-61 | After a plugin is left out, `--check` against that contract fails with exit 5 (changed cell and missing column). | test | tests/test_contract_lockfile.py::test_the_same_contract_fails_with_exit_5_after_leaving_a_plugin_out |
| CL-62 | User calls and cells that never ran are not pinned in a written contract. | test | tests/test_contract_lockfile.py::test_user_calls_and_cells_that_never_ran_are_not_pinned |
| CL-63 | `diff A.json B.json` prints only what changed and exits 0 when nothing did, 6 when something did. | test | tests/test_report_compare.py::test_diff_action_exits_0_without_change_and_6_with_one |
| CL-64 | `--since-last` compares with the report saved before this drill; plugins added or removed, cell outcomes and findings are listed. | test | tests/test_report_compare.py::test_since_last_compares_with_the_report_saved_before_this_drill |
| CL-65 | When `--compare` finds changes and `--check` fails, the exit code is 5, not 6. | test | tests/test_report_compare.py::test_compare_exit_6_never_hides_a_failed_contract |
| CL-66 | `--focus` leaves out the rows where every cell is PASSED THROUGH and names the rows it left out. | test | tests/test_focus_view.py::test_focus_markdown_hides_quiet_rows_and_names_them |
| CL-67 | The HTML report's focus checkbox works with CSS only; the page has no script and loads nothing from the network. | test | tests/test_focus_view.py::test_html_toggle_is_css_only_and_checked_by_focus |
| CL-68 | The JSON export caps each recorded text at 8192 characters and marks the cut. | test | tests/test_export_caps.py::test_json_export_caps_a_long_field_and_says_how_much_was_cut |
