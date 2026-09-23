# Subtask 1535b285 — Add the run state models

Parent story: 8831189b "Foundations: paths, run store and journal". Sibling subtasks: fdebc746 (`paths.py`, done, this subtask is blocked_by it) and ef248597 (SQLite store + append-only journal, blocked on this subtask). Source of truth for the shape: `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §9 (lines 346-368), decisions D4 and D5 (lines 69-70).

## Scope

One module, `src/agent_manager/models.py`, holding the Pydantic models that describe the state of a run: `Run`, `StoryRun`, `SubtaskRun`, `PhaseRun`, `Attempt`, `Dispatch`, plus the `RunConfig` object §9 draws as `config:` under `Run`. Pure data and validation only — no filesystem, no SQLite, no journal writing, no path construction. The module must import cleanly without touching the environment or disk.

Pydantic rather than dataclasses is deliberate: §4's package layout names `models.py` explicitly as "pydantic models (§9)", and D5's projection/journal duality requires lossless, validated round-tripping (see Observable behaviour #2) plus loud rejection of a stale or mistyped journal line — a guarantee plain dataclasses do not give for free. (D4's own validation-and-retry loop is a separate mechanism: it validates phase result files such as `ExploreResult`/`ImplementResult` against harness output, which this subtask's Scope explicitly places out of scope.) This overrides CLAUDE.md's general "plain dataclasses are fine for internal-only state" guidance for this module specifically.

Out of scope, owned elsewhere: phase result models (`ExploreResult`, `CriticResult`, `PlanResult`, `ImplementResult`, `ReviewResult`) belong to the engine story; path derivation is fdebc746's `paths.py`; persistence, projection rebuild and journal line schema are ef248597. Milestone orchestration (census, levels, parallel stories, integrate) and non-Claude harnesses are out of scope for the whole milestone.

## Observable behaviour

The module exposes the six models plus `RunConfig`, nested exactly as §9 draws them:

- `Run` — `id`, `workflow`, `repo_dir`, `base_branch`, `branch_prefix`, `status`, `started_at`, `config: RunConfig`, `stories: list[StoryRun]`.
- `RunConfig` — `max_concurrent_stories`, `dry_run`, `launcher`, `harness_map` (role to `{harness, model}`).
- `StoryRun` — `card_id`, `title`, `level`, `status`, `tip_branch`, `subtasks: list[SubtaskRun]`, ordered and executed sequentially within the story.
- `SubtaskRun` — `card_id`, `branch`, `base_branch`, `worktree_path`, `status`, `phases: list[PhaseRun]`.
- `PhaseRun` — `name`, `kind`, `status`, `started_at`, `ended_at`, `attempts: list[Attempt]`.
- `Attempt` — `n`, `exit_code`, `duration`, `tokens_in`, `tokens_out`, `cost`, `dispatch: Dispatch`, and the three artifact paths (`prompt.txt`, `result.json`, `stdout.log`).
- `Dispatch` — `harness`, `model`, `role`, `cwd`, `prompt_path`, `result_path`.

Behavioural requirements that follow from the constraints:

1. **A run in flight must be representable.** Per §9 resume semantics, an attempt that was started when the process died is recorded as `started` with no terminal event. So every terminal-only field on `Attempt` and `PhaseRun` (`exit_code`, `duration`, token counts, `cost`, `ended_at`) is optional and absent-by-default, and the status values include a non-terminal started state. Constructing an `Attempt` that carries only `n`, `dispatch` and a started status must validate; the engine can then discard it and re-run the phase.
2. **Round-trip fidelity.** Because the DB is a projection and the journal is truth (D5), a full `Run` must serialise and deserialise losslessly — `Run.model_validate(run.model_dump(mode="json"))` equals the original. This is what lets ef248597 rebuild the projection from journal lines without the model losing information.
3. **Attempt identity is addressable.** A journal line carries run id, card, phase, attempt number and a sequence number; the models must expose those five coordinates so a line can be matched to a node in the tree — run `id`, `SubtaskRun.card_id`, `PhaseRun.name`, `Attempt.n`. The sequence number itself lives in the journal, not in these models.
4. **Defaults keep partial construction cheap.** Nested collections (`stories`, `subtasks`, `phases`, `attempts`, `harness_map`) default to empty, so a run can be built top-down as the engine discovers work.

## Error paths

Validation must fail loudly and with readable messages, since the message is the retry signal:

- Missing required identity fields (`Run.id`, `StoryRun.card_id`, `SubtaskRun.card_id`, `PhaseRun.name`, `Attempt.n`, and `Dispatch.harness`/`role`) raise `pydantic.ValidationError`.
- Unknown status values raise `ValidationError` naming the allowed set — a typo'd status must not silently persist.
- Wrong types where coercion is not wanted (e.g. a non-numeric `Attempt.n`, a non-list `stories`) raise `ValidationError`.
- Out-of-range numerics raise `ValidationError`: `Attempt.n` is a positive integer, `RunConfig.max_concurrent_stories` is a positive integer, `duration`/`cost`/token counts are non-negative when present.
- Unknown extra fields are rejected rather than silently dropped, so a stale journal line or a mistyped key surfaces as an error instead of data loss.

## Test list

Tests live in `tests/test_models.py`, mirroring the source layout per CLAUDE.md, written in the precedent style of `tests/test_paths.py` — plain pytest functions, one behaviour per test, module docstring explaining the why.

Tier, per the repo's own tiering in spec §14 (lines 477-495, tiered by *kind of code*, not by unit/integration/e2e): `models.py` is pure, data-only code with no I/O, so **every test below is in the "pure functions" tier** — plain unit tests in the default `uv run pytest` suite, no temporary git repository, no `brd` board, no fake adapter, no harness launcher, and no `tmp_path`/`monkeypatch` fixtures needed. None of these tests belong to the Steps, Adapters, Engine or End-to-end tiers.

1. A minimal `Run` constructs with only its required fields and empty `stories` — pure functions tier.
2. A fully populated four-level tree (`Run` → `StoryRun` → `SubtaskRun` → `PhaseRun` → `Attempt` → `Dispatch`) constructs and preserves nesting and subtask order — pure functions tier.
3. An `Attempt` in flight — started status, no `exit_code`, no `duration`, no token counts, no `cost` — validates, and the corresponding `PhaseRun` validates with `ended_at` absent (§9 resume) — pure functions tier.
4. `Run.model_validate(run.model_dump(mode="json"))` round-trips a fully populated run without loss (D5 projection/journal duality) — pure functions tier.
5. The five journal coordinates (run id, subtask card id, phase name, attempt number) are reachable from a constructed tree, so a journal line can be located — pure functions tier.
6. Missing a required identity field raises `ValidationError` mentioning that field — pure functions tier.
7. An invalid status value raises `ValidationError` listing the permitted values — pure functions tier.
8. A wrongly typed field (non-numeric `Attempt.n`) raises `ValidationError` — pure functions tier.
9. Out-of-range numerics (`Attempt.n` zero or negative, `max_concurrent_stories` zero, negative `cost`) raise `ValidationError` — pure functions tier.
10. An unexpected extra field raises `ValidationError` rather than being dropped — pure functions tier.
11. `RunConfig` defaults and a populated `harness_map` of role to `{harness, model}` both validate — pure functions tier.
12. Nested collection defaults are independent per instance (no shared mutable default leaking between two `Run` objects) — pure functions tier.
13. Importing `agent_manager.models` performs no I/O and requires no environment variables — pure functions tier.

## Verification

- Full suite: `uv run pytest`
- Typecheck: none
- Lint: none
