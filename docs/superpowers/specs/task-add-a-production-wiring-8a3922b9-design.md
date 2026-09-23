# Add a production-wiring test that runs under a fake claude executable (8a3922b9)

Subtask of story 01bad3fd ("Prove it: the production wiring under a fake claude, and a real-harness test"), milestone 7aa00a90. Blocked by the seams story 360cd141. Sibling 34d3388b (the opt-in real-harness test) is blocked by this card and shares this card's fixtures.

## Scope

One new test package, `tests/e2e/`, containing `conftest.py` (fixtures, shared with the sibling), a fake `claude` executable, and `test_production_wiring.py`. No production code is authored here. The seams this test exposes belong to 360cd141; see "Seams and amendments".

The test drives `cli.run_card` for one subtask card with **no `runner_factory` argument**, so the production path is exercised end to end: `cli.default_runner_factory` (`src/agent_manager/cli.py:588`) builds the real `dispatch.AgentRunner` with the real `harness/launcher.py:94` `run_direct`, which `Popen`s the real `ClaudeAdapter` argv whose `COMMAND` is the bare name `"claude"` (`src/agent_manager/harness/claude.py:26`), resolved on `PATH`. The test's only intervention is putting a fake `claude` first on `PATH`.

What this test is *not*: it is not a harness-adapter test (those assert the pure `build_command` with the launcher injected), not an engine test (those inject a fake adapter with canned result files), and not the real-harness end-to-end test (that is the sibling, marked and excluded). It is the wiring in between: real adapter, real launcher, real process, fake model.

Out of scope, per addendum §4: ancestor roll-up. Only the subtask's own board status, via `rollup.set_status`, is asserted.

## The fake executable

A single-file Python script written into a tmp directory, `chmod +x`, with a `#!` line, and that directory prepended to `PATH` for the duration of the test. It is the *only* `claude` the run can find.

Its contract, which is the point of the whole card:

- It receives `["claude", "--model", <model>, "--dangerously-skip-permissions", "-p", "Read <prompt_path> and follow the instructions in it exactly. ..."]`. It parses the prompt path out of that `-p` sentence (the format string is `harness/claude.py:30`) and reads the file.
- Everything else it needs — **the absolute result path and the JSON Schema of the expected result model** — it extracts from the *prompt text only*. It has no environment variable, no argv flag, no filename convention and no import of `agent_manager` that could tell it where to write. This is the load-bearing property: a brief that omits the Result contract (addendum R2) makes the fake unable to write anything, and the test fails. Do not give the fake a side channel to make it pass.
- It generates its result **from the embedded schema**, not from a hardcoded literal, so the fake cannot drift away from the models the run actually validates against. Where the schema alone is underdetermined (a gate needs a specific value, not merely a well-typed one), the fake overrides that single field by name.
- It appends one line per invocation to a log file whose path it also derives from the prompt/result paths (a sibling of the result path, i.e. under `paths.data_dir()`, never inside the worktree): phase name, `os.getcwd()`, result path. This log is what the cwd assertions read.
- It exits 0 and prints a short, usage-free line to stdout. stdout is a log only (D4).

### Per-phase behaviour

Seven agent phases: `explore`, `spec`, `validate_spec`, `plan`, `validate_plan`, `implement`, `review`. The fake identifies the phase from the prompt text (the rendered prompt names it) and:

- **explore** — `ExploreResult {refused, reason, summary, verification{full_suite, typecheck, lint}}`. `summary` must exceed `MIN_SUMMARY_LENGTH` (60) and not be a placeholder, and the suite must echo the caller-provided commands *exactly*, because both `exploration_output_gate` and `verification_gate` read it (`src/agent_manager/steps/reducers.py:257` and `:30`).
- **spec** — `SpecResult {path, note|None}`, and it writes the document at the path the phase declares (`writes: docs/superpowers/specs/{stem}.md` in `workflow/builtin/task.yaml:33`), inside the worktree.
- **validate_spec**, **validate_plan** — `CriticResult {blockers, reason, summary}` with no blockers, so `critic_blockers_gate` passes.
- **plan** — `PlanResult {path, self_reviewed, note}`, plus the document at `docs/superpowers/plans/{stem}.md`.
- **implement** — `ImplementResult {blocked, blocked_reason, resumed, plan_hash, report}`. It makes a real commit in the cwd it was launched in (the worktree), carrying a `Plan-Hash: <hash>` trailer, where `<hash>` is the first 8 lowercase hex characters of the sha256 of the plan file — computed, not invented, so it matches what review reports.
- **review** — `ReviewResult {findings, unresolved_blockers, fix_summary, porcelain, commit_count, tagged_count, plan_hash}`, computed by actually running `git status --porcelain` and counting commits and trailers in the worktree, so that `review_gate` sees an empty porcelain, a non-zero `commit_count`, `tagged_count == commit_count`, and `plan_hash_gate` sees the same hash `implement` reported.

Field spelling is whatever `src/agent_manager/steps/reducers.py` and the landed models agree on — see the naming seam below. The fake reads names off the embedded schema rather than hardcoding them, which makes it tolerant of the alias decision the seams story makes.

## Fixtures (`tests/e2e/conftest.py`)

Copy the Steps-tier patterns already proven in `tests/test_cli.py:996-1052` and `tests/steps/test_worktree.py`, generalised so the sibling can reuse them:

- `XDG_DATA_HOME` monkeypatched into `tmp_path`, so `paths.data_dir()` and brd's own database never touch the developer's home. Run state, prompts, result files and stdout logs therefore live outside the worktree, which is what lets the clean-worktree assertion mean something (design §14; D4).
- A real temp git repo on `main` with a committed baseline and `commit.gpgsign=false`, plus `brd init` and its markers committed, so porcelain starts empty.
- A milestone -> story -> subtask card chain (`run --card` requires the subtask to have a parent).
- `requires_git` / `requires_brd` skip guards, matching the existing fixtures.
- A fixture that materialises the fake `claude` and prepends its directory to `PATH`.

Verification commands are passed through `run_card(commands=["<a command that passes in the toy repo>"])` — non-empty, so `verification_gate` passes without `allow_no_verification`, and so the `verify` phase's `verification_passed_gate` has something real to be green about. The same list is what `exploration_output_gate` compares explore's echo against.

## Observable behaviour asserted

1. **Board status (R7).** The card reads `done` **on the board** — `board.show(card_id, repo_dir=root)` (or `brd show`), not merely `summary["status"]`. The summary can say `done` while the board was never touched, which is exactly the bug this assertion exists to catch.
2. **Every agent phase ran.** All seven phases have a recorded attempt with status `ok` in the `Store` for this run id.
3. **cwd is the worktree (R6, D7).** Each logged cwd equals `cli.worktree_for(root, branch)` = `<repo>/.claude/worktrees/<branch>`, *including explore*.
4. **Clean worktree.** `git status --porcelain` in the worktree is empty after the run: no result file, no prompt, no stray artifact was written inside it.
5. **No side channel.** The fake resolved its result path purely from prompt text; this is structural (the fake has no other source) rather than a separate assertion, but the test asserts the rendered prompt on disk actually contains the result path and the schema, so a regression in prompt composition fails with a clear message rather than as a mysterious missing-result-file.

## Error paths

- Fake not on `PATH`, or not executable: the run fails with a harness error. Not asserted as a behaviour; the fixture makes it impossible.
- Any phase failing a gate surfaces as `summary["status"] != "done"` with `failed_phase` and `detail`; the assertions above are written so the failure message names the phase, not just "expected done, got escalated".
- `run_card` raising `ParentlessCardError` if the fixture chain is wrong — a fixture bug, caught by the chain fixture itself.
- Timeouts: the fake is instantaneous; the default dispatch timeout applies unchanged.

