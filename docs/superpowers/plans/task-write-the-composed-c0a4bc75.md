<!-- task-pipeline: validated -->
<!-- SPEC (verbatim, prepended per the task contract) -->

# Write the composed brief as the attempt's prompt.txt (card c0a4bc75)

Narrows R2 of `docs/superpowers/specs/2026-09-23-real-harness-design.md` (lines 63-75), amending `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §6 steps 3-4 and §8. Parent story 760dd05c. This card is the *engine* half of R2: making `dispatch.AgentRunner._attempt` write the full composed brief to `attempt_dir/prompt.txt` instead of the rendered inputs alone.

## Scope

In scope:

- `src/agent_manager/dispatch.py`: `_attempt` composes the brief for the attempt it is about to dispatch and writes it as that attempt's `prompt.txt`; `__call__`'s retry loop stops re-feeding an already-composed `RenderedPrompt` into `_attempt` and instead carries the accumulated feedback as data, so the brief is composed exactly once per attempt with exactly one Result contract.
- `src/agent_manager/prompt.py`: not edited: `compose_brief` already exists (see "Dependency on a1af1e04").
- `tests/test_dispatch.py`: the engine-tier tests listed below, plus the update to the one existing assertion that pins the old prompt shape.

Out of scope, explicitly: the Claude adapter (`src/agent_manager/harness/claude.py` is not edited — `-p` stays a pointer at `prompt.txt`, the brief never moves into `--append-system-prompt`), `cli.py` and `README` (card 19efcddc), the role bundles' `system.md` files (a1af1e04), the fake-`claude` PATH wiring test and the opt-in real-harness e2e test (milestone-level, R4), milestone orchestration, non-Claude harnesses, and every deferred item of the addendum's section 4.

## Dependency on a1af1e04

Sibling a1af1e04 has landed the composer: `prompt.compose_brief(role, rendered, *, result_path=None, result_model=None, feedback=None) -> str` (prompt.py), with `RESULT_HEADING`, `METHODOLOGY_HEADING_PREFIX` and `FEEDBACK_HEADING` (which `dispatch.FEEDBACK_HEADING` re-exports), and its own tests in `tests/test_prompt.py`. This card **consumes it unchanged** and adds no composition logic and no composer test (former test 8 is dropped). It requires `result_path` and `result_model` together (both or neither) and an absolute `result_path`; `attempt_dir` from `paths.attempt_dir` satisfies that.

Because `compose_brief` takes only a single `feedback` string (one heading), and this card needs one heading per accumulated block, `_attempt` calls `compose_brief(..., feedback=None)` and then appends each feedback block itself, in production order, as `"\n" + FEEDBACK_HEADING + "\n" + block.strip("\n") + "\n"`-style sections (one blank line between sections, single trailing newline). `with_feedback` stays defined (tests/test_dispatch.py pins it) but the retry loop no longer calls it.

## Observable behaviour

For every attempt, the file at `attempt_dir/prompt.txt` — the same path `Dispatch.prompt_path` and `Attempt.prompt_path` record, and the same path the adapter's argv points the harness at — contains, in this order:

1. The role's `system.md` text (`RoleBundle.system`).
2. Each methodology file of `RoleBundle.methodology` under its own `##` heading, in `RoleBundle.methodology` iteration order (the loader builds it in `VENDORED.lock` order, which `compose_brief` preserves), so two runs of the same role produce byte-identical briefs.
3. The rendered inputs: the existing `RenderedPrompt.text` from `render_prompt`, unchanged in content and section order.
4. A **Result contract** section stating the absolute result path — exactly `attempt_dir / dispatch.RESULT_NAME`, i.e. this attempt's `result.json` — instructing that valid JSON be written there, that the path is outside the worktree and must stay there, and embedding the JSON Schema of the phase's result model (`model.model_json_schema()`). When the phase declares no result (`phase.result is None`, so `model is None`), no Result contract section appears at all.
5. On a retry only, the feedback section(s), each introduced by `dispatch.FEEDBACK_HEADING`, after the Result contract. A third attempt carries both earlier feedback blocks, in the order they were produced — the accumulation `with_feedback` provides today is preserved.

Invariants that follow and that the tests pin:

- The Result contract appears **exactly once** in any attempt's prompt, retry or not. The brief is composed from the base `RenderedPrompt` plus the accumulated feedback; a composed brief is never re-composed.
- Each attempt's prompt names **its own** attempt's result path: attempt 2's brief says `.../explore.2/result.json`, not attempt 1's. This is why composition happens inside `_attempt`, after `next_attempt`/`paths.attempt_dir` have fixed the directory.
- The rendered-inputs body of a retry prompt is identical to that of the first attempt; only the appended feedback and the attempt-specific result path differ.
- `Attempt.prompt_path` and `Attempt.result_path` keep their present meaning and are still journalled on both the `started` and the terminal row.
- `dispatch.py` still imports no `subprocess`; the launcher stays injected (D7); `stdout.log` is still never parsed for a result (D4); the attempt directory is still outside the worktree.

Because the brief now leads with the role's system text, the prompt file no longer starts with `# phase: <name>`. The existing assertion `text.startswith("# phase: explore")` in `test_a_persistently_invalid_result_retries_to_max_attempts_then_fails` (tests/test_dispatch.py, near line 672) becomes a containment assertion on the rendered-inputs header.

## Error paths

- **Writing the prompt fails (`OSError`).** Unchanged contract: an `EngineError` naming the path and carrying `phase`, exactly as `RenderedPrompt.write` raises today (prompt.py lines 56-73). Reuse `write` rather than mirroring it, so there is one place that can fail this way. The failure propagates out of `_attempt`, `__call__` records the phase `failed`, and the exception is re-raised — the existing `except Exception` arm already covers this and gains no new branch.
- **Result model schema generation fails.** Not defended against: the result models are the repo's own pydantic models resolved by `results.resolve_result_model`, and a model whose `model_json_schema()` raises is a bug in this package, not a retryable attempt. Let it propagate as-is.
- **A role with an empty methodology dict.** Legitimate (`RoleBundle.methodology` defaults to `{}`); the brief simply has no methodology headings. Not an error.
- **A phase with no result model.** No Result contract; not an error. The `classify` path for `model is None` (a JSON object is still required) is untouched.
- **Unreadable role bundle.** Already handled upstream by `load_role` in `__call__`, before any attempt is journalled. No new path.

## Test list

Tier per the design spec's section 14 (docs/superpowers/specs/2026-09-23-agent-manager-design.md lines 476-490): pure functions get unit tests; engine/dispatch behaviour is driven with a fake adapter and a fake launcher writing canned result files; adapters are asserted via pure `build_command`; the single opt-in e2e test is milestone-level. All the behavioural tests of this card are therefore **engine tier, in `tests/test_dispatch.py`** — none is an adapter test, none is e2e.

Fixture work these need, in `tests/test_dispatch.py`:

- `make_role` (near line 114) gains non-trivial system text and at least one methodology file, so the composed brief has something distinguishable to find.
- `FakeAdapter.build_command` (near line 124) currently omits `prompt_path`; extend its argv with `--prompt <d.prompt_path>` so the launcher is genuinely *pointed at* the file rather than told the path out of band.
- `FakeLauncher` (near line 208) captures the contents of the file named after `--prompt` at launch time, per call, so a retry's prompt can be asserted against the prompt as the harness saw it rather than as it ended up on disk.

Tests:

1. **The prompt the launcher is pointed at is the full brief.** Engine tier. One successful attempt; assert the captured prompt text contains the role's system text, the methodology heading and its body, the rendered inputs' section text, and the literal string of `paths.attempt_dir(RUN_ID, CARD, "explore", 1) / "result.json"`, in that relative order.
2. **The Result contract embeds the phase's schema.** Engine tier. Same single attempt; assert the contract section contains the phase result model's `model_json_schema()` content (a required property name and the schema's own marker, not a whole-string equality that would pin pydantic's formatting).
3. **A retry prompt carries the feedback and exactly one Result contract.** Engine tier. Drive the existing invalid-result retry scenario; on attempt 2's captured prompt assert the Result contract marker occurs exactly once, `FEEDBACK_HEADING` is present, the feedback text follows the contract, and the rendered inputs appear once.
4. **A third attempt accumulates both feedback blocks, still with one contract.** Engine tier. `max_attempts = 3` with a persistently invalid result; assert two feedback blocks and one contract marker.
5. **Each attempt's prompt names its own result path.** Engine tier. From the same retry run, assert attempt 1's prompt contains `explore.1/result.json` and not `explore.2/result.json`, and vice versa for attempt 2.
6. **A phase with no declared result gets no Result contract.** Engine tier. A phase document with `result:` absent; assert the marker is absent and the brief still carries system, methodology and inputs.
7. **The prompt path journalled on the attempt is the file that was composed.** Engine tier. Assert `Attempt.prompt_path` from the journal equals the `--prompt` path the launcher received and that reading it back yields the composed brief.
8. (Dropped: the composer is a1af1e04's and already tested.)
9. **Existing prompt-shape assertion updated.** Engine tier, an edit not an addition: `text.startswith("# phase: explore")` becomes `"# phase: explore" in text`, with the brief's leading role text asserted before it.

<!-- END SPEC -->

---

# Write the composed brief as the attempt's prompt.txt — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every agent attempt's `prompt.txt` the full composed brief — role system text, methodology, rendered inputs, this attempt's Result contract, and accumulated feedback on retries — instead of the rendered inputs alone.

**Architecture:** `dispatch.AgentRunner._attempt` calls the existing pure `prompt.compose_brief(role, rendered, result_path=..., result_model=...)` after `paths.attempt_dir` has fixed the attempt directory, appends the accumulated feedback blocks itself (one `FEEDBACK_HEADING` per block), and writes the result through `dataclasses.replace(rendered, text=brief).write(attempt_dir)` so the single `RenderedPrompt.write` remains the only place a prompt write can raise `EngineError`. `AgentRunner.__call__` stops mutating the `RenderedPrompt` between attempts: it carries the feedback as a `list[str]` and passes it in, so the brief is composed exactly once per attempt from the untouched base and the contract appears exactly once with that attempt's own result path.

**Tech Stack:** Python 3.12+, pydantic v2, pytest, `uv` (`uv run pytest`). No new dependencies.

**Spec:** prepended verbatim above; source file `docs/superpowers/specs/task-write-the-composed-c0a4bc75-design.md` in this worktree.

## Global Constraints

- `src/agent_manager/dispatch.py` must still import no `subprocess`; the launcher stays injected (D7).
- `src/agent_manager/harness/claude.py` is not edited: `-p` stays a pointer at `prompt.txt`, the brief never moves into `--append-system-prompt`.
- `src/agent_manager/cli.py`, `README`, `src/agent_manager/prompt.py` and the shipped `roles/bundles/*/system.md` files are not edited by this card.
- `stdout.log` is never parsed for a result (D4); the result file is `result.json` (`dispatch.RESULT_NAME`) inside the attempt directory, which is outside every worktree.
- `models.Attempt.prompt_path` and `models.Attempt.result_path` keep their present meaning and stay journalled on both the `started` row and the terminal row.
- Source lives under `src/agent_manager/`, tests mirror it under `tests/`; pydantic models only at process boundaries, dataclasses for internal state (CLAUDE.md).
- All behavioural tests of this card are engine tier, in `tests/test_dispatch.py`, driven by the fake adapter and fake launcher. No adapter test, no e2e test, no new pytest marker.
- Verification command: `uv run pytest`.

## Review Focus

Five conditions the spec implies that the listed tests would otherwise leave unpinned; each has a test assigned below.

1. **A role whose `methodology` dict is empty** (the spec's error paths call this legitimate): the brief must simply carry no methodology heading and the attempt must still succeed — covered in Task 1, Step 1, `test_a_role_with_no_methodology_still_composes_a_brief`.
2. **A methodology document that contains its own `##` headings** (every vendored skill file does): its body must appear verbatim inside the brief rather than being re-flowed or truncated — covered in Task 1, Step 1, `test_a_methodology_documents_own_headings_survive_into_the_brief`.
3. **`prompt.txt` cannot be written (`OSError`)**: the caller must see an `EngineError` naming the path and carrying the phase, the phase must be journalled `failed`, and no harness may be launched — covered in Task 1, Step 1, `test_a_prompt_that_cannot_be_written_is_a_named_engine_error`.
4. **A phase that declares no result** must get no Result contract at all rather than a half-contract `EngineError` from `compose_brief` — covered in Task 1, Step 1, `test_a_phase_with_no_declared_result_gets_no_result_contract` (spec test 6).
5. **A retry's rendered-inputs body must be byte-identical to the first attempt's** so the only differences between attempts are the feedback and the attempt-specific result path — covered in Task 2, Step 1, `test_a_retry_changes_only_the_result_path_and_the_feedback`.

---

## File Structure

- Modify `src/agent_manager/dispatch.py`:
  - `__call__` (lines 378-430): the retry loop carries `feedback: list[str]` instead of rebuilding `text`.
  - `_attempt` (lines 432-499): new `feedback` parameter; composes the brief and writes it.
  - New module-level helper `_append_feedback` next to `with_feedback`.
- Modify `tests/test_dispatch.py`: `make_role`, `FakeAdapter.build_command`, `FakeLauncher`, `_runner`, the existing `startswith` assertion, plus the new tests.
- Nothing else in the tree changes.

---

### Task 1: Compose the brief inside `_attempt` and write it as this attempt's `prompt.txt`

**Files:**
- Modify: `src/agent_manager/dispatch.py:432-499` (`_attempt`), `src/agent_manager/dispatch.py:22-27` (imports), new helper after `with_feedback` at `src/agent_manager/dispatch.py:83`
- Test: `tests/test_dispatch.py` (engine tier — fake adapter + fake launcher, the tier design §14 lines 486-488 assigns to dispatch behaviour)

**Interfaces:**
- Consumes (already on this branch, unchanged): `prompt.compose_brief(role: RoleBundle, rendered: prompt.RenderedPrompt, *, result_path: Path | str | None = None, result_model: type[BaseModel] | None = None, feedback: str | None = None) -> str`; `prompt.RESULT_HEADING: str`; `prompt.METHODOLOGY_HEADING_PREFIX: str`; `prompt.FEEDBACK_HEADING: str` (re-exported as `dispatch.FEEDBACK_HEADING`); `prompt.RenderedPrompt.write(attempt_dir: Path) -> Path`; `paths.attempt_dir(run_id: str, card: str, phase: str, attempt: int) -> Path`; `dispatch.RESULT_NAME = "result.json"`.
- Produces for Task 2: `dispatch._append_feedback(brief: str, feedback: Sequence[str]) -> str` and the new `_attempt` signature `_attempt(self, phase: AgentPhase, context: Mapping[str, Any], rendered: prompt.RenderedPrompt, feedback: Sequence[str], target: Target, role: RoleBundle, cwd: Path, model: type[BaseModel] | None) -> Verdict`; plus the test fixtures `make_role(root, name="explorer", *, policy=POLICY, methodology: dict[str, str] | None = None)`, `FakeLauncher.prompts: list[str]`, and `_runner(..., methodology=...)`.

- [ ] **Step 1: Write the failing tests**

First extend the fixtures. In `tests/test_dispatch.py`, add `import hashlib` to the stdlib imports (it goes above `import json` on line 11):

```python
import hashlib
import json
import subprocess
```

Replace the whole `make_role` helper (currently lines 123-130) with one that ships a real, lock-consistent methodology document (`roles/loader._read_methodology` hashes every file and rejects any file without a `[[vendored]]` entry):

```python
METHODOLOGY = """\
# Test-driven development

## the loop

Red, green, refactor. Never write implementation code before a failing test.
"""


def make_role(
    root: Path,
    name: str = "explorer",
    *,
    policy: str = POLICY,
    methodology: dict[str, str] | None = None,
) -> Path:
    """A synthetic role bundle, built the way tests/roles/test_loader.py does.

    `methodology` defaults to one vendored document so a composed brief has a
    heading and a body to find; `{}` builds a bundle that vendors nothing.
    """
    directory = root / name
    (directory / "methodology").mkdir(parents=True, exist_ok=True)
    (directory / "system.md").write_text(
        f"Standing instructions for {name}.\n", encoding="utf-8"
    )
    (directory / "policy.toml").write_text(policy, encoding="utf-8")
    documents = (
        {"test-driven-development.md": METHODOLOGY}
        if methodology is None
        else methodology
    )
    entries = []
    for filename, text in documents.items():
        path = directory / "methodology" / filename
        path.write_text(text, encoding="utf-8")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append(
            "[[vendored]]\n"
            f'file = "{filename}"\n'
            f'upstream = "skills/{filename}"\n'
            f'sha256 = "{digest}"\n'
        )
    (directory / "VENDORED.lock").write_text(
        "".join(entries) or "vendored = []\n", encoding="utf-8"
    )
    return directory
```

Point the fake adapter's argv at the prompt file — replace `FakeAdapter.build_command` (currently lines 142-144):

```python
    def build_command(self, d: models.Dispatch) -> list[str]:
        self.dispatches.append(d)
        return [
            "fake-harness",
            "--model",
            d.model,
            "--prompt",
            str(d.prompt_path),
            "--result",
            str(d.result_path),
        ]
```

Capture what the harness was actually handed — in `FakeLauncher` (currently lines 216-244) add the `prompts` field after `calls` and the read at the top of `__call__`:

```python
    calls: list[list[str]] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)

    def __call__(self, argv, *, cwd, timeout, stdout_path) -> Outcome:
        self.calls.append(list(argv))
        if "--prompt" in argv:
            self.prompts.append(
                Path(argv[argv.index("--prompt") + 1]).read_text(encoding="utf-8")
            )
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
```

Let a test choose the bundle's methodology — in `_runner` (currently lines 562-586) replace the `make_role` line:

```python
    adapter = overrides.pop("adapter", FakeAdapter())
    make_role(tmp_path / "bundles", methodology=overrides.pop("methodology", None))
```

Now append these tests to the end of `tests/test_dispatch.py`:

```python
# ── the composed brief (addendum R2 §2) ──────────────────────────────────────


def _gated_workflow():
    return _workflow(AGENT_DOCUMENT, {"output_gate": lambda result: None})


def test_the_prompt_the_launcher_is_pointed_at_is_the_full_brief(
    store, tmp_path, worktree
):
    # Spec test 1: role system text, methodology heading and body, rendered
    # inputs, then this attempt's own result path, in that order.
    launcher = FakeLauncher(results=[VALID_RESULT])
    workflow = _gated_workflow()
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    runner(workflow.phase("explore"), _context(worktree), _rendered())

    brief = launcher.prompts[0]
    heading = f"{prompt.METHODOLOGY_HEADING_PREFIX}test-driven-development.md"
    result_path = str(paths.attempt_dir(RUN_ID, CARD, "explore", 1) / "result.json")
    assert brief.index("Standing instructions for explorer.") < brief.index(heading)
    assert brief.index(heading) < brief.index("Red, green, refactor.")
    assert brief.index("Red, green, refactor.") < brief.index("# phase: explore")
    assert brief.index("# phase: explore") < brief.index(prompt.RESULT_HEADING)
    assert brief.index(prompt.RESULT_HEADING) < brief.index(result_path)


def test_the_result_contract_embeds_the_phases_result_schema(store, tmp_path, worktree):
    # Spec test 2: the schema in the brief is the model the engine validates
    # against, so instruction and validator cannot drift.
    launcher = FakeLauncher(results=[VALID_RESULT])
    workflow = _gated_workflow()
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    runner(workflow.phase("explore"), _context(worktree), _rendered())

    contract = launcher.prompts[0].split(prompt.RESULT_HEADING, 1)[1]
    assert '"summary"' in contract
    assert '"required"' in contract
    assert '"additionalProperties": false' in contract


def test_a_phase_with_no_declared_result_gets_no_result_contract(
    store, tmp_path, worktree
):
    # Spec test 6 / Review Focus 4: no contract at all, not a half-contract error.
    document = """
name: agentic
phases:
  - name: spec
    kind: agent
    role: explorer
    writes: docs/superpowers/specs/{stem}.md
"""
    workflow = _workflow(document, {})
    launcher = FakeLauncher(results=[json.dumps({"wrote": "docs/spec.md"})])
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    result = runner(workflow.phase("spec"), _context(worktree), _rendered())

    brief = launcher.prompts[0]
    assert result == {"wrote": "docs/spec.md"}
    assert prompt.RESULT_HEADING not in brief
    assert "Standing instructions for explorer." in brief
    assert f"{prompt.METHODOLOGY_HEADING_PREFIX}test-driven-development.md" in brief
    assert "# phase: explore" in brief


def test_the_journalled_prompt_path_is_the_file_the_launcher_was_pointed_at(
    store, tmp_path, worktree
):
    # Spec test 7: Attempt.prompt_path, the adapter's argv and the composed file
    # are one and the same thing.
    launcher = FakeLauncher(results=[VALID_RESULT])
    workflow = _gated_workflow()
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    runner(workflow.phase("explore"), _context(worktree), _rendered())

    argv = launcher.calls[0]
    pointed = Path(argv[argv.index("--prompt") + 1])
    terminal = [
        line.payload
        for line in store.journal.read()
        if line.event == "attempt_upsert" and line.payload["status"] == "ok"
    ][0]
    assert pointed == paths.attempt_dir(RUN_ID, CARD, "explore", 1) / "prompt.txt"
    assert terminal["prompt_path"] == str(pointed)
    assert pointed.read_text(encoding="utf-8") == launcher.prompts[0]


def test_a_role_with_no_methodology_still_composes_a_brief(store, tmp_path, worktree):
    # Review Focus 1: RoleBundle.methodology defaults to {} and that is legal.
    launcher = FakeLauncher(results=[VALID_RESULT])
    workflow = _gated_workflow()
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree, methodology={})

    result = runner(workflow.phase("explore"), _context(worktree), _rendered())

    brief = launcher.prompts[0]
    assert result == {"summary": "explored the tree", "ok": True}
    assert prompt.METHODOLOGY_HEADING_PREFIX not in brief
    assert "Standing instructions for explorer." in brief
    assert "# phase: explore" in brief
    assert prompt.RESULT_HEADING in brief


def test_a_methodology_documents_own_headings_survive_into_the_brief(
    store, tmp_path, worktree
):
    # Review Focus 2: vendored skill files are markdown with their own headings;
    # the brief hands the agent the text, byte for byte.
    launcher = FakeLauncher(results=[VALID_RESULT])
    workflow = _gated_workflow()
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    runner(workflow.phase("explore"), _context(worktree), _rendered())

    brief = launcher.prompts[0]
    assert "# Test-driven development" in brief
    assert "## the loop" in brief
    assert METHODOLOGY.strip("\n") in brief


def test_a_prompt_that_cannot_be_written_is_a_named_engine_error(
    store, tmp_path, worktree, monkeypatch
):
    # Review Focus 3 / spec error paths: RenderedPrompt.write stays the single
    # place this can fail, the phase is journalled failed, nothing is launched.
    real_write_text = Path.write_text

    def refuse(self, *args, **kwargs):
        if self.name == "prompt.txt":
            raise OSError("no space left on device")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", refuse)
    launcher = FakeLauncher(results=[VALID_RESULT])
    workflow = _gated_workflow()
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    with pytest.raises(EngineError) as caught:
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    assert caught.value.phase == "explore"
    assert "prompt.txt" in str(caught.value)
    assert launcher.calls == []
    assert _phase_statuses(store) == [("explore", "started"), ("explore", "failed")]
```

Finally, update the one existing assertion the new prompt shape breaks (spec test 9) — in `test_a_persistently_invalid_result_retries_to_max_attempts_then_fails` (line 681) replace:

```python
    assert text.startswith("# phase: explore")
```

with:

```python
    assert text.startswith("Standing instructions for explorer.")
    assert "# phase: explore" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dispatch.py -x -q`
Expected: FAIL. `test_the_prompt_the_launcher_is_pointed_at_is_the_full_brief` fails on `brief.index("Standing instructions for explorer.")` with `ValueError: substring not found` — the written prompt is still only the rendered inputs. `test_the_result_contract_embeds_the_phases_result_schema`, `test_a_phase_with_no_declared_result_gets_no_result_contract`, `test_the_journalled_prompt_path_is_the_file_the_launcher_was_pointed_at`, `test_a_role_with_no_methodology_still_composes_a_brief`, `test_a_methodology_documents_own_headings_survive_into_the_brief` and the edited `startswith` assertion fail the same way, on the absent role text, methodology text or contract. One exception: `test_a_prompt_that_cannot_be_written_is_a_named_engine_error` passes already — `rendered.write(attempt_dir)` is the same `RenderedPrompt.write` the fix keeps using, so this one is a regression guard that must stay green through Step 4 rather than a red test.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/dispatch.py`, widen the `collections.abc` import on line 23:

```python
from collections.abc import Callable, Mapping, Sequence
```

Add the feedback-appending helper directly below `with_feedback` (after line 83):

```python
def _append_feedback(brief: str, feedback: Sequence[str]) -> str:
    """One `FEEDBACK_HEADING` section per accumulated complaint, after the brief.

    `prompt.compose_brief` takes a single feedback string and this loop needs one
    heading per attempt's complaint, so the sections are appended here instead.
    The joining matches `prompt._join_sections`: one blank line between
    neighbours, one newline at the end, interior text untouched.
    """
    parts = [brief.strip("\n")]
    for block in feedback:
        parts.append(FEEDBACK_HEADING + "\n" + block.strip("\n"))
    return "\n\n".join(parts) + "\n"
```

Change `_attempt`'s signature and its first four statements (lines 432-445). The parameter goes directly after `rendered`, so the accumulated complaints travel beside the prompt they belong to:

```python
    def _attempt(
        self,
        phase: AgentPhase,
        context: Mapping[str, Any],
        rendered: prompt.RenderedPrompt,
        feedback: Sequence[str],
        target: Target,
        role: RoleBundle,
        cwd: Path,
        model: type[BaseModel] | None,
    ) -> Verdict:
        """One dispatch: directory, brief, argv, launcher, result, gates.

        The brief is composed here rather than by the caller because addendum R2
        puts this attempt's own `result.json` in it, and that path only exists
        once `next_attempt` and `paths.attempt_dir` have fixed the directory.
        """
        n = next_attempt(self.run_id, self.card_id, phase.name)
        attempt_dir = paths.attempt_dir(self.run_id, self.card_id, phase.name, n)
        brief = prompt.compose_brief(
            role,
            rendered,
            result_path=None if model is None else attempt_dir / RESULT_NAME,
            result_model=model,
        )
        # `replace` rather than a second writer: `RenderedPrompt.write` stays the
        # one place a prompt write can fail, with the `EngineError` it already
        # raises, and it keeps the phase name this rendering came from.
        prompt_path = replace(rendered, text=_append_feedback(brief, feedback)).write(
            attempt_dir
        )
```

Everything below that line in `_attempt` is unchanged.

Update the call site in `__call__` (line 410) so it compiles — the loop still passes an empty tuple for now:

```python
                verdict = self._attempt(
                    phase, context, text, (), target, role, cwd, model
                )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dispatch.py -q`
Expected: PASS for every test in the file, including the seven new ones and the two pre-existing retry tests (`test_a_persistently_invalid_result_retries_to_max_attempts_then_fails` and `test_a_retryable_gate_failure_re_dispatches_with_the_gate_detail`), which still see their feedback because `__call__` still appends it to `text` via `with_feedback`.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/dispatch.py tests/test_dispatch.py
git commit -m "feat(dispatch): write the composed brief as the attempt's prompt.txt

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ"
```

---

### Task 2: Carry retry feedback as data so each attempt composes exactly one contract

**Files:**
- Modify: `src/agent_manager/dispatch.py:401-416` (`__call__`'s retry loop)
- Test: `tests/test_dispatch.py` (engine tier)

**Interfaces:**
- Consumes from Task 1: `dispatch._append_feedback(brief: str, feedback: Sequence[str]) -> str`; `_attempt(self, phase, context, rendered, feedback, target, role, cwd, model) -> Verdict`; `FakeLauncher.prompts: list[str]`; `make_role(..., methodology=...)`; `METHODOLOGY`; `_gated_workflow()`.
- Produces: nothing new for later tasks; `with_feedback` stays exported and unchanged (its own tests at lines 82-109 still pin it).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dispatch.py`:

```python
def test_a_retry_prompt_carries_the_feedback_after_exactly_one_contract(
    store, tmp_path, worktree
):
    # Spec test 3: the brief is composed once per attempt from the untouched
    # base, so the contract cannot be duplicated by a re-composition.
    workflow = _gated_workflow()
    launcher = FakeLauncher(results=[INVALID_RESULT])
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed):
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    second = launcher.prompts[1]
    assert second.count(prompt.RESULT_HEADING) == 1
    assert second.count("# phase: explore") == 1
    assert second.count(dispatch.FEEDBACK_HEADING) == 1
    assert second.index(prompt.RESULT_HEADING) < second.index(dispatch.FEEDBACK_HEADING)
    assert "summary" in second.split(dispatch.FEEDBACK_HEADING, 1)[1]


def test_a_third_attempt_accumulates_both_feedback_blocks_with_one_contract(
    store, tmp_path, worktree
):
    # Spec test 4: §6 step 7's "the prior prompt plus the feedback block",
    # preserved now that the accumulation lives in the loop rather than in the
    # RenderedPrompt.
    document = AGENT_DOCUMENT.replace("max_attempts: 2", "max_attempts: 3")
    workflow = _workflow(document, {"output_gate": lambda result: None})
    launcher = FakeLauncher(results=[INVALID_RESULT])
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed):
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    third = launcher.prompts[2]
    assert len(launcher.prompts) == 3
    assert third.count(dispatch.FEEDBACK_HEADING) == 2
    assert third.count(prompt.RESULT_HEADING) == 1
    assert third.count("# phase: explore") == 1


def test_each_attempts_prompt_names_its_own_result_path(store, tmp_path, worktree):
    # Spec test 5: attempt 2 must not tell the harness to overwrite attempt 1's
    # result file -- classify() reads this attempt's path and nothing else.
    workflow = _gated_workflow()
    launcher = FakeLauncher(results=[INVALID_RESULT])
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed):
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    first_path = str(paths.attempt_dir(RUN_ID, CARD, "explore", 1) / "result.json")
    second_path = str(paths.attempt_dir(RUN_ID, CARD, "explore", 2) / "result.json")
    assert first_path in launcher.prompts[0]
    assert second_path not in launcher.prompts[0]
    assert second_path in launcher.prompts[1]
    assert first_path not in launcher.prompts[1]


