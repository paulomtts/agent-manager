<!-- task-pipeline: validated -->
# Turn on the critic loops in TASK (card 058981d3)

Narrows pygents-engine design G4 (`docs/superpowers/specs/2026-09-25-pygents-engine-design.md:86-89`) and plan Task 5.1 (`docs/superpowers/plans/2026-09-25-pygents-engine.md:1322-1330`) to one subtask. Parent: 1ec08da2 "Switch over". This card has no `blocked_by`; a300ab2b (real harness), 7a744199 (pygents-only engine) and e46098be (docs) come after it and are out of scope.

## Scope

Files touched, and only these:

- `src/agent_manager/workflow/task.py`
- `tests/workflow/test_declared.py`
- `tests/e2e/fake_claude.py`
- `tests/e2e/test_production_wiring.py`

Changes to `TASK` in `workflow/task.py`:

- `validate_spec` gets `on_fail=Goto("spec")`. `validate_plan` gets `on_fail=Goto("plan")`. Both use the default `max_loops=1`. `Goto` is imported from `agent_manager.workflow.phases`.
- `"feedback"` is added to `spec`'s `inputs`, which become `("card", "explore", "spec_path", "feedback")`, and to `plan`'s `inputs`, which become `("spec_path", "plan_path", "feedback")`.
- `review` gets no `on_fail`. Review does not loop. No other phase changes. Timeouts, gates and retry stay as they are.
- `task.py` still imports nothing from pygents (rule 1). Its module docstring should say that `on_fail` and `feedback`, like timeouts, are data the YAML never had.

These are declaration changes only. The following are already implemented in the base state and stay unchanged:

- the loop-back and escalate mechanics in `runtime/compile.py` `agent_phase`
- `Goto` and the `Workflow.validate()` check that a `Goto` names an earlier phase
- the `_feedback` resolver in `prompt.py`
- `runtime/context.py`'s feedback table
- `reducers.critic_blockers_gate`

Nothing in `runtime/`, `engine.py`, `workflow/loader.py`, `workflow/registry.py`, `workflow/builtin/task.yaml` or the README changes. Base the branch on the merged m6 stories 1-4 state (for example `.claude/worktrees/m6/task-resume-and-relaunch-02890d5d/`), not on `master`, which does not have `workflow/task.py`.

## Observable behaviour

Under `--engine pygents`:

- **Critic blocks once:** if `validate_spec` blocks once, the run loops back to `spec`. `spec` is dispatched a second time, and its brief has a `feedback` section that contains the critic's reason. Then `validate_spec` passes and the run finishes `done`. `validate_plan` loops back to `plan` in the same way.
- **Critic blocks twice:** if `validate_spec` blocks on two consecutive attempts, the loop budget (`max_loops=1`) is used up. The run escalates `validation` the way it does today: `am run` exits 1 and `failed_phase == "validate_spec"`.
- **No block:** when no critic blocks, the `feedback` input is empty. `_feedback` then returns no section, so the spec and plan briefs are byte-identical to today's. The existing golden briefs and the engine-parity baseline must not change.

Under `--engine yaml`, a critic block still escalates on the first block. That is existing, documented behaviour, and this card asserts it without changing it.

Rule 5 / G10: `SubtaskSummary`, journal lines, phase/attempt rows and escalation payloads keep their current shape. A loop adds rows the way a normal re-dispatch does. It adds no new fields.

## Error paths

- **Second critic failure:** escalates at the critic phase, with the critic's detail. It does not escalate at the looped-to phase.
- **Invalid workflow:** if the change breaks `TASK.validate()` (a `Goto` to a later or unknown phase, `max_loops < 1`, a timeout not above `LAUNCHER_TIMEOUT` (G2), or `feedback` rejected as an input name), `test_both_validate` must fail.
- **Fake misconfiguration:** a misconfigured "blocks" marker or env value in the fake raises `FakeClaudeError`, the same way the rendezvous count validation does. It never silently passes.

## Test scaffolding: fake_claude "critic blocks" mode

Today `build_result()` always returns `blockers=False` for `validate_spec` and `validate_plan` (`tests/e2e/fake_claude.py:538-539`). Add a trigger that the test controls from outside the brief, following the `REVIEW_FAIL_MARKER` and rendezvous scaffolding. The trigger is either a marker file in the git common dir or a `FAKE_CLAUDE_*` env var. It must say which critic phase blocks and how many times (1 or 2).

- When the trigger is active for a phase, the fake returns `blockers=True` with a fixed, module-level reason constant, so a test can assert that the reason shows up in the next `spec` or `plan` brief. It does this until it has blocked the requested number of times, then it goes back to `blockers=False`.
- The fake may keep its block count in the git common dir or in a test-supplied directory, the way rendezvous leaves markers.
- Rule 4: the trigger and the count must never come from the brief's own content.
- With no trigger, behaviour is exactly today's.

## Tests

The placement rule is pygents-engine design §9 (lines 379-401):

- Tests mirror source modules one-for-one.
- Pure declared-data checks go in `tests/workflow/`.
- Wiring and critic-loop scenarios go in the existing engine-parametrized `tests/e2e/test_production_wiring.py` tier. Do not create a new tier.
- No test calls a model.

1. **`test_task_equals_the_shipped_yaml`** (existing, updated). Tier: `tests/workflow/test_declared.py`, pure-data unit tier. Change `_shipped()` so it takes `on_fail` from `like.phase(p.name)` (the same pattern as `timeout`), and adds `"feedback"` to `inputs` wherever the declared phase has it. The declared `TASK` digest must still equal the shipped YAML plus only those additions. `INTEGRATE`'s comparison must still pass unchanged.
2. **`test_task_critics_loop_back_once`** (new). Tier: `tests/workflow/test_declared.py`, pure-data unit tier. Asserts:
   - `TASK.phase("validate_spec").on_fail == Goto("spec", 1)`
   - `TASK.phase("validate_plan").on_fail == Goto("plan", 1)`
   - `TASK.phase("review").on_fail is None`
   - every other AgentPhase has `on_fail is None`
   - `"feedback"` is in the `spec` and `plan` inputs and in no other phase's inputs
