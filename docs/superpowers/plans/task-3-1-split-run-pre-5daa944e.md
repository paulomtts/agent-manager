<!-- task-pipeline: validated -->
# 3.1 Split run pre-flight from the engine hand-off (card 5daa944e)

Parent story fea654ef "am run --detach". Source design: `docs/superpowers/specs/2026-10-03-plugin-integration-design.md` section 2 (the whole pre-flight runs in the foreground with today's refusals, the run is created and its lease taken, and only then is the engine handed off) and its Compatibility section (additive only, journal schema 1).

## Scope

This is a behaviour-preserving refactor of `src/agent_manager/cli.py` (`run_card`) and `src/agent_manager/orchestrate.py` (`run_milestone` / `_run_milestone_async`). Each run path gets an explicit seam with three stages:

1. Pre-flight. Reads and refusals only, ending with the run id minted and the run record built. The one exception is today's fresh-milestone `refresh_git` (fetch and prune), which stays where it is, after the last refusal and before `Store.open`; it is not a store or run-directory write.
2. Recorded. The store is opened, the lease and claims are taken, and the run's `started` rows are written. The result is "pre-flight done, run recorded and leased".
3. Engine. Everything from the first engine-side action through the final records and the report.

The existing sync wrappers compose these three stages and behave exactly as they do today. A later caller (3.2's detached child) can then run stage 3 by itself against a run that stages 1 and 2 already produced. This card does not build that caller.

Out of scope, owned by siblings: the `--detach` flag, the child process, `run.log`, `report.json`, refusing `--dry-run` with `--detach` (3.2, aff9fdbf), and the e2e_fake test (3.3, ef6e5633). This card adds no CLI flag, makes no user-visible change, adds no JSON key, and makes no journal or schema change.

## The seam

The names below are the ones the tests use. Internal-only state uses plain frozen dataclasses (CLAUDE.md: Pydantic is only for boundary-validated models).

### `--card` path (cli.py)

- `preflight_card(card_id, *, repo_dir, branch_prefix, base_branch, clock) -> CardPreflight`. It runs, in today's order: `resolve_repo_dir`, `board.show` of the card, `ParentlessCardError`, `board.show` of the parent, `dag.task_branch`, `worktree_for`, the `card:<id>` claim, `refuse_claimed`, `clock()` and `mint_run_id`. It also builds the `models.Run`, `StoryRun` and `SubtaskRun` records with status `started`. It has no side effects: it creates no run directory and opens no store.
- `recorded_card_run(pre) -> ContextManager[RecordedRun]`. It runs `Store.open`, then `run_lease(store, claims=pre.claims)`, then `record_run`, `record_story` and `record_subtask`. It yields an object that exposes at least `run_id`, `store` and `lease`. On every exit, including an exception raised in the body, it releases the lease and claims (`Lease.__exit__`) and only then calls `store.close()`.
- Engine: the body that is left, a coroutine `run_card_engine(pre, recorded, *, commands, allow_no_verification, runner_factory, control_interval)`. It creates the `StopSignal`, runs `control.controlled(drive_subtask_async(...), lease=lease, ...)`, records the outcome, writes the `card_outcome_comment` and its warnings, and returns the payload. `lease.token` reaches `card_outcome_comment` exactly as it does today.
- `run_card` keeps its signature, return payload and exceptions. It becomes `pre = preflight_card(...)`, then `with recorded_card_run(pre) as rec:`, then the engine under `asyncio.run(...)`.

### `--milestone` path (orchestrate.py)

- `preflight_milestone(milestone, *, repo_dir, base_branch, branch_prefix, max_concurrent, clock, resume_run_id) -> MilestonePreflight`. It runs, in today's order: `resolve_repo_dir`, `resumable_milestone_run` (resume only), `board.roots`, `census.find_milestone` (the ambiguity and unknown refusals) or `find_run_milestone`, `board.tree` and `flatten_milestone`, `plan_levels` (the cycle refusal), `story_tips`, `milestone_claims`, and `cli.refuse_claimed` as the last refusal. A fresh run then continues with `refresh_git`, `clock()`, `mint_run_id` and `models.Run` (with `milestone_id` set). A resume continues with the `model_copy` of the resumed run. It never opens the store, so every refusal still leaves no fetch, no prune, no run row and no run directory.
- `recorded_milestone_run(pre) -> ContextManager[RecordedRun]`. It runs `Store.open`. On a resume it then runs `open_cards` and `resume_checkpoints` (read-only, before the lease). Then it takes `cli.run_lease(store, claims=pre.keys)`. On a resume, `refresh_git` runs under the lease. Then `record_run`, `record_plan` and, on a resume, `reopen_rows`. It yields `run_id`, `store`, `lease`, the plan `rows` and `checkpoints`. Its exit order is the same as the card's: lease and claims first, then `store.close()`.
- Engine (async), `run_milestone_engine(pre, recorded, *, commands, allow_no_verification, runner_factory, driver, max_concurrent, control_interval, slots)`. `pre` must carry everything the body reads after the recorded stage: `root`, `milestone_card`, `plan`, `levels`, `tips`, `keys`, `base_branch`, `branch_prefix`, `run_record`, `resumed` and the chosen `drive` (`cli.drive_subtask_async` unless `driver` is given). It starts at `comments.flush` and `reroll_stale_stories`, then runs `control.controlled(supervise(..., lease_token=lease.token, slots=slots), lease=lease, ...)`. After that come today's outcome handling, Integrate and `report` (including `resumed` and `took_over`). Board comment flushing is engine work, not pre-flight.
- `_run_milestone_async` keeps its signature (including `slots` and `resume_run_id`) and composes the three stages. `run_milestone` keeps its argument validation and its `asyncio.run(_run_milestone_async(...))`. `run_board` and `_run_board_async` are not split at their own level. They keep calling `_run_milestone_async` per milestone and inherit the seam. `resume_run` and `_resume_from_checkpoint` keep reaching `_run_milestone_async` through `resume_run_id`, with no change.
- The `cli.run` dispatch, `_check_run_targets`, the `HANDLED` mapping to the exit-3 envelope, and the attribute call sites `orchestrate.run_milestone` and `orchestrate.run_board` (which tests patch) are unchanged.

### Docstrings

Update the docstrings of `run_card`, `run_milestone` and `_run_milestone_async` so they name the three stages and say that the sync wrappers compose them with `asyncio.run`. The load-bearing ordering statements stay true and are moved onto the stage that now owns them:

- every refusal comes before any side effect;
- `refuse_claimed` comes before `refresh_git` and `Store.open`;
- the lease is taken before `record_run`;
- the lease and claims are released before `store.close()`.

## Behaviour that must not change

- Every refusal fires at the same point, as the same exception, with the same envelope and exit code: `ParentlessCardError`, `ClaimedError`, `RunIsLiveError`, an unknown or ambiguous milestone, a blocker cycle, a resume refusal, and a checkpoint under another workflow digest.
- The "missing verification" refusal (`reducers.verification_gate`, steps/reducers.py:30, with siblings in bases.py and integration.py) stays inside the engine's explore phase. This card does not move it. The seam forwards `allow_no_verification` to the engine unchanged. Moving it into pre-flight is a behaviour change, and no existing test proves that move safe.
- Payloads and journal rows are byte-for-byte as today. No new JSON keys are added.
- A crash in the engine still propagates, still releases the lease and claims, and still closes the store.

## Error paths for the seam itself

- When `preflight_*` raises, nothing has been written. There is no run directory under `<data dir>/runs/` and no `run_leases` row.
- When the lease is lost to a race inside `recorded_*_run`, entry raises `ClaimedError` or `RunIsLiveError` with no run row written (the empty run directory is the same as today), and the store is closed.
- When code inside the `recorded_*_run` block raises, including when the engine is never started, the lease and claims are released first and the store is closed after.

## Tests (TDD: write them first; the existing suite stays unmodified and green under `uv run pytest`)

Tier rule: CLAUDE.md "Test tiers". The tier depends on what a test spawns, not on which directory it is in. None of the tests below may start a subprocess. Use the `fake_board` fixture from tests/conftest.py, which installs FakeBoard as `board.run_brd`. Use a plain `tmp_path` directory as `repo_dir`, not the git-initialising `project` fixture. Point `XDG_DATA_HOME` into `tmp_path`. For the milestone tests, monkeypatch `orchestrate.refresh_git` to a recorder. Fake the drive with `FakeDriver` (orchestrate) or a fake `drive_subtask_async` (cli). Under those conditions every test below is **unit tier (unmarked)** and must stay within 0.5s.

tests/test_cli.py, all unit:
1. `preflight_card` on a parentless card raises `ParentlessCardError` and creates no run directory.
2. `preflight_card` on a card another live run claims raises `ClaimedError` and creates no run directory. Seed the claim the way the existing `run_leases`/`run_claims` helper near test_cli.py:6571 does.
3. `preflight_card` returns the minted run id, branch, worktree and `started` records, and writes nothing.
4. While inside `recorded_card_run`, the store holds the `started` run, story and subtask rows, the lease is live with the `card:<id>` claim, and `drive_subtask_async` has not been called (patch it to fail if called).
5. Leaving `recorded_card_run` with an exception, without running the engine, releases the claim and the lease before `store.close()`, and a second `preflight_card` for the same card is not refused.
6. `run_card` hands the engine the lease from the recorded stage: `controlled` and `card_outcome_comment` receive that lease and its token, shown through a fake drive.

tests/test_orchestrate.py, all unit:
7. `preflight_milestone` on a blocker cycle raises the same refusal as test_orchestrate.py:133, never calls `refresh_git`, and creates no run directory.
8. `preflight_milestone` on an ambiguous milestone needle raises the existing ambiguity error before `refresh_git`, and creates no run directory.
9. `preflight_milestone` with a claimed key raises `ClaimedError` before `refresh_git`.
10. A fresh `preflight_milestone` calls `refresh_git` once, after every refusal, and returns a `models.Run` with `milestone_id` set. It creates no run directory.
11. While inside `recorded_milestone_run`, the run is recorded `started`, every planned row is `pending`, the lease is live with all `milestone_claims` keys, and neither the driver nor `comments.flush` has been called.
12. Leaving `recorded_milestone_run` with an exception, without starting the engine, releases the claims and the lease before `store.close()`.
13. The milestone engine, given a recorded run (the autouse `integrate_recorder` in test_orchestrate.py stands in for Integrate, so no git runs), passes `lease.token` to `supervise` and the same lease to `controlled`. With a `FakeDriver` it ends `done` with the same payload `run_milestone` returns today.

---

# Split run pre-flight from the engine hand-off Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split `cli.run_card` and `orchestrate._run_milestone_async` into three explicit stages (pre-flight, recorded, engine) without changing any behaviour, so a later detached child can run the engine against a run that already exists and is leased.

**Architecture:** Each path gets a pure-ish `preflight_*` function returning a frozen dataclass, a `recorded_*_run` `@contextmanager` that opens the store, takes `run_lease` and writes the `started` rows (yielding a frozen dataclass with `run_id`, `store`, `lease`), and an `async` `run_*_engine` that holds today's remaining body. The existing entry points (`run_card`, `_run_milestone_async`) are rewritten as a composition of the three, keeping their signatures, `asyncio.run` placement and every patched attribute name.

**Tech Stack:** Python 3, Typer, Pydantic models (`agent_manager.models`), sqlite-backed `Store`, `control.Lease`, pytest with `asyncio_mode = "auto"`, the conftest `FakeBoard` behind `board.run_brd`.

**Spec:** `/home/mtts/Code/agent-manager/.claude/worktrees/ami/task-3-1-split-run-pre-5daa944e/docs/superpowers/specs/task-3-1-split-run-pre-5daa944e-design.md` (reproduced verbatim above).

## Global Constraints

- Behaviour-preserving: no new CLI flag, no user-visible change, no new JSON key, no journal or schema change (journal stays schema 1).
- Existing tests must stay byte-for-byte unmodified and green. Only additive edits to test files: new imports at the top and new tests/helpers appended at the end.
- `run_card`, `run_milestone`, `_run_milestone_async`, `run_board`, `resume_run`, `_resume_from_checkpoint` and `cli.run` keep their signatures (tests/test_orchestrate.py:2604 pins `_run_milestone_async` == `run_milestone` + `slots`).
- Module-attribute call sites stay patchable: `cli.drive_subtask_async`, `cli.StopSignal`, `cli.Store`, `cli.refuse_claimed`, `cli.run_lease`, `cli.card_outcome_comment`, `control.controlled`, `orchestrate.refresh_git`, `orchestrate.supervise`, `integration.integrate_milestone`, `comments.flush`, `orchestrate.run_milestone`, `orchestrate.run_board`. New code must read them by the same names at call time.
- `orchestrate` reads every `cli` name at call time (circular import), never at import/definition time.
- The "missing verification" refusal stays in the engine's explore phase; `allow_no_verification` is passed through unchanged.
- Internal state uses `@dataclass(frozen=True)`, not Pydantic (CLAUDE.md).
- Every new test is unit tier (unmarked): no subprocess, `fake_board`, plain `tmp_path` repo dir, `XDG_DATA_HOME` under `tmp_path`, `orchestrate.refresh_git` patched in milestone tests, at most 0.5s each. Tests live in `tests/test_cli.py` and `tests/test_orchestrate.py` (mirroring `src/agent_manager/cli.py` and `orchestrate.py`); they are not under `tests/steps/` or `tests/e2e/`, so no directory auto-mark applies.
- Verification: `uv run pytest` (no lint, no typecheck).

## Review Focus

- A resumed milestone run going through the seam: `refresh_git` must move out of pre-flight on a resume and run under the lease in the recorded stage, with `max_concurrent`, `base_branch` and `branch_prefix` taken from the recorded run. Pinned by `test_a_resumed_milestone_preflight_leaves_refresh_git_to_the_recorded_stage` (Task 5).
- A resume whose checkpoint was saved under another workflow digest: the recorded stage must refuse before the lease is taken and before `record_run`, leaving the run's old status and no claims. Pinned by `test_a_resume_checkpoint_under_another_digest_is_refused_before_the_lease` (Task 5).
- Another process claiming the card between pre-flight and the recorded stage (the lost race): entry must raise `ClaimedError`, write no run row and still close the store. Pinned by `test_a_claim_taken_after_card_preflight_is_refused_on_entry_with_nothing_recorded` (Task 2).
- A crash inside the card engine (the drive raises): it must propagate out of `run_card` and the claim and lease must be gone before `store.close()`. Pinned by `test_a_crashing_card_engine_still_releases_the_claim_and_lease_before_closing` (Task 3).
- A crash late in the milestone engine (Integrate raises): it must propagate out of `run_milestone` and claims and lease must be released before `store.close()`. Pinned by `test_a_crashing_milestone_engine_still_releases_its_lease_before_closing` (Task 6).

Spec deviations, decided here and to be kept:
- Test 7 (blocker cycle): through the board, a cycle between sibling stories is refused one step earlier by `census.flatten_milestone` (`census.CensusOrderError` from `order_siblings`), so it never reaches `plan_levels`. To hit "the same refusal as test_orchestrate.py:133" (`dag.DependencyCycleError`), the test patches `census.flatten_milestone` to return two cyclic `StoryPlan`s. Both refusals come before `refresh_git`; production order is unchanged.
- `run_milestone_engine` takes no `driver` or `max_concurrent` keyword. Both are carried on `MilestonePreflight` (`pre.drive`, `pre.max_concurrent`) because a resume overrides `max_concurrent` with the recorded run's `config.max_concurrent_stories` during pre-flight. Taking them again on the engine would let the two disagree.
- The milestone recorded stage yields `RecordedMilestoneRun` (with `rows` and `checkpoints`); the card one yields `cli.RecordedRun`. Both expose `run_id`, `store` and `lease`.

---

## File Structure

- Modify `src/agent_manager/cli.py`: add `CardPreflight`, `RecordedRun`, `preflight_card`, `recorded_card_run` and `run_card_engine` between `card_outcome_comment` and `run_card` (around line 841), then rewrite `run_card` (lines 842-993) as their composition.
- Modify `src/agent_manager/orchestrate.py`: add `Iterator` and `contextmanager` imports; add `MilestonePreflight`, `RecordedMilestoneRun`, `preflight_milestone`, `recorded_milestone_run` and `run_milestone_engine` between `milestone_card_ids` and `run_milestone` (around line 1556); edit the `run_milestone` docstring; rewrite `_run_milestone_async` as their composition.
- Modify `tests/test_cli.py`: add `from contextlib import contextmanager` to the imports, append the card seam helpers and tests.
- Modify `tests/test_orchestrate.py`: add `from agent_manager import comments` to the imports, append the milestone seam helpers and tests.

---

### Task 1: `preflight_card`

**Files:**
- Modify: `src/agent_manager/cli.py` (insert before `def run_card(` at line 842)
- Test: `tests/test_cli.py` (append at end of file)

**Interfaces:**
- Consumes: `resolve_repo_dir`, `board.show`, `ParentlessCardError`, `dag.task_branch`, `worktree_for`, `control.card_claim`, `refuse_claimed`, `mint_run_id`, `WORKFLOW_NAME` (all already in `cli.py`).
- Produces: `cli.CardPreflight` (frozen dataclass: `root: Path`, `card: models.Card`, `parent: models.Card`, `branch: str`, `worktree: Path`, `base_branch: str`, `claims: list[str]`, `run_id: str`, `run_record: models.Run`, `story: models.StoryRun`, `subtask: models.SubtaskRun`) and `cli.preflight_card(card_id: str, *, repo_dir: Path, branch_prefix: str, base_branch: str = "master", clock: Callable[[], datetime] = _utcnow) -> CardPreflight`.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_cli.py`:

```python
# ── run pre-flight, recorded stage and engine seam (card 5daa944e) ──────────
#
# Unit tier: the FakeBoard (`fake_board`) answers every board call, the repo
# dir is a plain directory, and no git, brd or claude process ever starts.

SEAM_AT = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)
SEAM_LATER = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)


