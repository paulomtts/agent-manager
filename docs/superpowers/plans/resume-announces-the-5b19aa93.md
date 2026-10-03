# Resume Announces the Kept Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `am resume` on a `task` run adds one warning, `verification: kept from checkpoint: [...]`, when the passed `--verify` differs from the suite the checkpoint's walk keeps.

**Architecture:** A read-only `runtime.engine.kept_commands(checkpoint)` reads the `"subtask"` seed item's `commands` out of the stored `Agent.to_dict()` (beside `pending_phase`, importing nothing from pygents). `cli._resume_from_checkpoint` calls it once the checkpoint is accepted, compares it with the passed `commands`, and puts at most one warning between the flushed board-comment warnings and the walk's warnings. The help text, the `resume_run` docstring and README.md:39 say so.

**Tech Stack:** Python 3, Typer, pytest, `uv`.

**Spec:** `docs/superpowers/specs/resume-announces-the-5b19aa93.md` (prepended in full below this plan's header, under "Spec").

## Global Constraints

- No new top-level resume payload key: `set(payload) == RESUME_KEYS` (`tests/test_cli.py:5119-5134`) must hold.
- Warning text, exactly: `f"verification: kept from checkpoint: {kept!r}"` — names the *kept* suite, never the passed one.
- Warning order: `"warnings": [*flushed, *kept_warning, *drive.warnings]`; flushed board-comment warnings lead (B7).
- At most one kept-suite warning per resume.
- Comparison is exact, order-sensitive list equality: `list(commands) != kept`; no whitespace normalising.
- Empty passed `commands` (flag omitted) → no warning. `kept is None` (unknown) → no warning. `kept == []` with non-empty passed `commands` → warning.
- `kept_commands` never raises on a checkpoint missing `context_pool`, `items`, the `"subtask"` item, `"commands"`, or with non-list `commands`; it writes nothing and builds no agent.
- `runtime/engine.py` may import pygents, but `kept_commands` itself must only read the stored dict (follow `pending_phase`); `runtime/walk.py` is untouched.
- The resumed walk still runs with the checkpoint's suite. No journal line, row, board comment, `RunConfig` field or exit-code change.
- Milestone-run resume (`orchestrate.run_milestone`) is untouched.
- Test tiers per CLAUDE.md: `kept_commands` tests are unmarked (unit); resume tests that drive `cli.run_card`/`cli.resume_run` are `@pytest.mark.brd` + `@pytest.mark.git`.
- Verification: `uv run pytest`, `uv run pytest -m brd`, `uv run pytest -m e2e_fake`.

## Review Focus

1. A checkpoint whose seed lacks `commands` (or whose pool / items / content is missing or oddly typed) must not break a resume: `kept_commands` returns `None` and no warning is added — pinned by Task 1's parametrized `test_kept_commands_is_none_when_the_seed_does_not_say` (includes `content: None` and `commands: "true"`).
2. A whitespace-differing or reordered suite counts as different and warns — pinned by Task 2's parametrized `test_a_task_resume_with_a_reordered_or_respaced_verify_announces_the_kept_suite` (reversed, extra duplicate, trailing space).
3. An opt-out run (`--allow-no-verification`, no commands) resumed with a `--verify` reports `[]`, not silence — pinned by Task 1's `test_kept_commands_is_empty_for_an_opted_out_seed` and Task 2's `test_an_opted_out_task_resume_with_a_verify_announces_an_empty_kept_suite`.
4. Flush warnings still lead the kept-suite warning — pinned by Task 2's `test_the_kept_suite_warning_follows_the_flush_warnings`.
5. A refused resume (digest mismatch) with a differing `--verify` carries no warning: the error envelope, exit 3, and nothing written — pinned by Task 2's `test_a_refused_task_resume_with_a_differing_verify_says_nothing_of_the_suite`.

## Spec

The spec this plan implements, verbatim from `docs/superpowers/specs/resume-announces-the-5b19aa93.md`:

## Resume announces the suite a checkpointed walk keeps

Card `5b19aa93`, child of story `9f445791`, blocked by `e1b1e7d5`.

### 1. Problem

`am resume <run-id> --verify ...` on a `task` run (`am run --card`) continues the
run's one in-flight subtask from its newest checkpoint. The walk keeps the
verification suite the run *started* with, because the agent, and with it the
`"subtask"` seed item holding `"commands"`, is rebuilt whole from the checkpoint
(`src/agent_manager/runtime/engine.py:166-170`). The suite built from the newly
passed `--verify` (`engine.py:131-133`) is only seeded on a fresh run
(`engine.py:178-179`). On a resume it is thrown away and nothing says so.

The behavior is correct and documented: README.md:39, the `--verify` help text at
`src/agent_manager/cli.py:2065-2073`, and the `resume_run` docstring at
`cli.py:1992-1999` all describe it. But it is silent. Milestone 17's operator passed
an instrumented `--verify` to `am resume`. They found out it was ignored only
because the probe file stayed empty.

This card makes the resume report say so.

### 2. Inherited constraints

- **Resume continues from the checkpoint's pool.** `am resume` continues with
  `Agent.from_dict`, so the pool (seed and earlier results) is the checkpoint's
  (pygents addendum §6 "Checkpoints and resume",
  `docs/superpowers/specs/2026-09-25-pygents-engine-design.md:265-306`, the
  "Resume:" bullets at lines 289-297). That addendum supersedes design spec §9
  (`docs/superpowers/specs/2026-09-23-agent-manager-design.md:350-352`).
- **The seed holds the verification commands.** The `"subtask"` seed `ContextItem`
  carries "the verification commands" as JSON (pygents addendum §5,
  `2026-09-25-pygents-engine-design.md:256-258`). In code it is
  `context.SUBTASK == "subtask"` (`runtime/context.py:23,63-66`), and its
  `content["commands"]` is a plain `list[str]` (`runtime/walk.py:109`; `encode`
  passes lists of `str` through unchanged, `runtime/context.py:27-42`).
- **The suite is recorded nowhere else.** `models.RunConfig` has no suite commands
  (`cli.py:1994-1996`; README.md:39 "The verification suite is not recorded"), so
  the checkpoint's pool is the only record of the suite being kept.
- **Output envelope.** CLI output is JSON by default, `--pretty` for humans, and uses
  the `brd` envelope (CLAUDE.md "Conventions"; design spec §10,
  `2026-09-23-agent-manager-design.md:410`). `--pretty` only indents the same JSON
  (`cli.py:177-186`). So "the warnings line a human reads" is the `warnings` entry
  of the same payload, and one change covers both readers.
- **The set of resume payload keys is closed.** `tests/test_cli.py:5119-5134`
  `RESUME_KEYS`: "the pygents branch adds and drops none (G10)". No new top-level
  key may be added. The report goes into the existing `warnings` list.
- **Warning order.** "the run's pending board comments are flushed (board-comments
  B7) and their warnings lead the payload's" (`cli.py:1869-1870`). Flushed warnings
  stay first.
