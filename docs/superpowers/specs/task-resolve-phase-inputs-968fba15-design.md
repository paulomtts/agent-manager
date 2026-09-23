# Resolve phase inputs and render prompts (card 968fba15)

Subtask of story 2143808b "The workflow document and the engine". Blocker ed77a917 ("Run deterministic phases in the engine") is done and its `engine.py` is the base this extends. Source of truth: `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §6 (the phase contract, lines 255-278) and §7 (context plumbing, lines 280-302).

## Scope

Design §6 step 2 — "renders the prompt from the phase's declared `inputs` (§7)" — and nothing either side of it. Step 1 (role bundle) is already served by `roles/loader.py`; step 3 onwards (attempt directory, `result.json` path, launching the harness, schema validation, the four journalled outcomes, retry, escalation) is sibling bf8e415b's and must not appear here.

Delivered:

1. A new module `src/agent_manager/prompt.py` holding the fixed §7 resolution table and the renderer. Given an `AgentPhase` and the engine's per-subtask binding table, it resolves every name in `phase.inputs` and returns a rendered prompt.
2. The rule §7 states, enforced mechanically: **small structured results are inlined; documents are passed by path.** Inlining a spec or a plan would re-bill it on every phase and invite the agent to work from a stale copy of a file it can read live in the worktree it is already sitting in. `spec_path` and `plan_path` therefore reach the prompt as a path string and never as file contents.
3. `spec_path` / `plan_path` population in `engine.py`, derived from the `writes:` field of the `spec` and `plan` phases of `builtin/task.yaml` (`docs/superpowers/specs/{stem}.md`, `docs/superpowers/plans/{stem}.md`) with `{stem}` expanded by `dag.task_stem`. They are computed once at subtask start from the document, not from those phases having run, because `plan_check` may `skip_to: implement` and `implement` still declares both.
4. `subtask_context` (`engine.py`) gains `card: models.Card` and `parent_story: models.Card | None` parameters, populated into the new `card_details` / `parent_story_details` context keys (see the resolution table below). Populating them is the caller's job — this module still never calls `board.show` — but the parameters and the keys they land in are this subtask's addition to `engine.py`, needed before any phase can resolve the `card` or `parent_story` input, or before `spec_path`/`plan_path` can be computed (item 3 needs the same full `Card` for `dag.task_stem`).
5. `RenderedPrompt.write(attempt_dir) -> Path` in `prompt.py`, called by whichever caller has an attempt directory in hand — that caller is bf8e415b's runner, once it creates the directory via `paths.attempt_dir` and receives the `rendered` prompt engine.py already produced. This subtask implements the method and guarantees byte-identical, UTF-8 output; it does not itself call `.write()` from `run_subtask`, and it does not create or know the run layout.

Not in scope: dispatch, `Dispatch` construction, `result.json`, gates over agent results, retry text, journalling of agent-phase outcomes.

## The resolution table

Each name in `inputs` resolves through the fixed §7 table and through nothing else. There is no expression language and no fallback lookup — the same invariant `workflow/registry.py` already enforces for `when:`/gates at load time.

| input name | source | form in the prompt |
|---|---|---|
| `card` | the run's cached `brd show <id>` `models.Card`, in the context as `card_details` | inlined JSON |
| `parent_story` | the parent card, cached the same way, in the context as `parent_story_details` | inlined JSON |
| `repo_docs` | `CLAUDE.md` / `AGENTS.md` at the worktree root | path + first 40 lines |
| `explore` | the validated `ExploreResult` of this subtask's `explore` phase, already in the context under its phase name | inlined JSON |
| `spec_path`, `plan_path` | expanded `writes:` templates | path only |
| `branch`, `base_branch` | computed by `dag.py`, in the context as `branch` / `base` | inlined string |
| `verification` | the commands discovered once per run (`commands` in the context) | inlined JSON |

Note on `card` / `parent_story`: `engine.py`'s existing context key `card` (from `subtask_context`, shipped by ed77a917) already holds the bare card-id **string** that `plan_check.find_validated_plan(card)` and other deterministic steps bind by that name — it is not, and must not become, the full `Card`. The §7 input named `card` therefore reads a *different* context key, `card_details`, and `parent_story` reads `parent_story_details`. `subtask_context` (`engine.py`) gains two new parameters to populate them — `card: models.Card` and `parent_story: models.Card | None` — supplied by the caller exactly like `commands` is today; this module still never calls `board.show` itself ("nothing here reads the board" below still holds). `card_details` and `parent_story_details` join `RESERVED_CONTEXT_KEYS` alongside `spec_path` and `plan_path`.

Input names are the *document's* vocabulary; context keys are the *callees'* parameter names, as `subtask_context` established. The table above is the translation between them, and it is not a one-to-one identity in several places: `base_branch -> base`, `worktree_path -> worktree` (the model field vs. the context key), `card -> card_details`, `parent_story -> parent_story_details`, and `verification -> commands`. New keys added to the context (`spec_path`, `plan_path`, `card_details`, `parent_story_details`) follow the same convention and are added to `RESERVED_CONTEXT_KEYS`, so a phase named `spec_path` (or `card_details`, etc.) could never overwrite the value every later phase binds.

`spec_path` and `plan_path` are computed once at subtask start from `writes:` templates whose `{stem}` is `dag.task_stem(card_details)` — the same full `Card` the `card` input reads, not the bare id string in the reserved `card` key, because `task_stem` needs the title to slug. This is why `card_details` must be populated before any phase runs, whether or not that phase declares `card` among its own `inputs`.

`repo_docs` resolves against the worktree root when the subtask has one and against `repo_dir` when it does not: `explore` is the first phase in `builtin/task.yaml` and the `worktree` phase runs two phases later, so at explore time there is no worktree to read from. Only whole files that exist contribute; a repo with neither file renders a stated absence rather than failing.

## Observable behaviour

- `render_prompt(phase, context)` returns a `RenderedPrompt` carrying the prompt text and the resolved inputs. It is pure: same phase and same context produce byte-identical text, and sections appear in the order the document declares them in `inputs`, each under a header naming the input. No clock, no randomness, no dict-ordering dependence.
- `RenderedPrompt.write(attempt_dir) -> Path` writes UTF-8 `prompt.txt` into an existing attempt directory and returns its path. It does not create the directory and does not know the run layout; `paths.attempt_dir` and its caller remain bf8e415b's.
- The role's `system.md` and methodology are *not* folded into `prompt.txt`. §8 says `Dispatch` carries the prompt text and the role bundle as separate fields, and the adapter materialises the bundle.
- The agent seam in `engine.py` widens from `(phase, context) -> result` to `(phase, context, rendered) -> result`. The engine resolves inputs and renders before calling the runner, which is exactly where §6 puts step 2, and the runner cannot dispatch without a rendered prompt it can write to disk first. Its docstring keeps saying that everything past the call belongs to bf8e415b.
- Nothing here reads the board, spawns a process or touches the network. Card and parent-story JSON arrive already cached in the context; the renderer only formats them.

## Error paths

All failures raise `engine.EngineError`, which already carries phase/function/parameter coordinates, and none of them return a partial prompt:

- An input name not in the §7 table: named, with the phase and the list of names that are.
- A declared input with nothing in the context — `explore` when the walk was started at a later phase, or `card` on a context whose `card_details` was never populated: named as such, distinguished from "unknown name".
- A `writes:` template containing a placeholder other than `{stem}`, or a phase whose `inputs` mention `spec_path`/`plan_path` while no phase in the document `writes:` a spec or a plan.
- An unreadable `CLAUDE.md`/`AGENTS.md` (permissions, non-UTF-8). Absence is not an error; unreadability is.
- `write` onto a directory that does not exist, surfaced with the path rather than as a bare `OSError`.

Because the current walk does not wrap the agent-runner call, a resolution failure propagates out of `run_subtask` as `EngineError`. Turning it into a journalled outcome is bf8e415b's choice, not a behaviour invented here.

## Tests

Tier per design §14 lines 477-492. Tests mirror `src/` 1:1 under `tests/` (CLAUDE.md); there are no `unit/` or `integration/` directories.

**Pure-function tier — `tests/test_prompt.py`, colocated with the module, no store, no git, no brd, no harness:**

1. Every one of the nine §7 names (`card`, `parent_story`, `repo_docs`, `explore`, `spec_path`, `plan_path`, `branch`, `base_branch`, `verification`) resolves to its stated form, table-driven against the row list.
2. `spec_path` and `plan_path` render as a path only: with the file present on disk holding a sentinel string, the sentinel never appears in the prompt text.
3. `card`, `parent_story`, `explore` and `verification` inline as JSON that round-trips through `json.loads`.
4. `repo_docs` renders path plus at most 40 lines, and marks the truncation.
5. `repo_docs` reads from `repo_dir` when the subtask has no worktree yet (the `explore` case) and from the worktree root once it does.
6. `repo_docs` with neither file present renders a stated absence and does not raise.
7. `base_branch` binds the context key `base`, and `branch` binds `branch`.
8. Rendering is deterministic and section order follows the declared `inputs` order.
9. `{stem}` expansion of a `writes:` template matches `dag.task_stem`; an unknown placeholder raises `EngineError`.
10. `write` produces UTF-8 `prompt.txt` at the returned path, overwriting an existing one.
11. Error paths: unknown input name, unresolvable input, missing `writes:` source, unreadable repo doc — each raising `EngineError` naming the phase and the input.

**Engine tier — `tests/test_engine.py`, the pattern already established there: canned fake functions in a hand-built `FunctionRegistry` standing in for §14's fake adapter, a real temp SQLite projection and a real temp JSONL journal, no git, no brd, no harness process:**

12. Walking `builtin/task.yaml` with a recording fake agent runner: `validate_spec`, `plan`, `validate_plan`, `implement` and `review` each receive a rendered prompt whose `spec_path`/`plan_path` equal the expanded `writes:` templates.
13. When `plan_check` opens its `skip_to: implement`, `implement` still receives both paths — they do not depend on the `spec` and `plan` phases having run.
14. Each agent phase's rendered prompt contains exactly the inputs that phase declares and no others (§13: each phase receives exactly its declared inputs).
15. A phase declaring an input the context cannot supply raises `EngineError` out of the walk and the fake runner is never called.
16. `spec_path`/`plan_path` are reserved: a phase named `spec_path` does not clobber the context value, and a deterministic step declaring a `spec_path` parameter binds the path.
17. `subtask_context(subtask, repo_dir, commands, card=..., parent_story=...)` places its `card` argument under `card_details` and its `parent_story` argument under `parent_story_details`, leaving the existing `card` key (the bare id string) untouched; the `explore` phase in a full `builtin/task.yaml` walk (test 12/14) resolves its `card` and `parent_story` inputs from these, not from the reserved `card` key.

No end-to-end tier test: nothing here executes a harness.
