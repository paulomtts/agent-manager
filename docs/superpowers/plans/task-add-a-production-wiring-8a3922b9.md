<!-- task-pipeline: validated -->
# Add a production-wiring test that runs under a fake claude executable (8a3922b9)

Subtask of story 01bad3fd ("Prove it: the production wiring under a fake claude, and a real-harness test"), milestone 7aa00a90. Blocked by the seams story 360cd141. Sibling 34d3388b (the opt-in real-harness test) is blocked by this card and shares this card's fixtures.

## Scope

One new test package, `tests/e2e/`, containing `conftest.py` (fixtures, shared with the sibling), a fake `claude` executable, and `test_production_wiring.py`. No production code is authored here. The seams this test exposes belong to 360cd141; see "Seams and amendments".

The test drives `cli.run_card` for one subtask card with **no `runner_factory` argument**, so the production path is exercised end to end: `cli.default_runner_factory` (`src/agent_manager/cli.py:588`) builds the real `dispatch.AgentRunner` with the real `harness/launcher.py:94` `run_direct`, which `Popen`s the real `ClaudeAdapter` argv whose `COMMAND` is the bare name `"claude"` (`src/agent_manager/harness/claude.py:26`), resolved on `PATH`. The test's only intervention is putting a fake `claude` first on `PATH`.

What this test is *not*: it is not a harness-adapter test (those assert the pure `build_command` with the launcher injected), not an engine test (those inject a fake adapter with canned result files), and not the real-harness end-to-end test (that is the sibling, marked and excluded). It is the wiring in between: real adapter, real launcher, real process, fake model.

Out of scope, per addendum §4: ancestor roll-up. Only the subtask's own board status, via `rollup.set_status`, is asserted.

## The fake executable

A single-file Python script written into a tmp directory, `chmod +x`, with a `#!` line, and that directory prepended to `PATH` for the duration of the test. It is the *only* `claude` the run can find.

Its contract, which is the point of the whole card:

- It receives `["claude", "--model", <model>, "--dangerously-skip-permissions", "-p", "Read <prompt_path> and follow the instructions in it exactly. ..."]`. It parses the prompt path out of that `-p` sentence (the format string is `harness/claude.py:30`) and reads the file.
- Everything else it needs — **the absolute result path and the JSON Schema of the expected result model** — it extracts from the *prompt text only*. It has no environment variable, no argv flag, no filename convention and no import of `agent_manager` that could tell it where to write. This is the load-bearing property: a brief that omits the Result contract (addendum R2) makes the fake unable to write anything, and the test fails. Do not give the fake a side channel to make it pass.
- It generates its result **from the embedded schema**, not from a hardcoded literal, so the fake cannot drift away from the models the run actually validates against. Where the schema alone is underdetermined (a gate needs a specific value, not merely a well-typed one), the fake overrides that single field by name.
- It appends one line per invocation to a log file whose path it also derives from the prompt/result paths (a sibling of the result path, i.e. under `paths.data_dir()`, never inside the worktree): phase name, `os.getcwd()`, result path. This log is what the cwd assertions read.
- It exits 0 and prints a short, usage-free line to stdout. stdout is a log only (D4).

### Per-phase behaviour

Seven agent phases: `explore`, `spec`, `validate_spec`, `plan`, `validate_plan`, `implement`, `review`. The fake identifies the phase from the prompt text (the rendered prompt names it) and:

- **explore** — `ExploreResult {refused, reason, summary, verification{full_suite, typecheck, lint}}`. `summary` must exceed `MIN_SUMMARY_LENGTH` (60) and not be a placeholder, and the suite must echo the caller-provided commands *exactly*, because both `exploration_output_gate` and `verification_gate` read it (`src/agent_manager/steps/reducers.py:257` and `:30`).
- **spec** — `SpecResult {path, note|None}`, and it writes the document at the path the phase declares (`writes: docs/superpowers/specs/{stem}.md` in `workflow/builtin/task.yaml:33`), inside the worktree.
- **validate_spec**, **validate_plan** — `CriticResult {blockers, reason, summary}` with no blockers, so `critic_blockers_gate` passes.
- **plan** — `PlanResult {path, self_reviewed, note}`, plus the document at `docs/superpowers/plans/{stem}.md`.
- **implement** — `ImplementResult {blocked, blocked_reason, resumed, plan_hash, report}`. It makes a real commit in the cwd it was launched in (the worktree), carrying a `Plan-Hash: <hash>` trailer, where `<hash>` is the first 8 lowercase hex characters of the sha256 of the plan file — computed, not invented, so it matches what review reports.
- **review** — `ReviewResult {findings, unresolved_blockers, fix_summary, porcelain, commit_count, tagged_count, plan_hash}`, computed by actually running `git status --porcelain` and counting commits and trailers in the worktree, so that `review_gate` sees an empty porcelain, a non-zero `commit_count`, `tagged_count == commit_count`, and `plan_hash_gate` sees the same hash `implement` reported.

Field spelling is whatever `src/agent_manager/steps/reducers.py` and the landed models agree on — see the naming seam below. The fake reads names off the embedded schema rather than hardcoding them, which makes it tolerant of the alias decision the seams story makes.

## Fixtures (`tests/e2e/conftest.py`)

Copy the Steps-tier patterns already proven in `tests/test_cli.py:996-1052` and `tests/steps/test_worktree.py`, generalised so the sibling can reuse them:

- `XDG_DATA_HOME` monkeypatched into `tmp_path`, so `paths.data_dir()` and brd's own database never touch the developer's home. Run state, prompts, result files and stdout logs therefore live outside the worktree, which is what lets the clean-worktree assertion mean something (design §14; D4).
- A real temp git repo on `main` with a committed baseline and `commit.gpgsign=false`, plus `brd init` and its markers committed, so porcelain starts empty.
- A milestone -> story -> subtask card chain (`run --card` requires the subtask to have a parent).
- `requires_git` / `requires_brd` skip guards, matching the existing fixtures.
- A fixture that materialises the fake `claude` and prepends its directory to `PATH`.

Verification commands are passed through `run_card(commands=["<a command that passes in the toy repo>"])` — non-empty, so `verification_gate` passes without `allow_no_verification`, and so the `verify` phase's `verification_passed_gate` has something real to be green about. The same list is what `exploration_output_gate` compares explore's echo against.

## Observable behaviour asserted

1. **Board status (R7).** The card reads `done` **on the board** — `board.show(card_id, repo_dir=root)` (or `brd show`), not merely `summary["status"]`. The summary can say `done` while the board was never touched, which is exactly the bug this assertion exists to catch.
2. **Every agent phase ran.** All seven phases have a recorded attempt with status `ok` in the `Store` for this run id.
3. **cwd is the worktree (R6, D7).** Each logged cwd equals `cli.worktree_for(root, branch)` = `<repo>/.claude/worktrees/<branch>`, *including explore*.
4. **Clean worktree.** `git status --porcelain` in the worktree is empty after the run: no result file, no prompt, no stray artifact was written inside it.
5. **No side channel.** The fake resolved its result path purely from prompt text; this is structural (the fake has no other source) rather than a separate assertion, but the test asserts the rendered prompt on disk actually contains the result path and the schema, so a regression in prompt composition fails with a clear message rather than as a mysterious missing-result-file.

## Error paths

- Fake not on `PATH`, or not executable: the run fails with a harness error. Not asserted as a behaviour; the fixture makes it impossible.
- Any phase failing a gate surfaces as `summary["status"] != "done"` with `failed_phase` and `detail`; the assertions above are written so the failure message names the phase, not just "expected done, got escalated".
- `run_card` raising `ParentlessCardError` if the fixture chain is wrong — a fixture bug, caught by the chain fixture itself.
- Timeouts: the fake is instantaneous; the default dispatch timeout applies unchanged.

## Suite placement

`tests/e2e/test_production_wiring.py` runs in the **default suite**: it launches no model, costs nothing, and must pass under the current `addopts = "--import-mode=importlib"` with `testpaths = ["tests"]`. **No `e2e` marker and no skip** on this test. Registering the `e2e` marker and adding `-m "not e2e"` to `addopts` is the sibling 34d3388b's job, and when it lands, this test must remain unmarked so it keeps running by default.

