<!-- task-pipeline: validated -->
# Spec (verbatim)

<!-- Prepended verbatim from docs/superpowers/specs/task-add-logs-be23d3c0-design.md -->

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

---

# `logs` Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a strictly read-only `agent-manager logs <run-id> <card> [--phase] [--attempt] [--repo-dir] [--pretty]` command that prints one attempt's prompt, result and captured stdout.

**Architecture:** Four new pure functions in `src/agent_manager/cli.py` (`find_subtask`, `select_attempt`, `read_artifact`, `logs_payload`) plus a thin `logs_for` composition and an `@app.command("logs")` wrapper, mirroring exactly how `status_rows` / `status_payload` sit under `status_for` / `status`. Artifact locations come from the `Attempt` rows the projection already holds; the command never calls `paths.attempt_dir`, `paths.run_dir` or `Store.open`, all of which create directories.

**Tech Stack:** Python 3, Typer, Pydantic (`agent_manager.models`), SQLite projection via `agent_manager.store`, pytest + `typer.testing.CliRunner`.

**Spec:** `docs/superpowers/specs/task-add-logs-be23d3c0-design.md` (prepended verbatim above); source of truth `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §9, §10, §14.

## Global Constraints

- Branch is `m1/task-add-logs-be23d3c0`, cut from `origin/m1/task-add-status-and-runs-3c39ae43`. No other subtask's code exists on it — do not reference `resume` or anything it owns.
- Do not touch `run`, `run_card`, `RunnerFactory`, `status`, `status_for`, `status_payload`, `status_rows`, `runs`, `runs_for`. Only additions to `src/agent_manager/cli.py` and `tests/test_cli.py`.
- `logs` is strictly read-only: no `Store.open`, no `Store.record_*`, no `Journal`, no `paths.run_dir`, no `paths.attempt_dir` in `src/agent_manager/cli.py`. The projection is read through the free functions `store_module.open_db` and `store_module.load_run`, and the connection is closed on every path including refusals.
- All refusals are `CliError` subclasses so they ride the existing `HANDLED` tuple to `EXIT_ERROR = 3`. `HANDLED` itself is not edited — `CliError` is already its first entry.
- Payload keeps `Path` objects; `render`'s `default=str` stringifies them once at the edge, as `status_payload` does.
- Artifact files are read with `encoding="utf-8", errors="replace"`, and `result.json` is returned as raw text, never parsed.
- All new tests go in `tests/test_cli.py` — no new file, no new tier.
- Verification: `uv run pytest`. There is no lint or typecheck command in this repo.

## Review Focus

Five input classes the spec implies that no spec-listed test exercises. Each has a test added to the owning task below.

1. **Two subtasks with the same `card_id` in one run's tree** — `find_subtask` must deterministically return the first match in tree order rather than the last or an arbitrary one (Task 1, Step 5).
2. **`--attempt 0` or a negative `--attempt`** — Typer accepts any int; `models.Attempt.n` is `gt=0`, so no such attempt can exist and the command must refuse with `UnknownAttemptError` at exit 3, not crash or silently fall back to the default (Task 2, Step 10).
3. **A recorded artifact path that names a directory, not a file** — `Path.exists()` would say `True` and `read_text` would raise `IsADirectoryError`, turning a read-only report into a traceback. `read_artifact` must test `is_file()` (Task 3, Step 5).
4. **An artifact file holding bytes that are not valid UTF-8** — a harness that wrote raw terminal output is exactly what `logs` is for; `errors="replace"` must be proven, not assumed (Task 3, Step 7).
5. **An artifact file that exists but is empty** — `""` is falsy and must still report `present: true, text: ""`, distinct from the missing-file `present: false, text: null` (Task 3, Step 5).

---

### Task 1: Card lookup across the run tree

**Files:**
- Modify: `src/agent_manager/cli.py` (add `UnknownCardError` after `ParentlessCardError` at line 68-75; add `find_subtask` after `status_payload`, which ends at line 206)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `models.Run`, `models.StoryRun`, `models.SubtaskRun` from `agent_manager.models`; the existing `_pure_run` / `_pure_dispatch` test helpers in `tests/test_cli.py` (lines 98-120).
- Produces: `find_subtask(run: models.Run, card: str) -> tuple[models.StoryRun, models.SubtaskRun] | None` and `class UnknownCardError(CliError)`, both used by Task 4's `logs_for`.

- [ ] **Step 1: Write the failing tests for the lookup**

Append to `tests/test_cli.py`, after `test_the_status_header_is_the_runs_identity_and_not_its_config` (which ends at line 301):

```python
def _pure_subtask(card_id: str, phases: list[models.PhaseRun]) -> models.SubtaskRun:
    """A subtask carrying hand-built phases: the `logs` selection tests touch no disk."""
    return models.SubtaskRun(
        card_id=card_id,
        branch=f"m1/{card_id}",
        base_branch="main",
        status="started",
        phases=phases,
    )


def _pure_story(card_id: str, subtasks: list[models.SubtaskRun]) -> models.StoryRun:
    return models.StoryRun(
        card_id=card_id, title=card_id, level=0, status="started", subtasks=subtasks
    )


def test_find_subtask_looks_past_the_first_story():
    """`SubtaskRun` has no back-reference to its story, so the lookup returns the
    pair: `story_id` in the payload has nowhere else to come from."""
    wanted = _pure_subtask("card-2", [])
    run = _pure_run(
        [
            _pure_story("story-1", [_pure_subtask("card-1", [])]),
            _pure_story("story-2", [wanted]),
        ]
    )

    found = cli.find_subtask(run, "card-2")

    assert found is not None
    story, subtask = found
    assert story.card_id == "story-2"
    assert subtask is wanted


