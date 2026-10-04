# Subtask 2.3 — `am runs`: `progress` (card 882b212b)

Narrows `docs/superpowers/specs/2026-10-03-plugin-integration-design.md` §1 ("Richer `am runs`") to its last field, `progress`. Story e187d6f8 "Richer am runs". Siblings 2.1 (`milestone_id`, `card_id`) and 2.2 (`lease`) are done, and this worktree already contains their work (`RunLease`, `RunSummary.milestone_id/card_id/lease`, `cli._lease_fields`, `cli.runs_for`, README "Listing runs"). Build on that tip, and leave those fields, their SQL and their tests as they are.

## Scope

- `src/agent_manager/store.py`: three new pydantic models, all `ConfigDict(extra="forbid")`:
  - `ProgressCount { done: int, total: int }`
  - `ProgressCurrent { card: str, phase: str, attempt: int | None }`
  - `RunProgress { stories: ProgressCount, subtasks: ProgressCount, current: ProgressCurrent | None }`
- `RunSummary` gains `progress: RunProgress | None = None`, placed after `lease`. The default keeps the field additive for anyone who builds a `RunSummary` by hand. Update the class docstring.
- `store.list_runs` fills `progress` for every run, using read-only `SELECT`s on the same connection over the `stories`, `subtasks`, `phases` and `attempts` rows for that `run_id`. It needs no `control` import, because nothing in it depends on liveness. `list_runs` never returns `progress: None`. `latest_run_id` calls `list_runs`, so it pays for the progress queries too; accept that and do not add a second listing path. Update the `list_runs` docstring with the rules below.
- `src/agent_manager/cli.py`: `runs_for` needs no logic change, because `model_dump()` already carries the new field into `data.runs[]`. Reword the `RUN_IDENTITY` docstring so that the superset it lists includes `progress`.
- `README.md` "Listing runs": add one `progress` bullet after the `lease` bullet. Keep the existing "New keys are additive" paragraph and do not add a second one.
- Out of scope: `--detach`, `logs --follow`, `watch --from-now`, README `--board` docs, any change to the journal (it stays schema 1), and any change to the lease, claim, control-request or journal-line contracts. No new tables or columns.

## Observable behavior (the rules to pin)

Each `data.runs[]` entry has `progress` with exactly the keys `stories`, `subtasks` and `current`. `stories` and `subtasks` each have exactly `done` and `total`.

- `stories.total` is the number of `stories` rows for the run, and `stories.done` is the number of those rows with `status = 'done'`. `subtasks` follows the same rule over the `subtasks` rows. "Done" means only the `done` value of `models.Status`. `failed`, `escalated`, `stopped` and `cancelled` count toward `total` but not toward `done`.
- The synthetic Integrate story (`card_id` `integrate`, title `Integrate`) and its resolver subtasks are projection rows, so they are counted like any other row. `store` does not special-case them: `integration` imports `store`, so `store` cannot import that constant. A milestone run that had to resolve a conflict therefore shows one more story than the milestone has. State this in the docstring and the README.
- A `task` (`--card`) run counts as its one story and one subtask.
- `current` is the in-flight step, read only from rows. It is the `phases` row with `status = 'started'` that has the latest `started_at`, (`started_at` is nullable text: order `started_at DESC`, so a NULL sorts last and a started phase with a NULL `started_at` is chosen only if no other phase is started), with ties broken ascending by `story_id`, `card_id` and then `position`, so the result is stable when a milestone run has parallel subtasks in flight. In that object, `card` is the phase row's `card_id`, `phase` is its `name`, and `attempt` is the highest `attempts.n` for that `(story_id, card_id, phase)`, or `null` when no attempt row exists yet. When no phase is `started`, `current` is `null`.
- `current` is based on rows and not on liveness. A run whose process died mid-phase still shows the phase it stopped in, and the sibling `lease.live` tells a consumer whether anyone is still working on it. Say so in the docstring and the README.
- A run with no tree rows (just recorded, or never got past the run row) gives `{stories:{done:0,total:0}, subtasks:{done:0,total:0}, current:null}`. It is not `progress: null`.
- Counts are per run. The rows of another run never leak into this one.
- Error paths: none are new. An empty history is still `{"runs": []}`. `RunSummary.model_validate` (with `extra="forbid"` all the way down) still fails loudly on a projection that drifted.

## Tests (TDD: write first; all spawn nothing, so all are unmarked `unit` tests)

Every test below drives a SQLite fixture store (`repo` / `projection` fixtures, `record_run`, `record_story`, `record_subtask`, and phase/attempt recording). None spawns `git`, `brd` or `claude`, so per CLAUDE.md "Test tiers" / design spec §14 all of them are unmarked (`unit`). No `git`, `brd` or `e2e_fake` test is added. The spec's one `e2e_fake` belongs to the `--detach` story.

`tests/test_store.py` (unit):
1. Extend the `SUMMARY_KEYS` pin (`set(store.RunSummary.model_fields) == SUMMARY_KEYS`) with `progress`, and extend `_summary_fields` if it needs to. Pin `RunProgress`, `ProgressCount` and `ProgressCurrent` field sets the same way, plus `extra="forbid"` (an unknown key is rejected).
2. `test_list_runs_gives_a_run_with_no_tree_rows_zero_progress_and_no_current`: 0/0, 0/0, `current is None`, and `progress is not None`.
3. `test_list_runs_counts_stories_and_subtasks_done_against_total`: a mix of statuses (`done`, `started`, `pending`, `failed`, `escalated`) and the exact counts expected.
4. `test_list_runs_counts_a_task_run_as_one_story_and_one_subtask`.
5. `test_list_runs_counts_the_integrate_story_and_its_resolver_subtasks`.
6. `test_list_runs_keeps_each_runs_progress_to_its_own_rows`: two runs, with disjoint counts and disjoint `current`.
7. `test_list_runs_current_is_the_started_phase_with_its_latest_attempt`: several attempts, where `attempt` is the max `n`.
8. `test_list_runs_current_attempt_is_null_before_any_attempt_row`.
9. `test_list_runs_current_is_null_when_no_phase_is_started`: all phases `done` or `pending`.
10. `test_list_runs_current_picks_the_latest_started_phase_among_parallel_ones`, which also covers the tie-break.
11. `test_list_runs_progress_reads_without_writing`: `total_changes` on the connection is unchanged.

`tests/test_cli.py` (unit, `am runs` through the CLI runner on the `projection` fixture):
12. Extend `test_runs_entries_have_exactly_the_old_keys_plus_milestone_id_and_card_id` (or its successor shape test) so that the entry key set includes `progress`. Rename the test if its name stops being true.
13. `test_runs_progress_has_exactly_its_keys_plain_and_pretty`: the nested key sets of `progress`, `stories`, `subtasks` and, when present, `current`, with both plain and `--pretty` output, modelled on `test_runs_lease_has_exactly_the_five_keys_plain_and_pretty`.
14. `test_runs_shows_a_fixture_runs_progress`: a fixture run tree with known done/total and a started phase shows those exact values in `data.runs[]`. A second run with no rows shows the zero shape and `current: null`.

Verification: `uv run pytest` (full suite) must be green. There is no typecheck or lint step.
