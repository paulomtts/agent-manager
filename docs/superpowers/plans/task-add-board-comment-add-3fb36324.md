<!-- task-pipeline: validated -->
# Subtask 3fb36324 — board.comment_add and board.comment_list

Card: 3fb36324-8ba0-46b2-902b-221bf293e174, parent story 60189137 ("Plumbing: board calls, the outbox table, composed bodies"), milestone 21f4cf06. Narrows decision B10 of `docs/superpowers/specs/2026-09-29-board-comments-design.md` (which amends D5 of `docs/superpowers/specs/2026-09-23-agent-manager-design.md`) and Task 1.1 of `docs/superpowers/plans/2026-09-29-board-comments.md`.

## Scope

Only the raw brd-calling primitives for card comments, in `src/agent_manager/board.py`, next to `set_status` / `set_status_argv`. `brd` is still only ever called from `board.py`.

In scope:

- `BoardComment` — a frozen plain dataclass with fields `id: str`, `body: str`, `author: str`. Plain dataclass, not a Pydantic model (named shape from the plan; no `_validated` call).
- `comment_add_argv(card_id: str, author: str) -> list[str]` returning `[BRD, "comment", "add", card_id, "-", "--author", author]`. The body is never part of argv; `-` tells brd to read it from stdin.
- `comment_list_argv(card_id: str) -> list[str]` returning `[BRD, "comment", "list", card_id]`.
- `comment_add(card_id: str, body: str, *, author: str = "am", repo_dir: Path | None = None) -> str` — runs the add argv with `body` piped on stdin and returns the new comment's `id`.
- `comment_list(card_id: str, *, repo_dir: Path | None = None) -> list[BoardComment]` — runs the list argv and returns the card's comments oldest-first, in the order brd returns them (brd already lists oldest-first; nothing is re-sorted here).
- `_run` gains an optional `input: str | None = None` parameter passed through to `subprocess.run(..., input=input, text=True)`. Existing callers are unchanged (default `None`).
- Both functions reuse `_run` and `_decode` unchanged otherwise.
- The `set_status` docstring currently says the board receives "status transitions and nothing else"; update that sentence so it no longer contradicts B1 (the board may also receive code-authored, append-only outcome comments). Wording change only.

Out of scope (owned by siblings — do not touch): the `board_comments` outbox table and `Store.enqueue_comment` / `pending_comments` / `mark_comment_posted` / `record_comment_failure` / `CommentRow` in `store.py` (subtask 2e63c3d8); body composition, `key()`, `agent_reason()`, the 1500-char cap, `[[` escaping, the am-key line in `comments.py` (subtask 476f1040); flush/enqueue orchestration and the board `ProcessLock` around flush (B7). No `write_lock` requirement is stated for these functions; do not add one.

## Observable behaviour

- brd's add payload is one object and its list payload is a list of objects, each `{id, entity_id, author, body, created_at}`. `comment_add` returns `data["id"]`; `comment_list` maps each item to `BoardComment(id=..., body=..., author=...)`.
- The body reaches brd byte-for-byte through stdin: newlines, long bodies (5,000 chars), and shell metacharacters such as `$(...)` go through unchanged and unexecuted.
- A card with no comments makes `comment_list` return `[]`.

## Error paths

- Unknown or nonexistent card: brd exits 1 with `{"ok": false, "error": {"type": "EntityNotFoundError", "message": "no entity with id <id>"}}`. The existing `_decode` turns this into `BoardError` with brd's message verbatim and `error_type="EntityNotFoundError"`. This holds for both add and list.
- brd missing, non-JSON output, non-envelope JSON, and so on: the existing `_run` / `_decode` `BoardError` paths, unchanged.
- Payload of the wrong shape (add `data` not an object with a string `id`; list `data` not a list, or an item missing `id`/`body`/`author`): raise `BoardError` carrying the argv, the same way `tree` / `roots` report unexpected shapes. Do not invent other semantics.

## Tests (`tests/test_board.py`)

Placement rule: design §14 "Testing", restated at `tests/test_board.py:1-7`. Anything that calls brd runs against a real temporary brd board over subprocess under `@requires_brd`, using the existing `temp_board` fixture and `_add_card` helper. No mocking of brd and no fake `_run`. Pure parts (argv construction) are ungated narrow checks at the bottom of the file.

