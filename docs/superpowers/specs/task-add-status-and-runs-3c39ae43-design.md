# Subtask 3c39ae43 — Add `status` and `runs`

Parent story: 1a46ab5a "The CLI: run, status, logs, resume" (milestone 352e955b). Depends on cbe34d00 (`run --card`, done). Narrows design §10 "CLI surface" (`agent-manager status [<run-id>]`, `agent-manager runs`) and §9 (the Run → StoryRun → SubtaskRun → PhaseRun → Attempt tree) to two read-only commands.

## Scope

Two new Typer commands in `src/agent_manager/cli.py`, plus the one projection query they need in `src/agent_manager/store.py`.

- `agent-manager status [<run-id>] [--repo-dir .] [--pretty]` — renders one run as a table of story / subtask / phase / attempt / state, read from the SQLite projection at `paths.project_db_path(resolve_repo_dir(repo_dir))`. With no `<run-id>`, it renders the most recent run recorded for that project.
- `agent-manager runs [--repo-dir .] [--pretty]` — lists that project's run history, newest first.
- A project-scoped read on the existing shared `runs` table (`store.py:26-36`), independent of any single run id: a listing of run summaries ordered newest first, and the latest run's id derived from that same listing. It must be reachable without `Store.open(root, run_id)`, which requires a run id the caller does not yet have (`store.py:328-330`), and it must open the database through the existing `open_db(root)` (`store.py:95-107`). No second database, no new tables, no new columns.

Not in scope: `logs` (sibling be23d3c0), `resume` (sibling 5524ae72), `watch`/`retry`/`cancel`, milestone orchestration (census, levels, parallel stories, integrate), non-Claude harnesses, any write to the projection or the journal, and any journal reading — `status` reads the projection, which is what the projection exists for.

## Observable behaviour

Both commands honour the existing envelope helpers unchanged: `ok_envelope` / `error_envelope` / `render` (`cli.py:102-121`), JSON on one line by default and indented under `--pretty`. Both are read-only, so the only exit codes are `0` on success and `EXIT_ERROR` (3) on a handled failure; `EXIT_ESCALATED` (1) is not used — an escalated run reported by `status` is a truthful reading, not a failure of the command. `2` stays Typer's.

`--repo-dir` is resolved through `resolve_repo_dir` before anything is derived from it (`cli.py:67-79`), exactly as `run_card` does.

`status` succeeds with `ok: true` and a payload that carries the run's own identity (id, workflow, repo_dir, base_branch, branch_prefix, status, started_at) and the nested walk of the §9 tree as `Store.load_run` already assembles it (`store.py:579-661`): stories in `position` order with `card_id`, `title`, `level`, `status`, `tip_branch`; their subtasks with `card_id`, `branch`, `base_branch`, `status`, `worktree_path`; their phases with `name`, `kind`, `status`, `started_at`, `ended_at`, `detail`; their attempts in `n` order with `n`, `status`, `exit_code`, `duration`, `tokens_in`, `tokens_out`, `cost`. Field names are `models.py`'s and are not renamed for display. A run whose walk died on its first phase renders what exists — a story and a subtask with no phases is a legitimate reading, not an error.

The "table" of §10 is a projection of that tree into flat rows, one row per attempt (and one row per phase with no attempts yet, so a `pending` or `started` deterministic phase is visible), each row carrying the story, subtask, phase, attempt number and state it belongs to. The row-building is a pure function over a `models.Run` and returns plain data; the command only renders it inside the envelope.

`runs` succeeds with `ok: true` and a payload listing every run in the project's database, newest first, each entry carrying at least `id`, `workflow`, `status` and `started_at`. Ordering is by `started_at` descending with the run id as the tiebreaker, and both are sortable UTC strings by construction (`cli.py:41-42`, `RUN_ID_TIME_FORMAT`). An empty database is `ok: true` with an empty list, not an error: a project that has never been run is a fact, not a fault.

`status` with no argument resolves the run id from that same newest-first listing and then renders it, so the two commands can never disagree about which run is "most recent".

## Error paths

All handled failures go through the existing `HANDLED` tuple pattern (`cli.py:324-337`): an `ok: false` envelope naming the exception class and message, exit 3. Anything outside the tuple is a bug and must crash with its real traceback.

- `--repo-dir` naming something that is not a directory → `RepoDirError` (already in `HANDLED` via `CliError`), exit 3. Applies to both commands.
- `status <run-id>` for a run id absent from this project's projection → a new `CliError` subclass (so it inherits the envelope and exit 3) whose message names the run id and the project it was looked for in. `Store.load_run` returns `None` for this (`store.py:585-587`) and `None` must not reach the renderer.
- `status` with no argument against a project with no runs at all → the same class of refusal, distinct message: there is no most-recent run to default to. This is the one case where an empty history is an error, because the command was asked for a run and there is none.
- A projection row that no longer validates against `models` → pydantic's `ValidationError`, which is not in `HANDLED` and must crash loudly: a drifted projection is a bug in this program, and §9's answer is to rebuild from the journal, not to print half a tree.

The database is opened read-only in effect — no `record_*` call appears in either command — and is closed on every path, including the failure paths, the way `run_card` closes its store in a `finally` (`cli.py:320-321`).

## Tests

Tier names are design §14 (`docs/superpowers/specs/2026-09-23-agent-manager-design.md:477-492`) as applied by `tests/test_cli.py:1-13` and `tests/test_store.py:1-11`.

Unit tier (pure, no filesystem, no subprocess) — in `tests/test_cli.py`:

1. The flat row builder turns a hand-built `models.Run` with two stories, subtasks, phases and attempts into rows in tree order, one per attempt.
2. The row builder emits a row for a phase with no attempts yet, so an in-flight or pending phase is not invisible.
3. The row builder on a `models.Run` with no stories yields no rows and does not raise.
4. The `status` payload of a hand-built `models.Run` survives `render` — it carries `Path` values (`repo_dir`, `worktree_path`) and `render`'s `default=str` must keep them from turning a successful read into a traceback.

Steps tier (real temp SQLite projection under `XDG_DATA_HOME` redirected to `tmp_path`, real rows written via `Store.record_*`, no mocks, no harness, default suite) — in `tests/test_store.py` for the query, `tests/test_cli.py` for the commands:

5. The new listing returns every run recorded in the project's database, newest first, and only those runs (a second project root, hence a second database, is not visible).
6. The new listing on a database with no runs returns an empty list.
7. The latest-run resolution picks the newest of three recorded runs, and agrees with the head of the listing.
8. `status <run-id>` on a projection populated with a run, story, subtask, phases and attempts prints `ok: true` and a payload whose rows cover every attempt recorded, exit 0.
9. `status` with no run id renders the most recently recorded run of that project.
10. `status <run-id>` for an unknown id prints `ok: false` with the refusal's class name, exit 3.
11. `status` with no run id against an empty project prints `ok: false`, exit 3.
12. `status --repo-dir <not a directory>` prints `ok: false` with `RepoDirError`, exit 3 — asserted for `runs` as well.
13. `runs` prints the project's history newest first, exit 0; with no runs it prints `ok: true` and an empty list, exit 0.
14. `--pretty` indents the envelope for both commands and the parsed JSON is identical to the unpretty form.
15. `status` on a run recorded with a story and subtask but no phases (a run that died before its first phase) prints `ok: true` with an empty row list — the §9 reason `run_card` writes those rows before the walk starts (`cli.py:228-230`).

No Engine tier test is needed here: neither command constructs a runner, a workflow or a dispatch. No adapter, launcher or end-to-end test is in this subtask's scope.
