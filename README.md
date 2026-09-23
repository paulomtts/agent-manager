# agent-manager

A local-first CLI that drives a `brd` milestone to completion by dispatching AI
harness instances (Claude Code, Codex, Pi) as subprocesses.

The program owns the dependency graph, the git mechanics, the gates, the retries
and the persistence. A harness is launched only for the steps that genuinely need
a model; everything else is plain Python.

See `docs/superpowers/specs/2026-09-23-agent-manager-design.md` for the design.

## Develop

```bash
uv sync
uv run pytest
```