def _seam_root(tmp_path: Path, monkeypatch) -> Path:
    """A plain repo directory (no git, no brd) with the data dir under tmp_path."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    root = tmp_path / "repo"
    root.mkdir()
    return root.resolve()


def _seam_cards(fake_board) -> dict[str, str]:
    """The milestone -> story -> subtask chain `run --card` needs, on the FakeBoard."""
    milestone = fake_board.add_card("Milestone 1: walking skeleton")
    story = fake_board.add_card("The CLI: run, status, logs, resume", parent_id=milestone)
    subtask = fake_board.add_card("Add run --card end to end", parent_id=story)
    return {"milestone": milestone, "story": story, "subtask": subtask}


def _preflight(root: Path, card_id: str, at: datetime = SEAM_AT) -> Any:
    return cli.preflight_card(
        card_id, repo_dir=root, branch_prefix="m1", base_branch="main", clock=lambda: at
    )


def test_preflight_card_refuses_a_parentless_card_and_creates_no_run_directory(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    loose = fake_board.add_card("A card with no story")

    with pytest.raises(cli.ParentlessCardError):
        _preflight(root, loose)

    assert _run_dirs() == []


def test_preflight_card_refuses_a_card_another_live_run_claims(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    key = control.card_claim(cards["subtask"])
    _plant_lease(
        root,
        run_id=OTHER_RUN_ID,
        token="other-life",
        heartbeat_at=datetime.now(timezone.utc),
        claims=(key,),
    )

    with pytest.raises(cli.ClaimedError) as caught:
        _preflight(root, cards["subtask"])

    assert (caught.value.key, caught.value.run_id) == (key, OTHER_RUN_ID)
    assert _run_dirs() == []
    assert _claim_rows(root) == [(key, OTHER_RUN_ID, "other-life")]


def test_preflight_card_returns_the_run_it_would_record_and_writes_nothing(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    card = board.show(cards["subtask"], repo_dir=root)
    branch = dag.task_branch("m1", card)

    pre = _preflight(root, cards["subtask"])

    assert pre.root == root
    assert (pre.card.id, pre.parent.id) == (cards["subtask"], cards["story"])
    assert pre.run_id == cli.mint_run_id(cards["subtask"], SEAM_AT)
    assert pre.branch == branch
    assert pre.worktree == cli.worktree_for(root, branch)
    assert pre.base_branch == "main"
    assert pre.claims == [control.card_claim(cards["subtask"])]
    assert (pre.run_record.id, pre.run_record.status) == (pre.run_id, "started")
    assert (pre.run_record.workflow, pre.run_record.started_at) == (cli.WORKFLOW_NAME, SEAM_AT)
    assert (pre.run_record.base_branch, pre.run_record.branch_prefix) == ("main", "m1")
    assert (pre.story.card_id, pre.story.status, pre.story.tip_branch) == (
        cards["story"],
        "started",
        branch,
    )
    assert (pre.subtask.card_id, pre.subtask.status, pre.subtask.branch) == (
        cards["subtask"],
        "started",
        branch,
    )
    assert pre.subtask.worktree_path == cli.worktree_for(root, branch)
    assert _run_dirs() == []
    assert _recorded_run_ids(root) == []
    assert _claim_rows(root) == []
    assert fake_board.writes == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "preflight_card" -v`
Expected: 3 FAILED with `AttributeError: module 'agent_manager.cli' has no attribute 'preflight_card'`.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/cli.py`, insert immediately above `def run_card(` (after `card_outcome_comment`, which ends with `    return None`):

```python
@dataclass(frozen=True)
class CardPreflight:
    """What `preflight_card` read and decided for one `run --card` (card 5daa944e).

    Everything the recorded stage and the engine read afterwards, built with
    no side effect: no store, no run directory, no lease. The three records
    are the `started` rows `recorded_card_run` writes. Internal state, so a
    dataclass.
    """

    root: Path
    card: models.Card
    parent: models.Card
    branch: str
    worktree: Path
    base_branch: str
    claims: list[str]
    run_id: str
    run_record: models.Run
    story: models.StoryRun
    subtask: models.SubtaskRun


def preflight_card(
    card_id: str,
    *,
    repo_dir: Path,
    branch_prefix: str,
    base_branch: str = "master",
    clock: Callable[[], datetime] = _utcnow,
) -> CardPreflight:
    """Stage 1 of `run --card`: every board read and refusal, then the run id (card 5daa944e).

    In today's order: the card, `ParentlessCardError`, its parent, the branch
    and worktree, then `refuse_claimed` over the `card:<id>` claim, read-only
    and before any store exists, so a refused card leaves no run directory
    (X5). Only then is the clock read and the run id minted, and the
    `started` run, story and subtask records built. Nothing is written.
    """
    root = resolve_repo_dir(repo_dir)
    card = board.show(card_id, repo_dir=root)
    if not card.parent_id:
        raise ParentlessCardError(
            f"card {card.id} ({card.title!r}) has no parent card; `run --card` drives "
            "a subtask of a story, and the story is what every run record is keyed by"
        )
    parent = board.show(card.parent_id, repo_dir=root)

    branch = dag.task_branch(branch_prefix, card)
    worktree = worktree_for(root, branch)
    claims = [control.card_claim(card.id)]
    # Read-only and before `Store.open`, so a refused card leaves no run
    # directory (X5); `take_lease` in the recorded stage re-checks atomically.
    refuse_claimed(root, claims)
    started_at = clock()
    run_id = mint_run_id(card.id, started_at)
    return CardPreflight(
        root=root,
        card=card,
        parent=parent,
        branch=branch,
        worktree=worktree,
        base_branch=base_branch,
        claims=claims,
        run_id=run_id,
        run_record=models.Run(
            id=run_id,
            workflow=WORKFLOW_NAME,
            repo_dir=root,
            base_branch=base_branch,
            branch_prefix=branch_prefix,
            status="started",
            started_at=started_at,
            config=models.RunConfig(),
        ),
        story=models.StoryRun(
            card_id=parent.id,
            title=parent.title,
            level=0,
            status="started",
            tip_branch=branch,
        ),
        subtask=models.SubtaskRun(
            card_id=card.id,
            branch=branch,
            base_branch=base_branch,
            status="started",
            worktree_path=worktree,
        ),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "preflight_card" -v`
Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "refactor(cli): add preflight_card, the side-effect-free stage of run --card"
```

---

### Task 2: `recorded_card_run`

**Files:**
- Modify: `src/agent_manager/cli.py` (insert after `preflight_card`, before `def run_card(`)
- Modify: `tests/test_cli.py` (imports at top; append at end)

**Interfaces:**
- Consumes: `CardPreflight` and `preflight_card` from Task 1; `Store`, `run_lease`, `control.Lease`.
- Produces: `cli.RecordedRun` (frozen dataclass: `run_id: str`, `store: Store`, `lease: control.Lease`) and `cli.recorded_card_run(pre: CardPreflight) -> Iterator[RecordedRun]`, decorated with `@contextmanager`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli.py`, add this import directly below `from datetime import datetime, timedelta, timezone` (line 26):

```python
from contextlib import contextmanager
```

Append to the end of `tests/test_cli.py`:

```python
def _close_snapshots(monkeypatch) -> list[tuple[int, int]]:
    """Patch `Store.close` to record `(claims, leases)` its run still holds as it closes.

    `(0, 0)` means the claims and the lease were released before the store
    closed. Counted over the closing store's own connection, before the real
    close runs.
    """
    seen: list[tuple[int, int]] = []
    real_close = store_module.Store.close

    def close(self) -> None:
        conn = self.connection
        claims = conn.execute(
            "SELECT COUNT(*) FROM run_claims WHERE run_id = ?", (self.run_id,)
        ).fetchone()[0]
        leases = conn.execute(
            "SELECT COUNT(*) FROM run_leases WHERE run_id = ?", (self.run_id,)
        ).fetchone()[0]
        seen.append((claims, leases))
        real_close(self)

    monkeypatch.setattr(store_module.Store, "close", close)
    return seen


def _no_drive(**kwargs: Any) -> Any:
    pytest.fail("drive_subtask_async ran in the recorded stage")


def test_inside_recorded_card_run_the_run_is_recorded_and_leased_but_not_driven(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    monkeypatch.setattr(cli, "drive_subtask_async", _no_drive)
    pre = _preflight(root, cards["subtask"])

    with cli.recorded_card_run(pre) as recorded:
        assert recorded.run_id == pre.run_id
        run = recorded.store.load_run(pre.run_id)
        assert run is not None
        (story,) = run.stories
        (subtask,) = story.subtasks
        assert (run.status, story.status, subtask.status) == ("started", "started", "started")
        assert (story.card_id, subtask.card_id) == (cards["story"], cards["subtask"])
        lease = _card_lease(root, pre.run_id)
        assert lease is not None
        assert lease.token == recorded.lease.token
        assert _claim_rows(root) == [
            (control.card_claim(cards["subtask"]), pre.run_id, recorded.lease.token)
        ]


def test_leaving_recorded_card_run_on_an_error_releases_the_claim_and_lease_before_closing(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    closes = _close_snapshots(monkeypatch)
    pre = _preflight(root, cards["subtask"])

    with pytest.raises(RuntimeError, match="engine never started"):
        with cli.recorded_card_run(pre):
            raise RuntimeError("engine never started")

    assert closes == [(0, 0)]
    assert _claim_rows(root) == []
    assert _card_lease(root, pre.run_id) is None
    again = _preflight(root, cards["subtask"], SEAM_LATER)
    assert again.run_id == cli.mint_run_id(cards["subtask"], SEAM_LATER)


def test_a_claim_taken_after_card_preflight_is_refused_on_entry_with_nothing_recorded(
    tmp_path, monkeypatch, fake_board
):
    """The lost race (spec, Error paths): another run claims the card between
    pre-flight and the recorded stage. `take_lease` refuses it atomically,
    nothing is recorded and the store is still closed."""
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    closes = _close_snapshots(monkeypatch)
    pre = _preflight(root, cards["subtask"])
    key = control.card_claim(cards["subtask"])
    _plant_lease(
        root,
        run_id=OTHER_RUN_ID,
        token="other-life",
        heartbeat_at=datetime.now(timezone.utc),
        claims=(key,),
    )

    with pytest.raises(cli.ClaimedError) as caught:
        with cli.recorded_card_run(pre):
            pytest.fail("the recorded stage yielded under another run's claim")

    assert (caught.value.key, caught.value.run_id) == (key, OTHER_RUN_ID)
    assert closes == [(0, 0)]
    assert _recorded_run_ids(root) == []
    assert _claim_rows(root) == [(key, OTHER_RUN_ID, "other-life")]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "recorded_card_run or claim_taken_after_card_preflight" -v`
Expected: 3 FAILED with `AttributeError: module 'agent_manager.cli' has no attribute 'recorded_card_run'`.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/cli.py`, insert directly after the end of `preflight_card` (its closing `    )`), still above `def run_card(`:

```python
@dataclass(frozen=True)
class RecordedRun:
    """A run past its recorded stage: its id, its open store and the lease it holds.

    What the engine of `run --card` needs that pre-flight could not give it
    (card 5daa944e). Internal state, so a dataclass.
    """

    run_id: str
    store: Store
    lease: control.Lease


@contextmanager
def recorded_card_run(pre: CardPreflight) -> Iterator[RecordedRun]:
    """Stage 2 of `run --card`: open the store, take the lease, record `started` (card 5daa944e).

    The lease and the `card:<id>` claim are taken inside the `try` that closes
    the store, so they are released before `store.close()` on every exit, an
    exception in the block included (C2, X5). They are taken before
    `record_run`, so every run write is fenced by this token; a lost race is
    `ClaimedError` (or `RunIsLiveError`) with nothing written but the empty
    run directory. The run, story and subtask rows are written before the
    block runs, so `status` and `resume` can see a run that dies on its first
    phase.
    """
    store = Store.open(pre.root, pre.run_id)
    try:
        with run_lease(store, claims=pre.claims) as lease:
            store.record_run(pre.run_record)
            store.record_story(pre.story)
            store.record_subtask(pre.story.card_id, pre.subtask)
            yield RecordedRun(run_id=pre.run_id, store=store, lease=lease)
    finally:
        store.close()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "preflight_card or recorded_card_run or claim_taken_after_card_preflight" -v`
Expected: 6 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "refactor(cli): add recorded_card_run, the store/lease/started-rows stage"
```

---

### Task 3: `run_card_engine` and `run_card` as the composition

**Files:**
- Modify: `src/agent_manager/cli.py` (insert `run_card_engine` after `recorded_card_run`; replace the whole `def run_card(` through its `    finally:\n        store.close()`, which sits just above `DEFAULT_MAX_CONCURRENT = 4`)
- Test: `tests/test_cli.py` (append at end)

**Interfaces:**
- Consumes: `CardPreflight`, `preflight_card` (Task 1); `RecordedRun`, `recorded_card_run` (Task 2); `drive_subtask_async`, `StopSignal`, `control.controlled`, `card_run_status`, `card_outcome_comment`, `orchestrate.post_comment`.
- Produces: `async def cli.run_card_engine(pre: CardPreflight, recorded: RecordedRun, *, commands: Sequence[str] = (), allow_no_verification: bool = False, runner_factory: RunnerFactory | None = None, control_interval: float = control.CONTROL_POLL_SECONDS) -> dict[str, Any]`. `run_card` keeps its exact signature.

- [ ] **Step 1: Write the tests**

Append to the end of `tests/test_cli.py`:

```python
def _done_drive(calls: list[dict[str, Any]]):
    """A fake `drive_subtask_async` that records its keywords and finishes `done`."""

    async def drive(**kwargs: Any) -> cli.SubtaskDrive:
        calls.append(kwargs)
        return cli.SubtaskDrive(summary=SubtaskSummary(status="done"), warnings=[])

    return drive


def test_run_card_hands_the_engine_the_lease_of_the_recorded_stage(
    tmp_path, monkeypatch, fake_board
):
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(cli, "drive_subtask_async", _done_drive(calls))
    handed: dict[str, Any] = {}

    real_recorded = cli.recorded_card_run

    @contextmanager
    def spying_recorded(pre):
        with real_recorded(pre) as recorded:
            handed["recorded"] = recorded
            yield recorded

    real_controlled = control.controlled

    async def spying_controlled(work, **kwargs):
        handed["controlled"] = kwargs["lease"]
        return await real_controlled(work, **kwargs)

    real_comment = cli.card_outcome_comment

    def spying_comment(**kwargs):
        handed["token"] = kwargs["token"]
        return real_comment(**kwargs)

    monkeypatch.setattr(cli, "recorded_card_run", spying_recorded)
    monkeypatch.setattr(control, "controlled", spying_controlled)
    monkeypatch.setattr(cli, "card_outcome_comment", spying_comment)

    result = cli.run_card(
        cards["subtask"],
        repo_dir=root,
        branch_prefix="m1",
        base_branch="main",
        clock=lambda: SEAM_AT,
        control_interval=0.01,
    )

    recorded = handed["recorded"]
    assert handed["controlled"] is recorded.lease
    assert handed["token"] == recorded.lease.token
    assert recorded.run_id == result["run_id"] == cli.mint_run_id(cards["subtask"], SEAM_AT)
    assert [call["run_id"] for call in calls] == [result["run_id"]]
    assert calls[0]["store"] is recorded.store
    assert result["status"] == "done"
    assert _claim_rows(root) == []


def test_a_crashing_card_engine_still_releases_the_claim_and_lease_before_closing(
    tmp_path, monkeypatch, fake_board
):
    """Spec: a crash in the engine still propagates, still releases the lease
    and claims, and still closes the store. A characterization pin: it passes
    before the split and must keep passing after it."""
    root = _seam_root(tmp_path, monkeypatch)
    cards = _seam_cards(fake_board)

    async def crashing_drive(**kwargs: Any) -> Any:
        raise RuntimeError("drive bug")

    monkeypatch.setattr(cli, "drive_subtask_async", crashing_drive)
    closes = _close_snapshots(monkeypatch)

    with pytest.raises(RuntimeError, match="drive bug"):
        cli.run_card(
            cards["subtask"],
            repo_dir=root,
            branch_prefix="m1",
            base_branch="main",
            clock=lambda: SEAM_AT,
            control_interval=0.01,
        )

    assert closes == [(0, 0)]
    assert _claim_rows(root) == []
```

- [ ] **Step 2: Run the tests to verify the first fails and the pin passes**

Run: `uv run pytest tests/test_cli.py -k "hands_the_engine_the_lease or crashing_card_engine" -v`
Expected: `test_run_card_hands_the_engine_the_lease_of_the_recorded_stage` FAILED with `KeyError: 'recorded'` (today's `run_card` never calls `cli.recorded_card_run`); `test_a_crashing_card_engine_still_releases_the_claim_and_lease_before_closing` PASSED (characterization pin).

- [ ] **Step 3: Add `run_card_engine`**

In `src/agent_manager/cli.py`, insert directly after `recorded_card_run` (after its `        store.close()`), still above `def run_card(`:

```python
async def run_card_engine(
    pre: CardPreflight,
    recorded: RecordedRun,
    *,
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    runner_factory: RunnerFactory | None = None,
    control_interval: float = control.CONTROL_POLL_SECONDS,
) -> dict[str, Any]:
    """Stage 3 of `run --card`: walk a recorded, leased run and report (card 5daa944e).

    The walk runs under `control.controlled` with the recorded stage's lease,
    which polls for `am pause`/`am cancel` every `control_interval` seconds
    and turns one into `stop.request` (C11). Then the outcome rows are
    recorded and, still under the lease, the card gets at most one comment
    (`card_outcome_comment`, keyed by `lease.token`); a flush's warnings join
    the payload's `warnings`. The caller owns the store and the lease.
    """
    store, lease, run_id = recorded.store, recorded.lease, recorded.run_id
    stop = StopSignal()
    # `controlled` only ever parks the walk through `stop` (C3); it
    # closes the window and runs a final sweep before returning.
    drive = await control.controlled(
        drive_subtask_async(
            store=store,
            run_id=run_id,
            card=pre.card,
            parent=pre.parent,
            subtask=pre.subtask,
            repo_dir=pre.root,
            commands=commands,
            allow_no_verification=allow_no_verification,
            runner_factory=runner_factory,
            stop=stop,
        ),
        store=store,
        stop=stop,
        lease=lease,
        interval=control_interval,
    )
    summary = drive.summary
    run_status = card_run_status(summary, stop)

    store.record_run(pre.run_record.model_copy(update={"status": run_status}))
    store.record_story(pre.story.model_copy(update={"status": summary.status}))
    store.record_subtask(
        pre.story.card_id, pre.subtask.model_copy(update={"status": summary.status})
    )

    # Board-comments B2 (card 5d9a875f): after the outcome is recorded and
    # still under the lease, so the outbox write is fenced. A board
    # failure is a warning (B8); a lost lease propagates.
    comment = card_outcome_comment(
        run_id=run_id,
        card=pre.card,
        summary=summary,
        stop=stop,
        branch=pre.branch,
        token=lease.token,
    )
    if comment is not None:
        # `orchestrate` imports `cli`, so it is read here, at call time.
        from agent_manager import orchestrate

        drive.warnings.extend(orchestrate.post_comment(store, pre.root, comment, run_id=run_id))

    return {
        "run_id": run_id,
        "card_id": pre.card.id,
        "story_id": pre.parent.id,
        "branch": pre.branch,
        "base_branch": pre.base_branch,
        "worktree": str(pre.worktree),
        "status": run_status,
        "failed_phase": summary.failed_phase,
        "detail": summary.detail,
        "skipped": list(summary.skipped),
        "warnings": drive.warnings,
    }
```

- [ ] **Step 4: Rewrite `run_card` as the composition**

Replace the whole of `run_card` in `src/agent_manager/cli.py`, from `def run_card(` down to and including its final lines

```python
    finally:
        store.close()
```

(the ones directly above `DEFAULT_MAX_CONCURRENT = 4`), with:

```python
def run_card(
    card_id: str,
    *,
    repo_dir: Path,
    branch_prefix: str,
    base_branch: str = "master",
    allow_no_verification: bool = False,
    commands: Sequence[str] = (),
    runner_factory: RunnerFactory | None = None,
    clock: Callable[[], datetime] = _utcnow,
    control_interval: float = control.CONTROL_POLL_SECONDS,
) -> dict[str, Any]:
    """Drive one subtask card through `workflow.task.TASK` once, and report.

    Three stages (card 5daa944e), composed here: `preflight_card` (the board
    reads and every refusal, then the run id and the `started` records, with
    no side effect), `recorded_card_run` (the store opened, the lease and
    the `card:<id>` claim taken, the `started` rows written) and
    `run_card_engine` (the walk, the outcome rows and the card comment),
    which runs under one `asyncio.run`.

    The order is the spec's and it is load-bearing: the board reads happen before
    a run id exists (so a bad card leaves no run directory), and the run, story
    and subtask rows are written before the walk starts (so `status` and `resume`
    can see a run that died on its first phase).

    Live control (C11) and claims (X5): the card is refused before
    `Store.open` if another live run claims it, and from before the
    `started` rows through the final ones the run holds a `control.Lease`
    with the `card:<id>` claim (`run_lease`), and the walk runs under `control.controlled`,
    which polls for `am pause`/`am cancel` every `control_interval` seconds
    and turns one into `stop.request`. A pause parks the walk before its next
    phase (`stopped`, resumable); a cancel parks it the same way and records
    the run `cancelled` (`card_run_status`). No control cancels a running phase.
    The lease and claim are released before `store.close()` on every exit.

    Board comments (card 5d9a875f): once the rows are recorded, still under
    the lease, the card gets at most one comment (`card_outcome_comment`);
    a flush's warnings join the payload's `warnings` and nothing else changes.
    """
    pre = preflight_card(
        card_id,
        repo_dir=repo_dir,
        branch_prefix=branch_prefix,
        base_branch=base_branch,
        clock=clock,
    )
    with recorded_card_run(pre) as recorded:
        return asyncio.run(
            run_card_engine(
                pre,
                recorded,
                commands=commands,
                allow_no_verification=allow_no_verification,
                runner_factory=runner_factory,
                control_interval=control_interval,
            )
        )
```

- [ ] **Step 5: Run the new card tests**

Run: `uv run pytest tests/test_cli.py -k "preflight_card or recorded_card_run or claim_taken_after_card_preflight or hands_the_engine_the_lease or crashing_card_engine" -v`
Expected: 8 PASSED.

- [ ] **Step 6: Run the whole cli module, default tiers**

Run: `uv run pytest tests/test_cli.py`
Expected: all PASSED (or skipped where a tier binary is missing), no failures.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "refactor(cli): run_card composes preflight, recorded stage and engine"
```

---

### Task 4: `preflight_milestone`

**Files:**
- Modify: `src/agent_manager/orchestrate.py` (insert after `milestone_card_ids`, which ends with `    return list(dict.fromkeys(ids))`, above `def run_milestone(`)
- Modify: `tests/test_orchestrate.py` (imports at top; append at end)

**Interfaces:**
- Consumes: `runs.resolve_repo_dir`, `resumable_milestone_run`, `board.roots`, `census.find_milestone`, `find_run_milestone`, `board.tree`, `census.flatten_milestone`, `plan_levels`, `story_tips`, `milestone_claims`, `cli.refuse_claimed`, `refresh_git`, `runs.mint_run_id`, `MILESTONE_WORKFLOW`, `Driver`, `PlannedStory`, `_utcnow` (all in `orchestrate.py`).
- Produces: `orchestrate.MilestonePreflight` (frozen dataclass: `root: Path`, `resumed: models.Run | None`, `milestone_card: models.CardNode`, `plan: census.Census`, `levels: list[list[PlannedStory]]`, `tips: list[dict[str, str]]`, `keys: list[str]`, `base_branch: str`, `branch_prefix: str`, `max_concurrent: int`, `run_id: str`, `run_record: models.Run`, `drive: Driver`) and `orchestrate.preflight_milestone(milestone: str | None, *, repo_dir: Path, base_branch: str | None = None, branch_prefix: str | None = None, max_concurrent: int = 1, clock: Callable[[], datetime] = _utcnow, resume_run_id: str | None = None, driver: Driver | None = None) -> MilestonePreflight`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_orchestrate.py`, add this line directly below the `from agent_manager import bases, board, census, cli, ...` import (line 45):

```python
from agent_manager import comments
```

Append to the end of `tests/test_orchestrate.py`:

```python
# ── run pre-flight, recorded stage and engine seam (card 5daa944e) ──────────
#
# Unit tier: the FakeBoard (`fake_board`) answers every board call, the repo
# dir is `_resume_root`'s plain directory, `orchestrate.refresh_git` is
# patched, and `integrate_recorder` (autouse) stands in for Integrate. No git,
# brd or claude process ever starts.


def _preflight_milestone(root: Path, milestone: str | None, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "repo_dir": root,
        "base_branch": "main",
        "branch_prefix": PREFIX,
        "max_concurrent": 1,
        "clock": lambda: STARTED_AT,
    }
    kwargs.update(overrides)
    return orchestrate.preflight_milestone(milestone, **kwargs)


def _no_refresh(root: Path) -> None:
    pytest.fail("refresh_git ran before a pre-flight refusal")


def test_preflight_milestone_refuses_a_blocker_cycle_before_refreshing_git(
    tmp_path, monkeypatch, fake_board
):
    """Through the board a sibling cycle is refused even earlier, by the
    census (`CensusOrderError`), so the census is patched to hand
    `plan_levels` the cyclic stories of test_plan_levels_refuses_a_blocker_cycle_before_any_geometry."""
    root = _resume_root(tmp_path, monkeypatch)
    milestone = _add_card(root, "Milestone 3: orchestration")
    a = _plan_story(1, [_plan_subtask(11)], blocked_by=[_plan_id(2)])
    b = _plan_story(2, [_plan_subtask(21)], blocked_by=[_plan_id(1)])
    monkeypatch.setattr(
        census,
        "flatten_milestone",
        lambda node: census.Census(milestone_title=node.title, stories=[a, b]),
    )
    monkeypatch.setattr(orchestrate, "refresh_git", _no_refresh)

    with pytest.raises(dag.DependencyCycleError):
        _preflight_milestone(root, milestone)

    assert _run_dirs() == []


def test_preflight_milestone_refuses_an_ambiguous_needle_before_refreshing_git(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    _add_card(root, "Milestone 3: orchestration")
    _add_card(root, "Milestone 3: integration")
    monkeypatch.setattr(orchestrate, "refresh_git", _no_refresh)

    with pytest.raises(census.MilestoneNotFoundError, match="ambiguous milestone"):
        _preflight_milestone(root, "Milestone 3")

    assert _run_dirs() == []


def test_preflight_milestone_refuses_a_claimed_key_before_refreshing_git(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    shape = _milestone(root, {"A": 1})
    key = f"card:{shape['milestone']}"
    _plant_lease(
        root,
        run_id=OTHER_RUN_ID,
        token="other-life",
        pid=os.getpid(),
        heartbeat_at=datetime.now(timezone.utc),
        claims=(key,),
    )
    monkeypatch.setattr(orchestrate, "refresh_git", _no_refresh)

    with pytest.raises(cli.ClaimedError) as caught:
        _preflight_milestone(root, shape["milestone"])

    assert (caught.value.key, caught.value.run_id) == (key, OTHER_RUN_ID)
    assert _run_dirs() == []
    assert _claim_rows(root) == [(key, OTHER_RUN_ID, "other-life")]


def test_a_fresh_milestone_preflight_refreshes_git_once_after_every_refusal(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    shape = _milestone(root, {"A": 2})
    events: list[str] = []
    real_refuse = cli.refuse_claimed

    def refuse(at: Path, keys: Any, *, run_id: str | None = None) -> None:
        events.append("refuse_claimed")
        real_refuse(at, keys, run_id=run_id)

    monkeypatch.setattr(cli, "refuse_claimed", refuse)
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: events.append(f"refresh_git:{at}"))
    driver = FakeDriver()

    pre = _preflight_milestone(root, shape["milestone"], driver=driver)

    assert events == ["refuse_claimed", f"refresh_git:{root}"]
    assert pre.root == root
    assert pre.resumed is None
    assert pre.milestone_card.id == shape["milestone"]
    assert pre.run_id == runs.mint_run_id(shape["milestone"], STARTED_AT)
    assert (pre.run_record.id, pre.run_record.status) == (pre.run_id, "started")
    assert pre.run_record.workflow == orchestrate.MILESTONE_WORKFLOW
    assert pre.run_record.milestone_id == shape["milestone"]
    assert pre.run_record.config.max_concurrent_stories == 1
    assert (pre.base_branch, pre.branch_prefix, pre.max_concurrent) == ("main", PREFIX, 1)
    assert pre.keys == orchestrate.milestone_claims(shape["milestone"], pre.plan.stories, PREFIX)
    assert pre.drive is driver
    assert driver.calls == []
    assert _run_dirs() == []
    assert _run_ids(root) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "preflight_milestone or fresh_milestone_preflight" -v`
Expected: 4 FAILED with `AttributeError: module 'agent_manager.orchestrate' has no attribute 'preflight_milestone'`.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/orchestrate.py`, insert directly after `milestone_card_ids` (after its `    return list(dict.fromkeys(ids))`) and above `def run_milestone(`:

```python
# ── the three stages of a milestone run (card 5daa944e) ─────────────────────


@dataclass(frozen=True)
class MilestonePreflight:
    """What `preflight_milestone` read and decided for one milestone run.

    Everything the recorded stage and the engine read afterwards. On a resume,
    `base_branch`, `branch_prefix` and `max_concurrent` are the recorded
    run's, and `resumed` is that run as it was left. `drive` is the chosen
    driver. Internal state, so a dataclass.
    """

    root: Path
    resumed: models.Run | None
    milestone_card: models.CardNode
    plan: census.Census
    levels: list[list[PlannedStory]]
    tips: list[dict[str, str]]
    keys: list[str]
    base_branch: str
    branch_prefix: str
    max_concurrent: int
    run_id: str
    run_record: models.Run
    drive: Driver


def preflight_milestone(
    milestone: str | None,
    *,
    repo_dir: Path,
    base_branch: str | None = None,
    branch_prefix: str | None = None,
    max_concurrent: int = 1,
    clock: Callable[[], datetime] = _utcnow,
    resume_run_id: str | None = None,
    driver: Driver | None = None,
) -> MilestonePreflight:
    """Stage 1 of a milestone run: every read and refusal, then the run record (card 5daa944e).

    In today's order: the resumable run (resume only), the board roots, the
    milestone (`census.find_milestone`'s unknown/ambiguous refusal, or
    `find_run_milestone`), its census, `plan_levels` (the cycle refusal), the
    tips, the claims, and `cli.refuse_claimed` as the last refusal --
    read-only, before `refresh_git` and before any store, so a key another
    live run holds leaves no fetch, prune, run row or run directory. A fresh
    run then refreshes git (its first side effect, still before the store),
    reads the clock and mints the run id; a resume keeps its own id and
    refreshes git later, under the lease. The store is never opened here.
    """
    root = runs.resolve_repo_dir(repo_dir)
    resumed = None if resume_run_id is None else resumable_milestone_run(root, resume_run_id)
    if resumed is not None:
        base_branch = resumed.base_branch
        branch_prefix = resumed.branch_prefix
        max_concurrent = resumed.config.max_concurrent_stories
    roots = board.roots(repo_dir=root)
    if resumed is None:
        milestone_card = census.find_milestone(roots, milestone)
    else:
        milestone_card = find_run_milestone(roots, resumed)
    plan = census.flatten_milestone(board.tree(milestone_card.id, repo_dir=root))
    levels = plan_levels(plan.stories, branch_prefix=branch_prefix, base_branch=base_branch)
    tips = story_tips(plan.stories, branch_prefix=branch_prefix, base_branch=base_branch)
    drive = cli.drive_subtask_async if driver is None else driver
    keys = milestone_claims(milestone_card.id, plan.stories, branch_prefix)
    # The last refusal (X5, X6): read-only, before `refresh_git` and before
    # `Store.open`, so a milestone, remaining subtask or integration branch
    # another live run claims leaves no fetch, prune, run row or run
    # directory. A resume's own rows are not a conflict; `take_lease` in the
    # recorded stage re-checks atomically.
    cli.refuse_claimed(root, keys, run_id=None if resumed is None else resumed.id)

    if resumed is None:
        # The first side effect. It runs after every refusal and before the store
        # is opened, so a failed fetch leaves no run directory behind.
        refresh_git(root)
        started_at = clock()
        run_id = runs.mint_run_id(milestone_card.id, started_at)
        run_record = models.Run(
            id=run_id,
            workflow=MILESTONE_WORKFLOW,
            repo_dir=root,
            base_branch=base_branch,
            branch_prefix=branch_prefix,
            status="started",
            started_at=started_at,
            config=models.RunConfig(max_concurrent_stories=max_concurrent),
            milestone_id=milestone_card.id,
        )
    else:
        run_id = resumed.id
        # Stamps a run recorded before `milestone_id` existed, so the next
        # resume no longer needs the short-id fallback.
        run_record = resumed.model_copy(
            update={"status": "started", "milestone_id": milestone_card.id}
        )
    return MilestonePreflight(
        root=root,
        resumed=resumed,
        milestone_card=milestone_card,
        plan=plan,
        levels=levels,
        tips=tips,
        keys=keys,
        base_branch=base_branch,
        branch_prefix=branch_prefix,
        max_concurrent=max_concurrent,
        run_id=run_id,
        run_record=run_record,
        drive=drive,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -k "preflight_milestone or fresh_milestone_preflight" -v`
Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "refactor(orchestrate): add preflight_milestone, the refusal stage of a milestone run"
```

---

### Task 5: `recorded_milestone_run`

**Files:**
- Modify: `src/agent_manager/orchestrate.py` (imports at top; insert after `preflight_milestone`, above `def run_milestone(`)
- Test: `tests/test_orchestrate.py` (append at end)

**Interfaces:**
- Consumes: `MilestonePreflight`, `preflight_milestone` (Task 4); `Store`, `cli.run_lease`, `open_cards`, `resume_checkpoints`, `refresh_git`, `record_plan`, `reopen_rows`, `Checkpoint`, `Workflow`.
- Produces: `orchestrate.RecordedMilestoneRun` (frozen dataclass: `run_id: str`, `store: Store`, `lease: control.Lease`, `rows: dict[str, tuple[models.StoryRun, dict[str, models.SubtaskRun]]]`, `checkpoints: dict[str, Checkpoint] | None`) and `orchestrate.recorded_milestone_run(pre: MilestonePreflight) -> Iterator[RecordedMilestoneRun]`, decorated with `@contextmanager`.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_orchestrate.py`:

```python
def _close_snapshots(monkeypatch) -> list[tuple[int, int]]:
    """Patch `Store.close` to record `(claims, leases)` its run still holds as it closes.

    `(0, 0)` means the claims and the lease were released before the store
    closed. Counted over the closing store's own connection, before the real
    close runs.
    """
    seen: list[tuple[int, int]] = []
    real_close = store_module.Store.close

    def close(self) -> None:
        conn = self.connection
        claims = conn.execute(
            "SELECT COUNT(*) FROM run_claims WHERE run_id = ?", (self.run_id,)
        ).fetchone()[0]
        leases = conn.execute(
            "SELECT COUNT(*) FROM run_leases WHERE run_id = ?", (self.run_id,)
        ).fetchone()[0]
        seen.append((claims, leases))
        real_close(self)

    monkeypatch.setattr(store_module.Store, "close", close)
    return seen


def _seam_resume_board(fake_board) -> tuple[str, str, str]:
    """A milestone whose short id is RESUME_RUN_ID's (`00000009`), one story, one subtask."""
    milestone = fake_board.add_card("Milestone 9: resume", card_id=_plan_id(9))
    story = fake_board.add_card("Story R", parent_id=milestone)
    subtask = fake_board.add_card("r1: the one subtask", parent_id=story)
    return milestone, story, subtask


