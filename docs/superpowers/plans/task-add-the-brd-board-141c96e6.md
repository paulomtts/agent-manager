<!-- task-pipeline: validated -->
# Subtask spec — Add the brd board adapter (141c96e6)

Parent story: `492ac463` "Naming and the brd board adapter". Milestone design (source of truth): `docs/superpowers/specs/2026-09-23-agent-manager-design.md`. This document narrows that agreed design to one module; it does not revisit it.

## Scope

Deliver `src/agent_manager/board.py`: the **only** module in the codebase that shells out to the `brd` CLI (design §4, package layout line 119). It is a thin, tested adapter over three operations — read one card, read a card subtree, write a card status — returning pydantic models from `src/agent_manager/models.py`.

`models.py` already exists (sibling subtask `1535b285`, done): it holds the §9 run-state tree (`Run`, `StoryRun`, `SubtaskRun`, `PhaseRun`, `Attempt`, `Dispatch`, `RunConfig`, `HarnessAssignment`) plus a private `_Model` base with `model_config = ConfigDict(extra="forbid")`, and it already defines a module-level `Status` literal for run/story/subtask/phase lifecycle (`"pending" | "started" | "done" | "failed" | "escalated"`). This subtask adds exactly the card/subtree types it needs to that same file (nothing more; the run-state models above are already done and are not touched here) — `Card` and `CardNode` (or equivalent). They must **not** subclass `_Model` or reuse the name `Status`: `_Model`'s `extra="forbid"` is correct for journal/projection fidelity but is the opposite of what `Card` needs (unknown `brd` fields tolerated, per Observable behaviour below), and `Status` is already taken by the run-lifecycle literal — `Card`'s status field is a plain `str` (board statuses such as `todo`/`in_progress`/`done`/`blocked` are `brd`'s to define, not modelled here as a closed literal). Give `Card`/`CardNode` their own pydantic config (default `extra="ignore"`, i.e. simply omitting `extra="forbid"`).

In scope:

- `show(card_id) -> Card` — one card, via `brd show`.
- `tree(card_id) -> CardNode` (or equivalent rooted subtree value) — the card and its descendants, via `brd tree`.
- `set_status(card_id, status) -> Card` — a board status transition, idempotent.
- Envelope decoding: parse `{"ok": true, "data": ...}` / `{"ok": false, ...}` from `brd` stdout into models or into a typed error.
- A single internal invocation helper that runs `brd` with an **argument list** and captures stdout/stderr.

Explicitly out of scope (stated by both card and parent story, and by design §5 line 252):

- Any naming, slug, stem, branch or ref-matching logic — that is `dag.py`, owned by the already-done sibling `01d725d6`. `board.py` never derives a branch name.
- Milestone orchestration: census, cycle checks, level computation, parallel stories, the `integrate` phase.
- Non-Claude harnesses, the launcher seam, engine/journal/SQLite work.
- Any shell-string command construction. `shell_quote` does not port; every `brd` call is an argv list passed to `subprocess`, never interpolated into a shell.
- Writing *anything about a run* to `brd` (decision D5). The board holds card status and nothing else; run state lives exclusively in the agent-manager SQLite/journal store (design §9). `board.py` therefore exposes no write surface beyond `set_status`.
- Network access. `brd` is local and SQLite-backed; `board.py` makes no network call and its tests make none.

## Observable behaviour

**Process boundary.** `brd`'s stdout is untrusted input crossing a process boundary, so it is validated with pydantic (CLAUDE.md convention), not hand-indexed dicts. A `Card` carries at minimum its id, title, status and parent id; unknown fields from `brd` are tolerated rather than fatal, so a `brd` schema addition does not break a running milestone. A `tree` result preserves parent/child structure so a caller can walk milestone → story → subtask depth (the same depth this card lives at).

**`show`.** Given a card id, returns the parsed `Card`. Called once per card at run start and cached by the engine (design §7) — `board.py` itself caches nothing.

**`tree`.** Given a card id, returns that card with its descendants. Ordering and depth come from `brd`; `board.py` does not re-sort or re-parent. `brd tree <id>`'s envelope `data` is a **one-element list** containing the root node even when a single `card_id` is given (`brd`'s `build_tree` always returns `list[dict]`; a bare id just makes it a singleton list) — `tree()` unwraps that single element and returns the `CardNode` itself, not a list. Each node in `brd`'s tree JSON nests its descendants under a `children` key and carries no `parent_id` of its own (unlike the flat `Card` from `show`); `CardNode` models that shape (id/title/status/description/children, no parent id per node), and it is the nesting under `children`, not a `parent_id` field, that preserves the milestone → story → subtask structure.

**`set_status`.** `brd` has no `set-status`/`status` subcommand; the only way to change a card's stored status is `brd update <card_id> --status <status>` (design §17 line 520 calls this out by name: "parallel stories mean concurrent `brd update` calls"). `set_status` shells out to `brd update`, not to an invented verb. `brd` itself rejects `--status blocked` (`blocked` is derived, never set directly) and surfaces that as a normal `"ok": false` envelope — `board.py` does not special-case it, it just propagates as `BoardError` like any other rejected envelope. Writes the requested status and returns the resulting card. **Idempotent** (design §9 line 376): calling it twice with the same `(card_id, status)` leaves the board in the same state and the second call succeeds — it does not raise on "already in that status". This is load-bearing, not a nicety: `resume` discards in-flight attempts and re-runs the whole phase from the top, and the phases that call it (`mark_in_progress`, `mark_done`) are `best_effort: true`, so a spurious failure would be recorded as a board-write failure in the run summary for work that actually succeeded.

**Errors.** One error type raised by this module (e.g. `BoardError`) carrying the failing argv, the exit code and `brd`'s own message, so a `best_effort` caller can journal something diagnosable. It is raised on:

- non-zero `brd` exit;
- stdout that is not valid JSON;
- a well-formed envelope with `"ok": false` (`brd`'s own shape is `{"ok": false, "error": {"type": ..., "message": ...}}`; `board.py` surfaces `error.message` — and may include `error.type` — verbatim, it does not invent its own wording);
- an `"ok": true` envelope whose `data` fails model validation;
- `brd` not being on `PATH`.

An unknown/nonexistent card id surfaces as whatever `brd` reports — `board.py` invents no "not found" semantics of its own. No error path is silently swallowed; `best_effort` tolerance is the *engine's* policy, not the adapter's.

## Test list

Placement follows design §14 (lines 477–492), whose tiers are defined by the module's nature, not by a generic unit/integration split. Tests mirror source: `tests/test_board.py`.

**Steps tier** (§14: "against temporary git repositories and a temporary `brd` board; no network") — `board.py` is the `brd` caller, so its read/write behaviour is exercised against a **real temporary/local `brd` board via subprocess**, with no mocking of `brd`:

1. `show` on a card created in a temp board returns a `Card` with the expected id, title, status and parent id.
2. `tree` on a parent returns the parent plus its children, preserving the milestone → story → subtask nesting.
3. `set_status` moves a card's status; a subsequent `show` observes the new value.
4. `set_status` called twice with the same status succeeds both times and leaves the same final status — the resume-idempotency guarantee.
5. `set_status` followed by `show` round-trips through the model types (no lossy re-serialisation).
6. A nonexistent card id raises `BoardError` carrying `brd`'s message and exit code.
7. Nothing beyond card status is written: after a full `set_status` cycle, the board contains no run/phase/attempt artefacts.

**Pure-parsing checks** (narrow unit-style, permitted alongside the above because envelope decoding and model construction are pure): 

8. `{"ok": true, "data": {...}}` decodes to a `Card`.
9. `{"ok": false, "error": {"type": ..., "message": ...}}` raises `BoardError` with the embedded `error.message`.
10. Non-JSON stdout raises `BoardError` rather than a `JSONDecodeError`.
11. `data` missing a required field raises `BoardError`, and extra unknown fields are tolerated.
12. The invocation helper builds an argv **list** for each of `show`, `tree` and `set_status` — asserted directly, guarding the §5 "argument lists, never shell strings" constraint.

Not applicable here: the Pure-functions tier (no `.test.mjs` behavioural spec ports into this module — those belong to `dag.py`), the Adapters tier (that is `build_command` for *harness* adapters, a different seam), the Engine tier, and the End-to-end tier.

## Verification

- Full suite: `uv run pytest`
- Typecheck: none
- Lint: none

---

# brd Board Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `src/agent_manager/board.py`, the only module that shells out to the `brd` CLI, exposing `show`, `tree` and an idempotent `set_status` that decode `brd`'s `{"ok", "data"}` envelope into pydantic `Card`/`CardNode` models or raise a single `BoardError`.

**Architecture:** `board.py` builds an argv **list** per operation (`brd show <id>`, `brd tree <id>`, `brd update <id> --status <status>`), runs it with `subprocess.run(..., cwd=repo_dir, capture_output=True, text=True)` — never a shell string — decodes stdout as a `{"ok", "data"}` envelope, and validates `data` with pydantic models added to `src/agent_manager/models.py`. Every failure path (missing `brd`, non-zero exit, non-JSON stdout, `ok: false`, validation failure) raises one `BoardError` carrying the argv, the exit code and `brd`'s own message. The module holds no cache, no naming logic, no run state.

**Tech Stack:** Python 3.12, pydantic v2, stdlib `subprocess`/`json`, pytest, `uv`. The real `brd` CLI is a test dependency (installed on `PATH`, SQLite-backed, local only).

**Spec:** `docs/superpowers/specs/task-add-the-brd-board-141c96e6-design.md` (prepended verbatim above). Milestone design: `docs/superpowers/specs/2026-09-23-agent-manager-design.md`.

## Global Constraints

- Source lives under `src/agent_manager/`, tests mirror it under `tests/` (CLAUDE.md) — so `src/agent_manager/board.py` and `tests/test_board.py`, top-level, not under `steps/` or `harness/` (design §4 line 119: "board.py    the only caller of `brd`").
- Every `brd` invocation is an argv **list** passed to `subprocess`; `shell=True`, string interpolation and `shell_quote`-style quoting are forbidden (design §5 line 252: "the program runs commands itself with argument lists").
- Nothing about a run is ever written to `brd` (decision D5, design §9): the only write surface is `set_status`, which maps to `brd update <id> --status <status>`.
- `set_status` is idempotent (design §9 line 376: "`rollup.set_status` is idempotent") — a second call with the same `(card_id, status)` succeeds.
- Pydantic for anything validated at a process boundary (CLAUDE.md); `brd` stdout is such a boundary.
- New card models do **not** subclass `models._Model` (its `extra="forbid"` is wrong here) and do **not** reuse the name `Status` (already the run-lifecycle literal). `Card.status` is a plain `str`.
- No naming, slug, stem, branch or ref logic in `board.py` — that is `dag.py`, already done.
- No network access in the module or its tests. No mocking of `brd` in the Steps-tier tests.
- Verification: `uv run pytest`. There is no lint and no typecheck command.

## Review Focus

- **`repo_dir` is not a `brd` project** (no `.brd` marker anywhere above it): `brd` prints an `ok: false` `ProjectNotFoundError` envelope and exits 1 — must surface as `BoardError` with that message, not as a `KeyError` on `data`. Pinned in Task 4.
- **Non-zero exit with empty stdout** (a `brd` crash or typer usage error writing only to stderr): must raise `BoardError` carrying stderr and the exit code, not a `JSONDecodeError` from parsing `""`. Pinned in Task 2.
- **A card id containing shell metacharacters** (`"abc; rm -rf /"`): must travel as exactly one argv element, unquoted and unmodified — the §5 guard. Pinned in Task 2.
- **`brd tree` returning other than exactly one root** (a future `brd` change, or `data` that is not a list): must raise `BoardError` rather than silently taking `data[0]` or `IndexError`-ing. Pinned in Task 5.
- **`set_status(card_id, "blocked")`**: `brd` rejects it as `InvalidStatusError` (`blocked` is derived); `board.py` must propagate `brd`'s message as `BoardError` and invent no special case. Pinned in Task 6.

---

## File Structure

- `src/agent_manager/models.py` (modify, append at end after `Run`): add `Card` and `CardNode`, the two boundary types `board.py` parses into. No existing model changes.
- `src/agent_manager/board.py` (create): `BoardError`, the three argv builders, the invocation helper, the envelope decoder, and the public `show` / `tree` / `set_status`.
- `tests/test_board.py` (create): the Steps-tier tests against a real temporary `brd` board plus the narrow pure-parsing checks.
- `tests/test_models.py` (modify, append at end): construction/validation tests for `Card` and `CardNode`, alongside the existing run-state model tests.

---

## Task 1: Card and CardNode boundary models

**Files:**
- Modify: `src/agent_manager/models.py` (append after `Run`, which ends at line 141)
- Test: `tests/test_models.py` (append after the final test, which ends at line 598)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `models.Card`: pydantic model, `model_config = ConfigDict(extra="ignore")`, fields `id: str` (min_length 1), `title: str`, `status: str` (min_length 1), `parent_id: str | None = None`, `description: str | None = None`.
  - `models.CardNode`: pydantic model, `model_config = ConfigDict(extra="ignore")`, fields `id: str` (min_length 1), `title: str`, `status: str` (min_length 1), `description: str | None = None`, `children: list[CardNode] = Field(default_factory=list)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_models.py`:

```python
def test_card_parses_a_brd_show_payload_and_ignores_unknown_fields():
    # `brd show`'s data carries blocked_by/children/timestamps too. A brd schema
    # addition must not break a running milestone, so unknown keys are ignored
    # rather than forbidden -- the opposite of the _Model journal types above.
    card = models.Card.model_validate(
        {
            "id": "141c96e6-4a08-4905-b7a6-c0c993d1c20d",
            "title": "Add the brd board adapter",
            "description": "the only caller of brd",
            "status": "todo",
            "parent_id": "492ac463-8d23-4699-847c-31dc0aebd80f",
            "created_at": "2026-09-23T10:00:00+00:00",
            "updated_at": "2026-09-23T10:00:00+00:00",
            "blocked_by": [],
            "children": [],
        }
    )
    assert card.id == "141c96e6-4a08-4905-b7a6-c0c993d1c20d"
    assert card.title == "Add the brd board adapter"
    assert card.status == "todo"
    assert card.parent_id == "492ac463-8d23-4699-847c-31dc0aebd80f"
    assert card.description == "the only caller of brd"


def test_card_status_is_an_open_string_not_the_run_lifecycle_literal():
    # Board statuses are brd's to define (todo/in_progress/done/blocked and
    # whatever it adds next); `Status` here is the *run* lifecycle and is a
    # different vocabulary.
    for board_status in ("todo", "in_progress", "done", "blocked", "on_hold"):
        assert models.Card(id="c1", title="t", status=board_status).status == (
            board_status
        )


def test_card_defaults_a_top_level_card_to_no_parent():
    card = models.Card(id="352e955b", title="Milestone 1", status="todo")
    assert card.parent_id is None
    assert card.description is None


def test_card_requires_id_title_and_status():
    with pytest.raises(ValidationError) as excinfo:
        models.Card.model_validate({"description": "no identity at all"})
    assert {error["loc"] for error in excinfo.value.errors()} == {
        ("id",),
        ("title",),
        ("status",),
    }


def test_card_rejects_empty_id_and_status():
    with pytest.raises(ValidationError) as excinfo:
        models.Card(id="", title="t", status="todo")
    assert [error["loc"] for error in excinfo.value.errors()] == [("id",)]

    with pytest.raises(ValidationError) as excinfo:
        models.Card(id="c1", title="t", status="")
    assert [error["loc"] for error in excinfo.value.errors()] == [("status",)]


def test_card_node_nests_milestone_story_subtask_depth():
    node = models.CardNode.model_validate(
        {
            "id": "352e955b",
            "title": "Milestone 1",
            "description": None,
            "status": "in_progress",
            "blocked_by": [],
            "created_at": "2026-09-23T10:00:00+00:00",
            "updated_at": "2026-09-23T10:00:00+00:00",
            "children": [
                {
                    "id": "492ac463",
                    "title": "Naming and the brd board adapter",
                    "description": None,
                    "status": "in_progress",
                    "blocked_by": [],
                    "children": [
                        {
                            "id": "141c96e6",
                            "title": "Add the brd board adapter",
                            "description": None,
                            "status": "todo",
                            "blocked_by": [],
                            "children": [],
                        }
                    ],
                }
            ],
        }
    )
    assert node.id == "352e955b"
    assert [child.id for child in node.children] == ["492ac463"]
    assert [grandchild.id for grandchild in node.children[0].children] == ["141c96e6"]
    assert node.children[0].children[0].title == "Add the brd board adapter"


def test_card_node_has_no_parent_id_field():
    # brd's tree nodes carry no parent_id; nesting under `children` is what
    # preserves the structure. An accidental parent_id field would be fiction.
    assert "parent_id" not in models.CardNode.model_fields
    assert models.CardNode(id="c1", title="t", status="todo").children == []


def test_card_node_children_default_is_per_instance():
    first = models.CardNode(id="c1", title="t", status="todo")
    second = models.CardNode(id="c2", title="t", status="todo")
    first.children.append(models.CardNode(id="c3", title="t", status="todo"))
    assert second.children == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_models.py -k "card" -v`
Expected: FAIL — `AttributeError: module 'agent_manager.models' has no attribute 'Card'`

- [ ] **Step 3: Write the minimal implementation**

Append to `src/agent_manager/models.py`:

```python
class Card(BaseModel):
    """One `brd` card as `brd show` reports it (design §4: board.py's boundary).

    Deliberately not a `_Model`: `extra="forbid"` is right for journal lines we
    wrote ourselves, and wrong for another program's output. A `brd` schema
    addition must not break a running milestone, so unknown keys are ignored.
    `status` is a plain string because the board's vocabulary
    (`todo`/`in_progress`/`done`/`blocked`) is brd's to define and is not the
    run lifecycle `Status` above.
    """

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    title: str
    status: str = Field(min_length=1)
    parent_id: str | None = None
    description: str | None = None


class CardNode(BaseModel):
    """One node of `brd tree`'s JSON: a card plus its nested descendants.

    Tree nodes carry no `parent_id` of their own -- the nesting under
    `children` is what preserves milestone -> story -> subtask depth.
    """

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    title: str
    status: str = Field(min_length=1)
    description: str | None = None
    children: list["CardNode"] = Field(default_factory=list)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS (all pre-existing model tests still pass too)

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/models.py tests/test_models.py
git commit -m "feat(models): add Card and CardNode boundary types for the brd board"
```

---

## Task 2: BoardError, argv builders and the invocation helper

**Files:**
- Create: `src/agent_manager/board.py`
- Create: `tests/test_board.py`

**Interfaces:**
- Consumes: nothing yet from `models` (Task 3 wires that in).
- Produces:
  - `board.BRD = "brd"` — the executable name, argv element zero.
  - `board.BoardError(RuntimeError)` with `__init__(self, message: str, *, argv: list[str], exit_code: int | None = None, error_type: str | None = None)` and attributes `message`, `argv`, `exit_code`, `error_type`.
  - `board.show_argv(card_id: str) -> list[str]`
  - `board.tree_argv(card_id: str) -> list[str]`
  - `board.set_status_argv(card_id: str, status: str) -> list[str]`
  - `board._run(argv: list[str], repo_dir: Path | None) -> subprocess.CompletedProcess[str]` — raises `BoardError` when `brd` is not on `PATH`, or when the exit code is non-zero and stdout is blank.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_board.py`:

```python
"""Behaviour of the brd board adapter (design §4 line 119, §9, spec 141c96e6).

Placement follows design §14: board.py is the brd caller, so its read/write
behaviour is exercised against a real temporary brd board over subprocess --
no mocking of brd, no network. The narrow checks at the bottom cover the pure
parts (argv construction, envelope decoding) that need no board at all.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_manager import board, models

requires_brd = pytest.mark.skipif(
    shutil.which("brd") is None,
    reason="the brd CLI must be installed for the board adapter's steps-tier tests",
)


def test_show_argv_is_a_list_of_plain_arguments():
    assert board.show_argv("141c96e6") == ["brd", "show", "141c96e6"]


def test_tree_argv_is_a_list_of_plain_arguments():
    assert board.tree_argv("141c96e6") == ["brd", "tree", "141c96e6"]


def test_set_status_argv_uses_brd_update_with_a_status_option():
    # brd has no set-status subcommand; `update --status` is the only writer.
    assert board.set_status_argv("141c96e6", "in_progress") == [
        "brd",
        "update",
        "141c96e6",
        "--status",
        "in_progress",
    ]


@pytest.mark.parametrize(
    "argv",
    [
        board.show_argv("141c96e6"),
        board.tree_argv("141c96e6"),
        board.set_status_argv("141c96e6", "done"),
    ],
    ids=["show", "tree", "set_status"],
)
def test_every_builder_returns_a_list_of_strings(argv):
    assert isinstance(argv, list)
    assert all(isinstance(element, str) for element in argv)
    assert argv[0] == "brd"


def test_shell_metacharacters_stay_inside_one_argv_element():
    # Design §5 line 252: argument lists, never shell strings. A hostile id is
    # one element, unquoted and unmodified -- there is nothing to escape.
    hostile = "141c96e6; rm -rf /"
    assert board.show_argv(hostile) == ["brd", "show", hostile]
    assert board.set_status_argv(hostile, "done; echo pwned") == [
        "brd",
        "update",
        hostile,
        "--status",
        "done; echo pwned",
    ]


def test_missing_brd_on_path_raises_board_error(monkeypatch, tmp_path):
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    with pytest.raises(board.BoardError) as excinfo:
        board._run(board.show_argv("141c96e6"), None)
    assert "brd" in str(excinfo.value)
    assert excinfo.value.argv == ["brd", "show", "141c96e6"]
    assert excinfo.value.exit_code is None


def test_non_zero_exit_with_no_stdout_reports_stderr_and_the_exit_code(tmp_path):
    # A brd crash or a typer usage error writes only to stderr. Parsing "" as
    # JSON would bury that behind a JSONDecodeError.
    fake_brd = tmp_path / "brd"
    fake_brd.write_text(
        "#!/bin/sh\necho 'Usage: brd [OPTIONS]' >&2\nexit 2\n"
    )
    fake_brd.chmod(0o755)
    with pytest.raises(board.BoardError) as excinfo:
        board._run([str(fake_brd), "show", "141c96e6"], None)
    assert "Usage: brd [OPTIONS]" in str(excinfo.value)
    assert excinfo.value.exit_code == 2


def test_run_returns_the_completed_process_on_success(tmp_path):
    fake_brd = tmp_path / "brd"
    fake_brd.write_text('#!/bin/sh\necho \'{"ok": true, "data": {}}\'\n')
    fake_brd.chmod(0o755)
    completed = board._run([str(fake_brd), "show", "141c96e6"], None)
    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {"ok": True, "data": {}}


def test_run_invokes_brd_in_the_given_repo_dir(tmp_path):
    fake_brd = tmp_path / "brd"
    fake_brd.write_text('#!/bin/sh\nprintf \'{"ok": true, "data": "%s"}\' "$(pwd)"\n')
    fake_brd.chmod(0o755)
    elsewhere = tmp_path / "some-repo"
    elsewhere.mkdir()
    completed = board._run([str(fake_brd), "show", "x"], elsewhere)
    assert json.loads(completed.stdout)["data"] == str(elsewhere.resolve())


def test_board_never_uses_a_shell():
    # The one grep that keeps the §5 constraint honest as the module grows.
    source = Path(board.__file__).read_text()
    assert "shell=True" not in source
    assert "os.system" not in source
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_board.py -v`
Expected: FAIL at collection — `ImportError: cannot import name 'board' from 'agent_manager'`

- [ ] **Step 3: Write the minimal implementation**

Create `src/agent_manager/board.py`:

```python
"""The only caller of the `brd` CLI (design §4 line 119).

Three operations cross this seam: read one card, read a card subtree, write a
card status. Nothing about a *run* is ever written to the board (decision D5,
design §9) -- run state lives in agent-manager's own SQLite projection and
journal, so `set_status` is the module's entire write surface.

Every invocation is an argument list handed to `subprocess`. Design §5 line 252
is explicit that the program runs commands itself with argument lists, so
`shell_quote` from the shell-script original does not port and there is no
string to quote: a card id full of shell metacharacters is just one argv
element.

Naming, slugs, branches and ref matching are `dag.py`'s job, not this module's.
"""

import subprocess
from pathlib import Path

BRD = "brd"
"""Executable name, resolved on PATH. Argv element zero of every call."""


class BoardError(RuntimeError):
    """Any failure of a `brd` invocation, carrying enough to journal it.

    A caller running a `best_effort: true` phase records this and continues;
    tolerance is the engine's policy, never the adapter's, so nothing here is
    swallowed.
    """

    def __init__(
        self,
        message: str,
        *,
        argv: list[str],
        exit_code: int | None = None,
        error_type: str | None = None,
    ) -> None:
        self.message = message
        self.argv = list(argv)
        self.exit_code = exit_code
        self.error_type = error_type
        super().__init__(
            f"{message} (argv={self.argv!r}, exit_code={exit_code!r})"
        )


def show_argv(card_id: str) -> list[str]:
    return [BRD, "show", card_id]


def tree_argv(card_id: str) -> list[str]:
    return [BRD, "tree", card_id]


def set_status_argv(card_id: str, status: str) -> list[str]:
    # brd has no set-status subcommand: `update --status` is the only writer
    # (design §17 line 520 names `brd update` directly).
    return [BRD, "update", card_id, "--status", status]


def _run(
    argv: list[str], repo_dir: Path | None
) -> "subprocess.CompletedProcess[str]":
    """Run one `brd` argv list, returning the completed process.

    Raises `BoardError` when `brd` is missing, or when it failed without
    printing an envelope -- a bare non-zero exit with stderr only.
    """
    try:
        completed = subprocess.run(
            argv,
            cwd=repo_dir,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise BoardError(
            f"could not run {argv[0]}: {exc.strerror}", argv=argv
        ) from exc

    if completed.returncode != 0 and not completed.stdout.strip():
        detail = completed.stderr.strip() or "no output"
        raise BoardError(detail, argv=argv, exit_code=completed.returncode)

    return completed
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_board.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/board.py tests/test_board.py
git commit -m "feat(board): add BoardError, argv builders and the brd invocation helper"
```

---

## Task 3: Envelope decoding and model validation

**Files:**
- Modify: `src/agent_manager/board.py` (append after `_run`)
- Modify: `tests/test_board.py` (append after the existing tests)

**Interfaces:**
- Consumes: `board.BoardError`, `board._run`, `models.Card`, `models.CardNode` from Tasks 1-2.
- Produces:
  - `board._decode(stdout: str, *, argv: list[str], exit_code: int) -> object` — returns the envelope's `data` or raises `BoardError`.
  - `board._validated(model: type[M], data: object, *, argv: list[str]) -> M` where `M` is a pydantic `BaseModel` subclass — raises `BoardError` on validation failure.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_board.py`:

```python
_SHOW_PAYLOAD = {
    "id": "141c96e6",
    "title": "Add the brd board adapter",
    "description": "the only caller of brd",
    "status": "todo",
    "parent_id": "492ac463",
    "created_at": "2026-09-23T10:00:00+00:00",
    "updated_at": "2026-09-23T10:00:00+00:00",
    "blocked_by": [],
    "children": [],
}


def test_decode_returns_the_data_of_an_ok_envelope():
    data = board._decode(
        json.dumps({"ok": True, "data": _SHOW_PAYLOAD}),
        argv=board.show_argv("141c96e6"),
        exit_code=0,
    )
    card = board._validated(models.Card, data, argv=board.show_argv("141c96e6"))
    assert card.id == "141c96e6"
    assert card.title == "Add the brd board adapter"
    assert card.status == "todo"
    assert card.parent_id == "492ac463"


def test_decode_raises_board_error_carrying_brds_own_message():
    envelope = {
        "ok": False,
        "error": {"type": "CardNotFoundError", "message": "no card with id nope"},
    }
    with pytest.raises(board.BoardError) as excinfo:
        board._decode(
            json.dumps(envelope), argv=board.show_argv("nope"), exit_code=1
        )
    assert excinfo.value.message == "no card with id nope"
    assert excinfo.value.error_type == "CardNotFoundError"
    assert excinfo.value.exit_code == 1
    assert "no card with id nope" in str(excinfo.value)


def test_decode_tolerates_an_error_envelope_without_a_message():
    with pytest.raises(board.BoardError) as excinfo:
        board._decode(
            json.dumps({"ok": False}), argv=board.show_argv("nope"), exit_code=1
        )
    assert excinfo.value.error_type is None
    assert excinfo.value.exit_code == 1


@pytest.mark.parametrize(
    "stdout",
    ["", "   ", "not json at all", "Traceback (most recent call last):", "{"],
    ids=["empty", "blank", "prose", "traceback", "truncated"],
)
def test_non_json_stdout_raises_board_error_not_a_json_decode_error(stdout):
    with pytest.raises(board.BoardError) as excinfo:
        board._decode(stdout, argv=board.show_argv("141c96e6"), exit_code=0)
    assert not isinstance(excinfo.value, json.JSONDecodeError)
    assert "JSON" in str(excinfo.value)


@pytest.mark.parametrize(
    "payload", ['["ok"]', '"ok"', "null", '{"data": {}}'], ids=["list", "str", "null", "no-ok"]
)
def test_json_that_is_not_an_envelope_raises_board_error(payload):
    with pytest.raises(board.BoardError) as excinfo:
        board._decode(payload, argv=board.show_argv("141c96e6"), exit_code=0)
    assert "envelope" in str(excinfo.value)


def test_a_non_zero_exit_is_an_error_even_behind_an_ok_envelope():
    # Spec: non-zero exit raises, full stop. An ok envelope from a process that
    # then failed is not a success.
    with pytest.raises(board.BoardError) as excinfo:
        board._decode(
            json.dumps({"ok": True, "data": _SHOW_PAYLOAD}),
            argv=board.show_argv("141c96e6"),
            exit_code=3,
        )
    assert excinfo.value.exit_code == 3
    assert "exited 3" in str(excinfo.value)


def test_data_missing_a_required_field_raises_board_error():
    incomplete = {key: value for key, value in _SHOW_PAYLOAD.items() if key != "title"}
    with pytest.raises(board.BoardError) as excinfo:
        board._validated(models.Card, incomplete, argv=board.show_argv("141c96e6"))
    assert "title" in str(excinfo.value)
    assert excinfo.value.argv == ["brd", "show", "141c96e6"]


def test_unknown_extra_fields_in_data_are_tolerated():
    generous = {**_SHOW_PAYLOAD, "assignee": "paulo", "labels": ["m1"]}
    card = board._validated(models.Card, generous, argv=board.show_argv("141c96e6"))
    assert card.id == "141c96e6"
    assert not hasattr(card, "assignee")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_board.py -k "decode or validated or envelope or tolerated or missing_a_required" -v`
Expected: FAIL — `AttributeError: module 'agent_manager.board' has no attribute '_decode'`

- [ ] **Step 3: Write the minimal implementation**

In `src/agent_manager/board.py`, extend the imports at the top:

```python
import json
import subprocess
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from agent_manager import models

M = TypeVar("M", bound=BaseModel)
```

and append after `_run`:

```python
def _decode(stdout: str, *, argv: list[str], exit_code: int) -> object:
    """Pull `data` out of a `{"ok", "data"}` envelope, or raise `BoardError`.

    brd's failure shape is `{"ok": false, "error": {"type", "message"}}` and its
    message is surfaced verbatim: this module invents no wording and no
    "not found" semantics of its own.
    """
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise BoardError(
            f"brd printed output that is not JSON: {stdout.strip()[:200]!r}",
            argv=argv,
            exit_code=exit_code,
        ) from exc

    if not isinstance(envelope, dict) or "ok" not in envelope:
        raise BoardError(
            f"brd printed JSON that is not an {{ok, data}} envelope: "
            f"{stdout.strip()[:200]!r}",
            argv=argv,
            exit_code=exit_code,
        )

    if not envelope["ok"]:
        error = envelope.get("error")
        if not isinstance(error, dict):
            error = {}
        raise BoardError(
            error.get("message", "brd reported a failure with no message"),
            argv=argv,
            exit_code=exit_code,
            error_type=error.get("type"),
        )

    if exit_code != 0:
        raise BoardError(
            f"brd exited {exit_code} despite printing an ok envelope",
            argv=argv,
            exit_code=exit_code,
        )

    if "data" not in envelope:
        raise BoardError(
            "brd printed an ok envelope with no data",
            argv=argv,
            exit_code=exit_code,
        )

    return envelope["data"]


def _validated(model: type[M], data: object, *, argv: list[str]) -> M:
    """Validate brd's payload at the process boundary (CLAUDE.md convention)."""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise BoardError(
            f"brd's payload did not validate as {model.__name__}: {exc}",
            argv=argv,
            exit_code=0,
        ) from exc
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_board.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/board.py tests/test_board.py
git commit -m "feat(board): decode the brd ok/data envelope into validated models"
```

---

## Task 4: `show` against a real temporary brd board

**Files:**
- Modify: `src/agent_manager/board.py` (append after `_validated`)
- Modify: `tests/test_board.py` (append the board fixture and the `show` tests)

**Interfaces:**
- Consumes: `board._run`, `board._decode`, `board._validated`, `board.show_argv`, `models.Card`.
- Produces: `board.show(card_id: str, *, repo_dir: Path | None = None) -> models.Card`. `repo_dir` is the directory `brd` runs in (it resolves its board from the nearest `.brd` marker at or above its cwd); `None` means the current working directory.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_board.py`:

```python
@pytest.fixture
def temp_board(tmp_path, monkeypatch):
    """A real, empty brd board in a throwaway directory.

    brd keys its SQLite files off XDG_DATA_HOME and resolves the board from the
    nearest `.brd` marker at or above its cwd, so pointing XDG_DATA_HOME at
    tmp_path and running in a fresh directory isolates these tests completely
    from the developer's own board. The subprocess inherits the patched
    environment.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    root = tmp_path / "board-repo"
    root.mkdir()
    subprocess.run(
        ["brd", "init", "--name", "temp-board"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return root


def _add_card(root: Path, title: str, parent: str | None = None) -> str:
    argv = ["brd", "add", "--title", title]
    if parent is not None:
        argv += ["--parent", parent]
    completed = subprocess.run(
        argv, cwd=root, check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)["data"]["id"]


def _brd_json(root: Path, *args: str) -> object:
    completed = subprocess.run(
        ["brd", *args], cwd=root, check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)["data"]


@requires_brd
def test_show_reads_a_card_from_a_real_board(temp_board):
    milestone = _add_card(temp_board, "Milestone 1")
    story = _add_card(temp_board, "Naming and the brd board adapter", milestone)
    subtask = _add_card(temp_board, "Add the brd board adapter", story)

    card = board.show(subtask, repo_dir=temp_board)

    assert isinstance(card, models.Card)
    assert card.id == subtask
    assert card.title == "Add the brd board adapter"
    assert card.status == "todo"
    assert card.parent_id == story


@requires_brd
def test_show_reports_a_top_level_card_with_no_parent(temp_board):
    milestone = _add_card(temp_board, "Milestone 1")
    card = board.show(milestone, repo_dir=temp_board)
    assert card.parent_id is None


@requires_brd
def test_show_of_a_nonexistent_card_raises_board_error_with_brds_message(temp_board):
    with pytest.raises(board.BoardError) as excinfo:
        board.show("no-such-card", repo_dir=temp_board)
    assert "no-such-card" in excinfo.value.message
    assert excinfo.value.error_type == "CardNotFoundError"
    assert excinfo.value.exit_code == 1
    assert excinfo.value.argv == ["brd", "show", "no-such-card"]


@requires_brd
def test_show_outside_a_brd_project_raises_board_error(tmp_path, monkeypatch):
    # No `.brd` marker anywhere above: brd answers with an ok:false
    # ProjectNotFoundError envelope and exits 1. That must surface as this
    # module's one error type, message intact.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    not_a_project = tmp_path / "nowhere"
    not_a_project.mkdir()
    with pytest.raises(board.BoardError) as excinfo:
        board.show("141c96e6", repo_dir=not_a_project)
    assert excinfo.value.error_type == "ProjectNotFoundError"
    assert excinfo.value.exit_code == 1
```

