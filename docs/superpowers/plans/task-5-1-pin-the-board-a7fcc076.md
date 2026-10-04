<!-- task-pipeline: validated -->
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

---

# Pin the `--board` Journal and Plan Shapes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pin, with five characterization tests, the `--board` run payload, the per-milestone journal shape (no new key), the `--board --dry-run` payload, and the CLI envelope, so sibling card 5.2 can document them from tests.

**Architecture:** Tests only. The production behaviour already exists, so every test is a characterization test: it is written first and is expected to PASS on the first run. To prove each test can fail (the RED evidence for a pinning test), each task includes a temporary one-line mutation of production code that must turn the test red, and is then reverted with `git checkout`. Source files end the card unchanged unless Task 6's decision gate trips.

**Tech Stack:** Python 3, pytest, Typer `CliRunner`, Pydantic models, `uv`.

**Spec:** `/home/mtts/Code/agent-manager/.claude/worktrees/ami/task-5-1-pin-the-board-a7fcc076/docs/superpowers/specs/task-5-1-pin-the-board-a7fcc076-design.md` (prepended verbatim above).

Work in the worktree `/home/mtts/Code/agent-manager/.claude/worktrees/ami/task-5-1-pin-the-board-a7fcc076` on branch `ami/task-5-1-pin-the-board-a7fcc076`. All paths below are relative to that worktree. Do not assume any other subtask's code exists on this branch.

## Global Constraints

- The journal stays schema 1. New JSON keys are additive only.
- No README text (that is sibling 5.2, card 4881c933).
- No change to `--card` or `--milestone` behaviour, `am runs` fields, `--detach`, `logs --follow`, `--from-now`.
- Test tier is chosen by what the test spawns: unmarked = unit (no subprocess, ≤0.5s), `@pytest.mark.e2e_fake` for production wiring under the fake claude (opt-in).
- Verification command: `uv run pytest` (default unit + git tiers). There is no lint or typecheck command.
- Every mutation made to prove a test bites MUST be reverted with `git checkout -- <file>` before committing; `git status` must show only test files changed.

## Review Focus

1. An escalated entry (a milestone that raised) has no `run_id` key at all, so a consumer that indexes `entry["run_id"]` on every entry would crash. Pinned in Task 1 (escalated key set is exactly `{milestone_id, status, error}`).
2. A blocked dependent was never dispatched, so it has no `run_id` either. Pinned in Task 1 (blocked key set is exactly `{milestone_id, status, blocked_by}`).
3. The synthetic story ids `integrate` and `bases` repeat across milestones' journals; a consumer keying stories by id alone across journals would merge two milestones. Pinned in Task 5 (synthetic ids excluded from the disjointness check, all lines share one `run_id`) and Task 2 (synthetic lines carry the store's `run_id`).
4. The nested per-milestone `plan` in a dry run has no `ok` key and no `run_id`; a consumer must not look for either. Pinned in Task 3.
5. The CLI envelope says `ok: true` even when the board payload's own `ok` is `false` (an escalation is a truthful result, exit code `EXIT_ESCALATED`). Pinned in Task 4.

---

### Task 1: Pin the `run_board` payload key sets (unit, `tests/test_orchestrate.py`)

**Files:**
- Test: `tests/test_orchestrate.py` (insert after `test_run_board_treats_an_already_done_blocker_as_satisfied`, which ends at line 7153, before `test_run_board_ends_on_a_base_exception_instead_of_hanging` at line 7156)
- Mutate temporarily, then revert: `src/agent_manager/orchestrate.py:2341-2346`

**Interfaces:**
- Consumes: existing fixtures/helpers in `tests/test_orchestrate.py`: `board_seams` (fixture, line 6885; `board_seams.cards`, `board_seams.runs.outcomes`), `_board(seams, **overrides)` (line 6897), `_by_id(result)` (line 6908), `_board_milestone(n, *, blocked_by=(), status="todo", done_children=False)` (line 6749). `FakeMilestoneRuns` answers an unlisted milestone with `{"done": True, "run_id": f"run-{milestone[:8]}"}`.
- Produces: nothing other tasks depend on.

- [ ] **Step 1: Write the test**

Insert into `tests/test_orchestrate.py` after `test_run_board_treats_an_already_done_blocker_as_satisfied`:

