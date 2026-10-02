# Register the markers and addopts tier exclusion — subtask 19b291e9

Parent story: 186fe934 "Define and enforce the test tiers". Source design: `docs/superpowers/specs/2026-10-02-test-tier-design.md` §3 V1 (tiers) and V2 (mechanics, `pyproject.toml` half only); §4 file map row for `pyproject.toml`.

## Scope

Edit only `pyproject.toml`, `[tool.pytest.ini_options]`:

1. **Markers.** Add four entries to the existing `markers` list, worded exactly as V2 gives them:
   - `"git: real git in tmp_path; part of the default run"`
   - `"brd: executes the real brd binary; opt-in (-m brd)"`
   - `"e2e_fake: production wiring under the fake claude; opt-in (-m e2e_fake)"`
   - `"soak: concurrency stress; opt-in, no budget"`
   
   Leave the existing `e2e` marker line exactly as it is now. Do not rewrite it to V2's shorter wording.
2. **addopts.** Change `--import-mode=importlib -m "not e2e"` to `--import-mode=importlib -m "not brd and not e2e_fake and not soak and not e2e" --durations=15 --durations-min=0.5`. The pytest options are taken from V2 line 97. Any TOML quoting style that produces that string is fine.
3. **Comment upkeep.** The comment block above `addopts` currently describes only `-m "not e2e"`. Update it so it says that the opt-in tiers (`brd`, `e2e_fake`, `soak`, `e2e`) are excluded by default, that a `-m` given on the command line replaces this one (for example `-m brd`, `-m e2e_fake`, `-m e2e`), and that `--durations` is there to keep slow tests visible. Keep the importlib-mode explanation and the `pythonpath` comment.

`testpaths`, `asyncio_mode` and `pythonpath` stay unchanged. Do not add `--strict-markers` or any other option that V2 does not list.

## Observable behavior

- `uv run pytest --markers` lists `git`, `brd`, `e2e_fake`, `soak` and `e2e` with the descriptions above.
- `uv run pytest` selects exactly the same tests as before. Nothing carries the new markers yet, so the extended `-m` expression excludes the same set as `not e2e`. The pass, fail and deselect counts must match the pre-change baseline.
- The default run's output now ends with a "slowest durations" section: at most 15 entries, each at least 0.5s.
- Using a new marker no longer raises `PytestUnknownMarkWarning`.
- `uv run pytest -m e2e` still selects the real-harness e2e tests. A command-line `-m` still overrides the one in addopts.

## Error paths

- If the `-m` expression is malformed, for example because of a TOML quoting mistake, pytest stops at startup with a usage error. The full-suite run catches this.
- If the deselected count is different from the pre-change baseline, the expression is wrong. It must exclude exactly what `not e2e` excluded today.

## Out of scope

The following belong to sibling subtasks that are blocked on this card:

- All `tests/conftest.py` hooks:
  - auto-marking by directory (b4edda6b)
  - the PATH-shim autouse fixture and the per-test duration budget check (3202b0b0)
  - the e2e cap/justification collection check and the `pytest_runtest_setup` skip hook that replaces the scattered `requires_git`/`requires_brd` copies (3aa663b8)

Also out of scope:

- Marking any existing test.
- FakeBoard work (V3–V5).
- e2e `justification:` docstrings (V9).
- Replacing design-doc §14.
- pytest-xdist.
- Any change to the engine, checkpoint format, harness adapter contract, `dispatch.py`'s `LauncherFn` seam, or the e2e tier's contents.
- Milestone 14's `am run --board`.

## Tests

This card adds no test code. It only changes configuration, and V2 says the new marks must not change behavior until the sibling cards apply them. Placement is still checked against the V1 rule, because the only way to verify this card is to run the existing suite:

- **Regression: the existing default suite** (`uv run pytest`). This is the `unit` + `git` default run. Under V1 that run is unit-tier and git-tier work, decided by what each test spawns, and the run's contents are unchanged. It must be green, with the same selected and deselected counts as before the change, plus the new durations report.
- **No new tier-gated test is added.** A test that asserts on marker registration or addopts would have to spawn pytest as a subprocess. That would put it outside the `unit` tier, and it would duplicate what the sibling collection hooks will enforce. Leave it out. Check registration by hand with `uv run pytest --markers`.