Note: `test_show_outside_a_brd_project_raises_board_error` uses `tmp_path` directly rather than `temp_board`, so it needs a directory with no `.brd` marker above it — pytest's `tmp_path` lives under `/tmp`, where no marker exists.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_board.py -k "show_reads or show_reports or show_of_a_nonexistent or show_outside" -v`
Expected: FAIL — `AttributeError: module 'agent_manager.board' has no attribute 'show'`

- [ ] **Step 3: Write the minimal implementation**

Append to `src/agent_manager/board.py`:

```python
def show(card_id: str, *, repo_dir: Path | None = None) -> models.Card:
    """One card, via `brd show`.

    `repo_dir` is the directory brd runs in; it resolves its board from the
    nearest `.brd` marker at or above that directory. Nothing is cached here --
    the engine caches the card at run start (design §7 line 287).
    """
    argv = show_argv(card_id)
    completed = _run(argv, repo_dir)
    data = _decode(
        completed.stdout, argv=argv, exit_code=completed.returncode
    )
    return _validated(models.Card, data, argv=argv)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_board.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/board.py tests/test_board.py
git commit -m "feat(board): read one card with show() against a real brd board"
```

---

## Task 5: `tree` against a real temporary brd board

**Files:**
- Modify: `src/agent_manager/board.py` (append after `show`)
- Modify: `tests/test_board.py` (append the `tree` tests)