```python
def test_run_board_payload_has_one_run_per_milestone_and_no_board_level_run(board_seams):
    """Card a7fcc076: a board run has no board-level Run. The payload carries
    no top-level `run_id`; each dispatched milestone's entry carries its own
    run's `run_id`, distinct per milestone. An escalated (raised) entry and a
    blocked entry were never given a run, so they carry no `run_id` at all.
    A done entry is `milestone_id` and `status` plus the run payload's own
    keys, which here are the fake's `done` and `run_id`."""
    a, b, c = _board_milestone(1), _board_milestone(2), _board_milestone(3)
    d = _board_milestone(4, blocked_by=(3,))
    board_seams.cards = [a, b, c, d]
    board_seams.runs.outcomes[c.id] = RuntimeError("boom")

    result = _board(board_seams)

    assert set(result) == {"ok", "board", "levels", "milestones"}
    assert "run_id" not in result
    assert result["ok"] is False
    assert result["board"] is True
    assert result["levels"] == [
        {"level": 0, "milestones": [a.id, b.id, c.id]},
        {"level": 1, "milestones": [d.id]},
    ]
    for level in result["levels"]:
        assert set(level) == {"level", "milestones"}
    by_id = _by_id(result)
    assert set(by_id) == {a.id, b.id, c.id, d.id}
    for done in (a, b):
        assert set(by_id[done.id]) == {"milestone_id", "status", "done", "run_id"}
        assert by_id[done.id]["status"] == "done"
    assert set(by_id[c.id]) == {"milestone_id", "status", "error"}
    assert by_id[c.id]["status"] == "escalated"
    assert by_id[c.id]["error"] == "RuntimeError: boom"
    assert set(by_id[d.id]) == {"milestone_id", "status", "blocked_by"}
    assert by_id[d.id]["status"] == "blocked"
    assert by_id[d.id]["blocked_by"] == [c.id]
    run_ids = [by_id[done.id]["run_id"] for done in (a, b)]
    assert len(set(run_ids)) == len(run_ids)
```

- [ ] **Step 2: Run the test; it pins existing behaviour, so expect PASS**

Run: `uv run pytest tests/test_orchestrate.py::test_run_board_payload_has_one_run_per_milestone_and_no_board_level_run -v`
Expected: PASS. If it FAILS, stop: the code disagrees with the spec. Read the failure, report the actual shape, and do not edit production code to match the test without escalating.

- [ ] **Step 3: Prove the test bites (temporary mutation)**

In `src/agent_manager/orchestrate.py`, change the final return of `run_board` (lines 2341-2346) to add a board-level run id:

```python
    return {
        "ok": all(entry["status"] == "done" for entry in entries),
        "board": True,
        "levels": levels_payload,
        "milestones": entries,
        "run_id": None,
    }
```

Run: `uv run pytest tests/test_orchestrate.py::test_run_board_payload_has_one_run_per_milestone_and_no_board_level_run -v`
Expected: FAIL at `assert set(result) == {"ok", "board", "levels", "milestones"}`.

- [ ] **Step 4: Revert the mutation and re-run**

```bash
git checkout -- src/agent_manager/orchestrate.py
uv run pytest tests/test_orchestrate.py::test_run_board_payload_has_one_run_per_milestone_and_no_board_level_run -v
```
Expected: PASS. `git status` shows only `tests/test_orchestrate.py` modified.

- [ ] **Step 5: Commit**

```bash
git add tests/test_orchestrate.py
git commit -m "test: pin the run_board payload key sets and one run per milestone (a7fcc076)"
```

---

### Task 2: Pin the per-milestone journal shape and the "no new key" decision (unit, `tests/test_store.py`)

**Files:**
- Test: `tests/test_store.py` (insert after `test_a_runs_milestone_id_survives_a_rebuild_from_the_journal`, which ends at line 3092, before `test_a_run_upsert_line_from_before_milestone_id_rebuilds_to_none` at line 3095)
- Mutate temporarily, then revert: `src/agent_manager/store.py:1359-1367` (`Store.record_story`)