## Tests

Per the placement rule (design §14, lines 477-492; applied in the docstrings of `tests/test_cli.py:1-13` and `tests/test_store.py:1-11`), tiers are defined by the kind of code under test. None of the five existing tiers covers "the production wiring with a fake process", so the card assigns these to a new **production-wiring tier** in `tests/e2e/`, built on **Steps-tier fixtures** (real temp git repo, real temp brd board, `XDG_DATA_HOME` in `tmp_path`). Every test below states its tier explicitly.

1. `test_run_card_drives_every_phase_under_a_fake_claude_and_the_board_says_done` — production-wiring tier (tests/e2e). One `run_card` call, asserts (1) board status `done` and (2) seven `ok` agent attempts in the Store.
2. `test_every_agent_ran_in_the_subtask_worktree` — production-wiring tier. Reads the fake's cwd log; asserts every phase, explore included, ran in `worktree_for(root, branch)`.
3. `test_the_worktree_is_clean_after_the_run` — production-wiring tier. `git status --porcelain` empty; no result file or prompt landed inside the worktree.
4. `test_the_brief_carries_the_result_path_and_the_schema` — production-wiring tier. Reads the rendered `prompt.txt` off the attempt directory and asserts it states the absolute result path and embeds the model's JSON Schema (addendum R2). This is the assertion that makes the no-side-channel property explicit rather than incidental.
5. `test_the_spec_and_plan_documents_exist_where_the_phases_declared_them` — production-wiring tier. The `writes:` templates resolved to real files inside the worktree and were committed.
6. `test_the_implement_commit_carries_a_plan_hash_trailer_review_agrees_with` — production-wiring tier. The branch's commits all carry `Plan-Hash`, and `implement`'s and `review`'s reported hashes match, so `plan_hash_gate` passed for a real reason rather than vacuously.

The fake executable's prompt-parsing helpers, if factored out as importable pure functions, get **unit tests** (pure functions tier) in `tests/e2e/test_fake_claude.py`: parsing the path out of the `-p` sentence, and generating a schema-conforming payload from a given JSON Schema. Keep them small; the fake is test infrastructure, not a second product.

## Seams and amendments

Re-verified against the current base: the seams story has landed most of what this card was written to expose. Re-read the code before writing the test and treat anything still missing as a genuine gap to flag.

- Landed: `results.RESULT_MODELS` is populated (`SpecResult` included); `rollup.set_status` is the real step; the `worktree` phase is now first in `workflow/builtin/task.yaml`, so explore runs in an existing worktree; `prompt.py` composes a `## Result contract` section (result path and `model_json_schema()`); the reducers read both snake_case and camelCase (`_either_field`), so the fake, generating names from the embedded schema, works with either.
- Also landed: `critic_blockers_gate` is bound to `reducers.critic_blockers_gate` and the `spec` phase declares `result: SpecResult`. Re-confirm before writing; if anything regressed, flag it.
- `mark_in_progress`/`mark_done` are `best_effort: true`, so a run can report `done` while the board never changed. That is why assertion (1) reads the board (R7).
- Phase order: `plan_check` sits between `mark_in_progress` and `spec` and skips to `implement` only when a validated plan already exists; the fixture repo has none, so all seven agent phases run. `verify` is deterministic and runs `commands` for real.

**Expect failures first if any seam is incomplete.** Do not make the test pass by weakening an assertion, relaxing a gate, or handing the fake a side channel. Fixing `src/` is the seams story's job; flag a genuine gap rather than drift.

---

# Production-wiring test under a fake claude — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove `cli.run_card`'s production wiring — real `default_runner_factory`, real `ClaudeAdapter`, real `run_direct`, a real child process — drives one subtask card to `done` on the board, entirely from briefs on disk, with a fake `claude` executable standing in for the model.

**Architecture:** A new `tests/e2e/` package holds three files: `fake_claude.py` (a stdlib-only script that parses its brief, generates a result from the schema embedded in that brief, and for `implement`/`review` actually drives git in its cwd), `conftest.py` (module-scoped Steps-tier fixtures — temp git repo + brd board, `XDG_DATA_HOME` in tmp, the fake first on `PATH`, and one shared `run_card` invocation), and `test_production_wiring.py` (the six assertions). The fake is copied to a tmp dir as an executable named `claude`; the adapter's bare `COMMAND = "claude"` (`src/agent_manager/harness/claude.py:26`) finds it on `PATH`, so nothing in `src/` is stubbed or injected.

**Tech Stack:** Python 3.12, pytest 9 (`--import-mode=importlib`), pydantic v2 (schemas only, via the briefs), real `git` and `brd` CLIs, stdlib `subprocess`/`hashlib`/`json`/`re` in the fake.

**Spec:** prepended verbatim above; source of truth is `docs/superpowers/specs/task-add-a-production-wiring-8a3922b9-design.md` in this worktree.

## Global Constraints

- Verification is exactly `uv run pytest`. There is no lint and no typecheck command (`CLAUDE.md`).
- `pyproject.toml [tool.pytest.ini_options]` stays `testpaths = ["tests"]` and `addopts = "--import-mode=importlib"`. Registering an `e2e` marker or adding `-m "not e2e"` is sibling 34d3388b's job and must NOT happen here.
- `tests/e2e/test_production_wiring.py` carries **no** marker and **no** unconditional skip: it must run in the default suite.
- The fake `claude` gets its result path and its JSON Schema from the brief text alone. No environment variable, no extra argv flag, no `import agent_manager`, no filename convention. Adding a side channel to make a test pass is a plan violation.
- The fake imports only the standard library, because it runs under `sys.executable` as a plain script.
- Tests mirror src under `tests/`; every test states its tier in its docstring (design §14 lines 477-492, as applied in `tests/test_cli.py:1-13`).
- Result files, prompts and logs live under `paths.data_dir()` (`src/agent_manager/paths.py:14-41`), never inside the worktree.
- Branch: `m2/task-add-a-production-wiring-8a3922b9`. Nothing from any other subtask's branch may be assumed present.

## Review Focus

- **A brief with no `## Result contract`.** The fake must die loudly with a message naming the missing section, not write a result to a guessed path — otherwise a prompt-composition regression shows up as "missing result file" (Task 2, Step 1).
- **A result model renames a field.** The fake's per-field overrides must refuse a name the embedded schema did not produce, so a rename fails the fake rather than producing a well-typed payload every gate then reads as `None` (Task 1, Step 7).
- **The adapter's `-p` sentence changes.** `prompt_path_from_argv` must raise on a sentence it does not recognise rather than silently returning a wrong path (Task 1, Step 1).
- **A schema node the generator has no default for.** `payload_from_schema` must raise rather than emit `null`, because `_Result` is `strict=True` and a silent `null` becomes an unexplained `schema_invalid` two layers away (Task 1, Step 5).
- **A retry appends a feedback block after the contract.** `dispatch._append_feedback` (`src/agent_manager/dispatch.py:86-97`) appends `## feedback on the previous attempt` *after* the Result contract, so the fake's contract parsing must still find the path and the schema on attempt 2 (Task 2, Step 5).

---

## File Structure

- Create `tests/e2e/fake_claude.py` — the fake model. Pure parsing helpers (`prompt_path_from_argv`, `phase_of`, `sections`, `result_path_of`, `schema_of`, `payload_from_schema`, `override`, `log_path`) plus `build_result` and `main`. No pytest import; it must run as a script.
- Create `tests/e2e/test_fake_claude.py` — pure-functions tier unit tests for those helpers, plus two subprocess-level tests of the whole script.
- Create `tests/e2e/conftest.py` — module-scoped fixtures: `module_monkeypatch`, `toolchain`, `project`, `cards`, `fake_claude_bin`, `completed_run`, `run_tree`, `agent_attempts`, `fake_log`.
- Create `tests/e2e/test_production_wiring.py` — the six production-wiring-tier tests.
- Modify `src/agent_manager/workflow/builtin/task.yaml:29-35` and `:43-48` — the one minimal src fix this test forces (Task 3).
- Modify `tests/workflow/test_builtin_task.py:128` and `tests/test_engine.py:1699,1701` — the expectations that pin those two `inputs` lists.

---