**Interfaces:**
- Consumes: `board._run`, `board._decode`, `board._validated`, `board.tree_argv`, `models.CardNode`.
- Produces: `board.tree(card_id: str, *, repo_dir: Path | None = None) -> models.CardNode` — unwraps `brd tree`'s single-element `data` list and returns the root node itself.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_board.py`:

```python
@requires_brd
def test_tree_returns_the_root_node_not_a_list(temp_board):
    # brd's build_tree always returns list[dict]; a bare id just makes it a
    # singleton. Callers want the node.
    milestone = _add_card(temp_board, "Milestone 1")
    node = board.tree(milestone, repo_dir=temp_board)
    assert isinstance(node, models.CardNode)
    assert node.id == milestone
    assert node.title == "Milestone 1"


@requires_brd
def test_tree_preserves_milestone_story_subtask_nesting(temp_board):
    milestone = _add_card(temp_board, "Milestone 1")
    story = _add_card(temp_board, "Naming and the brd board adapter", milestone)
    first = _add_card(temp_board, "Port card naming from naming.mjs", story)
    second = _add_card(temp_board, "Add the brd board adapter", story)

    node = board.tree(milestone, repo_dir=temp_board)

    assert [child.id for child in node.children] == [story]
    story_node = node.children[0]
    assert [grandchild.id for grandchild in story_node.children] == [first, second]
    assert story_node.children[1].title == "Add the brd board adapter"
    assert story_node.children[1].children == []


@requires_brd
def test_tree_rooted_at_a_leaf_has_no_children(temp_board):
    milestone = _add_card(temp_board, "Milestone 1")
    subtask = _add_card(temp_board, "Add the brd board adapter", milestone)
    node = board.tree(subtask, repo_dir=temp_board)
    assert node.id == subtask
    assert node.children == []


@requires_brd
def test_tree_does_not_re_sort_or_re_parent_what_brd_returned(temp_board):
    # Ordering and depth are brd's. Compare against brd's own raw JSON.
    milestone = _add_card(temp_board, "Milestone 1")
    for title in ("story a", "story b", "story c"):
        _add_card(temp_board, title, milestone)

    raw = _brd_json(temp_board, "tree", milestone)
    node = board.tree(milestone, repo_dir=temp_board)

    assert [child.id for child in node.children] == [
        child["id"] for child in raw[0]["children"]
    ]


@pytest.mark.parametrize(
    "data",
    [[], [{"id": "a", "title": "a", "status": "todo", "children": []},
          {"id": "b", "title": "b", "status": "todo", "children": []}],
     {"id": "a", "title": "a", "status": "todo", "children": []}],
    ids=["empty-list", "two-roots", "bare-dict"],
)
def test_tree_requires_exactly_one_root(data, tmp_path, monkeypatch):
    # A future brd change, or a board.py bug, must not become an IndexError or
    # a silently-wrong root.
    fake_brd = tmp_path / "brd"
    fake_brd.write_text(
        "#!/bin/sh\ncat <<'EOF'\n"
        + json.dumps({"ok": True, "data": data})
        + "\nEOF\n"
    )
    fake_brd.chmod(0o755)
    monkeypatch.setattr(board, "BRD", str(fake_brd))
    with pytest.raises(board.BoardError) as excinfo:
        board.tree("141c96e6", repo_dir=tmp_path)
    assert "exactly one root" in str(excinfo.value)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_board.py -k "tree_returns or tree_preserves or tree_rooted or tree_does_not or tree_requires" -v`
