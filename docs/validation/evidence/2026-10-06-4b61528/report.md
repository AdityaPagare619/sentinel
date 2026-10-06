# Track 7 validation report
- generated: 2026-10-06T05:48:10.830676+00:00
- branch: lane/build-t7-validation @ 4b61528

## AC-1 kill-switch drill: BLOCKED
pass=0 fail=0 blocked=4 deferred=0 cannot-verify=0
- [BLOCKED] test_1a_drill_artifact_runs_and_measures (tests.validation.test_ac1_kill_drill.TestAC1KillDrill.test_1a_drill_artifact_runs_and_measures): BLOCKED: Track 3 not merged — no kill endpoint (/api/v1/safety/kill), no `mode` field on the disposition record, no scripts/kill_drill.py on this branch (scripts/kill_drill.py absent)
- [BLOCKED] test_1b_sticky_no_self_revive (tests.validation.test_ac1_kill_drill.TestAC1KillDrill.test_1b_sticky_no_self_revive): BLOCKED: Track 3 not merged — no kill endpoint (/api/v1/safety/kill), no `mode` field on the disposition record, no scripts/kill_drill.py on this branch (scripts/kill_drill.py absent)
- [BLOCKED] test_1c_rearm_separate_deliberate (tests.validation.test_ac1_kill_drill.TestAC1KillDrill.test_1c_rearm_separate_deliberate): BLOCKED: Track 3 not merged — no kill endpoint (/api/v1/safety/kill), no `mode` field on the disposition record, no scripts/kill_drill.py on this branch (scripts/kill_drill.py absent)
- [BLOCKED] test_1d_kill_above_jev_control_principle (tests.validation.test_ac1_kill_drill.TestAC1KillDrill.test_1d_kill_above_jev_control_principle): BLOCKED: Track 3 not merged — no kill endpoint (/api/v1/safety/kill), no `mode` field on the disposition record, no scripts/kill_drill.py on this branch (scripts/kill_drill.py absent)

## AC-2 race outcomes: BLOCKED
pass=6 fail=0 blocked=2 deferred=0 cannot-verify=0
- [BLOCKED] test_2e_key_absent_fakejev_honest (tests.validation.test_ac2_race_outcomes.TestAC2RaceOutcomes.test_2e_key_absent_fakejev_honest): BLOCKED: Track 2 judge adapter not merged — FakeJev, spend endpoint, and the JudgeResult `source` field are unavailable on this branch
- [BLOCKED] test_2f_budget_exhausted_deterministic (tests.validation.test_ac2_race_outcomes.TestAC2RaceOutcomes.test_2f_budget_exhausted_deterministic): BLOCKED: Track 2 judge adapter not merged — FakeJev, spend endpoint, and the JudgeResult `source` field are unavailable on this branch

## AC-3 suppression correctness: PASS
pass=9 fail=0 blocked=0 deferred=0 cannot-verify=0

## AC-4 scale test: BLOCKED
pass=1 fail=0 blocked=1 deferred=0 cannot-verify=0
- [BLOCKED] test_4_track6_manifests_probe (tests.validation.test_ac4_scale.TestAC4Scale.test_4_track6_manifests_probe): BLOCKED (Track 6): no scenario manifests under platform/server/scenarios/ — running harness-generated profiles instead

