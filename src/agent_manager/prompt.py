"""Resolve a phase's declared `inputs` and render its prompt (design §6 step 2, §7).

§7 fixes a table: a phase declares `inputs`, and each name resolves through that
table and through nothing else. There is no expression language and no fallback
lookup -- the same invariant `workflow/registry.py` enforces for `when:` and
gates at load time. A name the table does not carry is a document bug, reported
with the phase and the list of names that are.

The rule the table encodes, from §7 verbatim: **small structured results are
inlined; documents are passed by path.** Inlining a spec or a plan would re-bill
it on every phase and invite the agent to work from a stale copy of a file it can
read live in the worktree it is already sitting in.

Input names are the *document's* vocabulary; context keys are the *callees'*
parameter names, as `engine.subtask_context` established. The two are not the
same word in several rows: `base_branch` reads `base`, `card` reads
`card_details`, `verification` reads `commands`.

Pure: no clock, no randomness, no board, no process. The only files read are the
repo docs a `repo_docs` input asks for, and the only file written is the
`prompt.txt` a caller asks for by handing over an attempt directory it made.
"""

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel

from agent_manager import dag, models
from agent_manager.errors import EngineError
from agent_manager.roles.loader import RoleBundle
from agent_manager.workflow.loader import AgentPhase

_MISSING = object()

FEEDBACK_HEADING = "## feedback on the previous attempt"
"""Heading of the block §6 step 7 appends before a re-dispatch.

A `##` section, matching `_assemble`'s section format, so the retry block reads
as one more section rather than as a stray paragraph. It lives here, not in
`dispatch`, because `dispatch` imports `prompt` and never the reverse: the
composer needs the same heading and a second copy of the string could drift.
"""


@dataclass(frozen=True)
class RenderedPrompt:
    """One phase's prompt text, plus the sections it was assembled from.

    Internal-only state, so a dataclass rather than a pydantic model (CLAUDE.md):
    nothing validates it at a process boundary -- it is produced and consumed
    inside one process, and written to disk as plain text.
    """

    phase: str
    text: str
    sections: tuple[tuple[str, str], ...] = ()

    @property
    def inputs(self) -> tuple[str, ...]:
        """The input names that produced sections, in the order they appear."""
        return tuple(name for name, _body in self.sections)

    def write(self, attempt_dir: Path) -> Path:
        """Write `prompt.txt` into an existing attempt directory, and return it.

        §6 puts the attempt directory at step 3 and the dispatch at step 4, so
        the prompt is on disk before any harness runs and every dispatch is
        reproducible from the run tree alone. This method does not create the
        directory and knows nothing of the run layout: `paths.attempt_dir` and
        the caller that makes it are sibling bf8e415b's.
        """
        path = Path(attempt_dir) / "prompt.txt"
        try:
            path.write_text(self.text, encoding="utf-8")
        except OSError as error:
            raise EngineError(
                f"cannot write the prompt to {path}: {type(error).__name__}: {error}",
                phase=self.phase,
            ) from error
        return path


@dataclass(frozen=True)
class _Request:
    """One input name being resolved, with everything a resolver may read."""

    name: str
    phase: AgentPhase
    context: Mapping[str, Any]


Resolver = Callable[[_Request], str]
"""Turns one declared input into the body of its prompt section."""


def _present(request: _Request, key: str) -> Any:
    """The context value under `key`, refusing an absent key by name."""
    value = request.context.get(key, _MISSING)
    if value is _MISSING:
        raise EngineError(
            f"is declared as an input, but nothing in the context supplies it "
            f"(it reads the context key {key!r}; the context has: "
            f"{', '.join(sorted(request.context)) or 'nothing'})",
            phase=request.phase.name,
            parameter=request.name,
        )
    return value


def _required(request: _Request, key: str) -> Any:
    """`_present`, and additionally refusing a key that is there but empty."""
    value = _present(request, key)
    if value is None:
        raise EngineError(
            f"is declared as an input, but its context key {key!r} was never populated",
            phase=request.phase.name,
            parameter=request.name,
        )
    return value


