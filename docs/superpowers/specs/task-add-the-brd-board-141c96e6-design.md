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
