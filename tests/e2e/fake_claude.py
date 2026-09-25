"""A fake `claude` executable: the model stand-in of the production-wiring tier.

Test infrastructure, not product code. It is copied to a tmp directory as an
executable named `claude` and put first on `PATH`, so `harness/claude.py`'s bare
`COMMAND = "claude"` resolves to it and the real adapter, the real
`launcher.run_direct` and a real child process all run unmodified.

Everything it needs comes out of the brief on disk: the prompt path from the
adapter's `-p` sentence (`harness/claude.py:30`), and the absolute result path
plus the JSON Schema from the `## Result contract` section the brief carries
(`prompt.py:281-374`). There is deliberately no extra argv flag and no import
of `agent_manager` -- a brief that omits the contract must make this script
fail, because that failure is the test's whole point. There are exactly four
test-controlled inputs, and none tells the fake anything the brief owns:
`REVIEW_FAIL_MARKER`, a file in the repo's git common dir that the fake finds
from its own cwd and compares with the brief's `## branch`;
`IMPLEMENT_EDITS_MARKER`, a JSON file beside it giving the files an implement
writes for the brief's `## branch`; the implement-only rendezvous
(`RENDEZVOUS_DIR_ENV` / `RENDEZVOUS_COUNT_ENV`), which only makes implement
wait for other lanes and changes nothing it writes; and `RESOLVER_ENV`, which
only makes the resolve phase leave the merge it was given unfinished while
still claiming `resolved`, so git has to catch the lie. The resolve phase
learns the tip and the conflicting files from the brief's `## merge_tip` and
`## conflict_files` and nowhere else.

Standard library only: it runs under a bare `#!<python>` line.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


class FakeClaudeError(RuntimeError):
    """The fake cannot do its job from the brief it was given."""


PROMPT_SENTENCE = re.compile(
    r"^Read (?P<path>.+?) and follow the instructions in it exactly\."
)
"""The adapter's `-p` text (`harness/claude.py:30`), read backwards."""

PHASE_HEADER = re.compile(r"^# phase: (?P<phase>\S+)\n# role: (?P<role>\S+)$", re.M)
"""`prompt._assemble`'s two-line head (`prompt.py:267-269`)."""


def prompt_path_from_argv(argv):
    """The brief's path, out of the adapter's `-p` sentence."""
    if "-p" not in argv:
        raise FakeClaudeError(f"no -p argument in argv: {argv!r}")
    sentence = argv[argv.index("-p") + 1]
    found = PROMPT_SENTENCE.match(sentence)
    if found is None:
        raise FakeClaudeError(
            f"the -p text is not the adapter's instruction sentence: {sentence!r}"
        )
    return Path(found.group("path"))


def phase_of(text):
    """The phase name the rendered prompt's header states."""
    found = PHASE_HEADER.search(text)
    if found is None:
        raise FakeClaudeError("the brief carries no `# phase:`/`# role:` header")
    return found.group("phase")


RESULT_HEADING = "## Result contract"
"""`prompt.RESULT_HEADING` (`prompt.py:281`), matched as a literal."""

RESULT_PATH_LEAD = "write your result as valid JSON to exactly this path:"
"""The sentence `prompt._result_contract` puts immediately before the path."""

SCHEMA_FENCE = re.compile(r"```json\n(?P<schema>.*?)\n```", re.S)

SECTION = re.compile(r"^## (?P<name>.+)$", re.M)
"""Any `## ` heading. Names are taken verbatim and matched case-sensitively:
the `repo_docs` body inlines a repo CLAUDE.md that may carry its own
`## Verification` heading, and that must not be read as the `verification`
input."""


def _contract(text):
    start = text.find(RESULT_HEADING)
    if start < 0:
        raise FakeClaudeError(
            f"the brief has no {RESULT_HEADING!r} section, so it names no result "
            "path and no schema; this fake has no other way to learn either "
            "(addendum R2)"
        )
    return text[start:]


