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

---

# `am run --board --detach` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `am run --board --detach` runs the board pre-flight in the foreground, hands `run_board_engine` to a forked child in its own session, and prints `{"ok": true, "data": {"board", "detached", "pid", "log", "report", "levels"}}` at once.

**Architecture:** `paths` gains a public `project_digest` and a `boards_dir`. `detach` gains the board's two files (an exclusive 0600 log and an atomic 0600 report, sharing `write_report`'s atomic writer). `orchestrate.detach_board` composes `preflight_board`, the board files and the injected detacher; its child body runs `run_board_engine` on the parent's `pre` and writes the envelope a foreground `am run --board` would print. `cli.run` stops refusing `--board --detach` and dispatches to `orchestrate.detach_board`.

**Tech Stack:** Python 3, Typer, pytest (`uv run pytest`), `os.fork`/`setsid` through the existing `detach.fork_detacher`.

**Spec:** `docs/superpowers/specs/2-2-am-run-board-detach-03f027ea.md` (prepended above). Parent: `docs/superpowers/specs/2026-10-04-board-detach-and-milestone-stacking-design.md`.

## Global Constraints

- `detach.py` imports only `paths` and the stdlib (`tests/test_detach.py::test_detach_module_imports_only_paths_and_the_stdlib`).
- Both board files are mode exactly 0600 whatever the umask; they live under `<data dir>/boards/`, never in the repository.
- `<data dir>/boards/` is created only after pre-flight passes.
- File stem: `<stamp>-<digest>`, stamp = `clock().strftime(runs.RUN_ID_TIME_FORMAT)` (`%Y%m%dT%H%M%SZ`), digest = full 64-hex sha256 of the resolved repo root (`paths.project_digest(pre.root)`).
- Envelope `data` is exactly `{"board", "detached", "pid", "log", "report", "levels"}` — no `run_id`, no `ok`, no `milestones`.
- All JSON changes additive; journal and watch schema stay 1.
- `orchestrate` reads every `cli` name (`render`, `ok_envelope`, `error_envelope`, `HANDLED`) through `cli.` at call time (circular import).
- `detach_board` does not use `cli.hand_off_to_child` / `cli.run_detached_child`.
- Test tiers per `CLAUDE.md`: unit tests spawn no subprocess; the real fork is `@pytest.mark.e2e_fake`.
- README `--detach` section keeps exactly one fenced JSON example.

## Review Focus

1. Two board detaches on the same repository in the same second must refuse with `BoardLogExistsError` and leave the first log untouched — pinned in Task 3 (`test_detach_board_refuses_a_log_that_already_exists_and_forks_nothing`).
2. A pre-flight refusal must leave `<data dir>/boards/` absent, not merely empty — pinned in Task 3 (`test_detach_board_refuses_in_the_foreground_and_forks_nothing`) and Task 4 (`test_a_board_detach_preflight_refusal_is_an_envelope_that_forks_nothing`).
3. A board with nothing open still detaches, and its report is the ordinary empty-board payload — pinned in Task 3 (`test_detach_board_on_a_board_with_nothing_open_still_hands_off`).
4. An escalated milestone (including a claim taken after the up-front check) must not change the parent's exit 0 and must show as `data.ok: false` in the report — pinned in Task 4 (`test_a_board_detach_exits_0_even_when_its_payload_reads_as_escalated`) and Task 3 (`test_the_detached_board_child_reports_an_escalated_milestone_inside_an_ok_envelope`).
5. A permissive (0) or restrictive (0o277) umask must not change either file's 0600 mode — pinned in Task 2 (`test_create_board_log_is_0600_whatever_the_umask`, `test_write_board_report_is_0600_whatever_the_umask`).

---

### Task 1: `paths.project_digest` and `paths.boards_dir`

**Files:**
- Modify: `src/agent_manager/paths.py:22-44`
- Test: `tests/test_paths.py` (append after `test_run_dir_is_idempotent`, ~line 224)