## Task 1: The fake's pure parsing and generation helpers

**Files:**
- Create: `tests/e2e/fake_claude.py`
- Test: `tests/e2e/test_fake_claude.py`

**Interfaces:**
- Consumes: nothing from earlier tasks. Mirrors two production strings it must keep in step with: `harness/claude.py:30` `PROMPT_INSTRUCTION` and `prompt.py:281` `RESULT_HEADING`.
- Produces, all importable from `tests/e2e/fake_claude.py`:
  - `FakeClaudeError(RuntimeError)`
  - `prompt_path_from_argv(argv: list[str]) -> pathlib.Path`
  - `phase_of(text: str) -> str`
  - `sections(text: str) -> dict[str, str]`
  - `result_path_of(text: str) -> pathlib.Path`
  - `schema_of(text: str) -> dict`
  - `payload_from_schema(schema: dict, defs: dict | None = None) -> dict`
  - `override(payload: dict, **fields) -> dict`
  - `log_path(result_path: pathlib.Path) -> pathlib.Path`
  - `LOG_NAME: str = "fake-claude.log"`, `SUMMARY: str`

- [ ] **Step 1: Write the failing unit tests for argv and header parsing**

Create `tests/e2e/test_fake_claude.py`:

```python
"""Pure-functions tier (design §14 lines 477-492) for the fake `claude`'s helpers.

`tests/e2e/fake_claude.py` is a script, not a package module: it is copied to a
tmp directory and executed as `claude` by the production-wiring tier. It is
loaded here by path rather than imported by name, because `tests/e2e` is not on
`sys.path` under `--import-mode=importlib`.
"""

import importlib.util
import json
from pathlib import Path

import pytest

_SOURCE = Path(__file__).with_name("fake_claude.py")
_spec = importlib.util.spec_from_file_location("e2e_fake_claude", _SOURCE)
assert _spec is not None and _spec.loader is not None
fake_claude = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fake_claude)


def test_the_prompt_path_is_parsed_out_of_the_adapters_p_sentence():
    argv = [
        "--model",
        "sonnet",
        "--dangerously-skip-permissions",
        "-p",
        "Read /tmp/run/explore.1/prompt.txt and follow the instructions in it "
        "exactly. It is your complete brief for this task.",
    ]

    assert fake_claude.prompt_path_from_argv(argv) == Path(
        "/tmp/run/explore.1/prompt.txt"
    )


def test_an_unrecognised_p_sentence_is_refused_rather_than_guessed():
    """Review focus: the adapter's sentence is a contract. A changed sentence
    must stop the fake, not make it read some other word as a path."""
    argv = ["-p", "do the thing described in /tmp/run/prompt.txt"]

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        fake_claude.prompt_path_from_argv(argv)

    assert "-p" in str(caught.value)


def test_argv_without_a_p_flag_is_refused():
    with pytest.raises(fake_claude.FakeClaudeError):
        fake_claude.prompt_path_from_argv(["--model", "sonnet"])


def test_the_phase_is_read_from_the_rendered_prompt_header():
    text = "# Reviewer\n\nstanding instructions\n\n# phase: review\n# role: reviewer\n\n## branch\nm1/task-x\n"

    assert fake_claude.phase_of(text) == "review"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/e2e/test_fake_claude.py -v`
Expected: collection error — `FileNotFoundError` / `spec_from_file_location` returning `None` for `tests/e2e/fake_claude.py`, because the file does not exist yet.

- [ ] **Step 3: Write the minimal fake with those three helpers**

Create `tests/e2e/fake_claude.py`:

```python
"""A fake `claude` executable: the model stand-in of the production-wiring tier.

Test infrastructure, not product code. It is copied to a tmp directory as an
executable named `claude` and put first on `PATH`, so `harness/claude.py`'s bare
`COMMAND = "claude"` resolves to it and the real adapter, the real
`launcher.run_direct` and a real child process all run unmodified.

Everything it needs comes out of the brief on disk: the prompt path from the
adapter's `-p` sentence (`harness/claude.py:30`), and the absolute result path
plus the JSON Schema from the `## Result contract` section the brief carries
(`prompt.py:281-374`). There is deliberately no environment variable, no extra
argv flag and no import of `agent_manager` -- a brief that omits the contract
must make this script fail, because that failure is the test's whole point.

Standard library only: it runs under a bare `#!<python>` line.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path


class FakeClaudeError(RuntimeError):
    """The fake cannot do its job from the brief it was given."""


PROMPT_SENTENCE = re.compile(
    r"^Read (?P<path>.+?) and follow the instructions in it exactly\."
)
"""The adapter's `-p` text (`harness/claude.py:30`), read backwards."""

PHASE_HEADER = re.compile(r"^# phase: (?P<phase>\S+)\n# role: (?P<role>\S+)$", re.M)
"""`prompt._assemble`'s two-line head (`prompt.py:267-269`)."""


def prompt_path_from_argv(argv):
    """The brief's path, out of the adapter's `-p` sentence."""
    if "-p" not in argv:
        raise FakeClaudeError(f"no -p argument in argv: {argv!r}")
    sentence = argv[argv.index("-p") + 1]
    found = PROMPT_SENTENCE.match(sentence)
    if found is None:
        raise FakeClaudeError(
            f"the -p text is not the adapter's instruction sentence: {sentence!r}"
        )
    return Path(found.group("path"))


