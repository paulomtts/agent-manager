# honesty — examples

Every example is real code at `master` `26cca95`. Line numbers drift; re-read
before relying on one.

## HO1 claim-drift

Bad (`src/agent_manager/cli.py:3-8`): the module docstring claims the module
"decides nothing a collaborator already decides" and holds "no step logic, no
gate logic".

```python
This module composes and renders; it decides nothing a collaborator already
decides. ...
constraint: no step logic, no gate logic, no branch strings built by hand, and
no run state written anywhere but through `Store`.
```

The same file decides a run's status (`card_run_status`,
`src/agent_manager/cli.py:808-818`), resets runs and resumes from checkpoints
(`docs/standards/architecture.md §6.1` lists the use cases it holds). The
docstring is the stale side: the fix is a docstring that says what the module
owns today, not a refactor to make the claim true.

Good (`src/agent_manager/board.py:44-45`): the claim ("argv element zero of
every call") is checkable and true — every argv builder in `board.py` starts
with `BRD`.

## HO2 narrative-docstring

Bad (`src/agent_manager/bases.py:1-6`): a spec path, a plan task and a card id
in a module docstring (`docs/standards/architecture.md §8`, rule 3).

```python
"""Build a multi-blocker story's merged base from its blockers' tips.

Supervisor-tree design §5 (`docs/superpowers/specs/2026-09-25-supervisor-tree-design.md`),
plan Task 2.1 (card 06bf46bb). A story blocked by two or more in-milestone
stories roots on `dag.base_branch_name`, a branch this module builds: ...
```

Bad (`src/agent_manager/models.py:28-32`): history and decision ids in a type's
docstring.

```python
"""Lifecycle of a run, story, subtask or phase. `started` is the non-terminal
state resume keys off (§9). `stopped` (addendum P4) is a clean stop on request
between phases: ...
```

Good (`src/agent_manager/dag.py:46`): the contract and nothing else.

```python
    """First eight hex characters of a card UUID, dashes stripped, lowercased."""
```

## HO3 citation-rot

Bad (`CLAUDE.md:54-55`): the source of truth is named as a file under
`docs/superpowers/`, which is untracked; a fresh clone has nothing there.

```markdown
- The design spec is the source of truth:
  `docs/superpowers/specs/2026-09-23-agent-manager-design.md`.
```

The same shape at `README.md:10` and the deferred-work links at
`README.md:522-528` and `README.md:861`.

Not a finding (`README.md:758-759`): the default spec and plan paths the `task`
workflow writes, shown as data.

```text
spec: docs/superpowers/specs/<slug>-design.md
plan: docs/superpowers/plans/<slug>.md
```
