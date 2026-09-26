# Verify also runs Explore's typecheck and lint (cf8b3888)

Subtask of story be007353 "Close the task.js gaps". Narrows pygents-engine design G9 item 4 (`docs/superpowers/specs/2026-09-25-pygents-engine-design.md:361-363`, also listed under §9 Tests at line 398) and plan Task 2.4 (`docs/superpowers/plans/2026-09-25-pygents-engine.md:570-610`) to one function.

## Scope

Owns only `src/agent_manager/steps/verify.py` (`run_suite`) and `tests/steps/test_verify.py`. Does not touch `steps/reducers.py`, role bundles, `workflow/builtin/task.yaml` / `integrate.yaml`, `workflow/registry.py`, or anything under `runtime/`. `verify.py` must not import pygents.

No workflow edit: `verify`'s phase in `task.yaml` declares no `inputs:` at all — deterministic phases don't need to, since `engine._run_deterministic` (`engine.py:487-499`) calls `bind_arguments` with the whole running context, not a phase's declared `inputs:`. `bind_arguments` (`engine.py:163-198`) binds parameters by name from that context and falls back to a parameter's default when the name is absent; `_bind_result` (`engine.py:460-470`) already writes every earlier phase's result into the context under its own phase name, so the context handed to `verify` already holds a key literally named `explore`. Declaring an `explore` parameter makes the engine hand `run_suite` the Explore phase's dumped result automatically (same pattern as `plan_hash_gate_adapter`, `steps/reducers.py:447-469`; registered unchanged at `workflow/registry.py:243`).

## Interface

`run_suite(commands, worktree, explore: object = None, *, runner: CommandRunner = run_command) -> dict[str, object]`

`explore` is positional-or-keyword and goes before the `*`. `runner` stays keyword-only.

## Observable behaviour

- The run order is: every entry of `commands`, then Explore's `verification.typecheck` if it is a non-blank string, then each entry of `verification.lint` in list order. Design wording: "Verify runs `--verify` commands, then Explore's `typecheck` (when non-empty) and each `lint` command, in that order; any non-zero exit fails `verification_passed_gate` as today."
- `explore` is read tolerantly with a local `_field`-style accessor in the style of `reducers._field` / `dag._field`. If `explore` is not a mapping, has no `verification` mapping, or is `None`, no extra commands run. `typecheck` and `lint` have no alias (`results.Verification`, `results.py:47-51`: only `full_suite` carries `serialization_alias="fullSuite"`), so the snake_case model dump the engine actually hands `run_suite` (`Verdict.result = validated.model_dump(mode="json")`, `dispatch.py:255`, no `by_alias=True`) and the camelCase hand-written fixture in this file's tests both use the same keys for `typecheck`/`lint`. `fullSuite`/`full_suite` inside `explore` is ignored, because `commands` is still the source of the suite.
- Extra commands go through the existing pipeline unchanged. `_argv_for` / `shlex.split` is used and there is never a shell (`shell=False`, module docstring lines 1-16). Blank entries are skipped. Malformed entries raise `ValueError` up front, before any process starts — the suite's commands, the typecheck command and every lint command are all planned into argvs before any of them runs, the same as a bad `commands` entry today. Each extra command gets its own `verified` entry (`command`/`ok`/`tail`) in the same form as the others.
- The failure path is the same as for a red `--verify` command (`verify.py:258-271`). A non-zero extra command appends an `ok: False` entry with the diagnostic tail and sets `detail` to `"verification failed: <cmd> — <diagnostic>"`. It also leaves `passed` False and stops, so no later command runs. A launch failure (`FileNotFoundError`/`PermissionError`/`NotADirectoryError`) raises `VerifyError` as it does today.
- Backward compatibility: when no `explore` is given, or when it has an empty typecheck and empty lint, the behaviour and result shape are identical to today. That includes the exact `{"passed": True, "verified": [], "detail": ""}` for an empty `commands`. The result keys (`passed`/`verified`/`detail`) do not change. No G10-protected shape (SubtaskSummary, journal lines, phase/attempt rows, escalation payloads) is altered.

## Tests

All tests go in `tests/steps/test_verify.py` and belong to the **Steps** tier (agent-manager design §14, `2026-09-23-agent-manager-design.md:497-513`: "against temporary git repositories and a temporary `brd` board; no network"; file docstring lines 1-14). Use real `_py(...)` subprocesses in `tmp_path` where a real process can produce the outcome. Use the injected `runner` (e.g. `_recorder()`, which receives `argv: list[str], cwd: str`) only where a test has to observe or force the ordering. The plan's fixtures compare command strings, so adapt them to argv lists.

1. **Typecheck and lint run after the suite, in order** (Steps, recorder runner): `explore = {"verification": {"fullSuite": [...], "typecheck": "uv run mypy", "lint": ["uv run ruff check"]}}`. The recorded argvs are the suite, then `["uv","run","mypy"]`, then `["uv","run","ruff","check"]`, and `passed` is True.
2. **No explore means only the suite** (Steps, recorder runner): calling without `explore` runs only `commands`.
3. **An empty typecheck is skipped** (Steps, recorder runner): `{"verification": {"typecheck": "", "lint": []}}` runs only `commands`.
4. **A failing extra command fails the suite like a red `--verify` command** (Steps, real `_py` subprocesses): the suite is green, typecheck is `_py("raise SystemExit(3)")` (or it writes to stderr and exits non-zero), and lint holds a command that would leave a marker file. Check that `passed` is False, the last `verified` entry is `ok: False` with the diagnostic, and `detail` starts with `"verification failed:"`. The lint marker must be absent, which shows the short-circuit.
5. **Malformed explore input is tolerated or rejected consistently** (Steps, recorder runner): if `explore` is a non-mapping or has no `verification`, only the suite runs. A lint entry of the wrong type (e.g. `7`) raises `ValueError` before any command runs, and the recorder sees no calls.

Existing tests (including the pass path at 122-156 and the red / short-circuit path at 184-223) must stay green unchanged. Verification: `uv run pytest` (whole default suite, including `tests/e2e`, on both engines while `--engine` exists). There is no typecheck or lint command for this repo.
