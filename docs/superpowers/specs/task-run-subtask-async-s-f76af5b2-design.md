# Resume re-ensures the worktree in `run_subtask_async` (subtask f76af5b2) — design

Parent story: ab65eee1 "A resume survives a worktree deleted outside am's bookkeeping". Narrowed from `docs/superpowers/specs/2026-10-03-resume-worktree-reensure-design.md` §§3.1, 3.2, 3.4, the engine-side half of §3.5, §3.7 and §4 items 1-6. That document stays the source of truth; this one only fixes what this subtask delivers.

## Scope

In scope:

- `src/agent_manager/runtime/engine.py`: the worktree re-check inside `run_subtask_async`'s `resume_from is not None` branch, and the new `ensure_worktree` keyword on `run_subtask` and `run_subtask_async`.
- `src/agent_manager/runtime/walk.py`: the new field `SubtaskSummary.resumed_at: str | None = None`, set by the engine.
- `tests/runtime/test_resume.py`: the six new unit tests below.
- Existing unit tests that resume with a worktree path that does not exist on disk: `tests/runtime/test_resume.py` (`Path("/w")` in `_subtask()`), `tests/runtime/test_exactly_once.py`, `tests/runtime/test_cancellation.py`, `tests/runtime/test_stop_bridge.py`, and the `task`-run resume tests in `tests/test_cli.py` built on `Path("/repo/...")`. Each one gets a real `tmp_path` directory or an injected fake `ensure_worktree`, so it keeps testing the path it tested before and does not fall into the decline path. `tests/test_orchestrate.py` needs no change because it never reaches the engine.

Out of scope:

- `src/agent_manager/steps/worktree.py`. The `-f` fix for a path that is registered but missing already landed in sibling cf03b236 (done). This subtask reads `ensure`'s result keys as they are: the five keys are unchanged and only `branch_existed` is read.
- Reading `resumed_at` (`am resume`'s `resumed_from`, the lane's `(resumed at <phase>)` comment via `compose_done`) and the README "Resuming: what runs again" docs. Sibling dd932306 owns these.
- The git-tier `worktree.py` tests (§4 items 7-10), which belong to cf03b236. Note: the exploration summary was cut off at this point (truncated at 8000 chars). It did not say who owns the `e2e_fake` scenario (§4 item 11). This spec does not claim it. Its assertion on `data.resumed_from` depends on dd932306's plumbing, so it fits that subtask or a later one better than this one.
- `cli.py`, `orchestrate.py` and `bases.py` do not change (§3.7, one call site).

## Observable behavior

`run_subtask(...)` and `run_subtask_async(...)` gain the keyword `ensure_worktree: Callable[..., Mapping[str, object]] = worktree.ensure`. It sits next to `agent_runner`/`clock`, and `run_subtask` forwards it. No caller in `src/` passes it.

In the resume branch the order is: (1) the digest check, unchanged, raising `CheckpointMismatch` before anything else; (2) the new worktree re-check; (3) the existing resume path (`_forget`, `Agent.from_dict`, `agent.resume()`, the adoption floor) if the checkpoint is kept, or (4) the existing fresh-walk path, used verbatim, if it is declined. The fresh-walk path builds a new `Agent`, adds the seed item from `binding` and `compiled.first_turn()`, and sets `adopt=None`.

With `W = subtask.worktree_path` and `B = subtask.branch`:

| State | Git run | Outcome | Warning | `summary.resumed_at` |
|---|---|---|---|---|
| `W is None` | none | checkpoint kept | none | pending phase |
| `W` is a directory (`Path.is_dir()`) | none (fast path) | checkpoint kept | none | pending phase |
| `W` missing; `ensure_worktree(B, subtask.base_branch, W, repo_dir)` returns `branch_existed=True` | one call, on a thread via `asyncio.to_thread` | checkpoint kept; walk continues at its pending phase | re-added | pending phase |
| `W` missing; seam returns `branch_existed=False` | one call | declined; fresh walk from phase 0 | declined, branch gone | `None` |
| `W` missing; seam raises any `Exception` | one call | declined; fresh walk from phase 0, whose own `ensure` step reports the real error | declined, re-add failed | `None` |

