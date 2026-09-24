# Subtask 34d3388b — the opt-in real-harness e2e test

Parent story 01bad3fd (milestone 7aa00a90). Blocked by 8a3922b9, which is done and owns everything this card builds on. Realises addendum R4 and section 3's acceptance line of `docs/superpowers/specs/2026-09-23-real-harness-design.md`: "Real harness, opt-in. `pytest -m e2e` runs the same toy card against a real `claude -p`. Excluded by default. Run by a human."

## Scope

Two files change.

1. A new test module under `tests/e2e/` holding exactly one test: the same toy card as the fake-claude production-wiring test, driven through `cli.run_card` with NO `runner_factory`, against whatever real `claude` is on `PATH`. It carries the `e2e` marker and self-skips when `claude` is absent.
2. `pyproject.toml` `[tool.pytest.ini_options]`: register the `e2e` marker under `markers = [...]` and extend `addopts` to `--import-mode=importlib -m "not e2e"`. `--import-mode=importlib` must stay, and so must the comment above it that explains why (two `test_loader.py` basenames collide in prepend mode).

Out of scope, and explicitly owned elsewhere: the fake `claude` script, `tests/e2e/conftest.py`'s toy-repo fixtures, and the default-suite production-wiring test and its assertions all belong to sibling 8a3922b9 and must not be edited, marked or skipped. Amendments R5 (`SpecResult` with `path` and `note | None`), R6 (worktree created before any agent phase, explore included) and R7 (`rollup.set_status` calls `board.set_status`) are production seams that earlier cards already landed; this card only observes them and must not re-implement or adjust them. Milestone orchestration, parallel stories, `integrate`, non-Claude harnesses and ancestor roll-up are addendum section 4 deferrals and stay untouched.

## Fixture reuse

`tests/e2e/conftest.py` is already present in this worktree (from the sibling's branch) and is the source of the toy repo: `module_monkeypatch`, `toolchain`, `project`, `cards`, `git()`, `VERIFY_COMMANDS = ("git rev-parse --verify HEAD",)`, `AGENT_PHASES`, and the downstream fixtures `run_tree`, `agent_attempts`, `worktree`, all of which consume a fixture named `completed_run`. Reuse them; do not duplicate a second toy repo.

The one thing that cannot be reused is `completed_run`: it depends on `fake_claude_bin`, which prepends a fake `claude` to `PATH`. This card defines, in the new module, a module-scoped fixture that is also named `completed_run`, so it overrides the conftest one and `run_tree`, `agent_attempts` and `worktree` resolve to it without duplication. It makes the same call — `cli.run_card(cards["subtask"], repo_dir=project, base_branch="main", branch_prefix=<this module's prefix>, commands=list(VERIFY_COMMANDS))` — with `fake_claude_bin` deliberately absent from its parameter list. Any new fixture this card needs lives in the new module, not in the shared conftest, for the marker reason below.

`toolchain` only checks `git` and `brd`, so it does not cover `claude`; the new module needs its own `shutil.which("claude")` guard.

## Observable behaviour

Running `uv run pytest` must not collect the new test for execution: it is deselected by `-m "not e2e"`, and every other test in the repo — including `tests/e2e/test_production_wiring.py` and `tests/e2e/test_fake_claude.py` — must keep running exactly as before.

Running `uv run pytest -m e2e` selects the new test and nothing else. With `claude` on `PATH` it performs one real, paid run of the toy card end to end. With `claude` absent it reports a skip whose message names `claude` and `PATH`, so a reader knows immediately that the tool is missing rather than that the test is broken.

The marker must be applied only inside the new module (a module-level `pytestmark` or a per-test `@pytest.mark.e2e`). Applying it from `tests/e2e/conftest.py` or via a package-wide `pytestmark` would mark the sibling's wiring test too, silently removing it from the default suite; the sibling's `test_this_module_runs_in_the_default_suite_unmarked` asserts against exactly that, and it must keep passing.

The marker must be registered in `pyproject.toml`, so no `PytestUnknownMarkWarning` appears and `-m e2e` is a documented selector rather than a typo-prone string.

### What the real run asserts

- The card ends `done`: the returned payload's `status`, and — per R7 and the `best_effort: true` on `mark_done` — `board.show(card_id, repo_dir=project).status == "done"` on the board itself.
- The branch carries commits beyond the base: `git rev-list main..HEAD` in the worktree is non-empty (a branch with no commits would otherwise satisfy the trailer loop vacuously).
- Every one of those commits carries a `Plan-Hash:` trailer, and every commit carries the *same* hash value — the real agent takes it from the implement brief, so a drifting value means the brief or the gate is not doing its job.
- Every recorded result file validates against its declared model: use the conftest `agent_attempts` fixture (the last attempt of each agent phase, built from `run_tree` via `store.open_db` + `store.load_run`), and for each agent attempt with a result file, resolve the phase's declared `result:` name through `agent_manager.results.RESULT_MODELS` and validate the file's contents against that model. R5's `SpecResult` is included by construction, not by a special case.

## Error paths

- `claude` not on `PATH`: `pytest.skip` with a message naming `claude` and `PATH`. This is the path exercised in CI and in this card's own verification.
- `git` or `brd` missing: handled by the shared `toolchain` fixture's existing skip; not re-implemented.
- The run fails or a gate rejects: the assertions fail with the payload's `failed_phase`, `detail` and `warnings` in the assertion message, the way the sibling's first test does, so a human debugging a paid run does not have to re-run it to learn where it stopped.
- An unregistered `result:` name or a malformed result file: surfaces as a validation failure naming the phase, not a bare `KeyError`.

## Tests

Per the test-placement rule in `docs/superpowers/specs/2026-09-23-agent-manager-design.md` section 14 (lines 477-492), which names five tiers — pure unit, steps, adapters, engine with a fake adapter, and one slow opt-in end-to-end test with a real harness.

1. **The real-harness toy-card test** (the whole deliverable, one test function). Tier: **end to end** — the single slow opt-in test the rule allows, in `tests/e2e/`, marked `e2e`, excluded from the default suite, run by a human. It makes the assertions listed above.

No other test is added. Assertion helpers, if any are extracted, are inlined in the module rather than promoted to a pure-unit-tier test of their own: this card's budget is one module, and the rule keeps other tiers for other kinds of subject.

## Verification of this card

None of these spend money; the real run is a human step and must not happen in the pipeline.

- `uv run pytest -m e2e --collect-only` lists the new test.
- `uv run pytest` deselects it, and the sibling's production-wiring tests still run and pass.
- `uv run pytest -m e2e -rs`, with `claude` absent from `PATH`, reports a skip whose reason carries the `claude`/`PATH` message.