**Interfaces:**
- Consumes: existing helpers in `tests/test_store.py`: `repo` fixture (line 36, redirects `XDG_DATA_HOME`/`HOME`), `RUN_ID = "run-2026-09-23-01"` (line 32), `MILESTONE_ID = "9c44c2fb-0000-4000-8000-000000000000"` (line 2915, module-level, defined above the insertion point), `_run(repo, run_id=RUN_ID) -> models.Run` (line 56), `_story() -> models.StoryRun` (line 600, card id `"8831189b"`), `_subtask(card_id="ef248597", base="main") -> models.SubtaskRun` (line 610). From src: `store.Store.open(root, run_id)`, `Store.record_run/record_story/record_subtask(story_id, subtask)`, `store.Journal(run_id).read() -> list[JournalLine]` (fields `seq, ts, run_id, event, story, card, phase, attempt, payload`), `bases.BASES_STORY_ID == "bases"`, `bases.BASES_STORY_TITLE`, `bases.resolver_card_id(story_id) -> "base-<story_id>"`, `integration.INTEGRATE_STORY_ID == "integrate"`.
- Produces: nothing other tasks depend on.

- [ ] **Step 1: Write the test**

Insert into `tests/test_store.py` after `test_a_runs_milestone_id_survives_a_rebuild_from_the_journal`:

```python
RUN_UPSERT_KEYS = {
    "id",
    "workflow",
    "repo_dir",
    "base_branch",
    "branch_prefix",
    "status",
    "started_at",
    "config",
    "milestone_id",
}
"""A `run_upsert` payload: the `Run` dump without `stories`."""

STORY_UPSERT_KEYS = {"card_id", "title", "level", "status", "tip_branch"}
"""A `story_upsert` payload: the `StoryRun` dump without `subtasks`. No milestone key."""


def test_a_milestone_runs_journal_names_its_milestone_once_at_the_head(repo):
    """Card a7fcc076, the "no new journal key" decision, pinned.

    A `--board` run gives each milestone its own Run, so one journal covers
    exactly one milestone. Its first line (lowest `seq`) is the `run_upsert`
    carrying `milestone_id`, and every later line shares that line's
    `run_id`. A `story_upsert` therefore needs no milestone key of its own:
    a consumer reading one run's journal learns the milestone from the head.
    The synthetic ids (`integrate`, `bases`, `base-<story id>`) are recorded
    the same way, under the same `run_id`. Should this ever fail because the
    head is not a `run_upsert` with `milestone_id`, or because lines mix run
    ids, the spec's fallback (an additive `milestone_id` on `story_upsert`)
    applies.
    """
    from agent_manager import bases, integration

    story = _story()
    merged = models.StoryRun(
        card_id=bases.BASES_STORY_ID,
        title=bases.BASES_STORY_TITLE,
        level=0,
        status="started",
    )
    resolver = _subtask(bases.resolver_card_id(story.card_id))
    integrate = models.StoryRun(
        card_id=integration.INTEGRATE_STORY_ID, title="Integrate", level=1, status="started"
    )
    run = _run(repo).model_copy(update={"milestone_id": MILESTONE_ID})
    st = store.Store.open(repo, RUN_ID)
    try:
        st.record_run(run)
        st.record_story(story)
        st.record_story(merged)
        st.record_subtask(bases.BASES_STORY_ID, resolver)
        st.record_story(integrate)
        st.record_run(run.model_copy(update={"status": "done"}))
    finally:
        st.close()

    lines = store.Journal(RUN_ID).read()

    assert [line.event for line in lines] == [
        "run_upsert",
        "story_upsert",
        "story_upsert",
        "subtask_upsert",
        "story_upsert",
        "run_upsert",
    ]
    head = min(lines, key=lambda line: line.seq)
    assert head is lines[0]
    assert head.event == "run_upsert"
    assert set(head.payload) == RUN_UPSERT_KEYS
    assert head.payload["milestone_id"] == MILESTONE_ID
    assert {line.run_id for line in lines} == {RUN_ID}
    for line in lines:
        if line.event == "run_upsert":
            assert line.payload["milestone_id"] == MILESTONE_ID
    story_lines = [line for line in lines if line.event == "story_upsert"]
    assert [line.story for line in story_lines] == [
        story.card_id,
        bases.BASES_STORY_ID,
        integration.INTEGRATE_STORY_ID,
    ]
    for line in story_lines:
        assert set(line.payload) == STORY_UPSERT_KEYS
        assert line.payload["card_id"] == line.story
        assert line.card is None
    (subtask_line,) = [line for line in lines if line.event == "subtask_upsert"]
    assert (subtask_line.story, subtask_line.card) == (
        bases.BASES_STORY_ID,
        f"base-{story.card_id}",
    )
    assert "milestone_id" not in subtask_line.payload
```

