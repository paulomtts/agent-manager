<!-- task-pipeline: validated -->
# Spec (verbatim)

# Subtask 3c39ae43 — Add `status` and `runs`

Parent story: 1a46ab5a "The CLI: run, status, logs, resume" (milestone 352e955b). Depends on cbe34d00 (`run --card`, done). Narrows design §10 "CLI surface" (`agent-manager status [<run-id>]`, `agent-manager runs`) and §9 (the Run → StoryRun → SubtaskRun → PhaseRun → Attempt tree) to two read-only commands.

## Scope

Two new Typer commands in `src/agent_manager/cli.py`, plus the one projection query they need in `src/agent_manager/store.py`.

- `agent-manager status [<run-id>] [--repo-dir .] [--pretty]` — renders one run as a table of story / subtask / phase / attempt / state, read from the SQLite projection at `paths.project_db_path(resolve_repo_dir(repo_dir))`. With no `<run-id>`, it renders the most recent run recorded for that project.
- `agent-manager runs [--repo-dir .] [--pretty]` — lists that project's run history, newest first.
- A project-scoped read on the existing shared `runs` table (`store.py:26-36`), independent of any single run id: a listing of run summaries ordered newest first, and the latest run's id derived from that same listing. It must be reachable without `Store.open(root, run_id)`, which requires a run id the caller does not yet have (`store.py:328-330`), and it must open the database through the existing `open_db(root)` (`store.py:95-107`). No second database, no new tables, no new columns.

Not in scope: `logs` (sibling be23d3c0), `resume` (sibling 5524ae72), `watch`/`retry`/`cancel`, milestone orchestration (census, levels, parallel stories, integrate), non-Claude harnesses, any write to the projection or the journal, and any journal reading — `status` reads the projection, which is what the projection exists for.

## Observable behaviour

Both commands honour the existing envelope helpers unchanged: `ok_envelope` / `error_envelope` / `render` (`cli.py:102-121`), JSON on one line by default and indented under `--pretty`. Both are read-only, so the only exit codes are `0` on success and `EXIT_ERROR` (3) on a handled failure; `EXIT_ESCALATED` (1) is not used — an escalated run reported by `status` is a truthful reading, not a failure of the command. `2` stays Typer's.

`--repo-dir` is resolved through `resolve_repo_dir` before anything is derived from it (`cli.py:67-79`), exactly as `run_card` does.

`status` succeeds with `ok: true` and a payload that carries the run's own identity (id, workflow, repo_dir, base_branch, branch_prefix, status, started_at) and the nested walk of the §9 tree as `Store.load_run` already assembles it (`store.py:579-661`): stories in `position` order with `card_id`, `title`, `level`, `status`, `tip_branch`; their subtasks with `card_id`, `branch`, `base_branch`, `status`, `worktree_path`; their phases with `name`, `kind`, `status`, `started_at`, `ended_at`, `detail`; their attempts in `n` order with `n`, `status`, `exit_code`, `duration`, `tokens_in`, `tokens_out`, `cost`. Field names are `models.py`'s and are not renamed for display. A run whose walk died on its first phase renders what exists — a story and a subtask with no phases is a legitimate reading, not an error.

The "table" of §10 is a projection of that tree into flat rows, one row per attempt (and one row per phase with no attempts yet, so a `pending` or `started` deterministic phase is visible), each row carrying the story, subtask, phase, attempt number and state it belongs to. The row-building is a pure function over a `models.Run` and returns plain data; the command only renders it inside the envelope.

`runs` succeeds with `ok: true` and a payload listing every run in the project's database, newest first, each entry carrying at least `id`, `workflow`, `status` and `started_at`. Ordering is by `started_at` descending with the run id as the tiebreaker, and both are sortable UTC strings by construction (`cli.py:41-42`, `RUN_ID_TIME_FORMAT`). An empty database is `ok: true` with an empty list, not an error: a project that has never been run is a fact, not a fault.

`status` with no argument resolves the run id from that same newest-first listing and then renders it, so the two commands can never disagree about which run is "most recent".

## Error paths

All handled failures go through the existing `HANDLED` tuple pattern (`cli.py:324-337`): an `ok: false` envelope naming the exception class and message, exit 3. Anything outside the tuple is a bug and must crash with its real traceback.

- `--repo-dir` naming something that is not a directory → `RepoDirError` (already in `HANDLED` via `CliError`), exit 3. Applies to both commands.
- `status <run-id>` for a run id absent from this project's projection → a new `CliError` subclass (so it inherits the envelope and exit 3) whose message names the run id and the project it was looked for in. `Store.load_run` returns `None` for this (`store.py:585-587`) and `None` must not reach the renderer.
- `status` with no argument against a project with no runs at all → the same class of refusal, distinct message: there is no most-recent run to default to. This is the one case where an empty history is an error, because the command was asked for a run and there is none.
- A projection row that no longer validates against `models` → pydantic's `ValidationError`, which is not in `HANDLED` and must crash loudly: a drifted projection is a bug in this program, and §9's answer is to rebuild from the journal, not to print half a tree.

The database is opened read-only in effect — no `record_*` call appears in either command — and is closed on every path, including the failure paths, the way `run_card` closes its store in a `finally` (`cli.py:320-321`).

## Tests

Tier names are design §14 (`docs/superpowers/specs/2026-09-23-agent-manager-design.md:477-492`) as applied by `tests/test_cli.py:1-13` and `tests/test_store.py:1-11`.

Unit tier (pure, no filesystem, no subprocess) — in `tests/test_cli.py`:

1. The flat row builder turns a hand-built `models.Run` with two stories, subtasks, phases and attempts into rows in tree order, one per attempt.
2. The row builder emits a row for a phase with no attempts yet, so an in-flight or pending phase is not invisible.
3. The row builder on a `models.Run` with no stories yields no rows and does not raise.
4. The `status` payload of a hand-built `models.Run` survives `render` — it carries `Path` values (`repo_dir`, `worktree_path`) and `render`'s `default=str` must keep them from turning a successful read into a traceback.

Steps tier (real temp SQLite projection under `XDG_DATA_HOME` redirected to `tmp_path`, real rows written via `Store.record_*`, no mocks, no harness, default suite) — in `tests/test_store.py` for the query, `tests/test_cli.py` for the commands:

5. The new listing returns every run recorded in the project's database, newest first, and only those runs (a second project root, hence a second database, is not visible).
6. The new listing on a database with no runs returns an empty list.
7. The latest-run resolution picks the newest of three recorded runs, and agrees with the head of the listing.
8. `status <run-id>` on a projection populated with a run, story, subtask, phases and attempts prints `ok: true` and a payload whose rows cover every attempt recorded, exit 0.
9. `status` with no run id renders the most recently recorded run of that project.
10. `status <run-id>` for an unknown id prints `ok: false` with the refusal's class name, exit 3.
11. `status` with no run id against an empty project prints `ok: false`, exit 3.
12. `status --repo-dir <not a directory>` prints `ok: false` with `RepoDirError`, exit 3 — asserted for `runs` as well.
13. `runs` prints the project's history newest first, exit 0; with no runs it prints `ok: true` and an empty list, exit 0.
14. `--pretty` indents the envelope for both commands and the parsed JSON is identical to the unpretty form.
15. `status` on a run recorded with a story and subtask but no phases (a run that died before its first phase) prints `ok: true` with an empty row list — the §9 reason `run_card` writes those rows before the walk starts (`cli.py:228-230`).

No Engine tier test is needed here: neither command constructs a runner, a workflow or a dispatch. No adapter, launcher or end-to-end test is in this subtask's scope.

---

# `status` and `runs` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two read-only Typer commands — `agent-manager status [<run-id>]` and `agent-manager runs` — that render the existing per-project SQLite projection as brd-shaped JSON envelopes.

**Architecture:** One new project-scoped query pair in `store.py` (`list_runs`, `latest_run_id`) reading the already-shared `runs` table through `open_db(root)`, plus a module-level `load_run(conn, run_id)` that `Store.load_run` delegates to — so the CLI can read a run's tree from a bare connection without `Store.open(root, run_id)`, which needs a run id the caller does not have and which would mint a run artifact directory as a side effect (`Journal.__init__` calls `paths.run_dir`, which `mkdir`s). On top of that, `cli.py` gains one pure row builder, one pure payload builder, two thin reader functions that open and close the connection in a `finally`, and two Typer commands that reuse `ok_envelope`/`error_envelope`/`render`/`HANDLED` unchanged.

**Tech Stack:** Python 3.12, Typer 0.27.2, Pydantic 2.x, stdlib `sqlite3`, pytest 9 (`uv run pytest`).

**Spec:** `docs/superpowers/specs/task-add-status-and-runs-3c39ae43-design.md` (reproduced verbatim above).

## Global Constraints

- Branch `m1/task-add-status-and-runs-3c39ae43`, worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/m1/task-add-status-and-runs-3c39ae43`, cut from `origin/m1/task-add-run-card-end-to-end-cbe34d00`. No other subtask's code exists on this branch — do not import `logs` or `resume` helpers.
- Verification command, the only one this repo has: `uv run pytest`. There is no separate lint or typecheck command (CLAUDE.md).
- Source under `src/agent_manager/`, tests mirror it under `tests/` (CLAUDE.md).
- Pydantic models for anything validated at a process boundary; plain dataclasses only for internal-only state (CLAUDE.md).
- CLI output is the brd envelope `{"ok": true, "data": ...}`, JSON one line by default, indented under `--pretty` (CLAUDE.md, `cli.py:102-121`).
- Exit codes: `0` success, `1` `EXIT_ESCALATED` (unused by these two commands), `2` Typer's own usage errors, `3` `EXIT_ERROR` for every handled failure (`cli.py:35-39`).
- `cli.py` "composes and renders; it decides nothing a collaborator already decides" (`cli.py:1-8`): no step logic, no gate logic, no run state written anywhere but through `Store`. Neither new command calls any `record_*`.
- No new tables, no new columns, no second database. Both commands reach SQLite through the existing `store.open_db(root)` and `paths.project_db_path(root)`.
- Anything outside the `HANDLED` tuple (`cli.py:324-337`) must crash with its real traceback — in particular pydantic's `ValidationError` on a drifted projection row.

## Review Focus

- `status` for a run id that was never recorded must not create `~/.local/share/agent-manager/runs/<id>/` as a side effect of looking it up — `Journal.__init__` → `paths.run_dir` `mkdir`s, which is exactly why the new read path avoids `Store.open`. Test in Task 1 (`test_load_run_of_an_unknown_id_is_none_and_creates_no_run_directory`) and Task 3 (`test_status_for_an_unknown_run_id_is_an_envelope`).
- A run row whose `started_at` is NULL (the column is nullable, `store.py:34`) must still appear in `runs` rather than sorting into the middle or crashing the ordering. Test in Task 1 (`test_list_runs_puts_a_run_with_no_start_time_last`).
- Two runs recorded within the same second share a `started_at`, so the listing must still be deterministic — the run id is the tiebreaker. Test in Task 1 (`test_list_runs_breaks_a_started_at_tie_with_the_run_id`).
- The `status` payload carries `Path` (`repo_dir`, `worktree_path`) and `datetime` (`started_at`, phase timestamps) objects, which `json.dumps` refuses without `default=str`; a successful read must not become a traceback. Test in Task 2 (`test_the_status_payload_survives_render_with_its_paths`) and Task 3 (every command test parses `result.stdout`).
- `--repo-dir` pointing at an existing *file* (not merely a missing path) is a `RepoDirError`, not an `IsADirectoryError` or a sqlite crash, for both commands. Test in Task 4 (`test_a_repo_dir_that_is_a_file_is_an_envelope_for_both_commands`).

---

### Task 1: Project-scoped run queries in `store.py`

**Files:**
- Modify: `src/agent_manager/store.py` (add `RunSummary`, `list_runs`, `latest_run_id`, module-level `load_run`; `Store.load_run` at `store.py:579-661` becomes a delegation)
- Test: `tests/test_store.py` (steps tier, per its own header at `tests/test_store.py:1-12`)

**Interfaces:**
- Consumes: `store.open_db(root)` (`store.py:95-107`), `store.Store.open`/`record_*`, `models.Run`, `models.Status`.
- Produces:
  - `store.RunSummary` — pydantic `BaseModel` with `id: str`, `workflow: str`, `repo_dir: Path`, `base_branch: str`, `branch_prefix: str`, `status: models.Status`, `started_at: datetime | None`.
  - `store.list_runs(conn: sqlite3.Connection) -> list[RunSummary]` — newest first.
  - `store.latest_run_id(conn: sqlite3.Connection) -> str | None`.
  - `store.load_run(conn: sqlite3.Connection, run_id: str) -> models.Run | None` — the module-level function `Store.load_run` now delegates to.

- [ ] **Step 1: Write the failing listing tests**

Append to `tests/test_store.py`:

```python
def _record_summary(root: Path, run_id: str, started_at: datetime | None) -> None:
    """One run row in `root`'s projection, with nothing below it."""
    opened = store.Store.open(root, run_id)
    try:
        opened.record_run(_run(root, run_id).model_copy(update={"started_at": started_at}))
    finally:
        opened.close()


