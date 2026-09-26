# Subtask 3d4947e8: Add the checkpoints table to the store

Parent story: a6c7bff3 "Checkpoints and resume" (milestone 84c3b532). Source of truth: plan Task 4.1 (`docs/superpowers/plans/2026-09-25-pygents-engine.md:1162-1176`) and the pygents-engine spec §6 (`docs/superpowers/specs/2026-09-25-pygents-engine-design.md:262-303`). If the two disagree, follow the plan's Interfaces block. The spec's prose at line 278 writes `latest_checkpoint(run_id, card_id)`, but the plan and this card both give `latest_checkpoint(card_id)` bound to the store's own run.

## Scope

Only `src/agent_manager/store.py` and `tests/test_store.py` change.

- Append the spec §6 `CREATE TABLE IF NOT EXISTS checkpoints (...)` to `_SCHEMA`, using the same text as spec lines 265-275 and the same style as the existing tables (`store.py:27-93`). The table has columns `run_id`, `card_id`, `seq`, `workflow`, `digest`, `reason` (with `CHECK (reason IN ('turn','parked','done','escalated'))`), `agent` and `saved_at`, and `PRIMARY KEY (run_id, card_id, seq)`. `open_db` already applies the schema idempotently, so it needs no other change.
- Add `Checkpoint`, a frozen plain dataclass (it is internal state, so not Pydantic). Its fields are `run_id: str`, `card_id: str`, `seq: int`, `workflow: str`, `digest: str`, `reason: str`, `agent: dict` and `saved_at: datetime`.
- Add three public methods to `Store`. Each one holds `self._lock` for its whole body, as the `record_*` methods do.
  - `save_checkpoint(card_id, *, workflow, digest, reason, agent, saved_at) -> Checkpoint` inserts one row under `self.run_id` and commits it. It assigns `seq` itself: 0 for the first row of `(self.run_id, card_id)`, and one more than the current maximum after that. `agent` is stored as `json.dumps(agent, sort_keys=True)`. `saved_at` is stored as ISO-8601 text, which is how `_iso` already stores datetimes. The method returns the `Checkpoint` it wrote.
  - `latest_checkpoint(card_id) -> Checkpoint | None` returns the row with the highest `seq` for `(self.run_id, card_id)`, or `None` if there is none. It does not filter on `reason`.
  - `latest_open_checkpoint(card_id, workflow) -> Checkpoint | None` looks across all runs. First it finds the card's newest row in any run and with any workflow, ordered by `saved_at` then `seq`. If that row's reason is `done`, it returns `None`. Otherwise it returns the newest row for that card and `workflow` whose `reason` is `turn`, `parked` or `escalated`, or `None` if there is none.
- Rows read back become `Checkpoint` values: `agent` goes through `json.loads` and `saved_at` through `datetime.fromisoformat`.

## Invariants

- Checkpoints are not part of the journal tree (G10):
  - `save_checkpoint` never touches `Journal`.
  - `EventKind` (`store.py:132-134`) gets no new kind.
  - `rebuild_from_journal` leaves `checkpoints` rows alone.
  - `SubtaskSummary`, journal lines and phase/attempt rows do not change.
- This is a deliberate, documented exception to the class docstring's rule that no public method writes a row on its own. Update the `Store` docstring so it says checkpoints are a row-only table outside the journal.
- The `agent` dict round-trips byte-equal: `json.dumps(saved.agent, sort_keys=True)` equals the stored text, and so does the value read back with `latest_checkpoint`.
- No `pygents` import, no hooks, no `ContextVar`, and nothing under `src/agent_manager/runtime/`.

## Error paths

- A `reason` outside the four allowed values is rejected by the `CHECK` constraint as `sqlite3.IntegrityError`. The error propagates unchanged, the lock is released, and no row is left behind. There is no separate Python-side validation.
- A duplicate `(run_id, card_id, seq)` cannot happen with a single writer, because `seq` is computed under the lock (P2).

## Tests (all in `tests/test_store.py`)

Every test here is in the Steps tier. That follows the placement rule in `2026-09-23-agent-manager-design.md:497-511` §14, refined by `2026-09-25-pygents-engine-design.md:379-401` §9: they run against a real temporary SQLite DB and journal, with no network and no harness dispatch. They reuse the existing `repo` fixture (`tests/test_store.py:26-40`), and they are not engine-parametrised, because store access does not depend on the engine.

1. `seq` starts at 0 and increments per card within a run. A second card starts again from 0.
2. `latest_checkpoint` returns the row with the highest `seq` for this store's run. It returns `None` for an unknown card, and it ignores another run's rows for the same card.
3. `save_checkpoint(..., reason="bogus")` raises `sqlite3.IntegrityError`, and no row is written.
4. `latest_open_checkpoint` returns an older run's `parked` row when read from a new run's store. It ignores rows for a different `workflow`.
5. `latest_open_checkpoint` returns `None` when the card's newest row across runs is `done`.
6. The `agent` dict, nested and with unsorted keys, round-trips byte-equal through JSON, both through the returned `Checkpoint` and after reading it back.
7. `rebuild_from_journal` leaves the checkpoints rows alone, and `save_checkpoint` appends no journal line.

## Out of scope

The following belong to sibling cards and must not be started here:

- `runtime/checkpoint.py`, the `BEFORE_TURN` hook, `Parked`, and the `done`/`escalated` saves (921ed349).
- `run_subtask(resume_from=...)`, `CheckpointMismatch` and `Agent.from_dict` (5698e4f6).
- `--engine` plumbing (7fdec762).
- `am resume` and milestone relaunch continuation (02890d5d).
- The supervisor tree, exactly-once phases, benchmarking prompts and upstream pygents fixes.

Do not touch `cli.py`, `orchestrate.py` or `integration.py`.

## Verification

Run `uv run pytest`. The whole default suite must pass, including `tests/e2e`. There are no typecheck or lint commands. Commit as `feat(store): add the checkpoints table` only when asked to.
