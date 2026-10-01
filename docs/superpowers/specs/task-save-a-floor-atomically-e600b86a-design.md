# Save a floor atomically with an agent-phase checkpoint (subtask e600b86a)

Parent story: 5bb73149 "The turn's identity: a floor saved with every agent-phase checkpoint". Milestone plan Task 1.1 of `docs/superpowers/plans/2026-09-27-exactly-once.md` (design: `docs/superpowers/specs/2026-09-27-exactly-once-design.md`). Note: both docs are not yet merged to master; they live in the `.claude/worktrees/docs-exactly-once` worktree. The card quotes the plan excerpt in full, and that excerpt is the authority here.

## Scope

This subtask adds only the storage primitive and the attempt-number primitive:

1. `paths.highest_attempt(run_id: str, card: str, phase: str) -> int` in `src/agent_manager/paths.py`, next to `attempt_dir`. It scans `run_dir(run_id) / card` for consecutive `{phase}.{n}` directories starting at 1, the same way `dispatch.next_attempt` does today. It returns the highest present `n`, or 0 when there are none. It creates no card or phase-attempt directory, and it works when the card directory does not exist. Like `next_attempt` today, calling it still calls `paths.run_dir(run_id)`, whose existing side effect of creating the run's own root directory is unchanged and is not what "creates no directory" refers to.
2. `dispatch.next_attempt` (dispatch.py:59-70) becomes `paths.highest_attempt(run_id, card, phase) + 1`. Its behavior and docstring intent stay the same. `dispatch.py` must still not import pygents.
3. `store.TurnFloor` is a frozen dataclass with fields `phase: str`, `loop: int`, `source_run: str` and `floor: int`. `store.Checkpoint` gains a last field `floor: TurnFloor | None = None`, so every existing constructor call stays valid.
4. `_SCHEMA` gains a new table `checkpoint_floors (run_id, card_id, seq, phase, loop, source_run, floor)`:
   - The primary key is `(run_id, card_id, seq)`.
   - It has `CHECK (floor >= 0)`.
   - It is created with `IF NOT EXISTS` like the other tables, so existing databases pick it up on open.
   - Like `checkpoints`, it is row-only and outside the journal. Nothing journals it, and `rebuild_from_journal` neither reads, writes nor clears it.
   - No existing table gains a column or changes a CHECK (M9 C1).
5. `Store.save_checkpoint(..., floor: TurnFloor | None = None)`:
   - The MAX(seq) read, the `checkpoints` insert and, when `floor` is given, the `checkpoint_floors` insert all run inside the existing `with self._lock, self._fenced():` block, in one transaction with a single `self._commit()`.
   - The existing try/except (store.py:1219-1237) is widened to cover both inserts, so any `sqlite3.Error` from either one rolls back both, propagates unchanged and spends no seq.
   - The returned `Checkpoint` carries `floor` as given (None when omitted).
   - No new lock, connection, pragma or commit is added.
6. `latest_checkpoint`, `latest_turn_checkpoint` and the row-returning second query of `latest_open_checkpoint`:
   - Each reads `FROM checkpoints c LEFT JOIN checkpoint_floors f ON f.run_id = c.run_id AND f.card_id = c.card_id AND f.seq = c.seq`.
   - Each selects `c.*, f.phase AS floor_phase, f.loop AS floor_loop, f.source_run AS floor_source_run, f.floor AS floor_floor`.
   - Each keeps its existing WHERE and ORDER BY, with columns qualified `c.`. The subquery on `runs` is unchanged.
   - The first "newest" query in `latest_open_checkpoint` is unchanged.
   - `_checkpoint_from_row` builds a `TurnFloor` when `floor_phase` is not NULL and otherwise sets `floor=None`. A plain `sqlite3.Row` raises `IndexError`, not `None`, for a key it does not have, so this must check `"floor_phase" in row.keys()` (or equivalent) before indexing, not index unconditionally: that is what lets rows without those keys, such as other callers selecting `*` from `checkpoints` alone, also give `None` instead of raising.

Out of scope, owned by sibling 94088f7e (Task 1.2): `runtime/state.py`, `runtime/checkpoint.py` and `runtime/engine.py`, which cover computing the floor, carrying it across a resume, `Adoption`/`RunDeps.adopt`. This subtask passes no floor from any runtime caller. Existing callers keep saving floorless checkpoints.

Per "read the code as built first", the task result must say where each touched function was actually found (file:line on the m11 base) and how the plan excerpt was adapted. For example, tests use `tests/test_store.py`'s `repo` fixture, `Store.open(repo, RUN_ID)` and the `_save_checkpoint` helper (around line 1748), not the plan snippet's `opened`/`T0` names.

## Observable behavior and error paths

- A checkpoint saved with a floor reads back through all three readers with an equal `TurnFloor`. One saved without a floor reads back with `floor is None`. Old rows with no `checkpoint_floors` row also read back as `None`.
- A floor insert that fails, for example `floor=-1` hitting the CHECK, raises `sqlite3.IntegrityError`. Neither the checkpoint row nor the floor row persists, and the next save reuses the same seq.
- An invalid `reason` still fails as before and writes no floor row.
- A lost lease still raises `LeaseLostError` from `_fenced` before anything is written, with or without a floor.
- `rebuild_from_journal` leaves `checkpoint_floors` rows intact.
- `highest_attempt` returns 0 for a missing card dir and for a dir with no matching phase. It returns N for `phase.1..phase.N`, ignores other phases, and creates no card or phase-attempt directory (same as `next_attempt` today, it still leaves `paths.run_dir(run_id)` created as a side effect). `next_attempt` keeps returning the same values as today.

## Tests

Placement rule: tests mirror modules (CLAUDE.md; design spec §14). All of these are unit tests in the mirrored module test files. None belong in `tests/e2e` or `tests/runtime`.

`tests/test_paths.py` (unit, mirrors `paths.py`):
- `highest_attempt` is 0 when the card dir is missing, and no card or phase-attempt dir is created (the run root from `paths.run_dir` existing is not what this checks).
- It is 0 when the card dir exists with no matching phase.
- It returns the highest consecutive attempt and ignores other phases' directories.

`tests/test_dispatch.py` (unit, mirrors `dispatch.py`; adapt the existing `next_attempt` tests around lines 56-71):
- `next_attempt` equals `highest_attempt + 1` for none and for several existing attempts. Existing assertions stay green.

`tests/test_store.py` (unit, mirrors `store.py`):
- Saving with a floor returns a `Checkpoint` with that floor, and `latest_checkpoint`, `latest_turn_checkpoint` and `latest_open_checkpoint` each return an equal `TurnFloor`.
- Saving without a floor gives `floor is None` from all three readers. A mix of floored and floorless rows in one card reads back correctly per seq.
- `floor=-1` raises `sqlite3.IntegrityError`. No checkpoint row is left (`latest_checkpoint` is unchanged), and the next valid save gets the seq that would have been spent.
- An invalid `reason` with a floor raises `IntegrityError`, and no `checkpoint_floors` row exists.
- With a lost lease, saving with a floor raises `LeaseLostError` and writes neither row.
- `rebuild_from_journal` keeps `checkpoint_floors` rows, so a floor still reads back afterwards.
- The `checkpoint_floors` table exists after open. Existing tables' columns are unchanged, for example `checkpoints` columns via `PRAGMA table_info`.

## Verification

`uv run pytest` must pass with the whole suite green, including tests/e2e when they are run explicitly. There is no typecheck or lint step. Work on the subtask's own `m11` branch off the base with M9 and M10 merged. Nothing is pushed, and the base branch is never moved.
