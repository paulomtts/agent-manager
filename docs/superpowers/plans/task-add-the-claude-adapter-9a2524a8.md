<!-- task-pipeline: validated -->
# Card 9a2524a8 — Add the Claude adapter

Subtask of story b4a96f6d ("Roles, the harness adapter protocol and the Claude adapter"), milestone 352e955b. Narrows the agreed design in `docs/superpowers/specs/2026-09-23-agent-manager-design.md` (§8 lines 304-318, §14 lines 478-492, decisions D4/D6/D7 at lines 69-72) to one module. It invents no new design.

## Scope

**Owns exactly one module:** `src/agent_manager/harness/claude.py`, plus its unit tests at `tests/harness/test_claude.py`.

The module provides `ClaudeAdapter`: the first concrete implementation of the `HarnessAdapter` Protocol printed at §8 lines 306-313 and coded in `src/agent_manager/harness/base.py:75-98`. It satisfies the Protocol structurally — it inherits from nothing, since `HarnessAdapter` is a pure structural Protocol and is deliberately not `runtime_checkable`.

**Out of scope, and must not be touched or duplicated:**

- `harness/base.py` and `harness/launcher.py` — sibling card 55e503e0, done, and this card's `blocked_by`. Import `Usage` and the `Dispatch` shape; never re-declare them, never widen them.
- Role bundle loading and validation (`roles/loader.py`, `roles/bundles/*`) — sibling card 47bd4ee6, done. This card consumes their output only: an already-materialized prompt file on disk and, where a caller passes it, a model string that came from `policy.toml`'s `default_model["claude"]`. D6 forbids resolving any plugin or skill at run time, and this module resolves nothing.
- Launching. No `subprocess`, no `os.exec*`, no process, no timers in `claude.py`. Execution is `harness/launcher.py::run_direct`, injected by the engine (§14 line 485).
- Reading `result.json`. D4 puts that outside the worktree, in the engine, at §6 step 5. The adapter never opens it and never opens the prompt file either.
- Other harnesses (`codex.py`, `pi.py`), milestone orchestration, the `bwrap`/`container` launcher seams.

## Observable behaviour

### `name` and `capabilities`

`name == "claude"` — the same string a `Dispatch.harness` carries and the same key `Policy.default_model` is indexed by (`roles/loader.py:61-85`, `roles/bundles/coder/policy.toml` → `default_model.claude = "sonnet"`), so routing, policy lookup and journalling all agree on one spelling.

`capabilities` is a `frozenset[str]` of what the harness can genuinely do, for the engine's plan-time capability check (§8 lines 336-339). It is data only; methodology is never a capability — D6 makes methodology vendored prompt text, not a harness feature.

### `build_command(d: Dispatch) -> list[str]`

Pure: same `Dispatch` in, same argv out, no I/O, no clock, no environment read, no process. Returns an argv **list** of `str` — never a shell string, never nested lists, never `Path` objects (§5 line 252 is explicit that `shell_quote` does not port; the launcher hands the list straight to `subprocess` without a shell).

The argv encodes, and only encodes:

- The `claude` executable in non-interactive print mode (`-p`), because an attempt is a one-shot process under D1's stateless dispatch.
- `d.model`, passed through verbatim — the adapter picks no default and rewrites no model name. Defaulting is the role policy's job upstream.
- The materialized role prompt. `Dispatch.prompt_path` is a **path**, not inline text (`models.py:53-70`), and §7 lines 296-298 is explicit that a prompt references an on-disk artifact by absolute path rather than inlining it. So the prompt argument the adapter emits directs the harness to open `d.prompt_path`; the adapter does not read, stat or inline the file's contents, which is what keeps `build_command` pure and its unit tests free of fixture files.
- Permissions bypassed, per D7: v1 launches full-auto.

The argv carries **no cwd flag**: D7's "cwd pinned to the subtask worktree" is realised by the launcher being called with `cwd=d.cwd` (`LauncherFn.__call__`, `launcher.py:56-63`), and duplicating it as a flag would create a second source of truth that can disagree with the one the process actually starts in. The one path that does appear in the argv — `d.prompt_path` — appears absolute, so the command means the same thing regardless of where it is started from. `d.result_path` is never an argv element: D4 passes it to the harness inside the rendered prompt text upstream (§6 step 2-3), not as a command-line flag, so `build_command` neither reads nor emits it; the adapter only checks it is absolute (see Error paths) as a defensive sanity check on the `Dispatch` it was handed.

`d.timeout` is not an argv element either; it is the launcher's kill deadline.

### `parse_usage(stdout: str) -> Usage | None`

Reads the harness's own log text and returns what it cost. It **never raises**, for any input — empty string, megabytes of unrelated chatter, truncated mid-line, or numbers that are negative, non-numeric, infinite or NaN. D4 makes stdout a log and not a channel: an attempt that produced a valid result file but a usage-free log is a *successful* attempt, and a `parse_usage` that raised would turn a cosmetic log change into a failed run.

- No recognisable usage anywhere in the log → `None`.
- Some fields recognised → a `Usage` carrying exactly those, the rest left `None`. Partial reporting is expected and is not an error.
- A recognised field whose value cannot be a valid `Usage` field (negative, `inf`, `nan`, unparseable) is dropped rather than propagated; if dropping leaves nothing, the result is `None`.
- When a log reports usage more than once, the last report wins — it is the cumulative one at the end of the run.

