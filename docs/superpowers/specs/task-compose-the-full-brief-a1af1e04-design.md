# Compose the full brief — subtask a1af1e04

Parent story: 760dd05c, "The brief: role, methodology, inputs and the result contract in one prompt". Milestone: 7aa00a90. Blocked by 5cc741ec (result models, R1).

Narrowing of decision R2 in `docs/superpowers/specs/2026-09-23-real-harness-design.md` §2. This card delivers the composer only. Writing the brief into an attempt's `prompt.txt`, the retry path, the claude adapter's `-p` pointer and the CLI flags are siblings c0a4bc75 and 19efcddc.

## Scope

One pure function in `src/agent_manager/prompt.py` (the composer sits beside `render_prompt`, which produces its third section; a sibling module is acceptable but buys nothing). Signature, near enough:

```python
def compose_brief(
    role: RoleBundle,
    rendered: RenderedPrompt,
    *,
    result_path: Path | str | None = None,
    result_model: type[BaseModel] | None = None,
    feedback: str | None = None,
) -> str
```

It returns the whole brief as text. It does not touch disk, the clock, randomness, the board or any process. It does not read `results.RESULT_MODELS` — the caller resolves the phase's model and hands the class (or its already-computed schema) in, which is what keeps the function pure and testable without the blocking story's models existing.

Also in scope, and only this: read all six `src/agent_manager/roles/bundles/*/system.md` (coder, critic, explorer, planner, reviewer, spec_author) and fix, minimally, any that fails to tell its role what to do. Exploration found all six already carry role instructions, so the expected diff is zero or near-zero. The one thing worth a second look is that `coder/system.md:5`, `planner/system.md:6` and `spec_author/system.md:11` cite `methodology/<file>.md` as a path; under this card those files become headings inside the same document. If the wording is edited at all, it is edited to point at the heading — not rewritten, not expanded.

Out of scope: `dispatch._attempt`, `RenderedPrompt.write`, `--append-system-prompt` (R2 deviates from main-spec §8 deliberately; the brief stays on disk), and everything in §4 of the addendum (orchestration, parallel stories, integrate, non-Claude harnesses, Store thread-safety, roll-up).

## Observable behavior

The returned text is one document, sections in exactly this order:

1. `role.system`, verbatim, first.
2. One section per entry of `role.methodology`, each under its own `##` heading naming the file (for example `## methodology: test-driven-development.md`), body verbatim. Iteration follows the dict's insertion order, which `roles/loader._read_methodology` fills from `VENDORED.lock`. A role with no methodology (critic, explorer, reviewer) contributes nothing here — no empty heading.
3. `rendered.text`, verbatim: the `# phase:` / `# role:` header plus the `## <input>` sections `render_prompt` assembled.
4. A `## Result contract` section, present **only** when a result is asked for. It states the absolute `result.json` path, says to write valid JSON to that path, says the path is deliberately outside the worktree so the file must not be created inside it, and embeds `result_model.model_json_schema()` rendered as indented JSON in a fenced block. When no result is asked for (`AgentPhase.result is None`, so the caller passes neither path nor model), the section is absent entirely — no heading, no placeholder.
5. When `feedback` is given and non-empty, a feedback section last, under the exact same heading text as today's `dispatch.FEEDBACK_HEADING` (`## feedback on the previous attempt`).