def test_list_runs_returns_this_projects_runs_newest_first(repo, tmp_path):
    """The `runs` table is shared by every run of one project, so the listing is
    a read of that table alone -- and a second project is a second database file,
    which this one must not see."""
    _record_summary(repo, "run-a", datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc))
    _record_summary(repo, "run-b", datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc))
    other = tmp_path / "other-repo"
    other.mkdir()
    _record_summary(other, "run-elsewhere", datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc))

    conn = store.open_db(repo)
    try:
        summaries = store.list_runs(conn)
    finally:
        conn.close()

    assert [summary.id for summary in summaries] == ["run-b", "run-a"]
    assert summaries[0].workflow == "milestone"
    assert summaries[0].status == "started"
    assert summaries[0].started_at == datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)


def test_list_runs_on_a_project_with_no_runs_is_empty(repo):
    conn = store.open_db(repo)
    try:
        assert store.list_runs(conn) == []
    finally:
        conn.close()


def test_list_runs_puts_a_run_with_no_start_time_last(repo):
    """`runs.started_at` is nullable, so a row without one must still be listed."""
    _record_summary(repo, "run-dated", datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc))
    _record_summary(repo, "run-undated", None)

    conn = store.open_db(repo)
    try:
        summaries = store.list_runs(conn)
    finally:
        conn.close()

    assert [summary.id for summary in summaries] == ["run-dated", "run-undated"]
    assert summaries[-1].started_at is None


