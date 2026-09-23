# Card 19efcddc — Complete `run`'s CLI surface: `--verify` and a required `--branch-prefix`

Narrows R3 of `docs/superpowers/specs/2026-09-23-real-harness-design.md` ("The CLI carries what a run needs") to the Typer layer of `src/agent_manager/cli.py`. R3 amends the main design spec (`docs/superpowers/specs/2026-09-23-agent-manager-design.md`), which remains the source of truth for everything this card does not touch.

## Why

`run_card` (cli.py:633) and `resume_run` (cli.py:946) already take `commands: Sequence[str]` and already forward it twice — to `engine.run_subtask(..., commands=commands, ...)` and to `gate_context(commands, allow_no_verification)` (cli.py:710-713 and cli.py:1031-1034). Nothing on the command line can reach that parameter today, so every CLI-driven run sees an empty suite and §12's gate has nothing to check. Separately, `--branch-prefix` defaults to `"m1"` (cli.py:769-771, and `run_card`'s own `branch_prefix: str = "m1"` at cli.py:638), which was scaffolding for building milestone 1 and now silently mislabels branches for every other milestone.

## Scope

Three changes, all in `src/agent_manager/cli.py` plus `README.md`, plus tests in `tests/test_cli.py`.

1. **`--verify` on `am run` and `am resume`.** A repeatable `typer.Option` typed `list[str]` (empty by default, so a run with no suite stays legal and lands on the existing `--allow-no-verification` path). Each occurrence contributes one whole shell command string; the command is not split, parsed or deduplicated. The list is handed to `run_card(..., commands=...)` / `resume_run(..., commands=...)` **in command-line order** — ordering is observable because `gate_context` copies it straight into `suite_cmds` (cli.py:626) and the engine runs the commands in sequence.
2. **`--branch-prefix` becomes required on `am run`.** `typer.Option(..., "--branch-prefix", help=...)`. The `"m1"` default is deleted from the Typer signature and from `run_card`'s keyword default at cli.py:638, so no caller inherits the leftover; the existing `tests/test_cli.py` call sites that rely on the default (`cli.run_card(...)` at roughly lines 1086, 1104, 1127, 1143, 1220 and their neighbours) must pass `branch_prefix=` explicitly.
3. **`README.md` gains a Usage section** between the intro and `## Develop`, showing `am run --card <id> --branch-prefix <prefix> --verify "<cmd>" --verify "<cmd>"` and `am resume <RUN_ID> --verify "<cmd>"`, and noting that `--pretty` indents the JSON envelope.

Everything else about these commands is unchanged: the `brd`-style envelope (`ok_envelope` / `error_envelope`), `--pretty`, exit codes (`0`, `EXIT_ESCALATED`, `EXIT_ERROR`), and the payload dicts returned by `run_card` and `resume_run`. `resume` still takes no `--base-branch` and no `--branch-prefix`: both were decided when the run started and are recorded on the `SubtaskRun` (cli.py:1085-1088). `--verify` *is* offered on `resume` for the symmetric reason the docstring at cli.py:964-969 already gives — `models.RunConfig` carries neither the suite commands nor the opt-out, so a resume must be told the same things a fresh `run` was told. No new field is grown onto `models.RunConfig`.

## Observable behaviour

- `am run --card C --branch-prefix m2 --verify "uv run pytest" --verify "uv run ruff check"` → `run_card` receives `commands=["uv run pytest", "uv run ruff check"]`, that exact list reaches `engine.run_subtask`, and `gate_context` reports `suite_cmds` equal to it with `allow_no_verification` false, `caller_provided` false, `provided_verification` `None`.
- `am run` with no `--verify` → `commands` is empty; behaviour is byte-for-byte what it is today.
- `am run` without `--branch-prefix` → Typer's own missing-option usage error on stderr and **exit code 2**. This is deliberately *not* the `ok: false` envelope: `HANDLED` (cli.py:744) covers program-level refusals, while a missing required option never enters the `try` block. Exit 2 stays distinct from `EXIT_ERROR`.
- `am resume RUN_ID --verify "uv run pytest"` → `resume_run` receives `commands=["uv run pytest"]`; the envelope still carries `resumed_from` and `discarded_attempts` unchanged.
- The success envelope for `run` remains `{"ok": true, "data": {...}}` with data keys exactly `run_id`, `card_id`, `story_id`, `branch`, `base_branch`, `worktree`, `status`, `failed_phase`, `detail`, `skipped`, `warnings`.