**Interfaces:**
- Consumes: nothing.
- Produces: `paths.project_digest(root: Path) -> str` (64 lowercase hex chars, sha256 of `str(root.resolve())`); `paths.boards_dir() -> Path` (`data_dir() / "boards"`, created with `parents=True, exist_ok=True`). The private `_project_digest` is removed; its two callers in `paths.py` use `project_digest`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_paths.py` after `test_run_dir_is_idempotent`:

```python
def test_project_digest_is_the_project_db_stem_and_64_hex(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    project_root = tmp_path / "repo"
    project_root.mkdir()

    digest = paths.project_digest(project_root)

    assert paths.project_db_path(project_root).name == f"{digest}.db"
    assert len(digest) == 64
    assert set(digest) <= set("0123456789abcdef")


def test_project_digest_matches_a_fixed_vector():
    assert paths.project_digest(Path("/nonexistent/repo")) == (
        "5b6e8e2d129e523b4fabf8a73dcdc18cb7f253565385fd9e6e5c0888ba865785"
    )


def test_boards_dir_is_under_data_dir_and_exists(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    result = paths.boards_dir()

    assert result == tmp_path / "agent-manager" / "boards"
    assert result.is_dir()


def test_boards_dir_is_idempotent_and_keeps_what_is_inside(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    first = paths.boards_dir()
    (first / "a.log").write_text("x\n", encoding="utf-8")

    second = paths.boards_dir()

    assert second == first
    assert (second / "a.log").read_text(encoding="utf-8") == "x\n"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_paths.py -k "project_digest or boards_dir" -v`
Expected: 4 FAIL with `AttributeError: module 'agent_manager.paths' has no attribute 'project_digest'` / `'boards_dir'`.

- [ ] **Step 3: Implement**

In `src/agent_manager/paths.py`, replace the `_project_digest` function (lines 22-24) with:

```python
def project_digest(root: Path) -> str:
    """The per-project file stem: sha256 of the resolved root path, 64 hex chars.

    Public because a detached board run names its files after it too
    (`<data dir>/boards/<stamp>-<digest>.log`).
    """
    return hashlib.sha256(str(root.resolve()).encode()).hexdigest()
```

Change `project_db_path`'s body to:

```python
    return _projects_dir() / f"{project_digest(root)}.db"
```

Change `project_lock_path`'s last line to:

```python
    return _projects_dir() / f"{project_digest(root)}.{name}.lock"
```

Add after `run_dir`:

```python
def boards_dir() -> Path:
    """Where a detached board run's log and report go: `data_dir()/boards`, created.

    A board run has no run id, so its files cannot live under `runs/`.
    """
    result = data_dir() / "boards"
    result.mkdir(parents=True, exist_ok=True)
    return result
```

- [ ] **Step 4: Run the paths tests**

Run: `uv run pytest tests/test_paths.py -v`
Expected: all PASS (including the existing `project_db_path` / `project_lock_path` fixed-vector tests). Then `grep -rn "_project_digest" src tests` prints nothing.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/paths.py tests/test_paths.py
git commit -m "feat: public paths.project_digest and paths.boards_dir (card 03f027ea)"
```

---

### Task 2: The board's log and report files in `detach`

**Files:**
- Modify: `src/agent_manager/detach.py` (constants after `FILE_MODE`; new functions after `create_run_log`; `write_report` body at lines 75-96; module docstring lines 1-12)
- Test: `tests/test_detach.py` (append after `test_write_report_replaces_an_earlier_report_whole`, before the `# -- fork_detacher itself` comment)

**Interfaces:**
- Consumes: `paths.boards_dir() -> Path` (Task 1).
- Produces:
  - `detach.BOARD_LOG_SUFFIX = ".log"`, `detach.BOARD_REPORT_SUFFIX = ".report.json"`
  - `detach.create_board_log(stem: str) -> Path` — `boards_dir() / f"{stem}{BOARD_LOG_SUFFIX}"`, created exclusively, empty, mode 0600; `FileExistsError` (with `.filename` the path) if present.
  - `detach.board_report_path(stem: str) -> Path` — `boards_dir() / f"{stem}{BOARD_REPORT_SUFFIX}"`; creates only the directory.
  - `detach.write_board_report(path: Path, text: str) -> Path` — writes `text + "\n"` atomically at 0600, returns `path`.
  - `write_report(run_id, text)` unchanged in behaviour; both use private `_write_atomically(target: Path, text: str) -> Path`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_detach.py` after `test_write_report_replaces_an_earlier_report_whole`:

```python
BOARD_STEM = "20261004T090000Z-" + "ab" * 32


def test_create_board_log_makes_an_empty_0600_file_under_boards():
    log = detach.create_board_log(BOARD_STEM)

    assert log == paths.data_dir() / "boards" / f"{BOARD_STEM}{detach.BOARD_LOG_SUFFIX}"
    assert log.is_file()
    assert log.read_bytes() == b""
    assert _mode(log) == 0o600


@pytest.mark.parametrize("umask", [0o000, 0o277])
def test_create_board_log_is_0600_whatever_the_umask(umask):
    paths.boards_dir()  # made first: a 0o277 umask would make it untraversable
    old = os.umask(umask)
    try:
        log = detach.create_board_log(BOARD_STEM)
    finally:
        os.umask(old)

    assert _mode(log) == 0o600


def test_create_board_log_refuses_an_existing_log_and_leaves_it_alone():
    log = detach.create_board_log(BOARD_STEM)
    log.write_text("the first board run\n", encoding="utf-8")

    with pytest.raises(FileExistsError) as caught:
        detach.create_board_log(BOARD_STEM)

    assert str(caught.value.filename) == str(log)
    assert log.read_text(encoding="utf-8") == "the first board run\n"


def test_board_report_path_names_the_report_and_creates_only_the_directory():
    path = detach.board_report_path(BOARD_STEM)

    assert path == paths.data_dir() / "boards" / f"{BOARD_STEM}{detach.BOARD_REPORT_SUFFIX}"
    assert path.parent.is_dir()
    assert not path.exists()
    assert list(path.parent.iterdir()) == []


def test_write_board_report_writes_one_line_at_0600_and_leaves_no_temp_file():
    target = detach.board_report_path(BOARD_STEM)

    path = detach.write_board_report(target, json.dumps({"ok": True, "data": {"board": True}}))

    assert path == target
    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True, "data": {"board": True}}
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert _mode(path) == 0o600
    assert sorted(entry.name for entry in path.parent.iterdir()) == [target.name]


def test_write_board_report_replaces_an_earlier_report_whole():
    target = detach.board_report_path(BOARD_STEM)
    detach.write_board_report(target, '{"ok":false}')

    path = detach.write_board_report(target, '{"ok":true}')

    assert path.read_text(encoding="utf-8") == '{"ok":true}\n'
    assert sorted(entry.name for entry in path.parent.iterdir()) == [target.name]


@pytest.mark.parametrize("umask", [0o000, 0o277])
def test_write_board_report_is_0600_whatever_the_umask(umask):
    target = detach.board_report_path(BOARD_STEM)
    old = os.umask(umask)
    try:
        detach.write_board_report(target, '{"ok":true}')
    finally:
        os.umask(old)

    assert _mode(target) == 0o600
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_detach.py -k "board" -v`
Expected: FAIL with `AttributeError: module 'agent_manager.detach' has no attribute 'create_board_log'` (and `BOARD_LOG_SUFFIX`, `board_report_path`, `write_board_report`).

- [ ] **Step 3: Implement**

In `src/agent_manager/detach.py`:

Replace the first paragraph of the module docstring (lines 1-6) with:

```python
"""Hand a run's engine to a child in its own session (`am run --detach`, card aff9fdbf).

Process-level pieces only: a run's `run.log` and `report.json`, a detached
board's `<stamp>-<digest>.log` and `.report.json` under `<data dir>/boards/`
(card 03f027ea), and `fork_detacher`, which forks the child. What the child
runs, and the lease hand-off around it, live in `cli` (`hand_off_to_child`,
`run_detached_child`) and `orchestrate` (`detach_board`).
This module imports only `paths` and the stdlib.
```

(keep the second paragraph, "Fork, not a re-exec: ...", unchanged).

After `FILE_MODE`'s docstring, add:

```python
BOARD_LOG_SUFFIX = ".log"
"""A detached board's stdout and stderr: `<data dir>/boards/<stamp>-<digest>.log`."""

BOARD_REPORT_SUFFIX = ".report.json"
"""A detached board's final envelope: `<data dir>/boards/<stamp>-<digest>.report.json`."""
```

After `create_run_log`, add:

```python
def create_board_log(stem: str) -> Path:
    """`<data dir>/boards/<stem>.log`, created exclusively and empty, mode exactly 0600.

    `O_EXCL`: a board run has no run id to keep two runs apart, so a second
    board detach on the same repository in the same second must not share
    the first one's log. That is `FileExistsError`, with the existing file
    left as it was. `fchmod` after the open, because `O_CREAT`'s mode is
    masked by the umask.
    """
    path = paths.boards_dir() / f"{stem}{BOARD_LOG_SUFFIX}"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND, FILE_MODE)
    try:
        os.fchmod(fd, FILE_MODE)
    finally:
        os.close(fd)
    return path


def board_report_path(stem: str) -> Path:
    """Where a detached board's report will be: `<data dir>/boards/<stem>.report.json`.

    Creates the `boards` directory, never the file: the child writes it when
    the board run ends.
    """
    return paths.boards_dir() / f"{stem}{BOARD_REPORT_SUFFIX}"
```

Replace `write_report` (lines 75-96) with:

```python
def write_report(run_id: str, text: str) -> Path:
    """Write `text` and a newline to the run's `report.json`, atomically, mode 0600."""
    return _write_atomically(paths.run_dir(run_id) / REPORT_NAME, text)


def write_board_report(path: Path, text: str) -> Path:
    """Write `text` and a newline to a detached board's report `path`, atomically, mode 0600."""
    return _write_atomically(path, text)


def _write_atomically(target: Path, text: str) -> Path:
    """`text` and a newline to `target`: a temp file in the same directory,
    fsynced, chmodded 0600, then `os.replace`d over the target, so a reader
    sees no file or a whole one, never a partial one, and no temp file stays.
    """
    fd, temp = tempfile.mkstemp(dir=target.parent, prefix=".report-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, FILE_MODE)
        os.replace(temp, target)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp)
        raise
    return target
```

- [ ] **Step 4: Run the detach tests**

Run: `uv run pytest tests/test_detach.py -v`
Expected: all unit tests PASS, including the unchanged `write_report` tests and `test_detach_module_imports_only_paths_and_the_stdlib`; the three `e2e_fake` fork tests are deselected.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/detach.py tests/test_detach.py
git commit -m "feat: detach writes a board's exclusive log and atomic report (card 03f027ea)"
```

---

### Task 3: `orchestrate.detach_board`

**Files:**
- Modify: `src/agent_manager/orchestrate.py` — import list (lines 61-72: add `paths`); add `BoardLogExistsError` and `detach_board` right after `run_board` (after line 2561, before `async def _run_board_async`).
- Test: `tests/test_orchestrate.py` (append at the end of the file, after the `am run --milestone --detach` tests).

**Interfaces:**
- Consumes: `paths.project_digest(root: Path) -> str` (Task 1); `detach.create_board_log(stem) -> Path`, `detach.board_report_path(stem) -> Path`, `detach.write_board_report(path, text) -> Path` (Task 2); existing `preflight_board(*, repo_dir, base_branch, branch_prefix_of, max_concurrent) -> BoardPreflight`, `run_board_engine(pre, *, commands, allow_no_verification, runner_factory, driver, clock, control_interval) -> dict`, `detach.Detacher`, `detach.Spawned(pid, go, abort)`, `runs.RUN_ID_TIME_FORMAT`.
- Produces:
  - `orchestrate.BoardLogExistsError(ValueError)` — message contains the existing log's path.
  - `orchestrate.detach_board(*, repo_dir: Path, base_branch: str | None, branch_prefix_of: Callable[[models.CardNode], str], detacher: detach.Detacher, commands: Sequence[str] = (), allow_no_verification: bool = False, max_concurrent: int = 1, runner_factory: runs.RunnerFactory | None = None, driver: Driver | None = None, clock: Callable[[], datetime] = _utcnow, control_interval: float = control.CONTROL_POLL_SECONDS) -> dict[str, Any]` returning `{"board": True, "detached": True, "pid": int, "log": str, "report": str, "levels": list}`.

The tests reuse helpers already in `tests/test_orchestrate.py`: the `board_seams` fixture (~line 7445: fakes `board.roots`, `cli.refuse_claimed`, `orchestrate._run_milestone_async`, `orchestrate._local_branch_exists`), `_board(seams, **overrides)` (calls `run_board` with `base_branch="main"`, `_prefix_of`, `max_concurrent=2`), `_preflight(seams, **overrides)` (same defaults, `preflight_board`), `_board_milestone(n, *, blocked_by=(), status="todo", done_children=False)`, `_prefix_of`, `OTHER_RUN_ID`, `FAKE_CHILD_PID` and `_FakeDetacher` (~line 8710: records `calls` (logs), `events` (`"go"`/`"abort"`), keeps `body`). The autouse conftest fixture points `XDG_DATA_HOME` at a fresh temp dir for every test.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_orchestrate.py`:

```python
# ── am run --board --detach (card 03f027ea) ─────────────────────────────────
#
# Unit tier: `board_seams` fakes the board, the claim check, the milestone
# runs and the branch check, and `_FakeDetacher` forks nothing; a test that
# needs the child runs `fake.body()` inline. The real fork is
# `tests/e2e/test_detached_run.py`'s.

BOARD_DETACH_AT = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)
"""The fixed clock: its stamp is `20261004T090000Z`."""