def test_list_runs_breaks_a_started_at_tie_with_the_run_id(repo):
    """Run ids are minted at second resolution, so two runs of one project can
    share a `started_at` and the order must still be total."""
    same = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)
    _record_summary(repo, "run-a", same)
    _record_summary(repo, "run-b", same)

    conn = store.open_db(repo)
    try:
        assert [summary.id for summary in store.list_runs(conn)] == ["run-b", "run-a"]
    finally:
        conn.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py -k list_runs -v`
Expected: FAIL, `AttributeError: module 'agent_manager.store' has no attribute 'list_runs'`

- [ ] **Step 3: Implement `RunSummary` and `list_runs`**

In `src/agent_manager/store.py`, insert immediately after the `replay` function (which ends at `store.py:314`) and before `class Store:`:

```python
class RunSummary(BaseModel):
    """One row of the shared `runs` table, without the tree hanging off it.

    A `models.Run` would be a lie here: its `stories` list would always be empty
    because `runs` is the only table read. The fields are the run's identity and
    nothing else, and they go through pydantic for the same reason `load_run`
    does -- a projection that drifted from `models` must fail loudly rather than
    print half a history.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    workflow: str
    repo_dir: Path
    base_branch: str
    branch_prefix: str
    status: models.Status
    started_at: datetime | None = None


def list_runs(conn: sqlite3.Connection) -> list[RunSummary]:
    """Every run recorded in this project's projection, newest first.

    Takes a connection rather than a root so one caller can list the history and
    then load a run's tree over the same connection, and close it once. The
    connection comes from `open_db(root)`; there is no second database.

    `started_at DESC` puts a NULL start time last (SQLite orders NULL below every
    value, so descending sends it to the end) and the id breaks a tie, which run
    ids minted at second resolution really do produce.
    """
    rows = conn.execute(
        "SELECT id, workflow, repo_dir, base_branch, branch_prefix, status, started_at"
        " FROM runs ORDER BY started_at DESC, id DESC"
    ).fetchall()
    return [RunSummary.model_validate(dict(row)) for row in rows]
```

`latest_run_id` is deliberately not written yet — Step 5 tests it first.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -k list_runs -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Write the failing latest-run tests**

Append to `tests/test_store.py`:

```python
def test_latest_run_id_is_the_newest_recorded_run(repo):
    _record_summary(repo, "run-a", datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc))
    _record_summary(repo, "run-c", datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc))
    _record_summary(repo, "run-b", datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc))

    conn = store.open_db(repo)
    try:
        assert store.latest_run_id(conn) == "run-c"
        assert store.latest_run_id(conn) == store.list_runs(conn)[0].id
    finally:
        conn.close()


def test_latest_run_id_is_none_for_a_project_with_no_runs(repo):
    conn = store.open_db(repo)
    try:
        assert store.latest_run_id(conn) is None
    finally:
        conn.close()
```

- [ ] **Step 6: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py -k latest_run_id -v`
Expected: FAIL with `AttributeError: module 'agent_manager.store' has no attribute 'latest_run_id'`

- [ ] **Step 7: Implement `latest_run_id`**

In `src/agent_manager/store.py`, append directly after `list_runs`:

```python
def latest_run_id(conn: sqlite3.Connection) -> str | None:
    """The most recent run of this project, or `None` if it has never been run.

    Derived from `list_runs` rather than from a second `ORDER BY`, so "most
    recent" can never mean two different things in two commands.
    """
    summaries = list_runs(conn)
    return summaries[0].id if summaries else None
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -k latest_run_id -v`
Expected: PASS (2 tests)

- [ ] **Step 9: Write the failing module-level `load_run` tests**

Append to `tests/test_store.py`:

```python
def test_load_run_reads_the_tree_from_a_bare_connection(repo):
    """`status` has no run id until it has read the database, so it cannot use
    `Store.open(root, run_id)` -- and must not, since a `Journal` mkdirs a run
    directory for a run that may not exist."""
    opened = store.Store.open(repo, RUN_ID)
    try:
        opened.record_run(_run(repo))
        opened.record_story(
            models.StoryRun(card_id="story-1", title="A story", level=0, status="started")
        )
        opened.record_subtask(
            "story-1",
            models.SubtaskRun(
                card_id="card-1", branch="m1/task-x", base_branch="main", status="started"
            ),
        )
    finally:
        opened.close()

    conn = store.open_db(repo)
    try:
        run = store.load_run(conn, RUN_ID)
    finally:
        conn.close()

    assert run is not None
    assert run.id == RUN_ID
    assert [story.card_id for story in run.stories] == ["story-1"]
    assert [subtask.card_id for subtask in run.stories[0].subtasks] == ["card-1"]


def test_load_run_of_an_unknown_id_is_none_and_creates_no_run_directory(repo):
    conn = store.open_db(repo)
    try:
        assert store.load_run(conn, "run-that-never-was") is None
    finally:
        conn.close()

    assert not (paths.data_dir() / "runs" / "run-that-never-was").exists()
```

- [ ] **Step 10: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py -k "load_run_reads_the_tree or unknown_id_is_none_and_creates" -v`
Expected: FAIL with `AttributeError: module 'agent_manager.store' has no attribute 'load_run'`

- [ ] **Step 11: Move `Store.load_run`'s body to a module-level function**

In `src/agent_manager/store.py`, insert this immediately after `latest_run_id` (still before `class Store:`). It is `Store.load_run`'s body from `store.py:585-661` verbatim with `self._conn` replaced by `conn`:

```python
def load_run(conn: sqlite3.Connection, run_id: str) -> models.Run | None:
    """Assemble one run's projection back into the §9 tree, or `None` if absent.

    A free function over a connection, because the reader that needs it -- the
    `status` command -- has no `Journal` and must not create one: `Journal`
    derives its path from `paths.run_dir`, which creates the directory, so
    looking up a run that does not exist through `Store.open` would leave an
    artifact directory behind for a run nobody ever started.

    Every value goes back through the `models` validators, so a projection that
    drifted from the schema fails here rather than downstream.
    """
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        return None

    run = models.Run(
        id=row["id"],
        workflow=row["workflow"],
        repo_dir=row["repo_dir"],
        base_branch=row["base_branch"],
        branch_prefix=row["branch_prefix"],
        status=row["status"],
        started_at=row["started_at"],
        config=json.loads(row["config"]),
    )

    for story_row in conn.execute(
        "SELECT * FROM stories WHERE run_id = ? ORDER BY position", (run_id,)
    ).fetchall():
        story = models.StoryRun(
            card_id=story_row["card_id"],
            title=story_row["title"],
            level=story_row["level"],
            status=story_row["status"],
            tip_branch=story_row["tip_branch"],
        )
        run.stories.append(story)

        for subtask_row in conn.execute(
            "SELECT * FROM subtasks WHERE run_id = ? AND story_id = ? ORDER BY position",
            (run_id, story.card_id),
        ).fetchall():
            subtask = models.SubtaskRun(
                card_id=subtask_row["card_id"],
                branch=subtask_row["branch"],
                base_branch=subtask_row["base_branch"],
                status=subtask_row["status"],
                worktree_path=subtask_row["worktree_path"],
            )
            story.subtasks.append(subtask)

            for phase_row in conn.execute(
                "SELECT * FROM phases WHERE run_id = ? AND story_id = ?"
                " AND card_id = ? ORDER BY position",
                (run_id, story.card_id, subtask.card_id),
            ).fetchall():
                phase = models.PhaseRun(
                    name=phase_row["name"],
                    kind=phase_row["kind"],
                    status=phase_row["status"],
                    started_at=phase_row["started_at"],
                    ended_at=phase_row["ended_at"],
                )
                subtask.phases.append(phase)

                for attempt_row in conn.execute(
                    "SELECT * FROM attempts WHERE run_id = ? AND story_id = ?"
                    " AND card_id = ? AND phase = ? ORDER BY n",
                    (run_id, story.card_id, subtask.card_id, phase.name),
                ).fetchall():
                    phase.attempts.append(
                        models.Attempt(
                            n=attempt_row["n"],
                            dispatch=json.loads(attempt_row["dispatch"]),
                            status=attempt_row["status"],
                            exit_code=attempt_row["exit_code"],
                            duration=attempt_row["duration"],
                            tokens_in=attempt_row["tokens_in"],
                            tokens_out=attempt_row["tokens_out"],
                            cost=attempt_row["cost"],
                            prompt_path=attempt_row["prompt_path"],
                            result_path=attempt_row["result_path"],
                            stdout_path=attempt_row["stdout_path"],
                        )
                    )

    return run
```

- [ ] **Step 12: Replace `Store.load_run`'s body with a delegation**

In `src/agent_manager/store.py`, replace the whole of `Store.load_run` (`store.py:579-661`, from `    def load_run(self, run_id: str) -> models.Run | None:` down to and including its closing `        return run`) with:

```python
    def load_run(self, run_id: str) -> models.Run | None:
        """The module-level `load_run` over this store's own connection.

        Kept as a method because `rebuild_from_journal` and every existing caller
        already hold a `Store`; the free function is what a reader without a run
        id uses.
        """
        return load_run(self._conn, run_id)
```

- [ ] **Step 13: Run the store suite to verify it passes**

Run: `uv run pytest tests/test_store.py -v`
Expected: PASS — including the pre-existing `test_load_run_rebuilds_the_tree_in_recorded_order` and `test_load_run_returns_none_for_an_unknown_run`, which now exercise the delegation.

- [ ] **Step 14: Run the whole suite**

Run: `uv run pytest`
Expected: PASS

- [ ] **Step 15: Commit**

```bash
git add src/agent_manager/store.py tests/test_store.py
git commit -m "feat(store): project-scoped run listing and connection-level load_run"
```

---

### Task 2: The pure row and payload builders in `cli.py`

**Files:**
- Modify: `src/agent_manager/cli.py` (add `RUN_IDENTITY`, `status_rows`, `status_payload` after `_utcnow` at `cli.py:124-125` and before `app = typer.Typer(` at `cli.py:128`)
- Test: `tests/test_cli.py` (unit tier — the file already holds unit-tier helper tests at `tests/test_cli.py:32-95`; put these beside them, above the `requires_git` marks at `tests/test_cli.py:98`)

**Interfaces:**
- Consumes: `models.Run`, `models.StoryRun`, `models.SubtaskRun`, `models.PhaseRun`, `models.Attempt`, `models.Dispatch`; `cli.render`, `cli.ok_envelope`.
- Produces:
  - `cli.RUN_IDENTITY: tuple[str, ...]` — `("id", "workflow", "repo_dir", "base_branch", "branch_prefix", "status", "started_at")`.
  - `cli.status_rows(run: models.Run) -> list[dict[str, Any]]` — each row has exactly the keys `story`, `subtask`, `phase`, `attempt`, `state`; `attempt` is `int | None`.
  - `cli.status_payload(run: models.Run) -> dict[str, Any]` — keys `run` (the `RUN_IDENTITY` fields), `stories` (the nested walk), `rows`.

- [ ] **Step 1: Write the failing row-builder tests**

Insert into `tests/test_cli.py` after `test_resolve_repo_dir_refuses_a_path_that_is_not_a_directory` (ends at `tests/test_cli.py:95`) and before the `requires_git = pytest.mark.skipif(` block:

```python
def _pure_dispatch() -> models.Dispatch:
    """A dispatch built by hand: these tests touch no filesystem at all."""
    return models.Dispatch(
        harness="claude",
        model="sonnet",
        role="coder",
        cwd=Path("/repo"),
        prompt_path=Path("/runs/prompt.txt"),
        result_path=Path("/runs/result.json"),
    )


def _pure_run(stories: list[models.StoryRun]) -> models.Run:
    return models.Run(
        id="20260923T140506Z-cbe34d00",
        workflow="task",
        repo_dir=Path("/repo"),
        base_branch="main",
        branch_prefix="m1",
        status="started",
        started_at=datetime(2026, 9, 23, 14, 5, 6, tzinfo=timezone.utc),
        stories=stories,
    )


def test_status_rows_are_one_row_per_attempt_in_tree_order():
    run = _pure_run(
        [
            models.StoryRun(
                card_id="story-1",
                title="One",
                level=0,
                status="done",
                subtasks=[
                    models.SubtaskRun(
                        card_id="card-1",
                        branch="m1/a",
                        base_branch="main",
                        status="done",
                        phases=[
                            models.PhaseRun(
                                name="explore",
                                kind="agent",
                                status="done",
                                attempts=[
                                    models.Attempt(
                                        n=1, dispatch=_pure_dispatch(), status="gate_failed"
                                    ),
                                    models.Attempt(n=2, dispatch=_pure_dispatch(), status="ok"),
                                ],
                            )
                        ],
                    )
                ],
            ),
            models.StoryRun(
                card_id="story-2",
                title="Two",
                level=1,
                status="started",
                subtasks=[
                    models.SubtaskRun(
                        card_id="card-2",
                        branch="m1/b",
                        base_branch="main",
                        status="started",
                        phases=[
                            models.PhaseRun(
                                name="implement",
                                kind="agent",
                                status="started",
                                attempts=[
                                    models.Attempt(
                                        n=1, dispatch=_pure_dispatch(), status="started"
                                    )
                                ],
                            )
                        ],
                    )
                ],
            ),
        ]
    )

    assert cli.status_rows(run) == [
        {
            "story": "story-1",
            "subtask": "card-1",
            "phase": "explore",
            "attempt": 1,
            "state": "gate_failed",
        },
        {
            "story": "story-1",
            "subtask": "card-1",
            "phase": "explore",
            "attempt": 2,
            "state": "ok",
        },
        {
            "story": "story-2",
            "subtask": "card-2",
            "phase": "implement",
            "attempt": 1,
            "state": "started",
        },
    ]


def test_status_rows_show_a_phase_that_has_no_attempts_yet():
    """A pending or in-flight deterministic phase has no attempt row to hang off,
    and a table that dropped it would hide exactly the phase an operator running
    `status` is asking about."""
    run = _pure_run(
        [
            models.StoryRun(
                card_id="story-1",
                title="One",
                level=0,
                status="started",
                subtasks=[
                    models.SubtaskRun(
                        card_id="card-1",
                        branch="m1/a",
                        base_branch="main",
                        status="started",
                        phases=[
                            models.PhaseRun(name="verify", kind="deterministic", status="pending")
                        ],
                    )
                ],
            )
        ]
    )

    assert cli.status_rows(run) == [
        {
            "story": "story-1",
            "subtask": "card-1",
            "phase": "verify",
            "attempt": None,
            "state": "pending",
        }
    ]


def test_status_rows_of_a_run_with_no_stories_are_empty():
    assert cli.status_rows(_pure_run([])) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k status_rows -v`
Expected: FAIL with `AttributeError: module 'agent_manager.cli' has no attribute 'status_rows'`

- [ ] **Step 3: Implement the row builder**

In `src/agent_manager/cli.py`, insert after `_utcnow` (ends at `cli.py:125`) and before `app = typer.Typer(`:

```python
RUN_IDENTITY = (
    "id",
    "workflow",
    "repo_dir",
    "base_branch",
    "branch_prefix",
    "status",
    "started_at",
)
"""The run's own fields, without `config` and without the tree below it. §10's
`status` header and `runs`' entries are the same seven names, so the two
commands describe a run the same way."""


def status_rows(run: models.Run) -> list[dict[str, Any]]:
    """§10's table as flat rows: one per attempt, in §9 tree order.

    Pure over the tree `load_run` already assembled -- no database, no clock --
    so the command stays a composition. A phase with no attempts gets a row of
    its own with `attempt: None`, because a `pending` or `started` phase is
    precisely what an operator runs `status` to see, and an attempt-keyed table
    would have nowhere to put it.

    `state` is the attempt's status on an attempt row and the phase's status on a
    phase row: both are the state of the thing the row is about.
    """
    rows: list[dict[str, Any]] = []
    for story in run.stories:
        for subtask in story.subtasks:
            for phase in subtask.phases:
                if not phase.attempts:
                    rows.append(
                        {
                            "story": story.card_id,
                            "subtask": subtask.card_id,
                            "phase": phase.name,
                            "attempt": None,
                            "state": phase.status,
                        }
                    )
                    continue
                for attempt in phase.attempts:
                    rows.append(
                        {
                            "story": story.card_id,
                            "subtask": subtask.card_id,
                            "phase": phase.name,
                            "attempt": attempt.n,
                            "state": attempt.status,
                        }
                    )
    return rows
```

`status_payload` is deliberately not written yet — Step 5 tests it first.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k status_rows -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Write the failing payload/render test**

Insert into `tests/test_cli.py` directly after `test_status_rows_of_a_run_with_no_stories_are_empty`:

```python
def test_the_status_payload_survives_render_with_its_paths():
    """`repo_dir` and `worktree_path` are `Path`s and `started_at` is a
    `datetime`; `json.dumps` refuses all three. A renderer that raised would turn
    a successful read into a traceback with no envelope at all."""
    run = _pure_run(
        [
            models.StoryRun(
                card_id="story-1",
                title="One",
                level=0,
                status="done",
                tip_branch="m1/a",
                subtasks=[
                    models.SubtaskRun(
                        card_id="card-1",
                        branch="m1/a",
                        base_branch="main",
                        status="done",
                        worktree_path=Path("/repo/.claude/worktrees/m1/a"),
                    )
                ],
            )
        ]
    )

    data = json.loads(cli.render(cli.ok_envelope(cli.status_payload(run))))["data"]

    assert data["run"]["id"] == "20260923T140506Z-cbe34d00"
    assert data["run"]["repo_dir"] == "/repo"
    assert "2026-09-23" in data["run"]["started_at"]
    assert data["stories"][0]["subtasks"][0]["worktree_path"] == "/repo/.claude/worktrees/m1/a"
    assert data["rows"] == []
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `uv run pytest tests/test_cli.py -k status_payload_survives -v`
Expected: FAIL with `AttributeError: module 'agent_manager.cli' has no attribute 'status_payload'`

- [ ] **Step 7: Implement the payload builder**

In `src/agent_manager/cli.py`, append directly after `status_rows` (and still before `app = typer.Typer(`):

```python
def status_payload(run: models.Run) -> dict[str, Any]:
    """The run's identity, the §9 tree, and the flat table over it.

    `model_dump()` rather than `model_dump(mode="json")`: the payload keeps its
    `Path` and `datetime` objects and `render`'s `default=str` stringifies them
    once, at the edge, the same way `run_card`'s `worktree` is handled. Field
    names are `models.py`'s and are not renamed for display.
    """
    tree = run.model_dump()
    return {
        "run": {field: tree[field] for field in RUN_IDENTITY},
        "stories": tree["stories"],
        "rows": status_rows(run),
    }
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `uv run pytest tests/test_cli.py -k status_payload_survives -v`
Expected: PASS

- [ ] **Step 9: Run the whole suite**

Run: `uv run pytest`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): pure status row and payload builders"
```

---

### Task 3: The `status` command

**Files:**
- Modify: `src/agent_manager/cli.py` (add `UnknownRunError` after `RepoDirError` at `cli.py:53-54`; add the `store as store_module` import to the `from agent_manager import ...` line at `cli.py:28`; add `status_for` and the `status` command at the end of the file, after the `run` command that ends at `cli.py:373`)
- Test: `tests/test_cli.py` (steps tier — real temp SQLite projection under a redirected `XDG_DATA_HOME`, rows written through `Store.record_*`; append at the end of the file)

**Interfaces:**
- Consumes: `store.open_db`, `store.load_run`, `store.latest_run_id` (Task 1); `cli.status_payload` (Task 2); `cli.resolve_repo_dir`, `cli.HANDLED`, `cli.ok_envelope`, `cli.error_envelope`, `cli.render`, `cli.EXIT_ERROR`.
- Produces:
  - `cli.UnknownRunError(CliError)`.
  - `cli.status_for(run_id: str | None, *, repo_dir: Path) -> dict[str, Any]` — the `status_payload` of the named run, or of the project's most recent run when `run_id is None`.
  - Typer command `status` — `agent-manager status [RUN_ID] [--repo-dir PATH] [--pretty]`.

- [ ] **Step 1: Write the failing steps-tier fixture and `status` tests**

Append to `tests/test_cli.py`:

```python
@pytest.fixture
def projection(tmp_path, monkeypatch) -> Path:
    """A project root whose SQLite projection is written directly.

    `status` and `runs` read the projection and nothing else -- no board, no git,
    no worktree -- so this steps-tier fixture is just a directory plus an
    `XDG_DATA_HOME` in `tmp_path`, and these tests need neither `git` nor `brd`.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    root = tmp_path / "recorded"
    root.mkdir()
    return root