Field names are `tokens_in`, `tokens_out`, `cost` and nothing else. `Usage` is frozen with `extra="forbid"` (`base.py:31-50`) and its names match `Attempt.tokens_in/tokens_out/cost` (`models.py:73-83`) one for one, so the engine's journalling stays a copy and never becomes a translation. `cost` is USD as a float.

## Error paths

`build_command` raises `ValueError` — not a bespoke exception class — in exactly the cases where no retry can help and a silently-wrong command would be worse than a loud stop:

- `d.harness != "claude"`: a dispatch routed to the wrong adapter. Building a Claude argv for a Codex dispatch would run the wrong program against a real worktree.
- `d.cwd`, `d.prompt_path` or `d.result_path` is not absolute: the harness starts in the worktree, and a relative result path would land the result file *inside* the worktree, breaking D4's "outside the worktree" guarantee and getting it committed.

Everything else `Dispatch` could get wrong is already refused by its own validators (`min_length=1` on `harness`/`model`/`role`, `timeout > 0`), so the adapter re-checks none of it.

`parse_usage` has no error path by construction — it returns `None` where another module would raise.

## Test list

All tests below are **unit tier**, per §14 line 484: "Adapters — `build_command` is pure and asserted per harness; the launcher is injected, so no harness is executed in unit tests." No test in this card spawns a process, invokes a real `claude` binary, touches a git worktree, writes a result file, or reaches the engine. Launcher behaviour is card 55e503e0's (`tests/harness/test_launcher.py`); canned-result-file scenarios are the engine card's (§14 line 486); a real harness run is the single opt-in end-to-end test (§14 lines 489-490). Tests live in `tests/harness/test_claude.py`, mirroring `src/agent_manager/harness/claude.py` per CLAUDE.md, and carry the tier-naming docstring pattern of `tests/harness/test_base.py:1-11`. `build_command` is asserted on its returned argv directly; `parse_usage` is asserted against literal stdout strings held in the test.

1. The adapter satisfies the Protocol structurally — every member of `HarnessAdapter.__protocol_attrs__` is present, and it inherits from no base class. (unit)
2. `name == "claude"` and it is the key `roles/bundles/coder/policy.toml`'s `default_model` uses, so policy lookup and dispatch routing cannot drift apart. (unit)
3. `capabilities` is a `frozenset[str]`. (unit)
4. `build_command` returns the full expected argv for a representative `Dispatch` — exact list equality, so the print-mode flag, the model, the prompt-path reference and the permission bypass are each pinned. (unit)
5. Every element of the returned argv is a `str`, and the return is a `list` — §5 line 252's no-shell-string rule. (unit)
6. `d.model` is passed through verbatim, including a model name the adapter has never heard of. (unit)
7. The argv references `d.prompt_path` as an absolute path and does **not** contain the prompt file's contents — asserted with a prompt file that exists on disk with known contents, proving the adapter did not read it. (unit)
8. The argv carries no cwd/working-directory flag and no timeout flag: both belong to the launcher call. (unit)
9. `build_command` is pure — two calls with the same `Dispatch` return equal argvs, and the `Dispatch` is unmutated afterwards. (unit)
10. `build_command` raises `ValueError` when `d.harness` is another harness. (unit)
11. `build_command` raises `ValueError` for a relative `cwd`, a relative `prompt_path` and a relative `result_path` (one case each). (unit)
12. `parse_usage` returns a fully populated `Usage` from a realistic Claude log containing input tokens, output tokens and a cost. (unit)
13. `parse_usage` returns `None` for a log with no usage at all, and for the empty string. (unit)
14. `parse_usage` returns a partial `Usage` when the log reports tokens but no cost. (unit)
15. `parse_usage` never raises on hostile input: truncated mid-line, a negative token count, a non-numeric count, and `inf`/`nan` costs — each returns `None` or a `Usage` with that field dropped, and no exception escapes. (unit)
16. `parse_usage` takes the last usage report when a log contains several. (unit)
17. `parse_usage`'s result validates as a `Usage` with `extra="forbid"` — the adapter produces no key outside `tokens_in`/`tokens_out`/`cost`. (unit)
18. `claude.py` imports no `subprocess`/`os.exec*` and calls nothing in `harness.launcher` — asserted on the module source/imports, pinning "the adapter never launches anything itself". (unit)

---

# Claude Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `ClaudeAdapter` in `src/agent_manager/harness/claude.py` — a pure `Dispatch` → `claude -p` argv builder plus a never-raising stdout usage parser — as the first concrete implementation of the `HarnessAdapter` Protocol.

**Architecture:** One module, one class, no inheritance: `ClaudeAdapter` satisfies `HarnessAdapter` structurally, since `base.HarnessAdapter` is a non-`runtime_checkable` Protocol. `build_command` is a pure function of its `Dispatch` — it validates two things `Dispatch` itself cannot (right harness, absolute paths), then returns a fixed-shape argv list; nothing in the module opens a file or starts a process, because the launcher (`harness/launcher.py`, already built and injected by the engine) owns execution. `parse_usage` scans the log text with three compiled regexes, converts each last-seen match through a dropping converter, and builds a `Usage` from whatever survived — returning `None` rather than raising, ever.

