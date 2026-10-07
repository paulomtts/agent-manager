---
name: audit
description: Run this repo's audit axes (placement-boundaries, economy, honesty, single-source-of-truth, test-quality) against a diff or PR (an audit) or a named unit or file list (a swipe) — select the applicable checks, verify each finding adversarially, and report. Report-only; it never changes code. Use whenever the user invokes /audit, asks to "audit this diff/branch/PR", "audit the seam unit", "run the audits", or wants a pre-PR quality pass over changed code.
---

# Audit

## Provenance and adaptations

This skill, its dimensions and `.claude/workflows/audit.js` are a vendored,
adapted copy of the audit suite in `ori` (`.claude/skills/audit/` and
`.claude/workflows/audit.js` at commit `8de7039191dea3d554c2c061053f565d87815da1`).
The sibling `refactor-nori` (commit `ff58e9bacef7f0aec75faaca0f04d0c768b6ba22`)
carries the same `SKILL.md`; its `audit.js` lacks the `docs/superpowers`
pathspec exclusion, so `ori` is the base. This copy is a report-only pilot;
extraction into a shared plugin is a later decision, so every change from the
source is listed here.

Adaptations to `audit.js`:

- Fix phase removed. `args.reportOnly: true` is required and any `args.fix`
  other than `false` throws. `fixModel` and `baseBranch` are gone with it.
- Bundle phase kept as a proposal only: it groups verified findings into
  suggested PR bundles and opens nothing. `bundle: false` skips it. Bundling
  rules rewritten for this repo (frozen contracts alone, layering findings by
  the move that removes them).
- Module scope no longer maps a bounded context with an agent. The source
  prompt named a web-app layout (module, persistence, route and template
  directories, contract and context sheets); this repo has none. `module` now
  takes a unit name plus the file list this skill resolves from the unit map
  below. New `files` scope takes an explicit path list. Both select `unit`
  checks.
- Excluded pathspecs: the vendored static-asset directory is replaced by
  `uv.lock`, `docs/superpowers`, `.claude/worktrees`; the same paths are also
  filtered from any handed-in file list.
- Standards: reads of per-module contract sheets and descriptive docs replaced
  by `docs/standards/**` and `CLAUDE.md`, with known exceptions taken from
  `docs/standards/architecture.md §11`. New optional `standardsRef` lets agents
  read standards from a git ref while the standard is unmerged.
- New optional `dimensions` list restricts selection to the named axes.
- Default base label `main` becomes `master`.
- Output shape unchanged (`scope`, `label`, `status`, `report`, `findings`,
  `bundles`, `fixed`); `fixed` is always `null` and a run with findings ends
  `reported`.

Adaptations to this file: scope table gains `files` and a unit map; git
commands use this repo's exclusions and `master`; fix defaults and fixing-run
reporting removed; the swipe and unit-procedure notes removed (no unit-only
check was imported).

Checks (frontmatter `was:` records the source id; dense renumbering per axis):

| Axis | Kept or adapted | New | Dropped from the source |
|---|---|---|---|
| placement-boundaries | PB1 layer-direction (was PB1), PB2 confinement (was PB2), PB3 private-or-reexported-name (was PB5) | PB4 new-code-placement | PB3 route-composes, PB4 dep-wiring, PB6 misplaced-component, PB7 domain-leak, PB8 script-in-template, PB9 zone-wrapper-re-emission |
| economy | EC1-EC4 (carve-outs rewritten) | — | — |
| honesty | HO1 claim-drift (was HO1), HO2 narrative-docstring (was HO2), HO3 citation-rot (was HO3) | — | HO4 index-drift |
| single-source-of-truth | — | SS1 status-vocabulary, SS2 duplicated-definition, SS3 derived-fact-stored-twice, SS4 restated-contract-drift | SS1-SS11 (SQL idiom, front-end reuse, tokens, CSS, JS idiom, drift sweep) |
| test-quality | TQ1 wrong-tier (was TQ1), TQ2 no-failure-story (was TQ2), TQ6 untested-behavior (was TQ6) | TQ3 duplicated-fake, TQ4 private-seam-patch, TQ5 giant-test-file | TQ3 duplicate-or-mistiered-coverage, TQ4 fixture-convention, TQ5 tier-isolation |

