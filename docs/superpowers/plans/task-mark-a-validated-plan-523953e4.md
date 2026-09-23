<!-- task-pipeline: validated -->
# Mark a validated plan deterministically (card 523953e4)

Parent story: 695b0514 "Own the commit trail: validated marker, docs commit and the Plan-Hash trailer" (milestone 7aa00a90).

## Why

Today nothing on the pipeline writes `VALIDATED_MARKER` into a plan. `plan_check.find_validated_plan` can only ever answer `validated: false`, so the `plan_check` → `skip_to: implement` shortcut (design §5, lines 169-173) is dead code on a re-run, and the Plan-Hash trailer the siblings add would be computed over a file that does not record that Validate signed it. This card adds the one deterministic step that stamps the marker, and wires it into the builtin document between `validate_plan` and `implement`. It is the first of three: sibling ba15da20 commits the spec and plan with the `Plan-Hash:` trailer, sibling f26b377d feeds the hash to the coder.

## Scope

In scope: one new function `plan_check.mark_validated`, its registry binding, one new phase in `builtin/task.yaml`, and the tests for both.

Explicitly out of scope, owned by siblings or by other cards: computing or writing the plan hash, the docs commit, `git` of any kind, `prompt.py`, `roles/`, `fake_claude.py`, `tests/e2e/`, milestone orchestration, parallel stories, integrate, non-Claude harnesses, ancestor roll-up, and counting reviews with git. This card commits nothing and runs no subprocess.

## Observable behaviour

### `plan_check.mark_validated`

Lives in `src/agent_manager/steps/plan_check.py`, beside the constant it uses. The module's contract holds unchanged: filesystem only — no git, no `brd`, no subprocess, no model. Its only I/O is the plan file.

Signature: `mark_validated(plan_path: str | Path, worktree: object | None = None) -> dict[str, object]`.

Both parameter names are bound from the engine context by name. `bind_arguments` (engine.py:163) passes only parameters the function declares, taking them from the context overlaid with the phase's `args:`; the phase therefore needs no `args:`. `plan_path` is in the context because `_document_paths` (engine.py:120) expands the `plan` phase's `writes:` template once at subtask start, for every `plan_path` declared as an input by some `AgentPhase` (`implement`, `validate_plan` and `review` all do). `prompt.expand_writes` returns a **repo-relative** POSIX path, so the step roots a relative `plan_path` at `worktree` — the reserved context key `subtask_context` always supplies — exactly as `verify.run_suite(commands, worktree, ...)` does. An absolute `plan_path` is used as given and `worktree` is then irrelevant; `worktree` defaults to `None` so unit tests can pass a real `tmp_path` file directly.

Behaviour:

- Reads the plan as UTF-8 and writes it back as UTF-8, explicitly, matching `read_file` (plan_check.py:110). No `encoding=None` platform default anywhere.
- If `VALIDATED_MARKER` already appears in the text, the file is left **byte-identical** — not rewritten, not re-terminated, not re-marked. Containment is the same literal-substring test `find_validated_plan` uses, never a regex; the module docstring already accepts that a plan whose prose discusses the marker reads as validated.
- Otherwise it appends the marker as its own final line: if the existing text is non-empty and does not end with `\n`, one `\n` is added first, then `VALIDATED_MARKER` and a trailing `\n`. An empty plan becomes exactly the marker line.
- The literal `<!-- task-pipeline: validated -->` is never re-typed. The existing `VALIDATED_MARKER` constant (plan_check.py:21) is the single source, so the writer and `find_validated_plan`'s reader cannot drift.
- Returns a plain dict (`CLAUDE.md`: Pydantic only at process boundaries; deterministic steps return plain dicts per design §6) of the shape `{"path": <path used, str>, "appended": <bool>}`. It **must** be a Mapping or `_run_deterministic` fails the phase (engine.py:494). The engine folds it into the context under the phase name `mark_validated`.

### The `mark_validated` phase

`src/agent_manager/workflow/builtin/task.yaml` gains, immediately after `validate_plan` and immediately before `implement`:

```yaml
  - name: mark_validated
    kind: deterministic
    run: plan_check.mark_validated
```

No `args:`, no `gates:`, no `best_effort:`. It is not best-effort on purpose: an unmarked plan makes the next run re-plan and makes the sibling's Plan-Hash trailer meaningless, so a failure here must escalate like any other deterministic phase.

