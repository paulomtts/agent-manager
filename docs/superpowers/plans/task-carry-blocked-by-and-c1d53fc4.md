<!-- task-pipeline: validated -->
# Carry blocked_by and created_at, and read the board's roots (card c1d53fc4)

Parent story b84d47e1 "Census: read a whole milestone from the board". This subtask narrows decision O1 of `docs/superpowers/specs/2026-09-24-orchestration-design.md` (lines 26-43) to its model and board.py half. The census functions (`find_milestone`, `order_siblings`, `flatten_milestone`, `Census`/`StoryPlan`/`SubtaskPlan`) belong to sibling cards 20abebdf and 65275a49 and are out of scope. Do not create `census.py`.

## Scope

1. `src/agent_manager/models.py`: add these two fields to both `Card` and `CardNode`:
   - `blocked_by: list[str] = Field(default_factory=list)`
   - `created_at: str | None = None`

   Both models keep `model_config = ConfigDict(extra="ignore")` and stay plain `BaseModel` rather than `_Model`, because brd is another program. `CardNode` still has no `parent_id`. No other field changes. `created_at` stays the ISO string brd prints and is not parsed into a datetime.

2. `src/agent_manager/board.py`:
   - Add the pure argv builder `roots_argv() -> list[str]`, which returns `[BRD, "tree"]`. Put it next to `show_argv`/`tree_argv`. `tree_argv` stays as it is.
   - Add `roots(*, repo_dir: Path | None = None) -> list[models.CardNode]`. It follows `tree()`: `_run`, then `_decode`, then it checks that the payload is a list, then it runs `_validated(models.CardNode, item, argv=argv)` on every element. It does not require exactly one element. An empty list is valid and returns `[]`. It returns brd's order and nesting unchanged, with no re-sorting or re-parenting.
   - board.py stays the only caller of brd. It passes argument lists only, never a shell. `set_status` stays the only write.

## Observable behaviour

- `board.show(id)` and `board.tree(id)` now populate `blocked_by` (a list of card ids) and `created_at`, because brd already emits both. Nested `children` carry them too.
- `board.roots()` returns every top-level card on the board as a `CardNode`, with descendants nested under `children`.
- Existing callers and tests that build `Card`/`CardNode` without the new keys still work, because both fields have defaults.

## Error paths

For `roots()`:
- Everything `_run`/`_decode` already raises: brd missing, a bare non-zero exit, non-JSON output, a non-envelope, `ok: false`, a missing `data`. These surface as `BoardError` unchanged.
- The `data` payload is not a list: raise `BoardError`, naming the type found, with `argv` and `exit_code`.
- Any element fails `CardNode` validation: raise `BoardError` via `_validated`.

## Tests

Tier placement follows design spec section 14 (lines 479-494): anything that touches brd is tested against a real temporary brd board with no mocking. Pure functions get unit tests. Nothing goes in `tests/e2e`. Existing tests are not edited, and the whole default suite, including `tests/e2e`, stays green.

`tests/test_board.py`. These are Step-tier tests against a real temp board, using the `temp_board` fixture, the `_add_card`/`_brd_json` helpers and `@requires_brd`:
- On an empty board, `board.roots()` returns `[]`.
- Two milestones, each with a child. `board.roots()` returns both milestones as `CardNode` with their children nested, and the ids match `_brd_json(root, "tree")`.
- A `blocked_by` edge between two cards, created with `brd block` (check `brd block --help` for the exact flag). The edge round-trips through `board.show` (as `Card.blocked_by`), through `board.tree` (on the node) and through `board.roots()` (on the nested node). Wherever brd emits `created_at`, it is a non-empty string equal to what `_brd_json` reports. The blocked card's `status` is `blocked` as brd derives it, passed through verbatim.

`tests/test_board.py`, pure section at the bottom. These are unit tests of pure functions:
- `roots_argv()` equals `[BRD, "tree"]`.
- The non-list payload path is not tested: `roots` calls `_run`, so reaching it needs a brd mock, which the no-mocking rule forbids. Do not factor a helper or add a mock just for it.

`tests/test_models.py`. These are unit tests of the pure models:
- `Card` and `CardNode` built without the new keys default to `blocked_by == []` and `created_at is None`. Separate instances do not share the default list.
- Both models accept `blocked_by`/`created_at` when they are given. Nested `children` nodes parse them too.
- Unknown keys are still ignored on both models alongside the new fields.