- **`runtime/walk.py` never imports pygents** (walk.py rule 1, `walk.py:11-13`). A
  reader of the pygents-shaped checkpoint dict follows `engine.pending_phase`'s
  precedent (`engine.py:41-54`): read-only, it reads the stored
  `Agent.to_dict()`, builds nothing, and imports nothing from pygents.
- **Test tiers** follow CLAUDE.md "Test tiers" and design spec §14. A test's tier is
  set by what it spawns.

### 3. Observable behavior

#### 3.1 The kept suite, read from a checkpoint

A new read-only function, `kept_commands(checkpoint) -> list[str] | None`, lives in
`src/agent_manager/runtime/engine.py` beside `pending_phase`. It returns the
`commands` the checkpoint's walk will verify with:

- It looks in `checkpoint.agent["context_pool"]["items"]` for the item whose
  `"id"` is `context.SUBTASK` and returns that item's `content["commands"]` as a
  `list[str]`, in stored order.
- It returns `None` if any of these hold: there is no `context_pool`, no `items`,
  no item with id `"subtask"`, the item's `content` has no `"commands"` key, or
  `content["commands"]` is not a list. `None` means the kept suite is unknown. It
  never raises on a checkpoint shaped this way. A resume must never fail because
  of a report.
- It writes nothing and builds no agent.

#### 3.2 The resume report (`task` run)

`_resume_from_checkpoint` (`cli.py:1841-1967`) compares `commands`, the passed
`--verify` list (`[]` when the flag is omitted, `cli.py:2065-2073`; `()` by
default through `resume_run`), with `kept = kept_commands(checkpoint)`:

| Passed `commands` | `kept` | Report |
|---|---|---|
| empty (flag omitted) | anything | nothing added |
| non-empty, `list(commands) == kept` | same list | nothing added |
| non-empty, differs from `kept` (including `kept == []`) | a list | **one warning added** |
| non-empty | `None` (unknown) | nothing added |

- **Comparison** is exact and order-sensitive list equality: `list(commands) != kept`.
  The suite runs in order, so a reordered list or an added duplicate counts as a
  different suite. Strings are compared as given, with no whitespace normalising.
