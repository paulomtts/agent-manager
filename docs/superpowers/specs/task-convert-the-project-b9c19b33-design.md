# Subtask b9c19b33 — Convert the `project`/`_milestone` fixtures to FakeBoard

Card: b9c19b33-e2f0-4174-8be6-c32ca53e10cc. Parent story: 8460355c ("Move test_orchestrate.py off the real board"). Milestone: 66ed75cd. Governing spec: `docs/superpowers/specs/2026-10-02-test-tier-design.md` §3 decision V4, §4 file-map row for `tests/test_orchestrate.py`, §6 Testing. This narrows V4 to its fixture half. The comment-assertion half belongs to sibling a4e7c1a3.

Note: the exploration summary that fed this spec was cut off at 8000 characters, partway through the test-placement paragraph (V6 discussion). It over-ran its brief. Nothing below depends on the missing text. The tier rule used here is taken from §3 V1 of the governing spec as quoted before the cut.

## Precondition

This worktree already contains the V3 dependencies: `board.run_brd` at `src/agent_manager/board.py:168`, which all six public functions call, and `FakeBoard` plus the `fake_board` fixture at `tests/conftest.py:83-405`. This subtask only uses them. It must not re-add or reshape the seam or the fake.

## Scope

The only file that changes is `tests/test_orchestrate.py`. Line numbers below refer to this worktree.

- **`project` fixture (`:718-747`).** Keep everything git-related exactly as it is: `XDG_DATA_HOME` into `tmp_path`, `git init -b main`, the user/gpgsign config, and the `README.md` "base" commit. Remove the `brd init --name temp-board` subprocess (`:738-744`) and the commit that followed it, `git add -A` + `commit -m "brd init"` (`:745-746`). Without `brd init` there is nothing left to commit, so `git commit` would fail. No test reads commit counts or `.brd` files (checked: no `rev-list`, `log`, `HEAD~` or `.brd` references in the file). The fixture also depends on `fake_board`, so every test that uses `project` gets a fresh, empty `FakeBoard` installed as `board.run_brd`. It still returns the repo `Path`.
- **`_add_card(root, title, parent=None)` (`:700-705`).** Same signature and same return value (a card id `str`). It seeds the active `FakeBoard` with `add_card(title, parent_id=parent)` and runs no `brd add` subprocess.
- **`_block(root, card_id, blocker)` (`:708-715`).** Same signature. It appends `blocker` to the seeded card's `blocked_by` in the active `FakeBoard`, and runs no `brd block` subprocess.
  - This is the "equivalent fake write" the card allows.
  - Passing `blocked_by=` to `add_card` cannot cover every call site. For example, story-on-story blocks in `_milestone` (`:774-776`) and at `:3430-3431` are added after both cards exist.
  - It must never write `status="blocked"`, because `blocked` is derived.
  - It is not a `FakeBoard.writes` entry, because seeding is not recorded.
- **`_milestone` (`:750-777`).** Signature, card titles, chain shape and return dict all stay the same. It already goes through `_add_card`/`_block`, so it needs no edits beyond what those helpers change.
- **Call sites stay untouched.** This covers the direct `_add_card`/`_block` callers at `:2631` and `:3422-3431` and every `_milestone(project, ...)` caller.
  - The helpers have to find the active `FakeBoard` without new parameters. How they do that is left to the plan stage. Two options: read it from where `project` stashed it, or take it from `board.run_brd`.
  - Under `--import-mode=importlib`, conftest classes cannot be imported by test modules (`conftest.py:148-149`). So `isinstance(..., FakeBoard)` is not available.
- **Not in scope:**
  - Any assertion. The comment-body assertions at `:5251,5282,5416,5446-5450,5574,5707,5843,5883-5884,6006` and similar stay as they are; they belong to a4e7c1a3.
  - The `requires_git`/`requires_brd` skipif decorators (124 `requires_brd` uses), which V1's marker-driven skip replaces. No markers are added.
  - `tests/conftest.py`, `board.py`, `test_cli.py`, `tests/e2e/*`, `steps/test_rollup.py`, `test_board.py`, `harness/test_launcher.py`, and pyproject markers.

## Observable behavior

- No test that uses `project`, `_milestone`, `_add_card` or `_block` starts a `brd` process.
  - All board traffic from `run_milestone` and from test bodies goes through `board.show/tree/roots/set_status/comment_add/comment_list` into the `FakeBoard`. That includes `_branch` (`:780-781`) and the `_comments`/`_keys` helpers.
  - Real `git` subprocesses stay.
- Card order is unchanged. `_milestone` still chains subtasks with `_block`, so census order never depends on timestamps. `FakeBoard` also hands out strictly increasing `created_at` values in seeding order.
- Tests that monkeypatch `board.*` functions to raise `BoardError` (`:1978`, `:2009`, `:5657`) still replace those functions above the seam, so their behavior does not change.

## Error paths

- `FakeBoard` raises `AssertionError` for any argv outside its six shapes. If any test path still reaches `brd init/add/block`, or anything else unmodeled, the failure names the argv. That is a conversion bug in this subtask, so fix the seeding. Do not widen the fake.
- `_refuse_links` raises `AssertionError` on `[[` in seeded descriptions and in `comment add` bodies. The fixture titles contain no `[[`.
- **Known open blocker.** This must be checked first and escalated if it is confirmed.
  - `comments._ref` (`src/agent_manager/comments.py:264-266`) writes `[[card_id]]` backlinks into run-end comment bodies (escalated/parked lists, integrate-failure lines).
  - `FakeBoard._comment_add` refuses those bodies with `AssertionError`.
  - `comments.flush` only catches `BoardError`/`LockTimeoutError` (`comments.py:431`), so the error propagates.
  - Any converted test whose run posts such a body would therefore change outcome. Examples are the tests asserting `escalated: [[...]]` / `parked: [[...]]`, plus any escalated or paused run with parked cards even where the body is not asserted.
  - That breaks the "identical pass/fail" requirement.
  - The fix lives in `FakeBoard` (V3's card 19b53ab3), which is out of this subtask's scope. Assertions belong to a4e7c1a3.
  - If the before/after diff shows it, stop and escalate with the list of affected test names. Do not edit assertions, do not relax `_refuse_links` unilaterally, and do not exclude tests.

## Tests and verification

This is a fixture swap. It adds no tests and removes none, and the set of test names in `tests/test_orchestrate.py` stays identical.

Tier, per the V1 placement rule in §3 of the governing spec:
- Every test riding `project` moves from the de-facto real-brd steps tier into the `git` tier: real git in `tmp_path`, no brd, no claude.
- The `FakeDriver`-only tests that never touch `project` stay in `unit`.
- This subtask does not apply the `git` marker. V1's collection-hook auto-mark owns that.

Verification, as prescribed by §6 V4/V5 and the card:
1. Before the change, record pass/fail per test name from `uv run pytest tests/test_orchestrate.py -v`, plus its wall time. The spec cites roughly 445s.
2. After the change, run the same command. The per-name outcomes must be identical, and the result must be recorded together with the new wall time, which should be well under 445s.
3. Full `uv run pytest` is green.
