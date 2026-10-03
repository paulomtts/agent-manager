# Coder and resolver bundles forbid background-and-wait (f78d6855)

Subtask of story f1a29ec3 "A harness that dies without a result gets one
retry". The story's motivating failure: milestone 17's implement harness
backgrounded a test run and ended its one-shot `claude -p` turn "waiting for the
notification". The process exited, no result file was written, the attempt was
journalled `harness_error`, and the whole run escalated. The sibling card
1fadbbdd (already merged on this branch, `dispatch.py:407-435`) makes the engine
give such an attempt one more dispatch. This card makes the failure less likely
in the first place, through role prompt text.

This card changes prose only. It adds one standing rule to two role bundles'
`system.md`. It also adds one unit-tier test file that pins that rule.

## Inherited constraints

- **Where the rule lives.** A role's standing instructions are its
  `roles/bundles/<role>/system.md` (design spec §8, lines 324-326). The loader
  reads that file verbatim, with no templating (`roles/loader.py:158`,
  `_read_system` at 181-185). `compose_brief` puts it first in every brief
  (`prompt.py:449`). The rule therefore reaches every coder and resolver
  dispatch with no code change.
- **Loader validation must stay green.** The only check on `system.md` is that
  it exists, decodes as UTF-8, and is not blank (`loader.py:181-185`). The
  shipped-bundle tests in `tests/roles/test_loader.py` (lines 484-576) must
  keep passing unchanged. They include:
  - every bundle loads;
  - the methodology a bundle vendors is named in its `system.md` (lines
    529-537);
  - the resolver's merge-rule phrases (lines 558-576, including "result file")
    are present.
- **Byte-identical instructions across harnesses (D6, design spec line 71;
  §13 line 498).** A role's text is the same whatever harness runs it. The rule
  must therefore not name a Claude-specific tool or parameter (no
  `run_in_background`, no `Bash`, no `Monitor`). It speaks of "a command",
  "your shell tool" and "a notification".
- **What the failure costs.** A missing result file after the harness exits is
  a `harness_error` (design spec §6, lines 280-281; `dispatch.py:classify`).
  The first `harness_error` of a phase call gets one free redispatch. A second
  one is counted against the phase's budget and normally escalates
  (`dispatch.py:407-435`). The rule's stated consequence must be true to this:
  the process dies, no result file is written, and the dispatch is lost.
- **Golden-brief convention.** The pygents addendum §9 (lines 379-402) tests
  roles as golden briefs: the shipped bundle is loaded through `load_role`, and
  its `system` text is asserted to contain needles. There is no model call, no
  git, no harness (`tests/roles/test_reviewer_brief.py`,
  `tests/roles/test_critic_briefs.py`).
- **Test tier.** A test that only loads a shipped bundle from disk spawns
  nothing. It is the unmarked `unit` tier (design spec §14 table, line 519;
  CLAUDE.md "Test tiers").
- **Existing voice.** Both bundles are flat bullet lists in the present tense
  and second person, wrapped at about 80 columns. Each bullet is a directive
  followed by its consequence. `coder/system.md:25-26` is the model for this:
  "Never leave a commit untagged. An untagged commit is debris, and the review
  phase stops the whole run on it."

## Observable behavior

### B1. The rule text

Both bundles carry this exact bullet. The words must match; the line wrapping
may differ only at whitespace:

```markdown
- You run headless and one-shot: the process exits the moment your turn ends.
  Run every command to completion in the foreground, however long it takes. If
  a command outlasts your shell tool's default timeout, raise the timeout
  rather than backgrounding it. Never background a command and end your turn
  to wait for a notification. No notification ever comes: the process dies
  without writing the result file, and the dispatch is lost.
```

Here is why each clause is there:

- **"headless and one-shot … exits the moment your turn ends"** states the
  fact the agent lacks. In an interactive session, ending the turn does not end
  the process.
- **"Run every command to completion in the foreground, however long it
  takes"** is the positive instruction. It is unambiguous for the long
  `uv run pytest -m e2e_fake` tier (≤8 min), which is exactly the kind of run
  that tempts an agent to background it.
- **"raise the timeout rather than backgrounding it"** closes the obvious
  escape route. A shell tool with a short default timeout is the usual reason
  an agent reaches for backgrounding. It stays harness-neutral under D6.
- **"Never background a command and end your turn to wait for a
  notification"** names the forbidden pattern in the words the failing
  transcript used.
- **"No notification ever comes … the dispatch is lost"** is the consequence,
  in the `coder/system.md:25-26` style. It is true under the 1fadbbdd
  redispatch: that redispatch is a safety net, not something the agent may
  count on.

### B2. Placement

- **`coder/system.md`**: a new bullet in the opening list, directly after the
  "Run the project's verification command before claiming a task is done…"
  bullet (currently lines 12-13). It is the last bullet before
  `## The Plan-Hash trailer`. Verification is the command most likely to be
  long, so the rule sits next to it.
