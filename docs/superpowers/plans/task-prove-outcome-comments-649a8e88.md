<!-- task-pipeline: validated -->
# Prove outcome comments end to end on a real board (card 649a8e88)

Parent story: 0f6ab456 "Proof and documentation" (milestone 21f4cf06). Plan task: `docs/superpowers/plans/2026-09-29-board-comments.md` Task 3.1. Design: `docs/superpowers/specs/2026-09-29-board-comments-design.md` (B2, B5, B7, B8, B9; §6 Testing).

## Scope

Add one new test module, `tests/e2e/test_board_comments.py`, that proves under the fake `claude` against a real temporary `brd` board that the board-comments outbox already built by sibling 77b0a064 (`comments.py` compose/enqueue/flush, wiring in `orchestrate.py`, `bases.py`, `cli.py`) puts the right comments on the right cards. This card adds tests and test scaffolding only.

In scope:
- `tests/e2e/test_board_comments.py` (new), with four scenarios (below).
- A `brd` failure shim, as a fixture in the new test module, or in `tests/e2e/conftest.py` if more than one module would use it. `tests/e2e/fake_claude.py` changes only if a fake-claude switch turns out to be needed. The plan allows this and nothing else needs it today.
- Commit `test(e2e): outcome comments on a real board` on an `m12` branch.

Out of scope (sibling-owned, do not touch):
- Any change to `src/agent_manager/comments.py`, `board.py`, `store.py`, `orchestrate.py`, `bases.py`, `cli.py`, or the unit and orchestrate-tier tests in `tests/test_comments.py`, `tests/test_orchestrate.py`, `tests/test_bases.py`, `tests/test_cli.py`, `tests/test_board.py`, `tests/test_store.py` (77b0a064). If an e2e scenario exposes a product bug, report it. Do not fix it here.
- README "What the board records" and the D5 amendment to the main design spec (3bf6ad5b).

## Fixtures and scaffolding

- Board and repo: use the real git+brd project the e2e tier already builds (`_init_project` in `tests/e2e/conftest.py`). The plan names the module-scoped `project` fixture. A milestone run moves every card it touches, so each scenario needs a board of its own. Use the function-scoped `fresh_project` / `milestone_board` (built from the same `_init_project`) or a smaller per-test card tree made with `_add_card`. Do not share one board across scenarios.
- Harness: `fake_claude_bin` (the `claude` PATH shim). Drive runs through the CLI exactly as the sibling e2e modules do: `run_milestone_cli`, `CliRunner` for `am resume`/`am cancel`, `review_fail_marker` to force a review escalation, and the `hold` / `am` / `spawn_am` helpers that `test_live_control.py` and `test_multi_process.py` use to hold a phase and cancel from another process. Never pass a `runner_factory`.
- Reading comments: read through the real `brd comment list <card>` (via `board.comment_list` or a subprocess call to `brd`). The test checks what the real board stores, not the store's outbox.
- `brd` failure shim: `board.py` invokes `BRD = "brd"` from PATH. Put a `brd` executable ahead of the real one on PATH. It resolves the real `brd` path when the fixture is created and passes every call straight through. When a scaffolding env var is set (for example `AM_E2E_BRD_FAIL_COMMENTS=1`), it exits non-zero on `brd comment ...` subcommands only. Card reads and status writes must keep working so the run can proceed. Only comment posting/listing is "the board being down", and that is the only board path B8 makes best-effort. Set the env var through function-scoped `monkeypatch` so child processes inherit it and it is undone after the test.
- Isolation: all data stays under the per-test `XDG_DATA_HOME` the fixtures set. Tests never touch the real data directory (tests/conftest.py guard).

## Observable behavior to assert

Every assertion about a comment checks these things:
- author `am`
- first line `am · <outcome> · run <run-id>`
- last line `am-key: <key>`, where `<key>` is `comments.key(run_id, card_id, event)`
- body length ≤ `comments.CAP`

Event names and token scoping follow B5 as `comments.py` implements them. Build expected keys from `comments.key` and the store's lease token. Do not retype key formats.

1. Clean milestone run. `am run --milestone` ends done. On every subtask card, `brd comment list` shows exactly one `am · done` comment, ending in that subtask's done key. The milestone card has exactly one run-end comment. Story cards have no comments, except a `base-failed` comment (the event name `comments.key` uses), which a clean run never produces.
2. Escalation, fix, `am resume`. With `review_fail_marker` armed for one subtask's branch, the run escalates. That subtask's card gets one escalation comment. Its body quotes only the `unresolved_blockers` text, and any `[[` in it appears as `[ [`. The milestone card gets one escalated run-end. Parked siblings get no comment. After removing the marker, `am resume <run-id>` finishes. On the escalated card, the escalation comment is still there (never deleted, B2), followed by exactly one `done (resumed at …)` comment (never a second done, B9). The milestone card ends with the first life's escalated run-end and a second, distinct run-end for the resumed life (lease-token-scoped keys, B5). Every `am-key` on every card is unique.
3. Cancel (M9). Hold a subtask mid-phase and `am cancel` the run from another process. The run records `cancelled`. Each subtask that the cancel left `in_progress` gets exactly one cancelled comment. Subtasks not yet started and subtasks already done get no cancelled comment. The milestone card gets one cancelled run-end.
4. Board down for the whole run (Review Focus 4, B8). With the shim failing `brd comment`, `am run --milestone` still exits 0 and ends `done`. The JSON payload carries warnings naming the unposted comment keys. No card status differs from scenario 1's clean run, and no card has a comment. Then clear the env var and launch the next run: a relaunch `am run --milestone` on the same milestone (its start-of-run flush over `milestone_card_ids`, orchestrate.py ~1599), or `am resume` if that is the entry point that reaches a flush for a done run. The previously pending comments now appear, one per key, with exactly the scenario 1 shape. Running the same flush entry point a second time adds nothing (B7/B9 idempotence through the `am-key` check). Each row fails exactly once during the down run (one in-lane flush per card), so nothing reaches the 3-attempt abandonment.

## Error paths covered

- Board failure during a run produces warnings only. The run's status, exit code, and card statuses are unaffected (B8, scenario 4).
- Comments that went unposted because the board was down are delivered by the next life (B7, scenario 4).
- Replay and resume never double-post a key (B9, scenarios 2 and 4).
- Agent text reaches the board only through the named failure field, quoted and `[[`-escaped (scenario 2).