It must not run when `validate_plan` blocked, and placement alone achieves that. `validate_plan` is an agent phase gated by `critic_blockers_gate`; a non-retryable gate failure raises `AgentPhaseFailed`, and the walk in `run_subtask` returns `_escalate(...)` before ever reaching the next index (engine.py:396-402). No extra guard is added here, and none should be.

### Registry

`src/agent_manager/workflow/registry.py`: `registry.register("plan_check.mark_validated", plan_check.mark_validated)` goes in the "Deterministic steps that already ship on this branch" block of `default_registry()` — the real imported callable, not a placeholder, so `resolve(name) is the_function`. The name is added to `BUILTIN_FUNCTION_NAMES` in sorted position, between `plan_check.has_validated_plan` and `plan_hash_gate`.

## Error paths

- Plan file missing, or unreadable: the `OSError` propagates. `_run_deterministic` is deliberately total (engine.py:509), records the phase `failed` with the rendered error and escalates the subtask. The step adds no `try` of its own and invents no "nothing to mark" success — a silent pass would hand the sibling a hash over a file that was never marked.
- Plan not valid UTF-8: `UnicodeDecodeError` propagates the same way. (`find_validated_plan` catches it because *its* answer is "re-plan"; here there is no benign answer.)
- `plan_path` relative and `worktree` absent or empty: raise `ValueError` naming both, in the style of `_plans_dir` (plan_check.py:92) and `verify._required_worktree`, rather than writing a marker into a file resolved against whatever the process CWD happens to be.
- A `plan_path` the context does not carry (a workflow document whose agent phases never declare `plan_path`): `bind_arguments` already raises `EngineError` naming the phase, the function and the parameter. Nothing new is needed.

## Test list

Tiering follows `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §14 (line 477): Steps are tested against temporary directories/files with no network; pure functions and workflow documents are unit-tested; the Engine tier uses a fake adapter; end-to-end is one slow opt-in test. No test here belongs to the engine or e2e tier — `tests/e2e/` does not exist on this branch and arrives with sibling ba15da20 — and the whole default suite (`uv run pytest`) must stay green.

**Steps tier — `tests/steps/test_plan_check.py`, real files under `tmp_path`, no fake filesystem** (the module docstring already states this placement):

1. Marking a plan with no marker appends it once: the text ends with `VALIDATED_MARKER + "\n"`, the marker occurs exactly once, the original body is still a prefix, and the result is `{"path": ..., "appended": True}`.
2. A second call on the same file leaves the bytes unchanged (compare `read_bytes()` before and after) and returns `"appended": False`; the marker still occurs exactly once.
3. A plan with no trailing newline gains one before the marker — the marker is on its own line, and the last body character is not glued to `<!--`.
4. After marking, `find_validated_plan` for the same card reports `found: True` and `validated: True` (plan file named so `matches_card` accepts it, listed from a real `tmp_path` plans directory).
5. A relative `plan_path` is rooted at `worktree`: the file inside `tmp_path/worktree/docs/superpowers/plans/...` is the one that gets the marker.
6. A relative `plan_path` with no `worktree` raises `ValueError` and writes nothing.
7. A missing plan file raises `OSError`/`FileNotFoundError` (the step does not swallow it).

**Pure/document tier — `tests/workflow/test_builtin_task.py`:**

8. `EXPECTED_PHASES` gains `("mark_validated", "deterministic")` between `validate_plan` and `implement`, giving **13** entries; `test_builtin_task_has_the_twelve_phases_in_spec_order` is renamed to say thirteen.
9. A new document test: the phase's `run` is `plan_check.mark_validated` with no `args`, and its index is greater than `validate_plan`'s and less than `implement`'s.
10. `test_every_resolved_function_is_the_registry_binding` must keep passing unchanged — it asserts the document's names and `default_registry()` names match exactly, so it is the drift guard for the registry binding and the YAML edit together.

**Registry tier — `tests/workflow/test_registry.py`:** its existing assertions over `BUILTIN_FUNCTION_NAMES` and `default_registry().names()` cover the new entry; add `"plan_check.mark_validated"` to the pinned `TASK_YAML_NAMES` tuple (between `plan_check.has_validated_plan` and `plan_hash_gate`, sorted), and add an assertion to `test_default_registry_resolves_implemented_steps_to_the_real_callables` that `registry.resolve("plan_check.mark_validated") is plan_check.mark_validated`. Engine references above (`engine.py`) mean `src/agent_manager/engine.py`.

## Verification

`uv run pytest` (full suite). There is no lint or typecheck command.

---

# Mark a validated plan deterministically — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one filesystem-only deterministic step, `plan_check.mark_validated`, that stamps `VALIDATED_MARKER` onto a plan Validate signed, and wire it into `builtin/task.yaml` between `validate_plan` and `implement`.

**Architecture:** The step is a new function in the existing `src/agent_manager/steps/plan_check.py`, reusing the module's `VALIDATED_MARKER` constant so the writer and `find_validated_plan`'s reader cannot drift. It reads the plan as UTF-8, and if the marker is not already in the text it opens the file in append mode and writes (optionally a `\n` first, then) the marker plus a newline — appending rather than rewriting keeps an already-marked or untouched-prefix file byte-identical and performs no newline translation on existing content. The engine binds `plan_path` and `worktree` by parameter name out of the subtask context (`engine.bind_arguments`), so the YAML phase needs no `args:`. Nothing else in the package is touched.

**Tech Stack:** Python 3, `pathlib`, pytest with `tmp_path`, PyYAML-loaded workflow documents, `uv` for running the suite.

**Spec:** `docs/superpowers/specs/task-mark-a-validated-plan-523953e4-design.md` (reproduced verbatim above)

## Global Constraints

- Branch `m2/task-mark-a-validated-plan-523953e4`, worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/m2/task-mark-a-validated-plan-523953e4`, cut fresh from `origin/m2/task-add-the-opt-in-real-34d3388b`. No other subtask's code exists here.
- Only these six files may be edited: `src/agent_manager/steps/plan_check.py`, `src/agent_manager/workflow/registry.py`, `src/agent_manager/workflow/builtin/task.yaml`, plus the three test files named in the tasks below. Do not touch `prompt.py`, `roles/`, `fake_claude.py`, `engine.py`, or create `tests/e2e/`.
- `plan_check.py` stays filesystem-only: no git, no `brd`, no subprocess, no model call, no network.
- The literal `<!-- task-pipeline: validated -->` is typed exactly once in source, in the existing `VALIDATED_MARKER` constant at `plan_check.py:21`. Every other use references the constant. (The existing test `test_the_marker_is_the_exact_literal_validate_writes` is the one place the literal is re-typed, and it stays as-is.)
- All file reads and writes pass `encoding="utf-8"` explicitly. Never rely on the platform default.
- Deterministic steps return plain dicts (`CLAUDE.md`: Pydantic only at process boundaries; design §6). The return value must be a Mapping or `engine._run_deterministic` fails the phase.
- The `mark_validated` phase has no `args:`, no `gates:`, no `best_effort:`, no `when:`, no `skip_to:`.
- The full default suite `uv run pytest` must be green at every commit.