| Test | Tier |
|---|---|
| `comment_add_argv` returns `[BRD, "comment", "add", id, "-", "--author", author]`, and the body never appears in it | pure, ungated |
| `comment_list_argv` returns `[BRD, "comment", "list", id]` | pure, ungated |
| `comment_add` on a real card returns a non-empty id that matches the id `comment_list` reports for that comment | `@requires_brd`, `temp_board` |
| `comment_list` returns several added bodies oldest-first, with `author` set (the `"am"` default and an explicit author) | `@requires_brd`, `temp_board` |
| A 5,000-character body containing newlines and `$(...)` round-trips exactly through `comment_add` → `comment_list` | `@requires_brd`, `temp_board` |
| `comment_list` on a card with no comments returns `[]` | `@requires_brd`, `temp_board` |
| `comment_add` and `comment_list` on an unknown card id raise `BoardError` (`error_type == "EntityNotFoundError"`) | `@requires_brd`, `temp_board` |

## Done

`uv run pytest` passes (there is no lint or typecheck step). Commit message: `feat(board): add and list card comments`.

---

# board.comment_add and board.comment_list Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `src/agent_manager/board.py` two raw brd comment primitives: `comment_add`, which pipes the body on stdin and returns the new id, and `comment_list`, which returns `BoardComment`s oldest-first. Both use the same `BoardError` handling as `set_status`.

**Architecture:** Two pure argv builders sit next to `set_status_argv`. `_run` gains a keyword `input=` that it forwards to `subprocess.run`. `comment_add` and `comment_list` follow the `_run` → `_decode` → shape check pattern that `tree`/`roots` already use, and they map payloads into a frozen plain dataclass `BoardComment`. No lock, outbox, composition or flush is added here.

**Tech Stack:** Python 3, `subprocess`, `dataclasses`, pytest, the real `brd` CLI (installed on PATH) for the steps-tier tests, run via `uv run pytest`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m12/task-add-board-comment-add-3fb36324/docs/superpowers/specs/task-add-board-comment-add-3fb36324-design.md` (prepended above).

**Worktree / branch:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m12/task-add-board-comment-add-3fb36324`, branch `m12/task-add-board-comment-add-3fb36324`, cut fresh from `origin/master`. No sibling subtask's code (store outbox, `comments.py`) exists on this branch, and nothing here may depend on it. Every path below is relative to this worktree.

## Global Constraints

- `brd` is only ever called from `src/agent_manager/board.py`.
- A comment body never goes in argv. It always goes on stdin, and the argv is `brd comment add <card_id> - --author <author>`.
- The list argv is exactly `brd comment list <card_id>`.
- `comment_add` defaults to `author="am"`.
- `BoardComment` is a frozen plain dataclass `(id: str, body: str, author: str)`, not a Pydantic model, and is not passed through `_validated`.
- Do not wrap comment calls in `write_lock`, and do not add any lock.
- Do not re-sort comments. The order brd returns is the order callers get (brd orders by `created_at, rowid`, see `/home/paulomtts/Code/brd/src/brd/comments.py:51-57`).
- Unexpected payload shapes raise `BoardError` with the argv, worded like the `tree`/`roots` shape guards.
- Do not touch `src/agent_manager/store.py`. Do not create `src/agent_manager/comments.py`. Do not add body composition, keys, caps or `[[` escaping.
- `shell=True` and `os.system` must never appear in `board.py` (`tests/test_board.py:118-122` enforces this).
- Verification: `uv run pytest`. There is no lint or typecheck command.
- Exactly one commit, at the end of Task 3, with message `feat(board): add and list card comments`.

## Review Focus

These cases are implied by the spec but no row of its test table covers them. The two whitespace and encoding items are in the 5,000-character round-trip test. Each of the others has its own test in the task that owns the code.

1. Empty or whitespace-only body: brd rejects it with `EmptyCommentError` (`/home/paulomtts/Code/brd/src/brd/comments.py:33-34`). The user expects `BoardError(error_type="EmptyCommentError")` and no comment written. Test: Task 2 `test_comment_add_of_a_blank_body_raises_brds_own_rejection`.
2. Leading or trailing whitespace and non-ASCII text in the body: brd reads stdin verbatim (`/home/paulomtts/Code/brd/src/brd/cli/comments.py:23`), so the round trip must be exact, including a final `\n`, a leading space, a tab, `é` and `✓`. Test: Task 3 `_hostile_body`, used by `test_a_long_hostile_body_round_trips_exactly_through_stdin`.
3. Comments am did not write, such as a human's `brd comment add --author paulo`: `comment_list` must still return them, in brd's order and with their real author, because the later outbox flush scans this list. Test: Task 3 `test_comment_list_includes_comments_am_did_not_write`.
4. Adding the same body twice: this layer does not deduplicate (idempotence belongs to the sibling outbox), so the two calls give two distinct ids, both listed. Test: Task 3 `test_comment_add_does_not_deduplicate`.
5. Comments are per card: a comment on card A must never appear in card B's list. Test: Task 3 `test_comment_list_is_scoped_to_one_card`.