def test_find_subtask_returns_none_for_a_card_that_is_not_in_the_tree():
    run = _pure_run([_pure_story("story-1", [_pure_subtask("card-1", [])])])

    assert cli.find_subtask(run, "card-9") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k find_subtask -v`
Expected: FAIL with `AttributeError: module 'agent_manager.cli' has no attribute 'find_subtask'`

- [ ] **Step 3: Add the error type and the lookup**

In `src/agent_manager/cli.py`, add after the `ParentlessCardError` class (ends at line 75):

```python
class UnknownCardError(CliError):
    """The run's tree holds no subtask with that card id.

    Its own type rather than `UnknownRunError`'s: the run was found and the card
    was not, so the fix an operator needs is `agent-manager status <run-id>` --
    a different instruction from the one a missing run gets -- and a script can
    tell the two apart by the `type` field of the envelope.
    """
```

And add after `status_payload` (ends at line 206), before the `app = typer.Typer(` line:

```python
def find_subtask(
    run: models.Run, card: str
) -> tuple[models.StoryRun, models.SubtaskRun] | None:
    """The `(story, subtask)` pair for one card id, or `None`.

    Pure over the tree `load_run` assembled, like `status_rows`. The owning story
    comes back with the match because `SubtaskRun` carries no back-reference to
    it and the `logs` payload's `story_id` has nowhere else to come from; a
    second walk to recover it would be a second source of truth for one match.

    The first match in §9 tree order wins. A card id appears once per run in
    everything this program writes, so a duplicate is a corrupt projection, and
    answering deterministically beats answering arbitrarily.
    """
    for story in run.stories:
        for subtask in story.subtasks:
            if subtask.card_id == card:
                return story, subtask
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k find_subtask -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Write the failing test for a duplicated card id (Review Focus 1)**

Append to `tests/test_cli.py` after `test_find_subtask_returns_none_for_a_card_that_is_not_in_the_tree`:

```python
def test_find_subtask_takes_the_first_match_when_a_card_id_is_duplicated():
    """A card id appears once per run in everything this program writes, so a
    duplicate means the projection is corrupt -- and `logs` must still answer the
    same way every time rather than picking arbitrarily."""
    first = _pure_subtask("card-1", [])
    second = _pure_subtask("card-1", [])
    run = _pure_run([_pure_story("story-1", [first]), _pure_story("story-2", [second])])

    found = cli.find_subtask(run, "card-1")

    assert found is not None
    story, subtask = found
    assert story.card_id == "story-1"
    assert subtask is first
```

- [ ] **Step 6: Run it to verify it passes**

Run: `uv run pytest tests/test_cli.py -k find_subtask -v`
Expected: PASS (3 tests) — the `return` inside the loop already pins first-match order; this test locks it against a later refactor.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): look up a subtask by card id across a run's stories"
```

---

### Task 2: Phase and attempt selection

**Files:**
- Modify: `src/agent_manager/cli.py` (add `UnknownPhaseError` and `UnknownAttemptError` after `UnknownCardError` from Task 1; add `select_attempt` after `find_subtask`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `models.SubtaskRun`, `models.PhaseRun`, `models.Attempt`; the `_pure_subtask` / `_pure_story` / `_pure_dispatch` helpers added in Task 1 and already present at `tests/test_cli.py:98`.
- Produces: `select_attempt(subtask: models.SubtaskRun, *, phase: str | None = None, attempt: int | None = None) -> tuple[models.PhaseRun, models.Attempt]`, raising `UnknownPhaseError` / `UnknownAttemptError`. Task 4's `logs_for` calls it; Task 3's `logs_payload` takes its two return values.

- [ ] **Step 1: Write the failing tests for the defaults**

Append to `tests/test_cli.py` after `test_find_subtask_takes_the_first_match_when_a_card_id_is_duplicated`:

```python
def _pure_attempt(n: int, status: str = "ok") -> models.Attempt:
    return models.Attempt(n=n, dispatch=_pure_dispatch(), status=status)


def test_select_attempt_defaults_to_the_last_phase_with_attempts_and_its_highest_n():
    subtask = _pure_subtask(
        "card-1",
        [
            models.PhaseRun(
                name="explore", kind="agent", status="done", attempts=[_pure_attempt(1)]
            ),
            models.PhaseRun(
                name="implement",
                kind="agent",
                status="done",
                attempts=[_pure_attempt(1, "gate_failed"), _pure_attempt(2)],
            ),
        ],
    )

    phase, attempt = cli.select_attempt(subtask)

    assert phase.name == "implement"
    assert attempt.n == 2


def test_select_attempt_skips_a_trailing_phase_that_has_no_attempts():
    """A trailing `pending` phase has no artifacts to print, so defaulting to it
    would make the no-flag common case §10 names useless."""
    subtask = _pure_subtask(
        "card-1",
        [
            models.PhaseRun(
                name="implement", kind="agent", status="done", attempts=[_pure_attempt(1)]
            ),
            models.PhaseRun(name="verify", kind="deterministic", status="pending"),
        ],
    )

    phase, attempt = cli.select_attempt(subtask)

    assert phase.name == "implement"
    assert attempt.n == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k select_attempt -v`
Expected: FAIL with `AttributeError: module 'agent_manager.cli' has no attribute 'select_attempt'`

- [ ] **Step 3: Add the error types and the selection**

In `src/agent_manager/cli.py`, add after the `UnknownCardError` class added in Task 1:

```python
class UnknownPhaseError(CliError):
    """The card has no phase of that name in this run.

    Separate from `UnknownAttemptError` because the two refusals point at
    different lists: this one can name the phases that exist, and conflating them
    would cost an operator that list at exactly the moment they mistyped a name.
    """