## Review Focus

- A plan whose prose merely *mentions* the marker mid-document: substring containment says validated, so the file must be left byte-identical and never gain a second marker. → Task 1, Step 9.
- An empty plan file (zero bytes): must become exactly the marker line with no leading blank line. → Task 1, Step 9.
- A plan that is not valid UTF-8: `UnicodeDecodeError` must propagate and the file must be left byte-identical, with no partial append. → Task 1, Step 9.
- An absolute `plan_path` while a `worktree` is also bound from the context (the real engine case once paths are absolute): the absolute path wins and `worktree` is ignored, rather than being joined into a path that does not exist. → Task 1, Step 9.
- The engine actually binding this step: a repo-relative `plan_path` plus the reserved `worktree` key out of a real `engine.subtask_context` must bind by name with no `args:`, or the phase dies at runtime while every unit test still passes. → Task 2, Step 5.

## File Structure

- `src/agent_manager/steps/plan_check.py` — **modify**. Gains a private `_resolved_plan_path` helper and the public `mark_validated`, appended after `has_validated_plan`. Responsibility unchanged: answer and now also record whether a card's plan is validated, filesystem only.
- `src/agent_manager/workflow/registry.py` — **modify**. One new entry in `BUILTIN_FUNCTION_NAMES` and one new `registry.register(...)` line in `default_registry()`.
- `src/agent_manager/workflow/builtin/task.yaml` — **modify**. One new three-line phase between `validate_plan` and `implement`.
- `tests/steps/test_plan_check.py` — **modify**. All behaviour tests for `mark_validated`, against real files under `tmp_path` (Steps tier, design §14 line 477).
- `tests/workflow/test_builtin_task.py` — **modify**. `EXPECTED_PHASES`, the renamed count test, and the new ordering test (pure/document tier).
- `tests/workflow/test_registry.py` — **modify**. `TASK_YAML_NAMES`, the real-callable assertion, and the binding test (pure tier).