- **Warning text**, exactly: `f"verification: kept from checkpoint: {kept!r}"`,
  where `kept` is the `list[str]` from §3.1. For example:
  `verification: kept from checkpoint: ['uv run pytest']`. When the run started
  with no suite (`--allow-no-verification`), it reads
  `verification: kept from checkpoint: []`. The warning names the *kept* suite,
  never the passed one.
- **Position:** `"warnings": [*flushed, *kept_warning, *drive.warnings]`.
  Board-comment flush warnings still lead (B7), and the kept-suite warning comes
  before every warning the walk produced.
- **At most one** such warning per resume.
- **When it is decided:** after the checkpoint is accepted (after
  `checkpoint_resume_phase` returns, `cli.py:1890-1892`). A refused resume (digest
  mismatch, no checkpoint, `done` or escalated checkpoint, cancelled or live run)
  behaves exactly as today: same error envelope, exit 3, nothing written. The
  warning never appears on a refusal, because there is no payload.
- **Every outcome:** the warning appears whether the resumed walk ends `done`,
  `escalated`, `stopped` or `cancelled`, because it describes the walk's input, not
  its result.
- **Nothing else changes.** The walk still runs with the checkpoint's suite (this
  card does not honor the new one), `set(payload) == RESUME_KEYS` still holds, the
  exit codes are unchanged, and no journal line, row or board comment is added.
  The warning lives only in the returned payload.

#### 3.3 Documentation

- README.md:39 (the `am resume` synopsis paragraph) gains one sentence: when
  `--verify` on a `--card` run differs from the kept suite, `data.warnings` names
  the kept suite as `verification: kept from checkpoint: [...]`.
- The `resume` command's `--verify` help text (`cli.py:2069-2072`) gains the same
  fact in one clause: "...keeps the suite the run started with, and says so in
  `warnings` when it differs."

### 4. Out of scope

- **Honoring the new suite.** The resumed walk keeps verifying with the
  checkpoint's commands (card: "Out of scope: honouring the new suite").
- **The `--allow-no-verification` opt-out.** The kept opt-out is not compared or
  reported. The card asks for the kept *commands* only. The opt-out is not part of
  the seed binding (`walk.py:100-110`), so nothing in the checkpoint records it to
  compare against.
- **Milestone-run resume** (`orchestrate.run_milestone(resume_run_id=...)`,
  `cli.py:2032-2040`). There, `--verify` *is* honored for subtasks with no
  checkpoint, merged bases and Integrate (README.md:39, 320). A per-lane "kept"
  report would need its own payload design across lanes. That belongs to a sibling
  card if wanted, not this one. Milestone payloads are untouched.
- **A relaunch** (`am run --milestone`) that continues an open checkpoint from an
  earlier run. It is not `am resume` and is untouched.
- **Any new payload key, journal line, projection column or recorded copy of the
  suite** (`models.RunConfig` stays as is).
- **Work of the blocker card `e1b1e7d5`** (`am logs --phase verify` reading
  persisted verify output). Already on this branch, and not touched.

### 5. Tests that prove it

| # | Test | File | Tier | Why this tier |
|---|---|---|---|---|
| T1 | `kept_commands` returns the `"subtask"` item's `commands` from a hand-built `Checkpoint` whose `agent` is a minimal `{"context_pool": {"items": [...]}}` dict, in stored order, among other items | `tests/runtime/test_resume.py` | unit (unmarked) | Pure function over a dataclass. Nothing spawned. |
| T2 | `kept_commands` returns `None` for: no `context_pool`; no `"subtask"` item; `content` without `"commands"`; `commands` not a list. Parametrized. | `tests/runtime/test_resume.py` | unit | Same as T1. Pins "a report never breaks a resume". |
| T3 | `kept_commands` returns `[]` for a seed whose `commands` is `[]` (a run started with the opt-out) | `tests/runtime/test_resume.py` | unit | Same as T1. Separates "empty suite" from "unknown". |
| T4 | A `task` run started with `commands=["true"]`, killed in `plan` (`_crash_pygents` extended with an optional `commands` argument passed through to `run_card`), resumed with `commands=["echo instrumented"]`: `payload["warnings"]` contains exactly one `"verification: kept from checkpoint: ['true']"`, it names the kept suite and not `echo instrumented`, and `set(payload) == RESUME_KEYS` | `tests/test_cli.py` | `@pytest.mark.brd` + `@pytest.mark.git` | Drives real `cli.run_card` and `cli.resume_run`, which call real `brd` (`board.show`) and real git worktrees, like the sibling `test_a_pygents_run_killed_in_plan_resumes_at_plan_from_its_checkpoint` (`test_cli.py:5295-5312`). No `claude`, because the recording runner fakes agents. |
| T5 | Same crash, resumed with the identical `commands=["true"]`: no warning starting `verification: kept from checkpoint` | `tests/test_cli.py` | brd + git | Same as T4. |
| T6 | Same crash, resumed with `commands` omitted (default `()`): no such warning | `tests/test_cli.py` | brd + git | Same as T4. Covers the "none passed" case. |
| T7 | Same crash, resumed with the same commands reordered or with one extra (`["true", "true"]`): the warning appears | `tests/test_cli.py` | brd + git | Pins order-sensitive equality through the real wiring. |
| T8 | A run started with no suite (`allow_no_verification=True`, `commands=()`), crashed and resumed with `commands=["uv run pytest"]`: the warning reads `verification: kept from checkpoint: []` | `tests/test_cli.py` | brd + git | Same as T4. The opt-out case. |
| T9 | Ordering: with a board-comment flush warning forced (reuse the mechanism of `test_a_task_resume_whose_start_flush_fails_warns_and_still_walks`, `test_cli.py:5373+`) and a differing `--verify`, the flush warning comes first and the kept-suite warning second | `tests/test_cli.py` | brd + git | Same as T4. Pins B7 "flush warnings lead". |

