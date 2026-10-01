<!-- task-pipeline: validated -->
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

---

# Document Several am Processes Per Repository Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the README's "one `am` process per repository" rule with an accurate "Several am processes" section written from the milestone-10 code, reword the one stale `Journal.append` docstring, and add superseded pointers to the older specs.

**Architecture:** Documentation only. No runtime code changes and nothing under `tests/` changes. Every statement in the README is copied from the code that raises or produces it (`cli.py`, `store.py`, `locks.py`, `control.py`, `orchestrate.py`, `paths.py`), with the source line cited in each step so a reviewer can check it. Because there is no behaviour to test, each task's RED step is a `grep` that shows the old text is present (or the new text absent), and its GREEN step is the same `grep` showing the reverse; the full `uv run pytest` suite is the regression guard.

**Tech Stack:** Markdown, Python docstrings, `grep`, `uv run pytest`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m10/task-document-several-am-b9c16de5/docs/superpowers/specs/task-document-several-am-b9c16de5-design.md` (prepended above). Background design: `docs/superpowers/specs/2026-09-27-multi-process-design.md` on the `docs-multi-process` worktree (`/home/paulomtts/Code/agent-manager/.claude/worktrees/docs-multi-process/docs/superpowers/specs/2026-09-27-multi-process-design.md`), §3 X1–X11.

All paths below are relative to the worktree root `/home/paulomtts/Code/agent-manager/.claude/worktrees/m10/task-document-several-am-b9c16de5`. Run every command from there.

## Global Constraints

- Docs only: change `README.md`, the `Journal.append` docstring in `src/agent_manager/store.py`, `docs/superpowers/specs/2026-09-23-agent-manager-design.md` and `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md`. Nothing else.
- No file under `tests/` is added, changed or deleted (sibling `cfcfa6e3` owns them).
- If the code differs from spec §3/X11, the code wins. In particular the X11 table's branch-claim message ("… use another --branch-prefix") and `LeaseLostError` message ("… was taken over by …") are NOT what the code says; use the code's wording given in Task 2.
- The `ClaimedError` message never mentions `--branch-prefix`; "use another `--branch-prefix`" may appear only as advice outside the quoted message.
- Do not create `docs/superpowers/specs/2026-09-27-live-control-design.md` on this branch; report its pointer as not applied.
- Do not add queueing, a cross-process lane cap, NFS support, git-common-dir keying, Windows, git/brd fencing or exactly-once phases to the README except as limits.
- Prose in the new README text is not hard-wrapped (one paragraph per line), matching the recent README sections.
- Verification: `uv run pytest`. There is no lint or typecheck command.
- Final commit message: `docs: several am processes per repository`.

## Review Focus

- A quoted message that drifts from the code (for example "600 s" where `LockTimeoutError` prints `600.0s`, or `run R` where `LeaseLostError` prints `run 'R'` with quotes) — Task 2 Step 4 greps the README for each code-exact fragment.
- A claim that `ClaimedError`'s `key`/`run_id` appear in the JSON envelope: `cli.error_envelope` (cli.py:221-224) emits only `type` and `message`. Task 2 Step 4 checks the README says the envelope has only those two.
- The old "not supported" paragraph surviving, or the four new limits appearing both in the new section and the existing list — Task 2 Steps 1 and 4 count them.
- A broken in-page link: the new heading's anchor is `#several-am-processes`, and README line 45 says "See [Parallel runs](#parallel-runs) for … the limits", which moves under the new heading — Task 2 updates line 45 and Step 4 greps the anchor.
- `am status`'s `control` shape in README line 336 omitting the `claims` key that `cli.control_view` now always emits (cli.py:302-350) — Task 2 updates it and Step 4 greps it.

---

### Task 1: Reword the `Journal.append` docstring and confirm the other docstrings

