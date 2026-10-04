# single-source-of-truth — examples

Every example is real code at `master` `26cca95`. Line numbers drift; re-read
before relying on one. Several bad examples are known and scheduled
(`docs/standards/architecture.md §5`, `docs/standards/architecture.md §9`,
`docs/standards/architecture.md §11`); they show the shape a new violation has.

## SS1 status-vocabulary

Bad (`src/agent_manager/models.py:25-27` against
`src/agent_manager/census.py:45`): the run status vocabulary spells
`cancelled`, while the `brd` card vocabulary `am` reads spells `canceled`.
`docs/standards/architecture.md §9` makes `canceled` canonical.

```python
Status = Literal[
    "pending", "started", "done", "failed", "escalated", "stopped", "cancelled"
]
```

```python
OUT_OF_PLAY_STATUSES = frozenset({"canceled", "archived"})
```

Bad (`src/agent_manager/store.py:1957`): a reader matching one spelling only.

```python
                or newest["status"] == "cancelled"
```

## SS2 duplicated-definition

Bad: five private copies of one clock helper
(`docs/standards/architecture.md §5`, row 5.12) at
`src/agent_manager/cli.py:201`, `src/agent_manager/orchestrate.py:310`,
`src/agent_manager/control.py:43`, `src/agent_manager/dispatch.py:323`,
`src/agent_manager/runtime/walk.py:220`.

```python
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
```

Bad: one default timeout written twice, once as a model default
(`src/agent_manager/models.py:67`) and once as a module constant whose
docstring says it must match (`src/agent_manager/dispatch.py:131-133`).

```python
    timeout: float = Field(default=1800.0, gt=0, allow_inf_nan=False)
```

```python
DEFAULT_TIMEOUT = 1800.0
"""Wall-clock seconds one attempt gets, matching `models.Dispatch.timeout`'s own
default ..."""
```

Good (`src/agent_manager/workflow/task.py:31`): a reader derives from the one
constant instead of restating `1800`.

```python
LAUNCHER_TIMEOUT = timedelta(seconds=dispatch.DEFAULT_TIMEOUT)
```

## SS3 derived-fact-stored-twice

Good (`src/agent_manager/census.py:50` and
`src/agent_manager/steps/rollup.py:50`): derived status sets are computed from
the sets that own the facts, so adding a status to `OUT_OF_PLAY_STATUSES`
updates both.

```python
LANDED_STATUSES = frozenset({"merged"}) | OUT_OF_PLAY_STATUSES
```

```python
_HUMAN_TERMINAL = (FINISHED_STATUSES | OUT_OF_PLAY_STATUSES) - {"done"}
```

Bad shape: the same grouping written as a fresh literal, e.g.
`frozenset({"merged", "canceled", "archived"})`, in a third module.

## SS4 restated-contract-drift

Calibration: `tests/test_readme.py` already pins part of the README contract
(the `am runs` keys, the detach envelope, the board-run section, `am watch
--from-now`, the `am logs` shape and examples). A README fact
that file checks is guarded; report only facts it does not cover.