class UnknownAttemptError(CliError):
    """The selected phase has no such attempt -- or has none at all.

    One type for both, because they are one fact: the command was asked for an
    attempt and there is none to report. The message is what tells apart "you
    asked for attempt 7 of three" from "nothing has run for this card yet", the
    same way `UnknownRunError` carries two readings of one refusal.
    """
```

And add after `find_subtask`:

```python
def select_attempt(
    subtask: models.SubtaskRun,
    *,
    phase: str | None = None,
    attempt: int | None = None,
) -> tuple[models.PhaseRun, models.Attempt]:
    """The `(phase, attempt)` §10's `logs` should report, or a refusal.

    Pure over the tree, no filesystem: which attempt is meant is a question about
    recorded state, and answering it before any file is opened is what keeps the
    artifact reading a single straight-line step.

    With no `phase`, the last phase in position order that actually has attempts
    wins -- `load_run` preserves position, and a trailing `pending` or
    deterministic phase has no artifacts to print. With no `attempt`, the highest
    `n` wins; `max` rather than `attempts[-1]` because the ordering is
    `load_run`'s promise, not the model's, and this function is also called with
    trees built by hand.
    """
    if phase is None:
        chosen = next((item for item in reversed(subtask.phases) if item.attempts), None)
        if chosen is None:
            raise UnknownAttemptError(
                f"no attempt has been recorded for card {subtask.card_id!r} yet"
                " (`agent-manager status` shows which phases exist)"
            )
    else:
        chosen = next((item for item in subtask.phases if item.name == phase), None)
        if chosen is None:
            names = ", ".join(item.name for item in subtask.phases) or "none"
            raise UnknownPhaseError(
                f"card {subtask.card_id!r} has no phase {phase!r};"
                f" recorded phases: {names}"
            )

    if attempt is None:
        if not chosen.attempts:
            raise UnknownAttemptError(
                f"phase {chosen.name!r} of card {subtask.card_id!r} has no recorded"
                " attempt yet"
            )
        return chosen, max(chosen.attempts, key=lambda item: item.n)

    for candidate in chosen.attempts:
        if candidate.n == attempt:
            return chosen, candidate
    numbers = ", ".join(str(item.n) for item in chosen.attempts) or "none"
    raise UnknownAttemptError(
        f"phase {chosen.name!r} of card {subtask.card_id!r} has no attempt {attempt};"
        f" recorded attempts: {numbers}"
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k select_attempt -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Write the failing tests for the explicit flags**

Append to `tests/test_cli.py` after `test_select_attempt_skips_a_trailing_phase_that_has_no_attempts`:

```python
def _two_phase_subtask() -> models.SubtaskRun:
    return _pure_subtask(
        "card-1",
        [
            models.PhaseRun(
                name="explore",
                kind="agent",
                status="done",
                attempts=[_pure_attempt(1, "gate_failed"), _pure_attempt(2)],
            ),
            models.PhaseRun(
                name="implement", kind="agent", status="done", attempts=[_pure_attempt(1)]
            ),
        ],
    )


def test_an_explicit_phase_wins_over_a_later_phase_that_also_has_attempts():
    phase, attempt = cli.select_attempt(_two_phase_subtask(), phase="explore")

    assert phase.name == "explore"
    assert attempt.n == 2


def test_an_explicit_attempt_selects_that_n_and_not_the_highest():
    phase, attempt = cli.select_attempt(_two_phase_subtask(), phase="explore", attempt=1)

    assert phase.name == "explore"
    assert attempt.n == 1
    assert attempt.status == "gate_failed"
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k select_attempt -v`
Expected: PASS (4 tests) — Step 3's implementation already covers the explicit flags; these lock the behaviour the defaults must not override.

- [ ] **Step 7: Write the failing tests for the refusals**

Append to `tests/test_cli.py` after `test_an_explicit_attempt_selects_that_n_and_not_the_highest`:

```python
def test_an_unknown_phase_name_is_refused_and_names_the_phases_that_exist():
    with pytest.raises(cli.UnknownPhaseError) as caught:
        cli.select_attempt(_two_phase_subtask(), phase="reveiw")

    message = str(caught.value)
    assert "reveiw" in message
    assert "explore" in message
    assert "implement" in message


def test_an_unknown_attempt_number_is_refused_and_names_the_attempts_that_exist():
    with pytest.raises(cli.UnknownAttemptError) as caught:
        cli.select_attempt(_two_phase_subtask(), phase="implement", attempt=7)

    message = str(caught.value)
    assert "7" in message
    assert "implement" in message


def test_a_card_with_no_attempts_at_all_is_the_attempt_refusal():
    """No `--phase` was given and there is nothing to default to. That is the same
    fact as a missing attempt number, worded for the operator who has not run
    anything yet."""
    subtask = _pure_subtask(
        "card-1",
        [
            models.PhaseRun(name="explore", kind="agent", status="pending"),
            models.PhaseRun(name="verify", kind="deterministic", status="pending"),
        ],
    )

    with pytest.raises(cli.UnknownAttemptError) as caught:
        cli.select_attempt(subtask)

    assert "no attempt has been recorded" in str(caught.value)
    assert "card-1" in str(caught.value)
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k select_attempt -v`
Expected: PASS (7 tests)

- [ ] **Step 9: Run them once more against a deliberately broken selection to prove they bite**

Temporarily change the `if attempt is None:` branch in `select_attempt` to `return chosen, chosen.attempts[0]`, then run:

Run: `uv run pytest tests/test_cli.py -k select_attempt -v`
Expected: FAIL on `test_select_attempt_defaults_to_the_last_phase_with_attempts_and_its_highest_n` (`assert 1 == 2`). Revert the change and re-run; expected PASS (7 tests).

- [ ] **Step 10: Write the failing test for a non-positive `--attempt` (Review Focus 2)**

Append to `tests/test_cli.py` after `test_a_card_with_no_attempts_at_all_is_the_attempt_refusal`:

```python
@pytest.mark.parametrize("n", [0, -1])
def test_a_non_positive_attempt_number_is_refused_rather_than_defaulting(n):
    """Typer will hand over any int, and `models.Attempt.n` is `gt=0`, so no such
    attempt can exist. `--attempt 0` must refuse, not quietly report the highest
    attempt as though no flag had been passed."""
    with pytest.raises(cli.UnknownAttemptError) as caught:
        cli.select_attempt(_two_phase_subtask(), phase="explore", attempt=n)

    assert str(n) in str(caught.value)
```

- [ ] **Step 11: Run it to verify it passes**

Run: `uv run pytest tests/test_cli.py -k non_positive_attempt -v`
Expected: PASS (2 parametrisations) — the `for candidate in chosen.attempts` loop already falls through to the refusal, because `attempt is None` is checked with `is`, not truthiness.

- [ ] **Step 12: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): select the phase and attempt logs should report"
```

---

### Task 3: Artifact reading and payload building

**Files:**
- Modify: `src/agent_manager/cli.py` (add `read_artifact` and `logs_payload` after `select_attempt`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `models.Run`, `models.StoryRun`, `models.SubtaskRun`, `models.PhaseRun`, `models.Attempt`; `cli.render` / `cli.ok_envelope` (`src/agent_manager/cli.py:113-132`); the Task 1/2 test helpers `_pure_run`, `_pure_story`, `_pure_subtask`, `_pure_dispatch`.
- Produces: `read_artifact(path: Path | None) -> dict[str, Any]` returning `{"path": Path | None, "present": bool, "text": str | None}`, and `logs_payload(run, story, subtask, phase, attempt) -> dict[str, Any]` with keys `run_id`, `story_id`, `card`, `phase`, `attempt`, `status`, `exit_code`, `artifacts`. Task 4's `logs_for` returns `logs_payload`'s result unchanged.

- [ ] **Step 1: Write the failing test for reading the three artifacts**

Append to `tests/test_cli.py` after `test_a_non_positive_attempt_number_is_refused_rather_than_defaulting`:

```python
def _artifact_attempt(directory: Path, n: int = 1, **overrides) -> models.Attempt:
    """An attempt whose three recorded paths point into `directory`."""
    fields: dict[str, Any] = {
        "prompt_path": directory / "prompt.txt",
        "result_path": directory / "result.json",
        "stdout_path": directory / "stdout.log",
    }
    fields.update(overrides)
    return models.Attempt(
        n=n, dispatch=_pure_dispatch(), status="ok", exit_code=0, **fields
    )


