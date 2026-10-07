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
fail, because that failure is the test's whole point. There are exactly seven
test-controlled inputs, and none tells the fake anything the brief owns:
`REVIEW_FAIL_MARKER`, a file in the repo's git common dir that the fake finds
from its own cwd and compares with the brief's `## branch`;
`IMPLEMENT_EDITS_MARKER`, a JSON file beside it giving the files an implement
writes for the brief's `## branch`; `PKILL_MARKER`, a JSON list beside them of
`pkill -f` patterns every implement runs after its hold, with SIGTERM
ignored, the empty pattern only inside a bwrap PID namespace (card
4a3e0414); the implement-only rendezvous
(`RENDEZVOUS_DIR_ENV` / `RENDEZVOUS_COUNT_ENV`), which only makes implement
wait for other lanes and changes nothing it writes; `RESOLVER_ENV`, which
only makes the resolve phase leave the merge it was given unfinished while
still claiming `resolved`, so git has to catch the lie;
`CRITIC_BLOCKS_ENV`, a budget file that makes a critic block a set number of
times with a fixed reason; and the hold (`HOLD_DIR_ENV` / `HOLD_PHASE_ENV`),
which only parks one phase of the card the brief's result path names until a
release file appears. The resolve phase learns the tip and the
conflicting files from the brief's `## merge_tip` and `## conflict_files` and
nowhere else.

Standard library only: it runs under a bare `#!<python>` line.
"""

import hashlib
import json
import os
import re
import signal
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


HOLD_DIR_ENV = "FAKE_CLAUDE_HOLD_DIR"
"""Test scaffolding, never in a brief: where a held phase announces itself and waits.

Unset or empty means no hold at all. Set, the hold phase writes
`<dir>/<short id><HOLD_SUFFIX>` holding this process's pid, then waits for
`<dir>/<short id><RELEASE_SUFFIX>`, polling every `RENDEZVOUS_POLL` and giving
up after `RENDEZVOUS_TIMEOUT`. Whether and where to hold comes from the
environment alone; which card is held is the card the brief's own result path
names (`<run dir>/<card id>/<phase>.<n>/result.json`). The multi-process tests
use it to keep a milestone run live for exactly as long as they need."""

HOLD_PHASE_ENV = "FAKE_CLAUDE_HOLD_PHASE"
"""Which phase holds. Unset or empty means `HOLD_DEFAULT_PHASE`."""

HOLD_DEFAULT_PHASE = "implement"

HOLD_PHASES = (
    "explore",
    "spec",
    "validate_spec",
    "plan",
    "validate_plan",
    "implement",
    "review",
    "resolve",
)
"""Every phase `build_result` knows. A hold phase outside it is a typo and stops
the fake, instead of silently never holding."""

HOLD_SUFFIX = ".held"
RELEASE_SUFFIX = ".release"

_HEX32 = re.compile(r"^[0-9a-fA-F]{32}$")


def short_id(card_id):
    """`agent_manager.dag.short_id`, copied: this script imports nothing from the package."""
    hex_only = str(card_id).replace("-", "")
    if not _HEX32.match(hex_only):
        raise FakeClaudeError(f"not a card id: {card_id!r}")
    return hex_only[:8].lower()


def hold(phase, result_path):
    """Announce this card's `phase` and wait for its release. A no-op unless armed.

    The marker is written to a temp name and renamed into place, so a test
    that sees `.held` always reads a whole pid.
    """
    directory = os.environ.get(HOLD_DIR_ENV)
    if not directory:
        return
    wanted = os.environ.get(HOLD_PHASE_ENV) or HOLD_DEFAULT_PHASE
    if wanted not in HOLD_PHASES:
        raise FakeClaudeError(
            f"{HOLD_PHASE_ENV} is {wanted!r}, not a phase this fake runs "
            f"(one of {list(HOLD_PHASES)})"
        )
    if phase != wanted:
        return
    card = short_id(Path(result_path).parents[1].name)
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    staging = folder / f".{card}{HOLD_SUFFIX}.tmp"
    staging.write_text(f"{os.getpid()}\n", encoding="utf-8")
    os.replace(staging, folder / f"{card}{HOLD_SUFFIX}")
    release = folder / f"{card}{RELEASE_SUFFIX}"
    deadline = time.monotonic() + RENDEZVOUS_TIMEOUT
    while not release.exists():
        if time.monotonic() >= deadline:
            raise FakeClaudeError(
                f"hold in {folder} timed out after {RENDEZVOUS_TIMEOUT}s: "
                f"{release} was never written"
            )
        time.sleep(RENDEZVOUS_POLL)


CRITIC_BLOCKS_ENV = "FAKE_CLAUDE_CRITIC_BLOCKS"
"""Test scaffolding, never in a brief: the path of a JSON budget of critic blocks.