**Files:**
- Modify: `src/agent_manager/store.py:280-290` (the `Journal.append` docstring only)
- Read only (confirm, do not edit): `src/agent_manager/store.py:137-159` (`BUSY_TIMEOUT_SECONDS`, `open_db`), `src/agent_manager/store.py:213-221` (`Journal`), `src/agent_manager/store.py:847-861` (`Store`), `src/agent_manager/board.py:1-23`, `src/agent_manager/steps/worktree.py:1-23` and `:180-188` (`_repo_lock`), `src/agent_manager/prompt.py:74`, `src/agent_manager/harness/launcher.py:73`
- Test: none added; regression via `tests/test_store.py` (unit tier) and the full suite in Task 4.

**Interfaces:**
- Consumes: nothing.
- Produces: no code interface. After this task the Step-3 grep returns exactly two lines (`harness/launcher.py:73`, `prompt.py:74`), which Task 4 re-checks.

- [ ] **Step 1: Run the grep to see the stale hit (RED)**

Run: `grep -rn "P2\|one process\|two .am. processes\|not supported" src`

Expected: exactly three lines:

```
src/agent_manager/store.py:282:        One process writes a given run (P2). The sequence number is cached when
src/agent_manager/harness/launcher.py:73:    child in its own session, so the whole tree is one process group.
src/agent_manager/prompt.py:74:    inside one process, and written to disk as plain text.
```

(Order may differ.) The `store.py:282` line is the one to fix.

- [ ] **Step 2: Replace the first paragraph of the docstring body**

In `src/agent_manager/store.py`, replace this exact text:

```python
        One process writes a given run (P2). The sequence number is cached when
        the journal is opened, not re-read from disk, and the lock is held from
        numbering the line until it is fsynced, so the threads of that process
        never share a number or interleave their bytes. The cached number
```

with:

```python
        Only the process holding the run's lease writes it: after a take-over
        the new owner writes, and the old owner's writes are fenced out by the
        lease token (multi-process X4). The sequence number is cached when the
        journal is opened (and re-read by `reseek` on a take-over), not re-read
        from disk on each append, and the lock is held from numbering the line
        until it is fsynced, so the threads of that process never share a
        number or interleave their bytes. The cached number
```

Leave every other line of the docstring and the method body unchanged (the sentences from "advances once the line has been written…" to "The lock is released either way." stay as they are).

- [ ] **Step 3: Run the grep again (GREEN)**

Run: `grep -rn "P2\|one process\|two .am. processes\|not supported" src`

