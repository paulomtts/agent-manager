# Port plan_check (card d3feb87e)

## 1. Scope

One module, `src/agent_manager/steps/plan_check.py`, and its tests at `tests/steps/test_plan_check.py`. It ports `find_validated_plan` (the `planCheck` function) and its helpers from the leave-me-alone plugin's `scripts/plan-check.mjs`, plus the boolean gate `has_validated_plan` that the workflow's `when:` clause names. `scripts/plan-check.test.mjs` is the behavioural specification: every test there has a ported equivalent here.

This is a deterministic step (design §4 `steps/` layout line 127, §6 lines 258-260: "The engine calls `run(ctx) -> dict`. No network, no model."). It reads the filesystem and nothing else — no git, no `brd`, no subprocess, no model call.

The step answers exactly one question the pipeline asks before dispatching Spec/Plan/Validate agents: *does this card already have a plan that Validate signed off?* If yes, the workflow phase `plan_check` skips to `implement` (design §5 lines 169-173).

**Out of scope.** The CLI flag parsing of the `.mjs` (`parseArgs`, `--compact`, the stdout JSON envelope) — this is an internal step, not a CLI command, so the `{"ok": true, "data": ...}` envelope (CLAUDE.md) does not apply to its return value. Registry/engine wiring of `plan_check.find_validated_plan` and `plan_check.has_validated_plan` into the workflow YAML belongs to the later workflow/registry effort; this card delivers the pure functions only. Short-id and stem naming stays in `dag.py` (card 01d725d6) and is consumed, never re-derived here. `steps/worktree.py` (card 0816e239, done) and `steps/verify.py` (card 9c3b1ffb, blocked on this one) are untouched. Milestone orchestration and non-Claude harnesses are later milestones.

## 2. Module surface

- `VALIDATED_MARKER = "<!-- task-pipeline: validated -->"` — a module constant, matched by literal substring containment only, **never** by regex. A plan whose prose merely discusses the marker therefore reads as validated. This is a deliberate, documented trade: the failure that matters is mistaking a real marker for prose, and a substring test can never make it. The docstring records the accepted cost.
- `matches_card(filename, card) -> bool` — only names ending in `.md` can match; the stem (name minus `.md`) is split on `-` and its **last** segment must equal the card's short id. Splitting is the whole anchor: `task-deadbeefa32af745.md` must not answer for card `a32af745`, which a "preceding character is not a digit" boundary would have let through, since hex ids may be preceded by hex characters.
- `pick_plan(filenames, card) -> str | None` — the matching names, sorted, last one wins; `None` when nothing matches or the listing is empty/`None`. Newest wins because a re-planned subtask leaves the stale file behind, and the stale file must not decide whether Spec/Plan/Validate re-run. "Newest" is lexicographic on the filename, exactly as the `.mjs` does; filenames are date-prefixed in practice, and no `stat` call is made.
- `find_validated_plan(card, plans_dir=None, *, repo_dir=None, list=list_dir, read=read_file) -> dict` — the step entry point. Returns a plain dict, not a Pydantic model: it crosses no process boundary, matching `worktree.ensure`'s own rationale ("a plain dict, since it crosses no process boundary and so needs no Pydantic model (`CLAUDE.md`)").
- `has_validated_plan(result) -> bool` — the `when:` gate: true only when the result says both `found` and `validated`. Pure; takes the dict `find_validated_plan` returned.
- `list_dir` / `read_file` — the default real-filesystem implementations, injectable via the `list`/`read` parameters. This mirrors the `.mjs`'s own `list = readdir, read = readFile` seam and `worktree.py`'s `GitRunner` callable-injection shape: a real default plus a swappable argument so tests can force errors a real filesystem will not produce on demand.

## 3. Observable behaviour

`find_validated_plan` resolves the plans directory first: the explicit `plans_dir` when given, otherwise `<repo_dir>/.claude/plans`. It then resolves `card` to a short id, dispatching on shape — `dag.short_id` itself only accepts a full UUID string (`src/agent_manager/dag.py`: `short_id(card_id: object) -> str` raises `ValueError` unless `card_id` is a 32-hex-character string once dashes are stripped; it does not accept a mapping, an object, or an already-short id), so `find_validated_plan` does the dispatch itself, never assuming `dag.short_id` does it:

- an 8-lowercase-hex-character string is accepted as-is, with no call into `dag.short_id`;
- any other string is passed to `dag.short_id` (a full card UUID, dashes optional);
- a mapping or an attribute-bearing object has its `id` field read locally — the same read `_field(card, "id")` performs in `dag.py`, reimplemented here rather than imported, since `_field` is a private module-level helper of `dag.py` and not part of its public surface — and the extracted value is then passed to `dag.short_id`.