---

# Carry blocked_by/created_at and read the board's roots Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `Card` and `CardNode` carry brd's `blocked_by` and `created_at`, and `board.roots()` reads every top-level card on the board via `brd tree` with no id.

**Architecture:** Two new defaulted fields on the two brd-boundary pydantic models in `src/agent_manager/models.py` (still `extra="ignore"`, still not `_Model`). A pure `roots_argv()` builder and a `roots()` reader in `src/agent_manager/board.py` that reuse the existing `_run` -> `_decode` -> `_validated` pipeline exactly like `tree()`, minus the exactly-one-root check. No new writes to the board, no shell, no new module.

**Tech Stack:** Python, pydantic v2, pytest, the real `brd` CLI over `subprocess`, `uv`.

**Spec:** `/home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-carry-blocked-by-and-c1d53fc4/docs/superpowers/specs/task-carry-blocked-by-and-c1d53fc4-design.md` (reproduced verbatim above). Parent decision: `docs/superpowers/specs/2026-09-24-orchestration-design.md` decision O1 (lines 26-43). Testing tiers: `docs/superpowers/specs/2026-09-23-agent-manager-design.md` section 14 (lines 479-494).

**Branch / worktree:** `m3/task-carry-blocked-by-and-c1d53fc4` in `/home/paulomtts/Code/agent-manager/.claude/worktrees/m3/task-carry-blocked-by-and-c1d53fc4`, cut from `m3/task-add-implement-blocked-dd321e61`. Do not assume any sibling subtask's code (no `census.py`) exists. All paths below are relative to that worktree root.

## Global Constraints

- `blocked_by: list[str] = Field(default_factory=list)` and `created_at: str | None = None` on BOTH `Card` and `CardNode`; no other field changes.
- Both models keep `model_config = ConfigDict(extra="ignore")` and stay plain `BaseModel`, not `_Model`.
- `CardNode` has no `parent_id` (existing `tests/test_models.py::test_card_node_has_no_parent_id_field` asserts it).
- `created_at` stays the ISO string brd prints; it is not parsed into a `datetime`.
- `roots_argv()` returns `[BRD, "tree"]`; `tree_argv` is unchanged.
- `roots(*, repo_dir: Path | None = None) -> list[models.CardNode]`: empty list valid, brd's order and nesting unchanged, no re-sorting or re-parenting.
- board.py stays the only caller of brd, argument lists only, never `shell=True`; `set_status` stays the only write.
- Do not create `census.py`. Nothing goes in `tests/e2e`. Existing tests are not edited. No mocking of brd in new tests.
- Verification: `uv run pytest` (the whole default suite) must pass. There is no separate lint or typecheck command.

## Review Focus

1. A card blocked by more than one card: `blocked_by` must list every blocker, in the order brd prints them, not just the first. Pinned in Task 1 (`test_show_and_tree_carry_blocked_by_and_created_at_from_a_real_board` blocks one card by two).
2. The blocker is later marked `done`: the adapter must pass brd's `status` and `blocked_by` through verbatim rather than inventing its own "is it still blocked" rule. Pinned in Task 1 (`test_show_passes_status_and_blocked_by_through_after_the_blocker_is_done`).
3. A `blocked_by` edge that crosses milestones (a card in milestone 2 blocked by a card in milestone 1): `roots()` must carry the foreign id untouched, without trying to resolve or re-parent it. Pinned in Task 2 (`test_roots_carries_a_cross_milestone_blocked_by_edge`).
4. Three-level depth (milestone -> story -> subtask) under `roots()`: grandchildren must be nested, not flattened or dropped. Pinned in Task 2 (`test_roots_returns_every_milestone_with_children_nested` adds a grandchild).
5. `roots()` run outside any brd project: must surface brd's own `ProjectNotFoundError` as `BoardError`, not crash or return `[]`. Pinned in Task 2 (`test_roots_outside_a_brd_project_raises_board_error`).

---

### Task 1: `Card` and `CardNode` carry `blocked_by` and `created_at`

**Files:**
- Modify: `src/agent_manager/models.py:161-194` (the `Card` and `CardNode` classes)
- Test: `tests/test_models.py` (append at end of file, after `test_phase_carries_the_reason_it_failed`, line 760)
- Test: `tests/test_board.py` (append at end of file, after `test_nothing_but_status_is_ever_written_to_the_board`, line 505)

