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
