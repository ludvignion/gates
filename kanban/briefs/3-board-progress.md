# Brief: the board refreshes itself and shows how much of each plan is built

## What I want
`traces/board.html` that is never stale and answers "how far along are we?" without reading
tickets:

1. **Auto-refresh.** The plugin's `Stop` hook (or a new one next to `trace_stop.py`) re-renders
   the board after every agent turn, so a person following along only reloads the page.
   Today `render_board.py` runs only when someone types `make board`; the last render before
   2026-09-02 was 2026-09-01 16:14, four merges ago. A project-local Stop hook in this repo's
   `.claude/settings.json` does the same job as a stopgap and should be removed when the
   plugin does it.
2. **Progress per plan.** For each approved plan, a line like
   `Plan 1 — ACs reached 5/10 · slices 3/7 · tickets done 1, in_review 2, ready 3, missing 1`.
   - *ACs reached*: plan ACs cited by at least one ticket whose status is `done` or
     `in_review`. Tickets already cite them in the AC text, e.g. `(plan 1 AC-4)`; the parse
     is a regex over `AC-\d+` inside `(plan <n> …)` groups.
   - *Slices*: the plan's `## Slices` list vs. tickets that exist for those ids; "missing"
     names slices with no ticket file yet (plan 1 slice 1.6 today).
   - Rendered as a bar per plan, and as a row per AC (AC → which tickets reach it, best
     status) so a partner-facing "definition of done" reads top-down.
3. The status column set gains `superseded` (2.4 is invisible on today's board because
   `STATUSES` does not list it).

## Why it matters
Without it, "how much is built" is a question only the agent can answer, by reading nine
tickets. A person who is not in the session cannot follow, and the routing stamp (`scrutiny`)
and gate states are already on disk but never rendered together.

## What I already know / have decided
- Everything needed is in frontmatter and ticket text already; no new fields on tickets.
- `render_board.py` and `_fm.py` in the plugin are the only code that changes; this repo
  contributes nothing but the brief and the stopgap hook.
- Manual count for plan 1 on 2026-09-02, done by hand (this is the number the feature
  should reproduce): ACs reached by done/in_review tickets — AC-1 (stub outputs, 1.1),
  AC-3, AC-4, AC-7, AC-8, AC-10 (1.3) → 6 of 10; AC-2, AC-5, AC-6, AC-9 wait on 1.4, 1.5,
  1.6, 1.7. Slices built 3 of 7 (1.1, 1.2, 1.3); 1.6 has no ticket. Plan 2: 2 of 3 slices
  done, 2.4 superseded, 2.3 has no ticket.

## What I'm unsure about
- Whether an AC reached by an `in_review` ticket should count as reached, or only `done`.
  Suggest two shades on the bar.
- Whether a plan AC that a later reopen superseded (plan 1 AC-7 → R-2) should be counted by
  its original text or by the amended one. Suggest counting the ticket's citation as-is.
- Whether the board should also list the open findings per ticket (count only). Useful for
  a reader, but a second parse.