Two tasks, not three: registering the name without adding the YAML phase would break the existing `test_every_resolved_function_is_the_registry_binding` (it asserts `sorted(workflow.functions) == sorted(registry.names())`), so the registry edit and the document edit must land in the same commit.

---

### Task 1: `plan_check.mark_validated`

**Files:**
- Modify: `src/agent_manager/steps/plan_check.py` (append after `has_validated_plan`, which ends at line 173)
- Test: `tests/steps/test_plan_check.py` (append at the end of the file)

**Interfaces:**
- Consumes: `VALIDATED_MARKER` (module constant, `plan_check.py:21`), `find_validated_plan` (existing, for the agreement test only).
- Produces: `plan_check.mark_validated(plan_path: str | Path, worktree: object | None = None) -> dict[str, object]`, returning `{"path": str, "appended": bool}`. Task 2 registers this exact callable under the name `"plan_check.mark_validated"`.

- [ ] **Step 1: Write the failing tests for appending, idempotence and the missing newline**

Append to the end of `tests/steps/test_plan_check.py`:

```python
# ── mark_validated ───────────────────────────────────────────────────────────
# Steps tier (design §14 line 477): real plan files under `tmp_path`, never a
# fake filesystem. The engine calls this step with the repo-relative `plan_path`
# `prompt.expand_writes` produced plus the reserved `worktree` context key.


def _plan_file(tmp_path: Path, body: str, name: str = "task-rows-a32af745.md") -> Path:
    """A real plan file on disk holding exactly `body` (no newline added)."""
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_marking_an_unmarked_plan_appends_the_marker_as_its_own_last_line(
    tmp_path: Path,
):
    plan = _plan_file(tmp_path, "# plan\n\nstep one\n")

    got = plan_check.mark_validated(plan)

    text = plan.read_text(encoding="utf-8")
    assert got == {"path": str(plan), "appended": True}
    assert text == f"# plan\n\nstep one\n{VALIDATED_MARKER}\n"
    assert text.count(VALIDATED_MARKER) == 1


def test_marking_a_plan_twice_leaves_the_bytes_identical(tmp_path: Path):
    # Re-entrancy (design §9): a resumed run re-enters the phase, and a second
    # marker would break the sibling's Plan-Hash over the same file.
    plan = _plan_file(tmp_path, "# plan\n")
    plan_check.mark_validated(plan)
    before = plan.read_bytes()

    got = plan_check.mark_validated(plan)

    assert got == {"path": str(plan), "appended": False}
    assert plan.read_bytes() == before
    assert plan.read_text(encoding="utf-8").count(VALIDATED_MARKER) == 1


def test_a_plan_with_no_trailing_newline_gains_one_before_the_marker(tmp_path: Path):
    plan = _plan_file(tmp_path, "# plan\nlast line with no newline")

    plan_check.mark_validated(plan)

    text = plan.read_text(encoding="utf-8")
    assert text == f"# plan\nlast line with no newline\n{VALIDATED_MARKER}\n"
    # The marker is never glued to the end of the body.
    assert "newline<!--" not in text
    assert text.splitlines()[-1] == VALIDATED_MARKER


def test_a_marked_plan_reads_back_as_validated_through_find_validated_plan(
    tmp_path: Path,
):
    # The whole point of the step: `plan_check` can now answer `validated: True`
    # on a re-run and take the `skip_to: implement` shortcut.
    directory = _plans(tmp_path, {"2026-09-task-rows-a32af745.md": "# plan\n"})

    plan_check.mark_validated(directory / "2026-09-task-rows-a32af745.md")

    assert plan_check.find_validated_plan("a32af745", directory) == {
        "found": True,
        "path": str(directory / "2026-09-task-rows-a32af745.md"),
        "validated": True,
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_plan_check.py -k mark_validated -v`
Expected: FAIL — `AttributeError: module 'agent_manager.steps.plan_check' has no attribute 'mark_validated'` on every one of the four tests.

- [ ] **Step 3: Write the minimal implementation**

Append to the end of `src/agent_manager/steps/plan_check.py`:

```python
def _resolved_plan_path(plan_path: object, worktree: object | None) -> Path:
    """The plan file to mark: absolute as given, else rooted at `worktree`.

    `prompt.expand_writes` hands the engine a repo-RELATIVE posix path, so a
    relative `plan_path` with no worktree would resolve against whatever the
    process CWD happens to be and stamp the marker into the wrong file (or
    create nothing anyone reads). That is a caller bug, raised up front in the
    style of `_plans_dir` above and `verify._required_worktree`.
    """
    text = "" if plan_path is None else str(plan_path).strip()
    if text == "":
        raise ValueError(
            f"plan_check.mark_validated needs a plan_path, got {plan_path!r}"
        )
    path = Path(text)
    if path.is_absolute():
        return path
    root = "" if worktree is None else str(worktree).strip()
    if root == "":
        raise ValueError(
            f"plan_check.mark_validated got the relative plan_path {text!r} and no "
            f"worktree to root it at (worktree={worktree!r})"
        )
    return Path(root) / path


def mark_validated(
    plan_path: str | Path, worktree: object | None = None
) -> dict[str, object]:
    """Record that Validate signed this plan, by appending `VALIDATED_MARKER`.

    The deterministic phase's result (design §6) -- a plain dict, since it
    crosses no process boundary and so needs no Pydantic model (`CLAUDE.md`).
    Filesystem only, like the rest of this module: the plan file is the only
    thing touched, and no hash is computed here (sibling ba15da20 owns that).

    Idempotent by literal containment, the same test `find_validated_plan`
    applies, so the writer and the reader can never disagree: an already-marked
    plan is left BYTE-IDENTICAL rather than rewritten, which is what makes a
    resumed run (design §9) safe. Appending instead of rewriting is deliberate
    -- it cannot re-encode or re-terminate a single existing byte.

    No `try` here on purpose: a missing, unreadable or non-UTF-8 plan must reach
    `engine._run_deterministic`, which is total, records the phase failed and
    escalates. A silent "nothing to mark" success would hand the sibling a
    Plan-Hash over a file that was never marked.
    """
    path = _resolved_plan_path(plan_path, worktree)
    text = path.read_text(encoding="utf-8")
    if VALIDATED_MARKER in text:
        return {"path": str(path), "appended": False}
    # `newline="\n"` so the two characters written are exactly the two intended,
    # on any platform; append mode so every byte already in the file survives.
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        if text and not text.endswith("\n"):
            handle.write("\n")
        handle.write(f"{VALIDATED_MARKER}\n")
    return {"path": str(path), "appended": True}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/steps/test_plan_check.py -k mark_validated -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Write the failing tests for path rooting and the missing file**

Append to the end of `tests/steps/test_plan_check.py`:

```python
def test_a_relative_plan_path_is_rooted_at_the_worktree(tmp_path: Path):
    # Exactly what the engine binds: `prompt.expand_writes` yields a
    # repo-relative posix path, and `worktree` is a reserved context key.
    worktree = tmp_path / "worktree"
    plans = worktree / "docs" / "superpowers" / "plans"
    plans.mkdir(parents=True)
    relative = "docs/superpowers/plans/task-rows-a32af745.md"
    (plans / "task-rows-a32af745.md").write_text("# plan\n", encoding="utf-8")
    decoy = tmp_path / "task-rows-a32af745.md"
    decoy.write_text("# decoy\n", encoding="utf-8")

    got = plan_check.mark_validated(relative, worktree)

    assert got == {"path": str(plans / "task-rows-a32af745.md"), "appended": True}
    assert VALIDATED_MARKER in (plans / "task-rows-a32af745.md").read_text(
        encoding="utf-8"
    )
    assert decoy.read_text(encoding="utf-8") == "# decoy\n"


@pytest.mark.parametrize("worktree", [None, "", "   "])
def test_a_relative_plan_path_without_a_worktree_is_a_caller_bug(
    tmp_path: Path, worktree: object
):
    # Never resolve against the process CWD: that stamps the marker into a file
    # nobody asked for, or creates one nobody reads.
    (tmp_path / "task-rows-a32af745.md").write_text("# plan\n", encoding="utf-8")

    with pytest.raises(ValueError, match="worktree"):
        plan_check.mark_validated("docs/superpowers/plans/x.md", worktree)

    assert (tmp_path / "task-rows-a32af745.md").read_text(encoding="utf-8") == "# plan\n"


def test_a_missing_plan_file_is_not_swallowed(tmp_path: Path):
    # `find_validated_plan` treats an unreadable plan as "re-plan"; here there is
    # no benign answer, so the engine's total handler must see the error.
    with pytest.raises(FileNotFoundError):
        plan_check.mark_validated(tmp_path / "nothing-here-a32af745.md")