The pending phase is `pending_phase(resume_from)`. A fresh walk with no checkpoint leaves `resumed_at` as `None`.

Warnings are worded exactly as in the milestone spec §3.4. `<seq>`/`<run-id>` follow `checkpoint_resume_phase`'s naming, `<path>` is `W`, `<phase>` is the pending phase, and the error goes through `walk._render_error`:

- `checkpoint #<seq> of run <run-id>: worktree <path> was missing and was added again for branch '<branch>'; resuming at '<phase>'`
- `checkpoint #<seq> of run <run-id> was not resumed (worktree <path> is missing and branch '<branch>' no longer exists); starting from the first phase`
- `checkpoint #<seq> of run <run-id> was not resumed (worktree <path> is missing and could not be added again: GitError: <message>); starting from the first phase`

Each outcome adds exactly one line, and the fast path adds none. The line is appended to the warnings list passed into `RunDeps(warnings=...)`, so `_collect` carries it into `summary.warnings` with no new plumbing.

## Error paths and invariants

- A missing worktree is a recovery, not a refusal. The re-check never raises. Every exception from the seam is caught and turned into a decline plus a warning.
- The engine does not escalate before the agent exists. When the re-add fails, phase 0 of the fresh walk runs `ensure` again under the normal step machinery. If that fails too, the subtask is escalated at `worktree` with a `GitError: ...` detail and a `failed` phase row.
- A declined checkpoint row is neither deleted nor rewritten. The fresh walk's first `turn` save at a higher `seq` replaces it as the card's newest open checkpoint.
- The digest check stays first, and a mismatch raises before the seam is called.
- The fast path spawns no subprocess. In tests every non-fast path goes through the injected seam, never through real git.

## Tests

The tier for each test follows the placement rule in CLAUDE.md "Test tiers": a test's tier is set by what it spawns or touches, not by its directory. Every test below drives the engine through `run_subtask` with an injected recording fake `ensure_worktree` and `tmp_path` directories, and spawns no subprocess. All of them are therefore unmarked (`unit`). They live in `tests/runtime/test_resume.py` and use the `_five` workflow. `_subtask()`'s worktree becomes a `tmp_path` directory instead of `Path("/w")`.

1. Intact worktree, unaffected (unit). Crash in `c`, then resume with the directory present. Assert: fake never called; `ran` is `["a","b","c"] + ["c","d","e"]`; `summary.warnings == []`; `summary.resumed_at == "c"`.
2. Worktree deleted, branch survives (unit). Remove the directory; the fake returns `branch_existed=True, worktree_existed=False, created=True`. Assert: fake called once with `(branch, base_branch, worktree_path, repo_dir)`; walk continues at `c`; exactly one warning, the re-added line; `resumed_at == "c"`; the carried adoption floor is unchanged (same assertion shape as `test_a_carried_floor_survives_a_resume`).
3. Worktree and branch both gone (unit). The fake returns `branch_existed=False, created=True`. Assert: `a` through `e` all run; exactly one warning, the branch-gone declined line; `resumed_at is None`; `deps.adopt is None`; the card's `latest_checkpoint` is newer than the declined row, and `latest_open_checkpoint` no longer returns that row.
4. Re-add raised (unit). The fake raises `GitError`, and phase 0 of the workflow is a step backed by the same raising fake. Assert: exactly one warning, the "could not be added again" line; the subtask ends `escalated` at phase 0 with a `detail` starting `GitError:`; the phase row is recorded `failed`; no exception escapes the engine.
5. No worktree path (unit). With `worktree_path=None`, assert the fake is never called and the resume is unchanged (continues at `c`, no warnings, `resumed_at == "c"`).
6. Digest first (unit). With a mismatched digest and a missing worktree, assert `CheckpointMismatch` is raised and the fake is never called.

Existing-test fixes (unit, stays unit): `test_resume.py`, `test_exactly_once.py`, `test_cancellation.py`, `test_stop_bridge.py`, and the `Path("/repo/...")` resume tests in `tests/test_cli.py` get a real `tmp_path` worktree directory or a fake `ensure_worktree`. Their existing assertions must pass unchanged. None of them may start spawning git, because that would move them out of the unit tier.