3. **`test_both_validate`** and **`test_declared_modules_never_import_pygents`** (existing, unchanged). Tier: `tests/workflow/test_declared.py`. They must stay green.
4. **Critic blocks once, run finishes done** (new, pygents). Tier: `tests/e2e/test_production_wiring.py`, pygents engine. Set the fake's trigger so `validate_spec` blocks once. Assert that:
   - the run ends `done` and the board card is `done`
   - `spec` was dispatched twice
   - the second `spec` brief's `feedback` section contains the fake's reason constant
5. **Critic blocks twice, run escalates** (new, pygents). Tier: `tests/e2e/test_production_wiring.py`, pygents engine. Set the trigger so `validate_spec` blocks twice. Assert that `am run --card` exits 1, `failed_phase == "validate_spec"`, and the escalation kind is `validation`.
6. **Critic blocks once under yaml, run escalates immediately** (new, yaml). Tier: `tests/e2e/test_production_wiring.py`, yaml engine. Set the trigger so `validate_spec` blocks once. Assert exit 1 and `failed_phase == "validate_spec"` on the first block, with `spec` dispatched only once. This is the documented yaml behaviour.
7. **Existing parametrized wiring and parity tests** (existing, unchanged). Tier: `tests/e2e/test_production_wiring.py`, both engines. With no trigger set, `test_the_run_data_is_the_same_on_both_engines` and the golden briefs stay green. This shows that an empty `feedback` adds nothing to the briefs.
8. **Fake mode self-check** (new, optional, only if the fake's other modes already have self-tests). Tier: beside the existing `fake_claude` self-tests in `tests/e2e/`. It checks that a malformed trigger raises `FakeClaudeError`.

The critic-loop scenarios in tests 4-6 each run on their own repo and board. They must not change the shared `completed_run` fixture that the parity and golden tests read.

## Verification

`uv run pytest` (full suite). There is no typecheck or lint command.

---

# Turn on the critic loops in TASK Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Declare the two critic loops in `TASK` (`validate_spec -> spec`, `validate_plan -> plan`, once each) and the `feedback` input on `spec`/`plan`, with a test-controlled "critic blocks" mode in the fake `claude` that proves the loop end to end under `--engine pygents` and proves `--engine yaml` still escalates on the first block.

**Architecture:** The loop mechanics already live in `runtime/compile.py` (`agent_phase` yields a `ContextItem` `{"for", "from", "detail"}` and re-enters `on_fail.phase` with `loop+1`, else raises `Escalated`), `runtime/context.py` (the per-phase `feedback` table) and `prompt._feedback` (renders `## feedback` with `Feedback from review` then `- <critic>: <detail>`). This card changes only declared data in `workflow/task.py`, the pure-data test that pins `TASK` to the shipped YAML, and the fake/e2e scaffolding. The fake gets its block budget from an env var naming a JSON file the test writes (the rendezvous pattern), never from the brief (Rule 4).

**Tech Stack:** Python 3, pytest, Typer `CliRunner`, `uv`. Real `git` and `brd` CLIs in the e2e tier.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-turn-on-the-critic-058981d3/docs/superpowers/specs/task-turn-on-the-critic-058981d3-design.md` (prepended above).

**Branch / worktree:** `m6/task-turn-on-the-critic-058981d3` in `/home/paulomtts/Code/agent-manager/.claude/worktrees/m6/task-turn-on-the-critic-058981d3`, cut from `m6/task-resume-and-relaunch-02890d5d`. Everything this plan reads (`workflow/task.py`, `workflow/phases.py` `Goto`, `runtime/compile.py`, `prompt._feedback`, the e2e fixtures) is already on that base. Do not assume any sibling subtask (a300ab2b, 7a744199, e46098be) has landed.

## Global Constraints

- Files touched: `src/agent_manager/workflow/task.py`, `tests/workflow/test_declared.py`, `tests/e2e/fake_claude.py`, `tests/e2e/test_production_wiring.py`, plus the fake's own self-tests in `tests/e2e/test_fake_claude.py` (spec test 8: "beside the existing `fake_claude` self-tests in `tests/e2e/`"). Nothing in `runtime/`, `engine.py`, `workflow/loader.py`, `workflow/registry.py`, `workflow/builtin/task.yaml`, `tests/e2e/conftest.py` or the README.
- `validate_spec`: `on_fail=Goto("spec")`; `validate_plan`: `on_fail=Goto("plan")`; default `max_loops=1`. `review` gets no `on_fail`.
- `spec` inputs become `("card", "explore", "spec_path", "feedback")`; `plan` inputs become `("spec_path", "plan_path", "feedback")`.
- Rule 1: `workflow/task.py` imports nothing from pygents.
- Rule 4: the fake's critic trigger and count never come from the brief's content.
- Rule 5 / G10: `SubtaskSummary`, journal lines, phase/attempt rows and escalation payloads keep their shape.
- G2: every AgentPhase timeout stays strictly above `LAUNCHER_TIMEOUT`; timeouts are not touched.
- `--engine yaml` still escalates on the first critic block; asserted, not changed.
- No test calls a model. Critic-loop scenarios run on their own repo and board and never touch the shared `completed_run` fixture.
- Verification: `uv run pytest` (no typecheck, no lint).

## Review Focus

1. Both critics blocking once in the same run (`validate_spec` then `validate_plan`): a person reading G4 ("each at most once") expects both loops to happen and the run to finish `done`. `runtime/compile.py` carries one `loop` counter along the whole walk (`after(phase, loop)` passes it on), so a spec loop leaves `validate_plan` at `loop=1` and its first block escalates. Fixing that is a `runtime/` change and outside this card's files, so Task 2 pins it with a `strict=True` xfail test that turns loud the day the runtime is fixed.
2. A critic block under `--engine yaml`: expected to escalate at once, with `spec` dispatched once and no loop (Task 2, spec test 6).
3. No critic blocks: no brief on either engine carries a `## feedback` section, so the change is invisible to a clean run (Task 2 guard test, on the shared `completed_run`, read-only).
4. A malformed critic-blocks budget (missing file, bad JSON, non-object, unknown phase, count outside 0..2, a bool or a string count): the fake stops with `FakeClaudeError` naming the env var and the process exits 1, never a silent pass (Task 1).
5. Feedback reaches only the looped-to phase and only after the loop: the first `spec` brief, every critic brief, and the other author's brief carry no `## feedback` section (asserted in Task 2's blocks-once scenario).