```

- [ ] **Step 6: Run the tests to verify they fail**

Run: `uv run pytest tests/steps/test_plan_check.py -k "relative_plan_path or missing_plan_file" -v`
Expected: all of these already PASS, because Step 3's implementation covers rooting, the `ValueError` and the propagated `FileNotFoundError`. Confirm each listed test actually ran (4 relative-path/worktree cases plus the missing-file test = 5 tests).

Note for the implementer: if a test in this step already passes before Step 7, say so and move on — Step 3's implementation already covers it. Do not weaken the test to force a red.

- [ ] **Step 7: Run the whole steps tier to verify it passes**

Run: `uv run pytest tests/steps/test_plan_check.py -v`
Expected: PASS, all tests including the pre-existing ones.

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/steps/plan_check.py tests/steps/test_plan_check.py
git commit -m "feat: add plan_check.mark_validated, an idempotent validated-marker stamp"
```

- [ ] **Step 9: Write the Review Focus tests**

Append to the end of `tests/steps/test_plan_check.py`:

```python
def test_a_plan_whose_prose_mentions_the_marker_is_left_untouched(tmp_path: Path):
    # Review focus: containment is a literal substring test anywhere in the file
    # (see VALIDATED_MARKER's docstring), so a plan that merely discusses the
    # marker already reads as validated -- it must not be marked a second time.
    plan = _plan_file(
        tmp_path, f"# plan\n\nValidate appends `{VALIDATED_MARKER}` here.\n\nstep one\n"
    )
    before = plan.read_bytes()

    got = plan_check.mark_validated(plan)

    assert got == {"path": str(plan), "appended": False}
    assert plan.read_bytes() == before
    assert plan.read_text(encoding="utf-8").count(VALIDATED_MARKER) == 1


def test_an_empty_plan_becomes_exactly_the_marker_line(tmp_path: Path):
    # Review focus: no leading blank line -- `text` is empty, so no separator is
    # written, and the file is a single valid marker line.
    plan = _plan_file(tmp_path, "")

    got = plan_check.mark_validated(plan)

    assert got == {"path": str(plan), "appended": True}
    assert plan.read_text(encoding="utf-8") == f"{VALIDATED_MARKER}\n"


def test_a_plan_that_is_not_utf8_fails_loudly_and_is_left_untouched(tmp_path: Path):
    # Review focus: UnicodeDecodeError is a ValueError, not an OSError, so it
    # needs naming. It must escape to the engine's total handler, and nothing
    # may be appended to a file we could not read.
    plan = tmp_path / "task-rows-a32af745.md"
    plan.write_bytes(b"# plan\n\xff\xfe not utf-8\n")
    before = plan.read_bytes()

    with pytest.raises(UnicodeDecodeError):
        plan_check.mark_validated(plan)

    assert plan.read_bytes() == before


def test_an_absolute_plan_path_ignores_the_worktree(tmp_path: Path):
    # Review focus: the engine always binds `worktree`, so the absolute case has
    # to win rather than be joined into a path that does not exist.
    plan = _plan_file(tmp_path, "# plan\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    got = plan_check.mark_validated(plan, elsewhere)

    assert got == {"path": str(plan), "appended": True}
    assert VALIDATED_MARKER in plan.read_text(encoding="utf-8")
```

- [ ] **Step 10: Run the Review Focus tests**

Run: `uv run pytest tests/steps/test_plan_check.py -v`
Expected: PASS, all tests. If `test_a_plan_that_is_not_utf8_fails_loudly_and_is_left_untouched` fails because something was appended, the implementation is writing before reading — fix the order, do not change the test.

- [ ] **Step 11: Commit**

```bash
git add tests/steps/test_plan_check.py
git commit -m "test: pin mark_validated on prose mentions, empty plans, bad utf-8 and absolute paths"
```

---

### Task 2: Register the step and add the `mark_validated` phase

**Files:**
- Modify: `src/agent_manager/workflow/registry.py:210-222` (`BUILTIN_FUNCTION_NAMES`) and `:253-258` (the deterministic-steps block of `default_registry()`)
- Modify: `src/agent_manager/workflow/builtin/task.yaml:50-62` (between the `validate_plan` and `implement` phases)
- Test: `tests/workflow/test_registry.py`, `tests/workflow/test_builtin_task.py`