**Interfaces:**
- Consumes: existing `board.show(card_id, *, repo_dir=None) -> models.Card`, `board.tree(card_id, *, repo_dir=None) -> models.CardNode`, `board.set_status(card_id, status, *, repo_dir=None) -> models.Card`; test helpers `temp_board`, `_add_card(root, title, parent=None) -> str`, `_brd_json(root, *args) -> object`, `requires_brd` in `tests/test_board.py`.
- Produces: `models.Card.blocked_by: list[str]`, `models.Card.created_at: str | None`, `models.CardNode.blocked_by: list[str]`, `models.CardNode.created_at: str | None`. Test helpers in `tests/test_board.py`: `_node_by_id(nodes: list[models.CardNode], card_id: str) -> models.CardNode` and `_raw_by_id(nodes: list[dict], card_id: str) -> dict` (Task 2 reuses both).

- [ ] **Step 1: Write the failing model tests**

Append to the end of `tests/test_models.py`:

```python
_BRD_MODELS = (models.Card, models.CardNode)


@pytest.mark.parametrize("model", _BRD_MODELS, ids=["Card", "CardNode"])
def test_brd_models_default_blocked_by_to_empty_and_created_at_to_none(model):
    # Existing callers build cards without these keys; the defaults keep them working.
    instance = model(id="c1", title="t", status="todo")
    assert instance.blocked_by == []
    assert instance.created_at is None


@pytest.mark.parametrize("model", _BRD_MODELS, ids=["Card", "CardNode"])
def test_brd_models_blocked_by_default_is_per_instance(model):
    first = model(id="c1", title="t", status="todo")
    second = model(id="c2", title="t", status="todo")
    first.blocked_by.append("c9")
    assert second.blocked_by == []


def test_card_parses_blocked_by_and_created_at_from_a_brd_show_payload():
    card = models.Card.model_validate(
        {
            "id": "c1",
            "title": "t",
            "status": "blocked",
            "parent_id": "p1",
            "description": None,
            "blocked_by": ["b1", "b2"],
            "created_at": "2026-09-23T10:00:00+00:00",
            "updated_at": "2026-09-23T11:00:00+00:00",
        }
    )
    assert card.blocked_by == ["b1", "b2"]
    # brd's ISO string, kept as a string: not parsed into a datetime.
    assert card.created_at == "2026-09-23T10:00:00+00:00"
    assert isinstance(card.created_at, str)


def test_card_node_parses_blocked_by_and_created_at_at_every_depth():
    node = models.CardNode.model_validate(
        {
            "id": "m1",
            "title": "Milestone 1",
            "status": "todo",
            "blocked_by": [],
            "created_at": "2026-09-23T10:00:00+00:00",
            "children": [
                {
                    "id": "s1",
                    "title": "story",
                    "status": "blocked",
                    "blocked_by": ["s0"],
                    "created_at": "2026-09-23T10:01:00+00:00",
                    "children": [
                        {
                            "id": "t1",
                            "title": "subtask",
                            "status": "blocked",
                            "blocked_by": ["t0", "x9"],
                            "created_at": "2026-09-23T10:02:00+00:00",
                            "children": [],
                        }
                    ],
                }
            ],
        }
    )
    assert node.blocked_by == []
    assert node.created_at == "2026-09-23T10:00:00+00:00"
    story = node.children[0]
    assert story.blocked_by == ["s0"]
    assert story.created_at == "2026-09-23T10:01:00+00:00"
    subtask = story.children[0]
    assert subtask.blocked_by == ["t0", "x9"]
    assert subtask.created_at == "2026-09-23T10:02:00+00:00"


@pytest.mark.parametrize("model", _BRD_MODELS, ids=["Card", "CardNode"])
def test_brd_models_still_ignore_unknown_keys_alongside_the_new_fields(model):
    instance = model.model_validate(
        {
            "id": "c1",
            "title": "t",
            "status": "todo",
            "blocked_by": ["b1"],
            "created_at": "2026-09-23T10:00:00+00:00",
            "updated_at": "2026-09-23T11:00:00+00:00",
            "assignee": "paulo",
        }
    )
    assert instance.blocked_by == ["b1"]
    assert instance.created_at == "2026-09-23T10:00:00+00:00"
    assert not hasattr(instance, "assignee")
    assert "updated_at" not in instance.model_dump()
    assert "assignee" not in instance.model_dump()
```

