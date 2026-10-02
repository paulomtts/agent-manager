# Test tiers — design addendum

Date: 2026-10-02
Extends: `2026-09-23-agent-manager-design.md` §14 (the testing-tier doc every milestone plan has
been placing tests against).
Status: proposed. Decisions V1–V9. No milestone scope assigned yet — see §7.

## 1. Why this shape

The default `uv run pytest` invocation — the one the task workflow runs after every single
subtask, dozens of times a day — currently takes **17-20 minutes** for 2592 tests. Measured with
`--durations=0` on a quiet machine: **2592 passed in 1267s**; **242 tests (9%) take >1s and
consume 996s (79%)** of that; 470 tests take under 100ms. This is not a close call — the default
suite needs to be the fast, always-green loop, and it is currently slower than the milestone's own
implement phase for some subtasks.

The root cause is not what it looks like at a glance. It is not git (a real `git init` + two
commits measured at 31ms), and it is not the opt-in, real-money `e2e` tier (5 tests, already
correctly excluded by `addopts`). It is two things:

- **`brd` is a uv-installed Python CLI; every invocation costs ~155ms of interpreter start-up**
  (measured: `brd init`/`add`/`show` all 150-160ms). Design §14's own tier rule places CLI and
  orchestration tests as "Engine tier on **Steps-tier fixtures** (a real temp git repo, **a real
  temp brd board**)" — a sentence every milestone plan since has copied verbatim. So every
  `run_milestone` test drives the real `brd` binary through `board.roots/tree/show/set_status/
  comment_add/comment_list`, 20-60 spawns per test, 3-10s each. `tests/test_orchestrate.py` alone:
  168 tests, 445s.
- **§14 has no tier for the "fake claude" tests.** `tests/e2e/test_production_wiring.py:4-7` says
  so verbatim: "None of the five tiers in design §14 covers this … carries no marker and no skip
  and runs in the default suite." Nine `tests/e2e/*` modules each carry a
  `test_this_module_runs_in_the_default_suite_unmarked` test whose entire job is asserting that
  this is still true. ~177 of these tests spawn a real OS subprocess (a fake `claude` stand-in) per
  phase, per subtask — 388s total, with only 5 tests across the whole suite actually marked `e2e`.

