# agent-manager

Python CLI (Typer + Pydantic), packaged with `uv`, mirroring the conventions of
the sibling `brd` project.

## Verification

Run the full suite with:

```bash
uv run pytest
```

There is no separate lint or typecheck command.

## Test tiers

`uv run pytest` runs the default `unit` + `git` tiers (target ≤90s serial). The
other tiers are opt-in: the `-m` expression in `pyproject.toml`'s `addopts`
excludes them, and a `-m` on the command line replaces it.

- unmarked = `unit` — pure functions and anything driven through an injected
  fake (`FakeLauncher`, `FakeDriver`, a fake `board_api`, `FakeBoard`); no
  subprocess of any kind (stub `brd`/`git`/`claude` on `PATH` exit 99). Default;
  ≤0.5s per test, tier ≤30s.
- `@pytest.mark.git` — real `git` in `tmp_path` only; no `brd`, no `claude`.
  Default; ≤2s per test, tier ≤45s.
- `@pytest.mark.brd` — the real `brd` binary (adapter contract). Opt-in:
  `uv run pytest -m brd`; tier ≤90s.
- `@pytest.mark.e2e_fake` — production wiring under the fake `claude`, one test
  per scenario family. Opt-in: `uv run pytest -m e2e_fake`; tier ≤8min.
- `@pytest.mark.soak` — concurrency/race stress, run nightly. Opt-in:
  `uv run pytest -m soak`; no budget.
- `@pytest.mark.e2e` — real `claude`, costs real money. Opt-in:
  `uv run pytest -m e2e`. Hard cap of 5 tests; each needs a `justification:`
  docstring line naming what `e2e_fake` cannot observe.

Placement rule: a test's tier is chosen by what it actually spawns or touches,
not by the directory it lives in. `tests/conftest.py` auto-marks unmarked items
under `tests/steps/` as `git` and under `tests/e2e/` as `e2e_fake`, but only as a
default — mark the test explicitly when what it spawns says otherwise.

The full table is in design spec §14
(`docs/superpowers/specs/2026-09-23-agent-manager-design.md`).

## Conventions

- Source lives under `src/agent_manager/`, tests mirror it under `tests/`.
- Pydantic models for anything validated at a process boundary (harness result
  files); plain dataclasses are fine for internal-only state.
- CLI output is JSON by default, `--pretty` for humans — the same envelope shape
  as `brd` (`{"ok": true, "data": ...}`).
- The design spec is the source of truth:
  `docs/superpowers/specs/2026-09-23-agent-manager-design.md`.