def _verbatim(key: str) -> Resolver:
    """A context value rendered as its own string: a branch name, or a path.

    Both §7 forms "inlined string" and "path only" render this way. They differ
    in the *rule*, not the formatting: a path is inlined as a path and its file
    is never opened, which is what stops a spec or a plan being re-billed on
    every phase.
    """

    def resolve(request: _Request) -> str:
        return str(_required(request, key))

    return resolve


def _phase_field(phase_key: str, field: str) -> Resolver:
    """One field of an earlier phase's result, inlined as its own string.

    `engine._bind_result` stores a phase's result in the context under the
    phase's own name, so `docs_commit`'s `{"plan_hash": digest}` lands at
    `context["docs_commit"]["plan_hash"]`. Input names are the document's
    vocabulary and context keys are the callees' names, so the declared input
    stays `plan_hash` while the lookup is nested -- the same split
    `base_branch` -> `base` already has.

    Two failures, deliberately distinguished. The phase never ran, so its key
    is absent: `_required` reports that, and it is the case a document hits by
    declaring the input on a phase that precedes the producer. The phase ran
    but its result does not carry the field: that is a step-contract breach,
    which no reordering of the document fixes.
    """

    def resolve(request: _Request) -> str:
        result = _required(request, phase_key)
        if not isinstance(result, Mapping):
            raise EngineError(
                f"is declared as an input, but the {phase_key!r} entry in the "
                f"context is a {type(result).__name__}, not a mapping, so it can "
                f"supply no {field!r}",
                phase=request.phase.name,
                parameter=request.name,
            )
        value = result.get(field)
        if value is None or not str(value).strip():
            raise EngineError(
                f"is declared as an input, but the {phase_key!r} result supplied "
                f"no {field!r} (that result carries: "
                f"{', '.join(sorted(str(key) for key in result)) or 'nothing'})",
                phase=request.phase.name,
                parameter=request.name,
            )
        return str(value)

    resolve.produced_by = phase_key  # type: ignore[attr-defined]
    return resolve


def _inline_json(key: str, *, allow_empty: bool = False) -> Resolver:
    """A context value inlined as JSON -- §7's form for small structured results.

    `allow_empty` is for `parent_story` alone: a subtask card genuinely may have
    no parent story, and `null` is the honest rendering of that. Everywhere else
    an unpopulated key is a bug in the caller, reported as one.
    """

    def resolve(request: _Request) -> str:
        value = _present(request, key) if allow_empty else _required(request, key)
        return json.dumps(_jsonable(value), indent=2, ensure_ascii=False, default=str)

    return resolve


def _jsonable(value: Any) -> Any:
    """A pydantic model as its JSON-mode dump; anything else unchanged.

    `default=str` on the dump catches whatever is left (a `Path` in `commands`):
    a prompt that says `/repo/scripts/check.sh` is strictly better than a
    `TypeError` escaping the renderer over a value the agent only has to read.
    """
    dump = getattr(value, "model_dump", None)
    return dump(mode="json") if callable(dump) else value


REPO_DOC_NAMES = ("CLAUDE.md", "AGENTS.md")
"""The repo-level conventions documents, in the order they are shown."""

REPO_DOC_LINES = 40
"""§7's "first N lines". An excerpt orients the agent; the file is in the
worktree it is already sitting in, so the rest costs it one read."""


def _repo_docs(request: _Request) -> str:
    """Path plus an excerpt of each repo conventions document that exists.

    Resolves against the worktree root when there is one and against `repo_dir`
    when there is not: `explore` is the first phase of `builtin/task.yaml` and
    the `worktree` phase runs two phases later, so at explore time there is no
    worktree to read from. Absence of both files is a stated fact, not a failure
    -- plenty of repositories have neither. Unreadability *is* a failure: a file
    that is there and cannot be read is a broken checkout, not an empty one.
    """
    root = _repo_docs_root(request)
    blocks = []
    for name in REPO_DOC_NAMES:
        block = _repo_doc(request, root / name)
        if block is not None:
            blocks.append(block)
    if not blocks:
        return f"no {' or '.join(REPO_DOC_NAMES)} at {root}"
    return "\n\n".join(blocks)


def _repo_docs_root(request: _Request) -> Path:
    worktree = request.context.get("worktree")
    if worktree is not None and Path(worktree).is_dir():
        return Path(worktree)
    return Path(_required(request, "repo_dir"))