def result_path_of(text):
    """The absolute result path the contract names."""
    contract = _contract(text)
    lead = contract.find(RESULT_PATH_LEAD)
    if lead < 0:
        raise FakeClaudeError(
            f"the {RESULT_HEADING!r} section does not say {RESULT_PATH_LEAD!r}"
        )
    for line in contract[lead + len(RESULT_PATH_LEAD) :].splitlines():
        if line.strip():
            return Path(line.strip())
    raise FakeClaudeError("the result contract names no path after its lead line")


def schema_of(text):
    """The JSON Schema embedded in the contract, as a dict."""
    found = SCHEMA_FENCE.search(_contract(text))
    if found is None:
        raise FakeClaudeError("the result contract embeds no ```json schema fence")
    return json.loads(found.group("schema"))


def sections(text):
    """Every `## <name>` section below the `# phase:` header, first wins."""
    head = PHASE_HEADER.search(text)
    body = text[head.end() :] if head is not None else text
    marks = list(SECTION.finditer(body))
    found = {}
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(body)
        found.setdefault(mark.group("name"), body[mark.end() : end].strip("\n"))
    return found


def payload_from_schema(schema, defs=None):
    """A schema-shaped payload: every declared property at its type's zero value.

    Generated rather than hardcoded, so this fake cannot drift away from the
    models the run validates against. Values that a *gate* needs (a real
    summary, a real commit count) are set afterwards by `override`.
    """
    table = schema.get("$defs", {}) if defs is None else defs
    return {
        name: _value_for(node, table)
        for name, node in schema.get("properties", {}).items()
    }


def _value_for(node, defs):
    if "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        if name not in defs:
            raise FakeClaudeError(f"the schema references unknown $def {name!r}")
        return payload_from_schema(defs[name], defs)
    if "anyOf" in node:
        options = node["anyOf"]
        if any(option.get("type") == "null" for option in options):
            return None
        return _value_for(options[0], defs)
    kind = node.get("type")
    if kind == "object":
        return payload_from_schema(node, defs)
    if kind == "array":
        return []
    if kind == "string":
        return ""
    if kind in ("integer", "number"):
        return 0
    if kind == "boolean":
        return False
    raise FakeClaudeError(f"no default value for schema node {node!r}")


def override(payload, **fields):
    """Set each named field, refusing a name the schema did not produce.

    The refusal is the drift detector: if a result model renames a field, this
    fake stops instead of writing a well-typed payload that every gate then
    reads as `None`.
    """
    for name, value in fields.items():
        if name not in payload:
            raise FakeClaudeError(
                f"the embedded schema has no field {name!r} "
                f"(it has: {sorted(payload)})"
            )
        payload[name] = value
    return payload


SUMMARY = (
    "the fake claude executable drove this phase from the brief on disk alone: "
    "it parsed the result contract, generated a payload from the embedded JSON "
    "Schema, and wrote it to exactly the path the contract named."
)
"""Longer than `reducers.MIN_SUMMARY_LENGTH` (60) and not one of
`reducers.PLACEHOLDER_SUMMARIES`, so `exploration_output_gate` passes."""

IMPLEMENTATION_NAME = "IMPLEMENTATION.md"
"""The one file the fake coder writes, so its commit is not empty.

The engine's `docs_commit` phase now commits the spec and the plan before
`implement` runs (card ba15da20), which is the whole point: the fake must not
do work the engine owes. A coder that wrote nothing at all would then have an
empty `git commit` and fail, so this fake writes the one file a real coder
would have written.
"""

LOG_NAME = "fake-claude.log"
"""The cwd log, written beside the run directory -- under `paths.data_dir()`,
never inside the worktree, so the clean-worktree assertion stays meaningful."""

REVIEW_FAIL_MARKER = "fake-claude-review-fail"
"""A file, in the repo's git common dir, naming branches whose review must fail.

One branch per line. It is test-controlled and found from this process's own
cwd through `git rev-parse --git-common-dir`, so it sits inside `.git`: it is
in no worktree's tree and never shows in `git status`. It is compared with
the review brief's `## branch` section, so the brief is still what picks the
subtask. This is not an env var or an argv flag, and the fake computes nothing
it could not read.
"""

