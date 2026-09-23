# Subtask be23d3c0 — Add `logs`

Parent: 1a46ab5a "The CLI: run, status, logs, resume" (milestone 352e955b). Blocker: 3c39ae43 "Add `status` and `runs`" — this card builds on branch `m1/task-add-status-and-runs-3c39ae43`, not on master (master holds only the package scaffold).

Source of truth: `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §10 (CLI surface, lines 388-411), §9 (state tree and attempt artifacts, lines 346-387), §14 (testing tiers, lines 477-492). This spec narrows that agreed design to one command; it invents nothing beyond it.

## Scope

One new command in `src/agent_manager/cli.py` and its tests in `tests/test_cli.py`:

```
agent-manager logs <run-id> <card> [--phase <name>] [--attempt <n>] [--repo-dir .] [--pretty]
```

`logs` locates one attempt directory of one card in one run and reports that attempt's prompt, result and captured stdout. With no flags it reports the latest attempt of the latest phase for that card — the common case §10 names.

Out of scope, owned by siblings and not to be touched: `run` / `run_card` / `RunnerFactory` (cbe34d00), `status` / `runs` / `status_for` / `status_payload` / `runs_for` / `status_rows` (3c39ae43), `resume` (5524ae72). Also out of scope: `watch`, `retry`, `cancel`, `--phase` globbing, tailing, truncation/paging of artifact text, and any filtering across runs.

`logs` is strictly read-only. It calls no `Store.record_*`, opens no `Journal`, and creates no directory. Two consequences are binding: it reads the projection through the free functions `store_module.open_db` / `store_module.load_run` (never `Store.open`, which constructs a `Journal` and therefore a run directory), and it never calls `paths.attempt_dir` or `paths.run_dir`, both of which `mkdir(parents=True, exist_ok=True)` as a side effect. Artifact locations come from the `Attempt` rows the projection already holds (`prompt_path`, `result_path`, `stdout_path`, recorded by `dispatch.AgentRunner` from `paths.attempt_dir(run_id, card, phase, n)`).

This keeps the module's own constraint intact: "no step logic, no gate logic, no branch strings built by hand, and no run state written anywhere but through `Store`".

## Shape

A pure selection layer over the `models.Run` tree, plus a thin command, mirroring how `status_rows` / `status_payload` sit under `status_for` / `status`:

- a pure lookup of a subtask by card id, walking `run.stories[*].subtasks[*]` and matching `card_id` (no such helper exists yet). `SubtaskRun` carries no back-reference to its story, so this lookup returns the owning `StoryRun` alongside the matched `SubtaskRun` (e.g. a `(story, subtask)` pair) — the payload's `story_id` has nowhere else to come from, and a second walk to recover it would be a second source of truth for the same match;
- a pure selection of the target phase and attempt from that subtask, honouring the `--phase` / `--attempt` defaults below;
- a pure payload builder that reads the three artifact files and shapes the envelope data;
- `logs_for(run_id, card, *, repo_dir, phase=None, attempt=None)` composing resolve-repo-dir → `open_db` → `load_run` → selection → payload, closing the connection on every path including refusals;
- `@app.command("logs")` wrapping `logs_for` in the existing `try / except HANDLED` / `render(...)` pattern.

`run_id` is a required positional here (unlike `status`): §10 writes `logs <run-id> <card>`, and there is no "latest run" default to argue about.

## Defaults and selection

- `--phase` omitted: the **last phase in `subtask.phases` position order that has at least one attempt**. Position order is what `load_run` preserves, and skipping attempt-less phases is what makes the no-flag case useful — a trailing `pending` phase has no artifacts to print.
- `--phase <name>` given: exactly that phase by name; if the subtask has no phase of that name, refuse.
- `--attempt` omitted: the attempt with the highest `n` in the selected phase (`load_run` returns attempts ordered by `n`, so this is the last element).
- `--attempt <n>` given: exactly that `n`; if the selected phase has no attempt with that `n`, refuse.

## Observable behaviour

Success: exit `0`, one line of JSON (indented under `--pretty`), `sort_keys=True`, `default=str`, through the existing `ok_envelope` / `render`. The payload identifies what was selected and carries the three artifacts:

- `run_id`, `story_id`, `card`, `phase`, `attempt` (the integer `n`), `status` (the attempt's), `exit_code`;
- `artifacts`: three entries keyed `prompt`, `result`, `stdout`, each `{"path": <recorded path or null>, "present": <bool>, "text": <file contents or null>}`.

`text` is the raw file content, not parsed: `result.json` is read as text like the other two. `logs` exists to show an operator what the harness produced, including the malformed output that made a phase fail, so it must never itself fail on an unparseable or half-written artifact. Files are read with `encoding="utf-8", errors="replace"` for the same reason.

A recorded path that is `null` and a recorded path whose file is absent are both `present: false` with `text: null` — facts, not refusals. `Attempt.prompt_path` / `result_path` / `stdout_path` are `Path | None` in `models.py`, and this payload builder must not assume a populated row: it is defensive against any attempt whose recorded path is `null`, whatever produced it, not just today's `dispatch.AgentRunner` (which currently always sets all three before the first `record_attempt` call). Deterministic phases never reach this case at all — they record no `Attempt` rows, so a phase with only deterministic runs has nothing for `--attempt` or the no-flag default to select, and is treated the same as any other phase with zero attempts (see Defaults and selection). An attempt that exists is always reportable.

`Path` values stay `Path` objects in the payload and are stringified once by `render`'s `default=str`, exactly as `status_payload` does.

## Error paths

All refusals are `CliError` subclasses, so they ride the existing `HANDLED` tuple — which already lists the `CliError` base — into an `ok: false` envelope at `EXIT_ERROR = 3`. No change to `HANDLED` is required. Exit `2` remains Typer's (a missing positional, a non-integer `--attempt`); `EXIT_ESCALATED = 1` is `run`-specific and unreachable here.

- Unknown run id in this project's projection: reuse `UnknownRunError`, message naming the run id, the root, and `agent-manager runs`.
- Card not present in that run's tree: a new `CliError` subclass, message naming the card and the run (and pointing at `agent-manager status <run-id>`).
- Named `--phase` absent from that card: a new `CliError` subclass, message naming the phase and listing the phases the card does have.
- Named `--attempt` absent from that phase: a new `CliError` subclass, message naming `n` and the attempts that exist.
- Card whose phases have no attempts at all (no `--phase` given, nothing to default to): the same "no such attempt" refusal, worded as "no attempt has been recorded for this card yet".
- `--repo-dir` that is not a directory: existing `RepoDirError` via `resolve_repo_dir`, unchanged.

Each subclass gets a docstring saying why it is its own type, in the register of `UnknownRunError` / `ParentlessCardError`.

## Tests

Tiers per §14 lines 477-492 and the tiering `tests/test_cli.py` already states in its own docstring. All new tests go in `tests/test_cli.py` beside the `run` / `status` / `runs` tests — no new file, no new tier.

**Pure-function unit tier** (hand-built `models.Run` trees, no database, no clock, `tmp_path` only where a file must exist on disk):

1. the card lookup finds a subtask under the second story, not just the first;
2. the card lookup returns nothing for a card id absent from the tree;
3. with no flags, selection picks the last phase that has attempts and its highest-`n` attempt;
4. with no flags, a trailing attempt-less `pending` phase is skipped rather than selected;
5. an explicit `--phase` selects that phase even when a later phase has attempts;
6. an explicit `--attempt` selects that `n`, not the highest;
7. an unknown phase name raises the phase refusal and the message names the phases that exist;
8. an unknown attempt number raises the attempt refusal;
9. a card whose phases hold no attempts at all raises the attempt refusal;
10. the payload builder reads prompt, result and stdout from disk and reports `present: true` with exact text;
11. the payload builder reports `present: false, text: null` for a recorded path whose file is missing, and for a `null` recorded path;
12. the payload builder returns the raw text of a `result.json` that is not valid JSON, without raising;
13. the payload survives `render` — the envelope round-trips through `json.loads` with paths as strings and embedded newlines intact.

**Engine tier on Steps-tier fixtures** (the existing `projection` fixture: a project root plus `XDG_DATA_HOME` under `tmp_path`, rows written through `store_module.Store`, artifact files written into the attempt directories by the test; no git, no brd, no harness process ever launched):

14. `logs <run-id> <card>` with no flags prints `ok: true`, exit `0`, and the latest attempt of the latest phase;
15. `--phase` and `--attempt` together select an earlier attempt and the payload says so;
16. `--pretty` indents the same envelope (same parsed object, contains newlines);
17. an unknown run id is an `ok: false` envelope at exit `3`;
18. a card absent from that run is an `ok: false` envelope at exit `3`, naming the card;
19. an unknown `--phase` and an unknown `--attempt` are each an `ok: false` envelope at exit `3`;
20. a `--repo-dir` that is not a directory is an `ok: false` envelope at exit `3`;
21. **`logs` writes nothing**: snapshot the tree under `XDG_DATA_HOME` and the `attempts` table before and after a successful invocation and after a refusal, and assert both are unchanged — in particular that a `logs` call for an unknown run leaves no `runs/<run-id>` directory behind;
22. an attempt whose `stdout.log` was never written still reports `ok: true` with `present: false`.

No End-to-end-tier work: `logs` reads a projection and three files, and the opt-in real-harness test is the milestone's, not this card's.

## Verification

```
uv run pytest
```

No separate lint or typecheck command exists in this repo.
