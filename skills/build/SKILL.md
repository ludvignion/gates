---
name: build
description: >-
  Implement one kanban ticket with tests committed before code, bound to the ticket's scope.
  Use whenever the user says build, implement, or names a ticket id to work on. Refuses tickets
  whose parent plan has no approval line.
disable-model-invocation: true
argument-hint: "<ticket id, e.g. 1.2>"
---

# Build

Input: `kanban/tickets/<id>.<slug>.md`. Output: commits on a branch `ticket/<id>`, ticket `## Log` entries.

## Sequence

1. **Gate check.** Open the parent `<n>.plan.md`. If it has no `approved:` line, stop and say so.
   Child tickets (`<n>.<m>.<p>`) skip this.
2. **Scope.** Write the ticket id to `kanban/.active` (the write-guard hook reads it).
   Set `status: in_progress`. Append `### [build] <timestamp> — start` to `## Log`.
3. **Plan.** Lay out steps with the todo tool: one line per AC, then integration, then close-out.
4. **Tests first.** Translate every AC into pytest. Property ACs → hypothesis or parametrised
   cases. Critical ACs → mark for mutation. Commit: `test(<id>): ACs as tests`. Nothing else in
   that commit. Run them; they must fail.
5. **Implement.** The most boring code that turns the tests green. Then, and only then, run
   `make ci`. CI output is the authority — never claim green from memory.
6. **Iterate headlessly when the artefact can't be exercised directly** (TUIs, jobs, external
   systems): factor pure logic into functions, test those; `python -m py_compile` after each edit.
7. **Close-out.** Every new file, function ≥10 lines, class, or dependency must trace to an AC.
   List them in `## Log` under `### [build] — close-out` with the AC each serves. Anything that
   traces to nothing: revert it, or log it as a finding. Set `status: in_review`. Clear `kanban/.active`.
8. **Report** the branch, the commit list, and the one-line command to run the thing.

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
- Review your own work. That is `/verdict`, in a fresh session.