**Tech Stack:** Python 3 (stdlib `re`, `math`, `ast`, `tomllib`), Pydantic v2 (`Usage`, `Dispatch`), pytest, `uv`.

**Spec:** `docs/superpowers/specs/task-add-the-claude-adapter-9a2524a8-design.md` (reproduced verbatim above). Upstream source of truth: `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §8 lines 304-318, §14 lines 478-492, decisions D4/D6/D7 at lines 69-72.

## Global Constraints

- Branch `m1/task-add-the-claude-adapter-9a2524a8`, worktree `/home/paulomtts/Code/agent-manager/.claude/worktrees/m1/task-add-the-claude-adapter-9a2524a8`. Every path below is relative to that worktree root.
- Exactly two files are created: `src/agent_manager/harness/claude.py` and `tests/harness/test_claude.py`. No other file in the repo is modified — not `harness/base.py`, not `harness/launcher.py`, not `harness/__init__.py` (its docstring already says re-exporting adapters from the package would give two import paths for one name), not `roles/`.
- `claude.py` must not import `subprocess`, must not import `os`, and must not import anything from `agent_manager.harness.launcher`. Task 4 asserts this.
- `build_command` returns `list[str]` — argv, never a shell string, never `Path` objects inside the list (spec §5 line 252).
- `parse_usage` never raises for any `str` input.
- `Usage` field names are exactly `tokens_in`, `tokens_out`, `cost` (`base.py:48-50`); `Usage` is frozen with `extra="forbid"`.
- Tests are unit tier and live at `tests/harness/test_claude.py`, mirroring `tests/harness/test_base.py`. That directory holds no `__init__.py` and none is added. No test spawns a process, invokes a real `claude` binary, creates a git worktree, or writes a result file.
- Verification command for the repo: `uv run pytest`. There is no separate lint or typecheck command.

## Review Focus

- A JSON usage object reports `cache_creation_input_tokens` and `cache_read_input_tokens` alongside `input_tokens`; a naive substring match reads a cache counter as the prompt size and journals a wrong number. Covered by Task 3, Step 8.
- A multi-megabyte log (a chatty agent run is routinely that big) must parse promptly and without catastrophic backtracking — `parse_usage` is called once per attempt on the whole file. Covered by Task 3, Step 8.
- A log written with CRLF line endings, a thousands-separated count, or a human-readable `Cost: $0.31` summary instead of a JSON line must still yield the same numbers. Covered by Task 3, Step 8.
- A `prompt_path` containing spaces or a quote character must land in the argv verbatim, unescaped and unquoted — the launcher runs without a shell, so any quoting the adapter added would become part of the filename. Covered by Task 2, Step 7.
- A `model` string that begins with `-` (a pasted flag, a typo in `harness_map`) must still be emitted as its own argv element after `--model`, not silently dropped or merged. Covered by Task 2, Step 7.

---

### Task 1: The module, its identity and Protocol conformance

**Files:**
- Create: `src/agent_manager/harness/claude.py`
- Create: `tests/harness/test_claude.py`

**Interfaces:**
- Consumes: `agent_manager.harness.base.HarnessAdapter` (Protocol with `name`, `capabilities`, `build_command`, `parse_usage`), `agent_manager.harness.base.Usage`, `agent_manager.models.Dispatch`, `agent_manager.roles.loader.bundles_dir() -> Path`.
- Produces: `ClaudeAdapter` with class attributes `name: str = "claude"` and `capabilities: frozenset[str]`, and the two methods `build_command(self, d: Dispatch) -> list[str]` and `parse_usage(self, stdout: str) -> Usage | None` (bodies filled in by Tasks 2 and 3). Module constants `COMMAND: str` and `PROMPT_INSTRUCTION: str` are added in Task 2.

- [ ] **Step 1: Write the failing tests**

Create `tests/harness/test_claude.py`:

```python
"""Behaviour of the Claude harness adapter (design §8 lines 304-318, card
9a2524a8).

Unit tier per design §14 line 484 ("Adapters -- `build_command` is pure and
asserted per harness; the launcher is injected, so no harness is executed in
unit tests"). Nothing here spawns a process, runs a real `claude` binary,
creates a worktree or writes a result file: `build_command` is asserted on the
argv it returns, and `parse_usage` on literal log strings held in this file.
Launcher behaviour is `test_launcher.py`'s; canned result files belong to the
engine card (§14 line 486); a real harness run is the single opt-in end-to-end
test (§14 lines 489-490).
"""

import tomllib
from pathlib import Path

from agent_manager.harness.base import HarnessAdapter
from agent_manager.harness.claude import ClaudeAdapter
from agent_manager.models import Dispatch
from agent_manager.roles.loader import bundles_dir


def _dispatch(**overrides: object) -> Dispatch:
    """A representative dispatch: absolute paths, a real role, a real model."""
    fields: dict[str, object] = {
        "harness": "claude",
        "model": "sonnet",
        "role": "coder",
        "cwd": Path("/repo/wt/9a2524a8"),
        "prompt_path": Path("/runs/run-1/9a2524a8/implement.1/prompt.md"),
        "result_path": Path("/runs/run-1/9a2524a8/implement.1/result.json"),
        "timeout": 60.0,
    }
    fields.update(overrides)
    return Dispatch(**fields)


