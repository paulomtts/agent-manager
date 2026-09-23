# Add paths and the run artifact layout

Subtask `fdebc746-1042-4965-8a19-40a48c747973`, under story `8831189b` ("Foundations: paths, run store and journal").

## Scope

One new module, `src/agent_manager/paths.py`, plus its unit tests in `tests/test_paths.py`. The module is the single place that decides where anything `agent-manager` writes lives on disk: the per-user data directory, the per-project database file, and the run/attempt artifact tree described in section 9 of the design spec (`docs/superpowers/specs/2026-09-23-agent-manager-design.md:346-387`).

It mirrors `/home/paulomtts/Code/brd/src/brd/paths.py` for the first two functions and extends it with the two run-tree functions that `brd` has no equivalent of.

Explicitly **not** in this subtask: run state models (`models.py`, sibling `1535b285`), the SQLite store and journal (`store.py`, sibling `ef248597`), phase result models, and anything to do with milestone orchestration or non-Claude harnesses. `paths.py` computes and creates directories; it never writes a file, never opens a database, and never shells out to git or `brd`.

No Pydantic here. Per CLAUDE.md, Pydantic is for values validated at a process boundary; this is pure filesystem logic, so plain module-level functions returning `pathlib.Path` are the right shape.

## Observable behaviour

`data_dir() -> Path` — returns `$XDG_DATA_HOME/agent-manager` when `XDG_DATA_HOME` is set to a non-empty value, otherwise `$HOME/.local/share/agent-manager`. An empty-string `XDG_DATA_HOME` is treated as unset, matching `brd`. The directory is created with `mkdir(parents=True, exist_ok=True)` before it is returned, so callers may assume it exists. Calling it repeatedly is idempotent.

`project_db_path(root: Path) -> Path` — returns `data_dir()/projects/<digest>.db`, where `<digest>` is the hex sha256 of `str(root.resolve())`. The `projects/` parent is created before the path is returned; the `.db` file itself is not created. Resolution happens before hashing, so two spellings of the same repo (relative path, symlink, trailing `..`) key to the same database, and two distinct repo roots key to different ones. The parameter is named `root` (the `brd` original calls it `root_path`); behaviour is otherwise identical.

`run_dir(run_id: str) -> Path` — returns `data_dir()/runs/<run_id>`, created with `mkdir(parents=True, exist_ok=True)`. This is the root of the artifact tree for a single run: the journal JSONL and every card's attempt directories hang off it. Because it is anchored at `data_dir()`, it is by construction outside any repository worktree.

`attempt_dir(run_id: str, card: str, phase: str, attempt: int) -> Path` — returns `run_dir(run_id)/<card>/<phase>.<attempt>`, created with `mkdir(parents=True, exist_ok=True)`. This is the directory the engine hands to a dispatch and into which the three per-attempt artifacts land: `prompt.txt`, `result.json`, `stdout.log` (design spec line 362). `attempt` is formatted as a plain decimal integer, so attempt 2 of the implement phase for card `abc123` is `.../abc123/implement.2`.

### The load-bearing invariant

Attempt directories must live **outside the repository worktree**. The design spec is explicit about why (lines 265-268): the harness runs with cwd set to the subtask worktree, and a `result.json` or `stdout.log` written inside that worktree would either fail the verify step's clean-tree check or be swept into a commit. Anchoring the whole run tree at `data_dir()` — never at the repo root, never at a path derived from a worktree — is what enforces this. No function in this module accepts a worktree path or a repo root as the base of the run tree; `project_db_path` takes a repo root only to hash it, never to write under it.

Paths are computed, not validated: the module makes no assertion that a run id or card id is well-formed, and does no escaping of them. That is the caller's concern, consistent with `brd`.

## Error paths

- `XDG_DATA_HOME` unset (or empty) and `HOME` unset: `KeyError` from `os.environ["HOME"]`. Same as `brd`; not caught or reworded here.
- `data_dir()` or any `mkdir` fails because of a permissions problem or a non-directory in the way: the underlying `OSError` propagates unchanged. This module adds no error envelope; CLI-level `{"ok": false}` formatting belongs to the CLI layer, not here.
- `root.resolve()` on a non-existent path: `pathlib` resolves non-strictly, so this returns a path rather than raising. A project database may therefore be keyed for a root that does not exist yet; that is intentional and matches `brd`.

## Test list

All tests below are **Pure functions tier** per section 14 of the design spec (`docs/superpowers/specs/2026-09-23-agent-manager-design.md:477-492`). `paths.py` is pure computation over environment variables plus idempotent directory creation, with no git repository, no `brd` board, no harness and no network — so none of the Steps, Adapters, Engine or End-to-end tiers apply. They live in `tests/test_paths.py` and use `monkeypatch` and `tmp_path` in the style of `/home/paulomtts/Code/brd/tests/test_paths.py`; every test sets `XDG_DATA_HOME` (or `HOME`) into `tmp_path` so nothing touches the real user data directory.

1. `data_dir` honours a set `XDG_DATA_HOME`: returns `tmp_path/"agent-manager"` and the directory exists.
2. `data_dir` falls back to `$HOME/.local/share/agent-manager` when `XDG_DATA_HOME` is deleted, and the directory exists.
3. `data_dir` treats an empty-string `XDG_DATA_HOME` as unset and falls back to `$HOME`.
4. `data_dir` is idempotent: two calls return the same path and neither raises when the directory already exists.
5. `project_db_path` is deterministic for one root: two calls are equal, the parent is `<data_dir>/projects`, and that parent exists.
6. `project_db_path` differs across two distinct roots.
7. `project_db_path` resolves before hashing: a relative or symlinked spelling of the same root yields the same path as the absolute one.
8. `run_dir` returns `<data_dir>/runs/<run_id>`, the directory exists, and a second call with the same id is a no-op returning the same path.
9. `attempt_dir` returns `<run_dir>/<card>/<phase>.<attempt>` with the attempt number rendered as a decimal integer, and the directory exists.
10. `attempt_dir` separates attempts and phases: attempts 1 and 2 of one phase differ, and two phases of one card differ, while both sit under the same card directory.
11. The run tree is outside any worktree: `run_dir` and `attempt_dir` for a run are not relative to a `tmp_path` repo root used as a stand-in worktree — asserted by checking the returned paths are under `data_dir()` and that the repo directory stays empty after the calls.

## Verification

`uv run pytest`. There is no separate lint or typecheck command in this repo.