def _payload_for(attempt: models.Attempt) -> dict[str, Any]:
    phase = models.PhaseRun(
        name="implement", kind="agent", status="done", attempts=[attempt]
    )
    subtask = _pure_subtask("card-1", [phase])
    story = _pure_story("story-1", [subtask])
    run = _pure_run([story])
    return cli.logs_payload(run, story, subtask, phase, attempt)


def test_the_payload_reads_the_three_artifacts_off_disk(tmp_path):
    (tmp_path / "prompt.txt").write_text("you are the coder\n", encoding="utf-8")
    (tmp_path / "result.json").write_text('{"ok": true}', encoding="utf-8")
    (tmp_path / "stdout.log").write_text("line one\nline two\n", encoding="utf-8")

    payload = _payload_for(_artifact_attempt(tmp_path))

    assert payload["run_id"] == "20260923T140506Z-cbe34d00"
    assert payload["story_id"] == "story-1"
    assert payload["card"] == "card-1"
    assert payload["phase"] == "implement"
    assert payload["attempt"] == 1
    assert payload["status"] == "ok"
    assert payload["exit_code"] == 0
    assert payload["artifacts"]["prompt"] == {
        "path": tmp_path / "prompt.txt",
        "present": True,
        "text": "you are the coder\n",
    }
    assert payload["artifacts"]["result"]["text"] == '{"ok": true}'
    assert payload["artifacts"]["stdout"]["text"] == "line one\nline two\n"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_the_payload_reads_the_three_artifacts_off_disk -v`
Expected: FAIL with `AttributeError: module 'agent_manager.cli' has no attribute 'logs_payload'`

- [ ] **Step 3: Add the reader and the payload builder**

In `src/agent_manager/cli.py`, add after `select_attempt`:

```python
def read_artifact(path: Path | None) -> dict[str, Any]:
    """One artifact as `{path, present, text}`, never a raised exception.

    `logs` exists to show an operator what the harness produced -- including the
    half-written or malformed file that made a phase fail -- so a missing path, a
    missing file and undecodable bytes are all facts to report, not refusals.
    `is_file()` rather than `exists()`: a recorded path that somehow names a
    directory must read as absent instead of raising `IsADirectoryError` out of
    a read-only command. `errors="replace"` for the same reason.

    `path` stays a `Path`; `render`'s `default=str` stringifies it once at the
    edge, exactly as `status_payload` leaves `worktree_path` alone.
    """
    if path is None or not Path(path).is_file():
        return {"path": path, "present": False, "text": None}
    return {
        "path": path,
        "present": True,
        "text": Path(path).read_text(encoding="utf-8", errors="replace"),
    }