def _recorded_dispatch(run_id: str) -> models.Dispatch:
    return models.Dispatch(
        harness="claude",
        model="sonnet",
        role="coder",
        cwd=Path("/repo"),
        prompt_path=paths.run_dir(run_id) / "prompt.txt",
        result_path=paths.run_dir(run_id) / "result.json",
    )


def _record(
    root: Path,
    run_id: str,
    *,
    started_at: datetime,
    status: str = "done",
    with_phases: bool = True,
) -> None:
    """One run -- story, subtask, and optionally two phases and two attempts --
    in `root`'s projection, written the only way this program writes rows."""
    opened = store_module.Store.open(root, run_id)
    try:
        opened.record_run(
            models.Run(
                id=run_id,
                workflow="task",
                repo_dir=root,
                base_branch="main",
                branch_prefix="m1",
                status=status,
                started_at=started_at,
            )
        )
        opened.record_story(
            models.StoryRun(card_id="story-1", title="The CLI", level=0, status=status)
        )
        opened.record_subtask(
            "story-1",
            models.SubtaskRun(
                card_id="card-1",
                branch="m1/task-x",
                base_branch="main",
                status=status,
                worktree_path=root / ".claude" / "worktrees" / "m1" / "task-x",
            ),
        )
        if not with_phases:
            return
        opened.record_phase(
            "story-1",
            "card-1",
            models.PhaseRun(name="explore", kind="agent", status="done"),
        )
        opened.record_attempt(
            "story-1",
            "card-1",
            "explore",
            models.Attempt(n=1, dispatch=_recorded_dispatch(run_id), status="gate_failed"),
        )
        opened.record_attempt(
            "story-1",
            "card-1",
            "explore",
            models.Attempt(n=2, dispatch=_recorded_dispatch(run_id), status="ok"),
        )
        opened.record_phase(
            "story-1",
            "card-1",
            models.PhaseRun(name="verify", kind="deterministic", status="pending"),
        )
    finally:
        opened.close()