- [ ] **Step 2: Write the failing real-board round-trip tests**

Append to the end of `tests/test_board.py`:

```python
def _node_by_id(nodes: list[models.CardNode], card_id: str) -> models.CardNode:
    """Depth-first lookup of one node in a parsed tree, wherever it nests."""
    for node in nodes:
        if node.id == card_id:
            return node
        try:
            return _node_by_id(node.children, card_id)
        except LookupError:
            continue
    raise LookupError(card_id)


def _raw_by_id(nodes: list[dict], card_id: str) -> dict:
    """Depth-first lookup of one node in brd's raw tree JSON."""
    for node in nodes:
        if node["id"] == card_id:
            return node
        try:
            return _raw_by_id(node["children"], card_id)
        except LookupError:
            continue
    raise LookupError(card_id)


@requires_brd
def test_show_and_tree_carry_blocked_by_and_created_at_from_a_real_board(temp_board):
    milestone = _add_card(temp_board, "Milestone 1")
    first = _add_card(temp_board, "story a", milestone)
    second = _add_card(temp_board, "story b", milestone)
    blocked = _add_card(temp_board, "story c", milestone)
    _brd_json(temp_board, "block", blocked, "--by", first)
    _brd_json(temp_board, "block", blocked, "--by", second)

    raw_show = _brd_json(temp_board, "show", blocked)
    card = board.show(blocked, repo_dir=temp_board)

    # Every blocker, in brd's order -- not just the first.
    assert sorted(card.blocked_by) == sorted([first, second])
    assert card.blocked_by == raw_show["blocked_by"]
    assert isinstance(card.created_at, str) and card.created_at
    assert card.created_at == raw_show["created_at"]
    # brd derives `blocked`; the adapter passes it through verbatim.
    assert card.status == "blocked"

    unblocked = board.show(first, repo_dir=temp_board)
    assert unblocked.blocked_by == []
    assert unblocked.created_at == _brd_json(temp_board, "show", first)["created_at"]

    raw_tree = _brd_json(temp_board, "tree", milestone)
    node = board.tree(milestone, repo_dir=temp_board)

    for card_id in (milestone, first, second, blocked):
        parsed = _node_by_id([node], card_id)
        raw = _raw_by_id(raw_tree, card_id)
        assert parsed.blocked_by == raw["blocked_by"]
        assert isinstance(parsed.created_at, str) and parsed.created_at
        assert parsed.created_at == raw["created_at"]
    blocked_node = _node_by_id([node], blocked)
    assert sorted(blocked_node.blocked_by) == sorted([first, second])
    assert blocked_node.status == "blocked"


@requires_brd
def test_show_passes_status_and_blocked_by_through_after_the_blocker_is_done(temp_board):
    # Whatever brd decides a done blocker means, the adapter must not second-guess it.
    blocker = _add_card(temp_board, "blocker")
    blocked = _add_card(temp_board, "blocked")
    _brd_json(temp_board, "block", blocked, "--by", blocker)

    board.set_status(blocker, "done", repo_dir=temp_board)

    raw = _brd_json(temp_board, "show", blocked)
    card = board.show(blocked, repo_dir=temp_board)
    assert card.status == raw["status"]
    assert card.blocked_by == raw["blocked_by"]
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_models.py tests/test_board.py -k "blocked_by or created_at or blocker or new_fields" -v`
Expected: FAIL. The model tests fail with `AttributeError: 'Card' object has no attribute 'blocked_by'` (and the same for `CardNode`); the `ignore_unknown_keys` tests fail on `instance.blocked_by`. The two `@requires_brd` tests fail with `AttributeError: 'Card' object has no attribute 'blocked_by'` (they are SKIPPED rather than failing only if `brd` is not on PATH; if so, install brd before continuing, because these tests are the round-trip evidence).

- [ ] **Step 4: Add the two fields to `Card` and `CardNode`**

In `src/agent_manager/models.py`, replace the `Card` and `CardNode` classes (lines 161-194) with:

```python
class Card(BaseModel):
    """One `brd` card as `brd show` reports it (design §4: board.py's boundary).

    Deliberately not a `_Model`: `extra="forbid"` is right for journal lines we
    wrote ourselves, and wrong for another program's output. A `brd` schema
    addition must not break a running milestone, so unknown keys are ignored.
    `status` is a plain string because the board's vocabulary
    (`todo`/`in_progress`/`done`/`blocked`) is brd's to define and is not the
    run lifecycle `Status` above.

    `blocked_by` and `created_at` are carried for the census (orchestration
    decision O1): the ids this card waits on, and brd's ISO creation timestamp
    kept as the string brd printed.
    """

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    title: str
    status: str = Field(min_length=1)
    parent_id: str | None = None
    description: str | None = None
    blocked_by: list[str] = Field(default_factory=list)
    created_at: str | None = None


class CardNode(BaseModel):
    """One node of `brd tree`'s JSON: a card plus its nested descendants.

    Tree nodes carry no `parent_id` of their own -- the nesting under
    `children` is what preserves milestone -> story -> subtask depth.
    `blocked_by` and `created_at` mean what they mean on `Card`.
    """

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    title: str
    status: str = Field(min_length=1)
    description: str | None = None
    blocked_by: list[str] = Field(default_factory=list)
    created_at: str | None = None
    children: list["CardNode"] = Field(default_factory=list)
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_models.py tests/test_board.py -k "blocked_by or created_at or blocker or new_fields" -v`
Expected: PASS (all selected tests).

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: PASS, including the unchanged `test_card_node_has_no_parent_id_field`, `test_card_node_children_default_is_per_instance` and `test_unknown_extra_fields_in_data_are_tolerated`.

- [ ] **Step 7: Commit**

```bash
git add src/agent_manager/models.py tests/test_models.py tests/test_board.py
git commit -m "$(cat <<'EOF'
feat(models): carry brd's blocked_by and created_at on Card and CardNode

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ
EOF
)"
```

---

### Task 2: `board.roots()` reads every top-level card

**Files:**
- Modify: `src/agent_manager/board.py:1-15` (module docstring), `src/agent_manager/board.py:61-62` (add `roots_argv` after `tree_argv`), `src/agent_manager/board.py:195` (add `roots` after `tree`, before `set_status`)
- Test: `tests/test_board.py` (append at end of file, after the Task 1 tests)

**Interfaces:**
- Consumes: `models.CardNode` with `blocked_by: list[str]` and `created_at: str | None` (Task 1); `board._run(argv, repo_dir)`, `board._decode(stdout, *, argv, exit_code)`, `board._validated(model, data, *, argv)`, `board.BoardError`; test helpers `_node_by_id(nodes: list[models.CardNode], card_id: str) -> models.CardNode` and `_raw_by_id(nodes: list[dict], card_id: str) -> dict` (Task 1), `temp_board`, `_add_card`, `_brd_json`, `requires_brd`.
- Produces: `board.roots_argv() -> list[str]` returning `["brd", "tree"]`; `board.roots(*, repo_dir: Path | None = None) -> list[models.CardNode]` (the sibling census cards consume this).

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_board.py`:

```python
@requires_brd
def test_roots_of_an_empty_board_is_an_empty_list(temp_board):
    # brd tree with no id on an empty board answers {"ok": true, "data": []}.
    assert board.roots(repo_dir=temp_board) == []


@requires_brd
def test_roots_returns_every_milestone_with_children_nested(temp_board):
    first = _add_card(temp_board, "Milestone 1")
    first_story = _add_card(temp_board, "story one", first)
    first_subtask = _add_card(temp_board, "subtask one", first_story)
    second = _add_card(temp_board, "Milestone 2")
    second_story = _add_card(temp_board, "story two", second)

    raw = _brd_json(temp_board, "tree")
    nodes = board.roots(repo_dir=temp_board)

    assert all(isinstance(node, models.CardNode) for node in nodes)
    # brd's order, untouched.
    assert [node.id for node in nodes] == [node["id"] for node in raw]
    assert sorted(node.id for node in nodes) == sorted([first, second])

    first_node = _node_by_id(nodes, first)
    assert [child.id for child in first_node.children] == [first_story]
    assert [grand.id for grand in first_node.children[0].children] == [first_subtask]
    assert first_node.children[0].children[0].children == []

    second_node = _node_by_id(nodes, second)
    assert [child.id for child in second_node.children] == [second_story]
    assert second_node.children[0].children == []

    for card_id in (first, first_story, first_subtask, second, second_story):
        parsed = _node_by_id(nodes, card_id)
        assert parsed.created_at == _raw_by_id(raw, card_id)["created_at"]
        assert parsed.blocked_by == []