Every mechanical check has `guard: pending`: no architecture test exists yet.
`examples.md` exists only where real examples from this repo were written
(economy has none).

Axes dropped entirely: access-tenancy, usability (no multi-tenant access model,
no UI). Axes not imported yet:

- runtime-coherence — would check that every journal write is fenced by the
  lease, that `am watch`/`am logs --follow` see what the run wrote, and that a
  detached child and its parent agree on run state.
- contract-fidelity — would check that a boundary hands over its declared
  shape: Pydantic models at harness result files, the envelope and exit codes,
  `JournalLine`, and the `brd` argv and output parse.
- domain-correctness — would check that run, story and subtask status
  transitions are guarded on every path (resume, cancel, pause, retry), and
  that a retried step does not duplicate a board write or comment.

## How the suite is organised

The suite is organised by **axis** (a quality we protect), not by code
surface. Each axis is a **dimension** under `dimensions/<axis>/CHECKLIST.md`
(with an optional `examples.md`). Its frontmatter lists the axis's **checks**:
each has an `id` (`PB1`), a `detection` mode (mechanical or judgment),
`scopes` (diff and/or unit), an `applies-to` selector (a comma-separated glob
list; a glob prefixed with `!` excludes), and, for a mechanical check, a
`guard` naming the executable test that owns it (or `pending` while the agent
still audits it). Applied to a **diff or PR** a check is an **audit**; applied
to a **unit** or a file list it is a **swipe**. The standards the checks cite
are `docs/standards/**` (cited as `docs/standards/architecture.md §N`) and
`CLAUDE.md`. Nothing under `docs/superpowers/` is audited or cited.

Your job is small: work out the scope, gather the git facts the workflow
cannot gather itself, and make **one** Workflow call. The workflow owns
everything else — selecting checks, running them, adversarial verification and
bundling. It never changes code.

## Step 1: Resolve the scope

| What the user gave you | Scope |
|---|---|
| nothing, "this diff", "my branch", a base ref | `diff` |
| a PR number, a PR URL, a merge sha | `pr` |
| a unit name from the map below | `module` |
| explicit paths or directories | `files` |

If it is genuinely ambiguous, ask — do not guess between a unit name and a
branch name.

### Unit map

Each unit is a set of git pathspecs; source and the tests that mirror it travel
together.

| Unit | Pathspecs |
|---|---|
| seam | `src/agent_manager/cli.py` `src/agent_manager/orchestrate.py` `src/agent_manager/runs.py` `src/agent_manager/bases.py` `src/agent_manager/integration.py` `tests/test_cli.py` `tests/test_cli_read_only.py` `tests/test_cli_migrate.py` `tests/test_orchestrate.py` `tests/test_runs.py` `tests/test_bases.py` `tests/test_integration.py` |
| store | `src/agent_manager/store` `src/agent_manager/control.py` `src/agent_manager/comments.py` `src/agent_manager/locks.py` `tests/store` `tests/test_store.py` `tests/test_control.py` `tests/test_comments.py` `tests/test_locks.py` `tests/lockhelpers.py` `tests/legacyhelpers.py` `src/agent_manager/migrate.py` `tests/test_migrate.py` |
| runtime | `src/agent_manager/runtime` `src/agent_manager/dispatch.py` `tests/runtime` `tests/test_dispatch.py` `tests/test_engine.py` |
| core | `src/agent_manager/__init__.py` `src/agent_manager/models.py` `src/agent_manager/errors.py` `src/agent_manager/paths.py` `src/agent_manager/census.py` `src/agent_manager/results.py` `src/agent_manager/dag.py` `src/agent_manager/prompt.py` `src/agent_manager/roles/__init__.py` `src/agent_manager/roles/loader.py` `src/agent_manager/workflow` `tests/test_models.py` `tests/test_paths.py` `tests/test_census.py` `tests/test_results.py` `tests/test_dag.py` `tests/test_prompt.py` `tests/test_package.py` `tests/test_integrate_workflow.py` `tests/roles` `tests/workflow` |
| steps | `src/agent_manager/steps` `tests/steps` |
| adapters | `src/agent_manager/board.py` `src/agent_manager/detach.py` `src/agent_manager/harness` `tests/test_board.py` `tests/test_fake_board.py` `tests/test_detach.py` `tests/harness` |
| suite | `tests/conftest.py` `tests/test_conftest_tiers.py` `tests/test_tier_guards.py` `tests/test_readme.py` `tests/test_audit_suite.py` `tests/test_spelling.py` `tests/e2e` `README.md` `CLAUDE.md` `docs/standards` |

