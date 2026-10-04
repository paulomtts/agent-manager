# store.diverging: a pure journal-vs-projection comparison (subtask 80dade04)

Parent story: ddf4da2c "am status catches journal/projection divergence instead of trusting either blindly". Milestone design: `docs/superpowers/specs/2026-10-03-journal-db-divergence-design.md` §3.2 (what counts as divergence), §3.3 (the `diverging`/`Mismatch` signature and field meanings), §4 (the `store.diverging` tests). This document narrows that design to the one piece this card delivers; it adds no decisions of its own.

## Scope

In scope, and only this:

- `src/agent_manager/store.py`: a new frozen dataclass `Mismatch` and a new pure function `diverging(lines: list[JournalLine], projection: models.Run) -> list[Mismatch]`.
- `tests/test_store.py`: the six tests listed below.

Out of scope (owned by sibling subtasks; do not touch): `cli.py`, `status_for`, `integrity_view`, the `checked:false` reasons, `Journal._for_reading` wiring (f63036db); `rebuild_from_journal`'s `force` parameter, `ProjectionDivergedError`, the store.py module/`Store` docstrings, the milestone spec's §9 sentence and the README (d8943ef5).

## `Mismatch`

A frozen dataclass with exactly the five fields of milestone spec §3.3:

- `node`: the node's coordinates in the journal's own vocabulary (`JournalLine`'s / am-watch's), with keys `story`, `card`, `phase`, `attempt`. All `None` for the run; `story` = story card_id for a story; `story` + `card` for a subtask; plus `phase` (name) for a phase; plus `attempt` (n) for an attempt.
- `field`: `"status"` for a status mismatch, `None` for a shape mismatch.
- `journal`: the status the replayed journal tree has for the node, or `None` if the journal lacks the node.
- `projection`: the status the projection has for the node, or `None` if the projection lacks the node.
- `kind`: `"stale"` or `"foreign"`.

## `diverging` behaviour

1. Replay `lines` with the existing `store.replay()` (store.py:526-585) to get the journal tree. `replay`'s own errors (`JournalError`, pydantic `ValidationError`) propagate unchanged; `diverging` does not catch or translate them (turning them into a report is f63036db's job).
2. A second linear pass over `lines` (in `seq` order) collects, per node identity, the set of every status value that node was ever journaled at. The node a line describes is identified the same way `replay` identifies it: the envelope's `story`/`card`/`phase` coordinates for its ancestors and the payload's own identity field (`card_id`, `name`, `n`) for the node itself.
3. Walk the §9 tree (run, stories, subtasks, phases, attempts) of the journal tree and `projection` together, matching children by identity exactly as `replay`'s `_upsert`/`_find` do (store.py:503-524): run by itself, story by `card_id`, subtask by `(story card_id, card_id)`, phase by `name`, attempt by `n`.
4. For a node present on both sides, compare only `status`. Equal: nothing. Different: one mismatch with `field="status"`; `kind="stale"` if the projection's value is in that node's ever-journaled set, else `"foreign"`. Then recurse into its children. No other field (title, branch, worktree path, timestamps, cost, config, exit code, tokens) is ever compared.
5. A node only the journal has: one shape mismatch, `field=None`, `journal=<its status>`, `projection=None`, `kind="stale"`. A node only the projection has: one shape mismatch, `field=None`, `journal=None`, `projection=<its status>`, `kind="foreign"`. Its descendants are not reported separately (one mismatch per missing subtree root), which is what makes the "exactly one mismatch" assertions below hold.
6. Output order is tree walk order: the run, then each story in position order with its subtree depth-first (subtasks, then phases, then attempts, each in position order). Journal-tree position order first; nodes present only in the projection follow, in projection order, among their siblings.
7. Pure: no file, journal or database I/O, no mutation of `lines` or `projection` (replay works on its own models; do not mutate the caller's `projection`). Empty result means the two agree.

Pinned limitation (decision, not bug): a node set back to a status the journal recorded at an earlier seq for that same node classifies `stale`, not `foreign`.

## Tests (`tests/test_store.py`)

Tier for all six: unit (unmarked). Per CLAUDE.md the tier is chosen by what a test spawns, and the dividing line is subprocesses: these tests build the projection and journal through `Store`/`record_*` and raw `sqlite3` against `tmp_path` (as the rest of test_store.py does, module docstring lines 1-10) and spawn no `git`, `brd` or `claude`, so they are unit, not `git` or higher. Milestone spec §4 says the same ("everything below is `unit`"). Each loads lines with the journal reader and the projection with `store.load_run`, then calls `diverging`.

1. Clean run: a run recorded only through `record_*` (including a subtask resumed and re-stamped `started`, and an attempt re-recorded `harness_error`) and loaded back gives `[]`.
2. The 2026-10-03 incident: newest `run_upsert` is `escalated`, then `UPDATE runs SET status='cancelled'` over a raw connection. Exactly one mismatch: run node (all coordinates `None`), `field="status"`, `journal="escalated"`, `projection="cancelled"`, `kind="foreign"`.
3. Journal-ahead crash: the setup of `test_rebuild_picks_up_a_journal_line_whose_row_never_landed` (tests/test_store.py:965-972: `record_run`, close, `record_story` fails with `sqlite3.Error` after the journal append). Exactly one mismatch: story node `8831189b`, `field=None`, `journal="started"`, `projection=None`, `kind="stale"`.
4. Set back to an earlier journaled value: a node journaled at status A then B, projection hand-set back to A. One status mismatch, `kind="stale"` (pins the §3.2 limitation).
5. Hand-inserted subtask row with no journal line: exactly one mismatch, that subtask's node, `field=None`, `journal=None`, `kind="foreign"`.
6. Tree order: a projection with several mismatches across levels/positions (e.g. run status, a later story, an earlier story's subtask) comes out in walk order, run first, not insertion or level order.

## Verification

`uv run pytest` (no separate typecheck or lint).