---

### Task 1: Fake `claude` critic-blocks mode

**Files:**
- Modify: `tests/e2e/fake_claude.py:8-24` (module docstring), after `:321` (new constants and functions, placed after `rendezvous`), `:538-539` (critic branch of `build_result`)
- Test: `tests/e2e/test_fake_claude.py` (append; import line `:23`)

**Interfaces:**
- Consumes: `fake_claude.FakeClaudeError`, `fake_claude.override`, `fake_claude.SUMMARY`, `fake_claude.payload_from_schema`, `fake_claude.build_result(phase, payload, text, cwd)`; `agent_manager.steps.reducers.critic_blockers_gate(result) -> dict | None`.
- Produces (Task 2 relies on these exact values across a process boundary, re-declared there as literals):
  - `CRITIC_BLOCKS_ENV = "FAKE_CLAUDE_CRITIC_BLOCKS"` — env var holding the path of a JSON budget file.
  - `CRITIC_PHASES = ("validate_spec", "validate_plan")`
  - `MAX_CRITIC_BLOCKS = 2`
  - `CRITIC_BLOCK_REASON = "fake-claude critic: the critic-blocks budget told this critic to block"`
  - `critic_blocks(phase: str) -> bool` — spends one block for `phase` and returns `True`, or returns `False` when the env var is unset/empty or the phase has none left.
  - Budget file format: a JSON object `{critic phase: remaining blocks}`; the fake rewrites it (`sort_keys=True`) after each spent block, so `{"validate_spec": 1}` becomes `{"validate_spec": 0}`.

- [ ] **Step 1: Write the failing self-tests**

In `tests/e2e/test_fake_claude.py`, change the reducers import at line 23 from:

```python
from agent_manager.steps.reducers import review_gate
```

to:

```python
from agent_manager.steps.reducers import critic_blockers_gate, review_gate
```

Then append at the end of the file:

```python
def _critic_text(phase):
    """A critic brief, as `builtin/task.yaml` renders one, without its contract."""
    return (
        "# Critic\n\nstanding instructions\n\n"
        f"# phase: {phase}\n# role: critic\n"
        "\n## spec_path\ndocs/superpowers/specs/x-00000001.md\n"
    )


def _critic(phase, cwd):
    return fake_claude.build_result(
        phase, fake_claude.payload_from_schema(CRITIC_SCHEMA), _critic_text(phase), cwd
    )


def _arm_critic_blocks(tmp_path, monkeypatch, table):
    """Write the budget file and point the env var at it, as the e2e tests do."""
    budget = tmp_path / "critic-blocks.json"
    budget.write_text(json.dumps(table), encoding="utf-8")
    monkeypatch.setenv(fake_claude.CRITIC_BLOCKS_ENV, str(budget))
    return budget


def test_the_critic_blocks_names_are_pinned():
    """`tests/e2e/test_production_wiring.py` re-declares these as literals; the
    script and the tests meet across a process boundary, like `RESOLVER_ENV`."""
    assert fake_claude.CRITIC_BLOCKS_ENV == "FAKE_CLAUDE_CRITIC_BLOCKS"
    assert fake_claude.CRITIC_BLOCK_REASON == (
        "fake-claude critic: the critic-blocks budget told this critic to block"
    )
    assert fake_claude.CRITIC_PHASES == ("validate_spec", "validate_plan")
    assert fake_claude.MAX_CRITIC_BLOCKS == 2


@pytest.mark.parametrize("value", [None, ""])
@pytest.mark.parametrize("phase", ["validate_spec", "validate_plan"])
def test_without_a_critic_blocks_budget_every_critic_passes(
    tmp_path, monkeypatch, phase, value
):
    """Unset or empty is today's behaviour exactly."""
    if value is None:
        monkeypatch.delenv(fake_claude.CRITIC_BLOCKS_ENV, raising=False)
    else:
        monkeypatch.setenv(fake_claude.CRITIC_BLOCKS_ENV, value)

    payload = _critic(phase, tmp_path)

    assert payload == {"blockers": False, "reason": None, "summary": fake_claude.SUMMARY}
    assert critic_blockers_gate(payload) is None


def test_a_budget_of_one_blocks_once_with_the_fixed_reason_then_passes(
    tmp_path, monkeypatch
):
    budget = _arm_critic_blocks(tmp_path, monkeypatch, {"validate_spec": 1})

    first = _critic("validate_spec", tmp_path)

    assert first == {
        "blockers": True,
        "reason": fake_claude.CRITIC_BLOCK_REASON,
        "summary": fake_claude.SUMMARY,
    }
    # The production gate turns it into a `validation` block carrying the reason.
    assert critic_blockers_gate(first) == {
        "blocked": "validation",
        "detail": fake_claude.CRITIC_BLOCK_REASON,
    }
    assert json.loads(budget.read_text(encoding="utf-8")) == {"validate_spec": 0}

    second = _critic("validate_spec", tmp_path)

    assert second["blockers"] is False
    assert second["reason"] is None


def test_a_budget_of_two_blocks_twice_then_passes(tmp_path, monkeypatch):
    budget = _arm_critic_blocks(tmp_path, monkeypatch, {"validate_plan": 2})

    verdicts = [_critic("validate_plan", tmp_path)["blockers"] for _ in range(3)]

    assert verdicts == [True, True, False]
    assert json.loads(budget.read_text(encoding="utf-8")) == {"validate_plan": 0}


def test_a_budget_for_one_critic_leaves_the_other_passing_and_the_file_untouched(
    tmp_path, monkeypatch
):
    budget = _arm_critic_blocks(tmp_path, monkeypatch, {"validate_plan": 1})
    before = budget.read_text(encoding="utf-8")

    payload = _critic("validate_spec", tmp_path)

    assert payload["blockers"] is False
    assert budget.read_text(encoding="utf-8") == before


@pytest.mark.parametrize(
    "raw",
    [
        "{not json",
        "[]",
        '"validate_spec"',
        '{"review": 1}',
        '{"validate_spec": 3}',
        '{"validate_spec": -1}',
        '{"validate_spec": "1"}',
        '{"validate_spec": true}',
        '{"validate_spec": 1.0}',
        '{"validate_spec": 1, "spec": 1}',
    ],
)
def test_a_malformed_critic_blocks_budget_stops_the_fake(tmp_path, monkeypatch, raw):
    """Review focus 4: a typo in a test must never read as "no block"."""
    budget = tmp_path / "critic-blocks.json"
    budget.write_text(raw, encoding="utf-8")
    monkeypatch.setenv(fake_claude.CRITIC_BLOCKS_ENV, str(budget))

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        _critic("validate_spec", tmp_path)

    assert fake_claude.CRITIC_BLOCKS_ENV in str(caught.value)
    assert budget.read_text(encoding="utf-8") == raw


def test_a_critic_blocks_env_naming_a_missing_file_stops_the_fake(tmp_path, monkeypatch):
    missing = tmp_path / "nowhere.json"
    monkeypatch.setenv(fake_claude.CRITIC_BLOCKS_ENV, str(missing))

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        _critic("validate_spec", tmp_path)

    assert fake_claude.CRITIC_BLOCKS_ENV in str(caught.value)
    assert str(missing) in str(caught.value)


def test_a_bad_critic_blocks_budget_makes_the_fake_process_exit_1(tmp_path, monkeypatch):
    """The `__main__` mapping, end to end: the child inherits the env (as it does
    under `launcher.run_direct`), refuses the budget, writes no result."""
    budget = tmp_path / "critic-blocks.json"
    budget.write_text('{"validate_spec": 9}', encoding="utf-8")
    monkeypatch.setenv(fake_claude.CRITIC_BLOCKS_ENV, str(budget))
    attempt = tmp_path / "runs" / "r1" / "card" / "validate_spec.1"
    attempt.mkdir(parents=True)
    result_path = attempt / "result.json"
    prompt_path = _brief(
        tmp_path, "validate_spec", "spec_critic", "\n## spec_path\nx.md\n",
        CRITIC_SCHEMA, result_path,
    )

    completed = _run_fake(prompt_path, tmp_path)

    assert completed.returncode == 1
    assert "FAKE_CLAUDE_CRITIC_BLOCKS" in completed.stderr
    assert not result_path.exists()


def test_a_blocking_critic_process_writes_the_blocked_result(tmp_path, monkeypatch):
    """The whole script, driven by a brief plus the env switch: the brief says
    nothing about blocking, the budget alone decides (Rule 4)."""
    budget = _arm_critic_blocks(tmp_path, monkeypatch, {"validate_spec": 1})
    attempt = tmp_path / "runs" / "r1" / "card" / "validate_spec.1"
    attempt.mkdir(parents=True)
    result_path = attempt / "result.json"
    prompt_path = _brief(
        tmp_path, "validate_spec", "spec_critic", "\n## spec_path\nx.md\n",
        CRITIC_SCHEMA, result_path,
    )

    completed = _run_fake(prompt_path, tmp_path)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["blockers"] is True
    assert payload["reason"] == fake_claude.CRITIC_BLOCK_REASON
    assert json.loads(budget.read_text(encoding="utf-8")) == {"validate_spec": 0}
```

- [ ] **Step 2: Run the self-tests to verify they fail**

Run: `uv run pytest tests/e2e/test_fake_claude.py -v`
Expected: the new tests FAIL with `AttributeError: module 'e2e_fake_claude' has no attribute 'CRITIC_BLOCKS_ENV'` (or `CRITIC_BLOCK_REASON`); `test_without_a_critic_blocks_budget_every_critic_passes` FAILS the same way at `setenv`/`delenv`; every pre-existing test still PASSES.

- [ ] **Step 3: Implement the critic-blocks mode in the fake**

In `tests/e2e/fake_claude.py`, replace the module docstring's lines 12-24 (from `fail, because that failure is the test's whole point. There are exactly four` through `` `## conflict_files` and nowhere else. ``) with:

```text
fail, because that failure is the test's whole point. There are exactly five
test-controlled inputs, and none tells the fake anything the brief owns:
`REVIEW_FAIL_MARKER`, a file in the repo's git common dir that the fake finds
from its own cwd and compares with the brief's `## branch`;
`IMPLEMENT_EDITS_MARKER`, a JSON file beside it giving the files an implement
writes for the brief's `## branch`; the implement-only rendezvous
(`RENDEZVOUS_DIR_ENV` / `RENDEZVOUS_COUNT_ENV`), which only makes implement
wait for other lanes and changes nothing it writes; `RESOLVER_ENV`, which
only makes the resolve phase leave the merge it was given unfinished while
still claiming `resolved`, so git has to catch the lie; and
`CRITIC_BLOCKS_ENV`, a budget file that makes a critic block a set number of
times with a fixed reason. The resolve phase learns the tip and the
conflicting files from the brief's `## merge_tip` and `## conflict_files` and
nowhere else.
```

Then insert, directly after the `rendezvous` function (after line 321, before `_common_dir_file`):

```python
CRITIC_BLOCKS_ENV = "FAKE_CLAUDE_CRITIC_BLOCKS"
"""Test scaffolding, never in a brief: the path of a JSON budget of critic blocks.

Unset or empty means no critic ever blocks -- today's behaviour exactly. Set,
it names a file the test wrote, mapping a critic phase (one of
`CRITIC_PHASES`) to how many more times it must block, from 0 to
`MAX_CRITIC_BLOCKS`. Each block spends one and writes the file back, so a
count of 1 blocks once and then passes, and 2 blocks twice. Which critic
blocks, and how often, comes from this file alone, never from the brief
(Rule 4); the whole file is checked on every critic call, so a typo stops the
fake instead of reading as "no block"."""

