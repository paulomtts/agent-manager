# Document milestone runs (subtask 7b6ad9bd)

Parent story 5017dd8c "Prove it against a real harness, and document it"; milestone 99e178cb. Blocked by f9e7bf4c (opt-in real-harness milestone test, done). This subtask is docs only: it edits `README.md` and adds one note to section 10 of the main design spec. It touches nothing under `src/` or `tests/`.

## Precondition

The implementation is merged into this worktree: `src/agent_manager/orchestrate.py`, `census.py`, `cli.py` with `--milestone` / `--dry-run` (`run` at line ~951, `_check_run_targets` at ~920, `dry_run_payload` at ~840) and `tests/e2e/test_milestone_run.py` / `test_real_harness_milestone.py` are all present. Write every statement from that code, not from the addendum. If the code and the addendum disagree, the README describes the code and the difference is reported in the handoff, not fixed here.

## Scope

### 1. README.md: a "Milestone runs" section

Add it under `## Usage`, after the existing `am run --card` / `am resume` text and before the JSON-envelope paragraph (or after it, as long as the envelope paragraph still applies to every command). Leave the existing content as it is. The section covers, in this order:

- **The command.** `am run --milestone <id|title> --branch-prefix <p> --verify '<cmd>'` (`--verify` is repeatable, passed through as written, run in the order given; `--allow-no-verification` is the explicit opt-out; `--repo-dir` defaults to `.`, `--base-branch` to `master`). `--milestone` takes a card id, an exact title (case-insensitive), or a title substring that matches exactly one milestone. An ambiguous substring is an error that lists the matches. `--card` and `--milestone` cannot be used together, one of them is required, a blank `--milestone` is refused, and `--dry-run` with `--card` is refused. All of these are usage errors.
- **`--dry-run` and the base column.** It prints `{"levels": [{"level", "stories": [{"story", "title", "root", "subtasks": [{"id", "title", "status", "branch", "base"}]}]}], "already_done": [...]}` and writes nothing: no run directory, no branches, no board changes. How to read it: each subtask's `base` is the previous subtask's branch in the story's full order, so a done subtask still anchors the next one. A story's first subtask builds on its blocker's tip. A blocked story whose root is the base branch means a `blocked_by` edge is missing on the board. Blocker cycles, and stories with two or more in-milestone blockers, are refused before anything is written.
- **What a clean run leaves behind.** Stories run one at a time, level by level. Subtasks run in order, each on its own local branch stacked on the previous one. The run does `git fetch origin` once (only if an `origin` remote exists) and `git worktree prune` once. Board statuses roll up to the story and the milestone. The payload is `{"done": true, "run_id", "levels": [{"level", "stories": [ids]}], "completed": [subtask ids], "tips": [{"story", "tip"}], "warnings"}`, and the exit code is 0. `tips` lists the branches for a human to merge. Nothing is merged or pushed.
- **What an escalation report contains.** The run stops at the first subtask that does not finish, whether it escalated or its driver raised. The payload is `{"escalated": true, "run_id", "level", "story", "subtask", "failed_phase", "detail", "warnings"}` and the exit code is `EXIT_ESCALATED` (1). The subtask, the story and the run are recorded as escalated. A coder that reports `blocked` ends the subtask escalated at `implement`, and review never runs. A handled error prints `{"ok": false, "error": ...}` and exits with `EXIT_ERROR` (3).
- **Relaunching resumes.** Running the same command again skips cards that are already done on the board. A killed subtask picks up in its existing worktree and does not redo a plan that already passed. Say plainly that `am resume <run-id>` is not milestone-aware: to continue a milestone, run the same `am run --milestone` command again.
- **What is not there.** Stories do not run in parallel (at most one at a time). There is no Integrate step. `am resume` is not milestone-aware. `watch`, `retry` and `cancel` do not exist. Link `docs/superpowers/specs/2026-09-24-orchestration-design.md`, section "4. Deferred to the follow-up milestone (found now, not cut yet)".

Confirm each key name and exit code against `orchestrate.run_milestone` (lines ~235-355), `cli.dry_run_payload`, `orchestrate.story_tips` and `cli.EXIT_*` as you write. Use the real names from the code, not the list above if they differ. Use the same prose style as the rest of the README (short paragraphs, fenced `bash` blocks).

### 2. Main spec, section 10: a Status note

In `docs/superpowers/specs/2026-09-23-agent-manager-design.md`, add a short `Status:` paragraph at the end of section 10 ("CLI surface", before `## 11. Concurrency`). It says what exists now: `run` (with `--card`, or `--milestone` with `--dry-run`, `--repo-dir`, `--base-branch`, `--branch-prefix`, `--verify`, `--allow-no-verification`), `status`, `runs`, `logs` and `resume` (single card only). It says what is deferred: `watch`, `retry`, `cancel`, `--workflow`, `--harness`, `--max-concurrent` (runs are sequential for now) and a milestone-aware `resume`. It points to addendum section 4. Get the list of existing commands from the `@app.command(...)` registrations in `cli.py`. Do not change anything else in the spec, including the synopsis block.

## Verification (by hand, no new tests)

Run every command the README shows against a temporary git repo with a temporary `brd` board. Never use the real board. A command that does not work exactly as written is a README bug. Scratch files go in the session scratchpad. At minimum:

- `am run --milestone <title substring> --branch-prefix p --dry-run --repo-dir <tmp>`: check the payload shape and the bases on a board with two stories where one blocks the other and one story has two subtasks. Also check that the tmp repo has no new run directory and no new branches afterwards.
- Each refusal the README names: `--card` together with `--milestone`, neither flag, a blank `--milestone`, `--dry-run` with `--card`, an ambiguous substring. Each should give an error envelope and a non-zero exit.
- The clean-run and escalation payloads and the relaunch behavior: either drive them once with the fake claude from `tests/e2e/fake_claude.py` on PATH, or read them from `tests/e2e/test_milestone_run.py`, which already asserts them through the production path. Do not call the real `claude`: it costs money. Any fake used by hand follows the milestone-2 rule: it knows only what the brief says (no plan hash, no committing the spec or plan, and the result path comes only from the prompt text).
- `uv run pytest` stays green. Nothing is expected to change, since no code or tests are touched.

## Tests

None added. Per the main spec section 14 and addendum O8, unit, step, engine and e2e tiers exist for code. This card changes no code, and the milestone-run behavior the README describes is already covered in the default suite by `tests/e2e/test_milestone_run.py` (fake claude on PATH) and in the opt-in tier by `tests/e2e/test_real_harness_milestone.py` (`pytest -m e2e`). The README check above is a manual step, not a test file.

## Out of scope

Any change to `src/` or `tests/`. Parallel stories, Integrate, milestone-aware `am resume`, watch/retry/cancel, review counts measured by git, verification discovery: document these as absent and do not start any of them. Do not change the addendum.