def _repo_doc(request: _Request, path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise EngineError(
            f"{path} exists but cannot be read: {type(error).__name__}: {error}",
            phase=request.phase.name,
            parameter=request.name,
        ) from error
    lines = text.splitlines()
    head = "\n".join(lines[:REPO_DOC_LINES])
    if len(lines) <= REPO_DOC_LINES:
        return f"path: {path}\n{len(lines)} line(s), shown in full:\n{head}"
    return (
        f"path: {path}\nfirst {REPO_DOC_LINES} of {len(lines)} lines "
        f"({len(lines) - REPO_DOC_LINES} more, open the file for the rest):\n{head}"
    )


_TABLE: dict[str, Resolver] = {
    "card": _inline_json("card_details"),
    "parent_story": _inline_json("parent_story_details", allow_empty=True),
    "repo_docs": _repo_docs,
    "explore": _inline_json("explore"),
    "verification": _inline_json("commands"),
    "spec_path": _verbatim("spec_path"),
    "plan_path": _verbatim("plan_path"),
    "branch": _verbatim("branch"),
    "base_branch": _verbatim("base"),
    "plan_hash": _phase_field("docs_commit", "plan_hash"),
    "merge_tip": _verbatim("merge_tip"),
    "conflict_files": _inline_json("conflict_files"),
}
"""The fixed §7 resolution table, keyed by the name a document may declare.

The last two rows are the resolver's (Integrate addendum §2 and I3,
`builtin/integrate.yaml`): the story tip being merged, inlined as a ref, and
the conflicting paths `steps.integrate.merge_tip` reported, inlined as JSON.
Neither reads another phase's result, so neither appears in `INPUT_PRODUCERS`:
the caller supplies both through `engine.run_subtask(extra_context=...)`.
"""


INPUT_PRODUCERS: dict[str, str] = {
    name: resolver.produced_by  # type: ignore[attr-defined]
    for name, resolver in _TABLE.items()
    if hasattr(resolver, "produced_by")
}
"""Declared input name -> the phase whose result its resolver reads.

Derived from `_TABLE`, never hand-written: `_phase_field` stamps the phase key
on the resolver it builds, so this map cannot disagree with the lookup it
describes. `cli.resume_start_phase` reads it, because an input resolved out of
another phase's result is a dependency on that phase having run in *this*
process -- the journal never replays the binding table.
"""


def render_prompt(phase: AgentPhase, context: Mapping[str, Any]) -> RenderedPrompt:
    """Resolve every name in `phase.inputs` and assemble the prompt text.

    Sections follow the order the document declares, because that order is the
    author's emphasis and a reordering would silently change what the model reads
    first. A name declared twice contributes one section: the second mention adds
    no information and would only be billed twice.
    """
    sections: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name in phase.inputs:
        if name in seen:
            continue
        seen.add(name)
        resolver = _TABLE.get(name)
        if resolver is None:
            raise EngineError(
                f"{name!r} is not an input this engine knows how to resolve; the fixed "
                f"§7 table is: {', '.join(sorted(_TABLE))}",
                phase=phase.name,
                parameter=name,
            )
        sections.append((name, resolver(_Request(name, phase, context))))
    return RenderedPrompt(
        phase=phase.name, text=_assemble(phase, sections), sections=tuple(sections)
    )


def _assemble(phase: AgentPhase, sections: list[tuple[str, str]]) -> str:
    head = f"# phase: {phase.name}\n# role: {phase.role}\n"
    return head + "".join(f"\n## {name}\n{body}\n" for name, body in sections)


METHODOLOGY_HEADING_PREFIX = "## methodology: "
"""Heading that introduces one vendored methodology file inside the brief.

The file is a heading in one document rather than a path on disk: the agent is
handed the text it must follow, so it cannot follow a stale copy or fail to
open it.
"""


RESULT_HEADING = "## Result contract"
"""Heading of the section that tells the agent where its `result.json` goes.

Only present when the phase asks for a result. The schema in it comes from the
model the engine will validate against, so the instruction and the validator
cannot drift (addendum R2 §2).
"""


def compose_brief(
    role: RoleBundle,
    rendered: RenderedPrompt,
    *,
    result_path: Path | str | None = None,
    result_model: type[BaseModel] | None = None,
    feedback: str | None = None,
) -> str:
    """The whole brief for one dispatch, as one document (addendum R2 §2).

    Order is fixed: the role's standing instructions, its methodology, the
    phase's rendered inputs, the result contract, the feedback. Feedback comes
    last and is only ever appended, so the brief without it is a prefix of the
    brief with it and a retry can append rather than recompose.

    Pure: no disk, no clock, no randomness, no process. The result model is
    handed in rather than looked up in `results.RESULT_MODELS`, which is what
    keeps this function testable without the engine's registry.
    """
    parts: list[str] = [role.system]
    for filename, body in role.methodology.items():
        heading = f"{METHODOLOGY_HEADING_PREFIX}{filename}"
        parts.append(heading + "\n" + body.strip("\n"))
    parts.append(rendered.text)
    contract = _result_contract(rendered.phase, result_path, result_model)
    if contract is not None:
        parts.append(contract)
    if feedback is not None and feedback.strip():
        parts.append(FEEDBACK_HEADING + "\n" + feedback.strip("\n"))
    return _join_sections(parts)


def _join_sections(parts: list[str]) -> str:
    """One blank line between neighbours, one newline at the end.

    Only the newlines at each section's edges are normalised; interior text is
    untouched, because a methodology document's own blank lines are part of it.
    """
    return "\n\n".join(part.strip("\n") for part in parts) + "\n"


def _result_contract(
    phase: str, result_path: Path | str | None, result_model: type[BaseModel] | None
) -> str | None:
    """The contract section, or `None` when this phase asks for no result.

    Half a contract is a caller bug, not something to render partially. A
    relative path is refused because it would resolve inside the worktree the
    agent is `cd`'d into. `model_json_schema()` is called straight through: a
    model that cannot describe itself is a defect in the result models.
    """
    if result_path is None and result_model is None:
        return None
    if result_path is None or result_model is None:
        raise EngineError(
            "a result contract needs both result_path and result_model; got "
            f"result_path={None if result_path is None else str(result_path)!r} and "
            f"result_model={getattr(result_model, '__name__', None)!r}",
            phase=phase,
        )
    path = Path(result_path)
    if not path.is_absolute():
        raise EngineError(
            f"the result path {str(result_path)!r} is not absolute; the brief has to "
            "name one unambiguous location outside the worktree the agent runs in, "
            "and a relative path would resolve inside it",
            phase=phase,
        )
    schema = json.dumps(result_model.model_json_schema(), indent=2, ensure_ascii=False)
    return (
        f"{RESULT_HEADING}\n"
        "When you are done, write your result as valid JSON to exactly this path:\n"
        "\n"
        f"{path}\n"
        "\n"
        "That path is deliberately outside the worktree you are working in. Do not "
        "create the file inside the worktree, and do not write it anywhere else -- "
        "nothing else is read as your result.\n"
        "\n"
        "The JSON must validate against this schema:\n"
        "\n"
        "```json\n"
        f"{schema}\n"
        "```"
    )


_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")


def expand_writes(
    template: str, card: models.Card, *, phase: str, input_name: str
) -> str:
    """The repo-relative path a phase's `writes:` template names, for one card.

    `{stem}` is the only placeholder, and `dag.task_stem` is the only thing that
    fills it: the stem is the load-bearing short card id plus a slug of the
    title, and every other derived name in the program comes from the same
    function. An unknown placeholder is a document bug, not something to leave
    literal in a path an agent is told to write to.
    """
    unknown = sorted({found for found in _PLACEHOLDER.findall(template) if found != "stem"})
    if unknown:
        raise EngineError(
            f"the writes template {template!r} uses "
            f"{', '.join(repr(name) for name in unknown)}; the only placeholder this "
            "engine expands is {stem}",
            phase=phase,
            parameter=input_name,
        )
    expanded = template.replace("{stem}", dag.task_stem(card))
    parts = PurePosixPath(expanded)
    if parts.is_absolute() or ".." in parts.parts:
        raise EngineError(
            f"the writes template {template!r} resolves to {expanded!r}, which leaves "
            "the worktree; a document-path input must name a file inside the worktree "
            "the agent runs in",
            phase=phase,
            parameter=input_name,
        )
    return expanded
