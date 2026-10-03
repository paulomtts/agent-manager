# Resume announces the suite a checkpointed walk keeps

Card `5b19aa93`, child of story `9f445791`, blocked by `e1b1e7d5`.

## 1. Problem

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

## 2. Inherited constraints

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

## 3. Observable behavior

### 3.1 The kept suite, read from a checkpoint

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

### 3.2 The resume report (`task` run)

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

### 3.3 Documentation

- README.md:39 (the `am resume` synopsis paragraph) gains one sentence: when
  `--verify` on a `--card` run differs from the kept suite, `data.warnings` names
  the kept suite as `verification: kept from checkpoint: [...]`.
- The `resume` command's `--verify` help text (`cli.py:2069-2072`) gains the same
  fact in one clause: "...keeps the suite the run started with, and says so in
  `warnings` when it differs."

## 4. Out of scope

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

## 5. Tests that prove it

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

## 6. Hand-off notes for the planner

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