CRITIC_PHASES = ("validate_spec", "validate_plan")
"""The two critic phases of `builtin/task.yaml`, the only keys a budget may name."""

MAX_CRITIC_BLOCKS = 2
"""The highest count a budget may give: one loop plus the block that escalates."""

CRITIC_BLOCK_REASON = (
    "fake-claude critic: the critic-blocks budget told this critic to block"
)
"""The `reason` of every blocked critic result. Fixed, so a test can find it in
the looped-to phase's `## feedback` and in the escalation detail."""


def _critic_budget(path):
    """The budget file's table, refused whole unless every entry is well formed."""
    if not path.is_file():
        raise FakeClaudeError(f"{CRITIC_BLOCKS_ENV} names {path}, which is not a file")
    try:
        table = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise FakeClaudeError(
            f"{CRITIC_BLOCKS_ENV} names {path}, which is not valid JSON: {error}"
        ) from None
    if not isinstance(table, dict):
        raise FakeClaudeError(
            f"{CRITIC_BLOCKS_ENV} names {path}, which is not a JSON object of "
            "critic phases"
        )
    for name, count in table.items():
        if name not in CRITIC_PHASES:
            raise FakeClaudeError(
                f"{CRITIC_BLOCKS_ENV} names {path}, whose key {name!r} is not a "
                f"critic phase (one of {list(CRITIC_PHASES)})"
            )
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or not 0 <= count <= MAX_CRITIC_BLOCKS
        ):
            raise FakeClaudeError(
                f"{CRITIC_BLOCKS_ENV} names {path}, which gives {name!r} {count!r} "
                f"blocks; a count is a whole number from 0 to {MAX_CRITIC_BLOCKS}"
            )
    return table


def critic_blocks(phase):
    """`True`, spending one block, when the budget says `phase` must block now.

    A no-op returning `False` unless `CRITIC_BLOCKS_ENV` is set. The file is
    only rewritten when a block is spent.
    """
    raw = os.environ.get(CRITIC_BLOCKS_ENV, "")
    if raw == "":
        return False
    path = Path(raw)
    table = _critic_budget(path)
    remaining = table.get(phase, 0)
    if remaining == 0:
        return False
    table[phase] = remaining - 1
    path.write_text(json.dumps(table, sort_keys=True), encoding="utf-8")
    return True
```

Then in `build_result`, replace lines 538-539:

```python
    if phase in ("validate_spec", "validate_plan"):
        return override(payload, blockers=False, reason=None, summary=SUMMARY)
```

with:

```python
    if phase in CRITIC_PHASES:
        # Test scaffolding: the budget, never the brief, says whether to block.
        if critic_blocks(phase):
            return override(
                payload, blockers=True, reason=CRITIC_BLOCK_REASON, summary=SUMMARY
            )
        return override(payload, blockers=False, reason=None, summary=SUMMARY)
```

- [ ] **Step 4: Run the self-tests to verify they pass**

Run: `uv run pytest tests/e2e/test_fake_claude.py -v`
Expected: PASS, all tests (new and pre-existing).

- [ ] **Step 5: Run the full suite to confirm no-trigger behaviour is unchanged**

Run: `uv run pytest`
Expected: PASS. With `FAKE_CLAUDE_CRITIC_BLOCKS` unset nothing else changed, so every e2e run, the golden briefs and the parity baseline stay green.

- [ ] **Step 6: Commit**

```bash
git add tests/e2e/fake_claude.py tests/e2e/test_fake_claude.py
git commit -m "test(e2e): fake claude critic-blocks budget"
```

---

### Task 2: Critics loop back once before escalating

**Files:**
- Modify: `src/agent_manager/workflow/task.py:1-15` (docstring), `:23` (import), `:55-86` (`spec`, `validate_spec`, `plan`, `validate_plan`)
- Modify: `tests/workflow/test_declared.py:13` (import), `:19-24` (`_shipped`), append new test
- Modify: `tests/e2e/test_production_wiring.py:11-20` (imports), append helpers and scenarios

**Interfaces:**
- Consumes: from Task 1, the env var `"FAKE_CLAUDE_CRITIC_BLOCKS"`, the budget-file format `{critic phase: remaining}`, and the reason `"fake-claude critic: the critic-blocks budget told this critic to block"` (re-declared here as literals because `tests/e2e/fake_claude.py` is not importable under `--import-mode=importlib`; Task 1's `test_the_critic_blocks_names_are_pinned` pins the other side). From the base: `agent_manager.workflow.phases.Goto(phase: str, max_loops: int = 1)` (frozen dataclass), `prompt.FEEDBACK_TITLE == "Feedback from review"`, `cli.app`, `cli.EXIT_ESCALATED == 1`, conftest fixtures `milestone_board` (function-scoped, `root`, `subtasks["A"][0]` is a1 with parent story A), `fake_claude_bin`, `read_fake_log(run_id) -> list[dict]` (entries `{"phase", "cwd", "result_path"}`), `agent_attempts` (module-scoped, per `engine`).
- Produces: `TASK.phase("validate_spec").on_fail == Goto("spec", 1)`, `TASK.phase("validate_plan").on_fail == Goto("plan", 1)`, `"feedback"` last in `spec`/`plan` inputs.

- [ ] **Step 1: Write the failing declared-data test and update `_shipped()`**

In `tests/workflow/test_declared.py`, change line 13 from:

```python
from agent_manager.workflow.phases import AgentPhase, Step, from_loader
```

to:

```python
from agent_manager.workflow.phases import AgentPhase, Goto, Step, from_loader
```

Replace `_shipped` (lines 19-24) with:

```python
DECLARED_ONLY_INPUTS = ("feedback",)
"""Inputs the YAML never had: the critic's reason reaches a looped-to phase
through `feedback`, which only the pygents engine supplies (G4)."""


