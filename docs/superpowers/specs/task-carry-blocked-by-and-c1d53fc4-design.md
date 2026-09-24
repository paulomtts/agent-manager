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
