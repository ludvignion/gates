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
1. **Sync.** Run `make sync` before the ticket is read: a project synced from GitHub Issues
   (`kanban/.issues`) pulls every new issue comment into the mirror's `## Log` first, so a
   collaborator's comment is there before this session reads the ticket (plan 4 AC-10). A
   project without the marker no-ops at once, with no `gh` call.
2. **Branch check.** `git rev-parse --abbrev-ref HEAD` must be `ticket/<id>`. If not, stop:
   `runner.py` checks the branch out (in place by default, under `.worktrees/<id>/` with
   `--parallel`); by hand it is `git checkout -b ticket/<id>`. Where the branch lives is the
   runner's choice, not yours; never switch branches or touch another checkout.
   Under `scrutiny: light` there is no per-ticket verdict — close-out still applies, and the
   whole branch gets one verdict before ship.
3. **Gate check.** Open the parent `<n>.plan.md`. If it has no `approved:` line, stop and say so.
   Child tickets (`<n>.<m>.<p>`) skip this.
4. **Retry check.** If the last `[verdict]` entry in `## Log` has `- block` lines without
   `→ child`, this is a retry. Those lines are your brief: address them and nothing else, then
   continue from step 7.
5. **Scope.** Write the ticket id to `kanban/.active` (the write-guard and re-anchor hooks read it;
   in a project synced from GitHub Issues this is the issue number, and `reanchor.sh` finds the
   mirror the same way it finds a file ticket). Set status and log the start through
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kanban_ops.py`, never a direct edit — it writes the file or the issue, whichever the project
   uses: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kanban_ops.py status <id> in_progress` then `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kanban_ops.py log <id> build start`.
6. **Plan.** One line per AC, then integration, then close-out.
7. **Tests first.** Translate every AC into pytest. Property ACs → hypothesis or parametrised
   cases. Critical ACs → mark for mutation. Commit: `test(<id>): ACs as tests`. Nothing else in
   that commit. Run them; they must fail. On a retry, add or fix tests only for the findings.
8. **Implement.** The most boring code that turns the tests green. Then, and only then, run
   `make ci`. CI output is the authority — never claim green from memory.
9. **Iterate headlessly when the artefact can't be exercised directly** (TUIs, jobs, external
   systems): factor pure logic into functions, test those.
10. **Close-out.** Every new file, function ≥10 lines, class, or dependency must trace to an AC.
    List them with `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kanban_ops.py log <id> build close-out "- <item>: <AC>" ...`. Anything that
    traces to nothing: revert it, or log it as a finding. Set status:
    `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kanban_ops.py status <id> in_review`. Clear `kanban/.active`.
11. **Report.** Log the final status through `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kanban_ops.py log <id> build "status: DONE |
    NEEDS_CONTEXT | BLOCKED" "- <reason>"`. NEEDS_CONTEXT = an AC is ambiguous in what the reader
    would notice, and the ticket + domain pack don't resolve it; the reason line must name
    the AC and the question — it feeds grill tuning. A technical ambiguity (how to build it)
    is never NEEDS_CONTEXT: decide it by `AGENTS.md`, name the choice in the close-out. BLOCKED = cannot finish for a reason outside the ticket (dependency, access, baseline).
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