## Test list

All four tests go in the e2e tier, `tests/e2e/test_board_comments.py`. The placement rule is design spec §14 "Testing" (`docs/superpowers/specs/2026-09-23-agent-manager-design.md`): anything that drives a harness against a real `brd` board end to end goes in `tests/e2e/`. Board-comments spec §6 names exactly these e2e cases. The unit and orchestrate tiers for compose, enqueue, and flush are already covered by 77b0a064 and are not repeated here.

| Test | Tier |
|---|---|
| `test_clean_milestone_run_posts_one_done_per_subtask_and_one_run_end` | e2e (`tests/e2e/`) |
| `test_escalation_then_resume_keeps_escalation_and_appends_resumed_done` | e2e (`tests/e2e/`) |
| `test_cancel_comments_in_progress_subtasks_and_milestone` | e2e (`tests/e2e/`) |
| `test_board_down_run_ends_done_with_warnings_and_next_life_flushes` | e2e (`tests/e2e/`) |

## Done when

`uv run pytest` is green for the whole suite, `tests/e2e` included, and the commit `test(e2e): outcome comments on a real board` is on this card's `m12` branch, which is built from the stacked story 1+2 work (`m12/task-comment-on-pause-cancel-5d9a875f`).

---

# Outcome Comments on a Real Board — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `tests/e2e/test_board_comments.py`, which proves under the fake `claude` against a real temporary `brd` board that a clean run, an escalation plus `am resume`, a cancel, and a board-down run each leave exactly the outcome comments the board-comments design (B2, B5, B7, B8, B9) promises.

**Architecture:** One new e2e module, no product code changes. Each test builds its own board through the function-scoped `milestone_board` fixture (a1 -> a2 in story A, B blocked by A with b1, C blocked by B with c1), drives `am run --milestone` / `am resume` / `am cancel` through `CliRunner` on the real `cli.app` with no `runner_factory`, and reads comments back through the real `board.comment_list` (`brd comment list`). The board-down scenario uses a module-local `brd` PATH shim (a `/bin/sh` script) that fails only `brd comment ...` calls while `AM_E2E_BRD_FAIL_COMMENTS` is set, and execs the real `brd` for everything else.

**Tech Stack:** Python 3, pytest, Typer's `CliRunner`, the real `brd` and `git` CLIs, `tests/e2e/fake_claude.py` as `claude`.

**Spec:** `docs/superpowers/specs/task-prove-outcome-comments-649a8e88-design.md` (prepended above), which implements Task 3.1 of `docs/superpowers/plans/2026-09-29-board-comments.md` against `docs/superpowers/specs/2026-09-29-board-comments-design.md`.

## Global Constraints