def phase_of(text):
    """The phase name the rendered prompt's header states."""
    found = PHASE_HEADER.search(text)
    if found is None:
        raise FakeClaudeError("the brief carries no `# phase:`/`# role:` header")
    return found.group("phase")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/e2e/test_fake_claude.py -v`
Expected: 4 passed.

- [ ] **Step 5: Write the failing unit tests for the contract and schema helpers**

Append to `tests/e2e/test_fake_claude.py` (the outer fence here is four backticks because the code itself contains triple-backtick fences):

````python
CONTRACT = """# Explorer

standing instructions

## methodology: exploring.md

```json
{"not": "the schema"}
```

# phase: explore
# role: explorer

## repo_docs
path: /w/CLAUDE.md
2 line(s), shown in full:
## Verification
uv run pytest

## verification
[
  "uv run pytest"
]

## Result contract
When you are done, write your result as valid JSON to exactly this path:

/xdg/agent-manager/runs/r1/card/explore.1/result.json

That path is deliberately outside the worktree you are working in.

The JSON must validate against this schema:

```json
{
  "$defs": {
    "Verification": {
      "properties": {
        "full_suite": {"items": {"type": "string"}, "type": "array"},
        "typecheck": {"type": "string"},
        "lint": {"items": {"type": "string"}, "type": "array"}
      },
      "type": "object"
    }
  },
  "properties": {
    "refused": {"type": "boolean"},
    "reason": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    "summary": {"type": "string"},
    "verification": {"$ref": "#/$defs/Verification"}
  },
  "type": "object"
}
```
"""


def test_the_result_path_comes_out_of_the_contract_section():
    assert fake_claude.result_path_of(CONTRACT) == Path(
        "/xdg/agent-manager/runs/r1/card/explore.1/result.json"
    )


def test_the_schema_is_the_fence_inside_the_contract_not_an_earlier_one():
    """A methodology section may carry its own ```json fence; only the fence
    after `## Result contract` is the schema the engine will validate against."""
    schema = fake_claude.schema_of(CONTRACT)

    assert sorted(schema["properties"]) == [
        "reason",
        "refused",
        "summary",
        "verification",
    ]


def test_a_brief_with_no_result_contract_is_refused():
    """Review focus: addendum R2. No contract means no result path, and the fake
    must say so rather than invent one."""
    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        fake_claude.result_path_of("# phase: explore\n# role: explorer\n\n## branch\nx\n")

    assert "Result contract" in str(caught.value)


def test_sections_are_read_case_sensitively_from_below_the_phase_header():
    """`repo_docs` inlines an excerpt of a repo CLAUDE.md, which may carry its
    own `## Verification` heading -- that must not shadow the `## verification`
    input the gate compares against."""
    found = fake_claude.sections(CONTRACT)

    assert json.loads(found["verification"]) == ["uv run pytest"]
    assert "Verification" in found
    assert found["Verification"] != found["verification"]


def test_a_payload_is_generated_from_the_schema_alone():
    payload = fake_claude.payload_from_schema(fake_claude.schema_of(CONTRACT))

    assert payload == {
        "refused": False,
        "reason": None,
        "summary": "",
        "verification": {"full_suite": [], "typecheck": "", "lint": []},
    }


def test_a_schema_node_with_no_default_is_refused_rather_than_nulled():
    """Review focus: the result models are `strict=True`, so a silently nulled
    field would surface as an unexplained `schema_invalid` two layers away."""
    with pytest.raises(fake_claude.FakeClaudeError):
        fake_claude.payload_from_schema(
            {"properties": {"weird": {"type": "tuple"}}, "type": "object"}
        )
````

- [ ] **Step 6: Run them to verify they fail**

Run: `uv run pytest tests/e2e/test_fake_claude.py -v`
Expected: 6 failures, each `AttributeError: module 'e2e_fake_claude' has no attribute 'result_path_of'` (and `schema_of`, `sections`, `payload_from_schema`).

- [ ] **Step 7: Implement the contract, section and payload helpers**

Append to `tests/e2e/fake_claude.py` (four-backtick outer fence again — the code contains a triple-backtick literal):

````python
RESULT_HEADING = "## Result contract"
"""`prompt.RESULT_HEADING` (`prompt.py:281`), matched as a literal."""

RESULT_PATH_LEAD = "write your result as valid JSON to exactly this path:"
"""The sentence `prompt._result_contract` puts immediately before the path."""

SCHEMA_FENCE = re.compile(r"```json\n(?P<schema>.*?)\n```", re.S)

SECTION = re.compile(r"^## (?P<name>.+)$", re.M)
"""Any `## ` heading. Names are taken verbatim and matched case-sensitively:
the `repo_docs` body inlines a repo CLAUDE.md that may carry its own
`## Verification` heading, and that must not be read as the `verification`
input."""


def _contract(text):
    start = text.find(RESULT_HEADING)
    if start < 0:
        raise FakeClaudeError(
            f"the brief has no {RESULT_HEADING!r} section, so it names no result "
            "path and no schema; this fake has no other way to learn either "
            "(addendum R2)"
        )
    return text[start:]


def result_path_of(text):
    """The absolute result path the contract names."""
    contract = _contract(text)
    lead = contract.find(RESULT_PATH_LEAD)
    if lead < 0:
        raise FakeClaudeError(
            f"the {RESULT_HEADING!r} section does not say {RESULT_PATH_LEAD!r}"
        )
    for line in contract[lead + len(RESULT_PATH_LEAD) :].splitlines():
        if line.strip():
            return Path(line.strip())
    raise FakeClaudeError("the result contract names no path after its lead line")


def schema_of(text):
    """The JSON Schema embedded in the contract, as a dict."""
    found = SCHEMA_FENCE.search(_contract(text))
    if found is None:
        raise FakeClaudeError("the result contract embeds no ```json schema fence")
    return json.loads(found.group("schema"))


def sections(text):
    """Every `## <name>` section below the `# phase:` header, first wins."""
    head = PHASE_HEADER.search(text)
    body = text[head.end() :] if head is not None else text
    marks = list(SECTION.finditer(body))
    found = {}
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(body)
        found.setdefault(mark.group("name"), body[mark.end() : end].strip("\n"))
    return found


def payload_from_schema(schema, defs=None):
    """A schema-shaped payload: every declared property at its type's zero value.

    Generated rather than hardcoded, so this fake cannot drift away from the
    models the run validates against. Values that a *gate* needs (a real
    summary, a real commit count) are set afterwards by `override`.
    """
    table = schema.get("$defs", {}) if defs is None else defs
    return {
        name: _value_for(node, table)
        for name, node in schema.get("properties", {}).items()
    }


def _value_for(node, defs):
    if "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        if name not in defs:
            raise FakeClaudeError(f"the schema references unknown $def {name!r}")
        return payload_from_schema(defs[name], defs)
    if "anyOf" in node:
        options = node["anyOf"]
        if any(option.get("type") == "null" for option in options):
            return None
        return _value_for(options[0], defs)
    kind = node.get("type")
    if kind == "object":
        return payload_from_schema(node, defs)
    if kind == "array":
        return []
    if kind == "string":
        return ""
    if kind in ("integer", "number"):
        return 0
    if kind == "boolean":
        return False
    raise FakeClaudeError(f"no default value for schema node {node!r}")


def override(payload, **fields):
    """Set each named field, refusing a name the schema did not produce.

    The refusal is the drift detector: if a result model renames a field, this
    fake stops instead of writing a well-typed payload that every gate then
    reads as `None`.
    """
    for name, value in fields.items():
        if name not in payload:
            raise FakeClaudeError(
                f"the embedded schema has no field {name!r} "
                f"(it has: {sorted(payload)})"
            )
        payload[name] = value
    return payload
````

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/e2e/test_fake_claude.py -v`
Expected: 10 passed.

- [ ] **Step 9: Add the drift-detector test for `override`**

Append to `tests/e2e/test_fake_claude.py`:

```python
def test_overriding_a_field_the_schema_does_not_have_is_refused():
    """Review focus: a renamed result-model field must stop the fake, not
    produce a payload whose gate silently reads `None`."""
    payload = {"summary": "", "blockers": False}

    with pytest.raises(fake_claude.FakeClaudeError) as caught:
        fake_claude.override(payload, findings=[])

    assert "findings" in str(caught.value)
    assert "blockers" in str(caught.value)
```

- [ ] **Step 10: Run it to verify it passes**

Run: `uv run pytest tests/e2e/test_fake_claude.py -v`
Expected: 11 passed.

- [ ] **Step 11: Commit**

```bash
git add tests/e2e/fake_claude.py tests/e2e/test_fake_claude.py
git commit -m "test: parsing helpers for the fake claude executable"
```

---

## Task 2: The fake as a runnable executable

**Files:**
- Modify: `tests/e2e/fake_claude.py` (append `SUMMARY`, `LOG_NAME`, `log_path`, `git`, `plan_hash_of`, `_document`, `build_result`, `main`, the `__main__` guard)
- Test: `tests/e2e/test_fake_claude.py`

**Interfaces:**
- Consumes: everything Task 1 produced.
- Produces:
  - `SUMMARY: str` — the prose every result reuses; longer than `reducers.MIN_SUMMARY_LENGTH` (60) and not in `PLACEHOLDER_SUMMARIES`.
  - `LOG_NAME: str = "fake-claude.log"`
  - `log_path(result_path: Path) -> Path` — `<run dir>/fake-claude.log`, i.e. `Path(result_path).parents[2] / LOG_NAME`.
  - `build_result(phase: str, payload: dict, text: str, cwd: Path) -> dict`
  - `main(argv: list[str]) -> int`

- [ ] **Step 1: Write the failing subprocess tests**

Append to `tests/e2e/test_fake_claude.py` (four-backtick outer fence: `_brief` writes a triple-backtick fence):

````python
import subprocess
import sys


def _brief(tmp_path, phase, role, body, schema, result_path):
    """A brief shaped like `prompt.compose_brief`'s output, written to disk."""
    text = (
        f"# {role}\n\nstanding instructions\n\n"
        f"# phase: {phase}\n# role: {role}\n"
        f"{body}\n"
        f"{fake_claude.RESULT_HEADING}\n"
        "When you are done, write your result as valid JSON to exactly this path:\n"
        "\n"
        f"{result_path}\n"
        "\n"
        "That path is deliberately outside the worktree you are working in.\n"
        "\n"
        "The JSON must validate against this schema:\n"
        "\n"
        "```json\n"
        f"{json.dumps(schema, indent=2)}\n"
        "```\n"
    )
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text(text, encoding="utf-8")
    return prompt_path


CRITIC_SCHEMA = {
    "properties": {
        "blockers": {"type": "boolean"},
        "reason": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "summary": {"type": "string"},
    },
    "type": "object",
}


