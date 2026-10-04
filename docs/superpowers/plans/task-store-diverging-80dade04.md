<!-- task-pipeline: validated -->
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

---

# store.diverging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the frozen dataclass `store.Mismatch` and the pure function `store.diverging(lines, projection)` that reports every place the SQLite projection's `status` or tree shape disagrees with the replayed journal, classified `stale` or `foreign`.

**Architecture:** `diverging` replays the lines with the existing `replay()`, makes a second pass over the same lines to collect each node's ever-journaled statuses (keyed by a `(story, card, phase, attempt)` tuple, the `JournalLine` coordinates), then walks the journal tree and the projection together level by level (`stories`/`card_id`, `subtasks`/`card_id`, `phases`/`name`, `attempts`/`n`, the same identity fields `_upsert` uses). Task 1 builds the status comparison for nodes both sides have; Task 2 adds shape mismatches (a node only one side has) and the sibling ordering rule.

**Tech Stack:** Python 3, pydantic v2 (`models`), stdlib `dataclasses`, pytest, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m19/task-store-diverging-80dade04/docs/superpowers/specs/task-store-diverging-80dade04-design.md` (prepended above), narrowing `docs/superpowers/specs/2026-10-03-journal-db-divergence-design.md` §3.2-§3.3 and §4.

**Worktree / branch:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m19/task-store-diverging-80dade04` on `m19/task-store-diverging-80dade04`. All commands below run from that directory. Nothing from sibling cards f63036db or d8943ef5 exists on this branch; do not assume or add it.

## Global Constraints

- Only two files change: `src/agent_manager/store.py` and `tests/test_store.py`.
- Do not touch `cli.py`, `status_for`, `integrity_view`, `rebuild_from_journal`, `ProjectionDivergedError`, the store.py module docstring (lines 1-12), the `Store` class docstring, any spec's §9 sentence, or the README.
- `Mismatch` has exactly five fields: `node`, `field`, `journal`, `projection`, `kind`. `node` is a dict with exactly the keys `story`, `card`, `phase`, `attempt`, all `None` for the run.
- `field` is `"status"` or `None`; `kind` is `"stale"` or `"foreign"`.
- Only `status` is compared, plus shape. Never titles, branches, paths, timestamps, cost, config, exit codes, tokens.
- `diverging` does no file, journal or database I/O and mutates neither `lines` nor `projection`. `replay`'s `JournalError` / pydantic `ValidationError` propagate unchanged.
- Every new test is unit tier: unmarked, in `tests/test_store.py`, no subprocess. Do not add `@pytest.mark.git` or any other marker.
- Verification: `uv run pytest` (no lint, no typecheck).

## Review Focus

1. A status the projection holds that the journal recorded for a *different* node (e.g. `ok`, journaled for the `explore` attempt, hand-written onto the `implement` attempt) must classify `foreign`, not `stale`: the ever-journaled set is per node, not global. Pinned by `test_diverging_classifies_against_the_nodes_own_history_at_attempt_level` in Task 1.
2. A hand-edit to a field `diverging` must not compare (story title, subtask branch, phase detail, attempt cost) must report nothing, or `am status` will cry wolf. Pinned by `test_diverging_ignores_every_field_but_status` in Task 1.
3. A journal that `replay` cannot fold (no `run_upsert`, or a payload that fails `models` validation) must raise the same `JournalError` / `ValidationError` replay raises, not an empty list that reads as "clean". Pinned by `test_diverging_lets_replays_errors_through_unchanged` in Task 1.
4. A projection missing a whole subtree (subtask row deleted, its phase/attempt rows orphaned) must report exactly one mismatch at the subtree root, not one per descendant. Pinned by `test_diverging_reports_a_missing_subtree_once_at_its_root` in Task 2.
5. Callers (f63036db, d8943ef5) pass the live projection they will go on to use; `diverging` mutating it or the lines would corrupt their later output. Pinned by `test_diverging_mutates_neither_its_lines_nor_its_projection` in Task 1.

---

### Task 1: `Mismatch` and the status comparison

**Files:**
- Modify: `src/agent_manager/store.py` (insert a new block between the end of `replay()` at line 585 and `class RunSummary` at line 588)
- Test: `tests/test_store.py` (append at the end of the file, after `test_a_taken_over_store_neither_marks_nor_fails_a_comment`, which ends at line 4644)