## File Structure

- Modify `src/agent_manager/board.py`. Changes: module docstring (lines 1-23), `from dataclasses import dataclass` import, new `BoardComment` after `BoardError` (after line 86), new argv builders after `set_status_argv` (after line 105), `_run` signature and call (lines 108-122), `set_status` docstring (lines 260-262), and new `comment_add` / `comment_list` appended after `set_status` (after line 280).
- Modify `tests/test_board.py`. Changes: `import dataclasses` and `import inspect` at the top. Steps-tier and fake-brd tests go immediately above the line `# Pure checks: no board needed.` (currently line 875). Pure checks are appended at the very end of the file, after `test_roots_argv_is_brd_tree_with_no_id`.

Test tiers follow the spec's table:
- Argv builders, the `_run` stdin pass-through (run with `cat`) and the `BoardComment` shape checks are pure and ungated. They go at the bottom of the file.
- Everything that calls brd uses `@requires_brd` and `temp_board`.
- The wrong-shape payload guards use a fake `brd` shell script on `board.BRD`. This is the same ungated pattern as `test_tree_requires_exactly_one_root` / `test_roots_requires_a_list_of_roots` (`tests/test_board.py:376-396`, `:857-872`). It is not a fake `_run`.

---

### Task 1: argv builders and `_run(input=...)`

**Files:**
- Modify: `src/agent_manager/board.py:102-122`
- Test: `tests/test_board.py` (top imports; append at end of file)

**Interfaces:**
- Consumes: `board.BRD` (`src/agent_manager/board.py:37`), `board._run` (`:108`).
- Produces:
  - `comment_add_argv(card_id: str, author: str) -> list[str]`
  - `comment_list_argv(card_id: str) -> list[str]`
  - `_run(argv: list[str], repo_dir: Path | None, *, input: str | None = None) -> subprocess.CompletedProcess[str]`

- [ ] **Step 1: Add the test imports**

In `tests/test_board.py`, replace the import block at lines 9-13:

```python
import json
import shutil
import subprocess
import threading
from pathlib import Path
```

with:

```python
import dataclasses
import inspect
import json
import shutil
import subprocess
import threading
from pathlib import Path
```

- [ ] **Step 2: Write the failing pure tests**

Append to the very end of `tests/test_board.py`, after `test_roots_argv_is_brd_tree_with_no_id`:

```python


def test_comment_add_argv_reads_the_body_from_stdin():
    # `-` is brd's own "read the body from stdin" marker (brd comment add --help).
    assert board.comment_add_argv("3fb36324", "am") == [
        "brd",
        "comment",
        "add",
        "3fb36324",
        "-",
        "--author",
        "am",
    ]


def test_comment_add_argv_has_no_way_to_carry_a_body():
    # Bodies go on stdin, never argv: the builder does not even accept one.
    assert list(inspect.signature(board.comment_add_argv).parameters) == [
        "card_id",
        "author",
    ]
    assert board.comment_add_argv("3fb36324", "am").count("-") == 1


def test_comment_list_argv_is_brd_comment_list_with_the_card_id():
    assert board.comment_list_argv("3fb36324") == [
        "brd",
        "comment",
        "list",
        "3fb36324",
    ]


def test_comment_argv_keeps_shell_metacharacters_inside_one_element():
    hostile = "3fb36324; rm -rf /"
    assert board.comment_add_argv(hostile, "am $(whoami)") == [
        "brd",
        "comment",
        "add",
        hostile,
        "-",
        "--author",
        "am $(whoami)",
    ]
    assert board.comment_list_argv(hostile) == ["brd", "comment", "list", hostile]


def test_run_pipes_input_to_the_process_stdin():
    # `cat` echoes stdin back, so stdout proves the bytes arrived untouched
    # and unexecuted.
    text = "line one\n$(echo pwned)\n`echo pwned`\n"
    completed = board._run(["cat"], None, input=text)
    assert completed.returncode == 0
    assert completed.stdout == text
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_board.py -k "comment_add_argv or comment_list_argv or comment_argv or run_pipes_input" -v`
Expected: all 5 FAIL. Four fail with `AttributeError: module 'agent_manager.board' has no attribute 'comment_add_argv'` (or `'comment_list_argv'`), and `test_run_pipes_input_to_the_process_stdin` fails with `TypeError: _run() got an unexpected keyword argument 'input'`.