T4 to T9 sit in the opt-in `brd` tier, as every existing `_crash_pygents` resume test
does. Every one of them must be run with `uv run pytest -m brd` as well as the default
`uv run pytest`. The planner may fold T7 to T9 into fewer test functions if each
assertion stays separate.

### 6. Hand-off notes for the planner

Follow the `writing-plans` format (plan header, Global Constraints, Review Focus,
`Files` / `Interfaces` blocks, bite-sized TDD steps).

- **File map:**
  - Modify `src/agent_manager/runtime/engine.py`: add `kept_commands` beside
    `pending_phase`.
  - Modify `src/agent_manager/cli.py`: add the comparison and the warning in
    `_resume_from_checkpoint` at the `"warnings"` line (`cli.py:1952`); update the
    `--verify` help text and the `resume_run` docstring (`cli.py:1992-1999`).
  - Modify `README.md:39`.
  - Tests in `tests/runtime/test_resume.py` and `tests/test_cli.py`.
- **Interface:** `runtime.engine.kept_commands(checkpoint: Checkpoint) -> list[str] | None`.
- **Suggested task cut:**
  1. `kept_commands` with T1 to T3.
  2. The `_resume_from_checkpoint` warning with T4 to T9.
  3. The doc and help text, folded into task 2.
- **Review Focus candidates:**
  - a checkpoint whose seed lacks `commands` must not break a resume;
  - whitespace or reordered suites count as different;
  - an opt-out run reports `[]`, not silence;
  - flush warnings still lead;
  - a refused resume carries no warning.
- **Verification:** `uv run pytest`, `uv run pytest -m brd`, `uv run pytest -m e2e_fake`.

---

## File Structure

- Modify `src/agent_manager/runtime/engine.py` — add `kept_commands` right after `pending_phase` (ends at line 54).
- Modify `src/agent_manager/cli.py` — in `_resume_from_checkpoint` (lines 1841-1967): compute the warning after `checkpoint_resume_phase` (line 1890-1892) and splice it into `"warnings"` (line 1952); extend the docstring's last paragraph; `resume_run` docstring (lines 1992-1999); `--verify` help text in `resume` (lines 2065-2073).
- Modify `README.md:39`.
- Test `tests/runtime/test_resume.py` — unit tests for `kept_commands`, beside the `pending_phase` tests (lines 442-481).
- Test `tests/test_cli.py` — extend `_crash_pygents` (line 5137) and add the resume tests after `test_a_task_resume_whose_start_flush_fails_warns_and_still_walks` (ends near line 5410).

---

### Task 1: `kept_commands` reads the kept suite from a checkpoint

**Files:**
- Modify: `src/agent_manager/runtime/engine.py:41-54` (add a function after `pending_phase`)
- Test: `tests/runtime/test_resume.py` (after `test_a_done_checkpoint_has_no_pending_phase`, near line 481)

**Interfaces:**
- Consumes: `agent_manager.store.Checkpoint` (dataclass; `agent: dict` is the decoded stored `Agent.to_dict()`); `agent_manager.runtime.context.SUBTASK == "subtask"` (already imported in `engine.py` as `context`).
- Produces: `runtime.engine.kept_commands(checkpoint: Checkpoint) -> list[str] | None` — the `"subtask"` seed item's `content["commands"]` in stored order, or `None` when unknown. Task 2 calls it as `runtime_engine.kept_commands(checkpoint)` from `cli.py`.

