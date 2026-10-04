# Plan critic

Adversarial review of the PLAN at the path in this brief's `## plan_path` section — its subtask card, in this repository. Its spec is at the path in this brief's `## spec_path` section and was already reviewed and corrected; treat it as settled and review the plan AGAINST it rather than re-litigating it.

Try to BREAK it before implementation: contradictions with this repo's architecture/standards docs (read them; the exploration cites them), decisions that bite sibling subtasks, dishonest or tautological tests, config side-effects, steps not executable verbatim. Verify every suspicion against the actual files/tools before reporting (run commands if needed).

Check it against superpowers' plan reviewer criteria:
- completeness — TODOs, placeholders, incomplete tasks, missing steps
- spec alignment — every spec requirement has a task, and no major scope creep beyond it
- task decomposition — clear boundaries, each step one actionable thing
- buildability — could an engineer follow this without getting stuck?

Calibration: only flag what would cause a real problem during implementation. An implementer building the wrong thing, or getting stuck, is an issue. Minor wording, stylistic preference and nice-to-haves are not — this stage gates a run, so treat it as a gate and not a critique.

If a plan defect traces back to the SPEC being wrong, say so in reason and set blockers=true rather than patching the plan around it: a plan that compensates for a bad spec hides the real problem from every later stage.

Fold every CONFIRMED fix directly into the plan file (edit it), keeping its structure. Explore already rolled the card's status to in_progress on a best-effort basis; do not touch card status here either way.

Never run `git commit`. Folding fixes into the plan file is the whole job: the workflow's `docs_commit` step commits the spec and the plan together, with the `Plan-Hash` trailer that ties them to the finished plan, unless the repository git-ignores them.

Return blockers=true only if something unresolvable remains (spec contradiction needing a human decision) with the reason.
