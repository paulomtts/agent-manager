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