def test_the_adapter_satisfies_the_protocol_structurally():
    # `HarnessAdapter` is deliberately not runtime_checkable (base.py lines
    # 78-82), so conformance is asserted member by member -- and the adapter
    # inherits from nothing, which is the whole point of a structural Protocol.
    adapter = ClaudeAdapter()
    for member in HarnessAdapter.__protocol_attrs__:
        assert hasattr(adapter, member), member
    assert ClaudeAdapter.__bases__ == (object,)
    assert callable(adapter.build_command)
    assert callable(adapter.parse_usage)


def test_the_name_is_the_one_key_routing_and_policy_both_use():
    # `Dispatch.harness`, `Policy.default_model`'s key and the journalled
    # harness name are one spelling; a rename here would silently strand
    # `default_model.claude` in every bundle.
    assert ClaudeAdapter.name == "claude"
    policy_path = bundles_dir() / "coder" / "policy.toml"
    policy = tomllib.loads(policy_path.read_text(encoding="utf-8"))
    assert ClaudeAdapter.name in policy["default_model"]
    assert policy["default_model"][ClaudeAdapter.name] == "sonnet"


def test_capabilities_is_a_frozenset_of_non_empty_strings():
    # Data for the engine's plan-time capability check (§8 lines 336-339).
    # Frozen because it is class-level shared state, and a phase's `needs:`
    # check must not be able to mutate what a harness claims.
    assert isinstance(ClaudeAdapter.capabilities, frozenset)
    assert ClaudeAdapter.capabilities
    for capability in ClaudeAdapter.capabilities:
        assert isinstance(capability, str) and capability.strip()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/harness/test_claude.py -v`
Expected: FAIL at collection — `ModuleNotFoundError: No module named 'agent_manager.harness.claude'`.

- [ ] **Step 3: Write the minimal implementation**

Create `src/agent_manager/harness/claude.py`:

```python
"""The Claude adapter: one `Dispatch` in, one `claude -p` argv out (card
9a2524a8).

Design §8 lines 306-313 prints the `HarnessAdapter` Protocol; this module is
its first concrete implementation. It satisfies that Protocol structurally and
inherits from nothing -- the Protocol is pure interface and deliberately not
`runtime_checkable` (`base.py` lines 75-92), so a base class would buy nothing
and would invite default implementations the other adapters do not want.

Two rules shape everything here. `build_command` is pure: it reads no file,
starts no process and consults no clock, so `launcher.py` (injected by the
engine, §14 line 485) stays the only thing in the program that runs anything.
And `parse_usage` never raises: D4 makes stdout a log rather than a channel, so
a chatty, truncated or usage-free log must not fail an attempt that produced a
perfectly good result file.
"""

from agent_manager.harness.base import Usage
from agent_manager.models import Dispatch


class ClaudeAdapter:
    """Turns a `Dispatch` into a `claude -p` argv and reads usage back out."""

    name = "claude"
    """The key `Dispatch.harness` carries and `Policy.default_model` is indexed
    by (`roles/bundles/coder/policy.toml` -> `default_model.claude`). One
    spelling, so routing, policy lookup and journalling cannot drift apart."""

    capabilities = frozenset({"bash", "edit"})
    """What this harness can genuinely do, for the plan-time capability check
    (§8 lines 336-339).

    `browser` is deliberately absent: Claude Code only drives a browser through
    an extension that a headless runner is not guaranteed to have, and claiming
    it would make the capability check pass for a phase that then cannot run.
    Claiming less than the truth refuses a phase early, which is the safe
    direction. Methodology is never listed -- D6 makes it vendored prompt text.
    """

    def build_command(self, d: Dispatch) -> list[str]:
        raise NotImplementedError  # Task 2

    def parse_usage(self, stdout: str) -> Usage | None:
        raise NotImplementedError  # Task 3
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/harness/test_claude.py -v`
Expected: PASS — 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/harness/claude.py tests/harness/test_claude.py
git commit -m "feat(harness): add ClaudeAdapter identity and protocol conformance"
```

---

### Task 2: `build_command`

**Files:**
- Modify: `src/agent_manager/harness/claude.py` (replace the `build_command` stub; add `COMMAND` and `PROMPT_INSTRUCTION` module constants)
- Test: `tests/harness/test_claude.py` (append)

**Interfaces:**
- Consumes: `ClaudeAdapter.name` from Task 1; `agent_manager.models.Dispatch` fields `harness: str`, `model: str`, `cwd: Path`, `prompt_path: Path`, `result_path: Path`, `timeout: float`.
- Produces: `ClaudeAdapter.build_command(self, d: Dispatch) -> list[str]`, raising `ValueError` on a foreign harness or any relative path. Module constants `COMMAND = "claude"` and `PROMPT_INSTRUCTION` (a `str.format` template with one `{path}` field). Task 4 reads neither, but the engine card will call `build_command` and hand the result to `LauncherFn(argv, cwd=d.cwd, timeout=d.timeout, stdout_path=...)`.

- [ ] **Step 1: Write the failing tests for the happy path**

Append to `tests/harness/test_claude.py`:

```python
EXPECTED_PROMPT_ARGUMENT = (
    "Read /runs/run-1/9a2524a8/implement.1/prompt.md and follow the "
    "instructions in it exactly. It is your complete brief for this task."
)


def test_build_command_returns_the_whole_expected_argv():
    # Exact equality, so the print-mode flag, the model, the permission bypass
    # and the prompt-path reference are each pinned: this argv is what actually
    # runs against a real worktree.
    assert ClaudeAdapter().build_command(_dispatch()) == [
        "claude",
        "--model",
        "sonnet",
        "--dangerously-skip-permissions",
        "-p",
        EXPECTED_PROMPT_ARGUMENT,
    ]


def test_the_argv_is_a_list_of_plain_strings():
    # §5 line 252: an argv list, never a shell string, and never `Path`
    # objects -- the launcher hands this straight to subprocess without a
    # shell.
    argv = ClaudeAdapter().build_command(_dispatch())
    assert isinstance(argv, list)
    for word in argv:
        assert type(word) is str


def test_the_model_is_passed_through_verbatim():
    # The adapter picks no default and rewrites no model name; defaulting is
    # the role policy's job upstream, so an unknown alias must survive intact.
    argv = ClaudeAdapter().build_command(_dispatch(model="a-model-that-ships-in-2030"))
    assert argv[argv.index("--model") + 1] == "a-model-that-ships-in-2030"


def test_the_prompt_is_referenced_by_absolute_path_and_never_inlined(tmp_path):
    # A real file with known contents: if the adapter ever read it, the
    # contents would show up in the argv. Referencing beats inlining (§7 lines
    # 296-298) and is what keeps build_command pure.
    prompt = tmp_path / "prompt.md"
    prompt.write_text("SECRET-PROMPT-BODY\n", encoding="utf-8")
    argv = ClaudeAdapter().build_command(_dispatch(prompt_path=prompt))
    assert str(prompt) in argv[-1]
    assert Path(str(prompt)).is_absolute()
    assert "SECRET-PROMPT-BODY" not in " ".join(argv)


def test_the_argv_carries_no_cwd_and_no_timeout():
    # D7's "cwd pinned to the worktree" is the launcher's `cwd=d.cwd`, and the
    # timeout is its kill deadline. A flag here would be a second source of
    # truth that can disagree with the directory the process starts in.
    d = _dispatch()
    argv = ClaudeAdapter().build_command(d)
    joined = " ".join(argv)
    for forbidden in ("--cwd", "--add-dir", "--directory", "-C", "--timeout"):
        assert forbidden not in argv
    assert str(d.cwd) not in joined
    assert str(d.result_path) not in joined
    assert "60" not in joined


def test_build_command_is_pure():
    # Same dispatch in, same argv out, and the dispatch comes back untouched:
    # the engine reuses one Dispatch across a retry.
    d = _dispatch()
    before = d.model_dump()
    first = ClaudeAdapter().build_command(d)
    second = ClaudeAdapter().build_command(d)
    assert first == second
    assert d.model_dump() == before
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/harness/test_claude.py -v`
Expected: FAIL — the six new tests raise `NotImplementedError`; the three from Task 1 still pass.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/harness/claude.py`, add the two constants directly below the imports:

```python
COMMAND = "claude"
"""The executable. A bare name, resolved on PATH by the launcher's Popen: the
adapter has no business knowing where a machine installed its harness."""

PROMPT_INSTRUCTION = (
    "Read {path} and follow the instructions in it exactly. It is your "
    "complete brief for this task."
)
"""The `-p` argument: a pointer at the materialized prompt, not the prompt.

§7 lines 296-298 pass documents by path rather than inlining them, and D4 puts
the result path inside that rendered prompt text upstream -- so this one
sentence is the whole bridge between the file the engine wrote and the process
the launcher starts. Inlining the file instead would re-bill it, invite a stale
copy, and make `build_command` read the disk.
"""
```

and replace the `build_command` stub with:

```python
    def build_command(self, d: Dispatch) -> list[str]:
        """The argv for one attempt. Pure: no disk, no clock, no process.

        The two checks are the ones `Dispatch` cannot make for itself. Both are
        `ValueError` rather than a bespoke class because no retry can fix
        either, and neither is a harness failure to journal -- they are bugs
        above the adapter.
        """
        if d.harness != self.name:
            raise ValueError(
                f"dispatch is routed to harness {d.harness!r}, not "
                f"{self.name!r}: building a {self.name!r} argv for it would run "
                f"the wrong program against a real worktree"
            )
        for field in ("cwd", "prompt_path", "result_path"):
            path = getattr(d, field)
            if not path.is_absolute():
                raise ValueError(
                    f"Dispatch.{field} must be absolute, got {str(path)!r}: the "
                    f"harness starts in the worktree, so a relative path would "
                    f"resolve inside it -- and a result file written there gets "
                    f"committed (D4 keeps it outside)"
                )
        return [
            COMMAND,
            "--model",
            d.model,
            # D7: v1 launches full-auto. Confinement is the launcher's seam,
            # not a flag the adapter negotiates.
            "--dangerously-skip-permissions",
            "-p",
            PROMPT_INSTRUCTION.format(path=d.prompt_path),
        ]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/harness/test_claude.py -v`
Expected: PASS — 9 passed.

- [ ] **Step 5: Write the failing tests for the error paths**

Add `import pytest` to the import block at the top of `tests/harness/test_claude.py` (after `import tomllib`, before `from pathlib import Path`), then append:

```python
def test_build_command_refuses_a_dispatch_for_another_harness():
    with pytest.raises(ValueError, match="codex"):
        ClaudeAdapter().build_command(_dispatch(harness="codex"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cwd", Path("wt/9a2524a8")),
        ("prompt_path", Path("prompt.md")),
        ("result_path", Path("result.json")),
    ],
)
def test_build_command_refuses_a_relative_path(field, value):
    # A relative result path lands the result file inside the worktree and gets
    # it committed, which is exactly what D4 keeps outside.
    with pytest.raises(ValueError, match=field):
        ClaudeAdapter().build_command(_dispatch(**{field: value}))