**Interfaces:**
- Consumes: `store.replay(lines: Iterable[JournalLine]) -> models.Run` (store.py:526), `store.JournalLine` (store.py:288), `models.Run/StoryRun/SubtaskRun/PhaseRun/Attempt` (each with `.status`). Test helpers already in `tests/test_store.py`: `repo` fixture (line 34), `RUN_ID` (line 31), `_dispatch` (line 44), `_run` (line 55), `_story` (line 599, card `8831189b`, status `started`), `_subtask` (line 609, status `started`), `_record_full_run` (line 773: run `started`; story `8831189b`; subtask `fdebc746` `done` with phase `verify` `done`; subtask `ef248597` `started` with phase `explore` `done` + attempt 1 `ok`, phase `implement` `started` + attempt 1 `started`).
- Produces: `store.Mismatch(node: dict[str, str | int | None], field: Literal["status"] | None, journal: str | None, projection: str | None, kind: Literal["stale", "foreign"])`; `store.diverging(lines: list[JournalLine], projection: models.Run) -> list[Mismatch]`; private helpers `_NodeKey`, `_RUN_KEY`, `_LEVELS`, `_coords`, `_child_key`, `_walk` that Task 2 edits. Test helpers `_node`, `_raw_sql`, `_diverging_now` that Task 2 reuses.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_store.py`:

```python
# -- store.diverging (journal/DB divergence §3.2-§3.3) ------------------------
#
# Unit tier: real sqlite and journal files under tmp_path, no subprocess.


def _node(
    story: str | None = None,
    card: str | None = None,
    phase: str | None = None,
    attempt: int | None = None,
) -> dict[str, str | int | None]:
    return {"story": story, "card": card, "phase": phase, "attempt": attempt}


def _raw_sql(repo: Path, sql: str, params: tuple = ()) -> None:
    """Write the projection behind the store's back, as a hand-edit would."""
    conn = sqlite3.connect(paths.project_db_path(repo))
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _diverging_now(repo: Path) -> list[store.Mismatch]:
    """Load the journal and the projection the way a caller would, and compare."""
    lines = store.Journal(RUN_ID).read()
    conn = store.open_db(repo)
    try:
        projection = store.load_run(conn, RUN_ID)
    finally:
        conn.close()
    assert projection is not None
    return store.diverging(lines, projection)