## Verification

- Full suite: `uv run pytest`
- Typecheck: none
- Lint: none

---

# Register the markers and addopts tier exclusion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Register the `git`, `brd`, `e2e_fake` and `soak` pytest markers and extend `addopts` so the opt-in tiers are excluded by default and slow tests are reported, without changing which tests the default run selects.

**Architecture:** A single configuration edit to the `[tool.pytest.ini_options]` block of `pyproject.toml`. No source or test code changes. The "failing test" for this card is an observable pytest command (`--markers` output, deselect count) captured before the edit, since the spec forbids a subprocess-spawning config test in the unit tier.

**Tech Stack:** pytest 9 (`pytest>=9.1.1`), pytest-asyncio, `uv`, TOML.

**Spec:** `docs/superpowers/specs/task-register-the-markers-19b291e9-design.md` (this card), derived from `docs/superpowers/specs/2026-10-02-test-tier-design.md` §3 V1/V2 and §4.

## Global Constraints

- Only file modified: `pyproject.toml` (plus this plan file already on disk). No change to `tests/conftest.py` or any test.
- Marker strings, verbatim: `"git: real git in tmp_path; part of the default run"`, `"brd: executes the real brd binary; opt-in (-m brd)"`, `"e2e_fake: production wiring under the fake claude; opt-in (-m e2e_fake)"`, `"soak: concurrency stress; opt-in, no budget"`.
- Existing `e2e` marker line stays byte-for-byte unchanged.
- addopts value, verbatim: `--import-mode=importlib -m "not brd and not e2e_fake and not soak and not e2e" --durations=15 --durations-min=0.5`.
- `testpaths`, `asyncio_mode`, `pythonpath` unchanged. No `--strict-markers`, no other added option.
- Verification command: `uv run pytest`.

## Review Focus

- TOML quoting of the `-m` expression: an escaping slip (e.g. unbalanced `\"`) gives pytest a broken expression and it aborts at startup with a usage error; a reasonable person expects the default run to start normally. Pinned by Task 1 Step 5 (full-suite run) and Step 3 (assert parsed string with `tomllib`).
- Deselect count drift: if the expression excludes more or fewer tests than `not e2e` did, tier selection silently changes; expected is an identical `N passed, M deselected` line. Pinned by Task 1 Step 1 (baseline) and Step 5 (compare).
- Command-line `-m` override: `uv run pytest -m e2e --collect-only -q` must still select the e2e tests instead of combining with the addopts expression; expected is the same selected count as before. Pinned by Task 1 Step 1 and Step 6.
- The `--durations-min=0.5` flag must not hide the durations section header or error out; expected is a "slowest 15 durations" section (possibly with fewer entries). Pinned by Task 1 Step 5.
- Accidental collateral edits (dropping the importlib explanation or the `pythonpath` comment, rewording the `e2e` line). Expected: those lines unchanged. Pinned by Task 1 Step 7 (`git diff` review).

---

### Task 1: Register tier markers and extend addopts in pyproject.toml

**Files:**
- Modify: `pyproject.toml:33-41` (the `markers` list and the comment block + `addopts` line inside `[tool.pytest.ini_options]`)
- Test: none added (see spec "Tests"); verification is the existing default suite, which is the `unit` + `git` default run under V1.

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: registered markers `git`, `brd`, `e2e_fake`, `soak` (plus existing `e2e`), and a default `-m` expression `not brd and not e2e_fake and not soak and not e2e`. Sibling subtasks b4edda6b, 3202b0b0, 3aa663b8 rely on these exact marker names.

- [ ] **Step 1: Record the pre-change baseline**

Run from the worktree root `/home/paulomtts/Code/agent-manager/.claude/worktrees/m15/task-register-the-markers-19b291e9`:

```bash
uv run pytest -q 2>&1 | tail -n 3
uv run pytest -m e2e --collect-only -q 2>&1 | tail -n 1
```

Expected: the first prints a summary line like `<P> passed, <D> deselected in <T>s` (all green). The second prints `<E>/<TOTAL> tests collected (<X> deselected) in <T>s`. Write down `<P>`, `<D>` and `<E>` exactly; Step 5 and Step 6 compare against them.