def logs_payload(
    run: models.Run,
    story: models.StoryRun,
    subtask: models.SubtaskRun,
    phase: models.PhaseRun,
    attempt: models.Attempt,
) -> dict[str, Any]:
    """§10's `logs` output: what was selected, and the three artifacts of it.

    The locations come from the `Attempt` row the projection already holds, never
    from `paths.attempt_dir` -- that helper creates the directory it names, and
    a read-only command that minted an artifact directory for a run nobody
    started would be writing state outside `Store`.

    `result.json` is read as text like the other two and is deliberately not
    parsed: the unparseable result is precisely the one an operator runs `logs`
    to look at.
    """
    return {
        "run_id": run.id,
        "story_id": story.card_id,
        "card": subtask.card_id,
        "phase": phase.name,
        "attempt": attempt.n,
        "status": attempt.status,
        "exit_code": attempt.exit_code,
        "artifacts": {
            "prompt": read_artifact(attempt.prompt_path),
            "result": read_artifact(attempt.result_path),
            "stdout": read_artifact(attempt.stdout_path),
        },
    }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_cli.py::test_the_payload_reads_the_three_artifacts_off_disk -v`
Expected: PASS

- [ ] **Step 5: Write the failing tests for absent, null, directory and empty artifacts (Review Focus 3 and 5)**

Append to `tests/test_cli.py` after `test_the_payload_reads_the_three_artifacts_off_disk`:

```python
def test_a_missing_file_and_a_null_path_are_both_absent_not_a_refusal(tmp_path):
    """`Attempt.prompt_path` and friends are `Path | None`, and a phase can die
    between recording an attempt and writing its files. Both are facts."""
    (tmp_path / "prompt.txt").write_text("you are the coder\n", encoding="utf-8")
    attempt = _artifact_attempt(tmp_path, result_path=None)

    payload = _payload_for(attempt)

    assert payload["artifacts"]["prompt"]["present"] is True
    assert payload["artifacts"]["result"] == {"path": None, "present": False, "text": None}
    assert payload["artifacts"]["stdout"] == {
        "path": tmp_path / "stdout.log",
        "present": False,
        "text": None,
    }


def test_an_empty_artifact_is_present_with_empty_text(tmp_path):
    """`""` is falsy and must not be conflated with the `None` of a missing file:
    a harness that produced no output at all is a different diagnosis from a
    harness that never got far enough to write the file."""
    (tmp_path / "stdout.log").write_text("", encoding="utf-8")

    payload = _payload_for(_artifact_attempt(tmp_path))

    assert payload["artifacts"]["stdout"] == {
        "path": tmp_path / "stdout.log",
        "present": True,
        "text": "",
    }


def test_a_recorded_path_that_names_a_directory_reads_as_absent(tmp_path):
    """`exists()` would say yes and `read_text` would raise `IsADirectoryError`,
    turning a read-only report into a traceback."""
    (tmp_path / "stdout.log").mkdir()

    payload = _payload_for(_artifact_attempt(tmp_path))

    assert payload["artifacts"]["stdout"]["present"] is False
    assert payload["artifacts"]["stdout"]["text"] is None
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "artifact or null_path or empty_artifact" -v`
Expected: PASS (all of the above) — Step 3's `is_file()` check is what makes the directory case and the missing-file case behave.

- [ ] **Step 7: Write the failing tests for unreadable content (spec item 12 and Review Focus 4)**

Append to `tests/test_cli.py` after `test_a_recorded_path_that_names_a_directory_reads_as_absent`:

```python
def test_a_result_json_that_is_not_valid_json_comes_back_as_raw_text(tmp_path):
    """The malformed result is the one that made the phase fail, and it is exactly
    what an operator runs `logs` to read. `logs` must never parse it."""
    (tmp_path / "result.json").write_text('{"summary": "half a fi', encoding="utf-8")

    payload = _payload_for(_artifact_attempt(tmp_path))

    assert payload["artifacts"]["result"]["present"] is True
    assert payload["artifacts"]["result"]["text"] == '{"summary": "half a fi'


def test_undecodable_bytes_in_an_artifact_are_replaced_not_raised(tmp_path):
    """A harness that dumped raw terminal output is not a reason for a read-only
    command to die with a `UnicodeDecodeError`."""
    (tmp_path / "stdout.log").write_bytes(b"ok \xff\xfe done\n")

    payload = _payload_for(_artifact_attempt(tmp_path))

    assert payload["artifacts"]["stdout"]["present"] is True
    assert payload["artifacts"]["stdout"]["text"].startswith("ok ")
    assert payload["artifacts"]["stdout"]["text"].endswith(" done\n")
    assert "�" in payload["artifacts"]["stdout"]["text"]
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "raw_text or undecodable" -v`
Expected: PASS (2 tests)

- [ ] **Step 9: Write the failing test for the render round trip**

Append to `tests/test_cli.py` after `test_undecodable_bytes_in_an_artifact_are_replaced_not_raised`:

```python
def test_the_logs_payload_survives_render_with_its_paths_and_newlines(tmp_path):
    """The payload carries three `Path`s that `json.dumps` refuses, and artifact
    text full of newlines that the one-line default must escape rather than
    break."""
    (tmp_path / "prompt.txt").write_text("first\nsecond\n", encoding="utf-8")

    payload = _payload_for(_artifact_attempt(tmp_path))
    text = cli.render(cli.ok_envelope(payload))

    assert "\n" not in text
    data = json.loads(text)["data"]
    assert data["artifacts"]["prompt"]["path"] == str(tmp_path / "prompt.txt")
    assert data["artifacts"]["prompt"]["text"] == "first\nsecond\n"
    assert data["artifacts"]["result"]["path"] == str(tmp_path / "result.json")
