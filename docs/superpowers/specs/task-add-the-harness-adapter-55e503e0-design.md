# Subtask 55e503e0 — the harness adapter protocol and the direct launcher

Parent story b4a96f6d ("Roles, the harness adapter protocol and the Claude adapter"), scoped to design spec §8 and decisions D4, D6, D7. Builds on sibling 47bd4ee6 (role bundle loader, `models.py`, `paths.py`), which is done; sibling 9a2524a8 (the Claude adapter) is blocked on this card and owns `harness/claude.py`.

## Scope

Two modules and their tests:

- `src/agent_manager/harness/base.py` — the `HarnessAdapter` Protocol exactly as §8 lines 306-313 writes it, plus the `Outcome` and `Usage` types it and the launcher trade in.
- `src/agent_manager/harness/launcher.py` — the `direct` launcher, plus `bwrap` and `container` as named-but-unimplemented modes (D7, line 72).
- `src/agent_manager/harness/__init__.py` — package marker; re-exports nothing the two modules do not already own.
- One field added to `Dispatch` in `src/agent_manager/models.py`: `timeout`. §8 line 315 states `Dispatch` carries "the prompt text, the role bundle, the cwd, the result path, the model, and a timeout", and the sibling's `Dispatch` has every one of those but the timeout. The launcher needs it and D1's stateless dispatch means it must be recorded, not held in memory. This is the only edit outside `harness/`.

