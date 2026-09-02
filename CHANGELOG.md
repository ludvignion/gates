# Changelog

## 0.4.0 — 2026-09-02

Brief: `kanban/briefs/3-board-progress.md` — the board refreshes itself and shows how much of
each plan is built.

- `hooks/hooks.json` — the `Stop` hook now re-renders `traces/board.html` after every agent
  turn (second command after `trace_stop.py`). No-op when the project has no `kanban/`.
- `scripts/render_board.py` — new **Progress** section above **Tickets**: one row per plan
  (`Plan <n> — ACs reached k/total · slices built/total · tickets done, in_review, ready,
  missing`), a two-shade bar (done vs in_review), and one line per plan AC listing the tickets
  that cite it and the best status. `superseded` added to the status columns.
- `scripts/schemas.py` — new. `Plan` (AC ids + `## Slices` ids) and `TicketCitations`
  (`(plan <n> AC-x, AC-y)` groups inside ticket AC lines). The renderer loads through them.
- `tests/` — synthetic fixture (two plans, eight tickets) plus hook exit-code checks.
  Run `python3 -m unittest discover -s tests`.

Decisions recorded:

- An AC cited by an `in_review` ticket counts as reached; the bar shades it apart from `done`.
- A plan AC amended by a reopened decision counts by the ticket's citation as written
  (`AC-7 as amended by R-2` reaches AC-7).
- Only citations inside a ticket's `## Acceptance criteria` lines count; prose, findings, and
  log mentions do not. Any `(… plan <n> AC-x …)` group inside an AC line counts, including
  `(unblocks plan <n> AC-x)`.
- A slice is "built" when its ticket is `done` or `in_review`; `in_progress` and `superseded`
  counts appear in the row only when non-zero.
- Findings count per ticket on the board: out of scope, not done.

Consuming projects: delete the project-local `Stop` hook in `.claude/settings.json` that ran
`make board` as a stopgap — the plugin does it now. Pin to `v0.4.0`.