## Suite placement

`tests/e2e/test_production_wiring.py` runs in the **default suite**: it launches no model, costs nothing, and must pass under the current `addopts = "--import-mode=importlib"` with `testpaths = ["tests"]`. **No `e2e` marker and no skip** on this test. Registering the `e2e` marker and adding `-m "not e2e"` to `addopts` is the sibling 34d3388b's job, and when it lands, this test must remain unmarked so it keeps running by default.

## Tests

Per the placement rule (design §14, lines 477-492; applied in the docstrings of `tests/test_cli.py:1-13` and `tests/test_store.py:1-11`), tiers are defined by the kind of code under test. None of the five existing tiers covers "the production wiring with a fake process", so the card assigns these to a new **production-wiring tier** in `tests/e2e/`, built on **Steps-tier fixtures** (real temp git repo, real temp brd board, `XDG_DATA_HOME` in `tmp_path`). Every test below states its tier explicitly.

1. `test_run_card_drives_every_phase_under_a_fake_claude_and_the_board_says_done` — production-wiring tier (tests/e2e). One `run_card` call, asserts (1) board status `done` and (2) seven `ok` agent attempts in the Store.
2. `test_every_agent_ran_in_the_subtask_worktree` — production-wiring tier. Reads the fake's cwd log; asserts every phase, explore included, ran in `worktree_for(root, branch)`.
3. `test_the_worktree_is_clean_after_the_run` — production-wiring tier. `git status --porcelain` empty; no result file or prompt landed inside the worktree.
4. `test_the_brief_carries_the_result_path_and_the_schema` — production-wiring tier. Reads the rendered `prompt.txt` off the attempt directory and asserts it states the absolute result path and embeds the model's JSON Schema (addendum R2). This is the assertion that makes the no-side-channel property explicit rather than incidental.
5. `test_the_spec_and_plan_documents_exist_where_the_phases_declared_them` — production-wiring tier. The `writes:` templates resolved to real files inside the worktree and were committed.
6. `test_the_implement_commit_carries_a_plan_hash_trailer_review_agrees_with` — production-wiring tier. The branch's commits all carry `Plan-Hash`, and `implement`'s and `review`'s reported hashes match, so `plan_hash_gate` passed for a real reason rather than vacuously.

The fake executable's prompt-parsing helpers, if factored out as importable pure functions, get **unit tests** (pure functions tier) in `tests/e2e/test_fake_claude.py`: parsing the path out of the `-p` sentence, and generating a schema-conforming payload from a given JSON Schema. Keep them small; the fake is test infrastructure, not a second product.

## Seams and amendments

Re-verified against the current base: the seams story has landed most of what this card was written to expose. Re-read the code before writing the test and treat anything still missing as a genuine gap to flag.

- Landed: `results.RESULT_MODELS` is populated (`SpecResult` included); `rollup.set_status` is the real step; the `worktree` phase is now first in `workflow/builtin/task.yaml`, so explore runs in an existing worktree; `prompt.py` composes a `## Result contract` section (result path and `model_json_schema()`); the reducers read both snake_case and camelCase (`_either_field`), so the fake, generating names from the embedded schema, works with either.
- Also landed: `critic_blockers_gate` is bound to `reducers.critic_blockers_gate` and the `spec` phase declares `result: SpecResult`. Re-confirm before writing; if anything regressed, flag it.
- `mark_in_progress`/`mark_done` are `best_effort: true`, so a run can report `done` while the board never changed. That is why assertion (1) reads the board (R7).
- Phase order: `plan_check` sits between `mark_in_progress` and `spec` and skips to `implement` only when a validated plan already exists; the fixture repo has none, so all seven agent phases run. `verify` is deterministic and runs `commands` for real.

**Expect failures first if any seam is incomplete.** Do not make the test pass by weakening an assertion, relaxing a gate, or handing the fake a side channel. Fixing `src/` is the seams story's job; flag a genuine gap rather than drift.
