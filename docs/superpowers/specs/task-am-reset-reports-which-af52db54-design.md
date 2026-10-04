# am reset reports which cards a relaunch would still continue (af52db54)

Subtask of story 816cb8b3 ("am reset closes a run nobody is driving"), milestone 19. Narrows `docs/superpowers/specs/2026-10-03-am-reset-design.md` §3.5 and §4 test 10, plus the `cards` half of test 1. Builds on the landed sibling 736d6728 (`am reset RUN_ID`). Its `reset_run` emits a `"cards": []` placeholder marked `# af52db54 fills this in from the run's checkpoints.` (`src/agent_manager/cli.py:2408-2409`).

## Scope

In scope:

1. Add `Store.checkpoint_cards(run_id: str) -> list[tuple[str, str]]` in `src/agent_manager/store.py`. It is a read-only query that returns every distinct `(card_id, workflow)` pair that has at least one `checkpoints` row whose `run_id` is the given run.
   - Any `reason` counts, including `done`.
   - The order is deterministic: `card_id`, then `workflow`.
   - It writes nothing and runs under `self._lock`, like the other readers.
   - It takes `run_id` explicitly and does not use `self.run_id`.
2. In `reset_run` (`src/agent_manager/cli.py`), replace the placeholder with the real `cards` computation:
   - Compute it while the store is still open (before `store.close()`), after the `run_lease` block. That is after the single `record_run` write, or after the no-op in the already-cancelled case.
   - For each `(card_id, workflow)` from `store.checkpoint_cards(run.id)`, call `cp = store.latest_open_checkpoint(card_id, workflow)`.
   - Emit `{"card_id": card_id, "workflow": workflow, "open_in": cp.run_id if cp is not None and cp.run_id != run.id else None}`.
   - Keep `cards` in the order `checkpoint_cards` returns.

Out of scope (leave untouched):

- Sibling 736d6728 owns the command, lease, fencing, takeover, refusals, the already-cancelled no-op, `message` and `took_over`.
- Sibling 522adfb5 owns the relaunch and resume wiring tests, the `worktree.ensure` git companion, the `e2e_fake` incident replay, and the README.
- Resolving the chain case is out of scope (no `--cascade`). It is only reported.

## Observable behavior

- The envelope's `cards` is a list of `{"card_id", "workflow", "open_in"}`. All other envelope keys are unchanged.
- `open_in` is `null` in the ordinary case. The card's newest row belonged to the reset run, which is now `cancelled`, so the newest-row rule in `latest_open_checkpoint` (`store.py:1555-1588`) closes it.
- `open_in` names another run only when `latest_open_checkpoint` still returns a row from a different run. This is the chain case: the reset run adopted that run's checkpoint and crashed before writing a newer row, or the card's newest row is another run's under a different workflow.
- `open_in` names the run the next relaunch would continue from. It does not claim that run's worktree exists, and no filesystem check is made.
- `open_in` comes only from `latest_open_checkpoint`. The adoption logic is not re-derived.
- A run with no checkpoint rows reports `cards: []`.
- An already-cancelled run still reports `cards`, computed the same way.
- No checkpoint row is written or deleted. The reset's only write is still the existing `record_run`.

## Error paths

There are no new error paths:

- Every refusal (`UnknownRunError`, `RunIsLiveError`, `NotResettableError`) raises before `cards` is computed, so it is unaffected.
- A store read error while computing `cards` propagates like any other store error. The status write has already committed by then.

## Tests

All tests are driven through the `projection` fixture in `tests/test_cli.py` or the store directly. Checkpoints are seeded through the store, and `brd`/`git`/`claude` are stubbed off `PATH`. Nothing spawns a subprocess, so under the CLAUDE.md placement rule ("chosen by what it actually spawns or touches") every test is `unit` tier and unmarked.

1. **`checkpoint_cards` returns only this run's distinct pairs** (`unit`). Seed:
   - several rows for one card and workflow (`turn`, `parked`, `done`);
   - a second workflow for the same card;
   - rows under another run.

   Assert the result is exactly this run's distinct pairs, in sorted order, and that an unknown run returns `[]`. Place it with the existing store checkpoint-query tests.
2. **Existing reset test 1 updated** (`unit`). `test_reset_records_a_stopped_run_cancelled_through_one_journal_line` currently asserts `"cards": []`. Change it to assert that the run's card appears with its `workflow` and `open_in: null`, in both the worktree-present and worktree-removed passes. The spec §4 test 7 case (no checkpoints) keeps asserting `cards: []`.
3. **Chain case: the newest row decides** (`unit`, spec test 10).
   - Seed: run Y holds an open row for card c, and run X holds a newer open row for c in the same workflow.
   - Reset X and assert `cards` lists c with `open_in: null`.
   - Assert `runs.continuable_checkpoint` (`runs.py:216`) returns `None` for c even though Y's row is still open.
   - Uses two stores on one database, the idiom from `tests/test_control.py`.
4. **Chain variant: a newer row under `bases`** (`unit`, spec test 10).
   - Seed, oldest first:
     1. Y holds an open `task` row for c (the row X adopted).
     2. X holds a `turn` row for c under `task`.
     3. Y holds the newest open row for c under `bases`.
   - Reset X. The newest row is now Y's, which is open, so `cards` reports `{"card_id": c, "workflow": "task", "open_in": Y}`.
   - Then reset Y. Resetting X again reports `open_in: null`, because the newest row now belongs to a cancelled run. Y's own reset also reports `null` for both of its pairs.
5. **An already-cancelled reset still reports `cards`** (`unit`). A second reset of the same run returns `already_cancelled: true` with the same `cards` as the first, and the journal length is unchanged.

## Verification

Run `uv run pytest` (the default `unit` + `git` tiers). There is no separate lint or typecheck command.