REVIEW_FAIL_PORCELAIN = "?? fake-claude: the review-fail marker names this branch"
"""What a failing review reports as `porcelain`. Non-empty, so the production
`review_gate` blocks on it, and worded so the escalation detail says why."""

IMPLEMENT_EDITS_MARKER = "fake-claude-implement-edits"
"""A JSON file, in the repo's git common dir, of files a subtask's implement writes.

It maps a branch to `{repo-relative path: full file content}`. It is found from
this process's own cwd through `git rev-parse --git-common-dir`, like
`REVIEW_FAIL_MARKER`, so it is in no worktree's tree. It is keyed by the
implement brief's `## branch` section, so the brief still picks the subtask.
The marker only says what that subtask's files contain, which is how the
Integrate tests make two stories edit the same line. No marker, or no entry
for the branch, changes nothing.
"""

RESOLVER_ENV = "FAKE_CLAUDE_RESOLVER"
"""Test scaffolding, never in a brief: how the resolve phase behaves.

Unset or empty resolves the merge. `refuse` claims `resolved: true` and leaves
the merge exactly as it found it (no edit, no add, no commit), so the
production `merge_completed_gate`, which asks git and never reads the flag,
has to catch it. Any other value is a typo and fails the fake."""

RESOLVER_REFUSE = "refuse"

REFUSE_SUMMARY = (
    "the fake claude executable was told to refuse: it claims the merge is "
    "resolved but left MERGE_HEAD, the conflict markers and the index exactly "
    "as it found them."
)

RENDEZVOUS_DIR_ENV = "FAKE_CLAUDE_RENDEZVOUS_DIR"
"""Test scaffolding, never in a brief: a directory where each implement leaves a
marker named for its cwd and then waits for other lanes' markers. Unset or
empty means no rendezvous at all. The parallel milestone tests set it so a run
can only finish if two lanes were inside implement at the same time."""

RENDEZVOUS_COUNT_ENV = "FAKE_CLAUDE_RENDEZVOUS_COUNT"
"""How many markers implement waits for; a whole number of at least 1."""

RENDEZVOUS_TIMEOUT = 20.0
"""Seconds to wait before failing. Read at call time, so a self-test can patch it."""

RENDEZVOUS_POLL = 0.05
"""Seconds between marker counts."""

RENDEZVOUS_SUFFIX = ".arrived"
"""Only files with this suffix count, so nothing else in the dir can release a wait."""


def rendezvous_marker_name(cwd):
    """A filesystem-safe marker name that is the same on every run for one cwd.

    Hashed rather than escaped, so a path of any shape or length is safe. One
    name per cwd means a retried implement in the same worktree counts once.
    """
    digest = hashlib.sha256(str(Path(cwd).resolve()).encode("utf-8")).hexdigest()
    return digest[:16] + RENDEZVOUS_SUFFIX


def _rendezvous_count(raw):
    try:
        needed = int(raw)
    except (TypeError, ValueError):
        raise FakeClaudeError(
            f"{RENDEZVOUS_DIR_ENV} is set but {RENDEZVOUS_COUNT_ENV} is {raw!r}, "
            "not a whole number"
        ) from None
    if needed < 1:
        raise FakeClaudeError(
            f"{RENDEZVOUS_COUNT_ENV} must be at least 1, got {needed}"
        )
    return needed


def rendezvous(cwd):
    """Leave this cwd's marker and wait until enough lanes have left theirs.

    A no-op unless `RENDEZVOUS_DIR_ENV` is set. Markers are never removed, so
    once a run reaches the count every later implement passes straight through.
    """
    directory = os.environ.get(RENDEZVOUS_DIR_ENV)
    if not directory:
        return
    needed = _rendezvous_count(os.environ.get(RENDEZVOUS_COUNT_ENV))
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / rendezvous_marker_name(cwd)).write_text(f"{cwd}\n", encoding="utf-8")
    deadline = time.monotonic() + RENDEZVOUS_TIMEOUT
    while True:
        seen = len(list(folder.glob(f"*{RENDEZVOUS_SUFFIX}")))
        if seen >= needed:
            return
        if time.monotonic() >= deadline:
            raise FakeClaudeError(
                f"rendezvous in {folder} timed out after {RENDEZVOUS_TIMEOUT}s: "
                f"saw {seen} of {needed} marker(s)"
            )
        time.sleep(RENDEZVOUS_POLL)



