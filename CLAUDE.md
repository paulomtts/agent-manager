# agent-manager

Python CLI (Typer + Pydantic), packaged with `uv`, mirroring the conventions of
the sibling `brd` project.

## Verification

Run the full suite with:

```bash
uv run pytest
```

There is no separate lint or typecheck command.

## Conventions

- Source lives under `src/agent_manager/`, tests mirror it under `tests/`.
- Pydantic models for anything validated at a process boundary (harness result
  files); plain dataclasses are fine for internal-only state.
- CLI output is JSON by default, `--pretty` for humans — the same envelope shape
  as `brd` (`{"ok": true, "data": ...}`).
- The design spec is the source of truth:
  `docs/superpowers/specs/2026-09-23-agent-manager-design.md`.
