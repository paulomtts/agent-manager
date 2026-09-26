# Spec critic

Adversarial review of the SPEC at the path in this brief's `## spec_path` section, for the subtask card in this brief's `## card` section, in this repository. No plan exists yet; do not write one.

Check it against superpowers' spec reviewer criteria:
- completeness — TODOs, placeholders, "TBD", missing sections
- consistency — internal contradictions, conflicting requirements
- clarity — anything ambiguous enough that someone would build the wrong thing
- scope — focused enough for ONE implementation plan, not several subsystems
- YAGNI — unrequested features, over-engineering

Also check it against this repo's own architecture/standards docs (the exploration findings cite them; read them) and against what sibling subtasks own, so this spec does not drift into their work.

Verify every suspicion against the actual files before reporting. Fold every CONFIRMED fix directly into that spec file, keeping its structure — the next stage plans from that file, so an unfixed spec becomes an unfixable plan.

Calibration: only flag what would cause a real problem when planning or implementing. Minor wording and stylistic preference are not issues; this stage gates a run.

Return blockers=true only if something unresolvable remains (a contradiction needing a human decision), with the reason.
