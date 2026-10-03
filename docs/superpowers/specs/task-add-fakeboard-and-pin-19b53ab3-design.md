# Subtask 19b53ab3 — Add FakeBoard and pin it against real brd

Parent story: 13c63fea ("Give board.py an injection seam and a FakeBoard fake"), milestone 66ed75cd ("Milestone 15: test tiers"). Narrows decision V3 of `docs/superpowers/specs/2026-10-02-test-tier-design.md` to its fixture-and-oracle half; the seam half is sibling `cf9f8555` (done).

## Prerequisites (reconcile before building)

- The `run_brd` seam from `cf9f8555` (branch `m15/task-add-the-run-brd-cf9f8555`, commits `2cb9349`, `aadfb0d`) must be in this branch. Master's `board.py` still calls `_run` directly, so without it FakeBoard has nothing to patch. Do not re-add or re-shape the seam.
- The `brd` marker and `addopts` tier exclusion from `19b291e9` (branch `m15/task-register-the-markers-19b291e9`) must be in this branch so that `-m brd` selects the pinning test and the default run skips it. Do not register markers here; consume them.

## Scope

1. A `FakeBoard` in `tests/conftest.py`, plus a fixture that builds one and installs it with `monkeypatch.setattr(board, "run_brd", fake)`. This is a different fake from the module-private `FakeBoard` already in `tests/test_comments.py` (which stands in for `board`'s Python call surface -- `comment_list`/`comment_add`/`write_lock` -- for `comments.flush`'s `board_api` seam). The new one stands in for the `brd` subprocess itself, one level lower, and is unrelated; do not merge or reuse the two.
   - It holds an in-memory card tree: cards with `id, title, status, parent_id, description, blocked_by, created_at`, plus per-card comments (`id, entity_id, body, author, created_at`). Tests seed it through a small helper API (for example, adding a card under a parent, or adding a comment).
   - It is callable as `run_brd(argv, repo_dir, stdin)` and returns a real `subprocess.CompletedProcess[str]` whose `stdout` is a JSON envelope and whose `returncode` matches what real brd returns.
   - It answers exactly the argv shapes `board.py` builds: `show <id>`, `tree <id>`, `tree` (no id; roots), `update <id> --status <s>`, `comment add <id> - --author <a>` (body read from `stdin`), and `comment list <id>`.
   - Success answers `{"ok": true, "data": ...}`, with data shaped like real brd's: a card object for `show`/`update`; a one-element list of nested nodes (`children`) for `tree <id>`; a list of roots for `tree`; a comment object with a string `id` for `comment add`; and the comments oldest first for `comment list`. Payloads must validate as `models.Card` and `models.CardNode` (`src/agent_manager/models.py:175-211`) and as `BoardComment` via `comment_list`.
   - Writes mutate the in-memory tree, so a later `show` sees the new status and a later `comment list` sees the new comment. Every write is also recorded in order, as `set_status` and `comment_add` entries carrying the card id, status or body, and author, so that tests can assert on them.
2. One `brd`-tier pinning test: it builds the same small card tree on a real temporary brd board and in a FakeBoard, then asserts that FakeBoard's envelopes match real `brd show`, `brd tree` and `brd comment list` output. This follows the "conftest twin" spirit of `tests/e2e/test_fake_claude.py:39-50`: the fake's shape is checked against its real counterpart so it cannot drift silently.
   - The tree has a parent, at least one child, and a comment. The FakeBoard is seeded from the real board's ids and fields.
   - "Match" means the same JSON structure at every level: the same key sets, the same JSON value types, the same list lengths and order, and equal values for every non-volatile field (`title, status, parent_id, description, blocked_by, body, author`, nesting). Volatile values such as generated ids and timestamps are compared by type and presence only, unless seeding makes them identical.
   - It reuses the existing real-board helpers in `tests/test_board.py` (`temp_board` around :241-260, `_add_card`/`_brd_json` around :264-274), or places the test beside them.

## Error paths

- An unknown card id in `show`, `tree <id>`, `update` or `comment add`/`list` answers `{"ok": false, "error": {"type", "message"}}` with a non-zero `returncode`, using the error type real brd actually uses for that argv -- verified against this worktree's installed `brd`: `CardNotFoundError` for `show`, `tree <id>` and `update`; `EntityNotFoundError` for `comment add` and `comment list`. The result is that `_decode` raises `BoardError` with that `error_type`, as it does against real brd.
- `comment add` with an empty body answers an `ok: false` envelope, mirroring brd's rejection.
- Any argv FakeBoard does not recognise fails loudly (an `AssertionError` naming the argv). It never returns a guessed envelope.

## Out of scope

The following are out of scope:

- Moving `test_orchestrate.py` or `test_cli.py` onto FakeBoard (V4/V5).
- Any change to `board.py`'s public signatures, envelopes or behaviour.
- The seam itself (`cf9f8555`).
- Marker registration (`19b291e9`).
- Auto-marking, the PATH-shim/duration guard, and e2e cap consolidation (`b4edda6b`, `3202b0b0`, `3aa663b8`).
- xdist.
- The engine, checkpoint, harness, `dispatch.py`.
- Milestone 14.

## Tests

Tier placement follows `docs/superpowers/specs/2026-10-02-test-tier-design.md` §3 V1: a test belongs to the tier of whatever it actually spawns.

| Test | Tier |
|---|---|
| FakeBoard pins against real brd `show`/`tree`/`comment list` for the same card tree (structural match as defined above) | `brd` (`@pytest.mark.brd`, because it spawns the real `brd` binary) |
| `board.show`/`tree`/`roots` through FakeBoard return the seeded `Card`/`CardNode` values, with nesting and order intact | `unit` (no marker; fake only, no subprocess) |
| `board.set_status` and `board.comment_add` through FakeBoard are recorded in order and visible to later `show`/`comment_list` | `unit` (no marker) |
| An unknown card id through FakeBoard raises `BoardError` with brd's error type; an empty comment body is rejected | `unit` (no marker) |
| An unrecognised argv makes FakeBoard fail loudly | `unit` (no marker) |

Unit-tier tests must stay well under the 0.5s per-test budget and must never touch a subprocess.

## Verification

- `uv run pytest -m brd` passes, including the new pinning test.
- `uv run pytest` (default) stays green, with the pinning test deselected and the FakeBoard unit tests running.