RECORDED_AT = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)


def test_status_prints_a_row_for_every_recorded_attempt(projection):
    _record(projection, "20260923T090000Z-cbe34d00", started_at=RECORDED_AT)

    result = runner.invoke(
        cli.app,
        ["status", "20260923T090000Z-cbe34d00", "--repo-dir", str(projection)],
    )

    assert result.exit_code == 0
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["run"]["id"] == "20260923T090000Z-cbe34d00"
    assert envelope["data"]["run"]["workflow"] == "task"
    assert [
        (row["story"], row["subtask"], row["phase"], row["attempt"], row["state"])
        for row in envelope["data"]["rows"]
    ] == [
        ("story-1", "card-1", "explore", 1, "gate_failed"),
        ("story-1", "card-1", "explore", 2, "ok"),
        ("story-1", "card-1", "verify", None, "pending"),
    ]
    assert "\n" not in result.stdout.strip()


def test_status_with_no_run_id_renders_the_most_recent_run(projection):
    _record(projection, "20260921T090000Z-cbe34d00", started_at=datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc))
    _record(projection, "20260924T090000Z-cbe34d00", started_at=datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc))
    _record(projection, "20260923T090000Z-cbe34d00", started_at=RECORDED_AT)

    result = runner.invoke(cli.app, ["status", "--repo-dir", str(projection)])

    assert result.exit_code == 0
    envelope = json.loads(result.stdout)
    assert envelope["data"]["run"]["id"] == "20260924T090000Z-cbe34d00"