Expected: FAIL — `AttributeError: module 'agent_manager.board' has no attribute 'tree'`

- [ ] **Step 3: Write the minimal implementation**

Append to `src/agent_manager/board.py`:

```python
def tree(card_id: str, *, repo_dir: Path | None = None) -> models.CardNode:
    """A card and its descendants, via `brd tree`.

    brd's `build_tree` always answers with a list; rooted at one card id that
    list holds exactly one node, which is what callers want. Ordering and depth
    come from brd -- nothing is re-sorted or re-parented here.
    """
    argv = tree_argv(card_id)
    completed = _run(argv, repo_dir)
    data = _decode(completed.stdout, argv=argv, exit_code=completed.returncode)
    if not isinstance(data, list) or len(data) != 1:
        found = len(data) if isinstance(data, list) else type(data).__name__
        raise BoardError(
            f"brd tree {card_id} returned {found} where exactly one root was "
            "expected",
            argv=argv,
            exit_code=completed.returncode,
        )
    return _validated(models.CardNode, data[0], argv=argv)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_board.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_manager/board.py tests/test_board.py
git commit -m "feat(board): read a card subtree with tree() against a real brd board"
```

---

## Task 6: Idempotent `set_status`, the module's only write surface

**Files:**
- Modify: `src/agent_manager/board.py` (append after `tree`)
- Modify: `tests/test_board.py` (append the `set_status` tests)

