<!-- File: CLAIMS.md -->
<!-- [VERIFIED by the §9.1 run] seed ledger; rows are added before any new public sentence is written. -->
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