## Error paths

- Missing `--branch-prefix`: Typer usage error, exit 2, nothing written — no run id minted, no run directory, because `run_card` is never called.
- `--verify` with an empty-string value: accepted verbatim and passed through. This card adds no validation of command text; whether an empty or nonsense command is a failure is the engine's and the gate's business, and inventing a CLI-level rejection here would be scope the findings do not support.
- Every pre-existing failure mode (`ParentlessCardError`, `board.BoardError`, `WorkflowLoadError`, `EngineError`, `ValueError`, `RepoDirError`, `UnknownRunError`, `NotResumableError`) keeps its current envelope and `EXIT_ERROR`.

## Tests

Per the test-placement rule — main spec section 14 (`docs/superpowers/specs/2026-09-23-agent-manager-design.md:477-492`): pure functions get unit tests; steps run against temp git repos and a temp `brd` board; adapters are asserted purely with injected launchers; the engine is driven with fake adapters; end-to-end is one slow opt-in test excluded from the default suite. Every test below is a **CLI tier** test in `tests/test_cli.py`, driven through `CliRunner` (`runner`, tests/test_cli.py:1157) with the `project` / `cards` fixtures (1012 / 1047) and a faked `cli.default_runner_factory` or a monkeypatched `cli.run_card`. **No test in this card belongs to the adapter, engine or end-to-end tier**: nothing here launches a real harness, and the card's text contains no e2e work (R4's fake-claude wiring and e2e tests are explicitly out of scope).

New:

1. `run` with two `--verify` values passes both to `run_card` as `commands` **in order** — monkeypatch `cli.run_card` (or `cli.engine.run_subtask`) and capture the keyword.
2. `run` with no `--verify` passes an empty `commands`, and `gate_context` still reports `suite_cmds == []` — guards the default against a `None`-vs-`[]` regression.
3. `run` without `--branch-prefix` exits 2 and prints a usage error, not a JSON envelope.
4. `run` with `--branch-prefix m2` puts that prefix in the payload's `branch` (proves the value is still threaded, now that the default is gone).
5. The `run` success envelope is `{"ok": true, "data": {...}}` with exactly the eleven data keys listed above — a shape-freeze test, asserted as a key-set equality.
6. `resume RUN_ID --verify ...` passes the commands to `resume_run`, placed beside the existing resume tests at tests/test_cli.py:2553-2590 and patching `cli.default_runner_factory` the way they do.

Updated (these break the moment `--branch-prefix` is required, and are part of this card):

7. The `_invoke` helper at tests/test_cli.py:1160-1171 adds `--branch-prefix` to its argv.
8. The standalone `runner.invoke(cli.app, ["run", ...])` at tests/test_cli.py:1444-1453 adds `--branch-prefix`.
9. Every direct `cli.run_card(...)` call that relied on the `"m1"` default now passes `branch_prefix=` explicitly.

## Verification

`uv run pytest` (full suite). No typecheck step, no lint step in this repo.

## Out of scope

Milestone orchestration, parallel stories, `integrate`, non-Claude harnesses, per-role `--harness`, the addendum's section 4 deferrals (`Card`/`CardNode.blocked_by`, `Store` thread-safety, `board.set_status` ancestor rollup), the sibling cards' brief composition (a1af1e04) and `prompt.txt` writing (c0a4bc75), and R4's fake-claude production-wiring and end-to-end tests.