def _detach_board(seams: BoardSeams, detacher: Any, **overrides: Any) -> dict[str, Any]:
    """`orchestrate.detach_board` with `_board`'s defaults and the fixed clock."""
    kwargs: dict[str, Any] = {
        "repo_dir": seams.root,
        "base_branch": "main",
        "branch_prefix_of": _prefix_of,
        "max_concurrent": 2,
        "detacher": detacher,
        "clock": lambda: BOARD_DETACH_AT,
    }
    kwargs.update(overrides)
    return orchestrate.detach_board(**kwargs)


def _board_stem(seams: BoardSeams) -> str:
    return f"20261004T090000Z-{paths.project_digest(runs.resolve_repo_dir(seams.root))}"


def _boards() -> Path:
    """`<data dir>/boards`, without creating it (unlike `paths.boards_dir`)."""
    return paths.data_dir() / "boards"


def _claim_conflict(monkeypatch) -> None:
    def refuse(root: Path, keys, *, run_id: str | None = None) -> None:
        raise cli.ClaimedError("held elsewhere", key=keys[-1], run_id=OTHER_RUN_ID)

    monkeypatch.setattr(cli, "refuse_claimed", refuse)


@pytest.mark.parametrize(
    ("cards", "overrides", "conflict", "error"),
    [
        pytest.param(
            lambda: [_board_milestone(1, blocked_by=(2,)), _board_milestone(2, blocked_by=(1,))],
            {},
            False,
            dag.DependencyCycleError,
            id="cycle",
        ),
        pytest.param(
            lambda: [
                _board_milestone(1),
                _board_milestone(2),
                _board_milestone(3, blocked_by=(1, 2)),
            ],
            {},
            False,
            orchestrate.MilestoneBlockersError,
            id="two-open-blockers",
        ),
        pytest.param(
            lambda: [_board_milestone(1)], {}, True, cli.ClaimedError, id="claim-conflict"
        ),
        pytest.param(
            lambda: [_board_milestone(1)],
            {"max_concurrent": 0},
            False,
            ValueError,
            id="max-concurrent-0",
        ),
    ],
)
def test_detach_board_refuses_in_the_foreground_and_forks_nothing(
    board_seams, monkeypatch, cards, overrides, conflict, error
):
    """Spec test 7 / Review Focus 2: `preflight_board`'s very refusal, no fork,
    and `<data dir>/boards` absent, not just empty."""
    board_seams.cards = cards()
    if conflict:
        _claim_conflict(monkeypatch)
    fake = _FakeDetacher()

    with pytest.raises(error) as detached:
        _detach_board(board_seams, fake, **overrides)
    with pytest.raises(error) as direct:
        _preflight(board_seams, **overrides)

    assert type(detached.value) is type(direct.value)
    assert str(detached.value) == str(direct.value)
    assert fake.calls == []
    assert not _boards().exists()
    assert board_seams.runs.calls == []