- [ ] **Step 2: Run the failing check (RED)**

```bash
uv run pytest --markers | grep -E '^@pytest\.mark\.(git|brd|e2e_fake|soak):'
```

Expected: no output and exit status 1 (grep found nothing) — the four markers are not registered yet.

- [ ] **Step 3: Edit `pyproject.toml`**

Replace lines 33-41 (from `markers = [` through the `addopts = ...` line) with exactly this. Line 42-44 (`pythonpath` comment and `pythonpath = ["tests"]`) stay as they are.

```toml
markers = [
    "e2e: slow, opt-in, costs real money -- drives the toy card against the real `claude` on PATH. Excluded by the `-m \"not e2e\"` in addopts; run it with `uv run pytest -m e2e` (a bare path invocation is still deselected, so pass `-m e2e` alongside any path).",
    "git: real git in tmp_path; part of the default run",
    "brd: executes the real brd binary; opt-in (-m brd)",
    "e2e_fake: production wiring under the fake claude; opt-in (-m e2e_fake)",
    "soak: concurrency stress; opt-in, no budget",
]
# importlib mode, not the default prepend mode: tests/steps/test_integrate.py and
# tests/e2e/test_integrate.py share a basename, and prepend mode names test modules
# after their basename alone, so collecting both aborts with "import file mismatch".
# The `-m` expression keeps the opt-in tiers (brd, e2e_fake, soak, and the paid
# real-harness e2e) out of the default run, leaving unit + git. A `-m` passed on the
# command line replaces this one, which is how `-m brd`, `-m e2e_fake`, `-m soak` or
# `-m e2e` select a tier. `--durations=15 --durations-min=0.5` lists the slowest
# tests (0.5s and up) after every run so tests drifting past their tier budget stay visible.
addopts = '--import-mode=importlib -m "not brd and not e2e_fake and not soak and not e2e" --durations=15 --durations-min=0.5'
```

Then confirm the parsed value is exactly the spec string:

```bash
uv run python -c 'import tomllib; o=tomllib.load(open("pyproject.toml","rb"))["tool"]["pytest"]["ini_options"]; assert o["addopts"] == "--import-mode=importlib -m \"not brd and not e2e_fake and not soak and not e2e\" --durations=15 --durations-min=0.5", o["addopts"]; assert [m.split(":")[0] for m in o["markers"]] == ["e2e","git","brd","e2e_fake","soak"]; print("ok")'
```

Expected: `ok`.

- [ ] **Step 4: Re-run the marker check (GREEN)**

```bash
uv run pytest --markers | grep -E '^@pytest\.mark\.(git|brd|e2e_fake|soak|e2e):'
```

Expected: exactly five lines, one each for `e2e`, `git`, `brd`, `e2e_fake`, `soak`, with the descriptions from Step 3.

- [ ] **Step 5: Run the full suite and compare to baseline**

```bash
uv run pytest
```

Expected: pytest starts without a usage error; all tests pass; the final summary shows `<P> passed, <D> deselected` with the same `<P>` and `<D>` recorded in Step 1; above the summary there is a `slowest 15 durations` section (entries only for calls of 0.5s or more; it may say some durations were hidden). If `<D>` differs, the `-m` expression is wrong — fix Step 3, do not proceed.

- [ ] **Step 6: Confirm the command-line `-m` override still works**

```bash
uv run pytest -m e2e --collect-only -q 2>&1 | tail -n 1
```

Expected: the same `<E>` selected count recorded in Step 1 (the command-line `-m e2e` replaces the addopts expression rather than combining with it). Do not run the e2e tests themselves — they cost real money.

- [ ] **Step 7: Review the diff for collateral edits**

```bash
git diff -- pyproject.toml
```

Expected: only the four added marker lines, the rewritten `-m`/addopts comment lines, and the `addopts` line change. The `e2e` marker line, the three importlib-mode comment lines, `testpaths`, `asyncio_mode` and the `pythonpath` lines are untouched. `git status --short` shows no other modified file besides the plan/spec docs.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml docs/superpowers/plans/task-register-the-markers-19b291e9.md docs/superpowers/specs/task-register-the-markers-19b291e9-design.md
git commit -m "Register test-tier markers and exclude opt-in tiers in addopts"
```