def _declared_only(converted, mine):
    """`converted` with the data the YAML never had taken from `mine`.

    Timeouts, `on_fail` loops and the `feedback` input are new in the declared
    workflow. Each is copied from the declared phase; an extra input is
    appended only where the declared phase has it, so an input the YAML does
    have can never be hidden this way.
    """
    extra = tuple(
        name for name in DECLARED_ONLY_INPUTS
        if name in mine.inputs and name not in converted.inputs
    )
    return type(converted)(**{
        **converted.__dict__,
        "timeout": mine.timeout,
        "on_fail": mine.on_fail,
        "inputs": converted.inputs + extra,
    })


def _shipped(name, like):
    converted = from_loader(load_builtin(name, default_registry()))
    return type(converted)(converted.name, tuple(
        p if not hasattr(p, "timeout") else _declared_only(p, like.phase(p.name))
        for p in converted.phases))
```

Append at the end of the file:

```python
def test_task_critics_loop_back_once():
    """G4: each critic loops back to the phase it judged, at most once; review
    does not loop; only the looped-to phases read `feedback`."""
    assert TASK.phase("validate_spec").on_fail == Goto("spec", 1)
    assert TASK.phase("validate_plan").on_fail == Goto("plan", 1)
    assert TASK.phase("review").on_fail is None
    agents = [p for p in TASK.phases if isinstance(p, AgentPhase)]
    assert {p.name for p in agents if p.on_fail is not None} == {
        "validate_spec",
        "validate_plan",
    }
    assert {p.name for p in agents if "feedback" in p.inputs} == {"spec", "plan"}
    assert TASK.phase("spec").inputs == ("card", "explore", "spec_path", "feedback")
    assert TASK.phase("plan").inputs == ("spec_path", "plan_path", "feedback")


def test_integrate_declares_no_loop_and_no_feedback():
    """This card turns on TASK's critics only."""
    agents = [p for p in INTEGRATE.phases if isinstance(p, AgentPhase)]
    assert all(p.on_fail is None for p in agents)
    assert all("feedback" not in p.inputs for p in agents)
```

- [ ] **Step 2: Write the failing e2e scenarios**

In `tests/e2e/test_production_wiring.py`, replace the imports (lines 11-20):

```python
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from agent_manager import board, prompt, results
from agent_manager.steps import docs_commit
from agent_manager.workflow import load_builtin
```

with:

```python
import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_manager import board, cli, prompt, results
from agent_manager.steps import docs_commit
from agent_manager.workflow import load_builtin
```

Append at the end of the file:

```python
CRITIC_BLOCKS_ENV = "FAKE_CLAUDE_CRITIC_BLOCKS"
"""Must equal `fake_claude.CRITIC_BLOCKS_ENV`, which `test_fake_claude.py` pins."""

CRITIC_BLOCK_REASON = (
    "fake-claude critic: the critic-blocks budget told this critic to block"
)
"""Must equal `fake_claude.CRITIC_BLOCK_REASON`, which `test_fake_claude.py` pins."""

VERIFY = "git rev-parse --verify HEAD"
"""Must equal the conftest's `VERIFY_COMMANDS[0]`."""

FEEDBACK_SECTION = "\n## feedback\n"
"""How `prompt._assemble` heads the `feedback` input's section."""


def _arm_critic_blocks(tmp_path: Path, monkeypatch, table: dict[str, int]) -> Path:
    """Write the fake's critic budget beside the repo and point the env var at it.

    Through the test's own function-scoped `monkeypatch`, so it is undone when
    the test ends and never reaches the shared `completed_run`. Child processes
    inherit it: `run_direct` calls `Popen` with no `env=`."""
    budget = tmp_path / "critic-blocks.json"
    budget.write_text(json.dumps(table), encoding="utf-8")
    monkeypatch.setenv(CRITIC_BLOCKS_ENV, str(budget))
    return budget


def _run_one_card(root: Path, card: str, engine: str):
    """`am run --card` through `CliRunner`, with no runner_factory anywhere."""
    return CliRunner().invoke(
        cli.app,
        [
            "run",
            "--card",
            card,
            "--repo-dir",
            str(root),
            "--base-branch",
            "main",
            "--branch-prefix",
            "m1",
            "--engine",
            engine,
            "--verify",
            VERIFY,
        ],
    )


def _envelope(result) -> dict:
    envelope = json.loads(result.stdout)
    assert set(envelope) == {"ok", "data"}, envelope
    assert envelope["ok"] is True, envelope
    return envelope["data"]


def _brief_of(entry: dict) -> str:
    """The brief the fake was given for one logged dispatch: `prompt.txt` sits
    beside the attempt's `result.json`."""
    return (Path(entry["result_path"]).parent / "prompt.txt").read_text(encoding="utf-8")


def _feedback_of(brief: str) -> str | None:
    """The body of the brief's `## feedback` section, or `None` when it has none."""
    start = brief.find(FEEDBACK_SECTION)
    if start < 0:
        return None
    body = brief[start + len(FEEDBACK_SECTION) :]
    end = body.find("\n## ")
    return body if end < 0 else body[:end]