Background for the engineer: a pygents `ContextPool.to_dict()` looks like
`{"limit": None, "items": [{"id": "subtask", "description": "...", "content": {...}}], "hooks": {}, "tags": []}`,
and `Agent.to_dict()` stores it under `"context_pool"`. The seed's `content` is the
encoded binding from `walk.subtask_context`, whose `"commands"` is `list(commands)` —
plain strings pass through `context.encode` unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/runtime/test_resume.py`, right after `test_a_done_checkpoint_has_no_pending_phase`:

```python
def _checkpoint_with(agent: dict) -> store_module.Checkpoint:
    return store_module.Checkpoint(
        run_id=RUN_ID,
        card_id=CARD_ID,
        seq=0,
        workflow="task",
        digest="any",
        reason="turn",
        agent=agent,
        saved_at=FIXED,
    )


def _pool(*items: dict) -> dict:
    return {"context_pool": {"limit": None, "items": list(items), "hooks": {}, "tags": []}}


def test_kept_commands_reads_the_seeds_commands_in_stored_order():
    """Spec T1: the `"subtask"` seed item's `commands`, among other items."""
    checkpoint = _checkpoint_with(
        _pool(
            {"id": "explore", "description": "result", "content": {"commands": ["no"]}},
            {
                "id": "subtask",
                "description": "fixed subtask context",
                "content": {"card": CARD_ID, "commands": ["uv run pytest", "uv run ruff check"]},
            },
            {"id": "spec", "description": "result", "content": {"spec": "SPEC"}},
        )
    )

    assert runtime_engine.kept_commands(checkpoint) == ["uv run pytest", "uv run ruff check"]


@pytest.mark.parametrize(
    "agent",
    [
        pytest.param({}, id="no-context-pool"),
        pytest.param({"context_pool": None}, id="null-context-pool"),
        pytest.param({"context_pool": {}}, id="no-items"),
        pytest.param(
            _pool({"id": "spec", "description": "result", "content": {"commands": ["x"]}}),
            id="no-subtask-item",
        ),
        pytest.param(
            _pool({"id": "subtask", "description": "seed", "content": {"card": CARD_ID}}),
            id="no-commands-key",
        ),
        pytest.param(
            _pool({"id": "subtask", "description": "seed", "content": {"commands": "true"}}),
            id="commands-not-a-list",
        ),
        pytest.param(
            _pool({"id": "subtask", "description": "seed", "content": None}),
            id="content-not-a-dict",
        ),
    ],
)
def test_kept_commands_is_none_when_the_seed_does_not_say(agent):
    """Spec T2: unknown, never an exception -- a report must not break a resume."""
    assert runtime_engine.kept_commands(_checkpoint_with(agent)) is None


def test_kept_commands_is_empty_for_an_opted_out_seed():
    """Spec T3: a run started with `--allow-no-verification` kept `[]`, not "unknown"."""
    checkpoint = _checkpoint_with(
        _pool({"id": "subtask", "description": "seed", "content": {"commands": []}})
    )

    assert runtime_engine.kept_commands(checkpoint) == []


def test_kept_commands_reads_a_real_checkpoints_seed(store):
    """The shape above is the one the engine really saves."""
    ran: list[str] = []
    with pytest.raises(_Crash):
        _go(_five(ran, {"c"}), store, commands=["uv run pytest"])

    assert runtime_engine.kept_commands(store.latest_checkpoint(CARD_ID)) == ["uv run pytest"]
```

Note: `_pool` is used inside the `parametrize` decorator, so it must be defined above `test_kept_commands_is_none_when_the_seed_does_not_say` — the order above does that. `_go` forwards `commands` to `runtime_engine.run_subtask` through `**kwargs`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/runtime/test_resume.py -k kept_commands -v`
Expected: every test FAILS with `AttributeError: module 'agent_manager.runtime.engine' has no attribute 'kept_commands'`.

- [ ] **Step 3: Write the implementation**

In `src/agent_manager/runtime/engine.py`, directly after `pending_phase` (after line 54, `return None if turn is None else turn["kwargs"]["phase"]`), add:

```python


def kept_commands(checkpoint: Checkpoint) -> list[str] | None:
    """The verification commands `checkpoint`'s walk keeps, or `None` if it does not say.

    Read-only, as `pending_phase` is: it reads the `"subtask"` seed item out of
    the stored `Agent.to_dict()` and builds nothing. A resume continues with
    the checkpoint's pool, so this seed -- not a newly passed `--verify` -- is
    the suite the resumed walk verifies with (card 5b19aa93). A pool with no
    seed, a seed with no `commands`, or `commands` that are not a list give
    `None`, never an exception: a report must never fail a resume.
    """
    pool = checkpoint.agent.get("context_pool") or {}
    for item in pool.get("items") or ():
        if item.get("id") != context.SUBTASK:
            continue
        content = item.get("content")
        commands = content.get("commands") if isinstance(content, dict) else None
        return list(commands) if isinstance(commands, list) else None
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/runtime/test_resume.py -v`
Expected: all PASS (the new `kept_commands` tests and every existing test in the file).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/runtime/engine.py tests/runtime/test_resume.py
git commit -m "feat: runtime.engine.kept_commands reads a checkpoint's kept suite (5b19aa93)"
```

---

### Task 2: The `task` resume announces a differing kept suite (code, help, docs)

**Files:**
- Modify: `src/agent_manager/cli.py:1841-1967` (`_resume_from_checkpoint`), `cli.py:1971-2003` (`resume_run` docstring), `cli.py:2065-2073` (`--verify` help)
- Modify: `README.md:39`
- Test: `tests/test_cli.py:5137-5149` (`_crash_pygents`) and new tests after `test_a_task_resume_whose_start_flush_fails_warns_and_still_walks` (near line 5410)

**Interfaces:**
- Consumes: `runtime_engine.kept_commands(checkpoint: store_module.Checkpoint) -> list[str] | None` from Task 1 (`cli.py` already has `from agent_manager.runtime import engine as runtime_engine` at line 54).
- Produces: a resume payload whose `"warnings"` is `[*flushed, *kept_warning, *drive.warnings]`, where `kept_warning` is `[]` or `[f"verification: kept from checkpoint: {kept!r}"]`. Test helper `_crash_pygents(project, cards, phase, *, commands=(), allow_no_verification=False)`.

- [ ] **Step 1: Extend `_crash_pygents`**

Replace `_crash_pygents` in `tests/test_cli.py` (lines 5137-5149) with:

```python
def _crash_pygents(
    project: Path,
    cards: dict[str, str],
    phase: str,
    *,
    commands: tuple[str, ...] = (),
    allow_no_verification: bool = False,
) -> str:
    """Drive a real pygents `run_card` until it is killed inside `phase`, and name the run.

    `commands` and `allow_no_verification` are what the run starts with: the
    seed, and so the suite a resumed walk keeps."""
    run_id = cli.mint_run_id(cards["subtask"], CRASHED_AT)
    with pytest.raises(_Killed):
        cli.run_card(
            cards["subtask"],
            repo_dir=project,
            base_branch="main",
            branch_prefix="m1",
            commands=commands,
            allow_no_verification=allow_no_verification,
            clock=lambda: CRASHED_AT,
            runner_factory=_resume_factory(crash_at=phase, crash_with=_Killed),
        )
    return run_id
```

Every existing caller passes three positional arguments, so they are unchanged.

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_cli.py` right after `test_a_task_resume_whose_start_flush_fails_warns_and_still_walks`:

```python
KEPT = "verification: kept from checkpoint"


def _kept_warnings(payload: dict[str, Any]) -> list[str]:
    return [w for w in payload["warnings"] if w.startswith(KEPT)]


@pytest.mark.brd
@pytest.mark.git
def test_a_task_resume_with_a_different_verify_announces_the_kept_suite(project, cards):
    """Card 5b19aa93, spec T4: one warning naming the kept suite, not the passed one."""
    run_id = _crash_pygents(project, cards, "plan", commands=("true",))

    payload = cli.resume_run(
        run_id,
        repo_dir=project,
        commands=["echo instrumented"],
        runner_factory=_resume_factory(),
    )

    assert _kept_warnings(payload) == ["verification: kept from checkpoint: ['true']"]
    assert not [w for w in payload["warnings"] if "echo instrumented" in w]
    assert set(payload) == RESUME_KEYS


@pytest.mark.brd
@pytest.mark.git
def test_a_task_resume_with_the_same_verify_says_nothing_of_the_suite(project, cards):
    """Spec T5: the passed suite is the kept one, so there is nothing to announce."""
    run_id = _crash_pygents(project, cards, "plan", commands=("true",))

    payload = cli.resume_run(
        run_id, repo_dir=project, commands=["true"], runner_factory=_resume_factory()
    )

    assert _kept_warnings(payload) == []
    assert set(payload) == RESUME_KEYS


@pytest.mark.brd
@pytest.mark.git
def test_a_task_resume_with_no_verify_says_nothing_of_the_suite(project, cards):
    """Spec T6: `--verify` omitted (`commands=()`) is not a different suite."""
    run_id = _crash_pygents(project, cards, "plan", commands=("true",))

    payload = cli.resume_run(run_id, repo_dir=project, runner_factory=_resume_factory())

    assert _kept_warnings(payload) == []
    assert set(payload) == RESUME_KEYS


@pytest.mark.brd
@pytest.mark.git
@pytest.mark.parametrize(
    "passed",
    [
        pytest.param(["echo kept", "true"], id="reordered"),
        pytest.param(["true", "echo kept", "true"], id="one-extra"),
        pytest.param(["true ", "echo kept"], id="trailing-space"),
    ],
)
def test_a_task_resume_with_a_reordered_or_respaced_verify_announces_the_kept_suite(
    project, cards, passed
):
    """Spec T7 and Review Focus 2: equality is exact and order-sensitive."""
    run_id = _crash_pygents(project, cards, "plan", commands=("true", "echo kept"))

    payload = cli.resume_run(
        run_id, repo_dir=project, commands=passed, runner_factory=_resume_factory()
    )

    assert _kept_warnings(payload) == [
        "verification: kept from checkpoint: ['true', 'echo kept']"
    ]
    assert set(payload) == RESUME_KEYS


@pytest.mark.brd
@pytest.mark.git
def test_an_opted_out_task_resume_with_a_verify_announces_an_empty_kept_suite(
    project, cards
):
    """Spec T8 and Review Focus 3: an opted-out run kept `[]`, and says so."""
    run_id = _crash_pygents(project, cards, "plan", allow_no_verification=True)

    payload = cli.resume_run(
        run_id,
        repo_dir=project,
        commands=["uv run pytest"],
        runner_factory=_resume_factory(),
    )

    assert _kept_warnings(payload) == ["verification: kept from checkpoint: []"]
    assert set(payload) == RESUME_KEYS


@pytest.mark.brd
@pytest.mark.git
def test_the_kept_suite_warning_follows_the_flush_warnings(project, cards, monkeypatch):
    """Spec T9 and Review Focus 4: B7's flush warnings lead, the kept suite is next."""
    run_id = _crash_pygents(project, cards, "plan", commands=("true",))
    key = f"{run_id}/{cards['subtask']}/escalated:an-earlier-life"
    opened = store_module.Store.open(cli.resolve_repo_dir(project), run_id)
    try:
        opened.enqueue_comment(
            run_id=run_id,
            card_id=cards["subtask"],
            key=key,
            body=f"am · escalated · run {run_id}\nphase: plan\nam-key: {key}",
            now=datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc),
        )
    finally:
        opened.close()

    def down(card_id, *, repo_dir=None):
        raise board.BoardError(
            "brd is down", argv=["brd", "comment", "list", card_id], exit_code=1
        )

    monkeypatch.setattr(board, "comment_list", down)

    payload = cli.resume_run(
        run_id,
        repo_dir=project,
        commands=["echo instrumented"],
        runner_factory=_resume_factory(),
    )

    assert set(payload) == RESUME_KEYS
    flush_at = [
        i for i, w in enumerate(payload["warnings"]) if f"board comment {key} " in w
    ]
    kept_at = [i for i, w in enumerate(payload["warnings"]) if w.startswith(KEPT)]
    assert flush_at == [0], payload["warnings"]
    assert kept_at == [1], payload["warnings"]
    assert payload["warnings"][1] == "verification: kept from checkpoint: ['true']"


@pytest.mark.brd
@pytest.mark.git
def test_a_refused_task_resume_with_a_differing_verify_says_nothing_of_the_suite(
    project, cards, monkeypatch
):
    """Review Focus 5: a refusal is today's error envelope, exit 3, nothing written."""
    run_id = _crash_pygents(project, cards, "plan", commands=("true",))
    _plant_changed_digest(project, run_id, cards["subtask"])
    before = _resume_state(project)
    monkeypatch.setattr(cli, "default_runner_factory", _Forbidden("default_runner_factory"))

    result = runner.invoke(
        cli.app,
        ["resume", run_id, "--repo-dir", str(project), "--verify", "echo instrumented"],
    )

    assert result.exit_code == cli.EXIT_ERROR, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "CheckpointMismatchError"
    assert KEPT not in result.stdout
    assert _resume_state(project) == before
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -m "brd" -k "kept_suite or says_nothing_of_the_suite or empty_kept_suite" -v`
Expected: `test_a_task_resume_with_a_different_verify_announces_the_kept_suite`, the three `..._reordered_or_respaced_...` cases, `test_an_opted_out_task_resume_...` and `test_the_kept_suite_warning_follows_the_flush_warnings` FAIL (`_kept_warnings(payload) == []` / `kept_at == []`). `test_a_task_resume_with_the_same_verify_...`, `..._with_no_verify_...` and `test_a_refused_task_resume_...` already PASS — they pin today's behavior and must keep passing.

