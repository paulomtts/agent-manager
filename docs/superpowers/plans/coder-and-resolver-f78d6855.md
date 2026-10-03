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

---

# Coder and resolver bundles forbid background-and-wait Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one standing bullet to the `coder` and `resolver` role bundles' `system.md` telling a headless one-shot agent never to background a command and end its turn waiting for a notification, and pin that bullet with a unit-tier golden-brief test.

**Architecture:** A role's `system.md` is read verbatim by `load_role` (`src/agent_manager/roles/loader.py:140-185`) and placed first in every brief by `compose_brief`, so editing the two Markdown files is the whole runtime change. A new test file `tests/roles/test_one_shot_briefs.py` holds the exact rule text as `ONE_SHOT_RULE`, loads each shipped bundle through `load_role`, and checks the rule's presence (whitespace-normalised), its load-bearing phrases, its harness neutrality, its placement, and its Markdown list shape.

**Tech Stack:** Python 3, pytest, `uv`. No new dependencies.

**Spec:** `docs/superpowers/specs/coder-and-resolver-f78d6855.md` (prepended above).

## Global Constraints

- The rule text is exactly B1's bullet; "The words must match; the line wrapping may differ only at whitespace". Use B1's wrapping verbatim in both files.
- Pure ASCII only: "The edit must not introduce smart quotes, em dashes or ellipsis characters".
- Harness-neutral (D6): the rule must "not name a Claude-specific tool or parameter (no `run_in_background`, no `Bash`, no `Monitor`)".
- "Nothing else in either file changes. No heading is added, and no existing bullet is reworded or reordered."
- "`policy.toml`, `VENDORED.lock` and `methodology/` for both roles stay byte-identical." "No Python source under `src/` changes."
- "The rule contains no backticked `` `## name` `` section reference."
- The new test is unit tier (unmarked): it only calls `load_role` on shipped bundles; no subprocess, no git, no model.
- Verification: `uv run pytest` (unit + git tiers). There is no lint or typecheck command.

## Review Focus

