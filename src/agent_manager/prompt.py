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
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_manager.errors import EngineError
from agent_manager.workflow.loader import AgentPhase

_MISSING = object()


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


_TABLE: dict[str, Resolver] = {
    "branch": _verbatim("branch"),
    "base_branch": _verbatim("base"),
}
"""The fixed §7 resolution table, keyed by the name a document may declare."""


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
