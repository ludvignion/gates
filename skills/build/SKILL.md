---
name: build
description: >-
  Implement one kanban ticket with tests committed before code, bound to the ticket's scope.
  Use whenever the user says build, implement, or names a ticket id to work on. Refuses tickets
  whose parent plan has no approval line, and refuses to run outside the ticket's worktree.
disable-model-invocation: true
argument-hint: "<ticket id, e.g. 1.2>"
---

# Build
Input: `kanban/tickets/<id>.<slug>.md`. Output: commits on branch `ticket/<id>` inside the
ticket worktree `../<repo>-<id>`, plus ticket `## Log` entries.

## Sequence
1. **Worktree check.** `git rev-parse --abbrev-ref HEAD` must be `ticket/<id>`. If not, stop:
   `loop.py` creates the worktree; by hand it is `git worktree add -b ticket/<id> ../<repo>-<id>`.
   Never build on the main checkout — parallel tickets share it.
2. **Gate check.** Open the parent `<n>.plan.md`. If it has no `approved:` line, stop and say so.
   Child tickets (`<n>.<m>.<p>`) skip this.
3. **Retry check.** If `## Log` already has a `[verdict]` entry with `severity: critical` findings,
   this is a retry. Those findings are your brief: address them and nothing else, then continue
   from step 6.
4. **Scope.** Write the ticket id to `kanban/.active` (the write-guard and re-anchor hooks read it).
   Set `status: in_progress`. Append `### [build] <timestamp> — start` to `## Log`.
5. **Plan.** Lay out steps with the todo tool: one line per AC, then integration, then close-out.
6. **Tests first.** Translate every AC into pytest. Property ACs → hypothesis or parametrised
   cases. Critical ACs → mark for mutation. Commit: `test(<id>): ACs as tests`. Nothing else in
   that commit. Run them; they must fail. On a retry, add or fix tests only for the findings.
7. **Implement.** The most boring code that turns the tests green. Then, and only then, run
   `make ci`. CI output is the authority — never claim green from memory.
8. **Iterate headlessly when the artefact can't be exercised directly** (TUIs, jobs, external
   systems): factor pure logic into functions, test those; `python -m py_compile` after each edit.
9. **Close-out.** Every new file, function ≥10 lines, class, or dependency must trace to an AC.
   List them in `## Log` under `### [build] — close-out` with the AC each serves. Anything that
   traces to nothing: revert it, or log it as a finding. Set `status: in_review`. Clear `kanban/.active`.
10. **Report** the worktree path, the commit list, and the one-line command to run the thing.

## After compaction
If context was compacted mid-build, the re-anchor hook injects the active ticket. Re-read the
ticket, its `## Log`, `git status`, and `git log --oneline -5`, then resume from the last logged
step. Do not commit until you have.

## YAGNI defaults
No abstraction, config knob, DI, wrapper, or utility module unless an AC requires it.
No helper until three real call sites exist. Two similar blocks are fine.

## Anti-derailment
If you notice a bug, a bad name, a missing test, or a tempting refactor outside the ticket: stop.
Append `### [build] — finding: ...` to the ticket. Do not touch it. The hook will block you anyway.

## Never
- Self-report test status. Run `make ci`.
- Add `pytest.skip`, `xfail`, or `only` without a linked finding.
- Commit implementation before the test commit.
- Build outside `../<repo>-<id>`, or remove the worktree — the human does that after merge.
- Review your own work. That is `/verdict`, in a fresh session.
- Add `pytest.skip`, `xfail`, or `only` without a linked finding.
- Commit implementation before the test commit.
- Review your own work. That is `/verdict`, in a fresh session.