- Read the code as built first: grep for real names rather than trusting milestone 9-11 plans. The names used below were read from this branch: `comments.key`, `comments.CAP`, `board.comment_list`, `board.BoardComment(id, body, author)`, `board.BoardError`, `board.set_status`, `store.open_db`, `store.load_run`, `store.latest_run_id`, the `board_comments` table (`run_id, card_id, key, body, state, comment_id, failed_attempts, ...`), `store.COMMENT_ATTEMPTS = 3`, `cli.EXIT_ESCALATED`, `cli.run_direct`, `control.apply_pending`.
- `uv run pytest` must stay green for the whole suite including tests/e2e; tests never touch the real data directory (tests/conftest.py's isolation fixture/guard). `milestone_board` -> `fresh_project` already points `XDG_DATA_HOME` into `tmp_path`.
- `brd` is only ever called from src/agent_manager/board.py; bodies go to `brd comment add` on stdin (`-`), never argv. The tests read through `board.comment_list` / `board.show`, never by building their own `brd` argv (the only exception is the shim script itself, which is the `brd` on PATH).
- A board failure never escalates, parks, or changes a run's status — it becomes a report warning only.
- No agent text on the board except the three named failure fields (validate_spec/validate_plan -> `reason`, implement -> `blocked_reason`, review -> `unresolved_blockers`), quoted, `[[` escaped, capped at 1,500 chars (`comments.CAP`).
- Author is `am`; every body's last line is `am-key: <key>` (format `<run-id>/<card-id>/<event>`, `comments.key()`).
- Branch prefix `m12` for this card's git branch (`m12/task-prove-outcome-comments-649a8e88`). The milestone runs the tests drive use the conftest's own `MILESTONE_PREFIX = "m3"`; that is unrelated.
- No change to any `src/agent_manager/` file, to `tests/e2e/conftest.py`, or to `tests/e2e/fake_claude.py`. A failing scenario that is not a test bug is a product bug for 77b0a064: stop and report it, do not fix it here.
- No documentation changes (owned by 3bf6ad5b).
- Final commit message: `test(e2e): outcome comments on a real board`.

## Decisions taken while planning (read before Task 5)

- The spec author's orientation summary reached this stage truncated at 2,000 characters, cut off mid-sentence on the open question of which entry point flushes after a board-down run. The spec on disk is complete and was used instead; the truncation is noted here as a sign the upstream stage over-ran its brief.
- That open question is settled from the code: `am resume` refuses a `done` milestone run (`orchestrate.resumable_milestone_run`, `NotResumableError` "run … finished; start new work with am run --milestone"). So the next life after a board-down run is a relaunch, `am run --milestone <milestone>`, which mints a new run id and, under its lease and before driving anything, calls `comments.flush(store, root, card_ids=milestone_card_ids(...))` (orchestrate.py:1599). `Store.pending_comments` matches `card_ids` across runs, so the first run's pending rows are posted.
- A relaunch is itself a run and posts its own run-end on the milestone card (orchestrate.py:1737-1738, `comment_run_end`). So "running the same flush entry point a second time adds nothing" is asserted as: no subtask or story card gains a comment, no key of the earlier runs appears twice, and the milestone card gains exactly one comment, the new run's own run-end.
- The fake's failing review writes `unresolved_blockers = ["the review-fail marker names <branch>"]`, which contains no `[[`. Scenario 2 therefore asserts the exact quoted `reason:` line and that the escalation body has no `[[` at all. `_unlink`'s escaping itself is owned by the unit tier (`tests/test_comments.py`, 77b0a064); no fake-claude switch is added for it (spec Scope: fake changes only if needed).
- `am cancel` is driven as `test_live_control.py` drives it: the milestone runs on a worker thread, one launch is held in-process by wrapping `cli.run_direct`, and `am cancel <run-id>` goes through `CliRunner` on a second store connection (the "other process" of live-control §7). The hold is on a2's `plan`, so a1 is already done and b1/c1 never start: that is the only layout on `milestone_board` that shows all three cases (done, in progress, not started) deterministically.
- Token-scoped keys (`escalated:<token>`, `run-end:<token>`) are checked as `comments.key(run_id, card, "<event>:")` plus a non-empty token, read off the posted comment and, in Task 5, cross-checked against the store's `board_comments` row. The lease token itself is not read from `run_leases`: a life's lease row is released when the run ends, so the posted key is the only durable record of it. Distinct lives are proven by distinct keys.
- The resumed done comment's second line is matched as `(resumed at <phase>)` with a regex rather than a pinned phase: the phase comes from `runtime_engine.pending_phase` of the rewound turn checkpoint, which this test does not own.

## Review Focus

1. A board that is down for the whole run must cost each queued comment exactly one failed attempt (one in-lane or run-end flush per card), never enough to reach `store.COMMENT_ATTEMPTS` and be abandoned before the next life can deliver it. Expected: after the down run every row of that run is `pending` with `failed_attempts == 1`, and the payload has exactly one warning per key saying `(attempt 1 of 3)`. Pinned in Task 5.
2. A shim left armed or left on PATH after its test would make every later comment in the session fail silently (only warnings). Expected: the env var and PATH change are undone at teardown. Pinned by the self-test in Task 4 (shim first on PATH while armed, comments work once up) and the module-last guard in Task 5.
3. brd may normalize a body on the way in (a trailing newline). Expected: the `am-key:` line is still the last non-blank line. `_assert_shape` compares on `body.rstrip()` lines in Task 1, the same rule `_post_one` uses.
4. A relaunch on a finished milestone posts its own run-end each time. Expected by a reader: the earlier run's comments are never posted twice, and the milestone card grows by exactly one comment per relaunch, keyed to the new run. Pinned in Task 5.
5. Agent text with `[[` reaching the board as a live backlink. Expected: none. The e2e fake produces no `[[`, so Task 2 pins that the escalation body contains no `[[` and quotes exactly the one `unresolved_blockers` line; escaping proper stays in the unit tier.

---

## File Structure

- Create: `tests/e2e/test_board_comments.py` — the only file this card adds. Sections, in order: module docstring, imports, constants, comment-reading helpers (Task 1), resume helper (Task 2), live-control hold helpers (Task 3), the `brd` shim fixture (Task 4), outbox reader (Task 5), then the tests in scenario order, with the module-last guard at the bottom.

Sibling conventions mirrored: `tests/e2e/test_milestone_run.py` and `tests/e2e/test_live_control.py` (module-local `_envelope`, `_resume`, `_hold`, `_control_while_held` helpers copied rather than imported, since tests run under `--import-mode=importlib` and no module imports another; unmarked so they run in the default suite).

---

### Task 1: Module, comment-reading helpers, and the clean-run scenario

**Files:**
- Create: `tests/e2e/test_board_comments.py`

**Interfaces:**
- Consumes (from `tests/e2e/conftest.py`, unchanged): fixtures `milestone_board` -> `{"root": Path, "milestone": str, "stories": {"A","B","C": str}, "subtasks": {"A": [a1, a2], "B": [b1], "C": [c1]}, "branches": {card_id: branch}}`; `run_milestone_cli(root, milestone) -> click.testing.Result`.
- Produces (used by Tasks 2-5): `KEY_LINE: str`, `_envelope(result) -> dict[str, Any]`, `_subtasks(shape) -> list[str]`, `_all_cards(shape) -> list[str]`, `_on(root, card_id) -> list[board.BoardComment]`, `_key_of(comment) -> str`, `_assert_shape(comment, *, outcome: str, run_id: str, key: str) -> list[str]`, `_assert_scoped(comment, *, outcome: str, run_id: str, prefix: str) -> tuple[list[str], str]`, `_comment_warnings(payload) -> list[str]`.

- [ ] **Step 1: Write the failing test**

Create `tests/e2e/test_board_comments.py` with this content (the helpers it calls do not exist yet):

```python
"""Default-suite e2e tier: outcome comments on a real brd board (card 649a8e88).

Board-comments design B2, B5, B7, B8, B9 and its §6 "Testing": under the fake
`claude` against a real temporary board, `brd comment list` after a clean run,
after an escalation and `am resume`, after a cancel, and after a run during
which `brd comment` was down. Every launch goes through `CliRunner` on the
real `cli.app` with no `runner_factory`, so the real `ClaudeAdapter` reaches
the fake `claude` first on `PATH`. Comments are read back through the real
`board.comment_list`, never through the store's outbox, except where the
board-down scenario checks what the outbox still owes.

Each test builds its own board (`milestone_board`): a1 -> a2 in story A,
B (b1) blocked by A, C (c1) blocked by B. Unmarked on purpose: it must run on
every `uv run pytest`.
"""

import json
import os
import re
import shlex
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agent_manager import board, cli, comments, control, store
from agent_manager.harness import launcher

KEY_LINE = "am-key: "
"""How every outcome comment's last line starts (board-comments B5)."""

INTEGRATION_BRANCH = "m3-integrate"
"""`integration.integration_branch` for the conftest's `m3` prefix."""


def test_clean_milestone_run_posts_one_done_per_subtask_and_one_run_end(
    milestone_board, run_milestone_cli
):
    """Spec scenario 1: one `am · done` per subtask, one run-end on the
    milestone, nothing on any story."""
    root = milestone_board["root"]
    milestone = milestone_board["milestone"]
    branches = milestone_board["branches"]

    result = run_milestone_cli(root, milestone)

    assert result.exit_code == 0, (result.output, result.exception)
    data = _envelope(result)
    assert data["done"] is True, data
    run_id = data["run_id"]
    assert _comment_warnings(data) == []

    for card in _subtasks(milestone_board):
        (done,) = _on(root, card)
        lines = _assert_shape(
            done, outcome="done", run_id=run_id, key=comments.key(run_id, card, "done")
        )
        assert f"branch: {branches[card]}" in lines, done.body
        assert not any(line.startswith("(resumed at") for line in lines), done.body

    for story in milestone_board["stories"].values():
        assert _on(root, story) == [], story

    (end,) = _on(root, milestone)
    lines, _key = _assert_scoped(
        end,
        outcome="done",
        run_id=run_id,
        prefix=comments.key(run_id, milestone, "run-end:"),
    )
    assert "done: 4 of 4" in lines, end.body
    assert f"integrated: {INTEGRATION_BRANCH}" in lines, end.body
    assert f"next: `git merge {INTEGRATION_BRANCH}`" in lines, end.body


def test_this_module_runs_in_the_default_suite_unmarked(request):
    """Like `test_milestone_run.py`'s guard: no `e2e` marker may reach this
    module, or outcome comments stop being checked on every run."""
    assert {mark.name for mark in request.node.own_markers} == set()
    assert {mark.name for mark in request.node.parent.own_markers} == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/e2e/test_board_comments.py::test_clean_milestone_run_posts_one_done_per_subtask_and_one_run_end -v`
Expected: FAIL with `NameError: name '_envelope' is not defined`.

- [ ] **Step 3: Write the helpers**

Insert this block in `tests/e2e/test_board_comments.py` directly after the `INTEGRATION_BRANCH` constant and before the first test:

```python
def _envelope(result) -> dict[str, Any]:
    envelope = json.loads(result.stdout)
    assert set(envelope) == {"ok", "data"}, envelope
    assert envelope["ok"] is True, envelope
    return envelope["data"]


def _subtasks(shape: dict[str, Any]) -> list[str]:
    """Every subtask of the board, census order: a1, a2, b1, c1."""
    return [card for chain in shape["subtasks"].values() for card in chain]


def _all_cards(shape: dict[str, Any]) -> list[str]:
    return [*_subtasks(shape), *shape["stories"].values(), shape["milestone"]]


def _on(root: Path, card_id: str) -> list[board.BoardComment]:
    """`brd comment list <card>`, oldest first, through the real board module."""
    return board.comment_list(card_id, repo_dir=root)


def _key_of(comment: board.BoardComment) -> str:
    """The comment's `am-key`, read off its last non-blank line."""
    last = comment.body.rstrip().splitlines()[-1]
    assert last.startswith(KEY_LINE), comment.body
    return last.removeprefix(KEY_LINE)


def _assert_shape(
    comment: board.BoardComment, *, outcome: str, run_id: str, key: str
) -> list[str]:
    """The four checks every outcome comment must pass; its lines for more."""
    lines = comment.body.rstrip().splitlines()
    assert comment.author == "am", comment
    assert lines[0] == f"am · {outcome} · run {run_id}", comment.body
    assert lines[-1] == f"{KEY_LINE}{key}", comment.body
    assert len(comment.body) <= comments.CAP, len(comment.body)
    return lines


def _assert_scoped(
    comment: board.BoardComment, *, outcome: str, run_id: str, prefix: str
) -> tuple[list[str], str]:
    """`_assert_shape` for a lease-token-scoped key (B5): `prefix` is
    `comments.key(run_id, card, "<event>:")` and the token after it is non-empty."""
    found = _key_of(comment)
    assert found.startswith(prefix) and len(found) > len(prefix), (found, prefix)
    return _assert_shape(comment, outcome=outcome, run_id=run_id, key=found), found


def _comment_warnings(payload: dict[str, Any]) -> list[str]:
    """The payload warnings `comments._warning` wrote, one per failed post."""
    return [warning for warning in payload["warnings"] if warning.startswith("board comment ")]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/e2e/test_board_comments.py -v`
Expected: both tests PASS. If the clean-run test fails on a comment assertion (not a NameError or a typo in this file), that is a product finding for 77b0a064: stop and report the failing assertion with the comment body; do not change `src/`.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_board_comments.py
git commit -m "test(e2e): outcome comments on a real board"
```

---

### Task 2: Escalation, fix, and `am resume`

**Files:**
- Modify: `tests/e2e/test_board_comments.py` (add `VERIFY`, `RESUMED_LINE`, `_resume`, and one test)

**Interfaces:**
- Consumes: Task 1's `_envelope`, `_subtasks`, `_all_cards`, `_on`, `_key_of`, `_assert_shape`, `_assert_scoped`; conftest's `review_fail_marker -> Path` (the repo's `.git/fake-claude-review-fail`, one branch per line), `run_milestone_cli`, `milestone_board`; `cli.EXIT_ESCALATED`.
- Produces: `VERIFY: str`, `RESUMED_LINE: re.Pattern[str]`, `_resume(root: Path, run_id: str) -> click.testing.Result` (used again by nothing later, but kept beside the constants).

- [ ] **Step 1: Write the failing test**

Append to `tests/e2e/test_board_comments.py`, after `test_this_module_runs_in_the_default_suite_unmarked`:

```python
def test_escalation_then_resume_keeps_escalation_and_appends_resumed_done(
    milestone_board, review_fail_marker, run_milestone_cli
):
    """Spec scenario 2: b1's review escalates; its escalation comment quotes
    only `unresolved_blockers` and stays after `am resume`, which appends
    exactly one `done (resumed at …)`. The milestone gets one run-end per
    life, with distinct lease-token-scoped keys (B2, B5, B9)."""
    root = milestone_board["root"]
    milestone = milestone_board["milestone"]
    branches = milestone_board["branches"]
    a1, a2 = milestone_board["subtasks"]["A"]
    (b1,) = milestone_board["subtasks"]["B"]
    (c1,) = milestone_board["subtasks"]["C"]
    review_fail_marker.write_text(f"{branches[b1]}\n", encoding="utf-8")

    first = run_milestone_cli(root, milestone)

    assert first.exit_code == cli.EXIT_ESCALATED, (first.output, first.exception)
    stopped = _envelope(first)
    assert stopped["subtask"] == b1, stopped
    assert stopped["failed_phase"] == "review", stopped
    run_id = stopped["run_id"]

    (escalation,) = _on(root, b1)
    escalation_lines, escalation_key = _assert_scoped(
        escalation,
        outcome="escalated",
        run_id=run_id,
        prefix=comments.key(run_id, b1, "escalated:"),
    )
    assert "phase: review" in escalation_lines, escalation.body
    # Only the review's own field, quoted (B3): the fake's one unresolved blocker.
    assert [line for line in escalation_lines if line.startswith("reason: ")] == [
        f'reason: "the review-fail marker names {branches[b1]}"'
    ], escalation.body
    assert "[[" not in escalation.body, escalation.body
    assert f"next: `am resume {run_id}`" in escalation_lines, escalation.body

    first_done = {}
    for card in (a1, a2):
        (done,) = _on(root, card)
        _assert_shape(done, outcome="done", run_id=run_id, key=comments.key(run_id, card, "done"))
        first_done[card] = done.id
    assert _on(root, c1) == []  # never started: no comment of any kind
    for story in milestone_board["stories"].values():
        assert _on(root, story) == [], story

    (first_end,) = _on(root, milestone)
    first_end_lines, first_end_key = _assert_scoped(
        first_end,
        outcome="escalated",
        run_id=run_id,
        prefix=comments.key(run_id, milestone, "run-end:"),
    )
    assert f"escalated: [[{b1}]] at review" in first_end_lines, first_end.body
    assert f"next: `am resume {run_id}`" in first_end_lines, first_end.body

    # Fix the fake, then resume the same run.
    review_fail_marker.unlink()
    resumed = _resume(root, run_id)

    assert resumed.exit_code == 0, (resumed.output, resumed.exception)
    data = _envelope(resumed)
    assert data["done"] is True, data
    assert data["resumed"] is True, data
    assert data["run_id"] == run_id
    assert sorted(data["completed"]) == sorted([b1, c1]), data

    # The escalation stays (never deleted, B2); one resumed done follows (B9).
    after = _on(root, b1)
    assert len(after) == 2, [comment.body for comment in after]
    assert (after[0].id, after[0].body) == (escalation.id, escalation.body)
    done_lines = _assert_shape(
        after[1], outcome="done", run_id=run_id, key=comments.key(run_id, b1, "done")
    )
    assert RESUMED_LINE.fullmatch(done_lines[1]), after[1].body

    (c1_done,) = _on(root, c1)
    c1_lines = _assert_shape(
        c1_done, outcome="done", run_id=run_id, key=comments.key(run_id, c1, "done")
    )
    assert not any(line.startswith("(resumed at") for line in c1_lines), c1_done.body
    for card in (a1, a2):
        assert [comment.id for comment in _on(root, card)] == [first_done[card]], card

    ends = _on(root, milestone)
    assert len(ends) == 2, [comment.body for comment in ends]
    assert ends[0].id == first_end.id
    _lines, second_end_key = _assert_scoped(
        ends[1],
        outcome="done",
        run_id=run_id,
        prefix=comments.key(run_id, milestone, "run-end:"),
    )
    assert second_end_key != first_end_key  # a new life, a new lease token (B5)
    assert escalation_key != comments.key(run_id, b1, "done")

    every_key = [_key_of(comment) for card in _all_cards(milestone_board) for comment in _on(root, card)]
    assert len(every_key) == len(set(every_key)), every_key
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/e2e/test_board_comments.py::test_escalation_then_resume_keeps_escalation_and_appends_resumed_done -v`
Expected: FAIL with `NameError: name '_resume' is not defined` (raised after the first launch, once the test reaches the resume).

- [ ] **Step 3: Add the resume helper and its constants**

Insert directly after the `INTEGRATION_BRANCH` constant in `tests/e2e/test_board_comments.py`:

```python
VERIFY = "git rev-parse --verify HEAD"
"""Must equal the conftest's `VERIFY_COMMANDS[0]`."""

RESUMED_LINE = re.compile(r"\(resumed at [a-z_]+\)")
"""A resumed done's second line: where the walk picked up (`compose_done`)."""


def _resume(root: Path, run_id: str):
    """`am resume <run-id>`: no prefix, base or bound, only what the record lacks."""
    return CliRunner().invoke(
        cli.app, ["resume", run_id, "--repo-dir", str(root), "--verify", VERIFY]
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/e2e/test_board_comments.py::test_escalation_then_resume_keeps_escalation_and_appends_resumed_done -v`
Expected: PASS. A failure on a comment assertion is a product finding: report it, do not touch `src/`.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_board_comments.py
git commit -m "test(e2e): outcome comments on a real board (escalation and resume)"
```

---

### Task 3: Cancel

**Files:**
- Modify: `tests/e2e/test_board_comments.py` (add hold/control helpers and one test)

**Interfaces:**
- Consumes: Task 1's helpers; `cli.run_direct` (read at call time by `cli.default_runner_factory`), `launcher.run_direct`, `control.apply_pending`, `store.open_db`, `store.latest_run_id`, `store.load_run`, `cli.resolve_repo_dir`.
- Produces: `HELD_PHASE = "plan"`, `WAIT = 120.0`, `_latest_run_id(root) -> str`, `_run_status(root, run_id) -> str`, `_attempt_of(stdout_path) -> tuple[str, str]`, `_hold(monkeypatch, card, phase, entered, release) -> None`, `_signal_when_applied(monkeypatch, applied) -> None`, `_in_background(work) -> tuple[threading.Thread, dict[str, Any]]`, `_control_while_held(root, milestone, command, *, run_milestone_cli, entered, release, applied) -> tuple[str, dict, Result]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/e2e/test_board_comments.py`:

```python
def test_cancel_comments_in_progress_subtasks_and_milestone(
    milestone_board, run_milestone_cli, monkeypatch
):
    """Spec scenario 3: held in a2's plan and cancelled from another
    connection. a1 (done) keeps only its done comment, a2 (in progress) gets
    one cancelled comment, b1 and c1 (never started) get nothing, and the
    milestone gets one cancelled run-end."""
    root = milestone_board["root"]
    milestone = milestone_board["milestone"]
    branches = milestone_board["branches"]
    a1, a2 = milestone_board["subtasks"]["A"]
    (b1,) = milestone_board["subtasks"]["B"]
    (c1,) = milestone_board["subtasks"]["C"]
    entered, release, applied = threading.Event(), threading.Event(), threading.Event()
    _hold(monkeypatch, a2, HELD_PHASE, entered, release)
    _signal_when_applied(monkeypatch, applied)

    run_id, requested, first = _control_while_held(
        root,
        milestone,
        "cancel",
        run_milestone_cli=run_milestone_cli,
        entered=entered,
        release=release,
        applied=applied,
    )

    assert requested["effective"] == "cancel", requested
    assert first.exit_code == 0, (first.output, first.exception)
    cancelled = _envelope(first)
    assert cancelled["cancelled"] is True, cancelled
    assert cancelled["run_id"] == run_id
    assert _run_status(root, run_id) == "cancelled"

    (a1_done,) = _on(root, a1)
    _assert_shape(a1_done, outcome="done", run_id=run_id, key=comments.key(run_id, a1, "done"))

    (a2_cancelled,) = _on(root, a2)
    lines = _assert_shape(
        a2_cancelled,
        outcome="cancelled",
        run_id=run_id,
        key=comments.key(run_id, a2, "cancelled"),
    )
    assert f"branch: {branches[a2]}" in lines, a2_cancelled.body
    assert f"relaunch: `am run --milestone {milestone}`" in lines, a2_cancelled.body

    for card in (b1, c1):
        assert _on(root, card) == [], card
    for story in milestone_board["stories"].values():
        assert _on(root, story) == [], story

    (end,) = _on(root, milestone)
    end_lines, _key = _assert_scoped(
        end,
        outcome="cancelled",
        run_id=run_id,
        prefix=comments.key(run_id, milestone, "run-end:"),
    )
    assert "done: 1 of 4" in end_lines, end.body
    assert f"parked: [[{a2}]]" in end_lines, end.body
    assert f"next: `am run --milestone {milestone}`" in end_lines, end.body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/e2e/test_board_comments.py::test_cancel_comments_in_progress_subtasks_and_milestone -v`
Expected: FAIL with `NameError: name '_hold' is not defined`.

- [ ] **Step 3: Add the hold and control helpers**

Insert directly after `_resume` in `tests/e2e/test_board_comments.py` (copied from `tests/e2e/test_live_control.py`, where they are proven, plus `_run_status`):

```python
HELD_PHASE = "plan"
"""The phase whose launch is held while the cancel is sent."""

WAIT = 120.0
"""Seconds any bounded wait gives up after. It only bounds a broken run."""


def _latest_run_id(root: Path) -> str:
    """The run id, read on a second connection: the "other process" of live-control §7."""
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        run_id = store.latest_run_id(conn)
    finally:
        conn.close()
    assert run_id is not None
    return run_id


def _run_status(root: Path, run_id: str) -> str:
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        run = store.load_run(conn, run_id)
    finally:
        conn.close()
    assert run is not None, run_id
    return run.status


def _attempt_of(stdout_path: Path) -> tuple[str, str]:
    """(card id, phase) of the attempt a launch belongs to, from
    `<run dir>/<card>/<phase>.<n>/stdout.log`; the fake is never asked."""
    attempt = Path(stdout_path).parent
    return attempt.parent.name, attempt.name.rsplit(".", 1)[0]


def _hold(
    monkeypatch,
    card: str,
    phase: str,
    entered: threading.Event,
    release: threading.Event,
) -> None:
    """Hold `card`'s `phase` launch once: announce it, wait for `release`, then launch.

    `cli.default_runner_factory` reads `cli.run_direct` at call time; the
    launch runs in a `to_thread` worker, so the control watcher stays free.
    One-shot, and undone with the test's function-scoped `monkeypatch`.
    """
    real = launcher.run_direct
    armed = {"on": True}

    def holding(argv, *, cwd, timeout, stdout_path, on_spawn=None):
        if armed["on"] and _attempt_of(stdout_path) == (card, phase):
            armed["on"] = False
            entered.set()
            if not release.wait(WAIT):
                raise AssertionError(f"{card}'s {phase} launch was never released")
        return real(
            argv, cwd=cwd, timeout=timeout, stdout_path=stdout_path, on_spawn=on_spawn
        )

    monkeypatch.setattr(cli, "run_direct", holding)


def _signal_when_applied(monkeypatch, applied: threading.Event) -> None:
    """Set `applied` once the running process has applied a control request
    (a non-empty return from `control.apply_pending`)."""
    real = control.apply_pending

    def applying(*args: Any, **kwargs: Any):
        rows = real(*args, **kwargs)
        if rows:
            applied.set()
        return rows

    monkeypatch.setattr(control, "apply_pending", applying)


def _in_background(work: Callable[[], Any]) -> tuple[threading.Thread, dict[str, Any]]:
    """Run `work` on a daemon thread; its result or error lands in the box."""
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["result"] = work()
        except BaseException as error:  # surfaced by the caller, never swallowed
            box["error"] = error

    worker = threading.Thread(target=target, name="am-run-milestone", daemon=True)
    worker.start()
    return worker, box


def _control_while_held(
    root: Path,
    milestone: str,
    command: str,
    *,
    run_milestone_cli,
    entered: threading.Event,
    release: threading.Event,
    applied: threading.Event,
):
    """Run the milestone in a worker, send `am <command>` while the hold is in,
    and finish it. Returns (run id, the control command's `data`, the run's
    `CliRunner` result). `release` is set in a `finally`, so a failed
    assertion never leaves the worker hanging."""
    worker, box = _in_background(lambda: run_milestone_cli(root, milestone))
    try:
        assert entered.wait(WAIT), "the held launch never arrived"
        run_id = _latest_run_id(root)
        requested = CliRunner().invoke(cli.app, [command, run_id, "--repo-dir", str(root)])
        assert requested.exit_code == 0, (requested.output, requested.exception)
        control_data = _envelope(requested)
        assert applied.wait(WAIT), f"the running process never applied the {command}"
    finally:
        release.set()
        worker.join(WAIT)
    assert not worker.is_alive(), "the milestone run never finished after release"
    assert "error" not in box, box.get("error")
    return run_id, control_data, box["result"]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/e2e/test_board_comments.py::test_cancel_comments_in_progress_subtasks_and_milestone -v`
Expected: PASS. A failure on a comment assertion is a product finding: report it, do not touch `src/`.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_board_comments.py
git commit -m "test(e2e): outcome comments on a real board (cancel)"
```

---

### Task 4: The `brd` comment-failure shim

**Files:**
- Modify: `tests/e2e/test_board_comments.py` (add `BRD_FAIL_COMMENTS_ENV`, `BRD_SHIM`, `BrdShim`, the `brd_shim` fixture, and its self-test)

**Interfaces:**
- Consumes: conftest's `toolchain` (skips without `brd`/`git`), `fake_claude_bin` (module-scoped PATH change, which must already be in place so the shim lands in front of it), `milestone_board`.
- Produces: `BRD_FAIL_COMMENTS_ENV = "AM_E2E_BRD_FAIL_COMMENTS"`; dataclass `BrdShim(path: Path, monkeypatch: pytest.MonkeyPatch)` with `down() -> None` and `up() -> None`; fixture `brd_shim -> BrdShim`, function-scoped, starts `up`.

- [ ] **Step 1: Write the failing test**

Append to `tests/e2e/test_board_comments.py`:

```python
def test_the_brd_shim_fails_only_comment_calls_while_down(milestone_board, brd_shim):
    """Scaffolding check: while down, `brd comment list/add` fail the way a
    dead board does (`BoardError`) and nothing is posted; card reads and a
    status write still go through; once up, comments work again."""
    root = milestone_board["root"]
    a1 = milestone_board["subtasks"]["A"][0]
    assert Path(shutil.which("brd")) == brd_shim.path

    brd_shim.down()
    with pytest.raises(board.BoardError):
        board.comment_list(a1, repo_dir=root)
    with pytest.raises(board.BoardError):
        board.comment_add(a1, f"shim check\n{KEY_LINE}shim/{a1}/check", repo_dir=root)
    status = board.show(a1, repo_dir=root).status
    assert board.set_status(a1, status, repo_dir=root).status == status
    assert board.tree(milestone_board["milestone"], repo_dir=root).id == milestone_board["milestone"]

    brd_shim.up()
    assert _on(root, a1) == []  # the add while down never reached the board
    assert os.environ.get(BRD_FAIL_COMMENTS_ENV) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/e2e/test_board_comments.py::test_the_brd_shim_fails_only_comment_calls_while_down -v`
Expected: ERROR at setup with `fixture 'brd_shim' not found`.

- [ ] **Step 3: Add the shim and its fixture**

Insert directly after `_control_while_held` in `tests/e2e/test_board_comments.py`:

```python
BRD_FAIL_COMMENTS_ENV = "AM_E2E_BRD_FAIL_COMMENTS"
"""Set (to anything non-empty) and the shim fails every `brd comment ...` call.
Must equal the variable name written into `BRD_SHIM`."""

BRD_SHIM = """#!/bin/sh
# Test scaffolding (card 649a8e88): the board is "down" for comments only.
if [ -n "${AM_E2E_BRD_FAIL_COMMENTS:-}" ] && [ "$1" = "comment" ]; then
    echo "brd shim: the board is down for comments" >&2
    exit 1
fi
exec @REAL_BRD@ "$@"
"""
"""A `brd` that exits 1 with stderr only (what `board._run` turns into
`BoardError`) on `brd comment ...` while the env var is set, and otherwise
`exec`s the real `brd` with the same argv and stdin. Card reads and status
writes always pass, so a run can proceed (B8 makes only comments best-effort)."""


@dataclass
class BrdShim:
    """Switches the `brd` shim between down (comments fail) and up.

    The env var goes through the test's own function-scoped `monkeypatch`,
    so it is undone when the test ends. `am` and `brd` children inherit it:
    `board._run` passes no `env=`.
    """

    path: Path
    monkeypatch: pytest.MonkeyPatch

    def down(self) -> None:
        self.monkeypatch.setenv(BRD_FAIL_COMMENTS_ENV, "1")

    def up(self) -> None:
        self.monkeypatch.delenv(BRD_FAIL_COMMENTS_ENV, raising=False)


@pytest.fixture
def brd_shim(tmp_path, monkeypatch, toolchain, fake_claude_bin) -> BrdShim:
    """The shim, first on `PATH` (ahead of the fake `claude`'s dir too), up.

    The real `brd` is resolved before the shim's dir is on `PATH` and baked
    into the script, so the shim can never call itself. The `PATH` change is
    the test's own `monkeypatch`, undone at teardown.
    """
    real = shutil.which("brd")
    assert real is not None
    bin_dir = tmp_path / "brd-shim"
    bin_dir.mkdir()
    shim = bin_dir / "brd"
    shim.write_text(BRD_SHIM.replace("@REAL_BRD@", shlex.quote(real)), encoding="utf-8")
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    switch = BrdShim(path=shim, monkeypatch=monkeypatch)
    switch.up()
    return switch
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/e2e/test_board_comments.py::test_the_brd_shim_fails_only_comment_calls_while_down -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_board_comments.py
git commit -m "test(e2e): outcome comments on a real board (brd comment shim)"
```

---

### Task 5: Board down for the whole run, then the next life flushes

**Files:**
- Modify: `tests/e2e/test_board_comments.py` (add `_outbox`, the board-down test, and the module-last guard)

**Interfaces:**
- Consumes: Task 1's helpers, Task 3's `_run_status(root, run_id) -> str`, Task 4's `brd_shim` / `BRD_FAIL_COMMENTS_ENV` / `BrdShim`, `store.COMMENT_ATTEMPTS`, the `board_comments` table columns `run_id, card_id, key, state, failed_attempts`.
- Produces: `_outbox(root: Path, run_id: str) -> dict[str, tuple[str, str, int]]` mapping key -> (card_id, state, failed_attempts).

- [ ] **Step 1: Write the failing test**

Append to `tests/e2e/test_board_comments.py`:

```python
def test_board_down_run_ends_done_with_warnings_and_next_life_flushes(
    milestone_board, brd_shim, run_milestone_cli
):
    """Spec scenario 4 (B7, B8, B9): with `brd comment` down the run still
    ends done, exit 0, with one warning per unposted key and every row
    failed once. A relaunch (the next life; `am resume` refuses a done run)
    posts them with the clean-run shape, and a second relaunch posts none of
    them again."""
    root = milestone_board["root"]
    milestone = milestone_board["milestone"]
    branches = milestone_board["branches"]
    order = _subtasks(milestone_board)

    brd_shim.down()
    down = run_milestone_cli(root, milestone)
    brd_shim.up()

    # A board failure changes nothing about the run (B8).
    assert down.exit_code == 0, (down.output, down.exception)
    data = _envelope(down)
    assert data["done"] is True, data
    assert "escalated" not in data
    assert data["completed"] == order
    run_id = data["run_id"]
    assert _run_status(root, run_id) == "done"

    # What the outbox still owes: one done per subtask and one run-end, each
    # failed exactly once, so none is near abandonment.
    owed = _outbox(root, run_id)
    end_prefix = comments.key(run_id, milestone, "run-end:")
    end_keys = [key for key in owed if key.startswith(end_prefix)]
    assert len(end_keys) == 1, owed
    done_keys = {comments.key(run_id, card, "done"): card for card in order}
    assert set(owed) == {*done_keys, *end_keys}, owed
    warnings = _comment_warnings(data)
    assert len(warnings) == len(owed), warnings
    for key, (card, state, attempts) in owed.items():
        assert (state, attempts) == ("pending", 1), (key, state, attempts)
        assert attempts < store.COMMENT_ATTEMPTS
        named = [warning for warning in warnings if f"board comment {key} on card {card} " in warning]
        assert len(named) == 1, (key, warnings)
        assert f"(attempt 1 of {store.COMMENT_ATTEMPTS})" in named[0], named[0]

    # Same statuses as the clean run, and nothing on the board yet.
    for card in _all_cards(milestone_board):
        assert board.show(card, repo_dir=root).status == "done", card
        assert _on(root, card) == [], card

    # The next life: a relaunch's start-of-run flush (orchestrate.py:1599).
    relaunch = run_milestone_cli(root, milestone)

    assert relaunch.exit_code == 0, (relaunch.output, relaunch.exception)
    later = _envelope(relaunch)
    assert later["done"] is True, later
    assert later["completed"] == [], later
    later_id = later["run_id"]
    assert later_id != run_id
    assert _comment_warnings(later) == []
    assert {state for _card, state, _attempts in _outbox(root, run_id).values()} == {"posted"}

    for card in order:
        (done,) = _on(root, card)
        lines = _assert_shape(
            done, outcome="done", run_id=run_id, key=comments.key(run_id, card, "done")
        )
        assert f"branch: {branches[card]}" in lines, done.body
    for story in milestone_board["stories"].values():
        assert _on(root, story) == [], story
    ends = _on(root, milestone)
    assert len(ends) == 2, [comment.body for comment in ends]
    _lines, flushed_end_key = _assert_scoped(
        ends[0], outcome="done", run_id=run_id, prefix=end_prefix
    )
    assert flushed_end_key == end_keys[0]
    _assert_scoped(
        ends[1],
        outcome="done",
        run_id=later_id,
        prefix=comments.key(later_id, milestone, "run-end:"),
    )

    # The same entry point again: nothing of the earlier runs is posted twice;
    # only the new run's own run-end is added to the milestone card.
    before = {card: [comment.id for comment in _on(root, card)] for card in _all_cards(milestone_board)}
    again = run_milestone_cli(root, milestone)

    assert again.exit_code == 0, (again.output, again.exception)
    third = _envelope(again)
    assert third["done"] is True, third
    assert _comment_warnings(third) == []
    for card in [*order, *milestone_board["stories"].values()]:
        assert [comment.id for comment in _on(root, card)] == before[card], card
    final = _on(root, milestone)
    assert [comment.id for comment in final[:2]] == before[milestone]
    assert len(final) == 3, [comment.body for comment in final]
    _assert_scoped(
        final[2],
        outcome="done",
        run_id=third["run_id"],
        prefix=comments.key(third["run_id"], milestone, "run-end:"),
    )
    every_key = [_key_of(comment) for card in _all_cards(milestone_board) for comment in _on(root, card)]
    assert len(every_key) == len(set(every_key)), every_key


def test_no_brd_shim_is_left_armed_for_later_tests():
    """Review Focus 2: the shim's env var and `PATH` entry are the test's own
    `monkeypatch`, so both are gone once its test ends. Kept last in the module."""
    assert os.environ.get(BRD_FAIL_COMMENTS_ENV) is None
    found = shutil.which("brd")
    assert found is None or Path(found).parent.name != "brd-shim", found
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/e2e/test_board_comments.py::test_board_down_run_ends_done_with_warnings_and_next_life_flushes -v`
Expected: FAIL with `NameError: name '_outbox' is not defined`.

- [ ] **Step 3: Add the outbox reader**

Insert directly after the `brd_shim` fixture in `tests/e2e/test_board_comments.py`:

```python
def _outbox(root: Path, run_id: str) -> dict[str, tuple[str, str, int]]:
    """`run_id`'s outbox rows, key -> (card id, state, failed attempts).

    Read on a fresh connection, like `am status`. Only the board-down
    scenario reads it, to show what the board still owes; every other
    assertion reads the board itself.
    """
    conn = store.open_db(cli.resolve_repo_dir(root))
    try:
        rows = conn.execute(
            "SELECT key, card_id, state, failed_attempts FROM board_comments"
            " WHERE run_id = ? ORDER BY rowid",
            (run_id,),
        ).fetchall()
    finally:
        conn.close()
    return {row[0]: (row[1], row[2], int(row[3])) for row in rows}
```

- [ ] **Step 4: Run the module to verify it passes**

Run: `uv run pytest tests/e2e/test_board_comments.py -v`
Expected: all seven tests PASS (four scenarios, the shim self-test, the unmarked guard, the module-last shim guard). A failure on a comment or warning assertion is a product finding for 77b0a064: report it, do not touch `src/`.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: the whole suite PASSES, `tests/e2e` included, with the `e2e`-marked real-harness modules deselected as usual.

- [ ] **Step 6: Commit**

```bash
git add tests/e2e/test_board_comments.py
git commit -m "test(e2e): outcome comments on a real board (board down, next life flushes)"
```