def test_detach_board_hands_the_board_to_a_child_and_reports_where_its_files_go(board_seams):
    """Spec test 8: the six keys, the files' names and modes, one go, and no
    milestone run in the parent."""
    a = _board_milestone(1)
    b = _board_milestone(2, blocked_by=(1,))
    board_seams.cards = [a, b]
    fake = _FakeDetacher()

    data = _detach_board(board_seams, fake)

    stem = _board_stem(board_seams)
    log = _boards() / f"{stem}{detach.BOARD_LOG_SUFFIX}"
    report = _boards() / f"{stem}{detach.BOARD_REPORT_SUFFIX}"
    assert set(data) == {"board", "detached", "pid", "log", "report", "levels"}
    assert data["board"] is True
    assert data["detached"] is True
    assert data["pid"] == FAKE_CHILD_PID
    assert data["log"] == str(log)
    assert data["report"] == str(report)
    assert data["levels"] == [
        {"level": 0, "milestones": [a.id]},
        {"level": 1, "milestones": [b.id]},
    ]
    assert data["levels"] == _preflight(board_seams).levels_payload
    assert log.read_bytes() == b""
    assert stat.S_IMODE(os.stat(log).st_mode) == 0o600
    assert not report.exists()
    assert fake.calls == [log]
    assert fake.events == ["go"]
    assert board_seams.runs.calls == []


def test_the_detached_board_child_writes_the_envelope_a_foreground_board_run_prints(
    board_seams,
):
    """Spec test 9: the report is `render(ok_envelope(run_board's payload))`
    plus a newline, mode 0600, with no temp file left beside it."""
    a = _board_milestone(1)
    b = _board_milestone(2, blocked_by=(1,))
    board_seams.cards = [a, b]
    fake = _FakeDetacher()
    data = _detach_board(board_seams, fake)

    fake.body()

    called = board_seams.runs.called()
    board_seams.runs.calls.clear()
    foreground = _board(board_seams)
    report = Path(data["report"])
    assert called == [a.id, b.id]
    assert report.read_text(encoding="utf-8") == cli.render(cli.ok_envelope(foreground)) + "\n"
    assert stat.S_IMODE(os.stat(report).st_mode) == 0o600
    assert sorted(entry.name for entry in _boards().iterdir()) == sorted(
        [Path(data["log"]).name, report.name]
    )


def test_the_detached_board_child_forwards_the_run_arguments(board_seams):
    one = _board_milestone(1)
    board_seams.cards = [one]
    fake = _FakeDetacher()
    runner_factory = object()
    driver = object()
    _detach_board(
        board_seams,
        fake,
        max_concurrent=3,
        commands=("git status",),
        allow_no_verification=True,
        runner_factory=runner_factory,
        driver=driver,
        control_interval=0.25,
    )

    fake.body()

    ((milestone, kwargs),) = board_seams.runs.calls
    assert milestone == one.id
    assert kwargs["max_concurrent"] == 3
    assert list(kwargs["commands"]) == ["git status"]
    assert kwargs["allow_no_verification"] is True
    assert kwargs["runner_factory"] is runner_factory
    assert kwargs["driver"] is driver
    assert kwargs["clock"]() == BOARD_DETACH_AT
    assert kwargs["control_interval"] == 0.25


@pytest.mark.parametrize(
    ("outcome", "error"),
    [
        pytest.param(RuntimeError("boom"), "RuntimeError: boom", id="milestone-raises"),
        pytest.param(
            cli.ClaimedError("claimed since the check", key="branch:x", run_id=OTHER_RUN_ID),
            "ClaimedError: claimed since the check",
            id="late-claim",
        ),
    ],
)
def test_the_detached_board_child_reports_an_escalated_milestone_inside_an_ok_envelope(
    board_seams, outcome, error
):
    """Spec test 10 / Review Focus 4: `ok_envelope` whose `data.ok` is false;
    a claim taken after the up-front check is an `escalated` entry."""
    a = _board_milestone(1)
    b = _board_milestone(2, blocked_by=(1,))
    board_seams.cards = [a, b]
    board_seams.runs.outcomes[a.id] = outcome
    fake = _FakeDetacher()
    data = _detach_board(board_seams, fake)

    fake.body()

    envelope = json.loads(Path(data["report"]).read_text(encoding="utf-8"))
    assert envelope["ok"] is True
    assert envelope["data"]["ok"] is False
    entries = {entry["milestone_id"]: entry for entry in envelope["data"]["milestones"]}
    assert entries[a.id] == {"milestone_id": a.id, "status": "escalated", "error": error}
    assert entries[b.id] == {"milestone_id": b.id, "status": "blocked", "blocked_by": [a.id]}


def test_the_detached_board_child_reports_a_handled_engine_error_as_an_error_envelope(
    board_seams, monkeypatch
):
    """Spec test 11, first half: the engine is read at call time, in the child."""
    board_seams.cards = [_board_milestone(1)]
    fake = _FakeDetacher()
    data = _detach_board(board_seams, fake)

    def engine(pre: Any, **kwargs: Any) -> dict[str, Any]:
        raise ValueError("x")

    monkeypatch.setattr(orchestrate, "run_board_engine", engine)
    fake.body()

    report = Path(data["report"])
    assert json.loads(report.read_text(encoding="utf-8")) == {
        "ok": False,
        "error": {"type": "ValueError", "message": "x"},
    }
    assert stat.S_IMODE(os.stat(report).st_mode) == 0o600