Joining: sections are separated by a blank line (normalise each section's trailing newlines so exactly one blank line separates neighbours, without altering interior text) and the brief ends with a single newline. Appending feedback must only add text after the base brief, so the no-feedback brief stays a prefix of the with-feedback one (test 9). Test 3's "immediately followed" means no methodology heading between them, not zero separator characters.

Two different roles produce different briefs for the same rendered prompt, because section 1 (and usually 2) differ.

Import direction: `dispatch` imports `prompt`, never the reverse. So the heading constant moves to `prompt.py` as `FEEDBACK_HEADING` and `dispatch` keeps its name bound to the imported constant (`FEEDBACK_HEADING = prompt.FEEDBACK_HEADING`), so `dispatch.with_feedback` and existing tests keep working and the two can never disagree. Taking the heading as a parameter is an acceptable alternative; the constant must not be duplicated.

Feedback is composed last and appended after the contract, so a retry can be produced by appending only the feedback block to the base brief. That is the arrangement sibling c0a4bc75 needs to avoid duplicating the contract on retry; this card only has to make it possible, not wire it.

Types follow CLAUDE.md: the composer consumes existing pydantic models (`RoleBundle`) and dataclasses (`RenderedPrompt`) and returns a plain `str`. It introduces no new model.

## Error paths

- `result_path` given without `result_model`, or `result_model` without `result_path`: `EngineError` carrying the phase (`rendered.phase`), naming both parameters. A contract half-specified is a caller bug, not something to render partially.
- `result_path` that is not absolute: `EngineError` naming the phase and the path. The contract's whole value is that the agent is told an unambiguous location outside the worktree it is `cd`'d into; a relative path would resolve inside it.
- `model_json_schema()` raising, or its output not being JSON-serialisable: allowed to propagate. A model that cannot describe itself is a defect in the result-models story, not a condition to paper over.
- Empty or whitespace-only `feedback`: treated as no feedback, no section. Matches the "no result, no contract section" rule — the composer never emits an empty heading.
- No new failure mode for role content: `roles/loader.load_role` already rejects an empty `system.md`, so the composer may assume `role.system` is non-empty.

## Test list

Tier, per `docs/superpowers/specs/2026-09-23-agent-manager-design.md` §14 "Testing": the composer is a pure function, so **every test below is a plain unit test** calling it directly, in `tests/test_prompt.py`. No temp git repo, no temp brd board, no launcher, no fake adapter, no disk, no `pytest` marker. §14's step tier, adapter tier, engine-with-fake-adapter tier and the opt-in real-harness end-to-end test are all inapplicable here, and addendum R4's fake-`claude` wiring test belongs to sibling c0a4bc75. Fixtures are a synthetic `RoleBundle` (or `load_role` against a synthetic `root`, as `tests/roles/test_loader.py` already does) and a small local `BaseModel` standing in for a result model.

1. Composition order: with a role that has system text and two methodology files, a rendered prompt and a result contract, the index of each landmark in the output is strictly increasing — system, methodology 1, methodology 2, rendered text, `## Result contract`.
2. Each methodology filename appears as its own heading, and each body appears verbatim.
3. A role with an empty `methodology` dict yields a brief with system text immediately followed by the rendered text and no methodology heading.
4. The contract section contains the absolute result path as given, and the embedded schema contains the stand-in model's property names (round-trip the fenced JSON and compare to `model_json_schema()` rather than matching strings).
5. The contract instructs writing valid JSON and keeping the file outside the worktree (assert on the path and on the presence of the instruction, not on exact prose).
6. No result: passing neither path nor model omits `## Result contract` entirely.
7. Two roles differ: composing the same `RenderedPrompt` under two different bundles yields two different briefs, and each contains its own system text and not the other's.
8. Feedback appended: with `feedback=`, the brief ends with `prompt.FEEDBACK_HEADING` followed by the feedback text, positioned after the contract; without it, the heading is absent.
9. Retry shape: the no-feedback brief is a prefix of the with-feedback brief (the property c0a4bc75 relies on so a retry appends rather than recomposes).
10. Error — `result_path` without `result_model` and the converse each raise `EngineError` mentioning the phase.
11. Error — a relative `result_path` raises `EngineError`.
12. Purity: the same arguments produce byte-identical output on two calls, and composing does not create `prompt.txt` or any file in a `tmp_path` handed in as the result path's parent.
13. Blank feedback (`""` and `"   "`) produces no feedback section.

Existing `tests/test_prompt.py` and `tests/test_dispatch.py` must keep passing unchanged, including whatever asserts on `dispatch.FEEDBACK_HEADING`.
