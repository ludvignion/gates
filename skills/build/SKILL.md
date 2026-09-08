---
name: build
description: >-
  Implement one kanban ticket with tests committed before code, bound to the ticket's scope.
  Use whenever the user says build, implement, or names a ticket id to work on. Refuses tickets
  whose parent plan has no approval line, and refuses to run outside the ticket's worktree
  unless the plan's backend is session.
disable-model-invocation: true
argument-hint: "<ticket id, e.g. 1.2>"
---

# Build
Input: `kanban/tickets/<id>.<slug>.md`. Output: commits on branch `ticket/<id>` (checked out by
the runner, in place or in a worktree), plus ticket `## Log` entries.

## Sequence
1. **Branch check.** `git rev-parse --abbrev-ref HEAD` must be `ticket/<id>`. If not, stop:
   `runner.py` checks the branch out (in place by default, under `.worktrees/<id>/` with
   `--parallel`); by hand it is `git checkout -b ticket/<id>`. Where the branch lives is the
   runner's choice, not yours; never switch branches or touch another checkout.
   Under `scrutiny: light` there is no per-ticket verdict — close-out still applies, and the
   whole branch gets one verdict before ship.
2. **Gate check.** Open the parent `<n>.plan.md`. If it has no `approved:` line, stop and say so.
   Child tickets (`<n>.<m>.<p>`) skip this.
3. **Retry check.** If the last `[verdict]` entry in `## Log` has `- block` lines without
   `→ child`, this is a retry. Those lines are your brief: address them and nothing else, then
   continue from step 6.
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
10. **Report.** Append the final Log entry:
    `### [build] <timestamp> — status: DONE | NEEDS_CONTEXT | BLOCKED`
    followed by one line of reason. NEEDS_CONTEXT = an AC is ambiguous and the ticket + domain
    pack don't resolve it; the reason line must name the AC and the question — it feeds grill
    tuning. BLOCKED = cannot finish for a reason outside the ticket (dependency, access, baseline).
    On either, stop immediately: do not commit implementation, do not guess.
    Then print the branch, the commit list, and the one-line command to run the thing.
    **The session ends here.** The status line is the last write and the report is the last
    output: no tool call after it. Under the runner every call after the close-out commit is
    logged as `orbit after close-out` and the session is terminated.

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
- Switch branches, create or remove a worktree, or merge — the runner and the board do that.
- Keep working after the status line.
- Review your own work. That is `/verdict`, in a fresh session.

Next: nothing; the runner continues.
