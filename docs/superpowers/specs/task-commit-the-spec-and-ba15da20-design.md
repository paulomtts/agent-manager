# Commit the spec and plan with the Plan-Hash trailer (card ba15da20)

## 1. Why this exists

Today nothing in the engine commits the spec and the plan. On the real branch the fake `claude` harness does it, which violates real-harness spec R4 (the fake must know no more than the brief and must not do work the engine owes) and leaves the production wiring untested. This subtask moves that commit into a deterministic step the engine runs, and makes that commit carry the `Plan-Hash` trailer that design §9's resume contract is built on: a commit with the current hash is resumed from, an untagged commit is debris.

Sibling `f26b377d` owns everything downstream of this step — the `plan_hash` phase *input* in `prompt.py`, adding it to `implement`'s `inputs:` in `task.yaml`, the coder role text, and teaching the fake harness to read the hash out of its brief. None of that belongs here. This card produces the hash, the commit, and the context entry the sibling will read.

## 2. Scope

One new module `src/agent_manager/steps/docs_commit.py`, its registration, one new deterministic phase in `workflow/builtin/task.yaml`, and tests. Nothing else in `src/` changes except `workflow/registry.py` and the document.

Out of scope, restated so it is not drifted into: milestone orchestration, parallel stories, `integrate`, non-Claude harnesses, ancestor rollup, counting reviews with git, and any change to `steps/reducers.py:plan_hash_gate` / `is_plan_hash` (the step must stay faithful to that definition, not redefine it).

## 3. The step

Public surface, mirroring the injection convention of `steps/worktree.py` (`GitRunner` parameter defaulting to a real `run_git`, so tests can observe argv while behaviour tests still use a real repo):