def test_status_for_an_unknown_run_id_is_an_envelope(projection):
    """And looking a run up must not mint the run directory a `Journal` would."""
    _record(projection, "20260923T090000Z-cbe34d00", started_at=RECORDED_AT)

    result = runner.invoke(
        cli.app, ["status", "no-such-run", "--repo-dir", str(projection)]
    )

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "UnknownRunError"
    assert "no-such-run" in envelope["error"]["message"]
    assert str(projection.resolve()) in envelope["error"]["message"]
    assert not (paths.data_dir() / "runs" / "no-such-run").exists()


def test_status_with_no_run_id_against_a_project_with_no_runs_is_an_envelope(projection):
    result = runner.invoke(cli.app, ["status", "--repo-dir", str(projection)])

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "UnknownRunError"
    assert "most recent" in envelope["error"]["message"]


def test_status_of_a_run_that_died_before_its_first_phase_is_ok_with_no_rows(projection):
    """`run_card` writes the run, story and subtask rows before the walk starts
    (cli.py:228-230) exactly so `status` can see a run that died on its first
    dispatch. That reading is a fact, not an error."""
    _record(
        projection,
        "20260923T090000Z-cbe34d00",
        started_at=RECORDED_AT,
        status="escalated",
        with_phases=False,
    )

    result = runner.invoke(
        cli.app, ["status", "20260923T090000Z-cbe34d00", "--repo-dir", str(projection)]
    )

    assert result.exit_code == 0
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["run"]["status"] == "escalated"
    assert envelope["data"]["rows"] == []
    assert envelope["data"]["stories"][0]["subtasks"][0]["card_id"] == "card-1"


def test_status_pretty_indents_the_same_envelope(projection):
    _record(projection, "20260923T090000Z-cbe34d00", started_at=RECORDED_AT)

    plain = runner.invoke(
        cli.app, ["status", "20260923T090000Z-cbe34d00", "--repo-dir", str(projection)]
    )
    pretty = runner.invoke(
        cli.app,
        ["status", "20260923T090000Z-cbe34d00", "--repo-dir", str(projection), "--pretty"],
    )

    assert pretty.exit_code == 0
    assert "\n" in pretty.stdout.strip()
    assert json.loads(pretty.stdout) == json.loads(plain.stdout)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "status_prints or status_with_no_run_id or status_for_an_unknown or status_of_a_run_that_died or status_pretty" -v`
Expected: FAIL — Typer exits 2 with "No such command 'status'" (the tests assert exit codes 0 and 3).

- [ ] **Step 3: Add the import and the refusal type**

In `src/agent_manager/cli.py`, change line 28 from:

```python
from agent_manager import board, dag, dispatch, engine, models
```

to:

```python
from agent_manager import board, dag, dispatch, engine, models, store as store_module
```

and insert after `RepoDirError` (ends at `cli.py:54`), before `class ParentlessCardError(CliError):`:

```python
class UnknownRunError(CliError):
    """`status` was asked for a run this project's projection does not hold.

    A `CliError` so it rides the existing `HANDLED` tuple into an `ok: false`
    envelope at exit 3 rather than reaching the renderer as a `None` tree. The
    same class covers "no most-recent run to default to": both are the same
    refusal -- the command was asked for a run and there is none -- and the
    message is what tells the two apart.
    """
```

- [ ] **Step 4: Implement `status_for` and the command**

Append to the end of `src/agent_manager/cli.py`:

```python
def status_for(run_id: str | None, *, repo_dir: Path) -> dict[str, Any]:
    """The §9 tree and §10 table of one run of this project.

    Read-only: no `record_*` is called, and the connection is closed on every
    path including the refusals, the way `run_card` closes its store. The default
    run id comes from `store_module.latest_run_id`, which is the head of the very
    listing `runs` prints, so the two commands cannot disagree about which run is
    the most recent one.
    """
    root = resolve_repo_dir(repo_dir)
    conn = store_module.open_db(root)
    try:
        wanted = run_id
        if wanted is None:
            wanted = store_module.latest_run_id(conn)
            if wanted is None:
                raise UnknownRunError(
                    f"no run has been recorded for {root}, so there is no most recent"
                    " run to report on; pass a run id or start one with `run --card`"
                )
        run = store_module.load_run(conn, wanted)
        if run is None:
            raise UnknownRunError(
                f"run {wanted!r} is not in the projection for {root}"
                " (`agent-manager runs` lists the ones that are)"
            )
        return status_payload(run)
    finally:
        conn.close()