```

- [ ] **Step 10: Run it to verify it passes**

Run: `uv run pytest tests/test_cli.py::test_the_logs_payload_survives_render_with_its_paths_and_newlines -v`
Expected: PASS

- [ ] **Step 11: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): build the logs payload from an attempt's recorded artifacts"
```

---

### Task 4: `logs_for` and the Typer command

**Files:**
- Modify: `src/agent_manager/cli.py` (add `logs_for` and `@app.command("logs")` at the end of the file, after `runs`, which ends at line 536)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `resolve_repo_dir` (`cli.py:78`), `store_module.open_db` (`store.py:95`), `store_module.load_run` (`store.py:366`), `UnknownRunError` (`cli.py:57`), `HANDLED` (`cli.py:405`), `EXIT_ERROR` (`cli.py:38`), `ok_envelope` / `error_envelope` / `render`, plus Task 1's `find_subtask` / `UnknownCardError`, Task 2's `select_attempt`, Task 3's `logs_payload`.
- Produces: `logs_for(run_id: str, card: str, *, repo_dir: Path, phase: str | None = None, attempt: int | None = None) -> dict[str, Any]` and the `logs` subcommand. Nothing later in this plan consumes them.

- [ ] **Step 1: Write the fixtures and the failing happy-path test**

Append to the end of `tests/test_cli.py` (after `test_a_repo_dir_that_is_a_file_is_an_envelope_for_both_commands`, which ends at line 1213):

```python
def _write_logs_attempt(
    run_id: str, phase: str, n: int, *, stdout: bool = True
) -> models.Attempt:
    """One attempt's three files on disk, plus the row that points at them.

    The *test* calls `paths.attempt_dir` -- which creates the directory -- because
    in production `dispatch.AgentRunner` is what creates it. `logs` itself must
    never call it, and `test_logs_writes_nothing` is what pins that.
    """
    directory = paths.attempt_dir(run_id, "card-1", phase, n)
    (directory / "prompt.txt").write_text(f"prompt for {phase}.{n}\n", encoding="utf-8")
    (directory / "result.json").write_text(
        json.dumps({"phase": phase, "attempt": n}), encoding="utf-8"
    )
    if stdout:
        (directory / "stdout.log").write_text(f"stdout of {phase}.{n}\n", encoding="utf-8")
    return models.Attempt(
        n=n,
        dispatch=_recorded_dispatch(run_id),
        status="ok" if n > 1 else "gate_failed",
        exit_code=0 if n > 1 else 1,
        prompt_path=directory / "prompt.txt",
        result_path=directory / "result.json",
        stdout_path=directory / "stdout.log",
    )


def _record_for_logs(root: Path, run_id: str, *, stdout: bool = True) -> None:
    """A run with two agent phases (two attempts, then one) and a pending phase.

    The trailing `verify` phase has no attempts, so the no-flag default has to
    skip it to reach `implement`.
    """
    opened = store_module.Store.open(root, run_id)
    try:
        opened.record_run(
            models.Run(
                id=run_id,
                workflow="task",
                repo_dir=root,
                base_branch="main",
                branch_prefix="m1",
                status="done",
                started_at=RECORDED_AT,
            )
        )
        opened.record_story(
            models.StoryRun(card_id="story-1", title="The CLI", level=0, status="done")
        )
        opened.record_subtask(
            "story-1",
            models.SubtaskRun(
                card_id="card-1", branch="m1/task-x", base_branch="main", status="done"
            ),
        )
        opened.record_phase(
            "story-1", "card-1", models.PhaseRun(name="explore", kind="agent", status="done")
        )
        opened.record_attempt(
            "story-1", "card-1", "explore", _write_logs_attempt(run_id, "explore", 1)
        )
        opened.record_attempt(
            "story-1", "card-1", "explore", _write_logs_attempt(run_id, "explore", 2)
        )
        opened.record_phase(
            "story-1",
            "card-1",
            models.PhaseRun(name="implement", kind="agent", status="done"),
        )
        opened.record_attempt(
            "story-1",
            "card-1",
            "implement",
            _write_logs_attempt(run_id, "implement", 1, stdout=stdout),
        )
        opened.record_phase(
            "story-1",
            "card-1",
            models.PhaseRun(name="verify", kind="deterministic", status="pending"),
        )
    finally:
        opened.close()


LOGS_RUN_ID = "20260923T090000Z-cbe34d00"


def test_logs_with_no_flags_reports_the_latest_attempt_of_the_latest_phase(projection):
    _record_for_logs(projection, LOGS_RUN_ID)

    result = runner.invoke(
        cli.app, ["logs", LOGS_RUN_ID, "card-1", "--repo-dir", str(projection)]
    )

    assert result.exit_code == 0
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    data = envelope["data"]
    assert data["run_id"] == LOGS_RUN_ID
    assert data["story_id"] == "story-1"
    assert data["card"] == "card-1"
    assert data["phase"] == "implement"
    assert data["attempt"] == 1
    assert data["artifacts"]["prompt"]["text"] == "prompt for implement.1\n"
    assert data["artifacts"]["result"]["text"] == '{"phase": "implement", "attempt": 1}'
    assert data["artifacts"]["stdout"]["text"] == "stdout of implement.1\n"
    assert "\n" not in result.stdout.strip()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_logs_with_no_flags_reports_the_latest_attempt_of_the_latest_phase -v`
