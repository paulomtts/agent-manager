# Subtask b9c16de5: Document several am processes per repository

Card `b9c16de5-dfd2-4d76-be87-7b4eed035aa3`, story `f88c5d7d` ("Proof and documentation"), milestone 10. This is Task 3.2 of `docs/superpowers/plans/2026-09-27-multi-process.md`, narrowed. The agreed design is `docs/superpowers/specs/2026-09-27-multi-process-design.md` (§3 X1–X11, §6 Error cases). That file lives on branch `docs/multi-process` (worktree `.claude/worktrees/docs-multi-process`), not on this branch, so read it there. Blocked by `cfcfa6e3` (done), whose code this worktree already contains (`src/agent_manager/locks.py` exists here).

## Scope

This subtask changes documentation only: `README.md`, one docstring in `src/agent_manager/store.py`, and superseded pointers in spec files. It adds, changes and deletes no tests, fixtures, `fake_claude.py` knobs or `tests/e2e/conftest.py` helpers. Those belong to sibling `cfcfa6e3`. It changes no runtime behaviour.

Write the documentation from the code as built (plan Step 1), not from the spec. Read `locks.py` (`ProcessLock`, `project_lock`, `LockTimeoutError`, `LockOrderError`, `LOCK_TIMEOUT_SECONDS`), `store.take_lease` and `store.LeaseLostError`, `control.Lease`, `cli.run_lease`, `cli.refuse_claimed`, `cli.ClaimedError` and `cli.RunIsLiveError`, `orchestrate.milestone_claims`, and `paths.project_lock_path`. Copy refusal types and message wording from the code that raises them. If the code differs from spec §3/X11, the code wins.

### 1. README.md

Remove the paragraph at `README.md:185-186` ("Run one `am` process per repository. Two `am` processes on the same repository or on the same run are not supported."). In its place, add a "Several am processes" section. Put it where the old paragraph was, or as its own heading right next to it, before the existing "Limits, stated plainly" list. It covers the following:

- **What may run together.** Give X1's table: a milestone run or a `--card` run, set against `am run --milestone`, `am run --card`, `am resume` and the readers. Give the rule in one line: runs may overlap when their cards and branches do not overlap. Add X6's claim keys (`card:<id>`, `branch:<name>`) and which command claims which, in the depth needed to explain the refusals.
- **Refusals.** Every refusal exits 3 with brd's error envelope, before any write. A `--card` or milestone refusal leaves no run directory, no fetch and no prune. For each refusal give the exact `type` and message shape:
  - `RunIsLiveError`: resume of a live run, and the loser of two resumes racing for one dead run.
  - `ClaimedError` on a card key or a branch key: both render the same way (`cli._claimed_error`): "{kind} {name} is being driven by run <run-id> (pid <pid> on <host>, heartbeat <n>s ago); wait for it, or `am pause <run-id>`", where `kind` and `name` come from splitting the claim key on its first `:` (so a `branch:` claim reads as "branch <name>"). The code does **not** mention `--branch-prefix` in either message (pinned by `tests/test_cli.py::test_refuse_claimed_names_the_kind_and_the_live_holder`); do not invent that wording. "Use another `--branch-prefix`" may still be stated as advice for a branch conflict, but must not be quoted as part of the error message text.
  - Also mention that `ClaimedError` carries `key` and `run_id` fields.
- **Readers always work and take nothing.** `am status`, `am runs`, `am logs` and `am run --dry-run` take no lease, no claim and no lock, and write nothing. `am status` shows `control.claims` (verified in `cli.py`).
- **`took_over`.** `am resume` of a dead run takes over the lease. A run is dead when its pid is gone on the same host, or its heartbeat is more than 30 s old from another host. The payload then includes `"took_over": {"pid", "host", "heartbeat_at"}`.
- **`LeaseLostError`.** A process that was stuck (SIGSTOP, a suspended laptop) and wakes after its run was taken over stops at its next store write. It writes nothing, not even the journal line, and exits 3 with `type: "LeaseLostError"`. Its lanes are cancelled as on a kill. State what fencing does not cover: a `git` or `brd` call that was already running.
- **Locks.** Board writes and git worktree operations are serialised across processes by flock files at `<data dir>/projects/<digest>.board.lock` and `<digest>.git.lock`. These are keyed like the project database and are never inside the repository. A wait longer than 600 s (`LOCK_TIMEOUT_SECONDS`) fails the phase with `LockTimeoutError` naming the lock file, and the lane escalates. The fix is `am resume` later. A SIGKILLed holder releases its lock at once.
- **Limits.**
  - `--max-concurrent` is per process, so two processes can run up to twice as many lanes.
  - One data directory per machine.
  - Not over NFS (flock).
  - POSIX only (`fcntl`).

  Either fold these into the existing "Limits, stated plainly" list or state them in the new section. Do not duplicate them in both places.