Unset or empty means no critic ever blocks -- today's behaviour exactly. Set,
it names a file the test wrote, mapping a critic phase (one of
`CRITIC_PHASES`) to how many more times it must block, from 0 to
`MAX_CRITIC_BLOCKS`. Each block spends one and writes the file back, so a
count of 1 blocks once and then passes, and 2 blocks twice. Which critic
blocks, and how often, comes from this file alone, never from the brief
(Rule 4); the whole file is checked on every critic call, so a typo stops the
fake instead of reading as "no block"."""

CRITIC_PHASES = ("validate_spec", "validate_plan")
"""The two critic phases of `builtin/task.yaml`, the only keys a budget may name."""

MAX_CRITIC_BLOCKS = 2
"""The highest count a budget may give: one loop plus the block that escalates."""

CRITIC_BLOCK_REASON = (
    "fake-claude critic: the critic-blocks budget told this critic to block"
)
"""The `reason` of every blocked critic result. Fixed, so a test can find it in
the looped-to phase's `## feedback` and in the escalation detail."""


def _critic_budget(path):
    """The budget file's table, refused whole unless every entry is well formed."""
    if not path.is_file():
        raise FakeClaudeError(f"{CRITIC_BLOCKS_ENV} names {path}, which is not a file")
    try:
        table = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise FakeClaudeError(
            f"{CRITIC_BLOCKS_ENV} names {path}, which is not valid JSON: {error}"
        ) from None
    if not isinstance(table, dict):
        raise FakeClaudeError(
            f"{CRITIC_BLOCKS_ENV} names {path}, which is not a JSON object of "
            "critic phases"
        )
    for name, count in table.items():
        if name not in CRITIC_PHASES:
            raise FakeClaudeError(
                f"{CRITIC_BLOCKS_ENV} names {path}, whose key {name!r} is not a "
                f"critic phase (one of {list(CRITIC_PHASES)})"
            )
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or not 0 <= count <= MAX_CRITIC_BLOCKS
        ):
            raise FakeClaudeError(
                f"{CRITIC_BLOCKS_ENV} names {path}, which gives {name!r} {count!r} "
                f"blocks; a count is a whole number from 0 to {MAX_CRITIC_BLOCKS}"
            )
    return table


def critic_blocks(phase):
    """`True`, spending one block, when the budget says `phase` must block now.

    A no-op returning `False` unless `CRITIC_BLOCKS_ENV` is set. The file is
    only rewritten when a block is spent.
    """
    raw = os.environ.get(CRITIC_BLOCKS_ENV, "")
    if raw == "":
        return False
    path = Path(raw)
    table = _critic_budget(path)
    remaining = table.get(phase, 0)
    if remaining == 0:
        return False
    table[phase] = remaining - 1
    path.write_text(json.dumps(table, sort_keys=True), encoding="utf-8")
    return True


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


PKILL_MARKER = "fake-claude-pkill"
"""A JSON file, in the repo's git common dir, of `pkill -f` patterns every implement runs.

A JSON list of strings, run in list order: JSON so that the empty pattern
`''` can be written. Found from this process's own cwd through
`git rev-parse --git-common-dir`, like `REVIEW_FAIL_MARKER`, so it is in no
worktree's tree. Not keyed by branch: every implement of the run runs the
whole list, after the hold and before any work. The run-hardening proofs
(card 4a3e0414) use it to make an agent signal everything it can see. No
marker changes nothing, and the log entry gets no `pkill` key.
"""

PKILL_COMMAND = ("pkill", "-f")

INIT_CMDLINE = Path("/proc/1/cmdline")
"""Whose first argument says whether this process is inside bwrap's PID namespace."""