```

- [ ] **Step 6: Run the tests to verify they fail**

Run: `uv run pytest tests/harness/test_claude.py -v`
Expected: PASS already for `test_build_command_refuses_a_dispatch_for_another_harness` and the three parametrized cases — the implementation in Step 3 included both guards. If any fails, fix the implementation before continuing; do not weaken the test.

- [ ] **Step 7: Write the failing Review Focus tests**

Append to `tests/harness/test_claude.py`:

```python
def test_a_prompt_path_with_spaces_and_quotes_is_not_escaped():
    # The launcher runs without a shell, so any quoting the adapter added
    # would become part of the filename the harness tries to open.
    weird = Path("/runs/run 1/o'brien's plan/prompt.md")
    argv = ClaudeAdapter().build_command(_dispatch(prompt_path=weird))
    assert str(weird) in argv[-1]
    assert "\\" not in argv[-1]
    assert '"' not in argv[-1]


def test_a_model_that_looks_like_a_flag_stays_its_own_argv_element():
    # A pasted flag or a typo in `harness_map` must not merge into, or drop
    # out of, the argv -- argv elements are never re-split.
    argv = ClaudeAdapter().build_command(_dispatch(model="--help"))
    assert argv[:3] == ["claude", "--model", "--help"]
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/harness/test_claude.py -v`
Expected: PASS — 15 passed.

- [ ] **Step 9: Commit**

```bash
git add src/agent_manager/harness/claude.py tests/harness/test_claude.py
git commit -m "feat(harness): build the claude -p argv from a Dispatch"
```

---

### Task 3: `parse_usage`

**Files:**
- Modify: `src/agent_manager/harness/claude.py` (replace the `parse_usage` stub; add `re`/`math`/`ValidationError` imports, three compiled patterns and two converters)
- Test: `tests/harness/test_claude.py` (append)

**Interfaces:**
- Consumes: `agent_manager.harness.base.Usage` (frozen, `extra="forbid"`, fields `tokens_in: int | None (ge=0)`, `tokens_out: int | None (ge=0)`, `cost: float | None (ge=0, allow_inf_nan=False)`).
- Produces: `ClaudeAdapter.parse_usage(self, stdout: str) -> Usage | None`, total over `str` — it raises for no input. Module-private `_TOKENS_IN`, `_TOKENS_OUT`, `_COST` (`re.Pattern[str]`), `_tokens(raw: str) -> int | None`, `_cost(raw: str) -> float | None`. The engine copies the returned fields straight onto `Attempt.tokens_in/tokens_out/cost`.

- [ ] **Step 1: Write the failing tests for the reporting paths**

Extend the top-level import of `agent_manager.harness.base` in `tests/harness/test_claude.py` to `from agent_manager.harness.base import HarnessAdapter, Usage`, then append:

```python
FULL_LOG = (
    "Reading the plan...\n"
    "Running the test suite.\n"
    '{"type":"result","subtype":"success","is_error":false,'
    '"result":"implemented","total_cost_usd":0.3142,'
    '"usage":{"cache_creation_input_tokens":0,"cache_read_input_tokens":0,'
    '"input_tokens":8123,"output_tokens":1544}}\n'
)


def test_a_full_usage_report_comes_back_whole():
    assert ClaudeAdapter().parse_usage(FULL_LOG) == Usage(
        tokens_in=8123, tokens_out=1544, cost=0.3142
    )


def test_a_log_with_no_usage_reports_nothing():
    # D4: stdout is a log, not a channel. A usage-free log is a *successful*
    # attempt, so this is None rather than an exception or a zeroed Usage.
    adapter = ClaudeAdapter()
    assert adapter.parse_usage("") is None
    assert adapter.parse_usage("just some chatter\nand a traceback-ish line\n") is None


def test_partial_reporting_yields_a_partial_usage():
    usage = ClaudeAdapter().parse_usage(
        '{"usage":{"input_tokens":12,"output_tokens":7}}\n'
    )
    assert usage == Usage(tokens_in=12, tokens_out=7)
    assert usage.cost is None


def test_the_last_report_wins():
    # A log that reports twice is reporting cumulatively; the final line is the
    # one that describes the whole run.
    log = (
        '{"usage":{"input_tokens":10,"output_tokens":2},"total_cost_usd":0.01}\n'
        "...more work...\n"
        '{"usage":{"input_tokens":90,"output_tokens":30},"total_cost_usd":0.25}\n'
    )
    assert ClaudeAdapter().parse_usage(log) == Usage(
        tokens_in=90, tokens_out=30, cost=0.25
    )