Expected: exactly two lines, `src/agent_manager/harness/launcher.py:73` ("one process group") and `src/agent_manager/prompt.py:74` ("inside one process"). Both describe a single OS process (a process group the launcher kills as one; a prompt held in one process's memory), not the one-am-per-repository rule, so they stay unchanged. Record that justification in the task report.

- [ ] **Step 4: Confirm the other named docstrings are already multi-process-aware**

Run: `grep -n "multi-process\|X4\|X7\|X9\|lease" src/agent_manager/store.py | sed -n '1,40p'` and read `src/agent_manager/store.py:137-159`, `:213-221`, `:847-861`; `src/agent_manager/board.py:9-14`; `src/agent_manager/steps/worktree.py:16-22` and `:180-188`.

Expected (already true on this branch, verified while writing this plan):
- `BUSY_TIMEOUT_SECONDS` (store.py:137-144) mentions "a second `am` process's short `BEGIN IMMEDIATE` write transactions … (multi-process X4, X9)" and no longer says "unsupported (P2)".
- `open_db` (store.py:147-159) says "two processes never write one run, because every run write is fenced by the lease token (multi-process X4)".
- `Journal` (store.py:213-221) says "The threads of the process that holds a run's lease share one `Journal`" and describes `reseek`.
- `Store` (store.py:857) says "The threads of the process holding a run's lease share one `Store`".
- `board.py:9-14` says writes are "serialized across threads and across `am` processes on one project (spec X7 …)".
- `steps/worktree.py:16-22` and `_repo_lock` (180-188) mention "separate `am` processes" and "Other `am` processes on the repository are coordinated by `git_lock`'s flock (spec X7)".

Make no edit to any of them. If one of them does say "one process per repository" or "unsupported (P2)", stop and report it rather than editing outside this plan.

- [ ] **Step 5: Run the store unit tests**

Run: `uv run pytest tests/test_store.py -q`
Expected: PASS (a docstring change cannot alter behaviour; this proves the file still imports).

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/store.py
git commit -m "docs: several am processes per repository (Journal.append docstring)"
```

---

### Task 2: README "Several am processes" section

**Files:**
- Modify: `README.md:45` (link sentence), `README.md:185-197` (replace the old paragraph with the new section and extend the "Limits, stated plainly" list), `README.md:336` (`control` shape)
- Test: none added; Step 4 is a grep check against the code-exact fragments.

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: the anchor `#several-am-processes`, used by README line 45 and line 336 in this same task.

Source facts this section is written from (all verified on this branch):
- Envelope: `cli.error_envelope` (cli.py:221-224) → `{"ok": false, "error": {"type": <class name>, "message": str(error)}}`; HANDLED errors exit 3 (cli.py:1175-1182, 1362-1364).
- `RunIsLiveError` message, `cli._run_is_live_error` (cli.py:827-833): `run {run_id} is still running in pid {pid} on {host} (heartbeat {n}s ago); wait for it to exit, or `am status {run_id}``.
- `ClaimedError` message, `cli._claimed_error` (cli.py:836-847): `{kind} {name} is being driven by run {run_id} (pid {pid} on {host}, heartbeat {n}s ago); wait for it, or `am pause {run_id}``, kind/name from `key.partition(":")`. `ClaimedError` has `.key` and `.run_id` attributes (cli.py:168-179) that are not in the envelope.
- Claims: `run_card` claims `[card:<card id>]` (cli.py:934); task-run resume claims `[card:<subtask id>]` (cli.py:1578); `orchestrate.milestone_claims` (orchestrate.py:1299-1316) claims `card:<milestone>`, `card:<id>` of each remaining subtask (done subtasks and closed stories add none), `branch:<prefix>-integrate`. Preflight `cli.refuse_claimed` runs before `refresh_git` and `Store.open` (orchestrate.py:1423-1434, cli.py:850-872).
- Liveness: `control.lease_is_live` (control.py:65-76), `LEASE_STALE_SECONDS = 30.0`, `HEARTBEAT_SECONDS = 5.0`.
- `took_over`: `{"pid", "host", "heartbeat_at"}` (cli.py:1655-1661; orchestrate.py:1516-1521, "in every payload").
- `LeaseLostError` message (store.py:687-703): `this process lost the lease of run '{run_id}': pid {pid} on {host} holds it now`, or `…: no process holds it now`. It is a `BaseException`; in HANDLED (cli.py:1181).
- Locks: `paths.project_lock_path` (paths.py:37-44) → `<data dir>/projects/<sha256 of resolved repo path>.<name>.lock`; data dir is `$XDG_DATA_HOME/agent-manager` or `~/.local/share/agent-manager` (paths.py:14-19). Names `board` (board.py:61) and `git` (worktree.py:207). `LOCK_TIMEOUT_SECONDS = 600.0`; message `timed out after 600.0s waiting for the lock {path}` (locks.py:27, 41). A deterministic phase that raises is recorded failed with detail `{Type}: {message}` (runtime/walk.py:405-420). At run start `refresh_git` (orchestrate.py:530-547) raises it to HANDLED → exit 3.
- Readers: `status_for` (cli.py:1379-1423) opens the db read-only-in-effect and adds `claims` only from a live lease.

- [ ] **Step 1: Check the old text is present and the new section absent (RED)**

Run: `grep -n "are not supported\|Several am processes\|\"claims\"" README.md`

Expected: exactly one line, `186:or on the same run are not supported.` No "Several am processes" heading and no `"claims"` yet.

- [ ] **Step 2: Replace the old paragraph and extend the Limits list**

In `README.md`, replace this exact text (lines 185-197):

````markdown
Run one `am` process per repository. Two `am` processes on the same repository
or on the same run are not supported.

Limits, stated plainly:

- **Your test suite runs side by side.** Each lane's `verify` phase runs your
  `--verify` commands in its own worktree while other lanes do the same. Tests
  that use a fixed port, a shared file or a shared database will collide. Run
  such a repository with `--max-concurrent 1`.
- **One environment per lane.** `uv run pytest` builds
  a `.venv` in each worktree, which takes time and disk once per lane.
- **Machine load.** N lanes means up to N `claude -p` processes at once, and
  nothing rate-limits them.
````

with:

````markdown
#### Several am processes

Several `am` processes may run on one repository at once, from different terminals, as long as their runs drive different cards and different branches. Each run claims, at its start, every card and branch it may drive, and holds those claims for as long as it holds its lease. A second process that needs any of them is refused before it writes anything. Nothing is queued: a refused command does not wait, so run it again once the other run has finished, or pause that run first.

What may run beside a live run:

| Beside a live … | `am run --milestone M2` | `am run --card X` | `am resume R` | `am status` / `am runs` / `am logs` / `am run --dry-run` |
|---|---|---|---|---|
| milestone run of `M1` | allowed when `M2` is not `M1` and the two `--branch-prefix` values differ | allowed unless `X` is a remaining subtask of `M1` | allowed unless `R` is that run or claims a key it holds | always |
| `--card` run of `Y` | allowed unless `Y` is a remaining subtask of `M2` | allowed when `X` is not `Y` | allowed unless `R` is that run or claims `Y` | always |

A claim is a key, `card:<card id>` or `branch:<branch name>`:

| Command | Claims |
|---|---|
| `am run --card X` | `card:X` |
| `am resume R` of a `--card` run | `card:<its one resumable subtask>` |
| `am run --milestone M` | `card:M`, `card:<id>` of every remaining subtask, and `branch:<prefix>-integrate` |
| `am resume R` of a milestone run | the same set, worked out again from the board with the run's recorded `--branch-prefix` |

A subtask already `done` on the board, and every subtask of a closed story, adds no key. `card:M` keeps two runs of one milestone apart whatever their prefixes, the subtask keys keep a `--card` run and a milestone run apart on a shared card in either order, and `branch:<prefix>-integrate` keeps two milestones with one `--branch-prefix` apart. A claim lives only as long as its run's lease: when the process dies, its claims die with it, and a later run takes them over.

Refusals. Each one prints `{"ok": false, "error": {"type", "message"}}`, exits 3, and comes before any write. A refused `am run --card` or `am run --milestone` leaves no run directory and does no `git fetch` and no `git worktree prune`.

- `RunIsLiveError`: `am resume` of a run whose lease another process holds and is live, and the loser when two `am resume` race to take over the same dead run (exactly one wins). The message reads ``run <run-id> is still running in pid <pid> on <host> (heartbeat <n>s ago); wait for it to exit, or `am status <run-id>` ``.
- `ClaimedError`: a card or a branch this run needs is claimed by another run whose lease is live. Both kinds read the same way, with the key's kind and name: ``card <card id> is being driven by run <run-id> (pid <pid> on <host>, heartbeat <n>s ago); wait for it, or `am pause <run-id>` ``, or ``branch <prefix>-integrate is being driven by run <run-id> (…); wait for it, or `am pause <run-id>` ``. A branch refusal means another milestone run uses the same `--branch-prefix`; picking another prefix avoids it. The JSON envelope has only `type` and `message`, and the message names both the key and the holding run; the `ClaimedError` exception itself also carries them as its `key` and `run_id` attributes.

Readers always work and take nothing. `am status`, `am runs`, `am logs` and `am run --dry-run` take no lease, no claim and no lock, and never write, so they work while any number of runs are going. `am status <run-id>` shows the keys the run's live lease holds in `control.claims`.

`took_over`. `am resume` of a run whose process is dead takes its lease over. A lease is dead when its pid no longer exists on the same host, or when its heartbeat is more than 30 seconds old; from another host, the heartbeat is the only test. The resumed report then has `"took_over": {"pid", "host", "heartbeat_at"}`, naming the dead holder. On a milestone run it is on every report shape.

`LeaseLostError`. A process that was stuck rather than dead (stopped with SIGSTOP, or on a laptop that was suspended) may wake after another process has taken its run over. Every write of a run checks that this process still holds the lease, so the stuck process stops at its next store write: it writes nothing, not even the journal line, its lanes are cancelled as on a kill, and it exits 3 with `type: "LeaseLostError"` and the message `this process lost the lease of run '<run-id>': pid <pid> on <host> holds it now`. The new owner's rows are untouched. What this does not cover: a `git` or `brd` call the stuck process had already started finishes on its own. Such a call is bounded by one phase.

Locks. Board writes (a card status and its rollup) and git worktree operations (`git worktree add`, and a run's starting `git fetch` and `git worktree prune`) are serialised across processes by two lock files, `<data dir>/projects/<digest>.board.lock` and `<data dir>/projects/<digest>.git.lock`. `<data dir>` is `$XDG_DATA_HOME/agent-manager`, or `~/.local/share/agent-manager`, and `<digest>` is the same per-repository digest as the project database `<digest>.db` beside them, so the lock files are never inside the repository or a worktree. A process waits at most 600 seconds for one. Past that, the wait fails with `LockTimeoutError` and the message `timed out after 600.0s waiting for the lock <path>`. Inside a phase, the phase fails and the subtask escalates with that as its `detail`, prefixed `LockTimeoutError: `; run `am resume <run-id>` once the other process has let go. While a run is starting, the command instead exits 3 with `type: "LockTimeoutError"`. A holder that is killed, even with SIGKILL, releases its lock at once.

Limits, stated plainly:

- **Your test suite runs side by side.** Each lane's `verify` phase runs your
  `--verify` commands in its own worktree while other lanes do the same. Tests
  that use a fixed port, a shared file or a shared database will collide. Run
  such a repository with `--max-concurrent 1`.
- **One environment per lane.** `uv run pytest` builds
  a `.venv` in each worktree, which takes time and disk once per lane.
- **Machine load.** N lanes means up to N `claude -p` processes at once, and
  nothing rate-limits them.
- **`--max-concurrent` is per process.** Two `am` processes with `--max-concurrent 4` each can run 8 lanes, and 8 `claude -p` processes, at once. Nothing caps lanes across processes.
- **One data directory per machine.** Leases, claims and lock files live under the data directory. Two processes that see different data directories, for example through a different `XDG_DATA_HOME`, do not see each other's runs and are not kept apart.
- **Not over NFS.** The locks are `flock`s, which are not reliable on a network filesystem. Keep the data directory on a local disk.
- **POSIX only.** The locks use `fcntl`, so `am` does not run on Windows.
- **A repository is known by its resolved path.** A linked worktree of the repository given as `--repo-dir` is a different project, with its own database, leases and locks, so a run there is not kept apart from a run on the main checkout.
````

- [ ] **Step 3: Fix the two references that now point at the moved content**

In `README.md` line 45, replace this exact text:

```markdown
See [Parallel runs](#parallel-runs) for what runs together, how a run stops, and the limits, and [Multiple blockers](#multiple-blockers) for a story with two or more blockers.
```

with:

```markdown
See [Parallel runs](#parallel-runs) for what runs together and how a run stops, [Several am processes](#several-am-processes) for running more than one `am` at once and for the limits, and [Multiple blockers](#multiple-blockers) for a story with two or more blockers.
```

In `README.md` (was line 336, now further down), replace this exact text:

```markdown
`am status <run-id>` always has a `control` key: `{"lease": {"pid", "host", "acquired_at", "heartbeat_at", "accepting", "live"} or null, "requests": [{"command", "requested_at", "handled_at"}]}`.
```

with:

```markdown
`am status <run-id>` always has a `control` key: `{"lease": {"pid", "host", "acquired_at", "heartbeat_at", "accepting", "live"} or null, "requests": [{"command", "requested_at", "handled_at"}], "claims": ["card:<id>", "branch:<name>", ...]}`. `claims` lists the keys the run's lease holds while it is live, and is empty otherwise (see [Several am processes](#several-am-processes)).
```

Leave the rest of that paragraph (from "`requests` lists the requests from every life of the run…") unchanged.

- [ ] **Step 4: Check the README against the code (GREEN)**

Run each and compare with the expected result:

- `grep -c "are not supported" README.md` → `0`
- `grep -c "^#### Several am processes$" README.md` → `1`
- `grep -c "(#several-am-processes)" README.md` → `2`
- `grep -c "^Limits, stated plainly:$" README.md` → `2` (the Integrate list and this one; no third list was added)
- `grep -c "per process\.\*\*" README.md` → `1` and `grep -c "Not over NFS" README.md` → `1` and `grep -c "POSIX only" README.md` → `1` (no limit stated twice)
- `grep -c "is still running in pid <pid> on <host> (heartbeat <n>s ago); wait for it to exit" README.md` → at least `1` (matches cli.py:830-832)
- `grep -c "is being driven by run <run-id> (pid <pid> on <host>, heartbeat <n>s ago); wait for it, or" README.md` → at least `1` (matches cli.py:841-844)
- `grep -n "branch-prefix" README.md | grep -c "is being driven by"` → `0` (the quoted message never names `--branch-prefix`)
- `grep -c "this process lost the lease of run '<run-id>': pid <pid> on <host> holds it now" README.md` → `1` (matches store.py:696-701)
- `grep -c "timed out after 600.0s waiting for the lock <path>" README.md` → `1` (matches locks.py:41)
- `grep -c "The JSON envelope has only \`type\` and \`message\`" README.md` → `1`
- `grep -c '"took_over": {"pid", "host", "heartbeat_at"}' README.md` → `1`
- `grep -c '"claims": \["card:<id>"' README.md` → `1`

If any count differs, fix the README text (not the code) and re-run.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: several am processes per repository (README)"
```

---

### Task 3: Superseded pointers in the older specs

**Files:**
- Modify: `docs/superpowers/specs/2026-09-23-agent-manager-design.md:438` (insert after)
- Modify: `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md:251`
- Not applied: `docs/superpowers/specs/2026-09-27-live-control-design.md` (absent on this branch)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing other tasks read.

- [ ] **Step 1: Check the pointers are absent (RED)**

Run: `grep -n "milestone 10\|multi-process-design" docs/superpowers/specs/2026-09-23-agent-manager-design.md docs/superpowers/specs/2026-09-25-supervisor-tree-design.md; ls docs/superpowers/specs/2026-09-27-live-control-design.md`

Expected: no grep output; `ls` reports "No such file or directory".

- [ ] **Step 2: Add the milestone-10 status to the base design §11**

In `docs/superpowers/specs/2026-09-23-agent-manager-design.md`, replace this exact text (line 438 plus the blank line after it):

```markdown
**Status (milestone 7):** superseded by the supervisor-tree addendum, `2026-09-25-supervisor-tree-design.md`: levels are no longer barriers (a story starts once its own blockers finish), a story with two or more blockers roots on a merged base, and `am resume` continues a milestone run.

```

with:

```markdown
**Status (milestone 7):** superseded by the supervisor-tree addendum, `2026-09-25-supervisor-tree-design.md`: levels are no longer barriers (a story starts once its own blockers finish), a story with two or more blockers roots on a merged base, and `am resume` continues a milestone run.

**Status (milestone 10):** superseded by the multi-process addendum, `2026-09-27-multi-process-design.md`: several `am` processes may now run on one repository at once, on disjoint cards and branches, each run holding a lease and claims that fence its writes, with board and git operations serialised by process-wide locks.

```

- [ ] **Step 3: Add the pointer to the supervisor-tree deferred list**

In `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md`, replace this exact line:

```markdown
- More than one `am` process per repository.
```

with:

```markdown
- More than one `am` process per repository. Superseded by `2026-09-27-multi-process-design.md`.
```

- [ ] **Step 4: Check the pointers (GREEN) and the live-control file is still absent**

Run: `grep -n "Status (milestone 10)" docs/superpowers/specs/2026-09-23-agent-manager-design.md; grep -n "Superseded by \`2026-09-27-multi-process-design.md\`" docs/superpowers/specs/2026-09-25-supervisor-tree-design.md; ls docs/superpowers/specs/2026-09-27-live-control-design.md`

Expected: one line from each grep (`439:` and `251:`); `ls` still reports "No such file or directory". Do not create that file. In the task report, state: "Pointer for `2026-09-27-live-control-design.md` §8 Deferred (bullet 'Everything else in issue `2db2a2ef`…', line 363 on the `docs-live-control` worktree) not applied: the file is not on this branch; add `Superseded by `2026-09-27-multi-process-design.md`.` to that bullet where the spec lands."

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-09-23-agent-manager-design.md docs/superpowers/specs/2026-09-25-supervisor-tree-design.md
git commit -m "docs: several am processes per repository (superseded pointers)"
```

---

### Task 4: Full verification

**Files:**
- None modified.

**Interfaces:**
- Consumes: the results of Tasks 1-3.
- Produces: the verified branch.

- [ ] **Step 1: Re-run the docstring grep**

Run: `grep -rn "P2\|one process\|two .am. processes\|not supported" src`
Expected: exactly the two justified lines, `src/agent_manager/harness/launcher.py:73` and `src/agent_manager/prompt.py:74`.

- [ ] **Step 2: Confirm nothing under `tests/` and no runtime code changed**

Run: `git diff --stat origin/m10/task-prove-several-am-cfcfa6e3...HEAD`
Expected: only `README.md`, `src/agent_manager/store.py`, `docs/superpowers/specs/2026-09-23-agent-manager-design.md`, `docs/superpowers/specs/2026-09-25-supervisor-tree-design.md`, plus this plan and its spec under `docs/superpowers/`. No path under `tests/`.

Run: `git diff origin/m10/task-prove-several-am-cfcfa6e3...HEAD -- src | grep '^[+-] ' | grep -v '^[+-] \{8\}'`
Expected: no output (every changed `src` line is an indented docstring line inside `Journal.append`).

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest`
Expected: PASS, including the default-tier `tests/e2e/test_multi_process.py` and the unit tiers `tests/test_locks.py`, `tests/test_store.py`, `tests/test_cli.py`, `tests/test_orchestrate.py`, `tests/test_board.py`, `tests/steps/test_rollup.py`, `tests/steps/test_worktree.py`, which pin the behaviour the README describes.

- [ ] **Step 4: Final commit**

If Tasks 1-3 were committed separately, there is nothing left to stage; otherwise commit the remaining docs changes with the spec's message:

```bash
git add README.md src/agent_manager/store.py docs/superpowers/specs/2026-09-23-agent-manager-design.md docs/superpowers/specs/2026-09-25-supervisor-tree-design.md docs/superpowers/plans/task-document-several-am-b9c16de5.md docs/superpowers/specs/task-document-several-am-b9c16de5-design.md
git commit -m "docs: several am processes per repository"
```