@pytest.mark.parametrize(
    ("critic", "author", "walked"),
    [
        (
            "validate_spec",
            "spec",
            [
                "explore", "spec", "validate_spec", "spec", "validate_spec",
                "plan", "validate_plan", "implement", "review",
            ],
        ),
        (
            "validate_plan",
            "plan",
            [
                "explore", "spec", "validate_spec", "plan", "validate_plan",
                "plan", "validate_plan", "implement", "review",
            ],
        ),
    ],
)
def test_a_critic_that_blocks_once_loops_back_and_the_run_finishes_done(
    milestone_board, fake_claude_bin, read_fake_log, tmp_path, monkeypatch,
    critic, author, walked,
):
    """Spec test 4 (and G4's "validate_plan loops back to plan in the same way"):
    under pygents one block sends the run back to the phase the critic judged,
    whose second brief carries the critic's reason, and the run ends done."""
    root = milestone_board["root"]
    card = milestone_board["subtasks"]["A"][0]
    budget = _arm_critic_blocks(tmp_path, monkeypatch, {critic: 1})

    result = _run_one_card(root, card, "pygents")

    assert result.exit_code == 0, (result.output, result.exception)
    data = _envelope(result)
    assert data["status"] == "done", (data["failed_phase"], data["detail"])
    assert board.show(card, repo_dir=root).status == "done"
    # Non-vacuity: the block really was spent, so the loop really happened.
    assert json.loads(budget.read_text(encoding="utf-8")) == {critic: 0}

    entries = read_fake_log(data["run_id"])
    assert [entry["phase"] for entry in entries] == walked
    first, second = [entry for entry in entries if entry["phase"] == author]
    assert Path(first["result_path"]).parent.name == f"{author}.1"
    assert Path(second["result_path"]).parent.name == f"{author}.2"

    feedback = _feedback_of(_brief_of(second))
    assert feedback is not None, _brief_of(second)
    assert feedback.splitlines()[0] == prompt.FEEDBACK_TITLE
    assert f"- {critic}: " in feedback
    assert CRITIC_BLOCK_REASON in feedback
    # Review focus 5: feedback reaches the looped-to phase's second brief only.
    for entry in entries:
        if entry is not second:
            assert _feedback_of(_brief_of(entry)) is None, entry


def test_a_critic_that_blocks_twice_escalates_validation_at_the_critic(
    milestone_board, fake_claude_bin, read_fake_log, tmp_path, monkeypatch
):
    """Spec test 5: the one loop is spent, so the second block escalates at the
    critic -- never at the looped-to phase -- the way it does today."""
    root = milestone_board["root"]
    card = milestone_board["subtasks"]["A"][0]
    budget = _arm_critic_blocks(tmp_path, monkeypatch, {"validate_spec": 2})

    result = _run_one_card(root, card, "pygents")

    assert result.exit_code == cli.EXIT_ESCALATED == 1, (result.output, result.exception)
    data = _envelope(result)
    assert data["status"] == "escalated"
    assert data["failed_phase"] == "validate_spec"
    assert "blocked=validation" in data["detail"], data["detail"]
    assert CRITIC_BLOCK_REASON in data["detail"], data["detail"]
    assert json.loads(budget.read_text(encoding="utf-8")) == {"validate_spec": 0}
    assert [entry["phase"] for entry in read_fake_log(data["run_id"])] == [
        "explore", "spec", "validate_spec", "spec", "validate_spec",
    ]
    assert board.show(card, repo_dir=root).status != "done"


def test_under_yaml_a_critic_that_blocks_once_escalates_at_once(
    milestone_board, fake_claude_bin, read_fake_log, tmp_path, monkeypatch
):
    """Spec test 6: `--engine yaml` never reads `on_fail`; the first block
    escalates, and `spec` is dispatched once. Documented, not changed."""
    root = milestone_board["root"]
    card = milestone_board["subtasks"]["A"][0]
    budget = _arm_critic_blocks(tmp_path, monkeypatch, {"validate_spec": 1})

    result = _run_one_card(root, card, "yaml")

    assert result.exit_code == cli.EXIT_ESCALATED == 1, (result.output, result.exception)
    data = _envelope(result)
    assert data["status"] == "escalated"
    assert data["failed_phase"] == "validate_spec"
    assert "blocked=validation" in data["detail"], data["detail"]
    assert CRITIC_BLOCK_REASON in data["detail"], data["detail"]
    assert json.loads(budget.read_text(encoding="utf-8")) == {"validate_spec": 0}
    assert [entry["phase"] for entry in read_fake_log(data["run_id"])] == [
        "explore", "spec", "validate_spec",
    ]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "runtime/compile.py carries one loop counter along the whole walk, so a "
        "spec loop leaves validate_plan at loop=1 and its first block escalates; "
        "G4 says each critic loops at most once. A runtime/ fix, outside card "
        "058981d3's files."
    ),
)
def test_both_critics_blocking_once_each_should_both_loop(
    milestone_board, fake_claude_bin, read_fake_log, tmp_path, monkeypatch
):
    """Review focus 1: pinned as a known gap, loud the day it is fixed."""
    root = milestone_board["root"]
    card = milestone_board["subtasks"]["A"][0]
    _arm_critic_blocks(tmp_path, monkeypatch, {"validate_spec": 1, "validate_plan": 1})

    result = _run_one_card(root, card, "pygents")

    data = _envelope(result)
    assert data["status"] == "done", (data["failed_phase"], data["detail"])


def test_with_no_critic_block_no_brief_carries_a_feedback_section(
    engine, completed_run, agent_attempts
):
    """Spec "No block" / review focus 3, on both engines: an empty `feedback`
    renders nothing, so a clean run's briefs are what they were before."""
    assert completed_run["status"] == "done", completed_run["detail"]
    for name in AGENT_PHASES:
        text = Path(agent_attempts[name].prompt_path).read_text(encoding="utf-8")
        assert FEEDBACK_SECTION not in text, (engine, name)