def test_inside_recorded_milestone_run_the_plan_is_recorded_and_leased_but_nothing_driven(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    shape = _milestone(root, {"A": 2})
    a1, a2 = shape["subtasks"]["A"]
    story = shape["stories"]["A"]
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)
    monkeypatch.setattr(
        comments,
        "flush",
        lambda *args, **kwargs: pytest.fail("comments.flush ran in the recorded stage"),
    )
    driver = FakeDriver()
    pre = _preflight_milestone(root, shape["milestone"], driver=driver)

    with orchestrate.recorded_milestone_run(pre) as recorded:
        assert recorded.run_id == pre.run_id
        run = recorded.store.load_run(recorded.run_id)
        assert run is not None
        assert _statuses(run) == {"run": "started", story: "pending", a1: "pending", a2: "pending"}
        assert run.milestone_id == shape["milestone"]
        assert _held_keys(root, recorded.run_id) == _expected_claims(shape["milestone"], [a1, a2])
        assert list(recorded.rows) == [story]
        assert recorded.checkpoints is None
        assert driver.calls == []


def test_leaving_recorded_milestone_run_on_an_error_releases_claims_and_lease_before_closing(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    shape = _milestone(root, {"A": 1})
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)
    closes = _close_snapshots(monkeypatch)
    pre = _preflight_milestone(root, shape["milestone"], driver=FakeDriver())

    with pytest.raises(RuntimeError, match="engine never started"):
        with orchestrate.recorded_milestone_run(pre):
            raise RuntimeError("engine never started")

    assert closes == [(0, 0)]
    assert _claim_rows(root) == []
    assert _held_keys(root, pre.run_id) == []
    assert _load(root, pre.run_id).status == "started"