- a pure helper that takes the plan file's bytes (or path) and returns the first 8 lowercase hex characters of their SHA-256 — the same shape `reducers.is_plan_hash` accepts, and the value `review` will independently recompute.
- the step entry point, whose parameter names are what the engine binds by name from `subtask_context` plus `_document_paths`: `card_details` (the full `models.Card`, for `title`), `spec_path`, `plan_path` (repo-relative strings, already expanded from the phases' `writes:` templates by `prompt.expand_writes` and placed in the context by `engine._document_paths`), `worktree` (absolute path the git calls run in), and `git_runner` with a default. No `args:` in the document are needed — every value is already in the binding table under exactly these names. The step never touches the board and never reads `brd`.

Returns a plain dict `{"plan_hash": "<8 hex>"}` — a `Mapping`, as `engine._run_deterministic` requires, stored in the context under the phase name, so later phases read `context["docs_commit"]["plan_hash"]`. Plain dict, not Pydantic, per `CLAUDE.md` (internal-only state).

### Observable behaviour

1. Resolve `spec_path` and `plan_path` against `worktree`. Both must exist as files by the time this runs — the `spec` and `plan` agent phases wrote them, and `validate_plan` (and, once it lands, `mark_validated`) has already appended the validated marker.
2. Compute the plan hash from the plan file's bytes **as they are on disk at this moment**, i.e. with the validated marker already present. That ordering is the whole reason the phase sits after `mark_validated`: a hash taken before the marker would never match what `review` recomputes.
3. Stage exactly the two declared paths: one `git add --` invocation naming `spec_path` and `plan_path` and nothing else. `git add -A`, `git add .` and pathspecs with wildcards are forbidden; an unrelated stray or untracked file in the worktree must survive uncommitted.
4. If anything is staged for those two paths, commit with subject `docs: add spec and plan for <card title>`, a blank line, and a final trailer line `Plan-Hash: <hash>`. `<card title>` is `card_details.title` verbatim. The trailer is the last line of the message.
5. Return `{"plan_hash": hash}`.

### Idempotence (design §9 resume)

If step 3 produced nothing to commit for those two paths **and** the branch already carries a commit whose message contains this exact `Plan-Hash: <hash>` line, the step commits nothing and returns the same hash. That is the resumed-run path: re-running the phase after a kill is a no-op, and the commit count does not grow.

If there is nothing to commit but no commit on the branch carries this hash, that is not a silent success — the documents are tracked and unchanged but untagged. The step commits nothing it cannot commit; it raises (see error paths) rather than returning a hash that no commit on the branch corroborates, because `review`'s untagged-commit branch would otherwise be reading a lie.

### Error paths

- `spec_path` or `plan_path` missing, blank, not a string, or absent on disk → `ValueError` naming the offending path, raised before any git call.
- `worktree` not an absolute path, or `card_details` absent / carrying an empty title → `ValueError` before any git call (same pre-flight style as `worktree._required_absolute`).
- Either path escaping the worktree after resolution → `ValueError`; the expander already refuses `..` and absolutes, and the step does not trust that twice over.
- Any git invocation exiting non-zero → the `GitError` from the runner propagates unchanged. `engine._run_deterministic` catches everything and records the phase failed, so no swallowing here.
- Nothing to commit and no matching trailer on the branch → a raised error whose message names the hash and both paths.

## 4. Wiring

`src/agent_manager/workflow/registry.py`: add the step's dotted name to `BUILTIN_FUNCTION_NAMES` (sorted tuple) and `registry.register(...)` it in `default_registry()` with the **real imported callable**, in the "steps that already ship on this branch" block — not a `_placeholder`.

`src/agent_manager/workflow/builtin/task.yaml`: a new `kind: deterministic` phase with `run:` pointing at the registered name, placed after `mark_validated` and before `implement`. If `mark_validated` is not present on the branch this lands on, place it after `validate_plan` and before `implement`; the invariant is "after the marker is written, before the coder runs".

Branch state (verified): `mark_validated` (`plan_check.mark_validated`, registered) is already in `task.yaml` and `registry.py`, and `tests/e2e/` exists with `fake_claude.py` and `test_production_wiring.py`. The new phase goes directly after `mark_validated` and before `implement`; no fallback placement is needed.

`tests/e2e/fake_claude.py`: its `spec`/`plan` branches already only write documents. The only commit it makes is in the `implement` branch (`git add -A` then `git commit` with a `Plan-Hash` trailer computed via `plan_hash_of`), which is the coder's implementation commit, not the docs commit. That implement-side behaviour, and teaching the fake to read the hash from its brief, belongs to sibling `f26b377d`; leave it alone. What this card must guarantee is that the docs commit is made by the engine step before `implement` runs, so the fake's `add -A` no longer sweeps spec and plan into its commit. Adjust only what breaks: existing e2e assertions (e.g. `test_production_wiring.py` commit counts / `review["commit_count"] == len(revisions)` and tagged counts) must be updated to include the new docs commit.

## 5. Tests

Tier per design §14 (`docs/superpowers/specs/2026-09-23-agent-manager-design.md`, Testing). Tiers used: *unit* (pure functions), *steps* (real temporary git repo under `tmp_path`, no network, faking only what a real process cannot produce), *workflow/document*, *wiring* (default-suite production-wiring test with a fake `claude` on `PATH`).

In `tests/steps/test_docs_commit.py`:

1. The hash helper returns exactly 8 lowercase hex characters for known bytes, matches `hashlib.sha256(...).hexdigest()[:8]`, and satisfies `reducers.is_plan_hash`. — *unit* (pure function, same file is fine).
2. Changing one byte of the plan changes the hash; the marker being present is part of the hashed bytes. — *unit*.
3. Happy path in a real `tmp_path` repo: writes spec and plan, runs the step, exactly one new commit exists, its subject is `docs: add spec and plan for <title>`, its last line is `Plan-Hash: <returned hash>`, and the returned hash equals the helper's hash of the plan file. — *steps*.
4. Only the two declared paths are committed: an unrelated untracked file and an unrelated modified tracked file in the worktree are still uncommitted afterwards, and `git show --name-only` lists exactly the spec and plan. — *steps*.
5. Idempotent resume: calling the step twice in a row yields the same hash, and the commit count is unchanged by the second call. — *steps*.
6. Nothing to commit and no trailer on the branch (documents committed by an earlier commit with no `Plan-Hash`) raises, naming the hash. — *steps*.
7. Error pre-flight: missing plan file, missing spec file, relative `worktree`, missing/blank card title each raise `ValueError` and run no git command (assert against an injected runner that records argv and would fail the test if called). — *steps*.
8. No forbidden git verb: with a recording runner, assert no invocation contains `add -A`/`add .`, `reset`, `clean`, `checkout -f`, or `push`. — *steps*.

In `tests/workflow/`:

9. `tests/workflow/test_registry.py` (~line 84): the new name appears in the expected-name list and `resolve(name)` **is** the imported callable, not a placeholder. — *workflow/document*.
10. `tests/workflow/test_builtin_task.py`: update `EXPECTED_PHASES` and the phase-count test; add a document test asserting the new phase's index is after `mark_validated` (after `validate_plan` if the former is absent) and before `implement`, is `kind: deterministic`, and its `run:` is the registered name. — *workflow/document*.

In `tests/e2e/test_production_wiring.py` (default suite, fake `claude` on `PATH`):

11. After a full run, the docs commit exists with subject `docs: add spec and plan for <title>` and a `Plan-Hash:` trailer, and it precedes the fake harness's implement commit in `git log` (the fake no longer commits documents). — *wiring*.
12. `git status --porcelain` in the worktree is clean after the run. — *wiring*.

The whole default suite (`uv run pytest`) must stay green; no new test is opt-in or slow, and the single slow real-harness end-to-end test stays excluded as it is.