```

- [ ] **Step 3: Run the new tests to verify they fail for the right reason**

Run: `uv run pytest tests/workflow/test_declared.py -v`
Expected: `test_task_critics_loop_back_once` FAILS with `AssertionError` (`None == Goto(phase='spec', max_loops=1)`); every other test, including the updated `test_task_equals_the_shipped_yaml` and `test_integrate_declares_no_loop_and_no_feedback`, PASSES (the new `_shipped` copies `on_fail=None` and adds no input while `TASK` has none).

Run: `uv run pytest tests/e2e/test_production_wiring.py -k critic -v`
Expected:
- `test_a_critic_that_blocks_once_loops_back_and_the_run_finishes_done[validate_spec-...]` and `[validate_plan-...]` FAIL: `exit_code` is 1, not 0 (the critic's first block escalates).
- `test_a_critic_that_blocks_twice_escalates_validation_at_the_critic` FAILS on the phase list (`['explore', 'spec', 'validate_spec']`) or the budget (`{'validate_spec': 1}`).
- `test_under_yaml_a_critic_that_blocks_once_escalates_at_once` PASSES (characterises unchanged yaml behaviour).
- `test_both_critics_blocking_once_each_should_both_loop` XFAILS.
- `test_with_no_critic_block_no_brief_carries_a_feedback_section[yaml]` and `[pygents]` PASS.

- [ ] **Step 4: Declare the loops and the `feedback` input in `TASK`**

In `src/agent_manager/workflow/task.py`, replace the module docstring (lines 1-15) with:

```python
"""The builtin `task` workflow as declared phase-model data (spec G3).

Pinned to `builtin/task.yaml`: `tests/workflow/test_declared.py` asserts that
`TASK.digest()` equals the digest of the shipped YAML run through
`phases.from_loader`, so the two cannot drift apart silently. Every callable
is the real function object `registry.default_registry()` binds -- never a
registry lookup -- because the digest names callables by `module.qualname`.

Timeouts, the critics' `on_fail` loops and the `feedback` input are the data
the YAML never had; the pinning test copies each from here and nothing else.

G2 requires every agent turn timeout to exceed the launcher's strictly (the
launcher must kill `claude -p` before the turn is cancelled), so each phase
gets `max(chosen, floor)` with the floor five minutes above the launcher
timeout. The launcher's is never lowered.

G4: `validate_spec` loops back to `spec` and `validate_plan` to `plan`, at
most once each (`Goto`'s default `max_loops=1`); a second block escalates
`validation` as before. Review does not loop. The looped-to phase reads the
critic's reason through its `feedback` input; with no loop that input is empty
and renders no section, so a clean run's briefs are unchanged. Only the
pygents engine reads `on_fail`: `--engine yaml` walks `builtin/task.yaml` and
still escalates on the first block.

No pygents import here (rule 1).
"""
```

Change line 23 from:

```python
from agent_manager.workflow.phases import AgentPhase, Retry, Step, Workflow
```

to:

```python
from agent_manager.workflow.phases import AgentPhase, Goto, Retry, Step, Workflow
```

Replace the four phases at lines 55-86 with:

```python
    AgentPhase(
        "spec",
        role="spec_author",
        inputs=("card", "explore", "spec_path", "feedback"),
        result=results.SpecResult,
        writes="docs/superpowers/specs/{stem}.md",
        timeout=agent_timeout(30),
    ),
    AgentPhase(
        "validate_spec",
        role="spec_critic",
        inputs=("card", "spec_path"),
        result=results.CriticResult,
        gates=(reducers.critic_blockers_gate,),
        timeout=agent_timeout(20),
        on_fail=Goto("spec"),
    ),
    AgentPhase(
        "plan",
        role="planner",
        inputs=("spec_path", "plan_path", "feedback"),
        result=results.PlanResult,
        writes="docs/superpowers/plans/{stem}.md",
        timeout=agent_timeout(30),
    ),
    AgentPhase(
        "validate_plan",
        role="plan_critic",
        inputs=("spec_path", "plan_path"),
        result=results.CriticResult,
        gates=(reducers.critic_blockers_gate,),
        timeout=agent_timeout(20),
        on_fail=Goto("plan"),
    ),
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `uv run pytest tests/workflow/test_declared.py -v`
Expected: PASS, all tests, including `test_task_equals_the_shipped_yaml`, `test_both_validate` and `test_declared_modules_never_import_pygents`.

Run: `uv run pytest tests/e2e/test_production_wiring.py -v`
Expected: every test PASSES except `test_both_critics_blocking_once_each_should_both_loop`, which XFAILS (it now escalates at `validate_plan` rather than `validate_spec`, still an `AssertionError`). If it XPASSES, stop: strict xfail fails the run, and the runtime behaves differently from what this plan read in `runtime/compile.py:122-134`; report it rather than editing the marker.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: PASS (one xfail). In particular the golden-brief tests, `test_the_run_data_is_the_same_on_both_engines`, `tests/runtime/`, `tests/test_cli.py` and `tests/test_orchestrate.py` (which compute `TASK.digest()` at run time) stay green.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/workflow/task.py tests/workflow/test_declared.py tests/e2e/test_production_wiring.py
git commit -m "feat(workflow): critics loop back once before escalating"
```

---

## Self-review notes

- Spec coverage: `on_fail`/`feedback` declarations and docstring (Task 2 Step 4); spec tests 1-3 (Task 2 Step 1, `test_both_validate` and the pygents-import test unchanged); tests 4-6 (Task 2 Step 2, test 4 extended to `validate_plan` per "Observable behaviour"); test 7 (unchanged suite in Task 2 Step 6 plus an explicit guard test); test 8 and "Fake misconfiguration" (Task 1); "Second critic failure escalates at the critic" (test 5 asserts `failed_phase == "validate_spec"` and the phase list). The escalation kind `validation` is observed as `blocked=validation` in `detail`, which is how `dispatch.evaluate_gates` renders `critic_blockers_gate`'s verdict.
- Out-of-scope finding: Review Focus 1 (shared loop counter in `runtime/compile.py`) is a runtime gap, not fixed here; it is pinned with a strict xfail and should be raised with the owner of `runtime/`.