@app.command("status")
def status(
    run_id: str | None = typer.Argument(
        None, metavar="[RUN_ID]", help="The run to report on. Defaults to the most recent."
    ),
    repo_dir: Path = typer.Option(
        Path("."), "--repo-dir", help="The repository whose projection is read."
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Report one run as story / subtask / phase / attempt / state."""
    try:
        payload = status_for(run_id, repo_dir=repo_dir)
    except HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    typer.echo(render(ok_envelope(payload), pretty=pretty))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "status_prints or status_with_no_run_id or status_for_an_unknown or status_of_a_run_that_died or status_pretty" -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest`
Expected: PASS — `run` is still a named subcommand and its tests are untouched.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): add the status command"
```

---

### Task 4: The `runs` command

**Files:**
- Modify: `src/agent_manager/cli.py` (append `runs_for` and the `runs` command after the `status` command from Task 3)
- Test: `tests/test_cli.py` (steps tier — appended after Task 3's tests, reusing the `projection` fixture and `_record` helper)

**Interfaces:**
- Consumes: `store.open_db`, `store.list_runs` (Task 1); `cli.resolve_repo_dir`, `cli.HANDLED`, `cli.render`, `cli.ok_envelope`, `cli.error_envelope`, `cli.EXIT_ERROR`; the `projection` fixture and `_record` helper (Task 3).
- Produces:
  - `cli.runs_for(*, repo_dir: Path) -> dict[str, Any]` — `{"runs": [<RunSummary.model_dump()>, ...]}`, newest first.
  - Typer command `runs` — `agent-manager runs [--repo-dir PATH] [--pretty]`.

- [ ] **Step 1: Write the failing `runs` tests**

Append to `tests/test_cli.py`:

```python
def test_runs_lists_the_projects_history_newest_first(projection):
    _record(projection, "20260921T090000Z-cbe34d00", started_at=datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc))
    _record(projection, "20260924T090000Z-cbe34d00", started_at=datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc))
    _record(projection, "20260923T090000Z-cbe34d00", started_at=RECORDED_AT)

    result = runner.invoke(cli.app, ["runs", "--repo-dir", str(projection)])

    assert result.exit_code == 0
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert [entry["id"] for entry in envelope["data"]["runs"]] == [
        "20260924T090000Z-cbe34d00",
        "20260923T090000Z-cbe34d00",
        "20260921T090000Z-cbe34d00",
    ]
    assert envelope["data"]["runs"][0]["workflow"] == "task"
    assert envelope["data"]["runs"][0]["status"] == "done"
    assert "2026-09-24" in envelope["data"]["runs"][0]["started_at"]
    assert envelope["data"]["runs"][0]["repo_dir"] == str(projection.resolve())


def test_runs_on_a_project_that_has_never_been_run_is_ok_and_empty(projection):
    """A project nobody has run yet is a fact, not a fault."""
    result = runner.invoke(cli.app, ["runs", "--repo-dir", str(projection)])

    assert result.exit_code == 0
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["runs"] == []


def test_runs_agrees_with_status_about_the_most_recent_run(projection):
    _record(projection, "20260921T090000Z-cbe34d00", started_at=datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc))
    _record(projection, "20260924T090000Z-cbe34d00", started_at=datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc))

    listed = json.loads(
        runner.invoke(cli.app, ["runs", "--repo-dir", str(projection)]).stdout
    )
    reported = json.loads(
        runner.invoke(cli.app, ["status", "--repo-dir", str(projection)]).stdout
    )

    assert listed["data"]["runs"][0]["id"] == reported["data"]["run"]["id"]


def test_runs_pretty_indents_the_same_envelope(projection):
    _record(projection, "20260923T090000Z-cbe34d00", started_at=RECORDED_AT)

    plain = runner.invoke(cli.app, ["runs", "--repo-dir", str(projection)])
    pretty = runner.invoke(cli.app, ["runs", "--repo-dir", str(projection), "--pretty"])

    assert pretty.exit_code == 0
    assert "\n" in pretty.stdout.strip()
    assert json.loads(pretty.stdout) == json.loads(plain.stdout)


def test_a_missing_repo_dir_is_an_envelope_for_both_read_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    missing = tmp_path / "missing"

    for argv in (["status", "--repo-dir", str(missing)], ["runs", "--repo-dir", str(missing)]):
        result = runner.invoke(cli.app, argv)
        assert result.exit_code == cli.EXIT_ERROR, argv
        envelope = json.loads(result.stdout)
        assert envelope["ok"] is False
        assert envelope["error"]["type"] == "RepoDirError"
        assert "missing" in envelope["error"]["message"]


def test_a_repo_dir_that_is_a_file_is_an_envelope_for_both_commands(tmp_path, monkeypatch):
    """`resolve_repo_dir` checks `is_dir`, not `exists`: a file that exists must
    be refused before `paths.project_db_path` hashes it into a database name."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    not_a_dir = tmp_path / "README.md"
    not_a_dir.write_text("not a repo\n", encoding="utf-8")

    for argv in (
        ["status", "--repo-dir", str(not_a_dir)],
        ["runs", "--repo-dir", str(not_a_dir)],
    ):
        result = runner.invoke(cli.app, argv)
        assert result.exit_code == cli.EXIT_ERROR, argv
        assert json.loads(result.stdout)["error"]["type"] == "RepoDirError"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "runs_lists or runs_on_a_project or runs_agrees or runs_pretty or read_commands or repo_dir_that_is_a_file" -v`
Expected: FAIL — Typer exits 2 with "No such command 'runs'".

- [ ] **Step 3: Implement `runs_for` and the command**

Append to the end of `src/agent_manager/cli.py`:

```python
def runs_for(*, repo_dir: Path) -> dict[str, Any]:
    """This project's run history, newest first.

    An empty history is an empty list, not a refusal: a project that has never
    been run is a fact. `model_dump()` keeps the `Path` and `datetime` objects
    for `render`'s `default=str`, exactly as `status_payload` does, so a run
    looks the same in both commands.
    """
    root = resolve_repo_dir(repo_dir)
    conn = store_module.open_db(root)
    try:
        return {"runs": [summary.model_dump() for summary in store_module.list_runs(conn)]}
    finally:
        conn.close()


@app.command("runs")
def runs(
    repo_dir: Path = typer.Option(
        Path("."), "--repo-dir", help="The repository whose projection is read."
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """List this project's run history, newest first."""
    try:
        payload = runs_for(repo_dir=repo_dir)
    except HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    typer.echo(render(ok_envelope(payload), pretty=pretty))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "runs_lists or runs_on_a_project or runs_agrees or runs_pretty or read_commands or repo_dir_that_is_a_file" -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): add the runs command"
```

---

## Final verification

- [ ] **Step 1: Run the full suite**

Run: `uv run pytest`
Expected: PASS, no skips beyond the pre-existing `requires_git`/`requires_brd` ones.

- [ ] **Step 2: Confirm the grammar §10 asks for**

Run: `uv run agent-manager --help`
Expected: three commands listed — `run`, `status`, `runs`.

- [ ] **Step 3: Confirm the tree is clean**

Run: `git status --porcelain`
Expected: empty output.