## Step 2: Gather the git facts

The Workflow runtime is sandboxed: **it has no filesystem and no shell, so it
cannot run git.** You must run these yourself and pass the results in.

For `diff` scope (default base `master`, or whatever base ref the user named):

```bash
git diff --name-only master...HEAD -- . ':(exclude)uv.lock' ':(exclude)docs/superpowers' ':(exclude).claude/worktrees'
```

If that is empty, tell the user there is nothing to audit and stop.

For `pr` scope, resolve a bare PR number to its merge commit first, then take
that commit's diff:

```bash
gh pr view <number> --json mergeCommit --jq .mergeCommit.oid
git diff --name-only <sha>^1 <sha> -- . ':(exclude)uv.lock' ':(exclude)docs/superpowers' ':(exclude).claude/worktrees'
```

For `module` scope, expand the unit's pathspecs from the map:

```bash
git ls-files -- <pathspecs of the unit>
```

For `files` scope, expand what the user named the same way
(`git ls-files -- <paths>`), so directories become files and untracked or
ignored paths drop out.

If `docs/standards/architecture.md` is not in the checked-out tree, find the
ref that carries it (`git log --all --format=%H -1 -- docs/standards/architecture.md`
and `git branch -a --contains <sha>`) and pass it as `standardsRef`.

## Step 3: Dispatch

Make exactly one call. `reportOnly: true` is always passed; the workflow
refuses to run without it.

```
Workflow({ name: "audit" }, args: {
  scope: "diff" | "pr" | "module" | "files",
  reportOnly: true,      // always
  files: [...],          // every scope except a diff/pr run using listFiles — from step 2
  range: "master...HEAD",// diff scope; for pr scope use "<sha>^1 <sha>"
  pr: 12,                // pr scope only, for labelling
  module: "seam",        // module scope only, the unit name
  base: "master",        // diff scope only, for labelling
  dimensions: [...],     // optional; restrict to these axes
  standardsRef: "...",   // optional; git ref carrying docs/standards/** when absent from the tree
  bundle: false,         // optional; skip the proposed-bundle phase
  notes: "..."           // optional; extra context passed to every agent
})
```

## Step 4: Report

Relay the workflow's `report` field and its proposed `bundles`. Group by
dimension, most severe first, and keep each finding's cited standards section
— the citation is what makes a finding actionable rather than an opinion.
Severity labels are the auditing agent's estimate; treat them as a sort key,
not a priority decision.

State plainly that no code was changed.

## Notes

- Which checks apply to which paths is defined once, in each check's
  `applies-to` frontmatter entry. Do not restate it here and do not select
  checks yourself — the workflow's Select phase reads the frontmatter.
- Mechanical checks with a real `guard` are skipped by default: the test owns
  them. Pass `includeGuarded: true` to run them anyway. Every guard is
  `pending` today.
- `tests/test_audit_suite.py` checks the frontmatter schema, id uniqueness,
  citation form, the unit map and the `.gitignore` exceptions; run it after
  editing any file here.
