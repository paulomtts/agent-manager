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