- [ ] **Step 4: Add the argv builders**

In `src/agent_manager/board.py`, directly after `set_status_argv` (after line 105, `return [BRD, "update", card_id, "--status", status]`), insert:

```python


def comment_add_argv(card_id: str, author: str) -> list[str]:
    # The body never rides in argv: `-` makes brd read it from stdin, so a body
    # of any length or content is never a command-line argument.
    return [BRD, "comment", "add", card_id, "-", "--author", author]


def comment_list_argv(card_id: str) -> list[str]:
    return [BRD, "comment", "list", card_id]
```

- [ ] **Step 5: Give `_run` an `input=` parameter**

In `src/agent_manager/board.py`, replace lines 108-122:

```python
def _run(
    argv: list[str], repo_dir: Path | None
) -> "subprocess.CompletedProcess[str]":
    """Run one `brd` argv list, returning the completed process.

    Raises `BoardError` when `brd` is missing, or when it failed without
    printing an envelope -- a bare non-zero exit with stderr only.
    """
    try:
        completed = subprocess.run(
            argv,
            cwd=repo_dir,
            capture_output=True,
            text=True,
        )
```

with:

```python
def _run(
    argv: list[str], repo_dir: Path | None, *, input: str | None = None
) -> "subprocess.CompletedProcess[str]":
    """Run one `brd` argv list, returning the completed process.

    `input`, when given, is written to the process's stdin -- the only way a
    comment body reaches brd. Raises `BoardError` when `brd` is missing, or
    when it failed without printing an envelope -- a bare non-zero exit with
    stderr only.
    """
    try:
        completed = subprocess.run(
            argv,
            cwd=repo_dir,
            capture_output=True,
            text=True,
            input=input,
        )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_board.py -k "comment_add_argv or comment_list_argv or comment_argv or run_pipes_input" -v`
Expected: 5 PASS.

- [ ] **Step 7: Run the whole board file to confirm existing callers are unaffected**

Run: `uv run pytest tests/test_board.py -v`
Expected: all PASS. Tests marked `requires_brd` are skipped only if `brd` is not on PATH. No commit yet, because the spec pins a single commit, made at the end of Task 3.

---

### Task 2: `comment_add`

**Files:**
- Modify: `src/agent_manager/board.py:1-23` (module docstring), `:255-280` (`set_status` docstring; append after it)
- Test: `tests/test_board.py` (insert immediately above the line `# Pure checks: no board needed.`)

**Interfaces:**
- Consumes: `comment_add_argv(card_id: str, author: str) -> list[str]` and `_run(..., *, input: str | None = None)` from Task 1; `_decode(stdout: str, *, argv: list[str], exit_code: int) -> object` (`board.py:135`); `BoardError(message, *, argv, exit_code=None, error_type=None)` (`board.py:64`); the test helpers `temp_board`, `_add_card(root, title, parent=None) -> str`, `_brd_json(root, *args) -> object` (`tests/test_board.py:239-276`), `requires_brd` (`:20`).
- Produces:
  - `comment_add(card_id: str, body: str, *, author: str = "am", repo_dir: Path | None = None) -> str`
  - Test helper `_fake_brd_answering(tmp_path: Path, data: object) -> Path`. It writes an executable `brd` stand-in that prints `{"ok": true, "data": <data>}`. Task 3 reuses it.

- [ ] **Step 1: Write the failing tests**

In `tests/test_board.py`, insert immediately above the line `# Pure checks: no board needed.`:

```python
def _fake_brd_answering(tmp_path: Path, data: object) -> Path:
    """An executable `brd` stand-in that prints one ok envelope around `data`.

    Same technique as the tree/roots shape guards above: it replaces the brd
    executable, not `_run`, so the real subprocess path still runs.
    """
    fake_brd = tmp_path / "brd"
    fake_brd.write_text(
        "#!/bin/sh\ncat <<'EOF'\n"
        + json.dumps({"ok": True, "data": data})
        + "\nEOF\n"
    )
    fake_brd.chmod(0o755)
    return fake_brd


@requires_brd
def test_comment_add_returns_the_new_comments_id(temp_board):
    card = _add_card(temp_board, "Add board.comment_add")

    comment_id = board.comment_add(card, "first outcome", repo_dir=temp_board)

    assert isinstance(comment_id, str) and comment_id
    raw = _brd_json(temp_board, "comment", "list", card)
    assert [(c["id"], c["body"], c["author"]) for c in raw] == [
        (comment_id, "first outcome", "am")
    ]


@requires_brd
def test_comment_add_passes_an_explicit_author(temp_board):
    card = _add_card(temp_board, "Add board.comment_add")

    board.comment_add(card, "by hand", author="paulo", repo_dir=temp_board)

    assert [c["author"] for c in _brd_json(temp_board, "comment", "list", card)] == [
        "paulo"
    ]


@requires_brd
def test_comment_add_on_an_unknown_card_raises_board_error(temp_board):
    with pytest.raises(board.BoardError) as excinfo:
        board.comment_add("no-such-card", "orphan", repo_dir=temp_board)
    assert excinfo.value.error_type == "EntityNotFoundError"
    assert "no-such-card" in excinfo.value.message
    assert excinfo.value.exit_code == 1
    assert excinfo.value.argv == [
        "brd",
        "comment",
        "add",
        "no-such-card",
        "-",
        "--author",
        "am",
    ]


@requires_brd
def test_comment_add_of_a_blank_body_raises_brds_own_rejection(temp_board):
    # brd refuses whitespace-only bodies; board.py special-cases nothing.
    card = _add_card(temp_board, "Add board.comment_add")
    with pytest.raises(board.BoardError) as excinfo:
        board.comment_add(card, "  \n\t", repo_dir=temp_board)
    assert excinfo.value.error_type == "EmptyCommentError"
    assert _brd_json(temp_board, "comment", "list", card) == []


@pytest.mark.parametrize(
    "data",
    [[], "nope", None, {}, {"id": 7}],
    ids=["list", "str", "null", "no-id", "int-id"],
)
def test_comment_add_requires_an_object_with_a_string_id(data, tmp_path, monkeypatch):
    fake_brd = _fake_brd_answering(tmp_path, data)
    monkeypatch.setattr(board, "BRD", str(fake_brd))
    with pytest.raises(board.BoardError) as excinfo:
        board.comment_add("3fb36324", "body", repo_dir=tmp_path)
    assert "a comment object with a string id" in str(excinfo.value)
    assert excinfo.value.argv == [
        str(fake_brd),
        "comment",
        "add",
        "3fb36324",
        "-",
        "--author",
        "am",
    ]


```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_board.py -k "comment_add_returns or comment_add_passes or comment_add_on_an_unknown or comment_add_of_a_blank or comment_add_requires" -v`
Expected: all FAIL with `AttributeError: module 'agent_manager.board' has no attribute 'comment_add'`. If brd is not installed, the four `requires_brd` tests are SKIPPED and only the 5 parametrized shape-guard cases fail. In that case install brd before going on, because the spec's main evidence needs a real board.

- [ ] **Step 3: Implement `comment_add`**

In `src/agent_manager/board.py`, append after the end of `set_status` (after line 280, `return _validated(models.Card, data, argv=argv)`):

```python