Expected: FAIL with exit code 2 and Typer's "No such command 'logs'."

- [ ] **Step 3: Add `logs_for` and the command**

Append to the end of `src/agent_manager/cli.py`:

```python
def logs_for(
    run_id: str,
    card: str,
    *,
    repo_dir: Path,
    phase: str | None = None,
    attempt: int | None = None,
) -> dict[str, Any]:
    """§10's `logs`: one attempt of one card of one run, with its artifacts.

    Read-only, like `status_for`: the projection is reached through the free
    `open_db` / `load_run` rather than `Store.open`, which would construct a
    `Journal` and therefore mint a run directory for a run that may not exist.
    The connection is closed on every path including the refusals.

    `run_id` is required -- §10 writes `logs <run-id> <card>` and there is no
    "most recent run" reading of it to default to.
    """
    root = resolve_repo_dir(repo_dir)
    conn = store_module.open_db(root)
    try:
        run = store_module.load_run(conn, run_id)
        if run is None:
            raise UnknownRunError(
                f"run {run_id!r} is not in the projection for {root}"
                " (`agent-manager runs` lists the ones that are)"
            )
        found = find_subtask(run, card)
        if found is None:
            raise UnknownCardError(
                f"card {card!r} is not in run {run_id!r}"
                f" (`agent-manager status {run_id}` lists the cards that are)"
            )
        story, subtask = found
        chosen_phase, chosen_attempt = select_attempt(subtask, phase=phase, attempt=attempt)
        return logs_payload(run, story, subtask, chosen_phase, chosen_attempt)
    finally:
        conn.close()


@app.command("logs")
def logs(
    run_id: str = typer.Argument(..., metavar="RUN_ID", help="The run to read."),
    card: str = typer.Argument(..., metavar="CARD", help="The subtask card id."),
    phase: str | None = typer.Option(
        None, "--phase", help="Which phase. Defaults to the last one with attempts."
    ),
    attempt: int | None = typer.Option(
        None, "--attempt", help="Which attempt. Defaults to the highest recorded."
    ),
    repo_dir: Path = typer.Option(
        Path("."), "--repo-dir", help="The repository whose projection is read."
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the JSON envelope."),
) -> None:
    """Print one attempt's prompt, result and captured stdout."""
    try:
        payload = logs_for(
            run_id, card, repo_dir=repo_dir, phase=phase, attempt=attempt
        )
    except HANDLED as error:
        typer.echo(render(error_envelope(error), pretty=pretty))
        raise typer.Exit(EXIT_ERROR) from None
    typer.echo(render(ok_envelope(payload), pretty=pretty))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_cli.py::test_logs_with_no_flags_reports_the_latest_attempt_of_the_latest_phase -v`
Expected: PASS

- [ ] **Step 5: Write the failing tests for the flags**

Append to the end of `tests/test_cli.py`:

```python
def test_logs_phase_and_attempt_together_select_an_earlier_attempt(projection):
    _record_for_logs(projection, LOGS_RUN_ID)

    result = runner.invoke(
        cli.app,
        [
            "logs",
            LOGS_RUN_ID,
            "card-1",
            "--phase",
            "explore",
            "--attempt",
            "1",
            "--repo-dir",
            str(projection),
        ],
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)["data"]
    assert data["phase"] == "explore"
    assert data["attempt"] == 1
    assert data["status"] == "gate_failed"
    assert data["exit_code"] == 1
    assert data["artifacts"]["prompt"]["text"] == "prompt for explore.1\n"


def test_logs_pretty_indents_the_same_envelope(projection):
    _record_for_logs(projection, LOGS_RUN_ID)

    plain = runner.invoke(
        cli.app, ["logs", LOGS_RUN_ID, "card-1", "--repo-dir", str(projection)]
    )
    pretty = runner.invoke(
        cli.app, ["logs", LOGS_RUN_ID, "card-1", "--repo-dir", str(projection), "--pretty"]
    )

    assert pretty.exit_code == 0
    assert "\n" in pretty.stdout.strip()
    assert json.loads(pretty.stdout) == json.loads(plain.stdout)


def test_logs_reports_an_attempt_whose_stdout_was_never_written(projection):
    """An attempt that exists is always reportable: the missing file is the
    finding, not a reason to refuse."""
    _record_for_logs(projection, LOGS_RUN_ID, stdout=False)

    result = runner.invoke(
        cli.app, ["logs", LOGS_RUN_ID, "card-1", "--repo-dir", str(projection)]
    )

    assert result.exit_code == 0
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    data = envelope["data"]
    assert data["artifacts"]["prompt"]["present"] is True
    assert data["artifacts"]["stdout"]["present"] is False
    assert data["artifacts"]["stdout"]["text"] is None
    assert data["artifacts"]["stdout"]["path"].endswith("stdout.log")
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "logs_phase_and_attempt or logs_pretty or stdout_was_never_written" -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Write the failing tests for the refusals**

Append to the end of `tests/test_cli.py`:

```python
def test_logs_for_an_unknown_run_is_an_envelope(projection):
    _record_for_logs(projection, LOGS_RUN_ID)

    result = runner.invoke(
        cli.app, ["logs", "no-such-run", "card-1", "--repo-dir", str(projection)]
    )

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "UnknownRunError"
    assert "no-such-run" in envelope["error"]["message"]