**Interfaces:**
- Consumes: `plan_check.mark_validated(plan_path: str | Path, worktree: object | None = None) -> dict[str, object]` from Task 1 — the real imported callable, so `default_registry().resolve("plan_check.mark_validated") is plan_check.mark_validated`.
- Produces: the name `"plan_check.mark_validated"` in `BUILTIN_FUNCTION_NAMES` and a `DeterministicPhase` named `mark_validated` in the loaded `task` workflow, at index 8 (after `validate_plan`, before `implement`).

The registry edit and the YAML edit land together: `test_every_resolved_function_is_the_registry_binding` asserts `sorted(workflow.functions) == sorted(default_registry().names())`, so either edit alone turns the suite red.

- [ ] **Step 1: Write the failing document tests**

In `tests/workflow/test_builtin_task.py`, replace the `EXPECTED_PHASES` tuple (lines 43-56) with:

```python
# Design spec lines 146-225, in file order.
EXPECTED_PHASES = (
    ("worktree", "deterministic"),
    ("explore", "agent"),
    ("mark_in_progress", "deterministic"),
    ("plan_check", "deterministic"),
    ("spec", "agent"),
    ("validate_spec", "agent"),
    ("plan", "agent"),
    ("validate_plan", "agent"),
    ("mark_validated", "deterministic"),
    ("implement", "agent"),
    ("review", "agent"),
    ("verify", "deterministic"),
    ("mark_done", "deterministic"),
)
```

Rename the count test (lines 66-68) to:

```python
def test_builtin_task_has_the_thirteen_phases_in_spec_order() -> None:
    workflow = load_builtin("task")
    assert tuple((phase.name, phase.kind) for phase in workflow.phases) == EXPECTED_PHASES
```

Then add, immediately after `test_plan_check_skips_forward_to_implement_when_a_plan_exists` (which ends at line 95):

```python
def test_mark_validated_stamps_the_plan_between_validation_and_implement() -> None:
    """Placement IS the guard: `validate_plan` is gated by
    `critic_blockers_gate`, and a non-retryable gate failure escalates the
    subtask out of the walk (engine.run_subtask) before this index is reached.
    So an unvalidated plan is never marked, with no extra logic here."""
    workflow = load_builtin("task")
    phase = workflow.phase("mark_validated")
    assert isinstance(phase, DeterministicPhase)
    assert phase.run == "plan_check.mark_validated"
    # No args: `bind_arguments` takes `plan_path` and `worktree` from the
    # context by parameter name. No gates and not best-effort: an unmarked plan
    # makes the next run re-plan, so a failure here must escalate.
    assert phase.args == {}
    assert phase.gates == []
    assert phase.best_effort is False
    assert phase.when is None
    assert phase.skip_to is None

    names = workflow.phase_names
    assert names.index("validate_plan") < names.index("mark_validated")
    assert names.index("mark_validated") < names.index("implement")
```

- [ ] **Step 2: Run the document tests to verify they fail**

Run: `uv run pytest tests/workflow/test_builtin_task.py -k "thirteen or mark_validated_stamps" -v`
Expected: FAIL — the count test fails on the tuple comparison (12 phases loaded, 13 expected), and the new test fails with a `WorkflowLoadError`/lookup error because the document has no phase named `mark_validated`.

- [ ] **Step 3: Write the failing registry tests**

In `tests/workflow/test_registry.py`, add `"plan_check.mark_validated"` to `TASK_YAML_NAMES` (lines 84-96) in sorted position, between `"plan_check.has_validated_plan"` and `"plan_hash_gate"`:

```python
TASK_YAML_NAMES = (
    "critic_blockers_gate",
    "exploration_output_gate",
    "plan_check.find_validated_plan",
    "plan_check.has_validated_plan",
    "plan_check.mark_validated",
    "plan_hash_gate",
    "review_gate",
    "rollup.set_status",
    "verification_gate",
    "verification_passed_gate",
    "verify.run_suite",
    "worktree.ensure",
)
```

Add one line to `test_default_registry_resolves_implemented_steps_to_the_real_callables` (line 118), after the `has_validated_plan` assertion:

```python
    assert registry.resolve("plan_check.mark_validated") is plan_check.mark_validated
```

- [ ] **Step 4: Run the registry tests to verify they fail**

Run: `uv run pytest tests/workflow/test_registry.py -v`
Expected: FAIL — `test_default_registry_holds_exactly_the_names_task_yaml_uses`, `test_default_registry_returns_an_independent_registry_each_call` and `test_default_registry_resolves_implemented_steps_to_the_real_callables` all fail because the name is not registered.

