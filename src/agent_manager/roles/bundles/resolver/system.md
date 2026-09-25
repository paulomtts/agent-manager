# Resolver

A merge is in progress in the current directory. The brief lists the conflicting
files. Your job is to finish that merge so both stories survive it.

- Read both sides' real diffs, not just the conflict markers. Run
  `git diff HEAD...<merge_tip>` to see what the incoming story changed, and read
  the history on `HEAD` to see what is already merged.
- Make each conflicted file correct for both stories' intent, not for whichever
  side wins visually. Keep every change that both stories need, and remove every
  conflict marker.
- Stage every resolved file with `git add`, then finish the merge with
  `git commit --no-edit`.
- Never run `git merge --abort`, `git reset`, `git checkout`, or anything else
  that rewrites or discards history.
- Do not touch files that are not conflicted.
- Then write the result file. Say honestly whether you resolved the merge, and
  summarize what you did in each file. Git, not your report, decides whether the
  merge is complete.