def test_the_detached_board_child_writes_no_report_for_an_unhandled_engine_error(
    board_seams, monkeypatch
):
    """Spec test 11, second half: a bug propagates (its traceback goes to the log)."""
    board_seams.cards = [_board_milestone(1)]
    fake = _FakeDetacher()
    data = _detach_board(board_seams, fake)

    def engine(pre: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("engine bug")

    monkeypatch.setattr(orchestrate, "run_board_engine", engine)
    with pytest.raises(RuntimeError, match="engine bug"):
        fake.body()

    assert not Path(data["report"]).exists()
    assert [entry.name for entry in _boards().iterdir()] == [Path(data["log"]).name]


def test_detach_board_on_a_board_with_nothing_open_still_hands_off(board_seams):
    """Spec test 12 / Review Focus 3."""
    board_seams.cards = [_board_milestone(1, status="done", done_children=True)]
    fake = _FakeDetacher()

    data = _detach_board(board_seams, fake)

    assert data["levels"] == []
    assert fake.calls == [Path(data["log"])]
    assert fake.events == ["go"]
    assert board_seams.claims == []
    fake.body()
    assert json.loads(Path(data["report"]).read_text(encoding="utf-8")) == {
        "ok": True,
        "data": {"ok": True, "board": True, "levels": [], "milestones": []},
    }
    assert board_seams.runs.calls == []


def test_detach_board_refuses_a_log_that_already_exists_and_forks_nothing(board_seams):
    """Spec test 13 / Review Focus 1: two board detaches in one second."""
    board_seams.cards = [_board_milestone(1)]
    stem = _board_stem(board_seams)
    existing = paths.boards_dir() / f"{stem}{detach.BOARD_LOG_SUFFIX}"
    existing.write_text("an earlier board run\n", encoding="utf-8")
    fake = _FakeDetacher()

    with pytest.raises(orchestrate.BoardLogExistsError) as caught:
        _detach_board(board_seams, fake)

    assert isinstance(caught.value, ValueError)
    assert str(existing) in str(caught.value)
    assert fake.calls == []
    assert existing.read_text(encoding="utf-8") == "an earlier board run\n"
    assert not (_boards() / f"{stem}{detach.BOARD_REPORT_SUFFIX}").exists()
    assert board_seams.runs.calls == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_orchestrate.py -k "detach_board or detached_board" -v`
Expected: every new test FAILs with `AttributeError: module 'agent_manager.orchestrate' has no attribute 'detach_board'` (or `'BoardLogExistsError'`). Collection succeeds: the parametrize lists only name `orchestrate.MilestoneBlockersError`, which exists.

- [ ] **Step 3: Implement**

In `src/agent_manager/orchestrate.py`, change the `from agent_manager import (...)` block to add `paths`:

```python
from agent_manager import (
    bases,
    board,
    census,
    cli,
    comments,
    control,
    dag,
    detach,
    integration,
    models,
    paths,
    runs,
)
```

Directly after `run_board` (after its `return run_board_engine(...)` call, before `async def _run_board_async`), add:

```python
class BoardLogExistsError(ValueError):
    """A board detach found its log already there (card 03f027ea).

    Another board run on this repository was detached in the same second, so
    the two would share a log and a report. Subclasses `ValueError`, so it is
    in `cli.HANDLED`: an `ok: false` envelope and exit 3, nothing forked, and
    the existing log untouched.
    """


def detach_board(
    *,
    repo_dir: Path,
    base_branch: str | None,
    branch_prefix_of: Callable[[models.CardNode], str],
    detacher: detach.Detacher,
    commands: Sequence[str] = (),
    allow_no_verification: bool = False,
    max_concurrent: int = 1,
    runner_factory: runs.RunnerFactory | None = None,
    driver: Driver | None = None,
    clock: Callable[[], datetime] = _utcnow,
    control_interval: float = control.CONTROL_POLL_SECONDS,
) -> dict[str, Any]:
    """`am run --board --detach` (card 03f027ea): pre-flight here, the board run in a child.

    `preflight_board` runs exactly as for `run_board`, so every refusal is
    the same and leaves nothing behind, `<data dir>/boards/` included. Then
    the log `<data dir>/boards/<stamp>-<digest>.log` is created exclusively
    at 0600 (`<stamp>` from `clock`, `<digest>` the repository's
    `paths.project_digest`); an existing one is `BoardLogExistsError`.
    `detacher` gets the body and the log, and the child is let go at once:
    a board run has no run id, store or lease to point at it, so this does
    not go through `cli.hand_off_to_child`. Nothing holds a store across the
    fork: `preflight_board` opens none.

    The child runs `run_board_engine` on this very `pre` (no second board
    read or claim check; each milestone's own run is created when it is
    dispatched) and writes `<stem>.report.json`: the envelope a foreground
    `am run --board` would have printed, or a `HANDLED` error's envelope.
    Anything else propagates with no report; its traceback goes to the log.

    Returns `{"board", "detached", "pid", "log", "report", "levels"}`, with
    `levels` the foreground payload's.
    """
    pre = preflight_board(
        repo_dir=repo_dir,
        base_branch=base_branch,
        branch_prefix_of=branch_prefix_of,
        max_concurrent=max_concurrent,
    )
    stem = f"{clock().strftime(runs.RUN_ID_TIME_FORMAT)}-{paths.project_digest(pre.root)}"
    try:
        log = detach.create_board_log(stem)
    except FileExistsError as error:
        raise BoardLogExistsError(
            f"board log {error.filename} already exists: another board run on this "
            "repository was detached in the same second; run the command again"
        ) from None
    report = detach.board_report_path(stem)

    def body() -> None:
        try:
            payload = run_board_engine(
                pre,
                commands=commands,
                allow_no_verification=allow_no_verification,
                runner_factory=runner_factory,
                driver=driver,
                clock=clock,
                control_interval=control_interval,
            )
        except cli.HANDLED as error:
            detach.write_board_report(report, cli.render(cli.error_envelope(error)))
            return
        detach.write_board_report(report, cli.render(cli.ok_envelope(payload)))

    spawned = detacher(body, log)
    spawned.go()
    return {
        "board": True,
        "detached": True,
        "pid": spawned.pid,
        "log": str(log),
        "report": str(report),
        "levels": pre.levels_payload,
    }
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_orchestrate.py -k "detach_board or detached_board or board" -v`
Expected: all PASS (new tests plus the existing `run_board` / `preflight_board` / `run_board_engine` seam tests).

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/orchestrate.py tests/test_orchestrate.py
git commit -m "feat: orchestrate.detach_board hands a pre-flighted board run to a child (card 03f027ea)"
```

---

### Task 4: `am run --board --detach` at the command line

**Files:**
- Modify: `src/agent_manager/cli.py` — `_check_run_targets` docstring (lines ~1570-1572) and the `if detach and board:` refusal (lines ~1612-1616); `RUN_EXAMPLES` (~1641-1649); the `--detach` Option help (~1685-1694); `run`'s dispatch (insert a branch between `if whole_board and dry_run:` and `elif whole_board:`, ~1752-1759).
- Test: `tests/test_cli.py` — the detach section (`DRY_RUN_DETACH` / `BOARD_DETACH` at ~10970-11040).

**Interfaces:**
- Consumes: `orchestrate.detach_board(**kwargs)` (Task 3), `orchestrate.BoardLogExistsError`; existing `board_prefix_of(branch_prefix)`, `detach.fork_detacher`, `HANDLED`, `render`, `ok_envelope`, `error_envelope`.
- Produces: the CLI behaviour of spec §3.1 and §3.6 (help, examples, docstring). `cli.run` calls `orchestrate.detach_board(repo_dir=repo_dir, base_branch=base_branch, branch_prefix_of=board_prefix_of(branch_prefix), commands=list(verify), allow_no_verification=allow_no_verification, max_concurrent=lanes, detacher=detach.fork_detacher)`.

Helpers already in `tests/test_cli.py`: `_forbid_board_paths(monkeypatch)` (~3386; forbids `run_card`, `Store`, `run_milestone`, `run_board`, `dry_run_board`, ...), `_board_run(tmp_path, *extra)` (~4107; `am run --board --repo-dir <tmp> --base-branch main *extra`), `_refusal(result)` (~2859; asserts exit 3, returns `error`), `BOARD_CARD` (~3262; stem `milestone-14-run-the-cbe34d00`), `SOME_CARD`, `_FakeDetacher` and `FAKE_CHILD_PID` (~10924), `runner`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli.py`, delete the line

```python
BOARD_DETACH = "--detach applies to --card and --milestone, not --board"
```

and replace `test_check_run_targets_refuses_detach_with_dry_run_or_board` (its whole `@pytest.mark.parametrize` block and body) with:

```python
@pytest.mark.parametrize(
    ("kwargs", "message", "hint"),
    [
        (
            {"card": None, "milestone": "M9", "board": False, "branch_prefix": "m9", "dry_run": True},
            DRY_RUN_DETACH,
            "'--detach' / '--dry-run'",
        ),
        (
            {"card": SOME_CARD, "milestone": None, "board": False, "branch_prefix": "m9", "dry_run": True},
            DRY_RUN_DETACH,
            "'--detach' / '--dry-run'",
        ),
        (
            {"card": None, "milestone": None, "board": True, "branch_prefix": None, "dry_run": True},
            DRY_RUN_DETACH,
            "'--detach' / '--dry-run'",
        ),
    ],
)
def test_check_run_targets_refuses_detach_with_dry_run(kwargs, message, hint):
    with pytest.raises(typer.BadParameter) as caught:
        cli._check_run_targets(detach=True, **kwargs)

    assert caught.value.message == message
    assert caught.value.param_hint == hint


@pytest.mark.parametrize(
    "kwargs",
    [
        {"branch_prefix": None},
        {"branch_prefix": "p"},
        {"branch_prefix": "p", "max_concurrent": 2},
    ],
)
def test_check_run_targets_accepts_detach_with_board(kwargs):
    """Card 03f027ea: `--board --detach` is no longer a usage error."""
    assert (
        cli._check_run_targets(
            card=None, milestone=None, board=True, dry_run=False, detach=True, **kwargs
        )
        is None
    )
```

Replace `test_detach_with_dry_run_or_board_is_a_usage_error_that_detaches_nothing` (its parametrize block and body) with:

```python
@pytest.mark.parametrize(
    "targets",
    [
        ["--milestone", "M9", "--branch-prefix", "m9", "--dry-run"],
        ["--board", "--dry-run"],
    ],
)
def test_detach_with_dry_run_is_a_usage_error_that_detaches_nothing(
    tmp_path, monkeypatch, targets
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _forbid_board_paths(monkeypatch)
    fake = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", fake)

    result = runner.invoke(
        cli.app, ["run", *targets, "--detach", "--repo-dir", str(tmp_path)]
    )

    assert result.exit_code == 2, result.output
    assert '"ok"' not in result.stdout
    assert "detached" in result.output
    assert fake.calls == []
    assert not (paths.data_dir() / "runs").exists()
    assert not (paths.data_dir() / "boards").exists()
```

Directly after it, add:

```python
# ── am run --board --detach (card 03f027ea) ─────────────────────────────────


DETACHED_BOARD_PAYLOAD: dict[str, Any] = {
    "board": True,
    "detached": True,
    "pid": FAKE_CHILD_PID,
    "log": "/data/agent-manager/boards/20261004T090000Z-abc.log",
    "report": "/data/agent-manager/boards/20261004T090000Z-abc.report.json",
    "levels": [{"level": 0, "milestones": [SOME_CARD]}],
}


def _patch_detach_board(monkeypatch, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Replace `orchestrate.detach_board`, forbid every other run path, record calls."""
    _forbid_board_paths(monkeypatch)
    calls: list[dict[str, Any]] = []

    def fake_detach_board(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return payload

    monkeypatch.setattr(orchestrate, "detach_board", fake_detach_board)
    return calls


@pytest.mark.parametrize(
    "error",
    [
        ValueError("max_concurrent must be at least 1, got 0"),
        dag.DependencyCycleError("dag: dependency cycle among milestones #a, #b"),
        orchestrate.MilestoneBlockersError(
            "milestone X is blocked by 2 milestones that are not landed (A, B); "
            "a milestone stacks on at most one: chain them (A <- B <- C)"
        ),
        cli.ClaimedError(
            "run 20261001T000000Z-00000001 already claims branch:m-integrate",
            key="branch:m-integrate",
            run_id="20261001T000000Z-00000001",
        ),
        board.BoardError("brd refused", argv=["brd", "tree"]),
    ],
    ids=["ValueError", "DependencyCycleError", "MilestoneBlockersError", "ClaimedError", "BoardError"],
)
def test_a_board_detach_preflight_refusal_is_an_envelope_that_forks_nothing(
    tmp_path, monkeypatch, error
):
    """Spec test 4 / Review Focus 2: the real `detach_board` over a refusing
    `preflight_board`: exit 3, no fork, `<data dir>/boards` absent."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _forbid_board_paths(monkeypatch)
    fake = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", fake)

    def refuse(**kwargs: Any) -> Any:
        raise error

    monkeypatch.setattr(orchestrate, "preflight_board", refuse)

    refusal = _refusal(_board_run(tmp_path, "--detach"))

    assert refusal == {"type": type(error).__name__, "message": str(error)}
    assert fake.calls == []
    assert not (paths.data_dir() / "boards").exists()


def test_a_board_detach_calls_detach_board_once_with_the_run_options(tmp_path, monkeypatch):
    """Spec test 5: the kwargs are compared whole, so an extra key fails;
    `detacher` is `detach.fork_detacher` read at call time."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    calls = _patch_detach_board(monkeypatch, DETACHED_BOARD_PAYLOAD)
    sentinel = _FakeDetacher()
    monkeypatch.setattr(detach, "fork_detacher", sentinel)

    result = _board_run(
        tmp_path,
        "--detach",
        "--verify",
        "X",
        "--max-concurrent",
        "3",
        "--branch-prefix",
        "p",
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == cli.ok_envelope(DETACHED_BOARD_PAYLOAD)
    (kwargs,) = calls
    prefix_of = kwargs.pop("branch_prefix_of")
    assert kwargs.pop("detacher") is sentinel
    assert kwargs == {
        "repo_dir": tmp_path,
        "base_branch": "main",
        "commands": ["X"],
        "allow_no_verification": False,
        "max_concurrent": 3,
    }
    assert prefix_of(BOARD_CARD) == cli.board_prefix_of("p")(BOARD_CARD)
    assert prefix_of(BOARD_CARD) == "p-milestone-14-run-the-cbe34d00"
    assert sentinel.calls == []


def test_a_board_detach_exits_0_even_when_its_payload_reads_as_escalated(
    tmp_path, monkeypatch
):
    """Spec test 6 / Review Focus 4: the board escalation check is skipped."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    payload = {**DETACHED_BOARD_PAYLOAD, "milestones": [{"status": "escalated"}]}
    _patch_detach_board(monkeypatch, payload)
    monkeypatch.setattr(detach, "fork_detacher", _FakeDetacher())

    result = _board_run(tmp_path, "--detach")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == cli.ok_envelope(payload)


def test_run_help_and_examples_document_the_board_detach():
    """Spec §3.6."""
    assert 'am run --board --verify "uv run pytest" --detach' in cli.RUN_EXAMPLES
    help_text = inspect.signature(cli.run).parameters["detach_run"].default.help
    assert "--board" in help_text
    assert "<data dir>/boards/<stamp>-<digest>.log" in help_text
    assert "<stamp>-<digest>.report.json" in help_text
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "detach_with_board or detach_with_dry_run or board_detach" -v`
Expected: `test_check_run_targets_accepts_detach_with_board` FAILs with `typer.BadParameter: --detach applies to --card and --milestone, not --board`; the three `test_a_board_detach_*` CLI tests FAIL on exit code 2 instead of 3/0; `test_run_help_and_examples_document_the_board_detach` FAILs on the `RUN_EXAMPLES` assertion. The two `..._detach_with_dry_run...` tests already PASS (the existing `detach and dry_run` check precedes the board one) — they pin that it stays that way.

- [ ] **Step 3: Implement**

In `src/agent_manager/cli.py`, `_check_run_targets`:

Replace the docstring's last three lines

```
    `--detach` (card aff9fdbf) is refused with `--dry-run`, which writes
    nothing to hand off, and with `--board`, whose run was not split into
    pre-flight, recorded stage and engine.
```

with

```
    `--detach` (card aff9fdbf) is refused with `--dry-run`, which writes
    nothing to hand off.
```

Delete the block

```python
    if detach and board:
        raise typer.BadParameter(
            "--detach applies to --card and --milestone, not --board",
            param_hint="'--detach' / '--board'",
        )
```

Replace `RUN_EXAMPLES` with:

```python
RUN_EXAMPLES = """\
Examples:
  am run --milestone "M9" --branch-prefix m9 --dry-run --pretty       # preview the plan
  am run --milestone "M9" --branch-prefix m9 --verify "uv run pytest"  # run it
  am run --milestone "M9" --branch-prefix m9 --verify "uv run pytest" --detach  # run it in the background
  am run --board --verify "uv run pytest"                             # run every open milestone
  am run --board --verify "uv run pytest" --detach                    # ... in the background
  am status <run-id> --pretty                                         # watch it (another terminal)
  am resume <run-id> --verify "uv run pytest"                         # after a fix, stop or crash
"""
```

Replace the `--detach` Option's `help=(...)` with:

```python
        help=(
            "With --card, --milestone or --board: make every check here (and, for "
            "--card or --milestone, record and lease the run), then hand the run to "
            "a background process in its own session and print its pid and log. "
            "A card or milestone run's output goes to "
            "<data dir>/runs/<run-id>/run.log and its final envelope to "
            "report.json; a board's output goes to "
            "<data dir>/boards/<stamp>-<digest>.log and its final envelope to "
            "<stamp>-<digest>.report.json."
        ),
```

In `run`, between the `if whole_board and dry_run:` branch and `elif whole_board:`, insert:

```python
        elif whole_board and detach_run:
            # Read as `orchestrate.detach_board` and `detach.fork_detacher`
            # so a test can patch either.
            payload = orchestrate.detach_board(
                repo_dir=repo_dir,
                base_branch=base_branch,
                branch_prefix_of=board_prefix_of(branch_prefix),
                commands=list(verify),
                allow_no_verification=allow_no_verification,
                max_concurrent=lanes,
                detacher=detach.fork_detacher,
            )
```

Leave the `if detach_run: return` after the echo as it is (it already skips the board escalation check).

- [ ] **Step 4: Run the CLI tests**

Run: `uv run pytest tests/test_cli.py -k "detach or board" -v`
Expected: all PASS. Then `grep -n "BOARD_DETACH\|not --board" src tests` prints nothing.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat: am run --board --detach dispatches to orchestrate.detach_board (card 03f027ea)"
```

---

### Task 5: README documents `--board --detach`

**Files:**
- Modify: `README.md` lines 80, 89, 100 (+ one new bullet after it), 159
- Test: `tests/test_readme.py` (append after `test_detach_section_documents_envelope`)

**Interfaces:**
- Consumes: `detach.BOARD_REPORT_SUFFIX`, `detach.BOARD_LOG_SUFFIX` (Task 2); existing `_section`, `_fenced_json_lines`, `README` in `tests/test_readme.py`.
- Produces: README text only.

- [ ] **Step 1: Write the failing test**

Append after `test_detach_section_documents_envelope` in `tests/test_readme.py`:

```python
def test_detach_section_documents_the_board_form():
    """Card 03f027ea: `--board --detach` is documented in prose, with no second
    JSON example (`test_detach_section_documents_envelope` pins exactly one)."""
    section = _section("Running detached with `--detach`")
    assert "`--card`, `--milestone` and `--board`" in section
    assert "<data dir>/boards/" in section
    assert detach.BOARD_LOG_SUFFIX in section
    assert detach.BOARD_REPORT_SUFFIX in section
    for key in ("board", "detached", "pid", "log", "report", "levels"):
        assert f"`{key}`" in section
    assert "no `run_id`" in section
    assert "am watch --all" in section
    assert len(_fenced_json_lines(section)) == 1
    text = README.read_text(encoding="utf-8")
    assert "`--detach` with `--board`" not in text
    assert "or with `--board`" not in text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_readme.py::test_detach_section_documents_the_board_form -v`
Expected: FAIL on `"`--card`, `--milestone` and `--board`" in section`.

- [ ] **Step 3: Edit the README**

Line 80: replace the ending

```
a `--max-concurrent` below 1 (with `--milestone` or `--board`), and `--detach` with `--dry-run` or with `--board`.
```

with

```
a `--max-concurrent` below 1 (with `--milestone` or `--board`), and `--detach` with `--dry-run`.
```

Line 89: replace the opening sentence

```
`--detach` works with `--card` and `--milestone`. The command first does
```

with

```
`--detach` works with `--card`, `--milestone` and `--board`. The command first does
```

(the rest of that line is unchanged).

Line 100: replace

```
- `--detach` with `--dry-run` or with `--board` is refused as a usage error (exit 2).
```

with

```
- `--detach` with `--dry-run` is refused as a usage error (exit 2).
- With `--board`, the command runs the board's whole pre-flight here: the argument checks, the board read, the cycle check, each milestone's prefix and base, and the up-front claim check. A refusal is the usual envelope with exit code 3, and nothing starts. Then the board run moves to the background process. The envelope's `data` has the keys `board`, `detached`, `pid`, `log`, `report` and `levels`, and no `run_id`: each milestone's run is created when that milestone starts, and `am runs` or `am watch --all` finds it. `log` is `<data dir>/boards/<stamp>-<digest>.log` and `report` is `<data dir>/boards/<stamp>-<digest>.report.json`, where `<digest>` is the repository's digest. Both are mode 0600. The report holds the envelope `am run --board` would have printed. A claim another run takes after the up-front check shows up there as an `escalated` milestone.
```

Line 159: replace

```
`--board` with `--card` or `--milestone`, a blank `--branch-prefix` with `--board`, and `--detach` with `--board` are usage errors (exit 2).
```

with

```
`--board` with `--card` or `--milestone` and a blank `--branch-prefix` with `--board` are usage errors (exit 2).
```

- [ ] **Step 4: Run the README tests**

Run: `uv run pytest tests/test_readme.py -v`
Expected: all PASS, including `test_detach_section_documents_envelope` (still exactly one JSON example).

- [ ] **Step 5: Commit**

```bash
git add README.md tests/test_readme.py
git commit -m "docs: README documents am run --board --detach (card 03f027ea)"
```

---

### Task 6: e2e_fake — a detached board run outlives its parent

**Files:**
- Modify: `tests/e2e/test_detached_run.py` (imports; append one test)

**Interfaces:**
- Consumes: the e2e conftest fixtures `milestone_board` (one milestone, stories A (a1 → a2), B (b1), C (c1); `"root"`, `"milestone"`, `"subtasks"`), `fake_claude_bin`, `hold` (`arm()`, `release(*ids)`, `held_marker(id)`), `am(*args) -> (exit_code, envelope)`; this file's `detached_pids`, `_until`, `_lease`, `VERIFY`; `paths.project_digest` (Task 1), `detach.BOARD_LOG_SUFFIX` / `BOARD_REPORT_SUFFIX` (Task 2), the CLI wiring (Task 4).
- Produces: the only observation of "the child survives the parent" for the board form.

- [ ] **Step 1: Write the test**

In `tests/e2e/test_detached_run.py`, add `import re` to the imports (after `import os`), and update the module docstring's first line to:

```python
"""e2e_fake: one real `am run --milestone ... --detach` and one real `am run --board --detach` (cards aff9fdbf, 03f027ea).
```

Add after `POLL = 0.05`:

```python
BOARD_STAMP = re.compile(r"^\d{8}T\d{6}Z$")
"""`runs.RUN_ID_TIME_FORMAT`'s shape, the first half of a board file's stem."""
```

Append at the end of the file:

```python
@pytest.mark.e2e_fake
def test_a_detached_board_run_outlives_its_parent_and_leaves_its_report(
    milestone_board, fake_claude_bin, hold, am, detached_pids
):
    """Card 03f027ea: the board form. No `--branch-prefix`, so the milestone
    runs under its own card stem; a1's implement is held to keep the child live."""
    root = milestone_board["root"]
    milestone = milestone_board["milestone"]
    a1, a2 = milestone_board["subtasks"]["A"]
    (b1,) = milestone_board["subtasks"]["B"]
    (c1,) = milestone_board["subtasks"]["C"]
    hold.arm()
    hold.release(a2, b1, c1)  # only a1 is held; the rest pass straight through

    code, envelope = am(
        "run",
        "--board",
        "--repo-dir",
        str(root),
        "--base-branch",
        "main",
        "--verify",
        VERIFY,
        "--detach",
    )

    assert code == 0, envelope
    assert envelope["ok"] is True, envelope
    data = envelope["data"]
    assert set(data) == {"board", "detached", "pid", "log", "report", "levels"}
    assert data["board"] is True
    assert data["detached"] is True
    assert data["levels"] == [{"level": 0, "milestones": [milestone]}]
    pid = data["pid"]
    detached_pids.append(pid)
    log, report = Path(data["log"]), Path(data["report"])
    boards = paths.data_dir() / "boards"
    digest = paths.project_digest(root)
    stamp = log.name.partition("-")[0]
    assert BOARD_STAMP.match(stamp), log
    assert log == boards / f"{stamp}-{digest}{detach.BOARD_LOG_SUFFIX}"
    assert report == boards / f"{stamp}-{digest}{detach.BOARD_REPORT_SUFFIX}"
    assert stat.S_IMODE(os.stat(log).st_mode) == 0o600

    # The parent has exited (am() waited for it); the child is still working.
    _until(lambda: hold.held_marker(a1).exists(), "a1's implement being held")
    assert control.pid_alive(pid)
    assert os.getsid(pid) == pid
    assert not report.exists()

    code, listing = am("runs", "--repo-dir", str(root))
    assert code == 0, listing
    (row,) = listing["data"]["runs"]
    assert row["lease"]["pid"] == pid
    assert row["lease"]["live"] is True
    run_id = row["id"]

    hold.release(a1)
    _until(report.exists, "the board's report appearing")
    assert stat.S_IMODE(os.stat(report).st_mode) == 0o600
    final = json.loads(report.read_text(encoding="utf-8"))
    assert final["ok"] is True, final
    assert final["data"]["board"] is True, final
    assert final["data"]["ok"] is True, final
    (entry,) = final["data"]["milestones"]
    assert entry["milestone_id"] == milestone
    assert entry["status"] == "done", entry
    assert entry["run_id"] == run_id
    _until(lambda: _lease(root, run_id) is None, "the child releasing its lease")
```

- [ ] **Step 2: Run it on the pre-Task-4 tree to see it fail (optional sanity check)**

If executing tasks in order this step is already past; otherwise, before Task 4 the command exits 2 and the test FAILs at `assert code == 0`.

Run: `uv run pytest -m e2e_fake tests/e2e/test_detached_run.py -v`

- [ ] **Step 3: Run it on the finished tree**

Run: `uv run pytest -m e2e_fake tests/e2e/test_detached_run.py -v`
Expected: both tests PASS (the milestone one unchanged, the new board one).

- [ ] **Step 4: Run the default suite**

Run: `uv run pytest`
Expected: all PASS within the tier budgets. Also run `uv run pytest -m e2e_fake tests/test_detach.py -v` (the fork tests) — PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_detached_run.py
git commit -m "test: e2e_fake detached board run outlives its parent and leaves its report (card 03f027ea)"
```
<!-- task-pipeline: validated -->