- [ ] **Step 5: Write the Review Focus binding test**

Add to `tests/workflow/test_registry.py`, immediately after `test_the_engine_can_bind_the_documents_args_to_the_rollup_step` (which begins at line 128):

```python
def test_the_engine_can_bind_mark_validated_out_of_the_subtask_context() -> None:
    """Review focus: the phase carries no `args:`, so both parameters have to
    come from the context by name -- `plan_path` from `engine._document_paths`
    and `worktree` from `engine.subtask_context`. If either name drifted, the
    phase would die at runtime while every unit test still passed."""
    bound = bind_arguments(
        plan_check.mark_validated,
        {
            "card": "a32af745",
            "worktree": Path("/repo/.claude/worktrees/m2/task-rows-a32af745"),
            "plan_path": "docs/superpowers/plans/task-rows-a32af745.md",
            "spec_path": "docs/superpowers/specs/task-rows-a32af745.md",
            "commands": ["uv run pytest"],
        },
        None,
        phase="mark_validated",
        function="plan_check.mark_validated",
    )

    assert bound == {
        "plan_path": "docs/superpowers/plans/task-rows-a32af745.md",
        "worktree": Path("/repo/.claude/worktrees/m2/task-rows-a32af745"),
    }
```

- [ ] **Step 6: Run the binding test to verify it fails**

Run: `uv run pytest tests/workflow/test_registry.py::test_the_engine_can_bind_mark_validated_out_of_the_subtask_context -v`
Expected: this one may already PASS, because `bind_arguments` needs only the callable from Task 1, not the registration. If it passes, record that and continue — it is a regression guard on the parameter names, not on this task's edits.

- [ ] **Step 7: Register the function**

In `src/agent_manager/workflow/registry.py`, add the name to `BUILTIN_FUNCTION_NAMES` (line 210) in sorted position:

```python
BUILTIN_FUNCTION_NAMES = (
    "critic_blockers_gate",
    "exploration_output_gate",
    "plan_check.find_validated_plan",
    "plan_check.has_validated_plan",
    "plan_check.mark_validated",
    "plan_hash_gate",
    "review_gate",
    "rollup.set_status",
    "verification_gate",
    "verification_passed_gate",
    "verify.run_suite",
    "worktree.ensure",
)
```

and add the binding to the deterministic-steps block of `default_registry()`, after the `has_validated_plan` line (line 257):

```python
    registry.register("plan_check.mark_validated", plan_check.mark_validated)
```

The real imported callable, not a `_placeholder`: `resolve(name) is the_function` has to hold, and the step exists as of Task 1.

- [ ] **Step 8: Add the phase to the workflow document**

In `src/agent_manager/workflow/builtin/task.yaml`, insert between the `validate_plan` phase (ending line 55) and the `implement` phase (beginning line 57):

```yaml
  - name: mark_validated
    kind: deterministic
    run: plan_check.mark_validated
```

so that region of the file reads:

```yaml
  - name: validate_plan
    kind: agent
    role: critic
    inputs: [spec_path, plan_path]
    result: CriticResult
    gates: [critic_blockers_gate]

  - name: mark_validated
    kind: deterministic
    run: plan_check.mark_validated

  - name: implement
    kind: agent
    role: coder
    inputs: [plan_path, spec_path, branch, base_branch]
    result: ImplementResult
```

- [ ] **Step 9: Run the workflow tier to verify it passes**

Run: `uv run pytest tests/workflow -v`
Expected: PASS, including `test_every_resolved_function_is_the_registry_binding`, `test_builtin_task_has_the_thirteen_phases_in_spec_order`, `test_mark_validated_stamps_the_plan_between_validation_and_implement` and every registry test.

- [ ] **Step 10: Run the full suite**

Run: `uv run pytest`
Expected: PASS, all green. Watch specifically for other tests that count phases or enumerate phase names (for example `test_no_agent_phase_precedes_the_worktree_phase`, which asserts seven agent phases — unchanged, since the new phase is deterministic). If one fails on the new count, update that count; do not remove the phase.

- [ ] **Step 11: Commit**

```bash
git add src/agent_manager/workflow/registry.py src/agent_manager/workflow/builtin/task.yaml tests/workflow/test_registry.py tests/workflow/test_builtin_task.py
git commit -m "feat: run plan_check.mark_validated between validate_plan and implement"
```
