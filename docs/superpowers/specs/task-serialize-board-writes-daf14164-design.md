# Serialize board writes and rollups, and stress brd itself (card daf14164)

Parent story 19b21273 "Serialize the shared git and board resources". This narrows decision P3 of `docs/superpowers/specs/2026-09-24-parallel-stories-design.md` (lines 58-65) to its board half. The git half (the per-repo `git worktree add` lock in `steps/worktree.py`) was delivered by sibling 88150552 and is out of bounds here: do not touch `steps/worktree.py` or its tests, and add no `git worktree prune`.

## Scope

1. `src/agent_manager/board.py` gets one module-level reentrant lock, `WRITE_LOCK = threading.RLock()`. It is public because `steps/rollup.py` has to take it too. `board.set_status` holds it for its whole body (`_run`, `_decode`, `_validated`). `show`, `tree` and `roots` stay unlocked.
2. `src/agent_manager/steps/rollup.py`: `rollup.set_status` holds `board.WRITE_LOCK` around its whole body. That covers writing the card, then every iteration of the ancestor walk (`board.show`, `board.tree`, `rollup_status`, the conditional `board.set_status`). The whole thing is one critical section, because a rollup reads, then modifies, then writes. The lock has to be an `RLock` because the nested `board.set_status` calls take it again on the same thread. These stay as they are: the first parameter named `card` (the engine binds by name), the return shape `{"card", "status", "rolled_up"}`, `board.BoardError` left uncaught, and the `MAX_ANCESTRY_DEPTH = 16` behaviour.
3. Retry only if needed. If the brd stress test below shows that `brd update` fails when called concurrently, `board._run` gets a bounded retry: a few attempts with a short backoff, and it retries only when brd reports its lock error. Before writing the code, check what brd actually prints when this happens. It could be an `{"ok": false, "error": {...}}` envelope on stdout, or a non-zero exit with only stderr. The retry must recognise the shape brd really uses. Every other failure is raised on the first attempt, exactly as it is today. When the attempts run out, the last failure surfaces the same way it would have without the retry: as `BoardError` from `_run`, or through `_decode`. If brd does not fail under the stress test, `_run` is left unchanged.
4. Module docstrings stay truthful. In `board.py`, `set_status` is still the entire write surface, nothing about a run is written to the board, and brd is only ever invoked with argv lists, never through a shell. Add one sentence about the write lock, plus a note on the retry if one was added. In `rollup.py`, add a sentence saying the walk runs under the board lock.

Only threads sharing one process are supported. Two `am` processes on the same repository are not supported, and this card adds no cross-process locking such as file locks.

## Observable behaviour

- With `--max-concurrent 1`, the sequential path behaves exactly as before: the same writes happen in the same order and the results are unchanged.
- Concurrent `rollup.set_status` calls on sibling subtasks can no longer lose a parent update. The last walk to run sees every child's final status and writes the correct parent and milestone status.
- Only a lock-type failure changes. A brd lock error that clears within the retry budget is no longer a `BoardError`. That applies only if the retry was needed.

## Error paths

- A `BoardError` raised anywhere inside the rollup critical section propagates. Because the lock is released through a `with` block, a failure cannot leave it held.
- The depth guard still raises `BoardError("exceeded maximum ancestry depth")` inside the lock, and the lock is released afterwards.
- Retry exhaustion (if retry exists) raises the final attempt's error unchanged. Non-lock failures such as a missing `brd`, card not found, or an invalid status are never retried.

## Tests

Tier rule, from design spec section 14 (line ~488): steps and the board adapter are tested against a real temporary `brd` board over subprocess, with brd never mocked. Narrow pure checks go at the bottom of `tests/test_board.py`. Nothing here belongs in `tests/e2e`.

- `tests/test_board.py`, `test_brd_update_survives_concurrent_writers` (board adapter tier, real temp brd, `requires_brd`): a temp board with 8 cards. 8 threads each call `subprocess.run(["brd", "update", id, "--status", s], cwd=board_root)` directly on their own card, bypassing `board.py` and its lock, 25 writes each, cycling through `todo`/`in_progress`/`done` (never `blocked`). Every call must return code 0 with an `ok: true` envelope, and afterwards each card's `brd show` status must equal the last status written to it. The test is not weakened if brd flakes. It is the evidence for whether the retry is needed. Set `XDG_DATA_HOME` to a tmp dir, the same way `temp_board` in `tests/steps/test_rollup.py` does, so subprocesses inherit it.
- Only if the retry is added: `tests/test_board.py`, `test_run_retries_brd_lock_error_then_succeeds` and `test_run_gives_up_after_bounded_lock_retries` (narrow checks at the bottom, using the existing fake-brd-script pattern at lines 83-100). The fake `brd` counts its attempts in a file and prints brd's real lock-error output N times before succeeding. The tests assert that the call succeeds within budget, that the attempt count is right, and that the error surfaces after the budget. A third check, `test_run_does_not_retry_non_lock_failure`, asserts that a fake brd failing with some other error is invoked exactly once.
- `tests/steps/test_rollup.py`, `test_concurrent_rollups_reach_done` (steps tier, real temp brd, `requires_brd`, reuses `temp_board`, `_add_card` and `_brd_json`): one milestone with 2 stories and 4 subtasks under each. 8 threads each call `rollup.set_status(subtask, "done", repo_dir=root)`, gated by a barrier so they start together. Afterwards `_brd_json` shows both stories and the milestone as `done`, and every thread returned without raising. Loop 20 iterations, each with fresh cards or with the cards reset to `todo`.
- All existing tests in `tests/steps/test_rollup.py` (lines 72-270) and `tests/test_board.py` stay green unchanged, and so does the whole default suite, including `tests/e2e`.

## Verification

`uv run pytest`. There is no typecheck or lint step.

## Final report must state

What `brd update` did under the 8x25 concurrent stress test: whether any call failed, and if so the exact failure output. Also whether the bounded retry in `board._run` was added and, if it was, the attempt count and backoff it uses.
