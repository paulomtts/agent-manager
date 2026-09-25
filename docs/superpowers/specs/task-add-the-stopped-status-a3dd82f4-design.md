# Add the stopped status (card a3dd82f4)

Parent story: 9bfb5ac2 "Stop cleanly: a stopped status and a cooperative stop" (Decision P4 of `2026-09-24-parallel-stories-design.md`, lines 66-75). Sibling 0d8b7c9a builds on this card.

## Scope

This card adds `stopped` as a recorded status and decides on purpose how every place that enumerates or branches on status treats it. Nothing produces `stopped` yet, so there is no behaviour change on any path that runs today. Per P4, `stopped` is not `failed`: relaunching the same command continues a stopped subtask through the existing idempotence and Plan-Hash re-entrancy.

In scope:

- `src/agent_manager/models.py`: `Status` becomes `Literal["pending", "started", "done", "failed", "escalated", "stopped"]`. Run, StoryRun, SubtaskRun and PhaseRun pick it up because they all use `Status`. `AttemptStatus` does not change.
- `src/agent_manager/engine.py`: `SubtaskSummary.status` widens from `Literal["done", "escalated"]` to `Literal["done", "escalated", "stopped"]`. No engine code produces it.
- `src/agent_manager/cli.py`, `select_resumable`: when no subtask is `started` and at least one is recorded `stopped`, the refusal message adds a stopped-specific remedy. It says a stopped subtask is continued by relaunching the milestone with the same `agent-manager run --milestone <id>` command. It must not suggest `retry`. The existing `found: card=status` listing and the `status <run>` pointer stay. When there are no stopped subtasks the message is unchanged. The docstring is updated to name `stopped` among the zero-started cases.
- Audit decisions. These places get no code change, only a comment where one helps a later reader:
  - `cli.status_rows`: `state` is `phase.status` or `attempt.status` and prints verbatim, so `stopped` shows with no change. A test pins this.
  - Exit codes. `run` (cli.py ~1040-1043) checks `payload["status"] == "escalated"` for a card and `payload.get("escalated") is True` for a milestone. `resume` (cli.py ~1361) checks `payload["status"] == "escalated"`. All three are strict equality, so `stopped` exits 0 with an `{"ok": true, ...}` envelope and never `EXIT_ESCALATED`. This stays as it is, and a test pins it.
  - `run --card` (cli.py ~789-803) and `resume` (cli.py ~1291-1307) copy `summary.status` into the run, story and subtask rows and into the payload `status`. A `stopped` summary is therefore recorded as `stopped` at all three levels and reported as `"status": "stopped"`. This is the intended flow, because the run did not finish and it did not fail. No code change.
  - `orchestrate.py` (~322): `if status != "done"` records the subtask, story and run as `escalated` and returns `escalated: True`. This is the one branch that would misclassify `stopped`, but the engine cannot return `stopped` until 0d8b7c9a, and the milestone treatment is owned by that card and later ones. It stays as it is, with a short comment noting that `stopped` must be handled here once the engine can produce it. It gets no behaviour change and no test in this card.
  - `store.py`: there is no CHECK constraint on status, so no schema change is needed. `load_run` and `rebuild_from_journal` validate through the pydantic models and accept `stopped` once `Status` does.

Out of scope: the `should_stop` callable, the pre-phase check, the "stopped before <phase>" detail, the `drive_subtask` and `Driver` pass-through, and the fake-runner stop tests, all owned by 0d8b7c9a. Also out of scope: the P5 `stopped` report key, Ctrl-C handling, milestone-aware `am resume`, watch/retry/cancel, and the rest of addendum section 5.

## Observable behaviour

- Models validate `status="stopped"` and still reject unknown strings.
- A run whose rows or journal lines carry `stopped` loads and rebuilds without error, and the value is preserved.
- `am status <run>` shows `stopped` in the state column for a stopped phase.
- `am resume <run>` on a run with a stopped subtask and none started refuses with `NotResumableError`, as it does today. The message now also tells the user to relaunch `run --milestone`.
- No command exits with the escalation code because of `stopped` alone.
- The default suite, including tests/e2e, stays green. `--max-concurrent 1` is unchanged.

## Error paths

- An invalid status string in the store or journal still raises from rebuild. The existing test at tests/test_store.py ~669 keeps passing because it checks a subset of the names.
- The resume refusal for zero-started runs keeps its current wording when no subtask is `stopped`.

## Tests

The tier comes from section 14 of `2026-09-23-agent-manager-design.md`: pure functions get unit tests, and tests mirror source files. None of these tests belong in e2e.

1. `tests/test_models.py` (unit, pure models): SubtaskRun, StoryRun and PhaseRun each accept `status="stopped"`. Run is covered too, since it shares the same `Status`.
2. `tests/test_models.py` (unit): an unknown status string is still rejected, showing the literal only grew by one value.
3. `tests/test_store.py` (store tier, temp directory like the existing store tests): a run recorded with a `stopped` subtask, story and phase (via `_record_full_run` or a variant) comes back as `stopped` from `load_run`.
4. `tests/test_store.py` (store tier): the same run rebuilt through `rebuild_from_journal` keeps `stopped` at each level.
5. `tests/test_cli.py` (unit, pure `status_rows`): a run with a stopped phase yields a row whose state is `stopped`.
6. `tests/test_cli.py` (unit, pure `select_resumable` with `_pure_run`, `_pure_story` and `_pure_subtask`): with one stopped subtask and none started, it raises `NotResumableError`. The message contains `card=stopped` and names relaunching `run --milestone`, and it does not contain `retry`.
7. `tests/test_cli.py` (unit): with zero started subtasks and none stopped (for example done and escalated), the message has no stopped remedy, which is a regression guard.
8. `tests/test_cli.py` (CLI tier, CliRunner or the exit-code helper): a card-run or resume payload with `status: "stopped"` maps to exit 0 with an `ok: true` envelope, not `EXIT_ESCALATED`. If the exit decision is inline in the command body and cannot be reached without a real stopped summary, drive it with an injected fake runner or driver whose summary is `stopped`, using the existing CLI test seams. Do not add engine stop plumbing.