def _common_dir_file(cwd, name):
    """`<git common dir>/<name>`, found from `cwd`."""
    common = git(cwd, "rev-parse", "--git-common-dir").strip()
    # Relative (`.git`) in a main checkout, absolute in a linked worktree;
    # joining onto the cwd handles both.
    return Path(cwd) / common / name


def review_fail_branches(cwd):
    """The branches the review-fail marker names, or an empty set when there is none."""
    marker = _common_dir_file(cwd, REVIEW_FAIL_MARKER)
    if not marker.is_file():
        return set()
    return {
        line.strip()
        for line in marker.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def log_path(result_path):
    """`<run dir>/fake-claude.log`, derived from the result path alone.

    `paths.attempt_dir` is `<run dir>/<card>/<phase>.<n>`, so the run directory
    is the result file's third parent. Derived, not configured: this fake gets
    nothing but the brief.
    """
    return Path(result_path).parents[2] / LOG_NAME


def git(cwd, *args):
    """Run one git command in `cwd`, raising `FakeClaudeError` on failure."""
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True
    )
    if completed.returncode != 0:
        raise FakeClaudeError(
            f"git {' '.join(args)} failed in {cwd}: "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )
    return completed.stdout


def plan_hash_of(path):
    """`reducers.is_plan_hash`'s shape: the first 8 hex chars of the sha256."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:8]


def _document(cwd, relative, kind):
    """Write the document the phase's `writes:` template declared."""
    path = Path(cwd) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {kind} for this card\n\n{SUMMARY}\n", encoding="utf-8")
    return path


def _section(found, name, phase):
    if name not in found:
        raise FakeClaudeError(
            f"the {phase!r} brief has no `## {name}` section "
            f"(it has: {sorted(found)})"
        )
    return found[name].strip()


def _inside_worktree(relative, marker):
    """`relative` as a `Path`, refusing anything that could land outside the cwd."""
    path = Path(relative)
    if not relative or path.is_absolute() or ".." in path.parts:
        raise FakeClaudeError(
            f"the implement-edits marker {marker} names {relative!r}, which is "
            "not a path inside the worktree"
        )
    return path