def parse_pkill_marker(text, marker):
    """The marker's patterns: a JSON list of strings, `""` included, or `FakeClaudeError`."""
    try:
        patterns = json.loads(text)
    except json.JSONDecodeError as error:
        raise FakeClaudeError(
            f"the pkill marker {marker} is not valid JSON: {error}"
        ) from None
    if not isinstance(patterns, list) or not all(
        isinstance(pattern, str) for pattern in patterns
    ):
        raise FakeClaudeError(
            f"the pkill marker {marker} is not a JSON list of pattern strings"
        )
    return patterns


def pkill_patterns(cwd):
    """The pkill marker's patterns, or `None` when there is no marker (or no repo)."""
    try:
        marker = _common_dir_file(cwd, PKILL_MARKER)
    except FakeClaudeError:
        return None  # not a git checkout: nowhere a marker could be
    if not marker.is_file():
        return None
    return parse_pkill_marker(marker.read_text(encoding="utf-8"), marker)


def init_argv(path=None):
    """`/proc/1/cmdline` split on NUL, or `[]` when it cannot be read."""
    try:
        raw = (INIT_CMDLINE if path is None else Path(path)).read_bytes()
    except OSError:
        return []
    return [part.decode("utf-8", "surrogateescape") for part in raw.split(b"\0") if part]


def in_bwrap_namespace(argv):
    """Whether pid 1 is bwrap: its first argument's basename is exactly `bwrap`."""
    return bool(argv) and Path(argv[0]).name == "bwrap"


def run_pkill(argv):
    """Run one `pkill` argv quietly and return its exit code (1 = nothing matched)."""
    return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True).returncode


def run_pkills(patterns, runner=None, read_init=None):
    """Run `pkill -f <pattern>` per pattern, in order, and record each exit code.

    Safety guard: an empty pattern runs only inside bwrap's PID namespace,
    judged by `/proc/1`'s first argument. Otherwise the whole list is refused
    before any pattern runs: un-isolated, `pkill -f ''` signals every process
    of this user. SIGTERM, pkill's default signal, is ignored while the
    pkills run, so `pkill -f ''` in the namespace does not end this fake, and
    the previous handler is restored afterwards, error or not. `runner` and
    `read_init` default to `run_pkill` and `init_argv`, looked up at call time.
    """
    if "" in patterns:
        argv = (init_argv if read_init is None else read_init)()
        if not in_bwrap_namespace(argv):
            raise FakeClaudeError(
                "refusing `pkill -f ''` outside a bwrap PID namespace: /proc/1 "
                f"runs {argv[:1]!r}, and un-isolated it would signal every "
                "process of this user"
            )
    run = run_pkill if runner is None else runner
    previous = signal.signal(signal.SIGTERM, signal.SIG_IGN)
    try:
        return [
            {"pattern": pattern, "returncode": run([*PKILL_COMMAND, pattern])}
            for pattern in patterns
        ]
    finally:
        signal.signal(signal.SIGTERM, previous)


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
    if phase in CRITIC_PHASES:
        # Test scaffolding: the budget, never the brief, says whether to block.
        if critic_blocks(phase):
            return override(
                payload, blockers=True, reason=CRITIC_BLOCK_REASON, summary=SUMMARY
            )
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
            # A review the production gates block. `review_blockers_gate`,
            # listed first on `review`, stops on the non-empty
            # `unresolved_blockers`; the non-empty `porcelain` is what
            # `review_gate` would block on if it ran.
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
    # Test scaffolding: park here, before any work, when the hold is armed.
    hold(phase, result_path)
    cwd = Path(os.getcwd())
    # Test scaffolding: after the hold, so a held implement has signalled
    # nothing yet, and before any work, so a refused marker writes nothing.
    pkills = None
    if phase == "implement":
        patterns = pkill_patterns(cwd)
        if patterns is not None:
            pkills = run_pkills(patterns)
    payload = build_result(phase, payload_from_schema(schema_of(text)), text, cwd)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    entry = {"phase": phase, "cwd": str(cwd), "result_path": str(result_path)}
    if pkills is not None:
        entry["pkill"] = pkills
    with log_path(result_path).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    # stdout is a log, never a channel (D4); nothing reads it.
    print(f"fake-claude ok phase={phase}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except FakeClaudeError as error:
        print(f"fake-claude: {error}", file=sys.stderr)
        sys.exit(1)