- **`resolver/system.md`**: a new bullet appended as the last item of the list,
  after the "Then write the result file…" bullet (currently lines 17-19, the
  file's last bullet). The existing bullets' order and the "Then" sequence are
  left intact.

Nothing else in either file changes. No heading is added, and no existing
bullet is reworded or reordered.

### B3. What does not change

- `policy.toml`, `VENDORED.lock` and `methodology/` for both roles stay
  byte-identical. The vendored-hash tests (`test_loader.py:499-510`) keep
  passing.
- No Python source under `src/` changes.
- The rule contains no backticked `` `## name` `` section reference. That keeps
  it clear of the critic-brief section-name check
  (`tests/workflow/test_task.py:293-302`), even though that check covers critic
  roles only.

## Error paths

This card adds no new runtime error path. The only failure it could cause is a
loader failure, if the edit left `system.md` empty or not valid UTF-8. That is
already reported as `RoleBundleError` (`loader.py:170-185`) and is caught by
`test_every_shipped_bundle_loads`. Both `system.md` files are pure ASCII
today, and B1's bullet is pure ASCII too. The edit must not introduce smart
quotes, em dashes or ellipsis characters: the `…` in this spec's prose is
commentary, not part of the rule.

## Tests

One new file, `tests/roles/test_one_shot_briefs.py`, unit tier (unmarked). It
only calls `load_role` on shipped bundles: no subprocess, no git, no model
(design §14 line 519). Module docstring follows `test_reviewer_brief.py`'s
pattern: card id, placement rationale citing pygents addendum §9.

| # | Test | Tier | Why this tier | Proves |
|---|---|---|---|---|
| T1 | `test_one_shot_rule_is_in_the_brief[coder]` / `[resolver]`, parametrised over `("coder", "resolver")` | unit | Loads a shipped bundle from disk only | The B1 bullet is present verbatim in each bundle. The check compares `" ".join(text.split())` against the whitespace-normalised B1 text, so a re-wrap passes but a reworded clause fails. |
| T2 | `test_one_shot_rule_names_the_forbidden_pattern_and_its_cost[coder]` / `[resolver]` | unit | Same | Each load-bearing phrase is present on its own: `"headless and one-shot"`, `"in the foreground"`, `"raise the timeout"`, `"Never background a command"`, `"wait for a notification"`, `"dispatch is lost"`. On failure the assertion message names the missing phrase, which T1's one long string cannot do. |
| T3 | `test_one_shot_rule_is_harness_neutral[coder]` / `[resolver]` | unit | Same | The `ONE_SHOT_RULE` constant contains none of `run_in_background`, `Monitor`, `Bash` (D6, design spec line 71). Combined with T1, this proves the shipped rule is harness-neutral. The check is scoped to the rule, not the whole `system.md`, so it constrains only this card's text. |

The suite stays green: `uv run pytest` (unit + git), including every existing
`tests/roles/test_loader.py` shipped-bundle test unchanged. No `e2e_fake`
test is added. Whether a real model obeys the rule cannot be observed under the
fake `claude`. A real-`claude` `e2e` test would count against the hard cap of 5
(design §14 line 524) for a prompt nudge whose backstop, 1fadbbdd, is already
tested. That is out of scope.

## Out of scope

- Every other role's bundle (`explorer`, `spec_author`, `planner`, the critics,
  `reviewer`). This is named explicitly by the card.
- Any engine behaviour: the redispatch of a `harness_error` belongs to sibling
  card 1fadbbdd and is already merged. Detecting backgrounded work, or
  extending timeouts in the adapter, is not part of this card.
- `policy.toml` changes, such as removing a background-capable tool from
  `allowed_tools`. The card asks for instruction text, and the tool list is
  harness-neutral by name.
- Measuring whether the model complies (no `e2e` test, see Tests).

## Handoff to the planner

Follow the writing-plans format in the brief.

**File structure:**

- Modify: `src/agent_manager/roles/bundles/coder/system.md`. Insert after
  line 13.
- Modify: `src/agent_manager/roles/bundles/resolver/system.md`. Append after
  line 19 (the file's last line).
- Create: `tests/roles/test_one_shot_briefs.py`.

**Task right-sizing:** this is one task. The test file holds the B1 text as a
module constant `ONE_SHOT_RULE`. T1-T3 are written first and fail for both
roles. Both bundles are then edited, and the task is one commit. A reviewer
could not meaningfully approve the coder edit while rejecting the resolver
edit: they are the same sentence.

**Review Focus candidates** (inputs the tests do not otherwise exercise):

1. The rule is re-wrapped with a stray character, such as a smart quote or an
   em dash pasted from the spec. T1 must then fail, so it has to compare the
   normalised text, not substrings only.
2. The insertion lands inside the `## The Plan-Hash trailer` section instead
   of the opening list. A placement assertion catches this: in coder, the rule's
   index is less than the index of `"## The Plan-Hash trailer"`.
3. The resolver edit breaks an existing merge-rule phrase. This is already
   covered by `test_the_resolver_prompt_states_the_merge_rules`; run it.
4. A blank line inside the bullet, or a missing `- ` marker, splits the
   markdown list. Guard it in the test: the first line of the rule in each
   file starts with `- You run headless`, and every following line up to
   `dispatch is lost.` is indented by two spaces.
5. The edit touches `methodology/` or `VENDORED.lock`. This is covered by the
   existing vendored-hash tests; keep the diff to the two `system.md` files and
   the new test.
