# 2.2 `am run --board --detach` — design (card 03f027ea)

Status: proposed. Subtask of story 22561886. Blocked by 203a9a5e ("2.1 Split the
board pre-flight from the board run"), which is in the tree:
`orchestrate.preflight_board`, `orchestrate.run_board_engine` and
`orchestrate.BoardPreflight` exist and `run_board` composes them.

Parent spec: `docs/superpowers/specs/2026-10-04-board-detach-and-milestone-stacking-design.md`
(cited below as **P**, by section and line).

## 1. Goal

`am run --board --detach` runs the board's whole pre-flight in the foreground,
then hands the board run to a detached child in its own session and returns at
once with one envelope (P §2, lines 76-96; P Goals, lines 24-25).

## 2. Inherited constraints

| Constraint | Source |
|---|---|
| Pre-flight in the foreground: argument checks, board read, cycle check, prefix derivation, `MilestoneBlockersError`, up-front claim check. Any refusal is the usual `{"ok": false, ...}` envelope, exit 3, nothing starts. | P §2 lines 78-82 |
| Detached child: own session, stdin closed, owns the whole board run. | P §2 line 83 |
| Child output to `<data dir>/boards/<stamp>-<digest>.log`, mode 0600; `<digest>` is the repository digest used elsewhere. | P §2 lines 84-85 |
| Final board payload to `<data dir>/boards/<stamp>-<digest>.report.json` when the run ends. | P §2 line 86 |
| One envelope, exit 0: `{"ok": true, "data": {"board": true, "detached": true, "pid", "log", "report", "levels"}}`. | P §2 lines 87-88 |
| No `run_id`: each milestone's run is created when that milestone is dispatched. | P §2 lines 89-91; P Non-goals line 33 |
| `--detach` with `--board --dry-run` stays refused; `--detach` without `--board` unchanged; README sentence refusing `--detach` with `--board` removed. | P §2 lines 92-93 |
| A claim taken after the up-front check becomes an `escalated` entry and shows in the report file. | P §2 lines 94-96 |
| All JSON changes additive; journal and watch schema stay 1. `--detach` with `--board` stops being a usage error. | P Compatibility lines 102-103; card |
| Tests: refusals in the foreground before detaching; child survives the parent; log and report exist with mode 0600; envelope shape. | P Testing lines 116-117 |
| README `--detach` section documents `--board`. | P Testing lines 118-119 |
| `detach.py` imports only `paths` and the stdlib (pinned by `tests/test_detach.py::test_detach_module_imports_only_paths_and_the_stdlib`). | `src/agent_manager/detach.py:6` |
| Test tiers as in `CLAUDE.md` "Test tiers": unit has no subprocess; a real fork belongs in `e2e_fake`. | `CLAUDE.md` |

## 3. Observable behavior

### 3.1 Usage errors (exit 2, Typer)

- `am run --board --detach` is no longer a usage error. The `BOARD_DETACH`
  refusal (`"--detach applies to --card and --milestone, not --board"`) is
  removed from `cli._check_run_targets`.
- `am run --board --dry-run --detach` is still refused with
  `"--dry-run writes nothing and cannot be detached"` and hint
  `'--detach' / '--dry-run'` (the existing `detach and dry_run` check already
  runs before the board-specific branch, so it applies unchanged).
- Every other usage error with `--board` is unchanged and is checked with or
  without `--detach` (`--board` with `--card`/`--milestone`, blank
  `--branch-prefix`, `--max-concurrent` below 1). A usage error forks nothing
  and writes nothing under the data directory.

### 3.2 Foreground pre-flight refusals (exit 3)

`am run --board --detach` first calls `orchestrate.preflight_board` with exactly
the arguments the foreground `--board` branch passes to `run_board`
(`repo_dir`, `base_branch`, `branch_prefix_of=board_prefix_of(branch_prefix)`,
`max_concurrent=lanes`). Every refusal it raises — `ValueError` (bad
arguments, a bad or shared prefix), `dag.DependencyCycleError`,
`orchestrate.MilestoneBlockersError`, `cli.ClaimedError`, `board.BoardError`,
`runs.RepoDirError` and any other `HANDLED` error — reaches the existing
`except HANDLED` in `cli.run` and prints `{"ok": false, "error": {"type",
"message"}}` with exit 3, exactly as the foreground board run does.

After any such refusal:

- the detacher was never called (no fork);
- `<data dir>/boards/` does not exist (the boards directory is created only
  after pre-flight passes);
- no run row, run directory or lease exists (inherited from `preflight_board`).

A `GitError` that `_local_branch_exists` lets through (not exit 1) propagates
exactly as in a foreground board run; this card does not change its handling.

### 3.3 Hand-off

Once pre-flight passes, in this order:

1. The stamp is the injected clock's `now` formatted with
   `runs.RUN_ID_TIME_FORMAT` (`%Y%m%dT%H%M%SZ`, e.g. `20261004T090000Z`). The
   clock defaults to `orchestrate._utcnow`.
2. The digest is the full 64-hex sha256 of the resolved repository root, the
   same value `paths.project_db_path` uses for `projects/<digest>.db`. It is
   computed from `pre.root`.
3. The stem is `<stamp>-<digest>`. The log is `<data dir>/boards/<stem>.log`,
   the report `<data dir>/boards/<stem>.report.json`.
4. The log is created **exclusively** and empty, mode exactly 0600 whatever the
   umask. If a file with that name already exists (a second board detach on the
   same repository in the same second), the command refuses with
   `orchestrate.BoardLogExistsError` (a `ValueError` subclass, so `HANDLED`):
   exit 3, envelope naming the existing log path, detacher not called, the
   existing log untouched.
5. The detacher is called once with `(body, log)`. If it raises, the error
   propagates (a fork failure is a bug-class `OSError`, not `HANDLED`); the
   empty log stays behind and nothing else was written.
6. The returned `Spawned.go()` is called immediately. There is no lease to
   re-point, so `abort` is never called on the success path.
7. The command prints
   `{"ok": true, "data": {"board": true, "detached": true, "pid": <child pid>,
   "log": "<abs log path>", "report": "<abs report path>", "levels": pre.levels_payload}}`
   and exits 0. `set(data)` is exactly those six keys; there is no `run_id`,
   no `ok` inside `data`, no `milestones`. `log` and `report` are strings.
   `levels` is the same list the foreground payload's `levels` would carry,
   `[{"level": i, "milestones": [ids]}]`, and is `[]` on a board with nothing open.
8. The exit code is 0 whenever the board was handed off, even if a milestone
   later escalates (the existing `if detach_run: return` in `cli.run` skips the
   board escalation check).

The report file does not exist when the command returns.

### 3.4 The child (`body`)

The child is started by `detach.fork_detacher` as today: `setsid` (its own
session, `os.getsid(pid) == pid`), stdin from `/dev/null`, stdout and stderr
appended to the log, blocked until the go byte. On go, `body` runs:

- `orchestrate.run_board_engine(pre, commands=..., allow_no_verification=...,
  runner_factory=..., driver=..., clock=..., control_interval=...)` on the very
  `pre` the parent built (fork, no re-read of the board, no second claim check).
  Each milestone's run is created when it is dispatched, with its own run row,
  journal and lease held by the child's pid.
- On return, the report is written as `cli.render(cli.ok_envelope(payload))`
  plus a newline: the exact envelope a foreground `am run --board` would have
  printed (compact, sorted keys). A payload with `ok: false` (an escalated or
  blocked milestone, including the late-claim race of P §2 lines 94-96) is
  still an `ok_envelope`, as in the foreground.
- If the engine raises a `HANDLED` error, the report is
  `cli.render(cli.error_envelope(error))`.
- Any other exception writes no report; its traceback is in the log
  (`fork_detacher._child` prints it) and the child exits 1.
- The report is written atomically (temp file in `<data dir>/boards/`, fsync,
  `chmod 0600`, `os.replace`): a reader sees no file or a whole one, and no
  temp file remains afterwards.

Nothing holds a sqlite connection or heartbeat thread across the fork:
`preflight_board` opens no store (its claim check opens and closes its own
connection before returning).

### 3.5 Files

- `<data dir>` is `paths.data_dir()` (`$XDG_DATA_HOME/agent-manager` or
  `~/.local/share/agent-manager`).
- `<data dir>/boards/` is created on first use with `mkdir(parents=True,
  exist_ok=True)`, only after pre-flight passes.
- Both files are mode 0600.
- Nothing is written inside the repository worktree.

### 3.6 Help and README

- `--detach` Option help: says it works with `--card`, `--milestone` and
  `--board`, and that a board's output goes to
  `<data dir>/boards/<stamp>-<digest>.log` and its final envelope to
  `<stamp>-<digest>.report.json`.
- `_check_run_targets` docstring: drop the "and with `--board`, whose run was
  not split..." clause.
- `RUN_EXAMPLES`: add `am run --board --verify "uv run pytest" --detach`.
- README:
  - line 80: the refusal list ends "`--detach` with `--dry-run`" (no "or with
    `--board`").
  - line 89: "`--detach` works with `--card`, `--milestone` and `--board`."
  - line 100: "`--detach` with `--dry-run` is refused as a usage error (exit 2)."
  - add one bullet to the `--detach` section describing the board form in
    prose: the board's pre-flight runs in the foreground, the envelope's keys
    `board`, `detached`, `pid`, `log`, `report`, `levels` and no `run_id`, the
    file locations under `<data dir>/boards/` with mode 0600, and that each
    milestone's run is found with `am runs` / `am watch --all`. **No fenced JSON
    example in the `--detach` section**: `tests/test_readme.py::test_detach_section_documents_envelope`
    asserts that section has exactly one JSON example (the card/milestone one).
  - line 159: drop "and `--detach` with `--board`" from the board usage errors.

## 4. Interfaces (handed to the planner)

- `paths.project_digest(root: Path) -> str` — public; the existing
  `_project_digest` body (sha256 of `str(root.resolve())`). The private name's
  callers in `paths.py` switch to it (or it stays as an alias); values unchanged.
- `paths.boards_dir() -> Path` — `data_dir() / "boards"`, created.
- `detach.BOARD_LOG_SUFFIX = ".log"`, `detach.BOARD_REPORT_SUFFIX = ".report.json"`.
- `detach.create_board_log(stem: str) -> Path` — `boards_dir() / f"{stem}.log"`,
  `O_WRONLY | O_CREAT | O_EXCL | O_APPEND`, then `fchmod 0600`; raises
  `FileExistsError` if present.
- `detach.board_report_path(stem: str) -> Path` — `boards_dir() / f"{stem}.report.json"`, creates nothing but the directory.
- `detach.write_board_report(path: Path, text: str) -> Path` — same atomic
  0600 write as `write_report`; the two share one private helper
  `_write_atomically(target: Path, text: str)`. `write_report`'s behaviour is
  unchanged.
- `orchestrate.BoardLogExistsError(ValueError)` — message names the log path.
- `orchestrate.detach_board(*, repo_dir, base_branch, branch_prefix_of,
  detacher: detach.Detacher, commands=(), allow_no_verification=False,
  max_concurrent=1, runner_factory=None, driver=None, clock=_utcnow,
  control_interval=control.CONTROL_POLL_SECONDS) -> dict[str, Any]` — §3.2-3.4.
  Placed next to `run_board`. Uses `cli.render`, `cli.ok_envelope`,
  `cli.error_envelope`, `cli.HANDLED` through the `cli.` prefix at call time
  (circular import, as `preflight_board` does with `cli.refuse_claimed`). Does
  **not** use `cli.hand_off_to_child` / `cli.run_detached_child` (they need a
  run id, Store and Lease a board does not have).
- `cli.run`: a new branch `elif whole_board and detach_run:` placed after
  `whole_board and dry_run` and before the plain `whole_board` branch, calling
  `orchestrate.detach_board(repo_dir=..., base_branch=...,
  branch_prefix_of=board_prefix_of(branch_prefix), commands=list(verify),
  allow_no_verification=..., max_concurrent=lanes, detacher=detach.fork_detacher)`,
  both names read at call time so tests can patch them.

## 5. Tests

| # | Test | File | Tier | Why this tier |
|---|---|---|---|---|
| 1 | `_check_run_targets(board=True, detach=True, card=None, milestone=None, branch_prefix=None, dry_run=False)` returns `None` (also with `branch_prefix="p"` and `max_concurrent=2`); the `BOARD_DETACH` row is removed from `test_check_run_targets_refuses_detach_with_dry_run_or_board` and the constant deleted. | `tests/test_cli.py` (detach section ~10970-11020) | unit | pure function |
| 2 | `_check_run_targets(board=True, dry_run=True, detach=True, ...)` raises `DRY_RUN_DETACH` with hint `'--detach' / '--dry-run'`. | `tests/test_cli.py` | unit | pure function |
| 3 | CLI: `["--board","--dry-run"]` + `--detach` is exit 2, no `"ok"` on stdout, `"detached"` in output, `_FakeDetacher.calls == []`, `<data dir>/boards` absent. Replaces the `["--board"], "applies"` row of `test_detach_with_dry_run_or_board_is_a_usage_error_that_detaches_nothing` (renamed to drop "or_board"). | `tests/test_cli.py` | unit | `CliRunner`, fake detacher, `_forbid_board_paths` |
| 4 | CLI: each pre-flight refusal (`ValueError`, `DependencyCycleError`, `MilestoneBlockersError`, `ClaimedError`, `BoardError`) raised by a patched `orchestrate.preflight_board` → exit 3, `{"ok": false, "error": {"type": <class name>, ...}}`, `_FakeDetacher.calls == []`, `<data dir>/boards` absent. | `tests/test_cli.py` | unit | `CliRunner`, patched pre-flight, fake detacher |
| 5 | CLI wiring: with `orchestrate.detach_board` patched to a recorder returning a fixed payload, `am run --board --detach --verify X --max-concurrent 3 --branch-prefix p --base-branch main` calls it once with `detacher is detach.fork_detacher` (patched sentinel), `max_concurrent=3`, `commands=["X"]`, `base_branch="main"`, a `branch_prefix_of` equal in effect to `board_prefix_of("p")`; prints `ok_envelope(payload)`; exit 0; `orchestrate.run_board` never called. | `tests/test_cli.py` | unit | patched seam |
| 6 | CLI: exit 0 even when the recorder's payload would read as escalated under the board check (e.g. carries `milestones: [{"status": "escalated"}]`). | `tests/test_cli.py` | unit | patched seam |
| 7 | `detach_board` refusals: with `board_seams`, a cycle, a two-open-blocker milestone, a claim conflict and `max_concurrent=0` each raise the same error `preflight_board` raises; the fake detacher is never called; `paths.data_dir() / "boards"` does not exist. | `tests/test_orchestrate.py` | unit | `board_seams` fakes (no git, brd, harness), `XDG_DATA_HOME` in `tmp_path` |
| 8 | `detach_board` success: fixed clock `2026-10-04T09:00:00Z`; returned dict has exactly `{board, detached, pid, log, report, levels}`, `board is True`, `detached is True`, `pid == FAKE_CHILD_PID`, `log == str(boards/"20261004T090000Z-<project_digest(root)>.log")`, `report` the matching `.report.json`, `levels == pre.levels_payload`; the log exists, empty, mode 0600; the report does not exist yet; fake detacher called once with that log and `events == ["go"]`; `board_seams.runs.calls == []` (engine not run in the parent). | `tests/test_orchestrate.py` | unit | fake detacher keeps the body |
| 9 | `detach_board` body: running the captured body inline writes the report as `cli.render(cli.ok_envelope(run_board_engine-payload)) + "\n"` (compare to `run_board` with the same seams), mode 0600, no temp file left in `boards/`. | `tests/test_orchestrate.py` | unit | body run in-process |
| 10 | `detach_board` body with a milestone that escalates (fake run raises / returns escalated): report is an `ok_envelope` whose `data.ok` is false and whose entry is `escalated`. | `tests/test_orchestrate.py` | unit | fakes |
| 11 | `detach_board` body whose engine raises a `HANDLED` error (patch `orchestrate.run_board_engine` to raise `ValueError("x")`): report is `error_envelope`, type `ValueError`. Engine raising `RuntimeError`: body raises it and no report exists. | `tests/test_orchestrate.py` | unit | patched engine |
| 12 | `detach_board` on an empty board: `levels == []`, detacher still called (the child reports `{"ok": true, "board": true, "levels": [], "milestones": []}` when its body runs). | `tests/test_orchestrate.py` | unit | fakes |
| 13 | `detach_board` collision: a pre-existing `<stem>.log` under `boards/` (same fixed clock) raises `BoardLogExistsError` naming the path, detacher not called, existing file contents unchanged. | `tests/test_orchestrate.py` | unit | filesystem in `tmp_path` only |
| 14 | `detach.create_board_log` makes an empty 0600 file under `boards_dir()`, 0600 under umask 0, raises `FileExistsError` on a second call; `write_board_report` writes one line at 0600, replaces an earlier report whole, leaves no temp file; `write_report` tests still pass unchanged. | `tests/test_detach.py` | unit | file I/O in `tmp_path`, no subprocess |
| 15 | `paths.project_digest(root)` equals the stem of `project_db_path(root)` and is 64 hex chars; `boards_dir()` is `data_dir()/"boards"` and exists. | `tests/test_paths.py` | unit | pure path functions |
| 16 | Real fork: `am run --board --detach` (real `am` process via the `am` fixture, `milestone_board`, `fake_claude_bin`, `hold` parking a1's implement, `detached_pids` killing the session at teardown). Asserts exit 0, `set(data) == {"board","detached","pid","log","report","levels"}`, log path `paths.data_dir()/"boards"/f"{stamp}-{paths.project_digest(root)}.log"` (stamp matched by regex `^\d{8}T\d{6}Z$`), log mode 0600; after the parent exited, the held marker appears, `control.pid_alive(pid)` and `os.getsid(pid) == pid`; `am runs` lists one run whose lease `pid == pid` and `live is True`; after `hold.release(a1)`, the report appears with mode 0600, `final["ok"] is True`, `final["data"]["board"] is True`, the one milestone entry is `done`. Waits by markers/files, never sleeps. | `tests/e2e/test_detached_run.py` | `e2e_fake` | real fork, real `git`/`brd`, fake `claude` — the only place "child survives the parent" is observable |
| 17 | README: `_section("Running detached with \`--detach\`")` mentions `--board`, `boards/`, `.report.json`, `report` and still has exactly one JSON example; README contains no "`--detach` with `--board`" and no "or with `--board`" refusal text. | `tests/test_readme.py` | unit | text only |

`uv run pytest` (unit + git) stays green; test 16 runs under `uv run pytest -m e2e_fake`.

## 6. Review focus (inputs the spec implies but are easy to miss)

1. Two board detaches on the same repository in the same second — must refuse
   (test 13), never share a log or clobber a report.
2. A refusal must leave `<data dir>/boards/` absent, not just empty — the
   directory is created after pre-flight (tests 4, 7).
3. A board with nothing open — still detaches and the report is the ordinary
   empty-board payload (test 12).
4. An escalated milestone must not change the parent's exit code from 0
   (test 6) and must show as `data.ok: false` in the report (test 10).
5. A restrictive or permissive umask must not change the 0600 modes (test 14).

## 7. Out of scope

- Milestone stacking / `milestone_bases` / `MilestoneBlockersError` itself
  (sibling cards; already in the tree).
- The pre-flight/engine split (card 203a9a5e, done).
- `am watch --all` following a detached board, pause/resume per milestone
  (P Testing line 114-115; a separate e2e_fake scenario card).
- A board-level run record, run id, lease or journal (P Non-goals line 33).
- Moving board files under `<data dir>/runs/` (P Open questions lines 125-126).
- Any change to `--card --detach` / `--milestone --detach` behaviour or to
  `cli.hand_off_to_child` / `cli.run_detached_child`.
- Cleaning up old board logs/reports.