def implement_edits(cwd, found, phase):
    """The files the implement-edits marker gives the brief's `## branch`, or `{}`.

    No marker means `{}` without reading the brief, so an implement brief that
    carries no `## branch` still works when no test asked for edits.
    """
    marker = _common_dir_file(cwd, IMPLEMENT_EDITS_MARKER)
    if not marker.is_file():
        return {}
    branch = _section(found, "branch", phase)
    try:
        table = json.loads(marker.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise FakeClaudeError(
            f"the implement-edits marker {marker} is not valid JSON: {error}"
        ) from None
    if not isinstance(table, dict):
        raise FakeClaudeError(
            f"the implement-edits marker {marker} is not a JSON object of branches"
        )
    entry = table.get(branch, {})
    if not isinstance(entry, dict) or not all(
        isinstance(name, str) and isinstance(content, str)
        for name, content in entry.items()
    ):
        raise FakeClaudeError(
            f"the implement-edits marker {marker} entry for {branch!r} is not an "
            "object of path -> content strings"
        )
    return {_inside_worktree(name, marker): content for name, content in entry.items()}


def _is_marker(bare, sigil):
    """A conflict-marker line: the seven-character sigil alone or followed by a space."""
    return bare == sigil or bare.startswith(sigil + " ")


def keep_both_sides(text):
    """`text` with every conflict hunk replaced by its two sides, ours then theirs.

    The marker lines go, and so does a diff3/zdiff3 `|||||||` base section, so
    a developer's `merge.conflictStyle` cannot break the fake. A `=======` line
    counts only inside a hunk, so a markdown underline survives. Line endings
    are kept as they were. A hunk that never closes is refused rather than
    half-rewritten.
    """
    kept = []
    state = None  # None outside a hunk, else "ours", "base" or "theirs"
    for line in text.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        if state is None:
            if _is_marker(bare, "<<<<<<<"):
                state = "ours"
            else:
                kept.append(line)
        elif state == "ours" and _is_marker(bare, "|||||||"):
            state = "base"
        elif state in ("ours", "base") and bare == "=======":
            state = "theirs"
        elif state == "theirs" and _is_marker(bare, ">>>>>>>"):
            state = None
        elif state != "base":
            kept.append(line)
    if state is not None:
        raise FakeClaudeError(
            "a conflict hunk is never closed: no `>>>>>>>` line after its `<<<<<<<`"
        )
    return "".join(kept)


def resolver_mode():
    """`"resolve"` or `RESOLVER_REFUSE`, from `RESOLVER_ENV`. Anything else is refused."""
    raw = os.environ.get(RESOLVER_ENV, "")
    if raw == "":
        return "resolve"
    if raw == RESOLVER_REFUSE:
        return RESOLVER_REFUSE
    raise FakeClaudeError(
        f"{RESOLVER_ENV} must be unset, empty or {RESOLVER_REFUSE!r}, got {raw!r}"
    )


def conflict_files_of(found, phase):
    """The brief's `## conflict_files`: a JSON list of non-empty path strings."""
    raw = _section(found, "conflict_files", phase)
    try:
        files = json.loads(raw)
    except json.JSONDecodeError:
        raise FakeClaudeError(
            f"the {phase!r} brief's `## conflict_files` is not JSON: {raw!r}"
        ) from None
    if not isinstance(files, list) or not all(
        isinstance(name, str) and name for name in files
    ):
        raise FakeClaudeError(
            f"the {phase!r} brief's `## conflict_files` is not a JSON list of "
            f"paths: {raw!r}"
        )
    return files


def check_merge_head(cwd, tip):
    """Refuse unless `MERGE_HEAD` in `cwd` is the commit the brief's `merge_tip` names."""
    probe = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--verify", "--quiet", "MERGE_HEAD"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        raise FakeClaudeError(
            f"no merge is in progress in {cwd} (no MERGE_HEAD), yet the brief's "
            f"merge_tip is {tip!r}"
        )
    merge_head = probe.stdout.strip()
    wanted = git(cwd, "rev-parse", "--verify", f"{tip}^{{commit}}").strip()
    if merge_head != wanted:
        raise FakeClaudeError(
            f"MERGE_HEAD in {cwd} is {merge_head}, not the brief's merge_tip "
            f"{tip!r} ({wanted})"
        )