- [ ] **Step 2: Run the test; it pins existing behaviour, so expect PASS**

Run: `uv run pytest tests/test_store.py::test_a_milestone_runs_journal_names_its_milestone_once_at_the_head -v`
Expected: PASS. If it FAILS on the head line or on mixed `run_id`s, stop and go to Task 6's decision gate. If it FAILS on a key set, report the actual keys; do not change production code.

- [ ] **Step 3: Prove the test bites (temporary mutation)**

In `src/agent_manager/store.py`, `Store.record_story` (lines 1359-1367), add a milestone key to the payload:

```python
    def record_story(self, story: models.StoryRun) -> JournalLine:
        with self._lock, self._fenced():
            line = self._journal.append(
                "story_upsert",
                {**story.model_dump(mode="json", exclude={"subtasks"}), "milestone_id": None},
                story=story.card_id,
            )
            self._write_story_row(self.run_id, story)
            return line
```

Run: `uv run pytest tests/test_store.py::test_a_milestone_runs_journal_names_its_milestone_once_at_the_head -v`
Expected: FAIL at `assert set(line.payload) == STORY_UPSERT_KEYS`.

- [ ] **Step 4: Revert the mutation and re-run**

```bash
git checkout -- src/agent_manager/store.py
uv run pytest tests/test_store.py::test_a_milestone_runs_journal_names_its_milestone_once_at_the_head -v
```
Expected: PASS. `git status` shows only test files modified.

- [ ] **Step 5: Commit**

```bash
git add tests/test_store.py
git commit -m "test: pin the per-milestone journal head and story_upsert shape, no new key (a7fcc076)"
```

---

### Task 3: Pin the `--board --dry-run` key sets (unit, `tests/test_cli.py`)

**Files:**
- Test: `tests/test_cli.py` (insert after `test_an_empty_board_dry_runs_to_no_levels_and_exits_zero`, which ends at line 3636, before the `@pytest.mark.parametrize` of `test_a_board_dry_run_refusal_is_an_envelope_and_writes_nothing` at line 3639)
- Mutate temporarily, then revert: `src/agent_manager/cli.py:1276-1300` (`dry_run_board` return)

**Interfaces:**
- Consumes: existing helpers in `tests/test_cli.py`: `_board_milestone(n, *, status="todo", blocked_by=(), card_id=None, title=None)` (line 3552; note `blocked_by` takes card ids, not numbers), `_serve_roots(monkeypatch, roots)` (line 3579, patches `cli.board.roots`), `_forbid_board_dry_run_writes(monkeypatch)` (line 3452, makes `store_module.Store`, `cli.refuse_claimed`, `cli.dry_run_milestone`, `orchestrate.run_board`, `orchestrate.run_milestone` raise if touched), `_board_dry_run(repo_dir, *extra)` (line 3479), `paths`. These neighbours are unmarked (unit) and use the fake board, so this test is unmarked too. (The brd/git-marked neighbour at line 3497 is the only one that uses a real board; this card does not need one.)
- Produces: nothing other tasks depend on.

- [ ] **Step 1: Write the test**

Insert into `tests/test_cli.py` after `test_an_empty_board_dry_runs_to_no_levels_and_exits_zero`:

```python
def test_the_board_dry_run_data_has_exactly_its_keys_and_no_ok_or_run_id(tmp_path, monkeypatch):
    """Card a7fcc076: the `--board --dry-run` shape, pinned for the README.

    `data` is exactly `{board, max_concurrent, levels}`; each level is
    `{level, milestones}`; each milestone is exactly `{milestone_id, title,
    branch_prefix, plan}`, and `plan` is that milestone's `dry_run_payload`
    (`{max_concurrent, levels, already_done, integrate}`). `ok` is only on
    the envelope, never inside `data`, and nothing carries a `run_id`: a
    preview mints no run and opens no Store."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    first = _board_milestone(1)
    second = _board_milestone(2, blocked_by=(first.id,))
    _serve_roots(monkeypatch, [first, second])
    _forbid_board_dry_run_writes(monkeypatch)

    result = _board_dry_run(tmp_path)

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert set(envelope) == {"ok", "data"}
    assert envelope["ok"] is True
    data = envelope["data"]
    assert set(data) == {"board", "max_concurrent", "levels"}
    assert "ok" not in data
    assert "run_id" not in data
    assert data["board"] is True
    assert [set(level) for level in data["levels"]] == [{"level", "milestones"}] * 2
    entries = [entry for level in data["levels"] for entry in level["milestones"]]
    assert [entry["milestone_id"] for entry in entries] == [first.id, second.id]
    for entry in entries:
        assert set(entry) == {"milestone_id", "title", "branch_prefix", "plan"}
        assert set(entry["plan"]) == {"max_concurrent", "levels", "already_done", "integrate"}
        assert "ok" not in entry["plan"]
        assert "run_id" not in entry["plan"]
    assert list(paths.data_dir().iterdir()) == []
```

- [ ] **Step 2: Run the test; it pins existing behaviour, so expect PASS**

Run: `uv run pytest tests/test_cli.py::test_the_board_dry_run_data_has_exactly_its_keys_and_no_ok_or_run_id -v`
Expected: PASS. If it FAILS, report the actual shape; do not change production code.

- [ ] **Step 3: Prove the test bites (temporary mutation)**

In `src/agent_manager/cli.py`, `dry_run_board`, change the first lines of the returned dict (line 1276-1277) to:

```python
    return {
        "ok": True,
        "board": True,
```

Run: `uv run pytest tests/test_cli.py::test_the_board_dry_run_data_has_exactly_its_keys_and_no_ok_or_run_id -v`
Expected: FAIL at `assert set(data) == {"board", "max_concurrent", "levels"}`.

- [ ] **Step 4: Revert the mutation and re-run**

```bash
git checkout -- src/agent_manager/cli.py
uv run pytest tests/test_cli.py::test_the_board_dry_run_data_has_exactly_its_keys_and_no_ok_or_run_id -v
```
Expected: PASS. `git status` shows only test files modified.

- [ ] **Step 5: Commit**

```bash
git add tests/test_cli.py
git commit -m "test: pin the board dry-run key sets, no ok or run_id inside data (a7fcc076)"
```

---

### Task 4: Pin the CLI envelope around the board run payload (unit, `tests/test_cli.py`)

**Files:**
- Test: `tests/test_cli.py` (insert after `test_a_board_run_prints_run_boards_payload_in_the_ok_envelope`, which ends at line 3807, before the `@pytest.mark.parametrize` of `test_a_board_run_exits_escalated_only_when_some_milestone_escalated` at line 3810; line numbers shift down by the Task 3 insertion, so locate by name)
- Mutate temporarily, then revert: `src/agent_manager/cli.py:1680-1687` (the `elif whole_board:` branch of the `run` command)

**Interfaces:**
- Consumes: existing helpers in `tests/test_cli.py`: `_plan_id(n) -> str`, `_patch_run_board(monkeypatch, outcome) -> list[dict]` (line 3707; replaces `orchestrate.run_board`, forbids other run paths), `_board_run(tmp_path, *extra)` (line 3700), `cli.EXIT_ESCALATED`.
- Produces: nothing other tasks depend on.

- [ ] **Step 1: Write the test**

Insert into `tests/test_cli.py` after `test_a_board_run_prints_run_boards_payload_in_the_ok_envelope`:

```python
def test_a_board_run_envelope_wraps_run_boards_keys_unchanged(tmp_path, monkeypatch):
    """Card a7fcc076: `am run --board` prints `{"ok": true, "data": payload}`
    with `run_board`'s payload untouched: `data` is exactly `{ok, board,
    levels, milestones}`, with no board-level `run_id`, and the done,
    escalated and blocked entries keep their own key sets. The envelope's
    `ok` stays true when `data.ok` is false: an escalation is a truthful
    result, reported through the exit code."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    done_id, escalated_id, blocked_id = _plan_id(1), _plan_id(2), _plan_id(3)
    payload = {
        "ok": False,
        "board": True,
        "levels": [
            {"level": 0, "milestones": [done_id, escalated_id]},
            {"level": 1, "milestones": [blocked_id]},
        ],
        "milestones": [
            {
                "milestone_id": done_id,
                "status": "done",
                "done": True,
                "run_id": "20261001T000000Z-00000001",
            },
            {"milestone_id": escalated_id, "status": "escalated", "error": "RuntimeError: boom"},
            {"milestone_id": blocked_id, "status": "blocked", "blocked_by": [escalated_id]},
        ],
    }
    _patch_run_board(monkeypatch, payload)

    result = _board_run(tmp_path)

    assert result.exit_code == cli.EXIT_ESCALATED, result.output
    envelope = json.loads(result.stdout)
    assert envelope == {"ok": True, "data": payload}
    assert set(envelope["data"]) == {"ok", "board", "levels", "milestones"}
    assert "run_id" not in envelope["data"]
    assert envelope["data"]["ok"] is False
    assert [set(entry) for entry in envelope["data"]["milestones"]] == [
        {"milestone_id", "status", "done", "run_id"},
        {"milestone_id", "status", "error"},
        {"milestone_id", "status", "blocked_by"},
    ]
```

- [ ] **Step 2: Run the test; it pins existing behaviour, so expect PASS**

Run: `uv run pytest tests/test_cli.py::test_a_board_run_envelope_wraps_run_boards_keys_unchanged -v`
Expected: PASS. If the exit code differs from `cli.EXIT_ESCALATED`, report it; do not change production code.

- [ ] **Step 3: Prove the test bites (temporary mutation)**

In `src/agent_manager/cli.py`, in the `run` command's `elif whole_board:` branch (lines 1680-1687), add one line right after the `orchestrate.run_board(...)` call closes:

```python
            payload = orchestrate.run_board(
                repo_dir=repo_dir,
                base_branch=base_branch,
                branch_prefix_of=board_prefix_of(branch_prefix),
                commands=list(verify),
                allow_no_verification=allow_no_verification,
                max_concurrent=lanes,
            )
            payload = {**payload, "run_id": None}
```

Run: `uv run pytest tests/test_cli.py::test_a_board_run_envelope_wraps_run_boards_keys_unchanged -v`
Expected: FAIL at `assert envelope == {"ok": True, "data": payload}`.

- [ ] **Step 4: Revert the mutation and re-run**

```bash
git checkout -- src/agent_manager/cli.py
uv run pytest tests/test_cli.py::test_a_board_run_envelope_wraps_run_boards_keys_unchanged -v
```
Expected: PASS. `git status` shows only test files modified.

- [ ] **Step 5: Commit**

```bash
git add tests/test_cli.py
git commit -m "test: pin the board run CLI envelope around run_board's payload (a7fcc076)"
```

---

### Task 5: Pin the real per-milestone journals in the e2e_fake board scenario (`tests/e2e/test_run_board.py`)

**Files:**
- Modify: `tests/e2e/test_run_board.py:31` (import line) and `tests/e2e/test_run_board.py:185-210` (`test_two_independent_milestones_both_finish`, already `@pytest.mark.e2e_fake`; no new test is added, per one test per scenario family)

**Interfaces:**
- Consumes: in this module: `board_root` fixture, `_milestone(root, label, prefix)` returning `{"id", "story", "subtask", "prefix", "branch"}`, `_run_board(root, *milestones, max_concurrent)`, `_entries(result)`. From src: `store.Journal(run_id).read()`, `integration.INTEGRATE_STORY_ID`, `bases.BASES_STORY_ID`.
- Produces: nothing other tasks depend on.

- [ ] **Step 1: Add the imports**

In `tests/e2e/test_run_board.py`, replace line 31:

```python
from agent_manager import board, cli, dag, models, orchestrate, paths, store
```

with:

```python
from agent_manager import bases, board, cli, dag, integration, models, orchestrate, paths, store
```

- [ ] **Step 2: Add the journal assertions**

In `test_two_independent_milestones_both_finish`, after the existing line `assert entries[x["id"]]["run_id"] != entries[y["id"]]["run_id"]` (line 209) and before `assert _git(root, "rev-parse", "main").strip() == main_before`, insert:

```python
    # Card a7fcc076: each milestone's own journal names its milestone at the
    # head and every line carries that milestone's own run id, so no line
    # needs a milestone key. Synthetic story ids (`integrate`, `bases`) are
    # fixed names that any milestone's journal may record, so only the real
    # story card ids are checked for disjointness across journals.
    synthetic = {integration.INTEGRATE_STORY_ID, bases.BASES_STORY_ID}
    real_stories: dict[str, set[str]] = {}
    for milestone in (x, y):
        run_id = entries[milestone["id"]]["run_id"]
        lines = store.Journal(run_id).read()
        assert lines, run_id
        assert lines[0].event == "run_upsert", lines[0]
        assert lines[0].payload["milestone_id"] == milestone["id"]
        assert {line.run_id for line in lines} == {run_id}
        for line in lines:
            if line.event == "run_upsert":
                assert line.payload["milestone_id"] == milestone["id"], line
            if line.event == "story_upsert":
                assert "milestone_id" not in line.payload, line
        stories = {line.story for line in lines if line.story is not None}
        real_stories[milestone["id"]] = stories - synthetic
        assert milestone["story"] in real_stories[milestone["id"]], stories
    assert real_stories[x["id"]].isdisjoint(real_stories[y["id"]])
```

- [ ] **Step 3: Run the e2e_fake test (opt-in tier; needs `brd` on PATH)**

Run: `uv run pytest -m e2e_fake "tests/e2e/test_run_board.py::test_two_independent_milestones_both_finish" -v`
Expected: PASS. If it FAILS on `lines[0]` or on the `run_id` set, stop and go to Task 6's decision gate. If `brd` is unavailable the test cannot run; record that in the task report rather than marking it passed.

- [ ] **Step 4: Confirm the default suite still collects the module unchanged**

Run: `uv run pytest tests/e2e/test_run_board.py -v`
Expected: only `test_this_module_runs_in_the_default_suite_unmarked` runs and PASSES; the e2e_fake tests are deselected.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_run_board.py
git commit -m "test: pin each board milestone's real journal head and run id (a7fcc076)"
```

---

### Task 6: Decision gate and full verification

**Files:**
- None expected. Only if the gate trips: see Step 2.

- [ ] **Step 1: Read the decision off the pinned tests**

The "no new key" decision holds if and only if Task 2 Step 2 and Task 5 Step 3 both passed (journal head is a `run_upsert` carrying `milestone_id`, and every line shares one `run_id`). Expected: both passed, so no production change is made. Go to Step 3.

- [ ] **Step 2: Only if the gate tripped — stop and escalate, do not improvise**

If Task 2 or Task 5 showed a journal whose first line is not a `run_upsert` with `milestone_id`, or a journal mixing run ids, the spec's fallback (an additive `milestone_id` on the `story_upsert` payload, schema stays 1) applies. Do not implement it inside this plan: `store.replay` validates each `story_upsert` payload with `models.StoryRun.model_validate`, and `models._Model` forbids extra keys, so a bare extra payload key would make `rebuild_from_journal` raise on every new journal. The fallback therefore needs a model-level change (a defaulted `milestone_id: str | None = None` on `StoryRun`, or `record_story` taking the milestone id) that the spec did not design. Report the failing assertion and this constraint, and leave the card for a spec revision.

- [ ] **Step 2b: Confirm no source change slipped in**

Run: `git diff --stat ami/task-4-2-logs-follow-end-9b7d0364 -- src/`
Expected: empty output (no file under `src/` changed on this branch).

- [ ] **Step 3: Run the default suite**

Run: `uv run pytest`
Expected: all tests PASS, including the four new unit tests from Tasks 1-4.

- [ ] **Step 4: Run the opt-in board scenario once more**

Run: `uv run pytest -m e2e_fake tests/e2e/test_run_board.py -v`
Expected: every e2e_fake test in the module PASSES (requires `brd` and the fake claude fixture; if unavailable, say so in the report).

- [ ] **Step 5: Final state check**

Run: `git status` and `git log --oneline ami/task-4-2-logs-follow-end-9b7d0364..HEAD`
Expected: clean working tree; five commits, one per Task 1-5, touching only `tests/test_orchestrate.py`, `tests/test_store.py`, `tests/test_cli.py`, `tests/e2e/test_run_board.py`.