Keep the out-of-scope items out of the README except as limits: queueing or waiting for a claim, a lane cap shared across processes, NFS, git-common-dir keying (a linked worktree given as `--repo-dir` is a different project), Windows, fencing of `git`/`brd` calls, and exactly-once phases (issue `232cbd44`).

### 2. Docstrings (plan Step 3)

Run `grep -rn "P2\|one process\|two .am. processes\|not supported" src`. It currently returns three hits. Update or justify each one:

- `store.py:282`, in `Journal.append`: "One process writes a given run (P2)." **Update it.** Only the process that holds the run's lease writes it. After a takeover the new owner writes, and the old owner's writes are fenced out (multi-process X4). Keep the rest of the docstring (seq caching, lock, fsync semantics) as it is. Make the wording consistent with the `Journal` class docstring around `store.py:216`.
- `prompt.py:74` ("inside one process"): **justify, do not change.** It describes a single OS process, not the one-am-per-repository rule.
- `harness/launcher.py:73` ("one process group"): **justify, do not change.** Same reason.

Re-read the other docstrings the plan names and confirm they are already multi-process-aware (sibling tasks updated them). Edit one only if it still says one process per repository or "unsupported (P2)". The ones to check: `store.BUSY_TIMEOUT_SECONDS` (around line 141), `store.open_db` (around line 154), the `Journal` class (around 216), the `Store` class (around 857), `board.py`'s module docstring, and `steps/worktree.py`'s module docstring and `_repo_lock`. The expected result is no change.

### 3. Superseded pointers

- `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §11 "Concurrency" (line 430). After the existing `**Status (milestone 7):** superseded by …` paragraph (line 438), add a paragraph in the same form. It starts with `**Status (milestone 10):**`, points to `2026-09-27-multi-process-design.md`, and says in one sentence that several `am` processes may now run on one repository, on disjoint cards and branches.
- `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md:251`. Change the bullet "More than one `am` process per repository." by appending "Superseded by `2026-09-27-multi-process-design.md`."
- `docs/superpowers/specs/2026-09-27-live-control-design.md` §8 "Deferred", the bullet "Everything else in issue `2db2a2ef`…" (line 363 on the `docs-live-control` worktree). Add the same pointer. This file is **not present on this branch**. If it is still absent at implementation time, do not create or copy it. Report the pointer as not applied, with the reason, so it can be added where that spec lands.

## Observable behaviour and error paths

No runtime behaviour changes, so there are no new error paths. The only observable results are:

- the README section;
- the reworded `Journal.append` docstring;
- the three pointers.

Failure modes the implementer must avoid:

- documenting a refusal `type` or message that the code does not raise;
- leaving the old "not supported" paragraph in place;
- touching any file under `tests/`.

## Tests

No new tests. Per the multi-process addendum §7 (which refines the base design doc §14 for this milestone), the behaviour the README describes is already pinned by existing tests in the following tiers:

- **Default unmarked suite:** `tests/e2e/test_multi_process.py`, real `am` child processes under the fake `claude`. Sibling `cfcfa6e3` owns it.
- **Unit tests:** `tests/test_locks.py`, `tests/test_store.py`, `tests/test_cli.py`, `tests/test_orchestrate.py`, `tests/test_board.py`, `tests/steps/test_rollup.py`, `tests/steps/test_worktree.py`.

Verification for this subtask:

1. **Doc check (manual, no test tier).** The plan Step 3 grep returns only the two justified false positives, `prompt.py:74` and `harness/launcher.py:73`.
2. **Regression (default suite).** `uv run pytest` is green. The docstring edit must not change behaviour, and the full suite, including the default-tier `tests/e2e/test_multi_process.py`, confirms that.

There is no lint or typecheck command (CLAUDE.md).

Commit message: `docs: several am processes per repository`.