def _run_fake(prompt_path, cwd):
    return subprocess.run(
        [
            sys.executable,
            str(_SOURCE),
            "--model",
            "sonnet",
            "--dangerously-skip-permissions",
            "-p",
            f"Read {prompt_path} and follow the instructions in it exactly. "
            "It is your complete brief for this task.",
        ],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def test_the_fake_writes_a_gate_passing_critic_result_where_the_brief_says(tmp_path):
    """Production-wiring tier's own fixture check: the script end to end, driven
    only by a brief on disk."""
    attempt = tmp_path / "runs" / "r1" / "card" / "validate_spec.1"
    attempt.mkdir(parents=True)
    result_path = attempt / "result.json"
    prompt_path = _brief(
        tmp_path,
        "validate_spec",
        "critic",
        "\n## spec_path\ndocs/superpowers/specs/x-00000001.md\n",
        CRITIC_SCHEMA,
        result_path,
    )

    completed = _run_fake(prompt_path, tmp_path)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["blockers"] is False
    assert payload["reason"] is None
    assert len(payload["summary"]) > 60


def test_the_fake_logs_its_phase_and_cwd_beside_the_run_directory(tmp_path):
    attempt = tmp_path / "runs" / "r1" / "card" / "validate_spec.1"
    attempt.mkdir(parents=True)
    result_path = attempt / "result.json"
    prompt_path = _brief(
        tmp_path, "validate_spec", "critic", "\n## spec_path\nx.md\n",
        CRITIC_SCHEMA, result_path,
    )
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    completed = _run_fake(prompt_path, workdir)

    assert completed.returncode == 0, completed.stderr
    log = tmp_path / "runs" / "r1" / fake_claude.LOG_NAME
    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert entry["phase"] == "validate_spec"
    assert Path(entry["cwd"]).resolve() == workdir.resolve()
    assert entry["result_path"] == str(result_path)


def test_a_brief_without_a_result_contract_makes_the_fake_exit_non_zero(tmp_path):
    """Review focus / addendum R2: no contract, no run. This is what makes the
    production-wiring test fail loudly if prompt composition ever regresses."""
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text(
        "# Critic\n\n# phase: validate_spec\n# role: critic\n\n## spec_path\nx.md\n",
        encoding="utf-8",
    )

    completed = _run_fake(prompt_path, tmp_path)

    assert completed.returncode == 1
    assert "Result contract" in completed.stderr


def test_a_feedback_block_after_the_contract_does_not_hide_the_contract(tmp_path):
    """Review focus: `dispatch._append_feedback` appends
    `## feedback on the previous attempt` AFTER the contract on a retry."""
    attempt = tmp_path / "runs" / "r1" / "card" / "validate_spec.2"
    attempt.mkdir(parents=True)
    result_path = attempt / "result.json"
    prompt_path = _brief(
        tmp_path, "validate_spec", "critic", "\n## spec_path\nx.md\n",
        CRITIC_SCHEMA, result_path,
    )
    prompt_path.write_text(
        prompt_path.read_text(encoding="utf-8")
        + "\n## feedback on the previous attempt\nthe result file was missing\n",
        encoding="utf-8",
    )

    completed = _run_fake(prompt_path, tmp_path)

    assert completed.returncode == 0, completed.stderr
    assert json.loads(result_path.read_text(encoding="utf-8"))["blockers"] is False
````

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/e2e/test_fake_claude.py -v -k "fake"`
Expected: FAIL — `returncode == 1` with stderr `AttributeError`/`NameError` for `main`, because `fake_claude.py` has no `__main__` entry point yet (and `AttributeError: module ... has no attribute 'LOG_NAME'` in the log test).

- [ ] **Step 3: Implement the phase behaviour and the entry point**

Append to `tests/e2e/fake_claude.py`:

```python
SUMMARY = (
    "the fake claude executable drove this phase from the brief on disk alone: "
    "it parsed the result contract, generated a payload from the embedded JSON "
    "Schema, and wrote it to exactly the path the contract named."
)
"""Longer than `reducers.MIN_SUMMARY_LENGTH` (60) and not one of
`reducers.PLACEHOLDER_SUMMARIES`, so `exploration_output_gate` passes."""

LOG_NAME = "fake-claude.log"
"""The cwd log, written beside the run directory -- under `paths.data_dir()`,
never inside the worktree, so the clean-worktree assertion stays meaningful."""


def log_path(result_path):
    """`<run dir>/fake-claude.log`, derived from the result path alone.

    `paths.attempt_dir` is `<run dir>/<card>/<phase>.<n>`, so the run directory
    is the result file's third parent. Derived, not configured: this fake gets
    nothing but the brief.
    """
    return Path(result_path).parents[2] / LOG_NAME


def git(cwd, *args):
    """Run one git command in `cwd`, raising `FakeClaudeError` on failure."""
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True
    )
    if completed.returncode != 0:
        raise FakeClaudeError(
            f"git {' '.join(args)} failed in {cwd}: "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )
    return completed.stdout


def plan_hash_of(path):
    """`reducers.is_plan_hash`'s shape: the first 8 hex chars of the sha256."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:8]


def _document(cwd, relative, kind):
    """Write the document the phase's `writes:` template declared."""
    path = Path(cwd) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {kind} for this card\n\n{SUMMARY}\n", encoding="utf-8")
    return path


def _section(found, name, phase):
    if name not in found:
        raise FakeClaudeError(
            f"the {phase!r} brief has no `## {name}` section "
            f"(it has: {sorted(found)})"
        )
    return found[name].strip()


def build_result(phase, payload, text, cwd):
    """The phase's result: the schema skeleton, with what the gates need set."""
    found = sections(text)
    if phase == "explore":
        suite = json.loads(_section(found, "verification", phase))
        override(
            payload["verification"], full_suite=suite, typecheck="", lint=[]
        )
        return override(
            payload,
            refused=False,
            reason=None,
            summary=SUMMARY,
            verification=payload["verification"],
        )
    if phase in ("validate_spec", "validate_plan"):
        return override(payload, blockers=False, reason=None, summary=SUMMARY)
    if phase == "spec":
        relative = _section(found, "spec_path", phase)
        _document(cwd, relative, "spec")
        return override(payload, path=relative, note=None)
    if phase == "plan":
        relative = _section(found, "plan_path", phase)
        _document(cwd, relative, "plan")
        return override(payload, path=relative, self_reviewed=True, note=None)
    if phase == "implement":
        relative = _section(found, "plan_path", phase)
        digest = plan_hash_of(Path(cwd) / relative)
        git(cwd, "add", "-A")
        git(cwd, "commit", "-m", f"feat: implement this card\n\nPlan-Hash: {digest}")
        return override(
            payload,
            blocked=False,
            blocked_reason=None,
            resumed=False,
            plan_hash=digest,
            report=SUMMARY,
        )
    if phase == "review":
        relative = _section(found, "plan_path", phase)
        base = _section(found, "base_branch", phase)
        revisions = git(cwd, "rev-list", f"{base}..HEAD").split()
        tagged = [
            revision
            for revision in revisions
            if "Plan-Hash:" in git(cwd, "show", "-s", "--format=%B", revision)
        ]
        return override(
            payload,
            findings=[],
            unresolved_blockers=[],
            fix_summary=SUMMARY,
            porcelain=git(cwd, "status", "--porcelain").strip(),
            commit_count=len(revisions),
            tagged_count=len(tagged),
            plan_hash=plan_hash_of(Path(cwd) / relative),
        )
    raise FakeClaudeError(f"no behaviour for phase {phase!r}")