def test_a_resumed_milestone_preflight_leaves_refresh_git_to_the_recorded_stage(
    tmp_path, monkeypatch, fake_board
):
    """A resume refreshes git under its own lease (X5), not in pre-flight,
    and takes its prefix, base and bound from the recorded run."""
    root = _resume_root(tmp_path, monkeypatch)
    milestone, _story, subtask = _seam_resume_board(fake_board)
    _record_resume_run(root)
    refreshed: list[list[str]] = []
    monkeypatch.setattr(
        orchestrate, "refresh_git", lambda at: refreshed.append(_held_keys(root, RESUME_RUN_ID))
    )

    pre = orchestrate.preflight_milestone(None, repo_dir=root, resume_run_id=RESUME_RUN_ID)

    assert refreshed == []
    assert (pre.run_id, pre.base_branch, pre.branch_prefix, pre.max_concurrent) == (
        RESUME_RUN_ID,
        "main",
        PREFIX,
        3,
    )
    assert pre.resumed is not None
    assert (pre.run_record.status, pre.run_record.milestone_id) == ("started", milestone)
    with orchestrate.recorded_milestone_run(pre) as recorded:
        assert recorded.checkpoints == {}
        assert refreshed == [_expected_claims(milestone, [subtask])]
    assert len(refreshed) == 1


