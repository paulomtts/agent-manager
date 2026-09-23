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