- [ ] **Step 4: Implement the warning in `_resume_from_checkpoint`**

In `src/agent_manager/cli.py`, inside `_resume_from_checkpoint`, right after

```python
            phase = checkpoint_resume_phase(
                checkpoint, card_id=subtask.card_id, run_id=run.id
            )
```

add:

```python
            # Card 5b19aa93: the walk keeps the checkpoint's suite, not
            # `commands`. Say so when a passed `--verify` differs from it; an
            # omitted flag or an unknown kept suite says nothing.
            kept = runtime_engine.kept_commands(checkpoint)
            kept_warning = (
                [f"verification: kept from checkpoint: {kept!r}"]
                if commands and kept is not None and list(commands) != kept
                else []
            )
```

Then change the payload line

```python
            "warnings": [*flushed, *drive.warnings],
```

to

```python
            "warnings": [*flushed, *kept_warning, *drive.warnings],
```

And extend the docstring's last paragraph (currently "Once the checkpoint is accepted, the run's pending board comments are flushed (board-comments B7) and their warnings lead the payload's.") to:

```python
    Once the checkpoint is accepted, the run's pending board comments are
    flushed (board-comments B7) and their warnings lead the payload's. Next
    comes, when a passed `commands` differs from the suite the checkpoint
    keeps (`runtime_engine.kept_commands`), one `verification: kept from
    checkpoint: [...]` warning naming the kept suite (card 5b19aa93).
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -m "brd" -k "kept_suite or says_nothing_of_the_suite or empty_kept_suite" -v`
Expected: all PASS.

- [ ] **Step 6: Update the help text, the `resume_run` docstring and README.md:39**

In `src/agent_manager/cli.py`, the `resume` command's `--verify` option help becomes:

```python
        help=(
            "A walk continued from a checkpoint keeps the suite the run started "
            "with, and says so in `warnings` when it differs. On a milestone run, "
            "this is the suite for what starts afresh: subtasks with no "
            "checkpoint, merged bases and Integrate."
        ),
```

In `resume_run`'s docstring, replace

```
    are still taken as arguments. A walk continued from a checkpoint never
    reads them: its binding comes from the checkpoint's pool. On a milestone
```

with

```
    are still taken as arguments. A walk continued from a checkpoint never
    reads them: its binding comes from the checkpoint's pool, and a `task`
    resume whose `commands` differ from that pool's adds a `verification:
    kept from checkpoint: [...]` warning. On a milestone
```

In `README.md` line 39, replace

```
so on a `--card` run `--verify` and `--allow-no-verification` have no effect.
```

with

```
so on a `--card` run `--verify` and `--allow-no-verification` have no effect. When `--verify` on a `--card` run differs from the kept suite, `data.warnings` names the kept suite as `verification: kept from checkpoint: [...]`.
```

(keeping the rest of the line unchanged, so it stays one paragraph line).

- [ ] **Step 7: Run the full verification**

Run: `uv run pytest`
Expected: PASS (no failures).

Run: `uv run pytest -m brd`
Expected: PASS, including every `_crash_pygents` resume test.

Run: `uv run pytest -m e2e_fake`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py README.md
git commit -m "feat: a task resume announces the suite its checkpoint keeps (5b19aa93)"
```

---

## Self-Review

- Spec §3.1 → Task 1 (T1 `..._in_stored_order`, T2 parametrized `None` cases, T3 `[]`, plus a real-checkpoint shape test).
- Spec §3.2 table → Task 2: empty passed (T6), equal (T5), differs incl. `kept == []` (T4, T7, T8), `None` → covered by the `commands and kept is not None` guard and Task 1's T2 cases. Exact text (T4/T7/T8 compare whole strings), position (T9), at most one (`_kept_warnings` compared to one-element lists), decided after acceptance (refusal test), every outcome (warning computed before the walk, independent of `summary`), payload keys (`RESUME_KEYS` in every test).
- Spec §3.3 → Task 2 Step 6.
- Spec §4 out of scope: milestone resume path untouched (`resume_run` change is docstring only).
- Names consistent: `kept_commands`, `kept_warning`, `KEPT`, `_kept_warnings`, `_crash_pygents(..., commands=, allow_no_verification=)`.
<!-- task-pipeline: validated -->