def test_logs_for_a_card_that_is_not_in_the_run_is_an_envelope(projection):
    _record_for_logs(projection, LOGS_RUN_ID)

    result = runner.invoke(
        cli.app, ["logs", LOGS_RUN_ID, "card-9", "--repo-dir", str(projection)]
    )

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "UnknownCardError"
    assert "card-9" in envelope["error"]["message"]
    assert LOGS_RUN_ID in envelope["error"]["message"]


def test_logs_for_an_unknown_phase_or_attempt_is_an_envelope(projection):
    _record_for_logs(projection, LOGS_RUN_ID)
    base = ["logs", LOGS_RUN_ID, "card-1", "--repo-dir", str(projection)]

    unknown_phase = runner.invoke(cli.app, [*base, "--phase", "reveiw"])
    assert unknown_phase.exit_code == cli.EXIT_ERROR
    phase_envelope = json.loads(unknown_phase.stdout)
    assert phase_envelope["error"]["type"] == "UnknownPhaseError"
    assert "reveiw" in phase_envelope["error"]["message"]
    assert "implement" in phase_envelope["error"]["message"]

    unknown_attempt = runner.invoke(cli.app, [*base, "--phase", "explore", "--attempt", "9"])
    assert unknown_attempt.exit_code == cli.EXIT_ERROR
    attempt_envelope = json.loads(unknown_attempt.stdout)
    assert attempt_envelope["error"]["type"] == "UnknownAttemptError"
    assert "9" in attempt_envelope["error"]["message"]


def test_logs_with_a_repo_dir_that_is_not_a_directory_is_an_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    result = runner.invoke(
        cli.app, ["logs", LOGS_RUN_ID, "card-1", "--repo-dir", str(tmp_path / "missing")]
    )

    assert result.exit_code == cli.EXIT_ERROR
    envelope = json.loads(result.stdout)
    assert envelope["error"]["type"] == "RepoDirError"
    assert "missing" in envelope["error"]["message"]
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "logs_for_an_unknown or logs_for_a_card or unknown_phase_or_attempt or logs_with_a_repo_dir" -v`
Expected: PASS (4 tests)

- [ ] **Step 9: Write the failing test that `logs` writes nothing**

Append to the end of `tests/test_cli.py`:

```python
def _runs_snapshot() -> dict[str, bytes]:
    """Every path under `XDG_DATA_HOME/agent-manager/runs`, with file contents.

    Only the `runs` tree: the SQLite projection's own `-wal` and `-shm` sidecars
    come and go with any reader, including a legitimate read-only one, so the
    `attempts` table is snapshotted as rows instead.
    """
    root = paths.data_dir() / "runs"
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else b"<dir>"
        for path in sorted(root.rglob("*"))
    }


def _attempt_rows(root: Path) -> list[tuple]:
    conn = sqlite3.connect(paths.project_db_path(root))
    try:
        return conn.execute(
            "SELECT run_id, story_id, card_id, phase, n, status, prompt_path,"
            " result_path, stdout_path FROM attempts ORDER BY phase, n"
        ).fetchall()
    finally:
        conn.close()


def test_logs_writes_nothing(projection):
    """`logs` is read-only: it opens no `Journal`, records nothing, and never
    calls `paths.attempt_dir` or `paths.run_dir`, both of which mkdir as a side
    effect. A refusal for an unknown run must leave no `runs/<run-id>` behind."""
    _record_for_logs(projection, LOGS_RUN_ID)
    tree_before = _runs_snapshot()
    rows_before = _attempt_rows(projection)

    success = runner.invoke(
        cli.app, ["logs", LOGS_RUN_ID, "card-1", "--repo-dir", str(projection)]
    )
    assert success.exit_code == 0
    assert _runs_snapshot() == tree_before
    assert _attempt_rows(projection) == rows_before

    refusal = runner.invoke(
        cli.app, ["logs", "no-such-run", "card-1", "--repo-dir", str(projection)]
    )
    assert refusal.exit_code == cli.EXIT_ERROR
    assert _runs_snapshot() == tree_before
    assert _attempt_rows(projection) == rows_before
    assert not (paths.data_dir() / "runs" / "no-such-run").exists()
```

- [ ] **Step 10: Run it to verify it passes, then prove it bites**

Run: `uv run pytest tests/test_cli.py::test_logs_writes_nothing -v`
Expected: PASS

Then temporarily insert `paths.attempt_dir(run_id, card, "explore", 1)` as the first line of `logs_for`'s `try:` block, add `from agent_manager import paths` to `src/agent_manager/cli.py`'s imports, and re-run:

Run: `uv run pytest tests/test_cli.py::test_logs_writes_nothing -v`
Expected: FAIL — the snapshot for the refusal case differs (`runs/no-such-run/...` appeared). Revert both edits, including the import, and re-run; expected PASS.

- [ ] **Step 11: Run the whole suite**

Run: `uv run pytest`
Expected: PASS, with no test in `tests/test_cli.py` failing and no new warnings.

- [ ] **Step 12: Commit**

```bash
git add src/agent_manager/cli.py tests/test_cli.py
git commit -m "feat(cli): add the logs command for one attempt's artifacts"
```