def build_result(phase, payload, text, cwd):
    """The phase's result: the schema skeleton, with what the gates need set."""
    found = sections(text)
    if phase == "explore":
        suite = json.loads(_section(found, "verification", phase))
        override(
            payload["verification"], full_suite=suite, typecheck="", lint=[]
        )
        return override(
            payload,
            refused=False,
            reason=None,
            summary=SUMMARY,
            verification=payload["verification"],
        )
    if phase in ("validate_spec", "validate_plan"):
        return override(payload, blockers=False, reason=None, summary=SUMMARY)
    if phase == "spec":
        relative = _section(found, "spec_path", phase)
        _document(cwd, relative, "spec")
        return override(payload, path=relative, note=None)
    if phase == "plan":
        relative = _section(found, "plan_path", phase)
        _document(cwd, relative, "plan")
        return override(payload, path=relative, self_reviewed=True, note=None)
    if phase == "implement":
        # Test scaffolding: wait here for the other lanes, before any work, so
        # an unmet rendezvous fails without committing anything.
        rendezvous(cwd)
        # Card f26b377d: the hash comes from the brief's `## plan_hash` section,
        # never from hashing the plan. A fake that computed it would keep the
        # wiring test green with the input missing from `builtin/task.yaml`,
        # which is the one thing this tier exists to catch (R4).
        digest = _section(found, "plan_hash", phase)
        relative = _section(found, "plan_path", phase)
        # Test scaffolding, keyed by the brief's `## branch`: read and checked
        # before anything is written, so a bad marker leaves the tree untouched.
        edits = implement_edits(cwd, found, phase)
        # The content names this card's plan, so a subtask stacked on another's
        # branch (where the file already exists) still has a change to commit.
        (Path(cwd) / IMPLEMENTATION_NAME).write_text(
            f"# implementation of {relative}\n\n{SUMMARY}\n", encoding="utf-8"
        )
        for path, content in edits.items():
            target = Path(cwd) / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        git(cwd, "add", "-A")
        # A relaunched subtask's implementation is already committed: nothing
        # changed, so there is nothing to commit, and the honest answer is
        # `resumed`, not a failed `git commit`.
        resumed = git(cwd, "status", "--porcelain").strip() == ""
        if not resumed:
            git(cwd, "commit", "-m", f"feat: implement this card\n\nPlan-Hash: {digest}")
        return override(
            payload,
            blocked=False,
            blocked_reason=None,
            resumed=resumed,
            plan_hash=digest,
            report=SUMMARY,
        )
    if phase == "review":
        relative = _section(found, "plan_path", phase)
        base = _section(found, "base_branch", phase)
        branch = _section(found, "branch", phase)
        revisions = git(cwd, "rev-list", f"{base}..HEAD").split()
        tagged = [
            revision
            for revision in revisions
            if "Plan-Hash:" in git(cwd, "show", "-s", "--format=%B", revision)
        ]
        if branch in review_fail_branches(cwd):
            # A review the production `review_gate` blocks: a non-empty
            # `porcelain`. `unresolved_blockers` alone would fail nothing,
            # because no gate reads it.
            findings = [f"the review-fail marker names {branch}"]
            porcelain = REVIEW_FAIL_PORCELAIN
        else:
            findings = []
            porcelain = git(cwd, "status", "--porcelain").strip()
        return override(
            payload,
            findings=findings,
            unresolved_blockers=list(findings),
            fix_summary=SUMMARY,
            porcelain=porcelain,
            commit_count=len(revisions),
            tagged_count=len(tagged),
            plan_hash=plan_hash_of(Path(cwd) / relative),
        )
    if phase == "resolve":
        # Everything is checked before anything is touched: the env switch, the
        # two brief sections, that the merge in the cwd is the brief's, and
        # that every listed file is there.
        mode = resolver_mode()
        tip = _section(found, "merge_tip", phase)
        files = conflict_files_of(found, phase)
        check_merge_head(cwd, tip)
        missing = [name for name in files if not (Path(cwd) / name).is_file()]
        if missing:
            raise FakeClaudeError(
                f"the brief's `## conflict_files` names paths that are not files "
                f"in {cwd}: {missing}"
            )
        if mode == RESOLVER_REFUSE:
            # The advisory flag lies; git, through `merge_completed_gate`, judges.
            return override(payload, resolved=True, summary=REFUSE_SUMMARY)
        rewritten = {
            name: keep_both_sides((Path(cwd) / name).read_bytes().decode("utf-8"))
            for name in files
        }
        for name, resolved_text in rewritten.items():
            (Path(cwd) / name).write_bytes(resolved_text.encode("utf-8"))
            git(cwd, "add", "--", name)
        git(cwd, "commit", "--no-edit")
        return override(payload, resolved=True, summary=SUMMARY)
    raise FakeClaudeError(f"no behaviour for phase {phase!r}")


def main(argv):
    """Read the brief, write the result, log the cwd. Exit code 0 on success."""
    text = prompt_path_from_argv(argv).read_text(encoding="utf-8")
    phase = phase_of(text)
    result_path = result_path_of(text)
    cwd = Path(os.getcwd())
    payload = build_result(phase, payload_from_schema(schema_of(text)), text, cwd)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    entry = {"phase": phase, "cwd": str(cwd), "result_path": str(result_path)}
    with log_path(result_path).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    # stdout is a log, never a channel (D4). Usage-free on purpose: the adapter
    # scans it with `parse_usage`, and inventing token counts here would
    # journal fiction.
    print(f"fake-claude ok phase={phase}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except FakeClaudeError as error:
        print(f"fake-claude: {error}", file=sys.stderr)
        sys.exit(1)