def test_the_result_carries_no_key_outside_the_three():
    # `Usage` is extra="forbid" and its names match Attempt's one for one, so
    # the engine's journalling stays a copy, never a translation.
    usage = ClaudeAdapter().parse_usage(FULL_LOG)
    assert usage is not None
    dumped = usage.model_dump()
    assert set(dumped) == {"tokens_in", "tokens_out", "cost"}
    assert Usage.model_validate(dumped) == usage
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/harness/test_claude.py -v`
Expected: FAIL — the five new tests raise `NotImplementedError`.

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/harness/claude.py`, extend the imports at the top of the module to:

```python
import math
import re

from pydantic import ValidationError

from agent_manager.harness.base import Usage
from agent_manager.models import Dispatch
```

add the patterns and converters below `PROMPT_INSTRUCTION`:

```python
def _key(*words: str) -> str:
    """A usage key, tolerant of the two ways a log spells one.

    `"input_tokens":` in a JSON line and `Input tokens:` in a human-readable
    summary are the same fact, and which one a harness version prints is not
    something a cost figure should depend on.

    The lookbehind is the point of this helper: without it, `input_tokens`
    matches inside `cache_creation_input_tokens`, and a cache counter gets
    journalled as the prompt size.
    """
    return r"(?<![\w-])" + r"[ _]".join(words)


_VALUE = r'"?\s*[:=]\s*"?\$?((?:[^\s,"}\]]|,(?=\d))+)'
"""The value after a usage key: quoted or bare, `:` or `=`, `$` allowed.

A comma ends the value unless a digit follows it, so `8,123` in a human-readable
summary survives while the `,` that separates two JSON keys still terminates the
match. Each alternative consumes exactly one character and the two are disjoint,
so there is nothing for the engine to backtrack over.

Deliberately loose about *what* it captures: a negative, non-numeric, inf or nan
value is captured here and dropped by the converters below, which is what makes
the dropping testable rather than accidental."""

_TOKENS_IN = re.compile(_key("input", "tokens") + _VALUE, re.IGNORECASE)
_TOKENS_OUT = re.compile(_key("output", "tokens") + _VALUE, re.IGNORECASE)
_COST = re.compile(
    r"(?<![\w-])(?:total[ _])?cost(?:[ _]usd)?" + _VALUE, re.IGNORECASE
)
"""Every pattern is literals plus one single-character repetition -- no nested
quantifiers and no ambiguous alternation -- so scanning a multi-megabyte log is
linear and cannot backtrack catastrophically."""


def _tokens(raw: str) -> int | None:
    """A token count, or `None` if the log printed something that is not one."""
    try:
        value = int(raw.replace(",", "").rstrip(".;"))
    except ValueError:
        return None
    return value if value >= 0 else None


def _cost(raw: str) -> float | None:
    """A USD cost, or `None`.

    `inf` and `nan` parse as floats and would then be rejected by `Usage`'s
    `allow_inf_nan=False`, turning a garbled log line into an exception. They
    are dropped here instead.
    """
    try:
        value = float(raw.replace(",", "").rstrip(".;"))
    except ValueError:
        return None
    if math.isnan(value) or math.isinf(value) or value < 0:
        return None
    return value
```

and replace the `parse_usage` stub with:

```python
    def parse_usage(self, stdout: str) -> Usage | None:
        """What the attempt cost, as far as the log says. Never raises.

        Each field is taken from its *last* match: a log that reports more than
        once is reporting cumulatively, and the final figure describes the whole
        run. A field whose last value will not convert is dropped rather than
        propagated, and a dispatch where nothing survives reports `None` -- D4
        makes stdout a log, so a usage-free log is a successful attempt.
        """
        fields: dict[str, int | float] = {}
        for field, pattern, convert in (
            ("tokens_in", _TOKENS_IN, _tokens),
            ("tokens_out", _TOKENS_OUT, _tokens),
            ("cost", _COST, _cost),
        ):
            found = pattern.findall(stdout)
            if not found:
                continue
            value = convert(found[-1])
            if value is not None:
                fields[field] = value
        if not fields:
            return None
        try:
            return Usage(**fields)
        except ValidationError:
            # Unreachable while the converters above are the only source of
            # values, and kept anyway: "never raises" is the contract the
            # engine relies on, and it must not depend on a converter staying
            # exactly as strict as `Usage` is.
            return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/harness/test_claude.py -v`
Expected: PASS — 20 passed.

- [ ] **Step 5: Write the failing tests for hostile input**

Append to `tests/harness/test_claude.py`:

```python
@pytest.mark.parametrize(
    ("log", "expected"),
    [
        ('{"usage":{"input_tokens":', None),
        ('{"usage":{"input_tokens":-5,"output_tokens":7}}', Usage(tokens_out=7)),
        ('{"usage":{"input_tokens":"many","output_tokens":7}}', Usage(tokens_out=7)),
        ('{"input_tokens":4,"total_cost_usd":inf}', Usage(tokens_in=4)),
        ('{"input_tokens":4,"total_cost_usd":nan}', Usage(tokens_in=4)),
        ('{"total_cost_usd":-1.5}', None),
        ("\x00\x01 binary noise \xff", None),
    ],
)
def test_hostile_logs_never_raise(log, expected):
    # A cosmetic change in another program's log must not fail an attempt that
    # produced a valid result file -- every bad value is dropped, and dropping
    # everything means None.
    assert ClaudeAdapter().parse_usage(log) == expected
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/harness/test_claude.py -v`
Expected: PASS — 27 passed. If a case fails, fix `_tokens`/`_cost`/the patterns; never relax the test.

- [ ] **Step 7: Run it to see the hostile cases really exercise the dropping**

Run: `uv run pytest tests/harness/test_claude.py -k hostile -v`
Expected: PASS — 7 passed. Each parametrized id should appear; if fewer than seven ran, the parametrize list was mistyped.

- [ ] **Step 8: Write the failing Review Focus tests**

Append to `tests/harness/test_claude.py`:

```python
def test_cache_counters_are_not_mistaken_for_the_prompt_size():
    # `cache_creation_input_tokens` ends in `input_tokens`; a substring match
    # would journal a cache counter as the prompt size.
    log = (
        '{"usage":{"cache_creation_input_tokens":4096,'
        '"cache_read_input_tokens":2048,"output_tokens":11}}'
    )
    assert ClaudeAdapter().parse_usage(log) == Usage(tokens_out=11)


def test_a_multi_megabyte_log_is_parsed_without_backtracking():
    # A chatty agent run is routinely this big, and parse_usage sees the whole
    # file once per attempt.
    log = ("thinking about the plan, reading files, running tests\n" * 40_000) + FULL_LOG
    assert len(log) > 2_000_000
    assert ClaudeAdapter().parse_usage(log) == Usage(
        tokens_in=8123, tokens_out=1544, cost=0.3142
    )


def test_crlf_line_endings_and_human_spelling_report_the_same_numbers():
    crlf = FULL_LOG.replace("\n", "\r\n")
    assert ClaudeAdapter().parse_usage(crlf) == Usage(
        tokens_in=8123, tokens_out=1544, cost=0.3142
    )
    human = "Input tokens: 8,123\r\nOutput tokens: 1544\r\nCost: $0.3142\r\n"
    assert ClaudeAdapter().parse_usage(human) == Usage(
        tokens_in=8123, tokens_out=1544, cost=0.3142
    )
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `uv run pytest tests/harness/test_claude.py -v`
Expected: PASS — 30 passed, and the whole file finishes in a couple of seconds. A hang means a pattern acquired nested quantifiers.

- [ ] **Step 10: Commit**

```bash
git add src/agent_manager/harness/claude.py tests/harness/test_claude.py
git commit -m "feat(harness): parse usage out of the claude log without ever raising"
```

---

### Task 4: Pin that the adapter launches nothing

**Files:**
- Test: `tests/harness/test_claude.py` (append)
- Modify: `src/agent_manager/harness/claude.py` — only if the assertion below fails.

**Interfaces:**
- Consumes: the finished `agent_manager.harness.claude` module from Tasks 1-3; stdlib `ast`.
- Produces: nothing importable. This task's deliverable is the standing assertion that `claude.py`'s import list stays clean, so a later edit cannot quietly move execution out of `launcher.py`.

- [ ] **Step 1: Write the failing test**

Add `import ast` as the first line of the import block at the top of `tests/harness/test_claude.py`, add `from agent_manager.harness import claude as claude_module` alongside the other `agent_manager` imports, then append:

```python
def _imports() -> tuple[set[str], set[str]]:
    source = Path(claude_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    plain: set[str] = set()
    froms: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            plain.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            froms.add(node.module)
    return plain, froms


def test_the_adapter_launches_nothing_itself():
    # §14 line 485: the launcher is injected, and one launcher serves every
    # adapter. The moment this module can start a process, that stops being
    # true and every test above it starts spawning things.
    plain, froms = _imports()
    assert "subprocess" not in plain | froms
    assert "os" not in plain | froms
    assert "agent_manager.harness.launcher" not in froms
    assert not hasattr(claude_module, "subprocess")
    assert not hasattr(claude_module, "run_direct")
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `uv run pytest tests/harness/test_claude.py::test_the_adapter_launches_nothing_itself -v`
Expected: PASS. It passes on the code written in Tasks 1-3 by construction — that is the point: it is a regression fence, and its value is that it fails the day someone adds `import subprocess` here. To see it fail on purpose, temporarily add `import subprocess` at the top of `claude.py`, re-run, confirm FAIL, then remove the line and re-run.

- [ ] **Step 3: Run the whole suite**

Run: `uv run pytest`
Expected: PASS — every pre-existing test (`tests/harness/test_base.py`, `tests/harness/test_launcher.py`, `tests/roles/test_loader.py`, `tests/steps/*`, `tests/test_*.py`) plus the 31 in `tests/harness/test_claude.py`. No file outside `src/agent_manager/harness/claude.py` and `tests/harness/test_claude.py` should appear in `git status`.

- [ ] **Step 4: Commit**

```bash
git add tests/harness/test_claude.py
git commit -m "test(harness): pin that the claude adapter imports no launcher"
```
