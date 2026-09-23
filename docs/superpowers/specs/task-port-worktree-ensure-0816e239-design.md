# Port `worktree.ensure` — subtask design (card 0816e239)

Narrows the milestone design (`docs/superpowers/specs/2026-09-23-agent-manager-design.md`, the source of truth per `CLAUDE.md`) to one deterministic step module. Ports the `prepare` logic of the legacy `scripts/worktree.mjs` (`/home/paulomtts/.claude/plugins/cache/paulomtts-plugins/leave-me-alone/4.1.2/scripts/worktree.mjs`) to Python; its `worktree.test.mjs` is the behavioural specification.

## Scope

One module, `src/agent_manager/steps/worktree.py`, listed in §4 of the design under `steps/` (no model calls). It exposes a single public function, `ensure(...)`, taking the explicit arguments listed under Observable behaviour below and returning a `dict`. Its return shape conforms to what the §6 deterministic-phase contract requires of a phase result. **Out of scope for this card**: the `run(ctx) -> dict` engine entry point itself, and any `workflow/registry.py` wiring that maps a phase name to `ensure` — those belong to whichever future card builds the engine/registry (neither exists yet in this milestone) and must adapt a `ctx` mapping into `ensure`'s explicit arguments; this card defines and tests only `ensure`. Per `CLAUDE.md`, Pydantic is required only at process boundaries (harness result files); this is an internal deterministic result, so a plain dict (optionally backed by a dataclass) is correct.

Inputs are already resolved by the caller: `branch` and `base_branch` are computed by `dag.py` and inlined (§7). The module never derives a branch name, never reads the board, and never touches plan or verification concerns.

**Out of scope** (owned by siblings or later milestones): plan-file discovery and the `validated` marker (`steps/plan_check.py`, card d3feb87e); running verification commands in the worktree (`steps/verify.py`, card 9c3b1ffb); census, levels, parallel stories, integrate, non-Claude harnesses. No CLI command is added by this card.

## Observable behaviour

`ensure` takes the subtask `branch`, the `base` branch name, the absolute `worktree` path and the absolute `repo_dir`, plus an injectable git runner, and returns snake_case keys (the JS camelCase does not carry over):

- `branch`, `worktree` — echoed back.
- `branch_existed` — `branch` appears in `git -C <repo_dir> for-each-ref --format=%(refname:short) refs/heads/`, matched as an **exact** line, never a prefix: `m1/task-` must not match `m1/task-9`.
- `worktree_existed` — `worktree` appears among the paths parsed from `git -C <repo_dir> worktree list --porcelain`, taking only lines beginning with the literal `worktree ` prefix and stripping it.
- `created` — true exactly when this call ran `git worktree add`.
- `commit_count` — `git -C <worktree> rev-list --count <resolved_base>..HEAD`, parsed as an int, `0` on any parse failure.

Base resolution: try `git -C <repo_dir> rev-parse --verify --quiet origin/<base>`; on success use `origin/<base>`, on failure fall back to the bare local `<base>`. Rationale from the source: every base other than the milestone's own base branch is a local branch this run created and never pushed, so most resolutions legitimately fall back.

Creation decision:

- worktree already exists → **no** `git worktree add` call at all; the existing directory is left untouched and `created` is false.
- worktree missing, branch existed → `git -C <repo_dir> worktree add <worktree> <branch>` (checkout, never re-cut: re-cutting would silently discard a killed run's prior commits).
- worktree missing, branch new → `git -C <repo_dir> worktree add <worktree> -b <branch> <resolved_base>`.

`commit_count` is computed in both the created and the already-existed paths.

**Forbidden operations**, asserted by tests: no `reset`, no `checkout -f`, no `clean`, no `commit`, no `push`, no `worktree remove`, no `prune`, no deletion of any path. Deciding resume vs reset needs a plan hash that does not exist at this point in the run; that decision is an explicit non-goal.

## Error paths

- Missing or empty `branch`/`base`, or a non-absolute `worktree`/`repo_dir` → raise a validation error before any git call (the legacy `parseArgs` contract, carried into the function signature rather than a CLI flag parser).
- `origin/<base>` not resolving is **not** an error — it is the expected fallback.
- A failing `git worktree add` propagates; the module does not attempt cleanup or retry.
- Non-numeric or empty `rev-list --count` output → `commit_count = 0`, no raise.
- An absent `.git`/invalid `repo_dir` surfaces as the underlying git failure; no special-casing.

## Test list

Placement rule (§14): `worktree.py` is a **Steps** component, so its tests are the *steps tier* — "against temporary git repositories and a temporary `brd` board; no network". The ported `.mjs` tests used a fake git callable; that strategy is adapted: these run real `git init` / `git worktree` in `tmp_path`. Tests live at `tests/steps/test_worktree.py`, mirroring the source path per `CLAUDE.md`. No integration or end-to-end tier tests are added by this card.

Steps tier, real temporary git repos:

1. Fresh repo, new branch, missing worktree — worktree dir exists afterwards; `branch_existed` false, `worktree_existed` false, `created` true, `commit_count` 0.
2. Second call with identical arguments is idempotent — `branch_existed` true, `worktree_existed` true, `created` false, and the worktree directory and its HEAD are unchanged.
3. Branch exists but worktree missing — checkout path; a commit made earlier on that branch survives and is reflected in `commit_count`, proving the branch was not re-cut from base.
4. Existing worktree with uncommitted local changes — files on disk are byte-identical after the call (no reset/clean).
5. `commit_count` counts only commits on top of the resolved base; a repo with N subtask commits reports N.
6. Exact branch matching — a repo containing `m1/task-9` asked for `m1/task-` reports `branch_existed` false and creates a new branch.
7. Porcelain worktree-path matching — a repo with sibling worktrees reports `worktree_existed` only for an exact path match, and ignores non-`worktree ` porcelain lines (e.g. `branch refs/heads/...`).
8. Base fallback — a repo with no `origin` remote resolves to the bare local base and succeeds; a repo with an `origin` whose `origin/<base>` resolves uses `origin/<base>` (assert via the branch's merge-base/commit, and/or the recorded git argv).
9. Forbidden-operation guard — with a recording git runner wrapping the real one, assert no invocation contains `reset`, `checkout -f`, `clean`, `commit`, `push`, `worktree remove`, or `prune`, across the create path, the resume path, and the already-exists path.
10. Argument validation — empty branch, empty base, relative `worktree`, relative `repo_dir` each raise before any git invocation is recorded.

Verify with `uv run pytest`; the repo has no separate lint or typecheck command.
