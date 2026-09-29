# Outcome comments on brd cards — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> Each task is one `brd` subtask card driven by the `task` workflow; the card's description names its Task and carries its excerpt.

**Goal:** `am` leaves a short, factual, idempotent comment on the brd card where each run outcome happened (done, escalated, cancelled, merged base failed, run end), never letting the board affect a run.

**Architecture:** Pure `comments.compose_*` functions build bodies; an outbox table in the store (`board_comments`) receives them inside the run's fenced transaction after the outcome is recorded; `comments.flush` posts pending rows under the board lock, checking a trailing `am-key:` line on the card first so a crash can never double-post. Posting sites are explicit calls where outcomes happen, not TASK steps.

**Tech Stack:** Python 3.12, `brd` CLI (`comment add/list`), SQLite, pytest (+ pytest-asyncio), `uv`.

**Spec:** `docs/superpowers/specs/2026-09-29-board-comments-design.md` (B1–B10).

## Global Constraints

- **Read the code as built first.** Milestones 9–11 (live control, several processes, exactly-once) land before this one and may differ from their plans. Every task starts by locating the real names it uses (`store.immediate`, the lease token, the board `ProcessLock`, `LeaseLostError`, M9's cancel outcome) with `grep`, and adapts.
- Verification for every task: `uv run pytest` green (the whole suite, including `tests/e2e`). Tests never touch the real data directory (`tests/conftest.py`'s isolation fixture and guard stay green).
- `brd` is only ever called from `src/agent_manager/board.py`. Bodies go to `brd comment add` on **stdin** (`-`), never argv.
- A board failure never escalates, parks or changes a run's status; it becomes a report warning.
- No agent text on the board except the three failure fields named in spec §1, quoted, `[[` escaped, capped.
- Author `am`; every body's last line is `am-key: <key>`; 1,500-character cap per body.
- Branch prefix `m12`. Base: `master` with milestones 9, 10 and 11 merged.

## Review Focus

1. **Agent text containing `[[<card-id>]]`** must not create a board backlink. Test in Task 1.3.
2. **A crash after `brd comment add` and before the row is marked posted.** The next flush finds the `am-key` and posts nothing. Test in Task 2.1.
3. **A resumed run escalating again at the same phase in its second life** gets a second escalation comment (lease-token-scoped key); a subtask done once gets exactly one done comment. Test in Task 2.2.
4. **`brd` down for a whole run.** The run ends `done` with warnings; rows stay `pending`; the next `am resume`/relaunch flush posts them. Test in Task 2.1 (unit) and Task 3.1 (e2e).
5. **The lease is lost while enqueueing (M10).** Enqueue raises the lease error like any fenced write and leaves no half row. Test in Task 2.1.

---

## Story 1 — Plumbing

### Task 1.1: Add `board.comment_add` and `board.comment_list`

**Files:** `src/agent_manager/board.py`. Test: `tests/test_board.py`.

**Interfaces:** `comment_add(card_id: str, body: str, *, author: str = "am", repo_dir: Path | None = None) -> str` (the new comment's id); `comment_list(card_id: str, *, repo_dir: Path | None = None) -> list[BoardComment]` with `BoardComment(id: str, body: str, author: str)` (frozen dataclass). Same `BoardError` handling as `set_status`; `comment_add` passes the body on stdin (`brd comment add <id> - --author <a>`).

- [ ] **Step 1: Failing tests** in `tests/test_board.py`'s existing style (read how it runs `brd`: a real temporary board or a faked `_run`; follow it): add returns an id; list returns bodies oldest first; a body of 5,000 characters with newlines and `$(...)` round-trips exactly (stdin, not argv); an unknown card raises `BoardError`.
- [ ] **Step 2:** watch them fail.  **Step 3:** implement next to `set_status_argv`/`set_status`, reusing `_run`/`_decode` (add an `input=` parameter to `_run` if it has none).
- [ ] **Step 4:** `uv run pytest` green.  **Step 5: Commit** — `feat(board): add and list card comments`

### Task 1.2: Add the `board_comments` outbox to the store

**Files:** `src/agent_manager/store.py`. Test: `tests/test_store.py`.

**Interfaces:** table per spec B6. `Store.enqueue_comment(*, run_id, card_id, key, body, now) -> bool` (True when inserted, False when the key existed; runs inside the run's fenced `store.immediate` transaction as M10 defines for row writers); `Store.pending_comments(run_id: str | None = None, card_ids: Iterable[str] | None = None) -> list[CommentRow]` (oldest first); `Store.mark_comment_posted(key, comment_id, now)`; `Store.record_comment_failure(key) -> int` (new `failed_attempts`; sets `abandoned` at 3). `CommentRow(run_id, card_id, key, body, state, comment_id, failed_attempts)`.

- [ ] **Step 1: Failing tests** — enqueue twice with one key → one row, second call returns False; pending order is insertion order; mark posted removes it from pending; three failures → `abandoned`, not pending; `pending_comments(card_ids=...)` filters across runs (for relaunch).
- [ ] **Step 2–4:** fail, implement (`CREATE TABLE IF NOT EXISTS`, no migration, M9 C1), green.
- [ ] **Step 5: Commit** — `feat(store): an outbox for board comments`

### Task 1.3: Compose comment bodies

**Files:** Create `src/agent_manager/comments.py`. Test: `tests/test_comments.py`.

**Interfaces (pure, no I/O):**
```python
CAP = 1500
@dataclass(frozen=True)
class Comment:
    card_id: str
    key: str
    body: str

def key(run_id: str, card_id: str, event: str) -> str          # "<run>/<card>/<event>"
def compose_done(*, run_id, card_id, summary, branch, resumed_at: str | None) -> Comment
def compose_escalated(*, run_id, card_id, token, failed_phase, detail, reason: str | None) -> Comment
def compose_cancelled(*, run_id, card_id, before_phase, branch, relaunch: str) -> Comment
def compose_base_failed(*, run_id, story_id, base_branch, detail) -> Comment
def compose_run_end(*, run_id, milestone_id, token, payload: Mapping[str, Any]) -> Comment
def agent_reason(results: Mapping[str, Any], failed_phase: str) -> str | None
    # spec_critic/plan_critic reason, implement blocked_reason, review unresolved_blockers; else None
```
Body rules (spec B4): first line `am · <outcome> · run <run-id>`; commands in backticks; real card ids as `[[id]]`; agent text in double quotes with `[[` → `[ [`; total ≤ `CAP`, cutting agent text first and appending `… (truncated; see \`am logs <run> <card> --phase <p>\`)`; last line `am-key: <key>`.

- [ ] **Step 1: Failing tests** — a golden body per event (done fresh, done resumed, escalated with and without reason, cancelled, base failed, run end done/escalated/paused/cancelled); a 10,000-char reason is cut and the body is ≤ 1,500 with the `am-key` line intact (Review Focus 1's sibling); agent text `see [[4f31e025-…]]` renders as `[ [4f31e025-…]]` (Review Focus 1); keys match spec B5.
- [ ] **Step 2–4:** fail, implement, green.  **Step 5: Commit** — `feat(comments): compose outcome comments`

## Story 2 — Outbox and wiring

### Task 2.1: Enqueue and flush

**Files:** `src/agent_manager/comments.py`. Test: `tests/test_comments.py`.

**Interfaces:** `enqueue(store, comment: Comment, *, run_id, now) -> None`; `flush(store, root: Path, *, run_id: str | None = None, card_ids=None, board_api=board) -> list[str]` (warnings). Flush takes each pending row, oldest first, and under the board `ProcessLock` (M10 X7; not inside a store transaction):

```python
existing = board_api.comment_list(row.card_id, repo_dir=root)
match = next((c for c in existing if c.body.rstrip().endswith(f"am-key: {row.key}")), None)
if match is None:
    comment_id = board_api.comment_add(row.card_id, row.body, author="am", repo_dir=root)
else:
    comment_id = match.id
store.mark_comment_posted(row.key, comment_id, now)
```
On `BoardError`/timeout: `store.record_comment_failure(row.key)`, append one warning, continue with the next row.

- [ ] **Step 1: Failing tests** with a fake `board_api`: pending rows post in order; a fake that raises after `comment_add` succeeded but before marking (simulate by raising in `mark` on the first call) → the second flush finds the key and adds nothing (Review Focus 2); board down → warnings, rows pending, then posted on a later flush (Review Focus 4); 3 failures → abandoned + warning; enqueue with a lost lease raises the lease error and leaves no row (Review Focus 5, use M10's test helper for a taken-over lease).
- [ ] **Step 2–4:** fail, implement, green.  **Step 5: Commit** — `feat(comments): a crash-safe outbox flush`

### Task 2.2: Comment on lane outcomes, merged bases and run end

**Files:** `src/agent_manager/orchestrate.py`, `src/agent_manager/bases.py`, `src/agent_manager/cli.py` (`resume_run`). Test: `tests/test_orchestrate.py`, `tests/test_bases.py`.

**Interfaces:** after each `record_subtask(... "done")` in `lane`: `enqueue(compose_done(...))` then `flush`; after `"escalated"`: `compose_escalated` with `agent_reason(summary.results, failed_phase)`; `bases` failure → `compose_base_failed` on the story card; `run_milestone` end → `compose_run_end` on the milestone card (done / escalated / Integrate failure); `run_milestone` and `resume_run` call `flush(store, root, card_ids=<the milestone's cards>)` before driving anything. `resumed_at` comes from the subtask's resume checkpoint (`engine.pending_phase`) when one was used.

- [ ] **Step 1: Failing tests** (fake drivers, fake `board_api`): clean 2-story run → one done comment per subtask + one run-end; an escalation → comments on the escalated card and the milestone card only, none on parked siblings; resume after the fix → the escalation comment stays and `done (resumed at review)` is added; a second life escalating at the same phase → a second escalation comment (Review Focus 3); a failed merged base → a comment on the story card.
- [ ] **Step 2–4:** fail, implement, green.  **Step 5: Commit** — `feat(orchestrate): comment outcomes on the board`

### Task 2.3: Comment on pause, cancel and `--card` runs

**Files:** `src/agent_manager/orchestrate.py` (M9's paused/cancelled outcome branch), `src/agent_manager/cli.py` (`run_card`). Test: `tests/test_orchestrate.py`, `tests/test_cli.py`.

**Interfaces:** cancel → `compose_cancelled` for each subtask the cancel left `in_progress` + `compose_run_end(cancelled)` on the milestone; pause → `compose_run_end(paused)` on the milestone card only; `run_card` → the subtask's done/escalated/cancelled comment (no milestone card).

- [ ] **Step 1: Failing tests** — cancel with two parked subtasks → two cancel comments + a cancelled run-end; pause → exactly one comment, on the milestone; `am run --card` done → one done comment on that card; `am run --card` escalated → one escalation comment.
- [ ] **Step 2–4:** fail, implement, green.  **Step 5: Commit** — `feat: comment pause, cancel and card-run outcomes`

## Story 3 — Proof and documentation

### Task 3.1: Prove it end to end

**Files:** `tests/e2e/test_board_comments.py` (new), `tests/e2e/fake_claude.py` only if a scaffolding switch is needed.

- [ ] **Step 1:** Under the fake claude against a real temporary board (`tests/e2e/conftest.py`'s `project` fixture): a clean milestone run → `brd comment list` per subtask shows one `am · done` comment ending in its `am-key`, the milestone card one run-end; an escalation + fix + `am resume` → escalation then `done (resumed at …)`; a cancel (M9) → cancel comments; `brd` made to fail for the whole run (a scaffolding env var in the fake's PATH shim) → the run ends `done` with warnings, and the next `am resume`/relaunch posts the pending comments (Review Focus 4).
- [ ] **Step 2:** `uv run pytest` green.  **Step 3: Commit** — `test(e2e): outcome comments on a real board`

### Task 3.2: Document what the board records

**Files:** `README.md`; `docs/superpowers/specs/2026-09-23-agent-manager-design.md` (D5 row).

- [ ] **Step 1:** Read `comments.py`, the orchestrate call sites and `board.py` as built; document from the code.
- [ ] **Step 2:** README: a "What the board records" section (events → card, an example of each comment, `am-key`, best-effort, nothing deleted); amend D5 as spec B1 states.
- [ ] **Step 3:** `uv run pytest` green.  **Step 4: Commit** — `docs: what am records on the board`