This addendum defines the missing tiers, gives `board.py` the injection seam it never had (every
other side-effecting module — `worktree.run_git`, `verify.CommandRunner`, `dispatch`'s
`LauncherFn` — already has one; `board.py:137-165`'s `_run` calls `subprocess.run` directly), and
re-tiers the existing suite against the result. The goal is **re-tiering and de-duplication, not
losing coverage**: nothing here proposes deleting a test for being slow without replacing what it
proved with a faster equivalent.

## 2. What was found

Measured on this worktree (`uv run pytest -q --durations=0`); a concurrent second run from another
session inflated absolute numbers by an estimated 10-20%, but the shape — which files, which
pattern — is not in question.

| file | tests | seconds | why |
|---|---|---|---|
| `tests/test_orchestrate.py` | 168 | 445 | 80 tests on the `project` fixture (`:726`) build a real git repo *and* a real brd board via `_milestone()` (`:758-786`); `run_milestone` itself calls `board.roots/tree` (`orchestrate.py:1535,1540`) and `rollup.set_status` (3 brd calls per status write, `rollup.py:119-137`) regardless of which driver is injected |
| `tests/e2e/*` (10 unmarked modules) | 177 | 388 | real `cli.app` → real `ClaudeAdapter` → real `Popen` of a fake `claude` per phase; per-test `git init`+`brd init`+5-10 `brd add` (`tests/e2e/conftest.py:151-182,273-311`) |
| `tests/test_cli.py` | 234 | 257 | 79 tests ride `project`/`cards`/`milestone_board`/`resume_board` (`:1148-1195,2502-2532`); fixture **setup alone is 72s** — `milestone_board` is 18 brd spawns (~2.8s) before the test body even starts |
| `tests/steps/test_rollup.py` | 29 | 90 | real brd; three tests (`:439,269`, and the failed-rollup-lock test) are explicit concurrency/soak probes and are 72% of the file's cost |
| `tests/test_board.py` | 68 | 33 | real brd per test; `:576` alone is 9-11s (8 threads × 25 `brd update` = 200 spawns, a deliberate concurrency probe) |

Premise corrections, so the fix targets the right files: `tests/e2e/test_fake_claude.py` (110
tests) costs only 2.1s — 12 spawn the fake-claude script, 26 build a git repo, the rest are pure
and already fast. `tests/test_comments.py` costs 0.1s and `tests/test_store.py` 9.3s (36ms/test,
SQLite in `tmp_path` is not a problem). 18 `sleep` calls total ≈5s — real but third-order next to
the brd/subprocess cost.

**Concrete duplication found, not just slowness:**

- **Scenario twins**: the same behaviour asserted once cheaply in `test_orchestrate.py` via
  `FakeDriver`, and again expensively in `tests/e2e/*` via 20+ real child processes. At least 12
  named pairs (e.g. `test_orchestrate.py:3467` ↔ `e2e/test_parallel_milestone.py:477`; the full
  list is in the milestone's stories below). One wiring test per scenario family is enough once the
  unit twin exists.
- **8 meta tests** (`test_this_module_runs_in_the_default_suite_unmarked`) whose assertion is "the
  bug this addendum fixes is still present." They go away with the fix, not survive it.
- **Comment bodies asserted twice**: `test_orchestrate.py` re-asserts exact comment text (`:5282`,
  `:5446`, `:5574`, …) that `test_comments.py` already pins as the golden body. Orchestrate's own
  tests only need to assert outbox keys/states.
- **`--pretty` tested 8 times** (`test_cli.py:64,1863,2767,3454,3531,3707,4758,5505`) — it is one
  function (`render`); one direct test plus one parametrized smoke covers it.
- **14 copies** of a `requires_git`/`requires_brd` `skipif` helper, 3+ copies of `project`/
  `temp_board`/`_git`/`_add_card`/`_plant_lease` fixtures across files.
- **Mis-tiered despite an existing fake**: `comments.py:412`'s `board_api` injection seam already
  lets `test_comments.py` fake the board; `test_orchestrate.py` and `test_cli.py` don't use the
  equivalent because `board.py` itself has no seam to fake.

## 3. Decisions

**V1 — Six tiers, by marker.** `unit` (default, no marker): pure functions and anything driven
through an injected fake (`FakeLauncher`, `FakeDriver`, a fake `board_api`, the new `FakeBoard`
below) — no `subprocess` of any kind, budget ≤0.5s per test, whole tier ≤30s. `git` (default,
`@pytest.mark.git`): real `git` in `tmp_path` only, no `brd`, no `claude` — budget ≤2s per test,
whole tier ≤45s. `brd` (opt-in, `-m brd`): the real-`brd` adapter contract — budget ≤90s. `e2e_fake`
(opt-in, `-m e2e_fake`): the fake-claude wiring tests, one per scenario family — budget ≤8min.
`soak` (opt-in, `-m soak`, nightly): concurrency/race stress, no budget. `e2e` (existing, opt-in,
real money): **hard-capped at 5 tests**, each requiring a `justification:` docstring line naming
what `e2e_fake` cannot observe. Default run (`unit`+`git`) target: ≤90s serial.

**V2 — pytest mechanics in `pyproject.toml` and `tests/conftest.py`.**
```toml
[tool.pytest.ini_options]
addopts = '--import-mode=importlib -m "not brd and not e2e_fake and not soak and not e2e" --durations=15 --durations-min=0.5'
markers = [
  "git: real git in tmp_path; part of the default run",
  "brd: executes the real brd binary; opt-in (-m brd)",
  "e2e_fake: production wiring under the fake claude; opt-in (-m e2e_fake)",
  "soak: concurrency stress; opt-in, no budget",
  "e2e: real claude, costs money; opt-in; hard cap of 5",
]
```
`tests/conftest.py` gains, alongside the existing data-dir guard (`:40-72`): a
`pytest_collection_modifyitems` hook that auto-marks everything under `tests/e2e/` as `e2e_fake`
and everything under `tests/steps/` as `git` unless already marked otherwise, and fails collection
if more than 5 items carry `e2e` or any `e2e` item's docstring lacks a `justification:` line; one
`pytest_runtest_setup` that skips `git`/`brd`-marked items when `shutil.which` can't find the
binary, replacing the 14 scattered `requires_git`/`requires_brd` copies; an autouse fixture that,
for items carrying no tier marker, shadows `PATH` with stub `brd`/`git`/`claude` scripts that exit
99 (the same PATH-shim technique already used at `steps/test_rollup.py:578-600` and
`e2e/test_board_comments.py:214-232`) so an accidental real spawn in the unit tier fails loudly
instead of silently costing 150ms; a `pytest_runtest_makereport` check failing any unmarked test
over 0.5s or `git`-marked test over 2s.

**V3 — `board.py` gets the injection seam every other side-effecting module already has.** A
module-level `run_brd: Callable[[Sequence[str], Path, str | None], subprocess.CompletedProcess] =
_run` (mirroring `steps/worktree.py:56`'s `GitRunner` and `steps/verify.py:95`'s `CommandRunner`);
every public function (`show/tree/roots/set_status/comment_add/comment_list`, `board.py:231-345`)
calls `run_brd` instead of the module-level `_run` directly. A `FakeBoard` fixture in
`tests/conftest.py` holds an in-memory card tree and answers each argv with the same
`{"ok": true, "data": ...}` envelope shape `_decode` (`board.py:167`) expects, recording every
write. One new `brd`-tier test pins `FakeBoard`'s envelopes against real `brd show`/`tree`/
`comment list` output (the same "conftest twin" pinning pattern already used at
`tests/e2e/test_fake_claude.py:39-50`), so the fake can't silently drift from the real CLI's shape.

**V4 — `test_orchestrate.py` moves off the real board.** The `project`/`_milestone` fixtures
(`:726-786`) keep their real git repo (git is cheap and is what a worktree test is actually about)
and drop `brd init`/`brd add`/`brd block` in favor of `FakeBoard`. Comment-body assertions
(`:5251,5282,5446,5574,5707,…`) become outbox key/state assertions; the exact body text is
`test_comments.py`'s job alone. The scenario twins already covered more cheaply here than in
`tests/e2e/*` are deleted from the e2e side in V6, not duplicated in both places.

**V5 — `test_cli.py` moves off the real board.** `cards`/`milestone_board`/`resume_board`
(`:1148-1195,2502-2532`) move to `FakeBoard`. Five tests that monkeypatch the command body away
entirely yet still build a real repo+board for no reason (`:2205,2291,2321,2459,4793`) take a
literal card-id string instead. The 8 `--pretty` tests (`:64,1863,2767,3454,3531,3707,4758,5505`)
collapse to the existing direct test of `render` (`:64`) plus one parametrized `CliRunner` smoke on
the cheapest command.

**V6 — `tests/e2e/*` becomes the `e2e_fake` tier, trimmed to one wiring test per scenario.** The
directory auto-mark in V2 handles the marker; this decision is about content. Delete the 8
`test_this_module_runs_in_the_default_suite_unmarked` tests (the policy they guard against is now
enforced structurally). Delete the scenario twins already covered in `test_orchestrate.py`:
`e2e/test_parallel_milestone.py:477,441,513,245`, `e2e/test_integrate.py:360,302`,
`e2e/test_milestone_run.py:69`, `e2e/test_board_comments.py:290,337,438,543`,
`e2e/test_production_wiring.py:41` — one wiring test per module stays to prove the real subprocess
path still works end to end.

**V7 — `tests/e2e/test_fake_claude.py` splits by what it actually touches.** The ~70 pure tests
(parsing, schema) move to the default tier (a new `tests/test_fake_claude_parsing.py`, or stay in
place unmarked once the directory auto-mark is scoped to exclude them — implementer's call, say so
in the PR). The ~12 process-spawning tests (`_run_fake`, e.g. `:271-331,766,946,1532,1553`) and ~26
git-repo tests (`_implement_repo`, `:372`) get `e2e_fake`/`git` respectively.

**V8 — `tests/steps/test_rollup.py` and `tests/test_board.py` split by real-brd vs. soak.** Both
files' non-concurrency tests move to `brd`. The three explicit concurrency/stress probes
(`test_rollup.py:439,269` and `test_board.py:576`) move to `soak` — they are deliberate stress
tests per spec §17, not regression tests that need to run on every subtask.

**V9 — The `e2e` tier gets its cap and its accounting fixed.** The existing 5 `e2e` tests each gain
a `justification:` docstring line (satisfying V2's collection check). `tests/harness/
test_launcher.py`'s grandchild-kill test (`:96-126`, a 2.0s sleep) moves to `soak`; its two
remaining timeouts (`:76-93` 0.5s, `:337-346` 0.3s) shrink to 0.2s/0.1s without weakening what they
assert. `tests/test_store.py:2800`'s `range(20)` lease-race parametrization (40 process spawns for
one property) moves to `soak`, or shrinks to `range(3)` if a story wants to keep a cheap smoke of
it in the default run — implementer's call.

## 4. File map

| File | Change |
|---|---|
| `pyproject.toml` | new markers, `addopts` tier exclusion, `--durations` reporting (V1, V2) |
| `tests/conftest.py` | collection-hook auto-mark, marker-driven skip (replaces 14 copies), unit-tier PATH-shim guard, per-test budget check, `e2e` cap + `justification:` check, consolidated `project`/`temp_board`/`_git`/`_add_card`/`_plant_lease` fixtures, new `FakeBoard` (V2, V3) |
| `src/agent_manager/board.py` | `run_brd` injection seam (V3) |
| `tests/test_orchestrate.py` | `project`/`_milestone` onto `FakeBoard`; comment assertions → keys/states (V4) |
| `tests/test_cli.py` | `cards`/`milestone_board`/`resume_board` onto `FakeBoard`; 5 fixture-less tests; `--pretty` collapse (V5) |
| `tests/e2e/*.py` (10 modules) | directory auto-mark absorbs the marker; 8 meta tests deleted; named twins deleted (V6) |
| `tests/e2e/test_fake_claude.py` | split pure vs. process/git (V7) |
| `tests/steps/test_rollup.py`, `tests/test_board.py` | `brd`/`soak` split (V8) |
| `tests/harness/test_launcher.py`, `tests/test_store.py` | timeout shrink, grandchild-kill → `soak`, lease-race → `soak` (V9) |
| `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §14 | replace "Engine tier on Steps-tier fixtures (a real temp git repo, a real temp brd board)" with the six-tier table above |
| `CLAUDE.md` | name the markers; document `uv run pytest -m brd` / `-m e2e_fake` / `-m soak` as the opt-in commands |

## 5. Compatibility

No change to any CLI-observable behaviour — this addendum touches only `board.py`'s internal
seam (same public function signatures, same envelopes) and the test suite itself. The default
`uv run pytest` command stays the same invocation; what changes is which tests it runs and how
fast. Every retiered test keeps its assertions; nothing is deleted without either a faster
equivalent already existing (the unit-tier twin) or the deleted test being a meta-test whose only
job was asserting the bug this addendum fixes.

## 6. Testing

Each story proves itself by the suite getting faster without getting smaller in coverage:
- V3: the new `FakeBoard` pinning test (`brd` tier) is the oracle that the fake matches real `brd`.
- V4/V5: run both the before and after test files with `-k` filters against the same scenarios and
  diff pass/fail — identical outcomes, different wall time.
- V6/V7/V8: `uv run pytest -m e2e_fake`, `-m brd`, `-m soak` each still pass standalone after the
  split (this is the main regression risk — a retiered test silently depending on fixture state
  that only existed in the old default run).
- V9: the 5 `e2e` tests still pass when run by hand (`-m e2e`, not part of CI).
- Final: `uv run pytest --durations=0` on a quiet machine shows the default tier at or under the
  90s budget in V1, and a full `-m "git or brd or e2e_fake or soak"` run still passes end to end.

## 7. Suggested sequencing

V1 (tier definitions, enforcement) and V3 (`FakeBoard` seam) have no dependency on each other and
can land first, in parallel. V4 and V5 (the two big real-board conversions) both need V3's
`FakeBoard` to exist. V8, V6, V7, V9 each only need V1's marker names registered, not V3's
`FakeBoard` — independent of V4/V5 and of each other. A final proof-and-docs pass needs the real
conversions (V4, V5) done to measure the actual new default-tier runtime honestly.

## 8. Out of scope

- Adding `pytest-xdist`/parallel test execution as the pipeline's canonical verify command — worth
  revisiting once the serial default tier is already fast; changing CLAUDE.md's verify command is
  a bigger decision than this addendum's budget.
- Any change to the pygents engine, the checkpoint format, the harness adapter contract, or
  `dispatch.py`'s `LauncherFn` seam (already correct, not part of the problem).
- Reducing the `e2e` tier below its current 5 tests, or changing what any of those 5 specifically
  verify — V9 only adds accounting (the `justification:` line), not a re-scope.
- Milestone 14's `am run --board` work (separate, pre-existing, unrelated spec) — this addendum
  does not touch its files or cards.
