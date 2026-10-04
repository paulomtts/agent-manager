# Task 5.1 — Pin the `--board` journal and plan shapes (card a7fcc076)

Parent story: 3a859c52 "am run --board contract". Source: `docs/superpowers/specs/2026-10-03-plugin-integration-design.md` §5, `docs/superpowers/specs/2026-10-01-run-board-design.md`, and `2026-10-03-merged-root-real-edges-design.md` §3.4. Sibling 5.2 (4881c933, README) is blocked by this card and reads its tests. This card writes no README text.

## Scope

This card pins, with tests written first, what an `am run --board` run records and what `am run --board --dry-run` returns. It changes production code only if a pinned test shows that the journal cannot tell a consumer which milestone a story belongs to. The tests below are expected to show that it can, so the expected outcome is tests only, with no source change.

What the tests confirm (observed from the code, to be pinned rather than assumed):

- **No board-level run.** `orchestrate.run_board` gives each milestone its own `models.Run`, run id, `journal.jsonl` and lease, just as a solo `--milestone` run does. The board payload is `{ok, board: true, levels: [{level, milestones: [ids]}], milestones: [entry...]}`. It has no top-level `run_id`. Each entry is one of `{milestone_id, status, **run payload}` (which includes `run_id`), `{milestone_id, status: "blocked", blocked_by: [...]}`, or `{milestone_id, status: "escalated", error: "Type: msg"}`.
- **`run.milestone_id`.** Each per-milestone run's `run_upsert` payload (`Store.record_run`, a dump of `Run` without `stories`) carries the full milestone card id. It is never null on a `--board` run. It stays null on `--card` runs, which is unchanged and is not this card's concern.
- **`story_upsert`.** The payload is the `StoryRun` dump without `subtasks`. The line's `story` field is the story card id. It has no milestone key, and the line's `run_id` is the per-milestone run id.
- **Synthetic ids.** `"integrate"` and `"bases"` (story ids) and `"base-<story id>"` (subtask card ids) come from the same per-milestone path as `--milestone`. They can appear in each milestone's journal, and only in that milestone's own journal, never in a shared one. Because the ids are fixed names, the same value can appear in several milestones' journals; they identify a story only together with the journal's `run_id`.
- **Dry run.** `cli.dry_run_board` returns `{board: true, max_concurrent, levels: [{level, milestones: [{milestone_id, title, branch_prefix, plan}]}]}`, where `plan` is `dry_run_payload(...)` for that milestone. It has no `ok` key inside `data`, opens no Store and writes nothing.

## Decision to pin: no new journal key

A consumer reads one run's journal at a time. That journal covers exactly one milestone, and its first line, the `run_upsert`, carries `milestone_id`. Every later line shares that line's `run_id`. So the milestone of any `story_upsert` follows from its run, and no additional key is added. A test states this reasoning in its docstring and asserts it: all lines share one `run_id`, and the `run_upsert` with the lowest `seq` carries the milestone id. The fallback is to add a `milestone_id` key to the `story_upsert` payload, and only to that payload. Use it only if a test shows a journal where the first line is not a `run_upsert` with `milestone_id`, or a journal that mixes run ids. If the fallback is used, the key is additive, the journal stays schema 1, and the tests below are extended to assert it.

## Error paths

None are new. The blocked and escalated entry shapes are pinned as listed above. A `--board` dry run on a board with no open milestones keeps its current behaviour, which the test only pins.

## Tests (write first)

1. `tests/test_orchestrate.py`, next to the `run_board` seam tests (`board_seams`). **Unit.** `_run_milestone_async` and `board.roots` are faked and nothing is spawned. Pins the board payload key set: no top-level `run_id`, and the exact key sets of the done, blocked and escalated entries. It also pins that the per-entry `run_id`s are distinct, which shows there is one run per milestone.
2. `tests/test_store.py`. **Unit.** Uses a real `Store` and `Journal` in `tmp_path` with no subprocess. Record a `Run(milestone_id=M)`, a story, and synthetic `integrate`/`bases` stories with a `base-<id>` subtask. Assert that the `run_upsert` payload has `milestone_id == M`, that `story_upsert` lines carry `story=<card id>` and no milestone key, that every line has the store's `run_id`, and that the first line is the `run_upsert`. The docstring records the "no new key" decision.
3. `tests/e2e/test_run_board.py`, added as assertions inside the existing `test_two_independent_milestones_both_finish` (one test per scenario family, so this adds no new test). **e2e_fake**, already marked. For each milestone, read `store.Journal(run_id).read()` and assert that the first `run_upsert` carries that milestone id and that every line's `run_id` equals the entry's run id. Also assert that the real story card ids found in X's journal never appear in Y's journal, and the reverse. Exclude the synthetic story ids `integrate` and `bases` (`integration.INTEGRATE_STORY_ID`, `bases.BASES_STORY_ID`) from that check: they are fixed names, so every milestone's journal that records them uses the same value. Instead assert that where the synthetic ids appear, they appear in that milestone's own journal under its own `run_id`, which the all-lines-share-one-`run_id` assertion already covers. The existing assertions in this test on distinct `run_id`s and `milestone_id` (read through `_load_run`) stay; the journal assertions are added beside them.
4. `tests/test_cli.py`, next to `test_the_board_dry_run_previews_every_open_milestone_by_level_and_writes_nothing` and its `_board_dry_run`/`_expected_board_milestone` helpers. **Unit**, using the same fake-board setup as its neighbours (if those neighbours turn out to be `brd`/`git`-marked, use the same markers). Pins the exact `data` key set `{board, max_concurrent, levels}`, the per-milestone key set `{milestone_id, title, branch_prefix, plan}`, the absence of `ok` inside `data`, and the absence of `run_id`.
5. `tests/test_cli.py`, in the board run CLI tests (~3720-3870, using `_patch_run_board`/`_board_payload`). **Unit.** Pins that the CLI envelope wraps the `run_board` payload unchanged: `{"ok": true, "data": {ok, board, levels, milestones}}`.

## Out of scope

The README (5.2), the `am runs` fields, `--detach`, `logs --follow`, `--from-now`, and any change to `--card` or `--milestone` behaviour.