def comment_add(
    card_id: str,
    body: str,
    *,
    author: str = "am",
    repo_dir: Path | None = None,
) -> str:
    """Append one comment to a card via `brd comment add`, returning its id.

    The body is piped on stdin (`brd comment add <id> - --author <author>`),
    never placed in argv, so newlines, long text and shell metacharacters
    reach brd byte-for-byte and are never executed. No lock is taken and
    nothing is deduplicated: idempotence and serialization belong to the
    outbox flush that calls this (board-comments design B7). brd's own
    failures -- an unknown card, an empty body -- surface as `BoardError`.
    """
    argv = comment_add_argv(card_id, author)
    completed = _run(argv, repo_dir, input=body)
    data = _decode(completed.stdout, argv=argv, exit_code=completed.returncode)
    if not isinstance(data, dict) or not isinstance(data.get("id"), str):
        raise BoardError(
            f"brd comment add {card_id} returned {type(data).__name__} where "
            "a comment object with a string id was expected",
            argv=argv,
            exit_code=completed.returncode,
        )
    return data["id"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_board.py -k "comment_add_returns or comment_add_passes or comment_add_on_an_unknown or comment_add_of_a_blank or comment_add_requires" -v`
Expected: all PASS (4 steps-tier and 5 parametrized shape-guard cases).

- [ ] **Step 5: Update the `set_status` docstring (spec: wording change only)**

In `src/agent_manager/board.py`, inside `set_status`, replace:

```python
    This is the module's entire write surface: under D5 the board receives
    status transitions and nothing else, and run state lives in agent-manager's
    own store.
```

with:

```python
    This is the module's only status writer. Under D5, as amended by decision
    B1 of the board-comments addendum, the board receives status transitions
    and code-authored, append-only outcome comments (`comment_add`) -- never
    run state, which lives in agent-manager's own store.
```

- [ ] **Step 6: Update the module docstring so it matches the new write surface**

The module docstring still says "Four operations cross this seam" and "`set_status` is the module's entire write surface", which repeats the sentence the spec tells us to correct. In `src/agent_manager/board.py`, replace lines 3-14:

```python
Four operations cross this seam: read one card, read a card subtree, read
every root of the board, write a card status. Nothing about a *run* is ever
written to the board (decision D5, design §9) -- run state lives in
agent-manager's own SQLite projection and journal, so `set_status` is the
module's entire write surface.

Writes are serialized across threads and across `am` processes on one project
(spec X7 of the multi-process design): `set_status` runs under
`write_lock(repo_dir)`, the project's process-wide `board` lock, whose
in-process layer is the module-level `WRITE_LOCK`. `steps/rollup.py` holds the
same lock around its whole read-modify-write walk up a card's ancestors. Reads
take no lock. A `locks.LockTimeoutError` is never caught here.
```

with:

```python
Six operations cross this seam: read one card, read a card subtree, read
every root of the board, write a card status, add a comment to a card, and
list a card's comments. Nothing about a *run* is ever written to the board
(decision D5, design §9) -- run state lives in agent-manager's own SQLite
projection and journal. Under decision B1 of the board-comments addendum the
board may also receive code-authored, append-only outcome comments, so
`set_status` and `comment_add` are the module's entire write surface. A
comment body is piped to `brd comment add <id> -` on stdin, never put in argv.

Status writes are serialized across threads and across `am` processes on one
project (spec X7 of the multi-process design): `set_status` runs under
`write_lock(repo_dir)`, the project's process-wide `board` lock, whose
in-process layer is the module-level `WRITE_LOCK`. `steps/rollup.py` holds the
same lock around its whole read-modify-write walk up a card's ancestors. Reads
and the comment calls take no lock here; the outbox flush that drives comments
owns its own locking (board-comments B7). A `locks.LockTimeoutError` is never
caught here.
```

- [ ] **Step 7: Run the whole board file**

Run: `uv run pytest tests/test_board.py -v`
Expected: all PASS. No commit yet; the single commit comes at the end of Task 3.

---

### Task 3: `BoardComment` and `comment_list`, then commit

**Files:**
- Modify: `src/agent_manager/board.py` (imports at lines 25-29; new class after `BoardError`, after line 86; append after `comment_add`)
- Test: `tests/test_board.py` (steps-tier and fake-brd tests go immediately above `# Pure checks: no board needed.`; the pure `BoardComment` check goes at the end of the file)

**Interfaces:**
- Consumes: `comment_list_argv(card_id: str) -> list[str]` (Task 1); `comment_add(card_id, body, *, author="am", repo_dir=None) -> str` (Task 2); `_fake_brd_answering(tmp_path: Path, data: object) -> Path` (Task 2, in `tests/test_board.py`); `_run`, `_decode`, `BoardError`; the test helpers `temp_board`, `_add_card`, `_brd_json`, `requires_brd`.
- Produces (sibling subtasks 2e63c3d8 and the flush rely on these exact names):
  - `@dataclass(frozen=True) class BoardComment: id: str; body: str; author: str`
  - `comment_list(card_id: str, *, repo_dir: Path | None = None) -> list[BoardComment]`

- [ ] **Step 1: Write the failing steps-tier and shape-guard tests**

In `tests/test_board.py`, insert immediately above the line `# Pure checks: no board needed.` (so after the Task 2 tests):

```python
def _hostile_body(marker: Path) -> str:
    """5,000 characters a shell would mangle or execute, ending in a newline.

    brd reads stdin verbatim (it never strips), so leading/trailing whitespace,
    tabs and non-ASCII must all survive exactly.
    """
    head = (
        " leading space, then newlines\n"
        f"$(touch {marker})\n"
        f"`touch {marker}`\n"
        "; echo pwned && rm -rf nothing | cat > /dev/null\n"
        "'single' \"double\" \\backslash \t tab, accents é, check ✓\n"
    )
    filler = "0123456789" * 7 + "\n"
    body = (head + filler * 100)[:4999] + "\n"
    assert len(body) == 5000
    return body


@requires_brd
def test_comment_list_of_a_card_with_no_comments_is_empty(temp_board):
    card = _add_card(temp_board, "Add board.comment_list")
    assert board.comment_list(card, repo_dir=temp_board) == []


@requires_brd
def test_comment_list_returns_comments_oldest_first_with_their_authors(temp_board):
    card = _add_card(temp_board, "Add board.comment_list")
    first = board.comment_add(card, "first", repo_dir=temp_board)
    second = board.comment_add(card, "second", author="paulo", repo_dir=temp_board)
    third = board.comment_add(card, "third", repo_dir=temp_board)

    comments = board.comment_list(card, repo_dir=temp_board)

    # The ids comment_add returned are the ids comment_list reports.
    assert comments == [
        board.BoardComment(id=first, body="first", author="am"),
        board.BoardComment(id=second, body="second", author="paulo"),
        board.BoardComment(id=third, body="third", author="am"),
    ]
    # brd's order, untouched.
    raw = _brd_json(temp_board, "comment", "list", card)
    assert [comment.id for comment in comments] == [c["id"] for c in raw]


@requires_brd
def test_a_long_hostile_body_round_trips_exactly_through_stdin(temp_board, tmp_path):
    marker = tmp_path / "pwned"
    body = _hostile_body(marker)
    card = _add_card(temp_board, "Add board.comment_add")

    comment_id = board.comment_add(card, body, repo_dir=temp_board)

    assert board.comment_list(card, repo_dir=temp_board) == [
        board.BoardComment(id=comment_id, body=body, author="am")
    ]
    # Nothing in the body was ever executed by a shell.
    assert not marker.exists()


@requires_brd
def test_comment_list_on_an_unknown_card_raises_board_error(temp_board):
    with pytest.raises(board.BoardError) as excinfo:
        board.comment_list("no-such-card", repo_dir=temp_board)
    assert excinfo.value.error_type == "EntityNotFoundError"
    assert "no-such-card" in excinfo.value.message
    assert excinfo.value.exit_code == 1
    assert excinfo.value.argv == ["brd", "comment", "list", "no-such-card"]


@requires_brd
def test_comment_list_includes_comments_am_did_not_write(temp_board):
    # A human's comment, written with brd directly, is listed in brd's order
    # with its real author -- the later outbox flush scans this list.
    card = _add_card(temp_board, "Add board.comment_list")
    mine = board.comment_add(card, "from am", repo_dir=temp_board)
    _brd_json(temp_board, "comment", "add", card, "from a human", "--author", "paulo")

    listed = board.comment_list(card, repo_dir=temp_board)

    assert [(c.body, c.author) for c in listed] == [
        ("from am", "am"),
        ("from a human", "paulo"),
    ]
    assert listed[0].id == mine


@requires_brd
def test_comment_add_does_not_deduplicate(temp_board):
    # Idempotence is the outbox's job, not this primitive's.
    card = _add_card(temp_board, "Add board.comment_add")
    first = board.comment_add(card, "same body", repo_dir=temp_board)
    second = board.comment_add(card, "same body", repo_dir=temp_board)

    assert first != second
    assert [c.id for c in board.comment_list(card, repo_dir=temp_board)] == [
        first,
        second,
    ]


@requires_brd
def test_comment_list_is_scoped_to_one_card(temp_board):
    one = _add_card(temp_board, "card one")
    two = _add_card(temp_board, "card two")
    on_one = board.comment_add(one, "about one", repo_dir=temp_board)

    assert board.comment_list(two, repo_dir=temp_board) == []
    assert [c.id for c in board.comment_list(one, repo_dir=temp_board)] == [on_one]


@pytest.mark.parametrize("data", [{}, "nope", None], ids=["dict", "str", "null"])
def test_comment_list_requires_a_list_of_comments(data, tmp_path, monkeypatch):
    fake_brd = _fake_brd_answering(tmp_path, data)
    monkeypatch.setattr(board, "BRD", str(fake_brd))
    with pytest.raises(board.BoardError) as excinfo:
        board.comment_list("3fb36324", repo_dir=tmp_path)
    assert "a list of comments" in str(excinfo.value)
    assert excinfo.value.argv == [str(fake_brd), "comment", "list", "3fb36324"]


@pytest.mark.parametrize(
    "item",
    [
        {"id": "c1", "body": "b"},
        {"id": "c1", "author": "am"},
        {"body": "b", "author": "am"},
        {"id": "c1", "body": None, "author": "am"},
        "c1",
    ],
    ids=["no-author", "no-body", "no-id", "null-body", "bare-str"],
)
def test_comment_list_rejects_an_item_that_is_not_a_comment(item, tmp_path, monkeypatch):
    good = {"id": "c0", "entity_id": "3fb36324", "author": "am", "body": "ok", "created_at": "t"}
    fake_brd = _fake_brd_answering(tmp_path, [good, item])
    monkeypatch.setattr(board, "BRD", str(fake_brd))
    with pytest.raises(board.BoardError) as excinfo:
        board.comment_list("3fb36324", repo_dir=tmp_path)
    assert "not a comment" in str(excinfo.value)
    assert excinfo.value.argv == [str(fake_brd), "comment", "list", "3fb36324"]


```

- [ ] **Step 2: Write the failing pure `BoardComment` check**

Append to the very end of `tests/test_board.py`, after `test_run_pipes_input_to_the_process_stdin`:

```python


def test_board_comment_is_a_frozen_plain_dataclass():
    comment = board.BoardComment(id="c1", body="body", author="am")
    assert dataclasses.is_dataclass(comment)
    assert [field.name for field in dataclasses.fields(comment)] == [
        "id",
        "body",
        "author",
    ]
    with pytest.raises(dataclasses.FrozenInstanceError):
        comment.body = "changed"  # type: ignore[misc]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_board.py -k "comment_list or hostile_body or does_not_deduplicate or board_comment_is" -v`
Expected: all FAIL with `AttributeError: module 'agent_manager.board' has no attribute 'comment_list'` or `... has no attribute 'BoardComment'`.

- [ ] **Step 4: Add the dataclass import**

In `src/agent_manager/board.py`, replace lines 25-29:

```python
import json
import subprocess
import threading
from pathlib import Path
from typing import TypeVar
```

with:

```python
import json
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar
```

- [ ] **Step 5: Add `BoardComment`**

In `src/agent_manager/board.py`, directly after the `BoardError` class (after its `super().__init__(...)` call, before `def show_argv`), insert:

```python


@dataclass(frozen=True)
class BoardComment:
    """One comment on a card, as `comment_list` reports it.

    A plain dataclass, not a Pydantic model: `comment_list` checks the three
    fields it keeps itself and drops brd's `entity_id` and `created_at`.
    """

    id: str
    body: str
    author: str
```

- [ ] **Step 6: Implement `comment_list`**

In `src/agent_manager/board.py`, append after `comment_add`:

```python


def comment_list(
    card_id: str, *, repo_dir: Path | None = None
) -> list[BoardComment]:
    """A card's comments, oldest first, via `brd comment list`.

    brd already lists oldest first (by creation time, then insertion), and that
    order is returned untouched -- nothing is re-sorted. Every comment on the
    card is included, whoever wrote it. A card with no comments is an empty
    list; an unknown card surfaces brd's `EntityNotFoundError` as `BoardError`.
    """
    argv = comment_list_argv(card_id)
    completed = _run(argv, repo_dir)
    data = _decode(completed.stdout, argv=argv, exit_code=completed.returncode)
    if not isinstance(data, list):
        raise BoardError(
            f"brd comment list {card_id} returned {type(data).__name__} where "
            "a list of comments was expected",
            argv=argv,
            exit_code=completed.returncode,
        )
    comments: list[BoardComment] = []
    for item in data:
        if not isinstance(item, dict) or not all(
            isinstance(item.get(field), str) for field in ("id", "body", "author")
        ):
            raise BoardError(
                f"brd comment list {card_id} returned an item that is not a "
                f"comment with a string id, body and author: {item!r:.200}",
                argv=argv,
                exit_code=completed.returncode,
            )
        comments.append(
            BoardComment(id=item["id"], body=item["body"], author=item["author"])
        )
    return comments
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_board.py -k "comment_list or hostile_body or does_not_deduplicate or board_comment_is" -v`
Expected: all PASS (7 steps-tier, 3 + 5 parametrized shape-guard cases, and 1 pure check).

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest`
Expected: all PASS. The new `requires_brd` tests must show PASSED, not SKIPPED. Confirm brd is on PATH, because a skip here would mean the spec's main evidence never ran.

- [ ] **Step 9: Commit**

```bash
git add src/agent_manager/board.py tests/test_board.py
git commit -m "feat(board): add and list card comments"
```
