# Subtask 69bcf17e: Default to four lanes and add --max-concurrent

Parent story: 1a254739 "Run a level's stories in parallel" (milestone cdbfa10d). Blocked by fe2c716b. Spec of record: `2026-09-24-parallel-stories-design.md` decision P1, plus main spec section 11 (default 4).

## Precondition

The sibling fe2c716b is present in this worktree. `orchestrate.run_milestone` takes `max_concurrent: int = 1`, rejects a value below 1 with `ValueError`, and records `models.RunConfig(max_concurrent_stories=max_concurrent)`. The store, board and repo locks (P2/P3) are also present. The main checkout at 72a6cff does not have this code, so this card has to be built on this worktree's branch. This card does not change `orchestrate.py`.

## Scope

1. **Model default.** `RunConfig.max_concurrent_stories` in `src/agent_manager/models.py` goes from `Field(default=1, gt=0)` to `Field(default=4, gt=0)`. `run_card` keeps building `models.RunConfig()` for a single card and stays single-lane. Its recorded config now reads 4, which is the documented default and changes nothing in how it runs.
2. **CLI flag.** `am run` gains `--max-concurrent N`, an int. The Option default is `None` so the code can tell whether the flag was given. The effective value is `N if given else 4`, and the help text says the default is 4. In the `--milestone` (non-dry-run) branch it is passed as `orchestrate.run_milestone(..., max_concurrent=<effective>)`. Orchestrate records it in the run's `RunConfig`, and this card does not record it separately.
3. **Validation (usage errors, exit 2, empty stdout).** `_check_run_targets` gains a `max_concurrent: int | None` parameter and raises `typer.BadParameter` (param hint `'--max-concurrent'`) when either of these holds:
   - `max_concurrent is not None and max_concurrent < 1`. The message says it must be at least 1.
   - `card is not None and max_concurrent is not None`. The message says `--max-concurrent` applies only to `--milestone`.
   Both checks run before the `HANDLED` try block, as the existing checks do. Nothing is read, dispatched or written. `min=1` on the Option is not used, so that every run-target refusal is worded and routed the same way.
4. **Dry run.** `--milestone X --dry-run --max-concurrent N` is accepted. `dry_run_payload` gains a keyword-only parameter `max_concurrent: int = 4`, and `dry_run_milestone` gains the same parameter and passes it through. The CLI passes the effective value, which is 4 when the flag is omitted. The additions to the payload are additive only:
   - a top-level `"max_concurrent": N`, and
   - on each level row, `"concurrent": min(len(level stories), N)`. This is how many of that level's stories would run together.
   `levels[].level`, `levels[].stories` and `already_done` are unchanged. The dry run still writes nothing: no Store, no run directory, no worktree, no board write.
5. **Docs.** `README.md` usage for `am run --milestone` shows `[--max-concurrent N]` (default 4) and gets one line saying `--max-concurrent 1` runs stories one at a time as before. The README refusal list gains two entries: `--max-concurrent` with `--card`, and `--max-concurrent` below 1. In the main spec section 10 (`2026-09-23-agent-manager-design.md` around line 415), the sentence that calls `--max-concurrent` deferred and runs sequential changes to say that a level's stories run on up to `--max-concurrent` lanes (default 4), with a pointer to the parallel-stories addendum P1. Nothing else in that spec changes.

## Out of scope

Everything inside `orchestrate.py`: the lane pool, the barrier, the stop Event, the escalation payload, and recording the config. Also out: the locks (P2/P3), Integrate, per-story readiness, milestone-aware `am resume`, watch/retry/cancel, cost capture, and Ctrl-C.

## Behaviour to keep

`--max-concurrent 1` behaves exactly as the sequential runner does (P1). The whole default suite stays green, including `tests/e2e/test_milestone_run.py`. Its `run_milestone_cli` fixture passes no `--max-concurrent`, so it now runs at the default of 4. If it fails, that points at a sibling's concurrency and should be reported, not worked around by pinning the fixture to 1.

## Tests

Placement rule: main spec section 14 and CLAUDE.md "tests mirror source". CLI option and validation behaviour goes in `tests/test_cli.py` through `CliRunner`, with `orchestrate.run_milestone` monkeypatched. Model defaults go in `tests/test_models.py`. Nothing here goes in `tests/e2e`, because this card has no real-harness wiring.

`tests/test_models.py` (unit, pure model):
- Update the default assertion (around line 299) from `== 1` to `== 4`. The `gt=0` refusal test stays as it is.

`tests/test_cli.py` (CLI tier, CliRunner with `run_milestone` patched through `_patch_run_milestone`):
- Omitting `--max-concurrent` passes `max_concurrent=4` to `run_milestone`. Update every test that compares the kwargs dict as a whole (for example `test_a_milestone_run_calls_run_milestone_once_with_the_run_options` and its sibling) to include `"max_concurrent": 4`.
- `--max-concurrent 2` passes `max_concurrent=2`, and `--max-concurrent 1` passes `1`.
- `--max-concurrent 0` and `--max-concurrent -1` exit 2 with empty stdout, and `run_milestone` is not called. Add these to the existing usage-error parametrization.
- `--card <id> --max-concurrent 2` exits 2 with empty stdout, and `run_card` is not called. This goes in the same parametrization.

`tests/test_cli.py` (pure function, next to the existing `dry_run_payload` tests):
- `dry_run_payload(..., max_concurrent=2)` over a level with 3 stories and a level with 1 story reports `max_concurrent == 2` and per-level `concurrent` values `[2, 1]`.
- The existing calls without `max_concurrent` still pass and report `max_concurrent == 4`.

`tests/test_cli.py` (CLI dry run, with `_dry_run` and `_forbid_writes` guards):
- `am run --milestone X --dry-run --max-concurrent 3` exits 0 and puts `max_concurrent: 3` in `data`. The forbidden-write guards do not fire, and no run directory is created.
- The same command without the flag reports `max_concurrent: 4`.