@requires_brd
def test_roots_carries_a_cross_milestone_blocked_by_edge(temp_board):
    first = _add_card(temp_board, "Milestone 1")
    blocker = _add_card(temp_board, "story a", first)
    second = _add_card(temp_board, "Milestone 2")
    blocked = _add_card(temp_board, "story b", second)
    _brd_json(temp_board, "block", blocked, "--by", blocker)

    raw = _brd_json(temp_board, "tree")
    nodes = board.roots(repo_dir=temp_board)

    blocked_node = _node_by_id(nodes, blocked)
    # The foreign id rides along untouched, and the node stays under its own milestone.
    assert blocked_node.blocked_by == [blocker]
    assert blocked_node.status == "blocked"
    assert [child.id for child in _node_by_id(nodes, second).children] == [blocked]
    assert blocked_node.created_at == _raw_by_id(raw, blocked)["created_at"]
    assert isinstance(blocked_node.created_at, str) and blocked_node.created_at
    assert _node_by_id(nodes, blocker).blocked_by == []


@requires_brd
def test_roots_outside_a_brd_project_raises_board_error(tmp_path, monkeypatch):
    # No `.brd` marker anywhere above: brd's ok:false ProjectNotFoundError must
    # surface as BoardError, not as an empty board.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    not_a_project = tmp_path / "nowhere"
    not_a_project.mkdir()
    with pytest.raises(board.BoardError) as excinfo:
        board.roots(repo_dir=not_a_project)
    assert excinfo.value.error_type == "ProjectNotFoundError"
    assert excinfo.value.exit_code == 1
    assert excinfo.value.argv == ["brd", "tree"]


# Pure checks: no board needed.


def test_roots_argv_is_brd_tree_with_no_id():
    assert board.roots_argv() == ["brd", "tree"]
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_board.py -k roots -v`
Expected: FAIL with `AttributeError: module 'agent_manager.board' has no attribute 'roots'` for the four `@requires_brd` tests and `... has no attribute 'roots_argv'` for `test_roots_argv_is_brd_tree_with_no_id`.

- [ ] **Step 3: Add `roots_argv`**

In `src/agent_manager/board.py`, directly after `tree_argv` (line 61-62) and before `set_status_argv`, insert:

```python
def roots_argv() -> list[str]:
    # No id: brd answers with every top-level card, descendants nested.
    return [BRD, "tree"]
```

- [ ] **Step 4: Add `roots`**

In `src/agent_manager/board.py`, directly after `tree()` (ends line 195) and before `set_status`, insert:

```python
def roots(*, repo_dir: Path | None = None) -> list[models.CardNode]:
    """Every top-level card on the board, via `brd tree` with no id.

    Each root nests its descendants under `children`. An empty board is an
    empty list, not an error. Ordering and depth come from brd -- nothing is
    re-sorted or re-parented here.
    """
    argv = roots_argv()
    completed = _run(argv, repo_dir)
    data = _decode(completed.stdout, argv=argv, exit_code=completed.returncode)
    if not isinstance(data, list):
        raise BoardError(
            f"brd tree returned {type(data).__name__} where a list of roots "
            "was expected",
            argv=argv,
            exit_code=completed.returncode,
        )
    return [_validated(models.CardNode, item, argv=argv) for item in data]
```

- [ ] **Step 5: Update the module docstring's operation count**

In `src/agent_manager/board.py`, replace lines 3-6:

```python
Three operations cross this seam: read one card, read a card subtree, write a
card status. Nothing about a *run* is ever written to the board (decision D5,
design §9) -- run state lives in agent-manager's own SQLite projection and
journal, so `set_status` is the module's entire write surface.
```

with:

```python
Four operations cross this seam: read one card, read a card subtree, read
every root of the board, write a card status. Nothing about a *run* is ever
written to the board (decision D5, design §9) -- run state lives in
agent-manager's own SQLite projection and journal, so `set_status` is the
module's entire write surface.
```

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_board.py -k roots -v`
Expected: PASS (five tests).

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest`
Expected: PASS, including the unchanged `test_board_never_uses_a_shell`, `test_tree_requires_exactly_one_root` and `test_nothing_but_status_is_ever_written_to_the_board`.

- [ ] **Step 8: Commit**

```bash
git add src/agent_manager/board.py tests/test_board.py
git commit -m "$(cat <<'EOF'
feat(board): read every root of the board with brd tree

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2UjNqoWcVtw4c7Kz7SgRZ
EOF
)"
```