**Not in scope.** No concrete harness: `claude.py`, `codex.py`, `pi.py` are other cards. No `build_command`/`parse_usage` implementation of any kind — only the Protocol that shapes them. No engine, no phase loop, no result-file reading or Pydantic validation of results (that is the engine's step 5 in §6), no capability-matrix routing (the engine refuses at plan time; this module only exposes `capabilities` for it to read). No real confinement. No milestone orchestration, no census/levels/integrate.

## Observable behaviour

**`HarnessAdapter`** is a `typing.Protocol` (runtime-checkable is not required and is not added — structural checks at import time are not a goal) with `name: str`, `capabilities: frozenset[str]`, `build_command(self, d: Dispatch) -> list[str]`, `parse_usage(self, stdout: str) -> Usage | None`. It is a pure interface: no default implementations, no base class anyone inherits from. `build_command` returns an argv list, never a shell string — §5 line 252 is explicit that nothing in this program builds a shell command string. `parse_usage` returns `None` rather than raising when a harness's stdout has no usage in it, because stdout is a log and not a channel (D4) and a chatty or truncated log must not fail an otherwise successful attempt.

**`Usage`** is a Pydantic model (`_Model` conventions: `extra="forbid"`, frozen) with `tokens_in: int | None`, `tokens_out: int | None`, `cost: float | None`, all `ge=0`, all defaulting to `None`. Pydantic rather than a dataclass because it is parsed out of another program's stdout — a process boundary, per CLAUDE.md. Its fields line up one-for-one with `Attempt.tokens_in/tokens_out/cost` so the engine's journalling is a copy.

**`Outcome`** is what the launcher returns and what the engine classifies into §6's four journalled outcomes. Plain dataclass (frozen): it never crosses a process boundary, it is constructed in-process from a completed subprocess. Fields: `argv: list[str]`, `exit_code: int | None`, `timed_out: bool`, `duration: float`, `stdout_path: Path`. `exit_code` is `None` exactly when `timed_out` is true. The engine reads `timed_out or exit_code != 0` as `harness_error`; the third `harness_error` trigger in §6 line 278, a missing result file, is the engine's to detect because the launcher never looks at the result path. `Outcome` carries no result payload and no parsed usage — keeping the launcher blind to both is what lets the same launcher serve every adapter.

**The launcher.** `run_direct(argv: list[str], *, cwd: Path, timeout: float, stdout_path: Path) -> Outcome` starts the process with `cwd` pinned (D7: cwd is the subtask worktree), stdout **and** stderr both written to `stdout_path` opened for writing, stdin closed or attached to devnull so a harness that prompts cannot hang forever, and the timeout enforced. On timeout the process is killed, the partial log is left on disk, and `Outcome(timed_out=True, exit_code=None)` is returned — a timeout is a value, not an exception, because §6 wants it journalled as an attempt rather than propagated. `duration` is wall-clock seconds measured around the call. The launcher creates `stdout_path`'s parent if it is missing but does not invent the path: the engine passes `attempt_dir(run_id, card, phase, attempt) / "stdout.log"` from `paths.py`, which is rooted under `data_dir()` and therefore outside every worktree (§6 line 265).

`LauncherFn` is the injected type — a `Protocol` (or `Callable` alias) with `run_direct`'s exact signature. Nothing in the engine or an adapter imports `run_direct` directly; a launcher is passed in. That injection is the whole point of the seam (§14 line 485).

`get_launcher(kind: Launcher) -> LauncherFn` maps the existing `Launcher = Literal["direct", "bwrap", "container"]` from `models.py` — no new enum is invented — onto an implementation. `"direct"` returns `run_direct`. `"bwrap"` and `"container"` are *named* and raise.

## Error paths

- `get_launcher("bwrap")` / `get_launcher("container")` raise `UnsupportedLauncherError`, a module-level error type carrying the requested `kind` and a message that says the mode is a seam and only `direct` is implemented (D7). They are named in the mapping so the failure is a deliberate refusal at config time, not an unhandled `KeyError` deep in a run.
- `get_launcher` with any other value raises the same error type with a different reason. The `Literal` catches this at type-check time; the runtime guard exists because `RunConfig.launcher` can arrive from a journal line.
- A non-existent or non-directory `cwd` raises rather than returning an `Outcome`: it is a programming error in the engine (the worktree step runs first), not a harness failure, and swallowing it as `harness_error` would burn `max_attempts` re-dispatching into a directory that will never exist.
- A non-positive `timeout` raises `ValueError`. `Dispatch.timeout` is validated `gt=0` at the model, so this only fires when a caller passes one directly.
- A non-zero exit is **not** an error path: it is an `Outcome` with that exit code. Same for a timeout. The launcher raises only for things no retry could fix.
- Adding `timeout` to `Dispatch` keeps `extra="forbid"` intact and gives the field a default so journal lines written before this card still load (the models module's stated contract is that a stale line must fail loudly *or* load — a silently dropped key is what `extra="forbid"` prevents, and a defaulted new field is the compatible direction).

## Test list

Tier per spec §14 lines 477-490 — the governing rule for this card is line 484: "Adapters — `build_command` is pure and asserted per harness; the launcher is injected, so no harness is executed in unit tests." There is no separate testing-standards doc.

`tests/harness/test_base.py` — **unit tier** (pure, in-process, no subprocess at all):

1. A minimal stub adapter defined in the test satisfies `HarnessAdapter` structurally — asserts the Protocol's member set and signatures are what §8 lines 306-313 print, so a later adapter card cannot drift the interface unnoticed.
2. `Usage` accepts a full payload and round-trips it; `Usage()` with everything absent is valid (a harness that reports nothing).
3. `Usage` rejects negative tokens, negative cost, and an unknown key (`extra="forbid"`).
4. `Outcome` is frozen and its fields are readable; an `Outcome` with `timed_out=True` carries `exit_code is None`.

`tests/harness/test_launcher.py` — **unit tier**. §14's prohibition is on executing *a harness*; these tests execute a trivial non-harness child (`sys.executable -c ...`) to exercise the launcher's own contract, which nothing above it can cover once it is injected. Actually running a harness binary stays in the single opt-in, marked, excluded-by-default end-to-end test (§14 lines 489-490), which this card does not add.

5. A command that exits 0 and prints returns `exit_code == 0`, `timed_out is False`, and `stdout_path` contains the printed text.
6. Stderr from the child lands in the same `stdout_path`.
7. A command that exits non-zero returns that exit code and does not raise.
8. A command that sleeps past a short timeout returns `timed_out is True`, `exit_code is None`, and leaves the partial log on disk.
9. `cwd` is honoured — the child reports its own working directory and it matches the tmp dir passed in, not the test process's.
10. `duration` is positive and finite.
11. A missing `cwd` raises; a `timeout <= 0` raises `ValueError`.
12. `get_launcher("direct")` returns a callable satisfying `LauncherFn`.
13. `get_launcher("bwrap")` and `get_launcher("container")` each raise `UnsupportedLauncherError` naming the mode; an unknown string raises it too.

`tests/test_models.py` (extending the sibling's file) — **unit tier**:

14. `Dispatch` accepts a `timeout`, rejects a non-positive one, and still loads a payload that omits it.

No fake adapter or fake launcher fixture ships in this card's tests beyond the stub in test 1 — the fake adapter returning canned result files belongs to the engine card (§14 line 486).
