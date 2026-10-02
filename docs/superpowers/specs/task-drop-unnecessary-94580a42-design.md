# Subtask 94580a42 — Drop unnecessary fixtures and collapse the `--pretty` tests

Parent story a0b987c1 "Move test_cli.py off the real board" (milestone 66ed75cd). Narrows V5 of `docs/superpowers/specs/2026-10-02-test-tier-design.md`, layered on `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §14. The only file touched is `tests/test_cli.py`. No change under `src/`.

## Precondition

This subtask depends on the FakeBoard seam from bf9f2b42. That work has not landed on `master`, but this worktree already has it: `tests/conftest.py` defines `class FakeBoard` and the `fake_board` fixture, and the `cards` fixture in `tests/test_cli.py` seeds `fake_board`. If a working tree is missing `FakeBoard`, stop and report that. Do not rebuild the seam here. The line numbers in the card and in the milestone spec (`:2205,2291,2321,2459,4793`, `:1863,2767,…`) are out of date. Find tests by what they contain, not by line number.

## Scope

1. **Fixture-less tests.** The governing spec's file map (`2026-10-02-test-tier-design.md` §4) says "five" from a since-renumbered pass over `test_cli.py`; a fresh pass over this worktree's current `test_cli.py`, checking every test that takes `project`, `cards`, `milestone_board`, or `resume_board` for whether it replaces the command body completely (for example `cli.run_card` / `cli.resume_run` swapped for a recorder with `monkeypatch.setattr`, not wrapped — as opposed to tests that only swap `default_runner_factory`, `board.show`, or similar and still drive the real `run_card`/`resume_run` against the real repo/board), found exactly three such tests, and all three only use `project`/`cards` for id strings or a `--repo-dir` path:
   - `test_repeated_verify_options_reach_run_card_in_command_line_order`
   - `test_no_verify_option_means_an_empty_command_list_not_none`
   - `test_a_verify_value_is_passed_through_verbatim_including_spaces_and_empties`

   Change each one to take `tmp_path` and a literal card-id (or run-id) string, the way `test_a_stopped_card_run_is_ok_true_and_exit_zero` already does with `"cbe34d00-9d8d-4f41-9c94-f99e665771b0"`. Any `_fake_payload(card_id, cards["story"])` argument becomes a literal story id string. The implementer should do one more pass in case the worktree has changed since this spec was written, but should not spend long hunting for a specific count: convert whichever tests fit the criterion above, however many that is, and do not force-convert a test that really uses the repo or board just to hit a target number.
2. **`--pretty` collapse.** There are 8 `--pretty` CliRunner call sites: `run`, milestone `--dry-run`, `_milestone_run`, `status`, `runs`, `logs`, `resume`, and control (pause/cancel). Reduce these to two things: the existing `test_render_indents_under_pretty` (unchanged), and one new parametrized CliRunner smoke test on the cheapest command. That should be a `projection`-backed command (`status`/`runs`/`logs`), which spawns no subprocess. Remove only the `--pretty` part of each old test. When a test's `--pretty` assertion is its whole purpose, delete the test. When it also checks other behaviour, keep it and drop just the `--pretty` invocation and its asserts.
3. **Re-tier.** Every converted or new test that no longer touches git or brd must lose `@requires_git` / `@requires_brd` and any git/brd marker. Those tests now belong to the default `unit` tier.

## Observable behaviour / compatibility

- CLI behaviour does not change at all (§5). Production code is not edited.
- No assertion is lost. Every assertion removed from an old `--pretty` test must already be covered by `test_render_indents_under_pretty` (indentation and envelope rendering) or by the new smoke test (`--pretty` reaches `render` through the CLI, exits 0, and gives indented output that parses back to the same envelope as the non-pretty run). If a removed assertion checks something command-specific beyond rendering, keep it in its original test without `--pretty`.
- The `--pretty` error path must still be covered. The current control test checks a `--pretty` refusal (`no-such-run`). The new smoke test must include at least one error-envelope case (`ok: false`, non-zero exit) as well as the success case.

## Out of scope

Changing the FakeBoard seam, `board.py`, or the fixture conversion itself (bf9f2b42). Making pytest-xdist the canonical verify. Changes to the engine, checkpoint format, harness adapter contract, or `dispatch.py`'s `LauncherFn`. The e2e tier (keep its 5 tests as they are). Milestone 14 `am run --board`.

## Tests (tier per V1 placement rule)

| Test | Change | Tier |
|---|---|---|
| `test_repeated_verify_options_reach_run_card_in_command_line_order` | `tmp_path` + literal id, decorators dropped | `unit` |
| `test_no_verify_option_means_an_empty_command_list_not_none` | same | `unit` |
| `test_a_verify_value_is_passed_through_verbatim_including_spaces_and_empties` | same | `unit` |
| `test_render_indents_under_pretty` | unchanged | `unit` |
| new `test_pretty_renders_through_the_cli` (parametrized: success + error envelope, `projection`-backed command) | new | `unit` (plain `tmp_path`, no subprocess) |
| the 7 other `--pretty` call sites | `--pretty` part removed; test deleted only when that was its entire purpose | keeps its current tier (unchanged by this subtask) |

## Verification

- `uv run pytest` is green.
- V5 §6 proof: run the old and new `tests/test_cli.py` with `-k` filters for the affected tests (the verify-option tests, the pretty tests) and diff pass/fail. Outcomes must match and wall time should be lower. A green suite alone is not enough.
- Grep check: none of the converted tests, and not the new smoke test, still requests `project`, `cards`, `milestone_board`, or `resume_board`, or carries `requires_git` / `requires_brd`.