def main(argv):
    """Read the brief, write the result, log the cwd. Exit code 0 on success."""
    text = prompt_path_from_argv(argv).read_text(encoding="utf-8")
    phase = phase_of(text)
    result_path = result_path_of(text)
    cwd = Path(os.getcwd())
    payload = build_result(phase, payload_from_schema(schema_of(text)), text, cwd)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    entry = {"phase": phase, "cwd": str(cwd), "result_path": str(result_path)}
    with log_path(result_path).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    # stdout is a log, never a channel (D4). Usage-free on purpose: the adapter
    # scans it with `parse_usage`, and inventing token counts here would
    # journal fiction.
    print(f"fake-claude ok phase={phase}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except FakeClaudeError as error:
        print(f"fake-claude: {error}", file=sys.stderr)
        sys.exit(1)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/e2e/test_fake_claude.py -v`
Expected: 15 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/fake_claude.py tests/e2e/test_fake_claude.py
git commit -m "test: the fake claude executable, driven only by its brief"
```

---

## Task 3: The fixtures, the first production-wiring test, and the seam it exposes

**Files:**
- Create: `tests/e2e/conftest.py`
- Create: `tests/e2e/test_production_wiring.py`
- Modify: `src/agent_manager/workflow/builtin/task.yaml:29-35`, `:43-48`
- Modify: `tests/workflow/test_builtin_task.py:128`
- Modify: `tests/test_engine.py:1699`, `:1701`

**Interfaces:**
- Consumes: `tests/e2e/fake_claude.py` (copied, not imported), `cli.run_card`, `cli.worktree_for`, `board.show`, `store.open_db` / `store.load_run`, `paths.run_dir`.
- Produces, as module-scoped fixtures in `tests/e2e/conftest.py`:
  - `module_monkeypatch -> pytest.MonkeyPatch`
  - `toolchain -> None` (skips the module when `git` or `brd` is missing)
  - `project -> Path` (git repo on `main` + brd board, `XDG_DATA_HOME` in tmp)
  - `cards -> dict[str, str]` with keys `milestone`, `story`, `subtask`
  - `fake_claude_bin -> Path` (the executable; prepends its dir to `PATH`)
  - `completed_run -> dict[str, Any]` (the `cli.run_card` payload)
  - `run_tree -> models.Run`
  - `agent_attempts -> dict[str, models.Attempt]`, keyed by phase name
  - `worktree -> Path` (`cli.worktree_for(project, branch)`, asserted equal to the payload's)
  - `fake_log -> Path`
  - `git(cwd: Path, *args: str) -> str` (module-level helper, used by the fixtures)
  - Module constants `VERIFY_COMMANDS: tuple[str, ...]`, `AGENT_PHASES: tuple[str, ...]`

- [ ] **Step 1: Write the fixtures**

Create `tests/e2e/conftest.py`:

```python
"""Production-wiring tier fixtures (spec "Fixtures"), shared with sibling 34d3388b.

Steps-tier ingredients (design §14 lines 477-492, as used by
`tests/test_cli.py:996-1052` and `tests/steps/test_worktree.py:47-62`): a real
temporary git repo, a real temporary brd board, and `XDG_DATA_HOME` inside
tmp, so `paths.data_dir()` and brd's own database never touch a developer's
home. Everything is module-scoped because one `cli.run_card` launches seven
real child processes and every test in a module reads the same finished run.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from agent_manager import cli, models, paths, store

FAKE_CLAUDE_SOURCE = Path(__file__).with_name("fake_claude.py")
"""The script copied to a tmp dir as the `claude` the adapter will find."""

FAKE_LOG_NAME = "fake-claude.log"
"""Must equal `fake_claude.LOG_NAME`; the script and the tests meet across a
process boundary, and `test_fake_claude.py` pins the script's own constant."""

VERIFY_COMMANDS = ("git rev-parse --verify HEAD",)
"""A real, green command for this toy repo. Non-empty, so `verification_gate`
passes without `allow_no_verification`, and long enough per element that
`exploration_output_gate`'s plausibility check is satisfied."""

AGENT_PHASES = (
    "explore",
    "spec",
    "validate_spec",
    "plan",
    "validate_plan",
    "implement",
    "review",
)
"""`builtin/task.yaml`'s seven agent phases, in document order."""


def git(cwd: Path, *args: str) -> str:
    """Run one git command for fixture setup or assertion, failing loudly."""
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def _add_card(root: Path, title: str, parent: str | None = None) -> str:
    argv = ["brd", "add", "--title", title]
    if parent is not None:
        argv += ["--parent", parent]
    completed = subprocess.run(
        argv, cwd=root, check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)["data"]["id"]


@pytest.fixture(scope="module")
def module_monkeypatch():
    """A module-scoped `monkeypatch`: the built-in one is function-scoped."""
    with pytest.MonkeyPatch.context() as patch:
        yield patch


@pytest.fixture(scope="module")
def toolchain() -> None:
    """Skip the whole module when the real CLIs this tier needs are missing."""
    for tool in ("git", "brd"):
        if shutil.which(tool) is None:
            pytest.skip(f"the {tool} CLI must be installed for the e2e tier")


@pytest.fixture(scope="module")
def project(tmp_path_factory, module_monkeypatch, toolchain) -> Path:
    """One directory that is both a real git repo on `main` and a real brd board."""
    base = tmp_path_factory.mktemp("e2e")
    module_monkeypatch.setenv("XDG_DATA_HOME", str(base / "xdg"))
    root = base / "project"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(root)],
        check=True,
        capture_output=True,
        text=True,
    )
    git(root, "config", "user.email", "tests@example.com")
    git(root, "config", "user.name", "agent-manager tests")
    git(root, "config", "commit.gpgsign", "false")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    git(root, "add", "README.md")
    git(root, "commit", "-m", "base")
    subprocess.run(
        ["brd", "init", "--name", "e2e-board"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    # brd leaves its `.gitignore`/`.brd` markers untracked; committing them keeps
    # the baseline clean, so the later porcelain check reflects only the run.
    git(root, "add", "-A")
    git(root, "commit", "-m", "brd init")
    return root


@pytest.fixture(scope="module")
def cards(project) -> dict[str, str]:
    """A milestone -> story -> subtask chain, the shape `run --card` requires."""
    milestone = _add_card(project, "Milestone 1: the production wiring")
    story = _add_card(project, "Prove the wiring under a fake claude", milestone)
    subtask = _add_card(project, "Drive run --card with a fake harness", story)
    return {"milestone": milestone, "story": story, "subtask": subtask}


@pytest.fixture(scope="module")
def fake_claude_bin(tmp_path_factory, module_monkeypatch) -> Path:
    """The fake `claude`, first on `PATH` and the only one the run can find."""
    bin_dir = tmp_path_factory.mktemp("fake-bin")
    launcher = bin_dir / "claude"
    launcher.write_text(
        f"#!{sys.executable}\n" + FAKE_CLAUDE_SOURCE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    module_monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return launcher


@pytest.fixture(scope="module")
def completed_run(project, cards, fake_claude_bin) -> dict[str, Any]:
    """One real `cli.run_card`, with NO `runner_factory`.

    That omission is the point: `cli.default_runner_factory` (`cli.py:588`)
    builds the real `dispatch.AgentRunner` with the real
    `harness/launcher.py:94` `run_direct`, which `Popen`s the real
    `ClaudeAdapter` argv -- resolved on `PATH` to `fake_claude_bin`.
    """
    return cli.run_card(
        cards["subtask"],
        repo_dir=project,
        base_branch="main",
        branch_prefix="m1",
        commands=list(VERIFY_COMMANDS),
    )


@pytest.fixture(scope="module")
def run_tree(project, completed_run) -> models.Run:
    """The run's `Store` projection, read back the way `status` reads it."""
    conn = store.open_db(project)
    try:
        run = store.load_run(conn, completed_run["run_id"])
    finally:
        conn.close()
    assert run is not None, completed_run["run_id"]
    return run


@pytest.fixture(scope="module")
def agent_attempts(run_tree) -> dict[str, models.Attempt]:
    """The last recorded attempt of each agent phase, keyed by phase name."""
    found: dict[str, models.Attempt] = {}
    for story in run_tree.stories:
        for subtask in story.subtasks:
            for phase in subtask.phases:
                if phase.kind == "agent" and phase.attempts:
                    found[phase.name] = phase.attempts[-1]
    return found


@pytest.fixture(scope="module")
def worktree(project, completed_run) -> Path:
    """The subtask worktree: `<repo>/.claude/worktrees/<branch>` (`cli.py:142`)."""
    path = cli.worktree_for(project, completed_run["branch"])
    assert path == Path(completed_run["worktree"])
    return path


@pytest.fixture(scope="module")
def fake_log(completed_run) -> Path:
    """The fake's cwd log, under the run directory and outside every worktree."""
    return paths.run_dir(completed_run["run_id"]) / FAKE_LOG_NAME
```

- [ ] **Step 2: Write the first failing production-wiring test**

Create `tests/e2e/test_production_wiring.py`:

```python
"""Production-wiring tier (spec "Tests"): `cli.run_card` with no injected
runner, the real `ClaudeAdapter`, the real `launcher.run_direct`, a real child
process -- and a fake `claude` first on `PATH` as the only stand-in.

None of the five tiers in design §14 lines 477-492 covers this, so the card
assigns it here. It launches no model and costs nothing, so it carries **no
marker and no skip** and runs in the default suite; registering an `e2e` marker
is sibling 34d3388b's job (spec "Suite placement").
"""

import hashlib
import json
import subprocess
from pathlib import Path

from agent_manager import board, prompt, results
from agent_manager.workflow import load_builtin

AGENT_PHASES = (
    "explore",
    "spec",
    "validate_spec",
    "plan",
    "validate_plan",
    "implement",
    "review",
)


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout


def test_run_card_drives_every_phase_under_a_fake_claude_and_the_board_says_done(
    project, completed_run, agent_attempts
):
    """R7: the card reads `done` ON THE BOARD, not merely in the payload.

    `mark_done` is `best_effort: true` (`builtin/task.yaml:75-79`), so a run can
    report `done` while the card never moved -- which is exactly the bug this
    assertion exists to catch.
    """
    assert completed_run["status"] == "done", (
        completed_run["failed_phase"],
        completed_run["detail"],
        completed_run["warnings"],
    )

    card = board.show(completed_run["card_id"], repo_dir=project)
    assert card.status == "done"

    assert sorted(agent_attempts) == sorted(AGENT_PHASES)
    for name in AGENT_PHASES:
        assert agent_attempts[name].status == "ok", (name, agent_attempts[name])
```

- [ ] **Step 3: Run it and read the failure**

Run: `uv run pytest tests/e2e/test_production_wiring.py -v`
Expected: FAIL. `completed_run["status"] == "escalated"`, `failed_phase == "spec"`, and `detail` contains `the harness exited 1`. The stdout log of that attempt (`<XDG>/agent-manager/runs/<run id>/<card>/spec.1/stdout.log`) reads: fake-claude: the 'spec' brief has no `## spec_path` section (it has: [...]).

This is the seam this card exists to expose, and it is **not** in the spec's "Landed" list. Confirm it by reading the document: `src/agent_manager/workflow/builtin/task.yaml:29-34` gives the `spec` phase `inputs: [card, explore]` and `writes: docs/superpowers/specs/{stem}.md`, and `:43-48` gives `plan` `inputs: [spec_path]` and `writes: docs/superpowers/plans/{stem}.md`. `prompt._assemble` (`src/agent_manager/prompt.py:267-269`) renders only declared `inputs`, and nothing renders `writes:`. So the two phases that must write a document are the only two phases never told where it goes, while `implement` and `review` (`:60`, `:66`) are handed `plan_path` and are expected to read the very file `plan` was supposed to produce. Do not paper over this by teaching the fake to re-derive the stem from the card: `dag.task_stem` plus a hardcoded `docs/superpowers/specs/` directory would be exactly the side channel the spec forbids.

- [ ] **Step 4: Make the minimal src fix — declare the document paths as inputs**

In `src/agent_manager/workflow/builtin/task.yaml`, change the `spec` phase's inputs line from:

```yaml
    inputs: [card, explore]
```

to:

```yaml
    inputs: [card, explore, spec_path]
```

and the `plan` phase's inputs line from:

```yaml
    inputs: [spec_path]
```

to:

```yaml
    inputs: [spec_path, plan_path]
```

Nothing else changes. `prompt._TABLE` already resolves both names (`src/agent_manager/prompt.py:231-232`), and `engine._document_paths` (`src/agent_manager/engine.py:120-148`) already computes both for the whole subtask by scanning every phase's inputs, so the new declarations bind against values that were already in the context.

- [ ] **Step 5: Update the two expectations that pin those input lists**

In `tests/workflow/test_builtin_task.py`, in `test_spec_declares_the_spec_result_and_still_writes_the_specs_document`, change line 128 from:

```python
    assert phase.inputs == ["card", "explore"]
```

to:

```python
    # `spec_path` is declared as an input as well as a `writes:` template: the
    # template is how the engine derives the path, and the input is how the
    # agent is told it (nothing renders `writes:` into a brief).
    assert phase.inputs == ["card", "explore", "spec_path"]
```

In `tests/test_engine.py`, in `test_each_agent_phase_receives_exactly_the_inputs_it_declares`, change lines 1699 and 1701 from:

```python
        "spec": ("card", "explore"),
```
```python
        "plan": ("spec_path",),
```

to:

```python
        "spec": ("card", "explore", "spec_path"),
```
```python
        "plan": ("spec_path", "plan_path"),
```

- [ ] **Step 6: Run the affected suites to verify they pass**

Run: `uv run pytest tests/workflow/test_builtin_task.py tests/test_engine.py tests/e2e -v`
Expected: PASS, including `test_run_card_drives_every_phase_under_a_fake_claude_and_the_board_says_done`.

- [ ] **Step 7: Commit**

```bash
git add tests/e2e/conftest.py tests/e2e/test_production_wiring.py \
        src/agent_manager/workflow/builtin/task.yaml \
        tests/workflow/test_builtin_task.py tests/test_engine.py
git commit -m "test: drive run --card end to end under a fake claude

The production-wiring test found a real gap and it is flagged here rather than
worked around: the spec and plan phases each declare a writes: template but no
document-path input, and nothing renders writes: into a brief, so the two
phases that must write a document were the only ones never told where. The fix
is two words of YAML -- spec_path on spec, plan_path on plan -- both already
resolvable by prompt._TABLE and already computed by engine._document_paths."
```

---

## Task 4: cwd and clean-worktree assertions

**Files:**
- Modify: `tests/e2e/test_production_wiring.py`

**Interfaces:**
- Consumes: the `worktree`, `fake_log` and `completed_run` fixtures from Task 3's `tests/e2e/conftest.py`.
- Produces: nothing other tasks consume.

- [ ] **Step 1: Write the two failing tests**

Append to `tests/e2e/test_production_wiring.py`:

```python
def test_every_agent_ran_in_the_subtask_worktree(fake_log, worktree):
    """R6 / D7: every agent phase, explore included, is dispatched with the
    subtask worktree as its cwd. Read off the fake's own log, which is the only
    witness of where the child process actually stood."""
    entries = [
        json.loads(line)
        for line in fake_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert {entry["phase"] for entry in entries} == set(AGENT_PHASES)
    for entry in entries:
        assert Path(entry["cwd"]).resolve() == worktree.resolve(), entry


def test_the_worktree_is_clean_after_the_run(worktree):
    """Design §14 / D4: run state lives under `paths.data_dir()`. A result file,
    a prompt or a stdout log inside the worktree would show up here."""
    assert _git(worktree, "status", "--porcelain") == ""
    assert list(worktree.rglob("result.json")) == []
    assert list(worktree.rglob("prompt.txt")) == []
    assert list(worktree.rglob("stdout.log")) == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/e2e/test_production_wiring.py -v -k "worktree"` **before** pasting them in.
Expected: `no tests ran` for those two names — they do not exist yet. Then paste them and run again: if either fails, the failure is real (a stray artifact inside the worktree, or a phase whose cwd is not the worktree) and must be diagnosed, never silenced by loosening the comparison.

- [ ] **Step 3: Run the module to verify they pass**

Run: `uv run pytest tests/e2e/test_production_wiring.py -v`
Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test_production_wiring.py
git commit -m "test: every agent ran in the worktree and left it clean"
```

---

## Task 5: The brief carries the result path and the schema

**Files:**
- Modify: `tests/e2e/test_production_wiring.py`

**Interfaces:**
- Consumes: the `agent_attempts` fixture (`dict[str, models.Attempt]`) from Task 3's conftest; `prompt.RESULT_HEADING`; `results.RESULT_MODELS`; `load_builtin("task")`.
- Produces: nothing other tasks consume.

- [ ] **Step 1: Write the failing test**

Append to `tests/e2e/test_production_wiring.py`:

```python
def test_the_brief_carries_the_result_path_and_the_schema(agent_attempts):
    """Addendum R2: the brief is one on-disk document that states the absolute
    result path and embeds `model_json_schema()`.

    The fake has no other source for either, so this assertion is what turns a
    prompt-composition regression into a named failure instead of a mysterious
    missing result file.
    """
    workflow = load_builtin("task")

    for name in AGENT_PHASES:
        attempt = agent_attempts[name]
        text = Path(attempt.prompt_path).read_text(encoding="utf-8")
        declared = workflow.phase(name).result
        model = results.RESULT_MODELS[declared]
        schema = json.dumps(
            model.model_json_schema(), indent=2, ensure_ascii=False
        )

        assert prompt.RESULT_HEADING in text, name
        assert str(attempt.result_path) in text, name
        assert schema in text, name
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/e2e/test_production_wiring.py::test_the_brief_carries_the_result_path_and_the_schema -v`
Expected: PASS is the likely outcome here, because `prompt.compose_brief` already emits the contract; run it first anyway and, if it fails, read which of the three assertions fired — a missing `RESULT_HEADING` means the contract regressed, a missing schema means `_result_contract` stopped embedding `model_json_schema()`. To see the test is not vacuous, temporarily change `prompt.RESULT_HEADING in text` to `"## No such heading" in text`, run, observe FAIL, and revert.

- [ ] **Step 3: Run the module to verify everything passes**

Run: `uv run pytest tests/e2e/test_production_wiring.py -v`
Expected: 4 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test_production_wiring.py
git commit -m "test: the brief states the result path and embeds the schema"
```

---

## Task 6: Documents at the declared paths, and Plan-Hash agreement

**Files:**
- Modify: `tests/e2e/test_production_wiring.py`

**Interfaces:**
- Consumes: `agent_attempts`, `worktree`, `project`, `completed_run` from Task 3's conftest; `prompt.expand_writes(template, card, *, phase, input_name) -> str`; `board.show`.
- Produces: nothing other tasks consume.

- [ ] **Step 1: Write the two failing tests**

Append to `tests/e2e/test_production_wiring.py`:

```python
def test_the_spec_and_plan_documents_exist_where_the_phases_declared_them(
    project, completed_run, worktree
):
    """The `writes:` templates resolved to real files inside the worktree and
    were committed. Paths come from the document and `prompt.expand_writes`,
    never from a convention retyped here."""
    workflow = load_builtin("task")
    card = board.show(completed_run["card_id"], repo_dir=project)
    spec_relative = prompt.expand_writes(
        workflow.phase("spec").writes, card, phase="spec", input_name="spec_path"
    )
    plan_relative = prompt.expand_writes(
        workflow.phase("plan").writes, card, phase="plan", input_name="plan_path"
    )

    assert (worktree / spec_relative).is_file()
    assert (worktree / plan_relative).is_file()

    tracked = _git(worktree, "ls-files").split("\n")
    assert spec_relative in tracked
    assert plan_relative in tracked


def test_the_implement_commit_carries_a_plan_hash_trailer_review_agrees_with(
    project, completed_run, worktree, agent_attempts
):
    """`review_gate` and `plan_hash_gate` passed for a real reason: the branch's
    commits all carry the trailer, and implement's hash is review's hash is the
    sha256 of the plan file on disk (`reducers.is_plan_hash`: 8 lowercase hex)."""
    workflow = load_builtin("task")
    card = board.show(completed_run["card_id"], repo_dir=project)
    plan_relative = prompt.expand_writes(
        workflow.phase("plan").writes, card, phase="plan", input_name="plan_path"
    )

    revisions = _git(worktree, "rev-list", "main..HEAD").split()
    assert revisions  # non-vacuity: a branch with no commits would pass emptily
    for revision in revisions:
        assert "Plan-Hash:" in _git(worktree, "show", "-s", "--format=%B", revision)

    implement = json.loads(
        Path(agent_attempts["implement"].result_path).read_text(encoding="utf-8")
    )
    review = json.loads(
        Path(agent_attempts["review"].result_path).read_text(encoding="utf-8")
    )
    expected = hashlib.sha256((worktree / plan_relative).read_bytes()).hexdigest()[:8]

    assert implement["plan_hash"] == expected
    assert review["plan_hash"] == expected
    assert review["porcelain"] == ""
    assert review["commit_count"] == len(revisions)
    assert review["tagged_count"] == review["commit_count"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/e2e/test_production_wiring.py -v -k "documents or plan_hash"`
Expected: both must be run and observed. If `test_the_spec_and_plan_documents_exist_where_the_phases_declared_them` fails on `tracked`, the documents exist but implement never committed them, which is a real failure of the fake's `implement` branch. If the hash assertions fail, implement and review disagree — again real, since `plan_hash_gate` only *warns* when counts are unusable and this test is what makes the gate non-vacuous.

- [ ] **Step 3: Run the module to verify everything passes**

Run: `uv run pytest tests/e2e/test_production_wiring.py -v`
Expected: 6 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test_production_wiring.py
git commit -m "test: the writes: documents landed and the Plan-Hash trailers agree"
```

---

## Task 7: Whole-suite verification and suite-placement guard

**Files:**
- Modify: `tests/e2e/test_production_wiring.py`
- Read-only: `pyproject.toml:28-33`

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing other tasks consume.

- [ ] **Step 1: Write the failing suite-placement test**

Append to `tests/e2e/test_production_wiring.py`:

```python
def test_this_module_runs_in_the_default_suite_unmarked(request):
    """Spec "Suite placement": this test costs nothing and must keep running by
    default. Sibling 34d3388b registers the `e2e` marker and adds
    `-m "not e2e"` to addopts; when it lands, nothing in THIS module may carry
    that marker, or the production wiring stops being checked on every run.

    Asserted against the collected node's markers rather than the file's text:
    a text scan would trip over its own assertion strings, and a marker applied
    from a conftest would not appear in this file at all.
    """
    assert {mark.name for mark in request.node.own_markers} == set()
    assert {mark.name for mark in request.node.parent.own_markers} == set()
```

- [ ] **Step 2: Prove the test is not vacuous, then run it green**

Temporarily add two lines at the top of `tests/e2e/test_production_wiring.py`, immediately after the imports:

```python
import pytest

pytestmark = pytest.mark.e2e
```

Run: `uv run pytest "tests/e2e/test_production_wiring.py::test_this_module_runs_in_the_default_suite_unmarked" -v`
Expected: FAIL — the module marker set is `{'e2e'}`, not `set()` (plus a `PytestUnknownMarkWarning`, since no such marker is registered).

Now remove those two lines again and re-run.
Expected: PASS.

- [ ] **Step 3: Confirm the pytest configuration is untouched**

Run: `git status --porcelain -- pyproject.toml && git log --oneline -3 -- pyproject.toml`
Expected: the status output is empty, and none of the three listed commits is one of this plan's `test:` commits. (There is no `origin` remote ref to diff against in this worktree.) `testpaths = ["tests"]` and `addopts = "--import-mode=importlib"` (`pyproject.toml:29,33`) must be unchanged.

- [ ] **Step 4: Run the whole suite**

Run: `uv run pytest`
Expected: PASS, with `tests/e2e/test_fake_claude.py` (11 tests + 4 subprocess tests) and `tests/e2e/test_production_wiring.py` (7 tests) collected without any `-m` filter.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_production_wiring.py
git commit -m "test: pin this module into the default suite, unmarked"
```

---

## Notes for the executor

- **The one src change in this plan is Task 3, Step 4, and it is a flag, not a drift.** Two words of YAML. If a reviewer disagrees, the alternative is not "make the fake smarter" (that is the forbidden side channel) — it is to escalate to the seams story 360cd141 with the Task 3, Step 3 diagnosis.
- **If any other seam turns out to be open** (the spec's "Landed" list is stale, `critic_blockers_gate` is a placeholder again, `worktree` is no longer the first phase), stop and report it with the failing phase and the attempt's `stdout.log` path. Do not relax a gate, do not weaken an assertion, do not give the fake a second source of truth.
- **Debugging a failing run:** everything is on disk under `<XDG_DATA_HOME>/agent-manager/runs/<run id>/<card id>/<phase>.<n>/` — `prompt.txt` is the exact brief the fake read, `stdout.log` holds the fake's stderr, `result.json` is what it wrote. Add `print(completed_run)` to see `failed_phase`, `detail` and `warnings`.