def test_a_retry_changes_only_the_result_path_and_the_feedback(
    store, tmp_path, worktree
):
    # Review Focus 5: the rendered-inputs body of attempt 2 is byte-identical to
    # attempt 1's, so a retry is the same brief plus one appended section.
    workflow = _gated_workflow()
    launcher = FakeLauncher(results=[INVALID_RESULT])
    runner, _ = _runner(store, workflow, launcher, tmp_path, worktree)

    with pytest.raises(AgentPhaseFailed):
        runner(workflow.phase("explore"), _context(worktree), _rendered())

    first_head = launcher.prompts[0].split(prompt.RESULT_HEADING, 1)[0]
    second_head = launcher.prompts[1].split(prompt.RESULT_HEADING, 1)[0]
    assert first_head == second_head
    replayed = launcher.prompts[1].split(dispatch.FEEDBACK_HEADING, 1)[0].replace(
        str(paths.attempt_dir(RUN_ID, CARD, "explore", 2)),
        str(paths.attempt_dir(RUN_ID, CARD, "explore", 1)),
    )
    assert replayed.rstrip("\n") == launcher.prompts[0].rstrip("\n")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dispatch.py -q -k "retry_prompt_carries or third_attempt_accumulates or own_result_path or changes_only_the_result_path"`
Expected: FAIL. `test_a_retry_prompt_carries_the_feedback_after_exactly_one_contract` fails on `second.index(prompt.RESULT_HEADING) < second.index(dispatch.FEEDBACK_HEADING)` — `__call__` still appends the feedback to the `RenderedPrompt` text, so it lands inside the rendered-inputs block *before* the contract. `test_a_retry_changes_only_the_result_path_and_the_feedback` fails on `first_head == second_head` for the same reason. (`test_a_third_attempt_accumulates_both_feedback_blocks_with_one_contract` and `test_each_attempts_prompt_names_its_own_result_path` may already pass; the two above are the red ones this task turns green.)

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/dispatch.py`, replace the retry-loop block in `__call__` (lines 405-416) with:

```python
        feedback: list[str] = []
        verdict = Verdict("harness_error", detail="no attempt was made")

        try:
            for _ in range(budget):
                verdict = self._attempt(
                    phase, context, rendered, tuple(feedback), target, role, cwd, model
                )
                if verdict.status == "ok":
                    self._record_phase(phase, "done", started_at, self.clock(), None)
                    return verdict.result
                if verdict.fatal or verdict.status not in retry_on:
                    break
                # Carried as data, not folded into `rendered`: `_attempt` composes
                # the whole brief from the base prompt every time, so feeding it a
                # brief it had already composed would duplicate the result contract
                # and bury the feedback inside the rendered inputs.
                feedback.append(verdict.detail or verdict.status)
```

The line `text = rendered` (line 405) goes away with it, and `with_feedback` is no longer called by this module — it stays defined and exported, since `tests/test_dispatch.py` pins it and it is the documented shape of §6 step 7's append.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dispatch.py -q`
Expected: PASS, including `test_a_retryable_gate_failure_re_dispatches_with_the_gate_detail` (the gate detail is now appended by `_append_feedback` instead of `with_feedback`), `test_a_persistently_invalid_result_retries_to_max_attempts_then_fails`, and the two `with_feedback` unit tests at lines 82-109.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/dispatch.py tests/test_dispatch.py
git commit -m "fix(dispatch): carry retry feedback as data so each brief has one contract

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ"
```

---

## Done when

- `uv run pytest` passes.
- `grep -n "subprocess" src/agent_manager/dispatch.py` prints nothing.
- `src/agent_manager/harness/claude.py`, `src/agent_manager/cli.py`, `src/agent_manager/prompt.py`, `README.md` and `src/agent_manager/roles/bundles/**` are untouched by the diff.