An uppercase or malformed id — including an 8-character string that is not all-lowercase-hex, or a full id that fails `dag.short_id`'s check — raises `ValueError` rather than quietly matching nothing — accepting it would turn a caller's typo into "no plan found", a gate failing open in the direction that halts a run for a reason that is not true.

It then lists the plans directory, picks the newest matching filename, reads it, and reports:

| situation | result |
| --- | --- |
| plans directory missing/unlistable | `{"found": False, "path": "", "validated": False}` |
| directory listable, no matching plan | `{"found": False, "path": "", "validated": False}` |
| plan found, marker present literally | `{"found": True, "path": <abs path>, "validated": True}` |
| plan found, marker absent | `{"found": True, "path": <abs path>, "validated": False}` |
| plan found, unreadable | `{"found": True, "path": <abs path>, "validated": False, "error": "could not read <path>: <reason>"}` |

`path` is the plans directory joined with the chosen filename; it is `""` and never `None` when nothing was found, so a consumer can format it without a guard.

## 4. Error paths

There are no raised exceptions from I/O. A missing plans directory is a normal answer on a first run, not a failure, and any listing error is treated the same way — the step reports "no plan" and the pipeline plans one. An unreadable but existing plan is different: it is reported as `found` and not `validated`, with an `error` string naming the path and the underlying reason, so the journal says why the run re-planned instead of silently pretending the file was not there. The `error` key is present only in that case.

The only exception raised is `ValueError` from argument validation (bad card id, or neither `plans_dir` nor `repo_dir` given) — raised before any filesystem touch, so a caller bug cannot be mistaken for a first run.

## 5. Tests

All tests below live in `tests/steps/test_plan_check.py` and are **Steps**-tier per design §14 line 482-483. For this module — which uses no git and no board, only the filesystem — the Steps-tier rule reads as: exercise the real code against real temporary directories and files created in pytest's `tmp_path`, doing real I/O, and fake the `list`/`read` callables *only* where a test must force an outcome the real filesystem will not produce on demand. That mirrors the `.mjs` test's own use of `fakeFs` and `test_worktree.py`'s placement docstring. None of these belong in the Pure-functions tier (`dag.py`/`reducers.py` unit tests) even though `matches_card`/`pick_plan` are pure, because they are this Steps module's internals and are asserted alongside it; none belong in the Engine or End-to-end tiers.

1. **Card id resolution** — a full card UUID string, and a card mapping/object exposing `id`, both resolve to the same short id via `dag.short_id`; an already-short lowercase id passes through without calling `dag.short_id`; an uppercase or malformed id (short or full) raises `ValueError`; `plans_dir` defaults to `<repo_dir>/.claude/plans`, and an explicit `plans_dir` overrides it. *(Steps tier; real `tmp_path`, no I/O needed for the raising cases.)*
2. **A plan matches when the short id is its final segment** — `task-write-rows-a32af745.md` and `task-a32af745.md` both match. *(Steps.)*
3. **One card's plan never answers for another** — `task-deadbeefa32af745.md`, `task-rows-a32af746.md` and `task-rows-a32af745-old.md` all fail to match `a32af745`. *(Steps.)*
4. **Only `.md` files match** — `task-rows-a32af745.txt` does not. *(Steps.)*
5. **The newest matching plan wins** — given two date-prefixed plans for the same card in a real `tmp_path` directory, the lexicographically last is chosen; a directory holding only another card's plan yields no match; an empty/`None` listing yields no match. *(Steps; real files on disk for the two-plan case.)*
6. **A missing plans directory is a normal answer, not a failure** — pointing at a `tmp_path` subdirectory that was never created returns `{"found": False, "path": "", "validated": False}` and raises nothing. *(Steps; real absent directory, no fake needed.)*
7. **Validated only when the marker is literally present** — a real plan file containing the marker returns `validated=True`; a sibling plan for another card without it returns `validated=False`. *(Steps; real files.)*
8. **A plan that merely discusses the marker still counts** — a real file whose prose quotes the marker returns `validated=True`, with the test naming this as the accepted cost of a literal, non-regex check. *(Steps; real file.)*
9. **A near-miss marker is not validated** — a file carrying a differently-spelled or differently-spaced marker returns `validated=False`, pinning the constant's exact text. *(Steps; real file.)*
10. **An unreadable plan is found but not validated, and says why** — `found=True`, `validated=False`, and `error` mentions the underlying reason. *(Steps; the one case that fakes `read`, because a real unreadable file cannot be reliably produced, exactly as the `.mjs` test's `fakeFs` does.)*
11. **`has_validated_plan` gates on both flags** — true only for `found and validated`; false for not-found, found-but-unvalidated, and the unreadable-with-`error` result. *(Steps; operates on dicts from the tests above.)*

## 6. Verification

`uv run pytest`. There is no separate lint or typecheck command (CLAUDE.md).