def test_diverging_finds_nothing_in_a_run_recorded_only_through_the_store(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
        resumed = _subtask("ef248597", base="m1/task-fdebc746")
        st.record_subtask("8831189b", resumed.model_copy(update={"status": "stopped"}))
        st.record_subtask("8831189b", resumed)  # resumed: re-stamped `started`
        st.record_attempt(
            "8831189b",
            "ef248597",
            "implement",
            models.Attempt(
                n=1,
                dispatch=_dispatch(card="ef248597", phase="implement"),
                status="harness_error",
                exit_code=1,
            ),
        )
        st.record_run(_run(repo).model_copy(update={"status": "escalated"}))
    finally:
        st.close()

    assert _diverging_now(repo) == []


def test_diverging_reports_the_2026_10_03_incident_as_a_foreign_run_status(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
        st.record_run(_run(repo).model_copy(update={"status": "escalated"}))
    finally:
        st.close()
    _raw_sql(repo, "UPDATE runs SET status = 'cancelled' WHERE id = ?", (RUN_ID,))

    assert _diverging_now(repo) == [
        store.Mismatch(
            node=_node(),
            field="status",
            journal="escalated",
            projection="cancelled",
            kind="foreign",
        )
    ]


def test_diverging_classifies_a_status_set_back_to_an_earlier_journaled_value_stale(repo):
    # Pinned decision (§3.2 known limit): indistinguishable from a crash.
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
        st.record_story(_story())
        st.record_story(_story().model_copy(update={"status": "done"}))
    finally:
        st.close()
    _raw_sql(repo, "UPDATE stories SET status = 'started' WHERE card_id = '8831189b'")

    assert _diverging_now(repo) == [
        store.Mismatch(
            node=_node(story="8831189b"),
            field="status",
            journal="done",
            projection="started",
            kind="stale",
        )
    ]


def test_diverging_classifies_against_the_nodes_own_history_at_attempt_level(repo):
    # `ok` was journaled for the explore attempt, never for the implement one.
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
    finally:
        st.close()
    _raw_sql(repo, "UPDATE attempts SET status = 'ok' WHERE phase = 'implement' AND n = 1")

    assert _diverging_now(repo) == [
        store.Mismatch(
            node=_node(story="8831189b", card="ef248597", phase="implement", attempt=1),
            field="status",
            journal="started",
            projection="ok",
            kind="foreign",
        )
    ]


def test_diverging_ignores_every_field_but_status(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
    finally:
        st.close()
    _raw_sql(repo, "UPDATE runs SET workflow = 'task', base_branch = 'develop'")
    _raw_sql(repo, "UPDATE stories SET title = 'hand-edited', tip_branch = NULL, level = 3")
    _raw_sql(repo, "UPDATE subtasks SET branch = 'elsewhere', worktree_path = NULL")
    _raw_sql(repo, "UPDATE phases SET detail = 'hand-edited', ended_at = NULL")
    _raw_sql(repo, "UPDATE attempts SET cost = 9.5, exit_code = 42, tokens_in = 7")

    assert _diverging_now(repo) == []


def test_diverging_lets_replays_errors_through_unchanged(repo):
    projection = _run(repo)
    with pytest.raises(store.JournalError, match="no run_upsert"):
        store.diverging([], projection)

    headless = store.JournalLine(
        seq=1,
        ts=datetime(2026, 10, 3, tzinfo=timezone.utc),
        run_id=RUN_ID,
        event="story_upsert",
        story="8831189b",
        payload=_story().model_dump(mode="json", exclude={"subtasks"}),
    )
    with pytest.raises(store.JournalError, match="no run_upsert preceded it"):
        store.diverging([headless], projection)

    malformed = store.JournalLine(
        seq=1,
        ts=datetime(2026, 10, 3, tzinfo=timezone.utc),
        run_id=RUN_ID,
        event="run_upsert",
        payload={"id": RUN_ID},
    )
    with pytest.raises(ValidationError):
        store.diverging([malformed], projection)


def test_diverging_mutates_neither_its_lines_nor_its_projection(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
    finally:
        st.close()
    _raw_sql(repo, "UPDATE runs SET status = 'cancelled' WHERE id = ?", (RUN_ID,))

    lines = store.Journal(RUN_ID).read()
    conn = store.open_db(repo)
    try:
        projection = store.load_run(conn, RUN_ID)
    finally:
        conn.close()
    assert projection is not None
    lines_before = [line.model_copy(deep=True) for line in lines]
    projection_before = projection.model_copy(deep=True)

    assert store.diverging(lines, projection) != []
    assert lines == lines_before
    assert projection == projection_before
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py -k diverging -v`
Expected: all 7 FAIL with `AttributeError: module 'agent_manager.store' has no attribute 'diverging'` (or `... 'Mismatch'`).

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/store.py`, insert this block immediately after the last line of `replay()` (`    return run`, line 585) and before `class RunSummary(BaseModel):` (line 588), keeping two blank lines on each side. `dataclass`, `Literal`, `Any`, `BaseModel` and `models` are already imported at the top of the file.

```python
MismatchKind = Literal["stale", "foreign"]
"""§3.2: `stale` is a value the journal recorded for that node at some seq (or a
node the projection lacks), which a rebuild repairs; `foreign` is a value no
journal line ever recorded for that node (or a node no line created), written
outside the store."""


@dataclass(frozen=True)
class Mismatch:
    """One place the projection disagrees with the replayed journal (§3.3).

    `node` is keyed by the journal's own coordinates (`story`, `card`, `phase`,
    `attempt`, as `JournalLine` and am-watch spell them), all `None` for the
    run. `field` is `"status"` for a status mismatch and `None` for a shape
    mismatch; then `journal`/`projection` is the status on the side that has
    the node and `None` on the side that lacks it.
    """

    node: dict[str, str | int | None]
    field: Literal["status"] | None
    journal: str | None
    projection: str | None
    kind: MismatchKind


_NodeKey = tuple[str | None, str | None, str | None, int | None]
"""(story, card, phase, attempt): one node of the §9 tree in `JournalLine`'s
coordinates. The run is all `None`."""

_RUN_KEY: _NodeKey = (None, None, None, None)

_NODE_MODELS: dict[str, type[BaseModel]] = {
    "run_upsert": models.Run,
    "story_upsert": models.StoryRun,
    "subtask_upsert": models.SubtaskRun,
    "phase_upsert": models.PhaseRun,
    "attempt_upsert": models.Attempt,
}
"""The model each event's payload validates as, exactly as `replay` reads it."""

_LEVELS: tuple[tuple[str, str], ...] = (
    ("stories", "card_id"),
    ("subtasks", "card_id"),
    ("phases", "name"),
    ("attempts", "n"),
)
"""Below the run, each level's child list and the field `_upsert` matches
siblings by. The identity value of a node at level `i` fills slot `i` of its
`_NodeKey`."""


def _coords(key: _NodeKey) -> dict[str, str | int | None]:
    story, card, phase, attempt = key
    return {"story": story, "card": card, "phase": phase, "attempt": attempt}


def _child_key(key: _NodeKey, depth: int, value: Any) -> _NodeKey:
    """`key` with the child's identity `value` in the slot for level `depth`."""
    slots = list(key)
    slots[depth] = value
    return (slots[0], slots[1], slots[2], slots[3])


def _line_node(line: JournalLine, node: Any) -> _NodeKey:
    """The node a line describes, located the way `replay` places it: the
    envelope names its ancestors, the payload's identity field names it."""
    if line.event == "run_upsert":
        return _RUN_KEY
    if line.event == "story_upsert":
        return (node.card_id, None, None, None)
    if line.event == "subtask_upsert":
        return (line.story, node.card_id, None, None)
    if line.event == "phase_upsert":
        return (line.story, line.card, node.name, None)
    return (line.story, line.card, line.phase, node.n)


def _journaled_statuses(lines: list[JournalLine]) -> dict[_NodeKey, set[str]]:
    """Every status each node was ever journaled at, at any seq (§3.2)."""
    seen: dict[_NodeKey, set[str]] = {}
    for line in sorted(lines, key=lambda item: item.seq):
        node: Any = _NODE_MODELS[line.event].model_validate(line.payload)
        seen.setdefault(_line_node(line, node), set()).add(node.status)
    return seen


def _walk(
    key: _NodeKey,
    depth: int,
    journal_node: Any,
    projection_node: Any,
    seen: dict[_NodeKey, set[str]],
    found: list[Mismatch],
) -> None:
    """Compare one node both sides have, then its children, in tree order."""
    if journal_node.status != projection_node.status:
        found.append(
            Mismatch(
                node=_coords(key),
                field="status",
                journal=journal_node.status,
                projection=projection_node.status,
                kind=(
                    "stale"
                    if projection_node.status in seen.get(key, set())
                    else "foreign"
                ),
            )
        )
    if depth == len(_LEVELS):
        return
    children, identity = _LEVELS[depth]
    theirs = {
        getattr(child, identity): child for child in getattr(projection_node, children)
    }
    for child in getattr(journal_node, children):
        value = getattr(child, identity)
        other = theirs.get(value)
        if other is not None:
            _walk(_child_key(key, depth, value), depth + 1, child, other, seen, found)


def diverging(lines: list[JournalLine], projection: models.Run) -> list[Mismatch]:
    """Every place `projection` disagrees with the journal `lines` replay to.

    Pure: the caller loads both sides; nothing here reads a file or the
    database, and neither argument is mutated. `replay`'s own errors
    (`JournalError`, pydantic `ValidationError`) propagate unchanged.

    Only `status` is compared, at every level of the §9 tree, plus shape.
    Nodes are matched as `replay` matches them: the run by itself, a story by
    `card_id`, a subtask by its story and `card_id`, a phase by `name`, an
    attempt by `n`. A differing status is `stale` if the journal ever recorded
    the projection's value for that node, else `foreign` (§3.2; a node set
    back to an earlier journaled status is therefore `stale`, by decision).
    Mismatches come out in tree walk order. An empty list means they agree.
    """
    lines = list(lines)
    journal = replay(lines)
    seen = _journaled_statuses(lines)
    found: list[Mismatch] = []
    _walk(_RUN_KEY, 0, journal, projection, seen, found)
    return found
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -k diverging -v`
Expected: all 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat(store): add Mismatch and diverging's status comparison"
```

---

### Task 2: Shape mismatches and walk order

**Files:**
- Modify: `src/agent_manager/store.py` (the `_walk` function added in Task 1, its final `for` loop)
- Test: `tests/test_store.py` (append after `test_diverging_mutates_neither_its_lines_nor_its_projection`, the last test Task 1 added)

**Interfaces:**
- Consumes (from Task 1): `store.Mismatch(node=..., field=..., journal=..., projection=..., kind=...)`, `store.diverging`, and in store.py the helpers `_NodeKey`, `_LEVELS`, `_coords(key)`, `_child_key(key, depth, value)`, `_walk(key, depth, journal_node, projection_node, seen, found)`. Test helpers `_node(story=None, card=None, phase=None, attempt=None)`, `_raw_sql(repo, sql, params=())`, `_diverging_now(repo)`, plus existing `_run`, `_story`, `_subtask`, `_record_full_run`, `RUN_ID`, `repo`.
- Produces: final `diverging` behaviour; nothing further depends on it within this card (siblings f63036db/d8943ef5 consume `store.diverging` and `store.Mismatch`).

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_store.py`:

```python
def test_diverging_reports_a_journal_line_whose_row_never_landed_as_stale_shape(repo):
    # The setup of test_rebuild_picks_up_a_journal_line_whose_row_never_landed:
    # the journal line is appended, the row write fails on the closed connection.
    st = store.Store.open(repo, RUN_ID)
    st.record_run(_run(repo))
    st.close()
    with pytest.raises(sqlite3.Error):
        st.record_story(_story())

    assert _diverging_now(repo) == [
        store.Mismatch(
            node=_node(story="8831189b"),
            field=None,
            journal="started",
            projection=None,
            kind="stale",
        )
    ]


def test_diverging_reports_a_hand_inserted_subtask_as_foreign_shape(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
        st.record_story(_story())
    finally:
        st.close()
    _raw_sql(
        repo,
        "INSERT INTO subtasks (run_id, story_id, card_id, branch, base_branch,"
        " status, worktree_path, position) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
        (RUN_ID, "8831189b", "deadbeef", "m1/task-deadbeef", "main", "done", 0),
    )

    assert _diverging_now(repo) == [
        store.Mismatch(
            node=_node(story="8831189b", card="deadbeef"),
            field=None,
            journal=None,
            projection="done",
            kind="foreign",
        )
    ]


def test_diverging_reports_a_missing_subtree_once_at_its_root(repo):
    # The subtask row goes; its phase and attempt rows are left orphaned, so
    # load_run never reaches them. One mismatch, not one per descendant.
    st = store.Store.open(repo, RUN_ID)
    try:
        _record_full_run(st, repo)
    finally:
        st.close()
    _raw_sql(repo, "DELETE FROM subtasks WHERE card_id = 'ef248597'")

    assert _diverging_now(repo) == [
        store.Mismatch(
            node=_node(story="8831189b", card="ef248597"),
            field=None,
            journal="started",
            projection=None,
            kind="stale",
        )
    ]


def test_diverging_reports_mismatches_in_tree_walk_order(repo):
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(_run(repo))
        st.record_story(_story())
        st.record_subtask("8831189b", _subtask())
        st.record_story(
            _story().model_copy(update={"card_id": "c0ffee12", "title": "Second story"})
        )
    finally:
        st.close()
    # Written deepest-last-first and out of tree order on purpose.
    _raw_sql(repo, "UPDATE stories SET status = 'cancelled' WHERE card_id = 'c0ffee12'")
    _raw_sql(repo, "UPDATE subtasks SET status = 'failed' WHERE card_id = 'ef248597'")
    _raw_sql(repo, "UPDATE runs SET status = 'done' WHERE id = ?", (RUN_ID,))
    # Position -1 sorts it first in the projection; only-in-projection
    # siblings still come after every journal sibling.
    _raw_sql(
        repo,
        "INSERT INTO stories (run_id, card_id, title, level, status, tip_branch,"
        " position) VALUES (?, 'feedface', 'Hand-made', 0, 'pending', NULL, -1)",
        (RUN_ID,),
    )

    assert _diverging_now(repo) == [
        store.Mismatch(
            node=_node(), field="status", journal="started", projection="done",
            kind="foreign",
        ),
        store.Mismatch(
            node=_node(story="8831189b", card="ef248597"), field="status",
            journal="started", projection="failed", kind="foreign",
        ),
        store.Mismatch(
            node=_node(story="c0ffee12"), field="status", journal="started",
            projection="cancelled", kind="foreign",
        ),
        store.Mismatch(
            node=_node(story="feedface"), field=None, journal=None,
            projection="pending", kind="foreign",
        ),
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py -k diverging -v`
Expected: the 4 new tests FAIL on their `assert` (the first three get `[]`, the walk-order test gets only the first three mismatches, missing the `feedface` one); the 7 Task 1 tests still PASS.

- [ ] **Step 3: Write the implementation**

In `src/agent_manager/store.py`, in `_walk`, replace this loop (the last lines of the function as Task 1 wrote it):

```python
    for child in getattr(journal_node, children):
        value = getattr(child, identity)
        other = theirs.get(value)
        if other is not None:
            _walk(_child_key(key, depth, value), depth + 1, child, other, seen, found)
```

with:

```python
    # One mismatch per missing subtree root: its descendants are not walked.
    journal_ids: set[Any] = set()
    for child in getattr(journal_node, children):
        value = getattr(child, identity)
        journal_ids.add(value)
        other = theirs.get(value)
        if other is None:
            found.append(
                Mismatch(
                    node=_coords(_child_key(key, depth, value)),
                    field=None,
                    journal=child.status,
                    projection=None,
                    kind="stale",
                )
            )
        else:
            _walk(_child_key(key, depth, value), depth + 1, child, other, seen, found)
    # Nodes only the projection has follow the journal's, in projection order.
    for child in getattr(projection_node, children):
        value = getattr(child, identity)
        if value not in journal_ids:
            found.append(
                Mismatch(
                    node=_coords(_child_key(key, depth, value)),
                    field=None,
                    journal=None,
                    projection=child.status,
                    kind="foreign",
                )
            )
```

Also update `_walk`'s docstring line from `"""Compare one node both sides have, then its children, in tree order."""` to:

```python
    """Compare one node both sides have, then its children, in tree order.

    A child only the journal has is one `stale` shape mismatch; a child only
    the projection has is one `foreign` shape mismatch, listed after the
    journal's children. Neither's descendants are reported.
    """
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -k diverging -v`
Expected: all 11 PASS.

- [ ] **Step 5: Run the full default suite**

Run: `uv run pytest`
Expected: PASS, no failures or errors (unit + git tiers; the new tests run here because they are unmarked).

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat(store): report shape mismatches in diverging, in tree walk order"
```

---

## Spec coverage map

- `Mismatch`, five fields, node coordinates: Task 1 Step 3; asserted by every test's expected `store.Mismatch(...)`.
- Behaviour 1 (replay, errors propagate): Task 1, `test_diverging_lets_replays_errors_through_unchanged`.
- Behaviour 2 (second pass, per-node ever-journaled set): Task 1 `_journaled_statuses`; `test_diverging_classifies_against_the_nodes_own_history_at_attempt_level`.
- Behaviour 3 (identity matching): Task 1 `_LEVELS`/`_walk`; exercised at run, story, subtask and attempt level across the tests.
- Behaviour 4 (status only): Task 1, `test_diverging_ignores_every_field_but_status`.
- Behaviour 5 (shape, one per subtree root): Task 2, crash-case, hand-inserted subtask and missing-subtree tests.
- Behaviour 6 (walk order, projection-only after): Task 2, `test_diverging_reports_mismatches_in_tree_walk_order`.
- Behaviour 7 (pure): Task 1, `test_diverging_mutates_neither_its_lines_nor_its_projection`.
- Spec tests 1-6: clean run (Task 1), incident (Task 1), crash case (Task 2), set back (Task 1), hand-inserted subtask (Task 2), tree order (Task 2). All unmarked, in `tests/test_store.py`.