**Interfaces:**
- Consumes: `board._run`, `board._decode`, `board._validated`, `board.set_status_argv`, `board.show`, `models.Card`.
- Produces: `board.set_status(card_id: str, status: str, *, repo_dir: Path | None = None) -> models.Card` — writes via `brd update --status` and returns the resulting card. Idempotent.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_board.py`:

```python
def _without_volatile(nodes: list[dict]) -> list[dict]:
    """Every tree field except the two a status write is allowed to move."""
    return [
        {
            "id": node["id"],
            "title": node["title"],
            "description": node["description"],
            "blocked_by": node["blocked_by"],
            "created_at": node["created_at"],
            "children": _without_volatile(node["children"]),
        }
        for node in nodes
    ]


@requires_brd
def test_set_status_moves_the_status_and_a_later_show_sees_it(temp_board):
    subtask = _add_card(temp_board, "Add the brd board adapter")

    returned = board.set_status(subtask, "in_progress", repo_dir=temp_board)

    assert isinstance(returned, models.Card)
    assert returned.status == "in_progress"
    assert board.show(subtask, repo_dir=temp_board).status == "in_progress"


@requires_brd
def test_set_status_is_idempotent(temp_board):
    # Design §9 line 376. Resume discards in-flight attempts and re-runs the
    # whole phase, so mark_in_progress/mark_done run twice on the same card; a
    # second call must not raise or move the board.
    subtask = _add_card(temp_board, "Add the brd board adapter")

    first = board.set_status(subtask, "done", repo_dir=temp_board)
    second = board.set_status(subtask, "done", repo_dir=temp_board)

    assert first.status == "done"
    assert second.status == "done"
    assert second.id == first.id
    assert board.show(subtask, repo_dir=temp_board).status == "done"