1. A stray non-ASCII character (smart quote, em dash, `…`) pasted into the rule from the spec -- T1 compares whitespace-normalised full text against the ASCII `ONE_SHOT_RULE`, so it fails; `test_one_shot_rule_constant_is_ascii` additionally pins the constant itself (Task 1, Step 1).
2. The coder insertion lands inside `## The Plan-Hash trailer` (or after the resolver's last bullet is not where it goes) -- `test_one_shot_rule_sits_in_the_opening_list[coder]` / `[resolver]` asserts the rule follows the named preceding bullet and, for coder, precedes `## The Plan-Hash trailer`; for resolver, ends the file (Task 1, Step 1).
3. The resolver edit breaks an existing merge-rule phrase -- already covered by `tests/roles/test_loader.py::test_the_resolver_prompt_states_the_merge_rules`; run in Task 1, Step 7.
4. A blank line inside the bullet, a missing `- ` marker, or a blank line between the previous bullet and the rule splits the Markdown list -- `test_one_shot_rule_is_one_unbroken_list_item[coder]` / `[resolver]` (Task 1, Step 1).
5. The edit touches `methodology/`, `VENDORED.lock`, `policy.toml` or `src/` Python -- covered by the existing vendored-hash tests plus the `git diff --stat` check in Task 1, Step 8.

---

### Task 1: The one-shot rule in the coder and resolver bundles

**Files:**
- Create: `tests/roles/test_one_shot_briefs.py`
- Modify: `src/agent_manager/roles/bundles/coder/system.md:13` (insert after line 13, before the blank line 14 and `## The Plan-Hash trailer` on line 15)
- Modify: `src/agent_manager/roles/bundles/resolver/system.md:19` (append after line 19, the file's last line)

**Interfaces:**
- Consumes: `agent_manager.roles.loader.load_role(name: str, *, root: Path | None = None) -> RoleBundle`; `RoleBundle.system: str` is the verbatim contents of `system.md`.
- Produces: module constant `ONE_SHOT_RULE: str` in `tests/roles/test_one_shot_briefs.py` (no later task depends on it).

Current file contents, for reference.

`src/agent_manager/roles/bundles/coder/system.md` lines 10-15:

```markdown
- Implement the task in front of you and nothing else. Work the plan's steps
  fully rather than skipping ahead.
- Run the project's verification command before claiming a task is done, and
  report its real output.

## The Plan-Hash trailer
```

`src/agent_manager/roles/bundles/resolver/system.md` lines 16-19 (end of file, with a trailing newline):

```markdown
- Do not touch files that are not conflicted.
- Then write the result file. Say honestly whether you resolved the merge, and
  summarize what you did in each file. Git, not your report, decides whether the
  merge is complete.
```

- [ ] **Step 1: Write the failing tests**

Create `tests/roles/test_one_shot_briefs.py` with exactly this content:

```python
"""Golden brief: the coder and resolver forbid background-and-wait (card f78d6855).

Placement: pygents-engine-design.md §9 (lines 379-402) tests roles as golden
briefs -- the shipped bundle is loaded through the public loader and its
system text is checked for the instruction a one-shot dispatch depends on. No
model call, no git repository, no harness, same tier as `test_loader.py`.
"""

import pytest

from agent_manager.roles.loader import load_role

ONE_SHOT_RULE = """\
- You run headless and one-shot: the process exits the moment your turn ends.
  Run every command to completion in the foreground, however long it takes. If
  a command outlasts your shell tool's default timeout, raise the timeout
  rather than backgrounding it. Never background a command and end your turn
  to wait for a notification. No notification ever comes: the process dies
  without writing the result file, and the dispatch is lost.
"""

ROLES = ("coder", "resolver")

# The bullet each role's rule sits directly after (spec B2).
PRECEDING_LINE = {
    "coder": "  report its real output.",
    "resolver": "  merge is complete.",
}


def _normalised(text: str) -> str:
    return " ".join(text.split())


def _rule_span(text: str) -> tuple[list[str], int, int]:
    """Return the file's lines and the first/last line index of the rule."""
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith("- You run headless")]
    assert len(starts) == 1, f"expected one rule bullet, found {len(starts)}"
    start = starts[0]
    ends = [i for i in range(start, len(lines)) if lines[i].endswith("dispatch is lost.")]
    assert ends, "the rule bullet never reaches 'dispatch is lost.'"
    return lines, start, ends[0]


def test_one_shot_rule_constant_is_ascii():
    assert ONE_SHOT_RULE.isascii()


@pytest.mark.parametrize("role", ROLES)
def test_one_shot_rule_is_in_the_brief(role):
    assert _normalised(ONE_SHOT_RULE) in _normalised(load_role(role).system)


@pytest.mark.parametrize("role", ROLES)
def test_one_shot_rule_names_the_forbidden_pattern_and_its_cost(role):
    text = _normalised(load_role(role).system)
    for phrase in (
        "headless and one-shot",
        "in the foreground",
        "raise the timeout",
        "Never background a command",
        "wait for a notification",
        "dispatch is lost",
    ):
        assert phrase in text, phrase


@pytest.mark.parametrize("role", ROLES)
def test_one_shot_rule_is_harness_neutral(role):
    # D6: a role's text is byte-identical whatever harness runs it. Scoped to
    # this card's rule; test_one_shot_rule_is_in_the_brief ties it to the file.
    assert _normalised(ONE_SHOT_RULE) in _normalised(load_role(role).system)
    for name in ("run_in_background", "Monitor", "Bash"):
        assert name not in ONE_SHOT_RULE, name


@pytest.mark.parametrize("role", ROLES)
def test_one_shot_rule_sits_in_the_opening_list(role):
    text = load_role(role).system
    lines, start, end = _rule_span(text)

    assert lines[start - 1] == PRECEDING_LINE[role]
    if role == "coder":
        assert text.index("- You run headless") < text.index("## The Plan-Hash trailer")
    else:
        assert end == len(lines) - 1, "the rule is not the resolver's last bullet"


@pytest.mark.parametrize("role", ROLES)
def test_one_shot_rule_is_one_unbroken_list_item(role):
    lines, start, end = _rule_span(load_role(role).system)

    assert lines[start - 1].strip(), "a blank line separates the rule from the list"
    for line in lines[start + 1 : end + 1]:
        assert line.startswith("  ") and line.strip(), repr(line)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/roles/test_one_shot_briefs.py -v`

Expected: 
- `test_one_shot_rule_constant_is_ascii` PASSES (it pins the constant only; it is a guard, not a RED test).
- Every `[coder]` and `[resolver]` case FAILS: `test_one_shot_rule_is_in_the_brief` and `test_one_shot_rule_is_harness_neutral` with `AssertionError` on the `in` check; `test_one_shot_rule_names_the_forbidden_pattern_and_its_cost` with `AssertionError: headless and one-shot`; `test_one_shot_rule_sits_in_the_opening_list` and `test_one_shot_rule_is_one_unbroken_list_item` with `AssertionError: expected one rule bullet, found 0`.

That is 1 passed, 10 failed. If any role case passes, stop: the rule is already present or the test is wrong.

- [ ] **Step 3: Add the rule to the coder bundle**

In `src/agent_manager/roles/bundles/coder/system.md`, replace:

```markdown
- Run the project's verification command before claiming a task is done, and
  report its real output.

## The Plan-Hash trailer
```

with:

```markdown
- Run the project's verification command before claiming a task is done, and
  report its real output.
- You run headless and one-shot: the process exits the moment your turn ends.
  Run every command to completion in the foreground, however long it takes. If
  a command outlasts your shell tool's default timeout, raise the timeout
  rather than backgrounding it. Never background a command and end your turn
  to wait for a notification. No notification ever comes: the process dies
  without writing the result file, and the dispatch is lost.

## The Plan-Hash trailer
```

Type the text as plain ASCII: straight apostrophe in `tool's`, plain colons, no em dashes.

- [ ] **Step 4: Run the coder cases to verify they pass**

Run: `uv run pytest tests/roles/test_one_shot_briefs.py -v -k coder`

Expected: 5 passed (every `[coder]` case).

- [ ] **Step 5: Add the rule to the resolver bundle**

In `src/agent_manager/roles/bundles/resolver/system.md`, replace the file's last bullet:

```markdown
- Then write the result file. Say honestly whether you resolved the merge, and
  summarize what you did in each file. Git, not your report, decides whether the
  merge is complete.
```

with:

```markdown
- Then write the result file. Say honestly whether you resolved the merge, and
  summarize what you did in each file. Git, not your report, decides whether the
  merge is complete.
- You run headless and one-shot: the process exits the moment your turn ends.
  Run every command to completion in the foreground, however long it takes. If
  a command outlasts your shell tool's default timeout, raise the timeout
  rather than backgrounding it. Never background a command and end your turn
  to wait for a notification. No notification ever comes: the process dies
  without writing the result file, and the dispatch is lost.
```

Keep exactly one trailing newline at the end of the file, as before.

- [ ] **Step 6: Run the new test file to verify it passes**

Run: `uv run pytest tests/roles/test_one_shot_briefs.py -v`

Expected: 11 passed.

- [ ] **Step 7: Run the role tests and the full default suite**

Run: `uv run pytest tests/roles -v`

Expected: all pass, including every `tests/roles/test_loader.py` shipped-bundle test unchanged (`test_every_shipped_bundle_loads`, `test_shipped_vendored_files_match_their_recorded_hashes`, `test_a_shipped_bundle_vendors_only_methodology_its_system_prompt_names`, and all ten `test_the_resolver_prompt_states_the_merge_rules` cases).

Run: `uv run pytest`

Expected: the whole default (unit + git) suite passes. Report the real summary line.

- [ ] **Step 8: Check the diff is confined to three files and is ASCII**

Run:

```bash
git status --porcelain
git diff --stat
LC_ALL=C grep -nP '[^\x00-\x7F]' src/agent_manager/roles/bundles/coder/system.md src/agent_manager/roles/bundles/resolver/system.md || echo ascii-ok
```

Expected: `git status --porcelain` lists only `M src/agent_manager/roles/bundles/coder/system.md`, `M src/agent_manager/roles/bundles/resolver/system.md` and `?? tests/roles/test_one_shot_briefs.py` (plus nothing under `src/` other than those two files, nothing under `methodology/`, no `policy.toml` or `VENDORED.lock`). `git diff --stat` shows `6 insertions(+)` in each `system.md` and no deletions. The grep prints `ascii-ok`.

Do not stage or commit anything under `docs/superpowers/specs/` or `docs/superpowers/plans/`; the engine commits those.

- [ ] **Step 9: Commit**

```bash
git add tests/roles/test_one_shot_briefs.py \
  src/agent_manager/roles/bundles/coder/system.md \
  src/agent_manager/roles/bundles/resolver/system.md
git commit -F - <<'MSG'
feat: coder and resolver briefs forbid background-and-wait (f78d6855)

A headless one-shot harness exits when its turn ends, so a backgrounded
command whose notification it waits for never reports back and the
dispatch is lost without a result file.

Plan-Hash: <the exact value in your brief's ## plan_hash section>
MSG
```

Replace the `Plan-Hash:` value with the exact hash your brief states, and keep it as the commit message's last line.

---

## Self-review notes

- **Spec coverage.** B1 (exact text): Steps 3 and 5 paste it verbatim; T1 pins it. B2 (placement): Steps 3 and 5; `test_one_shot_rule_sits_in_the_opening_list` pins it. B3 (nothing else changes): Step 8 plus the existing vendored-hash tests in Step 7. Error paths (non-ASCII, empty file): `test_one_shot_rule_constant_is_ascii`, T1, Step 8's grep, and `test_every_shipped_bundle_loads`. Tests T1-T3: all three are in Step 1 under the spec's names, parametrised over `("coder", "resolver")`. Out-of-scope items: no other bundle, no `src/` Python, no `policy.toml`, no `e2e` test.
- **T3 RED.** T3's neutrality check reads only the constant, which cannot fail before the edit; it also asserts the rule is in the file (as the spec says "combined with T1"), so its role cases still go RED in Step 2.
- **Placeholder scan.** The only angle-bracket value is the `Plan-Hash:` trailer, which the coder brief requires be copied from the brief and never computed; nothing else is left to fill in.
- **Type consistency.** `ONE_SHOT_RULE`, `ROLES`, `PRECEDING_LINE`, `_normalised`, `_rule_span` are defined once in Step 1 and used only there.
<!-- task-pipeline: validated -->
