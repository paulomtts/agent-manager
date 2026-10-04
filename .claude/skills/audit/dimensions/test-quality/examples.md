# test-quality — examples

Every example is real code at `master` `26cca95`. Line numbers drift; re-read
before relying on one.

## TQ3 duplicated-fake

Bad: three `FakeResolver` classes. Two play the same role — an in-process
`LauncherFn` double that resolves or refuses a merge in its worktree — and
have already diverged in how they learn to refuse
(`tests/test_bases.py:401-412`, a `refuse` field;
`tests/test_integration.py:275-284`, a `REFUSE_ENV` variable).

```python
@dataclass
class FakeResolver:
    """A `LauncherFn` double playing fake `claude`'s resolver mode in its cwd.
```

```python
@dataclass
class FakeResolver:
    """A `LauncherFn` double that plays the resolver in the worktree it runs in.
```

Not a duplicate (`tests/e2e/conftest.py:355-363`): the third `FakeResolver`
switches the fake `claude` binary through an environment variable; a different
role under the same name.

## TQ4 private-seam-patch

Bad (`tests/test_cli.py:2254`, a known exception in
`docs/standards/architecture.md §11`): the launcher is patched on the `cli`
module's alias instead of injected.

```python
    monkeypatch.setattr(cli, "run_direct", forbidden)
```

Good (`tests/test_bases.py:492`): the same collaborator passed through its seam
(`docs/standards/architecture.md §5`, row 5.14).

```python
        runner_factory=factory,
```

## TQ5 giant-test-file

Bad: `tests/test_cli.py` (12466 lines), `tests/test_orchestrate.py` (9187) and
`tests/test_store.py` (6483) each mirror one module that
`docs/standards/architecture.md §6` splits into several; their tests do not
split along the same lines yet.