## AC-5 P0 re-walk: BLOCKED
pass=0 fail=0 blocked=8 deferred=0 cannot-verify=0
- [BLOCKED] test_5_probe (tests.validation.test_ac5_p0_rewalk.TestAC5P0Rewalk.test_5_probe): BLOCKED: Track 1 not merged — /api/* has no operator bearer auth on this branch (the P0 from the infra domain research is still open)
- [BLOCKED] test_5a_unauthenticated_key_overwrite_401 (tests.validation.test_ac5_p0_rewalk.TestAC5P0Rewalk.test_5a_unauthenticated_key_overwrite_401): BLOCKED: Track 1 not merged — /api/* has no operator bearer auth on this branch (the P0 from the infra domain research is still open)
- [BLOCKED] test_5b_bogus_bearer_401 (tests.validation.test_ac5_p0_rewalk.TestAC5P0Rewalk.test_5b_bogus_bearer_401): BLOCKED: Track 1 not merged — /api/* has no operator bearer auth on this branch (the P0 from the infra domain research is still open)
- [BLOCKED] test_5c_cross_origin_post_blocked (tests.validation.test_ac5_p0_rewalk.TestAC5P0Rewalk.test_5c_cross_origin_post_blocked): BLOCKED: Track 1 not merged — /api/* has no operator bearer auth on this branch (the P0 from the infra domain research is still open)
- [BLOCKED] test_5d_unauthenticated_key_delete_401 (tests.validation.test_ac5_p0_rewalk.TestAC5P0Rewalk.test_5d_unauthenticated_key_delete_401): BLOCKED: Track 1 not merged — /api/* has no operator bearer auth on this branch (the P0 from the infra domain research is still open)
- [BLOCKED] test_5e_no_auth_on_non_exempt_surfaces (tests.validation.test_ac5_p0_rewalk.TestAC5P0Rewalk.test_5e_no_auth_on_non_exempt_surfaces): BLOCKED: Track 1 not merged — /api/* has no operator bearer auth on this branch (the P0 from the infra domain research is still open)
- [BLOCKED] test_5f_health_endpoints_exempt (tests.validation.test_ac5_p0_rewalk.TestAC5P0Rewalk.test_5f_health_endpoints_exempt): BLOCKED: Track 1 not merged — /api/* has no operator bearer auth on this branch (the P0 from the infra domain research is still open)
- [BLOCKED] test_5g_authenticated_key_save_works (tests.validation.test_ac5_p0_rewalk.TestAC5P0Rewalk.test_5g_authenticated_key_save_works): BLOCKED: Track 1 not merged — /api/* has no operator bearer auth on this branch (the P0 from the infra domain research is still open)

## AC-6 shadow audit: BLOCKED
pass=0 fail=0 blocked=4 deferred=0 cannot-verify=0
- [BLOCKED] test_6a_mode_field_correct (tests.validation.test_ac6_shadow_audit.TestAC6ShadowAudit.test_6a_mode_field_correct): BLOCKED: Track 3 not merged — no `mode` field on the disposition record (shadow vs live) on this branch
- [BLOCKED] test_6b_zero_rewritten_reasons (tests.validation.test_ac6_shadow_audit.TestAC6ShadowAudit.test_6b_zero_rewritten_reasons): BLOCKED: Track 3 not merged — no `mode` field on the disposition record (shadow vs live) on this branch
- [BLOCKED] test_6c_shadow_is_powerless (tests.validation.test_ac6_shadow_audit.TestAC6ShadowAudit.test_6c_shadow_is_powerless): BLOCKED: Track 3 not merged — no `mode` field on the disposition record (shadow vs live) on this branch
- [BLOCKED] test_6d_kill_attribution_intact (tests.validation.test_ac6_shadow_audit.TestAC6ShadowAudit.test_6d_kill_attribution_intact): BLOCKED: Track 3 not merged — no `mode` field on the disposition record (shadow vs live) on this branch

## AC-7 falsifiers: DEFERRED
pass=0 fail=0 blocked=0 deferred=9 cannot-verify=0
- [DEFERRED] test_7_1_3am_dynamic (tests.validation.test_ac7_falsifiers.TestAC7Falsifiers.test_7_1_3am_dynamic): DEFERRED: Track 8 has not merged a console on program/full-build yet
- [DEFERRED] test_7_2_scale_dynamic (tests.validation.test_ac7_falsifiers.TestAC7Falsifiers.test_7_2_scale_dynamic): DEFERRED: Track 8 has not merged a console on program/full-build yet
- [DEFERRED] test_7_3_honesty_static (tests.validation.test_ac7_falsifiers.TestAC7Falsifiers.test_7_3_honesty_static): DEFERRED: Track 8 has not merged a console on program/full-build yet
- [DEFERRED] test_7_4_ai_generic_static (tests.validation.test_ac7_falsifiers.TestAC7Falsifiers.test_7_4_ai_generic_static): DEFERRED: Track 8 has not merged a console on program/full-build yet
- [DEFERRED] test_7_5_hierarchy_grayscale_dynamic (tests.validation.test_ac7_falsifiers.TestAC7Falsifiers.test_7_5_hierarchy_grayscale_dynamic): DEFERRED: Track 8 has not merged a console on program/full-build yet
- [DEFERRED] test_7_6_no_process_theater_static (tests.validation.test_ac7_falsifiers.TestAC7Falsifiers.test_7_6_no_process_theater_static): DEFERRED: Track 8 has not merged a console on program/full-build yet
- [DEFERRED] test_7_7_dynamics_dynamic (tests.validation.test_ac7_falsifiers.TestAC7Falsifiers.test_7_7_dynamics_dynamic): DEFERRED: Track 8 has not merged a console on program/full-build yet
- [DEFERRED] test_7_8_appeal_audit_dynamic (tests.validation.test_ac7_falsifiers.TestAC7Falsifiers.test_7_8_appeal_audit_dynamic): DEFERRED: Track 8 has not merged a console on program/full-build yet
- [DEFERRED] test_7_9_phone_static (tests.validation.test_ac7_falsifiers.TestAC7Falsifiers.test_7_9_phone_static): DEFERRED: Track 8 has not merged a console on program/full-build yet

## AC-8 honesty audit: PASS
pass=5 fail=0 blocked=0 deferred=0 cannot-verify=0

**WITHHELD — red criteria present**