def test_a_resume_checkpoint_under_another_digest_is_refused_before_the_lease(
    tmp_path, monkeypatch, fake_board
):
    root = _resume_root(tmp_path, monkeypatch)
    _milestone_id, _story, subtask = _seam_resume_board(fake_board)
    _record_resume_run(root)
    opened = store_module.Store.open(root, RESUME_RUN_ID)
    try:
        _save(opened, subtask, "parked", phase="plan", digest="saved-under-another-task")
    finally:
        opened.close()
    monkeypatch.setattr(
        orchestrate,
        "refresh_git",
        lambda at: pytest.fail("refresh_git ran before the checkpoint refusal"),
    )
    pre = orchestrate.preflight_milestone(None, repo_dir=root, resume_run_id=RESUME_RUN_ID)

    with pytest.raises(runs.CheckpointMismatchError):
        with orchestrate.recorded_milestone_run(pre):
            pytest.fail("the recorded stage yielded past a stale checkpoint")

    assert _claim_rows(root) == []
    assert _held_keys(root, RESUME_RUN_ID) == []
    assert _load(root, RESUME_RUN_ID).status == "escalated"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "recorded_milestone_run or resumed_milestone_preflight or resume_checkpoint_under_another_digest" -v`
Expected: 4 FAILED with `AttributeError: module 'agent_manager.orchestrate' has no attribute 'recorded_milestone_run'` (the resume pre-flight assertions run first and pass; the failure is at the `recorded_milestone_run` call).

- [ ] **Step 3: Add the imports**

In `src/agent_manager/orchestrate.py`, change

```python
from collections.abc import Awaitable, Callable, Mapping, Sequence
```

to

```python
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
```

- [ ] **Step 4: Write the minimal implementation**

In `src/agent_manager/orchestrate.py`, insert directly after `preflight_milestone` (after its closing `    )`) and above `def run_milestone(`:

```python
@dataclass(frozen=True)
class RecordedMilestoneRun:
    """A milestone run past its recorded stage (card 5daa944e).

    Its id, its open store, the lease it holds, the plan rows `record_plan`
    wrote, and on a resume each open card's checkpoint (`None` on a fresh
    run). Internal state, so a dataclass.
    """

    run_id: str
    store: Store
    lease: control.Lease
    rows: dict[str, tuple[models.StoryRun, dict[str, models.SubtaskRun]]]
    checkpoints: dict[str, Checkpoint] | None


@contextmanager
def recorded_milestone_run(pre: MilestonePreflight) -> Iterator[RecordedMilestoneRun]:
    """Stage 2 of a milestone run: open the store, take the lease, record the plan (card 5daa944e).

    On a resume the checkpoints are read first: the store's own refusal, a
    checkpoint saved under another workflow, is read-only and comes before
    the lease, before git and before any write. Then `cli.run_lease` takes
    the lease with `pre.keys`, inside the `try` that closes the store, so the
    claims and the lease are released before `store.close()` on every exit
    (live control C2, X5), an exception in the block included. It is taken
    before `record_run`, so every run write is fenced by this token; a lost
    race is `ClaimedError` or `RunIsLiveError` with nothing recorded. A
    resume refreshes git first under the lease. Then the run is recorded
    `started` and the whole plan `pending`; a resume then reopens its rows.
    """
    store = Store.open(pre.root, pre.run_id)
    try:
        checkpoints: dict[str, Checkpoint] | None = None
        cards: list[tuple[str, Workflow]] = []
        if pre.resumed is not None:
            cards = open_cards(
                pre.plan.stories, branch_prefix=pre.branch_prefix, base_branch=pre.base_branch
            )
            checkpoints = resume_checkpoints(store, cards)
        with cli.run_lease(store, claims=pre.keys) as lease:
            if pre.resumed is not None:
                # A resume's first side effect, under this life's lease (X5):
                # a run still live elsewhere was refused on entry, before git.
                refresh_git(pre.root)
            store.record_run(pre.run_record)
            rows = record_plan(store, pre.levels, root=pre.root, branch_prefix=pre.branch_prefix)
            if pre.resumed is not None:
                # After `record_plan`, which records every planned row `pending`.
                reopen_rows(store, pre.resumed, {card_id for card_id, _workflow in cards})
            yield RecordedMilestoneRun(
                run_id=pre.run_id,
                store=store,
                lease=lease,
                rows=rows,
                checkpoints=checkpoints,
            )
    finally:
        store.close()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_orchestrate.py -k "preflight_milestone or fresh_milestone_preflight or recorded_milestone_run or resumed_milestone_preflight or resume_checkpoint_under_another_digest" -v`
Expected: 8 PASSED.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "refactor(orchestrate): add recorded_milestone_run, the store/lease/plan stage"
```

---

### Task 6: `run_milestone_engine` and `_run_milestone_async` as the composition

**Files:**
- Modify: `src/agent_manager/orchestrate.py` (insert `run_milestone_engine` after `recorded_milestone_run`; edit the `run_milestone` docstring; replace `_run_milestone_async` from `async def _run_milestone_async(` down to its `    finally:\n        store.close()`, just above `# ── the board run (card baef4f94)`)
- Test: `tests/test_orchestrate.py` (append at end)

**Interfaces:**
- Consumes: `MilestonePreflight`, `preflight_milestone` (Task 4); `RecordedMilestoneRun`, `recorded_milestone_run` (Task 5); `comments.flush`, `milestone_card_ids`, `reroll_stale_stories`, `StopSignal`, `control.controlled`, `supervise`, `supervisor_plan`, `bases_payload`, `with_bases`, `controlled_payload`, `escalated_payload`, `integrate_escalated_payload`, `integrated_payload`, `post_comment`, `_in_thread_to_completion`, `integration.integrate_milestone`, `cli.default_runner_factory`.
- Produces: `async def orchestrate.run_milestone_engine(pre: MilestonePreflight, recorded: RecordedMilestoneRun, *, commands: Sequence[str] = (), allow_no_verification: bool = False, runner_factory: runs.RunnerFactory | None = None, control_interval: float = control.CONTROL_POLL_SECONDS, slots: asyncio.Semaphore | None = None) -> dict[str, Any]`. `_run_milestone_async` and `run_milestone` keep their exact signatures.

- [ ] **Step 1: Write the tests**

Append to the end of `tests/test_orchestrate.py`:

```python
def test_the_milestone_engine_drives_a_recorded_run_under_its_lease(
    tmp_path, monkeypatch, fake_board, integrate_recorder
):
    root = _resume_root(tmp_path, monkeypatch)
    shape = _milestone(root, {"A": 1})
    story = shape["stories"]["A"]
    (a1,) = shape["subtasks"]["A"]
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)
    handed: dict[str, Any] = {}

    real_supervise = orchestrate.supervise

    async def spying_supervise(plan, **kwargs):
        handed["lease_token"] = kwargs["lease_token"]
        return await real_supervise(plan, **kwargs)

    real_controlled = control.controlled

    async def spying_controlled(work, **kwargs):
        handed["lease"] = kwargs["lease"]
        return await real_controlled(work, **kwargs)

    monkeypatch.setattr(orchestrate, "supervise", spying_supervise)
    monkeypatch.setattr(control, "controlled", spying_controlled)
    driver = FakeDriver()
    pre = _preflight_milestone(root, shape["milestone"], driver=driver)

    with orchestrate.recorded_milestone_run(pre) as recorded:
        result = asyncio.run(orchestrate.run_milestone_engine(pre, recorded))
        lease = recorded.lease

    assert handed["lease"] is lease
    assert handed["lease_token"] == lease.token
    assert [call["card"] for call in driver.calls] == [a1]
    assert [call["run_id"] for call in integrate_recorder.calls] == [pre.run_id]
    assert result == {
        "done": True,
        "run_id": pre.run_id,
        "levels": [{"level": 0, "stories": [story]}],
        "completed": [a1],
        "tips": [{"story": story, "tip": _branch(root, a1)}],
        "warnings": [],
        "integrated": _integrated(root, [story]),
    }
    assert _load(root, pre.run_id).status == "done"
    assert _claim_rows(root) == []


def test_a_crashing_milestone_engine_still_releases_its_lease_before_closing(
    tmp_path, monkeypatch, fake_board, integrate_recorder
):
    """Spec: a crash in the engine still propagates, still releases the lease
    and claims, and still closes the store. A characterization pin: it passes
    before the split and must keep passing after it."""
    root = _resume_root(tmp_path, monkeypatch)
    shape = _milestone(root, {"A": 1})
    monkeypatch.setattr(orchestrate, "refresh_git", lambda at: None)
    integrate_recorder.outcome = RuntimeError("integrate bug")
    closes = _close_snapshots(monkeypatch)

    with pytest.raises(RuntimeError, match="integrate bug"):
        _run(root, shape["milestone"], FakeDriver())

    assert closes == [(0, 0)]
    assert _claim_rows(root) == []
```

- [ ] **Step 2: Run the tests to verify the first fails and the pin passes**

Run: `uv run pytest tests/test_orchestrate.py -k "milestone_engine_drives_a_recorded_run or crashing_milestone_engine" -v`
Expected: `test_the_milestone_engine_drives_a_recorded_run_under_its_lease` FAILED with `AttributeError: module 'agent_manager.orchestrate' has no attribute 'run_milestone_engine'`; `test_a_crashing_milestone_engine_still_releases_its_lease_before_closing` PASSED (characterization pin).

- [ ] **Step 3: Add `run_milestone_engine`**

In `src/agent_manager/orchestrate.py`, insert directly after `recorded_milestone_run` (after its `        store.close()`) and above `def run_milestone(`:

```python
async def run_milestone_engine(
    pre: MilestonePreflight,
    recorded: RecordedMilestoneRun,
    *,
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    runner_factory: runs.RunnerFactory | None = None,
    control_interval: float = control.CONTROL_POLL_SECONDS,
    slots: asyncio.Semaphore | None = None,
) -> dict[str, Any]:
    """Stage 3 of a milestone run: drive a recorded, leased run to its report (card 5daa944e).

    From the first engine-side action to the last: the start flush of the
    milestone's pending comments and the stale-story re-roll, then
    `control.controlled(supervise(...))` under the recorded stage's lease
    (`lease_token=lease.token`), then the outcome precedence, Integrate, the
    final run record, the run-end comment and the report. The driver and the
    lane bound are `pre.drive` and `pre.max_concurrent` (a resume's bound is
    the recorded run's). The caller owns the store and the lease; a crash
    propagates.
    """
    store, lease, run_id = recorded.store, recorded.lease, recorded.run_id
    rows, checkpoints = recorded.rows, recorded.checkpoints
    root, plan, levels, tips = pre.root, pre.plan, pre.levels, pre.tips
    milestone_card, run_record, resumed = pre.milestone_card, pre.run_record, pre.resumed
    base_branch, branch_prefix = pre.base_branch, pre.branch_prefix

    # Board-comments B7: any run's leftover comments on this milestone's
    # cards go out under this lease, before anything is driven; a board
    # failure is a warning and the run goes on (B8).
    warnings = comments.flush(
        store, root, card_ids=milestone_card_ids(milestone_card.id, plan.stories)
    )
    warnings.extend(reroll_stale_stories(plan.stories, root))
    completed: list[str] = []
    stop = StopSignal()

    # `controlled` only ever parks the run through `stop` (C3); it
    # closes the window and runs a final sweep before returning.
    outcomes = await control.controlled(
        supervise(
            supervisor_plan(
                plan.stories,
                levels,
                rows,
                branch_prefix=branch_prefix,
                base_branch=base_branch,
                checkpoints=checkpoints,
            ),
            store=store,
            run_id=run_id,
            lease_token=lease.token,
            root=root,
            drive=pre.drive,
            commands=list(commands),
            allow_no_verification=allow_no_verification,
            runner_factory=runner_factory,
            max_concurrent=pre.max_concurrent,
            stop=stop,
            slots=slots,
        ),
        store=store,
        stop=stop,
        lease=lease,
        interval=control_interval,
    )
    # Wave order, census order within a wave, never finish order.
    for outcome in outcomes:
        completed.extend(outcome.completed)
        warnings.extend(outcome.warnings)
    built_bases = bases_payload(outcomes)
    total = sum(len(story.subtasks) for story in plan.stories)

    def report(payload: dict[str, Any]) -> dict[str, Any]:
        """Every payload shape on the same terms: `bases` when built, and on
        a resume `resumed` plus `took_over` when a dead holder's lease
        was taken over (X5), as `cli._resume_from_checkpoint` reports it."""
        if resumed is not None:
            payload["resumed"] = True
            if lease.displaced is not None:
                payload["took_over"] = {
                    "pid": lease.displaced.pid,
                    "host": lease.displaced.host,
                    "heartbeat_at": lease.displaced.heartbeat_at.isoformat(),
                }
        return with_bases(payload, built_bases)

    def comment_run_end(payload: dict[str, Any]) -> dict[str, Any]:
        """Comment `payload`'s outcome on the milestone card (board-comments B2).

        Called after the run's final record, on every exit that records
        one. `total` goes only into the dict `compose_run_end` reads, so
        the report keeps its shape; the flush's warnings join the
        report's own `warnings`.
        """
        comment = comments.compose_run_end(
            run_id=run_id,
            milestone_id=milestone_card.id,
            token=lease.token,
            payload={**payload, "total": total},
        )
        payload["warnings"].extend(post_comment(store, root, comment, run_id=run_id))
        return payload

    # Outcome precedence (live control C6): the first match wins. A
    # control is never an escalation, and a paused or cancelled run
    # never reaches Integrate in this invocation.
    if stop.requested == "cancel":
        store.record_run(run_record.model_copy(update={"status": "cancelled"}))
        payload = report(controlled_payload(run_id, "cancel", outcomes, warnings))
        # Board-comments B2 (card 5d9a875f): after the cancel is recorded,
        # each subtask it parked, in wave order, then the milestone. A lane
        # stopped while its base built names no subtask and gets nothing;
        # an escalated lane already commented its own escalation.
        for outcome in outcomes:
            if outcome.kind != "stopped" or outcome.subtask is None:
                continue
            assert outcome.story is not None
            comment = comments.compose_cancelled(
                run_id=run_id,
                card_id=outcome.subtask,
                before_phase=outcome.before_phase,
                branch=rows[outcome.story][1][outcome.subtask].branch,
                relaunch=f"am run --milestone {milestone_card.id}",
            )
            payload["warnings"].extend(post_comment(store, root, comment, run_id=run_id))
        return comment_run_end(payload)
    if any(outcome.kind == "escalated" for outcome in outcomes):
        store.record_run(run_record.model_copy(update={"status": "escalated"}))
        primary = next((outcome.story for outcome in outcomes if outcome.primary), None)
        payload = escalated_payload(run_id, primary, outcomes, warnings)
        if stop.requested == "pause":
            payload["control"] = "pause"
        return comment_run_end(report(payload))
    if stop.requested == "pause":
        store.record_run(run_record.model_copy(update={"status": "stopped"}))
        # Board-comments B2 (card 5d9a875f): only the milestone's run-end;
        # a parked subtask is resumed, not closed, so it gets no comment.
        return comment_run_end(report(controlled_payload(run_id, "pause", outcomes, warnings)))

    # Integrate (addendum I6) runs only once every lane finished clean,
    # and also when there was nothing left to drive: that is how a relaunch
    # retries an Integrate escalation, and why a finished milestone's
    # relaunch is a no-op merge. Read as `integration.integrate_milestone`
    # so a test can replace it, as `driver` is. It needs a factory for a
    # conflicting tip; `None` is production's, read off `cli` now.
    factory = cli.default_runner_factory if runner_factory is None else runner_factory
    # `integrate_milestone` stays a synchronous call (I6); it is run on a
    # worker thread, not the loop thread, only because its conflict
    # resolver (`runtime_engine.run_subtask`) makes its own nested
    # `asyncio.run(...)` call, which `asyncio.run` refuses once this
    # coroutine is already running on the loop thread. `Store`'s
    # connection is `check_same_thread=False` for exactly this kind of
    # cross-thread, strictly sequential use (store.py).
    # A cancel waits for that thread, so the store and lease
    # outlive it (`_in_thread_to_completion`).
    outcome = await _in_thread_to_completion(
        integration.integrate_milestone,
        stories=plan.stories,
        repo_dir=root,
        base_branch=base_branch,
        branch_prefix=branch_prefix,
        commands=list(commands),
        allow_no_verification=allow_no_verification,
        store=store,
        run_id=run_id,
        runner_factory=factory,
    )
    if isinstance(outcome, integration.IntegrateEscalation):
        # The branch and worktree stay exactly as Integrate left them (I5).
        store.record_run(run_record.model_copy(update={"status": "escalated"}))
        return comment_run_end(report(integrate_escalated_payload(run_id, outcome, warnings)))

    store.record_run(run_record.model_copy(update={"status": "done"}))
    return comment_run_end(
        report(
            {
                "done": True,
                "run_id": run_id,
                "levels": [
                    {"level": index, "stories": [planned.story.id for planned in level]}
                    for index, level in enumerate(levels)
                ],
                "completed": completed,
                "tips": tips,
                "warnings": warnings,
                "integrated": integrated_payload(outcome),
            }
        )
    )
```

- [ ] **Step 4: Rewrite `_run_milestone_async` as the composition**

Replace the whole of `_run_milestone_async` in `src/agent_manager/orchestrate.py`, from `async def _run_milestone_async(` down to and including its final lines

```python
    finally:
        store.close()
```

(the ones directly above `# ── the board run (card baef4f94) ───`), with:

```python
async def _run_milestone_async(
    milestone: str | None,
    *,
    repo_dir: Path,
    base_branch: str | None = None,
    branch_prefix: str | None = None,
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    runner_factory: runs.RunnerFactory | None = None,
    driver: Driver | None = None,
    clock: Callable[[], datetime] = _utcnow,
    max_concurrent: int = 1,
    resume_run_id: str | None = None,
    control_interval: float = control.CONTROL_POLL_SECONDS,
    slots: asyncio.Semaphore | None = None,
) -> dict[str, Any]:
    """`run_milestone`'s body without its argument validation, awaitable in a
    caller's own event loop.

    It composes the run's three stages (card 5daa944e): `preflight_milestone`
    (every read and refusal, `cli.refuse_claimed` last, then a fresh run's
    `refresh_git` and run record; no store), `recorded_milestone_run` (the
    store opened, the lease and claims taken before `record_run`, the plan
    recorded `pending`; the lease and claims released before `store.close()`
    on every exit) and `run_milestone_engine` (the start flush,
    `control.controlled(supervise(...))`, Integrate and the report).

    A caller that skips `run_milestone` must validate its own arguments first:
    a fresh run needs `max_concurrent >= 1` and a `milestone`, `base_branch`
    and `branch_prefix`. `slots`, when given, is forwarded to `supervise` and
    bounds this run's lanes instead of `max_concurrent`, so several runs can
    share one semaphore; `None` lets `supervise` make its own
    `asyncio.Semaphore(max_concurrent)`, as `run_milestone` does. The run is
    still recorded with `max_concurrent`. Board reads and `refresh_git` stay
    synchronous on the loop thread; Integrate stays a synchronous call but runs
    on a worker thread (see its call site), and a cancel arriving meanwhile
    waits for it to return before this coroutine unwinds.
    """
    pre = preflight_milestone(
        milestone,
        repo_dir=repo_dir,
        base_branch=base_branch,
        branch_prefix=branch_prefix,
        max_concurrent=max_concurrent,
        clock=clock,
        resume_run_id=resume_run_id,
        driver=driver,
    )
    with recorded_milestone_run(pre) as recorded:
        return await run_milestone_engine(
            pre,
            recorded,
            commands=commands,
            allow_no_verification=allow_no_verification,
            runner_factory=runner_factory,
            control_interval=control_interval,
            slots=slots,
        )
```

- [ ] **Step 5: Update the `run_milestone` docstring**

In `src/agent_manager/orchestrate.py`, inside `run_milestone`'s docstring, replace exactly

```
    `milestone` is a card id or a title needle (O1). Everything that can refuse,
    `max_concurrent < 1` and another live run's claim included, runs before
    the store is opened. Then the run takes a `control.Lease` with its
    `milestone_claims` (`cli.run_lease`), one `milestone` run is recorded with its
    whole plan `pending`, and `asyncio.run(control.controlled(supervise(...)))`
    runs every story the moment its blockers succeeded, at most
    `max_concurrent` at once. A subtask already `done` on
```

with

```
    `milestone` is a card id or a title needle (O1). This wrapper validates
    its arguments (`max_concurrent < 1` included) and then runs the three
    stages (card 5daa944e) under one `asyncio.run(_run_milestone_async(...))`:
    `preflight_milestone` runs everything that can refuse, another live
    run's claim included, before the store is opened; `recorded_milestone_run`
    takes a `control.Lease` with the run's `milestone_claims`
    (`cli.run_lease`) and records one `milestone` run with its whole plan
    `pending`; and `run_milestone_engine` runs `control.controlled(supervise(...))`,
    which starts every story the moment its blockers succeeded, at most
    `max_concurrent` at once. A subtask already `done` on
```

- [ ] **Step 6: Run the new milestone tests**

Run: `uv run pytest tests/test_orchestrate.py -k "preflight_milestone or fresh_milestone_preflight or recorded_milestone_run or resumed_milestone_preflight or resume_checkpoint_under_another_digest or milestone_engine_drives_a_recorded_run or crashing_milestone_engine" -v`
Expected: 10 PASSED.

- [ ] **Step 7: Run the whole orchestrate module, default tiers**

Run: `uv run pytest tests/test_orchestrate.py`
Expected: all PASSED (or skipped where a tier binary is missing), no failures. `test_the_async_core_takes_run_milestones_parameters_plus_slots` in particular still passes.

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "refactor(orchestrate): _run_milestone_async composes preflight, recorded stage and engine"
```

---

### Task 7: Full verification

**Files:** none changed.

- [ ] **Step 1: Confirm no existing test was edited**

Run: `git diff ami/task-2-3-am-runs-progress-882b212b -- tests/ | grep '^-[^-]'`
Expected: no output (only added lines in the test files; no removed or changed line).

- [ ] **Step 2: Run the default suite**

Run: `uv run pytest`
Expected: exit 0, all tests passed. No test of the new seam is reported over the unit 0.5s budget.

- [ ] **Step 3: Optional extra confidence on the opt-in tiers that cover run_card / run_milestone**

Many existing `run_card`/`run_milestone` behaviour tests are marked `brd` and are deselected by default. If the `brd` binary is installed, run them for these two modules:

Run: `uv run pytest -m "not e2e_fake and not soak and not e2e" tests/test_cli.py tests/test_orchestrate.py`
Expected: exit 0. Without `brd` on `PATH` these items are skipped, not failed.

- [ ] **Step 4: Commit (only if anything was fixed during verification)**

```bash
git status --short
```

Expected: clean working tree. If a fix was needed, commit it with a message naming the failing test it fixed.