@requires_brd
def test_set_status_round_trips_through_the_model_types(temp_board):
    story = _add_card(temp_board, "Naming and the brd board adapter")
    subtask = _add_card(temp_board, "Add the brd board adapter", story)

    written = board.set_status(subtask, "in_progress", repo_dir=temp_board)
    read_back = board.show(subtask, repo_dir=temp_board)

    assert read_back.id == written.id
    assert read_back.title == written.title
    assert read_back.status == written.status
    assert read_back.parent_id == written.parent_id == story
    assert read_back.description == written.description


@requires_brd
def test_set_status_of_a_nonexistent_card_raises_board_error(temp_board):
    with pytest.raises(board.BoardError) as excinfo:
        board.set_status("no-such-card", "done", repo_dir=temp_board)
    assert excinfo.value.error_type == "CardNotFoundError"
    assert excinfo.value.exit_code == 1
    assert excinfo.value.argv == [
        "brd",
        "update",
        "no-such-card",
        "--status",
        "done",
    ]


@requires_brd
def test_set_status_blocked_propagates_brds_own_rejection(temp_board):
    # brd derives `blocked` and refuses to store it. board.py special-cases
    # nothing: the ok:false envelope becomes a BoardError like any other.
    subtask = _add_card(temp_board, "Add the brd board adapter")
    with pytest.raises(board.BoardError) as excinfo:
        board.set_status(subtask, "blocked", repo_dir=temp_board)
    assert excinfo.value.error_type == "InvalidStatusError"
    assert "blocked" in excinfo.value.message


@requires_brd
def test_nothing_but_status_is_ever_written_to_the_board(temp_board, tmp_path):
    # Decision D5: the board receives status transitions and nothing else. No
    # run, phase or attempt artefact may appear on it.
    milestone = _add_card(temp_board, "Milestone 1")
    story = _add_card(temp_board, "Naming and the brd board adapter", milestone)
    subtask = _add_card(temp_board, "Add the brd board adapter", story)
    before = _brd_json(temp_board, "tree")

    board.set_status(subtask, "in_progress", repo_dir=temp_board)
    board.set_status(subtask, "done", repo_dir=temp_board)

    after = _brd_json(temp_board, "tree")
    assert _without_volatile(after) == _without_volatile(before)
    assert board.show(subtask, repo_dir=temp_board).status == "done"
    assert board.show(story, repo_dir=temp_board).status == "todo"

    # ...and no new brd storage of any kind was created beyond the one board db.
    brd_data = tmp_path / "xdg" / "brd"
    files = sorted(
        path.relative_to(brd_data).parts[0]
        for path in brd_data.rglob("*")
        if path.is_file()
    )
    assert files == ["master.db", "projects"]
    assert len(list((brd_data / "projects").glob("*.db"))) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_board.py -k "set_status_moves or idempotent or round_trips or set_status_of_a_nonexistent or set_status_blocked or nothing_but_status" -v`
Expected: FAIL — `AttributeError: module 'agent_manager.board' has no attribute 'set_status'`

- [ ] **Step 3: Write the minimal implementation**

Append to `src/agent_manager/board.py`:

```python
def set_status(
    card_id: str, status: str, *, repo_dir: Path | None = None
) -> models.Card:
    """Write a card's board status via `brd update --status`, and return it.

    This is the module's entire write surface: under D5 the board receives
    status transitions and nothing else, and run state lives in agent-manager's
    own store.

    Idempotent by construction (design §9 line 376): `brd update` stores the
    value it is given, so a repeat of the same transition is another successful
    write of the same value. Resume re-runs whole phases, and the phases that
    call this are `best_effort`, so a spurious second-call failure would be
    journalled as a board-write failure for work that actually succeeded.
    """
    argv = set_status_argv(card_id, status)
    completed = _run(argv, repo_dir)
    data = _decode(completed.stdout, argv=argv, exit_code=completed.returncode)
    return _validated(models.Card, data, argv=argv)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_board.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: PASS — every test in `tests/` including the pre-existing `test_dag.py`, `test_models.py`, `test_package.py`, `test_paths.py` and `test_store.py`

- [ ] **Step 6: Commit**

```bash
git add src/agent_manager/board.py tests/test_board.py
git commit -m "feat(board): write card status with an idempotent set_status"
```

---

## Verification

- Full suite: `uv run pytest`
- Typecheck: none
- Lint: none
